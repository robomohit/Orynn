from __future__ import annotations
import asyncio
import json
import subprocess
from typing import Any, Dict, Optional

class MCPBrowserBridge:
    """
    Bridge to interact with the Playwright MCP server.
    Implements a robust JSON-RPC event loop with response matching.
    """
    def __init__(self, mcp_server_cmd: list[str] = ["npx", "-y", "@playwright/mcp"]):
        self.cmd = mcp_server_cmd
        self.proc: Optional[subprocess.Popen] = None
        self._id_counter = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def start(self):
        """Launch the MCP server process."""
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self._listener_task = asyncio.create_task(self._listen())
        return True

    async def _listen(self):
        """Background listener for responses."""
        if not self.proc or not self.proc.stdout: return
        while self.proc.poll() is None:
            try:
                line = await asyncio.to_thread(self.proc.stdout.readline)
                if not line: break
                data = json.loads(line)
                if "id" in data:
                    req_id = data["id"]
                    fut = self._pending.pop(req_id, None)
                    if fut:
                        fut.set_result(data)
            except Exception:
                continue

    async def stop(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self.proc:
            self.proc.terminate()
            self.proc = None

    async def call_tool(self, name: str, args: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """Call a tool on the MCP server using JSON-RPC."""
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP server not running")

        self._id_counter += 1
        req_id = self._id_counter
        request = {
            "jsonrpc": "2.0",
            "method": f"tools/{name}",
            "params": args,
            "id": req_id
        }
        
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut

        # Send request
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

        try:
            response = await asyncio.wait_for(fut, timeout=timeout)
            if "error" in response:
                raise RuntimeError(f"MCP Tool Error: {response['error']}")
            return response.get("result", {})
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"MCP tool {name} timed out after {timeout}s")

    # Simplified wrappers to match ToolExecutor's expectations
    async def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1):
        return await self.call_tool("click", {"x": x, "y": y, "button": button, "clickCount": click_count})

    async def type_text(self, text: str):
        return await self.call_tool("type", {"text": text})

    async def press_key(self, key: str):
        return await self.call_tool("press", {"key": key})
