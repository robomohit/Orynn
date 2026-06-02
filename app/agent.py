from __future__ import annotations
import asyncio
import base64
import inspect
import io
import json
import logging
import os
import re
import subprocess
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
# Desktop/computer tasks click individual controls (one step each), read each
# outcome back to verify, and may need to undo+redo to recover from a misstep —
# so they legitimately need MORE budget than a generic agent task, not less. 25
# was cutting off correct recoveries mid-way (e.g. a chained calculation).
DESKTOP_MAX_STEPS = int(os.environ.get("DESKTOP_MAX_STEPS", "40"))

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

_UIA_ACTION_TYPES = {
    ActionType.uia_find,
    ActionType.uia_click,
    ActionType.uia_click_sequence,
    ActionType.uia_type,
    ActionType.uia_wait,
}

_VISUAL_DESKTOP_ACTION_TYPES = {
    ActionType.screenshot,
    ActionType.screen_context,
    ActionType.mouse_click,
    ActionType.double_click,
    ActionType.right_click,
    ActionType.middle_click,
    ActionType.mouse_move,
    ActionType.left_click_drag,
    ActionType.keyboard_type,
    ActionType.type_with_delay,
    ActionType.scroll,
    ActionType.find_on_screen,
    ActionType.computer,
}

_TEXT_ONLY_DESKTOP_TOOL_EXCLUDES = _VISUAL_DESKTOP_ACTION_TYPES | {
    ActionType.ocr_image,
    ActionType.pixel_color_at,
    ActionType.ui_critique,
}


def _goal_requests_screen_context(goal: str) -> bool:
    text = (goal or "").lower()
    screen_terms = (
        "look at my screen",
        "see my screen",
        "explain my screen",
        "explain screen",
        "summarize my screen",
        "summarize this page",
        "what am i looking at",
        "what is on my screen",
        "what's on my screen",
        "current screen",
        "this screen",
        "screen context",
    )
    return any(term in text for term in screen_terms)


def _tool_excludes_for_control_route(mode: str, model_sees: bool, goal: str = "") -> set[ActionType]:
    if mode in ("computer", "computer_isolated") and not model_sees:
        excluded = set(_TEXT_ONLY_DESKTOP_TOOL_EXCLUDES)
        if _goal_requests_screen_context(goal):
            excluded.discard(ActionType.screen_context)
        return excluded
    return set()

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


_FILE_WRITE_TYPES = frozenset({"write_file", "text_create", "text_str_replace", "text_insert"})


def _git_commit_file(file_path: str, workspace: Path, action_type: str, task_id: str = "") -> Optional[str]:
    """Auto-commit one file to git. Returns short hash or None (non-git / nothing staged / hook blocked)."""
    try:
        ws = workspace.expanduser().resolve()
        if subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(ws), capture_output=True, check=False
        ).returncode != 0:
            return None
        abs_path = file_path if os.path.isabs(file_path) else str(ws / file_path)
        subprocess.run(["git", "add", abs_path], cwd=str(ws), capture_output=True, check=False)
        subject = f"[ai-computer] {action_type}: {os.path.basename(file_path)}"
        msg = f"{subject}\n\ntask: {task_id[:8]}" if task_id else subject
        commit = subprocess.run(
            ["git", "commit", "-m", msg], cwd=str(ws), capture_output=True, text=True, check=False
        )
        if commit.returncode != 0:
            return None
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=False
        )
        return rev.stdout.strip() or None
    except Exception:
        return None


def _overlay_for_action_start(action: Action, *, visual_fallback: bool = False) -> Optional[Dict[str, Any]]:
    """Structured live overlay hint for the capsule before a tool returns."""
    tool = action.type.value
    args = action.args if isinstance(action.args, dict) else {}
    if action.type in _UIA_ACTION_TYPES:
        query = str(args.get("query") or "").strip()
        if action.type == ActionType.uia_click_sequence:
            raw_targets = args.get("targets") or args.get("queries") or args.get("query") or []
            if isinstance(raw_targets, str):
                targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
            else:
                targets = [str(t).strip() for t in (raw_targets or []) if str(t).strip()]
            target = ", ".join(targets[:4])
            if len(targets) > 4:
                target += f", +{len(targets) - 4} more"
            label = (
                f"Clicking {len(targets)} controls in sequence"
                if targets else "Clicking controls in sequence"
            )
            kind = "click"
            query = target
        elif action.type == ActionType.uia_click:
            label = f"Locating {query} to click" if query else "Locating control to click"
            kind = "click"
        elif action.type == ActionType.uia_type:
            label = f"Locating {query} to type" if query else "Locating field to type"
            kind = "type"
        elif action.type == ActionType.uia_wait:
            label = f"Waiting for {query}" if query else "Waiting for control"
            kind = "wait"
        else:
            label = f"Locating {query}" if query else "Locating control"
            kind = "find"
        return {
            "type": "status",
            "tool": tool,
            "kind": kind,
            "phase": "start",
            "label": label,
            "target": query,
            "app": str(args.get("app") or ""),
            "control_layer": "UIA exact",
            "control_reason": "querying Windows accessibility tree",
        }

    if action.type == ActionType.electron_check:
        return {
            "type": "status",
            "tool": tool,
            "kind": "inspect",
            "phase": "start",
            "label": "Checking Electron accessibility",
            "target": str(args.get("exe") or ""),
            "control_layer": "Electron probe",
            "control_reason": "detecting Chromium/Electron app shell",
        }

    if action.type == ActionType.electron_unlock:
        return {
            "type": "status",
            "tool": tool,
            "kind": "unlock",
            "phase": "start",
            "label": "Unlocking Electron accessibility",
            "target": str(args.get("exe") or ""),
            "control_layer": "Electron unlock",
            "control_reason": "enabling UIA for Electron controls",
        }

    point = None
    point_kind = "click"
    point_label = "Clicking"
    if action.type in {
        ActionType.mouse_click,
        ActionType.double_click,
        ActionType.right_click,
        ActionType.middle_click,
        ActionType.mouse_move,
        ActionType.left_click_drag,
    }:
        try:
            point = {"x": int(args["x"]), "y": int(args["y"])}
        except Exception:
            point = None
        if action.type == ActionType.double_click:
            point_kind, point_label = "double_click", "Double-clicking"
        elif action.type == ActionType.right_click:
            point_kind, point_label = "click", "Right-clicking"
        elif action.type == ActionType.middle_click:
            point_kind, point_label = "click", "Middle-clicking"
        elif action.type == ActionType.mouse_move:
            point_kind, point_label = "move", "Moving cursor"
        elif action.type == ActionType.left_click_drag:
            point_kind, point_label = "drag", "Dragging"
    elif action.type == ActionType.computer:
        try:
            if "x" in args and "y" in args:
                point = {"x": int(args["x"]), "y": int(args["y"])}
        except Exception:
            point = None
        computer_action = str(args.get("action") or "").strip().lower()
        if computer_action == "double_click":
            point_kind, point_label = "double_click", "Double-clicking"
        elif computer_action == "right_click":
            point_kind, point_label = "click", "Right-clicking"
        elif computer_action == "middle_click":
            point_kind, point_label = "click", "Middle-clicking"
        elif computer_action == "mouse_move":
            point_kind, point_label = "move", "Moving cursor"
        elif computer_action == "left_click_drag":
            point_kind, point_label = "drag", "Dragging"

    if visual_fallback:
        label = "No accessible control found; using visual fallback"
        return {
            "type": "point" if point else "status",
            "tool": tool,
            "kind": "fallback",
            "phase": "start",
            "label": label,
            "point": point,
            "fallback_reason": "uia_no_match",
            "control_layer": "Screenshot fallback",
            "control_reason": "previous UIA target failed",
        }

    if point:
        return {
            "type": "point",
            "tool": tool,
            "kind": point_kind,
            "phase": "start",
            "label": point_label,
            "point": point,
            "control_layer": "Screenshot fallback",
            "control_reason": "desktop pixel coordinate action",
        }

    return None


def _overlay_from_result(result: ToolResult) -> Optional[Dict[str, Any]]:
    data = getattr(result, "data", None)
    if isinstance(data, dict) and isinstance(data.get("overlay"), dict):
        return data["overlay"]
    return None


def _desktop_control_profile(
    app_hint: str = "",
    *,
    isolated: bool = False,
    model_sees: bool = False,
) -> Dict[str, Any]:
    """Cheap local routing profile for desktop tasks before the first model turn."""
    target = (app_hint or "").strip()
    profile: Dict[str, Any] = {
        "target_app": target,
        "isolated": bool(isolated),
        "model_vision": bool(model_sees),
        "window_found": False,
        "app_rect": None,
        "uia_control_count": 0,
        "controls": [],
        "ocr_available": False,
        "electron_hint": None,
    }
    try:
        from .widget.desktop_features import (
            app_window_rect,
            survey_app_controls,
            electron_hint_for_app,
            ocr_available,
        )
        if target:
            try:
                rect = app_window_rect(target)
                if isinstance(rect, dict):
                    norm_rect = {
                        "left": int(rect.get("left", 0)),
                        "top": int(rect.get("top", 0)),
                        "width": int(rect.get("width", 0)),
                        "height": int(rect.get("height", 0)),
                    }
                    profile["app_rect"] = norm_rect
                    profile["window_found"] = norm_rect["width"] > 0 and norm_rect["height"] > 0
            except Exception:
                pass
            try:
                # ONE tree walk → control count AND the names of clickable
                # controls. Handing the model this 'menu' up front stops it
                # guessing control names that don't exist (big accuracy + speed
                # win on unfamiliar apps like Settings).
                survey = survey_app_controls(target, cap=90)
                profile["uia_control_count"] = int(survey.get("count") or 0)
                profile["controls"] = survey.get("controls") or []
            except Exception:
                pass
            try:
                profile["electron_hint"] = electron_hint_for_app(target)
            except Exception:
                pass
        try:
            profile["ocr_available"] = bool(ocr_available())
        except Exception:
            pass
    except Exception:
        pass

    count = int(profile.get("uia_control_count") or 0)
    if target and count >= 12:
        route = "UIA exact"
    elif target and profile.get("electron_hint"):
        route = "Electron unlock"
    elif not target:
        route = "UIA exact"
    elif profile.get("ocr_available"):
        route = "OCR fallback"
    elif model_sees:
        route = "Screenshot fallback"
    else:
        route = "UIA degraded"
    profile["primary_route"] = route
    return profile


def _desktop_control_profile_text(profile: Dict[str, Any]) -> str:
    if not profile:
        return ""
    target = profile.get("target_app") or "not fixed (full desktop)"
    route = profile.get("primary_route") or "UIA exact"
    count = int(profile.get("uia_control_count") or 0)
    ocr = "available" if profile.get("ocr_available") else "unavailable"
    vision = "available" if profile.get("model_vision") else "skipped for this text-only/UIA route"
    lines = [
        "",
        "Desktop control readiness:",
        f"- Target app: {target}",
        f"- Primary route: {route}",
        f"- UIA controls visible now: {count}",
        f"- OCR fallback: {ocr}",
        f"- Screenshot/vision fallback: {vision}",
    ]
    controls = profile.get("controls") or []
    if controls:
        shown = ", ".join(controls[:24])
        lines.append(
            f"- Clickable controls available NOW (use these EXACT names with "
            f"uia_click/uia_type — do not guess others): {shown}"
        )
    if profile.get("target_app") and not profile.get("window_found"):
        lines.append("- Target window is not attached yet; open/focus it, then use uia_wait.")
    hint = profile.get("electron_hint")
    if isinstance(hint, dict):
        exe = hint.get("exe") or profile.get("target_app") or "the app"
        lines.append(
            "- Electron note: if uia_find/uia_wait returns no controls, call "
            f"electron_unlock with {exe} before using screenshot coordinates."
        )
    return "\n".join(lines)


def _desktop_control_profile_status(profile: Dict[str, Any]) -> str:
    route = profile.get("primary_route") or "UIA exact"
    target = profile.get("target_app") or "desktop"
    count = int(profile.get("uia_control_count") or 0)
    ocr = "OCR ready" if profile.get("ocr_available") else "OCR unavailable"
    return f"Desktop control route: {route} for {target} ({count} UIA controls, {ocr})."


def _goal_explicitly_allows_visual_action(goal: str, action: Action) -> bool:
    text = (goal or "").lower()
    if action.type == ActionType.screen_context:
        return _goal_requests_screen_context(goal)
    if action.type == ActionType.screenshot:
        return any(term in text for term in ("screenshot", "screen shot", "capture the screen"))
    if action.type == ActionType.computer:
        sub = str(action.args.get("action") or "").strip().lower()
        if sub in {"wait", "cursor_position"}:
            return True
        if sub == "screenshot":
            return any(term in text for term in ("screenshot", "screen shot", "capture the screen"))
    coordinate_terms = ("coordinate", "coords", "pixel", "at x", "x=", "y=")
    if any(term in text for term in coordinate_terms):
        return True
    return bool(re.search(r"\b\d{2,4}\s*,\s*\d{2,4}\b", text))


def _should_guard_premature_visual_action(
    action: Action,
    *,
    goal: str,
    is_desktop: bool,
    model_sees: bool,
    last_uia_failed: bool,
) -> bool:
    if not is_desktop or model_sees or last_uia_failed:
        return False
    if action.type not in _VISUAL_DESKTOP_ACTION_TYPES:
        return False
    if _goal_explicitly_allows_visual_action(goal, action):
        return False
    return True


def _visual_route_guard_message(action: Action) -> str:
    return (
        "[control-route guard] Skipped premature screenshot/pixel fallback "
        f"({action.type.value}). This run is using the fast text-only UIA route, "
        "so first use uia_find/uia_wait with the target app, then uia_click, "
        "uia_type, or uia_click_sequence by visible control name. Use screenshot "
        "or coordinate clicks only after a UIA/OCR miss, or when the user "
        "explicitly asks for a screenshot or coordinates."
    )


def _visual_route_guard_overlay(action: Action) -> Dict[str, Any]:
    return {
        "type": "status",
        "tool": action.type.value,
        "kind": "guard",
        "phase": "blocked",
        "label": "Skipped premature screenshot fallback",
        "target": _summarize_args(action.type.value, action.args),
        "fallback_reason": "premature_visual_action",
        "control_layer": "UIA guard",
        "control_reason": "text-only desktop route requires UIA/OCR before pixel fallback",
    }


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
        model_sees: bool = True,
        goal: str = "",
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
        self.model_sees = bool(model_sees)
        self.goal = goal or sub_task.description

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
        is_desktop = self.mode in ("computer", "computer_isolated")
        last_uia_failed = False

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

                if _should_guard_premature_visual_action(
                    action,
                    goal=self.goal,
                    is_desktop=is_desktop,
                    model_sees=self.model_sees,
                    last_uia_failed=last_uia_failed,
                ):
                    guard_obs = _visual_route_guard_message(action)
                    if is_isolated:
                        try:
                            hung_info = tools.current_target_hung_info()
                        except Exception:
                            hung_info = None
                        if hung_info:
                            title_hint = (hung_info.get("title") or "target app").replace('"', "'")
                            guard_obs = (
                                f"{guard_obs}\n\n[hung-app hint] The target window "
                                f"'{title_hint}' appears not responding. Consider "
                                f"force_close_window {{\"title\": \"{title_hint}\", "
                                "\"force\": true}} and then relaunch it."
                            )
                    guard_overlay = _visual_route_guard_overlay(action)
                    arg_summary = _summarize_args(action.type.value, action.args)
                    await self._emit("status", {
                        "message": "Keeping this planned run on the UIA route before using screenshot fallback.",
                    })
                    await self._emit("action_start", {
                        "action_id": action.id,
                        "action_type": action.type.value,
                        "explanation": action.explanation,
                        "args_summary": arg_summary,
                        "overlay": guard_overlay,
                    })
                    await self._emit("action_result", {
                        "action_id": action.id,
                        "ok": False,
                        "output": guard_obs,
                        "action_type": action.type.value,
                        "args_summary": arg_summary,
                        "overlay": guard_overlay,
                    })
                    results.append(guard_obs)
                    guarded_action = action.model_dump()
                    guarded_action["ok"] = False
                    actions_taken.append(guarded_action)
                    history.append(f"[{self.worker_id}] Guarded: {action.type.value} -> {guard_obs}")
                    self.sub_task.status = TaskStatus.failed
                    self.sub_task.error = guard_obs
                    await self._emit("subtask", {
                        "subtask_id": self.sub_task.id,
                        "status": "failed",
                        "reason": guard_obs,
                    })
                    return False

                decision = self.agent_service.safety.evaluate(action, safe_mode=not (is_coding or self.auto_approve))

                _start_overlay = _overlay_for_action_start(action)
                await self._emit("intent", {
                    "action_id": action.id,
                    "action_type": action.type.value,
                    "explanation": action.explanation,
                    "args_preview": _summarize_args(action.type.value, action.args),
                    **({"overlay": _start_overlay} if _start_overlay else {}),
                })
                await self._emit("action_start", {
                    "action_id": action.id,
                    "action_type": action.type.value,
                    "explanation": action.explanation,
                    "args_summary": _summarize_args(action.type.value, action.args),
                    **({"overlay": _start_overlay} if _start_overlay else {}),
                })

                # Permission & Approval logic (reusing AgentService helpers)
                granted, denied_scope = await self.agent_service._ensure_permission_for_action(
                    self.task_id,
                    action,
                    auto_grant=is_coding or self.auto_approve,
                    emit=self._emit,
                    args_summary=_summarize_args(action.type.value, action.args),
                )
                if not granted:
                    raise RuntimeError(f"Permission denied for {denied_scope or 'requested scope'}")

                if action.requires_approval or decision.requires_approval:
                    self.agent_service._prepare_approval_wait(self.task_id, action.id)
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
                action_record = action.model_dump()
                action_record["ok"] = bool(res.ok)
                actions_taken.append(action_record)
                await asyncio.to_thread(self.agent_service.memory.add_action_result, self.task_id, action.id, res.output)
                history.append(f"[{self.worker_id}] Action: {action.type.value} -> {res.output}")

                _result_overlay = _overlay_from_result(res)
                await self._emit("action_result", {
                    "action_id": action.id,
                    "ok": res.ok,
                    "output": res.output,
                    "action_type": action.type.value,
                    "args_summary": _summarize_args(action.type.value, action.args),
                    **({"overlay": _result_overlay} if _result_overlay else {}),
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

                if action.type in _UIA_ACTION_TYPES:
                    last_uia_failed = not res.ok
                elif action.type in _VISUAL_DESKTOP_ACTION_TYPES:
                    last_uia_failed = False

                # Special Mode Handling (Screenshots, File Changes)
                if is_coding:
                    if action.type.value in ("write_file", "text_create", "text_str_replace", "text_insert"):
                        await self._emit("file_change", {
                            "path": action.args.get("path", ""),
                            "action": action.type.value,
                            "content": action.args.get("content", action.args.get("file_text", "")),
                        })
                elif not is_computer_use:
                    if action.type in _SCREENSHOT_ACTIONS or action.type in {ActionType.screenshot, ActionType.screen_context}:
                        is_context_capture = action.type == ActionType.screen_context
                        if is_isolated and not is_context_capture:
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
                reflect_screenshot = None if (is_coding or is_computer_use or not self.model_sees) else _capture_screenshot_b64(self.screen_width, self.screen_height)
                reflect_excludes = _tool_excludes_for_control_route(self.mode, self.model_sees, self.goal)
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
                    exclude_actions=reflect_excludes,
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
                        if _should_guard_premature_visual_action(
                            retry_action,
                            goal=self.goal,
                            is_desktop=is_desktop,
                            model_sees=self.model_sees,
                            last_uia_failed=last_uia_failed,
                        ):
                            guard_obs = _visual_route_guard_message(retry_action)
                            retry_results.append(guard_obs)
                            guarded_retry = retry_action.model_dump()
                            guarded_retry["ok"] = False
                            retry_taken.append(guarded_retry)
                            history.append(f"[{self.worker_id}] Guarded retry: {retry_action.type.value} -> {guard_obs}")
                            break
                        retry_res = await asyncio.wait_for(
                            tools.run_action(retry_action, sw=self.screen_width, sh=self.screen_height),
                            timeout=300.0 if is_coding else 120.0,
                        )
                        retry_results.append(retry_res.output)
                        retry_record = retry_action.model_dump()
                        retry_record["ok"] = bool(retry_res.ok)
                        retry_taken.append(retry_record)
                        history.append(f"[{self.worker_id}] Retry: {retry_action.type.value} -> {retry_res.output}")
                        if retry_action.type in _UIA_ACTION_TYPES:
                            last_uia_failed = not retry_res.ok
                        elif retry_action.type in _VISUAL_DESKTOP_ACTION_TYPES:
                            last_uia_failed = False
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
                        exclude_actions=reflect_excludes,
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
        thinking_budget: str = "off",
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
        environment_payload["thinking_budget"] = thinking_budget or "off"
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
            thinking_budget=thinking_budget or "off",
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
                thinking_budget=thinking_budget or "off",
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
        thinking_budget: str = "off",
    ):
        provider_override = None
        if not isinstance(screen_width, int) and hasattr(screen_width, "stream_chat"):
            provider_override = screen_width
            screen_width = 1280
        active_skills = list(active_skills or [])
        provider = provider_override or _new_planner_provider(model)
        provider.thinking_budget = thinking_budget or "off"
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
        # Skills + connectors, Claude/OpenClaw-style PROGRESSIVE DISCLOSURE:
        #   L1 (always): a compact menu of what's available + WHEN to use it, so
        #       the agent knows the surfaces/skills exist (cheap, ~1 line each).
        #   L2 (on match): the FULL manual, but only for the connector(s) this
        #       goal actually needs — a connector is the tool, its skill the
        #       manual. Works for every entry point (capsule, dashboard, API).
        try:
            from . import connectors as _connectors
            _menu = _connectors.skill_menu()
            _all_skills = skill_manager.get_all_skills()
            _lines = [f"- {label}: {when}" for label, when in _menu if when]
            _lines += [f"- {sk.name}: {sk.description}"
                       for sk in (_all_skills or []) if sk.description]
            if _lines:
                skill_instructions += (
                    "\n\n### AVAILABLE SKILLS & CONNECTORS "
                    "(reach for these when the task calls for them)\n"
                    + "\n".join(_lines) + "\n")
            _briefs = _connectors.relevant_briefs(goal)
            if _briefs:
                skill_instructions += (
                    "\n\n### CONNECTOR MANUALS (how to use the services this "
                    "task needs)\n")
                for _label, _manual in _briefs:
                    skill_instructions += f"\n**{_label}**\n{_manual}\n"
        except Exception:
            pass
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
                self._prepare_approval_wait(task_id, "__plan__")
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

            _model_sees = is_vision_model(getattr(provider, "model", model))
            control_context = ""
            if mode in ("computer", "computer_isolated"):
                control_app = isolated_app if is_isolated else (infer_isolated_app_name(goal) or "")
                control_profile = await asyncio.to_thread(
                    _desktop_control_profile,
                    control_app,
                    isolated=is_isolated,
                    model_sees=_model_sees,
                )
                control_context = _desktop_control_profile_text(control_profile)
                await self._emit(task_id, "control_profile", control_profile)
                await self._emit(task_id, "status", {
                    "message": _desktop_control_profile_status(control_profile),
                    "elapsed_seconds": 0,
                })

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

            
            # No screenshots for coding/chat — only for computer/desktop modes.
            # Also skip when the model can't see (UIA tier uses a fast text-only
            # tool-calling model): a screenshot would be wasted tokens at best,
            # or break the request at worst. UIA drives the desktop blind by
            # control name, so no pixels are needed.
            if runs_in_background or is_coding_mode or not _model_sees:
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
                        plan_tool_excludes = _tool_excludes_for_control_route(mode, _model_sees, goal)
                        prompt = f"Goal: {goal}\n\nReturn one concise JSON plan using only these tools:\n{get_tool_guidance(packs, exclude_actions=plan_tool_excludes)}"
                        raw_plan = provider._call_llm("You are a fast-path planning agent. Return only JSON.", prompt, screenshot_b64)
                        plan = HierarchicalPlan.model_validate(_normalize_hierarchical_plan(_extract_json(raw_plan)))
                    else:
                        plan = provider.plan_hierarchical(
                            goal,
                            latest_screenshot_b64=screenshot_b64,
                            memory_context=mem_context,
                            mode=mode,
                            system_prompt_extension=skill_instructions or None,
                            exclude_actions=_tool_excludes_for_control_route(mode, _model_sees, goal),
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
                            model_sees=_model_sees,
                            goal=goal,
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
                    await self._emit(task_id, "usage_update", {"total_tokens": provider.total_tokens})
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
                tool_excludes = _tool_excludes_for_control_route(mode, _model_sees, goal)
                tool_guidance = get_tool_guidance(packs, exclude_actions=tool_excludes)
                tool_schemas = get_tool_schemas(packs, exclude_actions=tool_excludes)

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
                        "You are AI Computer — a desktop automation agent controlling a real Windows PC "
                        "through Windows UI Automation (UIA). You drive apps by the NAME of their on-screen "
                        "controls — no screenshots, no pixel coordinates, no guessing.\n\n"
                        "DESKTOP CONTROL WORKFLOW:\n"
                        "1. If the target app is already open, go straight to step 3. To open an app, use "
                        "run_command with the Windows `start` command (e.g. run_command {\"command\": \"start notepad\"}). "
                        "It returns immediately — never wait on it. Then `uia_wait` for one of its controls to appear.\n"
                        "2. Bring the app forward with focus_window (e.g. focus_window {\"title\": \"Notepad\"}).\n"
                        "3. Find what you want with `uia_find` using the control's visible NAME and the app "
                        "title (e.g. uia_find {\"query\": \"Text editor\", \"app\": \"Notepad\"}). It returns the "
                        "matching controls — never guess coordinates.\n"
                        "4. Act by NAME — the tools resolve the target for you through three layers "
                        "automatically (UIA exact → on-screen-text OCR → only then pixel), so just call "
                        "them with the visible label; you do NOT need to screenshot or compute coordinates:\n"
                        "   - `uia_click` to press a button / open a menu / select a channel. If there's no "
                        "accessible control it auto-falls-back to matching the on-screen TEXT and clicking it.\n"
                        "   - `uia_type` to enter text into a field. Pass app=<window title>. "
                        "clear_first=true replaces existing text; submit=true presses Enter afterwards. It "
                        "VERIFIES the text landed and reports '(verified)'.\n"
                        "   The result tells you which layer was used (UIA exact / OCR fallback). Trust these "
                        "tools; only take a manual `screenshot` if a tool reports it found NO match at all.\n"
                        "5. VERIFY THE OUTCOME before you finish. Don't assume an action worked — read the "
                        "concrete end-state back. `uia_find` the result/field/display and look at its actual "
                        "text (e.g. the Calculator's display, the file in the title bar, the message in the "
                        "channel). If the observed value contradicts the goal (e.g. the display shows 0 when you "
                        "expected a product), you made a mistake earlier — fix it and re-verify, don't paper over "
                        "it.\n"
                        "6. ELECTRON APPS (Discord, Slack, VS Code, Cursor, Spotify, Notion...): the uia tools "
                        "already try OCR on a miss, and when an app's DOM is locked to UIA they tell you so in "
                        "the result (an Electron unlock hint). Only THEN call `electron_unlock` — pass just the "
                        "app NAME (e.g. \"Discord\"); it resolves the running .exe and relaunches with "
                        "accessibility enabled. Wait a few seconds (the tree loads lazily) and retry. Don't "
                        "preemptively unlock an Electron app that's already responding to uia_find/uia_click.\n"
                        "7. When done, call finish with a short summary that STATES THE ACTUAL OBSERVED RESULT "
                        "you read back in step 5 — the real number/text/state, not a vague 'the result is "
                        "displayed'. If you could not confirm it, say so plainly.\n\n"
                        "RULES:\n"
                        "- FINISH ONLY WHEN EVERY CLAUSE OF THE GOAL IS DONE. Many goals chain steps: 'do X, "
                        "THEN Y', 'A and then B'. Completing the first clause is NOT the whole task — re-read the "
                        "original goal before finishing and make sure each part happened. E.g. 'compute (12+8) "
                        "then multiply by 5' is NOT done at 20; that's only the first half — continue with ×5 "
                        "until the display reads 100, THEN finish. Don't mistake an intermediate result for the "
                        "final answer.\n"
                        "- HONESTY: only report a step as done if its tool returned ok. If uia_find/uia_click "
                        "fails ('no UIA control matched'), DO NOT claim it worked — try a different control "
                        "name, uia_wait, or electron_unlock, and if still stuck say so plainly in finish. NEVER "
                        "claim a task succeeded without having read the concrete result back; a confident but "
                        "wrong 'done' is the worst possible outcome.\n"
                        "- MULTI-CLICK SEQUENCES → use uia_click_sequence (ONE call, no drift between clicks). "
                        "Any known run of buttons — Calculator digits/operators, tabbing a form — should go in a "
                        "single uia_click_sequence with the targets in order, NOT many separate uia_click calls. "
                        "Do NOT uia_type into the Calculator (no text field) and never invent names like "
                        "'Button::Digit 1' — use the accessible names (One..Nine, Plus, Minus, 'Multiply by', "
                        "'Divide by', Equals, Clear). 47×89 → uia_click_sequence targets "
                        "[Four,Seven,'Multiply by',Eight,Nine,Equals] (reads 4183). Chained (12+8)×5 → one "
                        "sequence [One,Two,Plus,Eight,Equals] (reads 20) then another [<read 20>,'Multiply by',"
                        "Five,Equals] → 100. After the sequence, uia_find the display and report the number.\n"
                        "- RECOVER, don't surrender: if your verified outcome is wrong or incomplete (e.g. you "
                        "clicked Clear by mistake and the display is 0), do the steps again correctly — do NOT "
                        "finish with a vague apology like 'I misunderstood' or 'next time'. Either deliver the "
                        "correct result, or finish by stating plainly the actual value you see and that it "
                        "doesn't match the goal. A defeatist non-answer is a failure.\n"
                        "- PREFER uia_find / uia_click / uia_type over screenshot + mouse_click. They are far "
                        "faster, cheaper (no image to the model), and self-heal via OCR before any pixel "
                        "guess. Reserve manual screenshot + coordinate clicks for genuinely visual targets "
                        "(canvas / game / icon with no text) AFTER a uia tool reports no match.\n"
                        "- ALWAYS pass the `app` window-title to uia_find/uia_click/uia_type so UIA targets the "
                        "right window even if focus didn't take.\n"
                        "- After navigating (opening an app, switching a channel/page), use `uia_wait` instead "
                        "of guessing — it returns the instant the next control is ready.\n"
                        "- Don't repeat an action that already succeeded. Don't loop.\n\n"
                        "CRITICAL SAFETY RULES:\n"
                        "- NEVER close, minimize, or interact with Google Chrome or Microsoft Edge — those are the monitoring dashboard.\n"
                        "- Do not interact with Cursor, browsers, File Explorer, or unrelated windows unless the user explicitly asks.\n"
                        "- NEVER send Alt+F4 unless explicitly asked to close a specific non-browser app.\n"
                        "- Do NOT click Send / Submit / Pay / Delete (or use submit=true on a message) unless the user clearly asked you to.\n\n"
                        "APP-SPECIFIC RULES:\n"
                        "- For Notepad/text-editor tasks, the editor control is named \"Text editor\".\n"
                        "- SAVING FILES: when a Save dialog opens, uia_type the FULL absolute path into the "
                        "\"File name\" field, then uia_click \"Save\". CRITICAL: resolve real folder paths with "
                        "PowerShell — Desktop/Documents are usually redirected to OneDrive, so "
                        "C:\\Users\\<name>\\Desktop is the WRONG (empty) folder on most machines. Get the true "
                        "path first with run_command {\"command\": \"powershell -Command "
                        "\\\"[Environment]::GetFolderPath('Desktop')\\\"\"} (or 'MyDocuments'), and build the "
                        "filename onto THAT (e.g. C:\\Users\\me\\OneDrive\\Desktop\\file.txt). After clicking "
                        "Save, VERIFY by checking the file exists on disk (list_directory / file_glob on that "
                        "resolved folder); if it's not there, the path was wrong — re-resolve and retry, don't "
                        "claim it saved.\n"
                        f"\nAvailable tools:\n{tool_guidance}\n"
                    )
                    xml_system = (
                        "You are AI Computer — a desktop automation agent driving a real Windows PC via UI Automation (UIA).\n"
                        "FORMAT: <thought>reasoning</thought> then <action type=\"tool\">{args}</action>\n\n"
                        "WORKFLOW: (open app via run_command \"start <app>\" only if not already open) → focus_window → "
                        "uia_find {query, app} → uia_click / uia_type {query, text, app, clear_first, submit} → uia_find to verify → finish\n"
                        "PREFER uia_find/uia_click/uia_type over screenshots and pixel clicks — faster and never mis-click. "
                        "Always pass the app window title. For Electron apps (Discord/Slack/VS Code) that return no controls, "
                        "electron_check then electron_unlock, then retry. Use uia_wait after navigating instead of sleeping.\n"
                        "SAFETY: NEVER close Chrome, Edge, Cursor, or unrelated windows. Do not send/submit/pay/delete unless asked.\n\n"
                        f"Available tools:\n{tool_guidance}\n\n"
                        "After each <observation>, decide the next step. Call finish as soon as the goal is achieved."
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
                messages = [{"role": "user", "content": f"{goal}{env_context}{auto_context}{control_context}{win_info}"}]
                use_native_tools = len(tool_schemas) > 0 and inspect.isasyncgenfunction(
                    getattr(provider, "stream_chat_with_tools", None)
                )
                
                # ── Anti-waste tracking: detect duplicate calls and cache writes ──
                _recent_calls: list[tuple[str, str]] = []  # (action_type, args_key) last 3 calls
                _write_cache: dict[str, str] = {}  # path → content of recently written files
                _last_uia_failed = False
                _preserve_screenshot_once = False
                xml_fallback_steps = 0
                max_steps = (
                    BROWSER_MAX_STEPS if _is_browser_use
                    else DESKTOP_MAX_STEPS if _is_computer_desktop
                    else AGENT_MAX_STEPS
                )

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

                    # ── Refresh screenshot each step for vision desktop mode ──
                    # UIA-tier text models drive controls by name and cannot use pixels.
                    if _preserve_screenshot_once:
                        _preserve_screenshot_once = False
                    elif _is_computer_desktop and _model_sees and step > 0:
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
                                    elif event["type"] == "tool_partial":
                                        _now = asyncio.get_running_loop().time()
                                        if _now - _last_reason_emit >= _REASON_MIN_INTERVAL:
                                            _last_reason_emit = _now
                                            _partial_name = event.get("name") or "tool"
                                            await self._emit(task_id, "reasoning", {
                                                "stage": f"Step {step+1} — composing",
                                                "summary": f"Composing {_partial_name}…",
                                                "detail": event.get("args_partial", "")[:200],
                                                "live": True,
                                                "elapsed_seconds": _step_elapsed(),
                                            })
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

                    if _should_guard_premature_visual_action(
                        act,
                        goal=goal,
                        is_desktop=_is_computer_desktop,
                        model_sees=_model_sees,
                        last_uia_failed=_last_uia_failed,
                    ):
                        _guard_obs = _visual_route_guard_message(act)
                        if is_isolated:
                            try:
                                hung_info = tools.current_target_hung_info()
                            except Exception:
                                hung_info = None
                            if hung_info:
                                title_hint = (hung_info.get("title") or isolated_app or "target app").replace('"', "'")
                                _guard_obs = (
                                    f"{_guard_obs}\n\n[hung-app hint] The target window "
                                    f"'{title_hint}' appears not responding. Consider "
                                    f"force_close_window {{\"title\": \"{title_hint}\", "
                                    "\"force\": true}} and then relaunch it."
                                )
                                await self._emit(task_id, "status", {
                                    "message": f"Target app '{title_hint}' appears hung. Consider closing and relaunching it.",
                                })
                        _guard_overlay = _visual_route_guard_overlay(act)
                        _arg_summary = _summarize_args(act.type.value, args)
                        await self._emit(task_id, "status", {
                            "message": "Keeping this run on the UIA route before using screenshot fallback.",
                        })
                        await self._emit(task_id, "action_start", {
                            "action_id": act.id,
                            "action_type": act.type.value,
                            "explanation": act.explanation,
                            "args_summary": _arg_summary,
                            "overlay": _guard_overlay,
                        })
                        await self._emit(task_id, "action_result", {
                            "action_id": act.id,
                            "action_type": act.type.value,
                            "ok": False,
                            "output": _guard_obs,
                            "args_summary": _arg_summary,
                            "overlay": _guard_overlay,
                        })
                        if use_native_tools and tool_call_id:
                            messages.append({
                                "role": "assistant",
                                "content": thought_text,
                                "tool_calls": [{
                                    "id": tool_call_id,
                                    "type": "function",
                                    "function": {"name": action_type, "arguments": json.dumps(args)},
                                }],
                            })
                            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": _guard_obs})
                        else:
                            messages.append({
                                "role": "assistant",
                                "content": f"<thought>{thought_text}</thought>\n<action type=\"{action_type}\">\n{json.dumps(args)}\n</action>",
                            })
                            messages.append({"role": "user", "content": f"<observation>\n{_guard_obs}\n</observation>"})
                        continue

                    # Approval handling
                    # M3: safety.evaluate is also called inside SubTaskWorker.run() but that is a
                    # different code path (hierarchical plan). The streaming loop here runs only when
                    # the hierarchical planner is not used or falls back — no double-evaluation occurs.
                    decision = self.safety.evaluate(act, safe_mode=not is_auto_approve)
                    if act.requires_approval or decision.requires_approval:
                        self._prepare_approval_wait(task_id, act.id)
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
                    _start_overlay = _overlay_for_action_start(
                        act,
                        visual_fallback=(
                            _last_uia_failed
                            and _is_computer_desktop
                            and act.type in _VISUAL_DESKTOP_ACTION_TYPES
                        ),
                    )
                    await self._emit(task_id, "intent", {
                        "action_id": act.id,
                        "action_type": act.type.value,
                        "explanation": act.explanation,
                        "args_preview": _arg_summary,
                        **({"overlay": _start_overlay} if _start_overlay else {}),
                    })
                    await self._emit(task_id, "status", {"message": f"Executing {act.type.value}: {_arg_summary}"})
                    await self._emit(task_id, "action_start", {
                        "action_id": act.id,
                        "action_type": act.type.value,
                        "explanation": act.explanation,
                        "args_summary": _arg_summary,
                        **({"overlay": _start_overlay} if _start_overlay else {}),
                    })

                    granted, denied_scope = await self._ensure_permission_for_action(
                        task_id,
                        act,
                        auto_grant=is_auto_approve,
                        args_summary=_arg_summary,
                    )
                    if not granted:
                        reason = f"Permission denied for {denied_scope or 'requested scope'}."
                        await self._emit(task_id, "action_result", {
                            "action_id": act.id,
                            "action_type": act.type.value,
                            "ok": False,
                            "output": reason,
                            "args_summary": _arg_summary,
                            **({"overlay": _start_overlay} if _start_overlay else {}),
                        })
                        self._finalize(task_id, "cancelled", reason)
                        return

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
                        
                    _result_overlay = _overlay_from_result(res)
                    await self._emit(task_id, "action_result", {
                        "action_id": act.id,
                        "ok": res.ok,
                        "output": res.output,
                        "action_type": act.type.value,
                        "args_summary": _arg_summary,
                        **({"overlay": _result_overlay} if _result_overlay else {}),
                    })
                    if act.type in _UIA_ACTION_TYPES:
                        _last_uia_failed = not res.ok
                    elif act.type in _VISUAL_DESKTOP_ACTION_TYPES:
                        _last_uia_failed = False
                    
                    # ── Populate write cache so subsequent reads are free ──
                    if act.type == AT.write_file and res.ok:
                        _write_path = args.get("path", "")
                        _write_content = args.get("content", "")
                        if _write_path:
                            _write_cache[_write_path] = _write_content

                    # ── File-change events + git auto-commit (coding mode) ──
                    if is_coding_mode and res.ok and action_type in _FILE_WRITE_TYPES:
                        _fpath = args.get("path") or args.get("file_path") or args.get("target_file") or ""
                        if _fpath:
                            await self._emit(task_id, "file_change", {
                                "path": _fpath,
                                "action": action_type,
                                "content": args.get("content") or args.get("file_text") or "",
                            })
                            _commit_hash = await asyncio.to_thread(
                                _git_commit_file, _fpath, tools.workspace, action_type, task_id
                            )
                            if _commit_hash:
                                await self._emit(task_id, "file_commit", {
                                    "path": _fpath,
                                    "commit_hash": _commit_hash,
                                    "message": f"[ai-computer] {action_type}: {os.path.basename(_fpath)}",
                                })

                    # ── Auto-screenshot after computer actions so model sees result ──
                    post_action_note = ""
                    explicit_screenshot = (
                        act.type == AT.screenshot
                        or (act.type == AT.computer and (act.args.get("action", "").strip().lower() == "screenshot"))
                    )
                    explicit_screen_context = act.type == AT.screen_context
                    if _is_computer_desktop and (explicit_screenshot or explicit_screen_context) and res.base64_image:
                        if _model_sees:
                            screenshot_b64 = res.base64_image
                            _preserve_screenshot_once = True
                        await self._emit(task_id, "screenshot", {
                            "data": res.base64_image,
                            "isolated": bool(explicit_screenshot and is_isolated and tools.resolve_isolated_hwnd()),
                            "worker_id": "planner",
                        })
                    elif _model_sees and _should_capture_post_action(act, res, _is_computer_desktop):
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
                    await self._emit(task_id, "usage_update", {"total_tokens": provider.total_tokens})
                    
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
        self._permission_waits.pop(task_id, None)
        for store in (self._approvals, self._approval_overrides, self._permission_waits):
            for key in [k for k in store if k.startswith(f"{task_id}:")]:
                store.pop(key, None)
        self._pause_events.pop(task_id, None)

    def _prepare_approval_wait(self, task_id: str, action_id: str) -> asyncio.Future:
        return self._approvals.setdefault(f"{task_id}:{action_id}", asyncio.Future())

    async def _wait_for_approval(self, task_id: str, action_id: str) -> bool:
        fut = self._prepare_approval_wait(task_id, action_id)
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

    async def _ensure_permission_for_action(
        self,
        task_id: str,
        action: Action,
        *,
        auto_grant: bool = False,
        emit: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        args_summary: str = "",
    ) -> tuple[bool, Optional[str]]:
        needed_scope = scope_for_action(action.type.value, action.args)
        if not needed_scope:
            return True, None
        scope = needed_scope.value
        if self.permissions.is_granted(task_id, scope):
            return True, scope
        if auto_grant:
            self.permissions.grant(task_id, scope)
            return True, scope
        if self.permissions.is_denied(task_id, scope):
            return False, scope
        payload = {
            "action_id": action.id,
            "scope": scope,
            "reason": f"Action '{action.type.value}' needs '{scope}' access.",
            "action_type": action.type.value,
            "args_summary": args_summary or _summarize_args(action.type.value, action.args),
            "action": action.model_dump(),
        }
        self._permission_waits.setdefault(f"{task_id}:{action.id}", asyncio.Future())
        if emit:
            await emit("permission_required", payload)
        else:
            await self._emit(task_id, "permission_required", payload)
        granted = await self._wait_for_permission(task_id, action.id)
        if granted:
            self.permissions.grant(task_id, scope)
        else:
            self.permissions.deny(task_id, scope)
        return bool(granted), scope

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
