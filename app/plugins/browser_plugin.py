from __future__ import annotations
import base64
import json
import os
from typing import Optional
from ..untrusted_content import wrap_untrusted_web_content

_pw = None
_browser = None
_page = None

# Headed by default when BROWSER_HEADED=1 in the env so the user can see
# the agent driving the browser. Falls back to headless otherwise.
_DEFAULT_HEADLESS = os.environ.get("BROWSER_HEADED", "").lower() not in ("1", "true", "yes")


async def _ensure_browser(headless: Optional[bool] = None):
    global _pw, _browser, _page
    if _browser is None:
        from playwright.async_api import async_playwright
        try:
            _pw = await async_playwright().start()
            use_headless = _DEFAULT_HEADLESS if headless is None else headless
            _browser = await _pw.chromium.launch(headless=use_headless)
            _page = await _browser.new_page(viewport={"width": 1280, "height": 800})
        except Exception:
            # Reset all globals so the next call retries from scratch
            _pw = _browser = _page = None
            raise


async def browser_open(url: str, headless: Optional[bool] = None) -> str:
    await _ensure_browser(headless)
    try:
        await _page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return f"Navigation error: {type(e).__name__}: {str(e)[:200]}"
    return f"Opened: {_page.url} | Title: {await _page.title()}"


async def browser_screenshot() -> str:
    await _ensure_browser()
    data = await _page.screenshot(type="png")
    return base64.b64encode(data).decode("utf-8")


async def browser_click(selector: str) -> str:
    await _ensure_browser()
    try:
        await _page.click(selector, timeout=10000)
    except Exception as e:
        return f"Click error on {selector!r}: {type(e).__name__}: {str(e)[:200]}"
    return f"Clicked {selector}"


async def browser_click_coords(x: int, y: int) -> str:
    await _ensure_browser()
    await _page.mouse.click(x, y)
    return f"Clicked coords ({x}, {y})"


async def browser_type(selector: str, text: str) -> str:
    await _ensure_browser()
    try:
        await _page.fill(selector, text, timeout=10000)
    except Exception as e:
        return f"Type error on {selector!r}: {type(e).__name__}: {str(e)[:200]}"
    return f"Typed {len(text)} chars into {selector}"


async def browser_scroll(direction: str = "down", amount: int = 500) -> str:
    await _ensure_browser()
    if direction == "down":
        await _page.evaluate(f"window.scrollBy(0, {amount})")
    else:
        await _page.evaluate(f"window.scrollBy(0, -{amount})")
    return f"Scrolled {direction} {amount}px"


async def browser_get_text() -> str:
    """Return visible page text (body.innerText). Capped so small models don't choke."""
    await _ensure_browser()
    try:
        text = await _page.evaluate("document.body ? document.body.innerText : ''")
    except Exception as e:
        return f"Error reading text: {e}"
    url = _page.url
    text = (text or "").strip()
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    return wrap_untrusted_web_content(f"URL: {url}\n\n{text}", source=url, kind="browser_get_text")


def _flatten_ax_tree(node: dict, depth: int = 0, lines: list | None = None, max_lines: int = 120) -> list:
    """Turn the Playwright accessibility snapshot into a compact outline for an LLM."""
    if lines is None:
        lines = []
    if not node or len(lines) >= max_lines:
        return lines
    role = node.get("role", "")
    name = (node.get("name") or "").strip()
    value = (node.get("value") or "").strip()
    label_parts = [role]
    if name:
        label_parts.append(f'"{name[:80]}"')
    if value:
        label_parts.append(f"[value={value[:40]!r}]")
    lines.append("  " * depth + " ".join(label_parts))
    for child in node.get("children", []) or []:
        if len(lines) >= max_lines:
            lines.append("  " * (depth + 1) + "...(truncated)")
            break
        _flatten_ax_tree(child, depth + 1, lines, max_lines)
    return lines


async def browser_accessibility_tree() -> str:
    """Return the page as a compact text outline — the primary 'vision' for free models.
    Compatible with both older Playwright (page.accessibility.snapshot) and newer
    (page.aria_snapshot / ARIA tree via evaluate).
    """
    await _ensure_browser()
    url = _page.url
    title = await _page.title()
    snap = None

    # Try the modern API first (Playwright >= 1.46)
    try:
        raw = await _page.aria_snapshot()
        if raw:
            lines = raw.splitlines()[:120]
            content = f"URL: {url}\nTitle: {title}\n\n" + "\n".join(lines)
            return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")
    except Exception:
        pass

    # Fallback: older page.accessibility.snapshot()
    try:
        snap = await _page.accessibility.snapshot()  # type: ignore[attr-defined]
    except Exception:
        snap = None

    if snap:
        lines = _flatten_ax_tree(snap)
        content = f"URL: {url}\nTitle: {title}\n\n" + "\n".join(lines)
        return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")

    # Last resort: body text
    try:
        text = await _page.evaluate("document.body ? document.body.innerText : ''")
        text = (text or "").strip()[:3000]
    except Exception as e:
        text = f"(Could not read page: {e})"
    content = f"URL: {url}\nTitle: {title}\n\n{text}"
    return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")


async def browser_navigate_back() -> str:
    await _ensure_browser()
    await _page.go_back()
    return "Navigated back"


async def browser_close() -> str:
    global _pw, _browser, _page
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
    _pw = _browser = _page = None
    return "Browser closed"


def handlers():
    return {
        "browser_open": browser_open,
        "browser_screenshot": browser_screenshot,
        "browser_click": browser_click,
        "browser_click_coords": browser_click_coords,
        "browser_type": browser_type,
        "browser_scroll": browser_scroll,
        "browser_get_text": browser_get_text,
        "browser_accessibility_tree": browser_accessibility_tree,
        "browser_navigate_back": browser_navigate_back,
        "browser_close": browser_close,
    }


def register():
    from ..models import PluginAction
    return PluginAction(
        name="browser",
        description="Browser control tools via Playwright",
        handlers=handlers(),
    )
