import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps

from .jobs import ats_identity, canonical_url, job_identity, normalize, priority
from .listing_store import ListingStore
from .freshness import extract_posting_date, freshness_state, parse_posted, utc, window
from .models import FINAL, State, now
from .retry import ErrorCategory, RetryPolicy

SECURITY_COLUMNS = {
    "application_state": "TEXT NOT NULL DEFAULT 'DISCOVERED'",
    "security_state": "TEXT NOT NULL DEFAULT 'NONE'",
    "verification_state": "TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
    "ats_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    "security_provider": "TEXT NOT NULL DEFAULT 'UNKNOWN'", "security_type": "TEXT DEFAULT ''",
    "last_http_status": "INTEGER", "last_security_message": "TEXT DEFAULT ''",
    "submission_confirmation_seen": "INTEGER NOT NULL DEFAULT 0",
    "submission_confirmation_reason": "TEXT DEFAULT ''",
    "manual_action_required": "INTEGER NOT NULL DEFAULT 0", "manual_action_reason": "TEXT DEFAULT ''",
    "retry_allowed": "INTEGER NOT NULL DEFAULT 1", "error_category": "TEXT DEFAULT ''",
    "url_before_submit": "TEXT", "url_after_submit": "TEXT", "previous_url": "TEXT",
    "current_url": "TEXT", "page_title": "TEXT", "screenshot_path": "TEXT",
    "diagnostics_at": "TEXT", "session_preserved": "INTEGER NOT NULL DEFAULT 0",
    "manual_resume_allowed": "INTEGER NOT NULL DEFAULT 0",
}

STATE_MAP = {
    "DISCOVERED": "DISCOVERED", "QUEUED": "DISCOVERED", "RETRY": "DISCOVERED",
    "CHECKING": "OPENED", "APPLYING": "FILLING", "READY": "READY_TO_SUBMIT",
    "SUBMITTING": "SUBMITTING", "SUBMITTED": "SUBMITTED", "ALREADY_APPLIED": "ALREADY_APPLIED",
    "MANUAL_REVIEW": "MANUAL_REQUIRED", "NEEDS_INPUT": "MANUAL_REQUIRED", "AUTH_REQUIRED": "MANUAL_REQUIRED",
}
STATE_MAP.update({s: s for s in ('CLOSED', 'INVALID', 'INELIGIBLE', 'DUPLICATE', 'FAILED')})


def atomic_mutation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if self.conn.in_transaction:
            return method(self, *args, **kwargs)
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, identity_key TEXT UNIQUE NOT NULL, company TEXT NOT NULL, title TEXT NOT NULL,
 location TEXT NOT NULL, canonical_url TEXT NOT NULL, ats TEXT, description TEXT DEFAULT '',
 posted_at TEXT, date_evidence TEXT, discovered_at TEXT NOT NULL, priority REAL DEFAULT 0,
 status TEXT NOT NULL, eligibility_json TEXT, reason TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS job_sources (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), source_name TEXT NOT NULL,
 source_url TEXT NOT NULL, source_job_id TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 UNIQUE(source_name, source_job_id));
CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY, job_id INTEGER UNIQUE NOT NULL REFERENCES jobs(id), status TEXT NOT NULL,
 started_at TEXT, updated_at TEXT NOT NULL, submitted_at TEXT, resume_used TEXT, resume_sha256 TEXT,
 confirmation_text TEXT, confirmation_url TEXT, failure_reason TEXT DEFAULT '', stage TEXT DEFAULT '',
 attempts INTEGER DEFAULT 0, retry_at TEXT, submit_intent_at TEXT, eligibility_override INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS questions (
 id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id), field_key TEXT NOT NULL,
 raw_question TEXT NOT NULL, normalized_question TEXT NOT NULL, field_type TEXT NOT NULL,
 options TEXT NOT NULL, required INTEGER NOT NULL, max_length INTEGER, scope TEXT NOT NULL,
 answer TEXT, answer_source TEXT, confidence REAL, status TEXT NOT NULL, reason TEXT DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(application_id, field_key));
CREATE TABLE IF NOT EXISTS known_answers (
 id INTEGER PRIMARY KEY, normalized_question TEXT NOT NULL, concept TEXT DEFAULT '', scope TEXT NOT NULL,
 answer TEXT NOT NULL, verified INTEGER NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
 last_used_at TEXT, usage_count INTEGER DEFAULT 0, UNIQUE(normalized_question, scope));
CREATE TABLE IF NOT EXISTS written_responses (
 id INTEGER PRIMARY KEY, question TEXT NOT NULL, topic TEXT, answer TEXT NOT NULL,
 company TEXT NOT NULL, job_title TEXT NOT NULL, verified INTEGER NOT NULL, provider TEXT,
 evidence TEXT, created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id), kind TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, delivered_at TEXT,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_revisions (
 source TEXT PRIMARY KEY, revision TEXT, default_branch TEXT, checked_at TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS app_queue ON applications(status, retry_at);
CREATE INDEX IF NOT EXISTS questions_pending ON questions(status, application_id);
CREATE TABLE IF NOT EXISTS manual_requests (
 application_id INTEGER PRIMARY KEY REFERENCES applications(id), action TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class Database(ListingStore):
    def __init__(self, path, max_listing_age_days=None, *, startup_maintenance=True):
        self._history_ready = False
        self._flushing_history = False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        if self.conn.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise RuntimeError("Database belongs to a newer AutoApply version")
        self.conn.executescript(SCHEMA)
        with self.transaction():
            columns = {row["name"] for row in self.rows("PRAGMA table_info(applications)")}
            migrate = "application_state" not in columns
            for name, definition in SECURITY_COLUMNS.items():
                if name not in columns:
                    self.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
            if migrate:
                from .security import classify_message
                for row in self.rows("SELECT a.id,a.status,a.confirmation_text,a.submit_intent_at,a.failure_reason,a.updated_at,j.ats FROM applications a JOIN jobs j ON j.id=a.job_id"):
                    confirmed = bool(row["confirmation_text"] and row["status"] == "SUBMITTED")
                    detection = classify_message(row["failure_reason"] or "")
                    self.update_security(row["id"], application_state=STATE_MAP.get(row["status"], "FAILED"),
                        submission_confirmation_seen=int(confirmed), submission_confirmation_reason=row["confirmation_text"] or "",
                        retry_allowed=int(not row["submit_intent_at"] and row["status"] not in FINAL and row["status"] != "MANUAL_REVIEW"),
                        manual_action_required=int(row["status"] in {"MANUAL_REVIEW", "AUTH_REQUIRED"}),
                        ats_type=row["ats"] or "UNKNOWN", security_state=detection.state, security_type=detection.type,
                        last_security_message=detection.message, error_category=detection.category)
                    self.execute("UPDATE applications SET updated_at=? WHERE id=?", (row["updated_at"], row["id"]))
            self.execute("PRAGMA user_version=2")
        first_listings = self.initialize_listings(Path(path))
        if max_listing_age_days is not None:
            window(max_listing_age_days)
            self.set_setting('listing_max_age_days', max_listing_age_days)
        self._initialize_history(Path(path).parent / 'application_history')
        self.listing_startup_report = self.cleanup_stale_listings(migration=first_listings) if startup_maintenance else {}

    def _initialize_history(self, root):
        from .archive import ApplicationHistory
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS history_dirty (application_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS history_transitions (
                id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, status TEXT NOT NULL,
                timestamp TEXT, reason TEXT, source TEXT, security_state TEXT,
                verification_state TEXT, security_provider TEXT, manual_action_required INTEGER);
            CREATE INDEX IF NOT EXISTS history_transition_app ON history_transitions(application_id);
            CREATE TRIGGER IF NOT EXISTS history_created AFTER INSERT ON applications BEGIN
                INSERT OR IGNORE INTO history_dirty VALUES (NEW.id);
                INSERT INTO history_transitions(application_id,status,timestamp,reason,source,security_state,verification_state,security_provider,manual_action_required)
                VALUES(NEW.id,NEW.application_state,NEW.updated_at,NEW.failure_reason,'database',NEW.security_state,NEW.verification_state,NEW.security_provider,NEW.manual_action_required);
            END;
            CREATE TRIGGER IF NOT EXISTS history_changed AFTER UPDATE ON applications BEGIN
                INSERT OR IGNORE INTO history_dirty VALUES (NEW.id);
            END;
            CREATE TRIGGER IF NOT EXISTS history_deleted AFTER DELETE ON applications BEGIN
                INSERT OR IGNORE INTO history_dirty VALUES (OLD.id);
            END;
            CREATE TRIGGER IF NOT EXISTS history_state AFTER UPDATE ON applications
            WHEN NEW.application_state != OLD.application_state OR NEW.security_state != OLD.security_state
                OR NEW.verification_state != OLD.verification_state OR NEW.security_provider != OLD.security_provider
                OR NEW.manual_action_required != OLD.manual_action_required BEGIN
                INSERT INTO history_transitions(application_id,status,timestamp,reason,source,security_state,verification_state,security_provider,manual_action_required)
                VALUES(NEW.id,NEW.application_state,NEW.updated_at,NEW.failure_reason,'database',NEW.security_state,NEW.verification_state,NEW.security_provider,NEW.manual_action_required);
            END;
        ''')
        for table, column in [('events', 'application_id'), ('questions', 'application_id'), ('job_sources', 'job_id'), ('jobs', 'id')]:
            for operation, row in [('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')]:
                value = f'SELECT id FROM applications WHERE job_id={row}.{column}' if table in {'jobs', 'job_sources'} else f'SELECT {row}.{column} WHERE {row}.{column} IS NOT NULL'
                self.conn.executescript(f'''CREATE TRIGGER IF NOT EXISTS history_{table}_{operation}
                    AFTER {operation} ON {table} BEGIN INSERT OR IGNORE INTO history_dirty {value}; END;''')
        self.history = ApplicationHistory(root)
        first = not (root / '.layout.json').exists()
        self.history_startup_report = self.history.validate()
        for row in self.rows('SELECT id FROM applications'):
            if first or str(row['id']) not in self.history.records:
                self.conn.execute('INSERT OR IGNORE INTO history_dirty VALUES (?)', (row['id'],))
        self._history_ready = True
        self.flush_history()

    def flush_history(self, ids=()):
        if not self._history_ready or self._flushing_history or self.conn.in_transaction:
            return
        self._flushing_history = True
        try:
            for app_id in ids:
                self.conn.execute('INSERT OR IGNORE INTO history_dirty VALUES (?)', (app_id,))
            # Hold the DB write lock through export so another process cannot
            # acknowledge an older snapshot over a newer committed transition.
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                pending = [r[0] for r in self.conn.execute('SELECT application_id FROM history_dirty')]
                if pending:
                    self.history.sync(self, pending)
                    self.conn.execute('DELETE FROM history_dirty')
                self.conn.execute('COMMIT')
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
        finally:
            self._flushing_history = False

    def close(self):
        self.conn.close()

    def execute(self, sql, args=()):
        cursor = self.conn.execute(sql, args)
        if sql.lstrip().split(None, 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}:
            self.flush_history()
        return cursor

    def rows(self, sql, args=()):
        return [dict(row) for row in self.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        row = self.execute(sql, args).fetchone()
        return dict(row) if row else None

    @contextmanager
    def transaction(self):
        self.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.execute("COMMIT")
        except BaseException:
            self.execute("ROLLBACK")
            raise
        self.flush_history()

    def setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def event(self, app_id, kind, detail):
        self.execute("INSERT INTO events(application_id,kind,detail,created_at) VALUES (?,?,?,?)",
                     (app_id, kind, detail, now()))

    def notify(self, key, payload):
        if key.startswith(("rate:", "daily-cap:", "question:", "hold:")):
            self.event(payload.get("application_id"), "notification_suppressed", key)
            return
        self.execute("INSERT OR IGNORE INTO notifications(dedupe_key,payload,created_at) VALUES (?,?,?)",
                     (key, json.dumps(payload), now()))

    @atomic_mutation
    def update_security(self, app_id, **fields):
        if not fields or not fields.keys() <= SECURITY_COLUMNS.keys():
            raise ValueError("Invalid security update")
        previous = self.one("SELECT submission_confirmation_seen FROM applications WHERE id=?", (app_id,))
        if previous and previous["submission_confirmation_seen"]:
            if fields.get("submission_confirmation_seen") == 0 or fields.get("application_state", "SUBMITTED") != "SUBMITTED":
                raise ValueError("Confirmed submission cannot be erased by verification outcome")
        self.execute("UPDATE applications SET " + ",".join(f"{k}=?" for k in fields) + ",updated_at=? WHERE id=?", (*fields.values(), now(), app_id))

    def ingest(self, listing, config):
        import logging
        from contextlib import nullcontext
        from dataclasses import replace
        log = logging.getLogger('autoapply')
        days = config['jobs']['max_listing_age_days']
        window(days)
        self.set_setting('listing_max_age_days', days)
        url, timestamp = canonical_url(listing.url), now()
        key = job_identity(url)
        source_id = listing.source_id or key
        evidence = extract_posting_date(api=listing.posted_at, repost=listing.reposted_at,
            genuine_repost=listing.posted_at_source == 'explicit.repost', reference=timestamp)
        posted = evidence.posted_at
        date_source = listing.posted_at_source or evidence.source
        confidence = listing.posted_at_confidence if listing.posted_at_source else evidence.confidence
        created = False
        with nullcontext() if self.conn.in_transaction else self.transaction():
            existing = self.one("SELECT j.* FROM jobs j JOIN job_sources s ON s.job_id=j.id WHERE s.source_name=? AND s.source_job_id=?",
                                (listing.source, source_id))
            existing = existing or self.one("SELECT * FROM jobs WHERE identity_key=? OR canonical_url=?", (key, url))
            alias = self.one('SELECT identity_key FROM listing_aliases WHERE source=? AND source_job_id=?', (listing.source,source_id))
            if alias:
                key = alias['identity_key']
                existing = existing or self.one('SELECT * FROM jobs WHERE identity_key=?',(key,))
            observed = self.one('SELECT * FROM listing_observations WHERE identity_key=?', (key,))
            previous = existing or observed
            if previous:
                log.info('[DEDUP] Existing listing found; updating last_seen_at')
                # Preserve earlier evidence. Only an explicit, verified repost can reset age.
                old = previous.get('posted_at')
                if old and not evidence.reposted_at and (not posted or utc(old) < utc(posted)):
                    posted = old
                    date_source = previous.get('posted_at_source', previous.get('date_source', ''))
                    confidence = previous.get('posted_at_confidence', previous.get('date_confidence', 'unknown'))
            fresh = freshness_state(posted, days, timestamp)
            status = 'CLOSED' if listing.closed else (previous or {}).get('listing_status', 'UNKNOWN')
            if status not in {'CLOSED','REMOVED'}:
                status = 'ACTIVE' if fresh == 'FRESH' else 'STALE' if fresh == 'STALE' else 'UNKNOWN'
            active = fresh == 'FRESH' and status == 'ACTIVE'
            original_dates = [parse_posted(value) for value in (
                (existing or {}).get('original_posted_at'), listing.original_posted_at, evidence.original_posted_at, posted)]
            original = min(value for value in original_dates if value is not None).isoformat() if any(original_dates) else None
            job_id = existing['id'] if existing else None
            if active and not existing:
                job_id = self.execute("""INSERT INTO jobs(identity_key,company,title,location,canonical_url,ats,description,posted_at,
                    date_evidence,discovered_at,priority,status,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key,listing.company,listing.title,listing.location,url,ats_identity(url)[0],listing.description,
                     posted,listing.date_evidence,timestamp,priority(replace(listing,posted_at=posted),config),'QUEUED','')).lastrowid
                app_id = self.execute("INSERT INTO applications(job_id,status,updated_at) VALUES (?,'QUEUED',?)",(job_id,timestamp)).lastrowid
                self.update_security(app_id, application_state='DISCOVERED', retry_allowed=1)
                self.event(app_id,'discovered','Queued from '+listing.source)
                created = True
            culled = timestamp if fresh == 'STALE' and status in {'CLOSED','REMOVED'} else None
            if job_id:
                key = existing['identity_key'] if existing else key
                self.execute("""UPDATE jobs SET posted_at=?,posted_at_source=?,posted_at_confidence=?,
                    original_posted_at=?,reposted_at=coalesce(?,reposted_at),
                    source_updated_at=coalesce(?,source_updated_at),listing_status=?,freshness_state=?,listing_active=?,
                    last_seen_at=?,last_checked_at=?,closed_at=coalesce(closed_at,?),stale_at=coalesce(stale_at,?),
                    culled_at=coalesce(culled_at,?) WHERE id=?""",
                    (posted,date_source,confidence,original,evidence.reposted_at,
                     listing.updated_at,status,fresh,int(active),timestamp,timestamp,
                     timestamp if status in {'CLOSED','REMOVED'} else None,timestamp if fresh=='STALE' else None,culled,job_id))
                if self.one('SELECT id FROM job_sources WHERE source_name=? AND source_job_id=?',(listing.source,source_id)):
                    self.execute('UPDATE job_sources SET last_seen=? WHERE source_name=? AND source_job_id=?',(timestamp,listing.source,source_id))
                else:
                    self.execute("INSERT INTO job_sources(job_id,source_name,source_url,source_job_id,first_seen,last_seen) VALUES (?,?,?,?,?,?)",
                        (job_id,listing.source,listing.url,source_id,timestamp,timestamp))
            self.observe_listing(key,url,listing.source,posted,fresh,status,job_id=job_id,
                date_source=date_source,confidence=confidence,duplicate=bool(previous),reference=timestamp,culled_at=culled)
            self.execute('INSERT OR IGNORE INTO listing_aliases(source,source_job_id,identity_key) VALUES (?,?,?)', (listing.source,source_id,key))
        self.last_ingest_result = dict(raw_results=1,already_known=int(bool(previous)),older_than_window=int(fresh=='STALE'),
            unknown_date=int(fresh=='UNKNOWN_DATE'),closed=int(status in {'CLOSED','REMOVED'}),
            fresh_eligible=int(active),new_listings_stored=int(created),existing_fresh_updated=int(bool(existing) and active))
        if fresh == 'UNKNOWN_DATE':
            log.info('[FRESHNESS] Skipped listing because posting date could not be verified')
        else:
            log.info('[FRESHNESS] Listing age: %.4f days — %s', (utc(timestamp)-utc(posted)).total_seconds()/86400, 'ACCEPT' if active else 'REJECT')
        if not getattr(self, '_discovery_batch', False):
            self.refresh_listing_statistics()
        return job_id, created

    @contextmanager
    def ingest_batch(self):
        previous = getattr(self, '_discovery_batch', False)
        self._discovery_batch = True
        try:
            with self.transaction():
                yield
        finally:
            self._discovery_batch = previous
            if not previous:
                self.refresh_listing_statistics()

    def application(self, app_id):
        row = self.one("""SELECT a.*,j.company,j.title,j.location,j.canonical_url,j.description,j.ats,j.posted_at,
                      j.eligibility_json,j.listing_status,j.listing_active,j.freshness_state,j.posted_at_source,j.posted_at_confidence FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?""", (app_id,))
        if not row:
            raise ValueError(f"Application {app_id} not found")
        return row

    @atomic_mutation
    def transition(self, app_id, status, reason="", **fields):
        app = self.application(app_id)
        if app["status"] in FINAL and status != app["status"]:
            raise ValueError("Cannot automatically reopen a final application")
        if status == State.SUBMITTED and not fields.get("confirmation_text"):
            raise ValueError("Submission requires positive confirmation evidence")
        allowed = {"stage", "started_at", "submitted_at", "resume_used", "resume_sha256", "confirmation_text", "confirmation_url", "retry_at", "submit_intent_at", "eligibility_override"}
        if not fields.keys() <= allowed:
            raise ValueError("Invalid application update")
        fields.update(status=str(status), updated_at=now(), failure_reason=reason)
        self.execute("UPDATE applications SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), app_id))
        self.execute("UPDATE jobs SET status=?,reason=? WHERE id=?", (str(status), reason, app["job_id"]))
        self.event(app_id, str(status), reason)
        security = {"application_state": STATE_MAP.get(str(status), "FAILED")}
        if status in {State.SUBMITTED, State.ALREADY_APPLIED} and fields.get("confirmation_text"):
            security.update(submission_confirmation_seen=1, submission_confirmation_reason=fields["confirmation_text"], retry_allowed=0)
        elif status in FINAL or status in {State.MANUAL_REVIEW, State.SUBMITTING}:
            security["retry_allowed"] = 0
        self.update_security(app_id, **security)

    def claim(self, application_id=None):
        if application_id is not None and self.automation_retired(application_id):
            return None
        target = self.setting("controlled_application_id")
        if target is not None and application_id != target:
            return None
        if application_id is None:
            self.cleanup_stale_listings()
        elif not self.one('SELECT id FROM applications WHERE id=?', (application_id,)) or not self.guard_listing(application_id):
            # Explicit runs must not perform maintenance on unrelated listings.
            return None
        with self.transaction():
            sql = """SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id
                WHERE j.listing_active=1 AND j.listing_status='ACTIVE' AND a.status IN ('QUEUED','RETRY') AND a.retry_allowed=1 AND a.submit_intent_at IS NULL AND (a.retry_at IS NULL OR a.retry_at<=?)
                AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id AND s.value NOT IN ('false','null','0'))
                """
            args = [now()]
            if application_id is not None:
                sql += " AND a.id=?"
                args.append(application_id)
            row = self.one(sql + " ORDER BY j.priority DESC,j.discovered_at,a.id LIMIT 1", args)
            if not row:
                return None
            if not self.guard_listing(row["id"]):
                return None
            previous = self.application(row["id"])
            self.transition(row["id"], State.CHECKING, started_at=previous["started_at"] or now(), stage="checking")
            self.execute("UPDATE applications SET attempts=attempts+1 WHERE id=?", (row["id"],))
            return self.application(row["id"])

    def recover(self):
        for row in self.rows("SELECT id FROM applications WHERE session_preserved=1 AND error_category!='INPUT_REQUIRED'"):
            self.notify(f"lost-session:{row['id']}:{now()}", {"application_id": row["id"], "message": "Worker restarted; the prior live form session is unavailable. Automatic resubmission remains disabled. Check employer history."})
        self.execute("UPDATE applications SET session_preserved=0 WHERE session_preserved=1")
        for row in self.rows("SELECT id,submit_intent_at FROM applications WHERE status IN ('CHECKING','APPLYING','SUBMITTING')"):
            state = State.MANUAL_REVIEW if row["submit_intent_at"] else State.RETRY
            reason = "Submission may have completed; verify employer history before retry" if row["submit_intent_at"] else "Recovered interrupted pre-submit processing"
            self.transition(row["id"], state, reason)
            if row["submit_intent_at"]:
                self.update_security(row["id"], application_state="UNKNOWN", manual_action_required=1,
                                     manual_action_reason=reason, retry_allowed=0, error_category="SUBMISSION_UNKNOWN")
            self.notify(f"recovery:{row['id']}:{now()}", {"application_id": row["id"], "message": reason})

    def retry(self, app_id):
        if self.automation_retired(app_id):
            raise ValueError("User-reported submission permanently excludes this application from automation")
        if not self.guard_listing(app_id):
            raise ValueError("Listing is no longer eligible for new processing")
        app = self.application(app_id)
        if not app["retry_allowed"]:
            raise ValueError("Automatic retry disabled. Use resume-manual with the preserved session or reconcile employer history.")
        if app["submit_intent_at"]:
            raise ValueError("Submission outcome is uncertain. Use reconcile after checking employer history.")
        if app["status"] in {"CHECKING", "APPLYING", "SUBMITTING"}:
            raise ValueError("Application is currently active")
        if self.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (app_id,)):
            raise ValueError("Answer or skip pending questions first")
        self.transition(app_id, State.RETRY, "User requested retry", retry_at=None)

    def fail(self, app_id, reason, max_retries, category=ErrorCategory.NETWORK_ERROR):
        app = self.application(app_id)
        decision = RetryPolicy().decide(category, app["attempts"], max_retries, bool(app["submit_intent_at"]))
        self.update_security(app_id, error_category=category, retry_allowed=int(decision.allowed))
        if app["submit_intent_at"]:
            self.transition(app_id, State.MANUAL_REVIEW, "Submission outcome uncertain: " + reason)
        elif decision.allowed:
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=decision.delay)).isoformat()
            self.transition(app_id, State.RETRY, reason, retry_at=retry_at)
        else:
            self.transition(app_id, State.FAILED, reason)

    def automation_retired(self, app_id):
        return bool(self.setting(f"duplicate_submission_guard:{app_id}", False))

    def submission_conflict(self, app_id):
        app = self.application(app_id)
        if self.automation_retired(app_id):
            return app
        if app["submit_intent_at"] or app["submission_confirmation_seen"]:
            return app
        for other in self.rows("""SELECT a.*,j.company,j.title,j.ats,j.canonical_url FROM applications a
            JOIN jobs j ON j.id=a.job_id WHERE a.id!=? AND
            (a.submit_intent_at IS NOT NULL OR a.submission_confirmation_seen=1 OR
             a.status IN ('SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REVIEW'))""", (app_id,)):
            if job_identity(app["canonical_url"]) == job_identity(other["canonical_url"]) or (
                normalize(app["company"]) == normalize(other["company"]) and
                normalize(app["title"]) == normalize(other["title"]) and app["ats"] == other["ats"]):
                return other
        # Imported records can outlive their original database. Consult the
        # central history API so a prior submission cannot be missed after migration.
        for other in self.history.find_by_url(app['canonical_url']):
            if other['application_id'] != str(app_id) and (
                other.get('submit_intent_at') or other.get('submission_confirmation_seen') or
                other['application_state'] in {'SUBMITTED', 'ALREADY_APPLIED', 'SUBMITTING', 'MANUAL_REQUIRED', 'UNKNOWN'}):
                return dict(other, id=other.get('id', other['application_id']),
                            status=other.get('status', other['application_state']))
        return None

    def question(self, app_id, q, reason=""):
        stamp = now()
        row = self.one("SELECT * FROM questions WHERE application_id=? AND field_key=?", (app_id, q.key))
        signature = (q.label, q.kind, json.dumps(q.options), int(q.required), q.max_length, q.scope)
        if row and (row["raw_question"], row["field_type"], row["options"], row["required"], row["max_length"], row["scope"]) == signature:
            return row
        self.execute("""INSERT INTO questions(application_id,field_key,raw_question,normalized_question,field_type,options,required,
            max_length,scope,status,reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?,?,?)
            ON CONFLICT(application_id,field_key) DO UPDATE SET raw_question=excluded.raw_question,
            normalized_question=excluded.normalized_question,field_type=excluded.field_type,options=excluded.options,
            required=excluded.required,max_length=excluded.max_length,scope=excluded.scope,status='PENDING',answer=NULL,
            answer_source=NULL,confidence=NULL,reason=excluded.reason,updated_at=excluded.updated_at""",
            (app_id, q.key, q.label, normalize(q.label), q.kind, json.dumps(q.options), q.required, q.max_length, q.scope, reason, stamp, stamp))
        return self.one("SELECT * FROM questions WHERE application_id=? AND field_key=?", (app_id, q.key))

    def save_answer(self, question_id, answer, verified=False):
        q = self.one("SELECT * FROM questions WHERE id=?", (question_id,))
        if not q:
            raise ValueError("Question not found")
        self.execute("UPDATE questions SET answer=?,answer_source=?,confidence=?,status='ANSWERED',updated_at=? WHERE id=?",
                     (json.dumps(answer.value), answer.source, answer.confidence, now(), question_id))
        if verified:
            self.execute("""INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at)
                VALUES (?,?,?,1,?,?) ON CONFLICT(normalized_question,scope) DO UPDATE SET answer=excluded.answer,
                  verified=1,source=excluded.source""", (q["normalized_question"], q["scope"], json.dumps(answer.value), answer.source, now()))
            from .field_mapping import answer_signature
            stored = self.one('SELECT id FROM known_answers WHERE normalized_question=? AND scope=?', (q['normalized_question'],q['scope']))
            self.set_setting('known_answer_signature:' + str(stored['id']),
                             answer_signature(q['raw_question'],q['field_type'],json.loads(q['options'])))

    def bind_url(self, app_id, url):
        app = self.application(app_id)
        key, url = job_identity(url), canonical_url(url)
        other = self.one("SELECT id FROM jobs WHERE (identity_key=? OR canonical_url=?) AND id!=?", (key, url, app["job_id"]))
        if other:
            self.execute("UPDATE job_sources SET job_id=? WHERE job_id=?", (other["id"], app["job_id"]))
            self.transition(app_id, State.DUPLICATE, f"Resolved to existing job {other['id']}")
            return False
        self.execute("UPDATE jobs SET canonical_url=?,identity_key=?,ats=? WHERE id=?", (url, key, ats_identity(url)[0], app["job_id"]))
        return True
