import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.agent import AgentService
from pathlib import Path

@pytest.mark.asyncio
async def test_action_parser():
    engine = AgentService(workspace=Path("."), log_emitter=MagicMock())
    
    # We can test the extraction logic directly if we extract it, 
    # but since it's buried in run_task stream loop, let's mock the stream.
    # We will test using mock_stream.
    
    mock_provider = MagicMock()
    
    async def mock_stream_chat(*args, **kwargs):
        yield '<thought>I should run a command</thought>\n'
        yield '<action type="run_command">\n'
        yield '{"command": "echo hello"}\n'
        yield '</action>\n'
        yield '<action type="finish">{"reason":"done"}</action>'

    mock_provider.stream_chat = mock_stream_chat

    events = []
    engine._emit = AsyncMock(side_effect=lambda task_id, type, data: events.append((type, data)))
    engine._emit_reasoning = AsyncMock()

    with patch('app.agent.PlannerProvider', return_value=mock_provider):
        await engine.run_task("test_task_id", "Run a test command", mock_provider)

    action_starts = [e for e in events if e[0] == "action_start"]
    assert len(action_starts) >= 1
    assert action_starts[0][1]["action_type"] == "run_command"

@pytest.mark.asyncio
async def test_delegate_parser():
    engine = AgentService(workspace=Path("."), log_emitter=MagicMock())
    mock_provider = MagicMock()
    
    async def mock_stream_chat(*args, **kwargs):
        yield '<thought>I will delegate now.</thought>\n'
        yield '<delegate model="gpt-4o-mini">\n'
        yield '<thought>Delegating this step</thought>\n'
        yield '<task>Write a haiku</task>\n'
        yield '</delegate>\n'
        yield '<action type="finish">{"reason":"done"}</action>'

    mock_provider.stream_chat = mock_stream_chat

    events = []
    engine._emit = AsyncMock(side_effect=lambda task_id, type, data: events.append((type, data)))
    engine._emit_reasoning = AsyncMock()

    # To avoid the actual sub-agent running in the background and hitting the network,
    # we patch asyncio.to_thread where it calls the sub-agent's call_llm.
    with patch('app.agent.PlannerProvider', return_value=mock_provider), \
         patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        
        mock_to_thread.return_value = "Here is a haiku."
        await engine.run_task("test_delegate_id", "Run a test delegate", mock_provider)

    action_starts = [e for e in events if e[0] == "action_start"]
    assert len(action_starts) >= 1
    assert action_starts[0][1]["action_type"] == "delegate"
    
    action_results = [e for e in events if e[0] == "action_result"]
    assert len(action_results) >= 1
    assert action_results[0][1]["action_type"] == "delegate"
    assert action_results[0][1]["output"] == "Here is a haiku."

