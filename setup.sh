#!/usr/bin/env bash
set -e

echo ""
echo " ============================================"
echo "   AI Computer - Setup"
echo " ============================================"
echo ""

# Check Python exists
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found. Install Python 3.10+ from https://python.org"
    exit 1
fi

# Require 3.10+
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
    echo "[ERROR] Python $(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")') is too old — need 3.10+."
    exit 1
fi
echo "Using Python $(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

echo ""
echo "[1/3] Installing Python dependencies..."
echo "      (Note: native desktop control is Windows-only; on macOS/Linux you"
echo "       get coding + browser modes via the web dashboard.)"
python3 -m pip install --upgrade pip >/dev/null 2>&1 || true
python3 -m pip install -r requirements.txt

echo ""
echo "[2/3] Installing Playwright browser (Chromium)..."
python3 -m playwright install chromium || echo "[WARN] Playwright install failed — browser mode won't work. Retry: python3 -m playwright install chromium"

echo ""
echo "[3/3] Creating .env file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env from .env.example"
    echo "  >> Open .env and add at least one API key before launching."
else
    echo "  .env already exists, skipping."
fi

echo ""
echo " ============================================"
echo "   Setup complete!"
echo ""
echo "   Next steps:"
echo "     1. Edit .env and add your API key"
echo "        (free: get OPENROUTER_API_KEY at openrouter.ai)"
echo "     2. Run: ./start.sh"
echo " ============================================"
echo ""
