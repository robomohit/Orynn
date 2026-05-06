"""Unit tests for core Pydantic models (fast, no network)."""

from __future__ import annotations

import pytest

from app.models import Action, ActionType, AgentContext, HierarchicalPlan, SubTask, TaskRecord
from app.providers import _extract_json


def test_action_finish_roundtrip():
    a = Action(
        id="a1",
        type=ActionType.finish,
        args={"reason": "done"},
        explanation="finish",
    )
    dumped = a.model_dump()
    b = Action(**dumped)
    assert b.type == ActionType.finish
    assert b.args.get("reason") == "done"


def test_hierarchical_plan_minimal():
    plan = HierarchicalPlan(
        reasoning="test",
        sub_tasks=[
            SubTask(
                id="s1",
                description="Do one thing",
                actions=[
                    Action(
                        id="x1",
                        type=ActionType.screenshot,
                        args={},
                        explanation="shot",
                    )
                ],
            )
        ],
    )
    assert len(plan.sub_tasks) == 1
    assert plan.sub_tasks[0].actions[0].type == ActionType.screenshot


def test_task_record_defaults():
    rec = TaskRecord(
        id="tid",
        status="running",
        context=AgentContext(goal="g"),
    )
    assert rec.id == "tid"
    assert rec.context.goal == "g"


def test_task_id_pattern_for_api_matches_models():
    """Task IDs used in API validation must be valid string content for records."""
    tid = "a1b2c3d4e5f6"
    rec = TaskRecord(id=tid, status="done", context=AgentContext(goal="ok"))
    assert rec.id == tid


def test_extract_json_array_returns_dict():
    result = _extract_json('[1, 2, 3]')
    assert isinstance(result, dict)
    assert result == {"result": [1, 2, 3]}


def test_extract_json_string_returns_dict():
    result = _extract_json('"hello"')
    assert isinstance(result, dict)
    assert result == {"result": "hello"}


def test_extract_json_dict_unchanged():
    result = _extract_json('{"key": "val"}')
    assert result == {"key": "val"}


def test_extract_json_empty_dict_unchanged():
    result = _extract_json('{}')
    assert result == {}
