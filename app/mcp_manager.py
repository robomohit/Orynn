from __future__ import annotations
import asyncio
import json
import subprocess
import os
import sys
from typing import Any, Dict, List, Optional
import logging

_log = logging.getLogger(__name__)

class MCPServer:
    def __init__(self, name: str, cmd: list[str], env: Optional[Dict[str, str]] = None):
        self.name = name
        self.cmd = cmd
        self.env = env or {}
        self.proc: Optional[subprocess.Popen] = None
        self._id_counter = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self.tools: List[Dict[str, Any]] = []

    async def start(self):
        env = os.environ.copy()
        env.update(self.env)
        
        # On Windows, npx needs shell=True or the `.cmd` extension
        cmd = self.cmd
        if os.name == 'nt' and cmd[0] == 'npx':
            cmd[0] = 'npx.cmd'
            
        _log.info(f"Starting MCP Server {self.name}: {' '.join(cmd)}")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        self._listener_task = asyncio.create_task(self._listen())
        
        # Initialize
        await self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai_computer", "version": "1.0.0"}
        })
        await self.notify("notifications/initialized", {})
        
        # Get tools
        res = await self.call("tools/list", {})
        self.tools = res.get("tools", [])
        _log.info(f"MCP Server {self.name} started with {len(self.tools)} tools.")
        return True

    async def _listen(self):
        if not self.proc or not self.proc.stdout: return
        while self.proc.poll() is None:
            try:
                line = await asyncio.to_thread(self.proc.stdout.readline)
                if not line: break
                data = json.loads(line)
                if "id" in data:
                    req_id = data["id"]
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        if "error" in data:
                            fut.set_exception(RuntimeError(data["error"]))
                        else:
                            fut.set_result(data.get("result", {}))
            except Exception as e:
                continue

    async def stop(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self.proc:
            self.proc.terminate()
            self.proc = None

    async def notify(self, method: str, params: Dict[str, Any]):
        if not self.proc or not self.proc.stdin:
            return
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

    async def call(self, method: str, params: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError(f"MCP server {self.name} not running")

        self._id_counter += 1
        req_id = self._id_counter
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id
        }
        
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut

        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"MCP {self.name} {method} timed out")

class MCPManager:
    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self._is_ready = False

    async def initialize_default_servers(self, workspace_path: str):
        if self._is_ready: return
        
        tasks = []
        
        # 1. Filesystem (Local)
        tasks.append(self.register_server(
            "filesystem", 
            ["npx", "-y", "@modelcontextprotocol/server-filesystem", workspace_path]
        ))
        
        # 2. Windows-MCP (Local OS control)
        # Assuming the npx package is @smithery/cli run windows-mcp or similar, but let's use a safe fallback.
        # Actually, let's use the standard ones available.
        # Let's skip Windows-MCP if it's not a standard npx package, or we can assume it's `npx -y windows-mcp`
        
        # 2. Exa Search
        if os.environ.get("EXA_API_KEY"):
            tasks.append(self.register_server("exa", ["npx", "-y", "@modelcontextprotocol/server-exa"]))

        # 3. Figma
        if os.environ.get("FIGMA_ACCESS_TOKEN"):
            tasks.append(self.register_server("figma", ["npx", "-y", "@modelcontextprotocol/server-figma"]))

        # 4. Tavily
        if os.environ.get("TAVILY_API_KEY"):
            tasks.append(self.register_server("tavily", ["npx", "-y", "@tavily/mcp-server"]))
            
        # 5. Slack
        if os.environ.get("SLACK_BOT_TOKEN"):
            tasks.append(self.register_server("slack", ["npx", "-y", "@modelcontextprotocol/server-slack"]))

        for task in tasks:
            try:
                await task
            except Exception as e:
                _log.warning(f"Failed to start MCP server: {e}")
                
        self._is_ready = True

    async def register_server(self, name: str, cmd: list[str], env: Optional[Dict[str, str]] = None):
        server = MCPServer(name, cmd, env)
        await server.start()
        self.servers[name] = server
        return server

    async def call_tool(self, server_name: str, tool_name: str, args: Dict[str, Any]) -> str:
        if server_name not in self.servers:
            raise RuntimeError(f"MCP server {server_name} not registered")
        server = self.servers[server_name]
        res = await server.call("tools/call", {
            "name": tool_name,
            "arguments": args
        })
        
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
