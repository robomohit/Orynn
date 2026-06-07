"""Safe Markdown rendering should not require optional Qt dependencies."""

from app.markdown_render import md_to_safe_html


def test_bold_italic_code_render():
    assert md_to_safe_html("**441**") == "<b>441</b>"
    assert md_to_safe_html("the *display*") == "the <i>display</i>"
    out = md_to_safe_html("run `uia_click` now")
    assert "<span" in out and "uia_click" in out and "monospace" in out


def test_real_answer_string():
    src = ("The Calculator was opened, the sequence **63 x 7** was entered, "
           "and the result displayed is **441**.")
    out = md_to_safe_html(src)
    assert "<b>63 x 7</b>" in out
    assert "<b>441</b>" in out
    assert "**" not in out


def test_headings_and_bullets():
    out = md_to_safe_html("# Title\n- one\n- two")
    # Headings now render with a real size hierarchy, not flat bold.
    assert "Title" in out
    assert "font-size" in out and "font-weight:700" in out
    assert "<b>Title</b>" not in out
    assert out.count("&bull;") == 2
    assert "<br>" in out


def test_heading_levels_have_distinct_sizes():
    h1 = md_to_safe_html("# Big")
    h2 = md_to_safe_html("## Medium")
    h3 = md_to_safe_html("### Small")
    assert "1.34em" in h1 and "1.17em" in h2 and "1.02em" in h3


def test_html_is_escaped_no_injection():
    out = md_to_safe_html("<script>alert(1)</script> **x**")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>x</b>" in out


def test_empty_and_plain():
    assert md_to_safe_html("") == ""
    assert md_to_safe_html("just plain text") == "just plain text"


def test_fenced_code_block_renders_as_pre():
    src = "Here:\n```python\ndef f(n):\n    return n\n```\nDone."
    out = md_to_safe_html(src)
    assert "<pre" in out and "</pre>" in out
    assert "```" not in out
    assert "def f(n):" in out
    assert "monospace" in out


def test_fenced_block_escapes_html_and_keeps_indent():
    src = "```\nif a < b and c > d:\n    pass\n```"
    out = md_to_safe_html(src)
    assert "&lt;" in out and "&gt;" in out
    assert "<script>" not in out
    assert "    pass" in out
