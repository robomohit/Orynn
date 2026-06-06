import asyncio
import json
import pytest
import sys
import types
import time

from app.models import Action, ActionType, ToolResult
from app.permissions import PermissionScope, can_auto_grant_scope, scope_for_action
from app.safety import SafetyManager
from app.text_editor import TextEditorTool
from app.tools import ToolExecutor
import app.tools as tools_module

@pytest.mark.asyncio
async def test_new_actions(monkeypatch, workspace):
    calls = {}
    pg = types.SimpleNamespace(
        moveTo=lambda *a, **k: calls.setdefault("moveTo", []).append((a, k)),
        scroll=lambda v: calls.setdefault("scroll", []).append(v),
        doubleClick=lambda *a, **k: calls.setdefault("doubleClick", []).append((a, k)),
        click=lambda *a, **k: calls.setdefault("click", []).append((a, k)),
        dragTo=lambda *a, **k: calls.setdefault("dragTo", []).append((a, k)),
        hotkey=lambda *a: calls.setdefault("hotkey", []).append(a),
        keyDown=lambda k: calls.setdefault("keyDown", []).append(k),
        keyUp=lambda k: calls.setdefault("keyUp", []).append(k),
        position=lambda: (5, 7),
        write=lambda x, **kw: calls.setdefault("write", []).append(x),
        size=lambda: (1920, 1080),
        easeInOutQuad=lambda n: n,  # tween function used by moveTo/dragTo
    )
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", pg)
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    
    assert (await t.run_action(Action(id="1", type=ActionType.scroll, args={"amount": 3, "x": 1, "y": 2}))).ok
    assert calls["scroll"][-1] == 3
    
    assert (await t.run_action(Action(id="2", type=ActionType.key_combo, args={"keys": "ctrl+shift+t"}))).ok
    assert calls["hotkey"][-1] == ("ctrl", "shift", "t")
    
    assert (await t.run_action(Action(id="3", type=ActionType.wait_action, args={"seconds": 2}))).ok
    assert slept[-1] == 2
    
    assert (await t.run_action(Action(id="4", type=ActionType.double_click, args={"x": 1, "y": 1}))).ok
    assert (await t.run_action(Action(id="5", type=ActionType.right_click, args={"x": 1, "y": 1}))).ok
    assert (await t.run_action(Action(id="6", type=ActionType.middle_click, args={"x": 1, "y": 1}))).ok
    
    assert (await t.run_action(Action(id="7", type=ActionType.mouse_move, args={"x": 1, "y": 1}))).ok
    assert (await t.run_action(Action(id="8", type=ActionType.left_click_drag, args={"x": 2, "y": 2}))).ok
    assert (await t.run_action(Action(id="9", type=ActionType.hold_key, args={"key": "a", "duration": 1}))).ok
    
    out = await t.run_action(Action(id="10", type=ActionType.cursor_position, args={}))
    assert out.data == {"x": 5, "y": 7}

    bash_out = await t.run_action(Action(id="11", type=ActionType.bash, args={"command": "cd ."}))
    assert bash_out.ok

    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "marker.txt").write_text("ok", encoding="utf-8")
    bash_cd_run = await t.run_action(
        Action(id="11b", type=ActionType.bash, args={"command": f"cd subdir && {sys.executable} -c \"from pathlib import Path; print(Path('marker.txt').read_text())\""})
    )
    assert bash_cd_run.ok
    assert "ok" in bash_cd_run.output

    bash_mkdir = await t.run_action(Action(id="11c", type=ActionType.bash, args={"command": "mkdir -p nested/project"}))
    assert bash_mkdir.ok
    assert (workspace / "nested" / "project").is_dir()

    run_mkdir = await t.run_action(Action(id="11d", type=ActionType.run_command, args={"command": "mkdir -p command_project"}))
    assert run_mkdir.ok
    assert (workspace / "command_project").is_dir()

    text_create_out = await t.run_action(
        Action(
            id="12",
            type=ActionType.text_editor,
            args={"command": "create", "path": "alias.txt", "file_text": "hello"},
        )
    )
    assert text_create_out.ok

    text_view_out = await t.run_action(
        Action(
            id="13",
            type=ActionType.text_editor,
            args={"command": "view", "path": "alias.txt"},
        )
    )
    assert "hello" in text_view_out.output

    computer_out = await t.run_action(
        Action(
            id="14",
            type=ActionType.computer,
            args={"action": "key", "keys": "ctrl+l"},
        )
    )
    assert computer_out.ok
    assert calls["hotkey"][-1] == ("ctrl", "l")

def test_safety_key_combo():
    s = SafetyManager()
    dec = s.evaluate(Action(id="1", type=ActionType.key_combo, args={"keys": "ctrl+alt+del"}))
    assert dec.danger.value == "high"


def test_safety_desktop_lifecycle_actions_always_require_approval():
    s = SafetyManager()

    close_decision = s.evaluate(
        Action(id="close", type=ActionType.force_close_window, args={"title": "Notepad"}),
        safe_mode=False,
    )
    unlock_decision = s.evaluate(
        Action(id="unlock", type=ActionType.electron_unlock, args={"exe": "Discord.exe"}),
        safe_mode=False,
    )

    assert close_decision.danger.value == "high"
    assert close_decision.requires_approval is True
    assert "terminates" in close_decision.reason
    assert unlock_decision.danger.value == "high"
    assert unlock_decision.requires_approval is True
    assert "relaunches" in unlock_decision.reason


def test_safety_process_and_watch_actions_are_classified():
    s = SafetyManager()

    kill_decision = s.evaluate(
        Action(id="kill", type=ActionType.kill_process, args={"pid": 1234}),
        safe_mode=False,
    )
    watch_safe_decision = s.evaluate(
        Action(id="watch-safe", type=ActionType.run_and_watch, args={"command": "npm run dev"}),
        safe_mode=True,
    )
    watch_auto_decision = s.evaluate(
        Action(id="watch-auto", type=ActionType.run_and_watch, args={"command": "npm run dev"}),
        safe_mode=False,
    )
    watch_dangerous_decision = s.evaluate(
        Action(id="watch-danger", type=ActionType.run_and_watch, args={"command": "shutdown /s"}),
        safe_mode=False,
    )

    assert kill_decision.danger.value == "high"
    assert kill_decision.requires_approval is True
    assert "terminates" in kill_decision.reason
    assert watch_safe_decision.danger.value == "high"
    assert watch_safe_decision.requires_approval is True
    assert watch_auto_decision.danger.value == "medium"
    assert watch_auto_decision.requires_approval is False
    assert watch_dangerous_decision.danger.value == "high"
    assert watch_dangerous_decision.requires_approval is True


def test_safety_and_permissions_classify_terminal_helpers():
    s = SafetyManager()

    run_tests_safe = s.evaluate(
        Action(id="tests-safe", type=ActionType.run_tests, args={"command": "pytest -q"}),
        safe_mode=True,
    )
    run_tests_auto = s.evaluate(
        Action(id="tests-auto", type=ActionType.run_tests, args={"command": "pytest -q"}),
        safe_mode=False,
    )
    run_tests_dangerous = s.evaluate(
        Action(id="tests-danger", type=ActionType.run_tests, args={"command": "shutdown /s"}),
        safe_mode=False,
    )
    git_safe = s.evaluate(
        Action(id="git", type=ActionType.git, args={"command": "status"}),
        safe_mode=True,
    )
    lint_safe = s.evaluate(
        Action(id="lint", type=ActionType.lint_code, args={"path": "app.py"}),
        safe_mode=True,
    )

    assert run_tests_safe.danger.value == "high"
    assert run_tests_safe.requires_approval is True
    assert run_tests_auto.danger.value == "medium"
    assert run_tests_auto.requires_approval is False
    assert run_tests_dangerous.danger.value == "high"
    assert run_tests_dangerous.requires_approval is True
    assert git_safe.danger.value == "high"
    assert git_safe.requires_approval is True
    assert lint_safe.danger.value == "high"
    assert lint_safe.requires_approval is True
    assert scope_for_action("run_tests") == PermissionScope.shell
    assert scope_for_action("run_and_watch") == PermissionScope.shell
    assert scope_for_action("git") == PermissionScope.shell
    assert scope_for_action("lint_code") == PermissionScope.shell


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item -Recurse -Force C:\\Users\\ACER\\Documents",
        "del /s /q C:\\Users\\ACER\\Documents\\*",
        "diskpart /s wipe.txt",
        "reg delete HKCU\\Software\\Orynn /f",
    ],
)
def test_safety_flags_destructive_windows_commands(command):
    decision = SafetyManager().evaluate(
        Action(id="danger", type=ActionType.run_command, args={"command": command}),
        safe_mode=False,
    )

    assert decision.danger.value == "high"
    assert decision.requires_approval is True


def test_safety_and_permissions_classify_folder_analysis_modes():
    s = SafetyManager()

    scan_decision = s.evaluate(
        Action(
            id="scan",
            type=ActionType.analyze_folder,
            args={"path": "~/Downloads", "action": "scan"},
        ),
        safe_mode=False,
    )
    organize_decision = s.evaluate(
        Action(
            id="organize",
            type=ActionType.analyze_folder,
            args={"path": "~/Downloads", "action": "organize"},
        ),
        safe_mode=False,
    )

    assert scan_decision.danger.value == "low"
    assert scan_decision.requires_approval is False
    assert "folder scan" in scan_decision.reason
    assert organize_decision.danger.value == "high"
    assert organize_decision.requires_approval is True
    assert "organize" in organize_decision.reason
    assert scope_for_action("analyze_folder") == PermissionScope.filesystem


def test_permissions_classify_filesystem_read_helpers():
    assert scope_for_action("read_file") == PermissionScope.filesystem
    assert scope_for_action("list_directory") == PermissionScope.filesystem
    assert scope_for_action("file_glob") == PermissionScope.filesystem
    assert scope_for_action("file_grep") == PermissionScope.filesystem
    assert scope_for_action("text_view") == PermissionScope.filesystem
    assert scope_for_action("diff_files") == PermissionScope.filesystem


@pytest.mark.asyncio
async def test_diff_files_rejects_paths_outside_allowed_roots(workspace, tmp_path):
    inside = workspace / "inside.txt"
    inside.write_text("safe\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    executor = ToolExecutor(
        workspace,
        home_dir=workspace,
        text_editor=TextEditorTool(workspace, home_dir=workspace),
    )
    result = await executor.run_action(
        Action(
            id="diff",
            type=ActionType.diff_files,
            args={"path_a": str(inside), "path_b": str(outside)},
        )
    )

    assert result.ok is False
    assert "Path escapes allowed roots" in result.output


def test_permissions_classify_privacy_sensitive_local_reads():
    assert scope_for_action("screenshot") == PermissionScope.screen
    assert scope_for_action("screen_context") == PermissionScope.screen
    assert scope_for_action("ocr_image") == PermissionScope.screen
    assert scope_for_action("pixel_color_at") == PermissionScope.screen
    assert scope_for_action("ui_critique") == PermissionScope.screen
    assert scope_for_action("find_on_screen") == PermissionScope.screen
    assert scope_for_action("computer", {"action": "screenshot"}) == PermissionScope.screen
    assert scope_for_action("computer", {"action": "left_click"}) is None
    assert scope_for_action("get_clipboard") == PermissionScope.clipboard
    assert scope_for_action("set_clipboard") == PermissionScope.clipboard
    # system_info is read-only static OS facts → free (no approval friction).
    assert scope_for_action("system_info") is None
    # list_processes reveals what's running → still gated behind the system scope.
    assert scope_for_action("list_processes") == PermissionScope.system
    assert scope_for_action("list_processes") == PermissionScope.system


def test_privacy_sensitive_scopes_are_not_auto_granted():
    assert can_auto_grant_scope(PermissionScope.filesystem) is True
    assert can_auto_grant_scope(PermissionScope.shell) is True
    assert can_auto_grant_scope(PermissionScope.screen) is False
    assert can_auto_grant_scope(PermissionScope.clipboard) is False
    assert can_auto_grant_scope(PermissionScope.system) is False
    assert can_auto_grant_scope(PermissionScope.mcp) is False


def test_safety_and_permissions_classify_dynamic_mcp_execution():
    s = SafetyManager()

    decision = s.evaluate(
        Action(
            id="mcp",
            type=ActionType.mcp_tool,
            args={
                "server_name": "notes",
                "tool_name": "delete_note",
                "tool_args": {"id": "abc"},
            },
        ),
        safe_mode=False,
    )
    list_servers_decision = s.evaluate(
        Action(id="mcp-list", type=ActionType.list_mcp_servers, args={}),
        safe_mode=False,
    )
    list_tools_decision = s.evaluate(
        Action(
            id="mcp-tools",
            type=ActionType.list_mcp_tools,
            args={"server_name": "notes"},
        ),
        safe_mode=False,
    )

    assert decision.danger.value == "high"
    assert decision.requires_approval is True
    assert "notes.delete_note" in decision.reason
    assert list_servers_decision.danger.value == "high"
    assert list_servers_decision.requires_approval is True
    assert "configured MCP server processes" in list_servers_decision.reason
    assert list_tools_decision.danger.value == "high"
    assert list_tools_decision.requires_approval is True
    assert "for notes" in list_tools_decision.reason
    assert scope_for_action("mcp_tool") == PermissionScope.mcp
    assert scope_for_action("list_mcp_servers") == PermissionScope.mcp
    assert scope_for_action("list_mcp_tools") == PermissionScope.mcp


def test_run_and_watch_collects_process_output_once(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))

    class FakeProcess:
        pid = 4242
        returncode = 0

        def __init__(self):
            self.communicate_calls = 0
            self.killed = False

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls > 1:
                raise AssertionError("communicate called more than once")
            return "ready\n", ""

        def kill(self):
            self.killed = True

    proc = FakeProcess()
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: proc)

    result = t.run_and_watch("serve", watch_seconds=0.5)

    assert result.ok
    assert "ready" in result.output
    assert result.data["exit_code"] == 0
    assert result.data["killed"] is False
    assert proc.killed is False
    assert proc.communicate_calls == 1


def test_file_glob_stays_inside_workspace(workspace):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('ok')", encoding="utf-8")

    result = t.file_glob("**/*.py")
    assert result.ok
    assert "src" in result.output

    with pytest.raises(Exception):
        t.file_glob("../**/*")
    with pytest.raises(Exception):
        t.file_glob(str((workspace.parent / "*.py").resolve()))


def test_safety_bash():
    s = SafetyManager()
    dec = s.evaluate(Action(id="2", type=ActionType.bash, args={"command": "echo hi"}))
    assert dec.danger.value == "high"


def test_run_tests_rewrites_bare_pytest(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return types.SimpleNamespace(returncode=0, stdout="1 passed in 0.01s", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = t.run_tests(command="pytest smoke_tests -q")

    assert result.ok
    assert seen["command"] == "python -m pytest smoke_tests -q"


def test_git_tool_runs_without_shell(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["shell"] = kwargs.get("shell")
        return types.SimpleNamespace(returncode=0, stdout=" M app.py\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = t.git("status", "--short")

    assert result.ok
    assert seen["command"] == ["git", "status", "--short"]
    assert seen["shell"] is None
    assert "app.py" in result.output


def test_git_tool_rejects_shell_metacharacters(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))

    def fail_run(*args, **kwargs):
        raise AssertionError("git command with shell metacharacters should not run")

    monkeypatch.setattr("subprocess.run", fail_run)
    result = t.git("status", "&& echo owned")

    assert result.ok is False
    assert "shell metacharacters" in result.output


def test_hung_window_check_tolerates_missing_pywin32_helper(monkeypatch):
    fake_user32 = types.SimpleNamespace(IsHungAppWindow=lambda hwnd: 0)
    fake_windll = types.SimpleNamespace(user32=fake_user32)
    fake_ctypes = types.SimpleNamespace(windll=fake_windll)
    fake_win32gui = types.SimpleNamespace()

    monkeypatch.setattr(tools_module, "ctypes", fake_ctypes)
    monkeypatch.setattr(tools_module, "win32gui", fake_win32gui)

    assert tools_module._is_hung_app_window(1234) is False


@pytest.mark.asyncio
async def test_run_action_offloads_blocking_tool(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))

    def slow_run_command(command: str):
        time.sleep(0.2)
        return types.SimpleNamespace(ok=True, output="done", base64_image=None, data=None)

    monkeypatch.setattr(t, "run_command", slow_run_command)

    started = time.monotonic()
    task = asyncio.create_task(t.run_action(Action(id="slow", type=ActionType.run_command, args={"command": "echo hi"})))
    await asyncio.sleep(0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    result = await task
    assert result.ok


@pytest.mark.asyncio
async def test_run_action_streams_run_command(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    seen = []

    async def fake_stream(command, on_chunk=None):
        if on_chunk:
            await on_chunk("hello\n")
            await on_chunk("world\n")
        return types.SimpleNamespace(ok=True, output="hello\nworld\n", base64_image=None, data=None)

    monkeypatch.setattr(t, "run_command_streaming", fake_stream)

    async def on_stream(chunk):
        seen.append(chunk)

    result = await t.run_action(
        Action(id="stream", type=ActionType.run_command, args={"command": "echo hi"}),
        on_stream=on_stream,
    )
    assert result.ok
    assert seen == ["hello\n", "world\n"]


def test_focus_window_survives_foreground_lock(workspace, monkeypatch):
    """Windows refuses SetForegroundWindow under foreground-lock (raises
    pywintypes.error). focus_window must NOT hard-fail — the window is activated
    anyway and UIA targets by title. Regression for the calc task where
    focus_window wrongly reported 'failed'."""
    import sys
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))

    def _set_fg(hwnd):
        raise RuntimeError("(0, 'SetForegroundWindow', 'foreground lock')")

    fake_win32gui = types.SimpleNamespace(
        EnumWindows=lambda cb, acc: cb(21, acc),
        IsWindowVisible=lambda hwnd: True,
        GetWindowText=lambda hwnd: "Calculator",
        IsIconic=lambda hwnd: False,
        ShowWindow=lambda hwnd, flag: None,
        BringWindowToTop=lambda hwnd: None,
        SetForegroundWindow=_set_fg,        # refused by the OS
        GetForegroundWindow=lambda: 999,    # something else is foreground
    )
    fake_client = types.SimpleNamespace(
        Dispatch=lambda name: types.SimpleNamespace(AppActivate=lambda title: True))
    fake_win32com = types.SimpleNamespace(client=fake_client)

    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(tools_module, "win32process", None)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    result = t.focus_window("Calc")
    assert result.ok is True
    assert "Calculator" in result.output
    assert t._isolated_app == "Calculator"


def test_wait_for_window_returns_visible_match(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))

    fake_win32gui = types.SimpleNamespace(
        EnumWindows=lambda callback, acc: [callback(10, acc), callback(11, acc)],
        IsWindow=lambda hwnd: hwnd == 11,
        IsWindowVisible=lambda hwnd: hwnd == 11,
        GetWindowText=lambda hwnd: "Untitled - Notepad" if hwnd == 11 else "Hidden",
        GetWindowRect=lambda hwnd: (100, 120, 500, 420),
    )
    fake_win32process = types.SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 4242))

    monkeypatch.setattr(tools_module, "win32gui", fake_win32gui)
    monkeypatch.setattr(tools_module, "win32process", fake_win32process)
    monkeypatch.setattr(tools_module, "_is_hung_app_window", lambda hwnd: False)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    result = t.wait_for_window("notepad", timeout=0.2)

    assert result.ok
    assert result.data == {"hwnd": 11, "pid": 4242, "title": "Untitled - Notepad"}
    assert t._isolated_hwnd == 11


def test_bash_gui_launch_waits_for_window_and_tracks_pid(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    t.set_isolated_hwnd(None, "Notepad")

    # Pin the precondition: no existing Notepad window, so this exercises the
    # launch+wait path (not the single-instance reuse shortcut).
    monkeypatch.setattr(t, "_iter_matching_windows", lambda title: [])
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: types.SimpleNamespace(pid=999))
    monkeypatch.setattr(
        t,
        "wait_for_window",
        lambda title, timeout=10.0, paint_seconds=0.35: ToolResult(
            ok=True,
            output="Window ready: 'Untitled - Notepad' (pid 4242)",
            data={"hwnd": 55, "pid": 4242, "title": "Untitled - Notepad"},
        ),
    )

    result = t.bash("start notepad")

    assert result.ok
    assert "Window ready" in result.output
    assert t._isolated_hwnd == 55
    assert 4242 in t._started_pids


def test_isolated_click_uses_window_rect_for_secondary_monitor(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    t.set_isolated_hwnd(55, "Notepad")
    seen = {}

    def screen_to_client(hwnd, point):
        seen["screen_point"] = point
        return (point[0] - 2000, point[1] - 300)

    fake_win32gui = types.SimpleNamespace(
        IsWindow=lambda hwnd: True,
        GetWindowRect=lambda hwnd: (2000, 300, 2600, 900),
        ScreenToClient=screen_to_client,
        PostMessage=lambda *args: seen.setdefault("post", []).append(args),
    )
    fake_win32api = types.SimpleNamespace(MAKELONG=lambda x, y: (x, y))
    fake_win32con = types.SimpleNamespace(
        WM_LBUTTONDOWN=1,
        WM_LBUTTONUP=2,
        WM_RBUTTONDOWN=3,
        WM_RBUTTONUP=4,
        MK_LBUTTON=5,
        MK_RBUTTON=6,
    )

    monkeypatch.setattr(tools_module, "win32gui", fake_win32gui)
    monkeypatch.setattr(tools_module, "win32api", fake_win32api)
    monkeypatch.setattr(tools_module, "win32con", fake_win32con)
    monkeypatch.setattr(tools_module, "_is_hung_app_window", lambda hwnd: False)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    result = t._mouse_click_isolated(640, 400, "left", 1, 1280, 800)

    assert result.ok
    assert seen["screen_point"] == (2300, 600)


def test_force_close_window_resolves_pid_from_title(workspace, monkeypatch):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    t._started_pids.add(4242)

    class NoSuchProcess(Exception):
        pass

    events = {}

    class FakeProcess:
        def __init__(self, pid):
            assert pid == 4242

        def name(self):
            return "notepad.exe"

        def kill(self):
            events["kill"] = True

        def terminate(self):
            events["terminate"] = True

        def wait(self, timeout=5):
            events["wait"] = timeout

    monkeypatch.setattr(t, "_get_hwnd_for_title", lambda title: 77)
    monkeypatch.setattr(tools_module, "win32process", types.SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 4242)))
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=FakeProcess, NoSuchProcess=NoSuchProcess))

    result = t.force_close_window(title="Notepad", force=True)

    assert result.ok
    assert "Closed 'Notepad'" in result.output
    assert events["kill"] is True
    assert 4242 not in t._started_pids


@pytest.mark.asyncio
async def test_plugin_failures_log_traceback(workspace, caplog):
    class BoomRegistry:
        def handlers(self):
            return {"browser_screenshot": lambda **kwargs: (_ for _ in ()).throw(RuntimeError("plugin exploded"))}

    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace), plugin_registry=BoomRegistry())
    action = Action(id="plugin", type=ActionType.browser_screenshot, args={})

    with caplog.at_level("ERROR"):
        result = await t.run_action(action)

    assert result.ok is False
    assert "Plugin error: plugin exploded" == result.output
    assert "Plugin handler browser_screenshot failed" in caplog.text


@pytest.mark.asyncio
async def test_delegate_coding_returns_backend_result(workspace, monkeypatch):
    class FakeBackend:
        name = "claude-code"

        def detect(self):
            return {"available": True, "detail": "ok"}

        def submit(self, brief):
            assert brief.task == "Refactor parser"
            assert "app/parser.py" in brief.files
            return types.SimpleNamespace(
                ok=True,
                summary="Refactored parser and added tests.",
                files_changed=["app/parser.py", "tests/test_parser.py"],
                cost_usd=0.0,
                session_id="sess-1",
                error="",
                to_dict=lambda: {
                    "ok": True,
                    "summary": "Refactored parser and added tests.",
                    "files_changed": ["app/parser.py", "tests/test_parser.py"],
                    "cost_usd": 0.0,
                    "session_id": "sess-1",
                    "error": "",
                },
            )

    class FakeRegistry:
        def get(self, name=None):
            return FakeBackend()

    monkeypatch.setattr("app.coding_backends.registry", FakeRegistry())
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    result = await t.run_action(
        Action(
            id="delegate",
            type=ActionType.delegate_coding,
            args={"task": "Refactor parser", "files": ["app/parser.py"]},
        )
    )

    assert result.ok is True
    assert "Delegated to claude-code" in result.output
    assert result.data["backend"] == "claude-code"
    assert "app/parser.py" in result.data["files_changed"]


@pytest.mark.asyncio
async def test_delegate_coding_reports_missing_backend(workspace, monkeypatch):
    class EmptyRegistry:
        def get(self, name=None):
            return None

    monkeypatch.setattr("app.coding_backends.registry", EmptyRegistry())
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    result = await t.run_action(
        Action(
            id="delegate-missing",
            type=ActionType.delegate_coding,
            args={"task": "Refactor parser"},
        )
    )

    assert result.ok is False
    assert "no coding backend is available" in result.output


@pytest.mark.asyncio
async def test_mouse_click_dispatches_to_background_browser_when_attached(workspace):
    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    seen = {}

    class FakeBrowser:
        is_running = True

        async def mouse_click(self, x, y, button="left", click_count=1):
            seen["mouse_click"] = (x, y, button, click_count)

    t.set_background_browser(FakeBrowser())
    result = await t.run_action(Action(id="bg-click", type=ActionType.mouse_click, args={"x": 12, "y": 34}))

    assert result.ok
    assert seen["mouse_click"] == (12, 34, "left", 1)


@pytest.mark.asyncio
async def test_mouse_click_uses_desktop_path_without_background_browser(workspace, monkeypatch):
    calls = {}
    pg = types.SimpleNamespace(
        moveTo=lambda *a, **k: calls.setdefault("moveTo", []).append((a, k)),
        click=lambda *a, **k: calls.setdefault("click", []).append((a, k)),
        size=lambda: (1920, 1080),
        easeInOutQuad=lambda n: n,
    )
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", pg)
    monkeypatch.setattr("app.tools._flash_pointer", lambda *args, **kwargs: None)
    monkeypatch.setattr("time.sleep", lambda s: None)

    t = ToolExecutor(workspace, text_editor=TextEditorTool(workspace))
    t._background_mode = False
    result = await t.run_action(Action(id="desktop-click", type=ActionType.mouse_click, args={"x": 12, "y": 34}))

    assert result.ok
    assert calls["click"]
