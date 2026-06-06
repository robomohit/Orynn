# Changelog

## Unreleased

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
