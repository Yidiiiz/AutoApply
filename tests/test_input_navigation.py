import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.answers import AnswerResolver
from autoapply.control import Controller
from autoapply.discord_bot import DiscordBot
from autoapply.engine import Engine
from autoapply.models import Question, State
from autoapply.scrolling import ScrollController, NavigationError, VISIBILITY
from test_browser import browser
from test_browser import site, prepare


@pytest.mark.browser
async def test_directional_nested_and_document(browser):
    page = await browser.new_page()
    await page.set_content('<form><input id="top"><div style="height:2500px"></div><input id="bottom"></form>')
    scroll = ScrollController(page)
    await scroll.ensure_visible(page.locator('#bottom'))
    assert len([e for e in scroll.events if e['changed']]) > 1
    assert (await page.locator('#bottom').evaluate(VISIBILITY))['visible']
    await scroll.ensure_visible(page.locator('#top'))
    assert scroll.events[-1]['direction'] == 'UP'
    await page.set_content('<div id="panel" style="height:250px;overflow-y:auto"><form><input id="top"><div style="height:1700px"></div><input id="bottom"></form></div>')
    await scroll.ensure_visible(page.locator('#bottom'))
    assert scroll.events[-1]['container'] == 'div#panel'
    assert await page.locator('#panel').evaluate('e=>e.scrollTop') > 0
    await scroll.ensure_visible(page.locator('#top'))
    assert (await page.locator('#top').evaluate(VISIBILITY))['visible']


@pytest.mark.browser
async def test_fallback_missing_and_viewport(browser, monkeypatch):
    page = await browser.new_page()
    await page.set_viewport_size({'width':310,'height':1100})
    result = await browser.ensure_desktop(page)
    assert result['after']['width'] == 1440 and result['after']['height'] == 900
    await page.set_content('<form><div style="height:2100px"></div><input id="bottom"></form>')
    monkeypatch.setattr(page.mouse, 'wheel', AsyncMock())
    scroll = ScrollController(page)
    await scroll.ensure_visible(page.locator('#bottom'))
    assert any(e['strategy']=='container' and e['changed'] for e in scroll.events)
    assert any(not e['changed'] for e in scroll.events)
    with pytest.raises(NavigationError):
        await scroll.ensure_visible(page.locator('#missing'))
    await page.set_content('<div style="height:100px;overflow:hidden"><div style="height:1500px"></div><input></div>')
    with pytest.raises(NavigationError) as error:
        await scroll.ensure_visible(page.locator('input'))
    assert error.value.category not in {'INPUT_REQUIRED','SECURITY_CHALLENGE','FORM_VALIDATION_ERROR'}


def wait_app(config, db, listing):
    db.ingest(listing, config)
    db.claim(1)
    db.transition(1, State.MANUAL_REVIEW, 'Input needed')
    db.update_security(1, manual_action_required=1, error_category='INPUT_REQUIRED', manual_resume_allowed=1)
    return db.application(1)


def test_partial_delayed_answers_and_canonical_reuse(config, db, listing):
    app = wait_app(config, db, listing)
    control = Controller(config, db)
    one = db.question(1, Question('a','Please include your LinkedIn profile','text',True,scope='global'))
    two = db.question(1, Question('b','An employer-specific question','text',True,scope='application:1'))
    db.execute("UPDATE questions SET created_at='2000-01-01' WHERE application_id=1")
    db.recover()
    assert len(control.pending()) == 2
    control.route(f"!answer {one['id']} https://www.linkedin.com/in/test")
    assert not db.rows('SELECT * FROM manual_requests')
    control.route(f"!answer {two['id']} My answer")
    assert db.rows('SELECT * FROM manual_requests')[0]['application_id'] == 1
    assert db.application(1)['status'] == 'MANUAL_REVIEW'
    q = Question('other','Your LinkedIn URL','text',True)
    assert AnswerResolver(config,db).resolve(q,app).value == 'https://www.linkedin.com/in/test'


def test_context_and_controlled_target(config, db, listing):
    wait_app(config, db, listing)
    control = Controller(config, db)
    q = db.question(1, Question('a','Confirm?','radio',True,['Yes','No']))
    db.question(1, Question('b','Confirm another?','radio',True,['Yes','No']))
    with pytest.raises(ValueError, match='question number'):
        control.route('!yes')
    control.route(f"!yes {q['id']}")
    assert 'pending' in control.route('!resume')
    db.set_setting('controlled_application_id',1)
    with pytest.raises(ValueError, match='only application'):
        control.route('!resume 2')
    assert '!answer' in control.route('!help')


@pytest.mark.parametrize('command',['!answer 1 Yes','!yes','!no','!resume','!status','!stop','!help','!foo'])
async def test_immediate_acknowledgement(config, db, monkeypatch, command):
    monkeypatch.setenv('DISCORD_USER_ID','123')
    control = Controller(config,db)
    channel = SimpleNamespace(send=AsyncMock())
    def route(text):
        assert channel.send.await_args_list[0].args == ('Received.',)
        return 'done'
    monkeypatch.setattr(control,'route',route)
    bot = DiscordBot(control)
    await bot.on_message(SimpleNamespace(author=SimpleNamespace(id=123,bot=False),guild=None,content=command,channel=channel))
    assert channel.send.await_count == 2
    assert db.rows("SELECT * FROM events WHERE kind='discord_command_ack'")
    await bot.close()


async def test_grouped_notifications_and_quiet_progress(config,db,listing):
    app = wait_app(config,db,listing)
    engine = Engine(config,db)
    for index in range(3):
        engine.request(app,Question(str(index),f'Question {index}','text',True),'Unknown')
    for key in ['rate:1','question:1','hold:1','daily-cap:1']:
        db.notify(key,{'application_id':1,'message':'routine'})
    engine.handoff.notify(1,'Input needed')
    engine.handoff.notify(1,'Input needed')
    rows=db.rows('SELECT * FROM notifications')
    assert len(rows)==1
    message=json.loads(rows[0]['payload'])['message']
    assert all(f'Question {i}' in message for i in range(3))
    assert 'protected step' not in message


@pytest.mark.parametrize('label',['Please include your LinkedIn profile','Provide your LinkedIn URL','Your LinkedIn URL'])
def test_link_mapping(config,db,listing,label):
    from autoapply.answers import concept
    assert concept(label)=='links.linkedin'


def test_export_citizenship_only(config,db,listing):
    app=wait_app(config,db,listing)
    q=Question('export','Export Compliance','radio',True,['I am currently a "U.S. Person"','I will soon become a "U.S. Person"','I am eligible for licensing'])
    result=AnswerResolver(config,db).resolve(q,app)
    assert result.value==q.options[0] and result.source.startswith('derived:')
    db.set_setting('verified_fact:citizenship.us_citizen',{'value':'No','source':'USER_PROVIDED'})
    assert AnswerResolver(config,db).resolve(q,app) is None
    q.label='Are you eligible for an export license?'
    assert AnswerResolver(config,db).resolve(q,app) is None


@pytest.mark.browser
async def test_restart_reconstructs_only_resolved_input(config,db,browser,listing,site):
    engine = await prepare(config,db,browser,listing,site)
    await engine.close()
    db.recover()
    restarted = Engine(config,db)
    try:
        await restarted.service_manual_requests()
        assert db.application(1)['status']=='SUBMITTED'
        assert len(site[1])==1
        assert not db.rows('SELECT * FROM manual_requests')
    finally:
        await restarted.close()


async def test_controlled_queue_cannot_claim_another(config,db,listing):
    db.ingest(listing,config)
    db.set_setting('controlled_application_id',6415)
    assert db.claim() is None and db.claim(1) is None
    assert not await Engine(config,db).process_one(1)


@pytest.mark.browser
async def test_headed_desktop_geometry(config,monkeypatch):
    from autoapply.browser import Browser
    from autoapply.config import ROOT
    config.data['browser']['headless']=False
    browser=Browser(config)
    try:
        page=await browser.new_page()
        metrics=browser.viewport_diagnostics['after']
        assert metrics['width'] >= 800 and metrics['width']/metrics['height'] > 1.2
        assert metrics['outerWidth'] >= metrics['width']
        assert metrics['outerHeight'] >= metrics['height']
    finally:
        await browser.close()


@pytest.mark.browser
async def test_persistent_site_zoom_reset(config,monkeypatch,site):
    from autoapply.browser import Browser
    from autoapply.config import ROOT
    config.data['browser']['headless']=False
    folder=config.private/'browser_profile/Default'
    folder.mkdir(parents=True)
    (folder/'Preferences').write_text(json.dumps({'partition':{'per_host_zoom_levels':{'x':{'127.0.0.1':{'zoom_level':-6.025685102665476}}}}}),encoding='utf-8')
    browser=Browser(config)
    try:
        page=await browser.new_page()
        await page.goto(site[0])
        before=await page.evaluate('innerWidth')
        result=await browser.normalize_zoom(page)
        assert abs(before-page.viewport_size['width'])<3
        assert abs(result['after']['width']-page.viewport_size['width'])<3
        saved=json.loads((config.private/'browser-zoom-before.json').read_text(encoding='utf-8'))
        assert saved['per_host_zoom_levels']['x']['127.0.0.1']['zoom_level']< -6
    finally:
        await browser.close()


@pytest.mark.browser
async def test_submit_archive_has_no_orphan_screenshot_folder(config,db,browser,listing,site):
    engine=await prepare(config,db,browser,listing,site)
    await engine.resume_manual(1)
    assert db.application(1)['status']=='SUBMITTED'
    assert all((p/'application.json').exists() for p in (config.private/'application_history').glob('*/*') if p.is_dir())


def test_unknown_command_help(config,db):
    assert '!help' in Controller(config,db).route('!foo')


def test_unconfirmed_notification_is_not_failure(config,db,listing):
    wait_app(config,db,listing)
    db.update_security(1,error_category='SUBMISSION_UNKNOWN')
    Engine(config,db).handoff.notify(1,'No affirmative confirmation')
    payload=json.loads(db.rows('SELECT * FROM notifications')[0]['payload'])
    assert payload['message'].startswith('SUBMISSION UNCONFIRMED')
    assert 'APPLICATION FAILED' not in payload['message']
