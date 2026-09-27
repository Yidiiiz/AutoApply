import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from autoapply.engine import Engine
from autoapply.models import State, now
from autoapply.security import SecurityResult

@pytest.fixture
def held(config, db, listing):
    db.ingest(listing, config); db.claim()
    db.transition(1, State.MANUAL_REVIEW)
    db.update_security(1, manual_action_required=1, manual_resume_allowed=1, session_preserved=1, error_category='INPUT_REQUIRED')
    engine=Engine(config,db)
    page=Mock(); page.is_closed.return_value=False
    engine.handoff.pages[1]=page; engine.handoff.sessions[1]='session-A'
    db.set_setting('manual_session:1',dict(token='session-A',busy=False))
    engine.inspect_security=AsyncMock(return_value=SimpleNamespace(confirmation=None,security=SecurityResult(),snapshot={'text':''}))
    engine.browser.reset_observation=Mock()
    engine._process_one=AsyncMock()
    return engine,page

@pytest.mark.asyncio
async def test_inspection_only_and_duplicate_ack(held,db):
    engine,page=held
    engine.control.command('inspect-manual 1')
    request=dict(db.one('SELECT * FROM manual_requests'))
    engine.control.command('resume-manual 1')
    assert dict(db.one('SELECT * FROM manual_requests'))==request
    await engine.service_manual_requests()
    engine.inspect_security.assert_awaited_once_with(1,page)
    engine._process_one.assert_not_awaited()
    assert engine.handoff.pages[1] is page
    db.execute('INSERT INTO manual_requests VALUES (?,?,?)',tuple(request.values()))
    await engine.service_manual_requests()
    assert engine.inspect_security.await_count==1
    assert not db.setting('manual_session:1')['busy']

@pytest.mark.asyncio
@pytest.mark.parametrize('condition',['stale','missing','retired'])
async def test_invalid_session_never_accesses_employer(held,db,condition):
    engine,page=held
    engine.control.command('resume-manual 1')
    if condition=='stale': engine.handoff.sessions[1]='session-B'
    elif condition=='missing': engine.handoff.pages.clear()
    else: db.set_setting('duplicate_submission_guard:1',True)
    await engine.service_manual_requests()
    engine.inspect_security.assert_not_awaited()
    engine._process_one.assert_not_awaited()

@pytest.mark.asyncio
async def test_resume_inspects_before_same_page_continuation(held,db,monkeypatch):
    engine,page=held
    cursor=SimpleNamespace(exit_manual_mode=AsyncMock())
    monkeypatch.setattr('autoapply.engine.get_cursor',lambda p:cursor)
    async def continuation(app_id,preserved_page):
        engine.inspect_security.assert_awaited_once_with(1,page)
        assert preserved_page is page
    engine._process_one.side_effect=continuation
    engine.control.command('resume-manual 1')
    await engine.service_manual_requests()
    engine._process_one.assert_awaited_once_with(1,preserved_page=page)

@pytest.mark.asyncio
async def test_post_submit_never_repeats(held,db):
    engine,page=held
    db.execute('UPDATE applications SET submit_intent_at=? WHERE id=1',(now(),))
    engine.handoff.request=AsyncMock()
    engine.control.command('resume-manual 1')
    await engine.service_manual_requests()
    engine._process_one.assert_not_awaited()
    engine.handoff.request.assert_awaited_once()


async def test_explicit_untouched_upload_protocol_refresh(held,db):
    from autoapply.uploads import UploadTracker
    engine,page=held
    original=page._autoapply_uploads=UploadTracker()
    db.set_setting('refresh_upload_protocol:1',True)
    from autoapply.maintenance import refresh_untouched_upload_protocol
    refresh_untouched_upload_protocol(engine, 1)
    assert page._autoapply_uploads is original
    assert not db.setting('refresh_upload_protocol:1')
    engine._process_one.assert_not_awaited()


async def test_upload_protocol_refresh_cannot_discard_selection(held,db):
    from autoapply.uploads import UploadTracker
    engine,page=held
    page._autoapply_uploads=UploadTracker()
    page._autoapply_uploads.select()
    db.set_setting('refresh_upload_protocol:1',True)
    with pytest.raises(ValueError,match='untouched'):
        from autoapply.maintenance import refresh_untouched_upload_protocol
        refresh_untouched_upload_protocol(engine, 1)
    engine.inspect_security.assert_not_awaited()


async def test_controlled_registration_can_only_inspect(held,db):
    engine,page=held
    db.set_setting('inspect_only_once:1',True)
    await engine.resume_manual(1,strict_session=True)
    engine.inspect_security.assert_awaited_once_with(1,page)
    engine._process_one.assert_not_awaited()
    assert not db.setting('inspect_only_once:1')
