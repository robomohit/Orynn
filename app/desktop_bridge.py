from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    import webview  # type: ignore
except ImportError:
    webview = None  # type: ignore


OVERLAY_ACCENT = "#5BE0D0"  # brand Aqua Mint
OVERLAY_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {
      margin: 0;
      width: 100%;
      height: 100%;
      background: transparent;
      overflow: hidden;
    }
    body {
      position: relative;
      backdrop-filter: blur(0.5px);
    }
    .band {
      position: absolute;
      inset: 0;
      border-radius: 999px;
      animation: pulse 1.8s ease-in-out infinite;
      box-shadow:
        0 0 18px rgba(91, 224, 208, 0.95),
        0 0 38px rgba(91, 224, 208, 0.52),
        0 0 64px rgba(91, 224, 208, 0.28);
    }
    body.top .band {
      background: linear-gradient(180deg, rgba(91, 224, 208, 0.98), rgba(91, 224, 208, 0.58) 45%, rgba(91, 224, 208, 0.12) 85%, transparent);
    }
    body.bottom .band {
      background: linear-gradient(0deg, rgba(91, 224, 208, 0.98), rgba(91, 224, 208, 0.58) 45%, rgba(91, 224, 208, 0.12) 85%, transparent);
    }
    body.left .band {
      background: linear-gradient(90deg, rgba(91, 224, 208, 0.98), rgba(91, 224, 208, 0.58) 45%, rgba(91, 224, 208, 0.12) 85%, transparent);
    }
    body.right .band {
      background: linear-gradient(270deg, rgba(91, 224, 208, 0.98), rgba(91, 224, 208, 0.58) 45%, rgba(91, 224, 208, 0.12) 85%, transparent);
    }
    @keyframes pulse {
      0%, 100% { opacity: 0.82; }
      50% { opacity: 1; }
    }
  </style>
</head>
<body class="{edge}">
  <div class="band"></div>
</body>
</html>
"""


@dataclass
class ScreenBounds:
    x: int
    y: int
    width: int
    height: int


@dataclass
class WindowSnapshot:
    x: int
    y: int
    width: int
    height: int
    state: str
    on_top: bool


class DesktopBridge:
    """Native bridge for pywebview desktop niceties and desktop companion mode."""

    COMPANION_MARGIN = 24
    COMPANION_WIDTH = 430
    COMPANION_MAX_HEIGHT = 760
    EDGE_THICKNESS = 14

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._main_window: Optional[webview.Window] = None
        self._overlay_windows: list[webview.Window] = []
        self._snapshot: Optional[WindowSnapshot] = None
        self._companion_active = False

    def bind_window(self, window: webview.Window) -> None:
        self._main_window = window

    def is_desktop_shell(self) -> bool:
        return self._main_window is not None

    def minimize(self) -> dict[str, Any]:
        with self._lock:
            if not self._main_window:
                return {"ok": False, "error": "desktop window unavailable"}
            self._main_window.minimize()
            return {"ok": True}

    def toggle_maximize(self) -> dict[str, Any]:
        with self._lock:
            if not self._main_window:
                return {"ok": False, "error": "desktop window unavailable"}
            state = self._window_state()
            if "max" in state or "full" in state:
                self._main_window.restore()
            else:
                self._main_window.maximize()
            return {"ok": True, "state": self._window_state()}

    def close(self) -> dict[str, Any]:
        with self._lock:
            self._destroy_overlays()
            if not self._main_window:
                return {"ok": False, "error": "desktop window unavailable"}
            self._main_window.destroy()
            return {"ok": True}

    def pick_folder(self) -> dict[str, Any]:
        """Open the native OS folder picker and return the chosen path.

        Lets the dashboard offer a lightweight "Browse…" without a custom
        in-app file explorer. Returns {"path": None} when cancelled.
        """
        with self._lock:
            if not self._main_window:
                return {"ok": False, "path": None, "error": "desktop window unavailable"}
        try:
            result = self._main_window.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return {"ok": True, "path": None}
            path = result[0] if isinstance(result, (list, tuple)) else result
            return {"ok": True, "path": str(path) if path else None}
        except Exception as exc:  # pragma: no cover - native dialog edge cases
            return {"ok": False, "path": None, "error": str(exc)}

    def move_window(self, dx: float, dy: float) -> dict[str, Any]:
        """Move the (frameless) window by a delta — drives titlebar dragging
        from JS, since WebView2 doesn't reliably honour -webkit-app-region."""
        with self._lock:
            if not self._main_window:
                return {"ok": False}
            try:
                self._main_window.move(
                    int(self._main_window.x) + int(dx),
                    int(self._main_window.y) + int(dy))
                return {"ok": True}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "error": str(exc)}

    # Window width for the Sidekick capsule shell. The shell window hugs the
    # capsule, so JS calls set_capsule_height() to grow/shrink the window as
    # the capsule's reply region expands.
    CAPSULE_WIDTH = 600

    def set_capsule_height(self, height: int) -> dict[str, Any]:
        """Resize the (frameless) Sidekick window to hug the capsule content."""
        with self._lock:
            if not self._main_window:
                return {"ok": False, "error": "desktop window unavailable"}
            try:
                h = max(72, min(760, int(height)))
                self._main_window.resize(self.CAPSULE_WIDTH, h)
                return {"ok": True, "height": h}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "error": str(exc)}

    def set_desktop_companion(self, active: bool, mode: str = "", task_title: str = "") -> dict[str, Any]:
        """Enable companion mode only for full desktop control runs."""
        with self._lock:
            if not self._main_window:
                return {"ok": False, "supported": False}

            should_activate = bool(active and mode == "computer")
            if should_activate:
                if self._companion_active:
                    self._set_companion_title(task_title)
                self._enter_companion(task_title=task_title)
            else:
                self._exit_companion()

            return {
                "ok": True,
                "supported": True,
                "active": self._companion_active,
                "mode": mode,
            }

    @classmethod
    def compute_companion_geometry(cls, screen: ScreenBounds) -> dict[str, int]:
        width = min(cls.COMPANION_WIDTH, max(360, int(screen.width * 0.30)))
        height = min(cls.COMPANION_MAX_HEIGHT, max(560, screen.height - (cls.COMPANION_MARGIN * 2)))
        return {
            "x": screen.x + screen.width - width - cls.COMPANION_MARGIN,
            "y": screen.y + cls.COMPANION_MARGIN,
            "width": width,
            "height": height,
        }

    @classmethod
    def compute_overlay_segments(cls, screen: ScreenBounds) -> list[dict[str, Any]]:
        t = cls.EDGE_THICKNESS
        return [
            {"edge": "top", "x": screen.x, "y": screen.y, "width": screen.width, "height": t},
            {"edge": "left", "x": screen.x, "y": screen.y + t, "width": t, "height": max(1, screen.height - (t * 2))},
            {"edge": "right", "x": screen.x + screen.width - t, "y": screen.y + t, "width": t, "height": max(1, screen.height - (t * 2))},
            {"edge": "bottom", "x": screen.x, "y": screen.y + screen.height - t, "width": screen.width, "height": t},
        ]

    def _get_screen_bounds(self) -> ScreenBounds:
        screen = None
        try:
            if getattr(webview, "screens", None):
                screen = webview.screens[0]
        except Exception:
            screen = None

        if screen is not None:
            return ScreenBounds(
                x=int(getattr(screen, "physical_x", 0)),
                y=int(getattr(screen, "physical_y", 0)),
                width=int(getattr(screen, "physical_width", 1920)),
                height=int(getattr(screen, "physical_height", 1080)),
            )

        return ScreenBounds(x=0, y=0, width=1920, height=1080)

    def _window_state(self) -> str:
        if not self._main_window:
            return "unknown"
        state = getattr(self._main_window, "state", "normal")
        return str(state).lower()

    def _capture_snapshot(self) -> WindowSnapshot:
        if self._main_window is None:
            raise RuntimeError("Cannot capture snapshot: main window is not bound")
        return WindowSnapshot(
            x=int(self._main_window.x),
            y=int(self._main_window.y),
            width=int(self._main_window.width),
            height=int(self._main_window.height),
            state=self._window_state(),
            on_top=bool(getattr(self._main_window, "on_top", False)),
        )

    def _enter_companion(self, *, task_title: str = "") -> None:
        if self._companion_active or not self._main_window:
            return

        self._snapshot = self._capture_snapshot()
        if "max" in self._snapshot.state or "full" in self._snapshot.state:
            self._main_window.restore()
            time.sleep(0.08)

        bounds = self._get_screen_bounds()
        geometry = self.compute_companion_geometry(bounds)
        self._main_window.resize(geometry["width"], geometry["height"])
        time.sleep(0.05)
        self._main_window.move(geometry["x"], geometry["y"])
        self._main_window.on_top = True
        self._set_companion_title(task_title)

        self._create_overlays(bounds)
        self._companion_active = True

    def _set_companion_title(self, task_title: str = "") -> None:
        if not self._main_window:
            return
        if task_title:
            self._main_window.set_title(f"AI Computer - Desktop Control - {task_title[:48]}")
        else:
            self._main_window.set_title("AI Computer - Desktop Control")

    def _exit_companion(self) -> None:
        if not self._main_window:
            return

        self._destroy_overlays()
        snapshot = self._snapshot
        self._snapshot = None
        self._companion_active = False
        self._main_window.set_title("AI Computer - Codex Dashboard")

        if not snapshot:
            self._main_window.on_top = False
            return

        self._main_window.restore()
        time.sleep(0.05)
        self._main_window.resize(snapshot.width, snapshot.height)
        time.sleep(0.05)
        self._main_window.move(snapshot.x, snapshot.y)
        self._main_window.on_top = snapshot.on_top
        if "max" in snapshot.state:
            time.sleep(0.05)
            self._main_window.maximize()

    def _create_overlays(self, screen: ScreenBounds) -> None:
        self._destroy_overlays()
        for spec in self.compute_overlay_segments(screen):
            overlay = webview.create_window(
                "",
                html=OVERLAY_HTML.format(edge=spec["edge"]),
                width=spec["width"],
                height=spec["height"],
                x=spec["x"],
                y=spec["y"],
                frameless=True,
                resizable=False,
                focus=False,
                on_top=True,
                shadow=False,
                transparent=True,
                easy_drag=False,
                draggable=False,
                background_color="#000000",
            )
            self._overlay_windows.append(overlay)

    def _destroy_overlays(self) -> None:
        while self._overlay_windows:
            overlay = self._overlay_windows.pop()
            try:
                overlay.destroy()
            except Exception:
                pass
