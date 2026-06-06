@echo off
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo    AI Computer - Setup
echo  ============================================
echo.

:: ---- Check Python is installed ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on your PATH.
    echo         Install Python 3.10 or newer from https://python.org/downloads
    echo         IMPORTANT: tick "Add python.exe to PATH" in the installer.
    echo.
    pause
    exit /b 1
)

:: ---- Check Python is 3.10+ ----
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)
if !PYMAJ! LSS 3 goto :badpy
if !PYMAJ! EQU 3 if !PYMIN! LSS 10 goto :badpy
echo  Using Python !PYVER!
echo.
goto :install
:badpy
echo [ERROR] Python !PYVER! is too old. This app needs Python 3.10 or newer.
echo         Get it from https://python.org/downloads
pause
exit /b 1

:install
echo [1/3] Installing dependencies (desktop app + UI + UIA control)...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements-desktop.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency install failed. Check your internet connection and
    echo         retry. If it persists, run:  python -m pip install -r requirements-desktop.txt
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browser (Chromium, for browser mode)...
python -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright install failed - browser mode won't work until you run:
    echo        python -m playwright install chromium
)

echo.
echo [3/3] Creating your .env file...
if not exist .env (
    copy .env.example .env >nul
    echo   Created .env from .env.example
) else (
    echo   .env already exists - leaving it untouched.
)

echo.
echo  ============================================
echo    Setup complete!  Next step:
echo.
echo      Double-click  start.bat  to launch the floating capsule.
echo      On first run it asks you to paste a FREE OpenRouter key
echo      (grab one in ~30s at openrouter.ai/keys) - no file editing needed.
echo.
echo      Prefer the full window? Run  start_dashboard.bat
echo  ============================================
echo.
pause
