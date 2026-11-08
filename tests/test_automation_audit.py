"""T006 offline regressions: no employer or Discord access."""
import asyncio
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

from autoapply.applications import GenericApplicationAdapter, FIELD_SCRIPT
from autoapply.conflicts import ConflictIndex, identity_match, submission_conflict
from autoapply.consent import dismiss_overlay, pass_sticky_header
from autoapply.database import Database
from autoapply.form_validation import VALIDATION_JS
from autoapply.models import Answer
from autoapply.semi_auto import SemiAutoLoop

FIXTURES = Path(__file__).parent / 'fixtures' / 'audit'


@pytest.fixture
async def audit_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.abort())
        page = await context.new_page()
        yield page
        await browser.close()


async def test_slow_discovery_services_event_loop_with_thread_owned_sqlite(config, db, monkeypatch):
    config.data['application']['auto_submit'] = False
    db.set_setting('auto_submit', False)
    db.set_setting('paused', False)
    loop = SemiAutoLoop(config, db)
    loop.publish = lambda: None
    owner = threading.get_ident()
    started = threading.Event()
    finished = threading.Event()
    def slow_discover(reader, config, seen):
        assert threading.get_ident() != owner
        assert reader is not db and reader.conn is not db.conn
        assert reader.setting('auto_submit') is False
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            reader.conn.execute("UPDATE settings SET value='true' WHERE key='auto_submit'")
        started.set()
        time.sleep(.2)
        finished.set()
        return []
    monkeypatch.setattr('autoapply.semi_auto.discover', slow_discover)
    task = asyncio.create_task(loop.fill_batch())
    ticks = 0
    while not task.done():
        await asyncio.sleep(.01)
        if started.is_set() and not finished.is_set():
            ticks += 1
            # The original connection remains usable on the owning thread.
            assert db.setting('auto_submit') is False
    await task
    assert ticks >= 5


def unindexed_conflict(db, app_id):
    """Independent linear version of the original protected identity policy."""
    app = db.application(app_id)
    if db.automation_retired(app_id) or app['submit_intent_at'] or app['submission_confirmation_seen']:
        return app
    rows = db.rows("""SELECT a.*,j.company,j.title,j.ats,j.canonical_url FROM applications a
        JOIN jobs j ON j.id=a.job_id WHERE
        (a.submit_intent_at IS NOT NULL OR a.submission_confirmation_seen=1 OR
         a.status IN ('SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REVIEW'))""")
    seen = {str(app_id)} | {str(r['id']) for r in rows if r['id'] != app_id}
    for other in rows:
        if other['id'] != app_id and (match := identity_match(app, other)):
            return dict(other, identity_match=match)
    for other in db.history.list_applications():
        protected = (other.get('submit_intent_at') or other.get('submission_confirmation_seen') or
                     other['application_state'] in {'SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REQUIRED','UNKNOWN'})
        if protected and other['application_id'] not in seen and (match := identity_match(app, other)):
            return dict(other, id=other.get('id', other['application_id']),
                        status=other.get('status', other['application_state']), identity_match=match)
    return None


def test_conflict_index_equals_linear_policy(config, db, listing, monkeypatch):
    # Distinct seed identities let us emulate legacy DB rows with shared URLs.
    urls = ['https://jobs.lever.co/example/exact', 'https://jobs.lever.co/example/exact',
            'https://boards.greenhouse.io/example/jobs/123', 'https://job-boards.greenhouse.io/example/jobs/123',
            'https://legacy.example.test/careers', 'https://jobs.lever.co/example/new',
            'https://jobs.lever.co/example/clear', 'https://jobs.lever.co/example/archived']
    for n, url in enumerate(urls, 1):
        db.ingest(replace(listing, company=f'Company {n}', url=f'https://jobs.lever.co/seed/{n}'), config)
        db.execute('UPDATE jobs SET canonical_url=?,company=? WHERE id=?',
                   (url, 'Legacy Co' if n in {5, 6} else f'Company {n}', n))
    for n in (1, 3, 5):
        db.execute("UPDATE applications SET status='MANUAL_REVIEW' WHERE id=?", (n,))
    history = [dict(application_id='900', id=900, application_state='UNKNOWN',
                    company='Archive Co', title=listing.title, canonical_url=urls[7]),
               # Archive copies of SQLite records must be suppressed.
               dict(application_id='1', application_state='SUBMITTED', company='Company 7',
                    title=listing.title, canonical_url=urls[6])]
    monkeypatch.setattr(db.history, 'list_applications', lambda: history)
    expected = {i: unindexed_conflict(db, i) for i in range(1, 9)}
    assert expected[2]['identity_match'] == 'exact_protected_duplicate'
    assert expected[4]['identity_match'] == 'exact_protected_duplicate'
    assert expected[6]['identity_match'] == 'ambiguous_legacy_identity'
    assert expected[7] is None and expected[8]['id'] == 900
    calls = 0
    def scan():
        nonlocal calls
        calls += 1
        return history
    monkeypatch.setattr(db.history, 'list_applications', scan)
    db._conflict_index = ConflictIndex(db)
    try:
        assert {i: submission_conflict(db, i) for i in range(1, 9)} == expected
        assert calls == 1
    finally:
        db._conflict_index = None


async def test_offline_inventory_and_verified_filling(audit_page):
    await audit_page.set_content((FIXTURES / 'controls.html').read_text())
    adapter = GenericApplicationAdapter(audit_page)
    questions = await adapter.get_questions({'id': 1, 'company': 'Fixture'})
    by_label = {q.label: q for q in questions}
    expected = {'Preferred schedule': ('radio', ['Full time', 'Part time']),
                'Available weekends': ('checkbox', ['Yes', 'No']),
                'City': ('text', []), 'Portfolio URL': ('text', []),
                'Why this role?': ('textarea', []), 'Team': ('combobox', ['Engineering', 'Design']),
                'Last name': ('text', []), 'Middle name': ('text', [])}
    assert {q.label: (q.kind, q.options) for q in questions} == expected
    assert by_label['Preferred schedule'].required
    assert await audit_page.evaluate(VALIDATION_JS)
    for label, value in [('Preferred schedule', 'Part time'), ('Available weekends', 'Yes'),
                         ('Why this role?', 'Building useful software.'), ('Team', 'Engineering'),
                         ('Last name', 'Student'), ('Middle name', 'Fixture')]:
        await adapter.answer_question(by_label[label], Answer(value, 'fixture'))
    assert not await adapter.validate()
    current = await audit_page.evaluate(FIELD_SCRIPT)
    assert next(f for f in current if f['label'] == 'Preferred schedule')['value'] == 'Part time'
    assert next(f for f in current if f['label'] == 'Why this role?')['value'] == 'Building useful software.'
    assert next(q for q in await adapter.get_questions({'id': 1, 'company': 'Fixture'}) if q.label == 'Why this role?').value == 'Building useful software.'
    assert not await audit_page.evaluate('Boolean(window.finalClicked)')
    await audit_page.locator('#shadow-host input').fill('Changed')
    assert any('Live control changed' in issue for issue in await adapter.validate())


@pytest.mark.parametrize('fixture,close_label', [('chat.html', 'Close chat'), ('newsletter.html', 'No thanks')])
async def test_clutter_closes_only_safe_controls(audit_page, fixture, close_label):
    await audit_page.set_content((FIXTURES / fixture).read_text())
    clicked = []
    async def guarded_click(page, target):
        clicked.append(await target.get_attribute('aria-label') or await target.inner_text())
        assert not await target.evaluate("e => e.type==='submit'")
        await target.click()
    assert await dismiss_overlay(audit_page, guarded_click)
    assert clicked == [close_label]


@pytest.mark.parametrize('inputs', [
    '<input aria-label="Email">', '<div contenteditable="true">Essay</div>',
    '<button type="button" role="switch" aria-checked="false">Available</button>',
    '<div id="host"></div><script>host.attachShadow({mode:"open"}).innerHTML="<input>"</script>',
    '<iframe srcdoc="<input>"></iframe>',
])
async def test_clutter_preserves_every_dialog_with_application_inputs(audit_page, inputs):
    await audit_page.set_content(f'<div role="dialog"><h2>Join our talent community</h2>{inputs}<button type="button">Close</button></div>')
    click = AsyncMock()
    assert not await dismiss_overlay(audit_page, click)
    click.assert_not_awaited()


@pytest.mark.parametrize('content', [
    '<h2>Newsletter</h2><button type="submit">Close</button><button>Accept</button>',
    '<h2>Identity verification</h2><button type="button">Close</button>',
    '<h2>CAPTCHA security challenge</h2><button type="button">Close</button>',
])
async def test_clutter_preserves_security_and_never_accepts_or_submits(audit_page, content):
    await audit_page.set_content(f'<div role="dialog">{content}</div>')
    click = AsyncMock()
    assert not await dismiss_overlay(audit_page, click)
    click.assert_not_awaited()


async def test_sticky_header_pass_scrolls_without_hiding_or_clicking(audit_page):
    await audit_page.set_content('<style>body{margin:0;height:2000px}header{position:fixed;top:0;height:100px;width:100%;background:white;z-index:9}input{position:absolute;top:500px}</style><header>Navigation</header><input aria-label="Name">')
    await audit_page.evaluate('scrollTo(0,480)')
    target = audit_page.get_by_label('Name')
    await pass_sticky_header(target)
    assert await target.evaluate('e=>e.getBoundingClientRect().top') >= 100
    assert await audit_page.locator('header').is_visible()
    assert await target.input_value() == ''


async def test_same_origin_iframe_without_url_keywords_and_foreign_frame_boundary(audit_page):
    async def fixture(route):
        path = route.request.url
        if path.endswith('/outer'):
            html = '<label>First name<input></label><iframe src="/embedded"></iframe><iframe src="https://other.example.test/widget"></iframe>'
        elif path.endswith('/embedded'):
            html = '<label>Last name<input></label>'
        else:
            html = '<label>Unrelated chat email<input></label>'
        await route.fulfill(content_type='text/html', body=html)
    await audit_page.context.route('**/*', fixture)
    await audit_page.goto('https://fixture.example.test/outer')
    adapter = GenericApplicationAdapter(audit_page)
    questions = await adapter.get_questions({'id': 1})
    assert [q.label for q in questions] == ['First name', 'Last name']


async def test_unsafe_custom_submit_controls_are_never_inventoried(audit_page):
    await audit_page.set_content('<form><div role="radiogroup" aria-required="true" aria-label="Schedule"><button role="radio" aria-checked="false">Full time</button></div><button aria-haspopup="listbox" aria-label="Team">Choose</button></form>')
    assert await audit_page.evaluate(FIELD_SCRIPT) == []
    assert await audit_page.evaluate(VALIDATION_JS)


def test_all_operator_protected_ids_excluded_from_fill_and_reruns():
    from autoapply.fill_batch import PROTECTED as FILL_PROTECTED
    from autoapply.semi_auto import PROTECTED
    assert {3556, 4522, 6397, 4611} <= FILL_PROTECTED & PROTECTED


@pytest.mark.parametrize('app_id', [3556, 4522, 6397, 4611])
async def test_protected_application_never_claimed_or_changed(config, db, app_id):
    from autoapply.engine import Engine
    engine = Engine(config, db, fill_only=True)
    engine._process_one = AsyncMock(side_effect=AssertionError('Protected application touched'))
    assert await engine.process_one(app_id) is False
    engine._process_one.assert_not_awaited()
    assert db.rows('SELECT * FROM events') == []


@pytest.mark.parametrize('wrapper', ['<div hidden>{}</div>', '<div style="opacity:0">{}</div>', '<div aria-busy="true">{}</div>'])
async def test_workday_visibility_override_retained(audit_page, wrapper):
    from autoapply.workday import WorkdayAdapter
    await audit_page.set_content('<form><label>First name<input></label>' + wrapper.format('<label>Resume<input type="file"></label>') + '</form>')
    fields = await audit_page.evaluate(WorkdayAdapter.field_script)
    assert [f['label'] for f in fields] == ['First name']


async def test_resume_in_open_shadow_root_verifies_upload_without_submission(audit_page, config):
    await audit_page.set_content('<form><div id="host"></div></form><script>host.attachShadow({mode:"open"}).innerHTML=\'<label>Resume<input type="file" required></label>\'</script>')
    adapter = GenericApplicationAdapter(audit_page)
    questions = await adapter.get_questions({'id': 1})
    assert [(q.label, q.kind) for q in questions] == [('Resume', 'file')]
    await adapter.upload_documents(questions[0], config.resume)
    assert not await adapter.validate()
