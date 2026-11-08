"""Actual unpacked MV3, synthetic shapes only, loopback backend and no providers."""
import json
from pathlib import Path
import threading

import pytest
import yaml
from playwright.sync_api import expect

from autoapply.dashboard import DashboardServer
from autoapply.repair_queue import RepairQueue, evidence, task_template
from test_dashboard import dashboard, offline_descriptions
from test_extension import loaded_extension

FIXTURE = Path(__file__).parent / 'fixtures/extension/shadow/observed.html'


def isolated(worker, tab, expression, arg=None):
    result=worker.evaluate('''async ({tab,expression,arg})=>{
      const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',
        func:async (expression,arg)=>{try{
          const api=globalThis.autoApplyContent;
          if(expression==='typeof autoApplyContent')return typeof api;
          if(expression==='await autoApplyContent.scan()')return await api.scan();
          if(expression==='autoApplyContent.analysisContext()')return api.analysisContext();
          if(expression==='await autoApplyContent.fill(arg)')return await api.fill(arg);
          if(expression==='capture')return {...api.pageStructure(),diagnostic:{...api.inspectPage().diagnostic,inspections:[api.pageStructure().inspection]}};
          if(expression==='privacy')return {description:autoApplyCredentials.describe(),scan:await api.scan(),chat:await api.chatContext({request:'Email'}),snapshot:api.pageStructure(),inspect:api.inspectPage()};
          throw Error('Unknown fixture operation');
        }catch(error){return {fixtureError:error.stack};}},args:[expression,arg]});
      return frame.result;
    }''', dict(tab=tab, expression=expression, arg=arg))
    assert not isinstance(result,dict) or 'fixtureError' not in result,result
    return result


@pytest.fixture
def shadow_page(loaded_extension, dashboard):
    # Only synthetic test-private profile paths, never the user's applicant data.
    profile=yaml.safe_load((dashboard.config.private/'profile.yaml').read_text())
    profile['links']={'linkedin':'https://www.linkedin.com/in/test-student', 'personal_website':'https://student.example.test'}
    (dashboard.config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    dashboard.repairs.configure(dict(enabled=True,paused=True))
    server=DashboardServer(dashboard,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    context,worker,_=loaded_extension
    base=f'http://127.0.0.1:{server.server_port}'
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=base,key=dashboard.settings.extension_key()))
    page=context.new_page(); errors=[]; requests=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:requests.append((request.method,request.url)))
    page.route('**/observed.html',lambda route:route.fulfill(path=str(FIXTURE),content_type='text/html'))
    page.goto(base+'/observed.html')
    tab=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    isolated(worker,tab,"typeof autoApplyContent")
    try:
        yield page,worker,tab,dashboard,errors
        assert errors==[]
        assert page.evaluate('submissions')==0
        assert all(url.startswith(base) or url.startswith('chrome-extension://') for _,url in requests)
    finally:
        page.close();server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.browser
def test_observed_native_shadow_fill_backend_and_composed_model(shadow_page):
    page,worker,tab,dashboard,_=shadow_page
    # Trusted user edits must survive the real background -> backend -> fill path.
    page.locator('#first-name-input').fill('Manual first')
    isolated(worker,tab,'await autoApplyContent.scan()')
    result=worker.evaluate('tab=>autoApplyAutofill(tab)',tab)
    assert not result.get('error'),result
    expected={'first-name-input':'Manual first','last-name-input':'Student','email-input':'student@example.test',
              'confirm-email-input':'student@example.test','linkedin-input':'https://www.linkedin.com/in/test-student',
              'website-input':'https://student.example.test'}
    for identity,value in expected.items():
        expect(page.locator('#'+identity)).to_have_value(value)
        assert page.evaluate('id=>model[id]',identity)==value
    for identity in ('facebook-input','twitter-input','hiring-manager-message-input'):
        expect(page.locator('#'+identity)).to_have_value('')
    for identity in ('spl-dropzone-file-input-1','spl-dropzone-file-input-2'):
        assert page.locator('#'+identity).evaluate('e=>e.files.length')==0
    expect(page.locator('#late')).to_have_value('Test')
    for label,source in [('Last name','profile:identity.last_name'),('LinkedIn','profile:links.linkedin'),('Website','profile:links.personal_website')]:
        completed=[f for f in result['completed'] if f['label']==label]
        assert len(completed)==1 and completed[0]['source']==source
    assert len([f for f in result['pending'] if f['kind']=='file'])==2
    assert {'Facebook','X (fka Twitter)','Let the company know about your interest working there'} <= {f['label'] for f in result['pending']}
    scan=isolated(worker,tab,'await autoApplyContent.scan()')
    assert len([f for f in scan['fields'] if f['label']=='First name'])==2


@pytest.mark.browser
def test_root_evidence_api_task_privacy_and_stale_replacement(shadow_page):
    page,worker,tab,dashboard,_=shadow_page
    page.locator('#late-host').evaluate("host=>{const outer=document.createElement('spl-input');host.shadowRoot.append(outer);outer.attachShadow({mode:'open'}).innerHTML='<div role=\"switch\" id=\"duplicate\" aria-labelledby=\"label\">Toggle</div><label id=\"label\">Unknown control</label>';}")
    # Capture before filling: actual rooted shapes -> authenticated API -> queue.
    captured=isolated(worker,tab,'capture')
    captured['context_url']=page.url
    response=worker.evaluate('''async payload=>{const {server,key}=await chrome.storage.local.get(['server','key']);const response=await fetch(server+'/ext/snapshot',{method:'POST',headers:{'Content-Type':'application/json','X-AutoApply-Key':key},body:JSON.stringify(payload)});return {status:response.status,body:await response.json()};}''',captured)
    assert response['status']==200,response
    saved=json.loads(next((dashboard.config.private/'snapshots').glob('*.json')).read_text())
    fields=saved['diagnostic']['fields']
    first=next(f for f in fields if f['control'].get('id')=='first-name-input')
    assert first['root_id']==first['control']['root_id']==12
    assert next(i for i in saved['diagnostic']['inspections'][0]['roots'] if i['id']==12)['parent_id']==0
    shape,reason=evidence(saved)
    assert reason is None
    unknown=next(f for f in shape['controls'] if f['control'].get('role')=='switch')
    assert unknown['root']['parent_id'] is not None
    assert unknown['control']['aria-labelledby']!=[]
    template=json.dumps(task_template('SyntheticTask',shape))
    assert 'spl-input' in template and 'root_trees' in template
    assert 'first-name-input' not in template and 'Unknown control' not in template
    assert dashboard.repairs.status()['jobs'][0]['status']=='queued'
    original=isolated(worker,tab,'await autoApplyContent.scan()')
    key=next(f['key'] for f in original['fields'] if f['label']=='Last name')
    identity=original['page_identity']
    page.locator('spl-input[data-observed-root="13"]').evaluate("host=>host.shadowRoot.innerHTML='<label for=last-name-input>Different question</label><input id=last-name-input>'")
    assert isolated(worker,tab,'autoApplyContent.analysisContext()')!=identity
    isolated(worker,tab,'await autoApplyContent.fill(arg)',[dict(key=key,status='RESOLVED',value='Wrong')])
    expect(page.locator('#last-name-input')).to_have_value('')
    assert isolated(worker,tab,'await autoApplyContent.scan()')['page_identity']!=identity


@pytest.mark.browser
@pytest.mark.parametrize('hidden_by', [
    'light_hidden', 'shadow_hidden', 'host_hidden', 'host_inert',
    'host_aria_hidden', 'host_display', 'host_visibility', 'host_opacity',
])
def test_hidden_password_does_not_block_application(shadow_page, hidden_by):
    page,worker,tab,_,_=shadow_page
    page.evaluate('''hiddenBy=>{
      const host=document.createElement('div');application.append(host);host.id='hidden-auth';
      const root=hiddenBy==='light_hidden'?host:host.attachShadow({mode:'open'});
      root.innerHTML='<input type=password value=hidden-synthetic-secret>';
      if(hiddenBy.endsWith('_hidden') && !hiddenBy.startsWith('host_'))root.querySelector('input').hidden=true;
      else if(hiddenBy==='host_hidden')host.hidden=true;
      else if(hiddenBy==='host_inert')host.inert=true;
      else if(hiddenBy==='host_aria_hidden')host.setAttribute('aria-hidden','true');
      else if(hiddenBy==='host_display')host.style.display='none';
      else if(hiddenBy==='host_visibility')host.style.visibility='hidden';
      else if(hiddenBy==='host_opacity')host.style.opacity='0';
      application.insertAdjacentHTML('beforeend','<label>First name<input id=normal-name></label><label>Email<input id=contact-email type=email></label>');
    }''', hidden_by)
    private=isolated(worker,tab,'privacy')
    assert not private['description']['active'] and not private['description']['supported']
    assert {'First name','Email'} <= {f['label'] for f in private['scan']['fields']}
    assert 'hidden-synthetic-secret' not in json.dumps(private)
    result=worker.evaluate('tab=>autoApplyAutofill(tab)',tab)
    assert not result.get('error') and not result.get('sign_in'),result
    expect(page.locator('#normal-name')).to_have_value('Test')
    expect(page.locator('#contact-email')).to_have_value('student@example.test')
    expect(page.locator('#email-input')).to_have_value('student@example.test')
    assert page.locator('#hidden-auth input').input_value()=='hidden-synthetic-secret'


@pytest.mark.browser
@pytest.mark.parametrize('readonly', [False, True])
def test_shadow_auth_is_manual_and_private(shadow_page, readonly):
    page,worker,tab,dashboard,_=shadow_page
    page.evaluate("""()=>{const host=document.createElement('spl-input');document.body.append(host);host.id='auth-host';host.attachShadow({mode:'open'}).innerHTML='<form><h2>Sign in</h2><label>Login email<input type=email value=private-user@example.test></label><label>Password<input type=password value=shadow-secret-123></label></form>';}""")
    page.locator('#auth-host input[type=password]').evaluate('(e,readonly)=>e.readOnly=readonly', readonly)
    private=isolated(worker,tab,'privacy')
    assert private['description']['active'] and not private['description']['supported']
    assert private['scan']['fields']==private['chat']['fields']==private['snapshot']['nodes']==private['inspect']['diagnostic']['fields']==[]
    encoded=json.dumps(private)
    assert 'shadow-secret-123' not in encoded and 'private-user@example.test' not in encoded
    result=worker.evaluate('tab=>autoApplyAutofill(tab)',tab)
    assert result.get('sign_in') and not result.get('error'),result
    expect(page.locator('#auth-host input[type=password]')).to_have_value('shadow-secret-123')
    page.locator('#auth-host input[type=password]').evaluate('e=>e.remove()')
    # Remove auth-looking hints too: privacy must survive through remembered identity.
    page.locator('#auth-host input[type=email]').evaluate('e=>e.parentElement.firstChild.textContent="Contact email"')
    after=isolated(worker,tab,'capture')
    encoded=json.dumps(after)
    assert 'shadow-secret-123' not in encoded and 'private-user@example.test' not in encoded
    assert not any(f['label']=='Contact email' for f in after['diagnostic']['fields'])
    assert any(f['label']=='First name' for f in after['diagnostic']['fields'])


@pytest.mark.browser
def test_scoped_duplicate_ids_groups_visibility_and_reacquire(shadow_page):
    page,worker,tab,_,_=shadow_page
    page.evaluate('''()=>{
      for(const [index,question] of ['First name','Last name'].entries()){
        const host=document.createElement('spl-input');host.id='duplicate-host-'+index;application.append(host);
        host.attachShadow({mode:'open'}).innerHTML=`<label id="prompt" for="same">${question}</label><input id="same" aria-labelledby="prompt">
          <fieldset><legend>Independent ${index}</legend><label>Yes<input type=radio name=choice value=yes></label><label>No<input type=radio name=choice value=no></label></fieldset>
          <fieldset><legend>Check ${index}</legend><label>Alpha<input type=checkbox name=check></label><label>Beta<input type=checkbox name=check></label></fieldset>
          <label>Select ${index}<select><option value=""></option><option value="${index}">Option ${index}</option></select></label>
          <label>Disabled<input disabled></label><div hidden><label>Hidden<input></label></div>`;
      }
    }''')
    scan=isolated(worker,tab,'await autoApplyContent.scan()')
    radios=[f for f in scan['fields'] if f['kind']=='radio']
    checks=[f for f in scan['fields'] if f['kind']=='checkbox']
    assert len(radios)==len(checks)==2
    assert all(f['options']==['Yes','No'] for f in radios)
    assert all(f['options']==['Alpha','Beta'] for f in checks)
    assert not any(f['label'] in {'Disabled','Hidden'} for f in scan['fields'])
    controls=isolated(worker,tab,'capture')['diagnostic']['fields']
    duplicates=[f for f in controls if f['control'].get('id')=='same']
    assert {f['label'] for f in duplicates}=={'First name','Last name'}
    assert len({f['root_id'] for f in duplicates})==2
    answers=[dict(key=f['key'],status='RESOLVED',value='Yes' if i==0 else 'No',source='synthetic') for i,f in enumerate(radios)]
    answers += [dict(key=f['key'],status='RESOLVED',value=['Alpha'] if i==0 else ['Beta'],source='synthetic') for i,f in enumerate(checks)]
    selects=[f for f in scan['fields'] if f['kind']=='select']
    answers += [dict(key=f['key'],status='RESOLVED',value=f['options'][0],source='synthetic') for f in selects]
    filled=isolated(worker,tab,'await autoApplyContent.fill(arg)',answers)
    assert filled['filled']==6,filled
    for i in range(2):
        host=page.locator('#duplicate-host-'+str(i))
        expect(host.locator('input[type=radio][value='+('yes' if i==0 else 'no')+']')).to_be_checked()
        expect(host.locator('select')).to_have_value(str(i))
    # Reacquire a replaced native input within its original root, never the other duplicate.
    scan=isolated(worker,tab,'await autoApplyContent.scan()')
    last=next(f for f in reversed(scan['fields']) if f['label']=='Last name')
    page.locator('#duplicate-host-1').evaluate("host=>{const old=host.shadowRoot.querySelector('#same');old.replaceWith(old.cloneNode());}")
    filled=isolated(worker,tab,'await autoApplyContent.fill(arg)',[dict(key=last['key'],status='RESOLVED',value='Scoped last',source='synthetic')])
    assert filled['filled']==1
    expect(page.locator('#duplicate-host-1 #same')).to_have_value('Scoped last')
    expect(page.locator('#duplicate-host-0 #same')).to_have_value('')


@pytest.mark.parametrize('mutation', ['root_limit','duplicate','parent_cycle','parent_missing','field_unknown','shape_mismatch'])
def test_root_metadata_validation_at_real_api(shadow_page, mutation):
    page,worker,tab,dashboard,_=shadow_page
    value=isolated(worker,tab,'capture');value['context_url']=page.url
    roots=value['diagnostic']['inspections'][0]['roots']
    if mutation=='root_limit':roots.extend([roots[0]]*128)
    elif mutation=='duplicate':roots[1]['id']=0
    elif mutation=='parent_cycle':roots[1]['parent_id']=1
    elif mutation=='parent_missing':roots[1]['parent_id']=127
    elif mutation=='field_unknown':
        value['diagnostic']['fields'][0]['root_id']=127
        value['diagnostic']['fields'][0]['control']['root_id']=127
    else:value['diagnostic']['fields'][0]['control']['root_id']=0
    response=worker.evaluate('''async payload=>{const {server,key}=await chrome.storage.local.get(['server','key']);const response=await fetch(server+'/ext/snapshot',{method:'POST',headers:{'Content-Type':'application/json','X-AutoApply-Key':key},body:JSON.stringify(payload)});return response.status;}''',value)
    assert response==400
    assert dashboard.repairs.status()['jobs']==[]
