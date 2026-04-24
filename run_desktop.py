import webview
import threading
import uvicorn
import time
import os
import sys
from app.main import app

def run_server():
    # Run FastAPI server on a background thread
    # We use a fixed port 8000 for the desktop app
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

if __name__ == "__main__":
    # 1. Start the backend server in a background thread
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 2. Wait a moment for the server to initialize
    time.sleep(2)

    # 3. Create the native window
    # We use the Windows 11-style title from our index_v2.html
    # Specify the new premium icon
    icon_path = os.path.join(os.path.dirname(__file__), "ai_computer_app_icon_1777005021291.png")
    
    window = webview.create_window(
        'AI Computer - Codex Dashboard', 
        'http://127.0.0.1:8000',
        width=1400,
        height=900,
        min_size=(1024, 768),
        background_color='#0a0a0a'
    )

    # 4. Launch the application
    print("[Desktop] AI Computer is launching...")
    webview.start(icon=icon_path if os.path.exists(icon_path) else None)
