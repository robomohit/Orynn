import pytest

from app.agent import AgentService


class MockLogEmitter:
    def emit(self, task_id, event, data):
        pass


log_emitter = MockLogEmitter()


class ReactiveOnlyProvider:
    model = "tier:uia"

    def __init__(self):
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self.stream_called = False

    @property
    def total_tokens(self):
        return self._total_input_tokens + self._total_output_tokens

    def _call_llm(self, *args, **kwargs):
        raise AssertionError("desktop routing must not make an upfront planning LLM call")

    def plan_hierarchical(self, *args, **kwargs):
        raise AssertionError("desktop routing must not call the hierarchical planner upfront")

    async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
        self.stream_called = True
        yield {
            "type": "tool_call",
            "id": "finish-call",
            "name": "finish",
            "args": {"reason": "done"},
            "thought": "done",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("complexity", ["atomic", "complex"])
async def test_desktop_tasks_stream_without_upfront_planning(monkeypatch, workspace, complexity):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = ReactiveOnlyProvider()

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: complexity)
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    await service.run_task("desktop-reactive", "Open Notepad", mode="computer")

    assert provider.stream_called is True


@pytest.mark.asyncio
async def test_isolated_desktop_tasks_stream_without_upfront_planning(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)
    provider = ReactiveOnlyProvider()

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)
    monkeypatch.setattr("app.agent.classify_task_complexity", lambda goal: "atomic")
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr("app.agent._get_hwnd_for_title", lambda title: None)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])

    await service.run_task(
        "isolated-reactive",
        "Open Notepad",
        mode="computer_isolated",
        isolated_app="Notepad",
    )

    assert provider.stream_called is True
