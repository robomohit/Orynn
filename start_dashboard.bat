@echo off
echo Starting Orynn - Dashboard (native desktop window)...
echo Close the window to quit.
echo.
python run_desktop.py --dashboard
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to launch. Did you run setup.bat first?
    pause
)
