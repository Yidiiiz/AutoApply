import json

from .answers import from_row, validate_answer, writing_topic, concept, AnswerResolver, is_writing_question
from .archive import archive_application
from .jobs import freshness
from .freshness import utc
from .models import Answer, State, now
from .manual import enqueue_inspection, ManualCommands, ManualCommand, validate_session_token


class Controller:
    def __init__(self, config, db, ai=None):
        self.config, self.db, self.ai = config, db, ai

    def pending(self):
        rows = self.db.rows("""SELECT q.*,a.status application_status,j.company,j.title,j.canonical_url,
            j.posted_at,j.listing_status,a.submit_intent_at,a.manual_action_required
            FROM questions q JOIN applications a ON a.id=q.application_id JOIN jobs j ON j.id=a.job_id
            WHERE (j.listing_active=1 OR a.submit_intent_at IS NOT NULL OR a.manual_action_required=1) AND q.status='PENDING' AND a.status NOT IN ('SUBMITTED','ALREADY_APPLIED','INELIGIBLE','CLOSED','INVALID','DUPLICATE') ORDER BY q.id""")
        days, reference = self.db.listing_days(), utc()
        return [row for row in rows if row['submit_intent_at']
                or (row['manual_action_required'] and row['application_status'] != 'NEEDS_INPUT')
                or self.db.listing_decision(row, reference, days=days)[2]]

    def answer(self, question_id, value):
        row = self.db.one("SELECT * FROM questions WHERE id=?", (question_id,))
        if not row or row["status"] != "PENDING":
            raise ValueError("Question is no longer pending")
        app = self.db.application(row["application_id"])
        if app["status"] not in {"NEEDS_INPUT", "MANUAL_REVIEW", "AUTH_REQUIRED"}:
            raise ValueError("Application is not waiting for input")
        self.check_target(app["id"])
        self.check_answer_mutation(app["id"])
        q = from_row(row)
        validate_answer(q, value)
        with self.db.transaction():
            self.db.save_answer(question_id, Answer(value, "user_confirmed", verified=True,
                provenance={'question_id': question_id,
                            'replaces_proposal': (json.loads(row.get('answer_provenance') or '{}').get('provenance')
                                                 or self.db.setting(f'draft_provenance:{question_id}')),
                            'canonical_profile_revision': self.config.profile_snapshot().revision}), verified=q.kind != "file")
            if q.key == "listing-date":
                self.db.execute("UPDATE jobs SET posted_at=?,date_evidence='user confirmed' WHERE id=?", (value, app["job_id"]))
                if not freshness(value, self.config["jobs"]["max_listing_age_days"]):
                    self.db.lifecycle.transition(app["id"], State.INVALID, "Confirmed posting date is outside the listing age window")
            elif q.key == "eligibility":
                if value == "No":
                    self.db.lifecycle.transition(app["id"], State.INELIGIBLE, "User confirmed ineligibility")
                else:
                    self.db.lifecycle.metadata(app["id"], eligibility_override=1)
            if q.kind == "textarea":
                self.db.execute("""INSERT INTO written_responses(question,topic,answer,company,job_title,verified,provider,evidence,created_at)
                    VALUES (?,?,?,?,?,1,'user','[]',?)""", (q.label, writing_topic(q.label), value, app["company"], app["title"], now()))
                self.db.execute('UPDATE written_responses SET question_signature=?,profile_revision=? WHERE id=last_insert_rowid()',
                                (row['question_signature'], self.config.profile_snapshot().revision))
            self.db.event(app["id"], "user_answer", f"Question {question_id} confirmed")
            from .concepts import REGISTRY
            from .field_mapping import describe
            descriptor = describe(q, app, self.config.profile_snapshot().facts)
            spec = REGISTRY.get(descriptor.semantic_key)
            path = spec.profile_path if spec and spec.factual_fallback and 'global' in spec.scopes else None
            # Combined sponsorship has a deliberately narrow canonical grammar.
            if descriptor.semantic_key == 'sponsorship_combined':
                path = 'sponsorship_combined'
            if path:
                # Canonical reusable facts stay in the normal verified answer store;
                # application-specific essays and salary answers retain their scope.
                self.db.set_setting("verified_fact:" + path, {"value": value, "source":"USER_PROVIDED", "question_id":question_id})
            self._resume_if_ready(app["id"])
        if self.db.one("SELECT application_id FROM manual_requests WHERE application_id=?", (app['id'],)):
            return f"All required answers are available; resuming {app['company']} #{app['id']}."
        return "Answer saved. " + self.db.application(app["id"])["status"]

    def _resume_if_ready(self, app_id):
        if self.db.setting("controlled_application_id") is not None:
            return  # Controlled holds require an explicit same-session resume.
        app = self.db.application(app_id)
        if (app["status"] == "MANUAL_REVIEW" and app["error_category"] == "INPUT_REQUIRED"
                and app["manual_resume_allowed"] and not app["submit_intent_at"]
                and app["security_state"] in {"NONE", "PASSIVE_PROTECTION_DETECTED"}
                and not self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))):
            enqueue_inspection(self.db, app_id, reconstruct=True)
        if app["status"] == "NEEDS_INPUT" and self.db.guard_listing(app_id) and not self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
            self.db.lifecycle.resolve_input(app_id)

    def check_answer_mutation(self, app_id):
        app = self.db.application(app_id)
        if app['submit_intent_at'] or app['submission_confirmation_seen'] or self.db.automation_retired(app_id):
            raise ValueError('Protected application requires outcome inspection, not answer changes')

    def skip(self, question_id):
        row = self.db.one("SELECT * FROM questions WHERE id=? AND status='PENDING'", (question_id,))
        if not row:
            raise ValueError("Question is no longer pending")
        self.check_target(row["application_id"])
        self.check_answer_mutation(row["application_id"])
        if row["required"]:
            self.db.lifecycle.transition(row["application_id"], State.MANUAL_REVIEW, "User skipped required question; answer remains unresolved")
        else:
            self.db.execute("UPDATE questions SET status='SKIPPED',updated_at=? WHERE id=?", (now(), question_id))
            self._resume_if_ready(row["application_id"])
        archive_application(self.config, self.db, row["application_id"])
        return "Skipped; no answer was invented."

    async def draft(self, question_id):
        row = self.db.one("SELECT * FROM questions WHERE id=? AND status='PENDING'", (question_id,))
        if not row or not self.ai:
            raise ValueError("Question or AI provider not available")
        self.check_target(row["application_id"])
        q, app = from_row(row), self.db.application(row["application_id"])
        if not self.db.guard_listing(app["id"]):
            raise ValueError("Listing is no longer eligible for application preparation")
        if not is_writing_question(q) or q.key in {"eligibility", "listing-date", "resume"}:
            raise ValueError("AI writing is only available for open-ended written questions")
        result = await self.ai.draft(q, app)
        with self.db.transaction():
            self.db.set_setting(f"draft:{question_id}", result.value)
            self.db.set_setting(f"draft_provenance:{question_id}", result.provenance)
            self.db.event(app["id"], "ai_draft", f"Draft for question {question_id}; source {result.source}")
        return result.value

    def status_snapshot(self, app_id):
        from .status import snapshot
        page = getattr(self, "live_page", lambda _: None)(app_id)
        return snapshot(self.db, app_id, live_page=page, live_browser=page is not None)

    def inspect(self, app_id):
        self.check_target(app_id)
        app = self.db.application(app_id)
        return {"application": app, "snapshot": self.status_snapshot(app_id),
                "manual_session": self.db.setting(f"manual_session:{app_id}"),
                "questions": self.db.rows("SELECT * FROM questions WHERE application_id=? ORDER BY id", (app_id,)),
                "events": self.db.rows("SELECT * FROM events WHERE application_id=? ORDER BY id DESC LIMIT 30", (app_id,)),
                "writing": self.db.rows("SELECT * FROM written_responses WHERE company=? AND job_title=? ORDER BY id DESC LIMIT 10", (app["company"], app["title"]))}

    def explain(self, app_id, message=""):
        data = self.inspect(app_id)
        app = data["application"]
        view = data["snapshot"]
        text = message.lower()
        if "description" in text:
            return app["description"] or "No job description captured yet."
        if "essay" in text or "writing" in text:
            return json.dumps(data["writing"], indent=2)
        if "answer" in text or "question" in text or "missing" in text:
            return "\n".join(f"#{q['id']} {q['raw_question']}: {q['answer'] or q['status']} ({q['reason']})" for q in data["questions"]) or "No questions captured yet."
        return f"Application {app_id}: {app['company']} — {app['title']}\n{app['status']} / {app['stage']}\nSubmission: {app['application_state']} | Security: {app['security_state']} | Verification: {app['verification_state']}\nConfirmed: {bool(app['submission_confirmation_seen'])} | Retry allowed: {bool(app['retry_allowed'])}\n{app['failure_reason']}\nLive session: {view['session']['availability']} | Readiness: {view['readiness']}\n{app['canonical_url']}\nAsk for description, answers, missing questions, writing, or use resume-manual/retry/stop."

    def check_target(self, app_id):
        target = self.db.setting("controlled_application_id")
        if target is not None and app_id != target:
            raise ValueError(f"This controlled run permits only application #{target}")
        if not self.db.application(app_id):
            raise ValueError("Unknown application")

    def resolve_known_pending(self, app_id):
        self.check_target(app_id)
        app = self.db.application(app_id)
        if app['error_category'] != 'INPUT_REQUIRED' or app['submit_intent_at']:
            return
        resolver = AnswerResolver(self.config, self.db)
        for row in self.db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
            answer = resolver.resolve(from_row(row), app)
            if answer and answer.confidence >= self.config['application']['min_confidence']:
                self.db.save_answer(row['id'], answer)
                self.db.event(app_id, 'known_pending_resolved', f"Question {row['id']}: {answer.source}")

    def waiting_target(self):
        rows = self.db.rows("SELECT id FROM applications WHERE manual_action_required=1 OR status='NEEDS_INPUT'")
        target = self.db.setting("controlled_application_id")
        rows = [r for r in rows if target is None or r['id'] == target]
        if len(rows) != 1:
            raise ValueError("Specify the application ID; there is no single waiting application.")
        return rows[0]['id']

    def command(self, text):
        return self.route(text)

    def route(self, text):
        command = ManualCommand.parse(text, self.db)
        parts, cmd = command.parameters['parts'], command.action
        if command.application_id is not None:
            self.check_target(command.application_id)
        supported = {"status","pending","answer","yes","no","skip","resume","stop","help","ai","accept","pause","queue","recent","autosubmit","retry","resume-manual","verification","application","inspect","ask","inspect-manual","reconcile"}
        if cmd not in supported:
            return f"I don't recognize !{cmd}. Use !help for available commands."
        if cmd == "help":
            return "Commands: !status [APP_ID], !pending, !answer QUESTION_ID <exact answer>, !yes [QUESTION_ID], !no [QUESTION_ID], !skip QUESTION_ID, !resume [APP_ID], !stop [APP_ID], !help. Example: !answer 23 Yes. !resume rechecks the application; it never repeats a submission with existing intent."
        if cmd in {"yes", "no"}:
            if len(parts) == 1:
                app_id = self.waiting_target()
                rows = self.db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))
                if len(rows) != 1 or set(json.loads(rows[0]['options'])) != {"Yes","No"}:
                    raise ValueError("I have multiple or non-yes/no pending questions. Specify the question number.")
                identifier = rows[0]['id']
            else:
                identifier = int(parts[1])
            return self.answer(identifier, cmd.title())
        if cmd == "resume" and (len(parts)>1 or self.db.rows("SELECT id FROM applications WHERE manual_action_required=1 OR status='NEEDS_INPUT'")):
            identifier = int(parts[1]) if len(parts)>1 else self.waiting_target()
            self.check_target(identifier)
            validate_session_token(self.db, identifier, command.session_token)
            self.resolve_known_pending(identifier)
            if self.db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (identifier,)):
                return "Required answers are still pending. The application remains paused."
            app = self.db.application(identifier)
            if not app['manual_action_required']:
                raise ValueError("Application is not waiting for manual resumption")
            token = command.session_token
            enqueue_inspection(self.db, identifier, token=token, reconstruct=True)
            self.db.set_setting("paused", False)
            return f"Resuming application #{identifier}; rechecking the saved state."
        if cmd == "stop":
            if len(parts)>1:
                self.check_target(int(parts[1]))
            self.db.set_setting("paused", True)
            ManualCommands(self.db).cancel(int(parts[1]) if len(parts)>1 else self.db.setting("controlled_application_id"))
            return "Stopping after the current safe point. Saved input remains available."
        if cmd == "status" and len(parts)==1 and self.db.setting("controlled_application_id") is not None:
            return self.explain(self.db.setting("controlled_application_id"))
        if cmd == "status" and len(parts)>1:
            self.check_target(int(parts[1]))
            return self.explain(int(parts[1]))
        if cmd in {"answer", "skip", "accept", "ai"} and len(parts)>1:
            row = self.db.one("SELECT application_id FROM questions WHERE id=?", (int(parts[1]),))
            if not row:
                raise ValueError("Unknown question")
            self.check_target(row['application_id'])
        if self.db.setting("controlled_application_id") is not None and cmd in {"retry","resume-manual","verification","application","inspect","ask","inspect-manual","reconcile"}:
            if len(parts)<2:
                raise ValueError("Specify an application ID")
            self.check_target(int(parts[1]))
        return self._command(command)

    def _command(self, command):
        parts = command.parameters["parts"]
        if not parts:
            return "Commands: pause, resume, status, queue, pending, recent, autosubmit on|off, retry ID, stop ID, application ID, answer QUESTION_ID ANSWER, skip QUESTION_ID"
        cmd = parts[0].lower()
        if cmd in {"resume-manual", "inspect-manual", "verification"}:
            if len(parts) < 2:
                raise ValueError("Use resume-manual ID or verification ID passed|failed|skipped")
            identifier = int(parts[1])
            app = self.db.application(identifier)
            if not app["manual_action_required"]:
                raise ValueError("Application has no pending manual intervention")
            action = ("inspect-only" if cmd == "inspect-manual" else "inspect") if cmd in {"resume-manual", "inspect-manual"} else parts[2].split()[0].upper() if len(parts) == 3 else ""
            if action not in {"inspect", "inspect-only", "PASSED", "FAILED", "SKIPPED"}:
                raise ValueError("Use verification ID passed|failed|skipped")
            if cmd == "verification" and app["verification_state"] == "NOT_REQUIRED":
                raise ValueError("No verification step is recorded for this application")
            if self.db.automation_retired(identifier):
                raise ValueError("User-reported submission permanently excludes this application from automation")
            token = command.session_token
            enqueue_inspection(self.db, identifier, action, token=token)
            return "Manual inspection queued for the running worker; keep its browser open. No automatic repeat submission."
        if cmd in {"pause", "resume"}:
            self.db.set_setting("paused", cmd == "pause")
            return "Processing " + ("paused" if cmd == "pause" else "resumed")
        if cmd == "autosubmit":
            if len(parts) != 2 or parts[1] not in {"on", "off"}:
                raise ValueError("Use autosubmit on|off")
            self.db.set_setting("auto_submit", parts[1] == "on")
            return "Auto-submit " + parts[1]
        if cmd == "status":
            return json.dumps({"paused": self.db.setting("paused", False), "auto_submit": self.db.setting("auto_submit", self.config["application"]["auto_submit"]),
                               "listing_stats": self.db.listing_statistics(),
                               "listing_statistics_as_of": self.db.setting('listing_statistics_as_of'),
                               "listing_maintenance_due_at": self.db.setting('listing_maintenance_due_at'),
                               "applications": [self.status_snapshot(r["id"]) for r in self.db.rows("SELECT id FROM applications ORDER BY id")],
                               "states": self.db.rows("SELECT status,count(*) count FROM applications GROUP BY status")}, indent=2)
        if cmd in {"queue", "recent"}:
            condition = "WHERE j.listing_active=1 AND a.status IN ('QUEUED','RETRY','CHECKING','APPLYING','READY')" if cmd == "queue" else ""
            rows = self.db.execute(f"SELECT a.id,j.company,j.title,a.status,a.updated_at,j.canonical_url,j.posted_at,j.listing_status FROM applications a JOIN jobs j ON j.id=a.job_id {condition} ORDER BY a.updated_at DESC")
            from itertools import islice
            if cmd == 'queue':
                days, reference = self.db.listing_days(), utc()
                rows = (row for row in rows if self.db.listing_decision(dict(row), reference, days=days)[2])
            rows = list(islice(rows, 20))
            return "\n".join(f"#{r['id']} {r['company']} | {r['title']} | {r['status']} | {r['updated_at']}\n{r['canonical_url']}" for r in rows) or "No applications."
        if cmd == "pending":
            return "\n".join(f"#{q['id']} (application {q['application_id']}): {q['raw_question']}" for q in self.pending()) or "No pending questions."
        if len(parts) < 2:
            raise ValueError("This command requires an ID")
        identifier = int(parts[1])
        if cmd == "reconcile":
            import shlex
            args = shlex.split(parts[2]) if len(parts)>2 else []
            if len(args)<3 or args[0] not in {'submitted','not-submitted'} or args[1] != '--evidence':
                raise ValueError('Use reconcile ID submitted|not-submitted --evidence <employer evidence>')
            self.reconcile(identifier, args[0]=='submitted', ' '.join(args[2:]))
            return 'Submission outcome reconciled.'
        if cmd == "retry":
            self.db.retry(identifier)
            return "Application queued for retry"
        if cmd in {"application", "inspect", "ask"}:
            return self.explain(identifier, parts[2] if len(parts) > 2 else "")
        if cmd == "answer" and len(parts) == 3:
            row = self.db.one("SELECT field_type FROM questions WHERE id=?", (identifier,))
            value = json.loads(parts[2]) if row and row["field_type"] == "multiselect" else parts[2]
            return self.answer(identifier, value)
        if cmd == "skip":
            return self.skip(identifier)
        if cmd == "accept":
            draft = self.db.setting(f"draft:{identifier}")
            if draft is None:
                raise ValueError("No proposed draft exists")
            return self.answer(identifier, draft)
        raise ValueError("I don't recognize that command. Use !help for available commands.")

    def reconcile(self, app_id, submitted, evidence, *, evidence_source="user"):
        self.check_target(app_id)
        self.db.lifecycle.reconcile(app_id, submitted, evidence, evidence_source)
