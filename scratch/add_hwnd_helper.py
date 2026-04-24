from pathlib import Path

p = Path('app/agent.py')
c = p.read_text(encoding='utf-8')

func = """
def _get_hwnd_for_title(partial_title: str) -> Optional[int]:
    try:
        import win32gui
        target = partial_title.lower()
        res = []
        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                if target in title: res.append(hwnd)
        win32gui.EnumWindows(_enum, None)
        return res[0] if res else None
    except Exception: return None
"""

if '_get_hwnd_for_title' not in c:
    c += func

p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully added _get_hwnd_for_title")
