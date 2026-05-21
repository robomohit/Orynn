# Floating widget — design notes

The floating AI Computer widget lives in `static/index.html`, `static/style.css`,
and `static/app.js` (the `#vorb-root` element + the `sidekickWidget()` IIFE).

## Current design — Liquid-Glass Sidekick v2 (LIVE)

**Collapsed** is a pill-shaped liquid-glass toggle card (178×64) pinned bottom-right:

- Layered `backdrop-filter` glass with a `.vorb-shine` shimmer sweep.
- A frosted icon core (`.vorb-core`), a two-line copy block (`.vorb-kicker`
  "AI COMPUTER" + `.vorb-state` live status), and a 3-bar audio meter
  (`.vorb-meter`) that animates while a task is running or the mic is live.

**Expanded** is the Sidekick panel (376 px wide):

- `.vpanel-aurora` — a soft animated aurora layer behind the content.
- Header: brand sigil + "AI Computer / SIDEKICK" + minimize + (widget-shell only) close.
- `.vpanel-activity` — a pulse dot + live status text.
- `#vpanel-steps` — a live step feed mirroring the last 5 feed-card / turn-summary
  titles from the dashboard, refreshed by `syncSteps()` every 700 ms.
- `.vpanel-log` — the conversation log (user + agent bubbles).
- `.vpanel-compose` — mic + text input + send, side by side.

**Behavior:**

- Drag-to-reposition: the toggle card and the panel header are draggable; the
  position is clamped to the viewport and persisted in `localStorage` under
  `ai-computer.vorb-position.v2`. A drag does not also fire the open-click.
- `Ctrl+Shift+Space` toggles the panel open/closed (dashboard mode).
- Theme-aware liquid glass via `--sk-*` CSS custom properties — white frost on
  light, near-black frost on dark. Cyan accent (`#5be0d0` dark / `#12a394` light).
- Honors `prefers-reduced-motion`.
- Funnels into the existing composer pipeline — typing/sending in the widget
  drives the same `/api/tasks` path as the dashboard composer. `speakAgentReply`
  is wrapped so agent replies also land in the widget log (read-aloud intact).

**Widget-shell mode (`?widget=1` or `?sidekick=1`):**

- Adds the `widget-shell` class to `<html>`, `<body>`, and `#vorb-root`.
- Hides every dashboard surface; `<body>` becomes transparent; the panel fills
  the whole window. The header is the OS drag region for the frameless window.
- This is what the native desktop launcher loads.

## Desktop launcher

Two equivalent launchers exist (consolidation tracked as a follow-up):

- `python -m app.shell` — spawns uvicorn + a frameless always-on-top pywebview
  window at `/?widget=1`.
- `python run_desktop.py --widget` — same idea, frameless/transparent/on-top
  pywebview window; without `--widget` it opens the full dashboard.

## History

- The earlier **Spotlight command pill** design (a 484×56 top-centered pill,
  commit `86c06c9`) is preserved in git history — `git show 86c06c9` to recover
  it. It is not kept as a live folder.
- The `static/widget-spotlight/` folder (added in `1261e58`) was a stale
  line-ending-only duplicate of `static/` and has been removed.
