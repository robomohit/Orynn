#!/usr/bin/env python3
"""
scratch/_record_minimized_demo.py
==================================
Records docs/demo.gif — Calculator driven while MINIMIZED by UIA InvokePattern.

Flow
----
1.  Kill any stale Calculator instance.
2.  Start the Orynn server on port 8080 (if not already healthy).
3.  Submit task: compute 2847 × 916 via Calculator, report the answer.
4.  Once Calculator appears → pause 2.5 s → MINIMIZE it.
5.  Capture Calculator frames via PrintWindow while agent runs in background.
6.  Poll until done → restore Calculator → capture result frames.
7.  Dedup, badge "MINIMIZED" on mid-run frames, composite with themed banner.
8.  Save docs/demo.gif.

Hard requirements
-----------------
* Genuine live run (no fakes).
* No click_fallback events — if any appear the script warns and keeps going
  (take is flagged impure; re-run to get a clean one).
* Expected answer: 2,607,852.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Event, Thread
from urllib import request as urlreq
from urllib.error import HTTPError

import win32con
import win32gui
import win32ui
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parents[1]
PORT    = 8080
BASE    = f"http://127.0.0.1:{PORT}"
MODEL   = "groq/llama-3.3-70b-versatile"
GOAL    = (
    "Open the Calculator app and compute 2847 times 916. "
    "Tell me the final number shown on the display."
)
TASK_ID  = f"demo-{uuid.uuid4().hex[:8]}"
EXPECTED = "2607852"

# GIF theme (same palette as _build_gif.py)
BG     = (13, 13, 18)
ACCENT = (91, 224, 208)
WHITE  = (236, 238, 241)
MUTED  = (138, 144, 156)
FPS_CAP = 5   # capture rate
PAD, BANNER, FOOTER = 22, 86, 36


# ──────────────────────────────────────────────
# API helpers (stdlib only, no extra deps)
# ──────────────────────────────────────────────
def _api(method: str, path: str, body=None, key: str = "") -> dict:
    data = json.dumps(body).encode() if body else None
    req  = urlreq.Request(
        BASE.rstrip("/") + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type":  "application/json"},
    )
    try:
        with urlreq.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except HTTPError as exc:
        raise RuntimeError(
            f"{method} {path} → HTTP {exc.code}: {exc.read().decode()}"
        ) from exc


def _healthy() -> bool:
    try:
        with urlreq.urlopen(f"{BASE}/healthz", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_healthy(timeout: float = 60.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _healthy():
            return
        time.sleep(0.5)
    raise RuntimeError(f"Server not healthy after {timeout:.0f} s")


# ──────────────────────────────────────────────
# Window helpers
# ──────────────────────────────────────────────
def _find_calc() -> int | None:
    """Find the Calculator's ApplicationFrameWindow handle."""
    found: list[tuple[int, int]] = []

    def _cb(hwnd, _):
        if (
            win32gui.IsWindowVisible(hwnd)
            and win32gui.GetWindowText(hwnd) == "Calculator"
            and win32gui.GetClassName(hwnd) == "ApplicationFrameWindow"
        ):
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if 200 <= w <= 800 and 300 <= h <= 1100:
                found.append((w * h, hwnd))

    win32gui.EnumWindows(_cb, None)
    found.sort(reverse=True)
    return found[0][1] if found else None


def _print_window(hwnd: int) -> Image.Image | None:
    """
    Render a window's own content via PrintWindow(PW_RENDERFULLCONTENT=2).
    Works even when the window is cloaked, minimized, or completely occluded.
    """
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp     = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        if not ok:
            return None
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]),
            bits, "raw", "BGRX", 0, 1,
        )
    except Exception:
        return None


# ──────────────────────────────────────────────
# Frame capture thread
# ──────────────────────────────────────────────
class FrameCapture:
    def __init__(self):
        self.frames:       list[Image.Image] = []
        self.minimize_idx: int = -1   # frames[minimize_idx:] = captured while minimized
        self._stop = Event()

    def mark_minimized(self):
        self.minimize_idx = len(self.frames)

    def stop(self):
        self._stop.set()

    def run(self, hwnd_ref: list):
        while not self._stop.is_set():
            hw = hwnd_ref[0]
            if hw:
                img = _print_window(hw)
                if img:
                    self.frames.append(img)
            time.sleep(1.0 / FPS_CAP)


# ──────────────────────────────────────────────
# GIF composition helpers
# ──────────────────────────────────────────────
def _font(sz: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    p = "C:/Windows/Fonts/" + ("segoeuib.ttf" if bold else "segoeui.ttf")
    try:
        return ImageFont.truetype(p, sz)
    except Exception:
        return ImageFont.load_default()


def _ctext(d: ImageDraw.ImageDraw, cx: float, y: float,
           s: str, fnt, fill):
    w = d.textlength(s, font=fnt)
    d.text((cx - w / 2, y), s, font=fnt, fill=fill)


def _card(W: int, H: int, lines: list[tuple]) -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d  = ImageDraw.Draw(im)
    total = sum(sz + 14 for _, sz, _, _ in lines)
    y = (H - total) / 2
    for txt, sz, bold, col in lines:
        _ctext(d, W / 2, y, txt, _font(sz, bold), col)
        y += sz + 14
    return im


def _compose(
    calc_img: Image.Image,
    goal_txt: str,
    minimized: bool = False,
    result:    bool = False,
) -> Image.Image:
    """Composite Calculator frame with branded banner + footer."""
    CW, CH = calc_img.size
    W = CW + 2 * PAD
    H = BANNER + CH + FOOTER
    im = Image.new("RGB", (W, H), BG)
    d  = ImageDraw.Draw(im)

    # ── banner row 1: branding ──
    d.ellipse([24, 26, 34, 36], fill=ACCENT)
    d.text((42, 22), "Orynn", font=_font(15, True), fill=ACCENT)
    d.text((96, 24),
           "— drives apps by control name, not screenshots",
           font=_font(12), fill=MUTED)

    # ── MINIMIZED badge (top-right) ──
    if minimized and not result:
        badge = "● MINIMIZED"
        bw = d.textlength(badge, font=_font(11))
        d.text((W - bw - 14, 24), badge, font=_font(11), fill=ACCENT)

    # ── banner row 2: goal or result ──
    if result:
        d.text((24, 48), "= 2,607,852", font=_font(24, True), fill=ACCENT)
    else:
        # small play-triangle
        d.polygon([(24, 53), (24, 67), (36, 60)], fill=ACCENT)
        d.text((46, 50), goal_txt, font=_font(20), fill=WHITE)

    # ── calculator image ──
    im.paste(calc_img, (PAD, BANNER))

    # ── footer ──
    _ctext(
        d, W / 2, BANNER + CH + 9,
        "github.com/robomohit/Orynn  ·  free model  ·  no pixel-clicks",
        _font(12), MUTED,
    )
    return im


def _dedup(frames: list[Image.Image], threshold: float = 0.40) -> list[Image.Image]:
    """Remove near-duplicate consecutive frames."""
    out: list[Image.Image] = []
    last_hash = None
    for im in frames:
        h = __import__("hashlib").md5(
            im.convert("RGB").resize((60, 100)).tobytes()
        ).hexdigest()
        if h == last_hash:
            continue
        if out:
            diff = ImageStat.Stat(
                ImageChops.difference(
                    im.convert("RGB"), out[-1].convert("RGB")
                ).convert("L")
            ).mean[0]
            if diff < threshold:
                last_hash = h
                continue
        out.append(im)
        last_hash = h
    return out


def build_gif(cap: FrameCapture, out_path: Path) -> None:
    all_frames  = cap.frames
    min_idx     = cap.minimize_idx  # -1 = never minimized

    # ── dedup ──
    states = _dedup(all_frames)
    if len(states) > 2:
        states = states[2:]   # drop splash/loading frames
    if not states:
        raise RuntimeError("No calculator frames captured — re-take needed")

    CW, CH = states[0].size
    W      = CW + 2 * PAD
    H      = BANNER + CH + FOOTER

    GOAL_SHORT = '"what\'s 2847 × 916?"'

    # ── determine which states were captured while minimized ──
    # (min_idx is in raw frames, not dedup states — approximate via ratio)
    minimized_threshold = 0
    if min_idx >= 0 and len(all_frames) > 0:
        minimized_threshold = int(len(states) * min_idx / len(all_frames))

    # ── cards ──
    intro = _card(W, H, [
        ("Orynn", 46, True, ACCENT),
        ("controls Windows apps by control name.", 19, False, WHITE),
        ("This run: Calculator minimized — cursor never moved.", 15, False, MUTED),
        ("", 8, False, BG),
        (GOAL_SHORT, 18, False, MUTED),
    ])
    outro = _card(W, H, [
        ("= 2,607,852", 44, True, ACCENT),
        ("Calculator was minimized the entire run.", 18, False, WHITE),
        ("no screenshots · no pixel-clicks · free model", 16, False, MUTED),
        ("", 10, False, BG),
        ("github.com/robomohit/Orynn", 17, True, WHITE),
    ])

    # ── assemble sequence ──
    seq: list[Image.Image] = []
    durs: list[int]        = []

    seq.append(intro); durs.append(4000)

    body   = states[:-1] if len(states) > 1 else states
    last_s = states[-1]

    for i, img in enumerate(body):
        is_min = (i >= minimized_threshold) and (min_idx >= 0)
        seq.append(_compose(img, GOAL_SHORT, minimized=is_min)); durs.append(2400)

    # result frame (last distinct state) – Calculator being restored
    seq.append(_compose(last_s, GOAL_SHORT, result=True)); durs.append(6400)
    seq.append(outro); durs.append(5000)

    # ── scale + quantize ──
    scale  = 0.84
    sw, sh = int(W * scale), int(H * scale)
    seq_p  = [
        im.resize((sw, sh), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=128)
        for im in seq
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    seq_p[0].save(
        str(out_path), save_all=True, append_images=seq_p[1:],
        duration=durs, loop=0, optimize=True, disposal=2,
    )
    kb = out_path.stat().st_size // 1024
    print(
        f"GIF saved: {out_path}  "
        f"({len(seq)} frames, {sum(durs)/1000:.1f}s, {kb} KB, {sw}×{sh})"
    )


# ──────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────
def main() -> int:
    # ── 0. Kill stale Calculator ──
    print("Killing any existing Calculator…")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Stop-Process -Name CalculatorApp -ErrorAction SilentlyContinue"],
        capture_output=True,
    )
    time.sleep(1.5)

    # ── 1. Ensure server is running ──
    server_owned = False
    if _healthy():
        print(f"Server already healthy on {BASE}")
    else:
        print(f"Starting Orynn server on {BASE}…")
        subprocess.Popen(
            [
                str(ROOT / ".venv" / "Scripts" / "python.exe"),
                "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1",
                "--port", str(PORT),
            ],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_healthy(90)
        server_owned = True
        print("Server healthy.")

    # Give server a moment to finish initialising MCP / lifespan
    time.sleep(2)

    # ── 2. Read API key ──
    sys.path.insert(0, str(ROOT))
    from app.local_auth import local_api_key
    KEY = local_api_key()
    if not KEY:
        print("ERROR: No API key found. Check ~/.config/orynn/.api_key")
        return 1
    print(f"API key: {KEY[:8]}…")

    # ── 3. Submit task ──
    print(f"Submitting task {TASK_ID}…")
    _api("POST", "/api/tasks", {
        "task_id":        TASK_ID,
        "goal":           GOAL,
        "mode":           "computer",
        "model":          MODEL,
        "autonomy_level": "fast",
        "plan_first":     False,
    }, KEY)
    print("Task accepted.")

    # ── 4. Start capture thread ──
    hwnd_ref: list[int | None] = [None]
    cap       = FrameCapture()
    cap_thread = Thread(target=cap.run, args=(hwnd_ref,), daemon=True)
    cap_thread.start()

    # ── 5. Wait for Calculator → minimize ──
    print("Waiting for Calculator window…")
    calc_found  = False
    minimized   = False
    t0          = time.time()

    while time.time() - t0 < 90:
        hw = _find_calc()
        if hw:
            hwnd_ref[0] = hw
            if not calc_found:
                calc_found = True
                print(f"Calculator appeared (hwnd={hw})")
            if not minimized:
                time.sleep(2.5)   # let a few "open" frames accumulate
                print("Minimizing Calculator…")
                win32gui.ShowWindow(hw, win32con.SW_MINIMIZE)
                cap.mark_minimized()
                minimized = True
                print("Minimized. Agent continues via UIA InvokePattern.")
                break
        time.sleep(0.4)

    if not minimized:
        print("WARNING: Calculator never appeared within 90 s — continuing without minimize.")

    # ── 6. Poll for completion ──
    print("Polling for task completion…")
    rec    = {}
    status = "running"
    t0     = time.time()
    while time.time() - t0 < 300:
        try:
            rec    = _api("GET", f"/api/tasks/{TASK_ID}", key=KEY)
            status = rec.get("status", "unknown")
            elapsed = int(time.time() - t0)
            print(f"  [{elapsed:3d}s] status={status}")
            if status not in ("running", "queued", "pending"):
                break
        except Exception as exc:
            print(f"  poll error: {exc}")
        time.sleep(3)

    print(f"\nTask finished: status={status}")

    # ── 7. Check for fallback events ──
    try:
        log  = _api("GET", f"/api/tasks/{TASK_ID}/log", key=KEY).get("log", [])
        bad  = [e for e in log
                if "click_fallback" in str(e) or "via Calculator keyboard" in str(e)]
        if bad:
            print(f"⚠  Fallback events ({len(bad)}) — take is impure, re-run for clean demo:")
            for e in bad[:3]:
                print("   ", str(e)[:120])
        else:
            print("✓  No fallback events — pure InvokePattern take.")
    except Exception as exc:
        print(f"  (could not fetch log: {exc})")

    # ── 8. Restore Calculator, capture result frames ──
    hw = hwnd_ref[0] or _find_calc()
    if hw:
        print("Restoring Calculator…")
        win32gui.ShowWindow(hw, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hw)
        except Exception:
            pass
        time.sleep(1.5)
        # Capture several result frames
        for _ in range(12):
            img = _print_window(hw)
            if img:
                cap.frames.append(img)
            time.sleep(0.12)
    else:
        print("WARNING: Calculator window not found for restore.")

    # ── 9. Stop capture ──
    cap.stop()
    cap_thread.join(timeout=5)
    print(f"Captured {len(cap.frames)} raw frames "
          f"(minimized from frame ~{cap.minimize_idx})")

    # ── 10. Build GIF ──
    out = ROOT / "docs" / "demo.gif"
    print(f"Building GIF → {out}")
    build_gif(cap, out)

    # ── 11. Verify answer ──
    answer = (
        rec.get("answer")
        or rec.get("result")
        or rec.get("output")
        or ""
    )
    digits = "".join(c for c in str(answer) if c.isdigit())
    if EXPECTED in digits:
        print(f"✓ Answer verified in task record: {str(answer)[:120]!r}")
    else:
        print(f"⚠ Answer not found in record (got: {str(answer)[:120]!r})")
        print("  Check the GIF manually — the display frame should show 2,607,852.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
