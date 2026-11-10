"""Premium (Claude) support: native Anthropic request building, history
conversion, SSE -> agent events, and auto-selection precedence."""
from __future__ import annotations

import json

import pytest

from app import anthropic_api as A
from app.providers import MODEL_TIERS, PlannerProvider, resolve_model_tier


# ── request building ────────────────────────────────────────────────────────

def test_current_models_use_adaptive_thinking_and_real_effort():
    headers, payload = A.build_request(
        "claude-opus-5-5", "sys", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        api_key="k", effort="max", stream=True,
    )
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "xhigh"}
    assert "budget_tokens" not in json.dumps(payload)
    assert "temperature" not in payload
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["fallbacks"] == "default" and "server-side-fallback" in headers["anthropic-beta"]
    assert headers["x-api-key"] == "k"


def test_legacy_models_keep_the_old_request_shape():
    headers, payload = A.build_request("claude-3-5-sonnet-20241022", "sys", [], api_key="k", effort="high",
                                       legacy_thinking_budget=5000)
    assert "output_config" not in payload and "fallbacks" not in payload
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 5000}
    assert "anthropic-beta" not in headers


def test_tools_are_converted_and_parallel_calls_disabled():
    tools = A.convert_tools([{"type": "function", "function": {
        "name": "click", "description": "Click a control", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}}])
    assert tools == [{"name": "click", "description": "Click a control",
                      "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]
    _, payload = A.build_request("claude-opus-5-5", "s", [], api_key="k", tools=tools)
    assert payload["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


# ── history conversion ──────────────────────────────────────────────────────

def _tool_turn(tid, name="click", args='{"name": "OK"}', result="clicked"):
    return [
        {"role": "assistant", "content": "thinking", "tool_calls": [
            {"id": tid, "type": "function", "function": {"name": name, "arguments": args}}]},
        {"role": "tool", "tool_call_id": tid, "content": result},
    ]


def test_tool_round_trip_converts_to_tool_use_and_tool_result():
    _, msgs = A.convert_messages([{"role": "user", "content": "go"}, *_tool_turn("call-1")])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    use = msgs[1]["content"][1]
    assert use == {"type": "tool_use", "id": "call-1", "name": "click", "input": {"name": "OK"}}
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "call-1", "content": "clicked"}


def test_orphaned_tool_result_from_trimmed_history_becomes_text():
    # The agent keeps only the last N messages, which can cut a tool call from its result.
    _, msgs = A.convert_messages([{"role": "tool", "tool_call_id": "call-9", "content": "window list"},
                                  *_tool_turn("call-10")])
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["type"] == "text" and "window list" in msgs[0]["content"][0]["text"]
    assert all(b["type"] != "tool_result" for b in msgs[0]["content"])


def test_history_never_ends_on_an_assistant_turn():
    _, msgs = A.convert_messages([{"role": "user", "content": "go"}, _tool_turn("call-1")[0]])
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"][0]["tool_use_id"] == "call-1"


def test_leading_system_messages_fold_into_system_and_screenshot_attaches():
    extra, msgs = A.convert_messages(
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "what is this"}],
        "data:image/png;base64,AAAA",
    )
    assert extra == "be terse"
    img = msgs[0]["content"][0]
    assert img["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}


# ── streaming ───────────────────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, status, lines, body=""):
        self.status_code, self._lines, self._body = status, lines, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aread(self):
        return self._body.encode()

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _sse(*events):
    return [f"data: {json.dumps(e)}" for e in events]


def _install(monkeypatch, responses, seen):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None, json=None):
            seen.append({"url": url, "headers": dict(headers), "payload": dict(json)})
            return responses.pop(0)

    monkeypatch.setattr(A.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(A, "_fallbacks_rejected", False)


_TOOL_STREAM = _sse(
    {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 90}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hidden"}},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Clicking OK."}},
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "click", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"name": '}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"OK"}'}},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
    {"type": "message_stop"},
)


async def _collect(provider, tools=None):
    schema = tools or [{"type": "function", "function": {"name": "click", "parameters": {"type": "object"}}}]
    return [e async for e in provider.stream_chat_with_tools("sys", [{"role": "user", "content": "press ok"}], schema)]


@pytest.mark.asyncio
async def test_claude_tool_call_streams_as_agent_events(monkeypatch):
    seen = []
    _install(monkeypatch, [_FakeStream(200, _TOOL_STREAM)], seen)
    provider = PlannerProvider(model="claude-opus-5-5")
    provider.effort = "high"
    events = await _collect(provider)

    call = events[-1]
    assert call == {"type": "tool_call", "id": "toolu_1", "name": "click", "args": {"name": "OK"}, "thought": "Clicking OK."}
    assert {"type": "thought", "content": "Clicking OK."} in events
    assert not any("hidden" in json.dumps(e) for e in events)  # thinking is never surfaced as reply text
    req = seen[0]
    assert req["url"] == A.API_URL
    assert req["payload"]["model"] == "claude-opus-5-5"
    assert req["payload"]["output_config"] == {"effort": "high"}
    assert req["payload"]["tools"][0]["name"] == "click"
    assert provider.total_tokens == 107


@pytest.mark.asyncio
async def test_premium_tier_stays_on_claude_when_first_model_is_overloaded(monkeypatch):
    seen = []
    busy = _FakeStream(529, [], json.dumps({"error": {"type": "overloaded_error", "message": "Overloaded"}}))
    _install(monkeypatch, [busy, _FakeStream(200, _TOOL_STREAM)], seen)
    provider = PlannerProvider(model="tier:premium")
    events = await _collect(provider)

    assert [r["payload"]["model"] for r in seen] == MODEL_TIERS["premium"][:2]
    assert {"type": "provider_info", "model": MODEL_TIERS["premium"][1], "fallback": True} in events
    assert events[-1]["type"] == "tool_call"


@pytest.mark.asyncio
async def test_rejected_fallbacks_field_is_dropped_and_retried(monkeypatch):
    seen = []
    rejected = _FakeStream(400, [], json.dumps({"error": {"type": "invalid_request_error", "message": "fallbacks: unknown field"}}))
    _install(monkeypatch, [rejected, _FakeStream(200, _TOOL_STREAM)], seen)
    events = await _collect(PlannerProvider(model="claude-opus-5-5"))

    assert "fallbacks" in seen[0]["payload"] and "fallbacks" not in seen[1]["payload"]
    assert "anthropic-beta" not in seen[1]["headers"]
    assert events[-1]["type"] == "tool_call"


@pytest.mark.asyncio
async def test_bad_key_surfaces_a_clear_error(monkeypatch):
    _install(monkeypatch, [_FakeStream(401, [], '{"error": {"message": "invalid x-api-key"}}')], [])
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await _collect(PlannerProvider(model="claude-opus-5-5"))


@pytest.mark.asyncio
async def test_plain_chat_streams_text(monkeypatch):
    stream = _sse(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    )
    _install(monkeypatch, [_FakeStream(200, stream)], [])
    provider = PlannerProvider(model="claude-sonnet-5-5")
    chunks = [c async for c in provider.stream_chat("sys", [{"role": "user", "content": "hi"}])]
    assert "".join(chunks) == "Hello"


# ── selection ───────────────────────────────────────────────────────────────

def test_premium_tier_resolves_to_claude():
    assert resolve_model_tier("tier:premium") == "premium"
    provider = PlannerProvider(model="tier:premium")
    assert provider.model == "claude-opus-5-5" and provider._is_anthropic()


def test_anthropic_key_wins_auto_selection(monkeypatch):
    from app.main import _select_model_for_task

    monkeypatch.delenv("PREFER_FREE_MODELS", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("DESKTOP_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    for mode in ("auto", "coding", "computer"):
        sel = _select_model_for_task("open notepad and type hello", mode=mode)
        assert sel["selected_model"] == "claude-opus-5-5", (mode, sel)
        assert sel["required_key"] == "ANTHROPIC_API_KEY"

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-fable-5-2")
    assert _select_model_for_task("hi")["selected_model"] == "claude-fable-5-2"

    monkeypatch.setenv("DESKTOP_MODEL", "z-ai/glm-4.5-air:free")
    assert _select_model_for_task("click ok", mode="computer")["selected_model"] == "z-ai/glm-4.5-air:free"

    monkeypatch.setenv("PREFER_FREE_MODELS", "1")
    assert not _select_model_for_task("hi")["selected_model"].startswith("claude")
