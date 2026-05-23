"""Native Qt widgets for the floating capsule.

Pure QWidget implementations — NO QWebEngineView, no HTML, no Tailwind.
Every icon is SVG-rendered. Every button executes real actions.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import (
    Qt, QByteArray, QPropertyAnimation, QEasingCurve,
    QSize, Signal, QParallelAnimationGroup, QTimer, QPoint,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy, QGraphicsOpacityEffect,
)

# ── Module-level API base (set by qt_shell at startup) ───────────────────────
_API_BASE = "http://127.0.0.1:8000"


def set_api_base(url: str):
    global _API_BASE
    _API_BASE = url


# ── SVG icon library ─────────────────────────────────────────────────────────
_SVG_PATHS = {
    "sparkles": '<path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z"/>'
                '<path d="M18 14l1 3 3 1-3 1-1 3-1-3-3-1 3-1z"/>',
    "folder":   '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
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
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "loader":   '<path d="M12 2v4"/><path d="m16.2 7.8 2.9-2.9"/><path d="M18 12h4"/><path d="m16.2 16.2 2.9 2.9"/>'
                '<path d="M12 18v4"/><path d="m4.9 19.1 2.9-2.9"/><path d="M2 12h4"/><path d="m4.9 4.9 2.9 2.9"/>',
}


def _render_icon(name: str, size: int = 18, color: str = "#B0B4BC",
                 stroke_w: float = 1.7) -> QPixmap:
    body = _SVG_PATHS.get(name, "")
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


ACCENT = "#5BE0D0"
_NO_BG = "background:transparent;border:none;"


# ── Base card ────────────────────────────────────────────────────────────────
class CapsuleCard(QWidget):
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_fx)
        self.setStyleSheet(
            "CapsuleCard{"
            "  background: rgba(255, 255, 255, 0.04);"
            "  border: 1px solid rgba(255, 255, 255, 0.07);"
            "  border-radius: 14px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def animate_in(self):
        self.setMaximumHeight(0)
        target = self.sizeHint().height() + 30
        fade = QPropertyAnimation(self._opacity_fx, b"opacity")
        fade.setDuration(400); fade.setStartValue(0.0); fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        expand = QPropertyAnimation(self, b"maximumHeight")
        expand.setDuration(480); expand.setStartValue(0); expand.setEndValue(target)
        expand.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(fade)
        self._anim_group.addAnimation(expand)
        self._anim_group.start()

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_NO_BG); self.setFixedHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        layout.addStretch()
        for icon_name, tip in [("sparkles","Auto"),("folder","Files"),
                                ("monitor","Screen"),("clipboard","Clipboard"),
                                ("link","Links"),("zap","Actions")]:
            btn = QPushButton()
            btn.setIcon(QIcon(_render_icon(icon_name, 16, "#8A8F98")))
            btn.setIconSize(QSize(16, 16)); btn.setToolTip(tip)
            btn.setFixedSize(36, 32); btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:8px;}"
                "QPushButton:hover{background:rgba(255,255,255,0.07);}")
            layout.addWidget(btn)
        layout.addStretch()


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_dismiss_btn() -> QPushButton:
    btn = QPushButton()
    btn.setIcon(QIcon(_render_icon("x", 12, "#6B7280")))
    btn.setIconSize(QSize(12, 12)); btn.setFixedSize(26, 26)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton{background:rgba(255,255,255,0.05);border:none;border-radius:13px;}"
        "QPushButton:hover{background:rgba(239,68,68,0.2);}")
    return btn

def _thin_divider() -> QFrame:
    d = QFrame(); d.setFixedHeight(1)
    d.setStyleSheet("background:rgba(255,255,255,0.06);border:none;")
    return d


# ── File icon map ────────────────────────────────────────────────────────────
_FILE_ICON_MAP = {
    "pdf": "file-text", "doc": "file-text", "txt": "file-text", "md": "file-text",
    "png": "image", "jpg": "image", "jpeg": "image", "svg": "image", "gif": "image",
    "zip": "archive", "tar": "archive", "gz": "archive", "rar": "archive",
    "exe": "settings", "msi": "settings", "bat": "settings",
}

def _guess_file_icon(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _FILE_ICON_MAP.get(ext, "file")


class _FileRow(QWidget):
    def __init__(self, name: str, size: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setStyleSheet(
            "QWidget{background:transparent;border:none;border-radius:8px;}"
            "QWidget:hover{background:rgba(255,255,255,0.04);}")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 12, 0); row.setSpacing(10)
        row.addWidget(_icon_label(_guess_file_icon(name), 15, "#7A7F88"))
        nm = QLabel(name); nm.setFont(QFont("Segoe UI Variable Text", 10))
        nm.setStyleSheet(f"color:#E2E4E8;{_NO_BG}"); row.addWidget(nm, 1)
        sz = QLabel(size); sz.setFont(QFont("Segoe UI", 9))
        sz.setStyleSheet(f"color:#6B7280;{_NO_BG}"); row.addWidget(sz)


# ── Clutter Sweeper — REAL, functional widget ────────────────────────────────
class ClutterSweeperWidget(CapsuleCard):
    """Shows REAL files from the OS. Buttons execute REAL actions."""

    def __init__(self, data: dict | None = None, parent=None):
        super().__init__(parent)
        data = data or {}
        folder = data.get("folder", "Downloads")
        self._folder_path = data.get("folder_path", "")
        self._files = data.get("files", [])
        total = data.get("total_size", "0 B")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(0)

        # header
        hdr = QHBoxLayout(); hdr.setSpacing(12)
        hdr.addWidget(_icon_label("broom", 20, ACCENT))
        col = QVBoxLayout(); col.setSpacing(1)
        title = QLabel(f"Clutter in {folder}")
        title.setFont(QFont("Segoe UI Variable Display", 13, QFont.DemiBold))
        title.setStyleSheet(f"color:#FFFFFF;{_NO_BG}"); col.addWidget(title)
        sub = QLabel(f"{len(self._files)} files  ·  {total} total")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet(f"color:#6B7280;{_NO_BG}"); col.addWidget(sub)
        hdr.addLayout(col, 1)
        dismiss = _make_dismiss_btn(); dismiss.clicked.connect(self.animate_out)
        hdr.addWidget(dismiss); lay.addLayout(hdr)

        lay.addSpacing(12); lay.addWidget(_thin_divider()); lay.addSpacing(8)

        # file list — real files from OS
        shown = self._files[:8]
        for f in shown:
            lay.addWidget(_FileRow(f["name"], f["size"]))
        if len(self._files) > 8:
            more = QLabel(f"+{len(self._files) - 8} more files")
            more.setFont(QFont("Segoe UI", 9)); more.setAlignment(Qt.AlignCenter)
            more.setStyleSheet(f"color:#4B5563;{_NO_BG}"); more.setFixedHeight(24)
            lay.addWidget(more)

        # status label (shows action results)
        self._status = QLabel("")
        self._status.setFont(QFont("Segoe UI", 9))
        self._status.setStyleSheet(f"color:{ACCENT};{_NO_BG}")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.hide()
        lay.addWidget(self._status)

        # action buttons — REAL actions
        lay.addSpacing(12)
        btns = QHBoxLayout(); btns.setSpacing(8)

        self._org_btn = QPushButton("  Organize All")
        self._org_btn.setIcon(QIcon(_render_icon("folder-open", 14, "#0A1A16", 2.2)))
        self._org_btn.setIconSize(QSize(14, 14))
        self._org_btn.setCursor(Qt.PointingHandCursor)
        self._org_btn.setFont(QFont("Segoe UI Variable Text", 10, QFont.DemiBold))
        self._org_btn.setFixedHeight(38)
        self._org_btn.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:#0A1A16;border:none;"
            f"border-radius:10px;padding:0 18px;}}"
            f"QPushButton:hover{{background:#6FEDE0;}}")
        self._org_btn.clicked.connect(self._on_organize)
        btns.addWidget(self._org_btn, 1)

        self._open_btn = QPushButton("  Open Folder")
        self._open_btn.setIcon(QIcon(_render_icon("folder", 14, "#B0B4BC")))
        self._open_btn.setIconSize(QSize(14, 14))
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.setFont(QFont("Segoe UI Variable Text", 10))
        self._open_btn.setFixedHeight(38)
        self._open_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.06);color:#D1D5DB;"
            "border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:0 18px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.10);color:#FFFFFF;}")
        self._open_btn.clicked.connect(self._on_open_folder)
        btns.addWidget(self._open_btn, 1)

        self._del_btn = QPushButton()
        self._del_btn.setIcon(QIcon(_render_icon("trash", 15, "#6B7280")))
        self._del_btn.setIconSize(QSize(15, 15)); self._del_btn.setFixedSize(38, 38)
        self._del_btn.setCursor(Qt.PointingHandCursor)
        self._del_btn.setToolTip("Delete all scanned files (careful!)")
        self._del_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);"
            "border-radius:10px;}"
            "QPushButton:hover{background:rgba(239,68,68,0.15);}")
        self._del_btn.clicked.connect(self._on_delete)
        btns.addWidget(self._del_btn)
        lay.addLayout(btns)

    # ── Real action handlers ──
    def _set_status(self, msg: str, color: str = ACCENT):
        self._status.setStyleSheet(f"color:{color};{_NO_BG}")
        self._status.setText(msg); self._status.show()

    def _on_organize(self):
        if not self._folder_path:
            self._set_status("No folder path — can't organize.", "#EF4444")
            return
        self._org_btn.setEnabled(False)
        self._set_status("Organizing files...")
        threading.Thread(target=self._do_organize, daemon=True).start()

    def _do_organize(self):
        try:
            import httpx
            r = httpx.post(f"{_API_BASE}/api/capsule/organize",
                           json={"folder_path": self._folder_path}, timeout=30)
            result = r.json()
            count = result.get("count", 0)
            errors = result.get("errors", [])
            if errors:
                QTimer.singleShot(0, lambda: self._set_status(
                    f"Organized {count} files, {len(errors)} errors", "#F59E0B"))
            else:
                QTimer.singleShot(0, lambda: self._set_status(
                    f"✓ Organized {count} files into folders", ACCENT))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._set_status(f"Error: {e}", "#EF4444"))
        QTimer.singleShot(0, lambda: self._org_btn.setEnabled(True))

    def _on_open_folder(self):
        if self._folder_path:
            import subprocess
            subprocess.Popen(["explorer", self._folder_path])

    def _on_delete(self):
        if not self._files:
            return
        paths = [f["path"] for f in self._files if "path" in f]
        if not paths:
            self._set_status("No file paths available.", "#EF4444")
            return
        self._del_btn.setEnabled(False)
        self._set_status(f"Deleting {len(paths)} files...")
        threading.Thread(target=self._do_delete, args=(paths,), daemon=True).start()

    def _do_delete(self, paths: list[str]):
        try:
            import httpx
            r = httpx.post(f"{_API_BASE}/api/capsule/delete",
                           json={"file_paths": paths}, timeout=30)
            result = r.json()
            count = result.get("count", 0)
            QTimer.singleShot(0, lambda: self._set_status(
                f"✓ Deleted {count} files", ACCENT))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._set_status(f"Error: {e}", "#EF4444"))
        QTimer.singleShot(0, lambda: self._del_btn.setEnabled(True))


# ── Status Card ──────────────────────────────────────────────────────────────
class StatusCardWidget(CapsuleCard):
    def __init__(self, data: dict | None = None, parent=None):
        super().__init__(parent)
        data = data or {}
        text = data.get("text", ""); icon = data.get("icon", "sparkles")
        card_title = data.get("title", "AI Computer")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(0)
        hdr = QHBoxLayout(); hdr.setSpacing(10)
        hdr.addWidget(_icon_label(icon, 18, ACCENT))
        t = QLabel(card_title)
        t.setFont(QFont("Segoe UI Variable Display", 12, QFont.DemiBold))
        t.setStyleSheet(f"color:#FFFFFF;{_NO_BG}"); hdr.addWidget(t, 1)
        dismiss = _make_dismiss_btn(); dismiss.clicked.connect(self.animate_out)
        hdr.addWidget(dismiss); lay.addLayout(hdr)
        if text:
            lay.addSpacing(10); lay.addWidget(_thin_divider()); lay.addSpacing(10)
            body = QLabel(text); body.setWordWrap(True)
            body.setFont(QFont("Segoe UI", 10))
            body.setStyleSheet(f"color:#D1D5DB;{_NO_BG}"); body.setMaximumHeight(300)
            lay.addWidget(body)


# ── Widget factory ───────────────────────────────────────────────────────────
WIDGET_REGISTRY: dict[str, type[CapsuleCard]] = {
    "clutter_sweeper": ClutterSweeperWidget,
    "status_card": StatusCardWidget,
}

def create_widget(widget_type: str, data: dict | None = None,
                  parent=None) -> CapsuleCard | None:
    cls = WIDGET_REGISTRY.get(widget_type)
    return cls(data=data, parent=parent) if cls else None
