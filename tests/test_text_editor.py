import pytest

from app.models import ToolError
from app.text_editor import TextEditorTool, _HISTORY_CAP


def test_text_editor(workspace):
    t = TextEditorTool(workspace)
    (workspace / "d1").mkdir()
    (workspace / "d1" / "f.txt").write_text("a\nb\nc\nd\n")
    assert "f.txt" in t.view("d1").output
    full = t.view("d1/f.txt").output
    assert "1:" in full and "4:" in full
    ranged = t.view("d1/f.txt", [2, 4]).output
    assert "1:" not in ranged and "2:" in ranged and "4:" in ranged

    assert t.str_replace("d1/f.txt", "b", "B").ok
    with pytest.raises(ToolError):
        t.str_replace("d1/f.txt", "zz", "x")
    (workspace / "d1" / "m.txt").write_text("x\nx\n")
    with pytest.raises(ToolError):
        t.str_replace("d1/m.txt", "x", "y")

    (workspace / "d1" / "i.txt").write_text("1\n2\n")
    t.insert("d1/i.txt", 0, "0")
    assert (workspace / "d1" / "i.txt").read_text().startswith("0")
    t.insert("d1/i.txt", 2, "X")
    assert "X" in (workspace / "d1" / "i.txt").read_text().splitlines()[2]
    t.undo_edit("d1/i.txt")
    with pytest.raises(ToolError):
        t.undo_edit("d1/none.txt")
    with pytest.raises(ToolError):
        t.view("../escape.txt")
    with pytest.raises(ToolError):
        t.view(str((workspace.parent / "escape.txt").resolve()))


def test_undo_preserves_utf8(workspace):
    t = TextEditorTool(workspace)
    f = workspace / "utf8.txt"
    original = "héllo wörld — 日本語\n"
    f.write_text(original, encoding="utf-8")
    t.str_replace("utf8.txt", "héllo", "hello")
    t.undo_edit("utf8.txt")
    assert f.read_text(encoding="utf-8") == original


def test_history_cap_enforced(workspace):
    t = TextEditorTool(workspace)
    f = workspace / "cap.txt"
    f.write_text("v0")
    for i in range(_HISTORY_CAP + 5):
        t.str_replace("cap.txt", f"v{i}", f"v{i+1}")
    key = str(f.resolve())
    assert len(t._history[key]) == _HISTORY_CAP
    # undo still works for recent edits
    t.undo_edit("cap.txt")
    assert len(t._history[key]) == _HISTORY_CAP - 1
