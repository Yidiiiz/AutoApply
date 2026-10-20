"""Offline dashboard tests: temporary storage, in-memory HTTP, stubbed probes/scans."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.client
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from autoapply.__main__ import execute, parser
from autoapply.database import Database
from autoapply.dashboard import (AUTOMATION_FAILURE_CATEGORIES, Dashboard, DashboardHandler,
                                 DashboardServer, PAGE, PROTECTED_APPLICATIONS)
from autoapply.fill_batch import discover, EXCLUDED
from autoapply.models import now
from autoapply.runtime import ProcessLock
from autoapply import cv, descriptions


@pytest.fixture(autouse=True)
def offline_descriptions(monkeypatch):
    monkeypatch.setattr(descriptions, 'fetch_description', lambda url, timeout=15: '')


def queue(db, config, listing, count):
    ids = []
    for i in range(count):
        job_id, _ = db.ingest(replace(listing, company=f'Company {i}',
                                     url=f'https://example.test/jobs/{i}'), config)
        ids.append(db.one('SELECT id FROM applications WHERE job_id=?', (job_id,))['id'])
    return ids


@pytest.fixture
def dashboard(config, db):
    value = Dashboard(config, db)
    yield value
    value.close()


@pytest.fixture
def http_dashboard(dashboard):
    # Exercise the actual handler without opening even a loopback socket.
    server = DashboardServer.__new__(DashboardServer)
    server.dashboard = dashboard
    server.server_port = 8765
    server.server_address = ('127.0.0.1', server.server_port)
    yield server, dashboard


class MemorySocket:
    def __init__(self, data):
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


def request(server, method, path, headers=None, body=None):
    headers = dict(headers or {})
    headers.setdefault('Host', f'127.0.0.1:{server.server_port}')
    payload = body.encode('utf-8') if isinstance(body, str) else body or b''
    if body is not None:
        headers.setdefault('Content-Length', str(len(payload)))
    raw = f'{method} {path} HTTP/1.1\r\n'
    raw += ''.join(f'{key}: {value}\r\n' for key, value in headers.items())
    socket = MemorySocket(raw.encode('latin-1') + b'\r\n' + payload)
    DashboardHandler(socket, ('127.0.0.1', 1), server)
    response = http.client.HTTPResponse(MemorySocket(socket.output.getvalue()), method=method)
    try:
        response.begin()
        body = response.read()
        content_type = response.getheader('Content-Type')
        if content_type == 'application/pdf':
            value = body
        else:
            body = body.decode('utf-8')
            value = body if content_type.startswith('text/html') else json.loads(body)
        return response.status, value, dict(response.getheaders())
    finally:
        response.close()


def finish_refresh(dashboard, job_id):
    dashboard._refresh_thread.join(timeout=5)
    job = dashboard.refresh(job_id)
    assert job['status'] in {'succeeded', 'failed'}, job
    return job


@pytest.fixture
def writing_provider(config, monkeypatch):
    config.data['ai'].update(enabled=True, providers=[{'name': 'fixture', 'type': 'codex_cli', 'billing': 'included'}])
    class FakeProvider:
        def __init__(self, *args):
            self.requests = []
            self.result = {'paragraphs': ['I am applying for the internship.', 'I study computer science.',
                                          'Thank you for considering my application.']}
        async def generate_response(self, payload):
            self.requests.append(payload)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
    provider = FakeProvider()
    monkeypatch.setattr('autoapply.codex_writer.CodexWritingProvider', lambda *args: provider)
    return provider


@pytest.mark.parametrize('fetched', ['', 'Bachelor students build Python services. ' * 12])
def test_cover_letter_fetches_missing_description(dashboard, config, db, listing, monkeypatch,
                                                 writing_provider, fetched):
    app_id = queue(db, config, replace(listing, description=''), 1)[0]
    before = db.application(app_id)
    def fetch(url):
        assert url == before['canonical_url']
        # A concurrent writer can acquire a lock while the HTTP call is in flight.
        with dashboard.connection(writable=True) as other:
            with other.transaction():
                pass
        return fetched
    fetcher = Mock(side_effect=fetch)
    monkeypatch.setattr(descriptions, 'fetch_description', fetcher)
    monkeypatch.setattr(cv, 'render_pdf', lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    assert dashboard.generate_cv(app_id)['file'] == '/api/cv/' + str(app_id)
    fetcher.assert_called_once_with(before['canonical_url'])
    assert writing_provider.requests[-1]['job']['description'] == fetched.strip()
    assert db.application(app_id) == dict(before, description=fetched)
    if fetched:
        dashboard.generate_cv(app_id)
        fetcher.assert_called_once()


@pytest.mark.parametrize('output,expected', [
    ({'questions': ['Paste the JOB DESCRIPTION', 'When can you start?']}, {'questions': ['When can you start?']}),
    ({'questions': ['Can you share the posting?']}, None),
    ({'questions': ['Paste the posting'], 'paragraphs': ['I study CS.']}, 'letter'),
])
def test_cover_letter_filters_posting_questions(http_dashboard, config, db, listing, monkeypatch,
                                              writing_provider, output, expected):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    writing_provider.result = output
    monkeypatch.setattr(cv, 'render_pdf', lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                                json.dumps({'application_id': app_id}))
    if expected is None:
        assert status == 400 and 'retry' in result['error']
    elif expected == 'letter':
        assert status == 200 and 'file' in result and 'questions' not in result
    else:
        assert status == 200 and result == expected
    assert len(writing_provider.requests) == 1


def test_cover_letter_questions_answers_persist_and_reuse(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    server, dashboard = http_dashboard
    app_id, other_id = queue(db, config, listing, 2)
    headers = run_headers(server, dashboard)
    writing_provider.result = {'questions': ['Why this role?', 'When can you start?']}
    renderer = Mock(side_effect=lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    monkeypatch.setattr(cv, 'render_pdf', renderer)
    body = {'application_id': app_id}
    assert request(server, 'POST', '/api/cv', headers, json.dumps(body))[:2] == (200, writing_provider.result)
    renderer.assert_not_called()
    body['answers'] = [{'question': 'Why this role?', 'answer': 'The listed Python work.'},
                       {'question': 'When can you start?', 'answer': 'June 2027.'}]
    writing_provider.result = {'paragraphs': ['I study CS.', 'I want to contribute.', 'Thank you.']}
    assert request(server, 'POST', '/api/cv', headers, json.dumps(body))[0] == 200
    assert writing_provider.requests[-1]['answers'] == body['answers']
    saved = config.private / f'documents/cv/{app_id}-company-0.answers.json'
    assert json.loads(saved.read_text(encoding='utf-8')) == body['answers']
    assert request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': app_id}))[0] == 200
    assert writing_provider.requests[-1]['answers'] == body['answers']
    corrected = [{'question': 'When can you start?', 'answer': 'July 2027.'}]
    assert request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': app_id, 'answers': corrected}))[0] == 200
    assert writing_provider.requests[-1]['answers'][-1] == corrected[0]
    assert request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': other_id}))[0] == 200
    assert writing_provider.requests[-1]['answers'] == []


@pytest.mark.parametrize('output', [None, {'paragraphs': []}, {'paragraphs': [' ']}, {'paragraphs': [5]},
                                    {'questions': ['Q'] * 5}, {'questions': []}, {'answer': 'Resume'}])
def test_cover_letter_invalid_output_is_error(http_dashboard, config, db, listing, monkeypatch, writing_provider, output):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    writing_provider.result = output
    renderer = Mock()
    monkeypatch.setattr(cv, 'render_pdf', renderer)
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard), json.dumps({'application_id': app_id}))
    assert status == 400 and 'Cover letter provider returned invalid output' in result['error']
    renderer.assert_not_called()
    assert not (config.private / 'documents/cv').exists()


@pytest.mark.parametrize('enabled', [False, True])
def test_cover_letter_ai_disabled_or_unconfigured(http_dashboard, config, db, listing, monkeypatch, enabled):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    config.data['ai'].update(enabled=enabled, providers=[])
    provider = Mock(side_effect=AssertionError('must not invoke provider'))
    monkeypatch.setattr('autoapply.codex_writer.CodexWritingProvider', provider)
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard), json.dumps({'application_id': app_id}))
    assert status == 409 and result['error'] == 'Cover letter writing needs an AI provider in Settings'
    provider.assert_not_called()


def test_cover_letter_unavailable_provider(http_dashboard, config, db, listing, writing_provider):
    from autoapply.ai import ProviderUnavailable
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    writing_provider.result = ProviderUnavailable('fixture login unavailable')
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard), json.dumps({'application_id': app_id}))
    assert status == 409 and result['error'] == 'Cover letter writing unavailable: fixture login unavailable'


@pytest.mark.parametrize('answers', ['text', {}, [None], [{'question': ' ', 'answer': ''}],
                                   [{'question': 'Q', 'answer': 1}], [{'question': 'Q', 'answer': None}]])
def test_cover_letter_invalid_answers(http_dashboard, config, db, listing, writing_provider, answers):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                                json.dumps({'application_id': app_id, 'answers': answers}))
    assert status == 400 and 'Cover letter answers' in result['error']
    assert writing_provider.requests == []


@pytest.mark.parametrize('blank', ['', '   '])
def test_cover_letter_skipped_answers_persist_and_reuse(http_dashboard, config, db, listing,
                                                       monkeypatch, writing_provider, blank):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    monkeypatch.setattr(cv, 'render_pdf', lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    headers = run_headers(server, dashboard)
    body = {'application_id': app_id, 'answers': [{'question': 'When can you start?', 'answer': blank}]}
    assert request(server, 'POST', '/api/cv', headers, json.dumps(body))[0] == 200
    saved = [{'question': 'When can you start?', 'answer': ''}]
    path = config.private / f'documents/cv/{app_id}-company-0.answers.json'
    assert json.loads(path.read_text(encoding='utf-8')) == saved
    assert request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': app_id}))[0] == 200
    assert writing_provider.requests[-1]['answers'] == saved
    assert writing_provider.requests[-1]['skipped_questions'] == ['When can you start?']


@pytest.mark.parametrize('extra,expected', [({'salutation': 'Dear Ms. Lee,'}, 'Dear Ms. Lee,'),
    ({}, 'Dear Company 0 Hiring Team,'), ({'salutation': 'Hello Ms. Lee,'}, 'Dear Company 0 Hiring Team,'),
    ({'salutation': ['Dear Ms. Lee,']}, 'Dear Company 0 Hiring Team,')])
def test_cover_letter_optional_salutation(http_dashboard, config, db, listing, monkeypatch,
                                          writing_provider, extra, expected):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    writing_provider.result.update(extra)
    renderer = Mock(side_effect=lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    monkeypatch.setattr(cv, 'render_pdf', renderer)
    assert request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                   json.dumps({'application_id': app_id}))[0] == 200
    html = renderer.call_args.args[0]
    assert '<p>' + expected + '</p>' in html
    assert '<p>Company 0 Hiring Team<br>Company 0</p>' in html


def test_cover_letter_job_entities_reach_provider_and_letter(http_dashboard, config, db, listing,
                                                            monkeypatch, writing_provider):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    db.conn.execute('''UPDATE jobs SET company=?,title=?,location=?,description=?
                       WHERE id=(SELECT job_id FROM applications WHERE id=?)''',
                    ('A &amp; B', 'Platform &amp; SRE', 'City &amp; State', 'Use &lt;tools&gt; &amp;amp; more', app_id))
    db.conn.commit()
    renderer = Mock(side_effect=lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    monkeypatch.setattr(cv, 'render_pdf', renderer)
    assert request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                   json.dumps({'application_id': app_id}))[0] == 200
    assert writing_provider.requests[-1]['job'] == {'company': 'A & B', 'title': 'Platform & SRE',
                                                  'location': 'City & State', 'description': 'Use <tools> &amp; more'}
    assert '<p>Dear A &amp; B Hiring Team,</p>' in renderer.call_args.args[0]


def test_cv_generate_preview_and_preserves_state(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    profile = config.private / 'profile.yaml'
    before_profile = profile.read_bytes()
    before_app = db.application(app_id)
    before_settings = db.rows('SELECT * FROM settings')
    before_events = db.rows('SELECT * FROM events')
    pdf = b'%PDF-1.4\nfixture CV\n%%EOF'
    renderer = Mock(side_effect=lambda html, path: path.write_bytes(pdf))
    monkeypatch.setattr(cv, 'render_pdf', renderer)
    headers = run_headers(server, dashboard)
    path = '/api/cv/' + str(app_id)
    assert request(server, 'GET', path, headers)[0] == 404
    status, result, _ = request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': app_id}))
    assert status == 200 and result == {'file': path, 'warnings': []}
    renderer.assert_called_once()
    folder = config.private / 'documents/cv'
    assert (folder / f'{app_id}-company-0.pdf').read_bytes() == pdf
    assert 'Test Student' in (folder / f'{app_id}-company-0.html').read_text(encoding='utf-8')
    status, body, response_headers = request(server, 'GET', path + '?token=' + dashboard.token)
    assert status == 200 and body == pdf
    assert response_headers['Content-Type'] == 'application/pdf'
    assert response_headers['Content-Disposition'] == "inline; filename*=UTF-8''Test%20Student%20-%20Cover%20Letter%20-%20Company%200.pdf"
    assert response_headers['Cache-Control'] == 'no-store'
    # Top-level preview navigation carries the query token, not a custom header or Origin.
    navigation = {'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
    assert request(server, 'GET', path + '?token=' + dashboard.token, navigation)[:2] == (200, pdf)
    assert request(server, 'GET', path, headers)[1] == pdf
    assert db.application(app_id) == before_app
    assert db.rows('SELECT * FROM settings') == before_settings
    assert db.rows('SELECT * FROM events') == before_events
    assert profile.read_bytes() == before_profile


def test_cv_latest_and_html_fallback(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    monkeypatch.setattr(cv, 'render_pdf', lambda html, path: path.write_bytes(b'%PDF-1.4 old'))
    dashboard.generate_cv(app_id)
    monkeypatch.setattr(cv, 'render_pdf', Mock(side_effect=RuntimeError('Chromium unavailable')))
    status, result, _ = request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                                json.dumps({'application_id': app_id}))
    assert status == 200 and 'PDF rendering failed' in result['warnings'][-1]
    status, body, headers = request(server, 'GET', result['file'] + '?token=' + dashboard.token)
    assert status == 200 and 'Sincerely,' in body and '<h2>Education</h2>' not in body
    assert headers['Content-Type'].startswith('text/html') and 'sandbox' in headers['Content-Security-Policy']
    assert not (config.private / f'documents/cv/{app_id}-company-0.pdf').exists()


@pytest.mark.browser
def test_cv_click_navigates_popup_offline(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    """Route every browser request through the in-memory handler; never open a socket."""
    from playwright.sync_api import sync_playwright

    executable = next((path for path in (
        Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if path.is_file()), None)
    if executable is None:
        pytest.skip('System Chromium is unavailable for offline popup verification')
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        try:
            context = browser.new_context()
            context.set_default_timeout(10000)
            fixture = context.new_page()
            fixture.set_content('<p>Temporary fixture CV</p>')
            pdf = fixture.pdf()
            fixture.close()
            monkeypatch.setattr(cv, 'render_pdf', lambda html, path: path.write_bytes(pdf))
            navigations = []

            def respond(route):
                incoming = route.request
                path = incoming.url.removeprefix('http://127.0.0.1:8765')
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort()
                    return
                # Match the real server's request thread, outside Playwright's event loop.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    status, body, headers = pool.submit(request, server, incoming.method, path,
                                                       incoming.all_headers(), incoming.post_data).result()
                if path.startswith('/api/cv/'):
                    navigations.append((incoming.is_navigation_request(), status, body))
                payload = body if isinstance(body, (str, bytes)) else json.dumps(body)
                route.fulfill(status=status, headers=headers, body=payload)

            context.route('**/*', respond)
            page = context.new_page()
            page.goto('http://127.0.0.1:8765/')
            page.locator('#tab-priority').click()
            writing_provider.result = {'questions': ['Why this role?', 'When can you start?']}
            page.get_by_role('button', name=f'Generate Cover letter for #{app_id}', exact=True).click()
            dialog = page.get_by_role('dialog', name='Cover letter questions')
            dialog.wait_for()
            assert len(context.pages) == 1 and navigations == []
            assert dialog.get_by_role('button', name='Generate', exact=True).is_enabled()
            assert page.get_by_label('When can you start?').get_attribute('required') is None
            page.get_by_label('Why this role?').fill('The Python work described in the posting.')
            writing_provider.result = {'paragraphs': ['I study CS.', 'I want to contribute.', 'Thank you.']}
            with page.expect_popup() as opened:
                dialog.get_by_role('button', name='Generate', exact=True).click()
            preview = opened.value
            preview.wait_for_url(f'http://127.0.0.1:8765/api/cv/{app_id}?token=*')
            assert navigations == [(True, 200, pdf)]
            assert page.get_by_text('Cover letter generated', exact=True).is_visible()
            assert writing_provider.requests[-1]['answers'] == [
                {'question': 'Why this role?', 'answer': 'The Python work described in the posting.'},
                {'question': 'When can you start?', 'answer': ''}]
            assert writing_provider.requests[-1]['skipped_questions'] == ['When can you start?']
            writing_provider.result = {'questions': ['Anything else?']}
            page.get_by_role('button', name=f'Generate Cover letter for #{app_id}', exact=True).click()
            dialog.wait_for()
            assert len(context.pages) == 2
            dialog.get_by_role('button', name='Cancel', exact=True).click()
            dialog.wait_for(state='detached')
            assert len(context.pages) == 2
            monkeypatch.setattr(dashboard, 'generate_cv', Mock(side_effect=RuntimeError('fixture failure')))
            page.get_by_role('button', name=f'Generate Cover letter for #{app_id}', exact=True).click()
            page.locator('#toast-message').filter(has_text='RuntimeError').wait_for()
            assert len(context.pages) == 2
        finally:
            browser.close()


@pytest.mark.parametrize('bad_id', [None, True, '1', 0, -1, 1.5, 2**63])
def test_cv_post_invalid_ids(http_dashboard, bad_id):
    server, dashboard = http_dashboard
    assert request(server, 'POST', '/api/cv', run_headers(server, dashboard),
                   json.dumps({'application_id': bad_id}))[0] == 400


@pytest.mark.parametrize('body', ['[]', 'null', '{', '{}'])
def test_cv_post_invalid_body(http_dashboard, body):
    server, dashboard = http_dashboard
    assert request(server, 'POST', '/api/cv', run_headers(server, dashboard), body)[0] == 400


@pytest.mark.parametrize('value,status', [('no', 400), ('../1', 400), ('0', 400),
                                         (str(2**63), 400), ('999999', 404), ('3556', 403)])
def test_cv_get_validation_and_post_unknown_protected(http_dashboard, value, status):
    server, dashboard = http_dashboard
    headers = run_headers(server, dashboard)
    assert request(server, 'GET', '/api/cv/' + value, headers)[0] == status
    if status in (403, 404):
        assert request(server, 'POST', '/api/cv', headers, json.dumps({'application_id': int(value)}))[0] == status


@pytest.mark.parametrize('method,path,headers', [
    ('POST', '/api/cv', {}), ('POST', '/api/cv', {'Origin': 'https://evil.test'}),
    ('GET', '/api/cv/1', {}), ('GET', '/api/cv/1?token=wrong', {}),
    ('GET', '/api/cv/1', {'X-Dashboard-Token': 'wrong'}),
    ('GET', '/api/cv/1', {'Sec-Fetch-Site': 'cross-site'}),
    ('GET', '/api/cv/1', {'Host': 'evil.test'}),
])
def test_cv_token_origin_and_host(http_dashboard, method, path, headers):
    server, dashboard = http_dashboard
    if 'Origin' in headers or 'Sec-Fetch-Site' in headers:
        headers = dict(headers, **{'X-Dashboard-Token': dashboard.token})
    assert request(server, method, path, headers, '{}' if method == 'POST' else None)[0] == 403
    assert request(server, 'GET', '/api/cv/1?token=' + dashboard.token + '&token=' + dashboard.token)[0] == 403


def test_cv_rendering_does_not_block_other_requests(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    entered, release = threading.Event(), threading.Event()
    results = []
    def render(html, path):
        entered.set()
        assert release.wait(5)
        path.write_bytes(b'%PDF-1.4 fixture')
    monkeypatch.setattr(cv, 'render_pdf', render)
    headers = run_headers(server, dashboard)
    body = json.dumps({'application_id': app_id})
    thread = threading.Thread(target=lambda: results.append(request(server, 'POST', '/api/cv', headers, body)))
    thread.start()
    try:
        assert entered.wait(5)
        assert request(server, 'GET', '/api/applied')[0] == 200
        assert request(server, 'GET', '/api/answers')[0] == 200
        assert request(server, 'POST', '/api/cv', headers, body)[0] == 409
    finally:
        release.set()
        thread.join(5)
    assert results[0][0] == 200


def test_priority_matches_discover_and_filters(dashboard, config, db, listing):
    ids = queue(db, config, listing, 13)
    with db.transaction():
        for app_id in ids:
            db.execute('UPDATE jobs SET priority=?,discovered_at=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
                       (20, '2026-01-01', app_id))
        db.execute('UPDATE jobs SET priority=30 WHERE id=(SELECT job_id FROM applications WHERE id=?)', (ids[2],))
        db.execute('UPDATE jobs SET discovered_at=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
                   ('2025-01-01', ids[1]))
        db.execute("UPDATE applications SET status='RETRY' WHERE id=?", (ids[1],))
        db.execute('UPDATE applications SET retry_allowed=0 WHERE id=?', (ids[3],))
        db.execute('UPDATE applications SET manual_action_required=1 WHERE id=?', (ids[4],))
        db.execute("UPDATE applications SET security_state='CAPTCHA_DETECTED' WHERE id=?", (ids[5],))
        db.execute('UPDATE applications SET submit_intent_at=? WHERE id=?', (now(), ids[6]))
        db.execute('UPDATE applications SET submission_confirmation_seen=1 WHERE id=?', (ids[7],))
        db.execute('UPDATE applications SET retry_at=? WHERE id=?',
                   ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), ids[8]))
        db.execute('UPDATE jobs SET listing_active=0 WHERE id=(SELECT job_id FROM applications WHERE id=?)', (ids[9],))
        db.execute('UPDATE jobs SET posted_at=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
                   ((datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), ids[10]))
        db.execute("UPDATE jobs SET location='Toronto, Canada' WHERE id=(SELECT job_id FROM applications WHERE id=?)", (ids[11],))
        db.set_setting('duplicate_submission_guard:' + str(ids[12]), True)
    expected = discover(db, config)
    assert expected == [ids[2], ids[1], ids[0]]
    rows = dashboard.priority()['rows']
    assert [r['application_id'] for r in rows] == expected
    assert [r['rank'] for r in rows] == [1, 2, 3]
    assert all(r['listing_status'] == 'ACTIVE' and r['last_liveness_at'] is None for r in rows)
    before = db.conn.total_changes
    dashboard.priority()
    assert db.conn.total_changes == before


def test_priority_ranks_reconcile_after_manual_mark_and_put_back(dashboard, config, db, listing):
    ids = queue(db, config, listing, 3)
    before = dashboard.priority()['rows']
    removed = before[1]['application_id']
    dashboard.mark_applied(removed)
    remaining = dashboard.priority()['rows']
    assert [row['rank'] for row in remaining] == [1, 2]
    assert [row['application_id'] for row in remaining] == [before[0]['application_id'], before[2]['application_id']]
    assert all(row['updated_at'] for row in remaining)
    assert len(dashboard.applied()['applications']) == 1
    dashboard.mark_applied(removed, undo=True)
    restored = dashboard.priority()['rows']
    assert [row['rank'] for row in restored] == [1, 2, 3]
    assert {row['application_id'] for row in restored} == set(ids)
    assert dashboard.applied()['applications'] == []


def test_priority_cache_and_bulk_details(dashboard, config, db, listing, monkeypatch):
    from autoapply.dashboard import _Connection
    ids = queue(db, config, listing, 3)
    selector = Mock(wraps=discover)
    monkeypatch.setattr('autoapply.dashboard.discover', selector)
    original_one = _Connection.one
    def no_row_lookup(self, sql, args=()):
        assert 'WHERE a.id=?' not in sql, 'per-row detail query'
        assert 'SELECT value FROM settings WHERE key=?' not in sql, 'per-candidate settings query'
        return original_one(self, sql, args)
    monkeypatch.setattr(_Connection, 'one', no_row_lookup)
    first = dashboard.priority()['rows']
    assert [r['application_id'] for r in first] == ids
    first[0]['title'] = 'mutated response'
    with dashboard._mutex:
        dashboard._observations[ids[0]] = dict(result='OPEN', checked_at=now())
    second = dashboard.priority()['rows']
    assert second[0]['title'] != 'mutated response'
    assert second[0]['last_liveness_result'] == 'OPEN'
    assert selector.call_count == 1
    db.execute('UPDATE jobs SET priority=999 WHERE id=(SELECT job_id FROM applications WHERE id=?)', (ids[-1],))
    assert [r['application_id'] for r in dashboard.priority()['rows']] == [ids[-1], *ids[:-1]]
    assert selector.call_count == 2
    config.data['jobs']['max_listing_age_days'] = 1
    dashboard.priority()
    assert selector.call_count == 3
    profile = config.private / 'profile.yaml'
    profile.write_text(profile.read_text(encoding='utf-8') + '\ncommon_answers: {fixture: changed}\n', encoding='utf-8')
    dashboard.priority()
    assert selector.call_count == 4
    dashboard._priority_at -= 31
    dashboard.priority()
    assert selector.call_count == 5


def test_dashboard_database_skips_history_maintenance(config, db, listing, monkeypatch):
    app_id = queue(db, config, listing, 1)[0]
    db.conn.execute('INSERT OR IGNORE INTO history_dirty VALUES (?)', (app_id,))
    pending = db.rows('SELECT * FROM history_dirty')
    with monkeypatch.context() as patch:
        patch.setattr('autoapply.archive.ApplicationHistory.validate', Mock(side_effect=AssertionError('validation')))
        patch.setattr('autoapply.archive.ApplicationHistory.sync', Mock(side_effect=AssertionError('export')))
        patch.setattr(Database, 'maintain_listings', Mock(side_effect=AssertionError('maintenance')))
        value = Database(config.private / 'test.sqlite3', startup_maintenance=False, history_maintenance=False)
        assert value.history_startup_report == value.listing_startup_report == {}
        assert value.rows('SELECT * FROM history_dirty') == pending
        value.close()
    assert db.rows('SELECT * FROM history_dirty') == pending


def test_dashboard_connections_share_history_cache(dashboard, config, db, listing, monkeypatch):
    first, second = queue(db, config, listing, 2)
    assert dashboard.history is db.history
    scan = Mock(wraps=dashboard.history._scan)
    write = Mock(wraps=dashboard.history._write)
    monkeypatch.setattr(dashboard.history, '_scan', scan)
    monkeypatch.setattr(dashboard.history, '_write', write)
    for app_id, text in ((first, 'First description'), (second, 'Second description')):
        with dashboard.connection(writable=True) as connection:
            assert connection.history is dashboard.history
            descriptions.store(connection, db.application(app_id)['job_id'], text * 20)
        folder = dashboard.history.paths[str(app_id)]
        assert json.loads((folder / 'application.json').read_text(encoding='utf-8'))['description'] == text * 20
        assert (folder / 'job_description.txt').read_text(encoding='utf-8') == text * 20
    assert scan.call_count == 0
    assert [call.args[0]['application_id'] for call in write.call_args_list] == [str(first), str(second)]
    assert db.rows('SELECT * FROM history_dirty') == []
    # Another exporter still invalidates the shared cache through the revision token.
    exporter = Database(config.private / 'test.sqlite3', startup_maintenance=False, history_maintenance=True)
    try:
        assert exporter.history is not dashboard.history
        descriptions.store(exporter, db.application(first)['job_id'], 'External description' * 30)
    finally:
        exporter.close()
    with dashboard.connection(writable=True) as connection:
        descriptions.store(connection, db.application(second)['job_id'], 'Latest description' * 30)
    assert scan.call_count == 1
    assert dashboard.history.records[str(first)]['description'] == 'External description' * 30


def test_dashboard_cli_disables_history_maintenance(config, monkeypatch):
    from autoapply.__main__ import main
    factory = Mock(return_value=Mock())
    monkeypatch.setattr('autoapply.__main__.Database', factory)
    monkeypatch.setattr('autoapply.__main__.Config', lambda root: config)
    monkeypatch.setattr('autoapply.__main__.setup_logging', lambda config: None)
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **kw: None)
    async def execute_dashboard(*args):
        return 0
    monkeypatch.setattr('autoapply.__main__.execute', execute_dashboard)
    monkeypatch.setattr(sys, 'argv', ['autoapply', '--root', str(config.root), 'dashboard', '--no-browser'])
    assert main() == 0
    assert factory.call_args.kwargs == dict(startup_maintenance=False, history_maintenance=False)


def protected_queue(db, listing, ids=PROTECTED_APPLICATIONS):
    with db.transaction():
        for app_id in ids:
            job_id = db.execute('''INSERT INTO jobs(identity_key,company,title,location,canonical_url,ats,
                description,posted_at,discovered_at,priority,status,listing_status,listing_active,freshness_state)
                VALUES (?,?,?,?,?,?,?,?,?,100,'QUEUED','ACTIVE',1,'FRESH')''',
                (f'protected-{app_id}', f'Protected {app_id}', listing.title, listing.location,
                 f'https://example.test/protected/{app_id}', 'generic', listing.description, now(), now())).lastrowid
            db.execute("INSERT INTO applications(id,job_id,status,updated_at) VALUES (?,?,'QUEUED',?)",
                       (app_id, job_id, now()))


def test_historical_exclusions_are_shared(dashboard, config, db, listing):
    protected_queue(db, listing, ids=EXCLUDED)
    assert discover(db, config) == []
    assert dashboard.priority()['rows'] == []


def test_liveness_retirement_and_noops(dashboard, config, db, listing, monkeypatch):
    ids = queue(db, config, listing, 4)
    results = ['CLOSED', 'REMOVED', 'OPEN', 'UNKNOWN']
    states = {db.application(i)['canonical_url']: result for i, result in zip(ids, results)}
    monkeypatch.setattr('autoapply.liveness.listing_state', states.__getitem__)
    before = {i: db.application(i) for i in ids}
    events = {i: db.rows('SELECT * FROM events WHERE application_id=?', (i,)) for i in ids}
    with ProcessLock(config.private / 'worker.lock'):
        dashboard.sweep()  # Sweeps do not acquire worker.lock.
    for app_id, result in zip(ids, results):
        app = db.application(app_id)
        if result in {'CLOSED', 'REMOVED'}:
            assert app['status'] == app['application_state'] == 'CLOSED'
            assert app['listing_status'] == result and app['listing_active'] == 0
            assert app['retry_allowed'] == 0
            assert app['attempts'] == before[app_id]['attempts']
            assert db.one("SELECT id FROM events WHERE application_id=? AND kind='CLOSED'", (app_id,))
            assert db.one("SELECT detail FROM events WHERE application_id=? AND kind='LISTING_LIVENESS_PROBE'", (app_id,))['detail'] == result
        else:
            assert app == before[app_id]
            assert db.rows('SELECT * FROM events WHERE application_id=?', (app_id,)) == events[app_id]
    data = dashboard.priority()
    assert [r['last_liveness_result'] for r in data['rows']] == ['OPEN', 'UNKNOWN']
    assert all(r['last_liveness_at'] for r in data['rows'])
    assert data['sweep']['finished_at']
    assert data['sweep']['counts'] == dict(checked=4, CLOSED=1, REMOVED=1, OPEN=1, UNKNOWN=1, retired=2, skipped=0, described=0)


def test_sweep_enriches_missing_descriptions_without_retiring_rejected_jobs(dashboard, config, db,
                                                                         listing, monkeypatch):
    first, second, full_id = queue(db, config, replace(listing, description=''), 3)
    ids = [first, second]
    db.execute('UPDATE jobs SET description=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
               ('Bachelor students build software. ' * 12, full_id))
    before = {i: db.application(i) for i in ids + [full_id]}
    assert len(dashboard.priority()['rows']) == 3  # Populate the cache before enrichment.
    texts = {before[ids[0]]['canonical_url']: 'phd students build research systems. ' * 12,
             before[ids[1]]['canonical_url']: 'Bachelor students build Python services. ' * 12}
    fetched = []
    def fetch(url):
        fetched.append(url)
        with dashboard.connection(writable=True) as other:
            with other.transaction():
                pass
        return texts[url]
    monkeypatch.setattr(descriptions, 'fetch_description', fetch)
    monkeypatch.setattr('autoapply.liveness.listing_state', lambda url: 'OPEN')
    retire = Mock(side_effect=AssertionError('Eligibility rejection must not retire a listing'))
    monkeypatch.setattr(dashboard, '_retire', retire)
    dashboard.sweep()
    assert set(fetched) == set(texts)
    for app_id in ids:
        assert db.application(app_id) == dict(before[app_id], description=texts[before[app_id]['canonical_url']])
    assert db.application(full_id) == before[full_id]
    data = dashboard.priority()
    assert data['sweep']['counts']['described'] == 2 and data['sweep']['error'] is None
    assert {row['application_id'] for row in data['rows']} == {ids[1], full_id}
    retire.assert_not_called()


def test_sweep_prefers_missing_descriptions(dashboard, config, db, listing, monkeypatch):
    full_id, missing_id = queue(db, config, replace(listing, description=''), 2)
    db.execute('UPDATE jobs SET description=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
               ('Bachelor students build software. ' * 12, full_id))
    monkeypatch.setattr('autoapply.dashboard.SWEEP_CAP', 1)
    fetcher = Mock(return_value='Bachelor students build Python services. ' * 12)
    monkeypatch.setattr(descriptions, 'fetch_description', fetcher)
    dashboard.sweep()
    fetcher.assert_called_once_with(db.application(missing_id)['canonical_url'])
    assert full_id not in dashboard._observations


def test_sweep_batches_writes_after_all_probes(dashboard, config, db, listing, monkeypatch):
    ids = queue(db, config, replace(listing, description=''), 3)
    states = {db.application(i)['canonical_url']: state for i, state in zip(ids, ('CLOSED', 'REMOVED', 'OPEN'))}
    fetched = set()
    probe_writer_lock = threading.Lock()
    writable_connections = []
    connection = dashboard.connection
    @contextmanager
    def tracked_connection(*, writable=False):
        if writable:
            assert fetched == set(states)
        with connection(writable=writable) as value:
            if writable:
                writes = []
                value.conn.set_trace_callback(writes.append)
                writable_connections.append(writes)
            yield value
    def fetch(url):
        assert not writable_connections
        # HTTP probes permit an independent writer; no sweep transaction is held.
        with probe_writer_lock:
            with sqlite3.connect(dashboard.path, timeout=0) as other:
                other.execute('BEGIN IMMEDIATE')
                other.rollback()
        fetched.add(url)
        return 'Public description for undergraduate students. ' * 10
    sync = Mock(wraps=dashboard.history.sync)
    monkeypatch.setattr(dashboard, 'connection', tracked_connection)
    monkeypatch.setattr(dashboard.history, 'sync', sync)
    monkeypatch.setattr(descriptions, 'fetch_description', fetch)
    monkeypatch.setattr('autoapply.liveness.listing_state', states.__getitem__)
    dashboard.sweep()
    assert dashboard._sweep['error'] is None
    assert len(writable_connections) == 1
    # One batch transaction and one archive flush, regardless of result count.
    assert sum(sql == 'BEGIN IMMEDIATE' for sql in writable_connections[0]) == 2
    sync.assert_called_once()
    assert set(sync.call_args.args[1]) == set(ids)
    assert dashboard._sweep['counts']['described'] == 3
    assert dashboard._sweep['counts']['retired'] == 2
    for app_id, state in zip(ids, ('CLOSED', 'CLOSED', 'QUEUED')):
        assert db.application(app_id)['status'] == state
        folder = dashboard.history.paths[str(app_id)]
        record = json.loads((folder / 'application.json').read_text(encoding='utf-8'))
        assert record['status'] == state and record['description'].startswith('Public description')


@pytest.mark.parametrize('error,message', [
    (sqlite3.OperationalError('database is locked'), 'Dashboard is busy, retry shortly'),
    (sqlite3.OperationalError('database is BUSY'), 'Dashboard is busy, retry shortly'),
    (sqlite3.OperationalError('no such table'), 'Dashboard request failed: OperationalError'),
    (RuntimeError('unexpected'), 'Dashboard request failed: RuntimeError'),
    (RuntimeError('locked'), 'Dashboard request failed: RuntimeError'),
])
@pytest.mark.parametrize('method,path,target', [
    ('GET', '/api/priority', 'priority'),
    ('POST', '/api/settings', 'settings.save'),
    ('PUT', '/api/answers', 'answers.save'),
    ('DELETE', '/api/answers/saved/fixture', 'answers.edit_saved'),
])
def test_api_unexpected_errors_return_json(http_dashboard, monkeypatch, error, message, method, path, target):
    server, dashboard = http_dashboard
    owner = dashboard
    parts = target.split('.')
    for part in parts[:-1]:
        owner = getattr(owner, part)
    monkeypatch.setattr(owner, parts[-1], Mock(side_effect=error))
    headers = {'Origin': 'http://127.0.0.1:8765', 'X-Dashboard-Token': dashboard.token}
    status, result, headers = request(server, method, path, headers, '{}')
    assert status == 503 and result == dict(error=message)
    assert headers['Content-Type'] == 'application/json; charset=utf-8'


def test_liveness_does_not_overwrite_worker_claim_or_protected(dashboard, config, db, listing, monkeypatch):
    app_id = queue(db, config, listing, 1)[0]
    protected_queue(db, listing)
    protected = {i: db.application(i) for i in PROTECTED_APPLICATIONS}
    probed = []

    def probe(url):
        probed.append(url)
        with dashboard.connection(writable=True) as other:
            other.lifecycle.transition(app_id, 'CHECKING')
        return 'REMOVED'

    monkeypatch.setattr('autoapply.liveness.listing_state', probe)
    dashboard.sweep()
    assert probed == [db.application(app_id)['canonical_url']]
    assert db.application(app_id)['status'] == 'CHECKING'
    assert db.application(app_id)['listing_status'] == 'ACTIVE'
    assert dashboard.priority()['sweep']['counts']['skipped'] == 1
    assert {i: db.application(i) for i in PROTECTED_APPLICATIONS} == protected


def test_liveness_cap_concurrency_and_rotation(dashboard, config, db, listing, monkeypatch):
    ids = queue(db, config, listing, 9)
    monkeypatch.setattr('autoapply.dashboard.SWEEP_CAP', 5)
    lock = threading.Lock()
    active = maximum = 0
    urls = []
    rendezvous = threading.Barrier(4)

    def probe(url):
        nonlocal active, maximum
        with lock:
            urls.append(url)
            n = len(urls)
            active += 1
            maximum = max(maximum, active)
        if n <= 4:
            rendezvous.wait(timeout=3)
        with lock:
            active -= 1
        return 'OPEN'

    monkeypatch.setattr('autoapply.liveness.listing_state', probe)
    dashboard.sweep()
    assert maximum == 4
    assert len(urls) == 5
    dashboard.sweep()
    assert len(urls) == 10
    assert len(set(urls)) == len(ids)
    assert dashboard.priority()['sweep']['counts']['checked'] == 5


def test_periodic_thread_and_interval_validation(dashboard, monkeypatch, config, db):
    called = threading.Event()
    monkeypatch.setattr(dashboard, 'sweep', called.set)
    dashboard.start()
    assert called.wait(timeout=3)
    assert dashboard.interval == 1800
    dashboard.close()
    assert not dashboard._sweep_thread.is_alive()
    for interval in (4.99, 0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError, match='at least 5'):
            Dashboard(config, db, liveness_minutes=interval)


def test_start_does_not_wait_for_first_sweep(dashboard, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def slow_sweep():
        started.set()
        assert release.wait(timeout=3)
    monkeypatch.setattr(dashboard, 'sweep', slow_sweep)
    try:
        dashboard.start()
        assert started.wait(timeout=1)
        assert not release.is_set()
    finally:
        release.set()
        dashboard.close()


def test_serve_starts_background_work_and_browser_after_serving(config, db, monkeypatch):
    from autoapply.dashboard import serve
    events, opened = [], threading.Event()
    class Server:
        server_port = 8765
        on_serving = None
        service_actions = DashboardServer.service_actions
        def __init__(self, *args):
            pass
        def serve_forever(self):
            events.append('serving')
            self.service_actions()
            self.service_actions()  # Startup callback runs only once.
            assert opened.wait(timeout=3)
        def server_close(self):
            events.append('closed')
    monkeypatch.setattr('autoapply.dashboard.DashboardServer', Server)
    monkeypatch.setattr(Dashboard, 'start', lambda self: events.append('start'))
    def open_browser(url):
        events.append('browser')
        assert url == 'http://127.0.0.1:8765/'
        opened.set()
    monkeypatch.setattr('autoapply.dashboard.webbrowser.open', open_browser)
    serve(config, db)
    assert events == ['serving', 'start', 'browser', 'closed']


def test_refresh_immediate_concurrency_and_new_rows(http_dashboard, config, db, listing, monkeypatch):
    server, dashboard = http_dashboard
    started, release = threading.Event(), threading.Event()

    async def fake_scan(scan_config, scan_db):
        assert scan_config is config
        assert scan_db.conn is not db.conn
        started.set()
        assert release.wait(timeout=5)
        queue(scan_db, config, listing, 1)
        return {'new': 1}

    monkeypatch.setattr('autoapply.dashboard.scan', fake_scan)
    headers = {'X-Dashboard-Token': dashboard.token, 'Origin': f'http://127.0.0.1:{server.server_port}'}
    try:
        status, job, _ = request(server, 'POST', '/api/refresh', headers)
        assert status == 202 and started.wait(timeout=3)
        assert request(server, 'GET', '/api/refresh/' + job['id'])[1]['status'] == 'running'
        assert request(server, 'POST', '/api/refresh', headers)[:2] == (409, {'error': 'refresh already running'})
        assert request(server, 'GET', '/api/priority')[0] == 200
        with pytest.raises(RuntimeError):
            with ProcessLock(config.private / 'worker.lock'):
                pass
    finally:
        release.set()
    assert finish_refresh(dashboard, job['id'])['result'] == {'new': 1}
    rows = request(server, 'GET', '/api/priority')[1]['rows']
    assert [r['application_id'] for r in rows] == discover(db, config)
    assert len(rows) == 1
    with ProcessLock(config.private / 'worker.lock'):
        pass


def test_refresh_worker_lock_refusal(http_dashboard, config, monkeypatch):
    server, dashboard = http_dashboard
    async def forbidden(*args):
        pytest.fail('A held worker lock must not scan')
    monkeypatch.setattr('autoapply.dashboard.scan', forbidden)
    with ProcessLock(config.private / 'worker.lock'):
        assert request(server, 'POST', '/api/refresh', run_headers(server, dashboard))[:2] == (409, {'error': 'worker running'})
    assert dashboard._active_refresh is None and dashboard._refreshes == {}


def test_refresh_failure_releases_lock_and_can_retry(dashboard, config, monkeypatch):
    async def fail(*args):
        raise ValueError('private details must not be returned')
    monkeypatch.setattr('autoapply.dashboard.scan', fail)
    first = dashboard.start_refresh()
    job = finish_refresh(dashboard, first)
    assert job['error'] == 'Discovery failed: ValueError'
    assert job['started_at'] and job['finished_at']
    with ProcessLock(config.private / 'worker.lock'):
        pass
    second = dashboard.start_refresh()
    assert second != first and finish_refresh(dashboard, second)['status'] == 'failed'


def test_scan_uses_engine_path_and_guards_reserved_records(dashboard, config, db, listing, monkeypatch):
    from autoapply.engine import Engine
    protected_queue(db, listing)
    before = {i: db.application(i) for i in PROTECTED_APPLICATIONS}
    jobs = {i: db.one('SELECT * FROM jobs WHERE id=?', (before[i]['job_id'],)) for i in before}
    events = {i: db.rows('SELECT * FROM events WHERE application_id=?', (i,)) for i in before}
    calls = []

    async def fake_source_scan(engine, force=False):
        calls.append(force)
        # Engine.scan maintenance and a source's bulk update cannot rewrite these.
        engine.db.execute("UPDATE applications SET stage='discovery',attempts=attempts+1,status='CLOSED'")
        for app_id in PROTECTED_APPLICATIONS:
            engine.db.mark_listing_closed(before[app_id]['job_id'], 'REMOVED')
            engine.db.event(app_id, 'CLOSED', 'must be ignored')
            engine.db.notify(f'test:{app_id}', {'application_id': app_id})
        queue(engine.db, config, listing, 1)
        return {'new': 1}

    monkeypatch.setattr(Engine, '_scan', fake_source_scan)
    job = finish_refresh(dashboard, dashboard.start_refresh())
    assert job['status'] == 'succeeded', job
    assert calls == [True]
    for app_id in PROTECTED_APPLICATIONS:
        assert db.application(app_id) == before[app_id]
        assert db.one('SELECT * FROM jobs WHERE id=?', (before[app_id]['job_id'],)) == jobs[app_id]
        assert db.rows('SELECT * FROM events WHERE application_id=?', (app_id,)) == events[app_id]
    assert db.rows('SELECT * FROM notifications') == []


@pytest.mark.parametrize('method,path,headers', [
    ('GET', '/', {'Host': 'evil.test'}),
    ('GET', '/api/priority', {'Host': '127.0.0.1.evil.test'}),
    ('POST', '/api/refresh', {}),
    ('POST', '/api/refresh', {'X-Dashboard-Token': 'incorrect'}),
    ('POST', '/api/refresh', {'X-Dashboard-Token': '\u00e9'}),
    ('POST', '/api/refresh', {'Origin': 'https://evil.test'}),
    ('POST', '/api/refresh', {'Origin': 'null'}),
    ('POST', '/api/refresh', {'Sec-Fetch-Site': 'cross-site'}),
])
def test_host_token_and_origin_rejection(http_dashboard, method, path, headers):
    server, dashboard = http_dashboard
    if 'Origin' in headers or 'Sec-Fetch-Site' in headers:
        headers = dict(headers, **{'X-Dashboard-Token': dashboard.token})
    assert request(server, method, path, headers)[0] == 403
    assert dashboard._refreshes == {}


def test_html_loads_queue_asynchronously_no_external_assets_or_unsafe_endpoints(http_dashboard, config, db, listing, monkeypatch):
    server, dashboard = http_dashboard
    queue(db, config, listing, 2)
    with monkeypatch.context() as patch:
        patch.setattr(dashboard, 'priority', Mock(side_effect=AssertionError('blocking queue read')))
        status, page, headers = request(server, 'GET', '/')
    assert status == 200
    assert server.server_address[0] == '127.0.0.1'
    assert dashboard.token in page and '__TOKEN__' not in page
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links=[]
            self.scripts=[]
        def handle_starttag(self,tag,attrs):
            if tag=='link': self.links.append(dict(attrs))
            if tag=='script': self.scripts.append(dict(attrs))
    assets=Assets()
    assets.feed(page)
    assert all('src' not in script for script in assets.scripts)
    assert len(assets.links)==1
    assert assets.links[0]['rel']=='icon' and assets.links[0]['type']=='image/svg+xml'
    assert assets.links[0]['href'].startswith('data:image/svg+xml,')
    assert 'setInterval(load,60000)' in page
    assert headers['Cache-Control'] == 'no-store'
    initial = json.loads(page.split('render(')[-1].split(');setInterval')[0])
    assert initial == dict(rows=[], sweep={})
    assert len(request(server, 'GET', '/api/priority')[1]['rows']) == 2
    for path in ('/api/fill', '/api/submit', '/api/review', '/api/unlock', '/api/retry/3556', '/api/refresh/missing'):
        assert request(server, 'GET', path)[0] == 404
        assert request(server, 'POST', path, run_headers(server, dashboard))[0] == 404


def test_page_listing_text_cannot_inject_script(http_dashboard, config, db, listing):
    server, _ = http_dashboard
    queue(db, config, replace(listing, company='</script><script>alert(1)</script>'), 1)
    # queue() supplies its own company; put hostile listing text in a displayed field.
    db.execute('UPDATE jobs SET title=?', ('Intern </script><script>alert(1)</script>',))
    page = request(server, 'GET', '/')[1]
    assert 'Intern </script>' not in page
    assert request(server, 'GET', '/api/priority')[1]['rows'][0]['title'] == 'Intern </script><script>alert(1)</script>'
    assert 'el.textContent=text' in page


def test_page_readable_timeouts_and_friendly_list_names(http_dashboard):
    page = request(http_dashboard[0], 'GET', '/')[1]
    assert "abort.abort(new DOMException('Request timed out','TimeoutError'))" in page
    assert "options.method==='GET'?30000:10000" in page
    assert "priority:'Queue','needs-you':'Needs you',failed:'Failed',applied:'Applied',run:'Status'" in page
    assert "notify(listNames[id]+': '+requestMessage(e),true,id)" in page
    assert "if(!background)throw e;return request(path)" in page
    assert 'headings[id][0]' not in page and 'Rank:' not in page


def test_javascript_timeout_and_quiet_background_retry(tmp_path):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable for offline JavaScript verification')
    request_code = PAGE[PAGE.index('async function request('):PAGE.index('function post(')]
    list_code = PAGE[PAGE.index('const loads={},listRecovery={};'):PAGE.index('let toastTimer')]
    script = r'''
const assert=require('node:assert/strict');
let timer,timeout,revision=0,messages=[];
const setTimeout=(fn,ms)=>{timer=fn;timeout=ms;},clearTimeout=()=>{};
const notice={dataset:{},hidden:true};const document={getElementById:()=>notice};
const notify=(message,error,owner='action')=>{messages.push(message);notice.dataset.owner=owner;notice.hidden=false;};
let fetch=(path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));
''' + request_code + list_code + r'''
(async()=>{
 const pending=request('/api/priority');assert.equal(timeout,30000);timer();
 await assert.rejects(pending,error=>error.name==='TimeoutError'&&error.message==='Request timed out');
 const write=request('/api/run',{method:'POST'});assert.equal(timeout,10000);timer();
 await assert.rejects(write,error=>error.name==='TimeoutError');
 const letter=request('/api/cv',{method:'POST'});assert.equal(timeout,240000);timer();
 await assert.rejects(letter,error=>error.name==='TimeoutError');
 let calls=0,rendered=[];
 request=async()=>{calls++;if(calls===1)throw new DOMException('Request timed out','TimeoutError');return {rows:[1]};};
 assert.equal(await loadList('priority','/api/priority',data=>rendered.push(data)),true);
 assert.equal(calls,2);assert.deepEqual(messages,[]);assert.deepEqual(rendered,[{rows:[1]}]);
 calls=0;request=async()=>{calls++;throw new DOMException('Request timed out','TimeoutError');};
 assert.equal(await loadList('priority','/api/priority',()=>assert.fail('failed read rendered')),false);
 assert.equal(calls,2);assert.deepEqual(messages,['Queue: request timed out']);
 calls=0;messages=[];
 assert.equal(await loadList('needs-you','/api/manual',()=>{},false),false);
 assert.equal(calls,1);assert.deepEqual(messages,['Needs you: request timed out']);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'dashboard-requests.js'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


async def test_cli_registration_and_dispatch(config, db, monkeypatch):
    args = parser().parse_args(['dashboard', '--port', '9876', '--liveness-minutes', '5', '--no-browser'])
    calls = []
    monkeypatch.setattr('autoapply.dashboard.serve', lambda *a, **kw: calls.append((a, kw)))
    assert await execute(args, config, db) == 0
    assert calls == [((config, db), dict(port=9876, liveness_minutes=5, no_browser=True))]
    defaults = parser().parse_args(['dashboard'])
    assert defaults.port == 8765 and defaults.liveness_minutes == 30 and not defaults.no_browser


@pytest.fixture
def worker_process(monkeypatch):
    child = Mock(pid=12345, poll=Mock(return_value=None))
    popen = Mock(return_value=child)
    monkeypatch.setattr('autoapply.dashboard.subprocess.Popen', popen)
    return popen, child


def run_headers(server, dashboard):
    return {'X-Dashboard-Token': dashboard.token,
            'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}


@pytest.mark.parametrize('state,view', [('QUEUED', 'priority'), ('READY', 'manual'), ('FAILED', 'failed')])
def test_manual_mark_unmark_round_trip(http_dashboard, config, db, listing, state, view):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    db.execute('UPDATE applications SET status=? WHERE id=?', (state, app_id))
    before = db.application(app_id)
    body = json.dumps({'application_id': app_id})
    headers = run_headers(server, dashboard)
    key = 'duplicate_submission_guard:' + str(app_id)
    field = 'rows' if view == 'priority' else 'applications'
    assert getattr(dashboard, view)()[field][0]['application_id'] == app_id
    # A running semi-auto process owns this lock, but check-offs use SQLite only.
    with ProcessLock(config.private / 'worker.lock'):
        status, result, _ = request(server, 'POST', '/api/applied', headers, body)
        assert status == 200 and result['application_id'] == app_id
        assert datetime.fromisoformat(result['marked_at']).utcoffset() == timedelta(0)
        assert db.setting(key) == dict(permanent=False, source='DASHBOARD_MANUAL', marked_at=result['marked_at'])
        assert db.automation_retired(app_id)
        assert discover(db, config) == []
        assert dashboard.priority()['rows'] == dashboard.manual()['applications'] == dashboard.failed()['applications'] == []
        (config.private / 'semi-auto-result.json').write_text(json.dumps({'applications': [{'application_id': app_id}]}))
        assert dashboard.status()['applications'] == []
        applied = request(server, 'GET', '/api/applied')[1]['applications']
        assert applied[0]['reversible'] is True and applied[0]['marked_at'] == result['marked_at']
        assert request(server, 'POST', '/api/applied', headers, body)[0] == 409
        assert request(server, 'POST', '/api/unapplied', headers, body)[:2] == (200, {'application_id': app_id})
    assert db.setting(key) is False
    assert db.one('SELECT value FROM settings WHERE key=?', (key,))['value'] == 'false'
    assert not db.automation_retired(app_id)
    assert getattr(dashboard, view)()[field][0]['application_id'] == app_id
    assert dashboard.status()['applications'][0]['application_id'] == app_id
    assert dashboard.applied()['applications'] == []
    assert db.application(app_id) == before
    assert [r['kind'] for r in db.rows("SELECT kind FROM events WHERE application_id=? AND kind LIKE 'USER_%APPLIED' ORDER BY id", (app_id,))] == ['USER_MARKED_APPLIED', 'USER_UNMARKED_APPLIED']
    assert request(server, 'POST', '/api/unapplied', headers, body)[0] == 409


@pytest.mark.parametrize('guard', [True, {'source': 'USER_REPORTED', 'permanent': True},
                                  {'source': 'USER_REPORTED', 'permanent': False},
                                  {'source': 'DASHBOARD_MANUAL', 'permanent': True},
                                  {'source': 'DASHBOARD_MANUAL'},
                                  {'source': 'DASHBOARD_MANUAL', 'permanent': 0}])
def test_manual_marks_never_replace_or_clear_other_guards(http_dashboard, config, db, listing, guard):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    key = 'duplicate_submission_guard:' + str(app_id)
    db.set_setting(key, guard)
    before = db.rows('SELECT * FROM events')
    for endpoint in ('applied', 'unapplied'):
        assert request(server, 'POST', '/api/' + endpoint, run_headers(server, dashboard),
                       json.dumps({'application_id': app_id}))[0] == 409
    assert db.setting(key) == guard and db.rows('SELECT * FROM events') == before
    assert dashboard.applied()['applications'][0]['reversible'] is False


@pytest.mark.parametrize('endpoint', ['applied', 'unapplied', 'not-eligible', 'eligible'])
@pytest.mark.parametrize('bad_id', [None, True, False, 0, -1, 1.5, '1', [], {}, 2**63])
def test_manual_mark_bad_ids(http_dashboard, endpoint, bad_id):
    server, dashboard = http_dashboard
    assert request(server, 'POST', '/api/' + endpoint, run_headers(server, dashboard),
                   json.dumps({'application_id': bad_id}))[0] == 400


@pytest.mark.parametrize('endpoint', ['applied', 'unapplied', 'not-eligible', 'eligible'])
@pytest.mark.parametrize('body', ['{}', '[]', '{', '', 'x' * 1025])
def test_manual_mark_bad_body(http_dashboard, endpoint, body):
    server, dashboard = http_dashboard
    assert request(server, 'POST', '/api/' + endpoint, run_headers(server, dashboard), body)[0] == 400


def test_manual_mark_unknown_protected_and_auth(http_dashboard, db, listing):
    server, dashboard = http_dashboard
    protected_queue(db, listing)
    before = {i: db.application(i) for i in PROTECTED_APPLICATIONS}
    for endpoint in ('applied', 'unapplied'):
        assert request(server, 'POST', '/api/' + endpoint, run_headers(server, dashboard), '{"application_id":99999}')[0] == 404
        for app_id in PROTECTED_APPLICATIONS:
            body = json.dumps({'application_id': app_id})
            assert request(server, 'POST', '/api/' + endpoint, run_headers(server, dashboard), body)[0] == 403
            assert request(server, 'POST', '/api/' + endpoint, {}, body)[0] == 403
    assert {i: db.application(i) for i in PROTECTED_APPLICATIONS} == before
    assert db.rows("SELECT * FROM settings WHERE key LIKE 'duplicate_submission_guard:%'") == []


def test_applied_shape_sort_unescape_and_urls(http_dashboard, config, db, listing):
    server, dashboard = http_dashboard
    ids = queue(db, config, listing, 5)
    stamps = ['2026-10-01T00:00:00+00:00', '2026-10-08T00:00:00+00:00']
    for app_id, stamp, source in zip(ids, stamps, ['DASHBOARD_MANUAL', 'USER_REPORTED']):
        db.set_setting('duplicate_submission_guard:' + str(app_id), dict(source=source, permanent=source != 'DASHBOARD_MANUAL', marked_at=stamp))
    db.set_setting('duplicate_submission_guard:' + str(ids[2]), True)
    db.set_setting('duplicate_submission_guard:' + str(ids[3]), False)
    db.set_setting('duplicate_submission_guard:' + str(ids[4]), None)
    db.execute('UPDATE jobs SET company=?,title=?,canonical_url=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
               ('A &amp; B', 'Role &lt;Intern&gt;', 'javascript:alert(1)', ids[1]))
    protected_queue(db, listing)
    for app_id in PROTECTED_APPLICATIONS:
        db.set_setting('duplicate_submission_guard:' + str(app_id), True)
    status, result, _ = request(server, 'GET', '/api/applied')
    assert status == 200 and result == dashboard.applied()
    assert [a['application_id'] for a in result['applications']] == [ids[1], ids[0], ids[2]]
    first = result['applications'][0]
    assert set(first) == {'application_id', 'company', 'title', 'ats', 'url', 'marked_at', 'source', 'reversible'}
    assert first['company'] == 'A & B' and first['title'] == 'Role <Intern>' and first['url'] == ''
    assert first['source'] == 'USER_REPORTED' and first['reversible'] is False


def test_manual_mark_guard_and_event_are_atomic(dashboard, config, db, listing, monkeypatch):
    app_id = queue(db, config, listing, 1)[0]
    def fail_event(*args):
        raise RuntimeError('event insert failed')
    monkeypatch.setattr('autoapply.dashboard._Connection.event', fail_event)
    with pytest.raises(RuntimeError, match='event insert failed'):
        dashboard.mark_applied(app_id)
    assert db.setting('duplicate_submission_guard:' + str(app_id)) is None
    assert not db.one("SELECT id FROM events WHERE kind='USER_MARKED_APPLIED'")


@pytest.mark.parametrize('state', ['QUEUED', 'READY'])
def test_not_eligible_round_trip_and_ranks(http_dashboard, config, db, listing, state):
    server, dashboard = http_dashboard
    ids = queue(db, config, listing, 3)
    app_id = ids[1]
    db.execute('UPDATE applications SET status=? WHERE id=?', (state, app_id))
    before = db.application(app_id)
    dashboard.priority()
    key = 'user_not_eligible:' + str(app_id)
    body = json.dumps(dict(application_id=app_id, reason='Requires Master’s / PhD', note='  Graduate students only  '))
    headers = run_headers(server, dashboard)
    with ProcessLock(config.private / 'worker.lock'):
        status, result, _ = request(server, 'POST', '/api/not-eligible', headers, body)
        assert status == 200
        assert db.setting(key) == dict(reason='Requires Master’s / PhD', note='Graduate students only',
                                       marked_at=result['marked_at'], source='DASHBOARD_MANUAL')
        assert dashboard._priority_key is None
        assert discover(db, config) == [ids[0], ids[2]]
        rows = dashboard.priority()['rows']
        assert [r['application_id'] for r in rows] == [ids[0], ids[2]]
        assert [r['rank'] for r in rows] == [1, 2]
        assert dashboard.manual()['applications'] == []
        item = request(server, 'GET', '/api/applied')[1]['not_eligible'][0]
        assert item == dict(application_id=app_id, company='Company 1', title=listing.title,
                            reason='Requires Master’s / PhD', note='Graduate students only',
                            marked_at=result['marked_at'], reversible=True)
        assert request(server, 'POST', '/api/not-eligible', headers, body)[0] == 409
        assert request(server, 'POST', '/api/eligible', headers, json.dumps(dict(application_id=app_id)))[0] == 200
    assert db.setting(key) is False
    assert dashboard._priority_key is None
    assert dashboard.applied()['not_eligible'] == []
    assert db.application(app_id) == before
    assert db.setting('duplicate_submission_guard:' + str(app_id)) is None
    assert [r['kind'] for r in db.rows("SELECT kind FROM events WHERE kind LIKE 'USER_%NOT_ELIGIBLE' ORDER BY id")] == [
        'USER_MARKED_NOT_ELIGIBLE', 'USER_UNMARKED_NOT_ELIGIBLE']
    assert app_id in (discover(db, config) if state == 'QUEUED' else [a['application_id'] for a in dashboard.manual()['applications']])
    assert request(server, 'POST', '/api/eligible', headers, json.dumps(dict(application_id=app_id)))[0] == 409


@pytest.mark.parametrize('reason,note', [(None, ''), ([], ''), ('Unknown', ''), ('Other', ''),
                                        ('Other', '  '), ('Other', None), ('Other', 1), ('Other', 'x' * 501)])
def test_not_eligible_rejects_invalid_reason_or_note(http_dashboard, config, db, listing, reason, note):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    assert request(server, 'POST', '/api/not-eligible', run_headers(server, dashboard),
                   json.dumps(dict(application_id=app_id, reason=reason, note=note)))[0] == 400
    assert db.setting('user_not_eligible:' + str(app_id)) is None


def test_not_eligible_auth_protected_unknown_and_foreign_marks(http_dashboard, config, db, listing):
    server, dashboard = http_dashboard
    protected_queue(db, listing)
    before = {i: db.application(i) for i in PROTECTED_APPLICATIONS}
    headers = run_headers(server, dashboard)
    for endpoint in ('not-eligible', 'eligible'):
        body = json.dumps(dict(application_id=99999, reason='Other', note='Fixture'))
        assert request(server, 'POST', '/api/' + endpoint, headers, body)[0] == 404
        for app_id in PROTECTED_APPLICATIONS:
            body = json.dumps(dict(application_id=app_id, reason='Other', note='Fixture'))
            assert request(server, 'POST', '/api/' + endpoint, headers, body)[0] == 403
        app_id = queue(db, config, listing, 1)[0]
        body = json.dumps(dict(application_id=app_id, reason='Other', note='Fixture'))
        for invalid in ({}, dict(headers, Origin='http://evil.test'), dict(headers, **{'X-Dashboard-Token': 'invalid'})):
            assert request(server, 'POST', '/api/' + endpoint, invalid, body)[0] == 403
        db.set_setting('user_not_eligible:' + str(app_id), dict(source='OTHER_SOURCE'))
        assert request(server, 'POST', '/api/' + endpoint, headers, body)[0] == 409
        assert db.setting('user_not_eligible:' + str(app_id)) == dict(source='OTHER_SOURCE')
    assert {i: db.application(i) for i in PROTECTED_APPLICATIONS} == before
    assert not db.one("SELECT key FROM settings WHERE key IN ('user_not_eligible:3556','user_not_eligible:4522','user_not_eligible:6397','user_not_eligible:4611')")


def test_not_eligible_event_and_setting_are_atomic(dashboard, config, db, listing, monkeypatch):
    app_id = queue(db, config, listing, 1)[0]
    key = 'user_not_eligible:' + str(app_id)
    with monkeypatch.context() as patch:
        patch.setattr('autoapply.dashboard._Connection.event', Mock(side_effect=RuntimeError('event failed')))
        with pytest.raises(RuntimeError, match='event failed'):
            dashboard.mark_not_eligible(app_id, 'Other', 'Fixture')
    assert db.setting(key) is None
    dashboard.mark_not_eligible(app_id, 'Other', 'x' * 500)
    before = db.setting(key)
    monkeypatch.setattr('autoapply.dashboard._Connection.event', Mock(side_effect=RuntimeError('event failed')))
    with pytest.raises(RuntimeError, match='event failed'):
        dashboard.mark_not_eligible(app_id, undo=True)
    assert db.setting(key) == before


def test_page_stable_navbar_and_eligibility_dialog():
    assert 'html{height:100%;overflow:hidden}' in PAGE
    assert 'body{height:100vh;height:100dvh;overflow:hidden;' in PAGE
    assert '.navbar{position:fixed;left:0;right:0;top:0;' in PAGE
    assert 'overflow-y:auto;scrollbar-gutter:stable' in PAGE
    assert 'html{scrollbar-gutter:stable}' not in PAGE
    assert '.tabs .count{display:inline-flex;min-width:4ch;width:4ch;flex:0 0 4ch;' in PAGE
    assert '<dialog id="eligibility-dialog"' in PAGE
    assert 'aria-labelledby="eligibility-title"' in PAGE
    assert '<textarea id="eligibility-note" maxlength="500">' in PAGE
    assert 'innerHTML' not in PAGE


@pytest.mark.parametrize('count', [0, 11, -1, 2.5, 5.0, True, False, '5', None, [], {}])
def test_run_rejects_invalid_count(http_dashboard, worker_process, count):
    server, dashboard = http_dashboard
    status, value, _ = request(server, 'POST', '/api/run', run_headers(server, dashboard),
                               json.dumps({'count': count}))
    assert status == 400 and 'integer between 1 and 10' in value['error']
    worker_process[0].assert_not_called()


@pytest.mark.parametrize('body', ['{}', '[]', '{', '', 'x' * 1025])
def test_run_rejects_bad_json(http_dashboard, worker_process, body):
    server, dashboard = http_dashboard
    assert request(server, 'POST', '/api/run', run_headers(server, dashboard), body)[0] == 400
    worker_process[0].assert_not_called()


@pytest.mark.parametrize('count', [1, 5, 10])
def test_run_launches_detached_same_interpreter_and_rejects_alive_child(
        http_dashboard, config, db, worker_process, count):
    server, dashboard = http_dashboard
    popen, child = worker_process
    db.set_setting('paused', True)
    db.set_setting('stop_requested', True)
    headers = run_headers(server, dashboard)
    assert request(server, 'POST', '/api/run', headers, json.dumps({'count': count}))[:2] == (202, {'pid': child.pid})
    command = popen.call_args.args[0]
    assert command == [sys.executable, '-m', 'autoapply', '--root', str(config.root),
                       'semi-auto', '--limit', str(count)]
    options = popen.call_args.kwargs
    assert options['stdin'] == subprocess.DEVNULL and options['stderr'] == subprocess.STDOUT
    assert options['stdout'].closed
    assert config.private / 'logs' == Path(options['stdout'].name).parent
    assert options['close_fds']
    if os.name == 'nt':
        assert options['creationflags'] & subprocess.DETACHED_PROCESS
        assert options['creationflags'] & subprocess.CREATE_NO_WINDOW
    else:
        assert options['start_new_session']
    assert db.setting('paused') is False and db.setting('stop_requested') is False
    assert request(server, 'POST', '/api/run', headers, '{"count":5}')[:2] == (
        409, {'error': 'dashboard worker already running'})
    assert request(server, 'POST', '/api/refresh', headers)[0] == 409
    state = request(server, 'GET', '/api/status')[1]
    assert state['running'] and state['pid'] == child.pid
    assert not state['paused'] and not state['stopping']
    popen.assert_called_once()
    child.poll.return_value = 0
    assert not dashboard.status()['running']
    assert request(server, 'POST', '/api/run', headers, '{"count":1}')[0] == 202
    assert popen.call_count == 2


def test_run_refuses_worker_lock_and_keeps_settings(http_dashboard, config, db, worker_process):
    server, dashboard = http_dashboard
    db.set_setting('paused', True)
    db.set_setting('stop_requested', True)
    with ProcessLock(config.private / 'worker.lock'):
        status, value, _ = request(server, 'POST', '/api/run', run_headers(server, dashboard), '{"count":5}')
        assert status == 409 and 'worker.lock is held' in value['error']
        state = request(server, 'GET', '/api/status')[1]
        assert state['running'] and state['worker_locked'] and state['pid'] is None
    assert db.setting('paused') is True and db.setting('stop_requested') is True
    worker_process[0].assert_not_called()


def test_concurrent_starts_spawn_only_once(dashboard, worker_process):
    from concurrent.futures import ThreadPoolExecutor
    from autoapply.dashboard import RunRejected
    def start():
        try:
            return dashboard.run_worker(5)
        except RunRejected:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert results.count('refused') == 1
    worker_process[0].assert_called_once()


def test_pause_resume_stop_only_change_control_settings(http_dashboard, config, db, listing, worker_process):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    before = db.application(app_id)
    db.set_setting('auto_submit', False)
    db.set_setting('test_hold', {'keep': True})
    headers = run_headers(server, dashboard)
    request(server, 'POST', '/api/run', headers, '{"count":5}')
    for action, paused in [('pause', True), ('resume', False)]:
        status, state, _ = request(server, 'POST', '/api/' + action, headers)
        assert status == 200 and state['paused'] is paused
        assert db.setting('paused') is paused
    status, state, _ = request(server, 'POST', '/api/stop', headers)
    assert status == 200 and state['stopping'] and state['running']
    assert db.setting('stop_requested') is True
    assert db.setting('auto_submit') is False and db.setting('test_hold') == {'keep': True}
    assert db.application(app_id) == before
    worker_process[1].terminate.assert_not_called()
    worker_process[1].kill.assert_not_called()


def test_status_reads_report_summary_without_saved_answers(http_dashboard, config):
    server, dashboard = http_dashboard
    report = dict(batch_id='batch-test', phase='REVIEW', updated_at='2026-10-07T00:00:00Z',
                  applications=[dict(application_id=12, lifecycle='MANUAL_REVIEW',
                  application_state='READY_FOR_MANUAL_SUBMIT', readiness='LIVE_READY_FOR_MANUAL_SUBMIT',
                  security='NONE', checkpoint={'committed_field_count': 3, 'answer': 'private answer'},
                  submission={'evidence': 'private evidence'})])
    (config.private / 'semi-auto-result.json').write_text(json.dumps(report), encoding='utf-8')
    status, state, _ = request(server, 'GET', '/api/status')
    assert status == 200 and not state['running']
    assert state['phase'] == 'REVIEW' and state['batch_id'] == 'batch-test'
    assert state['updated_at'] == report['updated_at']
    assert state['applications'] == [dict(application_id=12, lifecycle='MANUAL_REVIEW',
        application_state='READY_FOR_MANUAL_SUBMIT', readiness='LIVE_READY_FOR_MANUAL_SUBMIT',
        security='NONE', verified_fields=3)]
    assert 'private' not in json.dumps(state)


@pytest.mark.parametrize('report', [None, '{', '[]', '{"applications":{}}', '{"applications":[null]}',
                                  '{"applications":[{"checkpoint":"bad"}]}'])
def test_status_missing_or_invalid_report(dashboard, config, report):
    if report is not None:
        (config.private / 'semi-auto-result.json').write_text(report, encoding='utf-8')
    state = dashboard.status()
    assert state['applications'] == [] and state['phase'] is None
    assert bool(state['report_error']) == (report is not None)


@pytest.mark.parametrize('path', ['/api/run', '/api/pause', '/api/resume', '/api/stop'])
@pytest.mark.parametrize('invalid', ['token', 'origin', 'missing-origin', 'host', 'fetch-site'])
def test_run_controls_require_local_token_and_origin(http_dashboard, db, worker_process, path, invalid):
    server, dashboard = http_dashboard
    headers = run_headers(server, dashboard)
    if invalid == 'token':
        del headers['X-Dashboard-Token']
    elif invalid == 'origin':
        headers['Origin'] = 'https://evil.test'
    elif invalid == 'missing-origin':
        del headers['Origin']
    elif invalid == 'host':
        headers['Host'] = 'evil.test'
    else:
        headers['Sec-Fetch-Site'] = 'cross-site'
    before = db.rows('SELECT * FROM settings ORDER BY key')
    assert request(server, 'POST', path, headers, '{"count":5}')[0] == 403
    assert db.rows('SELECT * FROM settings ORDER BY key') == before
    worker_process[0].assert_not_called()


def test_run_panel_bounds_polling_and_safe_text(http_dashboard):
    server, _ = http_dashboard
    page = request(server, 'GET', '/')[1]
    assert 'type="number" value="5" min="1" max="10" step="1" required' in page
    assert 'Number.isInteger(n)' in page and 'count.reportValidity()' in page
    assert 'setInterval(loadStatus,5000)' in page
    assert 'Worker lock held by another process' in page and 'function updateControls(s)' in page
    assert 'li.textContent=' in page
    for action in ('start', 'pause', 'resume', 'stop'):
        assert f'id="{action}"' in page


FAILURE_CATEGORIES = {
    'ENGINEERING_FAILURE', 'FORM_NOT_READY', 'APPLICATION_ENTRY_MODE_UNRESOLVED',
    'TRANSITION_UNCONFIRMED', 'ADAPTER_CAPABILITY_FAILURE', 'FORM_INVENTORY_UNSTABLE',
    'UPLOAD_PENDING', 'UPLOAD_RACE_DETECTED', 'FORM_TERMINAL_FAILURE',
    'SUBMIT_CLICK_NOT_DELIVERED', 'BROWSER_START_FAILED',
}


@pytest.mark.parametrize('status,category,reason,is_failure', [
    ('MANUAL_REVIEW', category, 'Form could not be filled', True)
    for category in sorted(FAILURE_CATEGORIES)
] + [
    ('FAILED', 'NETWORK_ERROR', 'Connection failed', True),
    ('MANUAL_REVIEW', None, 'upload readiness failed: resume', True),
    ('MANUAL_REVIEW', None, 'Error during navigation', True),
    ('MANUAL_REVIEW', None, 'TargetClosedError: page closed', True),
    ('MANUAL_REVIEW', 'CAPTCHA', 'Solve challenge', False),
    ('MANUAL_REVIEW', 'IDENTITY_VERIFICATION', 'Verify identity', False),
    ('MANUAL_REVIEW', 'UNKNOWN_SECURITY_FAILURE', 'Check security', False),
    ('MANUAL_REVIEW', 'INPUT_REQUIRED', 'Answer question', False),
    ('MANUAL_REVIEW', 'ELIGIBILITY_UNKNOWN', 'Confirm eligibility', False),
    ('AUTH_REQUIRED', 'AUTH_REQUIRED', 'Sign in', False),
    ('NEEDS_INPUT', 'INPUT_REQUIRED', 'Answer question', False),
    ('READY', 'ENGINEERING_FAILURE', 'Old error during filling', False),
])
def test_failed_selection_and_manual_disjointness(dashboard, config, db, listing,
                                                status, category, reason, is_failure):
    assert AUTOMATION_FAILURE_CATEGORIES == FAILURE_CATEGORIES
    app_id = queue(db, config, listing, 1)[0]
    db.execute('UPDATE applications SET status=?,error_category=?,failure_reason=? WHERE id=?',
               (status, category, reason, app_id))
    before = db.application(app_id)
    failures = dashboard.failed()['applications']
    manual = dashboard.manual()['applications']
    assert [a['application_id'] for a in failures] == ([app_id] if is_failure else [])
    assert [a['application_id'] for a in manual] == ([] if is_failure else [app_id])
    assert not ({a['application_id'] for a in failures} & {a['application_id'] for a in manual})
    assert db.application(app_id) == before


@pytest.mark.parametrize('excluded', ['submitted', 'already-applied', 'closed', 'invalid',
                                    'ineligible', 'duplicate', 'retired', 'confirmed',
                                    'inactive', 'closed-listing', 'old'])
def test_attention_views_exclude_unsafe_and_old_rows(dashboard, config, db, listing, excluded):
    app_id = queue(db, config, listing, 1)[0]
    db.execute("UPDATE applications SET status='MANUAL_REVIEW',error_category='ENGINEERING_FAILURE' WHERE id=?", (app_id,))
    terminal = {'submitted': 'SUBMITTED', 'already-applied': 'ALREADY_APPLIED',
                'closed': 'CLOSED', 'invalid': 'INVALID', 'ineligible': 'INELIGIBLE', 'duplicate': 'DUPLICATE'}
    if excluded in terminal:
        db.execute('UPDATE applications SET status=? WHERE id=?', (terminal[excluded], app_id))
    elif excluded == 'retired':
        db.set_setting('duplicate_submission_guard:' + str(app_id), True)
    elif excluded == 'confirmed':
        db.execute('UPDATE applications SET submission_confirmation_seen=1 WHERE id=?', (app_id,))
    elif excluded == 'inactive':
        db.execute('UPDATE jobs SET listing_active=0 WHERE id=(SELECT job_id FROM applications WHERE id=?)', (app_id,))
    elif excluded == 'closed-listing':
        db.execute("UPDATE jobs SET listing_status='CLOSED' WHERE id=(SELECT job_id FROM applications WHERE id=?)", (app_id,))
    else:
        db.execute('UPDATE applications SET updated_at=? WHERE id=?',
                   ((datetime.now(timezone.utc) - timedelta(days=15)).isoformat(), app_id))
    assert dashboard.failed()['applications'] == []
    assert dashboard.manual()['applications'] == []
    # Verify these exclusions also apply to genuine manual holds.
    if excluded not in terminal:
        db.execute("UPDATE applications SET error_category='INPUT_REQUIRED' WHERE id=?", (app_id,))
        assert dashboard.manual()['applications'] == []
    if excluded == 'old':
        assert dashboard.manual(days=16)['applications'][0]['application_id'] == app_id


@pytest.mark.parametrize('state', ['FAILED', 'MANUAL_REVIEW', 'READY'])
def test_attention_views_exclude_protected_ids(dashboard, db, listing, state):
    protected_queue(db, listing)
    db.execute('UPDATE applications SET status=?,error_category=?', (state, 'ENGINEERING_FAILURE'))
    before = {i: db.application(i) for i in PROTECTED_APPLICATIONS}
    assert dashboard.failed()['applications'] == dashboard.manual()['applications'] == []
    assert {i: db.application(i) for i in PROTECTED_APPLICATIONS} == before


@pytest.mark.parametrize('attempts,max_retries,delivery,intent,possible', [
    (0, 3, 'NOT_STARTED', None, True), (3, 3, 'NOT_STARTED', None, True),
    (4, 3, 'NOT_STARTED', None, False), (1, 0, 'NOT_STARTED', None, False),
    (0, 3, 'UNKNOWN', None, False), (0, 3, 'DELIVERED', None, False),
    (0, 3, 'NOT_STARTED', '2026-10-01T00:00:00Z', False),
])
def test_failed_retry_possible_is_only_informational(dashboard, config, db, listing,
                                                   attempts, max_retries, delivery, intent, possible):
    app_id = queue(db, config, listing, 1)[0]
    db.execute("""UPDATE applications SET status='FAILED',attempts=?,max_retries=?,
                  delivery_state=?,submit_intent_at=? WHERE id=?""",
               (attempts, max_retries, delivery, intent, app_id))
    row = dashboard.failed()['applications'][0]
    assert row['retry_possible'] is possible
    assert row['attempts'] == attempts and row['max_retries'] == max_retries
    assert db.application(app_id)['attempts'] == attempts


@pytest.mark.parametrize('url,expected', [('https://example.test/apply', 'https://example.test/apply'),
                                       ('http://example.test/apply', 'http://example.test/apply'),
                                       ('javascript:alert(1)', ''), ('file:///local', ''),
                                       ('//example.test/apply', ''), ('https://', ''), ('https://[', '')])
def test_attention_urls_are_http_only(dashboard, config, db, listing, url, expected):
    app_id = queue(db, config, listing, 1)[0]
    db.execute('UPDATE jobs SET canonical_url=? WHERE id=(SELECT job_id FROM applications WHERE id=?)', (url, app_id))
    for state, method in [('FAILED', dashboard.failed), ('READY', dashboard.manual)]:
        db.execute('UPDATE applications SET status=? WHERE id=?', (state, app_id))
        assert method()['applications'][0]['url'] == expected


def test_failed_api_shape_order_reason_limit_and_window(http_dashboard, config, db, listing):
    server, dashboard = http_dashboard
    ids = queue(db, config, listing, 3)
    timestamps = [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat() for days in (2, 1, 15)]
    for app_id, stamp in zip(ids, timestamps):
        db.execute("UPDATE applications SET status='FAILED',error_category='ENGINEERING_FAILURE',failure_reason=?,updated_at=? WHERE id=?",
                   ('x' * 250, stamp, app_id))
    status, result, _ = request(server, 'GET', '/api/failed')
    assert status == 200 and result == dashboard.failed()
    assert [r['application_id'] for r in result['applications']] == [ids[1], ids[0]]
    assert [r['application_id'] for r in dashboard.failed(days=16)['applications']] == [ids[1], ids[0], ids[2]]
    assert set(result['applications'][0]) == {'application_id', 'company', 'title', 'ats',
        'error_category', 'reason', 'attempts', 'max_retries', 'retry_possible', 'url', 'updated_at'}
    assert all(len(r['reason']) == 200 for r in result['applications'])


def test_manual_ready_first_and_pending_questions(dashboard, config, db, listing):
    from autoapply.models import Question
    ids = queue(db, config, listing, 2)
    db.execute("UPDATE applications SET status='NEEDS_INPUT' WHERE id=?", (ids[0],))
    db.execute("UPDATE applications SET status='READY',updated_at=? WHERE id=?",
               ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), ids[1]))
    db.question(ids[0], Question('question', 'Which date can you start?'))
    rows = dashboard.manual()['applications']
    assert [r['application_id'] for r in rows] == [ids[1], ids[0]]
    assert rows[0]['state'] == 'Ready to submit'
    assert rows[1]['pending_questions'] == ['Which date can you start?'] and rows[1]['pending_count'] == 1


def test_status_includes_company_without_private_fields(dashboard, config, db, listing):
    app_id = queue(db, config, listing, 1)[0]
    (config.private / 'semi-auto-result.json').write_text(json.dumps({'applications': [
        {'application_id': app_id, 'checkpoint': {'committed_field_count': 4, 'answer': 'secret'}}
    ]}), encoding='utf-8')
    row = dashboard.status()['applications'][0]
    assert row['company'] == 'Company 0' and row['title'] == listing.title and row['verified_fields'] == 4
    stored = db.application(app_id)
    assert row['ats'] == stored['ats'] and row['url'] == stored['canonical_url']
    assert 'secret' not in json.dumps(row)


def test_tab_markup_accessibility_themes_polling_and_csp(http_dashboard):
    server, dashboard = http_dashboard
    _, page, headers = request(server, 'GET', '/')
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []
        def handle_starttag(self, tag, attrs):
            self.elements.append((tag, dict(attrs)))
    parsed = Elements()
    parsed.feed(page)
    tabs = [a for t, a in parsed.elements if a.get('role') == 'tab']
    panels = {a['id']: a for t, a in parsed.elements if a.get('role') == 'tabpanel'}
    assert len([a for t, a in parsed.elements if a.get('role') == 'tablist']) == 1
    assert [a['id'] for a in tabs] == ['tab-priority', 'tab-applications', 'tab-answers', 'tab-run', 'tab-settings']
    assert [a['aria-selected'] for a in tabs] == ['true', 'false', 'false', 'false', 'false']
    assert [a['tabindex'] for a in tabs] == ['0', '-1', '-1', '-1', '-1']
    assert 'hidden' not in panels['panel-priority']
    assert all('hidden' in panel for name, panel in panels.items() if name != 'panel-priority')
    for tab in tabs:
        assert panels[tab['aria-controls']]['aria-labelledby'] == tab['id']
    for key in ('ArrowRight', 'ArrowLeft', 'Home', 'End', 'location.hash', 'hashchange'):
        assert key in page
    for name, interval in [('loadStatus', 5000), ('loadManual', 30000), ('loadFailed', 30000), ('loadApplied', 30000), ('load', 60000)]:
        assert f'setInterval({name},{interval})' in page
    assert 'innerHTML' not in page and 'insertAdjacentHTML' not in page
    assert "a.target='_blank';a.rel='noopener noreferrer'" in page
    assert "u.protocol!=='https:'&&u.protocol!=='http:'" in page
    assert 'Open application #' in page
    assert 'Review #ID' not in page and '!submitted ID' not in page  # Removed legacy narration.
    assert 'prefers-color-scheme:light' in page and ':focus-visible' in page and '--surface:' in page
    assert 'dataset.label' in page and '@media(max-width:480px)' in page
    assert f"script-src 'nonce-{dashboard.token}'" in headers['Content-Security-Policy']
    for tag in ('style', 'script'):
        assert [a['nonce'] for t, a in parsed.elements if t == tag] == [dashboard.token]
    assert 'id="dismiss"' in page
    assert 'aria-label="Reload data"' in page and 'Does not search for listings' in page
    assert 'Find new listings' in page and 'prefers-reduced-motion' in page
    assert '-webkit-line-clamp:3' in page and 'minmax(300px,1fr)' in page
    assert 'box-shadow' not in page and 'gradient' not in page


@pytest.mark.parametrize('view,state', [('priority', 'QUEUED'), ('manual', 'READY'),
                                       ('failed', 'FAILED'), ('status', 'QUEUED')])
def test_listing_entities_are_decoded_without_changing_storage(dashboard, config, db, listing, view, state):
    app_id = queue(db, config, listing, 1)[0]
    company, title = 'Research &amp; Systems', 'Platform &amp; Site Reliability &lt;Intern&gt;'
    db.execute('UPDATE jobs SET company=?,title=? WHERE id=(SELECT job_id FROM applications WHERE id=?)',
               (company, title, app_id))
    db.execute('UPDATE applications SET status=? WHERE id=?', (state, app_id))
    if view == 'status':
        (config.private / 'semi-auto-result.json').write_text(
            json.dumps({'applications': [{'application_id': app_id}]}), encoding='utf-8')
    result = getattr(dashboard, view)()
    row = result['rows' if view == 'priority' else 'applications'][0]
    assert row['company'] == 'Research & Systems'
    assert row['title'] == 'Platform & Site Reliability <Intern>'
    assert db.application(app_id)['company'] == company
    assert db.application(app_id)['title'] == title


def test_manual_summary_retains_raw_display_state(dashboard, config, db, listing):
    app_id = queue(db, config, listing, 1)[0]
    db.execute("UPDATE applications SET status='MANUAL_REVIEW',error_category='AUTH_REQUIRED' WHERE id=?", (app_id,))
    row = dashboard.manual()['applications'][0]
    assert row['state'] == 'Account / sign-in required'
    assert row['state_code'] == 'AUTH_REQUIRED'


def test_page_display_helpers_and_no_reserved_notice_gap():
    assert Path('autoapply/dashboard.py').read_bytes().isascii()
    assert Path('autoapply/dashboard_ui.py').read_bytes().isascii()
    from autoapply.dashboard_ui import PAGE as ui_page
    assert PAGE is ui_page
    assert ' ? ' not in PAGE and r'\u00b7' in PAGE
    assert 'function humanize(' in PAGE and 'function timestamp(' in PAGE
    assert 'Intl.DateTimeFormat' in PAGE
    assert 'notice-slot' not in PAGE
    assert '<main><div id="notice" class="notice" hidden>' in PAGE
    assert '[hidden]{display:none!important}' in PAGE


def test_javascript_display_formatting_and_renderers(tmp_path):
    """Execute the shipped renderers offline with a tiny DOM, without a browser."""
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable for offline JavaScript verification')
    script = PAGE.split('<script nonce="__TOKEN__">', 1)[1].split('</script>', 1)[0]
    script_path = tmp_path / 'dashboard-syntax.js'
    script_path.write_text(script, encoding='utf-8')
    syntax = subprocess.run([executable, '--check', str(script_path)], text=True, capture_output=True, timeout=15)
    assert syntax.returncode == 0, syntax.stderr
    harness = r'''
const assert=require('node:assert/strict');
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.childNodes=this.children;this.dataset={};this._text='';this.attributes={};this.listeners={};this.value='';this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
 set id(value){this._id=value;elements.set(value,this);} get id(){return this._id;}
 set textContent(value){this._text=String(value);this.children.length=0;}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 append(...children){for(const child of children){child.remove();child.parent=this;this.children.push(child);}}
 replaceChildren(...children){this._text='';for(const child of [...this.children])child.remove();this.append(...children);}
 insertBefore(child,before){child.remove();child.parent=this;const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,child);}
 remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
 contains(el){return this===el||this.children.some(child=>child.contains(el));}
 setAttribute(k,v){this.attributes[k]=v;} getAttribute(k){return this.attributes[k];}
 addEventListener(k,fn){this.listeners[k]=fn;}
 click(){this.clicked=true;if(!this.disabled)this.listeners.click?.();}
 scrollIntoView(options){this.scrolled=options;} focus(){document.activeElement=this;}
 showModal(){this.open=true;} close(){this.open=false;this.listeners.close?.();} get isConnected(){return !!this.parent;}
}
const elements=new Map(),document={createElement:tag=>new Node(tag),createElementNS:(namespace,tag)=>new Node(tag),createDocumentFragment:()=>new Node('fragment'),
 querySelector:selector=>selector==='main'?document.getElementById('main'):null,
 querySelectorAll:selector=>selector==='[role="tab"]'?testTabs:selector==='[data-tools]'?testTools:[],activeElement:null,
 getElementById(id){if(!elements.has(id))elements.set(id,new Node('div'));return elements.get(id);}};
const testTabs=['run','priority','applications','answers','settings'].map(id=>{const tab=document.getElementById('tab-'+id);tab.id='tab-'+id;tab.setAttribute('aria-controls','panel-'+id);return tab;});
document.body=new Node('body');
const testTools=['run','needs-you','failed','priority','applied'].map(id=>{const el=new Node('div');el.dataset.tools=id;return el;});
const window={listeners:{},addEventListener(k,fn){const previous=this.listeners[k];this.listeners[k]=previous?event=>{previous(event);fn(event);}:fn;}},location={hash:'#run',href:'http://127.0.0.1:8765/'},history={replaceState:()=>{}};
const storage=new Map(),localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
let fetch=()=>new Promise(()=>{});const setInterval=()=>{},setTimeout=()=>{},clearTimeout=()=>{};
const requestAnimationFrame=fn=>fn();
Date.now=()=>Date.parse('2026-10-08T17:38:06.446586+00:00');
'''
    assertions = r'''
reloadButton.append(svgIcon('M0 0'));
const stamp='2026-10-08T15:38:06.446586+00:00';
selectTab('applied',true);assert.equal(document.activeElement.id,'tab-applications');
assert.equal(applicationSegment,'applied');assert.equal(document.getElementById('panel-applied').hidden,false);assert.equal(document.getElementById('panel-needs-you').hidden,true);
assert.deepEqual(document.activeElement.scrolled,{block:'nearest',inline:'nearest'});
document.activeElement.scrolled=null;window.listeners.resize();assert.equal(document.activeElement.scrolled.inline,'nearest');
selectTab('run');
const expected={READY_TO_SUBMIT:'Ready to submit',READY_FOR_MANUAL_SUBMIT:'Ready to submit',MANUAL_REQUIRED:'Needs you',
 RECONSTRUCTABLE_CHECKPOINT:'Filled, tab closed',SAVED_PROGRESS_CHECKPOINT:'Progress saved',
 PASSIVE_PROTECTION_DETECTED:'Passive bot protection',NONE:'None',FORM_NOT_READY:'Form did not load',
 APPLICATION_ENTRY_MODE_UNRESOLVED:'Could not open the application form',ENGINEERING_FAILURE:'Automation error',
 AUTH_REQUIRED:'Account sign-in required',INPUT_REQUIRED:'Needs answers',REVIEW:'Review',FILLING:'Filling'};
for(const [raw,label] of Object.entries(expected)){assert.equal(humanize(raw),label);assert.equal(code(raw).title,raw);}
assert.equal(humanize('UPLOAD_RACE_DETECTED'),'Upload race detected');
assert.equal(humanize('Already readable'),'Already readable');assert.equal(humanize(null),'Unknown');
assert.equal(humanize('toString'),'toString');
assert.equal(reasonText('[redacted]: FORM_NOT_READY: Form still loading'),'Form still loading');
assert.equal(reasonText('CATEGORY: Something happened'),'Something happened');
assert.equal(reasonText('Keep the CATEGORY: in the middle'),'Keep the CATEGORY: in the middle');
const time=timestamp(stamp);
assert.equal(time.title,stamp);assert.equal(time.dateTime,stamp);
assert.match(time.textContent,/Oct 8, 11:38 AM \(2h ago\)/);
assert.equal(timestamp(null).textContent,'Unknown');assert.equal(timestamp('').textContent,'Unknown');
assert.equal(timestamp('invalid').textContent,'Unknown');assert.equal(timestamp('invalid').title,'invalid');
assert.match(timestamp('2026-10-08T19:38:06Z').textContent,/\(in 2h\)/);
assert.match(timestamp('2026-10-08T17:38:00Z').textContent,/\(just now\)/);
assert.match(timestamp('2026-10-08').textContent,/^Oct 8 \(/);
assert.equal(timestamp('2026-10-08').title,'2026-10-08');
function descendants(el){return [el,...el.children.flatMap(descendants)];}
function titled(id,raw,label){assert.ok(descendants(document.getElementById(id)).some(el=>el.title===raw&&el.textContent===label));}
const app={application_id:4503,url:'https://example.test/application/4503',company:'Research & Systems',title:'Platform & Site Reliability',state:'Ready to submit',
 state_code:'READY',application_state:'READY_TO_SUBMIT',readiness:'RECONSTRUCTABLE_CHECKPOINT',security:'PASSIVE_PROTECTION_DETECTED',
 reason:'[redacted]: FORM_NOT_READY: Form still loading',error_category:'FORM_NOT_READY',updated_at:stamp,
 verified_fields:4,attempts:1,max_retries:3,retry_possible:true};
renderManual({applications:[app]});
assert.match(document.getElementById('manual').textContent,/#4503 \u00b7 Platform & Site Reliability/);
assert.ok(!document.getElementById('manual').textContent.includes('[redacted]'));
titled('manual','READY','Ready to submit');titled('manual',stamp,time.textContent);
renderFailed({applications:[app]});titled('failed','FORM_NOT_READY','Form did not load');
assert.equal(document.getElementById('count-needs-you').textContent,'1');assert.equal(document.getElementById('count-failed').textContent,'1');
render({rows:[{...app,rank:1,listing_status:'ACTIVE',last_liveness_result:'OPEN',last_liveness_at:stamp,posted_at:stamp}],
 sweep:{counts:{},finished_at:stamp}});
titled('rows','ACTIVE','Active');titled('rows','OPEN','Open');titled('rows',stamp,time.textContent);
const queueRow=document.getElementById('rows').children[1];
const posted=queueRow.children.find(el=>el.dataset.label==='Posted').children[1];
assert.equal(posted.textContent,'Oct 8');assert.equal(posted.dateTime,stamp);assert.match(posted.title,/2h ago/);
const check=queueRow.children.find(el=>el.dataset.label==='Listing check').children[1];
assert.equal(check.children[0].textContent,'Open');assert.equal(check.children[0].children[0].className,'dot');
assert.match(check.children[0].title,/Listing: Active \| Checked:.*2h ago/);
assert.equal(check.children[1].className,'listing-meta');
for(const id of ['manual','failed','rows']){const open=descendants(document.getElementById(id)).find(el=>el.attributes['aria-label']==='Open application #4503 in a new tab');
 assert.equal(open.textContent,'');assert.equal(open.children[0].tag,'svg');assert.equal(open.children[0].attributes['aria-hidden'],'true');
 assert.equal(open.children[0].children[0].tag,'path');assert.equal(open.parent.className,'role');}
assert.equal(document.getElementById('count-priority').textContent,'1');
renderStatus({phase:'FILLING',updated_at:stamp,applications:[app]});
titled('run-status','FILLING','Filling');titled('run-status',stamp,time.textContent);
titled('applications','READY_TO_SUBMIT','Ready to submit');
titled('applications','RECONSTRUCTABLE_CHECKPOINT','Filled, tab closed');
titled('applications','PASSIVE_PROTECTION_DETECTED','Passive bot protection');
assert.match(document.getElementById('applications').textContent,/Research & Systems#4503 \u00b7 Platform & Site Reliability/);
assert.equal(document.getElementById('count-run').textContent,'1');
renderStatus({phase:'REVIEW',applications:[{...app,application_state:null,lifecycle:'MANUAL_REQUIRED'},app]});
titled('applications','MANUAL_REQUIRED','Needs you');assert.equal(document.getElementById('count-run').textContent,'2');
renderStatus({applications:[]});assert.equal(document.getElementById('count-run').textContent,'0');
assert.match(document.getElementById('run-status').textContent,/Not published/);
renderStatus({applications:[app]});
const oldRow=document.getElementById('applications').children[1];renderStatus({applications:[app]});
assert.equal(document.getElementById('applications').children[1],oldRow);
for(const tab of tabIds){const toolbar=testTools.find(el=>el.dataset.tools===tab);assert.equal(toolbar.children[0].type,'search');
 assert.equal(typeof views,'undefined');assert.equal(toolbar.children.length,1);
 toolbar.children[0].value='no match';toolbar.children[0].listeners.input();assert.match(document.getElementById(bodyIds[tab]).textContent,/No matches/);
 toolbar.children[0].value='';toolbar.children[0].listeners.input();}
renderApplied({applications:[{...app,marked_at:stamp,reversible:true},{...app,application_id:4504,reversible:false}]});
assert.ok(descendants(document.getElementById('applied')).some(el=>el.attributes['aria-label']==='Put back #4503'&&!el.disabled));assert.ok(descendants(document.getElementById('applied')).some(el=>el.title==='Recorded in Discord'));
assert.ok(descendants(document.getElementById('applied')).some(el=>el.attributes['aria-label']==='Put back #4503'));
renderApplied({applications:[]});
for(const id of ['run','needs-you','failed','priority'])assert.ok(descendants(document.getElementById(bodyIds[id])).some(el=>el.attributes['aria-label']==='Mark #4503 as applied'));
const originalReload=reload;reload=async()=>[];
(async()=>{
 let resolveMark;const calls=[];post=(action,body)=>{calls.push([action,body]);return new Promise(resolve=>{resolveMark=resolve;});};
 const marking=markApplied(app);
 for(const id of ['run','needs-you','failed','priority'])assert.equal(document.getElementById('count-'+id).textContent,'0');
 assert.equal(document.getElementById('count-applied').textContent,'1');
 resolveMark({application_id:4503,marked_at:stamp});await marking;
 assert.deepEqual(calls,[['applied',{application_id:4503}]]);assert.equal(document.getElementById('toast-message').textContent,'Marked #4503 as applied');
 assert.equal(toastId,4503);assert.equal(document.getElementById('toast').hidden,false);
 post=async(action,body)=>{calls.push([action,body]);return {application_id:4503};};await putBack(4503);
 assert.deepEqual(calls[1],['unapplied',{application_id:4503}]);assert.equal(document.getElementById('count-applied').textContent,'0');
 for(const id of ['run','needs-you','failed','priority'])assert.equal(document.getElementById('count-'+id).textContent,'1');
 post=async()=>{throw Error('Already guarded');};await markApplied(app);
 assert.equal(document.getElementById('count-applied').textContent,'0');assert.equal(document.getElementById('message').textContent,'Already guarded');
 for(const id of ['run','needs-you','failed','priority'])assert.equal(document.getElementById('count-'+id).textContent,'1');
 // CV buttons show busy state, open the authenticated preview and toast warnings.
 for(const id of ['needs-you','applied'])assert.ok(descendants(makeRow(id,app)).some(el=>el.attributes['aria-label']==='Generate Cover letter for #4503'));
 assert.ok(!descendants(makeRow('priority',app)).some(el=>el.attributes['aria-label']==='Generate Cover letter for #4503'));
 const cvCalls=[],cvPreview={};
 window.open=(...args)=>{cvCalls.push(args);return cvPreview;};
 let finishCV;post=(action,body)=>{cvCalls.push([action,body]);return new Promise(resolve=>{finishCV=resolve;});};
 const generating=generateCV(app);assert.deepEqual(cvCalls,[['cv',{application_id:4503}]]);assert.ok(generatingCVs.has(4503));
 const busyCV=cvButton(app);assert.equal(busyCV.disabled,true);assert.equal(busyCV.attributes['aria-busy'],'true');assert.equal(busyCV.children[0].attributes.class,'icon spinning');
 finishCV({file:'/api/cv/4503',warnings:['Add profile projects']});await generating;
 assert.deepEqual(cvCalls[1],['http://127.0.0.1:8765/api/cv/4503?token='+encodeURIComponent(token),'_blank']);
 assert.equal(document.getElementById('toast-message').textContent,'Add profile projects');assert.equal(generatingCVs.has(4503),false);assert.equal(cvButton(app).disabled,false);
 let questionCalls=[];post=async(action,body)=>{questionCalls.push(body);return body.answers?{file:'/api/cv/4503',warnings:[]}:{questions:['Why this role?', 'Start date?']};};
 const questionRun=generateCV(app);await Promise.resolve();
 const main=document.getElementById('main'),dialog=main.children.find(el=>el.tag==='dialog');assert.ok(dialog.open);assert.equal(dialog.attributes['aria-labelledby'],'cover-letter-title-4503');
 const fields=dialog.children.filter(el=>el.tag==='textarea'),buttons=dialog.children.at(-1).children;
 assert.equal(fields.length,2);assert.ok(!buttons[1].disabled);assert.ok(fields.every(field=>!field.required));
 fields[0].value='Python work';fields[1].value='   ';assert.ok(!buttons[1].disabled);buttons[1].click();await questionRun;
 assert.deepEqual(questionCalls[1],{application_id:4503,answers:[{question:'Why this role?',answer:'Python work'},{question:'Start date?',answer:''}]});assert.equal(dialog.open,false);assert.ok(!main.children.includes(dialog));
 const openCount=cvCalls.length;const cancelled=generateCV(app);await Promise.resolve();const cancelDialog=main.children.find(el=>el.tag==='dialog');cancelDialog.children.at(-1).children[0].click();await cancelled;assert.equal(cvCalls.length,openCount);assert.equal(generatingCVs.has(4503),false);
 post=async()=>{throw Error('CV failed');};await generateCV(app);assert.equal(cvCalls.length,openCount);assert.equal(document.getElementById('message').textContent,'CV failed');assert.equal(document.getElementById('toast-message').textContent,'CV failed');
 window.open=()=>null;post=async()=>({file:'/api/cv/4503',warnings:[]});await generateCV(app);
 const fallback=document.body.children.at(-1),link=fallback.children.find(el=>el.tag==='a');
 assert.equal(link.textContent,'Open cover letter');assert.equal(link.href,'http://127.0.0.1:8765/api/cv/4503?token='+encodeURIComponent(token));assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');
 assert.ok(!fallback.hidden);link.click();assert.ok(!document.body.children.includes(fallback));
 await generateCV(app);const dismissed=document.body.children.at(-1);dismissed.children.find(el=>el.tag==='button').click();assert.ok(!document.body.children.includes(dismissed));
 // All tabs use the compact list.
 renderApplied({applications:[]});
 renderApplied({applications:[{...app,marked_at:stamp,reversible:false}]});
 const permanent=descendants(document.getElementById('applied')).find(el=>el.type==='checkbox');
 assert.equal(permanent.checked,true);assert.equal(permanent.disabled,true);assert.equal(permanent.title,'Recorded in Discord');
 renderApplied({applications:[]});
 // Removal renumbers optimistically; a failed POST restores both the row and ranks.
 render({rows:[{...app,rank:1},{...app,application_id:4505,rank:2}],sweep:{}});
 let rejectMark;post=()=>new Promise((resolve,reject)=>{rejectMark=reject;});const failedMark=markApplied(app);
 assert.ok(descendants(document.getElementById('rows')).some(el=>el.className==='cell rank'&&el.textContent==='Rank1'));
 assert.ok(!descendants(document.getElementById('rows')).some(el=>el.className==='cell rank'&&el.textContent==='Rank2'));
 rejectMark(Error('Try again'));await failedMark;
 assert.ok(descendants(document.getElementById('rows')).some(el=>el.className==='cell rank'&&el.textContent==='Rank2'));
 // Unchecking removes the applied row before the response, and Undo marks it again.
 post=async()=>({marked_at:stamp});await markApplied(app);
 let finishBack;post=()=>new Promise(resolve=>{finishBack=resolve;});const backing=putBack(app.application_id);
 assert.equal(document.getElementById('count-applied').textContent,'0');
 assert.equal(document.getElementById('count-priority').textContent,'2');
 finishBack({});await backing;assert.equal(toastAction,'applied');assert.equal(toastItem.application_id,4503);
 post=async()=>({marked_at:stamp});await document.getElementById('undo').listeners.click();
 // Let the asynchronous Undo POST finish.
 await Promise.resolve();await Promise.resolve();assert.equal(document.getElementById('count-applied').textContent,'1');
 // Not eligible works in list and grid, validates Other, preserves text and supports Undo.
 await putBack(app.application_id);
 assert.ok(descendants(makeRow('needs-you',app)).some(el=>el.attributes['aria-label']==='Mark #4503 as not eligible'));
 const trigger=descendants(document.getElementById('rows')).find(el=>el.attributes['aria-label']==='Mark #4503 as not eligible');trigger.click();
 assert.equal(eligibilityDialog.open,true);assert.equal(document.getElementById('eligibility-title').textContent,'Not eligible - '+app.company+' / '+app.title);
 assert.equal(eligibilityConfirm.disabled,true);assert.equal(document.getElementById('eligibility-reasons').children.length,7);
 document.getElementById('eligibility-cancel').click();assert.equal(eligibilityDialog.open,false);assert.equal(document.activeElement,trigger);
 trigger.click();document.getElementById('eligibility-reasons').children[6].click();assert.equal(eligibilityConfirm.disabled,true);
 eligibilityNote.value='   ';eligibilityNote.listeners.input();assert.equal(eligibilityConfirm.disabled,true);
 eligibilityNote.value='<script>fixture only</script>';eligibilityNote.listeners.input();assert.equal(eligibilityConfirm.disabled,false);
 post=async()=>{throw Error('Fixture refusal');};await markNotEligible();assert.equal(eligibilityDialog.open,true);assert.equal(document.getElementById('eligibility-error').textContent,'Fixture refusal');assert.equal(document.getElementById('count-priority').textContent,'2');
 const eligibilityCalls=[];post=async(action,body)=>{eligibilityCalls.push([action,body]);return {marked_at:stamp};};await markNotEligible();
 assert.deepEqual(eligibilityCalls[0],['not-eligible',{application_id:4503,reason:'Other',note:'<script>fixture only</script>'}]);
 assert.equal(eligibilityDialog.open,false);assert.equal(document.getElementById('count-priority').textContent,'1');assert.ok(descendants(document.getElementById('rows')).some(el=>el.className==='cell rank'&&el.textContent==='Rank1'));
 assert.ok(!descendants(document.getElementById('rows')).some(el=>el.className==='cell rank'&&el.textContent==='Rank2'));
 assert.ok(document.getElementById('not-eligible').textContent.includes('<script>fixture only</script>'));assert.ok(descendants(document.getElementById('not-eligible')).some(el=>el.attributes['aria-label']==='Restore #4503'));
 assert.equal(document.getElementById('toast-message').textContent,'Marked not eligible');assert.equal(toastAction,'eligible');
 document.getElementById('undo').click();await Promise.resolve();await Promise.resolve();
 assert.deepEqual(eligibilityCalls[1],['eligible',{application_id:4503}]);assert.equal(document.getElementById('count-priority').textContent,'2');assert.equal(notEligibleItems.length,0);
 // Reload refetches priority and every count, including when an older read is pending.
 const paths=[];request=async path=>{paths.push(path);return path==='/api/status'?{applications:[]}:path==='/api/priority'?{rows:[],sweep:{}}:{applications:[]};};
 let finishOld;loads.priority=new Promise(resolve=>{finishOld=()=>{delete loads.priority;resolve();};});
 statusLoading=null;const reading=originalReload();assert.deepEqual(new Set(paths),new Set(['/api/manual','/api/failed','/api/applied','/api/status']));finishOld();await reading;
 assert.deepEqual(new Set(paths),new Set(['/api/priority','/api/manual','/api/failed','/api/applied','/api/status']));
 assert.equal(document.getElementById('count-priority').textContent,'0');
 // Status failures disable controls with reasons and recover without a page reload.
 request=async()=>{throw Error('offline');};await loadStatus(true);
 assert.equal(statusOffline,true);assert.match(document.getElementById('run-status').textContent,/offline - retrying in 1s/);
 assert.equal(start.title,'Waiting for server');assert.equal(start.attributes['aria-description'],'Waiting for server');
 await loadStatus(true);assert.equal(nextStatusAt-Date.now(),2000);
 request=async()=>({applications:[],running:false,worker_locked:false});await loadStatus(true);
 assert.equal(statusOffline,false);assert.equal(start.disabled,false);
 for(const button of controls){assert.equal(button.disabled,true);assert.equal(button.title,'No worker is running');}
 renderStatus({applications:[],worker_locked:true});assert.equal(start.title,'Worker lock held by another process');
 renderStatus({applications:[],running:true,paused:true});assert.equal(controls[1].disabled,false);assert.equal(controls[0].title,'Worker is already paused');
 renderStatus({applications:[],running:false});let finishStart;post=()=>new Promise(resolve=>{finishStart=resolve;});
 const running=control('run',{count:5});assert.match(start.textContent,/Starting/);assert.equal(start.disabled,true);
 request=async()=>({applications:[],running:true});finishStart({pid:123});await running;
 assert.equal(document.getElementById('worker-label').textContent,'Running');assert.equal(controls[0].disabled,false);
 // Reload is data-only and displays elapsed and absolute completion time.
 reload=async()=>[true,true,true,true,true];refreshBusy=false;lastReloadAt=0;
 let finishReload;reload=()=>new Promise(resolve=>{finishReload=resolve;});
 const reloading=reloadData();reloadStarted=Date.now()-2000;updateReloadTime();assert.equal(reloadState.textContent,'Reloading…');
 finishReload([true,true,true,true,true]);await reloading;assert.equal(reloadState.textContent,'');assert.ok(reloadState.title);assert.ok(reloadButton.title.includes(reloadState.title));
 lastReloadAt=Date.now()-12000;updateReloadTime();assert.equal(reloadState.textContent,'');assert.ok(reloadState.title);
 // Discovery uses the server result and persists completion metadata.
 searchBusy=true;searchStarted=Date.now()-72000;updateSearchTime();assert.equal(discoveryLabel.textContent,'Searching... 1m 12s');
 finishSearch({status:'succeeded',started_at:'2026-10-08T17:36:01.446586Z',finished_at:'2026-10-08T17:38:06.446586Z',result:{new:7}});
 assert.match(discoveryState.textContent,/Last updated 0 hours ago/);
 assert.equal(discoveryLabel.textContent,'Find new listings');const saved=storage.get('autoapply.lastSearch');lastSearch=null;restoreSearch();
 assert.equal(lastSearch.newListings,7);assert.equal(lastSearch.duration,125000);assert.equal(storage.get('autoapply.lastSearch'),saved);

 // Settings navigation, inline validation, unsaved edits and resume text stay offline.
 const setting={path:'dashboard.default_batch_size',label:'Default batch size',type:'number',value:5,min:1,max:10,step:1,help:'Initial size',readonly:false};
 const confidence={...setting,path:'application.min_confidence',label:'Minimum confidence',value:.95,min:.90,max:1,step:.01};
 renderSettings({sections:[{key:'general',label:'General',fields:[]},{key:'dashboard',label:'Dashboard',fields:[setting,confidence]},{key:'resume',label:'Resume',fields:[]},{key:'safety',label:'Safety',fields:[{path:'application.auto_submit',label:'Automatic submission',type:'boolean',value:false,readonly:true,reason:'Final submission stays manual.'},{path:'discord.enabled',label:'Discord enabled',type:'boolean',value:true,readonly:true,reason:'Managed outside the dashboard.'},{path:'processing.max_retries',label:'Maximum retries',type:'number',value:3,readonly:true,reason:'Safety control.'}]}],restart_required_note:'Next start'});
 const confidenceInput=settingsInputs.get(confidence.path);assert.equal(confidenceInput.min,.90);assert.equal(confidenceInput.max,1);assert.equal(confidenceInput.step,.01);
 for(const value of ['0.80','0.89','1.01'])assert.match(validateSetting(confidence,value),/from 0.9 to 1/);
 for(const value of ['0.90','0.95','1.0'])assert.equal(validateSetting(confidence,value),'');
 const locked=descendants(document.getElementById('settings-section-safety')).filter(el=>el.className==='locked-value');
 assert.deepEqual(locked.map(el=>el.textContent),['Off','On','3']);assert.match(locked[0].attributes['aria-label'],/Off\. Locked/);assert.match(locked[1].attributes['aria-label'],/On\. Locked/);
 selectTab('settings',true);assert.equal(document.activeElement.id,'tab-settings');
 chooseSettingsSection('dashboard');assert.equal(document.getElementById('settings-section-dashboard').hidden,false);assert.equal(document.getElementById('settings-section-resume').hidden,true);
 const batchInput=settingsInputs.get(setting.path);batchInput.value='11';batchInput.listeners.input();assert.match(settingsErrors.get(setting.path).textContent,/integer/);assert.equal(settingsDirty(),true);assert.equal(document.getElementById('settings-footer').hidden,false);
 batchInput.value='7';batchInput.listeners.input();assert.equal(settingsChanges[setting.path],7);assert.equal(settingsErrors.get(setting.path).textContent,'');
 batchInput.value='5';batchInput.listeners.input();assert.equal(settingsDirty(),false);
 assert.match(validateSetting({...setting,type:'text',max:40},'x'.repeat(41)),/40/);
 renderResume({filename:'resume.pdf',size:1024,sha256:'123456789abc',modified_at:stamp,archived:[{filename:'resume-old.pdf',size:1024,sha256:'oldhash',modified_at:stamp}]});
 assert.match(document.getElementById('resume-current').textContent,/1.0 KB.*123456789abc/);assert.match(document.getElementById('resume-archives').textContent,/resume-old.pdf/);
 const preview=document.getElementById('resume-current').children.find(el=>el.tag==='a');assert.equal(preview.href,'/api/resume/file');assert.equal(preview.target,'_blank');
 // Selection and cancellation never POST; the native picker stays visually hidden.
 const picker=document.getElementById('resume-picker'),choose=document.getElementById('resume-choose'),drop=document.getElementById('resume-drop'),upload=document.getElementById('resume-upload'),cancel=document.getElementById('resume-cancel'),progress=document.getElementById('resume-progress'),message=document.getElementById('resume-message');
 assert.equal(picker.type,'file');assert.equal(picker.accept,'application/pdf,.pdf');assert.equal(picker.className,'visually-hidden');assert.equal(picker.tabIndex,-1);assert.equal(picker.attributes['aria-label'],'Resume PDF');
 assert.equal(choose.tag,'button');assert.equal(choose.type,'button');choose.click();assert.equal(picker.clicked,true);
 assert.equal(drop.parent.children[1].id,'resume-current');assert.equal(drop.parent.children[2],drop);assert.equal(drop.children[0].tag,'svg');assert.equal(drop.children[0].attributes['aria-hidden'],'true');assert.match(drop.textContent,/Drop a PDF here orChoose file/);
 let xhr;class FakeXHR{constructor(){xhr=this;this.upload={};this.headers={};}open(...args){this.opened=args;}setRequestHeader(k,v){this.headers[k]=v;}send(file){this.sent=file;}}
 globalThis.XMLHttpRequest=FakeXHR;
 const file={name:'Selected resume.pdf',size:2048};picker.files=[file];picker.listeners.change();assert.equal(xhr,undefined);assert.match(document.getElementById('resume-file').textContent,/Selected resume.pdf \(2.0 KB\)/);assert.equal(document.getElementById('resume-actions').hidden,false);
 cancel.click();assert.equal(selectedResume,null);assert.equal(document.getElementById('resume-actions').hidden,true);assert.equal(picker.value,'');assert.equal(document.activeElement,choose);
 let prevented=0;const drag={preventDefault:()=>{prevented++;},dataTransfer:{files:[file]}};drop.listeners.dragover(drag);assert.equal(drop.classes.has('dragover'),true);assert.equal(drag.dataTransfer.dropEffect,'copy');
 drop.listeners.dragleave({relatedTarget:choose});assert.equal(drop.classes.has('dragover'),true);drop.listeners.dragleave({relatedTarget:null});assert.equal(drop.classes.has('dragover'),false);
 drop.listeners.drop(drag);assert.equal(drop.classes.has('dragover'),false);assert.equal(prevented,2);assert.equal(selectedResume,file);assert.equal(xhr,undefined);
 selectResume({name:'wrong.txt',size:1024});assert.equal(message.dataset.state,'error');assert.match(message.textContent,/Choose a PDF/);assert.equal(selectedResume,file);
 selectResume({name:'too-large.pdf',size:10*1024*1024+1});assert.equal(message.dataset.state,'error');
 upload.click();assert.equal(xhr.sent,file);assert.deepEqual(xhr.opened,['POST','/api/resume']);assert.equal(xhr.headers['Content-Type'],'application/pdf');assert.equal(xhr.headers['X-Dashboard-Token'],token);assert.equal(progress.hidden,false);assert.equal(progress.value,0);assert.equal(progress.max,100);
 for(const control of [picker,choose,upload,cancel])assert.equal(control.disabled,true);assert.equal(drop.attributes['aria-busy'],'true');
 drop.listeners.drop({preventDefault:()=>{},dataTransfer:{files:[{name:'other.pdf',size:512}]}});assert.equal(selectedResume,file);
 xhr.upload.onprogress({lengthComputable:true,loaded:512,total:2048});assert.equal(progress.value,25);assert.match(message.textContent,/25%/);xhr.upload.onprogress({lengthComputable:false});assert.equal(progress.value,25);
 xhr.status=409;xhr.responseText=JSON.stringify({error:'Stop the worker before replacing the resume'});await xhr.onload();assert.equal(message.dataset.state,'error');assert.match(message.textContent,/Stop the worker/);assert.equal(selectedResume,file);assert.equal(progress.hidden,true);assert.equal(upload.disabled,false);
 upload.click();xhr.onerror();assert.equal(resumeUploading,false);assert.match(message.textContent,/check the connection/);assert.equal(selectedResume,file);
 upload.click();xhr.ontimeout();assert.equal(message.dataset.state,'error');assert.equal(upload.disabled,false);
 upload.click();xhr.status=200;xhr.responseText='invalid JSON';await xhr.onload();assert.equal(message.dataset.state,'error');assert.equal(selectedResume,file);
 request=async path=>{assert.equal(path,'/api/resume');return {filename:'resume.pdf',size:2048,sha256:'newhash',modified_at:stamp,archived:[]};};
 upload.click();xhr.status=200;xhr.responseText=JSON.stringify({affected_applications:[1,2,3,4],note:'Review the Answers tab'});await xhr.onload();assert.equal(message.textContent,'Resume replaced - 4 applications need a fresh upload');assert.equal(message.dataset.state,'success');assert.equal(document.getElementById('resume-note').textContent,'Review the Answers tab');assert.equal(selectedResume,null);assert.equal(document.getElementById('resume-actions').hidden,true);assert.equal(progress.hidden,true);assert.equal(choose.disabled,false);assert.match(document.getElementById('resume-current').textContent,/newhash/);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    env = dict(os.environ, TZ='America/New_York', LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
    script_path = tmp_path / 'dashboard-renderers.js'
    script_path.write_text(harness + script[:script.index("document.getElementById('tab-settings').addEventListener")]+assertions, encoding='utf-8')
    result = subprocess.run([executable, str(script_path)], text=True, capture_output=True, env=env, timeout=15)
    assert result.returncode == 0, result.stderr



def test_javascript_pagination_sorting_and_application_segments(tmp_path):
    """Exercise the real UI offline with enough rows to span multiple pages."""
    import ast
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == 'test_javascript_display_formatting_and_renderers')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
    harness = ast.literal_eval(assignment.value)
    script = PAGE.split('<script nonce="__TOKEN__">', 1)[1].split('</script>', 1)[0]
    script = script[:script.index("document.getElementById('tab-settings').addEventListener")]
    checks = r"""
const sample=Array.from({length:1083},(_,i)=>({application_id:i+10000,company:'Company '+i,title:'Role',ats:'Lever',posted_at:'2026-10-01'}));
render({rows:sample,sweep:{}});
const body=document.getElementById('rows'),footer=document.getElementById('pagination-priority');
assert.equal(perPage,50);assert.equal(body.children.length,51);
assert.equal(footer.children[0].textContent,'1\u201350 of 1,083');
assert.equal(footer.children[1].attributes['aria-label'],'Previous page');assert.equal(footer.children[1].disabled,true);
assert.equal(footer.children[2].attributes['aria-label'],'Next page');assert.equal(footer.children[2].disabled,false);
footer.children[2].click();assert.equal(pages.priority,2);assert.equal(body.children[1].dataset.applicationId,10050);
assert.equal(footer.children[0].textContent,'51\u2013100 of 1,083');assert.equal(footer.children[3].textContent,'Page 2 of 22');
pages.priority=22;draw('priority');assert.equal(body.children.length,34);assert.equal(footer.children[2].disabled,true);
let input=footer.children[4].children[0];assert.equal(input.value,'50');assert.equal(input.min,10);assert.equal(input.max,200);
for(const bad of ['', 'abc', '15.5','Infinity']){input.value=bad;input.listeners.change();assert.equal(input.value,'50');assert.equal(perPage,50);}
input.value='999';input.listeners.blur();assert.equal(perPage,200);assert.equal(storage.get('autoapply.perPage'),'200');assert.equal(pages.priority,1);
input=footer.children[4].children[0];input.value='1';input.listeners.change();assert.equal(perPage,10);
input=footer.children[4].children[0];input.value='50';input.listeners.change();
pages.priority=2;draw('priority');pendingMarks.set(10000,{});draw('priority');assert.equal(pages.priority,2);
assert.equal(body.children[1].dataset.applicationId,10051);pendingMarks.clear();
pages.priority=22;models.priority=sample.slice(0,70);draw('priority');assert.equal(pages.priority,2);
draw('priority');assert.ok(!body.classes.has('grid'));assert.equal(body.children.length,21);
const filter=testTools.find(el=>el.dataset.tools==='priority').children[0];filter.value='company 1';filter.listeners.input();assert.equal(pages.priority,1);
filter.value='';filter.listeners.input();models.priority=sample.slice(0,70);draw('priority');
const rank=body._head.children[1],application=body._head.children[2];
assert.equal(rank.attributes['aria-sort'],'ascending');rank.children[0].click();assert.equal(rank.attributes['aria-sort'],'descending');assert.equal(rank.children[0].children[1].textContent,'\u2193');assert.equal(body.children[1].dataset.applicationId,10069);
rank.children[0].click();assert.equal(rank.attributes['aria-sort'],'ascending');assert.equal(body.children[1].dataset.applicationId,10000);
for(let i=0;i<4;i++)application.children[0].click();assert.equal(application.attributes['aria-sort'],'descending');assert.equal(rank.attributes['aria-sort'],'none');
assert.deepEqual(JSON.parse(storage.get('autoapply.sort.priority')),{key:'company',direction:'descending'});assert.equal(pages.priority,1);
for(const id of tabIds){const head=document.getElementById(bodyIds[id])._head;for(const header of head.children.filter(el=>el!==head._chromeSelection)){assert.equal(header.attributes.role,'columnheader');assert.equal(header.children[0].tag,'button');}}
const stable=[{company:'alpha',rank:10,posted_at:'2026-10-01'},{company:'Alpha',rank:2,posted_at:'2025-10-01'},{company:'Beta',rank:1,posted_at:'2024-10-01'}];
sorts.priority={key:'company',direction:'ascending'};assert.deepEqual(sortedItems('priority',stable),stable);
sorts.priority={key:'rank',direction:'ascending'};assert.deepEqual(sortedItems('priority',stable).map(a=>a.rank),[1,2,10]);
sorts.priority={key:'posted_at',direction:'ascending'};assert.deepEqual(sortedItems('priority',stable).map(a=>a.rank),[1,2,10]);
renderManual({applications:sample.slice(0,7)});renderFailed({applications:sample.slice(20,24)});renderApplied({applications:sample.slice(10,16),not_eligible:[{...sample[7],reason:'Other',note:'Fixture',reversible:true}]});
assert.equal(document.getElementById('count-applications').textContent,'7');assert.equal(document.getElementById('count-failed').textContent,'4');assert.equal(document.getElementById('count-applied').textContent,'7');
const excluded=document.getElementById('not-eligible');assert.equal(excluded._head.children.length,4);excluded._head.children[1].children[0].click();assert.equal(excluded._head.children[1].attributes['aria-sort'],'ascending');
document.getElementById('main').scrollTop=100;selectTab('applications');assert.equal(document.getElementById('main').scrollTop,0);
for(const id of ['needs-you','failed','applied']){selectTab(id);assert.equal(applicationSegment,'applied');assert.equal(storage.get('autoapply.applicationSegment'),'applied');assert.equal(document.getElementById('panel-applied').hidden,false);assert.equal(document.getElementById('segment-'+id).hidden,true);}
// Persisted preferences load safely, including unavailable browser storage.
"""
    path = tmp_path / 'pagination.js'
    path.write_text(harness + script + checks, encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    for seed, expected in [
        ("storage.set('autoapply.perPage','100');storage.set('autoapply.sort.priority',JSON.stringify({key:'ats',direction:'descending'}));storage.set('autoapply.applicationSegment','failed');", "assert.equal(perPage,100);assert.equal(sorts.priority.key,'ats');assert.equal(applicationSegment,'applied');"),
        ("storage.set('autoapply.perPage','3.5');storage.set('autoapply.sort.priority','invalid');", "assert.equal(perPage,50);assert.equal(sorts.priority.key,'rank');"),
        ("localStorage.getItem=()=>{throw Error('denied');};localStorage.setItem=()=>{throw Error('denied');};", "assert.equal(perPage,50);render({rows:[],sweep:{}});selectSegment('applied');"),
    ]:
        path.write_text(harness + seed + script + expected, encoding='utf-8')
        result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr



@pytest.mark.browser
def test_content_scroll_and_pagination_layout_offline():
    """Render static fixture data with every browser request blocked."""
    from playwright.sync_api import sync_playwright
    executable = next((path for path in (
        Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if path.is_file()), None)
    if executable is None:
        pytest.skip('System Chromium is unavailable')
    page_source = PAGE.replace('__TOKEN__', 'fixture').replace('__BATCH_SIZE__', '5').replace('__INITIAL__', '{"rows":[],"sweep":{}}')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        try:
            context = browser.new_context()
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(page_source)
            page.evaluate("""render({rows:Array.from({length:103},(_,i)=>({application_id:10000+i,company:'Fixture '+i,title:'Intern',ats:'Lever'})),sweep:{}});selectTab('priority');""")
            for width in (1280, 375):
                page.set_viewport_size({'width': width, 'height': 600})
                main = page.locator('main')
                navbar = page.locator('.navbar')
                before = navbar.bounding_box()
                assert main.evaluate('(el)=>el.scrollHeight>el.clientHeight')
                main.evaluate('(el)=>el.scrollTop=el.scrollHeight')
                assert navbar.bounding_box() == before
                assert page.evaluate('document.documentElement.scrollHeight<=innerHeight')
                assert main.bounding_box()['y'] == before['height']
                assert page.locator('#rows .item').count() == 50
                assert page.locator('#pagination-priority .range').inner_text() == '1\u201350 of 103'
                assert page.get_by_role('button', name='Next page', exact=True).is_enabled()
                next_page = page.get_by_role('button', name='Next page', exact=True)
                next_page.focus()
                next_page.press('Enter')
                assert next_page.evaluate('(el)=>el===document.activeElement')
                assert page.locator('#pagination-priority .range').inner_text() == '51\u2013100 of 103'
                page.evaluate("pages.priority=1;draw('priority');selectTab('run');")
                assert main.evaluate('(el)=>el.scrollTop') == 0
                page.evaluate("selectTab('priority');")
            page.evaluate("draw('priority');")
            assert page.locator('#rows .list-head button').count() == 6
            assert page.locator('[data-view]').count() == 0
        finally:
            browser.close()


def test_javascript_chrome_selection_status_cancel_and_cover_popup(tmp_path):
    import ast
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == 'test_javascript_display_formatting_and_renderers')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
    harness = ast.literal_eval(assignment.value)
    harness = harness.replace('const setInterval=()=>{},setTimeout=()=>{},clearTimeout=()=>{};',
                              'const scheduled=[];const setInterval=()=>{},setTimeout=fn=>{scheduled.push(fn);return scheduled.length},clearTimeout=()=>{};')
    script = PAGE.split('<script nonce="__TOKEN__">', 1)[1].split('</script>', 1)[0]
    script = script[:script.index("document.getElementById('tab-settings').addEventListener")]
    checks = r"""
(async()=>{
const sample=[1,2,3].map(application_id=>({application_id,url:'https://fixture.test/'+application_id,company:'Fixture',title:'Intern'}));
render({rows:sample,sweep:{}});renderManual({applications:[{...sample[0],application_id:4}]});
const check=chromeCheckbox(sample[0]).children[0];check.checked=true;check.listeners.change();
assert.deepEqual([...chromeSelected],[1]);assert.equal(document.getElementById('chrome-run-priority').textContent,'Run (1)');assert.equal(document.getElementById('chrome-run-priority').attributes['aria-label'],'Run in Chrome (1)');
const controls=document.getElementById('chrome-selection-priority');controls.children[0].children[0].value='2';controls.children[1].click();assert.deepEqual([...chromeSelected],[1,2]);
const head=document.getElementById('rows')._head,all=head._chromeSelection.children[0];
assert.equal(all.attributes['aria-label'],'Select all visible for Chrome in Queue');assert.equal(all.indeterminate,true);
filters.priority='2';draw('priority');assert.equal(head._chromeItems.length,1);all.checked=true;all.listeners.change();assert.deepEqual([...chromeSelected],[1,2]);
all.checked=false;all.listeners.change();assert.deepEqual([...chromeSelected],[1]);filters.priority='';draw('priority');all.checked=true;all.listeners.change();assert.deepEqual([...chromeSelected],[1,2,3]);
all.checked=false;all.listeners.change();assert.deepEqual([...chromeSelected],[]);
controls.children[1].click();assert.deepEqual([...chromeSelected],[1,2]);
const compact=makeRow('priority',sample[0]);assert.equal(compact.children[0].className,'chrome-check');assert.equal(compact.children[0].textContent,'');assert.equal(compact.children[0].children[0].attributes['aria-label'],'Select #1 for Chrome');
assert.equal(typeof makeCard,'undefined');
let posted=[];post=async(action,body)=>{posted.push({action,body});return {items:[{...sample[0],state:action.endsWith('cancel')?'skipped':'pending'}],poll_age_seconds:null};};
await startChromeRun();assert.deepEqual(posted[0],{action:'ext-run',body:{application_ids:[1,2]}});
assert.equal(document.getElementById('chrome-run-panel').hidden,false);assert.match(document.getElementById('chrome-run-hint').textContent,/Open Chrome/);
renderChromeRun({items:['pending','opened','filled','needs_input','failed','skipped'].map((state,i)=>({...sample[0],application_id:i+1,state})),poll_age_seconds:1});
assert.match(document.getElementById('chrome-run-status').textContent,/5\/6.*opened.*Fixture/);assert.equal(document.getElementById('chrome-run-items').children.length,0);assert.equal(document.getElementById('chrome-run-hint').textContent,'');
await document.getElementById('chrome-run-cancel').listeners.click();assert.equal(posted[1].action,'ext-run/cancel');assert.equal(document.getElementById('chrome-run-cancel').disabled,true);
assert.equal(document.getElementById('chrome-run-panel').hidden,true);scheduled.at(-1)();assert.equal(document.getElementById('chrome-run-panel').hidden,true);assert.match(document.getElementById('toast-message').textContent,/Chrome run finished: 1 skipped/);
assert.equal(document.getElementById('chrome-last-run').hidden,false);assert.equal(document.getElementById('chrome-last-run').open,false);assert.deepEqual(document.getElementById('chrome-run-items').children.map(item=>item.textContent),['#1 Fixture - Intern - skipped']);assert.equal(document.getElementById('chrome-run-panel').hidden,true);
renderChromeRun({items:[{...sample[0],state:'filled'},{...sample[1],state:'needs_input'}]});scheduled.at(-1)();assert.equal(document.getElementById('chrome-run-panel').hidden,true);assert.match(document.getElementById('toast-message').textContent,/1 filled, 1 needs input/);
post=async()=>({file:'/fixture.pdf',warnings:[]});let opened={opener:'original'},openArgs;window.open=(...args)=>{openArgs=args;return opened;};let fallback=0;coverLetterLink=()=>fallback++;
await generateCV(sample[0]);assert.deepEqual(openArgs,['http://127.0.0.1:8765/fixture.pdf?token=__TOKEN__','_blank']);assert.equal(opened.opener,null);assert.equal(fallback,0);
window.open=()=>null;await generateCV(sample[1]);assert.equal(fallback,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    path = tmp_path / 'chrome-run.js'
    path.write_text(harness + script + checks, encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_compact_chrome_run_markup_and_theme_scrollbars():
    assert '<h3>Chrome run</h3>' not in PAGE
    assert '>Cancel run</button>' not in PAGE
    assert 'aria-label="Cancel Chrome run"' in PAGE
    assert '<details id="chrome-last-run" class="chrome-last-run" hidden>' in PAGE
    for css in (PAGE, Path('extension/shared.css').read_text(encoding='utf-8')):
        assert 'scrollbar-width:thin' in css and 'scrollbar-color:' in css
        assert '::-webkit-scrollbar-thumb' in css and 'border-radius:10px' in css


def test_t049_queue_timings(dashboard, config, db, listing):
    """674 unique ~3.3k descriptions; cold parser, forced rebuild, then queue cache."""
    import time
    from autoapply.requirements import _extract_requirements
    first = queue(db, config, listing, 1)[0]
    def description(i):
        intro = (f'Fixture engineering team {i} develops tools for local testing. '
                 'Our engineers collaborate on product design and maintain reliable systems. '
                 'The internship provides project experience and code reviews. ')
        return ('Job description\n' + intro * 13 + '\nResponsibilities\n'
                + 'Build and test internal tools with the team.\n' * 12
                + 'Required qualifications\nCurrently enrolled in a Bachelor degree program.\n'
                + 'Preferred qualifications\nPython experience preferred.\nSQL knowledge preferred.\n'
                + 'Benefits\nMentoring and learning opportunities.\n')
    def clone(table, changes):
        columns = [r['name'] for r in db.rows('PRAGMA table_info(' + table + ')') if r['name'] != 'id']
        source = db.one('SELECT * FROM ' + table + ' ORDER BY id LIMIT 1')
        values = [changes.get(k, source[k]) for k in columns]
        return db.conn.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', values).lastrowid
    ids = [first]
    db.conn.execute('BEGIN')
    with db.conn:
        db.conn.execute('UPDATE jobs SET description=?', (description(0),))
        for i in range(1, 674):
            job_id = clone('jobs', dict(company=f'Fixture {i}', canonical_url=f'https://example.test/jobs/{i}', identity_key=f'fixture-{i}', description=description(i)))
            ids.append(clone('applications', dict(job_id=job_id)))
    _extract_requirements.cache_clear()
    start = time.perf_counter()
    cold = dashboard.priority()['rows']
    cold_seconds = time.perf_counter() - start
    cold_cache = _extract_requirements.cache_info()._asdict()
    dashboard._priority_at = 0
    start = time.perf_counter()
    rebuilt = dashboard.priority()['rows']
    rebuild_seconds = time.perf_counter() - start
    start = time.perf_counter()
    warm = dashboard.priority()['rows']
    warm_seconds = time.perf_counter() - start
    assert len(cold) == len(rebuilt) == len(warm) == len(ids)
    assert [r['application_id'] for r in cold] == [r['application_id'] for r in rebuilt] == [r['application_id'] for r in warm]
    evidence = dict(rows=len(cold), unique_descriptions=674,
                    average_description_chars=sum(len(description(i)) for i in range(674))/674,
                    cold_seconds=cold_seconds, forced_rebuild_seconds=rebuild_seconds,
                    warm_seconds=warm_seconds, cold_parser_cache=cold_cache,
                    final_parser_cache=_extract_requirements.cache_info()._asdict())
    output = Path('.tmp/review/t049')
    output.mkdir(parents=True, exist_ok=True)
    (output/'representative-timings.json').write_text(json.dumps(evidence, indent=2))
    print('T049 representative local fixture:', json.dumps(evidence))


@pytest.mark.browser
def test_t051_dashboard_screenshots(http_dashboard, config, db, listing):
    from playwright.sync_api import sync_playwright, expect
    ids = queue(db, config, listing, 9)
    db.set_setting('duplicate_submission_guard:'+str(ids[-1]), dict(source='USER_REPORTED',permanent=True,marked_at='2026-10-15T00:00:00+00:00'))
    server, dashboard = http_dashboard
    output = Path('.tmp/review/t051')
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        try:
            context = browser.new_context()
            calls = []
            def respond(route):
                incoming = route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort()
                    return
                target = incoming.url.removeprefix('http://127.0.0.1:8765')
                calls.append(target)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    status, body, headers = pool.submit(request, server, incoming.method, target, incoming.all_headers(), incoming.post_data).result()
                route.fulfill(status=status, body=body if isinstance(body,(str,bytes)) else json.dumps(body), headers=headers)
            context.route('**/*', respond)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for theme in ('light','dark'):
                page.emulate_media(color_scheme=theme)
                for width in (1440,800,500):
                    page.set_viewport_size(dict(width=width,height=900))
                    page.goto('http://127.0.0.1:8765/#priority')
                    page.locator('#rows .item').first.wait_for()
                    page.wait_for_function("document.getElementById('refresh').disabled===false")
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth')
                    assert width<=600 or page.locator('#rows .item').first.evaluate("e=>{const boxes=[...e.children].filter(c=>getComputedStyle(c).display!=='none').map(c=>c.getBoundingClientRect());return boxes.every((b,i)=>!i || boxes[i-1].right<=b.left+1)}")
                    gear=page.locator('#tab-settings')
                    gear_box=gear.bounding_box()
                    assert gear_box['y']>=0 and gear_box['y']+gear_box['height']<=56
                    assert gear_box['x']+gear_box['width']<=width
                    assert gear.evaluate("e=>e.contains(document.elementFromPoint(e.getBoundingClientRect().x+16,e.getBoundingClientRect().y+16))")
                    assert page.locator('.queue-action-bar').evaluate('e=>e.scrollWidth<=e.clientWidth')
                    expect(page.locator('#chrome-run-priority')).to_be_disabled()
                    expect(page.locator('#rows .item .rank').first).to_be_visible()
                    assert page.locator('#rows .item').first.evaluate("e=>{const b=[...e.children].filter(c=>getComputedStyle(c).display!=='none').map(c=>c.getBoundingClientRect());return b.every((r,i)=>b.every((s,j)=>i===j || Math.min(r.right,s.right)-Math.max(r.left,s.left)<=1 || Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top)<=1))}")
                    page.screenshot(path=str(output / f'dashboard-{width}-{theme}.png'))
                    checks=page.locator('#rows .item .chrome-check input')
                    checks.first.check()
                    expect(page.locator('#rows .list-head .chrome-check input')).to_have_js_property('indeterminate',True)
                    expect(page.locator('#chrome-run-priority')).to_be_enabled()
                    page.screenshot(path=str(output / f'dashboard-selected-{width}-{theme}.png'))
                    page.locator('#rows .list-head .chrome-check input').check()
                    expect(page.locator('#rows .list-head .chrome-check input')).to_be_checked()
                    page.screenshot(path=str(output / f'dashboard-all-{width}-{theme}.png'))
                    page.locator('#rows .list-head .chrome-check input').uncheck()
                    assert not any(p.startswith(('/api/settings','/api/answers')) for p in calls)
                    gear.click()
                    expect(gear).to_have_attribute('aria-selected','true')
                    page.locator('#settings-nav button').first.wait_for()
                    page.screenshot(path=str(output / f'dashboard-settings-{width}-{theme}.png'))
                    page.locator('#tab-priority').click()
                    page.locator('#tab-priority').press('End')
                    expect(gear).to_be_focused()
                    expect(gear).to_have_attribute('aria-selected','true')
                    page.locator('#tab-applications').click()
                    expect(page.locator('#tab-applications')).to_have_attribute('aria-selected','true')
                    expect(page.locator('#panel-applied')).to_be_visible()
                    expect(page.locator('#applied')).to_be_visible()
                    expect(page.locator('#segment-applied')).to_have_attribute('aria-pressed','true')
                    for selector in ('.application-switch','#segment-needs-you','#segment-failed','#segment-applied','#panel-needs-you','#panel-failed','#tab-run','#worker-status'):
                        expect(page.locator(selector)).to_be_hidden()
                    disabled=page.locator('#applied .applied-check input').first
                    expect(disabled).to_be_checked()
                    expect(disabled).to_be_disabled()
                    columns=page.locator('#applied').evaluate("e=>[getComputedStyle(e.querySelector('.list-head')).gridTemplateColumns,getComputedStyle(e.querySelector('.item')).gridTemplateColumns]")
                    assert columns[0]==columns[1], (width,columns)
                    page.screenshot(path=str(output / f'dashboard-disabled-{width}-{theme}.png'))
                    page.locator('#tab-priority').click()
                    calls.clear()
            assert not errors
            assert not any(p.startswith(('/api/settings','/api/answers')) for p in calls)
        finally:
            browser.close()


def test_t049_priority_time_boundaries(dashboard, config, db, listing, monkeypatch):
    from autoapply.freshness import utc, window
    ids=queue(db,config,listing,2)
    base=utc()
    db.execute('UPDATE jobs SET posted_at=? WHERE id=(SELECT job_id FROM applications WHERE id=?)', ((base-window(30)+timedelta(seconds=1)).isoformat(),ids[0]))
    db.execute('UPDATE applications SET retry_at=? WHERE id=?', ((base+timedelta(seconds=.5)).isoformat(),ids[1]))
    clock=[base]
    def virtual_utc(value=None):
        return clock[0] if value is None else utc(value)
    monkeypatch.setattr('autoapply.freshness.utc',virtual_utc)
    monkeypatch.setattr('autoapply.dashboard.utc',virtual_utc)
    monkeypatch.setattr('autoapply.dashboard.time.time',lambda:clock[0].timestamp())
    monkeypatch.setattr('autoapply.fill_batch.now',lambda:clock[0].isoformat())
    assert [r['application_id'] for r in dashboard.priority()['rows']]==[ids[0]]
    clock[0]=base+timedelta(seconds=.6)
    assert [r['application_id'] for r in dashboard.priority()['rows']]==ids
    clock[0]=base+timedelta(seconds=1.1)
    assert [r['application_id'] for r in dashboard.priority()['rows']]==[ids[1]]


def test_t049_priority_history_token(dashboard, config, db, listing, monkeypatch):
    queue(db,config,listing,1)
    selector=Mock(wraps=discover)
    monkeypatch.setattr('autoapply.dashboard.discover',selector)
    dashboard.priority()
    dashboard.priority()
    assert selector.call_count==1
    with db.history.locked():
        db.history._invalidate_readers()
    dashboard.priority()
    assert selector.call_count==2


def test_t049_canonical_history_reuse_and_duplicate_detection(dashboard, config, db, listing):
    from autoapply.conflicts import ConflictIndex
    from autoapply.archive import atomic_json
    first=queue(db,config,listing,1)[0]
    original=db.history.records[str(first)]
    with db.history.locked():
        for i in range(64):
            atomic_json(db.history.root/'unknown'/f'fixture-{i}'/'application.json',dict(original,application_id=f'legacy-{i}',application_state='UNKNOWN'))
        db.history._invalidate_readers()
    assert dashboard.history is db.history
    with dashboard.connection() as reader:
        index = ConflictIndex(reader)
        assert len(index.history) == 64
        records = reader.history.list_applications()
    assert dashboard.history.revision == db.history._cache_revision()
    assert len(records)==65
    assert sum(r['application_state']=='UNKNOWN' for r in records)==64
    assert {r['application_id'] for r in records}=={str(first),*(f'legacy-{i}' for i in range(64))}
    with db.history.locked():
        atomic_json(db.history.root/'unknown'/'duplicate'/'application.json',dict(original,application_id='legacy-0',application_state='UNKNOWN'))
        db.history._invalidate_readers()
    with pytest.raises(ValueError,match='Duplicate application ID'):
        with dashboard.connection() as reader:
            ConflictIndex(reader)


@pytest.mark.browser
def test_t054_dashboard_gear_and_favicon_source(http_dashboard, config, db, listing):
    from playwright.sync_api import sync_playwright
    queue(db,config,listing,3)
    server,_=http_dashboard
    output=Path('.tmp/review/t054')
    output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='chromium',headless=True)
        try:
            page=browser.new_page(viewport=dict(width=1440,height=900))
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script("window.cspViolations=[];document.addEventListener('securitypolicyviolation',event=>cspViolations.push({directive:event.effectiveDirective,uri:event.blockedURI}));")
            def respond(route):
                incoming=route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort();return
                with ThreadPoolExecutor(max_workers=1) as pool:
                    status,body,headers=pool.submit(request,server,incoming.method,incoming.url.removeprefix('http://127.0.0.1:8765'),incoming.all_headers(),incoming.post_data).result()
                route.fulfill(status=status,body=body if isinstance(body,(str,bytes)) else json.dumps(body),headers=headers)
            page.route('**/*',respond)
            page.goto('http://127.0.0.1:8765/#priority')
            page.locator('#rows .item').first.wait_for()
            gear=page.locator('#tab-settings')
            assert gear.evaluate("e=>{const b=e.getBoundingClientRect();return b.right<=innerWidth&&b.top>=0&&e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}")
            assert page.locator('link[rel=icon]').get_attribute('href').startswith('data:image/svg+xml,')
            assert 'M20%2015.5A9' in page.locator('link[rel=icon]').get_attribute('href')
            _,_,response_headers=request(server,'GET','/')
            csp=next(value for key,value in response_headers.items() if key.lower()=='content-security-policy')
            directives=dict(part.strip().split(' ',1) for part in csp.split(';'))
            assert directives['default-src']=="'none'" and directives['img-src']=='data:'
            assert directives['connect-src']=="'self'"
            assert directives['script-src']==directives['style-src']==f"'nonce-{server.dashboard.token}'"
            assert all(directives[name]=="'none'" for name in ['base-uri','frame-ancestors','form-action'])
            # Decode and paint the actual favicon URI in the actual response CSP.
            # The in-memory handler fixture never starts the private dashboard.
            rendered=page.evaluate("""async()=>{
                const image=new Image();image.src=document.querySelector('link[rel=icon]').href;
                await image.decode();
                const canvas=document.createElement('canvas');canvas.width=canvas.height=24;
                const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0,24,24);
                const pixels=ctx.getImageData(0,0,24,24).data;
                return {width:image.naturalWidth,height:image.naturalHeight,painted:[...pixels].some((v,i)=>i%4===3&&v>0),uri:canvas.toDataURL()};
            }""")
            assert rendered['width']>0 and rendered['height']>0 and rendered['painted']
            assert page.evaluate('cspViolations')==[]
            import base64
            (output/'favicon-rendered.png').write_bytes(base64.b64decode(rendered.pop('uri').split(',',1)[1]))
            (output/'favicon-csp.json').write_text(json.dumps(dict(declaration=page.locator('link[rel=icon]').get_attribute('href'),csp=csp,display_verified=True,rendered=rendered,violations=page.evaluate('cspViolations'))),encoding='utf-8')
            page.screenshot(path=str(output/'dashboard-1440.png'))
            gear.click()
            page.locator('#settings-nav button').first.wait_for()
            page.screenshot(path=str(output/'dashboard-settings-1440.png'))
            assert not errors
        finally:
            browser.close()


def test_t056_direct_discovery_icon_fixture(http_dashboard, config, db, listing):
    from playwright.sync_api import sync_playwright, expect
    queue(db, config, listing, 3)
    server, _ = http_dashboard
    output = Path('.tmp/review/t056')
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        try:
            page = browser.new_page()
            errors, calls = [], []
            finished = False
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>cspViolations.push(e.effectiveDirective));")
            def respond(route):
                incoming = route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort(); return
                target = incoming.url.removeprefix('http://127.0.0.1:8765')
                calls.append((incoming.method, target))
                # Discovery is a fixture; never launch a source scan.
                if target == '/api/refresh' and incoming.method == 'POST':
                    route.fulfill(json=dict(id='fixture-discovery')); return
                if target == '/api/refresh/fixture-discovery':
                    route.fulfill(json=dict(id='fixture-discovery', status='done' if finished else 'running',
                                           result=dict(new=3), started_at='2026-10-16T20:00:00Z', finished_at='2026-10-16T20:00:02Z')); return
                with ThreadPoolExecutor(max_workers=1) as pool:
                    status, body, headers = pool.submit(request, server, incoming.method, target, incoming.all_headers(), incoming.post_data).result()
                route.fulfill(status=status, body=body if isinstance(body, (str, bytes)) else json.dumps(body), headers=headers)
            page.route('**/*', respond)
            for theme in ('light', 'dark'):
                page.emulate_media(color_scheme=theme, reduced_motion='reduce')
                for width in (1440, 500):
                    page.set_viewport_size(dict(width=width, height=900))
                    page.goto('http://127.0.0.1:8765/#priority')
                    page.locator('#rows .item').first.wait_for()
                    button = page.get_by_role('button', name='Find new listings', exact=True)
                    expect(button).to_be_visible()
                    assert page.locator('.queue-more').count() == 0
                    assert page.get_by_text('More', exact=True).count() == 0
                    assert button.evaluate("e=>{const b=e.getBoundingClientRect();return b.width===32&&b.right<=innerWidth&&e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}")
                    assert page.locator('#discovery-label').count() == 1
                    assert page.locator('.listing-checks,#sweep').count() == 0
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth')
                    page.screenshot(path=str(output/f'dashboard-{width}-{theme}.png'))
                    finished = False
                    button.click()
                    busy = page.locator('#refresh-discovery')
                    expect(busy).to_be_disabled()
                    expect(busy).to_have_attribute('aria-busy', 'true')
                    expect(busy).to_have_attribute('aria-label', __import__('re').compile('Searching'))
                    page.screenshot(path=str(output/f'dashboard-busy-{width}-{theme}.png'))
                    finished = True
                    expect(busy).to_be_enabled(timeout=10000)
                    expect(busy).to_have_attribute('aria-busy', 'false')
                    expect(busy).to_have_attribute('aria-label', 'Find new listings')
                    expect(page.locator('#discovery-state')).to_contain_text('Last updated')
            assert sum(method == 'POST' and path == '/api/refresh' for method, path in calls) == 4
            assert page.evaluate('cspViolations') == [] and not errors
        finally:
            browser.close()



def test_t057_queue_continues_after_repeated_timeouts_and_resource_ownership(tmp_path):
    executable=shutil.which('node')
    if not executable: pytest.skip('Node unavailable')
    code=PAGE[PAGE.index('async function request('):PAGE.index('function post(')]
    code+=PAGE[PAGE.index('const loads={},listRecovery={};'):PAGE.index('async function loadManual')]
    script=r"""
const assert=require('node:assert/strict');
let revision=0,now=0,calls=0,resolveHeld;
Date.now=()=>now;
const timers=new Map();let nextTimer=0;
const setTimeout=(fn,delay)=>{const id=++nextTimer;timers.set(id,{fn,delay,due:now+delay});return id;},clearTimeout=id=>timers.delete(id);
const notice={dataset:{},hidden:true},message={textContent:''};
const document={getElementById:()=>notice};
const notify=(text,error,owner='action')=>{message.textContent=text;notice.dataset.owner=owner;notice.hidden=false;};
let fetch=(path,{signal})=>{calls++;return new Promise((resolve,reject)=>{resolveHeld=data=>resolve({ok:true,json:async()=>data});signal.addEventListener('abort',()=>reject(signal.reason));});};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
async function fire(at){now=at;const entry=[...timers.entries()].find(([id,t])=>t.due===at);assert.ok(entry,'missing timer '+at);timers.delete(entry[0]);entry[1].fn();await flush();}
"""+code+r"""
(async()=>{
 let rendered=[];
 const cold=loadList('priority','/api/priority',data=>rendered.push(data));
 const overlap=loadList('priority','/api/priority',()=>assert.fail('duplicate render'));
 assert.equal(calls,1);await fire(30000);assert.equal(calls,2);await fire(60000);
 assert.equal(await cold,false);assert.equal(await overlap,false);
 assert.equal(notice.hidden,false);assert.equal(notice.dataset.owner,'priority');
 assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,5000);
 await loadList('priority','/api/priority',()=>assert.fail('backoff bypassed'));assert.equal(calls,2);
 await fire(65000);assert.equal(calls,3);await fire(95000);assert.equal(calls,4);await fire(125000);
 assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,10000);
 await fire(135000);assert.equal(calls,5);
 const final=loadList('priority','/api/priority',()=>assert.fail('duplicate render'));
 assert.equal(calls,5);resolveHeld({rows:[7]});await final;
 assert.deepEqual(rendered,[{rows:[7]}]);assert.equal(notice.hidden,true);assert.equal(timers.size,0);
 notify('Authentication failed',true);
 request=async()=>({rows:[8]});await loadList('priority','/api/priority',data=>rendered.push(data));
 assert.equal(notice.hidden,false);assert.equal(message.textContent,'Authentication failed');
 // A persisted mutation revision invalidates a held response; the next retry is still scheduled.
 request=()=>new Promise(resolve=>resolveHeld=resolve);const stale=loadList('priority','/api/priority',()=>assert.fail('stale rendered'));
 revision++;resolveHeld({rows:[9]});assert.equal(await stale,false);assert.equal(timers.size,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    path=tmp_path/'t057-recovery.js';path.write_text(script,encoding='utf-8')
    result=subprocess.run([executable,str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


@pytest.mark.browser
def test_t057_applied_queue_navigation_screenshots(http_dashboard,config,db,listing):
    from playwright.sync_api import sync_playwright,expect
    queue(db,config,listing,3)
    server,_=http_dashboard
    output=Path('.tmp/review/t057');output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chromium',headless=True)
        try:
            page=browser.new_page(viewport=dict(width=1440,height=900))
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def respond(route):
                incoming=route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort();return
                status,body,headers=request(server,incoming.method,incoming.url.removeprefix('http://127.0.0.1:8765'),incoming.all_headers(),incoming.post_data)
                route.fulfill(status=status,body=body if isinstance(body,(str,bytes)) else json.dumps(body),headers=headers)
            page.route('**/*',respond)
            page.goto('http://127.0.0.1:8765/')
            expect(page.locator('#tab-priority')).to_have_attribute('aria-selected','true')
            expect(page.locator('#tab-run')).to_be_hidden();expect(page.locator('#worker-status')).to_be_hidden()
            expect(page.locator('#rows .item')).to_have_count(3)
            for old in ['needs-you','failed','run']:
                page.evaluate('hash=>location.hash=hash',old)
                expect(page.locator('#tab-priority' if old=='run' else '#tab-applications')).to_have_attribute('aria-selected','true')
                assert page.evaluate('location.hash')==('#priority' if old=='run' else '#applications')
            for width in [1440,500]:
                page.set_viewport_size(dict(width=width,height=900))
                for tab in ['priority','applications']:
                    page.locator('#tab-'+tab).click()
                    expect(page.locator('#tab-'+tab)).to_have_attribute('aria-selected','true')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth')
                    page.screenshot(path=str(output/f'dashboard-{tab}-{width}.png'))
            expect(page.locator('#panel-applied')).to_be_visible()
            expect(page.locator('#segment-needs-you')).to_be_hidden();expect(page.locator('#segment-failed')).to_be_hidden()
            assert errors==[]
        finally:browser.close()


def test_t065_priority_returns_bounded_stored_description(dashboard, config, db, listing, monkeypatch):
    stored = '<b>Stored only</b> ' + 'Description. ' * 600
    app_id = queue(db, config, replace(listing, description=stored), 1)[0]
    probe = Mock(side_effect=AssertionError('Queue reads must never fetch descriptions'))
    monkeypatch.setattr(descriptions, 'fetch_description', probe)
    row = next(row for row in dashboard.priority()['rows'] if row['application_id'] == app_id)
    assert row['description'] == stored[:6000]
    assert not probe.called
