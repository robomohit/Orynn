# Research Notes (OpenClaw Discovery)

OpenClaw appends dated research notes here each night (3 AM cron).
Claude reads this during its 9 AM survey step before picking work from the queue.

Sources should be cited inline (URLs). Each daily section has its own heading.

---

## 2026-05-01 (scan: codebase patterns)

- **Memory search return-type inconsistency** — `memory.search()` returns plain strings in some contexts but `MemoryItem` objects with `.content` in others. Tests like `test_delegate_parser` (fixed in IDEA-06), `test_hierarchical_success`, and others fail with `AttributeError: 'str' object has no attribute 'content'`. Pattern: code checks `m.content` without defensive `getattr(m, 'content', m)` guard. Source: `tests/test_hierarchical.py:23`, `tests/test_agent.py::test_delegate_parser` [file:app/agent.py:629].
- **LogEmitter async race condition** — `emit()` submits disk writes to single-worker `ThreadPoolExecutor`, but `read_log()` can be called before writes complete. Pattern is fixed in `LogEmitter.flush()` (IDEA-07) but test `test_log_emitter_seek_replay_uses_binary_offsets_for_utf8` still fails with empty replay — likely needs `emitter.flush()` call before `read_log()` in test. Source: `tests/test_project_folder_runtime.py:102`, `app/log_emitter.py:165`.
- **Auth 401 failures in security tests** — three tests (`test_permanent_api_key_still_authenticates_server_api`, `test_task_id_rejects_path_traversal`, `test_create_task_internal_error_does_not_leak_details`) return 401 instead of expected codes. Pattern: `_client()` fixture sets `AGENT_API_KEY=token123` but server may not pick it up correctly; `main.py` generates random key if env var unset and no persisted key file exists. The auth check may be comparing against a different key. Source: `tests/test_security.py:33,60,76`, `app/main.py:21`.
- **Fast-path routing assertion failures** — `test_atomic_fast_path_routing` and `test_complex_task_routing` show `call_llm_called` stays False. Pattern: `PlannerProvider._call_llm` patching at `app.providers` module level may not match actual call site due to import/module aliasing (relative imports vs absolute). Source: `tests/test_fast_path.py:49,88`, `app/providers.py`.
- **JPEG magic-byte / vision-loop failures** — `test_vision_loop.py:28` and `test_visual_verification.py:20` expect base64-decoded payload to start with JPEG magic bytes (`\xff\xd8\xff`). Pattern: screenshot encoder may produce PNG bytes, or mock fixture provides wrong format. Source: `tests/test_vision_loop.py:28`, `tests/test_visual_verification.py:20`.
- **Hierarchical memory `.content` access** — same family as first bullet: `tests/test_hierarchical.py` checks `m.content` on memory search results which may be strings under test mocking. Needs defensive getter pattern used in `agent.py:629/633`. Source: `tests/test_hierarchical.py:23,44,70`.
- **TextEditorTool undo stores full copy pre-edit** — `str_replace`/`insert` store entire file text in `self._history` before modification. Pattern: fine for small files but unbounded growth on large files across many edits (no limit). Source: `app/text_editor.py:49,67`.
- **Missing `LogEmitter.flush()` usage** — test `test_log_emitter_seek_replay_uses_binary_offsets_for_utf8` fails because background thread writes may not be visible to `read_log()` called immediately after `emit()`. Pattern: needs explicit `flush()` before read assertions. Source: `tests/test_project_folder_runtime.py:102`, `app/log_emitter.py:217`.

---

## 2026-05-03 (scan: triage)

**Queue health overview:**
- Total IDEAs: 32 (includes 10 UI Phases A–F)
- Status breakdown: ~18 queued, ~11 done, ~3 split/blocked
- Done in last 72h: IDEA-08a through 08f (12 pre-existing test failures), IDEA-09 (vendored mermaid), IDEA-03 (/healthz endpoint)

**Critical path observations:**
- **UI redesign is a critical bottleneck.** Phases A, D, B, C1, E, F, C2 form a linear dependency chain (7 IDEAs, ~500 LOC total scope). Phase A (sidebar restructure) must ship first to unblock B/D. Currently queued with no in-progress marker.
- **Quick wins available:** IDEA-02 (Copy-task button, ~25 LOC), IDEA-05 (Auto-pause on loops, ~20 LOC), IDEA-04 (Duration badge, ~30 LOC) are independent and low-risk. Could batch these to unblock queue attention for Phase A start.
- **Dependency clarity:** Phase B explicitly notes "depends on Phase D — IDEA-08 — having shipped first" (line 257), but Phase D is purely removal (drop READY pill). D's 25 LOC should ship *before* B to avoid topbar slot conflict. Current queue order (D at line 248, B at line 250) is correct but not marked as dependency.

**Risk flags:**
- **localStorage mode persistence (IDEA-01):** Low-risk but untested. No Playwright smoke test currently specified; suggest adding one.
- **TextEditorTool memory cap (IDEA-2026-05-01-01):** "~10–15 LOC" scope estimate may be low if history format is complex. Worth a 15min code read before picking.
- **Phase F refactor (split HTML/CSS/JS):** Highest-risk in batch (5000 LOC moved). Recommend running Playwright full suite before claiming "zero visual change."

**Blocker status:**
- None currently. All queued items are unblocked or have explicit (but unmarked) soft dependencies.

**Recommendations for next PM run:**
1. Pick Phase D (IDEA-08) first — it's a pure delete, ~5 min, unblocks Phase B topbar work.
2. Batch quick-wins (IDEA-02, 04, 05) in one PR to reduce queue size.
3. Start Phase A (IDEA-07) after quick-wins; mark as in-progress to signal focus.
4. Add Playwright smoke tests to localStorage IDEA-01 acceptance criteria before work starts.

---

## 2026-05-04 (scan: triage)

**Queue shape snapshot:**
- 32 total IDEAs: ~18 queued, 11 done, 1 needs_human (IDEA-10), 2 split/blocked
- Velocity: 3 shipped past 24h (Phase D, TextEditor cap, /api/mcp cache)
- Test suite: 91 passed, 1 skipped, 0 failed (3-test net gain from recent work)

**Dependency resolution — UI Phases unblocked:**
- Phase D (IDEA-08: drop READY pill) ✅ shipped 2026-05-03
- Phase B (IDEA-09: topbar breadcrumb) now unblocked — was waiting for Phase D topbar slot to free
- Phases A→F form a sequential chain; no new blockers detected. A (sidebar restructure) is queued and can start immediately
- Source: `docs/FEATURE_IDEAS_QUEUE.md:257` (Phase B dependency note), confirmed resolved by PM notes from 2026-05-03

**Quick-win batch candidate:**
- IDEA-02 (Copy-task button, ~25 LOC) + IDEA-04 (Duration badge, ~30 LOC) + IDEA-05 (Auto-pause loops, ~20 LOC) = ~75 LOC, zero interdependencies
- All three are feature-complete, have clear acceptance criteria, low test complexity
- Recommendation: batch into single PR to unblock queue attention for Phase A (sidebar) which is higher-risk (~200 LOC)

**Blocking issue — no progress possible:**
- IDEA-2026-04-30-10 (Persist API key) marked needs_human: implementation wants `workspace/.api_key` but `workspace/` is never-touch. Hard blocker until human selects alternate path (e.g., `~/.agent_key` or `$HOME/.config/ai_computer/.api_key`) or confirms rotating keys are acceptable behavior
- No other queued IDEAs depend on this, so queue progression unaffected

**New encoding risk detected:**
- IDEA-2026-05-03-01: `app/text_editor.py:88` — `undo_edit()` calls `p.write_text(old)` with no encoding, uses platform default (cp1252 on Windows). Silent UTF-8 corruption on undo for any non-ASCII file
- 1-LOC fix: add `encoding="utf-8"` to write_text call. Marked queued but suggest picking after quick-wins to avoid context thrash

**No stale findings.**
- All queued IDEAs < 5 days old, queue has no drift (timestamps align with recent PM work)
- Test failures from 2026-05-01 are fully resolved (IDEA-08a through 08f done)

### Implications for Ai_computer

- **Critical path:** UI overhaul (Phases A–F) is now fully unblocked at Phase A; no surprises in the dependency chain. Recommend committing PM to Phase A next cycle to signal that the UI overhaul is underway and sustained focus. Current queue positioning (queued but not in_progress) makes it easy to starve for other work.
- **Short-term momentum:** Quick-wins batch (02, 04, 05) would reduce queue backlog and signal progress. Pairs well with Phase A starting — quick wins complete in ~30 min, clearing mental space for the 200+ LOC sidebar refactor.
- **Encoding regression:** The UTF-8 undo bug is platform-specific (Windows only); suggest testing on Windows after merging to prevent undetected corruption in customer workflows. Mark IDEA-2026-05-03-01 as "test on Windows" in acceptance criteria.

## 2026-05-05 — Codebase patterns

Scanned two random `app/*.py` files end-to-end: `app/providers.py` (1444 lines) and `app/tool_registry.py` (144 lines).

- **Multi-provider fallback chain** (providers.py:689–1035): Primary provider selected by model string. On rate-limit (402, 429, 5xx), cascades to hardcoded OpenRouter free models (gemma-4-31b-it, llama-3.3-70b, nemotron) with exponential backoff (2^attempt seconds), 3 retries per model. Fallback list is static; no alerting if all models sunset.
- **Aggressive JSON repair pipeline** (providers.py:483–527): Direct parse → sanitized parse (strip //, fix trailing commas, quote bare keys) → aggressive repair (escape newlines) → final fallback. Handles common LLM malformations (JS-style comments, missing commas).
- **Mode-specific system prompts** (providers.py:20–144): Coding (no screenshots, filesystem-first rules), computer_use (DOM-based, no pixel coords), default (hierarchical with screenshots). Rules hardcoded per mode; consistent structure but duplicated instructions across modes.
- **Streaming tool call assembly** (providers.py:1155–1320): Accumulates thought buffer and tool JSON across SSE chunks. Detects finish_reason to mark completion. **Assumes single tool_call per response** (line 1297 returns immediately); if OpenRouter emits parallel tool_calls, only first is captured.
- **Win32-specific screenshot capture** (providers.py:373–461): PrintWindow + device contexts. Explicit buffer cleanup (`.copy()` breaks PIL references). Thumbnail to 1280×800, JPEG quality=75. No Linux/macOS equivalent paths.
- **Modular tool packs** (tool_registry.py:60–70): Core (finish, request_permission, memory_recall, todo_write) + mode-specific subsets (filesystem, terminal, browser, computer, web). Deduplication in `get_tool_guidance()` prevents redundant tool visibility.
- **Regex-fragile schema generation** (tool_registry.py:84–122): `_json_schema_from_description()` infers types from description text via regex. Type matching is case-sensitive ("int", "dict", "list" must be lowercase; "Dict" or "List" missed). Current descriptions use lowercase, but no enforcement.

### Implications for Ai_computer

- **JSON return type fragility**: `_extract_json` returns `Any`. Most callers assume dict, but if LLM returns top-level array or plain string, agent crashes. Robust JSON repair, but not type-agnostic. Low immediate risk (LLMs trained for objects), but brittle on outliers.
- **Parallel tool call loss**: `stream_chat_with_tools` returns after first tool_call (line 1297). OpenRouter can emit multiple tool_calls in one SSE chunk (rare). Only first is captured; others lost silently.
- **Key validation deferred to first API call**: Provider keys loaded at `__init__` but never checked for emptiness or syntax. 401/403 errors delay feedback. Invalid key detected only on first task, not on startup.
- **Hardcoded fallback model list without alerts**: If OpenRouter sunsetts gemma-4-31b-it or llama-3.3-70b, the fallback chain silently steps down. No mechanism to alert on full fallback exhaustion or model deprecation.

