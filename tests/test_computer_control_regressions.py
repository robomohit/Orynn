import asyncio
import sys
import types
import json
from pathlib import Path

import pytest
import httpx

from app.agent import AgentService
from app.agent import (
    _desktop_control_profile,
    _desktop_control_profile_status,
    _desktop_control_profile_text,
)
from app.agent import _overlay_for_action_start
from app.log_emitter import LogEmitter
from app.models import Action, ActionType, HierarchicalPlan, SubTask, ToolResult
from app.providers import PlannerProvider, detect_task_mode, infer_isolated_app_name


class DummyLogEmitter:
    async def emit(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_isolated_mode_waits_for_target_window_instead_of_falling_back(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "complex")
    monkeypatch.setattr("app.agent._get_hwnd_for_title", lambda title: None)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda sw, sh: "fake-shot")
    monkeypatch.setattr("app.agent._get_active_window_rect", lambda sw, sh: None)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        types.SimpleNamespace(
            GetForegroundWindow=lambda: 0,
            IsWindowVisible=lambda hwnd: False,
            GetWindowText=lambda hwnd: "",
        ),
    )

    class FakeProvider:
        total_tokens = 0
        first_message = None

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.first_message = messages[0]["content"]
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def capture_reasoning(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", capture_reasoning)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-1", "Open Notepad", mode="computer_isolated", isolated_app="Notepad")

    mode_events = [data for event, data in events if event == "mode"]
    status_messages = [data.get("message", "") for event, data in events if event == "status"]
    assert "Target window: Notepad" in provider.first_message
    assert any(event["mode"] == "computer_isolated" and event["isolated"] is True and event.get("isolated_pending") is True for event in mode_events)
    assert any("Waiting to attach isolated control to 'Notepad'" in message for message in status_messages)


@pytest.mark.asyncio
async def test_isolated_mode_passes_app_title_to_tool_executor(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent._get_hwnd_for_title", lambda title: 1234)
    monkeypatch.setattr("app.providers._capture_hwnd_screenshot_b64", lambda hwnd: "fake-shot")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    captured = {}

    def fake_set_isolated_hwnd(hwnd, app_title=None):
        captured["hwnd"] = hwnd
        captured["app_title"] = app_title

    monkeypatch.setattr(service.tools, "set_isolated_hwnd", fake_set_isolated_hwnd)

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-2", "Open Notepad", mode="computer_isolated", isolated_app="Notepad")

    assert captured == {"hwnd": 1234, "app_title": "Notepad"}


@pytest.mark.asyncio
async def test_desktop_control_profile_is_injected_before_first_model_turn(monkeypatch, workspace):
    import app.widget.desktop_features as df

    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent._get_hwnd_for_title", lambda title: 2222)
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(df, "app_window_rect",
                        lambda app: {"left": 10, "top": 20, "width": 900, "height": 700})
    monkeypatch.setattr(df, "survey_app_controls",
                        lambda app, cap=90: {"count": 3, "controls": ["Send", "Search"]})
    monkeypatch.setattr(df, "ocr_available", lambda: True)
    monkeypatch.setattr(df, "electron_hint_for_app",
                        lambda app: {"exe": r"C:\\Discord\\Discord.exe",
                                     "tip": "Discord is an Electron app - unlock it."})
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0
        model = "tier:uia"
        first_message = ""

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.first_message = messages[0]["content"]
            assert screenshot_b64 is None
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task(
        "task-control-profile",
        "Open Discord and read the latest message",
        mode="computer_isolated",
        isolated_app="Discord",
        model="tier:uia",
    )

    assert "Desktop control readiness:" in provider.first_message
    assert "Primary route: Electron unlock" in provider.first_message
    assert "UIA controls visible now: 3" in provider.first_message
    assert "OCR fallback: available" in provider.first_message
    assert "electron_unlock" in provider.first_message
    profile_events = [data for event, data in events if event == "control_profile"]
    assert profile_events
    assert profile_events[-1]["primary_route"] == "Electron unlock"
    assert profile_events[-1]["electron_hint"]["exe"].endswith("Discord.exe")


def test_full_desktop_control_profile_surveys_foreground_window(monkeypatch):
    import app.widget.desktop_features as df

    rect_calls = []
    survey_calls = []

    def fake_rect(app, **kwargs):
        rect_calls.append((app, kwargs))
        return {"left": 20, "top": 30, "width": 640, "height": 480}

    def fake_survey(app, cap=90, **kwargs):
        survey_calls.append((app, cap, kwargs))
        return {"count": 4, "controls": ["File", "Edit", "Search"]}

    monkeypatch.setattr(df, "app_window_rect", fake_rect)
    monkeypatch.setattr(df, "survey_app_controls", fake_survey)
    monkeypatch.setattr(df, "foreground_window_info", lambda: {
        "title": "Untitled - Notepad",
        "exe": r"C:\Windows\System32\notepad.exe",
        "hwnd": 77,
    })
    monkeypatch.setattr(df, "ocr_available", lambda: True)
    monkeypatch.setattr(df, "electron_hint_for_app", lambda app: None)

    profile = _desktop_control_profile("", isolated=False, model_sees=False)

    assert rect_calls == [("", {"fallback_foreground": True})]
    assert survey_calls == [("", 90, {"fallback_foreground": True})]
    assert profile["window_found"] is True
    assert profile["app_rect"] == {"left": 20, "top": 30, "width": 640, "height": 480}
    assert profile["uia_control_count"] == 4
    assert profile["controls"] == ["File", "Edit", "Search"]
    assert profile["primary_route"] == "UIA exact"
    assert profile["foreground_window"]["title"] == "Untitled - Notepad"
    assert "Target app: Untitled - Notepad" in _desktop_control_profile_text(profile)
    assert "Foreground window: Untitled - Notepad" in _desktop_control_profile_text(profile)
    assert "for Untitled - Notepad" in _desktop_control_profile_status(profile)


@pytest.mark.asyncio
async def test_force_close_window_requires_approval_even_when_computer_auto_approves(
    monkeypatch, workspace
):
    import app.widget.desktop_features as df

    service = AgentService(workspace, log_emitter=DummyLogEmitter())
    events = []
    finalizations = []

    monkeypatch.setattr(df, "ocr_available", lambda: False)
    monkeypatch.setattr(df, "electron_hint_for_app", lambda app: None)
    monkeypatch.setattr(df, "foreground_window_info", lambda: {})
    monkeypatch.setattr(
        df,
        "app_window_rect",
        lambda app, *, fallback_foreground=False: {
            "left": 0,
            "top": 0,
            "width": 300,
            "height": 200,
        },
    )
    monkeypatch.setattr(
        df,
        "survey_app_controls",
        lambda app, cap=90, fallback_foreground=False: {"count": 0, "controls": []},
    )
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: True)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda sw, sh: "vision-shot")
    monkeypatch.setattr("app.agent._get_active_window_rect", lambda sw, sh: None)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        model = "vision-test"
        total_tokens = 0

        async def stream_chat_with_tools(
            self, system, messages, tools, screenshot_b64=None
        ):
            yield {
                "type": "tool_call",
                "id": "close-1",
                "name": "force_close_window",
                "args": {"title": "Notepad", "force": True},
                "thought": "close it",
            }

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "approval_required":
            service.submit_approval(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("force_close_window executed before approval")

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(
        service, "_finalize", lambda *args, **kwargs: finalizations.append(args)
    )
    monkeypatch.setattr(service.tools, "run_action", fail_run_action)

    await service.run_task(
        "task-force-close-approval",
        "Close Notepad",
        mode="computer",
        model="vision-test",
    )

    approval_events = [data for event, data in events if event == "approval_required"]
    assert approval_events
    assert approval_events[0]["action"]["type"] == "force_close_window"
    assert approval_events[0]["danger"] == "high"
    assert "terminates" in approval_events[0]["reason"]
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Action rejected" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_mcp_tool_requires_approval_even_when_coding_auto_approves(
    monkeypatch, workspace
):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())
    events = []
    finalizations = []

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        model = "coding-test"
        total_tokens = 0

        async def stream_chat_with_tools(
            self, system, messages, tools, screenshot_b64=None
        ):
            yield {
                "type": "tool_call",
                "id": "mcp-1",
                "name": "mcp_tool",
                "args": {
                    "server_name": "notes",
                    "tool_name": "delete_note",
                    "tool_args": {"id": "abc"},
                },
                "thought": "use integration",
            }

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "approval_required":
            service.submit_approval(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("mcp_tool executed before approval")

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(
        service, "_finalize", lambda *args, **kwargs: finalizations.append(args)
    )
    monkeypatch.setattr(service.tools, "run_action", fail_run_action)

    await service.run_task(
        "task-mcp-approval",
        "Delete the note via MCP",
        mode="coding",
        model="coding-test",
    )

    approval_events = [data for event, data in events if event == "approval_required"]
    permission_events = [data for event, data in events if event == "permission_required"]
    assert approval_events
    assert approval_events[0]["action"]["type"] == "mcp_tool"
    assert approval_events[0]["danger"] == "high"
    assert "notes.delete_note" in approval_events[0]["reason"]
    assert permission_events == []
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Action rejected" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_mcp_discovery_requires_approval_before_starting_servers(
    monkeypatch, workspace
):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())
    events = []
    finalizations = []

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        model = "coding-test"
        total_tokens = 0

        async def stream_chat_with_tools(
            self, system, messages, tools, screenshot_b64=None
        ):
            yield {
                "type": "tool_call",
                "id": "mcp-list-1",
                "name": "list_mcp_tools",
                "args": {"server_name": "notes"},
                "thought": "inspect integration tools",
            }

    async def capture_emit(task_id, event, data):
        events.append((event, data))
        if event == "approval_required":
            service.submit_approval(task_id, data["action_id"], False)

    async def noop_reasoning(*args, **kwargs):
        return None

    async def fail_run_action(*args, **kwargs):
        raise AssertionError("list_mcp_tools executed before approval")

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(
        service, "_finalize", lambda *args, **kwargs: finalizations.append(args)
    )
    monkeypatch.setattr(service.tools, "run_action", fail_run_action)

    await service.run_task(
        "task-mcp-discovery-approval",
        "List the available MCP tools",
        mode="coding",
        model="coding-test",
    )

    approval_events = [data for event, data in events if event == "approval_required"]
    permission_events = [data for event, data in events if event == "permission_required"]
    assert approval_events
    assert approval_events[0]["action"]["type"] == "list_mcp_tools"
    assert approval_events[0]["danger"] == "high"
    assert "configured MCP server processes" in approval_events[0]["reason"]
    assert permission_events == []
    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "Action rejected" in finalizations[-1][2]


@pytest.mark.asyncio
async def test_text_only_desktop_tool_schemas_hide_visual_fallback_tools(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0
        model = "tier:uia"
        tool_names = set()

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            assert screenshot_b64 is None
            self.tool_names = {tool["function"]["name"] for tool in tools}
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-text-only-tools", "Use Calculator", mode="computer", model="tier:uia")

    assert {"uia_find", "uia_click", "uia_type", "uia_wait", "electron_unlock"} <= provider.tool_names
    assert {"focus_window", "wait_for_window", "finish"} <= provider.tool_names
    assert {
        "screenshot",
        "mouse_click",
        "keyboard_type",
        "computer",
        "pixel_color_at",
        "ui_critique",
        "screen_context",
    }.isdisjoint(provider.tool_names)


@pytest.mark.asyncio
async def test_text_only_desktop_screen_context_stays_for_explicit_screen_goal(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0
        model = "tier:uia"
        tool_names = set()

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            assert screenshot_b64 is None
            self.tool_names = {tool["function"]["name"] for tool in tools}
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-screen-context-tools", "Look at my screen and explain what is open", mode="computer", model="tier:uia")

    assert "screen_context" in provider.tool_names
    assert {"uia_find", "uia_wait", "finish"} <= provider.tool_names
    assert {"screenshot", "mouse_click", "computer", "pixel_color_at", "ui_critique"}.isdisjoint(provider.tool_names)


@pytest.mark.asyncio
async def test_vision_desktop_tool_schemas_keep_visual_fallback_tools(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: True)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda sw, sh: "vision-shot")
    monkeypatch.setattr("app.agent._get_active_window_rect", lambda sw, sh: None)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0
        model = "openrouter/openai/gpt-4o"
        tool_names = set()
        screenshots_seen = []

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.screenshots_seen.append(screenshot_b64)
            self.tool_names = {tool["function"]["name"] for tool in tools}
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "done"}, "thought": ""}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-vision-tools", "Click the highlighted button", mode="computer", model="openrouter/openai/gpt-4o")

    assert provider.screenshots_seen == ["vision-shot"]
    assert {"screenshot", "mouse_click", "keyboard_type", "computer"} <= provider.tool_names
    assert {"uia_find", "uia_click", "electron_unlock"} <= provider.tool_names


@pytest.mark.asyncio
async def test_screen_context_emits_screenshot_and_updates_vision_context(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: True)
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda sw, sh: "initial-shot")
    monkeypatch.setattr("app.agent._get_active_window_rect", lambda sw, sh: None)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0
        model = "openrouter/openai/gpt-4o"

        def __init__(self):
            self.turn = 0
            self.screenshots_seen = []
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            self.screenshots_seen.append(screenshot_b64)
            if self.turn == 1:
                yield {"type": "tool_call", "id": "call-1", "name": "screen_context", "args": {}, "thought": "inspect"}
                return
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        if action.type == ActionType.screen_context:
            return ToolResult(ok=True, output="Screen captured.\n\nExtracted text from screen:\nReady", base64_image="context-shot")
        return ToolResult(ok=True, output=action.args.get("reason", "ok"))

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))
        if event_type == "permission_required":
            service.submit_permission(task_id, data["action_id"], True)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await asyncio.wait_for(
        service.run_task(
            "task-screen-context-vision",
            "Look at my screen and explain what is open",
            mode="computer",
            model="openrouter/openai/gpt-4o",
        ),
        timeout=10,
    )

    assert provider.screenshots_seen == ["initial-shot", "context-shot"]
    permission_events = [data for event, data in events if event == "permission_required"]
    assert permission_events
    assert permission_events[0]["scope"] == "screen"
    screenshots = [data["data"] for event, data in events if event == "screenshot"]
    assert "initial-shot" in screenshots
    assert "context-shot" in screenshots


@pytest.mark.asyncio
async def test_text_only_desktop_tool_schema_omits_visual_fallback_guidance(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        model = "tier:uia"

        def __init__(self):
            self._total_input_tokens = 0
            self._total_output_tokens = 0
            self.tool_names = []

        @property
        def total_tokens(self):
            return self._total_input_tokens + self._total_output_tokens

        def _call_llm(self, *args, **kwargs):
            raise AssertionError("desktop route must not make an upfront planning call")

        def plan_hierarchical(self, *args, **kwargs):
            raise AssertionError("desktop route must not call the hierarchical planner upfront")

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            assert screenshot_b64 is None
            self.tool_names = [tool["function"]["name"] for tool in tools]
            yield {"type": "tool_call", "id": "call-finish", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-atomic-uia-prompt", "Use Discord", mode="computer", model="tier:uia")

    assert "make_subtasks" in provider.tool_names
    assert "uia_find" in provider.tool_names
    assert "electron_unlock" in provider.tool_names
    assert "screenshot" not in provider.tool_names
    assert "mouse_click" not in provider.tool_names
    assert "computer" not in provider.tool_names
    assert "pixel_color_at" not in provider.tool_names
    assert "ui_critique" not in provider.tool_names
    assert "screen_context" not in provider.tool_names


def test_providers_module_exposes_asyncio():
    import app.providers as providers

    assert providers.asyncio is not None


def test_single_app_desktop_goal_auto_selects_isolated_mode():
    assert infer_isolated_app_name("Open Notepad and write hello") == "Notepad"
    assert detect_task_mode("Open Notepad and write hello") == "computer_isolated"
    assert detect_task_mode("Open the Start menu and click the Settings button") == "computer"


def test_control_layer_is_declared_on_start_overlays():
    uia_overlay = _overlay_for_action_start(
        Action(id="a1", type=ActionType.uia_click, args={"query": "Send", "app": "Discord"})
    )
    assert uia_overlay["control_layer"] == "UIA exact"

    pixel_overlay = _overlay_for_action_start(
        Action(id="a2", type=ActionType.mouse_click, args={"x": 20, "y": 30})
    )
    assert pixel_overlay["control_layer"] == "Screenshot fallback"

    electron_overlay = _overlay_for_action_start(
        Action(id="a3", type=ActionType.electron_unlock, args={"exe": "Discord"})
    )
    assert electron_overlay["control_layer"] == "Electron unlock"

    sequence_overlay = _overlay_for_action_start(
        Action(
            id="a4",
            type=ActionType.uia_click_sequence,
            args={"targets": ["One", "Two", "Plus", "Three", "Equals"], "app": "Calculator"},
        )
    )
    assert sequence_overlay["control_layer"] == "UIA exact"
    assert sequence_overlay["kind"] == "click"
    assert sequence_overlay["label"] == "Clicking 5 controls in sequence"
    assert sequence_overlay["target"] == "One, Two, Plus, Three, +1 more"


@pytest.mark.asyncio
async def test_reactive_desktop_finish_finalizes_without_reflection(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "complex")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        model = "tier:uia"

        def __init__(self):
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        @property
        def total_tokens(self):
            return self._total_input_tokens + self._total_output_tokens

        def plan_hierarchical(self, *args, **kwargs):
            raise AssertionError("desktop route must not call hierarchical planning upfront")

        def reflect_on_subtask(self, *args, **kwargs):
            raise AssertionError("finish should not enter reflection")

        def evaluate(self, *args, **kwargs):
            raise AssertionError("finish should not enter evaluation")

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            yield {
                "type": "tool_call",
                "id": "finish-call",
                "name": "finish",
                "args": {"reason": "Desktop task is complete."},
                "thought": "Finish after verification",
            }

    finalizations = []

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("task-reactive-finish", "Take a screenshot and finish", mode="computer")

    assert finalizations
    assert finalizations[-1][1] == "done"
    assert finalizations[-1][2] == "Desktop task is complete."


@pytest.mark.asyncio
async def test_desktop_action_emits_post_screenshot_and_no_effect_hint(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    capture_calls = {"count": 0}

    def fake_capture(sw, sh):
        capture_calls["count"] += 1
        return "initial-shot" if capture_calls["count"] == 1 else "after-shot"

    monkeypatch.setattr("app.agent._capture_screenshot_b64", fake_capture)
    monkeypatch.setattr("app.agent._post_action_no_effect_hint", lambda before, after: "[no-effect hint] unchanged")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: True)

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self.last_observation = ""
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {"type": "tool_call", "id": "call-1", "name": "mouse_click", "args": {"x": 50, "y": 60}, "thought": "click it"}
                return
            self.last_observation = messages[-1]["content"]
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        if action.type == ActionType.mouse_click:
            return ToolResult(ok=True, output="Clicked", base64_image=None, data=None)
        return ToolResult(ok=True, output="done", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-desktop-no-effect", "Click and verify", mode="computer")

    assert any(event == "screenshot" and data["data"] == "after-shot" for event, data in events)
    assert "[no-effect hint] unchanged" in provider.last_observation


@pytest.mark.asyncio
async def test_text_only_desktop_model_skips_automatic_screenshots(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)

    capture_calls = {"count": 0}

    def fake_capture(sw, sh):
        capture_calls["count"] += 1
        return "unexpected-shot"

    monkeypatch.setattr("app.agent._capture_screenshot_b64", fake_capture)

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self.screenshots_seen = []
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            self.screenshots_seen.append(screenshot_b64)
            if self.turn == 1:
                yield {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "mouse_click",
                    "args": {"x": 50, "y": 60},
                    "thought": "click it",
                }
                return
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        return ToolResult(ok=True, output="ok", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-desktop-text-only", "Click and verify", mode="computer", model="tier:uia")

    assert capture_calls["count"] == 0
    assert provider.screenshots_seen == [None, None]


@pytest.mark.asyncio
async def test_text_only_desktop_route_guards_premature_pixel_click(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self.last_observation = ""
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "mouse_click",
                    "args": {"x": 50, "y": 60},
                    "thought": "click it",
                }
                return
            self.last_observation = messages[-1]["content"]
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    calls = []

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        calls.append(action.type)
        return ToolResult(ok=True, output="ok", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-desktop-guard", "Click and verify", mode="computer", model="tier:uia")

    assert calls == [ActionType.finish]
    assert "[control-route guard]" in provider.last_observation
    guarded = [data for event, data in events if event == "action_result" and data["action_type"] == "mouse_click"]
    assert guarded
    assert guarded[-1]["ok"] is False
    assert guarded[-1]["overlay"]["control_layer"] == "UIA guard"
    assert guarded[-1]["overlay"]["fallback_reason"] == "premature_visual_action"


@pytest.mark.asyncio
async def test_text_only_desktop_route_guards_unadvertised_screen_context(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self.last_observation = ""
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {"type": "tool_call", "id": "call-1", "name": "screen_context", "args": {}, "thought": "look"}
                return
            self.last_observation = messages[-1]["content"]
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    calls = []

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        calls.append(action.type)
        return ToolResult(ok=True, output="ok", base64_image="unexpected-shot")

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-screen-context-guard", "Use Calculator", mode="computer", model="tier:uia")

    assert calls == [ActionType.finish]
    assert "[control-route guard]" in provider.last_observation
    guarded = [data for event, data in events if event == "action_result" and data["action_type"] == "screen_context"]
    assert guarded
    assert guarded[-1]["ok"] is False
    assert guarded[-1]["overlay"]["control_layer"] == "UIA guard"


@pytest.mark.asyncio
async def test_isolated_hung_app_hint_is_added_to_observation(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent._get_hwnd_for_title", lambda title: 1234)
    monkeypatch.setattr("app.providers._capture_hwnd_screenshot_b64", lambda hwnd: "isolated-shot")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr(service.tools, "current_target_hung_info", lambda: {"title": "Untitled - Notepad", "pid": 4242})

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self.last_observation = ""
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {"type": "tool_call", "id": "call-1", "name": "mouse_click", "args": {"x": 40, "y": 30}, "thought": "click"}
                return
            self.last_observation = messages[-1]["content"]
            yield {"type": "tool_call", "id": "call-2", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        if action.type == ActionType.mouse_click:
            return ToolResult(ok=True, output="Clicked", base64_image=None, data=None)
        return ToolResult(ok=True, output="done", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-isolated-hung", "Use notepad", mode="computer_isolated", isolated_app="Notepad")

    assert "force_close_window" in provider.last_observation
    assert "Untitled - Notepad" in provider.last_observation


def test_persistent_logs_omit_raw_screenshot_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    emitter = LogEmitter()
    screenshot_b64 = "a" * 10_000

    emitter.emit("task-log", "screenshot", {"data": screenshot_b64, "isolated": False})
    emitter.flush()  # wait for background write before reading

    events = emitter.read_log("task-log")
    assert len(events) == 1
    assert events[0]["data"] == "[omitted from persistent log]"
    assert events[0]["data_omitted"] is True
    assert events[0]["data_chars"] == len(screenshot_b64)


@pytest.mark.asyncio
async def test_native_tool_stream_timeout_falls_back_to_xml(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.MODEL_STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.native_calls = 0
            self.xml_calls = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.native_calls += 1
            await asyncio.sleep(1)
            yield {"type": "tool_call", "id": "call-1", "name": "finish", "args": {"reason": "native"}, "thought": ""}

        async def stream_chat(self, system, messages, screenshot_b64=None):
            self.xml_calls += 1
            yield "Done via XML fallback."

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    finalizations = []

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("task-timeout-fallback", "Say hello", mode="coding")

    assert provider.native_calls == 1
    assert provider.xml_calls == 1
    assert finalizations
    assert finalizations[-1][1] == "done"
    assert "Done via XML fallback." in finalizations[-1][2]


@pytest.mark.asyncio
async def test_xml_stream_timeout_fails_instead_of_hanging(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.MODEL_STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            raise RuntimeError("native unavailable")
            yield

        async def stream_chat(self, system, messages, screenshot_b64=None):
            await asyncio.sleep(1)
            yield "This should never arrive."

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())

    finalizations = []

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("task-timeout-fail", "Say hello", mode="coding")

    assert finalizations
    assert finalizations[-1][1] == "failed"
    assert "Timed out waiting for XML response from model." in finalizations[-1][2]


@pytest.mark.asyncio
async def test_xml_fallback_caps_recovery_steps(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            raise RuntimeError("native unavailable")
            yield

        async def stream_chat(self, system, messages, screenshot_b64=None):
            yield '<thought>retry</thought><action type="bogus_tool">{}</action>'

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())

    finalizations = []

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", noop_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("task-xml-cap", "Say hello", mode="coding")

    assert finalizations
    assert finalizations[-1][1] == "failed"
    assert "XML fallback exhausted its max recovery steps." in finalizations[-1][2]


@pytest.mark.asyncio
async def test_openrouter_stream_chat_falls_back_to_second_model_on_429(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    provider = PlannerProvider(model="openrouter/google/gemma-4-31b-it:free")

    attempted_models = []

    class FakeStreamResponse:
        def __init__(self, status_code, lines):
            self.status_code = status_code
            self._lines = lines
            self.request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status {self.status_code}",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class FakeStreamContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeAsyncClient:
        def __init__(self, timeout=300):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, json=None):
            payload = json
            attempted_models.append(payload["model"])
            if payload["model"] == "google/gemma-4-31b-it:free":
                return FakeStreamContext(FakeStreamResponse(429, []))

            lines = [
                f"data: {__import__('json').dumps({'choices': [{'delta': {'content': 'fallback ok'}}]})}",
                "data: [DONE]",
            ]
            return FakeStreamContext(FakeStreamResponse(200, lines))

    async def fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.providers.asyncio.sleep", fast_sleep)

    chunks = []
    async for chunk in provider.stream_chat(
        "system",
        [{"role": "user", "content": "hello"}],
    ):
        chunks.append(chunk)

    assert attempted_models == [
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
    ]
    assert "".join(chunks) == "fallback ok"


@pytest.mark.asyncio
async def test_xml_stream_normalizes_tool_history_for_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    provider = PlannerProvider(model="openrouter/google/gemma-4-26b-a4b-it:free")

    captured_payloads = []

    class FakeStreamResponse:
        def __init__(self, status_code, lines):
            self.status_code = status_code
            self._lines = lines
            self.request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status {self.status_code}",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class FakeStreamContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeAsyncClient:
        def __init__(self, timeout=300):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, json=None):
            captured_payloads.append(json)
            lines = [
                f"data: {__import__('json').dumps({'choices': [{'delta': {'content': 'xml ok'}}]})}",
                "data: [DONE]",
            ]
            return FakeStreamContext(FakeStreamResponse(200, lines))

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeAsyncClient)

    messages = [
        {"role": "assistant", "content": "I will open it.", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "browser_open", "arguments": "{\"url\":\"http://127.0.0.1:8000\"}"}}
        ]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Opened: http://127.0.0.1:8000/ | Title: Orynn · Stream"},
        {"role": "user", "content": "Continue from here."},
    ]

    chunks = []
    async for chunk in provider.stream_chat("system", messages):
        chunks.append(chunk)

    assert "".join(chunks) == "xml ok"
    assert captured_payloads, "Expected stream_chat to make a request"
    sent_messages = captured_payloads[0]["messages"]
    assert sent_messages[1]["role"] == "assistant"
    assert "browser_open" in sent_messages[1]["content"][0]["text"]
    assert sent_messages[2]["role"] == "user"
    assert "<observation>" in sent_messages[2]["content"][0]["text"]


def test_log_emitter_rejects_path_like_task_ids_and_cleans_queues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    emitter = LogEmitter()
    q = emitter.subscribe("safe-task")

    emitter.emit("safe-task", "status", {"message": "running"})
    assert "safe-task" in emitter._queues

    emitter.unsubscribe("safe-task", q)
    assert "safe-task" not in emitter._queues

    with pytest.raises(ValueError):
        emitter.emit("../escape", "status", {"message": "bad"})
    assert not (Path("workspace/logs") / "escape.jsonl").exists()


def test_sse_subscriber_queue_is_bounded(tmp_path, monkeypatch):
    """subscribe() queue has a maxsize; excess events are dropped without exception."""
    monkeypatch.chdir(tmp_path)
    emitter = LogEmitter()
    q = emitter.subscribe("flood-task")

    assert q.maxsize > 0, "Queue must be bounded"
    cap = q.maxsize

    for i in range(cap):
        emitter.emit("flood-task", "status", {"message": f"e{i}"})
    emitter.flush()
    assert q.qsize() == cap

    # One more emit past the cap must not raise — QueueFull is caught internally
    emitter.emit("flood-task", "status", {"message": "overflow"})
    emitter.flush()
    assert q.qsize() == cap  # overflow event was dropped, size unchanged


@pytest.mark.asyncio
async def test_killed_task_finalizes_as_cancelled_not_max_steps(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.AGENT_MAX_STEPS", 1)
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    class FakeProvider:
        total_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            service.kill_task("task-kill")
            yield {"type": "tool_call", "id": "call-1", "name": "bogus_tool", "args": {}, "thought": ""}

        async def stream_chat(self, system, messages, screenshot_b64=None):
            service.kill_task("task-kill")
            yield '<thought>stop</thought><action type="bogus_tool">{}</action>'

    finalizations = []
    events = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))

    async def noop_reasoning(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_reasoning)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("task-kill", "Keep working", mode="coding")

    assert finalizations
    assert finalizations[-1][1] == "cancelled"
    assert "killed" in finalizations[-1][2].lower()
    assert any(event == "cancelled" for event, _ in events)


def test_static_ui_avoids_innerhtml_for_untrusted_dynamic_sections():
    static = Path("static")
    parts = [(static / "index.html").read_text(encoding="utf-8")]
    for name in ("style.css", "app.js"):
        p = static / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    html = "\n".join(parts)

    assert "row.querySelector('.detail-title').innerHTML" not in html
    assert "worker-tag worker-${workerNum}\">${event.worker_id}" not in html
    assert "grid.innerHTML = allSkills.map" not in html
    assert "grid.innerHTML = allMCPServers.map" not in html
    assert "toolsContainer.innerHTML = server.tools.map" not in html
