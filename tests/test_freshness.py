import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from autoapply.config import Config
from autoapply.database import Database, SCHEMA
from autoapply.freshness import (closed_status, extract_posting_date, freshness_state,
                                 parse_posted, stale_boundary)
from autoapply.models import State
from autoapply.sources import BrowserJobSource, parse_repository, recency_url

NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize('html', [
    '<p>Updated <time datetime="2026-09-20">yesterday</time></p>',
    '<p>Last modified 2 days ago</p>',
    '<p>Updated: 2 days ago</p>',
    '<time itemprop="dateModified" datetime="2026-09-20">Updated yesterday</time>',
    '<article>Internship: apply today!</article>',
    '<p>You applied 2 days ago</p>',
])
def test_html_updated_is_not_posted(html):
    assert extract_posting_date(html=html, reference=NOW).posted_at is None


def test_html_explicit_posted_date_and_priority():
    evidence = extract_posting_date(html='<p>Posted on July 1, 2026</p><time datetime="2026-09-20"></time>', reference=NOW)
    assert evidence.source == 'explicit.posted_on'
    assert freshness_state(evidence.posted_at, reference=NOW) == 'STALE'
    assert extract_posting_date(html='<p>Posted 2 days ago</p>', reference=NOW).posted_at == (NOW-timedelta(days=2)).isoformat()


@pytest.mark.parametrize('age,state', [(0,'FRESH'),(1,'FRESH'),(29,'FRESH'),(30,'FRESH'),
                                     (30+1/86400,'STALE'),(31,'STALE'),(-1,'UNKNOWN_DATE')])
def test_exact_rolling_window(age,state):
    assert freshness_state((NOW-timedelta(days=age)).isoformat(), reference=NOW) == state


@pytest.mark.parametrize('value,hours', [('Today',0),('Just posted',0),('1 hour ago',1),
    ('5 hours ago',5),('Yesterday',24),('2 days ago',48),('1 week ago',168),
    ('3 weeks ago',504),('30 days ago',720)])
def test_relative(value,hours):
    posted = parse_posted(value,NOW)
    assert posted == NOW-timedelta(hours=hours)
    assert freshness_state(posted,reference=NOW) == 'FRESH'


@pytest.mark.parametrize('value',['30+ days ago','Over 30 days ago'])
def test_relative_lower_bounds_stale(value):
    assert freshness_state(parse_posted(value,NOW),reference=NOW) == 'STALE'


@pytest.mark.parametrize('value',[None,'','bad date','1 month ago','new','2026-02-30','9999999999999999999999 days ago'])
def test_unknown_and_malformed(value):
    assert parse_posted(value,NOW) is None
    assert freshness_state(value,reference=NOW) == 'UNKNOWN_DATE'


def test_timezone_and_dst():
    assert freshness_state('2026-08-22T08:00:00-04:00',reference=NOW) == 'FRESH'
    assert freshness_state('2026-08-22T07:59:59-04:00',reference=NOW) == 'STALE'
    # Across spring DST: exact elapsed hours, not calendar-day subtraction.
    assert freshness_state('2026-02-07T12:00:00-05:00',reference='2026-03-09T13:00:00-04:00') == 'FRESH'
    assert freshness_state('2026-02-07T11:59:59-05:00',reference='2026-03-09T13:00:00-04:00') == 'STALE'


def test_priority_metadata_repost_and_modified_dates():
    html = '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','datePosted':'2026-07-01','dateModified':'2026-09-21'})+'</script>'
    evidence = extract_posting_date(html=html,api='2026-09-20',relative='today',reference=NOW)
    assert evidence.source == 'json_ld.datePosted'
    assert freshness_state(evidence.posted_at,reference=NOW) == 'STALE'
    repost = extract_posting_date(html=html,repost='2026-09-20',genuine_repost=True,reference=NOW)
    assert repost.source == 'explicit.repost' and repost.original_posted_at.startswith('2026-07-01')
    assert freshness_state(repost.posted_at,reference=NOW) == 'FRESH'
    assert extract_posting_date(html=html,repost='2026-09-20',reference=NOW) == evidence
    assert extract_posting_date(structured='invalid',api='2026-09-20',reference=NOW).posted_at is None
    assert extract_posting_date(api='2026-09-22',reference=NOW).posted_at is None
    modified = '<script type="application/ld+json">{"@type":"JobPosting","dateModified":"2026-09-21"}</script>'
    assert extract_posting_date(html=modified,reference=NOW).posted_at is None


@pytest.mark.parametrize('days',[0,31,60,90,True])
def test_config_cannot_relax_policy(tmp_path,days):
    with pytest.raises(ValueError):
        Config(tmp_path,{'jobs':{'max_listing_age_days':days}})


@pytest.mark.parametrize('text',['job closed','position filled','no longer accepting applications',
    'this job is no longer available','job expired','posting removed','applications closed'])
def test_definitive_closure(text):
    assert closed_status(text) == 'CLOSED'


@pytest.mark.parametrize('status',[401,403,429,500,503])
def test_security_and_transient_errors_not_closed(status):
    assert closed_status('job closed',status) is None


def test_removed_and_security():
    assert closed_status(http_status=404) == 'REMOVED'
    assert closed_status(http_status=410) == 'REMOVED'
    assert closed_status(http_status=404,blocked=True) is None
    assert closed_status('CAPTCHA; sign in; network failed') is None


def test_stale_and_unknown_never_rank_or_create_apps(config,db,listing,monkeypatch):
    def no_rank(*args):
        raise AssertionError('ineligible listing ranked')
    monkeypatch.setattr('autoapply.database.priority',no_rank)
    for suffix,posted in [('old',(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()),('unknown',None),('bad','garbage')]:
        assert db.ingest(replace(listing,url='https://example.test/'+suffix,posted_at=posted),config) == (None,False)
    assert not db.rows('SELECT * FROM jobs')
    assert not db.rows('SELECT * FROM applications')
    stats = db.refresh_listing_statistics()
    assert stats['discovered_total'] == 3
    assert stats['rejected_too_old'] == 1 and stats['rejected_unknown_date'] == 2


def test_closed_fresh_never_processed(config,db,listing):
    assert db.ingest(replace(listing,closed=True),config) == (None,False)
    assert db.claim() is None
    assert db.refresh_listing_statistics()['closed'] == 1


def test_duplicate_and_changed_date(config,db,listing):
    first, _ = db.ingest(listing,config)
    before = db.one("SELECT discovered_at FROM jobs WHERE id=1")
    again, created = db.ingest(replace(listing,url=listing.url+'?utm_source=ad&ref=abc&source=linkedin'),config)
    assert again == first and not created
    assert db.one("SELECT discovered_at FROM jobs WHERE id=1") == before
    stale = (datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
    db.ingest(replace(listing,posted_at=stale),config)
    db.ingest(listing,config)
    assert db.application(1)['posted_at'] == stale
    assert db.claim() is None
    assert db.refresh_listing_statistics()['duplicates_skipped'] == 3


def test_genuine_repost(config,db,listing):
    old = (datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    db.ingest(replace(listing,posted_at=old,updated_at=datetime.now(timezone.utc).isoformat()),config)
    assert db.claim() is None
    db.ingest(replace(listing,posted_at=old,reposted_at=listing.posted_at,posted_at_source='explicit.repost'),config)
    assert db.claim()['status'] == 'CHECKING'


def test_rejected_source_id_cannot_bypass_with_new_url(config,db,listing):
    old = (datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    db.ingest(replace(listing,source_id='source-123',posted_at=old),config)
    db.ingest(replace(listing,source_id='source-123',url='https://example.test/changed'),config)
    assert db.rows('SELECT * FROM applications') == []
    assert len(db.rows('SELECT * FROM listing_observations')) == 1


def test_batch_rollback_and_single_history_export(config,db,listing,monkeypatch):
    calls = []
    original = db.history.sync
    def sync(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(db.history,'sync',sync)
    with db.ingest_batch():
        db.ingest(listing,config)
        db.ingest(replace(listing,url='https://example.test/second'),config)
    assert len(calls) == 1
    with pytest.raises(RuntimeError), db.ingest_batch():
        db.ingest(replace(listing,url='https://example.test/third'),config)
        raise RuntimeError('interrupted')
    assert len(db.rows('SELECT * FROM applications')) == 2
    assert db.refresh_listing_statistics()['discovered_total'] == 2


def test_cleanup_preserves_unconfirmed_manual_submission(config,db,listing):
    db.ingest(listing,config)
    db.claim()
    db.transition(1,State.SUBMITTING,submit_intent_at=datetime.now(timezone.utc).isoformat())
    db.transition(1,State.MANUAL_REVIEW,'spam rejected; outcome unknown')
    db.update_security(1,security_state='SPAM_REJECTED',manual_action_required=1,retry_allowed=0)
    snapshot = db.one('SELECT * FROM applications WHERE id=1')
    db.execute('UPDATE jobs SET posted_at=? WHERE id=1',('2020-01-01',))
    db.cleanup_stale_listings()
    assert not db.guard_listing(1)
    assert db.one('SELECT * FROM applications WHERE id=1') == snapshot


def test_corrupt_listing_statistics_rebuild_on_startup(config,db,listing):
    db.ingest(listing,config)
    (config.private/'listing_statistics.json').write_text('broken',encoding='utf-8')
    other = Database(config.private/'test.sqlite3')
    try:
        assert other.refresh_listing_statistics()['discovered_total'] == 1
    finally:
        other.close()


def test_cleanup_preserves_applied_history_and_is_idempotent(config,db,listing):
    db.ingest(listing,config)
    db.transition(1,State.SUBMITTED,confirmation_text='confirmed receipt',submitted_at=datetime.now(timezone.utc).isoformat())
    snapshot = db.one('SELECT * FROM applications WHERE id=1')
    history_before = db.history.get_application(1)
    old = (datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    db.execute("UPDATE jobs SET posted_at=?,listing_status='CLOSED' WHERE id=1",(old,))
    report = db.cleanup_stale_listings()
    assert report['culled_from_active_storage'] == 1
    assert report['applications_preserved'] == 1
    assert db.rows('SELECT * FROM active_listings') == []
    assert db.one('SELECT * FROM applications WHERE id=1') == snapshot
    after = db.history.get_application(1)
    for key in ['status_history','confirmation_text','submitted_at','application_state']:
        assert after[key] == history_before[key]
    again = db.cleanup_stale_listings()
    assert again['changed'] == 0 and again['culled_from_active_storage'] == 0
    assert db.refresh_listing_statistics()['culled_closed_stale'] == 1


def test_queue_ages_out_without_browser(config,db,listing):
    db.ingest(listing,config)
    db.execute('UPDATE jobs SET posted_at=? WHERE id=1',((datetime.now(timezone.utc)-timedelta(days=31)).isoformat(),))
    assert db.claim() is None
    assert db.application(1)['attempts'] == 0
    assert db.application(1)['listing_status'] == 'STALE'
    assert db.guard_listing(1) is False
    assert db.application(1)['failure_reason'] == 'STALE_BEFORE_APPLICATION'


def test_closed_before_submission_not_failure(config,db,listing):
    db.ingest(listing,config)
    db.claim()
    db.mark_listing_closed(1)
    assert not db.guard_listing(1)
    assert db.application(1)['status'] == 'CLOSED'
    assert db.application(1)['retry_allowed'] == 0
    stats = json.loads((db.history.root/'statistics.json').read_text())
    assert stats['status_counts']['FAILED'] == 0
    assert stats['listing_stats']['closed'] == 1


def test_unknown_cannot_bypass_cached_queue(config,db,listing):
    db.ingest(listing,config)
    db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
    assert not db.guard_listing(1)
    assert db.claim() is None


async def test_stale_pending_questions_do_not_reenter_discovery_outbox(config,db,listing,monkeypatch):
    from autoapply.engine import Engine
    from autoapply.models import Question
    db.ingest(listing,config)
    db.transition(1, State.NEEDS_INPUT, stage='discovery')
    db.question(1, Question('missing', 'Missing fact', required=True), 'unknown')
    db.execute('UPDATE jobs SET posted_at=? WHERE id=1', ('2020-01-01',))
    async def no_network(*args):
        return dict(sources=0, new=0, errors=0, pages_scanned=0)
    monkeypatch.setattr('autoapply.engine.scan_github', no_network)
    engine = Engine(config, db)
    await engine.scan()
    assert engine.control.pending() == []
    assert not db.rows("SELECT * FROM notifications WHERE dedupe_key LIKE 'question:%'")
    assert json.loads(engine.control.command('status'))['listing_stats']['fresh_eligible'] == 0
    assert db.application(1)['status'] == 'NEEDS_INPUT'


def test_statistics_activity_and_rebuild(config,db,listing):
    db.ingest(listing,config)
    stats = db.refresh_listing_statistics()
    assert stats['fresh_eligible'] == stats['fresh_discovered_today'] == stats['fresh_discovered_this_week'] == 1
    assert stats['average_listing_age_at_discovery'] >= 0
    assert db.history.rebuild_statistics()['listing_stats'] == stats


def test_legacy_migration_preserves_history(tmp_path):
    path = tmp_path/'legacy.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO jobs(identity_key,company,title,location,canonical_url,posted_at,discovered_at,status,date_evidence) VALUES ('one','Example','Intern','NY','https://example.test/1','2020-01-01','2020-01-01','SUBMITTED','')")
    connection.execute("INSERT INTO applications(job_id,status,updated_at,submitted_at,confirmation_text) VALUES (1,'SUBMITTED','2020-01-02','2020-01-02','receipt')")
    connection.commit()
    connection.close()
    db = Database(path)
    try:
        assert db.listing_startup_report['old'] == 1
        assert db.listing_startup_report['applications_preserved'] == 1
        assert db.application(1)['status'] == 'SUBMITTED'
        assert db.history.get_application(1)['confirmation_text'] == 'receipt'
        assert (tmp_path/'listing_freshness_backup.sqlite3').exists()
        assert not db.rows('SELECT * FROM active_listings')
    finally:
        db.close()


def test_repository_limit_and_updated_date_unknown():
    rows = '| Company | Role | Location | Application | Updated |\n|---|---|---|---|---|\n'
    rows += '\n'.join(f'| A | Intern | NYC | [Apply](https://example.test/{n}) | today |' for n in range(20))
    items = parse_repository(rows,'https://github.com/example/jobs',NOW,max_results=3)
    assert len(items) == 3 and all(item.posted_at is None for item in items)


def test_boundary_requires_guarantee(listing):
    old = replace(listing,posted_at='2026-01-01')
    fresh = replace(listing,posted_at='2026-09-20')
    assert stale_boundary([old],newest_first_guaranteed=True,reference=NOW)
    assert not stale_boundary([old],reference=NOW)
    assert not stale_boundary([old,fresh],newest_first_guaranteed=True,reference=NOW)
    assert not stale_boundary([replace(old,posted_at=None)],newest_first_guaranteed=True,reference=NOW)


def test_source_date_filter():
    base = 'https://www.linkedin.com/jobs/search/?keywords=intern'
    assert 'f_TPR=r2592000' in recency_url('linkedin',base,30)
    assert 'f_TPR=r604800' in recency_url('linkedin',base,14)
    assert 'f_TPR=r86400' in recency_url('linkedin',base+'&f_TPR=r86400',30)
    assert recency_url('handshake',base,30) == base


class FakePage:
    url = ''
    def __init__(self,pages):
        self.pages = pages
    async def content(self):
        return self.pages[self.url]
    async def close(self):
        pass


class FakeBrowser:
    def __init__(self,pages):
        self.page = FakePage(pages)
        self.visited = []
    async def new_page(self):
        return self.page
    async def navigate(self,page,url):
        page.url = url
        self.visited.append(url)
        return None,''


@pytest.mark.parametrize('ordered,expected',[(True,1),(False,2)])
async def test_pagination_order(config,db,monkeypatch,ordered,expected):
    async def condition(page):
        return None,''
    monkeypatch.setattr('autoapply.browser.page_condition',condition)
    first,second = 'https://example.test/search','https://example.test/page2'
    def card(ident,age):
        return f'<li><a href="/jobs/{ident}">Intern</a><time datetime="{age}"></time></li>'
    pages = {first:card(1,'2020-01-01')+f'<a rel="next" href="{second}">Next</a>',
             second:card(2,datetime.now(timezone.utc).isoformat())}
    config.data['handshake'] = dict(enabled=True,search_urls=[first])
    browser = FakeBrowser(pages)
    source = BrowserJobSource('handshake',config,browser,db)
    source.newest_first_guaranteed = ordered
    result = await source.discover()
    assert len(browser.visited) == expected
    assert len(result) == expected


async def test_pagination_cap(config,db,monkeypatch):
    async def condition(page):
        return None,''
    monkeypatch.setattr('autoapply.browser.page_condition',condition)
    first = 'https://example.test/search'
    pages = {first:'<li><a href="/jobs/1">Intern</a><time datetime="2020-01-01"></time></li><a rel="next" href="/page2">Next</a>'}
    config.data['handshake'] = dict(enabled=True,search_urls=[first])
    config.data['discovery']['max_pages_per_query'] = 1
    browser = FakeBrowser(pages)
    source = BrowserJobSource('handshake',config,browser,db)
    await source.discover()
    assert source.stop_reason == 'page cap' and browser.visited == [first]
