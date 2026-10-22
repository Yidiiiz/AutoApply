"""Load the actual unpacked MV3 extension; all HTTP traffic is fixture-only."""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error, expect, sync_playwright

from autoapply.answers import fit_options

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "extension"
KEY = "fixture-pairing-key"
RESUME = b"%PDF-1.4\n% synthetic fixture resume\n%%EOF\n"


def test_t064_outside_heading_generic_application(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    try:
        page.route('**/t064.html', lambda route: route.fulfill(path=str(FIXTURES/'t064.html'), content_type='text/html'))
        page.goto(server+'/t064.html')
        page.evaluate("showView('outside-heading')")
        application = page.locator('#view > form').first
        expect(application.locator('.autoapply-ask-ai')).to_have_count(1)
        expect(page.locator('.autoapply-ask-ai')).to_have_count(1)
        assert all(path in {'/ext/run/status', '/ext/run/next'} for path, _, _ in requests)
        application.get_by_role('button', name='Ask AI', exact=True).click()
        panel = page.frame_locator(f'iframe[src^="chrome-extension://{extension_id}/sidepanel.html"]')
        expect(panel.locator('#question')).to_have_value('Why this role?')
        expect(panel.locator('#ask-section')).to_be_visible()
        expect(page.locator('#autoapply-sidebar')).to_be_visible()
        expect(application.locator('textarea')).to_have_value('')
        for _ in range(2):
            page.locator('#view > h1').evaluate("e=>e.textContent='Application'")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
            page.locator('#view > h1').evaluate("e=>e.textContent='Job application'")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(1)
        application.locator('[type=file]').evaluate('e=>e.remove()')
        expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
        assert not [path for path, _, _ in requests if path in {'/ext/match', '/ext/answers', '/ext/write', '/ext/chat'}]
    finally:
        page.close()


def test_t064_nonapplication_and_spa_scope(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    try:
        page.route('**/t064.html', lambda route: route.fulfill(path=str(FIXTURES/'t064.html'), content_type='text/html'))
        page.goto(server+'/t064.html')
        page.wait_for_timeout(350)
        expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
        assert all(path in {'/ext/run/status', '/ext/run/next'} for path, _, _ in requests)
        tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id', page.url)
        worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
        expect(page.locator('#autoapply-sidebar')).to_be_visible()
        page.wait_for_timeout(200)
        expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
        for _ in range(2):
            page.evaluate("showView('application')")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(3)
            expect(page.locator('#unrelated + .autoapply-ask-ai,#outside + .autoapply-ask-ai,#chat-input + .autoapply-ask-ai')).to_have_count(0)
            page.locator('#essay').evaluate("e=>e.hidden=true")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(2)
            page.locator('#essay').evaluate("e=>e.hidden=false")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(3)
            page.locator('#long-answer').evaluate('e=>e.remove()')
            expect(page.locator('.autoapply-ask-ai')).to_have_count(2)
            # Keep the same writing controls while removing only application evidence.
            page.locator('#application-form').evaluate("e=>{e.querySelector('h1').textContent='Contact us';e.querySelector('[type=file]').remove();e.querySelector('button').textContent='Send';}")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
            page.evaluate("showView('nonapplication')")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
        assert not [path for path, _, _ in requests if path in {'/ext/match','/ext/answers','/ext/write','/ext/chat'}]
    finally:
        page.close()


def test_t064_supported_ats_routes_require_application_structure(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    try:
        cases = [
            ('https://fixture.myworkdayjobs.com/en-US/jobs/job/123/apply', 'workday_spa.html'),
            ('https://boards.greenhouse.io/fixture/jobs/123', 'greenhouse.html'),
            ('https://fixture.pinpointhq.com/postings/123/applications/new', 't057-pinpoint.html'),
            ('https://fixture.applytojob.com/apply/123/Engineer', 't057-jazzhr.html'),
        ]
        for url, fixture in cases:
            page.route(url, lambda route: route.fulfill(path=str(FIXTURES/'t064.html'), content_type='text/html'))
            page.goto(url)
            page.wait_for_timeout(200)
            expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
            page.unroute(url)
            # Every external request is fulfilled locally; no employer GET or write.
            def serve(route, request, fixture=fixture):
                assert route.request.method == 'GET'
                route.fulfill(path=str(FIXTURES/fixture), content_type='text/html')
            page.route(url, serve)
            page.goto(url)
            page.locator('form').first.evaluate("e=>e.insertAdjacentHTML('beforeend','<label>Written open question<textarea id=t064-response></textarea></label>')")
            expect(page.locator('#t064-response + .autoapply-ask-ai')).to_have_count(1)
            if fixture == 't057-pinpoint.html':
                page.evaluate("history.pushState({},'', '/support')")
                expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
                page.evaluate('url=>history.pushState({},"",url)', url)
                expect(page.locator('#t064-response + .autoapply-ask-ai')).to_have_count(1)
            # The ATS host and application URL alone cannot qualify contact/support.
            page.locator('body').evaluate("e=>e.innerHTML='<main><h1>Support</h1><form><input type=email><textarea></textarea><input size=60><div contenteditable=true>Editor</div><button>Send</button></form></main>'")
            expect(page.locator('.autoapply-ask-ai')).to_have_count(0)
        assert all(path in {'/ext/run/status', '/ext/run/next'} for path, _, _ in requests)
    finally:
        page.close()


def test_t064_same_origin_application_frame_context(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    page = context.new_page()
    try:
        page.route('**/t064.html', lambda route: route.fulfill(path=str(FIXTURES/'t064.html'), content_type='text/html'))
        page.route('**/t064-frame.html', lambda route: route.fulfill(body='<form><label>Written open question<textarea id=framed></textarea></label></form>', content_type='text/html'))
        page.goto(server+'/t064.html')
        page.evaluate("showView('application')")
        page.locator('#application-form').evaluate("e=>e.insertAdjacentHTML('beforeend','<iframe title=\"Application questions\" src=/t064-frame.html></iframe><iframe title=\"Chat\" src=/t064-frame.html></iframe>')")
        application = page.frame_locator('iframe[title="Application questions"]')
        chat = page.frame_locator('iframe[title="Chat"]')
        expect(application.locator('.autoapply-ask-ai')).to_have_count(1)
        expect(chat.locator('.autoapply-ask-ai')).to_have_count(0)
        page.locator('#application-form h1').evaluate("e=>e.textContent='Contact us'")
        page.locator('#application-form [type=file]').evaluate('e=>e.remove()')
        page.locator('#application-form button[type=submit]').evaluate("e=>e.textContent='Send'")
        expect(application.locator('.autoapply-ask-ai')).to_have_count(0)
        page.locator('#application-form h1').evaluate("e=>e.textContent='Job application'")
        expect(application.locator('.autoapply-ask-ai')).to_have_count(1)
        assert all(path in {'/ext/run/status', '/ext/run/next'} for path, _, _ in requests)
    finally:
        page.close()


def test_t056_snapshot_collector_lifecycle():
    """Run the shipped collector with Chrome-shaped storage and passive-frame stubs."""
    import subprocess
    script = r'''
import assert from 'node:assert/strict';
import {snapshotCollector} from './extension/snapshots.js';
const local={}, session={};let sends=[],reads=0,live=true,url='https://fixture.test/apply',fail=false,onRead=()=>{},onStore=()=>{},identity='step1',doc='document1',large=false;
const area=data=>({get:async()=>structuredClone(data),set:async values=>{Object.assign(data,structuredClone(values));if(data===session)onStore();}});
globalThis.chrome={storage:{local:area(local),session:area(session)},tabs:{get:async()=>({url})},scripting:{executeScript:async request=>{
  reads++;onRead();assert.match(String(request.func),/unresolvedStructure/);assert.doesNotMatch(String(request.func),/\.scan\(/);
  return [{documentId:doc,result:request.args[0]===identity?{host:'fixture.test',nodes:large?Array.from({length:600},()=>({tag:'label',text:'x'.repeat(2000)})):[{tag:'legend',text:identity}],truncated:false}:null}];
}}};
const make=()=>({tabId:1,url:'https://fixture.test/apply',pageIdentities:{0:{identity,documentId:'document1'}},pending:[{key:'0:aa-1',label:'Unknown credential',kind:'radio',options:['Yes','No'],value:'PRIVATE'}],writing:[],summary:{filled:6,errors:[]}});
let reports=0;
const deps={current:()=>live,report:async()=>reports++,delay:async()=>{},api:async(path,payload)=>{assert.equal(path,'/ext/snapshot');sends.push(payload);if(fail)throw Error('offline');return {ok:true};}};
let collector=snapshotCollector(deps);
assert.equal((await collector.preference()).enabled,true);
const first=make();await collector.capture(first);const event=first.snapshotFeedback;assert.equal(event.sent,true);assert.ok(event.id);assert.equal(sends.length,1);assert.equal(reports,1);assert.ok(!JSON.stringify(sends).includes('PRIVATE'));
await Promise.all([collector.capture(make()),collector.capture(make())]);assert.equal(sends.length,1);
collector=snapshotCollector(deps);const replay=make();await collector.capture(replay);assert.equal(sends.length,1);assert.deepEqual(replay.snapshotFeedback,event); // restart
identity='step2';await collector.capture(make());assert.equal(sends.length,2); // same URL, changed step
let result=make();result.pending.push({key:'0:aa-2',label:'New unknown',kind:'text'});await collector.capture(result);assert.equal(sends.length,3);
await collector.preference(false);reads=0;await collector.capture(make());assert.equal(reads,0);assert.equal(sends.length,3);
collector=snapshotCollector(deps);assert.equal((await collector.preference()).enabled,false);await collector.preference(true);
for(const changes of [{stalePage:true},{busy:true},{skillsBusy:true},{coverBusy:true},{waiting:true},{sign_in:true},{error:'failed scan'},{pending:[]},{writing:[{key:'0:w',label:'Essay',kind:'textarea'}]}]) {
  await collector.capture(Object.assign(make(),changes));assert.equal(sends.length,3);
}
live=false;await collector.capture(make());live=true;
url='https://fixture.test/other';await collector.capture(make());url='https://fixture.test/apply';
doc='new-document';await collector.capture(make());doc='document1';
result=make();result.pageIdentities[0].identity='old-step';await collector.capture(result);assert.equal(sends.length,3);
identity='cancel-read';onRead=()=>{live=false;};await collector.capture(make());onRead=()=>{};live=true;assert.equal(sends.length,3);
identity='skip-read';result=make();onRead=()=>{result.pending=[];};await collector.capture(result);onRead=()=>{};assert.equal(sends.length,3);
identity='disable-save';onStore=()=>{local.autoSendStructure=false;};await collector.capture(make());onStore=()=>{};await collector.preference(true);assert.equal(sends.length,3);
identity='stale-save';onStore=()=>{identity='next-step';};await collector.capture(make());onStore=()=>{};assert.equal(sends.length,3);
identity='failure';fail=true;result=make();await collector.capture(result);assert.equal(sends.length,5);assert.equal(result.snapshotFeedback.sent,false);
await collector.capture(make());collector=snapshotCollector(deps);await collector.capture(make());assert.equal(sends.length,5);
fail=false;identity='writing-settled';result=make();result.writing=[{key:'0:w',label:'Essay',kind:'textarea'}];result.summary.errors=['writer failed'];
await collector.capture(result);assert.equal(sends.length,5);result.writingSettled=true;await collector.capture(result);assert.equal(sends.length,6);
for(let i=0;i<102;i++){identity='bounded'+i;await collector.capture(make());}assert.equal(session.structureCaptures.length,100);
assert.ok(session.structureCaptures.every(item=>Object.keys(item).sort().join(',')==='attempts,feedback,sent,signature'));
large=true;identity='large';result=make();result.pending=Array.from({length:50},(_,i)=>({key:'0:'+i,label:'\u754c'.repeat(300),kind:'select',options:Array(20).fill('\u754c'.repeat(200))}));
await collector.capture(result);const payload=sends.at(-1);assert.ok(payload.truncated);assert.ok(new TextEncoder().encode(JSON.stringify(payload)).length<1000000);
console.log('snapshot lifecycle assertions passed');
'''
    result = subprocess.run(['node', '--input-type=module'], input=script, cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_t056_actual_sidebar_autosend_and_controls(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.emulate_media(color_scheme=theme, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/t056.html*', lambda route: route.fulfill(path=str(FIXTURES/'t056.html'), content_type='text/html'))
    page.goto(server+'/t056.html')
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id', page.url)
    value = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert value['summary']['filled'] == 6
    assert len(value['pending']) == 2 and not value['writing']
    panel = next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    expect(panel.locator('#snapshot-status')).to_have_text('Sent')
    snapshots = lambda: [payload for path, _, payload in requests if path == '/ext/snapshot']
    assert len(snapshots()) == 1
    expect(panel.locator('#auto-snapshot-notice')).to_have_count(0)
    page.set_viewport_size(dict(width=360, height=600))
    (ROOT/'.tmp/review/t056').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ROOT/'.tmp/review/t056'/f'feedback-sent-360-{theme}.png'))
    assert panel.evaluate("()=>{const s=document.getElementById('snapshot-status').getBoundingClientRect(),b=document.getElementById('send-structure').getBoundingClientRect();return s.right<=b.left&&Math.abs(s.y+s.height/2-b.y-b.height/2)<2;}")
    # Repeated actual result broadcasts cannot restart the fade deadline.
    for _ in range(4):
        worker.evaluate('async id=>{const {tabResults}=await chrome.storage.session.get("tabResults");await chrome.runtime.sendMessage({type:"autofill-result",result:tabResults[id]});}', tab_id)
        page.wait_for_timeout(600)
    expect(panel.locator('#snapshot-status')).to_be_empty(timeout=1000)
    worker.evaluate('async id=>{const {tabResults}=await chrome.storage.session.get("tabResults");await chrome.runtime.sendMessage({type:"autofill-result",result:tabResults[id]});}', tab_id)
    expect(panel.locator('#snapshot-status')).to_be_empty()
    diagnostic = snapshots()[0]['diagnostic']
    assert {field['kind'] for field in diagnostic['fields']} == {'radio', 'checkbox'}
    assert next(field for field in diagnostic['fields'] if field['kind'] == 'radio')['options'] == ['Yes', 'No']
    assert 'Optional fixture note' not in json.dumps(diagnostic)
    assert 'Fixture Student' not in json.dumps(snapshots())
    expect(panel.locator('#autofill .fill-count')).to_have_text('6 fields filled')
    assert 'fields filled' not in panel.locator('#progress').inner_text()
    expect(panel.locator('#auto-send-structure')).to_be_checked()
    output = ROOT/'.tmp/review/t056'
    output.mkdir(parents=True, exist_ok=True)
    def shot(name):
        assert panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert panel.evaluate('''()=>['autofill','next-page','auto-advance','auto-send-structure','send-structure','settings'].every(id=>{
          const e=document.getElementById(id),b=e.getBoundingClientRect();return b.left>=0&&b.right<=innerWidth&&b.bottom<=innerHeight&&e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));})''')
        page.screenshot(path=str(output/f'{name}-{theme}.png'))
    for width in (360, 400):
        page.set_viewport_size(dict(width=width, height=600))
        expect(panel.locator('#tab-fill')).to_have_css('font-size', '14px')
        expect(panel.locator('#tab-fill')).to_have_css('height', '36px')
        assert panel.locator('.auto-toggle').inner_text() == ''
        shot(f'filled-{width}-off')
        if width == 360:
            panel.locator('#auto-send-structure').uncheck()
            shot('capture-360-off')
            panel.locator('#auto-send-structure').check()
            expect(panel.locator('#snapshot-status')).to_be_empty()
            shot('capture-360-on')
        panel.locator('#auto-advance').check()
        assert panel.evaluate('''()=>{const input=document.getElementById('auto-advance'),lock=document.querySelector('.auto-lock'),b=input.getBoundingClientRect(),l=lock.getBoundingClientRect();
          const knob=getComputedStyle(input,'::before');return Math.abs(l.x+l.width/2-(b.x+15+5))<.6&&Math.abs(l.y+l.height/2-(b.y+b.height/2))<.6&&knob.transform.includes('12');}''')
        shot(f'filled-{width}-on')
        panel.locator('#auto-advance').uncheck()
    # Closing the actual iframe leaves the worker responsible for diagnostics.
    panel.locator('#close').click()
    worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    panel = next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    expect(panel.locator('#snapshot-status')).to_be_empty()
    assert len(snapshots()) == 1
    page.locator('form').evaluate("e=>e.insertAdjacentHTML('beforeend','<label>New unknown personal fact <input required name=newfact></label>')")
    worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    expect(panel.locator('#snapshot-status')).to_have_text('Sent')
    assert len(snapshots()) == 2
    panel.locator('#auto-send-structure').uncheck()
    expect(panel.locator('#auto-send-structure')).to_be_enabled()
    panel.evaluate('location.reload()')
    expect(panel.locator('#auto-send-structure')).not_to_be_checked()
    page.locator('h1').evaluate("e=>e.textContent='Changed step'")
    worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert len(snapshots()) == 2
    # Explicit Analyze remains repeatable; automatic capture above remains passive.
    for _ in range(2):
        panel.locator('#send-structure').click()
        expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    assert len(snapshots()) == 4
    assert panel.evaluate("()=>{const s=document.getElementById('snapshot-status').getBoundingClientRect(),b=document.getElementById('send-structure').getBoundingClientRect();return s.right<=b.left;}")
    # Final-review preference stays checked, disabled, and manual-only.
    panel.locator('#auto-advance').check()
    value.update(next_action=dict(kind='submit'), writing=[], pending=[])
    worker.evaluate('value=>chrome.storage.session.set({tabResults:{[value.tabId]:value}})', value)
    expect(panel.locator('#auto-advance')).to_be_disabled()
    expect(panel.locator('#auto-advance')).to_be_checked()
    expect(panel.locator('#next-page')).to_have_text('Submit')
    shot('final-review-400')
    page.set_viewport_size(dict(width=360, height=600))
    shot('final-review-360')
    page.goto(server+'/t056.html?step=next')
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel = next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    expect(panel.locator('#autofill .fill-count')).to_have_count(0)
    # A new scan clears old totals immediately, with no duplicate progress count.
    worker.evaluate('value=>chrome.storage.session.set({tabResults:{[value.tabId]:value}})', dict(tabId=tab_id, url=page.url, busy=True))
    expect(panel.locator('#autofill')).to_have_text('Filling…')
    expect(panel.locator('#autofill .fill-count')).to_have_count(0)
    assert not errors


def test_t056_writing_settles_and_snapshot_failure_manual_retry(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.route('**/t056.html', lambda route: route.fulfill(path=str(FIXTURES/'t056.html'), content_type='text/html'))
    page.goto(server+'/t056.html')
    page.locator('form').evaluate("e=>e.insertAdjacentHTML('beforeend','<label>Why this role?<textarea required name=essay></textarea></label>')")
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id', page.url)
    fixture_delay(worker, hold=['write'])
    worker.evaluate('''()=>{globalThis.realFetch=fetch;globalThis.snapshotAttempts=0;globalThis.fetch=(url,...args)=>String(url).endsWith('/ext/snapshot')?(snapshotAttempts++,Promise.reject(new TypeError('fixture offline'))):realFetch(url,...args);}''')
    value = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert value['writing']
    panel = next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    assert worker.evaluate('snapshotAttempts') == 0
    expect(panel.locator('#autofill .fill-count')).to_have_count(0)
    panel.locator('#close').click()
    fixture_delay(worker, release=['write'])
    expect(page.locator('[name=essay]')).to_have_value('I enjoy building useful local tools.')
    expect(panel.locator('#snapshot-status')).to_have_text('Failed', timeout=15000)
    expect(panel.locator('#snapshot-status')).to_have_attribute('aria-label', 'Capture not saved. Try Analyze page.')
    assert worker.evaluate('snapshotAttempts') == 2
    expect(panel.locator('#snapshot-status')).to_have_text('Failed')
    expect(panel.locator('#autofill .fill-count')).to_have_text('7 fields filled')
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    # Auto off hides automatic feedback; enabling restores its actionable failure.
    panel.locator('#auto-send-structure').uncheck()
    expect(panel.locator('#snapshot-status')).to_be_empty()
    panel.locator('#auto-send-structure').check()
    expect(panel.locator('#snapshot-status')).to_have_text('Failed')
    panel.locator('#send-structure').click()
    expect(panel.locator('#error')).to_contain_text('Try Analyze page again.')
    worker.evaluate('async id=>{const {tabResults}=await chrome.storage.session.get("tabResults");await chrome.runtime.sendMessage({type:"autofill-result",result:tabResults[id]});}', tab_id)
    expect(panel.locator('#error')).to_contain_text('Try Analyze page again.')
    worker.evaluate('()=>{globalThis.fetch=realFetch;}')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    expect(panel.locator('#error')).to_be_empty()
    expect(panel.locator('#snapshot-status')).to_be_empty(timeout=4500)
    panel.evaluate('location.reload()')
    expect(panel.locator('#snapshot-status')).to_be_empty()
    assert len([1 for path, _, _ in requests if path == '/ext/snapshot']) == 1


@pytest.fixture
def extension_server(config, db):
    from autoapply.dashboard import Dashboard
    from autoapply.extension_api import snapshot
    analysis_dashboard = Dashboard(config, db)
    requests = []
    run = {"items": [], "index": 0}
    skill_aliases = {"ecmascript": "JavaScript"}
    gates = {name: threading.Event() for name in ("write", "cover", "chat")}
    for gate in gates.values():
        gate.set()
    canned = {
        "Full name": "Fixture Student", "Email": "student@example.test",
        "Phone": "2025550100", "About you": "I build local tools.",
        "Country": "united states", "Country": "United States", "Work authorization": "Yes",
        "Skills": ["Python", "JavaScript"], "Contact consent": True,
        "Existing answer": "Do not overwrite", "Short answer": "Too long",
        "Additional information": "Plain editable text", "City": "Boston",
        "Website": "https://example.test/", "Frame name": "Embedded Student",
        "School": "University of Maryland - College Park", "Degree": "bachelor",
        "Discipline": "computer science", "Existing selection": "Other",
        "Are you interested in a Spring Co-Op, Summer Internship, or Both?": "Summer Internship",
        "Nonmatching choice": "Yes", "Uncommitted choice": "Computer Science",
        "Start date month": "August", "Start date year": "2024",
        "End date month": "5", "End date year": "2028",
        "Job Title": "Fixture Engineer", "Company": "Fixture Employer",
        "How Did You Hear About Us?": ["LinkedIn"], "I have a preferred name": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def response(self, status, data):
            self.send_response(status)
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-AutoApply-Key")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_OPTIONS(self):
            if not self.headers.get("Origin", "").startswith("chrome-extension://"):
                self.response(403, {"error": "Extension origin required"})
            else:
                self.response(200, {})

        def do_GET(self):
            if self.path.startswith("/ext/"):
                self.ext(None)
                return
            file = {"/t048.html": "t048.html", "/t047.html": "t047.html", "/t046.html": "t046.html", "/greenhouse-education.html": "greenhouse_education.html", "/t044.html": "t044.html", "/t040.html": "t040.html", "/t039.html": "t039.html", "/workday-experience.html": "workday_experience.html", "/workday-robust.html": "workday_robust.html", "/sections.html": "sections.html", "/phone.html": "phone.html", "/form.html": "form.html", "/frame.html": "frame.html", "/greenhouse.html": "greenhouse.html",
                    "/async_search.html": "async_search.html", "/needs_input.html": "needs_input.html",
                    "/batch-one.html": "batch_form.html", "/batch-two.html": "batch_form.html", "/job.html": "batch_job.html", "/workday-job.html": "workday_job.html", "/workday-signin.html": "workday_signin.html", "/workday-spa.html": "workday_spa.html"}.get(self.path)
            if not file:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((FIXTURES / file).read_bytes())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            self.ext(payload)

        def ext(self, payload):
            key = self.headers.get("X-AutoApply-Key")
            requests.append((self.path, key, payload))
            if key != KEY:
                self.response(403, {"error": "Wrong fixture key"})
                return
            if self.headers.get("Origin") and not self.headers["Origin"].startswith("chrome-extension://"):
                self.response(403, {"error": "Wrong fixture origin"})
                return
            if self.path == "/ext/fixture-delay":
                for name in payload.get("hold", []):
                    gates[name].clear()
                for name in payload.get("release", []):
                    gates[name].set()
                run.update(auto_cover=payload.get("auto_cover", run.get("auto_cover", False)), t057_chat=payload.get("t057_chat",run.get("t057_chat",False)))
                self.response(200, {"ok": True})
            elif self.path == "/ext/fixture-start":
                base = f"http://127.0.0.1:{self.server.server_port}"
                run.update(items=[dict(application_id=1, run_id="fixture-run", state="pending", url=base + ("/workday-job.html" if payload.get("workday") else "/job.html"), company="Fixture", title="One", index=1, total=2),
                                  dict(application_id=2, run_id="fixture-run", state="pending", url=base + "/batch-two.html", company="Fixture", title="Two", index=2, total=2)], index=0)
                self.response(200, {"ok": True})
            elif self.path == "/ext/run/next":
                if any(item["state"] == "opened" for item in run["items"]):
                    self.response(200, {"wait": True})
                    return
                if run["index"] < len(run["items"]):
                    item = run["items"][run["index"]]
                    run["index"] += 1
                    item["state"] = "opened"
                    self.response(200, item)
                else:
                    self.response(200, {"done": True})
            elif self.path == '/ext/run/status':
                self.response(200, dict(items=run['items']))
            elif self.path in {'/ext/run/cancel','/ext/run/skip'}:
                for item in run['items']:
                    if item['state'] in {'pending','opened'} and (self.path.endswith('/cancel') or item['application_id']==payload.get('application_id')):
                        item['state']='skipped'
                if self.path.endswith('/cancel'):
                    run['index']=len(run['items'])
                self.response(200, dict(items=run['items']))
            elif self.path == '/ext/run/result':
                item=next((i for i in run['items'] if i['application_id']==payload['application_id']),None)
                if item and item['state']=='opened':
                    item.update(state='skipped' if payload['state']=='skipped' else 'opened', phase=payload['state'], detail=payload.get('detail',''))
                    self.response(200, dict(ok=True))
                else:
                    self.response(409, dict(error='Application is not open in this run'))
            elif self.path == '/ext/trace':
                self.response(200, {"ok": True, "filename": "fixture-trace.json"})
            elif self.path == "/ext/skills/aliases":
                self.response(200, {"aliases": skill_aliases})
            elif self.path == "/ext/skills/choose":
                choice = "Node.js" if payload["skill"] == "Node" else None
                if choice:
                    skill_aliases[payload["skill"].lower()] = choice
                self.response(200, {"choice": choice})
            elif self.path == "/ext/skills/plan":
                self.response(200, {"matched": ["TypeScript"], "missing": ["Kubernetes"], "other": ["Python", "Rust"]})
            elif self.path == "/ext/profile-sections":
                self.response(200, {"experience": [], "projects": [], "education": [], "skills": [], "links": {}})
            elif self.path == "/ext/ping":
                self.response(200, {"ok": True, "profile_name": "Fixture Student"})
            elif self.path == "/ext/match":
                self.response(200, {"application_id": 42, "company": "Fixture Company", "title": "Fixture Intern"})
            elif self.path == "/ext/resume":
                self.response(200, {"filename": "fixture-resume.pdf", "mime": "application/pdf", "base64": base64.b64encode(RESUME).decode()})
            elif self.path == "/ext/write":
                gates["write"].wait(15)
                answer = "Refined fixture answer." if payload.get("previous") else "I enjoy building useful local tools."
                self.response(200, {"answer": answer, "words": len(answer.split()), "chars": len(answer)})
            elif self.path == '/ext/snapshot' and payload.get('diagnostic', {}).get('reason') == 'page_analysis':
                self.response(200, snapshot(analysis_dashboard, payload))
            elif self.path in {"/ext/applied", "/ext/unapplied", "/ext/remember", "/ext/snapshot"}:
                self.response(200, {"ok": True})
            elif self.path == "/ext/chat":
                gates["chat"].wait(15)
                if run.get('t057_chat'):
                    from autoapply.extension_api import chat_replacement, chat_field_context
                    replacement = chat_replacement(payload['messages'],chat_field_context(payload.get('field_context')),
                        last_draft=payload.get('last_draft'), writer=lambda field,previous,instruction: 'A concise synthetic answer.')
                    if payload['messages'][-1]['text'] == 'ordinary action':
                        replacement=dict(reply='Unsafe',actions=[dict(type='replace_text',key=payload['field_context']['current_key'],text='bad')])
                    if payload['messages'][-1]['text'] == 'fake Submit':
                        replacement = dict(reply='Unsafe',actions=[dict(type='submit',key='letter',text='bad')])
                    if payload['messages'][-1]['text'] == 'update Cover letter to be more concise but mismatch' and replacement.get('actions'):
                        replacement['actions'][0]['text']='Unrelated unvalidated output'
                    self.response(200,replacement or dict(reply=('A grounded synthetic draft. '*150).strip()))
                else:
                    self.response(200, {"reply": "Fixture chat reply."})
            elif self.path == "/ext/cover-letter":
                gates["cover"].wait(15)
                if "answers" not in payload and not run.get("auto_cover"):
                    self.response(200, {"questions": ["Which project should the letter highlight?", "Anything else?"]})
                else:
                    self.response(200, {"filename": "fixture-cover.pdf", "mime": "application/pdf", "base64": base64.b64encode(RESUME).decode()})
            elif self.path == "/ext/answers":
                answers = []
                for field in payload["fields"]:
                    label = field["label"]
                    if payload.get('url', '').endswith('/t059-pinpoint-demographics.html') and label in {'Gender','Disability','Veteran Status','Ethnicity'}:
                        synthetic = {'Gender': 'Choose not to self-identify',
                                     'Disability': 'Yes, I have a disability, or have had one in the past',
                                     'Veteran Status': 'I am not a protected veteran'}
                        value = fit_options(synthetic[label], field['options']) if label in synthetic and field['options'] else None
                        status = 'RESOLVED' if value else 'OPTIONAL_SKIP' if label == 'Ethnicity' else 'NEEDS_INPUT'
                    elif field["kind"] == "file":
                        status, value = "FILE", "cover_letter" if "Cover" in label else "resume"
                    elif label == "Why this role?":
                        status, value = "NEEDS_WRITING", None
                    elif payload.get('url', '').endswith('/needs_input.html') and label in {'Start date month', 'Are you interested in a Spring Co-Op, Summer Internship, or Both?'}:
                        status, value = 'NEEDS_INPUT', None
                    elif label in canned:
                        status, value = "RESOLVED", canned[label]
                        if label == "Country":
                            value = fit_options(value, field["options"]) if field.get("options") else None
                            status = "RESOLVED" if value is not None else "NEEDS_INPUT"
                        if label == "Start date month" and field["kind"] == "number":
                            value = "8"
                    else:
                        status, value = ("NEEDS_INPUT" if field["required"] else "OPTIONAL_SKIP"), None
                    answers.append({"key": field["key"], "value": value, "source": "fixture", "status": status})
                self.response(200, {"answers": answers})
            else:
                self.response(404, {"error": "No fixture endpoint"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        for gate in gates.values():
            gate.set()
        server.shutdown()
        server.server_close()
        thread.join()
        analysis_dashboard.close()


@pytest.fixture
def loaded_extension(tmp_path):
    extension = ROOT / "extension"
    with sync_playwright() as playwright:
        # channel='chromium' selects full Chromium's new headless mode, not headless shell.
        try:
            context = playwright.chromium.launch_persistent_context(
                str(tmp_path / "chrome-profile"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                      "--disable-background-networking", "--disable-component-update"],
            )
        except Error as error:
            # Do not hide an extension-specific launch error behind a skip.
            launch_errors = []
            for channel in ("chromium", None):
                try:
                    probe = playwright.chromium.launch_persistent_context(
                        str(tmp_path / f"launch-probe-{channel or 'default'}"),
                        channel=channel, headless=True,
                        args=["--disable-background-networking", "--disable-component-update"],
                    )
                except Error as probe_error:
                    launch_errors.append(str(probe_error))
                else:
                    probe.close()
                    pytest.fail(f"Chromium launches, but loading the extension failed: {error}")
            pytest.skip("Chromium cannot launch at all: " + "\n".join(launch_errors))
        try:
            context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} or route.request.url.startswith("chrome-extension://") else route.abort())
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=15000)
            # A missing worker/invalid manifest is a failure, never a skip.
            assert worker.evaluate("""async () => {
                for (let attempt = 0; attempt < 500; attempt++) {
                    if (typeof globalThis.autoApplyAutofill === 'function') return true;
                    await new Promise(resolve => setTimeout(resolve, 10));
                }
                return false;
            }"""), "Background module failed to initialize"
            yield context, worker, worker.url.split("/")[2]
        finally:
            context.close()


def test_unpacked_extension_autofill(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/form.html")
    page.frame_locator("iframe:not([title='AutoApply application assistant'])").locator("#name").wait_for()
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result, result
    assert result["matched"]["application_id"] == 42
    summary = result["summary"]
    assert summary["filled"] == 13
    assert not result["busy"]
    assert set(summary["needs_input"]) == {"Unresolved", "Short answer"}
    assert set(summary["needs_writing"]) == {"Cover letter", "Why this role?"}
    assert summary["skipped"] == 2
    for selector, value in {"#name": "Fixture Student", "#email": "student@example.test", "#phone": "2025550100", "#about": "I build local tools.", "#country": "us", "#city": "Boston", "#website": "https://example.test/", "#existing": "User supplied"}.items():
        assert page.locator(selector).input_value() == value
    assert page.locator("#editor").inner_text() == "Plain editable text"
    assert page.frame_locator("iframe[title='Embedded form']").locator("#name").input_value() == "Embedded Student"
    for selector in ("#authorized", "#python", "#js", "#consent"):
        assert page.locator(selector).is_checked()
    assert not page.locator("#unauthorized").is_checked()
    assert not page.locator("#java").is_checked()
    assert page.locator("#resume").evaluate("e => [e.files.length, e.files[0].name, e.files[0].type]") == [1, "fixture-resume.pdf", "application/pdf"]
    assert page.locator("#resume").evaluate("async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))") == list(RESUME)
    for selector in ("#missing", "#cover", "#too-long"):
        assert page.locator(selector).evaluate("e => e.style.outline") == "rgb(153, 153, 153) solid 2px"
    assert page.locator("#name").evaluate("e => e.style.outline") == "rgb(136, 136, 136) solid 2px"
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]
    assert page.frames[1].evaluate("submissions") == 0
    events = page.evaluate("fieldEvents")
    assert all(["name", kind] in events for kind in ("input", "change", "blur"))
    answers_payload = next(payload for path, _, payload in requests if path == "/ext/answers")
    assert answers_payload["page"]["company"] == "Fixture Company"
    assert answers_payload["page"]["title"] == "Fixture Intern"
    assert answers_payload["page"]["description"] == "Build useful local tools."
    fields = answers_payload["fields"]
    assert len({field["key"] for field in fields}) == len(fields)
    assert not any("trap" in field["label"].lower() or "Search jobs" == field["label"] for field in fields)
    assert next(field for field in fields if field["label"] == "Skills")["options"] == ["Python", "JavaScript", "Java"]
    assert next(field for field in fields if field["label"] == "About you")["max_length"] == 200
    assert sum(path == "/ext/resume" for path, _, _ in requests) == 1
    assert all(key == KEY for _, key, _ in requests)
    assert all(path in {"/ext/trace", "/ext/profile-sections", "/ext/ping", "/ext/match", "/ext/answers", "/ext/resume", "/ext/write", "/ext/cover-letter", "/ext/run/status", "/ext/run/next"} for path, _, _ in requests)
    traces = [payload for path, _, payload in requests if path == '/ext/trace']
    assert any(trace['kind'] == 'autofill' for trace in traces)
    assert 'User supplied' not in json.dumps(traces)
    assert RESUME.decode() not in json.dumps(traces)
    expect(page.locator("#writing")).to_have_value("I enjoy building useful local tools.")

    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    expect(panel.locator("#connection")).to_have_attribute("aria-label", "Connected — Fixture Student")
    assert panel.locator("#company").inner_text() == "Fixture Company"
    assert panel.locator("#job").inner_text() == "Fixture Intern"
    expect(panel.locator("#autofill .fill-count")).to_have_text("14 fields filled")
    assert "Unresolved" in panel.locator("#needs-input").inner_text()
    expect(panel.locator("#cover-questions")).to_contain_text("Which project")
    # Refill through the actual runtime message path, with the fixture tab active.
    page.bring_to_front()
    second = panel.evaluate("() => chrome.runtime.sendMessage({type: 'autofill'})")
    assert "error" not in second, second
    assert second["summary"]["filled"] == 0
    assert page.locator("#existing").input_value() == "User supplied"
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]


def test_pairing_options_and_errors(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    options = context.new_page()
    options.goto(f"chrome-extension://{extension_id}/options.html")
    options.locator("#server").fill(server)
    options.locator("#key").fill(KEY)
    options.get_by_role("button", name="Save", exact=True).click()
    expect(options.locator("#status")).to_have_text("Pairing saved.")
    assert worker.evaluate("() => chrome.storage.local.get(['server', 'key'])") == {"server": server, "key": KEY}
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Connected — Fixture Student").wait_for()
    options.locator("#key").fill("wrong-key")
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Pairing key rejected").wait_for()
    worker.evaluate("() => chrome.storage.local.remove('key')")
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator("#connection").filter(has_text="Not paired").wait_for()
    # Unknown local path returns a real HTTP failure, rendered as a clear error.
    options.locator("#server").fill(server + "/missing")
    options.locator("#key").fill(KEY)
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Dashboard request failed (404)").wait_for()
    assert any(path == "/ext/ping" and key == KEY for path, key, _ in requests)


def test_greenhouse_comboboxes_hidden_upload_and_dates(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/greenhouse.html")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result, result
    assert result["summary"]["filled"] == 9
    assert set(result["summary"]["needs_input"]) == {"Nonmatching choice", "Uncommitted choice"}
    assert result["summary"]["needs_writing"] == ["Cover Letter"]
    for name, value in {"school": "University of Maryland - College Park", "degree": "Bachelor's Degree", "discipline": "Computer Science", "interest": "Summer Internship", "existing": "User supplied"}.items():
        root = page.locator(f'.select[data-id="{name}"]')
        assert root.locator('.select__single-value').inner_text() == value
        assert root.locator('[type=hidden]').input_value() == value
        assert root.locator('[role=combobox]').input_value() == ""
        assert root.locator('[role=combobox]').get_attribute('aria-expanded') == 'false'
    for name in ("invalid", "uncommitted"):
        assert page.locator(f'#{name}').input_value() == ""
        assert page.locator(f'[name="{name}"]').input_value() == ""
    assert page.get_by_role('listbox').count() == 0
    for name, value in {"start-month": "08", "start-year": "2024", "end-month": "05", "end-year": "2028"}.items():
        assert page.locator(f'#{name}').input_value() == value
    assert page.locator('#resume').evaluate('async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))') == list(RESUME)
    assert page.locator('.filename').first.inner_text() == 'fixture-resume.pdf'
    assert page.locator('#cover_letter').evaluate('e => e.files.length') == 0
    assert page.evaluate('uploadEvents') == [['resume', 'input'], ['resume', 'change']]
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    fields = {field['label']: field for field in result['fields']}
    # Resolved choices go directly to the filler without scan-time menu collection.
    assert result["timings"]["scan_ms"] < 1000
    assert result["timings"].get("options_ms", 0) == 0
    assert fields['School']['options'] == []
    assert fields['Degree']['options'] == []
    assert fields['Discipline']['options'] == []
    assert fields['Are you interested in a Spring Co-Op, Summer Internship, or Both?']['options'] == []
    assert all(field['options'] == [] for field in payload['fields'] if field['kind'] == 'combobox')
    assert all(fields[label]['section'] == 'Education' for label in ('Start date month', 'Start date year', 'End date month', 'End date year'))
    assert fields['Resume/CV']['kind'] == fields['Cover Letter']['kind'] == 'file'
    assert not any(field['label'] == 'Attach' for field in payload['fields'])
    assert 'University of Maryland - College Park' in page.evaluate('schoolSearches')
    second = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert second['summary']['filled'] == 0
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_async_school_retries_prefix_and_commits_exact_match(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/greenhouse.html')
    page.locator('.select[data-id="school"]').evaluate("e => e.dataset.prefixOnly = 'true'")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert 'School' not in result['summary']['needs_input']
    root = page.locator('.select[data-id="school"]')
    assert root.locator('.select__single-value').inner_text() == 'University of Maryland - College Park'
    assert root.locator('[type=hidden]').input_value() == 'University of Maryland - College Park'
    assert root.locator('[role=combobox]').input_value() == ''
    searches = page.evaluate('schoolSearches')
    assert searches.index('University of Maryland - College Park') < searches.index('University of Maryland')
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.parametrize('mode', ['search', 'prefix', 'failure'])
def test_async_school_searches_beyond_first_page(loaded_extension, extension_server, mode):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/async_search.html')
    if mode != 'search':
        page.locator('.select').evaluate("(e, mode) => e.dataset[mode === 'prefix' ? 'prefixOnly' : 'noResults'] = 'true'", mode)
    # Opening alone renders ten unrelated schools, never the wanted answer.
    page.locator('#school').press('ArrowDown')
    assert page.get_by_role('option').count() == 10
    assert page.evaluate('searchEvents') == []
    page.locator('#school').press('Escape')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    expected = 'University of Maryland - College Park'
    assert result['summary']['filled'] == (0 if mode == 'failure' else 1)
    assert result['summary']['needs_input'] == (['School'] if mode == 'failure' else [])
    assert page.locator('.select__single-value').inner_text() == ('' if mode == 'failure' else expected)
    assert page.locator('[name=school]').input_value() == ('' if mode == 'failure' else expected)
    assert page.locator('#school').input_value() == ''
    assert page.locator('#school').get_attribute('aria-expanded') == 'false'
    assert page.get_by_role('listbox').count() == 0
    searches = page.evaluate('searchEvents')
    assert searches[0] == {'term': expected, 'inputType': 'insertText', 'data': expected}
    if mode != 'search':
        assert searches[1]['term'] == 'University of Maryland'
    if mode == 'failure':
        assert searches[-1]['term'] == ''
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    assert result['fields'][0]['options'] == []  # Resolved search avoids opening the menu during scanning.
    assert payload['fields'][0]['options'] == []
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.parametrize('resume_label,resume_attributes', [
    ('<label for="attachment">Resume/CV *</label>', 'id="attachment"'),
    ('<div id="upload-label-resume">Resume/CV *</div>', 'id="attachment" aria-labelledby="upload-label-resume"'),
    ('<div id="description">Resume/CV *</div>', 'id="attachment" aria-describedby="description"'),
    ('<h3>Resume/CV *</h3>', 'id="attachment"'),
    ('', 'id="resume" aria-label="Attach"'),
    ('', 'id="attachment" name="cv"'),
])
def test_upload_field_labels_ignore_generic_buttons(loaded_extension, extension_server, resume_label, resume_attributes):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/greenhouse.html')
    page.evaluate("""markup => {
        document.querySelector('form').innerHTML = markup;
        for (const input of document.querySelectorAll('[type=file]')) {
            for (const type of ['input', 'change']) input.addEventListener(type, () => {
                uploadEvents.push([input.id, type]);
                setTimeout(() => input.parentElement.querySelector('.filename').textContent = input.files[0]?.name || '', 150);
            });
        }
    }""", f'''{resume_label}<div class="upload"><button type="button">Attach</button>
        <input {resume_attributes} type="file" accept=".pdf" hidden required><span class="filename"></span></div>
        <div class="upload"><button type="button">Choose</button>
        <input id="other" name="cover_letter" type="file" hidden><span class="filename"></span></div>''')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert result['summary']['filled'] == 1
    assert result['summary']['needs_input'] == []
    assert result['summary']['needs_writing'] == ['Cover Letter']
    assert page.locator('.filename').first.inner_text() == 'fixture-resume.pdf'
    assert page.locator('[type=file]').first.evaluate('e => e.files[0].name') == 'fixture-resume.pdf'
    assert page.locator('#other').evaluate('e => e.files.length') == 0
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    assert [field['label'] for field in payload['fields']] == ['Resume/CV' if resume_label else 'Resume/CV', 'Cover Letter']
    assert [event[1] for event in page.evaluate('uploadEvents')] == ['input', 'change']
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]



def test_sidepanel_answers_cover_helper_and_chat(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/form.html")
    page.evaluate("document.querySelector('form').insertAdjacentHTML('beforeend', '<label>Written open question<textarea id=helper-field required></textarea></label>')")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    page.locator("#autoapply-sidebar iframe").wait_for()
    panel = next(frame for frame in page.frames if frame.url.startswith(f"chrome-extension://{extension_id}/sidepanel.html"))
    assert panel.locator("#tab-ask").count() == 0
    assert panel.locator('#fill-section').get_by_role('button', name='Ask AI', exact=True).count() == 0
    page.locator('#helper-field + .autoapply-ask-ai').get_by_role('button', name='Ask AI', exact=True).click()
    expect(panel.locator("#question")).to_have_value("Written open question")
    panel.locator("#generate").click()
    expect(panel.locator("#version")).to_have_text("1 / 1")
    panel.locator("#fill-answer").click()
    expect(page.locator("#helper-field")).to_have_value("I enjoy building useful local tools.")
    panel.locator("#tab-answers").click()
    expect(panel.locator(".answer-card").filter(has_text="Written open question").locator(".source-pill")).to_have_text("AI")
    panel.locator("#tab-fill").click()
    panel.get_by_label("Unresolved", exact=True).fill("User fixture answer")
    panel.locator(".prompt").filter(has_text="Unresolved").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#missing")).to_have_value("User fixture answer")
    panel.get_by_label("Unresolved", exact=True).wait_for(state="detached")
    remembered = next(payload for path, _, payload in requests if path == "/ext/remember")
    assert remembered["label"] == "Unresolved"
    assert remembered["value"] == "User fixture answer"
    assert remembered["application_id"] == 42
    panel.get_by_label("Which project should the letter highlight?", exact=True).fill("A fixture robotics project")
    panel.get_by_role("button", name="Generate with answers", exact=True).click()
    page.wait_for_function("document.querySelector('#cover').files.length === 1")
    assert page.locator("#cover").evaluate("e => e.files[0].name") == "fixture-cover.pdf"
    panel.locator("#cover-questions").get_by_role("button").wait_for(state="detached")
    cover = [payload for path, _, payload in requests if path == "/ext/cover-letter"][-1]
    assert cover["answers"] == [{"question": "Which project should the letter highlight?", "answer": "A fixture robotics project"}, {"question": "Anything else?", "answer": ""}]
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-helper").click()
    panel.locator("#question").fill("Describe your robotics experience. Word limit 300")
    panel.locator("#generate").click()
    expect(panel.locator("#version")).to_have_text("1 / 1")
    first = panel.locator("#answer").input_value()
    panel.locator("#refinement").fill("Shorter")
    panel.locator("#refine").click()
    expect(panel.locator("#version")).to_have_text("2 / 2")
    assert panel.locator("#answer").input_value() == "Refined fixture answer."
    refine = [payload for path, _, payload in requests if path == "/ext/write"][-1]
    assert refine["previous"] == first
    assert refine["instruction"] == "Shorter"
    assert refine["use_job_description"] is True
    assert "description" in refine["page"] and refine["page"]["url"] == page.url
    panel.locator("#use-job-description").uncheck()
    panel.locator("#refine").click()
    expect(panel.locator("#version")).to_have_text("3 / 3")
    disabled = [payload for path, _, payload in requests if path == "/ext/write"][-1]
    assert disabled["use_job_description"] is False and "description" not in disabled["page"]
    panel.locator("#previous").click()
    panel.locator("#previous").click()
    assert panel.locator("#answer").input_value() == first
    panel.locator("#next").click()
    assert panel.locator("#answer").input_value() == "Refined fixture answer."
    panel.evaluate("location.reload()")
    panel.locator("#tab-chat").wait_for()
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-helper").click()
    panel.locator("#question").fill("Describe your robotics experience. Word limit 300")
    expect(panel.locator("#version")).to_have_text("2 / 3")
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-chat").click()
    panel.locator("#chat-input").fill("How can I improve this answer?")
    panel.locator("#chat-input").press("Enter")
    panel.locator("#messages").filter(has_text="Fixture chat reply.").wait_for()
    chat = next(payload for path, _, payload in requests if path == "/ext/chat")
    assert chat["messages"] == [{"role": "user", "text": "How can I improve this answer?"}]
    assert chat["page"]["company"] == "Fixture Company"
    assert chat["use_job_description"] is True and "description" in chat["page"]
    panel.locator("#use-job-description").uncheck()
    panel.locator("#chat-input").fill("Help without job context")
    panel.locator("#send").click()
    expect(panel.locator(".bubble.assistant:not(.typing)")).to_have_count(2)
    disabled_chat = [payload for path, _, payload in requests if path == "/ext/chat"][-1]
    assert disabled_chat["use_job_description"] is False and "description" not in disabled_chat["page"]
    expect(panel.locator(".bubble.user p").first).to_have_text("How can I improve this answer?")
    expect(panel.locator(".bubble.assistant p").first).to_have_text("Fixture chat reply.")
    assert "You:" not in panel.locator("#messages").inner_text()
    assert "AI:" not in panel.locator("#messages").inner_text()
    assert panel.locator(".bubble time").count() == 4
    panel.locator("#clear-chat").click()
    assert panel.locator("#messages").inner_text() == ""
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]


def test_skills_discovery_preserves_collected_options(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/needs_input.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("""async id => {
        const [frame] = await chrome.scripting.executeScript({target: {tabId: id}, func: async () => {
            const api = globalThis.autoApplyContent;
            const scanned = await api.scan();
            const field = scanned.fields.find(field => field.label.startsWith('Are you interested'));
            const collecting = api.collectOptions([field.key]);
            // Sidebar button discovery overlaps a menu whose options load later.
            const during = api.skillsAvailable();
            const collected = await collecting;
            const after = api.skillsAvailable();
            const filled = await api.fill([{key: field.key, status: 'NEEDS_INPUT'}]);
            return {collected: collected.fields[0].options,
                    during, after,
                    pending: filled.pending[0].options};
        }});
        return frame.result;
    }""", tab_id)
    expected = ['Spring Co-Op', 'Summer Internship', 'Both']
    assert result == dict(collected=expected, during=False, after=False, pending=expected)
    assert page.get_by_role('listbox').count() == 0


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_needs_input_choices_and_compact_actions(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/needs_input.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    interest = 'Are you interested in a Spring Co-Op, Summer Internship, or Both?'
    assert set(result['summary']['needs_input']) == {'Start date month', interest, 'Schedule', 'Areas'}
    assert result['summary']['skipped'] == 3
    fields = {field['label']: field for field in result['fields']}
    assert all(fields[label]['answered'] for label in ('Country', 'Preselected select', 'Preselected input'))
    assert page.get_by_role('listbox').count() == 0

    panel = context.new_page()
    panel.set_viewport_size({'width': 360, 'height': 900})
    panel.emulate_media(color_scheme=theme)
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#needs-input .prompt')).to_have_count(4)
    assert panel.locator('#needs-input input, #needs-input textarea').count() == 0
    assert 'Country' not in panel.locator('#needs-input').inner_text()
    choices = [
        ('Start date month', ['January', 'August'], 'August', '#month', '08'),
        (interest, ['Spring Co-Op', 'Summer Internship', 'Both'], 'Both', '[name=interest]', 'Both'),
        ('Schedule', ['Full time', 'Part time'], 'Part time', '#part-time', None),
        ('Areas', ['Hardware', 'Software'], ['Hardware', 'Software'], '#hardware', None),
    ]
    for label, options, chosen, selector, expected in choices:
        prompt = panel.locator('.prompt').filter(has=panel.get_by_label(label, exact=True))
        control = prompt.get_by_label(label, exact=True)
        assert control.evaluate('e => e.tagName') == 'SELECT'
        assert control.locator('option').all_text_contents() == (options if label == 'Areas' else ['Choose an answer', *options])
        save = prompt.get_by_role('button', name='Save', exact=True)
        skip = prompt.get_by_role('button', name='Skip', exact=True)
        save_box, skip_box = save.bounding_box(), skip.bounding_box()
        assert save_box['y'] == skip_box['y']
        assert skip_box['x'] >= save_box['x'] + save_box['width']
        assert save_box['width'] < 100 and skip_box['width'] < 100
        control.select_option(label=chosen)
        save.click()
        if expected is None:
            expect(page.locator(selector)).to_be_checked()
        else:
            expect(page.locator(selector)).to_have_value(expected)
        expect(control).to_have_count(0)
        remembered = [payload for path, _, payload in requests if path == '/ext/remember' and payload['label'] == label]
        assert len(remembered) == 1
        assert remembered[0]['value'] == chosen
        assert remembered[0]['application_id'] == 42
    assert page.locator('#software').is_checked()
    assert page.locator('#phone-country').input_value() == ''
    assert page.locator('.css-phone-singleValue').inner_text() == 'US +1'
    assert page.evaluate('optionClicks') == ['Both']
    assert page.locator('#interest').get_attribute('aria-expanded') == 'false'
    assert page.get_by_role('listbox').count() == 0
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_preselected_country_and_replaced_upload(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/greenhouse.html")
    page.locator('.select[data-id="existing"] label').evaluate("e => e.textContent = 'Phone country *'")
    page.locator('.select[data-id="existing"] .select__single-value').evaluate("e => e.textContent = 'US +1'")
    page.evaluate("""() => {
        document.querySelector('#resume').addEventListener('change', event => {
            const input = event.target, filename = input.files[0].name;
            setTimeout(() => {
                const container = input.closest('.upload');
                container.innerHTML = '<p>' + filename + '</p><button type="button">Remove</button>';
            }, 100);
        });
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result
    assert "Phone country" not in result["summary"]["needs_input"]
    assert result["summary"]["skipped"] >= 1
    assert result["summary"]["errors"] == []
    replaced_upload = page.locator('.upload').filter(has=page.get_by_role("button", name="Remove", exact=True))
    expect(replaced_upload).to_have_text("fixture-resume.pdfRemove")
    expect(replaced_upload.locator("p")).to_have_text("fixture-resume.pdf")
    expect(replaced_upload.get_by_role("button", name="Remove", exact=True)).to_be_enabled()
    expect(page.locator('#resume')).to_have_count(0)
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.get_by_role("button", name="Generate with answers", exact=True).click()
    page.wait_for_function("document.querySelector('#cover_letter').files.length === 1")
    expect(page.locator('.filename')).to_have_text('fixture-cover.pdf')
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_chrome_run_holds_review_until_close_or_skip(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused: true, chromeRun: {paused: true}})")
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    worker.evaluate("""async server => {
        await fetch(server + '/ext/fixture-start', {method:'POST', headers:{'X-AutoApply-Key':'fixture-pairing-key', 'Content-Type':'application/json'}, body:'{}'});
        await chrome.storage.session.set({chromeRunPaused:false});await autoApplyRunTick();
    }""", server)
    first = next(page for page in context.pages if page.url == server + '/batch-one.html')
    expect(first.locator('#name')).to_have_value('Fixture Student')
    first.locator('#name').fill('User edit')
    claims = sum(path == '/ext/run/next' for path, _, _ in requests)
    for _ in range(3):
        worker.evaluate('()=>autoApplyRunTick()')
    assert sum(path == '/ext/run/next' for path, _, _ in requests) == claims
    assert not any(page.url == server + '/batch-two.html' for page in context.pages)
    expect(first.locator('#name')).to_have_value('User edit')
    assert first.evaluate('[submitClicks,submissions]') == [0,0]
    first.close()
    worker.evaluate('()=>autoApplyRunTick()')
    worker.evaluate('()=>autoApplyRunTick()')
    second = next(page for page in context.pages if page.url == server + '/batch-two.html')
    expect(second.locator('#name')).to_have_value('Fixture Student')
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator('#run-skip').click()
    worker.evaluate('()=>autoApplyRunTick()')
    assert not any(path == '/ext/applied' for path, _, _ in requests)
    assert [payload['application_id'] for path, _, payload in requests if path == '/ext/run/result' and payload['state']=='skipped'] == [1]


@pytest.mark.parametrize("batch", [False, True])
def test_workday_entry_stops_at_sign_in(loaded_extension, extension_server, batch):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused: true})")
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    detail = "Sign in to Workday in this tab. Autofill will resume automatically."
    if batch:
        worker.evaluate("""async server => {
            await fetch(server + '/ext/fixture-start', {method:'POST', headers:{'X-AutoApply-Key':'fixture-pairing-key', 'Content-Type':'application/json'}, body:JSON.stringify({workday:true})});
            await chrome.storage.session.set({chromeRunPaused:false});
            await globalThis.autoApplyRunTick();
        }""", server)
        results = [payload for endpoint, _, payload in requests if endpoint == '/ext/run/result']
        assert results[-1]['state'] == 'needs_input'
        assert results[-1]['detail'] == detail
        page = next(page for page in context.pages if page.url == server + '/workday-signin.html')
    else:
        page = context.new_page()
        page.goto(server + '/workday-job.html')
        result = worker.evaluate("""async url => {
            const [tab] = await chrome.tabs.query({url});
            return globalThis.autoApplyAutofill(tab.id);
        }""", page.url)
        assert result['state'] == 'needs_input'
        assert result['detail'] == detail
        assert not result.get('error')
    expect(panel.locator('#progress')).to_have_text(detail)
    expect(page.locator('#email')).to_have_value('')
    expect(page.locator('#password')).to_have_value('')
    assert page.evaluate('credentialInputs') == 0
    assert page.evaluate('[sessionStorage.applyClicks,sessionStorage.manualClicks,sessionStorage.wrongEntry,sessionStorage.submissions]') == ['1', '1', None, None]
    assert not any(payload and payload.get('url') == server + '/workday-signin.html' for endpoint, _, payload in requests if endpoint == '/ext/answers')


def test_generic_sign_in_stops_before_profile_answers(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/workday-signin.html')
    page.evaluate("document.querySelectorAll('[data-automation-id]').forEach(el=>el.removeAttribute('data-automation-id'))")
    result = worker.evaluate("""async url => {
        const [tab] = await chrome.tabs.query({url});
        return globalThis.autoApplyAutofill(tab.id);
    }""", page.url)
    assert result['state'] == 'needs_input'
    assert not result.get('error')
    assert page.evaluate('credentialInputs') == 0
    assert not any(endpoint == '/ext/answers' for endpoint, _, _ in requests)


@pytest.mark.parametrize('run_tab', [False, True])
def test_workday_spa_refills_without_navigation(loaded_extension, extension_server, run_tab):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused:true, chromeRun:{error:'Not paired. Open Pairing settings and save your dashboard key.'}})")
    worker.evaluate("config => chrome.storage.local.set(config)", {'server': server, 'key': KEY})
    page = context.new_page()
    page.goto(server + '/workday-spa.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    match = {'application_id': 7, 'company': 'Run Company', 'title': 'Run Intern'} if run_tab else None
    result = worker.evaluate("async args => globalThis.autoApplyAutofill(args.id, args.match)", {'id': tab_id, 'match': match})
    assert not result.get('error'), result
    expect(page.locator('#name')).to_have_value('Fixture Student')
    expect(page.locator('#country')).to_have_text('United States')
    expect(page.locator('[data-automation-id=selectedItem]')).to_have_text('LinkedIn')
    expect(page.locator('#preferred')).to_be_checked()
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [0, 0, 0]
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#chrome-run-status')).not_to_contain_text('Not paired')
    # Only the user advances. Workday replaces fields while staying in the same tab.
    page.locator('#next').click()
    expect(page.locator('#job-title')).to_have_value('Fixture Engineer', timeout=20000)
    expect(page.locator('#employer')).to_have_value('Fixture Employer')
    expect(page.locator('#month')).to_have_value('8')
    expect(page.locator('#year')).to_have_value('2024')
    expect(page.locator('#writing')).to_have_value('I enjoy building useful local tools.')
    expect(page.locator('#filename')).to_have_text('fixture-resume.pdf')
    assert page.locator('#resume').evaluate('e => [e.files.length, e.files[0].name, e.files[0].type]') == [1, 'fixture-resume.pdf', 'application/pdf']
    assert page.locator('#resume').evaluate('async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))') == list(RESUME)
    expect(panel.locator('#autofill .fill-count')).to_contain_text('fields filled')
    expect(panel.locator('#company')).to_have_text('Run Company' if run_tab else 'Fixture Company')
    answers = [p for endpoint, _, p in requests if endpoint == '/ext/answers']
    assert {p['application_id'] for p in answers} == {7 if run_tab else 42}
    assert all(p['page']['company'] == ('Run Company' if run_tab else 'Fixture Company') for p in answers)
    assert sum(endpoint == '/ext/match' for endpoint, _, _ in requests) == (0 if run_tab else 1)
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [1, 0, 0]
    # Filling and opening menus must not cause a refill loop.
    page.wait_for_timeout(1200)
    assert len([p for endpoint, _, p in requests if endpoint == '/ext/answers']) == len(answers)


def test_workday_resumes_after_sign_in(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused:true})")
    worker.evaluate("config => chrome.storage.local.set(config)", {'server':server, 'key':KEY})
    page = context.new_page()
    page.goto(server + '/workday-signin.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert result['state'] == 'needs_input'
    assert page.evaluate('credentialInputs') == 0
    # Fixture simulates the successful sign-in redirect without any employer service.
    page.goto(server + '/workday-spa.html')
    expect(page.locator('#name')).to_have_value('Fixture Student', timeout=15000)
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [0, 0, 0]


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_sidebar_answers_learning_phone_and_manual_cover(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.emulate_media(color_scheme=theme)
    page.goto(server + '/phone.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert not any(path == '/ext/cover-letter' for path, _, _ in requests)
    assert page.locator('#autoapply-sidebar').evaluate('el => !!el.shadowRoot')
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.get_by_role('status', name='Connected — Fixture Student', exact=True)).to_be_visible()
    expect(panel.locator('#connection')).to_have_attribute('title', 'Connected — Fixture Student')
    expect(panel.locator('#connection')).to_be_empty()
    fields = [dict(field, key=field['key'].split(':', 1)[1]) for field in result['fields'] if field['key'].startswith('0:')]
    values = [dict(key=field['key'], status='RESOLVED', value='+1 301-401-9768', force=True) for field in fields if 'phone' in field['label'].lower()]
    worker.evaluate('async ({tabId, values}) => chrome.scripting.executeScript({target: {tabId}, func: values => autoApplyContent.fill(values), args: [values]})', dict(tabId=tab_id, values=values))
    for selector, value in {'#wd': '3014019768', '#gh': '3014019768', '#us': '(301) 401-9768', '#intl': '+1 301-401-9768', '#ext': ''}.items():
        expect(page.locator(selector)).to_have_value(value)
    expect(page.get_by_label('Country Phone Code')).to_have_value('us')
    page.locator('#us').fill('2025550100')
    page.locator('#us').press('Tab')
    expect(panel.locator('#saved-status')).to_have_text('Saved')
    assert any(path == '/ext/remember' and payload['value'] == '2025550100' for path, _, payload in requests)
    panel.locator('#tab-answers').click()
    # Editing replaces the displayed value; keep the card locator stable.
    card = panel.locator('.answer-card').filter(has=panel.locator('.field-label', has_text=re.compile(r'^Phone$')))
    expect(card.locator('.answer-value')).to_have_text('2025550100')
    assert card.bounding_box()['height'] <= 64
    assert card.locator('.source-pill').count() == 1
    assert card.get_by_role('button', name='Edit', exact=True).inner_text() == '✎'
    card.get_by_role('button', name='Edit', exact=True).click()
    expect(card).to_have_class(re.compile(r'editing'))
    card.get_by_role('button', name='Cancel', exact=True).click()
    expect(card.locator('.answer-value')).to_have_text('2025550100')
    card.get_by_role('button', name='Edit', exact=True).click()
    card.get_by_label('Phone', exact=True).fill('3015550100')
    card.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('#us')).to_have_value('3015550100')
    # The page fills before the asynchronous remember request finishes.
    expect(card.locator('.answer-value')).to_have_text('3015550100')
    assert any(path == '/ext/remember' and payload['value'] == '3015550100' for path, _, payload in requests)
    before = len([entry for entry in requests if entry[0] == '/ext/remember'])
    page.locator('#password').fill('fixture secret')
    page.locator('#password').press('Tab')
    page.locator('#essay').fill('My own essay')
    page.locator('#essay').press('Tab')
    expect(panel.locator('#completed-answers')).to_contain_text('My own essay')
    assert len([entry for entry in requests if entry[0] == '/ext/remember']) == before
    panel.locator('#tab-fill').click()
    panel.locator('#cover-letter').click()
    expect(panel.locator('#cover-questions')).to_contain_text('Which project')
    page.screenshot(path=str(ROOT / '.tmp' / ('t032-sidebar-' + theme + '.png')), animations='disabled')
    panel.locator('#minimize').click()
    expect(page.locator('#autoapply-sidebar .restore')).to_be_visible()
    page.locator('#autoapply-sidebar .restore').click()
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()
    panel.locator('#close').click()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    worker.evaluate("id => globalThis.autoApplySidebar(id, 'toggle')", tab_id)
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()


@pytest.mark.parametrize('scenario', ['confirmed', 'no_click', 'invalid'])
def test_detected_manual_submission(loaded_extension, extension_server, scenario):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/phone.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    # Fixture button has no submission handler; this records only a human click.
    if scenario != 'no_click':
        page.locator('#fixture-submit').click()
    page.locator('#confirmation').evaluate("(el, invalid) => el.innerHTML = 'Thank you for applying' + (invalid ? '<p role=alert>Required field error</p>' : '')", scenario == 'invalid')
    panel = page.frame_locator('#autoapply-sidebar iframe')
    if scenario == 'confirmed':
        expect(panel.locator('#applied-status')).to_be_visible()
        page.locator('#confirmation').evaluate("el => el.append(' ')" )
        page.wait_for_timeout(700)
        assert len([entry for entry in requests if entry[0] == '/ext/applied']) == 1
        panel.locator('#undo-applied').click()
        expect(panel.locator('#applied-status')).to_be_hidden()
        assert any(path == '/ext/unapplied' for path, _, _ in requests)
    else:
        page.wait_for_timeout(900)
        assert not any(path == '/ext/applied' for path, _, _ in requests)


def fixture_delay(worker, **payload):
    worker.evaluate("""async payload => {
        const {server, key} = await chrome.storage.local.get(['server', 'key']);
        const response = await fetch(server + '/ext/fixture-delay', {method:'POST',
            headers:{'Content-Type':'application/json', 'X-AutoApply-Key':key}, body:JSON.stringify(payload)});
        if (!response.ok) throw new Error('Fixture delay configuration failed');
    }""", payload)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chat_bubbles_typing_composer_and_scroll(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/phone.html")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    panel = context.new_page()
    panel.set_viewport_size({"width": 360, "height": 640})
    panel.emulate_media(color_scheme=theme)
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    panel.locator("#tab-chat").click()
    fixture_delay(worker, hold=["chat"])
    composer = panel.locator("#chat-input")
    composer.fill("First line")
    composer.press("Shift+Enter")
    composer.press("End")
    composer.type("Second line")
    assert "\n" in composer.input_value()
    composer.press("Enter")
    expect(panel.locator(".typing")).to_be_visible()
    expect(panel.locator(".bubble.user p")).to_contain_text("First line")
    assert composer.bounding_box()["height"] <= 40
    assert panel.locator("#send").bounding_box()["y"] == composer.bounding_box()["y"]
    fixture_delay(worker, release=["chat"])
    expect(panel.locator(".bubble.assistant p")).to_have_text("Fixture chat reply.")
    expect(panel.locator(".typing")).to_have_count(0)
    user, assistant = panel.locator(".bubble.user"), panel.locator(".bubble.assistant")
    assert user.bounding_box()["x"] > assistant.bounding_box()["x"]
    assert user.evaluate("e => getComputedStyle(e).backgroundColor") != assistant.evaluate("e => getComputedStyle(e).backgroundColor")
    assert user.evaluate("e => e.offsetWidth <= e.parentElement.clientWidth * .86")
    assert not re.search(r"You:|AI:", panel.locator("#messages").inner_text())
    for index in range(8):
        composer.fill("A longer fixture message to exercise scrolling. " * 3)
        composer.press("Enter")
        expect(panel.locator(".bubble.assistant")).to_have_count(index + 2)
        expect(panel.locator("#send")).to_be_enabled()
    panel.wait_for_function("() => {const e=document.querySelector('#messages');return e.scrollHeight > e.clientHeight && e.scrollTop + e.clientHeight >= e.scrollHeight - 2;}")
    panel.screenshot(path=str(ROOT / '.tmp' / ('t032-chat-' + theme + '.png')), animations='disabled')
    panel.locator("#clear-chat").click()
    expect(panel.locator(".bubble")).to_have_count(0)


def test_preselected_phone_flag_and_displayed_choice_without_options(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/phone.html")
    page.locator("form").evaluate('''form => form.insertAdjacentHTML('afterbegin',
        '<div class="intl-phone"><div class="country-selector"><button type="button" role="combobox" aria-label="Country" aria-required="true"><span class="flag" title="United States">🇺🇸</span></button></div><span>+1</span><input type="tel" aria-label="National phone"></div>' +
        '<div role="combobox" aria-label="Schedule choice" aria-required="true">Full time</div>')''')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    for label in ("Country", "Schedule choice"):
        field = next(field for field in result["fields"] if field["label"] == label)
        assert field["answered"] and field["options"] == []
        assert label not in result["summary"]["needs_input"]
    assert result["summary"]["skipped"] >= 2


@pytest.mark.parametrize("with_ai", [False, True])
def test_autofill_returns_before_delayed_writing_and_cover(loaded_extension, extension_server, with_ai):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    fixture_delay(worker, hold=["write", "cover"] if with_ai else ["cover"], auto_cover=True)
    page = context.new_page()
    page.goto(server + "/form.html")
    if not with_ai:
        page.locator("#writing").fill("My existing answer")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    assert not result.get("error") and not result["busy"]
    assert page.locator("#name").input_value() == "Fixture Student"
    assert page.locator("#resume").evaluate("e => e.files.length") == 1
    assert page.locator("#cover").evaluate("e => e.files.length") == 0
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    expect(panel.locator("#autofill")).to_be_enabled()
    if with_ai:
        expect(panel.locator("#writing-progress")).to_contain_text("Writing:")
        assert page.locator("#writing").input_value() == ""
        # Review and save a deterministic answer while the fake AI call is held.
        panel.get_by_label("Unresolved", exact=True).fill("Reviewed while writing")
        panel.locator(".prompt").filter(has_text="Unresolved").get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#missing")).to_have_value("Reviewed while writing")
        fixture_delay(worker, release=["write"])
        expect(page.locator("#writing")).to_have_value("I enjoy building useful local tools.")
        expect(panel.locator("#writing-progress")).to_be_empty()
        written = panel.locator('.answer-card').filter(has_text='Why this role?')
        expect(written.locator('.answer-value')).to_have_text('I enjoy building useful local tools.')
        expect(written.locator('.source-pill')).to_have_text('AI')
    expect(panel.locator("#cover-progress")).to_have_text("Writing cover letter...")
    expect(panel.locator("#cover-letter")).to_be_disabled()
    expect(panel.get_by_role('link', name='Download cover letter')).to_be_hidden()
    assert page.locator("#cover").evaluate("e => e.files.length") == 0
    fixture_delay(worker, release=["cover"])
    expect(page.locator("#cover")).to_have_value(re.compile(r"fixture-cover\.pdf$"))
    expect(panel.locator("#cover-progress")).to_be_empty()
    expect(panel.get_by_role('link', name='Download cover letter')).to_be_visible()
    expect(panel.locator("#cover-letter")).to_be_enabled()
    expect(panel.locator("#autofill .fill-count")).to_have_text(re.compile(r"^\d+ fields filled$"))
    assert page.locator("#cover").evaluate("e => e.files[0].name") == "fixture-cover.pdf"
    final = worker.evaluate("async id => (await chrome.storage.session.get('tabResults')).tabResults[id]", tab_id)
    assert final["summary"]["needs_writing"] == []
    assert not final["coverBusy"]
    assert final["coverFile"]["filename"] == "fixture-cover.pdf"
    assert any(field['kind'] == 'file' and field['value'] == 'fixture-cover.pdf' for field in final['completed'])
    if not with_ai:
        expect(page.locator('#writing')).to_have_value('My existing answer')
    if with_ai:
        assert any(field["value"] == "Reviewed while writing" for field in final["completed"])
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]


@pytest.mark.parametrize("date_controls", ["workday", "greenhouse"])
def test_repeatable_profile_sections(loaded_extension, extension_server, date_controls):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + "/sections.html")
    if date_controls == "greenhouse":
        page.evaluate("""() => {
            document.querySelector('#work button').textContent='Add another';
            // Also convert newly added month controls to native month selects.
            const convert = () => {for(const input of document.querySelectorAll('input[data-automation-id=dateSectionMonth]')) {
                const select=document.createElement('select');for(const attr of input.attributes) if(attr.name!=='type')select.setAttribute(attr.name,attr.value);
                select.innerHTML='<option value="">Month</option>'+Array.from({length:12},(_,i)=>'<option value="'+String(i+1).padStart(2,'0')+'">'+new Date(2024,i,1).toLocaleString('en-US',{month:'long'})+'</option>').join('');input.replaceWith(select);
            }};convert();new MutationObserver(convert).observe(document.querySelector('#work'),{childList:true,subtree:true});
        }""")
    profile = dict(
        experience=[dict(title="Engineer", company="Fixture", location="Boston", start_date="2024-08", end_date="", current=True, description="Built tools."),
                    dict(title="Freelancer", company="Self", start_date="", end_date="", current=True)],
        education=[dict(school="Fixture University", degree="Bachelor", major="Computing", start_date="", end_date="2028-05", current=False)],
        projects=[dict(title="Robot", organization="Builder", start_date="2025-03", end_date="2025-08", url="https://example.test/robot", technologies=["Python"], description="Built a robot. Tested it.")],
        skills=["Python", "JavaScript", "Rust"], links=dict(github="https://example.test/code", linkedin="https://example.test/me"))
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    prepared = worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: p => globalThis.autoApplyContent.prepareSections(p), args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    missing = [f for f in prepared['fields'] if f.get('section_missing_date')]
    assert [f['label'] for f in missing] == ['Freelancer at Self: start date', 'Fixture University: start date']
    answers = prepared['answers'] + [dict(key=f['key'], status='NEEDS_INPUT') for f in missing]
    summary = worker.evaluate("""async ({id, answers}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: a => globalThis.autoApplyContent.fill(a), args:[answers]}))[0].result""", dict(id=tab_id, answers=answers))
    assert summary['needs_input'] == ['Freelancer at Self: start date', 'Fixture University: start date']
    assert page.locator('#work .entry').count() == 2
    assert int(page.locator('#work .entry').first.locator('[data-automation-id=dateSectionMonth]').first.input_value()) == 8
    assert page.locator('#work .entry').first.locator('[data-automation-id=dateSectionYear]').first.input_value() == '2024'
    assert page.locator('#work .entry input[type=checkbox]').first.is_checked()
    assert not page.locator('#work .entry .end').first.is_visible()
    assert page.locator('#projects input[type=date]').first.input_value() == '2025-03-01'
    assert page.locator('#projects input[type=date]').last.input_value() == '2025-08-01'
    assert page.locator('#skill-text').input_value() == 'Python, JavaScript, Rust'
    assert page.locator('#tags').inner_text() == ''
    skills = worker.evaluate("""async ({id,skills}) => (await chrome.scripting.executeScript({target:{tabId:id},func:s=>globalThis.autoApplyContent.autofillSkills(s),args:[skills]}))[0].result""", dict(id=tab_id,skills=profile['skills']))
    assert skills['added'] == 2 and skills['skipped'] == 1
    assert page.locator('#tags').inner_text() == 'PythonJavaScript'
    assert 'Robot - Builder - Python: Built a robot.' in page.locator('#project-text textarea').input_value()
    assert page.locator('section').filter(has=page.locator('h2', has_text='Websites')).locator('input').count() == 2
    country = next(f for f in prepared['fields'] if f['label'] == 'Country')
    assert not country['answered']
    # A stable date question can be answered once and distributed to month/year controls.
    worker.evaluate("""async ({id, key}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: key => globalThis.autoApplyContent.fill([{key,status:'RESOLVED',value:'2023-02'}]),args:[key]}))[0].result""", dict(id=tab_id, key=missing[0]['key']))
    assert int(page.locator('#work .entry').last.locator('[data-automation-id=dateSectionMonth]').first.input_value()) == 2
    before = page.locator('form').evaluate("e => Array.from(e.querySelectorAll('input,textarea'),n=>[n.value,n.checked])")
    worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async p => {const r=await globalThis.autoApplyContent.prepareSections(p);return globalThis.autoApplyContent.fill(r.answers)},args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    assert page.locator('form').evaluate("e => Array.from(e.querySelectorAll('input,textarea'),n=>[n.value,n.checked])") == before
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


@pytest.mark.parametrize("matching_school", [True, False])
@pytest.mark.parametrize("anchor_control", ["combobox", "text"])
def test_greenhouse_partial_education_entry(loaded_extension, extension_server, matching_school, anchor_control):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + "/greenhouse.html")
    school = "  UNIVERSITY OF MARYLAND - COLLEGE PARK  " if matching_school else "User University"
    page.evaluate("""({school, anchor_control}) => {
        const section=document.querySelector('section'), entry=document.createElement('div');
        for(const child of Array.from(section.children)) if(child.tagName!=='H2') entry.append(child);
        section.append(entry);
        for (const [id, value] of [['school', school], ['degree', "Bachelor's Degree"], ['discipline', 'Computer Science']]) {
            const root=document.querySelector('[data-id="'+id+'"]');
            root.querySelector('.select__single-value').textContent=value;
            root.querySelector('[type=hidden]').value=value;
        }
        if(anchor_control==='text') {
            document.querySelector('[data-id=school]').innerHTML='<label for="school">School</label><input id="school">';
            document.querySelector('#school').value=school;
        }
        document.querySelector('#start-month').add(new Option('January', '01'));
        document.querySelector('#end-month').value='05';
        document.querySelector('#end-year').value='2028';
    }""", dict(school=school, anchor_control=anchor_control))
    snapshot = "e => [Array.from(e.querySelectorAll('input,select'), n => [n.value, n.checked]), Array.from(e.querySelectorAll('.select__single-value'), n => n.textContent)]"
    before = page.locator('section').first.evaluate(snapshot)
    profile = dict(education=[dict(school="University of Maryland - College Park", degree="Master's Degree",
                                  major="Mechanical Engineering", start_date="2024-01", end_date="2029-06")])
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    prepared = worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: p => globalThis.autoApplyContent.prepareSections(p), args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    assert page.locator('#start-month').input_value() == ('01' if matching_school else '')
    assert page.locator('#start-month option:checked').inner_text() == ('January' if matching_school else 'Month')
    assert page.locator('#start-year').input_value() == ('2024' if matching_school else '')
    assert page.locator('#end-month').input_value() == '05'
    assert page.locator('#end-year').input_value() == '2028'
    assert page.locator('[data-id=degree] .select__single-value').inner_text() == "Bachelor's Degree"
    assert page.locator('[data-id=discipline] .select__single-value').inner_text() == 'Computer Science'
    assert next(s for s in prepared['sections'] if s['name']=='education')['added'] == int(matching_school)
    assert [(f['label'], f['value']) for f in prepared['completed']] == (
        [('Start date month', 'January'), ('Start date year', '2024')] if matching_school else [])
    if matching_school:
        assert all(f['answered'] for f in prepared['fields'] if f['label'].startswith('Start date'))
        assert page.locator('#school').evaluate("e => e.value || e.closest('.select').querySelector('[type=hidden]').value") == school
    else:
        assert page.locator('section').first.evaluate(snapshot) == before
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_saved_source_labels(loaded_extension):
    context, worker, extension_id = loaded_extension
    panel = context.new_page()
    worker.evaluate("""() => chrome.storage.session.set({lastResult:{tabId:999,page:{title:'Fixture'},
        completed:['profile:identity','USER_PROVIDED','verified_memory','verified_fuzzy_memory','AI','you'].map((source,i)=>({key:String(i),label:'Fact '+i,kind:'text',value:'Fixture',source})),
        pending:[],writing:[],summary:{filled:6,skipped:0,errors:[]},sections:[{name:'experience',added:2,skipped:0}]}})""")
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    expect(panel.locator('.source-pill')).to_have_text(['Profile','Saved','Saved','Saved (similar)','AI','You'])
    assert panel.locator('#section-results').text_content() == ''
    expect(panel.locator('#autofill .fill-count')).to_have_text('6 fields filled')
    assert panel.locator('#answer-section-results').text_content() == 'Experience: 2 added'


def test_existing_resume_entry_and_displayed_choice(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/sections.html')
    page.evaluate("""() => {
        const fields=document.querySelectorAll('#work .entry input');fields[0].value='User title';fields[1].value='User employer';
        const choice=document.createElement('div');choice.className='select__control';choice.innerHTML='<input role="combobox" aria-label="Other choice" required><span class="selection">Selected fact</span>';document.querySelector('form').append(choice);
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    result = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
        const api=globalThis.autoApplyContent;
        const prepared=await api.prepareSections({experience:[{title:'Profile title',company:'Profile employer',start_date:'2024-08',current:true},{title:'Second',company:'Second employer',start_date:'2022-01',current:false}],projects:[],education:[],skills:[],links:{}});
        await api.fill(prepared.answers);return prepared;
    }}))[0].result""", tab_id)
    assert page.locator('#work .entry').first.locator('input').first.input_value() == 'User title'
    assert page.locator('#work .entry').first.locator('input').nth(1).input_value() == 'User employer'
    assert page.locator('#work .entry').first.locator('input').nth(3).is_checked() is False
    assert page.locator('#work .entry').last.locator('input').first.input_value() == 'Second'
    assert next(f for f in result['fields'] if f['label']=='Other choice')['answered'] is True
    assert page.evaluate('clicks') == dict(next=0,submit=0,submissions=0)


@pytest.mark.parametrize("collect_first", [False, True])
def test_greenhouse_empty_click_only_phone_country(loaded_extension, extension_server, collect_first):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/sections.html")
    country = page.locator("#country")
    country.focus()
    country.press("ArrowDown")
    assert page.locator("#country-options [role=option]").count() == 0
    page.locator(".select__control").dispatch_event("mousedown")
    assert page.locator("#country-options [role=option]").count() == 0
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    if collect_first:
        collected = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},
            func: async () => {const api=globalThis.autoApplyContent;const scan=await api.scan();
                const country=scan.fields.find(f=>f.label==='Country');
                return {initial:country, collected:await api.collectOptions([country.key])};}
        }))[0].result""", tab_id)
        assert not collected['initial']['answered'] and not collected['initial']['required']
        field = collected['collected']['fields'][0]
        assert not field['answered']  # Visible dial codes in the popup are not a selection.
        assert len(field['options']) == 244
        assert field['options'][:2] == ['United States +1', 'Afghanistan +93']
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    country_requests = [field for endpoint, _, payload in requests if endpoint == "/ext/answers"
                        for field in payload["fields"] if field["label"] == "Country" and field.get("options")]
    assert country_requests
    assert len(country_requests[-1]["options"]) == 244
    assert fit_options("United States", country_requests[-1]["options"]) == "United States +1"
    expect(page.locator(".select__single-value")).to_have_text("+1")
    assert country.input_value() == '' and country.get_attribute('aria-expanded') == 'false'
    assert any(f['label'] == 'Country' for f in result['completed'])
    assert 'Country' not in result['summary']['needs_input']
    assert not any(f['label'] == 'Country' for f in result['pending'])
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    expect(panel.locator('.prompt').filter(has_text=re.compile(r'^Country'))).to_have_count(0)
    again = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    assert next(f for f in again['fields'] if f['label'] == 'Country')['answered']
    assert 'Country' not in again['summary']['needs_input']
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


@pytest.mark.parametrize("displayed, selected, closed, placeholder_gone, committed", [
    ("+1", False, False, False, True),
    ("", True, False, False, True),
    ("🇺🇸", False, True, True, True),
    ("United Kingdom +44", True, True, True, False),
    ("+44", True, True, True, False),
])
def test_greenhouse_phone_country_commit_signals(
    loaded_extension, extension_server, displayed, selected, closed, placeholder_gone, committed,
):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + "/sections.html")
    page.evaluate("""config => {
        document.querySelector('#country-options').addEventListener('click', event => {
            document.querySelector('.select__single-value').textContent = config.displayed;
            event.target.setAttribute('aria-selected', String(config.selected));
            document.querySelector('#country').setAttribute('aria-expanded', String(!config.closed));
            if (!config.placeholder_gone) {
                const placeholder = document.createElement('div');
                placeholder.className = 'select__placeholder';
                document.querySelector('.select__value-container').append(placeholder);
            }
        });
    }""", dict(displayed=displayed, selected=selected, closed=closed, placeholder_gone=placeholder_gone))
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    summary = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async () => {
            const api = globalThis.autoApplyContent;
            const country = (await api.scan()).fields.find(f => f.label === 'Country');
            return api.fill([{key:country.key, status:'RESOLVED', value:'United States', source:'profile_rule'}]);
        }
    }))[0].result""", tab_id)
    assert summary['filled'] == int(committed)
    assert summary['needs_input'] == ([] if committed else ['Country'])
    assert [f['label'] for f in summary['completed']] == (['Country'] if committed else [])
    assert page.locator('#country').input_value() == ''
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


def robust_fixture(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/workday-robust.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    return context, worker, extension_id, page, tab_id, requests


def test_workday_real_labels_conditional_options_and_attached_resume(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, requests = robust_fixture(loaded_extension, extension_server)
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert not result.get('error'), result
    expect(page.locator('input[aria-labelledby=real-question]')).to_have_value('Fixture Student')
    expect(page.locator('#email')).to_have_value('student@example.test')
    choice = next(f for f in result['pending'] if f['label']=='Lazy choice')
    assert choice['options'] == ['Yes', 'No']
    assert not any('secondaryQuestionnaire--' in f['label'] for f in result['fields'])
    assert any(f['value']=='existing-resume.pdf' for f in result['completed'])
    assert not any(endpoint=='/ext/resume' for endpoint, _, _ in requests)
    assert page.evaluate('clicks') == {'next':0,'submit':0,'uploads':0}
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    assert not panel.locator('#auto-advance').is_checked()
    panel.locator('#next-page').click()
    expect(page.locator('#submit')).to_have_text('Submit')
    expect(panel.locator('#next-page')).to_have_text('Submit')
    assert page.evaluate('clicks.submit') == 0
    panel.locator('#next-page').click()
    page.wait_for_function('clicks.submit === 1',timeout=5000)


def test_workday_load_options_retry(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate('optionsEnabled=false')
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert next(f for f in result['pending'] if f['label']=='Lazy choice')['options'] == []
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    page.evaluate('optionsEnabled=true')
    panel.get_by_role('button',name='Load options').click()
    expect(panel.locator('#needs-input select option')).to_have_text(['Choose an answer','Yes','No'])


@pytest.mark.parametrize('cancel,sign_in', [(False,True),(False,False),(True,True)])
def test_delayed_form_poll_and_cancellation(loaded_extension, extension_server, cancel, sign_in):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const main=document.querySelector('#main');
        main.innerHTML='<div data-automation-id="signInContent">Sign in<input type="password"></div>';
        setTimeout(()=>{main.innerHTML='<form><label>Full name *<input id="delayed" required></label></form>';},2500);
    }""")
    if not sign_in:
        page.evaluate("document.querySelector('#main').innerHTML=''")
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert result['waiting'] and not result['busy']
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    if cancel:
        panel.locator('#cancel-form-wait').click()
        page.wait_for_timeout(4500)
        expect(page.locator('#delayed')).to_have_value('')
        expect(panel.locator('#progress')).to_have_text('Form waiting cancelled.')
    else:
        expect(page.locator('#delayed')).to_have_value('Fixture Student',timeout=12000)


def test_delayed_form_poll_recovers_after_scanner_transition(loaded_extension, extension_server):
    context, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const main = document.querySelector('#main');
        main.innerHTML = '';
        setTimeout(() => {main.innerHTML = '<form><label>Full name *<input id="delayed" required></label></form>';}, 2500);
    }""")
    worker.evaluate("""async id => chrome.scripting.executeScript({target:{tabId:id}, func:() => {
        const api = globalThis.autoApplyContent;
        delete globalThis.autoApplyContent;
        setTimeout(() => {globalThis.autoApplyContent = api;}, 500);
    }})""", tab_id)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert not result.get('error'), result
    assert result['waiting'] and not result['busy']
    worker.evaluate("""async id => chrome.scripting.executeScript({target:{tabId:id}, func:() => {
        const api = globalThis.autoApplyContent, scan = api.scan;
        api.scan = () => {throw Error('Temporary document transition');};
        setTimeout(() => {api.scan = scan;}, 3500);
    }})""", tab_id)
    expect(page.locator('#delayed')).to_have_value('Fixture Student', timeout=12000)
    assert page.evaluate('clicks.submit') == 0


@pytest.mark.parametrize('validation', [False, True])
def test_auto_advance_stops_at_submit_or_validation(loaded_extension, extension_server, validation):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("validation=>{window.validation=validation;document.querySelector('#lazy').required=false;document.querySelector('#lazy').value='Yes';}", validation)
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#auto-advance').check()
    worker.evaluate("""async () => {for(let n=0;n<100;n++){const {autoAdvanceProfiles={}}=await chrome.storage.local.get('autoAdvanceProfiles');if(Object.values(autoAdvanceProfiles).includes(true))return;await new Promise(r=>setTimeout(r,20));}throw Error('Preference not saved');}""")
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)",tab_id)
    assert not result.get('error'), result
    assert page.evaluate('clicks.next') == 1
    assert page.evaluate('clicks.submit') == 0
    if validation:
        assert any('correct the required' in error for error in result['summary']['errors'])
    else:
        expect(page.locator('#submit')).to_have_text('Submit')
    panel.reload()
    expect(panel.locator('#auto-advance')).to_be_checked()


@pytest.mark.parametrize('caption,accessible', [('Submit',''),('Submit Application',''),('Apply',''),('Apply Now',''),('Next','Submit Application'),('Next','Apply now')])
def test_next_page_rejects_final_controls(loaded_extension, extension_server, caption, accessible):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("args=>{const next=document.querySelector('#next');next.textContent=args.caption;if(args.accessible)next.setAttribute('aria-label',args.accessible);}", {'caption':caption,'accessible':accessible})
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.nextPage()}))[0].result""",tab_id)
    assert response['clicked'] is False
    assert page.evaluate('clicks') == {'next':0,'submit':0,'uploads':0}


@pytest.mark.parametrize('attribute,identity', [
    ('data-automation-id','finalSubmit'), ('data-automation-id','submitApplicationButton'),
    ('data-automation-id','applyNowButton'), ('id','finalSubmit'),
    ('aria-labelledby','final-action-label'),
])
def test_next_page_rejects_final_semantic_identity(loaded_extension, extension_server, attribute, identity):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate('''({attribute,identity})=>{
        const next=document.querySelector('#next');
        if(attribute==='aria-labelledby'){
            const label=document.createElement('span');label.id=identity;label.textContent='Submit Application';
            label.hidden=true;document.body.append(label);
        }
        next.setAttribute(attribute,identity);
    }''',dict(attribute=attribute,identity=identity))
    response=worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},
        func:()=>autoApplyContent.nextPage()}))[0].result''',tab_id)
    assert response['clicked'] is False
    assert page.evaluate('clicks') == {'next':0,'submit':0,'uploads':0}


def test_next_page_allows_workday_footer_with_stepper_label(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate('''()=>{
        const next=document.querySelector('#next');next.textContent='Save and Continue';
        next.setAttribute('data-automation-id','pageFooterNextButton');
        next.setAttribute('aria-label','completed step 1 of 7 My Information completed step 2 of 7 My Experience current step 3 of 7 Application Questions step 4 of 7 Voluntary Disclosures step 5 of 7 Self Identify step 6 of 7 Review');
        next.setAttribute('name','jobApplication');
    }''')
    response=worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},
        func:()=>autoApplyContent.nextPage()}))[0].result''',tab_id)
    assert response['clicked'] is True
    assert page.evaluate('clicks') == {'next':1,'submit':0,'uploads':0}


def test_workday_preceding_text_replaces_identifier_label(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const input=document.querySelector('[aria-labelledby=real-question]');
        input.setAttribute('aria-labelledby','fake-label');
        const fake=document.createElement('span');fake.id='fake-label';fake.textContent='secondaryQuestionnaire__abcdef0123456789';fake.hidden=true;document.body.append(fake);
        document.querySelector('#real-question').removeAttribute('data-automation-id');
    }""")
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.scan()}))[0].result""",tab_id)
    assert response['fields'][0]['label'] == 'Full name'


def test_greenhouse_filename_and_remove_skip_resume(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const region=document.querySelector('[data-automation-id=formField-resume]');
        region.removeAttribute('data-automation-id');region.className='upload';region.querySelector('div').innerHTML='old-cv.docx <button type="button">Remove</button>';
    }""")
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const api=globalThis.autoApplyContent;const {fields}=await api.scan();const field=fields.find(f=>f.kind==='file');
        const filled=await api.fill([{key:field.key,status:'FILE',value:'resume',force:true}],{resume:{filename:'replacement.pdf',mime:'application/pdf',base64:'YWJj'}});
        return {field,filled};}}))[0].result""",tab_id)
    assert response['field']['answered'] is True
    assert page.evaluate('clicks.uploads') == 0


def test_workday_experience_education_and_languages(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/workday-experience.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    profile = dict(experience=[dict(title='Freelancer', company='Self', start_date='2019', current=True),
                               dict(title='Crew', company='Moge Tee', start_date='2026-06', end_date='2026-08', current=False)],
                   education=[dict(school='Fixture University', degree="Bachelor's", major='Computer Science',
                                   start_date='2024-08', end_date='2028-05', gpa='3.8')],
                   languages=[dict(language='English', fluent=True, proficiency='highest')])
    invoke = """async ({id,profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async p => {const api=globalThis.autoApplyContent;const result=await api.prepareSections(p);
                         await api.fill(result.answers);return result;},args:[profile]}))[0].result"""
    prepared = worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    work = page.locator('#work .entry')
    assert work.count() == 2
    assert work.first.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '01'
    assert work.first.locator('[data-automation-id=dateSectionYear-input]').first.input_value() == '2019'
    assert work.first.locator('[type=checkbox]').is_checked()
    assert work.first.locator('.end input').first.input_value() == ''
    assert not work.first.locator('.end').is_visible()
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '06'
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').last.input_value() == '08'
    assert not work.last.locator('[type=checkbox]').is_checked()
    order = page.evaluate('order')
    adds = [i for i, event in enumerate(order) if event == 'add:work']
    assert any(event == 'fill:work' for event in order[adds[0]+1:adds[1]])
    assert '2019' in page.evaluate('commits')
    assert page.locator('#education .entry').count() == 1
    assert page.get_by_label('Degree', exact=True).input_value() == "Bachelor's Degree"
    assert page.get_by_label('Field of Study', exact=True).input_value() == 'Computer Science'
    assert page.get_by_label('To', exact=True).input_value() == '05/2028'
    assert page.get_by_label('GPA', exact=True).input_value() == '3.8'
    assert page.locator('#languages .entry').count() == 1
    assert page.get_by_label('Language', exact=True).input_value() == 'English'
    assert page.get_by_label('I am fluent in this language', exact=True).is_checked()
    for name in ('Comprehension', 'Overall', 'Reading', 'Speaking', 'Writing', 'Listening'):
        assert page.get_by_label(name, exact=True).input_value() == '5 - Fluent'
    assert next(f for f in prepared['fields'] if f['label']=='When are you expected to graduate?')['placeholder'] == 'MMM-YY'
    assert prepared['completed']
    before = page.evaluate('order.length')
    worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    assert page.evaluate('order.length') == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


def test_workday_degree_discipline_and_word_proficiency(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/workday-experience.html')
    page.locator('#education button').click()
    page.locator('#languages button').click()
    page.evaluate("""() => {
        const degree=document.querySelector('#education select');
        degree.innerHTML='<option value="">Select one</option><option>Computer Science</option><option>Math</option>';
        for(const select of document.querySelectorAll('#languages select')) if(select.getAttribute('aria-label')!=='Language')
            select.innerHTML='<option value="">Select one</option><option>Basic</option><option>Advanced</option><option>Fluent</option><option>Native</option>';
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
        await globalThis.autoApplyContent.prepareSections({education:[{school:'Fixture',degree:"Bachelor's",major:'Computer Science'}],
            languages:[{language:'English',fluent:true,proficiency:'highest'}]});
    }}))[0].result""", tab_id)
    assert page.get_by_label('Degree', exact=True).input_value() == 'Computer Science'
    for name in ('Comprehension', 'Overall', 'Reading', 'Speaking', 'Writing', 'Listening'):
        assert page.get_by_label(name, exact=True).input_value() == 'Native'
    for option in ('Bachelors', 'BS', 'B.S.', 'Bachelor of Science'):
        page.get_by_label('Degree', exact=True).evaluate("(e,option)=>{e.innerHTML='<option value="">Select one</option>';const o=new Option(option,option);e.add(o);}", option)
        summary = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
            const api=globalThis.autoApplyContent;const field=(await api.scan()).fields.find(f=>f.label.startsWith('Degree') && f.kind==='select');
            return api.fill([{key:field.key,status:'RESOLVED',value:"Bachelor's"}]);
        }}))[0].result""", tab_id)
        assert summary and summary.get('filled') == 1, summary
        assert page.get_by_label('Degree', exact=True).input_value() == option
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


def test_t039_labels_rules_navigation_and_snapshot(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    worker.evaluate("config=>chrome.storage.local.set(config)", {"server":server,"key":KEY})
    page.goto(server + '/t039.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    response = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const api=globalThis.autoApplyContent, scanned=await api.scan();
        const filled=await api.fill(scanned.fields.filter(f=>f.label.includes('Address')||f.label==='Suite'||f.label==='Phone Type').map(f=>({key:f.key,status:'RESOLVED',value:f.label==='Phone Type'?'Mobile':'Never insert'})));
        return {scanned,filled,snapshot:api.pageStructure(),next:await api.nextPage()};
    }}))[0].result""", tab_id)
    labels = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const variants=['Select One Required','select','Choose','Please Select','Required','Optional','*','Search','-- select --'];
        const result=[];
        for(const value of variants){
            document.querySelector('[aria-haspopup=listbox]').setAttribute('aria-label',value);
            document.querySelector('#major').setAttribute('aria-label',value);
            document.querySelector('fieldset').setAttribute('aria-label',value);
            result.push((await globalThis.autoApplyContent.scan()).fields.map(f=>f.label));
        }
        return result;
    }}))[0].result""", tab_id)
    assert all('Phone Device Type' in rows and 'Your major' in rows and 'Previously employed?' in rows for rows in labels)
    fields = {f['label']: f for f in response['scanned']['fields']}
    assert {'Phone Device Type', 'Previously employed?', 'Tell us why', 'Address line2', 'Suite', 'Address 2', 'Your major'} <= fields.keys()
    assert fields['Phone Device Type']['required']
    assert fields['Previously employed?']['options'] == ['Yes', 'No']
    assert response['filled']['needs_input'] == ['Address 2']
    assert page.locator('#address').input_value() == ''
    assert page.locator('#suite').input_value() == ''
    assert page.locator('#phone-type').input_value() == 'Cell'
    assert response['next']['clicked'] and page.evaluate('nextClicks') == 1
    serialized = json.dumps(response['snapshot'])
    assert 'SECRET_VALUE' not in serialized and 'SECRET_FILE' not in serialized
    assert 'Phone Device Type' in serialized and 'formField-phoneType' in serialized
    final = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.nextPage()}))[0].result""", tab_id)
    assert final['message'] == 'Final submit is yours'
    assert page.evaluate('submitClicks') == 0
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    worker.evaluate("""async id=>chrome.storage.session.set({tabResults:{[id]:{tabId:id,busy:true,waiting:true,fields:[],pending:[],writing:[],page:{},summary:{filled:0,skipped:0,errors:[]}}}})""", tab_id)
    expect(panel.locator('#autofill')).to_be_disabled()
    for name in ('fill','answers','chat'):
        panel.locator('#tab-'+name).click()
        expect(panel.locator('#next-page')).to_be_visible()
        expect(panel.locator('#next-page')).to_be_enabled()
        assert panel.locator('#next-page').bounding_box()['y'] < panel.viewport_size['height']
    panel.locator('#next-page').click()
    expect(panel.locator('#progress')).to_have_text('Final submit is yours')
    page.locator('#submit').evaluate('e=>e.remove()')
    panel.locator('#next-page').click()
    expect(panel.locator('#progress')).to_have_text('No Next page button found.')
    assert page.evaluate('submitClicks') == 0
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'minimized')",tab_id)
    flag = page.locator('#autoapply-sidebar').get_by_role('button',name='Restore AutoApply sidebar')
    box=flag.bounding_box()
    page.mouse.move(box['x']+5,box['y']+5)
    page.mouse.down()
    page.mouse.move(box['x']+5,box['y']+105,steps=5)
    page.mouse.up()
    assert flag.bounding_box()['y'] > box['y']+50
    saved=worker.evaluate("async()=> (await chrome.storage.local.get('flagPosition:127.0.0.1'))['flagPosition:127.0.0.1']")
    assert saved >= 0
    page.reload()
    expect(flag).to_be_visible()
    assert abs(flag.bounding_box()['y']-saved) < 2
    expect(page.locator('#large + .autoapply-ask-ai')).to_have_count(1)
    writing = page.locator('#essay + .autoapply-ask-ai')
    expect(writing).to_have_count(1)
    page.locator('#essay').evaluate('e=>e.remove()')
    expect(writing).to_have_count(0)


@pytest.mark.parametrize("initial", ["none", "empty", "user"])
def test_t040_real_workday_repeatable_sections(loaded_extension, extension_server, initial):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    if initial != 'none':
        page.evaluate('initialEntry()')
    if initial == 'user':
        page.locator('#work [data-automation-id=formField-jobTitle] input').fill('User role')
        page.locator('#work [data-automation-id=formField-companyName] input').fill('User employer')
    profile = dict(experience=[dict(title='Freelance Software Developer', company='Self', start_date='2019', current=True),
                               dict(title='Engineer', company='Fixture', start_date='2025-02', end_date='2026-03')],
                   education=[dict(school='Fixture University', degree='Bachelor of Science', major='Computer Science',
                                   start_date='2024', end_date='2028-05', gpa='3.5', current=True)],
                   skills=['Python', 'JavaScript', 'Missing Skill'])
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    invoke = """async ({id,profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async profile => {const api=globalThis.autoApplyContent;const prepared=await api.prepareSections(profile);
            await api.fill(prepared.answers);prepared.skills=await api.autofillSkills(profile.skills);return prepared;},args:[profile]}))[0].result"""
    prepared = worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    work = page.locator('#work .entry')
    assert work.count() == 2
    assert work.first.locator('[data-automation-id=formField-jobTitle] input').input_value() == ('User role' if initial=='user' else 'Freelance Software Developer')
    if initial != 'user':
        assert work.first.locator('[type=checkbox]').is_checked()
        assert work.first.locator('.end input').first.is_disabled()
        assert not work.first.locator('.end').is_visible()
        assert work.first.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '01'
        assert work.first.locator('[data-automation-id=dateSectionYear-input]').first.input_value() == '2019'
        assert 'current:click' in page.evaluate('order')
    assert work.last.locator('[data-automation-id=formField-jobTitle] input').input_value() == 'Engineer'
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '02'
    assert work.last.locator('[data-automation-id=dateSectionYear-input]').last.input_value() == '2026'
    assert page.locator('#education .entry').count() == 1
    assert page.locator('[data-automation-id=formField-firstYearAttended] input').input_value() == '2024'
    assert page.locator('[data-automation-id=formField-lastYearAttended] input').input_value() == '2028'
    assert '2024' in page.evaluate('commits')
    assert page.evaluate("dateKeys.every(key=>/^[0-9]$/.test(key))")
    assert page.evaluate('dateKeys.length') >= 20
    assert page.locator('[data-automation-id=formField-fieldOfStudy] [data-automation-id=selectedItem]').inner_text() == 'Computer Science'
    assert page.locator('#skills [data-automation-id=selectedItem]').all_text_contents() == ['Python', 'JavaScript']
    # A settled popup without a matching option is skipped after one search.
    assert page.evaluate('searches') == ['Computer Science', 'Python', 'JavaScript', 'Missing Skill']
    assert all(',' not in value for value in page.evaluate('searches'))
    assert prepared['skills']['added'] == 2 and prepared['skills']['skipped'] == 1
    assert next(s for s in prepared['sections'] if s['name']=='skills')['message'] == 'Skills: run Autofill skills'
    experience = next(s for s in prepared['sections'] if s['name']=='experience')
    assert experience['added'] == (1 if initial=='user' else 2)
    assert experience['total'] == 2
    assert next(s for s in prepared['sections'] if s['name']=='education')['added'] == 1
    order = page.evaluate('order')
    adds = [i for i,event in enumerate(order) if event=='add:work']
    assert len(adds) == (2 if initial=='none' else 1)
    if initial=='none':
        assert 'fill:work' in order[adds[0]+1:adds[1]]
    # React may remove completed entries' old Add button; re-query for Add Another.
    assert page.locator('#work [data-automation-id=add-button]').inner_text() == 'Add Another'
    before = page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input'),n=>[n.value,n.checked])")
    worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    assert work.count() == 2
    assert page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input'),n=>[n.value,n.checked])") == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)
    panel = context.new_page()
    worker.evaluate("sections=>chrome.storage.session.set({lastResult:{tabId:999,page:{title:'Fixture'},completed:[],pending:[],writing:[],summary:{filled:0,skipped:0,errors:[]},sections}})", prepared['sections'])
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#section-results')).to_be_empty()
    expect(panel.locator('#answer-section-results')).to_have_text(f"Experience {experience['added']}/2 · Education 1/1 · Skills: run Autofill skills")


def test_t040_inferred_current_and_extra_user_entries(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        await globalThis.autoApplyContent.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'}]});
    }}))[0].result""", tab_id)
    assert page.locator('#work [type=checkbox]').is_checked()
    assert page.locator('#work .end input').first.is_disabled()
    page.evaluate("initialEntry();initialEntry()")
    page.locator('#work .entry').last.locator('[data-automation-id=formField-jobTitle] input').fill('Extra user entry')
    before = page.evaluate('order.length')
    worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        await globalThis.autoApplyContent.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'}]});
    }}))[0].result""", tab_id)
    assert page.locator('#work .entry').count() == 3
    assert page.locator('#work .entry').last.locator('[data-automation-id=formField-jobTitle] input').input_value() == 'Extra user entry'
    assert page.evaluate('order.length') == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


@pytest.mark.parametrize("mode", ["delayed_add", "rejected_date", "portal_prompt"])
def test_t040_waits_verifies_and_handles_prompt_portals(loaded_extension, extension_server, mode):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    if mode == 'delayed_add':
        page.evaluate("() => {document.querySelector('#work button').onclick=()=>setTimeout(initialEntry,1800);}")
    elif mode == 'rejected_date':
        page.evaluate("() => {initialEntry();document.querySelector('#work [data-automation-id=dateSectionYear-input]').oninput=e=>e.target.value='';}")
    else:
        page.evaluate("""() => {
            const input=document.querySelector('#skills input'), list=document.querySelector('#skills .results');
            document.body.append(list);
            input.closest('[data-automation-id=formField-skills]').querySelector('label').textContent='Technology prompt';
        }""")
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    result = worker.evaluate("""async ({id,mode})=>(await chrome.scripting.executeScript({target:{tabId:id},func:async mode=>{
        const api=globalThis.autoApplyContent;
        if(mode==='portal_prompt') {
            const field=(await api.scan()).fields.find(f=>f.label==='Technology prompt');
            return {field,filled:await api.fill([{key:field.key,status:'RESOLVED',value:['Python','JavaScript']}])};
        }
        return api.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'},
            ...(mode==='rejected_date'?[{title:'Second',company:'Other',start_date:'2020',end_date:'2022'}]:[])]});
    },args:[mode]}))[0].result""", dict(id=tab_id, mode=mode))
    if mode == 'portal_prompt':
        assert result['field']['kind'] == 'combobox'
        assert result['field']['multiple'] is True
        assert result['filled']['filled'] == 0
        assert page.locator('#skills [data-automation-id=selectedItem]').count() == 0
    else:
        assert page.locator('#work .entry').count() == 1
        section = next(s for s in result['sections'] if s['name']=='experience')
        assert section['added'] == (0 if mode=='rejected_date' else 1)
        assert page.locator('#work [data-automation-id=dateSectionYear-input]').first.input_value() == ('' if mode=='rejected_date' else '2024')
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


@pytest.mark.parametrize('control,style,commits', [
    ('combobox', 'full', True), ('combobox', 'short', True),
    ('combobox', 'number', True), ('combobox', 'padded', True),
    ('select', 'short', True), ('select', 'number', True),
    ('workday', 'padded', True), ('combobox', 'full', False),
])
def test_greenhouse_education_container_after_phone_legend(loaded_extension, extension_server, control, style, commits):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/greenhouse-education.html')
    page.evaluate("""({control, style, commits}) => {
        for (const root of document.querySelectorAll('[data-click-only]')) {
            const values = JSON.parse(root.dataset.options).map((name, i) =>
                style === 'short' ? name.slice(0, 3) : style === 'number' ? String(i + 1) :
                style === 'padded' ? String(i + 1).padStart(2, '0') : name);
            root.dataset.options = JSON.stringify(values);
            if (!commits) root.dataset.noCommit = 'true';
            if (control === 'select') {
                root.innerHTML = '<label for="' + root.dataset.id + '">' + root.dataset.label + '</label>';
                const select = document.createElement('select'); select.id = root.dataset.id;
                select.add(new Option('Month', ''));
                for (const value of values) select.add(new Option(value, value));
                root.append(select);
            } else if (control === 'workday') {
                root.innerHTML = '';
                const button = document.createElement('button'); button.type = 'button';
                button.id = root.dataset.id; button.textContent = 'Select one';
                button.setAttribute('aria-label', root.dataset.label);
                button.setAttribute('aria-haspopup', 'listbox'); button.setAttribute('aria-expanded', 'false');
                root.append(button);
                button.onclick = () => {
                    const list = document.createElement('div'); list.setAttribute('role', 'listbox');
                    for (const value of values) {
                        const option = document.createElement('div'); option.setAttribute('role', 'option');
                        option.textContent = value; option.onclick = () => {
                            button.textContent = value; button.setAttribute('aria-expanded', 'false'); list.remove();
                        }; list.append(option);
                    }
                    button.setAttribute('aria-expanded', 'true'); root.append(list);
                };
            }
        }
    }""", dict(control=control, style=style, commits=commits))
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    profile = dict(education=[dict(school='Fixture University', degree="Bachelor's Degree",
                                  major='Computer Science', start_date='2024-01', end_date='2028-05')])
    prepared = worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: p => globalThis.autoApplyContent.prepareSections(p), args:[profile]}))[0].result""",
        dict(id=tab_id, profile=profile))
    fields = {f['label']: f for f in prepared['fields']}
    assert fields['Phone number']['section'] == 'Phone'
    assert fields['Other question']['section'] == 'Application questions'
    for label in ('School', 'Degree', 'Discipline', 'Start date month', 'Start date year', 'End date month', 'End date year'):
        assert fields[label]['section'] == 'Education'
        assert fields[label]['section_managed'] is True
        assert fields[label]['answered'] is (commits or 'month' not in label)
    month_names = {'full': ('January', 'May'), 'short': ('Jan', 'May'),
                   'number': ('1', '5'), 'padded': ('01', '05')}[style]
    for name, value in (('school', 'Fixture University'), ('degree', "Bachelor's Degree"),
                        ('discipline', 'Computer Science'), ('start-month', month_names[0]), ('end-month', month_names[1])):
        if 'month' in name and control == 'select':
            assert page.locator(f'#{name}--0').input_value() == value
        elif 'month' in name and control == 'workday':
            assert page.locator(f'#{name}--0').inner_text() == value
        else:
            expected = value if commits or 'month' not in name else ''
            assert page.locator(f'[data-id="{name}--0"] .select__single-value').inner_text() == expected
            assert page.locator(f'[name="{name}--0"]').input_value() == expected
        if 'month' in name and control == 'combobox':
            assert page.evaluate('id => monthMenuClicks[id]', name + '--0') > 0
    assert page.locator('#start-year--0').input_value() == '2024'
    assert page.locator('#end-year--0').input_value() == '2028'
    assert next(s for s in prepared['sections'] if s['name'] == 'education')['added'] == int(commits)
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.parametrize('preference,index', [('Yes', 0), ('No', 1), ('decline', 2)])
def test_t044_self_identify_and_hidden_current(loaded_extension, extension_server, preference, index):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t044.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    result = worker.evaluate("""async ({id,preference})=>(await chrome.scripting.executeScript({target:{tabId:id},func:async preference=>{
        const api=globalThis.autoApplyContent;
        return api.prepareSections({identity:{full_name:'Fixture Student'},application_preferences:{disability:preference},
            experience:[{title:'Engineer',company:'Fixture',start_date:'2024',current:true}]});
    },args:[preference]}))[0].result""", dict(id=tab_id, preference=preference))
    assert result['errors'] == []
    assert page.locator('#current').is_checked()
    assert page.locator('.end input').is_disabled()
    assert page.locator('#name').input_value() == 'Fixture Student'
    today = page.evaluate("() => {const d=new Date();return [String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0'),String(d.getFullYear())]}")
    assert page.locator('#signature input').evaluate_all('els=>els.map(e=>e.value)') == today
    assert page.locator('#employee').input_value() == ''
    assert page.locator('#language').input_value() == 'en'
    assert page.locator('#choices input').evaluate_all('els=>els.map(e=>e.checked)') == [i == index for i in range(3)]
    assert page.evaluate('submissions') == 0


def test_t044_skills_action_buttons_progress_and_cancel(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server,key=KEY))
    page = context.new_page()
    page.goto(server + '/t040.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#skills-menu > summary').click()
    expect(panel.locator('#autofill-skills')).to_be_visible()
    assert panel.locator('#footer-autofill-skills').count() == 0
    result = worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const api=globalThis.autoApplyContent;
        await api.prepareSections({skills:['Python','JavaScript']});
        if(document.querySelector('[data-automation-id=selectedItem]')) throw Error('Main autofill filled skills');
        const progress=[];const timer=setInterval(()=>progress.push(api.skillsStatus().progress),25);
        const result=await api.autofillSkills(['Python','JavaScript','Missing Skill']);clearInterval(timer);
        const again=await api.autofillSkills(['Python','JavaScript']);
        const pending=api.autofillSkills(['Missing Skill','Python']);setTimeout(()=>api.cancelSkills(),100);
        return {result,again,progress,cancelled:await pending};
    }}))[0].result""", tab_id)
    assert result['result']['added'] == 2 and result['result']['skipped'] == 1
    assert result['again']['added'] == 0
    assert any('Python - added' in row for row in result['progress'])
    assert result['cancelled']['progress'].startswith('Cancelled.')
    assert page.locator('#skills [data-automation-id=selectedItem]').all_text_contents() == ['Python','JavaScript']
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


def test_t046_document_popup_skill_engine(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server, key=KEY))
    page = context.new_page()
    page.goto(server + '/t046.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    def fill(skills):
        return worker.evaluate("""async args=>(await chrome.scripting.executeScript({target:{tabId:args.id},
            func:skills=>globalThis.autoApplyContent.autofillSkills(skills),args:[args.skills]}))[0].result""", dict(id=tab_id, skills=skills))
    result = fill(['TypeScript', 'ECMAScript', 'Node', 'Unknown', 'OCaml', 'Linux/Unix'])
    assert result['added'] == 3 and result['skipped'] == 1
    assert result['items']['ECMAScript'] == 'alias used'
    assert result['items']['Node'] == 'AI picked'
    assert page.evaluate('searches') == ['TypeScript', 'ECMAScript', 'Node', 'Unknown']
    assert len(page.locator('[data-automation-id=selectedItem]').all_text_contents()) == 8
    assert page.evaluate('selectionEvents.slice(0,4)') == ['pointerdown','mousedown','pointerup','mouseup']
    assert not any(path == '/ext/skills/choose' and data['skill'] == 'TypeScript' for path, _, data in requests)
    page.reload()
    again = fill(['Node', 'JS/TS'])
    assert again['items']['Node'] == 'alias used'
    assert page.evaluate('searches') == ['Node', 'JS/TS', 'JavaScript', 'TypeScript']
    assert page.locator('[data-automation-id=selectedItem]').all_text_contents()[-3:] == ['Node.js','JavaScript','TypeScript']
    choices = [data for path, _, data in requests if path == '/ext/skills/choose' and data['skill'] == 'Node']
    assert len(choices) == 1


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_t046_skill_panel_controls(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server,key=KEY))
    page = context.new_page()
    page.goto(server + '/t046.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    panel = context.new_page()
    panel.set_viewport_size(dict(width=360,height=800))
    panel.emulate_media(color_scheme=theme)
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#skills-menu > summary').click()
    expect(panel.locator('#matched-skills')).to_contain_text('TypeScript')
    panel.get_by_role('button',name='Include Kubernetes',exact=True).click()
    expect(panel.locator('#matched-skills')).to_contain_text('Kubernetes')
    panel.get_by_role('button',name='Remove Kubernetes',exact=True).click()
    panel.locator('#other-skills summary').click()
    panel.get_by_label('Add all',exact=True).check()
    expect(panel.locator('#matched-skills')).to_contain_text('Python')
    expect(panel.locator('#matched-skills')).to_contain_text('Rust')
    panel.locator('#autofill-skills').click()
    expect(panel.locator('#skills-progress')).to_contain_text('3 added',timeout=20000)
    assert set(page.locator('[data-automation-id=selectedItem]').all_text_contents()[-3:]) == {'TypeScript','Python','Rust'}
    assert panel.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_t046_uncommitted_option_is_skipped_without_retry(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server,key=KEY))
    page=context.new_page()
    page.goto(server+'/t046.html')
    page.evaluate("document.addEventListener('click',event=>{if(event.target.closest('#popup'))event.stopImmediatePropagation();},true)")
    tab_id=worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id",page.url)
    result=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},
        func:()=>globalThis.autoApplyContent.autofillSkills(['TypeScript'])}))[0].result""",tab_id)
    assert result['added']==0 and result['skipped']==1
    assert page.evaluate('searches')==['TypeScript']
    assert page.locator('[data-automation-id=selectedItem]').count()==5


def test_t047_virtual_skills_education_and_trace(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server, key=KEY))
    page = context.new_page()
    page.goto(server + '/t047.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    profile = dict(education=[dict(school='University of Maryland - College Park', degree="Bachelor's",
                                  major='Computer Science', gpa='3.5', start_date='2024', end_date='2028')])
    result = worker.evaluate("""async ({id,profile})=>(await chrome.scripting.executeScript({target:{tabId:id},
        func:async profile=>{const api=globalThis.autoApplyContent;await api.prepareSections(profile);
            return api.autofillSkills(['Java']);},args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    assert result['added'] == 1 and result['skipped'] == 0
    assert page.locator('[data-automation-id=formField-schoolName] input').input_value() == profile['education'][0]['school']
    assert page.locator('#education-8--degree').inner_text() in ['Bachelors', "Bachelor's Degree"]
    assert page.locator('[data-automation-id=formField-fieldOfStudy] [data-automation-id=selectedItem]').inner_text() == 'Computer Science'
    assert page.locator('[name=gradeAverage]').input_value() == '3.5'
    assert page.locator('[data-automation-id=formField-firstYearAttended] input').input_value() == '2024'
    assert page.locator('[data-automation-id=formField-lastYearAttended] input').input_value() == '2028'
    assert page.locator('[data-automation-id=formField-skills] [data-automation-id=selectedItem]').inner_text() == 'Java'
    assert max(page.evaluate('scrolls')) > 2500
    traces = [data for path, _, data in requests if path == '/ext/trace']
    assert {'skills', 'section', 'fill'} <= {trace['kind'] for trace in traces}
    steps = [step for trace in traces for step in trace['entries']]
    assert any(step['action'] == 'scrolled' for step in steps)
    study_steps = [step for step in steps if step.get('label') == 'Field of Study']
    assert any(step['step'] == 'dropdown' and step['action'] == 'scrolled' for step in study_steps)
    assert any(step['step'] == 'dropdown' and step['action'] == 'verified' and step.get('verified') for step in study_steps)
    assert all(step['step'] != 'skills' for step in study_steps)
    assert any(step['action'] == 'verified' and step.get('verified') for step in steps)
    assert not any(path == '/ext/skills/choose' for path, _, _ in requests)


@pytest.mark.parametrize('mode', ['pill', 'label', 'transient', 'stale-label'])
def test_t048_inert_workday_row_activates_inner_control(loaded_extension, extension_server, mode):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server, key=KEY))
    page = context.new_page()
    page.goto(server + '/t048.html')
    page.evaluate('value=>window.mode=value', mode)
    # Prove this fixture reproduces the real failure: the exact outer row does nothing.
    search = page.locator('#skills--skills')
    search.fill('AI Workflows')
    search.press('Enter')
    expect(page.locator('[role=option]')).to_have_count(3)
    page.locator('[role=listbox]').evaluate('list=>{list.scrollTop=150;list.dispatchEvent(new Event("scroll"));}')
    row = page.locator('[role=option]').filter(has=page.locator('[data-automation-id=promptOption]', has_text=re.compile('^AI Workflows$')))
    expect(row).to_be_visible()
    row.evaluate('row=>row.click()')
    assert page.evaluate('inertClicks') == 1
    assert page.evaluate('activations') == []
    assert row.get_attribute('aria-label') == 'AI Workflows not checked'
    search.press('Escape')
    page.evaluate('searches.length=0')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)

    def fill():
        return worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},
            func:()=>globalThis.autoApplyContent.autofillSkills(['Python','AI Workflows'])}))[0].result""", tab_id)

    result = fill()
    committed = mode in {'pill', 'label'}
    assert result['added'] == int(committed)
    assert result['skipped'] == int(not committed)
    assert page.evaluate('activations') == ['AI Workflows']
    assert page.evaluate('replacements') == 1
    assert max(page.evaluate('scrolls')) >= 90
    assert page.evaluate('[...selected]') == (['Python', 'AI Workflows'] if committed else ['Python'])
    assert page.locator('[data-automation-id=selectedItem]').all_text_contents() == (
        ['Python', 'AI Workflows'] if mode == 'pill' else ['Python'])
    assert search.input_value() == ''
    assert page.evaluate('searches') == ['AI Workflows']
    steps = [entry for path, _, data in requests if path == '/ext/trace' for entry in data['entries']]
    clicked = next(step for step in steps if step['step'] == 'skills' and step['action'] == 'clicked')
    assert clicked['target'] == 'AI Workflows'
    assert clicked['target_tag'] == 'input'
    assert clicked['target_automation_id'] == 'checkboxPanel'
    assert any(step['action'] == 'activation interrupted' for step in steps)
    verified = next(step for step in steps if step['step'] == 'skills' and step['action'] == 'verified')
    assert verified['verified'] is committed
    assert verified['committed'] is committed
    assert verified['pill'] == ('AI Workflows' if mode == 'pill' else '')
    assert verified['aria_label'] == ('AI Workflows checked' if committed else 'AI Workflows not checked')
    assert not any(path == '/ext/skills/choose' for path, _, _ in requests)
    if committed:
        # Both an existing pill and a checked virtual row must survive another fill.
        again = fill()
        assert again['skipped'] == 0 and again['added'] == 0
        assert page.evaluate('activations') == ['AI Workflows']
        assert page.evaluate('[...selected]') == ['Python', 'AI Workflows']


@pytest.mark.parametrize('theme', ['light','dark'])
def test_t049_one_click_skills_and_layout(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server,key=KEY))
    page = context.new_page()
    page.goto(server+'/t046.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    panel = context.new_page()
    errors=[]
    panel.on('pageerror', lambda error: errors.append(str(error)))
    panel.emulate_media(color_scheme=theme)
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    expect(panel.locator('#skills-progress')).to_have_text('1 matched')
    assert not page.evaluate('searches')
    panel.locator('#autofill').click()
    expect(page.locator('[data-automation-id=selectedItem]').filter(has_text='TypeScript')).to_have_count(1,timeout=20000)
    expect(panel.locator('#autofill')).to_be_enabled(timeout=20000)
    assert page.evaluate('searches').count('TypeScript')==1
    expect(panel.locator('#skills-progress')).not_to_contain_text('Adding skills')
    expect(panel.locator('#skills-progress')).to_have_text('1 matched')
    expect(panel.locator('#progress')).to_have_text('3 skills added')
    assert {'TypeScript','Python','Rust'} <= set(page.locator('[data-automation-id=selectedItem]').all_text_contents())
    assert 'Kubernetes' not in page.locator('[data-automation-id=selectedItem]').all_text_contents()
    expect(panel.locator('#section-results')).to_be_empty()
    expect(panel.locator('#filled')).to_be_empty()
    expect(panel.locator('#skipped')).to_be_empty()
    assert not any(path=='/ext/skills/choose' and data['skill']=='TypeScript' for path,_,data in requests)
    output=ROOT/'.tmp/review/t049'
    output.mkdir(parents=True,exist_ok=True)
    for width in (360,400):
        panel.set_viewport_size(dict(width=width,height=800))
        assert panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
        footer=panel.locator('.page-navigation').bounding_box()
        assert footer['y']+footer['height']<=800
        assert panel.locator('#send-structure').is_visible()
        assert panel.locator('#footer-autofill-skills').count()==0
        panel.screenshot(path=str(output/f'sidebar-{width}-{theme}.png'))
    panel.set_viewport_size(dict(width=360,height=800))
    panel.locator('#skills-menu > summary').click()
    panel.screenshot(path=str(output/f'expanded-skills-360-{theme}.png'))
    panel.locator('#tab-answers').click()
    panel.screenshot(path=str(output/f'answers-360-{theme}.png'))
    panel.locator('#tab-chat').click()
    panel.screenshot(path=str(output/f'chat-360-{theme}.png'))
    panel.locator('#tab-fill').click()
    panel.evaluate("""async () => {
        const {tabResults}=await chrome.storage.session.get('tabResults');
        const current=Object.values(tabResults).find(r=>r.skills);
        current.matched={...current.matched,company:'Fixture International Research and Engineering Company',title:'Software Engineering Intern, Developer Tools and Distributed Systems'};
        current.pending=[{key:'fixture-input',label:'Which local fixture project should we highlight?',kind:'text',options:[]}];
        current.skillsBusy=true;
        await chrome.storage.session.set({tabResults,lastResult:current,chromeRun:{active:true,index:1,total:8,current:{application_id:42},tabId:current.tabId}});
    }""")
    expect(panel.locator('#run-controls')).to_be_visible()
    expect(panel.locator('#company')).to_have_text('Fixture International Research and Engineering Company')
    panel.locator('#skills-menu > summary').click()
    expect(panel.locator('#cancel-skills')).to_be_visible()
    expect(panel.locator('#needs-input')).to_contain_text('Which local fixture project')
    assert panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
    boxes=[panel.locator(selector).bounding_box() for selector in ('#company','#job','#run-controls','#settings')]
    assert boxes[0]['x']+boxes[0]['width']<=boxes[3]['x']+1
    assert boxes[1]['x']+boxes[1]['width']<=boxes[2]['x']+1
    assert abs(boxes[1]['y']-boxes[2]['y'])<=5
    expect(panel.locator('#progress')).to_be_empty()
    expect(panel.locator('#skills-progress')).to_have_text('1 matched')
    footer=panel.locator('.page-navigation').bounding_box()
    assert footer['y']+footer['height']<=800
    panel.screenshot(path=str(output/f'active-run-pending-360-{theme}.png'))
    assert not errors


@pytest.mark.parametrize('action',['cancel','skip'])
def test_t049_run_controls_during_inflight_fill(loaded_extension,extension_server,action):
    context,worker,extension_id=loaded_extension
    server,requests=extension_server
    worker.evaluate("()=>chrome.storage.session.set({chromeRunPaused:true})")
    worker.evaluate("config=>chrome.storage.local.set(config)",dict(server=server,key=KEY))
    worker.evaluate("server => chrome.storage.local.set({autoAdvanceProfiles:{[server+':Fixture Student']:true}})",server)
    fixture_delay(worker,hold=['write'])
    worker.evaluate("""async server=>fetch(server+'/ext/fixture-start',{method:'POST',headers:{'X-AutoApply-Key':'fixture-pairing-key'},body:'{}'})""",server)
    panel=context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#run-pause')).to_have_attribute('aria-label','Resume run')
    panel.locator('#run-pause').click()
    expect(panel.locator('#writing-progress')).to_contain_text('Writing:',timeout=20000)
    old_page=next(p for p in context.pages if p.url==server+'/batch-one.html')
    old_page.evaluate("""() => {window.nextClicks=0;const next=document.createElement('button');next.type='button';next.textContent='Next';next.dataset.automationId='pageFooterNextButton';next.onclick=()=>nextClicks++;document.querySelector('form').append(next);}""")
    expect(panel.locator('#run-skip')).to_be_enabled()
    panel.locator('#run-'+action).click()
    expect(panel.locator('#run-skip')).to_be_disabled()
    fixture_delay(worker,release=['write'])
    if action=='skip':
        expect(panel.locator('#chrome-run-status')).to_contain_text('2/2',timeout=30000)
    expect(panel.locator('#run-controls')).to_be_hidden(timeout=30000)
    results=[data for p,_,data in requests if p=='/ext/run/result']
    assert [data['application_id'] for data in results]==([2] if action=='skip' else [])
    pages=[p for p in context.pages if p.url in {server+'/batch-one.html',server+'/batch-two.html'}]
    assert len(pages)==(2 if action=='skip' else 1)
    assert all(p.evaluate('[submitClicks,submissions]')==[0,0] for p in pages)
    assert old_page.evaluate('nextClicks')==0
    expect(old_page.locator('#writing')).to_have_value('')


def test_t049_skill_overrides_survive_main_autofill(loaded_extension,extension_server):
    context,worker,extension_id=loaded_extension
    server,requests=extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)",dict(server=server,key=KEY))
    page=context.new_page()
    page.goto(server+'/t046.html')
    tab_id=worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id",page.url)
    panel=context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#skills-menu > summary').click()
    panel.get_by_role('button',name='Remove TypeScript',exact=True).click()
    panel.locator('#other-skills summary').click()
    panel.get_by_label('Add all',exact=True).check()
    manual=panel.get_by_role('button',name='Type to add skills',exact=True)
    manual.click()
    panel.locator('#manual-skill-input').fill('Node')
    panel.locator('#manual-skill-add').click()
    panel.locator('#autofill').click()
    expect(panel.locator('#autofill')).to_be_enabled(timeout=30000)
    selected=page.locator('[data-automation-id=selectedItem]').all_text_contents()
    assert 'TypeScript' not in selected
    assert {'Python','Rust','Node.js'}<=set(selected)
    assert page.evaluate('searches').count('Python')==1
    assert page.evaluate('searches').count('Rust')==1
    expect(panel.get_by_role('button',name='Remove Node',exact=True)).to_have_count(0)
    expect(panel.get_by_label('Python',exact=True)).not_to_be_checked()
    expect(panel.get_by_label('Rust',exact=True)).not_to_be_checked()


def test_t049_skills_without_mounted_sidebar_and_concurrent_fill(loaded_extension,extension_server):
    context,worker,_=loaded_extension
    server,requests=extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)",dict(server=server,key=KEY))
    page=context.new_page()
    page.goto(server+'/t046.html')
    tab_id=worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id",page.url)
    worker.evaluate("""async id=>chrome.scripting.executeScript({target:{tabId:id},func:()=>{globalThis.autoApplyContent.sidebar=()=> 'closed';}})""",tab_id)
    results=worker.evaluate("id=>Promise.all([autoApplyAutofill(id),autoApplyAutofill(id)])",tab_id)
    assert sum(not result.get('error') for result in results)==1
    assert sum(result.get('error')=='Autofill is already running.' for result in results)==1
    assert page.locator('[data-automation-id=selectedItem]').filter(has_text='TypeScript').count()==1
    assert page.locator('iframe').count()==0
    assert page.evaluate('searches').count('TypeScript')==1
    assert sum(p=='/ext/skills/plan' for p,_,_ in requests)==1


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_t050_actionable_status_and_compact_outcomes(loaded_extension, extension_server, theme):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused:true})")
    panel = context.new_page()
    panel.set_viewport_size(dict(width=360, height=800))
    panel.emulate_media(color_scheme=theme)
    errors = []
    panel.on('pageerror', lambda error: errors.append(str(error)))
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    output = ROOT / '.tmp/review/t050'
    output.mkdir(parents=True, exist_ok=True)
    base = dict(tabId=tab_id, page=dict(company='Fixture Company', title='Fixture Intern'),
                fields=[], pending=[], writing=[], completed=[], summary=dict(filled=4, skipped=0, errors=[]))
    sign_in = 'Sign in to Workday in this tab. Autofill will resume automatically.'
    for name, changes, expected in [
        ('sign-in', dict(state='needs_input', waiting=True, detail=sign_in), sign_in),
        ('detail', dict(state='needs_input', detail=sign_in,
                        progress='Autofill finished. Review the page.'), sign_in),
        ('cancelled', dict(state='needs_input', waiting=False, detail=sign_in,
                           progress='Form waiting cancelled.'), 'Form waiting cancelled.'),
        ('waiting', dict(waiting=True, progress='Waiting for the application form...'),
                    'Waiting for the application form...'),
        ('retry', dict(state='needs_input', detail=sign_in,
                      progress='No form appeared. Press Autofill to try again.'),
                  'No form appeared. Press Autofill to try again.'),
        ('error', dict(progress='Fix the page validation errors before continuing.',
                      error='Fixture connection error: check pairing settings.',
                      summary=dict(filled=4, skipped=0, errors=['Resume upload needs review.']),
                      writingProgress='Why this role?: Fixture writer unavailable',
                      coverProgress='Cover letter error: Fixture writer unavailable'),
                  'Fix the page validation errors before continuing.'),
        ('settled', dict(progress='Autofill finished. Review the page.',
                        writingProgress='Answer filled: Why this role?', coverProgress='Cover letter attached'),
                    'Review the page.'),
        ('step', dict(progress='Workday step: My Experience'), 'Review the page.'),
    ]:
        worker.evaluate("value => chrome.storage.session.set({tabResults:{[value.tabId]:value}})", dict(base, **changes))
        expect(panel.locator('#progress')).to_have_text(expected)
        if changes.get('waiting'):
            expect(panel.locator('#cancel-form-wait')).to_be_visible()
        else:
            expect(panel.locator('#cancel-form-wait')).to_be_hidden()
        if name == 'error':
            expect(panel.locator('#error')).to_have_text(changes['error'])
            expect(panel.locator('#upload-errors')).to_have_text('Resume upload needs review.')
            expect(panel.locator('#writing-progress')).to_have_text(changes['writingProgress'])
            expect(panel.locator('#cover-progress')).to_have_text(changes['coverProgress'])
        if name == 'settled':
            expect(panel.locator('#autofill .fill-count')).to_have_text('4 fields filled')
            expect(panel.locator('#writing-progress')).to_be_empty()
            expect(panel.locator('#cover-progress')).to_be_empty()
            expect(panel.locator('#error')).to_be_empty()
            expect(panel.locator('#upload-errors')).to_be_empty()
        assert panel.evaluate('document.documentElement.scrollWidth <= innerWidth')
        footer = panel.locator('.page-navigation').bounding_box()
        assert footer['y'] + footer['height'] <= 800
        if name in {'sign-in', 'cancelled', 'error', 'settled'}:
            panel.screenshot(path=str(output / f'{name}-360-{theme}.png'))
    assert not errors


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_t051_actual_iframe_settings_helper_and_collapsed_control(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config=>chrome.storage.local.set(config)", dict(server=server, key=KEY))
    page = context.new_page()
    page.emulate_media(color_scheme=theme)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/t046.html')
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'open')", tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#settings').wait_for()
    panel = next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    panel.wait_for_load_state()
    base = dict(tabId=tab_id, url=page.url, skills_available=True,
                page=dict(company='Fixture International Research and Engineering Company with a long name',
                          title='Software Engineering Intern, Developer Tools and Distributed Systems with a long title', description='Local tools'),
                fields=[], pending=[dict(key='search',label='Type to Add Skills',kind='combobox'),
                                    dict(key='real',label='Which skills would you like to develop?',kind='textarea')],
                completed=[dict(key='search',label='Type to Add Skills',kind='combobox',value='Python'),
                           dict(key='actual',label='Type to Add Skills?',kind='textarea',value='Real questionnaire answer'),
                           dict(key='real',label='Programming skills',kind='text',value='Python',source='profile')], writing=[])
    worker.evaluate("value=>chrome.storage.session.set({tabResults:{[value.tabId]:value},chromeRun:{active:true,index:1,total:8,current:{application_id:42}}})", base)
    expect(panel.locator('#company')).to_have_text(base['page']['company'])
    expect(panel.locator('#skills-progress')).to_have_text('1 matched')
    output = ROOT / '.tmp/review/t051'
    output.mkdir(parents=True, exist_ok=True)

    def bounds():
        assert panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert panel.evaluate("""() => {
            const header=document.querySelector('header').getBoundingClientRect(),footer=document.querySelector('footer').getBoundingClientRect();
            const controls=['settings','minimize','close','run-pause','run-cancel','run-skip','autofill','next-page','auto-advance','send-structure'];
            return header.top>=0 && header.bottom<footer.top && footer.bottom<=innerHeight && controls.every(id=>{
                const e=document.getElementById(id),b=e.getBoundingClientRect();
                return b.width>0 && b.height>0 && b.left>=0 && b.right<=innerWidth && b.top>=0 && b.bottom<=innerHeight && e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));
            });
        }""")

    for width, height in ((360,800),(400,800),(360,600)):
        page.set_viewport_size(dict(width=width,height=height))
        expect(panel.locator('#run-controls')).to_be_visible()
        bounds()
        assert panel.evaluate('innerWidth') == width
        page.screenshot(path=str(output/f'iframe-{width}x{height}-{theme}.png'))
        pages = len(context.pages)
        panel.locator('#settings').click()
        expect(panel.locator('#server')).to_be_enabled()
        expect(panel.locator('#assistant-view')).to_be_hidden()
        assert len(context.pages) == pages
        assert panel.locator('#key').get_attribute('type') == 'password'
        expect(panel.locator('#server')).to_have_value(server)
        panel.locator('#server').fill(server+'?unexpected=1')
        panel.get_by_role('button',name='Save',exact=True).click()
        expect(panel.locator('#status')).to_contain_text('without credentials, query or fragment')
        assert worker.evaluate("async()=> (await chrome.storage.local.get('server')).server") == server
        panel.locator('#server').fill(server)
        panel.locator('#key').fill('   ')
        panel.get_by_role('button',name='Save',exact=True).click()
        expect(panel.locator('#status')).to_have_text('Enter your dashboard pairing key.')
        assert worker.evaluate("async()=> (await chrome.storage.local.get('key')).key") == KEY
        panel.locator('#key').fill(KEY)
        panel.locator('#test').click()
        expect(panel.locator('#status')).to_have_text('Connected — Fixture Student')
        page.screenshot(path=str(output/f'settings-{width}x{height}-{theme}.png'))
        panel.locator('#settings-back').click()
        bounds()
    panel.locator('#tab-answers').click()
    expect(panel.locator('#completed-answers')).to_contain_text('Programming skills')
    expect(panel.locator('#completed-answers')).to_contain_text('Real questionnaire answer')
    assert panel.locator('#completed-answers .field-label').all_text_contents() == ['Type to Add Skills?', 'Programming skills']
    page.screenshot(path=str(output/f'answers-{theme}.png'))
    panel.locator('#tab-fill').click()
    assert panel.locator('#needs-input .prompt').count() == 1
    expect(panel.locator('#needs-input')).to_contain_text('Which skills would you like to develop?')
    panel.locator('#skills-menu > summary').click()
    panel.locator('#other-skills > summary').click()
    labels = panel.locator('#other-skill-options').inner_text()
    assert labels.index('Add all') < labels.index('Type to add skills')
    page.screenshot(path=str(output/f'skills-{theme}.png'))
    panel.locator('#tab-chat').click()
    panel.locator('#subtab-helper').click()
    expect(panel.locator('#helper-result')).to_be_hidden()
    expect(panel.locator('#refinement')).to_be_hidden()
    expect(panel.locator('#version')).to_be_hidden()
    page.screenshot(path=str(output/f'helper-empty-{theme}.png'))
    panel.locator('#question').fill('Why local tools?')
    panel.locator('#settings').click()
    panel.locator('#key').fill('wrong-fixture-key')
    panel.get_by_role('button',name='Save',exact=True).click()
    expect(panel.locator('#status')).to_have_text('Pairing saved.')
    panel.locator('#settings-back').click()
    panel.locator('#generate').click()
    expect(panel.locator('#ai-status')).to_contain_text('Pairing key rejected')
    expect(panel.locator('#helper-result')).to_be_hidden()
    expect(panel.locator('#generate')).to_be_enabled()
    panel.locator('#settings').click()
    panel.locator('#key').fill(KEY)
    panel.locator('#test').click()
    expect(panel.locator('#status')).to_have_text('Connected — Fixture Student')
    panel.locator('#settings-back').click()
    fixture_delay(worker, hold=['write'])
    panel.locator('#generate').click()
    expect(panel.locator('#generate')).to_be_disabled()
    expect(panel.locator('#ai-status')).to_contain_text('Writing')
    expect(panel.locator('#helper-result')).to_be_hidden()
    fixture_delay(worker, release=['write'])
    expect(panel.locator('#answer')).to_have_value('I enjoy building useful local tools.')
    expect(panel.locator('#refinement')).to_be_visible()
    panel.locator('#refinement').fill('Shorter')
    panel.locator('#refine').click()
    expect(panel.locator('#version')).to_have_text('2 / 2')
    expect(panel.locator('#answer')).to_have_value('Refined fixture answer.')
    page.set_viewport_size(dict(width=360,height=800))
    panel.locator('#ask-section').evaluate('e=>e.scrollTop=0')
    page.screenshot(path=str(output/f'helper-generated-{theme}.png'))
    page.set_viewport_size(dict(width=360,height=600))
    panel.locator('#settings').click()
    panel.locator('#server').fill(server+'/')
    panel.get_by_role('button',name='Save',exact=True).click()
    expect(panel.locator('#status')).to_have_text('Pairing saved.')
    panel.locator('#settings-back').press('Escape')
    expect(panel.locator('#subtab-helper')).to_have_attribute('aria-selected','true')
    expect(panel.locator('#version')).to_have_text('2 / 2')
    panel.locator('#question').fill('Another question')
    expect(panel.locator('#helper-result')).to_be_hidden()
    panel.locator('#question').fill('Why local tools?')
    expect(panel.locator('#version')).to_have_text('2 / 2')
    panel.locator('#subtab-chat').click()
    panel.locator('#chat-input').fill('Help with local tools')
    panel.locator('#send').click()
    expect(panel.locator('.bubble.assistant')).to_have_count(1)
    panel.locator('#chat-input').fill('Unsent draft')
    panel.locator('#settings').click()
    panel.locator('#test').click()
    expect(panel.locator('#status')).to_have_text('Connected — Fixture Student')
    panel.locator('#settings-back').click()
    expect(panel.locator('#subtab-chat')).to_have_attribute('aria-selected','true')
    expect(panel.locator('#chat-input')).to_have_value('Unsent draft')
    expect(panel.locator('.bubble.assistant')).to_have_count(1)
    bounds()
    composer=panel.locator('.chat-composer').bounding_box()
    assert composer['y']+composer['height']<=panel.locator('footer').bounding_box()['y']
    page.screenshot(path=str(output/f'chat-{theme}.png'))
    panel.locator('#auto-advance').check()
    expect(panel.locator('#auto-advance')).to_be_checked()
    page.screenshot(path=str(output/f'auto-enabled-{theme}.png'))
    panel.locator('#minimize').click()
    restore=page.get_by_role('button',name='Restore AutoApply sidebar')
    expect(restore).to_be_visible()
    page.screenshot(path=str(output/f'minimized-{theme}.png'))
    box=restore.bounding_box()
    page.mouse.move(box['x']+5,box['y']+5)
    page.mouse.down()
    page.mouse.move(box['x']+5,box['y']+75,steps=5)
    page.mouse.up()
    assert restore.bounding_box()['y']>box['y']+40
    saved=worker.evaluate("async()=> (await chrome.storage.local.get('flagPosition:127.0.0.1'))['flagPosition:127.0.0.1']")
    assert saved>=0
    restore.focus()
    restore.press('Enter')
    expect(panel.locator('#auto-advance')).to_be_checked()
    panel.locator('#minimize').click()
    page.get_by_role('button',name='Dismiss minimized AutoApply').click()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    page.screenshot(path=str(output/f'dismissed-{theme}.png'))
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'toggle')",tab_id)
    expect(panel.locator('#chat-input')).to_have_value('Unsent draft')
    expect(panel.locator('#auto-advance')).to_be_checked()
    # Preference remains when another assistant document is initialized.
    other=context.new_page()
    other.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    expect(other.locator('#auto-advance')).to_be_checked()
    panel.locator('#close').click()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'toggle')",tab_id)
    expect(panel.locator('#chat-input')).to_have_value('Unsent draft')
    assert page.evaluate('searches') == []
    other.goto(f'chrome-extension://{extension_id}/options.html')
    expect(other.locator('#server')).to_be_enabled()
    other.set_viewport_size(dict(width=360,height=600))
    other.emulate_media(color_scheme=theme)
    other.screenshot(path=str(output/f'fallback-options-{theme}.png'))
    panel.locator('#minimize').click()
    page.get_by_role('button',name='Dismiss minimized AutoApply').click()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    assert worker.evaluate("async id=>(await chrome.storage.session.get('sidebarTabs')).sidebarTabs[id]",tab_id) == 'closed'
    page.reload()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'toggle')",tab_id)
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()
    assert not any(path=='/ext/answers' for path,_,_ in requests)
    assert not errors


def test_t054_manual_submit_confirmation_and_narrow_utilities(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=server,key=KEY))
    page=context.new_page()
    page.route('**/t054.html',lambda route:route.fulfill(path=str(FIXTURES/'t054.html'),content_type='text/html'))
    page.goto(server+'/t054.html')
    page.emulate_media(reduced_motion='reduce')
    page.set_viewport_size(dict(width=360,height=600))
    tab_id=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.emulate_media(reduced_motion='reduce')
    worker.evaluate('id=>globalThis.autoApplySidebar(id,"open")',tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#settings').wait_for()
    panel=next(frame for frame in page.frames if frame.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    page.set_viewport_size(dict(width=360,height=600))
    page.screenshot(path=str(ROOT/'.tmp/review/t054/auto-off.png'))
    panel.locator('#auto-advance').check()
    page.screenshot(path=str(ROOT/'.tmp/review/t054/auto-on-lock.png'))
    value=worker.evaluate('id=>globalThis.autoApplyAutofill(id)',tab_id)
    assert value['next_action']['kind']=='submit' and not value.get('waiting')
    expect(panel.locator('#next-page')).to_have_text('Submit')
    expect(panel.locator('#auto-advance')).to_be_disabled()
    assert panel.locator('#auto-advance').is_checked()
    assert page.evaluate('clicks')==0
    automatic=worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>autoApplyContent.nextPage(true)}))[0].result''',tab_id)
    assert not automatic['clicked'] and page.evaluate('clicks')==0
    output=ROOT/'.tmp/review/t054'
    output.mkdir(parents=True,exist_ok=True)
    def shot(name):
        assert panel.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert panel.evaluate('''()=>['settings','minimize','close','autofill','next-page','send-structure'].every(id=>{
            const e=document.getElementById(id),b=e.getBoundingClientRect();
            return b.width>0&&b.left>=0&&b.right<=innerWidth&&b.bottom<=innerHeight&&e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));})''')
        page.screenshot(path=str(output/(name+'.png')))
    for width,height in [(360,600),(400,800)]:
        page.set_viewport_size(dict(width=width,height=height))
        for long in [False,True]:
            updated=dict(value,page=dict(company='Fixture Company'+(' with a very long research and engineering name'*3 if long else ''),title='Fixture Intern'+(' building distributed systems and developer tools'*3 if long else '')))
            updated['matched']=dict(value.get('matched',{}),company=updated['page']['company'],title=updated['page']['title'])
            worker.evaluate('value=>chrome.storage.session.set({tabResults:{[value.tabId]:value}})',updated)
            expect(panel.locator('#company')).to_have_text(updated['page']['company'])
            assert panel.evaluate('''()=>{const b=id=>document.getElementById(id).getBoundingClientRect();return Math.abs((b('company').y+b('company').height/2)-(b('settings').y+15))<2&&Math.abs((b('job').y+b('job').height/2)-(b('connection').y+3.5))<2;}''')
            shot(f'header-{width}x{height}-'+('long' if long else 'short'))
    page.set_viewport_size(dict(width=360,height=600))
    panel.locator('#settings').click()
    expect(panel.locator('#server')).to_be_enabled()
    page.screenshot(path=str(output/'settings-360x600.png'))
    panel.locator('#settings-back').click()
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    assert panel.evaluate('''()=>{const s=document.getElementById('snapshot-status').getBoundingClientRect(),b=document.getElementById('send-structure').getBoundingClientRect();return s.right<=b.left&&Math.abs(s.y+s.height/2-b.y-b.height/2)<2;}''')
    shot('sent-inline')
    expect(panel.locator('#snapshot-status')).to_be_empty(timeout=5000)
    panel.evaluate('''()=>{const send=chrome.runtime.sendMessage.bind(chrome.runtime);let failed=false;chrome.runtime.sendMessage=m=>m.type==='page-snapshot'&&!failed?(failed=true,Promise.resolve({error:'Fixture snapshot failed'})):send(m);}''')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Failed')
    expect(panel.locator('#error')).to_contain_text('Try Analyze page again')
    shot('snapshot-error')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    expect(panel.locator('#error')).to_be_empty()
    shot('snapshot-retry-sent')
    panel.locator('#minimize').click()
    page.locator('#autoapply-sidebar .restore').hover()
    page.screenshot(path=str(output/'minimized-hover-x.png'))
    assert page.locator('#autoapply-sidebar .collapsed').evaluate("e=>getComputedStyle(e).overflow")=='hidden'
    page.locator('#autoapply-sidebar .restore').click()
    # Runtime/page synthetic requests cannot invoke the privileged manual action.
    page.evaluate("()=>window.postMessage({type:'manual-submit'},'*')")
    assert page.evaluate('clicks')==0
    stale=panel.evaluate('''({tabId,expected})=>chrome.runtime.sendMessage({type:'manual-submit',tabId,expected:{...expected,step:'Old step'}})''',dict(tabId=tab_id,expected=value['next_action']))
    assert 'error' in stale and page.evaluate('clicks')==0
    worker.evaluate('()=>chrome.storage.session.set({chromeRun:{active:true}})')
    refused=panel.evaluate('''({tabId,expected})=>chrome.runtime.sendMessage({type:'manual-submit',tabId,expected})''',dict(tabId=tab_id,expected=value['next_action']))
    assert 'error' in refused and page.evaluate('clicks')==0
    worker.evaluate('()=>chrome.storage.session.set({chromeRun:{active:false}})')
    page.evaluate("mode='invalid'")
    panel.locator('#next-page').click()
    expect(panel.locator('#progress')).to_contain_text('Required information missing')
    assert page.evaluate('clicks')==1
    expect(panel.locator('#submission-status')).to_be_hidden()
    assert not any(path=='/ext/applied' for path,_,_ in requests)
    shot('submission-validation-failure')
    page.evaluate("document.querySelector('#feedback').replaceChildren();mode='pending'")
    panel.locator('#next-page').click()
    expect(panel.locator('#next-page')).to_have_text('Submitting...')
    expect(panel.locator('#submission-status')).to_be_hidden()
    assert page.evaluate('clicks')==2
    shot('submitting-no-celebration')
    duplicate=panel.evaluate('''({tabId,expected})=>chrome.runtime.sendMessage({type:'manual-submit',tabId,expected})''',dict(tabId=tab_id,expected=value['next_action']))
    assert 'error' in duplicate and page.evaluate('clicks')==2
    page.evaluate('confirmApplication()')
    expect(panel.locator('#submission-status')).to_be_visible()
    expect(panel.locator('#applied-status')).to_be_visible()
    expect(panel.locator('#progress')).to_be_empty()
    expect(panel.get_by_text('Application submitted',exact=True)).to_have_count(1)
    expect(panel.get_by_text('Marked as applied',exact=True)).to_have_count(0)
    expect(panel.get_by_role('button',name='Undo tracking',exact=True)).to_be_visible()
    assert panel.locator('#submission-status #undo-applied').count()==1
    shot('confirmed-moon')
    confirmed=worker.evaluate("async id=>(await chrome.storage.session.get('tabResults')).tabResults[id]",tab_id)
    for theme in ['dark','light']:
        page.emulate_media(color_scheme=theme,reduced_motion='reduce')
        for long in [False,True]:
            title='Fixture Intern'+(' building distributed systems and developer tools'*3 if long else '')
            company='Fixture Company'+(' with a very long research and engineering name'*3 if long else '')
            updated=dict(confirmed,matched=dict(confirmed.get('matched',{}),company=company,title=title))
            worker.evaluate('value=>chrome.storage.session.set({tabResults:{[value.tabId]:value}})',updated)
            expect(panel.locator('#company')).to_have_text(company)
            expect(panel.locator('#job')).to_have_text(title)
            assert panel.locator('#submission-status').evaluate("e=>e.getBoundingClientRect().height<=48")
            assert panel.locator('#undo-applied').evaluate("e=>{const b=e.getBoundingClientRect();return b.left>=0&&b.right<=innerWidth&&e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}")
            shot('confirmed-360x600-'+theme+'-'+('long' if long else 'short'))
    page.evaluate("document.querySelector('#submitted-heading').textContent='Application Submitted '")
    page.wait_for_timeout(500)
    assert sum(path=='/ext/applied' for path,_,_ in requests)==1
    panel.locator('#undo-applied').click()
    expect(panel.locator('#applied-status')).to_be_hidden()
    expect(panel.locator('#submission-status')).to_be_visible()
    expect(panel.locator('#tracking-status')).to_have_text('Tracking undone. Site submission is unchanged.')
    expect(panel.locator('#retry-tracking')).to_be_hidden()
    page.evaluate("document.querySelector('#submitted-heading').textContent='Application Submitted'")
    page.wait_for_timeout(500)
    assert sum(path=='/ext/applied' for path,_,_ in requests)==1
    assert sum(path=='/ext/unapplied' for path,_,_ in requests)==1
    # Reviewing a fresh form restores the explicit Submit action after tracking Undo.
    page.evaluate("document.querySelector('#feedback').replaceChildren()")
    worker.evaluate('id=>globalThis.autoApplyAutofill(id)',tab_id)
    expect(panel.locator('#submission-status')).to_be_hidden()
    expect(panel.locator('#next-page')).to_be_enabled()
    assert page.evaluate('clicks')==2
    assert not errors


# Keep the preserved regression cases in the runner's explicit extension file.
# The source module sets __test__ false so full discovery executes each case once.
@pytest.mark.parametrize('name,parameters', [
    ('test_snapshot_checkbox_questions_and_pending_options',{}),
    *[('test_english_proficiency_rerender_preserves_language_and_study',{'language':value}) for value in ['English','','Spanish']],
    ('test_stale_study_search_is_not_a_committed_pill',{}),
    *[('test_generic_study_requires_stable_pill_after_current_activation',{'mode':mode}) for mode in ['replaced','native-only','stale-label','transient-pill']],
    ('test_immediate_busy_before_delayed_scan_and_error_reset',{}),
    ('test_all_profile_skills_and_reset_do_not_create_exclusions',{}),
    ('test_c_cpp_split_before_search_preserves_punctuation_and_pills',{}),
])
def test_t053_preserved_browser_coverage(loaded_extension, extension_server, name, parameters, request):
    import test_t053_browser as source
    getattr(source,name)(loaded_extension,extension_server,**parameters)


@pytest.mark.parametrize('scenario',['tracking_error','unmatched','stale_confirmation'])
def test_t054_confirmation_tracking_boundaries(loaded_extension, extension_server, scenario):
    context,worker,extension_id=loaded_extension
    server,requests=extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=server,key=KEY))
    if scenario=='unmatched':
        worker.evaluate('''()=>{const original=fetch;globalThis.fetch=(url,...args)=>String(url).endsWith('/ext/match')?Promise.resolve(new Response('{}',{headers:{'Content-Type':'application/json'}})):original(url,...args);}''')
    page=context.new_page()
    page.route('**/t054.html',lambda route:route.fulfill(path=str(FIXTURES/'t054.html'),content_type='text/html'))
    page.goto(server+'/t054.html')
    tab_id=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    if scenario=='stale_confirmation':page.evaluate('confirmApplication()')
    worker.evaluate('id=>globalThis.autoApplyAutofill(id)',tab_id)
    if scenario=='unmatched':
        # Exercise an absent authorized match, not only an empty match object.
        worker.evaluate("async id=>{const {autofillTabs}=await chrome.storage.session.get('autofillTabs');delete autofillTabs[id].matched;await chrome.storage.session.set({autofillTabs});}",tab_id)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    panel=page.frame_locator('#autoapply-sidebar iframe')
    if scenario=='tracking_error':
        worker.evaluate('''()=>{const original=fetch;globalThis.fixtureFetch=original;globalThis.trackingAttempts=0;globalThis.fetch=(url,...args)=>String(url).endsWith('/ext/applied')?(globalThis.trackingAttempts++,Promise.resolve(new Response('{"error":"Fixture tracking unavailable"}',{status:503,headers:{'Content-Type':'application/json'}}))):original(url,...args);}''')
    page.locator('#final').click()
    if scenario=='stale_confirmation':
        page.locator('#feedback').evaluate("el=>el.append(' ')")
        page.wait_for_timeout(500)
        expect(panel.locator('#submission-status')).to_be_hidden()
        assert not any(path=='/ext/applied' for path,_,_ in requests)
        return
    page.evaluate('confirmApplication()')
    expect(panel.locator('#submission-status')).to_be_visible()
    expect(panel.locator('#applied-status')).to_be_hidden()
    expect(panel.locator('#tracking-status')).to_contain_text('No matching application' if scenario=='unmatched' else 'Tracking failed')
    expect(panel.locator('#retry-tracking')).to_be_visible()
    expect(panel.locator('#progress')).to_be_empty()
    expect(panel.get_by_text('Application submitted',exact=True)).to_have_count(1)
    page.evaluate("document.querySelector('#submitted-heading').textContent='Application Submitted '")
    page.wait_for_timeout(600)
    assert not any(path=='/ext/applied' for path,_,_ in requests)
    if scenario=='tracking_error':
        assert worker.evaluate('trackingAttempts')==1
    assert not errors
    page.emulate_media(reduced_motion='reduce')
    page.set_viewport_size(dict(width=360,height=600))
    page.screenshot(path=str(ROOT/'.tmp/review/t054'/('confirmation-'+scenario+'.png')))
    if scenario=='tracking_error':
        worker.evaluate('()=>{globalThis.fetch=globalThis.fixtureFetch;}')
        panel.locator('#retry-tracking').click()
        expect(panel.locator('#applied-status')).to_be_visible()
        expect(panel.locator('#tracking-status')).to_be_empty()
        assert sum(path=='/ext/applied' for path,_,_ in requests)==1


def test_t054_manifest_moon_icons_are_vector_derived():
    import struct
    manifest=json.loads((ROOT/'extension/manifest.json').read_text())
    assert manifest['icons']==manifest['action']['default_icon']
    assert 'M20 15.5A9 9 0 0 1 8.5 4 9 9 0 1 0 20 15.5Z' in (ROOT/'extension/icons/moon.svg').read_text()
    for size in [16,32,48,128]:
        data=(ROOT/'extension'/manifest['icons'][str(size)]).read_bytes()
        assert data[:8]==b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II',data[16:24])==(size,size)



def t057_page(loaded_extension, extension_server, fixture):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server,key=KEY))
    page = context.new_page()
    page.route('**/t057-*',lambda route:route.fulfill(path=str(FIXTURES/fixture),content_type='text/html'))
    page.goto(server+'/'+fixture)
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    page.wait_for_timeout(200)
    return page,worker,tab_id,requests


def test_t057_snapshot_jazzhr_fields_and_attach(loaded_extension,extension_server):
    page,worker,tab_id,requests=t057_page(loaded_extension,extension_server,'t057-jazzhr.html')
    page.evaluate("""() => {
      window.clickedLinks=[];window.submissions=0;
      document.addEventListener('click',e=>{const a=e.target.closest('a');if(a)clickedLinks.push(a.id);});
      document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();submissions++;});
    }""")
    result=worker.evaluate("""async id=>{const [r]=await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const api=autoApplyContent,scan=await api.scan();
      const values={'Address':'12 Example Road','City':'Example City','State/Province':'Maryland','Postal code':'20999','Permanent Address':'34 Home Road',
        'Were you referred to Healthpeak by a current employee?':'No','Do you currently, or will you in the future require sponsorship to work in the US?':'No'};
      const answers=scan.fields.map(f=>({key:f.key,status:f.kind==='file'?'FILE':values[f.label]?'RESOLVED':'OPTIONAL_SKIP',value:f.kind==='file'?'resume':values[f.label]}));
      return {scan,summary:await api.fill(answers,{resume:{base64:btoa('%PDF fixture'),filename:'synthetic-resume.pdf',mime:'application/pdf'}})};
    }});return r.result;}""",tab_id)
    fields=result['scan']['fields']
    assert [f['label'] for f in fields[:4]] == ['Address','City','State/Province','Postal code']
    groups=[f for f in fields if f['kind']=='checkbox']
    assert len(groups)==2 and all(f['options']==['Yes','No'] and not f['multiple'] for f in groups)
    assert result['summary']['filled']==8, result['summary']
    assert page.locator('#resumator-checkbox-2936528-2').is_checked()
    assert not page.locator('#resumator-checkbox-2936528-1').is_checked()
    assert page.locator('#resumator-resume-value').evaluate('e=>e.files[0].name')=='synthetic-resume.pdf'
    expect(page.locator('#resumator-resume-upload-wrapper')).to_be_visible()
    assert page.locator('#resumator-resume-value').evaluate('async e=>await e.files[0].text()')=='%PDF fixture'
    assert page.evaluate('clickedLinks')==['resumator-choose-upload']
    assert page.evaluate('submissions')==0
    assert page.locator('#resumator-questionnaire-q2936531').input_value()==''
    assert all('captcha' not in f['label'].lower() for f in fields)
    assert not any(path=='/ext/applied' for path,_,_ in requests)


@pytest.mark.parametrize('reset',[False,True])
def test_t057_pinpoint_settled_phone_labels_consent(loaded_extension,extension_server,reset):
    page,worker,tab_id,_=t057_page(loaded_extension,extension_server,'t057-pinpoint.html')
    page.evaluate("""reset=>{document.querySelectorAll('[id^=\"answer-label-\"]').forEach(el=>el.textContent=el.textContent.replace(/  yes$/, '    YeS  ').replace(/  no$/, '    nO  '));document.getElementById('application_form_application_phone').addEventListener('blur',event=>setTimeout(()=>{
      const clone=event.target.cloneNode();event.target.replaceWith(clone);clone.value=reset?'':'+1 202-555-0199';
    },220),{once:true});}""",reset)
    value=worker.evaluate("""async id=>{const [r]=await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const scan=await autoApplyContent.scan();const answers=scan.fields.filter(f=>['tel','radio','checkbox'].includes(f.kind)).map(f=>({key:f.key,status:'RESOLVED',value:f.kind==='tel'?'+12025550199':f.kind==='checkbox'?true:'Yes'}));
      return {fields:scan.fields,summary:await autoApplyContent.fill(answers)};
    }});return r.result;}""",tab_id)
    radios=[f for f in value['fields'] if f['kind']=='radio']
    assert [f['label'] for f in radios]==['Are you within a commutable distance or willing to relocate?','Are you legally authorized to work in the United States as per the below ITAR requirements?']
    assert all(f['options']==['Yes','No'] for f in radios)
    assert value['summary']['filled']==(3 if reset else 4),value['summary']
    assert bool(value['summary']['needs_input']) is reset
    expect(page.locator('#application_form_application_phone')).to_have_value('' if reset else '+1 202-555-0199')
    expect(page.locator('#application_process_information')).to_be_checked()
    expect(page.locator('#identity')).to_have_value('')


def test_t057_actual_chat_full_replacement_sidebar_restore(loaded_extension,extension_server):
    page,worker,tab_id,requests=t057_page(loaded_extension,extension_server,'t057-pinpoint.html')
    fixture_delay(worker,t057_chat=True)
    # Simulate a page opened before extension reload: reinjection must be idempotent.
    worker.evaluate("""async id=>{await chrome.scripting.executeScript({target:{tabId:id},func:()=>{delete globalThis.autoApplyContent;}});await autoApplySidebar(id);} """,tab_id)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    panel.locator('#tab-chat').click();panel.locator('#subtab-chat').click()
    page.locator('#letter').focus()
    page.evaluate("""()=>{document.getElementById('identity').value='OTHER PRIVATE ANSWER';for(const type of ['password','hidden','file']){const input=document.createElement('input');input.type=type;input.id=type+'-private';if(type!=='file')input.value='PRIVATE '+type;document.querySelector('form').append(input);}const captcha=document.createElement('textarea');captcha.id='captcha-private';captcha.value='PRIVATE CAPTCHA';document.querySelector('form').append(captcha);}""")
    panel.locator('#chat-input').fill('Refine the cover letter already in the field');panel.locator('#send').click()
    draft='A grounded synthetic draft. '*150
    expect(panel.locator('.bubble.assistant p')).to_have_text(draft.strip())
    sent=next(payload for path,_,payload in requests if path=='/ext/chat')
    assert sent['field_context']['current_answer']['text']=='Existing text'
    assert sent['field_context']['current_answer']['key']==sent['field_context']['current_key']
    assert 'PRIVATE' not in json.dumps(sent) and all('value' not in f for f in sent['field_context']['fields'])
    panel.locator('#chat-input').fill('replace current field with that');panel.locator('#send').click()
    expect(page.locator('#letter')).to_have_value(draft.strip())
    expect(panel.locator('.bubble.assistant p').last).to_contain_text('Replaced Cover letter')
    # Receipts never become drafts, including after more than a history window
    # of retries and a real MV3 panel reload.
    for index in range(7):
        panel.locator('#chat-input').fill('replace current field with that');panel.locator('#send').click()
        expect(panel.locator('#send')).to_be_enabled()
        expect(page.locator('#letter')).to_have_value(draft.strip())
    panel.locator('body').evaluate('()=>location.reload()')
    panel.locator('#tab-chat').click();panel.locator('#subtab-chat').click()
    panel.locator('#chat-input').fill('replace Cover letter field with that');panel.locator('#send').click()
    expect(panel.locator('#send')).to_be_enabled()
    expect(page.locator('#letter')).to_have_value(draft.strip())
    assert [payload for path,_,payload in requests if path=='/ext/chat'][-1]['last_draft']==draft.strip()
    page.set_viewport_size(dict(width=360,height=700))
    output=ROOT/'.tmp/review/t057';output.mkdir(parents=True,exist_ok=True)
    assert panel.locator('body').evaluate('e=>e.scrollWidth<=innerWidth')
    panel.locator('.bubble.assistant p').last.scroll_into_view_if_needed()
    page.screenshot(path=str(output/'chat-replacement-360.png'))
    old_context=panel.locator('body').evaluate('async()=>chrome.runtime.sendMessage({type:"chat-context",tabId:Number(new URL(location.href).searchParams.get("tabId"))})')
    assert old_context['current_answer']['text']==draft.strip() and all('value' not in field for field in old_context['fields'])

    panel.locator('#chat-input').fill('ordinary action');panel.locator('#send').click()
    expect(panel.locator('#chat-status')).to_contain_text('refused')
    expect(page.locator('#letter')).to_have_value(draft.strip())
    panel.locator('#chat-input').fill('fake Submit');panel.locator('#send').click()
    expect(panel.locator('#chat-status')).to_contain_text('refused')
    expect(page.locator('#letter')).to_have_value(draft.strip())
    assert not any(path=='/ext/applied' for path,_,_ in requests)
    for state in ['open','minimized','closed']:
        worker.evaluate('async args=>{await autoApplySidebar(args.id,args.state);await autoApplySidebar(args.id,"open");}',dict(id=tab_id,state=state))
        expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()
    assert page.locator('#autoapply-sidebar').count()==1
    page.goto(page.url+'?next=1')
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()
    rejected=panel.locator('body').evaluate('''async (body,ctx)=>chrome.runtime.sendMessage({type:'chat-replace',tabId:ctx.tabId,context:ctx,request:'replace current field with that',action:{type:'replace_text',key:ctx.current_key,text:'bad'}})''',old_context)
    assert rejected.get('error') and page.locator('#letter').input_value()=='Existing text'
    panel.locator('#tab-chat').click();panel.locator('#subtab-chat').click()
    panel.locator('#chat-input').fill('replace current field with that');panel.locator('#send').click()
    expect(panel.locator('.bubble.assistant p').last).to_contain_text('Select the text field')
    expect(page.locator('#letter')).to_have_value('Existing text')
    # A stale document and a stale field identity both refuse without changing the form.
    context=worker.evaluate('async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>autoApplyContent.chatContext()}))[0].result',tab_id)
    context['document']='old-document'
    refused=worker.evaluate("""async args=>{const [r]=await chrome.scripting.executeScript({target:{tabId:args.id},func:async ctx=>{try{await autoApplyContent.chatReplace(ctx,{type:'replace_text',key:ctx.fields.at(-1).key,text:'bad'});return false;}catch{return true;}},args:[args.context]});return r.result;}""",dict(id=tab_id,context=context))
    assert refused



def test_t057_actual_chat_rewrite_and_application_isolation(loaded_extension,extension_server):
    page,worker,tab_id,requests=t057_page(loaded_extension,extension_server,'t057-pinpoint.html')
    fixture_delay(worker,t057_chat=True)
    worker.evaluate('id=>autoApplySidebar(id)',tab_id)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    panel.locator('#tab-chat').click();panel.locator('#subtab-chat').click()
    page.locator('#letter').focus()
    panel.locator('#chat-input').fill('replace current field with a shorter version');panel.locator('#send').click()
    expect(page.locator('#letter')).to_have_value('A concise synthetic answer.')
    expect(panel.locator('.bubble.assistant p').last).to_contain_text('Replaced Cover letter')
    sent=[payload for path,_,payload in requests if path=='/ext/chat'][-1]
    assert sent['field_context']['current_answer']['text']=='Existing text'
    panel.locator('#chat-input').fill('update Cover letter to be more concise but mismatch');panel.locator('#send').click()
    expect(panel.locator('#chat-status')).to_contain_text('refused')
    expect(page.locator('#letter')).to_have_value('A concise synthetic answer.')
    for quote in ['"', "'"]:
        panel.locator('#chat-input').fill('replace Cover letter with '+quote+'No'+quote);panel.locator('#send').click()
        expect(panel.locator('#send')).to_be_enabled()
        expect(page.locator('#letter')).to_have_value('No')
    # A different application in the same panel cannot reference this draft.
    worker.evaluate('''async args=>{const {tabResults={}}=await chrome.storage.session.get('tabResults');
      tabResults[args.id]={tabId:args.id,url:args.url,matched:{application_id:987,company:'Another fixture'}};
      await chrome.storage.session.set({tabResults});}''',dict(id=tab_id,url=page.url))
    expect(panel.locator('#company')).to_have_text('Another fixture')
    expect(panel.locator('.bubble')).to_have_count(0)
    panel.locator('#chat-input').fill('replace Cover letter with that');panel.locator('#send').click()
    expect(panel.locator('#send')).to_be_enabled()
    expect(page.locator('#letter')).to_have_value('No')
    sent=[payload for path,_,payload in requests if path=='/ext/chat'][-1]
    assert 'last_draft' not in sent and not any(m['role']=='assistant' for m in sent['messages'])
    expect(panel.locator('.bubble.assistant p').last).to_contain_text('Draft the answer first')


def test_t057_phone_country_conflict_never_overwrites(loaded_extension,extension_server):
    page,worker,tab_id,_=t057_page(loaded_extension,extension_server,'t057-pinpoint.html')
    page.locator('.selected-flag').evaluate("e=>e.title='France: +33'")
    value=worker.evaluate("""async id=>{const [r]=await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const scan=await autoApplyContent.scan(),field=scan.fields.find(f=>f.kind==='tel');
      return autoApplyContent.fill([{key:field.key,status:'RESOLVED',value:'+12025550199'}]);
    }});return r.result;}""",tab_id)
    assert value['filled']==0 and value['needs_input']==['Phone']
    expect(page.locator('#application_form_application_phone')).to_have_value('')
    assert page.locator('.selected-flag').get_attribute('title')=='France: +33'



def test_t057_toolbar_entrypoint_missing_script_and_restricted_pages(tmp_path):
    import shutil
    import subprocess
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    source=(ROOT/'extension/background.js').read_text(encoding='utf-8')
    # Include the intent/version state used by both explicit opens and restores.
    code=source[source.index('const sidebarVersions='):source.index('globalThis.autoApplySidebar=')]
    script=r"""
const assert=require('node:assert/strict');let click,injections=0,title='',badge='',state='closed';const updates=[];
const session={},tab={id:1,url:'https://example.test/apply'},delay=async()=>{};
const chrome={tabs:{get:async()=>tab,query:async()=>[tab],onUpdated:{addListener:fn=>updates.push(fn)}},
 storage:{session:{get:async()=>session,set:async value=>Object.assign(session,value)}},
 action:{onClicked:{addListener:fn=>click=fn},setBadgeText:async value=>badge=value.text,setTitle:async value=>title=value.title},
 scripting:{executeScript:async spec=>{if(spec.files){injections++;globalThis.autoApplyContent={sidebar:mode=>state=mode};return [];}
 return [{result:await spec.func(...(spec.args||[]))}];}}};
const console={error:()=>{}};
"""+code+r"""
(async()=>{
 await click(tab);assert.equal(state,'open');assert.equal(injections,1);
 for(const previous of ['open','closed','minimized']){state=previous;await click(tab);assert.equal(state,'open');}
 assert.equal(injections,1);assert.equal(session.sidebarTabs[1],'open');
 tab.url='chrome://settings/';await click(tab);assert.equal(badge,'!');assert.match(title,/HTTP or HTTPS/);
 // Persist a minimize intent through the shipped API, then simulate a new document.
 tab.url='https://example.test/next';await showSidebar(tab.id,'minimized');assert.equal(session.sidebarTabs[1],'minimized');state='closed';
 for(const updated of updates)updated(1,{status:'complete'});
 // Drain storage reads and the serialized sidebar work before checking restore.
 await new Promise(setImmediate);assert.equal(state,'minimized');
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
"""
    path=tmp_path/'toolbar.js';path.write_text(script,encoding='utf-8')
    result=subprocess.run([node,str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr



def test_t057_content_initialization_waits_once_for_document(tmp_path):
    import subprocess
    source=(ROOT/'extension/content.js').read_text(encoding='utf-8')
    prefix=source[:source.index('  let nextKey =')]
    # Exercise the shipped initialization guard before installing DOM-dependent handlers.
    probe=prefix+"initializations++;globalThis.autoApplyContent={};globalThis[singleton]=globalThis.autoApplyContent;})();"
    script="const assert=require('node:assert/strict'),vm=require('node:vm');\n"
    script+="const callbacks=[],context={initializations:0,document:{body:null,addEventListener:(name,fn)=>callbacks.push(fn)}};vm.createContext(context);\n"
    script+="const code="+json.dumps(probe)+";vm.runInContext(code,context);vm.runInContext(code,context);assert.equal(callbacks.length,1);\n"
    script+="context.document.body={};callbacks[0]();assert.equal(context.initializations,1);vm.runInContext(code,context);assert.equal(context.initializations,1);\n"
    path=tmp_path/'initialization.js';path.write_text(script,encoding='utf-8')
    value=subprocess.run(['node',str(path)],capture_output=True,text=True,timeout=15)
    assert value.returncode==0,value.stderr



def test_t057_chat_user_edit_and_occupied_referral(loaded_extension, extension_server):
    page,worker,tab_id,_=t057_page(loaded_extension,extension_server,'t057-pinpoint.html')
    worker.evaluate('id=>autoApplySidebar(id)',tab_id)
    panel=page.frame_locator('#autoapply-sidebar iframe')
    page.locator('#letter').focus()
    initial='A'*12500
    page.locator('#letter').fill(initial)
    ctx=panel.locator('body').evaluate('async (body,id)=>chrome.runtime.sendMessage({type:"chat-context",tabId:id,request:"Cover letter"})',tab_id)
    assert ctx['current_key'] and ctx.get('current_answer'), ctx
    assert ctx['current_answer']['text']==initial[:12000]
    page.locator('#letter').fill(initial+'User typed while waiting')
    rejected=panel.locator('body').evaluate("async (body,ctx)=>chrome.runtime.sendMessage({type:'chat-replace',tabId:ctx.tabId,context:ctx,request:'replace Cover letter field with that',action:{type:'replace_text',key:ctx.current_key,text:'old draft'}})",ctx)
    assert 'while waiting' in rejected['error'].lower(), json.dumps(dict(rejected=rejected,context=ctx,actual_url=page.url))
    expect(page.locator('#letter')).to_have_value(initial+'User typed while waiting')
    page.evaluate("()=>{const f=document.createElement('fieldset');f.innerHTML='<legend>Employee referral</legend><label><input type=radio name=ref value=Yes checked>Yes</label><label><input type=radio name=ref value=No>No</label>';document.querySelector('form').append(f);}")
    summary=worker.evaluate("async id=>{const [r]=await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{const s=await autoApplyContent.scan(),f=s.fields.find(f=>f.label==='Employee referral');return autoApplyContent.fill([{key:f.key,status:'RESOLVED',value:'No'}]);}});return r.result;}",tab_id)
    expect(page.locator('input[name=ref][value=Yes]')).to_be_checked()
    assert summary['filled']==0


def test_t057_chat_busy_mutation_guards(tmp_path):
    import subprocess
    source=(ROOT/'extension/background.js').read_text(encoding='utf-8')
    code=source[source.index('function chatReplacementIntent('):source.index('  if (message.type === "page-context")')]
    # Execute the shipped action branch with synthetic Chrome APIs and busy maps.
    code += 'throw Error("unexpected branch");}'
    script="const assert=require('node:assert/strict');let running=false,edits=0,reads=0;const analysisTasks=new Map(),writingTasks=new Map(),activeResults=new Map(),tab={id:1,url:'https://example.test/apply'};const showSidebar=async()=>{};const chrome={tabs:{get:async()=>tab},scripting:{executeScript:async spec=>{reads++;return [{result:{fields:[],document:'doc'},documentId:'doc'}];}}};\n"+code+"""
(async()=>{
const ctx={tabId:1,url:tab.url,document:'doc',documentId:'doc',fields:[{key:'k',label:'Cover letter',kind:'textarea',max_length:null}],current_key:'k'};
const message={type:'chat-replace',tabId:1,context:ctx,request:'replace current field with that',action:{type:'replace_text',key:'k',text:'draft'}};
for(const mode of ['autofill','skills','writing']){
 running=mode==='autofill';skillsRunning=mode==='skills';if(mode==='writing')writingTasks.set(1,Promise.resolve());
 await assert.rejects(()=>action(message),/busy.*retry/i);assert.equal(reads,0);
 const context=await action({type:'chat-context',tabId:1});assert.equal(context.tabId,1);assert.equal(reads,1);reads=0;
 running=false;skillsRunning=false;writingTasks.clear();
}
// A failed task removed by the existing finally cleanup does not keep the lock.
await assert.rejects(()=>action({...message,request:'ordinary chat'}),/not authorized/);assert.equal(reads,0);
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
"""
    path=tmp_path/'busy.js';path.write_text(script,encoding='utf-8')
    result=subprocess.run(['node',str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def t059_page(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.route('**/t059-pinpoint-demographics.html', lambda route: route.fulfill(
        path=str(FIXTURES/'t059-pinpoint-demographics.html'), content_type='text/html'))
    page.goto(server+'/t059-pinpoint-demographics.html')
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    return page, worker, tab_id, requests


def test_t059_captured_pinpoint_demographics(loaded_extension, extension_server):
    page, worker, tab_id, requests = t059_page(loaded_extension, extension_server)
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert 'error' not in result, result
    fields = result['fields']
    assert len(fields) == 4 and len({f['key'] for f in fields}) == 4
    assert [f['label'] for f in fields] == ['Gender', 'Ethnicity', 'Disability', 'Veteran Status']
    assert all(f['kind'] == 'combobox' and not f['options_partial'] for f in fields)
    collected = [payload for path, _, payload in requests if path == '/ext/answers' and any(f['options'] for f in payload['fields'])]
    assert collected and [len(f['options']) for f in collected[0]['fields']] == [3, 8, 3, 7], result
    assert result['summary']['filled'] == 3, result
    assert [f['label'] for f in result['pending']] == ['Ethnicity']
    assert len(result['completed']) == 3
    page.wait_for_timeout(500)
    assert page.locator('.react-select input[type=hidden]').evaluate_all('(els)=>els.map(e=>e.value)') == ['87423', '', '87467', '87472']
    assert page.locator('.react-select__single-value').all_text_contents() == [
        'Choose not to self-identify', 'Yes, I have a disability, or have had one in the past', 'I am not a protected veteran']
    assert page.locator('select').evaluate_all('(els)=>els.map(e=>e.value)') == ['blank'] * 4
    assert page.locator('[role=combobox]').evaluate_all('(els)=>els.every(e=>getComputedStyle(e).opacity==="0"&&e.getBoundingClientRect().width<1&&!e.value)')
    assert page.evaluate('[submissions,proxyInputs,fallbackChanges,keys.includes("Enter")]') == [0, 0, 0, False]
    assert page.evaluate('controlOpens') >= 4
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#fill-section')).to_contain_text('Ethnicity')
    panel.locator('#tab-answers').click()
    for name in ['Gender', 'Disability', 'Veteran Status']:
        expect(panel.locator('#completed-answers')).to_contain_text(name)
    # Occupied manual choices survive subsequent autofill, including the missing-preference ethnicity.
    page.locator('.react-select__control').nth(1).click()
    page.locator('.react-select__option', has_text='Asian').click()
    page.wait_for_timeout(150)
    again = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert again['summary']['filled'] == 0
    assert page.locator('.react-select input[type=hidden]').evaluate_all('(els)=>els.map(e=>e.value)') == ['87423', '87431', '87467', '87472']
    assert not any(path == '/ext/applied' for path, _, _ in requests)


@pytest.mark.parametrize('failure', ['reset', 'hidden-only', 'display-only', 'wrong-id'])
def test_t059_select_only_requires_retained_commit(loaded_extension, extension_server, failure):
    page, worker, tab_id, _ = t059_page(loaded_extension, extension_server)
    page.evaluate('''failure=>{
      const widget=document.querySelector('.react-select');
      if(failure==='reset')widget.dataset.reset='true';
      else new MutationObserver(()=>{
        const display=widget.querySelector('.react-select__single-value'), hidden=widget.querySelector('[type=hidden]');
        if(failure==='hidden-only')display?.remove();
        else if(hidden.value && hidden.value !== 'wrong')hidden.value=failure==='wrong-id'?'wrong':'';
      }).observe(widget,{childList:true,subtree:true,attributes:true,attributeFilter:['value']});
    }''', failure)
    value = worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const scan=await autoApplyContent.scan(),field=scan.fields[0];
      return autoApplyContent.fill([{key:field.key,status:'RESOLVED',value:'Choose not to self-identify'}]);
    }}))[0].result''', tab_id)
    assert value['filled'] == 0 and value['needs_input'] == ['Gender'], value
    assert page.evaluate('[submissions,proxyInputs]') == [0, 0]


def test_t059_readonly_guards_and_responsive_fallback(loaded_extension, extension_server):
    page, worker, tab_id, _ = t059_page(loaded_extension, extension_server)
    page.evaluate('''()=>{
      const form=document.querySelector('form'), original=document.querySelector('section');
      for(const attr of ['hidden','inert','aria-disabled','disabled','aria-hidden']){
        const clone=original.cloneNode(true);clone.setAttribute(attr,attr.startsWith('aria-')?'true':'');form.append(clone);
      }
      for(const css of ['display:none','opacity:0','visibility:hidden']){
        const clone=original.cloneNode(true);clone.style=css;form.append(clone);
      }
      form.insertAdjacentHTML('beforeend','<label>Readonly text<input readonly></label><label>Readonly email<input type=email aria-readonly=true></label><label>Readonly textarea<textarea readonly></textarea></label><label>Readonly combo<input role=combobox aria-readonly=true aria-haspopup=true></label>');
    }''')
    result = worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>({
      scan:await autoApplyContent.scan(),chat:await autoApplyContent.chatContext()
    })}))[0].result''', tab_id)
    assert len(result['scan']['fields']) == 4
    assert result['chat']['fields'] == []
    page.set_viewport_size(dict(width=480, height=800))
    mobile = worker.evaluate('async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>autoApplyContent.scan()}))[0].result', tab_id)
    assert len(mobile['fields']) == 4 and all(f['kind'] == 'select' for f in mobile['fields'])
    assert len({f['key'] for f in mobile['fields']}) == 4


@pytest.mark.parametrize('width,kind', [(1000, 'combobox'), (480, 'select')])
def test_t060_capture_derived_active_variants(loaded_extension, extension_server, config, width, kind):
    # Reuse the sanitized actual T059 capture, including the tiny opacity-zero
    # focus proxies and responsive native copies, rather than a generic mock.
    page, worker, tab_id, requests = t059_page(loaded_extension, extension_server)
    page.set_viewport_size(dict(width=width, height=850))
    page.locator('form').evaluate('''form=>form.insertAdjacentHTML('beforeend', `
      <div id="analysis-extras">
        <aside style="visibility:hidden" aria-hidden="true"><div><div><div><div>
          <label>Strictly necessary cookies<input type="checkbox" disabled></label>
          <label>Analytics cookies<input type="checkbox"></label>
        </div></div></div></div></aside>
        <label>Application consent<input id="styled-consent" type="checkbox" style="opacity:0"></label>
        <div role="switch" aria-label="Visible unsupported preference" aria-checked="false" tabindex="0">Preference</div>
        <div role="checkbox" aria-label="Already checked custom choice" aria-checked="true" tabindex="0">Checked</div>
        <label>Visible prerequisite<input disabled></label>
      </div>`)''')
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel = next(f for f in page.frames if '/sidepanel.html' in f.url)
    def analyze():
        panel.locator('#send-structure').click()
        expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
        files = (config.private/'snapshots').glob('*.json')
        return json.loads(max(files, key=lambda p: p.stat().st_mtime_ns).read_text(encoding='utf-8'))['diagnostic']
    report = analyze()
    active = [f for f in report['fields'] if f['active']]
    demographics = [f for f in active if f['kind'] in {'combobox', 'select'}]
    assert len(demographics) == 4 and {f['kind'] for f in demographics} == {kind}
    assert len({f['label'] for f in demographics}) == 4
    assert len(report['repair']['before']['findings']) == 7, report
    assert len(report['findings']) == 2, report
    assert report['repair']['filled'] == 3
    assert not any('cookies' in f['label'] or 'Already checked' in f['label'] for f in report['findings'])
    assert any(f['label'] == 'Application consent' for f in report['repair']['before']['findings'])
    unsupported = next(f for f in report['findings'] if f['label'] == 'Visible unsupported preference')
    assert not unsupported['detected'] and any('Scanner omitted' in n for n in unsupported['notes'])
    assert any('Disabled control' in n for f in report['findings'] if f['label'] == 'Visible prerequisite' for n in f['notes'])
    assert any(not f['active'] for f in report['fields'])
    page.locator('#analysis-extras').evaluate('e=>e.remove()')
    report = analyze()
    assert report['repair']['filled'] == 0
    assert report['findings'] == []
    assert sum(f['answered'] for f in report['fields'] if f['active']) == 3
    assert page.evaluate('submissions') == 0
    assert not any(path == '/ext/applied' for path, _, _ in requests)


def test_t060_fresh_analysis_singleflight_bound_repair_report(loaded_extension, extension_server, config):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.route('**/t060.html', lambda route: route.fulfill(path=str(FIXTURES/'t060.html'), content_type='text/html'))
    page.goto(server+'/t060.html')
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    expect(panel.locator('#send-structure')).to_have_text('Analyze page')
    expect(panel.locator('#page-analysis')).to_be_hidden()
    expect(panel.locator('#auto-send-structure')).to_be_enabled()
    # A second active application must not steal this sidebar's explicitly bound tab.
    other = context.new_page()
    other.goto(server+'/job.html')
    worker.evaluate("()=>{globalThis.analysisFetch=fetch;globalThis.analysisCalls=0;globalThis.fetch=async(url,...args)=>{if(String(url).endsWith('/ext/snapshot')){analysisCalls++;await new Promise(r=>globalThis.releaseAnalysis=r);}return analysisFetch(url,...args);};}")
    panel.locator('#send-structure').evaluate('e=>{e.click();e.click()}')
    expect(panel.locator('#send-structure')).to_have_text('Analyzing\u2026')
    expect(panel.locator('#send-structure')).to_be_disabled()
    expect(panel.locator('#send-structure')).to_have_attribute('aria-busy','true')
    assert worker.evaluate("async()=>{for(let i=0;i<600 && !globalThis.releaseAnalysis;i++)await new Promise(r=>setTimeout(r,10));return analysisCalls}") == 1
    worker.evaluate('()=>releaseAnalysis()')
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed')
    expect(panel.locator('#page-analysis')).to_be_visible()
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 1')
    expect(panel.locator('#analysis-details')).not_to_have_attribute('open','')
    panel.locator('#analysis-details summary').click()
    expect(panel.locator('#analysis-findings')).to_contain_text('Scanner omitted')
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(page.locator('#existing')).to_have_value('PRIVATE_TYPED_VALUE')
    assert not any(e in {'submit','next'} for e in page.evaluate('events'))
    payload = next(p for path,_,p in requests if path=='/ext/snapshot')
    assert payload['context_url'] == page.url
    proxy = next(f for f in payload['diagnostic']['fields'] if f['control'].get('id')=='proxy')
    assert proxy['detected'] and not proxy['answered'] and proxy['control']['opacity']==0
    assert proxy['control']['transformed'] and proxy['menu']['roles']==['none']
    assert proxy['control']['aria-controls']=='choices'
    assert any(not f['detected'] and f['control'].get('id')=='omitted' for f in payload['diagnostic']['fields'])
    saved = next((config.private/'snapshots').glob('127.0.0.1-*.json')).read_text(encoding='utf-8')
    assert 'PRIVATE_' not in json.dumps(payload)+saved
    assert any(path == '/ext/answers' for path,_,_ in requests)
    assert not any(path in {'/ext/write','/ext/chat','/ext/remember','/ext/applied'} for path,_,_ in requests)
    shots=ROOT/'.tmp/review/t060';shots.mkdir(parents=True,exist_ok=True)
    (shots/'latest-analysis.json').write_text(saved,encoding='utf-8')
    page.screenshot(path=str(shots/'fresh-analysis.png'))
    panel.locator('#analysis-details summary').click()
    page.set_viewport_size(dict(width=360,height=600))
    page.screenshot(path=str(shots/'compact-analysis-360.png'))
    assert panel.evaluate("()=>document.documentElement.scrollWidth<=innerWidth")
    # Structural navigation invalidates the details without another Autofill.
    page.locator('form').evaluate("e=>{const label=document.createElement('label');label.textContent='New application question';e.append(label)}")
    expect(panel.locator('#page-analysis')).to_be_hidden()
    expect(panel.locator('#snapshot-status')).to_be_empty()
    worker.evaluate('()=>{globalThis.fetch=analysisFetch}')


def test_t060_analysis_busy_preserves_scanner_handles(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server,key=KEY,autoSendStructure=False))
    page=context.new_page();page.goto(server+'/form.html')
    tab_id=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    fixture_delay(worker,hold=['write'])
    worker.evaluate('id=>{globalThis.heldFill=autoApplyAutofill(id);return true}',tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel=next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    value=worker.evaluate("async id=>{for(let i=0;i<1000;i++){const {tabResults={}}=await chrome.storage.session.get('tabResults');const r=tabResults[id];if(r?.writing?.length && !r.writingSettled)return r;await new Promise(r=>setTimeout(r,10));}throw Error('writing did not start')}",tab_id)
    worker.evaluate("id=>chrome.scripting.executeScript({target:{tabId:id},func:()=>{autoApplyContent.scan=()=>{throw Error('Analysis rescanned')};}})",tab_id)
    before=page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input,textarea,select'),e=>[e.value,e.checked])")
    panel.locator('#send-structure').click()
    expect(panel.locator('#error')).to_contain_text('busy')
    expect(panel.locator('#send-structure')).to_be_enabled()
    assert not any(path=='/ext/snapshot' for path,_,_ in requests)
    assert page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input,textarea,select'),e=>[e.value,e.checked])")==before
    pending=next(f for f in value['pending'] if f['label']=='Unresolved')
    key=pending['key'].split(':',1)[1]
    applied=worker.evaluate("async ({id,key})=>{const [frame]=await chrome.scripting.executeScript({target:{tabId:id},func:async key=>autoApplyContent.fill([{key,value:'Synthetic fact',status:'RESOLVED'}]),args:[key]});return frame.result}",dict(id=tab_id,key=key))
    assert applied['filled']==1
    expect(page.locator('#missing')).to_have_value('Synthetic fact')
    fixture_delay(worker,release=['write'])
    worker.evaluate('()=>heldFill')
    expect(page.locator('#writing')).to_have_value('I enjoy building useful local tools.')
    assert page.evaluate('submissions')==0


def test_t060_analysis_empty_allfilled_and_stale_response(loaded_extension, extension_server):
    context,worker,extension_id=loaded_extension
    server,requests=extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)',dict(server=server,key=KEY))
    page=context.new_page();page.goto(server+'/job.html')
    tab_id=worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id',page.url)
    worker.evaluate('id=>autoApplySidebar(id,"open")',tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel=next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    panel.locator('#send-structure').click()
    expect(panel.locator('#analysis-summary')).to_have_text('No form controls found.')
    page.locator('body').evaluate("e=>e.insertAdjacentHTML('afterbegin','<form><label>Full name<input value=synthetic></label></form>')")
    expect(panel.locator('#page-analysis')).to_be_hidden()
    panel.locator('#send-structure').click()
    expect(panel.locator('#analysis-summary')).to_have_text('Filled 0 \u00b7 0 need attention')
    worker.evaluate("()=>{globalThis.analysisFetch=fetch;globalThis.fetch=async(url,...args)=>{if(String(url).endsWith('/ext/snapshot'))await new Promise(r=>globalThis.releaseAnalysis=r);return analysisFetch(url,...args);};}")
    panel.locator('#send-structure').click()
    expect(panel.locator('#send-structure')).to_be_disabled()
    worker.evaluate("async()=>{for(let i=0;i<600 && !globalThis.releaseAnalysis;i++)await new Promise(r=>setTimeout(r,10))}")
    page.locator('label').evaluate("e=>e.firstChild.textContent='Different question'")
    worker.evaluate('()=>releaseAnalysis()')
    expect(panel.locator('#send-structure')).to_be_enabled()
    expect(panel.locator('#page-analysis')).to_be_hidden()
    assert all(path not in {'/ext/applied','/ext/answers','/ext/remember'} for path,_,_ in requests)
    worker.evaluate("()=>{globalThis.fetch=async(url,...args)=>String(url).endsWith('/ext/snapshot')?{ok:true,status:200,json:async()=>({ok:true,filename:'legacy.json'})}:analysisFetch(url,...args)}")
    panel.locator('#send-structure').click()
    expect(panel.locator('#error')).to_contain_text('requires the updated local dashboard')
    expect(panel.locator('#page-analysis')).to_be_hidden()


def t063_page(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY, autoSendStructure=False))
    page = context.new_page()
    page.route('**/t063.html', lambda route: route.fulfill(path=str(FIXTURES/'t063.html'), content_type='text/html'))
    page.goto(server+'/t063.html')
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
    panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
    return page, worker, tab_id, panel, requests


def test_t063_analyze_alone_recovers_failed_capture_widget(loaded_extension, extension_server, config):
    page, worker, tab_id, requests = t059_page(loaded_extension, extension_server)
    page.set_viewport_size(dict(width=1100, height=850))
    page.locator('.react-select').first.evaluate("e=>e.dataset.reset='once'")
    page.locator('form').evaluate('''form=>form.insertAdjacentHTML('afterbegin', `
      <label>Full name<input id="full" required></label>
      <label>Contact consent<input id="consent" type="checkbox" required></label>
      <label>Existing answer<input id="manual" value="PRIVATE_MANUAL"></label>
      <label>Unknown personal fact<input id="unknown" required></label>
      <label>Resume<input type="file"></label>
      <label>Why this role?<textarea></textarea></label>
      <button type="button" onclick="submissions++">Next</button>
      <button type="button" onclick="submissions++">Add education</button>`)''')
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#auto-advance')).to_be_enabled()
    panel.locator('#auto-advance').check()
    expect(panel.locator('#auto-send-structure')).to_be_enabled()
    panel.locator('#auto-send-structure').uncheck()
    panel.locator('#auto-send-structure').check()
    expect(panel.locator('#auto-send-structure')).to_be_enabled()
    assert not any(path=='/ext/answers' for path,_,_ in requests)
    expect(page.locator('#full')).to_be_empty()
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=30000)
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 5')
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(page.locator('#consent')).to_be_checked()
    expect(page.locator('#manual')).to_have_value('PRIVATE_MANUAL')
    expect(page.locator('#unknown')).to_be_empty()
    page.wait_for_timeout(450)
    assert page.locator('.react-select input[type=hidden]').evaluate_all('(es)=>es.map(e=>e.value)') == ['87423','','87467','87472']
    assert page.evaluate('choiceAttempts.application_form_equality_monitoring_gender') == 2
    assert page.locator('.react-select__single-value').all_text_contents() == [
        'Choose not to self-identify','Yes, I have a disability, or have had one in the past','I am not a protected veteran']
    assert page.evaluate('[submissions,proxyInputs,fallbackChanges,keys.includes("Enter")]') == [0,0,0,False]
    assert page.locator('input[type=file]').evaluate('e=>e.files.length') == 0
    expect(page.locator('textarea')).to_be_empty()
    payload = next(p for path,_,p in requests if path=='/ext/snapshot')
    saved = json.loads(max((config.private/'snapshots').glob('*.json'), key=lambda p:p.stat().st_mtime_ns).read_text(encoding='utf-8'))
    report = saved['diagnostic']
    assert report['repair']['filled'] == 5 and report['repair']['attempts'] == 2
    assert report['repair']['stopped'] == 'none'
    gender=next(f for f in report['fields'] if f['label']=='Gender')
    assert gender['menu']['observed'] and gender['menu']['count']==3
    assert gender['options']==['Male','Female','Choose not to self-identify']
    assert any(f['label']=='Gender' and not f['answered'] for f in report['repair']['before']['fields'])
    assert any(f['label']=='Gender' and f['answered'] for f in report['fields'])
    assert {'Unknown personal fact','Resume','Why this role?'} == {f['label'] for f in report['findings']}
    serialized = json.dumps(payload)+json.dumps(saved)
    assert all(secret not in serialized for secret in ['PRIVATE_MANUAL','Fixture Student','student@example.test','87423','87467'])
    assert not any(path in {'/ext/profile-sections','/ext/resume','/ext/write','/ext/cover-letter','/ext/applied','/ext/remember','/ext/chat','/ext/match'} for path,_,_ in requests)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=15000)
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 0')
    assert page.evaluate('choiceAttempts.application_form_equality_monitoring_gender') == 2


def t063_hold_answers(worker):
    worker.evaluate('''()=>{globalThis.repairFetch=fetch;globalThis.answerCalls=0;
      globalThis.fetch=async(url,...args)=>{if(String(url).endsWith('/ext/answers')){
        answerCalls++;await new Promise(r=>globalThis.releaseAnswers=r);
      }return repairFetch(url,...args);};}''')


def test_t063_analyze_reconciles_existing_autofill_pending_and_counts(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    page.locator('input[type=file],textarea').evaluate_all("es=>es.forEach(e=>e.closest('label').remove())")
    worker.evaluate('''()=>{globalThis.pendingFetch=fetch;let first=true;globalThis.fetch=async(url,...args)=>{
      const response=await pendingFetch(url,...args);
      if(first&&String(url).endsWith('/ext/answers')){first=false;const data=await response.json();
        for(const answer of data.answers){const field=JSON.parse(args[0].body).fields.find(f=>f.key===answer.key);
          if(field?.label==='Country'){answer.status='NEEDS_INPUT';answer.value=null;}}
        return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
      }return response;};}''')
    initial = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    worker.evaluate('()=>{globalThis.fetch=pendingFetch}')
    assert {f['label'] for f in initial['pending']} == {'Country', 'Unknown personal fact'}
    expect(panel.locator('#needs-input .prompt').filter(has_text='Country')).to_have_count(1)
    expect(page.locator('#country')).to_have_value('')
    for attempt in range(2):
        panel.locator('#send-structure').click()
        expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
        expect(page.locator('#country')).to_have_value('us')
        expect(page.locator('#manual')).to_have_value('PRIVATE_MANUAL')
        expect(page.locator('#unknown')).to_be_empty()
        current = worker.evaluate("async id=>(await chrome.storage.session.get('tabResults')).tabResults[id]", tab_id)
        assert [f['label'] for f in current['pending']] == ['Unknown personal fact']
        assert current['summary']['needs_input'] == ['Unknown personal fact']
        assert current['summary']['filled'] == initial['summary']['filled'] + 1
        assert len(current['completed']) == len(initial['completed']) + 1
        assert all(answer in current['completed'] for answer in initial['completed'])
        country = next(f for f in current['fields'] if f['label'] == 'Country')
        assert country['answered'] and country['options'] == ['United States', 'Canada']
        answer = next(f for f in current['completed'] if f['label'] == 'Country')
        assert answer['key'] == country['key'] and answer['value'] == 'United States'
        assert current['matched'] == initial['matched'] and current['writing'] == initial['writing']
        expect(panel.locator('#needs-input .prompt').filter(has_text='Country')).to_have_count(0)
        expect(panel.locator('#needs-input .prompt').filter(has_text='Unknown personal fact')).to_have_count(1)
        expect(panel.locator('#autofill .fill-count')).to_have_text(f"{initial['summary']['filled'] + 1} fields filled")
        expect(panel.locator('#analysis-summary')).to_contain_text(f'Filled {1 if attempt == 0 else 0}')
    payloads = [p for path, _, p in requests if path == '/ext/snapshot']
    assert all(secret not in json.dumps(payloads) for secret in ['PRIVATE_MANUAL', 'Fixture Student', 'student@example.test'])
    assert page.evaluate('actions') == []


def test_t063_reconciliation_rechecks_user_edit_during_report_read(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    page.locator('input[type=file],textarea').evaluate_all("es=>es.forEach(e=>e.closest('label').remove())")
    # Leave Country pending until explicit Analyze, then pause its result report
    # after the saved snapshot response, inside the serialized storage read.
    worker.evaluate('''()=>{globalThis.pendingFetch=fetch;globalThis.fetch=async(url,...args)=>{
      const response=await pendingFetch(url,...args);
      if(String(url).endsWith('/ext/answers')){const data=await response.json();
        for(const answer of data.answers){const field=JSON.parse(args[0].body).fields.find(f=>f.key===answer.key);
          if(field?.label==='Country'){answer.status='NEEDS_INPUT';answer.value=null;}}
        return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
      }return response;};}''')
    initial = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    worker.evaluate('''()=>{globalThis.reportRead=chrome.storage.session.get.bind(chrome.storage.session);let hold=false;
      chrome.storage.session.get=async keys=>{const result=await reportRead(keys);
        if(hold&&Array.isArray(keys)&&keys.includes('applicationAnswers')){
          hold=false;await new Promise(r=>globalThis.releaseRepairReport=r);
        }return result;};
      globalThis.fetch=async(url,...args)=>{const response=await pendingFetch(url,...args);
        if(String(url).endsWith('/ext/snapshot'))hold=true;return response;};}''')
    panel.locator('#send-structure').click()
    assert worker.evaluate('''async()=>{for(let i=0;i<1000&&!globalThis.releaseRepairReport;i++)await new Promise(r=>setTimeout(r,10));return Boolean(globalThis.releaseRepairReport)}''')
    expect(page.locator('#country')).to_have_value('us')
    page.locator('#country').press('ArrowDown')
    worker.evaluate('()=>releaseRepairReport()')
    expect(panel.locator('#send-structure')).to_be_enabled(timeout=15000)
    # Trusted edits invalidate the analysis UI while its request is in flight.
    expect(panel.locator('#snapshot-status')).not_to_have_text('Analyzed')
    expect(page.locator('#country')).to_have_value('Canada')
    current = worker.evaluate("async id=>(await chrome.storage.session.get('tabResults')).tabResults[id]", tab_id)
    assert current['summary']['filled'] == initial['summary']['filled']
    assert all(answer in current['completed'] for answer in initial['completed'])
    assert not any(f['label']=='Country' and f['value']=='United States' for f in current['completed'])
    assert any(f['label']=='Unknown personal fact' for f in current['pending'])


def t063_answers_pending(worker):
    return worker.evaluate('''async()=>{for(let i=0;i<500&&!globalThis.releaseAnswers;i++)await new Promise(r=>setTimeout(r,10));return answerCalls}''')


def test_t063_duplicate_and_busy_mutation_exclusion(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    t063_hold_answers(worker)
    panel.locator('#send-structure').evaluate('e=>{e.click();e.click()}')
    expect(panel.locator('#send-structure')).to_have_attribute('aria-busy','true')
    assert t063_answers_pending(worker) == 1
    panel.evaluate('''()=>{globalThis.secondAnalysis=chrome.runtime.sendMessage({type:'page-snapshot',tabId:Number(new URL(location.href).searchParams.get('tabId'))});return true;}''')
    for kind in ['autofill','autofill-skills','fill-answer','cover-letter','chat-replace','next-page']:
        result = panel.evaluate('(type)=>chrome.runtime.sendMessage({type,tabId:Number(new URL(location.href).searchParams.get("tabId"))})', kind)
        assert re.search('busy.*retry', result.get('error',''), re.I), result
    direct = worker.evaluate('id=>autoApplyAutofill(id)', tab_id)
    assert 'busy' in direct['error']
    expect(page.locator('#full')).to_be_empty()
    assert page.evaluate('actions') == []
    worker.evaluate('()=>{globalThis.fetch=repairFetch;releaseAnswers()}')
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    duplicate = panel.evaluate('()=>secondAnalysis')
    assert duplicate['ok'] and duplicate['summary'].startswith('Filled 4')
    assert len([1 for path,_,_ in requests if path=='/ext/snapshot']) == 1
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(page.locator('#email')).to_have_value('student@example.test')
    expect(page.locator('#consent')).to_be_checked()
    expect(page.locator('#country')).to_have_value('us')
    expect(page.locator('#manual')).to_have_value('PRIVATE_MANUAL')
    assert page.evaluate('actions') == []


@pytest.mark.parametrize('change',['document','step','edit'])
def test_t063_delayed_answer_stale_document_step_and_user_edit(loaded_extension, extension_server, change):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    t063_hold_answers(worker)
    panel.locator('#send-structure').click()
    assert t063_answers_pending(worker) == 1
    if change == 'document':
        page.reload()
        page.frame_locator('#autoapply-sidebar iframe').locator('#autofill').wait_for()
        panel = next(f for f in page.frames if '/sidepanel.html' in f.url)
    elif change == 'step':
        page.locator('form').evaluate('e=>{const fresh=e.cloneNode(true);e.replaceWith(fresh)}')
    else:
        page.locator('#full').fill('PRIVATE_NEW_MANUAL')
        page.locator('#full').fill('')
    worker.evaluate('()=>{globalThis.fetch=repairFetch;releaseAnswers()}')
    expect(panel.locator('#send-structure')).to_be_enabled(timeout=15000)
    worker.evaluate('async()=>{for(let i=0;i<100;i++)await new Promise(r=>setTimeout(r,10))}')
    expect(page.locator('#full')).to_be_empty()
    expect(page.locator('#email')).to_be_empty()
    expect(page.locator('#consent')).not_to_be_checked()
    expect(page.locator('#country')).to_have_value('')
    assert page.evaluate('actions') == []
    if change == 'edit':
        expect(panel.locator('#analysis-summary')).to_contain_text('Filled 0')
        expect(panel.locator('#analysis-summary')).to_contain_text('Repair stopped')
    else:
        assert not any(path=='/ext/snapshot' for path,_,_ in requests)
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    if change == 'edit':
        expect(page.locator('#full')).to_be_empty()
        expect(page.locator('#email')).to_have_value('student@example.test')
    else:
        expect(page.locator('#full')).to_have_value('Fixture Student')
    assert page.evaluate('actions') == []


def test_t063_partial_progress_survives_failed_retry(loaded_extension, extension_server, config):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    page.locator('#consent').evaluate("e=>e.addEventListener('change',()=>setTimeout(()=>e.checked=false,100))")
    worker.evaluate('''()=>{globalThis.repairFetch=fetch;let calls=0;globalThis.fetch=(url,...args)=>{
      if(String(url).endsWith('/ext/answers')&&++calls===2)return Promise.reject(new TypeError('fixture offline'));
      return repairFetch(url,...args);};}''')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 3')
    expect(panel.locator('#analysis-summary')).to_contain_text('Repair stopped')
    expect(page.locator('#consent')).not_to_be_checked()
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(page.locator('#country')).to_have_value('us')
    report=json.loads(next((config.private/'snapshots').glob('*.json')).read_text(encoding='utf-8'))['diagnostic']
    assert report['repair']['filled']==3
    assert next(f for f in report['findings'] if f['label']=='Contact consent')['status']=='verification_failed'
    expect(panel.locator('#send-structure')).to_be_enabled()
    assert page.evaluate('actions') == []


def test_t063_iframe_bound_repair_after_sidebar_reopens(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests = t063_page(loaded_extension, extension_server)
    page.locator('form').evaluate("e=>e.insertAdjacentHTML('afterend','<iframe id=application src=/frame.html></iframe>')")
    expect(page.frame_locator('#application').locator('#name')).to_be_empty()
    # Reopening the actual sidebar preserves the explicit tab binding even when another tab is active.
    panel.locator('#close').click()
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    other=loaded_extension[0].new_page()
    other.goto(extension_server[0]+'/job.html')
    page.frame_locator('#autoapply-sidebar iframe').locator('#send-structure').click()
    panel=page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=20000)
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(page.frame_locator('#application').locator('#name')).to_have_value('Embedded Student')
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 5')
    assert page.frame_locator('#application').locator('body').evaluate('()=>submissions')==0
    assert page.evaluate('actions')==[]
    assert not any(path=='/ext/applied' for path,_,_ in requests)


def test_t063_manual_unchecked_answer_is_preserved(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests=t063_page(loaded_extension, extension_server)
    page.locator('#consent').check()
    page.locator('#consent').uncheck()
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed',timeout=15000)
    expect(page.locator('#consent')).not_to_be_checked()
    expect(page.locator('#full')).to_have_value('Fixture Student')
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 3')
    payload=next(p for path,_,p in requests if path=='/ext/snapshot')
    assert next(f for f in payload['diagnostic']['fields'] if f['label']=='Contact consent')['repair_status']=='user_edited'
    assert page.evaluate('actions')==[]


def test_t063_analyze_waits_for_autofill_final_async_guard(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests=t063_page(loaded_extension, extension_server)
    # No writing/cover task: hold the ordinary final auto-advance preference read,
    # after the legacy main busy flag has already been cleared.
    page.locator('textarea').evaluate('e=>e.closest("label").remove()')
    page.locator('input[type=file]').evaluate('e=>e.closest("label").remove()')
    worker.evaluate('''id=>{globalThis.finalFetch=fetch;globalThis.fetch=async(url,...args)=>{
      if(String(url).endsWith('/ext/ping')){
        const {tabResults={}}=await chrome.storage.session.get('tabResults');
        if(tabResults[id]?.summary&&!tabResults[id].busy)await new Promise(r=>globalThis.releaseFinalFill=r);
      }return finalFetch(url,...args);
    };globalThis.finalFill=autoApplyAutofill(id);return true;}''',tab_id)
    assert worker.evaluate('''async()=>{for(let i=0;i<1000&&!globalThis.releaseFinalFill;i++)await new Promise(r=>setTimeout(r,10));return Boolean(globalThis.releaseFinalFill)}''')
    response=panel.evaluate('''()=>chrome.runtime.sendMessage({type:'page-snapshot',tabId:Number(new URL(location.href).searchParams.get('tabId'))})''')
    assert 'busy' in response.get('error',''),response
    assert not any(path=='/ext/snapshot' for path,_,_ in requests)
    worker.evaluate('()=>{globalThis.fetch=finalFetch;releaseFinalFill()}')
    worker.evaluate('()=>finalFill')
    assert page.evaluate('actions')==[]
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed',timeout=10000)
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 0')


def test_t063_failed_checkbox_does_not_activate_adjacent_navigation(loaded_extension, extension_server):
    page, worker, tab_id, panel, requests=t063_page(loaded_extension, extension_server)
    page.locator('#consent').evaluate('''e=>{
      window.checkboxAttempts=0;
      e.addEventListener('click',event=>{checkboxAttempts++;event.preventDefault()});
      const label=e.closest('label');label.htmlFor=e.id;label.after(e);
      const next=document.createElement('button');next.type='button';next.textContent='Next';
      next.onclick=()=>actions.push('unsafe checkbox fallback');e.after(next);
    }''')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed',timeout=20000)
    expect(panel.locator('#analysis-summary')).to_contain_text('Filled 3')
    expect(page.locator('#consent')).not_to_be_checked()
    expect(page.locator('#country')).to_have_value('us')
    assert page.evaluate('checkboxAttempts')>=2
    payload=next(p for path,_,p in requests if path=='/ext/snapshot')
    assert next(f for f in payload['diagnostic']['fields'] if f['label']=='Contact consent')['repair_status']=='verification_failed'
    assert page.evaluate('actions')==[]


def test_t065_direct_mv3_lifecycle_and_queue_visuals():
    """Bounded Node/CDP test against an actual isolated unpacked extension.

    The fixture owns only local HTTP stubs and its own Chrome profile. No
    Playwright driver teardown, employer traffic, provider calls or live data.
    """
    import os
    import shutil
    import subprocess
    node = shutil.which('node')
    browsers = Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'
    if not node or not any(browsers.glob('chromium-*/chrome-win64/chrome.exe')):
        pytest.skip('Node and full local Chromium are required')
    result = subprocess.run([node, str(FIXTURES / 't065_verify.cjs')], cwd=ROOT,
                            capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((ROOT / '.tmp/review/t065/direct-result.json').read_text())
    assert report['ok'] and len(report['checks']) >= 74
