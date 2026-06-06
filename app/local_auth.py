from __future__ import annotations

import os
from pathlib import Path


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def _key_file_candidates() -> list[Path]:
    base = _config_home()
    return [
        base / "orynn" / ".api_key",
        base / "ai_computer" / ".api_key",
    ]


def local_api_key() -> str:
    """Return the local API key used by the backend, without generating one."""
    env_key = (
        os.environ.get("AGENT_API_KEY")
        or os.environ.get("ORYNN_API_KEY")
        or os.environ.get("AI_COMPUTER_API_KEY")
    )
    if env_key:
        return env_key.strip()

    for key_file in _key_file_candidates():
        try:
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
                if key:
                    return key
        except OSError:
            continue
    return ""


def local_auth_headers() -> dict[str, str] | None:
    key = local_api_key()
    return {"Authorization": f"Bearer {key}"} if key else None
