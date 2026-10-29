"""T081: real MV3, native Workday navigation buttons, local backend only."""
import json
from pathlib import Path
import time

import pytest

from autoapply.dashboard_settings import SettingsRejected
from autoapply.extension_api import trace
from test_t078_accounts import (
    EMAIL, PASSWORD, analyze, backend, browser, dashboard, loaded_extension,
    offline_descriptions,
)

MANUAL = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Manual_T081/apply'


@pytest.fixture
def native_browser(monkeypatch, request):
    monkeypatch.setattr('test_t078_accounts.FIXTURE', Path(__file__).parent / 'fixtures/extension/t081.html')
    return request.getfixturevalue('browser')


def account_rows(page, config):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        rows = [row for path in (config.private / 'traces').glob('*.json')
                for row in json.loads(path.read_text(encoding='utf-8'))['entries']
                if row.get('step') == 'account']
        if rows:
            return rows
        page.wait_for_timeout(50)
    pytest.fail('No persisted account diagnosis')


def private(value):
    text = json.dumps(value)
    assert EMAIL not in text and PASSWORD not in text and 'PRIVATE_SENTINEL' not in text


def bounded_action(panel, tab, action):
    started = time.monotonic()
    result = panel.evaluate('''({tab,action})=>Promise.race([
      chrome.runtime.sendMessage({type:action,tabId:tab}),
      new Promise((_,reject)=>setTimeout(()=>reject(Error('Account action exceeded 7 seconds')),7000))
    ])''', dict(tab=tab, action=action))
    assert time.monotonic() - started < 8
    return result


@pytest.mark.browser
@pytest.mark.parametrize('mode,confirmation', [('signup', 'label'), ('signup', 'automation'), ('login', 'none')])
@pytest.mark.parametrize('entry', ['analyze', 'waiting'])
def test_native_navigation_primary_once(native_browser, backend, db, mode, confirmation, entry):
    page, worker, tab, panel, isolated, _ = native_browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate('url=>history.pushState({},"",url)', MANUAL)
    if confirmation == 'automation':
        page.evaluate("confirmLabel.firstChild.textContent='Additional password'")
    if mode == 'login':
        page.evaluate('login()')
    assert page.locator('[data-automation-id=forgotPasswordLink]').get_attribute('type') is None
    assert page.locator('[data-automation-id=signInLink]').get_attribute('type') is None
    assert page.locator('[data-automation-id=forgotPasswordLink]').evaluate('e=>e.type') == 'submit'
    assert isolated('describe')['supported']
    if entry == 'waiting':
        backend[0].settings.credentials.save({'clear': True})
        initial = panel.evaluate('id=>chrome.runtime.sendMessage({type:"autofill",tabId:id})', tab)
        assert initial['waiting'] and not initial['busy'] and 'Settings' in initial['progress']
        assert page.evaluate('accountClicks') == 0
        backend[0].settings.credentials.save(dict(email=EMAIL, password=PASSWORD))
    result = analyze(panel, tab)
    assert result.get('ok') and 'activated' in result['summary'], result
    expected = 'createAccountSubmitButton' if mode == 'signup' else 'signInSubmitButton'
    assert page.evaluate('[accountClicks,navigationClicks,applicationClicks,ordered,submitters,consent.checked,marketing.checked]') == [1, 0, 0, [True], [expected], True, False]
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD] * (2 if mode == 'signup' else 1)
    again = analyze(panel, tab)
    assert 'attempted' in again.get('summary', '') or 'busy' in again.get('error', '').lower(), again
    assert page.evaluate('[accountClicks,navigationClicks,applicationClicks]') == [1, 0, 0]
    assert db.rows('SELECT id FROM applications') == before
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    for value in (result, again, saved, isolated('private')):
        private(value)
    for path in (backend[0].config.private / 'traces').glob('*.json'):
        private(json.loads(path.read_text(encoding='utf-8')))


CASES = {
    'heading': ("document.querySelector('form').insertAdjacentHTML('afterbegin','<h1>Reset your password</h1>')", 'reset_password', 'describe'),
    'path': ("history.pushState({},'','/reset-password')", 'reset_password', 'describe'),
    'primary_reset': ("document.querySelector('[type=submit]').textContent='Reset password'", 'reset_password', 'describe'),
    'login_reset': ("login();document.querySelector('[type=submit]').textContent='Reset password'", 'reset_password', 'describe'),
    'captcha': ("document.querySelector('form').insertAdjacentHTML('beforeend','<iframe title=captcha src=about:blank></iframe>')", 'captcha', 'describe'),
    'otp': ("document.querySelector('form').insertAdjacentHTML('beforeend','<input autocomplete=one-time-code>')", 'otp', 'describe'),
    'otp_frame': ("document.querySelector('form').insertAdjacentHTML('beforeend','<iframe src=about:blank#otp></iframe>')", 'otp', 'describe'),
    'current': ("password.parentElement.firstChild.textContent='Current password'", 'current_password', 'describe'),
    'old': ("password.name='oldPassword'", 'current_password', 'describe'),
    'cross_origin': ("document.querySelector('form').action='https://other.test/auth'", 'cross_origin', 'describe'),
    'action_origin': ("document.querySelector('[type=submit]').setAttribute('formaction','https://other.test/auth')", 'cross_origin', 'describe'),
    'ambiguous': ("document.querySelector('[type=submit]').insertAdjacentHTML('afterend','<button>Create account</button>')", 'action_ambiguous', 'describe'),
    'unavailable': ("document.querySelector('[type=submit]').textContent='Submit application'", 'action_unavailable', 'describe'),
    'confirmation': ("confirmLabel.firstChild.textContent='Additional password';secondaryPassword.removeAttribute('data-automation-id')", 'confirmation_unrecognized', 'describe'),
    'disabled': ("password.disabled=true", 'controls_not_editable', 'describe'),
    'no_password': ("document.querySelectorAll('input[type=password]').forEach(e=>e.remove())", 'no_password', 'describe'),
    'no_email': ("email.remove()", 'no_matching_email', 'describe'),
    'scope': ("document.querySelector('form').insertAdjacentHTML('beforeend','<input type=password>')", 'scope_ambiguous', 'describe'),
    'occupied': ("email.value='PRIVATE_SENTINEL'", 'credentials_preserved', 'prepare'),
    'consent_hidden': ("consent.parentElement.hidden=true", 'consent_required', 'prepare'),
    'consent_reverts': ("consent.onchange=()=>consent.checked=false", 'consent_unsettled', 'prepare'),
    'invalid': ("email.setCustomValidity('PRIVATE_SENTINEL')", 'form_invalid', 'prepare'),
    'role_link': ("document.querySelector('[type=submit]').setAttribute('role','link')", 'action_unavailable', 'describe'),
}


@pytest.mark.browser
@pytest.mark.parametrize('case', CASES)
def test_rejection_persisted(native_browser, backend, db, case):
    page, worker, tab, panel, isolated, _ = native_browser
    script, reason, phase = CASES[case]
    before = db.rows('SELECT id FROM applications')
    page.evaluate(script)
    description = isolated('describe')
    if phase == 'describe':
        assert not description['supported']
        assert description['diagnosis']['reason'] == reason
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result.get('ok') and 'manual attention: check credentials' not in result['summary'], result
    rows = account_rows(page, backend[0].config)
    assert any(row['reason'] == reason and row['observed']['phase'] == phase for row in rows), rows
    if case == 'otp_frame':
        assert 'verification code' in result['summary']
        assert any(row['reason'] == 'otp' and row['observed']['frame_count'] == 1
                   and row['observed']['password_count'] == 2 for row in rows)
    assert page.evaluate('[accountClicks,navigationClicks,applicationClicks,marketing.checked]') == [0, 0, 0, False]
    if phase == 'describe':
        assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(e=>!e.value)')
        if case != 'no_email':
            assert page.locator('#email').input_value() == ''
    assert db.rows('SELECT id FROM applications') == before
    private(rows)
    private(result)
    private(isolated('private'))
    for path in (backend[0].config.private / 'traces').glob('*.json'):
        private(json.loads(path.read_text(encoding='utf-8')))


@pytest.mark.browser
def test_autofill_otp_frame_returns_promptly(native_browser, backend, db):
    page, worker, tab, panel, isolated, _ = native_browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate(CASES['otp_frame'][0])
    result = bounded_action(panel, tab, 'autofill')
    assert not result.get('error') and not result['busy'], result
    assert 'verification code' in result['progress']
    rows = account_rows(page, backend[0].config)
    assert any(row['reason'] == 'otp' and row['observed']['phase'] == 'describe'
               and row['observed']['frame_count'] == 1
               and row['observed']['password_count'] == 2 for row in rows), rows
    assert page.evaluate('[accountClicks,navigationClicks,applicationClicks,consent.checked,marketing.checked]') == [0, 0, 0, False, False]
    assert page.locator('#email').input_value() == ''
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(e=>!e.value)')
    assert db.rows('SELECT id FROM applications') == before
    for value in (rows, result, isolated('private')):
        private(value)


@pytest.mark.browser
def test_autofill_diagnosis_and_safe_catch(native_browser, backend, monkeypatch):
    page, worker, tab, panel, isolated, _ = native_browser
    original_load = backend[0].settings.credentials.load
    releases = []
    def fail(*, profile_email=False):
        # Ordinary reads support redaction for /ext/match and /ext/trace.
        # Fail only the authorized /ext/credentials release under test.
        if profile_email:
            releases.append(True)
            raise RuntimeError(EMAIL + PASSWORD + 'PRIVATE_SENTINEL')
        return original_load(profile_email=profile_email)
    monkeypatch.setattr(backend[0].settings.credentials, 'load', fail)
    result = bounded_action(panel, tab, 'autofill')
    assert releases == [True]
    assert result['progress'] == 'Account action unavailable. Sign in manually, then continue.'
    rows = account_rows(page, backend[0].config)
    assert any(row['reason'] == 'unavailable' and row['observed']['phase'] == 'credentials' for row in rows)
    private(rows)
    private(result)
    private(isolated('private'))
    assert page.evaluate('[accountClicks,navigationClicks,applicationClicks,consent.checked,marketing.checked]') == [0, 0, 0, False, False]
    assert page.locator('#email').input_value() == ''
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(e=>!e.value)')
    for path in (backend[0].config.private / 'traces').glob('*.json'):
        private(json.loads(path.read_text(encoding='utf-8')))


def test_account_trace_schema(backend):
    app = backend[0]
    row = dict(step='account', reason='captcha', observed=dict(phase='describe', password_count=2,
               raw=PASSWORD), value=PASSWORD, error=EMAIL, stack='PRIVATE_SENTINEL', label=PASSWORD)
    result = trace(app, dict(host='fixture.myworkdayjobs.com', kind='autofill', entries=[row]))
    saved = json.loads((app.config.private / 'traces' / result['filename']).read_text(encoding='utf-8'))
    assert saved['entries'] == [dict(step='account', action='rejected', label='Account step', reason='captcha', observed=dict(phase='describe', password_count=2))]
    private(saved)
    for change in [dict(reason=PASSWORD), dict(observed=dict(phase=EMAIL)),
                   dict(observed=dict(phase='describe', password_count=True)),
                   dict(observed=dict(phase='describe', password_count=1001)),
                   dict(observed=dict(phase='describe', password_count=PASSWORD))]:
        with pytest.raises(SettingsRejected):
            trace(app, dict(host='fixture.myworkdayjobs.com', kind='autofill', entries=[dict(row, **change)]))
