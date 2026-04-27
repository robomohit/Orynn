from __future__ import annotations
import re
from typing import Dict, List, Any, Optional
from .models import ActionType

TOOL_DESCRIPTIONS = {
    ActionType.system_info: "system_info: {} — returns OS, home dir, workspace, and system paths. Call this to understand the environment.",
    ActionType.list_directory: "list_directory: {\"path\": str, \"max_depth\": int} — list contents of a directory.",
    ActionType.read_file: "read_file: {\"path\": str} — read a file's contents.",
    ActionType.write_file: "write_file: {\"path\": str, \"content\": str} — create or overwrite a file.",
    ActionType.move_file: "move_file: {\"source\": str, \"destination\": str} — rename or move a file.",
    ActionType.file_glob: "file_glob: {\"pattern\": str} — find files matching a glob pattern.",
    ActionType.file_grep: "file_grep: {\"pattern\": str, \"directory\": str} — search file contents via regex.",
    ActionType.run_command: "run_command: {\"command\": str} — run a shell command.",
    ActionType.bash: "bash: {\"command\": str, \"restart\": bool} — run a bash command in a persistent session.",
    ActionType.git: "git: {\"command\": str, \"args\": str} — run a safe git command inside the workspace.",
    ActionType.run_tests: "run_tests: {\"command\": str, \"path\": str} — run project tests from a workspace path.",
    ActionType.lint_code: "lint_code: {\"path\": str} — lint or syntax-check a source file.",
    ActionType.find_symbol: "find_symbol: {\"symbol\": str, \"path\": str} — find function/class definitions.",
    ActionType.list_processes: "list_processes: {} — list running processes.",
    ActionType.kill_process: "kill_process: {\"pid\": int, \"force\": bool} — kill a process by PID.",
    ActionType.text_view: "text_view: {\"path\": str, \"view_range\": [int, int] | null} — view specific lines of a file.",
    ActionType.text_create: "text_create: {\"path\": str, \"file_text\": str} — create a new file (fails if exists).",
    ActionType.text_str_replace: "text_str_replace: {\"path\": str, \"old_str\": str, \"new_str\": str} — precise find and replace.",
    ActionType.text_insert: "text_insert: {\"path\": str, \"insert_line\": int, \"new_str\": str} — insert text at a specific line.",
    ActionType.text_undo_edit: "text_undo_edit: {\"path\": str} — undo the last edit to a file.",
    ActionType.text_editor: "text_editor: {\"command\": str, \"path\": str, ...} — wrapper for text editing operations.",
    ActionType.browser_open: "browser_open: {\"url\": str} — open a URL in the background browser.",
    ActionType.browser_accessibility_tree: "browser_accessibility_tree: {} — read page structure via accessibility tree.",
    ActionType.browser_click: "browser_click: {\"selector\": str} — click an element via CSS selector.",
    ActionType.browser_type: "browser_type: {\"selector\": str, \"text\": str} — type text into an input.",
    ActionType.browser_scroll: "browser_scroll: {\"direction\": \"up\"|\"down\", \"amount\": int} — scroll the page.",
    ActionType.browser_get_text: "browser_get_text: {} — get all visible text from the page.",
    ActionType.browser_navigate_back: "browser_navigate_back: {} — go back in browser history.",
    ActionType.browser_close: "browser_close: {} — close the browser.",
    ActionType.wait_action: "wait_action: {\"seconds\": float} — pause execution for a few seconds.",
    ActionType.mouse_click: "mouse_click: {\"x\": int, \"y\": int, \"button\": \"left\"|\"right\"|\"middle\"} — click at coordinates.",
    ActionType.keyboard_type: "keyboard_type: {\"text\": str} — type text globally.",
    ActionType.focus_window: "focus_window: {\"title\": str} — bring a visible window with matching title to the foreground.",
    ActionType.screenshot: "screenshot: {} — take a screenshot of the desktop.",
    ActionType.web_search: "web_search: {\"query\": str} — search the web for information.",
    ActionType.web_fetch: "web_fetch: {\"url\": str} — fetch a webpage's content directly.",
    ActionType.api_call: "api_call: {\"method\": str, \"url\": str, \"headers\": dict, \"body\": dict} — make an HTTP(S) API call. Only public http(s) URLs are allowed.",
    ActionType.request_permission: "request_permission: {\"scope\": str, \"reason\": str} — ask user for permission.",
    ActionType.computer: "computer: {\"action\": str, \"x\": int, \"y\": int, \"text\": str, \"keys\": str} — high-level computer action (screenshot, mouse_move, left_click, right_click, double_click, key, type, scroll, cursor_position).",
    ActionType.virtual_input: "virtual_input: {\"action\": str, \"text\": str, \"keys\": str} — alias for high-level isolated input.",
    ActionType.list_mcp_servers: "list_mcp_servers: {} — discover the MCP servers currently registered for this workspace.",
    ActionType.list_mcp_tools: "list_mcp_tools: {\"server_name\": str} — list the tools exposed by one MCP server.",
    ActionType.finish: "finish: {\"reason\": str} — complete the task.",
    ActionType.mcp_tool: "mcp_tool: {\"server_name\": str, \"tool_name\": str, \"tool_args\": dict} — call an MCP server tool dynamically.",
    ActionType.pixel_color_at: "pixel_color_at: {\"x\": int, \"y\": int} — read the RGB hex color at a desktop pixel. Useful to verify a button state or confirm a UI element painted before clicking.",
    ActionType.diff_files: "diff_files: {\"path_a\": str, \"path_b\": str} — return a unified diff between two files. Use after editing to verify a patch applied correctly without re-reading both files.",
    ActionType.extract_links: "extract_links: {\"url\": str} — fetch a URL and return a structured list of (text, href) pairs. More reliable than scraping links from web_fetch text.",
}

TOOL_PACKS = {
    "core": [ActionType.system_info, ActionType.finish, ActionType.request_permission],
    "filesystem": [ActionType.read_file, ActionType.write_file, ActionType.list_directory, ActionType.move_file, ActionType.file_glob, ActionType.file_grep],
    "terminal": [ActionType.run_command, ActionType.bash, ActionType.git, ActionType.run_tests, ActionType.lint_code, ActionType.find_symbol, ActionType.list_processes, ActionType.kill_process],
    "editing": [ActionType.text_view, ActionType.text_create, ActionType.text_str_replace, ActionType.text_insert, ActionType.text_undo_edit, ActionType.text_editor],
    "browser": [ActionType.browser_open, ActionType.browser_accessibility_tree, ActionType.browser_click, ActionType.browser_type, ActionType.browser_scroll, ActionType.browser_get_text, ActionType.browser_navigate_back, ActionType.browser_close, ActionType.wait_action],
    "computer": [ActionType.mouse_click, ActionType.keyboard_type, ActionType.focus_window, ActionType.screenshot, ActionType.ocr_image, ActionType.scroll, ActionType.double_click, ActionType.right_click, ActionType.middle_click, ActionType.mouse_move, ActionType.left_click_drag, ActionType.key_combo, ActionType.hold_key, ActionType.cursor_position, ActionType.type_with_delay, ActionType.find_on_screen, ActionType.computer, ActionType.pixel_color_at],
    "web": [ActionType.web_fetch, ActionType.web_search, ActionType.extract_links],
    "utilities": [ActionType.api_call, ActionType.get_clipboard, ActionType.set_clipboard, ActionType.notify, ActionType.list_mcp_servers, ActionType.list_mcp_tools, ActionType.mcp_tool],
    "editing_extras": [ActionType.diff_files],
}

def get_tool_guidance(packs: List[str]) -> str:
    guidance = []
    seen_actions = set()
    for pack in packs:
        if pack in TOOL_PACKS:
            for action_type in TOOL_PACKS[pack]:
                if action_type not in seen_actions and action_type in TOOL_DESCRIPTIONS:
                    guidance.append(f"- {TOOL_DESCRIPTIONS[action_type]}")
                    seen_actions.add(action_type)
    return "\n".join(guidance)


def _json_schema_from_description(action_type: ActionType, description: str) -> Dict[str, Any]:
    match = re.search(r"\{.*?\}", description)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    if match:
        try:
            # The descriptions use examples like {"path": str}. Convert these
            # into the small JSON schema native tool APIs expect.
            example = match.group(0)
            for key, kind in re.findall(r'"([^"]+)"\s*:\s*([^,}]+)', example):
                kind = kind.strip().lower()
                schema_type = "string"
                if "int" in kind or "float" in kind or "number" in kind:
                    schema_type = "number"
                elif "bool" in kind:
                    schema_type = "boolean"
                elif "dict" in kind or "object" in kind:
                    schema_type = "object"
                elif "list" in kind or "[" in kind:
                    schema_type = "array"
                properties[key] = {"type": schema_type}
                if "null" not in kind:
                    required.append(key)
        except Exception:
            properties = {}
            required = []
    return {
        "type": "function",
        "function": {
            "name": action_type.value,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": True,
            },
        },
    }


def get_tool_schemas(packs: List[str]) -> List[Dict[str, Any]]:
    schemas: List[Dict[str, Any]] = []
    seen_actions = set()
    for pack in packs:
        for action_type in TOOL_PACKS.get(pack, []):
            if action_type in seen_actions or action_type not in TOOL_DESCRIPTIONS:
                continue
            schemas.append(_json_schema_from_description(action_type, TOOL_DESCRIPTIONS[action_type]))
            seen_actions.add(action_type)
    return schemas

def get_mode_packs(mode: str) -> List[str]:
    if mode in ("coding", "chat", "auto"):
        return ["core", "filesystem", "terminal", "editing", "editing_extras", "web", "utilities"]
    if mode == "computer_use":
        return ["core", "browser", "web"]
    if mode in ("computer", "computer_isolated"):
        return ["core", "filesystem", "terminal", "computer", "web", "utilities"]
    return ["core"]
