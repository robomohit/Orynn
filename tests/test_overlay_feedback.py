import pytest

from app.agent import AgentService
from app.log_emitter import LogEmitter
from app.models import ActionType, ToolResult
from app.tools import ToolExecutor


class DummyLogEmitter(LogEmitter):
    def __init__(self):
        pass

    def emit(self, *args, **kwargs):
        return None


def test_uia_find_returns_structured_exact_control_overlay(monkeypatch, tmp_path):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "find_ui_elements",
        lambda query, app, limit: {
            "ok": True,
            "items": [
                {
                    "name": "Text editor",
                    "automation_id": "",
                    "control_type": "EditControl",
                    "left": 11,
                    "top": 22,
                    "x": 161,
                    "y": 42,
                    "width": 300,
                    "height": 40,
                    "score": 100,
                    "offscreen": False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        desktop_features,
        "app_window_rect",
        lambda app: {"left": 5, "top": 10, "width": 500, "height": 400},
    )

    result = ToolExecutor(tmp_path, home_dir=tmp_path).uia_find("Text editor", "Notepad")

    assert result.ok is True
    assert "[uia:11,22,300,40]" in result.output
    overlay = result.data["overlay"]
    assert overlay["type"] == "uia_control"
    assert overlay["tool"] == "uia_find"
    assert overlay["rect"] == {"left": 11, "top": 22, "width": 300, "height": 40}
    assert overlay["app_rect"] == {"left": 5, "top": 10, "width": 500, "height": 400}


def test_uia_wait_returns_ready_overlay(monkeypatch, tmp_path):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "wait_for_ui_element",
        lambda query, app, timeout: {
            "ok": True,
            "name": "Save",
            "left": 100,
            "top": 200,
            "x": 125,
            "y": 212,
            "width": 50,
            "height": 24,
            "waited_s": 0.2,
        },
    )
    monkeypatch.setattr(
        desktop_features,
        "app_window_rect",
        lambda app: {"left": 80, "top": 160, "width": 600, "height": 500},
    )

    result = ToolExecutor(tmp_path, home_dir=tmp_path).uia_wait("Save", "Notepad", timeout=2)

    assert result.ok is True
    overlay = result.data["overlay"]
    assert overlay["label"] == "Ready: Save"
    assert overlay["rect"] == {"left": 100, "top": 200, "width": 50, "height": 24}


def test_blank_app_rect_payload_uses_foreground_window(monkeypatch):
    import app.widget.desktop_features as desktop_features

    calls = []

    def fake_app_window_rect(app, **kwargs):
        calls.append((app, kwargs))
        return {"left": 40, "top": 50, "width": 700, "height": 500}

    monkeypatch.setattr(desktop_features, "app_window_rect", fake_app_window_rect)

    assert ToolExecutor._app_rect_payload("") == {
        "left": 40,
        "top": 50,
        "width": 700,
        "height": 500,
    }
    assert calls == [("", {"fallback_foreground": True})]


def test_uia_failure_explains_visual_fallback_context(monkeypatch, tmp_path):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "find_ui_elements",
        lambda query, app, limit: {"ok": False, "error": "no UIA control matched 'Send'"},
    )
    monkeypatch.setattr(
        desktop_features,
        "app_window_rect",
        lambda app: {"left": 80, "top": 160, "width": 600, "height": 500},
    )
    # uia_find falls back to on-screen-text OCR before reporting a miss. Mock it
    # to "not found" so this test deterministically exercises the app_focus miss
    # path instead of depending on whatever text happens to be on the real screen
    # (otherwise it's flaky when other windows are open during the run).
    monkeypatch.setattr(
        desktop_features,
        "ocr_find_in_app",
        lambda query, app="": {"ok": False, "error": "no OCR text matched"},
    )

    result = ToolExecutor(tmp_path, home_dir=tmp_path).uia_find("Send", "Discord")

    assert result.ok is False
    overlay = result.data["overlay"]
    assert overlay["type"] == "app_focus"
    assert overlay["fallback_reason"] == "uia_no_match"
    assert "No accessible control" in overlay["label"]


@pytest.mark.asyncio
async def test_agent_emits_structured_overlay_events(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "uia_click",
                    "args": {"query": "Save", "app": "Notepad"},
                    "thought": "click save",
                }
                return
            yield {
                "type": "tool_call",
                "id": "call-2",
                "name": "finish",
                "args": {"reason": "done"},
                "thought": "done",
            }

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        if action.type == ActionType.uia_click:
            return ToolResult(
                ok=True,
                output="Activated 'Save' via invoke_pattern.",
                data={
                    "overlay": {
                        "type": "uia_control",
                        "tool": "uia_click",
                        "kind": "click",
                        "phase": "result",
                        "label": "Clicking Save",
                        "target": "Save",
                        "rect": {"left": 10, "top": 20, "width": 30, "height": 40},
                    }
                },
            )
        return ToolResult(ok=True, output="done")

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-overlay", "Click Save", mode="computer", model="tier:uia")

    starts = [data for event, data in events if event == "action_start" and data["action_type"] == "uia_click"]
    results = [data for event, data in events if event == "action_result" and data["action_type"] == "uia_click"]
    assert starts and starts[0]["overlay"]["phase"] == "start"
    assert starts[0]["overlay"]["label"] == "Locating Save to click"
    assert starts[0]["overlay"]["control_layer"] == "UIA exact"
    assert results and results[0]["overlay"]["rect"] == {"left": 10, "top": 20, "width": 30, "height": 40}


@pytest.mark.asyncio
async def test_agent_marks_visual_fallback_after_uia_miss(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)

    class FakeProvider:
        total_tokens = 0

        def __init__(self):
            self.turn = 0
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            self.turn += 1
            if self.turn == 1:
                yield {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "uia_find",
                    "args": {"query": "Send", "app": "Discord"},
                    "thought": "try accessible control first",
                }
                return
            if self.turn == 2:
                yield {
                    "type": "tool_call",
                    "id": "call-2",
                    "name": "computer",
                    "args": {"action": "left_click", "x": 400, "y": 300},
                    "thought": "fallback to pixels",
                }
                return
            yield {
                "type": "tool_call",
                "id": "call-3",
                "name": "finish",
                "args": {"reason": "done"},
                "thought": "done",
            }

    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        if action.type == ActionType.uia_find:
            return ToolResult(
                ok=False,
                output="no UIA match",
                data={
                    "overlay": {
                        "type": "status",
                        "tool": "uia_find",
                        "kind": "find",
                        "phase": "error",
                        "label": "No accessible control named Send",
                        "fallback_reason": "uia_no_match",
                    }
                },
            )
        if action.type == ActionType.computer:
            return ToolResult(ok=True, output="Clicked left 1 times at 400, 300")
        return ToolResult(ok=True, output="done")

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture_event(task_id, event_type, data):
        events.append((event_type, data))

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_emit", capture_event)
    monkeypatch.setattr(service, "_emit_reasoning", noop_emit)
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: None)

    await service.run_task("task-fallback-overlay", "Click Send", mode="computer", model="tier:uia")

    starts = [
        data for event, data in events
        if event == "action_start" and data["action_type"] == "computer"
    ]
    assert starts
    overlay = starts[0]["overlay"]
    assert overlay["type"] == "point"
    assert overlay["fallback_reason"] == "uia_no_match"
    assert overlay["control_layer"] == "Screenshot fallback"
    assert overlay["point"] == {"x": 400, "y": 300}
    assert overlay["label"] == "No accessible control found; using visual fallback"
