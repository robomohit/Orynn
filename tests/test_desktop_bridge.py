from app.desktop_bridge import DesktopBridge, ScreenBounds


def test_compute_companion_geometry_docks_top_right():
    screen = ScreenBounds(x=100, y=50, width=1920, height=1080)

    geometry = DesktopBridge.compute_companion_geometry(screen)

    assert geometry == {
        "x": 1566,
        "y": 74,
        "width": 430,
        "height": 760,
    }


def test_compute_companion_geometry_respects_small_screens():
    screen = ScreenBounds(x=0, y=0, width=1100, height=640)

    geometry = DesktopBridge.compute_companion_geometry(screen)

    assert geometry["width"] == 360
    assert geometry["height"] == 592
    assert geometry["x"] == 716
    assert geometry["y"] == 24


def test_compute_overlay_segments_wraps_edges():
    screen = ScreenBounds(x=10, y=20, width=800, height=600)

    segments = DesktopBridge.compute_overlay_segments(screen)

    assert segments == [
        {"edge": "top", "x": 10, "y": 20, "width": 800, "height": 14},
        {"edge": "left", "x": 10, "y": 34, "width": 14, "height": 572},
        {"edge": "right", "x": 796, "y": 34, "width": 14, "height": 572},
        {"edge": "bottom", "x": 10, "y": 606, "width": 800, "height": 14},
    ]
