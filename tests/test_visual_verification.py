import pytest
from app.agent import AgentService
from app.log_emitter import log_emitter
from app.models import Action, ActionType, HierarchicalPlan, SubTask

@pytest.mark.asyncio
async def test_post_action_screenshot_added(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    plan = HierarchicalPlan(
        reasoning="r",
        sub_tasks=[SubTask(id="s3", description="d3", actions=[Action(id="a3", type=ActionType.wait_action, args={"seconds": 0})])],
        overall_complete=False
    )
    monkeypatch.setattr("app.providers.PlannerProvider.plan_hierarchical", lambda *a, **k: plan)
    monkeypatch.setattr("app.providers.PlannerProvider.reflect_on_subtask", lambda *a, **k: {"success": True})
    monkeypatch.setattr("app.providers.PlannerProvider.evaluate", lambda *a, **k: {"complete": True, "reason": "done"})
    
    await s.run_task("t3", "refactor")
    out = s.memory.search("task_outcome")
    assert any("Outcome: True" in m.content for m in out)
