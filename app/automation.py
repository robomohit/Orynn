"""Watch & Act — trigger registry + CronTrigger (slice 1, AI-7).

Provides:
  CronTrigger  — fires on a standard 5-field cron schedule
  TriggerRegistry — persisted list of triggers, fire-once-per-minute semantics
  poll_and_fire   — async background poller; inject a submit coroutine
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from .state_store import read_json, workspace_state_path, write_json

_log = logging.getLogger(__name__)
_REGISTRY_FILE = "automation.json"


# ── persistence ──────────────────────────────────────────────────────────────

def _load_file() -> list[dict]:
    data = read_json(workspace_state_path(_REGISTRY_FILE), [])
    return data if isinstance(data, list) else []


def _save_file(data: list[dict]) -> None:
    write_json(workspace_state_path(_REGISTRY_FILE), data)


# ── Trigger ABC ───────────────────────────────────────────────────────────────

class Trigger(ABC):
    trigger_id: str
    task_template: str

    @abstractmethod
    def should_fire(self, now: float) -> bool: ...

    @abstractmethod
    def to_dict(self) -> dict: ...


# ── CronTrigger ───────────────────────────────────────────────────────────────

def _parse_cron_field(s: str, lo: int, hi: int) -> Optional[set[int]]:
    """Parse one cron field. Returns None for wildcard '*', otherwise set of ints."""
    if s == "*":
        return None
    result: set[int] = set()
    for part in s.split(","):
        if "-" in part and "/" not in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        elif "/" in part:
            base, step = part.split("/", 1)
            start = lo if base == "*" else int(base)
            result.update(range(start, hi + 1, int(step)))
        else:
            result.add(int(part))
    return result


# cron weekday 0=Sun..6=Sat (7=Sun alias) → Python tm_wday 0=Mon..6=Sun
_CRON_TO_PY_WDAY: dict[int, int] = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


class CronTrigger(Trigger):
    def __init__(
        self,
        schedule: str,
        task_template: str,
        trigger_id: Optional[str] = None,
    ) -> None:
        parts = schedule.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron schedule {schedule!r}: need exactly 5 fields "
                "(minute hour mday month wday)"
            )
        self.schedule = schedule
        self.task_template = task_template
        self.trigger_id = trigger_id or uuid.uuid4().hex[:8]
        self._minute = _parse_cron_field(parts[0], 0, 59)
        self._hour = _parse_cron_field(parts[1], 0, 23)
        self._mday = _parse_cron_field(parts[2], 1, 31)
        self._month = _parse_cron_field(parts[3], 1, 12)
        raw_wday = _parse_cron_field(parts[4], 0, 7)
        self._wday: Optional[set[int]] = (
            None if raw_wday is None
            else {_CRON_TO_PY_WDAY[w] for w in raw_wday if w in _CRON_TO_PY_WDAY}
        )

    def should_fire(self, now: float) -> bool:
        t = time.localtime(now)
        if self._minute is not None and t.tm_min not in self._minute:
            return False
        if self._hour is not None and t.tm_hour not in self._hour:
            return False
        if self._mday is not None and t.tm_mday not in self._mday:
            return False
        if self._month is not None and t.tm_mon not in self._month:
            return False
        if self._wday is not None and t.tm_wday not in self._wday:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.trigger_id,
            "type": "cron",
            "schedule": self.schedule,
            "task_template": self.task_template,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CronTrigger":
        return cls(d["schedule"], d["task_template"], d.get("id"))


# ── TriggerRegistry ───────────────────────────────────────────────────────────

class TriggerRegistry:
    def __init__(self) -> None:
        self._triggers: list[CronTrigger] = []
        self._fired: set[tuple[str, int]] = set()
        self._reload()

    def _reload(self) -> None:
        self._triggers.clear()
        for d in _load_file():
            if d.get("type") == "cron":
                try:
                    self._triggers.append(CronTrigger.from_dict(d))
                except (KeyError, ValueError) as exc:
                    _log.warning("Skipping bad trigger record: %s", exc)

    def _persist(self) -> None:
        _save_file([t.to_dict() for t in self._triggers])

    def add(self, trigger: CronTrigger) -> dict:
        self._triggers.append(trigger)
        self._persist()
        return trigger.to_dict()

    def remove(self, trigger_id: str) -> bool:
        before = len(self._triggers)
        self._triggers = [t for t in self._triggers if t.trigger_id != trigger_id]
        changed = len(self._triggers) < before
        if changed:
            self._persist()
        return changed

    def list_triggers(self) -> list[dict]:
        return [t.to_dict() for t in self._triggers]

    def due(self, now: float) -> list[CronTrigger]:
        """Return triggers that should fire at this minute (fire-once-per-minute)."""
        bucket = int(now // 60)
        self._fired = {k for k in self._fired if k[1] == bucket}
        result = []
        for t in self._triggers:
            key = (t.trigger_id, bucket)
            if key not in self._fired and t.should_fire(now):
                self._fired.add(key)
                result.append(t)
        return result


_registry: Optional[TriggerRegistry] = None


def get_registry() -> TriggerRegistry:
    global _registry
    if _registry is None:
        _registry = TriggerRegistry()
    return _registry


# ── background poller ─────────────────────────────────────────────────────────

async def poll_and_fire(submit_fn: Callable[[str], Awaitable[None]]) -> None:
    """Polls every 30 s; fires due cron triggers by calling submit_fn(goal)."""
    registry = get_registry()
    while True:
        try:
            for trigger in registry.due(time.time()):
                _log.info(
                    "Automation: firing trigger %s (%r)",
                    trigger.trigger_id,
                    trigger.task_template,
                )
                try:
                    await submit_fn(trigger.task_template)
                except Exception as exc:
                    _log.warning(
                        "Automation: trigger %s submit failed: %s",
                        trigger.trigger_id,
                        exc,
                    )
        except Exception as exc:
            _log.warning("Automation poll error: %s", exc)
        await asyncio.sleep(30)
