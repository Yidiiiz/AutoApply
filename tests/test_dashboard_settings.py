"""Offline settings checks; every config, profile, resume and DB is temporary."""

import hashlib
import http.client
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    values.update({key.title(): value for key, value in (headers or {}).items()})
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
                'processing.max_applications_per_day', 'dashboard.default_batch_size'}
    assert set(EDITABLE) == expected
    assert set(fields) == expected | {'application.auto_submit', 'browser.headless', 'processing.max_retries', 'discord.enabled'}
    assert 'never expose this' not in json.dumps(data)
    assert fields['application.auto_submit']['value'] is False
    confidence = fields['application.min_confidence']
    assert (confidence['min'], confidence['max'], confidence['step']) == (.90, 1.0, .01)
    assert '0.98 recommended' in confidence['help'] and 'not a probability' in confidence['help']
    assert all(set(f) == {'path', 'label', 'type', 'value', 'min', 'max', 'step', 'help', 'readonly', 'reason'} for f in fields.values())


def test_t066_source_roundtrip_preserves_disabled_flags(dashboard, config):
    config.data['github_sources'] = [{'url': 'https://github.com/example/existing', 'enabled': False, 'keep': 'value'}]
    config.data['linkedin']['enabled'] = False
    before_confidence = config.data['application']['min_confidence']
    sources = {'github': ['https://github.com/example/existing', 'https://github.com/example/new'],
               'linkedin': ['https://www.linkedin.com/jobs/search/?keywords=intern'],
               'handshake': ['https://school.joinhandshake.com/stu/postings?query=intern']}
    status, data, _ = request(dashboard, 'POST', '/api/settings', json.dumps({'changes': {'discovery.sources': sources}}).encode())
    assert status == 200 and data['discovery_sources'] == sources
    saved = read_yaml(config.private / 'config.yaml')
    assert saved['github_sources'][0] == config.data['github_sources'][0]
    assert saved['github_sources'][0]['enabled'] is False
    assert config.data['linkedin']['enabled'] is False
    assert config.data['application']['min_confidence'] == before_confidence
    sources['github'].pop()
    assert dashboard.settings.save({'discovery.sources': sources})['discovery_sources'] == sources
    assert Config(config.root).data['linkedin']['enabled'] is False
    sources['github'][0] = 'https://github.com/example/replacement'
    dashboard.settings.save({'discovery.sources': sources})
    assert config.data['github_sources'][0] == {'url': sources['github'][0], 'enabled': False, 'keep': 'value'}


@pytest.mark.parametrize('kind,url', [
    ('github', 'https://github.com/example/jobs?token=secret'),
    ('github', 'https://user:secret@github.com/example/jobs'),
    ('github', 'https://github.com/example/jobs/tree/main'),
    ('linkedin', 'http://www.linkedin.com/jobs/search/'),
    ('linkedin', 'https://www.linkedin.com.attacker.test/jobs/search/'),
    ('linkedin', 'https://www.linkedin.com/jobs/search/?session_token=secret'),
    ('handshake', 'https://127.0.0.1/stu/jobs'),
    ('handshake', 'https://joinhandshake.com.attacker.test/stu/jobs'),
    ('handshake', 'https://school.joinhandshake.com:444/stu/jobs'),
    ('handshake', 'https://arbitrary.test/jobs'),
])
def test_t066_source_invalid_save_rolls_back(dashboard, config, kind, url):
    sources = dashboard.settings.get()['discovery_sources']
    sources[kind] = [url]
    before = (config.private / 'config.yaml').read_bytes()
    status, data, _ = request(dashboard, 'POST', '/api/settings', json.dumps({'changes': {
        'jobs.target_season': 'Fall 2028', 'discovery.sources': sources}}).encode())
    assert status == 400 and 'discovery.sources' in data['errors']
    assert (config.private / 'config.yaml').read_bytes() == before
    assert not (config.private / 'backups').exists()


def test_t066_invalid_existing_credentials_never_render(dashboard, config):
    config.data['linkedin']['search_urls'] = ['https://www.linkedin.com/jobs/search/?token=fixture-secret']
    data = dashboard.settings.get()
    assert 'fixture-secret' not in json.dumps(data)
    assert data['hidden_source_count'] == 1
    before = (config.private / 'config.yaml').read_bytes()
    with pytest.raises(SettingsRejected, match='invalid existing'):
        dashboard.settings.save({'discovery.sources': data['discovery_sources']})
    assert (config.private / 'config.yaml').read_bytes() == before


def test_t066_settings_profile_browser_fixture(dashboard, config, db):
    """Actual handler + Chromium, all traffic intercepted, synthetic data only."""
    from playwright.sync_api import sync_playwright, expect
    output = Path('.tmp/review/t066')
    output.mkdir(parents=True, exist_ok=True)
    db.set_setting('extension_key', 'synthetic-pairing-key-for-offline-fixture')
    config.data['github_sources'] = [{'url': 'https://github.com/fixture/disabled', 'enabled': False}]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{window.fixtureCopied=value;}}});")
            def respond(route):
                incoming = route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort()
                    return
                target = incoming.url.removeprefix('http://127.0.0.1:8765')
                status, body, headers = request(dashboard, incoming.method, target,
                                                (incoming.post_data or '').encode(), incoming.all_headers())
                route.fulfill(status=status, body=body if isinstance(body, (str, bytes)) else json.dumps(body), headers=headers)
            page.route('**/*', respond)
            page.emulate_media(color_scheme='dark', reduced_motion='reduce')
            def uncovered(locator):
                locator.evaluate("e=>{e.focus();e.scrollIntoView({block:'center'});}")
                assert locator.evaluate('''e=>{
                    const r=e.getBoundingClientRect();
                    const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                    const overlaps=[...document.querySelectorAll('.settings-footer:not([hidden])')].some(f=>{
                        if(f.contains(e))return false;
                        const b=f.getBoundingClientRect();
                        return r.left<b.right&&r.right>b.left&&r.top<b.bottom&&r.bottom>b.top;
                    });
                    return !overlaps&&(hit===e||e.contains(hit));
                }'''), 'Control is covered by another element'
            for width in (1440, 390):
                page.set_viewport_size(dict(width=width, height=1000))
                successful_at = (datetime.now(timezone.utc) - timedelta(days=2, hours=1)).isoformat()
                db.set_setting('discovery_status', dict(successful_at=successful_at, status='succeeded', mode='quick'))
                page.goto('http://127.0.0.1:8765/')
                expect(page.locator('#discovery-state')).to_have_text('Last updated 2 days ago')
                # A new document has no browser-local completion cache to fall back on.
                page.reload()
                expect(page.locator('#discovery-state')).to_have_text('Last updated 2 days ago')
                db.set_setting('discovery_status', dict(successful_at=successful_at, status='running', mode='daily',
                                                       started_at=datetime.now(timezone.utc).isoformat()))
                dashboard._active_refresh = 'daily'
                try:
                    page.reload()
                    expect(page.locator('#refresh-discovery')).to_be_disabled()
                    expect(page.locator('#discovery-label')).to_contain_text('Daily check')
                    expect(page.locator('#discovery-state')).to_have_text('Last updated 2 days ago')
                    assert dashboard.start_refresh() == 'daily'
                finally:
                    dashboard._active_refresh = None
                db.set_setting('discovery_status', dict(successful_at=successful_at, status='failed', mode='daily'))
                page.reload()
                expect(page.locator('#refresh-discovery')).to_be_enabled()
                expect(page.locator('#discovery-state')).to_have_text('Last updated 2 days ago · Last check incomplete')
                page.get_by_role('tab', name='Settings', exact=True).click()
                page.locator('#settings-nav-discovery').click()
                expect(page.locator('.source-url-row').filter(has_text='Disabled')).to_have_count(1)
                original = dashboard.settings.get()['discovery_sources']
                new_url = f'https://github.com/fixture/jobs-{width}'
                page.get_by_label('New source URL', exact=True).fill(new_url)
                page.get_by_role('button', name='Add source', exact=True).click()
                page.locator('#settings-save').click()
                expect(page.locator('#settings-status')).to_contain_text('Saved')
                expect(page.locator('#settings-footer')).to_have_class('settings-footer saved-feedback')
                assert page.locator('#settings-footer').evaluate("e=>getComputedStyle(e).position==='static'&&getComputedStyle(e).borderTopWidth==='0px'")
                assert dashboard.settings.get()['discovery_sources']['github'][-1] == new_url
                uncovered(page.get_by_label('New source URL', exact=True))
                page.screenshot(path=str(output / f'discovery-{width}.png'), full_page=True)
                expect(page.locator('#settings-footer')).to_be_hidden(timeout=4500)
                # Invalid URL save leaves the persisted config and enabled flags intact.
                before = (config.private / 'config.yaml').read_bytes()
                index = len(original['github']) + 1
                page.get_by_label(f'GitHub URL {index}', exact=True).fill('https://arbitrary.test/jobs')
                uncovered(page.get_by_label('New source URL', exact=True))
                uncovered(page.locator('#settings-save'))
                page.locator('#settings-save').click()
                expect(page.locator('.discovery-sources .field-error').first).to_contain_text('matching type')
                assert (config.private / 'config.yaml').read_bytes() == before
                page.locator('#settings-discard').click()
                page.get_by_role('button', name=f'Remove GitHub URL {index}', exact=True).click()
                page.locator('#settings-save').click()
                expect(page.locator('#settings-status')).to_contain_text('Saved')
                assert dashboard.settings.get()['discovery_sources'] == original
                page.locator('#settings-nav-dashboard').click()
                expect(page.get_by_text('Background interval', exact=True)).to_have_count(0)
                expect(page.locator('#extension-key')).to_have_attribute('type', 'password')
                page.get_by_role('button', name='Reveal pairing key', exact=True).click()
                expect(page.locator('#extension-key')).to_have_attribute('type', 'text')
                page.get_by_role('button', name='Copy pairing key', exact=True).click()
                expect(page.locator('.pairing-status')).to_have_text('Pairing key copied')
                assert page.evaluate('fixtureCopied') == 'synthetic-pairing-key-for-offline-fixture'
                page.get_by_role('button', name='Hide pairing key', exact=True).click()
                page.screenshot(path=str(output / f'pairing-{width}.png'), full_page=True)
                page.get_by_role('tab', name='Answers', exact=True).click()
                page.locator('#answers-nav').get_by_role('button', name='Education', exact=True).click()
                page.get_by_role('button', name='Add education', exact=True).click()
                expect(page.locator('.answer-card').last).to_be_visible()
                expect(page.locator('#answers-footer')).to_be_hidden()
                for control in page.locator('.answer-card').last.locator('input,select,textarea,button').all():
                    uncovered(control)
                page.screenshot(path=str(output / f'education-{width}.png'), full_page=True)
                page.locator('.answer-card').get_by_role('button', name='Cancel', exact=True).click()
                expect(page.locator('#answers-footer')).to_be_hidden()
                # Done creates a draft change; Discard restores the original profile.
                page.get_by_role('button', name='Add education', exact=True).click()
                page.locator('.answer-card').get_by_label('School', exact=True).fill('Synthetic college')
                page.locator('.answer-card').get_by_role('button', name='Done', exact=True).click()
                expect(page.locator('#answers-footer')).to_be_visible()
                uncovered(page.locator('#answers-discard'))
                page.locator('#answers-discard').click()
                expect(page.locator('#answers-footer')).to_be_hidden()
                page.locator('#answers-nav').get_by_role('button', name='Links', exact=True).click()
                page.get_by_role('button', name='Add link', exact=True).click()
                expect(page.locator('#answers-footer')).to_be_hidden()
                for control in page.locator('.answer-card').last.locator('input,button').all():
                    uncovered(control)
                page.screenshot(path=str(output / f'links-{width}.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.get_by_label('Label', exact=True).fill('Synthetic link')
                page.get_by_label('URL', exact=True).fill('https://example.test/profile')
                page.locator('.answer-card').get_by_role('button', name='Done', exact=True).click()
                uncovered(page.locator('#answers-save'))
                page.locator('#answers-save').click()
                expect(page.locator('#answers-status')).to_contain_text('Saved')
                expect(page.locator('#answers-footer')).to_have_class('settings-footer saved-feedback')
                expect(page.locator('#answers-footer')).to_be_hidden(timeout=4500)
                page.reload()
                page.get_by_role('tab', name='Answers', exact=True).click()
                page.locator('#answers-nav').get_by_role('button', name='Links', exact=True).click()
                expect(page.locator('#answers-content')).to_contain_text('Synthetic link')
            assert errors == []
        finally:
            browser.close()


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
    assert 'next start' in data['note'] and 'comments' not in data['note']
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
