"""CIM patience and fail-closed cleanup; disposable fixtures only."""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from autoapply.browser import Browser
from autoapply.browser_resources import DedicatedResources
from autoapply.config import Config
from autoapply.runtime import ProcessLock


async def test_slow_windows_inventory_finishes_after_old_deadline_and_releases_locks(config, monkeypatch):
    import autoapply.browser_resources as module
    assert os.name == 'nt', 'This acceptance regression requires Windows'
    original = subprocess.run
    calls = []
    owner = DedicatedResources(config.private)
    owner.lock.__enter__()
    owner.locked = True
    def slow_inventory(args, **kwargs):
        calls.append(kwargs['timeout'])
        assert owner.diagnostics['inventory']['state'] == 'still_working'
        if len(calls) == 1:
            # Exercise the actual subprocess timeout with 16 seconds of real work.
            return original([sys.executable, '-c', 'import time; time.sleep(16); print("[]")'], **kwargs)
        return SimpleNamespace(stdout='[]')
    monkeypatch.setattr(module.subprocess, 'run', slow_inventory)
    monkeypatch.setattr(module, 'terminate_owned', lambda r: pytest.fail('Unproven process killed'))
    browser = Browser(config)
    browser.resources = owner
    started = time.monotonic()
    with ProcessLock(config.private / 'worker.lock'):
        await browser.close()
    assert 15 < time.monotonic() - started < 30
    assert calls == [30, 30, 30]  # Capture, pre-reap, exit verification: no timeout retries.
    assert owner.diagnostics['inventory']['state'] == 'completed'
    assert not owner.owned and not owner.locked
    assert browser.context is browser.playwright is browser.resources is None
    assert not browser.application_pages and not browser.leases
    with ProcessLock(config.private / 'worker.lock'), ProcessLock(config.private / 'browser-profile.lock'):
        pass
    await browser.close()  # Idempotent.


async def test_exhausted_inventory_fails_closed_without_killing_unproven_processes(config, monkeypatch):
    import autoapply.browser_resources as module
    owner = DedicatedResources(config.private)
    owner.lock.__enter__()
    owner.locked = True
    owner.owned[42] = dict(pid=42, birth='fixture')
    calls = []
    def exhausted():
        calls.append(1)
        raise subprocess.TimeoutExpired('read-only fixture CIM', 30)
    monkeypatch.setattr(module, 'process_inventory', exhausted)
    monkeypatch.setattr(module, 'terminate_owned', lambda r: pytest.fail('Unproven process killed'))
    browser = Browser(config)
    browser.resources = owner
    with pytest.raises(subprocess.TimeoutExpired):
        await browser.close()
    assert owner.diagnostics['inventory']['state'] == 'timed_out'
    assert owner.owned and browser.resources is owner  # Unverified ownership is retained.
    assert len(calls) == 2  # Capture failure, independent final reap verification; no restart loop.
    assert not owner.locked


@pytest.mark.parametrize('budget', [0, -1, True, float('inf'), float('nan'), 121])
def test_inventory_budget_is_finite_and_validated(config, budget):
    with pytest.raises(ValueError, match='process_inventory_timeout_seconds'):
        Config(config.root, {'browser': {'process_inventory_timeout_seconds': budget}})


def test_configurable_inventory_budget_and_definitive_failure(config, monkeypatch):
    import autoapply.browser_resources as module
    owner = DedicatedResources(config.private)
    owner.inventory_timeout_seconds = 45
    calls = []
    def failed(timeout_seconds):
        calls.append(timeout_seconds)
        raise subprocess.CalledProcessError(1, 'fixture CIM')
    monkeypatch.setattr(module, 'process_inventory', failed)
    with pytest.raises(subprocess.CalledProcessError):
        owner.capture()
    assert calls == [45] and owner.diagnostics['inventory']['state'] == 'definitively_failed'
