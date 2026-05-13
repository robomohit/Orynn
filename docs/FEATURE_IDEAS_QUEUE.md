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

### [IDEA-2026-05-02-10] UI Phase C1 — Terser one-line tool-call summaries in feed cards

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase C1"). Reference: Claude Code shows tool calls as `"Browsed the web, used 12 tools ›"`, `"Edited 3 files ›"`, `"Ran a command ›"` — collapsed by default with chevron to expand.
- **Why it fits Ai_computer:** Each feed-card head currently renders `<eyebrow> · <title> · <subtitle> · <state-chip>` — verbose; reads like a debug dump. Claude Code's terse one-liner pattern is denser and friendlier.
- **Scope (this PR only):** Rewrite `createCardHead()` in `static/index.html` (line 3389) so the head text becomes a single terse line: `Read 2 files`, `Ran a command`, `Edited file.py`, `Searched code`, etc. Map action types to verbs. Body keeps full detail and the existing collapse-on-click behavior (lines 3605–3608, already wired). ~80 LOC. Cards remain 1:1 with actions (grouping is C2, separate IDEA).
- **Acceptance criteria:** Every feed-card head is one short line ≤60 chars. Click chevron expands body and shows full args/output. Pytest green. UI smoke fires a trivial coding task and verifies the cards render correctly.
- **Out of scope:** Grouping consecutive same-verb actions into one card (that's IDEA-13 / Phase C2). Custom verbs per tool name beyond the obvious ones.
- **Status:** queued

### [IDEA-2026-05-02-11] UI Phase E — Typography + whitespace pass for tool-aesthetic feel

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
- **Status:** queued

### [IDEA-2026-05-02-12] UI Phase F — Split static/index.html into 3 files (refactor unlocker)

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase F"). Pure refactor. Encountered the override-block-fighting-base-rules problem in commit `3900273` — that issue will recur every UI change until the file is split.
- **Why it fits Ai_computer:** `static/index.html` is 4968 LOC of inline CSS + JS. Hard to navigate, easy to introduce CSS specificity bugs across distant rules. Splitting unlocks every future UI improvement.
- **Scope (this PR only):** Move all `<style>` block content (lines 11–2432) to `static/style.css`. Move all inline `<script>` content (lines 2803–4852) to `static/app.js`. Update `static/index.html` to reference both via `<link rel="stylesheet">` and `<script src="…" defer>`. Confirm `app/main.py` static mount serves both new files. Zero visual change. Net LOC moved ~5000, no new logic.
- **Acceptance criteria:** Page loads identically (visual diff at zero). All JS interactivity works. Pytest green. UI smoke playwright validates: page loads, mode dropdown works, task can be submitted.
- **Out of scope:** Module-splitting JS into ESM; bundler/build step; CSS module/extraction.
- **Status:** queued

### [IDEA-2026-05-02-13] UI Phase C2 — Group consecutive same-verb tool calls into one card

- **Source / context:** Full plan at `C:\Users\mohit\.claude\plans\okay-see-with-this-streamed-summit.md` ("Phase C2"). Reference: Claude Code's `"Edited 3 files ›"` collapses 3 individual writes into one row.
- **Why it fits Ai_computer:** Even with terser per-card titles (Phase C1 / IDEA-10), a long task with many tool calls produces a wall of cards. Grouping consecutive same-verb actions into one collapsed card with a count is the final density step.
- **Scope (this PR only):** In `processTaskEvent()` (`static/index.html` line 3883), when an `action_start` arrives for the same verb as the immediately-prior action, merge into the existing card instead of creating a new one. Card head shows `<verb> · <count>` (e.g. `Edited 3 files`, `Ran 2 commands`). Body lists each individual sub-detail. ~150–200 LOC. Higher risk than C1 because it changes event-routing.
- **Acceptance criteria:** A task that does 5 sequential write_file actions produces ONE card titled "Edited 5 files" with 5 expandable detail rows. A task that interleaves write_file + shell + write_file produces 3 cards (no over-grouping). Pytest green. UI smoke covers both interleaved and consecutive cases.
- **Out of scope:** Grouping non-consecutive (interleaved) same-verb actions; configurable grouping rules.
- **Status:** queued (depends on Phase C1 — IDEA-10 — having shipped first; defer until other phases stable)

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
- **Status:** queued

### [IDEA-2026-05-13-02] Add watchdog timer to detect dead MCP server listeners

- **Source:** `app/mcp_manager.py:117-151` — `_listen()` catches exceptions and logs but silently exits; server.status stays `"running"`. Future `call()` requests timeout after 60s before detecting the dead listener.
- **Why it fits Ai_computer:** If an MCP server listener crashes (e.g., OOM, segfault in subprocess), the caller gets no early feedback — they wait the full _CALL_TIMEOUT (60s) before learning the server is dead. For interactive agent workflows, 60s latency is unacceptable. Recommend: add a heartbeat check or detect listener silence early.
- **Scope (this PR only):** In `MCPServer`, add a `_last_response_at` timestamp updated whenever the listener receives a response (line 139). Add an async `_watchdog()` task that checks if `time.time() - _last_response_at > 15s` and no call is in-flight, then mark status `"dead"`. Run watchdog alongside listener. Cancel it on stop. ~20 LOC.
- **Acceptance criteria:** A test that stops an MCP server subprocess verifies status transitions to `"dead"` within 15s (watchdog interval) instead of 60s. Existing MCP server tests still pass. No regression on normal operation (rapid calls keep heartbeat fresh).
- **Out of scope:** Auto-restart dead servers; WebSocket/gRPC upgrade (higher complexity).
- **Status:** queued
