"""Opt-in synthetic test counters, emitted with the full offline suite."""
import functools
import inspect
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest


def pytest_addoption(parser):
    parser.addoption('--phase7-work', default='.tmp/phase7/control-work.json')


@pytest.fixture(autouse=True)
def phase7_work(request, monkeypatch):
    if not request.node.nodeid.startswith(('tests/test_phase7_fill.py', 'tests/test_phase7_lifecycle.py')):
        yield
        return
    from autoapply.browser import Browser
    from autoapply.database import Database
    from autoapply.engine import Engine
    from autoapply.manual import ManualCommand
    from autoapply.retry import RetryPolicy
    from autoapply.fill_batch import FillOnlyInvariant
    counts = Counter()
    snapshots = []
    def wrap(owner, name, metric):
        original = getattr(owner, name)
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def counted(*args, **kwargs):
                counts[metric] += 1
                return await original(*args, **kwargs)
        else:
            @functools.wraps(original)
            def counted(*args, **kwargs):
                counts[metric] += 1
                return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)
    for owner, name, metric in [(Browser,'new_page','application_opens'), (Browser,'navigate','navigations'),
                                (Engine,'_process_one','processing_passes'), (RetryPolicy,'decide','retry_decisions')]:
        wrap(owner,name,metric)
    original_rows = Database.rows
    def rows(self, sql, *args, **kwargs):
        if 'a.id!=?' in sql and 'a.submit_intent_at IS NOT NULL' in sql:
            counts['protected_set_scans'] += 1
        return original_rows(self,sql,*args,**kwargs)
    monkeypatch.setattr(Database,'rows',rows)
    original_snapshot = FillOnlyInvariant.snapshot
    def snapshot(self):
        result = original_snapshot(self)
        snapshots.append(result)
        return result
    monkeypatch.setattr(FillOnlyInvariant,'snapshot',snapshot)
    with patch.object(ManualCommand,'parse',side_effect=ManualCommand.parse) as parse:
        yield
        counts['command_parses'] = parse.call_count
    path = Path(request.config.getoption('--phase7-work'))
    results = json.loads(path.read_text()) if path.exists() else {}
    results[request.node.nodeid] = dict(counts=counts, last_invariant=snapshots[-1] if snapshots else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results,indent=2)+'\n')
