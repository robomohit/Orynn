from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "static"
STATIC_HTML = _STATIC / "index.html"


def _read_all_static() -> str:
    """Return index.html + style.css + app.js concatenated for pattern checks."""
    parts = [STATIC_HTML.read_text(encoding="utf-8")]
    for name in ("style.css", "app.js"):
        p = _STATIC / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_dynamic_lists_do_not_render_untrusted_api_data_with_innerhtml():
    html = _read_all_static()

    assert "grid.innerHTML = allSkills.map" not in html
    assert "grid.innerHTML = allMCPServers.map" not in html
    assert "toolsContainer.innerHTML = server.tools.map" not in html
    assert "addEventListener('click', () => toggleSkill(s.id))" in html
    assert "name.textContent = s.name" in html
    assert "pre.textContent = JSON.stringify(props, null, 2)" in html


def test_terminal_and_subtask_dynamic_values_use_textcontent():
    html = _read_all_static()

    assert "row.querySelector('.detail-title').innerHTML" not in html
    assert "title.textContent = command || 'Command output'" in html
    assert "channelEl.textContent = channel" in html
    assert "row.querySelector('.subtask-text').innerHTML +=" not in html
    assert "tag.textContent = event.worker_id" in html


def test_command_palette_rows_do_not_interpolate_model_labels_as_html():
    html = _read_all_static()

    assert 'row.innerHTML = `<span class="cmdk-icon">' not in html
    assert "label.textContent = c.label" in html
    assert "hint.textContent = c.hint" in html


def test_mode_selection_is_persisted_client_side():
    html = _read_all_static()

    assert "localStorage.setItem('ai_computer_mode'" in html
    assert "localStorage.getItem('ai_computer_mode')" in html


def test_project_folder_picker_is_present_and_persisted_client_side():
    html = _read_all_static()

    assert 'id="project-folder-trigger"' in html
    assert 'id="project-folder-modal"' in html
    assert "PROJECT_FOLDER_STORAGE_KEY" in html
    assert "localStorage.setItem(PROJECT_FOLDER_STORAGE_KEY" in html
    assert "project-folder-clear" in html


def test_phase_c1_turn_summary_present():
    html = _read_all_static()

    assert "turn-summary" in html
    assert "turn-summary-head" in html
    assert "turn-summary-body" in html
    assert "startTurnSummary" in html
    assert "finalizeTurnSummary" in html
    assert "_turnSummaryText" in html
    assert "activeTurnSummary" in html
    assert "finalizeTurnSummary();" in html


def test_phase_e_typography_whitespace():
    html = _read_all_static()

    # feed-card hover state
    assert ".feed-card:not(.is-active):hover" in html
    # line-height bumped to 1.6
    assert "line-height: 1.6;" in html
    # status dots replaced with text labels
    assert ".history-dot.running::after" in html
    assert "content: 'done'" in html
    # worker-tag colors reduced (workers 2-5 color lines removed)
    assert ".worker-tag.worker-2" not in html


def test_copy_task_button_present_and_wired():
    html = _read_all_static()

    assert ".history-retask" in html
    assert ".history-item.terminal .history-retask" in html
    assert "isTerminal" in html
    assert "↻ Copy task" in html
    assert "e.stopPropagation()" in html
    assert "inp.value = taskRecord.goal" in html
    assert "inp.focus()" in html


def test_settings_modal_surfaces_coding_backends():
    html = _read_all_static()

    assert 'id="coding-backends-section"' in html
    assert 'id="coding-backends-grid"' in html
    assert 'id="coding-backend-count"' in html
    assert "loadCodingBackends" in html
    assert "renderCodingBackends" in html


def test_mermaid_is_self_hosted():
    html = _read_all_static()

    assert "cdn.jsdelivr.net/npm/mermaid" not in html
    assert '/static/vendor/mermaid.min.js' in html


def test_shortcut_help_overlay_is_present_and_question_mark_wired():
    html = _read_all_static()

    assert 'id="shortcut-help"' in html
    assert "Keyboard shortcuts" in html
    assert "e.key === '?'" in html
    assert "isTextEntryTarget(e.target)" in html
    assert "openShortcutHelp()" in html


def test_phase_f_static_assets_split():
    """Phase F: CSS and JS are external files; index.html references them."""
    html = STATIC_HTML.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/style.css">' in html
    assert '<script src="/static/app.js" defer></script>' in html
    assert "<style>" not in html, "inline <style> block should be gone"
    assert (_STATIC / "style.css").exists(), "style.css must exist"
    assert (_STATIC / "app.js").exists(), "app.js must exist"
    # Ensure the split files contain expected content
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "--accent-h" in css, "CSS variables should be in style.css"
    assert "const init" in js, "main init function should be in app.js"
