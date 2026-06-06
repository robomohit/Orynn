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
    monkeypatch.setattr(_m, "detect_ollama", lambda: {"available": False, "models": []})
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server"] == "ok"
    non_local = {k: v for k, v in data["providers"].items() if k != "ollama"}
    assert all(v == "missing_key" for v in non_local.values())
    assert data["providers"]["ollama"] == "unavailable"


def test_healthz_with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    _m._healthz_cache["ts"] = 0.0
    _m._healthz_cache["result"] = None
    monkeypatch.setattr(_m, "detect_ollama", lambda: {"available": True, "models": ["llama3"]})
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"]["openrouter"] == "ok"
    assert data["providers"]["anthropic"] == "missing_key"
    assert data["providers"]["ollama"] == "ok"


def test_healthz_cache(monkeypatch):
    cached = {"server": "ok", "providers": {"openrouter": "ok"}}
    _m._healthz_cache["ts"] = time.time()
    _m._healthz_cache["result"] = cached
    client = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == cached


def test_readiness_endpoint_reports_local_capabilities(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    client = _client(monkeypatch)
    resp = client.get("/api/readiness", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] in {"ready", "warning", "blocked"}
    assert 0 <= data["score"] <= 100
    assert data["providers"]["openrouter"] is True
    checks = {item["key"]: item for item in data["checks"]}
    for key in ("provider_keys", "uia", "screenshot", "electron_unlock", "logs", "privacy"):
        assert key in checks
        assert checks[key]["label"]
        assert checks[key]["status"] in {"ready", "warning", "blocked", "unavailable"}
    assert data["summary"]["ready"] >= 1


def test_task_preflight_detects_auto_desktop_warnings(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    # Pin effort so the desktop model selection is deterministic regardless of
    # the developer's saved preference (medium -> the curated UIA tier).
    import app.preferences as _prefs
    monkeypatch.setattr(_prefs, "get_all", lambda: {"effort": "medium"})
    fake_readiness = {
        "overall": "warning",
        "score": 80,
        "summary": {"ready": 4, "warning": 2, "blocked": 1, "unavailable": 0},
        "checks": [
            {"key": "provider_keys", "label": "Model providers", "status": "ready", "detail": "Provider ready.", "category": "core"},
            {"key": "uia", "label": "UIA exact control", "status": "blocked", "detail": "UIA missing.", "category": "desktop"},
            {"key": "screenshot", "label": "Screenshot fallback", "status": "ready", "detail": "Capture ready.", "category": "desktop"},
            {"key": "input", "label": "Desktop input", "status": "ready", "detail": "Input ready.", "category": "desktop"},
            {"key": "electron_unlock", "label": "Electron unlock", "status": "blocked", "detail": "UIA needed.", "category": "desktop"},
            {"key": "logs", "label": "Flight recorder", "status": "ready", "detail": "Logs ready.", "category": "trust"},
        ],
    }
    monkeypatch.setattr(_m, "_build_readiness_payload", lambda: fake_readiness)
    client = _client(monkeypatch)

    resp = client.post(
        "/api/tasks/preflight",
        headers={"Authorization": "Bearer testtoken"},
        json={"goal": "Open Notepad and write hello", "mode": "auto"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["effective_mode"] == "computer_isolated"
    assert data["isolated_app"] == "Notepad"
    assert data["selected_model"] == "tier:uia"
    assert data["model_source"] == "auto:desktop:effort:medium"
    assert data["model_auto"] is True
    assert data["blocked"] is False
    assert data["can_override"] is True
    assert [issue["severity"] for issue in data["issues"]] == ["warning", "warning"]
    assert data["issues"][0]["key"] == "uia"


def test_create_task_blocks_when_preflight_has_blockers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "_MAX_ACTIVE_TASKS", 0)
    fake_preflight = {
        "ok": False,
        "blocked": True,
        "can_override": False,
        "issues": [{"key": "uia", "label": "UIA exact control", "severity": "blocked", "status": "blocked", "detail": "No control path."}],
        "effective_mode": "computer",
    }
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: fake_preflight)
    client = _client(monkeypatch)

    resp = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer testtoken"},
        json={"task_id": "blocked-preflight-task", "goal": "Open Settings", "mode": "computer"},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "readiness_preflight_blocked"
    assert detail["preflight"]["issues"][0]["key"] == "uia"


def test_create_task_requires_override_for_preflight_warnings(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "_MAX_ACTIVE_TASKS", 0)
    monkeypatch.setattr(_m, "_save_task_record", lambda record: None)
    warning_preflight = {
        "ok": False,
        "blocked": False,
        "can_override": True,
        "issues": [{"key": "uia", "label": "UIA exact control", "severity": "warning", "status": "blocked", "detail": "Using screenshot fallback."}],
        "effective_mode": "computer_isolated",
        "isolated_app": "Notepad",
    }
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: warning_preflight)
    client = _client(monkeypatch)

    blocked = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer testtoken"},
        json={"task_id": "warning-preflight-task", "goal": "Open Notepad", "mode": "auto"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "readiness_preflight_warning"
    assert "warning-preflight-task" not in _m._tasks

    allowed = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer testtoken"},
        json={
            "task_id": "warning-preflight-task",
            "goal": "Open Notepad",
            "mode": "auto",
            "readiness_override": True,
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["preflight"]["can_override"] is True
    assert _m._tasks["warning-preflight-task"].status == "queued"


def test_retry_task_requires_override_for_preflight_warnings(monkeypatch, tmp_path):
    from app.models import AgentContext, TaskRecord

    original = TaskRecord(
        id="retry-source",
        status="failed",
        context=AgentContext(goal="Open Notepad", isolated_app="Notepad"),
        goal="Open Notepad",
        model=None,
        mode="auto",
    )
    monkeypatch.setattr(_m, "_tasks", {"retry-source": original})
    monkeypatch.setattr(_m, "task_store_dir", tmp_path)
    monkeypatch.setattr(_m, "_save_task_record", lambda record: None)
    monkeypatch.setattr(_m.log_emitter, "emit", lambda *args, **kwargs: None)
    warning_preflight = {
        "ok": False,
        "blocked": False,
        "can_override": True,
        "issues": [{"key": "uia", "label": "UIA exact control", "severity": "warning", "status": "blocked", "detail": "Using screenshot fallback."}],
        "effective_mode": "computer_isolated",
        "isolated_app": "Notepad",
        "selected_model": "tier:uia",
    }
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: warning_preflight)
    init_calls = []

    def fake_init_task(**kwargs):
        init_calls.append(kwargs)
        return TaskRecord(
            id=kwargs["task_id"],
            status="running",
            context=AgentContext(
                goal=kwargs["goal"],
                screen_width=kwargs["screen_width"],
                screen_height=kwargs["screen_height"],
                isolated_app=kwargs.get("isolated_app"),
                active_skills=kwargs.get("active_skills") or [],
                project_folder=kwargs.get("project_folder"),
                environment=kwargs.get("environment") or {},
            ),
            goal=kwargs["goal"],
            model=kwargs["model"],
            mode=kwargs["mode"],
        )

    monkeypatch.setattr(_m.service, "init_task", fake_init_task)
    client = _client(monkeypatch)

    blocked = client.post(
        "/api/tasks/retry-source/retry",
        headers={"Authorization": "Bearer testtoken"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "readiness_preflight_warning"
    assert init_calls == []
    assert "retry-source-retry-1" not in _m._tasks

    allowed = client.post(
        "/api/tasks/retry-source/retry",
        headers={"Authorization": "Bearer testtoken"},
        json={"readiness_override": True},
    )

    assert allowed.status_code == 200
    body = allowed.json()
    assert body["task_id"] == "retry-source-retry-1"
    assert body["retried_from"] == "retry-source"
    assert body["preflight"]["can_override"] is True
    assert init_calls[0]["model"] == "tier:uia"
    assert _m._tasks["retry-source-retry-1"].status == "running"


def test_managed_external_submit_uses_preflight_queue_and_tracking(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "_MAX_ACTIVE_TASKS", 0)
    monkeypatch.setattr(_m, "task_store_dir", tmp_path)
    monkeypatch.setattr(_m, "_save_task_record", lambda record: None)
    events = []
    monkeypatch.setattr(_m.log_emitter, "emit", lambda task_id, event, payload: events.append((task_id, event, payload)))
    ok_preflight = {
        "ok": True,
        "blocked": False,
        "can_override": False,
        "issues": [],
        "effective_mode": "computer_isolated",
        "isolated_app": "Notepad",
        "selected_model": "tier:uia",
    }
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: ok_preflight)

    record = _m._submit_managed_task(
        goal="Open Notepad and write hello",
        task_id="telegram-managed",
        source="telegram",
    )

    assert record.status == "queued"
    assert _m._tasks["telegram-managed"].model == "tier:uia"
    assert _m._queued_task_specs[0]["model"] == "tier:uia"
    assert _m._queued_task_specs[0]["source"] == "telegram"
    created = [payload for _, event, payload in events if event == "task_created"][0]
    assert created["source"] == "telegram"
    assert created["preflight"]["effective_mode"] == "computer_isolated"
    queued = [payload for _, event, payload in events if event == "queued"][0]
    assert queued["source"] == "telegram"


def test_managed_external_submit_blocks_preflight_warnings(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "task_store_dir", tmp_path)
    monkeypatch.setattr(_m, "_save_task_record", lambda record: None)
    warning_preflight = {
        "ok": False,
        "blocked": False,
        "can_override": True,
        "issues": [{"key": "uia", "label": "UIA exact control", "severity": "warning", "status": "blocked", "detail": "Using screenshot fallback."}],
        "effective_mode": "computer_isolated",
        "isolated_app": "Notepad",
        "selected_model": "tier:uia",
    }
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: warning_preflight)

    with pytest.raises(RuntimeError, match="UIA exact control"):
        _m._submit_managed_task(
            goal="Open Notepad and write hello",
            task_id="telegram-warning",
            source="telegram",
        )

    assert "telegram-warning" not in _m._tasks
    assert _m._queued_task_specs == []


def test_get_mcp_not_ready_returns_initializing(monkeypatch):
    from app.mcp_manager import mcp_manager
    monkeypatch.setattr(mcp_manager, "_is_ready", False)
    client = _client(monkeypatch)
    resp = client.get("/api/mcp", headers={"Authorization": "Bearer testtoken"})
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
    resp = client.get("/api/mcp", headers={"Authorization": "Bearer testtoken"})
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
    key_dir = tmp_path / "orynn"
    key_dir.mkdir()
    (key_dir / ".api_key").write_text("filekey456")
    assert _m._load_or_create_api_key() == "filekey456"


def test_load_or_create_api_key_generates_and_saves(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    key = _m._load_or_create_api_key()
    assert len(key) == 64  # token_hex(32) produces 64 hex chars
    key_file = tmp_path / "orynn" / ".api_key"
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


def test_stream_invalid_keepalive_too_low(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/tasks/sometask/stream?keepalive_timeout_seconds=2", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 400


def test_stream_invalid_keepalive_too_high(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/tasks/sometask/stream?keepalive_timeout_seconds=400", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 400


def test_active_tasks_empty_when_no_tasks(monkeypatch):
    monkeypatch.setattr(_m, "_tasks", {})
    client = _client(monkeypatch)
    resp = client.get("/api/active-tasks", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    assert resp.json() == {"tasks": []}


@pytest.mark.asyncio
async def test_mcp_watchdog_marks_dead_when_pending_calls_get_no_response():
    """Watchdog transitions status to 'dead' within the timeout when calls are in-flight but silent."""
    from app.mcp_manager import MCPServer

    server = MCPServer("test", ["echo"])
    server.status = "running"
    server._last_response_at = 0.0  # epoch — always expired relative to _WATCHDOG_TIMEOUT

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    server._pending[1] = fut

    task = asyncio.create_task(server._watchdog(poll=0.01))
    await asyncio.sleep(0.05)  # let watchdog tick at least once

    assert server.status == "dead"
    assert fut.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_active_tasks_returns_non_terminal_only(monkeypatch):
    from app.models import AgentContext, TaskRecord
    running = TaskRecord(
        id="t1",
        status="running",
        context=AgentContext(goal="do stuff", isolated_app="Notepad"),
        goal="do stuff",
        mode="computer_isolated",
        model="gpt-4",
        paused=True,
    )
    done = TaskRecord(id="t2", status="done", context=AgentContext(goal="finished"), goal="finished", mode="coding", model="gpt-4")
    monkeypatch.setattr(_m, "_tasks", {"t1": running, "t2": done})
    client = _client(monkeypatch)
    resp = client.get("/api/active-tasks", headers={"Authorization": "Bearer testtoken"})
    assert resp.status_code == 200
    data = resp.json()
    ids = [t["task_id"] for t in data["tasks"]]
    assert "t1" in ids
    assert "t2" not in ids
    active = data["tasks"][0]
    assert active["status"] == "paused"
    assert active["paused"] is True
    assert active["isolated_app"] == "Notepad"
    assert active["context"]["isolated_app"] == "Notepad"


def test_create_task_queues_when_active_limit_reached(monkeypatch):
    from app.models import AgentContext, TaskRecord

    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "_MAX_ACTIVE_TASKS", 0)
    monkeypatch.setattr(_m, "detect_ollama", lambda: {"available": False, "models": []})
    client = _client(monkeypatch)

    resp = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer testtoken"},
        json={
            "task_id": "queued-task",
            "goal": "do later",
            "model": "claude-3-5-sonnet-20241022",
            "plan_first": True,
            "notify_on_completion": True,
            "auto_commit": True,
            "autonomy_level": "careful",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert _m._tasks["queued-task"].status == "queued"
    assert _m._tasks["queued-task"].plan_first is True
    assert _m._queued_task_specs[0]["autonomy_level"] == "careful"

    cancel = client.delete("/api/tasks/queued-task", headers={"Authorization": "Bearer testtoken"})
    assert cancel.status_code == 200
    assert _m._tasks["queued-task"].status == "cancelled"


def test_create_task_auto_desktop_uses_uia_tier(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(_m, "_tasks", {})
    monkeypatch.setattr(_m, "_queued_task_specs", [])
    monkeypatch.setattr(_m, "_MAX_ACTIVE_TASKS", 0)
    monkeypatch.setattr(_m, "_save_task_record", lambda record: None)
    monkeypatch.setattr(_m, "_build_task_preflight_payload", lambda **kwargs: {
        "ok": True,
        "blocked": False,
        "can_override": False,
        "issues": [],
        "effective_mode": "computer_isolated",
        "isolated_app": "Notepad",
    })
    client = _client(monkeypatch)

    resp = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer testtoken"},
        json={
            "task_id": "auto-desktop-task",
            "goal": "Open Notepad and write hello",
            "mode": "auto",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["preflight"]["effective_mode"] == "computer_isolated"
    assert body["preflight"]["isolated_app"] == "Notepad"
    assert _m._tasks["auto-desktop-task"].model == "tier:uia"
    assert _m._queued_task_specs[0]["model"] == "tier:uia"


def test_task_feedback_endpoint_persists_feedback(monkeypatch, tmp_path):
    from app.models import AgentContext, TaskRecord

    rec = TaskRecord(id="fb-task", status="done", context=AgentContext(goal="finished"), goal="finished")
    monkeypatch.setattr(_m, "_tasks", {"fb-task": rec})
    monkeypatch.setattr(_m, "workspace_dir", tmp_path)
    monkeypatch.setattr(_m, "task_store_dir", tmp_path / "tasks")
    (tmp_path / "tasks").mkdir()
    client = _client(monkeypatch)

    resp = client.post(
        "/api/tasks/fb-task/feedback",
        headers={"Authorization": "Bearer testtoken"},
        json={"rating": "up", "note": "useful"},
    )

    assert resp.status_code == 200
    assert rec.metadata["feedback"][0]["rating"] == "up"


def test_task_control_trace_report_summarizes_overlays(monkeypatch, tmp_path):
    from app.models import AgentContext, TaskRecord

    rec = TaskRecord(id="trace-task", status="done", context=AgentContext(goal="trace"), goal="trace")
    monkeypatch.setattr(_m, "_tasks", {"trace-task": rec})
    monkeypatch.setattr(_m.log_emitter, "log_dir", tmp_path)
    _m.log_emitter._seqs.pop("trace-task", None)
    _m.log_emitter._offsets.pop("trace-task", None)
    _m.log_emitter.emit("trace-task", "control_profile", {
        "target_app": "Notepad",
        "primary_route": "UIA exact",
        "uia_control_count": 42,
        "ocr_available": True,
        "model_vision": False,
        "window_found": True,
        "isolated": True,
        "electron_hint": None,
    })
    _m.log_emitter.emit("trace-task", "action_start", {
        "action_id": "a1",
        "action_type": "uia_find",
        "args_summary": "Text editor",
        "overlay": {
            "type": "status",
            "tool": "uia_find",
            "kind": "find",
            "phase": "start",
            "label": "Locating Text editor",
            "target": "Text editor",
            "control_layer": "UIA exact",
            "control_reason": "querying Windows accessibility tree",
        },
    })
    _m.log_emitter.emit("trace-task", "action_result", {
        "action_id": "a1",
        "action_type": "uia_find",
        "ok": True,
        "args_summary": "Text editor",
        "overlay": {
            "type": "uia_control",
            "tool": "uia_find",
            "kind": "find",
            "phase": "result",
            "label": "Found Text editor",
            "target": "Text editor",
            "rect": {"left": 10, "top": 20, "width": 300, "height": 40},
            "app_rect": {"left": 0, "top": 0, "width": 800, "height": 600},
            "control_layer": "UIA exact",
            "control_reason": "Windows accessibility tree",
        },
    })
    _m.log_emitter.flush()
    client = _client(monkeypatch)

    resp = client.get("/api/tasks/trace-task/control-trace", headers={"Authorization": "Bearer testtoken"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["primary_layer"] == "UIA exact"
    assert body["summary"]["profile_route"] == "UIA exact"
    assert body["summary"]["profile_target_app"] == "Notepad"
    assert body["summary"]["profile_uia_control_count"] == 42
    assert body["summary"]["profile_ocr_available"] is True
    assert body["summary"]["profile_window_found"] is True
    assert body["summary"]["profile_events"] == 1
    assert body["summary"]["used_profile_route"] is True
    assert body["summary"]["route_changed"] is False
    assert body["summary"]["used_uia"] is True
    assert body["summary"]["trace_events"] == 2
    assert body["profiles"][0]["primary_route"] == "UIA exact"
    assert body["entries"][1]["rect"] == {"left": 10, "top": 20, "width": 300, "height": 40}


def test_permission_endpoint_records_scope_grants(monkeypatch):
    _m.service.permissions.clear("perm-task")
    client = _client(monkeypatch)

    grant = client.post(
        "/api/permissions",
        headers={"Authorization": "Bearer testtoken"},
        json={"task_id": "perm-task", "action_id": "a1", "grant": True, "scope": "shell"},
    )
    assert grant.status_code == 200
    listed = client.get("/api/permissions/perm-task", headers={"Authorization": "Bearer testtoken"})
    assert listed.status_code == 200
    assert listed.json()["granted"] == ["shell"]
    assert listed.json()["denied"] == []

    deny = client.post(
        "/api/permissions",
        headers={"Authorization": "Bearer testtoken"},
        json={"task_id": "perm-task", "action_id": "a2", "grant": False, "scope": "screen"},
    )
    assert deny.status_code == 200
    listed = client.get("/api/permissions/perm-task", headers={"Authorization": "Bearer testtoken"})
    assert listed.json()["granted"] == ["shell"]
    assert listed.json()["denied"] == ["screen"]
    _m.service.permissions.clear("perm-task")


def test_trust_report_summarizes_runtime_trust_state(monkeypatch):
    from app.models import AgentContext, TaskRecord

    running = TaskRecord(
        id="trust-running",
        status="running",
        context=AgentContext(goal="use the desktop"),
        goal="use the desktop",
        mode="computer",
        model="tier:uia",
    )
    monkeypatch.setattr(_m, "_tasks", {"trust-running": running})
    class PendingWait:
        def done(self):
            return False

    _m.service._approvals["trust-running:approve-1"] = PendingWait()
    _m.service._permission_waits["trust-running:perm-1"] = PendingWait()
    _m.service.permissions.grant("trust-running", "filesystem")
    _m.service.permissions.deny("trust-running", "screen")
    client = _client(monkeypatch)

    unauth = client.get("/api/trust/report")
    resp = client.get("/api/trust/report", headers={"Authorization": "Bearer testtoken"})

    assert unauth.status_code == 401
    assert resp.status_code == 200
    body = resp.json()
    try:
        assert body["overall"] == "attention"
        assert body["pending_trust"]["count"] == 2
        assert body["pending_trust"]["approvals"] == [{"task_id": "trust-running", "action_id": "approve-1"}]
        assert body["pending_trust"]["permissions"] == [{"task_id": "trust-running", "action_id": "perm-1"}]
        assert body["active_tasks"][0]["id"] == "trust-running"
        assert body["consent_ledger"] == [{
            "task_id": "trust-running",
            "granted": ["filesystem"],
            "denied": ["screen"],
        }]
        assert body["kill_switch"]["available"] is True
        assert body["audit"]["permission_ledger"] is True
        assert any(item["key"] == "logs" for item in body["readiness"]["trust_checks"])
    finally:
        _m.service._approvals.pop("trust-running:approve-1", None)
        _m.service._permission_waits.pop("trust-running:perm-1", None)
        _m.service.permissions.clear("trust-running")


@pytest.mark.asyncio
async def test_session_prune_task_started_in_lifespan(monkeypatch):
    """_lifespan must start the background session-token pruning task (AI-27)."""
    from app.mcp_manager import mcp_manager

    async def noop(*a, **kw):
        pass

    monkeypatch.setattr(mcp_manager, "initialize_default_servers", noop)
    monkeypatch.setattr(_tg, "start_telegram", noop)
    monkeypatch.setattr(_dc, "start_discord", noop)

    async with _m._lifespan(_m.app):
        assert _m._session_prune_task is not None, "_session_prune_task must be created in lifespan"
        assert not _m._session_prune_task.done(), "_session_prune_task must be running"
