"""Normal sequential fill-only execution, with optional live human handoff."""
import asyncio
import json
import uuid

from .archive import atomic_json
from .engine import Engine
from .fill_batch import finish_preparation, release_checkpoint, verify_safety
from .status import snapshot
from .models import now


GLOBAL_BLOCKERS = frozenset({'BROWSER_START_FAILED', 'EXTERNAL_EXECUTION_APPROVAL_REQUIRED',
                           'EXECUTION_APPROVAL_BLOCKED'})


async def wait_for_handoff(engine, app_id):
    """Keep the single owned form alive; never submit or reopen a closed page."""
    while not engine.stop_event.is_set() and not engine.db.setting('paused', False):
        page = engine.handoff.pages.get(app_id) or engine.retained_pages.get(app_id)
        if page is None or page.is_closed():
            break
        engine.fill_invariant.check()
        await engine.service_manual_requests()
        await asyncio.sleep(.5)
    await release_checkpoint(engine, app_id)


async def run_selected(config, db, application_ids, *, checkpoint_only=False, emit=None, recovery_id=None):
    """Process explicitly selected IDs once, in order, without queue fallback.

    Caller owns worker.lock. Applicant/site holds are checkpointed and may be
    followed by the next selected application. Infrastructure and invariant
    failures stop the run. Historical retries/holds are never overridden.
    """
    ids = list(application_ids)
    if not ids or any(type(i) is not int or i < 1 for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('Select distinct positive application IDs')
    if recovery_id is not None and (len(ids) != 1 or not checkpoint_only):
        raise ValueError('Checkpoint recovery requires one checkpoint-only application')
    if config['browser']['headless'] and not checkpoint_only:
        raise ValueError('Live manual handoff requires browser.headless: false')
    for app_id in ids:
        db.application(app_id)  # Validate the whole selection before browser work.
    db.set_setting('auto_submit', False)
    verify_safety(db)
    engine = Engine(config, db, fill_only=True, preserve_fill_session=not checkpoint_only)
    report = {'run_id': uuid.uuid4().hex, 'started_at': now(),
              'result': 'PROCESSING', 'applications': []}
    report_path = config.private / 'fill-run-result.json'
    emit = emit or (lambda value: print(json.dumps(value), flush=True))
    try:
        db.set_setting('paused', False)
        atomic_json(report_path, report)
        for app_id in ids:
            if db.setting('paused', False) or engine.stop_event.is_set():
                report['result'] = 'PAUSED'
                break
            processed = await engine.process_one(app_id, recovery_id=recovery_id) if recovery_id else await engine.process_one(app_id)
            state = snapshot(db, app_id, live_page=engine.control.live_page(app_id),
                             live_browser=engine.browser.context is not None)
            emit({'application_id': app_id, 'processed': bool(processed), 'status': state})
            if state['application_state'] == 'READY_FOR_MANUAL_SUBMIT' and checkpoint_only:
                await finish_preparation(engine, app_id)
            elif not checkpoint_only and engine.control.live_page(app_id) is not None:
                emit({'application_id': app_id, 'action': 'MANUAL_HANDOFF',
                      'message': 'Complete missing fields in the dedicated browser. Final submission remains disabled. Close this tab to continue; Ctrl+C ends the run. Use answer and resume-manual commands to save facts and revalidate.'})
                await wait_for_handoff(engine, app_id)
            report['applications'].append(snapshot(db, app_id))
            engine.fill_invariant.check()
            engine.browser.check_tab_limit()
            report.update(engine.fill_invariant.snapshot())
            atomic_json(report_path, report)
            if state['manual_hold']['required'] and db.application(app_id)['error_category'] in GLOBAL_BLOCKERS:
                report['result'] = 'GLOBAL_EXECUTION_BLOCKER'
                break
        else:
            report['result'] = 'COMPLETE'
        report.update(engine.fill_invariant.snapshot())
        return report
    except BaseException as exc:
        report['result'] = 'INTERRUPTED' if isinstance(exc, asyncio.CancelledError) else 'RUN_FAILED'
        report['error_type'] = type(exc).__name__
        raise
    finally:
        try:
            db.set_setting('paused', True)
        finally:
            try:
                await engine.close()
            except BaseException as exc:
                report.update(result='CLEANUP_FAILED', error_type=type(exc).__name__)
                raise
            finally:
                report['finished_at'] = now()
                atomic_json(report_path, report)
