import sys
from pathlib import Path

p = Path('app/tools.py')
c = p.read_text(encoding='utf-8')

method = """
    def _mouse_click_isolated(self, x: int, y: int, button: str, clicks: int, sw: int, sh: int):
        import win32gui, win32api, win32con
        try:
            import pyautogui
            screen_w, screen_h = pyautogui.size()
            abs_x = int(x * screen_w / sw)
            abs_y = int(y * screen_h / sh)
            client_pt = win32gui.ScreenToClient(self._isolated_hwnd, (abs_x, abs_y))
            lparam = win32api.MAKELONG(client_pt[0], client_pt[1])
            msg_down = win32con.WM_LBUTTONDOWN if button == 'left' else win32con.WM_RBUTTONDOWN
            msg_up = win32con.WM_LBUTTONUP if button == 'left' else win32con.WM_RBUTTONUP
            for _ in range(clicks):
                win32gui.PostMessage(self._isolated_hwnd, msg_down, win32con.MK_LBUTTON if button == 'left' else win32con.MK_RBUTTON, lparam)
                time.sleep(0.05)
                win32gui.PostMessage(self._isolated_hwnd, msg_up, 0, lparam)
                time.sleep(0.1)
            return ToolResult(ok=True, output=f'Sent {button} click to window (Isolated)')
        except Exception as e:
            return ToolResult(ok=False, output=f'Isolated click failed: {str(e)}')

    def _keyboard_type_isolated(self, text: str):
        import win32gui, win32con
        try:
            for char in text:
                win32gui.PostMessage(self._isolated_hwnd, win32con.WM_CHAR, ord(char), 0)
                time.sleep(0.02)
            return ToolResult(ok=True, output='Sent keys to window (Isolated)')
        except Exception as e:
            return ToolResult(ok=False, output=f'Isolated typing failed: {str(e)}')
"""

# Inject calls
c = c.replace(
    'def mouse_click(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):',
    'def mouse_click(self, x: int, y: int, button: str = "left", clicks=1, sw=1280, sh=800):\n        if self._isolated_hwnd:\n            return self._mouse_click_isolated(x, y, button, clicks, sw, sh)'
)
c = c.replace(
    'def keyboard_type(self, text: str):',
    'def keyboard_type(self, text: str):\n        if self._isolated_hwnd:\n            return self._keyboard_type_isolated(text)'
)

# Insert methods
if 'async def _left_click_drag_bg' in c:
    c = c.replace('    async def _left_click_drag_bg', method + '\n    async def _left_click_drag_bg')
else:
    # fallback
    c += method

p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully patched tools.py")
