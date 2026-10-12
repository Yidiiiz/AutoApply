import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest

from autoapply.semi_auto import SemiAutoLoop, ReviewGuard
from autoapply.engine import Engine
from autoapply.models import Question
from autoapply.control import Controller

def setup_app(config,db,listing):
    db.ingest(listing,config); db.set_setting('auto_submit',False); db.set_setting('paused',False)
    return db.application(1)

def page(): return Mock(is_closed=Mock(return_value=False),bring_to_front=AsyncMock())

@pytest.mark.parametrize('mode',['ordinary','unpreserved','existing'])
def test_loop_requires_fresh_fill_only_owner(config,db,mode):
    if mode=='ordinary': engine=Engine(config,db)
    elif mode=='unpreserved': engine=Engine(config,db,fill_only=True)
    else:
        engine=Engine(config,db,fill_only=True,preserve_fill_session=True,semi_auto=True)
        engine.browser.context=object()
    with pytest.raises(ValueError,match='fresh dedicated'): SemiAutoLoop(config,db,engine)

async def test_manual_review_is_owner_action_and_never_automated_intent(config,db,listing):
    setup_app(config,db,listing)
    loop=SemiAutoLoop(config,db); loop.ids=[1]; loop.phase='REVIEW'
    p=page(); loop.engine.retained_pages[1]=p
    assert 'submit yourself' in await loop.command('review 1')
    assert loop.guard.review_pages[p]==1
    loop.guard.click({'page':p},True)
    assert not db.application(1)['submit_intent_at']
    assert db.application(1)['manual_resume_allowed']==0
    await loop.command('lock')
    with pytest.raises(ValueError): await loop.command('review 1')
    assert 'retired' in await loop.command('submitted 1 Employer receipt AB123')
    assert db.automation_retired(1) and not db.application(1)['submission_confirmation_seen']
    loop.guard.check()

@pytest.mark.parametrize('text',['autosubmit on','retry 1','review 3556','submitted 4522 Receipt','next'])
async def test_commands_cannot_bypass_old_holds_or_batch_ownership(config,db,listing,text):
    setup_app(config,db,listing)
    loop=SemiAutoLoop(config,db); loop.ids=[1]; loop.phase='REVIEW'; loop.engine.retained_pages[1]=page()
    before=db.application(1)
    with pytest.raises(ValueError): await loop.command(text)
    assert db.application(1)==before

async def test_refill_waits_for_manual_review_and_does_not_reconstruct(config,db,listing):
    setup_app(config,db,listing)
    loop=SemiAutoLoop(config,db); loop.ids=[1]; loop.phase='REVIEW'; p=page()
    loop.engine.retained_pages[1]=p
    loop.ready_to_refill.add(1); loop.guard.review_pages[p]=1
    loop.engine.service_manual_requests=AsyncMock()
    await loop.refill()
    loop.engine.service_manual_requests.assert_not_awaited()
    assert not db.rows('SELECT * FROM manual_requests')
    loop.guard.review_pages.clear(); loop.engine.retained_pages.clear()
    await loop.refill()
    assert not db.rows('SELECT * FROM manual_requests')

async def test_guard_blocks_final_requests_except_explicit_idle_review(config,db,listing):
    setup_app(config,db,listing); guard=ReviewGuard(db); p=page()
    req=SimpleNamespace(url='https://employer.test/submit',method='POST',post_data_buffer=b'{}',frame=SimpleNamespace(page=p))
    route=SimpleNamespace(request=req,abort=AsyncMock(),fallback=AsyncMock())
    await guard.route(route); route.abort.assert_awaited_once()
    with pytest.raises(RuntimeError): guard.check()
    guard=ReviewGuard(db); guard.review_pages[p]=1
    route.abort.reset_mock(); await guard.route(route)
    route.abort.assert_not_awaited(); route.fallback.assert_awaited_once()
    assert db.application(1)['application_state']=='UNKNOWN'
    assert not db.application(1)['submit_intent_at']
    guard.check()

async def test_batch_retains_exactly_five_and_blocks_sixth_real_page(config,db,listing,monkeypatch):
    from autoapply.browser import Browser
    config.data['application']['auto_submit']=False
    config.data['browser']['timeout_ms']=15000
    db.set_setting('auto_submit',False); db.set_setting('paused',False)
    html='''<h1>Software Engineering Intern Summer 2027</h1><p>Software engineering internship for undergraduate students in New York, NY.</p>
    <form><label>First Name<input name="first_name" required></label><label>Resume<input name="resume" type="file" required></label>
    <button type="submit">Submit application</button></form>'''
    for n in range(6):
        db.ingest(replace(listing,company=f'Company {n}',url=f'http://127.0.0.1:8123/app/{n}'),config)
    browser=Browser(config); original=browser._start
    async def start():
        result=await original()
        async def fixture(route):
            if '/submit' in route.request.url: await route.fallback()
            else: await route.fulfill(status=200,content_type='text/html',body=html)
        await browser.context.route('**/*',fixture)
        return result
    monkeypatch.setattr(browser,'_start',start)
    engine=Engine(config,db,browser,fill_only=True,preserve_fill_session=True,semi_auto=True)
    loop=SemiAutoLoop(config,db,engine)
    try:
        await loop.fill_batch()
        assert loop.ids==[1,2,3,4,5]
        assert len(loop.live_pages())==5 and browser.max_tabs_observed==5
        for i,p in loop.live_pages().items():
            assert await p.locator('[name=first_name]').input_value()=='Test'
            assert db.application(i)['application_state']=='READY_FOR_MANUAL_SUBMIT'
            assert db.setting(f'upload_result:{i}')['ready'] is True
        assert not db.application(6)['attempt_started']
        with pytest.raises(RuntimeError,match='TAB_LIMIT'): await engine.process_one(6)
        assert not any(a['submit_intent_at'] for a in db.rows('SELECT * FROM applications'))
        loop.guard.check()
    finally: await engine.close()

async def test_engine_refuses_work_while_manual_review_armed(config,db,listing):
    setup_app(config,db,listing); loop=SemiAutoLoop(config,db)
    loop.ids=[1]; loop.guard.review_pages[page()]=1
    loop.engine._process_one=AsyncMock()
    with pytest.raises(RuntimeError,match='Manual review'): await loop.engine.process_one(1)
    with pytest.raises(RuntimeError,match='Manual review'): await loop.engine.resume_manual(1)
    loop.engine._process_one.assert_not_awaited()

def test_unexpected_page_below_five_tab_limit_still_stops(config,db):
    loop=SemiAutoLoop(config,db); loop.ids=[1]
    owned=page(); popup=page()
    loop.engine.retained_pages[1]=owned
    loop.engine.browser.context=SimpleNamespace(pages=[owned,popup])
    with pytest.raises(RuntimeError,match='Unowned'): loop.verify_page_ownership()

async def test_review_cannot_race_same_form_revalidation(config,db,listing):
    setup_app(config,db,listing); loop=SemiAutoLoop(config,db)
    loop.ids=[1]; loop.phase='REVIEW'; p=page(); loop.engine.retained_pages[1]=p
    db.lifecycle.record_hold(1,'Input needed','INPUT_REQUIRED',manual_resume_allowed=1,session_preserved=1)
    db.set_setting('manual_session:1',dict(token='fixture',busy=False))
    loop.ready_to_refill.add(1)
    entered=asyncio.Event(); release=asyncio.Event()
    async def service(): entered.set(); await release.wait()
    loop.engine.service_manual_requests=service
    worker=asyncio.create_task(loop.refill()); await entered.wait()
    assert loop.phase=='REFILLING'
    with pytest.raises(ValueError): await loop.command('review 1')
    assert not loop.guard.review_pages
    release.set(); await worker
    assert loop.phase=='REVIEW'

async def test_no_browser_start_before_discord_ready(config,db,listing,monkeypatch):
    setup_app(config,db,listing)
    config.data['browser']['headless']=False; config.data['discord']['enabled']=True
    monkeypatch.setenv('DISCORD_BOT_TOKEN','fixture'); monkeypatch.setenv('DISCORD_USER_ID','123')
    loop=SemiAutoLoop(config,db); loop.fill_batch=AsyncMock()
    bot=Mock(start=AsyncMock(),wait_for_delivery=AsyncMock(side_effect=RuntimeError('DM failure')),close=AsyncMock())
    monkeypatch.setattr('autoapply.discord_bot.DiscordBot',lambda c:bot)
    with pytest.raises(RuntimeError,match='DM failure'): await loop.run()
    loop.fill_batch.assert_not_awaited()
    assert db.setting('paused') is True

async def test_discord_deduplicates_questions_and_keeps_old_outbox_private(config,db,listing,monkeypatch):
    from autoapply.discord_bot import DiscordBot
    setup_app(config,db,listing)
    db.lifecycle.record_hold(1,'Input needed','INPUT_REQUIRED',manual_resume_allowed=0)
    row=db.question(1,Question('git','Experience with Git','radio',True,['Yes','No']))
    loop=SemiAutoLoop(config,db); loop.ids=[1]; loop.phase='REVIEW'
    db.notify('input-one',dict(kind='input',application_id=1))
    db.notify('input-two',dict(kind='input',application_id=1))
    db.notify('historical',dict(kind='input',application_id=3556,message='Protected historical message'))
    monkeypatch.setenv('DISCORD_USER_ID','123')
    bot=DiscordBot(loop.engine.control); bot.delivery_ready.set()
    user=Mock(send=AsyncMock()); monkeypatch.setattr(bot,'get_user',lambda _:user)
    monkeypatch.setattr(bot,'is_closed',Mock(side_effect=[False,True]))
    monkeypatch.setattr('autoapply.discord_bot.asyncio.sleep',AsyncMock())
    await bot.deliver_outbox()
    user.send.assert_awaited_once()
    sent=user.send.await_args
    assert f"Question #{row['id']}" in sent.args[0]
    assert sent.kwargs['allowed_mentions'].everyone is False
    assert db.one("SELECT delivered_at FROM notifications WHERE dedupe_key='historical'")['delivered_at'] is None
    await bot.close()
