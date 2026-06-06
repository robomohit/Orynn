from __future__ import annotations

import os
from pathlib import Path


def local_api_key() -> str:
    """Return the local API key used by the backend, without generating one."""
    env_key = os.environ.get("AGENT_API_KEY") or os.environ.get("AI_COMPUTER_API_KEY")
    if env_key:
        return env_key.strip()

    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ai_computer"
    key_file = config_dir / ".api_key"
    try:
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def local_auth_headers() -> dict[str, str] | None:
    key = local_api_key()
    return {"Authorization": f"Bearer {key}"} if key else None
