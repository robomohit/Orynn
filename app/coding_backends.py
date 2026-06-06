"""Pluggable coding backends.

Orynn runs on cheap/free orchestration models. Those are weak at real
coding, so coding-heavy subtasks can be delegated to a stronger backend the
user has connected — e.g. the Claude Code CLI.

Design (from a study of Claude Code, Codex, and OpenClaw/ACPX):
- A backend is a named adapter with a small, uniform interface:
    detect()           -> probe availability + version
    submit(brief)      -> run a coding brief, return a structured result
    resume(sid, text)  -> continue a previous session
- Adapters shell out to a headless CLI and parse STRUCTURED JSON output —
  never PTY scraping.
- Backends are declared in a single config file so the user (and a Settings
  UI) can manage connectors. If the file is absent, a sensible default
  (the `claude` CLI on PATH) is assumed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Where user-declared backends live. Mirrors the .acpxrc.json shape.
DEFAULT_CONFIG_PATH = Path.home() / ".orynn" / "backends.json"
LEGACY_CONFIG_PATH = Path.home() / ".aicomputer" / "backends.json"

# A coding brief can run long (multi-file edits + tests). Hard ceiling.
SUBMIT_TIMEOUT_SECONDS = 900


@dataclass
class CodingBrief:
    """A self-contained coding task handed to a backend."""
    task: str
    repo_path: str = ""
    files: List[str] = field(default_factory=list)
    constraints: str = ""

    def to_prompt(self) -> str:
        parts = [self.task.strip()]
        if self.constraints:
            parts.append(f"\nConstraints:\n{self.constraints.strip()}")
        if self.files:
            parts.append(f"\nRelevant files: {', '.join(self.files)}")
        return "\n".join(parts)


@dataclass
class CodingResult:
    """Uniform result envelope returned by every backend."""
    ok: bool
    summary: str = ""
    files_changed: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    session_id: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "files_changed": self.files_changed,
            "cost_usd": self.cost_usd,
            "session_id": self.session_id,
            "error": self.error,
        }


class CodingBackend:
    """Base adapter. Subclasses delegate a brief to an external coding agent."""
    type = "base"

    def __init__(self, name: str, command: str, model: str = "", **extra: Any):
        self.name = name
        self.command = command
        self.model = model
        self.extra = extra

    def detect(self) -> Dict[str, Any]:
        """Return {available: bool, version: str, detail: str}."""
        raise NotImplementedError

    def submit(self, brief: CodingBrief) -> CodingResult:
        raise NotImplementedError

    def resume(self, session_id: str, followup: str, repo_path: str = "") -> CodingResult:
        raise NotImplementedError


class ClaudeCodeBackend(CodingBackend):
    """Delegates to the headless Claude Code CLI (`claude -p ... --output-format json`)."""
    type = "claude"

    def _resolve(self) -> Optional[str]:
        """Full path to the executable. Required on Windows where the CLI is a
        `.cmd` shim — subprocess (CreateProcess) does no PATHEXT resolution, so
        a bare ``claude`` fails with WinError 2 even though it's on PATH."""
        return shutil.which(self.command)

    def detect(self) -> Dict[str, Any]:
        exe = self._resolve()
        if not exe:
            return {"available": False, "version": "", "detail": f"'{self.command}' not found on PATH"}
        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            version = (proc.stdout or proc.stderr or "").strip()
            return {
                "available": proc.returncode == 0,
                "version": version,
                "detail": exe,
            }
        except (subprocess.SubprocessError, OSError) as exc:
            return {"available": False, "version": "", "detail": str(exc)}

    def _run(self, cli_args: List[str], repo_path: str) -> CodingResult:
        exe = self._resolve()
        if not exe:
            return CodingResult(ok=False, error=f"'{self.command}' not found on PATH")
        cmd = [exe] + cli_args + ["--output-format", "json", "--permission-mode", "acceptEdits"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                cwd=repo_path or None,
                timeout=SUBMIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return CodingResult(ok=False, error=f"{self.name} timed out after {SUBMIT_TIMEOUT_SECONDS}s")
        except OSError as exc:
            return CodingResult(ok=False, error=f"Failed to launch '{self.command}': {exc}")

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "non-zero exit").strip()
            return CodingResult(ok=False, error=detail[:600])

        # Headless `--output-format json` yields {result, session_id, total_cost_usd, is_error, ...}
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # Tolerate a plain-text response rather than failing the delegation.
            text = (proc.stdout or "").strip()
            return CodingResult(ok=bool(text), summary=text[:4000],
                                error="" if text else "empty response from backend")

        if not isinstance(data, dict):
            return CodingResult(ok=False, error="backend returned a non-object JSON response")

        structured = data.get("structured_output") if isinstance(data.get("structured_output"), dict) else {}
        summary = structured.get("summary") or (data.get("result") or "")
        return CodingResult(
            ok=not data.get("is_error", False),
            summary=str(summary)[:4000],
            files_changed=list(structured.get("files_changed", []) or []),
            cost_usd=float(data.get("total_cost_usd", 0) or 0),
            session_id=str(data.get("session_id", "") or ""),
        )

    def submit(self, brief: CodingBrief) -> CodingResult:
        return self._run(["-p", brief.to_prompt()], brief.repo_path)

    def resume(self, session_id: str, followup: str, repo_path: str = "") -> CodingResult:
        if not session_id:
            return CodingResult(ok=False, error="resume() requires a session_id")
        return self._run(["--resume", session_id, "-p", followup], repo_path)


# Adapter type -> class. Add new backend types here.
_BACKEND_TYPES: Dict[str, type] = {
    "claude": ClaudeCodeBackend,
}


class BackendRegistry:
    """Loads declared coding backends from config and exposes detection."""

    def __init__(self, config_path: Optional[Path] = None):
        self.backends: Dict[str, CodingBackend] = {}
        self.default: str = ""
        self._load(config_path or self._default_config_path())

    def _default_config_path(self) -> Path:
        if DEFAULT_CONFIG_PATH.exists() or not LEGACY_CONFIG_PATH.exists():
            return DEFAULT_CONFIG_PATH
        return LEGACY_CONFIG_PATH

    def _load(self, config_path: Path) -> None:
        cfg: Dict[str, Any] = {}
        try:
            if config_path.exists():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}

        declared = cfg.get("backends")
        if not isinstance(declared, dict) or not declared:
            # No config -> assume the Claude Code CLI on PATH.
            declared = {"claude-code": {"type": "claude", "command": "claude"}}

        for name, spec in declared.items():
            if not isinstance(spec, dict):
                continue
            btype = spec.get("type", "claude")
            cls = _BACKEND_TYPES.get(btype)
            if cls is None:
                raise ValueError(
                    f"Unknown backend type {btype!r} for backend {name!r}; "
                    f"known types: {list(_BACKEND_TYPES)}"
                )
            self.backends[name] = cls(
                name=name,
                command=spec.get("command", "claude"),
                model=spec.get("model", ""),
            )

        self.default = cfg.get("defaultBackend") or next(iter(self.backends), "")

    def get(self, name: Optional[str] = None) -> Optional[CodingBackend]:
        return self.backends.get(name or self.default)

    def detect_all(self) -> Dict[str, Any]:
        """Probe every declared backend. For the Settings connector list."""
        return {
            "default": self.default,
            "backends": [
                {"name": name, "type": b.type, "command": b.command, "model": b.model,
                 **b.detect()}
                for name, b in self.backends.items()
            ],
        }


# Process-wide registry, loaded once.
registry = BackendRegistry()
