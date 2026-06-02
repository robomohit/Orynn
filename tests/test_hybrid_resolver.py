"""Hybrid resolver: UIA -> OCR pixel (local, no model) -> (agent escalates to
vision). Plus uia_type post-action verification. These exercise the fallback
wiring with the OCR + UIA layers mocked, so they run without a real desktop."""
from pathlib import Path

import app.tools as tools_mod
from app.tools import ToolExecutor


def _ex(tmp_path):
    return ToolExecutor(Path(tmp_path), home_dir=Path(tmp_path))


def test_uia_click_falls_back_to_ocr_on_uia_miss(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    # UIA finds nothing...
    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": False, "error": "no UIA control matched"})
    # ...but OCR locates the on-screen text.
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": True, "x": 329, "y": 216,
                                      "matched": "Edit", "score": 100})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 800, "height": 600})

    clicked = {}
    fake_pyautogui = type("PG", (), {"click": staticmethod(
        lambda x, y: clicked.update(x=x, y=y))})
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", fake_pyautogui)

    res = _ex(tmp_path).uia_click("Edit", "Notepad")
    assert res.ok is True
    assert clicked == {"x": 329, "y": 216}
    assert res.data["overlay"]["control_layer"] == "OCR fallback"
    assert res.data["method"] == "ocr_pixel"


def test_uia_click_reports_miss_when_uia_and_ocr_both_fail(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": False, "error": "no UIA control matched"})
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": False, "error": "no OCR text matched"})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})

    res = _ex(tmp_path).uia_click("Reply", "Chrome")
    assert res.ok is False
    # the agent will escalate to the vision model from here
    assert res.data["overlay"]["control_layer"] == "UIA miss"


def test_uia_find_falls_back_to_ocr_on_uia_miss(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    # No accessible control in the tree...
    monkeypatch.setattr(df, "find_ui_elements",
                        lambda q, a, n: {"ok": False, "error": "no UIA control matched"})
    # ...but the text is visible on screen.
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": True, "x": 227, "y": 421,
                                      "matched": "Find", "score": 100})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 800, "height": 600})

    res = _ex(tmp_path).uia_find("Find", "Notepad")
    assert res.ok is True
    assert res.data["layer"] == "ocr"
    assert res.data["overlay"]["control_layer"] == "OCR fallback"
    assert res.data["items"][0]["x"] == 227 and res.data["items"][0]["y"] == 421
    assert "OCR matches" in res.output


def test_uia_find_reports_miss_when_uia_and_ocr_both_fail(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "find_ui_elements",
                        lambda q, a, n: {"ok": False, "error": "no UIA control matched"})
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": False, "error": "no OCR text matched"})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})

    res = _ex(tmp_path).uia_find("Ghost", "Chrome")
    assert res.ok is False
    assert res.data["overlay"]["control_layer"] == "UIA miss"


def test_ocr_norm_matches_through_punctuation_and_accelerators(monkeypatch):
    import app.widget.desktop_features as df

    # OCR sees menu labels with an accelerator '&' and trailing ellipsis; the
    # agent's query is the bare word. Normalisation should still match exactly.
    screen = [
        {"text": "&File", "x": 20, "y": 10},
        {"text": "Find...", "x": 80, "y": 10},
        {"text": "Edit,", "x": 140, "y": 10},
    ]
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 400, "height": 300})
    monkeypatch.setattr(df, "win_ocr_words", lambda l, t, w, h: screen)

    hit = df.ocr_find_in_app("Find", "Notepad")
    assert hit["ok"] is True
    assert (hit["x"], hit["y"]) == (80, 10)
    assert hit["score"] == 100  # exact after normalising the trailing "..."

    assert df.ocr_find_in_app("File", "Notepad")["score"] == 100  # '&' stripped
    assert df.ocr_find_in_app("Edit", "Notepad")["score"] == 100  # ',' stripped


def test_ocr_phrase_match_requires_word_boundary(monkeypatch):
    import app.widget.desktop_features as df

    # "view" must NOT match the substring inside "teview codebase" — that kind of
    # cross-word hit would send a fallback click to the wrong place.
    screen = [
        {"text": "teview", "x": 50, "y": 40},
        {"text": "codebase", "x": 120, "y": 40},
    ]
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 400, "height": 300})
    monkeypatch.setattr(df, "win_ocr_words", lambda l, t, w, h: screen)

    assert df.ocr_find_in_app("view", "Notepad")["ok"] is False
    # but a real whole-word phrase hit still matches
    screen2 = [{"text": "Review", "x": 50, "y": 40}, {"text": "codebase", "x": 120, "y": 40}]
    monkeypatch.setattr(df, "win_ocr_words", lambda l, t, w, h: screen2)
    assert df.ocr_find_in_app("review codebase", "Notepad")["score"] >= 100


def test_reuse_existing_window_for_single_instance_app(monkeypatch, tmp_path):
    ex = _ex(tmp_path)
    # A Notepad window is already open...
    monkeypatch.setattr(ex, "_iter_matching_windows",
                        lambda t: [{"hwnd": 1, "title": "Untitled - Notepad", "pid": 9}])
    focused = {}
    monkeypatch.setattr(ex, "focus_window",
                        lambda t: focused.update(t=t) or tools_mod.ToolResult(ok=True, output=f"Focused '{t}'"))
    launched = {}
    monkeypatch.setattr(tools_mod.subprocess, "Popen",
                        lambda *a, **k: launched.update(called=True))

    res = ex.run_command("start notepad")
    assert res.ok is True
    assert focused.get("t") == "Notepad"
    assert "no duplicate launched" in res.output
    assert launched == {}  # never spawned a second process


def test_no_reuse_for_browser_or_url(monkeypatch, tmp_path):
    ex = _ex(tmp_path)
    monkeypatch.setattr(ex, "_iter_matching_windows",
                        lambda t: [{"hwnd": 1, "title": t, "pid": 9}])
    monkeypatch.setattr(ex, "focus_window",
                        lambda t: tools_mod.ToolResult(ok=True, output="should-not-be-called"))
    launched = {}
    monkeypatch.setattr(tools_mod.subprocess, "Popen",
                        lambda *a, **k: launched.update(called=True))
    monkeypatch.setattr(ex, "_auto_wait_after_launch", lambda c: None)

    # chrome is not single-instance -> must launch, not focus
    ex.run_command("start chrome")
    assert launched.get("called") is True


def test_uia_click_verifies_state_change(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": True, "method": "invoke_pattern",
                                      "target": "Edit", "rect": {}})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 800, "height": 600})

    ex = _ex(tmp_path)
    # a menu popup (new top-level window 99) appears -> the click took effect.
    snaps = iter([{"fg": (1, "Notepad"), "wins": {1, 2}},
                  {"fg": (1, "Notepad"), "wins": {1, 2, 99}}])
    monkeypatch.setattr(ex, "_click_snapshot", lambda: next(snaps))

    res = ex.uia_click("Edit", "Notepad")
    assert res.ok is True
    assert res.data["verified"] is True
    assert "(verified)" in res.output


def test_uia_click_unverifiable_is_not_a_failure(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": True, "method": "invoke_pattern",
                                      "target": "Bold", "rect": {}})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 800, "height": 600})

    ex = _ex(tmp_path)
    # nothing observable changed (e.g. a toggle that keeps focus) -> verified None,
    # but the click is still a success, never mislabelled as failed.
    monkeypatch.setattr(ex, "_click_snapshot",
                        lambda: {"fg": (1, "Word"), "wins": {1, 2}})

    res = ex.uia_click("Bold", "Word")
    assert res.ok is True
    assert res.data["verified"] is None
    assert "(verified)" not in res.output


def test_electron_unlock_hint_on_hard_miss(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    # UIA and OCR both miss, AND the target is an Electron app -> the agent is
    # told it can unlock the DOM instead of just escalating to vision.
    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": False, "error": "no UIA control matched"})
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": False, "error": "no OCR text matched"})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})
    monkeypatch.setattr(df, "electron_hint_for_app",
                        lambda app: {"exe": r"C:\\Discord\\Discord.exe",
                                     "tip": "Discord is an Electron app — unlock it."})

    res = _ex(tmp_path).uia_click("Message", "Discord")
    assert res.ok is False
    assert res.data["electron_hint"]["exe"].endswith("Discord.exe")
    assert "Electron" in res.output


def test_no_electron_hint_for_native_app(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": False, "error": "no UIA control matched"})
    monkeypatch.setattr(df, "ocr_find_in_app",
                        lambda q, a: {"ok": False, "error": "no OCR text matched"})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})
    monkeypatch.setattr(df, "electron_hint_for_app", lambda app: None)

    res = _ex(tmp_path).uia_click("Save", "Notepad")
    assert res.ok is False
    assert "electron_hint" not in res.data


def test_uia_click_sequence_one_call(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    seen = []
    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: seen.append(q) or {"ok": True, "target": q})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 400, "height": 300})

    res = _ex(tmp_path).uia_click_sequence(
        ["Two", "Five", "Six", "Minus", "Eight", "Nine", "Equals"], "Calculator")
    assert res.ok is True
    assert res.data["clicked"] == 7 and res.data["total"] == 7
    assert seen == ["Two", "Five", "Six", "Minus", "Eight", "Nine", "Equals"]


def test_uia_click_sequence_stops_on_miss(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    # 'Nine' isn't found in UIA and OCR also misses -> stop, report which failed.
    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": q != "Nine", "target": q})
    monkeypatch.setattr(df, "ocr_find_in_app", lambda q, a: {"ok": False})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})

    res = _ex(tmp_path).uia_click_sequence("Two,Nine,Equals", "Calculator")
    assert res.ok is False
    assert res.data["failed"] == "Nine"
    assert res.data["clicked"] == 1  # only Two landed before the miss


def test_uia_click_sequence_adds_electron_hint_on_hard_miss(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "invoke_ui_element",
                        lambda q, a: {"ok": False, "error": "no UIA control matched"})
    monkeypatch.setattr(df, "ocr_find_in_app", lambda q, a: {"ok": False})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})
    monkeypatch.setattr(df, "electron_hint_for_app",
                        lambda app: {"exe": r"C:\\Discord\\Discord.exe",
                                     "tip": "Discord is an Electron app - unlock it."})

    res = _ex(tmp_path).uia_click_sequence(["Messages", "Send"], "Discord")
    assert res.ok is False
    assert res.data["electron_hint"]["exe"].endswith("Discord.exe")
    assert res.data["overlay"]["fallback_reason"] == "uia_no_match"
    assert "Electron app" in res.output


def test_uia_wait_adds_electron_hint_on_timeout(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "wait_for_ui_element",
                        lambda q, a, t: {"ok": False, "error": "timed out waiting"})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 0, "height": 0})
    monkeypatch.setattr(df, "electron_hint_for_app",
                        lambda app: {"exe": r"C:\\Slack\\slack.exe",
                                     "tip": "Slack is an Electron app - unlock it."})

    res = _ex(tmp_path).uia_wait("Message composer", "Slack", timeout=0.01)
    assert res.ok is False
    assert res.data["electron_hint"]["exe"].endswith("slack.exe")
    assert res.data["overlay"]["fallback_reason"] == "uia_wait_timeout"
    assert "Electron app" in res.output


def test_uia_type_reports_verification(monkeypatch, tmp_path):
    import app.widget.desktop_features as df

    monkeypatch.setattr(df, "type_into_ui_element",
                        lambda q, t, a, c, s: {"ok": True, "method": "paste",
                                               "target": "Text editor", "rect": {}})
    monkeypatch.setattr(df, "app_window_rect",
                        lambda a: {"left": 0, "top": 0, "width": 800, "height": 600})

    class _VP:
        Value = "hello world"

    class _Ctrl:
        def GetValuePattern(self):
            return _VP()

    monkeypatch.setattr(df, "_find_uia_control", lambda q, a: (_Ctrl(), {}))

    res = _ex(tmp_path).uia_type("Text editor", "hello world", "Notepad")
    assert res.ok is True
    assert res.data["verified"] is True
    assert "verified" in res.output
