from __future__ import annotations

import pytest

from app.adaptive_windows import (
    FailureClass,
    SurfaceRuntime,
    analyze_windows_failure,
    build_affordance_graph,
    classify_surface_runtime,
    format_runtime_plan,
    format_recovery_plan,
    remember_resolver_outcome,
    resolver_ids,
)
from app.models import Action, ActionType, ToolResult
from app.tools import ToolExecutor


def test_adaptive_windows_classifies_missing_app():
    analysis = analyze_windows_failure(
        action="uia_click",
        query="Send",
        app="Discord",
        output="no UIA control matched 'Send' - no window titled like 'Discord' is open",
    )

    assert analysis.failure_class == FailureClass.app_not_found
    assert analysis.resolvers[0].id == "wait_for_window"
    assert analysis.resolvers[0].args["title"] == "Discord"


def test_adaptive_windows_promotes_exact_listed_control_name():
    analysis = analyze_windows_failure(
        action="uia_click",
        query="Fuor",
        app="Calculator",
        output=(
            "no UIA control matched 'Fuor'. Did you mean: 'Four'? "
            "Controls actually in this window (use these EXACT names): "
            "'Equals', 'Multiply by'."
        ),
    )

    assert analysis.failure_class == FailureClass.uia_no_match
    assert analysis.resolvers[0].id == "use_listed_control_name"
    assert analysis.resolvers[0].args["query"] == "Four"
    assert "Adaptive recovery plan" in format_recovery_plan(analysis)


def test_adaptive_windows_learned_resolver_is_promoted(monkeypatch, tmp_path):
    monkeypatch.setenv("ORYNN_WORKSPACE", str(tmp_path))

    remember_resolver_outcome(
        "CanvasApp",
        FailureClass.uia_no_match.value,
        "ocr_text_target",
        True,
        detail="Found Start by OCR",
    )
    analysis = analyze_windows_failure(
        action="uia_click",
        query="Start",
        app="CanvasApp",
        output="no UIA control matched 'Start'.",
    )

    assert resolver_ids(analysis.resolvers)[0] == "ocr_text_target"
    assert analysis.learned[0]["successes"] == 1


def test_tool_executor_uia_find_attaches_adaptive_plan(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    def fake_find_ui_elements(query, app, limit):
        return {
            "ok": False,
            "error": (
                "no UIA control matched 'Fuor'. Did you mean: 'Four'? "
                "Controls actually in this window (use these EXACT names): 'Equals'."
            ),
        }

    monkeypatch.setattr(desktop_features, "find_ui_elements", fake_find_ui_elements)
    monkeypatch.setattr(ToolExecutor, "_ocr_find_fallback", lambda self, query, app: None)
    monkeypatch.setattr(ToolExecutor, "_electron_unlock_hint", lambda self, app, data: "")
    monkeypatch.setattr(ToolExecutor, "_app_rect_payload", staticmethod(lambda app: None))

    result = ToolExecutor(workspace).uia_find("Fuor", "Calculator")

    assert result.ok is False
    assert "Adaptive recovery plan" in result.output
    assert result.data["adaptive"]["failure_class"] == FailureClass.uia_no_match.value
    assert result.data["adaptive"]["resolvers"][0]["id"] == "use_listed_control_name"


def test_build_affordance_graph_groups_common_controls():
    graph = build_affordance_graph(
        app="Example",
        count=5,
        controls=["Text editor", "Save", "File", "Next tab", "Mystery"],
    )

    assert graph["groups"]["text_input"] == ["Text editor"]
    assert graph["groups"]["command"] == ["Save"]
    assert graph["groups"]["menu_or_toolbar"] == ["File"]
    assert graph["groups"]["navigation"] == ["Next tab"]
    assert graph["affordances"][0]["preferred_actions"] == ["uia_type", "uia_find"]


def test_classify_surface_runtime_prefers_rich_uia():
    graph = build_affordance_graph(
        app="Settings",
        count=80,
        controls=[f"Control {idx}" for idx in range(12)],
    )

    plan = classify_surface_runtime(
        app="Settings",
        graph=graph,
        app_rect={"left": 0, "top": 0, "width": 800, "height": 600},
        ocr_available=True,
    )

    assert plan.runtime == SurfaceRuntime.uia_rich
    assert plan.primary_layer == "uia"
    assert plan.next_tools[0] == "uia_find"
    assert "Runtime plan" in format_runtime_plan(plan)


def test_classify_surface_runtime_detects_electron_lock():
    graph = build_affordance_graph(app="Discord", count=0, controls=[])

    plan = classify_surface_runtime(
        app="Discord",
        graph=graph,
        app_rect={"left": 0, "top": 0, "width": 800, "height": 600},
        electron_hint={"exe": r"C:\Discord\Discord.exe"},
        ocr_available=True,
    )

    assert plan.runtime == SurfaceRuntime.electron_locked
    assert plan.primary_layer == "electron_accessibility"
    assert "electron_unlock" in plan.next_tools


def test_classify_surface_runtime_detects_custom_surface_without_ocr():
    graph = build_affordance_graph(app="Game", count=0, controls=[])

    plan = classify_surface_runtime(
        app="Game",
        graph=graph,
        app_rect={"left": 0, "top": 0, "width": 1280, "height": 720},
        ocr_available=False,
        model_vision=True,
    )

    assert plan.runtime == SurfaceRuntime.custom_rendered
    assert plan.primary_layer == "keyboard_visual"
    assert plan.next_tools[:2] == ["key_combo", "screen_context"]


def test_classify_surface_runtime_detects_custom_surface_when_ocr_probe_empty():
    graph = build_affordance_graph(app="Game", count=0, controls=[])

    plan = classify_surface_runtime(
        app="Game",
        graph=graph,
        app_rect={"left": 0, "top": 0, "width": 1280, "height": 720},
        ocr_available=True,
        visual_word_count=0,
    )

    assert plan.runtime == SurfaceRuntime.custom_rendered
    assert plan.confidence >= 0.8
    assert plan.evidence["visual_word_count"] == 0


def test_classify_surface_runtime_uses_ocr_when_probe_not_run():
    graph = build_affordance_graph(app="Canvas", count=0, controls=[])

    plan = classify_surface_runtime(
        app="Canvas",
        graph=graph,
        app_rect={"left": 0, "top": 0, "width": 900, "height": 600},
        ocr_available=True,
        visual_word_count=None,
    )

    assert plan.runtime == SurfaceRuntime.visual_text
    assert plan.primary_layer == "ocr"
    assert plan.evidence["visual_word_count"] is None


def test_adaptive_observe_schema_is_in_uia_pack():
    from app.tool_registry import get_tool_schemas

    names = [schema["function"]["name"] for schema in get_tool_schemas(["uia"])]

    assert names[0] == "adaptive_observe"
    schema = get_tool_schemas(["uia"])[0]["function"]["parameters"]
    assert "app" in schema["properties"]
    assert "cap" in schema["properties"]
    assert "app" not in schema["required"]


def test_window_hint_score_matches_titleless_process_window():
    import app.widget.desktop_features as desktop_features

    score = desktop_features._window_hint_score(
        {
            "hwnd": 4242,
            "title": "",
            "exe": r"C:\Games\Mahoraga.exe",
        },
        "Mahoraga",
    )

    assert score >= 80


def test_window_hint_score_matches_camelcase_exe_token_to_title():
    import app.widget.desktop_features as desktop_features

    score = desktop_features._window_hint_score(
        {
            "hwnd": 200,
            "title": "Settings",
            "exe": r"C:\Windows\System32\ApplicationFrameHost.exe",
        },
        "SystemSettings.exe",
    )

    assert score >= 90


def test_uia_root_candidates_falls_back_to_process_matched_hwnd(monkeypatch):
    import sys
    import types
    import app.widget.desktop_features as desktop_features

    class RootControl:
        def GetChildren(self):
            return []

    class AppControl:
        Name = ""
        NativeWindowHandle = 4242
        ControlTypeName = "WindowControl"
        ClassName = "Chrome_WidgetWin_1"

        def GetChildren(self):
            return []

    root = RootControl()
    app = AppControl()
    fake_uia = types.SimpleNamespace(
        GetRootControl=lambda: root,
        GetForegroundControl=lambda: types.SimpleNamespace(NativeWindowHandle=0),
        ControlFromHandle=lambda hwnd: app if hwnd == 4242 else None,
    )
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    monkeypatch.setattr(desktop_features, "_ensure_uia_config", lambda uia: None)
    monkeypatch.setattr(desktop_features, "_window_cloaked", lambda hwnd: False)
    monkeypatch.setattr(
        desktop_features,
        "_visible_top_level_windows",
        lambda include_untitled=False: [
            {
                "hwnd": 4242,
                "title": "",
                "exe": r"C:\Games\Mahoraga.exe",
                "class_name": "Chrome_WidgetWin_1",
                "area": 1920 * 1080,
            }
        ],
    )

    roots = desktop_features._uia_root_candidates("Mahoraga", fallback_foreground=False)

    assert roots == [app]


def test_uia_root_candidates_includes_same_title_uwp_frame(monkeypatch):
    import sys
    import types
    import app.widget.desktop_features as desktop_features

    class RootControl:
        def GetChildren(self):
            return []

    class AppControl:
        Name = "Settings"
        ClassName = ""

        def __init__(self, hwnd, control_type):
            self.NativeWindowHandle = hwnd
            self.ControlTypeName = control_type

        def GetChildren(self):
            return []

    core = AppControl(100, "PaneControl")
    frame = AppControl(200, "WindowControl")
    fake_uia = types.SimpleNamespace(
        GetRootControl=lambda: RootControl(),
        GetForegroundControl=lambda: types.SimpleNamespace(NativeWindowHandle=0),
        ControlFromHandle=lambda hwnd: {100: core, 200: frame}.get(hwnd),
    )
    monkeypatch.setitem(sys.modules, "uiautomation", fake_uia)
    monkeypatch.setattr(desktop_features, "_ensure_uia_config", lambda uia: None)
    monkeypatch.setattr(desktop_features, "_window_cloaked", lambda hwnd: False)
    monkeypatch.setattr(desktop_features, "_has_real_content", lambda ctrl: ctrl is frame)
    monkeypatch.setattr(
        desktop_features,
        "_visible_top_level_windows",
        lambda include_untitled=False: [
            {
                "hwnd": 100,
                "title": "Settings",
                "exe": r"C:\Windows\ImmersiveControlPanel\SystemSettings.exe",
                "class_name": "Windows.UI.Core.CoreWindow",
                "area": 1680 * 1000,
            },
            {
                "hwnd": 200,
                "title": "Settings",
                "exe": r"C:\Windows\System32\ApplicationFrameHost.exe",
                "class_name": "ApplicationFrameWindow",
                "area": 1688 * 1010,
            },
        ],
    )

    roots = desktop_features._uia_root_candidates("SystemSettings.exe", fallback_foreground=False)

    assert frame in roots
    assert roots[0] is frame


@pytest.mark.asyncio
async def test_tool_executor_adaptive_observe_maps_controls(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "survey_app_controls",
        lambda app, cap=90, max_names=60, fallback_foreground=False: {
            "count": 4,
            "controls": ["Text editor", "Save", "File", "Next tab"],
        },
    )
    monkeypatch.setattr(
        desktop_features,
        "foreground_window_info",
        lambda: {"title": "Notepad", "hwnd": 123},
    )
    monkeypatch.setattr(ToolExecutor, "_app_rect_payload", staticmethod(lambda app: None))

    result = await ToolExecutor(workspace).run_action(
        Action(
            id="observe",
            type=ActionType.adaptive_observe,
            args={"app": "Notepad", "cap": 120},
        )
    )

    assert result.ok is True
    assert "Adaptive app map for Notepad" in result.output
    assert "Runtime plan" in result.output
    assert result.data["runtime"]["runtime"] == SurfaceRuntime.uia_sparse.value
    assert result.data["graph"]["groups"]["text_input"] == ["Text editor"]
    assert result.data["graph"]["groups"]["command"] == ["Save"]


def test_tool_executor_adaptive_observe_empty_tree_adds_recovery_plan(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "survey_app_controls",
        lambda app, cap=90, max_names=60, fallback_foreground=False: {
            "count": 0,
            "controls": [],
        },
    )
    monkeypatch.setattr(desktop_features, "foreground_window_info", lambda: {"title": "Canvas"})
    monkeypatch.setattr(ToolExecutor, "_app_rect_payload", staticmethod(lambda app: None))
    monkeypatch.setattr(
        ToolExecutor,
        "wait_for_window",
        lambda self, title, timeout=10.0, paint_seconds=0.35: ToolResult(ok=False, output="missing"),
    )

    result = ToolExecutor(workspace).adaptive_observe("Canvas")

    assert result.ok is True
    assert "Adaptive recovery plan" in result.output
    assert result.data["adaptive"]["failure_class"] == FailureClass.empty_accessibility_tree.value


def test_tool_executor_adaptive_observe_empty_ocr_marks_custom_surface(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    monkeypatch.setattr(
        desktop_features,
        "survey_app_controls",
        lambda app, cap=90, max_names=60, fallback_foreground=False: {
            "count": 0,
            "controls": [],
        },
    )
    monkeypatch.setattr(desktop_features, "foreground_window_info", lambda: {"title": "Game"})
    monkeypatch.setattr(desktop_features, "electron_hint_for_app", lambda app: None)
    monkeypatch.setattr(desktop_features, "ocr_available", lambda: True)
    monkeypatch.setattr(desktop_features, "win_ocr_words", lambda l, t, w, h: [])
    monkeypatch.setattr(
        ToolExecutor,
        "_app_rect_payload",
        staticmethod(lambda app: {"left": 0, "top": 0, "width": 1280, "height": 720}),
    )
    monkeypatch.setattr(
        ToolExecutor,
        "wait_for_window",
        lambda self, title, timeout=10.0, paint_seconds=0.35: ToolResult(ok=False, output="missing"),
    )

    result = ToolExecutor(workspace).adaptive_observe("Game")

    assert result.ok is True
    assert result.data["runtime"]["runtime"] == SurfaceRuntime.custom_rendered.value
    assert result.data["runtime"]["primary_layer"] == "keyboard_visual"
    assert result.data["runtime"]["evidence"]["visual_word_count"] == 0


def test_tool_executor_adaptive_observe_recovers_after_focus_resurvey(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    surveys = [
        {"count": 1, "controls": []},
        {"count": 42, "controls": ["Search box, Find a setting", "System", "Apps"]},
    ]
    calls = []

    def fake_survey(app, cap=90, max_names=60, fallback_foreground=False):
        calls.append((app, fallback_foreground))
        return surveys.pop(0)

    monkeypatch.setattr(desktop_features, "survey_app_controls", fake_survey)
    monkeypatch.setattr(
        desktop_features,
        "foreground_window_info",
        lambda: {"title": "Unrelated foreground"},
    )
    monkeypatch.setattr(ToolExecutor, "_app_rect_payload", staticmethod(lambda app: None))
    monkeypatch.setattr(
        ToolExecutor,
        "wait_for_window",
        lambda self, title, timeout=10.0, paint_seconds=0.35: ToolResult(
            ok=True,
            output="ready",
            data={"title": title, "hwnd": 123},
        ),
    )
    monkeypatch.setattr(
        ToolExecutor,
        "focus_window",
        lambda self, title: ToolResult(ok=True, output=f"Focused {title}"),
    )

    result = ToolExecutor(workspace).adaptive_observe("Settings")

    assert result.ok is True
    assert result.data["recovered_by"] == "focus_wait_resurvey"
    assert result.data["graph"]["named_control_count"] == 3
    assert "foreground" not in result.data
    assert result.data["recovery_attempts"][-1]["named_control_count"] == 3
    assert calls == [("Settings", False), ("Settings", False)]


def test_verify_typed_reads_legacy_accessible_value(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    class LegacyPattern:
        Value = "hello from legacy"

    class Ctrl:
        def GetValuePattern(self):
            raise RuntimeError("no value pattern")

        def GetLegacyIAccessiblePattern(self):
            return LegacyPattern()

        def GetTextPattern(self):
            raise RuntimeError("no text pattern")

    monkeypatch.setattr(
        desktop_features,
        "_find_uia_control",
        lambda query, app: (Ctrl(), {"name": "Text editor"}),
    )

    assert ToolExecutor(workspace)._verify_typed("Text editor", "Notepad", "hello") is True


def test_verify_typed_returns_false_when_readback_contradicts(monkeypatch, workspace):
    import app.widget.desktop_features as desktop_features

    class ValuePattern:
        Value = "different value"

    class Ctrl:
        def GetValuePattern(self):
            return ValuePattern()

        def GetLegacyIAccessiblePattern(self):
            raise RuntimeError("no legacy pattern")

        def GetTextPattern(self):
            raise RuntimeError("no text pattern")

    monkeypatch.setattr(
        desktop_features,
        "_find_uia_control",
        lambda query, app: (Ctrl(), {"name": "Text editor"}),
    )

    assert ToolExecutor(workspace)._verify_typed("Text editor", "Notepad", "expected") is False
