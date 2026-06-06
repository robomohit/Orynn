from __future__ import annotations
import asyncio
import json
import subprocess
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

_log = logging.getLogger(__name__)

# Per-server startup timeout (seconds). npx has to download packages on first run.
_START_TIMEOUT = 60.0
# Per-call timeout in seconds.
_CALL_TIMEOUT = 60.0
# Watchdog: if no response arrives for this many seconds while calls are pending → mark dead.
_WATCHDOG_TIMEOUT = 15.0


class MCPServer:
    def __init__(self, name: str, cmd: list[str], env: Optional[Dict[str, str]] = None):
        self.name = name
        self.cmd = cmd
        self.env = env or {}
        self.proc: Optional[subprocess.Popen] = None
        self._id_counter = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_response_at: float = 0.0
        self.tools: List[Dict[str, Any]] = []
        # Health tracking
        self.status: str = "starting"   # starting | running | dead | unresponsive
        self.error: Optional[str] = None

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(exc)

    async def start(self):
        env = os.environ.copy()
        env.update(self.env)

        cmd = list(self.cmd)
        if os.name == "nt" and cmd[0] == "npx":
            cmd[0] = "npx.cmd"

        _log.info("Starting MCP server %s: %s", self.name, " ".join(cmd))
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as e:
            self.status = "dead"
            self.error = f"Command not found: {cmd[0]}"
            raise RuntimeError(self.error) from e

        self._listener_task = asyncio.create_task(self._listen())

        try:
            await asyncio.wait_for(
                self._initialize(),
                timeout=_START_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.status = "unresponsive"
            self.error = f"Timed out after {_START_TIMEOUT}s during initialization"
            await self._kill_proc()
            raise RuntimeError(self.error)
        except Exception as exc:
            self.status = "dead"
            self.error = str(exc)
            await self._kill_proc()
            raise

        self.status = "running"
        self._last_response_at = time.time()
        self._watchdog_task = asyncio.create_task(self._watchdog())
        _log.info("MCP server %s started with %d tools.", self.name, len(self.tools))
        return True

    async def _initialize(self):
        await self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "orynn", "version": "1.0.0"},
        })
        await self.notify("notifications/initialized", {})
        res = await self.call("tools/list", {})
        self.tools = res.get("tools", [])

    async def _kill_proc(self):
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await asyncio.shield(self._listener_task)
            except Exception:
                pass
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.to_thread(self.proc.wait, timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    async def _listen(self):
        if not self.proc or not self.proc.stdout:
            return
        disconnect_error: Optional[Exception] = None
        try:
            while self.proc.poll() is None:
                line = await asyncio.to_thread(self.proc.stdout.readline)
                if not line:
                    disconnect_error = RuntimeError(f"MCP server {self.name} closed stdout")
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    _log.warning("Ignoring invalid JSON from MCP server %s: %s", self.name, e)
                    continue
                if "id" in data:
                    req_id = data["id"]
                    self._last_response_at = time.time()
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        if "error" in data:
                            fut.set_exception(RuntimeError(str(data["error"])))
                        else:
                            fut.set_result(data.get("result", {}))
        except asyncio.CancelledError:
            disconnect_error = RuntimeError(f"MCP server {self.name} listener stopped")
            raise
        except Exception as e:
            disconnect_error = e
            _log.warning("MCP server %s listener failed: %s", self.name, e)
        finally:
            if self.status == "running":
                self.status = "dead"
                self.error = str(disconnect_error) if disconnect_error else "disconnected"
            if self._pending:
                self._fail_pending(disconnect_error or RuntimeError(f"MCP server {self.name} disconnected"))

    async def _watchdog(self, poll: float = 1.0) -> None:
        """Cancel in-flight calls and mark server dead if no response arrives while calls are pending."""
        while self.status == "running":
            await asyncio.sleep(poll)
            if self._pending and time.time() - self._last_response_at > _WATCHDOG_TIMEOUT:
                _log.warning("MCP server %s watchdog: no response in %.0fs; marking dead", self.name, _WATCHDOG_TIMEOUT)
                self.status = "dead"
                self.error = f"watchdog: no response for {_WATCHDOG_TIMEOUT}s"
                if self._listener_task:
                    self._listener_task.cancel()
                self._fail_pending(RuntimeError(f"MCP server {self.name}: watchdog timeout"))
                return

    async def stop(self):
        self.status = "dead"
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._listener_task:
            self._listener_task.cancel()
        if self.proc:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self._fail_pending(RuntimeError(f"MCP server {self.name} stopped"))

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def notify(self, method: str, params: Dict[str, Any]):
        if not self.proc or not self.proc.stdin:
            return
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            _log.warning("MCP server %s notify failed: %s", self.name, e)

    async def call(self, method: str, params: Dict[str, Any], timeout: float = _CALL_TIMEOUT) -> Dict[str, Any]:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError(f"MCP server {self.name} not running")
        if self.proc.poll() is not None:
            self.status = "dead"
            self.error = f"exited with code {self.proc.returncode}"
            self._fail_pending(RuntimeError(f"MCP server {self.name} {self.error}"))
            raise RuntimeError(f"MCP server {self.name} {self.error}")

        self._id_counter += 1
        req_id = self._id_counter
        request = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut

        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"MCP {self.name} write failed: {e}") from e

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            self.status = "unresponsive"
            self.error = f"{method} timed out"
            raise RuntimeError(f"MCP {self.name} {method} timed out")


class MCPManager:
    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self._is_ready = False
        self._workspace_path: Optional[str] = None

    def health(self) -> Dict[str, Any]:
        """Return per-server health status for the /api/mcp/health endpoint."""
        result: Dict[str, Any] = {}
        for name, server in self.servers.items():
            result[name] = {
                "status": server.status,
                "tools": len(server.tools),
                "alive": server.is_alive(),
            }
            if server.error:
                result[name]["error"] = server.error
        return result

    def _builtin_specs(self, workspace_path: str) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = [
            {
                "name": "filesystem",
                "cmd": ["npx", "-y", "@modelcontextprotocol/server-filesystem", workspace_path],
            }
        ]
        if os.environ.get("EXA_API_KEY"):
            specs.append({"name": "exa", "cmd": ["npx", "-y", "@modelcontextprotocol/server-exa"]})
        if os.environ.get("FIGMA_ACCESS_TOKEN"):
            specs.append({"name": "figma", "cmd": ["npx", "-y", "@modelcontextprotocol/server-figma"]})
        if os.environ.get("TAVILY_API_KEY"):
            specs.append({"name": "tavily", "cmd": ["npx", "-y", "@tavily/mcp-server"]})
        if os.environ.get("SLACK_BOT_TOKEN"):
            specs.append({"name": "slack", "cmd": ["npx", "-y", "@modelcontextprotocol/server-slack"]})
        return specs

    def _definition_paths(self, workspace_path: str) -> List[Path]:
        candidates: List[Path] = []
        env_path = os.environ.get("ORYNN_MCP_CONFIG") or os.environ.get("AI_COMPUTER_MCP_CONFIG")
        if env_path:
            candidates.append(Path(env_path).expanduser())

        workspace = Path(workspace_path)
        candidates.extend([
            workspace / "mcp_servers.json",
            workspace / "mcp_servers.local.json",
            Path.cwd() / "mcp_servers.json",
            Path.cwd() / "mcp_servers.local.json",
        ])

        unique: List[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = str(candidate.resolve())
            except OSError:
                resolved = str(candidate)
            if resolved in seen:
                continue
            seen.add(resolved)
            if candidate.exists():
                unique.append(candidate)
        return unique

    def _expand_value(self, value: Any, workspace_path: str) -> Any:
        if isinstance(value, str):
            home = str(Path.home().resolve())
            return (
                value.replace("${workspace}", workspace_path)
                .replace("${home}", home)
            )
        if isinstance(value, list):
            return [self._expand_value(item, workspace_path) for item in value]
        if isinstance(value, dict):
            return {str(key): str(self._expand_value(val, workspace_path)) for key, val in value.items()}
        return value

    def _load_dynamic_specs(self, workspace_path: str) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for config_path in self._definition_paths(workspace_path):
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                _log.warning("Failed to parse MCP config %s: %s", config_path, exc)
                continue

            raw_specs = payload.get("servers", []) if isinstance(payload, dict) else payload
            if not isinstance(raw_specs, list):
                _log.warning("Ignoring MCP config %s because 'servers' is not a list.", config_path)
                continue

            for raw_spec in raw_specs:
                if not isinstance(raw_spec, dict):
                    continue
                if raw_spec.get("enabled", True) is False:
                    continue
                name = str(raw_spec.get("name", "")).strip()
                cmd = self._expand_value(raw_spec.get("cmd", []), workspace_path)
                env = self._expand_value(raw_spec.get("env", {}), workspace_path)
                if not name or not isinstance(cmd, list) or not all(isinstance(part, str) and part for part in cmd):
                    _log.warning("Skipping invalid MCP server definition in %s: %s", config_path, raw_spec)
                    continue
                specs.append({"name": name, "cmd": cmd, "env": env if isinstance(env, dict) else {}})
        return specs

    async def initialize_default_servers(self, workspace_path: str):
        workspace_path = str(Path(workspace_path).expanduser().resolve())
        desired_specs = self._builtin_specs(workspace_path) + self._load_dynamic_specs(workspace_path)
        desired: Dict[str, Dict[str, Any]] = {
            spec["name"]: {"cmd": spec["cmd"], "env": spec.get("env", {})}
            for spec in desired_specs
        }

        stale_names = [name for name in self.servers if name not in desired]
        for name in stale_names:
            server = self.servers.pop(name, None)
            if server:
                try:
                    await server.stop()
                except Exception as exc:
                    _log.warning("Failed to stop stale MCP server %s: %s", name, exc)

        start_specs: List[Dict[str, Any]] = []
        start_tasks = []
        for name, spec in desired.items():
            existing = self.servers.get(name)
            should_restart = bool(
                existing
                and (
                    existing.cmd != spec["cmd"]
                    or existing.env != spec["env"]
                    or not existing.is_alive()
                )
            )
            if should_restart:
                try:
                    await existing.stop()
                except Exception as exc:
                    _log.warning("Failed to restart MCP server %s cleanly: %s", name, exc)
                self.servers.pop(name, None)
                existing = None

            if existing and existing.is_alive():
                continue

            start_specs.append({"name": name, **spec})
            start_tasks.append(self.register_server(name, spec["cmd"], spec.get("env")))

        # Parallel startup — failures are isolated per-server
        results = await asyncio.gather(*start_tasks, return_exceptions=True)
        for spec, result in zip(start_specs, results):
            if isinstance(result, Exception):
                _log.warning("Failed to start MCP server %s: %s", spec["name"], result)

        self._workspace_path = workspace_path
        self._is_ready = True

    async def register_server(self, name: str, cmd: list[str], env: Optional[Dict[str, str]] = None):
        server = MCPServer(name, cmd, env)
        self.servers[name] = server  # Register before start so health endpoint sees it
        try:
            await server.start()
        except Exception:
            # Keep the dead server in the registry so health endpoint can report it
            pass
        return server

    async def call_tool(self, server_name: str, tool_name: str, args: Dict[str, Any]) -> str:
        if server_name not in self.servers:
            raise RuntimeError(f"MCP server '{server_name}' not registered")
        server = self.servers[server_name]
        if not server.is_alive():
            raise RuntimeError(f"MCP server '{server_name}' is not running (status: {server.status})")
        res = await server.call("tools/call", {"name": tool_name, "arguments": args})

        contents = res.get("content", [])
        if not contents:
            if res.get("isError"):
                return "Error (no message)"
            return "Success (no output)"

        texts = []
        for c in contents:
            if c.get("type") == "text":
                texts.append(c.get("text", ""))
        return "\n".join(texts)


mcp_manager = MCPManager()
