"""Opt-in counters for local pytest fixtures; no production counters or I/O."""
import functools
import inspect
import json
import time
from collections import Counter
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption('--browser-work', default='.tmp/phase6/work.json')


@pytest.fixture(autouse=True)
def browser_work(request, monkeypatch):
    from playwright.async_api import Page, Frame, Locator, BrowserContext
    from autoapply import browser, combobox, uploads
    from autoapply.applications import GenericApplicationAdapter
    from autoapply.smartrecruiters import SmartRecruitersAdapter
    from autoapply.security import SecurityDetector
    from autoapply.cursor.controller import CursorController
    counts = Counter()
    started = time.monotonic()

    def wrap(owner, name, metric):
        original = getattr(owner, name)
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def counted(*args, **kwargs):
                counts[metric] += 1
                if name in {'evaluate', 'evaluate_all'} and len(args) > 1:
                    script = args[1]
                    if script == __import__('autoapply.applications', fromlist=['FIELD_SCRIPT']).FIELD_SCRIPT or 'phase6 bulk inventory' in script:
                        counts['full_inventories'] += 1
                result = await original(*args, **kwargs)
                if name == 'new_page' and isinstance(args[0], BrowserContext):
                    counts['max_context_pages'] = max(counts['max_context_pages'], len(args[0].pages))
                return result
        else:
            @functools.wraps(original)
            def counted(*args, **kwargs):
                counts[metric] += 1
                return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)

    for owner, name, metric in [
        (Page, 'evaluate', 'page_evaluations'), (Frame, 'evaluate', 'frame_evaluations'),
        (Locator, 'evaluate', 'control_evaluations'), (Locator, 'evaluate_all', 'bulk_control_evaluations'),
        (Page, 'locator', 'locator_constructions'), (Frame, 'locator', 'locator_constructions'),
        (Locator, 'count', 'control_state_reads'), (Locator, 'get_attribute', 'control_state_reads'),
        (Locator, 'is_visible', 'control_state_reads'), (Locator, 'input_value', 'control_state_reads'),
        (Locator, 'click', 'locator_clicks'), (Locator, 'fill', 'fills'),
        (Locator, 'all_text_contents', 'option_or_text_scans'),
        (Page, 'wait_for_timeout', 'fixed_browser_waits'),
        (BrowserContext, 'new_page', 'page_creations'),
        (SecurityDetector, 'snapshot', 'security_scans'),
        (GenericApplicationAdapter, 'get_questions', 'generic_inventory_calls'),
        (SmartRecruitersAdapter, 'get_questions', 'sr_inventory_calls'),
        (GenericApplicationAdapter, 'validate', 'validation_passes'),
        (SmartRecruitersAdapter, 'step_signature', 'transition_observations'),
        (combobox, 'options_for', 'option_searches'), (uploads, 'attachment_state', 'upload_observations'),
        (CursorController, '_geometry', 'cursor_geometry_reads'),
        (CursorController, '_viewport', 'cursor_viewport_reads'),
    ]:
        wrap(owner, name, metric)
    yield
    path = Path(request.config.getoption('--browser-work'))
    path.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(path.read_text()) if path.exists() else {}
    results[request.node.nodeid] = dict(counts=counts, seconds=round(time.monotonic()-started, 6))
    path.write_text(json.dumps(results, indent=2)+'\n')
