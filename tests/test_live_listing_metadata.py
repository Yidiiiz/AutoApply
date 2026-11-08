from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.applications import GenericApplicationAdapter
from autoapply.engine import Engine
from autoapply.patience import PatiencePolicy


async def test_jobvite_job_heading_and_description_exclude_site_chrome():
    html = '''<h1 class="jv-logo">Example Careers</h1><h2 class="jv-header">Software Development Intern, Summer 2027</h2>
    <div class="jv-job-detail-description"><h2>Qualifications</h2><p>Knowledge of APIs</p></div>
    <div class="jv-job-detail-bottom-actions">Apply Later</div><h3>Similar Jobs</h3><p>Senior Engineer</p>'''
    result = await GenericApplicationAdapter(SimpleNamespace(content=AsyncMock(return_value=html))).inspect()
    assert result['title'] == 'Software Development Intern, Summer 2027'
    assert result['description'] == 'Qualifications\nKnowledge of APIs'


async def test_blank_metadata_is_reobserved_without_interacting():
    page = SimpleNamespace(content=AsyncMock(side_effect=['<main></main>', '<main><h1>Software Intern</h1><p>Internship description</p></main>']))
    result = await GenericApplicationAdapter(page).inspect()
    assert result['title'] == 'Software Intern'
    assert 'Internship description' in result['description']
    assert page.content.await_count == 2


@pytest.mark.parametrize('hydrate', [False, True], ids=['never-ready', 'delayed-description'])
async def test_engine_waits_for_listing_description_or_records_engineering_hold(config, db, listing, monkeypatch, hydrate):
    monkeypatch.setattr('autoapply.applications.page_policy', lambda page: PatiencePolicy(timeout=8, extension=0, poll_seconds=.02))
    db.set_setting('auto_submit', False)
    app_id, _ = db.ingest(replace(listing, url='http://127.0.0.1:8123/metadata'), config)
    engine = Engine(config, db, fill_only=True)
    content = '<h1>Software Engineering Intern Summer 2027</h1><p>Software engineering internship for undergraduate students.</p><form><label>First Name<input name="first_name" required></label><label>Resume<input type="file" name="resume" required></label><button type="submit">Submit application</button></form>'
    html = '<main></main>'
    if hydrate:
        import json
        html += '<script>setTimeout(()=>document.querySelector("main").innerHTML=' + json.dumps(content) + ',200)</script>'
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        await engine.process_one(app_id)
        app = db.application(app_id)
        if hydrate:
            assert app['application_state'] == 'READY_FOR_MANUAL_SUBMIT', app['failure_reason']
            assert 'undergraduate students' in app['description']
        else:
            assert app['error_category'] == 'JOB_DESCRIPTION_UNAVAILABLE', app['failure_reason']
            assert app['status'] == 'MANUAL_REVIEW' and not app['retry_allowed']
            assert not db.rows('SELECT * FROM questions WHERE application_id=?', (app_id,))
        assert app['submit_intent_at'] is None
        engine.fill_invariant.check()
    finally:
        await engine.close()
