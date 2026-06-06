from __future__ import annotations
import base64
import json
import os
from typing import Optional
from ..untrusted_content import wrap_untrusted_web_content

_pw = None
_browser = None
_page = None
_sessions: dict[str, dict] = {}

# Headed by default when BROWSER_HEADED=1 in the env so the user can see
# the agent driving the browser. Falls back to headless otherwise.
_DEFAULT_HEADLESS = os.environ.get("BROWSER_HEADED", "").lower() not in ("1", "true", "yes")


async def _ensure_browser(headless: Optional[bool] = None, session_id: str = "default"):
    global _pw, _browser, _page
    session = _sessions.get(session_id)
    if session and session.get("browser") is not None:
        return session
    session = {"pw": None, "browser": None, "page": None}
    _sessions[session_id] = session
    if session["browser"] is None:
        from playwright.async_api import async_playwright
        try:
            session["pw"] = await async_playwright().start()
            use_headless = _DEFAULT_HEADLESS if headless is None else headless
            session["browser"] = await session["pw"].chromium.launch(headless=use_headless)
            session["page"] = await session["browser"].new_page(viewport={"width": 1280, "height": 800})
            if session_id == "default":
                _pw = session["pw"]
                _browser = session["browser"]
                _page = session["page"]
        except Exception:
            # Reset all globals so the next call retries from scratch
            _sessions.pop(session_id, None)
            if session_id == "default":
                _pw = _browser = _page = None
            raise
    return session


async def browser_open(url: str, headless: Optional[bool] = None, session_id: str = "default") -> str:
    # Block non-web schemes (file:, javascript:, data:, about:) — they have no
    # legitimate browsing use and are local-file-read / script-injection vectors.
    from urllib.parse import urlsplit
    scheme = (urlsplit(url).scheme or "").lower() if isinstance(url, str) else ""
    if scheme and scheme not in ("http", "https"):
        return f"Blocked URL scheme '{scheme}': only http(s) navigation is allowed."
    session = await _ensure_browser(headless, session_id=session_id)
    page = session["page"]
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return f"Navigation error: {type(e).__name__}: {str(e)[:200]}"
    return f"Opened: {page.url} | Title: {await page.title()}"


async def browser_screenshot(session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    data = await session["page"].screenshot(type="png")
    return base64.b64encode(data).decode("utf-8")


async def browser_click(selector: str, session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    try:
        await session["page"].click(selector, timeout=10000)
    except Exception as e:
        return f"Click error on {selector!r}: {type(e).__name__}: {str(e)[:200]}"
    return f"Clicked {selector}"


async def browser_click_coords(x: int, y: int, session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    await session["page"].mouse.click(x, y)
    return f"Clicked coords ({x}, {y})"


async def browser_type(selector: str, text: str, session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    try:
        await session["page"].fill(selector, text, timeout=10000)
    except Exception as e:
        return f"Type error on {selector!r}: {type(e).__name__}: {str(e)[:200]}"
    return f"Typed {len(text)} chars into {selector}"


async def browser_scroll(direction: str = "down", amount: int = 500, session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    page = session["page"]
    if direction == "down":
        await page.evaluate(f"window.scrollBy(0, {amount})")
    else:
        await page.evaluate(f"window.scrollBy(0, -{amount})")
    return f"Scrolled {direction} {amount}px"


async def browser_get_text(session_id: str = "default") -> str:
    """Return visible page text (body.innerText). Capped so small models don't choke."""
    session = await _ensure_browser(session_id=session_id)
    page = session["page"]
    try:
        text = await page.evaluate("document.body ? document.body.innerText : ''")
    except Exception as e:
        return f"Error reading text: {e}"
    url = page.url
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


async def browser_accessibility_tree(session_id: str = "default") -> str:
    """Return the page as a compact text outline — the primary 'vision' for free models.
    Compatible with both older Playwright (page.accessibility.snapshot) and newer
    (page.aria_snapshot / ARIA tree via evaluate).
    """
    session = await _ensure_browser(session_id=session_id)
    page = session["page"]
    url = page.url
    title = await page.title()
    snap = None

    # Try the modern API first (Playwright >= 1.46)
    try:
        raw = await page.aria_snapshot()
        if raw:
            lines = raw.splitlines()[:120]
            content = f"URL: {url}\nTitle: {title}\n\n" + "\n".join(lines)
            return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")
    except Exception:
        pass

    # Fallback: older page.accessibility.snapshot()
    try:
        snap = await page.accessibility.snapshot()  # type: ignore[attr-defined]
    except Exception:
        snap = None

    if snap:
        lines = _flatten_ax_tree(snap)
        content = f"URL: {url}\nTitle: {title}\n\n" + "\n".join(lines)
        return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")

    # Last resort: body text
    try:
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        text = (text or "").strip()[:3000]
    except Exception as e:
        text = f"(Could not read page: {e})"
    content = f"URL: {url}\nTitle: {title}\n\n{text}"
    return wrap_untrusted_web_content(content, source=url, kind="browser_accessibility_tree")


async def browser_navigate_back(session_id: str = "default") -> str:
    session = await _ensure_browser(session_id=session_id)
    await session["page"].go_back()
    return "Navigated back"


async def browser_close(session_id: str = "default") -> str:
    global _pw, _browser, _page
    session = _sessions.pop(session_id, None)
    if session is None:
        if session_id == "default":
            _pw = _browser = _page = None
        return "Browser closed"
    browser = session.get("browser")
    pw = session.get("pw")
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            pass
    if session_id == "default":
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
