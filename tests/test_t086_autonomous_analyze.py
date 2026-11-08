"""T086: synthetic input inventory, bounded repair and private SPA account state."""
import json
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from autoapply.extension_api import resolve_answers
from autoapply.dashboard_settings import SettingsRejected
from test_dashboard import dashboard, offline_descriptions
from test_extension import loaded_extension, select_analyze
from test_t077_analyze import backend, URL
from test_t085_answer_repair import repair_profile, field, proposal, save_profile

FIXTURE = Path(__file__).parent / 'fixtures/extension/t086.html'


def test_fast_pass_returns_pending_without_provider(dashboard, repair_profile):
    rows = resolve_answers(dashboard, dict(url='https://fixture.test/job', allow_ai=False,
        fields=[field(), field('email', label='Email', kind='email', options=[])]))['answers']
    assert rows[0]['reason_code'] == 'reasoning_pending'
    assert rows[0]['value'] is None
    assert rows[1]['status'] == 'RESOLVED'


def test_default_ai_remains_compatible(dashboard, repair_profile, monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: calls.append(request) or {'answers': [proposal()]})
    row = resolve_answers(dashboard, dict(url='https://fixture.test/job', fields=[field()]))['answers'][0]
    assert row['value'] == 'Python (programming)' and len(calls) == 1


@pytest.mark.parametrize('flag', [None, 0, 'false'])
def test_fast_flag_requires_boolean(dashboard, flag):
    with pytest.raises(SettingsRejected, match='allow_ai'):
        resolve_answers(dashboard, dict(url='https://fixture.test/job', fields=[], allow_ai=flag))


@pytest.mark.parametrize('preference', [True, False, None])
def test_ordinary_privacy_acknowledgement_requires_explicit_preference(dashboard, config, repair_profile, preference):
    preferences = repair_profile['application_preferences']
    if preference is None:
        preferences.pop('ordinary_policy_acknowledgement')
    else:
        preferences['ordinary_policy_acknowledgement'] = preference
    save_profile(config, repair_profile)
    labels = ['I agree to the privacy policy', 'I acknowledge the privacy notice',
              'By checking this box, you will declare that you read and understand the privacy policy of Synthetic.']
    rows = resolve_answers(dashboard, dict(url='https://fixture.test/job', allow_ai=False,
        fields=[field(str(i), label=label, kind='checkbox', options=[label]) for i, label in enumerate(labels)]))['answers']
    assert [row['value'] for row in rows] == [preference] * len(labels)


@pytest.mark.parametrize('label', [
    'I do not agree to the privacy policy',
    'I agree to the privacy policy and waive my rights',
    'I read and understand the privacy policy and release all claims',
    'I read and understand the privacy policy and certify my answers are accurate',
    'I read and understand the privacy policy and agree to marketing messages',
    'I read and understand the privacy policy and have no criminal convictions',
])
def test_privacy_preference_does_not_authorize_other_claims(label):
    from autoapply.profile_rules import preference_boolean
    assert preference_boolean(label) == (None, None)


def open_form(loaded_extension, backend, automatic=False, html=None):
    context, worker, _ = loaded_extension
    service, server, state, _ = backend
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server,
        key=service.settings.extension_key(), autoSendStructure=automatic, autoAccountFill=False))
    context.route('https://jobs.lever.co/**', lambda route: route.fulfill(body=html or FIXTURE.read_text(encoding='utf-8'), content_type='text/html')
                  if route.request.method == 'GET' else route.abort())
    page = context.new_page()
    page.goto(URL)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', URL)
    def isolated(method, *args):
        return worker.evaluate("""async v=>{
          const [f]=await chrome.scripting.executeScript({target:{tabId:v.tab},func:async v=>{
            try{return {value:await autoApplyContent[v.method](...v.args)};}
            catch(error){return {caught:error.message};}
          },args:[v]});
          if(f.error || f.result?.caught)throw Error(f.result?.caught || f.error.message);
          return f.result.value;
        }""", dict(tab=tab, method=method, args=list(args)))
    return page, worker, tab, isolated


def analyze(page, worker, tab):
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    select_analyze(page.frame_locator('#autoapply-sidebar iframe'))
    panel = next(f for f in page.frames if '/sidepanel.html' in f.url)
    return panel.evaluate('id=>chrome.runtime.sendMessage({type:"page-snapshot",tabId:id})', tab)


@pytest.mark.browser
def test_inventory_binds_contenteditable_excludes_navigation_and_mirrors(loaded_extension, backend):
    page, worker, tab, isolated = open_form(loaded_extension, backend)
    fields = isolated('scan')['fields']
    assert any(f['label'] == 'Which programming tools do you use?' and f['kind'] == 'textarea' for f in fields)
    assert not any('mirror' in f['label'] or f['label'] in {'Settings', 'Search'} for f in fields)
    assert any(f['kind']=='checkbox' and f['label']=='I agree to the privacy policy' for f in fields)
    assert len({f['key'] for f in fields}) == len(fields)
    assert page.evaluate('[opens,actions.length]') == [0, 0]
    prepared=isolated('repairStart','aria-edit')['fields']
    page.locator('#aria-policy').click()
    email=next(f for f in prepared if f['kind']=='email')
    with pytest.raises(Exception, match='changed'):
        isolated('repairFill','aria-edit',[dict(key=email['key'],status='RESOLVED',value='stale@example.test')])
    expect(page.locator('#email')).to_be_empty()


@pytest.mark.browser
def test_fast_fill_then_one_grounded_batch_exact_options(loaded_extension, backend, config, monkeypatch):
    service, _, state, _ = backend
    profile = config.profile
    profile.update(skills=['Python'], demographics={'hispanic_latino':'decline'},
                   application_preferences={'ordinary_policy_acknowledgement':True})
    save_profile(config, profile)
    calls=[]
    def writer(request):
        calls.append(request)
        return {'answers':[proposal(f['key'], 'Python (programming)' if f['options'] else 'Python') for f in request['fields']]}
    monkeypatch.setattr(service, 'run_writing', writer)
    page, worker, tab, isolated = open_form(loaded_extension, backend)
    isolated('scan', {'repair': True})
    # Native focus()/blur() emits trusted events without a changed answer.
    page.locator('#tools').evaluate('e=>{e.focus();e.blur();}')
    assert not [p for path,p in state['posts'] if path=='/ext/remember']
    response = analyze(page, worker, tab)
    assert response.get('ok'), response
    expect(page.locator('#name')).to_have_value('Test Student')
    expect(page.locator('#tools-text')).to_have_text('Python')
    expect(page.locator('#tools')).to_have_text('Python (programming)')
    expect(page.locator('#privacy')).to_be_checked()
    expect(page.locator('#aria-policy')).to_have_attribute('aria-checked','true')
    expect(page.locator('#achieved')).to_have_value('')
    expect(page.locator('#spouse')).to_have_value('')
    expect(page.locator('#hispanic')).to_have_value('')
    assert len(calls) == 1 and page.evaluate('opens') == 1
    requests = [payload for path,payload in state['posts'] if path=='/ext/answers']
    assert len(requests) == 2 and requests[0]['allow_ai'] is False
    assert all('password' not in json.dumps(p).lower() for p in requests)
    assert page.evaluate('actions') == []


@pytest.mark.browser
def test_role_checkbox_button_retains_caption_and_never_submits(loaded_extension, backend, config):
    profile = config.profile
    profile['application_preferences'] = {'ordinary_policy_acknowledgement': True}
    save_profile(config, profile)
    html = '''<!doctype html><form onsubmit="event.preventDefault();submits++"><h1>Job application</h1>
      <label>Email<input type=email></label>
      <fieldset><label><input id=native-policy type=checkbox>I acknowledge the privacy notice</label></fieldset>
      <fieldset><button id=policy type=button role=checkbox tabindex=0 aria-checked=false
        aria-label="I agree to the privacy policy"
        onclick="this.setAttribute('aria-checked',this.getAttribute('aria-checked')==='true'?'false':'true')">Privacy policy</button></fieldset>
      <button type=submit>Submit application</button></form><script>let submits=0</script>'''
    page, worker, tab, isolated = open_form(loaded_extension, backend, html=html)
    result = analyze(page, worker, tab)
    assert result.get('ok'), result
    expect(page.locator('#native-policy')).to_be_checked()
    expect(page.locator('#policy')).to_have_attribute('aria-checked', 'true')
    expect(page.locator('#policy')).to_have_text('Privacy policy')
    assert page.locator('#policy').evaluate('e=>e.value') == ''
    fields = isolated('inspectPage')['diagnostic']['fields']
    assert any(f['kind'] == 'checkbox' and f['detected'] and f['label'] == 'I agree to the privacy policy' for f in fields)
    assert page.evaluate('submits') == 0


@pytest.mark.browser
def test_native_batch_latency_and_deferred_reversion(loaded_extension, backend):
    page, _, _, isolated = open_form(loaded_extension, backend)
    fields = isolated('repairStart', 'latency')['fields']
    chosen = [f for f in fields if f['kind'] in {'text','email','tel','url'}][:7]
    page.evaluate("document.querySelector('#name').addEventListener('input',e=>setTimeout(()=>e.target.value='',200))")
    start=time.monotonic()
    isolated('repairFill', 'latency', [dict(key=f['key'],status='RESOLVED',value='1234567890' if f['kind']=='tel' else 'synthetic') for f in chosen])
    elapsed=time.monotonic()-start
    result=isolated('repairFinish', 'latency')
    assert result['filled'] == len(chosen)-1
    assert elapsed < 2.5, f'Native batch took {elapsed:.3f}s; old mandatory 400ms x {len(chosen)} exceeds this before text settlement'


@pytest.mark.browser
def test_automatic_initial_step_no_recursion_no_submit(loaded_extension, backend):
    page, worker, tab, isolated = open_form(loaded_extension, backend, automatic=True)
    expect(page.locator('#email')).to_have_value('student@example.test', timeout=10000)
    live_result(worker, tab, 14)
    count=backend[2]['answers']
    assert count == 2
    page.wait_for_timeout(800)
    assert backend[2]['answers'] == count
    assert page.locator('#autoapply-sidebar').count() == 0
    page.evaluate('step()')
    expect(page.locator('#step-email')).to_have_value('student@example.test', timeout=10000)
    live_result(worker, tab, 1)
    page.wait_for_timeout(800)
    assert backend[2]['answers'] == count + 1
    assert isolated('navigationState')['kind'] == 'submit'
    assert page.evaluate('actions') == []


def live_result(worker, tab, count):
    return worker.evaluate('''async ({tab,count})=>{
      for(let i=0;i<100;i++){
        const r=(await chrome.storage.session.get('tabResults')).tabResults?.[tab];
        if(r?.fields.length===count && r.summary.filled && !r.busy)return r;
        await new Promise(resolve=>setTimeout(resolve,100));
      }throw Error('Live Analyze result did not reconcile');
    }''', dict(tab=tab,count=count))


@pytest.mark.browser
def test_manual_autofill_on_normal_application_with_submit(loaded_extension, backend):
    html = '''<!doctype html><form onsubmit="event.preventDefault();submits++">
      <h1>Job application</h1><label>Email<input id=email type=email required></label>
      <button type=submit>Submit application</button></form><script>let submits=0</script>'''
    page, worker, tab, isolated = open_form(loaded_extension, backend, automatic=False, html=html)
    assert worker.evaluate('async ()=>(await chrome.storage.local.get("autoSendStructure")).autoSendStructure') is False
    scanned = isolated('scan')
    assert scanned['next_action']['kind'] == 'submit' and len(scanned['fields']) == 1
    expect(page.locator('#email')).to_be_empty()
    assert backend[2]['answers'] == 0

    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert not result.get('error'), result
    expect(page.locator('#email')).to_have_value('student@example.test')
    assert result['summary']['filled'] == 1 and result['pending'] == []
    assert result['next_action']['kind'] == 'submit'
    assert backend[2]['answers'] == 1
    assert not [p for path, p in backend[2]['posts'] if path in {'/ext/write', '/ext/applied'}]
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#next-page')).to_have_text('Submit')
    expect(panel.locator('#next-page')).to_be_enabled()
    assert page.evaluate('submits') == 0


@pytest.mark.browser
def test_automatic_only_initial_submit_footer_and_attribution_once(loaded_extension, backend, db):
    application_id = backend[3][0]
    original_status = dict(db.one('SELECT status,submitted_at FROM applications WHERE id=?', (application_id,)))
    html='''<!doctype html><form onsubmit="event.preventDefault();submits++;this.innerHTML='<h1>Application submitted</h1>'">
      <h1>Job application</h1><label>Email<input id=email type=email required></label>
      <button type=submit>Submit application</button></form><script>let submits=0</script>'''
    page,worker,tab,_=open_form(loaded_extension,backend,automatic=True,html=html)
    expect(page.locator('#email')).to_have_value('student@example.test',timeout=10000)
    result=live_result(worker,tab,1)
    assert result['matched']['application_id']==backend[3][0]
    assert result['summary']['filled']==1 and result['pending']==[]
    assert result['next_action']['kind']=='submit' and result['page']['url']==page.url
    saved=worker.evaluate('async id=>(await chrome.storage.session.get("autofillTabs")).autofillTabs[id]',tab)
    assert saved['matched']==result['matched'] and saved['url']==page.url
    assert any(path=='/ext/match' for path,_ in backend[2]['posts'])
    assert page.evaluate('submits')==0
    assert not [p for path,p in backend[2]['posts'] if path=='/ext/applied']
    worker.evaluate('id=>autoApplySidebar(id,"open")',tab)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#next-page')).to_have_text('Submit')
    expect(panel.locator('#next-page')).to_be_enabled()
    panel.locator('#next-page').click()
    expect(panel.locator('#applied-status')).to_be_visible(timeout=10000)
    # Repeated observed confirmations cannot create a second tracking write.
    page.locator('h1').evaluate("e=>e.textContent='Thank you for applying'")
    page.wait_for_timeout(700)
    applied=[p for path,p in backend[2]['posts'] if path=='/ext/applied']
    assert len(applied)==1 and applied[0]['application_id']==backend[3][0]
    assert page.evaluate('submits')==1
    assert db.setting('duplicate_submission_guard:'+str(backend[3][0]))
    assert any(item['application_id'] == application_id for item in backend[0].applied()['applications'])
    assert dict(db.one('SELECT status,submitted_at FROM applications WHERE id=?', (application_id,))) == original_status
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE application_id=? AND kind='EXT_SUBMISSION_CONFIRMED'",
                  (application_id,))['n'] == 1


@pytest.mark.browser
def test_same_url_reconciliation_retains_controls_and_manual_edits_not_recycled_keys(loaded_extension, backend):
    html='''<!doctype html><form id=application><h1>Job application</h1>
      <label>Email<input id=email type=email></label><label>City<input id=city></label>
      <label>Phone<input id=phone type=tel></label><button type=button>Next</button></form>'''
    page,worker,tab,_=open_form(loaded_extension,backend,automatic=True,html=html)
    before=live_result(worker,tab,3)
    page.locator('#email').fill('manual@example.test')
    # Replace two controls, explicitly recycling a prior key. Keep the email node.
    page.evaluate('''()=>{
      const key=city.dataset.autoapplyKey;city.closest('label').remove();phone.closest('label').remove();
      application.insertAdjacentHTML('beforeend',`<label>Military spouse<input id=spouse data-autoapply-key="${key}"></label>`);
      application.querySelector('button').textContent='Submit application';
      application.querySelector('button').type='submit';
    }''')
    after=live_result(worker,tab,2)
    expect(page.locator('#email')).to_have_value('manual@example.test')
    assert {f['label'] for f in after['fields']}=={'Email','Military spouse'}
    assert after['next_action']['kind']=='submit'
    assert after['summary']['filled']==1 and len(after['completed'])==1
    assert after['completed'][0]['value']=='manual@example.test'
    assert after['summary']['needs_input']==['Military spouse']
    spouse=next(f for f in after['fields'] if f['label']=='Military spouse')
    city_before=next(f for f in before['fields'] if f['label']=='City')
    assert spouse['key']==city_before['key'] and spouse['control_identity']!=city_before['control_identity']


@pytest.mark.browser
def test_optional_missing_fact_stays_pending_and_intentional_skip_is_quiet(loaded_extension, backend, config):
    profile=config.profile
    profile['demographics']={'hispanic_latino':'decline'}
    save_profile(config,profile)
    html='''<!doctype html><form><h1>Job application</h1><label>Email<input id=email type=email></label>
      <label>Are you currently a member of the National Guard or Reserves?<select id=guard><option value="">Select</option><option>Yes</option><option>No</option></select></label>
      <label>Are you Hispanic or Latino?<select id=race><option value="">Select</option><option>Yes</option><option>No</option></select></label></form>'''
    page,worker,tab,_=open_form(loaded_extension,backend,automatic=True,html=html)
    result=live_result(worker,tab,3)
    guard=next(f for f in result['fields'] if 'National Guard' in f['label'])
    race=next(f for f in result['fields'] if 'Hispanic' in f['label'])
    assert guard['reason_code']=='needs_fact' and guard['repair_status']=='unresolved'
    assert race['reason_code']==race['repair_status']=='optional_skip'
    assert [f['key'] for f in result['pending']]==[guard['key']]
    expect(page.locator('#guard')).to_have_value('')
    expect(page.locator('#race')).to_have_value('')
    assert not [p for path,p in backend[2]['posts'] if path=='/ext/snapshot']


@pytest.mark.browser
def test_automatic_shape_signal_drains_after_inflight_analysis(loaded_extension, backend):
    page,worker,tab,_=open_form(loaded_extension,backend)
    worker.evaluate('id=>autoApplySidebar(id,"open")',tab)
    select_analyze(page.frame_locator('#autoapply-sidebar iframe'))
    panel=next(f for f in page.frames if '/sidepanel.html' in f.url)
    state=backend[2]
    state['hold']=threading.Event()
    try:
        panel.evaluate('''id=>{globalThis.analysis=chrome.runtime.sendMessage({type:'page-snapshot',tabId:id});}''',tab)
        for _ in range(100):
            if state['entered'].is_set():
                break
            page.wait_for_timeout(50)
        assert state['entered'].is_set()
        worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
        page.evaluate('step()')
    finally:
        state['hold'].set()
    expect(page.locator('#step-email')).to_have_value('student@example.test',timeout=10000)
    result=live_result(worker,tab,1)
    assert result['summary']['filled']==1 and result['next_action']['kind']=='submit'
    count=state['answers']
    assert count == 2
    page.wait_for_timeout(800)
    assert state['answers']==count and page.evaluate('actions')==[]


@pytest.mark.browser
def test_ordinary_site_abstains(loaded_extension, backend):
    page, worker, tab, isolated = open_form(loaded_extension, backend)
    page.locator('#application').evaluate("e=>e.innerHTML='<h1>Contact us</h1><label>Email<input type=email></label><button type=submit>Submit application</button>'")
    worker.evaluate('()=>chrome.storage.local.set({autoSendStructure:true})')
    page.wait_for_timeout(700)
    assert isolated('runtimeState')['application'] is False
    assert isolated('navigationState')['kind'] == 'none'
    assert backend[2]['answers'] == 0


@pytest.mark.browser
@pytest.mark.parametrize('change', ['edit', 'close', 'shape'])
def test_repair_cancels_stale_writes(loaded_extension, backend, change):
    page, worker, tab, isolated = open_form(loaded_extension, backend)
    fields=isolated('repairStart','cancel')['fields']
    email=next(f for f in fields if f['kind']=='email')
    if change=='edit':
        page.locator('#email').fill('manual@example.test')
    elif change=='close':
        isolated('sidebar','closed')
    else:
        page.evaluate('step()')
    with pytest.raises(Exception, match='changed'):
        isolated('repairFill','cancel',[dict(key=email['key'],status='RESOLVED',value='stale@example.test')])
    assert not page.locator('input').evaluate_all("es=>es.some(e=>e.value==='stale@example.test')")
    if change=='edit':
        expect(page.locator('#email')).to_have_value('manual@example.test')


@pytest.mark.browser
@pytest.mark.parametrize('age_ms', [0, 61000])
def test_long_lived_account_observer_new_form_after_trusted_edit(loaded_extension, backend, age_ms):
    page, worker, tab, isolated = open_form(loaded_extension, backend)
    # Synthetic allowlisted account origin; all external traffic remains aborted.
    backend[0].settings.credentials.save(dict(email='synthetic@example.test',password='Synthetic-Only!7'))
    page.context.route('https://fixture.myworkdayjobs.com/**', lambda route: route.fulfill(body='<form><h1>Sign in</h1><label>Email<input type=email></label><label>Password<input type=password></label><button>Sign in</button></form>', content_type='text/html') if route.request.method=='GET' else route.abort())
    page.goto('https://fixture.myworkdayjobs.com/login')
    tab=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    page.locator('input[type=email]').fill('manual@example.test')
    def describe():
        return worker.evaluate("""async url=>{const t=(await chrome.tabs.query({})).find(t=>t.url===url);const [f]=await chrome.scripting.executeScript({target:{tabId:t.id},func:()=>autoApplyCredentials.describe()});return f.result;}""", page.url)
    assert describe()['autoEligible'] is False
    if age_ms:
        page.wait_for_timeout(age_ms)
    page.locator('form').evaluate("e=>e.outerHTML=e.outerHTML")
    assert describe()['autoEligible'] is True
    worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
    page.locator('form').evaluate("e=>e.outerHTML=e.outerHTML")
    page.wait_for_timeout(500)
    expect(page.locator('input[type=email]')).to_have_value('synthetic@example.test', timeout=10000)
    expect(page.locator('input[type=password]')).to_have_value('Synthetic-Only!7')
    # A fresh mode and fresh controls are eligible even on the same SPA URL.
    assert describe()['mode'] == 'login'
    assert 'manual@example.test' not in json.dumps(describe())
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#next-page')).to_have_text('Sign in')
    page.locator('form').evaluate("e=>e.innerHTML='<h1>Create account</h1><label>Email<input type=email></label><label>Password<input type=password></label><label>Confirm password<input type=password name=confirmPassword></label><button>Create account</button>'")
    expect(panel.locator('#next-page')).to_have_text('Create account')
    expect(page.locator('input[name=confirmPassword]')).to_have_value('Synthetic-Only!7', timeout=10000)
    page.locator('form').evaluate("e=>e.innerHTML='<h1>Job application</h1><label>Email<input type=email></label><button type=submit data-automation-id=pageFooterNextButton>Submit application</button>'")
    expect(panel.locator('#next-page')).to_have_text('Submit')
    expect(panel.locator('#next-page')).to_be_enabled()
    assert not [payload for path,payload in backend[2]['posts'] if path in {'/ext/applied','/ext/run/result'}]


@pytest.mark.browser
def test_external_account_consent_edit_and_same_nodes_mode_eligibility(loaded_extension, backend):
    page,worker,_,_=open_form(loaded_extension,backend)
    email,password='synthetic@example.test','Synthetic-Only!7'
    backend[0].settings.credentials.save(dict(email=email,password=password))
    html=FIXTURE.with_name('t082.html').read_text(encoding='utf-8')
    # A supported signup can have one password and an exact native primary.
    # Keeping that topology lets this test isolate mode from control replacement.
    html=html.replace('<label id="confirmLabel">Verify New Password*<input id="secondaryPassword" data-automation-id="verifyPassword" type="password" required></label>','')
    page.context.route('https://fixture.myworkdayjobs.com/**',lambda route:route.fulfill(body=html,content_type='text/html') if route.request.method=='GET' else route.abort())
    page.goto('https://fixture.myworkdayjobs.com/login')
    tab=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    def describe():
        return worker.evaluate('''async id=>{
          const [f]=await chrome.scripting.executeScript({target:{tabId:id},func:()=>autoApplyCredentials.describe()});return f.result;
        }''',tab)
    assert describe()['mode']=='signup' and describe()['supported']
    worker.evaluate('()=>chrome.storage.local.set({autoAccountFill:true})')
    page.locator('#authViewTitle').evaluate("e=>e.textContent='Create account'")
    expect(page.locator('#email')).to_have_value(email,timeout=10000)
    expect(page.locator('#consent')).to_be_checked()
    signup=describe()
    # Consent is outside the native form, inside its exact Workday surface.
    assert page.locator('#consent').evaluate('e=>e.form===null')
    page.locator('label[for=consent]').click()
    assert describe()['autoEligible'] is False
    page.locator('#authViewTitle').evaluate("e=>e.textContent+=' '")
    page.wait_for_timeout(800)
    expect(page.locator('#consent')).not_to_be_checked()
    # Trusted edits also protect these same credential nodes in signup mode.
    page.locator('#email').fill(email)
    page.evaluate('''()=>{
      globalThis.originalEmail=email;globalThis.originalPassword=password;
      authViewTitle.textContent='Sign in';primary.textContent='Sign in';
      primary.setAttribute('data-automation-id','signInSubmitButton');password.value='';
    }''')
    login=describe()
    assert login['mode']=='login' and login['autoEligible'] is True
    assert login['identity']==signup['identity']
    expect(page.locator('#password')).to_have_value(password,timeout=10000)
    assert page.evaluate('email===originalEmail && password===originalPassword')
    assert page.evaluate('[accountClicks,applicationClicks]')==[0,0]
    for value in (signup,login,describe()):
        assert email not in json.dumps(value) and password not in json.dumps(value)



@pytest.mark.parametrize('code,expected', [('needs_fact','needs_user_fact'), ('provider_unavailable','answer_check_unavailable')])
def test_analysis_preserves_answer_reason_without_engineering(dashboard, code, expected):
    from autoapply.extension_api import _page_analysis
    from test_extension_api import analysis_payload
    payload=analysis_payload()
    row=payload['diagnostic']['fields'][0]
    row.update(label='Military spouse', reasons=[], repair_status='unresolved', reason_code=code,
               control=dict(tag='input',type='text',visible=True,rect=[0,0,200,30]), ancestors=[])
    result=_page_analysis(dashboard,payload,'fixture.test')
    assert result['findings'][0]['status'] == expected
    assert result['fields'][0]['reason_code'] == code


@pytest.mark.parametrize('code,visible', [('needs_fact',True),('optional_skip',False)])
def test_analysis_optional_reason_takes_precedence_over_legacy_skip_status(dashboard,code,visible):
    from autoapply.extension_api import _page_analysis
    from test_extension_api import analysis_payload
    payload=analysis_payload()
    row=payload['diagnostic']['fields'][0]
    row.update(label='Are you currently a member of the National Guard or Reserves?',required=False,
               reasons=[],repair_status='optional_skip',reason_code=code,
               control=dict(tag='input',type='text',visible=True,rect=[0,0,200,30]),ancestors=[])
    result=_page_analysis(dashboard,payload,'fixture.test')
    assert bool(result['findings']) is visible
    if visible:
        assert result['findings'][0]['status']=='needs_user_fact'


def test_profile_change_during_grounded_batch_cancels_proposal(dashboard, config, repair_profile, monkeypatch):
    def writer(request):
        updated=dict(repair_profile, skills=['Rust'])
        save_profile(config, updated)
        return {'answers':[proposal()]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    row=resolve_answers(dashboard, dict(url='https://fixture.test/job', fields=[field()]))['answers'][0]
    assert row['value'] is None and row['reason_code'] == 'stale_data'
