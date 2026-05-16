from pathlib import Path


STATIC_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


def test_dynamic_lists_do_not_render_untrusted_api_data_with_innerhtml():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "grid.innerHTML = allSkills.map" not in html
    assert "grid.innerHTML = allMCPServers.map" not in html
    assert "toolsContainer.innerHTML = server.tools.map" not in html
    assert "addEventListener('click', () => toggleSkill(s.id))" in html
    assert "name.textContent = s.name" in html
    assert "pre.textContent = JSON.stringify(props, null, 2)" in html


def test_terminal_and_subtask_dynamic_values_use_textcontent():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "row.querySelector('.detail-title').innerHTML" not in html
    assert "title.textContent = command || 'Command output'" in html
    assert "channelEl.textContent = channel" in html
    assert "row.querySelector('.subtask-text').innerHTML +=" not in html
    assert "tag.textContent = event.worker_id" in html


def test_command_palette_rows_do_not_interpolate_model_labels_as_html():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert 'row.innerHTML = `<span class="cmdk-icon">' not in html
    assert "label.textContent = c.label" in html
    assert "hint.textContent = c.hint" in html


def test_mode_selection_is_persisted_client_side():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "localStorage.setItem('ai_computer_mode'" in html
    assert "localStorage.getItem('ai_computer_mode')" in html


def test_project_folder_picker_is_present_and_persisted_client_side():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert 'id="project-folder-trigger"' in html
    assert 'id="project-folder-modal"' in html
    assert "PROJECT_FOLDER_STORAGE_KEY" in html
    assert "localStorage.setItem(PROJECT_FOLDER_STORAGE_KEY" in html
    assert "project-folder-clear" in html


def test_phase_c1_turn_summary_present():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert "turn-summary" in html
    assert "turn-summary-head" in html
    assert "turn-summary-body" in html
    assert "startTurnSummary" in html
    assert "finalizeTurnSummary" in html
    assert "_turnSummaryText" in html
    assert "activeTurnSummary" in html
    assert "finalizeTurnSummary();" in html


def test_copy_task_button_present_and_wired():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert ".history-retask" in html
    assert ".history-item.terminal .history-retask" in html
    assert "isTerminal" in html
    assert "aria-label=\"Copy task\"" in html
    assert "Copy task" in html
    assert "e.stopPropagation()" in html
    assert "inp.value = taskRecord.goal" in html
    assert "inp.focus()" in html
