"""Native Qt desktop shell — the see-through liquid-glass capsule.

QtWebEngine (like WebView2) cannot render a transparent background on
Windows — Chromium's compositor surface is opaque. So the floating widget
is built from *native* Qt widgets, which DO support per-pixel window
transparency + Windows Acrylic. It funnels tasks to the local AI Computer
server over HTTP.

Launched by `run_desktop.py --widget`.
"""
from __future__ import annotations

import ctypes
import os
import secrets
import sys
import threading
import time
from ctypes import wintypes

# ── Windows Acrylic + rounded-window helpers ─────────────────────────────────

def _apply_acrylic(hwnd: int, tint_abgr: int = 0x40121016) -> None:
    """Real blur-behind so the capsule frosts the desktop wallpaper.

    tint_abgr = 0xAABBGGRR — low alpha keeps it mostly blur, little tint.
    """
    try:
        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int),
                        ("Data", ctypes.POINTER(ACCENTPOLICY)),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = ACCENTPOLICY(4, 2, tint_abgr, 0)  # 4 = ACRYLIC BLUR BEHIND
        data = WINCOMPATTRDATA(19, ctypes.pointer(accent), ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.pointer(data))
    except Exception:
        pass


def _round_window(hwnd: int, w: int, h: int, radius: int = 28) -> None:
    """Clip the window (and its Acrylic) to a rounded-rectangle shape."""
    try:
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1,
                                                     radius * 2, radius * 2)
        ctypes.windll.user32.SetWindowRgn(wintypes.HWND(hwnd), rgn, True)
    except Exception:
        pass


# ── icon rendering ───────────────────────────────────────────────────────────

_ICONS = {
    "logo": '<rect x="3" y="4" width="18" height="13" rx="2.4"/><line x1="8" y1="20.5" x2="16" y2="20.5"/><line x1="12" y1="17" x2="12" y2="20.5"/><circle cx="12" cy="10.5" r="2.1"/>',
    "send": '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
}


def _icon(name: str, size: int = 18, color: str = "#E8EAED", width: float = 1.9):
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    body = _ICONS.get(name, "")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


def main(port: int = 8000) -> int:
    from PySide6.QtCore import (Qt, QTimer, QObject, Signal, QPoint, QSize)
    from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QFont)
    from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit, QPushButton,
                                   QLabel, QVBoxLayout, QHBoxLayout)

    BASE = f"http://127.0.0.1:{port}"
    WIDTH = 600
    RADIUS = 26
    ACCENT = "#5BE0D0"

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # ── HTTP worker — submit a task + poll its log off the GUI thread ──
    class TaskRunner(QObject):
        statusChanged = Signal(str)
        finished = Signal(str)
        runningChanged = Signal(bool)

        def submit(self, goal: str) -> None:
            threading.Thread(target=self._run, args=(goal,), daemon=True).start()

        def _run(self, goal: str) -> None:
            try:
                import httpx
            except Exception as exc:  # pragma: no cover
                self.finished.emit(f"httpx unavailable: {exc}")
                return
            tid = "cap-" + secrets.token_hex(5)
            try:
                with httpx.Client(timeout=30.0) as c:
                    c.post(f"{BASE}/api/session")
                    r = c.post(f"{BASE}/api/tasks", json={
                        "task_id": tid, "goal": goal,
                        "mode": "auto", "model": "tier:balanced",
                    })
                    if r.status_code >= 400:
                        self.finished.emit(f"Couldn't start: {r.text[:160]}")
                        self.runningChanged.emit(False)
                        return
                    self.runningChanged.emit(True)
                    seen = 0
                    deadline = time.time() + 600
                    while time.time() < deadline:
                        time.sleep(1.1)
                        try:
                            log = c.get(f"{BASE}/api/tasks/{tid}/log").json().get("log", [])
                        except Exception:
                            continue
                        for ev in log[seen:]:
                            t = ev.get("type")
                            if t == "status":
                                msg = ev.get("message", "")
                                if msg:
                                    self.statusChanged.emit(msg)
                            elif t in ("done", "complete"):
                                self.finished.emit(ev.get("reason") or "Done.")
                                self.runningChanged.emit(False)
                                return
                            elif t in ("error", "failed", "cancelled"):
                                self.finished.emit(
                                    ev.get("reason") or ev.get("message") or "That task failed.")
                                self.runningChanged.emit(False)
                                return
                        seen = len(log)
                    self.finished.emit("Still working — taking longer than expected.")
                    self.runningChanged.emit(False)
            except Exception as exc:
                self.finished.emit(f"Error: {exc}")
                self.runningChanged.emit(False)

    # ── animated dot-matrix waveform ──
    class Waveform(QWidget):
        COLS, ROWS = 14, 5

        def __init__(self) -> None:
            super().__init__()
            self.setFixedSize(78, 18)
            self._active = False
            self._t = 0.0
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)

        def setActive(self, on: bool) -> None:
            self._active = on
            if on and not self._timer.isActive():
                self._timer.start(60)
            elif not on:
                self._timer.stop()
                self.update()

        def _tick(self) -> None:
            self._t += 0.18
            self.update()

        def paintEvent(self, _e) -> None:
            import math
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            cw, rh = w / self.COLS, h / self.ROWS
            dot = min(cw, rh) * 0.42
            col = QColor(ACCENT)
            for c in range(self.COLS):
                if self._active:
                    amp = (math.sin(self._t + c * 0.6) + 1) / 2
                    lit = 1 + round(amp * (self.ROWS - 1))
                else:
                    lit = 1
                for r in range(self.ROWS):
                    on = r >= self.ROWS - lit
                    col.setAlphaF(0.95 if (on and self._active) else (0.4 if on else 0.12))
                    p.setBrush(col)
                    p.setPen(Qt.NoPen)
                    cx = c * cw + cw / 2
                    cy = h - (r + 0.5) * rh
                    p.drawEllipse(QPoint(int(cx), int(cy)), int(dot), int(dot))
            p.end()

    # ── the capsule window ──
    class Capsule(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("AI Computer Sidekick")
            self.setWindowFlags(Qt.FramelessWindowHint
                                | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedWidth(WIDTH)
            self._drag = None
            self._busy = False

            outer = QVBoxLayout(self)
            outer.setContentsMargins(16, 13, 12, 14)
            outer.setSpacing(11)

            # ---- command row ----
            row = QHBoxLayout()
            row.setSpacing(10)

            logo = QLabel()
            logo.setPixmap(_icon("logo", 26, ACCENT).pixmap(26, 26))
            logo.setFixedSize(30, 30)
            logo.setAlignment(Qt.AlignCenter)
            row.addWidget(logo)

            self.input = QLineEdit()
            self.input.setPlaceholderText("Start a task…")
            self.input.setFont(QFont("Segoe UI", 13))
            self.input.returnPressed.connect(self._submit)
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;border:none;color:#F2F3F5;"
                "selection-background-color:%s;}" % ACCENT)
            row.addWidget(self.input, 1)

            self.status = QLabel("")
            self.status.setFont(QFont("Segoe UI", 12))
            self.status.setStyleSheet("color:%s;background:transparent;" % ACCENT)
            self.status.hide()
            row.addWidget(self.status, 1)

            self.wave = Waveform()
            row.addWidget(self.wave)

            self.send = QPushButton()
            self.send.setIcon(_icon("send", 18, "#04201C", 2.5))
            self.send.setIconSize(QSize(18, 18))
            self.send.setFixedSize(40, 40)
            self.send.setCursor(Qt.PointingHandCursor)
            self.send.clicked.connect(self._submit)
            self.send.setStyleSheet(
                "QPushButton{background:%s;border:none;border-radius:20px;}"
                "QPushButton:hover{background:#6FE8DA;}" % ACCENT)
            row.addWidget(self.send)

            self.close_btn = QPushButton()
            self.close_btn.setIcon(_icon("close", 16, "#C9CCD2", 2.4))
            self.close_btn.setIconSize(QSize(15, 15))
            self.close_btn.setFixedSize(36, 36)
            self.close_btn.setCursor(Qt.PointingHandCursor)
            self.close_btn.clicked.connect(self.close)
            self.close_btn.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,0.06);border:none;"
                "border-radius:18px;}"
                "QPushButton:hover{background:rgba(232,60,60,0.22);}")
            row.addWidget(self.close_btn)
            outer.addLayout(row)

            # ---- reply ----
            self.reply = QLabel("")
            self.reply.setWordWrap(True)
            self.reply.setFont(QFont("Segoe UI", 11))
            self.reply.setStyleSheet(
                "QLabel{color:#E6E8EC;background:rgba(255,255,255,0.06);"
                "border-radius:16px;padding:13px 15px;}")
            self.reply.setMaximumHeight(190)
            self.reply.hide()
            outer.addWidget(self.reply)

            # ---- backend wiring ----
            self.runner = TaskRunner()
            self.runner.statusChanged.connect(self._on_status)
            self.runner.finished.connect(self._on_finished)
            self.runner.runningChanged.connect(self._on_running)

            self.adjustSize()

        # --- task flow ---
        def _submit(self) -> None:
            goal = self.input.text().strip()
            if not goal or self._busy:
                return
            self.reply.hide()
            self.input.clear()
            self.runner.submit(goal)
            self._adjust()

        def _on_running(self, running: bool) -> None:
            self._busy = running
            self.wave.setActive(running)
            self.input.setVisible(not running)
            self.status.setVisible(running)
            if not running:
                self.status.hide()
                self.input.show()
            self.update()

        def _on_status(self, msg: str) -> None:
            self.status.setText(msg[:90])

        def _on_finished(self, text: str) -> None:
            self._busy = False
            self.wave.setActive(False)
            self.status.hide()
            self.input.show()
            clean = (text or "").strip()
            if clean:
                self.reply.setText(clean[:700])
                self.reply.show()
            self._adjust()

        def _adjust(self) -> None:
            self.adjustSize()
            QTimer.singleShot(0, self._reshape)

        def _reshape(self) -> None:
            hwnd = int(self.winId())
            _round_window(hwnd, self.width(), self.height(), RADIUS)

        # --- glass painting ---
        def paintEvent(self, _e) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            rect = self.rect().adjusted(1, 1, -1, -1)
            path.addRoundedRect(rect, RADIUS, RADIUS)
            # glass tint over the OS acrylic blur
            p.fillPath(path, QColor(18, 21, 28, 96))
            # crisp rim
            pen = QPen(QColor(255, 255, 255, 38))
            pen.setWidth(1)
            p.setPen(pen)
            p.drawPath(path)
            # soft top highlight
            hi = QPainterPath()
            hi.addRoundedRect(rect.adjusted(1, 1, -1, -rect.height() // 2),
                              RADIUS, RADIUS)
            p.fillPath(hi, QColor(255, 255, 255, 10))
            p.end()

        # --- frameless drag ---
        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.LeftButton:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e) -> None:
            if self._drag is not None and e.buttons() & Qt.LeftButton:
                self.move(e.globalPosition().toPoint() - self._drag)

        def mouseReleaseEvent(self, _e) -> None:
            self._drag = None

        def showEvent(self, e) -> None:  # noqa: N802
            super().showEvent(e)
            hwnd = int(self.winId())
            _apply_acrylic(hwnd)
            _round_window(hwnd, self.width(), self.height(), RADIUS)

        def resizeEvent(self, e) -> None:  # noqa: N802
            super().resizeEvent(e)
            self._reshape()

    win = Capsule()
    geo = app.primaryScreen().availableGeometry()
    win.move(geo.center().x() - WIDTH // 2, geo.top() + 70)
    win.show()
    win.input.setFocus()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(int(os.getenv("AI_COMPUTER_PORT", "8000"))))
