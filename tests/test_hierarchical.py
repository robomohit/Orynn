import pytest
import time
from app.agent import AgentService
from app.log_emitter import log_emitter
from app.models import Action, ActionType, HierarchicalPlan, SubTask

@pytest.mark.asyncio
async def test_hierarchical_success(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    
    plan = HierarchicalPlan(
        reasoning="r",
        sub_tasks=[SubTask(id="s1", description="d1", actions=[Action(id="a1", type=ActionType.wait_action, args={"seconds": 0})])],
        overall_complete=False
    )
    
    monkeypatch.setattr("app.providers.PlannerProvider.plan_hierarchical", lambda *a, **k: plan)
    monkeypatch.setattr("app.providers.PlannerProvider.reflect_on_subtask", lambda *a, **k: {"success": True})
    monkeypatch.setattr("app.providers.PlannerProvider.evaluate", lambda *a, **k: {"complete": True, "reason": "done"})
    
    await s.run_task("t1", "refactor")
    out = s.memory.search("task_outcome")
    assert any("Outcome: True" in m.content for m in out)

@pytest.mark.asyncio
async def test_hierarchical_retry(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    plan = HierarchicalPlan(
        reasoning="r",
        sub_tasks=[SubTask(id="s2", description="d2", actions=[Action(id="a2", type=ActionType.wait_action, args={"seconds": 0})])],
        overall_complete=False
    )
    monkeypatch.setattr("app.providers.PlannerProvider.plan_hierarchical", lambda *a, **k: plan)
    
    reflections = [{"success": False, "reason": "fail", "retry_actions": [{"type": "wait_action", "args": {"seconds": 0}}]}, {"success": True}]
    def mock_reflect(*a, **k):
        return reflections.pop(0)
    
    monkeypatch.setattr("app.providers.PlannerProvider.reflect_on_subtask", mock_reflect)
    monkeypatch.setattr("app.providers.PlannerProvider.evaluate", lambda *a, **k: {"complete": True, "reason": "done"})
    
    await s.run_task("t2", "refactor")
    out = s.memory.search("task_outcome")
    assert any("Outcome: True" in m.content for m in out)


@pytest.mark.asyncio
async def test_phase_updates_emit_progress(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    seen = []

    async def capture(task_id, event, data):
        if event == "status":
            seen.append(data["message"])

    async def fast_sleep(_seconds):
        await original_sleep(0)

    original_sleep = __import__("asyncio").sleep
    monkeypatch.setattr(s, "_emit", capture)
    monkeypatch.setattr("app.agent.asyncio.sleep", fast_sleep)

    def slow_fn():
        time.sleep(0.03)
        return "ok"

    result = await s._run_with_phase_updates("task", "Thinking", "Still planning", slow_fn)
    assert result == "ok"
    assert any(msg == "Thinking" for msg in seen)
    assert any("Still planning" in msg for msg in seen)
