"""Tests for the pluggable coding-backend connector system."""
import json
import subprocess
from pathlib import Path

import pytest

from app.coding_backends import (
    BackendRegistry,
    ClaudeCodeBackend,
    CodingBrief,
    CodingResult,
)


def test_brief_to_prompt_includes_constraints_and_files():
    brief = CodingBrief(task="Add a /ping route", constraints="Keep it ≤10 LOC", files=["app/main.py"])
    prompt = brief.to_prompt()
    assert "Add a /ping route" in prompt
    assert "Keep it" in prompt
    assert "app/main.py" in prompt


def test_result_to_dict_round_trips():
    r = CodingResult(ok=True, summary="done", files_changed=["a.py"], cost_usd=0.02, session_id="s1")
    d = r.to_dict()
    assert d["ok"] is True and d["summary"] == "done"
    assert d["files_changed"] == ["a.py"] and d["session_id"] == "s1"


def test_detect_reports_missing_when_not_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _c: None)
    b = ClaudeCodeBackend(name="claude-code", command="claude")
    info = b.detect()
    assert info["available"] is False
    assert "not found" in info["detail"]


def test_detect_reports_available_with_version(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/claude")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="9.9.9 (Claude Code)\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    info = ClaudeCodeBackend(name="claude-code", command="claude").detect()
    assert info["available"] is True
    assert info["version"].startswith("9.9.9")


def test_submit_parses_json_envelope(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/claude")
    envelope = {
        "result": "Added the route and a test.",
        "session_id": "sess-42",
        "total_cost_usd": 0.031,
        "is_error": False,
        "structured_output": {"summary": "Added /ping", "files_changed": ["app/main.py"]},
    }

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(envelope), stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = ClaudeCodeBackend(name="claude-code", command="claude").submit(CodingBrief(task="add /ping"))
    assert res.ok is True
    assert res.summary == "Added /ping"
    assert res.files_changed == ["app/main.py"]
    assert res.session_id == "sess-42"
    assert res.cost_usd == pytest.approx(0.031)


def test_submit_surfaces_nonzero_exit(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/claude")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom: bad flag")
    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ClaudeCodeBackend(name="claude-code", command="claude").submit(CodingBrief(task="x"))
    assert res.ok is False
    assert "boom" in res.error


def test_registry_defaults_to_claude_when_no_config(tmp_path):
    reg = BackendRegistry(config_path=tmp_path / "missing.json")
    assert "claude-code" in reg.backends
    assert reg.default == "claude-code"
    assert reg.get().type == "claude"


def test_registry_loads_declared_backends(tmp_path):
    cfg = tmp_path / "backends.json"
    cfg.write_text(json.dumps({
        "defaultBackend": "my-claude",
        "backends": {"my-claude": {"type": "claude", "command": "claude", "model": "claude-sonnet-4-6"}},
    }), encoding="utf-8")
    reg = BackendRegistry(config_path=cfg)
    assert reg.default == "my-claude"
    assert reg.get("my-claude").model == "claude-sonnet-4-6"


def test_registry_tolerates_corrupt_config(tmp_path):
    cfg = tmp_path / "backends.json"
    cfg.write_text("{ not valid json", encoding="utf-8")
    reg = BackendRegistry(config_path=cfg)
    # Falls back to the default claude-code backend instead of crashing.
    assert "claude-code" in reg.backends


def test_registry_raises_on_unknown_backend_type(tmp_path):
    cfg = tmp_path / "backends.json"
    cfg.write_text(json.dumps({
        "backends": {"my-backend": {"type": "claude_code_typo", "command": "claude"}},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown backend type"):
        BackendRegistry(config_path=cfg)
