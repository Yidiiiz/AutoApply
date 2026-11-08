"""Root-runnable actual MV3 gates. Worker collects only; every employer write is blocked."""
from pathlib import Path
import yaml

import pytest
from playwright.sync_api import expect

from test_extension import loaded_extension, extension_server, fixture_delay, KEY

pytestmark = pytest.mark.browser
FIXTURE = Path(__file__).parent/'fixtures/extension/interactions/t095.html'


@pytest.fixture
def interaction_profile(config):
    profile = config.profile
    profile['links'] = {'personal_website': 'https://old.test/'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')


def open_form(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY, autoSendStructure=False))
    page = context.new_page()
    url=server+'/interactions/t095.html'
    def route_request(route):
        assert route.request.method == 'GET', 'Employer POST must never leave the fixture'
        route.fulfill(path=str(FIXTURE), content_type='text/html')
    page.route(url, route_request)
    page.route('**/blocked-employer-write', lambda route: route.abort())
    page.goto(url)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', url)
    worker.evaluate('id=>autoApplySidebar(id,"open")',tab)
    panel=page.frame_locator(f'iframe[src^="chrome-extension://{extension_id}/sidepanel.html"]')
    return page,worker,tab,panel,requests


def settle(page,worker,tab):
    page.wait_for_function('()=>document.querySelector("#website").value!==""')
    worker.evaluate('''async id=>{for(let n=0;n<300;n++){
      const v=(await chrome.storage.session.get('tabResults')).tabResults?.[id];
      if(v?.writingSettled)return;await new Promise(r=>setTimeout(r,50));}throw Error('Autofill did not settle');}''',tab)


def test_t095_durable_native_and_panel_edits_scroll_skip_restore(interaction_profile, loaded_extension, extension_server):
    page,worker,tab,panel,requests=open_form(loaded_extension,extension_server)
    try:
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        page.locator('#website').fill('https://blur.test/')
        page.locator('#website').blur()
        expect(panel.locator('#saved-status')).to_have_text('Saved')
        assert any(p=='/ext/remember' and v.get('value')=='https://blur.test/' for p,_,v in requests)
        panel.locator('#tab-answers').click()
        card=panel.locator('.answer-card').filter(has=panel.locator('.field-label',has_text='Website'))
        card.get_by_role('button',name='Edit',exact=True).click()
        editor=card.get_by_role('textbox',name='Website',exact=True)
        expect(editor).to_be_focused()
        page.wait_for_function('scrollY>300')
        editor.fill('https://save.test/')
        card.get_by_role('button',name='Save',exact=True).click()
        expect(page.locator('#website')).to_have_value('https://save.test/')
        # Reopen and blur without Save; an intervening report must preserve typing.
        if card.get_by_role('button',name='Edit',exact=True).count():
            card.get_by_role('button',name='Edit',exact=True).click()
        editor=card.get_by_role('textbox',name='Website',exact=True)
        editor.fill('https://latest.test/')
        editor.blur()
        expect(page.locator('#website')).to_have_value('https://latest.test/')
        panel.locator('#tab-fill').click()
        prompt=panel.locator('.prompt').filter(has=panel.locator('p',has_text='Referral source')).first
        prompt.get_by_role('button',name='Skip',exact=True).click()
        expect(panel.get_by_role('button',name='Undo Skip',exact=True)).to_be_visible()
        assert page.locator('#referral').input_value()==''
        panel.get_by_role('button',name='Undo Skip',exact=True).click()
        expect(prompt.get_by_role('button',name='Skip',exact=True)).to_be_visible()
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        expect(page.locator('#website')).to_have_value('https://latest.test/')
        prompt.get_by_role('button',name='Skip',exact=True).click()
        expect(panel.get_by_role('button',name='Undo Skip',exact=True)).to_be_visible()
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        expect(panel.get_by_role('button',name='Undo Skip',exact=True)).to_be_visible()
        # A new document reuses the canonical correction and explicit Skip.
        page.reload()
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        expect(page.locator('#website')).to_have_value('https://latest.test/')
        worker.evaluate('id=>autoApplySidebar(id,"open")',tab)
        panel.locator('#tab-fill').click()
        expect(panel.get_by_role('button',name='Undo Skip',exact=True)).to_be_visible()
        assert page.locator('#referral').input_value()==''
        panel.get_by_role('button',name='Undo Skip',exact=True).click()
        expect(prompt.get_by_role('button',name='Skip',exact=True)).to_be_visible()
        assert page.evaluate('clicks===0 && posts===0')
    finally:
        page.close()


def test_t095_failed_panel_save_retains_draft_retry_and_latest_edit(interaction_profile, loaded_extension, extension_server):
    page,worker,tab,panel,requests=open_form(loaded_extension,extension_server)
    try:
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        panel.locator('#tab-answers').click()
        card=panel.locator('.answer-card').filter(has=panel.locator('.field-label',has_text='Website'))
        card.get_by_role('button',name='Edit',exact=True).click()
        editor=card.get_by_role('textbox',name='Website',exact=True)
        fixture_delay(worker,remember_failures=1)
        editor.fill('https://retry.test/')
        card.get_by_role('button',name='Save',exact=True).click()
        expect(card.get_by_text('Fixture remember transport failed',exact=True)).to_be_visible()
        expect(editor).to_have_value('https://retry.test/')
        expect(card.get_by_role('button',name='Save',exact=True)).to_be_enabled()
        # An unrelated report may refresh metadata while the failed draft stays.
        worker.evaluate('''async id=>{const {tabResults}=await chrome.storage.session.get('tabResults');
          await chrome.runtime.sendMessage({type:'autofill-result',result:tabResults[id]});}''',tab)
        expect(editor).to_have_value('https://retry.test/')
        card.get_by_role('button',name='Save',exact=True).click()
        expect(card.get_by_role('button',name='Edit',exact=True)).to_be_visible()
        card.get_by_role('button',name='Edit',exact=True).click()
        fixture_delay(worker,hold=['remember'])
        editor.fill('https://first-edit.test/')
        editor.blur()
        expect(card.get_by_role('button',name='Save',exact=True)).to_be_disabled()
        for _ in range(500):
            if any(p=='/ext/remember' and v.get('value')=='https://first-edit.test/' for p,_,v in requests):
                break
            page.wait_for_timeout(10)
        assert any(p=='/ext/remember' and v.get('value')=='https://first-edit.test/' for p,_,v in requests)
        editor.fill('https://middle-edit.test/')
        editor.fill('https://latest-edit.test/')
        fixture_delay(worker,release=['remember'])
        expect(page.locator('#website')).to_have_value('https://latest-edit.test/')
        editor.blur()
        expect(card.get_by_role('button',name='Edit',exact=True)).to_be_visible()
        values=[v['value'] for p,_,v in requests if p=='/ext/remember' and v.get('label')=='Website']
        assert 'https://first-edit.test/' in values
        assert 'https://middle-edit.test/' not in values
        assert values[-1]=='https://latest-edit.test/'
        page.reload()
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        expect(page.locator('#website')).to_have_value('https://latest-edit.test/')
        assert page.evaluate('clicks===0 && posts===0')
    finally:
        fixture_delay(worker,release=['remember'])
        page.close()


def test_t095_generated_draft_save_survives_report_and_panel_reload(interaction_profile, loaded_extension, extension_server):
    page,worker,tab,panel,requests=open_form(loaded_extension,extension_server)
    try:
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        settle(page,worker,tab)
        worker.evaluate('''async id=>{
          const {tabResults,applicationAnswers={},answerHistory={}}=await chrome.storage.session.get(['tabResults','applicationAnswers','answerHistory']);
          const result=tabResults[id],key=String(result.matched?.application_id || result.url.replace(/\\/apply(?:\\/.*)?$/i,'').split(/[?#]/)[0]);
          result.aiAnswers=[{label:'Why this fixture company?',kind:'textarea',value:'Old generated draft',source:'AI'}];
          applicationAnswers[key]=result.aiAnswers;
          answerHistory[key+':Why this fixture company?']={versions:[{answer:'Earlier draft'},{answer:'Old generated draft',warning:'Old warning'}],index:1};
          await chrome.storage.session.set({tabResults,lastResult:result,applicationAnswers,answerHistory});
        }''',tab)
        panel.locator('body').evaluate('()=>location.reload()')
        panel.locator('#tab-answers').click()
        card=panel.locator('.answer-card').filter(has=panel.locator('.field-label',has_text='Why this fixture company?'))
        card.get_by_role('button',name='Edit',exact=True).click()
        card.get_by_role('textbox').fill('Edited narrative for this employer.')
        card.get_by_role('button',name='Save',exact=True).click()
        expect(card.locator('.answer-value')).to_have_text('Edited narrative for this employer.')
        worker.evaluate('''async id=>{const {tabResults}=await chrome.storage.session.get('tabResults');
          await chrome.runtime.sendMessage({type:'autofill-result',result:tabResults[id]});}''',tab)
        expect(card.locator('.answer-value')).to_have_text('Edited narrative for this employer.')
        panel.locator('body').evaluate('()=>location.reload()')
        panel.locator('#tab-answers').click()
        expect(card.locator('.answer-value')).to_have_text('Edited narrative for this employer.')
        assert not any(p=='/ext/remember' and v.get('label')=='Why this fixture company?' for p,_,v in requests)
        assert page.evaluate('clicks===0 && posts===0')
    finally:
        page.close()


def test_t095_required_cover_cancel_optional_and_apply_enter(interaction_profile, loaded_extension, extension_server):
    page,worker,tab,panel,requests=open_form(loaded_extension,extension_server)
    try:
        worker.evaluate('''async()=>{const {server,key}=await chrome.storage.local.get(['server','key']);
          await fetch(server+'/ext/fixture-delay',{method:'POST',headers:{'Content-Type':'application/json','X-AutoApply-Key':key},body:JSON.stringify({hold:['cover'],auto_cover:true})});}''')
        worker.evaluate('id=>autoApplyAutofill(id)',tab)
        expect(panel.locator('#cancel-cover-letter')).to_be_visible()
        expect(page.locator('#required-writing')).not_to_have_value('')
        expect(page.locator('#first')).to_have_value('Test')
        expect(page.locator('#website')).to_have_value('')
        assert page.evaluate('clicks===0')
        panel.locator('#cancel-cover-letter').click()
        settle(page,worker,tab)
        assert page.locator('#cover').evaluate('e=>e.files.length')==0
        worker.evaluate('''async()=>{const {server,key}=await chrome.storage.local.get(['server','key']);await fetch(server+'/ext/fixture-delay',{method:'POST',headers:{'Content-Type':'application/json','X-AutoApply-Key':key},body:JSON.stringify({release:['cover']})});}''')
        page.wait_for_timeout(300)
        assert page.locator('#cover').evaluate('e=>e.files.length')==0
        panel.locator('#next-page').click()
        expect(page.locator('#feedback')).to_have_text('Required information missing')
        assert page.evaluate('clicks===1 && posts===0')
        panel.locator('#next-page').focus()
        panel.locator('#next-page').press('Enter')
        page.wait_for_function('clicks===2')
        # Long-answer Enter, IME and modified Enter cannot invoke final action.
        page.locator('#required-writing').press('Enter')
        panel.locator('#next-page').press('Shift+Enter')
        page.locator('#apply').dispatch_event('keydown',dict(key='Enter',isComposing=True))
        assert page.evaluate('clicks===2 && posts===0')
        automatic=worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>autoApplyContent.nextPage(true)}))[0].result''',tab)
        assert not automatic['clicked'] and page.evaluate('clicks===2')
    finally:
        page.close()
