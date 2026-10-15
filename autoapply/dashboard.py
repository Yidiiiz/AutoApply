"""Loopback-only discovery dashboard and bounded semi-auto run controls."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hmac
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from urllib.parse import parse_qs, quote, urlsplit

from .database import Database
from .dashboard_ui import PAGE
from .dashboard_settings import DashboardSettings, SettingsRejected, MAX_RESUME_BYTES
from .dashboard_answers import DashboardAnswers
from . import cv, descriptions, extension_api
from .fill_batch import discover
from .freshness import utc, parse_posted, window
from . import liveness
from .models import State, now
from .runtime import ProcessLock

PROTECTED_APPLICATIONS = frozenset({3556, 4522, 6397, 4611})
NOT_ELIGIBLE_REASONS = (
    'Requires Master\u2019s / PhD', 'Requires citizenship / security clearance',
    'Too senior / years of experience', 'Graduation date mismatch',
    'Location / not remote', 'Not a student / internship mismatch', 'Other',
)
SWEEP_CAP = 100
PROBE_WORKERS = 4
REFRESH_HISTORY_LIMIT = 20
_PROTECTED_SQL = ','.join(str(i) for i in sorted(PROTECTED_APPLICATIONS))
AUTOMATION_FAILURE_CATEGORIES = frozenset({
    'ENGINEERING_FAILURE', 'FORM_NOT_READY', 'APPLICATION_ENTRY_MODE_UNRESOLVED',
    'TRANSITION_UNCONFIRMED', 'ADAPTER_CAPABILITY_FAILURE', 'FORM_INVENTORY_UNSTABLE',
    'UPLOAD_PENDING', 'UPLOAD_RACE_DETECTED', 'FORM_TERMINAL_FAILURE',
    'SUBMIT_CLICK_NOT_DELIVERED', 'BROWSER_START_FAILED',
})


def _automation_failure(row):
    reason = (row['failure_reason'] or '').lower()
    return row['status'] == 'FAILED' or (
        row['status'] == 'MANUAL_REVIEW' and (
            row['error_category'] in AUTOMATION_FAILURE_CATEGORIES or
            any(marker in reason for marker in
                ('upload readiness failed', 'error during', 'targetclosederror'))))


def _safe_url(url):
    try:
        parsed = urlsplit(url or '')
        return url if parsed.scheme in {'http', 'https'} and parsed.hostname else ''
    except ValueError:
        return ''


class _Connection(Database):
    """Independent connection to the schema already initialized by the CLI.

    Reuse Database/lifecycle helpers without repeating startup migrations or
    exporting a running worker's pending history on every HTTP read.
    TEMP guards affect this connection only, including discovery's bulk writes.
    """

    def __init__(self, path, history, *, writable=False, check_same_thread=True):
        mode = 'rw' if writable else 'ro'
        self.conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=' + mode,
                                    uri=True, timeout=5, isolation_level=None, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._history_ready = writable
        self._flushing_history = False
        self._closed = False
        self.history = history
        if not writable:
            self.conn.execute('PRAGMA query_only=ON')
            return
        app = f'{{row}}.id IN ({_PROTECTED_SQL})'
        job = f'{{row}}.id IN (SELECT job_id FROM applications WHERE id IN ({_PROTECTED_SQL}))'
        identity = (f'{{row}}.identity_key IN (SELECT j.identity_key FROM jobs j '
                    f'JOIN applications a ON a.job_id=j.id WHERE a.id IN ({_PROTECTED_SQL}))')
        guards = {'applications': app, 'jobs': job,
                  'job_sources': job.replace('{row}.id', '{row}.job_id'),
                  'listing_observations': '(' + identity + ') OR (' + job.replace('{row}.id', '{row}.job_id') + ')',
                  'listing_maintenance': identity,
                  'listing_aliases': identity,
                  'notifications': f"CAST(json_extract({{row}}.payload,'$.application_id') AS INTEGER) IN ({_PROTECTED_SQL})"}
        for table in ('events', 'questions', 'manual_requests', 'manual_commands',
                      'history_transitions', 'history_dirty'):
            guards[table] = f'{{row}}.application_id IN ({_PROTECTED_SQL})'
        for table, predicate in guards.items():
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                rows = ('OLD', 'NEW') if operation == 'UPDATE' else (('OLD',) if operation == 'DELETE' else ('NEW',))
                condition = ' OR '.join(predicate.format(row=row) for row in rows)
                self.conn.execute(f'''CREATE TEMP TRIGGER dashboard_guard_{table}_{operation}
                    BEFORE {operation} ON main.{table} WHEN {condition}
                    BEGIN SELECT RAISE(IGNORE); END''')
        # An implicit INTEGER PRIMARY KEY is not assigned until after BEFORE INSERT.
        self.conn.execute(f'''CREATE TEMP TRIGGER dashboard_reserved_id
            AFTER INSERT ON main.applications WHEN NEW.id IN ({_PROTECTED_SQL})
            BEGIN SELECT RAISE(ABORT, 'reserved application id'); END''')

    def flush_history(self, ids=()):
        """Use the existing exporter, leaving protected pending exports to its owner."""
        if not self._history_ready or self._flushing_history or self.conn.in_transaction:
            return
        self._flushing_history = True
        try:
            self._check_history_lock_order()
            for app_id in ids:
                if app_id not in PROTECTED_APPLICATIONS:
                    self.conn.execute('INSERT OR IGNORE INTO history_dirty VALUES (?)', (app_id,))
            query = f'SELECT application_id FROM history_dirty WHERE application_id NOT IN ({_PROTECTED_SQL})'
            if not self.conn.execute(query).fetchone():
                return
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                pending = [r[0] for r in self.conn.execute(query)]
                if pending:
                    self.history.sync(self, pending)
                    self.conn.execute(f'DELETE FROM history_dirty WHERE application_id NOT IN ({_PROTECTED_SQL})')
                self.conn.execute('COMMIT')
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
        finally:
            self._flushing_history = False


async def scan(config, db):
    """The scan CLI's discovery-only path, never Engine.run/process_one."""
    from .engine import Engine
    engine = Engine(config, db)
    try:
        return await engine.scan(force=True)
    finally:
        await engine.close()


class RefreshRejected(RuntimeError):
    pass


class RunRejected(RuntimeError):
    pass


class AppliedRejected(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Dashboard:
    """Application service independent of HTTP routing."""

    def __init__(self, config, db, *, liveness_minutes=30):
        if liveness_minutes == 30:
            liveness_minutes = config.data.get('dashboard', {}).get('liveness_minutes', 30)
        if not math.isfinite(liveness_minutes) or liveness_minutes < 5:
            raise ValueError('--liveness-minutes must be at least 5')
        self.config = config
        self.path = db.one('PRAGMA database_list')['file']
        self.history_root = db.history.root
        # Reuse initialized records; canonical history reads/sync share the root lock
        # and revision token, including changes made by other processes.
        self.history = db.history
        self.interval = liveness_minutes * 60
        self.token = secrets.token_hex(32)
        self._mutex = threading.Lock()
        self._cv_locks = {}
        self._ext_mutex = threading.Lock()
        self._ext_run = []
        self._priority_mutex = threading.Lock()
        self._priority_db = None
        self._priority_key = None
        self._priority_rows = []
        self._priority_at = 0
        self._priority_deadline = 0
        self._sweep_mutex = threading.Lock()
        self._stop = threading.Event()
        self._observations = {}
        self._sweep = dict(running=False, started_at=None, finished_at=None, counts={}, error=None,
                           cap=SWEEP_CAP, concurrency=PROBE_WORKERS)
        self._refreshes = {}
        self._active_refresh = None
        self._sweep_thread = self._refresh_thread = None
        self._child = None
        self._pid = None
        self.settings = DashboardSettings(config, self.connection, self._mutex, self._child_alive)
        self.answers = DashboardAnswers(config, self.connection, self.settings.mutex)

    @contextmanager
    def connection(self, *, writable=False):
        db = _Connection(self.path, self.history, writable=writable)
        try:
            yield db
        finally:
            db.close()

    def priority(self):
        # data_version is comparable only on the same connection. Serialize its
        # reads and retain it between requests; other dashboard/worker writes invalidate it.
        with self._priority_mutex:
            if self._priority_db is None:
                self._priority_db = _Connection(self.path, self.history, check_same_thread=False)
            db = self._priority_db
            db.conn.execute('BEGIN')
            try:
                key = (db.one('PRAGMA data_version')['data_version'],
                       self.config.profile_snapshot().revision, json.dumps(self.config.data, sort_keys=True),
                       self.history._cache_revision())
                # Retry deadlines and listing age can change eligibility without a DB write.
                if (key != self._priority_key or time.monotonic() - self._priority_at >= 30
                        or time.time() >= self._priority_deadline):
                    rows = []
                    for rank, app in enumerate(discover(db, self.config, details=True), 1):
                        url = app.get('canonical_url') or ''
                        rows.append(dict(rank=rank, application_id=app['id'], company=unescape(app['company']),
                                         title=unescape(app['title']), ats=app['ats'], posted_at=app['posted_at'],
                                         listing_status=app['listing_status'], updated_at=app['updated_at'],
                                         url=url if url.startswith(('https://', 'http://')) else ''))
                    self._priority_rows, self._priority_key = rows, key
                    self._priority_at = time.monotonic()
                    # Re-evaluate at the earliest retry or age boundary, even without writes.
                    reference = utc()
                    deadlines = [reference.timestamp() + 30]
                    duration = window(self.config['jobs']['max_listing_age_days'])
                    for candidate in db.rows("""SELECT a.retry_at,j.posted_at FROM applications a
                            JOIN jobs j ON j.id=a.job_id WHERE a.status IN ('QUEUED','RETRY')
                            AND j.listing_active=1 AND j.listing_status='ACTIVE'"""):
                        posted = parse_posted(candidate['posted_at'], reference)
                        dates = [posted + duration if posted else None, posted]
                        if candidate['retry_at']:
                            try:
                                dates.append(utc(candidate['retry_at']))
                            except (ValueError, TypeError):
                                pass
                        deadlines.extend(date.timestamp() for date in dates if date and date >= reference)
                    self._priority_deadline = min(deadlines)
                rows = [dict(row) for row in self._priority_rows]
            finally:
                db.conn.execute('ROLLBACK')
        with self._mutex:
            for row in rows:
                result = self._observations.get(row['application_id'], {})
                row.update(last_liveness_result=result.get('result'), last_liveness_at=result.get('checked_at'))
            sweep = dict(self._sweep, counts=dict(self._sweep['counts']))
        return dict(rows=rows, sweep=sweep)

    MANUAL_STATES = {'READY': 'Ready to submit', 'NEEDS_INPUT': 'Needs answers', 'MANUAL_REVIEW': 'Needs you',
                     'AUTH_REQUIRED': 'Account / sign-in required'}

    @staticmethod
    def _reversible(guard):
        return (isinstance(guard, dict) and guard.get('source') == 'DASHBOARD_MANUAL'
                and guard.get('permanent') is False)

    def applied(self):
        with self.connection() as db:
            rows = db.rows(f'''SELECT a.id,j.company,j.title,j.ats,j.canonical_url,a.current_url,s.value
                FROM applications a JOIN jobs j ON j.id=a.job_id
                JOIN settings s ON s.key='duplicate_submission_guard:' || a.id
                WHERE a.id NOT IN ({_PROTECTED_SQL})''')
            excluded = db.rows(f'''SELECT a.id,j.company,j.title,s.value
                FROM applications a JOIN jobs j ON j.id=a.job_id
                JOIN settings s ON s.key='user_not_eligible:' || a.id
                WHERE a.id NOT IN ({_PROTECTED_SQL}) AND s.value NOT IN ('false','null','0')''')
        items = []
        for row in rows:
            guard = json.loads(row['value'])
            if not guard:
                continue
            info = guard if isinstance(guard, dict) else {}
            items.append(dict(application_id=row['id'], company=unescape(row['company']),
                              title=unescape(row['title']), ats=row['ats'],
                              url=_safe_url(row['canonical_url'] or row['current_url']),
                              marked_at=info.get('marked_at'), source=info.get('source'),
                              reversible=self._reversible(guard)))
        items.sort(key=lambda item: (item['marked_at'] or '', item['application_id']), reverse=True)
        not_eligible = []
        for row in excluded:
            mark = json.loads(row['value'])
            info = mark if isinstance(mark, dict) else {}
            not_eligible.append(dict(application_id=row['id'], company=unescape(row['company']),
                                     title=unescape(row['title']), reason=info.get('reason', ''),
                                     note=info.get('note', ''), marked_at=info.get('marked_at'),
                                     reversible=info.get('source') == 'DASHBOARD_MANUAL'))
        not_eligible.sort(key=lambda item: (item['marked_at'] or '', item['application_id']), reverse=True)
        return dict(applications=items, not_eligible=not_eligible)

    def mark_not_eligible(self, application_id, reason=None, note='', *, undo=False):
        if type(application_id) is not int or not 0 < application_id <= 2**63 - 1:
            raise AppliedRejected(400, 'application_id must be a positive integer')
        if application_id in PROTECTED_APPLICATIONS:
            raise AppliedRejected(403, 'protected application cannot be changed')
        if not undo:
            if not isinstance(reason, str) or reason not in NOT_ELIGIBLE_REASONS:
                raise AppliedRejected(400, 'choose a valid reason')
            if not isinstance(note, str) or len(note) > 500:
                raise AppliedRejected(400, 'note must be text of at most 500 characters')
            note = note.strip()
            if reason == 'Other' and not note:
                raise AppliedRejected(400, 'Other requires a note')
        key = 'user_not_eligible:' + str(application_id)
        with self.connection(writable=True) as db:
            with db.transaction():
                if not db.one('SELECT id FROM applications WHERE id=?', (application_id,)):
                    raise AppliedRejected(404, 'application not found')
                mark = db.setting(key, False)
                if undo:
                    if not isinstance(mark, dict) or mark.get('source') != 'DASHBOARD_MANUAL':
                        raise AppliedRejected(409, 'only dashboard manual marks can be restored')
                    db.set_setting(key, False)
                    db.event(application_id, 'USER_UNMARKED_NOT_ELIGIBLE', 'DASHBOARD_MANUAL')
                    result = dict(application_id=application_id)
                else:
                    if mark:
                        raise AppliedRejected(409, 'application already marked not eligible')
                    marked_at = now()
                    db.set_setting(key, dict(reason=reason, note=note, marked_at=marked_at, source='DASHBOARD_MANUAL'))
                    db.event(application_id, 'USER_MARKED_NOT_ELIGIBLE', 'DASHBOARD_MANUAL')
                    result = dict(application_id=application_id, marked_at=marked_at)
        with self._priority_mutex:
            self._priority_key = None
        return result

    def _cv_job(self, application_id):
        if type(application_id) is not int or not 0 < application_id <= 2**63 - 1:
            raise AppliedRejected(400, 'application_id must be a positive integer')
        if application_id in PROTECTED_APPLICATIONS:
            raise AppliedRejected(403, 'protected application cannot be changed')
        with self.connection() as db:
            job = db.one('''SELECT j.id AS job_id,j.canonical_url,j.company,j.title,j.description,j.location
                FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?''', (application_id,))
        if not job:
            raise AppliedRejected(404, 'application not found')
        return dict(job)

    def _writing_settings(self, label='AI'):
        settings = next((p for p in self.config['ai']['providers'] if p.get('type') == 'codex_cli'), None)
        if not self.config['ai']['enabled'] or settings is None:
            raise AppliedRejected(409, label + ' writing needs an AI provider in Settings')
        return dict(settings)

    def run_writing(self, request, label='AI'):
        from .codex_writer import CodexWritingProvider
        from .ai import ProviderUnavailable
        settings = self._writing_settings(label)
        provider = None
        try:
            provider = CodexWritingProvider(settings, None, None, self.config['ai']['allow_paid'])
            return asyncio.run(provider.generate_response(request))
        except ProviderUnavailable as exc:
            status = 400 if exc.category == 'MALFORMED_RESPONSE' else 409
            raise AppliedRejected(status, label + ' writing unavailable: ' + str(exc)) from None
        except ValueError as exc:
            status = 409 if provider is None else 400
            message = label + ' writing unavailable: ' + str(exc) if provider is None else str(exc)
            raise AppliedRejected(status, message) from None
        except Exception as exc:
            raise AppliedRejected(409, label + ' writing unavailable: ' + type(exc).__name__) from None

    def writing_job(self, job):
        """Enrich matched jobs without holding a connection during the fetch."""
        if 'job_id' in job and len(job.get('description') or '') < 200:
            description = descriptions.fetch_description(job['canonical_url'])
            if description:
                with self.connection(writable=True) as db:
                    descriptions.store(db, job['job_id'], description)
                job['description'] = description
        return job

    def generate_cv(self, application_id, answers=None):
        return self.generate_cover_letter(self._cv_job(application_id), answers, application_id=application_id)

    def generate_cover_letter(self, job, answers=None, *, application_id=None, extension=False):
        if answers is not None and (not isinstance(answers, list) or len(answers) > 20 or any(
                not isinstance(item, dict) or set(item) != {'question', 'answer'} or
                any(not isinstance(item[k], str) for k in ('question', 'answer')) or not item['question'].strip()
                for item in answers)):
            raise AppliedRejected(400, 'Cover letter answers must contain a non-empty question and an answer string')
        label = 'AI' if extension else 'Cover letter'
        self._writing_settings(label)
        with self._mutex:
            lock = (self._cv_locks.setdefault(application_id, threading.Lock())
                    if application_id is not None else threading.Lock())
        if not lock.acquire(blocking=False):
            raise AppliedRejected(409, 'Cover letter generation already running for this application')
        try:
            self.writing_job(job)
            with self.answers.mutex:
                profile = self.config.profile
            folder = self.config.private / 'documents/cv'
            stem = str(application_id) + '-' + cv.company_slug(job['company'])
            answers_path = folder / (stem + '.answers.json')
            saved = (json.loads(answers_path.read_text(encoding='utf-8'))
                     if application_id is not None and answers_path.exists() else [])
            combined = {item['question']: item['answer'] for item in saved}
            combined.update({item['question'].strip(): item['answer'].strip() for item in answers or []})
            answers = [dict(question=q, answer=a) for q, a in combined.items()]
            payload = cv.cover_letter_request(profile, job, self.config.private / 'cover_letter_samples', answers)
            result = self.run_writing(payload, label)
            try:
                kind, items = cv.validate_letter_result(result)
            except ValueError as exc:
                raise AppliedRejected(400, str(exc)) from None
            if application_id is not None:
                folder.mkdir(parents=True, exist_ok=True)
            if answers and application_id is not None:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as output:
                    temporary_answers = Path(output.name)
                    json.dump(answers, output, ensure_ascii=False)
                try:
                    os.replace(temporary_answers, answers_path)
                finally:
                    temporary_answers.unlink(missing_ok=True)
            if kind == 'questions':
                return dict(questions=items)
            html = cv.build_cover_letter(profile, job, items, salutation=result.get('salutation'))
            warnings = []
            # Rendering owns only this request thread; no DB or dashboard lock.
            with tempfile.TemporaryDirectory(dir=folder if application_id is not None else None,
                                             prefix='render-') as temporary:
                pdf = Path(temporary) / 'cv.pdf'
                try:
                    cv.render_pdf(html, pdf)
                    content = pdf.read_bytes()
                    if not content.startswith(b'%PDF-'):
                        raise ValueError('invalid PDF')
                except Exception:
                    if extension:
                        raise AppliedRejected(409, 'Cover letter PDF rendering failed; please retry') from None
                    warnings.append('Cover letter PDF rendering failed; opened the print-ready HTML cover letter instead')
                    pdf = None
                response = dict(filename=cv.profile_name(profile) + ' - Cover Letter - ' +
                                unescape(cv.text(job.get('company'))) + '.pdf', mime='application/pdf',
                                base64=base64.b64encode(content).decode('ascii')) if extension else None
                if application_id is None:
                    return response
                draft = Path(temporary) / 'cv.html'
                draft.write_text(html, encoding='utf-8')
                if pdf is not None:
                    os.replace(pdf, folder / (stem + '.pdf'))
                else:
                    (folder / (stem + '.pdf')).unlink(missing_ok=True)
                os.replace(draft, folder / (stem + '.html'))
            return response if extension else dict(file='/api/cv/' + str(application_id), warnings=warnings)
        finally:
            lock.release()

    def cv_file(self, application_id):
        job = self._cv_job(application_id)
        folder = self.config.private / 'documents/cv'
        latest = max(folder.glob(str(application_id) + '-*.html'),
                     key=lambda path: path.stat().st_mtime_ns, default=None)
        if latest is None:
            raise AppliedRejected(404, 'generate a cover letter first')
        pdf = latest.with_suffix('.pdf')
        path = pdf if pdf.is_file() else latest
        with self.answers.mutex:
            name = cv.profile_name(self.config.profile)
        filename = (name + ' - Cover Letter - ' + job['company']) + path.suffix
        return path.read_bytes(), filename, path.suffix

    def mark_applied(self, application_id, *, undo=False, evidence=None):
        if type(application_id) is not int or not 0 < application_id <= 2**63 - 1:
            raise AppliedRejected(400, 'application_id must be a positive integer')
        if application_id in PROTECTED_APPLICATIONS:
            raise AppliedRejected(403, 'protected application cannot be changed')
        key = 'duplicate_submission_guard:' + str(application_id)
        # BEGIN IMMEDIATE serializes competing check-offs and Discord guard writes.
        # No worker lock, status mutation or worker lifecycle action is involved.
        with self.connection(writable=True) as db:
            with db.transaction():
                if not db.one('SELECT id FROM applications WHERE id=?', (application_id,)):
                    raise AppliedRejected(404, 'application not found')
                guard = db.setting(key, False)
                if undo:
                    if not self._reversible(guard):
                        raise AppliedRejected(409, 'only dashboard manual marks can be put back')
                    db.set_setting(key, False)
                    db.event(application_id, 'USER_UNMARKED_APPLIED', 'DASHBOARD_MANUAL')
                    return dict(application_id=application_id)
                if guard and evidence is not None and self._reversible(guard):
                    return dict(application_id=application_id, marked_at=guard.get('marked_at'))
                if guard:
                    raise AppliedRejected(409, 'application already marked or guarded')
                marked_at = now()
                db.set_setting(key, dict(permanent=False, source='DASHBOARD_MANUAL', marked_at=marked_at))
                db.event(application_id, 'USER_MARKED_APPLIED', 'DASHBOARD_MANUAL')
                if evidence is not None:
                    db.event(application_id, 'EXT_SUBMISSION_CONFIRMED', evidence)
        return dict(application_id=application_id, marked_at=marked_at)

    def failed(self, days=14):
        """Recent automation failures, without changing retries or application holds."""
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connection() as db:
            rows = db.rows(f'''SELECT a.id,a.status,a.error_category,a.failure_reason,a.updated_at,
                    a.current_url,a.attempts,a.max_retries,a.delivery_state,a.submit_intent_at,
                    j.company,j.title,j.ats,j.canonical_url
                FROM applications a JOIN jobs j ON j.id=a.job_id
                WHERE a.status IN ('FAILED','MANUAL_REVIEW')
                  AND a.submission_confirmation_seen=0
                  AND j.listing_active=1 AND j.listing_status='ACTIVE'
                  AND a.updated_at>=? AND a.id NOT IN ({_PROTECTED_SQL})
                  AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id
                                  AND s.value NOT IN ('false','null','0'))
                  AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='user_not_eligible:' || a.id
                                  AND s.value NOT IN ('false','null','0'))
                ORDER BY a.updated_at DESC,a.id DESC''', (since,))
            items = [dict(application_id=r['id'], company=unescape(r['company']), title=unescape(r['title']), ats=r['ats'],
                          error_category=r['error_category'], reason=(r['failure_reason'] or '')[:200],
                          attempts=r['attempts'], max_retries=r['max_retries'],
                          retry_possible=(r['attempts'] < r['max_retries'] + 1
                                          and r['delivery_state'] == 'NOT_STARTED'
                                          and r['submit_intent_at'] is None),
                          url=_safe_url(r['canonical_url'] or r['current_url']), updated_at=r['updated_at'])
                     for r in rows if _automation_failure(r)]
        return dict(applications=items)

    def manual(self, days=14):
        """Applications that need the user, with the employer link to finish them by hand."""
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connection() as db:
            rows = db.rows(f'''SELECT a.id,a.status,a.error_category,a.failure_reason,a.updated_at,a.current_url,
                    j.company,j.title,j.ats,j.canonical_url
                FROM applications a JOIN jobs j ON j.id=a.job_id
                WHERE a.status IN ('READY','NEEDS_INPUT','MANUAL_REVIEW','AUTH_REQUIRED')
                  AND a.submit_intent_at IS NULL AND a.submission_confirmation_seen=0
                  AND j.listing_active=1 AND j.listing_status='ACTIVE'
                  AND a.updated_at>=? AND a.id NOT IN ({_PROTECTED_SQL})
                  AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id
                                  AND s.value NOT IN ('false','null','0'))
                  AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='user_not_eligible:' || a.id
                                  AND s.value NOT IN ('false','null','0'))
                ORDER BY (a.status='READY') DESC, a.updated_at DESC''', (since,))
            items = []
            for r in rows:
                if _automation_failure(r):
                    continue
                pending = [q['raw_question'] for q in db.rows(
                    "SELECT raw_question FROM questions WHERE application_id=? AND status='PENDING' ORDER BY id", (r['id'],))]
                url = r['canonical_url'] or r['current_url'] or ''
                category = r['error_category'] or ''
                state = ('Account / sign-in required' if category == 'AUTH_REQUIRED' else
                         'Security check' if category in {'CAPTCHA', 'IDENTITY_VERIFICATION', 'UNKNOWN_SECURITY_FAILURE'} else
                         self.MANUAL_STATES.get(r['status'], r['status']))
                items.append(dict(application_id=r['id'], company=unescape(r['company']), title=unescape(r['title']), ats=r['ats'],
                                  state=state, state_code=category if category in {'AUTH_REQUIRED', 'CAPTCHA',
                                      'IDENTITY_VERIFICATION', 'UNKNOWN_SECURITY_FAILURE'} else r['status'],
                                  reason=(r['failure_reason'] or '')[:200], updated_at=r['updated_at'],
                                  url=_safe_url(url),
                                  pending_questions=[p[:160] for p in pending[:8]], pending_count=len(pending)))
        return dict(applications=items)

    def _child_alive(self):
        return self._child is not None and self._child.poll() is None

    def _worker_locked(self):
        path = self.config.private / 'worker.lock'
        if not path.exists():
            return False
        try:
            with ProcessLock(path):
                pass
        except RuntimeError:
            return True
        return False

    def run_worker(self, count):
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError('count must be an integer between 1 and 10')
        with self._mutex:
            if self._stop.is_set():
                raise RunRejected('dashboard stopping')
            if self._child_alive():
                raise RunRejected('dashboard worker already running')
            # Probe rather than pass the lock: the child owns it through CLI cleanup.
            try:
                with ProcessLock(self.config.private / 'worker.lock'):
                    pass
            except RuntimeError:
                raise RunRejected('worker.lock is held; another worker or discovery is running') from None
            logs = self.config.private / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            options = dict(stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                           cwd=str(Path(__file__).resolve().parents[1]), close_fds=True)
            if os.name == 'nt':
                options['creationflags'] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                            | subprocess.CREATE_NO_WINDOW)
            else:
                options['start_new_session'] = True
            with self.connection(writable=True) as db:
                db.set_setting('stop_requested', False)
                db.set_setting('paused', False)
            with (logs / ('semi-auto-' + secrets.token_hex(8) + '.log')).open('ab') as output:
                self._child = subprocess.Popen(
                    [sys.executable, '-m', 'autoapply', '--root', str(self.config.root),
                     'semi-auto', '--limit', str(count)], stdout=output, **options)
            self._pid = self._child.pid
            return dict(pid=self._pid)

    def set_control(self, action):
        with self._mutex:
            with self.connection(writable=True) as db:
                if action in {'pause', 'resume'}:
                    db.set_setting('paused', action == 'pause')
                elif action == 'stop':
                    db.set_setting('stop_requested', True)
                else:
                    raise ValueError('unknown run control')
        return self.status()

    def status(self):
        with self._mutex:
            child_alive = self._child_alive()
            locked = self._worker_locked()
            running = child_alive or (locked and self._active_refresh is None)
            pid = self._pid if child_alive or not running else None
        with self.connection() as db:
            paused = bool(db.setting('paused', False))
            stopping = running and bool(db.setting('stop_requested', False))
        report, error = {}, None
        try:
            report = json.loads((self.config.private / 'semi-auto-result.json').read_text(encoding='utf-8'))
            if not isinstance(report, dict) or not isinstance(report.get('applications', []), list):
                raise ValueError('invalid report')
            applications = []
            for app in report.get('applications', []):
                if not isinstance(app, dict):
                    raise ValueError('invalid application summary')
                # Checkpoints can contain saved answers; expose only summary fields.
                checkpoint = app.get('checkpoint') or {}
                if not isinstance(checkpoint, dict):
                    raise ValueError('invalid checkpoint summary')
                applications.append({key: app.get(key) for key in
                                     ('application_id', 'lifecycle', 'application_state', 'readiness', 'security')}
                                    | dict(verified_fields=checkpoint.get('committed_field_count', 0)))
        except FileNotFoundError:
            applications = []
        except (OSError, ValueError, TypeError):
            report, applications, error = {}, [], 'Batch report unavailable or invalid'
        with self.connection() as db:
            applications = [app for app in applications
                            if type(app['application_id']) is not int
                            or not db.automation_retired(app['application_id'])]
            for app in applications:
                if type(app['application_id']) is int:
                    job = db.one('''SELECT j.company,j.title,j.ats,j.canonical_url FROM applications a
                        JOIN jobs j ON j.id=a.job_id WHERE a.id=?''', (app['application_id'],))
                    if job:
                        app.update(company=unescape(job['company']), title=unescape(job['title']),
                                   ats=job['ats'], url=_safe_url(job['canonical_url']))
        return dict(running=running, paused=paused, stopping=stopping, pid=pid,
                    worker_locked=locked, phase=report.get('phase'), batch_id=report.get('batch_id'),
                    updated_at=report.get('updated_at'), applications=applications, report_error=error)

    def _retire(self, db, candidate, result):
        app_id = candidate['id']
        if app_id in PROTECTED_APPLICATIONS:
            return False
        with db.lifecycle.atomic():
            app = db.application(app_id)
            # A worker can claim or hold the application while HTTP probes run.
            # Never overwrite that work, its security holds or submission evidence.
            if (app['status'] not in {'QUEUED', 'RETRY'} or not app['retry_allowed']
                    or app['manual_action_required'] or app['session_preserved']
                    or app['security_state'] not in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
                    or app['submit_intent_at'] or app['submission_confirmation_seen']
                    or not app['listing_active'] or app['listing_status'] != 'ACTIVE'
                    or app['job_id'] != candidate['job_id'] or app['canonical_url'] != candidate['canonical_url']
                    or db.automation_retired(app_id) or db.setting('controlled_application_id') is not None):
                return False
            db.event(app_id, 'LISTING_LIVENESS_PROBE', result)
            db.mark_listing_closed(app['job_id'], result)
            db.lifecycle.transition(app_id, State.CLOSED, 'LISTING_CLOSED: Dashboard probe: listing ' + result.lower())
        return True

    def sweep(self):
        """Probe a fair capped slice of the eligible queue, with no worker lock."""
        if not self._sweep_mutex.acquire(blocking=False):
            return
        counts = dict(checked=0, OPEN=0, UNKNOWN=0, CLOSED=0, REMOVED=0, retired=0, skipped=0, described=0)
        error = None
        with self._mutex:
            self._sweep.update(running=True, started_at=now(), error=None)
        try:
            with self.connection() as db:
                candidates = [db.application(i) for i in discover(db, self.config)
                              if i not in PROTECTED_APPLICATIONS]
            with self._mutex:
                candidates.sort(key=lambda a: (len(a['description'] or '') >= 200,
                                               self._observations.get(a['id'], {}).get('checked_at', '')))
            candidates = candidates[:SWEEP_CAP]
            def probe(app):
                try:
                    result = liveness.listing_state(app['canonical_url'])
                except Exception:
                    result = 'UNKNOWN'
                text = descriptions.fetch_description(app['canonical_url']) if len(app['description'] or '') < 200 else ''
                return result, text
            updates = []
            with ThreadPoolExecutor(max_workers=PROBE_WORKERS, thread_name_prefix='listing-probe') as pool:
                futures = {pool.submit(probe, app): app for app in candidates
                           if not self._stop.is_set()}
                for future in as_completed(futures):
                    app = futures[future]
                    try:
                        result, text = future.result()
                    except Exception:
                        result, text = 'UNKNOWN', ''
                    if result not in {'OPEN', 'UNKNOWN', 'CLOSED', 'REMOVED'}:
                        result = 'UNKNOWN'
                    counts['checked'] += 1
                    counts[result] += 1
                    with self._mutex:
                        self._observations[app['id']] = dict(result=result, checked_at=now())
                    if text or result in {'CLOSED', 'REMOVED'}:
                        updates.append((app, result, text))
            if updates:
                with self.connection(writable=True) as db:
                    with db.transaction():
                        for app, result, text in updates:
                            if text:
                                descriptions.store(db, app['job_id'], text)
                                counts['described'] += 1
                            if result in {'CLOSED', 'REMOVED'} and not self._stop.is_set():
                                retired = self._retire(db, app, result)
                                counts['retired' if retired else 'skipped'] += 1
        except Exception as exc:
            error = type(exc).__name__
        finally:
            with self._mutex:
                self._sweep.update(running=False, finished_at=now(), counts=counts, error=error)
            self._sweep_mutex.release()

    def start(self):
        if self._sweep_thread is not None:
            return
        def periodic():
            while not self._stop.is_set():
                self.sweep()
                if self._stop.wait(self.interval):
                    break
        self._sweep_thread = threading.Thread(target=periodic, name='dashboard-liveness', daemon=True)
        self._sweep_thread.start()

    def start_refresh(self):
        with self._mutex:
            if self._stop.is_set():
                raise RefreshRejected('dashboard stopping')
            if self._active_refresh is not None:
                raise RefreshRejected('refresh already running')
            if self._child_alive():
                raise RefreshRejected('worker running')
            worker_lock = ProcessLock(self.config.private / 'worker.lock')
            try:
                worker_lock.__enter__()  # Nonblocking; never wait for a worker.
            except RuntimeError:
                raise RefreshRejected('worker running') from None
            job_id = secrets.token_hex(16)
            self._active_refresh = job_id
            self._refreshes[job_id] = dict(id=job_id, status='queued', created_at=now(),
                                           started_at=None, finished_at=None, result=None, error=None)
            while len(self._refreshes) > REFRESH_HISTORY_LIMIT:
                del self._refreshes[next(iter(self._refreshes))]
            self._refresh_thread = threading.Thread(target=self._run_refresh, args=(job_id, worker_lock),
                                                     name='dashboard-discovery', daemon=True)
            try:
                self._refresh_thread.start()
            except BaseException:
                worker_lock.__exit__()
                self._active_refresh = None
                del self._refreshes[job_id]
                raise
            return job_id

    def _run_refresh(self, job_id, worker_lock):
        try:
            with self._mutex:
                self._refreshes[job_id].update(status='running', started_at=now())
            with self.connection(writable=True) as db:
                result = asyncio.run(scan(self.config, db))
            with self._mutex:
                self._refreshes[job_id].update(status='succeeded', result=result)
        except Exception as exc:
            with self._mutex:
                self._refreshes[job_id].update(status='failed', error='Discovery failed: ' + type(exc).__name__)
        finally:
            worker_lock.__exit__()
            with self._mutex:
                self._refreshes[job_id]['finished_at'] = now()
                self._active_refresh = None

    def refresh(self, job_id):
        with self._mutex:
            job = self._refreshes.get(job_id)
            return dict(job) if job else None

    def close(self):
        self._stop.set()
        for thread in (self._sweep_thread, self._refresh_thread):
            if thread is not None:
                thread.join(timeout=5)
        with self._priority_mutex:
            if self._priority_db is not None:
                self._priority_db.close()
                self._priority_db = None


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, dashboard, port=8765):
        self.dashboard = dashboard
        self.on_serving = None
        super().__init__(('127.0.0.1', port), DashboardHandler)

    def service_actions(self):
        if self.on_serving is not None:
            callback, self.on_serving = self.on_serving, None
            threading.Thread(target=callback, name='dashboard-startup', daemon=True).start()


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log page tokens or application data.

    def _unexpected_error(self, exc):
        busy = isinstance(exc, sqlite3.OperationalError) and any(
            marker in str(exc).lower() for marker in ('locked', 'busy'))
        message = 'Dashboard is busy, retry shortly' if busy else f'Dashboard request failed: {type(exc).__name__}'
        self._reply(503, dict(error=message))

    def _reply(self, status, data, *, html=False):
        body = data.encode('utf-8') if html else json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if getattr(self, '_extension_origin', None):
            self.send_header('Access-Control-Allow-Origin', self._extension_origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-AutoApply-Key')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        token = self.server.dashboard.token
        self.send_header('Content-Security-Policy', "default-src 'none'; img-src data:; connect-src 'self'; "
                         f"script-src 'nonce-{token}'; style-src 'nonce-{token}'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        hosts = self.headers.get_all('Host', [])
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if port == 80:
            allowed.update({'127.0.0.1', 'localhost'})
        if len(hosts) != 1 or hosts[0] not in allowed:
            self._reply(403, dict(error='non-local Host rejected'))
            return False
        return True

    def do_GET(self):
        if not self._local():
            return
        if self.path.startswith('/ext/'):
            self._extension_request()
            return
        dashboard = self.server.dashboard
        try:
            if self.path == '/':
                initial = json.dumps(dict(rows=[], sweep={}))
                page = PAGE.replace('__TOKEN__', dashboard.token).replace('__INITIAL__', initial)
                fields = dashboard.settings.get()['sections']
                batch = next(f['value'] for s in fields for f in s['fields']
                             if f['path'] == 'dashboard.default_batch_size')
                batch = batch if type(batch) is int and 1 <= batch <= 10 else 5
                page = page.replace('__BATCH_SIZE__', str(batch))
                self._reply(200, page, html=True)
            elif self.path == '/api/settings':
                self._reply(200, dashboard.settings.get())
            elif self.path == '/api/ext-run':
                self._reply(200, extension_api.run_status(dashboard, {}))
            elif self.path == '/api/answers':
                self._reply(200, dashboard.answers.get())
            elif self.path == '/api/answers/saved':
                self._reply(200, dashboard.answers.saved())
            elif self.path == '/api/answers/preview':
                self._reply(200, dashboard.answers.preview())
            elif self.path == '/api/resume':
                self._reply(200, dashboard.settings.resume())
            elif self.path == '/api/resume/file':
                body = dashboard.settings.resume_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Disposition', 'inline; filename="resume.pdf"')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
                self.end_headers()
                self.wfile.write(body)
            elif urlsplit(self.path).path.startswith('/api/cv/'):
                tokens = self.headers.get_all('X-Dashboard-Token', [])
                tokens += parse_qs(urlsplit(self.path).query).get('token', [])
                if (len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), dashboard.token.encode('ascii'))
                        or self.headers.get('Sec-Fetch-Site') not in {None, 'none', 'same-origin'}):
                    self._reply(403, dict(error='dashboard token required'))
                    return
                value = urlsplit(self.path).path[len('/api/cv/'):]
                if not value.isascii() or not value.isdigit() or len(value) > 19:
                    raise AppliedRejected(400, 'application_id must be a positive integer')
                body, filename, suffix = dashboard.cv_file(int(value))
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf' if suffix == '.pdf' else 'text/html; charset=utf-8')
                self.send_header('Content-Disposition', "inline; filename*=UTF-8''" + quote(filename, safe=''))
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; sandbox")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == '/api/priority':
                self._reply(200, dashboard.priority())
            elif self.path == '/api/manual':
                self._reply(200, dashboard.manual())
            elif self.path == '/api/failed':
                self._reply(200, dashboard.failed())
            elif self.path == '/api/applied':
                self._reply(200, dashboard.applied())
            elif self.path == '/api/status':
                self._reply(200, dashboard.status())
            elif self.path.startswith('/api/refresh/'):
                job = dashboard.refresh(self.path[len('/api/refresh/'):])
                self._reply(200 if job else 404, job or dict(error='refresh job not found'))
            else:
                self._reply(404, dict(error='endpoint not found'))
        except AppliedRejected as exc:
            self._reply(exc.status, dict(error=str(exc)))
        except SettingsRejected as exc:
            self._reply(exc.status, dict(error=str(exc), errors=exc.errors))
        except Exception as exc:
            self._unexpected_error(exc)

    def do_PUT(self):
        self._answer_write()

    def do_DELETE(self):
        self._answer_write(delete=True)

    def _answer_write(self, *, delete=False):
        if not self._local():
            return
        dashboard = self.server.dashboard
        origins = self.headers.get_all('Origin', [])
        tokens = self.headers.get_all('X-Dashboard-Token', [])
        if (len(origins) != 1 or origins[0] != 'http://' + self.headers['Host']
                or self.headers.get('Sec-Fetch-Site') not in {None, 'none', 'same-origin'}
                or len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), dashboard.token.encode('ascii'))):
            self._reply(403, dict(error='same-origin dashboard token required'))
            return
        is_saved = self.path.startswith('/api/answers/saved/')
        is_category = self.path == '/api/answers/categories'
        if not is_saved and (self.path not in {'/api/answers', '/api/answers/categories'} or delete):
            self._reply(404, dict(error='endpoint not found'))
            return
        try:
            body = {}
            if not delete:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or self.headers.get('Transfer-Encoding')
                        or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 1024 * 1024):
                    raise SettingsRejected('A JSON body of at most 1 MB is required')
                body = json.loads(self.rfile.read(int(lengths[0])))
                if not isinstance(body, dict):
                    raise SettingsRejected('A JSON object is required')
            if is_category:
                result = dashboard.answers.categories(body.get('changes'))
            elif is_saved:
                from urllib.parse import unquote
                identifier = unquote(self.path[len('/api/answers/saved/'):])
                result = (dashboard.answers.link_profile(identifier) if body.get('link_profile') and not delete
                          else dashboard.answers.edit_saved(identifier, body.get('answer'), delete=delete))
            else:
                result = dashboard.answers.save(body.get('changes'))
            self._reply(200, result)
        except SettingsRejected as exc:
            self._reply(exc.status, dict(error=str(exc), errors=exc.errors))
        except (ValueError, UnicodeError) as exc:
            self._reply(400, dict(error=str(exc)))
        except Exception as exc:
            self._unexpected_error(exc)

    def do_OPTIONS(self):
        if not self._local():
            return
        if not self.path.startswith('/ext/') or not self._extension_cors():
            self._reply(403, dict(error='extension origin required'))
            return
        if self.headers.get('Access-Control-Request-Method') not in {'GET', 'POST'}:
            self._reply(403, dict(error='extension method rejected'))
            return
        self._reply(200, dict(ok=True))

    def _extension_cors(self):
        origins = self.headers.get_all('Origin', [])
        if len(origins) == 1 and origins[0].startswith('chrome-extension://'):
            self._extension_origin = origins[0]
            return True
        return False

    def _extension_request(self):
        dashboard = self.server.dashboard
        if self.headers.get_all('Origin', []) and not self._extension_cors():
            self._reply(403, dict(error='extension origin required'))
            return
        try:
            keys = self.headers.get_all('X-AutoApply-Key', [])
            if (len(keys) != 1 or not hmac.compare_digest(keys[0].encode('utf-8'),
                                                         dashboard.settings.extension_key().encode('ascii'))):
                self._reply(403, dict(error='extension key required'))
                return
            payload = {}
            if self.command == 'POST':
                lengths = self.headers.get_all('Content-Length', [])
                writing = self.path in {'/ext/write', '/ext/chat', '/ext/cover-letter'}
                maximum = 64 * 1024 if writing else 1024 * 1024
                if (len(lengths) != 1 or self.headers.get('Transfer-Encoding') or not lengths[0].isdigit()
                        or not 0 < int(lengths[0]) <= maximum):
                    raise SettingsRejected('A JSON body of at most ' + ('64 KB' if writing else '1 MB') + ' is required')
                payload = json.loads(self.rfile.read(int(lengths[0])))
                if not isinstance(payload, dict):
                    raise SettingsRejected('A JSON object is required')
            self._reply(200, extension_api.dispatch(dashboard, payload, method=self.command, path=self.path))
        except (SettingsRejected, AppliedRejected) as exc:
            self._reply(exc.status, dict(error=str(exc)))
        except (ValueError, UnicodeError):
            self._reply(400, dict(error='Invalid JSON request'))
        except Exception as exc:
            self._unexpected_error(exc)

    def do_POST(self):
        if not self._local():
            return
        if self.path.startswith('/ext/'):
            self._extension_request()
            return
        dashboard = self.server.dashboard
        origins = self.headers.get_all('Origin', [])
        tokens = self.headers.get_all('X-Dashboard-Token', [])
        if (len(origins) != 1 or origins[0] != 'http://' + self.headers['Host']
                or self.headers.get('Sec-Fetch-Site') not in {None, 'none', 'same-origin'}
                or len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), dashboard.token.encode('ascii'))):
            self._reply(403, dict(error='same-origin dashboard token required'))
            return
        if self.path not in {'/api/refresh', '/api/run', '/api/pause', '/api/resume', '/api/stop',
                             '/api/applied', '/api/unapplied', '/api/not-eligible', '/api/eligible', '/api/settings', '/api/cv',
                             '/api/extension-key/regenerate', '/api/ext-run', '/api/ext-run/cancel'}:
            self._reply(404, dict(error='endpoint not found'))
            return
        try:
            if self.path == '/api/extension-key/regenerate':
                self._reply(200, dict(extension_key=dashboard.settings.extension_key(regenerate=True)))
            elif self.path == '/api/ext-run/cancel':
                self._reply(200, extension_api.run_cancel(dashboard, {}))
            elif self.path == '/api/resume' and (self.headers.get('Content-Type', '').split(';')[0].strip().lower() == 'application/pdf'
                                             or (self.headers.get('Content-Length', '0') != '0'
                                                 and self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json')):
                if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/pdf':
                    raise SettingsRejected('Use application/pdf for resume uploads')
                lengths = self.headers.get_all('Content-Length', [])
                try:
                    if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                        raise ValueError
                    length = int(lengths[0])
                    if not 5 <= length <= MAX_RESUME_BYTES:
                        raise ValueError
                except ValueError:
                    raise SettingsRejected('A valid Content-Length is required (maximum 10 MB)') from None
                body = self.rfile.read(length)
                if len(body) != length:
                    raise SettingsRejected('Incomplete PDF body')
                self._reply(200, dashboard.settings.replace_resume(body))
            elif self.path == '/api/refresh':
                self._reply(202, dict(id=dashboard.start_refresh()))
            elif self.path in {'/api/run', '/api/applied', '/api/unapplied', '/api/not-eligible', '/api/eligible', '/api/settings', '/api/cv', '/api/ext-run'} or (
                    self.path == '/api/resume' and self.headers.get('Content-Length', '0') != '0'):
                try:
                    lengths = self.headers.get_all('Content-Length', [])
                    if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('a JSON object is required')
                    length = int(lengths[0])
                    limit = 16384 if self.path in {'/api/settings', '/api/cv', '/api/ext-run'} else 8192 if self.path == '/api/not-eligible' else 1024
                    if not 0 < length <= limit:
                        raise ValueError(f'a JSON object is required (maximum body size {limit} bytes)')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('a JSON object is required')
                    if self.path == '/api/run':
                        result = dashboard.run_worker(body.get('count'))
                    elif self.path == '/api/ext-run':
                        result = extension_api.run_start(dashboard, body)
                    elif self.path == '/api/settings':
                        result = dashboard.settings.save(body.get('changes'))
                    elif self.path == '/api/cv':
                        result = dashboard.generate_cv(body.get('application_id'), body.get('answers'))
                    elif self.path in {'/api/not-eligible', '/api/eligible'}:
                        result = dashboard.mark_not_eligible(body.get('application_id'), body.get('reason'),
                                                             body.get('note', ''), undo=self.path == '/api/eligible')
                    elif self.path == '/api/resume':
                        if body:
                            raise ValueError('Use application/pdf for resume uploads')
                        result = dashboard.set_control('resume')
                    else:
                        result = dashboard.mark_applied(body.get('application_id'), undo=self.path == '/api/unapplied')
                except SettingsRejected:
                    raise
                except (ValueError, UnicodeError) as exc:
                    self._reply(400, dict(error=str(exc)))
                    return
                self._reply(202 if self.path == '/api/run' else 200, result)
            else:
                self._reply(200, dashboard.set_control(self.path[len('/api/'):]))
        except SettingsRejected as exc:
            self._reply(exc.status, dict(error=str(exc), errors=exc.errors))
        except AppliedRejected as exc:
            self._reply(exc.status, dict(error=str(exc)))
        except (RefreshRejected, RunRejected) as exc:
            self._reply(409, dict(error=str(exc)))
        except Exception as exc:
            self._unexpected_error(exc)


def serve(config, db, *, port=8765, liveness_minutes=30, no_browser=False):
    if not 0 <= port <= 65535:
        raise ValueError('--port must be between 0 and 65535')
    dashboard = Dashboard(config, db, liveness_minutes=liveness_minutes)
    server = DashboardServer(dashboard, port)
    url = f'http://127.0.0.1:{server.server_port}/'
    print('Dashboard: ' + url)
    def on_serving():
        dashboard.start()
        if not no_browser:
            webbrowser.open(url)
    server.on_serving = on_serving
    try:
        server.serve_forever()
    finally:
        server.server_close()
        dashboard.close()
