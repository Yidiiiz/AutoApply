"""Own live manual tabs; durable state never claims a session survived a restart."""
import logging
import asyncio
import json
import uuid

from .archive import archive_application
from .cursor import get_cursor
from .models import State, now
from .security import safe_text, safe_url

log = logging.getLogger("autoapply")
VERIFICATION = {"EMAIL_VERIFICATION", "PHONE_VERIFICATION", "IDENTITY_VERIFICATION"}


class ManualHandoffManager:
    def __init__(self, config, db, browser):
        self.config, self.db, self.browser = config, db, browser
        self.pages = {}
        self.sessions = {}

    async def request(self, app_id, page, reason, category="SUBMISSION_UNKNOWN", *, unknown=False):
        reason = safe_text(reason)
        app = self.db.application(app_id)
        confirmed = bool(app["submission_confirmation_seen"])
        preserved = page is not None and not page.is_closed()
        if preserved:
            self.pages[app_id] = page
            token = uuid.uuid4().hex
            self.sessions[app_id] = token
            self.db.set_setting(f"manual_session:{app_id}", {"token": token, "state": "WAITING_FOR_MANUAL_INTERVENTION", "busy": False})
        fields = dict(manual_action_required=1, manual_action_reason=safe_text(reason), retry_allowed=0,
                      session_preserved=int(preserved), error_category=category,
                      manual_resume_allowed=int(not app["submit_intent_at"] and
                          (app["security_state"] in VERIFICATION | {"INTERACTIVE_CHALLENGE"} or category in {"PRE_SUBMIT_TARGET_UNSTABLE", "PRE_SUBMIT_DROPDOWN_STATE_FAILURE", "FORM_VALIDATION_ERROR", "AUTH_REQUIRED", "INPUT_REQUIRED", "UPLOAD_PENDING", "UPLOAD_FAILED", "UPLOAD_NOT_STARTED", "UPLOAD_STALLED"})))
        if not confirmed:
            fields["application_state"] = "UNKNOWN" if unknown else "MANUAL_REQUIRED"
        if (app["security_state"] in VERIFICATION or app["security_state"] == "INTERACTIVE_CHALLENGE") and app["verification_state"] not in {"FAILED", "SKIPPED", "PASSED"}:
            fields["verification_state"] = "PENDING"
        fields.pop("application_state", None)
        fields.pop("manual_action_reason", None)
        fields.pop("error_category", None)
        self.db.lifecycle.record_hold(app_id, reason, category, unknown=unknown, **fields)
        if preserved:
            try:
                await get_cursor(page).enter_manual_mode()
            except Exception as exc:
                # The cursor is already stopped. Persist the hold even if the
                # browser could not acknowledge input cleanup.
                self.db.event(app_id, "cursor_cleanup_unavailable", type(exc).__name__)
        await self.diagnostics(app_id, page)
        self.notify(app_id, reason)
        log.info("[MANUAL] User intervention requested application=%s category=%s", app_id, category)
        log.info("[MANUAL] Browser session preserved=%s application=%s", preserved, app_id)
        log.info("[SECURITY] Automatic retry disabled application=%s", app_id)

    async def diagnostics(self, app_id, page):
        app = self.db.application(app_id)
        folder = archive_application(self.config, self.db, app_id)
        fields: dict[str, str | None] = {"diagnostics_at": now()}
        if page is not None and not page.is_closed():
            fields.update(current_url=safe_url(page.url))
            sensitive = app["security_state"] in VERIFICATION or self.browser.observation(page).get("dialog_open")
            if sensitive:
                fields["screenshot_path"] = None
            try:
                fields["page_title"] = "[protected page]" if sensitive else safe_text(await asyncio.wait_for(page.title(), timeout=2))
                # Never capture identity documents, faces, codes, or verification pages.
                if not sensitive:
                    await self.browser.screenshot(page, folder / "screenshots", "security")
                    fields["screenshot_path"] = str(folder / "screenshots/security.png")
            except Exception as exc:
                self.db.event(app_id, "diagnostics_unavailable", type(exc).__name__)
        self.db.update_security(app_id, **fields)
        app = self.db.application(app_id)
        failures = self.browser.observation(page).get('http_failures', [])
        if failures:
            self.db.event(app_id, 'http_failure_provenance', json.dumps(failures))
        self.db.event(app_id, "security_diagnostics", json.dumps({key: app[key] for key in
            ["job_id", "ats_type", "security_state", "security_provider", "last_security_message", "last_http_status",
             "current_url", "previous_url", "page_title", "diagnostics_at", "screenshot_path", "submission_confirmation_seen"]}))
        archive_application(self.config, self.db, app_id)

    def notify(self, app_id, reason):
        app = self.db.application(app_id)
        if app["error_category"] in {"EXTERNAL_EXECUTION_APPROVAL_REQUIRED", "EXECUTION_APPROVAL_BLOCKED"}:
            # The execution environment needs action; no applicant answer is missing.
            message = (f"EXECUTION APPROVAL - {app['company']} #{app_id}\n{app['title']}\n"
                       f"{app['error_category']}: {safe_text(reason)}\n"
                       "Existing applicant authorization is retained. No new applicant answer is requested. "
                       "Automatic retries are disabled.")
            self.db.notify(f"execution:{app_id}:{app['error_category']}",
                           {"message": message, "application_id": app_id, "kind": "execution_approval"})
            return
        if app["error_category"] == "INPUT_REQUIRED" or app['status'] == 'NEEDS_INPUT':
            questions = self.db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING' ORDER BY id", (app_id,))
            if not questions:
                return
            lines = [f"INPUT NEEDED - {app['company']} #{app_id}", app['title'],
                     "The application is paused. Answers and progress are saved. I will wait until you respond."]
            for q in questions:
                lines.append(f"\n#{q['id']}: {q['raw_question']}")
                options = json.loads(q['options'])
                if options:
                    lines.append("Options: " + " | ".join(options))
                lines.append(f"Reply: !answer {q['id']} <answer>")
                draft = self.db.setting(f"draft:{q['id']}")
                if draft:
                    lines.append(f"Draft for review: {draft}\nAccept with !accept {q['id']}, or provide your own answer.")
            key = "input:" + str(app_id) + ":" + ",".join(str(q['id']) + "@" + q['updated_at'] for q in questions)
            self.db.notify(key, {"message":"\n".join(lines), "application_id":app_id, "kind":"input", "question_ids":[q['id'] for q in questions]})
            return
        category = app["error_category"]
        heading = "APPLICATION REJECTED" if "REJECTION" in category else "IDENTITY VERIFICATION" if category == "IDENTITY_VERIFICATION" else "SECURITY CHECK" if app['security_state'] not in {"NONE", "PASSIVE_PROTECTION_DETECTED"} else "APPLICATION FAILED"
        if category == 'SUBMISSION_UNKNOWN' and app['security_state'] in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}:
            heading = 'SUBMISSION UNCONFIRMED'
        context = "APPLICATION ALREADY SUBMITTED; POST-SUBMISSION VERIFICATION REQUIRED" if app['submission_confirmation_seen'] and app['security_state'] in VERIFICATION else "APPLICATION ALREADY SUBMITTED" if app['submission_confirmation_seen'] else "APPLICATION NOT YET CONFIRMED"
        preserved = "Browser session preserved." if app['session_preserved'] else "Saved application state retained."
        message = f"{heading} - {app['company']} #{app_id}\n{app['title']}\n{context}. {preserved}\n{safe_text(reason)}\nAutomatic retries are disabled. Use !status {app_id} to inspect."
        self.db.notify(f"manual:{app_id}:{category}:{app['verification_state']}", {"message":message, "application_id":app_id, "kind":"manual"})

    def release(self, app_id):
        page = self.pages.pop(app_id, None)
        self.sessions.pop(app_id, None)
        self.db.lifecycle.release_hold(app_id)
        return page
