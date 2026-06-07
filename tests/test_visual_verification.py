import pytest

from app.agent import AgentService
from app.log_emitter import log_emitter


@pytest.mark.asyncio
async def test_desktop_reactive_finish_finalizes(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=log_emitter)

    class FakeProvider:
        model = "tier:uia"

        def __init__(self):
            self._total_input_tokens = 0
            self._total_output_tokens = 0

        @property
        def total_tokens(self):
            return self._total_input_tokens + self._total_output_tokens

        def plan_hierarchical(self, *args, **kwargs):
            raise AssertionError("desktop mode should not use upfront structured planning")

        async def stream_chat_with_tools(self, system, messages, tools, screenshot_b64=None):
            yield {
                "type": "tool_call",
                "id": "finish-call",
                "name": "finish",
                "args": {"reason": "done"},
                "thought": "done",
            }

    finalizations = []
    provider = FakeProvider()
    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: provider)
    monkeypatch.setattr("app.agent.is_vision_model", lambda model: False)
    monkeypatch.setattr(service.memory, "search", lambda goal, limit=5: [])
    monkeypatch.setattr(service.memory, "recall_sessions", lambda goal, limit=5: [])
    monkeypatch.setattr(service, "_finalize", lambda *args, **kwargs: finalizations.append(args))

    await service.run_task("t3", "refactor", mode="computer")

    assert finalizations[-1][1] == "done"
    assert finalizations[-1][2] == "done"
