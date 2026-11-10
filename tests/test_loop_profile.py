"""Strong models get a roomier agent loop: more steps, a larger token budget,
full observations and an untruncated history (see app/loop_profile.py)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import AgentService
from app.loop_profile import DEFAULT, STRONG, profile_for


def test_profile_selection(monkeypatch):
    for var in ("ORYNN_LOOP_PROFILE", "AGENT_MAX_STEPS", "DESKTOP_MAX_STEPS", "BROWSER_MAX_STEPS", "ORYNN_TOKEN_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    assert profile_for("claude-opus-5-5") is STRONG
    assert profile_for("claude-fable-5-2") is STRONG
    assert profile_for("openrouter/openai/gpt-oss-120b:free") is DEFAULT
    assert profile_for("claude-3-5-sonnet-20241022") is DEFAULT
    assert profile_for("") is DEFAULT

    monkeypatch.setenv("ORYNN_LOOP_PROFILE", "strong")
    assert profile_for("openrouter/openai/gpt-5") .strong
    monkeypatch.setenv("ORYNN_LOOP_PROFILE", "default")
    assert not profile_for("claude-opus-5-5").strong


def test_explicit_env_limits_win_over_the_profile(monkeypatch):
    monkeypatch.delenv("ORYNN_LOOP_PROFILE", raising=False)
    monkeypatch.setenv("DESKTOP_MAX_STEPS", "12")
    monkeypatch.setenv("ORYNN_TOKEN_BUDGET", "5000")
    p = profile_for("claude-opus-5-5")
    assert (p.desktop_steps, p.token_budget) == (12, 5000)
    assert p.agent_steps == STRONG.agent_steps and p.strong


async def _run(monkeypatch, workspace, model, steps):
    """Run a coding task whose model reads a big file `steps` times, then finishes."""
    big = "x" * 9000 + "END-OF-FILE"
    (workspace / "big.txt").write_text(big, encoding="utf-8")
    service = AgentService(workspace, log_emitter=SimpleNamespace(emit=lambda *a, **k: None))
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    seen = {"calls": 0, "last_messages": []}

    class FakeProvider:
        def __init__(self):
            self.model = model
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        @property
        def total_tokens(self):
            return self._total_input_tokens + self._total_output_tokens

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            seen["calls"] += 1
            seen["last_messages"] = [dict(m) for m in messages]
            n = seen["calls"]
            if n > steps:
                yield {"type": "tool_call", "id": f"c{n}", "name": "finish", "args": {"reason": "done"}, "thought": "done"}
            else:
                # A distinct line range each time so the duplicate-call guard stays out of the way.
                yield {"type": "tool_call", "id": f"c{n}", "name": "read_file",
                       "args": {"path": "big.txt", "start_line": n}, "thought": f"read {n}"}

    events = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))

    async def noop(*a, **k):
        return None

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop)
    await service.run_task(
        f"task-{model}", "Read big.txt repeatedly", mode="coding",
        environment={"workspace": str(workspace), "home": str(workspace.parent), "project_folder_selected": True},
        project_folder=str(workspace),
    )
    done = [d for e, d in events if e == "done"]
    return seen, (done[-1] if done else {})


@pytest.mark.asyncio
async def test_strong_model_runs_past_the_default_step_cap_with_full_history(monkeypatch, workspace):
    for var in ("ORYNN_LOOP_PROFILE", "AGENT_MAX_STEPS", "ORYNN_TOKEN_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    seen, done = await _run(monkeypatch, workspace, "claude-opus-5-5", steps=30)

    assert done.get("complete"), done
    assert seen["calls"] == 31
    tool_results = [m["content"] for m in seen["last_messages"] if m.get("role") == "tool"]
    assert len(tool_results) == 30                       # nothing dropped
    assert all("END-OF-FILE" in r for r in tool_results)  # nothing clipped or truncated


@pytest.mark.asyncio
async def test_default_model_keeps_the_tight_limits(monkeypatch, workspace):
    for var in ("ORYNN_LOOP_PROFILE", "AGENT_MAX_STEPS", "ORYNN_TOKEN_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    seen, done = await _run(monkeypatch, workspace, "openrouter/openai/gpt-oss-120b:free", steps=30)

    assert not done.get("complete"), done
    assert seen["calls"] <= 25
    tool_results = [m["content"] for m in seen["last_messages"] if m.get("role") == "tool"]
    assert not any("END-OF-FILE" in r for r in tool_results)
