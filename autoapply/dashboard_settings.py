"""Fixed-path, offline dashboard settings and canonical resume replacement."""

from datetime import datetime, timezone
import hashlib
import math
import os
import secrets
import shutil
import threading
import time

import yaml

from .archive import atomic_text
from .config import merge, read_yaml
from .resume_migration import invalidate_current_state
from .runtime import ProcessLock

MAX_RESUME_BYTES = 10 * 1024 * 1024
RESTART_NOTE = 'The running worker picks up settings at its next start. Restart the dashboard for dashboard defaults. YAML comments may be lost when saving.'


class SettingsRejected(ValueError):
    def __init__(self, message, *, status=400, errors=None):
        super().__init__(message)
        self.status, self.errors = status, errors or {}


def field(path, label, kind, minimum=None, maximum=None, step=None, help='', default=None, reason=None):
    return dict(path=path, label=label, type=kind, min=minimum, max=maximum,
                step=step, help=help, default=default, readonly=reason is not None, reason=reason)


SECTIONS = [
    ('general', 'General', [
        field('jobs.target_season', 'Target season', 'text', maximum=40, help='Season used to select matching internships.'),
        field('processing.max_applications_per_day', 'Daily application limit', 'number', 1, 500, 1, 'Maximum applications processed each day.')]),
    ('discovery', 'Discovery', [
        field('jobs.max_listing_age_days', 'Maximum listing age', 'number', 1, 30, 1, 'Ignore listings older than this many days.'),
        field('polling.github_minutes', 'GitHub polling interval', 'number', 1, 1440, 1, 'Minutes between discovery scans.'),
        field('polling.linkedin_minutes', 'LinkedIn polling interval', 'number', 5, 1440, 1, 'Minutes between discovery scans.'),
        field('polling.handshake_minutes', 'Handshake polling interval', 'number', 5, 1440, 1, 'Minutes between discovery scans.')]),
    ('filling', 'Filling', [
        field('application.delay_seconds', 'Application delay', 'number', 0, 600, 1, 'Seconds between applications.'),
        field('application.min_confidence', 'Minimum confidence', 'number', .90, 1.0, .01, 'Answer confidence threshold from 0.90 to 1.0.'),
        field('application.max_pages', 'Maximum pages', 'number', 1, 30, 1, 'Maximum form pages per application.'),
        field('application.confirmation_timeout_seconds', 'Confirmation timeout', 'number', 5, 120, 1, 'Seconds to wait for confirmation.')]),
    ('dashboard', 'Dashboard', [
        field('dashboard.default_batch_size', 'Default batch size', 'number', 1, 10, 1, 'Initial batch size on the Run tab.', 5),
        field('dashboard.liveness_minutes', 'Liveness interval', 'number', 5, 1440, 1, 'Minutes between listing checks after dashboard restart.', 30)]),
    ('resume', 'Resume', []),
    ('safety', 'Safety', [
        field('application.auto_submit', 'Automatic submission', 'boolean', default=False, reason='Always off; final submission stays manual.'),
        field('browser.headless', 'Headless browser', 'boolean', default=False, reason='Must stay false for semi-auto filling.'),
        field('processing.max_retries', 'Maximum retries', 'number', reason='The attempt budget is a safety control.'),
        field('discord.enabled', 'Discord enabled', 'boolean', reason='Managed outside the dashboard; semi-auto review requires Discord.')])]
EDITABLE = {f['path']: f for _, _, fields in SECTIONS for f in fields if not f['readonly']}


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def replace_file(source, target):
    # Same PermissionError retry policy as archive.atomic_text.
    for attempt in range(7):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(min(.05 * 2 ** attempt, 1))


class DashboardSettings:
    def __init__(self, config, connection, worker_mutex, worker_busy=lambda: False):
        self.config, self.connection, self.worker_mutex = config, connection, worker_mutex
        self.mutex = threading.RLock()
        self.worker_busy = worker_busy

    def _data(self):
        return merge(self.config.data, read_yaml(self.config.private / 'config.yaml'))

    def get(self):
        with self.mutex:
            data = self._data()
            sections = []
            for key, label, fields in SECTIONS:
                result = []
                for definition in fields:
                    f = {k: v for k, v in definition.items() if k != 'default'}
                    section, name = f['path'].split('.')
                    f['value'] = data.get(section, {}).get(name, definition['default'])
                    if f['path'] in {'application.auto_submit', 'browser.headless'}:
                        f['value'] = False
                    result.append(f)
                sections.append(dict(key=key, label=label, fields=result))
            return dict(sections=sections, restart_required_note=RESTART_NOTE, extension_key=self.extension_key())

    def extension_key(self, *, regenerate=False):
        with self.mutex:
            if not regenerate:
                with self.connection() as db:
                    key = db.setting('extension_key')
                if key:
                    return key
            with self.connection(writable=True) as db:
                with db.transaction():
                    # Recheck after taking the lock in case another dashboard paired.
                    key = db.setting('extension_key')
                    if regenerate or not key:
                        key = secrets.token_hex(32)
                        db.set_setting('extension_key', key)
                return key

    def _backup(self, path, timestamp):
        folder = self.config.private / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, folder / (path.stem + '-' + timestamp + '.yaml'))
        for old in sorted(folder.glob(path.stem + '-*.yaml'), reverse=True)[20:]:
            old.unlink()

    def save(self, changes):
        if not isinstance(changes, dict):
            raise SettingsRejected('changes must be an object')
        errors = {}
        for path, value in changes.items():
            f = EDITABLE.get(path)
            if f is None:
                errors[path] = 'This setting cannot be changed in the dashboard.'
            elif f['type'] == 'text':
                if not isinstance(value, str) or len(value) > f['max']:
                    errors[path] = 'Enter text of at most 40 characters.'
            elif (type(value) not in (int, float) or (type(value) is float and not math.isfinite(value))
                  or (f['step'] == 1 and type(value) is not int)
                  or not f['min'] <= value <= f['max']):
                errors[path] = f"Enter {'an integer' if f['step'] == 1 else 'a number'} from {f['min']} to {f['max']}."
        if errors:
            raise SettingsRejected('Settings validation failed', errors=errors)
        with self.mutex:
            path = self.config.private / 'config.yaml'
            data = read_yaml(path)
            if changes and data.get('application', {}).get('auto_submit') is True:
                raise SettingsRejected('Automatic submission must be disabled before saving settings',
                                       errors={'application.auto_submit': 'Disable automatic submission outside the dashboard first.'})
            update = {}
            for dotted, value in changes.items():
                section, key = dotted.split('.')
                update.setdefault(section, {})[key] = value
            if changes:
                self._backup(path, stamp())
                atomic_text(path, yaml.safe_dump(merge(data, update), sort_keys=False, allow_unicode=True))
            return dict(saved=changes, **self.get(), note=RESTART_NOTE)

    @staticmethod
    def _metadata(path):
        stat = path.stat()
        return dict(filename=path.name, size=stat.st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()[:12],
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat())

    def resume(self):
        with self.mutex:
            path = self.config.resume
            current = self._metadata(path) if path.exists() else dict(filename='resume.pdf', size=0, sha256=None, modified_at=None)
            current['archived'] = [self._metadata(p) for p in sorted(
                (path.parent / 'archive').glob('resume-*.pdf'), reverse=True) if p.is_file()]
            return current

    def resume_bytes(self):
        with self.mutex:
            try:
                return self.config.resume.read_bytes()
            except FileNotFoundError:
                raise SettingsRejected('No resume has been uploaded', status=404) from None

    def replace_resume(self, body):
        if not isinstance(body, bytes) or not 5 <= len(body) <= MAX_RESUME_BYTES or not body.startswith(b'%PDF-'):
            raise SettingsRejected('Upload a PDF starting with %PDF- (maximum 10 MB).')
        with self.mutex, self.worker_mutex:
            if self.worker_busy():
                raise SettingsRejected('Stop the worker before replacing the resume', status=409)
            lock = ProcessLock(self.config.private / 'worker.lock')
            try:
                lock.__enter__()
            except RuntimeError:
                raise SettingsRejected('Stop the worker before replacing the resume', status=409) from None
            try:
                return self._replace_resume(body)
            finally:
                lock.__exit__()

    def _replace_resume(self, body):
        path, timestamp = self.config.resume, stamp()
        path.parent.mkdir(parents=True, exist_ok=True)
        profile_path = self.config.private / 'profile.yaml'
        profile_bytes = profile_path.read_text(encoding='utf-8') if profile_path.exists() else None
        profile = read_yaml(profile_path)
        old_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        new_sha = hashlib.sha256(body).hexdigest()
        profile['resume_revision'] = dict(file='resumes/resume.pdf', sha256=new_sha)
        payload = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)
        temporary = path.with_name('resume.pdf.tmp')
        archived = None
        replaced = profile_replaced = False
        try:
            with temporary.open('wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if old_sha:
                archive = path.parent / 'archive'
                archive.mkdir(parents=True, exist_ok=True)
                archived = archive / f'resume-{old_sha[:12]}-{timestamp}.pdf'
                shutil.copy2(path, archived)
            self._backup(profile_path, timestamp)
            with self.connection(writable=True) as db:
                with db.transaction(sync_history=False):
                    affected, _ = invalidate_current_state(db.conn, old_sha, new_sha, {})
                    db.set_setting('resume_replaced:' + timestamp,
                                   dict(old_sha256=old_sha, new_sha256=new_sha, affected_applications=affected))
                    replace_file(temporary, path)
                    replaced = True
                    atomic_text(profile_path, payload)
                    profile_replaced = True
            return dict(sha256=new_sha, size=len(body), affected_applications=affected,
                        note='Resume-derived profile facts were not changed; review the Answers tab')
        except BaseException:
            # Compensate file changes if the profile write or SQLite commit fails.
            if replaced:
                if archived:
                    shutil.copy2(archived, temporary)
                    replace_file(temporary, path)
                else:
                    archive = path.parent / 'archive'
                    archive.mkdir(parents=True, exist_ok=True)
                    replace_file(path, archive / f'resume-{new_sha[:12]}-{timestamp}.pdf')
            if profile_replaced:
                if profile_bytes is not None:
                    atomic_text(profile_path, profile_bytes)
                else:
                    profile_path.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
