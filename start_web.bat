@echo off
echo Starting Orynn - web server (advanced / remote access)...
echo This serves the dashboard over HTTP for a browser or another device.
echo For the normal desktop experience use start.bat or start_dashboard.bat.
echo Open http://localhost:8080 in your browser. Press Ctrl+C to stop.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
pause
