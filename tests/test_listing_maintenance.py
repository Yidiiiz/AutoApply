"""Offline Phase 3 scheduling, workload and query-plan regressions."""
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from autoapply.control import Controller
from autoapply.database import Database
from autoapply.engine import Engine
from autoapply.freshness import extract_posting_date, freshness_state, utc, window
from autoapply.models import State

NOW = utc('2026-09-25T12:00:00+00:00')


@pytest.fixture
def clock(monkeypatch):
    current = [NOW]
    monkeypatch.setattr('autoapply.listing_store.utc', lambda value=None: utc(current[0] if value is None else value))
    return current


def seed(db, size, *, posted=NOW, closed=False):
    """Synthetic listing workload without expensive, irrelevant application exports."""
    value = posted.isoformat() if posted else None
    fresh = freshness_state(value, reference=NOW)
    status = 'CLOSED' if closed else 'ACTIVE' if fresh == 'FRESH' else 'STALE' if fresh == 'STALE' else 'UNKNOWN'
    with db.transaction():
        for n in range(size):
            key, url = f'fixture:{n}', f'https://example.test/{n}'
            db.execute('''INSERT INTO jobs(identity_key,company,title,location,canonical_url,posted_at,
                original_posted_at,discovered_at,status,listing_status,freshness_state,listing_active,last_seen_at)
                VALUES (?,'Synthetic','Intern','NY',?,?,?,?,'QUEUED',?,?,?,?)''',
                (key,url,value,value,NOW.isoformat(),status,fresh,int(status == 'ACTIVE'),NOW.isoformat()))
            db.observe_listing(key,url,'synthetic',value,fresh,status,job_id=n+1,reference=NOW)
    db.cleanup_stale_listings(reference=NOW)


@pytest.mark.parametrize('size', [100, 1000])
@pytest.mark.parametrize('kind', ['active', 'historical', 'unknown'])
def test_unchanged_work_is_bounded(db, monkeypatch, size, kind):
    posted = NOW if kind == 'active' else NOW-window(30)-timedelta(days=1) if kind == 'historical' else None
    seed(db,size,posted=posted,closed=kind == 'historical')
    refresh = Mock(wraps=db._refresh_listing_statistics)
    monkeypatch.setattr(db,'_refresh_listing_statistics',refresh)
    before = db.conn.total_changes
    report = db.cleanup_stale_listings(reference=NOW+timedelta(minutes=5))
    assert report['candidate_rows'] == report['changed'] == report['observation_updates'] == 0
    assert db.conn.total_changes-before <= 3  # Schedule metadata only, independent of size.
    refresh.assert_not_called()


def test_only_expired_or_edited_rows_are_candidates(db):
    seed(db,1000)
    db.execute('UPDATE jobs SET posted_at=? WHERE id=57', ((NOW-window(30)-timedelta(microseconds=1)).isoformat(),))
    report = db.cleanup_stale_listings(reference=NOW)
    assert report['candidate_rows'] == report['changed'] == report['observation_updates'] == 1
    assert db.one('SELECT listing_status FROM jobs WHERE id=57')['listing_status'] == 'STALE'
    assert db.cleanup_stale_listings(reference=NOW)['candidate_rows'] == 0


def test_due_cadence_and_restart(config, db, clock, monkeypatch):
    db.cleanup_stale_listings(reference=NOW)
    cleanup = Mock(wraps=db.cleanup_stale_listings)
    monkeypatch.setattr(db,'cleanup_stale_listings',cleanup)
    for second in range(0,300,2):
        clock[0] = NOW+timedelta(seconds=second)
        assert db.maintain_listings() == {}
    cleanup.assert_not_called()
    other = Database(config.private/'test.sqlite3')
    try:
        assert other.listing_startup_report == {}
    finally:
        other.close()
    clock[0] = NOW+timedelta(minutes=5)
    assert db.maintain_listings()['candidate_rows'] == 0
    assert db.maintain_listings() == {}
    assert cleanup.call_count == 1
    clock[0] += timedelta(minutes=5)
    other = Database(config.private/'test.sqlite3')
    try:
        assert other.listing_startup_report['candidate_rows'] == 0
        assert other.maintain_listings() == {}
    finally:
        other.close()


async def test_sources_not_due_do_not_repeat_maintenance(config, db, clock, monkeypatch):
    config.data['github_sources'] = [{'url':'https://github.com/fixture/offline'}]
    from autoapply.models import now
    db.execute('INSERT INTO source_revisions(source,checked_at) VALUES (?,?)',
               (config['github_sources'][0]['url'],now()))
    update = Mock(side_effect=AssertionError('Source poll is not due'))
    monkeypatch.setattr('autoapply.sources.GitHubRepositorySource.update',update)
    db.cleanup_stale_listings(reference=NOW)
    cleanup = Mock(wraps=db.cleanup_stale_listings)
    refresh = Mock(wraps=db._refresh_listing_statistics)
    monkeypatch.setattr(db,'cleanup_stale_listings',cleanup)
    monkeypatch.setattr(db,'_refresh_listing_statistics',refresh)
    engine = Engine(config,db,browser=Mock())
    for seconds in (0,2,4,299,300,302):
        clock[0] = NOW+timedelta(seconds=seconds)
        assert (await engine.scan())['sources'] == 0
        assert db.claim() is None
    assert cleanup.call_count == 1
    refresh.assert_not_called()
    update.assert_not_called()


@pytest.mark.parametrize('offset,state', [(1,'FRESH'),(0,'FRESH'),(-1,'STALE')])
def test_deadline_exact_cutoff(db, offset, state):
    seed(db,1,posted=NOW-window(30)+timedelta(microseconds=offset))
    job = db.one('SELECT * FROM jobs')
    assert job['freshness_state'] == state
    if state == 'FRESH':
        deadline = utc(db.one('SELECT due_at FROM listing_maintenance')['due_at'])
        assert db.cleanup_stale_listings(reference=deadline-timedelta(microseconds=1))['candidate_rows'] == 0
        assert db.cleanup_stale_listings(reference=deadline)['changed'] == 1
        assert db.one('SELECT freshness_state FROM jobs')['freshness_state'] == 'STALE'


@pytest.mark.parametrize('posted', [None,'invalid','2027-01-01T00:00:00+00:00'])
def test_unknown_invalid_future_evidence(db, posted):
    seed(db,1)
    db.execute('UPDATE jobs SET posted_at=? WHERE id=1',(posted,))
    db.cleanup_stale_listings(reference=NOW)
    job = db.one('SELECT * FROM jobs')
    assert job['freshness_state'] == 'UNKNOWN_DATE' and not job['listing_active']
    assert job['posted_at'] == (posted if posted and posted.startswith('2027') else None)
    if job['posted_at']:
        assert db.maintain_listings(reference=utc(posted))['changed'] == 1
        assert db.one('SELECT listing_status FROM jobs')['listing_status'] == 'ACTIVE'
    else:
        assert db.cleanup_stale_listings(reference=NOW+timedelta(days=60))['candidate_rows'] == 0


def test_non_posting_evidence_does_not_create_deadline(config, db, listing):
    date = extract_posting_date(html='<p>Updated today</p>',reference=NOW)
    db.ingest(replace(listing,posted_at=date.posted_at),config)
    db.cleanup_stale_listings(reference=NOW)
    assert not db.rows('SELECT * FROM jobs')
    assert not db.rows('SELECT * FROM listing_maintenance')
    assert db.one('SELECT posted_at,freshness_state FROM listing_observations') == dict(posted_at=None,freshness_state='UNKNOWN_DATE')


def test_age_window_invalidates_schedule_and_preserves_closure(db):
    seed(db,2,posted=NOW-timedelta(days=20))
    db.mark_listing_closed(2,'REMOVED')
    db.set_setting('listing_max_age_days',14)
    assert db.maintain_listings(reference=NOW)['candidate_rows'] == 2
    assert db.one('SELECT listing_status FROM jobs WHERE id=1')['listing_status'] == 'STALE'
    db.set_setting('listing_max_age_days',30)
    assert db.maintain_listings(reference=NOW)['candidate_rows'] == 2
    assert db.one('SELECT listing_status FROM jobs WHERE id=1')['listing_status'] == 'ACTIVE'
    assert db.one('SELECT listing_status,listing_active FROM jobs WHERE id=2') == dict(listing_status='REMOVED',listing_active=0)
    with pytest.raises(ValueError):
        db.set_setting('listing_max_age_days',31)


def test_cross_connection_edit_and_rollback_are_durable(config,db):
    seed(db,2)
    other = sqlite3.connect(config.private/'test.sqlite3')
    try:
        other.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
        other.rollback()
        assert db.maintain_listings(reference=NOW) == {}
        other.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
        other.commit()
        assert db.maintain_listings(reference=NOW)['candidate_rows'] == 1
        assert db.one('SELECT listing_active FROM jobs WHERE id=1')['listing_active'] == 0
    finally:
        other.close()


def test_dirty_batch_refreshes_statistics_once(config, db, listing, monkeypatch):
    refresh = Mock(wraps=db._refresh_listing_statistics)
    monkeypatch.setattr(db,'_refresh_listing_statistics',refresh)
    with db.ingest_batch():
        db.ingest(listing,config)
        db.ingest(replace(listing,source='second'),config)
    assert refresh.call_count == 1
    db.cleanup_stale_listings()
    db.refresh_listing_statistics()
    assert refresh.call_count == 1
    observation = db.one('SELECT * FROM listing_observations')
    assert observation['duplicates_skipped'] == 1
    assert len(db.rows('SELECT * FROM job_sources')) == 2
    assert db.listing_statistics()['duplicates_skipped'] == 1
    # Expiry changes both listing and observation; the existing history flush
    # incorporates the new statistics without a second history aggregate pass.
    history_stats = Mock(wraps=db.history._statistics)
    monkeypatch.setattr(db.history,'_statistics',history_stats)
    db.cleanup_stale_listings(reference=utc(listing.posted_at)+window(30)+timedelta(microseconds=1))
    assert refresh.call_count == 2
    assert history_stats.call_count == 1


def test_status_queue_pending_are_read_only(config, db, listing, monkeypatch):
    db.ingest(listing,config)
    db.cleanup_stale_listings()
    db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
    controller = Controller(config,db)
    cleanup = Mock(side_effect=AssertionError('Display cannot clean listings'))
    monkeypatch.setattr(db,'cleanup_stale_listings',cleanup)
    before = db.conn.total_changes
    path = config.private/'listing_statistics.json'
    contents, modified = path.read_bytes(),path.stat().st_mtime_ns
    for _ in range(3):
        status = json.loads(controller.command('status'))
        assert status['listing_stats']['discovered_total'] == 1
        assert status['listing_statistics_as_of']
        assert controller.command('queue') == 'No applications.'
        assert controller.pending() == []
    assert before == db.conn.total_changes
    assert path.read_bytes() == contents and path.stat().st_mtime_ns == modified


@pytest.mark.parametrize('protection', ['submitted','intent','manual','retired'])
def test_cleanup_preserves_protected_history_and_target(config,db,listing,protection):
    db.ingest(listing,config)
    if protection == 'submitted':
        db.transition(1,State.SUBMITTED,confirmation_text='Synthetic receipt',submitted_at=NOW.isoformat())
    elif protection == 'intent':
        db.transition(1,State.SUBMITTING,submit_intent_at=NOW.isoformat())
    elif protection == 'manual':
        db.transition(1,State.MANUAL_REVIEW,'Synthetic hold')
        db.update_security(1,manual_action_required=1,retry_allowed=0)
    else:
        db.set_setting('duplicate_submission_guard:1',{'permanent':True})
    db.set_setting('controlled_application_id',1)
    before = db.one('SELECT * FROM applications')
    history = db.history.get_application(1)
    events = db.rows('SELECT * FROM events')
    db.execute('UPDATE jobs SET posted_at=? WHERE id=1',('2020-01-01',))
    assert db.maintain_listings() == {}
    db.cleanup_stale_listings()
    assert db.one('SELECT * FROM applications') == before
    assert db.rows('SELECT * FROM events') == events
    for key in ('status_history','confirmation_text','submitted_at','application_state','submit_intent_at'):
        assert db.history.get_application(1).get(key) == history.get(key)
    assert db.setting('controlled_application_id') == 1
    if protection == 'retired':
        assert db.automation_retired(1) and db.claim(1) is None


def test_claim_never_trusts_cached_active(config,db,listing,monkeypatch):
    db.ingest(listing,config)
    db.cleanup_stale_listings()
    monkeypatch.setattr(db,'maintain_listings',lambda: {})
    db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
    assert db.application(1)['listing_active'] == 1
    assert db.claim() is None
    assert db.application(1)['attempts'] == 0


async def test_attempt_start_rechecks_after_claim(config,db,listing,monkeypatch):
    db.ingest(listing,config)
    claim = db.claim
    def changed_after_claim(*args):
        result = claim(*args)
        db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
        return result
    monkeypatch.setattr(db,'claim',changed_after_claim)
    browser = Mock()
    browser.new_page = AsyncMock(side_effect=AssertionError('Stale attempt cannot open a page'))
    engine = Engine(config,db,browser)
    assert await engine.process_one()
    browser.new_page.assert_not_called()
    assert db.application(1)['status'] == 'INVALID'


@pytest.mark.parametrize('sql,args,table,index', [
    ('SELECT * FROM jobs WHERE identity_key=? OR canonical_url=?',('fixture:1','https://example.test/1'),'jobs','jobs_canonical_url'),
    ('SELECT * FROM events WHERE application_id=? ORDER BY id',(1,),'events','events_application'),
    ('SELECT * FROM job_sources WHERE job_id=?',(1,),'job_sources','job_sources_job'),
    ('SELECT * FROM listing_maintenance WHERE due_at<=?',(NOW.isoformat(),),'listing_maintenance','listing_maintenance_due'),
])
def test_proven_query_plans(db,sql,args,table,index):
    plan = ' '.join(r['detail'] for r in db.rows('EXPLAIN QUERY PLAN '+sql,args))
    assert f'SCAN {table}' not in plan
    assert index in plan
    assert 'USE TEMP B-TREE' not in plan
    if table == 'jobs':
        assert 'MULTI-INDEX OR' in plan
    if table == 'events':
        # INTEGER PRIMARY KEY id is the rowid suffix of the application index.
        assert [r['name'] for r in db.rows('PRAGMA index_info(events_application)')] == ['application_id']


async def test_idle_worker_uses_shared_schedule(config,db,clock,monkeypatch):
    config.data['github_sources'] = []
    db.cleanup_stale_listings(reference=NOW)
    cleanup = Mock(wraps=db.cleanup_stale_listings)
    monkeypatch.setattr(db,'cleanup_stale_listings',cleanup)
    monkeypatch.setattr(db,'recover',lambda: None)
    engine = Engine(config,db,browser=Mock())
    engine.close = AsyncMock()
    engine.service_manual_requests = AsyncMock()
    async def no_application():
        assert not db.conn.in_transaction
        assert db.claim() is None
    engine.process_one = no_application
    ticks = iter([2,4,300,302,None])
    async def wake(awaitable,timeout):
        awaitable.close()
        tick = next(ticks)
        if tick is None:
            engine.stop_event.set()
            return
        clock[0] = NOW+timedelta(seconds=tick)
        raise TimeoutError
    monkeypatch.setattr('autoapply.engine.asyncio.wait_for',wake)
    await engine.run()
    assert cleanup.call_count == 1
    assert engine.service_manual_requests.await_count == 5
    engine.close.assert_awaited_once()


def test_pending_manual_evidence_survives_stale_display(config,db,listing):
    from autoapply.models import Question
    db.ingest(listing,config)
    db.transition(1,State.MANUAL_REVIEW,'Synthetic hold')
    db.update_security(1,manual_action_required=1)
    db.question(1,Question('fact','Unknown fact',required=True),'Unknown')
    db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
    before = db.conn.total_changes
    assert len(Controller(config,db).pending()) == 1
    assert db.conn.total_changes == before


def test_queue_limit_applies_after_freshness_filter(config,db,listing):
    with db.ingest_batch():
        for n in range(22):
            db.ingest(replace(listing,url=f'https://example.test/{n}'),config)
    with db.transaction():
        db.execute("UPDATE jobs SET posted_at=NULL WHERE id<=21")
        db.execute("UPDATE applications SET updated_at='2099-01-01' WHERE id<=21")
    assert '#22 ' in Controller(config,db).command('queue')


def test_maintenance_failure_does_not_acknowledge_work(db,monkeypatch):
    seed(db,1)
    db.execute('UPDATE jobs SET posted_at=NULL WHERE id=1')
    with monkeypatch.context() as patcher:
        patcher.setattr(db,'_refresh_listing_statistics',Mock(side_effect=RuntimeError('interrupted')))
        with pytest.raises(RuntimeError,match='interrupted'):
            db.cleanup_stale_listings(reference=NOW)
    assert db.one('SELECT due_at FROM listing_maintenance')['due_at'] == ''
    assert db.one('SELECT listing_active FROM jobs')['listing_active'] == 1
    assert db.cleanup_stale_listings(reference=NOW)['changed'] == 1


def test_query_indexes_installed_on_existing_database(config,db):
    for name in ('jobs_canonical_url','events_application','job_sources_job'):
        db.execute('DROP INDEX '+name)
    other = Database(config.private/'test.sqlite3',startup_maintenance=False)
    try:
        names = {r['name'] for r in other.rows("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'jobs_canonical_url','events_application','job_sources_job'} <= names
    finally:
        other.close()
