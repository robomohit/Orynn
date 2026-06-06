"""Virtual cursor overlay — gives the user a smooth visual cue whenever the
agent clicks/types somewhere on screen.

Designed to feel like Clicky / Claude-Computer-Use cursors: the pointer
glides along a gentle bezier curve, leaves a fading dotted trail, sits
inside a soft accent glow halo, and gives a satisfying scale-pulse on
click. No abrupt jumps, no clipped straight lines.

  • Frameless always-on-top fully-transparent overlay spanning the virtual
    desktop. WA_TransparentForMouseEvents so it never blocks input.
  • show_click(x, y) animates the cursor along a quadratic bezier to the
    target, then plays a click pulse + ripple.
  • show_type(x, y, text) flashes a typewriter caret at the location with
    a small label showing the text being typed.

Call sites: qt_shell capsule, which polls agent action_start events and
forwards mouse_click / keyboard_type to this overlay.
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import (Qt, QPoint, QRect, QRectF, QTimer,
                             QPropertyAnimation, QEasingCurve, Property,
                             QObject)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QPainterPath,
                           QFont, QGuiApplication)
from PySide6.QtWidgets import QWidget


# Tunable look
RIPPLE_COLOR = QColor(91, 224, 208)        # accent teal
CURSOR_COLOR = QColor(20, 24, 32, 235)     # near-black
CURSOR_OUTLINE = QColor(255, 255, 255, 240)
LABEL_BG = QColor(20, 24, 32, 220)
LABEL_FG = QColor(240, 242, 248, 245)


class _Ripple:
    """A single expanding ring + filled dot for one click."""
    __slots__ = ("x", "y", "t", "duration_ms")

    def __init__(self, x: int, y: int, duration_ms: int = 600):
        self.x = x; self.y = y; self.t = 0.0; self.duration_ms = duration_ms

    def progress(self) -> float:
        return min(1.0, self.t / max(1, self.duration_ms))

    def alive(self) -> bool:
        return self.t < self.duration_ms


class _Caret:
    """Brief typewriter caret indicator at a position."""
    __slots__ = ("x", "y", "t", "duration_ms", "text")

    def __init__(self, x: int, y: int, text: str = "", duration_ms: int = 900):
        self.x = x; self.y = y; self.t = 0.0
        self.duration_ms = duration_ms
        self.text = (text or "")[:32]

    def alive(self) -> bool:
        return self.t < self.duration_ms


class _Spotlight:
    """A precise focus-ring around a real UIA control's bounds, with a label.
    This is the UIA analogue of the virtual mouse: instead of guessing a pixel
    and moving a cursor, UIA knows the control's EXACT rectangle, so we trace it
    directly — a snapping focus ring + glow + a 'what it's doing' label."""
    __slots__ = ("x", "y", "w", "h", "label", "kind", "t", "duration_ms")

    def __init__(self, x, y, w, h, label="", kind="click", duration_ms=2200):
        self.x = int(x); self.y = int(y)
        self.w = max(8, int(w)); self.h = max(8, int(h))
        self.label = (label or "")[:48]
        self.kind = kind                 # click | type | find
        self.t = 0.0
        self.duration_ms = duration_ms

    def alive(self) -> bool:
        return self.t < self.duration_ms

    def progress(self) -> float:
        return min(1.0, self.t / max(1, self.duration_ms))


class _AppGlow:
    """A breathing glow tracing the edges of the whole app window the agent is
    working in (brand-colour). Persistent: re-armed on each action, fades out
    after a short hold of inactivity."""
    __slots__ = ("x", "y", "w", "h", "label", "t0", "armed_until", "fade_ms")

    def __init__(self, x, y, w, h, label, now, hold_ms=3500, fade_ms=480):
        self.x = int(x); self.y = int(y); self.w = int(w); self.h = int(h)
        self.label = (label or "")[:48]
        self.t0 = now
        self.armed_until = now + hold_ms
        self.fade_ms = fade_ms

    def rearm(self, x, y, w, h, label, now, hold_ms=3500):
        self.x = int(x); self.y = int(y); self.w = int(w); self.h = int(h)
        if label:
            self.label = label[:48]
        self.armed_until = now + hold_ms

    def alive(self, now) -> bool:
        return now < self.armed_until + self.fade_ms


class VirtualCursorOverlay(QWidget):
    """Full-screen click-through overlay that paints animated agent activity."""

    TICK_MS = 16
    CURSOR_DECAY_MS = 2800  # how long the cursor stays after activity
    # Smooth motion tunables — slower + curvier than a linear lerp
    TRAVEL_BASE_MS = 360      # baseline travel time for short hops
    TRAVEL_PER_PX = 0.7       # extra ms per pixel of distance (cap below)
    TRAVEL_MAX_MS = 900       # maximum travel time
    TRAIL_LEN = 14            # how many breadcrumb dots to keep
    TRAIL_STRIDE_MS = 30      # how often to record a trail point
    GLOW_RADIUS = 26          # soft accent halo radius around the cursor
    CLICK_PULSE_MS = 320      # cursor scale pulse duration on click

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._origin_x = 0
        self._origin_y = 0
        self._sync_virtual_geometry()

        self._ripples: list[_Ripple] = []
        self._carets: list[_Caret] = []
        self._spotlights: list[_Spotlight] = []
        self._app_glow: "_AppGlow | None" = None
        self._cursor_x = -100
        self._cursor_y = -100
        self._cursor_visible_until = 0  # epoch ms; 0 = hide
        self._click_pulse_start_ms = 0  # when last click happened

        # Bezier path animation toward the next target
        # Quadratic bezier: P0 (start) → P1 (control, off-line) → P2 (end)
        self._p0 = (-100, -100)
        self._p1 = (-100, -100)
        self._p2 = (-100, -100)
        self._anim_t = 1.0       # 0..1
        self._anim_duration_ms = self.TRAVEL_BASE_MS
        self._anim_elapsed_ms = self._anim_duration_ms

        # Trail breadcrumbs (newest last)
        self._trail: list[tuple[int, int, float]] = []  # (x, y, age_ms)
        self._last_trail_ms = 0

        # Floating action label under the cursor — e.g. "Clicking",
        # "Typing 'hello'", "Scrolling", "Looking at the screen". Fades
        # in/out smoothly as the agent's current action changes.
        self._action_label_text = ""
        self._action_label_set_ms = 0
        self._ACTION_LABEL_FADE_MS = 200
        self._ACTION_LABEL_HOLD_MS = 1100  # show for 1.1s after last update

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)

    # ── public API ──────────────────────────────────────────────────────
    def show_click(self, x: int, y: int, label: str = "Clicking") -> None:
        x, y = self._to_local_point(x, y)
        self._move_cursor_to(x, y)
        self._ripples.append(_Ripple(x, y))
        self._click_pulse_start_ms = self._now_ms()
        self._set_action_label(label)
        self._bump_cursor_visibility()
        self._ensure_visible()

    def show_type(self, x: int, y: int, text: str = "") -> None:
        x, y = self._to_local_point(x, y)
        self._move_cursor_to(x, y)
        self._carets.append(_Caret(x, y, text))
        if text:
            self._set_action_label(f"Typing “{text[:24]}”")
        else:
            self._set_action_label("Typing")
        self._bump_cursor_visibility()
        self._ensure_visible()

    def show_uia(self, x: int, y: int, w: int, h: int,
                 label: str = "", kind: str = "click") -> None:
        """UIA action feedback: trace a focus ring around the control's exact
        bounds and show what's happening. No cursor travel — UIA acts directly
        on the control, so we highlight precisely instead of faking a click.
        Keep only the latest spotlight so the view stays clean."""
        x, y, w, h = self._to_local_rect(x, y, w, h)
        self._spotlights = [s for s in self._spotlights if s.progress() < 0.7]
        self._spotlights.append(_Spotlight(x, y, w, h, label, kind))
        self._bump_cursor_visibility()
        self._ensure_visible()

    def show_app_focus(self, x: int, y: int, w: int, h: int,
                       label: str = "") -> None:
        """Glow the edges of the whole app window the agent is operating in
        (brand colour), with a status label. Re-armed on every action so the
        glow stays up while the agent works, then fades once it's idle."""
        x, y, w, h = self._to_local_rect(x, y, w, h)
        now = self._now_ms()
        if self._app_glow is None:
            self._app_glow = _AppGlow(x, y, w, h, label, now)
        else:
            self._app_glow.rearm(x, y, w, h, label, now)
        self._bump_cursor_visibility()
        self._ensure_visible()

    def keep_app_glow_alive(self) -> None:
        """Extend the app-edge glow's lifetime so it stays SOLID while the agent
        is busy — even when a slow model pauses several seconds between actions.
        Called on a heartbeat by the capsule while a task is running; once the
        task ends the heartbeat stops and the glow fades out naturally."""
        if self._app_glow is not None:
            self._app_glow.armed_until = self._now_ms() + 3500
            self._ensure_visible()

    def clear_app_glow(self) -> None:
        """Begin fading the app-edge glow out NOW (call the instant a task ends
        so the glow doesn't linger ~4s after completion). Fades over fade_ms
        (~0.5s) rather than vanishing, so it still feels smooth."""
        if self._app_glow is not None:
            self._app_glow.armed_until = self._now_ms()
            self._ensure_visible()

    def show_action(self, label: str, x: int | None = None,
                    y: int | None = None) -> None:
        """Show a label without firing a click/type — for actions like
        scrolling, focusing a window, taking a screenshot, etc."""
        if x is not None and y is not None:
            x, y = self._to_local_point(x, y)
            self._move_cursor_to(x, y)
        self._set_action_label(label)
        self._bump_cursor_visibility()
        self._ensure_visible()

    # Internal: set the floating action label that follows the cursor.
    def _set_action_label(self, label: str) -> None:
        self._action_label_text = (label or "").strip()
        self._action_label_set_ms = self._now_ms()

    # ── internal ────────────────────────────────────────────────────────
    def _now_ms(self) -> int:
        from PySide6.QtCore import QDateTime
        return QDateTime.currentMSecsSinceEpoch()

    def _ensure_visible(self) -> None:
        self._sync_virtual_geometry()
        if not self.isVisible():
            self.show()
            self.raise_()

    def _sync_virtual_geometry(self) -> None:
        """Cover all monitors and remember the global-to-local offset."""
        try:
            screens = QGuiApplication.screens()
            if not screens:
                screen = QGuiApplication.primaryScreen()
                screens = [screen] if screen is not None else []
            if not screens:
                return
            geo = screens[0].geometry()
            for screen in screens[1:]:
                geo = geo.united(screen.geometry())
            self._origin_x = int(geo.x())
            self._origin_y = int(geo.y())
            if self.geometry() != geo:
                self.setGeometry(geo)
        except Exception:
            pass

    def _to_local_point(self, x: int, y: int) -> tuple[int, int]:
        self._sync_virtual_geometry()
        return int(x) - self._origin_x, int(y) - self._origin_y

    def _to_local_rect(self, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        lx, ly = self._to_local_point(x, y)
        return lx, ly, int(w), int(h)

    def _bump_cursor_visibility(self) -> None:
        self._cursor_visible_until = self._now_ms() + self.CURSOR_DECAY_MS

    def _move_cursor_to(self, x: int, y: int) -> None:
        """Start a new bezier-curved animation from the current pos to (x,y).
        The control point is offset perpendicular to the line so the path
        looks like a hand-drawn arc rather than a straight ruler line."""
        # If cursor hasn't been on screen yet, snap to target without travel
        if self._cursor_x < 0:
            self._cursor_x, self._cursor_y = x, y
            self._p0 = self._p2 = self._p1 = (x, y)
            self._anim_t = 1.0
            return

        self._p0 = (self._cursor_x, self._cursor_y)
        self._p2 = (x, y)
        dx, dy = x - self._cursor_x, y - self._cursor_y
        dist = math.hypot(dx, dy)

        # Quadratic bezier control point: midpoint offset perpendicular,
        # with arc magnitude scaled to distance (15-25% of distance). The
        # sign is chosen so arcs lean slightly upward (feels more deliberate).
        mx, my = (self._cursor_x + x) / 2, (self._cursor_y + y) / 2
        if dist > 4:
            # Perpendicular unit vector (rotate 90deg ccw → upward bias)
            px, py = -dy / dist, dx / dist
            # Lean upward on screen → ensure py is negative (or zero)
            if py > 0:
                px, py = -px, -py
            arc = min(0.22 * dist, 90)  # cap arc so big jumps don't loop
            self._p1 = (mx + px * arc, my + py * arc)
        else:
            self._p1 = (mx, my)

        # Travel time scales with distance, with sensible bounds
        self._anim_duration_ms = int(min(
            self.TRAVEL_MAX_MS,
            self.TRAVEL_BASE_MS + dist * self.TRAVEL_PER_PX
        ))
        self._anim_elapsed_ms = 0
        self._anim_t = 0.0

    @staticmethod
    def _ease_in_out_cubic(t: float) -> float:
        """Smooth start AND smooth landing — clicky agent feel."""
        if t < 0.5:
            return 4 * t * t * t
        return 1 - ((-2 * t + 2) ** 3) / 2

    def _bezier_point(self, t: float) -> tuple[float, float]:
        """Evaluate the quadratic bezier at parameter t."""
        x0, y0 = self._p0; x1, y1 = self._p1; x2, y2 = self._p2
        u = 1 - t
        x = u * u * x0 + 2 * u * t * x1 + t * t * x2
        y = u * u * y0 + 2 * u * t * y1 + t * t * y2
        return (x, y)

    def _tick(self) -> None:
        # Drive cursor animation along the bezier with eased timing
        if self._anim_t < 1.0:
            self._anim_elapsed_ms += self.TICK_MS
            raw_t = min(1.0, self._anim_elapsed_ms / self._anim_duration_ms)
            self._anim_t = raw_t
            eased = self._ease_in_out_cubic(raw_t)
            x, y = self._bezier_point(eased)
            self._cursor_x, self._cursor_y = int(x), int(y)

        # Record trail breadcrumbs (independent of motion — tracks position)
        now_ms = self._now_ms()
        if (self._cursor_x >= 0
                and now_ms - self._last_trail_ms >= self.TRAIL_STRIDE_MS
                and self._anim_t < 1.0):  # only drop trail while moving
            self._trail.append((self._cursor_x, self._cursor_y, 0))
            self._last_trail_ms = now_ms
            if len(self._trail) > self.TRAIL_LEN:
                self._trail.pop(0)
        # Age trail
        self._trail = [(x, y, age + self.TICK_MS) for (x, y, age) in self._trail
                       if age < 800]

        # Age ripples + carets
        for r in self._ripples:
            r.t += self.TICK_MS
        self._ripples = [r for r in self._ripples if r.alive()]
        for c in self._carets:
            c.t += self.TICK_MS
        self._carets = [c for c in self._carets if c.alive()]
        for s in self._spotlights:
            s.t += self.TICK_MS
        self._spotlights = [s for s in self._spotlights if s.alive()]
        if self._app_glow is not None and not self._app_glow.alive(now_ms):
            self._app_glow = None

        # Hide if everything is done — also wait for the action label
        # fade to finish so the user gets to read what just happened.
        label_alive = (self._action_label_text and
                       now_ms - self._action_label_set_ms <
                       (self._ACTION_LABEL_HOLD_MS
                        + self._ACTION_LABEL_FADE_MS))
        all_done = (not self._ripples and not self._carets
                    and not self._spotlights
                    and self._app_glow is None
                    and not self._trail
                    and not label_alive
                    and now_ms >= self._cursor_visible_until
                    and self._anim_t >= 1.0)
        if all_done and self.isVisible():
            self.hide()
            self._action_label_text = ""
        elif not all_done:
            self.update()

    # ── painting ────────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QRadialGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 1. Trail breadcrumbs — small dots fading out behind the cursor
        for i, (tx, ty, age) in enumerate(self._trail):
            # Older dots are smaller + dimmer. Newest at end.
            life = max(0.0, 1.0 - age / 600.0)
            recency = (i + 1) / max(1, len(self._trail))  # 0..1
            radius = 2.5 + 1.5 * recency * life
            alpha = int(110 * recency * life)
            if alpha < 4:
                continue
            col = QColor(RIPPLE_COLOR); col.setAlpha(alpha)
            p.setBrush(QBrush(col))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(tx, ty), int(radius), int(radius))

        # 2. Ripples (under the cursor, on click sites)
        for r in self._ripples:
            prog = r.progress()
            # Two concentric expanding rings for a richer click feel
            for ring_offset, ring_alpha_mul in ((0.0, 1.0), (0.18, 0.55)):
                rp = max(0.0, prog - ring_offset)
                if rp <= 0 or rp > 1: continue
                radius = 8 + rp * 56
                alpha = int(170 * (1 - rp) * ring_alpha_mul)
                if alpha < 4: continue
                col = QColor(RIPPLE_COLOR); col.setAlpha(alpha)
                p.setPen(QPen(col, 2.2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPoint(r.x, r.y), int(radius), int(radius))
            # Inner solid dot
            inner_alpha = int(170 * (1 - prog) ** 2)
            ic = QColor(RIPPLE_COLOR); ic.setAlpha(inner_alpha)
            p.setBrush(QBrush(ic))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(r.x, r.y), 7, 7)

        # 3. Carets (typing indicator)
        for c in self._carets:
            t = c.t
            on = (int(t // 250) % 2) == 0
            if on:
                p.setPen(QPen(RIPPLE_COLOR, 2))
                p.drawLine(c.x, c.y - 10, c.x, c.y + 10)
            if c.text:
                p.setFont(QFont("Segoe UI Variable Text", 10, QFont.Medium))
                tw = max(40, p.fontMetrics().horizontalAdvance(c.text) + 18)
                rect = QRect(c.x + 14, c.y - 12, tw, 24)
                p.setBrush(QBrush(LABEL_BG))
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                p.drawRoundedRect(rect, 6, 6)
                p.setPen(QPen(LABEL_FG))
                p.drawText(rect.adjusted(8, 0, -8, 0),
                           Qt.AlignVCenter | Qt.AlignLeft, c.text)

        # 3a. App-edge glow — brand-colour border around the whole target app
        now0 = self._now_ms()
        if self._app_glow is not None:
            self._paint_app_glow(p, self._app_glow, now0)

        # 3b. UIA spotlights — focus ring tracing a real control's bounds
        for s in self._spotlights:
            self._paint_spotlight(p, s, now0)

        # 4. Cursor + soft accent glow halo
        now = self._now_ms()
        if now < self._cursor_visible_until and self._cursor_x >= 0:
            # Glow halo behind the cursor — radial gradient, accent color
            cx, cy = self._cursor_x, self._cursor_y
            grad = QRadialGradient(cx, cy + 4, self.GLOW_RADIUS)
            g0 = QColor(RIPPLE_COLOR); g0.setAlpha(120)
            g1 = QColor(RIPPLE_COLOR); g1.setAlpha(0)
            grad.setColorAt(0.0, g0)
            grad.setColorAt(1.0, g1)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(cx, cy + 4),
                          self.GLOW_RADIUS, self.GLOW_RADIUS)

            # Cursor with click-pulse scale (briefly shrinks → pops back)
            scale = self._click_pulse_scale(now)
            self._paint_cursor(p, cx, cy, scale=scale)

            # Action label pill — fades in/out under the cursor
            self._paint_action_label(p, cx, cy, now)

        p.end()

    def _paint_app_glow(self, p: QPainter, g: "_AppGlow", now_ms: int) -> None:
        """Draw a soft, breathing brand-colour border hugging the app window's
        edges, with a status label tag at the top."""
        if now_ms < g.armed_until:
            a = min(1.0, (now_ms - g.t0) / 200.0)      # ease in
        else:
            a = max(0.0, 1.0 - (now_ms - g.armed_until) / g.fade_ms)
        if a <= 0.01:
            return
        pulse = (1 + math.sin(now_ms / 430.0)) / 2      # 0..1 gentle breathing
        # Sit the ring a hair inside the window outline.
        rect = QRectF(g.x + 1.5, g.y + 1.5, g.w - 3, g.h - 3)
        rad = 11.0
        # wide soft glow passes (outer → inner), breathing in intensity
        for gw, ga in ((26.0, 22), (16.0, 44), (8.0, 78)):
            c = QColor(RIPPLE_COLOR)
            c.setAlpha(int(ga * a * (0.62 + 0.38 * pulse)))
            p.setPen(QPen(c, gw)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, rad, rad)
        # crisp inner line
        c = QColor(RIPPLE_COLOR); c.setAlpha(int(248 * a))
        p.setPen(QPen(c, 2.4)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, rad, rad)
        # status tag near the top edge of the window
        if g.label:
            self._draw_label_pill(p, int(g.x + g.w / 2), int(g.y + 14),
                                  g.label, a)

    def _paint_spotlight(self, p: QPainter, s: "_Spotlight", now_ms: int) -> None:
        """Draw a snapping focus ring + glow around a UIA control's bounds, with
        a label pill below it. Animated: a quick expand-in, hold, fade-out."""
        prog = s.progress()
        intro, outro = 0.10, 0.16
        if prog < intro:
            a = prog / intro
        elif prog > 1 - outro:
            a = max(0.0, (1 - prog) / outro)
        else:
            a = 1.0
        if a <= 0.01:
            return

        # Intro starts slightly outside the exact UIA bounds and settles onto
        # the true rectangle. Keep this tight so the outline feels precise.
        grow = (1 - min(1.0, prog / intro)) * 3 if prog < intro else 0.0
        # 'type' keeps a very gentle breathing pulse while it holds.
        if s.kind == "type" and intro <= prog <= 1 - outro:
            grow += 0.75 * (1 + math.sin(s.t / 130.0)) / 2

        x = s.x - grow
        y = s.y - grow
        w = s.w + 2 * grow
        h = s.h + 2 * grow
        rad = min(7.0, max(2.0, min(w, h) / 5))
        rect = QRectF(x + 0.5, y + 0.5, max(1.0, w - 1), max(1.0, h - 1))

        # Faint interior tint.
        fc = QColor(RIPPLE_COLOR); fc.setAlpha(int(12 * a))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(fc))
        p.drawRoundedRect(rect, rad, rad)

        # Tight glow, then an exact hairline. Big glows made small controls feel
        # approximate; this keeps the user's eye on the actual UIA rectangle.
        for gw, ga in ((6.0, 20), (3.0, 46)):
            gc = QColor(RIPPLE_COLOR); gc.setAlpha(int(ga * a))
            p.setPen(QPen(gc, gw)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, rad, rad)

        # Crisp exact ring with a faint outer contrast line for light UIs.
        oc = QColor(0, 0, 0, int(72 * a))
        p.setPen(QPen(oc, 3.2)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, rad, rad)
        rc = QColor(RIPPLE_COLOR); rc.setAlpha(int(235 * a))
        p.setPen(QPen(rc, 1.8)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, rad, rad)

        # Corner brackets make the exact bounds legible without flooding the
        # whole control. Draw them inside the rectangle so they never drift.
        corner = max(8, min(22, int(min(w, h) * 0.35)))
        bx0, by0 = int(rect.left()), int(rect.top())
        bx1, by1 = int(rect.right()), int(rect.bottom())
        bc = QColor(255, 255, 255, int(235 * a))
        p.setPen(QPen(bc, 1.4))
        p.drawLine(bx0 + 1, by0 + 1, bx0 + corner, by0 + 1)
        p.drawLine(bx0 + 1, by0 + 1, bx0 + 1, by0 + corner)
        p.drawLine(bx1 - corner, by0 + 1, bx1 - 1, by0 + 1)
        p.drawLine(bx1 - 1, by0 + 1, bx1 - 1, by0 + corner)
        p.drawLine(bx0 + 1, by1 - 1, bx0 + corner, by1 - 1)
        p.drawLine(bx0 + 1, by1 - corner, bx0 + 1, by1 - 1)
        p.drawLine(bx1 - corner, by1 - 1, bx1 - 1, by1 - 1)
        p.drawLine(bx1 - 1, by1 - corner, bx1 - 1, by1 - 1)

        # label pill, centred below the control (flips above if no room)
        if s.label:
            self._draw_label_pill(p, int(x + w / 2), int(y + h + 12), s.label, a)

    def _draw_label_pill(self, p: QPainter, cx: int, top_y: int,
                         text: str, alpha_mul: float) -> None:
        """A floating 'what it's doing' pill anchored at (cx centre, top_y)."""
        if not text or alpha_mul <= 0.01:
            return
        p.setFont(QFont("Segoe UI Variable Text", 10, QFont.Medium))
        fm = p.fontMetrics()
        pad_x, pad_y = 12, 6
        tw = fm.horizontalAdvance(text)
        w = tw + pad_x * 2 + 10           # +10 for the accent dot
        h = fm.height() + pad_y * 2
        geo = self.geometry()
        x = cx - w // 2
        y = top_y
        x = max(6, min(x, geo.width() - 6 - w))
        if y + h > geo.height() - 6:
            y = top_y - 24 - h
        # drop shadow
        for dy, sa in ((3, 26), (2, 40), (1, 54)):
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, int(sa * alpha_mul))))
            p.drawRoundedRect(x, y + dy, w, h, h // 2, h // 2)
        # glass pill
        p.setBrush(QBrush(QColor(20, 24, 32, int(228 * alpha_mul))))
        p.setPen(QPen(QColor(255, 255, 255, int(70 * alpha_mul)), 1.0))
        p.drawRoundedRect(x, y, w, h, h // 2, h // 2)
        # accent dot
        dc = QColor(RIPPLE_COLOR); dc.setAlpha(int(235 * alpha_mul))
        p.setBrush(QBrush(dc)); p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(x + pad_x, y + h // 2), 3, 3)
        # text
        p.setPen(QPen(QColor(240, 242, 248, int(245 * alpha_mul))))
        p.drawText(QRect(x + pad_x + 10, y, w - pad_x * 2 - 10, h),
                   Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_action_label(self, p: QPainter, cx: int, cy: int,
                            now_ms: int) -> None:
        """Render the floating action-name pill under the cursor."""
        if not self._action_label_text:
            return
        age = now_ms - self._action_label_set_ms
        hold = self._ACTION_LABEL_HOLD_MS
        fade = self._ACTION_LABEL_FADE_MS
        if age >= hold + fade:
            return
        # Fade-in over first 120ms, hold, fade-out at the end
        if age < 120:
            alpha_mul = age / 120.0
        elif age > hold:
            alpha_mul = max(0.0, 1.0 - (age - hold) / fade)
        else:
            alpha_mul = 1.0

        text = self._action_label_text
        p.setFont(QFont("Segoe UI Variable Text", 10, QFont.Medium))
        fm = p.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        pad_x, pad_y = 11, 6
        w = text_w + pad_x * 2
        h = fm.height() + pad_y * 2
        # Position below the cursor, slight offset to avoid overlapping the
        # arrow's tail. Keep within screen edges.
        rect_x = cx - w // 2 + 6
        rect_y = cy + 28
        screen_geo = self.geometry()
        if rect_x < 6: rect_x = 6
        if rect_x + w > screen_geo.width() - 6:
            rect_x = screen_geo.width() - 6 - w
        if rect_y + h > screen_geo.height() - 6:
            rect_y = cy - 28 - h  # flip above if no room below

        # Subtle drop shadow under the pill
        for dy, a in ((3, 28), (2, 42), (1, 56)):
            shadow_col = QColor(0, 0, 0, int(a * alpha_mul))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(shadow_col))
            p.drawRoundedRect(rect_x, rect_y + dy, w, h, h // 2, h // 2)

        # Pill background — dark glass with subtle accent rim
        bg = QColor(20, 24, 32, int(225 * alpha_mul))
        p.setBrush(QBrush(bg))
        rim = QColor(255, 255, 255, int(70 * alpha_mul))
        p.setPen(QPen(rim, 1.0))
        p.drawRoundedRect(rect_x, rect_y, w, h, h // 2, h // 2)

        # Small accent dot on the left side — gives it the "live" feel
        dot_r = 3
        dot_x = rect_x + pad_x - 2
        dot_y = rect_y + h // 2
        dot_col = QColor(RIPPLE_COLOR); dot_col.setAlpha(int(235 * alpha_mul))
        p.setBrush(QBrush(dot_col))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(dot_x, dot_y), dot_r, dot_r)

        # Text
        fg = QColor(240, 242, 248, int(245 * alpha_mul))
        p.setPen(QPen(fg))
        text_rect = QRect(rect_x + pad_x + 6, rect_y, w - pad_x * 2 - 6, h)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

    def _click_pulse_scale(self, now_ms: int) -> float:
        """Returns a scale 0.80..1.0 right after a click, then 1.0 baseline."""
        if self._click_pulse_start_ms <= 0:
            return 1.0
        elapsed = now_ms - self._click_pulse_start_ms
        if elapsed >= self.CLICK_PULSE_MS:
            return 1.0
        t = elapsed / self.CLICK_PULSE_MS  # 0..1
        # Ease: dip down then back up. min around t=0.35
        if t < 0.35:
            local = t / 0.35
            return 1.0 - 0.20 * local  # 1.0 → 0.80
        else:
            local = (t - 0.35) / 0.65
            # ease-out cubic back to 1
            return 0.80 + 0.20 * (1 - (1 - local) ** 3)

    def _paint_cursor(self, p: QPainter, x: int, y: int,
                      scale: float = 1.0) -> None:
        """Draw the macOS-style arrow at (x, y), tip on the point.
        Adds a soft drop shadow under the cursor for depth, and supports
        click-pulse scale animation around the tip."""
        # Drop shadow (slightly offset, blurred via semi-transparent fills)
        for offset, alpha in ((3, 28), (2, 40), (1, 55)):
            self._draw_arrow_path(p, x, y + offset, scale, fill=False,
                                  shadow_alpha=alpha)
        # Main arrow
        self._draw_arrow_path(p, x, y, scale, fill=True)

    def _draw_arrow_path(self, p: QPainter, x: int, y: int, scale: float,
                         fill: bool, shadow_alpha: int = 0) -> None:
        """Draw the arrow polygon, optionally as a shadow blob."""
        def sx(dx): return x + dx * scale
        def sy(dy): return y + dy * scale
        path = QPainterPath()
        path.moveTo(sx(0), sy(0))
        path.lineTo(sx(14), sy(14))
        path.lineTo(sx(8), sy(15))
        path.lineTo(sx(12), sy(22))
        path.lineTo(sx(10), sy(23))
        path.lineTo(sx(6), sy(17))
        path.lineTo(sx(2), sy(22))
        path.lineTo(sx(0), sy(0))
        path.closeSubpath()
        if shadow_alpha:
            col = QColor(0, 0, 0, shadow_alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            p.drawPath(path)
            return
        if fill:
            p.setPen(QPen(CURSOR_OUTLINE, 1.8))
            p.setBrush(QBrush(CURSOR_COLOR))
            p.drawPath(path)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for parsing the agent's action_start args_summary
# ─────────────────────────────────────────────────────────────────────────────

_NUM_PAIR_RE = re.compile(r"(-?\d+)\s*[, ]\s*(-?\d+)")
_AT_XY_RE = re.compile(r"\bat\s+(-?\d+)\s*[, ]\s*(-?\d+)", re.IGNORECASE)


def parse_click_xy(args_summary: str) -> tuple[int, int] | None:
    """Extract (x, y) from a mouse_click action's args_summary OR from the
    action_result's output text.

    The dashboard's `args_summary` for mouse_click is often just parameter
    NAMES ("x, y, button") rather than values — so we also look at result
    text patterns like "Clicked left 1 times at 656, 525".
    """
    if not args_summary:
        return None
    s = str(args_summary)
    # "Clicked left 1 times at 656, 525" or "moved to 100, 200"
    m = _AT_XY_RE.search(s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # x=…, y=…
    mx = re.search(r"x[=:]\s*(-?\d+)", s)
    my = re.search(r"y[=:]\s*(-?\d+)", s)
    if mx and my:
        return int(mx.group(1)), int(my.group(1))
    # bare "123, 456" but ONLY if there are no letters (avoid parsing
    # field-name strings like "x, y, button" as coordinates)
    if not re.search(r"[A-Za-z]", s):
        m2 = _NUM_PAIR_RE.search(s)
        if m2:
            return int(m2.group(1)), int(m2.group(2))
    return None
