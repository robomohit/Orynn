#!/usr/bin/env bash
echo "Starting AI Computer..."
echo "Open http://localhost:8080 in your browser."
echo "Press Ctrl+C to stop."
echo ""
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8080
