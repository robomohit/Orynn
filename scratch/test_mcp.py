import asyncio
from app.mcp_manager import mcp_manager
import os

async def main():
    print("Initializing MCP Servers...")
    await mcp_manager.initialize_default_servers(os.getcwd())
    
    print("\nRegistered servers:", list(mcp_manager.servers.keys()))
    
    if "filesystem" in mcp_manager.servers:
        print("\nTools for filesystem server:")
        for tool in mcp_manager.servers["filesystem"].tools:
            print(f" - {tool['name']}: {tool.get('description', '')}")
            
        print("\nTesting 'list_allowed_directories' tool:")
        try:
            res = await mcp_manager.call_tool("filesystem", "list_allowed_directories", {})
            print("Response:", res)
        except Exception as e:
            print("Error:", e)
            
    print("\nShutting down...")
    for srv in mcp_manager.servers.values():
        await srv.stop()

if __name__ == "__main__":
    asyncio.run(main())
