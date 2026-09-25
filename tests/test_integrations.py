import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from autoapply.ai import AIManager, BrowserAIProvider, ProviderUnavailable
from autoapply.discord_bot import DiscordBot
from autoapply.discord_bot import verify_delivery
from unittest.mock import AsyncMock, Mock
import asyncio
import discord
from autoapply.gmail import extract_verification
from autoapply.models import Question


def mail(identifier, body, sender="verify@employer.test", age_seconds=0):
    return {"id": identifier, "internalDate": str(int((datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).timestamp() * 1000)),
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Verification code"}],
                        "mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


def test_gmail_is_scoped_recent_and_unambiguous():
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    body = '<p>Your code is 123456</p><a href="https://apply.employer.test/verify?token=fixture">Verify</a>'
    messages = [mail("1", body)]
    result = extract_verification(messages, ["employer.test"], ["apply.employer.test"], since)
    assert result["code"] == "123456"
    assert result["link"].startswith("https://apply.employer.test")
    assert extract_verification(messages + [mail("2", body)], ["employer.test"], ["apply.employer.test"], since) is None
    assert extract_verification([mail("1", body, age_seconds=1000)], ["employer.test"], ["apply.employer.test"], since) is None
    assert extract_verification([mail("1", body, sender="x@attacker.test")], ["employer.test"], ["apply.employer.test"], since) is None


def test_gmail_rejects_multiple_codes():
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert extract_verification([mail("1", "code: 123456 and code: 999999")], ["employer.test"], [], since) is None


def test_discord_authorization(monkeypatch):
    monkeypatch.setenv("DISCORD_USER_ID", "123456789")
    bot = DiscordBot(None)
    assert bot.intents.dm_messages and bot.intents.guilds
    assert not bot.intents.message_content and not bot.intents.members and not bot.intents.presences
    assert not bot.intents.guild_messages
    class User:
        id, bot = 123456789, False
    user = User()
    assert bot.authorized(user)
    user.id = 987654321
    assert not bot.authorized(user)
    user.id, user.bot = 123456789, True
    assert not bot.authorized(user)


def test_paid_ai_disabled(db):
    with pytest.raises(ValueError):
        BrowserAIProvider({"name": "test", "billing": "paid"}, None, db)


async def test_ai_missing_facts_pauses(config, db):
    with pytest.raises(ProviderUnavailable, match="verified writing facts"):
        await AIManager(config, db, None).draft(Question("1", "Why this company?", "textarea"), {"company": "Example", "title": "Intern"})


async def test_discord_delivery_checks_configured_human_only():
    user = Mock(bot=False, send=AsyncMock())
    client = Mock(fetch_user=AsyncMock(return_value=user))
    await verify_delivery(client, 123)
    client.fetch_user.assert_awaited_once_with(123)
    user.send.assert_awaited_once()
    user.bot = True
    user.send.reset_mock()
    with pytest.raises(ValueError, match="personal Discord"):
        await verify_delivery(client, 123)
    user.send.assert_not_awaited()


async def test_discord_forbidden_has_actionable_error():
    error = discord.Forbidden(Mock(status=403, reason="Forbidden"), {"code": 50007, "message": "Cannot send messages to this user"})
    client = Mock(fetch_user=AsyncMock(return_value=Mock(bot=False, send=AsyncMock(side_effect=error))))
    with pytest.raises(RuntimeError, match="50007.*Install the bot"):
        await verify_delivery(client, 123)


async def test_discord_no_shared_server_explains_installation():
    error = discord.Forbidden(Mock(status=403, reason="Forbidden"), {"code": 50278, "message": "No mutual guilds"})
    client = Mock(fetch_user=AsyncMock(return_value=Mock(bot=False, send=AsyncMock(side_effect=error))))
    with pytest.raises(RuntimeError, match="no shared server.*50278"):
        await verify_delivery(client, 123)


async def test_worker_does_not_scan_before_discord_delivery(config, db, monkeypatch):
    from autoapply.engine import Engine
    config.data["discord"]["enabled"] = True
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture")
    monkeypatch.setenv("DISCORD_USER_ID", "123")
    bot = Mock(start=AsyncMock(), wait_for_delivery=AsyncMock(side_effect=RuntimeError("DM blocked")), close=AsyncMock())
    monkeypatch.setattr("autoapply.discord_bot.DiscordBot", lambda controller: bot)
    engine = Engine(config, db)
    engine.scan = AsyncMock()
    engine.process_one = AsyncMock()
    with pytest.raises(RuntimeError, match="DM blocked"):
        await engine.run()
    engine.scan.assert_not_awaited()
    engine.process_one.assert_not_awaited()
    bot.close.assert_awaited_once()


async def test_discord_startup_wait_propagates_connection_failure(config, db, monkeypatch):
    from autoapply.control import Controller
    monkeypatch.setenv("DISCORD_USER_ID", "123")
    bot = DiscordBot(Controller(config, db))
    async def fail():
        raise RuntimeError("Gateway refused intents")
    connection = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="Gateway refused intents"):
        await bot.wait_for_delivery(connection, timeout=1)
    await bot.close()
