# Feature ideas queue (autopilot)

**Discovery job** appends new sections. **Main autopilot job** picks **one** `Status: queued` item when tests are green.

Convention:

- Use a unique `IDEA-...` id per row block.
- Keep each idea **one small PR**; if it is huge, split into multiple IDEA ids.
- Main job updates **Status** to `in_progress` → `done` (or `blocked` with reason).

---

## Template (copy for new ideas)

### [IDEA-YYYY-MM-DD-01] Short title

- **Source app / link:** (optional)
- **Why it fits Ai_computer:**
- **Scope (this PR only):**
- **Acceptance criteria:**
- **Out of scope:**
- **Status:** queued

---

## Queued / history

_(Discovery cron will append below. You can seed items manually.)_

### [IDEA-2026-05-02-07] UI Phase A — Sidebar restructure (move toggles/MCP/Model/Mode to Settings modal)

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase A"). Reference: user's Claude Code UI screenshot — sidebar is pure text rows, no inline toggles, settings live in a separate panel.
- **Why it fits Ai_computer:** Today the sidebar mixes navigation (Sessions) with settings (Expertise toggles, MCP servers, Model dropdown, Mode dropdown) and decorative elements (duplicate brand stamp). Industry pattern (Cursor, Claude Code, VS Code, Codex) is sidebar = nav only; settings live in a modal/panel.
- **Scope (this PR only):** Move out of `static/index.html` sidebar (lines 2458–2528) into a new Settings modal:
  - Brand block (lines 2459–2465) — titlebar already shows "AI Computer"
  - Expertise Library (lines 2484–2489) — `renderSkills()` at line 4517 must wire to modal DOM
  - Active MCP Servers (lines 2490–2495) — `renderMCPServers()` at line 4551
  - Model + Mode dropdowns (lines 2507–2521) — `setMode()` at line 3237
  Keep in sidebar: New session button, Project folder row, Search history input, Sessions list. Add a small `⚙ Settings` button at sidebar bottom that opens the new modal. Pattern after `cmdk-overlay` (line 1898) — do NOT reuse the `tweaks` modal (that's for theme/density). Net change ≤200 LOC.
- **Acceptance criteria:** Sidebar shows only Sessions + project folder + new-session + ⚙ Settings. Clicking ⚙ opens a modal containing Expertise toggles, MCP list, Model + Mode pickers — all functional (toggle on/off still triggers the same handlers). Pytest stays green. UI smoke verifies modal opens/closes via Esc.
- **Out of scope:** Visual styling of the modal beyond pattern-match to cmdk-overlay; drag-to-reorder; per-skill settings.
- **Status:** done (2026-05-15: manual ship — sidebar restructured to pure nav; brand block removed, Expertise/MCP/Model/Mode moved into new Settings modal opened via gear button in sidebar footer; all element IDs preserved so renderSkills/renderMCPServers/setMode untouched)

### [IDEA-2026-05-02-10] UI Phase C1 — Collapse a turn's tool activity into one summary line

- **Source / context:** Direct study of claude.ai on 2026-05-16 (live conversations + a fresh tool-using chat). Claude collapses ALL tool/thinking activity for an assistant turn into ONE muted past-tense line — e.g. `"Retrieved current USD-CAD exchange rate ›"`, `"Synthesized multiple price sources and identified clustering range ›"`. It sits above the answer, collapsed by default: 14px, muted color `rgb(55,55,52)`, `›` chevron, 8px icon gap. The answer text below it is separate and full-contrast (16px, near-black).
- **Why it fits Ai_computer:** AI Computer's feed is a wall of equal-weight `feed-card`s, one per action, each with eyebrow/title/subtitle/state-chip — reads like a debug dump. Claude's model makes tool activity *subordinate* (one quiet line) and the answer *primary*. Single biggest feed-readability change.
- **Scope (this PR only):** In `processTaskEvent()` / `ensureActionCard()` (`static/index.html` ~3566/3883), collect all action/tool/terminal events that occur between two assistant text outputs into ONE summary container. Head = a single line: present tense while the turn streams (`Running command…`, `Searching…`, `Editing files…`); past tense once the turn completes (`Ran 3 commands`, `Edited 2 files`, `Read 4 files`) + `›`. Collapsed by default. For C1, the expanded body may simply stack the existing per-action cards — the icon-gutter step timeline is C2. ~120 LOC.
- **Acceptance criteria:** A task doing 5 tool actions then answering shows ONE collapsed summary line + answer text below (not 5 cards). Line is present-tense live, past-tense after completion. Click expands. Pytest green. UI smoke fires a trivial coding task and verifies one summary line appears.
- **Out of scope:** The icon-gutter step timeline inside the expanded view (IDEA-13). Inline source/citation chips.
- **Status:** done (2026-05-16: added turn-summary container that groups all action/tool events between reasoning events into ONE collapsed summary line; present-tense live ("Ran 2 commands, Edited 1 file…"), past-tense on finalize; click expands to show stacked tool cards; finalizeTurnSummary() called before reasoning/plan/reflection/screenshot/done/error/cancelled/approval/permission events; activeTurnSummary reset in resetTaskView; test_phase_c1_turn_summary_present added to test_ui_static_hardening.py; ~120 LOC)

### [IDEA-2026-05-02-11] UI Phase E — Typography + whitespace pass for tool-aesthetic feel [in_progress]

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase E"). Reference: Claude Code uses generous whitespace, no card borders, hover-only background shifts.
- **Why it fits Ai_computer:** Even after the structural phases, AI Computer's feed-cards have crisp 1px borders and tight padding that feel "demo." Claude Code's pattern is borderless cards with hover bg-shift, taller line-heights for readability.
- **Scope (this PR only):** CSS-only in `static/index.html`:
  - Feed-card vertical padding bumped to ~16px
  - Feed line-height 1.5 → 1.6
  - Replace `border: 1px solid` on `.feed-card` with hover-only `background: var(--bg-2)` shift
  - Strip color from session-list status dots → small text status (`done`, `failed`, `running`)
  - Worker-tag colors reduced from 5 to 1 accent + monochrome neutrals
  ~50 LOC net change. No HTML or JS edits.
- **Acceptance criteria:** Visible whitespace increase in feed (compare screenshots before/after). No regressions in light theme. Pytest green.
- **Out of scope:** Font swap; new color palette; dark/light theme parity audit (separate IDEA-06).
- **Status:** done (2026-05-17: CSS-only pass — .feed-card gets 8px top+bottom padding and hover bg-shift on :not(.is-active); card-subtitle/detail-copy/status-subtitle line-heights bumped from 1.5/1.55 to 1.6; history-dot colored circles replaced with small text labels via ::after (running/done/failed/cancelled); worker-tag theme colors reduced from 5 to 1 accent — workers 2-5 fall back to neutral base style; test_phase_e_typography_whitespace added)

### [IDEA-2026-05-02-12] UI Phase F — Split static/index.html into 3 files (refactor unlocker)

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase F"). Pure refactor. Encountered the override-block-fighting-base-rules problem in commit `3900273` — that issue will recur every UI change until the file is split.
- **Why it fits Ai_computer:** `static/index.html` is 4968 LOC of inline CSS + JS. Hard to navigate, easy to introduce CSS specificity bugs across distant rules. Splitting unlocks every future UI improvement.
- **Scope (this PR only):** Move all `<style>` block content (lines 11–2432) to `static/style.css`. Move all inline `<script>` content (lines 2803–4852) to `static/app.js`. Update `static/index.html` to reference both via `<link rel="stylesheet">` and `<script src="…" defer>`. Confirm `app/main.py` static mount serves both new files. Zero visual change. Net LOC moved ~5000, no new logic.
- **Acceptance criteria:** Page loads identically (visual diff at zero). All JS interactivity works. Pytest green. UI smoke playwright validates: page loads, mode dropdown works, task can be submitted.
- **Out of scope:** Module-splitting JS into ESM; bundler/build step; CSS module/extraction.
- **Status:** queued

### [IDEA-2026-05-02-13] UI Phase C2 — Expandable step timeline inside the tool summary

- **Source / context:** Direct study of claude.ai on 2026-05-16. Expanding a collapsed tool summary (IDEA-10) reveals a **vertical step timeline** with a small monochrome icon gutter on the left. Each step is one line: a 🕐 thinking step (reasoning text), a 🌐 tool/search step (query text + `N results` right-aligned), 🕐 intermediate findings, and a final ⊙ `Done`. Rich tool output (web-search results, file lists) renders as a **nested bordered card** — for search, rows of `favicon + title + domain`; scrollable. Everything in the timeline is quiet/muted; the answer below stays primary.
- **Why it fits Ai_computer:** C1 collapses a turn into one line but its expanded body just stacks old cards. The Claude pattern is a clean step timeline — far more readable for multi-step agent runs, which is exactly AI Computer's core workload (plan → act → reflect).
- **Scope (this PR only):** Replace the C1 expanded body with a step-timeline component in `static/index.html`. Left icon gutter (~20px), one row per step. Map AI Computer event types to step types: reasoning/`intent` → thinking step; `action_start`+`tool_call` → tool step (show args summary + result count/state); `terminal_output` → nested output card; final state → `Done`/`Failed` row. Reuse `processTaskEvent()` data already captured by C1. The timeline expands **inline** in the feed (not a modal). Each rich-output block (terminal output, file lists, search/tool results) is a **height-capped, internally-scrollable card** — `var(--bg-2)`, 8px radius, `max-height` ~240–280px, `overflow:auto`. This is the key detail observed on claude.ai: a 10-row search result or a 500-line terminal dump shows ~4–5 rows and you scroll *inside the box* — the feed itself never balloons. ~180 LOC. Highest-risk UI phase — touches event routing + new component.
- **Acceptance criteria:** Expanding a multi-step task's summary shows an icon-gutter timeline: thinking rows, tool rows with arg summaries, a final Done/Failed row. A long `terminal_output` (>40 lines) renders inside a height-capped card with its own scrollbar — the feed scroll position is unaffected by expanding it. Collapsed state unchanged (IDEA-10). Pytest green. UI smoke covers a task with ≥3 mixed steps including a long terminal dump.
- **Out of scope:** Animated step-by-step reveal during streaming (C1 already handles the live present-tense line); inline source chips; per-step copy buttons.
- **Status:** queued (depends on Phase C1 — IDEA-10 — shipping first)

### [IDEA-2026-04-29-01] Persist last-used mode across reloads

- **Why it fits Ai_computer:** Users reload the UI often during long agent runs; losing the selected mode (Coding/Browser/Desktop) is friction.
- **Scope (this PR only):** Save selected mode to localStorage on change; restore on page load. Fall back to auto-detect if absent.
- **Acceptance criteria:** Refresh the page after picking Browser mode → Browser is still selected. Unit/UI smoke test covers the round-trip.
- **Out of scope:** Persisting other UI state (model, API key prefs).
- **Status:** done (2026-05-05: localStorage.setItem on mode change; getItem + select restore in init(); test_mode_selection_is_persisted_client_side added)

### [IDEA-2026-04-29-02] Copy-task button on completed runs

- **Why it fits Ai_computer:** Re-running or tweaking a previous task is currently retype-from-memory; a one-click copy speeds iteration.
- **Scope (this PR only):** Add a small "↻ Copy task" button on each finished run card that fills the input box with the original goal text.
- **Acceptance criteria:** Button appears only on terminal-state runs; click populates input and focuses it. Playwright smoke test added.
- **Out of scope:** Editing/forking mid-run, history search.
- **Status:** done (2026-05-13: added .history-retask CSS + history-item.terminal class; renderHistoryItem adds terminal class for done/failed/cancelled status; ↻ Copy task button fills #input and focuses it on click; stopPropagation prevents loading task log; test_copy_task_button_present_and_wired added)

### [IDEA-2026-04-29-03] /healthz endpoint with provider checks

- **Why it fits Ai_computer:** Currently no quick way to verify which LLM providers are reachable / keys valid before kicking off a run.
- **Scope (this PR only):** Add `GET /healthz` that returns `{server: ok, providers: {openrouter: ok|missing_key|unreachable, ...}}`. Cache results for 30s.
- **Acceptance criteria:** Hitting /healthz with a missing key returns `missing_key` for that provider; with a valid key returns `ok`. Test added with mocked HTTP.
- **Out of scope:** Surfacing this in the UI (separate idea).
- **Status:** done (2026-05-02: implemented in app/main.py with 30s cache; 3 tests added in tests/test_healthz.py)

### [IDEA-2026-04-29-04] Run duration + token-cost badge on each run card

- **Why it fits Ai_computer:** Helpful to see at a glance how long a run took and roughly what it cost; encourages tighter prompts.
- **Scope (this PR only):** Compute wall-clock duration and approximate cost (token counts × model price table) and render as a small badge on each run card.
- **Acceptance criteria:** Free-tier OpenRouter runs show "$0.00 · 23s"; paid models show estimated cost. Unit test on the cost calculator.
- **Out of scope:** Aggregate analytics, daily/weekly reports.
- **Status:** queued

### [IDEA-2026-04-29-05] Auto-pause on repeated identical tool calls

- **Why it fits Ai_computer:** Agents sometimes loop calling the same tool with the same args; today the only escape is manual Pause.
- **Scope (this PR only):** Detect ≥3 identical consecutive tool calls (same name + args hash) and auto-pause with a banner explaining why.
- **Acceptance criteria:** Synthetic test feeding 3 duplicates triggers pause; 2 does not. Banner visible in UI smoke.
- **Out of scope:** Smarter cycle detection across non-adjacent calls.
- **Status:** queued

### [IDEA-2026-04-29-06] Fix memory.search returning strings instead of objects with .content

- **Source app / link:** `app/agent.py:629`, `tests/test_agent.py::test_delegate_parser`
- **Resolution:** Made lines 629/633 tolerant of both objects-with-.content and plain strings via `getattr(m, 'content', m)`. Test green.
- **Status:** done

### [IDEA-2026-04-29-07] Fix LogEmitter async disk-write race in test_persistent_logs_omit_raw_screenshot_payload

- **Source app / link:** `app/log_emitter.py:165`, `tests/test_computer_control_regressions.py::test_persistent_logs_omit_raw_screenshot_payload`
- **Why it fits Ai_computer:** The test fails because `emit()` submits disk writes to a `ThreadPoolExecutor` background thread, then `read_log()` is called synchronously before the write completes — an inherent race condition. Persistent log reads return 0 events instead of 1.
- **Scope (this PR only):** Add a `flush()` method to `LogEmitter` that drains all pending writes by submitting a sentinel task to the executor and calling `.result()` on it. Update `test_persistent_logs_omit_raw_screenshot_payload` to call `emitter.flush()` between `emit()` and `read_log()`. No other tests or callers change.
- **Acceptance criteria:** `pytest tests/test_computer_control_regressions.py::test_persistent_logs_omit_raw_screenshot_payload` passes. Full suite green.
- **Out of scope:** Changing the async write design for production paths; adding flush calls outside the test.
- **Status:** done

### [IDEA-2026-04-30-08] Master ticket: 12 pre-existing failures (split into 08a–08f below)

- **Source:** First full non-`-x` suite run (2026-04-30) surfaced 12 pre-existing failures hidden by `-x`.
- **Status:** split (do not work this directly — pick a sub-ticket)

### [IDEA-2026-04-30-08a] Fix LogEmitter seek-replay race in test_project_folder_runtime

- **Source app / link:** `tests/test_project_folder_runtime.py::test_log_emitter_seek_replay_uses_binary_offsets_for_utf8`
- **Resolution:** Added `emitter.flush()` before `read_log()` in the test. Test now passes.
- **Status:** done

### [IDEA-2026-04-30-08b] Fix auth 401 failures in test_security

- **Source app / link:** `tests/test_security.py:33,60,76`
- **Resolution:** Root cause was `load_dotenv(override=True)` in `main.py:3` clobbering the monkeypatched `AGENT_API_KEY` during `importlib.reload()`. Fix: `monkeypatch.setattr(m, "API_KEY", "token123")` after reload in `_client()`. All 7 security tests pass.
- **Status:** done

### [IDEA-2026-04-30-08c] Fix hierarchical/memory `.content` AttributeErrors

- **Source app / link:** `tests/test_hierarchical.py:23,44,70`
- **Partial resolution (line 70 — test_phase_updates_emit_progress):** Test mocked `asyncio.sleep` to instant, so real elapsed time never reached the 1s heartbeat threshold. Fix: pass `heartbeat_seconds=0` to `_run_with_phase_updates` in the test. Test now passes.
- **Remaining (lines 23,44 — test_hierarchical_success, test_hierarchical_retry):** Root cause is that tests check `s.memory.search("task_outcome")` expecting items with `"Outcome: True"`, but production code never stores this. `summarize_session()` stores `session_summary` with "Completed successfully". Expected behavior was never implemented. Needs human.
- **Status:** done (2026-05-02: added mode="computer" + _capture_screenshot_b64 mock + task_outcome memory storage in agent.py; all 3 assertions now pass)

### [IDEA-2026-04-30-08d] Fix fast-path routing `call_llm_called` assertion failures

- **Source app / link:** `tests/test_fast_path.py:49,88`
- **Resolution:** Tests called `run_task()` without `mode="computer"`, so the hierarchical/fast-path block (`if mode in ("computer", "computer_isolated")`) was never entered. Fix: added `mode="computer"` and mocked `_capture_screenshot_b64` in both tests. Both tests now pass.
- **Status:** done

### [IDEA-2026-04-30-08e] Fix JPEG magic-byte / vision-loop screenshot decoding

- **Source app / link:** `tests/test_vision_loop.py:28`, `tests/test_visual_verification.py:20`
- **Resolution (test_vision_loop):** `_capture_screenshot_b64` returns a data URL (`"data:image/jpeg;base64,..."`) but the test was calling `base64.b64decode()` on the full data URL string, getting garbage bytes. Fix: strip prefix with `.split(",", 1)[1]` before decoding. Test passes.
- **Resolution (test_visual_verification):** Same root cause as IDEA-08c — `memory.search("task_outcome")` returns empty; `"Outcome: True"` is never stored. Needs human intervention.
- **Status:** done (vision_loop fixed 2026-05-01; visual_verification fixed 2026-05-02 via same approach as 08c)

### [IDEA-2026-04-30-08f] Fix remaining project-folder-runtime failures

- **Source app / link:** `tests/test_project_folder_runtime.py:44,86`
- **Resolution:** Same auth issue as IDEA-08b — `load_dotenv(override=True)` clobbered monkeypatched key. Fixed by `monkeypatch.setattr(m, "API_KEY", "token123")` in the project_folder_runtime `_client()` fixture. Both tests pass.
- **Status:** done

### [IDEA-2026-04-30-09] Self-host mermaid.js (remove jsdelivr CDN)

- **Source:** `static/index.html:10` — `<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js">`
- **Why it fits Ai_computer:** Hard CDN dependency means the UI breaks on any offline / firewalled / air-gapped run. The whole product premise (run anywhere) is undermined by one external script tag.
- **Scope (this PR only):** Vendor `mermaid@10.9.1/dist/mermaid.min.js` into `static/vendor/mermaid.min.js`, update the script tag, add the file to git. ~2 LOC change in HTML + one vendored JS file (~3 MB but it's static).
- **Acceptance criteria:** `static/index.html` has no `cdn.jsdelivr.net` references. UI loads with internet disconnected. UI smoke test still passes.
- **Out of scope:** Vendoring other CDN assets (Google Fonts) — separate IDEA if needed.
- **Status:** queued

### [IDEA-2026-04-30-10] Persist AGENT_API_KEY across server restarts

- **Source:** `app/main.py:21` — `API_KEY = os.environ.get("AGENT_API_KEY") or secrets.token_hex(32)`
- **Why it fits Ai_computer:** When `AGENT_API_KEY` is unset, every restart generates a new key, silently invalidating any existing CLI/integration that stored the previous one. Users get unexplained 401s after a routine reboot.
- **Scope (this PR only):** On startup, if `AGENT_API_KEY` env var is unset, check for `workspace/.api_key` file. Use it if present; otherwise generate, write to that file (mode 600), use it. Log the file path on first generation. ~15 LOC.
- **Acceptance criteria:** Restart server with no env var → same API key as previous run. Setting the env var still wins. New unit test covers both paths.
- **Out of scope:** Key rotation, multi-key support.
- **Status:** done (2026-05-10: implemented _load_or_create_api_key() in app/main.py; checks AGENT_API_KEY env var first, then ~/.config/ai_computer/.api_key honoring XDG_CONFIG_HOME, then generates+saves new key with mode 600; 3 tests in test_healthz.py)

### [IDEA-2026-04-30-11] Streaming token + cost counter in UI

- **Source:** `static/index.html` (run cards), `app/agent.py` SSE event emission
- **Why it fits Ai_computer:** Today users can't see how many tokens a run consumed or what it cost until after — Cursor/Aider/OpenHands all show this live. Encourages tighter prompts and helps users stay within free-tier limits.
- **Scope (this PR only):** Emit a new SSE event `usage_update` with `{prompt_tokens, completion_tokens, cost_usd}` after each LLM call. Render a small live-updating badge in the run card ("12.4k tok · $0.03"). Cost = sum across calls; use a hardcoded provider price table in `app/providers.py`.
- **Acceptance criteria:** Free-tier OpenRouter run shows `$0.00 · 23s`; paid model shows nonzero. Unit test on the cost calculator. UI smoke verifies badge updates mid-run.
- **Out of scope:** Aggregate dashboard, daily/weekly cost rollups, exporting usage data.
- **Status:** queued

### [IDEA-2026-04-30-12] Cache /api/mcp instead of re-initializing on every GET

- **Source:** `app/main.py:432` — `await mcp_manager.initialize_default_servers(...)` runs on every GET
- **Why it fits Ai_computer:** Re-initializing MCP servers on every UI poll is wasteful (the UI may poll `/api/mcp` periodically). `initialize_default_servers` is presumably idempotent but still does work each time.
- **Scope (this PR only):** Remove the re-init call from the GET handler — rely on the lifespan-startup init. If the manager isn't ready yet, return `{"servers": [], "initializing": true}` so the UI can retry. ~5 LOC.
- **Acceptance criteria:** `GET /api/mcp` returns in <50ms after startup. Existing test for `/api/mcp` still passes; new test asserts no re-init happens on repeated GETs.
- **Out of scope:** Changing how `mcp_manager` itself initializes.
- **Status:** done (2026-05-03: removed re-init call from GET handler; returns {servers:[], initializing:true} when not ready; 2 tests added to test_healthz.py)

### [IDEA-2026-05-01-01] Limit TextEditorTool undo history to prevent unbounded memory growth

- **Source app / link:** `app/text_editor.py:49,67` — `str_replace`/`insert` store entire pre-edit file text in `self._history` per path with no bounds
- **Why it fits Ai_computer:** Text editor tool is used for file modifications; on large files or long editing sessions, `self._history` can grow unbounded (stores full file text for every edit). This wastes memory and has no practical limit.
- **Scope (this PR only):** Add a max history limit (e.g., 50 or 100 entries total across all files, or per-file cap of ~10 undo levels). Trim oldest history when limit exceeded. ~10–15 LOC in `text_editor.py`.
- **Acceptance criteria:** After exceeding the limit, oldest history entries are dropped; `undo_edit` still works for recent edits. Unit test verifies cap is enforced. No change to external API or behavior for within-limit cases.
- **Out of scope:** Changing undo semantics, adding redo support, or persisting history across restarts.
- **Status:** done (2026-05-03: added _HISTORY_CAP=10; str_replace and insert trim oldest entry when len > cap; test_history_cap_enforced added to test_text_editor.py)

### [IDEA-2026-05-02-01] Surface /healthz provider status in the UI header

- **Source app / link:** `app/main.py` `/healthz` endpoint (IDEA-2026-04-29-03, now done); `static/index.html` header area
- **Why it fits Ai_computer:** The `/healthz` endpoint now returns which providers have keys configured. The UI currently gives no indication — users discover missing keys only when a run fails mid-stream with a 401.
- **Scope (this PR only):** On page load, call `/healthz` and render a compact provider-chip bar below the mode selector: green dot for `ok`, grey for `missing_key`. Auto-refreshes every 60s. Pure JS, no new backend changes. ~25–35 LOC in index.html.
- **Acceptance criteria:** Page load shows coloured provider chips. Grey chip visible when OPENROUTER_API_KEY is unset. No regression on existing Playwright smoke test.
- **Out of scope:** Alert banners, tooltip explanations, keyboard accessibility.
- **Status:** done (2026-05-04: added .provider-chip CSS, #provider-chips div in sidebar footer below mode selector, refreshProviderChips() polls /healthz on load and every 60s; ok=green dot, missing_key=grey)

### [IDEA-2026-05-02-02] Auto-resize composer textarea (grow with content, max 8 lines)

- **Source app / link:** `static/index.html:2674` — `<textarea id="input">` is fixed-height
- **Why it fits Ai_computer:** Every modern agent UI (Cursor, Aider, ChatGPT, Claude.ai) auto-grows the input as the user types and shrinks back when cleared. Currently you have to scroll inside a tiny box for multi-line goals. Big ergonomics win, low risk.
- **Scope (this PR only):** Add an `input` event listener on `#input` that sets `style.height = 'auto'` then `style.height = el.scrollHeight + 'px'`, capped at 8 line-heights. Reset on send. ~15 LOC inline JS + 3 LOC CSS for `min-height` and `max-height`. No HTML structure change.
- **Acceptance criteria:** Typing 5 lines shows all 5 lines without inner scrollbar. Typing 20 lines hits the 8-line cap and starts inner-scrolling. Sending the task resets to single-line height. No regression in existing keyboard shortcuts.
- **Out of scope:** Markdown preview, syntax highlighting, file attachment.
- **Status:** done (2026-05-03: already implemented — autoGrow() at index.html:3824 uses scrollHeight capped at 180px; min-height:48px max-height:180px in CSS; wired to input event at line 4643)

### [IDEA-2026-05-02-03] Toast confirmation when copying log to clipboard

- **Source app / link:** `static/index.html:4345` — `navigator.clipboard.writeText(...)` runs silently
- **Why it fits Ai_computer:** The toast system already exists (line 2109). Copy-to-clipboard currently provides zero feedback — users click and have no idea if it worked, often clicking again and overwriting their own clipboard accidentally.
- **Scope (this PR only):** After the existing `navigator.clipboard.writeText` call succeeds, dispatch an info-level toast: "Log copied (N events)." Wrap in try/catch so a clipboard failure shows an error toast instead. ~8 LOC. Reuse existing `showToast(msg, type)` helper.
- **Acceptance criteria:** Clicking the copy-log button shows a green checkmark toast with the event count. Disabling clipboard permissions in devtools and clicking shows an error toast. Existing test for log download (if any) still passes.
- **Out of scope:** Adding copy buttons elsewhere; refactoring the toast system.
- **Status:** done (2026-05-03: already implemented — `copyCurrentLog()` at index.html:4261 wraps clipboard write in try/catch with `toast('Copied log to clipboard.', 'ok')` on success and error toast on failure)

### [IDEA-2026-05-02-04] Empty state for run history list

- **Source app / link:** `static/index.html:2578` — `<input id="history-search">` exists but no visible empty state when zero tasks have run
- **Why it fits Ai_computer:** First-time users (or after `_tasks` eviction lands in IDEA-13) see a search bar pointing at nothing. Industry standard is a friendly empty state — Aider, Continue, OpenHands all do this. Reduces "is it broken?" confusion.
- **Scope (this PR only):** When the history list is empty, render a small placeholder block: muted text "No tasks yet — describe one above to get started" + a subtle icon (use existing brand-mark or an inline SVG). Hides the moment a task is added. ~20 LOC HTML + CSS. No JS state changes; just toggle visibility based on existing list-empty check.
- **Acceptance criteria:** Fresh page load with no tasks shows the empty state. Submitting a task hides it. Light + dark theme both render readably.
- **Out of scope:** Onboarding tour, persistent dismissal, illustrated graphics.
- **Status:** done (2026-05-03: already implemented — .history-empty div at index.html:2480 shows placeholder text; addActiveHistoryItem() removes it when first task runs; CSS at line 549)

### [IDEA-2026-05-02-05] Keyboard shortcut help overlay (`?` to open)

- **Source app / link:** `static/index.html:4760` — many shortcuts exist (Ctrl+K, Enter, Esc, Space) but no discovery surface
- **Why it fits Ai_computer:** README documents 5 shortcuts; the UI shows none of them centrally. The `mini-chip` items in the composer hint at 2 (`↵`, `⇧↵`) but the rest are invisible. Power users in similar tools (Linear, GitHub, Notion) all use `?` to open a shortcut cheatsheet.
- **Scope (this PR only):** Add a `?` global keydown handler (skip if focus is in input/textarea) that opens an existing-pattern modal listing all current shortcuts. Reuse the cmdk-overlay or tweaks-modal styling for consistency. Static HTML — no backend, no state. ~50 LOC HTML + 10 LOC JS. Press `?` or `Esc` to close.
- **Acceptance criteria:** Pressing `?` outside an input opens the overlay. Pressing `?` while typing in the composer inserts a literal `?` (no overlay). All 5 shortcuts from README are listed with their keybindings.
- **Out of scope:** Customizing shortcuts, recording new ones, exporting to PDF.
- **Status:** queued

### [IDEA-2026-05-02-06] Light theme audit pass

- **Source app / link:** `static/index.html:97` light-theme block; comparison vs dark-theme block at line 56
- **Why it fits Ai_computer:** Dark theme has ~2× the per-component overrides as light (greppable: `[data-theme='dark']` appears 40+ times, `[data-theme='light']` appears ~25). The light theme is likely visually janky in spots — low contrast on muted text, wrong accent shade on hover, etc. No bug reports because nobody uses light. But a quick audit + fixes would make the toggle worth using.
- **Scope (this PR only):** Run a light-theme manual audit: load each major surface (sidebar, run card, command palette, tweaks modal, composer, action log, history list) with `data-theme='light'` and screenshot. File any visual issue ≥ minor as bullet points in this IDEA's resolution. Then fix the top 3 issues found, capped at 50 LOC of CSS-only changes. No new design system work.
- **Acceptance criteria:** Resolution section enumerates ≥3 specific fixes (file:line). Light theme has no missing-text or unreadable-contrast surfaces. Dark theme unchanged.
- **Out of scope:** Adding new themes, redesigning the palette, system-theme auto-detection.
- **Status:** queued

### [IDEA-2026-05-02-08] UI Phase D — Drop the READY pill from topbar

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase D"). Reference: Claude Code has no decorative "READY" pill anywhere.
- **Why it fits Ai_computer:** The `#status-pill` (lines 2541–2544) reads as "AI demo screenshot" decoration. Status already mirrors to `#sb-status` in the bottom statusbar — the topbar pill is redundant.
- **Scope (this PR only):** Remove `#status-pill` HTML (lines 2541–2544 in `static/index.html`). In `setStatus()` (line 3183–3191), remove the `#status-pill` write but keep the `#sb-status` write. Confirmed no JS reads from the pill DOM. ~25 LOC net change.
- **Acceptance criteria:** No pill visible top-right. Bottom statusbar still updates as task transitions through ready → running → complete. UI smoke playwright snapshot of running task verifies no regression.
- **Out of scope:** Adding a topbar status indicator (Phase B handles that as a small dot beside the breadcrumb).
- **Status:** done (2026-05-03: removed #status-pill HTML, agentPulse keyframe, .pill.status-* CSS rules, and JS pill write from setStatus(); sb-status statusbar write unchanged)

### [IDEA-2026-05-02-09] UI Phase B — Topbar breadcrumb with task goal · mode · model

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase B"). Reference: Claude Code top bar shows `Ai_computer / Create automated daily project improvement routine`.
- **Why it fits Ai_computer:** Today the topbar shows static `<h2>Stream</h2>` (line 2531). Useless when idle, no context when running. Breadcrumb pattern matches Claude Code, Cursor, Codex.
- **Scope (this PR only):** Replace `<h2 id="task-title">Stream</h2>` with a flex-row breadcrumb structure showing project folder name when idle, and `<truncated-goal> · <mode> · <model>` with subtle separator dots when a task is running. Extend `setTaskTitle()` (line 3279) signature from `(title) => …` to `(title, ctx) => …` where `ctx = { mode, model, status }`. Add a small status dot beside the breadcrumb (replaces the dropped pill from Phase D — IDEA-08). All call-sites of `setTaskTitle` (lines 4169, 4287, 4334) updated. ~50 LOC.
- **Acceptance criteria:** Idle state: topbar shows project folder name (or empty). Running state: shows truncated goal + mode label + model name with `·` separators + small status dot. Pytest green. UI smoke verifies the breadcrumb updates on task start.
- **Out of scope:** Clickable breadcrumb segments; multi-task tabs.
- **Status:** done (2026-05-04: added topbar-row flex container with status dot + ctx span; extended setTaskTitle(title, ctx) to render mode·model context; setStatus() syncs dot color; all 5 call sites updated)

### [IDEA-2026-05-03-01] Fix undo_edit missing encoding — silent UTF-8 corruption on Windows

- **Source app / link:** `app/text_editor.py:88` — `p.write_text(old)` has no `encoding` argument
- **Why it fits Ai_computer:** `str_replace` and `insert` both read files with `encoding="utf-8"` and store the text in history. `undo_edit` writes back with no encoding, using the platform default (cp1252 on Windows). Any file with non-ASCII content will be silently corrupted after undo on Windows.
- **Scope (this PR only):** Add `encoding="utf-8"` to `p.write_text(old)` at `text_editor.py:88`. 1 LOC.
- **Acceptance criteria:** `test_undo_preserves_utf8` creates a file with non-ASCII content, calls `str_replace`, then `undo_edit`, and asserts the restored content matches the original byte-for-byte. Test passes on all platforms.
- **Out of scope:** Encoding detection for non-UTF-8 source files.
- **Status:** done (2026-05-04: added encoding="utf-8" to p.write_text in undo_edit; test_undo_preserves_utf8 added to test_text_editor.py)

### [IDEA-2026-05-04-01] Restore mode+model breadcrumb when replaying a past task

- **Source app / link:** `static/index.html:4286` — `setTaskTitle(title)` called without ctx during task replay; `static/index.html:3884` — `task_created` event silently ignored in processTaskEvent
- **Why it fits Ai_computer:** The `task_created` SSE event already carries `mode` and `model` (emitted from `app/main.py:730`). When replaying a past task the topbar shows the title but the mode/model breadcrumb context (added in Phase B) stays blank. A user replaying an old run can't tell what mode or model was used for it.
- **Scope (this PR only):** In `loadTask()` (line ~4285), extract `mode` and `model` from the `task_created` event and pass as ctx to `setTaskTitle(title, { mode, model })`. No backend changes. ~5 LOC.
- **Acceptance criteria:** Clicking a past task in history shows the correct mode and model in the topbar breadcrumb. Pytest stays green. UI smoke verifies topbar ctx is populated after loading a history item.
- **Out of scope:** Showing mode/model in the history list item itself; real-time mode tracking during live stream.
- **Status:** done (2026-05-05: extracted createdEvent from loadTaskLog; passed mode+model as ctx to setTaskTitle — topbar breadcrumb now populated on task replay)

### [IDEA-2026-05-05-01] Handle multiple parallel tool calls in streaming response

- **Source:** `app/providers.py:1297` — `stream_chat_with_tools()` returns immediately after first tool_call
- **Why it fits Ai_computer:** OpenRouter's SSE streaming can emit multiple tool_calls within a single response chunk. Currently the method collects the first tool_call and returns (line 1297), silently dropping any additional tool_calls in that response. This limits agent autonomy when a task legitimately requires 2+ parallel actions (e.g., "fetch API AND read local file in parallel").
- **Scope (this PR only):** Modify `stream_chat_with_tools()` to collect ALL tool_calls from the response into a list instead of returning after the first one. Emit each as a separate `{"type": "tool_call", "name": ..., "args": ...}` dict. Caller loops to handle multiple tool calls. ~25 LOC change in the accumulation and emission logic (lines 1232–1320).
- **Acceptance criteria:** A mock OpenRouter response containing 2 sequential tool_calls in one SSE chunk yields both tool_calls to the caller in order. Existing single-tool_call responses still work (backward compatible). Test: `test_stream_chat_with_tools_multiple_calls` creates a payload with two tool_calls and asserts both are emitted.
- **Out of scope:** Executor-side changes to handle parallel execution. This IDEA only ensures the streaming layer doesn't drop tool_calls.
- **Status:** done (2026-05-06: replaced single-variable accumulators with dict keyed by tool_call index; all indices emitted in order on finish; test_providers.py added with single-call and multi-call coverage)

### [IDEA-2026-05-05-02] Guard _extract_json against non-dict top-level return

- **Source:** `app/providers.py:483–527` — `_extract_json()` returns `Any`; all callers assume a dict
- **Why it fits Ai_computer:** The JSON repair pipeline handles common LLM malformations, but if an LLM returns a top-level JSON array or plain string, `_extract_json` propagates that to callers that index into it as a dict, causing a `TypeError` or `AttributeError` and silently aborting the agent loop. Defensive wrapping costs ~5 LOC and prevents a hard-to-debug crash.
- **Scope (this PR only):** After the final fallback parse in `_extract_json`, add a check: if the result is not a `dict`, wrap it as `{"result": result}` (or return `{}`). ~5–10 LOC in `app/providers.py`. Add one unit test asserting that an array-returning mock produces a dict.
- **Acceptance criteria:** `_extract_json('[1,2,3]')` returns a dict (not a raw list). `_extract_json('{}')` and `_extract_json('{"key":"val"}')` are unaffected. Unit test added.
- **Out of scope:** Caller-side type narrowing; refactoring the repair pipeline.
- **Status:** done (2026-05-06: added _ensure_dict() helper inside _extract_json; all return paths wrap non-dict results as {"result": val}; 4 unit tests added to test_models.py)

### [IDEA-2026-05-06-01] Await MCP initialization at startup to close race window

- **Source:** `app/main.py:32–56` — `_lifespan` fires `_init_mcp()` via `asyncio.create_task()` without awaiting; yields immediately
- **Why it fits Ai_computer:** First client requests may arrive before MCP server init completes, hitting the `initializing:true` response path and adding latency. While mitigated by UI retries, the race is unnecessary — startup is already blocking for other reasons (lifespan events are sequential in practice). Deterministic init improves startup UX.
- **Scope (this PR only):** Replace `asyncio.create_task(_init_mcp)` with `await asyncio.gather(_init_mcp(), ...)` or `await _init_mcp()` directly (keep telegram/discord as fire-and-forget if they have their own timeout resilience). ~2 LOC change in `app/main.py:51`.
- **Acceptance criteria:** MCP manager is fully initialized before lifespan yields. First GET `/api/mcp` after server startup returns `initializing:false` or omits the field (indicates ready). Test: start server, immediately hit `/api/mcp`, assert no `initializing:true` in response.
- **Out of scope:** Lazy MCP initialization, making MCP optional.
- **Status:** done (2026-05-08: changed asyncio.create_task(_init_mcp()) to await _init_mcp() in app/main.py; test_mcp_init_awaited_before_lifespan_yields added to test_healthz.py)

### [IDEA-2026-05-06-02] Cap SSE subscriber queue depth to prevent unbounded memory growth

- **Source:** `app/log_emitter.py` — `subscribe(task_id)` returns an `asyncio.Queue` with no `maxsize`; `app/main.py:896` pops events with `await q.get()`
- **Why it fits Ai_computer:** A slow or stalled SSE client (network hiccup, devtools open) leaves its queue draining at zero throughput while the agent keeps emitting events. With no bound, a long-running agent + stalled client silently consumes unbounded memory. Adding a high-water mark (e.g. `maxsize=500`) causes `put_nowait` to raise `asyncio.QueueFull` instead of growing forever, which the SSE handler can catch and disconnect the client.
- **Scope (this PR only):** In `log_emitter.subscribe()`, change `asyncio.Queue()` to `asyncio.Queue(maxsize=500)`. In the SSE emit path (`log_emitter.emit` or equivalent), catch `asyncio.QueueFull` and log a warning with the task ID. In the SSE handler (`app/main.py` stream endpoint), detect the disconnected state and break out of the stream loop. ~10–15 LOC total.
- **Acceptance criteria:** A test fills the queue past 500 events without a consumer and verifies `QueueFull` is raised rather than growing indefinitely. Existing SSE tests still pass.
- **Out of scope:** Per-subscriber configurable limits; back-pressure signaling to the agent.
- **Status:** done (2026-05-08: pre-implemented — subscribe() already used maxsize=200, emit() already caught QueueFull with a warning log; test_sse_subscriber_queue_is_bounded added to test_computer_control_regressions.py)


### [IDEA-2026-05-08-01] Expose active task list API endpoint for real-time task visibility

- **Source:** OpenHands Task List tab (May 2026 update) — shows agent's current task list with status updates
- **Why it fits Ai_computer:** Competitors (OpenHands, Devin) expose real-time agent task breakdowns in the UI. AI_computer tracks `_active_tasks` dict (app/agent.py:338) but exposes no endpoint for it. Users have no visibility into what the agent is currently working on — only historical task list. Adding `/api/active-tasks` endpoint would let the UI show a "Current Tasks" panel matching competitor parity and improving real-time visibility.
- **Scope (this PR only):** Add `/api/active-tasks` GET endpoint in `app/main.py` that returns `{ tasks: [{ task_id: str, status: str, created_at: str, last_updated: str }, ...] }` by iterating `_active_tasks` dict. ~15–20 LOC in main.py. No changes to agent loop or SSE.
- **Acceptance criteria:** GET `/api/active-tasks` returns a list of task objects with task_id, status, timestamps. When a task is created, it appears in the list. When task completes, it's removed. Smoke test: create a task and verify it appears in `/api/active-tasks` before finishing.
- **Out of scope:** UI panel to display the task list; real-time push of task updates (use polling via endpoint); filtering or sorting tasks.
- **Status:** done (2026-05-10: added GET /api/active-tasks in app/main.py; filters _tasks dict by non-terminal status; returns task_id, status, goal, mode, model, created_at; 2 tests in test_healthz.py)

### [IDEA-2026-05-08-02] Store telegram/discord asyncio.Task refs to prevent silent GC cancellation

- **Source:** `app/main.py:52–53` — `asyncio.create_task(start_telegram(...))` and `asyncio.create_task(start_discord(...))` return Task objects that are immediately discarded
- **Why it fits Ai_computer:** Python's asyncio GC can silently cancel a Task if its only reference is dropped. CPython doesn't GC immediately (refcount), but PyPy and some edge cases can kill the task mid-run without any exception visible to the server. Storing the task refs in module-level variables costs 2 LOC and eliminates the risk. Also enables clean shutdown: cancelling the stored tasks in the lifespan shutdown block instead of letting them leak.
- **Scope (this PR only):** Store the two `create_task()` results in module-level `_telegram_task` and `_discord_task` variables in `app/main.py`. In the lifespan shutdown block (after `yield`), call `.cancel()` on each and await their cancellation. ~8 LOC.
- **Acceptance criteria:** `_telegram_task` and `_discord_task` are set at startup. Lifespan teardown cancels them. Existing tests pass. No regression on server shutdown (uvicorn still exits cleanly).
- **Out of scope:** Restarting failed integrations; monitoring integration health.
- **Status:** done (2026-05-10: added _telegram_task/_discord_task module vars; stored create_task refs; lifespan shutdown block cancels+awaits both; test_lifespan_stores_and_cancels_integration_tasks added to test_healthz.py)


### [IDEA-2026-05-10-01] Make SSE keepalive timeout configurable for slow networks

- **Source:** `app/main.py:902` — `asyncio.wait_for(q.get(), timeout=30.0)` hardcoded
- **Why it fits Ai_computer:** The 30-second keepalive timeout is safe for fast networks but risky on metered/mobile connections where events may be sparse (e.g., a long-running task with infrequent log emissions). Clients on slow networks risk connection timeout mid-stream. Exposing a configurable `?keepalive_timeout_seconds=N` parameter allows fine-tuning per use case (mobile: 60-90s, local: 30s, fast: 15s) without code changes.
- **Scope (this PR only):** Add `keepalive_timeout_seconds` optional query param to `/api/tasks/{task_id}/stream` endpoint. Validate: min 5s, max 300s (prevent abuse), default 30s. Update `event_generator()` to use the param instead of hardcoded 30.0. ~15 LOC in main.py:871-914.
- **Acceptance criteria:** GET `/api/tasks/{task_id}/stream?keepalive_timeout_seconds=60` uses 60s timeout. Invalid values (e.g., 2, 400) reject with 400 error. Default (no param) remains 30s. Existing SSE smoke tests pass with default timeout.
- **Out of scope:** Adaptive timeout (calculate based on historical event frequency); metrics/monitoring for timeout events.
- **Status:** done (2026-05-10: added keepalive_timeout_seconds query param (default=30, min=5, max=300) to stream_task; raises 400 on out-of-range values; 2 tests in test_healthz.py)


### [IDEA-2026-05-10-02] Log fallback model selection at INFO level for reproducibility

- **Source:** `app/providers.py:843` — `_chat_openrouter` silently retries next model on 402/429 with no INFO-level audit trail; users running same prompt twice may get different models
- **Why it fits Ai_computer:** When the primary model rate-limits and the fallback chain activates, users see no indication which model actually served their request. This makes debugging and cost tracking impossible. Competitors (Aider) expose model selection in the UI. Adding an INFO log line costs 1 LOC and a new SSE event `provider_info` (type, model, fallback: true) costs ~10 LOC.
- **Scope (this PR only):** In `_chat_openrouter` (app/providers.py:843), add `_log.info("Fallback activated: switched to %s", model_name)` on each retry. Emit a `{"type": "provider_info", "model": model_name, "fallback": True}` item to the caller's stream after fallback activates. ~10 LOC. No UI changes.
- **Acceptance criteria:** Server logs show which fallback model was selected. `provider_info` event visible in task SSE stream when fallback fires. Existing tests pass.
- **Out of scope:** UI badge showing model name mid-run; persistent per-task model audit log.
- **Status:** done (2026-05-13: added logging import + _log to providers.py; _chat_openrouter logs INFO on fallback; stream_chat_with_tools yields {"type":"provider_info","model":...,"fallback":True} on fallback; test_stream_chat_fallback_emits_provider_info added to test_providers.py)

### [IDEA-2026-05-13-01] Run memory consolidation in background to prevent agent loop hangs

- **Source:** `app/memory.py:466-571` — `consolidate()` is O(n²) Jaccard comparisons with no parallelization; runs synchronously via `maybe_auto_consolidate()` (line 573) triggered every 50 new summaries. At 500 summaries, this blocks the agent loop for seconds.
- **Why it fits Ai_computer:** Long-running agents that write many session summaries (e.g., 50+ summaries = 500+ items after actions) will experience unpredictable latency spikes when consolidation fires mid-task. Users notice a stall in the agent's response time with no visible cause. Recommend: move consolidation to a background task so agent loop stays responsive.
- **Scope (this PR only):** Change `maybe_auto_consolidate()` from synchronous to fire-and-forget: spawn `asyncio.create_task(self.consolidate())` instead of returning the result. Remove the return value (callers don't use it). Update one call-site in `app/agent.py` where `maybe_auto_consolidate()` is invoked. ~5 LOC.
- **Acceptance criteria:** A test that triggers 50+ `summarize_session()` calls verifies that the consolidation task is enqueued (via mock/spy) rather than blocking. No latency regression in the fast path (agent loop). Existing test_memory.py tests still pass.
- **Out of scope:** Reducing AUTO_CONSOLIDATE_EVERY or parallelizing Jaccard (those are tier-2 optimizations if background consolidation doesn't solve the issue).
- **Status:** done (2026-05-15: changed 3 asyncio.to_thread(maybe_auto_consolidate) call sites in agent.py to asyncio.create_task(asyncio.to_thread(...)) — consolidation no longer blocks agent loop; test_maybe_auto_consolidate_fires_at_threshold added to test_memory.py)

### [IDEA-2026-05-13-02] Add watchdog timer to detect dead MCP server listeners

- **Source:** `app/mcp_manager.py:117-151` — `_listen()` catches exceptions and logs but silently exits; server.status stays `"running"`. Future `call()` requests timeout after 60s before detecting the dead listener.
- **Why it fits Ai_computer:** If an MCP server listener crashes (e.g., OOM, segfault in subprocess), the caller gets no early feedback — they wait the full _CALL_TIMEOUT (60s) before learning the server is dead. For interactive agent workflows, 60s latency is unacceptable. Recommend: add a heartbeat check or detect listener silence early.
- **Scope (this PR only):** In `MCPServer`, add a `_last_response_at` timestamp updated whenever the listener receives a response (line 139). Add an async `_watchdog()` task that checks if `time.time() - _last_response_at > 15s` and no call is in-flight, then mark status `"dead"`. Run watchdog alongside listener. Cancel it on stop. ~20 LOC.
- **Acceptance criteria:** A test that stops an MCP server subprocess verifies status transitions to `"dead"` within 15s (watchdog interval) instead of 60s. Existing MCP server tests still pass. No regression on normal operation (rapid calls keep heartbeat fresh).
- **Out of scope:** Auto-restart dead servers; WebSocket/gRPC upgrade (higher complexity).
- **Status:** done (2026-05-15: added _last_response_at timestamp + _watchdog() task to MCPServer; watchdog fires if pending calls get no response for _WATCHDOG_TIMEOUT=15s; starts on server startup, cancelled on stop/_kill_proc; _last_response_at updated in _listen() on each response; test_mcp_watchdog_marks_dead_when_pending_calls_get_no_response added)

### [IDEA-2026-05-13-03] Parity test: Chroma vs FallbackCollection recall consistency

- **Source:** `app/memory.py:17-78` — `_FallbackCollection` pure keyword matching vs ChromaDB cosine+BM25 hybrid. Two code paths diverge silently when `USE_CHROMA=0`.
- **Why it fits Ai_computer:** Tests run with Chroma disabled (offline/CI) but production uses Chroma. A keyword search that returns "exact match" in fallback may return a very different ranking from Chroma cosine similarity. Regressions in recall quality are invisible because no parity test exists.
- **Scope (this PR only):** Add `tests/test_memory_parity.py` — run the same sequence of `memory.add` + `memory.search` calls on both a real MemoryStore (Chroma) and a forced-fallback MemoryStore (`USE_CHROMA=0`). Assert top-1 result is the same item (not necessarily same rank). Use `pytest.importorskip("chromadb")` to skip if Chroma not installed. ~30 LOC.
- **Acceptance criteria:** Test passes on machines with Chroma installed; auto-skips on CI where Chroma is absent. If the top-1 result diverges between backends for an obvious query, the test must fail and surface the difference.
- **Out of scope:** Fixing any divergence (that's a separate IDEA); BM25 parameter tuning.
- **Status:** queued
### [IDEA-2026-05-15-01] Add ALLOWED_MODELS env var for enterprise model governance

- **Source:** Cursor Enterprise Admin Controls (May 2026) — model allow-lists restrict which models teams can access; OpenRouter fallback chain (providers.py:872-910) currently has no restrictions.
- **Why it fits Ai_computer:** Enterprise/org deployments need cost control and compliance (e.g., "no closed-source models", "only Claude 3.5+"). Currently any LLM provider/model can be used. Adding `ALLOWED_MODELS` env var (comma-separated list or regex pattern) acts as a whitelist, rejecting disallowed models before calling provider. Prevents users/agents from accidentally consuming expensive out-of-policy models.
- **Scope (this PR only):** Add `_validate_allowed_models(model_name: str) -> bool` utility in `app/providers.py`. At startup (e.g., in `__init__` of ProviderClient), parse `ALLOWED_MODELS` env var (default: empty = all allowed). In `_chat_openrouter`, before calling the provider, check `_validate_allowed_models(model)` and raise ValueError if disallowed. ~15 LOC in providers.py. Add one unit test: `test_allowed_models_whitelist_blocks_disallowed_model`.
- **Acceptance criteria:** With `ALLOWED_MODELS="claude-3-5-sonnet,gpt-4"`, calling with model `gemma-3-27b-it` raises ValueError before HTTP call. Empty/unset `ALLOWED_MODELS` allows all models (backward compatible). Test passes.
- **Out of scope:** Dynamic allow-list reload (restart required); per-user allow-lists (team/org-level is the first step); cost budgets or rate limiting.
- **Status:** done (2026-05-15: added _get_allowed_models() in providers.py; filters fallback chain in _openrouter_models_to_try(); raises ValueError if all models blocked; empty/unset env var allows all; 3 tests added to test_providers.py)

### [IDEA-2026-05-15-02] Support glob patterns in ALLOWED_MODELS for flexible model governance

- **Source:** Follow-up to IDEA-2026-05-15-01 — current exact-match whitelist requires listing every model variant; enterprise deployments need prefix patterns like `claude-*` to allow all Claude models without enumerating each version.
- **Why it fits Ai_computer:** `ALLOWED_MODELS="claude-3-5-sonnet,claude-3-7-sonnet"` breaks as soon as a new Claude model ships. `ALLOWED_MODELS="claude-*"` is stable across model version bumps. `fnmatch` stdlib handles glob patterns, no new deps.
- **Scope (this PR only):** Change `_get_allowed_models()` in `app/providers.py` to return a list of patterns (not a frozenset). Change the filter in `_openrouter_models_to_try` to use `fnmatch.fnmatch(model, pattern)` for any pattern in the list. Exact strings still work (fnmatch treats literals as exact match). ~5 LOC change in providers.py. Update test to cover wildcard.
- **Acceptance criteria:** `ALLOWED_MODELS="claude-*"` allows `claude-3-5-sonnet` and `claude-3-7-sonnet` but blocks `gpt-4`. Exact-match behavior unchanged. Test added.
- **Out of scope:** Regex patterns; per-user lists; case-insensitive matching.
- **Status:** queued

### [IDEA-2026-05-16-01] Dual-mode action dispatch test coverage for desktop vs background browser

- **Source:** `app/tools.py:1380-1560` — ToolExecutor.run_action() dispatches to either background browser handlers (`_*_bg` async) or native desktop handlers (sync pyautogui). Actions are routed based on `use_bg = self._background_mode and self._bg_browser.is_running` (line 1412), creating two code paths that diverge silently.
- **Why it fits Ai_computer:** A test that passes in background browser mode may fail in desktop (pyautogui) mode due to timing differences, missing state initialization, or coordinate scaling edge cases. Currently no test validates both paths return identical (or equivalent) ToolResults for the same action. Risk: regressions in one mode go undetected.
- **Scope (this PR only):** Add `tests/test_tools_dual_mode.py` — create a ToolExecutor instance, mock a BackgroundBrowser, run a sequence of actions (click, type, scroll, key) in both modes (toggle `_background_mode=True/False`). Assert: (a) both modes return `ok=True`, (b) output doesn't contradict (e.g., not "Clicked at X,Y" vs "Clicked at 0,0"). Use `@pytest.mark.parametrize` to avoid duplication. ~40 LOC test, no code changes.
- **Acceptance criteria:** Test runs both desktop and background modes for each action type (mouse_move, mouse_click, keyboard_type, scroll, key_combo). If one mode fails and the other succeeds, test fails with clear message showing the divergence. Existing app/tools tests still pass.
- **Out of scope:** Fixing any divergence found; performance comparison between modes; isolated window mode (Win32 messaging).
- **Status:** queued

### [IDEA-2026-05-16-02] Log full traceback for plugin handler errors instead of swallowing it

- **Source:** `app/tools.py:1561` — plugin exception handler catches all errors and returns `ToolResult(ok=False, output=f"Plugin error: {str(e)}")`. Multi-line tracebacks are discarded.
- **Why it fits Ai_computer:** When a plugin handler crashes, the agent only sees a one-line summary. The developer/user has no way to diagnose root cause without attaching a debugger. Full traceback logged at ERROR level costs 2 LOC and dramatically improves debuggability.
- **Scope (this PR only):** In `run_action()` plugin fallback `except` block (`app/tools.py:~1561`), add `import traceback; logging.error("Plugin handler error: %s", traceback.format_exc())` before returning the ToolResult. ~3 LOC.
- **Acceptance criteria:** When a plugin handler raises an exception, the full traceback appears in server logs at ERROR level. The ToolResult still returns `ok=False` with the short message. 1 test verifies logging call is made.
- **Out of scope:** Structured error reporting to the UI; plugin sandboxing.
- **Status:** queued


### [IDEA-2026-05-17-01] Render markdown in the Agent answer message

- **Source / context:** The `.message.assistant` block (added 2026-05-17, commit 8d797f3) renders the agent's final reply via `textContent` — plain text. Replies routinely contain markdown (`` `inline code` ``, ```` ``` ```` fenced blocks, **bold**, lists). They currently show as literal characters.
- **Why it fits Ai_computer:** The Agent answer is now the primary thing the user reads. Claude renders markdown in its replies; AI Computer should too. The mermaid CDN was already vendored, and a small markdown renderer (or a minimal inline parser) would make agent replies readable.
- **Scope (this PR only):** In `static/index.html`, when appending an `assistant` message, run the text through a minimal markdown→HTML pass: fenced code blocks → `<pre>`, inline code → `<code>`, bold/italic, and bullet lists. Either vendor a tiny lib (e.g. a ~3KB markdown parser into `static/vendor/`) or hand-roll a small safe transformer. MUST escape HTML first to avoid injection. ~40-70 LOC.
- **Acceptance criteria:** An agent reply containing a fenced code block renders as a real code block; inline backticks render as `<code>`; no raw HTML injection possible (test with a reply containing `<script>`). Plain-text replies unchanged.
- **Out of scope:** Full CommonMark compliance; tables; syntax highlighting inside code blocks.
- **Status:** done (2026-05-17: manual ship — added renderMarkdown() safe parser; assistant messages render fenced code blocks, inline code, bold/italic, bullet lists; HTML-escaped first so no injection)

### [IDEA-2026-05-17-02] Log WARN when memory recall_count metadata update fails

- **Source:** `app/memory.py:438` — `collection.update(ids=[item.id], metadatas=[meta])` wrapped in bare `except Exception: pass` after incrementing `recall_count` in `recall_sessions()`.
- **Why it fits Ai_computer:** The MMR re-ranking uses `recall_count` as a log-scaled reinforcement boost (memory.py:426-439). If `collection.update()` fails silently (e.g. Chroma offline, schema mismatch), the count is never persisted. On the next recall, the same memory gets re-boosted from its stale low count, artificially inflating its score forever. Silent failures in the memory persistence layer are the hardest bugs to diagnose.
- **Scope (this PR only):** In `recall_sessions()` (`app/memory.py:~438`), change the bare `except Exception: pass` to `except Exception as e: _log.warning("recall_count update failed for %s: %s", item.id, e)`. 1 LOC change. Add 1 test that mocks `collection.update` to raise and asserts a warning is logged.
- **Acceptance criteria:** When `collection.update` raises, a WARNING is emitted with the item id and error. Normal recall path unchanged. Existing memory tests still pass.
- **Out of scope:** Retry logic; falling back to in-memory counter; fixing root cause of Chroma failures.
- **Status:** queued

### [IDEA-2026-05-17-02] PC-control: wait-for-window-ready before interacting

- **Source / context:** PC-control audit 2026-05-17. The core real workflow is "code an app -> run it -> drive its UI." Today the agent runs `python app.py` and immediately screenshots/clicks — but the window may not exist yet or may be unpainted, so it gets a blank image or a click into nothing, then blindly retries.
- **Why it fits Ai_computer:** This is the #1 robustness gap for the code-then-test loop the product is built around. Claude Computer Use / Operator never act on a window that isn't ready.
- **Scope (this PR only):** Add a `wait_for_window(title_substr, timeout=10s)` helper in `app/tools.py` (or `providers.py`) that polls `win32gui.EnumWindows` until a matching visible window with a non-zero rect appears, then waits one extra short beat for first paint. Expose it as a tool action `wait_for_window` so the agent can call it explicitly, and call it automatically after `run_command`/launch actions in computer/isolated mode. ~50-70 LOC + 1 test.
- **Acceptance criteria:** Launching an app then calling `wait_for_window` blocks until the window is enumerable; times out cleanly with `ok=False` if it never appears. Unit test with a mocked EnumWindows.
- **Out of scope:** Detecting "fully rendered" via frame diff (separate idea); cross-process readiness for web apps (use the browser path).
- **Status:** queued

### [IDEA-2026-05-17-03] PC-control: screenshot + state-change check after every UI action

- **Source / context:** PC-control audit 2026-05-17. AI Computer only refreshes the screenshot for a subset of actions (`_SCREENSHOT_ACTIONS`) and never compares before/after. A click consumed by an overlay, or a `keyboard_type` that dropped chars, isn't noticed until the reflection loop ~5 steps later.
- **Why it fits Ai_computer:** Claude Computer Use takes a screenshot after EVERY action and the model sees the effect before the next step. Without this, multi-step UI workflows drift silently.
- **Scope (this PR only):** In the computer-mode loop (`app/agent.py`, the `mode in ("computer","computer_isolated")` block), capture a screenshot after every UI action (not just the `_SCREENSHOT_ACTIONS` subset) and feed it to the next model turn. Add a cheap before/after perceptual check (e.g. compare a downscaled-image hash); if a click/type produced zero visual change, surface a `no-effect` hint to the model. ~60-90 LOC.
- **Acceptance criteria:** After a click, the next model turn receives a fresh screenshot. A click that changes nothing on screen produces a `no-effect` note in the agent context. No change to coding/browser modes.
- **Out of scope:** OCR; element grounding; pixel-diff visualization in the UI.
- **Status:** queued

### [IDEA-2026-05-17-04] PC-control: guard PrintWindow against indefinite hangs

- **Source / context:** PC-control audit 2026-05-17. `ctypes.windll.user32.PrintWindow` in `_capture_hwnd_image` (`app/providers.py`) can block indefinitely on some DWM/overlay/remote-session scenarios — it freezes the whole agent thread with no timeout.
- **Why it fits Ai_computer:** A single unkillable screenshot call stalls the entire task. Needs a bounded wait.
- **Scope (this PR only):** Run the `PrintWindow` call (and the BitBlt fallback) inside a worker thread with a hard timeout (~5s) via `concurrent.futures`. On timeout, abandon the capture and raise a clean `RuntimeError("window capture timed out")` so the caller falls back to the full-screen `mss` path. ~30 LOC + 1 test with a mocked slow PrintWindow.
- **Acceptance criteria:** A simulated slow PrintWindow is abandoned after the timeout; the agent continues instead of hanging. GDI handles still released (the IDEA-2026-05-17 leak fix stays intact).
- **Out of scope:** Changing the capture method; async rework of the screenshot path.
- **Status:** queued

### [IDEA-2026-05-17-05] PC-control: multi-monitor / per-window DPI-correct coordinates

- **Source / context:** PC-control audit 2026-05-17. Coordinate scaling (`_scale`, `get_scale_factor`) uses `pyautogui.size()` (primary monitor) regardless of which monitor the target window is on. On a multi-monitor setup with mixed DPI, clicks land off-target (observed ~25% off in the audit). Isolated-HWND clicks also never apply the computed scale factor.
- **Why it fits Ai_computer:** Off-target clicks silently break every desktop workflow on any multi-monitor machine — common for the target users.
- **Scope (this PR only):** When scaling coordinates for a desktop/isolated action, resolve the target window's monitor (`win32api.MonitorFromWindow`) and use that monitor's geometry + DPI (`GetDpiForWindow`) instead of the primary screen. Apply the scale factor in `_mouse_click_isolated`. ~70-110 LOC.
- **Acceptance criteria:** A click targeted at a window on a secondary monitor lands at the correct pixel. Single-monitor behavior unchanged. Unit test with mocked monitor geometry.
- **Out of scope:** Mixed-DPI screenshot stitching; the browser path.
- **Status:** queued

### [IDEA-2026-05-17-06] PC-control: detect and recover from hung application windows

- **Source / context:** PC-control audit 2026-05-17. `_is_hung_app_window()` detects a frozen window but the agent has no recovery action — if an app it launched freezes mid-workflow, the task just hangs.
- **Why it fits Ai_computer:** The code-then-test loop will regularly hit apps that hang (a buggy build the agent just wrote). The agent needs a way out.
- **Scope (this PR only):** Add a `force_close_window` / `kill_app` tool action that, given a window title or pid, terminates the process (`taskkill` / `psutil`). When `_is_hung_app_window()` is true for the current target, surface a hint to the model suggesting it kill + relaunch. ~40-60 LOC + 1 test.
- **Acceptance criteria:** The agent can terminate a hung app it launched and relaunch it. Killing is scoped to processes the agent started or an explicit pid/title — never a blanket kill.
- **Out of scope:** Killing system processes; a process manager UI.
- **Status:** queued

### [IDEA-2026-05-17-07] Connectors: pluggable coding backends (Claude Code CLI, Google Antigravity)

- **Source / context:** Product direction 2026-05-17. AI Computer is the always-on main agent; users connect free models for general use, but free models are weak at code. The agent should be able to delegate coding-heavy subtasks to a stronger free-but-capable backend the user has connected — e.g. Claude Code CLI, or Google Antigravity's free coding models.
- **Why it fits Ai_computer:** Lets users keep the free-model default for orchestration/chat while getting real coding quality on demand — without paying for a frontier API as the primary.
- **Scope (NEEDS DESIGN — do not implement blind):** This is a feature, not a small PR. Before coding: write a short design note covering (a) a `CodingBackend` interface (detect availability, send a coding brief, return a diff/result), (b) adapters for `claude` CLI and Antigravity, (c) how the agent decides to delegate (task complexity / explicit user request), (d) config + a Settings-modal connector list. Then split into implementation IDEAs. First PR should be just the interface + the `claude` CLI adapter + availability detection.
- **Acceptance criteria (design phase):** A design note in `docs/` enumerating the interface, adapters, routing rule, and config. Implementation IDEAs filed from it.
- **Out of scope:** Implementing all adapters at once; billing/quota tracking.
- **Status:** in_progress (slice 1 shipped 2026-05-17 commit 7a9a1e4: CodingBackend interface + ClaudeCodeBackend adapter + BackendRegistry + GET /api/coding-backends + 9 tests. Remaining slices filed as IDEA-17-08/09/10.)

### [IDEA-2026-05-17-08] Connectors: Settings-modal connector list for coding backends

- **Source / context:** Follow-up to IDEA-17-07 (connector foundation shipped 7a9a1e4). The `GET /api/coding-backends` endpoint exists and returns each backend with live availability; nothing in the UI shows it yet.
- **Why it fits Ai_computer:** Users need to see which coding backends are connected/available and which is the default — the same way the provider chips show LLM-key status. Mirrors Codex's marketplace list and OpenClaw's connector list.
- **Scope (this PR only):** In `static/index.html`, add a "Coding backends" section to the Settings modal (the modal added in Phase A). On modal open, fetch `/api/coding-backends` and render one row per backend: name, type, a green/grey availability dot (from the `available` field), version text, and a marker on the default. Pure read-only display + a manual refresh. ~50-70 LOC, no backend changes.
- **Acceptance criteria:** Opening Settings shows the Claude Code backend with a green dot + version when the CLI is installed, grey when not. No regression to existing Settings content.
- **Out of scope:** Adding/editing backends from the UI (config-file only for now); credential fields.
- **Status:** queued

### [IDEA-2026-05-17-09] Connectors: agent delegation — delegate_coding action + routing

- **Source / context:** Follow-up to IDEA-17-07. The backend registry + Claude Code adapter exist but nothing invokes them — the agent can't actually delegate yet.
- **Why it fits Ai_computer:** This is the payoff: a cheap orchestration model hands a multi-file coding subtask to the connected strong backend and gets back a structured result. Without it the connector is inert.
- **Scope (this PR only):** Add a `delegate_coding` tool action: args `{task, repo_path?, files?, constraints?}`; it calls `coding_backends.registry.get().submit(CodingBrief(...))` off the event loop and returns the `CodingResult` summary + files_changed into the agent loop as a tool result. Register it in `tool_registry.py` for the coding pack. Stream a feed event so the UI shows "Delegated to claude-code …". Do NOT auto-route yet — the agent calls it explicitly when its own model judges the task too heavy. ~80-110 LOC + tests (mock the registry).
- **Acceptance criteria:** A task can call `delegate_coding`; with a mocked backend the result flows back into the agent loop and renders in the feed. If no backend is available, the action returns a clear `ok=False` telling the agent to do it itself. Pytest green.
- **Out of scope:** Automatic routing heuristics (the model decides for now); resume/multi-turn delegation; cost budgeting.
- **Status:** queued (depends on IDEA-17-07 slice 1 — done)

### [IDEA-2026-05-17-10] Connectors: generic ACP adapter (Antigravity + custom backends)

- **Source / context:** Follow-up to IDEA-17-07. Only the Claude Code adapter exists. OpenClaw's ACPX shows a clean pattern: any backend implementing the Agent Client Protocol (JSON-RPC 2.0) is usable via one generic adapter.
- **Why it fits Ai_computer:** Lets users connect Google Antigravity's free coding models and any other ACP-speaking agent without a bespoke adapter each time — the free-but-good coding backend the product strategy depends on.
- **Scope (this PR only):** Add an `AcpBackend(CodingBackend)` adapter in `app/coding_backends.py` that speaks the Agent Client Protocol over a subprocess (spawn the configured command, JSON-RPC `session/new` → `session/prompt`, collect the structured result). Register `"acp"` in `_BACKEND_TYPES`. Verify against at least one real ACP backend if installed; otherwise unit-test the JSON-RPC framing with a fake subprocess. ~120-160 LOC.
- **Acceptance criteria:** A config entry `{"type":"acp","command":"<acp-server>"}` is loaded and `detect()`/`submit()` work via JSON-RPC. Unit test covers the protocol framing with a mock.
- **Out of scope:** Bundling Antigravity; a backend marketplace.
- **Status:** queued (depends on IDEA-17-07 slice 1 — done)

### [IDEA-2026-05-17-11] Skills: adopt the SKILL.md / Agent Skills standard format

- **Source / context:** Connector/skills research 2026-05-17. AI Computer's current skills (the Expertise Library — `app/skills.py`) are ad-hoc. Claude Code and Codex both use the open Agent Skills standard: a directory per skill with a `SKILL.md` (YAML frontmatter + markdown body), `description` as the fuzzy-matched trigger, progressive disclosure (only descriptions in context, body loads on invocation).
- **Why it fits Ai_computer:** Adopting the standard makes AI Computer skills portable with Claude Code / Codex and makes a future skill marketplace trivial. It's also a cleaner authoring format than whatever skills use today.
- **Scope (this PR only):** Audit `app/skills.py`. Add a `SKILL.md` loader: scan a `skills/` directory, parse YAML frontmatter (`name`, `description`, `allowed-tools`) + markdown body, expose them through the existing skill_manager interface so the Expertise Library and the agent both see them. Keep existing skills working (shim or migrate). Progressive disclosure: only `description` goes into the agent's context until the skill is invoked. ~100-140 LOC.
- **Acceptance criteria:** A skill authored as `skills/<name>/SKILL.md` is discovered, shows in the Expertise Library, and its body loads only when invoked. Existing skills unaffected.
- **Out of scope:** A skill marketplace; `scripts/`/`references/` bundle execution; hooks.
- **Status:** queued

### [IDEA-2026-05-17-12] DIFFERENTIATOR: "Watch & Act" — local event-triggered autonomous runs

- **Source / context:** Competitive research 2026-05-17. The strategic wedge: AI Computer runs on the user's own machine, free, always-on. Scheduled/unattended agents are a top-requested feature but Manus/Cursor-cloud/Devin meter or paywall them, and cloud agents *structurally cannot* watch a local filesystem or the user's real apps. OpenClaw triggers off chat messages only. **Nobody offers free, local, multi-trigger automation in one tool.**
- **Why it fits Ai_computer:** Converts the product from "a thing I prompt" into "a thing that runs my life" — the always-on local advantage cloud agents can't copy. Attracts every user type: researchers (daily digests), automation people (folder watchers), coders (CI-failure responders).
- **This is a MASTER ticket — do not implement directly.** It is built in slices: IDEA-17-13 (trigger foundation + cron), 17-14 (filesystem watch), 17-15 (message-channel trigger), 17-16 (Rules UI), 17-17 (safety rails). Ship 17-13 and 17-17 first (foundation + caps), then the rest.
- **Status:** split (pick a sub-ticket)

### [IDEA-2026-05-17-13] Watch & Act slice 1 — trigger foundation + cron schedule

- **Source / context:** Slice of IDEA-17-12. Reference: APScheduler for cron; existing task-run machinery in `app/agent.py` + `app/main.py`.
- **Why it fits Ai_computer:** The foundation every other trigger builds on — a rule model + a registry + the first trigger type (time/cron).
- **Scope (this PR only):** Add an `AutomationRule` model (`{id, name, trigger_type, trigger_config, goal, mode, enabled, created_at}`) persisted to `workspace/automation_rules.json` (gitignored). Add an `automation` module with a registry that loads rules and, for `trigger_type == "cron"`, schedules them via `APScheduler` (add to requirements). When a rule fires, start a normal agent task with the rule's `goal`/`mode`. CRUD endpoints: `GET/POST/DELETE /api/automation/rules`. ~150-200 LOC + tests. No UI yet (17-16).
- **Acceptance criteria:** A cron rule created via the API fires an agent task at its scheduled time (test with a near-future cron). Rules survive a server restart. Pytest green.
- **Out of scope:** Filesystem/message triggers (later slices); UI; the safety caps (17-17 — but do not enable autonomous firing in production until 17-17 ships).
- **Status:** queued

### [IDEA-2026-05-17-14] Watch & Act slice 2 — filesystem-watch trigger

- **Source / context:** Slice of IDEA-17-12. Uses the `watchdog` library.
- **Why it fits Ai_computer:** "When a file lands in ~/Invoices, process it" — a workflow no cloud agent can do. Strong for automation users.
- **Scope (this PR only):** Add a `filesystem` trigger type to the automation registry (IDEA-17-13). A rule with `trigger_config: {path, event: created|modified, glob}` registers a `watchdog` observer; on a matching event, fire the rule's task with the changed file path injected into the goal context. Debounce rapid events. ~100-140 LOC + tests.
- **Acceptance criteria:** Dropping a file matching the glob into the watched folder fires exactly one task (debounced). Observer stops cleanly on rule disable / shutdown. Pytest green with a temp dir.
- **Out of scope:** Recursive cross-drive watching; the other trigger types.
- **Status:** queued (depends on IDEA-17-13)

### [IDEA-2026-05-17-15] Watch & Act slice 3 — message-channel trigger

- **Source / context:** Slice of IDEA-17-12. Reuses the existing Discord/Telegram integration listeners.
- **Why it fits Ai_computer:** "When CI posts a failure in this Discord channel, reproduce and draft a fix." Coders + teams.
- **Scope (this PR only):** Add a `message` trigger type. A rule with `trigger_config: {channel, contains?}` hooks the existing Discord/Telegram listener — when a matching message arrives, fire the rule's task with the message text as context. Reuse the integration code already in `app/integrations/`. ~90-120 LOC + tests (mock the listener).
- **Acceptance criteria:** A simulated channel message matching the filter fires the rule's task with the message text in context. Pytest green.
- **Out of scope:** New chat platforms; replying back in-channel (the agent's normal delivery handles that).
- **Status:** queued (depends on IDEA-17-13)

### [IDEA-2026-05-17-16] Watch & Act slice 4 — Automation Rules UI

- **Source / context:** Slice of IDEA-17-12. The CRUD API exists (17-13); users need a UI.
- **Why it fits Ai_computer:** Rules are useless if you must hand-edit JSON.
- **Scope (this PR only):** Add an "Automation" panel (in the Settings modal or its own sidebar entry) listing rules — name, trigger summary, enabled toggle, last-fired time, delete. A small form to create a rule (name, trigger type + config, goal, mode). Talks to `/api/automation/rules`. ~120-160 LOC in `static/index.html`.
- **Acceptance criteria:** A user can create, toggle, and delete a cron rule from the UI; it round-trips through the API. UI smoke covers create + delete.
- **Out of scope:** Visual cron builder; rule run-history view.
- **Status:** queued (depends on IDEA-17-13)

### [IDEA-2026-05-17-17] Watch & Act slice 5 — safety rails for unattended runs

- **Source / context:** Slice of IDEA-17-12. CRITICAL — autonomous triggers can loop and burn the free model quota; a top Reddit complaint about agents is silent token-burn.
- **Why it fits Ai_computer:** Unattended firing is only safe with hard limits. Ship before enabling triggers in production.
- **Scope (this PR only):** For automation-fired runs: a per-rule max-runs-per-hour cap; a global concurrent-automation-run cap; a per-run step/token ceiling (reuse `TOKEN_BUDGET`); a `dry_run` flag on a rule that makes it log "would have run" instead of firing; loop detection (same rule firing >N times in a window → auto-disable + notify). ~120-160 LOC + tests.
- **Acceptance criteria:** A rule exceeding its hourly cap is skipped with a logged reason. A `dry_run` rule never starts a real task. A rule that fires too fast auto-disables. Pytest green.
- **Out of scope:** Cost dashboards; per-model budgets.
- **Status:** queued (depends on IDEA-17-13)

### [IDEA-2026-05-17-18] DIFFERENTIATOR: Closed-Loop Build & QA — run the app, drive its real UI, verify

- **Source / context:** Competitive research 2026-05-17 (2nd-ranked differentiator). After writing code, AI Computer launches the app, drives its real UI (browser for localhost, desktop control for native apps), reads the console, screenshots failures, and self-corrects until it actually works. Cursor's cloud agents do localhost click-through but in a sterile VM; paid QA products (Agentiqa) do browser-only. AI Computer is the only *free* tool that closes the loop AND can QA native desktop apps. Directly attacks the "agent says done but it's broken" complaint.
- **Why it fits Ai_computer:** It's the combination — coding + browser + desktop control — that no single competitor has, and it makes the agent's output trustworthy.
- **Scope (NEEDS the PC-control robustness work first — IDEA-17-02 window-ready + 17-03 per-action screenshot):** After a coding task that produced a runnable app, the agent: (1) starts the dev server / launches the app, (2) generates a short verification plan from the goal, (3) drives the UI (browser or desktop) clicking through the plan, (4) on a failure screenshot/console-error, feeds it back and re-attempts, capped at N cycles. First PR: just the orchestration skeleton + the localhost-browser path. ~150-200 LOC.
- **Acceptance criteria:** A "build a localhost page with a button that does X" task ends with the agent having actually clicked the button and confirmed X, or reporting the specific failure. Step-capped. Pytest + UI smoke.
- **Out of scope:** Native-desktop QA (follow-up once PC-control hardening lands); visual-regression diffing.
- **Status:** queued (depends on IDEA-17-02 and IDEA-17-03)

### [IDEA-2026-05-17-19] Private Context Bridge — research across logged-in browser + local notes

- **Source / context:** Competitive research 2026-05-17 (3rd-ranked). Pull from the user's authenticated browser sessions (internal wikis, Gmail, paywalled sites) and local notes/files together — nothing leaves the machine. Cloud agents fundamentally cannot (they're not logged into your accounts).
- **Why it fits Ai_computer:** Strong privacy + research wedge. Ranked last — overlaps with Watch & Act research workflows and "use my real browser profile" has CDP-stability + security rough edges.
- **Scope (NEEDS DESIGN — do not implement blind):** Write a design note covering (a) safely attaching to the user's real browser profile vs a dedicated agent profile, (b) a local-notes indexer (Obsidian/markdown/folders), (c) the security model — explicit per-source consent, never exfiltrate. Then file implementation IDEAs.
- **Acceptance criteria (design phase):** A design note in `docs/` with the browser-profile approach, notes-index approach, and consent model.
- **Out of scope:** Implementation until the design note is reviewed.
- **Status:** queued

### [IDEA-2026-05-17-20] Voice in + out via the browser Web Speech API (free, no deps)

- **Source / context:** Studied farzaa/clicky 2026-05-17 — a macOS AI companion whose whole UX is "talk to it, it talks back." Clicky pays for AssemblyAI (STT) + ElevenLabs (TTS). AI Computer is a web app, so it can do BOTH for free with the browser's built-in `SpeechRecognition` + `SpeechSynthesis` Web Speech APIs — zero dependencies, runs locally, no API keys. Clicky's headline feature, free.
- **Why it fits Ai_computer:** Voice is a major UX upgrade and a real differentiator at the free tier. Lets users dictate a task hands-free and have the agent's answer read back — strong for accessibility and for the always-on companion direction.
- **Scope (this PR only):** In `static/index.html`: (1) a mic button on the composer that uses `webkitSpeechRecognition`/`SpeechRecognition` to dictate into the task input (push-to-hold or click-to-toggle); (2) a "read replies aloud" toggle that runs the `assistant` message text through `speechSynthesis.speak()` when a task completes. Feature-detect and hide the controls gracefully where the API is absent (some browsers). Strip markdown before TTS so it doesn't read backticks. ~90-130 LOC, frontend only.
- **Acceptance criteria:** Mic button dictates into the composer in a supporting browser; with "read aloud" on, a completed agent reply is spoken. Controls hidden cleanly where unsupported. No regression to typed input. UI smoke covers the mic button presence.
- **Out of scope:** Streaming/real-time transcription; a paid TTS voice; wake-word; voice on the desktop (pywebview) wrapper if its webview lacks the API.
- **Status:** queued

### [IDEA-2026-05-17-21] Visual pointer overlay — show where the agent is acting in desktop mode

- **Source / context:** Studied farzaa/clicky 2026-05-17 — it flies a blue cursor overlay to UI elements Claude references, making on-screen guidance legible. AI Computer's desktop control clicks invisibly today; the user can't see what it's doing. `app/desktop_bridge.py` already creates transparent overlay windows.
- **Why it fits Ai_computer:** Makes desktop/computer-use mode trustworthy and watchable — the user sees the agent move to a target before it clicks, instead of mysterious cursor jumps. Also useful for a future "explain my screen" mode.
- **Scope (this PR only):** Before a desktop mouse action (`mouse_click`, `double_click`, etc.) in computer/isolated mode, briefly render a visible marker (a ring or arrow) at the target coordinates via a transparent always-on-top overlay — reuse the overlay-window machinery in `app/desktop_bridge.py`. Show ~400ms, then act. Make it toggleable (off by default for speed, on for "watch mode"). ~80-120 LOC.
- **Acceptance criteria:** With the pointer overlay enabled, a desktop click shows a marker at the target before the click fires; disabled, behavior is unchanged. Overlay never steals focus or blocks the click. Pytest green.
- **Out of scope:** Animated bezier-arc cursor flight; multi-monitor arc routing; the browser path (browser mode has its own page).
- **Status:** queued

### [IDEA-2026-05-17-22] "Explain my screen" companion mode — read-only screen help

- **Source / context:** Studied farzaa/clicky 2026-05-17. Clicky's core mode is read-only: it sees your screen and *explains*, never acts — a gentler product than a full action agent. AI Computer only has action modes.
- **Why it fits Ai_computer:** A calm, low-risk entry point that pulls non-technical users — "what am I looking at / help me with this" with no fear the agent will change anything. Complements the action modes.
- **Scope (NEEDS small design):** A new `explain` mode: capture a screenshot, send it + the user's question to the model, return an explanation — NO tool actions, NO mouse/keyboard, purely read-only. Wire it as a mode option; the agent loop short-circuits to a single vision-answer turn. Pairs well with IDEA-17-20 (voice) and IDEA-17-21 (pointer overlay) for a true companion. Write a 1-paragraph design note on how the mode bypasses the action loop, then implement. ~60-100 LOC.
- **Acceptance criteria:** Selecting `explain` mode and asking about the current screen returns an explanation and performs zero actions (no clicks/types/writes). Pytest asserts the explain path never dispatches a tool action.
- **Out of scope:** Pointing at elements (that's 17-21); voice (17-20); multi-turn screen conversations.
- **Status:** queued
