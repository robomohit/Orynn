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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentService

_log = logging.getLogger(__name__)

_INTEGRATION_NAME = "Telegram"


async def start_telegram(agent_service: "AgentService") -> None:
    """Entry point called from FastAPI lifespan. Returns immediately if token absent."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        _log.info("%s integration disabled (no TELEGRAM_BOT_TOKEN in env)", _INTEGRATION_NAME)
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
        _log.debug("Telegram message from %s: %s", chat_id, goal[:80])

        await update.message.reply_text("Working on it…")
        task_id = f"tg_{chat_id}_{int(asyncio.get_event_loop().time() * 1000)}"
        try:
            agent_service.init_task(task_id, goal)
        except Exception as exc:
            await update.message.reply_text(f"Failed to start task: {exc}")
            return

        collected: list[str] = []
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
                            caption = (last_intent[:1000] if last_intent else None)
                            await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption=caption)
                            screenshots_sent += 1
                            last_intent = ""
                        except Exception as exc:
                            _log.debug("Telegram screenshot send failed: %s", exc)
                elif event_type == "intent":
                    explanation = data.get("explanation") or ""
                    action_type = data.get("action_type") or ""
                    if explanation or action_type:
                        last_intent = f"{action_type} — {explanation}".strip()
                elif event_type == "done":
                    reason = data.get("reason", "")
                    await update.message.reply_text(reason or "Task complete.")
                    return
                elif event_type == "error":
                    await update.message.reply_text(f"Error: {data.get('message', 'unknown error')}")
                    return
        except Exception as exc:
            await update.message.reply_text(f"Task error: {exc}")

    async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                "Hi! I'm AI Computer. Send me a task and I'll get to work."
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
