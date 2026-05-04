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
- **Status:** queued

### [IDEA-2026-04-29-02] Copy-task button on completed runs

- **Why it fits Ai_computer:** Re-running or tweaking a previous task is currently retype-from-memory; a one-click copy speeds iteration.
- **Scope (this PR only):** Add a small "↻ Copy task" button on each finished run card that fills the input box with the original goal text.
- **Acceptance criteria:** Button appears only on terminal-state runs; click populates input and focuses it. Playwright smoke test added.
- **Out of scope:** Editing/forking mid-run, history search.
- **Status:** queued

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
- **Status:** needs_human — scope requires reading/writing `workspace/.api_key`; `workspace/` is in the NEVER-TOUCH list. Human must decide alternate key-persistence path (e.g. a dedicated `.agent_key` file in HOME_DIR, or accepting the rotating-key behavior as intentional).

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
- **Status:** queued
