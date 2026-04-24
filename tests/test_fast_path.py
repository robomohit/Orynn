import pytest
import asyncio
from pathlib import Path
from app.agent import AgentService
from app.models import HierarchicalPlan, SubTask, Action, ActionType

class MockLogEmitter:
    def emit(self, task_id, event, data):
        pass

log_emitter = MockLogEmitter()

@pytest.fixture
def workspace(tmp_path):
    return tmp_path

@pytest.mark.asyncio
async def test_atomic_fast_path_routing(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    
    # Simple goal that should be classified as 'atomic'
    goal = "write hello to world.txt"
    
    # We want to verify that provider._call_llm is called instead of provider.plan_hierarchical
    call_llm_called = False
    plan_hierarchical_called = False
    
    def mock_call_llm(self, system, prompt, screenshot_b64):
        nonlocal call_llm_called
        call_llm_called = True
        return '{"reasoning": "atomic", "overall_complete": false, "sub_tasks": [{"id": "s1", "description": "d1", "actions": [{"id": "a1", "type": "write_file", "args": {"path": "world.txt", "content": "hello"}, "explanation": "ex"}]}]}'

    def mock_plan_hierarchical(*a, **k):
        nonlocal plan_hierarchical_called
        plan_hierarchical_called = True
        return HierarchicalPlan(reasoning="complex", sub_tasks=[], overall_complete=False)

    monkeypatch.setattr("app.providers.PlannerProvider._call_llm", mock_call_llm)
    monkeypatch.setattr("app.providers.PlannerProvider.plan_hierarchical", mock_plan_hierarchical)
    monkeypatch.setattr("app.providers.PlannerProvider.evaluate", lambda *a, **k: {"complete": True})
    
    # Mock the tool execution to avoid actual file system changes or other tool side effects
    async def mock_run_action(*a, **k):
        return type('obj', (object,), {'ok': True, 'output': 'done', 'base64_image': None})
    monkeypatch.setattr("app.tools.ToolExecutor.run_action", mock_run_action)

    await s.run_task("atomic-task", goal)
    
    assert call_llm_called is True
    assert plan_hierarchical_called is False

@pytest.mark.asyncio
async def test_complex_task_routing(monkeypatch, workspace):
    s = AgentService(workspace, log_emitter=log_emitter)
    
    # Complex goal that should be classified as 'complex'
    goal = "refactor the entire authentication system and optimize database queries"
    
    call_llm_called = False
    plan_hierarchical_called = False
    
    def mock_call_llm_complex(self, system, prompt, screenshot_b64):
        nonlocal call_llm_called
        call_llm_called = True
        return '{"reasoning": "atomic", "overall_complete": false, "sub_tasks": []}'

    def mock_plan_hierarchical(*a, **k):
        nonlocal plan_hierarchical_called
        plan_hierarchical_called = True
        return HierarchicalPlan(
            reasoning="complex", 
            sub_tasks=[SubTask(id="s1", description="d1", actions=[Action(id="a1", type=ActionType.wait_action, args={"seconds": 0})])], 
            overall_complete=False
        )

    monkeypatch.setattr("app.providers.PlannerProvider._call_llm", mock_call_llm_complex)
    monkeypatch.setattr("app.providers.PlannerProvider.plan_hierarchical", mock_plan_hierarchical)
    monkeypatch.setattr("app.providers.PlannerProvider.evaluate", lambda *a, **k: {"complete": True})
    monkeypatch.setattr("app.providers.PlannerProvider.reflect_on_subtask", lambda *a, **k: {"success": True})
    async def mock_run_action(*a, **k):
        return type('obj', (object,), {'ok': True, 'output': 'done', 'base64_image': None})
    monkeypatch.setattr("app.tools.ToolExecutor.run_action", mock_run_action)

    await s.run_task("complex-task", goal)
    
    # In the complex path, plan_hierarchical is called. 
    # (Note: plan_hierarchical ITSELF calls _call_llm, but here we are checking the AgentService routing)
    assert plan_hierarchical_called is True
