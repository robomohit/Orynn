import asyncio
import time

import pytest

from app.agent import AgentService
from app.log_emitter import log_emitter
from app.models import ActionType, ToolResult


class MakeSubtasksProvider:
    model = "tier:uia"

    def __init__(self, make_subtasks_args):
        self.make_subtasks_args = make_subtasks_args
        self.turn = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    @property
    def total_tokens(self):
        return self._total_input_tokens + self._total_output_tokens

    def _call_llm(self, *args, **kwargs):
        raise AssertionError("make_subtasks must be optional, not an upfront planner call")

    def plan_hierarchical(self, *args, **kwargs):
        raise AssertionError("make_subtasks must be optional, not an upfront planner call")

    def reflect_on_subtask(self, *args, **kwargs):
        return {"success": True}

    async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
        turn = self.turn
        self.turn += 1
        if turn == 0:
            yield {
                "type": "tool_call",
                "id": "make-subtasks",
                "name": "make_subtasks",
                "args": self.make_subtasks_args,
                "thought": "This needs worker decomposition.",
            }
            return
        yield {
            "type": "tool_call",
            "id": "finish-call",
            "name": "finish",
            "args": {"reason": "done"},
            "thought": "Workers finished.",
        }


def _make_subtasks_args(*, execution_mode="serial", max_parallel_workers=1, subtasks=None):
    return {
        "execution_mode": execution_mode,
        "max_parallel_workers": max_parallel_workers,
        "subtasks": subtasks or [
            {
                "id": "s1",
                "description": "d1",
                "actions": [{"id": "a1", "type": "wait_action", "args": {"seconds": 0}}],
            }
        ],
    }


def _setup_desktop_run(monkeypatch, service, provider):
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])


@pytest.mark.asyncio
async def test_make_subtasks_success_runs_workers_and_finishes(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = MakeSubtasksProvider(_make_subtasks_args())
    _setup_desktop_run(monkeypatch, service, provider)

    ran_actions = []
    events = []
    finalizations = []

    async def fake_run_action(self, action, **kwargs):
        ran_actions.append(action.id)
        if action.type == ActionType.finish:
            return ToolResult(ok=True, output=action.args.get("reason", "done"))
        return ToolResult(ok=True, output=action.id)

    async def capture_emit(task_id, event, data):
        events.append(event)

    monkeypatch.setattr("app.tools.ToolExecutor.run_action", fake_run_action)
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("t1", "refactor", mode="computer")

    assert "a1" in ran_actions
    assert "plan" in events
    assert "subtask" in events
    assert finalizations[-1][1] == "done"


@pytest.mark.asyncio
async def test_make_subtasks_retry_uses_existing_worker_reflection(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = MakeSubtasksProvider(_make_subtasks_args())
    _setup_desktop_run(monkeypatch, service, provider)

    reflections = [
        {"success": False, "reason": "fail", "retry_actions": [{"type": "wait_action", "args": {"seconds": 0}}]},
        {"success": True},
    ]

    def mock_reflect(*args, **kwargs):
        return reflections.pop(0)

    provider.reflect_on_subtask = mock_reflect
    ran_waits = []

    async def fake_run_action(self, action, **kwargs):
        if action.type == ActionType.finish:
            return ToolResult(ok=True, output=action.args.get("reason", "done"))
        ran_waits.append(action.id)
        if len(ran_waits) == 1:
            return ToolResult(ok=False, output="failed once")
        return ToolResult(ok=True, output="retry ok")

    monkeypatch.setattr("app.tools.ToolExecutor.run_action", fake_run_action)

    await service.run_task("t2", "refactor", mode="computer")

    assert len(ran_waits) == 2
    assert not reflections


@pytest.mark.asyncio
async def test_phase_updates_emit_progress(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    seen = []

    async def capture(task_id, event, data):
        if event == "status":
            seen.append(data["message"])

    async def fast_sleep(_seconds):
        await original_sleep(0)

    original_sleep = __import__("asyncio").sleep
    monkeypatch.setattr(service, "_emit", capture)
    monkeypatch.setattr("app.agent.asyncio.sleep", fast_sleep)

    def slow_fn():
        time.sleep(0.03)
        return "ok"

    result = await service._run_with_phase_updates("task", "Thinking", "Still planning", slow_fn, heartbeat_seconds=0)
    assert result == "ok"
    assert any(msg == "Thinking" for msg in seen)
    assert any("Still planning" in msg for msg in seen)


@pytest.mark.asyncio
async def test_make_subtasks_parallel_plan_respects_dependencies(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = MakeSubtasksProvider(_make_subtasks_args(
        execution_mode="parallel",
        max_parallel_workers=2,
        subtasks=[
            {
                "id": "s2",
                "description": "second",
                "depends_on": ["s1"],
                "actions": [{"id": "a2", "type": "wait_action", "args": {"seconds": 0}}],
            },
            {
                "id": "s1",
                "description": "first",
                "actions": [{"id": "a1", "type": "wait_action", "args": {"seconds": 0}}],
            },
        ],
    ))
    _setup_desktop_run(monkeypatch, service, provider)

    order = []

    async def fake_run_action(self, action, **kwargs):
        if action.type == ActionType.wait_action:
            order.append(action.id)
        if action.type == ActionType.finish:
            return ToolResult(ok=True, output=action.args.get("reason", "done"))
        return ToolResult(ok=True, output=action.id)

    monkeypatch.setattr("app.tools.ToolExecutor.run_action", fake_run_action)

    await service.run_task("dep-plan", "refactor", mode="computer")

    assert order == ["a1", "a2"]


@pytest.mark.asyncio
async def test_make_subtasks_parallel_plan_runs_disjoint_write_scopes_concurrently(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = MakeSubtasksProvider(_make_subtasks_args(
        execution_mode="parallel",
        max_parallel_workers=2,
        subtasks=[
            {
                "id": "s1",
                "description": "left",
                "write_scope": ["src/a.py"],
                "actions": [{"id": "a1", "type": "wait_action", "args": {"seconds": 0}}],
            },
            {
                "id": "s2",
                "description": "right",
                "write_scope": ["src/b.py"],
                "actions": [{"id": "a2", "type": "wait_action", "args": {"seconds": 0}}],
            },
        ],
    ))
    _setup_desktop_run(monkeypatch, service, provider)

    peak_active = 0
    active = 0

    async def fake_run_action(self, action, **kwargs):
        nonlocal active, peak_active
        if action.type == ActionType.finish:
            return ToolResult(ok=True, output=action.args.get("reason", "done"))
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ToolResult(ok=True, output=action.id)

    monkeypatch.setattr("app.tools.ToolExecutor.run_action", fake_run_action)

    await service.run_task("parallel-plan", "refactor", mode="computer")

    assert peak_active >= 2


@pytest.mark.asyncio
async def test_make_subtasks_emits_usage_update(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = MakeSubtasksProvider(_make_subtasks_args())
    _setup_desktop_run(monkeypatch, service, provider)

    emitted_events = []

    async def capture_emit(task_id, event, data):
        emitted_events.append(event)

    async def fake_run_action(self, action, **kwargs):
        if action.type == ActionType.finish:
            return ToolResult(ok=True, output=action.args.get("reason", "done"))
        return ToolResult(ok=True, output=action.id)

    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr("app.tools.ToolExecutor.run_action", fake_run_action)

    await service.run_task("t-usage", "refactor", mode="computer")

    assert "usage_update" in emitted_events
