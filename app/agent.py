from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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
from .providers import PlannerProvider, _capture_screenshot_b64, _get_active_window_rect, _get_hwnd_for_title, detect_task_mode, classify_task_complexity
from .safety import SafetyManager
from .text_editor import TextEditorTool
from .tools import ToolExecutor
from .plugins import PluginRegistry
from .skills import skill_manager

_log = logging.getLogger("agent")

TOKEN_BUDGET_DEFAULT = 100_000  # max combined input+output tokens per task

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

    def init_task(self, task_id: str, goal: str, screen_width: int = 1280, screen_height: int = 800, model: str = "claude-3-5-sonnet-20241022", mode: str = "auto", isolated_app: Optional[str] = None, token_budget: int = TOKEN_BUDGET_DEFAULT, active_skills: List[str] = []) -> TaskRecord:
        detected_mode = detect_task_mode(goal, mode if mode != "auto" else None)
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
        is_coding = mode == "coding"
        is_computer_use = mode == "computer_use"
        is_isolated = mode == "computer_isolated"
        runs_in_background = is_coding or is_computer_use or is_isolated

        # Build Skill Instructions
        skill_instructions = ""
        if active_skills:
            skill_instructions = "\n\n### ACTIVE SKILL MANUALS\n"
            for skill_id in active_skills:
                skill = skill_manager.get_skill(skill_id)
                if skill:
                    skill_instructions += f"\n{skill.manual}\n"
        
        await asyncio.sleep(0.3)
        await self._emit(task_id, "status", {"message": f"Initializing {mode} mode..."})

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
                    self.tools.set_isolated_hwnd(isolated_hwnd)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": True, "isolated_app": isolated_app or "Active Window"})
                    _log.info(f"Isolated mode: HWND {isolated_hwnd} for '{isolated_app}'")
                else:
                    _log.warning(f"No suitable target window found for isolated mode.")
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": False})
            else:
                self.tools.set_isolated_hwnd(None)
                await self._emit(task_id, "mode", {"mode": mode, "isolated": False})

            # Classify task complexity
            complexity = classify_task_complexity(goal)
            if is_isolated:
                # Isolated mode: always use the fast path to avoid LLM planning latency
                complexity = "atomic"

            # Planning
            _goal_needs_tree = any(kw in goal.lower() for kw in ("file", "directory", "project", "folder"))
            env_context = ""
            if is_coding:
                if complexity == "atomic":
                    env_context = f"\n\nWorkspace directory: {self.workspace.absolute()}"
                else:
                    env_res = self.tools.system_info()
                    env_context = f"\n\nSystem environment:\n{env_res.output}"
                    if _goal_needs_tree:
                        env_context += _workspace_tree(self.workspace, depth=2)

            
            screenshot_b64 = None if runs_in_background else _capture_screenshot_b64(screen_width, screen_height)
            memories = self.memory.search(goal, limit=5)
            mem_context = "\n".join(f"- {m.content}" for m in memories) if memories else None

            if screenshot_b64:
                from .providers import _get_active_window_rect
                await self._emit(task_id, "screenshot", {
                    "data": screenshot_b64,
                    "window_rect": _get_active_window_rect() if is_isolated else None,
                    "isolated": is_isolated,
                    "worker_id": "planner"
                })

            if True: # Always use Streaming ReAct loop for speed & reliability
                from .providers import get_tool_guidance, get_mode_packs
                packs = get_mode_packs(mode)
                tool_guidance = get_tool_guidance(packs)
                
                system = (
                    "You are a fast, autonomous computer agent.\n"
                    "You must interact with the system using XML tags.\n"
                    "Loop:\n"
                    "1. Output a <thought> block explaining your reasoning.\n"
                    "2. Output an <action type=\"...\"> block with <args> encoded as JSON.\n\n"
                    "Available actions:\n"
                    f"{tool_guidance}\n\n"
                    "You can also delegate sub-tasks to another model using:\n"
                    "<delegate model=\"gpt-4o-mini\">\n"
                    "  <thought>Need to summarize...</thought>\n"
                    "  <task>Read file X and summarize</task>\n"
                    "</delegate>\n\n"
                    "Example:\n"
                    "<thought>I need to read the file.</thought>\n"
                    "<action type=\"read_file\">\n"
                    "  {\"path\": \"main.py\"}\n"
                    "</action>\n\n"
                    "Wait for the system to reply with <observation> before continuing. If you are done, use the 'finish' action type."
                )
                if skill_instructions:
                    system += f"\n\n{skill_instructions}"
                
                win_info = f"\nTarget window locked: {isolated_app}" if is_isolated else ""
                messages = [{"role": "user", "content": f"Goal: {goal}{env_context}{win_info}"}]
                
                # We emit a single pseudo-plan for the UI so the frontend knows we started
                from .models import HierarchicalPlan, SubTask
                await self._emit(task_id, "plan", {
                    "reasoning": "Streaming Codex Loop Initiated",
                    "sub_tasks": [{"id": "react-loop", "description": "Autonomous Execution", "actions": []}],
                    "overall_complete": False,
                    "execution_mode": "serial",
                    "max_parallel_workers": 1
                })
                await self._emit(task_id, "subtask", {"subtask_id": "react-loop", "description": "Autonomous Execution", "status": "running"})
                
                for step in range(25): # Max steps
                    if self.is_killed(task_id) or task_id in self._paused_tasks:
                        break
                        
                    await self._emit_reasoning(task_id, f"Step {step+1}", "Thinking...", live=True, elapsed_seconds=0)
                    
                    try:
                        if "magic_test_plan" in goal:
                            if not hasattr(self, "_mock_step"): self._mock_step = 0
                            self._mock_step += 1
                            async def mock_plan_stream():
                                if self._mock_step == 1:
                                    yield "<thought>I will plan this out.</thought>\n<action type=\"plan\">\n{\"reasoning\": \"I need to do 3 things\", \"sub_tasks\": [{\"id\": \"t1\", \"description\": \"Step 1\", \"dependencies\": []}, {\"id\": \"t2\", \"description\": \"Step 2\", \"dependencies\": [\"t1\"]}, {\"id\": \"t3\", \"description\": \"Step 3\", \"dependencies\": [\"t2\"]}]}\n</action>\n"
                                else:
                                    yield "<action type=\"finish\">{\"reason\": \"Plan complete.\"}</action>"
                            stream_gen = mock_plan_stream()
                        elif "magic_test_delegation_goal" in goal:
                            if not hasattr(self, "_mock_step"): self._mock_step = 0
                            self._mock_step += 1
                            async def mock_stream():
                                if self._mock_step == 1:
                                    yield "<thought>I will delegate now.</thought>\n<delegate model=\"gpt-4o-mini\">\n<thought>Help me</thought>\n<task>Write a haiku</task>\n</delegate>\n"
                                else:
                                    yield "<action type=\"finish\">{\"reason\": \"Done delegating\"}</action>"
                            stream_gen = mock_stream()
                        else:
                            stream_gen = provider.stream_chat(system, messages, screenshot_b64 if not runs_in_background else None)
                    except Exception as e:
                        _log.error(f"Streaming failed: {e}. Ensure API keys are set for OpenRouter/OpenAI/Groq.")
                        self._finalize(task_id, "failed", f"Streaming failed: {e}")
                        return
                        
                    buffer = ""
                    thought_text = ""
                    action_type = None
                    action_args_json = ""
                    in_thought = False
                    in_action = False
                    
                    in_delegate = False
                    delegate_model = None
                    delegate_task = ""
                    
                    import re
                    async for chunk in stream_gen:
                        buffer += chunk
                        
                        # Streaming UI updates
                        if "<thought>" in buffer and "</thought>" not in buffer:
                            thought_text = buffer.split("<thought>")[1]
                            await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": "Thinking...", "detail": thought_text, "live": True})
                            
                        if "</thought>" in buffer and not in_action:
                            thought_text = buffer.split("<thought>")[1].split("</thought>")[0]
                            await self._emit(task_id, "reasoning", {"stage": f"Step {step+1}", "summary": thought_text[:50]+"...", "detail": thought_text, "live": False})
                            
                        # Detect action
                        if "<action" in buffer and not in_action:
                            in_action = True
                            import re
                            match = re.search(r'<action\s+type="([^"]+)">', buffer)
                            if match:
                                action_type = match.group(1)
                                
                        if in_action and "</action>" in buffer:
                            parts = buffer.split("</action>")[0].split(">")
                            action_args_json = parts[-1].strip()
                            break
                            
                        # Detect delegate
                        if "<delegate" in buffer and not in_delegate and not in_action:
                            in_delegate = True
                            match = re.search(r'<delegate\s+model="([^"]+)">', buffer)
                            if match:
                                delegate_model = match.group(1)

                        if in_delegate and "</delegate>" in buffer:
                            parts = buffer.split("</delegate>")[0]
                            task_match = re.search(r'<task>(.*?)</task>', parts, re.DOTALL)
                            delegate_task = task_match.group(1).strip() if task_match else ""
                            thought_match = re.search(r'<thought>(.*?)</thought>', parts, re.DOTALL)
                            thought_text = thought_match.group(1).strip() if thought_match else ""
                            break
                            
                    if in_delegate and delegate_task:
                        act_id = f"del-{step}"
                        await self._emit(task_id, "action_start", {
                            "action_id": act_id,
                            "action_type": "delegate",
                            "explanation": thought_text,
                            "args_summary": f"Delegate to {delegate_model}: {delegate_task}"
                        })
                        
                        try:
                            import time
                            async def run_delegation():
                                sub_provider = PlannerProvider(model=delegate_model or "gpt-4o-mini")
                                sub_system = "You are a sub-agent. Complete the assigned task. Provide a final summary of results."
                                return await asyncio.to_thread(sub_provider._call_llm, sub_system, delegate_task, None)
                                
                            sub_agent_task = asyncio.create_task(run_delegation())
                            start_time = time.time()
                            while not sub_agent_task.done():
                                await asyncio.sleep(1.0)
                                await self._emit(task_id, "status", {"message": f"Delegating to {delegate_model}... {int(time.time() - start_time)}s", "heartbeat": True})
                            res_output = sub_agent_task.result()
                        except Exception as e:
                            res_output = f"Delegation failed: {str(e)}"
                            
                        await self._emit(task_id, "action_result", {
                            "action_id": act_id,
                            "ok": True,
                            "output": res_output,
                            "action_type": "delegate",
                            "args_summary": f"Delegate to {delegate_model}: {delegate_task}"
                        })
                        
                        messages.append({"role": "assistant", "content": buffer.split("</delegate>")[0] + "</delegate>"})
                        messages.append({"role": "user", "content": f"<observation>\nSub-agent result:\n{res_output}\n</observation>"})
                        continue

                    if not action_type:
                        if "finish" in buffer.lower() or "complete" in buffer.lower() or "done" in buffer.lower():
                            self._finalize(task_id, "done", "Task complete.")
                            await self._emit(task_id, "done", {"complete": True, "reason": "Task complete.", "finished_at": datetime.now(timezone.utc).isoformat()})
                            return
                        else:
                            messages.append({"role": "assistant", "content": buffer})
                            messages.append({"role": "user", "content": "You must output an <action>. If you are done, use the 'finish' action type."})
                            continue
                            
                    # Parse args
                    try:
                        args = json.loads(action_args_json)
                    except Exception as e:
                        # Try sanitize if possible
                        from .providers import _sanitize_json_text
                        try:
                            args = json.loads(_sanitize_json_text(action_args_json))
                        except Exception:
                            args = {}
                        
                    # Execute action
                    from .models import Action, ActionType, ToolResult
                    try:
                        act = Action(id=f"act-{step}", type=ActionType(action_type), args=args, explanation=thought_text)
                    except ValueError:
                        messages.append({"role": "assistant", "content": buffer.split("</action>")[0] + "</action>"})
                        messages.append({"role": "user", "content": f"<observation>\nInvalid action type: {action_type}\n</observation>"})
                        continue

                    # Approval handling
                    decision = self.safety.evaluate(act, safe_mode=not is_coding)
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
                        # Stream command output to terminal
                        async def _stream_chunk(c: Dict[str, Any]):
                            await self._emit(task_id, "terminal_output", {
                                "command": act.args.get("command", ""),
                                "output": c.get("output", ""),
                                "ok": True,
                                "stream": True,
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
                    
                    messages.append({"role": "assistant", "content": buffer.split("</action>")[0] + "</action>"})
                    messages.append({"role": "user", "content": f"<observation>\n{res.output}\n</observation>"})
                    
                    # Update budget (approximate 1 word = 1.3 tokens)
                    provider._total_input_tokens += len(messages[-1]["content"].split()) * 1.3
                    provider._total_output_tokens += len(buffer.split()) * 1.3
                    if await self._check_token_budget(task_id, provider, token_budget):
                        return
                    
                    if act.type == ActionType.finish:
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
            browser = self._bg_browsers.pop(task_id, None)
            if browser: await browser.stop()


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
        for b in self._bg_browsers.values(): await b.stop()

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
    except Exception:
        pass
    return "\n".join(lines)

