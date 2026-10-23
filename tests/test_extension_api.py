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
from test_question_keywords import rtx_profile, RTX_LABELS
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
    assert status == 200 and data == dict(ok=True, name='AutoApply', profile_name='Test Student', profile_revision=config.profile_snapshot().revision)
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
    assert 'Chrome extension pairing key' in page and "node('button','Regenerate')" in page
    assert "copy.setAttribute('aria-label','Copy pairing key')" in page
    assert "navigator.clipboard.writeText(key.value)" in page
    assert "key.type='password';key.value=data.extension_key;key.readOnly=true" in page
    assert "post('extension-key/regenerate')" in page and "key.value=result.extension_key" in page
    assert "key.type='password';reveal.setAttribute('aria-label','Reveal pairing key')" in page


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


@pytest.mark.parametrize('start_date,month_options,expected', [
    ('2024-08-20', ['August', 'September'], 'August'), ('2024-08', ['8', '9'], '8'),
    ('2024-08-20', ['08', '09'], '08'), ('2024-08-20', ['Aug', 'Sep'], 'Aug'),
    ('2024', ['January', 'February'], 'January'),
])
def test_education_date_parts(http_dashboard, config, start_date, month_options, expected):
    profile = dict(config.profile)
    profile['education'] = dict(profile['education'], start_date=start_date)
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = [dict(key='start-month', label='Start date month', kind='combobox', options=month_options),
              dict(key='start-year', label='Start date year', kind='text'),
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
        claimed = ext(http_dashboard, 'GET', '/ext/run/next')[1]
        assert claimed['application_id'] == ordered[index]
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(wait=True)
        assert ext(http_dashboard, 'GET', '/ext/run/status')[1]['poll_age_seconds'] < 2
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[index], state='filled', run_id='stale'))[0] == 409
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(application_id=ordered[index], state='submitted'))[0] == 400
        progress = dict(application_id=ordered[index], run_id=claimed['run_id'], state=state, detail='Test')
        assert ext(http_dashboard, 'POST', '/ext/run/result', progress)[0] == 200
        held = request(http_dashboard[0], 'GET', '/api/ext-run')[1]
        assert held['current'] == ordered[index]
        assert held['items'][index]['state'] == 'opened'
        assert held['items'][index]['phase'] == state
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(wait=True)
        # Closing/skip is terminal, idempotent and never marks applied.
        progress['state'] = 'skipped'
        assert ext(http_dashboard, 'POST', '/ext/run/result', progress)[0] == 200
        assert ext(http_dashboard, 'POST', '/ext/run/result', progress)[0] == 200
    assert web(http_dashboard, '/api/ext-run/cancel')[0] == 200
    assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(done=True)
    run = request(http_dashboard[0], 'GET', '/api/ext-run')[1]
    assert [r['state'] for r in run['items']] == ['skipped', 'skipped', 'skipped']
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
    assert status == 200 and result == dict(writing_provider.result, kind='draft')
    sent = writing_provider.requests[-1]
    assert sent['task'] == 'chat'
    assert sent['messages'] == [dict(role=m['role'], text=m['text'][:12000]) for m in messages[-12:]]
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


@pytest.mark.parametrize('label', ['Address Line 2', 'Address 2', 'Address line2', 'Apartment, suite, etc.', 'Apt/Suite', 'Apt', 'Suite', 'Unit'])
def test_t039_secondary_address(http_dashboard, label):
    fields=[dict(key='optional', label=label), dict(key='required', label=label, required=True)]
    status, result, _=ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://fixture.test/job',fields=fields))
    assert status == 200
    assert [row['status'] for row in result['answers']] == ['OPTIONAL_SKIP', 'OPTIONAL_SKIP']
    assert all(row['value'] == '' and row['source'] == 'policy:empty_address_line_2'
               for row in result['answers'])


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


def test_t056_snapshot_diagnostic_whitelist_and_bounds(http_dashboard, config):
    payload = dict(host='fixture.test', nodes=[], diagnostic=dict(
        reason='unresolved_fields', value='PRIVATE', answers=['PRIVATE'], profile={'name': 'PRIVATE'},
        fields=[dict(label='Fixture question', kind='radio', options=['Yes', 'No'], value='PRIVATE', answer='PRIVATE', arbitrary='PRIVATE')]))
    status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', payload)
    assert status == 200
    saved = json.loads((config.private / 'snapshots' / result['filename']).read_text(encoding='utf-8'))
    assert saved['diagnostic'] == dict(reason='unresolved_fields', fields=[dict(label='Fixture question', kind='radio', options=['Yes', 'No'])])
    assert 'PRIVATE' not in json.dumps(saved)
    for fields in ([{}], [dict(label='q', kind=[])], [dict(label='q', kind='password')], [dict(label='q', kind='text', options=['x'] * 21)],
                   [dict(label='q', kind='text')] * 51, 'invalid'):
        assert ext(http_dashboard, 'POST', '/ext/snapshot', dict(payload, diagnostic=dict(reason='unresolved_fields', fields=fields)))[0] == 400
    payload['diagnostic']['fields'] = [dict(label='q' * 1000, kind='select', options=['o' * 1000])]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', payload)
    assert status == 200
    saved = json.loads((config.private / 'snapshots' / result['filename']).read_text(encoding='utf-8'))
    assert len(saved['diagnostic']['fields'][0]['label']) == 300
    assert len(saved['diagnostic']['fields'][0]['options'][0]) == 200
    # Legacy payloads and unknown future metadata retain the original saved shape.
    status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', dict(payload, diagnostic=dict(reason='arbitrary', value='PRIVATE')))
    saved = json.loads((config.private / 'snapshots' / result['filename']).read_text(encoding='utf-8'))
    assert status == 200 and set(saved) == {'host', 'nodes', 'truncated'}


@pytest.mark.parametrize('required', [False, True])
def test_t041_boilerplate_needs_input(http_dashboard, required):
    fields = [dict(key=str(i), label=label, kind='select', required=required,
                   semantic_key='education.currently_enrolled', options=['Yes', 'No'])
              for i, label in enumerate(['Select One Required', 'Required', 'Please select',
                                        '*', 'Search', 'formField-degree', 'question_123'])]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers',
        dict(url='https://fixture.test/job', fields=fields))
    assert status == 200
    assert all(row['status'] == 'NEEDS_INPUT' and row['value'] is None for row in result['answers'])


def test_t041_rtx_answer_batch(http_dashboard, config, db, listing, rtx_profile):
    app_id = queue(db, config, listing, 1)[0]
    labels = RTX_LABELS + ['How many credit hours towards your degree do you anticipate having completed by the time you would start this position?*',
                          'What is your cumulative GPA?', 'What is your Sex?*',
                          'What is your race / ethnicity?*',
                          'Yes, I have read and consent to the terms and conditions*',
                          'Are you a protected veteran? (Veteran status definitions can be found in the instructions above)*',
                          'Phone Device Type*', 'Address line 2']
    choices = [['Select One', 'Yes', 'No']] * 4 + [[], ['3.0 - 3.49', '3.5 or higher'],
        ['Male', 'Female', 'I do not wish to share'],
        ['White (United States of America)', 'I do not wish to share (United States of America)'], [],
        ['IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN', 'I AM NOT A VETERAN'],
        ['Landline', 'Mobile'], []]
    fields = [dict(key=str(i), label=label, required=i != 11,
                   kind='checkbox' if i == 8 else 'number' if i == 4 else 'select' if choices[i] else 'text',
                   options=choices[i]) for i, label in enumerate(labels)]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers',
        dict(application_id=app_id, url=db.application(app_id)['canonical_url'], fields=fields))
    assert status == 200
    assert [r['value'] for r in result['answers']] == ['No'] * 4 + ['142', '3.5 or higher',
        'I do not wish to share', 'I do not wish to share (United States of America)',
        'Yes', 'I AM NOT A VETERAN', 'Mobile', '']
    assert [r['status'] for r in result['answers']] == ['RESOLVED'] * 11 + ['OPTIONAL_SKIP']


@pytest.mark.parametrize('value,expected', [('2028', '2028-01-01'),
                                           ('2028-05', '2028-05-01'),
                                           ('2028-05-17', '2028-05-17')])
def test_partial_graduation_date(http_dashboard, config, value, expected):
    profile = config.profile
    profile['education']['graduation_date'] = value
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = [dict(key='date', label='Expected graduation date', kind='date', required=True),
              dict(key='month', label='End date month', kind='combobox', section='Education',
                   options=['January', 'May'], required=True),
              dict(key='year', label='End date year', kind='text', section='Education', required=True)]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers',
                            dict(url='https://fixture.test/job', fields=fields))
    assert status == 200
    assert [row['value'] for row in result['answers']] == [expected, 'January' if value == '2028' else 'May', '2028']
    assert all(row['status'] == 'RESOLVED' for row in result['answers'])


@pytest.mark.parametrize('section,profile_key,end_key', [
    ('Education', 'education', 'graduation_date'),
    ('Work Experience', 'employment', 'end_date'),
    ('Projects', 'projects', 'end_date'),
])
@pytest.mark.parametrize('value,expected', [('2024', '2024-01-01'),
                                           ('2024-08', '2024-08-01'),
                                           ('2024-08-20', '2024-08-20')])
@pytest.mark.parametrize('label,key', [('Start date', 'start_date'), ('End date', 'end')])
def test_repeatable_full_date(section, profile_key, end_key, value, expected, label, key):
    from autoapply.extension_api import _repeatable_answer
    from autoapply.models import Question
    entry = {end_key if key == 'end' else key: value}
    profile = {profile_key: entry if profile_key == 'education' else [entry]}
    matched, answer = _repeatable_answer(Question('date', label, 'date', True, []),
                                         dict(section=section), profile)
    assert matched and answer.value == expected


@pytest.mark.parametrize('value,expected', [('2019', '2019-01'),
                                           ('2019-06', '2019-06'),
                                           ('2019-06-17', '2019-06')])
def test_profile_sections_partial_dates(value, expected):
    from autoapply.extension_api import profile_sections
    profile = dict(employment=[dict(start_date=value, end_date=value)],
                   projects=[dict(start_date=value, end_date=value)],
                   education=dict(start_date=value, graduation_date=value))
    result = profile_sections(Mock(config=Mock(profile=profile)), {})
    for section in ('experience', 'projects', 'education'):
        assert result[section][0]['start_date'] == expected
        assert result[section][0]['end_date'] == expected


@pytest.mark.parametrize('label', ['Start date month', 'Start date year', 'End date month',
                                  'End date year', 'Start month', 'From year', 'To month',
                                  'Desired start date month', 'Availability dates month'])
@pytest.mark.parametrize('section', ['', 'Phone'])
def test_date_part_without_resume_section_needs_input(http_dashboard, config, label, section):
    profile = config.profile
    profile['application_preferences'] = dict(desired_start_date='05/17/2027', availability_dates='05/17/2027 - 08/20/2027')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(
        url='https://jobs.example.test/fixture', fields=[dict(key='part', label=label, kind='text', section=section)]))
    assert status == 200
    assert data['answers'][0]['status'] == 'NEEDS_INPUT'
    assert data['answers'][0]['value'] is None
    assert data['answers'][0]['source'] == ''


def test_self_identification_profile_payload():
    from unittest.mock import Mock
    from autoapply.extension_api import profile_sections
    profile = {'identity': {'full_name': 'Fixture Student'}, 'application_preferences': {'disability': 'No'}}
    result = profile_sections(Mock(config=Mock(profile=profile)), {})
    assert result['identity'] == {'full_name': 'Fixture Student'}
    assert result['application_preferences'] == {'disability': 'No'}


def test_skill_choice_persists_and_validates_alias(http_dashboard, writing_provider, db):
    writing_provider.result = {'choice': 'Node.js'}
    status, value, _ = ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill': 'Node', 'options': ['Node.js', 'JavaScript']})
    assert status == 200 and value == {'choice': 'Node.js'}
    assert db.setting('skill_alias:node') == 'Node.js'
    assert ext(http_dashboard, 'GET', '/ext/skills/aliases')[1]['aliases']['node'] == 'Node.js'
    count = len(writing_provider.requests)
    assert ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill':'Node', 'options':['Node.js']})[1]['choice'] == 'Node.js'
    assert len(writing_provider.requests) == count
    writing_provider.result = {'choice': 'Invented'}
    assert ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill':'Unknown', 'options':['Python']})[1]['choice'] is None
    writing_provider.result = {'choice': 'NONE'}
    assert ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill':'Unknown', 'options':['Rust']})[1]['choice'] is None
    assert ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill':'C', 'options':['C++','C']})[1]['choice'] == 'C'


def test_skill_plan_parsing_and_cache(http_dashboard, config, writing_provider, monkeypatch):
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(dict(config.profile, skills=['Python', 'JavaScript', 'Rust', 'C', 'C++', 'Go'])), encoding='utf-8')
    monkeypatch.setattr(http_dashboard[1], 'run_writing', Mock(side_effect=AssertionError('No AI for planning')))
    payload = {'page': {'title':'Engineer','company':'Fixture','description':'Python JavaScript Kubernetes Rust C++; ongoing work.'}}
    status, value, _ = ext(http_dashboard, 'POST', '/ext/skills/plan', payload)
    assert status == 200 and value == {'matched':['Python','JavaScript','Rust','C++'], 'missing':[], 'other':['C','Go']}
    assert not writing_provider.requests
    assert ext(http_dashboard, 'POST', '/ext/skills/plan', payload)[1] == value
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(dict(config.profile, skills=['Rust'])), encoding='utf-8')
    assert ext(http_dashboard, 'POST', '/ext/skills/plan', payload)[1]['matched'] == ['Rust']


def test_skill_plan_disabled_fallback(http_dashboard, config, monkeypatch):
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(dict(config.profile, skills=['Python','C++','Rust','Go'])), encoding='utf-8')
    config.data['ai']['enabled'] = False
    assert ext(http_dashboard, 'POST', '/ext/skills/plan', {'page':{'description':'Use Python and C++; ongoing work.'}})[1] == {
        'matched':['Python','C++'], 'missing':[], 'other':['Rust','Go']}
    assert ext(http_dashboard, 'POST', '/ext/skills/choose', {'skill':'Unknown','options':['Python']})[1] == {'choice':None}


def test_t047_trace_paired_bounded_and_retained(http_dashboard, config):
    payload = dict(host='fixture.example', kind='skills', entries=[
        dict(timestamp='2026-10-14T00:00:00Z', step='skills', action='scrolled', scrollTop=300),
        dict(step='skills', action='verified', verified=True, pill='Java', password='excluded')])
    assert request(http_dashboard[0], 'POST', '/ext/trace', {}, json.dumps(payload))[0] == 403
    for _ in range(32):
        status, saved, _ = ext(http_dashboard, 'POST', '/ext/trace', payload)
        assert status == 200
    files = list((config.private / 'traces').glob('*.json'))
    assert len(files) == 30
    data = json.loads((config.private / 'traces' / saved['filename']).read_text(encoding='utf-8'))
    assert data['entries'][0]['action'] == 'scrolled'
    assert 'password' not in data['entries'][1]
    for change in [dict(host='../escape'), dict(kind='other'), dict(entries=[{}] * 2001),
                   dict(entries=[dict(value='x' * (1024 * 1024))])]:
        assert ext(http_dashboard, 'POST', '/ext/trace', dict(payload, **change))[0] == 400


def test_t049_run_skip_cancel_reject_inflight_result(http_dashboard, config, db, listing):
    ids = queue(db, config, listing, 3)
    assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids))[0] == 200
    first = ext(http_dashboard, 'GET', '/ext/run/next')[1]
    assert ext(http_dashboard, 'POST', '/ext/run/skip', dict(application_id=first['application_id'],run_id='old'))[0] == 409
    assert ext(http_dashboard, 'POST', '/ext/run/skip', first)[0] == 200
    assert ext(http_dashboard, 'POST', '/ext/run/result', dict(first,state='filled'))[0] == 409
    second = ext(http_dashboard, 'GET', '/ext/run/next')[1]
    assert second['application_id'] == ids[1]
    assert ext(http_dashboard, 'POST', '/ext/run/cancel', dict(run_id='old'))[0] == 409
    assert ext(http_dashboard, 'POST', '/ext/run/cancel', dict(run_id=second['run_id']))[0] == 200
    assert ext(http_dashboard, 'POST', '/ext/run/result', dict(second,state='filled'))[0] == 409
    assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(done=True)
    assert [i['state'] for i in ext(http_dashboard, 'GET', '/ext/run/status')[1]['items']] == ['skipped'] * 3
    assert all(db.application(i)['status'] == 'QUEUED' for i in ids)


@pytest.mark.affirmative
@pytest.mark.parametrize('case', list(range(16)))
def test_t053_preserved_answer_coverage(config, db, http_dashboard, monkeypatch, case, request):
    import test_t053_answers as source
    profile=source.expedia_profile.__wrapped__(config)
    if case==15: source.test_t054_current_profile_wins_over_stale_no_memory(config,db,profile,request.getfixturevalue('listing'))
    elif case>=11:
        company,supplied,expected=[('Expedia Group',True,'Yes'),('Another Employer',True,'Yes'),('Expedia Group',False,'No'),('Another Employer',False,'No')][case-11]
        source.test_t054_explicit_all_employer_restrictions(config,db,profile,company,supplied,expected)
    elif case==0: source.test_snapshot_labels_resolve_synthetic_facts(config,db,profile)
    elif case==1: source.test_ranked_available_distinct_locations(config,db,profile)
    elif case==2: source.test_restrictions_never_inferred_and_country_not_indiscriminate(config,db,profile)
    elif case==3: source.test_extension_checkbox_group_and_optional_decline(http_dashboard,profile,monkeypatch)
    elif case==4: source.test_ordered_preference_remember_pipeline(http_dashboard,config,profile)
    elif case==10: source.test_unknown_choice_provider_unavailable_stays_pending(http_dashboard,profile,monkeypatch)
    else:
        proposal,expected=[
            (dict(choices=['Python'],fact_ids=['skills'],needs_input=False),['Python']),
            (dict(choices=['Spanish'],fact_ids=['skills'],needs_input=False),None),
            (dict(choices=['Python','None of the Above'],fact_ids=['skills'],needs_input=False),None),
            (dict(choices=['Python'],fact_ids=['made_up'],needs_input=False),None),
            (dict(choices=[],fact_ids=[],needs_input=True),None),
        ][case-5]
        source.test_unknown_choices_use_grounded_writer_and_validate(http_dashboard,profile,monkeypatch,proposal,expected)


T057_IMMIGRATION = ('U.S. Export Control laws require that access to certain technical information be limited to U.S. Persons '
    '(U.S. Citizens and Lawful Permanent Residents/Green Card Holders) and individuals granted appropriate authorization. '
    'Please indicate your current Immigration and Work Authorization status. Verification will be required upon hire.')


@pytest.mark.parametrize('citizen', [True, False, None])
@pytest.mark.parametrize('option', ['USCitizen', 'US citizen', 'U.S. Citizen'])
def test_t057_current_citizenship_exact_offered_status(http_dashboard, config, citizen, option):
    profile = config.profile
    profile['citizenship'] = {} if citizen is None else dict(us_citizen=citizen)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    # USCitizen spelling is also an exact citizenship choice, never a Yes fallback.
    field = dict(key='citizen', label=T057_IMMIGRATION, kind='select', required=True,
                 options=[option, 'Lawful Permanent Resident/Green Card Holder', 'Yes', 'Other'])
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://example.test/apply', fields=[field]))
    assert status == 200
    answer = result['answers'][0]
    if citizen is True:
        assert answer['value'] == option and answer['source'] == 'profile:citizenship.us_citizen'
    else:
        assert answer['status'] == 'NEEDS_INPUT'


@pytest.mark.parametrize('label', ['Would you be eligible for an export license as a U.S. Citizen?',
                                   'If you were a U.S. Citizen, indicate your current Immigration and Work Authorization status.'])
def test_t057_citizenship_does_not_imply_legal_claims(http_dashboard, label):
    _, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://example.test/apply', fields=[
        dict(key='legal', label=label, kind='radio', required=True, options=['U.S. Citizen', 'Yes', 'No'])]))
    assert result['answers'][0]['status'] == 'NEEDS_INPUT'


def test_t057_consent_referral_and_distinct_addresses(http_dashboard, config):
    profile = config.profile
    profile['contact'].update(address='12 Example Road', city='Example City', state='Maryland', postal_code='20999',
                              permanent_address='34 Original Road', permanent_city='Home City')
    profile['application_preferences'] = dict(ordinary_policy_acknowledgement=True, referred_by_employee=False)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = [dict(key=label, label=label, kind='text') for label in ['Address','City','State/Province','Postal code','Permanent Address']]
    fields += [dict(key='referral',label='Were you referred to Healthpeak by a current employee?',kind='checkbox',options=['Yes','No'],required=True,multiple=False),
               dict(key='consent', label='(Required) Allow us to process your personal information. *',kind='checkbox',required=True,
                    options=['(Required) Allow us to process your personal information. *']),
               dict(key='marketing',label='Optional consent to marketing emails',kind='checkbox',options=['Yes'])]
    _, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://example.test/apply',fields=fields))
    answers = {v['key']: v for v in result['answers']}
    assert [answers[k]['value'] for k in ['Address','City','State/Province','Postal code']] == ['12 Example Road','Example City','Maryland','20999']
    assert '34 Original Road' in answers['Permanent Address']['value'] and 'Home City' in answers['Permanent Address']['value']
    assert answers['referral']['value'] == 'No'
    assert answers['consent']['status'] == 'RESOLVED' and answers['marketing']['status'] == 'OPTIONAL_SKIP'


def test_t057_chat_full_draft_and_authorization(http_dashboard, writing_provider):
    draft = 'A full previous draft. ' * 180
    context = dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='field-identity',max_length=None)],current_key='letter')
    messages = [dict(role='assistant',text=draft,kind='draft'),dict(role='user',text='replace current field with that')]
    status, value, _ = ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',messages=messages,field_context=context))
    assert status == 200 and value['actions'] == [dict(type='replace_text',key='letter',text=draft)]
    assert not writing_provider.requests
    context['current_key'] = None
    _, value, _ = ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',messages=messages,field_context=context))
    assert 'actions' not in value and 'Select' in value['reply']
    context['fields'][0]['kind'] = 'file'
    assert ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',messages=messages,field_context=context))[0] == 400
    writing_provider.result = dict(reply='Draft only',actions=[dict(type='submit',key='letter',text='bad')])
    status, value, _ = ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',messages=[dict(role='user',text='How do I replace the current field?')]))
    assert status == 400 and 'actions' not in value


@pytest.mark.parametrize('referred', [None, False])
def test_t057_missing_referral_and_conditional_name(http_dashboard,config,referred):
    profile=config.profile
    profile['application_preferences']={} if referred is None else dict(referred_by_employee=referred)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    _,result,_=ext(http_dashboard,'POST','/ext/answers',dict(url='https://example.test/apply',fields=[
        dict(key='ref',label='Were you referred to Healthpeak by a current employee?',kind='checkbox',options=['Yes','No'],multiple=False,required=True),
        dict(key='name',label='If Yes, please provide the name of the Healthpeak team member who referred you.',kind='text')]))
    assert result['answers'][0]['status']==('NEEDS_INPUT' if referred is None else 'RESOLVED')
    if referred is False:
        assert result['answers'][0]['value']=='No' and result['answers'][1]['status']=='OPTIONAL_SKIP'


def test_t057_trace_radio_questions_use_explicit_facts(http_dashboard,config):
    profile=config.profile;profile['locations']=dict(willing_to_relocate=True)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    fields=[dict(key=str(i),label=label,kind='radio',required=True,options=['Yes','No']) for i,label in enumerate([
        'Are you within a commutable distance or willing to relocate?',
        'Are you legally authorized to work in the United States as per the below ITAR requirements?'])]
    _,result,_=ext(http_dashboard,'POST','/ext/answers',dict(url='https://example.test/apply',fields=fields))
    assert [v['value'] for v in result['answers']]==['Yes','Yes']
    assert result['answers'][1]['source']=='profile:work_authorization.us_authorized'


@pytest.mark.parametrize('label', ['Were you referred?', 'Were you referred to Example by a current employee?',
    'Have you been referred by an employee?', 'Did a current employee refer you?', 'Employee referral (Yes/No)'])
@pytest.mark.parametrize('referred', [True, False, None])
def test_t057_all_employer_referral_variants(http_dashboard, config, label, referred):
    profile=config.profile
    profile['application_preferences']={} if referred is None else dict(referred_by_employee=referred)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    _,value,_=ext(http_dashboard,'POST','/ext/answers',dict(url='https://example.test/apply',fields=[
        dict(key='ref',label=label,kind='radio',options=['Yes','No'],required=True)]))
    answer=value['answers'][0]
    assert answer['status']==('NEEDS_INPUT' if referred is None else 'RESOLVED')
    if referred is not None:
        assert answer['value']==('Yes' if referred else 'No')
        assert answer['source']=='profile:application_preferences.referred_by_employee'


def test_t057_referral_not_generic_source(http_dashboard, config):
    profile=config.profile;profile['application_preferences']=dict(referred_by_employee=False)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    _,value,_=ext(http_dashboard,'POST','/ext/answers',dict(url='https://example.test/apply',fields=[
        dict(key='source',label='How did you hear about us?',kind='select',required=True,options=['Employee referral','Website'])]))
    assert value['answers'][0]['status']=='NEEDS_INPUT'


def test_t057_selected_answer_provider_allowlist(http_dashboard, writing_provider):
    writing_provider.result=dict(reply='A revised answer')
    field=dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=None,value='PRIVATE')
    context=dict(fields=[field],current_key='letter',current_answer=dict(key='letter',text='Existing answer',fingerprint='local-only',extra='PRIVATE'),extra='PRIVATE')
    status,_,_=ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',field_context=context,messages=[dict(role='user',text='Refine this answer')]))
    assert status==200
    sent=writing_provider.requests[-1]['field_context']
    assert sent['current_answer']==dict(key='letter',text='Existing answer')
    assert 'PRIVATE' not in json.dumps(sent) and 'fingerprint' not in json.dumps(sent)
    for key,text in [('other','text'),('letter','x'*12001),(None,'text')]:
        context['current_answer']=dict(key=key,text=text)
        assert ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',field_context=context,messages=[dict(role='user',text='Refine')]))[0]==400


@pytest.mark.parametrize('citizen,expected', [(True,'Yes'),(False,'No')])
def test_t057_boolean_citizenship_regression(http_dashboard, config, citizen, expected):
    profile=config.profile;profile['citizenship']=dict(us_citizen=citizen)
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    _,value,_=ext(http_dashboard,'POST','/ext/answers',dict(url='https://example.test/apply',fields=[
        dict(key='citizen',label='Are you a U.S. citizen?',kind='radio',options=['Yes','No'],required=True)]))
    assert value['answers'][0]['value']==expected


def test_t057_chat_receipts_and_bounded_draft_reference(http_dashboard, writing_provider):
    draft = 'A grounded full draft. ' * 160
    context = dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=None)],current_key='letter')
    receipts = ['Replaced Cover letter. Please review the field.', 'Select the text field.', 'Provider unavailable.']
    messages = [dict(role='assistant',text=draft,kind='draft')]
    for receipt in receipts:
        messages.extend([dict(role='user',text='replace current field with that'),dict(role='assistant',text=receipt,kind='receipt')])
    messages.append(dict(role='user',text='replace current field with that'))
    payload = dict(url='https://example.test/apply',messages=messages,field_context=context)
    status, value, _ = ext(http_dashboard,'POST','/ext/chat',payload)
    assert status == 200 and value['draft'] == value['actions'][0]['text'] == draft
    # Draft references survive eviction from the bounded message window.
    payload.update(messages=messages[-2:],last_draft=draft)
    assert ext(http_dashboard,'POST','/ext/chat',payload)[1]['actions'][0]['text'] == draft
    for text in receipts + ['No']:
        payload.pop('last_draft',None)
        payload['messages']=[dict(role='assistant',text=text),messages[-1]]
        assert 'actions' not in ext(http_dashboard,'POST','/ext/chat',payload)[1]
    payload['messages']=[dict(role='assistant',text='No',kind='draft'),messages[-1]]
    assert ext(http_dashboard,'POST','/ext/chat',payload)[1]['actions'][0]['text']=='No'
    assert not writing_provider.requests


@pytest.mark.parametrize('instruction', ['replace current field with a shorter version', 'update Cover letter to be more concise'])
def test_t057_chat_rewrite_uses_selected_answer_once(http_dashboard, writing_provider, instruction):
    writing_provider.result=dict(answer='I build useful tools.')
    context=dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=100)],
                 current_key='letter',current_answer=dict(key='letter',text='Existing selected answer.'))
    payload=dict(url='https://example.test/apply',use_job_description=False,field_context=context,
                 messages=[dict(role='assistant',text='Unrelated older draft',kind='draft'),dict(role='user',text=instruction)])
    status,value,_=ext(http_dashboard,'POST','/ext/chat',payload)
    assert status==200 and value['replacement_mode']=='rewrite'
    assert value['draft']==value['actions'][0]['text']=='I build useful tools.'
    assert len(writing_provider.requests)==1
    sent=writing_provider.requests[0]
    assert sent['task']=='question_answer' and sent['previous']=='Existing selected answer.'
    assert sent['question']=='Cover letter' and instruction in sent['instruction'] and '100 characters' in sent['instruction']
    assert sent['profile']['name']=='Test Student' and 'evidence_packet' in sent


@pytest.mark.parametrize('output', [dict(answer=''),dict(answer='x'*101),dict(answer='draft',needs_input=True),
    dict(answer='draft',actions=[dict(type='submit')]),dict(answer='```bad```')])
def test_t057_chat_rewrite_invalid_never_falls_back(http_dashboard,writing_provider,output):
    writing_provider.result=output
    context=dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=100)],
                 current_key='letter',current_answer=dict(key='letter',text='Existing selected answer.'))
    status,value,_=ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',field_context=context,
        messages=[dict(role='assistant',text='Old draft',kind='draft'),dict(role='user',text='replace current field with a shorter version')]))
    assert status==400 and 'actions' not in value and len(writing_provider.requests)==1


def test_t057_chat_rewrite_missing_source_unavailable_and_ambiguous(http_dashboard,writing_provider):
    from autoapply.ai import ProviderUnavailable
    context=dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=None)],current_key='letter')
    payload=dict(url='https://example.test/apply',field_context=context,messages=[dict(role='user',text='replace current field with a shorter version')])
    status,value,_=ext(http_dashboard,'POST','/ext/chat',payload)
    assert status==200 and 'actions' not in value and not writing_provider.requests
    context['current_answer']=dict(key='letter',text='Existing answer')
    writing_provider.result=ProviderUnavailable('Fixture unavailable; field unchanged')
    status,value,_=ext(http_dashboard,'POST','/ext/chat',payload)
    assert status==409 and 'actions' not in value and len(writing_provider.requests)==1
    payload['messages']=[dict(role='assistant',text='Old draft',kind='draft'),dict(role='user',text='replace current field with something')]
    status,value,_=ext(http_dashboard,'POST','/ext/chat',payload)
    assert status==200 and 'actions' not in value and 'What text' in value['reply']
    writing_provider.result=dict(reply='A shorter draft')
    payload['messages'][-1]['text']='make it shorter'
    assert 'actions' not in ext(http_dashboard,'POST','/ext/chat',payload)[1]
    assert writing_provider.requests[-1]['task']=='chat'


@pytest.mark.parametrize('quote', ['"', "'"])
def test_t057_chat_quoted_literal(http_dashboard,writing_provider,quote):
    context=dict(fields=[dict(key='letter',label='Cover letter',kind='textarea',identity='id',max_length=None)],current_key='letter')
    status,value,_=ext(http_dashboard,'POST','/ext/chat',dict(url='https://example.test/apply',field_context=context,
        messages=[dict(role='user',text='replace current field with '+quote+'No'+quote)]))
    assert status==200 and value['replacement_mode']=='literal' and value['actions'][0]['text']=='No'
    assert not writing_provider.requests


@pytest.mark.parametrize('kind', ['select', 'combobox'])
@pytest.mark.parametrize('source', ['demographics', 'application_preferences'])
def test_t059_captured_pinpoint_demographic_preferences(http_dashboard, config, writing_provider, source, kind):
    from pathlib import Path
    from bs4 import BeautifulSoup
    profile = config.profile
    profile['application_preferences'] = dict(gender='Decline to self-identify', race='Decline to self-identify',
                                               veteran='not a protected veteran')
    profile[source] = {**profile.get(source, {}), 'disability': 'Yes'}
    if source == 'demographics':
        profile[source].update(gender='Decline to self-identify', race='Decline to self-identify',
                               veteran_status='not a protected veteran')
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    before = (config.private/'profile.yaml').read_bytes()
    capture = BeautifulSoup((Path(__file__).parent/'fixtures/extension/t059-pinpoint-demographics.html').read_text(encoding='utf-8'), 'html.parser')
    fields = []
    for section in capture.select('section'):
        fields.append(dict(key=str(len(fields)), label=section.label.get_text(strip=True), kind=kind,
                           required=False, options_partial=False,
                           options=[o.get_text(strip=True) for o in section.select('select option')[1:]]))
    status, result, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(url='https://fixture.test/apply', fields=fields))
    assert status == 200
    answers = result['answers']
    # The MV3 fixture consumes these same three exact offered choices and leaves ethnicity pending.
    assert [a['status'] for a in answers] == ['RESOLVED', 'OPTIONAL_SKIP', 'RESOLVED', 'RESOLVED']
    assert [a['value'] for a in answers] == ['Choose not to self-identify', None,
        'Yes, I have a disability, or have had one in the past', 'I am not a protected veteran']
    assert answers[2]['source'] == 'profile:demographics.disability'
    assert not writing_provider.requests
    assert (config.private/'profile.yaml').read_bytes() == before


@pytest.mark.parametrize('saved,legacy,label,options,expected', [
    (True, None, 'Disability status', ['Yes, I have a disability, or have had one in the past'],
     'Yes, I have a disability, or have had one in the past'),
    ('Yes', 'No', 'Disability', ['Yes, I have a disability, or have had one in the past'], None),
    (None, None, 'Disability', ['Yes, I have a disability, or have had one in the past'], None),
    ('No', None, 'Disability', ['No, I do not have a disability and have never had one'], None),
    ('Decline', None, 'Disability', ['Yes, I have a disability, or have had one in the past', 'Prefer not to say'], 'Prefer not to say'),
    ('Yes', None, 'Disability', ['Yes, I have a disability, or have had one in the past'] * 2, None),
    ('Yes', None, 'Disability', ['Yes, I have a disability, or have had one in the past',
                               'YES I have a disability or have had one in the past'], None),
    ('Yes', None, 'Disability', ['Yes, I have a disability, or have had one in the past',
                               'Yes, I have a disability'], None),
    ('Yes', None, 'Disability', ['Yes, I require an accommodation'], None),
    ('Yes', None, 'Do you certify medical fitness?', ['Yes, I have a disability, or have had one in the past'], None),
    ('Yes', None, 'Disability', ['Yes', 'No'], 'Yes'),
    ('No', None, 'Disability', ['Yes', 'No'], 'No'),
])
def test_t059_disability_mapping_guards(config, db, saved, legacy, label, options, expected):
    from autoapply.answers import AnswerResolver, fit_options
    from autoapply.models import Question
    profile = config.profile
    profile['demographics'] = {'disability': saved}
    profile['application_preferences'] = {'disability': legacy}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    before = (config.private/'profile.yaml').read_bytes()
    resolver = AnswerResolver(config, db)
    for kind in ['select', 'combobox']:
        result = resolver.resolve_result(Question('disability', label, kind, False, options), dict(id=0, company='Fixture'))
        assert (result.answer.value if result.answer else None) == expected
        if expected is not None:
            assert result.answer.source == 'profile:demographics.disability'
            assert result.answer.evidence == ['demographics.disability']
    assert fit_options('Yes', ['Yes, I have a disability, or have had one in the past']) is None
    assert (config.private/'profile.yaml').read_bytes() == before


@pytest.mark.parametrize('policy', ['REQUIRE_USER', 'DO_NOT_ANSWER'])
def test_t059_disability_mapping_respects_policy(config, db, policy):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    profile = config.profile
    profile['demographics'] = {'disability': 'Yes'}
    profile['autofill_policies'] = {'demographics.disability': policy}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    q = Question('disability', 'Disability', 'combobox', False,
                 ['Yes, I have a disability, or have had one in the past'])
    assert AnswerResolver(config, db).resolve(q, dict(id=0, company='Fixture')) is None


def analysis_payload(**changes):
    control = dict(tag='input', role='combobox', id='proxy', **{'aria-readonly': 'true', 'aria-controls': 'choices'},
                   readonly=True, opacity=0, transformed=True, rect=[-100, 30, .01, .2], value='PRIVATE')
    field = dict(label='Full name', kind='text', required=True, detected=True, answered=False, options=[],
                 control=control, ancestors=[dict(tag='div', **{'class': 'react-select__control'}, visible=True, rect=[0, 0, 200, 40])],
                 reasons=['readonly_proxy', 'transparent_proxy'], menu=dict(observed=False, count=0, roles=[]),
                 current_answer='PRIVATE', frame_id=0, page_identity='1:2:3')
    payload = dict(host='fixture.test', context_url='https://fixture.test/application', nodes=[],
                   diagnostic=dict(reason='page_analysis', fields=[field], busy=False, responsive=True),
                   profile={'name': 'PRIVATE'}, actions=[dict(type='submit')])
    payload.update(changes)
    return payload


def test_t060_analysis_saved_readonly_canonical_and_redacted(http_dashboard, config, monkeypatch):
    from autoapply import codex_writer
    monkeypatch.setattr(codex_writer.CodexWritingProvider, 'generate_response', lambda *a, **k: pytest.fail('Analysis must not call a provider'))
    payload = analysis_payload()
    payload['diagnostic']['fields'] += [dict(payload['diagnostic']['fields'][0], label='Unmapped synthetic widget', detected=False,
        reasons=['readonly_proxy', 'transparent_proxy', 'omitted_by_scanner', 'disabled']),
        dict(payload['diagnostic']['fields'][0], label='Email', answered=True)]
    status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', payload)
    assert status == 200 and result['ok'], result
    assert result['summary'] == '2 unfilled controls \u00b7 1 omitted by scanner'
    assert result['findings'][0]['status'] == 'ready_for_saved_answer'
    assert result['findings'][1]['status'] == 'unknown_concept'
    assert any('visible parent control' in note for note in result['findings'][0]['notes'])
    assert any('Scanner omitted' in note for note in result['findings'][1]['notes'])
    assert 'filename' not in result and 'actions' not in result
    saved = json.loads(next((config.private/'snapshots').glob('fixture.test-*.json')).read_text(encoding='utf-8'))
    assert saved['id'] == result['analysis_id'] and saved['created_at'] and saved['source'] == 'manual_analysis'
    assert saved['diagnostic']['provider'] == 'not_used'
    row = saved['diagnostic']['fields'][0]
    assert row['control']['opacity'] == 0 and row['control']['transformed']
    assert row['control']['aria-controls'] == 'choices' and row['ancestors'][0]['class'] == 'react-select__control'
    assert row['frame_id'] == 0 and row['page_identity'] == '1:2:3'
    assert row['active'] and saved['diagnostic']['page_url'] == 'https://fixture.test/application'
    encoded = json.dumps(saved) + json.dumps(result)
    assert all(secret not in encoded for secret in ('PRIVATE', 'Test Student', 'student@example.test', 'current_answer', 'actions'))


@pytest.mark.parametrize('change', [
    {'control': {'tag': 'input', 'type': 'password'}},
    {'control': {'tag': 'input', 'rect': [float('nan'), 0, 0, 0]}},
    {'control': {'tag': 'input', 'opacity': 5}}, {'control': {'tag': 'input', 'readonly': 'yes'}},
    {'ancestors': [{'tag': 'div'}]*5}, {'options': ['choice']*21}, {'detected': 'true'},
    {'reasons': ['execute_script']}, {'menu': {'roles': ['<script>']}}, {'frame_id': -1},
    {'page_identity': 'PRIVATE'}, {'kind': 'password'},
])
def test_t060_analysis_independent_validation(http_dashboard, change):
    payload = analysis_payload()
    payload['diagnostic']['fields'][0].update(change)
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[0] == 400


def test_t060_analysis_empty_allfilled_bounds_and_provider_independence(http_dashboard, config, monkeypatch):
    from autoapply import extension_api
    payload = analysis_payload()
    payload['diagnostic']['fields'][0]['answered'] = True
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]['summary'] == 'All inspected controls are filled.'
    payload['diagnostic']['fields'] = []
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]['summary'] == 'No form controls found.'
    payload['diagnostic']['fields'] = analysis_payload()['diagnostic']['fields'] * 51
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[0] == 400
    payload = analysis_payload(context_url='https://different.test/')
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[0] == 400
    payload = analysis_payload()
    monkeypatch.setattr(extension_api.answers.AnswerResolver, 'resolve_result', lambda *a: (_ for _ in ()).throw(ValueError('unavailable')))
    result = ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]
    assert result['ok'] and result['findings'][0]['status'] == 'answer_check_unavailable'
    assert 'structural findings are still saved' in result['findings'][0]['notes'][-1]


def test_t060_analysis_inspects_without_profile_and_option_mismatch(http_dashboard, config):
    payload = analysis_payload()
    payload['diagnostic']['fields'][0].update(kind='select', options=['Unrelated choice'])
    result = ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]
    assert result['findings'][0]['status'] == 'no_offered_match'
    # This is a temporary synthetic test profile, never the repository private data.
    (config.private/'profile.yaml').unlink()
    result = ext(http_dashboard, 'POST', '/ext/snapshot', analysis_payload())[1]
    assert result['ok'] and result['findings']
    assert result['findings'][0]['status'] != 'ready_for_saved_answer'
    for path in (config.private/'snapshots').glob('*.json'):
        assert path.stat().st_size < 1000000


def test_t060_inactive_context_is_not_an_answer_request(http_dashboard, config):
    from copy import deepcopy
    payload = analysis_payload()
    prototype = payload['diagnostic']['fields'][0]
    hidden = deepcopy(prototype)
    hidden.update(label='Hidden mobile duplicate', active=True, reasons=['omitted_by_scanner'])
    hidden['control'].update(display='none', rect=[0, 0, 0, 0], visible=True)
    closed = deepcopy(prototype)
    closed.update(label='Cookie panel', active=True, reasons=['hidden_parent', 'disabled'])
    geometry = deepcopy(prototype)
    geometry.update(label='Zero geometry parent', active=True, reasons=[])
    geometry['ancestors'][0]['rect'] = [0, 0, 0, 0]
    payload['diagnostic']['fields'] = [hidden, closed, geometry]
    result = ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]
    assert result['findings'] == [] and result['summary'] == 'No active form controls found.'
    saved = json.loads(next((config.private/'snapshots').glob('*.json')).read_text(encoding='utf-8'))
    assert len(saved['diagnostic']['fields']) == 3
    assert not any(f['active'] for f in saved['diagnostic']['fields'])


def test_t060_page_paths_distinguishable_without_url_secrets(http_dashboard, config):
    for job in ('job-one', 'job-two'):
        payload = analysis_payload(context_url=f'https://user:URL_PASSWORD@fixture.test/{job}/apply?token=QUERY_SECRET#FRAGMENT_SECRET')
        status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', payload)
        assert status == 200 and result['ok']
        assert 'page_url' not in result
    saved = [json.loads(p.read_text(encoding='utf-8')) for p in (config.private/'snapshots').glob('*.json')]
    assert {p['diagnostic']['page_url'] for p in saved} == {'https://fixture.test/job-one/apply', 'https://fixture.test/job-two/apply'}
    assert all(secret not in json.dumps(saved) for secret in ('URL_PASSWORD', 'QUERY_SECRET', 'FRAGMENT_SECRET', 'user:'))
    payload = analysis_payload(context_url='https://fixture.test/' + 'a'*3000)
    assert ext(http_dashboard, 'POST', '/ext/snapshot', payload)[0] == 200
    assert all(len(json.loads(p.read_text(encoding='utf-8'))['diagnostic']['page_url']) <= 2048 for p in (config.private/'snapshots').glob('*.json'))


def test_t063_repair_diagnosis_before_after_sanitized(http_dashboard, config, monkeypatch):
    from autoapply import codex_writer
    monkeypatch.setattr(codex_writer.CodexWritingProvider, 'generate_response', lambda *a, **k: pytest.fail('Snapshot must not invoke a provider'))
    payload = analysis_payload()
    before = json.loads(json.dumps(payload['diagnostic']))
    before['fields'][0]['value'] = 'PRIVATE_BEFORE'
    before['answer'] = 'PRIVATE_ANSWER'
    payload['diagnostic']['fields'][0].update(answered=True, repair_status='verified', value='PRIVATE_AFTER')
    original = payload['diagnostic']['fields'][0]
    payload['diagnostic']['fields'] += [dict(original, label='Unknown personal fact', required=True, answered=False, repair_status='unresolved'),
                                      dict(original, label='Ethnicity', required=False, answered=False, repair_status='optional_skip'),
                                      dict(original, label='Contact consent', kind='checkbox', answered=False, repair_status='verification_failed')]
    payload['diagnostic']['repair'] = dict(filled=1, attempts=2, stopped='none', before=before, answers=['PRIVATE_REPAIR'])
    status, result, _ = ext(http_dashboard, 'POST', '/ext/snapshot', payload)
    assert status == 200 and result['summary'] == 'Filled 1 \u00b7 2 need attention', result
    assert {f['label'] for f in result['findings']} == {'Unknown personal fact','Contact consent'}
    assert result['findings'][-1]['status'] == 'verification_failed'
    saved=json.loads(next((config.private/'snapshots').glob('*.json')).read_text(encoding='utf-8'))
    report=saved['diagnostic']
    assert report['repair']['before']['fields'][0]['answered'] is False
    assert report['fields'][0]['answered'] is True
    assert report['repair']['filled']==1 and report['repair']['attempts']==2
    assert 'PRIVATE' not in json.dumps(saved)+json.dumps(result)
    payload['diagnostic']['repair']['stopped']='user_edit'
    assert 'Repair stopped' in ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]['summary']


@pytest.mark.parametrize('change', [dict(filled=True),dict(filled=501),dict(attempts=3),dict(stopped='PRIVATE'),dict(before={'repair':{}})])
def test_t063_repair_report_bounds(http_dashboard, change):
    payload=analysis_payload()
    payload['diagnostic']['repair']=dict(filled=0,attempts=1,stopped='none',before={'fields':[]})
    payload['diagnostic']['repair'].update(change)
    assert ext(http_dashboard,'POST','/ext/snapshot',payload)[0]==400


def test_t065_failed_progress_holds_claim_and_old_confirmation_is_rejected(http_dashboard, config, db, listing):
    ids = queue(db, config, listing, 3)
    assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids))[0] == 200
    first = ext(http_dashboard, 'GET', '/ext/run/next')[1]
    for state in ('needs_input', 'failed', 'filled'):
        assert ext(http_dashboard, 'POST', '/ext/run/result', dict(first, state=state, detail='Review current'))[0] == 200
        assert ext(http_dashboard, 'GET', '/ext/run/next')[1] == dict(wait=True)
    assert web(http_dashboard, '/api/ext-run/cancel', dict(run_id=first['run_id']))[0] == 200
    assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids))[0] == 200
    second = ext(http_dashboard, 'GET', '/ext/run/next')[1]
    before = db.application(ids[0])
    assert ext(http_dashboard, 'POST', '/ext/applied', dict(application_id=ids[0], run_id=first['run_id'],
               url=first['url'], evidence='Application submitted'))[0] == 409
    assert db.application(ids[0]) == before
    assert ext(http_dashboard, 'GET', '/ext/run/status')[1]['current'] == second['application_id']
