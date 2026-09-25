"""One controlled production application through the normal Engine.
Run from the approved normal Windows-user context. Local resume-manual and
inspect-manual commands are serviced indefinitely by this same worker.
"""
import asyncio
import getpass
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from autoapply.config import Config, setup_logging
from autoapply.database import Database
from autoapply.engine import Engine
from autoapply.freshness import freshness_state
from autoapply.models import now
from autoapply.runtime import ProcessLock


def snapshot(config):
    with sqlite3.connect(config.private / 'autoapply.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        apps = {str(r['id']): dict(r) for r in conn.execute('SELECT * FROM applications')}
    histories = {}
    for path in (config.private / 'application_history' / 'submitted').rglob('*'):
        if path.is_file():
            histories[str(path.relative_to(config.private))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'applications': apps, 'submitted_history': histories}


async def run(config, db, app_id, baseline):
    engine = Engine(config, db)
    old = {key: db.setting(key) for key in ('paused', 'auto_submit')}
    db.set_setting('controlled_application_id', app_id)
    db.set_setting('paused', False)
    db.set_setting('auto_submit', True)
    try:
        await engine.process_one(app_id)
        print(json.dumps({'application_id': app_id, 'status': db.application(app_id)['status'],
                          'reason': db.application(app_id)['failure_reason']}), flush=True)
        if engine.handoff.pages:
            print('WAITING_FOR_MANUAL_INTERVENTION: same worker retained; use inspect-manual or resume-manual.', flush=True)
            await engine.wait_for_manual()
    finally:
        for key, value in old.items():
            db.set_setting(key, value)
        await engine.close()
        after = snapshot(config)
        changed = [k for k,v in baseline['applications'].items() if after['applications'].get(k) != v]
        changed_history = [k for k,v in baseline['submitted_history'].items() if after['submitted_history'].get(k) != v]
        result = {'selected': app_id, 'changed_applications': changed, 'changed_prior_submitted_files': changed_history,
                  'final': db.application(app_id)}
        (config.private / 'controlled-run-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'changed_applications': changed, 'changed_prior_submitted_files': changed_history}), flush=True)


def main():
    if getpass.getuser().lower() == 'codexsandboxoffline':
        raise RuntimeError('EXTERNAL_EXECUTION_APPROVAL_REQUIRED: normal Windows-user context required')
    load_dotenv(ROOT / '.env')
    config = Config(ROOT)
    if config['browser']['headless']:
        raise RuntimeError('Headed browser required')
    setup_logging(config)
    with ProcessLock(config.private / 'worker.lock'):
        baseline = snapshot(config)
        (config.private / 'controlled-run-before.json').write_text(json.dumps(baseline, indent=2), encoding='utf-8')
        db = Database(config.private / 'autoapply.sqlite3', config['jobs']['max_listing_age_days'], startup_maintenance=False)
        try:
            candidates = db.rows("""SELECT a.id,j.posted_at FROM applications a JOIN jobs j ON j.id=a.job_id
                WHERE j.listing_active=1 AND j.listing_status='ACTIVE' AND a.status IN ('QUEUED','RETRY')
                AND a.retry_allowed=1 AND a.submit_intent_at IS NULL AND (a.retry_at IS NULL OR a.retry_at<=?)
                AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id AND s.value NOT IN ('false','null','0'))
                ORDER BY j.priority DESC,j.discovered_at,a.id""", (now(),))
            candidate = next((r for r in candidates if freshness_state(r['posted_at'], config['jobs']['max_listing_age_days']) == 'FRESH'), None)
            if not candidate:
                print('No eligible queued application.'); return
            app_id = candidate['id']
            app = db.application(app_id)
            print(json.dumps({k: app[k] for k in ('id','company','title','ats','posted_at','canonical_url')}), flush=True)
            asyncio.run(run(config, db, app_id, baseline))
        finally:
            db.close()


if __name__ == '__main__':
    main()
