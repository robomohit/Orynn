import argparse
import threading
import uvicorn
import time
import os
import sys
from app.main import app

PORT = int(os.getenv("AI_COMPUTER_PORT", "8000"))


def run_server():
    # Run FastAPI server on a background thread
    # Defaults to 8000; AI_COMPUTER_PORT can override it for local testing.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")


def parse_args():
    parser = argparse.ArgumentParser(description="Launch AI Computer desktop shell.")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the full dashboard instead of the compact always-on-top sidekick.",
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()

    # 1. Start the backend server in a background thread
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 2. Wait a moment for the server to initialize
    time.sleep(2)

    if not args.dashboard:
        # ── Floating Sidekick capsule ──
        # Rendered by the Qt/QtWebEngine shell: a frameless, translucent,
        # always-on-top window with real per-pixel transparency + Windows
        # Acrylic, so the glass capsule genuinely blurs the desktop behind
        # it. (WebView2/pywebview cannot do reliable window transparency.)
        from app.widget.qt_shell import main as qt_widget_main
        print("[Desktop] AI Computer Sidekick (Qt shell) is launching...")
        sys.exit(qt_widget_main(PORT))

    # ── Full dashboard (pywebview) ──
    import webview
    from app.desktop_bridge import DesktopBridge
    bridge = DesktopBridge()
    icon_path = os.path.join(os.path.dirname(__file__), "ai_computer_app_icon_1777005021291.png")
    window = webview.create_window(
        "AI Computer - Codex Dashboard",
        f"http://127.0.0.1:{PORT}",
        js_api=bridge,
        width=1400,
        height=900,
        min_size=(1024, 768),
        background_color="#0a0a0a",
    )

    def bind_bridge(main_window, desktop_bridge):
        desktop_bridge.bind_window(main_window)

    print("[Desktop] AI Computer is launching...")
    webview.start(bind_bridge, args=(window, bridge), icon=icon_path if os.path.exists(icon_path) else None)
