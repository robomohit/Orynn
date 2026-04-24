from __future__ import annotations
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)
import asyncio
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List, Literal
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from .agent import AgentService
from .log_emitter import log_emitter
from .models import AgentContext, TaskRecord
from .skills import skill_manager

API_KEY = os.environ.get("AGENT_API_KEY") or secrets.token_hex(32)
_masked = API_KEY[:6] + "***" + API_KEY[-4:] if len(API_KEY) > 10 else "***"
print(f"[AI_Computer] Agent API Key: {_masked} (use /api/config to retrieve full key)", flush=True)

from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(application):
    from .mcp_manager import mcp_manager
    asyncio.create_task(mcp_manager.initialize_default_servers(str(Path(".").absolute())))
    yield
    # Shutdown: clean up background browsers
    await service.shutdown()

app = FastAPI(title="AI Computer", lifespan=_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer(auto_error=False)
_tasks: Dict[str, TaskRecord] = {}

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(bearer)):
    if credentials is None or credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

workspace_dir = Path(".")
workspace_dir.mkdir(parents=True, exist_ok=True)
(workspace_dir / "logs").mkdir(parents=True, exist_ok=True)
task_store_dir = workspace_dir / "tasks"
task_store_dir.mkdir(parents=True, exist_ok=True)
service = AgentService(workspace_dir, log_emitter=log_emitter)


def _task_store_path(task_id: str) -> Path:
    return task_store_dir / f"{task_id}.json"


def _is_terminal_status(status: Optional[str]) -> bool:
    return status in {"done", "failed", "cancelled", "complete", "error"}


def _save_task_record(record: TaskRecord) -> None:
    _task_store_path(record.id).write_text(
        json.dumps(record.model_dump(), indent=2),
        encoding="utf-8",
    )


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
        except Exception:
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


_tasks = _load_persisted_tasks()

def _on_complete(task_id: str, status: str, reason: str):
    rec = _tasks.get(task_id)
    if rec:
        rec.status = status
        rec.finished_at = datetime.now(timezone.utc).isoformat()
        rec.reason = reason
        _save_task_record(rec)

service._on_task_complete = _on_complete

from pydantic import BaseModel, Field

class TaskIn(BaseModel):
    task_id: str
    goal: str = Field(..., min_length=5, max_length=2000)
    model: Optional[str] = None  # None = auto-pick from available keys
    mode: Literal["auto", "coding", "computer", "computer_use", "computer_isolated"] = "auto"
    screen_width: int = 1280
    screen_height: int = 800
    isolated_app: Optional[str] = None  # partial window title to target in isolated mode
    active_skills: List[str] = []

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
    servers = []
    for name, srv in mcp_manager.servers.items():
        servers.append({
            "name": name,
            "tools": srv.tools
        })
    return {"servers": servers}

@app.get("/api/config")
async def config():
    return {"api_key": API_KEY}

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
                "id": t.id,
                "goal": t.goal or t.context.goal,
                "status": t.status,
                "paused": t.paused,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
                "reason": t.reason,
                "model": t.model,
                "mode": t.mode,
            }
            for t in ordered
        ]
    }


@app.post("/api/tasks", dependencies=[Depends(verify_token)])
async def create_task(body: TaskIn):
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
        )
        _tasks[body.task_id] = record
        _save_task_record(record)
        log_emitter.emit(body.task_id, "task_created", {
            "task_id": body.task_id,
            "goal": body.goal,
            "model": selected_model,
            "mode": record.mode,
            "created_at": record.created_at,
        })
        print(f"[API] Task {body.task_id} initialized successfully", flush=True)
        return {"task_id": body.task_id, "status": "running"}
    except Exception as e:
        print(f"[ERROR] Failed to init task {body.task_id}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, credentials: HTTPAuthorizationCredentials = Security(bearer)):
    await verify_token(credentials)
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return record


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def cancel_task(task_id: str):
    cancelled = service.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Task not found or already complete")
    if task_id in _tasks:
        _tasks[task_id].status = "cancelled"
        _tasks[task_id].finished_at = datetime.now(timezone.utc).isoformat()
        _tasks[task_id].reason = "Task cancelled by user"
        _save_task_record(_tasks[task_id])
    log_emitter.emit(task_id, "cancelled", {"message": "Task cancelled by user"})

    return {"task_id": task_id, "status": "cancelled"}

@app.post("/api/tasks/{task_id}/pause", dependencies=[Depends(verify_token)])
async def pause_task(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    _tasks[task_id].paused = True
    _save_task_record(_tasks[task_id])
    service.pause_task(task_id)
    log_emitter.emit(task_id, "status", {"message": "Task paused."})
    return {"status": "paused"}

@app.post("/api/tasks/{task_id}/resume", dependencies=[Depends(verify_token)])
async def resume_task(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    _tasks[task_id].paused = False
    _save_task_record(_tasks[task_id])
    service.resume_task(task_id)
    log_emitter.emit(task_id, "status", {"message": "Task resumed."})
    return {"status": "resumed"}

@app.get("/api/tasks/{task_id}/log", dependencies=[Depends(verify_token)])
async def get_task_log(task_id: str):
    log_path = log_emitter.log_path(task_id)
    if not log_path.exists():
        return {"log": []}  # task exists without log (old task or pre-emit); return empty replay
    return {"log": log_emitter.read_log(task_id)}


@app.get("/api/tasks/{task_id}/log/download", dependencies=[Depends(verify_token)])
async def download_task_log(task_id: str):
    log_path = log_emitter.log_path(task_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(log_path, media_type="application/json", filename=f"{task_id}.jsonl")


@app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(verify_token)])
async def retry_task(task_id: str):
    record = _get_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    new_task_id = f"{task_id}-retry-{int(time.time())}"
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
    })
    return {"task_id": new_task_id, "status": "running", "retried_from": task_id}


@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request, token: Optional[str] = None, since: int = 0):
    p_token = token or ""
    if p_token != API_KEY:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != API_KEY:
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
