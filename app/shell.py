"""Native always-on-top floating shell for the Orynn voice widget.

Runs the FastAPI server as a subprocess and opens a frameless, always-on-top
pywebview window pointing at /?widget=1 — so the liquid-glass widget hovers
over the user's actual desktop instead of living inside a browser tab.

Usage:
    python -m app.shell                       # default 127.0.0.1:8765
    python -m app.shell --port 9000           # custom port
    python -m app.shell --no-server           # window only; assume server
                                              # is already running

The shell deliberately does NOT try to do anything clever with transparency
or window resizing in v1 — it just gives a clean frameless always-on-top
window that the user can drag around. The orb-to-panel collapse and
transparency are future polish (IDEA-2026-05-19-02).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional


def _wait_for_server(url: str, timeout: float = 25.0) -> bool:
    """Poll the health endpoint until it answers, up to `timeout` seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.6) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _spawn_server(port: int) -> subprocess.Popen:
    """Start the FastAPI server as a subprocess we own and can clean up."""
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    # Inherit the user's API key + .env-loaded vars from the current process.
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning",
    ]
    # Hide the subprocess console window on Windows so we don't get a
    # second stray cmd.exe popping up next to the pywebview window.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cmd, cwd=str(repo_root), env=env, creationflags=creationflags)


class _ShellApi:
    """JS-callable bridge exposed via pywebview's `js_api`.

    The widget's close button calls `pywebview.api.close_window()` to dismiss
    the floating window — we can't rely on window.close() since there is no
    OS chrome to host a close affordance.
    """

    def __init__(self) -> None:
        self.window = None  # set after create_window

    def close_window(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass


def run(port: int, manage_server: bool, width: int, height: int) -> int:
    try:
        import webview  # pywebview
    except ImportError:
        print(
            "[Orynn Shell] pywebview is not installed.\n"
            "  Install with:  pip install pywebview\n"
            "  (Windows uses Edge WebView2 — already present on Win10/11.)",
            file=sys.stderr,
        )
        return 2

    server_proc: Optional[subprocess.Popen] = None
    if manage_server:
        # Reuse an already-running server on the same port if it answers.
        if not _wait_for_server(f"http://127.0.0.1:{port}/healthz", timeout=1.0):
            server_proc = _spawn_server(port)
            if not _wait_for_server(f"http://127.0.0.1:{port}/healthz", timeout=30.0):
                print(
                    f"[Orynn Shell] Server on 127.0.0.1:{port} did not "
                    f"become ready within 30s. Aborting.",
                    file=sys.stderr,
                )
                if server_proc is not None:
                    server_proc.terminate()
                return 3
    else:
        if not _wait_for_server(f"http://127.0.0.1:{port}/healthz", timeout=2.0):
            print(
                f"[Orynn Shell] No server answering on 127.0.0.1:{port}. "
                f"Either drop --no-server, or start uvicorn first.",
                file=sys.stderr,
            )
            return 4

    api = _ShellApi()
    api.window = webview.create_window(
        title="Orynn",
        url=f"http://127.0.0.1:{port}/?widget=1",
        width=width,
        height=height,
        frameless=True,        # no OS chrome — the widget IS the window
        on_top=True,           # hovers over user's desktop
        easy_drag=True,        # whole window is draggable (frameless)
        resizable=False,
        background_color="#1c1c20",  # matches the glass panel when not transparent
        js_api=api,
    )
    try:
        webview.start()        # blocks until the window is closed
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.shell",
        description="Launch the Orynn voice widget as an always-on-top floating window.",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORYNN_PORT") or os.environ.get("AI_COMPUTER_PORT", "8765")))
    parser.add_argument("--no-server", action="store_true",
                        help="Don't start uvicorn; assume the server is already running.")
    parser.add_argument("--width", type=int, default=520)
    parser.add_argument("--height", type=int, default=320)
    args = parser.parse_args(argv)
    return run(port=args.port, manage_server=not args.no_server,
               width=args.width, height=args.height)


if __name__ == "__main__":
    sys.exit(main())
