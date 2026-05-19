from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class DangerLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionType(str, Enum):
    finish = "finish"
    run_command = "run_command"
    bash = "bash"
    wait_for_window = "wait_for_window"
    read_file = "read_file"
    write_file = "write_file"
    move_file = "move_file"
    mouse_click = "mouse_click"
    keyboard_type = "keyboard_type"
    screenshot = "screenshot"
    ocr_image = "ocr_image"
    api_call = "api_call"
    scroll = "scroll"
    double_click = "double_click"
    right_click = "right_click"
    middle_click = "middle_click"
    mouse_move = "mouse_move"
    left_click_drag = "left_click_drag"
    key_combo = "key_combo"
    hold_key = "hold_key"
    wait_action = "wait_action"
    cursor_position = "cursor_position"
    focus_window = "focus_window"
    text_view = "text_view"
    virtual_input = "virtual_input"
    text_create = "text_create"
    text_str_replace = "text_str_replace"
    text_insert = "text_insert"
    text_undo_edit = "text_undo_edit"
    text_editor = "text_editor"
    computer = "computer"
    browser_open = "browser_open"
    browser_screenshot = "browser_screenshot"
    browser_click = "browser_click"
    browser_click_coords = "browser_click_coords"
    browser_type = "browser_type"
    browser_scroll = "browser_scroll"
    browser_get_text = "browser_get_text"
    browser_accessibility_tree = "browser_accessibility_tree"
    browser_navigate_back = "browser_navigate_back"
    browser_close = "browser_close"
    type_with_delay = "type_with_delay"
    find_on_screen = "find_on_screen"
    get_clipboard = "get_clipboard"
    set_clipboard = "set_clipboard"
    notify = "notify"
    system_info = "system_info"
    list_directory = "list_directory"
    request_permission = "request_permission"
    file_glob = "file_glob"
    file_grep = "file_grep"
    web_fetch = "web_fetch"
    web_search = "web_search"
    list_processes = "list_processes"
    kill_process = "kill_process"
    force_close_window = "force_close_window"
    list_mcp_servers = "list_mcp_servers"
    list_mcp_tools = "list_mcp_tools"
    mcp_tool = "mcp_tool"
    git = "git"
    run_tests = "run_tests"
    lint_code = "lint_code"
    find_symbol = "find_symbol"
    delegate_coding = "delegate_coding"
    pixel_color_at = "pixel_color_at"
    diff_files = "diff_files"
    extract_links = "extract_links"
    todo_write = "todo_write"
    memory_recall = "memory_recall"
    run_and_watch = "run_and_watch"
    ui_critique = "ui_critique"


class Action(BaseModel):
    id: str
    type: ActionType
    args: Dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    requires_approval: bool = False


class ToolResult(BaseModel):
    ok: bool
    output: str
    base64_image: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ToolError(Exception):
    pass


class MemoryItem(BaseModel):
    id: int
    kind: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentContext(BaseModel):
    goal: str
    history: List[str] = Field(default_factory=list)
    screen_width: int = 1280
    screen_height: int = 800
    isolated_app: Optional[str] = None
    active_skills: List[str] = Field(default_factory=list)
    project_folder: Optional[str] = None
    environment: Dict[str, Any] = Field(default_factory=dict)


class TaskRecord(BaseModel):
    id: str
    status: str = "pending"
    context: AgentContext
    paused: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    goal: Optional[str] = None
    reason: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    execution_mode: str = "serial"
    max_parallel_workers: int = 1


class SubTask(BaseModel):
    id: str
    description: str
    actions: List[Action] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    write_scope: List[str] = Field(default_factory=list)
    worker_hint: Optional[str] = None
    worker_id: Optional[str] = None
    status: TaskStatus = TaskStatus.pending
    error: Optional[str] = None
    post_screenshot_b64: Optional[str] = None


class HierarchicalPlan(BaseModel):
    reasoning: str
    sub_tasks: List[SubTask]
    overall_complete: bool = False
    execution_mode: str = "serial"
    max_parallel_workers: int = 1


class ActionDecision(BaseModel):
    danger: DangerLevel
    reason: str
    requires_approval: bool


class ApprovalBundle(BaseModel):
    action_id: str
    action_type: str
    action_args: Dict[str, Any]
    danger: DangerLevel
    reason: str
    explanation: str
    context_screenshot_b64: Optional[str] = None
    timeout_seconds: int = 300
    task_id: str
    created_at: str


class PluginAction(BaseModel):
    name: str
    description: str
    handlers: Dict[str, Any]
