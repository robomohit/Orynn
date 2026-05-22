"""Qt / QtWebEngine desktop shell for the AI Computer capsule.

WebView2 (pywebview) cannot do reliable per-pixel window transparency on
Windows, so the floating widget rendered as an opaque rectangle. QtWebEngine
*can*: a frameless, translucent QWidget hosting a QWebEngineView with a
transparent page background — plus Windows Acrylic applied to the window —
gives a real frosted-glass capsule that blurs the desktop wallpaper behind it.

Launched by `run_desktop.py --widget`.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# ── Windows Acrylic + rounded-window helpers (degrade gracefully off-Windows) ──

def _apply_acrylic(hwnd: int, tint_abgr: int = 0x8C140F0B) -> None:
    """Give the window a real Acrylic blur-behind (Win10 1803+ / Win11).

    tint_abgr packs the glass tint as 0xAABBGGRR — alpha controls how much
    dark tint sits over the blurred wallpaper.
    """
    try:
        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENTPOLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        WCA_ACCENT_POLICY = 19
        accent = ACCENTPOLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, tint_abgr, 0)
        data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.pointer(accent),
                               ctypes.sizeof(accent))
        set_wca = ctypes.windll.user32.SetWindowCompositionAttribute
        set_wca(wintypes.HWND(hwnd), ctypes.pointer(data))
    except Exception:
        pass  # not Windows / unsupported — window is still translucent


def _round_window(hwnd: int, width: int, height: int, radius: int = 30) -> None:
    """Clip the window to a rounded-rectangle region so the Acrylic blur is
    shaped like the capsule, not a hard rectangle."""
    try:
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2)
        ctypes.windll.user32.SetWindowRgn(wintypes.HWND(hwnd), rgn, True)
    except Exception:
        pass


def main(port: int = 8000) -> int:
    from PySide6.QtCore import Qt, QUrl, QTimer, QObject, Slot
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineScript
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtCore import QFile, QIODevice

    CAPSULE_W = 600
    CORNER_RADIUS = 30

    app = QApplication.instance() or QApplication(sys.argv)

    # ── JS bridge: lets the in-page capsule drag/close the native window ──
    class ShellBridge(QObject):
        def __init__(self, window: QWidget):
            super().__init__()
            self._w = window

        @Slot(int, int)
        def moveBy(self, dx: int, dy: int) -> None:
            self._w.move(self._w.x() + int(dx), self._w.y() + int(dy))

        @Slot()
        def closeWindow(self) -> None:
            self._w.close()

    class Capsule(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("AI Computer Sidekick")
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.resize(CAPSULE_W, 104)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            self.view = QWebEngineView(self)
            self.view.setAttribute(Qt.WA_TranslucentBackground, True)
            self.view.page().setBackgroundColor(QColor(Qt.transparent))
            s = self.view.settings()
            s.setAttribute(QWebEngineSettings.ShowScrollBars, False)
            layout.addWidget(self.view)

            # web channel — register the native bridge
            self.bridge = ShellBridge(self)
            self.channel = QWebChannel()
            self.channel.registerObject("shell", self.bridge)
            self.view.page().setWebChannel(self.channel)

            # inject qwebchannel.js + a connector at document-creation time so
            # the served page can reach `window.__qtShell` with no server help
            self._inject_channel_script()

            self.view.setUrl(QUrl(f"http://127.0.0.1:{port}/?widget=1"))

            # poll the capsule's rendered height and hug it with the window
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._sync_height)
            self._timer.start(320)
            self._last_h = 0

        def _inject_channel_script(self) -> None:
            qfile = QFile(":/qtwebchannel/qwebchannel.js")
            qwc = ""
            if qfile.open(QIODevice.ReadOnly):
                qwc = bytes(qfile.readAll()).decode("utf-8", "ignore")
                qfile.close()
            connector = """
            (function () {
              function connect() {
                if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) {
                  return setTimeout(connect, 30);
                }
                new QWebChannel(qt.webChannelTransport, function (ch) {
                  window.__qtShell = ch.objects.shell;
                  window.dispatchEvent(new Event('qt-shell-ready'));
                });
              }
              connect();
            })();
            """
            script = QWebEngineScript()
            script.setName("qt-shell-bridge")
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(qwc + "\n" + connector)
            self.view.page().scripts().insert(script)

        def _sync_height(self) -> None:
            self.view.page().runJavaScript(
                "(function(){var c=document.querySelector('.vcap');"
                "return c?Math.ceil(c.getBoundingClientRect().height):0;})()",
                0, self._apply_height,
            )

        def _apply_height(self, h) -> None:
            try:
                h = int(h or 0)
            except (TypeError, ValueError):
                return
            if not (60 < h < 820):
                return
            target = h + 16
            if abs(target - self._last_h) <= 2:
                return
            self._last_h = target
            self.resize(CAPSULE_W, target)
            hwnd = int(self.winId())
            _round_window(hwnd, CAPSULE_W, target, CORNER_RADIUS)

        def showEvent(self, event) -> None:  # noqa: N802 (Qt signature)
            super().showEvent(event)
            hwnd = int(self.winId())
            _apply_acrylic(hwnd)
            _round_window(hwnd, self.width(), self.height(), CORNER_RADIUS)

    win = Capsule()
    # position: top-centre of the primary screen
    screen = app.primaryScreen().availableGeometry()
    win.move(screen.center().x() - CAPSULE_W // 2, screen.top() + 60)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import os
    sys.exit(main(int(os.getenv("AI_COMPUTER_PORT", "8000"))))
