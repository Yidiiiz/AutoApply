"""T082: native ownership and bounded Workday surfaces in actual offline MV3."""
import json
from pathlib import Path

import pytest

from test_t078_accounts import (
    EMAIL, PASSWORD, backend, browser, dashboard, loaded_extension, offline_descriptions,
)
from test_t081_account_diagnostics import account_rows, bounded_action, private


@pytest.fixture
def surface_browser(monkeypatch, request):
    monkeypatch.setattr('test_t078_accounts.FIXTURE', Path(__file__).parent / 'fixtures/extension/t082.html')
    return request.getfixturevalue('browser')


def rows(config):
    return [row for path in (config.private / 'traces').glob('*.json')
            for row in json.loads(path.read_text(encoding='utf-8'))['entries']
            if row.get('step') == 'account']


@pytest.mark.browser
@pytest.mark.parametrize('associated', [False, True])
@pytest.mark.parametrize('role', ['form', 'dialog'])
def test_external_account_controls_once(surface_browser, backend, db, associated, role):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    page.locator('#accountForm > div').evaluate('(el,role)=>el.setAttribute("role",role)', role)
    if associated:
        # Native form= ownership also works outside the visual surface.
        page.evaluate("primary.setAttribute('form','accountForm');document.body.append(primary)")
    assert page.locator('#password').evaluate("e=>e.closest('[role]')!==e.form")
    description = isolated('describe')
    assert description['supported'] and description['mode'] == 'signup', description
    assert description['diagnosis'] == dict(reason='supported', password_count=2, email_count=1,
        action_count=1, external_action_count=1, native_owner_mismatch_count=0,
        account_form_count=1, known_surface_count=1)
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result.get('ok') and 'activated' in result['summary'], result
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,ordered,submitters,consent.checked,marketing.checked]') == [1, 0, 0, 0, [True], ['createAccountSubmitButton'], True, False]
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD, PASSWORD]
    again = bounded_action(panel, tab, 'page-snapshot')
    assert 'attempted' in again.get('summary', '') or 'busy' in again.get('error', '').lower(), again
    page.wait_for_timeout(1200)
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks]') == [1, 0, 0, 0]
    assert db.rows('SELECT id FROM applications') == before
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    for value in (description, result, again, saved, isolated('private'), rows(backend[0].config)):
        private(value)


CASES = {
    'other_input_owner': ("document.body.insertAdjacentHTML('beforeend','<form id=other></form>');email.setAttribute('form','other')", 'native_owner_mismatch'),
    'other_button_owner': ("document.body.insertAdjacentHTML('beforeend','<form id=other></form>');primary.setAttribute('form','other')", 'native_owner_mismatch'),
    'two_auth_forms': ("accountSurface.insertAdjacentHTML('beforeend','<form data-automation-id=signInFormo><button>Sign in</button></form>')", 'account_surface_ambiguous'),
    'separate_credentials': ("confirmLabel.remove();document.body.insertAdjacentHTML('beforeend','<form><h2>Sign in</h2><input type=email><input type=password><button>Sign in</button></form>')", 'scope_ambiguous'),
    'competing_login': ("document.body.insertAdjacentHTML('beforeend','<form><h2>Sign in</h2><input type=email><button>Sign in</button></form>')", 'account_surface_ambiguous'),
    'nested_surfaces': ("accountSurface.insertAdjacentHTML('beforebegin','<div id=outer data-automation-id=signInContent></div>');outer.append(accountSurface)", 'account_surface_ambiguous'),
    'unrelated_create': ("document.body.append(primary)", 'signup_mode_unavailable'),
    'unknown_surface': ("accountSurface.removeAttribute('data-automation-id')", 'signup_mode_unavailable'),
    'duplicate': ("primary.insertAdjacentHTML('afterend','<button>Create Account</button>')", 'action_ambiguous'),
    'competing_action': ("primary.insertAdjacentHTML('afterend','<button data-automation-id=signInSubmitButton>Sign in</button>')", 'action_ambiguous'),
    'wrapper_only': ("primary.remove()", 'signup_mode_unavailable'),
    'action_origin': ("primary.setAttribute('formaction','https://other.test/auth')", 'cross_origin'),
    'form_origin': ("accountForm.action='https://other.test/auth'", 'cross_origin'),
    'otp': ("accountSurface.insertAdjacentHTML('beforeend','<input autocomplete=one-time-code>')", 'otp'),
    'captcha': ("accountSurface.insertAdjacentHTML('beforeend','<div data-sitekey=synthetic>Challenge</div>')", 'captcha'),
    'reset': ("authViewTitle.textContent='Reset your password'", 'reset_password'),
    'application': ("primary.textContent='Submit application'", 'action_unavailable'),
    'verify': ("confirmLabel.firstChild.textContent='Additional password';secondaryPassword.removeAttribute('data-automation-id')", 'confirmation_unrecognized'),
    'consent_owner': ("document.body.insertAdjacentHTML('beforeend','<form id=other></form>');consent.setAttribute('form','other')", 'native_owner_mismatch'),
    'invalid_consent': ("consent.setCustomValidity('PRIVATE_SENTINEL')", 'form_invalid'),
    'invalid': ("email.setCustomValidity('PRIVATE_SENTINEL')", 'form_invalid'),
}


@pytest.mark.browser
@pytest.mark.parametrize('case', CASES)
def test_surface_abstains_privately(surface_browser, backend, db, case):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    script, reason = CASES[case]
    page.evaluate(script)
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result.get('ok'), result
    saved = account_rows(page, backend[0].config)
    observed = next(row['observed'] for row in saved if row['reason'] == reason)
    assert observed['account_form_count'] >= 1 and observed['password_count'] == 2
    if case.startswith('other_') or case == 'consent_owner':
        assert observed['native_owner_mismatch_count'] == 1
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,marketing.checked]') == [0, 0, 0, 0, False]
    if observed['phase'] == 'describe':
        assert page.locator('#email').input_value() == ''
        assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(e=>!e.value)')
    assert db.rows('SELECT id FROM applications') == before
    for value in (result, saved, isolated('private')):
        private(value)
    assert set(observed) <= {'phase', 'password_count', 'email_count', 'action_count', 'frame_count',
                             'external_action_count', 'native_owner_mismatch_count', 'account_form_count', 'known_surface_count'}


@pytest.mark.browser
def test_identical_poll_diagnostics_deduplicated(surface_browser, backend):
    page, worker, tab, panel, isolated, _ = surface_browser
    page.evaluate('document.body.append(primary)')
    result = bounded_action(panel, tab, 'autofill')
    assert 'signup mode' in result['progress'], result
    first = account_rows(page, backend[0].config)
    page.wait_for_timeout(2500)
    assert rows(backend[0].config) == first
    # Explicit Analyze refreshes the same value-free diagnosis.
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result.get('ok'), result
    page.wait_for_timeout(400)
    assert len(rows(backend[0].config)) == len(first) + 1
    # A trusted edit changes revision, allowing one fresh poll diagnosis.
    page.locator('#email').fill('PRIVATE_SENTINEL')
    page.wait_for_timeout(2200)
    assert len(rows(backend[0].config)) == len(first) + 2
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks]') == [0, 0, 0, 0]
    private(rows(backend[0].config))
