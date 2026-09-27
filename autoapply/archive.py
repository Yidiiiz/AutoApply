"""Status-organized bundles. SQLite owns live work; bundles own rebuildable history."""
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .jobs import canonical_url
from .runtime import ProcessLock

log = logging.getLogger(__name__)
STATES = ('DISCOVERED', 'OPENED', 'FILLING', 'READY_TO_SUBMIT', 'READY_FOR_MANUAL_SUBMIT', 'SUBMITTING',
          'SUBMITTED', 'MANUAL_REQUIRED', 'FAILED', 'UNKNOWN', 'RATE_LIMITED',
          'CLOSED', 'INVALID', 'DUPLICATE', 'INELIGIBLE', 'ALREADY_APPLIED')
LEGACY = dict(QUEUED='DISCOVERED', RETRY='DISCOVERED', CHECKING='OPENED',
              APPLYING='FILLING', READY='READY_TO_SUBMIT', NEEDS_INPUT='MANUAL_REQUIRED',
              AUTH_REQUIRED='MANUAL_REQUIRED', MANUAL_REVIEW='MANUAL_REQUIRED')
_locks = {}
_lock_owners = threading.local()


def history_lock_held(root):
    return str(root) in getattr(_lock_owners, 'roots', ())


def safe_name(value):
    return re.sub(r'[^\w .-]', '_', value)[:70].strip(' .') or 'unknown'


def atomic_json(path, value):
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
    json.loads(payload)
    atomic_text(path, payload)


def atomic_text(path, payload):
    """Replace changed UTF-8 content atomically; preserve unchanged artifact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.read_text(encoding='utf-8') == payload:
            return
    except UnicodeError:
        pass  # Migration preserves malformed bytes before replacing the record.
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rename_with_retry(source, target):
    """Windows scanners can briefly hold a directory after a file replacement."""
    for attempt in range(7):
        try:
            return source.rename(target)
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(min(.05 * 2 ** attempt, 1))


def unique(items):
    seen, result = set(), []
    for item in items:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def record_state(record):
    state = str(record.get('application_state', '')).upper()
    legacy = str(record.get('status', '')).upper()
    if record.get('submitted') is True or record.get('submission_confirmation_seen'):
        return 'SUBMITTED'
    # These older workflow outcomes were incorrectly collapsed to FAILED.
    if legacy in {'CLOSED', 'INVALID', 'INELIGIBLE', 'DUPLICATE', 'ALREADY_APPLIED'}:
        return legacy
    if state in STATES:
        return state
    if state:  # An explicit but invalid state is not trustworthy.
        return 'UNKNOWN'
    if record.get('manual_action_required'):
        return 'MANUAL_REQUIRED'
    return LEGACY.get(legacy, legacy if legacy in STATES else 'UNKNOWN')


def normalize_record(record):
    result = dict(record)
    identifier = result.get('application_id', result.get('id'))
    if identifier is None or str(identifier).strip() == '':
        url = result.get('canonical_url') or result.get('url')
        try:
            identity = canonical_url(url) if url else None
        except (ValueError, TypeError):
            identity = None
        identity = identity or json.dumps({k: result.get(k) for k in
            ('job_id', 'external_job_id', 'company', 'title', 'created_at', 'discovered_at')}, sort_keys=True)
        if not any(result.get(k) for k in ('job_id', 'external_job_id', 'company', 'title', 'canonical_url', 'url')):
            identity = json.dumps(record, sort_keys=True)
        identifier = 'legacy-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    result['application_id'] = str(identifier)
    result['application_state'] = record_state(result)
    history = result.get('status_history', [])
    result['status_history'] = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
    if not result['status_history']:
        result['status_history'] = [dict(status=result['application_state'],
            timestamp=result.get('updated_at') or result.get('created_at'), source='legacy_snapshot',
            reason=result.get('failure_reason', ''), security_state=result.get('security_state', 'NONE'),
            security_provider=result.get('security_provider', 'UNKNOWN'),
            verification_state=result.get('verification_state', 'UNKNOWN'))]
    return result


class ApplicationHistory:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.records, self.paths = {}, {}
        self.revision = None

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with _locks.setdefault(str(self.root), threading.RLock()):
            deadline = time.monotonic() + 30
            lock = ProcessLock(self.root.parent / 'history.lock')
            while True:
                try:
                    lock.__enter__()
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Application history is busy; pending changes will recover on startup') from None
                    time.sleep(.05)
            try:
                roots = getattr(_lock_owners, 'roots', set())
                _lock_owners.roots = roots | {str(self.root)}
                yield
            finally:
                _lock_owners.roots = roots
                lock.__exit__()

    def _candidates(self):
        """Inspect complete bundles and legacy standalone records, never sidecars."""
        for entry in sorted(self.root.iterdir()):
            if entry.is_symlink():
                raise ValueError('History contains a symbolic link; manual review required')
            if entry.name in {'statistics.json', 'migration_report.json', '.layout.json', '.cache-token.json'} or entry.name.endswith('.tmp'):
                continue
            if entry.is_file():
                yield entry
            elif entry.name in {s.lower() for s in STATES}:
                for child in sorted(entry.iterdir()):
                    if child.is_symlink():
                        raise ValueError('History contains a symbolic link; manual review required')
                    yield child
            else:
                yield entry

    def _load(self, entry, report):
        path = entry / 'application.json' if entry.is_dir() else entry
        try:
            record = read_json(path)
            if not isinstance(record, dict) or not record:
                raise ValueError('Record must be a nonempty object')
            record = normalize_record(record)
        except (ValueError, OSError):
            report['warnings'].append(f'Unrecognized or malformed record preserved: {entry.relative_to(self.root)}')
            record = normalize_record({'application_id': 'recovery-' + hashlib.sha256(
                str(entry.relative_to(self.root)).encode()).hexdigest()[:24],
                'application_state': 'UNKNOWN', 'recovery_required': True})
        if entry.is_dir():
            for sidecar in entry.rglob('*.json'):
                try:
                    read_json(sidecar)
                except (ValueError, OSError):
                    report['warnings'].append(f'Malformed sidecar preserved: {sidecar.relative_to(self.root)}')
        return record

    def _backup(self):
        backup = self.root.parent / 'application_history_migration_backup'
        if backup.exists():
            return
        temporary = self.root.parent / ('history_backup_' + uuid.uuid4().hex)
        shutil.copytree(self.root, temporary)
        rename_with_retry(temporary, backup)

    def _merge_assets(self, source, target):
        """Keep conflicting bytes under preserved/, never silently overwrite them."""
        for path in sorted(source.rglob('*')) if source.is_dir() else [source]:
            if path.is_symlink():
                raise ValueError('History contains a symbolic link; manual review required')
            if not path.is_file():
                continue
            relative = path.relative_to(source) if source.is_dir() else Path('original.json')
            destination = target / relative
            if destination.exists():
                if destination.read_bytes() == path.read_bytes():
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
                destination = target / 'preserved' / digest / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)

    def _remove(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError('History operation outside root')
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _folder_name(self, record):
        ident = record['application_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', ident):
            ident = 'id-' + hashlib.sha256(ident.encode()).hexdigest()[:24]
        return f"{ident}_{safe_name(str(record.get('company', ''))[:28])}_{safe_name(str(record.get('title', ''))[:55])}"

    def _write(self, record, folder=None):
        record = normalize_record(record)
        identifier = record['application_id']
        folder = folder or self.paths.get(identifier)
        target = self.root / record['application_state'].lower() / (folder.name if folder else self._folder_name(record))
        if folder and folder != target and folder.exists():
            # Commit the new state before atomic directory rename. Startup can
            # repair an interrupted move from the internal record alone.
            atomic_json(folder / 'application.json', record)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                self._merge_assets(folder, target)
                atomic_json(target / 'application.json', record)
                self._remove(folder)
            else:
                rename_with_retry(folder, target)
        else:
            atomic_json(target / 'application.json', record)
        self.records[identifier], self.paths[identifier] = record, target
        return target

    def _scan(self):
        self.records, self.paths = {}, {}
        for path in self.root.glob('*/*/application.json'):
            record = normalize_record(read_json(path))
            ident = record['application_id']
            if ident in self.records:
                raise ValueError('Duplicate application ID; run history validate')
            self.records[ident], self.paths[ident] = record, path.parent

    def _refresh(self):
        revision = self._cache_revision()
        if revision != self.revision or not self.records:
            self._scan()
            self.revision = revision

    def _cache_revision(self):
        # Aggregate content may be unchanged while individual records change.
        # This token invalidates readers; it is NOT a dirty-queue acknowledgement.
        token = self.root / '.cache-token.json'
        statistics = self.root / 'statistics.json'
        return (read_json(token) if token.exists() else None,
                statistics.stat().st_mtime_ns if statistics.exists() else None)

    def _invalidate_readers(self):
        # Publish before writes, under the history lock. Even interrupted exports
        # invalidate other instances, and the durable DB dirty queue retries them.
        atomic_json(self.root / '.cache-token.json', uuid.uuid4().hex)

    def _statistics(self):
        from .history_statistics import calculate_statistics
        result = calculate_statistics(list(self.records.values()))
        listing_stats = self.root.parent / 'listing_statistics.json'
        if listing_stats.exists():
            try:
                result['listing_stats'] = read_json(listing_stats)
            except (ValueError, OSError):
                # Listing metrics are rebuilt from SQLite during startup maintenance.
                pass
        path = self.root / 'statistics.json'
        try:
            previous = read_json(path)
            if {k: v for k, v in previous.items() if k != 'generated_at'} == {
                    k: v for k, v in result.items() if k != 'generated_at'}:
                result['generated_at'] = previous['generated_at']
        except (OSError, ValueError, AttributeError, KeyError):
            pass
        atomic_json(path, result)
        self.revision = self._cache_revision()
        return result

    def validate(self):
        with self.locked():
            report = dict(inspected=0, moved=0, duplicates_merged=0, ambiguous_unknown=0, empty_directories=0, warnings=[])
            candidates = list(self._candidates())
            if any(p.is_symlink() for c in candidates if c.is_dir() for p in c.rglob('*')):
                raise ValueError('History contains a symbolic link; manual review required')
            empty = [p for p in candidates if p.is_dir() and not any(p.iterdir())]
            # Parse the full inventory before making any changes.
            loaded = [(p, self._load(p, report)) for p in candidates if p not in empty]
            if candidates and not (self.root / '.layout.json').exists():
                self._backup()
            self._invalidate_readers()
            for path in empty:
                report['empty_directories'] += 1
                report['warnings'].append(f'Empty legacy directory removed (no application record): {path.name}')
                path.rmdir()
            for state in STATES:
                (self.root / state.lower()).mkdir(exist_ok=True)
            groups = {}
            # Missing-ID exports can match an existing explicit ID by URL/job ID.
            aliases = {}
            for _, record in loaded:
                if not record['application_id'].startswith(('legacy-', 'recovery-')):
                    for key in ('job_id', 'canonical_url', 'url'):
                        if record.get(key):
                            aliases.setdefault((key, str(record[key])), set()).add(record['application_id'])
            for path, record in loaded:
                if record['application_id'].startswith('legacy-'):
                    matches = set().union(*(aliases.get((k, str(record.get(k))), set()) for k in ('job_id', 'canonical_url', 'url')))
                    if len(matches) == 1:
                        record['application_id'] = matches.pop()
                groups.setdefault(record['application_id'], []).append((path, record))
            self.records, self.paths = {}, {}
            for ident, group in groups.items():
                report['inspected'] += len(group)
                report['duplicates_merged'] += len(group) - 1
                group.sort(key=lambda pair: (str(pair[1].get('updated_at') or ''), len(json.dumps(pair[1]))))
                merged, transitions = {}, []
                for _, record in group:
                    # A single canonical record is not a merge: retain empty fields
                    # and their order so startup does not rewrite submitted history.
                    merged.update(record if len(group) == 1 else
                                  {k: v for k, v in record.items() if v is not None and v != ''})
                    transitions.extend(record['status_history'])
                merged['status_history'] = sorted(unique(transitions), key=lambda h: str(h.get('timestamp') or ''))
                # Never discard affirmative submission evidence from an older copy.
                if any(r.get('submission_confirmation_seen') or r.get('submitted') is True for _, r in group):
                    merged['submission_confirmation_seen'] = 1
                merged = normalize_record(merged)
                if merged['application_state'] == 'UNKNOWN':
                    report['ambiguous_unknown'] += 1
                    report['warnings'].append(f'Ambiguous application retained as UNKNOWN: {ident}')
                target = self.root / merged['application_state'].lower() / self._folder_name(merged)
                if len(group) == 1 and group[0][0].is_dir():
                    original = group[0][0]
                    target = self.root / merged['application_state'].lower() / original.name
                    if merged.get('recovery_required') and (original / 'application.json').exists():
                        recovery = original / 'preserved' / 'malformed-application.json'
                        recovery.parent.mkdir(exist_ok=True)
                        if not recovery.exists():
                            shutil.copy2(original / 'application.json', recovery)
                    self._write(merged, original)
                else:
                    for source, _ in group:
                        if source != target:
                            self._merge_assets(source, target)
                    self._write(merged, target)
                    for source, _ in group:
                        if source != target:
                            self._remove(source)
                report['moved'] += sum(p != target for p, _ in group)
            atomic_json(self.root / '.layout.json', {'version': 1})
            statistics = self._statistics()
            report['status_counts'] = statistics['status_counts']
            atomic_json(self.root / 'migration_report.json', report)
            log.info('History validation: inspected=%s moved=%s duplicates=%s unknown=%s',
                     report['inspected'], report['moved'], report['duplicates_merged'], report['ambiguous_unknown'])
            for warning in report['warnings']:
                log.warning(warning)
            return report

    migrate = validate

    def rebuild_statistics(self):
        # Repair/validate first, including duplicate IDs and malformed records.
        self.validate()
        return read_json(self.root / 'statistics.json')

    def list_applications(self, status=None):
        with self.locked():
            self._refresh()
            return [dict(r) for r in self.records.values() if status is None or r['application_state'] == str(status).upper()]

    def get_application(self, application_id):
        return next((r for r in self.list_applications() if r['application_id'] == str(application_id)), None)

    def find_by_job_id(self, job_id):
        return [r for r in self.list_applications() if str(r.get('job_id')) == str(job_id)]

    def find_by_url(self, url):
        target = canonical_url(url)
        found = []
        for record in self.list_applications():
            try:
                if canonical_url(record.get('canonical_url') or record.get('url') or '') == target:
                    found.append(record)
            except ValueError:
                continue
        return found

    def sync(self, db, ids):
        if not db.conn.in_transaction or not db._flushing_history:
            raise RuntimeError('History sync must be owned by Database.flush_history')
        with self.locked():
            self._refresh()
            self._invalidate_readers()
            for app_id in ids:
                if not db.one('SELECT id FROM applications WHERE id=?', (app_id,)):
                    folder = self.paths.get(str(app_id))
                    if folder:
                        # Explicit deletion remains recoverable outside canonical history.
                        trash = self.root.parent / 'application_history_deleted' / uuid.uuid4().hex
                        trash.parent.mkdir(exist_ok=True)
                        rename_with_retry(folder, trash)
                    self.paths.pop(str(app_id), None)
                    self.records.pop(str(app_id), None)
                    continue
                app = db.application(app_id)
                listing = db.one('SELECT * FROM jobs WHERE id=?', (app['job_id'],))
                events = db.rows('SELECT * FROM events WHERE application_id=? ORDER BY id', (app_id,))
                prior = self.records.get(str(app_id), {})
                history = db.rows('SELECT status,timestamp,reason,source,security_state,verification_state,security_provider,manual_action_required FROM history_transitions WHERE application_id=? ORDER BY id', (app_id,))
                if not history:
                    history = [dict(status=LEGACY.get(e['kind'], e['kind'].upper()), timestamp=e['created_at'], reason=e['detail'], source='events')
                               for e in events if e['kind'].upper() in STATES or e['kind'] in LEGACY]
                app.update(application_id=str(app_id), discovered_at=listing['discovered_at'],
                           status_history=sorted(unique(prior.get('status_history', []) + history), key=lambda h: str(h.get('timestamp') or '')),
                           sources=db.rows('SELECT * FROM job_sources WHERE job_id=?', (app['job_id'],)))
                # Preserve imported metadata not present in the live DB schema.
                record = dict(prior, **app)
                folder = self._write(record)
                old_screenshot = app.get('screenshot_path')
                if old_screenshot and Path(old_screenshot).parent.name == 'screenshots':
                    new_path = folder / 'screenshots' / Path(old_screenshot).name
                    if new_path.exists() and str(new_path) != old_screenshot:
                        db.conn.execute('UPDATE applications SET screenshot_path=? WHERE id=?', (str(new_path), app_id))
                        record['screenshot_path'] = str(new_path)
                        self._write(record)
                questions = db.rows('SELECT * FROM questions WHERE application_id=?', (app_id,))
                data = dict(listing=listing, sources=app['sources'], questions=questions,
                    answers=[q for q in questions if q['status'] == 'ANSWERED'],
                    generated_responses=[dict(question_id=q['id'], question=q['raw_question'], draft=db.setting(f"draft:{q['id']}")) for q in questions if db.setting(f"draft:{q['id']}") is not None],
                    status=dict(status=app['status'], reason=app['failure_reason'], updated_at=app['updated_at']),
                    events=events, confirmation={key: app[key] for key in ('status', 'submitted_at', 'confirmation_text', 'confirmation_url')})
                for name, value in data.items():
                    atomic_json(folder / (name + '.json'), value)
                atomic_text(folder / 'job_description.txt', app['description'] or '')
            self._statistics()


def archive_application(config, db, app_id):
    # Mutations already enqueue/flush via SQLite triggers. Do not force a second
    # export merely to get a screenshot directory. Missing bundles still repair.
    db.flush_history()
    with db.history.locked():
        db.history._refresh()
        folder = db.history.paths.get(str(app_id))
    if folder is None or not (folder / 'application.json').exists():
        db.flush_history([app_id])
    return db.history.paths[str(app_id)]


def migrate_application_history(root):
    return ApplicationHistory(root).migrate()


def validate_application_history(root):
    return ApplicationHistory(root).validate()


def rebuild_application_statistics(root):
    return ApplicationHistory(root).rebuild_statistics()
