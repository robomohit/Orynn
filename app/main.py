from __future__ import annotations
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
import asyncio
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List, Literal
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .agent import AgentService
from .log_emitter import log_emitter
from .models import AgentContext, TaskRecord
from .premium_features import (
    append_feedback,
    create_git_checkpoint,
    detect_ollama,
    revert_git_checkpoint,
    run_task_hooks,
    send_completion_notification,
)
from .skills import skill_manager

def _load_or_create_api_key() -> str:
    if env_key := os.environ.get("AGENT_API_KEY"):
        return env_key
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ai_computer"
    key_file = config_dir / ".api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    config_dir.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_hex(32)
    key_file.write_text(new_key)
    key_file.chmod(0o600)
    print(f"[AI_Computer] Generated new API key, saved to {key_file}", flush=True)
    return new_key

API_KEY = _load_or_create_api_key()
print(f"[AI_Computer] Agent API key configured: {bool(os.environ.get('AGENT_API_KEY'))}", flush=True)
SESSION_COOKIE_NAME = "ai_computer_session"
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "43200"))
_sessions: Dict[str, datetime] = {}

from contextlib import asynccontextmanager

import logging as _logging
_lifespan_log = _logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(application):
    from .mcp_manager import mcp_manager
    from .integrations.telegram import start_telegram
    from .integrations.discord import start_discord

    async def _init_mcp():
        try:
            await asyncio.wait_for(
                mcp_manager.initialize_default_servers(str(HOME_DIR)),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            _lifespan_log.warning(
                "MCP server initialization timed out after 15 s — continuing without MCP servers."
            )
        except Exception as exc:
            _lifespan_log.warning("MCP server initialization failed: %s", exc)

    global _telegram_task, _discord_task, _automation_task
    await _init_mcp()
    _telegram_task = asyncio.create_task(start_telegram(service))
    _discord_task = asyncio.create_task(start_discord(service))

    from .automation import get_registry as _get_auto_registry, poll_and_fire as _poll_and_fire

    async def _automation_submit(goal: str) -> None:
        tid = f"automation-{uuid.uuid4().hex[:8]}"
        for env_var, mdl in [
            ("OPENROUTER_API_KEY", "openrouter/nvidia/nemotron-3-super-120b-a12b:free"),
            ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20241022"),
            ("OPENAI_API_KEY", "gpt-4o-mini"),
            ("GOOGLE_API_KEY", "gemini-2.0-flash"),
            ("GROQ_API_KEY", "groq/llama-3.3-70b-versatile"),
        ]:
            if os.environ.get(env_var):
                selected = mdl
                break
        else:
            _lifespan_log.warning("Automation: no API key configured, cannot fire trigger for %r", goal)
            return
        spec = {
            "task_id": tid, "goal": goal, "model": selected,
            "mode": "auto", "screen_width": 1280, "screen_height": 800,
            "environment": _build_task_environment(HOME_DIR, project_folder_selected=False),
        }
        _start_task_from_spec(spec)
        _lifespan_log.info("Automation: fired task %s for goal %r", tid, goal)

    _automation_task = asyncio.create_task(_poll_and_fire(_automation_submit))

    yield
    # Shutdown: cancel integrations and automation poller, then clean up background browsers
    for _t in (_telegram_task, _discord_task, _automation_task):
        if _t and not _t.done():
            _t.cancel()
            try:
                await _t
            except asyncio.CancelledError:
                pass
    await service.shutdown()

app = FastAPI(title="AI Computer", lifespan=_lifespan)
_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Serve bundled static assets (vendored JS/CSS, e.g. static/vendor/mermaid.min.js)
# so the UI stays fully offline — no CDN dependency.
from fastapi.staticfiles import StaticFiles
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# Defeat aggressive browser caching for our static bundle so a fresh `static/style.css`
# or `static/app.js` actually reaches the user (and the pywebview shell) without a hard
# reload. `no-cache` still allows ETag revalidation, so this is cheap.
from starlette.middleware.base import BaseHTTPMiddleware


class _StaticNoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(_StaticNoCacheMiddleware)

# ── Capsule widget SSE infrastructure ────────────────────────────────────────
# The Qt floating capsule subscribes to /api/capsule/events (Server-Sent Events)
# to receive real-time widget spawn commands from the agent or test endpoints.
_capsule_queues: list[asyncio.Queue] = []


@app.get("/api/capsule/events")
async def capsule_events():
    """SSE stream for the floating capsule to receive widget spawn events."""
    q: asyncio.Queue = asyncio.Queue()
    _capsule_queues.append(q)

    async def stream():
        try:
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _capsule_queues:
                _capsule_queues.remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/capsule/widget")
async def push_capsule_widget(request: Request):
    """Push a widget event to all connected capsules."""
    payload = await request.json()
    payload.setdefault("type", "widget")
    for q in _capsule_queues:
        await q.put(payload)
    return {"ok": True, "listeners": len(_capsule_queues)}



@app.post("/api/capsule/organize")
async def organize_capsule_files(request: Request):
    """Organize files in a folder into category subfolders. REAL file moves."""
    from .clutter_scanner import organize_files
    body = await request.json()
    folder_path = body.get("folder_path", "")
    if not folder_path or not os.path.isdir(folder_path):
        raise HTTPException(400, "Invalid folder_path")
    result = await asyncio.to_thread(organize_files, folder_path)
    return result


@app.post("/api/capsule/delete")
async def delete_capsule_files(request: Request):
    """Delete specific files. REAL file deletion."""
    from .clutter_scanner import delete_files
    body = await request.json()
    file_paths = body.get("file_paths", [])
    if not file_paths:
        raise HTTPException(400, "No file_paths provided")
    result = await asyncio.to_thread(delete_files, file_paths)
    return result


@app.post("/api/capsule/scan")
async def scan_capsule_folder(request: Request):
    """Scan a folder and return real file listing."""
    from .clutter_scanner import scan_folder
    body = await request.json()
    folder_path = body.get("folder_path", None)
    result = await asyncio.to_thread(scan_folder, folder_path)
    return result

bearer = HTTPBearer(auto_error=False)
_tasks: Dict[str, TaskRecord] = {}
_telegram_task: Optional[asyncio.Task] = None
_discord_task: Optional[asyncio.Task] = None
_automation_task: Optional[asyncio.Task] = None

def _prune_sessions(now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    expired = [token for token, expires_at in _sessions.items() if expires_at <= now]
    for token in expired:
        _sessions.pop(token, None)


def _valid_session_token(token: str) -> bool:
    if not token:
        return False
    now = datetime.now(timezone.utc)
    _prune_sessions(now)
    expires_at = _sessions.get(token)
    return bool(expires_at and expires_at > now)


def _is_authorized(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> bool:
    bearer_token = credentials.credentials if credentials else ""
    if bearer_token == API_KEY or _valid_session_token(bearer_token):
        return True
    return _valid_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))


async def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Security(bearer)):
    if not _is_authorized(request, credentials):
        raise HTTPException(status_code=401, detail="Unauthorized")

workspace_dir = Path(".")
workspace_dir.mkdir(parents=True, exist_ok=True)
(workspace_dir / "logs").mkdir(parents=True, exist_ok=True)
task_store_dir = workspace_dir / "tasks"
task_store_dir.mkdir(parents=True, exist_ok=True)
HOME_DIR = Path.home().resolve()
SHORTCUT_DIRS = {
    "home": HOME_DIR,
    "desktop": HOME_DIR / "Desktop",
    "downloads": HOME_DIR / "Downloads",
    "repo": workspace_dir.resolve(),
}


def _resolve_project_folder(raw_path: Optional[str]) -> Optional[Path]:
    if raw_path is None or not str(raw_path).strip():
        return None
    raw = str(raw_path).strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (workspace_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists():
        raise HTTPException(status_code=422, detail=f"Project folder does not exist: {candidate}")
    if not candidate.is_dir():
        raise HTTPException(status_code=422, detail=f"Project folder is not a directory: {candidate}")
    return candidate


def _build_task_environment(workspace: Path, *, project_folder_selected: bool) -> Dict[str, Any]:
    import platform

    workspace = workspace.resolve()
    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "home": str(HOME_DIR),
        "workspace": str(workspace),
        "desktop": str(HOME_DIR / "Desktop"),
        "downloads": str(HOME_DIR / "Downloads"),
        "documents": str(HOME_DIR / "Documents"),
        "user": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
        "python": "python" if platform.system() == "Windows" else "python3",
        "project_folder_selected": project_folder_selected,
    }


def _display_name(path: Path) -> str:
    name = path.name.rstrip("\\/")
    if name:
        return name
    anchor = path.anchor.rstrip("\\/")
    return anchor or str(path)


def _breadcrumbs(path: Path) -> List[Dict[str, str]]:
    resolved = path.resolve()
    breadcrumbs: List[Dict[str, str]] = []
    current: Optional[Path] = None
    for part in resolved.parts:
        current = Path(part) if current is None else current / part
        breadcrumbs.append({"name": _display_name(current), "path": str(current)})
    return breadcrumbs


def _cleanup_orphan_tmp_files() -> int:
    """Remove leftover .tmp atomic-write files from previous crashes."""
    removed = 0
    for tmp in task_store_dir.glob(".*.json.*.tmp"):
        try:
            tmp.unlink()
            removed += 1
        except OSError:
            pass
    return removed


_orphans_removed = _cleanup_orphan_tmp_files()
if _orphans_removed:
    print(f"[AI_Computer] Cleaned up {_orphans_removed} orphaned task tmp file(s).", flush=True)

service = AgentService(workspace_dir, log_emitter=log_emitter)


TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_TASK_ID_RE = re.compile(TASK_ID_PATTERN)


def _validate_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.fullmatch(task_id or ""):
        raise HTTPException(status_code=422, detail="Invalid task_id. Use 1-128 letters, numbers, dots, underscores, or hyphens.")
    return task_id


def _task_store_path(task_id: str) -> Path:
    _validate_task_id(task_id)
    return task_store_dir / f"{task_id}.json"


def _is_terminal_status(status: Optional[str]) -> bool:
    return status in {"done", "failed", "cancelled", "complete", "error"}


def _save_task_record(record: TaskRecord) -> None:
    path = _task_store_path(record.id)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    data = json.dumps(record.model_dump(), indent=2)
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def _infer_task_from_log(task_id: str) -> Optional[TaskRecord]:
    events = log_emitter.read_log(task_id)
    if not events:
        return None

    log_path = log_emitter.log_path(task_id)
    created_at = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc).isoformat()
    goal = task_id
    model = None
    mode = None
    status = "running"
    finished_at = None
    reason = None

    for event in events:
        if event.get("type") == "task_created":
            goal = event.get("goal") or goal
            model = event.get("model") or model
            mode = event.get("mode") or mode
            created_at = event.get("created_at") or created_at
        elif event.get("type") == "mode":
            mode = event.get("mode") or mode
        elif event.get("type") == "done":
            status = "done" if event.get("complete") else "failed"
            finished_at = event.get("finished_at") or created_at
            reason = event.get("reason")
        elif event.get("type") == "cancelled":
            status = "cancelled"
            finished_at = event.get("finished_at") or created_at
            reason = event.get("message") or reason
        elif event.get("type") == "error":
            status = "failed"
            reason = event.get("message") or reason

    return TaskRecord(
        id=task_id,
        status=status,
        context=AgentContext(goal=goal),
        goal=goal,
        created_at=created_at,
        finished_at=finished_at,
        reason=reason,
        model=model,
        mode=mode,
    )


def _load_persisted_tasks() -> Dict[str, TaskRecord]:
    tasks: Dict[str, TaskRecord] = {}

    for meta_file in task_store_dir.glob("*.json"):
        try:
            record = TaskRecord.model_validate(json.loads(meta_file.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[AI_Computer] Skipped malformed task record {meta_file.name}: {exc}", flush=True)
            continue

        if record.status in {"running", "paused", "pending"}:
            record.status = "failed"
            record.reason = record.reason or "Server restarted while task was active."
            record.finished_at = record.finished_at or datetime.now(timezone.utc).isoformat()
            _save_task_record(record)
        tasks[record.id] = record

    for task_id in log_emitter.task_ids():
        if task_id in tasks:
            continue
        inferred = _infer_task_from_log(task_id)
        if inferred and inferred.status in {"running", "paused", "pending"}:
            inferred.status = "failed"
            inferred.reason = inferred.reason or "Server restarted while task was active."
            inferred.finished_at = inferred.finished_at or datetime.now(timezone.utc).isoformat()
        tasks[task_id] = inferred

    return {task_id: record for task_id, record in tasks.items() if record is not None}


def _get_task_record(task_id: str) -> Optional[TaskRecord]:
    record = _tasks.get(task_id)
    if record:
        return record

    persisted = _task_store_path(task_id)
    if persisted.exists():
        try:
            record = TaskRecord.model_validate(json.loads(persisted.read_text(encoding="utf-8")))
            _tasks[task_id] = record
            return record
        except Exception:
            pass

    inferred = _infer_task_from_log(task_id)
    if inferred:
        _tasks[task_id] = inferred
    return inferred


def _task_is_server_running(task_id: str) -> bool:
    task = service._active_tasks.get(task_id)
    return bool(task and not task.done())


def _serialize_task_record(record: TaskRecord) -> dict:
    payload = record.model_dump()
    payload["paused"] = bool(record.paused or record.id in service._paused_tasks)
    payload["server_running"] = _task_is_server_running(record.id)
    if payload["server_running"]:
        payload["status"] = "paused" if payload["paused"] else "running"
    return payload


_tasks = _load_persisted_tasks()

_MAX_IN_MEMORY_TASKS = 200  # keep at most this many completed tasks in _tasks dict
_MAX_ACTIVE_TASKS = int(os.environ.get("AI_COMPUTER_MAX_ACTIVE_TASKS", "5"))
_queued_task_specs: List[Dict[str, Any]] = []


def _evict_old_tasks() -> None:
    """Drop the oldest completed tasks from the in-memory dict when it grows too large.

    TaskRecord objects are small, but the dict still accumulates unboundedly across
    many runs.  We keep the newest _MAX_IN_MEMORY_TASKS entries so history still works
    for recent tasks while preventing a slow memory creep over long sessions.
    """
    if len(_tasks) <= _MAX_IN_MEMORY_TASKS:
        return
    terminal = [
        (tid, t) for tid, t in _tasks.items()
        if _is_terminal_status(t.status)
    ]
    if not terminal:
        return
    # Sort by finished_at ascending so we drop the oldest first
    terminal.sort(key=lambda x: x[1].finished_at or "")
    excess = len(_tasks) - _MAX_IN_MEMORY_TASKS
    for tid, _ in terminal[:excess]:
        _tasks.pop(tid, None)


def _task_workspace_for_record(rec: TaskRecord) -> Path:
    raw = rec.context.project_folder or rec.context.environment.get("workspace") or str(HOME_DIR)
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return HOME_DIR


def _start_task_from_spec(spec: Dict[str, Any]) -> TaskRecord:
    record = service.init_task(
        task_id=spec["task_id"],
        goal=spec["goal"],
        screen_width=spec["screen_width"],
        screen_height=spec["screen_height"],
        model=spec["model"],
        mode=spec["mode"],
        isolated_app=spec.get("isolated_app"),
        active_skills=spec.get("active_skills") or [],
        project_folder=spec.get("project_folder"),
        environment=spec.get("environment") or {},
        plan_first=bool(spec.get("plan_first")),
        notify_on_completion=bool(spec.get("notify_on_completion")),
        auto_commit=bool(spec.get("auto_commit")),
        autonomy_level=spec.get("autonomy_level") or "balanced",
        thinking_budget=spec.get("thinking_budget") or "off",
    )
    _tasks[record.id] = record
    _save_task_record(record)
    log_emitter.emit(record.id, "task_started", {
        "task_id": record.id,
        "status": "running",
        "queued": bool(spec.get("queued")),
        "model": record.model,
        "mode": record.mode,
    })
    return record


def _start_next_queued_task() -> None:
    active_count = lambda: sum(1 for task in service._active_tasks.values() if not task.done())
    while _queued_task_specs and active_count() < _MAX_ACTIVE_TASKS:
        spec = _queued_task_specs.pop(0)
        rec = _tasks.get(spec["task_id"])
        if rec and rec.status != "queued":
            continue
        spec["queued"] = True
        try:
            _start_task_from_spec(spec)
        except Exception as exc:
            if rec:
                rec.status = "failed"
                rec.reason = f"Queued task failed to start: {exc}"
                rec.finished_at = datetime.now(timezone.utc).isoformat()
                _save_task_record(rec)
            log_emitter.emit(spec["task_id"], "error", {"message": f"Queued task failed to start: {exc}"})


def _run_completion_side_effects(rec: TaskRecord, status: str, reason: str) -> None:
    workspace = _task_workspace_for_record(rec)
    hook_event = "task_done" if status == "done" else "task_failed" if status == "failed" else "task_cancelled"
    for result in run_task_hooks(workspace, hook_event, {
        "task_id": rec.id,
        "status": status,
        "reason": reason,
        "goal": rec.goal or rec.context.goal,
    }):
        log_emitter.emit(rec.id, "hook_result", result)

    if rec.auto_commit:
        checkpoint = create_git_checkpoint(workspace, rec.id, reason or rec.goal or rec.id)
        rec.metadata["checkpoint"] = checkpoint
        if checkpoint.get("commit"):
            rec.checkpoint_commit = checkpoint["commit"]
        log_emitter.emit(rec.id, "checkpoint", checkpoint)

    if rec.notify_on_completion:
        notification = send_completion_notification(rec.goal or rec.context.goal, status, reason)
        rec.metadata["notification"] = notification
        log_emitter.emit(rec.id, "notification", notification)


def _on_complete(task_id: str, status: str, reason: str):
    rec = _tasks.get(task_id)
    if rec:
        rec.status = status
        rec.finished_at = datetime.now(timezone.utc).isoformat()
        rec.reason = reason
        try:
            _run_completion_side_effects(rec, status, reason)
        except Exception as exc:
            log_emitter.emit(task_id, "hook_result", {"name": "completion-side-effects", "ok": False, "output": str(exc)})
        _save_task_record(rec)
    # Release per-task in-memory state in the log emitter (seq counter, disk flag)
    log_emitter.cleanup_task(task_id)
    # Evict oldest completed tasks to cap the in-memory dict size
    _evict_old_tasks()
    service._active_tasks.pop(task_id, None)
    _start_next_queued_task()

service._on_task_complete = _on_complete

from pydantic import BaseModel, Field

class TaskIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=128, pattern=TASK_ID_PATTERN)
    goal: str = Field(..., min_length=1, max_length=2000)
    model: Optional[str] = None  # None = auto-pick from available keys
    mode: Literal["auto", "coding", "computer", "computer_use", "computer_isolated", "explain"] = "auto"
    screen_width: int = 1280
    screen_height: int = 800
    isolated_app: Optional[str] = None  # partial window title to target in isolated mode
    active_skills: List[str] = []
    project_folder: Optional[str] = None
    plan_first: bool = False
    notify_on_completion: bool = False
    auto_commit: bool = False
    autonomy_level: Literal["careful", "balanced", "fast"] = "balanced"
    thinking_budget: Literal["off", "standard", "extended"] = "off"


class AutomationIn(BaseModel):
    schedule: str = Field(..., description="5-field cron expression: 'minute hour mday month wday'")
    task_template: str = Field(..., min_length=1, max_length=2000)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10240:
            return StreamingResponse(
                iter([b'{"detail":"Payload too large"}']), status_code=413, media_type="application/json"
            )
    try:
        return await call_next(request)
    except Exception as e:
        print(f"[ERROR] Middleware caught exception: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise e

class ApprovalIn(BaseModel):
    task_id: str
    action_id: str
    approve: bool
    plan_override: str = ""


class PermissionIn(BaseModel):
    task_id: str
    action_id: str
    grant: bool
    scope: Optional[str] = None

@app.get("/")
async def root():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
    )


@app.get("/v2")
async def root_v2():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
    )

import time
import subprocess
START_TIME = time.time()

def _git_commit_short() -> Optional[str]:
    """Return current short commit hash, or None if git is unavailable."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("utf-8", errors="ignore").strip() or None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "commit": _git_commit_short(),
        "uptime_seconds": time.time() - START_TIME,
        "active_tasks": sum(1 for t in _tasks.values() if t.status in ("pending", "running")),
    }

_HEALTHZ_PROVIDERS = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}
_healthz_cache: dict = {"ts": 0.0, "result": None}

@app.get("/healthz")
async def healthz():
    """Provider key status check, cached 30s."""
    if time.time() - _healthz_cache["ts"] < 30 and _healthz_cache["result"] is not None:
        return _healthz_cache["result"]
    providers = {
        name: ("ok" if os.environ.get(env_var) else "missing_key")
        for name, env_var in _HEALTHZ_PROVIDERS.items()
    }
    ollama = detect_ollama()
    providers["ollama"] = "ok" if ollama.get("available") else "unavailable"
    result = {"server": "ok", "providers": providers, "ollama": ollama}
    _healthz_cache["ts"] = time.time()
    _healthz_cache["result"] = result
    return result


def _upsert_env_var(name: str, value: str) -> None:
    """Create/update a KEY=value line in .env AND set it live in os.environ so
    it takes effect without a restart. Used by the first-run key setup."""
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out, found = [], False
    for ln in lines:
        if (not ln.lstrip().startswith("#")
                and re.match(rf"\s*{re.escape(name)}\s*=", ln)):
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{name}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[name] = value


class _ProviderKeyBody(BaseModel):
    provider: str
    key: str


@app.post("/api/setup/provider-key", dependencies=[Depends(verify_token)])
async def set_provider_key(body: _ProviderKeyBody):
    """First-run onboarding: save a provider API key to .env + live env so the
    agent works immediately, no manual file editing. (User's own local config.)"""
    prov = (body.provider or "").strip().lower()
    key = (body.key or "").strip()
    env_name = _HEALTHZ_PROVIDERS.get(prov)
    if not env_name:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{body.provider}'. Use one of: "
                   f"{', '.join(_HEALTHZ_PROVIDERS)}")
    if len(key) < 8:
        raise HTTPException(status_code=400, detail="That key looks too short.")
    try:
        await asyncio.to_thread(_upsert_env_var, env_name, key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save key: {exc}")
    _healthz_cache["ts"] = 0.0  # force a fresh status read
    return {"ok": True, "provider": prov, "env": env_name}


@app.get("/api/setup/status", dependencies=[Depends(verify_token)])
async def setup_status():
    """Whether onboarding is complete (at least one provider key present)."""
    have = {n: bool(os.environ.get(v)) for n, v in _HEALTHZ_PROVIDERS.items()}
    return {"configured": any(have.values()), "providers": have}


@app.get("/api/skills")
async def get_skills():
    return {"skills": skill_manager.get_all_skills()}

@app.get("/api/coding-backends")
async def get_coding_backends():
    """Declared coding-delegation backends + live availability detection.
    Powers the Settings connector list. Detection shells out to each CLI's
    --version, so run it off the event loop."""
    from .coding_backends import registry
    return await asyncio.to_thread(registry.detect_all)

@app.get("/api/mcp")
async def get_mcp():
    from .mcp_manager import mcp_manager
    if not mcp_manager._is_ready:
        return {"servers": [], "initializing": True}
    servers = []
    for name, srv in mcp_manager.servers.items():
        servers.append({
            "name": name,
            "tools": srv.tools
        })
    return {"servers": servers}

@app.get("/api/mcp/health")
async def mcp_health():
    from .mcp_manager import mcp_manager
    return {"servers": mcp_manager.health(), "ready": mcp_manager._is_ready}


@app.get("/api/memory/health")
async def memory_health():
    """Counts by kind, short-term session count, last consolidation timestamp."""
    return service.memory.health()


@app.post("/api/memory/consolidate", dependencies=[Depends(verify_token)])
async def memory_consolidate():
    """Manually trigger a consolidation pass (merge near-duplicates, prune
    stale never-recalled summaries). Safe to run repeatedly."""
    return service.memory.consolidate()


@app.post("/api/session")
async def create_session(response: Response):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
    _sessions[token] = expires_at
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        samesite="lax",
    )
    return {"authenticated": True, "expires_at": expires_at.isoformat()}


@app.get("/api/config")
async def config():
    return {
        "authenticated": False,
        "session_endpoint": "/api/session",
        "session_ttl_seconds": SESSION_TTL_SECONDS,
        "home_directory": str(HOME_DIR),
        "workspace_directory": str(workspace_dir.resolve()),
        "project_folder_shortcuts": {name: str(path) for name, path in SHORTCUT_DIRS.items() if path.exists()},
    }


@app.get("/api/browse-directory", dependencies=[Depends(verify_token)])
async def browse_directory(path: Optional[str] = None, max_entries: int = 240):
    current = _resolve_project_folder(path) if path else HOME_DIR
    if current is None:
        current = HOME_DIR
    if not current.exists() or not current.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    truncated = False
    try:
        children = sorted(current.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Cannot open directory: {exc}") from exc

    for child in children:
        if len(entries) >= max(25, min(max_entries, 500)):
            truncated = True
            break
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name or str(child),
            "path": str(child.resolve()),
            "is_dir": child.is_dir(),
            "size": None if child.is_dir() else stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })

    parent = current.parent if current.parent != current else None
    return {
        "path": str(current),
        "name": _display_name(current),
        "parent": str(parent) if parent else None,
        "breadcrumbs": _breadcrumbs(current),
        "entries": entries,
        "truncated": truncated,
        "shortcuts": [
            {"id": name, "label": _display_name(shortcut), "path": str(shortcut)}
            for name, shortcut in SHORTCUT_DIRS.items()
            if shortcut.exists()
        ],
    }

_ALL_MODELS = [
    # Free models (OpenRouter) — tested and working
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/arcee-ai/trinity-large-preview:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/qwen/qwen3-coder:free",
    "openrouter/google/gemma-4-31b-it:free",
    "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
    # Paid models
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20250219",
    "gpt-4o",
    "gpt-4o-mini",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

# Map model prefix → required env var for validation
_MODEL_KEY_MAP = {
    "ollama/": None,
    "openrouter/": "OPENROUTER_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "o1": "OPENAI_API_KEY",
    "o3": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq/": "GROQ_API_KEY",
}

def _required_key_for_model(model: str) -> Optional[str]:
    """Return the env-var name required for a given model, or None."""
    m = model.lower()
    for prefix, env_var in _MODEL_KEY_MAP.items():
        if m.startswith(prefix):
            return env_var
    return "OPENROUTER_API_KEY"  # default fallback

# ── Connectors API (additive — used by the dashboard sidebar)
@app.get("/api/connectors")
async def get_connectors():
    from .connectors import list_with_state
    return {"connectors": list_with_state()}


from pydantic import BaseModel as _BM_Conn
class _LinkBody(_BM_Conn):
    notes: str = ""


@app.post("/api/connectors/{connector_id}/link")
async def link_connector(connector_id: str, body: _LinkBody | None = None):
    from .connectors import link
    notes = body.notes if body else ""
    c = link(connector_id, notes=notes)
    if c is None:
        raise HTTPException(status_code=404, detail="Unknown connector")
    return c


@app.post("/api/connectors/{connector_id}/unlink")
async def unlink_connector(connector_id: str):
    from .connectors import unlink, get
    if get(connector_id) is None:
        raise HTTPException(status_code=404, detail="Unknown connector")
    return unlink(connector_id)


# ── Desktop-features API (snap layouts, telemetry promise, autostart) ──
@app.get("/api/desktop/telemetry")
async def telemetry_promise():
    """Single source of truth for the privacy panel — always telemetry-off."""
    from .widget.desktop_features import TELEMETRY_PROMISE
    return TELEMETRY_PROMISE


@app.get("/api/desktop/layouts")
async def list_snap_layouts():
    from .widget.desktop_features import LAYOUTS
    return {"layouts": [
        {"id": k, "description": v["description"]}
        for k, v in LAYOUTS.items()
    ]}


@app.post("/api/desktop/layouts/{layout_id}/apply")
async def apply_snap_layout(layout_id: str):
    from .widget.desktop_features import apply_layout
    return apply_layout(layout_id)


@app.get("/api/desktop/autostart")
async def get_autostart():
    from .widget.desktop_features import is_autostart_enabled
    return {"enabled": is_autostart_enabled()}


class _AutostartBody(BaseModel):
    enabled: bool


@app.post("/api/desktop/autostart")
async def set_autostart_endpoint(body: _AutostartBody):
    from .widget.desktop_features import set_autostart, is_autostart_enabled
    set_autostart(body.enabled)
    return {"enabled": is_autostart_enabled()}


# ── UIA navigation
@app.get("/api/desktop/uia/find")
async def uia_find(query: str, app: str = ""):
    from .widget.desktop_features import find_ui_element
    return find_ui_element(query, app)


@app.get("/api/desktop/uia/candidates")
async def uia_candidates(query: str, app: str = "", limit: int = 5):
    """Return top-N ranked candidates for the query."""
    from .widget.desktop_features import find_ui_elements
    return find_ui_elements(query, app, limit)


class _UiaClickBody(BaseModel):
    query: str
    app: str = ""
    button: str = "left"


@app.post("/api/desktop/uia/click")
async def uia_click(body: _UiaClickBody):
    """Find a control by name + physically click it via pyautogui."""
    from .widget.desktop_features import click_ui_element
    return click_ui_element(body.query, body.app, body.button)


@app.get("/api/desktop/uia/smart-find")
async def uia_smart_find(query: str, app: str = ""):
    """UIA find that, for Electron apps, also returns a relaunch hint so
    the agent can unlock their accessibility tree."""
    from .widget.desktop_features import smart_uia_find_with_unlock
    return smart_uia_find_with_unlock(query, app)


class _ElectronRelaunchBody(BaseModel):
    exe: str
    args: list[str] = []
    cdp: bool = False  # also tack on --remote-debugging-port=9222


@app.post("/api/desktop/electron/relaunch")
async def electron_relaunch(body: _ElectronRelaunchBody):
    """Relaunch an Electron app with --force-renderer-accessibility so its
    DOM exposes as a real UIA tree. Optional CDP flag for power users."""
    from .widget.desktop_features import relaunch_with_accessibility
    return relaunch_with_accessibility(body.exe, body.args, body.cdp)


@app.get("/api/desktop/electron/check")
async def electron_check(exe: str):
    """Heuristic: is this exe path an Electron app?"""
    from .widget.desktop_features import is_electron_app
    return {"exe": exe, "is_electron": is_electron_app(exe)}


# ── Clipboard history
@app.get("/api/desktop/clipboard/history")
async def clip_history(limit: int = 20):
    from .widget.desktop_features import list_clipboard_history
    return {"items": list_clipboard_history(limit)}


@app.get("/api/desktop/clipboard/search")
async def clip_search(q: str, limit: int = 10):
    from .widget.desktop_features import search_clipboard_history
    return {"items": search_clipboard_history(q, limit)}


# ── Scheduled recipes
@app.get("/api/desktop/scheduled")
async def list_sched():
    from .widget.desktop_features import list_scheduled
    return {"items": list_scheduled()}


class _SchedBody(BaseModel):
    name: str
    when: str
    goal: str
    mode: str = "auto"


@app.post("/api/desktop/scheduled")
async def add_sched(body: _SchedBody):
    from .widget.desktop_features import add_scheduled
    return add_scheduled(body.name, body.when, body.goal, body.mode)


@app.delete("/api/desktop/scheduled/{sid}")
async def del_sched(sid: str):
    from .widget.desktop_features import remove_scheduled
    return {"ok": remove_scheduled(sid)}


# ── Form profiles + autofill
@app.get("/api/desktop/profiles")
async def get_profiles():
    from .widget.desktop_features import list_profiles
    return list_profiles()


class _ProfileBody(BaseModel):
    name: str
    fields: dict


@app.post("/api/desktop/profiles")
async def put_profile(body: _ProfileBody):
    from .widget.desktop_features import save_profile
    return save_profile(body.name, body.fields)


@app.delete("/api/desktop/profiles/{name}")
async def del_profile(name: str):
    from .widget.desktop_features import delete_profile
    delete_profile(name)
    return {"ok": True}


@app.post("/api/desktop/profiles/{name}/autofill")
async def do_autofill(name: str):
    from .widget.desktop_features import autofill_active_form
    return autofill_active_form(name)


# ── Screen-region watch
@app.get("/api/desktop/watches")
async def get_watches():
    from .widget.desktop_features import list_watches
    return {"items": list_watches()}


class _WatchBody(BaseModel):
    name: str
    x: int
    y: int
    w: int
    h: int
    every_sec: int = 60
    prompt: str = ""


@app.post("/api/desktop/watches")
async def add_watch_ep(body: _WatchBody):
    from .widget.desktop_features import add_watch
    return add_watch(body.name, body.x, body.y, body.w, body.h,
                     body.every_sec, body.prompt)


@app.delete("/api/desktop/watches/{wid}")
async def del_watch(wid: str):
    from .widget.desktop_features import remove_watch
    return {"ok": remove_watch(wid)}


# ── Cross-app "send to"
class _SendBody(BaseModel):
    target: str  # 'notepad' | 'excel' | 'clipboard' | 'paint'
    text: str


@app.post("/api/desktop/send-to")
async def send_to_ep(body: _SendBody):
    from .widget.desktop_features import send_to
    return send_to(body.target, body.text)


# ── OCR
class _OcrBody(BaseModel):
    x: int
    y: int
    w: int
    h: int


@app.post("/api/desktop/ocr")
async def ocr_ep(body: _OcrBody):
    from .widget.desktop_features import ocr_region
    return ocr_region(body.x, body.y, body.w, body.h)


# ── Local RAG over a folder
class _RagIndexBody(BaseModel):
    folder: str
    name: str = "default"


@app.post("/api/desktop/rag/index")
async def rag_index_ep(body: _RagIndexBody):
    from .widget.desktop_features import rag_index_folder
    return rag_index_folder(body.folder, body.name)


@app.get("/api/desktop/rag/query")
async def rag_query_ep(name: str, q: str, top_k: int = 5):
    from .widget.desktop_features import rag_query
    return rag_query(name, q, top_k)


# ── Per-app trust policies
@app.get("/api/desktop/trust")
async def list_trust_ep():
    from .widget.desktop_features import list_trust
    return list_trust()


class _TrustBody(BaseModel):
    exe: str
    level: str


@app.post("/api/desktop/trust")
async def set_trust_ep(body: _TrustBody):
    from .widget.desktop_features import set_trust
    return set_trust(body.exe, body.level)


# ── Undo stack
@app.post("/api/desktop/undo")
async def undo_ep():
    from .widget.desktop_features import pop_and_execute_undo
    return pop_and_execute_undo()


@app.get("/api/models")
async def get_models():
    """Return only models whose API keys are actually configured."""
    keyed: List[dict] = []
    configured_providers: List[str] = []
    if os.environ.get("OPENROUTER_API_KEY"):
        configured_providers.append("OpenRouter")
        for m in [
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            "openrouter/google/gemma-4-31b-it:free",
            "openrouter/google/gemma-4-26b-a4b-it:free",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/qwen/qwen3-coder:free",
            "openrouter/arcee-ai/trinity-large-preview:free",
            "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
        ]:
            keyed.append(m)
    if os.environ.get("ANTHROPIC_API_KEY"):
        configured_providers.append("Anthropic")
        keyed.extend(["claude-3-5-sonnet-20241022", "claude-3-7-sonnet-20250219",
                       "claude-3-opus-20240229", "claude-3-5-haiku-20241022"])
    if os.environ.get("OPENAI_API_KEY"):
        configured_providers.append("OpenAI")
        keyed.extend(["gpt-4o", "gpt-4o-mini"])
    if os.environ.get("GOOGLE_API_KEY"):
        configured_providers.append("Google")
        keyed.extend(["gemini-2.5-flash", "gemini-2.0-flash"])
    if os.environ.get("GROQ_API_KEY"):
        configured_providers.append("Groq")
        keyed.extend(["groq/llama-3.3-70b-versatile", "groq/llama-3.2-90b-vision-preview"])
    ollama = detect_ollama()
    if ollama.get("available"):
        configured_providers.append("Ollama")
        keyed.extend([f"ollama/{name}" for name in ollama.get("models", [])[:8]])
    return {
        "models": keyed,
        "configured_providers": configured_providers,
        "has_keys": len(keyed) > 0,
        "ollama": ollama,
    }

@app.get("/api/tasks", dependencies=[Depends(verify_token)])
async def get_all_tasks():
    ordered = sorted(
        _tasks.values(),
        key=lambda t: t.created_at or "",
    )
    return {
        "tasks": [
            {
                **_serialize_task_record(t),
                "goal": t.goal or t.context.goal,
            }
            for t in ordered
        ]
    }


@app.get("/api/active-tasks", dependencies=[Depends(verify_token)])
async def get_active_tasks():
    """Return tasks currently running or pending (not in a terminal state)."""
    active = [
        {
            "task_id": tid,
            "status": rec.status,
            "goal": rec.goal or rec.context.goal,
            "mode": rec.mode,
            "model": rec.model,
            "created_at": rec.created_at,
        }
        for tid, rec in _tasks.items()
        if not _is_terminal_status(rec.status)
    ]
    return {"tasks": active}


@app.post("/api/tasks", dependencies=[Depends(verify_token)])
async def create_task(body: TaskIn):
    _validate_task_id(body.task_id)
    print(f"[API] create_task: {body.task_id} (model={body.model}, mode={body.mode})", flush=True)
    existing = _tasks.get(body.task_id)
    if existing and existing.status in {"running", "paused", "pending", "queued"}:
        raise HTTPException(status_code=409, detail=f"Task '{body.task_id}' already exists and is still active")
    if existing and existing.status in {"done", "failed", "cancelled", "complete"}:
        raise HTTPException(status_code=409, detail=f"Task '{body.task_id}' already exists")
    active = len(service._active_tasks)

    # Auto-pick a model from whatever keys are available when none is specified
    if not body.model:
        if os.environ.get("OPENROUTER_API_KEY"):
            _mode = body.mode or "auto"
            # Qwen3-Coder is purpose-built for code; use it for coding mode
            if _mode == "coding":
                selected_model = "openrouter/qwen/qwen3-coder:free"
            elif _mode in ("computer", "computer_isolated"):
                # Desktop control via UI Automation is text-only (no vision):
                # use the fast, accurate tool-calling UIA tier. (When an image
                # is attached the widget sends an explicit vision model, which
                # takes this branch out of play since body.model is set.)
                selected_model = "tier:uia"
            else:
                selected_model = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            selected_model = "claude-3-5-sonnet-20241022"
        elif os.environ.get("OPENAI_API_KEY"):
            selected_model = "gpt-4o-mini"
        elif os.environ.get("GOOGLE_API_KEY"):
            selected_model = "gemini-2.0-flash"
        elif os.environ.get("GROQ_API_KEY"):
            selected_model = "groq/llama-3.3-70b-versatile"
        elif os.environ.get("OLLAMA_DEFAULT_MODEL"):
            selected_model = f"ollama/{os.environ['OLLAMA_DEFAULT_MODEL']}"
        else:
            ollama = detect_ollama()
            if ollama.get("available") and ollama.get("models"):
                selected_model = f"ollama/{ollama['models'][0]}"
            else:
                selected_model = ""
        if not selected_model:
            raise HTTPException(
                status_code=400,
                detail="No API keys configured. Add at least one key (OPENROUTER_API_KEY, ANTHROPIC_API_KEY, etc.) to your .env file."
            )
    else:
        selected_model = body.model
        # Validate that the required API key is present for explicitly chosen models
        required_key = _required_key_for_model(selected_model)
        if required_key and not os.environ.get(required_key):
            raise HTTPException(
                status_code=400,
                detail=f"Model '{selected_model}' requires {required_key} to be set in your .env file."
            )

    selected_project_folder = _resolve_project_folder(body.project_folder)
    effective_workspace = selected_project_folder or HOME_DIR
    environment = _build_task_environment(
        effective_workspace,
        project_folder_selected=selected_project_folder is not None,
    )

    try:
        spec = {
            "task_id": body.task_id,
            "goal": body.goal,
            "screen_width": body.screen_width,
            "screen_height": body.screen_height,
            "model": selected_model,
            "mode": body.mode or "auto",
            "isolated_app": body.isolated_app,
            "active_skills": body.active_skills,
            "project_folder": str(selected_project_folder) if selected_project_folder else None,
            "environment": environment,
            "plan_first": body.plan_first,
            "notify_on_completion": body.notify_on_completion,
            "auto_commit": body.auto_commit,
            "autonomy_level": body.autonomy_level,
            "thinking_budget": body.thinking_budget,
        }
        if active >= _MAX_ACTIVE_TASKS:
            context = AgentContext(
                goal=body.goal,
                screen_width=body.screen_width,
                screen_height=body.screen_height,
                isolated_app=body.isolated_app,
                active_skills=body.active_skills,
                project_folder=str(selected_project_folder) if selected_project_folder else None,
                environment=environment,
            )
            record = TaskRecord(
                id=body.task_id,
                status="queued",
                context=context,
                goal=body.goal,
                model=selected_model,
                mode=body.mode or "auto",
                plan_first=body.plan_first,
                notify_on_completion=body.notify_on_completion,
                auto_commit=body.auto_commit,
                autonomy_level=body.autonomy_level,
            )
            _tasks[body.task_id] = record
            _queued_task_specs.append(spec)
            _save_task_record(record)
            log_emitter.emit(body.task_id, "task_created", {
                "task_id": body.task_id,
                "goal": body.goal,
                "model": selected_model,
                "mode": record.mode,
                "created_at": record.created_at,
                "project_folder": record.context.project_folder,
            })
            log_emitter.emit(body.task_id, "queued", {
                "task_id": body.task_id,
                "position": len(_queued_task_specs),
                "max_active_tasks": _MAX_ACTIVE_TASKS,
            })
            return {"task_id": body.task_id, "status": "queued", "position": len(_queued_task_specs)}

        print(f"[API] Initializing task {body.task_id}...", flush=True)
        record = _start_task_from_spec(spec)
        log_emitter.emit(body.task_id, "task_created", {
            "task_id": body.task_id,
            "goal": body.goal,
            "model": selected_model,
            "mode": record.mode,
            "created_at": record.created_at,
            "project_folder": record.context.project_folder,
            "plan_first": body.plan_first,
            "notify_on_completion": body.notify_on_completion,
            "auto_commit": body.auto_commit,
            "autonomy_level": body.autonomy_level,
        })
        print(f"[API] Task {body.task_id} initialized successfully", flush=True)
        return {"task_id": body.task_id, "status": "running"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to init task {body.task_id}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, request: Request, credentials: HTTPAuthorizationCredentials = Security(bearer)):
    _validate_task_id(task_id)
    await verify_token(request, credentials)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return _serialize_task_record(record)


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def cancel_task(task_id: str):
    _validate_task_id(task_id)
    if task_id in _tasks and _tasks[task_id].status == "queued":
        _queued_task_specs[:] = [spec for spec in _queued_task_specs if spec.get("task_id") != task_id]
        _tasks[task_id].status = "cancelled"
        _tasks[task_id].finished_at = datetime.now(timezone.utc).isoformat()
        _tasks[task_id].reason = "Queued task cancelled by user"
        _save_task_record(_tasks[task_id])
        log_emitter.emit(task_id, "cancelled", {"message": "Queued task cancelled by user", "finished_at": datetime.now(timezone.utc).isoformat()})
        return {"task_id": task_id, "status": "cancelled"}
    cancelled = service.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Task not found or already complete")
    if task_id in _tasks:
        _tasks[task_id].status = "cancelled"
        _tasks[task_id].finished_at = datetime.now(timezone.utc).isoformat()
        _tasks[task_id].reason = "Task cancelled by user"
        _save_task_record(_tasks[task_id])
    log_emitter.emit(task_id, "cancelled", {"message": "Task cancelled by user", "finished_at": datetime.now(timezone.utc).isoformat()})
    log_emitter.cleanup_task(task_id)

    return {"task_id": task_id, "status": "cancelled"}


@app.post("/api/tasks/{task_id}/kill", dependencies=[Depends(verify_token)])
async def kill_task(task_id: str):
    _validate_task_id(task_id)
    killed = service.cancel_task(task_id)
    if not killed:
        raise HTTPException(status_code=404, detail="Task not found or already complete")
    if task_id in _tasks:
        _tasks[task_id].status = "cancelled"
        _tasks[task_id].finished_at = datetime.now(timezone.utc).isoformat()
        _tasks[task_id].reason = "Task killed by user"
        _save_task_record(_tasks[task_id])
    log_emitter.emit(task_id, "cancelled", {"message": "Task killed by user", "finished_at": datetime.now(timezone.utc).isoformat()})
    log_emitter.cleanup_task(task_id)
    return {"task_id": task_id, "status": "cancelled", "reason": "Task killed by user"}

@app.post("/api/tasks/{task_id}/pause", dependencies=[Depends(verify_token)])
async def pause_task(task_id: str):
    _validate_task_id(task_id)
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    _tasks[task_id].paused = True
    _save_task_record(_tasks[task_id])
    service.pause_task(task_id)
    log_emitter.emit(task_id, "status", {"message": "Task paused."})
    return {"status": "paused"}

@app.post("/api/tasks/{task_id}/resume", dependencies=[Depends(verify_token)])
async def resume_task(task_id: str):
    _validate_task_id(task_id)
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    _tasks[task_id].paused = False
    _save_task_record(_tasks[task_id])
    service.resume_task(task_id)
    log_emitter.emit(task_id, "status", {"message": "Task resumed."})
    return {"status": "resumed"}

@app.get("/api/tasks/{task_id}/log", dependencies=[Depends(verify_token)])
async def get_task_log(task_id: str):
    _validate_task_id(task_id)
    log_path = log_emitter.log_path(task_id)
    if not log_path.exists():
        return {"log": []}  # task exists without log (old task or pre-emit); return empty replay
    return {"log": log_emitter.read_log(task_id)}


@app.get("/api/tasks/{task_id}/log/download", dependencies=[Depends(verify_token)])
async def download_task_log(task_id: str):
    _validate_task_id(task_id)
    log_path = log_emitter.log_path(task_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(log_path, media_type="application/json", filename=f"{task_id}.jsonl")


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    note: str = ""


@app.post("/api/tasks/{task_id}/feedback", dependencies=[Depends(verify_token)])
async def task_feedback(task_id: str, body: FeedbackIn):
    _validate_task_id(task_id)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    path = append_feedback(workspace_dir, task_id, body.rating, body.note[:1000])
    record.metadata.setdefault("feedback", []).append({"rating": body.rating, "note": body.note[:1000], "path": str(path)})
    _save_task_record(record)
    return {"ok": True, "path": str(path)}


@app.post("/api/tasks/{task_id}/checkpoint/revert", dependencies=[Depends(verify_token)])
async def revert_task_checkpoint(task_id: str):
    _validate_task_id(task_id)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    commit = record.checkpoint_commit or (record.metadata.get("checkpoint") or {}).get("commit", "")
    if not commit:
        raise HTTPException(status_code=400, detail="Task has no checkpoint commit to revert")
    result = revert_git_checkpoint(_task_workspace_for_record(record), commit)
    log_emitter.emit(task_id, "checkpoint_revert", result)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Checkpoint revert failed"))
    return result


@app.post("/api/tasks/{task_id}/git/revert", dependencies=[Depends(verify_token)])
async def revert_file_commit(task_id: str, body: Dict[str, Any] = Body(...)):
    """Revert a per-file auto-commit produced by the coding agent."""
    _validate_task_id(task_id)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    commit_hash = (body.get("commit_hash") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_hash):
        raise HTTPException(status_code=400, detail="Invalid commit_hash")
    workspace = _task_workspace_for_record(record)
    result = revert_git_checkpoint(workspace, commit_hash)
    log_emitter.emit(task_id, "file_revert", {"commit_hash": commit_hash, **result})
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Revert failed"))
    return result


@app.get("/api/model-health", dependencies=[Depends(verify_token)])
async def model_health():
    return {
        "max_active_tasks": _MAX_ACTIVE_TASKS,
        "active_tasks": len(service._active_tasks),
        "queued_tasks": len(_queued_task_specs),
        "ollama": detect_ollama(),
    }


@app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(verify_token)])
async def retry_task(task_id: str):
    _validate_task_id(task_id)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    for retry_num in range(1, 1000):
        _retry_suffix = f"-retry-{retry_num}"
        candidate = f"{task_id[:128 - len(_retry_suffix)]}{_retry_suffix}"
        if candidate not in _tasks and not _task_store_path(candidate).exists():
            new_task_id = candidate
            break
    else:
        new_task_id = f"{task_id[:119]}-retry-{secrets.token_hex(4)}"
    model = record.model
    mode = record.mode or "auto"
    goal = record.goal or record.context.goal
    if not goal:
        raise HTTPException(status_code=400, detail="Task has no goal to retry")
    new_record = service.init_task(
        task_id=new_task_id,
        goal=goal,
        screen_width=record.context.screen_width,
        screen_height=record.context.screen_height,
        model=model or "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        mode=mode,
        isolated_app=record.context.isolated_app,
        active_skills=record.context.active_skills,
        project_folder=record.context.project_folder,
        environment=record.context.environment,
        plan_first=record.plan_first,
        notify_on_completion=record.notify_on_completion,
        auto_commit=record.auto_commit,
        autonomy_level=record.autonomy_level,
    )
    _tasks[new_task_id] = new_record
    _save_task_record(new_record)
    log_emitter.emit(new_task_id, "task_created", {
        "task_id": new_task_id,
        "goal": goal,
        "model": new_record.model,
        "mode": new_record.mode,
        "created_at": new_record.created_at,
        "retried_from": task_id,
        "project_folder": new_record.context.project_folder,
    })
    return {"task_id": new_task_id, "status": "running", "retried_from": task_id}


@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request, since: int = 0, keepalive_timeout_seconds: int = 30, credentials: HTTPAuthorizationCredentials = Security(bearer)):
    _validate_task_id(task_id)
    if not _is_authorized(request, credentials):
        async def _bad_auth():
            yield 'data: {"type":"error","message":"unauthorized"}\n\n'
        return StreamingResponse(_bad_auth(), media_type="text/event-stream", status_code=401)
    if not (5 <= keepalive_timeout_seconds <= 300):
        raise HTTPException(status_code=400, detail="keepalive_timeout_seconds must be between 5 and 300")

    async def event_generator():
        # Replay persisted events first so fast-completing tasks aren't missed
        log_path = log_emitter.log_path(task_id)
        terminal_seen = False
        total_events = log_emitter.count_events(task_id)
        for ev in log_emitter.read_log(task_id, since=max(0, since)):
            event_id = ev.get("seq")
            prefix = f"id: {event_id}\n" if event_id is not None else ""
            yield f"{prefix}data: {json.dumps(ev)}\n\n"
            if ev.get("type") in ("done", "error", "cancelled"):
                terminal_seen = True
        if terminal_seen:
            return

        record = _get_task_record(task_id)
        if record and _is_terminal_status(record.status) and since >= total_events:
            return

        q = log_emitter.subscribe(task_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=float(keepalive_timeout_seconds))
                    event_id = msg.get("seq")
                    prefix = f"id: {event_id}\n" if event_id is not None else ""
                    yield f"{prefix}data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("done", "error", "cancelled"):
                        await asyncio.sleep(0.5) # Give ASGI server time to flush to the client
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            log_emitter.unsubscribe(task_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/approvals", dependencies=[Depends(verify_token)])
async def approvals(body: ApprovalIn):
    service.submit_approval(body.task_id, body.action_id, body.approve, body.plan_override)
    return {"ok": True}


@app.post("/api/permissions", dependencies=[Depends(verify_token)])
async def permissions(body: PermissionIn):
    service.submit_permission(body.task_id, body.action_id, body.grant)
    return {"ok": True, "scope": body.scope, "granted": body.grant}


@app.get("/api/permissions/{task_id}", dependencies=[Depends(verify_token)])
async def list_permissions(task_id: str):
    return {"task_id": task_id, "granted": service.permissions.granted_scopes(task_id)}


# ── Automation (Watch & Act slice 1, AI-7) ────────────────────────────────────

@app.get("/api/automation", dependencies=[Depends(verify_token)])
async def list_automation():
    from .automation import get_registry
    return {"triggers": get_registry().list_triggers()}


@app.post("/api/automation", dependencies=[Depends(verify_token)])
async def add_automation(body: AutomationIn):
    from .automation import get_registry, CronTrigger
    try:
        trigger = CronTrigger(body.schedule, body.task_template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return get_registry().add(trigger)


@app.delete("/api/automation/{trigger_id}", dependencies=[Depends(verify_token)])
async def remove_automation(trigger_id: str):
    from .automation import get_registry
    removed = get_registry().remove(trigger_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"removed": True, "id": trigger_id}
