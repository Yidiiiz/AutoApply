"""Extension checks use temporary files and in-memory HTTP only."""
import base64
from dataclasses import replace
import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from autoapply.dashboard import Dashboard
from test_dashboard import dashboard, http_dashboard, queue, request, protected_queue


def ext(pair, method, path, payload=None, **headers):
    server, dashboard = pair
    values = {'Origin': 'chrome-extension://fixture', 'X-AutoApply-Key': dashboard.settings.extension_key()}
    values.update(headers)
    return request(server, method, path, values, json.dumps(payload) if payload is not None else None)


def web(pair, path, payload=None):
    return request(pair[0], 'POST', path, {'Origin': 'http://127.0.0.1:8765',
                   'X-Dashboard-Token': pair[1].token}, json.dumps(payload) if payload is not None else None)


def test_pairing_cors_settings(http_dashboard, config, db):
    server, dashboard = http_dashboard
    key = dashboard.settings.extension_key()
    other = Dashboard(config, db)
    try:
        assert other.settings.extension_key() == key == db.setting('extension_key')
    finally:
        other.close()
    for path in ('/ext/ping', '/ext/resume', '/ext/run/next'):
        assert request(server, 'GET', path)[0] == 403
        assert ext(http_dashboard, 'GET', path, **{'X-AutoApply-Key': 'wrong'})[0] == 403
    status, data, headers = ext(http_dashboard, 'GET', '/ext/ping')
    assert status == 200 and data == dict(ok=True, name='AutoApply', profile_name='Test Student')
    assert headers['Access-Control-Allow-Origin'] == 'chrome-extension://fixture'
    preflight = {'Origin': 'chrome-extension://fixture', 'Access-Control-Request-Method': 'POST'}
    status, _, headers = request(server, 'OPTIONS', '/ext/answers', preflight)
    assert status == 200 and headers['Access-Control-Allow-Headers'] == 'Content-Type, X-AutoApply-Key'
    assert headers['Access-Control-Allow-Methods'] == 'GET, POST'
    assert request(server, 'OPTIONS', '/ext/answers', dict(preflight, Origin='https://evil.example'))[0] == 403
    assert ext(http_dashboard, 'GET', '/ext/ping', Origin='https://evil.example')[0] == 403
    assert ext(http_dashboard, 'GET', '/ext/ping', Host='evil.example')[0] == 403
    assert request(server, 'POST', '/api/ext-run', {'Origin': 'chrome-extension://fixture', 'X-AutoApply-Key': key}, '{}')[0] == 403
    assert request(server, 'POST', '/api/extension-key/regenerate', {}, '{}')[0] == 403
    status, result, _ = web(http_dashboard, '/api/extension-key/regenerate')
    assert status == 200 and result['extension_key'] != key
    assert request(server, 'GET', '/ext/ping', {'X-AutoApply-Key': key})[0] == 403
    assert request(server, 'GET', '/api/settings')[1]['extension_key'] == result['extension_key']
    page = request(server, 'GET', '/')[1]
    assert 'Chrome extension pairing key' in page and "node('button','Copy')" in page and "node('button','Regenerate')" in page


def test_extension_requests_read_key_without_write_lock(http_dashboard, config, db, monkeypatch):
    server, dashboard = http_dashboard
    key = dashboard.settings.extension_key()
    other = Dashboard(config, db)
    connection = other.connection
    reads = []
    @contextmanager
    def read_only_connection(*, writable=False):
        assert not writable
        with connection() as value:
            value.conn.set_trace_callback(reads.append)
            yield value
    monkeypatch.setattr(other.settings, 'connection', read_only_connection)
    server.dashboard = other
    try:
        with dashboard.connection(writable=True) as writer:
            with writer.transaction():
                for method, path, payload in (
                    ('GET', '/ext/ping', None), ('GET', '/ext/resume', None),
                    ('GET', '/ext/run/next', None), ('POST', '/ext/match', dict(url='https://unknown.test/job')),
                ):
                    headers = {'Origin': 'chrome-extension://fixture', 'X-AutoApply-Key': key}
                    assert request(server, method, path, headers, json.dumps(payload) if payload else None)[0] == 200
        assert len([sql for sql in reads if 'extension_key' in sql]) == 4
        assert not any(sql.startswith('BEGIN') for sql in reads)
    finally:
        server.dashboard = dashboard
        other.close()


@pytest.mark.parametrize('error,message', [
    (sqlite3.OperationalError('database is locked'), 'Dashboard is busy, retry shortly'),
    (sqlite3.OperationalError('database is BUSY'), 'Dashboard is busy, retry shortly'),
    (sqlite3.OperationalError('no such table'), 'Dashboard request failed: OperationalError'),
    (RuntimeError('unexpected'), 'Dashboard request failed: RuntimeError'),
    (RuntimeError('locked'), 'Dashboard request failed: RuntimeError'),
])
@pytest.mark.parametrize('method,path,target', [
    ('GET', '/ext/ping', 'key'), ('POST', '/ext/match', 'key'),
    ('GET', '/ext/ping', 'dispatch'), ('POST', '/ext/match', 'dispatch'),
])
def test_extension_unexpected_errors_return_json(http_dashboard, monkeypatch, error, message, method, path, target):
    server, dashboard = http_dashboard
    key = dashboard.settings.extension_key()
    failure = Mock(side_effect=error)
    if target == 'key':
        monkeypatch.setattr(dashboard.settings, 'extension_key', failure)
    else:
        monkeypatch.setattr('autoapply.extension_api.dispatch', failure)
    headers = {'Origin': 'chrome-extension://fixture', 'X-AutoApply-Key': key}
    status, result, headers = request(server, method, path, headers, '{}')
    assert status == 503 and result == dict(error=message)
    assert headers['Content-Type'] == 'application/json; charset=utf-8'
    assert headers['Access-Control-Allow-Origin'] == 'chrome-extension://fixture'


@pytest.mark.parametrize('stored,tab', [
    ('https://example.test/jobs/1', 'HTTPS://EXAMPLE.TEST/jobs/1/?utm_campaign=test'),
    ('https://jobs.lever.co/example/abc', 'https://jobs.lever.co/example/abc/apply?source=test'),
    ('https://job-boards.greenhouse.io/example/jobs/123', 'https://example.test/careers?gh_jid=123'),
    ('https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123', 'https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply'),
])
def test_match(http_dashboard, config, db, listing, stored, tab):
    job_id, _ = db.ingest(replace(listing, url=stored), config)
    app_id = db.one('SELECT id FROM applications WHERE job_id=?', (job_id,))['id']
    status, result, _ = ext(http_dashboard, 'POST', '/ext/match', dict(url=tab))
    assert status == 200 and result['application_id'] == app_id
    assert result['company'] == listing.company and result['title'] == listing.title
    assert ext(http_dashboard, 'POST', '/ext/match', dict(url='https://unknown.test/jobs/9'))[1] == dict(application_id=None)


def test_answers_description_limits(http_dashboard, config, db, listing):
    app_id = queue(db, config, listing, 1)[0]
    before = db.application(app_id)
    fields = [dict(key='email', label='Email', kind='email', required=True),
              dict(key='first', label='First name', required=True),
              dict(key='unknown', label='Favorite local animal', required=True),
              dict(key='optional', label='Favorite local color'),
              dict(key='essay', label='Why do you want to work here?', kind='textarea', required=True),
              dict(key='resume', label='Resume upload', kind='file'),
              dict(key='cover', label='Cover letter upload', kind='file'),
              dict(key='short', label='Email', kind='email', required=True, max_length=3)]
    description = 'New detailed public description. ' * 25
    payload = dict(url=before['canonical_url'], application_id=app_id, page=dict(description=description), fields=fields)
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', payload)
    assert status == 200
    rows = {r['key']: r for r in result['answers']}
    assert rows['email']['value'] == 'student@example.test' and rows['email']['status'] == 'RESOLVED'
    assert rows['first']['value'] == 'Test'
    assert [rows[k]['status'] for k in ('unknown', 'optional', 'essay', 'resume', 'short')] == ['NEEDS_INPUT', 'OPTIONAL_SKIP', 'NEEDS_WRITING', 'FILE', 'NEEDS_INPUT']
    assert rows['cover']['value'] == 'cover_letter' and rows['resume']['value'] == 'resume'
    payload['page']['description'] = 'Short'
    ext(http_dashboard, 'POST', '/ext/answers', payload)
    after = db.application(app_id)
    assert after['description'] == description
    assert {k: v for k, v in after.items() if k != 'description'} == {k: v for k, v in before.items() if k != 'description'}
    assert db.rows('SELECT * FROM questions') == []


@pytest.mark.parametrize('matched', [False, True])
def test_remember(http_dashboard, config, db, listing, matched):
    payload = dict(url='https://unmatched.test/jobs/1', label='Favorite local animal', kind='text', options=[], value='Fox')
    if matched:
        app_id = queue(db, config, listing, 1)[0]
        payload.update(application_id=app_id, url=db.application(app_id)['canonical_url'])
    assert ext(http_dashboard, 'POST', '/ext/remember', payload)[:2] == (200, dict(ok=True))
    for required in (True, False):
        field = dict(key='animal', label=payload['label'], required=required)
        status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(payload, fields=[field]))
        assert status == 200 and result['answers'][0]['value'] == 'Fox'
        assert result['answers'][0]['source'] == 'verified_memory'
        field['max_length'] = 2
        assert ext(http_dashboard, 'POST', '/ext/answers', dict(payload, fields=[field]))[1]['answers'][0]['value'] is None
    field.update(kind='select', options=['Cat', 'Fox'], max_length=None, required=True)
    assert ext(http_dashboard, 'POST', '/ext/answers', dict(payload, fields=[field]))[1]['answers'][0]['status'] == 'NEEDS_INPUT'
    field.update(kind='text', options=[])
    assert ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://other.test/jobs/2', fields=[field]))[1]['answers'][0]['value'] is None


def test_resume(http_dashboard, config):
    status, result, _ = ext(http_dashboard, 'GET', '/ext/resume')
    assert status == 200 and result['mime'] == 'application/pdf'
    assert result['filename'] == 'resume.pdf' and base64.b64decode(result['base64']) == config.resume.read_bytes()
    config.resume.unlink()
    assert ext(http_dashboard, 'GET', '/ext/resume')[0] == 404


def test_remember_profile_override(http_dashboard):
    payload = dict(url='https://unmatched.test/job', label='Email', kind='email', value='new@example.test')
    assert ext(http_dashboard, 'POST', '/ext/remember', payload)[0] == 200
    result = ext(http_dashboard, 'POST', '/ext/answers', dict(payload, fields=[dict(key='email', label='Email', kind='email', required=True)]))[1]
    assert result['answers'][0]['value'] == 'new@example.test'


def test_run_queue(http_dashboard, config, db, listing):
    ids = queue(db, config, listing, 3)
    db.execute("UPDATE applications SET status='NEEDS_INPUT' WHERE id=?", (ids[2],))
    before = [db.application(i) for i in ids]
    ordered = [ids[2], ids[0], ids[1]]
    status, run, _ = web(http_dashboard, '/api/ext-run', dict(application_ids=ordered))
    assert status == 200 and [i['application_id'] for i in run['items']] == ordered
    assert run['current'] is None
    for index, state in enumerate(('needs_input', 'filled')):
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1]['application_id'] == ordered[index]
        assert request(http_dashboard[0], 'GET', '/api/ext-run')[1]['current'] == ordered[index]
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[index], state='submitted'))[0] == 400
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[index], state=state, detail='Test'))[0] == 200
    assert web(http_dashboard, '/api/ext-run/cancel')[0] == 200
    assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(done=True)
    run = request(http_dashboard[0], 'GET', '/api/ext-run')[1]
    assert [r['state'] for r in run['items']] == ['needs_input', 'filled', 'skipped']
    assert run['current'] is None and [db.application(i) for i in ids] == before
    assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[2], state='filled'))[0] == 409


@pytest.mark.parametrize('ids', [[], [True], [0], [1, 1], [999999], [3556]])
def test_invalid_queue(http_dashboard, ids):
    assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids))[0] in {400, 403}
    assert request(http_dashboard[0], 'GET', '/api/ext-run')[1]['items'] == []


def test_protected_and_invalid(http_dashboard, db, listing):
    protected_queue(db, listing)
    before = db.application(3556)
    payload = dict(url=before['canonical_url'], application_id=3556, fields=[], page=dict(description='Long' * 1000))
    assert ext(http_dashboard, 'POST', '/ext/answers', payload)[0] == 403
    assert ext(http_dashboard, 'POST', '/ext/remember', dict(payload, label='Favorite animal', value='Fox'))[0] == 403
    assert db.application(3556) == before
    for payload in ([], {}, dict(url='file:///tmp/a'), dict(url='https://a.test', fields=[{}]),
                    dict(url='https://a.test', application_id=True, fields=[]),
                    dict(url='https://a.test', fields=[dict(label='Animal', kind=[])])):
        assert ext(http_dashboard, 'POST', '/ext/answers', payload)[0] == 400
