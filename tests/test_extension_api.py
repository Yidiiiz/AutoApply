"""Extension checks use temporary files and in-memory HTTP only."""
import base64
from dataclasses import replace
import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
import yaml

from autoapply.dashboard import Dashboard
from test_dashboard import (dashboard, http_dashboard, queue, request, protected_queue,
                            writing_provider, offline_descriptions)


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
    ('https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123', 'https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply/applyManually?source=test'),
    ('https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply/applyManually', 'https://tenant.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply/next'),
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


@pytest.mark.parametrize('month_options,expected', [
    (['August', 'September'], 'August'), (['8', '9'], '8'),
    (['08', '09'], '08'), (['Aug', 'Sep'], 'Aug'),
])
def test_education_date_parts(http_dashboard, config, month_options, expected):
    profile = dict(config.profile)
    profile['education'] = dict(profile['education'], start_date='2024-08-20')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = [dict(key='start-month', label='Start date month', kind='combobox', options=month_options),
              dict(key='start-year', label='Start date year', kind='number'),
              dict(key='end-month', label='End date month', kind='select', options=['May', 'June']),
              dict(key='end-year', label='End date year', kind='combobox', options=['2027', '2028'])]
    for field in fields:
        field.update(section='Education', required=True)
    payload = dict(url='https://unmatched.test/job', fields=fields)
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', payload)
    assert status == 200
    assert [row['value'] for row in result['answers']] == [expected, '2024', 'May', '2028']
    assert all(row['status'] == 'RESOLVED' for row in result['answers'])
    assert all(row['source'].startswith('profile:education.') for row in result['answers'])


def test_education_date_parts_abstain_on_missing_fact_or_option(http_dashboard):
    fields = [dict(key='start', label='Start date month', section='Education', required=True),
              dict(key='end', label='End date month', section='Education', kind='combobox', options=['January'], required=True),
              dict(key='unrelated', label='Start date year', section='Employment', required=True)]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://unmatched.test/job', fields=fields))
    assert status == 200
    assert all(row['status'] == 'NEEDS_INPUT' and row['value'] is None for row in result['answers'])
    fields[0]['section'] = []
    assert ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://unmatched.test/job', fields=fields))[0] == 400


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
    assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids))[0] == 409
    for index, state in enumerate(('needs_input', 'filled')):
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1]['application_id'] == ordered[index]
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(wait=True)
        status_run = ext(http_dashboard, 'GET', '/ext/run/status')[1]
        assert status_run['poll_age_seconds'] < 2
        assert request(http_dashboard[0], 'GET', '/api/ext-run')[1]['current'] == ordered[index]
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[index], state='filled', run_id='stale'))[0] == 409
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


@pytest.mark.parametrize('question,limits', [
    ('Tell us about robotics. word limit 300', {'words': 300}),
    ('Tell us about robotics (300 words)', {'words': 300}),
    ('Tell us about robotics. max 300 words', {'words': 300}),
    ('Tell us about robotics. 1,000 characters', {'chars': 1000}),
    ('Word limit: 300; max 200 words; 500 characters', {'words': 200, 'chars': 500}),
    ('Tell us about robotics.', {}),
])
def test_write_limits_and_refine(http_dashboard, writing_provider, question, limits):
    writing_provider.result = {'answer': 'I built a robot.'}
    payload = dict(question=question, previous='I built a machine.', instruction='Add my robotics project.',
                   page=dict(title='Intern', company='Example', description='Build robots.'))
    status, result, _ = ext(http_dashboard, 'POST', '/ext/write', payload)
    assert status == 200 and result == dict(answer='I built a robot.', words=4, chars=16)
    sent = writing_provider.requests[-1]
    assert sent['task'] == 'question_answer' and sent['limits'] == limits
    assert sent['previous'] == payload['previous'] and sent['instruction'] == payload['instruction']
    assert sent['question'] == question and sent['job']['description'] == 'Build robots.'
    assert sent['profile']['name'] == 'Test Student'
    assert 'contact' not in sent['profile'] and 'work_authorization' not in sent['profile']


def test_write_warns_without_truncating(http_dashboard, writing_provider):
    writing_provider.result = {'answer': 'I built a robot.'}
    status, result, _ = ext(http_dashboard, 'POST', '/ext/write', dict(question='Max 2 words, 10 characters'))
    assert status == 200 and result['answer'] == 'I built a robot.'
    assert result['words'] == 4 and result['chars'] == 16
    assert 'words limit exceeded (4/2)' in result['warning']
    assert 'chars limit exceeded (16/10)' in result['warning']


def test_write_matched_job_fetches_description(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    app_id = queue(db, config, replace(listing, description=''), 1)[0]
    before = db.application(app_id)
    description = 'Build robots using Python. ' * 12
    def fetch(url):
        assert url == before['canonical_url']
        with http_dashboard[1].connection(writable=True) as other:
            with other.transaction():
                pass
        return description
    monkeypatch.setattr('autoapply.descriptions.fetch_description', fetch)
    writing_provider.result = {'answer': 'I study computer science.'}
    payload = dict(url=before['canonical_url'], question='What can you contribute?')
    assert ext(http_dashboard, 'POST', '/ext/write', payload)[0] == 200
    assert writing_provider.requests[-1]['job']['company'] == before['company']
    assert writing_provider.requests[-1]['job']['description'] == description.strip()
    assert db.application(app_id) == dict(before, description=description)
    assert db.rows('SELECT * FROM questions') == []


def test_chat_trimmed_history_and_job(http_dashboard, config, db, listing, writing_provider):
    app_id = queue(db, config, listing, 1)[0]
    before = db.application(app_id)
    writing_provider.result = {'reply': 'What would you like to highlight?'}
    messages = [dict(role='user' if i % 2 else 'assistant', text=str(i) + 'x' * 2200) for i in range(20)]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/chat', dict(application_id=app_id, messages=messages))
    assert status == 200 and result == writing_provider.result
    sent = writing_provider.requests[-1]
    assert sent['task'] == 'chat'
    assert sent['messages'] == [dict(role=m['role'], text=m['text'][:2000]) for m in messages[-12:]]
    assert sent['profile']['name'] == 'Test Student' and sent['job']['company'] == 'Company 0'
    assert db.application(app_id) == before


def test_cover_letter_questions_pdf_and_persistence(http_dashboard, config, db, listing, monkeypatch, writing_provider):
    app_id = queue(db, config, listing, 1)[0]
    before = db.application(app_id)
    payload = dict(application_id=app_id)
    renderer = Mock(side_effect=lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    monkeypatch.setattr('autoapply.cv.render_pdf', renderer)
    writing_provider.result = {'questions': ['When can you start?']}
    assert ext(http_dashboard, 'POST', '/ext/cover-letter', payload)[:2] == (200, writing_provider.result)
    renderer.assert_not_called()
    payload['answers'] = [dict(question='When can you start?', answer='June 2027'),
                          dict(question='Why this company?', answer='')]
    writing_provider.result = dict(paragraphs=['I study computer science.'], salutation='Dear Ms. Lee,')
    status, result, _ = ext(http_dashboard, 'POST', '/ext/cover-letter', payload)
    assert status == 200 and result['filename'] == 'Test Student - Cover Letter - Company 0.pdf'
    assert result['mime'] == 'application/pdf' and base64.b64decode(result['base64']) == b'%PDF-1.4 fixture'
    assert '<p>Dear Ms. Lee,</p>' in renderer.call_args.args[0]
    assert 'font:12pt/1.15' in renderer.call_args.args[0]
    assert ext(http_dashboard, 'POST', '/ext/cover-letter', dict(application_id=app_id))[0] == 200
    assert writing_provider.requests[-1]['answers'] == payload['answers']
    assert writing_provider.requests[-1]['skipped_questions'] == ['Why this company?']
    saved = config.private / f'documents/cv/{app_id}-company-0.answers.json'
    assert json.loads(saved.read_text(encoding='utf-8')) == payload['answers']
    assert db.application(app_id) == before


@pytest.mark.parametrize('path,output', [('/ext/write', {'answer': 'I study computer science.'}),
    ('/ext/chat', {'reply': 'I can help with this role.'}),
    ('/ext/cover-letter', {'paragraphs': ['I study computer science.']})])
def test_writing_unmatched_page(http_dashboard, config, db, monkeypatch, writing_provider, path, output):
    writing_provider.result = output
    fetcher = Mock(side_effect=AssertionError('Unmatched pages must use supplied context'))
    monkeypatch.setattr('autoapply.descriptions.fetch_description', fetcher)
    monkeypatch.setattr('autoapply.cv.render_pdf', lambda html, path: path.write_bytes(b'%PDF-1.4 fixture'))
    payload = dict(page=dict(url='https://unmatched.test/job', company='A &amp; B', title='Intern',
                            description='Build tools.'), question='Why?', messages=[dict(role='user', text='Help me.')],
                   answers=[dict(question='Start?', answer='June')])
    status, result, _ = ext(http_dashboard, 'POST', path, payload)
    assert status == 200
    sent = writing_provider.requests[-1]
    assert sent['job']['company'] == 'A & B' and sent['job']['description'] == 'Build tools.'
    if path == '/ext/cover-letter':
        assert result['filename'] == 'Test Student - Cover Letter - A & B.pdf'
        assert base64.b64decode(result['base64']).startswith(b'%PDF-')
    assert not (config.private / 'documents/cv').exists()
    assert db.rows('SELECT * FROM applications') == []
    fetcher.assert_not_called()


@pytest.mark.parametrize('path', ['/ext/write', '/ext/chat', '/ext/cover-letter'])
def test_writing_pairing_size_and_ai_disabled(http_dashboard, config, monkeypatch, path):
    payload = dict(question='Why?', messages=[dict(role='user', text='Help')])
    provider = Mock(side_effect=AssertionError('Do not invoke disabled provider'))
    monkeypatch.setattr('autoapply.codex_writer.CodexWritingProvider', provider)
    assert request(http_dashboard[0], 'POST', path, {}, json.dumps(payload))[0] == 403
    status, result, _ = ext(http_dashboard, 'POST', path, payload)
    assert status == 409 and result['error'] == 'AI writing needs an AI provider in Settings'
    config.data['ai']['enabled'] = True  # Still no configured Codex provider.
    assert ext(http_dashboard, 'POST', path, payload)[0] == 409
    status, result, _ = ext(http_dashboard, 'POST', path, dict(payload, padding='x' * 65536))
    assert status == 400 and '64 KB' in result['error']
    provider.assert_not_called()


@pytest.mark.parametrize('path,output', [('/ext/write', None), ('/ext/write', {'answer': ''}),
    ('/ext/write', {'answer': 3}), ('/ext/chat', {'reply': []}), ('/ext/cover-letter', {'paragraphs': []})])
def test_writing_invalid_output(http_dashboard, writing_provider, path, output):
    writing_provider.result = output
    payload = dict(question='Why?', messages=[dict(role='user', text='Help')])
    status, result, _ = ext(http_dashboard, 'POST', path, payload)
    assert status == 400 and 'invalid output' in result['error']


@pytest.mark.parametrize('path', ['/ext/write', '/ext/chat', '/ext/cover-letter'])
def test_writing_provider_failure(http_dashboard, writing_provider, path):
    from autoapply.ai import ProviderUnavailable
    writing_provider.result = ProviderUnavailable('Fixture unavailable')
    status, result, _ = ext(http_dashboard, 'POST', path,
                             dict(question='Why?', messages=[dict(role='user', text='Help')]))
    assert status == 409 and 'Fixture unavailable' in result['error']


@pytest.mark.parametrize('path,payload', [('/ext/write', {}), ('/ext/write', {'question': []}),
    ('/ext/write', {'question': 'Why?', 'previous': True}), ('/ext/chat', {'messages': []}),
    ('/ext/chat', {'messages': [{'role': 'system', 'text': 'Help'}]}),
    ('/ext/chat', {'messages': [{'role': [], 'text': 'Help'}]}),
    ('/ext/cover-letter', {'page': []}), ('/ext/cover-letter', {'application_id': True}),
    ('/ext/cover-letter', {'answers': [{'question': 'Q', 'answer': 3}]})])
def test_writing_invalid_requests(http_dashboard, writing_provider, path, payload):
    assert ext(http_dashboard, 'POST', path, payload)[0] == 400
    assert writing_provider.requests == []


@pytest.mark.parametrize('path', ['/ext/write', '/ext/chat', '/ext/cover-letter'])
def test_writing_protected_application(http_dashboard, db, listing, writing_provider, path):
    protected_queue(db, listing)
    before = db.application(3556)
    status, _, _ = ext(http_dashboard, 'POST', path, dict(application_id=3556, question='Why?',
                                                        messages=[dict(role='user', text='Help')]))
    assert status == 403 and db.application(3556) == before
    assert writing_provider.requests == []


@pytest.mark.parametrize('failure', [RuntimeError('Render failed'), None])
def test_cover_letter_requires_valid_pdf(http_dashboard, monkeypatch, writing_provider, failure):
    renderer = Mock(side_effect=failure) if failure else Mock(side_effect=lambda html, path: path.write_bytes(b'not PDF'))
    monkeypatch.setattr('autoapply.cv.render_pdf', renderer)
    status, result, _ = ext(http_dashboard, 'POST', '/ext/cover-letter', dict(page=dict(company='Example')))
    assert status == 409 and 'PDF rendering failed' in result['error']


def test_writing_unavailable_configuration_and_malformed_json(http_dashboard, writing_provider, monkeypatch):
    from autoapply.ai import ProviderUnavailable
    monkeypatch.setattr('autoapply.codex_writer.CodexWritingProvider', Mock(side_effect=ValueError('CLI missing')))
    status, result, _ = ext(http_dashboard, 'POST', '/ext/write', dict(question='Why?'))
    assert status == 409 and 'CLI missing' in result['error']
    monkeypatch.setattr('autoapply.codex_writer.CodexWritingProvider', lambda *args: writing_provider)
    writing_provider.result = ProviderUnavailable('Invalid JSON', 'MALFORMED_RESPONSE')
    assert ext(http_dashboard, 'POST', '/ext/write', dict(question='Why?'))[0] == 400


def test_first_work_experience_facts(http_dashboard, config):
    profile = dict(config.profile)
    profile['employment'] = [dict(company='Fixture Employer', title='Engineer', start_date='2024-08', current=True),
                             dict(company='Second Employer', title='Another title')]
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = [dict(key='title', label='Job Title'), dict(key='company', label='Company'),
              dict(key='month', label='Start date month', kind='number'),
              dict(key='year', label='Start date year', kind='number'),
              dict(key='current', label='I currently work here', kind='checkbox')]
    for field in fields:
        field.update(section='Work Experience', required=True)
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://unmatched.test/job', fields=fields))
    assert status == 200
    assert [answer['value'] for answer in result['answers']] == ['Engineer', 'Fixture Employer', '8', '2024', True]
    assert all(answer['status'] == 'RESOLVED' for answer in result['answers'])


@pytest.mark.parametrize('kind', ['checkbox', 'textarea'])
def test_zipline_authorization_answers(http_dashboard, kind):
    from test_question_keywords import ZIPLINE_LABEL, ZIPLINE_OPTIONS
    from autoapply.question_keywords import AUTHORIZATION_STATEMENT
    field = dict(key='auth', label=ZIPLINE_LABEL, kind=kind, required=True,
                 options=ZIPLINE_OPTIONS if kind == 'checkbox' else [])
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers',
                           dict(url='https://fixture.test/job', fields=[field]))
    assert status == 200
    answer = result['answers'][0]
    assert answer['status'] == 'RESOLVED'
    assert answer['value'] == (ZIPLINE_OPTIONS[0] if kind == 'checkbox' else AUTHORIZATION_STATEMENT)


def test_extension_fuzzy_saved_answer(http_dashboard):
    payload = dict(url='https://fixture.test/job', label='What is your favorite local animal?', kind='text', value='Fox')
    assert ext(http_dashboard, 'POST', '/ext/remember', payload)[0] == 200
    field = dict(key='animal', label='Please provide your favorite local animal', kind='text')
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url=payload['url'], fields=[field]))
    assert status == 200 and result['answers'][0]['value'] == 'Fox'
    assert result['answers'][0]['source'] == 'verified_fuzzy_memory'


def test_detected_applied_evidence_idempotence_and_undo(http_dashboard, config, db, listing):
    app_id = queue(db, config, listing, 1)[0]
    payload = dict(application_id=app_id, url='https://example.test/thanks', evidence='Thank you for applying')
    first = ext(http_dashboard, 'POST', '/ext/applied', payload)
    assert first[0] == 200
    assert ext(http_dashboard, 'POST', '/ext/applied', payload)[:2] == first[:2]
    assert db.setting('duplicate_submission_guard:' + str(app_id))['source'] == 'DASHBOARD_MANUAL'
    assert ext(http_dashboard, 'POST', '/ext/unapplied', dict(application_id=app_id))[0] == 200
    assert db.setting('duplicate_submission_guard:' + str(app_id)) is False
    protected_queue(db, listing)
    assert ext(http_dashboard, 'POST', '/ext/applied', dict(payload, application_id=3556))[0] == 403
    assert ext(http_dashboard, 'POST', '/ext/applied', dict(payload, evidence=''))[0] == 400


def test_normalized_profile_sections_pairing_and_saved_date(http_dashboard, config):
    profile = dict(config.profile)
    profile.update(employment=[dict(company='Past', title='Intern', start_date='2022-09', end_date='2023-02', current='False', description='- Built tools.\n- Tested tools.'),
                               dict(company='Self', title='Freelancer', start_date='', current='True')],
                   projects=[dict(name='Robot', role='Builder', start_date='2024-03', technologies=['Python'])],
                   education=dict(school='Fixture', degree='Bachelor', major='Computing', graduation_date='2028-05', currently_enrolled=False),
                   skills=['Python', 'Rust'], skill_categories={'Languages': ['Python', 'JavaScript']}, links={'github': 'https://example.test/code'})
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    assert request(http_dashboard[0], 'GET', '/ext/profile-sections')[0] == 403
    status, result, _ = ext(http_dashboard, 'GET', '/ext/profile-sections')
    assert status == 200
    assert result['experience'][0]['title'] == 'Freelancer' and result['experience'][0]['current'] is True
    assert result['experience'][1]['current'] is False
    assert result['experience'][1]['description'] == '- Built tools.\n- Tested tools.'
    assert result['skills'] == ['Python', 'JavaScript', 'Rust']
    assert result['education'][0]['start_date'] == '' and result['education'][0]['end_date'] == '2028-05'
    assert result['projects'][0]['organization'] == 'Builder'
    assert result['projects'][0]['current'] is True
    payload = dict(url='https://fixture.test/job', label='Freelancer at Self: start date', kind='text', value='2023-02')
    assert ext(http_dashboard, 'POST', '/ext/remember', payload)[0] == 200
    field = dict(key='start', label=payload['label'], kind='text', required=True, section_missing_date=True)
    answer = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://other.test/job', fields=[field]))[1]['answers'][0]
    assert answer['value'] == '2023-02' and answer['source'] == 'verified_memory'


@pytest.mark.parametrize('section', ['experience', 'projects'])
@pytest.mark.parametrize('dates, expected', [
    (dict(start_date='2026-09'), True),
    (dict(start_date='2026-08', end_date='', current=None), True),
    (dict(start_date='2026-07', current=''), True),
    (dict(start_date='2026-06', end_date='2026-09'), False),
    (dict(start_date='2026-05', current=False), False),
    (dict(start_date='2026-04', current='False'), False),
    (dict(start_date='', current=True), True),
    (dict(start_date='', current='True'), True),
    (dict(start_date=''), False),
])
def test_profile_sections_infer_ongoing_only_when_current_unset(section, dates, expected):
    from autoapply.extension_api import profile_sections
    profile = {'employment' if section == 'experience' else 'projects': [dates]}
    result = profile_sections(Mock(config=Mock(profile=profile)), {})[section][0]
    assert result['current'] is expected
    assert isinstance(result['current'], bool)


@pytest.mark.parametrize('kind,placeholder,options,expected', [
    ('text', 'MMM-YY', [], 'May-28'),
    ('text', 'MM/YYYY', [], '05/2028'),
    ('text', 'MM/DD/YYYY', [], '05/01/2028'),
    ('date', '', [], '2028-05-01'),
    ('select', '', ['May-27', 'May-28'], 'May-28'),
    ('combobox', '', ['May 2028', 'June 2028'], 'May 2028'),
])
def test_expected_graduation_formats(http_dashboard, kind, placeholder, options, expected):
    field = dict(key='graduation', label='When are you expected to graduate?', kind=kind,
                 placeholder=placeholder, options=options, required=True)
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://fixture.test/job', fields=[field]))
    assert status == 200
    assert result['answers'][0]['value'] == expected
    assert result['answers'][0]['status'] == 'RESOLVED'


def test_profile_sections_year_only_and_languages():
    from types import SimpleNamespace
    from autoapply.extension_api import profile_sections
    dashboard = SimpleNamespace(config=SimpleNamespace(profile=dict(
        employment=[dict(title='Freelancer', start_date='2019', current=True),
                    dict(title='Crew', start_date='2026-06', end_date='2026-08')],
        languages=[dict(language='English', fluent=True, proficiency='highest')])))
    result = profile_sections(dashboard, {})
    assert result['experience'][0]['start_date'] == '2019-01'
    assert result['experience'][0]['current'] is True
    assert result['experience'][1]['end_date'] == '2026-08'
    assert result['languages'] == [dict(language='English', fluent=True, proficiency='highest')]


@pytest.mark.parametrize('label,expected', [('From date month', '1'), ('From date year', '2019')])
def test_work_year_only_date_parts(label, expected):
    from autoapply.extension_api import _repeatable_answer
    from autoapply.models import Question
    question = Question('date', label, 'number', True, [])
    matched, answer = _repeatable_answer(question, dict(section='Work Experience'),
                                         dict(employment=[dict(start_date='2019', current=True)]))
    assert matched and answer.value == expected


@pytest.mark.parametrize('kind,placeholder,expected', [
    ('text', 'MM/DD/YYYY', '05/17/2027'),
    ('text', 'DD/MM/YYYY', '17/05/2027'),
    ('text', 'MM/YYYY', '05/2027'),
    ('date', '', '2027-05-17'),
])
def test_desired_start_date_format(http_dashboard, config, kind, placeholder, expected):
    profile = config.profile
    profile['application_preferences'] = dict(desired_start_date='05/17/2027')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(
        url='https://jobs.example.test/fixture', fields=[dict(key='start', label='What is your desired start date?',
            kind=kind, placeholder=placeholder, required=True)]))
    assert status == 200
    assert data['answers'][0]['value'] == expected


def test_insulet_graduation_format(http_dashboard):
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(
        url='https://jobs.example.test/fixture', fields=[dict(key='grad', label='When are you expected to graduate?*',
            kind='text', required=True)]))
    assert status == 200 and data['answers'][0]['value'] == 'May-28'


def test_short_desired_start_date_field(http_dashboard, config):
    profile = config.profile
    profile['application_preferences'] = dict(desired_start_date='05/17/2027')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(
        url='https://jobs.example.test/fixture', fields=[dict(key='start', label='What is your desired start date?',
            kind='text', placeholder='MM/YYYY', max_length=7, required=True)]))
    assert status == 200 and data['answers'][0]['value'] == '05/2027'


@pytest.mark.parametrize('path,output', [('/ext/write', {'answer': 'Fixture answer.'}),
                                       ('/ext/chat', {'reply': 'Fixture reply.'})])
@pytest.mark.parametrize('matched', [False, True])
def test_prompt_job_context_toggle_and_cap(http_dashboard, config, db, listing, writing_provider,
                                           monkeypatch, path, output, matched):
    writing_provider.result = output
    app_id = queue(db, config, replace(listing, description=''), 1)[0] if matched else None
    url = db.application(app_id)['canonical_url'] if matched else 'https://fixture.test/job'
    description = 'Useful role requirements. ' * 400
    fetcher = Mock(side_effect=AssertionError('Use scanned context, never fetch'))
    monkeypatch.setattr('autoapply.descriptions.fetch_description', fetcher)
    payload = dict(url=url, page=dict(url=url, description=description, company='Fixture', title='Intern'),
                   question='Why this role?', messages=[dict(role='user', text='Help me.')])
    if matched:
        payload['application_id'] = app_id
    assert ext(http_dashboard, 'POST', path, payload)[0] == 200
    sent = writing_provider.requests[-1]['job']
    assert sent['description'] == description[:6000].strip() and sent['canonical_url'] == url
    if matched:
        assert db.application(app_id)['description'] == description[:6000]
    payload['use_job_description'] = False
    assert ext(http_dashboard, 'POST', path, payload)[0] == 200
    sent = writing_provider.requests[-1]['job']
    assert sent['description'] == '' and sent['canonical_url'] == ''
    fetcher.assert_not_called()


@pytest.mark.parametrize('label', ['Address line 2', 'Address 2', 'Address line2', 'Apt', 'Suite', 'Unit'])
def test_t039_secondary_address(http_dashboard, label):
    fields=[dict(key='optional', label=label), dict(key='required', label=label, required=True)]
    status, result, _=ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://fixture.test/job',fields=fields))
    assert status == 200
    assert [row['status'] for row in result['answers']] == ['OPTIONAL_SKIP','NEEDS_INPUT']
    assert all(row['value'] is None for row in result['answers'])


@pytest.mark.parametrize('label,options,expected', [
    ('Phone Device Type', [], 'Mobile'), ('Phone Type', ['Home','Mobile'], 'Mobile'),
    ('Phone Device Type', ['Home','Cell'], 'Cell'), ('Phone Type', ['Mobile Phone','Other'], 'Mobile Phone')])
def test_t039_phone_type(http_dashboard, label, options, expected):
    status, result, _=ext(http_dashboard,'POST','/ext/answers',dict(url='https://fixture.test/job',fields=[dict(key='phone',label=label,kind='select',options=options)]))
    assert status == 200 and result['answers'][0]['value'] == expected


def test_t039_snapshot_paired_sanitized(http_dashboard, config):
    server, _ = http_dashboard
    payload=dict(host='fixture.test', nodes=[dict(tag='input',type='text',id='name',value='SECRET',text='SECRET',visible=True),dict(tag='label',text='Full name',visible=True)],file_contents='SECRET')
    assert request(server,'POST','/ext/snapshot',{},json.dumps(payload))[0] == 403
    status, result, _=ext(http_dashboard,'POST','/ext/snapshot',payload)
    assert status == 200 and result['ok']
    paths=list((config.private/'snapshots').glob('fixture.test-*.json'))
    assert len(paths) == 1
    saved=paths[0].read_text(encoding='utf-8')
    assert 'SECRET' not in saved and 'Full name' in saved
    assert json.loads(saved)['nodes'][0] == dict(tag='input',type='text',id='name',visible=True)
    assert ext(http_dashboard,'POST','/ext/snapshot',dict(payload,host='../escape'))[0] == 400
    assert ext(http_dashboard,'POST','/ext/snapshot',dict(payload,nodes=[{}]))[0] == 400
    assert ext(http_dashboard,'POST','/ext/snapshot',dict(payload,nodes=[],extra='x'*1048576))[0] == 400
