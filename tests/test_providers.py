"""Unit tests for app/providers.py streaming and JSON utilities."""

from __future__ import annotations

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from app.providers import PlannerProvider
    return PlannerProvider(model="openrouter/test-model")


def _sse_lines(*chunks: dict) -> list[str]:
    """Convert dicts to SSE 'data: ...' lines followed by [DONE]."""
    lines = [f"data: {json.dumps(c)}" for c in chunks]
    lines.append("data: [DONE]")
    return lines


@pytest.mark.asyncio
async def test_stream_chat_single_tool_call(provider, monkeypatch):
    """Single tool call (existing behaviour) still works."""
    sse = _sse_lines(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"path":'}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"foo.txt"}'}}]}, "finish_reason": "tool_calls"}]},
    )

    async def fake_aiter_lines():
        for line in sse:
            yield line

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines

    mock_stream_cm = AsyncMock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream_cm)

    mock_client_cm = AsyncMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.httpx.AsyncClient", return_value=mock_client_cm):
        events = [e async for e in provider.stream_chat_with_tools("sys", [{"role": "user", "content": "go"}], [])]

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "read_file"
    assert tool_calls[0]["args"] == {"path": "foo.txt"}


@pytest.mark.asyncio
async def test_stream_chat_multiple_tool_calls(provider, monkeypatch):
    """Two parallel tool calls in one response — both must be emitted in index order."""
    sse = _sse_lines(
        # First chunk: names for both tool_calls
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"path":'}},
            {"index": 1, "id": "c2", "function": {"name": "run_command", "arguments": '{"cmd":'}},
        ]}, "finish_reason": None}]},
        # Second chunk: finish args for both
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"a.txt"}'}},
            {"index": 1, "function": {"arguments": '"ls"}'}},
        ]}, "finish_reason": "tool_calls"}]},
    )

    async def fake_aiter_lines():
        for line in sse:
            yield line

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines

    mock_stream_cm = AsyncMock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=mock_stream_cm)

    mock_client_cm = AsyncMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.httpx.AsyncClient", return_value=mock_client_cm):
        events = [e async for e in provider.stream_chat_with_tools("sys", [{"role": "user", "content": "go"}], [])]

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 2
    assert tool_calls[0]["name"] == "read_file"
    assert tool_calls[0]["args"] == {"path": "a.txt"}
    assert tool_calls[1]["name"] == "run_command"
    assert tool_calls[1]["args"] == {"cmd": "ls"}


def _make_client_cm_for_resp(mock_resp):
    """Wrap a mock response in the nested async context managers httpx expects."""
    stream_cm = AsyncMock()
    stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    client = AsyncMock()
    client.stream = MagicMock(return_value=stream_cm)

    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=client)
    client_cm.__aexit__ = AsyncMock(return_value=False)
    return client_cm


@pytest.mark.asyncio
async def test_stream_chat_fallback_emits_provider_info(monkeypatch):
    """When primary model returns 429, fallback yields provider_info before streaming."""
    import httpx as _httpx
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from app.providers import PlannerProvider
    provider = PlannerProvider(model="openrouter/primary-model")

    monkeypatch.setattr(provider, "_openrouter_models_to_try",
                        lambda *a, **kw: ["primary-model", "fallback-model"])

    # Primary: 429 — httpx raises HTTPStatusError
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.raise_for_status = MagicMock(
        side_effect=_httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock(status_code=429))
    )

    # Fallback: 200 + text_only stop
    fallback_sse = _sse_lines({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})

    async def fake_aiter():
        for line in fallback_sse:
            yield line

    mock_200 = AsyncMock()
    mock_200.status_code = 200
    mock_200.raise_for_status = MagicMock()
    mock_200.aiter_lines = fake_aiter

    client_cms = iter([_make_client_cm_for_resp(mock_429), _make_client_cm_for_resp(mock_200)])

    with patch("app.providers.httpx.AsyncClient", side_effect=lambda **kw: next(client_cms)):
        events = [e async for e in provider.stream_chat_with_tools("sys", [{"role": "user", "content": "go"}], [])]

    pinfo = [e for e in events if e.get("type") == "provider_info"]
    assert len(pinfo) == 1
    assert pinfo[0]["model"] == "fallback-model"
    assert pinfo[0]["fallback"] is True


def test_allowed_models_whitelist_blocks_disallowed_model(monkeypatch):
    """ALLOWED_MODELS env var prevents disallowed models from entering the fallback chain."""
    monkeypatch.setenv("ALLOWED_MODELS", "claude-3-5-sonnet,gpt-4")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from importlib import reload
    import app.providers as _prov
    reload(_prov)
    p = _prov.PlannerProvider(model="google/gemma-4-31b-it:free")
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        p._openrouter_models_to_try("google/gemma-4-31b-it:free")


def test_allowed_models_empty_allows_all(monkeypatch):
    """Unset ALLOWED_MODELS permits all models (backward compatible)."""
    monkeypatch.delenv("ALLOWED_MODELS", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from importlib import reload
    import app.providers as _prov
    reload(_prov)
    p = _prov.PlannerProvider(model="google/gemma-4-31b-it:free")
    result = p._openrouter_models_to_try("google/gemma-4-31b-it:free")
    assert "google/gemma-4-31b-it:free" in result


def test_allowed_models_permits_matching_model(monkeypatch):
    """Requests matching the allow-list succeed; non-matching fallbacks are stripped."""
    monkeypatch.setenv("ALLOWED_MODELS", "google/gemma-4-31b-it:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from importlib import reload
    import app.providers as _prov
    reload(_prov)
    p = _prov.PlannerProvider(model="google/gemma-4-31b-it:free")
    result = p._openrouter_models_to_try("google/gemma-4-31b-it:free")
    assert result == ["google/gemma-4-31b-it:free"]


def test_allowed_models_supports_glob_patterns(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", "google/gemma-*:free,meta-llama/*")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from importlib import reload
    import app.providers as _prov

    reload(_prov)
    p = _prov.PlannerProvider(model="google/gemma-4-31b-it:free")
    result = p._openrouter_models_to_try("google/gemma-4-31b-it:free")
    assert "google/gemma-4-31b-it:free" in result
    assert "google/gemma-4-26b-a4b-it:free" in result
    assert "meta-llama/llama-3.3-70b-instruct:free" in result
    assert "nvidia/nemotron-3-super-120b-a12b:free" not in result
