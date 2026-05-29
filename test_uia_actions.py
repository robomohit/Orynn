"""Integration test: drive a native app via UI Automation action types
WITHOUT screenshots. Proves uia_find / uia_type / uia_click / electron_check
flow through ToolExecutor.run_action end-to-end.

Run: python test_uia_actions.py
"""
import asyncio
import subprocess
import time
import uuid
from pathlib import Path

from app.tools import ToolExecutor
from app.models import Action, ActionType


def mk(t, **a):
    return Action(id=str(uuid.uuid4()), type=t, args=a, explanation="")


async def main():
    ex = ToolExecutor(Path.cwd())
    ex._background_mode = False  # drive the real desktop
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(2.2)
    print("[1] launched notepad pid", p.pid)

    # find the editor by accessible NAME (no screenshot)
    r = await ex.run_action(mk(ActionType.uia_find, query="Text editor", app="Notepad"))
    print("[2] uia_find editor:", r.ok, repr(r.output[:120]))
    assert r.ok, "uia_find could not locate the Notepad editor"

    # type via UIA ValuePattern
    sentence = "UIA agent test successful."
    r = await ex.run_action(mk(ActionType.uia_type, query="Text editor",
                               app="Notepad", text=sentence, clear_first=True))
    print("[3] uia_type:", r.ok, repr(r.output[:120]))
    assert r.ok, "uia_type failed"
    time.sleep(0.4)

    # verify the text actually landed (read it back through UIA)
    from app.widget.desktop_features import _find_uia_control
    ctrl, _info = _find_uia_control("Text editor", "Notepad")
    content = ctrl.GetValuePattern().Value
    print("[4] editor content:", repr(content[:80]))
    assert sentence in content, f"expected text not in editor: {content!r}"

    # activate a menu via InvokePattern (real button press, no pixels)
    r = await ex.run_action(mk(ActionType.uia_click, query="Edit", app="Notepad"))
    print("[5] uia_click Edit menu:", r.ok, repr(r.output[:120]))
    assert r.ok, "uia_click on Edit menu failed"
    time.sleep(0.4)

    # the menu should now be open -> "Find" menu item visible
    r = await ex.run_action(mk(ActionType.uia_find, query="Find", app="Notepad"))
    print("[6] uia_find 'Find' (menu open?):", r.ok, repr(r.output[:120]))
    assert r.ok, "Edit menu did not open (Find item not found)"

    # electron detection sanity
    r = await ex.run_action(mk(ActionType.electron_check, exe="C:/Windows/System32/notepad.exe"))
    print("[7] electron_check notepad:", r.ok, r.output)
    assert "is_electron=False" in r.output

    try:
        p.terminate()
    except Exception:
        pass
    print("\nALL UIA ACTION TESTS PASSED (no screenshots used).")


if __name__ == "__main__":
    asyncio.run(main())
