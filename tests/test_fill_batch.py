import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from autoapply.fill_batch import discover, BatchReport, MAX_ACTIVE_APPLICATION_TABS, verify_safety, checkpoint
from autoapply.browser import Browser
from autoapply.engine import Engine
from autoapply.runtime import ProcessLock
from scripts.fill_only_batch import FillDatabase, forbidden_submit

@pytest.mark.parametrize('count',[57,100])
async def test_discovery_never_opens_tabs(config,db,listing,count):
    for i in range(count):
        db.ingest(replace(listing,url=f'https://jobs.lever.co/test/{i}'),config)
    browser=SimpleNamespace(new_page=AsyncMock())
    engine=Engine(config,db,browser,fill_only=True)
    assert len(await engine.scan())==count
    browser.new_page.assert_not_called()

async def test_one_open_and_second_refused(config):
    browser=Browser(config)
    pages=[]
    async def new_page():
        page=SimpleNamespace(on=lambda *a:None)
        pages.append(page)
        return page
    browser.context=SimpleNamespace(pages=pages,new_page=new_page)
    browser.start=AsyncMock(); browser.ensure_desktop=AsyncMock(); browser.observe=lambda p:None
    browser.max_active_application_tabs=MAX_ACTIVE_APPLICATION_TABS
    # Test the tab boundary directly, independent of cursor/browser internals.
    browser.check_tab_limit(opening=True)
    await new_page()
    assert len(pages)==1
    with pytest.raises(RuntimeError,match='BATCH_TAB_LIMIT_VIOLATION'):
        await browser.new_page()
    assert len(pages)==1

@pytest.mark.parametrize('count',[57,100])
def test_sequential_release_prevents_accumulation(config,count):
    browser=Browser(config); browser.max_active_application_tabs=MAX_ACTIVE_APPLICATION_TABS
    browser.context=SimpleNamespace(pages=[])
    for i in range(count):
        browser.check_tab_limit(opening=True)
        browser.context.pages.append(i)
        browser.check_tab_limit()
        browser.context.pages.clear()
    assert browser.max_tabs_observed==1

@pytest.mark.parametrize('key,value',[('auto_submit',True),('controlled_application_id',4571)])
def test_preflight_rejects_unsafe_state(db,key,value):
    db.set_setting('auto_submit',False)
    db.set_setting(key,value)
    with pytest.raises(RuntimeError): verify_safety(db)

def test_commands_block_preflight(config,db,listing):
    db.ingest(listing,config); db.set_setting('auto_submit',False)
    db.execute("INSERT INTO manual_requests VALUES (1,'submit','now')")
    with pytest.raises(RuntimeError,match='Pending manual'): verify_safety(db)

async def test_submit_forbidden_and_setting_enforced(config):
    db=FillDatabase(config.private/'fill.db',startup_maintenance=False)
    try:
        db.set_setting('auto_submit',False)
        with pytest.raises(RuntimeError): db.set_setting('auto_submit',True)
        with pytest.raises(RuntimeError): db.transition(1,'SUBMITTING')
        with pytest.raises(RuntimeError): await forbidden_submit()
        assert db.setting('auto_submit') is False
    finally: db.close()

def test_current_report_replaces_stale_worker(tmp_path):
    path=tmp_path/'report.json'
    path.write_text(json.dumps({'worker_pid':4571,'auto_submit':True,'blocker':4571}))
    report=BatchReport(path); report.save(result='PREFLIGHT_PASSED')
    actual=json.loads(path.read_text())
    assert actual['worker_pid']!=4571 and actual['auto_submit'] is False
    assert 'blocker' not in actual
    assert actual['batch_run_id']
    assert json.loads((tmp_path/'fill-only-batch-previous.json').read_text())['auto_submit'] is True

def test_lock_releases_on_failure(tmp_path):
    path=tmp_path/'worker.lock'
    with pytest.raises(ValueError):
        with ProcessLock(path): raise ValueError('batch failure')
    with ProcessLock(path): pass

async def test_checkpoint_written_before_release(config,db,listing):
    db.ingest(listing,config)
    fields=SimpleNamespace(evaluate_all=AsyncMock(return_value=[{'name':'first','value':'Test'}]))
    frame=SimpleNamespace(url=listing.url,locator=lambda s:fields)
    page=SimpleNamespace(url=listing.url,frames=[frame])
    engine=SimpleNamespace(db=db,config=config)
    await checkpoint(engine,1,page)
    saved=json.loads((config.private/'fill-only-checkpoints/1.json').read_text())
    assert saved['frames'][0]['fields'][0]['value']=='Test'
    assert db.one("SELECT id FROM events WHERE kind='FILL_ONLY_CHECKPOINT'")

def test_protected_and_implicit_claims_forbidden(config):
    db=FillDatabase(config.private/'protected.db',startup_maintenance=False)
    try:
        for app_id in (None,4571,4535,6401,5310,6415,6416):
            with pytest.raises(RuntimeError): db.claim(app_id)
        with pytest.raises(RuntimeError): db.update_security(4571,session_preserved=0)
    finally: db.close()


def test_t068_cold_queue_720_matches_full_history_and_uncached_policy(config, listing, monkeypatch):
    """Reproducible offline benchmark; setup/export is outside measured GETs."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from time import perf_counter
    from autoapply.archive import ApplicationHistory, protected_record
    from autoapply.dashboard import Dashboard
    from autoapply.database import Database
    from test_candidate_policy import heterogeneous_candidates, clear_text_caches, uncached_policy

    path = config.private / 'queue-benchmark.sqlite3'
    db = Database(path, startup_maintenance=False, history_maintenance=False)
    reference = datetime.now(timezone.utc)
    candidates = list(heterogeneous_candidates(reference))
    try:
        with db.transaction():
            for candidate in candidates:
                db.ingest(replace(listing, company=candidate['company'], title=candidate['title'],
                                  description=candidate['description'], location=candidate['location'],
                                  url=candidate['canonical_url'], posted_at=candidate['posted_at']), config)
            # Exercise the independent SQL guards as well as text eligibility.
            for app_id, field, value in [(5, 'retry_allowed', 0), (9, 'manual_action_required', 1),
                                         (13, 'security_state', 'CAPTCHA_DETECTED'),
                                         (17, 'retry_at', (reference + timedelta(days=1)).isoformat()),
                                         (21, 'submit_intent_at', reference.isoformat()),
                                         (25, 'submission_confirmation_seen', 1)]:
                db.execute(f'UPDATE applications SET {field}=? WHERE id=?', (value, app_id))
            db.set_setting('duplicate_submission_guard:29', True)
            db.set_setting('user_not_eligible:33', True)
        writer = db.history
        with writer.locked():
            writer._invalidate_readers()
            for i in range(960):
                candidate = candidates[i % len(candidates)]
                protected = i % 23 == 0
                writer._write(dict(application_id=f'legacy-{i}', company=candidate['company'],
                                   title=candidate['title'], canonical_url=candidate['canonical_url'],
                                   description=candidate['description'],
                                   application_state='UNKNOWN' if protected else 'DISCOVERED'))
            writer._statistics()

        original_read = Path.read_text
        reads = []
        def read(path, *args, **kwargs):
            if path.name == 'application.json':
                reads.append(path)
            return original_read(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', read)
        def forbidden(*args, **kwargs):
            raise AssertionError('Queue GET attempted maintenance or live I/O')
        monkeypatch.setattr(ApplicationHistory, 'validate', forbidden)
        monkeypatch.setattr(ApplicationHistory, 'sync', forbidden)
        monkeypatch.setattr('autoapply.dashboard.scan', forbidden)
        monkeypatch.setattr('autoapply.liveness.listing_state', forbidden)
        monkeypatch.setattr('autoapply.descriptions.fetch_description', forbidden)
        db.history = ApplicationHistory(writer.root)
        value = Dashboard(config, db)
        try:
            before = {p: p.read_bytes() for p in writer.root.rglob('application.json')}
            clear_text_caches()
            start = perf_counter()
            actual = [r['application_id'] for r in value.priority()['rows']]
            optimized_seconds = perf_counter() - start
            assert reads == [] and actual
            clear_text_caches()
            with monkeypatch.context() as patch:
                uncached_policy(patch)
                patch.setattr(ApplicationHistory, 'list_protected_applications',
                              lambda self: [r for r in self.list_applications() if protected_record(r)])
                with Database.discovery_reader(path, writer.root) as baseline_db:
                    baseline = Dashboard(config, baseline_db)
                    try:
                        start = perf_counter()
                        expected = [r['application_id'] for r in baseline.priority()['rows']]
                        baseline_seconds = perf_counter() - start
                    finally:
                        baseline.close()
            assert actual == expected and len(reads) == 960
            assert {p: p.read_bytes() for p in writer.root.rglob('application.json')} == before
            print(f'T068 Queue cold 720/960 history: optimized={optimized_seconds:.4f}s '
                  f'baseline={baseline_seconds:.4f}s record_reads=0/960 returned={len(actual)}')

            def compare_fresh_baseline():
                actual = [r['application_id'] for r in value.priority()['rows']]
                with monkeypatch.context() as patch:
                    uncached_policy(patch)
                    patch.setattr(ApplicationHistory, 'list_protected_applications',
                                  lambda self: [r for r in self.list_applications() if protected_record(r)])
                    with Database.discovery_reader(path, writer.root) as baseline_db:
                        assert actual == discover(baseline_db, config)
                return actual

            # A different connection changes DB data_version. Re-evaluate content,
            # ordering, protected state, and independent manual/settings guards.
            target = actual[0]
            db.execute('UPDATE jobs SET description=?,priority=9999 WHERE id=(SELECT job_id FROM applications WHERE id=?)',
                       ('Required qualifications\nMust be a US citizen.', target))
            assert compare_fresh_baseline()[0] == target
            db.set_setting('verified_fact:citizenship.us_citizen', dict(value=False, source='USER_PROVIDED'))
            assert target not in compare_fresh_baseline()
            db.set_setting('verified_fact:citizenship.us_citizen', dict(value=True, source='USER_PROVIDED'))
            db.set_setting(f'user_not_eligible:{target}', True)
            assert target not in compare_fresh_baseline()
            db.set_setting(f'user_not_eligible:{target}', False)
            db.execute('UPDATE applications SET submit_intent_at=? WHERE id=?', (reference.isoformat(), target))
            assert target not in compare_fresh_baseline()
            db.execute('UPDATE applications SET submit_intent_at=NULL WHERE id=?', (target,))
            assert target in compare_fresh_baseline()
            # New protected legacy identity is published through a separate reader.
            with writer.locked():
                writer._invalidate_readers()
                writer._write(dict(application_id='new-protection', application_state='MANUAL_REQUIRED',
                                   canonical_url=db.application(target)['canonical_url']))
                writer._statistics()
            reads.clear()
            assert target not in [r['application_id'] for r in value.priority()['rows']]
            assert reads == []
            compare_fresh_baseline()
            # Profile file and age settings invalidate Queue's existing cache.
            profile = config.private / 'profile.yaml'
            profile.write_text(profile.read_text(encoding='utf-8').replace('us_authorized: true', 'us_authorized: false'), encoding='utf-8')
            compare_fresh_baseline()
            config.data['jobs']['max_listing_age_days'] = 1
            compare_fresh_baseline()
        finally:
            value.close()
    finally:
        db.close()
