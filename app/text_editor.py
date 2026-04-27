from __future__ import annotations

from pathlib import Path

from .models import ToolError, ToolResult


class TextEditorTool:
    def __init__(self, workspace: Path, *, home_dir: Path | None = None):
        self.workspace = workspace.resolve()
        self.home_dir = (home_dir or Path.home()).expanduser().resolve()
        self._history: dict[str, list[str]] = {}

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        roots = [self.workspace]
        if self.home_dir not in roots:
            roots.append(self.home_dir)
        return tuple(roots)

    def _safe_path(self, value: str) -> Path:
        """Resolve a path and require it to stay inside an allowed root."""
        candidate = (self.workspace / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        if any(candidate == root or root in candidate.parents for root in self.allowed_roots):
            return candidate
        allowed = ", ".join(str(root) for root in self.allowed_roots)
        raise ToolError(f"Path escapes allowed roots: {value}. Allowed roots: {allowed}")

    def view(self, path: str, view_range: list[int] | None = None) -> ToolResult:
        p = self._safe_path(path)
        if p.is_dir():
            out = []
            root_depth = len(p.parts)
            for item in sorted(p.rglob("*")):
                if len(item.parts) - root_depth <= 2:
                    out.append(str(item.relative_to(p)))
            return ToolResult(ok=True, output="\n".join(out))
        if not p.exists():
            raise ToolError("File not found")
        lines = p.read_text().splitlines()
        start, end = 1, len(lines)
        if view_range:
            start, end = view_range
        numbered = [f"{i:4d}: {line}" for i, line in enumerate(lines, start=1) if start <= i <= end]
        return ToolResult(ok=True, output="\n".join(numbered))

    def create(self, path: str, file_text: str) -> ToolResult:
        file_text = file_text.replace("\\n", "\n").replace("\\t", "\t")
        p = self._safe_path(path)
        if p.exists():
            raise ToolError("File already exists")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(file_text, encoding="utf-8")
        return ToolResult(ok=True, output=f"Created {p}")

    def str_replace(self, path: str, old_str: str, new_str: str) -> ToolResult:
        old_str = old_str.replace("\\n", "\n").replace("\\t", "\t")
        new_str = new_str.replace("\\n", "\n").replace("\\t", "\t")
        p = self._safe_path(path)
        text = p.read_text(encoding="utf-8")
        count = text.count(old_str)
        if count == 0:
            raise ToolError("old_str not found. Provide more precise context.")
        if count > 1:
            raise ToolError("old_str appears multiple times; please disambiguate.")
        self._history.setdefault(str(p), []).append(text)
        p.write_text(text.replace(old_str, new_str), encoding="utf-8")
        return ToolResult(ok=True, output="Replaced 1 occurrence")

    def insert(self, path: str, insert_line: int, new_str: str) -> ToolResult:
        new_str = new_str.replace("\\n", "\n").replace("\\t", "\t")
        p = self._safe_path(path)
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        self._history.setdefault(str(p), []).append(text)
        lines = text.splitlines()
        idx = max(0, min(insert_line, len(lines)))
        lines[idx:idx] = new_str.splitlines()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return ToolResult(ok=True, output="Inserted")

    def undo_edit(self, path: str) -> ToolResult:
        p = self._safe_path(path)
        key = str(p)
        if key not in self._history or not self._history[key]:
            raise ToolError("No edit history for path")
        old = self._history[key].pop()
        p.write_text(old)
        return ToolResult(ok=True, output="Undo complete")
