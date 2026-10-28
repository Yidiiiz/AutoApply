"""T080: synthetic handler checks and actual MV3 account-wait lifecycle cases."""
from dataclasses import replace
import json
import threading
import time

import pytest

from autoapply.dashboard import DashboardHandler
from test_credentials import auth_job, ext, http_dashboard, isolated_env
from test_t078_accounts import (
    EMAIL, PASSWORD, URL, analyze, backend, browser, dashboard,
    loaded_extension, offline_descriptions,
)

MANUAL = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Manual_T080/apply'
LOGIN = 'https://fixture.myworkdayjobs.com/login'


def action(url=MANUAL, **extra):
    return dict(url=url, frame_url=url, mode='signup',
                account_action=dict(intent='analyze', source_url=url), **extra)


def test_unsaved_account_handler_does_not_create_job(http_dashboard, db):
    http_dashboard[1].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    before = db.rows('SELECT id FROM applications')
    status, value, headers = ext(http_dashboard, action())
    assert status == 200 and value == dict(configured=True, email=EMAIL, password=PASSWORD)
    assert headers['Cache-Control'] == 'no-store'
    assert db.rows('SELECT id FROM applications') == before
    assert not http_dashboard[1]._ext_run


@pytest.mark.parametrize('change', [
    dict(account_action=None), dict(account_action={'intent': 'poll', 'source_url': MANUAL}),
    dict(account_action={'intent': 'analyze', 'source_url': 'https://other.myworkdayjobs.com/login'}),
    *[dict(account_action={'intent': 'analyze', 'source_url': value})
      for value in (None, False, 123, [], {})],
    dict(url='http://fixture.myworkdayjobs.com/login'),
    dict(url='https://fixture.myworkdayjobs.com:444/login'),
    dict(url='https://user@fixture.myworkdayjobs.com/login'),
    dict(url='https://shopping.test/login'), dict(frame_url='https://other.myworkdayjobs.com/login'),
    dict(frame_url='http://fixture.myworkdayjobs.com/login'), dict(mode='reset'),
    dict(application_id=None), dict(application_id=999999), dict(run_id='unowned'),
])
def test_unsaved_release_denies_unbounded_context(http_dashboard, change):
    http_dashboard[1].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    status, value, _ = ext(http_dashboard, dict(action(), **change))
    assert status in {400, 403}
    assert PASSWORD not in json.dumps(value) and EMAIL not in json.dumps(value)


def test_stored_id_stays_strict_and_manual_redirect_needs_source(http_dashboard, auth_job):
    http_dashboard[1].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    payload = action(LOGIN, application_id=auth_job['application_id'])
    assert ext(http_dashboard, payload)[0] == 403
    payload['account_action']['source_url'] = auth_job['url']
    assert ext(http_dashboard, payload)[0] == 200
    assert ext(http_dashboard, dict(payload, run_id='wrong'))[0] == 403
    assert ext(http_dashboard, dict(payload, application_id=999999))[0] == 403
    wrong = action(MANUAL, application_id=auth_job['application_id'])
    assert ext(http_dashboard, wrong)[0] == 403


def test_no_id_cannot_bypass_protected_job(http_dashboard, auth_job, monkeypatch):
    monkeypatch.setattr('autoapply.dashboard.PROTECTED_APPLICATIONS', {auth_job['application_id']})
    for payload in (action(auth_job['url']), dict(action(auth_job['url']), application_id=auth_job['application_id']),
                    dict(action(LOGIN), account_action=dict(intent='analyze', source_url=auth_job['url']))):
        assert ext(http_dashboard, payload)[0] == 403


def test_saved_account_rejects_other_stored_job(http_dashboard, auth_job, db, config, listing):
    http_dashboard[1].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    job, _ = db.ingest(replace(listing, url=MANUAL.removesuffix('/apply')), config)
    other = db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id']
    assert other != auth_job['application_id']
    before = db.rows('SELECT id FROM applications')
    payload = action(MANUAL, application_id=auth_job['application_id'])
    payload['account_action']['source_url'] = auth_job['url']
    status, value, _ = ext(http_dashboard, payload)
    assert status == 403
    assert EMAIL not in json.dumps(value) and PASSWORD not in json.dumps(value)
    # A conflicting saved source also cannot authorize another application's page.
    payload = action(auth_job['url'], application_id=auth_job['application_id'])
    payload['account_action']['source_url'] = MANUAL
    assert ext(http_dashboard, payload)[0] == 403
    payload = action(LOGIN, application_id=auth_job['application_id'])
    payload['account_action']['source_url'] = auth_job['url']
    assert ext(http_dashboard, payload)[0] == 200
    assert db.rows('SELECT id FROM applications') == before


@pytest.mark.browser
def test_unsaved_manual_fresh_analyze(browser, db):
    page, worker, tab, panel, isolated, _ = browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    assert page.evaluate('[accountClicks,ordered,marketing.checked,applicationClicks]') == [1, [True], False, 0]
    assert db.rows('SELECT id FROM applications') == before
    saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert saved['matched']['application_id'] is None
    assert EMAIL not in json.dumps(saved) and PASSWORD not in json.dumps(saved)


@pytest.mark.browser
@pytest.mark.parametrize('label, automation', [
    ('Verify New Password', False), ('Verify Password', False), ('Password', True),
    ('Verify New Password', True),
])
def test_unsaved_verify_password_analyze(browser, db, label, automation):
    page, worker, tab, panel, isolated, _ = browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate('''({url,label,automation})=>{
      history.pushState({},'',url);
      const input=document.querySelector('#confirm'), wrapper=input.parentElement;
      wrapper.id='secondaryPasswordLabel';
      wrapper.firstChild.textContent=label;
      input.id=automation?'verifyPassword':'secondaryPassword';
      if(automation)input.setAttribute('data-automation-id','verifyPassword');
    }''', dict(url=MANUAL, label=label, automation=automation))
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(el=>el.value)') == [PASSWORD, PASSWORD]
    assert page.evaluate('[accountClicks,ordered,consent.checked,marketing.checked,applicationClicks]') == [1, [True], True, False, 0]
    again = analyze(panel, tab)
    assert 'attempted' in again.get('summary', '') or 'busy' in again.get('error', '').lower(), again
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]
    assert db.rows('SELECT id FROM applications') == before
    private = isolated('private')
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    assert saved['session']['tabResults'][str(tab)]['matched']['application_id'] is None
    for value in (result, again, private, saved):
        assert EMAIL not in json.dumps(value) and PASSWORD not in json.dumps(value)


@pytest.mark.browser
@pytest.mark.parametrize('change', ['reversed_label', 'reversed_identity', 'unknown'])
def test_unsaved_ambiguous_passwords_abstain(browser, db, change):
    page, worker, tab, panel, isolated, _ = browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate('''({url,change})=>{
      history.pushState({},'',url);
      const input=document.querySelector('#confirm'), wrapper=input.parentElement;
      wrapper.id='secondaryPasswordLabel';
      wrapper.firstChild.textContent=change==='reversed_label'?'Verify New Password':'Additional password';
      input.id='secondaryPassword';
      if(change==='reversed_identity')input.setAttribute('data-automation-id','verifyPassword');
      if(change!=='unknown')wrapper.parentElement.insertBefore(wrapper,document.querySelector('#password').parentElement);
    }''', dict(url=MANUAL, change=change))
    assert not isolated('describe')['supported']
    result = analyze(panel, tab)
    assert result.get('ok'), result
    assert page.locator('#email').input_value() == ''
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(el=>el.value)') == ['', '']
    assert page.evaluate('[accountClicks,applicationClicks,consent.checked,marketing.checked]') == [0, 0, False, False]
    assert db.rows('SELECT id FROM applications') == before
    assert EMAIL not in json.dumps(result) and PASSWORD not in json.dumps(result)


@pytest.mark.browser
def test_new_manual_job_does_not_inherit_another_job(browser, backend):
    page, worker, tab, panel, isolated, _ = browser
    backend[0].settings.credentials.save({'clear': True})
    first = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert first['matched']['application_id'] is not None
    page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert saved['matched']['application_id'] is None
    assert saved['account_action']['source_url'] == MANUAL
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]


@pytest.mark.browser
@pytest.mark.parametrize('configured', [False, True])
def test_autofill_wait_analyze_recovers_once(browser, backend, configured):
    page, worker, tab, panel, isolated, _ = browser
    page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    if not configured:
        backend[0].settings.credentials.save({'clear': True})
    first = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert first['waiting'] and not first['busy'], first
    if not configured:
        assert 'Settings' in first['progress']
        assert page.evaluate('accountClicks') == 0
        backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    results = panel.evaluate('id=>Promise.all([1,2].map(()=>chrome.runtime.sendMessage({type:"page-snapshot",tabId:id})))', tab)
    assert all(r.get('ok') for r in results), results
    page.wait_for_timeout(1400)
    again = analyze(panel, tab)
    assert again.get('ok') and 'already attempted' in again['summary'], again
    assert page.evaluate('[accountClicks,ordered,applicationClicks]') == [1, [True], 0]
    cdp = page.context.new_cdp_session(page)
    cdp.send('ServiceWorker.enable')
    cdp.send('ServiceWorker.stopAllWorkers')
    restarted = analyze(panel, tab)
    assert restarted.get('ok') and 'already attempted' in restarted['summary'], restarted
    assert page.evaluate('accountClicks') == 1


@pytest.mark.browser
@pytest.mark.parametrize('saved_job', [False, True])
def test_shared_login_preserves_manual_context_without_run(browser, backend, saved_job):
    page, worker, tab, panel, isolated, app = browser
    if not saved_job:
        page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    backend[0].settings.credentials.save({'clear': True})
    first = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert first['waiting'] and 'Settings' in first['progress'], first
    page.evaluate('url=>history.pushState({},"",url)', LOGIN)
    page.wait_for_timeout(1200)
    assert page.evaluate('accountClicks') == 0
    backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert saved['matched']['application_id'] == (app if saved_job else None)
    assert not saved['matched'].get('run_id')
    assert saved['account_action']['source_url'] == (URL if saved_job else MANUAL)
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]


@pytest.mark.browser
def test_null_id_current_rematches_newly_saved_job(browser, backend, db, config, listing):
    page, worker, tab, panel, isolated, _ = browser
    page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    backend[0].settings.credentials.save({'clear': True})
    first = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert first['matched']['application_id'] is None
    job, _ = db.ingest(replace(listing, url=MANUAL.removesuffix('/apply')), config)
    app = db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id']
    backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert saved['matched']['application_id'] == app
    assert page.evaluate('accountClicks') == 1


@pytest.mark.browser
@pytest.mark.parametrize('change', ['cancel', 'navigation', 'edit'])
def test_wait_poll_cannot_renew_changed_authorization(browser, backend, change):
    page, worker, tab, panel, isolated, _ = browser
    backend[0].settings.credentials.save({'clear': True})
    first = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
    assert first['waiting']
    if change == 'cancel':
        panel.evaluate('id=>chrome.runtime.sendMessage({type:"cancel-form-wait",tabId:id})', tab)
    elif change == 'navigation':
        page.evaluate('history.pushState({},"","/login")')
    else:
        page.locator('#email').fill('manual@example.test')
    backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    page.wait_for_timeout(1600)
    assert page.evaluate('[accountClicks,applicationClicks]') == [0, 0]


@pytest.mark.browser
def test_analyze_still_rejects_running_autofill(browser, backend, monkeypatch):
    page, worker, tab, panel, isolated, _ = browser
    entered, release = threading.Event(), threading.Event()
    original = DashboardHandler.do_POST
    def delayed(handler):
        if handler.path == '/ext/credentials' and handler.server.dashboard is backend[0]:
            entered.set()
            release.wait(10)
        return original(handler)
    monkeypatch.setattr(DashboardHandler, 'do_POST', delayed)
    try:
        panel.evaluate('''id=>{globalThis.pendingFill=chrome.runtime.sendMessage({type:'autofill',tabId:id});}''', tab)
        deadline = time.monotonic() + 5
        while not entered.is_set() and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert entered.is_set()
        result = analyze(panel, tab)
        assert 'busy' in result.get('error', '').lower(), result
        assert page.evaluate('accountClicks') == 0
    finally:
        release.set()
    panel.evaluate('()=>pendingFill')
    assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]
