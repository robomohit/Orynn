import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import tempfile
from pathlib import Path

# Isolate runtime state (tasks/, workspace/logs/) into a throwaway tmp dir for the
# whole test session. Must be set before app.main / app.log_emitter are imported,
# since they read ORYNN_WORKSPACE at module import. Without this, every test
# that creates a task would persist a record into the real ./tasks directory and
# pollute the dashboard's folder-grouped session history.
os.environ.setdefault("ORYNN_WORKSPACE", tempfile.mkdtemp(prefix="orynn-tests-"))

import pytest


@pytest.fixture
def workspace(tmp_path):
    w = tmp_path / "workspace"
    w.mkdir()
    return w


@pytest.fixture(autouse=True)
def mock_keys(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "testtoken")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-raw-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-raw-anthropic")
