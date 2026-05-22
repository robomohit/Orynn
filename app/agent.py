from __future__ import annotations
import asyncio
import base64
import inspect
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from PIL import Image

from .background_browser import BackgroundBrowser
from .log_emitter import LogEmitter
from .memory import MemoryStore
from .models import (
    Action,
    ActionDecision,
    ActionType,
    AgentContext,
    ApprovalBundle,
    HierarchicalPlan,
    TaskRecord,
    SubTask,
    TaskStatus,
    ToolError,
    ToolResult,
)
from .permissions import PermissionStore, scope_for_action
from .premium_features import (
    build_preflight_plan,
    discover_project_rules,
    expand_workflow_goal,
    ocr_text_from_b64,
)
from .providers import PlannerProvider, _capture_screenshot_b64, _captured_dimensions, _get_active_window_rect, _get_hwnd_for_title, detect_task_mode, classify_task_complexity, infer_isolated_app_name, is_vision_model
from .safety import SafetyManager
from .text_editor import TextEditorTool
from .tools import ToolExecutor, _flash_pointer
from .plugins import PluginRegistry
from .skills import skill_manager

_log = logging.getLogger("agent")

TOKEN_BUDGET_DEFAULT = 100_000  # max combined input+output tokens per task
MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 120.0
XML_FALLBACK_MAX_STEPS = 3
APPROVAL_WAIT_TIMEOUT_SECONDS = float(os.environ.get("APPROVAL_WAIT_TIMEOUT_SECONDS", "300"))
PERMISSION_WAIT_TIMEOUT_SECONDS = float(os.environ.get("PERMISSION_WAIT_TIMEOUT_SECONDS", "300"))
AGENT_MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "25"))
BROWSER_MAX_STEPS = int(os.environ.get("BROWSER_MAX_STEPS", "35"))

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

_DESKTOP_POST_ACTION_TYPES = {
    ActionType.mouse_click,
    ActionType.keyboard_type,
    ActionType.scroll,
    ActionType.double_click,
    ActionType.right_click,
    ActionType.middle_click,
    ActionType.mouse_move,
    ActionType.left_click_drag,
    ActionType.key_combo,
    ActionType.hold_key,
    ActionType.type_with_delay,
    ActionType.focus_window,
    ActionType.wait_for_window,
    ActionType.force_close_window,
}

_POINT_TAG_RE = re.compile(r"\[POINT:(?P<body>[^\]]+)\]", re.IGNORECASE)
_ORIGINAL_PLANNER_PROVIDER = PlannerProvider


def _new_planner_provider(model: str) -> PlannerProvider:
    if PlannerProvider is not _ORIGINAL_PLANNER_PROVIDER:
        return PlannerProvider(model=model)
    # Resolve through the module each time so tests or dev reloads of app.providers
    # do not leave AgentService pinned to a stale class object.
    from . import providers as providers_module
    return providers_module.PlannerProvider(model=model)


def _visual_hash_from_b64(image_b64: Optional[str]) -> Optional[tuple[int, ...]]:
    if not image_b64:
        return None
    try:
        raw = base64.b64decode(image_b64)
        with Image.open(io.BytesIO(raw)) as image:
            thumb = image.convert("L").resize((12, 12), Image.Resampling.BILINEAR)
            pixels = list(thumb.getdata())
    except Exception:
        return None
    if not pixels:
        return None
    mean = sum(pixels) / len(pixels)
    return tuple(1 if value >= mean else 0 for value in pixels)


def _visual_hash_distance(before_b64: Optional[str], after_b64: Optional[str]) -> Optional[int]:
    before = _visual_hash_from_b64(before_b64)
    after = _visual_hash_from_b64(after_b64)
    if before is None or after is None or len(before) != len(after):
        return None
    return sum(1 for left, right in zip(before, after) if left != right)


def _post_action_no_effect_hint(before_b64: Optional[str], after_b64: Optional[str]) -> Optional[str]:
    distance = _visual_hash_distance(before_b64, after_b64)
    if distance is None or distance > 4:
        return None
    return (
        "[no-effect hint] The screen looked unchanged after this action. "
        "If you expected a click or typed text, re-check focus, target, or window state."
    )


def _computer_subaction_needs_capture(name: str) -> bool:
    return (name or "").strip().lower() not in {"", "screenshot", "cursor_position", "wait"}


def _should_capture_post_action(action: Action, result: ToolResult, is_desktop: bool) -> bool:
    if not is_desktop or not result.ok:
        return False
    if action.type in _DESKTOP_POST_ACTION_TYPES:
        return True
    if action.type == ActionType.computer:
        return _computer_subaction_needs_capture(action.args.get("action", ""))
    if action.type in {ActionType.bash, ActionType.run_command}:
        return "Launched (fire-and-forget):" in (result.output or "")
    return False


def _extract_point_tag(text: str) -> tuple[str, Optional[Dict[str, Any]]]:
    match = _POINT_TAG_RE.search(text or "")
    if not match:
        return (text or "").strip(), None
    body = match.group("body").strip()
    cleaned = _POINT_TAG_RE.sub("", text or "", count=1).strip()
    if body.lower() == "none":
        return cleaned, None
    coord_part, sep, label = body.partition(":")
    if not sep:
        return cleaned, None
    x_text, comma, y_text = coord_part.partition(",")
    if not comma:
        return cleaned, None
    try:
        point = {
            "x": int(x_text.strip()),
            "y": int(y_text.strip()),
            "label": label.strip(),
        }
    except ValueError:
        return cleaned, None
    return cleaned, point

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
        auto_approve: bool = False,
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
        self.auto_approve = bool(auto_approve)

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
        is_coding = self.mode in ("coding", "chat", "auto")
        is_computer_use = self.mode == "computer_use"
        tools = self.agent_service._get_task_tools(self.task_id)
        is_isolated = self.mode == "computer_isolated" or bool(tools.resolve_isolated_hwnd())

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
                    if self.agent_service.is_killed(self.task_id):
                        _log.warning(f"Task {self.task_id} KILLED while paused. Worker {self.worker_id} stopping.")
                        self.sub_task.status = TaskStatus.failed
                        return False
                    await asyncio.sleep(0.5)

                self.action_count += 1
                action = Action(**action_data.model_dump())
                decision = self.agent_service.safety.evaluate(action, safe_mode=not (is_coding or self.auto_approve))

                await self._emit("intent", {
                    "action_id": action.id,
                    "action_type": action.type.value,
                    "explanation": action.explanation,
                    "args_preview": _summarize_args(action.type.value, action.args),
                })
                await self._emit("action_start", {
                    "action_id": action.id,
                    "action_type": action.type.value,
                    "explanation": action.explanation,
                    "args_summary": _summarize_args(action.type.value, action.args),
                })

                # Permission & Approval logic (reusing AgentService helpers)
                needed_scope = scope_for_action(action.type.value, action.args)
                if needed_scope and not self.agent_service.permissions.is_granted(self.task_id, needed_scope.value):
                    if is_coding or self.auto_approve:
                        granted = True
                    else:
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
                    if isinstance(chunk, str):
                        chunk = {"output": chunk, "channel": "stdout"}
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
                        tools.run_action(action, sw=self.screen_width, sh=self.screen_height, on_stream=_stream_chunk),
                        timeout=timeout
                    )
                except Exception as e:
                    res = ToolResult(ok=False, output=f"Worker Error: {str(e)}")

                results.append(res.output)
                actions_taken.append(action.model_dump())
                await asyncio.to_thread(self.agent_service.memory.add_action_result, self.task_id, action.id, res.output)
                history.append(f"[{self.worker_id}] Action: {action.type.value} -> {res.output}")

                await self._emit("action_result", {
                    "action_id": action.id,
                    "ok": res.ok,
                    "output": res.output,
                    "action_type": action.type.value,
                    "args_summary": _summarize_args(action.type.value, action.args),
                })

                if action.type == ActionType.finish:
                    success = bool(res.ok)
                    self.sub_task.status = TaskStatus.done if success else TaskStatus.failed
                    self.sub_task.error = None if success else res.output
                    history.append(f"[FINAL] {res.output}")
                    await self._emit("subtask", {
                        "subtask_id": self.sub_task.id,
                        "status": "done" if success else "failed",
                        "reason": res.output,
                    })
                    return success

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
                            hwnd = tools.resolve_isolated_hwnd()
                            screenshot = res.base64_image or _capture_hwnd_screenshot_b64(hwnd)
                            shot_payload: Dict[str, Any] = {"data": screenshot, "isolated": True}
                        else:
                            screenshot = res.base64_image or _capture_screenshot_b64(self.screen_width, self.screen_height)
                            shot_payload = {"data": screenshot}
                            window_rect = _get_active_window_rect(self.screen_width, self.screen_height)
                            if window_rect:
                                shot_payload["window_rect"] = window_rect
                        await self._emit("screenshot", shot_payload)

            # Reflection — skip when atomic OR all actions succeeded (C4: no extra LLM call on success)
            _had_failures = self.consecutive_fails > 0 or (bool(results) and not actions_taken[-1].get("ok", True) if actions_taken else False)
            if self.complexity != "atomic" and _had_failures:
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
                retry_actions = reflection.get("retry_actions") or []
                if not success and retry_actions:
                    retry_results: List[str] = []
                    retry_taken: List[Dict[str, Any]] = []
                    for retry_idx, retry_data in enumerate(retry_actions):
                        retry_action = Action(
                            id=retry_data.get("id", f"{self.sub_task.id}-retry-{retry_idx + 1}"),
                            type=retry_data["type"],
                            args=retry_data.get("args", {}),
                            explanation=retry_data.get("explanation", "Retry action"),
                        )
                        retry_res = await asyncio.wait_for(
                            tools.run_action(retry_action, sw=self.screen_width, sh=self.screen_height),
                            timeout=300.0 if is_coding else 120.0,
                        )
                        retry_results.append(retry_res.output)
                        retry_taken.append(retry_action.model_dump())
                        history.append(f"[{self.worker_id}] Retry: {retry_action.type.value} -> {retry_res.output}")
                    retry_reflection = await self.agent_service._run_with_phase_updates(
                        self.task_id,
                        f"Worker {self.worker_id} reflecting on retry...",
                        "Retry Reflection",
                        provider.reflect_on_subtask,
                        self.sub_task.description,
                        retry_taken,
                        retry_results,
                        reflect_screenshot,
                        self.mode,
                        system_prompt_extension=self.system_prompt_extension,
                    )
                    success = retry_reflection.get("success", True)
                    reason = retry_reflection.get("reason", reason)
            else:
                # Atomic tasks or all-success subtasks skip reflection
                success = True
                reason = "Atomic fast-path success" if self.complexity == "atomic" else "Subtask completed successfully."

            
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
        self.workspace = workspace.resolve()
        self.home_dir = Path.home().resolve()
        self.log_emitter = log_emitter
        self.memory = MemoryStore(self.workspace)
        self.safety = SafetyManager()
        self.permissions = PermissionStore()
        self.plugin_registry = PluginRegistry()
        self.plugin_registry.load_defaults()
        self.tools = ToolExecutor(self.workspace, plugin_registry=self.plugin_registry, home_dir=self.home_dir, memory=self.memory)
        self._task_tools: Dict[str, ToolExecutor] = {}
        self._task_environments: Dict[str, Dict[str, Any]] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._paused_tasks: set[str] = set()
        self._approvals: Dict[str, asyncio.Future] = {}
        self._approval_overrides: Dict[str, str] = {}
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

    def _create_task_tools(self, workspace: Path) -> ToolExecutor:
        return ToolExecutor(workspace, plugin_registry=self.plugin_registry, home_dir=self.home_dir, memory=self.memory)

    def _assign_task_tools(self, task_id: str, workspace: Path) -> ToolExecutor:
        tools = self._create_task_tools(workspace.resolve())
        self._task_tools[task_id] = tools
        return tools

    def _get_task_tools(self, task_id: str) -> ToolExecutor:
        return self._task_tools.get(task_id, self.tools)

    def kill_task(self, task_id: str):
        """Mark a task for immediate termination."""
        self._killed_tasks.add(task_id)
        task = self._active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()

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
        self, task_id: str, waiting_message: str, progress_label: str, fn: Callable[..., Any], *args: Any, timeout: float = 180.0, poll_interval: float = 0.1, heartbeat_seconds: float = 1.0, **kwargs: Any,
    ) -> Any:
        await self._emit(task_id, "status", {"message": waiting_message})
        await self._emit_reasoning(task_id, progress_label, waiting_message, live=True, elapsed_seconds=0)
        work = asyncio.create_task(asyncio.to_thread(partial(fn, *args, **kwargs)))
        start = asyncio.get_running_loop().time()
        last_heartbeat = 0.0
        while not work.done():
            await asyncio.sleep(poll_interval)
            now = asyncio.get_running_loop().time() - start
            if now >= timeout:
                work.cancel()
                raise TimeoutError(f"{progress_label} timed out.")
            # Emit at most one heartbeat per second so the UI feels alive
            # without being flooded with 20 status events per second.
            if now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                await self._emit(task_id, "status", {"message": f"{progress_label}... {int(now)}s", "elapsed_seconds": int(now), "heartbeat": True})
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

    def init_task(
        self,
        task_id: str,
        goal: str,
        screen_width: int = 1280,
        screen_height: int = 800,
        model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        mode: str = "auto",
        isolated_app: Optional[str] = None,
        token_budget: int = TOKEN_BUDGET_DEFAULT,
        active_skills: Optional[List[str]] = None,
        project_folder: Optional[str] = None,
        environment: Optional[Dict[str, Any]] = None,
        plan_first: bool = False,
        notify_on_completion: bool = False,
        auto_commit: bool = False,
        autonomy_level: str = "balanced",
    ) -> TaskRecord:
        active_skills = list(active_skills or [])
        task_workspace = Path(project_folder).expanduser().resolve() if project_folder else self.home_dir
        goal = expand_workflow_goal(goal, task_workspace)
        detected_mode = detect_task_mode(goal, mode if mode != "auto" else None)
        if detected_mode == "computer_isolated" and not isolated_app:
            isolated_app = infer_isolated_app_name(goal)
        self._assign_task_tools(task_id, task_workspace)
        environment_payload = dict(environment or {})
        environment_payload["autonomy_level"] = autonomy_level
        environment_payload["plan_first"] = bool(plan_first)
        environment_payload["notify_on_completion"] = bool(notify_on_completion)
        environment_payload["auto_commit"] = bool(auto_commit)
        self._task_environments[task_id] = environment_payload
        context = AgentContext(
            goal=goal,
            screen_width=screen_width,
            screen_height=screen_height,
            isolated_app=isolated_app,
            active_skills=active_skills,
            project_folder=str(task_workspace) if project_folder else None,
            environment=environment_payload,
        )
        record = TaskRecord(
            id=task_id,
            status="running",
            context=context,
            goal=goal,
            model=model,
            mode=detected_mode,
            plan_first=bool(plan_first),
            notify_on_completion=bool(notify_on_completion),
            auto_commit=bool(auto_commit),
            autonomy_level=autonomy_level or "balanced",
        )
        self._active_tasks[task_id] = asyncio.create_task(
            self.run_task(
                task_id,
                goal,
                screen_width,
                screen_height,
                model,
                detected_mode,
                isolated_app=isolated_app,
                token_budget=token_budget,
                active_skills=active_skills,
                project_folder=str(task_workspace) if project_folder else None,
                environment=environment_payload,
                plan_first=bool(plan_first),
                notify_on_completion=bool(notify_on_completion),
                auto_commit=bool(auto_commit),
                autonomy_level=autonomy_level or "balanced",
            )
        )
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

    async def run_task(
        self,
        task_id: str,
        goal: str,
        screen_width: int = 1280,
        screen_height: int = 800,
        model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        mode: str = "coding",
        isolated_app: Optional[str] = None,
        token_budget: int = TOKEN_BUDGET_DEFAULT,
        active_skills: Optional[List[str]] = None,
        project_folder: Optional[str] = None,
        environment: Optional[Dict[str, Any]] = None,
        plan_first: bool = False,
        notify_on_completion: bool = False,
        auto_commit: bool = False,
        autonomy_level: str = "balanced",
    ):
        provider_override = None
        if not isinstance(screen_width, int) and hasattr(screen_width, "stream_chat"):
            provider_override = screen_width
            screen_width = 1280
        active_skills = list(active_skills or [])
        provider = provider_override or _new_planner_provider(model)
        tools = self._get_task_tools(task_id)
        if project_folder and task_id not in self._task_tools:
            tools = self._assign_task_tools(task_id, Path(project_folder).expanduser().resolve())
        environment_payload = dict(self._task_environments.get(task_id) or environment or {})
        if not environment_payload:
            environment_payload = _build_environment_payload(tools.workspace, self.home_dir, project_folder_selected=bool(project_folder))
        self._task_environments[task_id] = environment_payload
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
        if autonomy_level == "careful":
            is_auto_approve = False

        provider_model = getattr(provider, "model", model)
        await self._emit(task_id, "provider_info", {
            "model": provider_model,
            "requested_model": model,
            "tier": getattr(provider, "model_tier", None),
            "local": str(provider_model).startswith("ollama/"),
        })

        # Build Skill Instructions
        skill_instructions = ""
        if active_skills:
            skill_instructions = "\n\n### ACTIVE SKILL MANUALS\n"
            for skill_id in active_skills:
                skill = skill_manager.get_skill(skill_id)
                if skill:
                    skill_instructions += f"\n{skill.manual}\n"
        project_rules = await asyncio.to_thread(discover_project_rules, tools.workspace)
        if project_rules:
            skill_instructions += f"\n\n### PROJECT RULES\n{project_rules}\n"

        # Conversational acknowledgement — the agent "accepts" the task in plain
        # language before it starts working, instead of a cold "Initializing…".
        _ack_goal = (goal or "").strip().replace("\n", " ")
        if len(_ack_goal) > 96:
            _ack_goal = _ack_goal[:94].rstrip() + "…"
        await self._emit(task_id, "status", {
            "message": f"Got it — on it now: {_ack_goal}" if _ack_goal else "Got it — starting now…",
            "elapsed_seconds": 0,
        })

        try:
            if plan_first:
                plan_payload = build_preflight_plan(goal, mode=mode, autonomy_level=autonomy_level)
                await self._emit(task_id, "plan", plan_payload)
                plan_text = "\n".join(
                    f"{i + 1}. {item.get('description', '')}"
                    for i, item in enumerate(plan_payload.get("sub_tasks", []))
                )
                await self._emit(task_id, "approval_required", {
                    "action_id": "__plan__",
                    "action": {"type": "plan_review", "args": {"plan_text": plan_text}},
                    "danger": "low",
                    "reason": "Review or edit the plan before the agent starts acting.",
                })
                approved = await self._wait_for_approval(task_id, "__plan__")
                plan_override = self._approval_overrides.pop(f"{task_id}:__plan__", "")
                if not approved:
                    self._finalize(task_id, "cancelled", "Plan rejected by user.")
                    await self._emit(task_id, "cancelled", {
                        "message": "Plan rejected by user.",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    })
                    return
                if plan_override.strip():
                    goal = f"{goal}\n\nApproved execution plan:\n{plan_override.strip()}"
            # ── Explain mode: read-only screen Q&A ──
            # A single vision turn — screenshot + question -> answer. The whole
            # agent action loop is bypassed, so nothing on the machine is ever
            # touched: no clicks, no typing, no file writes, no commands.
            if mode == "explain":
                await self._emit(task_id, "status", {"message": "Looking at your screen…", "elapsed_seconds": 0})
                # Explain mode is inherently a vision task — a text-only model
                # can't see the screenshot. Fail helpfully instead of letting
                # the model vaguely answer "I don't see an image".
                # Use provider.model — the RESOLVED model (a speed tier like
                # "tier:balanced" has already been expanded to a real model id).
                if not is_vision_model(provider.model):
                    # Explain mode is inherently a vision task. Rather than fail
                    # when a text-only model (or a speed tier whose primary is
                    # text-only) is selected, transparently upgrade to a free
                    # vision-capable model so the feature just works.
                    _fallback_vision = "google/gemma-4-31b-it:free"
                    _log.info(
                        "explain mode: %s is text-only, upgrading to %s",
                        provider.model, _fallback_vision,
                    )
                    provider.model = _fallback_vision
                    await self._emit(task_id, "status", {
                        "message": "Using a vision model to read your screen…",
                        "elapsed_seconds": 0,
                    })
                explain_shot = None
                try:
                    explain_shot = _capture_screenshot_b64(screen_width, screen_height)
                except Exception as cap_err:
                    _log.warning("explain mode: screenshot capture failed: %s", cap_err)
                ocr_text = await asyncio.to_thread(ocr_text_from_b64, explain_shot)
                explain_system = (
                    "You are a helpful assistant explaining what is on the user's screen. "
                    "Look at the screenshot and answer the user's question clearly and concisely. "
                    "You are in READ-ONLY mode: describe, explain, and advise — never say you will "
                    "click, type, open, or change anything. If the screenshot is missing or unclear, say so. "
                    "If pointing would help, append exactly one tag at the end of your answer in the form "
                    "[POINT:x,y:label] using screenshot coordinates, or [POINT:none] if pointing would not help."
                )
                explain_goal = goal
                if ocr_text:
                    explain_goal = f"{goal}\n\nOCR text detected on screen:\n{ocr_text}"
                explain_answer_parts: List[str] = []
                try:
                    async for _chunk in provider.stream_chat(
                        explain_system, [{"role": "user", "content": explain_goal}], explain_shot,
                    ):
                        explain_answer_parts.append(_chunk)
                except Exception as explain_err:
                    self._finalize(task_id, "failed", str(explain_err))
                    await self._emit(task_id, "done", {
                        "complete": False,
                        "reason": f"Could not explain the screen: {explain_err}",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    })
                    return
                explain_answer = "".join(explain_answer_parts).strip() or \
                    "I couldn't read the screen clearly enough to explain it."
                explain_answer, explain_point = _extract_point_tag(explain_answer)
                if explain_point:
                    try:
                        cap_w, cap_h = _captured_dimensions(screen_width, screen_height)
                        real_x = max(0, min(screen_width - 1, round(explain_point["x"] * screen_width / max(1, cap_w))))
                        real_y = max(0, min(screen_height - 1, round(explain_point["y"] * screen_height / max(1, cap_h))))
                        await asyncio.to_thread(_flash_pointer, real_x, real_y)
                    except Exception as point_err:
                        _log.debug("explain mode: pointer overlay skipped: %s", point_err)
                self._finalize(task_id, "done", explain_answer)
                await self._emit(task_id, "done", {
                    "complete": True,
                    "reason": explain_answer,
                    "is_reply": True,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                })
                return

            # Setup Browser
            bg_browser: Optional[BackgroundBrowser] = None
            if is_computer_use:
                try:
                    bg_browser = BackgroundBrowser(width=screen_width, height=screen_height, headless=True)
                    await bg_browser.start()
                    self._bg_browsers[task_id] = bg_browser
                    tools.set_background_browser(bg_browser)
                    tools._background_mode = True
                except Exception as browser_err:
                    raise Exception(f"Failed to start background browser: {str(browser_err)}. Make sure playwright browsers are installed.")
            else:
                tools._background_mode = runs_in_background

            # Isolated App Control: find HWND and wire it up for computer mode
            isolated_hwnd: Optional[int] = None
            if mode == "computer_isolated":
                if isolated_app:
                    isolated_hwnd = _get_hwnd_for_title(isolated_app)
                
                if not isolated_hwnd:
                    # Try to grab the current foreground window if it's not a common system/shell window
                    try:
                        import win32gui  # type: ignore
                        hwnd = win32gui.GetForegroundWindow()
                        if hwnd and win32gui.IsWindowVisible(hwnd):
                            title = win32gui.GetWindowText(hwnd).lower()
                            # Avoid locking to the browser or the dashboard itself
                            if not any(kw in title for kw in ("chrome", "edge", "clean stream", "dash")):
                                isolated_hwnd = hwnd
                                isolated_app = win32gui.GetWindowText(hwnd)
                    except Exception:
                        isolated_hwnd = None

                if isolated_hwnd:
                    tools.set_isolated_hwnd(isolated_hwnd, isolated_app)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": True, "isolated_app": isolated_app or "Active Window"})
                    _log.info(f"Isolated mode: HWND {isolated_hwnd} for '{isolated_app}'")
                elif isolated_app:
                    tools.set_isolated_hwnd(None, isolated_app)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": True, "isolated_app": isolated_app, "isolated_pending": True})
                    await self._emit(task_id, "status", {"message": f"Waiting to attach isolated control to '{isolated_app}' once it opens."})
                else:
                    _log.warning(f"No suitable target window found for isolated mode. Falling back to full-desktop computer mode.")
                    mode = "computer"  # Fallback to full desktop mode so tools still work
                    is_isolated = False
                    isolated_app = None
                    tools.set_isolated_hwnd(None)
                    await self._emit(task_id, "mode", {"mode": mode, "isolated": False})
                    await self._emit(task_id, "status", {"message": "⚠️ No target window found for isolated mode — using full desktop instead."})
            else:
                tools.set_isolated_hwnd(None)
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
            project_folder_selected = bool(environment_payload.get("project_folder_selected"))
            if is_coding_mode:
                if complexity == "atomic":
                    env_context = f"\n\nWorkspace hint: {environment_payload.get('workspace') or str(tools.workspace)}"
                else:
                    await self._emit(task_id, "status", {"message": "Initializing: loading environment..."})
                    env_context = _environment_context_text(environment_payload)
                    if _goal_needs_tree and project_folder_selected:
                        await self._emit(task_id, "status", {"message": "Initializing: reading project tree..."})
                        env_context += _workspace_tree(tools.workspace, depth=1)

            
            # No screenshots for coding/chat — only for computer/desktop modes
            if runs_in_background or is_coding_mode:
                screenshot_b64 = None
            elif isolated_hwnd:
                # Isolated mode: crop to just the target window so model isn't distracted
                from .providers import _capture_hwnd_screenshot_b64
                screenshot_b64 = _capture_hwnd_screenshot_b64(isolated_hwnd)
            else:
                screenshot_b64 = _capture_screenshot_b64(screen_width, screen_height)

            memories = await asyncio.to_thread(self.memory.search, goal, 5)
            mem_context = "\n".join(f"- {getattr(m, 'content', m)}" for m in memories) if memories else None
            prior_sessions = await asyncio.to_thread(self.memory.recall_sessions, goal, 5)
            relevant_history_block = (
                "<relevant_history>\n"
                + "\n".join(f"- {getattr(s, 'content', s)}" for s in prior_sessions)
                + "\n</relevant_history>"
            ) if prior_sessions else ""

            if mode in ("computer", "computer_isolated"):
                try:
                    if complexity == "atomic":
                        from .providers import _extract_json, _normalize_hierarchical_plan, get_tool_guidance, get_mode_packs
                        packs = get_mode_packs(mode)
                        prompt = f"Goal: {goal}\n\nReturn one concise JSON plan using only these tools:\n{get_tool_guidance(packs)}"
                        raw_plan = provider._call_llm("You are a fast-path planning agent. Return only JSON.", prompt, screenshot_b64)
                        plan = HierarchicalPlan.model_validate(_normalize_hierarchical_plan(_extract_json(raw_plan)))
                    else:
                        plan = provider.plan_hierarchical(
                            goal,
                            latest_screenshot_b64=screenshot_b64,
                            memory_context=mem_context,
                            mode=mode,
                            system_prompt_extension=skill_instructions or None,
                        )

                    history: List[str] = []
                    final_reason = ""

                    async def _run_subtask(idx: int, sub_task: SubTask) -> bool:
                        worker = SubTaskWorker(
                            worker_id=f"worker-{idx + 1}",
                            task_id=task_id,
                            sub_task=sub_task,
                            agent_service=self,
                            mode=mode,
                            screen_dims=(screen_width, screen_height),
                            complexity=complexity,
                            system_prompt_extension=skill_instructions or None,
                            auto_approve=is_auto_approve,
                        )
                        return await worker.run(provider, history)

                    def _write_scopes_conflict(left: SubTask, right: SubTask) -> bool:
                        left_scope = [str(path).strip().lower() for path in (left.write_scope or []) if str(path).strip()]
                        right_scope = [str(path).strip().lower() for path in (right.write_scope or []) if str(path).strip()]
                        if not left_scope or not right_scope:
                            return True
                        for lhs in left_scope:
                            for rhs in right_scope:
                                if lhs == rhs or lhs.startswith(rhs.rstrip("/\\") + "/") or rhs.startswith(lhs.rstrip("/\\") + "/"):
                                    return True
                        return False

                    async def _execute_subtasks() -> List[bool]:
                        if not plan.sub_tasks:
                            return []

                        max_workers = max(1, int(plan.max_parallel_workers or 1))
                        pending: List[tuple[int, SubTask]] = list(enumerate(plan.sub_tasks))
                        done_by_id: Dict[str, bool] = {}
                        results: List[Optional[bool]] = [None] * len(plan.sub_tasks)
                        allow_parallel = str(plan.execution_mode or "serial").lower() == "parallel"

                        while pending:
                            ready: List[tuple[int, SubTask]] = []
                            still_pending: List[tuple[int, SubTask]] = []
                            progressed = False

                            for idx, sub_task in pending:
                                deps = list(sub_task.depends_on or [])
                                if any(done_by_id.get(dep) is False for dep in deps):
                                    done_by_id[sub_task.id] = False
                                    results[idx] = False
                                    progressed = True
                                    continue
                                if all(done_by_id.get(dep) is True for dep in deps):
                                    ready.append((idx, sub_task))
                                else:
                                    still_pending.append((idx, sub_task))

                            pending = still_pending
                            if not ready:
                                if not pending:
                                    break
                                ready = [pending.pop(0)]

                            batch: List[tuple[int, SubTask]] = []
                            if allow_parallel:
                                for item in ready:
                                    if len(batch) >= max_workers:
                                        pending.insert(0, item)
                                        continue
                                    if any(_write_scopes_conflict(item[1], chosen[1]) for chosen in batch):
                                        pending.append(item)
                                        continue
                                    batch.append(item)
                                if not batch:
                                    batch = [ready[0]]
                                    pending.extend(ready[1:])
                            else:
                                batch = [ready[0]]
                                pending = ready[1:] + pending

                            batch_results = await asyncio.gather(
                                *(_run_subtask(idx, sub_task) for idx, sub_task in batch),
                                return_exceptions=False,
                            )
                            progressed = True
                            for (idx, sub_task), ok in zip(batch, batch_results):
                                done_by_id[sub_task.id] = bool(ok)
                                results[idx] = bool(ok)
                                if not ok and not allow_parallel:
                                    for blocked_idx, blocked_task in pending:
                                        results[blocked_idx] = False
                                        done_by_id[blocked_task.id] = False
                                    pending = []
                                    break

                            if not progressed:
                                break

                        return [bool(result) for result in results if result is not None]

                    subtask_results = await _execute_subtasks()
                    all_success = all(subtask_results)
                    final_entries = [entry for entry in history if entry.startswith("[FINAL] ")]
                    if final_entries:
                        final_reason = final_entries[-1].replace("[FINAL] ", "", 1).strip()

                    if final_reason:
                        complete = all_success
                        reason = final_reason
                    else:
                        # C5: Replace LLM evaluate() call with simple heuristic — saves one round-trip
                        complete = all_success
                        reason = "Task complete." if complete else "Task failed."
                    self._finalize(task_id, "done" if complete else "failed", reason)
                    await self._emit(task_id, "done", {
                        "complete": complete,
                        "reason": reason,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    })
                    await asyncio.to_thread(self.memory.summarize_session, task_id, goal, complete, reason, mode)
                    await asyncio.to_thread(self.memory.add, "task_outcome", f"Outcome: {complete}. Goal: {goal}. Reason: {reason}")
                    asyncio.create_task(asyncio.to_thread(self.memory.maybe_auto_consolidate))
                    return
                except Exception as planning_err:
                    await self._emit(task_id, "status", {"message": f"Structured planning failed; using streaming loop. ({planning_err})"})

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
                        "1. Use browser_open to go to a URL.\n"
                        "2. Use browser_get_text to read page content and find elements.\n"
                        "3. Use browser_click, browser_type, browser_scroll to interact with the page.\n"
                        "4. Use browser_accessibility_tree when you need element references.\n"
                        "5. Use web_search if you need to find a URL first.\n"
                        "6. When you have the information requested, call finish with a clear summary.\n\n"
                        "RESEARCH RULES:\n"
                        "- For ecommerce rating tasks, check collection pages, product pages, review text, sort/filter labels, and web_fetch/search snippets when needed.\n"
                        "- If ratings/reviews are not visible after checking likely source pages and search snippets, call finish and say exactly what was checked and that ratings were not found. Do NOT keep browsing until max steps.\n"
                        "- Prefer exact evidence from page text over guessing. Never invent ratings.\n\n"
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
                        "WORKFLOW: browser_open -> browser_get_text -> interact -> browser_get_text -> finish\n"
                        "RESEARCH: For ecommerce ratings, check collection/product/review text and search snippets. If ratings are absent after likely checks, finish with a clear 'ratings not found' answer and list what you checked. Never invent ratings.\n"
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
                        "5. A fresh screenshot is captured automatically after every desktop interaction. Use explicit screenshot only when you want an extra check.\n"
                        "6. Use key_combo for shortcuts (e.g. ctrl+s to save, ctrl+w to close a tab).\n"
                        "7. SAVING FILES: When a Save-As dialog opens, type the FULL path (e.g. C:\\Users\\<username>\\Desktop\\filename.txt) to save to a specific folder. Do NOT type just the filename — it will save to the wrong folder.\n"
                        "   To get the username, run: bash {\"command\": \"echo %USERNAME%\"} before saving.\n"
                        "8. When done, call finish with a summary of what was accomplished.\n\n"
                        "CRITICAL SAFETY RULES:\n"
                        "- NEVER close, minimize, or interact with Google Chrome or Microsoft Edge — those are the monitoring dashboard. Any Alt+F4, Ctrl+W, or clicks on the browser X button are FORBIDDEN.\n"
                        "- In full desktop mode, do not interact with Cursor, browsers, File Explorer, or unrelated windows unless the user explicitly asks.\n"
                        "- NEVER send Alt+F4 unless explicitly asked to close a specific non-browser app.\n"
                        "- NEVER click outside the target app window — confirm target window is in focus first.\n"
                        "- If an action fails or the screenshot shows an unexpected state, re-evaluate and try a different approach.\n\n"
                        "APP-SPECIFIC RULES:\n"
                        "- For Notepad or text-editor tasks, start a fresh blank document before typing when possible. Do not type into a restored or titled document unless the user asked to edit it.\n"
                        "- In isolated mode, verify the focused window title matches the target app before typing.\n"
                        "- Once the requested result is visible in a screenshot, call finish. Do not keep taking screenshots or repeating input.\n\n"
                        "EFFICIENCY:\n"
                        "- Don't take screenshots you don't need (once after open, once after action is enough).\n"
                        "- Don't repeat failed actions — if a click didn't work, try a different coordinate or approach.\n"
                    )
                    xml_system = (
                        "You are AI Computer — a desktop automation agent controlling a real Windows PC.\n"
                        "FORMAT: <thought>reasoning</thought> then <action type=\"tool\">{args}</action>\n\n"
                        "WORKFLOW: screenshot → bash to open app → focus_window to bring it forward → keyboard_type text → inspect the automatic post-action screenshot → finish\n"
                        "SAFETY: NEVER close Chrome, Edge, Cursor, or unrelated windows. Verify the target window title before typing. For Notepad, use a fresh blank document when possible. Finish as soon as the requested result is visible.\n\n"
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
                        "- Use list_mcp_servers and list_mcp_tools to discover workspace MCP integrations before calling mcp_tool.\n"
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
                if relevant_history_block:
                    system += f"\n\n{relevant_history_block}"
                    xml_system += f"\n\n{relevant_history_block}"

                # ── Auto-inject workspace tree when workspace has files ────────
                auto_context = ""
                try:
                    if project_folder_selected:
                        tree = _workspace_tree(tools.workspace, depth=1)
                        if tree and tree.strip():
                            auto_context = f"\n\nWorkspace:\n{tree}"
                except Exception:
                    pass

                win_info = f"\nTarget window: {isolated_app}" if is_isolated else ""
                messages = [{"role": "user", "content": f"{goal}{env_context}{auto_context}{win_info}"}]
                use_native_tools = len(tool_schemas) > 0 and inspect.isasyncgenfunction(
                    getattr(provider, "stream_chat_with_tools", None)
                )
                
                # ── Anti-waste tracking: detect duplicate calls and cache writes ──
                _recent_calls: list[tuple[str, str]] = []  # (action_type, args_key) last 3 calls
                _write_cache: dict[str, str] = {}  # path → content of recently written files
                xml_fallback_steps = 0
                max_steps = BROWSER_MAX_STEPS if _is_browser_use else AGENT_MAX_STEPS

                for step in range(max_steps):
                    if self.is_killed(task_id):
                        break
                    while task_id in self._paused_tasks:
                        await asyncio.sleep(0.5)
                        if self.is_killed(task_id):
                            break
                    if self.is_killed(task_id):
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
                        isolated_hwnd = tools.resolve_isolated_hwnd() if is_isolated else None
                        if isolated_hwnd:
                            # Isolated mode: crop to just the target window
                            from .providers import _capture_hwnd_screenshot_b64
                            screenshot_b64 = _capture_hwnd_screenshot_b64(isolated_hwnd)
                        else:
                            # Full desktop
                            screenshot_b64 = _capture_screenshot_b64(screen_width, screen_height)

                    # ── Hard cap on message count: keep system prompt(s) + last
                    # MAX_TURNS conversation messages. Prevents unbounded growth
                    # on long-horizon tasks (50+ steps) where even truncated
                    # entries accumulate token cost.
                    MAX_TURNS = 30
                    if len(messages) > MAX_TURNS + 2:
                        # Keep leading system messages, drop oldest non-system
                        # until we're back at the cap.
                        sys_prefix = []
                        i = 0
                        while i < len(messages) and messages[i].get("role") == "system":
                            sys_prefix.append(messages[i])
                            i += 1
                        tail = messages[-MAX_TURNS:]
                        messages = sys_prefix + tail

                    # ── History compression: truncate old observation results and assistant tool calls ──
                    if len(messages) > 6:
                        for i in range(1, len(messages) - 4):
                            m = messages[i]
                            if m["role"] == "tool" and len(m.get("content", "")) > 500:
                                messages[i] = {**m, "content": m["content"][:500] + "\n...(truncated)"}
                            elif m["role"] == "user" and "<observation>" in m.get("content", "") and len(m["content"]) > 500:
                                messages[i] = {**m, "content": m["content"][:500] + "\n...(truncated)</observation>"}
                            elif m["role"] == "assistant":
                                if "tool_calls" in m:
                                    new_tool_calls = []
                                    for tc in m["tool_calls"]:
                                        if "function" in tc and "arguments" in tc["function"]:
                                            args_str = tc["function"]["arguments"]
                                            if len(args_str) > 500:
                                                import json as _json
                                                try:
                                                    parsed = _json.loads(args_str)
                                                    for k, v in parsed.items():
                                                        if isinstance(v, str) and len(v) > 200:
                                                            parsed[k] = v[:200] + "...(truncated)"
                                                    tc["function"]["arguments"] = _json.dumps(parsed)
                                                except Exception:
                                                    tc["function"]["arguments"] = args_str[:500] + '...{"truncated": true}'
                                        new_tool_calls.append(tc)
                                    messages[i] = {**m, "tool_calls": new_tool_calls}
                                elif len(m.get("content", "")) > 500 and "<action" in m.get("content", ""):
                                    import re
                                    truncated_content = re.sub(r'(<action[^>]*>).*?(</action>)', r'\1\n...(truncated args)...\n\2', m["content"], flags=re.DOTALL)
                                    messages[i] = {**m, "content": truncated_content}

                    try:
                        # ── TRY NATIVE TOOL CALLING FIRST ──
                        if use_native_tools:
                            try:
                                await self._emit(task_id, "status", {"message": f"Thinking through step {step+1}…"})
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
                                _got_first_token = False
                                # Background heartbeat while we wait for the
                                # model's first token. Without this the UI sees
                                # nothing for the full TTFT (5–15s on free
                                # tiers) and feels frozen. Cancelled the moment
                                # the first token arrives.
                                async def _ttft_heartbeat():
                                    waited = 0
                                    try:
                                        while True:
                                            await asyncio.sleep(1.5)
                                            waited += 2  # approx; granularity ok
                                            await self._emit(task_id, "status", {
                                                "message": f"Thinking… waiting on model (step {step+1}, {waited}s)",
                                                "elapsed_seconds": waited,
                                                "heartbeat": True,
                                            })
                                    except asyncio.CancelledError:
                                        return
                                _ttft_task = asyncio.create_task(_ttft_heartbeat())
                                # Throttle reasoning emits so a fast token
                                # stream doesn't flood the SSE channel.
                                _last_reason_emit = 0.0
                                _REASON_MIN_INTERVAL = 0.2  # max 5/sec
                                async for event in native_stream:
                                    if not _got_first_token:
                                        _got_first_token = True
                                        _ttft_task.cancel()
                                        await self._emit(task_id, "status", {"message": f"Working on step {step+1}…"})
                                    if event["type"] == "thought":
                                        thought_text += event["content"]
                                        _now = asyncio.get_running_loop().time()
                                        if _now - _last_reason_emit >= _REASON_MIN_INTERVAL:
                                            _last_reason_emit = _now
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
                                        # The card will be finalized at the end of the loop
                            except Exception as e:
                                _log.warning(f"Native tool calling failed, falling back to XML: {e}")
                                use_native_tools = False
                                await self._emit(task_id, "status", {"message": f"Native tool stream stalled or failed; falling back to XML. ({e})"})
                            finally:
                                # Make sure the TTFT heartbeat task can't leak
                                # if the stream raised before the first token.
                                try:
                                    _ttft_task.cancel()
                                except (NameError, AttributeError):
                                    pass
                        
                        # ── XML FALLBACK ──
                        if not use_native_tools and action_type is None:
                            xml_fallback_steps += 1
                            if xml_fallback_steps > XML_FALLBACK_MAX_STEPS:
                                raise TimeoutError("XML fallback exhausted its max recovery steps.")
                            buffer = ""
                            thought_text = ""
                            in_action = False
                            action_args_json = ""
                            delegate_info = None

                            await self._emit(task_id, "status", {"message": f"Thinking through step {step+1}…"})
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
                            _got_first_chunk = False
                            async for chunk in stream_gen:
                                if not _got_first_chunk:
                                    _got_first_chunk = True
                                    await self._emit(task_id, "status", {"message": f"Working on step {step+1}…"})
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
                                if "<delegate" in buffer and "</delegate>" in buffer and not in_action:
                                    model_match = re.search(r'<delegate\s+model="([^"]+)">', buffer)
                                    task_match = re.search(r"<task>(.*?)</task>", buffer, flags=re.DOTALL)
                                    delegate_info = {
                                        "model": model_match.group(1) if model_match else model,
                                        "task": task_match.group(1).strip() if task_match else "",
                                    }
                                    break
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
                            
                            if delegate_info:
                                delegate_action_id = f"delegate-{step}"
                                await self._emit(task_id, "action_start", {
                                    "action_id": delegate_action_id,
                                    "action_type": "delegate",
                                    "explanation": thought_text,
                                    "args_summary": str(delegate_info)[:80],
                                })
                                try:
                                    delegate_output = await asyncio.to_thread(lambda: "")
                                except Exception as e:
                                    delegate_output = f"Delegate failed: {e}"
                                await self._emit(task_id, "action_result", {
                                    "action_id": delegate_action_id,
                                    "ok": not str(delegate_output).startswith("Delegate failed:"),
                                    "output": str(delegate_output),
                                    "action_type": "delegate",
                                    "args_summary": str(delegate_info)[:80],
                                })
                                if '<action type="finish"' in buffer:
                                    self._finalize(task_id, "done", "done")
                                    await self._emit(task_id, "done", {"complete": True, "reason": "done", "finished_at": datetime.now(timezone.utc).isoformat()})
                                    return
                                messages.append({"role": "assistant", "content": buffer})
                                messages.append({"role": "user", "content": f"<observation>\n{delegate_output}\n</observation>"})
                                continue

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
                            # Finalize the current step as a reply rather than creating a duplicate "Response" card
                            await self._emit(task_id, "reasoning", {
                                "stage": f"Step {step+1}", "summary": thought_text[:80],
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
                    if _call_key in _recent_calls and action_type not in ("finish", "bash", "run_tests", "run_command"):
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
                    # M3: safety.evaluate is also called inside SubTaskWorker.run() but that is a
                    # different code path (hierarchical plan). The streaming loop here runs only when
                    # the hierarchical planner is not used or falls back — no double-evaluation occurs.
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

                    # Operator-style "intent" event — surface the agent's
                    # reasoning before the action so the UI can render
                    # "about to: X — because Y" in real time.
                    _arg_summary = _summarize_args(act.type.value, args)
                    await self._emit(task_id, "intent", {
                        "action_id": act.id,
                        "action_type": act.type.value,
                        "explanation": act.explanation,
                        "args_preview": _arg_summary,
                    })
                    await self._emit(task_id, "status", {"message": f"Executing {act.type.value}: {_arg_summary}"})
                    await self._emit(task_id, "action_start", {
                        "action_id": act.id,
                        "action_type": act.type.value,
                        "explanation": act.explanation,
                        "args_summary": _arg_summary,
                    })

                    pre_action_screenshot = screenshot_b64
                    try:
                        async def _stream_chunk(c: Dict[str, Any]):
                            if isinstance(c, str):
                                c = {"output": c, "channel": "stdout"}
                            await self._emit(task_id, "terminal_output", {
                                "command": act.args.get("command", ""),
                                "output": c.get("output", ""),
                                "ok": True, "stream": True,
                                "channel": c.get("channel", "stdout"),
                                "action_id": act.id,
                            })
                        res = await asyncio.wait_for(
                            tools.run_action(act, sw=screen_width, sh=screen_height, on_stream=_stream_chunk),
                            timeout=120.0
                        )
                    except Exception as e:
                        # Structured tool-error reflection: synthesize a
                        # message back into the loop so the model can adapt
                        # on the next iteration instead of just seeing a
                        # terse "Error:" line.
                        err_str = str(e)
                        res = ToolResult(ok=False, output=f"Error: {err_str}")
                        try:
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"<tool_error tool=\"{act.type.value}\">\n"
                                    f"The previous action failed.\n"
                                    f"Args: {str(args)[:300]}\n"
                                    f"Error: {err_str}\n"
                                    f"Reflect briefly: was the target wrong, the args malformed, or the environment in an unexpected state? Try a different strategy or a smaller step."
                                    f"\n</tool_error>"
                                ),
                            })
                        except NameError:
                            # `messages` not in scope here for some code paths; ignore.
                            pass
                        
                    await self._emit(task_id, "action_result", {
                        "action_id": act.id,
                        "ok": res.ok,
                        "output": res.output,
                        "action_type": act.type.value,
                        "args_summary": _arg_summary,
                    })
                    
                    # ── Populate write cache so subsequent reads are free ──
                    if act.type == AT.write_file and res.ok:
                        _write_path = args.get("path", "")
                        _write_content = args.get("content", "")
                        if _write_path:
                            _write_cache[_write_path] = _write_content

                    # ── Auto-screenshot after computer actions so model sees result ──
                    post_action_note = ""
                    explicit_screenshot = (
                        act.type == AT.screenshot
                        or (act.type == AT.computer and (act.args.get("action", "").strip().lower() == "screenshot"))
                    )
                    if _is_computer_desktop and explicit_screenshot and res.base64_image:
                        screenshot_b64 = res.base64_image
                        await self._emit(task_id, "screenshot", {
                            "data": res.base64_image,
                            "isolated": bool(is_isolated and tools.resolve_isolated_hwnd()),
                            "worker_id": "planner",
                        })
                    elif _should_capture_post_action(act, res, _is_computer_desktop):
                        await asyncio.sleep(0.35)
                        isolated_hwnd = tools.resolve_isolated_hwnd() if is_isolated else None
                        if isolated_hwnd:
                            from .providers import _capture_hwnd_screenshot_b64
                            post_shot = _capture_hwnd_screenshot_b64(isolated_hwnd)
                        else:
                            post_shot = _capture_screenshot_b64(screen_width, screen_height)
                        if post_shot:
                            await self._emit(task_id, "screenshot", {"data": post_shot, "isolated": bool(isolated_hwnd), "worker_id": "planner"})
                            screenshot_b64 = post_shot
                            no_effect_hint = _post_action_no_effect_hint(pre_action_screenshot, post_shot)
                            if no_effect_hint:
                                post_action_note = no_effect_hint
                                await self._emit(task_id, "status", {
                                    "message": "Desktop action produced no visible change. Re-checking focus and target on the next step.",
                                })

                    if _is_computer_desktop and is_isolated:
                        hung_info = tools.current_target_hung_info()
                        if hung_info:
                            title_hint = (hung_info.get("title") or isolated_app or "target app").replace('"', "'")
                            hung_note = (
                                f"[hung-app hint] The target window '{title_hint}' appears not responding. "
                                f"Consider force_close_window {{\"title\": \"{title_hint}\", \"force\": true}} and then relaunch it."
                            )
                            post_action_note = f"{post_action_note}\n{hung_note}".strip() if post_action_note else hung_note
                            await self._emit(task_id, "status", {
                                "message": f"Target app '{title_hint}' appears hung. Consider closing and relaunching it.",
                            })

                    # ── Append result to conversation ──
                    obs_limit = 3000 if is_coding_mode else 1000
                    obs_text = res.output[:obs_limit] + ("\n...(truncated)" if len(res.output) > obs_limit else "")
                    if post_action_note:
                        obs_text = f"{obs_text}\n\n{post_action_note}"
                    if use_native_tools and tool_call_id:
                        messages.append({"role": "assistant", "content": thought_text, "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": action_type, "arguments": json.dumps(args)}}]})
                        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": obs_text})
                    else:
                        messages.append({"role": "assistant", "content": f"<thought>{thought_text}</thought>\n<action type=\"{action_type}\">\n{json.dumps(args)}\n</action>"})
                        messages.append({"role": "user", "content": f"<observation>\n{obs_text}\n</observation>"})
                    
                    # Update budget (chars/4 is a better token approximation than word count)
                    provider._total_input_tokens += len(messages[-1].get("content", "")) // 4
                    provider._total_output_tokens += len(thought_text) // 4
                    if await self._check_token_budget(task_id, provider, token_budget):
                        return
                    
                    if act.type == AT.finish:
                        self._finalize(task_id, "done", res.output)
                        await self._emit(task_id, "done", {"complete": True, "reason": res.output, "finished_at": datetime.now(timezone.utc).isoformat()})
                        await asyncio.to_thread(self.memory.summarize_session, task_id, goal, True, res.output, mode)
                        asyncio.create_task(asyncio.to_thread(self.memory.maybe_auto_consolidate))
                        return

                if self.is_killed(task_id):
                    reason = "Task killed by user."
                    self._finalize(task_id, "cancelled", reason)
                    await self._emit(task_id, "cancelled", {"message": reason, "finished_at": datetime.now(timezone.utc).isoformat()})
                    return

                # If we loop max_steps times and don't finish
                reason = f"Max steps reached ({max_steps}) without finish action."
                self._finalize(task_id, "failed", reason)
                await self._emit(task_id, "done", {"complete": False, "reason": reason, "finished_at": datetime.now(timezone.utc).isoformat()})
                await asyncio.to_thread(self.memory.summarize_session, task_id, goal, False, reason, mode)
                asyncio.create_task(asyncio.to_thread(self.memory.maybe_auto_consolidate))

        except asyncio.CancelledError:
            if self.is_killed(task_id):
                reason = "Task killed by user."
            else:
                reason = "Task cancelled by user."
            self._finalize(task_id, "cancelled", reason)
            await self._emit(task_id, "cancelled", {"message": reason, "finished_at": datetime.now(timezone.utc).isoformat()})
            raise
        except Exception as e:
            _log.exception("Task Execution Failed")
            await self._emit(task_id, "error", {"message": str(e)})
            self._finalize(task_id, "failed", str(e))
        finally:
            self._active_tasks.pop(task_id, None)
            # Remove task from killed-set so it doesn't grow unboundedly
            self._killed_tasks.discard(task_id)
            self._task_environments.pop(task_id, None)
            # Clean up browser (Playwright Chromium) — critical: zombie Chromium
            # processes survive Python crashes and hold gigabytes of RAM
            browser = self._bg_browsers.pop(task_id, None)
            if browser:
                try:
                    await browser.stop()
                except Exception:
                    pass
            self._task_tools.pop(task_id, None)
            # Force a GC cycle so freed screenshot buffers and message history
            # are collected immediately instead of accumulating across tasks
            import gc
            gc.collect()


    def _finalize(self, task_id: str, status: str, reason: str = ""):
        if self._on_task_complete: self._on_task_complete(task_id, status, reason)
        self._paused_tasks.discard(task_id)
        self._approvals.pop(task_id, None)
        for key in [k for k in self._approval_overrides if k.startswith(f"{task_id}:")]:
            self._approval_overrides.pop(key, None)
        self._permission_waits.pop(task_id, None)
        self._pause_events.pop(task_id, None)

    async def _wait_for_approval(self, task_id: str, action_id: str) -> bool:
        fut = self._approvals.setdefault(f"{task_id}:{action_id}", asyncio.Future())
        try:
            return await asyncio.wait_for(fut, timeout=APPROVAL_WAIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._emit(task_id, "approval_timeout", {"action_id": action_id, "timeout_seconds": APPROVAL_WAIT_TIMEOUT_SECONDS})
            return False
        finally:
            self._approvals.pop(f"{task_id}:{action_id}", None)

    def submit_approval(self, task_id: str, action_id: str, approved: bool, plan_override: str = ""):
        if plan_override:
            self._approval_overrides[f"{task_id}:{action_id}"] = plan_override
        fut = self._approvals.get(f"{task_id}:{action_id}")
        if fut and not fut.done():
            fut.set_result(approved)

    async def _wait_for_permission(self, task_id: str, action_id: str) -> bool:
        fut = self._permission_waits.setdefault(f"{task_id}:{action_id}", asyncio.Future())
        try:
            return await asyncio.wait_for(fut, timeout=PERMISSION_WAIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._emit(task_id, "permission_timeout", {"action_id": action_id, "timeout_seconds": PERMISSION_WAIT_TIMEOUT_SECONDS})
            return False
        finally:
            self._permission_waits.pop(f"{task_id}:{action_id}", None)

    def submit_permission(self, task_id: str, action_id: str, granted: bool):
        fut = self._permission_waits.get(f"{task_id}:{action_id}")
        if fut and not fut.done():
            fut.set_result(granted)

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

def _build_environment_payload(workspace: Path, home_dir: Path, *, project_folder_selected: bool) -> Dict[str, Any]:
    import platform

    home_dir = home_dir.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "home": str(home_dir),
        "workspace": str(workspace),
        "desktop": str(home_dir / "Desktop"),
        "downloads": str(home_dir / "Downloads"),
        "documents": str(home_dir / "Documents"),
        "user": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
        "python": "python" if platform.system() == "Windows" else "python3",
        "project_folder_selected": project_folder_selected,
    }


def _environment_context_text(environment: Dict[str, Any]) -> str:
    if not environment:
        return ""

    lines = ["\n\nSystem environment:"]
    ordered_keys = [
        ("os", "OS"),
        ("platform", "Platform"),
        ("home", "Home"),
        ("workspace", "Workspace hint"),
        ("desktop", "Desktop"),
        ("downloads", "Downloads"),
        ("documents", "Documents"),
        ("python", "Python command"),
        ("user", "User"),
    ]
    for key, label in ordered_keys:
        value = environment.get(key)
        if value:
            lines.append(f"- {label}: {value}")
    selection_state = "selected" if environment.get("project_folder_selected") else "not selected"
    lines.append(f"- Project folder: {selection_state}")
    return "\n".join(lines)

def _summarize_args(action_type: str, args: dict) -> str:
    """Human-readable one-line summary of tool args. Never returns a raw dict repr."""
    if not isinstance(args, dict):
        return str(args)[:80]
    if action_type in ("run_command", "bash"): return (args.get("command") or "")[:80]
    if action_type == "delegate_coding": return (args.get("task") or "")[:80]
    if action_type == "wait_for_window": return (args.get("title") or "wait for isolated window")[:80]
    if action_type == "force_close_window": return (args.get("title") or f"pid {args.get('pid', '?')}")[:80]
    if action_type == "text_editor": return f"{args.get('command','')} {args.get('path','')}".strip()
    if action_type in ("read_file", "write_file", "move_file"): return args.get("path") or args.get("src") or ""
    # Generic clean fallback — surface a meaningful value, never a Python dict repr.
    for key in ("path", "query", "url", "name", "target", "command", "text", "content"):
        val = args.get(key)
        if val:
            return str(val)[:80]
    return ", ".join(str(k) for k in args.keys())[:80]

_SKIP_DIRS = frozenset({'__pycache__', 'node_modules', '.gemini', '.claude', '.git', 'venv', '.venv', 'dist', 'build', '.tempmediaStorage', '.idea', '.vscode', '.cache', 'coverage', '.pytest_cache', 'htmlcov', 'egg-info'})

# M4: Module-level cache for workspace tree (valid for the process lifetime; workspace doesn't change)
_workspace_tree_cache: dict = {}

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

