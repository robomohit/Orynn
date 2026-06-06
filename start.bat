@echo off
echo Starting Orynn (floating capsule)...
echo Press Ctrl+Shift+Space to show/hide it. Close this window to quit.
echo.
python run_desktop.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to launch. Did you run setup.bat first?
    pause
)
