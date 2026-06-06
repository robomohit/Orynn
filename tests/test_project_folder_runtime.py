import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent import AgentService
from app.log_emitter import LogEmitter
from app.mcp_manager import MCPManager, mcp_manager
from app.models import AgentContext, TaskRecord
from app.providers import PlannerProvider
from app.tools import ToolExecutor


def _client(monkeypatch, tmp_path):
    home = tmp_path / "home"
    for folder in (home, home / "Desktop", home / "Downloads", home / "Documents"):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("AGENT_API_KEY", "token123")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setattr(Path, "home", lambda: home)

    import app.main as m

    importlib.reload(m)
    monkeypatch.setattr(m, "API_KEY", "token123")
    return TestClient(m.app), m, home


def test_browse_directory_endpoint_returns_shortcuts_and_breadcrumbs(monkeypatch, tmp_path):
    client, _, home = _client(monkeypatch, tmp_path)
    project = home / "Desktop" / "alpha"
    project.mkdir(parents=True)
    (project / "notes.txt").write_text("hello", encoding="utf-8")

    response = client.get(
        f"/api/browse-directory?path={project}",
        headers={"Authorization": "Bearer token123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(project)
    assert any(crumb["path"] == str(project) for crumb in payload["breadcrumbs"])
    assert {shortcut["id"] for shortcut in payload["shortcuts"]} >= {"home", "desktop", "downloads"}
    assert any(entry["name"] == "notes.txt" and entry["is_dir"] is False for entry in payload["entries"])


def test_create_task_passes_project_folder_and_environment(monkeypatch, tmp_path):
    client, m, home = _client(monkeypatch, tmp_path)
    project = home / "Desktop" / "dispatch"
    project.mkdir(parents=True)
    captured = {}
    task_id = f"project-folder-task-{uuid4().hex[:8]}"

    def fake_init_task(**kwargs):
            captured.update(kwargs)
            return TaskRecord(
                id=kwargs["task_id"],
            status="running",
            goal=kwargs["goal"],
            model=kwargs["model"],
            mode=kwargs["mode"],
            context=AgentContext(
                goal=kwargs["goal"],
                project_folder=kwargs["project_folder"],
                environment=kwargs["environment"],
            ),
        )

    monkeypatch.setattr(m.service, "init_task", fake_init_task)

    response = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer token123"},
        json={
            "task_id": task_id,
            "goal": "Inspect the repo",
            "project_folder": str(project),
        },
    )

    assert response.status_code == 200
    assert captured["project_folder"] == str(project)
    assert captured["environment"]["workspace"] == str(project)
    assert captured["environment"]["project_folder_selected"] is True


def test_log_emitter_seek_replay_uses_binary_offsets_for_utf8(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    emitter = LogEmitter()

    emitter.emit("utf8-task", "status", {"message": "warmup"})
    emitter.emit("utf8-task", "status", {"message": "emoji 😀"})
    emitter.emit("utf8-task", "status", {"message": "done"})
    emitter.flush()

    replay = emitter.read_log("utf8-task", since=1)

    assert [event["message"] for event in replay] == ["emoji 😀", "done"]
    assert replay[0]["seq"] == 1


def test_provider_adapters_do_not_double_prefix_data_urls(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    provider = PlannerProvider(model="gpt-4o")

    def fake_post(url, headers=None, json=None):
        seen["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(provider._http_client, "post", fake_post)

    result = provider._chat_openai("system", "prompt", "data:image/jpeg;base64,abc123")

    assert result == "ok"
    image_url = seen["payload"]["messages"][1]["content"][0]["image_url"]["url"]
    assert image_url == "data:image/jpeg;base64,abc123"


def test_anthropic_adapter_extracts_raw_base64_from_data_url(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"text": "ok"}], "usage": {}}

    provider = PlannerProvider(model="claude-3-5-sonnet-20241022")

    def fake_post(url, headers=None, json=None):
        seen["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(provider._http_client, "post", fake_post)

    result = provider._chat_anthropic("system", "prompt", "data:image/jpeg;base64,xyz987")

    assert result == "ok"
    source = seen["payload"]["messages"][0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"
    assert source["data"] == "xyz987"


@pytest.mark.asyncio
async def test_run_task_uses_injected_environment_not_system_info(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=SimpleNamespace(emit=lambda *a, **k: None))
    monkeypatch.setattr(service.tools, "system_info", lambda: (_ for _ in ()).throw(AssertionError("system_info should not be called")))
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])

    class FakeProvider:
        def __init__(self):
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        @property
        def total_tokens(self):
            return self._total_input_tokens + self._total_output_tokens

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            yield {"type": "tool_call", "id": "finish-1", "name": "finish", "args": {"reason": "done"}, "thought": "done"}

    events = []

    async def capture_emit(task_id, event, data):
        events.append((event, data))

    async def noop_reasoning(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr(service, "_emit", capture_emit)
    monkeypatch.setattr(service, "_emit_reasoning", noop_reasoning)

    await service.run_task(
        "env-task",
        "Summarize the folder",
        mode="coding",
        environment={"workspace": str(workspace), "home": str(workspace.parent), "project_folder_selected": True},
        project_folder=str(workspace),
    )

    assert any(event == "done" and data.get("complete") for event, data in events)


@pytest.mark.asyncio
async def test_mcp_manager_loads_dynamic_server_definitions(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = workspace / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "notes",
                        "cmd": ["uvx", "notes-mcp", "--root", "${workspace}"],
                        "env": {"HOME_HINT": "${home}"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    manager = MCPManager()
    started = {}

    async def fake_register(name, cmd, env=None):
        started[name] = {"cmd": cmd, "env": env or {}}
        manager.servers[name] = SimpleNamespace(cmd=cmd, env=env or {}, proc=SimpleNamespace(poll=lambda: None))
        return manager.servers[name]

    monkeypatch.setattr(manager, "register_server", fake_register)

    await manager.initialize_default_servers(str(workspace))

    assert "filesystem" in started
    assert started["notes"]["cmd"][-1] == str(workspace)
    assert started["notes"]["env"]["HOME_HINT"] == str(Path.home().resolve())


def test_text_editor_allows_home_directory_when_project_folder_is_selected(tmp_path):
    home = tmp_path / "home"
    project = home / "Desktop" / "dispatch"
    project.mkdir(parents=True)
    desktop_file = home / "Desktop" / "notes.txt"

    tools = ToolExecutor(project, home_dir=home)
    result = tools.text_editor.create(str(desktop_file), "hello")

    assert result.ok is True
    assert desktop_file.read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_tool_executor_lists_runtime_mcp_tools(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    tools = ToolExecutor(project, home_dir=tmp_path / "home")

    async def fake_sync(workspace_path):
        assert workspace_path == str(project)

    monkeypatch.setattr(mcp_manager, "initialize_default_servers", fake_sync)
    monkeypatch.setattr(
        mcp_manager,
        "servers",
        {
            "notes": SimpleNamespace(
                cmd=["uvx", "notes-mcp"],
                tools=[
                    {
                        "name": "search_notes",
                        "description": "Search the indexed note set.",
                        "inputSchema": {"properties": {"query": {"type": "string"}}},
                    }
                ],
            )
        },
    )

    result = await tools.list_mcp_tools("notes")

    assert result.ok is True
    assert "search_notes" in result.output
    assert "query" in result.output
