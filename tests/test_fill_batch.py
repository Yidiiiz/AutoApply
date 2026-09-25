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
