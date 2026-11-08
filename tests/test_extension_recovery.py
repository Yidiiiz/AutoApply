"""Root-runnable actual MV3 gate; fixture-only HTTP and synthetic stub provider.

Run with -m browser where the unpacked-extension browser gate is supported.
The worker only collects these cases; it never launches Chrome or a server.
"""
import json
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_extension import loaded_extension
from test_t077_analyze import backend
from test_t086_autonomous_analyze import open_form, analyze
from test_t085_answer_repair import proposal, save_profile

pytestmark = pytest.mark.browser
FIXTURE = Path(__file__).parent / 'fixtures/extension/answer-recovery/observed.html'


def grounded_rows(request):
    return {'answers': [proposal(f['key']) if 'skills' in f['fact_ids'] else
                        dict(proposal(f['key'], value=None, needs_input=True, reason='needs_fact'), fact_ids=[])
                        for f in request['fields']]}


def recovery_form(loaded_extension, backend, config, monkeypatch, *, automatic=False, writer=None, html=None):
    service = backend[0]
    profile = config.profile
    profile['skills'] = ['Python']
    profile['demographics'] = {'race': 'decline'}
    save_profile(config, profile)
    calls = []
    def respond(request):
        calls.append(request)
        assert set(request['facts']) <= {'skills'}
        if writer:
            return writer(request, len(calls))
        return grounded_rows(request)
    monkeypatch.setattr(service, 'run_writing', respond)
    page, worker, tab, isolated = open_form(loaded_extension, backend, automatic=automatic,
                                           html=html or FIXTURE.read_text(encoding='utf-8'))
    return page, worker, tab, isolated, calls


def test_default_autofill_unknown_wording_to_exact_choice_and_answers_tab(loaded_extension, backend, config, monkeypatch):
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch)
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert not result.get('error'), result
    expect(page.locator('#ecosystem')).to_have_text('Python (programming)')
    assert len(calls) == 1
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#tab-answers')).to_have_attribute('aria-selected', 'true')
    row = next(a for a in result['completed'] if 'ecosystem' in a['label'])
    assert row['value'] == 'Python (programming)' and row['source'] == 'grounded_ai:answer_repair'
    assert page.evaluate('actions') == []
    assert not [p for path, p in backend[2]['posts'] if path in {'/ext/applied', '/ext/run/result'}]


def test_autofill_saved_decline_selects_exact_race_checkbox_and_counts_completion(loaded_extension, backend, config, monkeypatch):
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch)
    # Isolate saved-fact filling; ordinary unknown questions have their own gate.
    page.locator('#ecosystem').evaluate('e=>e.parentElement.remove()')
    page.locator('#source-group').evaluate('e=>e.parentElement.remove()')
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert not result.get('error'), result
    checked = page.locator('#race-group input:checked')
    expect(checked).to_have_count(1)
    expect(page.locator('#race-group label').filter(has_text='Do not wish to identify').locator('input')).to_be_checked()
    assert checked.evaluate_all('inputs=>inputs.map(e=>e.closest("label").textContent.trim())') == ['Do not wish to identify']
    race, = [row for row in result['completed'] if row['label'].startswith('Race/Ethnicity:')]
    assert race['value'] == ['Do not wish to identify']
    assert race['source'] == 'profile:demographics.race'
    assert result['summary']['filled'] == len(result['completed']) >= 1
    assert not any(row['label'].startswith('Race/Ethnicity:') for row in result['pending'])
    assert not calls and page.evaluate('actions') == []
    assert not [p for path, p in backend[2]['posts'] if path in {'/ext/applied', '/ext/run/result'}]


def test_observed_readonly_radio_and_generic_checkbox_labels_are_reconciled(loaded_extension, backend, config, monkeypatch):
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch)
    scanned = isolated('scan')['fields']
    radio = next(f for f in scanned if f['kind'] == 'radio')
    assert radio['label'] == 'Where did you discover this opportunity?'
    assert radio['options'] == ['Glassdoor', 'HireEZ', 'Indeed', 'Moseeker', 'RiseSmart', 'Zhiphin']
    race = next(f for f in scanned if f['kind'] == 'checkbox' and len(f['options']) == 7)
    assert race['label'].startswith('Race/Ethnicity:') and race['multiple']
    inspected = isolated('inspectPage')['diagnostic']['fields']
    assert next(f for f in inspected if f['kind'] == 'radio')['detected']
    token = 'native-proxy'
    prepared = isolated('repairStart', token)
    radio = next(f for f in prepared['fields'] if f['kind'] == 'radio')
    isolated('repairFill', token, [dict(key=radio['key'], status='RESOLVED', value='Indeed', source='synthetic')])
    assert isolated('repairFinish', token)['filled'] == 1
    expect(page.locator('input[value=Indeed]')).to_be_checked()
    assert not calls and page.evaluate('actions') == []


def test_one_trusted_field_edit_preserves_it_and_fills_other_fields(loaded_extension, backend, config, monkeypatch):
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch)
    prepared = isolated('repairStart', 'edit')
    fields = {f['label']: f for f in prepared['fields']}
    page.locator('#name').fill('Manually chosen')
    isolated('repairFill', 'edit', [
        dict(key=fields['Full name']['key'], status='RESOLVED', value='Stale name'),
        dict(key=fields['Email']['key'], status='RESOLVED', value='synthetic@example.test')])
    done = isolated('repairFinish', 'edit')
    expect(page.locator('#name')).to_have_value('Manually chosen')
    expect(page.locator('#email')).to_have_value('synthetic@example.test')
    assert done['filled'] == 1 and done['stopped'] == 'none'
    assert next(f for f in done['fields'] if f['label'] == 'Full name')['repair_status'] == 'user_edited'
    assert not calls and page.evaluate('actions') == []


@pytest.mark.parametrize('failure', ['provider', 'options'])
def test_automatic_transient_recovery_then_no_stable_reasoning_spin(loaded_extension, backend, config, monkeypatch, failure):
    def writer(request, number):
        if failure == 'provider' and number == 1:
            raise TimeoutError('synthetic temporary provider failure')
        return grounded_rows(request)
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch, writer=writer)
    page.locator('#source-group').evaluate('e=>e.parentElement.remove()')
    if failure == 'options':
        page.evaluate('available=false')
    worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
    if failure == 'options':
        # The first bounded option probe sees an empty stable menu. A delayed
        # retry must re-open it even though options_observed was already true.
        page.wait_for_timeout(4500)
        page.evaluate('available=true')
    expect(page.locator('#ecosystem')).to_have_text('Python (programming)', timeout=35000)
    count = len(calls)
    assert count == (2 if failure == 'provider' else 1), [
        [(f['key'], f['label'], f['options'], f['fact_ids']) for f in call['fields']] for call in calls]
    page.wait_for_timeout(18000)
    assert len(calls) == count
    assert page.evaluate('actions') == []


def test_automatic_structural_defect_queues_only_sanitized_evidence(loaded_extension, backend, config, monkeypatch):
    html = FIXTURE.read_text(encoding='utf-8').replace('</form>',
        '<label>Unrecognized fixture control<input type=range id=broken></label></form>')
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch, html=html)
    worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
    expect(page.locator('#ecosystem')).to_have_text('Python (programming)', timeout=15000)
    for _ in range(100):
        jobs = backend[0].repairs.status()['jobs']
        if jobs:
            break
        page.wait_for_timeout(100)
    assert len(jobs) == 1
    with backend[0].repairs._db() as db:
        data = json.loads(db.execute('SELECT evidence FROM jobs').fetchone()[0])
    assert [control['causes'] for control in data['controls']] == [['omitted_by_scanner', 'unsupported_widget']], data
    assert 'Python' not in json.dumps(data) and 'student@example.test' not in json.dumps(data)
    assert page.evaluate('actions') == []


@pytest.mark.parametrize('revert', [False, True])
def test_input_combobox_owned_body_option_buttons_require_retained_selection(loaded_extension, backend, config, monkeypatch, revert):
    html = FIXTURE.with_name('portal.html').read_text(encoding='utf-8')
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch, html=html)
    page.evaluate('value=>revert=value', revert)
    prepared = isolated('repairStart', 'portal')
    combo = next(f for f in prepared['fields'] if f['kind'] == 'combobox')
    options = isolated('repairOptions', 'portal', [combo['key']])
    assert next(f for f in options if f['key'] == combo['key'])['options'] == ['Yes', 'No']
    isolated('repairFill', 'portal', [dict(key=combo['key'], status='RESOLVED', value='Yes', source='synthetic')])
    done = isolated('repairFinish', 'portal')
    row = next(f for f in done['fields'] if f['key'] == combo['key'])
    if revert:
        expect(page.locator('#authorization')).to_have_value('No')
        assert done['filled'] == 0 and done['completed'] == []
        assert row['repair_status'] == 'verification_failed' and not row['answered']
        rescanned = isolated('scan')['fields']
        assert not next(f for f in rescanned if f['key'] == combo['key'])['answered']
        inspected = isolated('inspectPage', {'repair': True})['diagnostic']['fields']
        assert not next(f for f in inspected if f['kind'] == 'combobox')['answered']
    else:
        expect(page.locator('#authorization')).to_have_value('Yes')
        assert done['filled'] == 1 and done['completed'][0]['value'] == 'Yes'
        assert row['repair_status'] == 'verified' and row['answered']
    assert not calls and page.evaluate('actions') == []


def test_relabelled_question_cannot_receive_old_answer(loaded_extension, backend, config, monkeypatch):
    html = FIXTURE.with_name('portal.html').read_text(encoding='utf-8')
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch, html=html)
    prepared = isolated('repairStart', 'relabel')
    combo = next(f for f in prepared['fields'] if f['kind'] == 'combobox')
    isolated('repairOptions', 'relabel', [combo['key']])
    page.locator('#question').evaluate('e=>e.textContent="Which graduation year applies?"')
    isolated('repairFill', 'relabel', [dict(key=combo['key'], status='RESOLVED', value='Yes', source='synthetic')])
    done = isolated('repairFinish', 'relabel')
    expect(page.locator('#authorization')).to_have_value('')
    assert done['filled'] == 0 and done['completed'] == []
    assert not calls and page.evaluate('actions') == []


def test_close_during_grounded_reasoning_cannot_write(loaded_extension, backend, config, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def writer(request, number):
        entered.set()
        assert release.wait(8)
        return grounded_rows(request)
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch, writer=writer)
    worker.evaluate('id=>{globalThis.recoveryFill=autoApplyAutofill(id);return true}', tab)
    try:
        for _ in range(100):
            if entered.is_set():
                break
            page.wait_for_timeout(50)
        assert entered.is_set()
        worker.evaluate('id=>autoApplySidebar(id,"closed")', tab)
    finally:
        release.set()
    worker.evaluate('()=>recoveryFill')
    expect(page.locator('#ecosystem')).to_have_text('Select one')
    assert page.evaluate('actions') == []



def test_saved_missing_fact_rearms_automatic_recovery_through_trusted_panel(loaded_extension, backend, config, monkeypatch):
    page, worker, tab, isolated, calls = recovery_form(loaded_extension, backend, config, monkeypatch)
    # Keep the unfamiliar ecosystem question and ordinary resolved controls.
    # All fixture changes precede automatic analysis; the document stays put.
    page.locator('#source-group').evaluate('e=>e.parentElement.remove()')
    page.locator('#conflict').evaluate('e=>e.parentElement.remove()')
    profile = config.profile
    profile['skills'] = []
    save_profile(config, profile)
    original_url = page.url
    page.evaluate('globalThis.sameRecoveryDocument = true')

    def answer_revision():
        with backend[0].connection() as db:
            return [config.profile_snapshot().revision, db.answer_revision()]

    def settled(revision):
        state = None
        for _ in range(200):
            state = worker.evaluate("""async id=>{
              const {automaticRecovery=[],tabResults={}}=await chrome.storage.session.get(['automaticRecovery','tabResults']);
              return {recovery:automaticRecovery.find(([tab])=>tab===id)?.[1],result:tabResults[id]};
            }""", tab)
            result, recovery = state.get('result') or {}, state.get('recovery') or {}
            if (recovery.get('settled') and not result.get('busy') and not result.get('recoveryPending')
                    and result.get('answerRevision') == revision
                    and json.loads(recovery.get('revision') or 'null') == revision):
                return state
            page.wait_for_timeout(100)
        pytest.fail(f'Automatic recovery did not settle: {state}')

    initial_revision = answer_revision()
    worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
    before = settled(initial_revision)
    missing, = [row for row in before['result']['pending'] if 'ecosystem' in row['label']]
    assert missing['reason_code'] == 'needs_fact'
    assert len(calls) == 1 and calls[0]['facts'] == {}
    assert calls[0]['fields'][0]['fact_ids'] == []
    expect(page.locator('#ecosystem')).to_have_text('Select one')
    page.wait_for_timeout(18000)  # Past the retry deadline: abstention is stable.
    assert len(calls) == 1
    assert settled(initial_revision)['recovery'] == before['recovery']
    assert backend[0].repairs.status()['jobs'] == []

    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    expect(page.frame_locator('#autoapply-sidebar iframe').locator('#connection')).to_have_class('connected')
    panel = next(frame for frame in page.frames if '/sidepanel.html' in frame.url)
    assert panel.url.startswith(f'chrome-extension://{loaded_extension[2]}/sidepanel.html?')
    assert panel.evaluate('Number(new URL(location.href).searchParams.get("tabId"))') == tab

    profile['skills'] = ['Python']
    save_profile(config, profile)
    revision = answer_revision()
    assert revision != before['result']['answerRevision']

    def notify():
        # This must traverse the shipped onMessage allowlist with a real trusted
        # extension sender, including its application tab binding. A dropped
        # route fails here promptly, before waiting for any DOM change.
        response = panel.evaluate("""async message=>{
          let timer;
          try {
            return await Promise.race([
              chrome.runtime.sendMessage(message),
              new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('answer-context-changed response missing')),2000);})
            ]);
          } finally {clearTimeout(timer);}
        }""", dict(type='answer-context-changed', tabId=tab, revision=revision))
        assert response and response.get('ok') is True, response

    notify()
    notify()  # Same revision while recovery is pending must coalesce.
    expect(page.locator('#ecosystem')).to_have_text('Python (programming)', timeout=20000)
    after = settled(revision)
    assert len(calls) == 2, calls  # The old needs-fact cache was invalidated.
    assert set(calls[1]['facts']) == {'skills'} and list(calls[1]['facts']['skills']) == ['Python']
    assert calls[1]['fields'][0]['fact_ids'] == ['skills']
    completed, = [row for row in after['result']['completed'] if row['key'] == missing['key']]
    assert completed['value'] == 'Python (programming)'
    assert completed['source'] == 'grounded_ai:answer_repair'
    assert not any(row['key'] == missing['key'] for row in after['result']['pending'])
    assert after['result']['summary']['filled'] == len(after['result']['completed']) == before['result']['summary']['filled'] + 1
    # Authorized fills may change value-based identities. The contract is the
    # same frame documents, also checked by the retained global marker and URL.
    assert {frame: value['documentId'] for frame, value in after['result']['pageIdentities'].items()} == {
        frame: value['documentId'] for frame, value in before['result']['pageIdentities'].items()}
    assert after['recovery']['attempts'] == 1
    answer_requests = backend[2]['answers']
    notify()  # Also deduplicate after the new revision has settled.
    page.wait_for_timeout(18000)
    assert len(calls) == 2 and backend[2]['answers'] == answer_requests
    assert settled(revision)['recovery'] == after['recovery']
    expect(page.locator('#ecosystem')).to_have_text('Python (programming)')
    assert page.url == original_url and page.evaluate('sameRecoveryDocument') is True
    assert backend[0].repairs.status()['jobs'] == []  # No coding repair worker.
    assert page.evaluate('actions') == []
    assert not [p for path, p in backend[2]['posts'] if path in {'/ext/applied', '/ext/run/result'}]
