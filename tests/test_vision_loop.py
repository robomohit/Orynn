import base64
import time
import types

import pytest

from app.providers import (
    _capture_screenshot_b64,
    _pick_capture_cap,
    _run_with_timeout,
    get_scale_factor,
)


def test_capture_screenshot_b64(monkeypatch):
    class Shot:
        size = (100, 100)
        rgb = bytes([255, 0, 0] * 100 * 100)

    class MSSCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, monitor):
            assert monitor["width"] == 100
            return Shot()

    monkeypatch.setitem(__import__("sys").modules, "mss", types.SimpleNamespace(mss=lambda: MSSCtx()))

    data_url = _capture_screenshot_b64(100, 100)
    data = base64.b64decode(data_url.split(",", 1)[1])
    assert data[:3] == b"\xff\xd8\xff"
    assert get_scale_factor(1920, 1080) < 1.0
    assert get_scale_factor(800, 600) == 1.0


def test_capture_cap_matches_aspect_ratio():
    assert _pick_capture_cap(1024, 768) == (1024, 768)
    assert _pick_capture_cap(1920, 1200) == (1280, 800)
    assert _pick_capture_cap(1920, 1080) == (1366, 768)


def test_run_with_timeout_raises_on_slow_call():
    def slow():
        time.sleep(0.2)
        return 1

    with pytest.raises(RuntimeError, match="timed out"):
        _run_with_timeout(slow, 0.01, label="slow-call")
