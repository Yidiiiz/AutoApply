"""Actual MV3 content/background scripts against snapshot-derived local fixtures."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_extension import loaded_extension, extension_server, KEY, ROOT
from test_t053_answers import ROLE, LANGUAGE, ROLES

pytestmark = pytest.mark.browser


def fixture_page(loaded_extension, extension_server, name):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.route('**/t053*.html', lambda route: route.fulfill(path=str(ROOT / 'tests/fixtures/extension' / name), content_type='text/html'))
    page.goto(server + '/' + name)
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    return context, worker, extension_id, page, tab_id, requests


def content(worker, tab_id, method, value=None):
    return worker.evaluate('''async ({id,method,value})=>(await chrome.scripting.executeScript({target:{tabId:id},
        func:async (method,value)=>globalThis.autoApplyContent[method](value),args:[method,value]}))[0].result''',
        dict(id=tab_id, method=method, value=value))


def test_snapshot_checkbox_questions_and_pending_options(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = fixture_page(loaded_extension, extension_server, 't053.html')
    scan = content(worker, tab_id, 'scan')
    fields = {f['label']: f for f in scan['fields']}
    assert set(fields) == {ROLE, LANGUAGE, 'Which fixture tracks interest you?', 'I agree'}
    roles, languages, unknown, terms = [fields[label] for label in [ROLE, LANGUAGE, 'Which fixture tracks interest you?', 'I agree']]
    assert roles['options'] == ['Front-End','Back-End','Full Stack','Machine Learning Engineer','Data Engineer','Mobile (iOS)','Mobile (Android)','I do not have a preference','I am not applying for a Software or Mobile Developer role']
    assert roles['required'] and roles['multiple'] and roles['kind'] == 'checkbox'
    assert languages['options'] == ['Spanish', 'French', 'None of the Above'] and languages['required']
    pending = content(worker, tab_id, 'fill', [dict(key=unknown['key'], status='NEEDS_INPUT')])
    assert pending['pending'][0]['label'] == unknown['label']
    assert pending['pending'][0]['options'] == ['Track A','Track B']
    answers = [dict(key=f['key'], status='RESOLVED', value=value) for f,value in [(roles,ROLES),(languages,['None of the Above']),(terms,'I agree'),(unknown,['Track A','Track B'])]]
    filled = content(worker, tab_id, 'fill', answers)
    assert filled['filled'] == 4 and not filled['pending'] and not filled['errors']
    assert page.locator('input:checked').count() == 10
    assert page.locator('#termsAndConditions--acceptTermsAndAgreements').is_checked()
    assert page.evaluate('submissions') == 0
    again = content(worker, tab_id, 'fill', answers)
    assert again['filled'] == 0 and again['skipped'] == 4


@pytest.mark.parametrize('language', ['English', '', 'Spanish'])
def test_english_proficiency_rerender_preserves_language_and_study(loaded_extension, extension_server, language):
    _, worker, _, page, tab_id, _ = fixture_page(loaded_extension, extension_server, 't053_sections.html')
    page.evaluate('value=>{window.language=value;render();}', language)
    profile = dict(languages=[dict(language='English', fluent=True, proficiency='highest')],
                   education=[dict(school='Fixture University',major='Computer Science')])
    result = content(worker, tab_id, 'prepareSections', profile)
    if language == 'Spanish':
        assert page.evaluate('language') == 'Spanish'
        assert page.evaluate('levels') == {} and not page.evaluate('fluent')
    else:
        assert page.evaluate('language') == 'English'
        assert page.evaluate('fluent') is True
        assert len(page.evaluate('levels')) == 4
        assert set(page.evaluate('Object.values(levels)')) == {'4 - Fluent'}
        assert page.evaluate('nativeClicks') == 1
        content(worker, tab_id, 'prepareSections', profile)
        assert page.evaluate('nativeClicks') == 1
    assert page.evaluate('[adds,submissions]') == [0,0]
    assert page.locator('[data-automation-id=selectedItem]').all_text_contents() == ['Computer Science']
    assert page.locator('#education-26--fieldOfStudy').input_value() == 'stale search'


def test_stale_study_search_is_not_a_committed_pill(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = fixture_page(loaded_extension, extension_server, 't053_sections.html')
    page.locator('[data-automation-id=selectedItemList]').evaluate('el=>el.replaceChildren()')
    fields = content(worker, tab_id, 'scan')['fields']
    study = next(f for f in fields if f['label'] == 'Field of Study')
    assert not study['answered']
    result = content(worker, tab_id, 'fill', [dict(key=study['key'],status='RESOLVED',value='Computer Science')])
    assert result['filled'] == 1 and not result['pending']
    assert page.locator('[data-automation-id=selectedItem]').all_text_contents() == ['Computer Science']
    assert page.evaluate('submissions') == 0


@pytest.mark.parametrize('mode', ['replaced', 'native-only', 'stale-label', 'transient-pill'])
def test_generic_study_requires_stable_pill_after_current_activation(loaded_extension, extension_server, mode):
    _, worker, _, page, tab_id, requests = fixture_page(loaded_extension, extension_server, 't053_sections.html')
    page.locator('[data-automation-id=selectedItemList]').evaluate('el=>el.replaceChildren()')
    page.evaluate('''mode=>{
        window.activations=0;window.replacements=0;
        document.addEventListener('mousedown',event=>{
            const row=event.target.closest('#study-popup [data-automation-id=menuItem]');
            if(!row || mode!=='replaced' || replacements)return;
            const replacement=row.cloneNode(true);
            replacement.querySelector('input').onclick=row.querySelector('input').onclick;
            row.replaceWith(replacement);replacements++;
        },true);
        document.addEventListener('click',event=>{
            const row=event.target.closest('#study-popup [data-automation-id=menuItem]');
            if(!row)return;
            activations++;
            if(mode==='replaced')return;
            event.stopImmediatePropagation();
            if(mode==='stale-label')row.setAttribute('aria-label','Computer Science checked');
            if(mode==='transient-pill'){
                const pill=document.createElement('li');pill.dataset.automationId='selectedItem';
                pill.textContent='Computer Science';
                document.querySelector('[data-automation-id=selectedItemList]').append(pill);
                setTimeout(()=>pill.remove(),100);
            }
        },true);
    }''',mode)
    study=next(f for f in content(worker,tab_id,'scan')['fields'] if f['label']=='Field of Study')
    result=content(worker,tab_id,'fill',[dict(key=study['key'],status='RESOLVED',value='Computer Science')])
    committed=mode=='replaced'
    assert result['filled']==int(committed)
    assert len(result['pending'])==int(not committed)
    assert page.locator('[data-automation-id=selectedItem]').all_text_contents()==(['Computer Science'] if committed else [])
    assert page.evaluate('activations')==1
    assert page.evaluate('replacements')==int(committed)
    assert page.locator('#education-26--fieldOfStudy').input_value()==''
    assert page.evaluate('submissions')==0
    assert not any(path=='/ext/skills/choose' for path,_,_ in requests)
    steps=[entry for path,_,data in requests if path=='/ext/trace' for entry in data['entries']]
    study_steps=[entry for entry in steps if entry.get('label')=='Field of Study']
    assert all(entry['step']!='skills' for entry in study_steps)
    assert any(entry['action']=='verified' and entry.get('verified') is committed for entry in study_steps)
    if committed:
        assert any(entry['action']=='activation interrupted' for entry in study_steps)


def test_immediate_busy_before_delayed_scan_and_error_reset(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, _ = fixture_page(loaded_extension, extension_server, 't053.html')
    worker.evaluate('''async id=>chrome.scripting.executeScript({target:{tabId:id},func:()=>{
        window.scanCalls=0;autoApplyContent.scan=async()=>{window.scanCalls++;await new Promise(r=>setTimeout(r,2000));throw Error('Fixture delayed scan failure');};
    }})''', tab_id)
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#autofill').click()
    expect(panel.locator('#autofill')).to_be_disabled(timeout=1000)
    expect(panel.locator('#autofill')).to_have_text('Filling…', timeout=1000)
    state = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]',tab_id)
    assert state['busy'] and state['tabId'] == tab_id
    expect(panel.locator('#autofill')).to_be_enabled(timeout=10000)
    expect(panel.locator('#error')).to_contain_text('Fixture delayed scan failure')
    assert page.evaluate('submissions') == 0


def test_all_profile_skills_and_reset_do_not_create_exclusions(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, requests = fixture_page(loaded_extension, extension_server, 't046.html')
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    expect(panel.locator('#skills-progress')).to_have_text('1 matched')
    panel.locator('#skills-menu > summary').click()
    panel.locator('#other-skills summary').click()
    expect(panel.get_by_label('Python', exact=True)).to_be_checked()
    expect(panel.get_by_label('Rust', exact=True)).to_be_checked()
    panel.get_by_label('Rust', exact=True).uncheck()
    panel.locator('#autofill').click()
    expect(panel.locator('#autofill')).to_be_enabled(timeout=30000)
    pills = page.locator('[data-automation-id=selectedItem]').all_text_contents()
    assert {'TypeScript', 'Python'} <= set(pills) and 'Rust' not in pills and 'Kubernetes' not in pills
    expect(panel.get_by_label('Python', exact=True)).not_to_be_checked()
    expect(panel.get_by_label('Rust', exact=True)).not_to_be_checked()
    assert panel.get_by_role('button',name='Remove TypeScript',exact=True).count() == 0
    before = page.evaluate('searches.slice()')
    panel.locator('#autofill').click()
    expect(panel.locator('#autofill')).to_be_enabled(timeout=30000)
    assert page.evaluate('searches') == before
    stored = panel.evaluate('async id=>chrome.runtime.sendMessage({type:"skill-selection",tabId:id})', tab_id)
    assert stored['selection']['excluded'] == ['Rust']
    assert not any(path == '/ext/skills/choose' for path,_,_ in requests)


def test_c_cpp_split_before_search_preserves_punctuation_and_pills(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = fixture_page(loaded_extension, extension_server, 't046.html')
    page.evaluate('''()=>{catalog.C=['C++','C#','C'];catalog['C++']=['C','C#','C++'];catalog['C#']=['C','C++','C#'];}''')
    before = page.locator('[data-automation-id=selectedItem]').all_text_contents()
    result = content(worker, tab_id, 'autofillSkills', ['C/C++','C#','Python','python'])
    assert result['added'] == 4 and result['skipped'] == 0
    searches = page.evaluate('searches')
    assert searches == ['C','C++','C#','Python']
    assert set(page.locator('[data-automation-id=selectedItem]').all_text_contents()) == set(before + ['C','C++','C#','Python'])
    result = content(worker, tab_id, 'autofillSkills', ['C/C++','C#','Python'])
    assert result['added'] == result['skipped'] == 0
    assert page.evaluate('searches') == searches


# Executed by the configured extension/API coverage wrappers; avoid duplicate collection.
__test__ = False
