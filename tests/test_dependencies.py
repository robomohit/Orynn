import os
import sys
import types
import importlib


def test_imports():
    modules = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "httpx",
        "jinja2",
        "mss",
        "PIL",
        "pytesseract",
        "pyautogui",
        "pytest",
        "pytest_asyncio",
    ]
    os.environ.setdefault("DISPLAY", ":99")
    sys.modules.setdefault("pyautogui", types.ModuleType("pyautogui"))
    for m in modules:
        importlib.import_module(m)
