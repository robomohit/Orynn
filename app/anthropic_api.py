"""Native Anthropic (Claude) Messages API support for PlannerProvider.

The rest of the provider layer speaks the OpenAI chat-completions dialect
(OpenRouter / OpenAI / Groq). Claude has its own wire format, so this module
owns the translation in both directions:

  * request side  — OpenAI-style tools + message history -> Anthropic payload
  * response side — Anthropic SSE stream -> the event dicts the agent loop
                    already consumes (thought / tool_partial / tool_call /
                    text_only / provider_info)

It is deliberately free of PlannerProvider state so it can be unit-tested with
plain dicts.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# The premium default. Override with ANTHROPIC_MODEL (e.g. claude-fable-5-2).
DEFAULT_MODEL = "claude-opus-5-5"

# Shown in the model picker when an Anthropic key is configured.
MODELS: List[str] = [
    "claude-opus-5-5",
    "claude-fable-5-2",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
]

# "tier:premium" — stays on Claude when the first choice is rate-limited or
# overloaded, instead of dropping to a free model mid-task.
PREMIUM_CHAIN: List[str] = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

DISPLAY_NAMES: Dict[str, str] = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-fable-5-2": "Claude Fable 5.2",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
}

# Orynn's effort slider -> the API's output_config.effort. "Max" maps to xhigh:
# the strongest setting that is still sensible for an interactive agent loop.
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"}

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Set once the API rejects the `fallbacks` field so we stop sending it.
_fallbacks_rejected = False

# Models that take adaptive thinking + output_config.effort. Anything older
# (claude-3-*, 4.0-4.5) keeps the legacy request shape.
_ADAPTIVE_RE = re.compile(
    r"claude-(?:fable|mythos|opus-(?:4-[6-9]|[5-9])|sonnet-(?:4-[6-9]|[5-9])|haiku-[5-9])"
)
_SERVER_FALLBACK_RE = re.compile(r"claude-(?:fable-5-[12]|opus-5(?:-5)?$|sonnet-5-5)")
_NO_XHIGH_RE = re.compile(r"claude-(?:opus|sonnet)-4-6")


def default_model() -> str:
    return (os.environ.get("ANTHROPIC_MODEL") or "").strip() or DEFAULT_MODEL


def is_adaptive_model(model: str) -> bool:
    return bool(_ADAPTIVE_RE.match((model or "").lower()))


def api_effort(model: str, effort: Optional[str]) -> str:
    level = _EFFORT_MAP.get((effort or "medium").strip().lower(), "medium")
    if level == "xhigh" and _NO_XHIGH_RE.match((model or "").lower()):
        return "high"
    return level


def fallback_chain(model: str, tier: Optional[str] = None) -> List[str]:
    """Models to try in order. Only the premium tier chains; an explicitly
    chosen model is never silently swapped for another."""
    if tier == "premium":
        return [model] + [m for m in PREMIUM_CHAIN if m != model]
    return [model]


# ── Request building ────────────────────────────────────────────────────────

def _split_data_url(image_data: str) -> Tuple[str, str]:
    if image_data.startswith("data:") and "," in image_data:
        head, data = image_data.split(",", 1)
        mime = head[5:].split(";", 1)[0] or "image/jpeg"
        return mime, data
    return "image/jpeg", image_data


def _image_block(image_data: str) -> Dict[str, Any]:
    mime, data = _split_data_url(image_data)
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


def convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI function schemas -> Anthropic tool definitions."""
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) and "function" in t else t
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or fn.get("input_schema")
            or {"type": "object", "properties": {}},
        })
    return out


_ID_OK = re.compile(r"[^a-zA-Z0-9_-]")


def _clean_id(raw: Any) -> str:
    return _ID_OK.sub("_", str(raw or ""))


def _user_blocks(content: Any) -> List[Dict[str, Any]]:
    """A user message's content (string or OpenAI-style parts) -> Anthropic blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: List[Dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and (part.get("text") or "").strip():
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            if url.startswith("data:"):
                blocks.append(_image_block(url))
        elif part.get("type") in ("image", "tool_result"):
            blocks.append(part)
    return blocks


def convert_messages(
    messages: List[Dict[str, Any]], screenshot_b64: Optional[str] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """OpenAI-style history -> (extra system text, Anthropic messages).

    The agent trims and truncates its history between steps, so this also
    repairs what the Anthropic API would reject: tool results whose tool call
    was trimmed away, tool calls with no result, a history that starts or ends
    on an assistant turn.
    """
    extra_system: List[str] = []
    out: List[Dict[str, Any]] = []
    unnamed_ids: List[str] = []  # generated ids awaiting their (id-less) results
    gen = 0

    def push(role: str, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages or []:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            text = content if isinstance(content, str) else ""
            if not text.strip():
                continue
            if out:
                push("user", [{"type": "text", "text": f"[System note]\n{text}"}])
            else:
                extra_system.append(text)
        elif role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                tid = _clean_id(tc.get("id"))
                if not tid:
                    gen += 1
                    tid = f"call_gen_{gen}"
                    unnamed_ids.append(tid)
                blocks.append({"type": "tool_use", "id": tid, "name": fn.get("name") or "tool", "input": args})
            push("assistant", blocks)
        elif role == "tool":
            tid = _clean_id(m.get("tool_call_id"))
            if not tid and unnamed_ids:
                tid = unnamed_ids.pop(0)
            text = content if isinstance(content, str) else json.dumps(content, default=str)
            push("user", [{"type": "tool_result", "tool_use_id": tid, "content": text or "(empty)"}])
        else:
            push("user", _user_blocks(content))

    # Repair tool_use / tool_result pairing.
    for i, msg in enumerate(out):
        if msg["role"] != "user":
            continue
        prev_ids = set()
        if i > 0:
            prev_ids = {b["id"] for b in out[i - 1]["content"] if b.get("type") == "tool_use"}
        results, others = [], []
        for b in msg["content"]:
            if b.get("type") == "tool_result":
                if b.get("tool_use_id") in prev_ids:
                    results.append(b)
                    prev_ids.discard(b["tool_use_id"])
                else:  # its tool call was trimmed from history
                    others.append({"type": "text", "text": f"<observation>\n{b.get('content', '')}\n</observation>"})
            else:
                others.append(b)
        for missing in sorted(prev_ids):
            results.append({"type": "tool_result", "tool_use_id": missing, "content": "(no result recorded)"})
        msg["content"] = results + others
    # A tool call followed by another assistant turn can't happen after push()
    # merging; only a trailing assistant turn is left to close.
    if out and out[-1]["role"] == "assistant":
        pending = [b["id"] for b in out[-1]["content"] if b.get("type") == "tool_use"]
        if pending:
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": t, "content": "(no result recorded)"} for t in pending
            ]})
        else:
            out.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(Earlier conversation trimmed.)"}]})
    if not out:
        out.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})

    # Same rule as the OpenAI path: the screenshot rides on a plain user turn.
    if screenshot_b64 and messages and messages[-1].get("role") == "user":
        out[-1]["content"].insert(0, _image_block(screenshot_b64))

    return "\n\n".join(extra_system), out


def build_request(
    model: str,
    system: str,
    messages: List[Dict[str, Any]],
    *,
    api_key: str,
    effort: Optional[str] = "medium",
    tools: Optional[List[Dict[str, Any]]] = None,
    stream: bool = False,
    legacy_thinking_budget: int = 0,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Return (headers, payload) for POST /v1/messages. `messages` and `tools`
    are already in Anthropic format."""
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if system:
        # Cache breakpoint on the system prompt covers tools + system, which
        # are identical on every step of a task.
        payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tools:
        payload["tools"] = tools
        # The agent loop executes one action per step.
        payload["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    if stream:
        payload["stream"] = True

    if is_adaptive_model(model):
        payload["max_tokens"] = 64000 if stream else 16000
        payload["thinking"] = {"type": "adaptive"}
        payload["output_config"] = {"effort": api_effort(model, effort)}
        if _SERVER_FALLBACK_RE.match(model.lower()) and not _fallbacks_rejected:
            # If a safety classifier declines the request, the API re-runs it
            # on a fallback model inside the same call.
            headers["anthropic-beta"] = _FALLBACK_BETA
            payload["fallbacks"] = "default"
    else:
        budget = int(legacy_thinking_budget or 0)
        payload["max_tokens"] = max(4096, budget + 1024) if budget else (8192 if stream else 4096)
        if budget:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    return headers, payload


def strip_fallbacks(headers: Dict[str, str], payload: Dict[str, Any]) -> None:
    """Drop the server-side fallback opt-in (after the API rejected it)."""
    global _fallbacks_rejected
    _fallbacks_rejected = True
    payload.pop("fallbacks", None)
    headers.pop("anthropic-beta", None)


def is_fallbacks_rejection(status: int, body: str) -> bool:
    return status == 400 and "fallback" in (body or "").lower()


def is_retryable_status(status: int) -> bool:
    return status in (408, 409, 429) or status >= 500  # 529 = overloaded


def error_text(status: int, body: str, model: str) -> str:
    detail = (body or "").strip()[:600]
    try:
        detail = json.loads(body)["error"]["message"]
    except Exception:
        pass
    if status == 401:
        return "Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY."
    if is_retryable_status(status):
        # "busy" + the status code keep the chain-retry logic recognising this.
        return f"Anthropic is busy ({status}, {model}): {detail or 'rate limited or overloaded'}"
    return f"Anthropic {status} ({model}): {detail or 'empty response body'}"


def refusal_text(stop_details: Any) -> str:
    category = (stop_details or {}).get("category") if isinstance(stop_details, dict) else None
    return "The model declined this request" + (f" (safety category: {category})." if category else ".")


def record_usage(usage: Dict[str, int], data: Dict[str, Any]) -> None:
    u = data or {}
    usage["input"] = usage.get("input", 0) + int(
        (u.get("input_tokens") or 0)
        + (u.get("cache_read_input_tokens") or 0)
        + (u.get("cache_creation_input_tokens") or 0)
    )
    usage["output"] = usage.get("output", 0) + int(u.get("output_tokens") or 0)


# ── Streaming ───────────────────────────────────────────────────────────────

async def stream_events(
    *,
    models: List[str],
    system: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    api_key: str,
    effort: Optional[str] = "medium",
    legacy_thinking_budget: int = 0,
    usage: Optional[Dict[str, int]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream one model turn as agent-loop events. `messages`/`tools` are in
    Anthropic format. Tries each model in `models` until one starts streaming."""
    usage = usage if usage is not None else {}
    last_err: Optional[Exception] = None
    timeout = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=10.0)

    for n, model in enumerate(models):
        if n:
            yield {"type": "provider_info", "model": model, "fallback": True}
        headers, payload = build_request(
            model, system, messages, api_key=api_key, effort=effort, tools=tools,
            stream=True, legacy_thinking_budget=legacy_thinking_budget,
        )
        text = ""
        blocks: Dict[int, Dict[str, Any]] = {}  # index -> {type, id, name, json}
        stop_reason: Optional[str] = None
        stop_details: Any = None
        streamed = False
        last_partial = 0.0
        retry_without_fallbacks = True
        try:
            while True:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", API_URL, headers=headers, json=payload) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            if retry_without_fallbacks and "fallbacks" in payload and is_fallbacks_rejection(resp.status_code, body):
                                retry_without_fallbacks = False
                                strip_fallbacks(headers, payload)
                                continue
                            raise _ApiError(resp.status_code, error_text(resp.status_code, body, model))
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                ev = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                continue
                            etype = ev.get("type")
                            if etype == "message_start":
                                record_usage(usage, (ev.get("message") or {}).get("usage") or {})
                            elif etype == "content_block_start":
                                cb = ev.get("content_block") or {}
                                blocks[ev.get("index", 0)] = {
                                    "type": cb.get("type"), "id": cb.get("id", ""),
                                    "name": cb.get("name", ""), "json": "",
                                }
                            elif etype == "content_block_delta":
                                delta = ev.get("delta") or {}
                                dtype = delta.get("type")
                                if dtype == "text_delta" and delta.get("text"):
                                    streamed = True
                                    text += delta["text"]
                                    yield {"type": "thought", "content": delta["text"]}
                                elif dtype == "input_json_delta":
                                    streamed = True
                                    entry = blocks.setdefault(ev.get("index", 0), {"type": "tool_use", "id": "", "name": "", "json": ""})
                                    entry["json"] += delta.get("partial_json") or ""
                                    now = asyncio.get_event_loop().time()
                                    if now - last_partial >= 0.2:
                                        last_partial = now
                                        yield {"type": "tool_partial", "name": entry["name"], "args_partial": entry["json"]}
                            elif etype == "message_delta":
                                d = ev.get("delta") or {}
                                stop_reason = d.get("stop_reason") or stop_reason
                                stop_details = d.get("stop_details") or stop_details
                                record_usage(usage, {"output_tokens": (ev.get("usage") or {}).get("output_tokens")})
                            elif etype == "error":
                                err = ev.get("error") or {}
                                kind = err.get("type", "error")
                                status = 529 if kind == "overloaded_error" else 429 if kind == "rate_limit_error" else 500
                                raise _ApiError(status, error_text(status, json.dumps(ev), model))
                break
        except _ApiError as e:
            last_err = RuntimeError(str(e))
            if is_retryable_status(e.status) and not streamed:
                continue  # next model in the chain
            raise last_err from None
        except httpx.TransportError as e:
            last_err = RuntimeError(f"Anthropic connection failed ({model}): {e}")
            if not streamed:
                continue
            raise last_err from None

        if stop_reason == "refusal":
            yield {"type": "text_only", "content": text or refusal_text(stop_details), "refusal": not text}
            return
        tool_blocks = [blocks[i] for i in sorted(blocks) if blocks[i].get("type") == "tool_use"]
        if tool_blocks and stop_reason == "max_tokens":
            raise RuntimeError(f"Anthropic response was cut off at max_tokens mid tool call ({model}).")
        if tool_blocks:
            # One action per step (parallel tool use is disabled in the request).
            tb = tool_blocks[0]
            try:
                args = json.loads(tb["json"]) if tb["json"] else {}
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "id": tb["id"], "name": tb["name"],
                   "args": args if isinstance(args, dict) else {}, "thought": text}
            return
        yield {"type": "text_only", "content": text}
        return

    raise last_err or RuntimeError("Anthropic request failed.")


class _ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
