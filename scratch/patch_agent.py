import sys
from pathlib import Path

p = Path('app/agent.py')
c = p.read_text(encoding='utf-8')

# 1. Update SubTaskWorker.run to handle mode logic
c = c.replace(
    '        is_computer_use = self.mode == "computer_use"',
    '        is_computer_use = self.mode == "computer_use"\n        is_isolated = self.mode == "computer_isolated"'
)

# 2. Update screenshot logic in SubTaskWorker.run
c = c.replace(
    '                elif not is_computer_use:',
    '                elif not is_computer_use and not is_isolated:'
)

# 3. Add Isolated screenshot handler
c = c.replace(
    '                        await self._emit("screenshot", shot_payload)',
    '                        await self._emit("screenshot", shot_payload)\n                    elif is_isolated:\n                        # In isolated mode, always send screenshot of the locked app\n                        screenshot = _capture_screenshot_b64(self.screen_width, self.screen_height)\n                        shot_payload = {"data": screenshot}\n                        window_rect = _get_active_window_rect(self.screen_width, self.screen_height)\n                        if window_rect: shot_payload["window_rect"] = window_rect\n                        await self._emit("screenshot", shot_payload)'
)

# 4. Update AgentService.submit_approval to lock HWND
c = c.replace(
    '    def submit_approval(self, task_id: str, action_id: str, approved: bool):',
    '    def submit_approval(self, task_id: str, action_id: str, approved: bool):\n        if approved:\n            try:\n                import win32gui\n                hwnd = win32gui.GetForegroundWindow()\n                if hwnd:\n                    self.tools._isolated_hwnd = hwnd\n                    _log.info(f"Isolated control locked to HWND {hwnd}")\n            except Exception: pass'
)

p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully patched agent.py")
