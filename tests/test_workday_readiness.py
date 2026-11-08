import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright._impl._errors import TargetClosedError
from playwright.async_api import Error

from autoapply.applications import UnsupportedForm, adapter_for
from autoapply.browser import Browser
from autoapply.engine import Engine
from autoapply.runtime import ProcessLock
from autoapply.uploads import UploadTracker
from autoapply.workday import WorkdayAdapter


LOADING = (Path(__file__).parent/'fixtures/workday-loading.html').read_text()
FORM = '''<h1>Software Engineering Intern Summer 2027</h1>
<p>Software engineering internship for undergraduate students in New York, NY.</p>
<form data-automation-id="applicationPage">
<label>First Name<input name="first_name" required></label>
<label>Resume<input name="resume" type="file" required></label>
<button type="button">Submit application</button></form>
<script>window.finalClicks=0;document.querySelector('button').onclick=()=>window.finalClicks++;</script>'''


@pytest.fixture
async def browser(config):
    value = Browser(config)
    await value.start()
    yield value
    await value.close()


@pytest.fixture(autouse=True)
def short_deadlines(monkeypatch):
    monkeypatch.setattr(WorkdayAdapter, 'readiness_timeout', 1.5)
    monkeypatch.setattr(WorkdayAdapter, 'stable_seconds', .12)


async def test_skeleton_reproduces_empty_inventory_without_upload_failure(browser, monkeypatch):
    from autoapply.applications import GenericApplicationAdapter
    from autoapply.patience import PatiencePolicy
    page = await browser.new_page()
    await page.set_content(LOADING)
    generic = GenericApplicationAdapter(page)
    monkeypatch.setattr('autoapply.applications.page_policy', lambda page: PatiencePolicy(timeout=8, extension=0))
    with pytest.raises(UnsupportedForm) as entry_error:
        await generic.begin()
    assert entry_error.value.category == 'FORM_NOT_READY'
    assert page._autoapply_uploads.selected == 0
    assert await generic.get_questions({'id':1}) == []
    # The old generic path reached upload readiness with this empty mapping.
    assert not await browser.uploads_ready(page, generic.uploads, .1)
    result = browser.observation(page)['upload_result']
    assert result['category'] == 'UPLOAD_NOT_ATTEMPTED'
    assert not any(result['ui'][key] for key in ('replace_visible','replace_usable','file_entry'))
    adapter = WorkdayAdapter(page)
    states = []
    adapter.emit = lambda _, detail: states.append(detail['state'])
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.get_questions({'id':1})
    assert caught.value.category == 'FORM_NOT_READY'
    assert states == ['LOADING','TIMEOUT_UNCONFIRMED']
    assert page._autoapply_uploads.selected == 0


@pytest.mark.parametrize('wrapper,attribute', [
    ('<div hidden>{}</div>', ''), ('<div aria-hidden="true">{}</div>', ''),
    ('<template>{}</template>', ''), ('<div inert>{}</div>', ''),
    ('<div style="visibility:hidden">{}</div>', ''),
    ('<div style="opacity:0">{}</div>', ''), ('<div aria-busy="true">{}</div>', ''),
    ('{}', 'disabled'), ('{}', 'aria-disabled="true"'), ('{}', 'style="display:none"'),
])
async def test_hidden_template_disabled_uploads_not_in_inventory(browser, wrapper, attribute):
    page = await browser.new_page()
    file = wrapper.format(f'<label>Resume<input type="file" {attribute}></label>')
    await page.set_content(FORM.replace('<label>Resume<input name="resume" type="file" required></label>', file))
    adapter = WorkdayAdapter(page)
    if 'aria-busy' in wrapper:
        with pytest.raises(UnsupportedForm, match='FORM_NOT_READY'):
            await adapter.get_questions({'id':1})
    else:
        questions = await adapter.get_questions({'id':1})
        assert [q.label for q in questions] == ['First Name']
    assert not adapter.uploads and page._autoapply_uploads.selected == 0


@pytest.mark.parametrize('change', ['e.remove()', 'e.style.visibility="hidden"', 'e.disabled=true'])
async def test_stale_upload_control_rejected_before_selection(browser, config, change):
    page = await browser.new_page()
    await page.set_content(FORM)
    adapter = WorkdayAdapter(page)
    q = next(q for q in await adapter.get_questions({'id':1}) if q.kind=='file')
    await page.locator('input[type=file]').evaluate('e=>{' + change + '}')
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.upload_documents(q, config.resume)
    assert caught.value.category == 'UPLOAD_CONTROL_NOT_READY'
    assert not page._autoapply_uploads.selected


async def test_delayed_form_and_loading_disappearance_required(browser):
    page = await browser.new_page()
    await page.set_content(LOADING + FORM)
    adapter = WorkdayAdapter(page)
    task = asyncio.create_task(adapter.get_questions({'id':1}))
    await asyncio.sleep(.2)
    assert not task.done()
    await page.locator('[aria-busy=true]').evaluate('e=>e.remove()')
    questions = await task
    assert [q.label for q in questions] == ['First Name','Resume']
    assert adapter.readiness['state'] == 'FORM_READY'


async def test_unstable_dom_never_becomes_ready(browser):
    page = await browser.new_page()
    await page.set_content(FORM)
    await page.evaluate("() => setInterval(()=>document.querySelector('form').setAttribute('data-changing',Math.random()),30)")
    adapter = WorkdayAdapter(page)
    with pytest.raises(UnsupportedForm, match='FORM_NOT_READY'):
        await adapter.wait_ready()


async def test_delayed_shell_entry_clicked_only_once_after_loading(browser):
    page = await browser.new_page()
    await page.set_content(LOADING)
    await page.evaluate('''html=>{
      window.entries=0;
      setTimeout(()=>{
        document.body.innerHTML='<button>Apply</button>';
        document.querySelector('button').onclick=()=>{window.entries++;document.body.innerHTML=html};
      },200);
    }''', FORM)
    adapter = WorkdayAdapter(page)
    await adapter.begin()
    assert await page.evaluate('window.entries') == 1
    assert adapter.readiness['state']=='FORM_READY'


@pytest.mark.parametrize('body,state,category', [
    ('<input type="password">','SECURITY_OR_AUTH','AUTH_REQUIRED'),
    ('<h1>Verify you are human</h1>','SECURITY_OR_AUTH','SECURITY_CONTROL'),
    ('<h1>This job is no longer available</h1>','TERMINAL_FAILURE','FORM_TERMINAL_FAILURE'),
])
async def test_security_auth_and_terminal_states(browser, body, state, category):
    page = await browser.new_page()
    await page.set_content(body)
    adapter = WorkdayAdapter(page)
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.wait_ready()
    assert caught.value.category == category
    assert adapter.readiness['state'] == state


@pytest.mark.parametrize('host', ['tenant.myworkdayjobs.com','tenant.myworkdaysite.com'])
def test_provider_routing_is_not_application_specific(host):
    assert isinstance(adapter_for(SimpleNamespace(url=f'https://{host}/careers/job/fixture')), WorkdayAdapter)
    assert '3556' not in Path('autoapply/workday.py').read_text()


def test_no_selection_verdict_and_attempted_failure_are_distinct():
    state = dict(attached=False, busy=False, disabled=False, warning=False,
                 replace_visible=False, replace_usable=False, file_entry=False)
    tracker = UploadTracker()
    assert tracker.verdict(state, ashby=False)['category'] == 'UPLOAD_NOT_ATTEMPTED'
    tracker.select()
    assert tracker.verdict(state, ashby=False)['category'] == 'UPLOAD_FAILED'


async def test_workday_attachment_still_requires_positive_evidence_and_current_hash(browser, config):
    page = await browser.new_page()
    await page.set_content(FORM)
    adapter = WorkdayAdapter(page)
    q = next(q for q in await adapter.get_questions({'id':1}) if q.kind=='file')
    assert not await browser.uploads_ready(page, adapter.uploads, .1)
    await adapter.upload_documents(q, config.resume)
    assert await browser.uploads_ready(page, adapter.uploads, 2)
    assert page._autoapply_uploads.result['file_selected']
    config.resume.write_bytes(b'%PDF-1.4\nchanged fixture')
    assert not await browser.uploads_ready(page, adapter.uploads, .1)
    assert page._autoapply_uploads.result['category'] == 'UPLOAD_FAILED'
    assert page._autoapply_uploads.receipt is None


@pytest.mark.parametrize('scenario', ['loading','delayed','unknown','missing_resume'])
async def test_engine_fill_only_ordering_and_truthful_holds(config, db, listing, scenario):
    db.set_setting('auto_submit', False)
    db.ingest(replace(listing, url='https://fixture.myworkdayjobs.com/careers/job/test_1'), config)
    engine = Engine(config, db, fill_only=True)
    try:
        await engine.browser.start()
        body = FORM
        if scenario in {'loading','delayed'}:
            body = LOADING
        if scenario == 'unknown':
            body = FORM.replace('First Name', 'Unknown applicant identifier').replace('name="first_name"', 'name="unknown"')
        if scenario == 'missing_resume':
            body = FORM.replace('<label>Resume<input name="resume" type="file" required></label>', '')
        async def fixture(route):
            await route.fulfill(content_type='text/html', body=body)
        await engine.browser.context.route('**/*', fixture)
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        if scenario == 'delayed':
            original = engine.browser.navigate
            async def navigate(page, url):
                result = await original(page, url)
                await page.evaluate('(html)=>setTimeout(()=>{document.body.innerHTML=html},200)', FORM)
                return result
            engine.browser.navigate = navigate
        await engine.process_one(1)
        app = db.application(1)
        events = db.rows('SELECT kind,detail FROM events WHERE application_id=1 ORDER BY id')
        saved = json.loads((config.private/'fill-only-checkpoints/1.json').read_text())
        if scenario == 'delayed':
            assert app['application_state']=='READY_FOR_MANUAL_SUBMIT', app['failure_reason']
            assert saved['form_filled'] and saved['upload_ready']
            kinds = [e['kind'] for e in events]
            assert kinds.index('WORKDAY_FORM_READINESS') < kinds.index('UPLOAD_FILE_SELECTED') < kinds.index('UPLOAD_READINESS')
        else:
            category = {'loading':'FORM_NOT_READY','unknown':'INPUT_REQUIRED','missing_resume':'UPLOAD_NOT_ATTEMPTED'}[scenario]
            assert app['error_category']==category, app['failure_reason']
            assert not saved['form_filled'] and not saved['upload_ready']
            assert saved['validated_at'] is None and not saved['reconstruction_verified']
            assert engine.control.status_snapshot(1)['readiness']=='SAVED_PROGRESS_CHECKPOINT'
            with pytest.raises(ValueError): db.lifecycle.restore_checkpoint(1)
            if scenario=='loading':
                assert not any(e['kind'] in {'UPLOAD_READINESS','UPLOAD_FILE_SELECTED','LIVE_CONTROL_COMMITTED'} for e in events)
                assert not db.application(1)['retry_allowed']
            if scenario=='unknown':
                question = db.one("SELECT * FROM questions WHERE raw_question='Unknown applicant identifier'")
                assert question['status']=='PENDING' and not question['answer']
        assert not app['submit_intent_at'] and not app['submission_confirmation_seen']
        engine.fill_invariant.check()
        assert all(v==0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()


async def test_engine_defers_upload_until_later_step_and_carries_verified_receipt(config, db, listing):
    db.set_setting('auto_submit', False)
    db.ingest(replace(listing, url='https://fixture.myworkdayjobs.com/careers/job/steps_1'), config)
    engine = Engine(config, db, fill_only=True)
    first = '''<h1>Software Engineering Intern Summer 2027</h1>
    <p>Software engineering internship for undergraduate students in New York, NY.</p>
    <form><label>First Name<input name="first_name" required></label><button type="button">Next</button></form>'''
    second = '''<form><label>Resume<input name="resume" type="file" required
        onchange="this.nextElementSibling.textContent=this.files[0].name"><span></span></label><button type="button">Next</button></form>'''
    third = '<form><label>Last Name<input name="last_name" required></label><button type="button">Submit application</button></form>'
    body = first + '<script>window.steps=0;window.finalClicks=0;document.addEventListener("click",e=>{' + \
        'if(e.target.tagName!=="BUTTON")return;if(window.steps++===0)document.querySelector("form").outerHTML=' + json.dumps(second) + \
        ';else if(window.steps===2)document.querySelector("form").outerHTML=' + json.dumps(third) + ';else window.finalClicks++});</script>'
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route:route.fulfill(content_type='text/html', body=body))
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        await engine.process_one(1)
        assert db.application(1)['application_state']=='READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        events = [r['kind'] for r in db.rows('SELECT kind FROM events WHERE application_id=1 ORDER BY id')]
        assert events.index('STEP_ADVANCED') < events.index('UPLOAD_FILE_SELECTED') < events.index('UPLOAD_READINESS')
        assert await engine.retained_pages[1].evaluate('window.finalClicks')==0
        engine.fill_invariant.check()
    finally:
        await engine.close()


def test_manual_hold_cannot_inherit_strong_checkpoint_label(db, config, listing):
    from autoapply.status import snapshot
    db.ingest(listing, config)
    db.lifecycle.record_hold(1, 'Form loading', 'FORM_NOT_READY')
    db.set_setting('fill_checkpoint:1', {'readiness':'RECONSTRUCTABLE_CHECKPOINT'})
    assert snapshot(db, 1)['readiness']=='FILLED_CHECKPOINT'
    with pytest.raises(ValueError):
        db.lifecycle.restore_checkpoint(1)


async def test_already_closed_context_cleanup_keeps_telemetry_and_releases_lock(config, db, listing):
    db.ingest(listing, config)
    db.event(1, 'LOCAL_TELEMETRY', 'captured')
    browser = Browser(config)
    stop = AsyncMock()
    browser.playwright = SimpleNamespace(stop=stop)
    close = AsyncMock(side_effect=TargetClosedError())
    browser.context = SimpleNamespace(pages=[], close=close)
    browser.leases['owned'] = object()
    lock = config.private/'fixture.lock'
    with ProcessLock(lock):
        await browser.close()
        await browser.close()
    with ProcessLock(lock):
        assert not browser.leases and browser.context is None
    close.assert_awaited_once()
    stop.assert_awaited_once()
    assert db.one("SELECT detail FROM events WHERE kind='LOCAL_TELEMETRY'")['detail']=='captured'


async def test_unrelated_context_close_exception_propagates(config):
    browser = Browser(config)
    browser.context = SimpleNamespace(pages=[], close=AsyncMock(side_effect=Error('unrelated protocol failure')))
    browser.playwright = SimpleNamespace(stop=AsyncMock())
    with pytest.raises(Error, match='unrelated protocol failure'):
        await browser.close()
    assert browser.context is None and not browser.leases


async def test_cursor_protocol_failure_is_not_hidden_by_cleanup(config):
    browser = Browser(config)
    cursor = SimpleNamespace(cancel=AsyncMock(side_effect=Error('cursor protocol failure')))
    close = AsyncMock()
    browser.context = SimpleNamespace(pages=[SimpleNamespace(_autoapply_cursor=cursor)], close=close)
    with pytest.raises(Error, match='cursor protocol failure'):
        await browser.close()
    close.assert_awaited_once()
    assert browser.context is None


async def test_real_closed_context_and_page_cleanup_are_idempotent(browser):
    page = await browser.new_page()
    await browser.context.close()
    await browser.release_page(page)
    await browser.close()
    await browser.close()
    assert not browser.leases


@pytest.mark.parametrize('already_closed', [True, False])
async def test_page_close_race_only_releases_closed_ownership(config, already_closed):
    from autoapply.browser import PageLease
    class Page:
        def is_closed(self): return False  # Simulate the race after this check.
        async def close(self):
            raise TargetClosedError() if already_closed else Error('unrelated page protocol failure')
    browser = Browser(config)
    page = Page()
    lease = PageLease(browser, page)
    browser.leases[page] = lease
    if already_closed:
        await lease.release()
        assert page not in browser.leases
    else:
        with pytest.raises(Error, match='unrelated page protocol failure'):
            await lease.release()
        assert page in browser.leases
