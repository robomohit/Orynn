"""Telegram bot integration.

Reads TELEGRAM_BOT_TOKEN from the environment. If absent, logs a notice and
exits silently so the server starts cleanly without any tokens configured.

When the token is present, starts a polling loop via python-telegram-bot.
Incoming messages are forwarded to AgentService as tasks and streaming
responses are sent back to the same chat.

Get a token: https://t.me/BotFather  (/newbot command)
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

_INTEGRATION_NAME = "Telegram"


def _parse_id_list(value: str) -> set[int]:
    ids: set[int] = set()
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            _log.warning("Ignoring invalid Telegram allowlist id: %r", part)
    return ids


def _telegram_sender_allowed(chat_id: int | None, user_id: int | None, *, allowed_chat_ids: set[int], allowed_user_ids: set[int]) -> bool:
    return (chat_id is not None and chat_id in allowed_chat_ids) or (user_id is not None and user_id in allowed_user_ids)


async def consume_telegram_sse(
    message: Any,
    working_msg: Any,
    events: AsyncIterable[tuple[str, dict]],
    *,
    edit_throttle_s: float = 1.2,
    reaction_bot: Any | None = None,
) -> None:
    """One status message edited as intents stream in; screenshots as separate photos.

    If ``reaction_bot`` is set (``context.bot``), sets a 🤔 reaction on the user's message
    while working and replaces it with a terminal emoji when the task ends (Bot API 7+).
    """
    MAX_SCREENSHOTS = 5
    MAX_LINES = 8

    screenshots_sent = 0
    activity: deque[str] = deque(maxlen=MAX_LINES)
    activity.append("🤔 Thinking…")
    last_intent_caption = ""
    last_edit_at = 0.0

    def _render() -> str:
        return "\n".join(activity)[:3900]

    async def _maybe_edit(force: bool = False) -> None:
        nonlocal last_edit_at
        now = asyncio.get_event_loop().time()
        if force or (now - last_edit_at >= edit_throttle_s):
            try:
                await working_msg.edit_text(_render())
                last_edit_at = now
            except Exception as exc:
                _log.debug("Telegram edit failed: %s", exc)

    async def _set_user_message_reaction(emoji: str | None) -> None:
        """emoji=None clears the bot's reaction (best-effort)."""
        if reaction_bot is None:
            return
        chat_id = getattr(message, "chat_id", None)
        message_id = getattr(message, "message_id", None)
        if chat_id is None or message_id is None:
            return
        try:
            from telegram import ReactionTypeEmoji
        except ImportError:
            return
        try:
            if emoji is None:
                await reaction_bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=[],
                )
            else:
                await reaction_bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=[ReactionTypeEmoji(emoji)],
                )
        except Exception as exc:
            _log.debug("Telegram message reaction failed: %s", exc)

    outcome = "timeout"
    await _set_user_message_reaction("\N{THINKING FACE}")
    try:
        async for event_type, data in events:
            if event_type == "intent":
                explanation = (data.get("explanation") or "").strip()
                action_type = (data.get("action_type") or "").strip()
                if action_type or explanation:
                    line = f"🔧 {action_type}"
                    if explanation:
                        line += f" — {explanation[:120]}"
                    activity.append(line)
                    last_intent_caption = f"{action_type} — {explanation}".strip()
                    await _maybe_edit()
            elif event_type == "terminal_output":
                out = (data.get("output") or "").strip()
                if out:
                    snippet = out.splitlines()[-1][:90] if out else ""
                    if snippet:
                        activity.append(f"  └ {snippet}")
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
                        caption = (last_intent_caption[:1000] if last_intent_caption else None)
                        await message.reply_photo(photo=io.BytesIO(img_bytes), caption=caption)
                        screenshots_sent += 1
                        last_intent_caption = ""
                    except Exception as exc:
                        _log.debug("Telegram screenshot send failed: %s", exc)
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
        final = {
            "done": "\N{WHITE HEAVY CHECK MARK}",
            "error": "\N{CROSS MARK}",
            "cancelled": "\N{WARNING SIGN}",
            "crash": "\N{CROSS MARK}",
            "timeout": None,
        }.get(outcome)
        await _set_user_message_reaction(final)


async def start_telegram(agent_service: "AgentService", submit_task: Any | None = None) -> None:
    """Entry point called from FastAPI lifespan. Returns immediately if token absent."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        _log.info("%s integration disabled (no TELEGRAM_BOT_TOKEN in env)", _INTEGRATION_NAME)
        return
    allowed_chat_ids = _parse_id_list(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    allowed_user_ids = _parse_id_list(os.environ.get("TELEGRAM_ALLOWED_USER_IDS", ""))
    if not allowed_chat_ids and not allowed_user_ids:
        _log.warning(
            "%s integration disabled: set TELEGRAM_ALLOWED_CHAT_IDS or "
            "TELEGRAM_ALLOWED_USER_IDS before enabling remote task submission.",
            _INTEGRATION_NAME,
        )
        return

    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError:
        _log.warning(
            "%s integration disabled — python-telegram-bot not installed. "
            "Run: pip install python-telegram-bot",
            _INTEGRATION_NAME,
        )
        return

    _log.info("Starting %s integration…", _INTEGRATION_NAME)

    async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        goal = update.message.text.strip()
        chat_id = update.message.chat_id
        user_id = update.effective_user.id if update.effective_user else None
        if not _telegram_sender_allowed(
            chat_id,
            user_id,
            allowed_chat_ids=allowed_chat_ids,
            allowed_user_ids=allowed_user_ids,
        ):
            _log.warning("Ignoring Telegram task from unauthorized chat=%s user=%s", chat_id, user_id)
            await update.message.reply_text("This Orynn bot is restricted to approved users.")
            return
        _log.debug("Telegram message from %s: %s", chat_id, goal[:80])

        # Openclaw-style streaming: one message edited as the agent works,
        # throttled so we don't hammer Telegram's edit rate limit.
        working_msg = await update.message.reply_text("🤔 Thinking…")
        task_id = f"tg_{chat_id}_{int(asyncio.get_event_loop().time() * 1000)}"
        try:
            if submit_task:
                record = submit_task(goal=goal, task_id=task_id, source="telegram")
                task_id = record.id
            else:
                agent_service.init_task(task_id, goal)
        except Exception as exc:
            try:
                await working_msg.edit_text(f"Failed to start task: {exc}")
            except Exception:
                pass
            return

        await consume_telegram_sse(
            update.message,
            working_msg,
            _stream_task(agent_service, task_id),
            reaction_bot=context.bot,
        )

    async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                "Hi! I'm Orynn. Send me a task and I'll get to work."
            )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        _log.info("%s bot polling started.", _INTEGRATION_NAME)
        # Keep running — the task stays alive for the app lifetime
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        _log.error("%s bot crashed: %s", _INTEGRATION_NAME, exc)
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
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
