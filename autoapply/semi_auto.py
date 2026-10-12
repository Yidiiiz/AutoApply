"""Bounded retained forms, one automatic writer, explicit human review authority."""
import asyncio
import json
import logging
import uuid

from .archive import atomic_json
from .batch_approval import is_final_submission, request_body
from .fill_batch import FillOnlyInvariant, checkpoint, release_checkpoint, discover, verify_safety, PROTECTED
from .manual import enqueue_inspection
from .models import now
from .status import snapshot

log = logging.getLogger('autoapply')


class ReviewGuard(FillOnlyInvariant):
    """Automated final actions stay forbidden; an idle human may review one page.

    Arming is a Discord operator action, never an Engine action. No automated
    processing runs while any page is armed. An observed final mutation latches
    an uncertain manual outcome and prevents automatic continuation/replay.
    """
    def __init__(self, db):
        super().__init__(db)
        self.review_pages = {}
        self.manual_requests = set()
        self.changed = False

    async def route(self, route):
        request = route.request
        if is_final_submission(request.url, request.method, request_body(request)):
            page = request.frame.page
            app_id = self.review_pages.get(page)
            if app_id is not None:
                if app_id not in self.manual_requests:
                    self.manual_requests.add(app_id)
                    self.changed = True
                    self.db.event(app_id, 'HUMAN_SUBMISSION_OBSERVED', 'Manual review network mutation; outcome unconfirmed')
                    self.db.lifecycle.record_hold(app_id, 'Manual submission observed; inspect employer confirmation',
                        'MANUAL_SUBMISSION_UNCONFIRMED', unknown=True, manual_resume_allowed=0)
                await route.fallback()
                return
            self.submission_requests_blocked += 1
            await route.abort('blockedbyclient')
            return
        await route.fallback()

    def observed_request(self, request):
        # A manual request is not an engine request. Unknown pages fail closed.
        if is_final_submission(request.url, request.method, request_body(request)):
            if request.frame.page not in self.review_pages:
                self.employer_submission_requests += 1

    async def install(self, context):
        await context.expose_binding('__autoapplyFinalClick', self.click)
        script = """(() => {
          if (window.__autoapplyFinalObserved) return;
          window.__autoapplyFinalObserved=true;
          document.addEventListener('click',e=>{
            const b=e.target.closest('button,input[type=submit],[role=button]');
            if (!b) return;
            const s=(b.getAttribute('aria-label')||b.innerText||b.value||'').trim();
            if (/^(submit|send)( application)?$|submit application|finalize application/i.test(s))
              window.__autoapplyFinalClick(e.isTrusted);
          },true);
        })();"""
        await context.add_init_script(script)
        for page in context.pages:
            await page.evaluate(script)
        context.on('requestfinished', self.observed_request)
        await context.route('**/*', self.route)

    def click(self, source, trusted):
        if source['page'] not in self.review_pages or trusted is not True:
            self.observed_click()
        else:
            app_id = self.review_pages[source['page']]
            self.db.event(app_id, 'HUMAN_FINAL_CLICK_OBSERVED', 'Trusted click in explicitly armed manual review')
            # Click evidence alone bars revalidation, even without network evidence.
            self.manual_requests.add(app_id)
            self.changed = True
            self.db.lifecycle.record_hold(app_id, 'Manual final click observed; inspect employer confirmation',
                'MANUAL_SUBMISSION_UNCONFIRMED', unknown=True, manual_resume_allowed=0)


RERUNNABLE = frozenset({'ENGINEERING_FAILURE','APPLICATION_ENTRY_MODE_UNRESOLVED','FORM_NOT_READY',
                        'ADAPTER_CAPABILITY_FAILURE','FORM_INVENTORY_UNSTABLE','INPUT_REQUIRED'})


def authorize_rerun(db, app_id, source):
    """Operator-named rerun of a zero-delivery entry failure; never a submission replay."""
    if not source.strip():
        raise ValueError('Explicit operator authorization source required for --rerun')
    from .fill_batch import EXCLUDED
    with db.lifecycle.atomic():
        app = db.application(app_id)
        if (not app or app_id in PROTECTED or app_id in EXCLUDED or db.automation_retired(app_id)
                or app['submit_intent_at'] or app['submission_confirmation_seen']
                or app['delivery_state'] != 'NOT_STARTED'
                or app['security_state'] not in {'NONE','PASSIVE_PROTECTION_DETECTED'}
                or app['status'] not in {'MANUAL_REVIEW','RETRY','QUEUED'}
                or app['status'] == 'MANUAL_REVIEW' and app['error_category'] not in RERUNNABLE
                or app['attempts'] >= app['max_retries'] + 1 or not db.guard_listing(app_id)):
            raise ValueError(f'Application {app_id} is not eligible for an operator rerun')
        if app['status'] == 'MANUAL_REVIEW':
            db.set_setting(f'manual_session:{app_id}', None)
            db.lifecycle.transition(app_id, 'RETRY', 'Operator rerun: ' + source, retry_at=None,
                attempt_started=0, manual_action_required=0, manual_action_reason='', error_category='',
                retry_allowed=1, manual_resume_allowed=0, session_preserved=0)
        db.event(app_id, 'OPERATOR_RERUN_AUTHORIZED', json.dumps({'source': source, 'auto_submit': False}))
    return True


class SemiAutoLoop:
    def __init__(self, config, db, engine=None, rerun=(), rerun_source='', limit=5):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('--limit must be an integer between 1 and 10')
        from .engine import Engine
        self.config, self.db = config, db
        self.engine = engine or Engine(config, db, fill_only=True, preserve_fill_session=True, semi_auto=True)
        if (not self.engine.fill_only or not self.engine.preserve_fill_session or not self.engine.semi_auto
                or self.engine.browser.context is not None):
            raise ValueError('Semi-auto requires a fresh dedicated owner in preserved fill-only mode')
        self.engine.fill_invariant = ReviewGuard(db)
        self.engine.browser.fill_invariant = self.engine.fill_invariant
        self.engine.control.semi_auto = self
        self.guard = self.engine.fill_invariant
        self.ids = []
        self.seen = set()
        self.batch_id = None
        self.phase = 'STARTING'
        self.ready_to_refill = set()
        self.next_requested = False
        self.rerun = [int(i) for i in rerun]
        self.rerun_source = rerun_source
        self.batch_size = limit
        self.engine.fill_tab_limit = limit
        self.engine.browser.max_active_application_tabs = limit

    def stop_requested(self):
        if self.db.setting('stop_requested', False):
            self.engine.stop_event.set()
        return self.engine.stop_event.is_set()

    def live_pages(self):
        return {i:p for i in self.ids if (p:=self.engine.control.live_page(i)) is not None and not p.is_closed()}

    def verify_page_ownership(self):
        if set(self.engine.browser.application_pages) != set(self.live_pages().values()):
            raise RuntimeError('Unowned application page appeared; semi-auto stopped')

    def answer_ready(self, app_id):
        if app_id in self.ids and not self.engine.control.pending_input_blocker(app_id):
            self.ready_to_refill.add(app_id)

    def summary(self):
        lines = ['AutoApply · batch review']
        for i in self.ids:
            app = self.db.application(i)
            saved = self.db.setting(f'fill_checkpoint:{i}', {})
            pending = self.db.one("SELECT count(*) n FROM questions WHERE application_id=? AND status='PENDING'", (i,))['n']
            ready = app['application_state'] == 'READY_FOR_MANUAL_SUBMIT'
            status = 'Ready to review' if ready else f'{pending} answers needed' if pending else app['error_category'] or app['status']
            lines.append(f"#{i} {app['company']} — {status} · {saved.get('committed_field_count',0)} verified fields")
        lines.extend(['Choose Review to unlock your browser Submit button; AutoApply never clicks it.',
                      'Answer questions first. Use !lock before refilling; !submitted ID <confirmation> records your outcome.',
                      f'Close finished tabs. !next starts the next {self.batch_size} only when this batch has no open forms.'])
        return '\n'.join(lines)

    def publish(self):
        data = dict(batch_id=self.batch_id, phase=self.phase, application_ids=self.ids,
                    live_ids=list(self.live_pages()), updated_at=now(), auto_submit=False,
                    applications=[snapshot(self.db,i,live_page=self.engine.control.live_page(i),
                        live_browser=self.engine.browser.context is not None) for i in self.ids],
                    **self.guard.snapshot())
        atomic_json(self.config.private/'semi-auto-result.json', data)
        return data

    async def fill_batch(self):
        if self.live_pages() or self.guard.review_pages:
            raise ValueError('Finish and close current forms before another batch')
        verify_safety(self.db)
        reruns, self.rerun = [i for i in self.rerun if authorize_rerun(self.db, i, self.rerun_source)], []
        self.ids = []
        self.batch_id = uuid.uuid4().hex
        self.phase = 'FILLING'
        self.publish()
        retained, probes = 0, 0
        # Never pass the event-loop SQLite connection to a thread. Discovery
        # gets its own read-only connection and returns plain ids only.
        from .database import Database
        path = self.db.one('PRAGMA database_list')['file']
        history_root, seen = self.db.history.root, frozenset(self.seen)
        def select_candidates():
            with Database.discovery_reader(path, history_root) as reader:
                return discover(reader, self.config, seen)
        selected = await asyncio.to_thread(select_candidates)
        candidates = reruns + [i for i in selected if i not in PROTECTED and i not in reruns]
        for i in candidates:
            if retained >= self.batch_size or probes >= 40:
                break
            if self.db.setting('paused',False) or self.stop_requested():
                break
            probes += 1
            # Never choose a listing whose employer page already reports it closed/removed.
            if not await self.listing_open(i):
                self.seen.add(i)
                continue
            if self.db.setting('paused',False) or self.stop_requested():
                break
            await self.engine.process_one(i)
            self.seen.add(i)
            self.ids.append(i)
            if self.db.application(i)['status'] not in {'CLOSED','INVALID','INELIGIBLE'}:
                retained += 1
            self.verify_page_ownership()
            self.guard.check()
            self.engine.browser.check_tab_limit()
            self.publish()
            if self.db.application(i)['error_category'] in {'BROWSER_START_FAILED','EXTERNAL_EXECUTION_APPROVAL_REQUIRED','EXECUTION_APPROVAL_BLOCKED'}:
                break
        self.phase = 'REVIEW'
        self.publish()
        self.db.notify('semi-batch:'+self.batch_id, dict(kind='semi_batch',message=self.summary(),application_ids=self.ids))

    async def listing_open(self, app_id):
        from .liveness import listing_state
        from .models import State
        app = self.db.application(app_id)
        state = await asyncio.to_thread(listing_state, app['canonical_url'])
        self.db.event(app_id, 'LISTING_LIVENESS_PROBE', state)
        if state in {'CLOSED','REMOVED'}:
            self.engine.hold(app_id, State.CLOSED, f'Pre-selection probe: listing {state.lower()}')
            if state == 'REMOVED':
                self.db.mark_listing_closed(app['job_id'], 'REMOVED')
            log.info('[LIVENESS] Skipped application=%s listing %s', app_id, state)
            return False
        return True

    async def refill(self):
        if (self.stop_requested() or not self.ready_to_refill or self.guard.review_pages
                or self.phase != 'REVIEW' or self.db.setting('paused',False)):
            return
        self.phase = 'REFILLING'
        try:
            for i in tuple(self.ready_to_refill):
                if self.stop_requested() or self.db.setting('paused',False):
                    break
                self.ready_to_refill.discard(i)
                app = self.db.application(i)
                page = self.live_pages().get(i)
                if (page is None or i in self.guard.manual_requests or app['submit_intent_at']
                        or app['error_category'] != 'INPUT_REQUIRED' or not app['manual_resume_allowed']
                        or app['security_state'] not in {'NONE','PASSIVE_PROTECTION_DETECTED'}):
                    continue
                session = self.db.setting(f'manual_session:{i}') or {}
                if not self.db.one("SELECT 1 FROM manual_commands WHERE application_id=? AND state='PENDING'", (i,)):
                    enqueue_inspection(self.db,i,token=session.get('token'))
                await self.engine.service_manual_requests()
                self.db.notify(f'semi-refilled:{self.batch_id}:{i}:{now()}',dict(kind='semi_batch',message=self.summary(),application_ids=self.ids))
        finally:
            # No review unlock can race a writer across one of its await boundaries.
            self.phase = 'REVIEW'
            self.publish()

    async def command(self, text):
        parts = text.split(maxsplit=2)
        action = parts[0].lower() if parts else 'help'
        if action == 'batch':
            return self.summary()
        if action == 'next':
            if self.db.setting('paused',False):
                raise ValueError('Resume semi-auto processing before !next')
            if self.phase != 'REVIEW' or self.live_pages():
                raise ValueError('Close current application forms before !next')
            self.guard.review_pages.clear()
            self.next_requested = True
            return 'Next priority batch requested.'
        if action == 'lock':
            self.guard.review_pages.clear()
            return 'Submit locked. Saved answers can now be revalidated in their existing forms.'
        if action in {'review','submitted'}:
            if self.phase != 'REVIEW' or len(parts) < 2 or int(parts[1]) not in self.ids:
                raise ValueError('Select an application in the current review batch')
            i = int(parts[1]); app = self.db.application(i)
            page = self.live_pages().get(i)
            if action == 'review':
                if any(armed_id != i for armed_id in self.guard.review_pages.values()):
                    raise ValueError('Use !lock before reviewing another application')
                if self.db.setting('paused',False) or page is None or self.db.automation_retired(i) or i in self.guard.manual_requests:
                    raise ValueError('Review session unavailable or outcome already requires inspection')
                if self.db.submission_conflict(i) or app['submit_intent_at'] or app['submission_confirmation_seen']:
                    raise ValueError('Protected submission history prevents review unlock')
                self.guard.check()
                self.guard.review_pages[page] = i
                self.db.event(i,'MANUAL_REVIEW_ARMED','Operator owns final action; automated processing suspended')
                await page.bring_to_front()
                return f'Review #{i} in your dedicated browser and submit yourself. !lock returns to automatic filling.'
            if len(parts) < 3 or not parts[2].strip():
                raise ValueError('Use !submitted ID <employer confirmation or receipt> after submitting yourself')
            # A user report permanently retires automation; it is not an observed ATS confirmation.
            with self.db.transaction():
                self.db.lifecycle.record_hold(i,'User reported manual submission; automation retired',
                    'USER_REPORTED_SUBMISSION',unknown=True,manual_resume_allowed=0)
                self.db.set_setting(f'duplicate_submission_guard:{i}', dict(permanent=True,source='USER_REPORTED',evidence=parts[2]))
                self.db.event(i,'USER_REPORTED_MANUAL_SUBMISSION','Outcome reported by owner; no automated intent')
            if page:
                self.guard.review_pages.pop(page,None)
            self.publish()
            return f'#{i} retired from automation. Close its tab when you have saved the confirmation.'
        if action in {'help','status'} and len(parts)==1:
            return self.summary() + '\n!pause stops new filling; !stop ends this loop after its safe point.'
        if action == 'stop':
            self.engine.stop_event.set()
            return 'Stopping after the current safe point; checkpoints retained.'
        if action == 'pause':
            self.db.set_setting('paused',True)
            self.guard.review_pages.clear()
            return 'Paused. Manual submit locked; forms remain open.'
        if action == 'resume' and len(parts)==1:
            self.db.set_setting('paused',False)
            return 'Semi-auto processing resumed. Automatic submission remains disabled.'
        if action == 'resume-manual' and len(parts)==2 and int(parts[1]) in self.ids:
            self.answer_ready(int(parts[1]))
            return 'Same-form revalidation requested; held/security/uncertain outcomes remain protected. Use !lock first if reviewing.'
        if action not in {'answer','yes','no','skip','accept','ai','pending','application','status'}:
            raise ValueError('Use !batch, !review ID, !lock, !submitted ID <receipt>, !next, or !answer ID <answer>')
        return self.engine.control.route(text)

    async def run(self):
        try:
            await self._run()
        finally:
            self.db.set_setting('stop_requested',False)

    async def _run(self):
        from .discord_bot import DiscordBot
        if self.config['browser']['headless'] or not self.config['discord']['enabled']:
            raise ValueError('Semi-auto requires visible browser and Discord enabled')
        self.config.data['application']['auto_submit']=False
        self.db.set_setting('auto_submit',False)
        verify_safety(self.db)
        # Every exit persists paused=True. Clear that stale state at process start
        # (a pause requested while Discord connects still applies), otherwise the
        # batch loop exits immediately without processing anything.
        self.db.set_setting('paused',False)
        bot = DiscordBot(self.engine.control)
        import os
        bot_task = asyncio.create_task(bot.start(os.environ['DISCORD_BOT_TOKEN']))
        try:
            await bot.wait_for_delivery(bot_task)
            await self.fill_batch()
            while not self.stop_requested():
                self.verify_page_ownership()
                if self.db.setting('auto_submit') is not False:
                    raise RuntimeError('Auto-submit setting changed; semi-auto stopped')
                if self.db.setting('paused',False):
                    self.guard.review_pages.clear()
                if self.guard.changed:
                    self.guard.changed=False
                    self.publish()
                if bot_task.done():
                    await bot_task
                    raise RuntimeError('Discord disconnected; semi-auto stopped')
                for i in self.ids:
                    page = self.engine.control.live_page(i)
                    if page is not None and page.is_closed():
                        self.guard.review_pages.pop(page,None)
                        await release_checkpoint(self.engine,i)
                        self.publish()
                # Resume requests queued outside Discord (CLI `answer`/`resume-manual`)
                # are serviced exactly like !resume-manual for forms in this batch.
                for row in self.db.rows("SELECT DISTINCT application_id FROM manual_commands WHERE state='PENDING'"):
                    if row['application_id'] in self.ids:
                        self.answer_ready(row['application_id'])
                await self.refill()
                if self.next_requested:
                    self.next_requested=False
                    await self.fill_batch()
                await asyncio.sleep(.5)
        finally:
            self.guard.review_pages.clear()
            self.db.set_setting('paused',True)
            try:
                await self.engine.close()
            finally:
                try:
                    await bot.close()
                finally:
                    bot_task.cancel()
                    await asyncio.gather(bot_task,return_exceptions=True)
                    self.phase='STOPPED'
                    self.publish()
