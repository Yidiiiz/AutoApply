"""Entry-only diagnostics: no answers, uploads, account creation, or submission."""
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .applications import adapter_for
from .batch_approval import is_final_submission, request_body
from .browser import classify_page_condition
from .freshness import closed_status
from .providers import detect_ats
from .security import SecurityDetector


@dataclass
class EntryResult:
    outcome: Literal['form_ready', 'account_required', 'closed', 'security_challenge', 'entry_failed']
    steps: list[str] = field(default_factory=list)
    field_count: int = 0
    ats: str = 'generic'
    error: str | None = None


async def install_submission_guard(context):
    """Keep the guard active for the entire context lifetime, including popups.

    Callers must use a context with service_workers='block'; intercepted routes
    cannot protect requests made by an already-installed service worker.
    """
    if hasattr(context, '_autoapply_entry_blocked'):
        return context._autoapply_entry_blocked
    blocked = []

    async def guard(route):
        request = route.request
        try:
            final = is_final_submission(request.url, request.method, request_body(request))
        except Exception:
            # An unreadable mutation cannot be established safe.
            final = request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
        if final:
            blocked.append('final_submission_aborted')
            await route.abort('blockedbyclient')
        else:
            await route.fallback()

    await context.route('**/*', guard)
    context._autoapply_entry_blocked = blocked
    return blocked


async def terminal_outcome(page):
    security, snapshot = await SecurityDetector().detect(
        page, getattr(page, '_autoapply_observation', None))
    condition, _ = classify_page_condition(security, snapshot)
    if security.blocking:
        return 'security_challenge'
    if str(condition) == 'AUTH_REQUIRED':
        return 'account_required'
    observation = getattr(page, '_autoapply_observation', {})
    if str(condition) == 'CLOSED' or closed_status(
            snapshot.get('condition_text', snapshot['text']), observation.get('status')):
        return 'closed'
    if condition:
        return 'entry_failed'
    return None


async def probe_entry(page, url) -> EntryResult:
    """Navigate and run ONLY adapter.begin() and passive readiness observation.

    Use a fresh page per listing. No applicant configuration or database is read.
    The route guard stays installed after return until the context is closed.
    """
    steps = ['submission_guard_installed']
    blocked = await install_submission_guard(page.context)
    blocked_before = len(blocked)
    page._autoapply_entry_only = True
    page._autoapply_entry_steps = steps
    ats = detect_ats(url).provider
    if ats == 'UNKNOWN':
        ats = 'generic'
    result = EntryResult('entry_failed', steps=steps, ats=ats)
    try:
        response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        page._autoapply_observation = {'status': response.status if response else None}
        steps.append('page_opened')
        terminal = await terminal_outcome(page)
        if terminal:
            result.outcome = terminal
        else:
            adapter = adapter_for(page)
            # Provider detection includes Workable, whose entry adapter is generic.
            result.ats = detect_ats(page.url).provider
            if result.ats == 'UNKNOWN':
                result.ats = adapter.name

            def record(kind, detail):
                state = detail.get('state')
                steps.append(f'{kind}:{state}' if state else kind)

            adapter.emit = record
            steps.append('adapter_begin:' + adapter.name)
            await adapter.begin()
            terminal = await terminal_outcome(page)
            if terminal:
                result.outcome = terminal
            else:
                sample = await adapter.transition_sample()
                result.field_count = sample['fields']
                # A lone submit button does not establish an actual form.
                if result.field_count and not sample['loading']:
                    result.outcome = 'form_ready'
                else:
                    result.error = 'FORM_NOT_READY'
    except Exception as exc:
        # Do not put page text, field values, or provider exception messages in reports.
        result.error = getattr(exc, 'category', type(exc).__name__)
        try:
            result.outcome = await terminal_outcome(page) or 'entry_failed'
        except Exception:
            result.outcome = 'entry_failed'
        if result.outcome in {'account_required', 'closed', 'security_challenge'}:
            result.error = None
    finally:
        if len(blocked) > blocked_before:
            steps.extend(blocked[blocked_before:])
            result.outcome = 'entry_failed'
            result.error = 'FINAL_SUBMISSION_BLOCKED'
        steps.append('outcome:' + result.outcome)
    return result


def select_listings(root, *, limit=5, ids=()):
    """Read active job IDs in priority order without Database startup/migrations."""
    if type(limit) is not int or limit < 1:
        raise ValueError('--limit must be positive')
    if any(type(i) is not int or i < 1 for i in ids):
        raise ValueError('--ids must be positive listing IDs')
    path = Path(root).resolve() / 'data/private/autoapply.sqlite3'
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as exc:
        raise RuntimeError('Cannot open the listing database read-only') from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        query = """SELECT id,company,title,canonical_url FROM jobs
            WHERE listing_active=1 AND listing_status='ACTIVE'"""
        params = []
        if ids:
            query += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
            params.extend(ids)
        query += ' ORDER BY priority DESC,id ASC LIMIT ?'
        params.append(limit)
        return [dict(row) for row in connection.execute(query, params)]
    except sqlite3.Error as exc:
        raise RuntimeError('Cannot select active listings from the existing database schema') from exc
    finally:
        connection.close()


async def run_entry_test(root, *, limit=5, ids=(), headed=False):
    """Operator opt-in only. Read listings, discard browser state, write one report."""
    from playwright.async_api import async_playwright

    listings = select_listings(root, limit=limit, ids=ids)
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'auto_submit': False, 'entry_only': True, 'listings': []}
    if listings:
        with TemporaryDirectory(prefix='autoapply-entry-') as profile:
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile, headless=not headed, accept_downloads=False,
                    service_workers='block', viewport={'width': 1440, 'height': 900},
                    args=['--disable-background-networking'])
                try:
                    context.set_default_timeout(10000)
                    await install_submission_guard(context)
                    for listing in listings:
                        page = await context.new_page()
                        try:
                            result = await probe_entry(page, listing['canonical_url'])
                            report['listings'].append(dict(listing, **asdict(result)))
                        finally:
                            # Includes unexpected popups; no forms are retained.
                            for opened in list(context.pages):
                                await opened.close()
                finally:
                    await context.close()
    destination = Path(root).resolve() / 'data/private/entry-test-report.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
