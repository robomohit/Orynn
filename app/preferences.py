"""User preferences — chosen on first run and editable in Settings.

Persisted to workspace/preferences.json (per-user, gitignored). These are pure
UX choices; secrets/keys live in .env, not here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .state_store import read_json, workspace_state_path, write_json

# The schema: key -> (default, allowed values or None for free text/bool).
DEFAULTS: dict[str, Any] = {
    "theme": "auto",            # auto | dark | light
    "default_mode": "auto",     # auto | coding | computer_use | computer
    "speak_replies": False,     # read the agent's answer aloud (TTS)
    "voice_input": False,       # show/enable the mic for voice input
    "show_action_glow": True,   # aqua glow around the app being controlled
    "confirm_sensitive": True,  # always confirm before send/post/buy/delete
    "desktop_model": "",        # blank = free UIA tier; or a stronger model id
    "effort": "medium",         # low | medium | high | max — trades speed for a
                                # bigger model (free models have no reasoning knob)
    "onboarded": False,         # has the user finished first-run setup?
}

_ALLOWED = {
    "theme": {"auto", "dark", "light"},
    "default_mode": {"auto", "coding", "computer_use", "computer"},
    "effort": {"low", "medium", "high", "max"},
}


def store_path() -> Path:
    return workspace_state_path("preferences.json")


def get_all() -> dict[str, Any]:
    """Stored prefs merged over defaults (so new keys appear automatically)."""
    saved = read_json(store_path(), {})
    if not isinstance(saved, dict):
        saved = {}
    out = dict(DEFAULTS)
    for k, v in saved.items():
        if k in DEFAULTS:
            out[k] = v
    return out


def _coerce(key: str, value: Any) -> Any:
    """Validate/normalize a single preference against its default's type."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if key in _ALLOWED:
        v = str(value).strip()
        return v if v in _ALLOWED[key] else default
    return str(value).strip()


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update of known keys, ignoring anything unknown."""
    current = get_all()
    for k, v in (patch or {}).items():
        if k in DEFAULTS:
            current[k] = _coerce(k, v)
    write_json(store_path(), current)
    return current
