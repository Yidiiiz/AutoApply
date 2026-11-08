"""Bounded Workday readiness and guarded application-mode navigation."""
import asyncio
import json
import re
import time
from urllib.parse import urlsplit

from .applications import FIELD_SCRIPT, GenericApplicationAdapter
from .form_controls import DOM_VISIBILITY_JS
from .browser import classify_page_condition
from .cursor import click_element, get_cursor
from .cursor.types import CursorCancelledError, CursorError
from .cursor.target import ActionabilityValidator
from .inspection import dom_revision, wait_for_dom_change
from .scrolling import NavigationError
from .security import SecurityDetector
from .patience import DEFAULT_READINESS_TIMEOUT, TransitionPatience, page_policy
from playwright.async_api import Error as BrowserError


# Hidden native upload inputs are deliberately not actionable here. Supporting a
# custom picker requires an observed, verified association, not a template guess.
VISIBLE = r'''e => {
 if (!e.isConnected || e.closest('template,[hidden],[inert],[aria-hidden="true"],[aria-busy="true"]')) return false;
 if (e.matches(':disabled') || e.getAttribute('aria-disabled')==='true') return false;
 for(let n=e;n;n=n.parentElement) {
   const s=getComputedStyle(n);
   if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0) return false;
 }
 const r=e.getBoundingClientRect();
 return r.width>0 && r.height>0;
}'''

# Keep disabled choices in observations: they identify the chooser, but never
# authorize input. Identities are kept in a WeakMap, not applicant-field tokens.
ENTRY_SELECTOR = 'button,a,[role="button"],[role="link"],input[type="button"],input[type="submit"]'
ENTRY_OBSERVATION = r'''
 const normalize=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
 const labels={'autofill with resume':'autofillWithResume','apply manually':'applyManually',
               'use my last application':'useMyLastApplication'};
 const modes=Object.values(labels);
 const route=raw=>{
   try {
     const u=new URL(raw,location.href);
     const match=u.pathname.replace(/\/+$/,'').match(/\/apply\/(autofillWithResume|applyManually|useMyLastApplication)$/i);
     const mode=match&&modes.find(m=>m.toLowerCase()===match[1].toLowerCase());
     const safe=!!mode&&u.protocol==='https:'&&u.origin===location.origin&&
       /\.(myworkdayjobs|myworkdaysite)\.com$/i.test(u.hostname)&&!u.username&&!u.password;
     return {mode,safe};
   } catch { return {mode:null,safe:false}; }
 };
 const displayed=e=>{
   if(!e.isConnected||e.closest('template,[hidden],[inert],[aria-hidden="true"]'))return false;
   for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);
     if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
   const r=e.getBoundingClientRect();return r.width>0&&r.height>0;
 };
 const choices=[...document.querySelectorAll('__ENTRY_SELECTOR__')].map((e,index)=>{
   const labelled=(e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ');
   const names=[e.getAttribute('aria-label'),labelled,e.innerText||e.value].map(normalize).filter(Boolean);
   const named=names.map(n=>labels[n]).filter(Boolean);
   const raw=[e.getAttribute('href'),e.getAttribute('formaction'),e.getAttribute('action')].filter(v=>v!==null);
   const routes=raw.map(route);
   const signals=[...new Set([...named,...routes.map(r=>r.mode).filter(Boolean)])];
   if(!signals.length)return null;
   if(!g.ids.has(e))g.ids.set(e,++g.next);
   const target=e.getAttribute('target')||document.querySelector('base')?.getAttribute('target');
   return {id:g.ids.get(e),index,modes:signals,displayed:displayed(e),
     usable:usable(e)&&!e.hasAttribute('disabled')&&!e.closest('[aria-disabled="true"]'),
     conflict:signals.length!==1||routes.some(r=>!r.safe)||names.some(n=>/\bsubmit\b/i.test(n))||
       !!(target&&target.toLowerCase()!=='_self')||e.hasAttribute('download')||
       !!(e.form&&e.type==='submit'),names,raw};
 }).filter(Boolean);
 const heading=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
   .some(e=>displayed(e)&&normalize(e.innerText)==='start your application');
 const choice=heading||choices.some(c=>c.displayed);
 const manualRoute=route(location.href).safe&&route(location.href).mode==='applyManually';
'''.replace('__ENTRY_SELECTOR__', ENTRY_SELECTOR)

OBSERVE = r'''() => {
 const usable=__VISIBLE__;
 const loading=[...document.querySelectorAll('[aria-busy="true"],[role="progressbar"],[data-automation-id*="loading" i],[data-automation-id*="spinner" i],[data-automation-id*="skeleton" i],[class*="skeleton" i],[class*="spinner" i],[class*="loading" i]')]
   .some(e=>e.isConnected&&!e.closest('[hidden],[aria-hidden="true"],template')&&e.getBoundingClientRect().width>0&&getComputedStyle(e).visibility!=='hidden');
 const roots=[...document.querySelectorAll('form,[data-automation-id="applicationPage"],[data-automation-id="jobApplicationPage"]')].filter(usable);
 const fields=roots.flatMap(root=>[...root.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]),textarea,select,[role="combobox"],[role="checkbox"]')].filter(usable));
 const actions=[...document.querySelectorAll('button,[role="button"],input[type="submit"]')].filter(e=>usable(e)&&/^(Next|Continue|Review application|Save and continue|Submit application|Submit my application|Submit)$/i.test((e.innerText||e.value||'').trim()));
 const entries=[...document.querySelectorAll('button,a,[role="button"]')].filter(e=>usable(e)&&/^(Apply|Apply now|Apply for this job)$/i.test((e.innerText||'').trim()));
 if(!window.__autoapplyWorkday)window.__autoapplyWorkday={ids:new WeakMap(),next:0};
 const g=window.__autoapplyWorkday;
 __ENTRY_OBSERVATION__
 const signature=es=>es.map(e=>{
   if(!g.ids.has(e))g.ids.set(e,++g.next);
   const r=e.getBoundingClientRect();
   return [g.ids.get(e),e.tagName,e.type,e.id,e.name,e.getAttribute('aria-label'),r.x,r.y,r.width,r.height];
 });
 return {loading,choice,choices,manualRoute,documentReady:document.readyState,form:roots.length>0&&fields.length>0&&actions.length===1,
   entry:entries.length===1,fields:fields.length,files:fields.filter(e=>e.type==='file').length,
   signature:signature([...roots,...fields,...actions,...entries])};
}'''.replace('__VISIBLE__', VISIBLE).replace('__ENTRY_OBSERVATION__', ENTRY_OBSERVATION)


class WorkdayAdapter(GenericApplicationAdapter):
    name = 'workday'
    requires_verified_resume = True
    defer_upload_until_present = True
    readiness_timeout = DEFAULT_READINESS_TIMEOUT
    stable_seconds = .3
    field_script = FIELD_SCRIPT.replace(
        DOM_VISIBILITY_JS,
        'const visible = ' + VISIBLE + ';').replace(
        "&& kind !== 'file'", '')

    def record_readiness(self, state, **detail):
        self.readiness = {'state':state, **detail}
        self.emit('WORKDAY_FORM_READINESS', self.readiness)
        return state

    async def observe_ready(self):
        security, snapshot = await SecurityDetector().detect(
            self.page, getattr(self.page, '_autoapply_observation', None))
        condition, reason = classify_page_condition(security, snapshot)
        if condition:
            blocked = security.blocking or str(condition) in {'AUTH_REQUIRED', 'MANUAL_REVIEW'}
            self.record_readiness('SECURITY_OR_AUTH' if blocked else 'TERMINAL_FAILURE')
            raise self.transition_error('AUTH_REQUIRED' if str(condition)=='AUTH_REQUIRED' else
                'SECURITY_CONTROL' if blocked else 'FORM_TERMINAL_FAILURE', reason)
        return await self.page.evaluate(OBSERVE)

    async def readiness_sample(self):
        try:
            return await dom_revision(self.page), await self.observe_ready()
        except BrowserError as exc:
            # A real link navigation may destroy the old JS context between
            # reads. Retry observation within the existing deadline, never input.
            if 'Execution context was destroyed' not in str(exc):
                raise
            await asyncio.sleep(.05)
            return None, None

    def patience(self):
        policy = page_policy(self.page)
        return TransitionPatience(policy, timeout=max(policy.timeout, self.readiness_timeout)
                                 if self.readiness_timeout == DEFAULT_READINESS_TIMEOUT else self.readiness_timeout,
                                 interaction_at=getattr(self.page, '_autoapply_workday_interaction_at', None))

    def observe_patience(self, wait, evidence, *, failure='FORM_NOT_READY'):
        summary = {key:evidence.get(key) for key in
                   ('loading', 'choice', 'manualRoute', 'form', 'fields', 'files', 'documentReady')}
        # URL is used for comparison only; telemetry contains no URL or field data.
        progress = (urlsplit(self.page.url).path, tuple(summary.values()))
        wait.observe(progress=progress, failure=failure, summary=summary)

    def readiness_error(self, category, message, wait):
        error = self.transition_error(category, message)
        error.transient_readiness = wait.telemetry()
        return error

    async def wait_ready(self, *, shell=False, allow_choice=True):
        wait = self.patience()
        stable_since, previous = None, None
        chooser_since, chooser_key = None, None
        confirmed = getattr(self.page, '_autoapply_workday_manual_entry_confirmed', False)
        self.record_readiness('POST_ENTRY_LOADING' if confirmed else 'LOADING', phase='shell' if shell else 'form')
        unresolved = False
        try:
            async with asyncio.timeout(wait.hard_remaining):
                while wait.remaining:
                    marker, evidence = await self.readiness_sample()
                    if evidence is None:
                        stable_since, previous = None, None
                        continue
                    # The page-session latch is authoritative after delivery.
                    # A stale chooser on the manual route cannot reopen that decision.
                    if evidence['choice'] and not evidence['loading'] and allow_choice and not confirmed:
                        return self.record_readiness('APPLICATION_ENTRY_CHOICE')
                    unresolved = bool(evidence['choice'] and not evidence['loading'] and
                                      not evidence['manualRoute'] and not evidence['form'] and
                                      any(c['displayed'] and c['usable'] for c in evidence['choices']))
                    current_chooser = (self.page.url, marker, json.dumps(evidence['choices'], sort_keys=True))
                    if not unresolved or current_chooser != chooser_key:
                        chooser_since = time.monotonic() if unresolved else None
                    chooser_key = current_chooser
                    # The stable failure predicate is absence of positive form
                    # readiness; a specific chooser hold additionally requires
                    # stable actionable current markup, checked below.
                    self.observe_patience(wait, evidence)
                    candidate = not evidence['choice'] and not evidence['loading'] and (evidence['form'] or shell and evidence['entry'])
                    key = (self.page.url, marker, json.dumps(evidence, sort_keys=True))
                    if candidate:
                        if previous != key:
                            stable_since = time.monotonic()
                        if stable_since is not None and time.monotonic()-stable_since >= self.stable_seconds:
                            wait.ready()
                            return self.record_readiness('FORM_READY' if evidence['form'] else 'SHELL_READY',
                                                         fields=evidence['fields'], files=evidence['files'], patience=wait.telemetry())
                    else:
                        stable_since = None
                    previous = key
                    await wait_for_dom_change(self.page, marker, min(wait.policy.poll_seconds, max(.001,wait.remaining))*1000)
        except TimeoutError:
            pass
        # Only stable current chooser markup away from the completed manual route
        # can represent another unresolved decision. Otherwise this is readiness.
        stable_choice = (unresolved and chooser_since is not None and
                         time.monotonic()-chooser_since >= wait.policy.stable_seconds)
        category = 'APPLICATION_ENTRY_MODE_UNRESOLVED' if stable_choice and wait.stable_failure else 'FORM_NOT_READY'
        self.record_readiness('TIMEOUT_UNCONFIRMED', patience=wait.telemetry())
        raise self.readiness_error(category, 'Workday site/form readiness timed out; no blind retry', wait)

    async def inspect(self):
        await self.wait_ready(shell=True)
        return await super().inspect()

    async def begin(self):
        state = await self.wait_ready(shell=True)
        if state == 'SHELL_READY':
            if getattr(self.page, '_autoapply_workday_apply_attempted', False):
                await self.require_form()
                return
            pattern = re.compile(r'^(Apply|Apply now|Apply for this job)$', re.I)
            entries = []
            for role in ('button', 'link'):
                for target in await self.page.get_by_role(role, name=pattern).all():
                    if await target.evaluate(VISIBLE):
                        entries.append(target)
            if len(entries) != 1:
                raise self.transition_error('FORM_NOT_READY', 'No unique stable Workday application entry')
            # The normal production cursor performs fresh security, visibility,
            # geometry and hit-test checks. One delivery only, never a click loop.
            self.page._autoapply_workday_apply_attempted = True
            self.page._autoapply_workday_interaction_at = time.monotonic()
            await click_element(self.page, entries[0])
            state = await self.wait_ready()
        if state == 'APPLICATION_ENTRY_CHOICE':
            await self.select_entry_mode()
        await self.require_form()

    def manual_choice(self, evidence):
        choices = evidence['choices']
        manual = [c for c in choices if 'applyManually' in c['modes']]
        if (not evidence['choice'] or evidence['loading'] or evidence['form'] or
                len(manual) != 1 or not manual[0]['usable'] or any(c['conflict'] for c in choices)):
            raise self.transition_error('APPLICATION_ENTRY_MODE_UNRESOLVED',
                                        'No unambiguous actionable Workday Apply Manually option')
        return manual[0]

    async def wait_manual_choice(self):
        """A missing/disabled option may be hydrating; unsafe choices still stop."""
        wait = self.patience()
        try:
            async with asyncio.timeout(wait.hard_remaining):
                while wait.remaining:
                    marker, evidence = await self.readiness_sample()
                    if evidence is None:
                        continue
                    manual = [c for c in evidence['choices'] if 'applyManually' in c['modes']]
                    if evidence['form'] or len(manual) > 1 or any(c['conflict'] for c in evidence['choices']):
                        return self.manual_choice(evidence)  # Definitively ambiguous/unsafe: no waiting or input.
                    self.observe_patience(wait, evidence, failure='APPLICATION_ENTRY_MODE_UNRESOLVED')
                    if not evidence['loading'] and len(manual) == 1 and manual[0]['usable']:
                        return self.manual_choice(evidence)
                    await wait_for_dom_change(self.page, marker, min(wait.policy.poll_seconds, max(.001,wait.remaining))*1000)
        except TimeoutError:
            pass
        self.record_readiness('TIMEOUT_UNCONFIRMED', patience=wait.telemetry())
        raise self.readiness_error('APPLICATION_ENTRY_MODE_UNRESOLVED',
                                   'No actionable manual entry after bounded observation', wait)

    async def select_entry_mode(self):
        # Session-scoped latch also prevents a new adapter on this page from
        # repeating an interaction whose delivery might be ambiguous.
        if getattr(self.page, '_autoapply_workday_entry_attempted', False):
            raise self.transition_error('TRANSITION_UNCONFIRMED', 'Entry selection already attempted; no retry')
        # Clear a cookie backdrop before the delivery guard is installed.
        from .consent import dismiss_consent, dismiss_overlay
        await dismiss_consent(self.page, click_element)
        await dismiss_overlay(self.page, click_element)
        chosen = await self.wait_manual_choice()
        cursor = get_cursor(self.page)
        original_guard = cursor.security_guard
        possible_delivery = False
        before_path = urlsplit(self.page.url).path.rstrip('/').lower()
        async def guard():
            nonlocal possible_delivery
            if str(cursor.state) in {'PRESSING', 'WAITING_FOR_RESULT'}:
                possible_delivery = True
            if original_guard and await original_guard():
                return True
            fresh = await self.observe_ready()
            if not possible_delivery and self.manual_choice(fresh) != chosen:
                raise self.transition_error('APPLICATION_ENTRY_MODE_UNRESOLVED', 'Entry option changed before delivery')
            return False
        try:
            async with asyncio.timeout(self.readiness_timeout):
                # Fresh pre-delivery validation retains the exact chosen identity.
                if self.manual_choice(await self.observe_ready()) != chosen:
                    raise self.transition_error('APPLICATION_ENTRY_MODE_UNRESOLVED', 'Entry option changed before delivery')
                before_path = urlsplit(self.page.url).path.rstrip('/').lower()
                target = self.page.locator(ENTRY_SELECTOR).nth(chosen['index'])
                cursor.security_guard = guard
                self.page._autoapply_workday_entry_attempted = True
                self.page._autoapply_workday_interaction_at = time.monotonic()
                await click_element(self.page, target)
                possible_delivery = True
        except (CursorError, NavigationError, BrowserError, TimeoutError) as exc:
            # Input backend failures while pressing also leave delivery unknown.
            possible_delivery |= str(cursor.state) in {'PRESSING', 'WAITING_FOR_RESULT'} or any(
                e['event']=='aborted' and e['state'] in {'PRESSING', 'WAITING_FOR_RESULT'}
                for e in cursor.events[-3:])
            # The guard can observe the next page (account sign-in) before the
            # cursor completes; report that real outcome, not an entry failure.
            cause = exc
            while cause is not None and getattr(cause, 'category', None) is None:
                cause = cause.__cause__ or cause.__context__
            if getattr(cause, 'category', None) in {'AUTH_REQUIRED', 'SECURITY_CONTROL'}:
                raise cause from exc
            # Workday updates the URL while its chooser settles; the cursor cancels
            # on any navigation. Delivered: confirm by positive evidence below.
            # Not yet pressed: one fresh retry of the same exact option.
            navigated = (isinstance(exc, CursorCancelledError) and
                         any(e.get('event') == 'cancelled' and e.get('reason') == 'navigation' for e in cursor.events[-4:]))
            if navigated and possible_delivery:
                cursor.security_guard = original_guard
                self.emit('WORKDAY_ENTRY_MODE_SELECTED', {'mode':'applyManually', 'navigation_cancelled': True})
                await self.confirm_entry_transition(before_path)
                return
            if navigated and not getattr(self.page, '_autoapply_workday_entry_retried', False):
                cursor.security_guard = original_guard
                self.page._autoapply_workday_entry_retried = True
                self.page._autoapply_workday_entry_attempted = False
                await asyncio.sleep(.5)
                return await self.select_entry_mode()
            category = 'TRANSITION_UNCONFIRMED' if possible_delivery else 'APPLICATION_ENTRY_MODE_UNRESOLVED'
            raise self.transition_error(category, f'Guarded entry interaction failed ({type(exc).__name__}: {str(exc)[:120]}); no retry') from exc
        finally:
            cursor.security_guard = original_guard
        self.emit('WORKDAY_ENTRY_MODE_SELECTED', {'mode':'applyManually'})
        await self.confirm_entry_transition(before_path)

    async def confirm_entry_transition(self, before_path):
        wait = self.patience()
        stable_since = None
        self.record_readiness('LOADING', phase='entry_transition')
        try:
            async with asyncio.timeout(wait.hard_remaining):
                while wait.remaining:
                    marker, evidence = await self.readiness_sample()
                    if evidence is None:
                        stable_since = None
                        continue
                    # Disappearance alone, unrelated DOM churn, or a changed URL
                    # with a still-present chooser does not confirm delivery.
                    new_route = evidence['manualRoute'] and urlsplit(self.page.url).path.rstrip('/').lower() != before_path
                    self.observe_patience(wait, evidence, failure='TRANSITION_UNCONFIRMED')
                    advanced = (not evidence['choice'] and (evidence['form'] or evidence['loading'] or new_route) or
                                new_route and evidence['loading'])
                    if advanced:
                        stable_since = stable_since or time.monotonic()
                        if time.monotonic()-stable_since >= self.stable_seconds:
                            wait.ready()
                            self.page._autoapply_workday_manual_entry_confirmed = True
                            self.emit('WORKDAY_ENTRY_TRANSITION_CONFIRMED', {'mode':'applyManually'})
                            self.record_readiness('WORKDAY_MANUAL_ENTRY_CONFIRMED', patience=wait.telemetry())
                            return
                    else:
                        stable_since = None
                    await wait_for_dom_change(self.page, marker, min(wait.policy.poll_seconds, max(.001,wait.remaining))*1000)
        except TimeoutError:
            pass
        self.record_readiness('TIMEOUT_UNCONFIRMED', patience=wait.telemetry())
        raise self.readiness_error('TRANSITION_UNCONFIRMED', 'Apply Manually transition unconfirmed; no retry', wait)

    async def require_form(self):
        await self.wait_ready(allow_choice=False)

    async def get_questions(self, app):
        await self.require_form()
        return await super().get_questions(app)

    async def _upload_documents(self, q, path):
        await self.require_form()
        frame, tokens = self.controls[q.key]
        field = frame.locator('[data-autoapply-field="' + tokens[0] + '"]')
        if await field.count() != 1 or not await field.evaluate(VISIBLE):
            raise self.transition_error('UPLOAD_CONTROL_NOT_READY', 'Workday file control is not actionable')
        await ActionabilityValidator().check(field, 1000)
        await super()._upload_documents(q, path)
        from .uploads import SessionAttachment
        self.uploads[q.key] = (SessionAttachment(self.page, 'input[type="file"][data-autoapply-field="' + tokens[0] + '"]',
                                                require_actionable=True), path.name, path.stat().st_size)

    def transitioned(self):
        from .uploads import SessionAttachment
        for attachment, _, _ in self.uploads.values():
            if isinstance(attachment, SessionAttachment):
                attachment.advance()
        super().transitioned()
