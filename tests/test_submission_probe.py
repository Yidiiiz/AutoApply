import json
import asyncio

import pytest

from autoapply.cursor import click_element
from autoapply.engine import Engine
from autoapply.models import State, now
from autoapply.submission_probe import SubmissionProbe
from autoapply.submission_probe import SubmitObstructed
from test_browser import browser, site, prepare

pytestmark = pytest.mark.browser


async def make_probe(config,db,browser,listing):
    db.ingest(listing,config)
    db.claim(1)
    page=await browser.new_page()
    await page.set_content('<button type="button">Submit application</button>')
    probe=SubmissionProbe(db,1,page,page.get_by_role('button'))
    await probe.prepare()
    await probe.arm()
    db.transition(1,State.SUBMITTING,submit_intent_at=now())
    probe.intent()
    return page,probe


async def test_silent_button_distinguishes_delivery_from_effect(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    probe.click_started()
    await click_element(page,page.get_by_role('button'))
    await probe.click_returned()
    await Engine(config,db,browser).post_submit(1,page,wait=False,probe=probe)
    await probe.finish()
    assert probe.data['intent_created'] and probe.data['click_call_returned']
    assert probe.data['delivered'] and not probe.has_effect
    assert [e['type'] for e in probe.data['events']]==['mousedown','mouseup','click']
    assert db.application(1)['error_category']=='SUBMIT_CLICK_NOT_DELIVERED'
    assert not db.application(1)['retry_allowed']


async def test_undelivered_click_is_not_unknown(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    probe.click_started()  # Simulate a transport call returning without input.
    await probe.click_returned()
    await Engine(config,db,browser).post_submit(1,page,wait=False,probe=probe)
    await probe.finish()
    assert not probe.data['delivered'] and not probe.has_effect
    assert db.application(1)['error_category']=='SUBMIT_CLICK_NOT_DELIVERED'


async def test_effect_without_confirmation_remains_unknown(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    await page.get_by_role('button').evaluate("e=>e.onclick=()=>{e.disabled=true;e.textContent='Sending'}")
    probe.click_started()
    await click_element(page,page.get_by_role('button'))
    await probe.click_returned()
    await Engine(config,db,browser).post_submit(1,page,wait=False,probe=probe)
    await probe.finish()
    assert probe.data['delivered'] and probe.has_effect
    assert db.application(1)['error_category']=='SUBMISSION_UNKNOWN'


async def test_production_flow_records_one_click_request_and_confirmation(config,db,browser,listing,site):
    engine=await prepare(config,db,browser,listing,site)
    await engine.resume_manual(1)
    key=db.setting('latest_submit_probe:1')
    data=db.setting('submit_probe:1:'+key)
    assert data['intent_created'] and data['click_call_executed'] and data['click_call_returned']
    assert data['delivered'] and data['confirmation_observed']
    assert len([e for e in data['events'] if e['type']=='click' and e['reaches_button']])==1
    assert len(site[1])==1
    assert any(r['method']=='POST' and r['status']==200 for r in data['network'])
    assert 'student@example.test' not in json.dumps(data)
    assert db.application(1)['status']=='SUBMITTED'


async def test_confirmed_result_persists_before_post_click_screenshot_error(config,db,browser,listing,site,monkeypatch):
    from playwright.async_api import Error
    engine = await prepare(config,db,browser,listing,site)
    original_click = SubmissionProbe.physical_click
    original_screenshot = browser.screenshot

    async def click_and_wait(probe):
        await original_click(probe)
        await probe.page.get_by_text('Thank you for applying', exact=False).wait_for()

    async def screenshot(page,folder,name):
        if name.startswith('post-click-'):
            raise Error('Detached screenshot frame')
        return await original_screenshot(page,folder,name)

    monkeypatch.setattr(SubmissionProbe,'physical_click',click_and_wait)
    monkeypatch.setattr(browser,'screenshot',screenshot)
    await engine.resume_manual(1)
    app = db.application(1)
    assert app['status']=='SUBMITTED' and app['submission_confirmation_seen']
    assert not app['retry_allowed'] and len(site[1])==1
    event = db.one("SELECT detail FROM events WHERE kind='POST_SUBMIT_EXCEPTION'")
    assert json.loads(event['detail'])['confirmation_persisted']
    archive = list((config.private/'application_history'/'submitted').glob('*/application.json'))
    assert len(archive)==1
    assert json.loads(archive[0].read_text())['status']=='SUBMITTED'


async def test_user_reconciliation_preserves_prior_intent(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    original=db.application(1)['submit_intent_at']
    db.transition(1,State.MANUAL_REVIEW,'Outcome unknown')
    engine=Engine(config,db,browser)
    engine.control.reconcile(1,False,'User inspected the form and employer history; no submission')
    assert db.application(1)['submit_intent_at'] is None
    event=db.rows("SELECT * FROM events WHERE kind='submission_intent_reconciled'")[0]
    assert json.loads(event['detail'])['prior_submit_intent_at']==original
    await probe.finish()


async def test_production_silent_button_does_not_count_screenshot_as_effect(config,db,browser,listing,site):
    engine=await prepare(config,db,browser,listing,site)
    config.data['application']['confirmation_timeout_seconds']=1
    page=engine.handoff.pages[1]
    await page.get_by_role('button',name='Submit application').evaluate("e=>e.type='button'")
    await engine.resume_manual(1)
    key=db.setting('latest_submit_probe:1')
    data=db.setting('submit_probe:1:'+key)
    assert data['delivered'] and not data['network']
    assert db.application(1)['error_category']=='SUBMIT_CLICK_NOT_DELIVERED',data
    assert not site[1]


async def test_upload_ready_waits_for_network_and_replace_control(browser):
    page=await browser.new_page()
    await page.set_content('<div><input type=file><button disabled>Replace</button></div>')
    field=page.locator('input')
    page._autoapply_uploads.select()
    await field.set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'x'})
    browser.observation(page)['pending_uploads'][123] = True
    task=asyncio.create_task(browser.uploads_ready(page,{'resume':(field,'resume.pdf',1)},3))
    await asyncio.sleep(.3)
    assert not task.done()
    await page.locator('button').evaluate('e=>e.disabled=false')
    await asyncio.sleep(.3)
    assert not task.done()
    browser.observation(page)['pending_uploads'].clear()
    assert await task


async def test_upload_never_finishes_returns_not_ready(browser):
    page=await browser.new_page()
    await page.set_content('<div><input type=file><button disabled>Replace</button></div>')
    assert not await browser.uploads_ready(page,{'resume':(page.locator('input'),'resume.pdf',1)},.3)


@pytest.mark.parametrize('descendant', [False, True])
async def test_physical_center_click_and_single_call(config,db,browser,listing,descendant):
    page,probe=await make_probe(config,db,browser,listing)
    if descendant:
        await probe.button.evaluate("e=>e.innerHTML='<span>Submit application</span>'")
    await probe.physical_click()
    await probe.finish()
    events=[e for e in probe.data['events'] if e.get('reaches_button')]
    assert [e['type'] for e in events]==['mousedown','mouseup','click']
    assert all(e['trusted'] for e in events)
    assert events[-1]['target']['tag']==('SPAN' if descendant else 'BUTTON')
    kinds=[r['kind'] for r in db.rows('SELECT * FROM events')]
    for kind in ['SUBMIT_MOUSE_MOVE_STARTED','SUBMIT_MOUSE_MOVE_COMPLETED','SUBMIT_BUTTON_UNOBSTRUCTED',
                 'SUBMIT_MOUSEDOWN_OBSERVED','SUBMIT_MOUSEUP_OBSERVED','SUBMIT_CLICK_OBSERVED']:
        assert kind in kinds
    with pytest.raises(RuntimeError,match='already attempted'):
        await probe.physical_click()


async def test_movement_after_prepare_uses_fresh_box(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    await probe.button.evaluate("e=>e.style.marginLeft='200px'")
    await probe.physical_click()
    await probe.finish()
    assert probe.data['pre_click']['bounding_box']['x'] > probe.data['before']['bounding_box']['x']+150
    assert probe.data['delivered']


@pytest.mark.parametrize('change',['overlay','moved'])
async def test_overlay_or_moving_button_prevents_click(config,db,browser,listing,monkeypatch,change):
    page,probe=await make_probe(config,db,browser,listing)
    original=page.mouse.move
    async def move(x,y,**kwargs):
        await original(x,y,**kwargs)
        if change=='overlay':
            await page.locator('body').evaluate("e=>e.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:9999\"></div>')")
        else:
            await probe.button.evaluate("e=>e.style.marginLeft='200px'")
    monkeypatch.setattr(page.mouse,'move',move)
    with pytest.raises(SubmitObstructed):
        await probe.physical_click()
    assert not probe.data['click_call_executed']
    assert not probe.data['delivered']
    await probe.finish()


async def test_visible_upload_warning_blocks_readiness(browser):
    page=await browser.new_page()
    await page.set_content('<div role=status>Uploading resume</div><input type=file>')
    page._autoapply_uploads.select()
    await page.locator('input').set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'x'})
    controls={'resume':(page.locator('input'),'resume.pdf',1)}
    assert not await browser.uploads_ready(page,controls,.3)
    await page.locator('[role=status]').evaluate("e=>e.textContent='Upload complete'")
    assert await browser.uploads_ready(page,controls,2)


async def test_failed_upload_is_not_successful_completion(browser):
    page=await browser.new_page()
    await browser.context.route('https://upload-fixture.test/**',
        lambda route: route.fulfill(status=500 if '/upload' in route.request.url else 200,
                                   content_type='text/html',body='<input type=file>'))
    await page.goto('https://upload-fixture.test/form')
    await page.evaluate("async()=>await fetch('/upload',{method:'POST',body:'fixture'})")
    await asyncio.sleep(.1)
    assert browser.observation(page)['upload_failed']
    assert not await browser.uploads_ready(page,{'resume':(page.locator('input'),'resume.pdf',1)},1)


async def test_pending_upload_blocks_production_ready_state(config,db,browser,listing,site,monkeypatch):
    from unittest.mock import AsyncMock
    engine=await prepare(config,db,browser,listing,site)
    monkeypatch.setattr(browser,'uploads_ready',AsyncMock(return_value=False))
    await engine.resume_manual(1)
    app=db.application(1)
    assert not app['submit_intent_at'] and not site[1]
    assert app['error_category']=='UPLOAD_PENDING'
    assert db.setting('latest_submit_probe:1') is None


async def test_ashby_upload_lifecycle_waits_for_metadata_and_ui(browser):
    release = asyncio.Event()
    started = asyncio.Event()
    async def route(request):
        if 'non-user-graphql' in request.request.url:
            started.set()
            await release.wait()
            await request.fulfill(json={'data':{'setFormValueToFile':{'id':'fixture'}}})
        else:
            await request.fulfill(content_type='text/html', body='''<div><input type=file>
              <span>resume.pdf</span><button disabled>Replace</button></div>''')
    await browser.context.route('https://jobs.ashbyhq.com/**', route)
    page=await browser.new_page()
    await page.goto('https://jobs.ashbyhq.com/fixture/application')
    field=page.locator('input')
    page._autoapply_uploads.select()
    await field.set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'x'})
    await page.evaluate("() => {fetch('/api/non-user-graphql?op=ApiSetFormValueToFile',{method:'POST'});}")
    await asyncio.wait_for(started.wait(),3)
    task=asyncio.create_task(browser.uploads_ready(page,{'resume':(field,'resume.pdf',1)},5))
    await asyncio.sleep(.2)
    assert not task.done()
    release.set()
    await page.wait_for_timeout(200)
    assert not task.done()  # disabled Replace still blocks a completed upload
    await page.locator('button').evaluate('e=>e.disabled=false')
    assert await task
    assert browser.observation(page)['upload_result']['metadata_confirmed']
    await field.set_input_files([])
    assert not await browser.uploads_ready(page,{'resume':(field,'resume.pdf',1)},1)
    assert browser.observation(page)['upload_result']['category']=='UPLOAD_FAILED'


async def test_upload_race_never_reclicks(config,db,browser,listing):
    page,probe=await make_probe(config,db,browser,listing)
    await probe.button.evaluate("e=>e.onclick=()=>e.insertAdjacentHTML('afterend', '<div role=alert>We are updating your application (e.g. uploading files), please try again when they are finished.</div>')")
    await probe.physical_click()
    await Engine(config,db,browser).post_submit(1,page,wait=False,probe=probe)
    await probe.finish()
    assert db.application(1)['error_category']=='UPLOAD_RACE_DETECTED'
    assert not db.application(1)['retry_allowed']
    assert sum(e['type']=='click' for e in probe.data['events'])==1


@pytest.mark.parametrize('confirm',[False, True])
async def test_ashby_http200_confirmation_and_no_locator_fallback(config,db,browser,listing,monkeypatch,confirm):
    await browser.context.route('https://jobs.ashbyhq.com/**',
        lambda route: route.fulfill(json={'data':{'submitted':True}}) if 'graphql' in route.request.url
        else route.fulfill(content_type='text/html',body='<button>Submit application</button>'))
    db.ingest(listing,config);db.claim(1)
    page=await browser.new_page();await page.goto('https://jobs.ashbyhq.com/fixture/application')
    button=page.get_by_role('button')
    await button.evaluate('''(e, confirm)=>e.onclick=async()=>{
      await fetch('/api/non-user-graphql?op=ApiSubmitSingleApplicationFormAction',{method:'POST'});
      if(confirm)document.body.innerHTML="Your application was successfully submitted. We'll contact you if there are next steps.";
    }''',confirm)
    probe=SubmissionProbe(db,1,page,button);await probe.prepare();await probe.arm()
    db.transition(1,State.SUBMITTING,submit_intent_at=now());probe.intent()
    async def forbidden(*args,**kwargs):
        raise AssertionError('No locator-click fallback is allowed')
    monkeypatch.setattr(type(button),'click',forbidden)
    await probe.physical_click()
    await page.wait_for_timeout(200)
    await Engine(config,db,browser).post_submit(1,page,wait=False,probe=probe)
    await probe.finish()
    assert any(r['operation']=='ApiSubmitSingleApplicationFormAction' and r['status']==200 for r in probe.data['network'])
    assert bool(db.application(1)['submission_confirmation_seen'])==confirm
    assert sum(e['type']=='click' for e in probe.data['events'])==1
    assert not db.application(1)['retry_allowed']
    with pytest.raises(RuntimeError,match='already attempted'):
        await probe.physical_click()
