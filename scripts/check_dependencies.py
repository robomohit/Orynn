from __future__ import annotations

import importlib
import importlib.util
import os
import sys

os.environ.setdefault("DISPLAY", ":99")

MODULES = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "multipart",
    "httpx",
    "jinja2",
    "mss",
    "PIL",
    "pytesseract",
    "pyautogui",
    "chromadb",
    "sentence_transformers",
    "playwright",
    "pytest",
    "pytest_asyncio",
]

missing = []
for name in MODULES:
    try:
        importlib.import_module(name)
        print(f"OK {name}")
    except Exception:
        if importlib.util.find_spec(name) is not None:
            print(f"OK {name} (installed, import constrained by environment)")
        else:
            missing.append(name)
            print(f"MISSING {name}")

sys.exit(1 if missing else 0)
