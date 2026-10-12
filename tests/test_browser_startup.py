"""Offline startup/attempt regressions: fakes and temporary blank profiles only."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.browser import Browser, BrowserStartFailed
from autoapply.browser_resources import DedicatedResources, BrowserOwnershipError
from autoapply.engine import Engine
from autoapply.models import State
from autoapply.retry import Delivery
from autoapply.runtime import ProcessLock


TARGET_FAILURE = 'BrowserContext.new_page: Protocol error (Target.createTarget): Failed to open a new tab'


def continuation_fixture(config, db, listing):
    from autoapply.continuation import prepare
    db.ingest(listing, config)
    db.set_setting('auto_submit', False)
    db.execute("UPDATE applications SET attempts=6,attempt_started=1,max_retries=5,status='MANUAL_REVIEW',manual_action_required=1,manual_action_reason='proven zero-page startup',error_category='BROWSER_START_FAILED',retry_allowed=0 WHERE id=1")
    db.event(1, 'HISTORICAL_ATTEMPT', 'six historical attempts preserved')
    return prepare(db, config, 1, source='Explicit fixture user authorization')


def test_continuation_preparation_preserves_history_and_never_queues(config, db, listing):
    command = continuation_fixture(config, db, listing)
    app = db.application(1)
    assert app['attempts'] == 6 and app['attempt_started'] == 1 and app['max_retries'] == 5
    assert app['manual_action_required'] and not app['retry_allowed']
    assert not db.rows('SELECT * FROM manual_requests') and not db.claim(1)
    assert db.one('SELECT state FROM manual_commands WHERE id=?', (command.id,))['state'] == 'PREPARED'
    with pytest.raises(ValueError, match='outstanding'):
        from autoapply.continuation import prepare
        prepare(db, config, 1, source='Duplicate preparation')
    assert db.one("SELECT detail FROM events WHERE kind='HISTORICAL_ATTEMPT'")['detail'] == 'six historical attempts preserved'


async def test_continuation_zero_page_failure_spends_command_but_no_attempt(config, db, listing, launches, monkeypatch):
    command = continuation_fixture(config, db, listing)
    launches.contexts.extend(Context([1]) for _ in range(3))
    engine = Engine(config, db, fill_only=True)
    monkeypatch.setattr(engine.fill_invariant, 'install', AsyncMock())  # No requests in this fake context.
    try:
        assert await engine.process_one(1, continuation_id=command.id)
        app = db.application(1)
        assert app['attempts'] == 6 and not app['attempt_started'] and not app['retry_allowed']
        assert app['error_category'] == 'BROWSER_START_FAILED'
        assert len(launches.drivers) == 3 and not engine.browser.leases
        assert db.one('SELECT state FROM manual_commands WHERE id=?', (command.id,))['state'] == 'ACKNOWLEDGED'
        with pytest.raises(ValueError, match='consumed'):
            await engine.process_one(1, continuation_id=command.id)
        assert len(launches.drivers) == 3
    finally:
        await engine.close()


async def test_continuation_health_before_seventh_attempt_and_no_budget_replay(config, db, listing, launches, monkeypatch):
    command = continuation_fixture(config, db, listing)
    engine = Engine(config, db, fill_only=True)
    monkeypatch.setattr(engine.fill_invariant, 'install', AsyncMock())  # No requests in this fake context.
    launches.contexts.extend([Context([1]), Context()])
    async def navigate(page, url):
        app = db.application(1)
        assert app['attempts'] == 7 and app['max_retries'] == 5 and not app['retry_allowed']
        assert engine.browser.health_ready and page in engine.browser.leases
        assert not engine.browser.health_pages and not engine.browser.startup_pages
        return State.CLOSED, 'offline fixture'
    monkeypatch.setattr(engine.browser, 'navigate', navigate)
    monkeypatch.setattr(engine, 'security_gate', AsyncMock(return_value=False))
    # No form exists in this fake; isolate checkpoint serialization from the boundary test.
    monkeypatch.setattr('autoapply.fill_batch.checkpoint', AsyncMock())
    try:
        assert await engine.process_one(1, continuation_id=command.id)
        assert len(launches.drivers) == 2 and db.application(1)['attempts'] == 7
        assert len(db.rows("SELECT * FROM events WHERE kind='APPLICATION_ATTEMPT_STARTED'")) == 1
        assert not any(engine.fill_invariant.snapshot().values())
        assert db.setting('auto_submit') is False
        with pytest.raises(ValueError, match='consumed'):
            await engine.process_one(1, continuation_id=command.id)
    finally:
        await engine.close()


@pytest.mark.parametrize('gate', ['intent','uncertainty','security','verification','retired','duplicate','resume','hold','application','mode'])
async def test_continuation_cannot_override_other_safety_gates(config, db, listing, launches, gate):
    command = continuation_fixture(config, db, listing)
    engine = Engine(config, db, fill_only=gate != 'mode')
    if gate == 'intent':
        db.execute("UPDATE applications SET submit_intent_at='fixture' WHERE id=1")
    elif gate == 'uncertainty':
        db.lifecycle.delivery(1, Delivery.POSSIBLY_DELIVERED)
    elif gate == 'security':
        db.update_security(1, security_state='INTERACTIVE_CHALLENGE')
    elif gate == 'verification':
        db.update_security(1, verification_state='PENDING')
    elif gate == 'retired':
        db.set_setting('duplicate_submission_guard:1', True)
    elif gate == 'duplicate':
        db.execute("UPDATE applications SET submission_confirmation_seen=1 WHERE id=1")
    elif gate == 'resume':
        config.resume.write_bytes(b'%PDF-1.4 changed fixture')
    elif gate == 'hold':
        db.update_security(1, error_category='AUTH_REQUIRED')
    with pytest.raises((ValueError, RuntimeError)):
        await engine.process_one(2 if gate == 'application' else 1, continuation_id=command.id)
    assert not launches.drivers and db.application(1)['attempts'] == 6
    assert db.one('SELECT state FROM manual_commands WHERE id=?', (command.id,))['state'] == 'PREPARED'
    await engine.close()


def test_continuation_crash_recovery_retires_command_and_unbound_budget_fails(config, db, listing):
    from autoapply.continuation import claim
    from autoapply.manual import ManualCommands
    command = continuation_fixture(config, db, listing)
    claim(db, config, command.id)
    db.lifecycle.fill_only = True
    with pytest.raises(ValueError, match='budget exhausted'):
        db.lifecycle.begin_attempt(1, 5)  # Ledger alone grants no automatic budget.
    ManualCommands(db).recover()
    assert db.one('SELECT state FROM manual_commands WHERE id=?', (command.id,))['state'] == 'FAILED'
    with pytest.raises(ValueError, match='consumed'):
        claim(db, config, command.id)
    assert db.application(1)['attempts'] == 6


class Page:
    def __init__(self, context):
        self.context, self.url, self.closed = context, 'about:blank', False
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def is_closed(self):
        return self.closed

    async def evaluate(self, script):
        return self.url

    async def close(self):
        self.closed = True
        if self in self.context.pages:
            self.context.pages.remove(self)
        if 'close' in self.handlers:
            self.handlers['close']()


class Context:
    browser = None

    def __init__(self, failures=()):
        self.pages, self.handlers, self.closed = [], {}, False
        self.calls, self.failures = 0, set(failures)
        self.pages.append(Page(self))  # Chromium's initial blank, not an app page.

    def set_default_timeout(self, timeout):
        pass

    def on(self, event, callback):
        self.handlers[event] = callback

    async def new_page(self):
        self.calls += 1
        if self.calls in self.failures:
            raise RuntimeError(TARGET_FAILURE)
        page = Page(self)
        self.pages.append(page)
        if 'page' in self.handlers:
            self.handlers['page'](page)
        return page

    async def close(self):
        for page in list(self.pages):
            await page.close()
        self.closed = True


class Resources:
    marker = '--autoapply-session=fixture'
    diagnostics = {}

    def __init__(self, private):
        self.reaped, self.released = False, False

    def acquire(self):
        pass

    def capture(self, pid=None):
        pass

    def reap(self):
        self.reaped = True

    def release(self):
        self.released = True


@pytest.fixture
def launches(config, monkeypatch):
    contexts, drivers, owners, sleeps = [], [], [], []
    async def launch(*args, **kwargs):
        value = contexts[len(drivers) - 1]
        if isinstance(value, Exception):
            raise value
        return value
    async def start():
        driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=AsyncMock())
        drivers.append(driver)
        return driver
    def resources(private):
        owner = Resources(private)
        owners.append(owner)
        return owner
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr('playwright.async_api.async_playwright', lambda: SimpleNamespace(start=start))
    monkeypatch.setattr('autoapply.browser.DedicatedResources', resources)
    monkeypatch.setattr('autoapply.browser.get_cursor', lambda *args: None)
    monkeypatch.setattr('autoapply.browser.asyncio.sleep', sleep)
    monkeypatch.setattr(Browser, 'ensure_desktop', AsyncMock())
    monkeypatch.setattr(Browser, 'observe', lambda *args: None)
    return SimpleNamespace(contexts=contexts, drivers=drivers, owners=owners, sleeps=sleeps)


async def test_probe_has_zero_application_pages_leases_and_attempts(config, db, listing, launches):
    db.ingest(listing, config)
    launches.contexts.append(Context())
    browser = Browser(config)
    browser.max_active_application_tabs = 1
    await browser.start()
    assert browser.health_ready
    assert browser.context.pages == [] and browser.leases == {} and browser.health_pages == set()
    assert browser.application_tabs_opened == browser.max_tabs_observed == 0
    assert db.application(1)['attempts'] == 0
    page = await browser.new_page()
    assert browser.application_tabs_opened == browser.max_tabs_observed == len(browser.leases) == 1
    with pytest.raises(RuntimeError, match='BATCH_TAB_LIMIT'):
        await browser.new_page()
    await browser.release_page(page)
    await browser.close()


@pytest.mark.parametrize('failed_call', [1, 2])
async def test_first_target_failure_cleanly_restarts_and_second_launch_recovers(config, launches, failed_call):
    first, second = Context([failed_call]), Context()
    launches.contexts.extend([first, second])
    browser = Browser(config)
    page = await browser.new_page()
    assert first.closed and launches.drivers[0].stop.await_count == 1
    assert launches.owners[0].reaped and launches.owners[0].released
    assert page.context is second
    assert browser.startup_telemetry == dict(browser_start_count=2, browser_restart_count=1,
                                            browser_health_probe_failures=int(failed_call == 1))
    assert launches.sleeps == [4]
    assert browser.application_tabs_opened == len(browser.leases) == 1
    await browser.close()
    assert not browser.context and not browser.playwright and not browser.leases


@pytest.mark.parametrize('failure', ['probe', 'application_target', 'partial_launch'])
async def test_exhaustion_is_bounded_and_cleans_every_launch(config, launches, failure):
    launches.contexts.extend([RuntimeError('browser closed') if failure == 'partial_launch'
                             else Context([1 if failure == 'probe' else 2]) for _ in range(3)])
    browser = Browser(config)
    with pytest.raises(BrowserStartFailed):
        await browser.new_page()
    assert len(launches.drivers) == 3 and launches.sleeps == [4, 4]
    assert all(d.stop.await_count == 1 for d in launches.drivers)
    assert all(r.reaped and r.released for r in launches.owners)
    assert browser.startup_telemetry['browser_restart_count'] == 2
    assert browser.context is browser.playwright is browser.resources is None
    assert not browser.leases and not browser.health_pages


async def test_engine_zero_page_failure_preserves_historical_attempts_and_lock(config, db, listing, launches):
    db.ingest(listing, config)
    db.execute('UPDATE applications SET attempts=2 WHERE id=1')
    db.event(1, 'HISTORICAL_ATTEMPT', 'truthful prior increment')
    history = db.rows('SELECT * FROM events')
    launches.contexts.extend(Context([1]) for _ in range(3))
    engine = Engine(config, db)
    with ProcessLock(config.private / 'worker.lock'):
        assert await engine.process_one(1)
    with ProcessLock(config.private / 'worker.lock'):
        pass
    app = db.application(1)
    assert app['attempts'] == 2 and app['attempt_started'] == 0
    assert app['error_category'] == 'BROWSER_START_FAILED'
    assert app['application_state'] != 'UNKNOWN' and not app['submit_intent_at']
    assert not app['retry_allowed'] and app['delivery_state'] == Delivery.NOT_STARTED
    assert db.rows('SELECT * FROM events')[:len(history)] == history
    assert not db.one("SELECT * FROM events WHERE kind='APPLICATION_ATTEMPT_STARTED'")
    assert not engine.browser.context and not engine.browser.leases
    await engine.close()


async def test_recovery_counts_one_attempt_immediately_before_navigation(config, db, listing, launches, monkeypatch):
    db.ingest(listing, config)
    launches.contexts.extend([Context([1]), Context()])
    engine = Engine(config, db)
    async def navigate(page, url):
        assert db.application(1)['attempts'] == 1
        assert page in engine.browser.leases
        assert not engine.browser.health_pages and engine.browser.health_ready
        return State.CLOSED, 'offline fixture'
    monkeypatch.setattr(engine.browser, 'navigate', navigate)
    monkeypatch.setattr(engine, 'security_gate', AsyncMock(return_value=False))
    assert await engine.process_one(1)
    assert db.application(1)['attempts'] == 1
    assert engine.browser.startup_telemetry['browser_start_count'] == 2
    await engine.close()


@pytest.mark.parametrize('evidence', ['interaction', 'upload', 'ambiguous', 'intent', 'security'])
async def test_startup_restart_forbidden_after_application_work(config, db, listing, launches, evidence):
    db.ingest(listing, config)
    db.claim(1)
    engine = Engine(config, db)
    if evidence in {'interaction', 'upload'}:
        db.lifecycle.begin_attempt(1, 3)
    elif evidence == 'ambiguous':
        db.lifecycle.delivery(1, Delivery.POSSIBLY_DELIVERED)
    elif evidence == 'intent':
        db.execute("UPDATE applications SET submit_intent_at='fixture' WHERE id=1")
    else:
        db.update_security(1, security_state='INTERACTIVE_CHALLENGE')
    with pytest.raises(ValueError, match='does not authorize'):
        await engine.open_application_page(1)
    assert not launches.drivers


async def test_created_page_then_setup_failure_is_never_replayed(config, launches, monkeypatch):
    launches.contexts.append(Context())
    browser = Browser(config)
    monkeypatch.setattr(browser, 'ensure_desktop', AsyncMock(side_effect=RuntimeError(TARGET_FAILURE)))
    with pytest.raises(RuntimeError, match='Target.createTarget'):
        await browser.new_page()
    assert len(launches.drivers) == 1 and launches.sleeps == []
    assert browser.application_tabs_opened == 1 and not browser.leases
    await browser.close()


async def test_closed_interacted_page_does_not_reauthorize_browser_recovery(config, launches):
    context = Context([3])  # Probe succeeds, application opens, later creation fails.
    launches.contexts.append(context)
    browser = Browser(config)
    page = await browser.new_page()
    page.url = 'https://fixture.invalid/interacted-form'
    await page.close()
    assert not browser.leases and not browser.application_pages
    with pytest.raises(RuntimeError, match='Target.createTarget'):
        await browser.new_page()
    assert len(launches.drivers) == 1 and not launches.sleeps
    await browser.close()


async def test_probe_context_closes_then_recovers(config, launches):
    first = Context()
    first.new_page = AsyncMock(side_effect=RuntimeError('Target page, context or browser has been closed'))
    launches.contexts.extend([first, Context()])
    browser = Browser(config)
    await browser.start()
    assert first.closed and browser.health_ready
    await browser.close()


async def test_unexpected_restored_target_stops_without_restart(config, launches):
    context = Context()
    context.pages[0].url = 'https://unexpected.invalid/form'
    launches.contexts.append(context)
    with pytest.raises(BrowserStartFailed):
        await Browser(config).new_page()
    assert context.closed and not launches.sleeps and len(launches.drivers) == 1


@pytest.mark.parametrize('lock', ['SingletonLock', 'SingletonCookie', 'SingletonSocket'])
def test_ambiguous_profile_lock_is_never_removed(config, monkeypatch, lock):
    monkeypatch.setattr('autoapply.browser_resources.process_inventory', lambda: {})
    owner = DedicatedResources(config.private)
    owner.profile.mkdir()
    path = owner.profile / lock
    path.write_text('unproven ownership')
    try:
        with pytest.raises(BrowserOwnershipError, match='ambiguous'):
            owner.acquire()
        assert path.read_text() == 'unproven ownership'
    finally:
        owner.release()


def test_only_owned_orphans_reaped_and_personal_processes_untouched(config, monkeypatch):
    import autoapply.browser_resources as module
    owner = DedicatedResources(config.private)
    def row(pid, parent, command):
        return dict(pid=pid, parent=parent, birth=str(pid), command=command)
    rows = {10: row(10, os.getpid(), 'node playwright run-driver'),
            11: row(11, 10, f'chrome --user-data-dir={owner.profile} {owner.marker}'),
            12: row(12, 11, 'chrome --type=renderer'),
            # An older orphan can retain a parent PID subsequently reused by us.
            5: row(5, 11, 'chrome --type=renderer'),
            20: row(20, 1, 'chrome --user-data-dir=personal'),
            21: row(21, 20, 'chrome --type=renderer'),
            30: row(30, 1, 'node playwright run-driver')}
    monkeypatch.setattr(module, 'process_inventory', lambda: dict(rows))
    monkeypatch.setattr(module, 'bind_process_identity', lambda record: record)
    killed = []
    def terminate(record):
        killed.append(record['pid'])
        rows.pop(record['pid'])
    monkeypatch.setattr(module, 'terminate_owned', terminate)
    owner.capture(10)
    assert set(owner.owned) == {10, 11, 12}
    owner.reap()
    assert set(killed) == {10, 11, 12} and set(rows) == {5, 20, 21, 30}
    assert not owner.owned


def test_existing_dedicated_process_is_conflict_not_kill_authority(config, monkeypatch):
    owner = DedicatedResources(config.private)
    rows = {42: dict(pid=42, parent=1, birth='old', command=f'chrome --user-data-dir={owner.profile}')}
    monkeypatch.setattr('autoapply.browser_resources.process_inventory', lambda: rows)
    owner.capture()
    assert not owner.owned
    with pytest.raises(BrowserOwnershipError):
        owner.inspect()


def test_profile_redirect_is_rejected_before_process_work(config, monkeypatch):
    from pathlib import Path
    original = Path.resolve
    def redirected(path, *args, **kwargs):
        if path.name == 'browser_profile':
            return config.private / 'personal-profile'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', redirected)
    with pytest.raises(BrowserOwnershipError, match='redirects'):
        DedicatedResources(config.private)


def test_pid_reuse_is_not_terminated(config, monkeypatch):
    owner = DedicatedResources(config.private)
    owner.owned[42] = dict(pid=42, birth='old')
    monkeypatch.setattr('autoapply.browser_resources.process_inventory', lambda: {42: dict(pid=42, birth='new')})
    def forbidden(record):
        pytest.fail('Unowned process was terminated')
    monkeypatch.setattr('autoapply.browser_resources.terminate_owned', forbidden)
    owner.reap()
    assert not owner.owned


async def test_cleanup_failure_prevents_relaunch(config, launches, monkeypatch):
    launches.contexts.append(Context([1]))
    monkeypatch.setattr(Resources, 'reap', lambda self: (_ for _ in ()).throw(BrowserOwnershipError('cleanup')))
    with pytest.raises(BrowserStartFailed, match='cleanup'):
        await Browser(config).new_page()
    assert len(launches.drivers) == 1 and not launches.sleeps


async def test_probe_cancellation_cleans_without_retry(config, launches):
    context = Context()
    context.new_page = AsyncMock(side_effect=asyncio.CancelledError())
    launches.contexts.append(context)
    browser = Browser(config)
    with pytest.raises(asyncio.CancelledError):
        await browser.start()
    assert context.closed and not browser.leases and not launches.sleeps


@pytest.mark.browser
@pytest.mark.parametrize('headless', [True, False])
async def test_real_blank_health_and_cleanup(config, headless):
    config.data['browser']['headless'] = headless
    browser = Browser(config)
    browser.max_active_application_tabs = 1
    try:
        await browser.start()
        assert browser.health_ready and not browser.leases and not browser.application_pages
        assert not browser.health_pages and set(browser.context.pages) == browser.startup_pages
        assert not browser.application_tabs_opened
        owner = browser.resources
        assert owner.owned  # OS inventory proves actual local process ownership.
        page = await browser.new_page()
        assert page.url == 'about:blank' and browser.application_tabs_opened == 1
        assert not browser.startup_pages and browser.context.pages == [page]
    finally:
        await browser.close()
    assert not owner.owned and not owner.locked
    assert browser.context is browser.playwright is None


@pytest.mark.parametrize('component', ['context', 'driver'])
async def test_os_cleanup_allows_restart_when_graceful_close_fails(config, launches, component):
    first = Context([1])
    launches.contexts.extend([first, Context()])
    if component == 'context':
        first.close = AsyncMock(side_effect=RuntimeError('broken context close'))
    else:
        async def new_page():
            launches.drivers[0].stop.side_effect = RuntimeError('broken driver stop')
            raise RuntimeError(TARGET_FAILURE)
        first.new_page = new_page
    browser = Browser(config)
    await browser.new_page()
    assert len(launches.drivers) == 2 and launches.owners[0].reaped
    assert any('cleanup_recovered' in d for d in browser.startup_diagnostics)
    await browser.close()


async def test_delivery_becomes_uncertain_during_probe_no_restart(config, db, listing, launches):
    db.ingest(listing, config)
    db.claim(1)
    context = Context()
    async def fail():
        db.lifecycle.delivery(1, Delivery.POSSIBLY_DELIVERED)
        raise RuntimeError(TARGET_FAILURE)
    context.new_page = fail
    launches.contexts.append(context)
    engine = Engine(config, db)
    with pytest.raises(RuntimeError, match='Target.createTarget'):
        await engine.open_application_page(1)
    assert context.closed and not launches.sleeps
    assert db.application(1)['delivery_state'] == Delivery.POSSIBLY_DELIVERED
    assert not engine.browser.context


def test_locked_windows_profile_file_fails_safely(config, monkeypatch):
    owner = DedicatedResources(config.private)
    owner.profile.mkdir()
    lock = owner.profile / 'lockfile'
    lock.write_text('existing')
    monkeypatch.setattr('autoapply.browser_resources.process_inventory', lambda: {})
    def locked(path):
        raise BrowserOwnershipError('lock is owned by unknown process')
    monkeypatch.setattr('autoapply.browser_resources.check_profile_lock', locked)
    with pytest.raises(BrowserOwnershipError):
        owner.inspect()
    assert lock.read_text() == 'existing'


async def test_application_retry_budget_checked_before_launch(config, db, listing, launches):
    db.ingest(listing, config)
    db.claim(1)
    db.execute('UPDATE applications SET attempts=10 WHERE id=1')
    with pytest.raises(ValueError, match='budget exhausted'):
        await Engine(config, db).open_application_page(1)
    assert db.application(1)['attempts'] == 10 and not launches.drivers


async def test_closed_reconstruction_page_does_not_start_attempt(config, db, listing):
    from autoapply.fill_batch import verify_reconstruction
    from autoapply.operations import FileSnapshot
    db.ingest(listing, config)
    db.set_setting('auto_submit', False)
    db.lifecycle.metadata(1, resume_sha256=FileSnapshot.read(config.resume).sha256)
    engine = Engine(config, db, fill_only=True)
    page = Page(Context())
    await page.close()
    engine.retained_pages[1] = page
    with pytest.raises(RuntimeError, match='live application page is unavailable'):
        await verify_reconstruction(engine, 1)
    assert db.application(1)['attempts'] == 0


async def test_hung_startup_operations_have_an_absolute_budget(config, launches, monkeypatch):
    contexts = [Context() for _ in range(3)]
    async def hung():
        await asyncio.Event().wait()
    for context in contexts:
        context.new_page = hung
    launches.contexts.extend(contexts)
    monkeypatch.setattr('autoapply.browser.STARTUP_ATTEMPT_TIMEOUT', .05)
    browser = Browser(config)
    with pytest.raises(BrowserStartFailed):
        await browser.start()
    assert all(c.closed for c in contexts) and len(launches.drivers) == 3
    assert launches.sleeps == [4, 4]


async def test_startup_telemetry_failure_releases_page_before_transfer(config, db, listing, launches, monkeypatch):
    db.ingest(listing, config)
    db.claim(1)
    launches.contexts.append(Context())
    engine = Engine(config, db)
    def fail(*args, **kwargs):
        raise OSError('event storage unavailable')
    monkeypatch.setattr(db, 'event', fail)
    with pytest.raises(OSError):
        await engine.open_application_page(1)
    assert not engine.browser.leases and not engine.browser.context.pages
    assert db.application(1)['attempts'] == 0
    await engine.close()


@pytest.mark.parametrize('partial_failure', [False, True])
async def test_headed_startup_anchor_is_separate_and_partial_target_is_cleaned(config, launches, partial_failure):
    config.data['browser']['headless'] = False
    context = Context()
    launches.contexts.append(context)
    browser = Browser(config)
    browser.max_active_application_tabs = 1
    await browser.start()
    assert len(browser.startup_pages) == 1 and not browser.application_pages and not browser.leases
    assert not browser.health_pages and browser.application_tabs_opened == 0
    await browser.enforce_application_limit(1)
    assert len(context.pages) == 1
    if partial_failure:
        original = context.new_page
        async def fail_after_creation():
            await original()
            raise RuntimeError('creation returned error after page event')
        context.new_page = fail_after_creation
        with pytest.raises(RuntimeError, match='creation returned error'):
            await browser.new_page()
        assert not browser.leases and not browser.application_pages
        assert not launches.sleeps  # A target existed; never replay.
    else:
        page = await browser.new_page()
        assert page.context is context and browser.application_tabs_opened == 1
        assert not browser.startup_pages and context.pages == [page]
        assert browser.max_tabs_observed == 1
    await browser.close()


@pytest.mark.browser
def test_real_owned_orphan_termination_preserves_unowned_child(config):
    import subprocess
    import sys
    owner = DedicatedResources(config.private)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    owned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)',
                              '--user-data-dir=' + str(owner.profile), owner.marker], **options)
    unowned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], **options)
    try:
        owner.capture()
        assert owned.pid in owner.owned and unowned.pid not in owner.owned
        owner.reap()
        assert owned.wait(timeout=5) is not None
        assert unowned.poll() is None
        assert not owner.owned
    finally:
        # Both Popen handles were created by this fixture, never personal processes.
        for process in (owned, unowned):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)

