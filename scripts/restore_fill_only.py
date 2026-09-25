"""Restore one verified fill-only checkpoint for the user to submit manually.

Usage: python scripts/restore_fill_only.py APPLICATION_ID
Close the browser tab or interrupt this command when finished. No final action is automated.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autoapply.config import Config
from autoapply.engine import Engine
from autoapply.fill_batch import EXCLUDED, verify_safety
from autoapply.runtime import ProcessLock
from scripts.fill_only_batch import FillDatabase


async def restore(config, db, app_id):
    if app_id in EXCLUDED:
        raise ValueError('Protected application excluded')
    path = config.private / 'fill-only-checkpoints' / f'{app_id}.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    if not saved.get('reconstruction_verified'):
        raise ValueError('This application has no verified reconstruction checkpoint')
    verify_safety(db)
    if db.submission_conflict(app_id):
        raise ValueError('Submission history prevents reconstruction')
    engine = Engine(config, db, fill_only=True)
    try:
        db.set_setting('paused', False)
        db.update_security(app_id, retry_allowed=1)
        db.transition(app_id, 'RETRY', 'User requested restoration of verified fill-only checkpoint', retry_at=None)
        await engine.process_one(app_id)
        page = engine.retained_pages.get(app_id)
        if not page or db.application(app_id)['application_state'] != 'READY_FOR_MANUAL_SUBMIT':
            raise RuntimeError('Restoration needs attention; inspect saved application state')
        print(f'Application #{app_id} is ready in the browser. Submit manually if desired, then close its tab.', flush=True)
        await page.wait_for_event('close', timeout=0)
    finally:
        db.set_setting('paused', True)
        # Only the user knows whether they submitted after the handoff. Do not infer it.
        if app_id in engine.retained_pages:
            db.update_security(app_id, session_preserved=0, application_state='MANUAL_REQUIRED',
                               manual_action_required=1, manual_action_reason='Manual session ended; reconcile employer status before another restoration')
        await engine.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application_id', type=int)
    args = parser.parse_args()
    config = Config(ROOT)
    config.data['application']['auto_submit'] = False
    with ProcessLock(config.private / 'worker.lock'):
        db = FillDatabase(config.private / 'autoapply.sqlite3', startup_maintenance=False)
        try:
            db.set_setting('auto_submit', False)
            asyncio.run(restore(config, db, args.application_id))
        finally:
            db.close()
