"""A saved file answer cannot substitute for fresh upload acceptance."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from autoapply.engine import Engine
from autoapply.control import Controller
from autoapply.models import Question
from autoapply.operations import FileSnapshot
from autoapply.uploads import UploadTracker
from autoapply.applications import GenericApplicationAdapter


@pytest.mark.parametrize('label,kind,allowed', [
    ('Resume', 'file', True), ('Curriculum Vitae', 'file', True),
    ('Cover Letter', 'file', False), ('Resume and transcript', 'file', False),
    ('Resume', 'text', False),
])
async def test_reconstruction_prepares_only_canonical_resume(config, db, listing, monkeypatch, label, kind, allowed):
    db.ingest(listing, config)
    db.claim(1)
    db.lifecycle.record_hold(1, 'Historical unanswered field', 'INPUT_REQUIRED', manual_resume_allowed=1)
    row = db.question(1, Question('historical-document', label, kind, True))
    db.set_setting('auto_submit', False)
    controller = Controller(config, db)
    controller.command('resume 1')
    engine = Engine(config, db, fill_only=True)
    engine.browser.enforce_application_limit = AsyncMock()
    engine._process_one = AsyncMock(return_value=True)
    await engine.service_manual_requests()
    assert engine._process_one.await_count == int(allowed)
    saved = db.one('SELECT * FROM questions WHERE id=?', (row['id'],))
    assert saved['status'] == 'PENDING' and saved['answer'] is None
    assert not db.application(1)['submit_intent_at']
    assert not db.setting('auto_submit')


@pytest.mark.parametrize('accepted', [False, True])
async def test_pending_document_is_resolved_only_after_current_acceptance(config, db, listing, accepted):
    db.ingest(listing, config)
    row = db.question(1, Question('resume-control', 'Resume', 'file', True))
    other = db.question(1, Question('other-resume', 'Resume', 'file', True))
    tracker = UploadTracker()
    tracker.result = dict(ready=accepted, category='UPLOAD_READY' if accepted else 'UPLOAD_FAILED')
    snapshot = FileSnapshot.read(config.resume)
    page = SimpleNamespace(_autoapply_uploads=tracker,
        _autoapply_document_snapshots={'resume-control': snapshot})
    engine = Engine(config, db, fill_only=True)
    observation = {}
    engine.browser.uploads_ready = AsyncMock(return_value=accepted)
    engine.browser.observation = lambda p: observation
    assert await engine.check_uploads(1, page, {'resume-control': object()}) == accepted
    saved = db.one('SELECT * FROM questions WHERE id=?', (row['id'],))
    assert saved['status'] == ('ANSWERED' if accepted else 'PENDING')
    assert (json.loads(saved['answer']) if saved['answer'] else None) == ('resume.pdf' if accepted else None)
    assert db.one('SELECT status FROM questions WHERE id=?', (other['id'],))['status'] == 'PENDING'


@pytest.mark.parametrize('boundary', ['missing_pdf', 'unknown_fact', 'intent', 'uncertain_step', 'exhausted'])
async def test_resume_preparation_preserves_recovery_boundaries(config, db, listing, boundary):
    db.ingest(listing, config)
    db.claim(1)
    db.lifecycle.record_hold(1, 'Input needed', 'INPUT_REQUIRED', manual_resume_allowed=1)
    db.question(1, Question('resume', 'Resume', 'file', True))
    if boundary == 'missing_pdf':
        config.resume.unlink()
    elif boundary == 'unknown_fact':
        db.question(1, Question('duration', 'Years of specific experience', 'number', True))
    elif boundary == 'intent':
        db.execute("UPDATE applications SET submit_intent_at='uncertain' WHERE id=1")
    elif boundary == 'uncertain_step':
        db.execute("UPDATE applications SET delivery_state='POSSIBLY_DELIVERED' WHERE id=1")
    else:
        db.execute('UPDATE applications SET attempts=4,max_retries=3 WHERE id=1')
    db.set_setting('auto_submit', False)
    db.set_setting('paused', False)
    engine = Engine(config, db, fill_only=True)
    engine.browser.enforce_application_limit = AsyncMock()
    engine._process_one = AsyncMock()
    try:
        await engine.resume_manual(1)
    except ValueError:
        pass
    engine._process_one.assert_not_awaited()
    assert db.one("SELECT status FROM questions WHERE field_key='resume'")['status'] == 'PENDING'


@pytest.mark.parametrize('accepted', [False, True])
async def test_real_browser_reconstruction_reuploads_before_resolving(config, db, listing, monkeypatch, accepted):
    body = '''<h1>Software Engineering Intern Summer 2027</h1>
    <p>Software engineering internship for undergraduate students in New York, NY.</p>
    <form><label>First Name<input name="first_name" required></label>
    <label>Resume<input name="resume" type="file" required></label>
    <button type="submit">Submit application</button></form>
    <script>window.finalClicks=0;document.querySelector('button').onclick=()=>window.finalClicks++;
    document.querySelector('form').onsubmit=e=>{e.preventDefault();fetch('/submit',{method:'POST'})}</script>'''
    db.ingest(replace(listing, url='http://127.0.0.1:8123/resume'), config)
    db.set_setting('auto_submit', False)
    engine = Engine(config, db, fill_only=True, preserve_fill_session=True)
    requests = []
    try:
        await engine.browser.start()
        async def route(r):
            if r.request.method == 'POST':
                requests.append(r.request.url)
            await r.fulfill(content_type='text/html', body=body)
        await engine.browser.context.route('**/*', route)
        inspection = await engine.browser.new_page()
        await engine.browser.navigate(inspection, 'http://127.0.0.1:8123/resume')
        qs = await GenericApplicationAdapter(inspection).get_questions(db.application(1))
        resume = next(q for q in qs if q.kind == 'file')
        historical = db.question(1, resume)
        await engine.browser.release_page(inspection)
        db.claim(1)
        db.lifecycle.record_hold(1, 'Historical file row still pending', 'INPUT_REQUIRED', manual_resume_allowed=1)
        db.set_setting('paused', False)
        original = engine.browser.uploads_ready
        if not accepted:
            monkeypatch.setattr(engine.browser, 'uploads_ready', AsyncMock(return_value=False))
        assert await engine.resume_manual(1)
        page = engine.retained_pages.get(1) or engine.handoff.pages[1]
        assert await page.locator('input[name=first_name]').input_value() == 'Test'
        assert await page.locator('input[type=file]').evaluate('(e)=>e.files[0].name') == 'resume.pdf'
        row = db.one('SELECT * FROM questions WHERE id=?', (historical['id'],))
        assert row['status'] == ('ANSWERED' if accepted else 'PENDING')
        assert db.application(1)['application_state'] == ('READY_FOR_MANUAL_SUBMIT' if accepted else 'MANUAL_REQUIRED')
        assert not requests and await page.evaluate('window.finalClicks') == 0
        assert all(v == 0 for v in engine.fill_invariant.snapshot().values())
    finally:
        await engine.close()
