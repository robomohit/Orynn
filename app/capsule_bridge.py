"""Capsule Bridge — Pillar 1 (Universal LLM Bridge) + Pillar 3 (Screen Awareness).

This module provides:
1. Screen capture utilities that feed the LLM visual context
2. Widget payload construction helpers for the universal renderer
3. The show_widget tool implementation that pushes JSON to the capsule via SSE
"""
from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

from .local_auth import local_auth_headers

_log = logging.getLogger(__name__)


# ── Pillar 3: Screen Awareness ──────────────────────────────────────────────

def capture_screen_b64(quality: int = 55) -> str | None:
    """Take a silent screenshot of the primary monitor and return base64 JPEG.
    
    Used to give the LLM visual context of what the user is looking at,
    so 'summarize this' or 'what am I looking at?' queries work.
    """
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
            from PIL import Image
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            # Resize for faster LLM processing (max 1280px wide)
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        _log.warning("Screen capture failed: %s", e)
        return None


def capture_screen_text() -> str:
    """Attempt OCR on the current screen. Returns extracted text or empty string."""
    try:
        import mss
        import pytesseract
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            text = pytesseract.image_to_string(img)
            return text.strip()[:4000]  # cap length
    except Exception as e:
        _log.debug("OCR unavailable: %s", e)
        return ""


# ── Pillar 1: Universal Widget Payload Builder ──────────────────────────────

def build_list_widget(
    title: str,
    items: list[dict[str, str]],
    folder_path: str = "",
    icon: str = "folder",
) -> dict[str, Any]:
    """Build a standardized list widget payload from scan results.
    
    This constructs the JSON that DynamicWidget can render.
    """
    total_bytes = sum(item.get("size_bytes", item.get("bytes", 0)) for item in items)
    total_str = _format_bytes(total_bytes)
    
    formatted_items = [
        {
            "name": item.get("name", ""),
            "detail": item.get("size", _format_bytes(item.get("size_bytes", 0))),
        }
        for item in items
    ]
    
    buttons = []
    if folder_path:
        buttons.append({
            "label": "Organize All", "style": "primary",
            "icon": "folder-open",
            "action": "/api/capsule/organize",
            "payload": {"folder_path": folder_path},
        })
        buttons.append({
            "label": "Open Folder", "style": "secondary",
            "icon": "folder",
            "action": "open_folder",
            "payload": {"path": folder_path},
        })
        buttons.append({
            "label": "", "style": "danger", "icon": "trash",
            "action": "/api/capsule/delete",
            "payload": {
                "folder_path": folder_path,
                "file_paths": [item.get("path", "") for item in items if item.get("path")],
            },
        })
    
    return {
        "title": title,
        "subtitle": f"{len(formatted_items)} files  ·  {total_str} total",
        "icon": icon,
        "items": formatted_items,
        "buttons": buttons,
    }


def build_status_widget(
    title: str, text: str, icon: str = "info"
) -> dict[str, Any]:
    """Build a simple status/notification widget payload."""
    return {
        "title": title,
        "icon": icon,
        "text": text,
    }


def build_progress_widget(
    title: str, progress: float, text: str = "", icon: str = "download"
) -> dict[str, Any]:
    """Build a progress indicator widget payload."""
    return {
        "title": title,
        "icon": icon,
        "progress": max(0.0, min(1.0, progress)),
        "text": text,
    }


# ── Push widget to capsule via SSE ──────────────────────────────────────────

def push_widget(spec: dict, api_base: str = "http://127.0.0.1:8000") -> bool:
    """Push a widget JSON payload to the Qt capsule via the backend SSE bridge.
    
    Returns True on success, False on failure (capsule not connected, etc.)
    """
    try:
        import httpx
        r = httpx.post(
            f"{api_base}/api/capsule/widget",
            json=spec,
            headers=local_auth_headers(),
            timeout=5,
        )
        return r.status_code < 400
    except Exception as e:
        _log.debug("push_widget failed: %s", e)
        return False


# ── Utility ─────────────────────────────────────────────────────────────────

def _format_bytes(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
