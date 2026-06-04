"""The capsule answer card renders a safe subset of Markdown (so the model's
**bold** / `code` shows styled, not as literal stars) while escaping any HTML
so model/agent output can't inject markup."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.widget.capsule_widgets import _md_to_html


def test_bold_italic_code_render():
    assert _md_to_html("**441**") == "<b>441</b>"
    assert _md_to_html("the *display*") == "the <i>display</i>"
    out = _md_to_html("run `uia_click` now")
    assert "<span" in out and "uia_click" in out and "monospace" in out


def test_real_answer_string():
    src = ("The Calculator was opened, the sequence **63 x 7** was entered, "
           "and the result displayed is **441**.")
    out = _md_to_html(src)
    assert "<b>63 x 7</b>" in out
    assert "<b>441</b>" in out
    assert "**" not in out  # no stray literal markers left


def test_headings_and_bullets():
    out = _md_to_html("# Title\n- one\n- two")
    assert "<b>Title</b>" in out
    assert out.count("•") == 2  # two bullets
    assert "<br>" in out


def test_html_is_escaped_no_injection():
    out = _md_to_html("<script>alert(1)</script> **x**")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>x</b>" in out  # markdown still applied around the escaped html


def test_empty_and_plain():
    assert _md_to_html("") == ""
    assert _md_to_html("just plain text") == "just plain text"


def test_fenced_code_block_renders_as_pre():
    src = "Here:\n```python\ndef f(n):\n    return n\n```\nDone."
    out = _md_to_html(src)
    assert "<pre" in out and "</pre>" in out
    assert "```" not in out            # fences consumed
    assert "def f(n):" in out          # code preserved
    assert "monospace" in out


def test_fenced_block_escapes_html_and_keeps_indent():
    src = "```\nif a < b and c > d:\n    pass\n```"
    out = _md_to_html(src)
    assert "&lt;" in out and "&gt;" in out          # < > escaped
    assert "<script>" not in out
    # indentation preserved as real spaces inside <pre>
    assert "    pass" in out
