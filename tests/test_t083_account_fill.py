"""T083: fill-only account results using the real offline MV3/backend fixtures."""
import pytest

from test_t078_accounts import (
    EMAIL, PASSWORD, backend, browser, dashboard, loaded_extension, offline_descriptions,
    test_account_pending_action_invalidated as pending_action_invalidated,
)
from test_t081_account_diagnostics import account_rows, bounded_action, private
from test_t082_account_surface import rows, surface_browser


UNAVAILABLE = {
    'missing': 'primary.remove()',
    'hidden': 'primary.hidden=true',
    'disabled': 'primary.disabled=true',
    'caption': "primary.textContent='Submit application'",
    'identity': "primary.setAttribute('data-automation-id','unknownPrimary')",
    'missing_invalid': "primary.remove();email.setCustomValidity('PRIVATE_SENTINEL');consent.setCustomValidity('PRIVATE_SENTINEL');consent.parentElement.hidden=true",
}
PARTIAL = 'Account details filled. Create account button is not available yet.'


def protect_application(page):
    page.evaluate('''()=>{
      document.body.insertAdjacentHTML('beforeend',
        '<button type=button id=finalSubmit onclick="applicationClicks++">Submit application</button>');
      globalThis.credentialEvents=[];
      for(const el of [email,password,secondaryPassword])for(const type of ['input','change'])
        el.addEventListener(type,e=>credentialEvents.push([el.id,type,e.composed]));
    }''')


def no_activation(page, worker, db, before, *, consent_checked=False):
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,consent.checked,marketing.checked]') == [0, 0, 0, 0, consent_checked, False]
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    assert not saved['session'].get('accountActionClaims')
    assert db.rows('SELECT id FROM applications') == before
    private(saved)


@pytest.mark.browser
@pytest.mark.parametrize('case', UNAVAILABLE)
@pytest.mark.parametrize('entry', ['page-snapshot', 'autofill'])
def test_unavailable_primary_fills_only(surface_browser, backend, db, case, entry):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    page.evaluate(UNAVAILABLE[case])
    description = isolated('describe')
    assert description['supported'] and not description['actionReady'], description
    assert description['mode'] == 'signup'
    result = bounded_action(panel, tab, entry)
    assert result.get('summary' if entry == 'page-snapshot' else 'progress') == PARTIAL, result
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD, PASSWORD]
    assert page.evaluate('credentialEvents') == [[control, event, True]
        for control in ['email', 'password', 'secondaryPassword'] for event in ['input', 'change']]
    no_activation(page, worker, db, before, consent_checked=case != 'missing_invalid')
    persisted = account_rows(page, backend[0].config)
    assert any(row['reason'] == 'action_unavailable' and row['observed']['phase'] == 'prepare'
               and row['observed']['action_count'] == (1 if case in {'disabled', 'identity'} else 0)
               for row in persisted), persisted
    assert not any(row['reason'] in {'signup_mode_unavailable', 'unavailable'} for row in persisted)
    again = bounded_action(panel, tab, 'page-snapshot')
    assert again.get('summary') == PARTIAL, again
    assert len(page.evaluate('credentialEvents')) == 6
    no_activation(page, worker, db, before, consent_checked=case != 'missing_invalid')
    for value in (description, result, again, persisted, isolated('private')):
        private(value)


@pytest.mark.browser
@pytest.mark.parametrize('unready', ['missing', 'hidden', 'disabled'])
@pytest.mark.parametrize('edited', [False, True])
def test_primary_later_explicit_retry(surface_browser, backend, db, unready, edited):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    page.evaluate('globalThis.savedPrimary=primary')
    page.evaluate(UNAVAILABLE[unready])
    first = bounded_action(panel, tab, 'page-snapshot')
    assert first['summary'] == PARTIAL, first
    no_activation(page, worker, db, before, consent_checked=True)
    if edited:
        page.locator('#email').fill('PRIVATE_SENTINEL')
    page.evaluate('''()=>{
      savedPrimary.hidden=false;savedPrimary.disabled=false;
      document.querySelector('[data-automation-id=click_filter]').append(savedPrimary);
    }''')
    assert isolated('describe')['actionReady']
    retry = bounded_action(panel, tab, 'page-snapshot')
    if edited:
        assert 'preserved' in retry['summary'], retry
        assert page.locator('#email').input_value() == 'PRIVATE_SENTINEL'
        no_activation(page, worker, db, before, consent_checked=True)
    else:
        assert 'activated' in retry['summary'], retry
        assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,ordered,consent.checked,marketing.checked]') == [1, 0, 0, 0, [True], True, False]
        assert len(page.evaluate('credentialEvents')) == 6
        again = bounded_action(panel, tab, 'page-snapshot')
        assert 'attempted' in again.get('summary', ''), again
        assert page.evaluate('[accountClicks,applicationClicks]') == [1, 0]
        claims = worker.evaluate('async()=>(await chrome.storage.session.get("accountActionClaims")).accountActionClaims')
        assert len(claims) == 1
    assert db.rows('SELECT id FROM applications') == before
    for value in (first, retry, isolated('private'), rows(backend[0].config)):
        private(value)


@pytest.mark.browser
@pytest.mark.parametrize('entry', ['page-snapshot', 'autofill'])
def test_consent_enables_primary_same_call(surface_browser, backend, db, entry):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    page.evaluate('''()=>{
      primary.disabled=true;
      consent.addEventListener('change',()=>primary.disabled=!consent.checked);
    }''')
    description = isolated('describe')
    assert description['supported'] and not description['actionReady'], description
    result = bounded_action(panel, tab, entry)
    assert 'activated' in result.get('summary' if entry == 'page-snapshot' else 'progress', ''), result
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD, PASSWORD]
    assert len(page.evaluate('credentialEvents')) == 6
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,ordered,submitters,consent.checked,marketing.checked]') == [1, 0, 0, 0, [True], ['createAccountSubmitButton'], True, False]
    again = bounded_action(panel, tab, 'page-snapshot')
    assert 'attempted' in again.get('summary', ''), again
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks]') == [1, 0, 0, 0]
    saved = worker.evaluate('async()=>({session:await chrome.storage.session.get(null),local:await chrome.storage.local.get(null)})')
    assert len(saved['session']['accountActionClaims']) == 1
    assert db.rows('SELECT id FROM applications') == before
    for value in (description, result, again, saved, isolated('private'), rows(backend[0].config)):
        private(value)


@pytest.mark.browser
def test_missing_primary_checks_eligible_consent_without_claim(surface_browser, backend, db):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    page.evaluate('primary.remove()')
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result['summary'] == PARTIAL, result
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD, PASSWORD]
    assert len(page.evaluate('credentialEvents')) == 6
    assert page.evaluate('submitters') == []
    no_activation(page, worker, db, before, consent_checked=True)
    for value in (result, isolated('private'), rows(backend[0].config)):
        private(value)


GUARDS = {
    'unknown_second': "confirmLabel.firstChild.textContent='Additional password';secondaryPassword.removeAttribute('data-automation-id')",
    'reversed': "password.setAttribute('aria-label','Verify New Password')",
    'reset': "authViewTitle.textContent='Reset your password'",
    'current': "password.setAttribute('aria-label','Current password')",
    'old': "password.name='oldPassword'",
    'otp': "accountSurface.insertAdjacentHTML('beforeend','<input autocomplete=one-time-code>')",
    'captcha': "accountSurface.insertAdjacentHTML('beforeend','<div data-sitekey=synthetic>Challenge</div>')",
    'cross_origin': "accountForm.action='https://other.test/auth'",
    'owner': "document.body.insertAdjacentHTML('beforeend','<form id=other></form>');email.setAttribute('form','other')",
    'hidden_action_origin': "savedPrimary.hidden=true;savedPrimary.setAttribute('formaction','https://other.test/auth');accountSurface.append(savedPrimary)",
    'hidden_action_owner': "document.body.insertAdjacentHTML('beforeend','<form id=other></form>');savedPrimary.hidden=true;savedPrimary.setAttribute('form','other');accountSurface.append(savedPrimary)",
    'incomplete': 'email.remove()',
    'occupied_email': "email.value='PRIVATE_SENTINEL'",
    'occupied_password': "password.value='PRIVATE_SENTINEL'",
}


@pytest.mark.browser
@pytest.mark.parametrize('case', GUARDS)
def test_missing_primary_retains_guards(surface_browser, backend, db, case):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    page.evaluate('globalThis.savedPrimary=primary;primary.remove()')
    page.evaluate(GUARDS[case])
    existing = page.locator('#accountForm input').evaluate_all('(els)=>els.map(e=>e.value)')
    result = bounded_action(panel, tab, 'page-snapshot')
    assert result.get('ok') and result['summary'] != PARTIAL, result
    assert page.locator('#accountForm input').evaluate_all('(els)=>els.map(e=>e.value)') == existing
    no_activation(page, worker, db, before)
    for value in (result, account_rows(page, backend[0].config), isolated('private')):
        private(value)


@pytest.mark.browser
@pytest.mark.parametrize('change', ['edit', 'navigation', 'hash', 'cancel', 'pause'])
def test_missing_primary_stale_lookup(surface_browser, backend, monkeypatch, db, change):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    page.evaluate('primary.remove()')
    pending_action_invalidated(surface_browser, backend, monkeypatch, change)
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(e=>!e.value)')
    assert page.locator('#email').input_value() == ('manual@example.test' if change == 'edit' else '')
    no_activation(page, worker, db, before)
    private(rows(backend[0].config))


@pytest.mark.browser
def test_known_primary_positive(surface_browser, backend, db):
    page, worker, tab, panel, isolated, _ = surface_browser
    before = db.rows('SELECT id FROM applications')
    protect_application(page)
    description = isolated('describe')
    assert description['supported'] and description['actionReady']
    result = bounded_action(panel, tab, 'page-snapshot')
    assert 'activated' in result.get('summary', ''), result
    assert page.locator('#email').input_value() == EMAIL
    assert page.locator('input[type=password]').evaluate_all('(els)=>els.map(e=>e.value)') == [PASSWORD, PASSWORD]
    assert page.evaluate('[accountClicks,wrapperClicks,navigationClicks,applicationClicks,ordered,marketing.checked]') == [1, 0, 0, 0, [True], False]
    assert db.rows('SELECT id FROM applications') == before
    for value in (description, result, isolated('private')):
        private(value)
