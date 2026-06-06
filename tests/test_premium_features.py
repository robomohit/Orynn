from __future__ import annotations

import json


def test_project_rules_and_workflow_expansion(tmp_path):
    from app.premium_features import discover_project_rules, expand_workflow_goal

    (tmp_path / "AGENTS.md").write_text("Use pytest and keep changes small.", encoding="utf-8")
    rules_dir = tmp_path / ".orynn" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "ui.md").write_text("UI controls must be accessible.", encoding="utf-8")
    workflows = tmp_path / ".orynn" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ship.md").write_text("Run tests, summarize risk, prepare rollback.", encoding="utf-8")

    rules = discover_project_rules(tmp_path)
    assert "AGENTS.md" in rules
    assert "Use pytest" in rules
    assert ".orynn" in rules

    expanded = expand_workflow_goal("/ship finish the feature", tmp_path)
    assert "finish the feature" in expanded
    assert "Workflow /ship" in expanded
    assert "rollback" in expanded


def test_preflight_plan_is_local_and_mode_aware():
    from app.premium_features import build_preflight_plan

    plan = build_preflight_plan("fix the failing tests", mode="coding", autonomy_level="careful")
    descriptions = [s["description"] for s in plan["sub_tasks"]]
    assert any("Inspect" in step for step in descriptions)
    assert any("Pause for approval" in step for step in descriptions)


def test_hooks_run_from_local_config(tmp_path):
    from app.premium_features import run_task_hooks

    (tmp_path / ".orynn").mkdir()
    (tmp_path / ".orynn" / "hooks.json").write_text(
        json.dumps({"task_done": [{"name": "echo", "command": "python -c \"print('hook ok')\""}]}),
        encoding="utf-8",
    )

    results = run_task_hooks(tmp_path, "task_done", {"task_id": "t1"})
    assert results[0]["name"] == "echo"
    assert results[0]["ok"] is True
    assert "hook ok" in results[0]["output"]


def test_detect_ollama_success(monkeypatch):
    from app import premium_features as pf

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "llama3.2"}, {"name": "qwen2.5"}]}

    monkeypatch.setattr(pf.httpx, "get", lambda *a, **k: Resp())
    data = pf.detect_ollama("http://ollama.test")
    assert data["available"] is True
    assert data["models"] == ["llama3.2", "qwen2.5"]


def test_send_completion_notification_discord(monkeypatch):
    from app import premium_features as pf

    class _Resp:
        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr(pf.httpx, "post", lambda url, json=None, timeout=None: (calls.append((url, json)), _Resp())[1])
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    result = pf.send_completion_notification("Write a sort function", "done", "Completed successfully")

    assert result["ok"] is True
    assert result["sent"] == ["discord"]
    assert len(calls) == 1
    assert "Write a sort function" in calls[0][1]["content"]
    assert "done" in calls[0][1]["content"]


def test_send_completion_notification_no_connector(monkeypatch):
    from app import premium_features as pf

    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    called = []
    monkeypatch.setattr(pf.httpx, "post", lambda *a, **kw: called.append(1))

    result = pf.send_completion_notification("some goal", "failed", "error occurred")

    assert result["ok"] is False
    assert result["sent"] == []
    assert called == []
