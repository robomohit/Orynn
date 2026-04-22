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
from .providers import PlannerProvider, _capture_screenshot_b64, detect_task_mode
from .safety import SafetyManager
from .text_editor import TextEditorTool
from .tools import ToolExecutor
from .plugins import PluginRegistry

_log = logging.getLogger("agent")

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
    ):
        self.worker_id = worker_id
        self.task_id = task_id
        self.sub_task = sub_task
        self.agent_service = agent_service
        self.mode = mode
        self.screen_width, self.screen_height = screen_dims
        self.consecutive_fails = 0
        self.action_count = 0
        self.max_actions = 20 # sub-task limit

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

        try:
            for action_data in self.sub_task.actions:
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
                        screenshot = res.base64_image or _capture_screenshot_b64(self.screen_width, self.screen_height)
                        await self._emit("screenshot", {"data": screenshot})

            # Reflection
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
            )
            
            success = reflection.get("success", True)
            self.sub_task.status = TaskStatus.done if success else TaskStatus.failed
            self.sub_task.error = reflection.get("reason") if not success else None
            
            await self._emit("subtask", {
                "subtask_id": self.sub_task.id,
                "status": "done" if success else "failed",
                "reason": reflection.get("reason"),
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
        self, task_id: str, waiting_message: str, progress_label: str, fn: Callable[..., Any], *args: Any, timeout: float = 120.0, heartbeat_interval: float = 1.0,
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

    def init_task(self, task_id: str, goal: str, screen_width: int = 1280, screen_height: int = 800, model: str = "claude-3-5-sonnet-20241022", mode: str = "auto") -> TaskRecord:
        detected_mode = detect_task_mode(goal, mode if mode != "auto" else None)
        context = AgentContext(goal=goal, screen_width=screen_width, screen_height=screen_height)
        record = TaskRecord(id=task_id, status="running", context=context, goal=goal, model=model, mode=detected_mode)
        self._active_tasks[task_id] = asyncio.create_task(self.run_task(task_id, goal, screen_width, screen_height, model, detected_mode))
        return record

    async def run_task(self, task_id: str, goal: str, screen_width: int = 1280, screen_height: int = 800, model: str = "claude-3-5-sonnet-20241022", mode: str = "coding"):
        provider = PlannerProvider(model=model)
        is_coding = mode == "coding"
        is_computer_use = mode == "computer_use"
        runs_in_background = is_coding or is_computer_use

        await asyncio.sleep(0.3)
        await self._emit(task_id, "status", {"message": f"Initializing {mode} mode..."})
        
        # Setup Browser
        bg_browser: Optional[BackgroundBrowser] = None
        if is_computer_use:
            bg_browser = BackgroundBrowser(width=screen_width, height=screen_height, headless=True)
            await bg_browser.start()
            self._bg_browsers[task_id] = bg_browser
            self.tools.set_background_browser(bg_browser)
            self.tools._background_mode = True
        else:
            self.tools._background_mode = runs_in_background

        try:
            # Planning
            env_context = ""
            if is_coding:
                env_res = self.tools.system_info()
                env_context = f"\n\nSystem environment:\n{env_res.output}"
            
            screenshot_b64 = None if runs_in_background else _capture_screenshot_b64(screen_width, screen_height)
            memories = self.memory.search(goal, limit=5)
            mem_context = "\n".join(f"- {m.content}" for m in memories) if memories else None

            plan = await self._run_with_phase_updates(task_id, "Planning...", "Planning", provider.plan_hierarchical, goal + env_context, screenshot_b64, mem_context, mode)
            await self._emit(task_id, "plan", plan.model_dump())
            
            if not plan.sub_tasks:
                self._finalize(task_id, "failed", "No plan produced.")
                return

            # Orchestration
            history: List[str] = []
            pending_tasks = {st.id: st for st in plan.sub_tasks}
            completed_tasks: Set[str] = set()
            running_tasks: Dict[str, asyncio.Task] = {}
            max_workers = plan.max_parallel_workers if plan.execution_mode == "parallel" else 1

            while pending_tasks or running_tasks:
                # 1. Start ready tasks
                ready_to_start = [
                    st for st in pending_tasks.values()
                    if all(dep in completed_tasks for dep in st.depends_on)
                    and len(running_tasks) < max_workers
                ]
                
                for st in ready_to_start:
                    del pending_tasks[st.id]
                    worker_id = f"worker-{len(completed_tasks) + len(running_tasks) + 1}"
                    worker = SubTaskWorker(worker_id, task_id, st, self, mode, (screen_width, screen_height))
                    running_tasks[st.id] = asyncio.create_task(worker.run(provider, history))

                if not running_tasks and pending_tasks:
                    # Deadlock detection
                    _log.error(f"Orchestration Deadlock in task {task_id}")
                    break

                # 2. Wait for at least one worker to finish
                done, _ = await asyncio.wait(running_tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                
                # 3. Clean up finished tasks
                for st_id, task in list(running_tasks.items()):
                    if task in done:
                        success = await task
                        if success:
                            completed_tasks.add(st_id)
                            del running_tasks[st_id]
                        else:
                            # Sub-task failed — we currently stop the whole goal
                            self._finalize(task_id, "failed", f"Sub-task {st_id} failed.")

                            # Evaluation
                            eval_screenshot = None if runs_in_background else _capture_screenshot_b64(screen_width, screen_height)
                            eval_res = await self._run_with_phase_updates(task_id, "Evaluating...", "Evaluation", provider.evaluate, goal, history, eval_screenshot, mode)
                            self.memory.add("task_outcome", f"Goal: {goal} | Outcome: {eval_res.get('complete')} | Reason: {eval_res.get('reason')}")

                            return

            # Ensure evaluation runs for ALL tasks
            eval_screenshot = None if runs_in_background else _capture_screenshot_b64(screen_width, screen_height)
            eval_res = await self._run_with_phase_updates(task_id, "Evaluating...", "Evaluation", provider.evaluate, goal, history, eval_screenshot, mode)
            
            status = "done" if eval_res.get("complete") else "failed"
            self._finalize(task_id, status, eval_res.get("reason", ""))
            await self._emit(task_id, "done", {**eval_res, "finished_at": datetime.now(timezone.utc).isoformat()})
            self.memory.add("task_outcome", f"Goal: {goal} | Outcome: {eval_res.get('complete')} | Reason: {eval_res.get('reason')}")

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
