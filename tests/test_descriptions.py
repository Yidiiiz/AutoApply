import json
import sqlite3

import pytest
from bs4 import BeautifulSoup, Comment, Declaration, Doctype, ProcessingInstruction

from autoapply import descriptions, liveness
from autoapply.applications import description_text


DETAILS = ('Work with our engineering team to develop and test software used by customers. '
           'Collaborate with mentors on design, implementation, documentation and code reviews. '
           'Applicants should be pursuing a bachelor degree in computer science or a related field.')
HTML_DETAILS = '<p>' + DETAILS + '</p>'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Unmocked HTTP request')
    monkeypatch.setattr(liveness, '_get', fail)


@pytest.mark.parametrize('url,endpoint,payload,expected', [
    ('https://boards.greenhouse.io/example/jobs/123',
     'https://boards-api.greenhouse.io/v1/boards/example/jobs/123',
     {'content': '&lt;p&gt;Build &amp;amp; learn&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;' + HTML_DETAILS},
     'Build & learn\nPython\n' + DETAILS),
    ('https://job-boards.greenhouse.io/example/jobs/123?gh_src=fixture',
     'https://boards-api.greenhouse.io/v1/boards/example/jobs/123',
     {'content': '<p>Build</p><p>Learn</p>' + HTML_DETAILS}, 'Build\nLearn\n' + DETAILS),
    ('https://boards.greenhouse.io/embed/job_app?for=example&token=123',
     'https://boards-api.greenhouse.io/v1/boards/example/jobs/123',
     {'content': '<p>Build</p>' + HTML_DETAILS}, 'Build\n' + DETAILS),
    ('https://jobs.lever.co/example/abc/apply',
     'https://api.lever.co/v0/postings/example/abc',
     {'descriptionPlain': 'Build\nLearn', 'lists': [{'text': 'Requirements', 'content': '<ul><li>Python</li><li>SQL</li></ul>'}],
      'additionalPlain': DETAILS},
     'Build\nLearn\nRequirements\nPython\nSQL\n' + DETAILS),
    ('https://jobs.ashbyhq.com/example/abc/application',
     'https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=false',
     {'jobs': [{'id': 'other', 'descriptionPlain': 'Wrong role'}, {'id': 'abc', 'descriptionPlain': 'Build\nLearn\n' + DETAILS}]},
     'Build\nLearn\n' + DETAILS),
    ('https://jobs.ashbyhq.com/example/abc',
     'https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=false',
     {'jobs': [{'id': 'abc', 'descriptionHtml': '<p>Build</p><p>Learn</p>' + HTML_DETAILS}]}, 'Build\nLearn\n' + DETAILS),
    ('https://jobs.smartrecruiters.com/Example/123-software-intern',
     'https://api.smartrecruiters.com/v1/companies/Example/postings/123',
     {'jobAd': {'sections': {'jobDescription': {'text': '<p>Build</p>'}, 'qualifications': {'text': '<p>Learn</p>' + HTML_DETAILS}}}},
     'Build\nLearn\n' + DETAILS),
    ('https://example.wd5.myworkdayjobs.com/en-US/Careers/job/NY/Intern_R1',
     'https://example.wd5.myworkdayjobs.com/wday/cxs/example/Careers/job/NY/Intern_R1',
     {'jobPostingInfo': {'jobDescription': '<p>Build</p><p>Learn</p>' + HTML_DETAILS}}, 'Build\nLearn\n' + DETAILS),
])
def test_public_endpoints(monkeypatch, url, endpoint, payload, expected):
    calls = []
    def get(address, timeout, accept=''):
        calls.append((address, timeout, accept))
        assert address == endpoint
        return 200, address, json.dumps(payload)
    monkeypatch.setattr(liveness, '_get', get)
    assert descriptions.fetch_description(url, timeout=7) == expected
    assert calls == [(endpoint, 7, 'application/json')]


@pytest.mark.parametrize('data', [
    {'@type': 'JobPosting', 'description': '<p>Build <strong>Python</strong></p><p>Learn</p>' + HTML_DETAILS},
    [{'@type': 'Organization'}, {'@type': 'JobPosting', 'description': '<p>Build Python</p><p>Learn</p>' + HTML_DETAILS}],
    {'@graph': [{'@type': ['Thing', 'JobPosting'], 'description': '<p>Build Python</p><p>Learn</p>' + HTML_DETAILS}]},
])
def test_json_ld(monkeypatch, data):
    body = '<script type="application/ld+json">broken</script>'
    body += '<script type="application/ld+json">' + json.dumps(data) + '</script><main>Wrong text</main>'
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', body))
    assert descriptions.fetch_description('https://example.test/job') == 'Build Python\nLearn\n' + DETAILS


@pytest.mark.parametrize('markup', [
    '<div id="content">{}</div>', '<div id="job-description">{}</div>',
    '<div class="job-description">{}</div>', '<div class="jv-job-detail-description">{}</div>',
    '<div class="posting-page"><div class="content">{}</div></div>',
    '<div data-testid="job-description">{}</div>', '<main>{}</main>', '<article>{}</article>',
])
def test_html_fallback(monkeypatch, markup):
    body = markup.format('<h2>Requirements</h2><p>Python <strong>preferred</strong></p><p>SQL</p><script>Ignore</script>' + HTML_DETAILS)
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', body))
    assert descriptions.fetch_description('https://example.test/job') == 'Requirements\nPython preferred\nSQL\n' + DETAILS


@pytest.mark.parametrize('node_type', [Comment, Declaration, Doctype, ProcessingInstruction])
def test_description_text_skips_non_content_nodes(node_type):
    soup = BeautifulSoup('<p>Build <strong>Python</strong></p><p>Learn</p>', 'html.parser')
    soup.insert(0, node_type('non-content'))
    assert description_text(soup) == 'Build Python\nLearn'


def test_doctype_html_fallback(monkeypatch):
    body = '<!DOCTYPE html><?instruction ignore?><html><body>' + HTML_DETAILS + '</body></html>'
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', body))
    assert descriptions.fetch_description('https://example.test/job') == DETAILS


@pytest.mark.parametrize('source', ['html', 'json_ld', 'lever'])
@pytest.mark.parametrize('length', [0, 199, 200])
def test_minimum_description_length(monkeypatch, source, length):
    text = 'x' * length
    padded = '  ' + text + ' \n '
    url = 'https://example.test/job'
    if source == 'lever':
        url = 'https://jobs.lever.co/example/abc'
        body = json.dumps({'descriptionPlain': padded})
    elif source == 'json_ld':
        body = '<script type="application/ld+json">' + json.dumps(
            {'@type': 'JobPosting', 'description': padded}) + '</script>'
    else:
        body = '<main>' + padded + '</main>'
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', body))
    assert descriptions.fetch_description(url) == (text if length >= 200 else '')


@pytest.mark.parametrize('body', [
    '<!DOCTYPE html><title>Kitware - Software Developer Internship</title>',
    '<!DOCTYPE html><html><body><p>Nokia</p></body></html>',
])
def test_junk_not_stored(monkeypatch, tmp_path, body):
    def get(url, timeout, accept=''):
        return 404 if accept == 'application/json' else 200, url, body
    monkeypatch.setattr(liveness, '_get', get)
    text = descriptions.fetch_description('https://jobs.lever.co/example/closed')
    assert text == ''
    with sqlite3.connect(tmp_path / 'descriptions.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, description TEXT)')
        db.executemany('INSERT INTO jobs VALUES (?, ?)', [(1, None), (2, 'Saved')])
        descriptions.store(db, 1, text)
        descriptions.store(db, 2, text)
        assert db.execute('SELECT description FROM jobs ORDER BY id').fetchall() == [(None,), ('Saved',)]


@pytest.mark.parametrize('query,expected', [
    ('', '?in_iframe=1'),
    ('?mode=job&source=fixture&blank=', '?mode=job&source=fixture&blank=&in_iframe=1'),
    ('?in_iframe=0&mode=job', '?in_iframe=1&mode=job'),
    ('?in_iframe=1', '?in_iframe=1'),
])
@pytest.mark.parametrize('markup', [
    '<div id="content">Navigation<div class="iCIMS_JobContent">{}</div></div>',
    '<div class="iCIMS_InfoMsg_Job">{}</div>',
    '<main>{}</main>',
])
def test_icims_iframe(monkeypatch, query, expected, markup):
    url = ('https://careers-peraton.icims.com/jobs/172061/'
           'summer-2027-software-development-intern--topeka%2c-ks/job')
    calls = []
    def get(address, timeout, accept=''):
        calls.append((address, timeout, accept))
        if address == url + expected:
            return 200, address, '<!DOCTYPE html>' + markup.format(HTML_DETAILS)
        return 200, address, '<iframe src="posting"></iframe>'
    monkeypatch.setattr(liveness, '_get', get)
    assert descriptions.fetch_description(url + query, timeout=7) == DETAILS
    assert calls == [(url + expected, 7, '')]


@pytest.mark.parametrize('failure', [TimeoutError(), OSError(), ValueError(), RuntimeError()])
def test_network_failure(monkeypatch, failure):
    def get(*args):
        raise failure
    monkeypatch.setattr(liveness, '_get', get)
    assert descriptions.fetch_description('https://jobs.lever.co/example/abc') == ''


@pytest.mark.parametrize('payload', ['invalid json', 'null', '[]', '{}'])
def test_bad_api_falls_back(monkeypatch, payload):
    def get(url, timeout, accept=''):
        return 200, url, payload if accept == 'application/json' else '<main><p>Fallback</p>' + HTML_DETAILS + '</main>'
    monkeypatch.setattr(liveness, '_get', get)
    assert descriptions.fetch_description('https://jobs.lever.co/example/abc') == 'Fallback\n' + DETAILS


@pytest.mark.parametrize('status', [403, 404, 410, 500])
def test_http_failure(monkeypatch, status):
    monkeypatch.setattr(liveness, '_get', lambda *args: (status, '', '<main>Error</main>'))
    assert descriptions.fetch_description('https://example.test/job') == ''


@pytest.mark.parametrize('url', ['', None, 'file:///tmp/job', 'https://[', 'https://user:password@example.test/job'])
def test_invalid_url(url):
    assert descriptions.fetch_description(url) == ''


def test_description_cap(monkeypatch):
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', '<main>' + 'x' * 25000 + '</main>'))
    assert descriptions.fetch_description('https://example.test/job') == 'x' * 20000


def test_parse_failure(monkeypatch):
    monkeypatch.setattr(liveness, '_get', lambda *args: (200, '', '<main>Job</main>'))
    def fail(*args):
        raise ValueError('Invalid document')
    monkeypatch.setattr(descriptions, 'BeautifulSoup', fail)
    assert descriptions.fetch_description('https://example.test/job') == ''


def test_store_only_lengthens(tmp_path):
    with sqlite3.connect(tmp_path / 'descriptions.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, description TEXT)')
        db.executemany('INSERT INTO jobs VALUES (?, ?)', [(1, 'Saved'), (2, None)])
        for text in ('', 'tiny', 'Equal'):
            descriptions.store(db, 1, text)
            assert db.execute('SELECT description FROM jobs WHERE id=1').fetchone()[0] == 'Saved'
        descriptions.store(db, 1, 'Longer description')
        descriptions.store(db, 2, 'New description')
        descriptions.store(db, 99, 'No such job')
        assert db.execute('SELECT description FROM jobs ORDER BY id').fetchall() == [
            ('Longer description',), ('New description',)]
