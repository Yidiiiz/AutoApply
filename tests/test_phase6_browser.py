from pathlib import Path

import pytest

from autoapply.applications import GenericApplicationAdapter
from autoapply.smartrecruiters import SmartRecruitersAdapter
from test_browser import browser  # noqa: F401


@pytest.mark.parametrize('fixture,adapter_type', [
    ('application.html', GenericApplicationAdapter),
    ('greenhouse-controls.html', GenericApplicationAdapter),
    ('smartrecruiters-controls.html', SmartRecruitersAdapter),
])
async def test_repeated_local_inventory(browser, config, fixture, adapter_type):
    page = await browser.new_page()
    await page.set_content((Path(__file__).parent/'fixtures'/fixture).read_text())
    adapter = adapter_type(page)
    adapter.profile = config.profile_snapshot().facts
    first = await adapter.get_questions({'id': 1})
    for _ in range(3):
        current = await adapter.get_questions({'id': 1})
        assert [(q.key, q.label, q.options) for q in current] == [(q.key, q.label, q.options) for q in first]


async def test_conditional_local_inventory(browser):
    page = await browser.new_page()
    await page.set_content((Path(__file__).parent/'fixtures/application.html').read_text())
    adapter = GenericApplicationAdapter(page)
    first = await adapter.get_questions({'id': 1})
    # The local fixture's existing Last Name field becomes conditional.
    await page.locator('#last').evaluate("e => e.hidden=true")
    hidden = await adapter.get_questions({'id': 1})
    assert len(hidden) == len(first)-1
    await page.locator('#first').evaluate("e => e.oninput=()=>document.querySelector('#last').hidden=false")
    await page.locator('#first').fill('Test')
    assert len(await adapter.get_questions({'id': 1})) == len(first)


@pytest.mark.parametrize('change', ['value', 'required', 'replacement', 'option', 'label', 'validation', 'challenge', 'navigation'])
async def test_snapshot_invalidation_and_fresh_boundaries(browser, change):
    from autoapply.models import Answer
    from autoapply.security import SubmissionClassifier, PreSubmitState
    from autoapply.applications import UnsupportedForm
    page = await browser.new_page()
    await page.set_content('<form><label for="f">First name</label><input id="f" value="Test"><select aria-label="Choice"><option>A</option></select></form>')
    adapter = GenericApplicationAdapter(page)
    questions = await adapter.get_questions({'id':1})
    old = adapter._step_snapshot
    assert old
    result = await SubmissionClassifier().classify(page)
    # Returned dictionaries and mutable Questions cannot modify the snapshot.
    result.snapshot['text'] = 'tampered'
    questions[0].label = 'tampered'
    assert 'tampered' not in result.snapshot['text']
    assert (await adapter.get_questions({'id':1}))[0].label == 'First name'
    actions = {
        'value': "document.querySelector('#f').value='Changed'",
        'required': "document.querySelector('#f').required=true",
        'replacement': "document.querySelector('#f').outerHTML=document.querySelector('#f').outerHTML",
        'option': "document.querySelector('select').insertAdjacentHTML('beforeend','<option>B</option>')",
        'label': "document.querySelector('label').textContent='Other question'",
        'validation': "document.querySelector('#f').setCustomValidity('Invalid')",
        'challenge': "document.body.insertAdjacentHTML('beforeend','<h1>Verify you are human</h1>')",
    }
    if change == 'navigation':
        await page.goto('about:blank#next')
    else:
        await page.evaluate(actions[change])
    fresh = await adapter.get_questions({'id':1})
    assert adapter._step_snapshot is not old
    if change == 'value':
        assert fresh[0].value == 'Changed'
    elif change == 'option':
        assert fresh[1].options == ['A', 'B']
    elif change == 'required':
        assert fresh[0].required
    elif change == 'label':
        assert fresh[0].label == 'Other question'
    elif change == 'validation':
        assert (await SubmissionClassifier().pre_submit(page, adapter))[0] == PreSubmitState.VALIDATION_ERROR
    elif change == 'challenge':
        with pytest.raises(UnsupportedForm, match='security'):
            await adapter.answer_question(fresh[0], Answer('Test', 'fixture'))
        assert (await SubmissionClassifier().classify(page)).security.blocking


async def test_value_refresh_avoids_full_inventory(browser, monkeypatch):
    from playwright.async_api import Frame
    from autoapply.applications import FIELD_SCRIPT
    count = 0
    original = Frame.evaluate
    async def evaluated(frame, script, *args, **kwargs):
        nonlocal count
        count += script == FIELD_SCRIPT
        return await original(frame, script, *args, **kwargs)
    monkeypatch.setattr(Frame, 'evaluate', evaluated)
    page = await browser.new_page()
    await page.set_content('<label>Name<input id="f"></label>')
    adapter = GenericApplicationAdapter(page)
    await adapter.get_questions({'id':1})
    await page.locator('#f').fill('Test')
    assert (await adapter.get_questions({'id':1}))[0].value == 'Test'
    assert count == 1


async def test_changed_dropdown_options_cannot_replay(browser):
    from autoapply.models import Answer
    from autoapply.applications import UnsupportedForm
    page = await browser.new_page()
    await page.set_content('''<label for="f">Choice</label><input id="f" role="combobox" aria-expanded="true" aria-controls="menu">
      <div id="menu" role="listbox"><div role="option">A</div></div>''')
    adapter = GenericApplicationAdapter(page)
    q, = await adapter.get_questions({'id':1})
    assert q.options == ['A']
    await page.locator('[role=option]').evaluate("e=>e.textContent='B'")
    with pytest.raises(UnsupportedForm, match='options changed'):
        await adapter.answer_question(q, Answer('A', 'verified_memory'))


@pytest.mark.parametrize('change', ['removed', 'busy', 'warning', 'late_failure', 'replacement'])
async def test_upload_receipt_reuse_and_invalidation(browser, config, monkeypatch, change):
    from autoapply import uploads
    page = await browser.new_page()
    await page.set_content('<label>Resume<input type="file" id="resume"></label>')
    adapter = GenericApplicationAdapter(page)
    q, = await adapter.get_questions({'id':1})
    await adapter.upload_documents(q, config.resume)
    assert await browser.uploads_ready(page, adapter.uploads, 2)
    assert page._autoapply_uploads.receipt
    original, reads = uploads.attachment_state, []
    async def read(*args):
        reads.append(1)
        return await original(*args)
    monkeypatch.setattr(uploads, 'attachment_state', read)
    assert await browser.uploads_ready(page, adapter.uploads, .3)
    assert len(reads) == 1
    if change == 'late_failure':
        class Request:
            url = 'https://fixture.test/upload'
            method = 'POST'
            failure = 'net::ERR_FAILED'
        request = Request()
        page._autoapply_uploads.started(request)
        page._autoapply_uploads.failed(request)
    else:
        await page.evaluate({
            'removed':"document.querySelector('input').remove()",
            'replacement':"document.querySelector('input').outerHTML=document.querySelector('input').outerHTML",
            'busy':"document.body.insertAdjacentHTML('beforeend','<div role=progressbar>Loading</div>')",
            'warning':"document.body.insertAdjacentHTML('beforeend','<div role=status>Uploading</div>')",
        }[change])
    assert not await browser.uploads_ready(page, adapter.uploads, .3)
    assert page._autoapply_uploads.receipt is None


async def test_combobox_deadline_covers_delayed_open_and_missing_options(browser):
    import time
    from autoapply.combobox import open_and_select_combobox, DropdownStateError
    page = await browser.new_page()
    await page.set_content('''<input id="f" role="combobox" aria-expanded="false"
      onclick="setTimeout(()=>this.setAttribute('aria-expanded','true'),150)">''')
    started = time.monotonic()
    with pytest.raises(DropdownStateError):
        await open_and_select_combobox(page.main_frame, {'id':'f','label':'Choice'}, 'Absent', timeout_ms=300)
    assert time.monotonic()-started < .8


async def test_generic_next_once_and_old_snapshot_retired(browser):
    page = await browser.new_page()
    await page.set_content('''<div id="step"><label>Name<input></label></div><button
      onclick="window.clicks=(window.clicks||0)+1;document.querySelector('#step').innerHTML='<label>Email<input type=email required></label>'">Next</button>''')
    adapter = GenericApplicationAdapter(page)
    await adapter.get_questions({'id':1})
    old = adapter._step_snapshot
    await adapter.advance()
    assert adapter._step_snapshot is None
    assert await page.evaluate('window.clicks') == 1
    assert (await adapter.get_questions({'id':1}))[0].label == 'Email'
    assert adapter._step_snapshot is not old
    assert await adapter.validate()


def test_resume_file_version_and_forced_final_read(tmp_path, monkeypatch):
    from autoapply.operations import FileSnapshot
    path = tmp_path/'resume.pdf'
    path.write_bytes(b'%PDF-A')
    first = FileSnapshot.read(path)
    assert FileSnapshot.read(path, first) is first
    assert FileSnapshot.read(path, first, force=True) == first
    path.write_bytes(b'%PDF-B')
    assert FileSnapshot.read(path, first).sha256 != first.sha256


async def test_new_page_setup_failure_releases_owned_page(config, monkeypatch):
    from autoapply.browser import Browser
    from unittest.mock import AsyncMock
    browser = Browser(config)
    browser.max_active_application_tabs = 1
    monkeypatch.setattr(browser, 'ensure_desktop', AsyncMock(side_effect=RuntimeError('setup')))
    try:
        with pytest.raises(RuntimeError, match='setup'):
            await browser.new_page()
        assert browser.context.pages == []
        assert not browser.leases
    finally:
        await browser.close()


async def test_browser_stop_survives_context_close_failure(config):
    from autoapply.browser import Browser
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    browser = Browser(config)
    stop = AsyncMock()
    browser.context = SimpleNamespace(pages=[], close=AsyncMock(side_effect=RuntimeError('close')))
    browser.playwright = SimpleNamespace(stop=stop)
    with pytest.raises(RuntimeError, match='close'):
        await browser.close()
    stop.assert_awaited_once()
    assert browser.context is browser.playwright is None


@pytest.mark.parametrize('failure', ['archive', 'cancellation'])
async def test_engine_cleanup_survives_diagnostics_and_cancellation(config, db, listing, monkeypatch, failure):
    import asyncio
    from unittest.mock import AsyncMock
    from autoapply.engine import Engine
    from autoapply.models import State
    import autoapply.engine as module
    db.ingest(listing, config)
    engine = Engine(config, db)
    async def navigate(page, url):
        if failure == 'cancellation':
            raise asyncio.CancelledError()
        return State.CLOSED, 'closed fixture'
    monkeypatch.setattr(engine.browser, 'navigate', navigate)
    monkeypatch.setattr(engine, 'security_gate', AsyncMock(return_value=False))
    if failure == 'archive':
        def failed(*args):
            raise OSError('archive fixture')
        monkeypatch.setattr(module, 'archive_application', failed)
    try:
        with pytest.raises(OSError if failure == 'archive' else asyncio.CancelledError):
            await engine.process_one(1)
        assert not engine.browser.leases
        assert all(p.url == 'about:blank' for p in engine.browser.context.pages)
    finally:
        await engine.close()


async def test_direct_fill_only_prestarted_context_enforces_owned_tab_limit(config, db, browser):
    from autoapply.engine import Engine
    db.set_setting('auto_submit', False)
    engine = Engine(config, db, browser, fill_only=True)
    await browser.enforce_application_limit(1)
    page = await browser.new_page()
    with pytest.raises(RuntimeError, match='BATCH_TAB_LIMIT_VIOLATION'):
        await browser.new_page()
    assert browser.max_tabs_observed == 1
    assert not page.is_closed()
    await browser.release_page(page, preserve=True)
    assert browser.leases[page].preserved
    await browser.release_page(page)
    assert page.is_closed()


async def test_browser_rejects_foreign_page_without_closing_it(config):
    from autoapply.browser import Browser
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    browser = Browser(config)
    foreign = SimpleNamespace(context=object(), close=AsyncMock())
    with pytest.raises(ValueError, match='outside'):
        await browser.release_page(foreign)
    foreign.close.assert_not_awaited()


async def test_pure_security_readers_share_one_observation(browser, monkeypatch):
    from autoapply.security import SecurityDetector, SubmissionClassifier
    from autoapply.browser import classify_page_condition
    calls = []
    original = SecurityDetector.snapshot
    async def snapshot(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)
    monkeypatch.setattr(SecurityDetector, 'snapshot', snapshot)
    page = await browser.new_page()
    await page.set_content('<label>Name<input></label>')
    classifier = SubmissionClassifier()
    result = await classifier.classify(page)
    assert not result.security.blocking
    assert classify_page_condition(result.security, result.snapshot) == (None, '')
    assert classifier.classify_evidence(result.inspection).confirmation is None
    classifier.ats(result.snapshot)
    assert len(calls) == 1
    await page.set_content('<h1>Verify you are human</h1>')
    assert (await classifier.classify(page)).security.blocking
    assert len(calls) == 2


async def test_shadow_inventory_root_labels_and_radio_grouping(browser):
    page = await browser.new_page()
    await page.set_content('<div id="a"></div><div id="b"></div>')
    await page.evaluate('''() => {
      for(const id of ['a','b'])document.getElementById(id).attachShadow({mode:'open'}).innerHTML=
        '<fieldset><legend>'+id+'</legend><label><input type=radio name=same required>Yes</label><label><input type=radio name=same>No</label></fieldset>'+
        '<label id=label>'+id+' name</label><input aria-labelledby=label><input type=range aria-label=Optional>';
    }''')
    adapter = SmartRecruitersAdapter(page)
    questions = await adapter.get_questions({'id':1})
    assert [q.label for q in questions if q.kind == 'radio'] == ['a', 'b']
    assert {q.label for q in questions if q.kind == 'text'} == {'a name', 'b name'}
    assert sum(i['support'] == 'UNSUPPORTED_OPTIONAL' for i in adapter.inventory) == 2
    await page.locator('#a').evaluate("e=>e.shadowRoot.querySelector('input[type=range]').required=true")
    from autoapply.smartrecruiters import CapabilityFailure
    with pytest.raises(CapabilityFailure, match='UNSUPPORTED_REQUIRED'):
        await adapter.get_questions({'id':1})


async def test_transient_upload_mutation_requires_new_stability(browser, config):
    page = await browser.new_page()
    await page.set_content('<label>Resume<input type=file></label>')
    adapter = GenericApplicationAdapter(page)
    q, = await adapter.get_questions({'id':1})
    await adapter.upload_documents(q, config.resume)
    assert await browser.uploads_ready(page, adapter.uploads, 2)
    await page.evaluate("() => {const e=document.createElement('div'); e.role='progressbar'; document.body.append(e); e.remove()}")
    assert not await browser.uploads_ready(page, adapter.uploads, .2)


async def test_next_ambiguity_is_one_click_with_bounded_observation(browser):
    from autoapply.applications import UnsupportedForm
    page = await browser.new_page()
    await page.set_content('<label>Name<input></label><button onclick="window.clicks=(window.clicks||0)+1">Next</button>')
    adapter = GenericApplicationAdapter(page)
    with pytest.raises(UnsupportedForm, match='STEP_TRANSITION_UNCONFIRMED'):
        await adapter.advance(timeout_seconds=2)
    assert await page.evaluate('window.clicks') == 1


async def test_search_only_combobox_opens_on_input(browser):
    from autoapply.combobox import open_and_select_combobox
    page = await browser.new_page()
    await page.set_content('''<input id=f role=combobox aria-controls=menu aria-expanded=false>
      <div id=menu role=listbox></div><script>
      const f=document.querySelector('input'),m=document.querySelector('#menu');
      f.oninput=()=>{f.setAttribute('aria-expanded','true');m.innerHTML='<div role=option>Choice</div>';
        m.firstChild.onclick=()=>{window.clicks=(window.clicks||0)+1;f.value='Choice';f.setAttribute('aria-expanded','false');m.innerHTML=''}};
      </script>''')
    assert (await open_and_select_combobox(page.main_frame, {'id':'f','label':'Choice'}, 'Choice'))['committed']
    assert await page.evaluate('window.clicks') == 1


async def test_popup_is_owned_closed_and_limit_stays_failed(config):
    from autoapply.browser import Browser
    browser = Browser(config)
    browser.max_active_application_tabs = 1
    try:
        page = await browser.new_page()
        # An ATS popup is created inside this dedicated context only.
        async with page.expect_popup() as pending:
            await page.evaluate("window.open('about:blank')")
        popup = await pending.value
        if not popup.is_closed():
            await popup.wait_for_event('close')
        assert not page.is_closed()
        assert browser.context.pages == [page]
        with pytest.raises(RuntimeError, match='BATCH_TAB_LIMIT_VIOLATION'):
            browser.check_tab_limit()
    finally:
        await browser.close()


async def test_engine_close_releases_browser_after_history_failure(config, db, monkeypatch):
    from autoapply.engine import Engine
    from unittest.mock import AsyncMock
    engine = Engine(config, db)
    engine.handoff.pages[1] = object()
    monkeypatch.setattr(db, 'update_security', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('history')))
    monkeypatch.setattr(engine.browser, 'close', AsyncMock())
    with pytest.raises(OSError, match='history'):
        await engine.close()
    engine.browser.close.assert_awaited_once()


async def test_presubmit_normalized_validation_runs_once_and_stays_fresh(browser, monkeypatch):
    from autoapply.security import SNAPSHOT, SubmissionClassifier, PreSubmitState
    from autoapply.form_validation import VALIDATION_JS
    from playwright.async_api import Page, Frame
    counts = []
    page = await browser.new_page()
    await page.set_content('<label>Email<input type=email required value="valid@example.test"></label>')
    adapter = GenericApplicationAdapter(page)
    for cls in (Page, Frame):
        original = cls.evaluate
        async def evaluate(self, script, arg=None, *, original=original):
            if script == VALIDATION_JS or script == SNAPSHOT and (not arg or arg.get('validation', True)):
                counts.append(1)
            return await original(self, script, arg)
        monkeypatch.setattr(cls, 'evaluate', evaluate)
    classifier = SubmissionClassifier()
    assert (await classifier.pre_submit(page, adapter))[0] == PreSubmitState.NO_SECURITY_BLOCK
    assert len(counts) == 1
    await page.locator('input').fill('invalid')
    assert (await classifier.pre_submit(page, adapter))[0] == PreSubmitState.VALIDATION_ERROR
    assert len(counts) == 2
