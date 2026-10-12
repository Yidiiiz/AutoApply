"""Offline settings checks; every config, profile, resume and DB is temporary."""

import hashlib
import http.client
import io
import json
from unittest.mock import Mock

import pytest
import yaml

from autoapply.config import Config, read_yaml
from autoapply.dashboard import Dashboard, DashboardHandler, DashboardServer
from autoapply.dashboard_settings import EDITABLE, SettingsRejected, MAX_RESUME_BYTES
from autoapply.runtime import ProcessLock


@pytest.fixture
def dashboard(config, db):
    config.data['application']['auto_submit'] = False
    config.data['browser']['headless'] = False
    (config.private / 'config.yaml').write_text(
        '# preserved in backup\njobs:\n  target_season: Summer 2027\nprivate_extension:\n  keep: unchanged\n', encoding='utf-8')
    old_sha = hashlib.sha256(config.resume.read_bytes()).hexdigest()
    profile = config.profile
    profile['resume_revision'] = dict(file='resumes/resume.pdf', sha256=old_sha)
    profile['verified_facts'] = {'qualifications.python': True}
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    value = Dashboard(config, db)
    yield value
    value.close()


class MemorySocket:
    def __init__(self, data):
        self.input, self.output = io.BytesIO(data), io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


def request(dashboard, method, path, body=b'', headers=None):
    server = DashboardServer.__new__(DashboardServer)
    server.dashboard, server.server_port = dashboard, 8765
    values = {'Host': '127.0.0.1:8765', 'Content-Length': str(len(body))}
    if method == 'POST':
        values.update({'Origin': 'http://127.0.0.1:8765', 'X-Dashboard-Token': dashboard.token,
                       'Content-Type': 'application/json'})
    values.update(headers or {})
    raw = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in values.items()) + '\r\n'
    socket = MemorySocket(raw.encode() + body)
    DashboardHandler(socket, ('127.0.0.1', 1), server)
    response = http.client.HTTPResponse(MemorySocket(socket.output.getvalue()))
    response.begin()
    content = response.read()
    data = json.loads(content) if response.getheader('Content-Type').startswith('application/json') else content
    return response.status, data, dict(response.getheaders())


def test_exact_fields_and_no_secrets(dashboard, config):
    config.data['secret_token'] = 'never expose this'
    status, data, _ = request(dashboard, 'GET', '/api/settings')
    assert status == 200
    fields = {f['path']: f for s in data['sections'] for f in s['fields']}
    expected = {'jobs.max_listing_age_days', 'jobs.target_season', 'polling.github_minutes',
                'polling.linkedin_minutes', 'polling.handshake_minutes', 'application.delay_seconds',
                'application.min_confidence', 'application.max_pages', 'application.confirmation_timeout_seconds',
                'processing.max_applications_per_day', 'dashboard.default_batch_size', 'dashboard.liveness_minutes'}
    assert set(EDITABLE) == expected
    assert set(fields) == expected | {'application.auto_submit', 'browser.headless', 'processing.max_retries', 'discord.enabled'}
    assert 'never expose this' not in json.dumps(data)
    assert fields['application.auto_submit']['value'] is False
    confidence = fields['application.min_confidence']
    assert (confidence['min'], confidence['max'], confidence['step']) == (.90, 1.0, .01)
    assert confidence['help'] == 'Answer confidence threshold from 0.90 to 1.0.'
    assert all(set(f) == {'path', 'label', 'type', 'value', 'min', 'max', 'step', 'help', 'readonly', 'reason'} for f in fields.values())


@pytest.mark.parametrize('path,value', [
    ('jobs.max_listing_age_days', 0), ('jobs.max_listing_age_days', 31), ('jobs.max_listing_age_days', True),
    ('jobs.max_listing_age_days', 1.0), ('jobs.target_season', 'x' * 41), ('jobs.target_season', 1),
    ('polling.github_minutes', 1441), ('polling.linkedin_minutes', 4), ('polling.handshake_minutes', '5'),
    ('application.delay_seconds', -1), ('application.min_confidence', .79), ('application.min_confidence', .80),
    ('application.min_confidence', .89), ('application.min_confidence', 1.01), ('application.min_confidence', float('nan')),
    ('application.min_confidence', float('inf')), ('application.min_confidence', 10 ** 400),
    ('application.max_pages', 31), ('application.confirmation_timeout_seconds', 121),
    ('processing.max_applications_per_day', 501), ('dashboard.default_batch_size', 11), ('dashboard.liveness_minutes', 4),
    ('application.auto_submit', True), ('browser.headless', True), ('processing.max_retries', 1),
    ('discord.enabled', False), ('unknown.key', 1), ('.env', 'secret')])
def test_invalid_changes_are_all_or_nothing(dashboard, config, path, value):
    before = (config.private / 'config.yaml').read_bytes()
    changes = {'dashboard.default_batch_size': 7, path: value}
    status, data, _ = request(dashboard, 'POST', '/api/settings',
                              json.dumps({'changes': changes}).encode())
    assert status == 400 and path in data['errors']
    assert (config.private / 'config.yaml').read_bytes() == before
    assert not (config.private / 'backups').exists()


def test_save_merge_backup_and_defaults(dashboard, config):
    before = (config.private / 'config.yaml').read_bytes()
    changes = {path: f['min'] if f['type'] == 'number' else 'Fall 2028' for path, f in EDITABLE.items()}
    changes['dashboard.default_batch_size'] = 8
    status, data, _ = request(dashboard, 'POST', '/api/settings', json.dumps({'changes': changes}).encode())
    assert status == 200 and data['saved'] == changes
    assert 'next start' in data['note'] and 'comments' in data['note']
    assert read_yaml(config.private / 'config.yaml')['private_extension'] == {'keep': 'unchanged'}
    backups = list((config.private / 'backups').glob('config-*.yaml'))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    status, page, _ = request(dashboard, 'GET', '/')
    assert status == 200 and b'id="count" type="number" value="8"' in page
    for n in range(22):
        dashboard.settings.save({'jobs.target_season': str(n)})
    assert len(list((config.private / 'backups').glob('config-*.yaml'))) == 20


@pytest.mark.parametrize('value', [.90, .95, 1.0])
def test_confidence_bounds_remain_loadable(dashboard, config, value):
    changes = {'application.min_confidence': value}
    status, data, _ = request(dashboard, 'POST', '/api/settings', json.dumps({'changes': changes}).encode())
    assert status == 200 and data['saved'] == changes
    assert Config(config.root).data['application']['min_confidence'] == value


def test_resume_replace_invalidates_and_snapshot_succeeds(dashboard, config, db, listing):
    job_id, _ = db.ingest(listing, config)
    app_id = db.one('SELECT id FROM applications WHERE job_id=?', (job_id,))['id']
    old_bytes, old_profile = config.resume.read_bytes(), config.profile
    old_sha = hashlib.sha256(old_bytes).hexdigest()
    db.set_setting(f'fill_checkpoint:{app_id}', {'readiness': 'READY', 'committed_field_count': 4})
    receipt = {'ready': True, 'sha256': old_sha}
    db.set_setting(f'upload_result:{app_id}', receipt)
    config.profile_snapshot()
    new_pdf = b'%PDF-1.4\n% replacement fixture\n%%EOF\n'
    status, result, _ = request(dashboard, 'POST', '/api/resume', new_pdf,
                                {'Content-Type': 'application/pdf', 'X-Filename': '../../outside.pdf'})
    new_sha = hashlib.sha256(new_pdf).hexdigest()
    assert status == 200 and result['sha256'] == new_sha and result['size'] == len(new_pdf)
    assert result['affected_applications'] == [app_id]
    assert config.resume.read_bytes() == new_pdf
    archived = list((config.resume.parent / 'archive').glob('resume-*.pdf'))
    assert len(archived) == 1 and archived[0].read_bytes() == old_bytes
    assert config.profile['resume_revision'] == {'file': 'resumes/resume.pdf', 'sha256': new_sha}
    assert config.profile['verified_facts'] == old_profile['verified_facts']
    assert config.profile_snapshot().facts['resume_revision']['sha256'] == new_sha
    assert Config(config.root).profile_snapshot().facts['resume_revision']['sha256'] == new_sha
    assert read_yaml(next((config.private / 'backups').glob('profile-*.yaml'))) == old_profile
    assert db.setting(f'fill_checkpoint:{app_id}')['readiness'] == 'RESUME_REVALIDATION_REQUIRED'
    assert db.setting(f'upload_result:{app_id}')['ready'] is False
    assert db.setting(f'resume_migration_history:{old_sha}:upload_result:{app_id}') == receipt
    recorded = db.one("SELECT value FROM settings WHERE key LIKE 'resume_replaced:%'")
    assert json.loads(recorded['value']) == dict(old_sha256=old_sha, new_sha256=new_sha, affected_applications=[app_id])
    status, info, _ = request(dashboard, 'GET', '/api/resume')
    assert status == 200 and info['filename'] == 'resume.pdf' and info['sha256'] == new_sha[:12]
    assert info['archived'][0]['sha256'] == old_sha[:12] and info['modified_at']
    status, content, headers = request(dashboard, 'GET', '/api/resume/file')
    assert status == 200 and content == new_pdf and headers['Content-Type'] == 'application/pdf'
    assert headers['Content-Disposition'] == 'inline; filename="resume.pdf"'


@pytest.mark.parametrize('body,headers', [
    (b'not a PDF', {'Content-Type': 'application/pdf'}),
    (b'%PDF-', {'Content-Type': 'application/pdf', 'Content-Length': 'invalid'}),
    (b'%PDF-', {'Content-Type': 'application/pdf', 'Content-Length': str(MAX_RESUME_BYTES + 1)}),
    (b'%PDF-', {'Content-Type': 'application/pdf', 'Content-Length': '9'}),
    (b'%PDF-', {'Content-Type': 'application/pdf', 'Transfer-Encoding': 'chunked'}),
    (b'{}', {'Content-Type': 'text/plain'})])
def test_resume_rejects_bad_body_or_size(dashboard, config, body, headers):
    before = config.resume.read_bytes(), (config.private / 'profile.yaml').read_bytes()
    assert request(dashboard, 'POST', '/api/resume', body, headers)[0] == 400
    assert before == (config.resume.read_bytes(), (config.private / 'profile.yaml').read_bytes())


def test_resume_worker_lock_and_startup_refusal(dashboard, config):
    before = config.resume.read_bytes()
    with ProcessLock(config.private / 'worker.lock'):
        status, data, _ = request(dashboard, 'POST', '/api/resume', b'%PDF-new', {'Content-Type': 'application/pdf'})
    assert status == 409 and data['error'] == 'Stop the worker before replacing the resume'
    dashboard._child = Mock()
    dashboard._child.poll.return_value = None
    assert request(dashboard, 'POST', '/api/resume', b'%PDF-new', {'Content-Type': 'application/pdf'})[0] == 409
    assert config.resume.read_bytes() == before
    assert not (config.private / 'backups').exists()


def test_resume_rollback_on_profile_failure(dashboard, config, db, monkeypatch):
    before = config.resume.read_bytes(), (config.private / 'profile.yaml').read_bytes()
    monkeypatch.setattr('autoapply.dashboard_settings.atomic_text', Mock(side_effect=PermissionError))
    with pytest.raises(PermissionError):
        dashboard.settings.replace_resume(b'%PDF-new')
    assert before == (config.resume.read_bytes(), (config.private / 'profile.yaml').read_bytes())
    assert not db.one("SELECT key FROM settings WHERE key LIKE 'resume_replaced:%'")
    assert config.profile_snapshot()
    assert not config.resume.with_name('resume.pdf.tmp').exists()


@pytest.mark.parametrize('path,body,content_type', [('/api/settings', b'{"changes":{}}', 'application/json'),
                                                   ('/api/resume', b'%PDF-new', 'application/pdf')])
def test_write_auth_and_origin(dashboard, path, body, content_type):
    for headers in [{'X-Dashboard-Token': 'wrong'}, {'Origin': 'https://example.test'}, {'Host': 'example.test'}]:
        headers['Content-Type'] = content_type
        assert request(dashboard, 'POST', path, body, headers)[0] == 403


def test_json_resume_still_unpauses(dashboard, db):
    db.set_setting('paused', True)
    assert request(dashboard, 'POST', '/api/resume', b'{}')[0] == 200
    assert db.setting('paused') is False


def test_never_rewrites_enabled_auto_submit(dashboard, config):
    path = config.private / 'config.yaml'
    path.write_text('application:\n  auto_submit: true\n', encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(SettingsRejected, match='must be disabled'):
        dashboard.settings.save({'dashboard.default_batch_size': 6})
    assert path.read_bytes() == before
    assert not (config.private / 'backups').exists()
