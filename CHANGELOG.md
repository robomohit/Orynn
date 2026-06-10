# Changelog

## Unreleased

### Background agent (input politeness)
- **Fixed silent pixel-click degradation of every "UIA" click.** The
  uiautomation lib defines `GetInvokePattern()` only on its typed control
  subclasses; our tree walks return generic `Control` wrappers, so the call
  raised `AttributeError`, was swallowed, and EVERY uia_click/sequence fell
  back to real-mouse coordinate clicks — which require the window visible and
  silently click whatever covers it (the root cause of the live-run "8 clicks
  ok, display still 0"). A universal `_uia_pattern()` accessor (GetPattern by
  PatternId) restores true InvokePattern delivery: verified live, a chained
  calculation lands on a MINIMIZED Calculator with zero mouse movement. Also
  added a TogglePattern tier for checkboxes/switches.
- **Chained-expression verification** — `uia_click_sequence`'s calculator
  read-back check now evaluates chained input ('12+8=' then '×5=') instead of
  silently skipping verification when the expression contains a mid-sequence
  '='; a wrong display now triggers the keyboard self-correction. Verified
  live on Groq Llama-3.3-70b: (12+8)×5 → verified 100 in 2 actions / 34s
  (June 6 baseline: 86–180s, 9–18 tool failures, zero correct results).
- **Background typing tier** — `uia_type` now tries a UIA `ValuePattern`
  write with read-back verification BEFORE the focus+paste path: on native
  edit controls the text lands with zero focus steal and zero keyboard
  hijack, so the agent can fill fields while the user keeps working. React/
  Electron inputs that desync on value writes are detected by the read-back
  and fall through to the proven focus+paste tier.
- **Input-politeness guard** — every action that hijacks the real keyboard or
  mouse (focus+paste typing, `keyboard_type`, `key_combo`, pixel clicks, OCR
  fallbacks, the Calculator keystroke fallback) now waits — bounded, never a
  deadlock — for the user's hands to pause before acting, and reports when it
  waited. The guard discriminates the agent's own synthetic input from the
  user's via the last-input timestamp, so multi-step runs don't throttle
  themselves. Disable with `ORYNN_INPUT_POLITE=0`.

### Small-model reliability (prompt + loop)
- Desktop system prompt rewritten small-model-first (about half the size):
  a CORE KIT prior, a decision table with canonical examples, a plan-ledger
  protocol (the model restates its numbered plan position every turn, so the
  plan re-enters context and multi-clause goals don't lose their second
  clause), a failure budget (same target failed twice → switch approach;
  three approaches → finish honestly), and an anti-cheat rule (answers must
  be read from the app's UI, never computed in the shell).
- XML fallback prompt pins the exact output format with a canonical example
  (strict one-line JSON, one action per turn) for models whose native tool
  calling hiccups.
- Goal re-anchor: the original goal is re-injected into every 4th desktop
  observation so fast free models stop drifting to the last observation.
- Desktop observation cap raised 1000 → 1600 chars so control menus and
  teaching errors survive truncation.
- Fixed `uia_click_sequence`'s native tool schema omitting `read_result` —
  tool-calling models could never use the verify-in-the-same-call pattern.

### Free-model reliability (desktop control)
- **Control menu on window-ready** — `wait_for_window`, `focus_window`, and app
  launches now attach a "Visible controls" list (real UIA control names) to the
  tool result, so the model picks names off a menu instead of guessing. The
  calculator e2e runs showed free models burning 10+ steps guessing names
  ("Four"/"4"/"digit"/"×") that they could simply have read.
- **Teaching miss errors** — a `uia_find` miss now returns the nearest real
  control names (fuzzy-matched) plus the window's actual interactive controls,
  instead of a bare "no UIA control matched".
- **Zombie-window immunity** — UIA root selection now demotes DWM-cloaked
  frames (suspended/zombie UWP windows that shadow the live app with an
  identical title) and requires real content beyond title-bar chrome; searches
  fall through to the runner-up window on a total miss instead of dying inside
  an empty frame.
- **Finish evidence gate** — a desktop-mode `finish` with zero successful
  desktop actions is bounced once with instructions to do and verify the task
  (kills the "Done." empty-finish failure class seen on small free models);
  a second finish is always allowed through.
- **Keyboard-first guidance** — the desktop prompt now steers the model to one
  keyboard action over click-chains when the app accepts keystrokes, and to
  pick control names from the attached menus.

### Benchmark honesty
- `scripts/benchmark_tasks.py` now records a `route` summary per task and flags
  desktop runs whose answer was computed via shell instead of the app UI
  (`verified_desktop_path` / `route.warning`); BENCHMARKS.md documents the rule.

### Connectors & free-model focus
- **Real API connectors** — weather (Open-Meteo), Wikipedia, Hacker News, GitHub (public repos), and dictionary. Each is a single no-auth API call that returns real structured data, auto-linked with zero setup. This is the surface that stays reliable on a fast free model — one call → real data — unlike the multi-step web-UI driving that derails. Verified live on Groq (London weather, topic summaries, repo stars/issues).
- Reframed the idle dashboard around the reliable free-model use-cases: instant connector answers (weather / explain a topic / trending in tech / check a repo) plus a quick desktop task and run-code.

### Conversation & stream
- Minimal stream: planning/working/reflection chrome collapses into a single calm "Thinking…" indicator with a moving accent shimmer; on completion the working/approval cards fold into "Worked for X" and only the answer text remains.
- Even when the minimal stream suppressed every working card, a finished desktop/coding turn now leaves a quiet "Worked for X" capstone above the answer (a plain chat stays bare).
- The final answer streams in token-by-token (typing reveal) with live markdown formatting.
- Proper markdown rendering — real heading hierarchy (H1–H3), ordered + bullet lists, horizontal rules — in both the web dashboard and the Qt capsule.
- Multi-turn chat: a follow-up message continues the conversation with prior context instead of starting a new one; clicking a continued chat in the sidebar replays the whole thread.
- Sidebar: repeated identical runs collapse into one "×N" row.
- Settings reorganized into General / Permissions / Extensions tabs.

### Agent
- Unified tool surface: the model gets the full tool catalogue (UIA desktop, screen, browser, web research, files, shell) and decides which a task needs, instead of mode-gated tool sets.
- Planning is model-decided: no forced upfront plan for desktop tasks; decomposition is an optional `make_subtasks` tool the model calls only when worthwhile.

### Onboarding & reliability
- Onboarding steers new users to a free **and fast** Groq key (accepts an OpenRouter or Groq key, auto-detected by prefix).
- Groq is now the preferred free provider when its key is set (sub-second vs OpenRouter's 5–15s latency), with a transparent cross-provider fallback to the OpenRouter `:free` chain if Groq is busy/unavailable — fast by default, reliable as backup. A deliberate `DESKTOP_MODEL` opt-in still wins for explicit desktop tasks.
- More persistent chain retry on free-tier rate-limit storms so a transient 429 wave recovers into a (slow) success instead of failing the task.
- During a backoff, the "retrying in Ns…" notice and the stall-watchdog "still working" hint now persist instead of being overwritten by keep-alive heartbeats — a rate-limited task explains the wait rather than silently reading "Thinking".

### Visual polish
- Ambient accent glow enabled, dark default theme, decluttered composer (task options behind a toggle), feed breathing room, and removed the unused 3.2 MB mermaid dependency.

### Docs
- Animated demo GIF as the README hero.

### Dashboard UI
- Codex-inspired redesign: centered idle hero, flat background (no gradient wash), and quiet "reveal-on-hover" chrome so nothing is overloaded.
- Contextual hero that names the active project ("What should we build in <folder>?").
- Session history grouped by working folder as a project tree (folder glyph + nested chats), showing the 5 most recent with a "Show more" expander and folder-scoped search.
- Done-state summary like Codex: a collapsible "Worked for Xm Ys" timeline plus an "N files changed" capstone listing every file the agent created/edited/deleted.
- Hover-revealed message actions: copy the reply and rate it (thumbs wired to the feedback endpoint).
- Replaced the heavy in-app folder browser with a lightweight dropdown (quick folders + native OS "Browse…" dialog).
- Calmer, more professional motion (no blur/throb entrances) and a keyboard focus ring for all controls (`:focus-visible`), honoring `prefers-reduced-motion`.
- Stall watchdog and rate-limit feedback so a running task never sits silently — it surfaces "free models are busy, retrying…" instead of looking frozen.

### Security
- Hardened task identifiers to prevent path-like IDs from reaching task metadata or log file paths.
- Removed API-key prefix/suffix logging and kept task initialization 500 responses generic.
- Reworked dynamic UI rendering for skills, MCP tools, terminal rows, subtasks, and command palette entries so API/model-provided strings are inserted as text, not HTML.

### Reliability
- Added an explicit task kill endpoint and cancellation finalization path so killed tasks end as `cancelled` rather than falling through to max-step failure.
- Cleaned task SSE queues and emitter state on unsubscribe, cancel, kill, and task completion.
- Fixed isolated desktop worker detection so `computer_isolated` keeps cropped isolated screenshots in hierarchical flows.

### Testing
- Added regression tests for task ID containment, generic init errors, log emitter cleanup, kill finalization, and UI injection hardening.
- Added `scripts/cleanup_resources.py` for repeatable low-RAM test hygiene and memory snapshots.

## [1.1.0] - Real-Time Streaming & Discovery Update

### Added
- **Streaming Overhaul**: Low-latency SSE streaming implemented for both the activity log and the main chat panel.
- **Thinking Indicators**: Visual pulsing dots (`...`) in the UI during agent reasoning phases.
- **Coding-First Mode**: High-speed, text-only mode optimized for software engineering.
- **Environment Discovery**: Added `system_info` and `list_directory` tools for dynamic OS/path detection.
- **Enhanced Chat**: Streaming status bubbles and action mini-cards for better feedback.
- **Experimental Vision**:
  - `find_on_screen`: Locates specific images on the display via template matching.
  - `ocr_image`: Full-screen text extraction using Tesseract OCR (requires Tesseract binaries).
  - Integrated scaling logic to ensure accuracy across different screen resolutions.

### Fixed
- **Newline Reliability**: Automatic normalization of literal `\n` characters in file write actions to prevent syntax errors.
- **Cross-Platform Safety**: Standardized `_safe_path` logic for Windows/Unix compatibility.

## [1.0.0] - Production Ready Release

### Added
- **UI Dashboard Rewrite**: Transformed into a 3-panel UI matching modern dashboard quality (Vercel/Linear-inspired) using Inter font and dark theme.
- **Agent Intelligence**: Memory context is now natively prepended to hierarchical planning prompts.
- **Auto Re-planning**: Dynamic generation of a new plan if >2 subtasks consecutively fail.
- **New Tools**:
  - `type_with_delay` for realistic keyboard input.
  - Targeted `scroll` utilizing specific coordinates.
  - Image recognition matching via `find_on_screen`.
  - Clipboard tracking (`get_clipboard`, `set_clipboard`).
  - Desktop popups via `notify`.
- **Robust Endpoints**:
  - `GET /api/health` with exact uptime telemetry.
  - `GET /api/models` explicitly returning activated env-keys.
  - Task management: listing, deletion (cleanup), pause, and resume.
  - Full task history extraction via `GET /api/tasks/{task_id}/log` backed by `.jsonl` appends.

### Fixed
- **Missing Imports**: Correctly scoped `pytesseract` to prevent runtime crashes.
- **Safety Overhaul**: Hard-blocks specifically dangerous shell patterns (`rm -rf`, fork bombs) avoiding accidental system destructions.
- **Timeouts**: Added strict `asyncio.wait_for(timeout=30.0)` around every individual tool execution to avoid hung agents.
- **Infinite Loops**: Hardcap constraint of 50 actions per root task.
- **Async Mismatches**: Verified plugin `playwright` correctly executes inside the async event loop.
- **Error Streams**: Handled graceful shutdown during backend crashes so the SSE client correctly emits an `'error'` signal instead of hanging.
- **ActionType Types**: Synchronized backend Pydantic Enums with the agent handler configurations.

### Changed
- Refactored `MemoryStore` to initialize pure in-memory `_FallbackCollection` automatically if ChromaDB binary dependencies fail.
- OpenRouter/Groq/Google `_chat_*` providers now use 3-attempt exponential backoff for `HTTP 429/500+` stability.
- Screenshot generation dynamically scales to `1280x800` max-resolution before Base64 serialization, saving immense token budgets.
