"""First-run setup — a polished, breathing key-entry window, same visual tier as the
Gemini Live bubble: dark acrylic glass, one blue accent, generous space.

Shown by run_desktop.py BEFORE the backend starts, only when the Gemini key (Live's
lifeblood) is missing. Collects the Gemini key (required) + an optional agent key
(Groq or OpenRouter), validates the Gemini one against the API, and writes them to
.env. Returns True once a working key is saved (or one already existed)."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

GEMINI_LINK = "https://aistudio.google.com/apikey"
GROQ_LINK = "https://console.groq.com/keys"
OPENROUTER_LINK = "https://openrouter.ai/keys"
ANTHROPIC_LINK = "https://console.anthropic.com/"
OPENAI_LINK = "https://platform.openai.com/api-keys"

# One accent, Live-blue. Everything else is neutral glass.
ACCENT = "#4F8DFF"
ACCENT_DIM = "#3D6FCC"
TEXT = "#F1F2F6"
MUTED = "#8B8C99"
FIELD_BG = "rgba(255,255,255,0.045)"
FIELD_BORDER = "rgba(255,255,255,0.09)"


def _env_path() -> Path:
    # Frozen .exe: .env lives next to Orynn.exe (the install folder), not in the bundle.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return Path(__file__).resolve().parents[2] / ".env"


def _gemini_key_present() -> bool:
    if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip():
        return True
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(_env_path())
        return bool((vals.get("GEMINI_API_KEY") or vals.get("GOOGLE_API_KEY") or "").strip())
    except Exception:
        return False


def _validate_gemini(key: str) -> tuple[bool, str]:
    """Confirm the key actually authenticates (cheap models.list call)."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your Gemini key to continue."
    try:
        from google import genai
        client = genai.Client(api_key=key)
        next(iter(client.models.list()), None)  # one auth'd request; raises on a bad key
        return True, ""
    except Exception as exc:
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "401" in msg or "PERMISSION" in msg.upper():
            return False, "That key didn't work — double-check you copied all of it."
        return False, f"Couldn't reach Google to verify the key ({msg[:60]})."


def _validate_agent_key(key: str) -> tuple[bool, str]:
    """Validate optional agent key if provided based on its prefix."""
    key = (key or "").strip()
    if not key:
        return True, ""
    
    import urllib.request
    
    # 1. Anthropic (Claude) key
    if key.lower().startswith("sk-ant-"):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                data=b'{"model":"claude-haiku-5-5","max_tokens":1,"messages":[{"role":"user","content":"Hi"}]}',
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status == 200, ""
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "unauthorized" in msg.lower() or "invalid" in msg.lower() or "forbidden" in msg.lower():
                return False, "Anthropic API key is invalid."
            return True, ""

    # 2. Groq key
    elif key.lower().startswith("gsk_"):
        try:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status == 200, ""
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "unauthorized" in msg.lower():
                return False, "Groq API key is invalid."
            return True, ""

    # 3. OpenAI key
    elif key.lower().startswith("sk-"):
        if key.lower().startswith("sk-or-"):
            # OpenRouter
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {key}"}
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return resp.status == 200, ""
            except Exception as exc:
                msg = str(exc)
                if "401" in msg or "unauthorized" in msg.lower():
                    return False, "OpenRouter API key is invalid."
                return True, ""
        else:
            # OpenAI
            try:
                req = urllib.request.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"}
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return resp.status == 200, ""
            except Exception as exc:
                msg = str(exc)
                if "401" in msg or "unauthorized" in msg.lower():
                    return False, "OpenAI API key is invalid."
                return True, ""

    return True, ""


def _write_env(updates: dict[str, str]) -> None:
    """Set/append KEY=value lines in .env, preserving everything else."""
    path = _env_path()
    lines: list[str] = []
    try:
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line else ""
        if k in remaining:
            out.append(f"{k}={remaining.pop(k)}")
        else:
            out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    try:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass
    for k, v in updates.items():
        os.environ[k] = v


def ensure_keys_configured() -> bool:
    """If the Gemini key is missing or not onboarded, show the setup window. True once configured."""
    try:
        from app import preferences
        onboarded = preferences.get_all().get("onboarded", False)
    except Exception:
        onboarded = False

    if _gemini_key_present() and onboarded:
        return True
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        # No Qt available — can't show the window; let the launcher proceed and the
        # backend will report the missing key the old way.
        return True
    app = QApplication.instance() or QApplication([])
    win = sys.modules[__name__].SetupWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    app.exec()
    try:
        onboarded = preferences.get_all().get("onboarded", False)
    except Exception:
        onboarded = False
    return _gemini_key_present() and onboarded


def _build():  # imports deferred so importing this module never needs Qt
    import math
    from PySide6.QtCore import Qt, Signal, QTimer, QRect
    from PySide6.QtWidgets import (
        QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QGraphicsDropShadowEffect, QStackedWidget, QComboBox,
    )
    from PySide6.QtGui import QColor, QPainter, QRadialGradient, QBrush, QPen, QPainterPath, QFont

    def _link(text: str, url: str) -> str:
        return f'<a href="{url}" style="color:{ACCENT};text-decoration:none;">{text}</a>'

    class BreathingOrbWidget(QWidget):
        """Cinematic left pane: an ambient aurora field, a tall glowing blue
        caret that breathes and blinks, and a glass command box below it where
        prompt phrases cross-fade in and out. One blue accent, lots of space."""

        def __init__(self, parent=None):
            from PySide6.QtWidgets import QSizePolicy
            import random
            super().__init__(parent)
            self.setFixedWidth(400)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

            self._time = 0.0
            self._state = "idle"

            # Live-blue palette; lerped toward state targets each frame.
            self._c1 = QColor(79, 141, 255)    # core blue
            self._c2 = QColor(143, 85, 255)    # violet edge
            self._c3 = QColor(70, 200, 255)    # cyan highlight

            rng = random.Random(42)

            # Rotating prompt phrases — cross-faded, never typed.
            self._messages = [
                "How can I help you today?",
                "Opening Spotify now\u2026",
                "Searching the web for you\u2026",
                "Setting a reminder for 3pm\u2026",
                "What would you like to do?",
                "Playing your favorite playlist\u2026",
                "I'm listening\u2026",
                "Translating that for you\u2026",
                "Tell me anything.",
                "Checking the weather now\u2026",
            ]
            self._msg_idx   = 0
            self._msg_phase = "in"      # in -> hold -> out
            self._msg_t     = 0.0
            self._msg_alpha = 0.0
            self._msg_slide = 10.0

            # Drifting aurora blobs (x, y, radius, drift speed, phase).
            self._aurora = [
                [rng.uniform(60, 340), rng.uniform(120, 460),
                 rng.uniform(150, 230), rng.uniform(0.06, 0.16), rng.uniform(0, 6.28)]
                for _ in range(3)
            ]

            # Rising dust motes (x, y, vy, size, base alpha).
            self._motes = [
                [rng.uniform(0, 400), rng.uniform(0, 640),
                 rng.uniform(6, 16), rng.uniform(0.8, 1.8), rng.uniform(0.12, 0.45)]
                for _ in range(22)
            ]

            self._pixmap = None
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(16)

        def set_state(self, state: str):
            if state in ("idle", "verifying", "error", "success"):
                self._state = state

        @staticmethod
        def _ease_out(x):
            return 1.0 - (1.0 - x) ** 3

        @staticmethod
        def _ease_in(x):
            return x * x * x

        def _tick(self):
            import math
            import random as _rand
            try:
                DT = 0.016
                self._time += DT
                t = self._time

                # Smoothly retarget the palette to the current state.
                if self._state == "idle":
                    tc1 = QColor(79 + int(18 * math.sin(t * 0.6)),
                                 141 + int(26 * math.sin(t * 0.9 + 1)), 255)
                    tc2 = QColor(143 + int(24 * math.sin(t * 0.75 + 3)),
                                 85 + int(18 * math.sin(t * 0.45 + 2)), 255)
                    tc3 = QColor(62 + int(14 * math.sin(t * 0.6 + 1.5)),
                                 198 + int(14 * math.sin(t * 0.65)), 255)
                elif self._state == "verifying":
                    tc1, tc2, tc3 = QColor(255, 176, 32), QColor(224, 96, 0), QColor(255, 220, 60)
                elif self._state == "error":
                    tc1, tc2, tc3 = QColor(255, 70, 70), QColor(180, 20, 20), QColor(255, 110, 90)
                else:
                    tc1, tc2, tc3 = QColor(0, 220, 140), QColor(0, 160, 90), QColor(60, 255, 180)

                def lp(c, tc):
                    return QColor(int(c.red() + (tc.red() - c.red()) * 0.06),
                                  int(c.green() + (tc.green() - c.green()) * 0.06),
                                  int(c.blue() + (tc.blue() - c.blue()) * 0.06))
                self._c1 = lp(self._c1, tc1)
                self._c2 = lp(self._c2, tc2)
                self._c3 = lp(self._c3, tc3)

                # Cross-fading prompt phrases.
                self._msg_t += DT
                if self._msg_phase == "in":
                    p = min(1.0, self._msg_t / 0.55)
                    self._msg_alpha = self._ease_out(p)
                    self._msg_slide = 10.0 * (1.0 - self._ease_out(p))
                    if p >= 1.0:
                        self._msg_phase, self._msg_t = "hold", 0.0
                elif self._msg_phase == "hold":
                    self._msg_alpha, self._msg_slide = 1.0, 0.0
                    if self._msg_t > 2.4:
                        self._msg_phase, self._msg_t = "out", 0.0
                else:
                    p = min(1.0, self._msg_t / 0.5)
                    self._msg_alpha = 1.0 - self._ease_in(p)
                    self._msg_slide = -10.0 * self._ease_in(p)
                    if p >= 1.0:
                        self._msg_idx = (self._msg_idx + 1) % len(self._messages)
                        self._msg_phase, self._msg_t = "in", 0.0

                # Drift aurora, rise motes.
                for a in self._aurora:
                    a[4] += a[3] * DT
                for m in self._motes:
                    m[1] -= m[2] * DT * 3.0
                    if m[1] < -8:
                        m[1] = 650.0
                        m[0] = _rand.uniform(0, 400)

                self._render_to_pixmap()
                self.update()
            except Exception as e:
                import sys
                print(f"[Orb] tick error: {e}", file=sys.stderr)

        def paintEvent(self, event):
            if self._pixmap is None:
                return
            p = QPainter(self)
            p.drawPixmap(0, 0, self._pixmap)

        def _render_to_pixmap(self):
            from PySide6.QtGui import QLinearGradient, QRadialGradient, QPixmap, QFontMetrics
            import math

            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            if self._pixmap is None or self._pixmap.width() != w or self._pixmap.height() != h:
                self._pixmap = QPixmap(w, h)
            self._pixmap.fill(Qt.transparent)

            p = QPainter(self._pixmap)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)

            # Clip to the card's rounded-left corners (right edge is square).
            clip = QPainterPath()
            clip.moveTo(w, 0); clip.lineTo(20, 0)
            clip.arcTo(0, 0, 40, 40, 90, 90)
            clip.lineTo(0, h - 20)
            clip.arcTo(0, h - 40, 40, 40, 180, 90)
            clip.lineTo(w, h)
            clip.closeSubpath()
            p.setClipPath(clip)

            t   = self._time
            cx  = w / 2
            c1, c2, c3 = self._c1, self._c2, self._c3

            # --- Deep background: vertical gradient into near-black ---
            bg = QLinearGradient(0, 0, 0, h)
            bg.setColorAt(0.0, QColor(9, 11, 24))
            bg.setColorAt(0.5, QColor(6, 7, 16))
            bg.setColorAt(1.0, QColor(3, 3, 9))
            p.fillRect(QRect(0, 0, w, h), QBrush(bg))

            # --- Ambient: a couple of soft, slow blue glows for quiet depth ---
            for a in self._aurora:
                ax = a[0] + math.cos(a[4]) * 26
                ay = a[1] + math.sin(a[4] * 0.8) * 22
                rad = a[2] + 14 * math.sin(a[4] * 1.3)
                glow = QRadialGradient(ax, ay, rad)
                glow.setColorAt(0.0, QColor(c2.red(), c2.green(), c2.blue(), 13))
                glow.setColorAt(0.5, QColor(c1.red(), c1.green(), c1.blue(), 6))
                glow.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(int(ax - rad), int(ay - rad), int(rad * 2), int(rad * 2))

            # --- A few slow dust motes for life (very faint) ---
            for m in self._motes:
                ma = int(max(0, m[4] * (0.5 + 0.5 * math.sin(t * 1.0 + m[0]))) * 55)
                if ma <= 0:
                    continue
                p.setBrush(QBrush(QColor(170, 195, 235, ma)))
                p.setPen(Qt.NoPen)
                p.drawEllipse(int(m[0] - m[3] / 2), int(m[1] - m[3] / 2), int(m[3]), int(m[3]))

            # --- Voice pill: a soft glass capsule holding a dot-matrix waveform
            # and the spoken phrase. Voice-first, so there is no send button. ---
            BOX_MX = 44
            box_x  = float(BOX_MX)
            box_w  = float(w - 2 * BOX_MX)
            box_h  = 50.0
            box_y  = float(int(h * 0.46 - box_h / 2))
            radius = box_h / 2.0
            row_cy = box_y + box_h / 2.0

            box_path = QPainterPath()
            box_path.addRoundedRect(box_x, box_y, box_w, box_h, radius, radius)
            fill = QLinearGradient(0, box_y, 0, box_y + box_h)
            fill.setColorAt(0.0, QColor(255, 255, 255, 13))
            fill.setColorAt(1.0, QColor(255, 255, 255, 6))
            p.fillPath(box_path, QBrush(fill))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
            p.drawPath(box_path)

            msg = self._messages[self._msg_idx]
            ta  = int(max(0.0, min(1.0, self._msg_alpha)) * 255)

            font = p.font()
            font.setPointSize(12)
            font.setFamily("Segoe UI")
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.2)
            p.setFont(font)

            fm = QFontMetrics(font)
            text_w_px = fm.horizontalAdvance(msg)

            # Mini voice waveform (mirrors the capsule's dot-matrix bars).
            WF_COLS = 5
            WF_GAP  = 6.0
            WF_W    = (WF_COLS - 1) * WF_GAP
            GAP_TW  = 16.0          # gap between waveform and text
            group_w = WF_W + GAP_TW + text_w_px
            gx0     = cx - group_w / 2.0

            # Waveform dots/bars — animated, soft cyan, gently breathing.
            for i in range(WF_COLS):
                env = math.sin((i / (WF_COLS - 1)) * math.pi)        # tallest in middle
                amp = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 3.4 + i * 0.9))
                bh  = 5 + 13 * env * amp
                bx  = gx0 + i * WF_GAP
                ba  = int(150 + 90 * env * amp)
                bar = QPainterPath()
                bar.addRoundedRect(bx - 1.4, row_cy - bh / 2, 2.8, bh, 1.4, 1.4)
                p.fillPath(bar, QBrush(QColor(c3.red(), c3.green(), c3.blue(), ba)))

            # The spoken phrase, cross-fading next to the waveform.
            if ta > 0:
                text_x = int(gx0 + WF_W + GAP_TW)
                ty     = int(row_cy - 11 + self._msg_slide * 0.4)
                p.setPen(QColor(222, 230, 244, ta))
                p.drawText(QRect(text_x, ty, text_w_px + 6, 22),
                           Qt.AlignVCenter | Qt.AlignLeft, msg)

            # --- Vignette for depth (soft) ---
            vg = QRadialGradient(cx, h * 0.46, max(w, h) * 0.80)
            vg.setColorAt(0.0,  QColor(0, 0, 0, 0))
            vg.setColorAt(0.55, QColor(0, 0, 0, 0))
            vg.setColorAt(1.0,  QColor(0, 0, 0, 130))
            p.fillRect(QRect(0, 0, w, h), QBrush(vg))

            # --- Wordmark ---
            p.setPen(QColor(236, 240, 250, 200))
            font.setPointSize(16); font.setBold(True)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.2)
            p.setFont(font)
            p.drawText(0, h - 70, w, 28, Qt.AlignCenter, "O R Y N N")

            p.end()

    class _SetupWindow(QWidget):
        _validated = Signal(bool, str)

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Welcome to Orynn")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedSize(960, 640)
            self._drag = None

            # Glass card (rounded surface; the window itself is transparent so corners stay clean).
            card = QFrame(self)
            card.setObjectName("card")
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(48)
            shadow.setColor(QColor(0, 0, 0, 170))
            shadow.setOffset(0, 12)
            card.setGraphicsEffect(shadow)

            # Dual-pane horizontal layout inside the card
            card_lay = QHBoxLayout(card)
            card_lay.setContentsMargins(0, 0, 0, 0)
            card_lay.setSpacing(0)

            # Left Pane: Animated breathing orb
            self.orb_pane = BreathingOrbWidget(self)
            self.orb_pane.setObjectName("orb_pane")
            card_lay.addWidget(self.orb_pane)

            # Right Pane: Onboarding settings form
            right_pane = QWidget()
            right_pane.setObjectName("right_pane")
            right_lay = QVBoxLayout(right_pane)
            right_lay.setContentsMargins(36, 26, 36, 24)
            right_lay.setSpacing(6)
            card_lay.addWidget(right_pane)

            # Header: blue orb + wordmark, then subtitle.
            head = QHBoxLayout()
            head.setSpacing(12)
            orb = QLabel("●")
            orb.setStyleSheet(f"color:{ACCENT};font-size:16px;")
            name = QLabel("Welcome")
            name.setStyleSheet(f"color:{TEXT};font-size:23px;font-weight:700;")
            head.addWidget(orb)
            head.addWidget(name)
            head.addStretch(1)

            self.btn_close = QPushButton("✕")
            self.btn_close.setObjectName("close_btn")
            self.btn_close.setCursor(Qt.PointingHandCursor)
            self.btn_close.clicked.connect(self.close)
            head.addWidget(self.btn_close)

            right_lay.addLayout(head)
            
            self.sub = QLabel("Let's get you set up — this only takes a moment.")
            self.sub.setStyleSheet(f"color:{MUTED};font-size:13px;")
            right_lay.addWidget(self.sub)
            right_lay.addSpacing(16)

            # Stacked widget for onboarding steps
            self.stacked = QStackedWidget()
            right_lay.addWidget(self.stacked)

            # -------------------------------------------------------------
            # Page 1: Keys
            # -------------------------------------------------------------
            self.page_keys = QWidget()
            keys_lay = QVBoxLayout(self.page_keys)
            keys_lay.setContentsMargins(0, 0, 0, 0)
            keys_lay.setSpacing(8)

            # Gemini field (required)
            keys_lay.addWidget(self._field_label("GEMINI API KEY", "required", ACCENT))
            gemini_container, self.gemini = self._create_input_group("Paste your key here", is_password=True)
            keys_lay.addWidget(gemini_container)
            keys_lay.addWidget(self._helper(
                "Powers Orynn's voice & vision. " + _link("Get one free →", GEMINI_LINK)))
            keys_lay.addSpacing(18)

            # Agent field (optional)
            keys_lay.addWidget(self._field_label("AGENT KEY", "optional", MUTED))
            agent_container, self.agent = self._create_input_group("Agent API key (Groq, OpenRouter, Claude, OpenAI)", is_password=True)
            keys_lay.addWidget(agent_container)
            keys_lay.addWidget(self._helper(
                "Lets Orynn do bigger jobs on your PC. Get keys: &nbsp;&nbsp;"
                + _link("Groq &rarr;", GROQ_LINK) + " &nbsp;&nbsp;&nbsp;&middot;&nbsp;&nbsp;&nbsp; "
                + _link("OpenRouter &rarr;", OPENROUTER_LINK) + " &nbsp;&nbsp;&nbsp;&middot;&nbsp;&nbsp;&nbsp; "
                + _link("Claude &rarr;", ANTHROPIC_LINK) + " &nbsp;&nbsp;&nbsp;&middot;&nbsp;&nbsp;&nbsp; "
                + _link("OpenAI &rarr;", OPENAI_LINK)))
            keys_lay.addSpacing(20)

            # Continue button
            self.btn_keys = QPushButton("Continue")
            self.btn_keys.setObjectName("primary")
            self.btn_keys.setCursor(Qt.PointingHandCursor)
            self.btn_keys.setFixedHeight(46)
            self.btn_keys.clicked.connect(self._on_keys_continue)
            keys_lay.addWidget(self.btn_keys)

            self.stacked.addWidget(self.page_keys)

            # -------------------------------------------------------------
            # Page 2: Preferences
            # -------------------------------------------------------------
            self.page_prefs = QWidget()
            prefs_lay = QVBoxLayout(self.page_prefs)
            prefs_lay.setContentsMargins(0, 0, 0, 0)
            prefs_lay.setSpacing(6)

            prefs_lay.addWidget(self._field_label("UX PREFERENCES", "configure", ACCENT))
            prefs_lay.addSpacing(2)

            # Wake word input
            wake_lay = QHBoxLayout()
            wake_lay.setContentsMargins(0, 0, 0, 0)
            wake_lay.setSpacing(10)
            
            lbl_wake = QLabel("Wake Name:")
            lbl_wake.setStyleSheet(f"color:{MUTED};font-size:13px;font-weight:600;")
            
            self.wake_word = self._input("What should the agent respond to?")
            self.wake_word.setText("Orynn")
            
            wake_lay.addWidget(lbl_wake)
            wake_lay.addWidget(self.wake_word, 1)
            prefs_lay.addLayout(wake_lay)
            prefs_lay.addSpacing(4)

            # Speak replies toggle
            self.btn_speak = QPushButton("○  Speak replies out loud")
            self.btn_speak.setCheckable(True)
            self.btn_speak.setObjectName("toggle")
            self.btn_speak.clicked.connect(self._on_speak_toggled)
            prefs_lay.addWidget(self.btn_speak)

            # Voice input toggle
            self.btn_mic = QPushButton("○  Voice control (Microphone)")
            self.btn_mic.setCheckable(True)
            self.btn_mic.setObjectName("toggle")
            self.btn_mic.clicked.connect(self._on_mic_toggled)
            prefs_lay.addWidget(self.btn_mic)
            
            prefs_lay.addSpacing(10)

            # Permissions Disclosure
            prefs_lay.addWidget(self._field_label("OS PERMISSIONS", "required for features", ACCENT))
            perm_note = QLabel(
                "• <b>Microphone</b>: Required if you enable Voice Control.<br>"
                "• <b>Desktop / Accessibility</b>: Required for the agent to control your screen."
            )
            perm_note.setStyleSheet(f"color:{MUTED};font-size:12px;background:rgba(255,255,255,0.03);padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.05);")
            perm_note.setWordWrap(True)
            prefs_lay.addWidget(perm_note)

            prefs_lay.addSpacing(6)

            # Theme selection
            prefs_lay.addWidget(self._field_label("APP THEME", "appearance", MUTED))
            self.combo_theme = QComboBox()
            self.combo_theme.addItems(["System Default (Auto)", "Dark Mode", "Light Mode"])
            self.combo_theme.setCurrentIndex(0)
            prefs_lay.addWidget(self.combo_theme)
            
            prefs_lay.addSpacing(6)

            # Default Mode selection
            prefs_lay.addWidget(self._field_label("DEFAULT AGENT MODE", "capabilities", MUTED))
            self.combo_mode = QComboBox()
            self.combo_mode.addItems(["Auto (Context-aware)", "Coding Specialist", "Computer Control", "Computer Only"])
            self.combo_mode.setCurrentIndex(0)
            prefs_lay.addWidget(self.combo_mode)

            prefs_lay.addSpacing(12)

            # Back / Finish navigation buttons
            nav_lay = QHBoxLayout()
            nav_lay.setSpacing(12)

            self.btn_back = QPushButton("Back")
            self.btn_back.setObjectName("secondary")
            self.btn_back.setCursor(Qt.PointingHandCursor)
            self.btn_back.setFixedHeight(46)
            self.btn_back.clicked.connect(self._on_back)
            
            self.btn_finish = QPushButton("Finish Setup")
            self.btn_finish.setObjectName("primary")
            self.btn_finish.setCursor(Qt.PointingHandCursor)
            self.btn_finish.setFixedHeight(46)
            self.btn_finish.clicked.connect(self._on_finish)

            nav_lay.addWidget(self.btn_back, 1)
            nav_lay.addWidget(self.btn_finish, 2)
            prefs_lay.addLayout(nav_lay)

            self.stacked.addWidget(self.page_prefs)

            # -------------------------------------------------------------
            # Page 3: Success
            # -------------------------------------------------------------
            self.page_success = QWidget()
            success_lay = QVBoxLayout(self.page_success)
            success_lay.setContentsMargins(0, 0, 0, 0)
            success_lay.setSpacing(12)
            success_lay.setAlignment(Qt.AlignCenter)

            success_icon = QLabel("✓")
            success_icon.setStyleSheet(f"color:{ACCENT};font-size:48px;font-weight:bold;")
            success_icon.setAlignment(Qt.AlignCenter)
            success_lay.addWidget(success_icon)

            success_title = QLabel("All Set!")
            success_title.setStyleSheet(f"color:{TEXT};font-size:20px;font-weight:700;")
            success_title.setAlignment(Qt.AlignCenter)
            success_lay.addWidget(success_title)

            success_desc = QLabel("Orynn is starting now. Press the hotkey to activate.")
            success_desc.setStyleSheet(f"color:{MUTED};font-size:13px;")
            success_desc.setAlignment(Qt.AlignCenter)
            success_desc.setWordWrap(True)
            success_lay.addWidget(success_desc)

            self.stacked.addWidget(self.page_success)

            # Status line (validation feedback)
            self.status = QLabel("")
            self.status.setStyleSheet(f"color:{MUTED};font-size:12.5px;")
            self.status.setWordWrap(True)
            right_lay.addWidget(self.status)

            outer = QVBoxLayout(self)
            outer.setContentsMargins(18, 18, 18, 18)  # room for the drop shadow
            outer.addWidget(card)
            self.setStyleSheet(self._qss())
            self._validated.connect(self._on_validated)
            self.gemini.returnPressed.connect(self._on_keys_continue)
            self.gemini.setFocus()
            self._center()

        # ---- small builders -------------------------------------------------
        def _field_label(self, title: str, tag: str, tag_color: str):
            w = QLabel(f'<span style="letter-spacing:1px;">{title}</span>'
                       f'<span style="color:{tag_color};"> · {tag}</span>')
            w.setStyleSheet(f"color:{MUTED};font-size:11px;font-weight:600;")
            return w

        def _input(self, placeholder: str):
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setMinimumHeight(44)
            e.setObjectName("field")
            return e

        def _create_input_group(self, placeholder: str, is_password: bool = False):
            container = QWidget()
            container.setObjectName("input_container")
            lay = QHBoxLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)

            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setMinimumHeight(44)
            e.setObjectName("field")
            lay.addWidget(e, 1)

            if is_password:
                e.setEchoMode(QLineEdit.Password)
                toggle = QPushButton("👁")
                toggle.setObjectName("visibility_toggle")
                toggle.setCursor(Qt.PointingHandCursor)
                toggle.setFixedSize(44, 44)

                def on_toggle():
                    if e.echoMode() == QLineEdit.Password:
                        e.setEchoMode(QLineEdit.Normal)
                        toggle.setText("🙈")
                    else:
                        e.setEchoMode(QLineEdit.Password)
                        toggle.setText("👁")
                toggle.clicked.connect(on_toggle)
                lay.addWidget(toggle)

            return container, e

        def _helper(self, html: str):
            l = QLabel(html)
            l.setOpenExternalLinks(True)
            l.setStyleSheet(f"color:{MUTED};font-size:12px;")
            l.setWordWrap(True)
            return l

        # ---- toggles --------------------------------------------------------
        def _on_speak_toggled(self, checked: bool):
            self.btn_speak.setText("●  Speak replies out loud" if checked else "○  Speak replies out loud")

        def _on_mic_toggled(self, checked: bool):
            self.btn_mic.setText("●  Voice control (Microphone)" if checked else "○  Voice control (Microphone)")

        # ---- behavior -------------------------------------------------------
        def _on_keys_continue(self):
            key = self.gemini.text().strip()
            agent = self.agent.text().strip()
            if not key:
                self._set_status("Paste your Gemini key to continue.", error=True)
                return
            self.btn_keys.setEnabled(False)
            self.btn_keys.setText("Checking…")
            self._set_status("Verifying your keys…", error=False)
            self.orb_pane.set_state("verifying")
            threading.Thread(target=self._do_validate, args=(key, agent), daemon=True).start()

        def _do_validate(self, key: str, agent: str):
            ok, msg = _validate_gemini(key)
            if not ok:
                self._validated.emit(False, msg)
                return
            if agent:
                agent_ok, agent_msg = _validate_agent_key(agent)
                if not agent_ok:
                    self._validated.emit(False, agent_msg)
                    return
            self._validated.emit(True, "")

        def _on_validated(self, ok: bool, msg: str):
            self.btn_keys.setEnabled(True)
            self.btn_keys.setText("Continue")
            if not ok:
                self.orb_pane.set_state("error")
                self._set_status(msg, error=True)
                return
            self.orb_pane.set_state("idle")
            self._set_status("", error=False)
            self.sub.setText("Customize your Orynn experience.")
            self.stacked.setCurrentWidget(self.page_prefs)

        def _on_back(self):
            self.orb_pane.set_state("idle")
            self.sub.setText("Let's get you set up — this only takes a moment.")
            self.stacked.setCurrentWidget(self.page_keys)

        def _on_finish(self):
            self.btn_finish.setEnabled(False)
            self.btn_finish.setText("Saving…")

            # 1. Write environment keys
            updates = {"GEMINI_API_KEY": self.gemini.text().strip()}
            agent = self.agent.text().strip()
            if agent:
                if agent.lower().startswith("sk-ant-"):
                    key_name = "ANTHROPIC_API_KEY"
                elif agent.lower().startswith("gsk_"):
                    key_name = "GROQ_API_KEY"
                elif agent.lower().startswith("sk-or-"):
                    key_name = "OPENROUTER_API_KEY"
                elif agent.lower().startswith("sk-"):
                    key_name = "OPENAI_API_KEY"
                else:
                    key_name = "OPENROUTER_API_KEY"
                updates[key_name] = agent
            _write_env(updates)

            # 2. Save UX Preferences
            theme_map = {0: "auto", 1: "dark", 2: "light"}
            mode_map = {0: "auto", 1: "coding", 2: "computer_use", 3: "computer"}
            
            prefs = {
                "theme": theme_map.get(self.combo_theme.currentIndex(), "auto"),
                "default_mode": mode_map.get(self.combo_mode.currentIndex(), "auto"),
                "speak_replies": self.btn_speak.isChecked(),
                "voice_input": self.btn_mic.isChecked(),
                "wake_word": self.wake_word.text().strip() or "Orynn",
                "onboarded": True,
                "first_live_run": True,
            }
            
            try:
                from app import preferences
                preferences.update(prefs)
            except Exception as exc:
                print(f"[Setup] Error saving preferences: {exc}", file=sys.stderr)

            # 3. Success page transition
            self.orb_pane.set_state("success")
            self.sub.setText("")
            self.stacked.setCurrentWidget(self.page_success)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, self.close)

        def _set_status(self, text: str, error: bool):
            self.status.setText(text)
            self.status.setStyleSheet(
                f"color:{'#FF6B6B' if error else ACCENT};font-size:12.5px;")

        # ---- chrome ---------------------------------------------------------
        def _center(self):
            from PySide6.QtGui import QGuiApplication
            scr = QGuiApplication.primaryScreen().availableGeometry()
            # Do not call adjustSize() to avoid layout-driven window reshaping/shrinking.
            # Fixed size 960x600 is already set in __init__.
            self.move(scr.center().x() - self.width() // 2,
                      scr.center().y() - self.height() // 2)

        def mousePressEvent(self, e):
            if e.button() == Qt.LeftButton:
                wh = self.windowHandle()
                if wh is not None:
                    # Native OS drag: the OS moves the window in its own loop,
                    # completely decoupled from the Qt/DWM paint loop — no freeze.
                    wh.startSystemMove()
                else:
                    # Fallback for edge cases where native handle isn't ready
                    self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e):
            # Only runs if startSystemMove() wasn't available (fallback path)
            if self._drag is not None and e.buttons() & Qt.LeftButton:
                self.move(e.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, e):
            if e.button() == Qt.LeftButton:
                self._drag = None

        def _qss(self) -> str:
            return f"""
            #card {{
                background: transparent;
                border: 1px solid rgba(255,255,255,0.06);
                border-radius: 20px;
            }}
            #orb_pane {{
                border-top-left-radius: 20px;
                border-bottom-left-radius: 20px;
            }}
            #right_pane {{
                background: #191920;
                border-top-right-radius: 20px;
                border-bottom-right-radius: 20px;
            }}
            QLineEdit#field {{
                background: {FIELD_BG};
                border: 1px solid {FIELD_BORDER};
                border-radius: 12px;
                padding: 0 14px;
                color: {TEXT};
                font-size: 14px;
                selection-background-color: {ACCENT};
            }}
            QLineEdit#field:focus {{ border: 1px solid {ACCENT}; background: rgba(79,141,255,0.07); }}
            QPushButton#primary {{
                background: {ACCENT};
                border: none; border-radius: 12px;
                color: white; font-size: 15px; font-weight: 600;
            }}
            QPushButton#primary:hover {{ background: #5C97FF; }}
            QPushButton#primary:disabled {{ background: {ACCENT_DIM}; color: rgba(255,255,255,0.7); }}
            
            QPushButton#secondary {{
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 12px;
                color: {TEXT}; font-size: 15px; font-weight: 600;
            }}
            QPushButton#secondary:hover {{ background: rgba(255,255,255,0.1); }}

            QPushButton#toggle {{
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 12px;
                color: {MUTED};
                font-size: 14px;
                min-height: 44px;
                text-align: left;
                padding-left: 14px;
            }}
            QPushButton#toggle:checked {{
                background: rgba(79,141,255,0.12);
                border: 1px solid {ACCENT};
                color: {TEXT};
            }}
            QPushButton#toggle:hover {{
                border-color: {ACCENT};
            }}

            QComboBox {{
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 12px;
                padding: 0 14px;
                color: {TEXT};
                font-size: 14px;
                min-height: 44px;
            }}
            QComboBox:focus {{ border: 1px solid {ACCENT}; background: rgba(79,141,255,0.07); }}
            QComboBox::drop-down {{
                border: none;
                width: 30px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid {MUTED};
                margin-right: 12px;
            }}
            QComboBox QAbstractItemView {{
                background: #191920;
                border: 1px solid rgba(255,255,255,0.08);
                selection-background-color: {ACCENT};
                color: {TEXT};
            }}
            QPushButton#visibility_toggle {{
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 12px;
                color: rgba(255,255,255,0.6);
                font-size: 16px;
            }}
            QPushButton#visibility_toggle:hover {{
                border-color: {ACCENT};
                color: white;
                background: rgba(79,141,255,0.07);
            }}
            QPushButton#close_btn {{
                background: transparent;
                border: none;
                color: rgba(255,255,255,0.4);
                font-size: 16px;
                font-weight: bold;
                padding: 4px;
            }}
            QPushButton#close_btn:hover {{
                color: #FF6B6B;
            }}
            """

    return _SetupWindow


# Lazily resolve the Qt class so `import setup_window` never requires PySide6.
def __getattr__(name):
    if name == "SetupWindow":
        return _build()
    raise AttributeError(name)
