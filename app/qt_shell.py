"""Native Qt desktop shell — the see-through liquid-glass capsule.

QtWebEngine (like WebView2) cannot render a transparent background on
Windows — Chromium's compositor surface is opaque. So the floating widget
is built from *native* Qt widgets, which DO support per-pixel window
transparency + Windows Acrylic. It funnels tasks to the local AI Computer
server over HTTP.

Launched by `run_desktop.py` (default mode).
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import sys
import threading
import time
from ctypes import wintypes

# ── Windows Acrylic + rounded-window helpers ─────────────────────────────────

def _extend_frame(hwnd: int) -> bool:
    """Extend the DWM frame into the entire client area so the system
    backdrop reaches every pixel of the window (required on Win11)."""
    try:
        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]
        m = MARGINS(-1, -1, -1, -1)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(m))
        return True
    except Exception:
        return False


def _apply_win11_backdrop(hwnd: int) -> bool:
    """Modern Win11 22H2+ system backdrop (Acrylic).

    Uses the documented `DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)`
    API — the legacy `SetWindowCompositionAttribute` is undocumented and
    broken on newer Windows 11 builds.
    """
    try:
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_TRANSIENTWINDOW = 3            # Acrylic
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dwmapi = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark), ctypes.sizeof(dark))
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)
        hr = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        return hr == 0
    except Exception:
        return False


def _apply_acrylic_legacy(hwnd: int, tint_abgr: int = 0x20121016) -> None:
    """Legacy Win10 path — undocumented, but the only thing that works on
    older Win10 builds. Used as a fallback when DwmSetWindowAttribute fails.
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


def _apply_acrylic(hwnd: int) -> None:
    """Apply the right backdrop API for the OS version."""
    _extend_frame(hwnd)
    if not _apply_win11_backdrop(hwnd):
        _apply_acrylic_legacy(hwnd)


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
    from PySide6.QtCore import (Qt, QTimer, QObject, Signal, QPoint, QSize,
                                QPropertyAnimation, QEasingCurve, QRect)
    from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QFont,
                               QLinearGradient, QFontDatabase)
    from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit, QPushButton,
                                   QLabel, QVBoxLayout, QHBoxLayout, QScrollArea,
                                   QSizePolicy)

    from .capsule_widgets import CapabilityBar, create_widget, set_api_base
    from .clutter_scanner import scan_folder

    BASE = f"http://127.0.0.1:{port}"
    set_api_base(BASE)
    WIDTH = 620
    RADIUS = 26
    ACCENT = "#5BE0D0"

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    # Use the Win11 system font (Segoe UI Variable). Falls back to Segoe UI
    # then any sans-serif. Tight tracking is set per-widget below.
    app.setFont(QFont("Segoe UI Variable Text", 10))

    # ── HTTP worker — submit a task + poll its log off the GUI thread ──
    class TaskRunner(QObject):
        statusChanged = Signal(str)
        finished = Signal(str)
        runningChanged = Signal(bool)
        widgetRequested = Signal(dict)

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
                            elif t == "widget":
                                self.widgetRequested.emit(ev)
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

    # ── SSE listener — subscribes to /api/capsule/events for widget events ──
    class SSEListener(QObject):
        widgetRequested = Signal(dict)

        def __init__(self, base_url: str):
            super().__init__()
            self._base = base_url
            self._running = True

        def start(self):
            threading.Thread(target=self._listen, daemon=True).start()

        def _listen(self):
            try:
                import httpx
            except ImportError:
                return
            while self._running:
                try:
                    with httpx.stream("GET", f"{self._base}/api/capsule/events",
                                      timeout=None) as r:
                        for line in r.iter_lines():
                            if not self._running:
                                break
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[6:])
                                    if data.get("type") == "widget":
                                        self.widgetRequested.emit(data)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                except Exception:
                    if self._running:
                        time.sleep(3)  # reconnect after delay

        def stop(self):
            self._running = False

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
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setFixedWidth(WIDTH)
            self.setMinimumHeight(60)
            self._drag = None
            self._busy = False

            outer = QVBoxLayout(self)
            outer.setContentsMargins(16, 13, 12, 14)
            outer.setSpacing(8)

            # ---- command row ----
            row = QHBoxLayout()
            row.setSpacing(10)

            logo = QLabel()
            logo.setPixmap(_icon("logo", 26, ACCENT).pixmap(26, 26))
            logo.setFixedSize(30, 30)
            logo.setAlignment(Qt.AlignCenter)
            row.addWidget(logo)

            self.input = QLineEdit()
            self.input.setPlaceholderText("Ask AI Computer...")
            input_font = QFont("Segoe UI Variable Display", 14)
            input_font.setWeight(QFont.Medium)
            input_font.setLetterSpacing(QFont.PercentageSpacing, 98)
            self.input.setFont(input_font)
            self.input.returnPressed.connect(self._submit)
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;border:none;color:#FFFFFF;"
                "selection-background-color:%s; padding: 4px;}" % ACCENT)
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

            # ---- capability bar ----
            self.cap_bar = CapabilityBar()
            outer.addWidget(self.cap_bar)

            # ---- widget container (scrollable) ----
            self.widget_scroll = QScrollArea()
            self.widget_scroll.setWidgetResizable(True)
            self.widget_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.widget_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.widget_scroll.setMaximumHeight(500)
            self.widget_scroll.setStyleSheet(
                "QScrollArea{background:transparent;border:none;}"
                "QScrollBar:vertical{background:transparent;width:6px;}"
                "QScrollBar::handle:vertical{background:rgba(255,255,255,0.15);"
                "border-radius:3px;min-height:20px;}"
                "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{"
                "height:0;}"
            )
            self.widget_container = QWidget()
            self.widget_container.setStyleSheet("background:transparent;")
            self.widget_layout = QVBoxLayout(self.widget_container)
            self.widget_layout.setContentsMargins(0, 0, 0, 0)
            self.widget_layout.setSpacing(8)
            self.widget_layout.addStretch()
            self.widget_scroll.setWidget(self.widget_container)
            self.widget_scroll.hide()
            outer.addWidget(self.widget_scroll)

            # ---- reply (legacy text fallback) ----
            self.reply = QLabel("")
            self.reply.setWordWrap(True)
            self.reply.setFont(QFont("Segoe UI", 11))
            self.reply.setStyleSheet(
                "QLabel{color:#FFFFFF; background:transparent; "
                "border-top: 1px solid rgba(255, 255, 255, 0.15); "
                "padding: 18px 5px 5px 5px; margin-top: 5px;}")
            self.reply.setMaximumHeight(350)
            self.reply.hide()
            outer.addWidget(self.reply)

            # ---- backend wiring ----
            self.runner = TaskRunner()
            self.runner.statusChanged.connect(self._on_status)
            self.runner.finished.connect(self._on_finished)
            self.runner.runningChanged.connect(self._on_running)
            self.runner.widgetRequested.connect(self._spawn_widget)

            # ---- SSE listener for server-pushed widgets ----
            self.sse = SSEListener(BASE)
            self.sse.widgetRequested.connect(self._spawn_widget)
            self.sse.start()

            self.adjustSize()

        # --- task flow ---
        def _submit(self) -> None:
            goal = self.input.text().strip()
            if not goal or self._busy:
                return
            # Check for test commands
            if goal.lower() in ("/test-widget", "/test"):
                self.input.clear()
                self._test_widget()
                return
            self.reply.hide()
            self._clear_widgets()
            self.input.clear()
            self.runner.submit(goal)
            self._adjust()

        def _test_widget(self):
            """Scan REAL Downloads folder and show results."""
            real_data = scan_folder()  # scans ~/Downloads
            self._spawn_widget({
                "type": "widget",
                "widget_type": "clutter_sweeper",
                "data": real_data,
            })

        def _spawn_widget(self, event: dict):
            """Create a native Qt widget and animate it into the capsule."""
            widget_type = event.get("widget_type", "")
            data = event.get("data", {})
            widget = create_widget(widget_type, data, parent=self.widget_container)
            if widget is None:
                return
            widget.dismissed.connect(lambda w=widget: self._remove_widget(w))
            # Insert before the stretch at the end
            count = self.widget_layout.count()
            self.widget_layout.insertWidget(count - 1, widget)
            self.widget_scroll.show()
            self.cap_bar.hide()  # hide capability icons when widgets are active
            self._adjust()
            # Animate after a frame so geometry is settled
            QTimer.singleShot(50, widget.animate_in)

        def _remove_widget(self, widget):
            self.widget_layout.removeWidget(widget)
            widget.deleteLater()
            # Hide scroll area if no widgets left (only stretch remains)
            if self.widget_layout.count() <= 1:
                self.widget_scroll.hide()
                self.cap_bar.show()  # restore capability icons
            self._adjust()

        def _clear_widgets(self):
            """Remove all widgets from the container."""
            while self.widget_layout.count() > 1:
                item = self.widget_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self.widget_scroll.hide()

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
            rect = self.rect().adjusted(0, 0, 0, 0)
            path.addRoundedRect(rect, RADIUS, RADIUS)
            # translucent dark glass — low alpha to let acrylic blur show
            p.fillPath(path, QColor(14, 15, 20, 120))
            # very soft rim — barely visible, no harsh outline
            pen = QPen(QColor(255, 255, 255, 22))
            pen.setWidthF(0.5)
            p.setPen(pen)
            p.drawPath(path)
            # subtle top-edge highlight for depth
            grad = QLinearGradient(0, 0, 0, 60)
            grad.setColorAt(0, QColor(255, 255, 255, 14))
            grad.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillPath(path, grad)
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
            # spring entry — fade + slide
            self.setWindowOpacity(0.0)
            self._intro = QPropertyAnimation(self, b"windowOpacity")
            self._intro.setDuration(380)
            self._intro.setStartValue(0.0)
            self._intro.setEndValue(1.0)
            self._intro.setEasingCurve(QEasingCurve.OutCubic)
            self._intro.start()

            geo = app.primaryScreen().availableGeometry()
            start_pos = QPoint(geo.center().x() - self.width() // 2, geo.top() + 50)
            end_pos = QPoint(geo.center().x() - self.width() // 2, geo.top() + 70)
            self._slide = QPropertyAnimation(self, b"pos")
            self._slide.setDuration(380)
            self._slide.setStartValue(start_pos)
            self._slide.setEndValue(end_pos)
            self._slide.setEasingCurve(QEasingCurve.OutBack)
            self._slide.start()
            self.input.setFocus()

        def keyPressEvent(self, e) -> None:
            if e.key() == Qt.Key_Escape:
                if self.reply.isVisible():
                    self.reply.hide()
                    self._adjust()
                elif self.widget_scroll.isVisible():
                    self._clear_widgets()
                    self._adjust()
                else:
                    self.input.clear()
                    self.hide()
            else:
                super().keyPressEvent(e)

        def resizeEvent(self, e) -> None:  # noqa: N802
            super().resizeEvent(e)
            self._reshape()

    class HotkeySignaler(QObject):
        toggle = Signal()

    win = Capsule()

    signaler = HotkeySignaler()
    def on_toggle():
        if win.isVisible():
            win.hide()
        else:
            win.show()
            win.activateWindow()
            win.raise_()
            win.input.setFocus()

    signaler.toggle.connect(on_toggle)

    def hotkey_callback():
        signaler.toggle.emit()

    try:
        import keyboard
        # Register the global hotkey
        keyboard.add_hotkey('ctrl+shift+space', hotkey_callback)
        print("[Desktop] Global hotkey Ctrl+Shift+Space registered.")
    except ImportError:
        print("[Desktop] Install 'keyboard' (pip install keyboard) for global hotkeys.")
    except Exception as e:
        print(f"[Desktop] Could not register global hotkey: {e}")

    geo = app.primaryScreen().availableGeometry()
    win.move(geo.center().x() - WIDTH // 2, geo.top() + 70)
    win.show()
    win.input.setFocus()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(int(os.getenv("AI_COMPUTER_PORT", "8000"))))
