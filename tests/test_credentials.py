"""Synthetic credentials only; handler tests never open sockets or providers."""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from autoapply.credentials import CredentialStore, NAMES
from autoapply.dashboard_settings import SettingsRejected
from autoapply.extension_api import dispatch
from test_dashboard import dashboard, http_dashboard, request
from test_extension import loaded_extension

PASSWORD = '  synthetic!#$= % \\"quotes\\" \'single\' `$(literal) ${UNCHANGED} \\end  '
EMAIL = 'private-fixture@example.test'
ORIGIN = 'chrome-extension://' + 'a' * 32


def assert_no_credentials(value):
    if isinstance(value, str):
        assert PASSWORD not in value and EMAIL not in value
    elif isinstance(value, dict):
        for key, child in value.items():
            assert_no_credentials(key)
            assert_no_credentials(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_no_credentials(child)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def web(pair, payload, **headers):
    server, dashboard = pair
    return request(server, 'POST', '/api/credentials', {
        'Origin': 'http://127.0.0.1:8765', 'X-Dashboard-Token': dashboard.token,
        **headers}, json.dumps(payload))


def ext(pair, payload, **headers):
    server, dashboard = pair
    return request(server, 'POST', '/ext/credentials', {
        'Origin': ORIGIN, 'X-AutoApply-Key': dashboard.settings.extension_key(), **headers}, json.dumps(payload))


@pytest.fixture
def auth_job(config, db, listing):
    url = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_R123'
    job, _ = db.ingest(replace(listing, url=url), config)
    app = db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id']
    return dict(application_id=app, url=url + '/apply', frame_url=url + '/apply', mode='login')


def test_save_clear_write_only_and_separate(http_dashboard, config):
    pair = http_dashboard
    before = (config.private / 'profile.yaml').read_bytes()
    assert web(pair, {'email': EMAIL, 'password': PASSWORD})[:2] == (200, dict(
        configured=True, strict_configured=False, email_override=True, environment_override=False))
    store = CredentialStore(config)
    assert store.load() == dict(email=EMAIL, password=PASSWORD)
    assert (config.private / 'profile.yaml').read_bytes() == before
    assert not (config.private / 'backups').exists()
    data = request(pair[0], 'GET', '/api/settings')[1]
    assert_no_credentials(data)
    assert request(pair[0], 'GET', '/api/credentials')[0] == 404
    assert web(pair, {'clear': True})[1]['configured'] is False
    assert not store.path.exists()


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://evil.test'}, {'X-Dashboard-Token': 'wrong'},
    {'Sec-Fetch-Site': 'cross-site'}, {'Content-Length': '17000'}, {'Transfer-Encoding': 'chunked'}])
def test_settings_guards(http_dashboard, config, headers):
    status, value, _ = web(http_dashboard, {'email': EMAIL, 'password': PASSWORD}, **headers)
    assert status in {400, 403}
    assert PASSWORD not in json.dumps(value) and not CredentialStore(config).path.exists()


@pytest.mark.parametrize('value', [None, [], {'password': PASSWORD}, {'email': EMAIL, 'password': ''},
    {'email': EMAIL, 'password': PASSWORD, 'other': PASSWORD}, {'email': 'bad', 'password': PASSWORD},
    {'email': EMAIL, 'password': 'x' * 4097}, {'clear': False}, {'email': EMAIL, 'password': 'line\nbreak'}])
def test_invalid_fixed_errors(http_dashboard, value):
    status, result, _ = web(http_dashboard, value)
    assert status == 400 and PASSWORD not in json.dumps(result)


def test_atomic_rollback_and_clear_failure(config, monkeypatch):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=PASSWORD))
    original = store.path.read_bytes()
    monkeypatch.setattr('autoapply.credentials.os.replace', lambda *args: (_ for _ in ()).throw(OSError(PASSWORD)))
    with pytest.raises(SettingsRejected, match='could not be saved'):
        store.save(dict(email='', password='replacement'))
    assert store.path.read_bytes() == original
    assert not list(config.private.glob('.credentials-*'))
    unlink = Path.unlink
    def fail_clear(path, *args, **kwargs):
        if path == store.path:
            raise OSError(PASSWORD)
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_clear)
    with pytest.raises(SettingsRejected, match='could not be cleared'):
        store.save({'clear': True})
    assert store.path.read_bytes() == original


def test_env_literal_and_precedence(config, monkeypatch):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password='saved'))
    (config.root / '.env').write_text('UNRELATED=$(never execute)\nAUTOAPPLY_LOGIN_PASSWORD=' + PASSWORD + '\n', encoding='utf-8')
    assert store.load()['password'] == PASSWORD
    monkeypatch.setenv(NAMES[1], 'process override')
    assert store.load()['password'] == 'process override'
    assert store.save({'clear': True})['configured'] is True
    monkeypatch.setenv(NAMES[1], '')
    assert store.status()['configured'] is False


def test_invalid_environment_does_not_replace_saved(config, monkeypatch):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=PASSWORD))
    original = store.path.read_bytes()
    monkeypatch.setenv(NAMES[0], 'invalid email')
    with pytest.raises(SettingsRejected):
        store.save(dict(email='', password='replacement'))
    assert store.path.read_bytes() == original


def test_unicode_password_roundtrip(config):
    store = CredentialStore(config)
    password = 'fixture-\U0001f512' * 400
    store.save(dict(email='', password=password))
    assert store.load()['password'] == password


def test_release_missing_and_default_email(http_dashboard, auth_job):
    pair = http_dashboard
    assert ext(pair, auth_job)[:2] == (200, {'configured': False})
    pair[1].settings.credentials.save(dict(email='', password=PASSWORD))
    result = ext(pair, auth_job)
    assert result[0] == 200 and result[1] == dict(configured=True, email='student@example.test', password=PASSWORD)
    assert result[2]['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('change', [
    {'url': 'http://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply'},
    {'url': 'https://unrelated.test/login'}, {'url': 'https://fixture.myworkdayjobs.com/login'},
    {'frame_url': 'https://evil.test/login'}, {'frame_url': 'http://fixture.myworkdayjobs.com/login'},
    {'mode': 'reset'}, {'application_id': 999999}, {'application_id': None},
    {'frame_url': 'https://fixture.myworkdayjobs.com:444/login'}])
def test_release_context_denied(http_dashboard, auth_job, change):
    http_dashboard[1].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    status, data, _ = ext(http_dashboard, dict(auth_job, **change))
    assert status in {400, 403} and PASSWORD not in json.dumps(data) and EMAIL not in json.dumps(data)


def test_release_pairing_origin_method_body(http_dashboard, auth_job):
    for headers in ({'Origin': ''}, {'Origin': 'https://evil.test'}, {'Origin': ORIGIN + '/path'},
                    {'X-AutoApply-Key': 'wrong'}, {'Content-Length': '4097'}):
        assert ext(http_dashboard, auth_job, **headers)[0] in {400, 403}
    server, dashboard = http_dashboard
    assert request(server, 'GET', '/ext/credentials', {'Origin': ORIGIN,
        'X-AutoApply-Key': dashboard.settings.extension_key()})[0] == 404


def test_active_run_same_origin_only(http_dashboard, auth_job):
    dashboard = http_dashboard[1]
    dashboard.settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    dashboard._ext_run = [dict(application_id=auth_job['application_id'], state='opened', run_id='fixture-run')]
    login = dict(auth_job, url='https://fixture.myworkdayjobs.com/login', run_id='fixture-run')
    assert ext(http_dashboard, login)[0] == 200
    assert ext(http_dashboard, dict(login, run_id='stale'))[0] == 403
    assert ext(http_dashboard, dict(login, frame_url='https://other.myworkdayjobs.com/login'))[0] == 403


def test_all_dispatch_payloads_redacted(http_dashboard, monkeypatch):
    from autoapply import extension_api
    dashboard = http_dashboard[1]
    dashboard.settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    for (method, path) in list(extension_api.ROUTES):
        if path == '/ext/credentials':
            continue
        seen = []
        monkeypatch.setitem(extension_api.ROUTES, (method, path), lambda d, p: seen.append(p) or {})
        payload = dict(label='Ordinary mixed field', page={'description': EMAIL + PASSWORD}, value=PASSWORD, nested=[EMAIL])
        if path == '/ext/remember':
            with pytest.raises(SettingsRejected):
                dispatch(dashboard, payload, method=method, path=path)
        else:
            dispatch(dashboard, payload, method=method, path=path)
            assert_no_credentials(seen)


def test_store_permissions(config):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=PASSWORD))
    if os.name != 'nt':
        assert store.path.stat().st_mode & 0o777 == 0o600
    else:
        import subprocess
        acl = subprocess.check_output(['icacls', str(store.path)], text=True)
        assert '(I)' not in acl


@pytest.mark.browser
def test_actual_mv3_auth_forms_and_privacy(loaded_extension):
    context, worker, _ = loaded_extension
    fixture = Path(__file__).parent / 'fixtures/extension/credentials/auth.html'
    worker.evaluate('''config=>{
      globalThis.fixtureRequests=[];
      globalThis.fixtureConfigured=true;
      globalThis.fetch=async(url,options={})=>{
        const path=new URL(url).pathname,payload=options.body?JSON.parse(options.body):null;
        fixtureRequests.push({path,payload});
        const result=path==='/ext/match'?{application_id:1}:path==='/ext/credentials'?(fixtureConfigured?{configured:true,email:config.email,password:config.password}:{configured:false}):path==='/ext/answers'?{answers:[]}:path==='/ext/profile-sections'?{experience:[],projects:[],education:[],skills:[],links:{}}:path==='/ext/run/next'?{done:true}:path==='/ext/run/status'?{items:[]}:{};
        return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
      };
      return chrome.storage.local.set({server:'http://127.0.0.1:9876',key:'offline-fixture-key',autoAccountFill:false});
    }''', dict(email=EMAIL, password=PASSWORD))
    page = context.new_page()
    page.route('https://fixture.myworkdayjobs.com/**', lambda route: route.fulfill(path=str(fixture), content_type='text/html'))
    try:
        url = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply'
        page.goto(url)
        tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', url)
        def isolated(operation):
            return worker.evaluate('''async ({tab,operation})=>{
              for(let attempt=0;attempt<50;attempt++){
                const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',func:async operation=>{
                  if(!globalThis.autoApplyCredentials || !globalThis.autoApplyContent)return;
                  if(operation==='describe')return autoApplyCredentials.describe();
                  return {scan:await autoApplyContent.scan(),chat:await autoApplyContent.chatContext({request:'Ordinary application field'}),inspect:autoApplyContent.inspectPage(),snapshot:autoApplyContent.pageStructure()};
                },args:[operation]});
                if(frame.result)return frame.result;
                await new Promise(resolve=>setTimeout(resolve,50));
              }
              throw Error('Fixture isolated content API unavailable');
            }''', dict(tab=tab, operation=operation))
        def autofill():
            return worker.evaluate('id=>autoApplyAutofill(id)', tab)
        result = autofill()
        assert not result.get('error'), result
        assert 'Account action activated' in result['progress']
        assert page.locator('#email').input_value() == EMAIL
        assert page.locator('#password').input_value() == PASSWORD
        assert page.locator('#confirm').input_value() == ''
        assert page.evaluate('reactState.email') == EMAIL and page.evaluate('reactState.password') == PASSWORD
        assert page.evaluate('events') == ['email:input', 'email:change', 'password:input', 'password:change']
        assert page.evaluate('[submissions,oauthClicks,agreement.checked]') == [1, 0, False]
        page.locator('#mixed').fill(EMAIL)
        page.locator('#mixed').blur()
        private = isolated('privacy')
        assert private['scan']['fields'] == private['chat']['fields'] == private['snapshot']['nodes'] == []
        saved = worker.evaluate('async()=>({storage:await chrome.storage.session.get(null),requests:fixtureRequests})')
        for payload in (result, private, saved):
            assert_no_credentials(payload)
        assert not [r for r in saved['requests'] if r['path'] in {'/ext/remember','/ext/answers','/ext/chat','/ext/write'}]
        page.reload()
        page.evaluate('signup()')
        result = autofill()
        assert 'Account action activated' in result['progress']
        assert page.locator('#confirm').input_value() == PASSWORD
        assert page.evaluate('reactState.confirm') == PASSWORD
        assert page.evaluate('[submissions,oauthClicks,agreement.checked]') == [1, 0, False]
        page.reload()
        page.evaluate('signup()')
        page.locator('#password').fill('occupied-fixture-password')
        page.locator('#confirm').fill('occupied-confirmation')
        autofill()
        assert page.locator('#password').input_value() == 'occupied-fixture-password'
        assert page.locator('#confirm').input_value() == 'occupied-confirmation'
        page.reload()
        page.locator('#email').fill('occupied@example.test')
        autofill()
        assert page.locator('#email').input_value() == 'occupied@example.test'
        assert page.locator('#password').input_value() == ''
        for heading in ('Reset password', 'Change password', 'Unrelated shopping account'):
            page.reload()
            page.evaluate('heading=>{document.querySelector("h1").textContent=heading;document.querySelector("#submit").textContent=heading;}', heading)
            count = worker.evaluate('fixtureRequests.filter(r=>r.path==="/ext/credentials").length')
            autofill()
            assert page.locator('#password').input_value() == ''
            assert worker.evaluate('fixtureRequests.filter(r=>r.path==="/ext/credentials").length') == count
        page.reload()
        page.locator('form').evaluate('form=>form.insertAdjacentHTML("beforeend",\'<input autocomplete="one-time-code">\')')
        assert isolated('describe')['supported'] is False
        assert page.evaluate('[submissions,oauthClicks,agreement.checked]') == [0, 0, False]
        page.reload()
        page.locator('form').evaluate('form=>form.action="http://fixture.myworkdayjobs.com/insecure"')
        assert isolated('describe')['supported'] is False
        page.reload()
        page.locator('#password').evaluate('input=>input.readOnly=true')
        description = isolated('describe')
        assert description['active'] and not description['supported']
        assert isolated('privacy')['scan']['fields'] == []
        page.reload()
        worker.evaluate('fixtureConfigured=false')
        missing = autofill()
        assert 'Set your account password' in missing['progress']
        assert page.locator('#password').input_value() == ''
        worker.evaluate('fixtureConfigured=true')
        # A form appearing during an active wait is filled once, then resumes
        # ordinary application filling when the user completes authentication.
        page.reload()
        page.locator('#password').evaluate('input=>input.disabled=true')
        attempts = worker.evaluate('fixtureRequests.filter(r=>r.path==="/ext/credentials").length')
        worker.evaluate('id=>autoApplyAutofill(id,{application_id:1,run_id:"fixture-run"})', tab)
        assert worker.evaluate('fixtureRequests.filter(r=>r.path==="/ext/credentials").length') == attempts
        page.locator('#password').evaluate('input=>input.disabled=false')
        from playwright.sync_api import expect
        expect(page.locator('#password')).to_have_value(PASSWORD)
        page.wait_for_timeout(1200)
        assert worker.evaluate('fixtureRequests.filter(r=>r.path==="/ext/credentials").length') == attempts + 1
        page.evaluate('document.querySelector("main").innerHTML="<h1>Job application</h1><form><label>Full name<input id=normal></label></form>"')
        for _ in range(70):
            current = worker.evaluate('async()=>{const {lastResult}=await chrome.storage.session.get("lastResult");return lastResult; }')
            if current and not current.get('waiting') and not current.get('busy') and not current.get('sign_in'):
                break
            page.wait_for_timeout(100)
        assert not current.get('waiting') and not current.get('error') and not current.get('sign_in'), current
        page.locator('#normal').fill(EMAIL)
        assert_no_credentials(isolated('privacy'))
        page.wait_for_timeout(500)
        assert_no_credentials(worker.evaluate('async()=>({storage:await chrome.storage.session.get(null),requests:fixtureRequests})'))
    finally:
        page.close()


@pytest.mark.browser
def test_toolbar_injects_missing_credentials_before_content(loaded_extension):
    from playwright.sync_api import expect
    context, worker, _ = loaded_extension
    fixture = Path(__file__).parent / 'fixtures/extension/credentials/auth.html'
    worker.evaluate('''async config=>{
      await chrome.storage.session.set({chromeRunPaused:true});
      globalThis.fixtureRequests=[];
      globalThis.fetch=async(url,options={})=>{
        const path=new URL(url).pathname,payload=options.body?JSON.parse(options.body):null;
        fixtureRequests.push({path,payload});
        const result=path==='/ext/match'?{application_id:1}:path==='/ext/credentials'?{configured:true,email:config.email,password:config.password}:path==='/ext/run/status'?{items:[]}:{};
        return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
      };
      await chrome.storage.local.set({server:'http://127.0.0.1:9876',key:'offline-fixture-key',autoAccountFill:false});
    }''', dict(email=EMAIL, password=PASSWORD))
    page = context.new_page()
    page.route('https://fixture.myworkdayjobs.com/**', lambda route: route.fulfill(path=str(fixture), content_type='text/html'))
    try:
        page.goto('https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply')
        tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
        worker.evaluate('''async tab=>{
          for(let attempt=0;attempt<50;attempt++){
            const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',func:()=>{
              if(!globalThis.autoApplyCredentials || !globalThis.autoApplyContent)return false;
              delete globalThis.autoApplyCredentials;
              delete globalThis.autoApplyNativeRoots;
              delete globalThis.autoApplyContent;
              delete globalThis[Symbol.for('autoapply.content')];
              return !globalThis.autoApplyNativeRoots && !globalThis.autoApplyCredentials && !globalThis.autoApplyContent && !globalThis[Symbol.for('autoapply.content')];
            }});
            if(frame.result)return;
            await new Promise(resolve=>setTimeout(resolve,50));
          }
          throw Error('Could not simulate missing content scripts on loaded fixture');
        }''', tab)
        worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
        expect(page.locator('#autoapply-sidebar')).to_be_visible()
        worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
        expect(page.locator('#autoapply-sidebar')).to_have_count(1)
        result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
        assert not result.get('error'), result
        assert result['state'] == 'needs_input' and result['sign_in']
        assert 'Account fields filled' in result['progress']
        expect(page.locator('#email')).to_have_value(EMAIL)
        expect(page.locator('#password')).to_have_value(PASSWORD)
        expect(page.locator('#confirm')).to_have_value('')
        page.locator('#mixed').fill(EMAIL)
        page.locator('#mixed').blur()
        private = worker.evaluate('''async tab=>{
          const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',func:async()=>{
            // These wrappers are installed only when credentials exists at content initialization.
            const redact=autoApplyCredentials.redact;
            let calls=0;
            autoApplyCredentials.redact=value=>{calls++;return redact(value);};
            try {
              const outputs={},guarded={};
              for(const name of ['scan','chatContext','inspectPage','pageStructure']){
                calls=0;
                outputs[name]=await autoApplyContent[name]({request:'Ordinary application field'});
                guarded[name]=calls>0;
              }
              return {outputs,guarded};
            } finally {autoApplyCredentials.redact=redact;}
          }});
          return frame.result;
        }''', tab)
        assert all(private['guarded'].values())
        outputs = private['outputs']
        assert outputs['scan']['sign_in']
        assert outputs['scan']['fields'] == outputs['chatContext']['fields'] == outputs['inspectPage']['fields'] == outputs['pageStructure']['nodes'] == []
        saved = worker.evaluate('async()=>({storage:await chrome.storage.session.get(null),requests:fixtureRequests})')
        for payload in (result, private, saved):
            assert_no_credentials(payload)
        assert not [r for r in saved['requests'] if r['path'] in {'/ext/remember','/ext/answers','/ext/chat','/ext/write','/ext/applied'}]
        assert page.evaluate('[submissions,oauthClicks,agreement.checked]') == [0, 0, False]
        expect(page.locator('#autoapply-sidebar')).to_have_count(1)
    finally:
        page.close()


@pytest.mark.browser
def test_settings_credential_controls_actual_browser(loaded_extension, http_dashboard):
    from playwright.sync_api import expect
    context, _, _ = loaded_extension
    server, dashboard = http_dashboard
    page = context.new_page()
    def respond(route):
        incoming = route.request
        target = incoming.url.removeprefix('http://127.0.0.1:8765')
        status, body, headers = request(server, incoming.method, target,
            {k.title(): v for k, v in incoming.headers.items()}, incoming.post_data)
        encoded = body if isinstance(body, str) else json.dumps(body)
        route.fulfill(status=status, body=encoded, headers={k:v for k,v in headers.items() if k.lower() != 'content-length'})
    page.route('http://127.0.0.1:8765/**', respond)
    try:
        page.goto('http://127.0.0.1:8765/')
        page.locator('#tab-settings').click()
        page.get_by_role('button', name='Account credentials', exact=True).click()
        card = page.locator('#settings-section-credentials')
        expect(card.get_by_role('status')).to_contain_text('No password configured.')
        assert page.locator('#account-password').get_attribute('type') == 'password'
        page.locator('#account-email').fill(EMAIL)
        page.locator('#account-password').fill(PASSWORD)
        card.get_by_role('button', name='Save account credentials', exact=True).click()
        expect(card.get_by_role('status')).to_contain_text('Password configured.')
        expect(page.locator('#account-password')).to_have_value('')
        expect(page.locator('#account-email')).to_have_value('')
        assert dashboard.settings.credentials.load()['password'] == PASSWORD
        page.reload()
        page.locator('#tab-settings').click()
        page.get_by_role('button', name='Account credentials', exact=True).click()
        expect(page.locator('#account-password')).to_have_value('')
        expect(page.locator('#account-email')).to_have_value('')
        card.get_by_role('button', name='Clear saved credentials', exact=True).click()
        expect(card.get_by_role('status')).to_contain_text('No password configured.')
        assert not dashboard.settings.credentials.path.exists()
    finally:
        page.close()
