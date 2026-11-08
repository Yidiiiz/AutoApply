"""T078: actual MV3 + temporary real backend, synthetic account/trap fixtures only."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
import yaml

import pytest
from playwright.sync_api import expect

from autoapply.credentials import NAMES
from autoapply.dashboard import DashboardHandler, DashboardServer
from autoapply.extension_api import dispatch
from test_dashboard import dashboard, offline_descriptions
from test_extension import loaded_extension

EMAIL = 't078-synthetic@example.test'
PASSWORD = 'T078-synthetic-Only!'
URL = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_T078/apply'
FIXTURE = Path(__file__).parent / 'fixtures/extension/t078.html'


@pytest.fixture
def backend(dashboard, config, db, listing, monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    profile = config.profile
    profile['links'] = {'personal_website': 'https://synthetic.example.test/'}
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    job, _ = db.ingest(replace(listing, url=URL.removesuffix('/apply')), config)
    app = db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id']
    dashboard.settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    server = DashboardServer(dashboard, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield dashboard, f'http://127.0.0.1:{server.server_port}', app
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def browser(loaded_extension, backend):
    context, worker, extension_id = loaded_extension
    _, server, app = backend
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server, key=backend[0].settings.extension_key(), autoSendStructure=False, autoAccountFill=False))
    page = context.new_page()
    blocked = []
    def serve(route):
        if route.request.method != 'GET':
            blocked.append(route.request.method)
            route.abort()
        else:
            route.fulfill(path=str(FIXTURE), content_type='text/html')
    # Context's default fixture policy aborts every other external request.
    page.route('https://fixture.myworkdayjobs.com/**', serve)
    page.goto(URL)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', URL)
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    page.frame_locator('#autoapply-sidebar iframe').locator('#send-structure').wait_for()
    panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    def isolated(body, arg=None):
        return worker.evaluate('''async ({tab,body,arg})=>{
          const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',func:async({body,arg})=>{
            if(body==='describe')return autoApplyCredentials.describe();
            if(body==='active')return autoApplyCredentials.active();
            if(body==='scan')return await autoApplyContent.scan();
            if(body==='inspect')return autoApplyContent.inspectPage();
            if(body==='fill')return await autoApplyContent.fill(arg);
            if(body==='fillFiles')return await autoApplyContent.fill(arg.answers,arg.files);
            if(body==='private')return {scan:await autoApplyContent.scan(),inspection:autoApplyContent.inspectPage(),structure:autoApplyContent.pageStructure(),chat:await autoApplyContent.chatContext()};
            throw Error('Unknown synthetic test operation');
          },args:[{body,arg}]});return frame.result;
        }''', dict(tab=tab, body=body, arg=arg))
    try:
        yield page, worker, tab, panel, isolated, app
        assert not blocked
    finally:
        page.close()


def analyze(panel, tab):
    return panel.evaluate('id=>chrome.runtime.sendMessage({type:"page-snapshot",tabId:id})', tab)


@pytest.mark.browser
@pytest.mark.parametrize('mode', ['signup', 'login'])
def test_account_analyze_activates_once_privately(browser, mode):
    page, worker, tab, panel, isolated, app = browser
    if mode == 'login':
        page.evaluate('login()')
    first = analyze(panel, tab)
    assert first.get('ok'), first
    assert 'activated' in first['summary'], first
    assert page.evaluate('[accountClicks,ordered,marketing.checked,applicationClicks]') == [1, [True], False, 0]
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('#password').input_value() == PASSWORD
    assert isolated('describe')['mode'] == mode
    # Repeated explicit requests and poll ticks cannot reactivate.
    second = analyze(panel, tab)
    assert 'busy' in second.get('error', '').lower() or 'attempted' in second.get('summary', '')
    page.wait_for_timeout(1200)
    assert page.evaluate('accountClicks') == 1
    private = isolated('private')
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    for value in (first, second, private, saved):
        assert EMAIL not in json.dumps(value) and PASSWORD not in json.dumps(value)
    assert len(saved['session']['accountActionClaims']) == 1


@pytest.mark.browser
@pytest.mark.parametrize('change', ['different_email', 'different_password', 'captcha', 'otp', 'ambiguous', 'submit', 'disabled', 'readonly', 'inert', 'cross_origin', 'consent_reverts', 'consent_hidden', 'reset', 'sso'])
def test_account_abstains(browser, change):
    page, worker, tab, panel, isolated, app = browser
    scripts = {
        'different_email': "email.value='other@example.test'",
        'different_password': "password.value='Other-password!'",
        'captcha': "document.querySelector('form').insertAdjacentHTML('beforeend','<iframe title=captcha src=about:blank></iframe>')",
        'otp': "document.querySelector('form').insertAdjacentHTML('beforeend','<input autocomplete=one-time-code>')",
        'ambiguous': "document.querySelector('[type=submit]').insertAdjacentHTML('afterend','<button>Create account</button>')",
        'submit': "document.querySelector('[type=submit]').textContent='Submit application'",
        'disabled': "password.disabled=true",
        'readonly': "password.readOnly=true",
        'inert': "document.querySelector('form').inert=true",
        'consent_reverts': "consent.onchange=()=>{consent.checked=false;consent.setAttribute('aria-checked','false')}",
        'consent_hidden': "consent.parentElement.hidden=true",
        'reset': "document.querySelector('form').insertAdjacentHTML('afterbegin','<h1>Reset your password</h1>')",
        'sso': "document.querySelector('[type=submit]').textContent='Sign in with Google'",
        'cross_origin': "document.querySelector('form').action='https://other.test/auth'",
    }
    page.evaluate(scripts[change])
    value = worker.evaluate('v=>autoApplyAutofill(v.tab,{application_id:v.app})', dict(tab=tab, app=app))
    assert not value.get('error'), value
    assert page.evaluate('[accountClicks,applicationClicks,marketing.checked]') == [0, 0, False]
    if change == 'different_email':
        assert page.locator('#password').input_value() == ''
    if change == 'different_password':
        assert page.locator('#password').input_value() == 'Other-password!'


@pytest.mark.browser
@pytest.mark.parametrize('change', ['edit', 'navigation', 'hash', 'cancel', 'pause'])
def test_account_pending_action_invalidated(browser, backend, monkeypatch, change):
    page, worker, tab, panel, isolated, app = browser
    entered, release = threading.Event(), threading.Event()
    original = DashboardHandler.do_POST
    def delayed(handler):
        if handler.path == '/ext/credentials' and handler.server.dashboard is backend[0]:
            entered.set()
            release.wait(15)
        return original(handler)
    monkeypatch.setattr(DashboardHandler, 'do_POST', delayed)
    before = isolated('describe')
    try:
        panel.evaluate('''id=>{
      globalThis.pendingOutcome={settled:false};
      globalThis.pending=chrome.runtime.sendMessage({type:"autofill",tabId:id}).then(
        result=>{pendingOutcome={settled:true,result};return result;},
        error=>{pendingOutcome={settled:true,error:String(error)};return pendingOutcome;});
        }''', tab)
        deadline = time.monotonic() + 8
        while not entered.is_set() and time.monotonic() < deadline:
            # Yield to Playwright so routed fixture requests can be fulfilled.
            page.wait_for_timeout(50)
            outcome = panel.evaluate('()=>pendingOutcome')
            assert not outcome['settled'], outcome
        assert entered.is_set(), panel.evaluate('()=>pendingOutcome')
        assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0]
        if change == 'edit':
            page.locator('#email').fill('manual@example.test')
        elif change in ('navigation', 'hash'):
            page.evaluate("suffix=>history.pushState({},'',location.pathname+suffix)", '?changed' if change == 'navigation' else '#changed')
        elif change == 'cancel':
            panel.evaluate('id=>chrome.runtime.sendMessage({type:"cancel-form-wait",tabId:id})', tab)
        else:
            paused = panel.evaluate('()=>chrome.runtime.sendMessage({type:"run-pause",paused:true})')
            assert paused.get('paused') is True, paused
    finally:
        release.set()
    result = panel.evaluate('()=>pending')
    assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0], result
    # A later poll must not turn the stopped lookup into new authorization.
    page.wait_for_timeout(1800)
    assert page.evaluate('accountClicks') == 0, result
    assert page.evaluate('applicationClicks') == 0, result
    if change in ('navigation', 'hash'):
        after = isolated('describe')
        assert after['revision'] > before['revision']
        assert after['url'] == before['url']
        assert 'changed' not in json.dumps(after)


@pytest.mark.browser
@pytest.mark.parametrize('style', ['hidden', 'style="opacity:0"'])
def test_ordinary_cover_upload_proxy_preserved(browser, style):
    page, worker, tab, panel, isolated, app = browser
    page.evaluate('''style=>{
      document.querySelector('form').outerHTML=`<form><h1>Job application</h1>
        <div><label for="cover">Cover Letter</label><button type="button">Upload</button>
          <input id="cover" type="file" ${style}><span id="filename"></span></div>
        <div id="ancestor"><label for="blocked">Cover Letter</label><button type="button">Upload</button>
          <input id="blocked" type="file" hidden></div>
        <label>Attachment<input id="ambiguous" type="file"></label></form>`;
      cover.addEventListener('change',()=>{filename.textContent=cover.files[0]?.name||''});
    }''', style)
    fields = isolated('scan')['fields']
    page.locator('#ancestor').evaluate('el=>el.hidden=true')
    result = isolated('fillFiles', dict(
        answers=[dict(key=f['key'], status='FILE', value='cover_letter') for f in fields if f['kind'] == 'file'],
        files=dict(cover_letter=dict(filename='synthetic-cover.pdf', mime='application/pdf', base64='JVBERiBmaXh0dXJl'))))
    assert result['filled'] == 1, result
    assert page.locator('#cover').evaluate('el=>el.files[0].name') == 'synthetic-cover.pdf'
    expect(page.locator('#filename')).to_have_text('synthetic-cover.pdf')
    assert page.locator('#blocked').evaluate('el=>el.files.length') == 0
    assert page.locator('#ambiguous').evaluate('el=>el.files.length') == 0
    assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0]


@pytest.mark.browser
def test_honeypots_analyze_and_stale_fill(browser):
    page, worker, tab, panel, isolated, app = browser
    page.evaluate('traps()')
    scan = isolated('scan')
    assert not any('robot' in f['label'].lower() for f in scan['fields'])
    assert {'Website', 'Full name'} <= {f['label'] for f in scan['fields']}
    # Prepare real stale client keys, then change labels/eligibility before fill.
    website = next(f for f in scan['fields'] if f['label'] == 'Website')
    full = next(f for f in scan['fields'] if f['label'] == 'Full name')
    page.locator('#website').evaluate("e=>e.parentElement.firstChild.textContent='Website. Robots only'")
    page.locator('#full').evaluate('e=>e.parentElement.hidden=true')
    result = isolated('fill', [dict(key=website['key'], status='RESOLVED', value='https://wrong.test'), dict(key=full['key'], status='RESOLVED', value='Wrong')])
    assert result['filled'] == 0
    assert page.locator('#website').input_value() == page.locator('#full').input_value() == ''
    page.locator('#website').evaluate("e=>e.parentElement.firstChild.textContent='Website'")
    page.locator('#full').evaluate('e=>e.parentElement.hidden=false')
    result = analyze(panel, tab)
    assert result.get('ok'), result
    assert page.locator('#full').input_value() == 'Test Student'
    assert page.locator('#website').input_value() == 'https://synthetic.example.test/'
    assert page.locator('#trap').input_value() == ''
    assert page.locator('#occupiedTrap').input_value() == 'MANUAL_TRAP'
    for selector in ('#hidden', '#inert', '#readonly'):
        assert page.locator(selector).input_value() == ''
    diagnostic = isolated('inspect')
    assert not any('robot' in f['label'].lower() for f in diagnostic['diagnostic']['fields'])
    assert page.evaluate('applicationClicks') == 0


def test_backend_robot_label_never_resolves_or_writes(backend):
    dashboard, _, app = backend
    fields = [dict(key='trap', label="Enter website. This input is for robots only, do not enter if you're human.", kind='text', required=True), dict(key='attr', label='Website', kind='url', required=True, **{'data-automation-id':'beecatcher'})]
    result = dispatch(dashboard, dict(url=URL, application_id=app, fields=fields), method='POST', path='/ext/answers')
    assert all(a['status'] == 'OPTIONAL_SKIP' and a['value'] is None for a in result['answers'])
    assert dispatch(dashboard, dict(question=fields[0]['label']), method='POST', path='/ext/write') == dict(answer=None, status='OPTIONAL_SKIP')


def test_backend_legitimate_website_still_resolves(backend):
    dashboard, _, app = backend
    result = dispatch(dashboard, dict(url=URL, application_id=app, fields=[dict(key='legit', label='Website', kind='url', required=True)]), method='POST', path='/ext/answers')
    assert result['answers'][0]['status'] == 'RESOLVED'
    assert result['answers'][0]['value'] == 'https://synthetic.example.test/'


@pytest.mark.browser
def test_account_double_request_restart_never_reactivates(browser):
    page, worker, tab, panel, isolated, app = browser
    result = panel.evaluate('id=>Promise.all([chrome.runtime.sendMessage({type:"autofill",tabId:id}),chrome.runtime.sendMessage({type:"autofill",tabId:id})])', tab)
    assert page.evaluate('accountClicks') == 1, result
    cdp = page.context.new_cdp_session(page)
    cdp.send('ServiceWorker.enable')
    cdp.send('ServiceWorker.stopAllWorkers')
    result = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert 'attempted' in result['progress'], result
    assert page.evaluate('accountClicks') == 1


@pytest.mark.browser
def test_observed_auth_transition_resumes_ordinary_fill(browser):
    page, worker, tab, panel, isolated, app = browser
    page.evaluate('transition=true')
    result = analyze(panel, tab)
    assert result.get('ok'), result
    expect(page.locator('#full')).to_have_value('Test Student', timeout=10000)
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]


@pytest.mark.browser
def test_ordinary_application_linkedin_is_not_account(browser):
    page, worker, tab, panel, isolated, app = browser
    page.evaluate("traps();document.querySelector('form').insertAdjacentHTML('afterbegin','<button type=button>Apply with LinkedIn</button>')")
    assert not isolated('active')
    result = analyze(panel, tab)
    assert result.get('ok'), result
    assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0]
