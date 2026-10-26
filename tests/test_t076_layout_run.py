"""T076: actual unpacked MV3, loopback forms and real temporary backend only."""
from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest
from playwright.sync_api import expect
from autoapply.dashboard import DashboardServer, DashboardHandler
from autoapply import extension_api
from test_dashboard import dashboard, offline_descriptions, queue, http_dashboard, request
from test_extension import loaded_extension, extension_server, fixture_delay, KEY

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/extension/t076.html'
ARTIFACTS=ROOT/'.tmp/t076-visuals'


@pytest.fixture
def backend(dashboard, config, db, listing):
    calls=[];gate=threading.Event();gate.set();entered=threading.Event();hold=['']
    class Handler(DashboardHandler):
        def do_GET(self):
            calls.append(('GET',self.path))
            if self.path.startswith('/synthetic/'):
                if hold[0]=='loading':entered.set();gate.wait(10)
                data=FIXTURE.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();
                try:self.wfile.write(data)
                except ConnectionError:pass
            else:super().do_GET()
        def do_POST(self):
            calls.append(('POST',self.path))
            if self.path==hold[0]:entered.set();gate.wait(10)
            if self.path=='/ext/run/cancel' and hold[0]=='cancel-fail':
                self._reply(503,dict(error='Synthetic retry'));return
            super().do_POST()
    server=DashboardServer(dashboard,port=0);server.RequestHandlerClass=Handler
    base=f'http://127.0.0.1:{server.server_port}'
    ids=[]
    for n in range(2):
        job,_=db.ingest(replace(listing,url=base+f'/synthetic/{n}',company='A very long synthetic company name '+str(n)),config)
        ids.append(db.one('SELECT id FROM applications WHERE job_id=?',(job,))['id'])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield base,ids,calls,gate,entered,hold
    finally:gate.set();server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.browser
@pytest.mark.parametrize('phase',['loading','/ext/answers','/ext/run/result','review'])
def test_owned_close_cancels_real_backend_immediately(loaded_extension,backend,dashboard,phase):
    context,worker,extension_id=loaded_extension
    base,ids,calls,gate,entered,hold=backend
    worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:true})')
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=base,key=dashboard.settings.extension_key()))
    view=context.new_page();view.goto(base+'/#priority')
    errors=[];view.on('pageerror',lambda error:errors.append(str(error)))
    extension_api.run_start(dashboard,dict(application_ids=ids))
    if phase!='review':hold[0]=phase;gate.clear()
    worker.evaluate('''()=>{chrome.storage.session.set({chromeRunPaused:false}).then(()=>autoApplyRunTick());return true;}''')
    if phase!='review':
        for _ in range(80):
            if entered.is_set():break
            view.wait_for_timeout(100)
        assert entered.is_set(), (calls,worker.evaluate('async()=>chrome.storage.session.get(null)'))
    worker.evaluate('''async()=>{for(let i=0;i<500;i++){const {chromeRun:r}=await chrome.storage.session.get('chromeRun');if(r?.tabId)return;await new Promise(r=>setTimeout(r,10));}throw Error('No owned tab');}''')
    state=worker.evaluate('async()=>(await chrome.storage.session.get("chromeRun")).chromeRun')
    tab=next((p for p in context.pages if p.url==base+'/synthetic/0'),None)
    if phase=='review':expect(tab.locator('#name')).not_to_have_value('',timeout=15000)
    unrelated=context.new_page();unrelated.close()
    assert any(i['state'] in {'pending','opened'} for i in extension_api.run_status(dashboard,{})['items'])
    start=time.monotonic();worker.evaluate('id=>chrome.tabs.remove(id)',state['tabId'])
    worker.evaluate('''async()=>{for(let i=0;i<200;i++){const {chromeRun:r}=await chrome.storage.session.get('chromeRun');if(r?.active===false&&!r.current&&!r.tabId)return;await new Promise(r=>setTimeout(r,10));}throw Error('Run did not stop');}''')
    assert time.monotonic()-start<2.5
    deadline=time.monotonic()+2
    while any(i['state'] in {'pending','opened'} for i in extension_api.run_status(dashboard,{})['items']) and time.monotonic()<deadline:view.wait_for_timeout(20)
    status=extension_api.run_status(dashboard,{})
    assert all(i['state']=='skipped' for i in status['items'])
    gate.set();hold[0]=''
    worker.evaluate('()=>autoApplyRunTick()');worker.evaluate('()=>autoApplyRunTick()')
    expect(view.locator('#chrome-run-panel')).to_be_hidden(timeout=5000)
    assert not any(p.url==base+'/synthetic/1' for p in context.pages)
    assert not any(path=='/ext/applied' for _,path in calls)
    assert not errors


@pytest.mark.browser
def test_close_retry_survives_actual_worker_restart(loaded_extension,backend,dashboard):
    context,worker,extension_id=loaded_extension;base,ids,calls,gate,entered,hold=backend
    worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:true})')
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=base,key=dashboard.settings.extension_key()))
    extension_api.run_start(dashboard,dict(application_ids=ids))
    worker.evaluate('async()=>{await chrome.storage.session.set({chromeRunPaused:false});await autoApplyRunTick();}')
    state=worker.evaluate('async()=>(await chrome.storage.session.get("chromeRun")).chromeRun')
    panel=context.new_page();panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    hold[0]='cancel-fail'
    worker.evaluate('id=>chrome.tabs.remove(id)',state['tabId'])
    expect(panel.locator('#run-controls')).to_be_hidden(timeout=2500)
    panel.wait_for_timeout(150)
    assert worker.evaluate('async()=>Boolean((await chrome.storage.session.get("pendingRunCancel")).pendingRunCancel)')
    # Terminate only this test profile's MV3 worker through Chromium's CDP.
    cdp=context.new_cdp_session(panel);cdp.send('ServiceWorker.enable');cdp.send('ServiceWorker.stopAllWorkers')
    hold[0]=''
    panel.evaluate('()=>chrome.runtime.sendMessage({type:"run-status-refresh"})')
    fresh=next((w for w in context.service_workers if w.url.endswith('/background.js')),None)
    if fresh is None:fresh=context.wait_for_event('serviceworker',timeout=10000)
    fresh.evaluate('()=>autoApplyRunTick()')
    deadline=time.monotonic()+4
    while any(i['state'] in {'pending','opened'} for i in extension_api.run_status(dashboard,{})['items']) and time.monotonic()<deadline:panel.wait_for_timeout(50)
    assert all(i['state']=='skipped' for i in extension_api.run_status(dashboard,{})['items'])
    fresh.evaluate('()=>autoApplyRunTick()')
    assert not any(p.url==base+'/synthetic/1' for p in context.pages)
    assert not fresh.evaluate('async()=>Boolean((await chrome.storage.session.get("pendingRunCancel")).pendingRunCancel)')


@pytest.mark.browser
def test_docking_preserves_styles_values_and_lifecycle(loaded_extension,backend,dashboard):
    context,worker,extension_id=loaded_extension;base,*_=backend
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=base,key=dashboard.settings.extension_key()))
    page=context.new_page();page.set_viewport_size(dict(width=1440,height=900));page.goto(base+'/synthetic/0')
    tab=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    worker.evaluate('id=>autoApplySidebar(id,"open")',tab)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#layout-overlay')).to_have_attribute('aria-pressed','true')
    page.locator('#name').fill('Manual preserved value')
    before=page.locator('html').get_attribute('style');normal=page.locator('form').bounding_box()
    panel.locator('#layout-docked').click()
    expect(panel.locator('#layout-docked')).to_have_attribute('aria-pressed','true')
    def bounds():
        form=page.locator('form').bounding_box();host=page.locator('#autoapply-sidebar').bounding_box()
        assert form['x']+form['width']<=host['x']+1
    bounds();assert page.locator('html').get_attribute('style')==before
    # Inline application updates, including priorities, survive every transition.
    page.locator('html').evaluate("el=>el.style.setProperty('margin-left','3px','important')")
    changed=page.locator('html').get_attribute('style')
    ARTIFACTS.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(ARTIFACTS/'docked-desktop.png'))
    for state in ['minimized','closed']:
        worker.evaluate('([id,state])=>autoApplySidebar(id,state)',[tab,state])
        assert abs(page.locator('form').bounding_box()['width']-normal['width'])<5
        assert page.locator('html').get_attribute('style')==changed
        worker.evaluate('id=>autoApplySidebar(id,"open")',tab);bounds()
    panel.locator('#layout-overlay').click();expect(panel.locator('#layout-overlay')).to_have_attribute('aria-pressed','true')
    assert page.locator('form').bounding_box()['width']>1000
    panel.locator('#layout-docked').click();bounds()
    page.set_viewport_size(dict(width=700,height=900));expect(panel.locator('#layout-overlay')).to_have_attribute('aria-pressed','true')
    assert page.locator('form').bounding_box()['width']>600
    page.set_viewport_size(dict(width=1440,height=900));expect(panel.locator('#layout-docked')).to_have_attribute('aria-pressed','true');bounds()
    page.evaluate("document.querySelector('#autoapply-sidebar').remove()")
    expect(page.locator('#autoapply-sidebar')).to_be_visible();bounds()
    page.evaluate("()=>{const root=document.createElement('html');root.innerHTML='<head></head><body></body>';const form=document.querySelector('form');root.querySelector('body').append(form);document.replaceChild(root,document.documentElement);}")
    expect(page.locator('#autoapply-sidebar')).to_be_visible();bounds()
    expect(page.locator('#name')).to_have_value('Manual preserved value')
    assert page.evaluate('submissions')==0
    page.reload();expect(page.locator('#autoapply-sidebar')).to_be_visible(timeout=10000);bounds()
    expect(page.frame_locator('#autoapply-sidebar iframe').locator('#layout-docked')).to_have_attribute('aria-pressed','true')



@pytest.mark.browser
@pytest.mark.parametrize('width,theme',[(1440,'light'),(500,'light'),(390,'light'),(1440,'dark'),(390,'dark')])
def test_dashboard_alignment_navigation_and_compact_run(loaded_extension,http_dashboard,config,db,listing,width,theme):
    context,worker,_=loaded_extension;server,dashboard=http_dashboard
    ids=queue(db,config,listing,3)
    page=context.new_page();page.emulate_media(color_scheme=theme);page.set_viewport_size(dict(width=width,height=900));errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    marks=[];popup_gets=[]
    def route(incoming):
        url=incoming.request.url
        if url.startswith('http://127.0.0.1:8765/'):
            if incoming.request.method=='POST' and url=='http://127.0.0.1:8765/api/applied':
                marks.append(incoming.request.post_data_json)
            status,body,headers=request(server,incoming.request.method,url.removeprefix('http://127.0.0.1:8765'),incoming.request.all_headers(),incoming.request.post_data)
            import json
            incoming.fulfill(status=status,body=body if isinstance(body,str) else json.dumps(body),headers=headers)
        elif incoming.request.method=='GET' and url in {f'https://example.test/jobs/{n}' for n in range(3)}:
            popup_gets.append(url)
            incoming.fulfill(status=200,content_type='text/html',body='<title>Offline application</title><p>Synthetic application</p>')
        else:incoming.abort()
    context.route('**/*',route);page.goto('http://127.0.0.1:8765/#priority')
    row=page.locator('#rows .item').first;expect(row).to_be_visible()
    header=page.locator('#rows .applied-head .sort-button');check=row.locator('.applied-check input')
    h,c=header.bounding_box(),check.bounding_box();assert abs(h['x']+h['width']/2-c['x']-c['width']/2)<1, page.evaluate('''()=>[...document.querySelectorAll('#rows .applied-head,#rows .applied-head button,#rows .item:first-of-type .actions')].map(e=>({tag:e.className,display:getComputedStyle(e).display,columns:getComputedStyle(e).gridTemplateColumns,gap:getComputedStyle(e).gap,justify:getComputedStyle(e).justifySelf,box:e.getBoundingClientRect().toJSON()}))''')
    assert page.evaluate('document.querySelector("main").scrollWidth<=document.querySelector("main").clientWidth')
    page.evaluate("""()=>{window.opens=[];document.addEventListener('click',e=>{const a=e.target.closest('a');if(a){opens.push(a.href);if(window.suppressOpen!==false)e.preventDefault();}},true)}""")
    row.locator('.rank').click();assert page.evaluate('opens.length')==1
    row.locator('.role a').first.click();assert page.evaluate('opens.length')==2
    row.locator('.chrome-check input').check();assert page.evaluate('opens.length')==2
    row.focus();page.keyboard.press('Enter');assert page.evaluate('opens.length')==3
    row.locator('.not-eligible-button').click();expect(page.locator('#eligibility-dialog')).to_be_visible();assert page.evaluate('opens.length')==3;page.keyboard.press('Escape');page.wait_for_timeout(150)
    page.evaluate("()=>{const r=document.createRange();r.selectNodeContents(document.querySelector('#rows .identity'));getSelection().removeAllRanges();getSelection().addRange(r)}")
    assert page.evaluate('Boolean(getSelection().toString())')
    row.locator('.rank').dispatch_event('click',dict(button=0));assert page.evaluate('opens.length')==3;page.evaluate('getSelection().removeAllRanges()')
    page.evaluate("renderChromeRun({items:[{application_id:1,run_id:'fixture',state:'opened',company:'Extremely long synthetic company '.repeat(8),title:'Long internship '.repeat(10)}],poll_age_seconds:0})")
    expect(page.locator('#chrome-run-cancel')).to_be_visible();assert page.locator('#chrome-last-run').count()==0
    assert page.evaluate('document.querySelector("main").scrollWidth<=document.querySelector("main").clientWidth')
    ARTIFACTS.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(ARTIFACTS/f'dashboard-{width}-{theme}.png'))
    # Grid expansion and mark controls do only their own work.
    page.evaluate("models.priority[0].description='Synthetic description '.repeat(100);views.priority='grid';draw('priority')")
    expand=page.locator('#rows .item').first.locator('.queue-description button');expect(expand).to_be_visible();expand.click()
    assert page.evaluate('opens.length')==3
    marked_id=page.locator('#rows .item').first.get_attribute('data-application-id')
    marked_row=page.locator(f'#rows .item[data-application-id="{marked_id}"]')
    marked_row.locator('.applied-check input').click()
    expect(page.locator('#rows .item')).to_have_count(2);assert page.evaluate('opens.length')==3
    expect(marked_row).to_have_count(0)
    expect(page.locator('#toast-message')).to_have_text(f'Marked #{marked_id} as applied')
    assert marks==[{'application_id':int(marked_id)}]
    assert {int(value) for value in page.locator('#rows .item').evaluate_all('(rows)=>rows.map(row=>row.dataset.applicationId)')}==set(ids)-{int(marked_id)}
    assert popup_gets==[]
    page.evaluate("views.priority='list';draw('priority');window.suppressOpen=false")
    popup_url=page.locator('#rows .item').first.locator('.role a').first.get_attribute('href')
    with context.expect_page() as opened:
        page.locator('#rows .item').first.locator('.rank').click()
    popup=opened.value;popup.wait_for_load_state();expect(popup).to_have_url(popup_url)
    expect(popup.locator('p')).to_have_text('Synthetic application');assert popup_gets==[popup_url]
    assert page.evaluate('opens.length')==4;popup.close()
    page.evaluate("()=>{const empty=makeRow('priority',{application_id:99,company:'No URL',title:'No URL'});if(empty.querySelector('a')||empty.tabIndex===0)throw Error('Empty URL row is navigable')}")
    assert not errors

    assert marks==[{'application_id':int(marked_id)}]
