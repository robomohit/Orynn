# Orynn vs Codex — Complete Fix List

> **Status (2026-11-10):** this list was written against an older dashboard. A
> re-audit against the running app found most items already resolved by later
> redesign work. Fixed in the re-audit pass: live markdown while streaming (#1,
> #2), code-block header overlapping the first line (#22/#23), pre-tool narration
> glued to the answer (#1), light-mode sidebar contrast (#9), settings section
> labels and dividers (#27), and squeezed sidebar titles (#4).
> Already resolved before it: #3, #5, #6, #7, #8, #12, #14, #15, #16, #17, #18,
> #19, #20, #24, #25, #26. Still open by choice: date grouping in the sidebar
> (#4, it groups by folder), a richer collapsed work summary (#13), custom
> dropdowns (#10), and `#vorb-root` (#11, still used by the widget shell).

Compiled from live screenshots of Orynn at `localhost:8080` + code review of `app.js`, `style.css`, `liquid-glass.css`, `index.html`, `markdown_render.py`.

---

## 🔴 CRITICAL — Breaks the core experience

### 1. No live streaming of assistant text
**What Codex does:** Text appears token by token as the model generates it, inside a live bubble that grows in real time. You see thinking happen.

**What Orynn does:** Nothing renders in the feed until the entire task completes. `appendMessage()` is only called once on the `done` event using `event.reason`. If the task fails or pauses mid-run, the user sees zero assistant output.

**Files:** `static/app.js` — `appendMessage()`, `processTaskEvent()`, `openStream()`

**Fix:**
- On `item/agentMessage/delta` (or equivalent SSE delta event), create the assistant bubble the first time and append text incrementally using `insertAdjacentText`.
- On `done`, replace raw content with the fully rendered `renderMarkdown()` output.
- This is the single biggest UX gap between Orynn and Codex.

---

### 2. Markdown headings not rendered in the JS renderer
**What Codex does:** Headings (H1–H6) render with proper size hierarchy.

**What Orynn does:** `renderMarkdown()` in `app.js` handles bold, italic, code, links, bullets — but has **no heading support**. Any `# Title` in an AI response renders as raw `# Title` text.

**File:** `static/app.js` — `renderMarkdown()`

**Fix:** Add before the bold regex:
```js
raw = raw.replace(/^(#{1,6})\s+(.+)$/gm, (_, hashes, text) => {
  const level = hashes.length;
  const sizes = ['2em','1.5em','1.25em','1.1em','1em','0.95em'];
  return `<h${level} class="md-h md-h${level}" style="font-size:${sizes[level-1]};font-weight:700;margin:12px 0 4px;">${text}</h${level}>`;
});
```

---

### 3. Stale red dot badges on every old session
**What's seen:** Dozens of old "dispatch / Inspect the repo" sessions all show a red • (active indicator). This means the sidebar looks like everything is on fire — there are "116 active" sessions at once, which is impossible.

**File:** Backend session state, `static/app.js` sidebar rendering

**Fix:** Red dot should only show on sessions with `status === 'running'` or `status === 'paused'`. Completed/failed tasks should show no dot, or a gray/green dot. Audit the state that drives the red dot render.

---

## 🟠 HIGH — Seriously degrades polish vs Codex

### 4. Sidebar: Dozens of identical sessions with no grouping
**What's seen:** The sidebar shows 8+ groups all named "dispatch" each containing one session "Inspect the repo". They look completely identical. Users can't tell them apart.

**What Codex does:** Conversations have distinct titles, are grouped by recency (Today, Yesterday, Last Week), and old ones collapse.

**Fix:**
- Auto-generate better session titles from the first message (first 6–8 words, truncated cleanly).
- Group by date: Today / Yesterday / This week / Older.
- Collapse old groups by default.
- Add hover actions (rename, delete) on session rows.
- Consider hiding 2-week-old sessions behind "Show X older sessions".

---

### 5. Topbar model string is raw and ugly
**What's seen:** `coding · openrouter/qwen/qwen3-coder:free` — a raw API model identifier shown verbatim to the user.

**What Codex does:** Shows a clean model name like "o4-mini" or "GPT-4o" with a chevron to switch.

**Fix:** Map provider/model strings to display names. E.g. `openrouter/qwen/qwen3-coder:free` → `Qwen3-Coder (free)`. Add a `MODEL_DISPLAY_NAMES` map in app.js or the backend.

---

### 6. Feed error/status messages are raw unstyled text
**What's seen:**
- `"Task paused."` — plain text, no icon, no color
- `"Task failed: Max steps reached (25) without finish action."` — inline red text
- `"Paused"` — lone word at bottom of feed, looks like a CSS bug

**What Codex does:** Status transitions are inline cards with icons, clear labels, and appropriate styling. Errors have a clear visual treatment.

**Fix:**
- `Task paused.` → render as a status chip: `⏸ Task paused` with muted styling
- `Task failed: ...` → render as an error card with ⚠ icon, the reason in human-readable form, and a "Retry" action
- `Paused` standalone → this should be part of the status chip or removed
- Create a `renderStatusNote(type, text)` helper for consistent inline statuses

---

### 7. Composer bottom row: icon-only buttons with no labels or tooltips
**What's seen:** Three small icon buttons (sliders/EQ icon, speaker, mic). No labels, no tooltips, no affordance.

**What Codex does:** Uses labeled controls or at minimum clear tooltips with keyboard shortcuts.

**Fix:**
- Add `title=""` tooltip attributes at minimum.
- On wider screens, show text labels next to the icons.
- The sliders icon = Tweaks? Put a clear name on it.
- Consider moving voice/TTS toggles into Settings and keeping the composer bar clean.

---

### 8. Multiple toasts stacking for reconnect events
**What's seen:** Three toasts piling up at bottom-right: "Reconnected to running task", "Stream interrupted. Reconnecting (attempt 1).", "Stream interrupted. Reconnecting (attempt 2)." — these are internal network events that users can't act on.

**What Codex does:** Reconnect silently; only surface errors the user must act on.

**Fix:**
- Suppress "Stream interrupted. Reconnecting..." toasts entirely — show only if all retries fail.
- "Reconnected to running task" can be a subtle inline status change, not a toast.
- Deduplicate: if a toast with the same message already exists, update it in-place rather than stacking.

---

### 9. Light mode glass effect is invisible
**What Codex does:** Has a proper light mode with clear contrast and depth.

**What Orynn does:** `--lg-rail-light: rgba(248, 248, 246, 0.72)` over `--bg-0: #f8f8f6` — blurring cream over cream. The glass effect is indistinguishable from a flat background. The sidebar and panels look identical to the page background.

**File:** `static/liquid-glass.css`

**Fix:** In light mode, use a true contrast background:
```css
--lg-rail-light: rgba(230, 232, 235, 0.82);  /* visible gray tint */
--lg-panel-light: rgba(255, 255, 255, 0.78);
```
Or switch light mode to a warmer white (#ffffff) body with clearly separated panel surfaces.

---

### 10. Settings modal uses native `<select>` dropdowns
**What's seen:** The Appearance and Default mode fields use raw browser `<select>` elements — they look like 1998 form fields inside an otherwise styled modal.

**Fix:** Replace with custom styled dropdowns (matching the existing `.composer-select-label` style in glass mode). Or use a segmented control for small option sets like Dark/Light/System.

---

## 🟡 MEDIUM — Friction and clutter

### 11. `#vorb-root` capsule widget is embedded in dashboard HTML but invisible
**File:** `static/index.html`

The capsule widget `#vorb-root` lives inside the dashboard page as dead HTML weight. It's hidden but still in the DOM. The dashboard and sidekick are separate products — this should be fully stripped from the dashboard HTML.

---

### 12. Welcome/empty state is too generic
**What the code has:** `.hello-eye` showing "Orynn" + 4 example suggestion buttons with generic prompts.

**What Codex does:** Clean centered layout, personal greeting, 4 well-chosen starter suggestions that reflect actual use cases, and nothing else.

**Fix:**
- Center the welcome vertically in the feed, not at the top.
- Make the 4 starter suggestions specific and compelling (e.g., "Fix a bug in my codebase", "Explain this error", "Write tests for this function", "Refactor this file").
- Remove the `hello-eye` text if it's just the word "Orynn" — it adds nothing.
- Consider a subtle animated cursor or gradient shimmer on the Orynn wordmark to signal it's alive.

---

### 13. Collapsed work summary row is barely readable
**What's seen:** `1 step, Edited 2 files, Ran 1 command ›` — a gray text line spanning the full feed width.

**What Codex does:** Collapsed tool-call groups are clear, icon-rich cards that communicate what happened and invite expansion.

**Fix:**
- Add file/tool icons next to each action type.
- Style as a distinct pill or card, not inline text.
- Put the chevron `›` on the right edge; left-align action summary.
- Show timing: `1 step · 4s ›`.

---

### 14. Topbar task title truncates poorly
**What's seen:** `In the folder agent_smoke_buggy, run the tests,...` — ellipsis cut mid-sentence at a weird point.

**Fix:** Truncate to a max of ~60 chars with proper CSS `text-overflow: ellipsis` and full title in a `title=""` tooltip.

---

### 15. `data-theme` defaults to `"light"` in HTML but the app looks dark
**File:** `static/index.html` — `<html data-theme="light">`

The default is light mode but the glass CSS is dark-first. This causes the welcome state to load with light theme before user preference is applied, causing a flash.

**Fix:** Either:
1. Default `data-theme="dark"` in HTML
2. Or read saved preference synchronously from `localStorage` before first paint and set the attribute in a `<script>` tag in `<head>` (before CSS loads)

---

### 16. No heading size hierarchy in Qt/sidekick markdown renderer
**File:** `app/markdown_render.py`

The `_heading()` function now has `size = {1: "1.34em", 2: "1.17em", 3: "1.02em"}` — this is better, but H4/H5/H6 all fall back to H3 size (level capped at 3). They should at minimum all render at the same size and weight, or use `1.02em` for H3+.

**Fix:** Change `min(len(m.group(1)), 3)` to just `len(m.group(1))` and extend the size dict to all 6 levels, or cap gracefully.

---

### 17. No keyboard shortcut for "New session" shown in UI
The sidebar button shows `⌘N` only on hover. On Windows this should be `Ctrl+N`. Check that the shortcut actually works on Windows (`event.metaKey` vs `event.ctrlKey`).

---

### 18. Sessions count badge is confusing
**What's seen:** `General 116` and `129` badges next to group headers.

These look like unread notification counts but they're actually total session counts. Users will expect them to mean "unread items."

**Fix:** Either remove the count badges entirely, or label them clearly (e.g., "116 sessions") on hover.

---

## 🟢 POLISH — Codex-level refinements

### 19. No send button in composer
Codex has an arrow/send button that activates on text input. Orynn relies on Enter-to-send with no visual affordance. Add a send button that appears when the textarea has content.

### 20. No model/mode indicator in composer
Codex shows the current model right in the composer area. Orynn shows it in the topbar only. Add a small clickable chip in the composer bottom-left showing current model/mode.

### 21. Feed scroll behavior
After a message is added, the feed should smooth-scroll to the bottom. Check that this works during streaming (scroll should follow the growing bubble, not jump).

### 22. Code blocks missing syntax highlighting
`renderMarkdown()` creates `<pre><code>` blocks but applies no syntax highlighting. Codex uses highlighted code blocks. Add `highlight.js` (CDN) or a lightweight tokenizer for the most common languages (Python, JS, Bash, JSON).

### 23. No "copy" button on code blocks
Codex adds a copy-to-clipboard button on hover over code blocks. Add one — it's expected behavior in 2025.

### 24. User message bubble sizing
**What's seen:** User bubble is right-aligned, good. But it spans most of the feed width. Codex keeps user bubbles narrow (max ~60% width), making them clearly distinct from the wide assistant response area.

### 25. Glass mode not activated by default
The liquid glass styles only apply when `body[data-glass="on"]` is set. Check whether this is being set on load. If the glass class isn't applied, all the `liquid-glass.css` work is invisible and surfaces render flat.

### 26. "General mode" label in sidebar
The sidebar header under "New session" reads `General mode` with a folder icon. This means nothing to a first-time user. Codex just shows the project/conversation name.

**Fix:** Either remove it, rename it to the actual current session name, or replace with a project selector.

### 27. Settings modal needs section dividers and grouping
The settings run from EFFORT → API key → Appearance → Default mode → three toggles with no visual separation. Group into sections: **Model**, **Appearance**, **Features**, each with a hairline separator and a 10px label.

### 28. Approval/permission modals not tested
The code has 8+ inline overlay modals (`#approval`, `#permission`, `#desktop-access`, `#readiness-preflight`, `#mcp-overlay`, etc.). These were not triggered during testing. Verify their styling matches the rest of the app — they were likely not updated when the glass redesign was applied.

---

## Priority Order (What to Fix First)

1. **Live streaming** — This is the killer feature gap (#1)
2. **Markdown headings** — Quick win, one regex (#2)
3. **Red dots on old sessions** — Makes the app look broken (#3)
4. **Feed status/error styling** — Users need clear feedback (#6)
5. **Sidebar session titles & grouping** — Core navigation (#4, #5)
6. **Toast flood suppression** — Annoying on every use (#8)
7. **Light mode glass** — Fix the invisible effect (#9)
8. **Streaming scroll follow** — Needed once streaming is fixed (#21)
9. **Syntax highlighting** — High impact visual upgrade (#22, #23)
10. **Everything else** — Polish tier (#10–#28)
