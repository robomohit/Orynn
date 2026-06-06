"""Tests for AI-20: per-file git auto-commit in coding mode."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
import pytest

from app.agent import AgentService, _git_commit_file, _FILE_WRITE_TYPES


class DummyLogEmitter:
    async def emit(self, *args, **kwargs):
        return None


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


# ── Unit tests for _git_commit_file ─────────────────────────────────────────

def test_git_commit_file_non_git_workspace(workspace, monkeypatch):
    """Returns None without error when workspace is not a git repo."""
    def fake_run(args, **kwargs):
        m = MagicMock()
        m.returncode = 1  # git rev-parse fails → not a repo
        m.stdout = ""
        m.stderr = "not a git repository"
        return m
    monkeypatch.setattr("app.agent.subprocess.run", fake_run)
    result = _git_commit_file("hello.py", workspace, "write_file")
    assert result is None


def test_git_commit_file_success(workspace, monkeypatch):
    """Returns short hash when git add + commit succeed."""
    def fake_run(args, **kwargs):
        m = MagicMock()
        cmd = args  # args is the list passed as first positional argument
        if "--is-inside-work-tree" in cmd:
            m.returncode = 0
            m.stdout = "true"
        elif "add" in cmd:
            m.returncode = 0
            m.stdout = ""
        elif "commit" in cmd:
            m.returncode = 0
            m.stdout = "1 file changed"
        elif "--short" in cmd:
            m.returncode = 0
            m.stdout = "abc1234"
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    monkeypatch.setattr("app.agent.subprocess.run", fake_run)
    result = _git_commit_file("app/foo.py", workspace, "write_file")
    assert result == "abc1234"


def test_git_commit_file_nothing_to_commit(workspace, monkeypatch):
    """Returns None when commit exits non-zero (e.g. nothing staged)."""
    def fake_run(args, **kwargs):
        m = MagicMock()
        m.stdout = ""
        m.stderr = ""
        if "--is-inside-work-tree" in args:
            m.returncode = 0
        elif "add" in args:
            m.returncode = 0
        elif "commit" in args:
            m.returncode = 1  # nothing to commit or hook blocked
        else:
            m.returncode = 0
        return m

    monkeypatch.setattr("app.agent.subprocess.run", fake_run)
    result = _git_commit_file("new.py", workspace, "text_create")
    assert result is None


# ── Integration tests: file_commit event emitted in streaming loop ───────────

@pytest.mark.asyncio
async def test_write_file_emits_file_commit_in_coding_mode(workspace, monkeypatch):
    """write_file in coding mode emits file_change + file_commit events when in a git repo."""
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent._git_commit_file", lambda path, ws, action, task_id="": "deadbeef")

    class FakeProvider:
        total_tokens = 0
        _total_input_tokens = 0
        _total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            if len(messages) == 1:
                yield {"type": "tool_call", "id": "c1", "name": "write_file",
                       "args": {"path": "hello.py", "content": "print('hi')"}, "thought": "write"}
                return
            yield {"type": "tool_call", "id": "c2", "name": "finish",
                   "args": {"reason": "done"}, "thought": "done"}

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        from app.models import ToolResult
        return ToolResult(ok=True, output="written", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture(task_id, event_type, data):
        events.append((event_type, data))

    monkeypatch.setattr(service, "_emit", capture)
    monkeypatch.setattr(service, "_emit_reasoning", AsyncMock())
    monkeypatch.setattr(service, "_finalize", lambda *a, **kw: None)

    await service.run_task("task-git-1", "Write hello.py", mode="coding")

    assert any(t == "file_change" and d.get("path") == "hello.py" for t, d in events), \
        "file_change event not emitted"
    assert any(t == "file_commit" and d.get("commit_hash") == "deadbeef" for t, d in events), \
        "file_commit event not emitted"


@pytest.mark.asyncio
async def test_write_file_no_file_commit_when_not_git(workspace, monkeypatch):
    """write_file in coding mode emits file_change but NOT file_commit when not in a git repo."""
    service = AgentService(workspace, log_emitter=DummyLogEmitter())

    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr("app.agent._git_commit_file", lambda path, ws, action, task_id="": None)  # non-git

    class FakeProvider:
        total_tokens = 0
        _total_input_tokens = 0
        _total_output_tokens = 0

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            if len(messages) == 1:
                yield {"type": "tool_call", "id": "c1", "name": "write_file",
                       "args": {"path": "out.py", "content": "x=1"}, "thought": "write"}
                return
            yield {"type": "tool_call", "id": "c2", "name": "finish",
                   "args": {"reason": "done"}, "thought": "done"}

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())

    async def fake_run_action(action, sw=1280, sh=800, on_stream=None):
        from app.models import ToolResult
        return ToolResult(ok=True, output="written", base64_image=None, data=None)

    monkeypatch.setattr(service.tools, "run_action", fake_run_action)

    events = []

    async def capture(task_id, event_type, data):
        events.append((event_type, data))

    monkeypatch.setattr(service, "_emit", capture)
    monkeypatch.setattr(service, "_emit_reasoning", AsyncMock())
    monkeypatch.setattr(service, "_finalize", lambda *a, **kw: None)

    await service.run_task("task-git-2", "Write out.py", mode="coding")

    assert any(t == "file_change" for t, d in events), "file_change should still be emitted"
    assert not any(t == "file_commit" for t, d in events), "file_commit must NOT be emitted in non-git workspace"


def test_file_write_types_set():
    """_FILE_WRITE_TYPES contains the expected action types."""
    assert "write_file" in _FILE_WRITE_TYPES
    assert "text_create" in _FILE_WRITE_TYPES
    assert "text_str_replace" in _FILE_WRITE_TYPES
    assert "text_insert" in _FILE_WRITE_TYPES


def test_git_commit_file_includes_task_id_in_message(workspace, monkeypatch):
    """Commit message body includes task_id[:8] prefix for audit trail (AI-31)."""
    commit_messages = []

    def fake_run(args, **kwargs):
        m = MagicMock()
        if "--is-inside-work-tree" in args:
            m.returncode = 0
        elif "add" in args:
            m.returncode = 0
        elif "commit" in args:
            # Capture the -m argument
            idx = args.index("-m")
            commit_messages.append(args[idx + 1])
            m.returncode = 0
            m.stdout = "1 file changed"
        elif "--short" in args:
            m.returncode = 0
            m.stdout = "abc1234"
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    monkeypatch.setattr("app.agent.subprocess.run", fake_run)
    result = _git_commit_file("app/foo.py", workspace, "write_file", "mytask-abc123")
    assert result == "abc1234"
    assert len(commit_messages) == 1
    assert "task: mytask-a" in commit_messages[0], "task_id[:8] must appear in commit message"


def test_git_commit_file_no_task_id_uses_subject_only(workspace, monkeypatch):
    """Commit message is plain subject when no task_id is provided (backward compat)."""
    commit_messages = []

    def fake_run(args, **kwargs):
        m = MagicMock()
        if "--is-inside-work-tree" in args:
            m.returncode = 0
        elif "add" in args:
            m.returncode = 0
        elif "commit" in args:
            idx = args.index("-m")
            commit_messages.append(args[idx + 1])
            m.returncode = 0
            m.stdout = "1 file changed"
        elif "--short" in args:
            m.returncode = 0
            m.stdout = "deadbeef"
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    monkeypatch.setattr("app.agent.subprocess.run", fake_run)
    result = _git_commit_file("out.py", workspace, "write_file")
    assert result == "deadbeef"
    assert len(commit_messages) == 1
    assert "\n" not in commit_messages[0], "no multi-line message when task_id is absent"
    assert commit_messages[0] == "[ai-computer] write_file: out.py"
