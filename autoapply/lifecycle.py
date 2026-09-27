"""Authoritative application mutations; legacy database methods delegate here.

No operation holds a transaction across browser I/O. SQL status and archive state
are compatibility projections; security, verification and listing are independent.
"""
import json
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from .models import FINAL, State, now
from .retry import Delivery, ErrorCategory, Failure, RetryPolicy

STATE_MAP = {
    'DISCOVERED': 'DISCOVERED', 'QUEUED': 'DISCOVERED', 'RETRY': 'DISCOVERED',
    'CHECKING': 'OPENED', 'APPLYING': 'FILLING', 'READY': 'READY_TO_SUBMIT',
    'SUBMITTING': 'SUBMITTING', 'SUBMITTED': 'SUBMITTED', 'ALREADY_APPLIED': 'ALREADY_APPLIED',
    'MANUAL_REVIEW': 'MANUAL_REQUIRED', 'NEEDS_INPUT': 'MANUAL_REQUIRED', 'AUTH_REQUIRED': 'MANUAL_REQUIRED',
    **{s: s for s in ('CLOSED', 'INVALID', 'INELIGIBLE', 'DUPLICATE', 'FAILED')},
}
SAFE_SECURITY = {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
HOLDS = {'NEEDS_INPUT', 'AUTH_REQUIRED', 'MANUAL_REVIEW'}
RUNNABLE = {'QUEUED', 'RETRY', 'CHECKING', 'APPLYING', 'READY', 'SUBMITTING'}
# Terminal outcomes and holds may be recorded from any unfinished state.
ALLOWED = {
    'DISCOVERED': {'QUEUED'}, 'QUEUED': {'CHECKING'},
    'CHECKING': {'APPLYING', 'RETRY', 'READY'},
    'APPLYING': {'NEEDS_INPUT', 'READY', 'SUBMITTING', 'RETRY'},
    'READY': {'SUBMITTING', 'APPLYING', 'RETRY'},
    'SUBMITTING': {'SUBMITTED'}, 'FAILED': {'RETRY'}, 'RETRY': {'CHECKING'},
    'NEEDS_INPUT': {'RETRY', 'APPLYING'}, 'AUTH_REQUIRED': {'APPLYING'},
    'MANUAL_REVIEW': {'APPLYING', 'RETRY', 'SUBMITTED'},
}


class Lifecycle:
    def __init__(self, db):
        self.db = db
        self.fill_only = False

    def atomic(self):
        return nullcontext() if self.db.conn.in_transaction else self.db.transaction()

    def transition(self, app_id, status, reason='', *, legacy=False, reconciled=False, **fields):
        from .database import SECURITY_COLUMNS
        status = str(State(status))
        with self.atomic():
            app = self.db.application(app_id)
            if app['status'] in FINAL and status != app['status']:
                raise ValueError('Cannot automatically reopen a final application')
            if self.db.automation_retired(app_id) and status in RUNNABLE:
                raise ValueError('User-reported submission permanently excludes this application from automation')
            if app['submit_intent_at'] and status in RUNNABLE and not reconciled:
                raise ValueError('Submission intent requires evidence-backed reconciliation')
            if self.fill_only and (status == 'SUBMITTING' or fields.get('submit_intent_at')):
                raise ValueError('Fill-only forbids submission intent')
            if not legacy and status != app['status'] and status not in (ALLOWED.get(app['status'], set()) | HOLDS | set(FINAL) | {'FAILED'}):
                raise ValueError(f"Forbidden lifecycle transition: {app['status']} -> {status}")
            if status == 'SUBMITTED' and not str(fields.get('confirmation_text') or '').strip():
                raise ValueError('Submission requires positive confirmation evidence')
            allowed = {'stage', 'started_at', 'submitted_at', 'resume_used', 'resume_sha256',
                       'confirmation_text', 'confirmation_url', 'retry_at', 'submit_intent_at',
                       'eligibility_override', 'attempts', 'attempt_started', 'max_retries', 'delivery_state'} | SECURITY_COLUMNS.keys()
            if not fields.keys() <= allowed:
                raise ValueError('Invalid application update')
            if 'submit_intent_at' in fields and app['submit_intent_at'] and not reconciled:
                raise ValueError('Submission intent cannot be erased or replaced')
            projection = fields.pop('application_state', STATE_MAP[status])
            if app['submission_confirmation_seen'] and projection != 'SUBMITTED' and status != 'ALREADY_APPLIED':
                raise ValueError('Confirmed submission cannot be erased by verification outcome')
            if status in FINAL or status in HOLDS | {'SUBMITTING'}:
                fields.update(retry_allowed=0, retry_at=None)
            if status in HOLDS:
                fields.setdefault('manual_action_required', 1)
                fields.setdefault('manual_action_reason', reason)
            if status in {'SUBMITTED', 'ALREADY_APPLIED'} and fields.get('confirmation_text'):
                fields.update(submission_confirmation_seen=1, submission_confirmation_reason=fields['confirmation_text'], retry_allowed=0)
            fields.update(status=status, application_state=projection, updated_at=now(), failure_reason=reason)
            self.db.execute('UPDATE applications SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), app_id))
            self.db.execute('UPDATE jobs SET status=?,reason=? WHERE id=?', (status, reason, app['job_id']))
            self.db.event(app_id, status, reason)

    def metadata(self, app_id, **fields):
        if not fields or not fields.keys() <= {'stage', 'resume_used', 'resume_sha256', 'eligibility_override'}:
            raise ValueError('Invalid lifecycle metadata')
        with self.atomic():
            self.db.execute('UPDATE applications SET ' + ','.join(f'{k}=?' for k in fields) + ',updated_at=? WHERE id=?', (*fields.values(), now(), app_id))

    def begin_attempt(self, app_id, max_retries):
        """Count at the browser-work boundary, before opening/navigating a page."""
        with self.atomic():
            app = self.db.application(app_id)
            if app['attempt_started']:
                return
            if app['attempts'] >= max_retries + 1:
                raise ValueError('Application attempt budget exhausted')
            self.db.execute('UPDATE applications SET attempts=attempts+1,attempt_started=1,max_retries=?,delivery_state=? WHERE id=?',
                            (max_retries, Delivery.NOT_STARTED, app_id))

    def delivery(self, app_id, value):
        with self.atomic():
            self.db.execute('UPDATE applications SET delivery_state=? WHERE id=?', (str(Delivery(value)), app_id))

    def record_hold(self, app_id, reason, category, *, unknown=False, **fields):
        with self.atomic():
            app = self.db.application(app_id)
            status = app['status'] if app['submission_confirmation_seen'] else 'MANUAL_REVIEW'
            fields.update(manual_action_required=1, manual_action_reason=reason, retry_allowed=0,
                          retry_at=None, error_category=str(category))
            if unknown:
                fields['manual_resume_allowed'] = 0
            fields['application_state'] = 'SUBMITTED' if app['submission_confirmation_seen'] else 'UNKNOWN' if unknown else 'MANUAL_REQUIRED'
            if app['submission_confirmation_seen']:
                fields['confirmation_text'] = app['confirmation_text'] or app['submission_confirmation_reason']
            self.transition(app_id, status, reason, **fields)

    def record_ready(self, app_id, *, live=False, url=None):
        app = self.db.application(app_id)
        fields = {'retry_allowed': int(app['attempts'] <= app['max_retries'] and
                  app['security_state'] in SAFE_SECURITY and self.db.setting('controlled_application_id') is None)}
        if live:
            fields = dict(application_state='READY_FOR_MANUAL_SUBMIT', session_preserved=1,
                          retry_allowed=0, manual_resume_allowed=0, url_before_submit=url)
        self.transition(app_id, 'READY', 'Ready for manual submission; no final Submit performed' if live else 'Auto-submit is disabled; retry to revalidate', **fields)

    def record_submission_intent(self, app_id):
        with self.atomic():
            app = self.db.application(app_id)
            if (self.db.submission_conflict(app_id) or app['manual_action_required']
                    or app['security_state'] not in SAFE_SECURITY or app['delivery_state'] != Delivery.NOT_STARTED):
                raise ValueError('Submission blocked by history or hold')
            self.transition(app_id, 'SUBMITTING', stage='submission intent', submit_intent_at=now(), delivery_state=Delivery.POSSIBLY_DELIVERED)

    def record_confirmation(self, app_id, evidence, url):
        self.transition(app_id, 'SUBMITTED', confirmation_text=evidence, confirmation_url=url,
                        submitted_at=now(), stage='confirmed', delivery_state=Delivery.OBSERVED)

    def record_failure(self, app_id, failure, max_retries):
        with self.atomic():
            app = self.db.application(app_id)
            if app['status'] in FINAL or self.db.automation_retired(app_id):
                return
            if app['delivery_state'] != Delivery.NOT_STARTED and failure.delivery == Delivery.NOT_STARTED:
                failure = replace(failure, delivery=Delivery(app['delivery_state']))
            held = bool(app['manual_action_required'] or self.db.setting('controlled_application_id') is not None or
                        app['security_state'] not in SAFE_SECURITY)
            decision = RetryPolicy().decide(failure, app['attempts'], max_retries, bool(app['submit_intent_at']), held=held)
            self.db.event(app_id, 'retry_decision', json.dumps({'failure': asdict(failure), 'decision': asdict(decision), 'attempts': app['attempts'], 'max_retries': max_retries}))
            if app['submit_intent_at'] or failure.delivery != Delivery.NOT_STARTED:
                self.record_hold(app_id, 'Outcome uncertain: ' + failure.reason,
                                 'SUBMISSION_UNKNOWN' if app['submit_intent_at'] else 'STEP_TRANSITION_UNKNOWN', unknown=True)
            elif held:
                self.record_hold(app_id, app['manual_action_reason'] or failure.reason, app['error_category'] or failure.category)
            elif decision.allowed:
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=decision.delay)).isoformat()
                self.transition(app_id, 'RETRY', failure.reason, retry_at=retry_at, retry_allowed=1,
                                error_category=failure.category, attempt_started=0, max_retries=max_retries)
            else:
                self.transition(app_id, 'FAILED', failure.reason, retry_at=None, retry_allowed=0,
                                error_category=failure.category, max_retries=max_retries)
            return decision

    def release_hold(self, app_id):
        with self.atomic():
            self.db.set_setting(f'manual_session:{app_id}', None)
            self.db.update_security(app_id, manual_action_required=0, manual_action_reason='', session_preserved=0)

    def resolve_input(self, app_id):
        """Resolved answers allow only a budgeted, otherwise unheld restart."""
        with self.atomic():
            app = self.db.application(app_id)
            if (app['status'] != 'NEEDS_INPUT' or app['submit_intent_at']
                    or app['security_state'] not in SAFE_SECURITY
                    or self.db.automation_retired(app_id)
                    or self.db.setting('controlled_application_id') is not None
                    or self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))):
                return False
            decision = RetryPolicy().decide(Failure(ErrorCategory.PROCESS_INTERRUPTED, 'Input resolved'), app['attempts'], app['max_retries'])
            self.transition(app_id, 'RETRY' if decision.allowed else 'FAILED', 'All pending questions resolved',
                            retry_allowed=int(decision.allowed), retry_at=None, manual_action_required=0,
                            manual_action_reason='', attempt_started=0)
            return decision.allowed

    def begin_reconstruction(self, app_id):
        with self.atomic():
            app = self.db.application(app_id)
            if (app['status'] != 'READY' or app['manual_action_required']
                    or app['security_state'] not in SAFE_SECURITY
                    or self.db.setting('controlled_application_id') is not None):
                raise ValueError('Reconstruction requires an unheld prepared application')
            if app['attempts'] > app['max_retries']:
                raise ValueError('Application attempt budget exhausted')
            self.transition(app_id, 'APPLYING', 'Explicit reconstruction verification', attempt_started=0)
            self.begin_attempt(app_id, app['max_retries'])

    def restore_checkpoint(self, app_id):
        with self.atomic():
            app = self.db.application(app_id)
            saved = self.db.setting(f'fill_checkpoint:{app_id}', {})
            if (saved.get('readiness') not in {'RECONSTRUCTABLE_CHECKPOINT', 'RECONSTRUCTION_VERIFIED'}
                    or app['manual_action_required'] or app['security_state'] not in SAFE_SECURITY
                    or self.db.submission_conflict(app_id) or self.db.setting('controlled_application_id') is not None
                    or not self.db.guard_listing(app_id)):
                raise ValueError('Checkpoint restoration requires an unprotected, unheld prepared application')
            decision = RetryPolicy().decide(Failure(ErrorCategory.PROCESS_INTERRUPTED, 'Explicit checkpoint restoration'), app['attempts'], app['max_retries'])
            if not decision.allowed:
                raise ValueError('Application attempt budget exhausted')
            self.transition(app_id, 'RETRY', 'User requested checkpoint restoration; revalidation required',
                            retry_allowed=1, retry_at=None, attempt_started=0)

    def resume(self, app_id, *, reconstruct=False, verification=None):
        with self.atomic():
            app = self.db.application(app_id)
            if (app['submit_intent_at'] or app['submission_confirmation_seen'] or self.db.automation_retired(app_id)
                    or not app['manual_resume_allowed'] or app['delivery_state'] != Delivery.NOT_STARTED
                    or app['security_state'] not in SAFE_SECURITY
                    or app['error_category'] in {'EXTERNAL_EXECUTION_APPROVAL_REQUIRED', 'EXECUTION_APPROVAL_BLOCKED'}):
                raise ValueError('Hold does not authorize automatic continuation')
            if reconstruct:
                if self.db.setting('controlled_application_id') is not None:
                    raise ValueError('Controlled hold requires the same live session')
                decision = RetryPolicy().decide(Failure(ErrorCategory.PROCESS_INTERRUPTED, 'Explicit reconstruction'), app['attempts'], app['max_retries'])
                if not decision.allowed:
                    raise ValueError('Application attempt budget exhausted')
            self.release_hold(app_id)
            self.transition(app_id, 'RETRY' if reconstruct else 'APPLYING', 'Explicit reconstruction' if reconstruct else 'Manual step completed; revalidating preserved form',
                            retry_allowed=int(reconstruct), retry_at=None, attempt_started=0 if reconstruct else app['attempt_started'],
                            verification_state=verification or app['verification_state'])

    def reconcile(self, app_id, submitted, evidence, evidence_source='user'):
        if evidence_source not in {'user', 'saved_employer_screenshot'}:
            raise ValueError('Unknown reconciliation evidence source')
        with self.atomic():
            app = self.db.application(app_id)
            if not app['submit_intent_at'] or app['status'] != 'MANUAL_REVIEW' or not evidence.strip():
                raise ValueError('Reconciliation requires an uncertain submission and evidence from employer history')
            if not submitted and (app['security_state'] not in SAFE_SECURITY or self.db.automation_retired(app_id)
                                  or self.db.setting('controlled_application_id') is not None):
                raise ValueError('A security hold or controlled/retired application cannot enable automatic retry')
            self.db.event(app_id, 'submission_intent_reconciled', json.dumps({'prior_submit_intent_at': app['submit_intent_at'],
                          'user_report': 'SUBMITTED' if submitted else 'NOT_SUBMITTED', 'evidence': evidence, 'source': evidence_source}))
            if submitted:
                self.record_confirmation(app_id, evidence, app['canonical_url'])
                if app['verification_state'] == 'NOT_REQUIRED':
                    self.release_hold(app_id)
            else:
                decision = RetryPolicy().decide(Failure(ErrorCategory.PROCESS_INTERRUPTED, evidence), app['attempts'], app['max_retries'])
                self.transition(app_id, 'RETRY' if decision.allowed else 'FAILED', 'User verified no submission: ' + evidence,
                                reconciled=True, submit_intent_at=None, retry_at=None, retry_allowed=int(decision.allowed),
                                manual_action_required=0, manual_action_reason='', error_category='', manual_resume_allowed=0,
                                session_preserved=0, attempt_started=0, delivery_state=Delivery.NOT_STARTED)

    def claim(self, application_id=None):
        if application_id is not None and self.db.automation_retired(application_id):
            return None
        target = self.db.setting("controlled_application_id")
        if target is not None and application_id != target:
            return None
        if application_id is None:
            self.db.maintain_listings()
        elif not self.db.one('SELECT id FROM applications WHERE id=?', (application_id,)) or not self.db.guard_listing(application_id):
            # Explicit runs must not perform maintenance on unrelated listings.
            return None
        with self.atomic():
            sql = """SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id
                WHERE j.listing_active=1 AND j.listing_status='ACTIVE' AND a.status IN ('QUEUED','RETRY') AND a.retry_allowed=1 AND a.manual_action_required=0 AND a.security_state IN ('NONE','PASSIVE_PROTECTION_DETECTED') AND a.submit_intent_at IS NULL AND (a.retry_at IS NULL OR a.retry_at<=?)
                AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id AND s.value NOT IN ('false','null','0'))
                """
            args = [now()]
            if application_id is not None:
                sql += " AND a.id=?"
                args.append(application_id)
            row = self.db.one(sql + " ORDER BY j.priority DESC,j.discovered_at,a.id LIMIT 1", args)
            if not row:
                return None
            if not self.db.guard_listing(row["id"]):
                return None
            previous = self.db.application(row["id"])
            self.transition(row["id"], State.CHECKING, started_at=previous["started_at"] or now(), stage="checking", attempt_started=0)
            return self.db.application(row["id"])


    def recover(self):
        from .manual import ManualCommands
        ManualCommands(self.db).recover()
        with self.atomic():
            for row in self.db.rows("SELECT id FROM applications WHERE session_preserved=1"):
                self.db.set_setting(f"manual_session:{row['id']}", None)
                self.db.update_security(row['id'], session_preserved=0)
                if self.db.application(row['id'])['application_state'] == 'READY_FOR_MANUAL_SUBMIT':
                    self.db.update_security(row['id'], application_state='READY_TO_SUBMIT')
                self.db.notify(f"lost-session:{row['id']}:{now()}", {"application_id": row['id'], "message": "Worker restarted; prior live browser unavailable. Saved checkpoint is not a live session."})
            for row in self.db.rows("SELECT * FROM applications WHERE status IN ('CHECKING','APPLYING','SUBMITTING')"):
                if not row['attempt_started'] and not row['submit_intent_at'] and row['status'] == 'CHECKING' and not row['manual_action_required'] and self.db.setting('controlled_application_id') is None and row['security_state'] in SAFE_SECURITY:
                    # A claim with no browser work is a reservation, not an employer attempt.
                    self.transition(row['id'], 'RETRY', 'Recovered unused reservation', retry_at=None)
                else:
                    delivery = Delivery(row['delivery_state'])
                    self.record_failure(row['id'], Failure(ErrorCategory.PROCESS_INTERRUPTED,
                        'Recovered interrupted processing', operation='recovery', stage=row['stage'], delivery=delivery), row['max_retries'])


    def retry(self, app_id):
        if self.db.automation_retired(app_id):
            raise ValueError("User-reported submission permanently excludes this application from automation")
        if not self.db.guard_listing(app_id):
            raise ValueError("Listing is no longer eligible for new processing")
        app = self.db.application(app_id)
        if (app["manual_action_required"] or app["security_state"] not in SAFE_SECURITY or self.db.setting("controlled_application_id") is not None):
            raise ValueError("Manual/security/controlled hold prevents automatic retry")
        if not app["retry_allowed"]:
            raise ValueError("Automatic retry disabled. Use resume-manual with the preserved session or reconcile employer history.")
        if app["submit_intent_at"]:
            raise ValueError("Submission outcome is uncertain. Use reconcile after checking employer history.")
        if app["status"] in {"CHECKING", "APPLYING", "SUBMITTING"}:
            raise ValueError("Application is currently active")
        if self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
            raise ValueError("Answer or skip pending questions first")
        if app["attempts"] > app["max_retries"]:
            raise ValueError("Application attempt budget exhausted")
        self.transition(app_id, State.RETRY, "User requested retry", retry_at=None, attempt_started=0)


    def dimensions(self, app_id, **fields):
        from .database import SECURITY_COLUMNS
        with self.atomic():
            if not fields or not fields.keys() <= SECURITY_COLUMNS.keys():
                raise ValueError("Invalid security update")
            previous = self.db.one("SELECT * FROM applications WHERE id=?", (app_id,))
            if previous and previous["submission_confirmation_seen"]:
                if fields.get("submission_confirmation_seen") == 0 or fields.get("application_state", "SUBMITTED") != "SUBMITTED":
                    raise ValueError("Confirmed submission cannot be erased by verification outcome")
            # diagnostics_at describes the last changed observation, not a heartbeat.
            # Explicit timestamp-only diagnostics still persist (e.g. handoff capture).
            compared = fields.keys() - {'diagnostics_at'} or fields.keys()
            if previous and all(previous[key] == fields[key] for key in compared):
                return
            self.db.execute("UPDATE applications SET " + ",".join(f"{k}=?" for k in fields) + ",updated_at=? WHERE id=?", (*fields.values(), now(), app_id))
