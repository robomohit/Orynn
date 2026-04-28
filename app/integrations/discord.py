"""Discord bot integration.

Reads DISCORD_BOT_TOKEN from the environment. If absent, logs a notice and
exits silently so the server starts cleanly without any tokens configured.

When the token is present, starts a discord.py client. Incoming messages
mentioning the bot (or DMs) are forwarded to AgentService as tasks and
streaming responses are sent back to the same channel.

Get a token: https://discord.com/developers/applications
  → New Application → Bot → Reset Token
  Required intents: Message Content Intent (enabled in Bot settings)
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentService

_log = logging.getLogger(__name__)

_INTEGRATION_NAME = "Discord"


async def start_discord(agent_service: "AgentService") -> None:
    """Entry point called from FastAPI lifespan. Returns immediately if token absent."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        _log.info("%s integration disabled (no DISCORD_BOT_TOKEN in env)", _INTEGRATION_NAME)
        return

    try:
        import discord
    except ImportError:
        _log.warning(
            "%s integration disabled — discord.py not installed. "
            "Run: pip install discord.py",
            _INTEGRATION_NAME,
        )
        return

    _log.info("Starting %s integration…", _INTEGRATION_NAME)

    intents = discord.Intents.default()
    intents.message_content = True  # Required to read message body
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        _log.info("Discord bot connected as %s (id: %s)", client.user, client.user.id if client.user else "?")

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        # Respond to DMs or @mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = client.user is not None and client.user.mentioned_in(message)
        if not is_dm and not is_mention:
            return

        # Strip the bot mention from the goal text
        goal = message.content
        if client.user:
            goal = goal.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        if not goal:
            await message.channel.send("Please provide a task for me to work on.")
            return

        _log.debug("Discord message from %s: %s", message.author, goal[:80])
        await message.channel.send("Working on it…")

        task_id = f"dc_{message.author.id}_{int(asyncio.get_event_loop().time() * 1000)}"
        try:
            agent_service.init_task(task_id, goal)
        except Exception as exc:
            await message.channel.send(f"Failed to start task: {exc}")
            return

        # Screenshot delivery: cap to MAX_SCREENSHOTS per task so a chatty
        # computer-mode task doesn't flood the channel.
        MAX_SCREENSHOTS = 5
        screenshots_sent = 0
        last_intent: str = ""
        try:
            async for event_type, data in _stream_task(agent_service, task_id):
                if event_type == "screenshot" and screenshots_sent < MAX_SCREENSHOTS:
                    b64 = data.get("data") or ""
                    if b64:
                        try:
                            img_bytes = base64.b64decode(b64)
                            file = discord.File(io.BytesIO(img_bytes), filename=f"screenshot_{screenshots_sent + 1}.jpg")
                            caption = (last_intent[:1900] if last_intent else None)
                            await message.channel.send(content=caption, file=file)
                            screenshots_sent += 1
                            last_intent = ""
                        except Exception as exc:
                            _log.debug("Discord screenshot send failed: %s", exc)
                elif event_type == "intent":
                    # Buffer the agent's most recent stated intent so the next
                    # screenshot we send arrives with that as its caption.
                    explanation = data.get("explanation") or ""
                    action_type = data.get("action_type") or ""
                    if explanation or action_type:
                        last_intent = f"**{action_type}** — {explanation}".strip()
                elif event_type == "done":
                    reason = data.get("reason", "")
                    # Discord max message length is 2000 chars
                    reply = (reason or "Task complete.")[:1990]
                    await message.channel.send(reply)
                    return
                elif event_type == "error":
                    await message.channel.send(f"Error: {data.get('message', 'unknown error')}"[:1990])
                    return
        except Exception as exc:
            await message.channel.send(f"Task error: {exc}")

    try:
        await client.start(token)
    except asyncio.CancelledError:
        pass
    except discord.LoginFailure:
        _log.error("Discord bot failed to login — check DISCORD_BOT_TOKEN")
    except Exception as exc:
        _log.error("Discord bot crashed: %s", exc)
    finally:
        if not client.is_closed():
            try:
                await client.close()
            except Exception:
                pass


async def _stream_task(agent_service: "AgentService", task_id: str):
    """Yield (event_type, data) pairs from the log emitter queue for this task."""
    from ..log_emitter import log_emitter

    terminal = {"done", "error", "cancelled"}
    queue = log_emitter.subscribe(task_id)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=300.0)
            except asyncio.TimeoutError:
                return
            # Payload fields are spread directly into msg (no nested "data" key)
            event_type = msg.get("type", "")
            yield event_type, msg
            if event_type in terminal:
                return
    finally:
        log_emitter.unsubscribe(task_id, queue)
