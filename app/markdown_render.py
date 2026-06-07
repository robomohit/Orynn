from __future__ import annotations

import html as _html
import re
from pathlib import Path


MD_CODE_BG = "rgba(128,128,128,0.20)"


def md_to_safe_html(text: str) -> str:
    """Render a safe subset of Markdown to Qt-compatible rich text."""
    s = _html.escape(str(text or ""), quote=False)

    blocks: list[str] = []

    def _stash_block(m: re.Match[str]) -> str:
        blocks.append(m.group(1).strip("\n"))
        return f"\x00B{len(blocks) - 1}\x00"

    s = re.sub(r"```[^\n`]*\n?(.*?)```", _stash_block, s, flags=re.S)

    code_spans: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        code_spans.append(m.group(1))
        return f"\x00C{len(code_spans) - 1}\x00"

    s = re.sub(r"`([^`\n]+)`", _stash, s)

    def _heading(m: re.Match[str]) -> str:
        level = min(len(m.group(1)), 3)
        size = {1: "1.34em", 2: "1.17em", 3: "1.02em"}[level]
        return (f'<div style="font-size:{size};font-weight:700;'
                f'margin:6px 0 2px;">{m.group(2)}</div>')

    s = re.sub(r"(?m)^\s{0,3}(#{1,6})\s+(.+?)\s*$", _heading, s)
    s = re.sub(r"\*\*([^\n]+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", s)
    s = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "&bull;&nbsp;", s)

    def _restore(m: re.Match[str]) -> str:
        body = code_spans[int(m.group(1))]
        return (f'<span style="font-family:Consolas,\'Cascadia Mono\',monospace;'
                f'background:{MD_CODE_BG};">&nbsp;{body}&nbsp;</span>')

    s = re.sub(r"\x00C(\d+)\x00", _restore, s)
    s = s.replace("\n", "<br>")

    def _restore_block(m: re.Match[str]) -> str:
        code = blocks[int(m.group(1))]
        return (
            '<pre style="font-family:Consolas,\'Cascadia Mono\',monospace;'
            f'background:{MD_CODE_BG};padding:6px 9px;margin:2px 0;">'
            f'{code}</pre>'
        )

    return re.sub(r"\x00B(\d+)\x00", _restore_block, s)


def safe_local_folder_path(value: str) -> str:
    text = str(value or "").strip()
    if not text or any(ord(ch) < 32 for ch in text):
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(text)
    except Exception:
        return ""
    if parsed.scheme and len(parsed.scheme) > 1:
        return ""
    try:
        path = Path(text).expanduser()
        if not path.is_dir():
            return ""
        return str(path.resolve())
    except Exception:
        return ""
