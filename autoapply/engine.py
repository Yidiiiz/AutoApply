from .cursor import click_element, get_cursor, Point
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import date, datetime, timezone

from .ai import AIManager, ProviderUnavailable
from .answers import AnswerResolver, validate_answer, is_writing_question
from .applications import UnsupportedForm, adapter_for
from .archive import archive_application
from .browser import Browser, page_condition, classify_page_condition
from .control import Controller
from .jobs import eligibility
from .models import Answer, Question, State, now
from .sources import BrowserJobSource, scan_github
from .handoff import ManualHandoffManager, VERIFICATION
from .security import PreSubmitState, SubmissionClassifier, safe_url
from .retry import ErrorCategory, SiteError, Failure, Delivery
from .manual import ManualCommands
from .scrolling import NavigationError
from .submission_probe import SubmissionProbe
from .cursor.types import TargetUnstableError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

log = logging.getLogger("autoapply")


class Engine:
    def __init__(self, config, db, browser=None, *, fill_only=False):
        self.config, self.db = config, db
        self.browser = browser or Browser(config)
        self.resolver = AnswerResolver(config, db)
        self.ai = AIManager(config, db, self.browser, resolver=self.resolver)
        self.control = Controller(config, db, self.ai)
        self.next_application = 0.0
        self.stop_event = asyncio.Event()
        self.classifier = SubmissionClassifier()
        self.handoff = ManualHandoffManager(config, db, self.browser)
        self.processing_lock = asyncio.Lock()
        self.fill_only = fill_only
        self.db.lifecycle.fill_only = fill_only
        if fill_only:
            from .fill_batch import MAX_ACTIVE_APPLICATION_TABS
            self.browser.max_active_application_tabs = MAX_ACTIVE_APPLICATION_TABS
        self.retained_pages = {}
        self.control.live_page = lambda app_id: self.handoff.pages.get(app_id) or self.retained_pages.get(app_id)
        self.resume_snapshot = None
        self.fill_invariant = None
        if fill_only:
            from .fill_batch import FillOnlyInvariant
            self.fill_invariant = FillOnlyInvariant(db)
            self.browser.fill_invariant = self.fill_invariant

    def interrupted(self, app_id):
        if self.fill_only:
            self.ensure_fill_policy()
            from .fill_batch import verify_safety
            verify_safety(self.db)
            self.fill_invariant.check()
            self.browser.check_tab_limit()
        return self.stop_event.is_set() or self.db.setting("paused", False) or self.db.application(app_id)["status"] not in {"CHECKING", "APPLYING", "READY"}

    def ensure_fill_policy(self):
        if self.fill_only and self.fill_invariant is None:
            from .fill_batch import FillOnlyInvariant, MAX_ACTIVE_APPLICATION_TABS
            self.fill_invariant = FillOnlyInvariant(self.db)
            self.browser.fill_invariant = self.fill_invariant
            self.browser.max_active_application_tabs = MAX_ACTIVE_APPLICATION_TABS
            self.db.lifecycle.fill_only = True

    def adapter_event(self, app_id, kind, detail):
        """Persist semantic progress, never browser handles or applicant values."""
        with self.db.transaction():
            self.db.event(app_id, kind, json.dumps(detail))
            key = f'application_transaction:{app_id}'
            state = self.db.setting(key, {'step':1, 'next_clicks':0, 'stage':'APPLICATION_OPENED'})
            if kind in {'STEP_DISCOVERED','STEP_FILLED','UPLOAD_SELECTED','UPLOAD_READY','STEP_ADVANCED','READY_TO_SUBMIT'}:
                state['stage'] = kind
            if kind == 'STEP_DISCOVERED':
                state['inventory'] = detail
            if kind == 'STEP_NEXT_INTENT':
                self.db.lifecycle.delivery(app_id, Delivery.POSSIBLY_DELIVERED)
            if kind == 'STEP_NEXT_CLICK_DELIVERED':
                state['next_clicks'] += 1
            if kind == 'STEP_ADVANCED':
                self.db.lifecycle.delivery(app_id, Delivery.NOT_STARTED)
                state['step'] += 1
                state.pop('inventory', None)
            if kind == 'STEP_NEXT_FAILURE':
                state['stage'] = 'MANUAL_INTERVENTION_REQUIRED'
            state['updated_at'] = now()
            self.db.set_setting(key, state)

    def request(self, app, q, reason):
        row = self.db.question(app["id"], q, reason)
        return row

    def hold(self, app_id, state, reason):
        evidence = {"confirmation_text": reason, "confirmation_url": safe_url(self.db.application(app_id)["canonical_url"])} if state == State.ALREADY_APPLIED else {}
        if state == State.CLOSED:
            self.db.mark_listing_closed(self.db.application(app_id)["job_id"])
            reason = "LISTING_CLOSED: " + reason
        with self.db.transaction():
            self.db.lifecycle.transition(app_id, state, reason, **evidence)
            self.db.event(app_id, "hold", reason)

    def daily_count(self):
        # Count intent, not only confirmations: uncertain submissions also consume the ceiling.
        return self.db.one("SELECT count(*) n FROM applications WHERE substr(submit_intent_at,1,10)=?", (datetime.now(timezone.utc).date().isoformat(),))["n"]

    async def scan(self, force=False):
        if self.fill_only:
            from .fill_batch import discover
            return discover(self.db, self.config)
        self.db.maintain_listings()
        self.db._discovery_batch = True
        try:
            return await self._scan(force)
        finally:
            self.db._discovery_batch = False
            self.db.refresh_listing_statistics()

    async def _scan(self, force=False):
        counts = await scan_github(self.config, self.db, force)
        for name in ["linkedin", "handshake"]:
            if not self.config[name]["enabled"]:
                continue
            last = self.db.setting("scan:" + name, 0)
            if not force and time.time() - last < self.config["polling"][name + "_minutes"] * 60:
                continue
            self.db.set_setting("scan:" + name, time.time())
            try:
                source = BrowserJobSource(name, self.config, self.browser, self.db)
                listings = await source.discover()
                counts["pages_scanned"] += source.pages_scanned
                counts[name+"_stop_reason"] = source.stop_reason
                with self.db.ingest_batch():
                    for listing in listings:
                        _, created = self.db.ingest(listing, self.config)
                        counts["new"] += created
                        for metric,value in self.db.last_ingest_result.items():
                            counts[metric] = counts.get(metric,0) + value
            except Exception as exc:
                counts["errors"] += 1
                self.db.event(None, "source_error", name + ": " + type(exc).__name__)
                self.db.notify("source:" + name, {"message": f"{name} scan failed. Restore authentication or inspect source layout."})
        for row in self.db.rows("SELECT id FROM applications WHERE stage='' AND status IN ('INVALID','CLOSED','NEEDS_INPUT')"):
            self.db.lifecycle.metadata(row["id"], stage="discovery")
        for row in self.db.rows("SELECT DISTINCT q.application_id FROM questions q JOIN applications a ON a.id=q.application_id JOIN jobs j ON j.id=a.job_id WHERE j.listing_active=1 AND q.status='PENDING' AND a.status='NEEDS_INPUT'"):
            self.handoff.notify(row['application_id'], "Required information is pending")
        log.info("DISCOVERY COMPLETE: %s", counts)
        return counts

    async def inspect_security(self, app_id, page):
        result = await asyncio.wait_for(self.classifier.classify(page, self.browser.observation(page)), timeout=10)
        ats = self.classifier.ats(result.snapshot)
        s = result.security
        previous = self.db.application(app_id)
        ats_name = previous["ats_type"] if ats.provider in {"UNKNOWN", "custom"} and previous["ats_type"] not in {"UNKNOWN", "custom", "generic"} else ats.provider
        self.db.update_security(app_id, ats_type=ats_name, security_state=s.state,
            security_provider=s.provider if s.state != "NONE" else previous["security_provider"],
            security_type=s.type if s.state != "NONE" else previous["security_type"],
            last_http_status=s.http_status if s.http_status is not None else previous["last_http_status"],
            last_security_message=s.message or previous["last_security_message"], current_url=safe_url(page.url),
            previous_url=previous["current_url"] if previous["current_url"] != safe_url(page.url) else previous["previous_url"], diagnostics_at=now())
        if previous["ats_type"] != ats.provider:
            log.info("[ATS] Detected %s application=%s", ats.provider, app_id)
        if s.state != "NONE" and (s.state != previous["security_state"] or s.provider != previous["security_provider"]):
            log.info("[SECURITY] application=%s provider=%s state=%s", app_id, s.provider, s.state)
        return result

    async def security_gate(self, app_id, page, *, result=None):
        result = result or await self.inspect_security(app_id, page)
        if result.security.blocking:
            if result.security.state == "RATE_LIMITED" and not self.db.application(app_id)["submit_intent_at"]:
                self.db.fail(app_id, result.security.message, self.config["processing"]["max_retries"], ErrorCategory.RATE_LIMIT)
                self.db.update_security(app_id, application_state="RATE_LIMITED")
                await self.handoff.diagnostics(app_id, page)
                self.db.notify(f"rate:{app_id}:{now()}", {"application_id": app_id, "message": "Rate limited; the security hold prevents automatic retry."})
            else:
                await self.handoff.request(app_id, page, result.security.message, result.security.category)
            return True
        return False

    async def confirm(self, app_id, page, evidence):
        app = self.db.application(app_id)
        if not app["submission_confirmation_seen"]:
            self.db.lifecycle.record_confirmation(app_id, evidence, safe_url(page.url))
            self.db.notify(f"submitted:{app_id}", {"application_id": app_id, "message": f"APPLICATION SUBMITTED - {app['company']} #{app_id}\n{app['title']}\nEmployer confirmation received."})
            log.info("[SUBMIT] Confirmation detected application=%s", app_id)

    async def post_submit(self, app_id, page, *, wait=True, probe=None):
        deadline = time.monotonic() + (self.config["application"]["confirmation_timeout_seconds"] if wait else 0)
        upload_race = False
        log.info("[SUBMIT] Waiting for confirmation application=%s", app_id)
        while True:
            marker = 0 if self.browser.observation(page).get("dialog_open") else await self.browser.change_marker(page)
            result = await self.inspect_security(app_id, page)
            self.db.update_security(app_id, url_after_submit=safe_url(page.url))
            if result.confirmation:
                if probe:
                    probe.confirmed(result.confirmation)
                await self.confirm(app_id, page, result.confirmation)
            app = self.db.application(app_id)
            if result.security.blocking:
                await self.handoff.request(app_id, page, result.security.message, result.security.category)
                return
            if app["submission_confirmation_seen"]:
                if time.monotonic() < deadline:
                    await self.browser.wait_for_change(page, marker, min(500, (deadline - time.monotonic()) * 1000))
                    continue
                if app["verification_state"] == "PENDING":
                    # Disappearance alone is not evidence of passing identity verification.
                    self.db.update_security(app_id, verification_state="UNKNOWN")
                folder = archive_application(self.config, self.db, app_id)
                await self.browser.screenshot(page, folder / "screenshots", "confirmation")
                return
            upload_race = upload_race or bool(re.search(r'updating your application|uploading files', result.snapshot['text'], re.I))
            if result.validation_error and not upload_race:
                await self.handoff.request(app_id, page, "Employer form validation failed after submit; correct fields manually. Automatic resubmission disabled.", ErrorCategory.FORM_VALIDATION_ERROR)
                self.db.update_security(app_id, application_state="FILLING")
                return
            if time.monotonic() >= deadline:
                if upload_race:
                    await self.handoff.request(app_id, page, 'Employer reported an upload/update in progress after the single Submit click. No second click attempted.', ErrorCategory.UPLOAD_RACE_DETECTED)
                    return
                if probe:
                    await probe.sample()
                    if not probe.has_effect:
                        reason = ('Production click returned without observable form, navigation, or network effect. '
                                  + ('Trusted click reached the button, but no application response was observed.' if probe.data['delivered'] else
                                     'No trusted click reaching the submit button was observed; inspect submit telemetry.'))
                        await self.handoff.request(app_id, page, reason, ErrorCategory.SUBMIT_CLICK_NOT_DELIVERED)
                        return
                await self.handoff.request(app_id, page, "Submit was clicked but no affirmative confirmation was found; check the preserved page and employer history.", unknown=True)
                return
            # Wait for DOM changes, with a bounded wakeup to inspect response metadata.
            await self.browser.wait_for_change(page, marker, min(500, (deadline - time.monotonic()) * 1000))

    async def process_one(self, application_id=None):
        self.ensure_fill_policy()
        async with self.processing_lock:
            if self.fill_only:
                from .fill_batch import MAX_ACTIVE_APPLICATION_TABS
                await self.browser.enforce_application_limit(MAX_ACTIVE_APPLICATION_TABS)
                if self.retained_pages or self.handoff.pages:
                    raise RuntimeError('BATCH_TAB_LIMIT_VIOLATION: release previous application first')
                self.browser.check_tab_limit(opening=True)
                self.fill_invariant.check()
            target = self.db.setting("controlled_application_id")
            if target is not None and application_id != target:
                return False
            if self.handoff.pages and not self.fill_only:
                return False
            result = await self._process_one(application_id)
            if self.fill_invariant:
                self.fill_invariant.check()
            return result

    async def _process_one(self, application_id=None, preserved_page=None):
        self.ensure_fill_policy()
        if self.fill_invariant:
            self.fill_invariant.check()
        if self.fill_only and self.db.setting("auto_submit") is not False:
            raise RuntimeError("Fill-only requires auto_submit=false")
        if application_id is not None and self.db.automation_retired(application_id):
            return False
        if self.db.setting("paused", False) or self.stop_event.is_set():
            return False
        if self.daily_count() >= self.config["processing"]["max_applications_per_day"]:
            self.db.notify("daily-cap:" + date.today().isoformat(), {"message": "Daily application ceiling reached; new submissions resume next UTC day."})
            return False
        app = self.db.application(application_id) if preserved_page else self.db.claim(application_id)
        if not app:
            return False
        page = preserved_page
        probe = None
        try:
            app_id = app["id"]
            if not self.db.guard_listing(app_id):
                return True
            from .candidate_policy import preflight
            from .fill_batch import EXCLUDED
            snapshot = self.resolver.refresh()
            decision = preflight(self.db, self.config, app, snapshot=snapshot,
                                 mode='fill_only' if self.fill_only else 'ordinary',
                                 historical_exclusions=EXCLUDED if self.fill_only else ())
            if not decision.proceed:
                state = (State.MANUAL_REVIEW if decision.status == 'HOLD' else
                         State.CLOSED if decision.reason == 'confirmed_closed' else
                         State.INVALID if decision.reason == 'stale_listing' else State.INELIGIBLE)
                self.hold(app_id, state, 'Candidate preflight: ' + decision.reason)
                self.db.event(app_id, 'CANDIDATE_PREFLIGHT', json.dumps(asdict(decision)))
                return True
            if self.fill_only:
                from .fill_batch import MAX_ACTIVE_APPLICATION_TABS
                await self.browser.enforce_application_limit(MAX_ACTIVE_APPLICATION_TABS)
            if not preserved_page:
                self.db.lifecycle.begin_attempt(app_id, self.config["processing"]["max_retries"])
            page = page or await self.browser.new_page()
            if preserved_page:
                state, evidence = await page_condition(page)
            else:
                state, evidence = await self.browser.navigate(page, app["canonical_url"])
            if await self.security_gate(app_id, page):
                return True
            if state:
                if state in {State.MANUAL_REVIEW, State.AUTH_REQUIRED}:
                    await self.handoff.request(app_id, page, evidence, ErrorCategory.AUTH_REQUIRED if state == State.AUTH_REQUIRED else ErrorCategory.UNKNOWN_SECURITY_FAILURE)
                else:
                    self.hold(app_id, state, evidence)
                return True
            if not self.db.bind_url(app_id, page.url):
                return True
            adapter = adapter_for(page)
            if app["ats"] in {"linkedin", "handshake"}:
                external = await adapter.external_application()
                if external:
                    state, evidence = await self.browser.navigate(page, external)
                    if await self.security_gate(app_id, page):
                        return True
                    if state:
                        # The internal flow remains available on an explicit retry.
                        if state in {State.MANUAL_REVIEW, State.AUTH_REQUIRED}:
                            await self.handoff.request(app_id, page, evidence, ErrorCategory.AUTH_REQUIRED if state == State.AUTH_REQUIRED else ErrorCategory.UNKNOWN_SECURITY_FAILURE)
                        else:
                            self.hold(app_id, state, evidence)
                        return True
                    if not self.db.bind_url(app_id, page.url):
                        return True
                    adapter = adapter_for(page)
            self.db.execute("UPDATE jobs SET last_checked_at=? WHERE id=?", (now(),app["job_id"]))
            listing = await adapter.inspect()
            self.db.execute("UPDATE jobs SET description=? WHERE id=?", (listing["description"], app["job_id"]))
            if listing["location"]:
                self.db.execute("UPDATE jobs SET location=? WHERE id=?", (listing["location"], app["job_id"]))
            for key in ["company", "title"]:
                if listing[key] and (key == "title" or app[key] == "Unknown employer"):
                    self.db.execute(f"UPDATE jobs SET {key}=? WHERE id=?", (listing[key], app["job_id"]))
            app = self.db.application(app_id)
            check = eligibility(app, self.config.profile_snapshot().facts)
            self.db.execute("UPDATE jobs SET eligibility_json=? WHERE id=?", (json.dumps(asdict(check)), app["job_id"]))
            for match in check.standing_matches:
                self.db.event(app_id, "standing_eligibility_answer", json.dumps(match))
            if check.eligible is False:
                self.hold(app_id, State.INELIGIBLE, "; ".join(check.reasons))
                return True
            if check.eligible is None and not app["eligibility_override"]:
                q = Question("eligibility", "Have you verified that you meet all the listed requirements and this is a U.S. opportunity?", "radio", True, ["Yes", "No"], scope=f"application:{app_id}")
                self.request(app, q, "\n".join(check.uncertainties))
                self.hold(app_id, State.NEEDS_INPUT, "Eligibility requires verification")
                await self.handoff.request(app_id, page, "Eligibility requires verification", ErrorCategory.INPUT_REQUIRED)
                return True
            if self.interrupted(app_id):
                return True
            self.db.lifecycle.transition(app_id, State.APPLYING, stage="opening form")
            if not preserved_page or app["stage"] == "checking":
                await adapter.begin()
            boundary = await self.inspect_security(app_id, page)
            if await self.security_gate(app_id, page, result=boundary):
                return True
            existing_confirmation = boundary.confirmation
            if existing_confirmation:
                self.hold(app_id, State.ALREADY_APPLIED, "Existing confirmation was visible before AutoApply submitted: " + existing_confirmation)
                return True
            seen_pages = set()
            saved_answers = {r['field_key']: r for r in self.db.rows(
                "SELECT * FROM questions WHERE application_id=? AND status='ANSWERED' AND field_type='combobox'", (app_id,))}
            adapter.answer_hints = saved_answers
            adapter.profile = self.config.profile_snapshot().facts
            adapter.emit = lambda kind, detail: self.adapter_event(app_id, kind, detail)
            if adapter.structured_inventory:
                adapter.mapper.cache = self.db.setting('field_mapping_cache:' + adapter.name, {})
                self.db.event(app_id, 'PROFILE_PREFLIGHT', json.dumps({'missing':self.config.setup_issues()}))
            for step in range(self.config["application"]["max_pages"]):
                if self.interrupted(app_id):
                    return True
                boundary = await self.inspect_security(app_id, page)
                state, evidence = classify_page_condition(boundary.security, boundary.snapshot)
                if await self.security_gate(app_id, page, result=boundary):
                    return True
                if state:
                    if state in {State.MANUAL_REVIEW, State.AUTH_REQUIRED}:
                        await self.handoff.request(app_id, page, evidence, ErrorCategory.AUTH_REQUIRED if state == State.AUTH_REQUIRED else ErrorCategory.UNKNOWN_SECURITY_FAILURE)
                    else:
                        self.hold(app_id, state, evidence)
                    return True
                self.db.lifecycle.metadata(app_id, stage=f"form page {step + 1}")
                questions = await adapter.get_questions(app)
                step_answers = {}
                if adapter.structured_inventory:
                    support = adapter.support_report(questions)
                    self.db.event(app_id, 'ADAPTER_CAPABILITIES', json.dumps(support))
                    self.db.set_setting('field_mapping_cache:' + adapter.name, adapter.mapper.cache)
                    if not support['autonomous']:
                        raise UnsupportedForm('Missing mandatory capabilities: ' + ', '.join(support['missing']))
                    from .field_mapping import field_policy
                    preflight_missing = []
                    for question in questions:
                        if question.semantic_key.startswith(('contact.', 'education.', 'links.', 'work_authorization.')):
                            step_answers[question.key] = self.resolver.resolve_result(question, app)
                            if not step_answers[question.key].accepted:
                                preflight_missing.append({'key':question.semantic_key,'required':question.required})
                                if question.required:
                                    self.request(app, question, 'Missing verified profile value identified before filling this step')
                    self.db.event(app_id, 'PROFILE_COMPLETENESS_PREFLIGHT', json.dumps({'missing':preflight_missing}))
                    if any(item['required'] for item in preflight_missing):
                        await self.handoff.request(app_id,page,'Required profile fields are missing; no step input performed','INPUT_REQUIRED')
                        return True
                signature = tuple((q.key, q.label, tuple(q.options)) for q in questions)
                if signature in seen_pages:
                    raise UnsupportedForm("Form did not advance or repeats an indistinguishable step")
                seen_pages.add(signature)
                missing = False
                for q in questions:
                    if self.interrupted(app_id):
                        return True
                    if q.kind == "file":
                        row = self.db.question(app_id, q)
                        if re.search(r"resume|curriculum vitae|\bcv\b", q.label, re.I):
                            from .operations import FileSnapshot
                            self.resume_snapshot = (FileSnapshot.read(self.config.resume, self.resume_snapshot)
                                                    if self.config.resume.exists() else None)
                            if not self.resume_snapshot or not self.resume_snapshot.is_pdf:
                                # File contents remain local; Discord only asks whether setup is complete.
                                setup = Question("resume", "Place your current PDF at data/private/resumes/resume.pdf, then answer Ready", "text", True, scope=f"application:{app_id}")
                                self.request(app, setup, "The configured resume is missing or is not a PDF")
                                missing = True
                                continue
                            await adapter.upload_documents(q, self.config.resume)
                            digest = self.resume_snapshot.sha256
                            self.db.lifecycle.metadata(app_id, resume_used=str(self.config.resume), resume_sha256=digest)
                            self.db.save_answer(row["id"], Answer("resume.pdf", "verified_document"))
                        elif q.required:
                            self.request(app, q, "A required document needs manual preparation and upload")
                            missing = True
                        else:
                            self.db.execute("UPDATE questions SET status='SKIPPED' WHERE id=?", (row["id"],))
                        continue
                    result = self.resolver.current_result(step_answers.get(q.key), q, app)
                    q.scope, q.signature, q.policy = result.descriptor.scope, result.descriptor.signature, result.descriptor.policy
                    row = self.db.question(app_id, q)
                    policy = result.descriptor.policy
                    if policy == 'DO_NOT_ANSWER':
                        if q.required:
                            self.request(app,q,'Required field is covered by DO_NOT_ANSWER policy')
                            missing = True
                        else:
                            self.db.execute("UPDATE questions SET status='SKIPPED' WHERE id=?",(row['id'],))
                        continue
                    answer = result.answer
                    if not answer and row['status'] == 'ANSWERED':
                        answer = self.resolver.replay(row, q, app)
                    if not answer and is_writing_question(q) and policy not in {'REQUIRE_USER', 'DO_NOT_ANSWER'}:
                        answer = self.ai.verified_reuse(q, app, self.resolver.snapshot)
                    if not answer and is_writing_question(q) and policy not in {'REQUIRE_USER', 'DO_NOT_ANSWER'} and (not adapter.requires_explicit_narrative_policy or policy == 'GENERATE_GROUNDED'):
                        try:
                            answer = await self.ai.draft(q, app)
                        except ProviderUnavailable as exc:
                            reason = str(exc)
                            self.db.execute("UPDATE questions SET status='PENDING',reason=? WHERE id=?", (reason, row['id']))
                            self.request(app, q, reason)
                            missing = True
                            continue
                    if answer and answer.confidence >= self.config["application"]["min_confidence"]:
                        self.resolver.stamp(answer, result.descriptor)
                        await adapter.answer_question(q, answer)
                        with self.db.transaction():
                            self.db.save_answer(row["id"], answer)
                            self.db.event(app_id, 'LIVE_CONTROL_COMMITTED', json.dumps({'field_id':q.key,'label':q.label,
                                'source':answer.source,'evidence':getattr(adapter,'live_committed',{}).get(q.key,{}).get('evidence',{})}))
                            if adapter.structured_inventory:
                                self.db.event(app_id,'FIELD_FILLED',json.dumps({'field_id':q.key,'semantic_key':q.semantic_key,
                                                                             'source':answer.source,'confidence':answer.confidence}))
                    elif not q.required and (not q.value or q.kind == "checkbox" and q.value == "No") and not (
                            policy == 'REQUIRE_USER' or adapter.requires_demographic_answer and q.semantic_key.startswith('demographics.')):
                        self.db.execute("UPDATE questions SET status='SKIPPED' WHERE id=?", (row["id"],))
                    else:
                        reason = "No sufficiently confident verified answer; existing prefilled values also require verification"
                        self.db.execute("UPDATE questions SET status='PENDING',reason=? WHERE id=?", (reason, row["id"]))
                        self.request(app, q, reason)
                        missing = True
                if missing:
                    self.hold(app_id, State.NEEDS_INPUT, "Application has unanswered questions")
                    await self.handoff.request(app_id, page, "Application has unanswered questions", ErrorCategory.INPUT_REQUIRED)
                    return True
                if await self.security_gate(app_id, page):
                    return True
                if not await self.check_uploads(app_id, page, adapter.uploads):
                    if not await self.security_gate(app_id, page):
                        await self.handoff.request(app_id, page, 'ATS upload readiness failed; see upload lifecycle evidence. No Submit click was attempted.', self.browser.observation(page).get('upload_result', {}).get('category', ErrorCategory.UPLOAD_PENDING))
                    return True
                self.db.event(app_id, 'ATS_UPLOAD_READY', json.dumps({
                    'tracked_uploads_complete':True, 'controls_not_busy':True,
                    'resume_selected':bool(self.db.application(app_id)['resume_sha256'])}))
                if adapter.structured_inventory:
                    self.adapter_event(app_id,'UPLOAD_READY',{'ready':True})
                current_questions = await adapter.get_questions(app)
                if tuple((q.key, q.label, tuple(q.options)) for q in current_questions) != signature:
                    # Conditional fields must be answered and verified before submitting.
                    continue
                readiness, issues = await self.classifier.pre_submit(page, adapter, self.browser.observation(page))
                if readiness == PreSubmitState.INTERACTIVE_SECURITY_STEP:
                    await self.security_gate(app_id, page)
                    return True
                if readiness == PreSubmitState.VALIDATION_ERROR:
                    await self.handoff.request(app_id, page, "Validation failed: " + "; ".join(issues), ErrorCategory.FORM_VALIDATION_ERROR)
                    return True
                kind, button = await adapter.action()
                if not kind:
                    raise UnsupportedForm("No unique supported next/submit action was found")
                if kind == "next":
                    if self.interrupted(app_id):
                        return True
                    if adapter.structured_inventory:
                        self.adapter_event(app_id, 'STEP_FILLED', {'step':step+1})
                    await adapter.advance()
                    if await self.security_gate(app_id, page):
                        return True
                    continue
                if not questions:
                    raise UnsupportedForm("Refusing to submit a form without inspectable fields")
                if adapter.requires_verified_resume:
                    fresh = eligibility(self.db.application(app_id), self.config.profile_snapshot().facts)
                    if fresh.eligible is not True:
                        raise UnsupportedForm('ELIGIBILITY_UNVERIFIED: fresh eligibility must pass before final Submit')
                    if not adapter.uploads or not self.browser.observation(page).get('upload_result', {}).get('ready'):
                        raise UnsupportedForm('FILE_UPLOAD: verified resume attachment required')
                if self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
                    self.hold(app_id, State.NEEDS_INPUT, "Unresolved questions remain from an earlier form step")
                    await self.handoff.request(app_id, page, "Unresolved questions remain from an earlier form step", ErrorCategory.INPUT_REQUIRED)
                    return True
                if self.fill_only:
                    if self.db.setting("auto_submit") is not False:
                        raise RuntimeError("Fill-only setting changed; refusing final action")
                    fresh = eligibility(self.db.application(app_id), self.config.profile_snapshot().facts)
                    if fresh.eligible is not True:
                        raise UnsupportedForm("ELIGIBILITY_UNVERIFIED: manual-ready requires eligibility PASS")
                    if not await self.check_uploads(app_id, page, adapter.uploads):
                        raise UnsupportedForm("UPLOAD_NOT_READY: manual review requires completed uploads")
                    if self.db.submission_conflict(app_id):
                        raise UnsupportedForm("Existing submission history prevents manual-ready classification")
                    with self.db.transaction():
                        self.db.lifecycle.record_ready(app_id, live=True, url=safe_url(page.url))
                        self.db.event(app_id, "READY_FOR_MANUAL_SUBMIT", json.dumps({
                            "eligibility":"PASS", "upload_ready":True, "final_control_identified":True,
                            "url":safe_url(page.url), "final_submit_clicks":0, "submission_requests":0}))
                    self.retained_pages[app_id] = page
                    return True
                if not self.db.setting("auto_submit", self.config["application"]["auto_submit"]):
                    self.db.lifecycle.record_ready(app_id)
                    return True
                folder = archive_application(self.config, self.db, app_id)
                probe = SubmissionProbe(self.db, app_id, page, button)
                await probe.prepare()
                self.db.update_security(app_id, application_state="READY_TO_SUBMIT", url_before_submit=safe_url(page.url))
                if adapter.structured_inventory:
                    self.adapter_event(app_id,'READY_TO_SUBMIT',{'eligibility':'PASS','validation':'PASS','upload_ready':True})
                folder = archive_application(self.config, self.db, app_id)
                pre_name = 'pre-submit-' + probe.key
                await self.browser.screenshot(page, folder / "screenshots", pre_name)
                probe.data['pre_submit_screenshot'] = pre_name + '.png'
                probe.save()
                if await self.security_gate(app_id, page):
                    return True
                # Re-check controls after the final await, immediately before durable submission intent.
                if self.interrupted(app_id) or not self.db.setting("auto_submit", self.config["application"]["auto_submit"]):
                    return True
                if self.daily_count() >= self.config["processing"]["max_applications_per_day"]:
                    self.hold(app_id, State.READY, "Daily ceiling reached before submission")
                    return True
                current = self.db.application(app_id)
                if current["resume_sha256"] and (not self.config.resume.exists() or hashlib.sha256(self.config.resume.read_bytes()).hexdigest() != current["resume_sha256"]):
                    raise UnsupportedForm("Resume changed after upload")
                # Inspect closure again after filling/screenshots, before any submit intent.
                state, evidence = await page_condition(page)
                if state:
                    if state in {State.MANUAL_REVIEW, State.AUTH_REQUIRED}:
                        await self.handoff.request(app_id, page, evidence)
                    else:
                        self.hold(app_id, state, evidence)
                    return True
                if self.interrupted(app_id) or not self.db.setting("auto_submit", self.config["application"]["auto_submit"]):
                    return True
                if not self.db.guard_listing(app_id):
                    return True
                readiness, issues = await self.classifier.pre_submit(page, adapter, self.browser.observation(page))
                if readiness == PreSubmitState.INTERACTIVE_SECURITY_STEP:
                    await self.security_gate(app_id, page)
                    return True
                if readiness == PreSubmitState.VALIDATION_ERROR:
                    await self.handoff.request(app_id, page, "Final validation failed: " + "; ".join(issues), ErrorCategory.FORM_VALIDATION_ERROR)
                    return True
                probe.data['validation'] = {
                    'all_pending_resolved':not bool(self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))),
                    'resume_verified':bool(self.db.application(app_id)['resume_sha256']),
                    'pre_submit_state':str(readiness), 'issues':issues,
                    'questions':[{'id':q['id'],'label':q['raw_question'],'required':bool(q['required']),
                                  'status':q['status'],'source':q['answer_source']}
                                 for q in self.db.rows('SELECT * FROM questions WHERE application_id=?',(app_id,))]}
                probe.record('PRE_SUBMIT_VALIDATION',probe.data['validation'])
                if not await self.check_uploads(app_id, page, adapter.uploads):
                    await self.handoff.request(app_id, page, 'ATS upload is not ready before Submit; see upload lifecycle evidence.', self.browser.observation(page).get('upload_result', {}).get('category', ErrorCategory.UPLOAD_PENDING))
                    return True
                await probe.arm()
                self.db.lifecycle.record_submission_intent(app_id)
                probe.intent()
                self.browser.reset_observation(page)
                log.info("[SUBMIT] Submission initiated application=%s", app_id)
                await probe.physical_click()
                immediate = await self.inspect_security(app_id, page)
                # Persist affirmative employer evidence before optional screenshot I/O.
                # A disappearing frame can invalidate a screenshot mask after success.
                if immediate.confirmation:
                    probe.confirmed(immediate.confirmation)
                    await self.confirm(app_id, page, immediate.confirmation)
                if immediate.security.state not in VERIFICATION and not self.browser.observation(page).get('dialog_open'):
                    folder = archive_application(self.config, self.db, app_id)
                    post_name = 'post-click-' + probe.key
                    await self.browser.screenshot(page, folder / 'screenshots', post_name)
                    probe.data['post_click_screenshot'] = post_name + '.png'
                    probe.save()
                if immediate.security.blocking:
                    await self.handoff.request(app_id, page, immediate.security.message, immediate.security.category)
                    return True
                await self.post_submit(app_id, page, probe=probe)
                return True
            raise UnsupportedForm("Application exceeded configured page limit")
        except NavigationError as exc:
            if page is None or page.is_closed() or not await self.security_gate(app['id'], page):
                await self.handoff.request(app["id"], page, str(exc), exc.category)
        except UnsupportedForm as exc:
            await self.handoff.request(app["id"], page, str(exc), getattr(exc, 'category', 'ADAPTER_CAPABILITY_FAILURE'))
        except Exception as exc:
            # Exception messages can contain form values, tokens or sensitive URLs.
            reason = f"{type(exc).__name__} during {self.db.application(app['id'])['stage']}"
            current = self.db.application(app["id"])
            if not current['submit_intent_at'] and isinstance(exc, TargetUnstableError):
                await self.handoff.request(app['id'], page, str(exc), 'PRE_SUBMIT_TARGET_UNSTABLE')
                return True
            if current["submit_intent_at"]:
                import traceback
                self.db.event(app['id'], 'POST_SUBMIT_EXCEPTION', json.dumps({
                    'type': type(exc).__name__,
                    'frames': [{'file': f.filename.replace('\\', '/').rsplit('/', 1)[-1],
                                'line': f.lineno, 'function': f.name}
                               for f in traceback.extract_tb(exc.__traceback__)],
                    'confirmation_persisted': bool(current['submission_confirmation_seen'])}))
            if current["status"] != State.SUBMITTED:
                blocked = False
                if page and not page.is_closed():
                    try:
                        blocked = await self.security_gate(app["id"], page)
                    except Exception:
                        pass
                if not blocked:
                    if page and self.browser.observation(page).get('execution_blocked'):
                        await self.handoff.request(app['id'], page,
                            'Execution environment denied network access (ERR_NETWORK_ACCESS_DENIED)',
                            ErrorCategory.EXECUTION_APPROVAL_BLOCKED,
                            unknown=bool(current['submit_intent_at']))
                    elif current["submit_intent_at"]:
                        await self.handoff.request(app["id"], page, reason, unknown=True)
                    elif isinstance(exc, (TimeoutError, PlaywrightTimeoutError, OSError, SiteError)) or (page and self.browser.observation(page).get("network_error")):
                        category = ErrorCategory.SITE_ERROR if isinstance(exc, SiteError) else ErrorCategory.NETWORK_ERROR
                        self.db.fail(app["id"], reason, self.config["processing"]["max_retries"], category)
                    else:
                        await self.handoff.request(app["id"], page, reason)
                if self.db.application(app["id"])["status"] == "FAILED":
                    self.db.notify(f"failure:{app['id']}:{current['attempts']}", {"application_id": app["id"], "message": "APPLICATION FAILED: " + reason})
            log.warning("Application %s: %s", app["id"], reason)
        finally:
            try:
                if probe:
                    try:
                        await probe.finish()
                    except Exception as exc:
                        self.db.event(app['id'], 'submit_telemetry_unavailable', type(exc).__name__)
                current = self.db.application(app["id"])
                if (self.db.setting('controlled_application_id') == app['id'] and page
                        and not page.is_closed() and app['id'] not in self.handoff.pages
                        and (current['status'] not in {'SUBMITTED', 'CLOSED', 'INELIGIBLE', 'ALREADY_APPLIED'}
                             or current['manual_action_required'])):
                    await self.handoff.request(app['id'], page,
                        'Controlled run stopped; preserve current page for inspection',
                        current['error_category'] or 'SUBMISSION_UNKNOWN',
                        unknown=bool(current['submit_intent_at']))
                    current = self.db.application(app['id'])
                if current["status"] in {"CHECKING", "APPLYING"}:
                    self.db.fail(app["id"], "Processing interrupted before submission", self.config["processing"]["max_retries"], ErrorCategory.PROCESS_INTERRUPTED)
                folder = archive_application(self.config, self.db, app["id"])
                if self.fill_only and page and not page.is_closed():
                    from .fill_batch import checkpoint
                    # The checkpoint must reach disk before releasing this page or claiming another job.
                    await checkpoint(self, app['id'], page)
                    if self.db.application(app['id'])['application_state'] != 'READY_FOR_MANUAL_SUBMIT':
                        self.retained_pages.pop(app['id'], None)
                        self.handoff.pages.pop(app['id'], None)
                        self.handoff.sessions.pop(app['id'], None)
                        self.db.set_setting(f"manual_session:{app['id']}", None)
                        self.db.update_security(app['id'], session_preserved=0, manual_resume_allowed=0)
                if page and app["id"] not in self.handoff.pages and app['id'] not in self.retained_pages:
                    try:
                        if current["status"] in {"NEEDS_INPUT", "MANUAL_REVIEW", "AUTH_REQUIRED", "FAILED"}:
                            await self.browser.screenshot(page, folder / "screenshots", "attention")
                    except Exception:
                        pass
                    await self.browser.release_page(page)
                self.next_application = time.monotonic() + self.config["application"]["delay_seconds"]
                log.info("Application %s: %s", app["id"], self.db.application(app["id"])["status"])
            finally:
                # History, checkpoint, telemetry and diagnostics failures must not
                # bypass resource cleanup. Explicit handoffs retain their owner.
                if page and not page.is_closed():
                    preserve = app['id'] in self.handoff.pages or app['id'] in self.retained_pages
                    await asyncio.shield(self.browser.release_page(page, preserve=preserve))
        return True

    async def check_uploads(self, app_id, page, controls):
        ready = await self.browser.uploads_ready(page, controls)
        tracker = getattr(page, '_autoapply_uploads', None)
        if tracker:
            with self.db.transaction():
                for event in tracker.events:
                    self.db.event(app_id, event['kind'], json.dumps(event))
                self.db.set_setting(f'upload_result:{app_id}', tracker.result)
                self.db.event(app_id, 'UPLOAD_READINESS', json.dumps(tracker.result))
            tracker.events.clear()
        return ready

    async def resume_manual(self, app_id, outcome=None, *, inspect_only=False, strict_session=False, session_token=None):
        self.ensure_fill_policy()
        if self.fill_only:
            from .fill_batch import MAX_ACTIVE_APPLICATION_TABS
            await self.browser.enforce_application_limit(MAX_ACTIVE_APPLICATION_TABS)
        if self.db.automation_retired(app_id):
            raise ValueError("User-reported submission permanently excludes this application from automation")
        async with self.processing_lock:
            target = self.db.setting("controlled_application_id")
            if target is not None and (app_id != target or not session_token or session_token != self.handoff.sessions.get(app_id)):
                raise ValueError("Controlled resume requires the correct application and live session token")
            if self.db.setting(f'inspect_only_once:{app_id}', False):
                # A controlled worker can register its existing page without an
                # accidental fill if a security restriction disappears meanwhile.
                inspect_only = True
                self.db.set_setting(f'inspect_only_once:{app_id}', False)
            page = self.handoff.pages.get(app_id)
            if not inspect_only:
                self.control.resolve_known_pending(app_id)
            app = self.db.application(app_id)
            previous_security = app["security_state"]
            if page is None or page.is_closed():
                self.db.update_security(app_id, session_preserved=0)
                if (not strict_session and not inspect_only and app["error_category"] in {"INPUT_REQUIRED", "UPLOAD_PENDING", "UPLOAD_FAILED", "UPLOAD_NOT_STARTED", "UPLOAD_STALLED"} and app["manual_resume_allowed"]
                        and not app["submit_intent_at"] and not app["submission_confirmation_seen"]
                        and app["security_state"] in {"NONE", "PASSIVE_PROTECTION_DETECTED"}
                        and not self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))):
                    if self.db.setting("paused", False) or self.stop_event.is_set():
                        return False
                    if self.db.submission_conflict(app_id) or not self.db.guard_listing(app_id):
                        return False
                    self.db.lifecycle.resume(app_id, reconstruct=True)
                    self.handoff.release(app_id)
                    return await self._process_one(app_id)
                if app["submission_confirmation_seen"] and outcome in {"PASSED", "FAILED", "SKIPPED"}:
                    self.db.update_security(app_id, verification_state=outcome)
                    self.db.event(app_id, "verification_user_report", outcome)
                    self.handoff.release(app_id)
                    archive_application(self.config, self.db, app_id)
                    self.db.notify(f"manual-result:{app_id}:{now()}", {"message": f"Application #{app_id} remains SUBMITTED. User reported verification {outcome}; live session unavailable."})
                    return True
                if app["submission_confirmation_seen"] and not app["manual_action_required"]:
                    self.handoff.release(app_id)
                    return True
                self.handoff.notify(app_id, "The preserved tab is unavailable; check employer history. No automatic restart or resubmission was performed.")
                return False
            self.browser.reset_observation(page)
            result = await self.inspect_security(app_id, page)
            if result.confirmation:
                await self.confirm(app_id, page, result.confirmation)
            app = self.db.application(app_id)
            if outcome in {"PASSED", "FAILED", "SKIPPED"}:
                self.db.update_security(app_id, verification_state=outcome)
                self.db.event(app_id, "verification_user_report", outcome)
            elif re.search(r"verification (?:was |has been )?(?:successful|complete|passed)|identity verified", result.snapshot["text"], re.I):
                self.db.update_security(app_id, verification_state="PASSED")
            elif re.search(r"verification (?:was |has )?failed|unable to verify (?:your )?identity", result.snapshot["text"], re.I):
                self.db.update_security(app_id, verification_state="FAILED")
            elif re.search(r"verification (?:was )?skipped", result.snapshot["text"], re.I):
                self.db.update_security(app_id, verification_state="SKIPPED")
            app = self.db.application(app_id)
            if result.security.blocking and not (app["submission_confirmation_seen"] and result.security.state in VERIFICATION and app["verification_state"] in {"PASSED", "FAILED", "SKIPPED"}):
                await self.handoff.request(app_id, page, result.security.message, result.security.category)
                return False
            if app["submission_confirmation_seen"]:
                if app["verification_state"] == "PENDING":
                    self.db.update_security(app_id, verification_state="UNKNOWN")
                # Keep the tab open for inspection, but it no longer blocks the worker.
                self.handoff.release(app_id)
                archive_application(self.config, self.db, app_id)
                self.db.notify(f"manual-result:{app_id}:{now()}", {"message": f"Application #{app_id} remains SUBMITTED. Verification: {self.db.application(app_id)['verification_state']}. No repeat submission."})
                log.info("[RESUME] Manual step completed application=%s; application confirmed", app_id)
                return True
            if app["submit_intent_at"]:
                await self.handoff.request(app_id, page, "Submission still unconfirmed after manual intervention. No repeat click; check employer history.", unknown=True)
                return False
            if inspect_only:
                return False
            if self.db.setting("paused", False) or self.stop_event.is_set():
                return False
            if self.daily_count() >= self.config["processing"]["max_applications_per_day"]:
                return False
            if not app["manual_resume_allowed"]:
                await self.handoff.request(app_id, page, "Security rejection requires manual completion. The banner disappeared, but automatic submission remains disabled.")
                return False
            if self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
                self.handoff.notify(app_id, "Pending factual questions must be answered before resuming.")
                return False
            await get_cursor(page).exit_manual_mode(Point(1, 1))
            # Reinspect/fill this exact page without navigation, resetting the form, or claiming another job.
            self.db.lifecycle.resume(app_id, verification="PASSED" if previous_security == "INTERACTIVE_CHALLENGE" else app["verification_state"])
            self.handoff.release(app_id)
            log.info("[RESUME] Revalidating preserved form application=%s", app_id)
            await self._process_one(app_id, preserved_page=page)
            return True

    async def service_manual_requests(self):
        ledger = ManualCommands(self.db)
        for request in self.db.rows("SELECT * FROM manual_requests ORDER BY created_at"):
            command = None
            app_id = request['application_id']
            try:
                command = ledger.claim(request)
                if command is None:
                    continue
                app = self.db.application(app_id)
                target = self.db.setting('controlled_application_id')
                reconstruct = command.parameters.get('reconstruct', False)
                token = self.handoff.sessions.get(app_id)
                page = self.handoff.pages.get(app_id)
                if self.db.automation_retired(app_id) or (target is not None and app_id != target):
                    raise ValueError('Retired application or controlled target mismatch')
                if reconstruct:
                    if target is not None or app['error_category'] != 'INPUT_REQUIRED' or app['submit_intent_at']:
                        raise ValueError('Reconstruction is not authorized')
                elif (not token or command.session_token != token or page is None or page.is_closed()):
                    raise ValueError('Stale or unavailable live session')
                if command.action not in {'inspect', 'inspect-only', 'PASSED', 'FAILED', 'SKIPPED'}:
                    raise ValueError('Unsupported manual operation')
                if token:
                    self.db.set_setting(f'manual_session:{app_id}', {'token': token, 'state': 'MANUAL_INTERVENTION_COMPLETE', 'busy': True})
                result = await self.resume_manual(app_id,
                    command.action if command.action in {'PASSED','FAILED','SKIPPED'} else None,
                    inspect_only=command.action == 'inspect-only', strict_session=not reconstruct,
                    session_token=command.session_token)
                ledger.finish(command, 'ACKNOWLEDGED', 'Completed' if result else 'Inspected; application remains held')
            except Exception as exc:
                if command:
                    ledger.finish(command, 'FAILED', type(exc).__name__ + ': ' + str(exc))
                else:
                    # Malformed legacy requests remain visible; no destructive action ran.
                    self.db.event(app_id, 'manual_inspection_error', type(exc).__name__)
            finally:
                token = self.handoff.sessions.get(app_id)
                if token:
                    self.db.set_setting(f'manual_session:{app_id}', {'token': token, 'state': 'WAITING_FOR_MANUAL_INTERVENTION', 'busy': False})

    async def wait_for_manual(self):
        # CLI work-once remains alive so Playwright does not dispose the populated form.
        while self.handoff.pages and not self.stop_event.is_set():
            await self.service_manual_requests()
            await asyncio.sleep(.5)

    async def close(self):
        # Called on explicit worker shutdown; a manual hold itself never calls close.
        try:
            for app_id in set(self.handoff.pages) | set(self.retained_pages):
                self.db.update_security(app_id, session_preserved=0)
                self.db.set_setting(f'manual_session:{app_id}', None)
                if self.db.application(app_id)['application_state'] == 'READY_FOR_MANUAL_SUBMIT':
                    self.db.update_security(app_id, application_state='READY_TO_SUBMIT')
                self.db.event(app_id, "manual_session_ended", "Worker/browser shutdown; automatic retry remains disabled")
                archive_application(self.config, self.db, app_id)
        finally:
            await self.browser.close()

    async def run(self):
        self.db.recover()
        bot = bot_task = None
        discord_available = not self.config["discord"]["enabled"]
        if self.config["discord"]["enabled"]:
            import os
            from .discord_bot import DiscordBot
            bot = DiscordBot(self.control)
            bot_task = asyncio.create_task(bot.start(os.environ["DISCORD_BOT_TOKEN"]))
        try:
            if bot:
                await bot.wait_for_delivery(bot_task)
                discord_available = True
            while not self.stop_event.is_set():
                if bot_task and bot_task.done():
                    if not self.handoff.pages:
                        await bot_task  # Invalid Discord credentials must be visible to the operator.
                        raise RuntimeError("Discord connection stopped")
                    try:
                        await bot_task
                    except Exception as exc:
                        self.db.event(None, "discord_disconnected", type(exc).__name__)
                    bot_task = None
                    discord_available = False
                    self.db.set_setting("paused", True)
                    log.error("[MANUAL] Discord stopped; browser retained. Use local resume-manual, then restart the worker to restore Discord.")
                await self.service_manual_requests()
                if discord_available and not self.db.setting("paused", False) and not self.handoff.pages:
                    try:
                        if self.db.setting("controlled_application_id") is None:
                            await self.scan()
                            if time.monotonic() >= self.next_application:
                                await self.process_one()
                    except Exception as exc:
                        log.warning("Worker cycle failed: %s", type(exc).__name__)
                        self.db.event(None, "worker_error", type(exc).__name__)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=2)
                except TimeoutError:
                    pass
        finally:
            if bot:
                await bot.close()
            if bot_task:
                bot_task.cancel()
                await asyncio.gather(bot_task, return_exceptions=True)
            await self.close()
