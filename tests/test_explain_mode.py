from types import SimpleNamespace

import pytest

from app.agent import AgentService, _extract_point_tag


def test_extract_point_tag_strips_and_parses_coordinates():
    text, point = _extract_point_tag("The button is here. [POINT:320,240:submit button]")
    assert text == "The button is here."
    assert point == {"x": 320, "y": 240, "label": "submit button"}


def test_extract_point_tag_ignores_none_and_malformed():
    text, point = _extract_point_tag("Nothing to point at. [POINT:none]")
    assert text == "Nothing to point at."
    assert point is None

    text2, point2 = _extract_point_tag("Bad tag [POINT:abc]")
    assert text2 == "Bad tag"
    assert point2 is None


@pytest.mark.asyncio
async def test_explain_mode_strips_point_tag_and_flashes_pointer(monkeypatch, workspace):
    service = AgentService(workspace, log_emitter=SimpleNamespace(emit=lambda *a, **k: None))
    events = []
    flashed = []

    class FakeProvider:
        def __init__(self):
            self.model = "google/gemma-4-31b-it:free"

        async def stream_chat(self, system, messages, screenshot_b64=None):
            yield "Check the top-right control. [POINT:640,120:toolbar button]"

    async def capture_emit(task_id, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.agent.PlannerProvider", lambda model=None: FakeProvider())
    monkeypatch.setattr("app.agent._capture_screenshot_b64", lambda sw, sh: "data:image/jpeg;base64,abc")
    monkeypatch.setattr("app.agent._flash_pointer", lambda x, y, hold_ms=400: flashed.append((x, y, hold_ms)))
    monkeypatch.setattr(service, "_emit", capture_emit)

    await service.run_task("explain-1", "What is this button?", mode="explain")

    done_events = [data for event, data in events if event == "done"]
    assert done_events
    assert done_events[-1]["reason"] == "Check the top-right control."
    assert flashed
    x, y, hold_ms = flashed[-1]
    assert x > 0 and y > 0
    assert hold_ms == 400
