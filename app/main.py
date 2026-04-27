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
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .agent import AgentService
from .log_emitter import log_emitter
from .models import AgentContext, TaskRecord
from .skills import skill_manager

API_KEY = os.environ.get("AGENT_API_KEY") or secrets.token_hex(32)
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

    asyncio.create_task(_init_mcp())
    yield
    # Shutdown: clean up background browsers
    await service.shutdown()

app = FastAPI(title="AI Computer", lifespan=_lifespan)
_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer(auto_error=False)
_tasks: Dict[str, TaskRecord] = {}

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


def _on_complete(task_id: str, status: str, reason: str):
    rec = _tasks.get(task_id)
    if rec:
        rec.status = status
        rec.finished_at = datetime.now(timezone.utc).isoformat()
        rec.reason = reason
        _save_task_record(rec)
    # Release per-task in-memory state in the log emitter (seq counter, disk flag)
    log_emitter.cleanup_task(task_id)
    # Evict oldest completed tasks to cap the in-memory dict size
    _evict_old_tasks()

service._on_task_complete = _on_complete

from pydantic import BaseModel, Field

class TaskIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=128, pattern=TASK_ID_PATTERN)
    goal: str = Field(..., min_length=1, max_length=2000)
    model: Optional[str] = None  # None = auto-pick from available keys
    mode: Literal["auto", "coding", "computer", "computer_use", "computer_isolated"] = "auto"
    screen_width: int = 1280
    screen_height: int = 800
    isolated_app: Optional[str] = None  # partial window title to target in isolated mode
    active_skills: List[str] = []
    project_folder: Optional[str] = None

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
START_TIME = time.time()

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": time.time() - START_TIME
    }

@app.get("/api/skills")
async def get_skills():
    return {"skills": skill_manager.get_all_skills()}

@app.get("/api/mcp")
async def get_mcp():
    from .mcp_manager import mcp_manager
    await mcp_manager.initialize_default_servers(mcp_manager._workspace_path or str(HOME_DIR))
    servers = []
    for name, srv in mcp_manager.servers.items():
        servers.append({
            "name": name,
            "tools": srv.tools
        })
    return {"servers": servers}

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
    return {
        "models": keyed,
        "configured_providers": configured_providers,
        "has_keys": len(keyed) > 0,
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


@app.post("/api/tasks", dependencies=[Depends(verify_token)])
async def create_task(body: TaskIn):
    _validate_task_id(body.task_id)
    print(f"[API] create_task: {body.task_id} (model={body.model}, mode={body.mode})", flush=True)
    existing = _tasks.get(body.task_id)
    if existing and existing.status in {"running", "paused", "pending"}:
        raise HTTPException(status_code=409, detail=f"Task '{body.task_id}' already exists and is still active")
    if existing and existing.status in {"done", "failed", "cancelled", "complete"}:
        raise HTTPException(status_code=409, detail=f"Task '{body.task_id}' already exists")
    active = len(service._active_tasks)
    if active >= 5:
        raise HTTPException(status_code=429, detail="max concurrent tasks reached")

    # Auto-pick a model from whatever keys are available when none is specified
    if not body.model:
        if os.environ.get("OPENROUTER_API_KEY"):
            # Qwen3-Coder is purpose-built for code; use it for coding mode
            if (body.mode or "auto") == "coding":
                selected_model = "openrouter/qwen/qwen3-coder:free"
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
        else:
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
        print(f"[API] Initializing task {body.task_id}...", flush=True)
        record = service.init_task(
            task_id=body.task_id,
            goal=body.goal,
            screen_width=body.screen_width,
            screen_height=body.screen_height,
            model=selected_model,
            mode=body.mode or "auto",
            isolated_app=body.isolated_app,
            active_skills=body.active_skills,
            project_folder=str(selected_project_folder) if selected_project_folder else None,
            environment=environment,
        )
        _tasks[body.task_id] = record
        _save_task_record(record)
        log_emitter.emit(body.task_id, "task_created", {
            "task_id": body.task_id,
            "goal": body.goal,
            "model": selected_model,
            "mode": record.mode,
            "created_at": record.created_at,
            "project_folder": record.context.project_folder,
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


@app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(verify_token)])
async def retry_task(task_id: str):
    _validate_task_id(task_id)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    _retry_suffix = f"-retry-{int(time.time())}"
    new_task_id = f"{task_id[:128 - len(_retry_suffix)]}{_retry_suffix}"
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
async def stream_task(task_id: str, request: Request, since: int = 0, credentials: HTTPAuthorizationCredentials = Security(bearer)):
    _validate_task_id(task_id)
    if not _is_authorized(request, credentials):
        async def _bad_auth():
            yield 'data: {"type":"error","message":"unauthorized"}\n\n'
        return StreamingResponse(_bad_auth(), media_type="text/event-stream", status_code=401)

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
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
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
    service.submit_approval(body.task_id, body.action_id, body.approve)
    return {"ok": True}


@app.post("/api/permissions", dependencies=[Depends(verify_token)])
async def permissions(body: PermissionIn):
    service.submit_permission(body.task_id, body.action_id, body.grant)
    return {"ok": True, "scope": body.scope, "granted": body.grant}


@app.get("/api/permissions/{task_id}", dependencies=[Depends(verify_token)])
async def list_permissions(task_id: str):
    return {"task_id": task_id, "granted": service.permissions.granted_scopes(task_id)}
