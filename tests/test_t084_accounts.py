"""Focused synthetic T084 account release and offline MV3 flows."""
import json
import os

import pytest
from playwright.sync_api import expect

from autoapply.credentials import CredentialStore, NAMES
from autoapply.dashboard_settings import SettingsRejected
from test_credentials import auth_job, ext, http_dashboard, web
from test_t078_accounts import (
    EMAIL, PASSWORD, URL, backend, browser, dashboard, loaded_extension, offline_descriptions,
)
from test_t082_account_surface import surface_browser
from test_extension import extension_server, FIXTURES, KEY

STRICT = 'T084-Strict-only!7'
WEAK = 'legacy-password'
ORIGIN = 'https://fixture.myworkdayjobs.com'


@pytest.fixture(autouse=True)
def clean_credentials_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def test_strict_validation_and_legacy_compatibility(config):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=WEAK))
    original = store.path.read_bytes()
    for invalid in ('Short!7a', 'alllowercase!7', 'ALLUPPERCASE!7', 'NoNumbersHere!', 'NoSpecialHere7', ['invalid']):
        with pytest.raises(SettingsRejected):
            store.save(dict(email='', password='', strict_password=invalid))
        assert store.path.read_bytes() == original
    status = store.save(dict(email='', password='', strict_password=STRICT))
    assert status == dict(configured=True, strict_configured=True, email_override=True, environment_override=False)
    assert store.load() == dict(email=EMAIL, password=WEAK)
    assert CredentialStore(config).load(include_strict=True)['strict_password'] == STRICT
    if os.name != 'nt':
        assert store.path.stat().st_mode & 0o777 == 0o600
    assert not store.save({'clear': True})['strict_configured']
    assert not store.path.exists()


def test_strict_atomic_redaction_and_literal_env(config, monkeypatch):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=WEAK, strict_password=STRICT))
    original = store.path.read_bytes()
    monkeypatch.setattr('autoapply.credentials.os.replace', lambda *args: (_ for _ in ()).throw(OSError(STRICT)))
    with pytest.raises(SettingsRejected, match='could not be saved') as error:
        store.save(dict(email='', password='', strict_password=STRICT + '2'))
    assert STRICT not in str(error.value) and store.path.read_bytes() == original
    assert not list(config.private.glob('.credentials-*'))
    literal = 'T084!7a`$(literal)'
    (config.root / '.env').write_text('AUTOAPPLY_LOGIN_STRICT_PASSWORD=' + literal + '\n', encoding='utf-8')
    assert store.load(include_strict=True)['strict_password'] == literal
    assert store.redact([EMAIL, WEAK, STRICT, literal]) == ['[private account value]'] * 4


def test_private_selection_preview_and_signin(http_dashboard, auth_job):
    store = http_dashboard[1].settings.credentials
    store.save(dict(email=EMAIL, password=WEAK))
    request = dict(auth_job, mode='signup', password_policy='strict',
                   account_action=dict(intent='auto_fill', source_url=auth_job['url']))
    assert ext(http_dashboard, request)[:2] == (200, dict(configured=False, strict_required=True))
    assert web(http_dashboard, dict(email='', password='', strict_password=STRICT))[0] == 200
    before = store.path.read_bytes()
    assert ext(http_dashboard, request)[1] == dict(configured=True, email=EMAIL, password=STRICT, variant='strict')
    assert store.path.read_bytes() == before  # Preview cannot remap an existing sign-in.
    login = dict(request, mode='login', password_policy='standard')
    assert ext(http_dashboard, login)[1]['password'] == WEAK
    assert ext(http_dashboard, dict(request, remember_variant='strict'))[0] == 403
    remember = dict(request, remember_variant='strict', account_action=dict(intent='autofill', source_url=auth_job['url']))
    assert ext(http_dashboard, remember)[:2] == (200, dict(remembered=True))
    assert ext(http_dashboard, login)[1]['password'] == STRICT
    assert CredentialStore(store.config).select(ORIGIN, 'login', 'standard')['password'] == STRICT
    assert store.select('https://other.myworkdayjobs.com', 'login', 'standard')['password'] == WEAK
    store.save(dict(email='other@example.test', password=WEAK))
    assert store.select(ORIGIN, 'login', 'standard')['password'] == WEAK


def test_strict_policy_keeps_release_guards_and_private_status(http_dashboard, auth_job):
    store = http_dashboard[1].settings.credentials
    store.save(dict(email=EMAIL, password=WEAK, strict_password=STRICT))
    payload = dict(auth_job, password_policy='strict')
    for change in ({'password_policy': []}, {'password_policy': {}}, {'password_policy': True},
                   {'remember_variant': []}, {'url': 'https://arbitrary.test/login'},
                   {'frame_url': 'https://other.myworkdayjobs.com/login'}, {'mode': 'reset'}):
        status, result, _ = ext(http_dashboard, dict(payload, **change))
        assert status == 403 and STRICT not in json.dumps(result)
    from test_dashboard import request
    settings = request(http_dashboard[0], 'GET', '/api/settings')[1]
    assert settings['credentials']['strict_configured'] is True
    assert all(secret not in json.dumps(settings) for secret in (EMAIL, WEAK, STRICT))


def test_strong_standard_does_not_require_alternative(config):
    store = CredentialStore(config)
    store.save(dict(email=EMAIL, password=PASSWORD))
    assert store.select(ORIGIN, 'signup', 'strict')['variant'] == 'standard'
    store.remember(ORIGIN, 'standard')
    store.save(dict(email='', password='', strict_password=STRICT))
    assert store.select(ORIGIN, 'login', 'standard')['password'] == PASSWORD


def current_panel(page):
    page.frame_locator('#autoapply-sidebar iframe').locator('#send-structure').wait_for(timeout=5000)
    return next(f for f in page.frames if '/sidepanel.html' in f.url)


def enable_auto(page, worker, mode='signup', strict=False):
    page.evaluate('v=>sessionStorage.setItem("t084setup",JSON.stringify(v))', dict(mode=mode, strict=strict))
    page.add_init_script('''addEventListener('DOMContentLoaded',()=>{
      const setup=JSON.parse(sessionStorage.getItem('t084setup') || '{}');
      if(setup.mode==='login')login();
      if(setup.strict)password.minLength=12;
    },{once:true});''')
    worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
    page.reload()
    return current_panel(page)


def private(value):
    assert all(secret not in json.dumps(value) for secret in (EMAIL, PASSWORD, WEAK, STRICT))


@pytest.mark.browser
@pytest.mark.parametrize('mode,count', [('signup', 3), ('login', 2)])
def test_automatic_account_fill_without_activation(browser, mode, count):
    page, worker, tab, panel, isolated, _ = browser
    panel = enable_auto(page, worker, mode=mode)
    expect(page.locator('#email')).to_have_value(EMAIL)
    expect(page.locator('#password')).to_have_value(PASSWORD)
    page.wait_for_timeout(600)
    assert isolated('describe')['filled'] == count
    assert page.evaluate('[accountClicks,applicationClicks,marketing.checked]') == [0, 0, False]
    assert page.locator('#consent').is_checked()
    assert not worker.evaluate('async()=>(await chrome.storage.session.get("accountActionClaims")).accountActionClaims')
    state = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert state['summary']['filled'] == count and state['accountMode'] == mode
    private(state)


@pytest.mark.browser
def test_late_account_arrival_retries_after_busy(surface_browser, backend):
    page, worker, tab, panel, isolated, _ = surface_browser
    mutex = backend[0]._ext_mutex
    mutex.acquire()
    try:
        worker.evaluate('()=>{void autoApplyRunTick();return true;}')
        page.wait_for_timeout(100)
        worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
        page.evaluate("globalThis.savedForm=accountForm.outerHTML;accountForm.remove()")
        page.wait_for_timeout(350)
        page.evaluate("accountSurface.insertAdjacentHTML('afterbegin',savedForm)")
        page.wait_for_timeout(1100)
        expect(page.locator('#password')).to_have_value('')
    finally:
        mutex.release()
    expect(page.locator('#password')).to_have_value(PASSWORD, timeout=5000)
    expect(panel.locator('#autofill')).to_contain_text('3 fields filled')
    assert isolated('describe')['filled'] == 3
    assert page.evaluate('[accountClicks,applicationClicks,marketing.checked]') == [0, 0, False]
    assert not worker.evaluate('async()=>(await chrome.storage.session.get("accountActionClaims")).accountActionClaims')
    private(worker.evaluate('async()=>await chrome.storage.session.get(null)'))


@pytest.mark.browser
def test_late_account_footer_once_and_stable_counts(surface_browser):
    page, worker, tab, panel, isolated, _ = surface_browser
    worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
    page.evaluate("globalThis.lateForm=document.querySelector('form').outerHTML;document.querySelector('form').remove();globalThis.latePrimary=document.querySelector('#primary');latePrimary.remove()")
    page.wait_for_timeout(350)
    page.evaluate("accountSurface.insertAdjacentHTML('afterbegin',lateForm)")
    expect(page.locator('#password')).to_have_value(PASSWORD)
    expect(panel.locator('#next-page')).to_have_text('Create account')
    expect(panel.locator('#autofill')).to_contain_text('3 fields filled')
    assert page.evaluate('accountClicks') == 0
    page.evaluate("accountSurface.append(latePrimary)")
    page.wait_for_timeout(800)
    expect(panel.locator('#autofill')).to_contain_text('3 fields filled')
    panel.locator('#next-page').click()
    expect(page.locator('#password')).to_have_value(PASSWORD)
    page.wait_for_timeout(600)
    assert page.evaluate('[accountClicks,applicationClicks,ordered]') == [1, 0, [True]]
    panel.locator('#next-page').click()
    page.wait_for_timeout(400)
    assert page.evaluate('accountClicks') == 1
    assert isolated('describe')['filled'] == 3
    private(worker.evaluate('async()=>await chrome.storage.session.get(null)'))


@pytest.mark.browser
def test_strict_signup_preview_then_footer_and_signin(browser, backend):
    page, worker, tab, panel, isolated, _ = browser
    store = backend[0].settings.credentials
    store.save(dict(email=EMAIL, password=WEAK, strict_password=STRICT))
    panel = enable_auto(page, worker, strict=True)
    expect(page.locator('#password')).to_have_value(STRICT)
    expect(page.locator('#confirm')).to_have_value(STRICT)
    assert store.select(ORIGIN, 'login', 'standard')['password'] == WEAK
    expect(panel.locator('#next-page')).to_have_text('Create account')
    panel.locator('#next-page').click()
    page.wait_for_timeout(700)
    assert page.evaluate('accountClicks') == 1
    assert store.select(ORIGIN, 'login', 'standard')['password'] == STRICT
    page.evaluate('()=>sessionStorage.setItem("t084setup",JSON.stringify({mode:"login"}))')
    page.reload()
    panel = current_panel(page)
    expect(page.locator('#password')).to_have_value(STRICT)
    expect(panel.locator('#next-page')).to_have_text('Sign in')
    panel.locator('#next-page').click()
    page.wait_for_timeout(600)
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]
    assert isolated('describe')['filled'] == 2
    private(worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})'))
    page.evaluate('showApplication()')
    expect(page.locator('#full')).to_have_value('Test Student', timeout=5000)
    assert page.evaluate('applicationClicks') == 0


@pytest.mark.browser
def test_auto_abstains_after_edits_pause_and_ordinary_form(browser):
    page, worker, tab, panel, isolated, _ = browser
    page.locator('#email').fill('manual@example.test')
    worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
    page.evaluate("password.minLength=12")
    page.wait_for_timeout(900)
    expect(page.locator('#password')).to_have_value('')
    worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:true})')
    page.reload()
    page.wait_for_timeout(900)
    expect(page.locator('#email')).to_have_value('')
    worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:false})')
    page.evaluate('showApplication()')
    page.wait_for_timeout(900)
    expect(page.locator('#full')).to_have_value('')
    assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0]


@pytest.mark.browser
def test_footer_keeps_explicit_submit_route(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server, key=KEY))
    page = context.new_page()
    try:
        page.route('**/t054.html', lambda route: route.fulfill(path=str(FIXTURES / 't054.html'), content_type='text/html'))
        page.goto(server + '/t054.html')
        tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
        state = worker.evaluate('id=>autoApplyAutofill(id)', tab)
        panel = current_panel(page)
        assert state['next_action']['kind'] == 'submit'
        expect(panel.locator('#next-page')).to_have_text('Submit')
        automatic = worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},
          func:()=>autoApplyContent.nextPage(true)}))[0].result''', tab)
        assert not automatic['clicked'] and page.evaluate('clicks') == 0
        panel.evaluate('''()=>{const send=chrome.runtime.sendMessage.bind(chrome.runtime);
          globalThis.footerRoutes=[];chrome.runtime.sendMessage=m=>{footerRoutes.push(m.type);return send(m);};}''')
        page.evaluate("mode='invalid'")
        panel.locator('#next-page').click()
        expect(panel.locator('#progress')).to_contain_text('Required information missing')
        assert 'manual-submit' in panel.evaluate('footerRoutes') and page.evaluate('clicks') == 1
    finally:
        page.close()
