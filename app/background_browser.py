"""
BackgroundBrowser — Sandboxed Playwright browser for cowork-style background agent.

Instead of pyautogui controlling the user's real desktop, all GUI interactions
happen inside a headless (invisible) Playwright browser.  The user keeps full
control of their machine while the agent works silently in the background.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("background_browser")


class BackgroundBrowser:
    """Manages a headless Playwright browser that the agent drives instead of the desktop."""

    def __init__(self, width: int = 1280, height: int = 800, headless: bool = True):
        self.width = width
        self.height = height
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        """Launch the browser.  Safe to call multiple times."""
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is required for background browser mode.  "
                "Install it with: pip install playwright && playwright install chromium"
            )

        self._playwright = await asyncio.wait_for(async_playwright().start(), timeout=30)
        self._browser = await asyncio.wait_for(self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--window-size={self.width},{self.height}",
                "--no-sandbox",
            ],
        ), timeout=30)
        self._context = await self._browser.new_context(
            viewport={"width": self.width, "height": self.height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        # Start with a blank page
        await self._page.goto("about:blank")
        self._started = True
        log.info("Background browser started (%dx%d, headless=%s)", self.width, self.height, self.headless)

    async def stop(self):
        """Shut down the browser."""
        if not self._started:
            return
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            log.warning("Error stopping background browser: %s", exc)
        finally:
            self._playwright = self._browser = self._context = self._page = None
            self._started = False
            log.info("Background browser stopped")

    @property
    def page(self):
        if not self._page:
            raise RuntimeError("Background browser not started. Call start() first.")
        return self._page

    @property
    def is_running(self) -> bool:
        return self._started

    # ── navigation ──────────────────────────────────────────────────────

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> str:
        """Navigate to a URL and return the page title."""
        await self.page.goto(url, wait_until=wait_until, timeout=30_000)
        return await self.page.title()

    # ── mouse ───────────────────────────────────────────────────────────

    async def mouse_move(self, x: int, y: int):
        await self.page.mouse.move(x, y)

    async def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1):
        await self.page.mouse.click(x, y, button=button, click_count=click_count)

    async def mouse_drag(self, from_x: int, from_y: int, to_x: int, to_y: int):
        await self.page.mouse.move(from_x, from_y)
        await self.page.mouse.down()
        await self.page.mouse.move(to_x, to_y)
        await self.page.mouse.up()

    async def scroll(self, delta_x: int = 0, delta_y: int = -120, x: Optional[int] = None, y: Optional[int] = None):
        """Scroll the page.  delta_y negative = scroll down in pyautogui convention."""
        if x is not None and y is not None:
            await self.page.mouse.move(x, y)
        await self.page.mouse.wheel(delta_x, -delta_y)  # Playwright: positive = down

    # ── keyboard ────────────────────────────────────────────────────────

    async def type_text(self, text: str, delay: float = 0.01):
        """Type text character by character."""
        await self.page.keyboard.type(text, delay=int(delay * 1000))

    async def press_key(self, key: str):
        """Press a single key or combo like 'Control+c'."""
        # Normalise pyautogui-style "ctrl+c" → Playwright-style "Control+c"
        _MAP = {"ctrl": "Control", "alt": "Alt", "shift": "Shift", "win": "Meta",
                "meta": "Meta", "cmd": "Meta", "enter": "Enter", "return": "Enter",
                "tab": "Tab", "esc": "Escape", "escape": "Escape", "backspace": "Backspace",
                "delete": "Delete", "space": "Space", "up": "ArrowUp", "down": "ArrowDown",
                "left": "ArrowLeft", "right": "ArrowRight", "home": "Home", "end": "End",
                "pageup": "PageUp", "pagedown": "PageDown", "f1": "F1", "f2": "F2",
                "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
                "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12"}
        parts = [p.strip() for p in key.split("+") if p.strip()]
        mapped = [_MAP.get(p.lower(), p) for p in parts]
        combo = "+".join(mapped)
        await self.page.keyboard.press(combo)

    async def hold_key(self, key: str, duration: float = 0.5):
        await self.page.keyboard.down(key)
        await asyncio.sleep(duration)
        await self.page.keyboard.up(key)

    # ── screenshot ──────────────────────────────────────────────────────

    async def screenshot_b64(self) -> str:
        """Capture the browser page as a base64-encoded JPEG (10x smaller than PNG)."""
        raw = await self.page.screenshot(type="jpeg", quality=65, full_page=False)
        return base64.b64encode(raw).decode("utf-8")

    async def screenshot_bytes(self) -> bytes:
        """Capture the browser page as raw JPEG bytes."""
        return await self.page.screenshot(type="jpeg", quality=65, full_page=False)

    # ── DOM interaction (for computer_use mode compatibility) ─────────

    async def click_selector(self, selector: str):
        """Click an element by CSS selector."""
        await self.page.click(selector, timeout=10_000)

    async def type_into_selector(self, selector: str, text: str):
        """Type into an element by CSS selector."""
        await self.page.fill(selector, text, timeout=10_000)

    async def get_page_text(self) -> str:
        """Get visible text content of the page."""
        return await self.page.inner_text("body")

    async def get_accessibility_tree(self) -> str:
        """Get a simplified accessibility tree of the page via JS."""
        js = """
        () => {
            const elements = document.querySelectorAll('a, button, input, textarea, select, h1, h2, h3, h4, h5, h6');
            const lines = [];
            for (const el of elements) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0 || rect.top < 0 || rect.top > window.innerHeight) continue;
                
                const tag = el.tagName.toLowerCase();
                let text = (el.innerText || el.value || el.title || el.alt || '').replace(/\\n/g, ' ').trim();
                if (!text) continue;
                text = text.length > 80 ? text.substring(0, 80) + '...' : text;
                lines.push(`[${tag}] "${text}"`);
            }
            return lines.slice(0, 150).join('\\n');
        }
        """
        try:
            tree = await self.page.evaluate(js)
            if not tree:
                return "(no visible interactive elements found)"
            return tree
        except Exception as e:
            return f"Error extracting tree: {e}"

    async def get_url(self) -> str:
        return self.page.url

    async def go_back(self):
        await self.page.go_back()

    async def close_page(self):
        """Close the current page only if it has crashed or been closed, then open a fresh blank one.

        If the existing page is still alive this is a no-op, which avoids the overhead
        of tearing down and recreating a healthy page on every call.
        """
        if self._page and not self._page.is_closed():
            return
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = await self._context.new_page()
        await self._page.goto("about:blank")

