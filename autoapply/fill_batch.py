"""Data-only queue selection and durable fill-only checkpoints."""
import json
import os
import uuid
from .archive import atomic_json
from .freshness import freshness_state
from .models import now

MAX_ACTIVE_APPLICATION_TABS = 1
EXCLUDED = frozenset({4571, 4535, 6401, 5310, 6415, 6416})


def verify_safety(db):
    if db.setting('auto_submit') is not False:
        raise RuntimeError('Fill-only requires auto_submit=false')
    if db.setting('controlled_application_id') is not None:
        raise RuntimeError('Controlled worker must be retired before batch startup')
    if db.rows('SELECT * FROM manual_requests'):
        raise RuntimeError('Pending manual command; batch startup refused')


def discover(db, config, seen=()):
    """Ranking reads SQLite only; never constructs or calls a browser."""
    rows = db.rows('''SELECT a.id,j.posted_at FROM applications a JOIN jobs j ON j.id=a.job_id
        WHERE j.listing_active=1 AND j.listing_status='ACTIVE' AND a.status IN ('QUEUED','RETRY')
        AND a.retry_allowed=1 AND a.submit_intent_at IS NULL AND a.submission_confirmation_seen=0
        AND (a.retry_at IS NULL OR a.retry_at<=?)
        AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id
                        AND s.value NOT IN ('false','null','0'))
        ORDER BY j.priority DESC,j.discovered_at,a.id''', (now(),))
    return [r['id'] for r in rows if r['id'] not in EXCLUDED and r['id'] not in seen
            and not db.automation_retired(r['id'])
            and freshness_state(r['posted_at'], config['jobs']['max_listing_age_days']) == 'FRESH']


async def checkpoint(engine, app_id, page):
    app = engine.db.application(app_id)
    frames = []
    for frame in page.frames:
        fields = await frame.locator('input:not([type=hidden]):not([type=password]),textarea,select,[role=combobox]').evaluate_all('''els => els.map(e => ({
            id:e.id,name:e.name,role:e.getAttribute('role'),type:e.type,
            label:e.getAttribute('aria-label'),value:e.type==='file'?null:e.value,
            checked:e.checked, files:e.files?Array.from(e.files).map(f=>({name:f.name,size:f.size})):[],
            selected:e.selectedOptions?Array.from(e.selectedOptions).map(o=>o.text):[]
        }))''')
        frames.append({'url':frame.url,'fields':fields})
    record = {'application_id':app_id,'company':app['company'],'role':app['title'],'ats':app['ats'],
              'url':page.url,'timestamp':now(),'state':app['application_state'],
              'blocker':app['failure_reason'],'frames':frames,
              'questions':engine.db.rows('SELECT * FROM questions WHERE application_id=?',(app_id,)),
              'resume_sha256':app['resume_sha256'],
              'upload':engine.db.setting(f'upload_result:{app_id}'),
              'live_session_required':app['application_state']=='READY_FOR_MANUAL_SUBMIT',
              'reconstruction_verified':False}
    path = engine.config.private / 'fill-only-checkpoints' / f'{app_id}.json'
    atomic_json(path, record)
    engine.db.event(app_id,'FILL_ONLY_CHECKPOINT',json.dumps({'path':str(path),'state':record['state']}))
    return record


async def verify_reconstruction(engine, app_id):
    """Replay the saved answers in the same sole tab before declaring a durable draft."""
    verify_safety(engine.db)
    page = engine.retained_pages.pop(app_id)
    engine.handoff.pages.pop(app_id, None)
    app = engine.db.application(app_id)
    original_resume = app['resume_sha256']
    state, reason = await engine.browser.navigate(page, app['canonical_url'])
    if state:
        engine.retained_pages[app_id] = page
        raise RuntimeError('RECONSTRUCTION_FAILED: ' + str(state))
    # A fresh document has no carried file attachment. Keep observer callbacks bound
    # to the same tracker object while clearing evidence from the previous document.
    page._autoapply_attached_documents = {}
    page._autoapply_uploads.__init__()
    engine.browser.reset_observation(page)
    engine.db.transition(app_id, 'APPLYING', 'Verifying reconstruction from saved answers; final submission disabled')
    await engine._process_one(app_id, preserved_page=page)
    app = engine.db.application(app_id)
    if (app['application_state'] != 'READY_FOR_MANUAL_SUBMIT'
            or app['resume_sha256'] != original_resume):
        raise RuntimeError('RECONSTRUCTION_FAILED: ' + app['failure_reason'])
    record = await checkpoint(engine, app_id, page)
    record.update(reconstruction_verified=True, live_session_required=False,
                  reconstruction_method='Navigate canonical URL and replay verified saved answers with Engine(fill_only=True)',
                  validation_status='PASS', upload_ready=True)
    atomic_json(engine.config.private / 'fill-only-checkpoints' / f'{app_id}.json', record)
    engine.db.event(app_id, 'FILL_ONLY_RECONSTRUCTION_VERIFIED', json.dumps({'url':page.url,'upload_ready':True}))
    await page.close()
    engine.retained_pages.pop(app_id, None)
    engine.handoff.pages.pop(app_id, None)
    engine.handoff.sessions.pop(app_id, None)
    engine.db.set_setting(f'manual_session:{app_id}', None)
    engine.db.update_security(app_id, session_preserved=0)
    return record


class BatchReport:
    def __init__(self, path):
        self.path = path
        self.data = dict(batch_run_id=uuid.uuid4().hex,started_at=now(),updated_at=now(),
            worker_pid=os.getpid(),browser_pid=None,current_application_id=None,
            listings_considered=0,applications_opened=0,applications_filled=0,ready_count=0,
            skipped_ineligible=0,skipped_closed=0,blocked_questions=0,active_tab_count=0,
            max_active_application_tabs=MAX_ACTIVE_APPLICATION_TABS,max_active_tabs_observed=0,
            final_submit_clicks=0,employer_submission_requests=0,auto_submit=False,
            result='PREFLIGHT',applications=[],smoke_test='NOT_STARTED')
        if path.exists():
            atomic_json(path.with_name('fill-only-batch-previous.json'),json.loads(path.read_text(encoding='utf-8')))

    def save(self, **updates):
        self.data.update(updates,updated_at=now())
        atomic_json(self.path,self.data)
