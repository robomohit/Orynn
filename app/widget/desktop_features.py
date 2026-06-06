"""Desktop-native features that round out the AI Computer widget into a
shippable product.

Implements (from research-deferred list):
  * Window-snap layouts            — "coding", "research", "meeting" presets
  * Autostart on Windows login     — HKCU Run-key registry toggle
  * Crash recovery / session save  — persist last goal, offer resume
  * "Explain this screen" hotkey   — screenshot + describe via vision model
  * Telemetry-off promise          — exposed as a settings flag (always-off)

All Windows-native; no extra pip deps beyond what's already required.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from ..state_store import read_json, workspace_state_path, write_json


# ─────────────────────────────────────────────────────────────────────────────
# WINDOW-SNAP LAYOUTS
# ─────────────────────────────────────────────────────────────────────────────
def list_visible_windows() -> list[dict]:
    """Return [{'hwnd', 'title', 'exe'}] of user-visible top-level windows."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwm = ctypes.windll.dwmapi

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    results: list[dict] = []
    DWMWA_CLOAKED = 14
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SKIP = {"Program Manager", "Windows Input Experience",
            "Microsoft Text Input Application", "Settings", "Search"}

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cloaked = wintypes.DWORD(0)
            dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED,
                                      ctypes.byref(cloaked),
                                      ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buf, length + 2)
            title = buf.value
            if not title or title in SKIP:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = ""
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                     False, pid.value)
            if h:
                ebuf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                kernel32.QueryFullProcessImageNameW(h, 0, ebuf,
                                                    ctypes.byref(size))
                exe = ebuf.value
                kernel32.CloseHandle(h)
            results.append({"hwnd": int(hwnd), "title": title, "exe": exe})
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return results


def foreground_window_info() -> dict:
    """Return title/exe/rect for the current foreground top-level window."""
    try:
        import uiautomation as uia
    except ImportError:
        return {}
    _ensure_uia_config(uia)
    try:
        ctrl = uia.GetForegroundControl()
        if ctrl is None:
            return {}
        hwnd = int(getattr(ctrl, "NativeWindowHandle", 0) or 0)
        title = (getattr(ctrl, "Name", "") or "").strip()
        exe = ""
        if hwnd:
            try:
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
                h = kernel32.OpenProcess(0x1000, False, pid.value)
                if h:
                    ebuf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    kernel32.QueryFullProcessImageNameW(h, 0, ebuf, ctypes.byref(size))
                    exe = ebuf.value
                    kernel32.CloseHandle(h)
            except Exception:
                exe = ""
        return {
            "hwnd": hwnd,
            "title": title,
            "exe": exe,
            "rect": _onscreen_rect(ctrl),
        }
    except Exception:
        return {}


def primary_workarea() -> tuple[int, int, int, int]:
    """Returns (x, y, width, height) of the primary monitor work-area
    (excludes taskbar)."""
    user32 = ctypes.windll.user32
    SPI_GETWORKAREA = 0x0030
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return (rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top)


def _set_window_pos(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """SetWindowPos with SWP_NOZORDER + SWP_SHOWWINDOW."""
    user32 = ctypes.windll.user32
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetWindowPos(
        wintypes.HWND(hwnd), wintypes.HWND(0),
        x, y, w, h, SWP_NOZORDER | SWP_SHOWWINDOW))


# Built-in named layouts. Each layout maps a friendly slot name → (x, y, w, h)
# as fractions of the work-area, and a list of (slot, exe_keyword) targets.
LAYOUTS = {
    "coding": {
        "description": "VS Code/Cursor left, browser right",
        "slots": {
            "left":  (0.0, 0.0, 0.55, 1.0),
            "right": (0.55, 0.0, 0.45, 1.0),
        },
        "targets": [
            ("left",  ("code.exe", "cursor.exe", "windsurf.exe",
                       "antigravity.exe", "devenv.exe")),
            ("right", ("chrome.exe", "msedge.exe", "firefox.exe",
                       "brave.exe", "comet.exe")),
        ],
    },
    "research": {
        "description": "Browser large, notes/notepad small",
        "slots": {
            "main": (0.0, 0.0, 0.7, 1.0),
            "side": (0.7, 0.0, 0.3, 1.0),
        },
        "targets": [
            ("main", ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                      "comet.exe")),
            ("side", ("notepad.exe", "notion.exe", "obsidian.exe")),
        ],
    },
    "standup": {
        "description": "Calendar + Teams/Slack + browser",
        "slots": {
            "tl": (0.0, 0.0, 0.5, 0.5),
            "tr": (0.5, 0.0, 0.5, 0.5),
            "bl": (0.0, 0.5, 0.5, 0.5),
            "br": (0.5, 0.5, 0.5, 0.5),
        },
        "targets": [
            ("tl", ("outlook.exe", "thunderbird.exe")),
            ("tr", ("teams.exe", "slack.exe", "discord.exe")),
            ("bl", ("chrome.exe", "msedge.exe")),
            ("br", ("notepad.exe", "code.exe")),
        ],
    },
}


def apply_layout(layout_name: str) -> dict:
    """Snap currently-open windows into the named layout. Returns a summary."""
    layout = LAYOUTS.get(layout_name)
    if layout is None:
        return {"ok": False, "error": f"unknown layout '{layout_name}'"}
    wx, wy, ww, wh = primary_workarea()
    wins = list_visible_windows()
    moved: list[dict] = []
    for slot, keywords in layout["targets"]:
        # Find first window whose exe basename matches a keyword
        target = None
        for w in wins:
            base = os.path.basename((w.get("exe") or "")).lower()
            if any(k.lower() == base for k in keywords):
                target = w
                break
        if target is None:
            continue
        fx, fy, fw, fh = layout["slots"][slot]
        x = wx + int(ww * fx)
        y = wy + int(wh * fy)
        w = int(ww * fw)
        h = int(wh * fh)
        ok = _set_window_pos(target["hwnd"], x, y, w, h)
        moved.append({"slot": slot, "title": target["title"][:50],
                      "exe": os.path.basename(target.get("exe") or ""),
                      "ok": ok})
    return {"ok": True, "layout": layout_name,
            "description": layout["description"], "moved": moved}


# ─────────────────────────────────────────────────────────────────────────────
# AUTOSTART (HKCU\Software\Microsoft\Windows\CurrentVersion\Run)
# ─────────────────────────────────────────────────────────────────────────────
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "AI_Computer"


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            try:
                winreg.QueryValueEx(k, _AUTOSTART_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False


def set_autostart(enable: bool, launch_cmd: Optional[str] = None) -> bool:
    """Toggle autostart. `launch_cmd` defaults to `pythonw run_desktop.py`
    in the current Ai_computer folder."""
    if launch_cmd is None:
        # Use pythonw to launch without a console window
        py = sys.executable
        if py.endswith("python.exe"):
            py = py[:-len("python.exe")] + "pythonw.exe"
        repo = Path(__file__).resolve().parents[2]
        script = repo / "run_desktop.py"
        launch_cmd = f'"{py}" "{script}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _AUTOSTART_NAME, 0, winreg.REG_SZ,
                                  launch_cmd)
            else:
                try:
                    winreg.DeleteValue(k, _AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        print(f"[desktop_features] autostart toggle failed: {exc}", flush=True)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CRASH RECOVERY — persist last in-flight goal so we can offer to resume
# ─────────────────────────────────────────────────────────────────────────────
def _state_path() -> Path:
    return workspace_state_path("widget_state.json")


def save_pending_task(goal: str, mode: str, task_id: str = "") -> None:
    try:
        payload = {
            "goal": goal,
            "mode": mode,
            "task_id": task_id,
            "ts": time.time(),
        }
        write_json(_state_path(), payload)
    except Exception:
        pass


def clear_pending_task() -> None:
    try:
        if _state_path().exists():
            _state_path().unlink()
    except Exception:
        pass


def load_pending_task() -> Optional[dict]:
    """Return the previously-pending task if it's recent (<24h) and unfinished."""
    try:
        data = read_json(_state_path(), None)
        if not isinstance(data, dict):
            return None
        # Stale > 24h → drop
        if time.time() - data.get("ts", 0) > 24 * 3600:
            clear_pending_task()
            return None
        return data
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TELEMETRY — always off. Single source of truth for the privacy panel.
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# UIA TREE NAVIGATION — find controls by name/role, no pixel coords
# Uses Microsoft IUIAutomation COM via the `uiautomation` PyPI lib if present.
# ─────────────────────────────────────────────────────────────────────────────
# Electron/Chromium apps nest their DOM deeply (Discord's search box sits at
# UIA depth 12+). A shallow walk silently misses real controls, so we go deep.
_UIA_MAX_DEPTH = 40

_uia_configured = False


def _ensure_uia_config(uia) -> None:
    """One-time tuning so UIA calls return fast instead of using the library's
    default 10 s search timeout / 0.5 s retry interval."""
    global _uia_configured
    if _uia_configured:
        return
    for setter, val in (("SetGlobalSearchTimeout", 1.0),
                        ("SetGlobalSearchInterval", 0.05)):
        try:
            getattr(uia, setter)(val)
        except Exception:
            pass
    _uia_configured = True


def _uia_root(app_hint: str = "", fallback_foreground: bool = True):
    """Return the right top-level window to search for an app.

    RANKS all windows whose title contains the hint instead of taking the first
    substring match — otherwise noise windows that merely MENTION the app steal
    the target. The classic culprit: the "Activate Windows. Go to Settings..."
    watermark (an empty Pane) outranks the real Settings window in raw iteration
    order, so every UIA op silently searched an empty pane. We prefer an exact
    title, a real WindowControl, and a window that actually has children; and we
    hard-demote the activation watermark. Falls back to the foreground window.
    """
    import uiautomation as uia
    if app_hint:
        hint = app_hint.lower().strip()
        # Foreground window handle — when several windows of the same app are
        # open (e.g. 5 Notepads), prefer the one the user is actually looking at.
        fg_handle = 0
        try:
            fg_handle = int(uia.GetForegroundControl().NativeWindowHandle or 0)
        except Exception:
            pass
        best, best_score = None, -1
        for top in uia.GetRootControl().GetChildren():
            try:
                low = (top.Name or "").strip().lower()
                if not low or hint not in low:
                    continue
                score = 100 if low == hint else (60 if low.startswith(hint) else 30)
                try:
                    if top.ControlTypeName == "WindowControl":
                        score += 20
                except Exception:
                    pass
                try:
                    if top.GetChildren():       # has real content (not an empty pane)
                        score += 25
                except Exception:
                    pass
                try:                            # the active window wins ties
                    if fg_handle and int(top.NativeWindowHandle or 0) == fg_handle:
                        score += 40
                except Exception:
                    pass
                if "activate windows" in low:   # the activation watermark, never a target
                    score -= 200
                if score > best_score:
                    best, best_score = top, score
            except Exception:
                continue
        if best is not None:
            return best
    return uia.GetForegroundControl() if fallback_foreground else None


def _score_match(query: str, name: str, aid: str, role: str) -> int:
    """Higher = better match."""
    q = query.lower().strip()
    n = (name or "").lower()
    a = (aid or "").lower()
    r = (role or "").lower()
    if not q:
        return 0
    if n == q:    return 100   # exact name
    if a == q:    return 95    # exact automation id
    if n.startswith(q): return 70
    if q in n:    return 50
    if q in a:    return 40
    if q in r:    return 20
    # Word-boundary match in name (e.g. "send" in "Send button")
    if any(w == q for w in n.split()):
        return 60
    return 0


def find_ui_elements(query: str, app_hint: str = "",
                     limit: int = 5) -> dict:
    """Return up to `limit` matching controls, ranked by match score."""
    try:
        import uiautomation as uia  # noqa: F401
    except ImportError:
        return {"ok": False,
                "error": "uiautomation not installed (pip install uiautomation)"}
    _ensure_uia_config(uia)
    try:
        root = _uia_root(app_hint)
        candidates: list[tuple[int, dict]] = []
        perfect = [0]  # count of exact (score==100) hits found so far

        def walk(ctrl, depth=0):
            if depth > _UIA_MAX_DEPTH or perfect[0] >= limit:
                return
            try:
                name = ctrl.Name or ""
                aid = ctrl.AutomationId or ""
                role = ctrl.ControlTypeName or ""
                # Skip the root container itself (depth 0): the top-level window
                # pane's name contains the app + current server/channel, so it
                # would match substring queries and steal the real target.
                score = _score_match(query, name, aid, role) if depth > 0 else 0
                if score > 0:
                    rect = ctrl.BoundingRectangle
                    has_rect = rect.right > rect.left and rect.bottom > rect.top
                    # Electron/Chromium controls (Discord servers/channels) often
                    # report a 0x0 rect and IsOffscreen even when they're visible
                    # and clickable via Invoke/Select patterns. Keep them — just
                    # rank them below on-screen matches so a visible duplicate
                    # wins ties. uia_click scrolls them into view before acting.
                    try:
                        offscreen = bool(ctrl.IsOffscreen)
                    except Exception:
                        offscreen = False
                    eff = score - (8 if (offscreen or not has_rect) else 0)
                    candidates.append((eff, {
                        "name": name,
                        "automation_id": aid,
                        "control_type": role,
                        "left": rect.left if has_rect else 0,
                        "top": rect.top if has_rect else 0,
                        "x": (rect.left + rect.right) // 2 if has_rect else 0,
                        "y": (rect.top + rect.bottom) // 2 if has_rect else 0,
                        "width": max(0, rect.right - rect.left),
                        "height": max(0, rect.bottom - rect.top),
                        "score": score,
                        "offscreen": offscreen or not has_rect,
                    }))
                    if score >= 100 and has_rect and not offscreen:
                        perfect[0] += 1
                for child in ctrl.GetChildren():
                    if perfect[0] >= limit:
                        break
                    walk(child, depth + 1)
            except Exception:
                pass

        walk(root)
        candidates.sort(key=lambda x: -x[0])
        items = [c for _, c in candidates[:limit]]
        if not items:
            return {"ok": False, "error": f"no UIA control matched '{query}'"}
        return {"ok": True, "items": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def find_ui_element(query: str, app_hint: str = "") -> dict:
    """Single-result variant — returns the highest-scoring match."""
    res = find_ui_elements(query, app_hint, limit=1)
    if not res.get("ok"):
        return res
    item = res["items"][0]
    return {"ok": True, **item}


# ─────────────────────────────────────────────────────────────────────────────
# ELECTRON ACCESSIBILITY UNLOCK
#
# Chromium-based desktop apps (VS Code, Slack, Discord, Teams, Notion, Spotify,
# Cursor, Antigravity, Comet, etc.) ship with their UI Automation tree empty
# until an "assistive technology" is detected. Our UIA agent looks like nothing
# to them. The fix is a documented Chromium command-line switch:
#
#     --force-renderer-accessibility
#
# When the app is launched with this flag, every DOM element shows up in the
# Windows UIA tree as a real control. Our existing find_ui_element /
# click_ui_element helpers then work on Electron apps with zero extra code.
#
# We DO NOT implement DLL injection. AV will flag it, anti-cheat will ban,
# the maintenance burden is unacceptable for a consumer product.
# We DO NOT implement Chrome DevTools Protocol by default. The flag stops
# working across Electron upgrades (electron/electron#41325, #10445); we keep
# detection here so power users can opt in if they want.
# ─────────────────────────────────────────────────────────────────────────────

# Known Electron exe basenames — the agent uses this to suggest a relaunch
# instead of fumbling vision on a blank UIA tree.
ELECTRON_EXES = {
    "code.exe", "cursor.exe", "windsurf.exe", "antigravity.exe",
    "slack.exe", "discord.exe", "teams.exe", "ms-teams.exe",
    "notion.exe", "spotify.exe", "obsidian.exe", "github desktop.exe",
    "1password.exe", "figma.exe", "linear.exe", "raycast.exe",
    "trello.exe", "whatsapp.exe", "signal.exe", "comet.exe",
}


def resolve_app_exe(name_or_path: str) -> str:
    """Resolve a bare app name (e.g. 'Discord' or 'Discord.exe') to the full
    .exe path of a currently-running window's process. Agents rarely know the
    full install path; this lets electron_check/electron_unlock accept a name.
    Returns the input unchanged if nothing matches.
    """
    if not name_or_path:
        return name_or_path
    if os.path.exists(name_or_path):
        return name_or_path
    base = os.path.basename(name_or_path).lower()
    base_exe = base if base.endswith(".exe") else base + ".exe"
    stem = base_exe[:-4]
    try:
        windows = list_visible_windows()
        # 1. exact exe basename match
        for w in windows:
            exe = w.get("exe") or ""
            if exe and os.path.basename(exe).lower() == base_exe:
                return exe
        # 2. app name appears in a window title (e.g. '... - Discord')
        for w in windows:
            if stem and stem in (w.get("title") or "").lower():
                exe = w.get("exe") or ""
                if exe:
                    return exe
    except Exception:
        pass
    return name_or_path


def electron_hint_for_app(app_hint: str) -> Optional[dict]:
    """If `app_hint` resolves to a running Electron app, return a hint telling
    the agent it can unlock the app's DOM for UIA by relaunching it with
    --force-renderer-accessibility. None for native apps (where a UIA/OCR miss
    is genuinely a miss, not a hidden-DOM problem). Used on hard resolver misses
    so the agent can self-heal on Electron instead of blindly escalating."""
    try:
        exe = resolve_app_exe(app_hint or "")
        if exe and is_electron_app(exe):
            return {
                "exe": exe,
                "tip": (f"{app_hint or 'This app'} is an Electron app — its DOM "
                        "is invisible to UIA until unlocked. Relaunch it with "
                        "--force-renderer-accessibility (electron_unlock / "
                        "/api/desktop/electron/relaunch) and the controls become "
                        "directly clickable by name."),
            }
    except Exception:
        pass
    return None


def count_app_controls(app_hint: str, cap: int = 60) -> int:
    """Count UIA controls under an app's top-level window (stops early at `cap`).
    Used to tell whether an Electron app already exposes a rich accessibility
    tree, so we can skip a disruptive --force-renderer-accessibility relaunch.
    """
    try:
        import uiautomation as uia  # noqa: F401
    except ImportError:
        return 0
    _ensure_uia_config(uia)
    try:
        # Only count when a top-level window actually matches app_hint — do NOT
        # fall back to the foreground window (that would falsely report a closed
        # app as "already accessible"). _uia_root ranks candidates so a noise
        # window (e.g. the "Activate Windows" watermark) can't steal the match.
        root = _uia_root(app_hint, fallback_foreground=False) if app_hint else None
        if root is None:
            return 0
        n = [0]

        def w(c, d=0):
            if d > _UIA_MAX_DEPTH or n[0] >= cap:
                return
            try:
                n[0] += 1
                for ch in c.GetChildren():
                    if n[0] >= cap:
                        break
                    w(ch, d + 1)
            except Exception:
                pass

        w(root)
        return n[0]
    except Exception:
        return 0


_INTERACTIVE_CTRL_TYPES = {
    "ButtonControl", "ListItemControl", "TabItemControl", "MenuItemControl",
    "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "TreeItemControl",
    "ComboBoxControl", "SplitButtonControl", "EditControl",
}

# NOTE: deliberately NOT "system" — that's a real Settings nav item (System ›
# About). Only the unambiguous title-bar buttons.
_CHROME_NAMES = {"minimize", "maximize", "restore", "close"}


def _is_chrome_control(name: str) -> bool:
    """Window-chrome / non-label noise that shouldn't appear in the control menu:
    title-bar buttons (Minimize/Maximize/Restore/Close, incl. 'Close Settings')
    and bare camelCase type names with no real label ('BreadcrumbBarItemButton')."""
    low = name.lower()
    first = low.split(" ", 1)[0]
    if first in _CHROME_NAMES:
        return True
    # A single CamelCase token with no spaces is a control-type name, not a label.
    if " " not in name and len(name) > 8 and name[:1].isupper() and name.isalnum():
        return True
    return False


def survey_app_controls(
    app_hint: str,
    cap: int = 90,
    max_names: int = 28,
    *,
    fallback_foreground: bool = False,
) -> Dict[str, Any]:
    """ONE UIA tree walk that returns both the control COUNT and the NAMES of the
    interactive controls (buttons, tabs, list items, menu items, links, fields)
    the agent can click/type by name. Handing the model this 'menu' up front stops
    it guessing control names that don't exist (e.g. searching 'Search' in
    Settings) — far more accurate AND faster (fewer wasted misses)."""
    out: Dict[str, Any] = {"count": 0, "controls": []}
    try:
        import uiautomation as uia
    except ImportError:
        return out
    _ensure_uia_config(uia)
    try:
        root = _uia_root(app_hint, fallback_foreground=fallback_foreground) if (app_hint or fallback_foreground) else None
        if root is None:
            return out
        n = [0]
        names: list[str] = []
        seen: set[str] = set()

        def walk(c, d=0):
            if d > _UIA_MAX_DEPTH or n[0] >= cap or len(names) >= max_names:
                return
            try:
                n[0] += 1
                if c.ControlTypeName in _INTERACTIVE_CTRL_TYPES:
                    nm = (c.Name or "").strip()
                    if (nm and len(nm) <= 40 and nm.lower() not in seen
                            and not _is_chrome_control(nm)):
                        seen.add(nm.lower())
                        names.append(nm)
                for ch in c.GetChildren():
                    if n[0] >= cap or len(names) >= max_names:
                        break
                    walk(ch, d + 1)
            except Exception:
                pass

        walk(root)
        out["count"] = n[0]
        out["controls"] = names
        return out
    except Exception:
        return out


def is_electron_app(exe_path: str) -> bool:
    """Returns True if the .exe path looks like an Electron app.
    Heuristics:
      * known exe basename
      * `resources/app.asar` next to the exe
      * sibling `chrome_100_percent.pak` (Chromium asset bundle)
    """
    if not exe_path:
        return False
    base = os.path.basename(exe_path).lower()
    if base in ELECTRON_EXES:
        return True
    try:
        d = Path(exe_path).parent
        if (d / "resources" / "app.asar").exists():
            return True
        # Chromium content asset
        if (d / "chrome_100_percent.pak").exists():
            return True
    except Exception:
        pass
    return False


def relaunch_with_accessibility(exe_path: str,
                                args: list[str] | None = None,
                                also_remote_debug: bool = False) -> dict:
    """Re-launch an Electron app with --force-renderer-accessibility so its
    DOM exposes as a UIA tree. If `also_remote_debug` is True, also tack on
    --remote-debugging-port=9222 for the CDP power-user path.

    NOTE: This does NOT kill an already-running instance. The agent should
    propose this to the user and let them close + relaunch themselves.
    Returns the launched subprocess PID (or error).
    """
    import subprocess
    if not exe_path or not os.path.exists(exe_path):
        return {"ok": False, "error": f"exe not found: {exe_path}"}
    cmd = [exe_path]
    cmd += list(args or [])
    cmd.append("--force-renderer-accessibility")
    if also_remote_debug:
        cmd.append("--remote-debugging-port=9222")
    try:
        proc = subprocess.Popen(cmd, close_fds=True)
        return {
            "ok": True,
            "pid": proc.pid,
            "exe": exe_path,
            "flags": cmd[len(args or [])+1:],
            "note": ("Existing instance (if any) was NOT terminated — "
                     "Electron apps fight to single-instance themselves; "
                     "if the new window doesn't appear, close the running "
                     "copy first."),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def smart_uia_find_with_unlock(query: str, app_hint: str = "") -> dict:
    """UIA find that, on empty result for an Electron app, surfaces a clear
    "relaunch with --force-renderer-accessibility" suggestion instead of
    falling back to vision.

    Identifies the foreground app, checks if it's Electron, and if so
    returns hit + an `electron_hint` field even when the find succeeded
    (so the agent can choose to relaunch for richer access)."""
    hit = find_ui_element(query, app_hint)
    # Determine current foreground exe to advise on
    try:
        import uiautomation as uia
        fg = uia.GetForegroundControl()
        # Walk up to top-level control
        top = fg
        while top and top.GetParentControl():
            if top.GetParentControl() == uia.GetRootControl():
                break
            top = top.GetParentControl()
        exe = ""
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = top.NativeWindowHandle if top else 0
            pid = wintypes.DWORD(0)
            if hwnd:
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
                if h:
                    ebuf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    kernel32.QueryFullProcessImageNameW(
                        h, 0, ebuf, ctypes.byref(size))
                    exe = ebuf.value
                    kernel32.CloseHandle(h)
        except Exception:
            pass
        if exe and is_electron_app(exe):
            hit = dict(hit) if isinstance(hit, dict) else {"ok": False}
            hit["electron_hint"] = {
                "exe": exe,
                "tip": ("This is an Electron app — its DOM is invisible "
                        "to UIA by default. Call /api/desktop/electron/"
                        "relaunch with this exe path to launch it with "
                        "--force-renderer-accessibility, then retry."),
            }
        return hit
    except Exception:
        return hit


def click_ui_element(query: str, app_hint: str = "",
                     button: str = "left") -> dict:
    """Find a control then physically click its center via pyautogui."""
    hit = find_ui_element(query, app_hint)
    if not hit.get("ok"):
        return hit
    try:
        import pyautogui
        pyautogui.click(hit["x"], hit["y"], button=button)
        return {"ok": True, "clicked": {"name": hit["name"],
                                         "x": hit["x"], "y": hit["y"],
                                         "control_type": hit["control_type"]}}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "found_at": hit}


def _find_uia_control(query: str, app_hint: str = ""):
    """Internal: walk the UIA tree and return the live control object
    (not just a dict of coords) for the best match. Returns (ctrl, info)
    or (None, error_dict)."""
    try:
        import uiautomation as uia  # noqa: F401
    except ImportError:
        return None, {"ok": False,
                      "error": "uiautomation not installed (pip install uiautomation)"}
    _ensure_uia_config(uia)
    try:
        root = _uia_root(app_hint)

        # ── Fast path: native exact-name FindFirst (runs in UIA's C++ core,
        # ~2x faster than the Python walk below). The walk also early-exits on
        # the first exact (score-100) hit, so this returns the same control —
        # just quicker. maxSearchSeconds=0 = a single immediate search, so a
        # miss returns fast and falls through to the scored walk (which handles
        # fuzzy / AutomationId / role matches). Skipped for chrome/titlebar
        # names so we never grab the window's Close over a real "Close" button.
        q = (query or "").strip()
        if root is not None and q and not _is_chrome_control(q):
            try:
                fast = root.Control(searchDepth=0xFFFFFFFF, Name=q)
                if fast.Exists(maxSearchSeconds=0, searchIntervalSeconds=0):
                    r = fast.BoundingRectangle
                    has_rect = r.right > r.left and r.bottom > r.top
                    return fast, {
                        "name": fast.Name or "",
                        "automation_id": fast.AutomationId or "",
                        "control_type": fast.ControlTypeName or "",
                        "x": (r.left + r.right) // 2 if has_rect else 0,
                        "y": (r.top + r.bottom) // 2 if has_rect else 0,
                        "score": 100,
                        "offscreen": not has_rect,
                    }
            except Exception:
                pass

        best = [0, None, None]  # score, ctrl, info (mutable for early-exit)

        def walk(ctrl, depth=0):
            # Stop the entire walk the instant we have a perfect match —
            # nothing can beat an exact-name hit (score 100).
            if depth > _UIA_MAX_DEPTH or best[0] >= 100:
                return
            try:
                name = ctrl.Name or ""
                aid = ctrl.AutomationId or ""
                role = ctrl.ControlTypeName or ""
                # Skip the root container (depth 0) — see note in find_ui_elements.
                score = _score_match(query, name, aid, role) if depth > 0 else 0
                if score > 0:
                    rect = ctrl.BoundingRectangle
                    has_rect = rect.right > rect.left and rect.bottom > rect.top
                    try:
                        offscreen = bool(ctrl.IsOffscreen)
                    except Exception:
                        offscreen = False
                    # Keep offscreen/0-size controls (Electron lists report them
                    # even when invokable) but rank below on-screen matches.
                    eff = score - (8 if (offscreen or not has_rect) else 0)
                    if eff > best[0]:
                        info = {
                            "name": name, "automation_id": aid,
                            "control_type": role,
                            "x": (rect.left + rect.right) // 2 if has_rect else 0,
                            "y": (rect.top + rect.bottom) // 2 if has_rect else 0,
                            "score": score,
                            "offscreen": offscreen or not has_rect,
                        }
                        best[0], best[1], best[2] = eff, ctrl, info
                for child in ctrl.GetChildren():
                    if best[0] >= 100:
                        break
                    walk(child, depth + 1)
            except Exception:
                pass

        walk(root)
        if best[1] is None:
            return None, {"ok": False, "error": f"no UIA control matched '{query}'"}
        return best[1], best[2]
    except Exception as exc:
        return None, {"ok": False, "error": str(exc)}


def _dwm_visible_rect(hwnd: int) -> Optional[dict]:
    """The window's VISIBLE bounds via DWM (DWMWA_EXTENDED_FRAME_BOUNDS).

    GetWindowRect / UIA BoundingRectangle include the ~7px invisible resize
    border Windows adds around top-level windows, so a glow drawn on that rect
    sits too wide and too low. DWM's extended frame bounds is the real visible
    edge — the glow then hugs the window precisely. Returns None on failure."""
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        rect = wintypes.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect))
        if hr == 0:
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            if w > 0 and h > 0:
                return {"left": int(rect.left), "top": int(rect.top),
                        "width": w, "height": h}
    except Exception:
        pass
    return None


def app_window_rect(app_hint: str, *, fallback_foreground: bool = False) -> dict:
    """On-screen bounds of the top-level window matching app_hint (its title
    substring). Used to draw a glowing edge around the whole app the agent is
    working in. Uses the same ranked top-level-window resolver as UIA find/click
    so overlays and OCR crops follow the real target window. Zeros if unavailable."""
    try:
        import uiautomation as uia  # noqa: F401
    except ImportError:
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    _ensure_uia_config(uia)
    try:
        top = _uia_root(app_hint, fallback_foreground=fallback_foreground) if (app_hint or fallback_foreground) else None
        if top is None:
            return {"left": 0, "top": 0, "width": 0, "height": 0}
        # Prefer DWM's visible bounds so the glow hugs the real window edge
        # (BoundingRectangle includes the invisible resize border).
        hwnd = int(getattr(top, "NativeWindowHandle", 0) or 0)
        vis = _dwm_visible_rect(hwnd)
        if vis is not None:
            return vis
        return _onscreen_rect(top)
    except Exception:
        return {"left": 0, "top": 0, "width": 0, "height": 0}


async def _win_ocr_async(left: int, top: int, width: int, height: int) -> list:
    import io
    import winsdk.windows.media.ocr as _ocr
    import winsdk.windows.graphics.imaging as _img
    import winsdk.windows.storage.streams as _st
    from PIL import ImageGrab
    region = (left, top, left + width, top + height)
    img = ImageGrab.grab(region).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    data = buf.getvalue()
    stream = _st.InMemoryRandomAccessStream()
    writer = _st.DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(data)
    await writer.store_async()
    decoder = await _img.BitmapDecoder.create_async(stream)
    bmp = await decoder.get_software_bitmap_async()
    engine = _ocr.OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    result = await engine.recognize_async(bmp)
    out = []
    for line in result.lines:
        for w in line.words:
            r = w.bounding_rect
            out.append({
                "text": w.text,
                "x": int(left + r.x + r.width / 2),
                "y": int(top + r.y + r.height / 2),
                "w": int(r.width), "h": int(r.height),
            })
    return out


def win_ocr_words(left: int, top: int, width: int, height: int) -> list:
    """Windows-native OCR of a screen region -> [{text, x, y, w, h}] in SCREEN
    coords. Uses Windows.Media.Ocr (no external binary). [] if unavailable."""
    if width <= 0 or height <= 0:
        return []
    try:
        import asyncio
        return asyncio.run(_win_ocr_async(left, top, width, height))
    except Exception:
        return []


def ocr_available() -> bool:
    try:
        import winsdk.windows.media.ocr as _ocr  # noqa: F401
        return _ocr.OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        return False


def _ocr_norm(s: str) -> str:
    """Normalise text for OCR matching: lowercase, drop the Windows menu
    accelerator '&' ("&File" -> "file"), and strip leading/trailing punctuation
    so "Find...", "Edit," and "(Reply)" all compare as their bare label. Inner
    characters are kept so multi-word labels still line up."""
    s = (s or "").replace("&", "").strip().lower()
    return s.strip(".,:;!?()[]{}<>\"'`…-—–/\\|")


def ocr_find_in_app(query: str, app_hint: str = "") -> dict:
    """OCR FALLBACK: find the on-screen pixel centre of text matching `query`
    inside the app window — used when UIA has no accessible control. Local +
    fast (no vision model). Returns {ok, x, y, matched, score}."""
    q = _ocr_norm(query)
    if not q:
        return {"ok": False, "error": "empty query"}
    wr = app_window_rect(app_hint)
    if not wr.get("width"):
        try:
            from PIL import ImageGrab
            sw, sh = ImageGrab.grab().size
            wr = {"left": 0, "top": 0, "width": sw, "height": sh}
        except Exception:
            return {"ok": False, "error": "no app window / screen"}
    words = win_ocr_words(wr["left"], wr["top"], wr["width"], wr["height"])
    if not words:
        return {"ok": False, "error": "ocr unavailable or no text found"}
    best, best_score = None, 0
    # single-word matches
    for wd in words:
        t = _ocr_norm(wd["text"])
        if not t:
            continue
        if t == q:
            score = 100
        elif t.startswith(q):
            score = 82  # "Find" -> "Find..." (prefix is safe)
        elif len(t) >= 3 and t in q:
            score = 44  # OCR split a label the query spans (reverse contain)
        else:
            # Deliberately NO bare `q in t`: a single OCR token has no internal
            # word boundary, so "view" would wrongly match inside "teview" and
            # send a fallback click to the wrong place. Prefer a clean miss (the
            # agent then escalates to vision) over a confident wrong click.
            score = 0
        if score > best_score:
            best_score, best = score, wd
    # consecutive-word phrase matches (same line)
    for n in (2, 3, 4):
        for i in range(len(words) - n + 1):
            grp = words[i:i + n]
            if max(g["y"] for g in grp) - min(g["y"] for g in grp) > 16:
                continue
            phrase = " ".join(_ocr_norm(g["text"]) for g in grp).strip()
            if phrase == q:
                score = 100 + n
            elif re.search(r"\b" + re.escape(q) + r"\b", phrase):
                # whole-word(s) hit only — avoids "view" matching "teview"
                score = 74
            else:
                score = 0
            if score > best_score:
                best_score = score
                best = {
                    "text": " ".join(g["text"] for g in grp),
                    "x": sum(g["x"] for g in grp) // len(grp),
                    "y": sum(g["y"] for g in grp) // len(grp),
                }
    if best and best_score >= 44:
        return {"ok": True, "x": best["x"], "y": best["y"],
                "matched": best["text"], "score": best_score}
    return {"ok": False, "error": f"no OCR text matched '{query}'"}


def _onscreen_rect(ctrl) -> dict:
    """Current on-screen bounds of a live control as {left, top, width, height}.
    Read AFTER ScrollIntoView so virtualized/offscreen controls report real
    coordinates. Returns zeros if unavailable."""
    try:
        r = ctrl.BoundingRectangle
        w = max(0, r.right - r.left)
        h = max(0, r.bottom - r.top)
        if w > 0 and h > 0:
            return {"left": r.left, "top": r.top, "width": w, "height": h}
    except Exception:
        pass
    return {"left": 0, "top": 0, "width": 0, "height": 0}


def type_into_ui_element(query: str, text: str, app_hint: str = "",
                         clear_first: bool = False,
                         submit: bool = False) -> dict:
    """Find an editable control by name/id and enter text reliably and FAST.

    Primary method is focus + clipboard paste: it is instant regardless of
    text length AND fires the native input/paste events that modern web-app
    inputs (React/Electron contenteditable — Discord, Slack, Notion, VS Code)
    listen for. Plain UIA ValuePattern.SetValue updates the DOM value but does
    NOT trigger React's onChange, so the app's internal state stays empty and
    Enter sends nothing — which is exactly the Discord bug we hit. Paste avoids
    that on every kind of input.

    If `submit` is True, presses Enter afterwards in the focused control so a
    "type and send/search" is a single reliable action.
    """
    import uiautomation as uia
    ctrl, info = _find_uia_control(query, app_hint)
    if ctrl is None:
        return info  # error dict
    # Bring virtualized/offscreen inputs into view first (no-op if N/A).
    try:
        sip = ctrl.GetScrollItemPattern()
        if sip is not None:
            sip.ScrollIntoView()
            time.sleep(0.05)
    except Exception:
        pass
    # 1. Focus the control via UIA (no pixel click). Fall back to a click on
    #    its centre only if SetFocus is unsupported.
    focused = False
    try:
        ctrl.SetFocus()
        focused = True
    except Exception:
        try:
            import pyautogui
            pyautogui.click(info["x"], info["y"])
            focused = True
        except Exception:
            pass
    if not focused:
        return {"ok": False, "error": "could not focus control",
                "found_at": info}
    try:
        time.sleep(0.08)  # let focus settle before sending keys
        # All keystrokes go through the control's own SendKeys (targeted
        # SendInput) — far more reliable than pyautogui's global hotkeys, which
        # drop/reorder chars and leak stray keys under rapid automation.
        if clear_first:
            ctrl.SendKeys("{Ctrl}a{Delete}", waitTime=0)
        # 2. Text entry via verified clipboard paste. Instant for any length AND
        #    fires the native paste/input events that React/Electron inputs
        #    (Discord, Slack, Notion, VS Code) require — plain
        #    ValuePattern.SetValue updates the DOM but not React state (Enter
        #    then sends nothing — the Discord bug), and per-char typing drops
        #    chars on laggy WinUI inputs. We wait for the clipboard to actually
        #    hold our text before pasting (no stale-char leak) and restore the
        #    prior clipboard once the paste has consumed it.
        method = "paste"
        pasted_ok = False
        try:
            try:
                saved = uia.GetClipboardText()
            except Exception:
                saved = ""
            uia.SetClipboardText(text)
            for _ in range(20):
                try:
                    if uia.GetClipboardText() == text:
                        pasted_ok = True
                        break
                except Exception:
                    pass
                time.sleep(0.01)
            if pasted_ok:
                ctrl.SendKeys("{Ctrl}v", waitTime=0)
                time.sleep(0.1)   # let the paste fully consume the clipboard
                if saved:         # restore prior clipboard, after paste is done
                    try:
                        uia.SetClipboardText(saved)
                    except Exception:
                        pass
        except Exception:
            pasted_ok = False
        if not pasted_ok:
            # Fallback: type literally via pyautogui (no clipboard, and avoids
            # SendKeys treating { } ( ) + ^ % as special tokens).
            method = "keystroke"
            import pyautogui
            pyautogui.typewrite(text, interval=0.01)
        # 3. Optional submit (send / search) in the same focused control.
        if submit:
            time.sleep(0.04)
            ctrl.SendKeys("{Enter}", waitTime=0)
            method += "+enter"
        return {"ok": True, "method": method,
                "target": info["name"] or info["automation_id"],
                "control_type": info["control_type"],
                "rect": _onscreen_rect(ctrl)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "found_at": info}


def invoke_ui_element(query: str, app_hint: str = "") -> dict:
    """Activate a control by name without a pixel click. Order of attempts:
    scroll it into view (Electron virtualized lists), then InvokePattern (a
    real button/menu activation), then SelectionItemPattern (servers/channels/
    list items), and finally a coordinate click if the control has a real rect.
    Works on offscreen/0-size Electron controls that a pixel click can't hit.
    """
    ctrl, info = _find_uia_control(query, app_hint)
    if ctrl is None:
        return info
    target = info["name"] or info["automation_id"]
    # Bring virtualized/offscreen items into view first (no-op if not scrollable)
    try:
        sip = ctrl.GetScrollItemPattern()
        if sip is not None:
            sip.ScrollIntoView()
            time.sleep(0.05)
    except Exception:
        pass
    # Capture the on-screen bounds NOW (after scroll, before activation may
    # navigate the control away) for the UIA focus-ring overlay.
    rect = _onscreen_rect(ctrl)
    # 1. InvokePattern — the cleanest activation
    try:
        ip = ctrl.GetInvokePattern()
        if ip is not None:
            ip.Invoke()
            return {"ok": True, "method": "invoke_pattern",
                    "target": target, "control_type": info["control_type"],
                    "rect": rect}
    except Exception:
        pass
    # 2. SelectionItemPattern — for list/tree items (Discord servers & channels)
    try:
        sp = ctrl.GetSelectionItemPattern()
        if sp is not None:
            sp.Select()
            return {"ok": True, "method": "selection_pattern",
                    "target": target, "control_type": info["control_type"],
                    "rect": rect}
    except Exception:
        pass
    # 3. Coordinate click — only if the control now has a real on-screen rect.
    try:
        import pyautogui
        if rect["width"] > 0 and rect["height"] > 0:
            x = rect["left"] + rect["width"] // 2
            y = rect["top"] + rect["height"] // 2
            pyautogui.click(x, y)
            return {"ok": True, "method": "click_fallback",
                    "x": x, "y": y, "target": target, "rect": rect}
        return {"ok": False,
                "error": f"'{target}' has no invokable pattern and no on-screen "
                         "rect to click", "found_at": info}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "found_at": info}


def wait_for_ui_element(query: str, app_hint: str = "",
                        timeout: float = 6.0, interval: float = 0.12) -> dict:
    """Poll for a control to appear, returning the instant it does. Use this
    after a navigation/click instead of a fixed sleep — it's both faster (no
    over-waiting) and more reliable (no under-waiting) while an app re-renders.
    """
    deadline = time.time() + max(0.1, timeout)
    last = {"ok": False, "error": f"timed out waiting for '{query}'"}
    while time.time() < deadline:
        res = find_ui_element(query, app_hint)
        if res.get("ok"):
            res["waited_s"] = round(timeout - (deadline - time.time()), 2)
            return res
        last = res
        time.sleep(interval)
    return last


# ─────────────────────────────────────────────────────────────────────────────
# CLIPBOARD HISTORY — tail clipboard in a background thread, keep last N items
# ─────────────────────────────────────────────────────────────────────────────
_clip_history: list[dict] = []        # newest first
_CLIP_HISTORY_MAX = 50
_clip_lock = None                     # threading.Lock lazily created
_clip_thread_started = False


def _clip_path() -> Path:
    return workspace_state_path("clipboard_history.json")


def _load_clip_history() -> None:
    global _clip_history
    try:
        data = read_json(_clip_path(), [])
        _clip_history = data if isinstance(data, list) else []
    except Exception:
        _clip_history = []


def _save_clip_history() -> None:
    try:
        write_json(_clip_path(), _clip_history[:_CLIP_HISTORY_MAX])
    except Exception:
        pass


def start_clipboard_watcher() -> None:
    """Idempotent: starts a background thread that polls the clipboard
    every 1s and prepends new text to the history."""
    global _clip_thread_started, _clip_lock
    if _clip_thread_started:
        return
    _clip_thread_started = True
    import threading
    _clip_lock = threading.Lock()
    _load_clip_history()

    def _poll():
        last_text = ""
        while True:
            try:
                # Use Qt's clipboard if available (already in capsule app)
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is None:
                    time.sleep(2)
                    continue
                cb = app.clipboard()
                text = (cb.text() or "").strip()
                if text and text != last_text and len(text) <= 8000:
                    last_text = text
                    with _clip_lock:
                        # dedupe: bump existing item to front
                        _clip_history[:] = [
                            h for h in _clip_history if h["text"] != text]
                        _clip_history.insert(0, {
                            "text": text,
                            "ts": time.time(),
                            "preview": text[:120],
                        })
                        del _clip_history[_CLIP_HISTORY_MAX:]
                        _save_clip_history()
            except Exception:
                pass
            time.sleep(1.0)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()


def list_clipboard_history(limit: int = 20) -> list[dict]:
    return _clip_history[:limit]


def search_clipboard_history(query: str, limit: int = 10) -> list[dict]:
    q = query.lower()
    return [h for h in _clip_history if q in h["text"].lower()][:limit]


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULED RECIPES — JSON-persisted cron list, daemon checks every minute
# ─────────────────────────────────────────────────────────────────────────────
def _sched_path() -> Path:
    return workspace_state_path("scheduled_recipes.json")


def list_scheduled() -> list[dict]:
    try:
        data = read_json(_sched_path(), [])
        return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def add_scheduled(name: str, when: str, goal: str, mode: str = "auto") -> dict:
    """`when` is a simple "HH:MM" (daily) or "weekday HH:MM" (Mon..Sun)
    or `every Nm` (every N minutes)."""
    item = {
        "id": f"sch-{int(time.time())}",
        "name": name, "when": when, "goal": goal, "mode": mode,
        "last_run": 0,
    }
    items = list_scheduled()
    items.append(item)
    write_json(_sched_path(), items)
    return item


def remove_scheduled(sid: str) -> bool:
    items = [i for i in list_scheduled() if i["id"] != sid]
    write_json(_sched_path(), items)
    return True


_sched_thread_started = False


def start_scheduler_daemon(submit_fn) -> None:
    """`submit_fn(goal, mode)` is called when a scheduled item is due."""
    global _sched_thread_started
    if _sched_thread_started:
        return
    _sched_thread_started = True
    import threading
    from datetime import datetime

    DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    def _due(when: str, now: datetime, last: float) -> bool:
        try:
            w = when.strip().lower()
            # "every Nm"
            if w.startswith("every"):
                parts = w.split()
                if len(parts) >= 2 and parts[1].endswith("m"):
                    n = int(parts[1][:-1])
                    return time.time() - last >= n * 60
            # "HH:MM" daily
            if ":" in w and " " not in w:
                hh, mm = w.split(":"); hh = int(hh); mm = int(mm)
                if now.hour == hh and now.minute == mm:
                    if (time.time() - last) > 90:
                        return True
            # "mon 09:00" weekday
            if " " in w:
                day, t = w.split(); hh, mm = t.split(":")
                if (DOW[now.weekday()] == day.lower()
                        and now.hour == int(hh) and now.minute == int(mm)):
                    if (time.time() - last) > 90:
                        return True
        except Exception:
            return False
        return False

    def _loop():
        while True:
            try:
                items = list_scheduled()
                now = datetime.now()
                for it in items:
                    if _due(it["when"], now, it.get("last_run", 0)):
                        try:
                            submit_fn(it["goal"], it.get("mode", "auto"))
                        except Exception:
                            pass
                        it["last_run"] = time.time()
                        write_json(_sched_path(), items)
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_loop, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# FORM-PROFILE AUTOFILL — store named profiles, fill matching labels via UIA
# ─────────────────────────────────────────────────────────────────────────────
def _profile_path() -> Path:
    return workspace_state_path("form_profiles.json")


def list_profiles() -> dict:
    try:
        data = read_json(_profile_path(), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_profile(name: str, fields: dict) -> dict:
    profiles = list_profiles()
    profiles[name] = fields
    write_json(_profile_path(), profiles)
    return profiles[name]


def delete_profile(name: str) -> None:
    profiles = list_profiles()
    profiles.pop(name, None)
    write_json(_profile_path(), profiles)


def autofill_active_form(profile_name: str) -> dict:
    """Walk the foreground app's UIA tree, find inputs, match each field's
    label/AutomationId to a profile key, type the matching value."""
    profiles = list_profiles()
    profile = profiles.get(profile_name)
    if not profile:
        return {"ok": False, "error": f"no profile '{profile_name}'"}
    try:
        import uiautomation as uia
    except ImportError:
        return {"ok": False, "error": "needs `pip install uiautomation`"}
    root = uia.GetForegroundControl()
    filled: list[str] = []
    # Field-name → list of label keywords to match (case-insensitive)
    syn = {
        "name":     ["name", "full name", "first name"],
        "email":    ["email", "e-mail"],
        "phone":    ["phone", "telephone", "mobile"],
        "address":  ["address", "street"],
        "city":     ["city"],
        "state":    ["state", "province"],
        "zip":      ["zip", "postal", "postcode"],
        "country":  ["country"],
        "company":  ["company", "organization", "employer"],
        "title":    ["title", "job title", "role"],
    }
    def walk(ctrl, depth=0):
        if depth > 8: return
        try:
            if (ctrl.ControlTypeName or "").lower() == "edit":
                label = (ctrl.Name or "").lower()
                aid = (ctrl.AutomationId or "").lower()
                for key, value in profile.items():
                    keys = syn.get(key.lower(), [key.lower()])
                    if any(k in label or k in aid for k in keys):
                        try:
                            ctrl.SendKeys(str(value), waitTime=0.05)
                            filled.append(f"{key}={value}")
                            break
                        except Exception:
                            pass
            for child in ctrl.GetChildren():
                walk(child, depth + 1)
        except Exception:
            pass
    walk(root)
    return {"ok": True, "filled": filled,
            "skipped_due_to_no_match": [k for k in profile if k not in
                                         [f.split("=")[0] for f in filled]]}


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN-REGION WATCH & NOTIFY
# ─────────────────────────────────────────────────────────────────────────────
def _watches_path() -> Path:
    return workspace_state_path("screen_watches.json")


def list_watches() -> list[dict]:
    try:
        data = read_json(_watches_path(), [])
        return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def add_watch(name: str, x: int, y: int, w: int, h: int,
              every_sec: int, prompt: str = "") -> dict:
    item = {"id": f"watch-{int(time.time())}",
            "name": name, "x": x, "y": y, "w": w, "h": h,
            "every_sec": max(15, int(every_sec)),
            "prompt": prompt or "Has this region changed meaningfully?",
            "last_check": 0, "last_hash": ""}
    items = list_watches()
    items.append(item)
    write_json(_watches_path(), items)
    return item


def remove_watch(wid: str) -> bool:
    items = [i for i in list_watches() if i["id"] != wid]
    write_json(_watches_path(), items)
    return True


def _grab_region_hash(x: int, y: int, w: int, h: int) -> str:
    """Quick perceptual-ish hash: capture region, compute md5 of downscaled."""
    import hashlib
    try:
        import mss
        with mss.mss() as sct:
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
            data = shot.rgb
        # Downsample to 16x16-ish chunks for change tolerance
        return hashlib.md5(data[::64]).hexdigest()
    except Exception:
        return ""


_watch_thread_started = False


def start_watch_daemon(notify_fn) -> None:
    """`notify_fn(name, prompt)` is called when a watched region changes."""
    global _watch_thread_started
    if _watch_thread_started:
        return
    _watch_thread_started = True
    import threading

    def _loop():
        while True:
            try:
                items = list_watches()
                changed = False
                for w in items:
                    if time.time() - w.get("last_check", 0) < w["every_sec"]:
                        continue
                    h = _grab_region_hash(w["x"], w["y"], w["w"], w["h"])
                    if h and w.get("last_hash") and h != w["last_hash"]:
                        try:
                            notify_fn(w["name"], w["prompt"])
                        except Exception:
                            pass
                    w["last_hash"] = h
                    w["last_check"] = time.time()
                    changed = True
                if changed:
                    write_json(_watches_path(), items)
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_loop, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-APP "SEND TO" — last answer → Notepad / Excel / clipboard
# ─────────────────────────────────────────────────────────────────────────────
def send_to(target: str, text: str) -> dict:
    """target = 'notepad' | 'excel' | 'clipboard' | 'paint'"""
    import subprocess, tempfile
    if target == "clipboard":
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                return {"ok": False, "error": "no Qt app running"}
            app.clipboard().setText(text)
            return {"ok": True, "target": "clipboard"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if target == "notepad":
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False,
                                              mode="w", encoding="utf-8")
            tmp.write(text); tmp.close()
            subprocess.Popen(["notepad.exe", tmp.name])
            return {"ok": True, "target": "notepad", "path": tmp.name}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if target == "excel":
        # If the text looks like CSV/TSV, save as .csv and open with Excel
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False,
                                              mode="w", encoding="utf-8")
            tmp.write(text); tmp.close()
            os.startfile(tmp.name)
            return {"ok": True, "target": "excel", "path": tmp.name}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if target == "paint":
        try:
            subprocess.Popen(["mspaint.exe"])
            return {"ok": True, "target": "paint"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown target '{target}'"}


# ─────────────────────────────────────────────────────────────────────────────
# OCR — Windows 11 has Windows.Media.Ocr but binding is heavy. Fall back to
# pytesseract if present, otherwise gracefully report.
# ─────────────────────────────────────────────────────────────────────────────
def ocr_region(x: int, y: int, w: int, h: int) -> dict:
    try:
        import mss
        with mss.mss() as sct:
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
            from PIL import Image
            img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as e:
        return {"ok": False, "error": f"capture failed: {e}"}
    try:
        import pytesseract
        text = pytesseract.image_to_string(img)
        return {"ok": True, "text": text.strip(), "backend": "tesseract"}
    except ImportError:
        return {"ok": False, "error": "pytesseract not installed; "
                "`pip install pytesseract` + install Tesseract OCR binary"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL RAG — folder embedding + question answering via chroma
# ─────────────────────────────────────────────────────────────────────────────
def rag_index_folder(folder: str, name: str = "default") -> dict:
    """Embed all .txt/.md/.py files in folder into a chroma collection."""
    try:
        import chromadb
    except ImportError:
        return {"ok": False, "error": "chromadb not installed"}
    try:
        client = chromadb.PersistentClient(
            path=str(Path(os.environ.get(
                "AI_COMPUTER_WORKSPACE", ".")).resolve() / "rag_db"))
        try:
            coll = client.get_collection(name)
        except Exception:
            coll = client.create_collection(name)
        docs = []
        ids = []
        for p in Path(folder).rglob("*"):
            if p.is_file() and p.suffix.lower() in {".txt", ".md", ".py",
                                                     ".js", ".ts", ".json"}:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    # Skip very large files
                    if len(text) > 50_000:
                        text = text[:50_000]
                    # Naive chunk: 1.5KB blocks
                    for i in range(0, len(text), 1500):
                        chunk = text[i:i+1500]
                        if chunk.strip():
                            docs.append(chunk)
                            ids.append(f"{p.name}#{i}")
                except Exception:
                    continue
        if not docs:
            return {"ok": False, "error": "no indexable files found"}
        # chroma's default embedder downloads a small model; OK for ship MVP
        coll.add(documents=docs, ids=ids)
        return {"ok": True, "collection": name, "n_chunks": len(docs)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def rag_query(name: str, query: str, top_k: int = 5) -> dict:
    try:
        import chromadb
        client = chromadb.PersistentClient(
            path=str(Path(os.environ.get(
                "AI_COMPUTER_WORKSPACE", ".")).resolve() / "rag_db"))
        coll = client.get_collection(name)
        res = coll.query(query_texts=[query], n_results=top_k)
        return {"ok": True, "hits": [
            {"id": i, "doc": d}
            for i, d in zip(res["ids"][0], res["documents"][0])
        ]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# PER-APP TRUST POLICIES
# ─────────────────────────────────────────────────────────────────────────────
def _trust_path() -> Path:
    return workspace_state_path("trust_policies.json")


def list_trust() -> dict:
    try:
        data = read_json(_trust_path(), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def set_trust(exe_name: str, level: str) -> dict:
    """level ∈ {allow, ask, deny}"""
    if level not in ("allow", "ask", "deny"):
        return {"ok": False, "error": f"bad level '{level}'"}
    pol = list_trust()
    pol[exe_name.lower()] = level
    write_json(_trust_path(), pol)
    return {"ok": True, "exe": exe_name.lower(), "level": level}


def get_trust(exe_name: str) -> str:
    return list_trust().get(exe_name.lower(), "ask")


# ─────────────────────────────────────────────────────────────────────────────
# UNDO STACK — record inverse actions, replay
# ─────────────────────────────────────────────────────────────────────────────
_undo_stack: list[dict] = []
_UNDO_MAX = 20


def record_undo(action: dict) -> None:
    """action: {kind: 'type'|'move'|'click', inverse_fn_name, args}"""
    _undo_stack.append(action)
    del _undo_stack[:-_UNDO_MAX]


def pop_and_execute_undo() -> dict:
    if not _undo_stack:
        return {"ok": False, "error": "nothing to undo"}
    last = _undo_stack.pop()
    # Built-in handlers
    kind = last.get("kind")
    try:
        if kind == "type":
            # Ctrl+Z in active app
            import pyautogui
            pyautogui.hotkey("ctrl", "z")
            return {"ok": True, "undone": "Ctrl+Z to undo last type"}
        if kind == "move_file":
            import shutil
            shutil.move(last["args"]["dst"], last["args"]["src"])
            return {"ok": True, "undone": "file moved back"}
        if kind == "close_tab":
            import pyautogui
            pyautogui.hotkey("ctrl", "shift", "t")
            return {"ok": True, "undone": "Ctrl+Shift+T to reopen tab"}
        return {"ok": False, "error": f"unknown undo kind '{kind}'"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TELEMETRY_PROMISE = {
    "telemetry_enabled": False,
    "outbound_destinations": [
        "OpenRouter free-tier LLMs (only LLM prompts + tool results)",
        "Web pages you ask the agent to browse via Playwright",
    ],
    "stays_local": [
        "Clipboard contents",
        "File contents you attach",
        "Window screenshots",
        "Connector link state (workspace/connectors.json)",
        "Session history",
        "Voice recordings (Windows SAPI processes on-device)",
    ],
    "notes": (
        "Zero analytics SDKs, zero crash reporters, zero usage telemetry. "
        "The only network calls are LLM API requests and the URLs you "
        "explicitly ask the agent to fetch."
    ),
}
