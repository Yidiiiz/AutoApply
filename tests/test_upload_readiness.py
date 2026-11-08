"""T090: synthetic public shapes, temporary backend; browser gate runs separately."""
import json
from pathlib import Path
import subprocess
import time

import pytest
from playwright.sync_api import expect

from autoapply.extension_api import resolve_answers
from test_t078_accounts import backend, dashboard, loaded_extension, offline_descriptions

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/extension/upload-readiness'
WORKDAY = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_T090/apply'
SMART = 'https://jobs.smartrecruiters.com/oneclick-ui/company/Fixture/publication/001359e4-c46c-45cb-a388-bc79014089ec'


def open_form(loaded_extension, backend, mode='workday', automatic=False):
    context, worker, extension_id = loaded_extension
    service, server, _ = backend
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server,
        key=service.settings.extension_key(), autoSendStructure=automatic, autoAccountFill=False))
    blocked = []
    def serve(route):
        if route.request.method == 'GET':
            route.fulfill(path=str(FIXTURES / 'forms.html'), content_type='text/html')
        else:
            blocked.append(route.request.method)
            route.abort()
    url = SMART if mode == 'smart' else WORKDAY
    context.route(url + '**', serve)
    page = context.new_page()
    page.add_init_script('window.fixtureMode=' + json.dumps(mode))
    page.goto(url)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', url)
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    page.frame_locator('#autoapply-sidebar iframe').locator('#send-structure').wait_for()
    panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    def isolated(method, *args):
        return worker.evaluate('''async ({tab,method,args})=>{
          const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},
            func:(method,args)=>globalThis.autoApplyContent[method](...args),args:[method,args]});
          return frame.result;
        }''', dict(tab=tab, method=method, args=list(args)))
    return page, worker, tab, panel, isolated, blocked


def action(panel, tab, kind):
    return panel.evaluate('v=>chrome.runtime.sendMessage(v)', dict(type=kind, tabId=tab))


def begin(panel, tab, kind):
    panel.evaluate('''v=>{globalThis.outcome=null;
      chrome.runtime.sendMessage(v).then(r=>globalThis.outcome=r,e=>globalThis.outcome={error:String(e)});
    }''', dict(type=kind, tabId=tab))


def finish(page, panel, timeout=16):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = panel.evaluate('()=>globalThis.outcome')
        if result is not None:
            return result
        page.wait_for_timeout(50)
    pytest.fail('Bounded application action did not settle')


@pytest.mark.browser
@pytest.mark.parametrize('method,mode', [('autofill', 'workday'), ('page-snapshot', 'workday'), ('page-snapshot', 'nested')])
def test_captured_workday_resume_semantics_and_retained_upload(loaded_extension, backend, method, mode):
    page, worker, tab, panel, isolated, blocked = open_form(loaded_extension, backend, mode)
    try:
        fields = isolated('scan')['fields']
        assert len(fields) == 1 and fields[0]['label'] == 'Resume/CV', fields
        result = action(panel, tab, method)
        assert not result.get('error'), result
        if method == 'page-snapshot':
            assert result['summary'].startswith('Filled 1'), result
            inspected = isolated('inspectPage', dict(repair=True))
            assert inspected['inspection']['status'] == 'controls_exposed', inspected
            assert inspected['diagnostic']['fields'][0].get('file_proxy'), inspected
        expect(page.locator('input[type=file]')).to_have_count(1)
        assert page.locator('input[type=file]').evaluate('e=>e.files.length') == 1
        expect(page.locator('[role=status]').first).to_contain_text('.pdf')
        saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
        assert saved['summary']['filled'] == 1, saved
        assert page.evaluate('[uploadAttempts,submissions]') == [1, 0]
        assert not blocked
    finally:
        page.close()


@pytest.mark.browser
def test_upload_abstains_for_ambiguous_purposes_and_preserves_user_files(loaded_extension, backend):
    page, _, tab, panel, isolated, blocked = open_form(loaded_extension, backend)
    try:
        for change in ('siblings', 'same_wrapper', 'generic', 'transcript', 'photo', 'cover', 'disabled', 'readonly', 'inert', 'occupied'):
            page.evaluate('workday()')
            page.evaluate('''kind=>{
              const input=document.querySelector('input[type=file]'),section=document.querySelector('section');
              if(kind==='siblings')section.insertAdjacentHTML('beforeend','<div><input type=file></div>');
              if(kind==='same_wrapper')input.insertAdjacentHTML('afterend','<input type=file hidden data-automation-id=file-upload-input-ref>');
              if(['generic','transcript','photo','cover'].includes(kind))section.querySelector('h4').textContent=
                {generic:'Supporting documents',transcript:'Transcript',photo:'Photo',cover:'Cover Letter'}[kind];
              if(kind==='disabled')input.disabled=true;
              if(kind==='readonly')input.readOnly=true;
              if(kind==='inert')section.inert=true;
              if(kind==='occupied'){const d=new DataTransfer();d.items.add(new File(['synthetic'],'manual.pdf',{type:'application/pdf'}));input.files=d.files;}
            }''', change)
            result = action(panel, tab, 'page-snapshot')
            assert not result.get('error'), (change, result)
            assert page.evaluate('uploadAttempts') == 0, change
            if change == 'occupied':
                assert page.locator('input[type=file]').evaluate('e=>e.files[0].name') == 'manual.pdf'
        assert page.evaluate('submissions') == 0 and not blocked
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('behavior,method', [('reject', 'page-snapshot'), ('clear', 'page-snapshot'), ('reset', 'autofill')])
def test_upload_rejection_or_replaced_input_never_counts_unrelated_remove(loaded_extension, backend, behavior, method):
    page, worker, tab, panel, isolated, blocked = open_form(loaded_extension, backend)
    try:
        page.evaluate('v=>behavior=v', behavior)
        result = action(panel, tab, method)
        assert not result.get('error'), result
        if method == 'page-snapshot':
            assert result['summary'].startswith('Filled 0') and '1 need attention' in result['summary'], result
            assert any(f['status'] == 'verification_failed' for f in result['findings']), result
        saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
        assert saved['summary']['filled'] == 0, saved
        assert saved['pending'] and not isolated('scan')['fields'][0]['answered']
        assert page.evaluate('[uploadAttempts,submissions]') == [1, 0] and not blocked
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('method', ['autofill', 'page-snapshot', 'automatic'])
def test_empty_smart_shell_late_nested_roots_fill_inputs_and_resume(loaded_extension, backend, method):
    page, worker, tab, panel, isolated, blocked = open_form(loaded_extension, backend, 'smart')
    try:
        initial = isolated('inputReadiness')
        assert initial['initializing'] and initial['controls'] == 0
        assert len(isolated('pageStructure')['inspection']['roots']) == 4
        if method == 'automatic':
            worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
        else:
            begin(panel, tab, method)
        # Mount only after the action has observed the genuinely empty shell.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults?.[id]', tab)
            if status and status.get('waiting'):
                break
            page.wait_for_timeout(50)
        else:
            pytest.fail('No waiting progress for initialized empty shell')
        page.evaluate('mount()')
        expect(page.locator('#email')).to_have_value('student@example.test', timeout=10000)
        expect(page.locator('spl-dropzone [role=status]')).to_contain_text('.pdf', timeout=10000)
        if method != 'automatic':
            assert not finish(page, panel).get('error')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
            if saved.get('summary', {}).get('filled') == 2 and not saved.get('busy'):
                break
            page.wait_for_timeout(50)
        assert saved['summary']['filled'] == 2 and not saved['busy'], saved
        assert len(isolated('scan')['fields']) == 2
        assert page.evaluate('[uploadAttempts,submissions]') == [1, 0] and not blocked
    finally:
        page.close()


@pytest.mark.browser
def test_persistent_empty_shell_is_bounded_and_retryable(loaded_extension, backend):
    page, _, tab, panel, isolated, blocked = open_form(loaded_extension, backend, 'smart')
    try:
        start = time.monotonic()
        result = action(panel, tab, 'page-snapshot')
        assert 7 <= time.monotonic() - start < 16, result
        assert not result.get('busy') and not result.get('error'), result
        assert 'loading' in result['summary'].lower() or 'inputs' in result['summary'].lower()
        assert 'unsupported' not in result['summary'].lower()
        assert isolated('scan')['fields'] == []
        page.evaluate('mount()')
        retry = action(panel, tab, 'page-snapshot')
        assert not retry.get('error'), retry
        expect(page.locator('#email')).to_have_value('student@example.test')
        assert page.evaluate('submissions') == 0 and not blocked
        page.evaluate('''()=>{stage.innerHTML='<div data-automation-id="signInContent"><form data-automation-id="signInFormo"><h3>Sign in</h3><label>Email Address<input type=email autocomplete=username></label><label>Password<input type=password value="synthetic-private-password"></label></form></div>';}''')
        assert isolated('inputReadiness').get('private')
        assert isolated('scan')['fields'] == []
        assert isolated('pageStructure')['nodes'] == []
        assert 'synthetic-private-password' not in json.dumps(isolated('inspectPage'))
    finally:
        page.close()


@pytest.mark.browser
def test_readiness_invalidation_prevents_late_writes(loaded_extension, backend):
    # Exercise both public action paths; each new document starts with no inputs.
    for change in ('navigation', 'document', 'edit', 'close', 'pause'):
        page, worker, tab, panel, isolated, blocked = open_form(loaded_extension, backend, 'smart')
        try:
            begin(panel, tab, 'autofill' if change in ('edit', 'close') else 'page-snapshot')
            page.wait_for_timeout(500)
            if change == 'navigation':
                page.evaluate("history.pushState({},'',location.pathname+'?changed')")
            elif change == 'document':
                page.reload()
            elif change == 'edit':
                page.evaluate("stage.insertAdjacentHTML('afterend','<label>Manual note<input id=manual></label>')")
                page.locator('#manual').fill('User answer')
            elif change == 'close':
                worker.evaluate('id=>autoApplySidebar(id,"closed")', tab)
            else:
                assert panel.evaluate('()=>chrome.runtime.sendMessage({type:"run-pause",paused:true})').get('paused')
            page.evaluate('mount()')
            page.wait_for_timeout(1500)
            expect(page.locator('#email')).to_have_value('')
            assert page.evaluate('[uploadAttempts,submissions]') == [0, 0] and not blocked
        finally:
            if change == 'pause':
                worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:false})')
            page.close()


@pytest.mark.parametrize('label,expected', [('Resume/CV', 'FILE'), ('Upload a file (5MB max)', 'NEEDS_INPUT'),
    ('File attachment', 'NEEDS_INPUT'), ('Transcript', 'NEEDS_INPUT'), ('Photo', 'NEEDS_INPUT')])
def test_backend_file_purpose_still_abstains_on_generic_attachments(backend, label, expected):
    result = resolve_answers(backend[0], dict(url=WORKDAY, allow_ai=False,
        fields=[dict(key='file', label=label, kind='file', required=True, options=[])]))
    answer = result['answers'][0]
    assert answer['status'] == expected
    if expected == 'FILE':
        assert answer['value'] == 'resume'
    else:
        assert not answer.get('value')


def test_readiness_lifecycle_offline_contract():
    result = subprocess.run(['node', str(FIXTURES / 'readiness.cjs'), str(ROOT / 'extension/background.js')],
                            check=True, capture_output=True, text=True, timeout=15)
    assert 'readiness lifecycle passed' in result.stdout


def file_analysis_payload(answered=False, proxy=True):
    from test_extension_api import analysis_payload
    payload = analysis_payload()
    field = payload['diagnostic']['fields'][0]
    field.update(label='Resume/CV', kind='file', answered=answered, reasons=['hidden_control'],
                 control=dict(tag='input', type='file', display='none', rect=[0, 0, 0, 0]),
                 ancestors=[dict(tag='div', visible=True, rect=[0, 0, 300, 80])],
                 repair_status='verified' if answered else 'verification_failed')
    if proxy:
        field['file_proxy'] = dict(tag='div', visible=True, rect=[0, 0, 300, 80],
                                   **{'data-automation-id': 'attachments-FileUpload'})
    payload['diagnostic']['repair'] = dict(filled=int(answered), attempts=1, stopped='none', before=dict(fields=[]))
    return payload


@pytest.mark.parametrize('answered', [False, True])
def test_hidden_owned_resume_proxy_assesses_verified_and_failed_uploads(dashboard, answered):
    from autoapply.extension_api import _page_analysis
    result = _page_analysis(dashboard, file_analysis_payload(answered), 'fixture.test')
    assert result['fields'][0]['active']
    assert result['summary'] == f'Filled {int(answered)} \u00b7 {int(not answered)} need attention'
    assert len(result['findings']) == int(not answered)
    if not answered:
        assert result['findings'][0]['status'] == 'verification_failed'
        assert not any('Hidden ancestor' in note for note in result['findings'][0]['notes'])


@pytest.mark.parametrize('hidden', ['unowned', 'parent', 'proxy'])
def test_hidden_uploads_without_exposed_owned_proxy_stay_inactive(dashboard, hidden):
    from autoapply.extension_api import _page_analysis
    payload = file_analysis_payload(proxy=hidden != 'unowned')
    field = payload['diagnostic']['fields'][0]
    if hidden == 'parent':
        field['reasons'].append('hidden_parent')
        field['ancestors'][0]['display'] = 'none'
    elif hidden == 'proxy':
        field['file_proxy']['display'] = 'none'
    result = _page_analysis(dashboard, payload, 'fixture.test')
    assert not result['fields'][0]['active'] and not result['findings']
    assert result['summary'] == 'No active form controls found.'


def test_verified_upload_count_survives_absent_active_geometry(dashboard):
    from autoapply.extension_api import _page_analysis
    payload = file_analysis_payload(answered=True, proxy=False)
    payload['diagnostic']['inspections'] = [dict(status='loading')]
    result = _page_analysis(dashboard, payload, 'fixture.test')
    assert not result['fields'][0]['active']
    assert result['summary'] == 'Filled 1 \u00b7 0 need attention'


@pytest.mark.parametrize('change', [dict(kind='text'), dict(detected=False), dict(control=dict(tag='input', type='text'))])
def test_owned_file_proxy_metadata_requires_detected_native_file(dashboard, change):
    from autoapply.dashboard_settings import SettingsRejected
    from autoapply.extension_api import _page_analysis
    payload = file_analysis_payload()
    payload['diagnostic']['fields'][0].update(change)
    with pytest.raises(SettingsRejected, match='Invalid analysis file proxy'):
        _page_analysis(dashboard, payload, 'fixture.test')
