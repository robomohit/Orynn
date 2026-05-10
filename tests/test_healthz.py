import asyncio
import time
import pytest
import app.main as _m  # force import (and load_dotenv) at collection time
import app.integrations.telegram as _tg
import app.integrations.discord as _dc
from fastapi.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setattr(_m, "API_KEY", "testtoken")
    return TestClient(_m.app)


def test_healthz_missing_keys(monkeypatch):
    for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    _m._healthz_cache["ts"] = 0.0
    _m._healthz_cache["result"] = None
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server"] == "ok"
    assert all(v == "missing_key" for v in data["providers"].values())


def test_healthz_with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    _m._healthz_cache["ts"] = 0.0
    _m._healthz_cache["result"] = None
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"]["openrouter"] == "ok"
    assert data["providers"]["anthropic"] == "missing_key"


def test_healthz_cache(monkeypatch):
    cached = {"server": "ok", "providers": {"openrouter": "ok"}}
    _m._healthz_cache["ts"] = time.time()
    _m._healthz_cache["result"] = cached
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == cached


def test_get_mcp_not_ready_returns_initializing(monkeypatch):
    from app.mcp_manager import mcp_manager
    monkeypatch.setattr(mcp_manager, "_is_ready", False)
    client = _client(monkeypatch)
    resp = client.get("/api/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["servers"] == []
    assert data["initializing"] is True


def test_get_mcp_ready_no_reinit(monkeypatch):
    from app.mcp_manager import mcp_manager
    monkeypatch.setattr(mcp_manager, "_is_ready", True)
    monkeypatch.setattr(mcp_manager, "servers", {})
    reinit_called = []
    monkeypatch.setattr(mcp_manager, "initialize_default_servers", lambda *a, **kw: reinit_called.append(1))
    client = _client(monkeypatch)
    resp = client.get("/api/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"servers": []}
    assert reinit_called == []


@pytest.mark.asyncio
async def test_mcp_init_awaited_before_lifespan_yields(monkeypatch):
    """_lifespan must await MCP init so _is_ready is True before the first request."""
    from app.mcp_manager import mcp_manager

    ready_on_entry = []

    async def mock_init(*a, **kw):
        mcp_manager._is_ready = True

    async def noop(*a, **kw):
        pass

    monkeypatch.setattr(mcp_manager, "_is_ready", False)
    monkeypatch.setattr(mcp_manager, "initialize_default_servers", mock_init)
    monkeypatch.setattr(_tg, "start_telegram", noop)
    monkeypatch.setattr(_dc, "start_discord", noop)

    async with _m._lifespan(_m.app):
        ready_on_entry.append(mcp_manager._is_ready)

    assert ready_on_entry[0] is True, "MCP init must complete before lifespan yields"


def test_load_or_create_api_key_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_API_KEY", "mykey123")
    assert _m._load_or_create_api_key() == "mykey123"


def test_load_or_create_api_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    key_dir = tmp_path / "ai_computer"
    key_dir.mkdir()
    (key_dir / ".api_key").write_text("filekey456")
    assert _m._load_or_create_api_key() == "filekey456"


def test_load_or_create_api_key_generates_and_saves(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    key = _m._load_or_create_api_key()
    assert len(key) == 64  # token_hex(32) produces 64 hex chars
    key_file = tmp_path / "ai_computer" / ".api_key"
    assert key_file.exists()
    assert key_file.read_text().strip() == key


@pytest.mark.asyncio
async def test_lifespan_stores_and_cancels_integration_tasks(monkeypatch):
    from app.mcp_manager import mcp_manager

    async def mock_init(*a, **kw):
        pass

    async def long_running(*a, **kw):
        await asyncio.sleep(9999)

    monkeypatch.setattr(mcp_manager, "initialize_default_servers", mock_init)
    monkeypatch.setattr(_tg, "start_telegram", long_running)
    monkeypatch.setattr(_dc, "start_discord", long_running)

    async with _m._lifespan(_m.app):
        assert _m._telegram_task is not None and not _m._telegram_task.done()
        assert _m._discord_task is not None and not _m._discord_task.done()

    assert _m._telegram_task.done()
    assert _m._discord_task.done()


def test_active_tasks_empty_when_no_tasks(monkeypatch):
    monkeypatch.setattr(_m, "_tasks", {})
    client = _client(monkeypatch)
    resp = client.get("/api/active-tasks", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    assert resp.json() == {"tasks": []}


def test_active_tasks_returns_non_terminal_only(monkeypatch):
    from app.models import AgentContext, TaskRecord
    running = TaskRecord(id="t1", status="running", context=AgentContext(goal="do stuff"), goal="do stuff", mode="coding", model="gpt-4")
    done = TaskRecord(id="t2", status="done", context=AgentContext(goal="finished"), goal="finished", mode="coding", model="gpt-4")
    monkeypatch.setattr(_m, "_tasks", {"t1": running, "t2": done})
    client = _client(monkeypatch)
    resp = client.get("/api/active-tasks", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    data = resp.json()
    ids = [t["task_id"] for t in data["tasks"]]
    assert "t1" in ids
    assert "t2" not in ids
