"""Agent-loop tuning per model class.

The loop's defaults were tuned for small free models: tight step caps, a
100k-token budget, aggressive history truncation, clipped observations and
periodic goal reminders. Those limits protect a weak model from itself, but
they hold a strong, large-context model back — it loses the control menus and
earlier results it could have used, and gets cut off mid-task.

`profile_for(model)` returns the limits to use. Explicit env settings
(AGENT_MAX_STEPS, BROWSER_MAX_STEPS, DESKTOP_MAX_STEPS, ORYNN_TOKEN_BUDGET)
always win over the profile.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from . import anthropic_api


@dataclass(frozen=True)
class LoopProfile:
    name: str
    agent_steps: int
    browser_steps: int
    desktop_steps: int
    token_budget: int
    # History kept in the prompt (messages). Older ones are dropped.
    max_turns: int
    # Old observations/tool args are truncated only once the history exceeds
    # this many characters (0 = truncate every step). Leaving history untouched
    # until then keeps the provider's prompt cache valid between steps.
    compress_after_chars: int
    # Per-observation character caps when appending a tool result.
    obs_limit_coding: int
    obs_limit_desktop: int
    obs_limit_other: int
    # Re-inject the goal every few desktop steps (small models drift).
    goal_reanchor: bool
    # Prior-conversation seeding for follow-up messages.
    history_messages: int
    history_chars: int
    history_turn_chars: int

    @property
    def strong(self) -> bool:
        return self.name == "strong"


DEFAULT = LoopProfile(
    name="default",
    agent_steps=25, browser_steps=35, desktop_steps=40,
    token_budget=100_000,
    max_turns=30,
    compress_after_chars=0,
    obs_limit_coding=3000, obs_limit_desktop=1600, obs_limit_other=1000,
    goal_reanchor=True,
    history_messages=16, history_chars=6000, history_turn_chars=2000,
)

STRONG = LoopProfile(
    name="strong",
    agent_steps=60, browser_steps=80, desktop_steps=100,
    token_budget=2_000_000,
    max_turns=400,
    compress_after_chars=600_000,   # ~150k tokens of a 1M window
    obs_limit_coding=20_000, obs_limit_desktop=12_000, obs_limit_other=12_000,
    goal_reanchor=False,
    history_messages=60, history_chars=60_000, history_turn_chars=8000,
)


def is_strong_model(model: str) -> bool:
    """Current-generation Claude via the native API. Force either way with
    ORYNN_LOOP_PROFILE=strong|default (e.g. for a strong model on OpenRouter)."""
    forced = os.environ.get("ORYNN_LOOP_PROFILE", "").strip().lower()
    if forced in ("strong", "default"):
        return forced == "strong"
    return anthropic_api.is_adaptive_model(str(model or ""))


def _env_int(name: str):
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def profile_for(model: str) -> LoopProfile:
    base = STRONG if is_strong_model(model) else DEFAULT
    overrides = {
        field: value
        for field, value in (
            ("agent_steps", _env_int("AGENT_MAX_STEPS")),
            ("browser_steps", _env_int("BROWSER_MAX_STEPS")),
            ("desktop_steps", _env_int("DESKTOP_MAX_STEPS")),
            ("token_budget", _env_int("ORYNN_TOKEN_BUDGET")),
        )
        if value is not None and value > 0
    }
    return replace(base, **overrides) if overrides else base
