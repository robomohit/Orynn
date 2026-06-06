from __future__ import annotations

from .models import Action, ActionDecision, DangerLevel


class SafetyManager:
    def evaluate(self, action: Action, safe_mode: bool = True) -> ActionDecision:
        t = action.type.value

        command_risk = {"run_command", "bash", "run_tests", "run_and_watch"}
        high_risk = {
            *command_risk,
            "git",
            "lint_code",
            "write_file",
            "move_file",
            "text_editor",
            "text_create",
            "text_str_replace",
            "text_insert",
        }

        # Hard-blocked dangerous commands — always require approval regardless of mode
        if t in command_risk:
            import re
            raw_cmd = action.args.get("command", "")
            cmd = re.sub(r"\s+", " ", raw_cmd).lower().strip()
            dangerous_patterns = ["rm -rf /", "format c:", "del /f /s", ":(){ :|:& };:",
                                  "rd /s /q c:", "rmdir /s /q c:", "shutdown", "reboot"]
            if any(p in cmd for p in dangerous_patterns):
                return ActionDecision(
                    danger=DangerLevel.high,
                    reason=f"Hard-blocked dangerous shell command: {cmd}",
                    requires_approval=True
                )

        # In coding mode (safe_mode=False), auto-approve file ops and safe commands
        if not safe_mode and t in high_risk:
            return ActionDecision(
                danger=DangerLevel.medium,
                reason="coding mode — auto-approved",
                requires_approval=False,
            )
            
        if t in high_risk:
            return ActionDecision(
                danger=DangerLevel.high,
                reason="filesystem/shell mutation",
                requires_approval=True,
            )
        if t == "analyze_folder":
            folder_action = str(action.args.get("action", "scan")).strip().lower()
            if folder_action not in {"", "scan"}:
                return ActionDecision(
                    danger=DangerLevel.high,
                    reason=f"folder action may mutate local files: {folder_action}",
                    requires_approval=True,
                )
            return ActionDecision(
                danger=DangerLevel.low,
                reason="read-only folder scan",
                requires_approval=False,
            )

        low = {
            "scroll",
            "mouse_move",
            "cursor_position",
            "wait_action",
            "browser_open",
            "browser_screenshot",
            "browser_get_text",
            "browser_accessibility_tree",
            "browser_navigate_back",
            "browser_close",
        }
        medium = {
            "double_click",
            "right_click",
            "middle_click",
            "browser_click",
            "browser_click_coords",
            "browser_type",
            "browser_scroll",
        }
        if t in low:
            return ActionDecision(danger=DangerLevel.low, reason="read-only or safe UI action", requires_approval=False)
        if t == "left_click_drag":
            return ActionDecision(danger=DangerLevel.medium, reason="drag can move or delete UI elements", requires_approval=safe_mode)
        if t in medium:
            return ActionDecision(danger=DangerLevel.medium, reason="UI interaction that may have side effects", requires_approval=safe_mode)
        if t == "key_combo":
            keys = action.args.get("keys", "").lower().replace(" ", "")
            dangerous = {"ctrl+alt+del", "win+l", "ctrl+alt+t", "alt+f4"}
            if keys in dangerous:
                return ActionDecision(danger=DangerLevel.high, reason=f"dangerous key combo: {keys}", requires_approval=True)
            return ActionDecision(danger=DangerLevel.medium, reason="keyboard shortcut", requires_approval=False)
        if t == "force_close_window":
            return ActionDecision(
                danger=DangerLevel.high,
                reason="terminates a desktop application",
                requires_approval=True,
            )
        if t == "kill_process":
            return ActionDecision(
                danger=DangerLevel.high,
                reason="terminates a process",
                requires_approval=True,
            )
        if t == "electron_unlock":
            return ActionDecision(
                danger=DangerLevel.high,
                reason="relaunches a desktop application with accessibility flags",
                requires_approval=True,
            )
        if t == "mcp_tool":
            server = str(action.args.get("server_name", "")).strip()
            tool = str(action.args.get("tool_name", "")).strip()
            label = f"{server}.{tool}" if server and tool else "external MCP tool"
            return ActionDecision(
                danger=DangerLevel.high,
                reason=f"executes dynamic MCP tool: {label}",
                requires_approval=True,
            )
        if t in {"list_mcp_servers", "list_mcp_tools"}:
            server = str(action.args.get("server_name", "")).strip()
            suffix = f" for {server}" if server else ""
            return ActionDecision(
                danger=DangerLevel.high,
                reason=f"may start configured MCP server processes{suffix}",
                requires_approval=True,
            )
        if t == "api_call":
            method = action.args.get("method", "GET").upper()
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                return ActionDecision(danger=DangerLevel.high, reason=f"external API mutation ({method})", requires_approval=True)
            return ActionDecision(danger=DangerLevel.low, reason="read-only API call", requires_approval=False)
        if t == "ocr_image":
            return ActionDecision(danger=DangerLevel.low, reason="read-only screen analysis", requires_approval=False)
        if t == "find_on_screen":
            return ActionDecision(danger=DangerLevel.low, reason="read-only visual search", requires_approval=False)
        if t in ("get_clipboard",):
            return ActionDecision(danger=DangerLevel.low, reason="read clipboard", requires_approval=False)
        if t in ("set_clipboard",):
            return ActionDecision(danger=DangerLevel.medium, reason="writes to clipboard", requires_approval=False)
        if t == "notify":
            return ActionDecision(danger=DangerLevel.low, reason="system notification", requires_approval=False)
        if t == "finish":
            return ActionDecision(danger=DangerLevel.low, reason="task completion signal", requires_approval=False)
        if t == "request_permission":
            # The action itself is the user consent flow — no extra approval.
            return ActionDecision(danger=DangerLevel.low, reason="permission request", requires_approval=False)
        if t == "web_search":
            return ActionDecision(danger=DangerLevel.low, reason="read-only web search", requires_approval=False)
        if t == "computer":
            return ActionDecision(danger=DangerLevel.medium, reason="computer interaction wrapper", requires_approval=safe_mode)
        return ActionDecision(danger=DangerLevel.low, reason="default — unclassified action", requires_approval=False)
