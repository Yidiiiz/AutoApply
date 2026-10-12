import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoapply.applications import UnsupportedForm
from autoapply.browser import Browser
from autoapply.engine import Engine
from autoapply.workday import WorkdayAdapter


CHOOSER = (Path(__file__).parent/'fixtures/workday-entry-choice.html').read_text()
LOADING = (Path(__file__).parent/'fixtures/workday-loading.html').read_text()
FORM = '''<h1>Software Engineering Intern Summer 2027</h1>
<p>Software engineering internship for undergraduate students in New York, NY.</p>
<form><label>First Name<input name="first_name" required></label>
<label>Resume<input name="resume" type="file" required></label>
<button type="button">Submit application</button></form>'''


@pytest.fixture
async def browser(config):
    value = Browser(config)
    await value.start()
    await value.enforce_application_limit(1)
    yield value
    await value.close()


@pytest.fixture(autouse=True)
def deadlines(monkeypatch):
    monkeypatch.setattr(WorkdayAdapter, 'readiness_timeout', 2)
    monkeypatch.setattr(WorkdayAdapter, 'stable_seconds', .12)


async def fixture_page(browser, body=CHOOSER, host='fixture.myworkdayjobs.com'):
    await browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=body))
    page = await browser.new_page()
    await page.goto(f'https://{host}/en-US/careers/job/example')
    await page.evaluate('''() => {
      window.clicks=[];window.finalClicks=0;
      document.addEventListener('click', e=>{
        const action=e.target.closest('a,button,[role=button]');
        if(!action)return;
        e.preventDefault();window.clicks.push(action.id);
        if(action.textContent==='Submit application')window.finalClicks++;
      });
    }''')
    return page


async def deliver_form(page, delay=0):
    await page.evaluate('''({html,delay})=>{
      document.querySelector('#manual').onclick=()=>{
        setTimeout(()=>{document.body.innerHTML=html},delay);
      };
    }''', {'html':FORM,'delay':delay})


async def assert_navigation_only(page, adapter):
    assert not adapter.controls and not adapter.live_committed and not adapter.answer_hints
    assert not adapter.uploads and not page._autoapply_uploads.selected
    assert page._autoapply_uploads.receipt is None
    assert await page.evaluate('window.finalClicks') == 0
    assert len(page.context.pages) == 1


@pytest.mark.parametrize('host', ['fixture.myworkdayjobs.com','fixture.myworkdaysite.com'])
@pytest.mark.parametrize('representation', ['labels','hrefs','both','aria_label','aria_labelledby','action','normalized'])
async def test_semantic_chooser_manual_only(browser, host, representation):
    page = await fixture_page(browser, host=host)
    if representation == 'labels':
        await page.locator('a').evaluate_all("es=>es.forEach(e=>{e.removeAttribute('href');e.setAttribute('role','button')})")
    elif representation == 'hrefs':
        await page.locator('a').evaluate_all("es=>es.forEach(e=>e.textContent='Localized option')")
        await page.locator('h2').evaluate("e=>e.remove()")
    elif representation in {'aria_label', 'aria_labelledby'}:
        await page.locator('a').evaluate_all('''(es,kind)=>es.forEach(e=>{
          if(kind==='aria_label')e.setAttribute('aria-label',e.textContent);
          else {const s=document.createElement('span');s.id=e.id+'-label';s.hidden=true;
            s.textContent=e.textContent;document.body.append(s);e.setAttribute('aria-labelledby',s.id);}
          e.textContent='';e.style.display='inline-block';e.style.width='160px';e.style.height='40px';
          e.removeAttribute('href');e.setAttribute('role','button');
        })''', representation)
    elif representation == 'action':
        await page.locator('a').evaluate_all("es=>es.forEach(e=>{e.setAttribute('action',e.getAttribute('href'));e.removeAttribute('href');e.setAttribute('role','button')})")
    elif representation == 'normalized':
        await page.locator('a').evaluate_all("es=>es.forEach(e=>{e.setAttribute('href',e.getAttribute('href').toUpperCase()+'/?source=fixture');e.textContent='  '+e.textContent.toUpperCase()+'  '})")
    await deliver_form(page)
    adapter = WorkdayAdapter(page)
    events = []
    adapter.emit = lambda kind, detail: events.append((kind,detail))
    assert await adapter.wait_ready() == 'APPLICATION_ENTRY_CHOICE'
    await adapter.begin()
    assert adapter.readiness['state'] == 'FORM_READY'
    assert await page.evaluate('window.clicks') == ['manual']
    assert [detail for kind,detail in events if kind=='WORKDAY_ENTRY_MODE_SELECTED'] == [{'mode':'applyManually'}]
    assert any(kind=='WORKDAY_ENTRY_TRANSITION_CONFIRMED' for kind,_ in events)
    await assert_navigation_only(page, adapter)


@pytest.mark.parametrize('change', [
    "document.querySelector('#manual').remove()",
    "document.querySelector('#manual').after(document.querySelector('#manual').cloneNode(true))",
    "document.querySelector('#manual').hidden=true",
    "document.querySelector('#manual').setAttribute('aria-disabled','true')",
    "document.querySelector('#manual').setAttribute('disabled','')",
    "document.querySelector('#manual').parentElement.setAttribute('aria-disabled','true')",
    "document.querySelector('#manual').outerHTML='<button id=manual disabled>Apply Manually</button>'",
    "document.querySelector('#manual').href='/en-US/careers/job/example/apply/autofillWithResume'",
    "document.querySelector('#manual').href='/en-US/careers/job/example/apply/useMyLastApplication'",
    "document.querySelector('#manual').href='/submit'",
    "document.querySelector('#manual').href='https://foreign.myworkdayjobs.com/apply/applyManually'",
    "document.querySelector('#manual').href='https://fixture.myworkdayjobs.com.evil.test/apply/applyManually'",
    "document.querySelector('#manual').href='javascript:void(0)'",
    "document.querySelector('#manual').setAttribute('aria-label','Use My Last Application')",
    "document.querySelector('#manual').target='_blank'",
    "document.querySelector('#manual').outerHTML='<form action=\"/apply/applyManually\"><button id=manual>Apply Manually</button></form>'",
    "document.querySelector('#manual').style.width='0';document.querySelector('#manual').style.fontSize='0'",
    "document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:100;background:white\"></div>')",
    "document.body.insertAdjacentHTML('beforeend','<form><input><button>Next</button></form>')",
])
async def test_unsafe_or_ambiguous_choices_hold(browser, change):
    page = await fixture_page(browser)
    await page.evaluate('() => {'+change+'}')
    adapter = WorkdayAdapter(page)
    assert await adapter.wait_ready() == 'APPLICATION_ENTRY_CHOICE'
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.begin()
    assert caught.value.category == 'APPLICATION_ENTRY_MODE_UNRESOLVED'
    assert await page.evaluate('window.clicks') == []
    await assert_navigation_only(page, adapter)


@pytest.mark.parametrize('change', [
    'e.remove()', 'e.replaceWith(e.cloneNode(true))',
    "e.href='/apply/useMyLastApplication'", 'e.hidden=true',
    "e.setAttribute('aria-disabled','true')",
    "e.after(e.cloneNode(true))",
])
async def test_fresh_guard_rejects_changed_target(browser, change):
    page = await fixture_page(browser)
    cursor = page._autoapply_cursor
    original = cursor.resolver.select
    async def changed(*args):
        target = await original(*args)
        await page.locator('#manual').evaluate('e=>{'+change+'}')
        return target
    cursor.resolver.select = changed
    adapter = WorkdayAdapter(page)
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.begin()
    assert caught.value.category == 'APPLICATION_ENTRY_MODE_UNRESOLVED'
    assert await page.evaluate('window.clicks') == []


@pytest.mark.parametrize('delay', [0, 300, 650])
async def test_main_apply_delayed_chooser_and_form_are_single_clicks(browser, delay):
    page = await fixture_page(browser, '<button id="apply">Apply</button>')
    await page.evaluate('''({chooser,form,delay})=>{
      document.querySelector('#apply').onclick=()=>{
        document.body.innerHTML='<div aria-busy="true">Loading</div>';
        setTimeout(()=>{
          document.body.innerHTML=chooser;
          document.querySelector('#manual').onclick=()=>setTimeout(()=>{document.body.innerHTML=form},delay);
        },delay);
      };
    }''', dict(chooser=CHOOSER,form=FORM,delay=delay))
    adapter = WorkdayAdapter(page)
    states = []
    adapter.emit = lambda kind, detail: states.append(detail.get('state'))
    await adapter.begin()
    assert await page.evaluate('window.clicks') == ['apply','manual']
    assert states.index('SHELL_READY') < states.index('APPLICATION_ENTRY_CHOICE') < states.index('FORM_READY')
    await assert_navigation_only(page, adapter)


@pytest.mark.parametrize('scenario', ['no_change','dom_churn','url_only','blank','same_route_blank','backend_failure'])
async def test_unconfirmed_delivery_never_reclicks(browser, scenario):
    page = await fixture_page(browser)
    if scenario == 'dom_churn':
        await page.locator('#manual').evaluate("e=>e.onclick=()=>setInterval(()=>document.body.dataset.tick=Math.random(),30)")
    elif scenario == 'url_only':
        await page.locator('#manual').evaluate("e=>e.onclick=()=>history.pushState({},'',e.href)")
    elif scenario in {'blank', 'same_route_blank'}:
        if scenario=='same_route_blank':
            await page.evaluate("history.pushState({},'',document.querySelector('#manual').href)")
        await page.locator('#manual').evaluate("e=>e.onclick=()=>document.body.innerHTML=''")
    elif scenario == 'backend_failure':
        backend = page._autoapply_cursor.backend
        original = backend.up
        async def uncertain(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('simulated lost input acknowledgement')
        backend.up = uncertain
    adapter = WorkdayAdapter(page)
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.begin()
    assert caught.value.category == 'TRANSITION_UNCONFIRMED'
    if scenario == 'backend_failure':
        backend.up = original
    assert await page.evaluate('window.clicks') == ['manual']
    with pytest.raises(UnsupportedForm):
        await WorkdayAdapter(page).select_entry_mode()
    assert await page.evaluate('window.clicks') == ['manual']
    await assert_navigation_only(page, adapter)


async def test_confirmed_loading_waits_for_real_form(browser):
    page = await fixture_page(browser)
    await page.evaluate('''({loading,form})=>document.querySelector('#manual').onclick=()=>{
      document.body.innerHTML=loading;setTimeout(()=>document.body.innerHTML=form,500);
    }''', dict(loading=LOADING,form=FORM))
    adapter = WorkdayAdapter(page)
    await adapter.begin()
    assert adapter.readiness['state']=='FORM_READY'
    assert await page.evaluate('window.clicks')==['manual']


@pytest.mark.parametrize('initial', ['chooser','shell'])
async def test_real_href_navigation_uses_exposed_route_once(browser, db, initial):
    from autoapply.fill_batch import FillOnlyInvariant
    requests = []
    async def route_fixture(route):
        requests.append(route.request.url)
        body = FORM if '/apply/applyManually' in route.request.url else CHOOSER
        if initial=='shell' and route.request.url.endswith('/example'):
            body = '<a href="/en-US/careers/job/example/entry">Apply</a>'
        await route.fulfill(content_type='text/html', body=body)
    await browser.context.route('**/*', route_fixture)
    invariant = FillOnlyInvariant(db)
    await invariant.install(browser.context)
    page = await browser.new_page()
    await page.goto('https://fixture.myworkdayjobs.com/en-US/careers/job/example')
    adapter = WorkdayAdapter(page)
    await adapter.begin()
    assert adapter.readiness['state']=='FORM_READY'
    assert [url for url in requests if '/apply/' in url] == [
        'https://fixture.myworkdayjobs.com/en-US/careers/job/example/apply/applyManually']
    assert len(browser.context.pages)==1
    invariant.check()
    assert all(v==0 for v in invariant.snapshot().values())


async def test_loading_after_delivery_does_not_authorize_inventory(browser):
    page = await fixture_page(browser)
    await page.evaluate('html=>document.querySelector("#manual").onclick=()=>document.body.innerHTML=html', LOADING)
    adapter = WorkdayAdapter(page)
    events = []
    adapter.emit = lambda kind, detail: events.append(kind)
    with pytest.raises(UnsupportedForm, match='FORM_NOT_READY'):
        await adapter.begin()
    assert 'WORKDAY_ENTRY_TRANSITION_CONFIRMED' in events
    assert await page.evaluate('window.clicks')==['manual']
    await assert_navigation_only(page, adapter)


@pytest.mark.parametrize('initial', ['form','shell'])
async def test_direct_to_form_compatibility(browser, initial):
    page = await fixture_page(browser, FORM if initial=='form' else '<button id="apply">Apply</button>')
    if initial=='shell':
        await page.evaluate('html=>document.querySelector("button").onclick=()=>document.body.innerHTML=html', FORM)
    adapter = WorkdayAdapter(page)
    await adapter.begin()
    assert adapter.readiness['state']=='FORM_READY'
    assert await page.evaluate('window.clicks')==([] if initial=='form' else ['apply'])


async def test_inventory_cannot_treat_choice_as_answer(browser):
    page = await fixture_page(browser)
    adapter = WorkdayAdapter(page)
    with pytest.raises(UnsupportedForm, match='APPLICATION_ENTRY_MODE_UNRESOLVED'):
        await adapter.get_questions({'id':1})
    await assert_navigation_only(page, adapter)


@pytest.mark.parametrize('body,category', [
    ('<input type=password>', 'AUTH_REQUIRED'),
    ('<h2>Verify you are human</h2>', 'SECURITY_CONTROL'),
    ('<h2>This job is no longer available</h2>', 'FORM_TERMINAL_FAILURE'),
])
async def test_security_and_terminal_take_priority(browser, body, category):
    page = await fixture_page(browser, CHOOSER+body)
    with pytest.raises(UnsupportedForm) as caught:
        await WorkdayAdapter(page).begin()
    assert caught.value.category == category
    assert await page.evaluate('window.clicks')==[]


@pytest.mark.parametrize('scenario', ['known','unknown','ambiguous','unconfirmed'])
async def test_engine_fill_only_entry_navigation_preserves_policy(config, db, listing, scenario):
    db.set_setting('auto_submit', False)
    db.ingest(replace(listing, url='https://fixture.myworkdayjobs.com/careers/job/example'), config)
    engine = Engine(config, db, fill_only=True)
    form = FORM if scenario!='unknown' else FORM.replace('First Name','Unknown applicant identifier').replace('first_name','unknown')
    chooser = CHOOSER if scenario!='ambiguous' else CHOOSER.replace('id="manual"', 'hidden id="manual"')
    body = chooser+'''<script>window.entryClicks=[];window.finalClicks=0;
      document.addEventListener('click',e=>{const a=e.target.closest('a');
        if(a){e.preventDefault();window.entryClicks.push(a.id);'''+(
        'if(a.id==="manual")document.body.innerHTML='+json.dumps(form)+';' if scenario!='unconfirmed' else '')+'''}
        if(e.target.textContent==='Submit application')window.finalClicks++;
      });</script>'''
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route:route.fulfill(content_type='text/html', body=body))
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        await engine.process_one(1)
        app = db.application(1)
        events = db.rows('SELECT kind,detail FROM events WHERE application_id=1 ORDER BY id')
        kinds = [e['kind'] for e in events]
        if scenario=='known':
            assert app['application_state']=='READY_FOR_MANUAL_SUBMIT', app['failure_reason']
            assert kinds.index('WORKDAY_ENTRY_MODE_SELECTED') < kinds.index('WORKDAY_ENTRY_TRANSITION_CONFIRMED') < kinds.index('LIVE_CONTROL_COMMITTED') < kinds.index('UPLOAD_FILE_SELECTED')
            page = engine.retained_pages[1]
            assert await page.evaluate('window.entryClicks')==['manual']
            assert await page.evaluate('window.finalClicks')==0
            assert len(engine.browser.context.pages)==1
        else:
            assert app['error_category']=={'unknown':'INPUT_REQUIRED','ambiguous':'APPLICATION_ENTRY_MODE_UNRESOLVED','unconfirmed':'TRANSITION_UNCONFIRMED'}[scenario], app['failure_reason']
            if scenario == 'unknown':
                # Unknown personal facts do not prevent the independent resume
                # upload once entry and form readiness have been confirmed.
                assert 'LIVE_CONTROL_COMMITTED' not in kinds
                assert kinds.index('WORKDAY_ENTRY_TRANSITION_CONFIRMED') < kinds.index('UPLOAD_FILE_SELECTED')
                assert db.setting('upload_result:1')['ready']
            else:
                assert not any(k in kinds for k in ('LIVE_CONTROL_COMMITTED','UPLOAD_FILE_SELECTED','UPLOAD_READINESS'))
            assert not app['retry_allowed']
        questions = db.rows('SELECT * FROM questions')
        assert not any('Apply Manually' in q['raw_question'] for q in questions)
        if scenario=='unknown':
            q = next(q for q in questions if q['raw_question']=='Unknown applicant identifier')
            assert q['status']=='PENDING' and not q['answer']
        assert not app['submit_intent_at'] and not app['submission_confirmation_seen']
        engine.fill_invariant.check()
        assert all(v==0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()
