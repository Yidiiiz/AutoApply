import asyncio
import json
import os
import logging
import time

import discord

log = logging.getLogger("autoapply.discord")


async def verify_delivery(client, owner_id):
    """Send only to the configured human recipient; surface actionable setup errors."""
    try:
        user = await client.fetch_user(owner_id)
        if user.bot:
            raise ValueError("DISCORD_USER_ID must identify your personal Discord account, not a bot")
        await user.send(
            "AutoApply connection check: private notifications are working. "
            "When the worker is running, use !status, !pending, !pause or !resume here.",
            allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden as exc:
        if exc.code == 50278:
            raise RuntimeError(
                "Discord cannot deliver DMs because the bot and your account have no shared server "
                "(code 50278). Install the bot in a server you belong to, then run discord-check again.") from None
        raise RuntimeError(
            f"Discord refused private-message delivery (code {exc.code}). "
            "Install the bot in a server you belong to, allow DMs from that server, "
            "and check that the bot is not blocked. Then run discord-check again.") from None
    except discord.HTTPException as exc:
        raise RuntimeError(f"Discord delivery check failed (HTTP {exc.status}, code {exc.code}); retry after fixing the connection") from None


async def check_connection():
    """Operator-invoked REST delivery check without starting a worker or draining its outbox."""
    owner = os.getenv("DISCORD_USER_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or not owner.isdigit():
        raise ValueError("Set DISCORD_BOT_TOKEN and a numeric DISCORD_USER_ID in ignored .env")
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        await verify_delivery(client, int(owner))
    except discord.LoginFailure:
        raise RuntimeError("Discord token was rejected; update DISCORD_BOT_TOKEN in ignored .env") from None
    finally:
        await client.close()


def chunks(text, limit=1850):
    return [text[i:i + limit] for i in range(0, len(text), limit)] or ["No results."]


class AnswerModal(discord.ui.Modal):
    def __init__(self, bot, question_id):
        super().__init__(title=f"Answer question #{question_id}")
        self.bot, self.question_id = bot, question_id
        self.answer = discord.ui.TextInput(label="Your answer (exact option or text)", style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.answer)

    async def on_submit(self, interaction):
        if not self.bot.authorized(interaction.user):
            await interaction.response.send_message("Unauthorized", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = self.bot.controller.route(f"answer {self.question_id} {self.answer.value}")
        except (ValueError, TypeError) as exc:
            result = str(exc)
        await interaction.followup.send(result, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class QuestionView(discord.ui.View):
    def __init__(self, bot, question):
        super().__init__(timeout=None)
        self.bot, self.question = bot, question
        for action, label in [("answer", "Answer / Change"), ("accept", "Accept proposal"), ("skip", "Skip"), ("ai", "Let AI Answer"), ("ask", "Ask / Clarify")]:
            button = discord.ui.Button(label=label, custom_id=f"aa:{question['id']}:{action}", style=discord.ButtonStyle.secondary)
            async def callback(interaction, selected=action):
                await self.act(interaction, selected)
            button.callback = callback
            self.add_item(button)
        options = json.loads(question["options"])
        if options and len(options) <= 25 and all(0 < len(o) <= 100 for o in options):
            select = discord.ui.Select(placeholder="Choose an answer", custom_id=f"aa:{question['id']}:select",
                                       options=[discord.SelectOption(label=o, value=str(i)) for i, o in enumerate(options)],
                                       max_values=len(options) if question["field_type"] == "multiselect" else 1)
            async def select_callback(interaction):
                if not bot.authorized(interaction.user):
                    await interaction.response.send_message("Unauthorized", ephemeral=True)
                    return
                try:
                    selected = [options[int(i)] for i in select.values]
                    value = selected if question["field_type"] == "multiselect" else selected[0]
                    result = bot.controller.answer(question["id"], value)
                except ValueError as exc:
                    result = str(exc)
                await interaction.response.send_message(result, ephemeral=True)
            select.callback = select_callback
            self.add_item(select)

    async def act(self, interaction, action):
        if not self.bot.authorized(interaction.user):
            await interaction.response.send_message("Unauthorized", ephemeral=True)
            return
        identifier = self.question["id"]
        if action == "answer":
            await interaction.response.send_modal(AnswerModal(self.bot, identifier))
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if action == "ai":
                result = "Proposed answer — review, then Accept proposal or Change:\n" + await self.bot.controller.draft(identifier)
            elif action == "ask":
                result = self.bot.controller.explain(self.question["application_id"], "missing") + "\nUse !application ID description to see the listing or !application ID answers to inspect verified answers."
            else:
                result = self.bot.controller.route(f"{action} {identifier}")
        except Exception as exc:
            result = str(exc) if isinstance(exc, ValueError) or exc.__class__.__name__ == "ProviderUnavailable" else "Operation failed; inspect the application audit history."
        for chunk in chunks(result):
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class DiscordBot(discord.Client):
    def __init__(self, controller):
        # Discord includes message content for DMs without the privileged intent.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.dm_messages = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.controller = controller
        self.owner_id = int(os.environ["DISCORD_USER_ID"])
        self.outbox_task = None
        self.delivery_ready = asyncio.Event()

    async def on_ready(self):
        if not self.delivery_ready.is_set():
            try:
                user = await self.fetch_user(self.owner_id)
                if user.bot:
                    raise ValueError("Configured recipient must be a human")
            except (RuntimeError, ValueError) as exc:
                self.controller.db.set_setting("discord_delivery_error", str(exc))
                log.error("%s", exc)
                await self.close()
                return
            self.controller.db.set_setting("discord_delivery_error", None)
            self.delivery_ready.set()

    async def wait_for_delivery(self, connection_task, timeout=45):
        ready_task = asyncio.create_task(self.delivery_ready.wait())
        try:
            await asyncio.wait({ready_task, connection_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if connection_task.done():
                await connection_task
                raise RuntimeError(self.controller.db.setting("discord_delivery_error") or "Discord disconnected before startup validation")
            if not self.delivery_ready.is_set():
                raise RuntimeError("Discord did not become ready within 45 seconds; check connection and enabled bot intents")
        finally:
            ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)

    def authorized(self, user):
        return user.id == self.owner_id and not user.bot

    async def setup_hook(self):
        for q in self.controller.pending():
            self.add_view(QuestionView(self, q))
        self.outbox_task = asyncio.create_task(self.deliver_outbox())

    async def close(self):
        if self.outbox_task:
            self.outbox_task.cancel()
            await asyncio.gather(self.outbox_task, return_exceptions=True)
        await super().close()

    async def on_message(self, message):
        if not self.authorized(message.author) or message.guild is not None:
            return
        if not message.content.startswith(("!", "/")):
            return
        started = time.monotonic()
        await message.channel.send("Received.")
        command = message.content.lstrip("!/")
        self.controller.db.event(None, "discord_command_ack", json.dumps({"command":command.split()[0] if command.split() else "", "latency_ms":round((time.monotonic()-started)*1000)}))
        try:
            if command.startswith("ai "):
                result = "Proposed answer (confirm with !accept QUESTION_ID):\n" + await self.controller.draft(int(command.split()[1]))
            else:
                result = self.controller.route(command)
        except Exception as exc:
            result = str(exc) if isinstance(exc, (ValueError, KeyError)) or exc.__class__.__name__ == "ProviderUnavailable" else "Command failed; inspect local application history."
        for chunk in chunks(result):
            await message.channel.send(chunk)

    async def deliver_outbox(self):
        await self.delivery_ready.wait()
        while not self.is_closed():
            try:
                user = self.get_user(self.owner_id) or await self.fetch_user(self.owner_id)
                for row in self.controller.db.rows("SELECT * FROM notifications WHERE delivered_at IS NULL ORDER BY id LIMIT 10"):
                    payload = json.loads(row["payload"])
                    view = None
                    if "question_id" in payload:
                        q = next((q for q in self.controller.pending() if q["id"] == payload["question_id"]), None)
                        if not q:
                            self.controller.db.execute("UPDATE notifications SET delivered_at=datetime('now') WHERE id=?", (row["id"],))
                            continue
                        text = f"{q['company']} — {q['title']}\n{q['canonical_url']}\nApplication #{q['application_id']}, question #{q['id']}\n{q['raw_question']}\nType: {q['field_type']} | Required: {bool(q['required'])}\nOptions: {q['options']}\nReason: {q['reason']}"
                        proposal = self.controller.db.setting(f"draft:{q['id']}")
                        if proposal:
                            text += "\nProposed answer:\n" + str(proposal)
                        view = QuestionView(self, q)
                    else:
                        text = payload.get("message", "Application requires attention")
                        if payload.get("kind") == "input":
                            pending = self.controller.db.rows("SELECT id FROM questions WHERE application_id=? AND status='PENDING'", (payload['application_id'],))
                            if not pending:
                                self.controller.db.execute("UPDATE notifications SET delivered_at=datetime('now') WHERE id=?", (row['id'],))
                                continue
                    pieces = chunks(text)
                    for piece in pieces[:-1]:
                        await user.send(piece)
                    await user.send(pieces[-1], view=view)
                    self.controller.db.execute("UPDATE notifications SET delivered_at=datetime('now') WHERE id=?", (row["id"],))
                    await asyncio.sleep(1)
            except discord.Forbidden as exc:
                self.controller.db.set_setting("discord_delivery_error", f"Private-message delivery refused (code {exc.code}); restore DMs, then resume")
                self.controller.db.set_setting("paused", True)
                log.error("Discord delivery refused; processing paused and notifications retained")
                await asyncio.sleep(55)
            except (discord.HTTPException, OSError):
                # The durable outbox remains pending across disconnects and restarts.
                pass
            await asyncio.sleep(5)
