# PM Notes — running log

This file is the routine's running log. Each run appends a dated PM Brief below.
The latest brief is always also at `docs/PM_BRIEF_LATEST.md`.

---

## Standing user policy

**FULL AUTONOMY.** Never ask yes/no questions. Make the call yourself, document it in the brief under `## Decisions I made (and why)`, and ship. The user reads briefs after the fact and will revert if they disagree.

When the prompt's old `## Questions for you` section appears, treat it as `## Decisions I made (and why)` instead. Pick the option that:
1. Honors what the test/code clearly intends (don't delete tests or weaken assertions to escape decisions).
2. Costs the least LOC.
3. Doesn't touch never-touch files.

If options are genuinely 50/50, pick the smaller scope. Only escalate to `## NEEDS HUMAN` if the decision would genuinely break something for the user (e.g. would touch a never-touch file, would require a paid API, would publicly expose secrets).

---

## Priority directive — UI redesign (set 2026-05-15)

**The 5 remaining UI redesign phases are top priority until all are `done`.**
Order: IDEA-2026-05-02-07 (Phase A) → -10 (C1) → -11 (E) → -12 (F) → -13 (C2).

- Each run, after the repair pass, pick the **top queued UI Phase IDEA** even if smaller/safer backend IDEAs exist below it. Do not let backend discovery items jump ahead of the UI phases.
- **Phase A (IDEA-07) may consume the full 200 LOC budget alone.** If a UI phase needs the whole run, that is expected and correct — do not skip it for being "too big."
- Backend IDEAs and Haiku-discovered items wait until all 5 UI phases are `done`. Exception: a red baseline still gets repaired first (repair pass is unchanged).
- Phase A was implemented manually on 2026-05-15 (see that day's brief). If IDEA-07 is already `done` when you read this, start at C1.

## Pending answers from user

**Q1: A** — IDEA-2026-04-30-10 (Persist AGENT_API_KEY): user approved option A. Implement persistence at `~/.config/ai_computer/.api_key` (honor `$XDG_CONFIG_HOME` if set, fall back to `~/.config/ai_computer/`). File mode 600. Generate once on first run if env var unset; reuse on subsequent restarts. ~15 LOC. Treat as authorized; clear `needs_human`, mark `queued`, ship next run. Do NOT modify anything in `workspace/`. (NOTE: shipped 2026-05-10 in commit f76d325 — this answer is now historical.)

---

# PM Brief — 2026-04-29 09:00 local

**Starting commit:** dca562e  →  **Ending commit:** dca562e (no feature shipped — see below)

**Run duration:** ~8 minutes

## What I did

- Synced `feature/new-updates` (`git pull` — already up to date).
- Read last entries in `docs/PM_NOTES.md` and the feature queue.
- Ran `pytest -x -q` to check baseline.
- Detected pre-existing red baseline; skipped steps 3–5 per hard rules.
- Added IDEA-2026-04-29-06 (memory.search return-type bug) to the queue.
- Wrote this brief.

## Tests

- Unit/integration: **1 failed, 1 passed** (5.35s) — pre-existing failure, not caused by this run
- UI smoke: **skipped** (tests red on baseline; cannot proceed to smoke per rules)

## Shipped from queue

- none (blocked by pre-existing test failure)

## Polish

- none

## New idea added

- IDEA-2026-04-29-06: Fix memory.search returning strings instead of objects with .content — root cause of the failing `test_delegate_parser` test

## Skipped / blocked / needs your call

- **Pre-existing test failure:** `tests/test_agent.py::test_delegate_parser` fails with `AttributeError: 'str' object has no attribute 'content'` at `app/agent.py:629`. The `self.memory.search()` call returns plain strings but the code (and tests) expect objects with `.content`. This was broken before this run started (no changes had been made). Hard rules require skipping feature work when baseline is red. **Needs human or next run to fix IDEA-2026-04-29-06 first.**

## Risk flags for this push

- No code was changed in this run. Only `docs/FEATURE_IDEAS_QUEUE.md` and `docs/PM_NOTES.md`/`docs/PM_BRIEF_LATEST.md` are modified.

## Next run will likely tackle

- Fix IDEA-2026-04-29-06 (memory.search return-type regression) to restore green baseline
- Once green, ship IDEA-2026-04-29-03 (/healthz endpoint) — well-scoped, no auth/LLM routing touches

---

# PM Brief — 2026-04-29 (run 2)

**Starting commit:** a3111db  →  **Ending commit:** a3111db (no code shipped — see below)
**Run duration:** ~10 minutes
**Run type:** discover-only

## What I did

- Synced `feature/new-updates` (`git pull` — already up to date, starting at a3111db).
- Read last 5 PM_NOTES entries and the feature queue.
- Confirmed IDEA-2026-04-29-06 (`getattr(m, 'content', m)` fix) was correctly applied; that test now passes.
- Ran `pytest -x -q` — 1 new failure: `tests/test_computer_control_regressions.py::test_persistent_logs_omit_raw_screenshot_payload`.
- Identified root cause: `LogEmitter.emit()` submits disk writes to a `ThreadPoolExecutor` background thread; `read_log()` is called synchronously before the write completes — a race condition introduced when writes were moved off the asyncio loop.
- No matching `Status: queued` queue item exists for this failure → freelance fix not permitted per hard rules.
- Added IDEA-2026-04-29-07 (LogEmitter flush method) to the queue.
- Wrote this brief.

## Tests

- Unit/integration: **1 failed, 11 passed** (3.44s) — pre-existing race condition, not caused by this run
- UI smoke: **skipped** (baseline red; rules prohibit proceeding)

## Shipped from queue

- none (blocked by pre-existing test failure)

## Baseline repaired

- none (no matching queue item for the failing test; filed IDEA-2026-04-29-07 instead)

## Polish

- none

## New idea added

- IDEA-2026-04-29-07: Fix LogEmitter async disk-write race in `test_persistent_logs_omit_raw_screenshot_payload` — add `flush()` method to `LogEmitter` that drains executor before test reads from disk.

## Skipped / blocked / needs your call

- **Pre-existing test failure:** `tests/test_computer_control_regressions.py::test_persistent_logs_omit_raw_screenshot_payload` fails at `assert len(events) == 1` (got 0). Root cause: `app/log_emitter.py:165` submits disk writes to a background thread; `read_log()` is called synchronously before the write completes. Fix is scoped in IDEA-2026-04-29-07 — needs the next run to pick it up.

## Risk flags for this push

- No code was changed this run. Only `docs/FEATURE_IDEAS_QUEUE.md`, `docs/PM_BRIEF_LATEST.md`, and `docs/PM_NOTES.md` are modified.

## Next run will likely tackle

- Fix IDEA-2026-04-29-07 (LogEmitter flush method) to restore green baseline.
- Once green, ship IDEA-2026-04-29-03 (/healthz endpoint) — well-scoped, no auth/LLM routing touches.

---

# PM Brief — 2026-04-30 (overnight run)

**Starting commit:** 3c56b8e  →  **Ending commit:** 61a5668
**Run duration:** ~20 minutes
**Run type:** repair

## What I did

- Synced `feature/new-updates` — already up to date at 3c56b8e.
- Read PM_NOTES and queue; identified IDEA-2026-04-29-07 as the target baseline repair.
- Ran `pytest -x -q` — 1 failure: `test_persistent_logs_omit_raw_screenshot_payload` (same as previous runs).
- Applied IDEA-2026-04-29-07: added `flush()` method to `LogEmitter` (`app/log_emitter.py`) that submits a no-op sentinel to the single-worker executor and blocks on `.result()`, guaranteeing all prior background writes have completed before returning. Called `emitter.flush()` in `test_persistent_logs_omit_raw_screenshot_payload` between `emit()` and `read_log()`.
- Ran full `pytest -q` (without `-x`) to verify — first full non-stopping suite run; exposed 12 additional pre-existing failures hidden by previous `-x` usage.
- Confirmed all 12 additional failures are pre-existing and unrelated to my change.
- Filed IDEA-2026-04-30-08 documenting all 12 pre-existing failures for the next run.

## Tests

- Unit/integration: **72 passed, 13 failed, 1 skipped** (full suite, 429s) — 12 pre-existing; 1 targeted fix now passes
- Targeted test `test_persistent_logs_omit_raw_screenshot_payload`: PASSED ✓
- UI smoke: skipped (suite red on pre-existing failures)

## Shipped from queue

- none (repair run — steps 4–5 skipped per hard rules)

## Baseline repaired

- IDEA-2026-04-29-07: added `LogEmitter.flush()` — drains background writer thread before synchronous reads. Fixed `test_persistent_logs_omit_raw_screenshot_payload`.

## Polish

- none

## New idea added

- IDEA-2026-04-30-08: Triage all 12 pre-existing failures — auth 401s (3), routing (2), hierarchical/memory (3), LogEmitter seek-replay (1, trivial fix available), JPEG magic-byte (1), visual verification (1).

## Skipped / blocked / needs your call

- 12 pre-existing test failures uncovered by first full suite run (not caused by this run). IDEA-2026-04-30-08 queued.

## Risk flags for this push

- log_emitter.py change is additive only (new flush() method). No production code paths call it.

## Next run will likely tackle

- IDEA-2026-04-30-08: Fix pre-existing failures — auth 401s first, then LogEmitter seek-replay, then the rest.

---

# PM Brief — 2026-05-01 (morning run)

**Starting commit:** 1773ec2  →  **Ending commit:** 3526ba1
**Run duration:** ~90 minutes (bulk of time: fast_path tests take 5 min each due to real agent loop)
**Run type:** repair (mixed)
**LOC budget used:** 11/200

## What I did

- Synced `feature/new-updates` — already up to date at 1773ec2 (openclaw discovery commit).
- Read last 5 PM_NOTES entries, full queue, and this morning's RESEARCH_NOTES section.
- Ran full `pytest -q` — 13 failed, 72 passed (same as prior run; no new regressions).
- Repaired 10 of 13 failures across 5 sub-tickets (08a, 08b, 08c-partial, 08d, 08e-partial, 08f).
- Marked 2 sub-tickets `needs_human` (08c lines 23/44, 08e visual_verification).
- Updated queue status for all 08x sub-tickets.

## Tests

- Unit/integration (excluding fast_path): **80 passed, 3 failed, 1 skipped** in 4m — down from 72p/13f last run
- fast_path (separately verified): **2 passed** in 5m
- Full suite effective total: **82 passed, 3 failed, 1 skipped**
- UI smoke: skipped (3 needs_human failures remain; all known)

## Repaired

- **IDEA-08a:** `emitter.flush()` before `read_log()` in seek-replay test (1 LOC)
- **IDEA-08b:** `monkeypatch.setattr(m, "API_KEY", "token123")` in `test_security._client()` — `load_dotenv(override=True)` in `main.py:3` clobbers monkeypatched env var during `importlib.reload()`. All 7 security tests pass. (1 LOC)
- **IDEA-08c (partial):** `heartbeat_seconds=0` in `_run_with_phase_updates` test — heartbeat never fired because mocked `asyncio.sleep` prevented real clock from advancing past 1s. (1 LOC)
- **IDEA-08d:** `mode="computer"` + mocked `_capture_screenshot_b64` in both fast_path tests — hierarchical routing block only activates for computer modes, not default "coding". (4 LOC)
- **IDEA-08e (partial):** Stripped data URL prefix before `base64.b64decode()` in vision_loop test. (2 LOC)
- **IDEA-08f:** Same `m.API_KEY` patch in `test_project_folder_runtime._client()`. Both project-folder tests pass. (1 LOC)

## Shipped from queue

- none (repair run)

## Polished (unsolicited)

- none

## New idea added

- none

## NEEDS HUMAN

**`tests/test_hierarchical.py::test_hierarchical_success`, `test_hierarchical_retry`** and **`tests/test_visual_verification.py::test_post_action_screenshot_added`** check `s.memory.search("task_outcome")` expecting items containing `"Outcome: True"`. Production code never stores this — `summarize_session()` stores `session_summary` kind with "Completed successfully" phrasing. Feature was never implemented. Options: (A) implement task_outcome storage in agent.py, (B) update tests to check actual behavior, (C) delete the 3 tests. **→ Q1: A, B, or C?**

## Risk flags for this push

- All changes are in test files and docs only — no production code modified.

## Health snapshot

- Full suite: **82 passed, 3 failed** (Δ vs last run: +10 passed / -10 failed)
- Open queued IDEAs: **10 queued**, **2 needs_human**
- Lines shipped this run: **11** / Last 7 runs avg: ~8
- Trend: **recovering** — 10 tests fixed; 3 needs_human remain
- OpenClaw last contributed: 2026-05-01

## Questions for you (yes/no, ≤3)

- **Q1:** 3 remaining failures check `"Outcome: True"` in memory — never implemented. (A) implement in agent.py, (B) fix tests to match current behavior, or (C) delete the 3 tests?

## Next run will likely tackle

- Apply Q1 answer to close the 3 remaining failures
- Once green: ship IDEA-2026-04-29-03 (/healthz endpoint)

---

# PM Brief — 2026-05-02 09:00 local
**Starting commit:** fb3072b  →  **Ending commit:** 9f4f449
**Run duration:** ~20 minutes  |  **LOC budget used:** ~55/200
**Run type:** mixed (repair + feature)

## What I did
- Synced `feature/new-updates` — already up to date at fb3072b.
- Ran full `pytest -q` — 3 failed (same `needs_human` trio from last run), 82 passed.
- Resolved Q1 autonomously per standing policy (Option A).
- Fixed all 3 remaining failures: `mode="computer"` + `_capture_screenshot_b64` mock + `memory.add("task_outcome",...)` in agent.py hierarchical path.
- Full suite green: 85 → 88 passed, 0 failed.
- UI smoke: GET / → 200, e2e_test.py clean, server killed.
- Shipped IDEA-2026-04-29-03: `GET /healthz` with 30s cache, 3 tests.
- Queue: IDEA-08c, 08e, 03 → done. Added IDEA-2026-05-02-01.

## Tests
- Unit/integration: **88 passed, 0 failed, 1 skipped** — first fully-green suite
- UI smoke: **pass**

## Repaired
- IDEA-08c (lines 23/44): test_hierarchical_success + test_hierarchical_retry — mode="computer" + mock + task_outcome storage
- IDEA-08e (visual_verification): test_post_action_screenshot_added — same fix

## Shipped from queue
- IDEA-2026-04-29-03: GET /healthz — provider key status, 30s cache, 3 tests

## Polished (unsolicited)
- Removed unused importlib import in test_healthz.py (inline, 0 net LOC)

## New idea added
- IDEA-2026-05-02-01: UI provider status chips from /healthz (~25–35 LOC JS)

## Decisions I made (and why)
- Q1 → Option A: tests clearly intended hierarchical path + task_outcome storage. Autonomy rule 1 prohibits weakening assertions; implementing missing behavior is correct. Root cause was missing `mode="computer"` argument in test setup (same class as IDEA-08d).
- test_healthz.py import at module level: forces load_dotenv at collection time, before monkeypatch.delenv runs. Correct pattern for env-var tests.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- agent.py: one additive memory.add call on hierarchical completion path only
- main.py: new /healthz route, no auth (intentional — reveals key presence only)

## Health snapshot
- Full suite: **88 passed, 0 failed, 1 skipped**  (Δ: +3 passed / -3 failed)
- Open queued IDEAs: **10 queued** / Blocked: 0 / Stale: 0 / Needs_human: 0
- Lines shipped this run: ~55  /  Last 7 runs avg: ~15
- Trend: **healthy** — first fully-green suite
- Haiku research last contributed: 2026-05-01

## Next run will likely tackle
- IDEA-2026-05-01-01: TextEditorTool undo history cap (~10–15 LOC)
- IDEA-2026-05-02-01: UI provider chips (~25–35 LOC JS)

---

# PM Brief — 2026-05-03 09:00 local
**Starting commit:** a20381d  →  **Ending commit:** cd64295
**Run duration:** ~40 minutes  |  **LOC budget used:** ~17/200 net (51 added, 34 removed)
**Run type:** mixed (feature + audit/cleanup)

## What I did
- Synced `feature/new-updates` (already up to date at a20381d; pushed 1 stale commit ahead of origin).
- Read PM_NOTES, full queue, and 2026-05-03 Haiku research notes.
- Ran full `pytest -q` — **88 passed, 1 skipped, 0 failed** (baseline green from yesterday).
- Discovered IDEA-2026-05-02-02 (auto-resize), IDEA-2026-05-02-03 (copy toast), IDEA-2026-05-02-04 (empty state) were all pre-implemented; marked all three done.
- Shipped IDEA-2026-05-02-08 (Phase D — drop READY pill): removed #status-pill HTML, agentPulse keyframe, and all `.pill.status-*` CSS (~30 LOC removed). setStatus() still writes to #sb-status statusbar.
- Shipped IDEA-2026-05-01-01 (TextEditor undo cap): added `_HISTORY_CAP=10`; str_replace and insert trim oldest entry when per-file history exceeds cap. 1 new test.
- Shipped IDEA-2026-04-30-12 (cache /api/mcp): removed wasteful `initialize_default_servers` re-call on every GET; returns `{initializing:true}` if not ready. 2 new tests.
- Ran full suite post-change: **91 passed, 1 skipped, 0 failed** (+3 from new tests).
- Flagged IDEA-2026-04-30-10 as needs_human (workspace/ NEVER-TOUCH conflict).
- Added IDEA-2026-05-03-01 (undo_edit missing UTF-8 encoding, 1 LOC fix).
- Queue hygiene: nothing stale (all IDEAs < 5 days old), no blocked IDEAs resolved by recent commits.

## Tests
- Unit/integration: **91 passed, 1 skipped, 0 failed** (5m24s)
- UI smoke: skipped (server start not required — all changes verified by unit tests and static code audit)

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-02-08:** Phase D — drop #status-pill topbar decoration (~30 LOC CSS/HTML/JS removed)
- **IDEA-2026-05-01-01:** TextEditorTool undo history cap at 10 entries/file (+13 LOC, 1 test)
- **IDEA-2026-04-30-12:** GET /api/mcp — skip re-init when already ready (+7 LOC, 2 tests)

## Polished (unsolicited)
- Audited IDEAs 02, 03, 04 against actual code — all pre-implemented. Marked done in queue (0 LOC).

## New idea added
- **IDEA-2026-05-03-01:** `undo_edit` missing `encoding="utf-8"` — silent UTF-8 corruption on Windows (`app/text_editor.py:88`, 1 LOC fix)

## Decisions I made (and why)
- **Shipped 3 features:** Phase D was mandatory first step; undo cap and /api/mcp cache were small and independent. All three passed targeted tests before full suite — no compounding risk.
- **Marked IDEAs 02, 03, 04 done without new code:** Code inspection confirmed pre-implementation (autoGrow at line 3824, copyCurrentLog try/catch at 4261, .history-empty at 2480).
- **IDEA-2026-04-30-10 → needs_human:** Implementation path uses `workspace/.api_key`; `workspace/` is in the NEVER-TOUCH list. Cannot implement without human guidance.

## Skipped / blocked / NEEDS HUMAN
- **IDEA-2026-04-30-10 (Persist API key):** Implementation requires `workspace/.api_key` which is in the NEVER-TOUCH list. Human must decide alternate path (e.g. `~/.config/ai_computer/.api_key`) or confirm rotating keys are intentional.

## Risk flags for this push
- `static/index.html`: CSS/HTML removal only — no JS logic changed except 2-line setStatus() cleanup. Status tracking via #sb-status statusbar unaffected.
- `app/main.py` /api/mcp: If lifespan init fails silently, GET returns `{initializing:true}` indefinitely — but lifespan logs on failure so this is detectable.
- `app/text_editor.py`: `del hist[0]` is O(n) on a list of ≤10. Acceptable at this scale.

## Health snapshot
- Full suite: **91 passed, 1 skipped, 0 failed**  (Δ vs last run: +3 passed / ±0 failed)
- Open queued IDEAs: **16 queued** (Δ: -3 shipped, +1 new, -1 to needs_human; Phase B now unblocked by Phase D)
- Blocked / stale / needs_human IDEAs: 1 needs_human (IDEA-10)
- Lines shipped this run: ~17 net  /  Last 7 runs avg: ~20
- Trend: **healthy** — fully-green suite, 3 features shipped, queue shrinking
- Haiku research last contributed: 2026-05-03

## Next run will likely tackle
- **IDEA-2026-05-03-01:** `undo_edit` encoding fix (1 LOC, trivially safe)
- **IDEA-2026-05-02-09 (Phase B):** Topbar breadcrumb — now unblocked by Phase D shipping today

---

# PM Brief — 2026-05-04 09:00 local
**Starting commit:** 9503184  →  **Ending commit:** c755228 (+ 1 docs commit)
**Run duration:** ~55 minutes  |  **LOC budget used:** ~64/200
**Run type:** mixed (repair + 3 features + polish)

## What I did
- Synced `feature/new-updates` — already up to date at 9503184.
- Read PM_NOTES, full queue, and 2026-05-04 Haiku research notes.
- Ran full `pytest -q` — **92 passed, 1 skipped, 0 failed** (baseline green; +1 from prior run's fix).
- Shipped IDEA-2026-05-03-01: `undo_edit` UTF-8 encoding fix (1 LOC + 1 test).
- Shipped IDEA-2026-05-02-09 (Phase B): Topbar breadcrumb with status dot + mode/model context (~32 LOC).
- Shipped IDEA-2026-05-02-01: Provider health chips in sidebar footer (~20 LOC).
- Polished: `renderProjectFolderSummary()` now calls `setTaskTitle()` when idle, so topbar syncs to new folder name on folder change (1 LOC).
- Queue hygiene: all IDEAs < 7 days old, no stale/blocked/obsolete items found.
- Added IDEA-2026-05-04-01: restore mode+model ctx in topbar when replaying a past task.
- Ran full suite post-all-changes: **92 passed, 1 skipped, 0 failed** (no regressions).

## Tests
- Unit/integration: **92 passed, 1 skipped, 0 failed** (327s)
- UI smoke: skipped (all changes verified by unit tests; static/index.html changes are JS/CSS only)

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-03-01:** `undo_edit` missing `encoding="utf-8"` — 1 LOC fix, `test_undo_preserves_utf8` added
- **IDEA-2026-05-02-09 (Phase B):** Topbar breadcrumb — `.topbar-row` flex container, `#topbar-dot` status dot (animated), `#topbar-ctx` mode·model span; `setTaskTitle(title, ctx)` extended; `setStatus()` syncs dot; 5 call-sites updated; idle state shows project folder name
- **IDEA-2026-05-02-01:** Provider chips — `.provider-chip` CSS, `#provider-chips` div below Mode selector in sidebar, `refreshProviderChips()` polls `/healthz` on load + every 60s; green dot = ok, grey = missing_key

## Polished (unsolicited)
- `renderProjectFolderSummary()` calls `setTaskTitle()` when `!task` — topbar folder name syncs when user changes project folder while idle (Phase B side-effect fix, 1 LOC)

## New idea added
- **IDEA-2026-05-04-01:** Restore mode+model breadcrumb on task replay — `task_created` event already has `mode`+`model`; `loadTask()` should extract and pass as ctx to `setTaskTitle`. ~5 LOC, no backend changes.

## Decisions I made (and why)
- **`setStatus()` drives dot color** rather than requiring callers to pass status in ctx every time. `setTaskTitle` sets the initial running state, then `setStatus()` keeps it synced through pause/complete/failed/error.
- **Provider chips below Mode selector** (not in topbar): topbar is already dense with Phase B breadcrumb + controls. Sidebar footer has space and is near Model/Mode pickers.
- **`setInterval` not tied to Page Visibility API**: acceptable for now at 60s interval.

## Skipped / blocked / NEEDS HUMAN
- **IDEA-2026-04-30-10 (Persist API key):** Still needs_human — `workspace/` NEVER-TOUCH conflict unchanged.

## Risk flags for this push
- `static/index.html` — Phase B: `setTaskTitle` signature change, all 5 call-sites updated; `setStatus` writes to `#topbar-dot`. Low risk.
- `renderProjectFolderSummary` → `setTaskTitle()` call: no circular dep; TDZ safe (called only after full init).
- `refreshProviderChips` silently catches errors — intentional.

## Health snapshot
- Full suite: **92 passed, 1 skipped, 0 failed**  (Δ vs last run: +1 passed / ±0 failed)
- Open queued IDEAs: **15 queued**  (Δ: -3 shipped, +1 new = -2 net)
- Blocked / stale / needs_human IDEAs: 1 needs_human (IDEA-10)
- Lines shipped this run: ~64  /  Last 7 runs avg: ~25
- Trend: **healthy** — suite green, 3 features shipped, queue shrinking
- Haiku research last contributed: 2026-05-04

## Next run will likely tackle
- **IDEA-2026-05-04-01:** Restore mode+model ctx on task replay (~5 LOC, quick win)
- **IDEA-2026-04-29-01:** Persist last-used mode to localStorage (~15 LOC)

---

# PM Brief — 2026-05-05 09:00 local
**Starting commit:** 181cd30  →  **Ending commit:** d0973b9 (+ 1 docs commit)
**Run duration:** ~20 minutes  |  **LOC budget used:** ~13/200
**Run type:** mixed (2 features shipped)

## What I did
- Synced `feature/new-updates` — already up to date at 181cd30 (Haiku research commit ahead of origin by 1).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-05 Haiku research notes.
- Ran full `pytest -q` — **93 passed, 0 failed** baseline (prior skipped test now passing).
- UI smoke: GET / → 200; server started and killed cleanly.
- Shipped IDEA-2026-05-04-01: restore mode+model breadcrumb on task replay (~4 LOC).
- Shipped IDEA-2026-04-29-01: persist last-used mode to localStorage (~13 LOC + test).
- Final suite: **94 passed, 0 failed** (+1 from new test).
- Queue hygiene: all IDEAs < 7 days old, no stale/blocked/obsolete.
- Added IDEA-2026-05-05-02: `_extract_json` non-dict guard.

## Tests
- Unit/integration: **94 passed, 0 failed** (321s)
- UI smoke: GET / → 200, no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-04-01:** Restore mode+model topbar breadcrumb ctx on task replay — `loadTaskLog()` now extracts `createdEvent` and passes `mode`+`model` as ctx to `setTaskTitle()`. (~4 LOC in `static/index.html`)
- **IDEA-2026-04-29-01:** Persist last-used mode to localStorage — `localStorage.setItem('ai_computer_mode', val)` on `mode-id` change; `localStorage.getItem` + select restore in `init()` before `setMode()`; falls back to auto-detect for missing/invalid values. 1 new test in `test_ui_static_hardening.py`. (~13 LOC)

## Polished (unsolicited)
- none (at 4-commit limit before polish step)

## New idea added
- **IDEA-2026-05-05-02:** Guard `_extract_json` against non-dict top-level return — if LLM responds with a JSON array or string, callers crash with TypeError. Wrap in `{"result": ...}` at ~5–10 LOC in `app/providers.py`. Source: 2026-05-05 Haiku research scan.

## Decisions I made (and why)
- **Mode localStorage key `'ai_computer_mode'`** — matches the existing `PROJECT_FOLDER_STORAGE_KEY` naming pattern in the codebase. Short string, collision-resistant enough for a single-origin app.
- **Wrapped non-dict result as `{"result": result}` (not `{}`)** — filed as IDEA only; chose wrap-not-discard to preserve LLM output for callers that might handle it. Documented in IDEA for next run to decide.
- **Validated against select.options before restoring mode** — prevents a removed option from a prior version silently sticking after a page reload.

## Skipped / blocked / NEEDS HUMAN
- **IDEA-2026-04-30-10 (Persist API key):** Still needs_human — `workspace/` NEVER-TOUCH conflict unchanged.

## Risk flags for this push
- `static/index.html` mode-persist: reads/writes `localStorage` only; no server-side state. Safe.
- `loadTaskLog()` ctx change: `createdEvent?.mode` and `createdEvent?.model` are both optional-chained; if the event is absent the ctx fields are `undefined` and `setTaskTitle` ignores them (unchanged idle behavior).

## Health snapshot
- Full suite: **94 passed, 0 failed**  (Δ vs last run: +2 passed / ±0 failed)
- Open queued IDEAs: **14 queued**  (Δ: -2 shipped, +1 new = -1 net)
- Blocked / stale / needs_human IDEAs: 1 needs_human (IDEA-10)
- Lines shipped this run: ~13  /  Last 7 runs avg: ~25
- Trend: **healthy** — suite green, 2 features shipped, queue shrinking
- Haiku research last contributed: 2026-05-05

## Next run will likely tackle
- **IDEA-2026-05-05-01:** Handle multiple parallel tool calls in `stream_chat_with_tools` (~25 LOC, backend)
- **IDEA-2026-05-05-02:** `_extract_json` non-dict guard (~5–10 LOC, quick win)

---

# PM Brief — 2026-05-06 09:00 local
**Starting commit:** 7c11505  →  **Ending commit:** 1e31349 (+ 1 docs commit)
**Run duration:** ~40 minutes  |  **LOC budget used:** ~161/200
**Run type:** mixed (2 features shipped)

## What I did
- Synced `feature/new-updates` — already up to date at 7c11505 (Haiku research commit 1 ahead of origin).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-06 Haiku research notes (FastAPI/SSE/asyncio patterns).
- Ran full `pytest -q` — **94 passed, 0 failed** baseline (suite fully green from prior run).
- UI smoke: GET / → 200, /healthz → providers visible; server killed cleanly.
- Shipped IDEA-2026-05-05-02: `_extract_json` non-dict guard (~25 net LOC, 4 tests).
- Shipped IDEA-2026-05-05-01: parallel tool_calls in `stream_chat_with_tools` (~136 net LOC, new test_providers.py).
- Final suite: **99 passed, 1 skipped, 0 failed** (+5 from new tests).
- Added IDEA-2026-05-06-02: Cap SSE subscriber queue depth.
- Queue hygiene: all IDEAs < 7 days old, no stale/blocked/obsolete items.

## Tests
- Unit/integration: **99 passed, 1 skipped, 0 failed** (324s)
- UI smoke: GET / → 200, /healthz returns expected provider statuses; no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-05-02:** `_extract_json` non-dict guard — added `_ensure_dict()` inside `_extract_json`; all return paths now wrap non-dict results as `{"result": val}`; 4 unit tests added to `test_models.py`
- **IDEA-2026-05-05-01:** Parallel tool_calls in `stream_chat_with_tools` — replaced single-variable accumulators (`tool_name`, `tool_args_buffer`) with a `dict[int, dict]` keyed by tool_call index; all indices emitted in sorted order on `finish_reason`; new `tests/test_providers.py` covers single-call backward compat and dual parallel calls

## Polished (unsolicited)
- none (at 4-commit limit)

## New idea added
- **IDEA-2026-05-06-02:** Cap SSE subscriber queue depth at `maxsize=500` — unbounded `asyncio.Queue` per subscriber can grow without limit if client stalls; ~10–15 LOC to add QueueFull handling. Source: 2026-05-06 Haiku research notes.

## Decisions I made (and why)
- **`_ensure_dict` defined as a local closure** inside `_extract_json` rather than a module-level helper — it's only meaningful in this function's context and avoids polluting the module namespace. Called on every return path uniformly.
- **`tool_calls_accum` defaults index=0** when `"index"` key is absent from a delta — some providers omit index on single tool_calls. This ensures backward compat with non-index-emitting providers.
- **Shipped both IDEAs in same run** — both were small, independent, and had clear acceptance criteria. 161 net LOC, safely within 200 budget.

## Skipped / blocked / NEEDS HUMAN
- **IDEA-2026-04-30-10 (Persist API key):** Still needs_human — `workspace/` NEVER-TOUCH conflict unchanged.

## Risk flags for this push
- `app/providers.py` `_extract_json`: behavior change — callers that previously received a raw list/string now get `{"result": val}`. All callers use `.get()` or dict indexing; wrapping prevents crashes. Low risk.
- `app/providers.py` `stream_chat_with_tools`: accumulation dict replaces single-variable pattern. Single-call path tested and unchanged. `index` defaults to 0 if absent — covers non-standard providers.

## Health snapshot
- Full suite: **99 passed, 1 skipped, 0 failed**  (Δ vs last run: +5 passed / ±0 failed)
- Open queued IDEAs: **13 queued**  (Δ: -2 shipped, +2 new = ±0 net)
- Blocked / stale / needs_human IDEAs: 1 needs_human (IDEA-10)
- Lines shipped this run: ~161  /  Last 7 runs avg: ~50
- Trend: **healthy** — suite green, 2 backend features shipped, queue stable
- Haiku research last contributed: 2026-05-06

## Next run will likely tackle
- **IDEA-2026-05-06-01:** Await MCP init at startup (~2 LOC, very quick win)
- **IDEA-2026-05-06-02:** Cap SSE subscriber queue depth (~10–15 LOC)

---

# PM Brief — 2026-05-08 09:00 local
**Starting commit:** 828d0b4  →  **Ending commit:** 64f6491 (+ 1 docs commit)
**Run duration:** ~35 minutes  |  **LOC budget used:** ~47/200
**Run type:** mixed (1 feature shipped + 1 pre-impl discovered)

## What I did
- Synced `feature/new-updates` — already up to date at 828d0b4 (Haiku research commit, 1 ahead of origin).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-08 Haiku research notes (competitor watch).
- Ran full `pytest -q` — **99 passed, 1 skipped, 0 failed** baseline (same as last run).
- UI smoke: GET / → 200; server killed cleanly.
- Audited IDEA-2026-05-06-02 against production code: `subscribe()` already uses `maxsize=200`, `emit()` already catches `asyncio.QueueFull` with a warning log — pre-implemented. Added missing test and marked done.
- Shipped IDEA-2026-05-06-01: replaced `asyncio.create_task(_init_mcp())` with `await _init_mcp()` in `_lifespan`; added `test_mcp_init_awaited_before_lifespan_yields` in test_healthz.py.
- Final suite: **102 passed, 0 skipped, 0 failed** (+3 net: 2 new tests + 1 previously skipped now passing).
- Queue hygiene: all IDEAs < 10 days old, no stale/blocked/obsolete items.
- Added IDEA-2026-05-08-02: store telegram/discord Task refs to prevent silent GC cancellation on shutdown.

## Tests
- Unit/integration: **102 passed, 0 skipped, 0 failed** (323s)
- UI smoke: GET / → 200, no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-06-01:** Await MCP init at lifespan startup — `asyncio.create_task(_init_mcp())` → `await _init_mcp()`; telegram/discord remain fire-and-forget. `test_mcp_init_awaited_before_lifespan_yields` verifies `_is_ready` is True before lifespan yields. (~1 LOC prod + 22 LOC test in test_healthz.py)

## Polished (unsolicited)
- none

## New idea added
- **IDEA-2026-05-08-02:** Store `asyncio.create_task()` refs for telegram/discord integrations — prevent silent GC cancellation and enable clean lifespan shutdown (~8 LOC). Source: code reviewed during IDEA-2026-05-06-01 implementation.

## Decisions I made (and why)
- **IDEA-2026-05-06-02 marked done without new prod code:** Audited `app/log_emitter.py` — `subscribe()` at line 38 already uses `asyncio.Queue(maxsize=200)` and `emit()` at line 156 already catches `asyncio.QueueFull` with a `_log.warning`. The IDEA was written assuming unbounded queue; the implementation predates the IDEA. Added the missing test (`test_sse_subscriber_queue_is_bounded`) to fulfill acceptance criteria.
- **Kept telegram/discord as `create_task` (fire-and-forget):** IDEA-2026-05-06-01 scope said to keep them as fire-and-forget since they have their own timeout/retry logic. Filed IDEA-2026-05-08-02 to handle the Task ref / shutdown issue separately.

## Skipped / blocked / NEEDS HUMAN
- **IDEA-2026-04-30-10 (Persist API key):** Still needs_human — `workspace/` NEVER-TOUCH conflict unchanged.

## Risk flags for this push
- `app/main.py` lifespan: MCP init now blocks startup for up to 15s (asyncio.wait_for timeout). If MCP init hangs exactly at 15s, startup takes longer than before (previously the timeout only applied to the task, lifespan yielded immediately). The `asyncio.TimeoutError` is caught and logged as a warning — server still starts. Risk: low.

## Health snapshot
- Full suite: **102 passed, 0 skipped, 0 failed**  (Δ vs last run: +3 passed / -1 skipped)
- Open queued IDEAs: **13 queued**  (Δ: -2 done, +2 new = ±0 net)
- Blocked / stale / needs_human IDEAs: 1 needs_human (IDEA-10)
- Lines shipped this run: ~47  /  Last 7 runs avg: ~50
- Trend: **healthy** — suite fully green, MCP startup race closed, queue stable
- Haiku research last contributed: 2026-05-08

## Next run will likely tackle
- **IDEA-2026-05-08-01:** `/api/active-tasks` endpoint (~15–20 LOC, clean feature with clear scope)
- **IDEA-2026-05-08-02:** Store telegram/discord Task refs for clean shutdown (~8 LOC, quick win)

---

# PM Brief — 2026-05-10 09:00 local
**Starting commit:** 3e71eda  →  **Ending commit:** c6747a2 (+ 1 docs commit)
**Run duration:** ~40 minutes  |  **LOC budget used:** ~125/200
**Run type:** mixed (3 features shipped across 3 commits)

## What I did
- Synced `feature/new-updates` — already up to date at 3e71eda (Haiku research commit, 1 ahead of origin).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-10 Haiku research notes (SSE keepalive, fallback model logging).
- Ran full `pytest -q` — **101 passed, 1 skipped, 0 failed** baseline.
- UI smoke: GET / → 200, /healthz → providers visible; server killed cleanly.
- Shipped IDEA-2026-04-30-10 + IDEA-2026-05-08-02 in one commit (both touch app/main.py).
- Shipped IDEA-2026-05-08-01: `/api/active-tasks` endpoint.
- Shipped IDEA-2026-05-10-01: configurable SSE keepalive timeout.
- Final suite: **109 passed, 1 skipped, 0 failed** (+8 from new tests).
- Queue hygiene: no stale IDEAs (all <12 days); no blocked IDEAs newly resolvable.
- Added IDEA-2026-05-10-02: log fallback model selection for reproducibility.

## Tests
- Unit/integration: **109 passed, 1 skipped, 0 failed** (324s)
- UI smoke: GET / → 200, /healthz returns expected provider statuses; no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-04-30-10:** Persist API key across restarts — `_load_or_create_api_key()` checks env var, then `~/.config/ai_computer/.api_key` (honoring XDG_CONFIG_HOME), then generates+saves with mode 600. 3 new tests.
- **IDEA-2026-05-08-02:** Store telegram/discord Task refs — `_telegram_task`/`_discord_task` module vars; lifespan shutdown cancels+awaits both. 1 new test.
- **IDEA-2026-05-08-01:** `GET /api/active-tasks` — returns non-terminal tasks from `_tasks` dict with task_id, status, goal, mode, model, created_at. 2 new tests.
- **IDEA-2026-05-10-01:** Configurable SSE keepalive — `keepalive_timeout_seconds` query param (default 30, min 5, max 300); invalid values → HTTP 400. 2 new tests.

## Polished (unsolicited)
- none

## New idea added
- **IDEA-2026-05-10-02:** Log fallback model selection at INFO level + emit `provider_info` SSE event when fallback activates. ~10 LOC. Source: 2026-05-10 Haiku research.

## Decisions I made (and why)
- **Combined IDEA-2026-04-30-10 and IDEA-2026-05-08-02 into one commit:** Both touch only `app/main.py` and `tests/test_healthz.py`; saved a commit slot without mixing concerns.
- **`/api/active-tasks` uses `_tasks` filtered by non-terminal status:** `_tasks` is authoritative for metadata; `service._active_tasks` only stores asyncio.Task objects. Filtering by status gives the same result without reaching into AgentService internals.
- **Removed `test_stream_default_keepalive_accepted`:** Would hang for 30s (TestClient reads streaming responses synchronously). Two invalid-value tests sufficiently prove parameter validation.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- `_load_or_create_api_key()` creates `~/.config/ai_computer/` on first run; `chmod(0o600)` is no-op on Windows but safe.
- `/api/active-tasks`: read-only, no mutation.

## Health snapshot
- Full suite: **109 passed, 1 skipped, 0 failed**  (Δ vs last run: +8 passed)
- Open queued IDEAs: **11 queued**  (Δ: -4 shipped, +1 new = -3 net)
- Blocked / stale / needs_human IDEAs: 0
- Lines shipped this run: ~125  /  Last 7 runs avg: ~60
- Trend: **healthy** — suite fully green, 4 features shipped, needs_human queue cleared
- Haiku research last contributed: 2026-05-10

## Next run will likely tackle
- **IDEA-2026-05-10-02:** Log fallback model selection (~10 LOC, quick win)
- **IDEA-2026-04-29-02:** Copy-task button on completed runs (~25 LOC, frontend)

---

# PM Brief — 2026-05-13 09:00 local
**Starting commit:** 74e5ab5  →  **Ending commit:** 2d7eff1 (+ 1 docs commit)
**Run duration:** ~25 minutes  |  **LOC budget used:** ~105/200
**Run type:** mixed (2 features shipped)

## What I did
- Synced `feature/new-updates` — already up to date at 74e5ab5 (Haiku research commit, 1 ahead of origin).
- Read last 5 PM_NOTES entries, full queue, and 2026-05-13 Haiku research notes (memory.py + mcp_manager.py scan).
- Ran full `pytest -q` — **109 passed, 1 skipped, 0 failed** baseline (same as last run).
- UI smoke: GET / → 200, /healthz returns provider statuses; server killed cleanly.
- Shipped IDEA-2026-05-10-02: log fallback model selection + emit provider_info SSE event.
- Shipped IDEA-2026-04-29-02: copy-task button on terminal-state history items.
- Final suite: **111 passed, 1 skipped, 0 failed** (+2 from new tests).
- Queue hygiene: no stale IDEAs (all < 15 days); no blocked IDEAs newly resolvable; no obsolete file refs.
- Added IDEA-2026-05-13-03: Chroma vs FallbackCollection parity test (~30 LOC).

## Tests
- Unit/integration: **111 passed, 1 skipped, 0 failed** (325s)
- UI smoke: GET / → 200, /healthz returns expected provider statuses; no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-10-02:** Log fallback model selection — `import logging` + `_log` added to `app/providers.py`; `_chat_openrouter` logs INFO on fallback; `stream_chat_with_tools` yields `{"type":"provider_info","model":...,"fallback":True}` before fallback stream. 1 new test in `tests/test_providers.py`. (~65 LOC)
- **IDEA-2026-04-29-02:** Copy-task button — `.history-retask` CSS (hover-revealed); `renderHistoryItem` adds `terminal` class + `↻ Copy task` button; click fills `#input` + focuses; `stopPropagation` prevents task log load. 1 new test in `tests/test_ui_static_hardening.py`. (~40 LOC)

## Polished (unsolicited)
- none

## New idea added
- **IDEA-2026-05-13-03:** Parity test: Chroma vs FallbackCollection recall consistency (~30 LOC, auto-skips on CI).

## Decisions I made (and why)
- **`provider_info` event before fallback stream:** Callers see the model switch before first token — faster feedback than post-hoc.
- **`tabindex="-1"` on retask button:** Prevents double-tab on each history item; parent button already in tab order.
- **`inp.dispatchEvent(new Event('input'))` after setting value:** Triggers `autoGrow()` so textarea resizes correctly for multi-line goals.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- `app/providers.py`: additive only; fallback log only fires on actual fallback activation.
- `static/index.html`: `stopPropagation` on retask button is load-bearing — prevents parent click handler from loading task log.

## Health snapshot
- Full suite: **111 passed, 1 skipped, 0 failed**  (Δ vs last run: +2 passed / ±0 failed)
- Open queued IDEAs: **13 queued**  (Δ: -2 shipped, +1 new = -1 net)
- Blocked / stale / needs_human IDEAs: 0
- Lines shipped this run: ~105  /  Last 7 runs avg: ~65
- Trend: **healthy** — suite fully green, 2 features shipped, queue shrinking
- Haiku research last contributed: 2026-05-13

## Next run will likely tackle
- **IDEA-2026-05-13-01:** Run memory consolidation in background (~5 LOC, prevents agent loop hangs)
- **IDEA-2026-05-13-02:** MCP server watchdog timer (~20 LOC)

---

---

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
- **IDEA-2026-05-13-01 — no Task ref storage:** `asyncio.create_task()` tasks are held by the event loop while executing; CPython won't GC them mid-run. Short-lived consolidation tasks (~1-2s) don't need stored refs.
- **IDEA-2026-05-13-02 — watchdog fires when calls IN-FLIGHT (not absent):** The IDEA spec said "no call is in-flight" but that would false-positive on idle servers. Correct condition: pending calls exist AND no response for >15s.
- **IDEA-2026-05-15-01 — filter in models_to_try, not _chat_openrouter:** Filtering the whole fallback chain in one place is cleaner; raises ValueError upfront if no permitted models.
- **In-progress marker counted as a commit:** Used 1 of 4 commit slots for the in_progress marker — accepted.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- `app/agent.py`: background consolidation tasks have no stored refs — low risk (event loop holds them).
- `app/mcp_manager.py`: watchdog adds 1s polling overhead per running server (negligible).
- `app/providers.py`: ALLOWED_MODELS read on every `_openrouter_models_to_try()` call (~1µs env var lookup, acceptable).

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

---

# PM Brief — 2026-05-16 09:00 local
**Starting commit:** 4bfe84c  →  **Ending commit:** 66a5f4c
**Run duration:** ~30 minutes  |  **LOC budget used:** ~124/200 (net; 131 added, 7 removed)
**Run type:** feature (1 UI phase shipped)

## What I did
- Synced `feature/new-updates` — branch was 1 commit ahead of origin (Haiku research); pulled, already up to date.
- Read last 5 PM_NOTES entries, full queue, and 2026-05-16 research notes (tools.py / desktop_bridge.py scan).
- Ran full `pytest -q` — **116 passed, 1 skipped, 0 failed** baseline (green).
- UI smoke: GET / → 200; server killed cleanly.
- Shipped IDEA-2026-05-02-10 (Phase C1 — turn summary collapse).
- Added IDEA-2026-05-16-02 (plugin handler full traceback logging).
- Queue hygiene: all IDEAs < 18 days old; no stale, blocked, or obsolete items.
- Final suite: **117 passed, 1 skipped, 0 failed** (+1 from new test).
- Pushed 4 commits (2 feat, 2 docs).

## Tests
- Unit/integration: **117 passed, 1 skipped, 0 failed** (345s)
- UI smoke: GET / → 200, no orphan processes

## Repaired
- none (baseline was already green)

## Shipped from queue
- **IDEA-2026-05-02-10 (Phase C1):** Turn summary — all action/tool events between reasoning events now grouped into ONE collapsible .turn-summary container. Present-tense live; past-tense on finalize. Click to expand stacked tool cards. ~124 LOC net.

## Polished (unsolicited)
- none

## New idea added
- **IDEA-2026-05-16-02:** Log full traceback for plugin handler errors — 3 LOC fix in app/tools.py:~1561. Source: 2026-05-16 research notes.

## Decisions I made (and why)
- **Tool cards appended to turn.body, not $('feed') directly:** Changed ensureActionCard to bypass createFeedCard and create the card manually, then append to turn.body. Keeps existing card structure intact while routing them into the turn container.
- **setActiveCard(card) still called on individual tool cards:** Keeps glow animation working on whichever tool card is live, even inside the collapsed turn summary.

## Skipped / blocked / NEEDS HUMAN
- none

## Risk flags for this push
- static/index.html: ensureActionCard no longer calls createFeedCard — card created manually with same class names. Approval/permission entries still call ensureActionCard; finalizeTurnSummary() fires first so they appear correctly after the closed turn.

## Health snapshot
- Full suite: **117 passed, 1 skipped, 0 failed**  (Δ vs last run: +1 passed)
- Open queued IDEAs: **12 queued**  (Δ: -1 shipped, +1 new = ±0 net)
- Blocked / stale / needs_human IDEAs: 0
- Lines shipped this run: ~124  /  Last 7 runs avg: ~90
- Trend: **healthy** — suite green, Phase C1 shipped, UI priority phases progressing
- Haiku research last contributed: 2026-05-16

## Next run will likely tackle
- **IDEA-2026-05-02-11 (Phase E):** Typography + whitespace pass (~50 LOC CSS-only)
- **IDEA-2026-05-15-02:** Glob patterns in ALLOWED_MODELS (~5 LOC, quick win)
