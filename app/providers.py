from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from PIL import Image

from .models import HierarchicalPlan
from .tool_registry import get_tool_guidance, get_mode_packs

SYSTEM_PROMPT = """You are a computer control planner. Use the provided actions to achieve the user's goal."""

HIERARCHICAL_SYSTEM_PROMPT = """You are a hierarchical planning engine for an autonomous computer agent.
Return ONLY valid JSON with shape:
{{
  "reasoning": str,
  "overall_complete": bool,
  "execution_mode": "serial" | "parallel",
  "max_parallel_workers": int,
  "sub_tasks": [
    {{
      "id": str,
      "description": str,
      "depends_on": [str],
      "write_scope": [str],
      "actions": [{{ "id": str, "type": str, "args": object, "explanation": str, "requires_approval": bool }}]
    }}
  ]
}}
For simple one-action tasks, use exactly 1 sub-task. For complex tasks, decompose into 2-8 sequential sub-tasks. Each sub-task should be independently verifiable.
If multiple sub-tasks can run safely in parallel (without touching the same files/directories), set execution_mode to "parallel".

Available actions:
{tool_guidance}

Never output markdown. Never output prose outside JSON."""

# ──────────────────────────────────────────────────────────────────────────────
#  CODING MODE PROMPTS  — no screenshots, no mouse/keyboard/vision actions
# ──────────────────────────────────────────────────────────────────────────────
CODING_SYSTEM_PROMPT = """You are an expert autonomous coding agent. You write, read, and execute code.
Return ONLY valid JSON with shape:
{{
  "reasoning": str,
  "overall_complete": bool,
  "execution_mode": "serial" | "parallel",
  "max_parallel_workers": int,
  "sub_tasks": [
    {{
      "id": str,
      "description": str,
      "depends_on": [str],
      "write_scope": [str],
      "actions": [{{ "id": str, "type": str, "args": object, "explanation": str, "requires_approval": false }}]
    }}
  ]
}}
Decompose the goal into sequential or parallel sub-tasks. Each sub-task should be independently verifiable.
If multiple sub-tasks can run safely in parallel (without touching the same files/directories), set execution_mode to "parallel".

Available actions:
{tool_guidance}

Rules:
1. The system environment (OS, paths, python command) is provided in the prompt. Use those EXACT paths.
2. For project/code files: use relative paths (resolved from the workspace directory).
3. Use list_directory to explore or verify folder contents when needed.
4. Create directories automatically via write_file (parents are auto-created).
5. Always verify your work: after writing code, run it or read it back.
6. When you generate action ids, use short descriptive strings like "create-main", "run-test", etc.
Never output markdown. Never output prose outside JSON."""

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
1. Your FIRST action in the first sub-task MUST be request_permission with the right scope (google_sheets if the task involves Google Sheets, otherwise browser).
2. After browser_open or any click that navigates, wait 1-2 seconds then call browser_accessibility_tree to see the new page state.
3. Use CSS selectors based on the accessibility tree output. Prefer stable selectors: input[type=...], button[aria-label=...], #id, [role=...].
4. NEVER use pixel coordinates. NEVER use mouse_click, keyboard_type, or screenshot. Those are blocked in this mode.
5. For Google Sheets: open https://docs.google.com/spreadsheets/ and use browser_accessibility_tree to see cells. Click a cell then browser_type to write.
Never output markdown. Never output prose outside JSON."""


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


def detect_task_mode(goal: str, explicit_mode: Optional[str] = None) -> str:
    """Return 'coding', 'computer_use', or 'computer'. If explicit_mode is set, honour it."""
    if explicit_mode and explicit_mode in ("coding", "computer", "computer_use"):
        return explicit_mode
    g = goal.lower()
    computer_use_score = sum(1 for kw in _COMPUTER_USE_KEYWORDS if kw in g)
    coding_score = sum(1 for kw in _CODING_KEYWORDS if kw in g)
    computer_score = sum(1 for kw in _COMPUTER_KEYWORDS if kw in g)
    if computer_use_score >= 2 and computer_use_score > coding_score:
        return "computer_use"
    if computer_score >= 2 and computer_score > coding_score:
        return "computer"
    return "coding"


def get_scale_factor(width: int, height: int) -> float:
    long_edge_scale = 1568 / max(width, height)
    total_pixels_scale = math.sqrt(1_150_000 / (width * height))
    return min(1.0, long_edge_scale, total_pixels_scale)


def _capture_screenshot_b64(width: int, height: int) -> str:
    import mss

    # Cap at 1280x800
    w = min(width, 1280)
    h = min(height, 800)
    with mss.mss() as sct:
        monitor = {"left": 0, "top": 0, "width": w, "height": h}
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        if image.size[0] > w or image.size[1] > h:
            image.thumbnail((w, h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")


def _sanitize_json_text(text: str) -> str:
    """Strip trailing commas and JS-style comments that some LLMs emit."""
    # Remove single-line comments //...
    text = re.sub(r'//[^\n]*', '', text)
    # Remove block comments /* ... */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM response text, handling markdown code fences and conversational filler."""
    text = text.strip()
    # Try finding a markdown block first
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    else:
        # Fallback: extract anything that looks like a JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1].strip()

    # First attempt — raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt — after sanitizing trailing commas / comments
    try:
        return json.loads(_sanitize_json_text(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}\nRaw text was:\n{text}")


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
            action = {
                "id": str(item.get("id", f"action-{index}")),
                "type": item.get("type"),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "explanation": item.get("explanation") or "",
                "requires_approval": bool(item.get("requires_approval", False)),
            }
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
    words = g.split()
    atomic_signals = ["write", "create file", "rename", "delete", "move file", "print", "run", "execute", "install", "append", "touch", "mkdir", "echo", "copy file", "read file", "hello world", "to a file", "save to"]
    complex_signals = ["refactor", "redesign", "improve", "optimize", "analyze", "architect", "fix all", "migrate", "integrate", "full", "entire", "build an app", "create a server"]

    if len(words) <= 25 and any(k in g for k in atomic_signals):
        if not any(k in g for k in complex_signals):
            return "atomic"
    return "complex"


class PlannerProvider:
    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        self.model = model
        self._anthropic_key: Optional[str] = os.environ.get("ANTHROPIC_API_KEY")
        self._openai_key: Optional[str] = os.environ.get("OPENAI_API_KEY")
        self._google_key: Optional[str] = os.environ.get("GOOGLE_API_KEY")
        self._openrouter_key: Optional[str] = os.environ.get("OPENROUTER_API_KEY")
        self._groq_key: Optional[str] = os.environ.get("GROQ_API_KEY")

    def _is_anthropic(self) -> bool:
        return self.model.startswith("claude") and not self.model.startswith("openrouter/")

    def _is_openai(self) -> bool:
        m = self.model.lower()
        return not m.startswith("openrouter/") and ("gpt" in m or "o1" in m or "o3" in m)

    def _is_google(self) -> bool:
        m = self.model.lower()
        return not m.startswith("openrouter/") and (m.startswith("gemini") or m.startswith("google/"))

    def _is_groq(self) -> bool:
        m = self.model.lower()
        return not m.startswith("openrouter/") and (m.startswith("groq/") or "llama" in m or "mixtral" in m or "gemma" in m)

    def _chat_anthropic(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
            
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}})
            
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=300) as client:
                    resp = client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": self._anthropic_key, "anthropic-version": "2023-06-01"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    return resp.json()["content"][0]["text"]
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err

    def _chat_openai(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._openai_key:
            raise RuntimeError("OPENAI_API_KEY not set")
            
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64:
            content.insert(0, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}})
            
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        payload = {"model": self.model, "max_tokens": 4096, "messages": messages}
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=300) as client:
                    resp = client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self._openai_key}"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    return _extract_chat_message_text(resp.json())
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err

    def _chat_openrouter(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
            
        model = self.model.replace("openrouter/", "")
        is_vision_model = any(x in model.lower() for x in ["vision", "vl", "gemini", "claude", "gpt-4o", "gpt-4-turbo", "pixtral", "llava", "gemma"])
        
        # If task has a screenshot but the user selected a non-vision model (e.g. Nemotron),
        # automatically swap to the preferred vision model.
        if screenshot_b64 and not is_vision_model:
            model = "google/gemma-4-31b-it:free"
            is_vision_model = True
            
        models_to_try = [model]
        # If using the preferred 31B model, set up the 26B as a fallback
        if model == "google/gemma-4-31b-it:free":
            models_to_try.append("google/gemma-4-26b-a4b-it:free")
            
        last_err = None
        for current_model in models_to_try:
            content: List[Any] = [{"type": "text", "text": prompt}]
            if screenshot_b64 and is_vision_model:
                content.insert(0, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}})
                
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ]
            payload = {"model": current_model, "messages": messages}
            
            success = False
            for attempt in range(5):
                try:
                    with httpx.Client(timeout=300) as client:
                        resp = client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {self._openrouter_key}"},
                            json=payload,
                        )
                        if resp.status_code != 200:
                            print(f"OPENROUTER ERROR ({current_model}):", resp.text)
                        resp.raise_for_status()
                        resp_json = resp.json()
                        # OpenRouter can return 200 + {"error": {...}} when rate-limited or quota-exceeded.
                        if "error" in resp_json:
                            err_msg = resp_json["error"].get("message", str(resp_json["error"]))
                            if attempt < 4:
                                print(f"OPENROUTER SOFT ERROR ({current_model}, attempt {attempt + 1}): {err_msg}")
                                time.sleep(2 ** (attempt + 1))
                                continue
                            raise RuntimeError(f"OpenRouter error: {err_msg}")
                        if "choices" not in resp_json:
                            raise RuntimeError(f"Unexpected OpenRouter response: {str(resp_json)[:200]}")
                        return _extract_chat_message_text(resp_json)
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code == 429 or e.response.status_code >= 500:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    break # Hard error, stop retrying this model
            
            # If we reach here, this model failed all retries or hit a hard error.
            # The loop will continue to the next model in models_to_try.
        raise last_err

    def _chat_google(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._google_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
            
        model = self.model.replace("google/", "")
        
        parts: List[Any] = [{"text": prompt}]
        if screenshot_b64:
            parts.insert(0, {"inline_data": {"mime_type": "image/png", "data": screenshot_b64}})
            
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
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err

    def _chat_groq(self, system: str, prompt: str, screenshot_b64: Optional[str] = None) -> str:
        if not self._groq_key:
            raise RuntimeError("GROQ_API_KEY not set")
            
        model = self.model.replace("groq/", "")
        
        content: List[Any] = [{"type": "text", "text": prompt}]
        if screenshot_b64 and ("llava" in model.lower() or "vision" in model.lower()):
            content.insert(0, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64} "}})
            
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
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err

    # Fallback model chain: when a provider 429s, try the next one
    _FALLBACK_MODELS = [
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
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
                is_retryable = primary_err.response.status_code == 429 or primary_err.response.status_code >= 500
            elif "rate" in str(primary_err).lower() or "429" in str(primary_err):
                is_retryable = True

            if not is_retryable or not self._openrouter_key:
                raise  # Non-retryable error or no fallback available

            print(f"[FALLBACK] Primary model '{self.model}' hit rate limit. Falling back to OpenRouter...", flush=True)

            # 2. Try OpenRouter fallback models with the SAME context
            original_model = self.model
            for fallback_model in self._FALLBACK_MODELS:
                try:
                    self.model = fallback_model
                    print(f"[FALLBACK] Trying {fallback_model}...", flush=True)
                    result = self._chat_openrouter(system, prompt, screenshot_b64)
                    print(f"[FALLBACK] Success with {fallback_model}", flush=True)
                    return result
                except Exception as fallback_err:
                    print(f"[FALLBACK] {fallback_model} also failed: {fallback_err}", flush=True)
                    continue
                finally:
                    self.model = original_model  # Always restore original model name

            # All fallbacks exhausted — raise original error
            raise primary_err

    def plan_hierarchical(
        self,
        goal: str,
        latest_screenshot_b64: Optional[str] = None,
        memory_context: Optional[str] = None,
        mode: str = "computer",
    ) -> HierarchicalPlan:
        prompt = f"Goal: {goal}\n\nFor simple one-action tasks, use exactly 1 sub-task. For complex tasks, decompose into 2-8 sequential sub-tasks with concrete actions."
        if memory_context:
            prompt = f"Relevant past experience:\n{memory_context}\n\n{prompt}"
        
        packs = get_mode_packs(mode)
        tool_guidance = get_tool_guidance(packs)
        
        if mode == "coding":
            system = CODING_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)
            raw_text = self._call_llm(system, prompt)  # no screenshot for coding
        elif mode == "computer_use":
            system = COMPUTER_USE_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)
            raw_text = self._call_llm(system, prompt)  # no screenshot — DOM-based
        else:
            system = HIERARCHICAL_SYSTEM_PROMPT.format(tool_guidance=tool_guidance)
            raw_text = self._call_llm(system, prompt, latest_screenshot_b64)
            
        return HierarchicalPlan.model_validate(_normalize_hierarchical_plan(_extract_json(raw_text)))

    def reflect_on_subtask(
        self,
        description: str,
        actions: List[Dict[str, Any]],
        results: List[str],
        post_screenshot_b64: Optional[str] = None,
        mode: str = "computer",
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
                f"Results (page text / accessibility trees / URLs):\n{json.dumps(results, indent=2)[:8000]}\n\n"
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
            raw_text = self._call_llm(REFLECT_SYSTEM_PROMPT, prompt, post_screenshot_b64)
        return _extract_json(raw_text)

    def evaluate(
        self, goal: str, history: List[str], latest_screenshot_b64: Optional[str] = None,
        mode: str = "computer",
    ) -> Dict[str, Any]:
        recent = history[-20:]
        prompt = f"Goal: {goal}\n\nRecent action history:\n" + "\n".join(recent) + "\n\nIs the overall goal now complete?"
        if mode == "coding":
            raw_text = self._call_llm(CODING_EVALUATE_PROMPT, prompt)  # no screenshot
        elif mode == "computer_use":
            raw_text = self._call_llm(COMPUTER_USE_EVALUATE_PROMPT, prompt)  # no screenshot
        else:
            raw_text = self._call_llm(EVALUATE_SYSTEM_PROMPT, prompt, latest_screenshot_b64)
        return _extract_json(raw_text)


__all__ = [
    "PlannerProvider",
    "detect_task_mode",
    "classify_task_complexity",
    "_capture_screenshot_b64",
    "_extract_json",
    "CODING_SYSTEM_PROMPT",
    "HIERARCHICAL_SYSTEM_PROMPT",
    "COMPUTER_USE_SYSTEM_PROMPT"
]
