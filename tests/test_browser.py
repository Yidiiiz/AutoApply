import asyncio
import json
import os
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from autoapply.applications import GenericApplicationAdapter, AshbyAdapter
from autoapply.browser import Browser, page_condition
from autoapply.config import ROOT
from autoapply.control import Controller
from autoapply.engine import Engine
from autoapply.models import State, Answer

pytestmark = pytest.mark.browser


@pytest.fixture
def site():
    submissions = []
    html = (Path(__file__).parent / "fixtures/application.html").read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            submissions.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/", submissions
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def browser(config, monkeypatch):
    value = Browser(config)
    await value.start()
    yield value
    await value.close()


async def test_greenhouse_removes_input_but_attachment_and_network_are_verified(browser, config):
    from autoapply.models import Question
    page = await browser.new_page()
    html = '''<form><div class="file-upload"><input type="file" data-autoapply-field="resume"></div></form>
    <script>document.querySelector('input').onchange=async e=>{
      const file=e.target.files[0], root=e.target.parentElement;
      e.target.remove(); root.innerHTML='<div role="progressbar"></div>';
      const response=await fetch('https://boards-production.s3.amazonaws.com/',{method:'POST',body:file});
      await response.text(); if(response.ok) root.innerHTML='<div class="file-upload__filename">'+file.name+'<button aria-label="Remove file" type="button"></button></div>';
    };</script>'''
    async def route(r):
        if 's3.amazonaws.com' in r.request.url:
            await r.fulfill(status=200, headers={'Access-Control-Allow-Origin':'*'})
        else:
            await r.fulfill(content_type='text/html', body=html)
    await page.route('**/*', route)
    await page.goto('https://job-boards.greenhouse.io/fixture')
    adapter = GenericApplicationAdapter(page)
    q = Question('resume', 'Resume', 'file', True)
    adapter.controls[q.key] = (page.main_frame, ['resume'])
    await adapter.upload_documents(q, config.resume)
    assert await page.locator('input[type=file]').count() == 0
    assert await browser.uploads_ready(page, adapter.uploads, timeout_seconds=2), json.dumps(page._autoapply_uploads.result)
    assert not await adapter.validate()
    assert GenericApplicationAdapter(page).uploads is adapter.uploads
    await page.locator('.file-upload__filename').evaluate('e=>e.remove()')
    assert await adapter.validate() == ['Required uploaded document is no longer attached']


@pytest.mark.parametrize('retained', [True, False])
async def test_compact_country_requires_exact_selected_option(browser, retained):
    from autoapply.models import Question
    from autoapply.applications import UnsupportedForm
    page = await browser.new_page()
    await page.set_content('''<form><div><span id="compact"></span><div><input role="combobox" data-autoapply-field="country"></div></div>
    <div role="listbox" hidden><div role="option" aria-selected="false">United States +1</div></div></form>
    <script>const input=document.querySelector('input'),list=document.querySelector('[role=listbox]'),option=document.querySelector('[role=option]');
    input.onclick=()=>list.hidden=false;
    option.onclick=()=>{option.setAttribute('aria-selected','true');document.querySelector('#compact').textContent='+1';list.hidden=true};
    input.onkeydown=e=>{if(e.key==='Escape')list.hidden=true};</script>''')
    adapter = GenericApplicationAdapter(page)
    q = Question('country','Country','combobox',True,['United States +1'])
    adapter.controls[q.key] = (page.main_frame,['country'])
    if not retained:
        await page.evaluate("document.querySelector('[role=option]').onclick=()=>{document.querySelector('#compact').textContent='+1';document.querySelector('[role=listbox]').hidden=true}")
    if retained:
        await adapter.answer_question(q, Answer('United States +1','user_confirmed'))
    else:
        with pytest.raises(UnsupportedForm, match='Cannot verify custom dropdown selection'):
            await adapter.answer_question(q, Answer('United States +1','user_confirmed'))
    assert await page.get_by_role('listbox').count() == 0


async def test_already_open_combobox_is_not_toggled_closed(browser):
    from autoapply.models import Question
    page = await browser.new_page()
    await page.set_content('''<div><span id="selected"></span><div><input role="combobox" aria-expanded="true" data-autoapply-field="choice"></div></div>
    <div role="listbox"><div role="option">Choice</div></div>
    <script>const input=document.querySelector('input'),list=document.querySelector('[role=listbox]');
    input.onclick=()=>{list.hidden=!list.hidden;input.setAttribute('aria-expanded',String(!list.hidden))};
    document.querySelector('[role=option]').onclick=()=>{document.querySelector('#selected').textContent='Choice';list.hidden=true;input.setAttribute('aria-expanded','false')};</script>''')
    adapter = GenericApplicationAdapter(page)
    q = Question('choice','Choice','combobox',True,['Choice'])
    adapter.controls[q.key] = (page.main_frame,['choice'])
    await adapter.answer_question(q,Answer('Choice','user_confirmed'))


async def test_reconstruction_searches_saved_dropdown_option(browser):
    import hashlib
    import json
    from autoapply.answers import scope_for
    page = await browser.new_page()
    await page.set_content('''<form><label for="school">School</label><div><span id="selected"></span><div><input id="school" role="combobox" aria-expanded="false"></div></div>
    <div role="listbox" hidden></div></form><script>
    const input=document.querySelector('input'),list=document.querySelector('[role=listbox]');
    input.onclick=()=>{list.hidden=false;input.setAttribute('aria-expanded','true')};
    input.oninput=()=>{list.innerHTML=input.value==='Saved University'?'<div role="option">Saved University</div>':'';
      if(list.firstChild)list.firstChild.onclick=()=>{document.querySelector('#selected').textContent='Saved University';input.value='';list.hidden=true;input.setAttribute('aria-expanded','false')}};
    input.onkeydown=e=>{if(e.key==='Escape'){list.hidden=true;input.setAttribute('aria-expanded','false')}};
    </script>''')
    adapter = GenericApplicationAdapter(page)
    app = {'id':1, 'company':'Example'}
    key = hashlib.sha256(b'0|School|combobox|0').hexdigest()[:24]
    adapter.answer_hints[key] = {'raw_question':'School','scope':scope_for('School',app),'answer':json.dumps('Saved University')}
    questions = await adapter.get_questions(app)
    assert questions[0].options == ['Saved University']
    await adapter.answer_question(questions[0], Answer('Saved University','verified_document'))
    assert await page.locator('#selected').inner_text() == 'Saved University'


async def test_searchable_long_menu_is_filtered_before_selection(browser):
    from autoapply.models import Question
    page = await browser.new_page()
    await page.set_content('''<div><span id="selected"></span><div><input role="combobox" aria-expanded="true" data-autoapply-field="month"></div></div>
    <div role="listbox"></div><script>
    const input=document.querySelector('input'),list=document.querySelector('[role=listbox]');
    list.innerHTML=Array.from({length:12},(_,i)=>'<div role="option">'+(i===4?'May':'Other '+i)+'</div>').join('');
    input.oninput=()=>{window.filtered=true;list.innerHTML='<div role="option">May</div>';list.firstChild.onclick=()=>{document.querySelector('#selected').textContent='May';list.hidden=true;input.value=''}};
    </script>''')
    adapter = GenericApplicationAdapter(page)
    q = Question('month','Month','combobox',True,['May'])
    adapter.controls[q.key] = (page.main_frame,['month'])
    await adapter.answer_question(q,Answer('May','verified_document'))
    assert await page.evaluate('window.filtered') is True
    assert await page.locator('#selected').inner_text() == 'May'


async def prepare(config, db, browser, listing, site, suffix=""):
    url, submissions = site
    db.ingest(replace(listing, url=url + suffix), config)
    engine = Engine(config, db, browser)
    await engine.process_one()
    app = db.application(1)
    assert app["status"] == State.MANUAL_REVIEW, app
    assert app["session_preserved"] and not app["retry_allowed"]
    assert app["error_category"] == "INPUT_REQUIRED"
    assert not await engine.resume_manual(1)  # Unanswered facts cannot be bypassed.
    pending = engine.control.pending()
    assert len(pending) == 1, pending
    assert pending[0]["raw_question"] == "Describe a technical challenge"
    assert not submissions
    engine.control.answer(pending[0]["id"], "I debugged a test fixture and verified the fix with regression tests.")
    assert db.application(1)["status"] == State.MANUAL_REVIEW
    return engine


async def test_full_flow_pauses_answers_uploads_submits_and_archives(config, db, browser, listing, site):
    engine = await prepare(config, db, browser, listing, site)
    await engine.resume_manual(1)
    app = db.application(1)
    assert app["status"] == State.SUBMITTED, app
    assert app["submit_intent_at"] and app["confirmation_text"] and app["resume_sha256"]
    assert len(site[1]) == 1
    assert site[1][0]["first"] == "Test"
    assert site[1][0]["sponsorship"] == "No"
    assert site[1][0]["auth"] == "yes"
    assert site[1][0]["resume"] == "resume.pdf"
    assert site[1][0]["marketing"] is False
    assert list((config.private / "application_history").glob("*/*/confirmation.json"))
    assert not await engine.process_one()


async def test_autosubmit_off_revalidates_before_retry(config, db, browser, listing, site):
    config.data["application"]["auto_submit"] = False
    engine = await prepare(config, db, browser, listing, site)
    await engine.resume_manual(1)
    assert db.application(1)["status"] == State.READY
    assert not site[1]
    engine.control.command("autosubmit on")
    engine.control.command("retry 1")
    await engine.process_one()
    assert db.application(1)["status"] == State.SUBMITTED
    assert len(site[1]) == 1


async def test_fill_only_keeps_ready_tab_and_refuses_fanout(config, db, browser, listing, site):
    db.set_setting('auto_submit', False)
    engine = await prepare(config, db, browser, listing, site)
    engine.fill_only = True
    db.execute('DELETE FROM manual_requests')
    await engine.resume_manual(1)
    assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT'
    assert not engine.retained_pages[1].is_closed()
    assert not db.application(1)['submit_intent_at']
    assert (config.private / 'fill-only-checkpoints/1.json').exists()
    db.ingest(replace(listing, url=site[0] + 'second'), config)
    with pytest.raises(RuntimeError, match='BATCH_TAB_LIMIT_VIOLATION'):
        await engine.process_one(2)
    assert db.application(2)['attempts'] == 0
    assert not site[1]
    from autoapply.fill_batch import verify_reconstruction
    record = await verify_reconstruction(engine, 1)
    assert record['reconstruction_verified']
    assert record['readiness'] == 'RECONSTRUCTION_VERIFIED'
    assert db.application(1)['application_state'] == 'READY_TO_SUBMIT'
    assert not db.application(1)['session_preserved']
    assert not engine.retained_pages
    assert not site[1]


@pytest.mark.parametrize('change', ['stale', 'closed', 'unknown'])
async def test_listing_guard_immediately_before_submit(config, db, browser, listing, site, monkeypatch, change):
    from datetime import datetime, timedelta, timezone
    engine = await prepare(config, db, browser, listing, site)
    original = browser.screenshot
    async def screenshot(page, folder, name):
        await original(page, folder, name)
        if name.startswith('pre-submit'):
            if change == 'closed':
                await page.locator('body').evaluate("el => el.insertAdjacentHTML('afterbegin', '<h2>This job is no longer available</h2>')")
            else:
                posted = (datetime.now(timezone.utc)-timedelta(days=30,seconds=1)).isoformat() if change == 'stale' else None
                db.execute('UPDATE jobs SET posted_at=? WHERE id=1',(posted,))
    monkeypatch.setattr(browser,'screenshot',screenshot)
    await engine.resume_manual(1)
    app = db.application(1)
    assert app['status'] == ('CLOSED' if change == 'closed' else 'INVALID'), app
    assert app['submit_intent_at'] is None
    assert not site[1]
    assert db.claim() is None


async def test_uncertain_submission_never_retried(config, db, browser, listing, site):
    config.data["application"]["confirmation_timeout_seconds"] = 2
    engine = await prepare(config, db, browser, listing, site, "?no-confirmation")
    await engine.resume_manual(1)
    assert db.application(1)["status"] == State.MANUAL_REVIEW
    assert len(site[1]) == 1
    db.recover()
    with pytest.raises(ValueError):
        db.retry(1)
    assert not await engine.process_one()
    assert len(site[1]) == 1


async def test_persistent_login_survives_browser_restart(browser, site):
    page = await browser.new_page()
    await browser.navigate(page, site[0])
    assert any(c["name"] == "fixture_session" for c in await browser.context.cookies())
    await browser.close()
    await browser.start()
    assert any(c["name"] == "fixture_session" and c["value"] == "persisted" for c in await browser.context.cookies())


@pytest.mark.parametrize("html,status", [
    ("<body>You have already applied to this position</body>", State.ALREADY_APPLIED),
    ("<body>This job is no longer available</body>", State.CLOSED),
    ("<body>You have already applied. This job is no longer available</body>", State.ALREADY_APPLIED),
    ('<body>Job closed. <label>Password <input type="password"></label></body>', State.AUTH_REQUIRED),
    ("<body>Verify you are human</body>", State.MANUAL_REVIEW),
    ("<body>We couldn't submit your application. Your application submission was flagged as possible spam.</body>", State.MANUAL_REVIEW),
    ('<body><label>Password <input type="password"></label></body>', State.AUTH_REQUIRED),
])
async def test_browser_conditions(browser, html, status):
    page = await browser.new_page()
    await page.set_content(html)
    assert (await page_condition(page))[0] == status
    await page.close()


async def test_pause_just_before_submission_prevents_click(config, db, browser, listing, site, monkeypatch):
    engine = await prepare(config, db, browser, listing, site)
    original = browser.screenshot
    async def screenshot(page, folder, name):
        await original(page, folder, name)
        if name.startswith("pre-submit"):
            db.set_setting("paused", True)
    monkeypatch.setattr(browser, "screenshot", screenshot)
    await engine.resume_manual(1)
    assert not site[1]
    assert db.application(1)["submit_intent_at"] is None


async def test_clickthrough_legal_attestation_becomes_question(browser):
    page = await browser.new_page()
    await page.set_content('<body><form><label>First Name<input required></label><p>By submitting this application, you certify that all answers are accurate.</p><button>Submit application</button></form></body>')
    adapter = GenericApplicationAdapter(page)
    questions = await adapter.get_questions({"id": 1})
    legal = [q for q in questions if q.kind == "attestation"]
    assert len(legal) == 1 and legal[0].required
    assert legal[0].scope == "application:1"


async def test_greenhouse_proxy_lazy_dropdown_and_resume(browser, config):
    page = await browser.new_page()
    await page.set_content((Path(__file__).parent / "fixtures/greenhouse-controls.html").read_text())
    adapter = GenericApplicationAdapter(page)
    questions = await adapter.get_questions({"id": 1})
    assert len(questions) == 2
    school, resume = questions
    assert school.label == "School" and school.required and not school.options
    assert resume.label == "Resume" and resume.required
    await adapter.answer_question(school, Answer("Example University", "profile"))
    await adapter.upload_documents(resume, config.resume)
    assert not await adapter.validate()


async def test_ashby_waits_for_application_tab_to_render(browser):
    page = await browser.new_page()
    await page.set_content('''<body><main>Loading</main><script>
      setTimeout(() => {
        const tab = document.createElement('a'); tab.setAttribute('role','tab');
        tab.textContent='Application'; tab.href='#application';
        tab.onclick = () => document.querySelector('main').innerHTML = '<form><label>Name<input required></label></form>';
        document.body.appendChild(tab);
      }, 200);
    </script></body>''')
    adapter = AshbyAdapter(page)
    await adapter.begin()
    questions = await adapter.get_questions({'id':1})
    assert len(questions) == 1 and questions[0].label == 'Name'


async def test_ashby_radio_uses_question_not_first_option(browser):
    page = await browser.new_page()
    await page.set_content('''<body><main>
      <div class="ashby-application-form-autofill-pane"><input type="file"></div>
      <div data-field-entry-id="fixture"><fieldset><label>Can you attend the office?</label>
        <label><input type="radio" name="office">Yes</label>
        <label><input type="radio" name="office">No</label>
      </fieldset></div></main></body>''')
    questions = await AshbyAdapter(page).get_questions({'id':1})
    assert len(questions) == 1
    assert questions[0].label == 'Can you attend the office?'
    assert questions[0].required and questions[0].value == ''
    assert questions[0].options == ['Yes','No']


async def test_engine_presubmit_challenge_resumes_without_navigation(config, db, browser, listing, site, monkeypatch):
    config.data['application']['confirmation_timeout_seconds'] = 1
    engine = await prepare(config, db, browser, listing, site)
    original = GenericApplicationAdapter.answer_question
    async def answer_and_challenge(adapter, question, answer):
        await original(adapter, question, answer)
        if question.kind == 'textarea':
            await adapter.page.evaluate("() => {const e=document.createElement('div'); e.id='challenge'; e.setAttribute('role','alert'); e.textContent='Verify you are human'; document.body.append(e)}")
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', answer_and_challenge)
    await engine.resume_manual(1)
    held = engine.handoff.pages[1]
    assert not site[1] and not held.is_closed()
    assert db.application(1)['submit_intent_at'] is None
    assert await held.locator('#first').input_value() == 'Test'
    assert await held.locator('#resume').evaluate('e => e.files.length') == 1
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', original)
    await held.locator('#challenge').evaluate('e => e.remove()')
    navigations = []
    held.on('framenavigated', lambda frame: navigations.append(frame.url))
    await engine.resume_manual(1)
    assert not navigations
    assert len(site[1]) == 1 and db.application(1)['status'] == 'SUBMITTED'


async def test_removed_resume_is_caught_at_validation(browser, config):
    page = await browser.new_page()
    await page.set_content('<label>Resume<input type="file" required></label><button>Submit</button>')
    adapter = GenericApplicationAdapter(page)
    question = (await adapter.get_questions({'id': 1}))[0]
    await adapter.upload_documents(question, config.resume)
    assert not await adapter.validate()
    await page.locator('input').set_input_files([])
    assert await adapter.validate()



@pytest.mark.browser
async def test_same_page_resume_does_not_reselect_attachment(browser, config):
    from autoapply.applications import GenericApplicationAdapter
    from autoapply.models import Question
    page = await browser.new_page()
    await page.set_content('<input type="file" data-autoapply-field="resume">')
    question = Question('resume', 'Resume', 'file', True)
    adapter = GenericApplicationAdapter(page)
    adapter.controls[question.key] = (page.main_frame, ['resume'])
    await page.evaluate("window.selections=0; document.querySelector('input').addEventListener('change',()=>window.selections++)")
    await adapter.upload_documents(question, config.resume)
    resumed = GenericApplicationAdapter(page)
    resumed.controls[question.key] = (page.main_frame, ['resume'])
    await resumed.upload_documents(question, config.resume)
    assert await page.evaluate('window.selections') == 1
    assert not await resumed.validate()

async def test_fill_only_closed_candidate_releases_before_next(config, db, listing, monkeypatch):
    from unittest.mock import AsyncMock
    for suffix in ('one', 'two'):
        db.ingest(replace(listing, url='https://jobs.lever.co/example/'+suffix), config)
    db.set_setting('auto_submit', False)
    engine=Engine(config, db, fill_only=True)
    engine.browser.navigate=AsyncMock(return_value=(State.CLOSED,'Listing closed'))
    engine.security_gate=AsyncMock(return_value=False)
    try:
        for app_id in (1,2):
            await engine.process_one(app_id)
            assert db.application(app_id)['status']=='CLOSED'
            assert not engine.browser.context.pages
            assert (config.private/f'fill-only-checkpoints/{app_id}.json').exists()
        assert engine.browser.application_tabs_opened==2
        assert engine.browser.max_tabs_observed==1
    finally:
        await engine.close()

async def test_checkbox_group_validation_preserves_consent_and_native_errors(browser):
    from autoapply.security import SubmissionClassifier, PreSubmitState
    page=await browser.new_page()
    await page.set_content('''<form><fieldset><legend>Select up to 3 interests</legend>
      <label><input type="checkbox" required checked>A</label>
      <label><input type="checkbox" required checked>B</label>
      <label><input type="checkbox" required>C</label>
      <label><input type="checkbox" required>D</label>
      </fieldset><label><input id="consent" type="checkbox" required>Consent</label>
      <input id="email" aria-label="Email" type="email" value="student@example.test" required>
      <button>Submit application</button></form>''')
    adapter=GenericApplicationAdapter(page)
    questions=await adapter.get_questions({'id':1})
    choices=[q for q in questions if q.label in ['A','B','C','D']]
    assert len(choices)==4 and all(not q.required for q in choices)
    assert await adapter.validate()  # Ordinary required consent still fails.
    await page.locator('#consent').check()
    assert not await adapter.validate()
    result,_=await SubmissionClassifier().pre_submit(page,adapter)
    assert result==PreSubmitState.NO_SECURITY_BLOCK
    for box in await page.locator('fieldset input').all(): await box.check()
    assert any('group' in issue for issue in await adapter.validate())
    await page.locator('fieldset input').last.uncheck()
    await page.locator('#email').fill('invalid')
    assert await adapter.validate()
