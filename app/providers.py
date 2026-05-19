from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import fnmatch
import io
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

import httpx
from PIL import Image

from .models import HierarchicalPlan
from .tool_registry import get_tool_guidance, get_mode_packs

SYSTEM_PROMPT = """You are a computer control planner. Use the provided actions to achieve the user's goal."""

HIERARCHICAL_SYSTEM_PROMPT = """You are a hierarchical planning engine. Decompose the goal into sequential or parallel sub-tasks.
Return ONLY valid JSON:
{{
  "reasoning": "Plan explanation",
  "execution_mode": "serial" | "parallel",
  "max_parallel_workers": 3,
  "sub_tasks": [
    {{
      "id": "step-1",
      "description": "Clear instruction",
      "depends_on": [],
      "actions": [{{ "id": "act-1", "type": "...", "args": {{}}, "explanation": "..." }}]
    }}
  ]
}}

Available actions:
{tool_guidance}

Rules:
1. For simple tasks, use 1 sub-task. For complex tasks, use 2-5.
2. Be concise. No markdown."""


# ──────────────────────────────────────────────────────────────────────────────
#  CODING MODE PROMPTS  — no screenshots, no mouse/keyboard/vision actions
# ──────────────────────────────────────────────────────────────────────────────
CODING_SYSTEM_PROMPT = """You are an expert autonomous coding agent. Decompose the goal into sub-tasks.
Return ONLY valid JSON:
{{
  "reasoning": "Strategy explanation",
  "execution_mode": "serial" | "parallel",
  "max_parallel_workers": 3,
  "sub_tasks": [
    {{
      "id": "step-1",
      "description": "Clear instruction",
      "depends_on": [],
      "actions": [{{ "id": "act-1", "type": "...", "args": {{}}, "explanation": "..." }}]
    }}
  ]
}}

Available actions:
{tool_guidance}

Rules:
1. Use relative paths.
2. Mandatory Verification: After any edit (create, str_replace, insert), ALWAYS include a 'view' action and a 'lint' action in the same sub-task to verify correctness.
3. If a linter error occurs, you MUST fix it immediately.
4. No markdown. No prose."""

CODING_REFLECT_PROMPT = """You are a reflection agent for an autonomous coding agent.
Given a completed sub-task description, the actions that ran, and their outputs (stdout/stderr/file contents),
determine if the sub-task succeeded.
Return ONLY valid JSON: {{"success": bool, "reason": str, "retry_actions": []}}
If success is false, optionally populate retry_actions with corrective action objects using these available types:
{tool_guidance}
Never output markdown. Never output prose outside JSON."""

CODING_EVALUATE_PROMPT = """You are an evaluation agent for an autonomous coding agent.
Given a goal, the action history (file writes, command outputs, etc.), determine if the overall goal is complete.
Return ONLY valid JSON: {{"complete": bool, "reason": str}}
Never output markdown. Never output prose outside JSON."""


# ──────────────────────────────────────────────────────────────────────────────
#  COMPUTER USE MODE  — DOM/accessibility-tree based, NO screenshots.
#  Tuned for small free models: short prompt, narrow action vocabulary.
# ──────────────────────────────────────────────────────────────────────────────
COMPUTER_USE_SYSTEM_PROMPT = """You are a browser-automation agent. You read pages as text via the accessibility tree — NEVER assume pixel coordinates.
Return ONLY valid JSON with shape:
{{
  "reasoning": str,
  "overall_complete": bool,
  "sub_tasks": [
    {{
      "id": str,
      "description": str,
      "actions": [{{ "id": str, "type": str, "args": object, "explanation": str, "requires_approval": false }}]
    }}
  ]
}}
For simple one-action tasks, use exactly 1 sub-task. For complex tasks, decompose into 2-8 sequential sub-tasks.

Available actions:
{tool_guidance}

Rules:
1. Start with browser_open for the target URL or web_search if you need to find a URL. Browser actions automatically request permission when needed.
2. After browser_open or any click that navigates, wait 1-2 seconds then call browser_accessibility_tree to see the new page state.
3. Use CSS selectors based on the accessibility tree output. Prefer stable selectors: input[type=...], button[aria-label=...], #id, [role=...].
4. NEVER use pixel coordinates. NEVER use mouse_click, keyboard_type, or screenshot. Those are blocked in this mode.
5. For Google Sheets: open https://docs.google.com/spreadsheets/ and use browser_accessibility_tree to see cells. Click a cell then browser_type to write.
6. NEVER invent action types. Only use action types listed above. Do NOT use 'launch_app' or any other unlisted type. To launch an application, use 'run_command' instead.
7. Your response MUST be valid JSON only — no markdown fences, no prose, no trailing text outside the JSON object."""


COMPUTER_USE_REFLECT_PROMPT = """You are a reflection agent for a browser-automation task.
Given a sub-task description, the actions that ran, and their outputs (URLs, page text, accessibility trees),
determine if the sub-task succeeded.
Return ONLY valid JSON: {{"success": bool, "reason": str, "retry_actions": []}}
If success is false, optionally populate retry_actions with corrective actions using these available types:
{tool_guidance}
Never output markdown. Never output prose outside JSON."""


COMPUTER_USE_EVALUATE_PROMPT = """You are an evaluation agent for a browser-automation task.
Given a goal and the action history (URLs visited, page text observed, form submissions), determine if the goal is complete.
Return ONLY valid JSON: {{"complete": bool, "reason": str}}
Never output markdown. Never output prose outside JSON."""

REFLECT_SYSTEM_PROMPT = """You are a reflection agent for an autonomous computer agent.
Given a completed sub-task description, the actions that ran, their results, and a screenshot of the
current screen, determine if the sub-task succeeded.
Return ONLY valid JSON: {{"success": bool, "reason": str, "retry_actions": []}}
If success is false, optionally populate retry_actions with corrective action objects.
Never output markdown. Never output prose outside JSON."""

EVALUATE_SYSTEM_PROMPT = """You are an evaluation agent for an autonomous computer agent.
Given a goal, the action history, and the current screenshot, determine if the overall goal is complete.
Return ONLY valid JSON: {{"complete": bool, "reason": str}}
Never output markdown. Never output prose outside JSON."""


# ──────────────────────────────────────────────────────────────────────────────
#  Task mode detection
# ──────────────────────────────────────────────────────────────────────────────
_CODING_KEYWORDS = [
    "write", "code", "script", "function", "class", "file", "create", "build",
    "implement", "refactor", "debug", "fix", "test", "install", "pip", "npm",
    "python", "javascript", "typescript", "html", "css", "react", "node",
    "api", "server", "database", "sql", "json", "yaml", "config", "setup",
    "project", "app", "module", "package", "library", "framework", "deploy",
    "dockerfile", "git", "commit", "repository", "repo", "compile", "lint",
    "format", "parse", "generate", "scaffold", "boilerplate", "template",
    "algorithm", "data structure", "endpoint", "route", "middleware",
    "component", "hook", "state", "reducer", "model", "schema", "migration",
    "makefile", "cmake", "cargo", "gradle", "maven", "webpack", "vite",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cpp",
    ".c", ".h", ".rb", ".php", ".swift", ".kt", ".sh", ".bash",
]

_COMPUTER_KEYWORDS = [
    "open", "click", "type into", "browser", "screenshot", "mouse", "scroll",
    "desktop", "window", "drag", "notepad", "chrome", "firefox", "visual",
    "screen", "navigate", "tab", "menu", "button", "gui", "interface",
    "application", "launch", "icon", "taskbar", "cursor",
]


_COMPUTER_USE_KEYWORDS = [
    "browser", "chrome", "firefox", "web", "website", "google search",
    "google sheets", "spreadsheet", "sheets", "docs.google", "gmail",
    "youtube", "wikipedia", "navigate to", "open url", "open site",
    "fill out form", "search for", "submit form", "log into", "log in to",
    "sign in to", "visit", "webpage",
]

_KNOWN_ISOLATED_APPS = {
    "notepad": "Notepad",
    "calculator": "Calculator",
    "calc": "Calculator",
    "paint": "Paint",
    "mspaint": "Paint",
    "wordpad": "WordPad",
    "word": "Word",
    "excel": "Excel",
    "powerpoint": "PowerPoint",
    "visual studio code": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "powershell": "PowerShell",
    "command prompt": "Command Prompt",
    "cmd": "Command Prompt",
    "terminal": "Terminal",
    "file explorer": "File Explorer",
    "explorer": "File Explorer",
    "photos": "Photos",
    "snipping tool": "Snipping Tool",
}


_CHAT_GREETINGS = {
    "hi", "hey", "hello", "sup", "yo", "hiya", "howdy", "greetings",
    "hi there", "hey there", "good morning", "good afternoon", "good evening",
}

_CHAT_PATTERNS = [
    "how are you", "what are you", "who are you", "what can you do",
    "what do you do", "tell me about yourself", "what's up", "whats up",
    "are you", "can you", "do you", "will you", "could you explain",
    "what is", "what's", "whats", "explain ", "describe ", "why is",
    "why does", "how does", "what does", "give me an example",
    "i'm bored", "im bored", "talk to me", "just chatting", "just asking",
    "thanks", "thank you", "thx", "ty", "cool", "nice", "great", "ok",
    "okay", "sounds good", "got it", "i see", "interesting",
]


_VISION_MODEL_KEYWORDS = ("vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma")


def is_vision_model(model: str) -> bool:
    """Heuristic: does this model accept image input? Mirrors the inline checks
    in the chat methods so callers (e.g. explain mode) can pre-flight."""
    return any(kw in (model or "").lower() for kw in _VISION_MODEL_KEYWORDS)


def detect_task_mode(goal: str, explicit_mode: Optional[str] = None) -> str:
    """Return 'chat', 'coding', 'computer_use', 'computer', or 'computer_isolated'. If explicit_mode is set, honour it."""
    if explicit_mode and explicit_mode in ("coding", "auto", "chat", "computer", "computer_use", "computer_isolated", "explain"):
        return explicit_mode  # Always respect the user's explicit mode selection
    # 'explain' is read-only screen Q&A — only ever an explicit choice, never auto-detected.

    g = goal.strip().lower()

    # --- Chat detection ---
    # Pure greeting (no other content)
    if g in _CHAT_GREETINGS or g.rstrip("!?.") in _CHAT_GREETINGS:
        return "chat"
    # Very short message with no action keywords
    if len(g.split()) <= 6 and not any(kw in g for kw in _CODING_KEYWORDS + _COMPUTER_KEYWORDS):
        return "chat"
    # Starts with a chat pattern
    if any(g.startswith(p) or g == p.strip() for p in _CHAT_PATTERNS):
        return "chat"

    # --- Existing mode detection ---
    # Explicit "browser cowork" phrasing is unambiguous — route straight to
    # the headless-browser mode, never to desktop/isolated window control.
    if "cowork" in g:
        return "computer_use"

    computer_use_score = sum(1 for kw in _COMPUTER_USE_KEYWORDS if kw in g)
    coding_score = sum(1 for kw in _CODING_KEYWORDS if kw in g)
    computer_score = sum(1 for kw in _COMPUTER_KEYWORDS if kw in g)
    # A web URL / domain is a strong browser signal.
    if re.search(r"https?://\S+|\bwww\.\S+|\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|dev|co|gov|edu|app)\b", g):
        computer_use_score += 2
    if computer_use_score >= 2 and computer_use_score >= coding_score:
        return "computer_use"
    if computer_score >= 2 and computer_score > coding_score:
        return "computer_isolated" if infer_isolated_app_name(goal) else "computer"
    return "coding"


def infer_isolated_app_name(goal: str) -> Optional[str]:
    """Infer a likely single-app window title from a desktop-control goal."""
    raw_goal = (goal or "").strip()
    if not raw_goal:
        return None

    lowered = raw_goal.lower()
    for alias, title in _KNOWN_ISOLATED_APPS.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered):
            return title

    match = re.search(
        r"\b(?:open|launch|use|inside|within|in)\s+([A-Za-z][A-Za-z0-9.&()'/-]*(?:\s+[A-Za-z][A-Za-z0-9.&()'/-]*){0,3})",
        raw_goal,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    candidate = match.group(1).strip(" .,!?:;\"'")
    candidate_lower = candidate.lower()
    if candidate_lower in {"desktop", "screen", "window", "app", "application", "browser"}:
        return None
    # A domain / URL is a website, not a desktop app to isolate.
    if re.search(r"\.[a-z]{2,}\b", candidate_lower) or candidate_lower.startswith(("http", "www.")):
        return None
    # Multi-word phrases starting with "browser" (e.g. "browser cowork mode").
    if candidate_lower.startswith("browser "):
        return None
    if candidate_lower.startswith(("a ", "an ", "the ")):
        return None
    if len(candidate.split()) > 4:
        return None
    return " ".join(part.capitalize() if part.islower() else part for part in candidate.split())


def get_scale_factor(width: int, height: int) -> float:
    long_edge_scale = 1568 / max(width, height)
    total_pixels_scale = math.sqrt(1_150_000 / (width * height))
    return min(1.0, long_edge_scale, total_pixels_scale)


def _capture_screenshot_b64(width: int, height: int) -> str:
    import mss

    target_w, target_h = _pick_capture_cap(width, height)
    with mss.mss() as sct:
        monitor = {
            "left": 0,
            "top": 0,
            "width": max(1, int(width)),
            "height": max(1, int(height)),
        }
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        try:
            if image.size[0] > target_w or image.size[1] > target_h:
                image.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=75)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
        finally:
            image.close()  # Explicitly release the PIL buffer immediately


def _get_active_window_rect(sw: int, sh: int) -> Optional[Dict[str, Any]]:
    """Return the foreground window's rect as fractions of the screenshot dimensions."""
    try:
        import win32gui  # type: ignore
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        title = win32gui.GetWindowText(hwnd)[:60]
        # Normalise against the actual captured dimensions (capped at 1280×800)
        cap_w = max(1, sw)
        cap_h = max(1, sh)
        x = max(0, left)
        y = max(0, top)
        w = min(right - left, cap_w - x)
        h = min(bottom - top, cap_h - y)
        if w <= 10 or h <= 10:
            return None
        return {
            "x": x / cap_w,
            "y": y / cap_h,
            "w": w / cap_w,
            "h": h / cap_h,
            "title": title,
        }
    except Exception:
        return None


def _get_hwnd_for_title(partial_title: str) -> Optional[int]:
    """Find a visible HWND by partial title match; checks top-level then child windows."""
    try:
        import win32gui  # type: ignore
        found: List[int] = []

        def _enum(hwnd: int, _: Any) -> None:
            if win32gui.IsWindowVisible(hwnd):
                text = win32gui.GetWindowText(hwnd)
                if partial_title.lower() in text.lower():
                    found.append(hwnd)

        win32gui.EnumWindows(_enum, None)
        if not found:
            return None
        top_hwnd = found[0]

        # Try to lock onto a more specific child window (e.g., MDI children or document panes)
        children: List[int] = []

        def _enum_children(hwnd: int, _: Any) -> None:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                children.append(hwnd)

        try:
            win32gui.EnumChildWindows(top_hwnd, _enum_children, None)
        except Exception:
            pass

        return children[0] if children else top_hwnd
    except Exception:
        return None


def _capture_hwnd_screenshot_b64(hwnd: int) -> str:
    """Capture a screenshot of the given HWND via PrintWindow so fullscreen overlays don't block it."""
    image = _capture_hwnd_image(hwnd)
    try:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=75)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    finally:
        image.close()  # Explicitly release the PIL buffer immediately


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL)


def _split_image_data(image_data: Optional[str], default_mime: str = "image/jpeg") -> tuple[Optional[str], Optional[str]]:
    if not image_data:
        return None, None
    payload = image_data.strip()
    match = _DATA_URL_RE.match(payload)
    if match:
        return match.group("mime"), match.group("data")
    return default_mime, payload


def _image_data_url(image_data: Optional[str], default_mime: str = "image/jpeg") -> Optional[str]:
    mime, payload = _split_image_data(image_data, default_mime=default_mime)
    if not payload:
        return None
    if image_data and image_data.strip().lower().startswith("data:"):
        return image_data.strip()
    return f"data:{mime or default_mime};base64,{payload}"


def _get_allowed_models() -> Optional[frozenset]:
    """Parse ALLOWED_MODELS env var (comma-separated).

    Entries may be exact ids or shell-style globs such as `google/*:free`.
    Returns None when all models are permitted.
    """
    raw = os.environ.get("ALLOWED_MODELS", "").strip()
    if not raw:
        return None
    return frozenset(m.strip() for m in raw.split(",") if m.strip())


def _is_model_allowed(model: str, allowed: Optional[frozenset]) -> bool:
    if allowed is None:
        return True
    candidate = (model or "").strip()
    return any(fnmatch.fnmatchcase(candidate, pattern) for pattern in allowed)


_SCREENSHOT_CAPS: tuple[tuple[int, int], ...] = (
    (1024, 768),
    (1280, 800),
    (1366, 768),
)


def _pick_capture_cap(width: int, height: int) -> tuple[int, int]:
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    aspect = width / height
    return min(_SCREENSHOT_CAPS, key=lambda cap: abs((cap[0] / cap[1]) - aspect))


def _captured_dimensions(width: int, height: int) -> tuple[int, int]:
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    cap_w, cap_h = _pick_capture_cap(width, height)
    scale = min(1.0, cap_w / width, cap_h / height)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _run_with_timeout(fn: Any, timeout_seconds: float, *, label: str) -> Any:
    """Run a blocking callable in a worker thread with a hard timeout."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="aicapture")
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise RuntimeError(f"{label} timed out") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _capture_hwnd_image(hwnd: int) -> Image.Image:
    import ctypes
    import win32con  # type: ignore
    import win32gui  # type: ignore
    import win32ui  # type: ignore

    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("Target window is not available for capture.")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)

    # Every GDI handle starts None and is cleaned up in `finally` only if it
    # was actually created. Acquiring these OUTSIDE a try (the old code) meant
    # any failure mid-acquisition leaked a window DC / compatible DC / bitmap.
    # Leaked GDI objects exhaust the per-process pool (~10k), after which
    # later CreateCompatibleDC/DeleteDC calls fail outright ("DeleteDC failed").
    hwnd_dc = None
    src_dc = None
    mem_dc = None
    bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc:
            raise RuntimeError("Could not acquire a window device context.")
        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bitmap)

        pw_render_fullcontent = 0x00000002
        result = _run_with_timeout(
            lambda: ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), pw_render_fullcontent),
            5.0,
            label="PrintWindow(fullcontent)",
        )
        if result != 1:
            result = _run_with_timeout(
                lambda: ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0),
                5.0,
                label="PrintWindow",
            )
        if result != 1:
            _run_with_timeout(
                lambda: mem_dc.BitBlt((0, 0), (width, height), src_dc, (0, 0), win32con.SRCCOPY),
                5.0,
                label="BitBlt",
            )

        bmp_info = bitmap.GetInfo()
        bmp_bytes = bitmap.GetBitmapBits(True)
        raw_image = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bytes,
            "raw",
            "BGRX",
            0,
            1,
        )
        # .copy() breaks the reference to bmp_bytes so it can be freed immediately
        image = raw_image.copy()
        raw_image.close()
        del bmp_bytes  # release the large Win32 bitmap buffer ASAP
        target_w, target_h = _pick_capture_cap(width, height)
        if image.size[0] > target_w or image.size[1] > target_h:
            image.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        return image
    finally:
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
        if mem_dc is not None:
            try:
                mem_dc.DeleteDC()
            except Exception:
                pass
        if src_dc is not None:
            try:
                src_dc.DeleteDC()
            except Exception:
                pass
        if hwnd_dc:
            try:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass


def _sanitize_json_text(text: str) -> str:
    """Strip trailing commas, JS-style comments, and repair common structural errors."""
    # Remove single-line comments //...
    text = re.sub(r'//[^\n]*', '', text)
    # Remove block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix missing commas between objects/arrays in a list
    text = re.sub(r'}\s*{', '}, {', text)
    text = re.sub(r']\s*\[', '], [', text)
    # Fix missing quotes on property names (keys)
    # Match { key: or , key: where key is alphanumeric
    text = re.sub(r'([{,]\s*)([a-zA-Z0-9_]+)(\s*:)', r'\1"\2"\3', text)
    # Fix missing commas between key:value pairs
    text = re.sub(r'("\s*:\s*[^,]+?)\s*(")', r'\1, \2', text)
    # Fix extra double quotes like ""}, or ""],
    text = re.sub(r'""\s*([}\]])', r'"\1', text)
    return text

def _extract_json(text: str) -> dict:
    """Extract and repair JSON from LLM response text. Always returns a dict."""
    if not text:
        return {}

    text = text.strip()
    # Cap input to avoid catastrophic backtracking on huge malformed responses
    if len(text) > 256 * 1024:
        text = text[:256 * 1024]

    # Try finding a markdown block first
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        json_str = fence.group(1).strip()
    else:
        # Fallback: outermost { ... }
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        json_str = match.group(1) if match else text.strip()

    def _ensure_dict(val: Any) -> dict:
        return val if isinstance(val, dict) else {"result": val}

    # Try standard parse
    try:
        return _ensure_dict(json.loads(json_str))
    except Exception:
        pass

    # Try sanitized parse
    try:
        sanitized = _sanitize_json_text(json_str)
        return _ensure_dict(json.loads(sanitized))
    except Exception:
        pass

    # Aggressive repair: fix unescaped newlines in strings
    repaired = json_str.replace('\n', '\\n').replace('\r', '\\r')
    repaired = re.sub(r'\\n\s*([{}\[\]])', r'\n\1', repaired)
    repaired = re.sub(r'([{}\[\]])\s*\\n', r'\1\n', repaired)

    try:
        return _ensure_dict(json.loads(_sanitize_json_text(repaired)))
    except Exception:
        # Final fallback: just try to load whatever we have
        try:
            return _ensure_dict(json.loads(_sanitize_json_text(json_str)))
        except Exception as e:
            raise ValueError(f"Failed to parse JSON: {e}\nRaw text was:\n{json_str}")



def _sentence_case_description(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "Execute sub-task."
    if cleaned[-1] not in ".!?":
        cleaned = f"{cleaned}."
    return cleaned


def _normalize_hierarchical_plan(payload: Any) -> Any:
    """Repair common malformed planner outputs before strict model validation."""
    if not isinstance(payload, dict):
        return payload

    def _repair_action(action: Any) -> Any:
        if not isinstance(action, dict):
            return action
        repaired_action = dict(action)
        args = repaired_action.get("args") if isinstance(repaired_action.get("args"), dict) else {}
        action_type = str(repaired_action.get("type") or "")
        if not args:
            command_value = (
                repaired_action.get("command")
                or repaired_action.get("cmd")
                or repaired_action.get("shell_command")
            )
            if action_type in {"bash", "run_command", "run_tests", "git", "lint_code"} and isinstance(command_value, str):
                args["command"] = command_value
            if isinstance(repaired_action.get("path"), str):
                args["path"] = repaired_action["path"]
            if isinstance(repaired_action.get("content"), str):
                args["content"] = repaired_action["content"]
            if isinstance(repaired_action.get("file_text"), str):
                args["file_text"] = repaired_action["file_text"]
            if isinstance(repaired_action.get("old_str"), str):
                args["old_str"] = repaired_action["old_str"]
            if isinstance(repaired_action.get("new_str"), str):
                args["new_str"] = repaired_action["new_str"]
        repaired_action["args"] = args
        return repaired_action

    normalized = dict(payload)
    raw_sub_tasks = normalized.get("sub_tasks")
    if not isinstance(raw_sub_tasks, list):
        return normalized

    fixed_sub_tasks: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_sub_tasks, start=1):
        if not isinstance(item, dict):
            fixed_sub_tasks.append(item)
            continue

        if "type" in item and "actions" not in item:
            action = _repair_action({
                "id": str(item.get("id", f"action-{index}")),
                "type": item.get("type"),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "explanation": item.get("explanation") or "",
                "requires_approval": bool(item.get("requires_approval", False)),
            })
            description = item.get("description") or item.get("explanation") or f"Run {action['type']}"
            fixed_sub_tasks.append(
                {
                    "id": f"subtask-{index}",
                    "description": _sentence_case_description(description.replace("_", " ")),
                    "actions": [action],
                }
            )
            continue

        repaired = dict(item)
        if isinstance(repaired.get("actions"), dict):
            repaired["actions"] = [repaired["actions"]]
        if isinstance(repaired.get("actions"), list):
            repaired["actions"] = [_repair_action(action) for action in repaired["actions"]]

        if not repaired.get("id"):
            repaired["id"] = f"subtask-{index}"

        if not repaired.get("description"):
            first_action = repaired["actions"][0] if isinstance(repaired.get("actions"), list) and repaired["actions"] else {}
            if isinstance(first_action, dict):
                description = (
                    repaired.get("title")
                    or first_action.get("explanation")
                    or (f"Run {first_action.get('type', 'task')}".replace("_", " "))
                )
            else:
                description = repaired.get("title") or f"Execute sub-task {index}"
            repaired["description"] = _sentence_case_description(description)

        fixed_sub_tasks.append(repaired)

    normalized.setdefault("reasoning", "Generated plan")
    normalized.setdefault("overall_complete", False)
    normalized["sub_tasks"] = fixed_sub_tasks
    return normalized


def _extract_chat_message_text(payload: Dict[str, Any]) -> str:
    """Extract assistant text from OpenAI-compatible chat responses."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or json.dumps(error)
        else:
            message = payload.get("message") or payload.get("detail") or json.dumps(payload)
        raise RuntimeError(f"Provider response did not include choices: {message}")

    message = choices[0].get("message", {})
    content = message.get("content")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif part.get("type") == "text" and isinstance(part.get("content"), str):
                    text_parts.append(part["content"])
        if text_parts:
            return "\n".join(text_parts)

    if isinstance(choices[0].get("text"), str):
        return choices[0]["text"]

    raise RuntimeError(f"Provider response contained choices but no readable text: {json.dumps(choices[0])}")


def classify_task_complexity(goal: str) -> str:
    """Returns 'atomic' or 'complex' based on keyword analysis."""
    g = goal.lower()
    atomic_signals = [
        "write", "create file", "create a file", "rename", "delete", "move file",
        "print", "run", "execute", "install", "append", "touch", "mkdir", "echo",
        "copy file", "read file", "hello world", "to a file", "save to", "open ",
        "search", "browse", "go to", "find", "navigate", "weather", "time",
        "edit", "modify", "update", "change", "fix", "add", "remove", "show", "list",
    ]
    complex_signals = [
        "refactor", "redesign", "architect", "fix all", "migrate", "integrate",
        "build an app", "create a server", "rewrite", "overhaul",
    ]
    if any(k in g for k in complex_signals):
        return "complex"
    if any(k in g for k in atomic_signals):
        return "atomic"
    # Default: short goals are atomic, long multi-step descriptions are complex
    return "atomic" if len(g.split()) <= 20 else "complex"


DEFAULT_OPENROUTER_MODEL = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"

# Speed tiers — each is an ordered free-model fallback chain. A user picks a
# tier ("tier:quick" / "tier:balanced") instead of a raw model; the chain
# survives the constant free-tier flakiness (most free models error at any
# given moment). Latencies measured 2026-05-18 against OpenRouter free tier.
MODEL_TIERS: Dict[str, List[str]] = {
    # Quick — fast, lighter. Fine for simple/short tasks; weaker at hard coding.
    "quick": [
        "liquid/lfm-2.5-1.2b-instruct:free",       # ~1s — genuinely fast, small
        "openai/gpt-oss-20b:free",                 # ~11s — capable fallback
        "nvidia/nemotron-nano-9b-v2:free",         # nano fallback
    ],
    # Balanced — best free quality; ~8-21s. The sensible default.
    "balanced": [
        "minimax/minimax-m2.5:free",               # ~9s — capable, mid speed
        "openai/gpt-oss-120b:free",                # ~21s — strongest free
        "nvidia/nemotron-3-super-120b-a12b:free",  # heavy fallback
    ],
}


def resolve_model_tier(model: str) -> Optional[str]:
    """If `model` names a speed tier ('tier:quick', 'balanced', ...), return the
    canonical tier key; otherwise None (it's a concrete model id)."""
    key = (model or "").strip().lower()
    if key.startswith("tier:"):
        key = key[5:]
    return key if key in MODEL_TIERS else None


class PlannerProvider:
    def __init__(self, model: str = DEFAULT_OPENROUTER_MODEL):
        # A speed-tier selection ("tier:quick"/"tier:balanced") resolves to its
        # primary model for all the per-model code paths; the full chain is
        # used by _openrouter_models_to_try via self.model_tier.
        self.model_tier: Optional[str] = resolve_model_tier(model)
        if self.model_tier:
            model = MODEL_TIERS[self.model_tier][0]
        self.model = model
        self._anthropic_key: Optional[str] = os.environ.get("ANTHROPIC_API_KEY")
        self._openai_key: Optional[str] = os.environ.get("OPENAI_API_KEY")
        self._google_key: Optional[str] = os.environ.get("GOOGLE_API_KEY")
        self._openrouter_key: Optional[str] = os.environ.get("OPENROUTER_API_KEY")
        self._groq_key: Optional[str] = os.environ.get("GROQ_API_KEY")
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        # Persistent HTTP client — reuses TCP connections and avoids SSL handshake per call
        self._http_client = httpx.Client(timeout=300)
        # Cache provider type so _is_X() string checks don't repeat every call
        m = model.lower()
        self._is_anthropic_model = "anthropic" in m or "claude" in m
        self._is_openai_model = ("openai" in m or "gpt" in m) and "openrouter" not in m
        self._is_openrouter_model = "openrouter" in m or ("/" in m and not self._is_anthropic_model and not self._is_openai_model)
        self._is_groq_model = "groq" in m

    @property
    def total_tokens(self) -> int:
        return self._total_input_tokens + self._total_output_tokens

    def _is_anthropic(self) -> bool:
        return self.model.startswith("claude") and not self.model.startswith("openrouter/")

    def _is_openai(self) -> bool:
        m = self.model.lower()
        # A model id with a "vendor/" prefix is an OpenRouter id (e.g.
        # "openai/gpt-oss-120b:free"), NOT the direct OpenAI API.
        if m.startswith("openrouter/") or "/" in m:
            return False
        return "gpt" in m or "o1" in m or "o3" in m

    def _is_google(self) -> bool:
        m = self.model.lower()
        # Only bare "gemini-*" ids hit the direct Google API. A "google/..."
        # id (e.g. "google/gemma-4-31b-it:free") is an OpenRouter model.
        if "/" in m:
            return False
        return m.startswith("gemini")

    def _is_groq(self) -> bool:
        m = self.model.lower()
        if m.startswith("openrouter/"):
            return False
        if m.startswith("groq/"):
            return True
        # A "vendor/model" id (e.g. "google/gemma-4-31b-it:free") is an
        # OpenRouter id — don't let the "gemma"/"llama" substring misroute it
        # to the Groq API. Only bare model names can be Groq-hosted.
        if "/" in m:
            return False
        return "llama" in m or "mixtral" in m or "gemma" in m

    def _chat_anthropic(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
            
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64:
            mime, data = _split_image_data(screenshot_b64)
            if data:
                content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": mime or "image/jpeg", "data": data}})
            
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        last_err = None
        for attempt in range(3):
            try:
                resp = self._http_client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": self._anthropic_key, "anthropic-version": "2023-06-01"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                self._total_input_tokens += usage.get("input_tokens", 0)
                self._total_output_tokens += usage.get("output_tokens", 0)
                return data["content"][0]["text"]
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in (402, 429) or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err or RuntimeError("All API retries exhausted")

    def _chat_openai(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._openai_key:
            raise RuntimeError("OPENAI_API_KEY not set")
            
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64:
            image_url = _image_data_url(screenshot_b64)
            if image_url:
                content.insert(0, {"type": "image_url", "image_url": {"url": image_url}})
            
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        payload = {"model": self.model, "max_tokens": 4096, "messages": messages}
        last_err = None
        for attempt in range(3):
            try:
                resp = self._http_client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._openai_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                self._total_input_tokens += usage.get("prompt_tokens", 0)
                self._total_output_tokens += usage.get("completion_tokens", 0)
                return _extract_chat_message_text(data)
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in (402, 429) or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err or RuntimeError("All API retries exhausted")

    def _chat_openrouter(self, system: str, prompt: str, screenshot_b64: Optional[str] = None, _model_override: Optional[str] = None) -> str:
        if not self._openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        base_model = _model_override if _model_override is not None else self.model
        models_to_try = self._openrouter_models_to_try(
            base_model.replace("openrouter/", ""), screenshot_b64
        )

        last_err = None
        for current_model in models_to_try:
            if current_model != models_to_try[0]:
                _log.info("Fallback activated: using %s", current_model)
            is_vision_model = any(
                x in current_model.lower()
                for x in ["vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma"]
            )
            content: List[Any] = [{"type": "text", "text": prompt}]
            if screenshot_b64 and is_vision_model:
                image_url = _image_data_url(screenshot_b64)
                if image_url:
                    content.insert(0, {"type": "image_url", "image_url": {"url": image_url}})
                
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ]
            payload = {"model": current_model, "messages": messages}
            
            is_last_model = (current_model == models_to_try[-1])
            for attempt in range(3 if is_last_model else 1):
                try:
                    resp = self._http_client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self._openrouter_key}"},
                        json=payload,
                    )
                    if resp.status_code != 200:
                        print(f"OPENROUTER ERROR ({current_model}):", resp.text)
                    resp.raise_for_status()
                    resp_json = resp.json()
                    if "error" in resp_json:
                        err_msg = resp_json["error"].get("message", str(resp_json["error"]))
                        print(f"OPENROUTER SOFT ERROR ({current_model}): {err_msg}")
                        # Rate/quota error on non-final model → skip to next model immediately
                        if not is_last_model:
                            break
                        if attempt < 2:
                            time.sleep(2 ** (attempt + 1))
                            continue
                        raise RuntimeError(f"OpenRouter error: {err_msg}")
                    if "choices" not in resp_json:
                        raise RuntimeError(f"Unexpected OpenRouter response: {str(resp_json)[:200]}")
                    return _extract_chat_message_text(resp_json)
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in (402, 429) or e.response.status_code >= 500:
                        if not is_last_model:
                            break  # fail fast to next model
                        time.sleep(2 ** (attempt + 1))
                        continue
                    break
            
            # If we reach here, this model failed all retries or hit a hard error.
            # The loop will continue to the next model in models_to_try.
        raise last_err or RuntimeError("All API retries exhausted")

    def _openrouter_models_to_try(self, requested_model: str, screenshot_b64: Optional[str] = None) -> List[str]:
        """Return an ordered OpenRouter model fallback chain for this request."""
        # Speed tier: use the tier's whole chain. The chain already survives
        # free-tier flakiness, so no per-model fallback logic is needed.
        if self.model_tier and self.model_tier in MODEL_TIERS:
            chain = list(MODEL_TIERS[self.model_tier])
            # A screenshot needs a vision model — prepend one if the tier's
            # primary is text-only.
            if screenshot_b64 and chain and not is_vision_model(chain[0]):
                # Prepend free vision models so a screenshot can actually be
                # seen. Two of them — the 31B is rate-limited often, so the
                # 26B is a same-family backup before the text-only tier kicks in.
                chain = ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"] + chain
            deduped_tier: List[str] = []
            for candidate in chain:
                if candidate not in deduped_tier:
                    deduped_tier.append(candidate)
            allowed_tier = _get_allowed_models()
            if allowed_tier is not None:
                deduped_tier = [m for m in deduped_tier if _is_model_allowed(m, allowed_tier)]
                if not deduped_tier:
                    raise ValueError(
                        f"No models in the {self.model_tier} tier are permitted by "
                        f"ALLOWED_MODELS={os.environ.get('ALLOWED_MODELS')!r}"
                    )
            return deduped_tier

        model = requested_model
        is_vision = any(
            x in model.lower()
            for x in ["vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma"]
        )

        # If a screenshot is present and the chosen model is text-only, upgrade to a vision-capable model.
        if screenshot_b64 and not is_vision:
            model = "google/gemma-4-31b-it:free"

        models_to_try: List[str] = [model]

        # Prefer the 31B Gemma model, but fall back to smaller Gemma then text-only models.
        if model == "google/gemma-4-31b-it:free":
            models_to_try.append("google/gemma-4-26b-a4b-it:free")
            models_to_try.append("meta-llama/llama-3.3-70b-instruct:free")
            models_to_try.append("nvidia/nemotron-3-super-120b-a12b:free")

        if model == "google/gemma-4-26b-a4b-it:free":
            models_to_try.append("meta-llama/llama-3.3-70b-instruct:free")
            models_to_try.append("nvidia/nemotron-3-super-120b-a12b:free")

        # Qwen3-Coder free tier rate-limits aggressively; fall back to Llama then Nemotron.
        # Note: model has already had "openrouter/" stripped by callers.
        if model == "qwen/qwen3-coder:free":
            models_to_try.append("meta-llama/llama-3.3-70b-instruct:free")
            models_to_try.append("nvidia/nemotron-3-super-120b-a12b:free")

        # Llama free tier also rate-limits; fall back to Nemotron.
        if model == "meta-llama/llama-3.3-70b-instruct:free":
            models_to_try.append("nvidia/nemotron-3-super-120b-a12b:free")

        deduped: List[str] = []
        for candidate in models_to_try:
            if candidate not in deduped:
                deduped.append(candidate)

        allowed = _get_allowed_models()
        if allowed is not None:
            deduped = [m for m in deduped if _is_model_allowed(m, allowed)]
            if not deduped:
                raise ValueError(
                    f"No models in fallback chain are permitted by ALLOWED_MODELS={os.environ.get('ALLOWED_MODELS')!r}"
                )
        return deduped

    def _chat_google(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._google_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
            
        model = self.model.replace("google/", "")
        
        parts: List[Any] = [{"text": prompt}]
        if screenshot_b64:
            mime, data = _split_image_data(screenshot_b64)
            if data:
                parts.insert(0, {"inline_data": {"mime_type": mime or "image/jpeg", "data": data}})
            
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": 4096}
        }
        
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=300) as client:
                    resp = client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self._google_key}",
                        json=payload,
                    )
                    resp.raise_for_status()
                    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in (402, 429) or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err or RuntimeError("All API retries exhausted")

    def _chat_groq(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._groq_key:
            raise RuntimeError("GROQ_API_KEY not set")
            
        model = self.model.replace("groq/", "")
        
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64 and ("llava" in model.lower() or "vision" in model.lower()):
            image_url = _image_data_url(screenshot_b64)
            if image_url:
                content.insert(0, {"type": "image_url", "image_url": {"url": image_url}})
            
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        payload = {"model": model, "max_tokens": 4096, "messages": messages}
        
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=300) as client:
                    resp = client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self._groq_key}"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    return _extract_chat_message_text(resp.json())
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in (402, 429) or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err or RuntimeError("All API retries exhausted")

    # Fallback model chain: when a provider 429s, try the next one
    _FALLBACK_MODELS = [
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/qwen/qwen3-coder:free",
        "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
    ]

    def _call_llm(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        # 1. Try the primary provider
        primary_fn = None
        if self._is_groq():
            primary_fn = self._chat_groq
        elif self._is_google():
            primary_fn = self._chat_google
        elif self._is_anthropic():
            primary_fn = self._chat_anthropic
        elif self._is_openai():
            primary_fn = self._chat_openai
        else:
            # Already OpenRouter — no fallback needed, it has its own retry chain
            return self._chat_openrouter(system, prompt, screenshot_b64)

        try:
            return primary_fn(system, prompt, screenshot_b64)
        except (httpx.HTTPStatusError, RuntimeError) as primary_err:
            # Check if this is a rate-limit (429) or server error (5xx)
            is_retryable = False
            if isinstance(primary_err, httpx.HTTPStatusError):
                is_retryable = primary_err.response.status_code in (402, 429) or primary_err.response.status_code >= 500
            elif "rate" in str(primary_err).lower() or "429" in str(primary_err) or "402" in str(primary_err):
                is_retryable = True

            if not is_retryable or not self._openrouter_key:
                raise  # Non-retryable error or no fallback available

            print(f"[FALLBACK] Primary model '{self.model}' hit rate limit. Falling back to OpenRouter...", flush=True)

            # 2. Try OpenRouter fallback models with the SAME context
            for fallback_model in self._FALLBACK_MODELS:
                try:
                    print(f"[FALLBACK] Trying {fallback_model}...", flush=True)
                    result = self._chat_openrouter(system, prompt, screenshot_b64, _model_override=fallback_model)
                    print(f"[FALLBACK] Success with {fallback_model}", flush=True)
                    return result
                except Exception as fallback_err:
                    print(f"[FALLBACK] {fallback_model} also failed: {fallback_err}", flush=True)
                    continue

            # All fallbacks exhausted — raise original error with note
            num_fallbacks = len(self._FALLBACK_MODELS)
            raise RuntimeError(
                f"{primary_err} (also tried {num_fallbacks} fallback models)"
            ) from primary_err

    async def stream_chat(self, system: str, messages: List[Dict[str, Any]], screenshot_b64: Optional[str] = None):
        """Async generator that streams tokens from OpenRouter/OpenAI."""
        import httpx

        # If no key, fallback to standard error
        if not self._openrouter_key and not self._openai_key and not self._groq_key:
             raise RuntimeError("No API key available for streaming (OPENROUTER/OPENAI/GROQ).")

        # Determine endpoint and key
        if self._is_openai():
            url = "https://api.openai.com/v1/chat/completions"
            key = self._openai_key
            model = self.model
        elif self._is_groq():
            url = "https://api.groq.com/openai/v1/chat/completions"
            key = self._groq_key
            model = self.model.replace("groq/", "")
            models_to_try = [model]
        else: # Default OpenRouter
            url = "https://openrouter.ai/api/v1/chat/completions"
            key = self._openrouter_key
            models_to_try = self._openrouter_models_to_try(
                self.model.replace("openrouter/", ""), screenshot_b64
            )
            model = models_to_try[0]

        if not self._is_openai() and not self._is_groq():
            # model already set above
            pass
        else:
            models_to_try = [model]

        last_err = None
        for current_model in models_to_try:
            is_vision_model = any(
                x in current_model.lower()
                for x in ["vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma"]
            )

            formatted_messages = [{"role": "system", "content": system}]
            for m in messages:
                if m["role"] == "user" and screenshot_b64 and m == messages[-1] and is_vision_model:
                    # Attach screenshot to the latest user message if vision is supported
                    image_url = _image_data_url(screenshot_b64)
                    if image_url:
                        formatted_messages.append({
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image_url}},
                                {"type": "text", "text": m["content"]}
                            ]
                        })
                    else:
                        formatted_messages.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
                elif m["role"] == "tool":
                    formatted_messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": f"<observation>\n{m.get('content', '')}\n</observation>"}],
                    })
                elif m["role"] == "assistant" and "tool_calls" in m:
                    tool_xml_parts = []
                    for tc in m.get("tool_calls", []):
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        name = fn.get("name", "tool_call")
                        arguments = fn.get("arguments", "{}")
                        tool_xml_parts.append(
                            f"<action type=\"{name}\">\n{arguments}\n</action>"
                        )
                    assistant_text = "\n".join(
                        part for part in [m.get("content", "") or "", *tool_xml_parts] if part
                    )
                    formatted_messages.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": assistant_text}],
                    })
                else:
                    formatted_messages.append({"role": m["role"], "content": [{"type": "text", "text": m["content"]}]})

            # Explicit max_tokens — without it OpenRouter auto-fills the model's
            # full context as the completion budget, which overshoots providers
            # that cap output lower (e.g. Venice caps Llama-3.3-70b at 16384).
            payload = {"model": current_model, "messages": formatted_messages, "stream": True, "max_tokens": 8192}

            # When OpenRouter already has a model fallback chain, fail over quickly
            # instead of spending a full backoff ladder on a rate-limited first choice.
            _retry_delays = [] if len(models_to_try) > 1 else [5, 15, 30]
            for _attempt, _delay in enumerate([0] + _retry_delays):
                if _delay:
                    await asyncio.sleep(_delay)
                try:
                    _timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=10.0)
                    async with httpx.AsyncClient(timeout=_timeout) as client:
                        async with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as resp:
                            if resp.status_code in (402, 429) and _attempt < len(_retry_delays):
                                continue  # retry same model
                            # For non-rate-limit 4xx/5xx, read the body so the real
                            # reason is surfaced — a bare "400 Bad Request" hides
                            # whether the model rejected `tools`, the payload, etc.
                            if resp.status_code >= 400 and resp.status_code not in (402, 429):
                                _body = await resp.aread()
                                _detail = _body.decode("utf-8", errors="ignore").strip()[:600]
                                # Record the error and fall through to the next model
                                # in the fallback chain instead of aborting the task.
                                last_err = RuntimeError(f"OpenRouter {resp.status_code} ({current_model}): {_detail or 'empty response body'}")
                                break  # move to next model fallback
                            resp.raise_for_status()
                            async for chunk in resp.aiter_lines():
                                if chunk.startswith("data: "):
                                    data_str = chunk[6:]
                                    if data_str.strip() == "[DONE]":
                                        break
                                    try:
                                        data = json.loads(data_str)
                                        if "choices" in data and len(data["choices"]) > 0:
                                            delta = data["choices"][0].get("delta", {})
                                            if "content" in delta and delta["content"]:
                                                yield delta["content"]
                                    except json.JSONDecodeError:
                                        pass
                    return
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in (402, 429) and _attempt < len(_retry_delays):
                        continue
                    if e.response.status_code in (402, 429):
                        break  # move to next model fallback
                    raise

        if last_err:
            raise last_err

    async def stream_chat_with_tools(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], screenshot_b64: Optional[str] = None):
        """Async generator that streams tool calls via native function calling.
        
        Yields dicts with structure:
            {"type": "thought", "content": "..."} — assistant reasoning text
            {"type": "tool_call", "name": "...", "args": {...}} — structured tool call
            {"type": "done"} — stream finished
        """
        import httpx

        if not self._openrouter_key and not self._openai_key and not self._groq_key:
            raise RuntimeError("No API key available for streaming (OPENROUTER/OPENAI/GROQ).")

        # Determine endpoint and key
        if self._is_openai():
            url = "https://api.openai.com/v1/chat/completions"
            key = self._openai_key
            model = self.model
        elif self._is_groq():
            url = "https://api.groq.com/openai/v1/chat/completions"
            key = self._groq_key
            model = self.model.replace("groq/", "")
            models_to_try = [model]
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"
            key = self._openrouter_key
            models_to_try = self._openrouter_models_to_try(
                self.model.replace("openrouter/", ""), screenshot_b64
            )
            model = models_to_try[0]

        if not self._is_openai() and not self._is_groq():
            pass
        else:
            models_to_try = [model]

        last_err = None
        for current_model in models_to_try:
            if current_model != models_to_try[0]:
                _log.info("Fallback activated: using %s", current_model)
                yield {"type": "provider_info", "model": current_model, "fallback": True}
            is_vision_model = any(
                x in current_model.lower()
                for x in ["vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma"]
            )

            formatted_messages = [{"role": "system", "content": system}]
            for m in messages:
                if m["role"] == "user" and screenshot_b64 and m == messages[-1] and is_vision_model:
                    image_url = _image_data_url(screenshot_b64)
                    if image_url:
                        formatted_messages.append({
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image_url}},
                                {"type": "text", "text": m["content"]}
                            ]
                        })
                    else:
                        formatted_messages.append({"role": "user", "content": m.get("content", "")})
                elif m["role"] == "tool":
                    # Tool result messages pass through directly
                    formatted_messages.append(m)
                elif m["role"] == "assistant" and "tool_calls" in m:
                    # Preserve tool_calls in assistant messages for multi-turn tool calling
                    formatted_messages.append({
                        "role": "assistant",
                        "content": m.get("content", "") or None,
                        "tool_calls": m["tool_calls"],
                    })
                else:
                    formatted_messages.append({"role": m["role"], "content": m.get("content", "")})

            payload = {
                "model": current_model,
                "messages": formatted_messages,
                "tools": tools,
                "stream": True,
                # Explicit cap — see note in stream_chat(); avoids overshooting
                # providers that cap completion tokens below the context window.
                "max_tokens": 8192,
            }

            thought_buffer = ""
            # Keyed by tool_call index; each entry: {id, name, args_buffer}
            tool_calls_accum: dict[int, dict] = {}

            # When OpenRouter already has a model fallback chain, fail over quickly
            # instead of spending a full backoff ladder on a rate-limited first choice.
            _retry_delays = [] if len(models_to_try) > 1 else [5, 15, 30]
            for _attempt, _delay in enumerate([0] + _retry_delays):
                if _delay:
                    await asyncio.sleep(_delay)
                try:
                    _timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=10.0)
                    async with httpx.AsyncClient(timeout=_timeout) as client:
                        async with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as resp:
                            if resp.status_code in (402, 429) and _attempt < len(_retry_delays):
                                continue  # retry same model
                            # For non-rate-limit 4xx/5xx, read the body so the real
                            # reason is surfaced — a bare "400 Bad Request" hides
                            # whether the model rejected `tools`, the payload, etc.
                            if resp.status_code >= 400 and resp.status_code not in (402, 429):
                                _body = await resp.aread()
                                _detail = _body.decode("utf-8", errors="ignore").strip()[:600]
                                # Record the error and fall through to the next model
                                # in the fallback chain instead of aborting the task.
                                last_err = RuntimeError(f"OpenRouter {resp.status_code} ({current_model}): {_detail or 'empty response body'}")
                                break  # move to next model fallback
                            resp.raise_for_status()
                            async for chunk in resp.aiter_lines():
                                if chunk.startswith("data: "):
                                    data_str = chunk[6:]
                                    if data_str.strip() == "[DONE]":
                                        break
                                    try:
                                        data = json.loads(data_str)
                                        if "choices" not in data or not data["choices"]:
                                            continue
                                        choice = data["choices"][0]
                                        delta = choice.get("delta", {})
                                        finish_reason = choice.get("finish_reason")

                                        # Content (thought/reasoning text)
                                        if "content" in delta and delta["content"]:
                                            thought_buffer += delta["content"]
                                            yield {"type": "thought", "content": delta["content"]}

                                        # Tool calls — accumulate per-index to support parallel calls
                                        if "tool_calls" in delta:
                                            for tc in delta["tool_calls"]:
                                                idx = tc.get("index", 0)
                                                if idx not in tool_calls_accum:
                                                    tool_calls_accum[idx] = {"id": "", "name": "", "args_buffer": ""}
                                                entry = tool_calls_accum[idx]
                                                if "id" in tc:
                                                    entry["id"] = tc["id"]
                                                fn = tc.get("function", {})
                                                if "name" in fn:
                                                    entry["name"] = fn["name"]
                                                if "arguments" in fn:
                                                    entry["args_buffer"] += fn["arguments"]

                                        # Finish — emit all accumulated tool calls in index order
                                        if finish_reason in ("tool_calls", "stop") and tool_calls_accum:
                                            for idx in sorted(tool_calls_accum):
                                                entry = tool_calls_accum[idx]
                                                buf = entry["args_buffer"]
                                                try:
                                                    args = json.loads(buf) if buf else {}
                                                except json.JSONDecodeError:
                                                    try:
                                                        args = json.loads(_sanitize_json_text(buf))
                                                    except (json.JSONDecodeError, ValueError, TypeError):
                                                        args = {}
                                                yield {
                                                    "type": "tool_call",
                                                    "id": entry["id"],
                                                    "name": entry["name"],
                                                    "args": args,
                                                    "thought": thought_buffer,
                                                }
                                            return

                                        if finish_reason == "stop" and not tool_calls_accum:
                                            yield {"type": "text_only", "content": thought_buffer}
                                            return

                                    except json.JSONDecodeError:
                                        pass
                    break
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in (402, 429) and _attempt < len(_retry_delays):
                        continue
                    if e.response.status_code in (402, 429):
                        break  # move to next model fallback
                    raise

        if last_err:
            raise last_err

        # If we reach here without a tool call, yield what we have
        if thought_buffer:
            yield {"type": "text_only", "content": thought_buffer}

    def plan_hierarchical(
        self,
        goal: str,
        latest_screenshot_b64: Optional[str] = None,
        memory_context: Optional[str] = None,
        mode: str = "computer",
        system_prompt_extension: Optional[str] = None,
    ) -> HierarchicalPlan:
        prompt = f"Goal: {goal}\n\nFor simple one-action tasks, use exactly 1 sub-task. For complex tasks, decompose into 2-8 sequential sub-tasks with concrete actions."
        if memory_context:
            prompt = f"Relevant past experience:\n{memory_context[:1500]}\n\n{prompt}"
        
        packs = get_mode_packs(mode)
        tool_guidance = get_tool_guidance(packs)
        
        if mode == "coding":
            system = CODING_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)
        elif mode == "computer_use":
            system = COMPUTER_USE_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)
        else:
            system = HIERARCHICAL_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)

        if system_prompt_extension:
            system = f"{system}\n\n{system_prompt_extension}"

        if mode == "coding":
            raw_text = self._call_llm(system, prompt)  # no screenshot for coding
        elif mode == "computer_use":
            raw_text = self._call_llm(system, prompt)  # no screenshot — DOM-based
        else:
            raw_text = self._call_llm(system, prompt, latest_screenshot_b64)
            
        return HierarchicalPlan.model_validate(_normalize_hierarchical_plan(_extract_json(raw_text)))

    def reflect_on_subtask(
        self,
        description: str,
        actions: List[Dict[str, Any]],
        results: List[str],
        post_screenshot_b64: Optional[str] = None,
        mode: str = "computer",
        system_prompt_extension: Optional[str] = None,
    ) -> Dict[str, Any]:
        packs = get_mode_packs(mode)
        tool_guidance = get_tool_guidance(packs)
        
        if mode == "coding":
            prompt = (
                f"Sub-task: {description}\n\n"
                f"Actions taken:\n{json.dumps(actions, indent=2)}\n\n"
                f"Results (stdout/stderr/file contents):\n{json.dumps(results, indent=2)}\n\n"
                "Based on the action results, did this sub-task succeed?"
            )
            raw_text = self._call_llm(CODING_REFLECT_PROMPT.format(tool_guidance=tool_guidance), prompt)  # no screenshot
        elif mode == "computer_use":
            prompt = (
                f"Sub-task: {description}\n\n"
                f"Actions taken:\n{json.dumps(actions, indent=2)}\n\n"
                f"Results (page text / accessibility trees / URLs):\n{json.dumps([r[:2500] for r in results])}\n\n"
                "Based on the action results, did this sub-task succeed?"
            )
            raw_text = self._call_llm(COMPUTER_USE_REFLECT_PROMPT.format(tool_guidance=tool_guidance), prompt)  # no screenshot
        else:
            prompt = (
                f"Sub-task: {description}\n\n"
                f"Actions taken:\n{json.dumps(actions, indent=2)}\n\n"
                f"Results:\n{json.dumps(results, indent=2)}\n\n"
                "Based on the screenshot and results, did this sub-task succeed?"
            )
            system = REFLECT_SYSTEM_PROMPT
            if system_prompt_extension:
                system = f"{system}\n\n{system_prompt_extension}"
            raw_text = self._call_llm(system, prompt, post_screenshot_b64)
        try:
            result = _extract_json(raw_text)
            if not isinstance(result, dict):
                raise ValueError("Non-dict reflection result")
            return result
        except Exception:
            return {"success": True, "reason": "Reflection parse failed; assuming success."}

    def evaluate(
        self, goal: str, history: List[str], latest_screenshot_b64: Optional[str] = None,
        mode: str = "computer",
        system_prompt_extension: Optional[str] = None,
    ) -> Dict[str, Any]:
        recent = history[-20:]
        prompt = f"Goal: {goal}\n\nRecent action history:\n" + "\n".join(recent) + "\n\nIs the overall goal now complete?"
        if mode == "coding":
            raw_text = self._call_llm(CODING_EVALUATE_PROMPT, prompt)  # no screenshot
        elif mode == "computer_use":
            raw_text = self._call_llm(COMPUTER_USE_EVALUATE_PROMPT, prompt)  # no screenshot
        else:
            system = EVALUATE_SYSTEM_PROMPT
            if system_prompt_extension:
                system = f"{system}\n\n{system_prompt_extension}"
            raw_text = self._call_llm(system, prompt, latest_screenshot_b64)
        try:
            result = _extract_json(raw_text)
            if not isinstance(result, dict):
                raise ValueError("Non-dict evaluation result")
            # Normalise: if "complete" key is absent but response looks positive, default to False
            result.setdefault("complete", False)
            result.setdefault("reason", "")
            return result
        except Exception:
            return {"complete": False, "reason": "Evaluation failed to parse LLM response."}


__all__ = [
    "PlannerProvider",
    "detect_task_mode",
    "classify_task_complexity",
    "_capture_screenshot_b64",
    "_get_active_window_rect",
    "_get_hwnd_for_title",
    "_capture_hwnd_screenshot_b64",
    "infer_isolated_app_name",
    "_extract_json",
    "CODING_SYSTEM_PROMPT",
    "HIERARCHICAL_SYSTEM_PROMPT",
    "COMPUTER_USE_SYSTEM_PROMPT"
]
