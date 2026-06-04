from __future__ import annotations

from enum import Enum
from typing import Dict, Set


class PermissionScope(str, Enum):
    browser = "browser"
    clipboard = "clipboard"
    google_sheets = "google_sheets"
    filesystem = "filesystem"
    shell = "shell"
    mcp = "mcp"
    screen = "screen"
    system = "system"


_BROWSER_ACTIONS = {
    "browser_open",
    "browser_screenshot",
    "browser_click",
    "browser_click_coords",
    "browser_type",
    "browser_scroll",
    "browser_get_text",
    "browser_accessibility_tree",
    "browser_navigate_back",
    "browser_close",
}

_FS_ACTIONS = {
    "analyze_folder",
    "diff_files",
    "file_glob",
    "file_grep",
    "list_directory",
    "read_file",
    "text_view",
    "write_file",
    "move_file",
    "text_editor",
    "text_create",
    "text_str_replace",
    "text_insert",
    "text_undo_edit",
}

_SHELL_ACTIONS = {"run_command", "bash", "run_tests", "run_and_watch", "git", "lint_code"}

_MCP_ACTIONS = {"list_mcp_servers", "list_mcp_tools", "mcp_tool"}

_CLIPBOARD_ACTIONS = {"get_clipboard", "set_clipboard"}

_SCREEN_ACTIONS = {
    "find_on_screen",
    "ocr_image",
    "pixel_color_at",
    "screen_context",
    "screenshot",
    "ui_critique",
}

# system_info is read-only, static OS facts (platform, Python version, CPU
# count) — harmless, so it's a FREE action (no approval) to avoid pointless
# "Allow system access?" friction on simple tasks. list_processes stays gated
# because a live process snapshot reveals what the user is running.
_SYSTEM_ACTIONS = {"list_processes"}

_COMPUTER_SCREEN_SUBACTIONS = {"screenshot"}

_EXPLICIT_GRANT_SCOPES = {
    PermissionScope.clipboard,
    PermissionScope.mcp,
    PermissionScope.screen,
    PermissionScope.system,
}


def can_auto_grant_scope(scope: PermissionScope | str) -> bool:
    try:
        normalized = PermissionScope(scope)
    except ValueError:
        return False
    return normalized not in _EXPLICIT_GRANT_SCOPES


def scope_for_action(action_type: str, args: dict | None = None) -> PermissionScope | None:
    """Map an action type to the permission scope it needs (or None if free).

    google_sheets is a refinement of browser: if a browser action targets a
    sheets URL, prefer google_sheets so the user sees the right context.
    """
    args = args or {}
    if action_type in _BROWSER_ACTIONS:
        url = (args.get("url") or "").lower()
        if "docs.google.com/spreadsheets" in url or "sheets.google.com" in url:
            return PermissionScope.google_sheets
        return PermissionScope.browser
    if action_type in _FS_ACTIONS:
        return PermissionScope.filesystem
    if action_type in _SHELL_ACTIONS:
        return PermissionScope.shell
    if action_type in _MCP_ACTIONS:
        return PermissionScope.mcp
    if action_type in _CLIPBOARD_ACTIONS:
        return PermissionScope.clipboard
    if action_type in _SCREEN_ACTIONS:
        return PermissionScope.screen
    if action_type == "computer":
        subaction = str(args.get("action") or "").strip().lower()
        if subaction in _COMPUTER_SCREEN_SUBACTIONS:
            return PermissionScope.screen
    if action_type in _SYSTEM_ACTIONS:
        return PermissionScope.system
    return None


class PermissionStore:
    """Per-task grant map. Scopes persist for the lifetime of a task.

    The agent emits `permission_required` when an action needs a scope that
    hasn't been granted; the UI replies via `/api/permissions`. Grants are
    remembered per task so repeated actions in the same scope don't re-prompt.
    """

    def __init__(self):
        self._granted: Dict[str, Set[str]] = {}
        self._denied: Dict[str, Set[str]] = {}

    def grant(self, task_id: str, scope: str) -> None:
        self._granted.setdefault(task_id, set()).add(scope)
        if task_id in self._denied:
            self._denied[task_id].discard(scope)

    def deny(self, task_id: str, scope: str) -> None:
        self._denied.setdefault(task_id, set()).add(scope)
        if task_id in self._granted:
            self._granted[task_id].discard(scope)

    def is_granted(self, task_id: str, scope: str) -> bool:
        return scope in self._granted.get(task_id, set())

    def is_denied(self, task_id: str, scope: str) -> bool:
        return scope in self._denied.get(task_id, set())

    def granted_scopes(self, task_id: str) -> list[str]:
        return sorted(self._granted.get(task_id, set()))

    def denied_scopes(self, task_id: str) -> list[str]:
        return sorted(self._denied.get(task_id, set()))

    def task_ids(self) -> list[str]:
        return sorted(set(self._granted) | set(self._denied))

    def clear(self, task_id: str) -> None:
        self._granted.pop(task_id, None)
        self._denied.pop(task_id, None)
