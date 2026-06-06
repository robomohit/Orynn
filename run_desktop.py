import argparse
import threading
import uvicorn
import time
import os
import sys
from app.main import app

PORT = int(os.getenv("ORYNN_PORT") or os.getenv("AI_COMPUTER_PORT", "8000"))


def run_server(port: int):
    # Run FastAPI server on a background thread
    # Defaults to 8000; ORYNN_PORT can override it for local testing.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def _server_healthy(port: int, timeout: float = 0.7) -> bool:
    """True only when Orynn is actually serving HTTP on this port."""
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/healthz",
            timeout=timeout,
        ) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_healthy(port, timeout=0.45):
            return True
        time.sleep(0.2)
    return False


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_backend(preferred_port: int) -> int:
    if _server_healthy(preferred_port):
        print(f"[Desktop] Reusing healthy backend on port {preferred_port}.")
        return preferred_port

    print(f"[Desktop] Starting backend on port {preferred_port}...")
    threading.Thread(
        target=run_server,
        args=(preferred_port,),
        daemon=True,
    ).start()
    if _wait_for_server(preferred_port):
        return preferred_port

    fallback_port = _free_port()
    print(
        f"[Desktop] Backend on port {preferred_port} did not become healthy; "
        f"trying port {fallback_port}.",
        file=sys.stderr,
    )
    threading.Thread(
        target=run_server,
        args=(fallback_port,),
        daemon=True,
    ).start()
    if _wait_for_server(fallback_port):
        return fallback_port

    print("[Desktop] Backend failed to start; dashboard not opened.", file=sys.stderr)
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description="Launch Orynn desktop shell.")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the full dashboard instead of the compact always-on-top sidekick.",
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    # 1. Start the backend server in a background thread, unless one is already
    #    running (e.g. the capsule launched us to open a second native window).
    port = _start_backend(PORT)

    if not args.dashboard:
        # Floating Sidekick capsule
        # Rendered by the Qt/QtWebEngine shell: a frameless, translucent,
        # always-on-top window with real per-pixel transparency + Windows
        # Acrylic, so the glass capsule genuinely blurs the desktop behind
        # it. (WebView2/pywebview cannot do reliable window transparency.)
        from app.widget.qt_shell import main as qt_widget_main
        print("[Desktop] Orynn Sidekick (Qt shell) is launching...")
        sys.exit(qt_widget_main(port))

    # Full dashboard (pywebview)
    try:
        import webview
    except ImportError:
        print(
            "[Desktop] pywebview is not installed. Run setup.bat or "
            "install requirements-desktop.txt to open the native dashboard.",
            file=sys.stderr,
        )
        sys.exit(1)
    from app.desktop_bridge import DesktopBridge
    bridge = DesktopBridge()
    root_dir = os.path.dirname(__file__)
    icon_path = next(
        (
            os.path.join(root_dir, name)
            for name in ("orynn_app_icon.png", "app_icon.ico")
            if os.path.exists(os.path.join(root_dir, name))
        ),
        None,
    )
    window = webview.create_window(
        "Orynn",
        f"http://127.0.0.1:{port}",
        js_api=bridge,
        width=1400,
        height=900,
        min_size=(1024, 768),
        background_color="#0a0a0a",
        # Frameless: the dashboard draws its own titlebar (drag region + custom
        # min/max/close wired to DesktopBridge), so we drop the OS frame to
        # avoid a double titlebar. easy_drag=False so only the titlebar moves
        # the window (its CSS -webkit-app-region: drag), not the whole canvas.
        frameless=True,
        easy_drag=False,
    )

    def bind_bridge(main_window, desktop_bridge):
        desktop_bridge.bind_window(main_window)

    print("[Desktop] Orynn is launching...")
    webview.start(bind_bridge, args=(window, bridge), icon=icon_path)
