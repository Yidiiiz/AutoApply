import asyncio
import time
from pathlib import Path

import pytest

from autoapply.applications import GenericApplicationAdapter, UnsupportedForm
from autoapply.browser import Browser
from autoapply.patience import PatiencePolicy, TransitionPatience
from autoapply.workday import WorkdayAdapter


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def observation(wait, progress='loading', failure='pending'):
    wait.observe(progress=progress, failure=failure, summary={'loading':True})


@pytest.mark.parametrize('delay', [1, 5, 8, 10])
def test_positive_readiness_has_no_mandatory_grace_sleep(delay):
    clock = Clock()
    wait = TransitionPatience(clock=clock, interaction_at=0)
    observation(wait)
    clock.now = delay
    wait.ready()
    assert wait.ready_at == delay and wait.remaining == 30-delay
    assert wait.telemetry()['total_wait_duration'] == delay


def test_transient_failure_needs_grace_and_multiple_stable_observations():
    clock = Clock()
    wait = TransitionPatience(clock=clock)
    observation(wait)
    for t in (1, 2, 7.99):
        clock.now = t
        observation(wait)
        assert not wait.stable_failure
    clock.now = 8
    observation(wait)
    assert wait.stable_failure
    observation(wait, failure='different condition')
    assert not wait.stable_failure


def test_persistent_loading_expires_at_normal_deadline():
    clock = Clock()
    wait = TransitionPatience(clock=clock)
    for t in range(31):
        clock.now = t
        observation(wait)
    assert wait.remaining == 0 and wait.deadline == 30 and wait.stable_failure


def test_meaningful_progress_can_extend_only_to_hard_cap():
    clock = Clock()
    wait = TransitionPatience(clock=clock)
    observation(wait)
    clock.now = 29
    observation(wait, 'form_structure')
    assert wait.deadline == 34
    clock.now = 33
    observation(wait, 'visible_controls')
    assert wait.deadline == 35
    clock.now = 35
    observation(wait, 'more_controls')
    assert wait.remaining == 0
    assert wait.telemetry()['last_meaningful_progress'] == 35


def test_repeated_animation_is_not_meaningful_progress():
    clock = Clock()
    wait = TransitionPatience(clock=clock)
    for tick in range(350):
        clock.now = tick/10
        observation(wait)  # DOM revisions do not enter the semantic progress key.
    assert wait.deadline == 30 and wait.progress == []


@pytest.mark.parametrize('options', [{'grace':2}, {'timeout':2}, {'extension':float('inf')}, {'extension':31}])
def test_policy_cannot_remove_grace_or_become_unbounded(options):
    with pytest.raises(ValueError):
        PatiencePolicy(**options)


@pytest.fixture
async def browser(config):
    value = Browser(config)
    await value.start()
    await value.enforce_application_limit(1)
    yield value
    await value.close()


async def page_fixture(browser, html):
    await browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
    page = await browser.new_page()
    await page.goto('https://fixture.myworkdayjobs.com/careers/job/fixture')
    return page


@pytest.mark.parametrize('delay', [1000, 5000, 8000, 10000])
async def test_real_delayed_post_entry_form_preserves_single_clicks(browser, delay):
    html = (Path(__file__).parent/'fixtures/workday-post-entry-delayed.html').read_text()
    page = await page_fixture(browser, html)
    await page.evaluate('(delay)=>window.hydrationDelay=delay', delay)
    adapter = WorkdayAdapter(page)
    events = []
    adapter.emit = lambda kind, detail: events.append((kind,detail))
    start = time.monotonic()
    pending = asyncio.create_task(adapter.begin())
    try:
        if delay >= 5000:
            await asyncio.sleep(2)
            assert not pending.done()
        await pending
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    elapsed = time.monotonic()-start
    assert elapsed >= delay/1000
    assert elapsed < delay/1000 + 4
    assert page._autoapply_workday_manual_entry_confirmed
    states = [detail.get('state') for _, detail in events]
    assert 'WORKDAY_MANUAL_ENTRY_CONFIRMED' in states and 'POST_ENTRY_LOADING' in states
    assert states[-1] == 'FORM_READY'
    assert '/apply/applyManually' in page.url
    await WorkdayAdapter(page).begin()  # A new adapter inherits the page latch.
    assert await page.evaluate('window.clicks') == dict(apply=1,manual=1,next=0,submit=0)
    assert len(page.context.pages) == 1
    telemetry = adapter.readiness['patience']
    assert telemetry['interaction_at'] <= telemetry['grace_started_at']
    assert telemetry['readiness_established_at'] is not None
    assert 'first_name' not in str(telemetry)


@pytest.mark.parametrize('manual_route', [True, False])
async def test_stable_post_entry_chooser_holds_only_after_grace(browser, manual_route):
    page = await page_fixture(browser, '<h2>Start Your Application</h2><button>Apply Manually</button>')
    page._autoapply_workday_manual_entry_confirmed = True
    page._autoapply_workday_entry_attempted = True
    if manual_route:
        await page.evaluate("history.pushState({},'',location.pathname+'/apply/applyManually')")
    adapter = WorkdayAdapter(page)
    adapter.readiness_timeout = 8
    start = time.monotonic()
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.require_form()
    assert 8 <= time.monotonic()-start < 10
    assert caught.value.category == ('FORM_NOT_READY' if manual_route else 'APPLICATION_ENTRY_MODE_UNRESOLVED')
    assert caught.value.transient_readiness['terminal_condition_stable_at'] is not None
    assert not adapter.controls and not page._autoapply_uploads.selected


async def test_loading_with_dom_churn_is_bounded_in_real_browser(browser):
    page = await page_fixture(browser, '<div role=progressbar>Loading</div>')
    await page.evaluate('setInterval(()=>document.body.dataset.tick=Math.random(),20)')
    adapter = WorkdayAdapter(page)
    adapter.readiness_timeout = 8
    start = time.monotonic()
    with pytest.raises(UnsupportedForm) as caught:
        await adapter.require_form()
    assert 8 <= time.monotonic()-start < 10
    assert caught.value.transient_readiness['progress_observations'] == []


@pytest.mark.parametrize('body,category', [('<input type=password>', 'AUTH_REQUIRED'),
                                         ('<h2>Verify you are human</h2>', 'SECURITY_CONTROL')])
async def test_immediate_security_exception_bypasses_grace(browser, body, category):
    page = await page_fixture(browser, body)
    start = time.monotonic()
    with pytest.raises(UnsupportedForm) as caught:
        await WorkdayAdapter(page).require_form()
    assert caught.value.category == category
    assert time.monotonic()-start < 2


@pytest.mark.parametrize('label', ['Next', 'Continue'])
async def test_next_hydration_is_observation_only(browser, label):
    page = await page_fixture(browser, '<form><input name=first><button type=button>'+label+'</button></form>')
    await page.evaluate('''() => {
      window.clicks=0; window.finalClicks=0;
      document.querySelector('button').onclick=()=>{
        window.clicks++; document.body.innerHTML='<div role=progressbar>Loading</div>';
        setTimeout(()=>document.body.innerHTML='<form><input name=last><button onclick="window.finalClicks++">Submit application</button></form>',5000);
      };
    }''')
    start = time.monotonic()
    await GenericApplicationAdapter(page).advance()
    assert time.monotonic()-start >= 5
    assert await page.evaluate('[window.clicks,window.finalClicks]') == [1,0]


async def test_final_boundary_is_ready_without_consuming_grace(browser):
    page = await page_fixture(browser, '<form><input><button onclick="window.finalClicks++">Submit application</button></form>')
    await page.evaluate('window.finalClicks=0')
    adapter = GenericApplicationAdapter(page)
    start = time.monotonic()
    await adapter.wait_form_controls()
    assert (await adapter.action())[0] == 'submit'
    assert time.monotonic()-start < 2
    assert await page.evaluate('window.finalClicks') == 0


async def test_transient_hold_captures_evidence_before_terminal_and_cleanup(config, db, listing, monkeypatch):
    from dataclasses import replace
    from autoapply.engine import Engine
    monkeypatch.setattr(WorkdayAdapter, 'readiness_timeout', 8)
    db.set_setting('auto_submit', False)
    db.ingest(replace(listing, url='https://fixture.myworkdayjobs.com/careers/job/fixture'), config)
    html = (Path(__file__).parent/'fixtures/workday-post-entry-delayed.html').read_text()
    html = html.replace('<button id="apply">', '<h1>Software Engineering Intern Summer 2027</h1>'
                        '<p>Software engineering internship for undergraduate students in New York, NY.</p>'
                        '<script>window.hydrationDelay=60000;</script><button id="apply">')
    engine = Engine(config, db, fill_only=True)
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        await engine.process_one(1)
        app = db.application(1)
        assert app['error_category'] == 'FORM_NOT_READY', app['failure_reason']
        events = db.rows('SELECT kind,detail FROM events WHERE application_id=1 ORDER BY id')
        kinds = [e['kind'] for e in events]
        first = kinds.index('BROWSER_READINESS_FINAL_OBSERVATION')
        assert first < kinds.index('security_diagnostics', first) < kinds.index('BROWSER_READINESS_TERMINAL')
        assert Path(app['screenshot_path']).is_file()
        assert not app['retry_allowed'] and not app['submit_intent_at']
        assert not any(k in kinds for k in ('LIVE_CONTROL_COMMITTED', 'UPLOAD_FILE_SELECTED'))
        assert not engine.browser.leases
        engine.fill_invariant.check()
    finally:
        await engine.close()


def test_patience_has_no_application_specific_branch():
    import inspect
    import autoapply.patience
    import autoapply.workday
    for module in (autoapply.patience, autoapply.workday):
        assert '3556' not in inspect.getsource(module)


async def test_manual_option_can_hydrate_before_single_selection(browser):
    page = await page_fixture(browser, '<h2>Start Your Application</h2><button id=manual disabled>Apply Manually</button>')
    await page.evaluate('''() => {
      window.clicks=0;
      document.querySelector('button').onclick=()=>{
        window.clicks++; document.body.innerHTML='<form><input><button>Submit application</button></form>';
      };
      setTimeout(()=>document.querySelector('button').disabled=false, 1000);
    }''')
    start = time.monotonic()
    await WorkdayAdapter(page).begin()
    assert 1 <= time.monotonic()-start < 4
    assert await page.evaluate('window.clicks') == 1
