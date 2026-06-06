from __future__ import annotations
import asyncio
import json
import time
import subprocess
import os
import base64
import io
import shutil
import re
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
import mss
from PIL import Image
try:
    import pytesseract
except ImportError:
    pytesseract = None

from .models import Action, ActionType, ToolError, ToolResult
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .memory import MemoryStore
from .providers import get_scale_factor
from .untrusted_content import wrap_untrusted_web_content

try:
    import win32gui, win32api, win32con, win32process  # type: ignore
except ImportError:
    win32gui = win32api = win32con = win32process = None  # type: ignore
import ctypes
import time
import logging

_log = logging.getLogger(__name__)

# Pre-click pointer overlay — off by default. Set ORYNN_POINTER_OVERLAY=1
# to make desktop clicks "watchable": a ring flashes at the target before the
# click so the user can see where the agent is acting.
_POINTER_OVERLAY_ENABLED = (
    os.environ.get("ORYNN_POINTER_OVERLAY")
    or os.environ.get("AI_COMPUTER_POINTER_OVERLAY", "")
).strip() in ("1", "true", "yes")


def _flash_pointer(x: int, y: int, hold_ms: int = 400) -> None:
    """Briefly show a ring marker at (x, y) on screen, then remove it.

    The window is fully destroyed BEFORE this returns, so the click that
    follows can never be intercepted by the marker. Entirely best-effort:
    any failure (no display, no Tk, threading quirk) is swallowed — the
    click path must never break because of a cosmetic overlay.
    """
    if not _POINTER_OVERLAY_ENABLED:
        return
    try:
        import tkinter as tk
    except Exception:
        return
    try:
        size = 64
        root = tk.Tk()
        root.overrideredirect(True)            # borderless
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", "magenta")  # Windows: magenta -> see-through
        except Exception:
            pass
        root.geometry(f"{size}x{size}+{int(x) - size // 2}+{int(y) - size // 2}")
        canvas = tk.Canvas(root, width=size, height=size, bg="magenta", highlightthickness=0)
        canvas.pack()
        canvas.create_oval(6, 6, size - 6, size - 6, outline="#2563eb", width=4)
        canvas.create_oval(size // 2 - 4, size // 2 - 4, size // 2 + 4, size // 2 + 4,
                           fill="#2563eb", outline="")
        root.after(max(50, int(hold_ms)), root.destroy)
        root.mainloop()
        try:
            root.destroy()
        except Exception:
            pass
    except Exception as exc:
        _log.debug("pointer overlay skipped: %s", exc)


def _is_hung_app_window(hwnd: int) -> bool:
    """Return whether a Win32 window is hung, tolerating pywin32 builds without this helper."""
    if win32gui is not None and hasattr(win32gui, "IsHungAppWindow"):
        try:
            return bool(win32gui.IsHungAppWindow(hwnd))
        except Exception:
            pass
    try:
        return bool(ctypes.windll.user32.IsHungAppWindow(int(hwnd)))
    except Exception:
        return False


def _rect_payload(rect: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """Normalize a screen rectangle to the structured overlay shape."""
    if not isinstance(rect, dict):
        return None
    try:
        left = int(rect.get("left", 0))
        top = int(rect.get("top", 0))
        width = int(rect.get("width", 0))
        height = int(rect.get("height", 0))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def _clean_finish_reason(args: Dict[str, Any]) -> str:
    """Extract a clean, human-readable answer from the finish action's args.

    Some models (notably gpt-oss in harmony mode) double-wrap the answer as a
    JSON object — e.g. reason='{"reason":"…"}' — which then renders as literal
    JSON in the answer card. Unwrap up to a couple of nested reason/answer/text
    payloads so the user always sees plain prose."""
    keys = ("reason", "answer", "text", "result", "summary", "message")
    raw: Any = None
    for k in keys:
        if args.get(k) not in (None, ""):
            raw = args.get(k)
            break
    if raw is None:
        raw = args.get("reason", "")
    for _ in range(3):
        if isinstance(raw, dict):
            nxt = next((raw[k] for k in keys if raw.get(k) not in (None, "")), "")
            raw = nxt
            continue
        if isinstance(raw, str):
            s = raw.strip()
            if len(s) >= 2 and s[0] == "{" and s[-1] == "}" and any(
                f'"{k}"' in s for k in keys
            ):
                try:
                    obj = json.loads(s)
                except Exception:
                    break
                if isinstance(obj, dict) and any(obj.get(k) for k in keys):
                    raw = next((obj[k] for k in keys if obj.get(k) not in (None, "")), "")
                    continue
            break
        break
    out = str(raw or "").strip()
    return out or "Task marked complete by agent."


def _rect_from_match(item: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Build an exact rect from a UIA match, preserving left/top when known."""
    try:
        width = int(item.get("width", 0))
        height = int(item.get("height", 0))
        if width <= 0 or height <= 0:
            return None
        if "left" in item and "top" in item:
            left = int(item.get("left", 0))
            top = int(item.get("top", 0))
        else:
            left = int(item["x"]) - width // 2
            top = int(item["y"]) - height // 2
        return {"left": left, "top": top, "width": width, "height": height}
    except Exception:
        return None


def _overlay_payload(
    overlay_type: str,
    tool: str,
    kind: str,
    label: str,
    *,
    target: str = "",
    rect: Optional[Dict[str, Any]] = None,
    app_rect: Optional[Dict[str, Any]] = None,
    point: Optional[Dict[str, Any]] = None,
    phase: str = "result",
    fallback_reason: str = "",
    control_layer: str = "",
    control_reason: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": overlay_type,
        "tool": tool,
        "kind": kind,
        "phase": phase,
        "label": (label or "").strip(),
    }
    if target:
        payload["target"] = target
    norm_rect = _rect_payload(rect)
    if norm_rect:
        payload["rect"] = norm_rect
    norm_app = _rect_payload(app_rect)
    if norm_app:
        payload["app_rect"] = norm_app
    if isinstance(point, dict):
        try:
            payload["point"] = {"x": int(point["x"]), "y": int(point["y"])}
        except Exception:
            pass
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    if control_layer:
        payload["control_layer"] = control_layer
    if control_reason:
        payload["control_reason"] = control_reason
    return payload


def _uia_result_data(
    raw: Dict[str, Any],
    *,
    tool: str,
    kind: str,
    label: str,
    target: str = "",
    rect: Optional[Dict[str, Any]] = None,
    app_rect: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = dict(raw or {})
    data["overlay"] = _overlay_payload(
        "uia_control" if _rect_payload(rect) else "app_focus",
        tool,
        kind,
        label,
        target=target,
        rect=rect,
        app_rect=app_rect,
        control_layer="UIA exact" if _rect_payload(rect) else "UIA app",
        control_reason="Windows accessibility tree",
    )
    return data

# Opus Audit Fix: Enable DPI awareness for precise mouse/keyboard isolation
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1) # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from .text_editor import TextEditorTool


def _init_dpi_awareness() -> None:
    """Call once at startup so Win32 coords and pyautogui coords match on HiDPI displays."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_init_dpi_awareness()


_BLOCKED_HOST_SUBSTRINGS = (
    "localhost",
    "metadata.google.internal",
    "metadata.goog",
)
_BLOCKED_HOST_EXACT = {
    "0.0.0.0",
    "::",
    "169.254.169.254",  # AWS / GCP / Azure instance metadata
    "fd00:ec2::254",
}


def _validate_public_http_url(url: str) -> str:
    """Block SSRF-style targets (file://, internal IPs, metadata) and require http(s).

    Returns the URL if it is acceptable, otherwise raises ToolError.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    if not isinstance(url, str) or not url.strip():
        raise ToolError("URL is required.")

    normalized_url = url.strip()
    parts = urlsplit(normalized_url)
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ToolError(f"Only http(s) URLs are allowed (got scheme '{scheme or 'none'}').")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ToolError("URL must include a hostname.")

    if host in _BLOCKED_HOST_EXACT:
        raise ToolError(f"Refusing to fetch internal host: {host}")
    for needle in _BLOCKED_HOST_SUBSTRINGS:
        if needle in host:
            raise ToolError(f"Refusing to fetch internal host: {host}")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    def _is_internal_ip(candidate) -> bool:
        return bool(
            candidate.is_loopback
            or candidate.is_private
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_unspecified
            or candidate.is_reserved
        )

    if ip is not None and _is_internal_ip(ip):
        raise ToolError(f"Refusing to fetch private/internal IP: {host}")

    if ip is None:
        try:
            infos = socket.getaddrinfo(host, parts.port or None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            infos = []
        seen_ips = set()
        for info in infos:
            try:
                resolved = ipaddress.ip_address(info[4][0])
            except Exception:
                continue
            if resolved in seen_ips:
                continue
            seen_ips.add(resolved)
            if _is_internal_ip(resolved):
                raise ToolError(
                    f"Refusing to fetch host resolving to private/internal IP: {host} -> {resolved}"
                )

    return normalized_url


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_300(self, req, fp, code, msg, headers):
        from urllib.error import HTTPError

        raise HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_300
    http_error_302 = http_error_300
    http_error_303 = http_error_300
    http_error_307 = http_error_300
    http_error_308 = http_error_300


def _read_public_http_url(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
) -> tuple[bytes, str]:
    """Fetch a public http(s) URL, validating every redirect before following it."""
    import urllib.error
    import urllib.parse
    import urllib.request

    current = _validate_public_http_url(url)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request_headers = headers or {"User-Agent": "Mozilla/5.0 (Orynn Agent)"}
    for _ in range(6):
        req = urllib.request.Request(current, headers=request_headers)
        try:
            with opener.open(req, timeout=timeout) as response:
                final_url = _validate_public_http_url(response.geturl() or current)
                return response.read(max_bytes), final_url
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise ToolError(f"Redirect from {current} did not include a Location header.")
            current = _validate_public_http_url(urllib.parse.urljoin(current, location))
    raise ToolError("Too many redirects while fetching URL.")


class ToolExecutor:
    def __init__(self, workspace: Path, text_editor=None, plugin_registry=None, *, home_dir: Optional[Path] = None, memory: Optional["MemoryStore"] = None):
        self.workspace = workspace.resolve()
        self.home_dir = (home_dir or Path.home()).expanduser().resolve()
        self.text_editor = text_editor or TextEditorTool(self.workspace, home_dir=self.home_dir)
        self.plugin_registry = plugin_registry
        self.memory = memory
        self._bash_cwd = self.workspace
        # Background browser for sandboxed GUI — set by AgentService
        self._bg_browser = None
        # Whether to run GUI actions in background (cowork) mode
        self._background_mode = True
        self._isolated_hwnd = None
        self._isolated_app = None
        self._started_pids: set[int] = set()

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        roots = [self.workspace]
        if self.home_dir not in roots:
            roots.append(self.home_dir)
        return tuple(roots)

    def _read_text_file(self, path: Path) -> str:
        """Read text robustly on Windows, preferring UTF-8 but tolerating legacy files."""
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def set_background_browser(self, browser):
        """Attach a BackgroundBrowser for sandboxed GUI actions."""
        self._bg_browser = browser

    def set_isolated_hwnd(self, hwnd: Optional[int], app_title: Optional[str] = None):
        """Set the target HWND for isolated control. None reverts to normal mode."""
        self._isolated_hwnd = hwnd
        self._isolated_app = app_title

    def has_isolated_target(self) -> bool:
        return bool(self._isolated_hwnd or self._isolated_app)

    def resolve_isolated_hwnd(self) -> Optional[int]:
        """Resolve the current isolated HWND, re-discovering it by title when possible."""
        if win32gui is None:
            return None
        if self._isolated_hwnd and win32gui.IsWindow(self._isolated_hwnd):
            return self._isolated_hwnd
        self._isolated_hwnd = self._get_hwnd_for_title(self._isolated_app or "")
        return self._isolated_hwnd

    def _get_hwnd_for_title(self, title: str):
        """Find a window by title within the same process context if possible."""
        if win32gui is None:
            return None
        if not title: return None
        def callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
                windows.append(hwnd)
        windows = []
        win32gui.EnumWindows(callback, windows)
        return windows[0] if windows else None

    def _iter_matching_windows(self, title_substr: str) -> list[dict[str, Any]]:
        if win32gui is None:
            return []
        needle = (title_substr or "").strip().lower()
        matches: list[dict[str, Any]] = []

        def _callback(hwnd, windows):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd) or ""
                if needle and needle not in title.lower():
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width = max(0, right - left)
                height = max(0, bottom - top)
                if width <= 0 or height <= 0:
                    return
                pid = None
                if win32process is not None:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    except Exception:
                        pid = None
                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "pid": pid,
                    "rect": (left, top, right, bottom),
                    "area": width * height,
                })
            except Exception:
                return

        win32gui.EnumWindows(_callback, matches)
        matches.sort(key=lambda item: item["area"], reverse=True)
        return matches

    def _remember_started_pid(self, pid: Optional[int]) -> None:
        try:
            if pid is not None and int(pid) > 0:
                self._started_pids.add(int(pid))
        except Exception:
            return

    def _looks_like_gui_launch(self, command: str) -> bool:
        stripped = (command or "").strip().lower()
        return bool(re.match(
            r'^(start\s+\S|explorer\s|cmd\s*/c\s+start|powershell(?:\.exe)?\s+-command\s+"?start(?:-process)?)',
            stripped,
        ))

    def _guess_launch_target_title(self, command: str) -> str:
        if self._isolated_app:
            return self._isolated_app
        stripped = (command or "").strip()
        patterns = [
            r'^(?:cmd\s*/c\s+)?start\s+(?:"[^"]*"\s+)?(?P<target>\S+)',
            r'^explorer\s+(?P<target>\S+)',
            r'^powershell(?:\.exe)?\s+-command\s+"?start(?:-process)?\s+(?P<target>\S+)',
        ]
        target = ""
        for pattern in patterns:
            match = re.match(pattern, stripped, flags=re.IGNORECASE)
            if match:
                target = (match.group("target") or "").strip().strip('"').strip("'")
                break
        if not target or re.match(r"^[a-z]+://", target, flags=re.IGNORECASE):
            return ""
        base = Path(target.rstrip(":")).stem or target.rstrip(":")
        alias = {
            "notepad": "Notepad",
            "calc": "Calculator",
            "calculator": "Calculator",
            "mspaint": "Paint",
            "ms-paint": "Paint",
            "paint": "Paint",
            "code": "Visual Studio Code",
            "cursor": "Cursor",
            "explorer": "File Explorer",
        }
        return alias.get(base.lower(), base)

    def _assert_hwnd_responsive(self, hwnd: int) -> Optional[str]:
        """Return an error string if the window is gone or hung, else None."""
        try:
            import win32gui  # type: ignore
        except ImportError:
            return "Isolated window control is only available on Windows."
        if not win32gui.IsWindow(hwnd):
            return "Target window no longer exists."
        if _is_hung_app_window(hwnd):
            return "Target window is not responding (hung)."
        return None

    def current_target_hung_info(self) -> Optional[Dict[str, Any]]:
        hwnd = self.resolve_isolated_hwnd()
        if not hwnd:
            return None
        if not _is_hung_app_window(hwnd):
            return None
        title = ""
        pid = None
        if win32gui is not None:
            try:
                title = win32gui.GetWindowText(hwnd) or ""
            except Exception:
                title = ""
        if win32process is not None:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = None
        return {"hwnd": hwnd, "title": title or (self._isolated_app or ""), "pid": pid}

    def _safe_path(self, value: str) -> Path:
        """Resolve a path within the preferred project folder or the user's home directory."""
        raw = Path((value or ".")).expanduser()
        candidate = (self.workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
        for root in self.allowed_roots:
            if candidate == root or root in candidate.parents:
                return candidate
        raise ToolError(
            f"Path escapes allowed roots: {value}. Allowed roots: "
            + ", ".join(str(root) for root in self.allowed_roots)
        )

    def _scale(self, x: int, y: int, sw: int, sh: int):
        """Scale coordinates — in background mode, use browser viewport directly."""
        if self._background_mode and self._bg_browser:
            # Background browser uses its own viewport, no scaling needed
            return x, y
        import pyautogui
        screen_w, screen_h = pyautogui.size()
        rx = int(x * screen_w / sw)
        ry = int(y * screen_h / sh)
        return rx, ry

    # ── mouse actions ────────────────────────────────────────────────────

    async def _mouse_move_bg(self, x: int, y: int, sw=1280, sh=800):
        await self._bg_browser.mouse_move(x, y)
        return ToolResult(
            ok=True,
            output=f"Moved mouse to {x}, {y} (background)",
            data={"overlay": _overlay_payload(
                "point", "mouse_move", "move", "Moving cursor", point={"x": x, "y": y},
                control_layer="Browser visual", control_reason="background browser coordinate action",
            )},
        )

    def mouse_move(self, x: int, y: int, sw=1280, sh=800):
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Smooth, human-like movement
        pyautogui.moveTo(rx, ry, duration=0.6, tween=pyautogui.easeInOutQuad)
        return ToolResult(
            ok=True,
            output=f"Moved mouse to {rx}, {ry}",
            data={"overlay": _overlay_payload(
                "point", "mouse_move", "move", "Moving cursor", point={"x": rx, "y": ry},
                control_layer="Screenshot fallback", control_reason="desktop pixel coordinate action",
            )},
        )

    async def _mouse_click_bg(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):
        await self._bg_browser.mouse_click(x, y, button=button, click_count=clicks)
        label = "Double-clicking" if clicks > 1 else "Clicking"
        return ToolResult(
            ok=True,
            output=f"Clicked {button} {clicks} times at {x}, {y} (background)",
            data={"overlay": _overlay_payload(
                "point", "mouse_click", "click", label, point={"x": x, "y": y},
                control_layer="Browser visual", control_reason="background browser coordinate action",
            )},
        )

    def mouse_click(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):
        if self.has_isolated_target():
            return self._mouse_click_isolated(x, y, button, clicks, sw, sh)
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Clamp to the real screen — an off-screen target can hang or be
        # silently dropped, and pyautogui's fail-safe corner aborts the run.
        screen_w, screen_h = pyautogui.size()
        rx = max(0, min(rx, screen_w - 1))
        ry = max(0, min(ry, screen_h - 1))
        # Optional: flash a marker at the target so the action is watchable.
        # The marker window is destroyed before the click — no interference.
        _flash_pointer(rx, ry)
        try:
            pyautogui.moveTo(rx, ry, duration=0.4, tween=pyautogui.easeInOutQuad)
            time.sleep(0.1)
            pyautogui.click(button=button, clicks=clicks, interval=0.1)
        except Exception as e:
            # Screen locked, fail-safe triggered, no display, etc. — report it
            # so the agent can react instead of believing the click landed.
            return ToolResult(ok=False, output=f"Click at {rx},{ry} failed: {e}")
        label = "Double-clicking" if clicks > 1 else "Clicking"
        return ToolResult(
            ok=True,
            output=f"Clicked {button} {clicks} times at {rx}, {ry}",
            data={"overlay": _overlay_payload(
                "point", "mouse_click", "click", label, point={"x": rx, "y": ry},
                control_layer="Screenshot fallback", control_reason="desktop pixel coordinate action",
            )},
        )


    def _mouse_click_isolated(self, x: int, y: int, button: str, clicks: int, sw: int, sh: int):
        try:
            # Opus Audit: Auto-discovery & Recovery for Child Windows/Modals
            hwnd = self.resolve_isolated_hwnd()
            if not hwnd:
                return ToolResult(ok=False, output=f"Target window '{self._isolated_app or 'isolated app'}' not found.")

            # Opus Audit: Hung Application Detection
            if _is_hung_app_window(hwnd):
                return ToolResult(ok=False, output="Target application is frozen/not responding.")

            if not (0 <= x <= sw and 0 <= y <= sh):
                return ToolResult(ok=False, output=f"Coordinates {x},{y} are out of bounds ({sw}x{sh})")

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            window_w = max(1, right - left)
            window_h = max(1, bottom - top)
            abs_x = left + int(x * window_w / max(sw, 1))
            abs_y = top + int(y * window_h / max(sh, 1))
            
            # Sub-pixel precise conversion for DPI-aware windows
            client_pt = win32gui.ScreenToClient(hwnd, (abs_x, abs_y))
            lparam = win32api.MAKELONG(client_pt[0], client_pt[1])
            
            msg_down = win32con.WM_LBUTTONDOWN if button == 'left' else win32con.WM_RBUTTONDOWN
            msg_up = win32con.WM_LBUTTONUP if button == 'left' else win32con.WM_RBUTTONUP
            
            for _ in range(clicks):
                win32gui.PostMessage(hwnd, msg_down, win32con.MK_LBUTTON if button == 'left' else win32con.MK_RBUTTON, lparam)
                time.sleep(0.05)
                win32gui.PostMessage(hwnd, msg_up, 0, lparam)
                time.sleep(0.1)
            label = "Double-clicking" if clicks > 1 else "Clicking"
            return ToolResult(
                ok=True,
                output=f'Sent {button} click to window (Isolated)',
                data={"overlay": _overlay_payload(
                    "point", "mouse_click", "click", label, point={"x": abs_x, "y": abs_y},
                    control_layer="Window pixel", control_reason="isolated app window message",
                )},
            )
        except Exception as e:
            return ToolResult(ok=False, output=f'Isolated click failed: {str(e)}')

    def _keyboard_type_isolated(self, text: str):
        try:
            import win32con  # type: ignore  # noqa: F401
            hwnd = self.resolve_isolated_hwnd()
            if not hwnd:
                return ToolResult(ok=False, output=f"Target window '{self._isolated_app or 'isolated app'}' not found.")
            err = self._assert_hwnd_responsive(hwnd)
            if err:
                return ToolResult(ok=False, output=err)

            path_paste = self._maybe_paste_path(text, isolated=True)
            if path_paste:
                return path_paste

            # Prefer clipboard paste — much faster than char-by-char PostMessage
            clipboard_result = self._paste_via_clipboard(text, isolated=True)
            if clipboard_result and clipboard_result.ok:
                return clipboard_result

            # Fallback: char-by-char PostMessage (slow but always works)
            import win32gui
            for char in text:
                win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(char), 0)
                time.sleep(0.05)  # Rate limited for stability
            return ToolResult(ok=True, output='Sent keys to window (Isolated)')
        except Exception as e:
            return ToolResult(ok=False, output=f'Isolated typing failed: {str(e)}')

    async def _left_click_drag_bg(self, x: int, y: int, sw=1280, sh=800):
        # Drag from current position to target
        await self._bg_browser.mouse_drag(0, 0, x, y)
        return ToolResult(
            ok=True,
            output=f"Dragged to {x}, {y} (background)",
            data={"overlay": _overlay_payload(
                "point", "left_click_drag", "drag", "Dragging", point={"x": x, "y": y},
                control_layer="Browser visual", control_reason="background browser coordinate action",
            )},
        )

    def left_click_drag(self, x: int, y: int, sw=1280, sh=800):
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Smooth drag
        pyautogui.dragTo(rx, ry, duration=0.8, tween=pyautogui.easeInOutQuad, button="left")
        return ToolResult(
            ok=True,
            output=f"Dragged to {rx}, {ry}",
            data={"overlay": _overlay_payload(
                "point", "left_click_drag", "drag", "Dragging", point={"x": rx, "y": ry},
                control_layer="Screenshot fallback", control_reason="desktop pixel coordinate action",
            )},
        )

    # ── keyboard actions ─────────────────────────────────────────────────

    async def _keyboard_type_bg(self, text: str):
        await self._bg_browser.type_text(text)
        return ToolResult(ok=True, output="Typed text (background)")

    def keyboard_type(self, text: str):
        if self.has_isolated_target():
            return self._keyboard_type_isolated(text)
        path_paste = self._maybe_paste_path(text, isolated=False)
        if path_paste:
            return path_paste
        import pyautogui
        # Slightly randomized human-like typing speed (~40-80ms per char)
        import random
        for char in text:
            pyautogui.write(char)
            time.sleep(random.uniform(0.02, 0.08))
        return ToolResult(ok=True, output="Typed text smoothly")

    async def _key_bg(self, keys: str):
        await self._bg_browser.press_key(keys)
        return ToolResult(ok=True, output=f"Pressed hotkey: {keys} (background)")

    def key(self, keys: str):
        if self.has_isolated_target():
            return self._isolated_key(keys)
        import pyautogui
        parts = [p.strip() for p in keys.split("+") if p.strip()]
        pyautogui.hotkey(*parts)
        return ToolResult(ok=True, output=f"Pressed hotkey: {keys}")

    async def _hold_key_bg(self, key: str, duration: float = 0.5):
        await self._bg_browser.hold_key(key, duration)
        return ToolResult(ok=True, output=f"Held {key} for {duration}s (background)")

    def hold_key(self, key: str, duration: float = 0.5):
        import pyautogui
        pyautogui.keyDown(key)
        time.sleep(duration)
        pyautogui.keyUp(key)
        return ToolResult(ok=True, output=f"Held {key} for {duration}s")

    async def _scroll_bg(self, amount: int, x: Optional[int] = None, y: Optional[int] = None, sw=1280, sh=800):
        await self._bg_browser.scroll(delta_y=amount * 120, x=x, y=y)
        return ToolResult(ok=True, output=f"Scrolled {amount} (background)")

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None, sw=1280, sh=800):
        import pyautogui
        if x is not None and y is not None:
            rx, ry = self._scale(x, y, sw, sh)
            pyautogui.moveTo(rx, ry)
        pyautogui.scroll(amount)
        return ToolResult(ok=True, output=f"Scrolled {amount}")

    async def _type_with_delay_bg(self, text: str, delay: float = 0.05):
        await self._bg_browser.type_text(text, delay=delay)
        return ToolResult(ok=True, output=f"Typed text with {delay}s delay (background)")

    def type_with_delay(self, text: str, delay: float = 0.05):
        if self.has_isolated_target():
            return self._keyboard_type_isolated(text)
        path_paste = self._maybe_paste_path(text, isolated=False)
        if path_paste:
            return path_paste
        import pyautogui
        for char in text:
            pyautogui.write(char)
            time.sleep(delay)
        return ToolResult(ok=True, output=f"Typed text with {delay}s delay")

    # ── screenshot ───────────────────────────────────────────────────────

    async def _screenshot_bg(self):
        b64 = await self._bg_browser.screenshot_b64()
        return ToolResult(ok=True, output="Screenshot captured (background browser)", base64_image=b64)

    def screenshot(self):
        if self.has_isolated_target():
            hwnd = self.resolve_isolated_hwnd()
            if hwnd:
                return self._isolated_screenshot()
        import pyautogui
        screen_w, screen_h = pyautogui.size()
        cap_w = min(screen_w, 1280)
        cap_h = min(screen_h, 800)
        with mss.mss() as sct:
            monitor = {"left": 0, "top": 0, "width": cap_w, "height": cap_h}
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            try:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=65, optimize=True)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return ToolResult(ok=True, output="Screenshot captured", base64_image=b64)
            finally:
                img.close()

    def _isolated_screenshot(self) -> ToolResult:
        from .providers import _capture_hwnd_screenshot_b64
        hwnd = self.resolve_isolated_hwnd()
        if not hwnd:
            return ToolResult(ok=False, output=f"Target window '{self._isolated_app or 'isolated app'}' not found.")
        b64 = _capture_hwnd_screenshot_b64(hwnd)
        return ToolResult(ok=True, output="Isolated screenshot (PrintWindow)", base64_image=b64)

    def _isolated_key(self, keys: str) -> ToolResult:
        """Send a key combo via WM_KEYDOWN/WM_KEYUP to the isolated HWND."""
        try:
            import win32api, win32con  # type: ignore  # noqa: F401
        except ImportError:
            return ToolResult(ok=False, output="Isolated key control is only available on Windows.")
        hwnd = self.resolve_isolated_hwnd()
        if not hwnd:
            return ToolResult(ok=False, output=f"Target window '{self._isolated_app or 'isolated app'}' not found.")
        err = self._assert_hwnd_responsive(hwnd)
        if err:
            return ToolResult(ok=False, output=err)
        VK_MAP = {
            "enter": win32con.VK_RETURN, "return": win32con.VK_RETURN,
            "escape": win32con.VK_ESCAPE, "esc": win32con.VK_ESCAPE,
            "tab": win32con.VK_TAB, "backspace": win32con.VK_BACK,
            "delete": win32con.VK_DELETE, "space": win32con.VK_SPACE,
            "up": win32con.VK_UP, "down": win32con.VK_DOWN,
            "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
            "home": win32con.VK_HOME, "end": win32con.VK_END,
            "ctrl": win32con.VK_CONTROL, "shift": win32con.VK_SHIFT,
            "alt": win32con.VK_MENU,
            "f1": win32con.VK_F1, "f2": win32con.VK_F2, "f3": win32con.VK_F3,
            "f4": win32con.VK_F4, "f5": win32con.VK_F5, "f6": win32con.VK_F6,
            "f7": win32con.VK_F7, "f8": win32con.VK_F8, "f9": win32con.VK_F9,
            "f10": win32con.VK_F10, "f11": win32con.VK_F11, "f12": win32con.VK_F12,
        }
        parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
        vk_codes = []
        for p in parts:
            if p in VK_MAP:
                vk_codes.append(VK_MAP[p])
            elif len(p) == 1:
                vk_codes.append(ord(p.upper()))
        for vk in vk_codes:
            win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
            time.sleep(0.02)
        for vk in reversed(vk_codes):
            win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)
            time.sleep(0.02)
        return ToolResult(ok=True, output=f"Isolated key: {keys}")

    def _looks_like_windows_absolute_path(self, text: str) -> bool:
        candidate = (text or "").strip().strip('"')
        return bool(re.match(r"^[A-Za-z]:\\", candidate) or candidate.startswith("\\\\"))

    def _paste_via_clipboard(self, text: str, *, isolated: bool) -> Optional[ToolResult]:
        try:
            import pyperclip
        except ImportError:
            return None

        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None

        try:
            pyperclip.copy(text)
            time.sleep(0.05)
            if isolated:
                result = self._isolated_key("ctrl+v")
                if not result.ok:
                    return result
            else:
                import pyautogui
                pyautogui.hotkey("ctrl", "v")
            time.sleep(0.05)
            return ToolResult(ok=True, output="Pasted text via clipboard")
        except Exception:
            return None
        finally:
            if previous is not None:
                try:
                    pyperclip.copy(previous)
                except Exception:
                    pass

    def _maybe_paste_path(self, text: str, *, isolated: bool) -> Optional[ToolResult]:
        if not self._looks_like_windows_absolute_path(text):
            return None
        return self._paste_via_clipboard(text, isolated=isolated)

    async def _cursor_position_bg(self):
        # Background browser doesn't track cursor in same way
        return ToolResult(ok=True, output="Cursor position not applicable in background mode", data={"x": 0, "y": 0})

    async def _computer_action_bg(self, action: str, **kwargs):
        action = action.lower().strip()
        if action == "screenshot":
            return await self._screenshot_bg()
        if action == "mouse_move":
            return await self._mouse_move_bg(kwargs["x"], kwargs["y"], kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "left_click":
            return await self._mouse_click_bg(kwargs["x"], kwargs["y"], "left", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "double_click":
            return await self._mouse_click_bg(kwargs["x"], kwargs["y"], "left", 2, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "right_click":
            return await self._mouse_click_bg(kwargs["x"], kwargs["y"], "right", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "middle_click":
            return await self._mouse_click_bg(kwargs["x"], kwargs["y"], "middle", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "left_click_drag":
            return await self._left_click_drag_bg(kwargs["x"], kwargs["y"], kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "key":
            return await self._key_bg(kwargs["keys"])
        if action == "type":
            return await self._keyboard_type_bg(kwargs["text"])
        if action == "scroll":
            return await self._scroll_bg(kwargs.get("amount", 0), kwargs.get("x"), kwargs.get("y"), kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "cursor_position":
            return await self._cursor_position_bg()
        if action == "wait":
            seconds = kwargs.get("seconds", 1.0)
            await asyncio.sleep(seconds)
            return ToolResult(ok=True, output=f"Waited {seconds} seconds (background)")
        raise ToolError(f"Unsupported computer action: {action}")

    def cursor_position(self):
        import pyautogui
        x, y = pyautogui.position()
        return ToolResult(ok=True, output=f"Cursor at {x}, {y}", data={"x": x, "y": y})

    # ── other tools (no pyautogui needed) ────────────────────────────────

    def find_on_screen(self, image_path: str):
        if self._background_mode:
            return ToolResult(ok=False, output="find_on_screen is not available in background mode. Use browser selectors instead.")
        import pyautogui
        try:
            p = self._safe_path(image_path)
            res = pyautogui.locateOnScreen(str(p))
            if res:
                return ToolResult(ok=True, output=f"Found at {res}")
            return ToolResult(ok=False, output="Not found on screen")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    def get_clipboard(self):
        try:
            import pyperclip
            text = pyperclip.paste()
            return ToolResult(ok=True, output=text)
        except ImportError:
            return ToolResult(ok=False, output="pyperclip not installed")

    def set_clipboard(self, text: str):
        try:
            import pyperclip
            pyperclip.copy(text)
            return ToolResult(ok=True, output="Clipboard updated")
        except ImportError:
            return ToolResult(ok=False, output="pyperclip not installed")

    def notify(self, message: str):
        try:
            from plyer import notification
            notification.notify(title="Orynn", message=message, timeout=5)
            return ToolResult(ok=True, output="Notification sent")
        except ImportError:
            return ToolResult(ok=False, output="plyer not installed")

    def wait_action(self, seconds: float):
        requested = float(seconds)
        seconds = min(requested, 60.0)
        time.sleep(seconds)
        if seconds < requested:
            return ToolResult(ok=True, output=f"Waited {seconds:.1f}s (capped from {requested:.1f}s; max is 60s)")
        return ToolResult(ok=True, output=f"Waited {seconds:.1f} seconds")

    def ocr_image(self):
        if not pytesseract:
            return ToolResult(ok=False, output="pytesseract not installed")
        if self._background_mode:
            return ToolResult(ok=False, output="OCR not available in background mode. Use browser_get_text instead.")
        import pyautogui
        img = pyautogui.screenshot()
        text = pytesseract.image_to_string(img)
        return ToolResult(ok=True, output=text)

    def focus_window(self, title: str) -> ToolResult:
        """Bring the first visible window whose title contains `title` to the foreground."""
        try:
            import win32gui, win32com.client  # type: ignore
            found: list = []

            def _enum(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
                    found.append(hwnd)

            win32gui.EnumWindows(_enum, None)
            if not found:
                return ToolResult(ok=False, output=f"No window with title containing '{title}' found.")
            hwnd = found[0]
            actual_title = win32gui.GetWindowText(hwnd)
            import time
            # Windows blocks SetForegroundWindow under foreground-lock and raises
            # pywintypes.error(0, 'SetForegroundWindow'). That is NOT a real
            # failure — the window is usually activated anyway, and our UIA tools
            # target by window title regardless of foreground. So try several
            # activation methods best-effort and only hard-fail if NONE plausibly
            # worked. (Restoring a minimized window + AppActivate is what actually
            # clears the lock in practice.)
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            except Exception:
                pass
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                shell.AppActivate(actual_title)  # bypasses foreground-lock
            except Exception:
                pass
            time.sleep(0.2)
            try:
                win32gui.BringWindowToTop(hwnd)
            except Exception:
                pass
            foregrounded = False
            try:
                win32gui.SetForegroundWindow(hwnd)
                foregrounded = True
            except Exception:
                # Verify whether it ended up foreground anyway (AppActivate often
                # succeeds even when SetForegroundWindow is refused).
                try:
                    foregrounded = (win32gui.GetForegroundWindow() == hwnd)
                except Exception:
                    foregrounded = False
            self.set_isolated_hwnd(hwnd, actual_title)
            if win32process is not None:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    self._remember_started_pid(pid)
                except Exception:
                    pass
            note = "" if foregrounded else " (activated; OS held foreground — UIA still targets it by title)"
            return ToolResult(ok=True, output=f"Focused window: '{actual_title}'{note}")
        except Exception as e:
            return ToolResult(ok=False, output=f"focus_window failed: {e}")

    def wait_for_window(self, title: str = "", timeout: float = 10.0, paint_seconds: float = 0.35):
        if win32gui is None:
            return ToolResult(ok=False, output="wait_for_window is only available on Windows.")
        needle = (title or self._isolated_app or "").strip()
        if not needle:
            return ToolResult(ok=False, output="wait_for_window needs a title substring or an isolated target app.")

        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            matches = self._iter_matching_windows(needle)
            if matches:
                match = matches[0]
                hwnd = int(match["hwnd"])
                actual_title = match["title"] or needle
                pid = match.get("pid")
                self.set_isolated_hwnd(hwnd, actual_title)
                self._remember_started_pid(pid)
                time.sleep(max(0.0, float(paint_seconds)))
                return ToolResult(
                    ok=True,
                    output=f"Window ready: '{actual_title}' (pid {pid or '?'})",
                    data={"hwnd": hwnd, "pid": pid, "title": actual_title},
                )
            time.sleep(0.1)
        return ToolResult(ok=False, output=f"Timed out waiting for a visible window matching '{needle}'.")

    def _auto_wait_after_launch(self, command: str):
        title_hint = self._guess_launch_target_title(command)
        if not title_hint:
            return None
        wait_result = self.wait_for_window(title_hint, timeout=10.0)
        if wait_result.ok and wait_result.data:
            self.set_isolated_hwnd(wait_result.data.get("hwnd"), wait_result.data.get("title", title_hint))
            self._remember_started_pid(wait_result.data.get("pid"))
        return wait_result

    # Apps where a second "start" just litters a duplicate window — safe to
    # focus the existing one instead. Deliberately excludes browsers, editors
    # (VS Code/Cursor), Explorer, and any URL/file target, where a fresh window
    # is a legitimate intent.
    _SINGLE_INSTANCE_APPS = {"Notepad", "Calculator", "Paint"}

    def _reuse_existing_window(self, command: str) -> Optional[ToolResult]:
        title = self._guess_launch_target_title(command)
        if title not in self._SINGLE_INSTANCE_APPS:
            return None
        try:
            matches = self._iter_matching_windows(title)
        except Exception:
            return None
        if not matches:
            return None
        focused = self.focus_window(title)
        if focused.ok:
            focused.output = f"{title} already open — focused it (no duplicate launched).\n{focused.output}"
        return focused if focused.ok else None

    def _launch_gui_command(self, command: str, cwd: Path) -> ToolResult:
        reuse = self._reuse_existing_window(command)
        if reuse is not None:
            return reuse
        try:
            popen_kwargs = {
                "shell": True,
                "cwd": cwd,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            creationflags = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
            subprocess.Popen(
                command,
                **popen_kwargs,
            )
        except Exception as e:
            return ToolResult(ok=False, output=f"Launch failed: {e}")

        launch_output = f"Launched (fire-and-forget): {command}\nCWD:\n{cwd}"
        wait_result = self._auto_wait_after_launch(command)
        if wait_result is None:
            return ToolResult(ok=True, output=launch_output)
        if wait_result.ok:
            return ToolResult(ok=True, output=f"{launch_output}\n{wait_result.output}", data=wait_result.data)
        return ToolResult(ok=False, output=f"{launch_output}\n{wait_result.output}")

    def run_command(self, command: str):
        try:
            import re
            mkdir_p = re.fullmatch(r'(?:mkdir|md)\s+-p\s+["\']?(.+?)["\']?', command.strip(), flags=re.IGNORECASE)
            if mkdir_p:
                target = self._safe_path(mkdir_p.group(1).strip())
                target.mkdir(parents=True, exist_ok=True)
                return ToolResult(ok=True, output=f"Created directory: {target}")
            if self._looks_like_gui_launch(command):
                return self._launch_gui_command(command, self.workspace)
            res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, cwd=self.workspace)
            return ToolResult(ok=res.returncode == 0, output=f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="Command timed out after 120 seconds.")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    async def run_command_streaming(
        self,
        command: str,
        on_chunk: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> ToolResult:
        if self._looks_like_gui_launch(command):
            return self._launch_gui_command(command, self.workspace)
        return await self._stream_subprocess(command, self.workspace, on_chunk)

    def bash(self, command: str, restart: bool = False):
        if restart:
            self._bash_cwd = self.workspace

        stripped = command.strip()
        if stripped.lower() in {"pwd", "cd"}:
            return ToolResult(ok=True, output=str(self._bash_cwd))

        try:
            import re
            mkdir_p = re.fullmatch(r'(?:mkdir|md)\s+-p\s+["\']?(.+?)["\']?', stripped, flags=re.IGNORECASE)
            if mkdir_p:
                target = self._safe_path(mkdir_p.group(1).strip())
                target.mkdir(parents=True, exist_ok=True)
                return ToolResult(ok=True, output=f"Created directory: {target}")
        except Exception as e:
            return ToolResult(ok=False, output=f"mkdir -p failed: {e}")

        cd_match = None
        try:
            import re
            cd_and_run = re.fullmatch(r'cd\s+["\']?(.+?)["\']?\s*(?:&&|;)\s*(.+)', stripped, flags=re.IGNORECASE)
            if cd_and_run:
                target = self._safe_path(cd_and_run.group(1).strip())
                if not target.is_dir():
                    return ToolResult(ok=False, output=f"Not a directory: {target}")
                self._bash_cwd = target
                stripped = cd_and_run.group(2).strip()
                command = stripped
            cd_match = re.fullmatch(r'cd\s+["\']?(.+?)["\']?', stripped, flags=re.IGNORECASE)
        except Exception:
            cd_match = None

        if cd_match:
            target = self._safe_path(cd_match.group(1))
            if not target.is_dir():
                return ToolResult(ok=False, output=f"Not a directory: {target}")
            self._bash_cwd = target
            return ToolResult(ok=True, output=f"Changed directory to {target}")

        # ── Detect fire-and-forget GUI launch commands ──
        # On Windows, `start <app>` / `explorer` / `cmd /c start` launch a GUI process
        # and the parent cmd.exe may never exit, causing subprocess.run to hang.
        # For these, use Popen without waiting.
        if self._looks_like_gui_launch(command):
            return self._launch_gui_command(command, self._bash_cwd)

        try:
            res = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self._bash_cwd,
            )
            output = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}\nCWD:\n{self._bash_cwd}"
            return ToolResult(ok=res.returncode == 0, output=output)
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="Bash command timed out after 60 seconds.")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    async def bash_streaming(
        self,
        command: str,
        restart: bool = False,
        on_chunk: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> ToolResult:
        if restart:
            self._bash_cwd = self.workspace

        stripped = command.strip()
        if stripped.lower() in {"pwd", "cd"}:
            return ToolResult(ok=True, output=str(self._bash_cwd))

        try:
            import re
            cd_match = re.fullmatch(r'cd\s+["\']?(.+?)["\']?', stripped, flags=re.IGNORECASE)
        except Exception:
            cd_match = None

        if cd_match:
            target = self._safe_path(cd_match.group(1))
            if not target.is_dir():
                return ToolResult(ok=False, output=f"Not a directory: {target}")
            self._bash_cwd = target
            return ToolResult(ok=True, output=f"Changed directory to {target}")

        # GUI launches (`start notepad`, `explorer`, etc.) keep stdout/stderr
        # pipes open until the app closes — streaming would block for the full
        # timeout. Detach and fire-and-forget instead.
        if self._looks_like_gui_launch(command):
            return self._launch_gui_command(command, self._bash_cwd)

        return await self._stream_subprocess(command, self._bash_cwd, on_chunk, include_cwd=True)

    async def _stream_subprocess(
        self,
        command: str,
        cwd: Path,
        on_chunk: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        include_cwd: bool = False,
    ) -> ToolResult:
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _MAX_CHUNK_BYTES = 10 * 1024 * 1024  # 10 MB cap; prevents OOM on long-running commands
            chunks: list[str] = []
            _accumulated = [0]  # mutable cell shared across both pump coroutines

            async def _pump(stream, channel: str):
                while True:
                    chunk = await stream.read(512)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    if _accumulated[0] < _MAX_CHUNK_BYTES:
                        chunks.append(text)
                        _accumulated[0] += len(text)
                        if _accumulated[0] >= _MAX_CHUNK_BYTES:
                            chunks.append(f"\n[... output truncated at {_MAX_CHUNK_BYTES // 1_048_576} MB ...]\n")
                    if on_chunk:
                        await on_chunk({"channel": channel, "output": text})

            await asyncio.gather(_pump(proc.stdout, "stdout"), _pump(proc.stderr, "stderr"))
            returncode = await proc.wait()
            output = "".join(chunks)
            if include_cwd:
                output = f"{output}\nCWD:\n{cwd}"
            return ToolResult(ok=returncode == 0, output=output or "(no output)")
        except asyncio.CancelledError:
            # Outer wait_for timed out — kill the child so it doesn't become an orphan
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise
        except asyncio.TimeoutError:
            # Defensive: raised if a caller uses asyncio.wait_for on Python <3.11
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return ToolResult(ok=False, output="Command timed out.")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    _MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard cap for read/write

    def read_file(self, path: str):
        p = self._safe_path(path)
        try:
            size = p.stat().st_size
            if size > self._MAX_FILE_BYTES:
                return ToolResult(ok=False, output=f"File too large to read ({size / 1_048_576:.1f} MB); max 50 MB.")
        except OSError:
            pass  # non-existent file — let read_bytes raise naturally
        return ToolResult(ok=True, output=self._read_text_file(p))

    def write_file(self, path: str, content: str):
        # LLMs often over-escape newlines in JSON strings as literal \n
        content = content.replace("\\n", "\n").replace("\\t", "\t")
        if len(content.encode('utf-8')) > self._MAX_FILE_BYTES:
            return ToolResult(ok=False, output="Content too large to write (max 50 MB).")
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote to {path}")

    def move_file(self, source: str, destination: str):
        src = self._safe_path(source)
        dst = self._safe_path(destination)
        shutil.move(str(src), str(dst))
        return ToolResult(ok=True, output=f"Moved {source} to {destination}")

    def system_info(self):
        """Return OS info, home dir, workspace, and common folder paths."""
        import platform
        home = self.home_dir
        info = {
            "os": platform.system(),
            "platform": platform.platform(),
            "home": str(home),
            "workspace": str(self.workspace),
            "downloads": str(home / "Downloads"),
            "desktop": str(home / "Desktop"),
            "documents": str(home / "Documents"),
            "cwd": str(Path.cwd()),
            "user": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
            "python": "python" if platform.system() == "Windows" else "python3",
            "background_mode": self._background_mode,
            "allowed_roots": [str(root) for root in self.allowed_roots],
        }
        return ToolResult(ok=True, output=json.dumps(info, indent=2))

    async def list_mcp_servers(self) -> ToolResult:
        from .mcp_manager import mcp_manager

        await mcp_manager.initialize_default_servers(str(self.workspace))
        if not mcp_manager.servers:
            return ToolResult(ok=True, output="No MCP servers are currently registered.")

        lines = []
        for name, server in sorted(mcp_manager.servers.items()):
            tool_count = len(getattr(server, "tools", []) or [])
            cmd_preview = " ".join(server.cmd[:4])
            if len(server.cmd) > 4:
                cmd_preview += " ..."
            lines.append(f"{name}: {tool_count} tools | {cmd_preview}")
        return ToolResult(ok=True, output="\n".join(lines))

    async def list_mcp_tools(self, server_name: str) -> ToolResult:
        from .mcp_manager import mcp_manager

        await mcp_manager.initialize_default_servers(str(self.workspace))
        server = mcp_manager.servers.get(server_name)
        if server is None:
            raise ToolError(f"MCP server not registered: {server_name}")

        tools = getattr(server, "tools", []) or []
        if not tools:
            return ToolResult(ok=True, output=f"{server_name} exposes no MCP tools.")

        lines = []
        for tool in tools:
            name = str(tool.get("name", "")).strip()
            if not name:
                continue
            description = str(tool.get("description", "")).strip()
            input_schema = tool.get("inputSchema")
            schema_hint = ""
            if isinstance(input_schema, dict):
                props = input_schema.get("properties")
                if isinstance(props, dict) and props:
                    schema_hint = " args: " + ", ".join(sorted(str(key) for key in props.keys()))
            line = name
            if description:
                line += f" — {description}"
            if schema_hint:
                line += schema_hint
            lines.append(line)
        return ToolResult(ok=True, output="\n".join(lines) if lines else f"{server_name} exposes no named MCP tools.")

    def list_directory(self, path: str, max_depth: int = 2):
        """List directory contents. Accepts absolute or workspace-relative paths."""
        p = self._safe_path(path)
        if not p.exists():
            return ToolResult(ok=False, output=f"Path does not exist: {path}")
        if not p.is_dir():
            return ToolResult(ok=False, output=f"Not a directory: {path}")
        entries = []
        root_depth = len(p.parts)
        for dirpath, dirnames, filenames in os.walk(p):
            current = Path(dirpath)
            depth = len(current.parts) - root_depth
            if depth > max_depth:
                dirnames[:] = []  # prune — don't recurse past max_depth
                continue
            dirnames.sort()
            if depth > 0:
                indent = "  " * (depth - 1)
                entries.append(f"{indent}📁 {current.name}/")
            file_indent = "  " * depth
            for fname in sorted(filenames):
                fpath = current / fname
                size = ""
                try:
                    sz = fpath.stat().st_size
                    size = f" ({sz:,} bytes)" if sz < 1_000_000 else f" ({sz/1_000_000:.1f} MB)"
                except OSError:
                    pass
                entries.append(f"{file_indent}📄 {fname}{size}")
        if not entries:
            entries = ["(empty directory)"]
        header = f"Directory: {p}\n{'─' * 40}"
        return ToolResult(ok=True, output=f"{header}\n" + "\n".join(entries))

    def file_glob(self, pattern: str):
        import glob
        raw = Path(pattern)
        if raw.is_absolute() or ".." in raw.parts:
            raise ToolError("Glob pattern must be relative and stay inside the workspace.")
        matches = glob.glob(str(self.workspace / pattern), recursive=True)
        rel_matches = []
        for match in matches:
            path = Path(match).resolve()
            try:
                rel_matches.append(str(path.relative_to(self.workspace)))
            except ValueError:
                raise ToolError("Glob pattern escaped workspace.")
        return ToolResult(ok=True, output="\n".join(rel_matches) if rel_matches else "No matches found.")

    GREP_SKIP_DIRS = {
        '.git', 'node_modules', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.next', '.cache', 'coverage', 'htmlcov',
    }

    def file_grep(self, pattern: str, directory: str = "."):
        import re
        p = self._safe_path(directory)
        matches = []
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(ok=False, output=f"Invalid regex pattern: {exc}")
        for root, dirnames, files in os.walk(p):
            # Prune directories we never want to search
            dirnames[:] = [
                d for d in dirnames
                if d not in self.GREP_SKIP_DIRS and not d.endswith('.egg-info')
            ]
            for file in files:
                filepath = Path(root) / file
                try:
                    content = filepath.read_text(encoding="utf-8")
                    for i, line in enumerate(content.splitlines(), 1):
                        if regex.search(line):
                            rel_path = filepath.relative_to(self.workspace) if self.workspace in filepath.parents else filepath
                            matches.append(f"{rel_path}:{i}: {line.strip()}")
                except Exception:
                    pass
        return ToolResult(ok=True, output="\n".join(matches) if matches else "No matches found.")

    def run_and_watch(self, command: str, watch_seconds: float = 10.0):
        """Start a process, watch its stdout+stderr for `watch_seconds`,
        kill it cleanly, return everything captured plus exit metadata.

        Use case: launch an app/server and observe its first few seconds of
        output for crashes/errors before deciding what to fix. Different from
        `bash` because we KILL the process at the end rather than waiting for
        natural exit, so this works for long-running servers."""
        try:
            watch_seconds = max(0.5, min(float(watch_seconds), 120.0))
        except (TypeError, ValueError):
            watch_seconds = 10.0
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.workspace),
                errors="replace",
            )
            self._remember_started_pid(proc.pid)
        except Exception as e:
            return ToolResult(ok=False, output=f"run_and_watch failed to spawn: {e}")

        still_running = False
        exit_code = None
        try:
            stdout, stderr = proc.communicate(timeout=watch_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            still_running = True
            exit_code = -1
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            return ToolResult(ok=False, output=f"run_and_watch error: {e}")

        stdout = (stdout or "")[-8000:]
        stderr = (stderr or "")[-8000:]
        if still_running:
            label = f"[killed after {watch_seconds:.1f}s — process was still running]"
        else:
            label = f"[exited with code {exit_code}]"
        body = f"{label}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        return ToolResult(
            ok=True,
            output=body,
            data={
                "exit_code": exit_code,
                "killed": still_running,
                "stdout": stdout,
                "stderr": stderr,
            },
        )

    def ui_critique(self, focus: str = ""):
        """Take a desktop screenshot and return a structured prompt asking
        the model to enumerate visible UI issues. The screenshot rides along
        as base64_image so the next LLM turn can see and analyze it."""
        shot = self.screenshot()
        if not shot.ok or not shot.base64_image:
            return ToolResult(ok=False, output="ui_critique: failed to capture screenshot")
        focus_hint = f" Focus area: {focus}." if focus else ""
        prompt = (
            "UI critique. Look at the screenshot above carefully and list the "
            "top 3-5 specific UI issues you can see. For each issue, give:\n"
            "  - a one-line description of what's wrong (clutter, alignment, "
            "redundancy, info density, contrast, etc.)\n"
            "  - a hypothesis about which CSS/HTML element to change "
            "(selector, id, class, or pixel coordinates)\n"
            f"{focus_hint} Respond as numbered bullets. Then in your NEXT "
            "action either edit the relevant file to fix the top issue or "
            "take another screenshot to verify a previous fix landed."
        )
        return ToolResult(ok=True, output=prompt, base64_image=shot.base64_image)

    def analyze_folder(self, path: str = "", action: str = "scan"):
        """Scan a real folder and push results as a Generative UI widget to the capsule.

        This is the LLM-to-capsule bridge. The LLM calls this tool with a
        dynamic path, and the result appears as an interactive widget on the
        user's desktop.
        """
        from .clutter_scanner import scan_folder, organize_files
        from .capsule_bridge import build_list_widget, build_status_widget, push_widget

        # Resolve path — support ~, relative, and absolute
        if not path or path.strip() in ("", "~", "."):
            resolved = str(Path.home() / "Downloads")
        elif path.startswith("~"):
            resolved = str(Path.home() / path[2:].lstrip("/\\"))
        elif not Path(path).is_absolute():
            resolved = str(Path.home() / path)
        else:
            resolved = path

        if not os.path.isdir(resolved):
            return ToolResult(ok=False, output=f"Folder not found: {resolved}")

        folder_name = os.path.basename(resolved) or resolved

        if action == "organize":
            result = organize_files(resolved)
            spec = build_status_widget(
                title=f"Organized {folder_name}",
                text=f"Moved {result['count']} files into category folders.\n"
                     + (f"Errors: {len(result['errors'])}" if result['errors'] else "No errors."),
                icon="folder-open",
            )
            push_widget(spec)
            return ToolResult(ok=True, output=f"Organized {result['count']} files in {resolved}")

        # Default: scan
        scan_data = scan_folder(resolved)
        spec = build_list_widget(
            title=f"Clutter in {folder_name}",
            items=scan_data.get("files", []),
            folder_path=scan_data.get("folder_path", resolved),
            icon="broom",
        )
        push_widget(spec)

        file_count = len(scan_data.get("files", []))
        total = scan_data.get("total_size", "0 B")
        return ToolResult(
            ok=True,
            output=f"Scanned {folder_name}: {file_count} files, {total} total. "
                   f"Widget spawned in capsule showing the files sorted by size.",
        )

    def show_widget(self, spec: dict):
        """Push ANY JSON widget spec to the Qt capsule.

        This is the universal Generative UI tool. The LLM designs
        the widget by outputting a JSON spec, and the capsule renders it.
        """
        from .capsule_bridge import push_widget

        if not spec or not isinstance(spec, dict):
            return ToolResult(ok=False, output="show_widget: spec must be a JSON object with at least 'title'")
        if "title" not in spec:
            return ToolResult(ok=False, output="show_widget: 'title' field is required")

        ok = push_widget(spec)
        title = spec.get("title", "widget")
        item_count = len(spec.get("items", []))
        btn_count = len(spec.get("buttons", []))

        parts = [f"Widget '{title}' pushed to capsule"]
        if item_count:
            parts.append(f"{item_count} items")
        if btn_count:
            parts.append(f"{btn_count} buttons")
        if not ok:
            parts.append("(capsule may not be connected)")

        return ToolResult(ok=True, output=". ".join(parts) + ".")

    def screen_context(self):
        """Capture the user's screen and return base64 image + OCR text.

        Gives the LLM visual awareness of what the user is looking at.
        """
        from .capsule_bridge import capture_screen_b64, capture_screen_text

        b64 = capture_screen_b64()
        if not b64:
            return ToolResult(ok=False, output="screen_context: failed to capture screen")

        ocr_text = capture_screen_text()
        output = "Screen captured."
        if ocr_text:
            output += f"\n\nExtracted text from screen:\n{ocr_text}"
        else:
            output += "\n\nOCR unavailable — use the screenshot image for visual analysis."

        return ToolResult(ok=True, output=output, base64_image=b64)

    def todo_write(self, items):
        """Persist an explicit task plan for this task.

        Stored on the executor instance + mirrored to a small JSON file in the
        workspace so the agent can survive restarts and the UI can pick it up
        via a future endpoint. Returns the structured list back as the output.
        """
        if not isinstance(items, list):
            return ToolResult(ok=False, output="todo_write: 'items' must be a list of objects")
        cleaned = []
        valid_status = {"pending", "in_progress", "completed"}
        in_progress_count = 0
        for raw in items:
            if not isinstance(raw, dict):
                return ToolResult(ok=False, output="todo_write: each item must be an object with content/activeForm/status")
            content = str(raw.get("content", "")).strip()
            active = str(raw.get("activeForm", "") or content).strip()
            status = str(raw.get("status", "pending")).strip().lower()
            if not content:
                continue
            if status not in valid_status:
                status = "pending"
            if status == "in_progress":
                in_progress_count += 1
            cleaned.append({"content": content, "activeForm": active, "status": status})
        if in_progress_count > 1:
            # Coerce to one — keep the first in_progress, demote the rest.
            seen = False
            for item in cleaned:
                if item["status"] == "in_progress":
                    if seen:
                        item["status"] = "pending"
                    else:
                        seen = True
        # Persist on the executor (per-task) and to disk for observability.
        try:
            self._todos = cleaned
        except Exception:
            pass
        try:
            todos_path = self.workspace / ".agent_todos.json"
            todos_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        # Pretty-print summary for the LLM's own next-iteration context.
        lines = []
        for item in cleaned:
            mark = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}.get(item["status"], "[ ]")
            label = item["activeForm"] if item["status"] == "in_progress" else item["content"]
            lines.append(f"{mark} {label}")
        summary = f"Plan ({len(cleaned)} item{'s' if len(cleaned) != 1 else ''}):\n" + "\n".join(lines) if cleaned else "Plan cleared."
        return ToolResult(ok=True, output=summary, data={"todos": cleaned})

    def memory_recall(self, query: str) -> ToolResult:
        """Search long-term memory for relevant past session summaries."""
        if not query or not query.strip():
            return ToolResult(ok=False, output="memory_recall: query must not be empty")
        if self.memory is None:
            return ToolResult(ok=True, output="memory_recall: no memory store attached to this executor")
        try:
            items = self.memory.recall_sessions(query.strip(), n=5)
            if not items:
                items = self.memory.search(query.strip(), limit=5)
            if not items:
                return ToolResult(ok=True, output="No relevant memories found.")
            lines = [f"[{i+1}] {item.content}" for i, item in enumerate(items)]
            return ToolResult(ok=True, output="Relevant past sessions:\n" + "\n".join(lines))
        except Exception as e:
            return ToolResult(ok=False, output=f"memory_recall error: {e}")

    def delegate_coding(
        self,
        task: str,
        repo_path: str = "",
        files: Optional[list[str]] = None,
        constraints: str = "",
        backend: str = "",
    ) -> ToolResult:
        """Delegate a coding-heavy subtask to a connected coding backend."""
        task = str(task or "").strip()
        if not task:
            return ToolResult(ok=False, output="delegate_coding: task must not be empty")

        from .coding_backends import CodingBrief, registry

        selected = registry.get(backend or None)
        if selected is None:
            return ToolResult(
                ok=False,
                output="delegate_coding: no coding backend is available. Continue locally.",
            )

        availability = selected.detect()
        if not availability.get("available"):
            detail = availability.get("detail") or "backend unavailable"
            return ToolResult(
                ok=False,
                output=f"delegate_coding: backend '{selected.name}' is unavailable ({detail}). Continue locally.",
                data={
                    "backend": selected.name,
                    "available": False,
                    "detail": detail,
                },
            )

        repo_root = repo_path or str(self.workspace)
        brief = CodingBrief(
            task=task,
            repo_path=repo_root,
            files=[str(item) for item in (files or []) if str(item).strip()],
            constraints=str(constraints or "").strip(),
        )
        result = selected.submit(brief)
        payload = result.to_dict()
        payload["backend"] = selected.name
        if result.ok:
            summary = result.summary or "Delegated coding task completed."
            return ToolResult(
                ok=True,
                output=f"Delegated to {selected.name}: {summary}",
                data=payload,
            )
        return ToolResult(
            ok=False,
            output=f"delegate_coding: {result.error or 'backend failed to complete the task'}",
            data=payload,
        )

    def pixel_color_at(self, x: int, y: int):
        """Read the RGB hex of a single desktop pixel."""
        try:
            x = int(x); y = int(y)
            with mss.mss() as sct:
                bbox = {"left": x, "top": y, "width": 1, "height": 1}
                shot = sct.grab(bbox)
                # mss returns BGRA; index 2,1,0 = R,G,B
                px = shot.pixel(0, 0)
                r, g, b = px[0], px[1], px[2]
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            return ToolResult(ok=True, output=hex_color, data={"r": r, "g": g, "b": b, "hex": hex_color, "x": x, "y": y})
        except Exception as e:
            return ToolResult(ok=False, output=f"pixel_color_at error: {e}")

    def diff_files(self, path_a: str, path_b: str):
        """Return a unified diff between two files (capped at ~20k chars)."""
        try:
            import difflib
            a_path = Path(path_a)
            b_path = Path(path_b)
            if not a_path.exists():
                return ToolResult(ok=False, output=f"path_a not found: {path_a}")
            if not b_path.exists():
                return ToolResult(ok=False, output=f"path_b not found: {path_b}")
            try:
                a = a_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                b = b_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            except Exception as e:
                return ToolResult(ok=False, output=f"read error: {e}")
            diff = "".join(difflib.unified_diff(a, b, fromfile=str(a_path), tofile=str(b_path), n=3))
            if not diff:
                return ToolResult(ok=True, output="(files are identical)")
            if len(diff) > 20000:
                diff = diff[:20000] + "\n... (truncated)"
            return ToolResult(ok=True, output=diff)
        except Exception as e:
            return ToolResult(ok=False, output=f"diff_files error: {e}")

    def extract_links(self, url: str):
        """Fetch a URL and return a list of (text, href) pairs as JSON."""
        try:
            safe_url = _validate_public_http_url(url)
        except ToolError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            import urllib.parse
            raw, final_url = _read_public_http_url(safe_url, max_bytes=2_000_000)
            try:
                html = raw.decode('utf-8')
            except UnicodeDecodeError:
                html = raw.decode('utf-8', errors='replace')
            # Pull <a href="...">text</a> pairs (non-greedy, case-insensitive).
            link_re = re.compile(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                re.IGNORECASE | re.DOTALL,
            )
            tag_re = re.compile(r'<[^>]+>')
            seen = set()
            links = []
            for m in link_re.finditer(html):
                href = m.group(1).strip()
                text = tag_re.sub('', m.group(2))
                text = ' '.join(text.split())[:200]
                if not href or href.startswith('#'):
                    continue
                # Resolve relative URLs against the source.
                full = urllib.parse.urljoin(final_url, href)
                try:
                    full = _validate_public_http_url(full)
                except ToolError:
                    continue
                key = (text, full)
                if key in seen:
                    continue
                seen.add(key)
                links.append({"text": text, "href": full})
                if len(links) >= 200:
                    break
            return ToolResult(
                ok=True,
                output=json.dumps(links, ensure_ascii=False, indent=2)[:20000],
                data={"count": len(links), "links": links},
            )
        except Exception as e:
            return ToolResult(ok=False, output=f"extract_links error: {e}")

    def web_fetch(self, url: str):
        try:
            safe_url = _validate_public_http_url(url)
        except ToolError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            # Cap body to ~1 MB to avoid blowing memory on adversarial servers
            raw, final_url = _read_public_http_url(safe_url, max_bytes=1_000_000)
            try:
                html = raw.decode('utf-8')
            except UnicodeDecodeError:
                html = raw.decode('utf-8', errors='replace')
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = ' '.join(text.split())
            return ToolResult(
                ok=True,
                output=wrap_untrusted_web_content(text[:20000], source=final_url, kind="web_fetch"),
            )
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    def web_search(self, query: str, max_results: int = 5):
        try:
            import html
            import re
            import urllib.parse
            import urllib.request

            encoded = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            raw, _final_url = _read_public_http_url(url, max_bytes=1_000_000)
            page = raw.decode("utf-8", errors="replace")

            results = []
            ddg_base = "https://duckduckgo.com/"
            pattern = re.compile(
                r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            for match in pattern.finditer(page):
                href = html.unescape(re.sub(r"<.*?>", "", match.group("href"))).strip()
                title = html.unescape(re.sub(r"<.*?>", "", match.group("title"))).strip()
                snippet = html.unescape(re.sub(r"<.*?>", "", match.group("snippet"))).strip()
                full_href = urllib.parse.urljoin(ddg_base, href)
                parsed_href = urllib.parse.urlsplit(full_href)
                if parsed_href.netloc.lower().endswith("duckduckgo.com") and parsed_href.path.startswith("/l/"):
                    redirect_params = urllib.parse.parse_qs(parsed_href.query)
                    uddg = redirect_params.get("uddg", [""])[0]
                    if uddg:
                        full_href = urllib.parse.unquote(uddg)
                try:
                    href = _validate_public_http_url(full_href)
                except ToolError:
                    continue
                if href and title:
                    results.append(f"{title}\n{href}\n{snippet}")
                if len(results) >= max_results:
                    break

            if not results:
                return ToolResult(ok=False, output=f"No search results found for: {query}")
            return ToolResult(
                ok=True,
                output=wrap_untrusted_web_content("\n\n".join(results), source=url, kind="web_search"),
            )
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    def text_editor_action(self, command: str, path: str, **kwargs):
        command = command.lower().strip()
        if command == "view":
            return self.text_editor.view(path, kwargs.get("view_range"))
        if command == "create":
            return self.text_editor.create(path, kwargs.get("file_text", ""))
        if command == "str_replace":
            return self.text_editor.str_replace(path, kwargs["old_str"], kwargs["new_str"])
        if command == "insert":
            return self.text_editor.insert(path, kwargs["insert_line"], kwargs["new_str"])
        if command == "undo_edit":
            return self.text_editor.undo_edit(path)
        if command == "lint":
            return self.lint_file(path)
        raise ToolError(f"Unsupported text_editor command: {command}")

    def lint_file(self, path: str):
        """Run a basic syntax check on a file (legacy — prefer lint_code)."""
        return self.lint_code(path)

    def lint_code(self, path: str):
        """Run real linters: flake8/mypy for Python, eslint/tsc for JS/TS."""
        import subprocess
        abs_path = self._safe_path(path)
        if not abs_path.exists():
            return ToolResult(ok=False, output=f"File not found: {path}")

        ext = abs_path.suffix.lower()
        results = []

        def _run(cmd, label):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=str(self.workspace))
                out = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    results.append(f"[{label}] ✓ clean")
                else:
                    if "No module named" in out and label != "syntax":
                        results.append(f"[{label}] skipped (not installed)")
                        return None
                    results.append(f"[{label}] issues:\n{out[:2000]}")
                return r.returncode == 0
            except FileNotFoundError:
                return None  # tool not installed
            except Exception as e:
                results.append(f"[{label}] error: {e}")
                return False

        if ext == ".py":
            # Syntax first
            ok = _run(["python", "-m", "py_compile", str(abs_path)], "syntax")
            # flake8 style (pyflakes is lighter, likely installed)
            if _run(["python", "-m", "flake8", "--max-line-length=120", str(abs_path)], "flake8") is None:
                _run(["python", "-m", "pyflakes", str(abs_path)], "pyflakes")
            # mypy type check
            _run(["python", "-m", "mypy", "--ignore-missing-imports", str(abs_path)], "mypy")
        elif ext in (".js", ".mjs", ".cjs"):
            _run(["node", "--check", str(abs_path)], "syntax")
            _run(["npx", "--yes", "eslint", "--no-eslintrc", "-c", "{}", str(abs_path)], "eslint")
        elif ext in (".ts", ".tsx"):
            _run(["npx", "--yes", "tsc", "--noEmit", "--allowJs", str(abs_path)], "tsc")
        else:
            return ToolResult(ok=True, output=f"No linter configured for {ext} files.")

        ok_all = all("[✓]" in r or "✓ clean" in r or "skipped" in r for r in results)
        return ToolResult(ok=ok_all, output="\n".join(results) if results else "No linters ran.")

    def git(self, command: str, args: str = ""):
        """Run a git command safely inside the workspace."""
        import re
        import shlex
        import subprocess
        BLOCKED = {"push", "reset --hard", "clean -f", "rm -rf"}
        command_text = (command + " " + args).strip()
        if not command_text:
            return ToolResult(ok=False, output="git error: command is required")
        if re.search(r"[&|;<>`\r\n]", command_text):
            return ToolResult(ok=False, output="Blocked: shell metacharacters are not allowed in git commands.")
        cmd_lower = command_text.lower()
        for b in BLOCKED:
            if b in cmd_lower:
                return ToolResult(ok=False, output=f"Blocked: '{b}' requires explicit user approval.")
        try:
            argv = ["git", *shlex.split(command_text, posix=True)]
        except ValueError as e:
            return ToolResult(ok=False, output=f"git parse error: {e}")
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=30, cwd=str(self.workspace)
            )
            out = (r.stdout + r.stderr).strip()
            return ToolResult(ok=(r.returncode == 0), output=out or "(no output)")
        except Exception as e:
            return ToolResult(ok=False, output=f"git error: {e}")

    def run_tests(self, command: str = "", path: str = "."):
        """Run test suite and return structured pass/fail summary."""
        import subprocess, re
        cwd = str(self._safe_path(path))
        # Auto-detect if no command given
        if not command:
            if (self.workspace / "pytest.ini").exists() or (self.workspace / "setup.cfg").exists() or (self.workspace / "pyproject.toml").exists():
                command = "python -m pytest -v --tb=short"
            elif (self.workspace / "package.json").exists():
                command = "npm test"
            elif (self.workspace / "Cargo.toml").exists():
                command = "cargo test"
            else:
                command = "python -m pytest -v --tb=short"
        elif command.strip().lower().startswith("pytest"):
            command = f"python -m {command.strip()}"
        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, cwd=cwd)
            out = (r.stdout + r.stderr).strip()
            # Parse pytest summary line
            summary_match = re.search(r"((\d+ passed).*?((\d+ failed).*?)?(\d+ error)?.*?in [\d.]+s)", out)
            header = f"[{'PASS' if r.returncode == 0 else 'FAIL'}] {summary_match.group(1) if summary_match else ''}"
            return ToolResult(ok=(r.returncode == 0), output=f"{header}\n\n{out[:4000]}")
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="Tests timed out after 120s.")
        except Exception as e:
            return ToolResult(ok=False, output=f"Test runner error: {e}")

    def find_symbol(self, symbol: str, path: str = "."):
        """Find function/class definitions across the codebase."""
        import re, subprocess
        patterns = [
            rf"^\s*(def|class|function|const|let|var|async def)\s+{re.escape(symbol)}\b",
            rf"(export\s+)?(default\s+)?(function|class)\s+{re.escape(symbol)}\b",
        ]
        combined = "|".join(patterns)
        hits = []
        for root, _, files in os.walk(self._safe_path(path)):
            for fname in files:
                if not fname.endswith(('.py', '.js', '.ts', '.tsx', '.jsx')):
                    continue
                fpath = Path(root) / fname
                try:
                    for i, line in enumerate(fpath.read_text(errors='ignore').splitlines(), 1):
                        if re.search(combined, line):
                            rel = fpath.relative_to(self.workspace) if self.workspace in fpath.parents else fpath
                            hits.append(f"{rel}:{i}: {line.strip()}")
                except Exception:
                    pass
        return ToolResult(ok=True, output="\n".join(hits) if hits else f"Symbol '{symbol}' not found.")

    def computer_action(self, action: str, **kwargs):
        action = action.lower().strip()
        if action == "screenshot":
            return self.screenshot()
        if action == "mouse_move":
            return self.mouse_move(kwargs["x"], kwargs["y"], kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "left_click":
            return self.mouse_click(kwargs["x"], kwargs["y"], "left", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "double_click":
            return self.mouse_click(kwargs["x"], kwargs["y"], "left", 2, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "right_click":
            return self.mouse_click(kwargs["x"], kwargs["y"], "right", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "middle_click":
            return self.mouse_click(kwargs["x"], kwargs["y"], "middle", 1, kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "left_click_drag":
            return self.left_click_drag(kwargs["x"], kwargs["y"], kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "key":
            return self.key(kwargs["keys"])
        if action == "type":
            return self.keyboard_type(kwargs["text"])
        if action == "scroll":
            return self.scroll(kwargs.get("amount", 0), kwargs.get("x"), kwargs.get("y"), kwargs.get("sw", 1280), kwargs.get("sh", 800))
        if action == "cursor_position":
            return self.cursor_position()
        if action == "wait":
            return self.wait_action(kwargs.get("seconds", 1.0))
        if action == "focus_window":
            return self.focus_window(kwargs.get("title", ""))
        raise ToolError(f"Unsupported computer action: {action}")

    def list_processes(self):
        try:
            import psutil
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info']):
                try:
                    pinfo = proc.info
                    mem_mb = pinfo['memory_info'].rss / (1024 * 1024) if pinfo.get('memory_info') else 0
                    processes.append(f"PID: {pinfo['pid']} | Name: {pinfo['name']} | User: {pinfo['username']} | CPU: {pinfo['cpu_percent']}% | Mem: {mem_mb:.1f} MB")
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            return ToolResult(ok=True, output="\n".join(processes))
        except ImportError:
            return ToolResult(ok=False, output="psutil not installed. Use run_command('tasklist' or 'ps').")

    def kill_process(self, pid: int, force: bool = False):
        try:
            import psutil
            proc = psutil.Process(pid)
            if force:
                proc.kill()
            else:
                proc.terminate()
            self._started_pids.discard(int(pid))
            return ToolResult(ok=True, output=f"Terminated process {pid} ({proc.name()})")
        except psutil.NoSuchProcess:
            return ToolResult(ok=False, output=f"No process with PID {pid}")
        except ImportError:
            return ToolResult(ok=False, output="psutil not installed.")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    def force_close_window(self, title: str = "", pid: Optional[int] = None, force: bool = True):
        try:
            import psutil
        except ImportError:
            return ToolResult(ok=False, output="psutil not installed.")

        resolved_pid = int(pid) if pid is not None else None
        resolved_title = (title or self._isolated_app or "").strip()
        hwnd = None
        if resolved_pid is None:
            hwnd = self._get_hwnd_for_title(resolved_title)
            if not hwnd:
                return ToolResult(ok=False, output=f"No window with title containing '{resolved_title}' found.")
            if win32process is not None:
                try:
                    _, resolved_pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    resolved_pid = None
            if resolved_pid is None:
                return ToolResult(ok=False, output=f"Found '{resolved_title}' but could not resolve its process id.")

        try:
            proc = psutil.Process(int(resolved_pid))
            proc_name = proc.name()
            if force:
                proc.kill()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            self._started_pids.discard(int(resolved_pid))
            if hwnd and self._isolated_hwnd == hwnd:
                self.set_isolated_hwnd(None, self._isolated_app)
            label = resolved_title or proc_name
            return ToolResult(
                ok=True,
                output=f"Closed '{label}' via process {resolved_pid} ({proc_name})",
                data={"pid": int(resolved_pid), "title": resolved_title or proc_name, "force": force},
            )
        except psutil.NoSuchProcess:
            return ToolResult(ok=False, output=f"No process with PID {resolved_pid}")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    # ── UIA (UI Automation) tools — drive native/Electron apps by control
    #    name/AutomationId, no screenshots, no pixel guessing. ──────────────
    @staticmethod
    def _uia_rect_token(rect: dict) -> str:
        """Encode a control's on-screen bounds so the capsule's overlay can
        position its label there. Parsed by the widget; harmless to the model."""
        try:
            l, t = int(rect["left"]), int(rect["top"])
            w, h = int(rect["width"]), int(rect["height"])
            if w > 0 and h > 0:
                return f" [uia:{l},{t},{w},{h}]"
        except Exception:
            pass
        return ""

    @staticmethod
    def _app_rect_token(app: str, payload: Optional[Dict[str, int]] = None) -> str:
        """Encode the target app WINDOW bounds so the overlay can draw a glowing
        edge around the whole app the agent is working in. Pass `payload` to reuse
        an already-fetched rect and skip a second window-enumeration lookup."""
        r = payload if payload is not None else ToolExecutor._app_rect_payload(app)
        if r:
            return f" [app:{r['left']},{r['top']},{r['width']},{r['height']}]"
        return ""

    @staticmethod
    def _app_rect_payload(app: str) -> Optional[Dict[str, int]]:
        """Structured top-level app bounds for the capsule overlay."""
        try:
            from .widget.desktop_features import app_window_rect
            r = (
                app_window_rect(app)
                if app
                else app_window_rect("", fallback_foreground=True)
            )
            return _rect_payload(r)
        except Exception:
            pass
        return None

    def uia_find(self, query: str, app: str = "", limit: int = 5):
        from .widget.desktop_features import find_ui_elements
        res = find_ui_elements(query, app, limit)
        if not res.get("ok"):
            # OCR fallback: the control isn't in the accessibility tree, but is
            # its TEXT visible on screen? Report its pixel location + layer.
            ocr = self._ocr_find_fallback(query, app)
            if ocr is not None:
                return ocr
            app_rect = self._app_rect_payload(app)
            data = dict(res)
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status",
                "uia_find",
                "find",
                f"No accessible control named {query}" if query else "No accessible control found",
                target=query,
                app_rect=app_rect,
                phase="error",
                fallback_reason="uia_no_match",
                control_layer="UIA miss",
                control_reason="no accessible control and OCR found no match",
            )
            suffix = self._electron_unlock_hint(app, data)
            return ToolResult(ok=False, output=res.get("error", "no match") + suffix, data=data)
        lines = [f"{i+1}. {c.get('name') or c.get('automation_id') or '(unnamed)'} "
                 f"[{c.get('control_type')}] @ ({c['x']},{c['y']}) score={c.get('score')}"
                 for i, c in enumerate(res.get("items", []))]
        # focus-ring token for the top match (center x,y + w,h -> left,top,w,h)
        tok = ""
        items = res.get("items", [])
        top_item = items[0] if items else {}
        rect = _rect_from_match(top_item) if top_item else None
        target = str(top_item.get("name") or top_item.get("automation_id") or query or "").strip()
        if items:
            c0 = items[0]
            ww, hh = int(c0.get("width", 0)), int(c0.get("height", 0))
            if ww > 0 and hh > 0:
                tok = self._uia_rect_token({
                    "left": int(c0.get("left", int(c0["x"]) - ww // 2)),
                    "top": int(c0.get("top", int(c0["y"]) - hh // 2)),
                    "width": ww, "height": hh})
        app_rect = self._app_rect_payload(app)
        data = _uia_result_data(
            res,
            tool="uia_find",
            kind="find",
            label=f"Found {target}" if target else "Found control",
            target=target,
            rect=rect,
            app_rect=app_rect,
        )
        return ToolResult(ok=True, output="UIA matches:\n" + "\n".join(lines) + tok + self._app_rect_token(app, app_rect), data=data)

    # ── Hybrid resolver fallbacks: UIA -> OCR pixel (local, no model) -> the
    #    agent escalates to the vision model only if both miss. ──────────────
    def _ocr_click_fallback(self, query: str, app: str):
        """On a UIA miss, locate the target by on-screen TEXT (Windows OCR) and
        pixel-click it. Returns a ToolResult tagged 'OCR fallback', or None if
        OCR can't find it (then the caller reports the UIA miss)."""
        try:
            from .widget.desktop_features import ocr_find_in_app
            import pyautogui
            hit = ocr_find_in_app(query, app)
            if not hit.get("ok"):
                return None
            x, y = int(hit["x"]), int(hit["y"])
            pyautogui.click(x, y)
            app_rect = self._app_rect_payload(app)
            matched = hit.get("matched") or query
            data = {"ok": True, "method": "ocr_pixel", "matched": matched,
                    "x": x, "y": y}
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status", "uia_click", "click",
                f"Clicking “{matched}” (OCR)", target=matched, app_rect=app_rect,
                rect={"left": x - 14, "top": y - 12, "width": 28, "height": 24},
                control_layer="OCR fallback",
                control_reason="no accessible control — matched on-screen text",
            )
            return ToolResult(ok=True, data=data, output=(
                f"Clicked '{matched}' via OCR fallback at ({x},{y}). "
                f"[uia:{x-14},{y-12},28,24]{self._app_rect_token(app, app_rect)}"))
        except Exception:
            return None

    def _electron_unlock_hint(self, app: str, data: dict) -> str:
        """When UIA *and* OCR both miss, check whether the target is an Electron
        app whose DOM is simply locked to UIA. If so, attach the relaunch hint to
        `data` and return a one-line suffix for the output so the agent can
        self-heal (unlock) instead of blindly escalating to vision."""
        try:
            from .widget.desktop_features import electron_hint_for_app
            hint = electron_hint_for_app(app)
            if hint:
                data["electron_hint"] = hint
                if isinstance(data.get("overlay"), dict):
                    data["overlay"]["control_reason"] = (
                        "Electron app — DOM locked to UIA; relaunch with "
                        "--force-renderer-accessibility to unlock")
                return f" {hint['tip']}"
        except Exception:
            pass
        return ""

    def _ocr_find_fallback(self, query: str, app: str):
        """On a UIA find miss, locate the target by on-screen TEXT (Windows OCR)
        and report its pixel position. Returns a ToolResult or None."""
        try:
            from .widget.desktop_features import ocr_find_in_app
            hit = ocr_find_in_app(query, app)
            if not hit.get("ok"):
                return None
            x, y = int(hit["x"]), int(hit["y"])
            matched = hit.get("matched") or query
            app_rect = self._app_rect_payload(app)
            rect = {"left": x - 14, "top": y - 12, "width": 28, "height": 24}
            data = {"ok": True, "items": [{"name": matched, "x": x, "y": y,
                    "control_type": "OcrText", "score": hit.get("score", 0)}],
                    "layer": "ocr"}
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status", "uia_find", "find",
                f"Found “{matched}” (OCR)", target=matched, app_rect=app_rect,
                rect=rect, control_layer="OCR fallback",
                control_reason="no accessible control — matched on-screen text")
            return ToolResult(ok=True, data=data, output=(
                f"OCR matches (no accessible control):\n1. {matched} [OcrText] "
                f"@ ({x},{y}) via screen text. [uia:{x-14},{y-12},28,24]"
                f"{self._app_rect_token(app, app_rect)}"))
        except Exception:
            return None

    def _ocr_type_fallback(self, query: str, text: str, app: str,
                           clear_first: bool, submit: bool):
        """On a UIA miss for a text field: OCR-find the field/label, click to
        focus it, then paste the text. Returns a ToolResult or None."""
        try:
            from .widget.desktop_features import ocr_find_in_app
            import pyautogui, uiautomation as uia
            hit = ocr_find_in_app(query, app)
            if not hit.get("ok"):
                return None
            x, y = int(hit["x"]), int(hit["y"])
            pyautogui.click(x, y)
            time.sleep(0.12)
            if clear_first:
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("delete")
            try:
                saved = uia.GetClipboardText()
            except Exception:
                saved = ""
            uia.SetClipboardText(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
            if submit:
                pyautogui.press("enter")
            try:
                if saved:
                    uia.SetClipboardText(saved)
            except Exception:
                pass
            app_rect = self._app_rect_payload(app)
            matched = hit.get("matched") or query
            data = {"ok": True, "method": "ocr_pixel_type", "matched": matched,
                    "x": x, "y": y}
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status", "uia_type", "type",
                f"Typing into “{matched}” (OCR)", target=matched, app_rect=app_rect,
                rect={"left": x - 14, "top": y - 12, "width": 28, "height": 24},
                control_layer="OCR fallback",
                control_reason="no accessible field — matched on-screen text",
            )
            return ToolResult(ok=True, data=data, output=(
                f"Typed into '{matched}' via OCR fallback at ({x},{y}). "
                f"[uia:{x-14},{y-12},28,24]{self._app_rect_token(app, app_rect)}"))
        except Exception:
            return None

    def _click_snapshot(self):
        """Cheap fingerprint of UI state so we can tell whether a click did
        something: the foreground window plus the set of visible top-level
        windows (a menu or dialog opening spawns a NEW window — class #32768 for
        menus — even when the foreground owner stays put). Pure win32gui, no slow
        UIA focus read. Best-effort; any failure -> empty snapshot."""
        snap = {"fg": None, "wins": None}
        try:
            import win32gui  # type: ignore
            h = win32gui.GetForegroundWindow()
            snap["fg"] = (h, win32gui.GetWindowText(h))
            hwnds = set()

            def _enum(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    hwnds.add(hwnd)

            win32gui.EnumWindows(_enum, None)
            snap["wins"] = hwnds
        except Exception:
            pass
        return snap

    def _verify_clicked(self, before: dict):
        """True if the click visibly changed UI state (a menu/dialog appeared or
        the foreground window changed), None if we can't tell. Never False: a
        click that produces no observable change is common and legitimate, so we
        annotate confidence rather than cry failure."""
        try:
            time.sleep(0.12)  # let a menu/dialog actually paint before we look
            after = self._click_snapshot()
            bw, aw = before.get("wins"), after.get("wins")
            if bw is not None and aw is not None and (aw - bw):
                return True  # a new top-level window (menu/dialog) appeared
            bf, af = before.get("fg"), after.get("fg")
            if bf and af and bf != af:
                return True
        except Exception:
            pass
        return None

    def uia_click_sequence(self, targets, app: str = "", stop_on_error: bool = True,
                           read_result: str = ""):
        """Click a whole ORDERED list of controls in ONE call (each resolved by
        UIA InvokePattern, with the same OCR pixel fallback as uia_click). This
        collapses an N-click task (e.g. entering digits + operators into the
        Calculator, or tabbing a form) into a single tool round-trip — which is
        the key reliability win: the model can't drift or lose track between
        clicks because there is no intermediate turn. Returns a per-step summary.
        `targets` may be a list or a comma-separated string.

        Pass `read_result` (a result control's NAME, e.g. "Display") to read that
        control's value back in THIS SAME call — so you can verify + finish in one
        fewer turn (no separate uia_find needed)."""
        from .widget.desktop_features import invoke_ui_element
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        targets = [str(t).strip() for t in (targets or []) if str(t).strip()]
        if not targets:
            return ToolResult(ok=False, output="uia_click_sequence: no targets given.")
        steps, clicked, failed = [], 0, None
        for tgt in targets:
            res = invoke_ui_element(tgt, app)
            if res.get("ok"):
                clicked += 1
                steps.append(f"{tgt}=ok")
            else:
                ocr = self._ocr_click_fallback(tgt, app)
                if ocr is not None and ocr.ok:
                    clicked += 1
                    steps.append(f"{tgt}=ocr")
                else:
                    failed = tgt
                    steps.append(f"{tgt}=MISS")
                    if stop_on_error:
                        break
            time.sleep(0.06)  # let each click register before the next
        ok = failed is None
        app_rect = self._app_rect_payload(app)
        head = (f"Clicked {clicked}/{len(targets)} in sequence"
                + ("" if ok else f"; STOPPED at '{failed}' (not found)"))
        data = {"ok": ok, "clicked": clicked, "total": len(targets),
                "steps": steps, "failed": failed}
        data["overlay"] = _overlay_payload(
            "app_focus" if app_rect else "status", "uia_click_sequence", "click",
            head, target=(failed or (targets[-1] if targets else "")),
            app_rect=app_rect, phase=("done" if ok else "error"),
            fallback_reason=("" if ok else "uia_no_match"),
            control_layer=("UIA exact" if ok else "UIA miss"),
            control_reason=(
                "all sequence targets resolved by UIA/OCR"
                if ok else "sequence target missing after UIA and OCR fallback"))
        out = head + "\n" + " → ".join(steps) + self._app_rect_token(app, app_rect)
        if not ok:
            suffix = self._electron_unlock_hint(app, data)
            out += ("\nThe rest were not attempted. Re-check the name of the "
                    "missing control with uia_find, then continue." + suffix)
        elif read_result:
            # Read the named result control back in THIS call so the agent can
            # verify + finish without spending a separate uia_find turn.
            try:
                from .widget.desktop_features import find_ui_elements
                rr = find_ui_elements(read_result, app, 1)
                items = rr.get("items") or []
                val = (items[0].get("name") or "").strip() if items else ""
                if val:
                    data["result"] = val
                    out += f"\nResult — {read_result}: {val}"
                else:
                    out += (f"\n(Could not read '{read_result}' — uia_find it to "
                            "confirm the outcome.)")
            except Exception:
                pass
        return ToolResult(ok=ok, output=out, data=data)

    def uia_click(self, query: str, app: str = ""):
        from .widget.desktop_features import invoke_ui_element
        before = self._click_snapshot()
        res = invoke_ui_element(query, app)
        if not res.get("ok"):
            # Auto-fallback: try OCR pixel-click before giving up to the model.
            ocr_result = self._ocr_click_fallback(query, app)
            if ocr_result is not None:
                return ocr_result
            app_rect = self._app_rect_payload(app)
            data = dict(res)
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status",
                "uia_click",
                "click",
                f"Could not click {query}" if query else "Could not click control",
                target=query,
                app_rect=app_rect,
                phase="error",
                fallback_reason="uia_no_match",
                control_layer="UIA miss",
                control_reason="no accessible control and OCR found no match",
            )
            suffix = self._electron_unlock_hint(app, data)
            return ToolResult(ok=False, output=res.get("error", "click failed") + suffix, data=data)
        app_rect = self._app_rect_payload(app)
        target = str(res.get("target") or query or "").strip()
        tok = self._uia_rect_token(res.get("rect", {})) + self._app_rect_token(app, app_rect)
        data = _uia_result_data(
            res,
            tool="uia_click",
            kind="click",
            label=f"Clicking {target}" if target else "Clicking",
            target=target,
            rect=res.get("rect", {}),
            app_rect=app_rect,
        )
        # Post-action verification: did the click visibly change UI state?
        verified = self._verify_clicked(before)
        data["verified"] = verified
        if isinstance(data.get("overlay"), dict):
            data["overlay"]["verified"] = verified
        verdict = " (verified)" if verified is True else ""
        return ToolResult(ok=True, output=f"Activated '{res.get('target')}' via {res.get('method')}{verdict}.{tok}", data=data)

    def uia_type(self, query: str, text: str, app: str = "", clear_first: bool = False, submit: bool = False):
        from .widget.desktop_features import type_into_ui_element
        res = type_into_ui_element(query, text, app, clear_first, submit)
        if not res.get("ok"):
            # Auto-fallback: OCR-find the field, click to focus, then paste.
            ocr_result = self._ocr_type_fallback(query, text, app, clear_first, submit)
            if ocr_result is not None:
                return ocr_result
            app_rect = self._app_rect_payload(app)
            data = dict(res)
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status",
                "uia_type",
                "type",
                f"Could not type into {query}" if query else "Could not type into control",
                target=query,
                app_rect=app_rect,
                phase="error",
                fallback_reason="uia_no_match",
                control_layer="UIA miss",
                control_reason="no accessible field and OCR found no match",
            )
            suffix = self._electron_unlock_hint(app, data)
            return ToolResult(ok=False, output=res.get("error", "type failed") + suffix, data=data)
        # Post-action verification: read the control back and confirm the text
        # actually landed (computer mastery, not just "fire and hope").
        verified = self._verify_typed(query, app, text)
        app_rect = self._app_rect_payload(app)
        target = str(res.get("target") or query or "").strip()
        tok = self._uia_rect_token(res.get("rect", {})) + self._app_rect_token(app, app_rect)
        data = _uia_result_data(
            res,
            tool="uia_type",
            kind="type",
            label=f"Typing into {target}" if target else "Typing",
            target=target,
            rect=res.get("rect", {}),
            app_rect=app_rect,
        )
        data["verified"] = verified
        if isinstance(data.get("overlay"), dict):
            data["overlay"]["verified"] = verified
        verdict = " (verified)" if verified is True else (
            " (could not verify)" if verified is False else "")
        return ToolResult(ok=True, output=f"Typed into '{res.get('target')}' via {res.get('method')}{verdict}.{tok}", data=data)

    def _verify_typed(self, query: str, app: str, text: str):
        """Read the control back after typing; True if the text is present,
        False if a value read-back contradicts it, None if not verifiable."""
        try:
            from .widget.desktop_features import _find_uia_control
            ctrl, _info = _find_uia_control(query, app)
            if ctrl is None:
                return None
            try:
                val = ctrl.GetValuePattern().Value
            except Exception:
                return None
            if val is None:
                return None
            return (text or "").strip() in str(val)
        except Exception:
            return None

    def uia_wait(self, query: str, app: str = "", timeout: float = 6.0):
        from .widget.desktop_features import wait_for_ui_element
        res = wait_for_ui_element(query, app, timeout)
        if not res.get("ok"):
            app_rect = self._app_rect_payload(app)
            data = dict(res)
            data["overlay"] = _overlay_payload(
                "app_focus" if app_rect else "status",
                "uia_wait",
                "wait",
                f"Still waiting for {query}" if query else "Still waiting for control",
                target=query,
                app_rect=app_rect,
                phase="error",
                fallback_reason="uia_wait_timeout",
                control_layer="UIA miss",
                control_reason="accessible control did not appear",
            )
            suffix = self._electron_unlock_hint(app, data)
            return ToolResult(ok=False, output=res.get("error", "wait timed out") + suffix, data=data)
        app_rect = self._app_rect_payload(app)
        target = str(res.get("name") or query or "").strip()
        tok = self._uia_rect_token({
            "left": int(res.get("left", 0)),
            "top": int(res.get("top", 0)),
            "width": int(res.get("width", 0)),
            "height": int(res.get("height", 0)),
        }) + self._app_rect_token(app, app_rect)
        data = _uia_result_data(
            res,
            tool="uia_wait",
            kind="wait",
            label=f"Ready: {target}" if target else "Ready",
            target=target,
            rect={
                "left": int(res.get("left", 0)),
                "top": int(res.get("top", 0)),
                "width": int(res.get("width", 0)),
                "height": int(res.get("height", 0)),
            },
            app_rect=app_rect,
        )
        return ToolResult(ok=True, output=f"'{res.get('name') or query}' ready after {res.get('waited_s','?')}s @ ({res.get('x')},{res.get('y')}).{tok}", data=data)

    def electron_check(self, exe: str):
        from .widget.desktop_features import is_electron_app, resolve_app_exe
        resolved = resolve_app_exe(exe)
        is_e = is_electron_app(resolved)
        data = {
            "exe": resolved,
            "is_electron": is_e,
            "overlay": _overlay_payload(
                "status",
                "electron_check",
                "inspect",
                "Checking Electron accessibility",
                target=resolved,
                control_layer="Electron probe",
                control_reason="detecting Chromium/Electron app shell",
            ),
        }
        return ToolResult(ok=True, output=f"is_electron={is_e} (exe={resolved})", data=data)

    def electron_unlock(self, exe: str, args: list = None):
        from .widget.desktop_features import (
            relaunch_with_accessibility, resolve_app_exe, count_app_controls)
        import os as _os
        resolved = resolve_app_exe(exe)
        # Skip the disruptive relaunch if the app already exposes a rich UIA
        # tree (already unlocked, or not an app that needs it). The earlier
        # Discord grind wasted ~15s relaunching an already-accessible app.
        app_name = _os.path.splitext(_os.path.basename(resolved))[0]
        if count_app_controls(app_name, cap=60) >= 40:
            data = {
                "exe": resolved,
                "already_accessible": True,
                "overlay": _overlay_payload(
                    "status",
                    "electron_unlock",
                    "unlock",
                    "Electron UIA already unlocked",
                    target=app_name,
                    control_layer="UIA exact",
                    control_reason="Electron app already exposes controls",
                ),
            }
            return ToolResult(ok=True, output=(
                f"{app_name} already exposes a rich UIA tree (no relaunch needed). "
                f"Use uia_find/uia_click/uia_type directly."),
                data=data)
        res = relaunch_with_accessibility(resolved, args or [], False)
        if not res.get("ok"):
            data = dict(res)
            data["overlay"] = _overlay_payload(
                "status",
                "electron_unlock",
                "unlock",
                "Electron accessibility unlock failed",
                target=app_name,
                phase="error",
                control_layer="Electron unlock failed",
                control_reason="relaunch with renderer accessibility failed",
            )
            return ToolResult(ok=False, output=res.get("error", "relaunch failed"), data=data)
        data = dict(res)
        data["overlay"] = _overlay_payload(
            "status",
            "electron_unlock",
            "unlock",
            "Unlocking Electron accessibility",
            target=app_name,
            control_layer="Electron unlock",
            control_reason="relaunching with --force-renderer-accessibility",
        )
        return ToolResult(ok=True, output=(
            f"Relaunched {exe} (pid {res.get('pid')}) with --force-renderer-accessibility. "
            f"Its DOM is now exposed to UIA — retry uia_find/uia_click/uia_type. "
            f"{res.get('note','')}"), data=data)

    def api_call(self, method: str, url: str, headers: dict = None, body: dict = None):
        try:
            safe_url = _validate_public_http_url(url)
        except ToolError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            import httpx
            resp = httpx.request(
                method,
                safe_url,
                headers=headers or {},
                json=body,
                timeout=15.0,
                follow_redirects=False,
            )
            return ToolResult(ok=resp.is_success, output=resp.text[:20000])
        except Exception as e:
            return ToolResult(ok=False, output=f"api_call failed: {e}")

    _REQUIRED_ARGS: dict = {
        "run_command":    ["command"],
        "bash":           ["command"],
        "wait_for_window": ["title"],
        "read_file":      ["path"],
        "write_file":     ["path", "content"],
        "move_file":      ["source", "destination"],
        "text_view":      ["path"],
        "text_create":    ["path", "file_text"],
        "text_str_replace": ["path", "old_str", "new_str"],
        "text_insert":    ["path", "insert_line", "new_str"],
        "text_undo_edit": ["path"],
        "text_editor":    ["command", "path"],
        "computer":       ["action"],
        "mouse_move":     ["x", "y"],
        "mouse_click":    ["x", "y"],
        "double_click":   ["x", "y"],
        "right_click":    ["x", "y"],
        "middle_click":   ["x", "y"],
        "left_click_drag":["x", "y"],
        "keyboard_type":  ["text"],
        "key_combo":      ["keys"],
        "hold_key":       ["key"],
        "type_with_delay":["text"],
        "find_on_screen": ["image_path"],
        "set_clipboard":  ["text"],
        "notify":         ["message"],
        "api_call":       ["method", "url"],
        "file_glob":      ["pattern"],
        "file_grep":      ["pattern"],
        "web_fetch":      ["url"],
        "web_search":     ["query"],
        "kill_process":   ["pid"],
        "force_close_window": [],
        "list_mcp_tools": ["server_name"],
        "mcp_tool":       ["server_name", "tool_name"],
        "git":            ["command"],
        "lint_code":      ["path"],
        "find_symbol":    ["symbol"],
        "uia_find":       ["query"],
        "uia_click":      ["query"],
        "uia_click_sequence": ["targets"],
        "uia_type":       ["query", "text"],
        "uia_wait":       ["query"],
        "electron_check": ["exe"],
        "electron_unlock":["exe"],
    }

    def _validate_action_args(self, action: "Action") -> "Optional[ToolResult]":
        required = self._REQUIRED_ARGS.get(action.type.value, [])
        missing = [k for k in required if k not in action.args]
        if missing:
            example = ", ".join(f'"{k}": ...' for k in required)
            return ToolResult(
                ok=False,
                output=(
                    f"Missing required argument(s) {missing} for '{action.type.value}'. "
                    f"Provide a JSON object like: {{{example}}}"
                ),
            )
        return None

    async def run_action(self, action: Action, sw=1280, sh=800, on_stream: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None) -> ToolResult:
        validation_error = self._validate_action_args(action)
        if validation_error:
            return validation_error

        use_bg = self._background_mode and self._bg_browser and self._bg_browser.is_running

        # Background browser handlers for GUI actions
        if use_bg:
            bg_handlers = {
                ActionType.mouse_move: lambda a: self._mouse_move_bg(a.args["x"], a.args["y"], sw, sh),
                ActionType.mouse_click: lambda a: self._mouse_click_bg(a.args["x"], a.args["y"], a.args.get("button", "left"), 1, sw, sh),
                ActionType.double_click: lambda a: self._mouse_click_bg(a.args["x"], a.args["y"], "left", 2, sw, sh),
                ActionType.right_click: lambda a: self._mouse_click_bg(a.args["x"], a.args["y"], "right", 1, sw, sh),
                ActionType.middle_click: lambda a: self._mouse_click_bg(a.args["x"], a.args["y"], "middle", 1, sw, sh),
                ActionType.left_click_drag: lambda a: self._left_click_drag_bg(a.args["x"], a.args["y"], sw, sh),
                ActionType.keyboard_type: lambda a: self._keyboard_type_bg(a.args["text"]),
                ActionType.key_combo: lambda a: self._key_bg(a.args["keys"]),
                ActionType.hold_key: lambda a: self._hold_key_bg(a.args["key"], a.args.get("duration", 0.5)),
                ActionType.scroll: lambda a: self._scroll_bg(a.args.get("amount", 0), a.args.get("x"), a.args.get("y"), sw, sh),
                ActionType.type_with_delay: lambda a: self._type_with_delay_bg(a.args["text"], a.args.get("delay", 0.05)),
                ActionType.screenshot: lambda a: self._screenshot_bg(),
                ActionType.cursor_position: lambda a: self._cursor_position_bg(),
                ActionType.computer: lambda a: self._computer_action_bg(
                    a.args["action"],
                    **{k: v for k, v in a.args.items() if k != "action"},
                ),
            }
            if action.type in bg_handlers:
                try:
                    return await bg_handlers[action.type](action)
                except Exception as e:
                    return ToolResult(ok=False, output=f"Background browser error ({action.type}): {str(e)}")

        # Standard (non-GUI) handlers — always available
        if action.type == ActionType.run_command and on_stream:
            try:
                return await self.run_command_streaming(action.args["command"], on_stream)
            except Exception as e:
                return ToolResult(ok=False, output=f"Error executing {action.type}: {str(e)}")

        if action.type == ActionType.bash and on_stream:
            try:
                return await self.bash_streaming(action.args["command"], action.args.get("restart", False), on_stream)
            except Exception as e:
                return ToolResult(ok=False, output=f"Error executing {action.type}: {str(e)}")

        if action.type == ActionType.mcp_tool:
            try:
                from .mcp_manager import mcp_manager
                await mcp_manager.initialize_default_servers(str(self.workspace))
                res = await mcp_manager.call_tool(action.args["server_name"], action.args["tool_name"], action.args.get("tool_args", {}))
                return ToolResult(ok=True, output=res)
            except Exception as e:
                return ToolResult(ok=False, output=f"Error executing MCP tool: {str(e)}")

        if action.type == ActionType.list_mcp_servers:
            try:
                return await self.list_mcp_servers()
            except Exception as e:
                return ToolResult(ok=False, output=f"Error listing MCP servers: {str(e)}")

        if action.type == ActionType.list_mcp_tools:
            try:
                return await self.list_mcp_tools(action.args["server_name"])
            except Exception as e:
                return ToolResult(ok=False, output=f"Error listing MCP tools: {str(e)}")

        handlers = {
            ActionType.mouse_move: lambda a: self.mouse_move(a.args["x"], a.args["y"], sw, sh),
            ActionType.mouse_click: lambda a: self.mouse_click(a.args["x"], a.args["y"], a.args.get("button", "left"), 1, sw, sh),
            ActionType.double_click: lambda a: self.mouse_click(a.args["x"], a.args["y"], "left", 2, sw, sh),
            ActionType.right_click: lambda a: self.mouse_click(a.args["x"], a.args["y"], "right", 1, sw, sh),
            ActionType.middle_click: lambda a: self.mouse_click(a.args["x"], a.args["y"], "middle", 1, sw, sh),
            ActionType.left_click_drag: lambda a: self.left_click_drag(a.args["x"], a.args["y"], sw, sh),
            ActionType.keyboard_type: lambda a: self.keyboard_type(a.args["text"]),
            ActionType.key_combo: lambda a: self.key(a.args["keys"]),
            ActionType.hold_key: lambda a: self.hold_key(a.args["key"], a.args.get("duration", 0.5)),
            ActionType.scroll: lambda a: self.scroll(a.args.get("amount", 0), a.args.get("x"), a.args.get("y"), sw, sh),
            ActionType.type_with_delay: lambda a: self.type_with_delay(a.args["text"], a.args.get("delay", 0.05)),
            ActionType.find_on_screen: lambda a: self.find_on_screen(a.args["image_path"]),
            ActionType.get_clipboard: lambda a: self.get_clipboard(),
            ActionType.set_clipboard: lambda a: self.set_clipboard(a.args["text"]),
            ActionType.notify: lambda a: self.notify(a.args["message"]),
            ActionType.screenshot: lambda a: self.screenshot(),
            ActionType.cursor_position: lambda a: self.cursor_position(),
            ActionType.focus_window: lambda a: self.focus_window(a.args.get("title", "")),
            ActionType.wait_for_window: lambda a: self.wait_for_window(
                a.args.get("title", ""),
                a.args.get("timeout", 10.0),
                a.args.get("paint_seconds", 0.35),
            ),
            ActionType.wait_action: lambda a: self.wait_action(a.args.get("seconds", 1.0)),
            ActionType.ocr_image: lambda a: self.ocr_image(),
            ActionType.run_command: lambda a: self.run_command(a.args["command"]),
            ActionType.bash: lambda a: self.bash(a.args["command"], a.args.get("restart", False)),
            ActionType.read_file: lambda a: self.read_file(a.args["path"]),
            ActionType.write_file: lambda a: self.write_file(a.args["path"], a.args["content"]),
            ActionType.move_file: lambda a: self.move_file(a.args["source"], a.args["destination"]),
            ActionType.api_call: lambda a: self.api_call(
                a.args["method"], a.args["url"], a.args.get("headers"), a.args.get("body")
            ),
            ActionType.text_view: lambda a: self.text_editor.view(a.args["path"], a.args.get("view_range")),
            ActionType.text_create: lambda a: self.text_editor.create(a.args["path"], a.args["file_text"]),
            ActionType.text_str_replace: lambda a: self.text_editor.str_replace(
                a.args["path"], a.args["old_str"], a.args["new_str"]
            ),
            ActionType.text_insert: lambda a: self.text_editor.insert(
                a.args["path"], a.args["insert_line"], a.args["new_str"]
            ),
            ActionType.text_undo_edit: lambda a: self.text_editor.undo_edit(a.args["path"]),
            ActionType.text_editor: lambda a: self.text_editor_action(
                a.args["command"],
                a.args["path"],
                **{k: v for k, v in a.args.items() if k not in {"command", "path"}},
            ),
            ActionType.computer: lambda a: self.computer_action(
                a.args["action"],
                **{k: v for k, v in a.args.items() if k != "action"},
            ),
            ActionType.finish: lambda a: ToolResult(ok=True, output=_clean_finish_reason(a.args)),
            ActionType.system_info: lambda a: self.system_info(),
            ActionType.list_directory: lambda a: self.list_directory(a.args.get("path", "."), a.args.get("max_depth", 2)),
            ActionType.file_glob: lambda a: self.file_glob(a.args["pattern"]),
            ActionType.file_grep: lambda a: self.file_grep(a.args["pattern"], a.args.get("directory", ".")),
            ActionType.web_fetch: lambda a: self.web_fetch(a.args["url"]),
            ActionType.web_search: lambda a: self.web_search(a.args["query"], a.args.get("max_results", 5)),
            ActionType.list_processes: lambda a: self.list_processes(),
            ActionType.kill_process: lambda a: self.kill_process(a.args["pid"], a.args.get("force", False)),
            ActionType.force_close_window: lambda a: self.force_close_window(
                a.args.get("title", ""),
                a.args.get("pid"),
                a.args.get("force", True),
            ),
            ActionType.virtual_input: lambda a: self.computer_action(
                a.args.get("action", "type"),
                **{k: v for k, v in a.args.items() if k != "action"},
            ),
            # request_permission is normally intercepted by the agent, but stub
            # it here so ActionType enum coverage is complete.
            ActionType.request_permission: lambda a: ToolResult(ok=True, output=f"Permission request for '{a.args.get('scope','')}' noted."),
            # Coding power tools
            ActionType.git: lambda a: self.git(a.args["command"], a.args.get("args", "")),
            ActionType.run_tests: lambda a: self.run_tests(a.args.get("command", ""), a.args.get("path", ".")),
            ActionType.lint_code: lambda a: self.lint_code(a.args["path"]),
            ActionType.find_symbol: lambda a: self.find_symbol(a.args["symbol"], a.args.get("path", ".")),
            ActionType.delegate_coding: lambda a: self.delegate_coding(
                a.args.get("task", ""),
                a.args.get("repo_path", ""),
                a.args.get("files", []),
                a.args.get("constraints", ""),
                a.args.get("backend", ""),
            ),
            # New built-ins
            ActionType.pixel_color_at: lambda a: self.pixel_color_at(a.args["x"], a.args["y"]),
            ActionType.diff_files: lambda a: self.diff_files(a.args["path_a"], a.args["path_b"]),
            ActionType.extract_links: lambda a: self.extract_links(a.args["url"]),
            ActionType.todo_write: lambda a: self.todo_write(a.args.get("items", [])),
            ActionType.memory_recall: lambda a: self.memory_recall(a.args.get("query", "")),
            ActionType.run_and_watch: lambda a: self.run_and_watch(a.args["command"], a.args.get("watch_seconds", 10.0)),
            ActionType.ui_critique: lambda a: self.ui_critique(a.args.get("focus", "")),
            ActionType.analyze_folder: lambda a: self.analyze_folder(a.args.get("path", ""), a.args.get("action", "scan")),
            ActionType.show_widget: lambda a: self.show_widget(a.args),
            ActionType.screen_context: lambda a: self.screen_context(),
            ActionType.uia_find: lambda a: self.uia_find(a.args["query"], a.args.get("app", ""), a.args.get("limit", 5)),
            ActionType.uia_click: lambda a: self.uia_click(a.args["query"], a.args.get("app", "")),
            ActionType.uia_click_sequence: lambda a: self.uia_click_sequence(a.args.get("targets") or a.args.get("queries") or a.args.get("query"), a.args.get("app", ""), a.args.get("stop_on_error", True), a.args.get("read_result", "")),
            ActionType.uia_type: lambda a: self.uia_type(a.args["query"], a.args["text"], a.args.get("app", ""), a.args.get("clear_first", False), a.args.get("submit", False)),
            ActionType.uia_wait: lambda a: self.uia_wait(a.args["query"], a.args.get("app", ""), a.args.get("timeout", 6.0)),
            ActionType.electron_check: lambda a: self.electron_check(a.args["exe"]),
            ActionType.electron_unlock: lambda a: self.electron_unlock(a.args["exe"], a.args.get("args", [])),
        }
        if action.type in handlers:
            try:
                return await asyncio.to_thread(handlers[action.type], action)
            except Exception as e:
                return ToolResult(ok=False, output=f"Error executing {action.type}: {str(e)}")

        if self.plugin_registry:
            h = self.plugin_registry.handlers()
            if action.type.value in h:
                try:
                    handler = h[action.type.value]
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(**action.args)
                    else:
                        result = await asyncio.to_thread(handler, **action.args)
                    return ToolResult(ok=True, output=str(result))
                except Exception as e:
                    _log.exception("Plugin handler %s failed", action.type.value)
                    return ToolResult(ok=False, output=f"Plugin error: {str(e)}")

        return ToolResult(ok=False, output=f"Unknown action type: {action.type}")
