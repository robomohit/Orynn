from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from PIL import Image


RULE_FILES = (
    "AGENTS.md",
    "CODEX.md",
    "CLAUDE.md",
    ".cursorrules",
    ".rules",
    ".github/copilot-instructions.md",
)

DEFAULT_WORKFLOWS: Dict[str, str] = {
    "review": "Review the relevant changes for bugs, regressions, security issues, and missing tests before proposing fixes.",
    "security": "Run a security-oriented pass: check secrets, unsafe shell use, SSRF, path traversal, auth bypasses, and prompt/tool injection risks.",
    "test": "Create or run the smallest useful test set, fix failures, and summarize exactly what passed.",
    "release": "Prepare a release checklist: tests, docs, migration notes, known risks, and rollback steps.",
    "debug": "Reproduce the issue, isolate the failing layer, make the smallest fix, and verify it with a focused test.",
}


def _safe_read_text(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.strip()[:max_chars]


def discover_project_rules(workspace: Path, *, max_chars: int = 12000) -> str:
    """Collect durable project rules from common agent-instruction files.

    Inspired by Cursor/Windsurf/Zed/GitHub Copilot rule discovery. This is
    deterministic context, so it works well even when the selected model is free
    and small.
    """
    workspace = workspace.expanduser().resolve()
    chunks: List[str] = []
    for rel in RULE_FILES:
        path = workspace / rel
        if path.exists() and path.is_file():
            text = _safe_read_text(path, 3000)
            if text:
                chunks.append(f"### {rel}\n{text}")

    for rules_dir in (workspace / ".windsurf" / "rules", workspace / ".aicomputer" / "rules"):
        if not rules_dir.exists():
            continue
        for path in sorted(rules_dir.glob("*.md"))[:8]:
            text = _safe_read_text(path, 2000)
            if text:
                chunks.append(f"### {path.relative_to(workspace)}\n{text}")

    result = "\n\n".join(chunks)
    return result[:max_chars]


def expand_workflow_goal(goal: str, workspace: Path) -> str:
    """Expand `/workflow rest of prompt` commands using local workflow files.

    Local workflow files live at `.aicomputer/workflows/<name>.md`; a small set
    of useful built-ins is provided so the feature works immediately.
    """
    text = (goal or "").strip()
    match = re.match(r"^/(?P<name>[A-Za-z0-9_-]+)(?:\s+(?P<body>.*))?$", text, re.DOTALL)
    if not match:
        return goal
    name = match.group("name").lower()
    body = (match.group("body") or "").strip()
    workflow_path = workspace.expanduser().resolve() / ".aicomputer" / "workflows" / f"{name}.md"
    workflow = _safe_read_text(workflow_path, 6000) if workflow_path.exists() else DEFAULT_WORKFLOWS.get(name, "")
    if not workflow:
        return goal
    return (
        f"{body or 'Run the requested workflow.'}\n\n"
        f"Workflow /{name}:\n{workflow}"
    ).strip()


def build_preflight_plan(goal: str, *, mode: str, autonomy_level: str = "balanced") -> Dict[str, Any]:
    """Build a cheap deterministic review plan before execution.

    This avoids spending a slow free-model call just to create a first-pass plan,
    while still giving the user a chance to correct course.
    """
    g = (goal or "").strip()
    lower = g.lower()
    steps: List[str] = ["Restate the goal and identify constraints."]
    if mode == "coding" or any(k in lower for k in ("code", "test", "file", "repo", "fix", "implement")):
        steps.extend([
            "Inspect the relevant files and project instructions.",
            "Make the smallest scoped code or config change.",
            "Run focused tests or static checks for the touched behavior.",
        ])
    elif mode == "computer_use":
        steps.extend([
            "Open or search for the target page.",
            "Read the page through browser text/accessibility data.",
            "Perform the requested interaction or extract the requested result.",
        ])
    elif mode in {"computer", "computer_isolated", "explain"}:
        steps.extend([
            "Observe the current screen or target window.",
            "Act only on the requested surface with approval gates for risky steps.",
            "Verify the visible result before finishing.",
        ])
    else:
        steps.extend([
            "Gather the minimum context needed.",
            "Answer or act in small verifiable steps.",
        ])
    if autonomy_level == "careful":
        steps.append("Pause for approval before tool actions that change files, apps, or external state.")
    elif autonomy_level == "fast":
        steps.append("Prefer direct deterministic tools and avoid extra reflection calls.")
    else:
        steps.append("Keep progress visible and verify before finalizing.")

    return {
        "reasoning": "Preflight plan generated locally for review before execution.",
        "sub_tasks": [{"id": f"plan-{i + 1}", "description": step} for i, step in enumerate(steps)],
    }


def ocr_text_from_b64(image_b64: Optional[str], *, max_chars: int = 3000) -> str:
    if not image_b64:
        return ""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return ""
    try:
        payload = image_b64.split(",", 1)[1] if image_b64.strip().startswith("data:") else image_b64
        raw = base64.b64decode(payload)
        with Image.open(io.BytesIO(raw)) as image:
            text = pytesseract.image_to_string(image)
    except Exception:
        return ""
    return (text or "").strip()[:max_chars]


def detect_ollama(base_url: Optional[str] = None, *, timeout: float = 0.8) -> Dict[str, Any]:
    base = (base_url or os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        response = httpx.get(f"{base}/api/tags", timeout=timeout)
        response.raise_for_status()
        data = response.json()
        models = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
        return {"available": True, "base_url": base, "models": models}
    except Exception as exc:
        return {"available": False, "base_url": base, "models": [], "detail": str(exc)[:200]}


def run_task_hooks(workspace: Path, event: str, payload: Dict[str, Any], *, timeout: float = 60.0) -> List[Dict[str, Any]]:
    """Run optional local hooks from `.aicomputer/hooks.json`.

    Shape:
      {"task_done": [{"name": "tests", "command": "pytest -q"}]}
    """
    config_path = workspace.expanduser().resolve() / ".aicomputer" / "hooks.json"
    if not config_path.exists():
        return []
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"name": "hooks.json", "ok": False, "output": f"Invalid hooks config: {exc}"}]
    hooks = config.get(event, [])
    if not isinstance(hooks, list):
        return []
    results: List[Dict[str, Any]] = []
    env = os.environ.copy()
    env.update({f"AI_COMPUTER_{k.upper()}": str(v) for k, v in payload.items() if isinstance(v, (str, int, float, bool))})
    for idx, hook in enumerate(hooks[:5]):
        if not isinstance(hook, dict) or not hook.get("command"):
            continue
        name = str(hook.get("name") or f"hook-{idx + 1}")
        try:
            proc = subprocess.run(
                str(hook["command"]),
                cwd=str(workspace),
                shell=True,
                capture_output=True,
                text=True,
                timeout=float(hook.get("timeout", timeout)),
                env=env,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            results.append({"name": name, "ok": proc.returncode == 0, "returncode": proc.returncode, "output": out[:4000]})
        except Exception as exc:
            results.append({"name": name, "ok": False, "output": str(exc)[:1000]})
    return results


def _git(workspace: Path, args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )


def create_git_checkpoint(workspace: Path, task_id: str, message: str) -> Dict[str, Any]:
    """Create an opt-in git checkpoint commit for one-click rollback."""
    workspace = workspace.expanduser().resolve()
    inside = _git(workspace, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0:
        return {"ok": False, "skipped": True, "reason": "not a git repository"}
    status = _git(workspace, ["status", "--porcelain"])
    if status.returncode != 0 or not status.stdout.strip():
        return {"ok": True, "skipped": True, "reason": "no changes"}
    _git(workspace, ["add", "-A"])
    commit_msg = f"AI Computer checkpoint: {message[:72] or task_id}\n\nTask: {task_id}"
    commit = _git(workspace, ["commit", "-m", commit_msg])
    if commit.returncode != 0:
        return {"ok": False, "error": (commit.stderr or commit.stdout).strip()[:1000]}
    rev = _git(workspace, ["rev-parse", "HEAD"])
    sha = (rev.stdout or "").strip()
    return {"ok": True, "commit": sha, "message": commit_msg}


def revert_git_checkpoint(workspace: Path, commit: str) -> Dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit or ""):
        return {"ok": False, "error": "Invalid checkpoint commit."}
    proc = _git(workspace, ["revert", "--no-edit", commit])
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[:1000]}
    return {"ok": True, "output": (proc.stdout or "").strip()[:1000]}


def send_completion_notification(goal: str, status: str, reason: str) -> Dict[str, Any]:
    """Send best-effort completion notifications via common free channels."""
    title = f"AI Computer task {status}"
    text = f"{title}\n\nGoal: {goal[:500]}\n\nResult: {reason[:1000]}"
    sent: List[str] = []
    errors: List[str] = []

    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord_webhook:
        try:
            httpx.post(discord_webhook, json={"content": text[:1900]}, timeout=8.0).raise_for_status()
            sent.append("discord")
        except Exception as exc:
            errors.append(f"discord: {exc}")

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": text[:3900]},
                timeout=8.0,
            ).raise_for_status()
            sent.append("telegram")
        except Exception as exc:
            errors.append(f"telegram: {exc}")

    return {"ok": bool(sent), "sent": sent, "errors": errors, "timestamp": datetime.now(timezone.utc).isoformat()}


def append_feedback(workspace: Path, task_id: str, rating: str, note: str = "") -> Path:
    out_dir = workspace.expanduser().resolve() / "logs" / "feedback"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{task_id}-{int(time.time())}.json"
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "rating": rating,
                "note": note,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
