import asyncio
import base64
import pytest
from app.agent import AgentService
from app.models import Action, ActionDecision, ActionType, DangerLevel, ToolError
from app.log_emitter import log_emitter

@pytest.mark.asyncio
async def test_approval_flow(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    png = base64.b64encode(b"\x89PNGx").decode()
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda w, h: png)
    action = Action(id="a1", type=ActionType.mouse_click, args={"x": 1, "y": 2}, explanation="do")
    decision = ActionDecision(danger=DangerLevel.medium, reason="r", requires_approval=True)

    async def approve_later():
        await asyncio.sleep(0.01)
        s.submit_approval("task", "a1", True)

    t = asyncio.create_task(approve_later())
    out = await s._wait_for_approval("task", "a1")
    await t
    assert out is True

@pytest.mark.asyncio
async def test_approval_denial(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda w, h: "x")
    action = Action(id="a2", type=ActionType.mouse_click, args={"x": 1, "y": 2})
    decision = ActionDecision(danger=DangerLevel.medium, reason="r", requires_approval=True)

    async def deny_later():
        await asyncio.sleep(0.01)
        s.submit_approval("task", "a2", False)

    t = asyncio.create_task(deny_later())
    out = await s._wait_for_approval("task", "a2")
    await t
    assert out is False

@pytest.mark.asyncio
async def test_approval_timeout(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda w, h: "x")
    action = Action(id="a3", type=ActionType.mouse_click, args={"x": 1, "y": 2})
    decision = ActionDecision(danger=DangerLevel.medium, reason="r", requires_approval=True)
    # the function no longer takes timeout_seconds. It just waits forever.
    # We will cancel it to simulate timeout.
    t = asyncio.create_task(s._wait_for_approval("task", "a3"))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


@pytest.mark.asyncio
async def test_approval_response_after_prepare_is_not_lost(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)

    s._prepare_approval_wait("task", "a-fast")
    s.submit_approval("task", "a-fast", True, "edited plan")

    assert await s._wait_for_approval("task", "a-fast") is True
    assert s._approval_overrides["task:a-fast"] == "edited plan"


@pytest.mark.asyncio
async def test_finalize_clears_task_scoped_waiters(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)

    s._prepare_approval_wait("task", "a1")
    s._approval_overrides["task:a1"] = "plan"
    s._permission_waits["task:p1"] = asyncio.Future()
    s.permissions.grant("task", "shell")
    s.permissions.deny("task", "screen")
    s.permissions.grant("other", "shell")
    s._prepare_approval_wait("other", "a1")

    s._finalize("task", "cancelled", "done")

    assert not any(key.startswith("task:") for key in s._approvals)
    assert not any(key.startswith("task:") for key in s._approval_overrides)
    assert not any(key.startswith("task:") for key in s._permission_waits)
    assert s.permissions.granted_scopes("task") == []
    assert s.permissions.is_denied("task", "screen") is False
    assert s.permissions.is_granted("other", "shell") is True
    assert "other:a1" in s._approvals


@pytest.mark.asyncio
async def test_permission_gate_prompts_and_records_scope(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    action = Action(id="p1", type=ActionType.run_command, args={"command": "echo hello"})
    events = []

    async def capture_emit(task_id, event, data):
        events.append((task_id, event, data))

    s._emit = capture_emit
    wait = asyncio.create_task(
        s._ensure_permission_for_action(
            "task",
            action,
            args_summary="echo hello",
        )
    )
    for _ in range(20):
        if events:
            break
        await asyncio.sleep(0.01)

    assert events
    assert events[0][1] == "permission_required"
    assert events[0][2]["scope"] == "shell"
    assert events[0][2]["action_type"] == "run_command"

    s.submit_permission("task", "p1", True)
    granted, scope = await wait
    assert granted is True
    assert scope == "shell"
    assert s.permissions.is_granted("task", "shell") is True


def test_permission_store_deny_revokes_prior_grant(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    s.permissions.grant("task", "shell")
    s.permissions.deny("task", "shell")
    assert s.permissions.is_granted("task", "shell") is False
    assert s.permissions.is_denied("task", "shell") is True


@pytest.mark.asyncio
async def test_streaming_loop_enforces_permission_gate(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)

    class FakeProvider:
        total_tokens = 0

        async def stream_chat(self, *args, **kwargs):
            yield '<thought>Read the page</thought><action type="browser_get_text">{"selector":"body"}</action>'

    events = []
    finalizations = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "permission_required":
            s.submit_permission(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    monkeypatch.setattr(s, "_emit", capture_emit)
    monkeypatch.setattr(s, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(s, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await s.run_task(
        "perm-stream",
        "Read a browser page",
        FakeProvider(),
        mode="coding",
        autonomy_level="careful",
    )

    assert any(event == "permission_required" and data["scope"] == "browser" for event, data in events)
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Permission denied" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_streaming_loop_prompts_for_terminal_helper_permission(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, *args, **kwargs):
            yield {
                "type": "tool_call",
                "id": "tests-1",
                "name": "run_tests",
                "args": {"command": "pytest -q"},
                "thought": "verify",
            }

    events = []
    finalizations = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "approval_required":
            s.submit_approval(task_id, data["action_id"], True)
        if event == "permission_required":
            s.submit_permission(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("run_tests executed before shell permission")

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(s.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(s.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(s, "_emit", capture_emit)
    monkeypatch.setattr(s, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(s, "_finalize", lambda *args, **kwargs: finalizations.append(args))
    monkeypatch.setattr(s.tools, "run_action", fail_run_action)

    await s.run_task(
        "perm-run-tests",
        "Run tests",
        mode="coding",
        autonomy_level="careful",
    )

    assert any(
        event == "approval_required" and data["action"]["type"] == "run_tests"
        for event, data in events
    )
    assert any(
        event == "permission_required"
        and data["scope"] == "shell"
        and data["action_type"] == "run_tests"
        for event, data in events
    )
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Permission denied" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_streaming_loop_prompts_for_filesystem_read_permission(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    target = workspace / "secret.txt"
    target.write_text("private", encoding="utf-8")

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, *args, **kwargs):
            yield {
                "type": "tool_call",
                "id": "read-1",
                "name": "read_file",
                "args": {"path": str(target)},
                "thought": "read local file",
            }

    events = []
    finalizations = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "permission_required":
            s.submit_permission(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("read_file executed before filesystem permission")

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(s.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(s.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(s, "_emit", capture_emit)
    monkeypatch.setattr(s, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(s, "_finalize", lambda *args, **kwargs: finalizations.append(args))
    monkeypatch.setattr(s.tools, "run_action", fail_run_action)

    await s.run_task(
        "perm-read-file",
        "Read a file",
        mode="coding",
        autonomy_level="careful",
    )

    assert any(
        event == "permission_required"
        and data["scope"] == "filesystem"
        and data["action_type"] == "read_file"
        for event, data in events
    )
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Permission denied" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_streaming_loop_prompts_for_screen_context_permission(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, *args, **kwargs):
            yield {
                "type": "tool_call",
                "id": "screen-1",
                "name": "screen_context",
                "args": {},
                "thought": "inspect the screen",
            }

    events = []
    finalizations = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "permission_required":
            s.submit_permission(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("screen_context executed before screen permission")

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(s.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(s.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(s, "_emit", capture_emit)
    monkeypatch.setattr(s, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(s, "_finalize", lambda *args, **kwargs: finalizations.append(args))
    monkeypatch.setattr(s.tools, "run_action", fail_run_action)

    await s.run_task(
        "perm-screen-context",
        "Look at the screen",
        mode="coding",
        autonomy_level="careful",
    )

    assert any(
        event == "permission_required"
        and data["scope"] == "screen"
        and data["action_type"] == "screen_context"
        for event, data in events
    )
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Permission denied" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_screen_permission_is_not_auto_granted(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    action = Action(id="screen-auto", type=ActionType.screen_context, args={})
    events = []

    async def capture_emit(event, data):
        events.append((event, data))
        s.submit_permission("task", data["action_id"], False)

    granted, scope = await s._ensure_permission_for_action(
        "task",
        action,
        auto_grant=True,
        emit=capture_emit,
    )

    assert granted is False
    assert scope == "screen"
    assert events
    assert events[0][0] == "permission_required"
    assert events[0][1]["scope"] == "screen"
    assert s.permissions.is_granted("task", "screen") is False


@pytest.mark.asyncio
async def test_computer_screenshot_permission_is_not_auto_granted(workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    action = Action(id="computer-shot", type=ActionType.computer, args={"action": "screenshot"})
    events = []

    async def capture_emit(event, data):
        events.append((event, data))
        s.submit_permission("task", data["action_id"], False)

    granted, scope = await s._ensure_permission_for_action(
        "task",
        action,
        auto_grant=True,
        emit=capture_emit,
    )

    assert granted is False
    assert scope == "screen"
    assert events
    assert events[0][0] == "permission_required"
    assert events[0][1]["scope"] == "screen"
    assert events[0][1]["action_type"] == "computer"
    assert s.permissions.is_granted("task", "screen") is False
