"""Discord bot integration.

Reads DISCORD_BOT_TOKEN from the environment. If absent, logs a notice and
exits silently so the server starts cleanly without any tokens configured.

When the token is present, starts a discord.py client. Incoming messages
mentioning the bot (or DMs) are forwarded to AgentService as tasks and
streaming responses are sent back to the same channel.

Get a token: https://discord.com/developers/applications
  → New Application → Bot → Reset Token
  Required intents: Message Content Intent (enabled in Bot settings).
  Guild channel permission: Add Reactions (bot reacts to your message while working).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncIterable

if TYPE_CHECKING:
    from ..agent import AgentService

_log = logging.getLogger(__name__)

_INTEGRATION_NAME = "Discord"

# Reactions on the *user's* trigger message (OpenClaw-style feedback).
_REACT_WORKING = "\N{THINKING FACE}"  # 🤔


async def consume_discord_sse(
    channel: Any,
    working_msg: Any,
    events: AsyncIterable[tuple[str, dict]],
    *,
    discord_file_cls: Any,
    edit_throttle_s: float = 1.2,
    user_message: Any | None = None,
    bot_user: Any | None = None,
) -> None:
    """One working message edited as intents stream in; screenshots as separate sends.

    ``discord_file_cls`` should be ``discord.File`` when discord.py is available.
    ``edit_throttle_s`` defaults to ~OpenClaw (1.2s) to stay under Discord edit limits.

    If ``user_message`` and ``bot_user`` are set, adds a working reaction on the user's
    message and swaps it for a terminal reaction when the task finishes.
    """
    MAX_SCREENSHOTS = 5
    MAX_LINES = 8

    screenshots_sent = 0
    activity: deque[str] = deque(maxlen=MAX_LINES)
    activity.append("🤔 Thinking…")
    last_intent_caption = ""
    last_edit_at = 0.0

    def _render() -> str:
        return "\n".join(activity)[:1990]

    async def _maybe_edit(force: bool = False) -> None:
        nonlocal last_edit_at
        now = asyncio.get_event_loop().time()
        if force or (now - last_edit_at >= edit_throttle_s):
            try:
                await working_msg.edit(content=_render())
                last_edit_at = now
            except Exception as exc:
                _log.debug("Discord edit failed: %s", exc)

    async def _add_working_reaction() -> None:
        if user_message is None or bot_user is None:
            return
        try:
            await user_message.add_reaction(_REACT_WORKING)
        except Exception as exc:
            _log.debug("Discord working reaction failed: %s", exc)

    async def _finalize_reactions(outcome: str) -> None:
        if user_message is None or bot_user is None:
            return
        try:
            await user_message.remove_reaction(_REACT_WORKING, bot_user)
        except Exception:
            pass
        final: str | None = {
            "done": "\N{WHITE HEAVY CHECK MARK}",  # ✅
            "error": "\N{CROSS MARK}",  # ❌
            "cancelled": "\N{WARNING SIGN}",  # ⚠️
            "crash": "\N{CROSS MARK}",
            "timeout": None,
        }.get(outcome)
        if not final:
            return
        try:
            await user_message.add_reaction(final)
        except Exception as exc:
            _log.debug("Discord final reaction failed: %s", exc)

    outcome = "timeout"
    await _add_working_reaction()
    try:
        async for event_type, data in events:
            if event_type == "intent":
                explanation = (data.get("explanation") or "").strip()
                action_type = (data.get("action_type") or "").strip()
                if action_type or explanation:
                    line = f"🔧 **{action_type}**"
                    if explanation:
                        line += f" — {explanation[:120]}"
                    activity.append(line)
                    last_intent_caption = f"**{action_type}** — {explanation}".strip()
                    await _maybe_edit()
            elif event_type == "terminal_output":
                out = (data.get("output") or "").strip()
                if out:
                    snippet = out.splitlines()[-1][:90] if out else ""
                    if snippet:
                        activity.append(f"  └ `{snippet}`")
                        await _maybe_edit()
            elif event_type == "action_result":
                out = (data.get("output") or "").strip()
                ok = data.get("ok", True)
                if out:
                    first_line = out.splitlines()[0][:120] if out else ""
                    prefix = "✓" if ok else "✗"
                    activity.append(f"  {prefix} {first_line}")
                    await _maybe_edit()
            elif event_type == "screenshot" and screenshots_sent < MAX_SCREENSHOTS:
                b64 = data.get("data") or ""
                if b64:
                    try:
                        img_bytes = base64.b64decode(b64)
                        file = discord_file_cls(io.BytesIO(img_bytes), filename=f"screenshot_{screenshots_sent + 1}.jpg")
                        caption = (last_intent_caption[:1900] if last_intent_caption else None)
                        await channel.send(content=caption, file=file)
                        screenshots_sent += 1
                        last_intent_caption = ""
                    except Exception as exc:
                        _log.debug("Discord screenshot send failed: %s", exc)
            elif event_type == "done":
                reason = (data.get("reason") or "Task complete.").strip()
                activity.append(f"✅ {reason[:300]}")
                await _maybe_edit(force=True)
                outcome = "done"
                return
            elif event_type == "error":
                err = (data.get("message") or "unknown error").strip()
                activity.append(f"❌ Error: {err[:300]}")
                await _maybe_edit(force=True)
                outcome = "error"
                return
            elif event_type == "cancelled":
                msg = (data.get("message") or "Task cancelled.").strip()
                activity.append(f"⚠️ {msg[:300]}")
                await _maybe_edit(force=True)
                outcome = "cancelled"
                return
    except Exception as exc:
        activity.append(f"❌ Task crashed: {exc}")
        await _maybe_edit(force=True)
        outcome = "crash"
    finally:
        await _finalize_reactions(outcome)


async def start_discord(agent_service: "AgentService") -> None:
    """Entry point called from FastAPI lifespan. Returns immediately if token absent."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        _log.info("%s integration disabled (no DISCORD_BOT_TOKEN in env)", _INTEGRATION_NAME)
        print("[Discord] disabled — no DISCORD_BOT_TOKEN in environment", flush=True)
        return

    try:
        import discord
    except ImportError:
        _log.warning(
            "%s integration disabled — discord.py not installed. "
            "Run: pip install discord.py",
            _INTEGRATION_NAME,
        )
        print("[Discord] disabled — install discord.py (pip install discord.py)", flush=True)
        return

    _log.info("Starting %s integration…", _INTEGRATION_NAME)
    print("[Discord] connecting to gateway…", flush=True)

    intents = discord.Intents.default()
    intents.message_content = True  # Required to read message body
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        _log.info("Discord bot connected as %s (id: %s)", client.user, client.user.id if client.user else "?")
        print(f"[Discord] online as {client.user} (mention in #general or DM to test)", flush=True)

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

        # Openclaw-style streaming: one Discord message that gets EDITED as
        # the agent works, throttled to ~1.2s so we don't hit Discord's
        # 5-edits-per-5s rate limit. Screenshots still arrive as separate
        # attachments (you can't edit content+file together cleanly).
        working_msg = await message.channel.send("🤔 Thinking…")

        task_id = f"dc_{message.author.id}_{int(asyncio.get_event_loop().time() * 1000)}"
        try:
            agent_service.init_task(task_id, goal)
        except Exception as exc:
            await working_msg.edit(content=f"Failed to start task: {exc}"[:1990])
            return

        await consume_discord_sse(
            message.channel,
            working_msg,
            _stream_task(agent_service, task_id),
            discord_file_cls=discord.File,
            user_message=message,
            bot_user=client.user,
        )

    try:
        await client.start(token)
    except asyncio.CancelledError:
        pass
    except discord.LoginFailure:
        _log.error("Discord bot failed to login — check DISCORD_BOT_TOKEN")
        print("[Discord] LOGIN FAILED — token wrong or reset; fix .env and restart", flush=True)
    except Exception as exc:
        _log.error("Discord bot crashed: %s", exc)
        print(f"[Discord] crashed: {exc}", flush=True)
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
