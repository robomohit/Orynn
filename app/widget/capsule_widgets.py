"""Universal Dynamic Widget System for the Qt capsule.

ONE widget class that renders ANY JSON layout the LLM outputs.
No more ClutterSweeperWidget, StatusCardWidget, etc.
The LLM is the designer — it outputs a JSON spec, and this
renderer builds the native Qt UI from it.

JSON spec format:
{
    "title": "Clutter in Desktop",
    "subtitle": "5 files · 83.5 MB",
    "icon": "broom",
    "items": [
        {"icon": "file", "name": "video.mp4", "detail": "80 MB"},
        ...
    ],
    "text": "Optional body paragraph",
    "buttons": [
        {"label": "Organize", "style": "primary",
         "action": "/api/capsule/organize",
         "payload": {"folder_path": "C:/Users/.../Desktop"}},
        {"label": "Open", "style": "secondary",
         "action": "open_folder",
         "payload": {"path": "C:/..."}},
    ]
}
"""
from __future__ import annotations

import json
import subprocess
import threading

from PySide6.QtCore import (
    Qt, QByteArray, QPropertyAnimation, QEasingCurve,
    QSize, Signal, QParallelAnimationGroup, QTimer,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy, QGraphicsOpacityEffect, QProgressBar,
)

# ── Module-level API base (set by qt_shell at startup) ───────────────────────
_API_BASE = "http://127.0.0.1:8000"


def set_api_base(url: str):
    global _API_BASE
    _API_BASE = url


# ── SVG icon library ─────────────────────────────────────────────────────────
# Lucide-style 24×24 viewBox, thin monochrome strokes.
_SVG_PATHS: dict[str, str] = {
    "sparkles": '<path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z"/>'
                '<path d="M18 14l1 3 3 1-3 1-1 3-1-3-3-1 3-1z"/>',
    "folder":   '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "monitor":  '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/>'
                '<line x1="12" y1="17" x2="12" y2="21"/>',
    "clipboard":'<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'
                '<rect x="8" y="2" width="8" height="4" rx="1"/>',
    "link":     '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
                '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "zap":      '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/>',
    "broom":    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a1 1 0 0 0 0-1.4l-1.6-1.6a1 1 0 0 0-1.4 0z"/>'
                '<path d="m12 8-7.6 7.6A2 2 0 0 0 4 17v3a1 1 0 0 0 1 1h3a2 2 0 0 0 1.4-.6L17 13"/>',
    "file-text":'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
                '<polyline points="14 2 14 8 20 8"/>'
                '<line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    "image":    '<rect x="3" y="3" width="18" height="18" rx="2"/>'
                '<circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    "archive":  '<polyline points="4 8 4 21 20 21 20 8"/>'
                '<rect x="2" y="3" width="20" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
    "settings": '<circle cx="12" cy="12" r="1.5"/>'
                '<path d="M6.5 12H2"/><path d="M22 12h-4.5"/>'
                '<path d="M12 6.5V2"/><path d="M12 22v-4.5"/>',
    "file":     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
                '<polyline points="14 2 14 8 20 8"/>',
    "x":        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "check":    '<polyline points="20 6 9 17 4 12"/>',
    "trash":    '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
                '<path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    "search":   '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "globe":    '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
                '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "cpu":      '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>'
                '<path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/>'
                '<path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "alert":    '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3z"/>'
                '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "info":     '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/>'
                '<line x1="12" y1="8" x2="12.01" y2="8"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    "eye":      '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
}


def _render_icon(name: str, size: int = 18, color: str = "#B0B4BC",
                 stroke_w: float = 1.7) -> QPixmap:
    body = _SVG_PATHS.get(name, _SVG_PATHS.get("file", ""))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_w}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def _icon_label(name: str, size: int = 16, color: str = "#B0B4BC") -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(_render_icon(name, size, color))
    lbl.setFixedSize(size + 4, size + 4)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("background:transparent;border:none;")
    return lbl


# ── Design tokens ────────────────────────────────────────────────────────────
ACCENT = "#5BE0D0"
_NO_BG = "background:transparent;border:none;"

# Adaptive card text palette — flipped by the capsule when the backdrop behind
# it goes light (so answer cards stay legible on a bright liquid-glass body).
CARD_TITLE = "#FFFFFF"
CARD_SUB = "#6B7280"
CARD_BODY = "#D1D5DB"
CARD_MORE = "#4B5563"
# Card SURFACE — the capsule body is now clear see-through glass, so cards must
# carry their OWN opaque-enough surface or the desktop bleeds through the text.
CARD_BG = "rgba(28,32,42,0.55)"
CARD_BD = "rgba(255,255,255,0.10)"


def set_card_palette(light: bool) -> None:
    """Switch card text + surface between dark-mode and light-mode so answer/
    result cards stay legible on the clear-glass body over any backdrop."""
    global CARD_TITLE, CARD_SUB, CARD_BODY, CARD_MORE, CARD_BG, CARD_BD
    if light:
        CARD_TITLE = "#10131A"; CARD_SUB = "#5B6472"
        CARD_BODY = "#283340"; CARD_MORE = "#7A828F"
        # Content cards sit on clear glass, so they're near-solid for crisp text
        # over ANY backdrop (just a hint of translucency keeps them glassy).
        CARD_BG = "rgba(250,251,253,0.92)"
        CARD_BD = "rgba(20,24,32,0.13)"
    else:
        CARD_TITLE = "#FFFFFF"; CARD_SUB = "#6B7280"
        CARD_BODY = "#D1D5DB"; CARD_MORE = "#4B5563"
        CARD_BG = "rgba(22,26,35,0.85)"
        CARD_BD = "rgba(255,255,255,0.12)"

_FILE_ICON_MAP = {
    "pdf": "file-text", "doc": "file-text", "docx": "file-text",
    "txt": "file-text", "md": "file-text",
    "png": "image", "jpg": "image", "jpeg": "image", "svg": "image",
    "gif": "image", "webp": "image",
    "zip": "archive", "tar": "archive", "gz": "archive", "rar": "archive",
    "7z": "archive",
    "exe": "settings", "msi": "settings", "bat": "settings",
    "mp4": "eye", "mkv": "eye", "avi": "eye", "mov": "eye",
    "mp3": "zap", "wav": "zap", "flac": "zap",
    "py": "terminal", "js": "terminal", "ts": "terminal",
    "html": "globe", "css": "globe",
}


def _guess_icon(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _FILE_ICON_MAP.get(ext, "file")


# ── Base animated card ───────────────────────────────────────────────────────
class CapsuleCard(QWidget):
    """Animated container — every widget that spawns in the capsule inherits this."""
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_fx)
        # CRITICAL: a plain QWidget subclass ignores its stylesheet `background`
        # unless WA_StyledBackground is enabled. Without this the card stayed
        # fully transparent on the clear-glass body (text bled into the desktop).
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Style by objectName so the surface applies to the card ONLY (not its
        # child labels/buttons) and works for the DynamicWidget subclass too.
        self.setObjectName("capsuleCard")
        self.setStyleSheet(
            "#capsuleCard{"
            f"  background: {CARD_BG};"
            f"  border: 1px solid {CARD_BD};"
            "  border-radius: 14px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def animate_in(self):
        # Fade only — no max-height tween. The expand animation read sizeHint()
        # before Qt's async layout had reflowed the card, so body text got
        # clipped. Pure fade-in still reads as "alive" and never clips.
        self.setMaximumHeight(16777215)
        fade = QPropertyAnimation(self._opacity_fx, b"opacity")
        fade.setDuration(260); fade.setStartValue(0.0); fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_in = fade
        fade.start()

    def animate_out(self):
        fade = QPropertyAnimation(self._opacity_fx, b"opacity")
        fade.setDuration(220); fade.setStartValue(1.0); fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self._dismiss)
        self._fade_out = fade; fade.start()

    def _dismiss(self):
        self.dismissed.emit(); self.deleteLater()


# ── Capability bar ───────────────────────────────────────────────────────────
class CapabilityBar(QWidget):
    actionTriggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_NO_BG); self.setFixedHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        layout.addStretch()
        for icon_name, tip in [("sparkles", "Auto"), ("folder", "Files"),
                                ("monitor", "Screen"), ("clipboard", "Clipboard"),
                                ("link", "Links"), ("zap", "Actions")]:
            btn = QPushButton()
            btn.setIcon(QIcon(_render_icon(icon_name, 16, "#A0A6B1")))
            btn.setIconSize(QSize(16, 16)); btn.setToolTip(tip)
            btn.setFixedSize(36, 32); btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:8px;}"
                "QPushButton:hover{background-color: rgba(255, 255, 255, 15); border-radius: 8px;}"
                "QPushButton:pressed{background-color: rgba(255, 255, 255, 8);}"
            )
            btn.clicked.connect(lambda checked=False, name=icon_name: self.actionTriggered.emit(name))
            layout.addWidget(btn)
        layout.addStretch()


# ── Small helpers ────────────────────────────────────────────────────────────
def _dismiss_btn() -> QPushButton:
    btn = QPushButton()
    btn.setIcon(QIcon(_render_icon("x", 12, "#6B7280")))
    btn.setIconSize(QSize(12, 12)); btn.setFixedSize(26, 26)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton{background:rgba(255,255,255,0.05);border:none;border-radius:13px;}"
        "QPushButton:hover{background:rgba(239,68,68,0.2);}")
    return btn


def _divider() -> QFrame:
    d = QFrame(); d.setFixedHeight(1)
    d.setStyleSheet("background:rgba(255,255,255,0.06);border:none;")
    return d


# ═════════════════════════════════════════════════════════════════════════════
# ██  PILLAR 2 — UNIVERSAL DYNAMIC WIDGET RENDERER                         ██
# ═════════════════════════════════════════════════════════════════════════════
class DynamicWidget(CapsuleCard):
    """Renders ANY JSON layout the LLM outputs as a native Qt widget.

    The LLM is the designer. It outputs a JSON spec with:
        title, subtitle, icon, items[], text, buttons[], progress
    This class parses that spec and builds the UI.
    """

    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._build_ui(spec)

    def _build_ui(self, s: dict):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(0)

        # ── Header: icon + title + subtitle + dismiss ──
        title_text = s.get("title", "AI Computer")
        subtitle_text = s.get("subtitle", "")
        icon_name = s.get("icon", "sparkles")

        hdr = QHBoxLayout(); hdr.setSpacing(12)
        hdr.addWidget(_icon_label(icon_name, 20, ACCENT))

        col = QVBoxLayout(); col.setSpacing(1)
        title = QLabel(title_text)
        title.setFont(QFont("Segoe UI Variable Display", 13, QFont.DemiBold))
        title.setStyleSheet(f"color:{CARD_TITLE};{_NO_BG}")
        col.addWidget(title)

        if subtitle_text:
            sub = QLabel(subtitle_text)
            sub.setFont(QFont("Segoe UI", 9))
            sub.setStyleSheet(f"color:{CARD_SUB};{_NO_BG}")
            col.addWidget(sub)

        hdr.addLayout(col, 1)
        dismiss = _dismiss_btn(); dismiss.clicked.connect(self.animate_out)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── Items list ──
        items = s.get("items", [])
        if items:
            lay.addSpacing(12); lay.addWidget(_divider()); lay.addSpacing(8)
            shown = items[:10]
            for item in shown:
                lay.addWidget(self._make_item_row(item))
            if len(items) > 10:
                more = QLabel(f"+{len(items) - 10} more")
                more.setFont(QFont("Segoe UI", 9)); more.setAlignment(Qt.AlignCenter)
                more.setStyleSheet(f"color:{CARD_MORE};{_NO_BG}"); more.setFixedHeight(24)
                lay.addWidget(more)

        # ── Text body ──
        body_text = s.get("text", "")
        if body_text:
            lay.addSpacing(10); lay.addWidget(_divider()); lay.addSpacing(10)
            body = QLabel(body_text)
            body.setWordWrap(True)
            body.setFont(QFont("Segoe UI", 10))
            body.setStyleSheet(f"color:{CARD_BODY};{_NO_BG}")
            body.setMaximumHeight(300)
            lay.addWidget(body)

        # ── Progress bar ──
        progress = s.get("progress")
        if progress is not None:
            lay.addSpacing(12)
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            pbar.setValue(int(float(progress) * 100))
            pbar.setFixedHeight(6)
            pbar.setTextVisible(False)
            pbar.setStyleSheet(
                f"QProgressBar{{background:rgba(255,255,255,0.06);border:none;border-radius:3px;}}"
                f"QProgressBar::chunk{{background:{ACCENT};border-radius:3px;}}"
            )
            lay.addWidget(pbar)

        # ── Status label for action feedback ──
        self._status = QLabel("")
        self._status.setFont(QFont("Segoe UI", 9))
        self._status.setStyleSheet(f"color:{ACCENT};{_NO_BG}")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.hide()
        lay.addWidget(self._status)

        # ── Buttons ──
        buttons = s.get("buttons", [])
        if buttons:
            lay.addSpacing(12)
            btns_layout = QHBoxLayout(); btns_layout.setSpacing(8)
            for bspec in buttons[:4]:  # max 4 buttons
                btn = self._make_button(bspec)
                stretch = 0 if bspec.get("style") == "icon" else 1
                btns_layout.addWidget(btn, stretch)
            lay.addLayout(btns_layout)

    def _make_item_row(self, item: dict) -> QWidget:
        """Build one row of a list widget."""
        row_w = QWidget()
        row_w.setFixedHeight(38)
        row_w.setStyleSheet(
            "QWidget{background:transparent;border:none;border-radius:8px;}"
            "QWidget:hover{background:rgba(255,255,255,0.04);}")
        row = QHBoxLayout(row_w)
        row.setContentsMargins(8, 0, 12, 0); row.setSpacing(10)

        name = item.get("name", "")
        icon = item.get("icon") or _guess_icon(name)
        detail = item.get("detail", item.get("subtitle", ""))

        row.addWidget(_icon_label(icon, 15, "#7A7F88"))
        nm = QLabel(name); nm.setFont(QFont("Segoe UI Variable Text", 10))
        nm.setStyleSheet(f"color:#E2E4E8;{_NO_BG}"); row.addWidget(nm, 1)

        if detail:
            dt = QLabel(str(detail)); dt.setFont(QFont("Segoe UI", 9))
            dt.setStyleSheet(f"color:#6B7280;{_NO_BG}"); row.addWidget(dt)
        return row_w

    def _make_button(self, bspec: dict) -> QPushButton:
        """Build one action button from a JSON spec."""
        label = bspec.get("label", "")
        style = bspec.get("style", "secondary")
        action = bspec.get("action", "")
        payload = bspec.get("payload", {})
        icon_name = bspec.get("icon", "")

        btn = QPushButton(f"  {label}" if label else "")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("Segoe UI Variable Text", 10,
                          QFont.DemiBold if style == "primary" else QFont.Normal))
        btn.setFixedHeight(38)

        if icon_name:
            ic = "#0A1A16" if style == "primary" else "#B0B4BC"
            btn.setIcon(QIcon(_render_icon(icon_name, 14, ic, 2.0)))
            btn.setIconSize(QSize(14, 14))

        if style == "primary":
            btn.setStyleSheet(
                f"QPushButton{{background:{ACCENT};color:#0A1A16;border:none;"
                f"border-radius:10px;padding:0 18px;}}"
                f"QPushButton:hover{{background:#6FEDE0;}}"
                f"QPushButton:disabled{{background:#3A5A56;color:#1A2A26;}}")
        elif style == "danger":
            if not label:  # icon-only danger button
                btn.setFixedSize(38, 38)
            btn.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,0.04);"
                "border:1px solid rgba(255,255,255,0.06);border-radius:10px;"
                "padding:0 12px;color:#D1D5DB;}"
                "QPushButton:hover{background:rgba(239,68,68,0.15);color:#FCA5A5;}")
        else:  # secondary
            btn.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,0.06);color:#D1D5DB;"
                "border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:0 18px;}"
                "QPushButton:hover{background:rgba(255,255,255,0.10);color:#FFFFFF;}")

        # Wire the action
        btn.clicked.connect(lambda _, a=action, p=payload, b=btn: self._execute_action(a, p, b))
        return btn

    # ── Action execution ──
    def _set_status(self, msg: str, color: str = ACCENT):
        self._status.setStyleSheet(f"color:{color};{_NO_BG}")
        self._status.setText(msg); self._status.show()

    def _execute_action(self, action: str, payload: dict, btn: QPushButton):
        """Route a button click to the right handler."""
        if not action:
            return

        # Local actions
        if action == "dismiss":
            self.animate_out(); return
        if action == "open_folder":
            payload_obj = payload if isinstance(payload, dict) else {"path": str(payload or "")}
            path = payload_obj.get("path", payload_obj.get("folder_path", ""))
            if path:
                subprocess.Popen(["explorer", path])
            return
        if action == "open_url":
            payload_obj = payload if isinstance(payload, dict) else {"url": str(payload or "")}
            url = payload_obj.get("url", "")
            if url:
                import webbrowser; webbrowser.open(url)
            return

        # HTTP actions — POST to backend
        if action.startswith("/"):
            btn.setEnabled(False)
            self._set_status("Working...")
            threading.Thread(
                target=self._do_http_action,
                args=(action, payload, btn),
                daemon=True,
            ).start()

    def _do_http_action(self, endpoint: str, payload: dict, btn: QPushButton):
        try:
            import httpx
            url = f"{_API_BASE}{endpoint}"
            with httpx.Client(timeout=30) as client:
                client.post(f"{_API_BASE}/api/session")
                r = client.post(url, json=payload)
            result = r.json() if r.status_code < 400 else {"error": r.text}

            if "error" in result:
                QTimer.singleShot(0, lambda: self._set_status(
                    f"Error: {result['error'][:80]}", "#EF4444"))
            else:
                count = result.get("count", "")
                msg = result.get("message", f"✓ Done{f' ({count} items)' if count else ''}")
                QTimer.singleShot(0, lambda: self._set_status(msg, ACCENT))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._set_status(f"Error: {e}", "#EF4444"))
        QTimer.singleShot(0, lambda: btn.setEnabled(True))


# ═════════════════════════════════════════════════════════════════════════════
# ██  WIDGET FACTORY — single entry point                                   ██
# ═════════════════════════════════════════════════════════════════════════════
def create_widget(spec: dict, parent=None) -> CapsuleCard | None:
    """Create a DynamicWidget from ANY JSON spec the LLM or backend provides.

    This is the ONLY factory. No registry, no class lookup.
    The spec IS the widget.
    """
    if not spec or not isinstance(spec, dict):
        return None
    return DynamicWidget(spec, parent=parent)
