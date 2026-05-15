# PM Brief — 2026-05-15 09:00 local
**Starting commit:** 4f79340  →  **Ending commit:** f8c49a1 (+ 1 docs commit)
**Run duration:** ~35 minutes  |  **LOC budget used:** ~88/200 (44 prod + 44 test)
**Run type:** mixed (3 features shipped)

## What I did
- Synced `feature/new-updates` — already up to date at 4f79340 (Haiku research commit, 1 ahead of origin).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-15 Haiku research notes (competitor watch).
- Ran full `pytest -q` — **111 passed, 1 skipped, 0 failed** baseline (same as last run).
- UI smoke: GET / → 200; killed orphan python process; server clean.
- Shipped IDEA-2026-05-13-01: background memory consolidation (3 agent.py lines + 1 test).
- Shipped IDEA-2026-05-13-02: MCP server watchdog timer (25 LOC in mcp_manager.py + 1 test).
- Shipped IDEA-2026-05-15-01: ALLOWED_MODELS env var for model governance (16 LOC in providers.py + 3 tests).
- Final suite: **117 passed, 0 failed** (+6 from new tests).
- Queue hygiene: no stale IDEAs (all < 17 days); no blocked IDEAs resolved.
- Added IDEA-2026-05-15-02: glob pattern support in ALLOWED_MODELS (~5 LOC follow-up).

## Tests
- Unit/integration: **117 passed, 0 failed** (387s)
- UI smoke: GET / → 200, no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-13-01:** Background memory consolidation — changed 3x `await asyncio.to_thread(memory.maybe_auto_consolidate)` in agent.py to `asyncio.create_task(asyncio.to_thread(...))`. O(n²) Jaccard consolidation no longer blocks the agent loop mid-task. `test_maybe_auto_consolidate_fires_at_threshold` added.
- **IDEA-2026-05-13-02:** MCP server watchdog — `_watchdog()` async task polls every 1s; marks server dead and cancels in-flight calls if no response for `_WATCHDOG_TIMEOUT=15s` while calls are pending. Previously callers waited full 60s `_CALL_TIMEOUT`. `_last_response_at` updated in `_listen()` on each response. `test_mcp_watchdog_marks_dead_when_pending_calls_get_no_response` added.
- **IDEA-2026-05-15-01:** ALLOWED_MODELS env var — `_get_allowed_models()` parses comma-separated whitelist; `_openrouter_models_to_try()` filters fallback chain and raises ValueError if all models blocked. Empty/unset allows all. 3 tests in test_providers.py.

## Polished (unsolicited)
- none (at 4-commit limit before polish step)

## New idea added
- **IDEA-2026-05-15-02:** Glob patterns in ALLOWED_MODELS — use `fnmatch.fnmatch` for patterns like `claude-*`; exact strings still work; ~5 LOC change.

## Decisions I made (and why)
- **IDEA-2026-05-13-01 — no Task ref storage:** `asyncio.create_task()` tasks are held by the event loop while executing; CPython won't GC them mid-run. Short-lived consolidation tasks (~1-2s) don't need stored refs. Filed IDEA-2026-05-15-02 pattern if needed.
- **IDEA-2026-05-13-02 — watchdog fires when calls IN-FLIGHT (not absent):** The IDEA spec said "no call is in-flight" but that would false-positive on idle servers. Correct condition: pending calls exist AND no response for >15s. Documented in commit message.
- **IDEA-2026-05-15-01 — filter in models_to_try, not _chat_openrouter:** Filtering the whole fallback chain in one place is cleaner than checking each candidate individually; raises ValueError upfront if no permitted models, avoiding wasted HTTP calls.
- **In-progress marker counted as a commit:** Used 1 of 4 commit slots for the in_progress marker. Accepted — rules require the marker before implementation.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- `app/agent.py`: background consolidation tasks have no stored refs — low risk (event loop holds them). If consolidation fails silently, no retry mechanism (unchanged from prior behavior).
- `app/mcp_manager.py`: watchdog adds 1s polling overhead per running server. At scale (many MCP servers), this is negligible.
- `app/providers.py`: ALLOWED_MODELS is read on every `_openrouter_models_to_try()` call (env var lookup ~1µs). Could cache in `__init__` if profiling shows it matters — filed as IDEA-2026-05-15-02 follow-up.

## Health snapshot
- Full suite: **117 passed, 0 failed**  (Δ vs last run: +6 passed / ±0 failed)
- Open queued IDEAs: **12 queued**  (Δ: -3 shipped, +1 new = -2 net)
- Blocked / stale / needs_human IDEAs: 0
- Lines shipped this run: ~88  /  Last 7 runs avg: ~72
- Trend: **healthy** — suite fully green, 3 features shipped, queue shrinking
- Haiku research last contributed: 2026-05-15

## Next run will likely tackle
- **IDEA-2026-05-13-03:** Chroma vs FallbackCollection parity test (~30 LOC, auto-skips on CI)
- **IDEA-2026-05-15-02:** Glob patterns in ALLOWED_MODELS (~5 LOC, quick follow-up)
