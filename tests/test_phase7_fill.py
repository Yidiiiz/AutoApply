import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.engine import Engine
from autoapply.fill_batch import BatchPolicy, FillOnlyInvariant, discover, finish_preparation, verify_reconstruction
from autoapply.models import now

FORM = '''<h1>Software Engineering Intern Summer 2027</h1>
<p>Software engineering internship for undergraduate students in New York, NY.</p>
<form><label>First Name<input name="first_name" required></label>
<label>Resume<input name="resume" type="file" required></label><button type="submit">Submit application</button></form>
<script>window.finalClicks=0;document.querySelector('button').onclick=()=>window.finalClicks++;
document.querySelector('form').onsubmit=e=>{e.preventDefault();fetch('/submit',{method:'POST'})}</script>'''


@pytest.mark.parametrize('verify', [False, True])
async def test_sequential_checkpoint_batch_local_forms(config, db, listing, verify):
    db.set_setting('auto_submit', False)
    engine = Engine(config, db, fill_only=True)
    for i in range(3):
        db.ingest(replace(listing, url=f'http://127.0.0.1:8123/job/{i}'), config)
    requests = []
    try:
        await engine.browser.start()
        async def fixture(route):
            if route.request.method == 'POST': requests.append(route.request.url)
            await route.fulfill(content_type='text/html', body=FORM)
        await engine.browser.context.route('**/*', fixture)
        # Install the invariant last, so fixture fulfillment cannot bypass it.
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        for app_id in discover(db, config):
            await engine.process_one(app_id)
            assert db.application(app_id)['application_state']=='READY_FOR_MANUAL_SUBMIT', db.application(app_id)['failure_reason']
            page = engine.retained_pages[app_id]
            assert await page.evaluate('window.finalClicks') == 0
            assert engine.control.status_snapshot(app_id)['readiness']=='LIVE_READY_FOR_MANUAL_SUBMIT'
            saved = await finish_preparation(engine, app_id, verify=verify)
            assert saved['readiness'] == ('RECONSTRUCTION_VERIFIED' if verify else 'RECONSTRUCTABLE_CHECKPOINT')
            assert not engine.retained_pages and not engine.browser.context.pages
            assert engine.control.status_snapshot(app_id)['session']['live_page'] is False
            engine.fill_invariant.check()
        assert engine.browser.max_tabs_observed == 1
        assert engine.browser.application_tabs_opened == 3
        assert requests == []
        assert all(v == 0 for v in engine.fill_invariant.snapshot().values())
        events = db.rows("SELECT kind FROM events WHERE kind='FILL_ONLY_RECONSTRUCTION_VERIFIED'")
        assert len(events) == (3 if verify else 0)
        assert not discover(db, config)
    finally:
        await engine.close()


@pytest.mark.parametrize('body,category', [
    ('<h1>Security check</h1><p>Verify you are human</p>', 'BOT_CHALLENGE'),
    (FORM.replace('First Name', 'Unknown factual identifier').replace('first_name', 'unknown'), 'INPUT_REQUIRED'),
    (FORM.replace('name="first_name"', 'name="first_name" type="color"'), 'UNSUPPORTED_CONTROL'),
])
async def test_held_local_form_is_checkpoint_not_ready(config, db, listing, body, category):
    db.set_setting('auto_submit', False)
    db.ingest(replace(listing, url='http://127.0.0.1:8123/hold'), config)
    engine = Engine(config, db, fill_only=True)
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=body))
        await engine.process_one(1)
        saved = json.loads((config.private/'fill-only-checkpoints/1.json').read_text())
        assert saved['readiness']=='FILLED_CHECKPOINT'
        assert not saved['reconstruction_verified']
        assert db.application(1)['manual_action_required']
        assert not engine.browser.context.pages
        engine.fill_invariant.check()
    finally:
        await engine.close()


def test_whole_run_intent_violation_is_detected(config, db, listing):
    db.ingest(listing, config)
    guard = FillOnlyInvariant(db)
    db.execute('UPDATE applications SET submit_intent_at=? WHERE id=1', (now(),))
    with pytest.raises(RuntimeError, match='INVARIANT'): guard.check()


async def test_final_request_is_blocked_and_latched(db):
    guard = FillOnlyInvariant(db)
    route = SimpleNamespace(request=SimpleNamespace(url='https://fixture.test/submit', method='POST', post_data='{}'), abort=AsyncMock(), fallback=AsyncMock())
    await guard.route(route)
    route.abort.assert_awaited_once()
    route.fallback.assert_not_awaited()
    assert guard.snapshot()['employer_submission_requests'] == 0
    assert guard.snapshot()['submission_requests_blocked'] == 1
    with pytest.raises(RuntimeError): guard.check()
    with pytest.raises(RuntimeError): guard.check()


@pytest.mark.parametrize('target_count', [2, 3])
@pytest.mark.parametrize('smoke_success', [False, True])
async def test_specialized_runner_smoke_gate(config, db, listing, tmp_path, monkeypatch, smoke_success, target_count):
    from scripts import fill_only_batch as runner
    from autoapply.fill_batch import BatchReport
    db.set_setting('auto_submit', False)
    for i in range(2): db.ingest(replace(listing, url=f'https://jobs.lever.co/fixture/{i}'), config)
    monkeypatch.setattr(runner, 'EXCLUDED', ())
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'synthetic')
    user = SimpleNamespace(send=AsyncMock())
    class Bot:
        owner_id = 1
        def __init__(self, control): pass
        async def start(self, token): pass
        async def wait_for_delivery(self, connection): pass
        async def fetch_user(self, owner): return user
        async def close(self): pass
    monkeypatch.setattr(runner, 'BatchBot', Bot)
    engine = Engine(config, db, fill_only=True)
    engine.browser = SimpleNamespace(network_policy=None, check_tab_limit=lambda:None,
        application_tabs_opened=0, leases={}, context=None, max_tabs_observed=0,
        unexpected_popup_violations=0, browser_pid=None)
    visited = []
    async def process(app_id):
        visited.append(app_id)
        db.claim(app_id)
        if smoke_success:
            db.lifecycle.record_ready(app_id, live=True)
        else:
            db.lifecycle.record_hold(app_id, 'Unsupported control', 'UNSUPPORTED_CONTROL')
    engine.process_one = process
    engine.close = AsyncMock()
    monkeypatch.setattr(runner, 'Engine', lambda *a, **k:engine)
    async def finish(engine, app_id, **kwargs):
        db.set_setting(f'fill_checkpoint:{app_id}', {'readiness':'RECONSTRUCTABLE_CHECKPOINT'})
        db.update_security(app_id, application_state='READY_TO_SUBMIT', session_preserved=0)
    monkeypatch.setattr(runner, 'finish_preparation', finish)
    approval = SimpleNamespace(select=lambda app:None, blocked=[], final_requests_blocked=0)
    report = BatchReport(tmp_path/'report.json')
    await runner.run(config, db, report, approval=approval, policy=BatchPolicy(target_count=target_count))
    assert visited == ([1,2] if smoke_success else [1])
    assert report.data['smoke_test'] == ('PASSED' if smoke_success else 'FAILED')
    assert report.data['result'] == (('TARGET_CHECKPOINTS_PREPARED' if target_count==2 else 'QUEUE_EXHAUSTED') if smoke_success else 'SMOKE_TEST_FAILED')
    assert report.data['ready_count'] == 0


async def test_reconstruction_failure_never_claims_verified(config, db, listing):
    db.set_setting('auto_submit', False)
    db.ingest(listing, config)
    db.transition(1, 'READY')
    db.update_security(1, application_state='READY_FOR_MANUAL_SUBMIT')
    engine = Engine(config, db, fill_only=True)
    page = SimpleNamespace()
    engine.retained_pages[1] = page
    engine.browser.navigate = AsyncMock(return_value=('MANUAL_REVIEW', 'Security hold'))
    with pytest.raises(RuntimeError, match='RECONSTRUCTION_FAILED'):
        await verify_reconstruction(engine, 1)
    assert not db.setting('fill_checkpoint:1', {}).get('reconstruction_verified')
    assert engine.retained_pages[1] is page


async def test_actual_final_click_is_observed_and_stops_run(config, db):
    from autoapply.browser import Browser
    browser = Browser(config)
    guard = FillOnlyInvariant(db)
    browser.fill_invariant = guard
    try:
        page = await browser.new_page()
        await page.route('http://127.0.0.1:8123/counter', lambda route: route.fulfill(body='<form><button type="button">Submit application</button></form>', content_type='text/html'))
        await page.goto('http://127.0.0.1:8123/counter')
        await page.locator('button').click()
        await page.wait_for_function('window.__autoapplyFinalObserved')
        assert guard.snapshot()['final_submit_clicks']==1
        with pytest.raises(RuntimeError): guard.check()
    finally:
        await browser.close()


def test_completed_final_request_is_counted(db):
    guard = FillOnlyInvariant(db)
    guard.observed_request(SimpleNamespace(url='https://fixture.test/submit', method='POST', post_data='{}'))
    assert guard.snapshot()['employer_submission_requests']==1
    with pytest.raises(RuntimeError): guard.check()
