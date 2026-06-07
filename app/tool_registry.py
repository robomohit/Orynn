from __future__ import annotations
import re
from typing import Dict, List, Any, Optional, Iterable
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
    ActionType.wait_for_window: "wait_for_window: {\"title\": str, \"timeout\": float} — wait until a visible desktop window with matching title appears and is ready to receive input.",
    ActionType.git: "git: {\"command\": str, \"args\": str} — run a safe git command inside the workspace.",
    ActionType.run_tests: "run_tests: {\"command\": str, \"path\": str} — run project tests from a workspace path.",
    ActionType.lint_code: "lint_code: {\"path\": str} — lint or syntax-check a source file.",
    ActionType.find_symbol: "find_symbol: {\"symbol\": str, \"path\": str} — find function/class definitions.",
    ActionType.delegate_coding: "delegate_coding: {\"task\": str, \"repo_path\": str, \"files\": list, \"constraints\": str, \"backend\": str} — delegate a coding-heavy subtask to a connected coding backend and return its structured result.",
    ActionType.list_processes: "list_processes: {} — list running processes.",
    ActionType.kill_process: "kill_process: {\"pid\": int, \"force\": bool} — kill a process by PID.",
    ActionType.force_close_window: "force_close_window: {\"title\": str, \"pid\": int, \"force\": bool} — terminate a desktop app by window title or PID. Use when a launched app is frozen or unresponsive.",
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
    ActionType.todo_write: "todo_write: {\"items\": list} — maintain a task plan across loop iterations. Each item: {content: str, activeForm: str, status: \"pending\"|\"in_progress\"|\"completed\"}. USE ONLY when the task has 3+ genuinely distinct steps that benefit from tracking. SKIP for single-step tasks (\"read X\", \"answer this\", \"run this command\"), trivial 2-step tasks, and pure conversation. Calling this on a simple task wastes a turn. When you do use it, exactly one item is in_progress at a time and update only when status genuinely changes.",
    ActionType.memory_recall: "memory_recall: {\"query\": str} — search long-term memory for relevant past session summaries. Returns up to 5 semantically similar prior sessions. Use to recall what was done in previous related tasks.",
    ActionType.run_and_watch: "run_and_watch: {\"command\": str, \"watch_seconds\": float} — start a process, capture its stdout+stderr for watch_seconds (default 10), then kill it cleanly. Use to launch an app/server and observe its early output for crashes or errors. Returns labeled stdout/stderr plus exit_code and whether it was still running when killed.",
    ActionType.ui_critique: "ui_critique: {\"focus\": str} — take a desktop screenshot and ask the model to enumerate visible UI issues (clutter, alignment, redundancy, accessibility) with hypothesized fixes. Optional 'focus' narrows attention (e.g. 'sidebar', 'cards'). Use during UI cleanup workflows. Best in computer/auto modes where the screenshot flows back to the next turn.",
    ActionType.analyze_folder: "analyze_folder: {\"path\": str, \"action\": str} — scan a local folder to find files and show them in a Generative UI widget on the user's desktop capsule. 'path' is the folder to scan (e.g. '~/Downloads', '~/Desktop', or any absolute path). 'action' is what to do: 'scan' (list files sorted by size), 'organize' (move files into category subfolders like Documents/, Images/, Archives/), or 'delete_large' (find files over 100MB). ALWAYS use this tool when the user asks to clean, organize, tidy, sweep, or analyze files in a folder. The results appear as an interactive widget the user can act on.",
    ActionType.show_widget: "show_widget: {\"title\": str, \"subtitle\": str, \"icon\": str, \"items\": list, \"text\": str, \"buttons\": list, \"progress\": float} — display a dynamic, interactive Generative UI widget in the user's desktop capsule. Use this to present ANY data visually: search results, file lists, system info, status updates, confirmations, or custom dashboards. All fields are optional except 'title'. 'items' is a list of {name, detail, icon} for list views. 'buttons' is a list of {label, style, action, payload, icon} where style is 'primary'/'secondary'/'danger' and action is an API endpoint or 'open_folder'/'open_url'/'dismiss'. 'progress' is 0.0-1.0 for a progress bar. Use this INSTEAD of plain text whenever data would benefit from visual structure.",
    ActionType.screen_context: "screen_context: {} — silently capture the user's current screen and return its contents as a base64 image and OCR text. Use this when the user says 'this', 'what am I looking at', 'summarize this page', or any context-dependent query where you need to see their screen. Returns both the screenshot and extracted text so you can understand the visual context.",
    ActionType.uia_find: "uia_find: {\"query\": str, \"app\": str, \"limit\": int} — find UI controls in a desktop app by their NAME or AutomationId via Windows UI Automation (NO screenshot needed). 'query' is the control's visible label (e.g. 'File', 'Search', 'Send'). 'app' optionally narrows to a window title. Returns ranked matches with control_type and coordinates. PREFER THIS over screenshot+guessing coordinates.",
    ActionType.uia_click: "uia_click: {\"query\": str, \"app\": str} — activate a control by NAME/AutomationId using the UIA InvokePattern (a real button press, no pixel click), falling back to a center click. Use after uia_find confirms the control exists. No screenshot needed.",
    ActionType.uia_click_sequence: "uia_click_sequence: {\"targets\": [str, ...], \"app\": str} — click a whole ORDERED list of controls by NAME in ONE call (each via InvokePattern + OCR fallback). Use this for any known multi-click sequence — entering a number into Calculator, pressing operator+digits, tabbing through a form — so the entire sequence runs in a single step with NO chance to lose track between clicks. E.g. Calculator 256-89: {\"targets\":[\"Two\",\"Five\",\"Six\",\"Minus\",\"Eight\",\"Nine\",\"Equals\"],\"app\":\"Calculator\",\"read_result\":\"Display\"}. Optional read_result names a result control to read back IN THE SAME call (e.g. \"Display\" for Calculator) so you can verify + finish without a separate uia_find — one fewer turn. Stops and reports if a control isn't found. Far more reliable than many separate uia_click calls.",
    ActionType.uia_type: "uia_type: {\"query\": str, \"text\": str, \"app\": str, \"clear_first\": bool, \"submit\": bool} — enter text into an editable control (text box, search field, chat message box) found by NAME/AutomationId. Focuses the control then pastes — instant for any length and works on React/Electron inputs (Discord, Slack, Notion) where plain value-setting silently fails. No screenshot or coordinates needed. clear_first=true replaces existing content. submit=true presses Enter afterwards (send a message / run a search) in one reliable step.",
    ActionType.uia_wait: "uia_wait: {\"query\": str, \"app\": str, \"timeout\": float} — wait until a control with this NAME/AutomationId appears, returning the instant it does (default 6s). Use this after clicking/navigating (e.g. switching a Discord channel) instead of guessing a sleep — faster and more reliable while the app re-renders.",
    ActionType.electron_check: "electron_check: {\"exe\": str} — check whether an .exe path is an Electron/Chromium app (VS Code, Slack, Discord, Spotify, Notion, Cursor, etc.). These hide their UIA tree by default.",
    ActionType.electron_unlock: "electron_unlock: {\"exe\": str, \"args\": list} — relaunch an Electron app with --force-renderer-accessibility so its DOM becomes a real UIA tree that uia_find/uia_click/uia_type can drive. Use when uia_find returns nothing on an Electron app (electron_check says true). Does NOT kill the running copy — the user may need to close it first.",
}

TOOL_PACKS = {
    "core": [ActionType.system_info, ActionType.finish, ActionType.request_permission, ActionType.todo_write, ActionType.memory_recall, ActionType.analyze_folder, ActionType.show_widget, ActionType.screen_context],
    "filesystem": [ActionType.read_file, ActionType.write_file, ActionType.list_directory, ActionType.move_file, ActionType.file_glob, ActionType.file_grep],
    "terminal": [ActionType.run_command, ActionType.bash, ActionType.wait_for_window, ActionType.git, ActionType.run_tests, ActionType.lint_code, ActionType.find_symbol, ActionType.delegate_coding, ActionType.list_processes, ActionType.kill_process, ActionType.run_and_watch],
    "editing": [ActionType.text_view, ActionType.text_create, ActionType.text_str_replace, ActionType.text_insert, ActionType.text_undo_edit, ActionType.text_editor],
    "browser": [ActionType.browser_open, ActionType.browser_accessibility_tree, ActionType.browser_click, ActionType.browser_type, ActionType.browser_scroll, ActionType.browser_get_text, ActionType.browser_navigate_back, ActionType.browser_close, ActionType.wait_action],
    "computer": [ActionType.mouse_click, ActionType.keyboard_type, ActionType.focus_window, ActionType.wait_for_window, ActionType.force_close_window, ActionType.screenshot, ActionType.ocr_image, ActionType.scroll, ActionType.double_click, ActionType.right_click, ActionType.middle_click, ActionType.mouse_move, ActionType.left_click_drag, ActionType.key_combo, ActionType.hold_key, ActionType.cursor_position, ActionType.type_with_delay, ActionType.find_on_screen, ActionType.computer, ActionType.pixel_color_at, ActionType.ui_critique],
    "uia": [ActionType.uia_find, ActionType.uia_click, ActionType.uia_click_sequence, ActionType.uia_type, ActionType.uia_wait, ActionType.electron_check, ActionType.electron_unlock, ActionType.focus_window, ActionType.wait_for_window],
    "web": [ActionType.web_fetch, ActionType.web_search, ActionType.extract_links],
    "utilities": [ActionType.api_call, ActionType.get_clipboard, ActionType.set_clipboard, ActionType.notify, ActionType.list_mcp_servers, ActionType.list_mcp_tools, ActionType.mcp_tool],
    "editing_extras": [ActionType.diff_files],
}

def _excluded_actions(exclude_actions: Optional[Iterable[ActionType]] = None) -> set[ActionType]:
    return set(exclude_actions or [])


def get_tool_guidance(packs: List[str], exclude_actions: Optional[Iterable[ActionType]] = None) -> str:
    guidance = []
    seen_actions = set()
    excluded = _excluded_actions(exclude_actions)
    for pack in packs:
        if pack in TOOL_PACKS:
            for action_type in TOOL_PACKS[pack]:
                if action_type in excluded:
                    continue
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
                # JSON Schema requires an `array` type to declare `items`.
                # Strict providers (Google Gemini) reject the whole request
                # otherwise. Element type is unknown from the description,
                # so default to string.
                if schema_type == "array":
                    properties[key]["items"] = {"type": "string"}
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


def get_tool_schemas(packs: List[str], exclude_actions: Optional[Iterable[ActionType]] = None) -> List[Dict[str, Any]]:
    schemas: List[Dict[str, Any]] = []
    seen_actions = set()
    excluded = _excluded_actions(exclude_actions)
    for pack in packs:
        for action_type in TOOL_PACKS.get(pack, []):
            if action_type in excluded:
                continue
            if action_type in seen_actions or action_type not in TOOL_DESCRIPTIONS:
                continue
            schemas.append(_json_schema_from_description(action_type, TOOL_DESCRIPTIONS[action_type]))
            seen_actions.add(action_type)
    return schemas

# Unified tool surface — every capability in one set. The MODEL decides which
# surface a task needs (desktop UIA, screen, browser, web research, files,
# shell, editing) instead of being boxed into a single mode's tools. Platform/
# vision pruning is applied separately via exclude_actions (e.g. a text-only
# model drops the pixel/screenshot tools and drives the desktop blind by UIA).
UNIFIED_PACKS = [
    "core", "filesystem", "editing", "editing_extras", "terminal",
    "uia", "computer", "browser", "web", "utilities",
]


def get_unified_packs() -> List[str]:
    return list(UNIFIED_PACKS)


def get_mode_packs(mode: str) -> List[str]:
    if mode in ("coding", "chat", "auto"):
        return ["core", "filesystem", "terminal", "editing", "editing_extras", "web", "utilities"]
    if mode == "computer_use":
        return ["core", "browser", "web"]
    if mode in ("computer", "computer_isolated"):
        # Desktop app control: UIA + the few launch/clipboard helpers. Drop
        # filesystem/web (a desktop task doesn't read files or web-search) — fewer
        # tool schemas = smaller prompt = faster per turn AND fewer distractions
        # for the model (more accurate). Coding tools inside `terminal` are pruned
        # via _tool_excludes_for_control_route.
        return ["core", "uia", "terminal", "computer"]
    return ["core"]
