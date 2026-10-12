"""Real Chromium entry paths, served by loopback HTTP; no external requests."""
import json
import mimetypes
import sqlite3
import subprocess
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright

from autoapply.applications import GenericApplicationAdapter
from autoapply.consent import dismiss_overlay
from autoapply.entry_harness import EntryResult, probe_entry, run_entry_test, select_listings
from autoapply.patience import PatiencePolicy
from autoapply.workday import WorkdayAdapter


FIXTURES = Path(__file__).parent / 'fixtures/entry'
CASES = [
    ('onetrust-cookie.html', 'fixture.example.test', 'form_ready', 'generic', ['Reject All', 'Apply']),
    ('accept-only-cookie.html', 'fixture.example.test', 'entry_failed', 'generic', []),
    ('workable-progress.html', 'fixture.workable.com', 'form_ready', 'workable', ['Apply now']),
    ('greenhouse-inline.html', 'fixture.greenhouse.io', 'form_ready', 'greenhouse', []),
    ('lever-link.html', 'fixture.lever.co', 'form_ready', 'lever', ['Apply for this job']),
    ('workday-manual.html', 'fixture.myworkdayjobs.com', 'form_ready', 'workday', ['Apply', 'Decline', 'Apply Manually']),
    ('workday-account.html', 'fixture.myworkdayjobs.com', 'account_required', 'workday', ['Apply', 'Apply Manually']),
    ('newsletter-overlay.html', 'fixture.example.test', 'form_ready', 'generic', ['No thanks', 'Apply now']),
    ('chat-overlay.html', 'fixture.example.test', 'form_ready', 'generic', ['Close', 'Apply']),
    ('removed.html', 'fixture.example.test', 'closed', 'generic', []),
    ('security-challenge.html', 'fixture.example.test', 'security_challenge', 'generic', []),
    ('application-dialog.html', 'fixture.example.test', 'form_ready', 'generic', []),
    ('scripted-submission.html', 'fixture.example.test', 'entry_failed', 'generic', []),
    ('submit-entry.html', 'fixture.example.test', 'entry_failed', 'generic', []),
]


@pytest.fixture
def fixture_server():
    sent = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = Path(urlsplit(self.path).path).name
            target = FIXTURES / name
            if not target.is_file():
                self.send_error(404)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def mutation(self):
            sent.append((self.command, self.path))
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        do_POST = do_PUT = do_PATCH = do_DELETE = mutation

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', sent
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def entry_page(fixture_server, monkeypatch):
    # Shorten only observation budgets, retaining the production eight-second grace.
    monkeypatch.setattr('autoapply.applications.page_policy', lambda page: PatiencePolicy(timeout=8, extension=0))
    monkeypatch.setattr(WorkdayAdapter, 'readiness_timeout', 8)

    forbidden = AsyncMock(side_effect=AssertionError('Entry probe called a field/step operation'))
    for method in ('get_questions', 'upload_documents', 'advance'):
        monkeypatch.setattr(GenericApplicationAdapter, method, forbidden)

    base, _ = fixture_server
    allowed_hosts = {case[1] for case in CASES}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers='block', viewport={'width': 1440, 'height': 900})

        async def offline(route):
            # ATS hostnames exercise adapter_for unchanged. Transport is ALWAYS
            # rewritten to loopback, including styles/scripts and form navigation.
            parts = urlsplit(route.request.url)
            if parts.hostname not in allowed_hosts:
                await route.abort('blockedbyclient')
                return
            response = await route.fetch(url=base + parts.path, max_redirects=0)
            await route.fulfill(response=response)

        await context.route('**/*', offline)
        page = await context.new_page()
        page._fixture_errors = []
        page.on('pageerror', lambda error: page._fixture_errors.append(str(error)))
        yield page
        await context.close()
        await browser.close()
    forbidden.assert_not_awaited()


@pytest.mark.browser
@pytest.mark.parametrize('name,host,outcome,ats,clicks', CASES, ids=[case[0] for case in CASES])
async def test_entry_fixtures(entry_page, fixture_server, name, host, outcome, ats, clicks):
    result = await probe_entry(entry_page, f'https://{host}/entry/{name}')
    assert not entry_page._fixture_errors, entry_page._fixture_errors
    assert result.outcome == outcome, json.dumps(asdict(result))
    assert result.ats == ats
    assert result.steps[0] == 'submission_guard_installed'
    assert result.steps[-1] == 'outcome:' + outcome
    if outcome == 'form_ready':
        assert result.field_count > 0
        assert result.error is None
    audit = await entry_page.evaluate('window.audit')
    assert audit['clicks'] == clicks
    assert audit['accept'] == audit['submit'] == audit['input'] == 0
    assert await entry_page.locator('input,textarea,select').evaluate_all(
        'es => es.every(e => !e.value && (!e.files || e.files.length===0))')
    assert fixture_server[1] == []  # zero mutation requests reached HTTP server
    if name == 'scripted-submission.html':
        assert await entry_page.evaluate('window.attempts') == ['aborted'] * 5
        assert result.steps.count('final_submission_aborted') == 5
        assert result.error == 'FINAL_SUBMISSION_BLOCKED'
    if name == 'application-dialog.html':
        assert await entry_page.locator('#application').is_visible()
    if name == 'workday-manual.html':
        assert 'WORKDAY_ENTRY_MODE_SELECTED' in result.steps
        assert 'overlay_dismissed' not in result.steps


@pytest.mark.browser
@pytest.mark.parametrize('content', [
    '<form><label>First Name<input name="first_name"></label></form>',
    '<input hidden name="application_id">',
    '<textarea></textarea>',
    '<div contenteditable="true"></div>',
    '<h2>Start Your Application</h2><button type="button">Autofill with Resume</button><button type="button">Apply Manually</button><button type="button">Use My Last Application</button>',
    '<button type="button">Subscribe</button>',
    '<p>We use cookies</p>',
])
async def test_overlay_never_closes_inputs_choosers_or_non_close_actions(entry_page, content):
    await entry_page.set_content(f'<div role="dialog" aria-modal="true">{content}<button type="button">Close</button></div>')
    click = AsyncMock()
    assert not await dismiss_overlay(entry_page, click)
    click.assert_not_awaited()
    assert await entry_page.get_by_role('dialog').is_visible()


def listing_database(root):
    private = root / 'data/private'
    private.mkdir(parents=True)
    path = private / 'autoapply.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE jobs (id INTEGER,company TEXT,title TEXT,canonical_url TEXT,priority REAL,listing_active INTEGER,listing_status TEXT)')
        connection.executemany('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', [
            (1, 'Example', 'Intern', 'https://fixture.example.test/1', 5, 1, 'ACTIVE'),
            (2, 'Example', 'Intern', 'https://fixture.example.test/2', 10, 1, 'ACTIVE'),
            (3, 'Example', 'Intern', 'https://fixture.example.test/3', 100, 0, 'CLOSED'),
        ])
    return path


def test_read_only_listing_selection(tmp_path):
    path = listing_database(tmp_path)
    before = path.read_bytes()
    assert [row['id'] for row in select_listings(tmp_path, limit=1)] == [2]
    assert [row['id'] for row in select_listings(tmp_path, ids=[1, 3])] == [1]
    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == {path}
    with pytest.raises(ValueError, match='positive'):
        select_listings(tmp_path, limit=0)


async def test_empty_cli_selection_writes_report_without_browser(tmp_path, monkeypatch):
    path = listing_database(tmp_path)
    before = path.read_bytes()

    def forbidden():
        raise AssertionError('Empty selection started a browser')

    monkeypatch.setattr('playwright.async_api.async_playwright', forbidden)
    report = await run_entry_test(tmp_path, ids=[3])
    assert report['listings'] == []
    assert json.loads((path.parent / 'entry-test-report.json').read_text()) == report
    assert path.read_bytes() == before


@pytest.mark.parametrize('fail', [False, True])
async def test_cli_temporary_profile_report_and_cleanup(tmp_path, monkeypatch, fail):
    path = listing_database(tmp_path)
    before = path.read_bytes()
    # Sentinels prove the operator profile and lock are neither used nor rewritten.
    profile = path.parent / 'browser_profile'
    profile.mkdir()
    (profile / 'cookies').write_text('operator cookies')
    lock = path.parent / 'worker.lock'
    lock.write_text('operator lock')
    launched = []
    context = SimpleNamespace(route=AsyncMock(), set_default_timeout=lambda _: None,
                              new_page=AsyncMock(), close=AsyncMock(), pages=[])
    page = SimpleNamespace(close=AsyncMock())
    context.new_page.return_value = page
    context.pages = [page]

    async def launch(profile_path, **options):
        assert Path(profile_path).is_dir()
        assert Path(profile_path) != profile
        assert options['service_workers'] == 'block'
        assert options['headless'] is False
        assert options['accept_downloads'] is False
        launched.append(Path(profile_path))
        return context

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        async def __aexit__(self, *args):
            pass

    probe = AsyncMock(side_effect=RuntimeError('fixture failure') if fail else None,
                      return_value=EntryResult('form_ready', ['page_opened', 'outcome:form_ready'], 2))
    monkeypatch.setattr('playwright.async_api.async_playwright', Playwright)
    monkeypatch.setattr('autoapply.entry_harness.probe_entry', probe)
    if fail:
        with pytest.raises(RuntimeError, match='fixture failure'):
            await run_entry_test(tmp_path, ids=[1], headed=True)
    else:
        report = await run_entry_test(tmp_path, ids=[1], headed=True)
        saved = json.loads((path.parent / 'entry-test-report.json').read_text())
        assert saved == report
        assert saved['auto_submit'] is False
        assert saved['entry_only'] is True
        assert saved['listings'][0]['id'] == 1
        assert saved['listings'][0]['steps'] == ['page_opened', 'outcome:form_ready']
    context.close.assert_awaited_once()
    page.close.assert_awaited_once()
    assert launched and all(not directory.exists() for directory in launched)
    assert path.read_bytes() == before
    assert lock.read_text() == 'operator lock'
    assert (profile / 'cookies').read_text() == 'operator cookies'


def test_entry_help_needs_no_private_startup():
    result = subprocess.run([sys.executable, '-m', 'autoapply', 'entry-test', '--help'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert all(flag in result.stdout for flag in ('--limit', '--ids', '--headed'))


def test_main_entry_bypasses_config_database_and_locks(tmp_path, monkeypatch):
    import autoapply.__main__ as main

    def forbidden(*args, **kwargs):
        raise AssertionError('Entry CLI accessed normal worker startup')

    monkeypatch.setattr(main, 'Config', forbidden)
    monkeypatch.setattr(main, 'Database', forbidden)
    monkeypatch.setattr(main, 'ProcessLock', forbidden)
    run = AsyncMock(return_value={'listings': []})
    monkeypatch.setattr('autoapply.entry_harness.run_entry_test', run)
    monkeypatch.setattr(sys, 'argv', ['autoapply', '--root', str(tmp_path), 'entry-test', '--limit', '2', '--ids', '1', '--headed'])
    assert main.main() == 0
    run.assert_awaited_once_with(str(tmp_path), limit=2, ids=[1], headed=True)
