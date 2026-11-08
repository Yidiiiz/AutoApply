"""T077: real MV3 and backend, synthetic data and fixture-only employer pages."""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import yaml
from playwright.sync_api import expect

from autoapply.dashboard import Dashboard, DashboardHandler, DashboardServer
from autoapply.extension_api import resolve_answers
from autoapply.repair_queue import evidence
from test_extension import loaded_extension, select_analyze
from test_dashboard import dashboard, http_dashboard, request, offline_descriptions
from test_extension_api import ext, analysis_payload

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://jobs.lever.co/synthetic/t077/apply'
SECOND = 'https://jobs.lever.co/synthetic/t077-second/apply'
FIXTURE = ROOT / 'tests/fixtures/extension/t077.html'


@pytest.fixture
def backend(config, db, listing):
    profile = config.profile
    profile['contact'].update(city='Boston', state='Massachusetts', country='United States')
    profile['links'] = {'personal_website': 'https://portfolio.example.test/'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    ids = []
    for url in (URL, SECOND):
        job, _ = db.ingest(replace(listing, url=url), config)
        ids.append(db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id'])
    service = Dashboard(config, db)
    service.repairs.configure({'enabled': True})
    state = dict(busy=0, answers=0, replies=[], hold=None, entered=threading.Event(), park_hold=None, park_entered=threading.Event(), posts=[])

    class Handler(DashboardHandler):
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.payload=json.loads(raw or b'{}')
            self.rfile=io.BytesIO(raw)
            state['posts'].append((self.path,self.payload))
            if self.path == '/ext/answers':
                state['answers'] += 1
                if state['hold'] is not None:
                    state['entered'].set()
                    assert state['hold'].wait(10)
                if state['busy']:
                    state['busy'] -= 1
                    self._extension_origin = self.headers.get('Origin')
                    self._unexpected_error(sqlite3.OperationalError('database is locked'))
                    return
            super().do_POST()

        def _reply(self, status, data, **kwargs):
            state['replies'].append((self.path, status, data))
            if self.path=='/ext/run/result' and getattr(self,'payload',{}).get('state')=='skipped' and state['park_hold']:
                state['park_entered'].set()
                assert state['park_hold'].wait(10)
            super()._reply(status, data, **kwargs)

    server = DashboardServer(service, 0)
    server.RequestHandlerClass = Handler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, f'http://127.0.0.1:{server.server_port}', state, ids
    finally:
        if state['hold']:
            state['hold'].set()
        if state['park_hold']:
            state['park_hold'].set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        service.close()


def open_fixture(loaded_extension, backend, *, broken=False):
    context, worker, extension_id = loaded_extension
    service, server, state, ids = backend
    worker.evaluate('value=>chrome.storage.local.set(value)',
                    {'server': server, 'key': service.settings.extension_key(),
                     'autoSendStructure': False, 'autoAccountFill': False})
    html = FIXTURE.read_text(encoding='utf-8')
    if broken:
        html = html.replace('</ul>', '<li class="application-question"><label>Unsupported fixture<div class="application-field"><input type="range" id="broken"></div></label></li></ul>')
    context.route('https://jobs.lever.co/**', lambda route: route.fulfill(body=html, content_type='text/html') if route.request.method=='GET' else route.abort())
    page = context.new_page()
    page.goto(URL)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', URL)
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
    select_analyze(page.frame_locator('#autoapply-sidebar iframe'))
    panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    return page, worker, tab, panel


def own_run(worker, backend, tab):
    service, _, _, ids = backend
    service._ext_run = [dict(application_id=identifier,run_id='synthetic-run',state='opened' if i==0 else 'pending',
        url=url,company='Synthetic',title='Intern') for i,(identifier,url) in enumerate(zip(ids,(URL,SECOND)))]
    current = dict(service._ext_run[0], index=1, total=2)
    worker.evaluate('run=>chrome.storage.session.set({chromeRun:run})',
                    dict(active=True,tabId=tab,started=True,current=current,index=1,total=2))
    result = worker.evaluate('v=>autoApplyAutofill(v.tab,v.app)', {'tab':tab,'app':current})
    assert not result.get('error'), result
    return current


@pytest.mark.browser
def test_analyze_known_lever_fields_resume_and_bounded_busy_recovery(loaded_extension, backend, db):
    page, worker, tab, panel = open_fixture(loaded_extension, backend)
    service, _, state, _ = backend
    state['busy'] = 2
    select_analyze(panel)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    expect(page.locator('[name=name]')).to_have_value('Test Student')
    expect(page.locator('[name=email]')).to_have_value('student@example.test')
    expect(page.locator('[name=phone]')).to_have_value('2025550100')
    expect(page.locator('[name=location]')).not_to_be_empty()
    expect(page.locator('[name=portfolio]')).to_have_value('https://portfolio.example.test/')
    expect(page.locator('[name=company]')).to_be_empty()
    expect(page.locator('[name="urls[Other]"]')).to_be_empty()
    assert page.locator('[type=file]').evaluate('e=>e.files[0].name')
    snapshot = next(data for path, status, data in state['replies'] if path=='/ext/snapshot' and status==200)
    assert snapshot['summary'].startswith('Filled 6'), snapshot
    assert state['answers'] == 3
    assert not service._ext_run
    assert page.evaluate('actions') == []
    assert not [r for r in state['replies'] if r[0] in {'/ext/applied','/ext/run/result'}]
    assert all(not row['submitted_at'] for row in db.rows('SELECT submitted_at FROM applications'))


@pytest.mark.browser
@pytest.mark.parametrize('change', ['edit', 'navigation', 'cancel', 'auth'])
def test_delayed_analysis_preserves_user_and_run_guards(loaded_extension, backend, change):
    page, worker, tab, panel = open_fixture(loaded_extension, backend, broken=True)
    service, _, state, ids = backend
    own_run(worker, backend, tab)
    page.locator('input').evaluate_all("es=>es.forEach(e=>{if(e.type!=='range')e.value='';})")
    state['replies'].clear()
    service.repairs.configure({'enabled':True})
    state['hold'] = threading.Event()
    if change == 'auth':
        page.locator('form').evaluate("e=>e.innerHTML='<label>Email<input type=email></label><label>Password<input type=password></label><button onclick=actions.push(1)>Sign in</button>'")
    select_analyze(panel)
    panel.locator('#send-structure').click()
    if change != 'auth':
        for _ in range(100):
            if state['entered'].is_set():
                break
            page.wait_for_timeout(50)
        assert state['entered'].is_set(), panel.locator('#error').inner_text()
        if change == 'edit':
            page.locator('[name=name]').fill('Synthetic manual edit')
        elif change == 'navigation':
            page.goto(SECOND)
        else:
            # The real run-control listener must invalidate an in-flight Analyze.
            panel.evaluate("()=>chrome.runtime.sendMessage({type:'run-cancel'})")
        state['hold'].set()
    expect(panel.locator('#send-structure')).to_be_enabled(timeout=20000) if change!='navigation' else page.wait_for_timeout(800)
    # Run ticks may report progress without releasing the current application.
    progress = [data for path,data in state['posts'] if path=='/ext/run/result']
    assert all(data.get('state') in {'needs_input', 'failed', 'filled'} for data in progress), progress
    assert not [path for path,_ in state['posts'] if path in {'/ext/applied', '/ext/run/skip'}]
    assert all(item['state']=='skipped' for item in service._ext_run) if change=='cancel' else service._ext_run[0]['state']=='opened'
    assert service._ext_run[1]['state']=='skipped' if change=='cancel' else service._ext_run[1]['state']=='pending'
    assert page.evaluate('actions') == []
    if change == 'auth':
        assert not any(t['url']==SECOND for t in worker.evaluate('()=>chrome.tabs.query({})'))
    if change == 'edit':
        expect(page.locator('[name=name]')).to_have_value('Synthetic manual edit')
        expect(page.locator('[name=email]')).to_be_empty()


@pytest.mark.browser
def test_structural_repair_parks_owned_run_once_and_keeps_page(loaded_extension, backend, db):
    page, worker, tab, panel = open_fixture(loaded_extension, backend, broken=True)
    service, _, state, ids = backend
    own_run(worker, backend, tab)
    select_analyze(panel)
    panel.locator('#send-structure').click()
    expect(panel.locator('#send-structure')).to_be_enabled(timeout=25000)
    worker.evaluate('()=>autoApplyRunTick()')
    worker.evaluate('()=>autoApplyRunTick()')
    assert service._ext_run[0]['state']=='skipped', state['replies']
    assert service._ext_run[1]['state']=='opened'
    skips = [data for path,data in state['posts'] if path=='/ext/run/result' and data.get('state')=='skipped']
    assert len(skips)==1
    assert len(service.repairs.status()['jobs']) == 1
    assert len(list(service.config.private.joinpath('snapshots').glob('*.json'))) >= 1
    assert 'Parked unsubmitted for repair Repair_' in service._ext_run[0]['detail']
    assert page.url == URL and not page.is_closed()
    tabs = worker.evaluate('()=>chrome.tabs.query({})')
    assert sum(t['url']==SECOND for t in tabs) == 1
    assert page.evaluate('actions') == []
    assert all(not row['submitted_at'] for row in db.rows('SELECT submitted_at FROM applications'))
    assert not [r for r in state['replies'] if r[0]=='/ext/applied']


def test_answer_reads_do_not_contend_with_writer_or_export_history(http_dashboard, config, db, listing, monkeypatch):
    service = http_dashboard[1]
    key = service.settings.extension_key()
    job, _ = db.ingest(listing, config)
    original = db.one('SELECT description FROM jobs WHERE id=?',(job,))['description']
    release, entered = threading.Event(), threading.Event()
    def hold_writer():
        connection=sqlite3.connect(service.path, isolation_level=None)
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("INSERT INTO settings VALUES ('synthetic-writer','true')")
        entered.set()
        assert release.wait(8)
        connection.execute('COMMIT')
        connection.close()
    thread=threading.Thread(target=hold_writer)
    thread.start()
    assert entered.wait(5)
    payload=dict(url=listing.url,page={'description':'Synthetic public description '*100},fields=[
        dict(key='name',label='Full name',kind='text',required=True),
        dict(key='unknown',label='Current company',kind='text',required=False),
        dict(key='email',label='Email',kind='email',required=True)])
    try:
        started=time.monotonic()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:request(http_dashboard[0],'POST','/ext/answers',
                {'Origin':'chrome-extension://fixture','X-AutoApply-Key':key},json.dumps(payload)), range(8)))
        assert time.monotonic()-started < 3
        assert all(status==200 for status,_,_ in results), results
        assert [a['status'] for a in results[0][1]['answers']]==['RESOLVED','OPTIONAL_SKIP','RESOLVED']
        assert not release.is_set()
        # A real competing write waits for the same lock, then commits normally.
        with ThreadPoolExecutor() as pool:
            write=pool.submit(request,http_dashboard[0],'POST','/ext/remember',
                {'Origin':'chrome-extension://fixture','X-AutoApply-Key':key},json.dumps(dict(url=listing.url,
                    key='saved',label='Synthetic preferred tool',kind='text',value='Python')))
            time.sleep(.15)
            assert not write.done()
            release.set()
            assert write.result(timeout=5)[0]==200
    finally:
        release.set()
        thread.join(5)
    assert db.one('SELECT description FROM jobs WHERE id=?',(job,))['description']==original
    assert db.one('SELECT count(*) AS n FROM history_dirty')['n']==0


def test_transport_failure_is_not_a_user_fact_or_structural_veto():
    capture=analysis_payload()
    field=capture['diagnostic']['fields'][0]
    field.update(active=True,answered=False,reasons=['omitted_by_scanner'])
    capture['diagnostic']['repair']={'stopped':'transport_error'}
    assert evidence(capture)[0]['controls']
    field.update(reasons=[],detected=True)
    assert evidence(capture)==(None,'transport')
    capture['diagnostic']['repair']['stopped']='user_edit'
    field['reasons']=['omitted_by_scanner']
    assert evidence(capture)==(None,'user')


def test_dashboard_second_process_exits_before_initialization(tmp_path):
    # Execute the real CLI guard in separate Python processes without a server,
    # configuration/profile reads, migrations, provider calls or console windows.
    prefix = "import sys,tempfile; from autoapply import __main__ as m; lockdir=sys.argv[1]; tempfile.gettempdir=lambda:lockdir; sys.argv=['autoapply','dashboard','--port','49777','--no-browser']; "
    first = prefix + "m._main_initialized=lambda args:(print('owned',flush=True),input(),0)[-1]; raise SystemExit(m.main())"
    second = prefix + "m._main_initialized=lambda args:(_ for _ in ()).throw(AssertionError('duplicate initialization')); raise SystemExit(m.main())"
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    owner=subprocess.Popen([sys.executable,'-c',first,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=flags)
    try:
        assert owner.stdout.readline().strip()=='owned'
        started=time.monotonic()
        result=subprocess.run([sys.executable,'-c',second,str(tmp_path)],capture_output=True,text=True,timeout=5,creationflags=flags)
        assert result.returncode==0, result.stderr
        assert 'Dashboard already running' in result.stdout
        assert time.monotonic()-started < 3
    finally:
        owner.communicate(chr(10),timeout=5)
    # Lock releases with its owner, without process discovery or stale PID state.
    from autoapply.runtime import ProcessLock
    with ProcessLock(tmp_path/'autoapply-dashboard-127.0.0.1-49777.lock'):
        pass


@pytest.mark.browser
@pytest.mark.parametrize('change',['edit','navigation','cancel'])
def test_late_parking_ack_cannot_advance_changed_or_cancelled_run(loaded_extension,backend,change):
    page,worker,tab,panel=open_fixture(loaded_extension,backend,broken=True)
    service,_,state,_=backend
    own_run(worker,backend,tab)
    state['park_hold']=threading.Event()
    select_analyze(panel)
    panel.locator('#send-structure').click()
    for _ in range(150):
        if state['park_entered'].is_set():break
        page.wait_for_timeout(50)
    assert state['park_entered'].is_set(), panel.locator('#error').inner_text()
    if change=='edit':page.locator('[name=name]').fill('Synthetic revised name')
    elif change=='navigation':page.goto(SECOND)
    else:panel.evaluate("()=>chrome.runtime.sendMessage({type:'run-cancel'})")
    state['park_hold'].set()
    page.wait_for_timeout(600)
    worker.evaluate('()=>autoApplyRunTick()')
    run=worker.evaluate("()=>chrome.storage.session.get('chromeRun')")['chromeRun']
    assert not run.get('active') if change=='cancel' else run.get('paused')
    assert service._ext_run[1]['state']=='skipped' if change=='cancel' else service._ext_run[1]['state']=='pending'
    tabs=worker.evaluate('()=>chrome.tabs.query({})')
    assert not [t for t in tabs if t['id']!=tab and t.get('url')==SECOND]
    assert page.evaluate('actions')==[]


@pytest.mark.browser
@pytest.mark.parametrize('guard',['disabled','hidden','ambiguous','occupied'])
def test_analyze_resume_association_and_occupied_upload_guards(loaded_extension,backend,guard):
    page,worker,tab,panel=open_fixture(loaded_extension,backend)
    upload=page.locator('[type=file]')
    if guard=='disabled':upload.evaluate('e=>e.disabled=true')
    elif guard=='hidden':upload.evaluate('e=>e.parentElement.hidden=true')
    elif guard=='ambiguous':page.locator('.application-label').first.evaluate("e=>e.textContent='Supporting document'")
    else:upload.set_input_files({'name':'manual-fixture.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4 synthetic manual'})
    select_analyze(panel)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed',timeout=20000)
    expect(page.locator('[name=name]')).to_have_value('Test Student')
    files=upload.evaluate('e=>Array.from(e.files,f=>f.name)')
    assert files==(['manual-fixture.pdf'] if guard=='occupied' else [])
    assert not [r for r in backend[2]['replies'] if r[0]=='/ext/resume']
    assert page.evaluate('actions')==[]


@pytest.mark.browser
@pytest.mark.parametrize('broken',[False,True])
def test_exhausted_transport_is_truthful_and_does_not_veto_structural_queue(loaded_extension,backend,broken):
    page,worker,tab,panel=open_fixture(loaded_extension,backend,broken=broken)
    service,_,state,_=backend
    state['busy']=9
    select_analyze(panel)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed',timeout=15000)
    assert state['answers']==3
    snapshot=next(data for path,status,data in state['replies'] if path=='/ext/snapshot' and status==200)
    assert 'Dashboard transport unavailable after bounded retries' in snapshot['summary']
    assert 'user input' not in snapshot['repair']['message']
    assert bool(service.repairs.status()['jobs'])==broken
    assert snapshot['repair']['status']==('queued' if broken else 'not_queued')
    expect(page.locator('[name=name]')).to_be_empty()
    assert page.evaluate('actions')==[]


@pytest.mark.browser
@pytest.mark.parametrize('paused', [False, True])
def test_exhausted_transport_parks_review_error_run_unless_user_paused(loaded_extension, backend, db, paused):
    page, worker, tab, panel = open_fixture(loaded_extension, backend, broken=True)
    employer_writes = []
    loaded_extension[0].on('request', lambda request: employer_writes.append(request.url)
        if request.url.startswith('https://jobs.lever.co/') and request.method not in {'GET', 'HEAD'} else None)
    service, _, state, _ = backend
    current = own_run(worker, backend, tab)
    # Fault injection belongs to this next run, after the prior optional tail.
    worker.evaluate('''async id=>{for(let i=0;i<200;i++){
      const r=(await chrome.storage.session.get('tabResults')).tabResults?.[id];
      if(r?.writingSettled)return;await new Promise(resolve=>setTimeout(resolve,50));
    }throw Error('Owned autofill stages did not settle');}''', tab)
    # Exercise the real existing-run error path rather than inventing pause state.
    state['busy'] = 1
    failed = worker.evaluate('v=>autoApplyAutofill(v.tab,v.app)', {'tab':tab,'app':current})
    assert 'Dashboard is busy' in failed['error']
    worker.evaluate('()=>autoApplyRunTick()')
    run = worker.evaluate("()=>chrome.storage.session.get(['chromeRun','chromeRunPaused'])")
    assert run['chromeRun']['phase']=='failed'
    assert not run.get('chromeRunPaused') and not run['chromeRun'].get('paused')
    if paused:
        panel.evaluate("()=>chrome.runtime.sendMessage({type:'run-pause',paused:true})")
    state['answers'] = 0
    state['busy'] = 9
    state['posts'].clear()
    state['replies'].clear()
    select_analyze(panel)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    worker.evaluate('()=>autoApplyRunTick()')
    worker.evaluate('()=>autoApplyRunTick()')
    assert state['answers']==3
    snapshot = next(data for path,status,data in state['replies'] if path=='/ext/snapshot' and status==200)
    assert snapshot['analysis_id'] and snapshot['repair']['id']
    assert snapshot['repair']['status']=='queued'
    assert 'Dashboard transport unavailable after bounded retries' in snapshot['summary']
    assert 'user input' not in snapshot['repair']['message']
    assert len(service.repairs.status()['jobs'])==1
    skips = [data for path,data in state['posts'] if path=='/ext/run/result' and data.get('state')=='skipped']
    assert len(skips)==(0 if paused else 1)
    assert service._ext_run[0]['state']==('opened' if paused else 'skipped')
    assert service._ext_run[1]['state']==('pending' if paused else 'opened')
    tabs = worker.evaluate('()=>chrome.tabs.query({})')
    assert sum(t.get('url')==SECOND for t in tabs)==(0 if paused else 1)
    if not paused:
        assert 'Parked unsubmitted for repair '+snapshot['repair']['id'] in service._ext_run[0]['detail']
    else:
        assert worker.evaluate("()=>chrome.storage.session.get('chromeRunPaused')")['chromeRunPaused']
    assert page.url==URL and not page.is_closed()
    assert page.evaluate('actions')==[]
    assert all(not row['submitted_at'] for row in db.rows('SELECT submitted_at FROM applications'))
    assert not [path for path,_ in state['posts'] if path=='/ext/applied']
    assert employer_writes==[]
