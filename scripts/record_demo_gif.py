#!/usr/bin/env python3
"""Record docs/demo.gif from the Orynn dashboard Notepad demo stream."""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8080


def _health(base: str, timeout: float = 0.8) -> bool:
    try:
        return httpx.get(f"{base}/healthz", timeout=timeout).status_code == 200
    except Exception:
        return False


def _wait_health(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health(base):
            return
        time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy at {base}")


def _start_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", f"--port", str(port)],
        cwd=ROOT,
    )


def _thin_pngs(png_dir: Path, max_frames: int) -> int:
    paths = sorted(png_dir.glob("frame_*.png"))
    if len(paths) <= max_frames:
        return len(paths)
    step = max(1, len(paths) // max_frames)
    keep = paths[::step][:max_frames]
    for path in paths:
        if path not in keep:
            path.unlink()
    for idx, path in enumerate(keep):
        path.rename(png_dir / f"frame_{idx:03d}.png")
    return len(keep)


def _ffmpeg_path() -> Path | None:
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / "AppData/Local" / "ms-playwright"))
    matches = sorted(root.glob("ffmpeg-*/ffmpeg.exe"), reverse=True)
    if matches and matches[0].exists():
        return matches[0]
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def _save_gif_from_pngs(png_dir: Path, path: Path, *, fps: float) -> None:
    ffmpeg = _ffmpeg_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if ffmpeg:
        palette = png_dir / "palette.png"
        pattern = str(png_dir / "frame_%03d.png")
        subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-framerate",
                str(fps),
                "-i",
                pattern,
                "-vf",
                "palettegen=stats_mode=diff",
                str(palette),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-framerate",
                str(fps),
                "-i",
                pattern,
                "-i",
                str(palette),
                "-lavfi",
                "paletteuse=dither=bayer:bayer_scale=3",
                str(path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    frames = [Image.open(png_dir / f"frame_{idx:03d}.png").convert("RGB") for idx in range(len(list(png_dir.glob('frame_*.png'))))]
    if not frames:
        raise RuntimeError("No frames captured")
    duration_ms = int(1000 / fps)
    first, *rest = frames
    first.save(path, save_all=True, append_images=rest, duration=duration_ms, loop=0, optimize=False)


async def _capture_dashboard(
    *,
    base: str,
    out_width: int,
    frame_count: int,
    interval: float,
    png_dir: Path,
) -> None:
    from playwright.async_api import async_playwright

    png_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1360, "height": 860},
            color_scheme="dark",
        )

        # Keep the recording clean: never auto-resume a real persisted task.
        # Otherwise its goal hijacks the header and its dropped SSE sprays
        # "stream interrupted / reconnecting" toasts all over the demo.
        async def _no_resume(route):
            await route.fulfill(status=200, content_type="application/json", body='{"tasks": []}')

        async def _no_stream(route):
            await route.abort()

        await context.route("**/api/active-tasks*", _no_resume)
        await context.route("**/api/tasks/*/stream*", _no_stream)

        session = await context.request.post(f"{base}/api/session")
        if session.status != 200:
            raise RuntimeError(f"Session bootstrap failed: {session.status}")
        await context.request.post(
            f"{base}/api/preferences",
            data='{"preferences":{"onboarded":true,"theme":"dark"}}',
            headers={"Content-Type": "application/json"},
        )
        page = await context.new_page()
        await page.goto(f"{base}/", wait_until="networkidle")
        await page.wait_for_function("() => typeof window.__aiComputerPlayNotepadDemoStream === 'function'")
        await page.wait_for_selector(".composer", state="visible")
        await page.evaluate(
            """() => {
              document.documentElement.setAttribute('data-theme', 'dark');
              const overlay = document.getElementById('onboarding');
              if (overlay) overlay.hidden = true;
              document.querySelectorAll('.toast').forEach((node) => node.remove());
            }"""
        )
        # Fire-and-forget: the demo fn is async; returning undefined (not the
        # promise) lets capture run concurrently with the animation instead of
        # blocking until it finishes and screenshotting only the final frame.
        await page.evaluate("() => { window.__aiComputerPlayNotepadDemoStream(); }")

        for idx in range(frame_count):
            shot = png_dir / f"frame_{idx:03d}.png"
            await page.screenshot(path=str(shot), full_page=False)
            img = Image.open(shot).convert("RGB")
            height = max(1, int(img.height * out_width / img.width))
            img.resize((out_width, height), Image.Resampling.LANCZOS).save(shot)
            await asyncio.sleep(interval)
        await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--width", type=int, default=820)
    parser.add_argument("--frames", type=int, default=34)
    parser.add_argument("--interval", type=float, default=0.85)
    parser.add_argument("--max-frames", type=int, default=34)
    parser.add_argument("--duration-ms", type=int, default=700)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "demo.gif")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    base = f"http://127.0.0.1:{args.port}"
    proc: subprocess.Popen | None = None

    try:
        if not _health(base):
            print(f"[demo] Starting uvicorn on {base}")
            proc = _start_server(args.port)
            _wait_health(base)

        print("[demo] Capturing dashboard Notepad demo stream")
        with tempfile.TemporaryDirectory(prefix="orynn-demo-out-") as out_tmp:
            png_dir = Path(out_tmp)
            asyncio.run(
                _capture_dashboard(
                    base=base,
                    out_width=args.width,
                    frame_count=args.frames,
                    interval=args.interval,
                    png_dir=png_dir,
                )
            )
            frame_total = _thin_pngs(png_dir, args.max_frames)
            fps = max(1.0, 1000 / args.duration_ms)
            _save_gif_from_pngs(png_dir, args.output, fps=fps)
        size_kb = args.output.stat().st_size / 1024
        print(f"[demo] Wrote {args.output} ({frame_total} frames, {size_kb:.0f} KB)")
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
