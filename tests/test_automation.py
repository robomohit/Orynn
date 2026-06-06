"""Tests for app/automation.py — Watch & Act slice 1 (AI-7)."""
import asyncio
import time

import pytest

import app.automation as auto_mod
from app.automation import CronTrigger, TriggerRegistry, _parse_cron_field


# ── _parse_cron_field ─────────────────────────────────────────────────────────

def test_parse_cron_field_wildcard():
    assert _parse_cron_field("*", 0, 59) is None


def test_parse_cron_field_single():
    assert _parse_cron_field("5", 0, 59) == {5}


def test_parse_cron_field_range():
    assert _parse_cron_field("1-3", 0, 59) == {1, 2, 3}


def test_parse_cron_field_list():
    assert _parse_cron_field("0,15,30", 0, 59) == {0, 15, 30}


def test_parse_cron_field_step():
    assert _parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}


# ── CronTrigger ───────────────────────────────────────────────────────────────

def test_cron_trigger_wildcard_fires():
    t = CronTrigger("* * * * *", "every minute task")
    assert t.should_fire(time.time())


def test_cron_trigger_wrong_hour_does_not_fire():
    now = time.time()
    t_struct = time.localtime(now)
    wrong_hour = (t_struct.tm_hour + 1) % 24
    trigger = CronTrigger(f"* {wrong_hour} * * *", "wrong hour task")
    assert not trigger.should_fire(now)


def test_cron_trigger_wrong_minute_does_not_fire():
    now = time.time()
    t_struct = time.localtime(now)
    wrong_min = (t_struct.tm_min + 1) % 60
    trigger = CronTrigger(f"{wrong_min} * * * *", "wrong minute task")
    assert not trigger.should_fire(now)


def test_cron_trigger_invalid_field_count_raises():
    with pytest.raises(ValueError, match="5 fields"):
        CronTrigger("* * * *", "bad schedule")


def test_cron_trigger_friday_5pm_weekday_mapping():
    # cron "5" = Friday → Python tm_wday=4
    trigger = CronTrigger("0 17 * * 5", "clean Downloads")
    assert trigger._wday == {4}
    assert trigger._hour == {17}
    assert trigger._minute == {0}


def test_cron_trigger_to_dict_round_trip():
    t = CronTrigger("0 9 * * 1", "Monday morning", trigger_id="abc123")
    d = t.to_dict()
    assert d == {"id": "abc123", "type": "cron", "schedule": "0 9 * * 1", "task_template": "Monday morning"}
    t2 = CronTrigger.from_dict(d)
    assert t2.schedule == t.schedule
    assert t2.task_template == t.task_template
    assert t2.trigger_id == t.trigger_id


def test_cron_trigger_sunday_alias():
    # cron 0 and 7 both mean Sunday → Python tm_wday=6
    t0 = CronTrigger("0 0 * * 0", "Sunday 0")
    t7 = CronTrigger("0 0 * * 7", "Sunday 7")
    assert t0._wday == {6}
    assert t7._wday == {6}


# ── TriggerRegistry ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton(tmp_path, monkeypatch):
    # Isolate each test's automation.json. workspace_state_path() resolves via
    # AI_COMPUTER_WORKSPACE (set session-wide in conftest), so point it at this
    # test's tmp_path — chdir alone no longer isolates the workspace.
    monkeypatch.setenv("AI_COMPUTER_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    auto_mod._registry = None
    yield
    auto_mod._registry = None


def test_registry_add_list_remove():
    r = auto_mod.get_registry()
    entry = r.add(CronTrigger("0 9 * * 1", "Monday morning"))
    assert entry["type"] == "cron"
    assert entry["schedule"] == "0 9 * * 1"
    assert len(r.list_triggers()) == 1
    removed = r.remove(entry["id"])
    assert removed
    assert len(r.list_triggers()) == 0


def test_registry_remove_nonexistent_returns_false():
    r = auto_mod.get_registry()
    assert not r.remove("nonexistent-id")


def test_registry_persists_across_reload(tmp_path):
    r1 = auto_mod.get_registry()
    r1.add(CronTrigger("0 9 * * 1", "Monday morning"))
    # New instance reads from same file
    r2 = TriggerRegistry()
    assert len(r2.list_triggers()) == 1
    assert r2.list_triggers()[0]["schedule"] == "0 9 * * 1"


def test_registry_due_fires_once_per_minute():
    r = auto_mod.get_registry()
    r.add(CronTrigger("* * * * *", "every minute"))
    now = time.time()
    fired = r.due(now)
    assert len(fired) == 1
    # Same minute: does not fire again
    fired2 = r.due(now)
    assert len(fired2) == 0


def test_registry_due_non_matching_trigger_does_not_fire():
    r = auto_mod.get_registry()
    now = time.time()
    t_struct = time.localtime(now)
    wrong_hour = (t_struct.tm_hour + 1) % 24
    r.add(CronTrigger(f"* {wrong_hour} * * *", "wrong hour"))
    assert r.due(now) == []


# ── poll_and_fire ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_and_fire_calls_submit_fn():
    r = auto_mod.get_registry()
    r.add(CronTrigger("* * * * *", "fire me"))
    submitted: list[str] = []

    async def fake_submit(goal: str) -> None:
        submitted.append(goal)

    task = asyncio.create_task(auto_mod.poll_and_fire(fake_submit))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert submitted == ["fire me"]


@pytest.mark.asyncio
async def test_poll_and_fire_submit_error_does_not_crash():
    r = auto_mod.get_registry()
    r.add(CronTrigger("* * * * *", "failing task"))

    async def bad_submit(goal: str) -> None:
        raise RuntimeError("submit exploded")

    task = asyncio.create_task(auto_mod.poll_and_fire(bad_submit))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Should not propagate — poller logs warning and continues
