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
import re
import secrets
import sys
import threading
import time
from ctypes import wintypes

# ── Windows DWM glass / pill-shape helpers ───────────────────────────────────
# Why we don't use pywinstyles here:
#   pywinstyles applies DWM acrylic/aero to the FULL hwnd rectangle. SetWindowRgn
#   clips Qt's painted output but DWM composites the blur on the rectangular
#   window — so a rectangular acrylic "halo" leaks past the rounded mask.
#
#   The fix is DwmEnableBlurBehindWindow with hRgnBlur set to the same pill
#   region. Now DWM only blurs INSIDE the pill, and the rest of the window
#   is fully transparent (WA_TranslucentBackground). No outline.


class _DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags", ctypes.c_uint),
        ("fEnable", ctypes.c_int),
        ("hRgnBlur", ctypes.c_void_p),
        ("fTransitionOnMaximized", ctypes.c_int),
    ]


_DWM_BB_ENABLE = 0x01
_DWM_BB_BLURREGION = 0x02


# ── Acrylic backdrop (Win10/11) via undocumented SetWindowCompositionAttribute.
# Gives a strong frosted/blurred backdrop with a tint — much closer to Apple
# "liquid glass" than the weak DwmEnableBlurBehindWindow gaussian. The window
# region (SetWindowRgn) clips it to the rounded pill, so no rectangular halo.
class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),   # 0xAABBGGRR
        ("AnimationId", ctypes.c_uint),
    ]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_WCA_ACCENT_POLICY = 19


def _apply_acrylic(hwnd: int, tint_abgr: int = 0x252028_00) -> bool:
    """Enable acrylic blur-behind on the window. GradientColor is 0xAABBGGRR —
    the low 0xAA byte is the tint opacity. A low alpha keeps it clear/glassy and
    lets our painted material define the look. Returns True on success."""
    try:
        user32 = ctypes.windll.user32
        accent = _ACCENT_POLICY()
        accent.AccentState = _ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 0
        accent.GradientColor = tint_abgr
        data = _WINCOMPATTRDATA()
        data.Attribute = _WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        fn = user32.SetWindowCompositionAttribute
        return bool(fn(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


MAX_CORNER_RADIUS = 32  # rounded-rectangle look, not a pill


# ── Modern Win11 (22H2+) glass: real acrylic backdrop + anti-aliased rounded
# corners, all managed by DWM. This is the GOOD path — the legacy
# SetWindowRgn approach below clips with a 1-bit GDI region, which produces a
# hard, jagged ("sharp second border") edge, and the old
# ACCENT_ENABLE_ACRYLICBLURBEHIND acrylic was disabled by Microsoft on newer
# Win11 builds (so the capsule lost its transparency and fell back to an opaque
# painted tint). DWM's system backdrop fixes both at once.
class _MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1

# ── CLEAR GLASS mode ─────────────────────────────────────────────────────────
# Big rounded "pill" corners + see-through (NOT frosted). We rely entirely on
# Qt's anti-aliased paintEvent for the rounded shape (smooth at ANY radius) and
# the window's per-pixel alpha (WA_TranslucentBackground) for see-through. So we
# apply NO GDI region (that's what jagged the corners) and NO acrylic blur (the
# user wants clear glass, not frost). The desktop shows through the painted
# tint's alpha, sharp and unblurred.
CLEAR_CORNER_RADIUS = 40    # generous pill rounding; Qt AA keeps it smooth


def _apply_pill_glass(hwnd: int, w: int, h: int, radius: int) -> None:
    """Clear-glass shaping: strip any region/blur so the corners are defined
    purely by Qt's anti-aliased paint. Smooth at any radius, fully see-through."""
    if w <= 0 or h <= 0:
        return
    try:
        user32 = ctypes.windll.user32
        # Remove any stale rounded region (a region would re-introduce the hard,
        # jagged edge and also clip Qt's smooth AA corners).
        user32.SetWindowRgn(wintypes.HWND(hwnd), None, True)
        # Tell DWM not to round either — Qt owns the corner shape.
        try:
            corner = ctypes.c_int(_DWMWCP_DONOTROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner), ctypes.sizeof(corner))
        except Exception:
            pass
    except Exception:
        pass


# Back-compat shim — older callsites still invoke _round_window().
def _round_window(hwnd: int, w: int, h: int, radius: int = 28) -> None:
    _apply_pill_glass(hwnd, w, h, radius)


def _clip_region(hwnd: int, w: int, h: int, radius: int) -> None:
    """No-op in clear-glass mode. The rounded shape is painted by Qt with
    anti-aliasing (smooth at any height), so there's no region to track during
    the grow/shrink animation — and applying one would re-jag the corners."""
    return
    r = min(radius, h // 2, MAX_CORNER_RADIUS)
    try:
        gdi = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        rgn = gdi.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
        user32.SetWindowRgn(wintypes.HWND(hwnd), rgn, True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# RECIPES — the agent's "what can it do for me" entry points.
# Each recipe is a high-value action workflow the agent will execute.
# (Built from research on Perplexity Comet, Manus, Operator, OpenInterpreter.)
# ─────────────────────────────────────────────────────────────────────────────
RECIPES = [
    {
        "id": "research",
        "label": "Research",
        "tip": "Multi-tab researcher → Markdown brief",
        "icon": "monitor",
        "prompt": (
            "Research the topic below across at least 6 reputable web sources. "
            "Use web_search and web_fetch tools. Build a Markdown brief with: "
            "TL;DR (3 bullets), Key facts (5-10), Source links. Save the brief "
            "to %USERPROFILE%/Documents/AI_Computer_Briefs/<slug>.md and open "
            "it in Notepad.\n\nTopic: "),
        "mode": "computer_use",
        "verb": "Researching",
    },
    {
        "id": "summarize_url",
        "label": "Summarize URL",
        "tip": "Fetch a page or YouTube video and digest it",
        "icon": "link",
        "prompt": (
            "Fetch the URL below (use web_fetch for articles, or get the "
            "YouTube transcript if it's a youtube.com link). Produce: 3-bullet "
            "TL;DR + 5 key takeaways + any action items mentioned. Show it in "
            "your reply.\n\nURL: "),
        "mode": "auto",
        "verb": "Digesting",
    },
    {
        "id": "clean_downloads",
        "label": "Clean Downloads",
        "tip": "Sort Downloads into categorized subfolders",
        "icon": "broom",
        "prompt": (
            "Scan %USERPROFILE%\\Downloads. For each file, decide a category "
            "(Documents, Images, Archives, Installers, Media, Other) and move "
            "it into a subfolder of that name (create if missing). Skip "
            "anything modified in the last 24h. Report how many you moved per "
            "category."),
        "mode": "computer",
        "verb": "Cleaning",
    },
    {
        "id": "form_filler",
        "label": "Fill Form",
        "tip": "Use clipboard data to fill the active form",
        "icon": "clipboard",
        "prompt": (
            "The user has copied source data to the clipboard and has a form "
            "open on screen. Take a screenshot to see the form's fields, "
            "match each visible field label to the matching value from the "
            "clipboard contents, then click and type into each field. STOP "
            "before any Submit / Send / Pay button and ask for confirmation."),
        "mode": "computer",
        "verb": "Filling form",
    },
    {
        "id": "scrape_list",
        "label": "Scrape List",
        "tip": "Extract rows from a list page to CSV",
        "icon": "archive",
        "prompt": (
            "There is a list / search-results page open on screen. Take a "
            "screenshot, identify the repeating items, and extract each into "
            "a row with columns: title, subtitle, link, any visible price or "
            "metric. Scroll once to capture more rows if obvious. Save as "
            "%USERPROFILE%\\Documents\\AI_Computer_Scrapes\\<timestamp>.csv "
            "and report the row count."),
        "mode": "computer",
        "verb": "Scraping",
    },
    {
        "id": "watch_ping",
        "label": "Watch & Ping",
        "tip": "Poll a screen region and notify on change",
        "icon": "zap",
        "prompt": (
            "Watch the current screen for the change described below. Every "
            "60 seconds: take a screenshot, compare to the previous, and "
            "report only when the described change happens. Stop after 30 "
            "checks if no change.\n\nWatch for: "),
        "mode": "computer",
        "verb": "Watching",
    },
]


# Words that signal a user wants the agent to DO something rather than chat.
# When the prompt contains one of these and no explicit context is set, we
# upgrade `mode=auto` to `mode=computer` so the agent uses its action tools.
# Connectors — services / surfaces the agent can drive on the user's behalf.
# A connector either pre-fills a recipe with the right context URL, or sets
# the agent's task mode + scope so it goes straight to the right surface.
CONNECTORS = [
    {"id": "gmail",    "label": "Gmail",    "icon": "mail",
     "tint": "#EA4335",
     "tip": "Triage your Gmail inbox in browser",
     "prompt": ("Open https://mail.google.com in the browser. Scan the inbox "
                "(top 10 unread). For each: classify (reply-needed/FYI/trash) "
                "and draft a reply where appropriate. Don't send anything — "
                "save as drafts. Report a summary."),
     "mode": "computer_use"},
    {"id": "outlook",  "label": "Outlook",  "icon": "mail",
     "tint": "#0078D4",
     "tip": "Triage your Outlook web inbox",
     "prompt": ("Open https://outlook.office.com in the browser. Scan the "
                "inbox (top 10 unread). For each: classify and draft a "
                "reply where appropriate. Save as drafts only."),
     "mode": "computer_use"},
    {"id": "gcal",     "label": "Calendar", "icon": "calendar",
     "tint": "#4285F4",
     "tip": "What's on my Calendar this week",
     "prompt": ("Open https://calendar.google.com and report my upcoming "
                "events this week (next 7 days). Group by day. Note any "
                "conflicts."),
     "mode": "computer_use"},
    {"id": "github",   "label": "GitHub",   "icon": "github",
     "tint": "#181717",
     "tip": "Triage GitHub notifications + PRs",
     "prompt": ("Open https://github.com/notifications and list open PRs "
                "and issues assigned to me or awaiting my review. "
                "Group by repo."),
     "mode": "computer_use"},
    {"id": "slack",    "label": "Slack",    "icon": "slack",
     "tint": "#4A154B",
     "tip": "Summarize Slack unreads",
     "prompt": ("Open https://app.slack.com in the browser. Visit each "
                "unread channel and summarize what was discussed (skip "
                "bot/notification channels). Don't post anything."),
     "mode": "computer_use"},
    {"id": "notion",   "label": "Notion",   "icon": "notion",
     "tint": "#000000",
     "tip": "Search my Notion workspace",
     "prompt": ("Open https://www.notion.so in the browser. Search my "
                "workspace for the topic I specify next, summarize the "
                "top 3 hits. Topic: "),
     "mode": "computer_use"},
    {"id": "drive",    "label": "Drive",    "icon": "drive",
     "tint": "#0F9D58",
     "tip": "Find a file in Google Drive",
     "prompt": ("Open https://drive.google.com and find the file I name "
                "next. Open it and read me the first paragraph / summary. "
                "File: "),
     "mode": "computer_use"},
    {"id": "youtube",  "label": "YouTube",  "icon": "youtube",
     "tint": "#FF0000",
     "tip": "Summarize a YouTube video",
     "prompt": ("Open the YouTube URL below and produce a 5-bullet summary "
                "of the video using its transcript. Include timestamps for "
                "the key claims.\n\nURL: "),
     "mode": "computer_use"},
    {"id": "chrome",   "label": "Active Tab","icon": "browser",
     "tint": "#4285F4",
     "tip": "Work on whatever tab is in front",
     "prompt": ("Take a screenshot of the current browser. Tell me what "
                "page I'm on and what I'm probably trying to do. Then "
                "ask if I want you to take a specific action on it."),
     "mode": "computer"},
]


ACTION_VERBS = {
    "open", "click", "send", "post", "fill", "submit", "scrape", "download",
    "rename", "move", "organize", "clean", "sort", "book", "buy", "order",
    "search the web", "browse", "navigate to", "go to", "automate", "run",
    "execute", "screenshot", "watch", "monitor", "type into", "press",
}


# ── enumerate open top-level windows (for the Apps capability) ───────────────

def _list_open_windows():
    """Return [{'hwnd': int, 'title': str, 'exe': str}] for visible windows.
    Filters out cloaked / off-screen shells (Program Manager, Settings host)."""
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwm = ctypes.windll.dwmapi

    DWMWA_CLOAKED = 14
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SKIP_TITLES = {"Program Manager", "Windows Input Experience",
                   "Microsoft Text Input Application", "Settings",
                   "Search", "Start"}

    results = []

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            # cloaked windows: UWP suspended apps / virtual-desktop hidden
            cloaked = wintypes.DWORD(0)
            dwm.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), DWMWA_CLOAKED,
                ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True
            tlen = user32.GetWindowTextLengthW(hwnd)
            if tlen == 0:
                return True
            buf = ctypes.create_unicode_buffer(tlen + 2)
            user32.GetWindowTextW(hwnd, buf, tlen + 2)
            title = buf.value
            if not title or title in SKIP_TITLES:
                return True
            # process exe
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = ""
            h = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if h:
                ebuf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                kernel32.QueryFullProcessImageNameW(
                    h, 0, ebuf, ctypes.byref(size))
                exe = ebuf.value
                kernel32.CloseHandle(h)
            results.append({"hwnd": int(hwnd), "title": title, "exe": exe})
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return results


def _icon_for_exe(exe_path: str, size: int = 24):
    """Get the .exe's icon as a QPixmap. Uses Qt's QFileIconProvider which
    delegates to the shell — reliable across exes, no manual HICON/GDI dance."""
    if not exe_path:
        return None
    try:
        from PySide6.QtCore import QFileInfo, Qt
        from PySide6.QtWidgets import QFileIconProvider

        provider = QFileIconProvider()
        qicon = provider.icon(QFileInfo(exe_path))
        if qicon is None or qicon.isNull():
            return None
        pm = qicon.pixmap(size, size)
        if pm.isNull():
            return None
        if pm.width() != size or pm.height() != size:
            pm = pm.scaled(size, size, Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        return pm
    except Exception:
        return None


_FILE_CLAIM_RE = re.compile(
    r"(?:saved (?:to|at|as)|wrote to|created file at|stored at)\s+([A-Za-z]:[\\\/][^\s\"'`)]+|/[^\s\"'`)]+\.\w+|[~%][^\s\"'`)]+\.\w+)",
    re.IGNORECASE,
)


def _verify_file_claims(text: str) -> list[tuple[str, bool]]:
    """Find 'saved to X' patterns in agent output and check disk reality.
    Returns [(path, exists), ...] — used to append a verification line so
    the user sees when the agent hallucinated a file write."""
    import os
    results = []
    for m in _FILE_CLAIM_RE.finditer(text or ""):
        raw = m.group(1).rstrip(".,;")
        path = os.path.expandvars(os.path.expanduser(raw))
        try:
            results.append((raw, os.path.exists(path)))
        except Exception:
            results.append((raw, False))
    return results


def _humanize_tool(name: str, args: str) -> str:
    """Convert a tool call to a user-readable phrase for the live ticker."""
    n = (name or "").lower()
    a = (args or "").strip()[:60]
    table = {
        "web_search":     f"Searching the web: {a}" if a else "Searching the web…",
        "web_fetch":      f"Fetching {a}" if a else "Fetching page",
        "screenshot":     "Taking a screenshot",
        "screen_context": "Looking at the screen",
        "focus_window":   f"Focusing window {a}" if a else "Focusing window",
        "mouse_click":    f"Clicking at {a}" if a else "Clicking",
        "keyboard_type":  f"Typing: {a[:40]}",
        "type_with_delay":f"Typing: {a[:40]}",
        "key":            f"Pressing {a}",
        "scroll":         "Scrolling",
        "read_file":      f"Reading {a}",
        "write_file":     f"Writing {a}",
        "move_file":      f"Moving {a}",
        "file_glob":      f"Listing files {a}",
        "file_grep":      f"Searching files {a}",
        "find_on_screen": "Locating element on screen",
        "uia_find":       f"Locating {a}" if a else "Locating control",
        "uia_click":      f"Clicking {a}" if a else "Clicking control",
        "uia_type":       f"Typing into {a}" if a else "Typing",
        "uia_wait":       f"Waiting for {a}" if a else "Waiting for control",
        "electron_check": "Checking app type",
        "electron_unlock":"Unlocking app accessibility",
        "lint_file":      f"Linting {a}",
        "ui_critique":    "Critiquing the UI",
        "todo_write":     "Updating plan",
        "diff_files":     "Comparing files",
    }
    if n in table:
        return table[n]
    if a:
        return f"{n}: {a}"
    return n or "Working"


def _shorten_url(u: str, max_len: int = 28) -> str:
    """Shorten a URL for display: example.com/foo → example.com."""
    try:
        from urllib.parse import urlparse
        host = urlparse(u).netloc or u
        if host.startswith("www."):
            host = host[4:]
        return host[:max_len]
    except Exception:
        return (u or "?")[:max_len]


def _capture_window_pixmap(hwnd: int, max_w: int = 220, max_h: int = 140):
    """Snapshot the window's pixels via PrintWindow (works even if occluded).
    Returns a QPixmap scaled to fit max_w × max_h, or None on failure."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPixmap

        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 40 or h < 40:
            return None

        # Cap source size for perf — render at ≤2x the target then scale down.
        scale = min(1.0, (max_w * 2) / w, (max_h * 2) / h)
        rw, rh = max(1, int(w * scale)), max(1, int(h * scale))

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        src_dc = user32.GetWindowDC(hwnd)
        mem_dc = gdi32.CreateCompatibleDC(src_dc)
        bmp = gdi32.CreateCompatibleBitmap(src_dc, rw, rh)
        old = gdi32.SelectObject(mem_dc, bmp)

        # If we need scaling, use SetStretchBltMode + render at native then blit.
        # Simpler approach: render at native into a full-size bitmap, then scale
        # the QImage in Qt.
        full_bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
        full_old = gdi32.SelectObject(mem_dc, full_bmp)
        PW_RENDERFULLCONTENT = 0x02
        ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            # Fallback: try without the flag
            user32.PrintWindow(hwnd, mem_dc, 0)

        # Extract bits
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int),
                ("biHeight", ctypes.c_int), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_int),
                ("biYPelsPerMeter", ctypes.c_int), ("biClrUsed", ctypes.c_uint),
                ("biClrImportant", ctypes.c_uint),
            ]
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(bi); bi.biWidth = w; bi.biHeight = -h
        bi.biPlanes = 1; bi.biBitCount = 32; bi.biCompression = 0
        buf = (ctypes.c_ubyte * (w * h * 4))()
        gdi32.GetDIBits(mem_dc, full_bmp, 0, h, buf, ctypes.byref(bi), 0)

        gdi32.SelectObject(mem_dc, full_old)
        gdi32.DeleteObject(full_bmp)
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)

        img = QImage(bytes(buf), w, h, QImage.Format_ARGB32)
        # GetDIBits writes BGRA; ARGB32 reads the same byte order on little-endian Windows.
        if img.isNull():
            return None

        # Hardware-accelerated apps (Chromium, Electron, NVIDIA overlays)
        # return a near-black bitmap from PrintWindow. Two tests:
        #   1. Average brightness across samples is above a real-window floor.
        #   2. At least 25% of samples are clearly bright (>120 sum-of-RGB).
        # An all-black capture fails both.
        sample_x = max(1, w // 18)
        sample_y = max(1, h // 14)
        sampled = 0
        non_black = 0
        total_brightness = 0
        for y in range(0, h, sample_y):
            for x in range(0, w, sample_x):
                sampled += 1
                px = img.pixel(x, y)
                r = (px >> 16) & 0xFF
                g = (px >> 8) & 0xFF
                b = px & 0xFF
                s = r + g + b
                total_brightness += s
                if s > 120:           # clearly not black (avg > 40/channel)
                    non_black += 1
        if sampled == 0:
            return None
        avg_brightness = total_brightness / sampled  # 0..765
        bright_ratio = non_black / sampled
        if avg_brightness < 60 or bright_ratio < 0.25:
            return None

        pm = QPixmap.fromImage(img.copy()).scaled(
            max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm if not pm.isNull() else None
    except Exception:
        return None


def _hicon_to_pixmap(hicon, size):
    """Fallback HICON → QPixmap conversion via GDI bitmap copy."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPixmap
        ICONINFO = type("ICONINFO", (ctypes.Structure,), {"_fields_": [
            ("fIcon", ctypes.c_int),
            ("xHotspot", ctypes.c_uint),
            ("yHotspot", ctypes.c_uint),
            ("hbmMask", ctypes.c_void_p),
            ("hbmColor", ctypes.c_void_p),
        ]})
        info = ICONINFO()
        if not ctypes.windll.user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None
        # Use a screen DC to render the icon into an RGBA image
        hdc = ctypes.windll.user32.GetDC(0)
        mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, size, size)
        old = ctypes.windll.gdi32.SelectObject(mem_dc, bmp)
        ctypes.windll.user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, 0, 0x3)
        # Pull bits
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int),
                ("biHeight", ctypes.c_int), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_int),
                ("biYPelsPerMeter", ctypes.c_int), ("biClrUsed", ctypes.c_uint),
                ("biClrImportant", ctypes.c_uint),
            ]
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(bi); bi.biWidth = size; bi.biHeight = -size
        bi.biPlanes = 1; bi.biBitCount = 32; bi.biCompression = 0
        buf = (ctypes.c_ubyte * (size * size * 4))()
        ctypes.windll.gdi32.GetDIBits(mem_dc, bmp, 0, size, buf,
                                       ctypes.byref(bi), 0)
        img = QImage(bytes(buf), size, size, QImage.Format_ARGB32)
        pm = QPixmap.fromImage(img.copy())
        ctypes.windll.gdi32.SelectObject(mem_dc, old)
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(mem_dc)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if info.hbmColor: ctypes.windll.gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:  ctypes.windll.gdi32.DeleteObject(info.hbmMask)
        return pm
    except Exception:
        return None


# ── icon rendering ───────────────────────────────────────────────────────────

_ICONS = {
    "logo": '<rect x="3" y="4" width="18" height="13" rx="2.4"/><line x1="8" y1="20.5" x2="16" y2="20.5"/><line x1="12" y1="17" x2="12" y2="20.5"/><circle cx="12" cy="10.5" r="2.1"/>',
    "send": '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    # Capability row icons (Perplexity-style, thin monochrome strokes)
    "apps":     '<rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/>',
    "folder":   '<path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "image":    '<rect x="3" y="3" width="18" height="18" rx="2.2"/><circle cx="8.5" cy="9" r="1.6"/><path d="m21 16-5-5L5 21"/>',
    "clipboard":'<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/>',
    "paperclip":'<path d="M21 12.2 12.5 20.7a5.5 5.5 0 1 1-7.8-7.8L13.4 4.2a3.7 3.7 0 1 1 5.2 5.2L10 17.9a1.9 1.9 0 1 1-2.7-2.7L15 7.5"/>',
    "mic":      '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/>',
    "link":     '<path d="M10 13a5 5 0 0 0 7.07.07l3-3a5 5 0 0 0-7.07-7.07l-1.71 1.71"/><path d="M14 11a5 5 0 0 0-7.07-.07l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "mail":     '<rect x="2" y="4" width="20" height="16" rx="2"/><polyline points="2 6 12 13 22 6"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "github":   '<path d="M12 2A10 10 0 0 0 9 21.5c.5.1.7-.2.7-.5v-1.8c-2.8.6-3.4-1.4-3.4-1.4-.5-1.1-1.1-1.4-1.1-1.4-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.3 1.1 2.9.8.1-.7.4-1.1.6-1.4-2.2-.3-4.6-1.1-4.6-5 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.7 1a9.4 9.4 0 0 1 5 0c1.9-1.3 2.7-1 2.7-1 .5 1.4.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.9-2.4 4.7-4.6 5 .4.3.7.9.7 1.9v2.7c0 .3.2.6.7.5A10 10 0 0 0 12 2z"/>',
    "slack":    '<rect x="2" y="8" width="6" height="2" rx="1"/><rect x="14" y="14" width="6" height="2" rx="1"/><rect x="8" y="14" width="2" height="6" rx="1"/><rect x="14" y="4" width="2" height="6" rx="1"/>',
    "notion":   '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="8" y1="7" x2="8" y2="17"/><line x1="8" y1="7" x2="16" y2="17"/><line x1="16" y1="7" x2="16" y2="17"/>',
    "drive":    '<polygon points="12 3 3 18 7 18 12 9 17 18 21 18"/><polyline points="3 18 12 18 21 18"/>',
    "youtube":  '<rect x="2" y="6" width="20" height="12" rx="3"/><polygon points="10 9 16 12 10 15"/>',
    "browser":  '<circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "music":    '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "message":  '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.5-.7L3 21l1.8-5.4A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>',
    "video":    '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4z"/>',
    "ticket":   '<path d="M3 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z"/><line x1="13" y1="6" x2="13" y2="18"/>',
    "sound":    '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    "soundoff": '<path d="M11 5 6 9H2v6h4l5 4z"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/>',
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
                                QPropertyAnimation, QEasingCurve, QRect,
                                QRectF, QPointF)
    from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QFont,
                               QLinearGradient, QRadialGradient, QFontDatabase)
    from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit, QPushButton,
                                   QLabel, QVBoxLayout, QHBoxLayout, QScrollArea,
                                   QSizePolicy)

    from .capsule_widgets import create_widget, set_api_base, set_card_palette
    from PySide6.QtWidgets import QFrame
    from .virtual_cursor import VirtualCursorOverlay, parse_click_xy
    from . import desktop_features as _df

    BASE = f"http://127.0.0.1:{port}"
    set_api_base(BASE)
    WIDTH = 640
    RADIUS = 999     # huge → clipped to height/2 → true pill
    # Transparent padding around the glass where the soft drop shadow is painted.
    # The window is (WIDTH + 2*SHADOW_PAD) wide; the visible glass stays WIDTH.
    SHADOW_PAD = 18
    ACCENT = "#5BE0D0"

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    # Use the Win11 system font (Segoe UI Variable). Falls back to Segoe UI
    # then any sans-serif. Tight tracking is set per-widget below.
    app.setFont(QFont("Segoe UI Variable Text", 10))

    # ── HTTP worker — submit a task + poll its log off the GUI thread ──
    class TaskRunner(QObject):
        statusChanged = Signal(str)
        finished = Signal(str, list)  # final text, source URLs
        runningChanged = Signal(bool)
        widgetRequested = Signal(dict)
        agentDelta = Signal(str)      # incremental agent text (typewriter)
        toolUsed = Signal(str, str, dict)   # tool_name, args_summary, overlay hint
        toolResult = Signal(str, str, dict) # tool_name, output_text, overlay result

        # Exposed so the UI can target /cancel for the current run
        current_task_id: str = ""

        def submit(self, goal: str, attach: dict | None = None) -> None:
            """attach = {window?, folder?, image_window_title?, recipe_mode?}."""
            threading.Thread(
                target=self._run, args=(goal, attach or {}), daemon=True
            ).start()

        def recover_active(self) -> None:
            """Attach the capsule to an already-running backend task after restart."""
            if self.current_task_id:
                return
            threading.Thread(target=self._recover_active, daemon=True).start()

        # ── Free-OpenRouter model picker ──
        # Verified-working free vision models, in fallback order. If the
        # first errors out (rate limited / 404), the next is tried.
        VISION_MODELS = [
            "openrouter/google/gemma-4-31b-it:free",
            "openrouter/google/gemma-4-26b-a4b-it:free",
        ]
        VISION_MODEL = VISION_MODELS[0]

        # Cache of currently-linked connectors. Refreshed every few minutes
        # in the background so the dashboard <-> widget stays in sync without
        # blocking task submit.
        _linked_connectors_cache: list[dict] = []
        _linked_cache_ms: int = 0

        def _fetch_linked_connectors(self) -> list[dict]:
            """Returns connector dicts with linked=True. Cached ~3 min."""
            import time as _t
            now_ms = int(_t.time() * 1000)
            if self._linked_connectors_cache and (
                    now_ms - self._linked_cache_ms < 180_000):
                return self._linked_connectors_cache
            try:
                import httpx
                with httpx.Client(timeout=4.0) as c:
                    c.post(f"{BASE}/api/session")
                    r = c.get(f"{BASE}/api/connectors")
                    if r.status_code == 200:
                        all_c = r.json().get("connectors", [])
                        self._linked_connectors_cache = [
                            c for c in all_c if c.get("linked")
                        ]
                        self._linked_cache_ms = now_ms
            except Exception as exc:
                print(f"[capsule] connector fetch failed: {exc}", flush=True)
            return self._linked_connectors_cache

        # Prompt prefix that meaningfully improves how free OpenRouter
        # models drive the desktop. Forces a screenshot-first / verify-after
        # rhythm and discourages the common failure modes (hallucinated
        # clicks at random pixels, no validation, runaway loops).
        DESKTOP_HARDENING = (
            "You are driving the user's Windows desktop. Prefer UI Automation "
            "(UIA) over screenshots — it is faster and never mis-clicks:\n"
            "1. `focus_window` (or `wait_for_window`) to bring the target app "
            "to the front.\n"
            "2. `uia_find` with the control's visible NAME (e.g. 'File', "
            "'Search', 'Send') to locate it — DO NOT take a screenshot or "
            "guess coordinates. ALWAYS pass the `app` window-title (e.g. "
            "app='Notepad') so UIA targets the right window even if focus "
            "didn't take.\n"
            "3. Act with `uia_click` (buttons/menus/channels) or `uia_type` "
            "(text boxes; clear_first=true to replace text, submit=true to "
            "press Enter and send/search in one step). After navigating, use "
            "`uia_wait` to block until the next control appears instead of "
            "guessing a delay.\n"
            "4. If `uia_find` returns nothing AND the app is Electron "
            "(VS Code, Slack, Discord, Spotify, Notion, Cursor...), call "
            "`electron_check` then `electron_unlock` on its .exe to relaunch "
            "with --force-renderer-accessibility, then retry uia_find.\n"
            "5. Only fall back to `screenshot` + coordinate clicks when a "
            "control has no accessible name (canvas/custom-drawn UI).\n"
            "6. Stop after at most 8 steps. If blocked, ask a clear question "
            "instead of looping.\n"
            "7. Never click Send / Submit / Pay / Delete without explicit "
            "user confirmation.\n\n"
            "TASK: "
        )

        def _build_payload(self, tid: str, goal: str, attach: dict) -> dict:
            payload: dict = {"task_id": tid, "goal": goal}

            scoped_window = attach.get("window")
            folder = attach.get("folder")
            image_window_title = attach.get("image_window_title")
            recipe_mode = attach.get("recipe_mode")

            if scoped_window and scoped_window.get("title"):
                payload["mode"] = "computer_isolated"
                payload["isolated_app"] = scoped_window["title"]
                payload["model"] = self.VISION_MODEL
            elif image_window_title:
                payload["mode"] = "computer_isolated"
                payload["isolated_app"] = image_window_title
                payload["model"] = self.VISION_MODEL
            elif folder:
                payload["mode"] = "coding"
                payload["project_folder"] = folder
                # let server auto-pick qwen3-coder:free for coding mode
            elif recipe_mode:
                # Recipe-suggested mode wins over heuristic
                payload["mode"] = recipe_mode
                if recipe_mode == "computer_isolated":
                    payload["model"] = self.VISION_MODEL
            else:
                # If the prompt sounds like a real action ("open Chrome and
                # do X", "clean my downloads"), switch to `computer` so the
                # agent uses its mouse/keyboard tools instead of just chatting.
                low = goal.lower()
                if any(v in low for v in ACTION_VERBS):
                    payload["mode"] = "computer"
                else:
                    payload["mode"] = "auto"

            # Apply desktop-control hardening for computer / computer_use
            # modes — these instructions dramatically improve free-tier
            # model behavior (less hallucinated clicks, more verification).
            if payload.get("mode") in ("computer", "computer_use",
                                       "computer_isolated"):
                payload["goal"] = self.DESKTOP_HARDENING + payload["goal"]

            # Tell the agent which connectors (surfaces) are linked & available.
            # The detailed per-connector MANUAL is injected server-side by the
            # agent (connectors.relevant_briefs) for whichever ones this goal
            # actually needs, so it works for the dashboard path too.
            linked = self._fetch_linked_connectors()
            web_connectors = [c for c in linked
                              if c.get("auth_kind") == "browser"]
            if web_connectors:
                names = ", ".join(c["label"] for c in web_connectors)
                payload["goal"] = (
                    f"[Linked & available services you may use when relevant: "
                    f"{names}.]\n\n" + payload["goal"]
                )

            return payload

        def _preflight_payload(self, payload: dict) -> dict:
            return {
                "goal": payload.get("goal", ""),
                "mode": payload.get("mode", "auto"),
                "model": payload.get("model"),
                "isolated_app": payload.get("isolated_app"),
            }

        def _summarize_preflight_issues(self, issues: list[dict]) -> str:
            lines: list[str] = []
            for issue in issues[:4]:
                label = str(issue.get("label") or issue.get("key") or "Capability")
                detail = str(issue.get("detail") or issue.get("fix") or "").strip()
                if detail:
                    lines.append(f"- {label}: {detail}")
                else:
                    lines.append(f"- {label}")
            if len(issues) > 4:
                lines.append(f"- {len(issues) - 4} more setup item(s)")
            return "\n".join(lines)

        def _run_preflight(self, client, payload: dict) -> bool:
            try:
                r = client.post(f"{BASE}/api/tasks/preflight",
                                json=self._preflight_payload(payload))
                if r.status_code >= 400:
                    self.statusChanged.emit("Preflight unavailable - starting cautiously...")
                    return True
                preflight = r.json()
            except Exception as exc:
                print(f"[capsule] preflight failed: {exc}", flush=True)
                self.statusChanged.emit("Preflight unavailable - starting cautiously...")
                return True

            issues = preflight.get("issues", [])
            if not isinstance(issues, list) or not issues:
                return True

            blocked = bool(preflight.get("blocked"))
            summary = self._summarize_preflight_issues(issues)
            if blocked:
                self.widgetRequested.emit({
                    "title": "Setup needed",
                    "icon": "alert",
                    "text": summary or "This task cannot start until setup is fixed.",
                })
                self.finished.emit("Setup needed before this task can run.", [])
                self.runningChanged.emit(False)
                return False

            payload["readiness_override"] = True
            self.statusChanged.emit("Capability fallback - running with safeguards...")
            self.widgetRequested.emit({
                "title": "Capability fallback",
                "icon": "alert",
                "text": summary or "Running with degraded local capabilities.",
            })
            return True

        def _retry_after_preflight_rejection(self, client, payload: dict, response):
            try:
                detail = response.json().get("detail", {})
            except Exception:
                return response
            if not isinstance(detail, dict):
                return response
            code = detail.get("code")
            preflight = detail.get("preflight") or {}
            issues = preflight.get("issues", []) if isinstance(preflight, dict) else []
            summary = self._summarize_preflight_issues(issues if isinstance(issues, list) else [])
            if code == "readiness_preflight_blocked":
                self.widgetRequested.emit({
                    "title": "Setup needed",
                    "icon": "alert",
                    "text": summary or "This task cannot start until setup is fixed.",
                })
                self.finished.emit("Setup needed before this task can run.", [])
                self.runningChanged.emit(False)
                return None
            if code == "readiness_preflight_warning" and isinstance(preflight, dict) and preflight.get("can_override"):
                payload["readiness_override"] = True
                self.statusChanged.emit("Capability fallback - running with safeguards...")
                self.widgetRequested.emit({
                    "title": "Capability fallback",
                    "icon": "alert",
                    "text": summary or "Running with degraded local capabilities.",
                })
                return client.post(f"{BASE}/api/tasks", json=payload)
            return response

        def _emit_approval_widget(self, tid: str, ev: dict) -> None:
            action = ev.get("action") if isinstance(ev.get("action"), dict) else {}
            action_type = str(action.get("type") or "action")
            action_id = str(ev.get("action_id") or "")
            reason = str(ev.get("reason") or action.get("explanation") or "This action needs approval.")
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            detail = reason
            if args:
                try:
                    detail += "\n\n" + json.dumps(args, indent=2)[:1200]
                except Exception:
                    pass
            label = action_type.replace("_", " ").title()
            self.statusChanged.emit("Waiting for approval...")
            self.widgetRequested.emit({
                "title": f"Approve {label}?",
                "icon": "alert",
                "text": detail,
                "buttons": [
                    {
                        "label": "Deny",
                        "style": "danger",
                        "action": "/api/approvals",
                        "payload": {"task_id": tid, "action_id": action_id, "approve": False},
                    },
                    {
                        "label": "Approve",
                        "style": "primary",
                        "action": "/api/approvals",
                        "payload": {"task_id": tid, "action_id": action_id, "approve": True},
                    },
                ],
            })

        def _emit_permission_widget(self, tid: str, ev: dict) -> None:
            scope = str(ev.get("scope") or "access")
            action_id = str(ev.get("action_id") or "")
            detail = str(ev.get("reason") or ev.get("explanation") or f"The agent needs {scope}.")
            self.statusChanged.emit("Waiting on permission...")
            self.widgetRequested.emit({
                "title": f"Allow {scope}?",
                "icon": "alert",
                "text": detail,
                "buttons": [
                    {
                        "label": "Deny",
                        "style": "danger",
                        "action": "/api/permissions",
                        "payload": {"task_id": tid, "action_id": action_id, "grant": False, "scope": scope},
                    },
                    {
                        "label": "Allow",
                        "style": "primary",
                        "action": "/api/permissions",
                        "payload": {"task_id": tid, "action_id": action_id, "grant": True, "scope": scope},
                    },
                ],
            })

        def _emit_trust_timeout_widget(self, ev: dict) -> None:
            is_approval = ev.get("type") == "approval_timeout"
            seconds = ev.get("timeout_seconds")
            wait = f" within {seconds}s" if isinstance(seconds, (int, float)) else ""
            title = "Approval timed out" if is_approval else "Permission timed out"
            self.widgetRequested.emit({
                "title": title,
                "icon": "alert",
                "text": f"No response was received{wait}. The agent stopped that action.",
            })

        def _is_terminal_status(self, status: str | None) -> bool:
            return str(status or "").lower() in {"done", "complete", "failed", "error", "cancelled"}

        def _task_id_from_record(self, record: dict) -> str:
            return str(record.get("task_id") or record.get("id") or "")

        def _task_goal_from_record(self, record: dict, log: list[dict] | None = None) -> str:
            for ev in log or []:
                if ev.get("type") == "task_created" and ev.get("goal"):
                    return str(ev.get("goal"))
            context = record.get("context") if isinstance(record.get("context"), dict) else {}
            return str(record.get("goal") or context.get("goal") or "Running task")

        def _latest_active_record(self, client):
            try:
                r = client.get(f"{BASE}/api/active-tasks")
                if r.status_code >= 400:
                    return None
                records = [
                    rec for rec in r.json().get("tasks", [])
                    if self._task_id_from_record(rec)
                    and not self._is_terminal_status(rec.get("status"))
                ]
            except Exception as exc:
                print(f"[capsule] active-task check failed: {exc}", flush=True)
                return None
            if not records:
                return None
            records.sort(key=lambda rec: str(rec.get("created_at") or ""))
            return records[-1]

        def _pending_trust_event(self, log: list[dict]):
            pending: dict[str, dict] = {}
            for ev in log:
                t = ev.get("type")
                aid = str(ev.get("action_id") or "")
                if t in ("approval_required", "permission_required") and aid:
                    pending[aid] = ev
                    continue
                if aid and t in ("action_start", "action_result", "approval_timeout", "permission_timeout"):
                    pending.pop(aid, None)
                if "__plan__" in pending and t in ("plan", "subtask", "action_start"):
                    pending.pop("__plan__", None)
                if t in ("done", "complete", "error", "failed", "cancelled"):
                    pending.clear()
            return list(pending.values())[-1] if pending else None

        def _recovery_state_from_log(self, log: list[dict]) -> dict:
            action_type_by_id: dict[str, str] = {}
            last_agent_text = ""
            for ev in log:
                if ev.get("type") == "action_start" and ev.get("action_id"):
                    action_type_by_id[str(ev.get("action_id"))] = str(ev.get("action_type") or "")
                elif ev.get("type") == "agent" and ev.get("text"):
                    last_agent_text = str(ev.get("text") or "")
            return {
                "source_urls": [],
                "url_re": re.compile(r"https?://[^\s<>\"'`)]+"),
                "last_agent_text": last_agent_text,
                "action_type_by_id": action_type_by_id,
            }

        def _emit_recovered_snapshot(self, tid: str, record: dict, log: list[dict], state: dict) -> None:
            goal = self._task_goal_from_record(record, log)
            mode = record.get("mode") or ""
            paused = bool(record.get("paused")) or str(record.get("status") or "").lower() == "paused"
            self.widgetRequested.emit({
                "title": "Reconnected to running task",
                "icon": "sparkles",
                "text": f"{goal[:700]}\n\nTask id: {tid[:12]}{f' · {mode}' if mode else ''}",
            })

            last_status = next((ev for ev in reversed(log) if ev.get("type") == "status" and ev.get("message")), None)
            last_action = next((ev for ev in reversed(log) if ev.get("type") in ("action_start", "tool")), None)
            if last_action:
                if last_action.get("type") == "action_start":
                    name = str(last_action.get("action_type") or "?")
                    args = str(last_action.get("args_summary") or "")[:120]
                    overlay = last_action.get("overlay") if isinstance(last_action.get("overlay"), dict) else {}
                    self.toolUsed.emit(name, args, overlay)
                else:
                    self.toolUsed.emit(str(last_action.get("name") or "?"), str(last_action.get("args") or "")[:120], {})
            elif last_status:
                self.statusChanged.emit(str(last_status.get("message") or "Reconnected."))

            pending = self._pending_trust_event(log)
            if pending:
                if pending.get("type") == "approval_required":
                    self._emit_approval_widget(tid, pending)
                elif pending.get("type") == "permission_required":
                    self._emit_permission_widget(tid, pending)
            elif paused:
                self.statusChanged.emit("Task paused.")
            else:
                self.statusChanged.emit("Reconnected to running task.")

        def _handle_task_event(self, tid: str, ev: dict, state: dict) -> bool:
            t = ev.get("type")
            url_re = state["url_re"]
            source_urls = state["source_urls"]
            if t == "status":
                msg = ev.get("message", "")
                if msg:
                    self.statusChanged.emit(msg)
            elif t == "widget":
                self.widgetRequested.emit(ev)
            elif t == "agent":
                txt = ev.get("text") or ""
                last_agent_text = state.get("last_agent_text", "")
                if txt and txt != last_agent_text:
                    if txt.startswith(last_agent_text):
                        self.agentDelta.emit(txt[len(last_agent_text):])
                    else:
                        self.agentDelta.emit(txt)
                    state["last_agent_text"] = txt
            elif t == "approval_required":
                self._emit_approval_widget(tid, ev)
            elif t == "permission_required":
                self._emit_permission_widget(tid, ev)
            elif t in ("approval_timeout", "permission_timeout"):
                self._emit_trust_timeout_widget(ev)
            elif t == "action_start":
                name = str(ev.get("action_type") or "?")
                args = str(ev.get("args_summary") or "")[:120]
                aid = ev.get("action_id")
                if aid:
                    state["action_type_by_id"][str(aid)] = name
                overlay = ev.get("overlay") if isinstance(ev.get("overlay"), dict) else {}
                self.toolUsed.emit(name, args, overlay)
                for u in url_re.findall(args):
                    if u not in source_urls:
                        source_urls.append(u)
            elif t == "action_result":
                aid = str(ev.get("action_id") or "")
                name = state["action_type_by_id"].get(aid, "")
                out = str(ev.get("output", ""))[:600]
                overlay = ev.get("overlay") if isinstance(ev.get("overlay"), dict) else {}
                if name and out:
                    self.toolResult.emit(name, out, overlay)
            elif t == "tool":
                name = str(ev.get("name") or "?")
                args = str(ev.get("args") or "")[:120]
                self.toolUsed.emit(name, args, {})
                for u in url_re.findall(args):
                    if u not in source_urls:
                        source_urls.append(u)
            elif t in ("done", "complete"):
                reason = ev.get("reason") or "Done."
                for u in url_re.findall(reason):
                    if u not in source_urls:
                        source_urls.append(u)
                self.finished.emit(reason, source_urls[:8])
                self.runningChanged.emit(False)
                return True
            elif t in ("error", "failed", "cancelled"):
                reason = ev.get("reason") or ev.get("message") or ""
                self.finished.emit(reason or "That task failed.", [])
                self.runningChanged.emit(False)
                return True
            return False

        def _poll_recovered_task(self, client, tid: str, seen: int, state: dict) -> None:
            deadline = time.time() + 600
            while time.time() < deadline:
                time.sleep(0.6)
                try:
                    log = client.get(f"{BASE}/api/tasks/{tid}/log").json().get("log", [])
                except Exception:
                    continue
                for ev in log[seen:]:
                    if self._handle_task_event(tid, ev, state):
                        return
                seen = len(log)
            self.finished.emit("Still working - taking longer than expected.", [])
            self.runningChanged.emit(False)

        def _recover_active(self) -> None:
            try:
                import httpx
            except Exception:
                return
            for attempt in range(10):
                if self.current_task_id:
                    return
                try:
                    with httpx.Client(timeout=8.0) as c:
                        c.post(f"{BASE}/api/session")
                        record = self._latest_active_record(c)
                        if not record or self.current_task_id:
                            return
                        tid = self._task_id_from_record(record)
                        log = c.get(f"{BASE}/api/tasks/{tid}/log").json().get("log", [])
                        self.current_task_id = tid
                        state = self._recovery_state_from_log(log)
                        self.runningChanged.emit(True)
                        self._emit_recovered_snapshot(tid, record, log, state)
                        self._poll_recovered_task(c, tid, len(log), state)
                        return
                except Exception as exc:
                    if attempt >= 9:
                        print(f"[capsule] active-task recovery failed: {exc}", flush=True)
                    time.sleep(1.0)

        def _run(self, goal: str, attach: dict) -> None:
            try:
                import httpx
            except Exception as exc:  # pragma: no cover
                self.finished.emit(f"httpx unavailable: {exc}")
                return
            tid = "cap-" + secrets.token_hex(5)
            self.current_task_id = tid
            payload = self._build_payload(tid, goal, attach)
            try:
                _df.save_pending_task(goal, payload.get("mode", "auto"), tid)
            except Exception:
                pass

            try:
                with httpx.Client(timeout=30.0) as c:
                    c.post(f"{BASE}/api/session")
                    if not self._run_preflight(c, payload):
                        return
                    r = c.post(f"{BASE}/api/tasks", json=payload)
                    if r.status_code >= 400:
                        retried = self._retry_after_preflight_rejection(c, payload, r)
                        if retried is None:
                            return
                        r = retried
                    if r.status_code >= 400:
                        self.finished.emit(f"Couldn't start: {r.text[:200]}")
                        self.runningChanged.emit(False)
                        return
                    # debug: surface model + mode the server received
                    print(f"[capsule] task {tid} sent mode={payload.get('mode')} "
                          f"model={payload.get('model','<auto>')} "
                          f"isolated={payload.get('isolated_app','-')} "
                          f"folder={payload.get('project_folder','-')}",
                          flush=True)
                    self.runningChanged.emit(True)
                    seen = 0
                    deadline = time.time() + 600
                    source_urls: list[str] = []
                    URL_RE = re.compile(r"https?://[^\s<>\"'`)]+")
                    last_agent_text = ""
                    # action_id → action_type so we can pair action_result
                    # events back to the tool they belong to (the result
                    # event carries the REAL coordinates that the cursor
                    # overlay needs, not the args_summary template).
                    action_type_by_id: dict[str, str] = {}
                    while time.time() < deadline:
                        time.sleep(0.6)  # tighter poll for smoother streaming
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
                            elif t == "agent":
                                # Streaming/text deltas from the model. Emit only
                                # the NEW chars so the answer card grows live.
                                txt = ev.get("text") or ""
                                if txt and txt != last_agent_text:
                                    if txt.startswith(last_agent_text):
                                        self.agentDelta.emit(txt[len(last_agent_text):])
                                    else:
                                        self.agentDelta.emit(txt)
                                    last_agent_text = txt
                            elif t == "approval_required":
                                self._emit_approval_widget(tid, ev)
                            elif t == "permission_required":
                                self._emit_permission_widget(tid, ev)
                            elif t in ("approval_timeout", "permission_timeout"):
                                self._emit_trust_timeout_widget(ev)
                            elif t == "action_start":
                                # The dashboard's event format. Each agent
                                # tool call surfaces as action_start with
                                # action_type + args_summary.
                                name = ev.get("action_type", "?")
                                args = str(ev.get("args_summary", ""))[:120]
                                aid = ev.get("action_id")
                                if aid:
                                    action_type_by_id[aid] = name
                                overlay = ev.get("overlay") if isinstance(ev.get("overlay"), dict) else {}
                                self.toolUsed.emit(name, args, overlay)
                                for u in URL_RE.findall(args):
                                    if u not in source_urls:
                                        source_urls.append(u)
                            elif t == "action_result":
                                aid = ev.get("action_id")
                                name = action_type_by_id.get(aid or "", "")
                                # Keep enough text that trailing tokens (e.g. the
                                # UIA focus-ring [uia:l,t,w,h]) survive — uia_find
                                # appends it after a multi-line match list.
                                out = str(ev.get("output", ""))[:600]
                                overlay = ev.get("overlay") if isinstance(ev.get("overlay"), dict) else {}
                                if name and out:
                                    self.toolResult.emit(name, out, overlay)
                            elif t == "tool":
                                # Some flows may still emit the older `tool` event
                                name = ev.get("name", "?")
                                args = str(ev.get("args", ""))[:120]
                                self.toolUsed.emit(name, args, {})
                                for u in URL_RE.findall(args):
                                    if u not in source_urls:
                                        source_urls.append(u)
                            elif t in ("done", "complete"):
                                reason = ev.get("reason") or "Done."
                                # also scrape URLs from final reason text
                                for u in URL_RE.findall(reason):
                                    if u not in source_urls:
                                        source_urls.append(u)
                                self.finished.emit(reason, source_urls[:8])
                                self.runningChanged.emit(False)
                                return
                            elif t in ("error", "failed", "cancelled"):
                                reason = (ev.get("reason")
                                          or ev.get("message") or "")
                                # Auto-retry on free-tier rate-limit.
                                # Look for "429" / "rate-limited" /
                                # "retry shortly" and resubmit ONCE after
                                # the suggested Retry-After window.
                                lr = reason.lower()
                                if (("429" in lr or "rate-limited" in lr
                                        or "retry shortly" in lr)
                                        and not getattr(self, "_did_retry", False)):
                                    self._did_retry = True
                                    # Parse Retry-After seconds if present;
                                    # default 15s.
                                    import re as _re
                                    m = _re.search(
                                        r"retry[_ -]after[_ -]?seconds?[\":\s]*?(\d+)",
                                        reason, _re.IGNORECASE)
                                    wait = int(m.group(1)) if m else 15
                                    wait = min(wait, 60)
                                    self.statusChanged.emit(
                                        f"Rate-limited — retrying in {wait}s…")
                                    time.sleep(wait)
                                    # Re-fire same task with a fresh id
                                    self.statusChanged.emit("Retrying…")
                                    new_tid = "cap-" + secrets.token_hex(5)
                                    self.current_task_id = new_tid
                                    payload["task_id"] = new_tid
                                    r2 = c.post(f"{BASE}/api/tasks",
                                                json=payload)
                                    if r2.status_code < 400:
                                        # Reset polling for the new tid.
                                        # `_retry_reset` tells the for-end
                                        # to skip seen = len(log).
                                        tid = new_tid
                                        deadline = time.time() + 600
                                        _retry_reset = True
                                        break
                                self._did_retry = False
                                self.finished.emit(
                                    reason or "That task failed.", [])
                                self.runningChanged.emit(False)
                                return
                        if locals().get("_retry_reset"):
                            seen = 0
                            _retry_reset = False
                        else:
                            seen = len(log)
                    self.finished.emit(
                        "Still working — taking longer than expected.", [])
                    self.runningChanged.emit(False)
            except Exception as exc:
                self.finished.emit(f"Error: {exc}", [])
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
        # Thread-safe delivery of a recognized voice transcript to the UI thread.
        transcriptReady = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("AI Computer Sidekick")
            flags = Qt.FramelessWindowHint
            if os.getenv("AI_COMPUTER_TOOL_WINDOW", "1").lower() not in {"0", "false", "no"}:
                flags |= Qt.Tool
            if os.getenv("AI_COMPUTER_TOPMOST", "1").lower() not in {"0", "false", "no"}:
                flags |= Qt.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setFixedWidth(WIDTH + 2 * SHADOW_PAD)
            self.setMinimumHeight(60)
            self._drag = None
            self._busy = False
            self._scoped_window = None        # picked via Apps button
            self._scoped_folder = None        # picked via Folder button
            self._scoped_image = None         # {"path":..., "viewer_title":...}
            self._clipboard_text = None       # captured via Clipboard button
            self._attached_file = None        # {"path":..., "text":..., "is_image":bool}

            # Adaptive glass — sampled luminance of whatever is behind the
            # capsule. A dark backdrop gets clear bright-edged glass; a light
            # backdrop gets a denser tint + soft dark edge so it never washes
            # out into muddy grey or shows seams. Re-sampled on a slow timer
            # and after drags. 0.0 = dark bg, 1.0 = light bg.
            self._bg_light = 0.0
            self._animating = False           # True during grow/shrink tween
            self._setup_mode = False          # first-run: input captures API key
            self._voice_mode = False          # talk-to-it / it-talks-back loop
            self._listening = False           # mic actively capturing
            self.transcriptReady.connect(self._on_transcript)
            # Discrete light/dark theme for the CONTENT (chips, icons, card
            # text). Flipped with hysteresis so the chrome stays legible: over
            # a bright backdrop the whole capsule becomes a light glass with
            # dark content; over dark it's the dark glass. None = not yet set.
            self._light_mode = None
            self._capsule_state = "idle"
            self._last_action_phrase = ""
            self._last_control_layer = ""
            self._last_control_reason = ""

            # Virtual cursor overlay — frameless click-through window that
            # paints a smooth animated cursor + ripple wherever the agent
            # clicks or types. Gives the user a visible "what just happened"
            # cue during agent desktop control.
            self._vcursor = VirtualCursorOverlay()

            # DWM glass is applied in _apply_pill_glass() — scoped to the
            # pill region so no rectangular halo leaks out. Do NOT call
            # pywinstyles here; it would re-apply blur to the full hwnd rect
            # and bring the rectangle back.
            # NOTE: no QGraphicsDropShadowEffect either — it gets clipped by
            # SetWindowRgn and renders as a black rectangle. DWM already
            # supplies a system shadow for frameless top-level windows.
            self.setObjectName("main_container")
            # Transparent here — the glass material is drawn in paintEvent so we
            # can layer a top highlight + accent edge without QSS limits.
            self.setStyleSheet("#main_container { background: transparent; }")

            outer = QVBoxLayout(self)
            # Add SHADOW_PAD on every side so content stays inside the glass and
            # the outer transparent band holds the drop shadow.
            outer.setContentsMargins(18 + SHADOW_PAD, 14 + SHADOW_PAD,
                                     18 + SHADOW_PAD, 16 + SHADOW_PAD)
            outer.setSpacing(12)

            # =========================================================
            # ROW 1 — raised input pill (lighter glass within the panel)
            # =========================================================
            top_row = QHBoxLayout()
            top_row.setSpacing(10)

            input_pill = QFrame()
            input_pill.setObjectName("input_pill")
            input_pill.setFixedHeight(54)
            input_pill.setStyleSheet(
                "#input_pill {"
                "  background: rgba(255,255,255,200);"
                "  border: 1px solid rgba(20,24,32,40);"
                "  border-radius: 27px;"
                "}"
            )
            pill_row = QHBoxLayout(input_pill)
            pill_row.setContentsMargins(18, 0, 8, 0)
            pill_row.setSpacing(12)

            # Logo inside the pill (Perplexity puts brand mark left-of-input)
            logo = QLabel()
            logo.setPixmap(_icon("logo", 20, "#1A1D24", 1.9).pixmap(20, 20))
            logo.setFixedSize(24, 24)
            logo.setAlignment(Qt.AlignCenter)
            logo.setStyleSheet("background: transparent; border: none;")
            pill_row.addWidget(logo)

            self.input = QLineEdit()
            self.input.setPlaceholderText("Start a task…")
            input_font = QFont("Segoe UI Variable Display", 14)
            input_font.setWeight(QFont.Medium)
            input_font.setLetterSpacing(QFont.PercentageSpacing, 98)
            self.input.setFont(input_font)
            self.input.returnPressed.connect(self._submit)
            self.input.textChanged.connect(
                lambda text: None if self._busy else self._set_capsule_state(
                    "focused" if text.strip() else "context_ready"))
            self.input.setStyleSheet(
                "QLineEdit{"
                "  background: transparent; border: none;"
                "  color: #1A1D24; padding: 4px 0;"
                "  selection-background-color: %s;"
                "}"
                "QLineEdit::placeholder{ color: rgba(60,66,78,160); }"
                % ACCENT)
            pill_row.addWidget(self.input, 1)

            self.status = QLabel("")
            self.status.setFont(QFont("Segoe UI", 12))
            self.status.setStyleSheet("color:%s;background:transparent;" % ACCENT)
            self.status.hide()
            pill_row.addWidget(self.status)

            # Voice conversation toggle — when ON, the agent reads its replies
            # aloud and re-arms the mic for hands-free back-and-forth.
            self.voice_btn = QPushButton()
            self.voice_btn.setCheckable(True)
            self.voice_btn.setIcon(_icon("soundoff", 16, "#1A1D24", 1.8))
            self.voice_btn.setIconSize(QSize(16, 16))
            self.voice_btn.setFixedSize(32, 32)
            self.voice_btn.setCursor(Qt.PointingHandCursor)
            self.voice_btn.setToolTip("Voice conversation — talk to it, it talks back")
            self.voice_btn.setStyleSheet(
                "QPushButton{ background: transparent;"
                "  border: 1px solid rgba(20,24,32,30); border-radius: 16px; }"
                "QPushButton:hover{ background: rgba(20,24,32,30); }"
                "QPushButton:checked{ background: rgba(91,224,208,70);"
                "  border-color: rgba(40,160,150,200); }"
            )
            self.voice_btn.clicked.connect(self._toggle_voice_mode)
            pill_row.addWidget(self.voice_btn)

            # Mic — click to dictate (toggle). Recognizes one utterance and, in
            # voice mode, submits it automatically.
            self.mic_btn = QPushButton()
            self.mic_btn.setIcon(_icon("mic", 16, "#1A1D24", 1.8))
            self.mic_btn.setIconSize(QSize(16, 16))
            self.mic_btn.setFixedSize(32, 32)
            self.mic_btn.setCursor(Qt.PointingHandCursor)
            self.mic_btn.setToolTip("Click to dictate")
            self.mic_btn.setStyleSheet(
                "QPushButton{"
                "  background: transparent;"
                "  border: 1px solid rgba(20,24,32,30);"
                "  border-radius: 16px;"
                "}"
                "QPushButton:hover{ background: rgba(20,24,32,30); }"
                "QPushButton:checked{"
                "  background: rgba(229,72,77,200);"
                "  border-color: rgba(229,72,77,255);"
                "}"
            )
            self.mic_btn.setCheckable(True)
            self.mic_btn.clicked.connect(self._toggle_mic)
            pill_row.addWidget(self.mic_btn)

            # Plus button — submit. Sits at the right edge of the pill.
            self.send = QPushButton()
            self.send.setIcon(_icon("plus", 16, "#FFFFFF", 2.4))
            self.send.setIconSize(QSize(16, 16))
            self.send.setFixedSize(38, 38)
            self.send.setCursor(Qt.PointingHandCursor)
            self.send.clicked.connect(self._submit)
            # Solid dark accent button — the "submit" CTA pops on the light pill
            self.send.setStyleSheet(
                "QPushButton{"
                "  background: #1A1D24;"
                "  border: 1px solid #2A2E38;"
                "  border-radius: 19px;"
                "}"
                "QPushButton:hover{"
                "  background: #2A2E38;"
                "  border-color: %s;"
                "}" % ACCENT
            )
            pill_row.addWidget(self.send)
            top_row.addWidget(input_pill, 1)

            # Close lives outside the pill — tiny floating circle.
            self.close_btn = QPushButton()
            self.close_btn.setIcon(_icon("close", 12, "#F0F2F8", 2.2))
            self.close_btn.setIconSize(QSize(12, 12))
            self.close_btn.setFixedSize(28, 28)
            self.close_btn.setCursor(Qt.PointingHandCursor)
            self.close_btn.clicked.connect(self.close)
            self.close_btn.setStyleSheet(
                "QPushButton{"
                "  background: transparent;"
                "  border: 1px solid transparent;"
                "  border-radius: 14px;"
                "}"
                "QPushButton:hover{"
                "  background: rgba(255,255,255,32);"
                "  border-color: rgba(255,255,255,60);"
                "}"
            )
            top_row.addWidget(self.close_btn)
            outer.addLayout(top_row)

            # =========================================================
            # ROW 1b — adaptive context surface
            # Shows scope, perception mode, current phase, and hard controls.
            # =========================================================
            self.context_bar = QFrame()
            self.context_bar.setObjectName("context_bar")
            self.context_bar.setStyleSheet(
                "#context_bar{"
                "  background: rgba(255,255,255,28);"
                "  border: 1px solid rgba(255,255,255,55);"
                "  border-radius: 15px;"
                "}"
            )
            ctx = QHBoxLayout(self.context_bar)
            ctx.setContentsMargins(10, 3, 7, 3)
            ctx.setSpacing(6)

            chip_qss = (
                "QLabel{"
                "  color: rgba(240,242,248,230);"
                "  background: rgba(255,255,255,30);"
                "  border-radius: 10px;"
                "  padding: 3px 8px;"
                "  font-size: 10px;"
                "  font-weight: 650;"
                "}"
            )
            self.scope_chip = QLabel("Computer")
            self.scope_chip.setStyleSheet(chip_qss + "QLabel{color:%s;}" % ACCENT)
            self.vision_chip = QLabel("Ready")
            self.vision_chip.setStyleSheet(chip_qss)
            self.phase_chip = QLabel("Idle")
            self.phase_chip.setStyleSheet(chip_qss)
            ctx.addWidget(self.scope_chip)
            ctx.addWidget(self.vision_chip)
            ctx.addWidget(self.phase_chip)
            ctx.addStretch()

            mini_qss = (
                "QPushButton{"
                "  color: rgba(240,242,248,235);"
                "  background: rgba(255,255,255,28);"
                "  border: 1px solid rgba(255,255,255,55);"
                "  border-radius: 11px;"
                "  padding: 3px 9px;"
                "  font-size: 10px;"
                "  font-weight: 700;"
                "}"
                "QPushButton:hover{ background: rgba(255,255,255,48); }"
            )
            danger_qss = (
                "QPushButton{"
                "  color: #FFDADA;"
                "  background: rgba(220,70,70,72);"
                "  border: 1px solid rgba(255,170,170,90);"
                "  border-radius: 11px;"
                "  padding: 3px 9px;"
                "  font-size: 10px;"
                "  font-weight: 700;"
                "}"
                "QPushButton:hover{ background: rgba(220,70,70,120); }"
            )
            self.context_pause_btn = QPushButton("Pause")
            self.context_pause_btn.setCursor(Qt.PointingHandCursor)
            self.context_pause_btn.setStyleSheet(mini_qss)
            self.context_pause_btn.clicked.connect(self._pause_or_resume)
            self.context_stop_btn = QPushButton("Stop")
            self.context_stop_btn.setCursor(Qt.PointingHandCursor)
            self.context_stop_btn.setStyleSheet(danger_qss)
            self.context_stop_btn.clicked.connect(self._cancel_running)
            self.context_details_btn = QPushButton("Details")
            self.context_details_btn.setCursor(Qt.PointingHandCursor)
            self.context_details_btn.setStyleSheet(mini_qss)
            self.context_details_btn.clicked.connect(self._show_context_details)
            ctx.addWidget(self.context_pause_btn)
            ctx.addWidget(self.context_stop_btn)
            ctx.addWidget(self.context_details_btn)
            self.context_bar.hide()
            outer.addWidget(self.context_bar)

            # =========================================================
            # ROW 2 — capability row (Apps / Folder / Image / Clipboard / Paperclip)
            # =========================================================
            cap_row = QHBoxLayout()
            cap_row.setSpacing(8)
            cap_row.addStretch()

            cap_btn_qss = (
                "QPushButton{"
                "  background: transparent;"
                "  border: 1px solid transparent;"
                "  border-radius: 12px;"
                "  padding: 6px;"
                "}"
                "QPushButton:hover{"
                "  background: rgba(255,255,255,32);"
                "  border-color: rgba(255,255,255,60);"
                "}"
                "QPushButton:checked{"
                "  background: rgba(255,255,255,48);"
                "  border-color: rgba(255,255,255,90);"
                "}"
            )
            self.cap_buttons = {}
            for icon_name, tip in [
                ("apps", "Open apps"),
                ("folder", "Files"),
                ("image", "Image"),
                ("clipboard", "Clipboard"),
                ("paperclip", "Attach"),
                # Connectors moved to the Dashboard ("Connectors" section
                # in the sidebar). The widget consumes whatever's already
                # linked via the backend.
            ]:
                b = QPushButton()
                b.setIcon(_icon(icon_name, 18, "#F0F2F8", 1.7))
                b.setIconSize(QSize(18, 18))
                b.setFixedSize(36, 32)
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setToolTip(tip)
                b.setStyleSheet(cap_btn_qss)
                self.cap_buttons[icon_name] = b
                cap_row.addWidget(b)
            cap_row.addStretch()
            outer.addLayout(cap_row)

            self.cap_buttons["apps"].clicked.connect(
                lambda _checked=False: self._toggle_apps_panel())
            self.cap_buttons["folder"].clicked.connect(
                lambda _c=False: self._pick_folder())
            self.cap_buttons["image"].clicked.connect(
                lambda _c=False: self._pick_image())
            self.cap_buttons["clipboard"].clicked.connect(
                lambda _c=False: self._toggle_clipboard())
            self.cap_buttons["paperclip"].clicked.connect(
                lambda _c=False: self._pick_attachment())

            # =========================================================
            # ROW 2b — RECIPE CHIPS (the "I can do real actions" surface)
            # Visible when the input is empty and we're not busy.
            # Each chip pre-fills a structured prompt + sets the right mode.
            # =========================================================
            self.recipes_row = QHBoxLayout()
            self.recipes_row.setSpacing(6)
            self.recipes_row.addStretch()
            # Recipe chips — discoverable quick-actions that match the
            # darker capsule (white-on-dark glass). Hover lifts to accent.
            recipe_qss = (
                "QPushButton{"
                "  color: rgba(240,242,248,225);"
                "  background: rgba(255,255,255,30);"
                "  border: 1px solid rgba(255,255,255,55);"
                "  border-radius: 13px;"
                "  padding: 5px 12px 5px 8px;"
                "  font-size: 11px;"
                "}"
                "QPushButton:hover{"
                "  background: rgba(91,224,208,80);"
                "  border-color: rgba(91,224,208,180);"
                "  color: #062925;"
                "}"
            )
            self.recipe_buttons = []
            for r in RECIPES:
                btn = QPushButton(r["label"])
                btn.setIcon(_icon(r["icon"], 13, "#F0F2F8", 1.7))
                btn.setIconSize(QSize(13, 13))
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(r["tip"])
                btn.setStyleSheet(recipe_qss)
                btn.clicked.connect(
                    lambda _c=False, rid=r["id"]: self._apply_recipe(rid))
                # Visible by default — recipes are the discoverability hook
                # that tells the user "this is an action agent, not a chat".
                self.recipe_buttons.append(btn)
                self.recipes_row.addWidget(btn)
            self.recipes_row.addStretch()
            outer.addLayout(self.recipes_row)

            # =========================================================
            # ROW 2c — LIVE ACTION TICKER
            # Visible only while a task is running. Shows the last tool
            # the agent invoked so the user sees "what I'm doing right now".
            # =========================================================
            self.action_ticker = QFrame()
            self.action_ticker.setObjectName("action_ticker")
            self.action_ticker.setFixedHeight(28)
            self.action_ticker.setStyleSheet(
                "#action_ticker{"
                "  background: rgba(91,224,208,38);"
                "  border: 1px solid rgba(91,224,208,140);"
                "  border-radius: 14px;"
                "}"
            )
            tk_lay = QHBoxLayout(self.action_ticker)
            tk_lay.setContentsMargins(12, 0, 12, 0)
            tk_lay.setSpacing(8)
            self.action_dot = QLabel("●")
            self.action_dot.setStyleSheet(
                "color: %s; background: transparent; font-size: 14px;" % ACCENT)
            tk_lay.addWidget(self.action_dot)
            self.action_label = QLabel("Working…")
            self.action_label.setStyleSheet(
                "color: #062925; background: transparent; font-size: 11px;")
            self.action_label.setFont(QFont("Segoe UI Variable Text", 9,
                                            QFont.Medium))
            tk_lay.addWidget(self.action_label, 1)
            self.stop_btn_inline = QPushButton("Stop")
            self.stop_btn_inline.setFixedSize(50, 22)
            self.stop_btn_inline.setCursor(Qt.PointingHandCursor)
            self.stop_btn_inline.setStyleSheet(
                "QPushButton{"
                "  color: #FFFFFF;"
                "  background: rgba(220,90,90,160);"
                "  border: 1px solid rgba(255,255,255,90);"
                "  border-radius: 11px;"
                "  font-size: 10px;"
                "  font-weight: 600;"
                "}"
                "QPushButton:hover{ background: rgba(220,90,90,210); }"
            )
            self.stop_btn_inline.clicked.connect(self._cancel_running)
            tk_lay.addWidget(self.stop_btn_inline)
            self.action_ticker.hide()
            outer.addWidget(self.action_ticker)

            # =========================================================
            # ROW 3 — dynamic area: horizontal scroll of app thumbnails
            # (hidden until Apps is toggled). No bg — the thumbnails float
            # below the capsule like the Perplexity reference.
            # =========================================================
            self.apps_scroll = QScrollArea()
            self.apps_scroll.setWidgetResizable(True)
            self.apps_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.apps_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.apps_scroll.setFixedHeight(170)
            self.apps_scroll.setFrameShape(QFrame.NoFrame)
            self.apps_scroll.setStyleSheet(
                "QScrollArea{background:transparent;border:none;}"
                "QScrollBar:horizontal{background:transparent;height:6px;margin:0;}"
                "QScrollBar::handle:horizontal{"
                "  background:rgba(255,255,255,90);border-radius:3px;min-width:24px;}"
                "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
            )
            apps_container = QWidget()
            apps_container.setStyleSheet("background: transparent;")
            self.apps_panel_layout = QHBoxLayout(apps_container)
            self.apps_panel_layout.setContentsMargins(8, 8, 8, 8)
            self.apps_panel_layout.setSpacing(14)
            self.apps_panel_layout.addStretch()
            self.apps_scroll.setWidget(apps_container)
            self.apps_scroll.hide()
            outer.addWidget(self.apps_scroll)

            # ── FOLDER PANEL — Perplexity-style native folder cards
            # Click the folder cap icon to toggle. Shows the user's main
            # folders with their actual Windows folder icons + item counts.
            self.folder_panel = QFrame()
            self.folder_panel.setObjectName("folder_panel")
            self.folder_panel.setStyleSheet(
                "#folder_panel{ background: transparent; border: none; }")
            fp_outer = QVBoxLayout(self.folder_panel)
            fp_outer.setContentsMargins(8, 4, 8, 4)
            fp_outer.setSpacing(8)
            fp_cards = QHBoxLayout()
            fp_cards.setSpacing(14)
            fp_cards.addStretch()
            self._folder_cards = []
            home = os.path.expanduser("~")
            for label, sub in [
                ("Downloads", "Downloads"),
                ("Documents", "Documents"),
                ("Screenshots", "Pictures\\Screenshots"),
                ("Desktop", "Desktop"),
                ("Videos", "Videos"),
            ]:
                path = os.path.join(home, sub)
                card = self._make_folder_card(label, path)
                if card is not None:
                    self._folder_cards.append(card)
                    fp_cards.addWidget(card)
            fp_cards.addStretch()
            fp_outer.addLayout(fp_cards)
            choose = QPushButton("Choose Folder")
            choose.setCursor(Qt.PointingHandCursor)
            choose.setStyleSheet(
                "QPushButton{"
                "  color: rgba(240,242,248,235);"
                "  background: rgba(255,255,255,28);"
                "  border: 1px solid rgba(255,255,255,75);"
                "  border-radius: 14px;"
                "  padding: 6px 18px;"
                "  font-size: 11px;"
                "  font-weight: 500;"
                "}"
                "QPushButton:hover{ background: rgba(255,255,255,48);"
                "  border-color: rgba(255,255,255,140); }"
            )
            choose.clicked.connect(lambda _c=False: self._pick_folder_dialog())
            choose_row = QHBoxLayout()
            choose_row.addStretch()
            choose_row.addWidget(choose)
            choose_row.addStretch()
            fp_outer.addLayout(choose_row)
            self.folder_panel.hide()
            outer.addWidget(self.folder_panel)

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

            # Streaming answer card — created at submit time, grows as text
            # deltas arrive, finalized with sources on `done`.
            self._answer_card = None
            self._answer_text_buf = ""
            self._answer_tools_used: list[str] = []

            # ---- backend wiring ----
            self.runner = TaskRunner()
            self.runner.agentDelta.connect(self._on_agent_delta)
            self.runner.toolUsed.connect(self._on_tool_used)
            self.runner.toolResult.connect(self._on_tool_result)
            self.runner.statusChanged.connect(self._on_status)
            self.runner.finished.connect(self._on_finished)
            self.runner.runningChanged.connect(self._on_running)
            self.runner.widgetRequested.connect(self._spawn_widget)

            # ---- SSE listener for server-pushed widgets ----
            self.sse = SSEListener(BASE)
            self.sse.widgetRequested.connect(self._spawn_widget)
            self.sse.start()

            self.adjustSize()

        # --- Apps panel: horizontal cards of open windows w/ live thumbnails ---
        def _toggle_apps_panel(self) -> None:
            checked = self.cap_buttons["apps"].isChecked()
            if not checked:
                self.apps_scroll.hide()
                self._scoped_window = None
                self.input.setPlaceholderText("Start a task…")
                self._adjust()
                return
            # clear any previous cards (keep the trailing stretch)
            while self.apps_panel_layout.count() > 1:
                item = self.apps_panel_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            wins = _list_open_windows()
            # de-dupe by exe + skip our own capsule
            seen_exes = set()
            own_title = self.windowTitle()
            cards_added = 0
            for w in wins:
                if w["title"] == own_title:
                    continue
                key = (w["exe"] or w["title"]).lower()
                if key in seen_exes:
                    continue
                seen_exes.add(key)
                card = self._make_app_card(w)
                if card is None:
                    continue
                # insert before the trailing stretch
                self.apps_panel_layout.insertWidget(
                    self.apps_panel_layout.count() - 1, card)
                cards_added += 1
            if cards_added == 0:
                lbl = QLabel("No open windows detected.")
                lbl.setStyleSheet("color: rgba(255,255,255,160); padding: 12px;")
                self.apps_panel_layout.insertWidget(0, lbl)
            self.apps_scroll.show()
            self._adjust()

        def _make_app_card(self, win: dict):
            """A 200×150 card: window thumbnail with the .exe icon as a
            badge in the bottom-left corner. Click to scope the agent to it."""
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QPixmap

            CARD_W, CARD_H = 200, 150
            THUMB_H = 120

            thumb = _capture_window_pixmap(win["hwnd"], CARD_W - 12, THUMB_H - 12)

            card = QPushButton()
            card.setCheckable(True)
            card.setCursor(Qt.PointingHandCursor)
            card.setFixedSize(CARD_W, CARD_H)
            card.setToolTip(win["title"])
            card.setStyleSheet(
                "QPushButton{"
                "  background: rgba(255,255,255,75);"
                "  border: 1px solid rgba(255,255,255,100);"
                "  border-radius: 14px;"
                "  padding: 0;"
                "}"
                "QPushButton:hover{"
                "  background: rgba(255,255,255,110);"
                "  border-color: rgba(255,255,255,170);"
                "}"
                "QPushButton:checked{"
                "  background: rgba(91,224,208,130);"
                "  border: 1.5px solid rgba(91,224,208,230);"
                "}"
            )

            # Thumbnail label, parented to the card so we can overlay an icon badge.
            thumb_lbl = QLabel(card)
            thumb_lbl.setGeometry(6, 6, CARD_W - 12, THUMB_H - 12)
            thumb_lbl.setAlignment(Qt.AlignCenter)
            thumb_lbl.setStyleSheet(
                "QLabel{"
                "  background: rgba(255,255,255,160);"
                "  border-radius: 10px;"
                "  color: rgba(30,34,42,220);"
                "}"
            )
            if thumb is not None and not thumb.isNull():
                thumb_lbl.setPixmap(thumb)
                thumb_lbl.setScaledContents(False)
            else:
                # No screenshot (Chromium/UWP/protected window) — center the
                # large app icon on a soft gradient. Cleaner than a black box.
                thumb_lbl.setStyleSheet(
                    "QLabel{"
                    "  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "    stop:0 rgba(255,255,255,200),"
                    "    stop:1 rgba(255,255,255,140));"
                    "  border-radius: 10px;"
                    "}"
                )
                big_icon = _icon_for_exe(win["exe"], 56)
                if big_icon is not None:
                    thumb_lbl.setPixmap(big_icon)
                    thumb_lbl.setAlignment(Qt.AlignCenter)
                else:
                    short = (win["title"] or "?").strip()[:1].upper()
                    f = QFont("Segoe UI Variable Display", 32)
                    f.setWeight(QFont.DemiBold)
                    thumb_lbl.setFont(f)
                    thumb_lbl.setText(short)
                    thumb_lbl.setStyleSheet(thumb_lbl.styleSheet() +
                                             "QLabel{color: rgba(30,34,42,200);}")

            # App-icon badge — bottom-left, hangs slightly off the thumbnail
            badge = QLabel(card)
            badge.setFixedSize(34, 34)
            badge.setStyleSheet(
                "QLabel{"
                "  background: rgba(255,255,255,240);"
                "  border: 1px solid rgba(255,255,255,200);"
                "  border-radius: 8px;"
                "}"
            )
            badge.setAlignment(Qt.AlignCenter)
            badge.move(8, THUMB_H - 10)
            pm = _icon_for_exe(win["exe"], 26)
            if pm is not None:
                badge.setPixmap(pm)

            # App name under the thumbnail
            from os.path import basename, splitext
            short_name = splitext(basename(win["exe"]))[0] if win["exe"] else win["title"][:20]
            name_lbl = QLabel(short_name[:22], card)
            name_lbl.setGeometry(50, THUMB_H + 2, CARD_W - 58, 22)
            name_lbl.setStyleSheet("color:#F2F4F8; background: transparent;")
            f2 = QFont("Segoe UI Variable Text", 9)
            f2.setWeight(QFont.Medium)
            name_lbl.setFont(f2)

            card.clicked.connect(
                lambda _c=False, w=win, b=card: self._scope_to_window(w, b))
            return card

        def _scope_to_window(self, win: dict, btn) -> None:
            # uncheck siblings — only one app scoped at a time
            for i in range(self.apps_panel_layout.count()):
                w = self.apps_panel_layout.itemAt(i).widget()
                if w is not btn and hasattr(w, "setChecked"):
                    w.setChecked(False)
            self._scoped_window = win if btn.isChecked() else None
            self._refresh_placeholder()

        # ── Folder panel — Perplexity-style inline cards ──────────────
        def _pick_folder(self) -> None:
            """Toggle the inline folder panel showing Downloads/Documents/etc.
            A "Choose Folder" button opens the native dialog for custom paths."""
            checked = self.cap_buttons["folder"].isChecked()
            self.folder_panel.setVisible(checked)
            if not checked:
                self._scoped_folder = None
                self._refresh_placeholder()
            self._adjust()

        def _pick_folder_dialog(self) -> None:
            """Native file dialog for arbitrary folder selection."""
            from PySide6.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(
                self, "Pick a project folder",
                self._scoped_folder or os.path.expanduser("~"))
            if folder:
                self._scoped_folder = folder
                self._refresh_placeholder()
                self.cap_buttons["folder"].setChecked(False)
                self.folder_panel.hide()
                self._adjust()

        def _select_folder_card(self, path: str, card_btn) -> None:
            """Click handler for one of the folder cards in the panel."""
            for c in self._folder_cards:
                if c is not card_btn:
                    c.setChecked(False)
            self._scoped_folder = path if card_btn.isChecked() else None
            self._refresh_placeholder()

        def _make_folder_card(self, label: str, path: str):
            """Build one folder card: native blue folder icon + label + count.
            Returns None if the folder doesn't exist on this machine."""
            from PySide6.QtCore import QFileInfo, Qt
            from PySide6.QtGui import QIcon, QPixmap
            from PySide6.QtWidgets import QFileIconProvider

            if not os.path.isdir(path):
                return None

            # Count items (cheap — main user folders are small enough)
            try:
                items = sum(1 for _ in os.scandir(path))
            except Exception:
                items = 0

            CARD_W, CARD_H = 110, 120
            card = QPushButton()
            card.setCheckable(True)
            card.setCursor(Qt.PointingHandCursor)
            card.setFixedSize(CARD_W, CARD_H)
            card.setToolTip(path)
            card.setStyleSheet(
                "QPushButton{"
                "  background: transparent;"
                "  border: 1px solid transparent;"
                "  border-radius: 14px;"
                "  text-align: center;"
                "}"
                "QPushButton:hover{ background: rgba(255,255,255,28); }"
                "QPushButton:checked{"
                "  background: rgba(255,255,255,48);"
                "  border: 1.5px solid rgba(255,255,255,140);"
                "}"
            )

            # Native blue folder icon via shell
            icon_lbl = QLabel(card)
            icon_lbl.setGeometry((CARD_W - 64) // 2, 10, 64, 60)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("background: transparent;")
            provider = QFileIconProvider()
            qicon = provider.icon(QFileInfo(path))
            pm = qicon.pixmap(56, 56) if qicon and not qicon.isNull() else None
            if pm is not None and not pm.isNull():
                icon_lbl.setPixmap(pm)

            # Folder name
            name_lbl = QLabel(label, card)
            name_lbl.setGeometry(4, 74, CARD_W - 8, 18)
            name_lbl.setAlignment(Qt.AlignCenter)
            name_lbl.setStyleSheet(
                "color: #FFFFFF; background: transparent;")
            f = QFont("Segoe UI Variable Text", 10, QFont.DemiBold)
            name_lbl.setFont(f)

            # Item count
            count_lbl = QLabel(f"{items} items", card)
            count_lbl.setGeometry(4, 94, CARD_W - 8, 16)
            count_lbl.setAlignment(Qt.AlignCenter)
            count_lbl.setStyleSheet(
                "color: rgba(240,242,248,170); background: transparent;")
            count_lbl.setFont(QFont("Segoe UI Variable Text", 9))

            card.clicked.connect(
                lambda _c=False, p=path, b=card: self._select_folder_card(p, b))
            return card

        # ── Image picker ────────────────────────────────────────────────
        # Trick: open the image with the OS default viewer, then scope the
        # agent to that viewer window. PrintWindow → vision model reads it.
        # No backend changes; reuses the existing isolated-mode pipeline.
        def _pick_image(self) -> None:
            from PySide6.QtWidgets import QFileDialog
            checked = self.cap_buttons["image"].isChecked()
            if not checked:
                self._scoped_image = None
                self._refresh_placeholder()
                return
            path, _ = QFileDialog.getOpenFileName(
                self, "Pick an image",
                os.path.expanduser("~"),
                "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
            if not path:
                self.cap_buttons["image"].setChecked(False)
                return
            try:
                os.startfile(path)  # open in default viewer (Photos / Paint)
            except Exception as exc:
                self.cap_buttons["image"].setChecked(False)
                print(f"[capsule] couldn't open image: {exc}", flush=True)
                return
            # The Photos app titles its window with the filename; sleep briefly
            # then look it up so the agent can find the HWND.
            QTimer.singleShot(1800, lambda: self._lock_in_image(path))

        def _lock_in_image(self, path: str) -> None:
            fname = os.path.basename(path)
            # Find a window whose title contains the filename
            wins = _list_open_windows()
            stem = os.path.splitext(fname)[0]
            match = next(
                (w for w in wins
                 if stem.lower() in w["title"].lower()
                 or fname.lower() in w["title"].lower()),
                None,
            )
            viewer_title = match["title"] if match else fname
            self._scoped_image = {"path": path, "viewer_title": viewer_title}
            self._refresh_placeholder()

        # ── Clipboard ───────────────────────────────────────────────────
        def _toggle_clipboard(self) -> None:
            from PySide6.QtWidgets import QApplication
            checked = self.cap_buttons["clipboard"].isChecked()
            if not checked:
                self._clipboard_text = None
                self._refresh_placeholder()
                return
            cb = QApplication.clipboard()
            text = cb.text() or ""
            if not text.strip():
                # try image — could expand later. For now, just notify.
                self.cap_buttons["clipboard"].setChecked(False)
                self.status.setText("Clipboard empty")
                self.status.show()
                QTimer.singleShot(1800, self.status.hide)
                return
            self._clipboard_text = text
            self._refresh_placeholder()

        # ── Paperclip (any-file attach) ─────────────────────────────────
        def _pick_attachment(self) -> None:
            from PySide6.QtWidgets import QFileDialog
            checked = self.cap_buttons["paperclip"].isChecked()
            if not checked:
                self._attached_file = None
                self._refresh_placeholder()
                return
            path, _ = QFileDialog.getOpenFileName(
                self, "Attach a file", os.path.expanduser("~"))
            if not path:
                self.cap_buttons["paperclip"].setChecked(False)
                return
            is_image = path.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
            if is_image:
                # Treat like the Image button.
                try:
                    os.startfile(path)
                except Exception:
                    pass
                QTimer.singleShot(1800, lambda: self._lock_in_image(path))
                self._attached_file = {"path": path, "is_image": True}
            else:
                # Read first 8KB of text and inline it in the goal.
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read(8000)
                except Exception as exc:
                    text = f"<could not read: {exc}>"
                self._attached_file = {"path": path, "text": text,
                                       "is_image": False}
            self._refresh_placeholder()

        # ── Push-to-talk via Windows Speech Recognition (SAPI) ──────────
        # ── Voice conversation (local, free: Windows STT in + SAPI TTS out) ──
        def _toggle_voice_mode(self) -> None:
            """Turn the talk-to-it / it-talks-back loop on or off."""
            self._voice_mode = self.voice_btn.isChecked()
            try:
                from . import voice as _voice
            except Exception:
                _voice = None
            if self._voice_mode:
                self.voice_btn.setIcon(_icon("sound", 16, "#06231f", 1.8))
                if _voice and not _voice.tts_available():
                    self.statusChanged_local("Voice output unavailable on this PC")
                self.statusChanged_local("Voice on — click the mic and talk")
            else:
                self.voice_btn.setIcon(_icon("soundoff", 16, "#1A1D24", 1.8))
                try:
                    if _voice:
                        _voice.stop_speaking()
                except Exception:
                    pass

        def _toggle_mic(self) -> None:
            """Click to start/stop a single dictation capture."""
            if self._listening:
                # let the in-flight recognition finish on its own; just un-check
                self.mic_btn.setChecked(False)
                return
            try:
                from . import voice as _voice
            except Exception:
                self.statusChanged_local("Voice not available")
                self.mic_btn.setChecked(False)
                return
            if not _voice.stt_available():
                self.statusChanged_local("Speech recognition unavailable on this PC")
                self.mic_btn.setChecked(False)
                return
            self._listening = True
            self.mic_btn.setChecked(True)
            self.status.setText("Listening…")
            self.status.show()

            def _run():
                text = ""
                try:
                    text = _voice.listen(timeout=8.0)
                except Exception as exc:
                    print(f"[capsule] mic error: {exc}", flush=True)
                # marshal back to the UI thread
                self.transcriptReady.emit(text or "")

            threading.Thread(target=_run, daemon=True).start()

        def _on_transcript(self, text: str) -> None:
            self._listening = False
            self.mic_btn.setChecked(False)
            self.status.hide()
            text = (text or "").strip()
            if not text:
                self.statusChanged_local(
                    "Didn't catch that — check your mic is on")
                return
            if self._setup_mode:
                self.input.setText(text)
                return
            # fill the input; in voice mode, submit hands-free
            self.input.setText((self.input.text() + " " + text).strip()
                               if self.input.text().strip() else text)
            self.input.setFocus()
            if self._voice_mode:
                self._submit()

        def _speak_answer(self, text: str) -> None:
            """In voice mode, read the agent's reply aloud, then re-arm the mic
            for a hands-free next turn."""
            if not self._voice_mode:
                return
            try:
                from . import voice as _voice
                _voice.speak(text)
            except Exception as exc:
                print(f"[capsule] speak error: {exc}", flush=True)
            # hands-free: listen again a moment after the reply starts
            QTimer.singleShot(900, self._auto_listen_next)

        def _auto_listen_next(self) -> None:
            if self._voice_mode and not self._listening and not self._busy:
                self._toggle_mic()

        def statusChanged_local(self, msg: str) -> None:
            self.status.setText(msg)
            self.status.show()
            QTimer.singleShot(2000, self.status.hide)

        # ── Unified placeholder refresh ─────────────────────────────────
        def _refresh_placeholder(self) -> None:
            if self._scoped_window:
                t = self._scoped_window["title"][:38]
                self.input.setPlaceholderText(f"Ask about “{t}”…")
            elif self._scoped_image:
                f = os.path.basename(self._scoped_image["path"])[:38]
                self.input.setPlaceholderText(f"Ask about image “{f}”…")
            elif self._scoped_folder:
                f = os.path.basename(self._scoped_folder) or self._scoped_folder
                self.input.setPlaceholderText(f"Code in “{f[:38]}”…")
            elif self._clipboard_text:
                n = len(self._clipboard_text)
                self.input.setPlaceholderText(
                    f"Ask about clipboard ({n} chars)…")
            elif self._attached_file and not self._attached_file.get("is_image"):
                f = os.path.basename(self._attached_file["path"])[:38]
                self.input.setPlaceholderText(f"Ask about file “{f}”…")
            else:
                self.input.setPlaceholderText("Start a task…")
            if not self._busy:
                self._set_capsule_state("context_ready")

        # --- task flow ---
        # ── First-run onboarding ────────────────────────────────────────────
        def _check_setup(self) -> None:
            """On boot, ask the server whether any provider key is configured.
            If not, switch the capsule into a friendly key-entry mode. Also apply
            the user's saved voice preference so the capsule starts the way they
            left it. Runs on the GUI thread (QTimer), so widget calls are safe."""
            prefs = None
            try:
                import httpx
                with httpx.Client(timeout=3.0) as c:
                    c.post(f"{BASE}/api/session")
                    r = c.get(f"{BASE}/api/setup/status")
                    try:
                        pr = c.get(f"{BASE}/api/preferences")
                        if pr.status_code == 200:
                            prefs = pr.json().get("preferences") or {}
                    except Exception:
                        prefs = None
                if r.status_code == 200 and not r.json().get("configured"):
                    self._enter_setup_mode()
            except Exception:
                pass  # offline / server not ready — stay in normal mode
            # Honor the saved voice preference (talk-to-it / it-talks-back).
            try:
                if prefs and (prefs.get("speak_replies") or prefs.get("voice_input")):
                    if not self.voice_btn.isChecked():
                        self.voice_btn.setChecked(True)
                        self._toggle_voice_mode()
            except Exception:
                pass

        def _enter_setup_mode(self) -> None:
            self._setup_mode = True
            self.input.setPlaceholderText(
                "Paste your free OpenRouter key, then press Enter…")
            self._spawn_widget({
                "title": "Welcome to AI Computer",
                "icon": "sparkles",
                "text": ("Let's get you set up. Paste a free OpenRouter API key "
                         "below and press Enter — that's it.\n\n"
                         "Grab one in ~30 seconds at openrouter.ai/keys "
                         "(no credit card needed)."),
                "buttons": [{"label": "Get a free key", "style": "secondary",
                             "action": "open_url",
                             "payload": "https://openrouter.ai/keys"}],
            })
            self._adjust()

        def _save_provider_key(self, key: str) -> None:
            self.action_label.setText("Saving your key…")
            try:
                import httpx
                with httpx.Client(timeout=8.0) as c:
                    c.post(f"{BASE}/api/session")
                    r = c.post(f"{BASE}/api/setup/provider-key",
                               json={"provider": "openrouter", "key": key})
                if r.status_code == 200:
                    self._setup_mode = False
                    self.input.setPlaceholderText("Start a task…")
                    self._clear_widgets()
                    self._spawn_widget({
                        "title": "You're all set",
                        "icon": "sparkles",
                        "text": "Key saved. Type a goal above to begin — try "
                                "“open notepad and write me a haiku”.",
                    })
                else:
                    detail = ""
                    try:
                        detail = r.json().get("detail", "")
                    except Exception:
                        detail = r.text[:160]
                    self._spawn_widget({
                        "title": "That key didn't work",
                        "icon": "alert",
                        "text": (detail or "Please check the key and try again.")
                                + "\n\nPaste it again below and press Enter.",
                    })
                self._adjust()
            except Exception as exc:
                self._spawn_widget({
                    "title": "Couldn't reach the server",
                    "icon": "alert",
                    "text": str(exc)[:160],
                })
                self._adjust()

        def _submit(self) -> None:
            user_text = self.input.text().strip()
            if not user_text or self._busy:
                return

            # First-run onboarding: the input captures the API key, not a task.
            if self._setup_mode:
                if len(user_text) >= 8:
                    self.input.clear()
                    self._save_provider_key(user_text)
                return

            # Compose the final goal text — prepend any pure-text context
            # (clipboard, text-file attachment) since the model sees it as
            # part of the prompt. Vision/folder context rides on payload fields.
            parts = []
            if self._clipboard_text:
                parts.append(
                    f"[Clipboard contents]\n{self._clipboard_text[:6000]}\n\n")
            if self._attached_file and not self._attached_file.get("is_image"):
                af = self._attached_file
                parts.append(
                    f"[Attached file: {os.path.basename(af['path'])}]\n"
                    f"{af.get('text','')}\n\n")
            parts.append(user_text)
            goal = "".join(parts)

            recipe_hint = getattr(self, "_recipe_hint", None)
            attach = {
                "window": self._scoped_window,
                "folder": self._scoped_folder,
                # If image scope picked but viewer didn't open in time, fall
                # back to the path so the agent at least knows where it lives.
                "image_window_title": (self._scoped_image or {}).get("viewer_title"),
                "recipe_mode": (recipe_hint or {}).get("mode"),
            }
            # Recipe used → clear it so next prompt is fresh
            self._recipe_hint = None

            # Multi-turn: KEEP previous answer cards on screen so the capsule
            # reads as a conversation thread, just like Perplexity.
            self.reply.hide()
            self._answer_card = None
            self._answer_text_buf = ""
            self._answer_tools_used = []
            # Spawn a user-message card showing what was asked
            self._spawn_widget({
                "title": "You",
                "icon": "logo",
                "text": user_text[:1000],
            })
            self.input.clear()
            # Persist the goal so we can offer to resume if the widget crashes
            try:
                _df.save_pending_task(goal, attach.get("recipe_mode") or "auto")
            except Exception:
                pass
            self.runner.submit(goal, attach)
            self._adjust()

        def _spawn_widget(self, event: dict):
            """Create a DynamicWidget from ANY JSON spec the LLM provides."""
            # The event may have the spec directly, or nested under 'data'
            spec = event.get("data", event) if "data" in event else event
            # Strip internal SSE keys that aren't widget spec fields
            spec = {k: v for k, v in spec.items()
                    if k not in ("type", "widget_type", "event")}
            widget = create_widget(spec, parent=self.widget_container)
            if widget is None:
                return
            widget.dismissed.connect(lambda w=widget: self._remove_widget(w))
            count = self.widget_layout.count()
            self.widget_layout.insertWidget(count - 1, widget)
            self.widget_scroll.show()
            # Force Qt to process the widget's layout NOW so sizeHint reflects
            # the body QLabel, not just the header. Without this the
            # animation's target height is wrong and the body gets clipped.
            widget.adjustSize()
            QApplication.processEvents()
            self._fit_widget_scroll()
            self._adjust()
            QTimer.singleShot(50, widget.animate_in)
            # Re-fit after the animation lands, plus a generous final pass.
            for ms in (550, 900, 1400):
                QTimer.singleShot(ms, self._fit_widget_scroll)
                QTimer.singleShot(ms, self._adjust)

        def _fit_widget_scroll(self) -> None:
            """Grow widget_scroll to fit content (capped at ~65% screen)."""
            try:
                container = self.widget_scroll.widget()
                if container is None:
                    return
                # Force every child card to its preferred height by clearing
                # the animation-imposed maxHeight (the OutCubic curve may not
                # have finished animating before this fires).
                for i in range(self.widget_layout.count()):
                    item = self.widget_layout.itemAt(i)
                    if item is None: continue
                    w = item.widget()
                    if w is not None:
                        w.setMaximumHeight(16777215)
                        w.adjustSize()
                container.adjustSize()
                hint_h = container.sizeHint().height()
                if hint_h <= 0:
                    hint_h = container.minimumSizeHint().height()
                screen = QApplication.instance().primaryScreen().availableGeometry()
                max_h = max(280, int(screen.height() * 0.65))
                target = min(max(hint_h + 12, 160), max_h)
                # Force the scroll area to EXACTLY `target` so the parent
                # layout grows the capsule by that much — without min==max,
                # the layout collapses to the smaller of the two.
                self.widget_scroll.setFixedHeight(target)
                print(f"[capsule] _fit_widget_scroll cards={self.widget_layout.count()-1} "
                      f"hint={hint_h} target={target}", flush=True)
            except Exception as exc:
                print(f"[capsule] _fit_widget_scroll error: {exc}", flush=True)

        def _remove_widget(self, widget):
            self.widget_layout.removeWidget(widget)
            widget.deleteLater()
            # Hide scroll area if no widgets left (only stretch remains)
            if self.widget_layout.count() <= 1:
                self.widget_scroll.hide()
            self._adjust()

        def _clear_widgets(self):
            """Remove all widgets from the container."""
            while self.widget_layout.count() > 1:
                item = self.widget_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self.widget_scroll.hide()

        def _capsule_scope(self) -> str:
            if self._scoped_window:
                title = self._scoped_window.get("title") or "Selected app"
                return title[:28]
            if self._scoped_folder:
                return os.path.basename(self._scoped_folder) or "Folder"
            if self._scoped_image:
                return "Image"
            if self._clipboard_text:
                return "Clipboard"
            if self._attached_file:
                return os.path.basename(self._attached_file.get("path", ""))[:28] or "Attachment"
            return "Computer"

        def _capsule_vision_label(self, state: str) -> str:
            if self._last_control_layer and state in (
                "acting", "planning", "waiting_approval", "paused", "done", "error"
            ):
                return self._last_control_layer
            if state == "listening":
                return "Voice input"
            if state in ("acting", "planning"):
                return "Seeing screen"
            if state == "waiting_approval":
                return "Approval paused"
            if self._scoped_folder:
                return "Folder context"
            if self._clipboard_text or self._attached_file:
                return "Attached context"
            return "Ready"

        def _set_capsule_state(self, state: str, action: str = "") -> None:
            labels = {
                "idle": "Idle",
                "focused": "Ready",
                "context_ready": "Context ready",
                "submitting": "Starting",
                "planning": "Planning",
                "acting": "Acting",
                "waiting_approval": "Needs approval",
                "paused": "Paused",
                "done": "Done",
                "error": "Error",
            }
            self._capsule_state = state
            if action:
                self._last_action_phrase = action
            elif state == "idle":
                self._last_action_phrase = ""
                self._last_control_layer = ""
                self._last_control_reason = ""
            self.scope_chip.setText(self._capsule_scope())
            self.vision_chip.setText(self._capsule_vision_label(state))
            if self._last_control_reason:
                self.vision_chip.setToolTip(self._last_control_reason)
            self.phase_chip.setText(labels.get(state, state.title()))
            should_show = state != "idle" or bool(self._last_action_phrase)
            self.context_bar.setVisible(should_show)
            active = state in ("submitting", "planning", "acting", "waiting_approval", "paused")
            self.context_pause_btn.setVisible(active)
            self.context_stop_btn.setVisible(active)
            if state in ("planning", "acting", "waiting_approval") and self._last_action_phrase:
                self.status.setText(self._last_action_phrase[:90])
            self._adjust()

        def _show_context_details(self) -> None:
            detail = self._last_action_phrase or self.status.text() or "Ready."
            self._spawn_widget({
                "title": f"{self._capsule_scope()} · {self.phase_chip.text()}",
                "icon": "sparkles",
                "text": detail,
            })

        def _pause_or_resume(self) -> None:
            tid = getattr(self, "_current_task_id", None)
            if not tid:
                return
            paused = self._capsule_state == "paused"
            self.status.setText("Resuming..." if paused else "Pausing...")
            def _post():
                try:
                    import httpx
                    with httpx.Client(timeout=5.0) as c:
                        c.post(f"{BASE}/api/session")
                        c.post(f"{BASE}/api/tasks/{tid}/{'resume' if paused else 'pause'}")
                except Exception as exc:
                    print(f"[capsule] pause/resume failed: {exc}", flush=True)
            threading.Thread(target=_post, daemon=True).start()
            self._set_capsule_state("acting" if paused else "paused",
                                    "Task resumed" if paused else "Task paused")

        def _on_running(self, running: bool) -> None:
            self._busy = running
            # Hide recipe chips while busy; show the action ticker.
            # When idle, show recipes again so the user knows what's possible.
            for b in self.recipe_buttons:
                b.setVisible(not running)
            self.action_ticker.setVisible(running)
            if running:
                self.action_label.setText("Starting…")
                self._current_task_id = self.runner.current_task_id
                self._set_capsule_state("submitting", "Starting task...")
            self.input.setVisible(not running)
            self.status.setVisible(running)
            if not running:
                self.status.hide()
                self.input.show()
                self._current_task_id = None
                if self._capsule_state not in ("done", "error"):
                    self._set_capsule_state("idle", "")
            self.update()
            QTimer.singleShot(0, self._adjust)

        # Status strings the backend emits that aren't useful for the user.
        # Filter them so the live ticker stays clean.
        _NOISY_STATUS = (
            "Structured planning failed",
            "Native tool stream stalled",
            "falling back to XML",
            "Client error '404",
            "validation error",
            "Field required",
        )

        def _on_status(self, msg: str) -> None:
            if not msg:
                return
            lower = msg.lower()
            # Surface OpenRouter rate-limits in plain English instead of JSON
            if "rate-limited" in lower or "429" in lower or "retry shortly" in lower:
                self.status.setText("Free tier rate-limited — retrying…")
                self._set_capsule_state("planning", "Rate-limited — retrying...")
                return
            if "openrouter error" in lower:
                # Many of these are transient; show a calm placeholder
                self.status.setText("Switching models…")
                self._set_capsule_state("planning", "Switching models...")
                return
            if ("waiting for approval" in lower
                    or "waiting on permission" in lower
                    or "needs approval" in lower):
                clean = msg.strip()
                self.status.setText(clean[:90])
                self._set_capsule_state("waiting_approval", clean[:90])
                return
            if "paused" in lower:
                clean = msg.strip()
                self.status.setText(clean[:90])
                self._set_capsule_state("paused", clean[:90])
                return
            if "resumed" in lower:
                clean = msg.strip()
                self.status.setText(clean[:90])
                self._set_capsule_state("acting", clean[:90])
                return
            for noise in self._NOISY_STATUS:
                if noise.lower() in lower:
                    self.status.setText("Adjusting strategy…")
                    self._set_capsule_state("planning", "Adjusting strategy...")
                    return
            # Trim and show clean status text
            clean = msg.strip()
            if "waiting on model" in lower:
                return
            self.status.setText(clean[:90])
            state = "planning" if any(k in lower for k in (
                "planning", "thinking", "model", "initializing", "strategy"
            )) else "acting"
            self._set_capsule_state(state, clean[:90])

        def _on_agent_delta(self, delta: str) -> None:
            """Append a streaming text chunk to the live answer card."""
            self._answer_text_buf += delta
            if self._answer_card is None:
                # Lazy-create the answer card on the first delta
                spec = {
                    "title": "Answer",
                    "icon": "sparkles",
                    "text": self._answer_text_buf[:4000],
                }
                self._spawn_widget(spec)
                # capture the just-spawned widget for live updates
                idx = self.widget_layout.count() - 2  # before trailing stretch
                if idx >= 0:
                    self._answer_card = self.widget_layout.itemAt(idx).widget()
            else:
                self._update_card_text(self._answer_card, self._answer_text_buf)

        def _overlay_control_layer(self, overlay: dict | None) -> tuple[str, str]:
            if not isinstance(overlay, dict):
                return "", ""
            layer = str(overlay.get("control_layer") or "").strip()
            reason = str(overlay.get("control_reason") or overlay.get("fallback_reason") or "").strip()
            return layer, reason

        def _apply_overlay(self, overlay: dict | None, fallback_name: str = "") -> bool:
            """Render structured overlay metadata from the backend.

            Returns True when it drew something, so legacy text parsing can be
            skipped. Status-only overlays still update the capsule ticker but do
            not draw arbitrary screen markers.
            """
            if not isinstance(overlay, dict) or not overlay:
                return False
            label = str(overlay.get("label") or "").strip()
            otype = str(overlay.get("type") or "").lower()
            kind = str(overlay.get("kind") or fallback_name or "").lower()
            layer, reason = self._overlay_control_layer(overlay)

            def rect_from(key: str):
                r = overlay.get(key)
                if not isinstance(r, dict):
                    return None
                try:
                    l = int(r.get("left", 0))
                    t = int(r.get("top", 0))
                    w = int(r.get("width", 0))
                    h = int(r.get("height", 0))
                    if w > 0 and h > 0:
                        return l, t, w, h
                except Exception:
                    return None
                return None

            if label:
                self.action_label.setText(label[:80])
                self._pulse_action_dot()
            if layer:
                self._last_control_layer = layer[:28]
                self._last_control_reason = reason
                self.vision_chip.setText(layer[:28])
                if reason:
                    self.vision_chip.setToolTip(reason)

            if otype == "uia_control":
                rect = rect_from("rect")
                if rect:
                    self._vcursor.show_uia(*rect, label=label, kind=kind or "find")
                    return True
            if otype == "app_focus":
                rect = rect_from("app_rect") or rect_from("rect")
                if rect:
                    self._vcursor.show_app_focus(*rect, label=label)
                    return True
            if otype == "point":
                pt = overlay.get("point")
                if isinstance(pt, dict):
                    try:
                        x, y = int(pt.get("x", 0)), int(pt.get("y", 0))
                    except Exception:
                        return bool(label)
                    if kind in ("click", "double_click"):
                        self._vcursor.show_click(x, y, label=label or "Clicking")
                    elif kind == "drag":
                        self._vcursor.show_click(x, y, label=label or "Dragging")
                    elif kind == "type":
                        self._vcursor.show_type(x, y, text=label or "Typing")
                    else:
                        self._vcursor.show_action(label or "Working", x, y)
                    return True
            return bool(label)

        def _on_tool_result(self, name: str, output: str, overlay: dict | None = None) -> None:
            """Fire the cursor overlay from the RESULT event — args_summary
            on action_start is often template names ("x, y, button"); the
            output text carries real coordinates like
            'Clicked left 1 times at 656, 525'."""
            lname = (name or "").lower()
            if self._apply_overlay(overlay, lname):
                return
            # ── UIA actions: trace the exact control bounds when available ──
            if lname in ("uia_click", "uia_type", "uia_find", "uia_wait"):
                try:
                    import re as _re
                    tgt_m = _re.search(r"'([^']+)'", output)
                    tgt = (tgt_m.group(1) if tgt_m else "").strip()
                    if lname == "uia_click":
                        label = f"Clicking {tgt}" if tgt else "Clicking"
                    elif lname == "uia_type":
                        label = f"Typing into {tgt}" if tgt else "Typing"
                    elif lname == "uia_wait":
                        label = f"Ready: {tgt}" if tgt else "Ready"
                    else:
                        label = f"Found {tgt}" if tgt else "Located"
                    cm = _re.search(r"\[uia:(-?\d+),(-?\d+),(\d+),(\d+)\]", output)
                    if cm:
                        l, t, w, h = (int(cm.group(i)) for i in range(1, 5))
                        kind = ("type" if lname == "uia_type"
                                else "find" if lname in ("uia_find", "uia_wait") else "click")
                        self._vcursor.show_uia(l, t, w, h, label=label, kind=kind)
                    else:
                        am = _re.search(r"\[app:(-?\d+),(-?\d+),(\d+),(\d+)\]", output)
                        if am:
                            l, t, w, h = (int(am.group(i)) for i in range(1, 5))
                            self._vcursor.show_app_focus(l, t, w, h, label=label)
                except Exception as exc:
                    print(f"[capsule] uia overlay error: {exc}", flush=True)
                return
            if lname not in ("mouse_click", "double_click",
                              "left_click_drag", "keyboard_type",
                              "type_with_delay"):
                return
            try:
                if lname in ("mouse_click", "double_click", "left_click_drag"):
                    xy = parse_click_xy(output)
                    print(f"[cursor] {lname} -> xy={xy} from {output[:60]!r}",
                          flush=True)
                    if xy is not None:
                        verb = ("Double-clicked" if lname == "double_click"
                                else "Dragged" if lname == "left_click_drag"
                                else "Clicked")
                        self._vcursor.show_click(*xy, label=verb)
                elif lname in ("keyboard_type", "type_with_delay"):
                    # Output often says: "Typed 'hello world'" — surface it
                    import re as _re
                    m = _re.search(r"[Tt]yped\s+['\"]?([^'\"]+)", output)
                    text = m.group(1)[:24] if m else output[:24]
                    cx = self._vcursor._cursor_x if self._vcursor._cursor_x > 0 else 600
                    cy = self._vcursor._cursor_y if self._vcursor._cursor_y > 0 else 400
                    self._vcursor.show_type(cx, cy, text=text)
            except Exception as exc:
                print(f"[capsule] _on_tool_result error: {exc}", flush=True)

        def _on_tool_used(self, name: str, args: str, overlay: dict | None = None) -> None:
            if name and name not in self._answer_tools_used:
                self._answer_tools_used.append(name)
            # Update the live ticker so the user sees "what I'm doing now"
            phrase = _humanize_tool(name, args)
            self.action_label.setText(phrase[:80])
            self._set_capsule_state("acting", phrase[:90])
            self._pulse_action_dot()
            overlay_applied = self._apply_overlay(overlay, (name or "").lower())
            if (
                overlay_applied
                and isinstance(overlay, dict)
                and overlay.get("type") in ("point", "uia_control", "app_focus")
            ):
                return

            # Virtual cursor — paint an animated cursor with a smooth
            # bezier path + ripple + action label whenever the agent does
            # ANYTHING. Free models can flail with coordinates, so giving
            # the user a clear visual cue is essential.
            lname = (name or "").lower()
            try:
                if lname in ("mouse_click", "double_click"):
                    xy = parse_click_xy(args)
                    if xy is not None:
                        verb = "Double-clicking" if lname == "double_click" else "Clicking"
                        self._vcursor.show_click(*xy, label=verb)
                elif lname == "left_click_drag":
                    xy = parse_click_xy(args)
                    if xy is not None:
                        self._vcursor.show_click(*xy, label="Dragging")
                elif lname in ("keyboard_type", "type_with_delay"):
                    self._vcursor.show_type(
                        self._vcursor._cursor_x if self._vcursor._cursor_x > 0 else 600,
                        self._vcursor._cursor_y if self._vcursor._cursor_y > 0 else 400,
                        text=str(args)[:32],
                    )
                elif lname == "key":
                    self._vcursor.show_action(f"Pressing {str(args)[:24]}")
                elif lname == "scroll":
                    self._vcursor.show_action("Scrolling")
                elif lname == "screenshot":
                    self._vcursor.show_action("Looking at the screen")
                elif lname == "screen_context":
                    self._vcursor.show_action("Reading the screen")
                elif lname == "focus_window":
                    self._vcursor.show_action(f"Focusing {str(args)[:24]}")
                elif lname == "find_on_screen":
                    self._vcursor.show_action("Locating element")
                elif lname == "web_search":
                    self._vcursor.show_action(f"Searching: {str(args)[:24]}")
                elif lname == "web_fetch":
                    self._vcursor.show_action(f"Fetching {str(args)[:30]}")
                elif lname == "write_file":
                    self._vcursor.show_action(f"Writing {str(args)[:24]}")
                elif lname == "read_file":
                    self._vcursor.show_action(f"Reading {str(args)[:24]}")
            except Exception:
                pass

        def _pulse_action_dot(self) -> None:
            """Quick teal flash on the live-ticker dot when a new tool runs."""
            try:
                self.action_dot.setStyleSheet(
                    "color: #B6FFEC; background: transparent; font-size: 14px;")
                QTimer.singleShot(220, lambda: self.action_dot.setStyleSheet(
                    "color: %s; background: transparent; font-size: 14px;" % ACCENT))
            except Exception:
                pass

        # ── Recipe library ────────────────────────────────────────────
        def _apply_recipe(self, recipe_id: str) -> None:
            r = next((x for x in RECIPES if x["id"] == recipe_id), None)
            if not r:
                return
            # Fill the input with the recipe prompt. If the prompt ends with
            # a trailing space (waiting for user to add a topic / URL), put
            # the cursor at the end and focus.
            self.input.setText(r["prompt"])
            self.input.setFocus()
            try:
                self.input.setCursorPosition(len(self.input.text()))
            except Exception:
                pass
            # Stash the recipe-suggested mode so _build_payload can use it
            self._recipe_hint = {"mode": r["mode"], "verb": r["verb"]}

        # ── Cancel a running task (hard stop) ─────────────────────────
        def _cancel_running(self) -> None:
            tid = getattr(self, "_current_task_id", None)
            if not tid:
                # Try anyway — also pulse the status so user sees feedback
                self.status.setText("Stopping…")
                self._set_capsule_state("paused", "Stopping...")
                return
            self.status.setText("Stopping…")
            self.status.show()
            self._set_capsule_state("paused", "Stopping...")
            def _post():
                try:
                    import httpx
                    with httpx.Client(timeout=5.0) as c:
                        c.post(f"{BASE}/api/session")
                        c.delete(f"{BASE}/api/tasks/{tid}")
                except Exception as exc:
                    print(f"[capsule] cancel error: {exc}", flush=True)
            threading.Thread(target=_post, daemon=True).start()

        def _update_card_text(self, card, text: str) -> None:
            """Find the body QLabel of a DynamicWidget and set its text."""
            if card is None:
                return
            try:
                from PySide6.QtWidgets import QLabel
                labels = [w for w in card.findChildren(QLabel) if w.wordWrap()]
                if labels:
                    labels[-1].setText(text[:4000])
                    # Card may have grown — let the layout reflow then resize.
                    card.adjustSize()
                    if card.maximumHeight() < 100000:
                        card.setMaximumHeight(16777215)
                    self._fit_widget_scroll()
                    self._adjust()
            except Exception:
                pass

        def _on_finished(self, text: str, sources: list) -> None:
            self._busy = False
            self.status.hide()
            self.input.show()
            clean = (text or "").strip()

            # Treat known no-content / boilerplate replies as failures so we
            # don't render an empty "Answer" card.
            if clean.lower() in {
                "", "done.", "done", "complete", "finished",
                "still working — taking longer than expected.",
            }:
                self.status.setText("No response — try rephrasing")
                self._set_capsule_state("error", "No response — try rephrasing")
                self.status.show()
                QTimer.singleShot(3000, self.status.hide)
                self._answer_card = None
                self._answer_text_buf = ""
                self._answer_tools_used = []
                self._adjust()
                return

            # Detect free-tier rate-limit cascade and surface a clear note
            cl = clean.lower()
            if ("rate-limited" in cl or "rate limit" in cl
                    or "openrouter error" in cl):
                # Replace cryptic JSON with a plain-English answer card
                clean = (
                    "All free OpenRouter models are rate-limited right now. "
                    "Wait ~30-60 seconds and try again, or link your own "
                    "OpenRouter key in Settings → Connectors to remove the "
                    "limit. Original error preserved below.\n\n"
                    + clean[:600]
                )

            # Reality-check any "saved to X" file claims so the user sees
            # when the agent hallucinated a file write that didn't happen.
            file_claims = _verify_file_claims(clean)
            if file_claims:
                ok = [p for p, e in file_claims if e]
                bad = [p for p, e in file_claims if not e]
                verify_lines = []
                if ok:
                    verify_lines.append("✓ Verified on disk: " + ", ".join(ok))
                if bad:
                    verify_lines.append("⚠ File not found (agent may have "
                                        "hallucinated): " + ", ".join(bad))
                if verify_lines:
                    clean = clean + "\n\n" + "\n".join(verify_lines)

            if self._answer_card is None and clean:
                # Non-streaming model — render the final reply as a fresh card
                spec = {
                    "title": "Answer",
                    "icon": "sparkles",
                    "text": clean[:4000],
                }
                if sources:
                    spec["buttons"] = [
                        {"label": _shorten_url(u), "style": "secondary",
                         "action": "open_url", "payload": {"url": u}}
                        for u in sources[:6]
                    ]
                if self._answer_tools_used:
                    spec["subtitle"] = "Used: " + ", ".join(
                        self._answer_tools_used[:4])
                self._spawn_widget(spec)
            elif self._answer_card is not None:
                # Streaming card already on screen — patch in sources + tools
                if clean and clean != self._answer_text_buf:
                    self._update_card_text(self._answer_card, clean)
                if sources:
                    self._append_sources_strip(self._answer_card, sources)

            # Task ended successfully — clear pending-task crash flag.
            try:
                _df.clear_pending_task()
            except Exception:
                pass

            # Voice mode: read the reply aloud and re-arm the mic for a
            # hands-free next turn.
            if clean:
                self._speak_answer(clean)

            # Reset streaming state so the NEXT prompt gets a fresh card
            # (multi-turn stacking: don't delete old cards).
            self._answer_card = None
            self._answer_text_buf = ""
            self._answer_tools_used = []
            self._set_capsule_state("done", "Done — result ready")
            self._adjust()

        def _append_sources_strip(self, card, sources: list) -> None:
            """Add a horizontal row of clickable source links under a card."""
            try:
                from PySide6.QtWidgets import (QLabel, QHBoxLayout, QFrame,
                                                QVBoxLayout, QWidget as QW)
                strip = QFrame(card)
                strip.setStyleSheet(
                    "QFrame{background: rgba(255,255,255,28);"
                    " border-radius: 10px; padding: 6px;}"
                )
                lay = QHBoxLayout(strip)
                lay.setContentsMargins(8, 4, 8, 4)
                lay.setSpacing(6)
                lay.addWidget(QLabel("Sources:"))
                for u in sources[:5]:
                    lbl = QLabel(
                        f'<a style="color:#5BE0D0;text-decoration:none" '
                        f'href="{u}">{_shorten_url(u)}</a>'
                    )
                    lbl.setOpenExternalLinks(True)
                    lbl.setStyleSheet("color: #5BE0D0; padding: 2px 6px;")
                    lay.addWidget(lbl)
                lay.addStretch()
                # Find the card's outer layout and append the strip
                if card.layout():
                    card.layout().addWidget(strip)
            except Exception as exc:
                print(f"[capsule] sources strip failed: {exc}", flush=True)

        def _adjust(self) -> None:
            # Compute the target height from the layout, then SMOOTHLY animate
            # the capsule to it (Perplexity-style dynamic growth) instead of
            # snapping. The rounded window region tracks each frame.
            self.adjustSize()
            try:
                hint = self.layout().sizeHint()
                target_h = max(self.minimumHeight(), hint.height())
            except Exception:
                QTimer.singleShot(0, self._reshape)
                return
            cur_h = self.height()
            if target_h == cur_h:
                QTimer.singleShot(0, self._reshape)
                return
            self._animate_height(cur_h, target_h)

        def _animate_height(self, from_h: int, to_h: int) -> None:
            g = self.geometry()
            # cancel any in-flight growth so rapid updates don't stack/fight
            prev = getattr(self, "_grow_anim", None)
            if prev is not None:
                try:
                    prev.stop()
                except Exception:
                    pass
            anim = QPropertyAnimation(self, b"geometry", self)
            # Slightly longer for big jumps, snappy for small ones.
            dist = abs(to_h - from_h)
            anim.setDuration(max(160, min(340, 140 + dist // 3)))
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.setStartValue(QRect(g.x(), g.y(), g.width(), from_h))
            anim.setEndValue(QRect(g.x(), g.y(), g.width(), to_h))

            # While animating, resizeEvent re-clips region-only (fast); full
            # region+acrylic reshape happens once on finish.
            self._animating = True

            def _done():
                self._animating = False
                self._reshape()

            anim.finished.connect(_done)
            self._grow_anim = anim
            anim.start()

        def _reshape(self) -> None:
            hwnd = int(self.winId())
            _round_window(hwnd, self.width(), self.height(), RADIUS)

        def _sample_bg(self) -> None:
            """Sample a thin ring of screen pixels just OUTSIDE the capsule to
            estimate backdrop luminance, then adapt the glass. Cheap; runs on a
            slow timer and after drags."""
            try:
                import PIL.ImageGrab as ig
                tl = self.mapToGlobal(QPoint(0, 0))
                x, y, w, h = tl.x(), tl.y(), self.width(), self.height()
                pad = 22
                shot = ig.grab((x - pad, y - pad, x + w + pad, y + h + pad))
                px = shot.load()
                sw, sh = shot.size
                acc = n = 0
                # top + bottom strips, then left + right strips (the outer ring)
                for yy in range(0, pad, 3):
                    for xx in range(0, sw, 14):
                        for ry in (yy, sh - 1 - yy):
                            r, g, b = px[xx, ry][:3]
                            acc += 0.299 * r + 0.587 * g + 0.114 * b
                            n += 1
                for xx in range(0, pad, 3):
                    for yy in range(0, sh, 14):
                        for rx in (xx, sw - 1 - xx):
                            r, g, b = px[rx, yy][:3]
                            acc += 0.299 * r + 0.587 * g + 0.114 * b
                            n += 1
                if not n:
                    return
                lum = acc / n / 255.0           # 0..1
                # smooth, mapped: <0.42 dark, >0.62 light, ramp between
                target = max(0.0, min(1.0, (lum - 0.42) / 0.20))
                if abs(target - self._bg_light) > 0.03:
                    self._bg_light = target
                    self.update()
                # Discrete content theme with hysteresis so it doesn't flicker
                # near the threshold.
                new_mode = self._light_mode
                if lum > 0.66:
                    new_mode = True
                elif lum < 0.50:
                    new_mode = False
                if new_mode is not None and new_mode != self._light_mode:
                    self._light_mode = new_mode
                    self._apply_palette(new_mode)
            except Exception:
                pass

        def _apply_palette(self, light: bool) -> None:
            """Recolour the content chrome (chips, toolbar icons, close, ticker,
            reply, answer cards) so it stays legible when the glass body flips
            between dark and light. The input pill is already light in both."""
            if light:
                ic = "#1A1D24"                       # dark icons/text
                chip_text = "rgba(28,32,42,245)"
                # Clear glass shows the desktop through it, so the chips carry
                # their OWN opaque-ish surface to stay crisp + readable.
                chip_bg = "rgba(255,255,255,205)"
                chip_bd = "rgba(20,24,32,55)"
                chip_hbg = "rgba(91,224,208,200)"
                chip_hbd = "rgba(40,150,140,230)"
                chip_ht = "#06231f"
                tb_hbg = "rgba(255,255,255,150)"
                tb_hbd = "rgba(20,24,32,45)"
                tb_chk = "rgba(91,224,208,150)"
                ticker = "rgba(38,46,58,235)"
                reply_c = "#1A2230"
                reply_bd = "rgba(20,24,32,0.16)"
            else:
                ic = "#F0F2F8"                       # light icons/text
                chip_text = "rgba(244,246,250,245)"
                # Clear DARK glass: chips carry a denser dark surface so labels
                # stay readable over whatever shows through.
                chip_bg = "rgba(34,40,52,150)"
                chip_bd = "rgba(255,255,255,70)"
                chip_hbg = "rgba(91,224,208,150)"
                chip_hbd = "rgba(91,224,208,210)"
                chip_ht = "#062925"
                tb_hbg = "rgba(255,255,255,55)"
                tb_hbd = "rgba(255,255,255,75)"
                tb_chk = "rgba(91,224,208,90)"
                ticker = ACCENT
                reply_c = "#FFFFFF"
                reply_bd = "rgba(255,255,255,0.15)"
            # recipe chips
            chip_qss = (
                "QPushButton{"
                f"  color: {chip_text};"
                f"  background: {chip_bg};"
                f"  border: 1px solid {chip_bd};"
                "  border-radius: 13px; padding: 5px 12px 5px 8px; font-size: 11px;"
                "}"
                f"QPushButton:hover{{ background: {chip_hbg};"
                f"  border-color: {chip_hbd}; color: {chip_ht}; }}"
            )
            try:
                for btn, r in zip(self.recipe_buttons, RECIPES):
                    btn.setStyleSheet(chip_qss)
                    btn.setIcon(_icon(r["icon"], 13, ic, 1.7))
            except Exception:
                pass
            # toolbar icon buttons
            tb_qss = (
                "QPushButton{ background: transparent;"
                "  border: 1px solid transparent; border-radius: 12px; padding: 6px; }"
                f"QPushButton:hover{{ background: {tb_hbg}; border-color: {tb_hbd}; }}"
                f"QPushButton:checked{{ background: {tb_chk}; border-color: {tb_hbd}; }}"
            )
            try:
                for nm, b in self.cap_buttons.items():
                    b.setStyleSheet(tb_qss)
                    b.setIcon(_icon(nm, 18, ic, 1.7))
            except Exception:
                pass
            # close, ticker, reply
            try:
                self.close_btn.setIcon(_icon("close", 12, ic, 2.2))
            except Exception:
                pass
            try:
                self.action_label.setStyleSheet(
                    f"color:{ticker};background:transparent;")
            except Exception:
                pass
            try:
                self.context_bar.setStyleSheet(
                    "#context_bar{"
                    f"background: {chip_bg}; border: 1px solid {chip_bd};"
                    "border-radius: 15px;}"
                )
                chip_label_qss = (
                    "QLabel{"
                    f"color:{chip_text}; background:{tb_chk};"
                    "border-radius: 10px; padding: 3px 8px;"
                    "font-size: 10px; font-weight: 650;}"
                )
                self.scope_chip.setStyleSheet(
                    chip_label_qss + f"QLabel{{color:{ACCENT};}}")
                self.vision_chip.setStyleSheet(chip_label_qss)
                self.phase_chip.setStyleSheet(chip_label_qss)
            except Exception:
                pass
            try:
                self.reply.setStyleSheet(
                    f"QLabel{{color:{reply_c}; background:transparent; "
                    f"border-top: 1px solid {reply_bd}; "
                    "padding: 18px 5px 5px 5px; margin-top: 5px;}}")
            except Exception:
                pass
            # answer/widget cards created from here on use this palette
            try:
                set_card_palette(light)
            except Exception:
                pass
            self.update()

        # --- liquid-glass painting (adaptive) ---
        def paintEvent(self, _e) -> None:
            # ── Apple "liquid glass" material ────────────────────────────────
            # Layered to mimic a thick slab of refractive glass sitting over the
            # DWM-blurred backdrop:
            #   1. clear cool tint (lets the blur read as glass, not plastic)
            #   2. broad top specular sheen + a soft top-left highlight blob
            #   3. EDGE LENSING — a bright inner rim that hugs the whole
            #      perimeter (light refracting through the glass edge); this is
            #      the signature liquid-glass tell
            #   4. a soft inner shadow along the bottom for slab thickness
            #   5. a crisp 1px outer rim, brightest at the top
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            # CLEAR GLASS: big anti-aliased pill corners + see-through. No OS blur
            # behind, so the painted tint IS the glass — kept semi-transparent so
            # the desktop reads through it (sharp, not frosted). ascale<1 thins
            # the tint toward clear glass.
            # The glass is inset by SHADOW_PAD; the outer transparent band holds
            # the drop shadow so the capsule floats above the desktop.
            pad = SHADOW_PAD
            gw, gh = w - 2 * pad, h - 2 * pad        # visible glass size
            r = min(gh / 2, gw / 2, CLEAR_CORNER_RADIUS)
            ascale = 0.86

            # ── Soft drop shadow ── layered translucent strokes nudged downward,
            # so the capsule reads as a glass slab lifted off the desktop. Painted
            # in WINDOW coords (before we translate to the glass origin).
            p.save()
            for spread, alpha in ((pad * 1.7, 7), (pad * 1.1, 12),
                                  (pad * 0.6, 18), (pad * 0.3, 26)):
                sh = QPainterPath()
                sh.addRoundedRect(pad + 0.5, pad + 4.5, gw - 1, gh - 1, r, r)
                p.setPen(QPen(QColor(16, 24, 42, alpha), spread))
                p.setBrush(Qt.NoBrush)
                p.drawPath(sh)
            p.restore()

            # Shift the origin to the glass top-left and rebind w/h to the glass
            # size, so ALL the material code below (which uses w, h, path) paints
            # on the glass exactly as before — no other changes needed.
            p.translate(pad, pad)
            w, h = gw, gh
            inset = 0.5
            path = QPainterPath()
            path.addRoundedRect(inset, inset, w - 1, h - 1, r, r)

            # ── LIGHT MODE: a bright, airy frosted glass over light backdrops.
            # Matches the already-light input pill so the whole capsule reads as
            # one premium light-glass object (content flips to dark via
            # _apply_palette). This is where the light background shines.
            if self._light_mode:
                # 1) EVEN cool-white tint — clear glass, consistent top-to-bottom
                #    (no milky band). Low alpha so the desktop reads through sharp.
                tint = QLinearGradient(0, 0, 0, h)
                tint.setColorAt(0.0, QColor(255, 255, 255, 116))
                tint.setColorAt(0.5, QColor(248, 250, 253, 124))
                tint.setColorAt(1.0, QColor(238, 242, 249, 132))
                p.fillPath(path, tint)
                # 2) whisper of top gloss — just a hint, never an opaque sheet
                sheen = QLinearGradient(0, 0, 0, h)
                sheen.setColorAt(0.00, QColor(255, 255, 255, 60))
                sheen.setColorAt(0.18, QColor(255, 255, 255, 14))
                sheen.setColorAt(0.40, QColor(255, 255, 255, 0))
                p.fillPath(path, sheen)
                # 3) soft cool inner shadow at the very bottom for slab depth
                p.save(); p.setClipPath(path)
                lo = QLinearGradient(0, h * 0.72, 0, h)
                lo.setColorAt(0.0, QColor(70, 84, 110, 0))
                lo.setColorAt(1.0, QColor(70, 84, 110, 26))
                p.fillRect(QRectF(0, h * 0.72, w, h * 0.28), lo)
                p.restore()
                # 4) crisp inner top highlight line — the glass edge catching light
                p.save(); p.setClipPath(path)
                hl = QPainterPath()
                hl.addRoundedRect(1.4, 1.4, w - 2.8, h - 2.8, r, r)
                hl_g = QLinearGradient(0, 0, 0, h * 0.5)
                hl_g.setColorAt(0.0, QColor(255, 255, 255, 190))
                hl_g.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setPen(QPen(hl_g, 1.1))
                p.setBrush(Qt.NoBrush); p.drawPath(hl)
                p.restore()
                # 5) crisp cool outer rim — clean glass definition on the desktop
                edge = QLinearGradient(0, 0, 0, h)
                edge.setColorAt(0.0, QColor(150, 162, 182, 150))
                edge.setColorAt(0.5, QColor(120, 134, 158, 110))
                edge.setColorAt(1.0, QColor(96, 110, 136, 140))
                p.setPen(QPen(edge, 1.0)); p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                p.end()
                return

            # Adaptive blend: t=0 over a dark backdrop (clear glass + bright
            # edge lensing), t=1 over a light backdrop (denser dark tint so it
            # never washes to grey, faded highlights, soft DARK edge instead of
            # the bright rim that would otherwise show as seams on light).
            t = self._bg_light

            def L(a, b):
                return a + (b - a) * t

            # 1) Tint — same dark glass colour, but denser over a light backdrop.
            tint = QLinearGradient(0, 0, 0, h)
            tint.setColorAt(0.0, QColor(60, 68, 84, int(L(78, 156) * ascale)))
            tint.setColorAt(0.5, QColor(40, 46, 58, int(L(96, 178) * ascale)))
            tint.setColorAt(1.0, QColor(26, 30, 40, int(L(120, 205) * ascale)))
            p.fillPath(path, tint)

            # 2a) Broad top specular sheen — fades out over light backdrops.
            sheen = QLinearGradient(0, 0, 0, h)
            sheen.setColorAt(0.00, QColor(255, 255, 255, int(L(95, 36))))
            sheen.setColorAt(0.14, QColor(255, 255, 255, int(L(34, 12))))
            sheen.setColorAt(0.40, QColor(255, 255, 255, 0))
            p.fillPath(path, sheen)

            # 2b) Soft specular highlight blob, upper-left.
            p.save()
            p.setClipPath(path)
            blob = QRadialGradient(QPointF(w * 0.30, h * 0.05), w * 0.55)
            blob.setColorAt(0.0, QColor(255, 255, 255, int(L(70, 26))))
            blob.setColorAt(0.5, QColor(255, 255, 255, int(L(16, 6))))
            blob.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(QRectF(0, 0, w, h), blob)
            p.restore()

            # 3) EDGE LENSING — bright inner rim hugging the perimeter (the
            #    signature liquid-glass tell). Strong on dark, faded to a whisper
            #    on light so it never reads as a hard seam.
            p.save()
            p.setClipPath(path)
            for width_px, alpha in ((7.0, 26), (4.0, 50), (2.2, 95), (1.0, 165)):
                a = L(alpha, alpha * 0.18)
                rim = QLinearGradient(0, 0, 0, h)
                rim.setColorAt(0.0, QColor(255, 255, 255, int(min(255, a + 80))))
                rim.setColorAt(0.45, QColor(255, 255, 255, int(a)))
                # cool-blue tail on dark, neutral white on light (no grey seam)
                rim.setColorAt(1.0, QColor(int(L(205, 255)), int(L(222, 255)),
                                           255, int(min(255, a + 20))))
                p.setPen(QPen(rim, width_px))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            p.restore()

            # 4) Soft inner shadow along the bottom — slab thickness.
            p.save()
            p.setClipPath(path)
            shadow = QLinearGradient(0, h * 0.62, 0, h)
            shadow.setColorAt(0.0, QColor(0, 0, 0, 0))
            shadow.setColorAt(1.0, QColor(0, 0, 0, int(L(60, 80))))
            p.fillRect(QRectF(0, h * 0.62, w, h * 0.38), shadow)
            p.restore()

            # 5) Crisp 1px outer rim — bright white on dark (light catching the
            #    edge), soft dark on light (clean edge definition, no glow seam).
            edge = QLinearGradient(0, 0, 0, h)
            edge.setColorAt(0.0, QColor(int(L(255, 70)), int(L(255, 78)),
                                        int(L(255, 92)), int(L(225, 90))))
            edge.setColorAt(0.5, QColor(int(L(220, 60)), int(L(230, 66)),
                                        int(L(245, 80)), int(L(70, 55))))
            edge.setColorAt(1.0, QColor(int(L(255, 60)), int(L(255, 66)),
                                        int(L(255, 80)), int(L(55, 70))))
            p.setPen(QPen(edge, 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
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
            self._sample_bg()  # re-adapt glass to the new backdrop

        def showEvent(self, e) -> None:  # noqa: N802
            super().showEvent(e)
            hwnd = int(self.winId())
            _round_window(hwnd, self.width(), self.height(), RADIUS)
            # Adaptive-glass sampler: initial read + slow periodic re-read so the
            # capsule keeps matching whatever ends up behind it.
            self._sample_bg()
            if not hasattr(self, "_bg_timer"):
                self._bg_timer = QTimer(self)
                self._bg_timer.timeout.connect(self._sample_bg)
                self._bg_timer.start(1500)
            # First-run onboarding check (once) — shortly after the window shows.
            if not getattr(self, "_setup_check_done", False):
                self._setup_check_done = True
                QTimer.singleShot(600, self._check_setup)
            if not getattr(self, "_active_recover_check_done", False):
                self._active_recover_check_done = True
                QTimer.singleShot(950, self.runner.recover_active)
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

        def _fade_hide(self) -> None:
            """Smoothly fade the capsule out, then hide it (no graphics effect —
            animates the window opacity directly, which is artifact-safe)."""
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(160)
            anim.setStartValue(self.windowOpacity() or 1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InCubic)

            def _done():
                self.hide()
                self.setWindowOpacity(1.0)  # reset for next show

            anim.finished.connect(_done)
            self._fade_anim = anim
            anim.start()

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
                    self._fade_hide()
            else:
                super().keyPressEvent(e)

        def resizeEvent(self, e) -> None:  # noqa: N802
            super().resizeEvent(e)
            if getattr(self, "_animating", False):
                # Fast path during the grow/shrink animation — clip only.
                _clip_region(int(self.winId()), self.width(), self.height(), RADIUS)
            else:
                self._reshape()

    class HotkeySignaler(QObject):
        toggle = Signal()

    win = Capsule()

    signaler = HotkeySignaler()
    def on_toggle():
        if win.isVisible():
            win._fade_hide()
        else:
            win.show()
            win.activateWindow()
            win.raise_()
            win.input.setFocus()

    signaler.toggle.connect(on_toggle)

    def hotkey_callback():
        signaler.toggle.emit()

    # ── EXPLAIN-THIS-SCREEN hotkey signal ────────────────────────────
    class _ExplainSig(QObject):
        fire = Signal()
    explain_sig = _ExplainSig()
    def _explain_fire():
        # Auto-fill the input with the explain prompt and submit
        win.show(); win.activateWindow(); win.raise_()
        win.input.setText(
            "Take a screenshot of the foreground window and explain what's "
            "visible. Identify the app, what the user is probably trying to "
            "do, and 2-3 suggested next actions.")
        win.input.setFocus()
        # Fire submit asynchronously so the show() animation can settle
        QTimer.singleShot(150, win._submit)
    explain_sig.fire.connect(_explain_fire)

    try:
        import keyboard
        keyboard.add_hotkey('ctrl+shift+space', hotkey_callback)
        print("[Desktop] Global hotkey Ctrl+Shift+Space registered.",
              flush=True)
        # "Explain this screen" — Ctrl+Shift+E
        keyboard.add_hotkey('ctrl+shift+e',
                            lambda: explain_sig.fire.emit())
        print("[Desktop] Explain hotkey Ctrl+Shift+E registered.",
              flush=True)
    except ImportError:
        print("[Desktop] Install 'keyboard' (pip install keyboard) for global hotkeys.")
    except Exception as e:
        print(f"[Desktop] Could not register global hotkey: {e}")

    # Start clipboard-history background watcher + scheduler + region-watch
    try:
        _df.start_clipboard_watcher()
        print("[Desktop] Clipboard history watcher started.", flush=True)
    except Exception as e:
        print(f"[Desktop] Clipboard watcher failed: {e}", flush=True)

    try:
        def _schedule_submit(goal: str, mode: str):
            win.runner.submit(goal, {"recipe_mode": mode})
        _df.start_scheduler_daemon(_schedule_submit)
        print("[Desktop] Scheduled-recipe daemon started.", flush=True)
    except Exception as e:
        print(f"[Desktop] Scheduler failed: {e}", flush=True)

    try:
        def _notify(name: str, prompt: str):
            try:
                tray.showMessage(
                    f"Watch: {name}", prompt or "Region changed",
                    QSystemTrayIcon.Information, 5000)
            except Exception:
                pass
        _df.start_watch_daemon(_notify)
        print("[Desktop] Region-watch daemon started.", flush=True)
    except Exception as e:
        print(f"[Desktop] Watch daemon failed: {e}", flush=True)

    # ── System tray icon — table-stakes hygiene so the app feels native.
    # Shows in the Windows taskbar tray, right-click for Show/Hide/Quit.
    # Single-click also toggles the capsule.
    try:
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        from PySide6.QtGui import (QAction, QIcon as _QIcon, QPixmap as _QPx,
                                    QBrush as _QBrush, QPen as _QPen,
                                    QPainter as _QPainter, QColor as _QColor)
        _QBrush_ = _QBrush; _QPen_ = _QPen; _QPainter_ = _QPainter
        _QColor_ = _QColor
        # Build a simple white-on-teal monitor glyph as the tray icon
        tray_pm = _QPx(32, 32)
        tray_pm.fill(Qt.transparent)
        _tp = _QPainter_(tray_pm)
        _tp.setRenderHint(_QPainter_.Antialiasing)
        _tp.setBrush(_QBrush_(_QColor_(ACCENT)))
        _tp.setPen(Qt.NoPen)
        _tp.drawRoundedRect(2, 2, 28, 28, 8, 8)
        _tp.setBrush(Qt.NoBrush)
        _tp.setPen(_QPen_(_QColor_("#062925"), 2.2))
        _tp.drawRect(8, 9, 16, 12)
        _tp.drawLine(13, 22, 19, 22)
        _tp.drawLine(16, 21, 16, 23)
        _tp.end()
        tray = QSystemTrayIcon(_QIcon(tray_pm))
        tray.setToolTip("AI Computer — click to toggle")
        menu = QMenu()
        act_show = QAction("Show / Hide capsule", menu)
        act_show.triggered.connect(on_toggle)
        menu.addAction(act_show)
        act_dash = QAction("Open dashboard…", menu)
        def _open_dash():
            # Open the dashboard as a NATIVE desktop window (frameless pywebview),
            # NOT in a web browser — this is a desktop app, not a website. We
            # spawn a separate process because pywebview must own its main thread;
            # it reuses the backend already running here (no port clash).
            import subprocess, sys, os
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            try:
                kwargs = {"cwd": root}
                if os.name == "nt":
                    kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
                subprocess.Popen([sys.executable, "run_desktop.py", "--dashboard"], **kwargs)
            except Exception as _e:
                # Last-resort fallback so the user is never stranded.
                import webbrowser as _wb
                _wb.open(f"http://127.0.0.1:{port}")
        act_dash.triggered.connect(_open_dash)
        menu.addAction(act_dash)

        # Snap layout submenu — instant native window arrangement
        layout_menu = menu.addMenu("Snap layout")
        for _name, _spec in _df.LAYOUTS.items():
            _act = QAction(f"{_name.title()} — {_spec['description']}",
                            layout_menu)
            _act.triggered.connect(
                lambda _checked=False, n=_name: _df.apply_layout(n))
            layout_menu.addAction(_act)

        # Autostart toggle
        act_autostart = QAction("Start with Windows", menu)
        act_autostart.setCheckable(True)
        act_autostart.setChecked(_df.is_autostart_enabled())
        def _toggle_autostart(checked):
            _df.set_autostart(checked)
            act_autostart.setChecked(_df.is_autostart_enabled())
        act_autostart.toggled.connect(_toggle_autostart)
        menu.addAction(act_autostart)

        menu.addSeparator()
        act_quit = QAction("Quit AI Computer", menu)
        act_quit.triggered.connect(app.quit)
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        # Left-click = toggle, right-click = menu (default on Win)
        def _on_tray_activated(reason):
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                on_toggle()
        tray.activated.connect(_on_tray_activated)
        tray.show()
        print("[Desktop] System tray icon registered.", flush=True)
    except Exception as e:
        print(f"[Desktop] System tray unavailable: {e}", flush=True)

    geo = app.primaryScreen().availableGeometry()
    win.move(geo.center().x() - WIDTH // 2, geo.top() + 70)
    win.show()
    win.input.setFocus()
    # Don't quit when the capsule is hidden — tray icon keeps the app alive
    app.setQuitOnLastWindowClosed(False)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(int(os.getenv("AI_COMPUTER_PORT", "8000"))))
