"""Data-only queue selection and durable fill-only checkpoints."""
import json
import os
import uuid
from dataclasses import dataclass
from .archive import atomic_json
from .freshness import freshness_state
from .models import now

MAX_ACTIVE_APPLICATION_TABS = 1
EXCLUDED = frozenset({4571, 4535, 6401, 5310, 6415, 6416})


@dataclass(frozen=True)
class BatchPolicy:
    target_count: int = 5
    verify_reconstruction: bool = False

    def __post_init__(self):
        if self.target_count < 1:
            raise ValueError('Batch target must be positive')


class FillOnlyInvariant:
    """Run-wide counters and latched violations, not per-application resets."""
    def __init__(self, db):
        self.db = db
        self.initial_intents = {r['id']: r['submit_intent_at'] for r in db.rows('SELECT id,submit_intent_at FROM applications')}
        self.initial_event_id = db.one("SELECT coalesce(max(id),0) n FROM events")["n"]
        self.created_intents = 0
        self.final_submit_clicks = 0
        self.employer_submission_requests = 0
        self.submission_requests_blocked = 0

    def snapshot(self):
        intents = sum(bool(r['submit_intent_at']) and r['submit_intent_at'] != self.initial_intents.get(r['id'])
                      for r in self.db.rows('SELECT id,submit_intent_at FROM applications'))
        recorded = self.db.one("SELECT count(*) n FROM events WHERE id>? AND kind='SUBMITTING'", (self.initial_event_id,))["n"]
        self.created_intents = max(self.created_intents, intents, recorded)
        return dict(final_submit_clicks=self.final_submit_clicks,
                    employer_submission_requests=self.employer_submission_requests,
                    submission_intents_created=self.created_intents, submission_requests_blocked=self.submission_requests_blocked)

    def check(self):
        if any(self.snapshot().values()):
            raise RuntimeError('FILL_ONLY_SUBMISSION_INVARIANT_VIOLATION')

    async def install(self, context):
        # Observes actual clicks, including an unexpected adapter/user path. The
        # engine policy prevents reaching its final interaction in the first place.
        await context.expose_binding('__autoapplyFinalClick', lambda source: self.observed_click())
        script = """(() => {
          if (window.__autoapplyFinalObserved) return;
          window.__autoapplyFinalObserved = true;
          document.addEventListener('click', e => {
            const b=e.target.closest('button,input[type=submit],[role=button]');
            if (!b || !b.closest('form')) return;
            const label=(b.getAttribute('aria-label') || b.innerText || b.value || '').trim();
            if (/^(submit|send)( application)?$|submit application|finalize application/i.test(label))
              window.__autoapplyFinalClick();
          }, true);
        })();"""
        await context.add_init_script(script)
        for page in context.pages:
            await page.evaluate(script)
        context.on('requestfinished', self.observed_request)
        await context.route('**/*', self.route)

    def observed_click(self):
        self.final_submit_clicks += 1

    def observed_request(self, request):
        from .batch_approval import is_final_submission
        if is_final_submission(request.url, request.method, request.post_data):
            self.employer_submission_requests += 1

    async def route(self, route):
        from .batch_approval import is_final_submission
        request = route.request
        if is_final_submission(request.url, request.method, request.post_data):
            self.submission_requests_blocked += 1
            await route.abort('blockedbyclient')
        else:
            await route.fallback()


async def release_checkpoint(engine, app_id):
    """Close the sole owned page after a durable checkpoint; no replay implied."""
    page = engine.retained_pages.get(app_id) or engine.handoff.pages.get(app_id)
    saved = engine.db.setting(f'fill_checkpoint:{app_id}', {})
    if not saved:
        raise ValueError('Checkpoint must be durable before release')
    if page is not None and not page.is_closed():
        await engine.browser.release_page(page)
    engine.retained_pages.pop(app_id, None)
    engine.handoff.pages.pop(app_id, None)
    engine.handoff.sessions.pop(app_id, None)
    with engine.db.lifecycle.atomic():
        engine.db.set_setting(f'manual_session:{app_id}', None)
        fields = dict(session_preserved=0)
        if engine.db.application(app_id)['application_state'] == 'READY_FOR_MANUAL_SUBMIT':
            fields['application_state'] = 'READY_TO_SUBMIT'
        engine.db.update_security(app_id, **fields)
        engine.db.event(app_id, 'FILL_ONLY_PAGE_RELEASED', saved['readiness'])
    return saved


async def finish_preparation(engine, app_id, *, verify=False):
    if engine.db.application(app_id)['application_state'] != 'READY_FOR_MANUAL_SUBMIT':
        raise ValueError('Preparation has not reached verified live readiness')
    if verify:
        return await verify_reconstruction(engine, app_id)
    page = engine.retained_pages[app_id]
    await checkpoint(engine, app_id, page)
    return await release_checkpoint(engine, app_id)


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
        AND a.retry_allowed=1 AND a.manual_action_required=0
        AND a.security_state IN ('NONE','PASSIVE_PROTECTION_DETECTED')
        AND a.submit_intent_at IS NULL AND a.submission_confirmation_seen=0
        AND (a.retry_at IS NULL OR a.retry_at<=?)
        AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id
                        AND s.value NOT IN ('false','null','0'))
        ORDER BY j.priority DESC,j.discovered_at,a.id''', (now(),))
    from .candidate_policy import preflight
    from .answers import AnswerResolver
    snapshot = AnswerResolver(config, db).refresh()
    return [r['id'] for r in rows if r['id'] not in EXCLUDED and r['id'] not in seen
            and freshness_state(r['posted_at'], config['jobs']['max_listing_age_days']) == 'FRESH'
            and preflight(db, config, db.application(r['id']), snapshot=snapshot,
                          mode='fill_only', historical_exclusions=EXCLUDED).proceed]


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
              'reconstruction_verified':False,
              'readiness':'RECONSTRUCTABLE_CHECKPOINT' if app['application_state']=='READY_FOR_MANUAL_SUBMIT' else 'FILLED_CHECKPOINT',
              'validated_at':now() if app['application_state']=='READY_FOR_MANUAL_SUBMIT' else None}
    path = engine.config.private / 'fill-only-checkpoints' / f'{app_id}.json'
    atomic_json(path, record)
    with engine.db.lifecycle.atomic():
        engine.db.set_setting(f'fill_checkpoint:{app_id}', {k:record[k] for k in ('readiness','reconstruction_verified','validated_at')})
        engine.db.event(app_id,'FILL_ONLY_CHECKPOINT',json.dumps({'path':str(path),'state':record['state'],'readiness':record['readiness']}))
    return record


async def verify_reconstruction(engine, app_id):
    """Explicit stronger verification: replay once in the sole owned tab."""
    verify_safety(engine.db)
    engine.db.lifecycle.begin_reconstruction(app_id)
    page = engine.retained_pages[app_id]
    engine.handoff.pages.pop(app_id, None)
    app = engine.db.application(app_id)
    original_resume = app['resume_sha256']
    state, reason = await engine.browser.navigate(page, app['canonical_url'])
    if state:
        engine.retained_pages[app_id] = page
        engine.db.lifecycle.record_hold(app_id, 'Reconstruction interrupted: ' + str(state), 'RECONSTRUCTION_FAILED')
        raise RuntimeError('RECONSTRUCTION_FAILED: ' + str(state))
    # A fresh document has no carried file attachment. Keep observer callbacks bound
    # to the same tracker object while clearing evidence from the previous document.
    page._autoapply_attached_documents = {}
    page._autoapply_uploads.__init__()
    engine.browser.reset_observation(page)
    await engine._process_one(app_id, preserved_page=page)
    app = engine.db.application(app_id)
    if (app['application_state'] != 'READY_FOR_MANUAL_SUBMIT'
            or app['resume_sha256'] != original_resume):
        raise RuntimeError('RECONSTRUCTION_FAILED: ' + app['failure_reason'])
    record = await checkpoint(engine, app_id, page)
    record.update(reconstruction_verified=True, live_session_required=False,
                  readiness='RECONSTRUCTION_VERIFIED',
                  reconstruction_method='Navigate canonical URL and replay verified saved answers with Engine(fill_only=True)',
                  validation_status='PASS', upload_ready=True)
    atomic_json(engine.config.private / 'fill-only-checkpoints' / f'{app_id}.json', record)
    with engine.db.lifecycle.atomic():
        engine.db.set_setting(f'fill_checkpoint:{app_id}', {k:record[k] for k in ('readiness','reconstruction_verified','validated_at')})
        engine.db.event(app_id, 'FILL_ONLY_RECONSTRUCTION_VERIFIED', json.dumps({'url':page.url,'upload_ready':True}))
    await release_checkpoint(engine, app_id)
    return record


class BatchReport:
    def __init__(self, path):
        self.path = path
        self.data = dict(batch_run_id=uuid.uuid4().hex,started_at=now(),updated_at=now(),
            worker_pid=os.getpid(),browser_pid=None,current_application_id=None,
            listings_considered=0,applications_opened=0,applications_filled=0,ready_count=0,
            skipped_ineligible=0,skipped_closed=0,blocked_questions=0,active_tab_count=0,
            max_active_application_tabs=MAX_ACTIVE_APPLICATION_TABS,max_active_tabs_observed=0,
            final_submit_clicks=0,employer_submission_requests=0,submission_intents_created=0,auto_submit=False,
            result='PREFLIGHT',applications=[],smoke_test='NOT_STARTED')
        if path.exists():
            atomic_json(path.with_name('fill-only-batch-previous.json'),json.loads(path.read_text(encoding='utf-8')))

    def save(self, **updates):
        self.data.update(updates,updated_at=now())
        atomic_json(self.path,self.data)
