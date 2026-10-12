import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoapply.applications import GenericApplicationAdapter, InventoryChanged
from autoapply.config import Config
from autoapply.engine import Engine
from autoapply.fill_batch import checkpoint, release_checkpoint
from autoapply.fill_runner import run_selected
from autoapply.form_progress import inventory_schema
from autoapply.models import Question
from autoapply.runtime import ProcessLock
from test_browser_startup import Context, Resources, launches


def test_atomic_history_replace_recovers_without_rewriting_payload(tmp_path, monkeypatch):
    import autoapply.archive as archive
    path = tmp_path / 'history.json'
    path.write_text('old complete record', encoding='utf-8')
    original_replace = archive.os.replace
    attempts, sleeps = [], []
    def temporarily_locked(source, target):
        attempts.append(source)
        assert source.read_text(encoding='utf-8') == 'new complete record'
        assert path.read_text(encoding='utf-8') == 'old complete record'
        if len(attempts) < 3:
            raise PermissionError('temporary local sharing denial')
        return original_replace(source, target)
    monkeypatch.setattr(archive.os, 'replace', temporarily_locked)
    monkeypatch.setattr(archive.time, 'sleep', sleeps.append)
    archive.atomic_text(path, 'new complete record')
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert sleeps == [.05, .1]
    assert path.read_text(encoding='utf-8') == 'new complete record'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('error', [PermissionError, OSError])
def test_atomic_history_denial_keeps_old_record_and_cleans_temporary(tmp_path, monkeypatch, error):
    import autoapply.archive as archive
    path = tmp_path / 'history.json'
    path.write_text('old complete record', encoding='utf-8')
    attempts, sleeps = [], []
    def denied(source, target):
        attempts.append(source)
        raise error('persistent local failure')
    monkeypatch.setattr(archive.os, 'replace', denied)
    monkeypatch.setattr(archive.time, 'sleep', sleeps.append)
    with pytest.raises(error):
        archive.atomic_text(path, 'new complete record')
    assert len(attempts) == (7 if error is PermissionError else 1)
    assert len(sleeps) == (6 if error is PermissionError else 0)
    assert path.read_text(encoding='utf-8') == 'old complete record'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('stage', ['acquire', 'capture'])
@pytest.mark.parametrize('recover', [True, False])
async def test_inventory_timeout_uses_only_existing_preapplication_recovery(config, db, listing, launches, monkeypatch, stage, recover):
    from autoapply.browser import Browser, BrowserStartFailed
    db.ingest(listing, config)
    launches.contexts.extend(Context() for _ in range(3))
    calls = []
    def inventory_timeout(owner, *args):
        calls.append(owner)
        # Each failed startup capture is followed by an independent successful
        # cleanup capture. Exhaustion does not authorize unverified cleanup.
        if len(calls) == 1 or (not recover and (stage == 'acquire' or len(calls) % 2)):
            raise subprocess.TimeoutExpired('read-only fixture CIM', 30)
    monkeypatch.setattr(Resources, stage, inventory_timeout)
    browser = Browser(config)
    try:
        if recover:
            await browser.start()
            assert browser.health_ready and launches.sleeps == [4]
            assert len(launches.owners) == 2
        else:
            with pytest.raises(BrowserStartFailed):
                await browser.start()
            assert launches.sleeps == [4, 4]
            assert len(launches.owners) == 3
        old_owners = launches.owners[:-1] if recover else launches.owners
        assert all(owner.reaped and owner.released for owner in old_owners)
        assert browser.application_tabs_opened == 0 and not browser.leases
        assert db.application(1)['attempts'] == 0
        assert not db.application(1)['submit_intent_at']
    finally:
        await browser.close()


async def test_inventory_timeout_with_unverified_cleanup_never_relaunches(config, launches, monkeypatch):
    from autoapply.browser import Browser, BrowserStartFailed
    from autoapply.browser_resources import BrowserOwnershipError
    launches.contexts.append(Context())
    def timed_out(owner, *args):
        raise subprocess.TimeoutExpired('read-only fixture CIM', 30)
    def unverified(owner):
        raise BrowserOwnershipError('cleanup cannot prove exit')
    monkeypatch.setattr(Resources, 'capture', timed_out)
    monkeypatch.setattr(Resources, 'reap', unverified)
    browser = Browser(config)
    try:
        with pytest.raises(BrowserStartFailed, match='cleanup'):
            await browser.start()
        assert len(launches.drivers) == len(launches.owners) == 1
        assert not launches.sleeps and browser.resources is launches.owners[0]
        assert browser.application_tabs_opened == 0
    finally:
        # Restore a provable fake cleanup; never leave this fixture unresolved.
        monkeypatch.setattr(Resources, 'capture', lambda *args: None)
        monkeypatch.setattr(Resources, 'reap', lambda owner: setattr(owner, 'reaped', True))
        await browser.close()


async def test_inventory_timeout_after_application_page_never_replays(config, launches):
    from autoapply.browser import Browser
    launches.contexts.append(Context())
    browser = Browser(config)
    calls = []
    async def timed_out():
        calls.append(1)
        raise subprocess.TimeoutExpired('read-only fixture CIM', 30)
    try:
        page = await browser.new_page()
        await browser.release_page(page)
        with pytest.raises(subprocess.TimeoutExpired):
            await browser._startup_recovery(timed_out)
        assert calls == [1] and len(launches.drivers) == 1
        assert not launches.sleeps and browser.application_tabs_opened == 1
    finally:
        await browser.close()


POSTING = '''<script type="application/ld+json">{"@type":"JobPosting",
"title":"Software Engineering Intern Summer 2027",
"description":"Software engineering internship for undergraduate students.",
"hiringOrganization":{"name":"Example Company"},
"jobLocation":{"address":{"addressLocality":"New York","addressRegion":"NY","addressCountry":"US"}}}</script>
<h1>Software Engineering Intern Summer 2027</h1>'''
FORM = '''<form><label>First Name<input id="first" name="first_name" required></label>
<label>Last Name<input id="last" required></label>
<label>Resume<input name="resume" type="file" required onchange="this.parentElement.insertAdjacentHTML('afterend','<span>resume.pdf</span>')"></label>
<button type="submit">Submit application</button></form>
<script>window.finalClicks=0;document.querySelector('button').onclick=()=>window.finalClicks++;
document.querySelector('form').onsubmit=e=>{e.preventDefault();fetch('/submit',{method:'POST'})}</script>'''


async def route_engine(engine, bodies):
    requests = []
    await engine.browser.start()
    async def route(r):
        if r.request.method != 'GET':
            requests.append(r.request.url)
        body = bodies(r.request.url) if callable(bodies) else bodies
        await r.fulfill(content_type='text/html', body=body)
    await engine.browser.context.route('**/*', route)
    await engine.browser.context.route('**/*', engine.fill_invariant.route)
    return requests


async def test_readiness_starts_stability_after_loading_changes_between_observations(config):
    from autoapply.browser import Browser
    browser = Browser(config)
    try:
        await readiness_loading_race(browser)
    finally:
        await browser.close()


async def readiness_loading_race(browser):
    page = await browser.new_page()
    await page.set_content('''<form><input name="first"><button type="button">Continue</button></form>
    <script>window.clicks=0;window.finalClicks=0;
    document.querySelector('button').onclick=()=>{window.clicks++;
      document.body.innerHTML='<div role="progressbar">Loading</div>'};</script>''')
    adapter = GenericApplicationAdapter(page)
    original_sample = adapter.transition_sample
    async def sample_then_finish_loading():
        sample = await original_sample()
        if sample['loading']:
            # Model a real hydration race: sampled busy state, then new DOM
            # before the separate step-signature observation completes.
            await page.evaluate('''() => document.body.innerHTML=
              '<form><input name="last"><button onclick="window.finalClicks++">Submit application</button></form>' ''')
        return sample
    adapter.transition_sample = sample_then_finish_loading
    await asyncio.wait_for(adapter.advance(), timeout=4)
    assert await page.evaluate('[window.clicks,window.finalClicks]') == [1, 0]
    assert await page.locator('input[name=last]').is_visible()


def add_application(config, db, listing, url='http://127.0.0.1:8123/application'):
    db.set_setting('auto_submit', False)
    return db.ingest(replace(listing, url=url), config)[0]


@pytest.mark.parametrize('changed', ['required', 'kind', 'options', 'max_length', 'min_selections'])
def test_conditional_identity_includes_constraints(changed):
    q = Question('field', 'Question')
    values = {'required': True, 'kind': 'select', 'options': ['Yes'], 'max_length': 10, 'min_selections': 1}
    assert inventory_schema([q]) != inventory_schema([replace(q, **{changed: values[changed]})])
    assert inventory_schema([q]) == inventory_schema([replace(q, value='private value')])


def test_default_config_requires_submission_opt_in(tmp_path):
    assert Config(tmp_path)['application']['auto_submit'] is False


@pytest.mark.parametrize('rejection', ['fresh_check', 'changed_resume'])
async def test_upload_rejection_replaces_prior_ready_evidence(config, db, listing, monkeypatch, rejection):
    from autoapply.operations import FileSnapshot
    add_application(config, db, listing)
    engine = Engine(config, db, fill_only=True)
    try:
        page = await engine.browser.new_page()
        tracker = page._autoapply_uploads
        prior = dict(ready=True, category='UPLOAD_READY')
        tracker.result = prior
        tracker.receipt = {'ready': True}
        engine.browser.observation(page)['upload_result'] = prior
        page._autoapply_document_snapshots = {'resume': FileSnapshot.read(config.resume, force=True)}
        monkeypatch.setattr(engine.browser, 'uploads_ready', AsyncMock(return_value=False))
        if rejection == 'changed_resume':
            config.resume.write_bytes(b'%PDF-1.4\n% changed canonical resume\n%%EOF\n')
        assert not await engine.check_uploads(1, page, {'resume': object()})
        expected = 'UPLOAD_FAILED' if rejection == 'changed_resume' else 'UPLOAD_PENDING'
        assert tracker.result['category'] == expected and not tracker.result['ready']
        assert tracker.receipt is None
        assert engine.browser.observation(page)['upload_result'] == tracker.result
        assert db.setting('upload_result:1') == tracker.result
        assert not db.application(1)['submit_intent_at']
        assert all(value == 0 for value in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()


@pytest.mark.parametrize('value', [0, -1, True, 1.5, 65])
def test_inventory_budget_is_bounded_and_validated(tmp_path, value):
    with pytest.raises(ValueError):
        Config(tmp_path, {'application': {'max_inventory_rounds': value}})


@pytest.mark.parametrize('ats', ['generic', 'smartrecruiters'])
async def test_missing_fact_does_not_block_verified_fields_or_resume(config, db, listing, ats):
    url = 'https://jobs.smartrecruiters.com/fixture/123' if ats == 'smartrecruiters' else 'http://127.0.0.1:8123/application'
    add_application(config, db, listing, url)
    # Missing contact field used to stop structured preflight before all filling.
    body = POSTING + FORM.replace('<label>Last Name', '<label>Email<input type="email" required></label><label>Last Name')
    profile_path = config.private/'profile.yaml'
    import yaml
    profile = config.profile
    profile['contact'].pop('email')
    profile_path.write_text(yaml.safe_dump(profile))
    engine = Engine(config, db, fill_only=True, preserve_fill_session=True)
    try:
        requests = await route_engine(engine, body)
        await engine.process_one(1)
        page = engine.handoff.pages[1]
        assert await page.locator('#first').input_value() == 'Test'
        assert await page.locator('#last').input_value() == 'Student'
        assert await page.locator('input[type=email]').input_value() == ''
        assert await page.locator('input[type=file]').evaluate('e=>e.files.length') == 1
        pending = db.rows("SELECT * FROM questions WHERE status='PENDING'")
        assert [q['raw_question'] for q in pending] == ['Email']
        assert db.application(1)['error_category'] == 'INPUT_REQUIRED'
        assert db.application(1)['session_preserved']
        assert db.setting('upload_result:1')['ready']
        saved = json.loads((config.private/'fill-only-checkpoints/1.json').read_text())
        assert saved['progress_kind'] == 'PARTIAL' and saved['committed_field_count'] == 2
        assert saved['readiness'] == 'SAVED_PROGRESS_CHECKPOINT'
        assert not saved['form_filled'] and not saved['reconstruction_verified']
        assert not requests and await page.evaluate('window.finalClicks') == 0
        assert all(v == 0 for v in engine.fill_invariant.snapshot().values())
        await release_checkpoint(engine, 1)
        assert not db.application(1)['session_preserved']
        assert not engine.browser.leases and not engine.handoff.pages
    finally:
        await engine.close()


@pytest.mark.parametrize('change', ['reveal', 'replace', 'delayed'])
async def test_conditional_reconciliation_preserves_page_budget_and_unknowns(config, db, listing, change):
    add_application(config, db, listing)
    config.data['application']['max_pages'] = 1
    body = POSTING + FORM.replace('<label>First Name', '<label>Unknown personal identifier<input id="unknown" required></label><label>First Name')
    if change != 'replace':
        body = body.replace('<label>Last Name', '<label id="conditional" hidden>Last Name')
        effect = "document.querySelector('#conditional').hidden=false"
        if change == 'delayed':
            effect = "document.querySelector('form').setAttribute('aria-busy','true');setTimeout(()=>{document.querySelector('#conditional').hidden=false;document.querySelector('form').removeAttribute('aria-busy')},650)"
    else:
        effect = "document.querySelector('#last').outerHTML='<input id=last required>'"
    body += '<script>document.querySelector("#first").oninput=()=>{' + effect + '};</script>'
    engine = Engine(config, db, fill_only=True, preserve_fill_session=True)
    try:
        requests = await route_engine(engine, body)
        await engine.process_one(1)
        page = engine.handoff.pages[1]
        assert await page.locator('#last').input_value() == 'Student'
        assert await page.locator('#unknown').input_value() == ''
        assert db.application(1)['error_category'] == 'INPUT_REQUIRED'
        assert db.application(1)['attempts'] == 1
        assert db.one("SELECT count(*) n FROM events WHERE kind='LIVE_CONTROL_COMMITTED'")['n'] == 2
        assert not requests and all(v == 0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()


async def test_removed_committed_conditional_control_does_not_poison_validation(config, db, listing):
    add_application(config, db, listing)
    # A later answer legitimately removes an earlier completed conditional field.
    body = POSTING + FORM + '''<script>document.querySelector('#last').oninput=()=>document.querySelector('#first').parentElement.remove()</script>'''
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, body)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        assert db.one("SELECT id FROM events WHERE kind='FIELD_NO_LONGER_ACTIVE'")
        assert db.one("SELECT answer FROM questions WHERE raw_question='First Name'")['answer'] == '"Test"'
    finally:
        await engine.close()


async def test_hidden_unknown_is_inactive_without_fabricating_or_erasing_answer(config, db, listing):
    add_application(config, db, listing)
    body = POSTING + FORM.replace('<label>First Name', '<label id="conditional">Unknown personal identifier<input required></label><label>First Name')
    body += '''<script>document.querySelector('#first').oninput=()=>{document.querySelector('#conditional').hidden=true;document.querySelector('#conditional input').disabled=true}</script>'''
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, body)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        row = db.one("SELECT * FROM questions WHERE raw_question='Unknown personal identifier'")
        assert row['status'] == 'INACTIVE' and row['answer'] is None
        assert db.one("SELECT id FROM events WHERE kind='QUESTION_NO_LONGER_ACTIVE'")
    finally:
        await engine.close()


async def test_options_changed_before_input_are_resolved_again(config, db, listing, monkeypatch):
    add_application(config, db, listing)
    body = POSTING + FORM.replace('<label>Last Name', '<label>Do you require sponsorship now?<select id="sponsor" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label><label>Last Name')
    original = GenericApplicationAdapter.answer_question
    changed = []
    async def answer(adapter, q, value):
        if q.kind == 'select' and not changed:
            changed.append(True)
            await adapter.page.locator('#sponsor').evaluate("e=>e.appendChild(e.options[1])")
        return await original(adapter, q, value)
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', answer)
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, body)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        page = engine.retained_pages[1]
        assert await page.locator('#sponsor').input_value() == 'No'
        assert db.one("SELECT id FROM events WHERE kind='FIELD_INVENTORY_REFRESH'")
    finally:
        await engine.close()


async def test_generic_entry_waits_for_visible_hydrated_fields_instead_of_hidden_template(config, db):
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, '''<input type=email hidden><button onclick="window.entryClicks++;setTimeout(()=>{document.body.innerHTML='<label>Email<input type=email></label>'},300)">Apply now</button><script>window.entryClicks=0</script>''')
        page = await engine.browser.new_page()
        await page.goto('http://127.0.0.1:8123/application')
        adapter = GenericApplicationAdapter(page)
        await adapter.begin()
        assert await page.evaluate('window.entryClicks') == 1
        assert len(await adapter.get_questions({'id':1})) == 1
    finally:
        await engine.close()


async def test_embedded_application_controls_establish_readiness(config, db):
    engine = Engine(config, db, fill_only=True)
    try:
        def bodies(url):
            if '/embedded' in url:
                return '<label>Email<input type=email required></label><button>Continue</button>'
            return '<iframe src="https://boards.greenhouse.io/application/embedded"></iframe>'
        await route_engine(engine, bodies)
        page = await engine.browser.new_page()
        await page.goto('http://127.0.0.1:8123/application')
        adapter = GenericApplicationAdapter(page)
        await adapter.wait_form_controls(timeout=8)
        assert [q.label for q in await adapter.get_questions({'id':1})] == ['Email']
    finally:
        await engine.close()


async def test_selected_runner_continues_after_input_hold_without_discord(config, db, listing, monkeypatch):
    for i in range(2):
        add_application(config, db, replace(listing, company=f'Example {i}'), f'http://127.0.0.1:8123/application/{i}')
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, lambda url: POSTING + (FORM.replace('First Name', 'Unknown personal identifier') if url.endswith('/0') else FORM))
        monkeypatch.setattr('autoapply.fill_runner.Engine', lambda *args, **kwargs: engine)
        messages = []
        result = await run_selected(config, db, [1,2], checkpoint_only=True, emit=messages.append)
        assert result['result'] == 'COMPLETE'
        assert [r['application_id'] for r in result['applications']] == [1,2]
        assert db.application(1)['error_category'] == 'INPUT_REQUIRED'
        assert db.application(2)['status'] == 'READY', db.application(2)['failure_reason']
        assert len(messages) == 2
        assert not engine.browser.context and not engine.browser.leases
        assert db.setting('paused') and db.setting('auto_submit') is False
        assert all(result[k] == 0 for k in ('final_submit_clicks','employer_submission_requests','submission_intents_created','submission_requests_blocked'))
    finally:
        await engine.close()


async def test_selected_runner_stops_after_infrastructure_failure(config, db, listing, monkeypatch):
    for i in range(2):
        add_application(config, db, listing, f'http://127.0.0.1:8123/application/{i}')
    engine = Engine(config, db, fill_only=True)
    visited = []
    async def fail(app_id):
        visited.append(app_id)
        db.claim(app_id)
        db.lifecycle.browser_start_failed(app_id, 'Local infrastructure unavailable')
    engine.process_one = fail
    engine.close = AsyncMock()
    monkeypatch.setattr('autoapply.fill_runner.Engine', lambda *args, **kwargs: engine)
    result = await run_selected(config, db, [1,2], checkpoint_only=True, emit=lambda v: None)
    assert visited == [1] and result['result'] == 'GLOBAL_EXECUTION_BLOCKER'
    assert db.application(1)['attempts'] == db.application(2)['attempts'] == 0
    assert db.application(2)['status'] == 'QUEUED'
    engine.close.assert_awaited_once()


def test_fill_cli_selects_explicit_ids_without_queue_fallback():
    from autoapply.__main__ import parser
    args = parser().parse_args(['fill','7','9','--checkpoint-only'])
    assert args.ids == [7,9] and args.checkpoint_only


async def test_checkpoint_counts_only_current_attempt_not_historical_answers(config, db, listing):
    add_application(config, db, listing)
    db.event(1, 'LIVE_CONTROL_COMMITTED', json.dumps({'field_id':'old'}))
    db.claim(1)
    db.lifecycle.begin_attempt(1, 3)
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, POSTING + FORM)
        page = await engine.browser.new_page()
        await page.goto('http://127.0.0.1:8123/application')
        saved = await checkpoint(engine, 1, page)
        assert saved['progress_kind'] == 'UNFILLED' and saved['committed_field_count'] == 0
        assert db.one("SELECT id FROM events WHERE kind='LIVE_CONTROL_COMMITTED'")
    finally:
        await engine.close()


@pytest.mark.parametrize('protected', [False, True])
async def test_checkpoint_does_not_capture_verification_values(config, db, listing, protected):
    add_application(config, db, listing)
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, '<label>First name<input value="Fixture"></label><label>Verification code<input id="code" autocomplete="one-time-code" value="sensitive-fixture"></label>')
        page = await engine.browser.new_page()
        await page.goto('http://127.0.0.1:8123/application')
        if protected:
            db.update_security(1, security_state='EMAIL_VERIFICATION')
        saved = await checkpoint(engine, 1, page)
        assert 'sensitive-fixture' not in json.dumps(saved)
        if protected:
            assert saved['frames'][0]['protected'] and saved['frames'][0]['fields'] == []
        else:
            assert saved['frames'][0]['fields'][0]['value'] == 'Fixture'
    finally:
        await engine.close()


async def test_replaced_step_with_same_question_schema_advances_once(config, db, listing):
    add_application(config, db, listing, 'https://jobs.smartrecruiters.com/fixture/123')
    body = POSTING + FORM.replace('<button type="submit">Submit application</button>', '<button type="button" id="next">Next</button>')
    body += '''<script>window.nextClicks=0;document.querySelector('#next').onclick=()=>{window.nextClicks++;
    document.querySelector('#first').outerHTML='<input id="first" name="first_name" required>';
    document.querySelector('#last').outerHTML='<input id="last" required>';
    document.querySelector('#next').textContent='Submit application';document.querySelector('#next').onclick=()=>window.finalClicks++}</script>'''
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, body)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        page = engine.retained_pages[1]
        assert await page.evaluate('window.nextClicks') == 1
        assert await page.evaluate('window.finalClicks') == 0
        assert await page.locator('#first').input_value() == 'Test'
        assert await page.locator('#last').input_value() == 'Student'
        assert db.setting('application_transaction:1')['step'] == 2
    finally:
        await engine.close()


@pytest.mark.parametrize('automatic', [False, True])
def test_real_fill_cli_processes_local_forms_sequentially(config, db, listing, automatic):
    """Exercise the user-facing command, database, browser and cleanup together."""
    import yaml
    posts = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = POSTING + (FORM.replace('First Name', 'Unknown personal identifier') if self.path.endswith('/one') else FORM)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body.encode())
        def do_POST(self):
            posts.append(self.path)
            self.send_response(500)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for index, path in enumerate(('one', 'two')):
            add_application(config, db, replace(listing, company=f'Example {index}'), f'http://127.0.0.1:{server.server_port}/jobs/{path}')
        config.data['discord']['enabled'] = True
        config.data['gmail']['enabled'] = True
        config.data['ai']['enabled'] = True
        (config.private/'config.yaml').write_text(yaml.safe_dump(config.data))
        target = sqlite3.connect(config.private/'autoapply.sqlite3')
        db.conn.backup(target)
        target.close()
        selection = ['--limit','2'] if automatic else ['1','2']
        result = subprocess.run([sys.executable, '-m', 'autoapply', '--root', str(config.root),
                                 'fill', *selection, '--checkpoint-only'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                timeout=90)
        assert result.returncode == 0, result.stderr + result.stdout
        with sqlite3.connect(config.private/'autoapply.sqlite3') as saved:
            assert saved.execute('SELECT status,error_category,attempts FROM applications ORDER BY id').fetchall() == [('MANUAL_REVIEW','INPUT_REQUIRED',1),('READY','',1)]
            assert saved.execute('SELECT count(*) FROM applications WHERE submit_intent_at IS NOT NULL').fetchone()[0] == 0
            assert saved.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == 'true'
            assert saved.execute("SELECT value FROM settings WHERE key='auto_submit'").fetchone()[0] == 'false'
        assert not posts
        # Windows lock files persist; successful reacquisition proves release.
        with ProcessLock(config.private/'worker.lock'):
            pass
        with ProcessLock(config.private/'browser-profile.lock'):
            pass
        assert '"result": "COMPLETE"' in result.stdout
        report = json.loads((config.private/'fill-run-result.json').read_text())
        assert report['result'] == 'COMPLETE' and report['finished_at']
        assert [r['application_id'] for r in report['applications']] == [1,2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_conditional_receipt_does_not_hide_changed_verified_profile(config, db, listing, monkeypatch):
    import yaml
    add_application(config, db, listing)
    original = GenericApplicationAdapter.answer_question
    changed = []
    async def answer(adapter, q, value):
        await original(adapter, q, value)
        if q.label == 'Last Name' and not changed:
            changed.append(True)
            profile = config.profile
            profile['identity']['first_name'] = 'Updated'
            (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', answer)
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, POSTING + FORM)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        assert await engine.retained_pages[1].locator('#first').input_value() == 'Updated'
    finally:
        await engine.close()


async def test_read_only_metadata_refresh_is_bounded_without_replaying_input(config, db, listing, monkeypatch):
    add_application(config, db, listing)
    config.data['application']['max_inventory_rounds'] = 2
    async def changed(*args):
        raise InventoryChanged('Metadata changed before input')
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', changed)
    engine = Engine(config, db, fill_only=True, preserve_fill_session=True)
    try:
        await route_engine(engine, POSTING + FORM)
        await engine.process_one(1)
        assert db.application(1)['error_category'] == 'FORM_INVENTORY_UNSTABLE'
        assert db.one("SELECT count(*) n FROM events WHERE kind='FIELD_INVENTORY_REFRESH'")['n'] == 2
        assert await engine.handoff.pages[1].locator('#first').input_value() == ''
        assert db.application(1)['attempts'] == 1
        assert all(v == 0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()


async def test_pre_submit_engineering_error_is_classified_and_sanitized(config, db, listing, monkeypatch):
    add_application(config, db, listing)
    async def fail(*args):
        raise ValueError('private-fixture-sentinel')
    monkeypatch.setattr(GenericApplicationAdapter, 'answer_question', fail)
    engine = Engine(config, db, fill_only=True)
    try:
        await route_engine(engine, POSTING + FORM)
        await engine.process_one(1)
        app = db.application(1)
        assert app['error_category'] == 'ENGINEERING_FAILURE'
        assert app['application_state'] == 'MANUAL_REQUIRED' and not app['submit_intent_at']
        event = json.loads(db.one("SELECT detail FROM events WHERE kind='APPLICATION_EXCEPTION'")['detail'])
        assert event['type'] == 'ValueError' and not event['submission_intent']
        assert event['frames'] and 'private-fixture-sentinel' not in json.dumps(db.rows('SELECT * FROM events'))
    finally:
        await engine.close()


async def test_daily_submission_ceiling_does_not_block_fill_only_preparation(config, db, listing, monkeypatch):
    add_application(config, db, listing)
    engine = Engine(config, db, fill_only=True)
    monkeypatch.setattr(engine, 'daily_count', lambda: config['processing']['max_applications_per_day'])
    try:
        await route_engine(engine, POSTING + FORM)
        await engine.process_one(1)
        assert db.application(1)['application_state'] == 'READY_FOR_MANUAL_SUBMIT', db.application(1)['failure_reason']
        assert all(v == 0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()
