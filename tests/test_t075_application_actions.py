"""T075 synthetic fixtures only: no providers, employer writes or applicant data."""
import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading

import pytest
import yaml
from playwright.sync_api import expect

from autoapply import cv
from autoapply.dashboard import DashboardServer
from autoapply.extension_api import chat_field_context, chat_replacement, chat_replacement_intent
from test_dashboard import dashboard, http_dashboard, offline_descriptions, writing_provider
from test_extension import loaded_extension
from test_extension_api import ext

REQUEST = ("for the cover letter include my C-UASC UMD drone club experience, where we are currently working on computer vision "
           "using OpenCV and YOLO to detect flags and numbers on flags. We made OpenCV recognize the shape of the flag, "
           "and YOLO is trained on image samples.")
DRAFT = "Dear Hiring Team,\n\nIn C-UASC at UMD, we are working on computer vision using OpenCV and YOLO to detect flags and numbers on flags. We made OpenCV recognize the shape of the flag, and YOLO is trained on image samples.\n\nSincerely,\nTest Student"
URL = 'https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern_R123/apply'
FIXTURE = Path(__file__).parent / 'fixtures/extension/t075.html'


def test_explicit_intent_and_negative_guards():
    for text in [REQUEST, 'Generate my cover letter', 'Revise the cover letter to include robotics',
                 'Make this answer shorter', 'My answer should say more about robotics']:
        assert chat_replacement_intent(text), text
    for text in ['Draft only: write a cover letter', 'Write a cover letter, draft only',
                 'How do I update the cover letter?', 'Do not update the cover letter',
                 'Can you explain how to change this answer?', 'Write a cover letter without changing the field']:
        assert not chat_replacement_intent(text), text
    field = dict(key='k', label='Cover letter', kind='textarea', identity='synthetic', max_length=None)
    context = chat_field_context(dict(fields=[field], current_key='k'))
    calls=[]
    reply=chat_replacement([dict(role='user',text=REQUEST)],context,
        writer=lambda *args:calls.append(args) or DRAFT)
    assert reply['actions']==[dict(type='replace_text',key='k',text=DRAFT)] and len(calls)==1
    ambiguous=dict(context,fields=[field,dict(field,key='other')])
    assert 'actions' not in chat_replacement([dict(role='user',text=REQUEST)],ambiguous,writer=lambda *args:DRAFT)
    for label in ['Resume', 'Resume and cover letter', 'Attachment']:
        with pytest.raises(ValueError):
            chat_field_context(dict(fields=[dict(field,kind='file',purpose='cover_letter',label=label)]))


def test_canonical_website_and_government_backend(http_dashboard, config):
    profile=config.profile
    profile['links']=dict(personal_website='https://personal.example.test',portfolio='https://stale.example.test',
                          github='https://github.com/synthetic',linkedin='https://www.linkedin.com/in/synthetic')
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    fields=[dict(key=label,label=label,kind='text') for label in ['Website','GitHub','LinkedIn']]
    fields += [dict(key='gov',label='Prior Government Employment?',kind='radio',required=True,options=['Yes','No'])]
    status,result,_=ext(http_dashboard,'POST','/ext/answers',dict(url=URL,fields=fields))
    assert status==200
    assert [a['value'] for a in result['answers']]==[profile['links']['personal_website'],profile['links']['github'],profile['links']['linkedin'],'No']
    assert result['answers'][0]['source']=='profile:links.personal_website'
    before=(config.private/'profile.yaml').read_bytes()
    assert ext(http_dashboard,'POST','/ext/remember',dict(url=URL,label='Website',kind='text',value=profile['links']['github']))[0]==200
    _,again,_=ext(http_dashboard,'POST','/ext/answers',dict(url=URL,fields=fields))
    assert again['answers'][0]['value']==profile['links']['personal_website']
    assert (config.private/'profile.yaml').read_bytes()==before
    for label in ['Do you promise to avoid government employment?', 'Prior government employment or conflicts?',
                  'Do government employment restrictions apply?']:
        status,value,_=ext(http_dashboard,'POST','/ext/answers',dict(url=URL,fields=[dict(key='g',label=label,kind='radio',options=['Yes','No'],required=True)]))
        assert value['answers'][0]['status']!='RESOLVED'


def isolated(worker,tab,operation,arg=None):
    return worker.evaluate("""async ({tab,operation,arg})=>{
      const [frame]=await chrome.scripting.executeScript({target:{tabId:tab},world:'ISOLATED',func:async(op,arg)=>{
        const api=globalThis.autoApplyContent;
        if(op==='scan')return api.scan();
        if(op==='privacy')return {auth:autoApplyCredentials.describe(),incomplete:autoApplyCredentials.incomplete(),scan:await api.scan(),chat:await api.chatContext({request:'Cover letter'}),snapshot:api.pageStructure(),inspect:api.inspectPage()};
        if(op==='fill')return api.fill(arg);
        if(op==='context')return api.chatContext({request:arg||'Cover letter'});
        if(op==='replace'){try{return await api.chatReplace(arg.context,arg.action);}catch(error){return {error:error.message};}}
        if(op==='close'){api.sidebar('closed');return true;}
      },args:[operation,arg]});return frame.result;
    }""",dict(tab=tab,operation=operation,arg=arg))


@pytest.fixture
def application(loaded_extension,dashboard,config,db,listing,writing_provider,monkeypatch,tmp_path):
    # Every profile/credential used here belongs to pytest's temporary Config.
    profile=config.profile;profile['links']=dict(personal_website='https://personal.example.test',github='https://github.com/synthetic',linkedin='https://www.linkedin.com/in/synthetic')
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile),encoding='utf-8')
    db.ingest(replace(listing,url=URL.removesuffix('/apply')),config)
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    server=DashboardServer(dashboard,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    context,worker,extension_id=loaded_extension
    base=f'http://127.0.0.1:{server.server_port}'
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=base,key=dashboard.settings.extension_key()))
    page=context.new_page();writes=[]
    def serve(route):
        if route.request.method!='GET':
            writes.append(route.request.method);route.abort();return
        route.fulfill(path=str(FIXTURE),content_type='text/html')
    page.route('https://fixture.myworkdayjobs.com/**',serve)
    page.goto(URL)
    tab=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',URL)
    page.wait_for_function('document.querySelectorAll("presentation-icon").length===160')
    try:
        yield page,worker,tab,context,extension_id,dashboard,writing_provider
        assert not writes and page.evaluate('submissions')==0
    finally:
        page.close();server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.browser
def test_mv3_decorations_links_and_real_proxy_model(application):
    page,worker,tab,*_=application
    page.locator('#cover').evaluate('el=>el.remove()')
    private=isolated(worker,tab,'privacy')
    assert not private['auth']['active'] and not private['incomplete']
    assert {'First name','Website','Prior Government Employment?'} <= {f['label'] for f in private['scan']['fields']}
    field=next(f for f in private['scan']['fields'] if f['label']=='Prior Government Employment?')
    assert field['kind']=='radio' and field['options']==['Yes','No'] and not field['answered']
    assert sum(f['label']=='Prior Government Employment?' for f in private['scan']['fields'])==1
    assert page.locator('.ashby-application-form-input-yesno button').evaluate_all(
        "buttons=>buttons.every(b=>!b.hasAttribute('type') && !b.hasAttribute('role') && b.type==='submit' && b.form===null)")
    # Exercise real background -> HTTP handler -> resolver -> content fill.
    page.locator('#first').fill('Manual name')
    result=worker.evaluate('id=>autoApplyAutofill(id)',tab)
    assert not result.get('error') and not result.get('sign_in'),result
    for key,value in [('first','Manual name'),('contact','student@example.test'),('question_28381882003','https://personal.example.test'),('github','https://github.com/synthetic'),('linkedin','https://www.linkedin.com/in/synthetic')]:
        expect(page.locator('#'+key)).to_have_value(value)
        assert page.evaluate('key=>model[key]',key)==value or key=='first'
    assert page.evaluate('[model.government,model.governmentSubmitted]')==['No',False]
    expect(page.get_by_role('button',name='No',exact=True)).to_have_attribute('aria-pressed','true')
    expect(page.get_by_role('button',name='Yes',exact=True)).to_have_attribute('aria-pressed','false')
    assert not page.locator('#question_28381884003').is_checked()
    assert any(f['label']=='Prior Government Employment?' and f['value']=='No' for f in result['completed'])


@pytest.mark.browser
@pytest.mark.parametrize('association,button_type', [
    ('ancestor',None), ('external',None), ('ancestor','submit'),
    ('external','submit'), ('ancestor','invalid'), ('external','image'),
])
def test_mv3_proxy_no_submit_guard(application,association,button_type):
    page,worker,tab,*_=application
    # Save a valid inventory, then change the runtime form association before fill.
    scan=isolated(worker,tab,'scan')
    field=next(f for f in scan['fields'] if f['label']=='Prior Government Employment?')
    page.evaluate("""({association,button_type})=>{
      const wrapper=document.querySelector('.ashby-application-form-input-yesno');
      window.proxyClicks=0;wrapper.addEventListener('click',()=>proxyClicks++);
      const form=document.createElement('form');form.id='actual-form';application.append(form);
      if(association==='ancestor')form.append(wrapper);
      for(const button of wrapper.querySelectorAll('button')){
        if(association==='external')button.setAttribute('form',form.id);
        if(button_type!==null)button.setAttribute('type',button_type);
      }
    }""",dict(association=association,button_type=button_type))
    assert page.locator('.ashby-application-form-input-yesno button').evaluate_all(
        "buttons=>buttons.every(b=>b.form instanceof HTMLFormElement && b.type==='submit')")
    result=isolated(worker,tab,'fill',[dict(key=field['key'],status='RESOLVED',value='No')])
    assert not result['completed'] and result['filled']==0
    assert page.evaluate('[proxyClicks,submissions,model.government??null]')==[0,0,None]
    assert not page.locator('#question_28381884003').is_checked()
    # Fresh inventory must also abstain from offering this as a supported proxy.
    assert not any(f['label']=='Prior Government Employment?' for f in isolated(worker,tab,'scan')['fields'])


@pytest.mark.browser
def test_mv3_proxy_explicit_button_inside_form(application):
    page,worker,tab,*_=application
    page.evaluate("""()=>{
      const wrapper=document.querySelector('.ashby-application-form-input-yesno');
      const form=document.createElement('form');application.append(form);form.append(wrapper);
      for(const button of wrapper.querySelectorAll('button'))button.type='button';
    }""")
    field=next(f for f in isolated(worker,tab,'scan')['fields'] if f['label']=='Prior Government Employment?')
    result=isolated(worker,tab,'fill',[dict(key=field['key'],status='RESOLVED',value='No')])
    assert result['filled']==1 and result['completed'][0]['value']=='No'
    assert page.evaluate('[model.government,model.governmentSubmitted,submissions]')==['No',False,0]


@pytest.mark.browser
@pytest.mark.parametrize('behavior', ['inert','native-mismatch','native-only','reverted','late-form','class-only'])
def test_mv3_proxy_requires_settled_positive_no_evidence(application,behavior):
    page,worker,tab,*_=application
    field=next(f for f in isolated(worker,tab,'scan')['fields'] if f['label']=='Prior Government Employment?')
    page.evaluate("""behavior=>{
      const wrapper=document.querySelector('.ashby-application-form-input-yesno');
      const buttons=wrapper.querySelectorAll('button');
      for(const button of buttons){
        const update=button.onclick;
        if(behavior==='class-only')button.removeAttribute('aria-pressed');
        button.onclick=()=>{
          if(behavior==='native-mismatch'){button.setAttribute('aria-pressed','true');wrapper.querySelector('input').checked=true;}
          if(behavior==='native-only')wrapper.querySelector('input').checked=false;
          if(behavior==='reverted' || behavior==='late-form'){
            update();setTimeout(()=>{
              if(behavior==='reverted')button.setAttribute('aria-pressed','false');
              else{const form=document.createElement('form');form.id='late-form';application.append(form);button.setAttribute('form',form.id);}
            },40);
          }
        };
      }
    }""",behavior)
    result=isolated(worker,tab,'fill',[dict(key=field['key'],status='RESOLVED',value='No')])
    assert result['filled']==0 and not result['completed']
    assert page.locator('#question_28381884003').is_checked()==(behavior=='native-mismatch')
    assert page.evaluate('submissions')==0


@pytest.mark.browser
def test_mv3_deep_auth_and_incomplete_privacy(application):
    page,worker,tab,_,_,dashboard,_=application
    page.evaluate("""()=>{
      window.authSubmissions=0;
      const host=document.createElement('auth-host');document.body.append(host);
      let root=host.attachShadow({mode:'open'});
      for(let i=0;i<12;i++){const node=document.createElement('auth-layer');root.append(node);root=node.attachShadow({mode:'open'});}
      root.innerHTML='<form><h2>Sign in</h2><label>Login email<input id=auth-email type=email></label><label>Password<input id=auth-password type=password></label><button type=submit>Sign in</button></form>';
      // Account activation is separate from the fixture's application-submit guard.
      root.querySelector('form').onsubmit=event=>{event.preventDefault();authSubmissions++;};
    }""")
    privacy=isolated(worker,tab,'privacy')
    assert privacy['auth']['active'] and privacy['auth']['supported']
    page.locator('#auth-password').evaluate('el=>el.readOnly=true')
    readonly=isolated(worker,tab,'privacy')
    assert readonly['auth']['active'] and not readonly['auth']['supported'] and readonly['scan']['fields']==[]
    page.locator('#auth-password').evaluate('el=>el.readOnly=false')
    absent=worker.evaluate('id=>autoApplyAutofill(id)',tab)
    assert 'Settings' in absent['progress'] and page.locator('#auth-password').input_value()==''
    assert page.evaluate('[authSubmissions,submissions]')==[0,0]
    dashboard.settings.credentials.save(dict(email='synthetic-auth@example.test',password='synthetic-password'))
    result=worker.evaluate('id=>autoApplyAutofill(id)',tab)
    assert 'Account action activated. Waiting for authentication' in result['progress'],result
    assert page.evaluate('[authSubmissions,submissions]')==[1,0]
    expect(page.locator('#auth-email')).to_have_value('synthetic-auth@example.test')
    expect(page.locator('#auth-password')).to_have_value('synthetic-password')
    assert page.evaluate('model["auth-password"]')=='synthetic-password'
    assert 'synthetic-password' not in json.dumps(isolated(worker,tab,'privacy'))
    repeated=worker.evaluate('id=>autoApplyAutofill(id)',tab)
    assert 'Account action already attempted' in repeated['progress'],repeated
    assert page.evaluate('[authSubmissions,submissions]')==[1,0]
    assert 'synthetic-password' not in json.dumps(isolated(worker,tab,'privacy'))
    page.reload()
    page.locator('#application').evaluate("el=>el.insertAdjacentHTML('beforeend','<label>Create account optional<input id=createaccountoptional type=password></label>')")
    optional=isolated(worker,tab,'privacy')
    assert not optional['auth']['active'] and any(f['label']=='Email' for f in optional['scan']['fields'])
    page.reload()
    page.evaluate("""()=>{for(let i=0;i<140;i++){const host=document.createElement('unknown-root');icons.append(host);host.attachShadow({mode:'open'}).innerHTML='<span>Unclassified</span>';}
      const host=document.createElement('unknown-auth');icons.append(host);host.attachShadow({mode:'open'}).innerHTML='<form><h2>Sign in</h2><input type=email value=unvisited-private@example.test><input type=password value=unvisited-secret></form>';
    }""")
    privacy=isolated(worker,tab,'privacy')
    assert privacy['incomplete'] and not privacy['auth']['active'] and not privacy['scan']['sign_in']
    assert privacy['scan']['fields']==privacy['chat']['fields']==privacy['snapshot']['nodes']==[]
    assert 'unvisited-private' not in json.dumps(privacy) and 'unvisited-secret' not in json.dumps(privacy)


@pytest.mark.browser
def test_mv3_chat_exact_pdf_upload_and_stale_guards(application,monkeypatch):
    page,worker,tab,context,extension_id,_,provider=application
    page.locator('#letter').evaluate('el=>el.parentElement.remove()')
    provider.result=dict(answer=DRAFT)
    rendered=[]
    renderer=cv.render_pdf
    def record_render(html,path):
        renderer(html,path)  # Actual local Chromium, never a fake PDF renderer.
        rendered.append((html,path.read_bytes()))
    monkeypatch.setattr(cv,'render_pdf',record_render)
    panel=context.new_page();panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab}')
    panel.locator('#tab-chat').click()
    panel.locator('#chat-input').fill(REQUEST);panel.locator('#send').click()
    expect(panel.locator('#chat-status')).to_have_text('',timeout=30000)
    assert page.locator('#cover').evaluate('e=>e.files[0]?.name')=='Cover Letter.pdf'
    assert page.evaluate('model.cover')==['Cover Letter.pdf']
    assert page.locator('#resume').evaluate('e=>e.files.length')==0
    assert len(provider.requests)==1 and REQUEST in provider.requests[0]['instruction']
    encoded=page.locator('#cover').evaluate("""async el=>{const bytes=new Uint8Array(await el.files[0].arrayBuffer());let value='';for(const b of bytes)value+=String.fromCharCode(b);return btoa(value);}""")
    from bs4 import BeautifulSoup
    assert BeautifulSoup(rendered[0][0],'html.parser').body.get_text()==DRAFT
    assert base64.b64decode(encoded)==rendered[0][1] and rendered[0][1].startswith(b'%PDF-')
    expect(panel.locator('#messages')).to_contain_text('Replaced Cover Letter attachment')
    # A reference must render the same draft, without another provider request.
    panel.locator('#chat-input').fill('replace Cover Letter attachment with that');panel.locator('#send').click()
    expect(panel.locator('#chat-status')).to_have_text('',timeout=30000)
    assert len(provider.requests)==1
    captured=isolated(worker,tab,'context','replace Cover Letter attachment with that')
    action=dict(type='replace_cover_letter',key=captured['current_key'],text=DRAFT,file=dict(filename='Cover Letter.pdf',mime='application/pdf',base64=encoded))
    page.locator('#cover').set_input_files(dict(name='User letter.pdf',mimeType='application/pdf',buffer=b'%PDF-1.4 synthetic user edit'))
    outcome=isolated(worker,tab,'replace',dict(context=captured,action=action))
    assert 'error' in outcome and page.locator('#cover').evaluate('e=>e.files[0].name')=='User letter.pdf'
    captured=isolated(worker,tab,'context','Cover Letter attachment')
    isolated(worker,tab,'close')
    assert 'error' in isolated(worker,tab,'replace',dict(context=captured,action=action))
    panel.close()


@pytest.mark.browser
def test_mv3_chat_text_negative_and_inflight_user_edit(application,monkeypatch):
    import asyncio
    page,worker,tab,context,extension_id,_,provider=application
    page.locator('#cover').evaluate('el=>el.parentElement.remove()')
    provider.result=dict(answer=DRAFT)
    panel=context.new_page();panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab}')
    panel.locator('#tab-chat').click()
    panel.locator('#chat-input').fill(REQUEST);panel.locator('#send').click()
    expect(page.locator('#letter')).to_have_value(DRAFT,timeout=15000)
    assert page.evaluate('model.letter')==DRAFT
    expect(panel.locator('#chat-status')).to_have_text('')
    provider.result=dict(reply='Draft only, unchanged live field')
    for instruction in ['Write a cover letter, draft only', 'Do not replace the cover letter', 'How do I replace the cover letter?']:
        panel.locator('#chat-input').fill(instruction);panel.locator('#send').click()
        expect(panel.locator('#send')).to_be_enabled(timeout=10000)
        expect(page.locator('#letter')).to_have_value(DRAFT)
    started,released=threading.Event(),threading.Event()
    original=provider.generate_response
    async def blocked(payload):
        started.set()
        await asyncio.to_thread(released.wait,10)
        return await original(payload)
    monkeypatch.setattr(provider,'generate_response',blocked)
    provider.result=dict(answer='Revised supported draft')
    panel.locator('#chat-input').fill('Revise the cover letter to include my club experience');panel.locator('#send').click()
    for attempt in range(50):
        panel.wait_for_timeout(100)
        if started.is_set():break
    assert started.is_set(), panel.locator('#chat-status').inner_text()
    page.locator('#letter').fill('User changed this while waiting')
    released.set()
    expect(panel.locator('#chat-status')).to_contain_text('changed',timeout=15000)
    expect(page.locator('#letter')).to_have_value('User changed this while waiting')
    saved=worker.evaluate('async()=> (await chrome.storage.session.get("chatHistories")).chatHistories')
    assert any(item.get('lastDraft')=='Revised supported draft' for item in saved.values())
    panel.close()


def test_pdf_failure_keeps_generated_draft(http_dashboard,writing_provider,monkeypatch):
    writing_provider.result=dict(answer=DRAFT)
    def failed(*args):raise RuntimeError('synthetic rendering failure')
    monkeypatch.setattr(cv,'cover_letter_draft_artifact',failed)
    field=dict(key='k',label='Cover letter',kind='file',purpose='cover_letter',identity='fixture',max_length=None)
    status,result,_=ext(http_dashboard,'POST','/ext/chat',dict(url=URL,field_context=dict(fields=[field],current_key='k'),messages=[dict(role='user',text=REQUEST)]))
    assert status==200 and result['kind']=='draft' and result['reply']==DRAFT and 'actions' not in result
    assert 'unchanged' in result['warning'] and len(writing_provider.requests)==1
