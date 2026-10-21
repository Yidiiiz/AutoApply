"""Source refresh regressions; all revisions, pages and clocks are offline."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from autoapply.sources import (BrowserJobSource, GitHubRepositorySource, context_key,
                               parse_repository, record_contexts, scan_github)


def table(rows):
    return '| Company | Role | Location | Application | Date |\n|---|---|---|---|---|\n' + '\n'.join(
        f'| Example | {title} | NY | [Apply](https://example.test/jobs/{i}) | {date} |'
        for i, title, date in rows) + '\n'


def test_context_fingerprints_do_not_cross_sources_documents_or_tables(db):
    text = table([(i, f'Intern {i}', '2026-10-20') for i in range(4)])
    observations = []
    assert len(parse_repository(text, 'repo-a', document='README.md', observations=observations)) == 4
    record_contexts(db, observations)
    cache = {r['context_key']: r for r in db.rows('SELECT * FROM discovery_contexts')}
    assert parse_repository(text, 'repo-a', document='README.md', cache=cache) == []
    assert len(parse_repository(text, 'repo-b', document='README.md', cache=cache)) == 4
    assert len(parse_repository(text, 'repo-a', document='README-Off-Season.md', cache=cache)) == 4
    # A duplicate table cannot hide a new section; its membership is independent.
    parsed = parse_repository(text + '\n## New section\n' + text + '\n' +
                              table([(9, 'New Intern', '')]), 'repo-a', document='README.md', cache=cache)
    assert [x.url.rsplit('/', 1)[-1] for x in parsed] == ['0', '1', '2', '3', '9']
    changed = text.replace('Intern 3', 'Reopened Intern 3')
    assert len(parse_repository(changed, 'repo-a', document='README.md', cache=cache)) == 4
    # Yesterday's section cache is never a daily boundary.
    for prior in cache.values():
        prior['checked_day'] = '2000-01-01'
    assert len(parse_repository(text, 'repo-a', document='README.md', cache=cache)) == 4


async def test_quick_unchanged_sha_and_daily_buried_change(config, db, monkeypatch):
    config.data['github_sources'] = [{'url': 'https://github.com/example/jobs'}]
    contents = {'README.md': table([(i, f'Intern {i}', '2026-10-20') for i in range(6)]),
                'README-Off-Season.md': table([(99, 'Archive Intern', '')])}
    revision = ['one']
    monkeypatch.setattr(GitHubRepositorySource, 'update', lambda self: (revision[0], 'main', datetime.now(timezone.utc)))
    def git(self, command, *args, **kwargs):
        if command == 'ls-tree':
            return '\n'.join(contents)
        assert command == 'show'
        return contents[args[0].split(':', 1)[1]]
    monkeypatch.setattr(GitHubRepositorySource, 'git', git)
    original = GitHubRepositorySource.discover
    calls = []
    monkeypatch.setattr(GitHubRepositorySource, 'discover', lambda self, *args: (calls.append(args), original(self, *args))[1])
    ingest = Mock(wraps=db.ingest)
    monkeypatch.setattr(db, 'ingest', ingest)
    assert not (await scan_github(config, db, quick=True))['errors']
    assert ingest.call_count == 7
    ingest.reset_mock()
    assert not (await scan_github(config, db, quick=True))['errors']
    assert len(calls) == 1 and ingest.call_count == 0
    revision[0] = 'two'  # unrelated commit: exact unchanged sections are cheap
    await scan_github(config, db, quick=True)
    assert ingest.call_count == 0
    contents['README.md'] = contents['README.md'].replace('Intern 5', 'Changed Intern 5')
    await scan_github(config, db, force=True)
    assert ingest.call_count == 7
    assert any(c.args[0].title == 'Changed Intern 5' for c in ingest.call_args_list)
    members = [json.loads(r['members']) for r in db.rows('SELECT * FROM discovery_contexts')]
    assert any('https://example.test/jobs/99' in m for m in members)


async def test_unordered_pinned_rows_other_queries_and_pages_never_stop(config, db):
    config.data['linkedin'].update(enabled=True, search_urls=[
        'https://www.linkedin.com/jobs/search/?keywords=a',
        'https://www.linkedin.com/jobs/search/?keywords=b'])
    config.data['discovery'].update(max_pages_per_query=3, max_results_per_query=20)
    page = Mock(close=AsyncMock())
    page.content = AsyncMock()
    async def navigate(page, url):
        page.url = url
        later = 'page=2' in url
        rows = [3, 4] if later else [1, 2, 3]
        html = ''.join(f'<li><a href="/jobs/view/{i}">Intern {i}</a> '
                       f'{"today" if i == 4 else "2 months ago"}</li>' for i in rows)
        if not later:
            html += '<a rel="next" href="?page=2">Next</a>'
        page.content.return_value = html
        return None, ''
    # No real navigation or page condition detector.
    browser = Mock(new_page=AsyncMock(return_value=page), navigate=AsyncMock(side_effect=navigate))
    from unittest.mock import patch
    with patch('autoapply.browser.page_condition', AsyncMock(return_value=(None, ''))):
        source = BrowserJobSource('linkedin', config, browser, db)
        found = await source.discover()
    assert [x.title for x in found].count('Intern 4') == 2
    assert source.pages_scanned == 4
    assert len({key for key, _, _ in source.observations}) == 4
    assert all('https://www.linkedin.com/jobs/view/3' in members for _, _, members in source.observations)
    assert context_key('linkedin', 'a', page='2') != context_key('linkedin', 'b', page='2')
    with patch('autoapply.browser.page_condition', AsyncMock(return_value=(None, ''))):
        daily = BrowserJobSource('linkedin', config, browser, db)
        daily.newest_first_guaranteed = True
        assert len(await daily.discover(full=True)) == 8
        assert daily.pages_scanned == 4  # full bypasses even an adapter guarantee


def test_discovery_context_migration_is_additive_and_restart_safe(config):
    from autoapply.database import Database
    path = config.private / 'migration.sqlite3'
    first = Database(path)
    first.set_setting('existing', {'keep': True})
    first.conn.execute('DROP TABLE discovery_contexts')
    first.close()
    second = Database(path)
    assert second.setting('existing') == {'keep': True}
    assert second.rows('SELECT * FROM discovery_contexts') == []
    second.close()


def test_quick_section_changed_duplicate_unknown_dates_and_cap(db):
    text = table([(1, 'Pinned Intern', '2020-01-01'), (2, 'Old Intern', '2020-01-01'),
                  (3, 'Old Intern', '2020-01-01'), (1, 'Reopened Intern', ''), (4, 'New Intern', '')])
    observations = []
    parsed = parse_repository(text, 'repo', max_results=5, observations=observations)
    assert len(parsed) == 5 and parsed[-1].title == 'New Intern'
    assert len(observations[0][2]['https://example.test/jobs/1']) == 2
    record_contexts(db, observations)
    cache = {r['context_key']: r for r in db.rows('SELECT * FROM discovery_contexts')}
    assert parse_repository(text, 'repo', max_results=5, cache=cache) == []
    assert len(parse_repository(text, 'repo', max_results=3, cache=cache)) == 3
    assert len(parse_repository(text, 'repo', max_results=5)) == 5  # full bypass


def test_remote_unchanged_sha_does_not_fetch(config, monkeypatch):
    source = GitHubRepositorySource(config, 'https://github.com/fixture/jobs')
    source.path.mkdir(parents=True)
    sha = 'a' * 40
    calls = []
    def git(command, *args, **kwargs):
        calls.append(command)
        if command == 'ls-remote':
            return f'ref: refs/heads/main\tHEAD\n{sha}\tHEAD\n'
        if command == 'rev-parse':
            return sha
        if command == 'show':
            return '2026-10-20T00:00:00+00:00'
        raise AssertionError('Unexpected remote operation')
    monkeypatch.setattr(source, 'git', git)
    assert source.update()[0] == sha
    assert calls == ['ls-remote', 'rev-parse', 'show']


async def test_browser_auth_and_unknown_layout_fail_without_skipping_other_queries(config, db, monkeypatch):
    config.data['handshake'].update(enabled=True, search_urls=[
        'https://school.joinhandshake.com/stu/postings?query=' + name for name in ('auth', 'changed', 'empty')])
    page = Mock(close=AsyncMock(), content=AsyncMock())
    async def navigate(page, url):
        page.url = url
        page.content.return_value = '<p>No jobs found</p>' if 'empty' in url else '<p>Changed layout</p>'
        return ('login', 'fixture') if 'auth' in url else (None, '')
    monkeypatch.setattr('autoapply.browser.page_condition', AsyncMock(return_value=(None, '')))
    browser = Mock(new_page=AsyncMock(return_value=page), navigate=AsyncMock(side_effect=navigate))
    source = BrowserJobSource('handshake', config, browser, db)
    assert await source.discover(full=True) == []
    assert source.pages_scanned == 3 and source.errors == 2
    page.close.assert_awaited_once()
