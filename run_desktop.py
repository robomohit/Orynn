import argparse
import webview
import threading
import uvicorn
import time
import os
import sys
from app.main import app
from app.desktop_bridge import DesktopBridge

PORT = int(os.getenv("AI_COMPUTER_PORT", "8000"))


def run_server():
    # Run FastAPI server on a background thread
    # Defaults to 8000; AI_COMPUTER_PORT can override it for local testing.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")


def parse_args():
    parser = argparse.ArgumentParser(description="Launch AI Computer desktop shell.")
    parser.add_argument(
        "--widget",
        action="store_true",
        help="Launch the compact always-on-top sidekick instead of the full dashboard.",
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    bridge = DesktopBridge()

    # 1. Start the backend server in a background thread
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 2. Wait a moment for the server to initialize
    time.sleep(2)

    # 3. Create the native window
    icon_path = os.path.join(os.path.dirname(__file__), "ai_computer_app_icon_1777005021291.png")

    if args.widget:
        window = webview.create_window(
            "AI Computer Sidekick",
            f"http://127.0.0.1:{PORT}/?widget=1",
            js_api=bridge,
            width=600,
            height=320,
            min_size=(420, 200),
            frameless=True,
            resizable=True,
            on_top=True,
            transparent=True,
            easy_drag=True,
            background_color="#000000",
        )
    else:
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

    # 4. Launch the application
    print("[Desktop] AI Computer is launching...")
    webview.start(bind_bridge, args=(window, bridge), icon=icon_path if os.path.exists(icon_path) else None)
