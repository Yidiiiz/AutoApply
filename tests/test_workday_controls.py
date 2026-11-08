"""T087 public-shape MV3 regressions. No live employer/provider requests.

Root gate: pytest tests/test_workday_controls.py -m browser
Worker/offline gate: pytest tests/test_workday_controls.py -m 'not browser'
"""
import json
from pathlib import Path
import subprocess

import pytest
from playwright.sync_api import expect

from autoapply.extension_api import resolve_answers
from test_t078_accounts import (
    EMAIL, PASSWORD, URL, backend, dashboard, loaded_extension, offline_descriptions,
)

FIXTURE = Path(__file__).parent / 'fixtures/extension/workday/regressions.html'
ROOT = Path(__file__).resolve().parents[1]


def open_controls(loaded_extension, backend, view='review', automatic=False):
    context, worker, extension_id = loaded_extension
    service, server, _ = backend
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server,
        key=service.settings.extension_key(), autoSendStructure=False, autoAccountFill=automatic))
    blocked = []
    def serve(route):
        if route.request.method == 'GET':
            route.fulfill(path=str(FIXTURE), content_type='text/html')
        else:
            blocked.append(route.request.method)
            route.abort()
    context.route('https://fixture.myworkdayjobs.com/**', serve)
    page = context.new_page()
    page.add_init_script('window.initialView=' + json.dumps(view))
    page.goto(URL.removesuffix('/apply') if view == 'landing' else URL)
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
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


def save_source(config):
    import yaml
    profile = config.profile
    profile.setdefault('application_preferences', {}).update(application_source_answer='Other', discovery_source='Job board')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')


def own_review(worker, backend, tab):
    service, _, app = backend
    item = dict(application_id=app, run_id='t087-synthetic', state='opened', url=URL,
                title='Synthetic Intern', company='Synthetic')
    service._ext_run = [item]
    worker.evaluate('run=>chrome.storage.session.set({chromeRun:run,chromeRunPaused:false})',
                    dict(active=True, tabId=tab, started=True, current=item, index=1, total=1))
    result = worker.evaluate('v=>autoApplyAutofill(v.tab,v.app)', dict(tab=tab, app=item))
    assert not result.get('error'), result
    worker.evaluate('()=>autoApplyRunTick()')
    return result


def send(panel, tab, kind, **args):
    return panel.evaluate('v=>chrome.runtime.sendMessage(v)', dict(type=kind, tabId=tab, **args))


@pytest.mark.browser
@pytest.mark.parametrize('foreign', [False, True])
def test_native_workday_entry_and_qualified_anchor(loaded_extension, backend, foreign):
    page, _, _, panel, isolated, blocked = open_controls(loaded_extension, backend, 'landing')
    try:
        if foreign:
            page.locator('#entry').evaluate("e=>e.href='https://foreign.example.test/apply'")
        state = isolated('navigationState')
        assert state['kind'] == ('none' if foreign else 'entry')
        if not foreign:
            expect(panel.locator('#next-page')).to_have_text('Apply')
        assert isolated('enterApplication') is (not foreign)
        assert page.evaluate('[entryClicks,applicationClicks]') == [0 if foreign else 1, 0]
        if not foreign:
            expect(page.locator('[data-automation-id="searchBox"]')).to_be_visible()
        assert not blocked
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('nested,method', [(False, 'autofill'), (True, 'page-snapshot')])
def test_source_leaf_and_two_level_exact_saved_answer(loaded_extension, backend, config, nested, method):
    save_source(config)
    page, worker, tab, panel, isolated, blocked = open_controls(loaded_extension, backend, 'source')
    try:
        page.evaluate('v=>nested=v', nested)
        if method == 'autofill':
            result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
        else:
            result = send(panel, tab, method)
            assert result.get('ok'), result
        expect(page.locator('[data-automation-id="selectedItem"]')).to_have_text('Other')
        assert isolated('scan')['fields'][0]['kind'] == 'combobox'
        assert isolated('scan')['fields'][0]['answered']
        assert page.locator('[data-popup="current"]').count() == 0
        assert page.evaluate('menuClicks.filter(x=>x==="Other").length') == 1
        assert not blocked and page.evaluate('applicationClicks') == 0
    finally:
        page.close()


@pytest.mark.browser
def test_known_yes_menu_is_grounded_after_native_fast_fill(loaded_extension, backend):
    page, worker, tab, panel, isolated, blocked = open_controls(loaded_extension, backend, 'choices')
    try:
        page.evaluate('''()=>{
          document.querySelector('[data-automation-id="formField-degree"]').remove();
          document.querySelector('label[for="identity"]').textContent='Are you legally authorized to work in the United States?';
          stage.insertAdjacentHTML('beforeend','<label>Email<input id=known-email type=email></label>');
          const open=identity.onclick;globalThis.emailAtDiscovery=[];
          identity.onclick=()=>{emailAtDiscovery.push(document.getElementById('known-email').value);open();};
        }''')
        worker.evaluate('''()=>{const original=fetch;globalThis.answerBatches=[];
          globalThis.fetch=(url,options)=>{if(String(url).endsWith('/ext/answers'))answerBatches.push(JSON.parse(options.body));
            return original(url,options);};}''')
        reply = send(panel, tab, 'page-snapshot')
        assert reply.get('ok'), reply
        expect(page.locator('#identity')).to_have_text('Yes')
        expect(page.locator('#known-email')).to_have_value('student@example.test')
        assert page.evaluate('emailAtDiscovery.every(v=>v==="student@example.test") && emailAtDiscovery.length>0')
        batches = worker.evaluate('answerBatches')
        assert len(batches) == 2 and batches[0]['allow_ai'] is False
        assert next(f for f in batches[0]['fields'] if f['kind'] == 'combobox')['options'] == []
        assert len(batches[1]['fields']) == 1 and batches[1]['fields'][0]['options'] == ['Yes', 'No']
        assert all(f['answered'] for f in isolated('scan')['fields'])
        assert page.locator('[data-popup="current"]').count() == 0
        assert page.evaluate('menuClicks') == ['Yes']
        assert not blocked and page.evaluate('applicationClicks') == 0
    finally:
        page.close()


@pytest.mark.browser
def test_owned_popup_excludes_old_portal_and_preserves_exact_degree(loaded_extension, backend):
    page, _, _, _, isolated, _ = open_controls(loaded_extension, backend, 'choices')
    try:
        page.evaluate('staleMenu()')
        fields = isolated('scan')['fields']
        options = isolated('collectOptions', [f['key'] for f in fields])['fields']
        identity = next(f for f in options if f['label'] == 'Identity verification')
        degree = next(f for f in options if f['label'].startswith('Degree'))
        assert identity['options'] == ['Yes', 'No']
        exact = page.evaluate('degreeLabel')
        assert exact in degree['options']
        result = isolated('fill', [dict(key=identity['key'], status='RESOLVED', value='Yes'),
                                   dict(key=degree['key'], status='RESOLVED', value=exact)])
        assert result['filled'] == 2, result
        expect(page.locator('#identity')).to_have_text('Yes')
        expect(page.locator('[name="degree"]')).to_have_text(exact)
        assert page.locator('[data-popup="current"]').count() == 0
        assert page.locator('[data-popup="stale"]').count() == 1
        assert page.evaluate('applicationClicks') == 0
    finally:
        page.close()


@pytest.mark.browser
def test_virtualized_java_and_c_cpp_skills_close_without_unselecting(loaded_extension, backend):
    page, _, _, _, isolated, _ = open_controls(loaded_extension, backend, 'skills')
    try:
        page.evaluate('staleMenu()')
        result = isolated('autofillSkills', ['Java', 'C/C++'])
        assert result['skipped'] == 0, result
        assert sorted(page.locator('[data-automation-id="selectedItem"]').all_text_contents()) == ['C', 'C++', 'Java']
        assert page.locator('[data-popup="current"]').count() == 0
        again = isolated('autofillSkills', ['Java', 'C/C++'])
        assert again['added'] == 0
        assert page.locator('[data-automation-id="selectedItem"]').count() == 3
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('ambiguous', [False, True])
def test_hidden_native_resume_group_analyze_and_ambiguity(loaded_extension, backend, ambiguous):
    page, _, tab, panel, isolated, blocked = open_controls(loaded_extension, backend, 'resume')
    try:
        if ambiguous:
            page.locator('[data-automation-id="attachments-FileUpload"]').first.evaluate(
                "e=>e.insertAdjacentHTML('beforeend','<input type=file style=\"width:0;height:0;opacity:0\" data-automation-id=\"file-upload-input-ref\">')")
        scan = isolated('scan')
        resumes = [f for f in scan['fields'] if f['kind'] == 'file' and f['label'] == 'Resume/CV']
        assert len(resumes) == (0 if ambiguous else 1)
        result = send(panel, tab, 'page-snapshot')
        assert result.get('ok'), result
        counts = page.locator('input[type=file]').evaluate_all('els=>els.map(e=>e.files.length)')
        assert counts == ([0, 0, 0] if ambiguous else [1, 0])
        assert not blocked and page.evaluate('applicationClicks') == 0
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('unavailable', ['missing', 'disabled'])
def test_preview_signin_explicit_late_primary_retry_once(loaded_extension, backend, unavailable):
    page, worker, tab, panel, _, blocked = open_controls(loaded_extension, backend, 'account', automatic=True)
    try:
        expect(page.locator('#email')).to_have_value(EMAIL)
        expect(page.locator('#password')).to_have_value(PASSWORD)
        assert page.evaluate('accountClicks') == 0
        assert not worker.evaluate("async()=>Object.keys((await chrome.storage.session.get('accountActionClaims')).accountActionClaims||{}).length")
        page.evaluate("window.savedPrimary=primary;" + ('primary.remove()' if unavailable == 'missing' else 'primary.disabled=true'))
        result = send(panel, tab, 'next-page')
        assert not result.get('error'), result
        assert page.evaluate('accountClicks') == 0
        page.evaluate('savedPrimary.disabled=false;if(!savedPrimary.isConnected)stage.querySelector("[data-automation-id=signInContent]").append(savedPrimary)')
        # The trusted footer uses the existing private action path, and can retry
        # an unavailable action without rewriting credentials or burning a claim.
        expect(panel.locator('#next-page')).to_have_text('Sign in')
        if unavailable == 'disabled':
            worker.evaluate("""()=>{const execute=chrome.scripting.executeScript.bind(chrome.scripting);
              let fail=true;chrome.scripting.executeScript=options=>{
                if(fail && String(options.func).includes('autoApplyCredentials?.activate')){
                  fail=false;return execute({...options,func:id=>{
                    const button=document.getElementById('primary');button.disabled=true;
                    const result=autoApplyCredentials.activate(id);button.disabled=false;return result;
                  }});
                }return execute(options);
              };}""")
            failed = send(panel, tab, 'next-page')
            assert not failed.get('error'), failed
            assert page.evaluate('accountClicks') == 0
            assert not worker.evaluate("async()=>Object.keys((await chrome.storage.session.get('accountActionClaims')).accountActionClaims||{}).length")
        panel.locator('#next-page').click()
        for _ in range(60):
            if page.evaluate('accountClicks'):
                break
            page.wait_for_timeout(50)
        assert page.evaluate('accountClicks') == 1
        send(panel, tab, 'next-page')
        assert page.evaluate('accountClicks') == 1
        assert not blocked and page.evaluate('applicationClicks') == 0
        stored = worker.evaluate('()=>chrome.storage.session.get(null)')
        assert EMAIL not in json.dumps(stored) and PASSWORD not in json.dumps(stored)
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('guard', ['owned', 'foreign', 'paused'])
def test_explicit_footer_submit_owned_run_and_guards(loaded_extension, backend, guard):
    page, worker, tab, panel, _, blocked = open_controls(loaded_extension, backend)
    try:
        result = own_review(worker, backend, tab)
        assert result['next_action']['kind'] == 'submit' and not result.get('waiting')
        assert page.evaluate('applicationClicks') == 0
        if guard == 'foreign':
            worker.evaluate('''async()=>{const {chromeRun}=await chrome.storage.session.get('chromeRun');chromeRun.current.application_id+=100;await chrome.storage.session.set({chromeRun});}''')
        if guard == 'paused':
            worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:true})')
        reply = send(panel, tab, 'manual-submit', expected=result['next_action'])
        assert bool(reply.get('error')) is (guard != 'owned'), reply
        assert page.evaluate('applicationClicks') == (1 if guard == 'owned' else 0)
        assert backend[0]._ext_run[0]['state'] == 'opened'
        assert not blocked
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize('scenario', ['receipt_close', 'close_only', 'invalid', 'description', 'stale'])
def test_verified_receipt_once_wins_close_and_negative_proofs_abstain(loaded_extension, backend, db, scenario):
    page, worker, tab, panel, isolated, blocked = open_controls(loaded_extension, backend)
    service, _, app = backend
    result = own_review(worker, backend, tab)
    # Hold only the local backend acknowledgement. Employer requests remain aborted.
    worker.evaluate('''()=>{const original=fetch;globalThis.appliedRequests=0;
      globalThis.releaseApplied=undefined;
      globalThis.fetch=async(url,...args)=>{
        if(!String(url).endsWith('/ext/applied'))return original(url,...args);
        appliedRequests++;const response=await original(url,...args);
        await new Promise(resolve=>{globalThis.releaseApplied=resolve});return response;
      };}''')
    if scenario == 'stale':
        page.evaluate('receipt()')
    if scenario != 'close_only':
        reply = send(panel, tab, 'manual-submit', expected=result['next_action'])
        assert not reply.get('error'), reply
    if scenario == 'receipt_close':
        page.evaluate("receipt();setInterval(()=>feedback.append(document.createTextNode(' ')),10)")
        # Wait for durable, natively verified proof, then close immediately while
        # its API acknowledgement is held. No proof is inferred from closure.
        assert worker.evaluate('''async id=>{for(let i=0;i<100;i++){
          const {pendingSubmissions={}}=await chrome.storage.session.get('pendingSubmissions');
          if(pendingSubmissions[id] && globalThis.releaseApplied)return true;
          await new Promise(r=>setTimeout(r,20));}return false;}''', tab)
        page.close()
        assert worker.evaluate('appliedRequests') == 1
        worker.evaluate('()=>releaseApplied()')
        assert worker.evaluate('''async()=>{for(let i=0;i<100;i++){
          const {chromeRun,pendingSubmissions={}}=await chrome.storage.session.get(['chromeRun','pendingSubmissions']);
          if(!chromeRun?.current && !Object.keys(pendingSubmissions).length)return true;
          await new Promise(r=>setTimeout(r,20));}return false;}''')
        assert service._ext_run[0]['state'] == 'submitted'
        assert db.setting('duplicate_submission_guard:' + str(app))['source'] == 'DASHBOARD_MANUAL'
        worker.evaluate('()=>autoApplyRunTick()')
        assert worker.evaluate('appliedRequests') == 1
    else:
        if scenario == 'invalid':
            page.evaluate("receipt();feedback.insertAdjacentHTML('beforeend','<div role=alert>Required information missing</div>')")
        if scenario == 'description':
            page.evaluate("feedback.innerHTML='<div data-automation-id=jobPostingDescription><h2>You have successfully submitted your application</h2></div>'")
            assert not isolated('confirmationEvidence')
        page.wait_for_timeout(450)
        assert worker.evaluate('appliedRequests') == 0
        page.close()
        assert worker.evaluate('''async()=>{for(let i=0;i<100;i++){
          const {chromeRun}=await chrome.storage.session.get('chromeRun');if(!chromeRun?.current)return true;
          await new Promise(r=>setTimeout(r,20));}return false;}''')
        assert service._ext_run[0]['state'] != 'submitted'
    assert not blocked


@pytest.mark.browser
@pytest.mark.parametrize('restart', [False, True])
def test_standalone_verified_receipt_retries_after_closed_tab(loaded_extension, backend, db, monkeypatch, restart):
    from autoapply.dashboard import DashboardHandler

    context, _, extension_id = loaded_extension
    service, _, app = backend
    attempts, accepted, reject = [], [], [True]
    original = DashboardHandler.do_POST

    def fail_until_closed(handler):
        if handler.path == '/ext/applied' and handler.server.dashboard is service:
            attempts.append(True)
            if reject[0]:
                # Consume the body, but do not dispatch a write to the backend.
                handler.rfile.read(int(handler.headers.get('Content-Length', '0')))
                body = b'{"error":"synthetic first applied 503"}'
                handler.send_response(503)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
                return
            accepted.append(True)
        return original(handler)

    monkeypatch.setattr(DashboardHandler, 'do_POST', fail_until_closed)
    page, worker, tab, panel, _, blocked = open_controls(loaded_extension, backend)
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert result['matched']['application_id'] == app
    assert not result['matched'].get('run_id')
    assert not send(panel, tab, 'manual-submit', expected=result['next_action']).get('error')
    page.evaluate('receipt()')
    assert worker.evaluate('''async id=>{for(let i=0;i<100;i++){
      const {pendingSubmissions={},tabResults={}}=await chrome.storage.session.get(['pendingSubmissions','tabResults']);
      if(pendingSubmissions[id] && tabResults[id]?.trackingError)return true;
      await new Promise(r=>setTimeout(r,20));}return false;}''', tab)
    assert attempts and not accepted
    assert not db.setting('duplicate_submission_guard:' + str(app))
    # Hold the existing tick while closing/restarting; proof is already native-verified.
    worker.evaluate('()=>chrome.storage.session.set({chromeRunPaused:true})')
    control = context.new_page()
    control.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    page.close()
    assert worker.evaluate('''async id=>{for(let i=0;i<100;i++){
      const s=await chrome.storage.session.get(['pendingSubmissions','autofillTabs','tabResults']);
      if(s.pendingSubmissions?.[id] && s.autofillTabs?.[id] && !s.tabResults?.[id])return true;
      await new Promise(r=>setTimeout(r,20));}return false;}''', tab)
    if restart:
        cdp = context.new_cdp_session(control)
        cdp.send('ServiceWorker.enable')
        cdp.send('ServiceWorker.stopAllWorkers')
        control.evaluate('()=>chrome.runtime.sendMessage({type:"run-status-refresh"})')
        worker = next((w for w in context.service_workers if w.url.endswith('/background.js')), None)
        if worker is None:
            worker = context.wait_for_event('serviceworker', timeout=10000)
    reject[0] = False
    worker.evaluate('async()=>{await chrome.storage.session.set({chromeRunPaused:false});await autoApplyRunTick()}')
    assert worker.evaluate('''async id=>{for(let i=0;i<100;i++){
      const s=await chrome.storage.session.get(['pendingSubmissions','autofillTabs','tabResults']);
      if(!s.pendingSubmissions?.[id] && !s.autofillTabs?.[id] && !s.tabResults?.[id])return true;
      await new Promise(r=>setTimeout(r,20));}return false;}''', tab)
    assert len(accepted) == 1
    assert db.setting('duplicate_submission_guard:' + str(app))['source'] == 'DASHBOARD_MANUAL'
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE application_id=? AND kind='EXT_SUBMISSION_CONFIRMED'", (app,))['n'] == 1
    worker.evaluate('()=>autoApplyRunTick()')
    assert len(accepted) == 1 and not blocked


@pytest.mark.browser
@pytest.mark.parametrize('variant', ['valid', 'native_click', 'other_job', 'other_origin', 'expired', 'preexisting', 'invalid', 'review', 'reassigned', 'paused'])
def test_replacement_document_receipt_detected_natively(loaded_extension, backend, db, variant):
    page, worker, tab, panel, _, blocked = open_controls(loaded_extension, backend)
    _, _, app = backend
    result = own_review(worker, backend, tab)
    suffix = '' if variant == 'review' else 'receipt();'
    if variant == 'invalid':
        suffix += "feedback.insertAdjacentHTML('beforeend','<div role=alert>Required information missing</div>');"
    html = FIXTURE.read_text(encoding='utf-8').replace("show(window.initialView || 'review');", "show('review');" + suffix)

    def replacement(route):
        if route.request.method == 'GET':
            route.fulfill(body=html, content_type='text/html')
        else:
            blocked.append(route.request.method)
            route.abort()

    page.route('https://**.myworkdayjobs.com/**', replacement)
    if variant == 'preexisting':
        page.evaluate('receipt()')
    if variant == 'native_click':
        page.evaluate('final.onclick=()=>{applicationClicks++;location.reload()}')
        page.locator('#final').click()
    else:
        assert not send(panel, tab, 'manual-submit', expected=result['next_action']).get('error')
    if variant in ('expired', 'reassigned', 'paused'):
        worker.evaluate('''async ({id,variant})=>{
          const {autofillTabs}=await chrome.storage.session.get('autofillTabs');
          if(variant==='expired')autofillTabs[id].submitAt=Date.now()-120001;
          if(variant==='reassigned')autofillTabs[id].matched.application_id++;
          await chrome.storage.session.set({autofillTabs,...(variant==='paused'?{chromeRunPaused:true}: {})});
        }''', dict(id=tab, variant=variant))
    if variant == 'other_job':
        page.goto(URL.replace('Intern_T078', 'Different_Job'))
    elif variant == 'other_origin':
        page.goto(URL.replace('fixture.', 'other.'))
    elif variant != 'native_click':
        page.reload()
    # No artificial confirmation message or Analyze call: the new document's
    # observer must offer its visible receipt and the background must reread it.
    applied = variant in ('valid', 'native_click')
    if applied:
        assert worker.evaluate('''async id=>{for(let i=0;i<100;i++){
          if((await chrome.storage.session.get('autofillTabs')).autofillTabs[id]?.applied)return true;
          await new Promise(r=>setTimeout(r,20));}return false;}''', tab)
        binding = worker.evaluate('async id=>(await chrome.storage.session.get("autofillTabs")).autofillTabs[id]', tab)
        current_document = worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>true}))[0].documentId''', tab)
        assert binding['submitDocumentId'] == current_document
    else:
        page.wait_for_timeout(800)
    assert bool(db.setting('duplicate_submission_guard:' + str(app))) is applied
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE application_id=? AND kind='EXT_SUBMISSION_CONFIRMED'", (app,))['n'] == int(applied)
    if variant == 'review':
        page.locator('#feedback').evaluate("e=>e.textContent='Review your application'")
    else:
        page.evaluate("""invalid=>{receipt('Your application has been successfully submitted');
          if(invalid)feedback.insertAdjacentHTML('beforeend','<div role=alert>Required information missing</div>');} """, variant == 'invalid')
    page.wait_for_timeout(400)
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE application_id=? AND kind='EXT_SUBMISSION_CONFIRMED'", (app,))['n'] == int(applied)
    assert not blocked
    page.close()


def test_saved_source_and_resume_offline_contract(backend, config):
    save_source(config)
    result = resolve_answers(backend[0], dict(url=URL, allow_ai=False, fields=[
        dict(key='source', label='How Did You Hear About Us?', kind='combobox', required=True,
             options=['Other'], multiple=True),
        dict(key='resume', label='Resume/CV', kind='file', required=False, options=[]),
    ]))
    answers = {a['key']: a for a in result['answers']}
    assert answers['source']['value'] in ('Other', ['Other'])
    assert answers['resume']['status'] == 'FILE' and answers['resume']['value'] == 'resume'


def test_known_answers_can_precede_offered_options_offline(backend, config):
    save_source(config)
    result = resolve_answers(backend[0], dict(url=URL, allow_ai=False, fields=[
        dict(key='source', label='How Did You Hear About Us?', kind='combobox', required=True, options=[], multiple=True),
        dict(key='eligible', label='Are you legally authorized to work in the United States?', kind='combobox', required=True, options=[]),
    ]))
    answers = {a['key']: a for a in result['answers']}
    # With no offers the saved discovery category resolves immediately; offered
    # leaves must still be discovered and sent back through the resolver.
    assert answers['source']['value'] == 'Job board'
    assert answers['eligible']['value'] == 'Yes'
    assert all(a['status'] == 'RESOLVED' and a['reason_code'] == 'answer_available' for a in answers.values())


def test_scoped_javascript_syntax():
    for name in ('background.js', 'content.js', 'credentials.js', 'sidepanel.js'):
        subprocess.run(['node', '--check', str(ROOT / 'extension' / name)], check=True,
                       capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize('scenario', ['owned_submit', 'foreign_submit', 'poll_submit',
                                      'receipt_close', 'persisted_receipt', 'close_only', 'invalid', 'stale',
                                      'standalone_retry', 'standalone_restart', 'standalone_lost_ack', 'standalone_paused_retry',
                                      'standalone_undo', 'standalone_reassigned', 'standalone_url_changed',
                                      'undo_inflight', 'cancel_pending', 'skip_pending',
                                      'replacement_valid', 'replacement_native_navigation', 'replacement_other_job', 'replacement_other_origin',
                                      'replacement_expired', 'replacement_preexisting', 'replacement_no_intent',
                                      'replacement_invalid', 'replacement_review', 'replacement_paused', 'replacement_pause_resume',
                                      'replacement_reassigned'])
def test_background_submission_lifecycle_offline(scenario):
    result = subprocess.run(['node', str(FIXTURE.with_name('background.cjs')), scenario],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'passed ' + scenario in result.stdout


@pytest.mark.parametrize('scenario', ['offered', 'unoffered'])
def test_known_choices_wait_for_bound_option_discovery_offline(scenario):
    result = subprocess.run(['node', str(FIXTURE.with_name('analysis.cjs')), scenario],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'passed ' + scenario in result.stdout
