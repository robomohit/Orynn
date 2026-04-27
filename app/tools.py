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
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
import mss
from PIL import Image
try:
    import pytesseract
except ImportError:
    pytesseract = None

from .models import Action, ActionType, ToolError, ToolResult
from .providers import get_scale_factor

try:
    import win32gui, win32api, win32con, win32process  # type: ignore
except ImportError:
    win32gui = win32api = win32con = win32process = None  # type: ignore
import ctypes
import time
import logging

_log = logging.getLogger(__name__)


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
    from urllib.parse import urlsplit

    if not isinstance(url, str) or not url.strip():
        raise ToolError("URL is required.")

    parts = urlsplit(url.strip())
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
    if ip is not None and (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        raise ToolError(f"Refusing to fetch private/internal IP: {host}")

    return url


class ToolExecutor:
    def __init__(self, workspace: Path, text_editor=None, plugin_registry=None, *, home_dir: Optional[Path] = None):
        self.workspace = workspace.resolve()
        self.home_dir = (home_dir or Path.home()).expanduser().resolve()
        self.text_editor = text_editor or TextEditorTool(self.workspace, home_dir=self.home_dir)
        self.plugin_registry = plugin_registry
        self._bash_cwd = self.workspace
        # Background browser for sandboxed GUI — set by AgentService
        self._bg_browser = None
        # Whether to run GUI actions in background (cowork) mode
        self._background_mode = True
        self._isolated_hwnd = None
        self._isolated_app = None

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
        return ToolResult(ok=True, output=f"Moved mouse to {x}, {y} (background)")

    def mouse_move(self, x: int, y: int, sw=1280, sh=800):
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Smooth, human-like movement
        pyautogui.moveTo(rx, ry, duration=0.6, tween=pyautogui.easeInOutQuad)
        return ToolResult(ok=True, output=f"Moved mouse to {rx}, {ry}")

    async def _mouse_click_bg(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):
        await self._bg_browser.mouse_click(x, y, button=button, click_count=clicks)
        return ToolResult(ok=True, output=f"Clicked {button} {clicks} times at {x}, {y} (background)")

    def mouse_click(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):
        if self.has_isolated_target():
            return self._mouse_click_isolated(x, y, button, clicks, sw, sh)
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Move smoothly first, then click
        pyautogui.moveTo(rx, ry, duration=0.4, tween=pyautogui.easeInOutQuad)
        time.sleep(0.1)
        pyautogui.click(button=button, clicks=clicks, interval=0.1)
        return ToolResult(ok=True, output=f"Clicked {button} {clicks} times at {rx}, {ry}")


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
            
            import pyautogui
            screen_w, screen_h = pyautogui.size()
            abs_x = int(x * screen_w / sw)
            abs_y = int(y * screen_h / sh)
            
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
            return ToolResult(ok=True, output=f'Sent {button} click to window (Isolated)')
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
        return ToolResult(ok=True, output=f"Dragged to {x}, {y} (background)")

    def left_click_drag(self, x: int, y: int, sw=1280, sh=800):
        import pyautogui
        rx, ry = self._scale(x, y, sw, sh)
        # Smooth drag
        pyautogui.dragTo(rx, ry, duration=0.8, tween=pyautogui.easeInOutQuad, button="left")
        return ToolResult(ok=True, output=f"Dragged to {rx}, {ry}")

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
            notification.notify(title="AI Computer", message=message, timeout=5)
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
            # Use shell.AppActivate which bypasses the foreground-lock restriction
            shell = win32com.client.Dispatch("WScript.Shell")
            shell.AppActivate(win32gui.GetWindowText(hwnd))
            import time; time.sleep(0.3)
            win32gui.SetForegroundWindow(hwnd)
            actual_title = win32gui.GetWindowText(hwnd)
            return ToolResult(ok=True, output=f"Focused window: '{actual_title}'")
        except Exception as e:
            return ToolResult(ok=False, output=f"focus_window failed: {e}")

    def run_command(self, command: str):
        try:
            import re
            mkdir_p = re.fullmatch(r'(?:mkdir|md)\s+-p\s+["\']?(.+?)["\']?', command.strip(), flags=re.IGNORECASE)
            if mkdir_p:
                target = self._safe_path(mkdir_p.group(1).strip())
                target.mkdir(parents=True, exist_ok=True)
                return ToolResult(ok=True, output=f"Created directory: {target}")
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
        import re as _re
        _stripped_lower = stripped.lower()
        _is_gui_launch = bool(_re.match(
            r'^(start\s+\S|explorer\s|cmd\s*/c\s+start|powershell\s+-command\s+"?start)',
            _stripped_lower
        ))

        if _is_gui_launch:
            try:
                subprocess.Popen(
                    command, shell=True, cwd=self._bash_cwd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                return ToolResult(ok=True, output=f"Launched (fire-and-forget): {command}\nCWD:\n{self._bash_cwd}")
            except Exception as e:
                return ToolResult(ok=False, output=f"Launch failed: {e}")

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

    def web_fetch(self, url: str):
        try:
            safe_url = _validate_public_http_url(url)
        except ToolError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            import urllib.request
            req = urllib.request.Request(safe_url, headers={'User-Agent': 'Mozilla/5.0 (AI Computer Agent)'})
            with urllib.request.urlopen(req, timeout=10) as response:
                # Cap body to ~1 MB to avoid blowing memory on adversarial servers
                raw = response.read(1_000_000)
            try:
                html = raw.decode('utf-8')
            except UnicodeDecodeError:
                html = raw.decode('utf-8', errors='replace')
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = ' '.join(text.split())
            return ToolResult(ok=True, output=text[:20000])
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
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AI Computer Agent)"})
            with urllib.request.urlopen(req, timeout=10) as response:
                page = response.read().decode("utf-8", errors="replace")

            results = []
            pattern = re.compile(
                r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            for match in pattern.finditer(page):
                href = html.unescape(re.sub(r"<.*?>", "", match.group("href"))).strip()
                title = html.unescape(re.sub(r"<.*?>", "", match.group("title"))).strip()
                snippet = html.unescape(re.sub(r"<.*?>", "", match.group("snippet"))).strip()
                if href and title:
                    results.append(f"{title}\n{href}\n{snippet}")
                if len(results) >= max_results:
                    break

            if not results:
                return ToolResult(ok=False, output=f"No search results found for: {query}")
            return ToolResult(ok=True, output="\n\n".join(results))
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
        import subprocess
        BLOCKED = {"push", "reset --hard", "clean -f", "rm -rf"}
        cmd_lower = (command + " " + args).strip().lower()
        for b in BLOCKED:
            if b in cmd_lower:
                return ToolResult(ok=False, output=f"Blocked: '{b}' requires explicit user approval.")
        full_cmd = f"git {command} {args}".strip()
        try:
            r = subprocess.run(
                full_cmd, shell=True, capture_output=True, text=True,
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
            return ToolResult(ok=True, output=f"Terminated process {pid} ({proc.name()})")
        except psutil.NoSuchProcess:
            return ToolResult(ok=False, output=f"No process with PID {pid}")
        except ImportError:
            return ToolResult(ok=False, output="psutil not installed.")
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    def api_call(self, method: str, url: str, headers: dict = None, body: dict = None):
        try:
            safe_url = _validate_public_http_url(url)
        except ToolError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            import httpx
            resp = httpx.request(method, safe_url, headers=headers or {}, json=body, timeout=15.0)
            return ToolResult(ok=resp.is_success, output=resp.text[:20000])
        except Exception as e:
            return ToolResult(ok=False, output=f"api_call failed: {e}")

    _REQUIRED_ARGS: dict = {
        "run_command":    ["command"],
        "bash":           ["command"],
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
        "list_mcp_tools": ["server_name"],
        "mcp_tool":       ["server_name", "tool_name"],
        "git":            ["command"],
        "lint_code":      ["path"],
        "find_symbol":    ["symbol"],
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
            ActionType.finish: lambda a: ToolResult(ok=True, output=a.args.get("reason", "Task marked complete by agent.")),
            ActionType.system_info: lambda a: self.system_info(),
            ActionType.list_directory: lambda a: self.list_directory(a.args.get("path", "."), a.args.get("max_depth", 2)),
            ActionType.file_glob: lambda a: self.file_glob(a.args["pattern"]),
            ActionType.file_grep: lambda a: self.file_grep(a.args["pattern"], a.args.get("directory", ".")),
            ActionType.web_fetch: lambda a: self.web_fetch(a.args["url"]),
            ActionType.web_search: lambda a: self.web_search(a.args["query"], a.args.get("max_results", 5)),
            ActionType.list_processes: lambda a: self.list_processes(),
            ActionType.kill_process: lambda a: self.kill_process(a.args["pid"], a.args.get("force", False)),
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
                    return ToolResult(ok=False, output=f"Plugin error: {str(e)}")

        return ToolResult(ok=False, output=f"Unknown action type: {action.type}")
