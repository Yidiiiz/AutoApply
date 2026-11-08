"""Offline regressions for batch attempts and exclusive human review."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, call

import pytest

from autoapply.semi_auto import SemiAutoLoop


def make_loop(config, db, listing, count=3):
    for i in range(1, count + 1):
        db.ingest(replace(listing, company=f"Company {i}",
                          url=f"https://example.test/application/{i}"), config)
    db.set_setting("auto_submit", False)
    db.set_setting("paused", False)
    return SemiAutoLoop(config, db)


@pytest.mark.parametrize("interruption", [
    "paused_before", "stopped_before", "paused_during", "stopped_during",
    "BROWSER_START_FAILED", "EXTERNAL_EXECUTION_APPROVAL_REQUIRED",
    "EXECUTION_APPROVAL_BLOCKED",
])
async def test_unattempted_ids_remain_eligible_after_interruption(
        config, db, listing, monkeypatch, interruption):
    loop = make_loop(config, db, listing)
    discover = Mock(side_effect=lambda db, config, seen: [
        i for i in (1, 2, 3) if i not in seen
    ])
    monkeypatch.setattr("autoapply.semi_auto.discover", discover)
    monkeypatch.setattr("autoapply.semi_auto.verify_safety", Mock())

    async def process_one(app_id):
        if interruption == "paused_during":
            db.set_setting("paused", True)
        elif interruption == "stopped_during":
            loop.engine.stop_event.set()
        elif interruption.isupper():
            db.update_security(app_id, error_category=interruption)

    loop.engine.process_one = AsyncMock(side_effect=process_one)
    if interruption == "paused_before":
        db.set_setting("paused", True)
    elif interruption == "stopped_before":
        loop.engine.stop_event.set()

    await loop.fill_batch()

    attempted = [] if interruption.endswith("_before") else [1]
    remaining = [i for i in (1, 2, 3) if i not in attempted]
    assert loop.engine.process_one.await_args_list == [call(i) for i in attempted]
    assert loop.seen == set(attempted)
    assert loop.ids == attempted
    saved = json.loads((config.private / "semi-auto-result.json").read_text())
    assert saved["application_ids"] == attempted
    assert [app["application_id"] for app in saved["applications"]] == attempted
    notification = json.loads(db.one(
        "SELECT payload FROM notifications WHERE dedupe_key=?",
        ("semi-batch:" + loop.batch_id,),
    )["payload"])
    assert notification["application_ids"] == attempted
    for app_id in remaining:
        assert f"#{app_id} " not in loop.summary()
        assert f"#{app_id} " not in notification["message"]

    db.set_setting("paused", False)
    loop.engine.stop_event.clear()
    loop.engine.process_one = AsyncMock()
    await loop.fill_batch()

    assert loop.engine.process_one.await_args_list == [call(i) for i in remaining]
    assert loop.ids == remaining
    assert loop.seen == {1, 2, 3}
    assert discover.call_count == 2


async def test_batch_records_ids_only_after_process_one_is_awaited(
        config, db, listing, monkeypatch):
    loop = make_loop(config, db, listing)
    monkeypatch.setattr("autoapply.semi_auto.discover", lambda *args: [1, 2, 3])
    monkeypatch.setattr("autoapply.semi_auto.verify_safety", Mock())

    async def process_one(app_id):
        assert loop.ids == list(range(1, app_id))
        assert loop.seen == set(range(1, app_id))

    loop.engine.process_one = AsyncMock(side_effect=process_one)
    await loop.fill_batch()

    assert loop.engine.process_one.await_args_list == [call(1), call(2), call(3)]
    assert loop.ids == [1, 2, 3]
    assert loop.seen == {1, 2, 3}


async def test_review_requires_lock_before_arming_another_application(
        config, db, listing):
    loop = make_loop(config, db, listing, count=2)
    loop.ids = [1, 2]
    loop.phase = "REVIEW"
    first = Mock(is_closed=Mock(return_value=False), bring_to_front=AsyncMock())
    second = Mock(is_closed=Mock(return_value=False), bring_to_front=AsyncMock())
    loop.engine.retained_pages.update({1: first, 2: second})

    await loop.command("review 1")
    await loop.command("review 1")
    assert loop.guard.review_pages == {first: 1}
    assert first.bring_to_front.await_count == 2
    with pytest.raises(ValueError):
        await loop.command("review 2")
    assert loop.guard.review_pages == {first: 1}
    second.bring_to_front.assert_not_awaited()

    await loop.command("lock")
    assert not loop.guard.review_pages
    await loop.command("review 2")
    assert loop.guard.review_pages == {second: 2}
    second.bring_to_front.assert_awaited_once()


async def test_next_is_rejected_while_paused(config, db, listing):
    loop = make_loop(config, db, listing)
    loop.phase = "REVIEW"
    db.set_setting("paused", True)

    with pytest.raises(ValueError):
        await loop.command("next")
    assert loop.next_requested is False


@pytest.mark.parametrize('limit', [1, 3, 5, 10])
async def test_configured_limit_retains_at_most_requested_forms(config, db, listing, monkeypatch, limit):
    from types import SimpleNamespace
    loop = make_loop(config, db, listing, count=12)
    loop = SemiAutoLoop(config, db, engine=loop.engine, limit=limit)
    monkeypatch.setattr('autoapply.semi_auto.verify_safety', Mock())
    # All pages are mocks. Neither a browser nor the real process_one can run.
    loop.engine.browser.context = SimpleNamespace(pages=[])
    async def process_one(app_id):
        page = Mock(is_closed=Mock(return_value=False))
        loop.engine.retained_pages[app_id] = page
        loop.engine.browser.context.pages.append(page)
    loop.engine.process_one = AsyncMock(side_effect=process_one)
    await loop.fill_batch()
    assert loop.batch_size == loop.engine.fill_tab_limit == limit
    assert loop.engine.browser.max_active_application_tabs == limit
    assert loop.engine.process_one.await_count == limit
    assert len(loop.ids) == len(loop.live_pages()) == limit
    assert loop.engine.browser.max_tabs_observed == limit
    assert all(db.application(i)['attempts'] == 0 for i in range(limit + 1, 13))


@pytest.mark.parametrize('limit', [0, 11, -1, 1.5, True, '5'])
def test_loop_rejects_invalid_limit(config, db, limit):
    with pytest.raises(ValueError, match='integer between 1 and 10'):
        SemiAutoLoop(config, db, limit=limit)


@pytest.mark.parametrize('value', ['0', '11', '-1', '2.5', 'invalid'])
def test_semi_auto_cli_rejects_invalid_limit(value, capsys):
    from autoapply.__main__ import parser
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(['semi-auto', '--limit', value])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert '--limit must be' in error and 'between 1 and 10' in error


async def test_semi_auto_cli_dispatches_limit(config, db, monkeypatch):
    from autoapply.__main__ import execute, parser
    loop = Mock(run=AsyncMock())
    factory = Mock(return_value=loop)
    monkeypatch.setattr('autoapply.semi_auto.SemiAutoLoop', factory)
    args = parser().parse_args(['semi-auto', '--limit', '10'])
    assert await execute(args, config, db) == 0
    factory.assert_called_once_with(config, db, rerun=[], rerun_source='', limit=10)
    loop.run.assert_awaited_once()
    assert parser().parse_args(['semi-auto']).limit == 5
    assert SemiAutoLoop(config, db).batch_size == 5


@pytest.mark.parametrize('when', ['before', 'probe', 'during'])
async def test_persisted_stop_is_honored_between_applications(config, db, listing, monkeypatch, when):
    loop = make_loop(config, db, listing)
    monkeypatch.setattr('autoapply.semi_auto.verify_safety', Mock())
    completed = []
    async def process_one(app_id):
        db.set_setting('stop_requested', True)
        # Stop cannot set the engine event or tear down a page during this action.
        assert not loop.engine.stop_event.is_set()
        completed.append(app_id)
    loop.engine.process_one = AsyncMock(side_effect=process_one)
    if when == 'before':
        db.set_setting('stop_requested', True)
    elif when == 'probe':
        async def probe(app_id):
            db.set_setting('stop_requested', True)
            return True
        loop.listing_open = AsyncMock(side_effect=probe)
    await loop.fill_batch()
    expected = [1] if when == 'during' else []
    assert completed == loop.ids == expected
    assert loop.engine.stop_event.is_set()
    assert loop.seen == set(expected)


async def test_idle_run_polls_stop_and_clears_it_after_cleanup(config, db, monkeypatch):
    import asyncio
    config.data['browser']['headless'] = False
    config.data['discord']['enabled'] = True
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'offline-fixture')
    loop = SemiAutoLoop(config, db)
    loop.fill_batch = AsyncMock()
    cleanup = []
    async def start(token):
        await asyncio.Event().wait()
    async def engine_close():
        assert db.setting('stop_requested') is True
        cleanup.append('engine')
    async def bot_close():
        cleanup.append('bot')
    async def safe_sleep(delay):
        assert delay == .5
        db.set_setting('stop_requested', True)
    bot = Mock(start=AsyncMock(side_effect=start), wait_for_delivery=AsyncMock(),
               close=AsyncMock(side_effect=bot_close))
    monkeypatch.setattr('autoapply.discord_bot.DiscordBot', lambda _: bot)
    monkeypatch.setattr('autoapply.semi_auto.asyncio.sleep', safe_sleep)
    loop.engine.close = AsyncMock(side_effect=engine_close)
    await loop.run()
    assert cleanup == ['engine', 'bot']
    assert loop.engine.stop_event.is_set()
    assert db.setting('stop_requested') is False and db.setting('paused') is True
    assert loop.phase == 'STOPPED'
    assert json.loads((config.private / 'semi-auto-result.json').read_text())['phase'] == 'STOPPED'


async def test_pause_during_startup_is_not_cleared_and_stop_skips_first_form(
        config, db, listing, monkeypatch):
    import asyncio
    loop = make_loop(config, db, listing)
    config.data['browser']['headless'] = False
    config.data['discord']['enabled'] = True
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'offline-fixture')
    monkeypatch.setattr('autoapply.semi_auto.verify_safety', Mock())
    async def start(token):
        await asyncio.Event().wait()
    async def ready(task):
        db.set_setting('paused', True)
        db.set_setting('stop_requested', True)
    bot = Mock(start=AsyncMock(side_effect=start), wait_for_delivery=AsyncMock(side_effect=ready), close=AsyncMock())
    monkeypatch.setattr('autoapply.discord_bot.DiscordBot', lambda _: bot)
    loop.engine.process_one = AsyncMock()
    loop.engine.close = AsyncMock()
    original_fill = loop.fill_batch
    async def paused_fill():
        assert db.setting('paused') is True
        await original_fill()
    loop.fill_batch = AsyncMock(side_effect=paused_fill)
    await loop.run()
    loop.engine.process_one.assert_not_awaited()
    assert db.setting('paused') is True and db.setting('stop_requested') is False


async def test_persisted_stop_blocks_refill_at_safe_point(config, db, listing):
    loop = make_loop(config, db, listing)
    loop.ids = [1]
    loop.phase = 'REVIEW'
    loop.ready_to_refill.add(1)
    db.set_setting('stop_requested', True)
    loop.engine.service_manual_requests = AsyncMock()
    await loop.refill()
    loop.engine.service_manual_requests.assert_not_awaited()
    assert loop.ready_to_refill == {1}


async def test_stop_request_clears_on_failed_startup(config, db):
    loop = SemiAutoLoop(config, db)
    db.set_setting('stop_requested', True)
    with pytest.raises(ValueError, match='visible browser and Discord'):
        await loop.run()
    assert db.setting('stop_requested') is False
