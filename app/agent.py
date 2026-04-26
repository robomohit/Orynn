from __future__ import annotations
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from .background_browser import BackgroundBrowser
from .log_emitter import LogEmitter
from .memory import MemoryStore
from .models import (
    Action,
    ActionDecision,
    ActionType,
    AgentContext,
    ApprovalBundle,
    TaskRecord,
    SubTask,
    TaskStatus,
    ToolError,
    ToolResult,
)
from .permissions import PermissionStore, scope_for_action
from .providers import PlannerProvider, _capture_screenshot_b64, _get_active_window_rect, _get_hwnd_for_title, detect_task_mode, classify_task_complexity, infer_isolated_app_name
from .safety import SafetyManager
from .text_editor import TextEditorTool
from .tools import ToolExecutor
from .plugins import PluginRegistry
from .skills import skill_manager

_log = logging.getLogger("agent")

TOKEN_BUDGET_DEFAULT = 100_000  # max combined input+output tokens per task
MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 120.0
XML_FALLBACK_MAX_STEPS = 3

_SCREENSHOT_ACTIONS = {
    ActionType.mouse_click,
    ActionType.keyboard_type,
    ActionType.scroll,
    ActionType.double_click,
    ActionType.right_click,
    ActionType.middle_click,
    ActionType.mouse_move,
    ActionType.left_click_drag,
    ActionType.key_combo,
}

class SubTaskWorker:
    """Handles the execution of a single sub-task."""
    def __init__(
        self,
        worker_id: str,
        task_id: str,
        sub_task: SubTask,
        agent_service: AgentService,
        mode: str,
        screen_dims: tuple[int, int],
        complexity: str = "complex",
        system_prompt_extension: Optional[str] = None,
    ):
        self.worker_id = worker_id
        self.task_id = task_id
        self.sub_task = sub_task
        self.agent_service = agent_service
        self.mode = mode
        self.screen_width, self.screen_height = screen_dims
        self.complexity = complexity
        self.consecutive_fails = 0
        self.action_count = 0
        self.max_actions = 20 # sub-task limit
        self.system_prompt_extension = system_prompt_extension

    async def _emit(self, event: str, data: Dict[str, Any]):
        data["worker_id"] = self.worker_id
        await self.agent_service._emit(self.task_id, event, data)

    async def run(self, provider: PlannerProvider, history: List[str]) -> bool:
        """Execute the sub-task and return success/failure."""
        self.sub_task.status = TaskStatus.running
        self.sub_task.worker_id = self.worker_id
        
        await self._emit("subtask", {
            "subtask_id": self.sub_task.id,
            "description": self.sub_task.description,
            "status": "running",
        })
        
        await self.agent_service._emit_reasoning(
            self.task_id,
            f"Execution ({self.worker_id})",
            f"Starting worker: {self.sub_task.description}",
            self.sub_task.description,
            elapsed_seconds=0,
        )

        results: List[str] = []
        actions_taken: List[Dict[str, Any]] = []
        is_coding = self.mode == "coding"
        is_computer_use = self.mode == "computer_use"
        is_isolated = self.mode == "computer" and bool(self.agent_service.tools._isolated_hwnd)

        try:
            for action_data in self.sub_task.actions:
                # Opus Audit: Emergency Kill-Switch Check
                if self.agent_service.is_killed(self.task_id):
                    _log.warning(f"Task {self.task_id} KILLED by user. Worker {self.worker_id} stopping.")
                    self.sub_task.status = TaskStatus.failed
                    return False

                if self.action_count >= self.max_actions:
                    _log.warning(f"Worker {self.worker_id} hit action limit.")
                    break

                while self.task_id in self.agent_service._paused_tasks:
                    await asyncio.sleep(0.5)

                self.action_count += 1
                action = Action(**action_data.model_dump())
                decision = self.agent_service.safety.evaluate(action, safe_mode=not is_coding)

                await self._emit("action_start", {
                    "action_id": action.id,
                    "action_type": action.type.value,
                    "explanation": action.explanation,
                    "args_summary": _summarize_args(action.type.value, action.args),
                })

                # Permission & Approval logic (reusing AgentService helpers)
                needed_scope = scope_for_action(action.type.value, action.args)
                if needed_scope and not self.agent_service.permissions.is_granted(self.task_id, needed_scope.value):
                    await self._emit("permission_required", {
                        "action_id": action.id,
                        "scope": needed_scope.value,
                        "reason": f"Action '{action.type.value}' needs '{needed_scope.value}' access.",
                    })
                    granted = await self.agent_service._wait_for_permission(self.task_id, action.id)
                    if not granted:
                        raise RuntimeError(f"Permission denied for {needed_scope.value}")
                    self.agent_service.permissions.grant(self.task_id, needed_scope.value)

                if action.requires_approval or decision.requires_approval:
                    await self._emit("approval_required", {
                        "action_id": action.id,
                        "action": action.model_dump(),
                        "danger": decision.danger.value,
                        "reason": decision.reason,
                    })
                    approved = await self.agent_service._wait_for_approval(self.task_id, action.id)
                    if not approved:
                        raise RuntimeError("Action rejected by user")

                # Tool Execution
                timeout = 300.0 if is_coding else 120.0
                async def _stream_chunk(chunk: Dict[str, Any]):
                    await self._emit("terminal_output", {
                        "command": action.args.get("command", ""),
                        "output": chunk.get("output", ""),
                        "ok": True,
                        "stream": True,
                        "channel": chunk.get("channel", "stdout"),
                        "action_id": action.id,
                    })

                try:
                    res = await asyncio.wait_for(
                        self.agent_service.tools.run_action(action, sw=self.screen_width, sh=self.screen_height, on_stream=_stream_chunk),
                        timeout=timeout
                    )
                except Exception as e:
                    res = ToolResult(ok=False, output=f"Worker Error: {str(e)}")

                results.append(res.output)
                actions_taken.append(action.model_dump())
                self.agent_service.memory.add_action_result(self.task_id, action.id, res.output)
                history.append(f"[{self.worker_id}] Action: {action.type.value} -> {res.output}")

                await self._emit("action_result", {
                    "action_id": action.id,
                    "ok": res.ok,
                    "output": res.output,
                    "action_type": action.type.value,
                    "args_summary": _summarize_args(action.type.value, action.args),
                })

                # Special Mode Handling (Screenshots, File Changes)
                if is_coding:
                    if action.type.value in ("write_file", "text_create", "text_str_replace", "text_insert"):
                        await self._emit("file_change", {
                            "path": action.args.get("path", ""),
                            "action": action.type.value,
                            "content": action.args.get("content", action.args.get("file_text", "")),
                        })
                elif not is_computer_use:
                    if action.type in _SCREENSHOT_ACTIONS or action.type == ActionType.screenshot:
                        if is_isolated:
                            from .providers import _capture_hwnd_screenshot_b64
                            hwnd = self.agent_service.tools._isolated_hwnd
                            screenshot = res.base64_image or _capture_hwnd_screenshot_b64(hwnd)
                            shot_payload: Dict[str, Any] = {"data": screenshot, "isolated": True}
                        else:
                            screenshot = res.base64_image or _capture_screenshot_b64(self.screen_width, self.screen_height)
                            shot_payload = {"data": screenshot}
                            window_rect = _get_active_window_rect(self.screen_width, self.screen_height)
                            if window_rect:
                                shot_payload["window_rect"] = window_rect
                        await self._emit("screenshot", shot_payload)

            # Reflection
            if self.complexity != "atomic":
                reflect_screenshot = None if (is_coding or is_computer_use) else _capture_screenshot_b64(self.screen_width, self.screen_height)
                reflection = await self.agent_service._run_with_phase_updates(
                    self.task_id,
                    f"Worker {self.worker_id} reflecting...",
                    "Reflection",
                    provider.reflect_on_subtask,
                    self.sub_task.description,
                    actions_taken,
                    results,
                    reflect_screenshot,
                    self.mode,
                    system_prompt_extension=self.system_prompt_extension,
                )
                success = reflection.get("success", True)
                reason = reflection.get("reason")
            else:
                # Atomic tasks skip reflection
                success = True
                reason = "Atomic fast-path success"

            
            self.sub_task.status = TaskStatus.done if success else TaskStatus.failed
            self.sub_task.error = reason if not success else None
            
            await self._emit("subtask", {
                "subtask_id": self.sub_task.id,
                "status": "done" if success else "failed",
                "reason": reason,
            })
            
            return success

        except Exception as e:
            self.sub_task.status = TaskStatus.failed
            self.sub_task.error = str(e)
            await self._emit("subtask", {
                "subtask_id": self.sub_task.id,
                "status": "failed",
                "reason": str(e),
            })
            return False


class AgentService:
    def __init__(self, workspace: Path, log_emitter: LogEmitter):
        self.workspace = workspace
        self.log_emitter = log_emitter
        self.memory = MemoryStore(workspace)
        self.safety = SafetyManager()
        self.permissions = PermissionStore()
        self.plugin_registry = PluginRegistry()
        self.plugin_registry.load_defaults()
        self.tools = ToolExecutor(workspace, plugin_registry=self.plugin_registry)
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._paused_tasks: set[str] = set()
        self._approvals: Dict[str, asyncio.Future] = {}
        self._permission_waits: Dict[str, asyncio.Future] = {}
        self._pause_events: Dict[str, asyncio.Event] = {}
        self._on_task_complete: Optional[Callable[[str, str, str], None]] = None
        self._bg_browsers: Dict[str, BackgroundBrowser] = {}
        self._killed_tasks: Set[str] = set()
        self._total_tokens_spent: int = 0
        self._token_budgets: Dict[str, int] = {}
        # Register emergency cleanup so Playwright Chromium processes are killed
        # even if Python crashes or is terminated without running the finally block
        import atexit
        atexit.register(self._sync_emergency_cleanup)

    def kill_task(self, task_id: str):
        """Mark a task for immediate termination."""
        self._killed_tasks.add(task_id)

    def is_killed(self, task_id: str) -> bool:
        """Check if a task has been killed."""
        return task_id in self._killed_tasks

    def update_token_usage(self, tokens: int):
        """Track global token usage."""
        self._total_tokens_spent += tokens

    async def _emit(self, task_id: str, event: str, data: Dict[str, Any]):
        self.log_emitter.emit(task_id, event, data)
        await asyncio.sleep(0)

    async def _emit_reasoning(
        self, task_id: str, stage: str, summary: str, detail: str = "", *, live: bool = False, elapsed_seconds: Optional[int] = None,
    ) -> None:
        payload = {"stage": stage, "summary": summary, "detail": detail, "live": live}
        if elapsed_seconds is not None: payload["elapsed_seconds"] = elapsed_seconds
        await self._emit(task_id, "reasoning", payload)

    async def _run_with_phase_updates(
        self, task_id: str, waiting_message: str, progress_label: str, fn: Callable[..., Any], *args: Any, timeout: float = 180.0, heartbeat_interval: float = 1.0,
    ) -> Any:
        await self._emit(task_id, "status", {"message": waiting_message})
        await self._emit_reasoning(task_id, progress_label, waiting_message, live=True, elapsed_seconds=0)
        work = asyncio.create_task(asyncio.to_thread(fn, *args))
        start = asyncio.get_running_loop().time()
        while not work.done():
            await asyncio.sleep(heartbeat_interval)
            elapsed = int(asyncio.get_running_loop().time() - start)
            if elapsed >= timeout:
                work.cancel()
                raise TimeoutError(f"{progress_label} timed out.")
            await self._emit(task_id, "status", {"message": f"{progress_label}... {elapsed}s", "elapsed_seconds": elapsed, "heartbeat": True})
        return await work

    async def _stream_with_idle_timeout(
        self,
        stream: AsyncIterator[Any],
        *,
        timeout: float,
        stream_name: str,
    ) -> AsyncIterator[Any]:
        """Abort stalled model streams instead of waiting forever for the next item."""
        while True:
            try:
                item = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                try:
                    await stream.aclose()
                except Exception:
                    pass
                raise TimeoutError(f"Timed out waiting for {stream_name} response from model.") from exc
            yield item

    def init_task(self, task_id: str, goal: str, screen_width: int = 1280, screen_height: int = 800, model: str = "claude-3-5-sonnet-20241022", mode: str = "auto", isolated_app: Optional[str] = None, token_budget: int = TOKEN_BUDGET_DEFAULT, active_skills: List[str] = []) -> TaskRecord:
        detected_mode = detect_task_mode(goal, mode if mode != "auto" else None)
        if detected_mode == "computer_isolated" and not isolated_app:
            isolated_app = infer_isolated_app_name(goal)
        context = AgentContext(goal=goal, screen_width=screen_width, screen_height=screen_height, isolated_app=isolated_app, active_skills=active_skills)
        record = TaskRecord(id=task_id, status="running", context=context, goal=goal, model=model, mode=detected_mode)
        self._active_tasks[task_id] = asyncio.create_task(self.run_task(task_id, goal, screen_width, screen_height, model, detected_mode, isolated_app=isolated_app, token_budget=token_budget, active_skills=active_skills))
        return record

    async def _check_token_budget(self, task_id: str, provider: "PlannerProvider", budget: int) -> bool:
        """Emit a warning/abort if the provider has exceeded the token budget. Returns True if over budget."""
        used = provider.total_tokens
        if used >= budget:
            msg = f"Token budget exhausted: {used:,} tokens used (limit {budget:,})."
            _log.warning(msg)
            await self._emit(task_id, "token_budget", {"used": used, "budget": budget, "exhausted": True})
            self._finalize(task_id, "failed", msg)
            return True
        if used >= int(budget * 0.8):
            await self._emit(task_id, "token_budget", {"used": used, "budget": budget, "exhausted": False, "warning": True})
        return False

    async def run_task(self, task_id: str, goal: str, screen_width: int = 1280, screen_height: int = 800, model: str = "claude-3-5-sonnet-20241022", mode: str = "coding", isolated_app: Optional[str] = None, token_budget: int = TOKEN_BUDGET_DEFAULT, active_skills: List[str] = []):
        provider = PlannerProvider(model=model)
        if mode == "computer_isolated" and not isolated_app:
            isolated_app = infer_isolated_app_name(goal)
        # chat/coding both run unified agent — only computer modes need special setup
        is_computer_use = mode == "computer_use"
        is_isolated = mode == "computer_isolated"
        # Only headless browser mode runs truly in background (no real desktop screenshots)
        runs_in_background = is_computer_use
        # Treat chat and coding the same: unified agent, model decides what to do
        is_coding_mode = mode in ("coding", "chat", "auto")
        # Auto-approve actions in any non-safe mode (coding, computer, computer_isolated)
        # safe_mode=True means EVERY bash/write triggers a popup — bad for automation
        is_auto_approve = mode in ("coding", "chat", "auto", "computer", "computer_isolated", "computer_use")

        # Build Skill Instructions
        skill_instructions = ""
        if active_skills:
            skill_instructions = "\n\n### ACTIVE SKILL MANUALS\n"
            for skill_id in active_skills:
                skill = skill_manager.get_skill(skill_id)
                if skill:
                    skill_instructions += f"\n{skill.manual}\n"

        await asyncio.sleep(0.3)
        # Only show the mode init message for computer/browser modes — for chat/coding/auto it's noise
        if not is_coding_mode:
            await self._emit(task_id, "status", {"message": f"Initializing {mode} mode...", "elapsed_seconds": 0})

        try:
            # Setup Browser
            bg_browser: Optional[BackgroundBrowser] = None
            if is_computer_use:
                try:
                    bg_browser = BackgroundBrowser(width=screen_width, height=screen_height, headless=True)
                    await bg_browser.start()
                    self._bg_browsers[task_id] = bg_browser
                    self.tools.set_background_browser(bg_browser)
                    self.tools._background_mode = True
                except Exception as browser_err:
                    raise Exception(f"Failed to start background browser: {str(browser_err)}. Make sure playwright browsers are installed.")
            else:
                self.tools._background_mode = runs_in_background

            # Isolated App Control: find HWND and wire it up for computer mode
            isolated_hwnd: Optional[int] = None
            if mode == "computer_isolated":
                if isolated_app:
                    isolated_hwnd = _get_hwnd_for_title(isolated_app)
                
                if not isolated_hwnd:
                    # Try to grab the current foreground window if it's not a common system/shell window
                    import win32gui
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd and win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd).lower()
                        # Avoid locking to the browser or the dashboard itself
                        if not any(kw in title for kw in ("chrome", "edge", "clean stream", "dash")):
                            isolated_hwnd = hwnd
                            isolated_app = win32gui.GetWindowText(hwnd)

                if isolated_hwnd:
                    self.tools.set_isolated_hwnd(isolated_hwnd, isolated_app)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": True, "isolated_app": isolated_app or "Active Window"})
                    _log.info(f"Isolated mode: HWND {isolated_hwnd} for '{isolated_app}'")
                elif isolated_app:
                    self.tools.set_isolated_hwnd(None, isolated_app)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": True, "isolated_app": isolated_app, "isolated_pending": True})
                    await self._emit(task_id, "status", {"message": f"Waiting to attach isolated control to '{isolated_app}' once it opens."})
                else:
                    _log.warning(f"No suitable target window found for isolated mode. Falling back to full-desktop computer mode.")
                    mode = "computer"  # Fallback to full desktop mode so tools still work
                    is_isolated = False
                    isolated_app = None
                    self.tools.set_isolated_hwnd(None)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": False})
                    await self._emit(task_id, "status", {"message": "⚠️ No target window found for isolated mode — using full desktop instead."})
            else:
                self.tools.set_isolated_hwnd(None)
                is_isolated = False
                await self._emit(task_id, "mode", {"mode": mode, "isolated": False})

            # Classify task complexity
            complexity = classify_task_complexity(goal)
            if is_isolated:
                # Isolated mode: always use the fast path to avoid LLM planning latency
                complexity = "atomic"

            # Planning
            _goal_needs_tree = any(kw in goal.lower() for kw in ("file", "directory", "project", "folder"))
            env_context = ""
            if is_coding_mode:
                if complexity == "atomic":
                    env_context = f"\n\nWorkspace directory: {self.workspace.absolute()}"
                else:
                    env_res = self.tools.system_info()
                    env_context = f"\n\nSystem environment:\n{env_res.output}"
                    if _goal_needs_tree:
                        env_context += _workspace_tree(self.workspace, depth=2)

            
            # No screenshots for coding/chat — only for computer/desktop modes
            if runs_in_background or is_coding_mode:
                screenshot_b64 = None
            elif isolated_hwnd:
                # Isolated mode: crop to just the target window so model isn't distracted
                from .providers import _capture_hwnd_screenshot_b64
                screenshot_b64 = _capture_hwnd_screenshot_b64(isolated_hwnd)
            else:
                screenshot_b64 = _capture_screenshot_b64(screen_width, screen_height)

            memories = self.memory.search(goal, limit=5)
            mem_context = "\n".join(f"- {m.content}" for m in memories) if memories else None

            if screenshot_b64:
                from .providers import _get_active_window_rect
                await self._emit(task_id, "screenshot", {
                    "data": screenshot_b64,
                    "window_rect": _get_active_window_rect(screen_width, screen_height) if is_isolated else None,
                    "isolated": bool(isolated_hwnd),
                    "worker_id": "planner"
                })

            if True: # Always use Streaming ReAct loop for speed & reliability
                from .providers import get_tool_guidance, get_mode_packs
                from .tool_registry import get_tool_schemas
                packs = get_mode_packs(mode)
                tool_guidance = get_tool_guidance(packs)
                tool_schemas = get_tool_schemas(packs)

                # ── Mode-specific system prompts ──────────────────────────────
                _is_computer_desktop = mode in ("computer", "computer_isolated")
                _is_browser_use = mode == "computer_use"

                if _is_browser_use:
                    system = (
                        "You are AI Computer — a headless browser automation agent.\n"
                        "You control a real Playwright browser running in the background. There is NO visible desktop.\n\n"
                        "BROWSER WORKFLOW:\n"
                        "1. Use browser_navigate to go to a URL.\n"
                        "2. Use browser_read_page to read the page content and find elements.\n"
                        "3. Use browser_click, browser_type, browser_scroll to interact with the page.\n"
                        "4. Use browser_snapshot for accessibility tree when you need element references.\n"
                        "5. Use web_search if you need to find a URL first.\n"
                        "6. When you have the information requested, call finish with a clear summary.\n\n"
                        "RULES:\n"
                        "- NEVER use bash, write_file, or desktop tools — you only have browser access.\n"
                        "- NEVER call finish without actually visiting the page and retrieving the data.\n"
                        "- If a page doesn't load or has no useful data, try scrolling or navigating to a subpage.\n"
                        "- Always read the page after navigating before concluding anything.\n"
                        f"\nAvailable tools:\n{tool_guidance}\n"
                    )
                    xml_system = (
                        "You are AI Computer — a headless browser automation agent.\n"
                        "FORMAT: <thought>reasoning</thought> then <action type=\"tool\">{args}</action>\n\n"
                        "WORKFLOW: browser_navigate → browser_read_page → interact → browser_read_page → finish\n"
                        "NEVER use bash or file tools. NEVER call finish without visiting the page first.\n\n"
                        f"Available tools:\n{tool_guidance}\n\n"
                        "After each <observation>, decide your next step. Call finish with a clear answer when done."
                    )
                elif _is_computer_desktop:
                    system = (
                        "You are AI Computer — a desktop automation agent controlling a real Windows PC.\n"
                        "A screenshot of the current desktop is attached. Use it to understand the current state.\n\n"
                        "DESKTOP CONTROL WORKFLOW:\n"
                        "1. Look at the screenshot to understand the current state of the desktop.\n"
                        "2. Use bash to open applications (e.g. `start notepad`, `start calc`, `start ms-paint:`). Do NOT try to double-click desktop icons.\n"
                        "3. After opening an app, IMMEDIATELY call focus_window with the app name (e.g. focus_window {\"title\": \"Notepad\"}) to bring it to the foreground. This is MANDATORY before typing.\n"
                        "4. After focus_window succeeds, type your text with keyboard_type.\n"
                        "5. After every mouse_click or keyboard_type, take a screenshot to verify the result.\n"
                        "6. Use key_combo for shortcuts (e.g. ctrl+s to save, ctrl+w to close a tab).\n"
                        "7. SAVING FILES: When a Save-As dialog opens, type the FULL path (e.g. C:\\Users\\<username>\\Desktop\\filename.txt) to save to a specific folder. Do NOT type just the filename — it will save to the wrong folder.\n"
                        "   To get the username, run: bash {\"command\": \"echo %USERNAME%\"} before saving.\n"
                        "8. When done, call finish with a summary of what was accomplished.\n\n"
                        "CRITICAL SAFETY RULES:\n"
                        "- NEVER close, minimize, or interact with Google Chrome or Microsoft Edge — those are the monitoring dashboard. Any Alt+F4, Ctrl+W, or clicks on the browser X button are FORBIDDEN.\n"
                        "- NEVER send Alt+F4 unless explicitly asked to close a specific non-browser app.\n"
                        "- NEVER click outside the target app window — confirm target window is in focus first.\n"
                        "- If an action fails or the screenshot shows an unexpected state, re-evaluate and try a different approach.\n\n"
                        "EFFICIENCY:\n"
                        "- Don't take screenshots you don't need (once after open, once after action is enough).\n"
                        "- Don't repeat failed actions — if a click didn't work, try a different coordinate or approach.\n"
                    )
                    xml_system = (
                        "You are AI Computer — a desktop automation agent controlling a real Windows PC.\n"
                        "FORMAT: <thought>reasoning</thought> then <action type=\"tool\">{args}</action>\n\n"
                        "WORKFLOW: screenshot → bash to open app → focus_window to bring it forward → keyboard_type text → screenshot to verify → finish\n"
                        "SAFETY: NEVER close Chrome or Edge (monitoring dashboard). NEVER send Alt+F4 to the browser.\n\n"
                        f"Available tools:\n{tool_guidance}\n\n"
                        "After each <observation>, decide next step. Always take a screenshot after opening an app or after clicking."
                    )
                else:
                    system = (
                        "You are AI Computer — an intelligent assistant and coding agent.\n"
                        "You can have natural conversations AND take real actions using tools.\n\n"
                        "ENVIRONMENT: Windows 11. The shell is CMD/PowerShell — NOT bash/zsh.\n"
                        "- Use 'python' not 'python3'. Use 'dir' not 'ls'. Use 'type' not 'cat'.\n"
                        "- 'head', 'grep', 'tail', 'which' do NOT exist. Use Python one-liners or PowerShell instead.\n"
                        "- Path separator is backslash. Use forward slashes in Python code only.\n\n"
                        "HOW TO RESPOND:\n"
                        "- Questions, greetings, explanations → just reply conversationally. No tools needed.\n"
                        "- Tasks (write code, edit files, run commands, search, fix bugs) → use your tools.\n"
                        "- YOU decide when tools are needed. Never use a tool just for the sake of it.\n"
                        "- When you're done with a task or have answered a question, call finish.\n\n"
                        "EFFICIENCY RULES (strictly follow these to avoid wasted steps):\n"
                        "- NEVER call the same tool with the same arguments twice — use the result you already have.\n"
                        "- NEVER read_file on a file you just wrote — you already know its contents.\n"
                        "- NEVER use list_directory just to confirm a file you just wrote exists.\n"
                        "- NEVER use web_fetch on a URL you already fetched — use the content already returned.\n"
                        "- NEVER use mcp_tool unless you know the exact server name from a prior list_mcp_servers call.\n"
                        "- After getting data you need, synthesize it and call finish — don't loop.\n\n"
                        "WHEN WRITING/EDITING CODE:\n"
                        "- Read files before editing. Use text_str_replace for targeted edits.\n"
                        "- After edits: run lint_code. After features: run run_tests.\n"
                        "- Use git to commit working changes. Use find_symbol to locate definitions.\n"
                        "- Use bash for shell needs (pip install, build commands, running scripts).\n"
                        "- Use relative paths. Fix failures before calling finish.\n"
                    )
                    xml_system = (
                        "You are AI Computer — an intelligent assistant and coding agent.\n"
                        "You can have natural conversations AND take real actions.\n\n"
                        "FORMAT:\n"
                        "- To think: <thought>your reasoning</thought>\n"
                        "- To act: <action type=\"tool_name\">{\"arg\": \"value\"}</action>\n"
                        "- To just reply (chat/explain): write your response normally, then <action type=\"finish\">{\"reason\": \"done\"}</action>\n\n"
                        "WHEN TO USE TOOLS: only when you actually need to do something (create files, run code, search, etc.).\n"
                        "For questions and conversation, just answer — then finish.\n\n"
                        "EFFICIENCY: Never call the same tool twice with the same args. Never read_file a file you just wrote. Never re-fetch a URL. After getting what you need, call finish.\n\n"
                        f"Available tools:\n{tool_guidance}\n\n"
                        "After each <observation>, decide your next step. Call finish when done."
                    )
                if skill_instructions:
                    system += f"\n\n{skill_instructions}"
                    xml_system += f"\n\n{skill_instructions}"

                # ── Auto-inject workspace tree when workspace has files ────────
                auto_context = ""
                try:
                    tree = self._workspace_tree(self.tools.workspace, max_depth=3)
                    if tree and tree.strip():
                        auto_context = f"\n\nWorkspace:\n{tree}"
                except Exception:
                    pass

                win_info = f"\nTarget window: {isolated_app}" if is_isolated else ""
                messages = [{"role": "user", "content": f"{goal}{env_context}{auto_context}{win_info}"}]
                use_native_tools = len(tool_schemas) > 0 and hasattr(provider, "stream_chat_with_tools")
                
                # ── Anti-waste tracking: detect duplicate calls and cache writes ──
                _recent_calls: list[tuple[str, str]] = []  # (action_type, args_key) last 3 calls
                _write_cache: dict[str, str] = {}  # path → content of recently written files
                xml_fallback_steps = 0

                for step in range(25): # Max steps
                    if self.is_killed(task_id) or task_id in self._paused_tasks:
                        break

                    step_start = asyncio.get_event_loop().time()
                    await self._emit_reasoning(task_id, f"Step {step+1}", "Thinking...", live=True, elapsed_seconds=0)

                    action_type = None
                    args = {}
                    thought_text = ""
                    tool_call_id = None

                    def _step_elapsed() -> int:
                        return int(asyncio.get_event_loop().time() - step_start)

                    # ── Refresh screenshot each step for computer/desktop mode ──
                    # The model needs to see the CURRENT state, not a stale snapshot
                    if _is_computer_desktop and step > 0:
                        isolated_hwnd = self.tools.resolve_isolated_hwnd() if is_isolated else None
                        if isolated_hwnd:
                            # Isolated mode: crop to just the target window
                            from .providers import _capture_hwnd_screenshot_b64
                            screenshot_b64 = _capture_hwnd_screenshot_b64(isolated_hwnd)
                        else:
                            # Full desktop
                            screenshot_b64 = _capture_screenshot_b64(screen_width, screen_height)

                    # ── History compression: truncate old observation results ──
                    if len(messages) > 6:
                        for i in range(1, len(messages) - 4):
                            m = messages[i]
                            if m["role"] == "tool" and len(m.get("content", "")) > 500:
                                messages[i] = {**m, "content": m["content"][:500] + "\n...(truncated)"}
                            elif m["role"] == "user" and "<observation>" in m.get("content", "") and len(m["content"]) > 500:
                                messages[i] = {**m, "content": m["content"][:500] + "\n...(truncated)</observation>"}

                    try:
                        # ── TRY NATIVE TOOL CALLING FIRST ──
                        if use_native_tools:
                            try:
                                native_stream = self._stream_with_idle_timeout(
                                    provider.stream_chat_with_tools(
                                        system,
                                        messages,
                                        tool_schemas,
                                        screenshot_b64 if not runs_in_background else None,
                                    ),
                                    timeout=MODEL_STREAM_IDLE_TIMEOUT_SECONDS,
                                    stream_name="native tool",
                                )
                                async for event in native_stream:
                                    if event["type"] == "thought":
                                        thought_text += event["content"]
                                        await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": "Thinking...", "detail": thought_text, "live": True, "elapsed_seconds": _step_elapsed()})
                                    elif event["type"] == "tool_call":
                                        action_type = event["name"]
                                        args = event.get("args", {})
                                        thought_text = event.get("thought", thought_text)
                                        tool_call_id = event.get("id", f"call-{step}")
                                        # Always emit a finalized reasoning card to show real elapsed time
                                        await self._emit(task_id, "reasoning", {
                                            "stage": f"Step {step+1}",
                                            "summary": (thought_text[:50] + "...") if thought_text else f"→ {action_type}",
                                            "detail": thought_text,
                                            "live": False,
                                            "elapsed_seconds": _step_elapsed(),
                                        })
                                    elif event["type"] == "text_only":
                                        thought_text = event.get("content", "")
                                        if thought_text:
                                            await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": thought_text[:50]+"...", "detail": thought_text, "live": False, "elapsed_seconds": _step_elapsed()})
                            except Exception as e:
                                _log.warning(f"Native tool calling failed, falling back to XML: {e}")
                                use_native_tools = False
                                await self._emit(task_id, "status", {"message": f"Native tool stream stalled or failed; falling back to XML. ({e})"})
                        
                        # ── XML FALLBACK ──
                        if not use_native_tools and action_type is None:
                            xml_fallback_steps += 1
                            if xml_fallback_steps > XML_FALLBACK_MAX_STEPS:
                                raise TimeoutError("XML fallback exhausted its max recovery steps.")
                            buffer = ""
                            thought_text = ""
                            in_action = False
                            action_args_json = ""
                            
                            stream_gen = self._stream_with_idle_timeout(
                                provider.stream_chat(
                                    xml_system,
                                    messages,
                                    screenshot_b64 if not runs_in_background else None,
                                ),
                                timeout=MODEL_STREAM_IDLE_TIMEOUT_SECONDS,
                                stream_name="XML",
                            )
                            import re
                            async for chunk in stream_gen:
                                buffer += chunk
                                if not in_action:
                                    # Stream text before <action> tag regardless of <thought> wrapping
                                    if "<thought>" in buffer:
                                        # Extract content inside <thought> if the model uses it
                                        if "</thought>" not in buffer:
                                            thought_text = buffer.split("<thought>")[1]
                                        else:
                                            thought_text = buffer.split("<thought>")[1].split("</thought>")[0]
                                    else:
                                        # No <thought> wrapper — stream all pre-action text (e.g. nemotron)
                                        thought_text = re.sub(r'<action.*', '', buffer, flags=re.DOTALL).strip()
                                    if thought_text:
                                        await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": "Thinking...", "detail": thought_text, "live": True, "elapsed_seconds": _step_elapsed()})
                                if "</thought>" in buffer and not in_action:
                                    thought_text = buffer.split("<thought>")[1].split("</thought>")[0]
                                    await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": thought_text[:50]+"...", "detail": thought_text, "live": False, "elapsed_seconds": _step_elapsed()})
                                if "<action" in buffer and not in_action:
                                    in_action = True
                                    match = re.search(r'<action\s+type="([^"]+)">', buffer)
                                    if match:
                                        action_type = match.group(1)
                                if in_action and "</action>" in buffer:
                                    action_start = buffer.find('>', buffer.find('<action')) + 1
                                    action_end = buffer.find('</action>')
                                    if action_start > 0 and action_end > action_start:
                                        action_args_json = buffer[action_start:action_end].strip()
                                    break
                            
                            if action_type and action_args_json:
                                try:
                                    args = json.loads(action_args_json)
                                except Exception:
                                    from .providers import _sanitize_json_text
                                    try:
                                        args = json.loads(_sanitize_json_text(action_args_json))
                                    except Exception:
                                        args = {}
                                        _log.warning(f"Failed to parse action args JSON for '{action_type}': {action_args_json!r}")
                            elif action_type and not action_args_json:
                                _log.warning(f"Action '{action_type}' had no args between tags; executing with empty args")
                    except Exception as e:
                        _log.error(f"Streaming failed: {e}")
                        self._finalize(task_id, "failed", f"Streaming failed: {e}")
                        return
                    
                    if not action_type:
                        # Model gave a text-only response — that IS the answer.
                        # Emit as a finalized response card only (no duplicate live card).
                        if thought_text and thought_text.strip():
                            # Only emit the final card; the live card was already emitted during streaming
                            await self._emit(task_id, "reasoning", {
                                "stage": "Response", "summary": thought_text[:80],
                                "detail": thought_text, "live": False,
                                "elapsed_seconds": _step_elapsed(), "is_reply": True
                            })
                            self._finalize(task_id, "done", thought_text)
                            await self._emit(task_id, "done", {
                                "complete": True, "reason": thought_text, "is_reply": True,
                                "finished_at": datetime.now(timezone.utc).isoformat()
                            })
                        else:
                            self._finalize(task_id, "done", "Done.")
                            await self._emit(task_id, "done", {"complete": True, "reason": "Done.", "finished_at": datetime.now(timezone.utc).isoformat()})
                        return
                        
                    # Execute action
                    from .models import Action, ActionType as AT, ToolResult

                    # ── Anti-waste: detect exact duplicate consecutive calls ──
                    _call_key = (action_type, json.dumps(args, sort_keys=True))
                    if _call_key in _recent_calls and action_type not in ("finish", "bash", "run_tests"):
                        _cached_obs = f"[Duplicate call skipped] You just called {action_type} with the same arguments. The result was already provided above — use it to proceed."
                        if use_native_tools and tool_call_id:
                            messages.append({"role": "assistant", "content": thought_text, "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": action_type, "arguments": json.dumps(args)}}]})
                            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": _cached_obs})
                        else:
                            messages.append({"role": "assistant", "content": f"<thought>{thought_text}</thought>\n<action type=\"{action_type}\">\n{json.dumps(args)}\n</action>"})
                            messages.append({"role": "user", "content": f"<observation>\n{_cached_obs}\n</observation>"})
                        continue

                    # ── Anti-waste: read-after-write cache ──
                    if action_type == "read_file":
                        _rpath = args.get("path", "")
                        if _rpath in _write_cache:
                            _cached_content = _write_cache[_rpath]
                            _cached_obs = f"[Cached from recent write]\n{_cached_content}"
                            if use_native_tools and tool_call_id:
                                messages.append({"role": "assistant", "content": thought_text, "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": action_type, "arguments": json.dumps(args)}}]})
                                messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": _cached_obs})
                            else:
                                messages.append({"role": "assistant", "content": f"<thought>{thought_text}</thought>\n<action type=\"{action_type}\">\n{json.dumps(args)}\n</action>"})
                                messages.append({"role": "user", "content": f"<observation>\n{_cached_obs}\n</observation>"})
                            continue

                    # Update recent calls ring buffer (last 5)
                    _recent_calls.append(_call_key)
                    if len(_recent_calls) > 5:
                        _recent_calls.pop(0)

                    try:
                        act = Action(id=f"act-{step}", type=AT(action_type), args=args, explanation=thought_text)
                    except ValueError:
                        messages.append({"role": "assistant", "content": thought_text})
                        messages.append({"role": "user", "content": f"Invalid action type: {action_type}. Use only the provided tools."})
                        continue

                    # Approval handling
                    decision = self.safety.evaluate(act, safe_mode=not is_auto_approve)
                    if act.requires_approval or decision.requires_approval:
                        await self._emit(task_id, "approval_required", {
                            "action_id": act.id,
                            "action": act.model_dump(),
                            "danger": decision.danger.value,
                            "reason": decision.reason,
                        })
                        approved = await self._wait_for_approval(task_id, act.id)
                        if not approved:
                            self._finalize(task_id, "cancelled", "Action rejected by user.")
                            return

                    await self._emit(task_id, "action_start", {
                        "action_id": act.id,
                        "action_type": act.type.value,
                        "explanation": act.explanation,
                        "args_summary": str(args)[:80],
                    })
                    
                    try:
                        async def _stream_chunk(c: Dict[str, Any]):
                            await self._emit(task_id, "terminal_output", {
                                "command": act.args.get("command", ""),
                                "output": c.get("output", ""),
                                "ok": True, "stream": True,
                                "channel": c.get("channel", "stdout"),
                                "action_id": act.id,
                            })
                        res = await asyncio.wait_for(
                            self.tools.run_action(act, sw=screen_width, sh=screen_height, on_stream=_stream_chunk),
                            timeout=120.0
                        )
                    except Exception as e:
                        res = ToolResult(ok=False, output=f"Error: {str(e)}")
                        
                    await self._emit(task_id, "action_result", {
                        "action_id": act.id,
                        "ok": res.ok,
                        "output": res.output,
                        "action_type": act.type.value,
                        "args_summary": str(args)[:80],
                    })
                    
                    # ── Populate write cache so subsequent reads are free ──
                    if act.type == AT.write_file and res.ok:
                        _write_path = args.get("path", "")
                        _write_content = args.get("content", "")
                        if _write_path:
                            _write_cache[_write_path] = _write_content

                    # ── Auto-screenshot after computer actions so model sees result ──
                    _needs_screenshot = act.type in _SCREENSHOT_ACTIONS or (
                        _is_computer_desktop and act.type == AT.bash and res.ok
                    )
                    if _is_computer_desktop and _needs_screenshot:
                        await asyncio.sleep(0.4)  # brief settle time for UI to render
                        isolated_hwnd = self.tools.resolve_isolated_hwnd() if is_isolated else None
                        if isolated_hwnd:
                            from .providers import _capture_hwnd_screenshot_b64
                            post_shot = _capture_hwnd_screenshot_b64(isolated_hwnd)
                        else:
                            post_shot = _capture_screenshot_b64(screen_width, screen_height)
                        if post_shot:
                            await self._emit(task_id, "screenshot", {"data": post_shot, "isolated": bool(isolated_hwnd), "worker_id": "planner"})
                            screenshot_b64 = post_shot  # use for next step too

                    # ── Append result to conversation ──
                    # Coding mode needs more context (test failures, file contents, lint output)
                    obs_limit = 6000 if is_coding_mode else 2000
                    obs_text = res.output[:obs_limit] + ("\n...(truncated)" if len(res.output) > obs_limit else "")
                    if use_native_tools and tool_call_id:
                        messages.append({"role": "assistant", "content": thought_text, "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": action_type, "arguments": json.dumps(args)}}]})
                        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": obs_text})
                    else:
                        messages.append({"role": "assistant", "content": f"<thought>{thought_text}</thought>\n<action type=\"{action_type}\">\n{json.dumps(args)}\n</action>"})
                        messages.append({"role": "user", "content": f"<observation>\n{obs_text}\n</observation>"})
                    
                    # Update budget
                    provider._total_input_tokens += len(messages[-1].get("content", "").split()) * 1.3
                    provider._total_output_tokens += len(thought_text.split()) * 1.3
                    if await self._check_token_budget(task_id, provider, token_budget):
                        return
                    
                    if act.type == AT.finish:
                        self._finalize(task_id, "done", res.output)
                        await self._emit(task_id, "done", {"complete": True, "reason": res.output, "finished_at": datetime.now(timezone.utc).isoformat()})
                        self.memory.add("task_outcome", f"Goal: {goal} | Outcome: True | Reason: {res.output}")
                        return

                # If we loop 25 times and don't finish
                self._finalize(task_id, "failed", "Max steps reached without finish action.")
                await self._emit(task_id, "done", {"complete": False, "reason": "Max steps reached.", "finished_at": datetime.now(timezone.utc).isoformat()})
                self.memory.add("task_outcome", f"Goal: {goal} | Outcome: False | Reason: Max steps reached")

        except Exception as e:
            _log.exception("Task Execution Failed")
            await self._emit(task_id, "error", {"message": str(e)})
            self._finalize(task_id, "failed", str(e))
        finally:
            self._active_tasks.pop(task_id, None)
            # Remove task from killed-set so it doesn't grow unboundedly
            self._killed_tasks.discard(task_id)
            # Clean up browser (Playwright Chromium) — critical: zombie Chromium
            # processes survive Python crashes and hold gigabytes of RAM
            browser = self._bg_browsers.pop(task_id, None)
            if browser:
                try:
                    await browser.stop()
                except Exception:
                    pass
            # Force a GC cycle so freed screenshot buffers and message history
            # are collected immediately instead of accumulating across tasks
            import gc
            gc.collect()


    def _finalize(self, task_id: str, status: str, reason: str = ""):
        if self._on_task_complete: self._on_task_complete(task_id, status, reason)

    async def _wait_for_approval(self, task_id: str, action_id: str) -> bool:
        fut = self._approvals.setdefault(f"{task_id}:{action_id}", asyncio.Future())
        try: return await fut
        finally: self._approvals.pop(f"{task_id}:{action_id}", None)

    def submit_approval(self, task_id: str, action_id: str, approved: bool):
        fut = self._approvals.get(f"{task_id}:{action_id}")
        if fut: fut.set_result(approved)

    async def _wait_for_permission(self, task_id: str, action_id: str) -> bool:
        fut = self._permission_waits.setdefault(f"{task_id}:{action_id}", asyncio.Future())
        try: return await fut
        finally: self._permission_waits.pop(f"{task_id}:{action_id}", None)

    def submit_permission(self, task_id: str, action_id: str, granted: bool):
        fut = self._permission_waits.get(f"{task_id}:{action_id}")
        if fut: fut.set_result(granted)

    def pause_task(self, task_id: str):
        self._paused_tasks.add(task_id)

    def resume_task(self, task_id: str):
        self._paused_tasks.discard(task_id)

    def cancel_task(self, task_id: str) -> bool:
        if task_id in self._active_tasks:
            self._active_tasks[task_id].cancel()
            return True
        return False

    async def shutdown(self):
        for b in list(self._bg_browsers.values()):
            try:
                await b.stop()
            except Exception:
                pass
        self._bg_browsers.clear()

    def _sync_emergency_cleanup(self) -> None:
        """Synchronous atexit handler: kill any live Playwright browsers.

        When Python is killed (crash, OOM, Ctrl-C) the async finally blocks
        may not run.  Chromium child processes then survive as zombies and hold
        gigabytes of RAM until the machine is rebooted.  This handler runs
        synchronously at interpreter shutdown and force-stops them.
        """
        if not self._bg_browsers:
            return
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.shutdown())
            loop.close()
        except Exception:
            # Last resort: kill the Playwright subprocess directly via psutil
            try:
                import psutil
                current = psutil.Process()
                for child in current.children(recursive=True):
                    if "chrom" in child.name().lower():
                        child.kill()
            except Exception:
                pass

def _summarize_args(action_type: str, args: dict) -> str:
    if action_type in ("run_command", "bash"): return (args.get("command") or "")[:80]
    if action_type == "text_editor": return f"{args.get('command','')} {args.get('path','')}"
    if action_type in ("read_file", "write_file", "move_file"): return args.get("path") or args.get("src") or ""
    return ""

_SKIP_DIRS = frozenset({'__pycache__', 'node_modules', '.gemini', '.claude', '.git', 'venv', '.venv', 'dist', 'build', '.tempmediaStorage'})

def _workspace_tree(root: Path, depth: int = 2) -> str:
    if not root.exists():
        return ""
    lines = [f"\n\nWorkspace layout ({root}):"]
    try:
        def _walk(directory: Path, current_depth: int):
            if current_depth > depth:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                return
            for entry in entries:
                if entry.name.startswith('.') or entry.name in _SKIP_DIRS:
                    continue
                indent = "  " * (current_depth - 1)
                lines.append(f"{indent}{'/' if entry.is_dir() else ''}{entry.name}")
                if entry.is_dir():
                    _walk(entry, current_depth + 1)
        _walk(root, 1)
    except Exception as e:
        _log.warning(f"Failed to generate workspace tree: {e}")
    return "\n".join(lines)

