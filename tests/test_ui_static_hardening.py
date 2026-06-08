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
    assert "tile.innerHTML =" not in html
    assert "toolsContainer.innerHTML = server.tools.map" not in html
    assert "container.innerHTML = Object.entries(providers).map" not in html
    assert "addEventListener('click', () => toggleSkill(s.id))" in html
    assert "name.textContent = s.name" in html
    assert "chip.title = `${name}: ${status}`" in html
    assert "pre.textContent = JSON.stringify(props, null, 2)" in html


def test_app_js_has_no_literal_nul_sentinels():
    js = (_STATIC / "app.js").read_bytes()
    assert b"\x00" not in js
    assert b"@@AIC@" in js
    assert b"const stash = (html, kind)" in js


def test_terminal_and_subtask_dynamic_values_use_textcontent():
    html = _read_all_static()

    assert "row.querySelector('.detail-title').innerHTML" not in html
    assert "title.textContent = command || 'Command output'" in html
    assert "channelEl.textContent = channel" in html
    assert "row.querySelector('.subtask-text').innerHTML +=" not in html
    assert "row.innerHTML = `<div class=\"subtask-icon\">" not in html
    assert "text.textContent = subtask.description" in html
    assert "tag.textContent = event.worker_id" in html


def test_command_palette_rows_do_not_interpolate_model_labels_as_html():
    html = _read_all_static()

    assert 'row.innerHTML = `<span class="cmdk-icon">' not in html
    assert "label.textContent = c.label" in html
    assert "hint.textContent = c.hint" in html


def test_plan_checklist_renders_model_labels_as_text():
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")

    assert "const safeMermaidId" not in js
    assert "const safeMermaidLabel" not in js
    assert "text.textContent = subtask.description" in js
    assert "row.appendChild(text)" in js
    assert 'subtask.description.replace(/"/g' not in js


def test_mode_selection_no_longer_restores_frontend_routing():
    html = _read_all_static()

    assert "localStorage.setItem('orynn_mode'" not in html
    assert "localStorage.getItem('orynn_mode')" not in html
    assert "localStorage.getItem('ai_computer_mode')" not in html
    assert "localStorage.removeItem('orynn_mode')" in html
    assert "setMode('auto')" in html


def test_project_folder_picker_is_present_and_persisted_client_side():
    html = _read_all_static()

    assert 'id="project-folder-trigger"' in html
    # Lightweight dropdown picker (Codex-style) — not a heavy in-app file browser.
    assert 'id="project-folder-menu"' in html
    assert "toggleFolderMenu" in html
    assert "PROJECT_FOLDER_STORAGE_KEY" in html
    assert "localStorage.setItem(PROJECT_FOLDER_STORAGE_KEY" in html
    # General-mode (clear) option lives inside the dropdown
    assert "isGeneral: true" in html


# NOTE: the verbose turn-summary + step-timeline + per-step copy-button UI was
# simplified to the Codex-style work fold — see test_codex_work_summary_folding.


def test_codex_work_summary_folding():
    """Codex-style: working steps fold under a 'Worked for Xm Ys ›' toggle
    when a task finishes, keeping the final answer as the primary reading."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # JS: summarizeWork function and its invocation in the done handler
    assert "const summarizeWork" in js, "summarizeWork function missing"
    assert "fmtWorkDuration" in js, "duration formatter missing"
    assert "work-summary" in js, "work-summary class missing from JS"
    assert "work-summary-head" in js, "work-summary-head class missing from JS"
    assert "work-summary-body" in js, "work-summary-body class missing from JS"
    assert "body.hidden = !body.hidden" in js, "toggle logic missing"
    assert "summarizeWork(elapsed)" in js, "summarizeWork not called in done handler"

    # CSS: styling for the collapsed/expanded work summary
    assert ".work-summary" in css, "work-summary CSS rule missing"
    assert ".work-summary-head" in css, "work-summary-head CSS rule missing"
    assert ".work-summary-body" in css, "work-summary-body CSS rule missing"
    assert ".work-summary.open .work-summary-head .work-chevron" in css, "open-state chevron rotation missing"
    assert ".work-summary-head.no-expand" in css, "no-expand variant missing"


def test_worked_for_capstone_when_minimal_stream_suppresses_work():
    """Minimal stream hides the per-step working cards, so a finished
    desktop/coding turn has nothing to fold. A 'Worked for X' capstone must
    still appear when the turn did real tool work — but never for a plain chat."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # Work is tracked on action_start (the suppressed event) and reset per turn.
    assert "_turnDidWork = false" in js, "_turnDidWork not declared/reset"
    assert "event.type === 'action_start') _turnDidWork = true" in js, \
        "_turnDidWork not set when a suppressed action_start fires"
    # collapseWorkIntoFold emits the capstone only when work happened (gated, not for chat).
    assert "work-capstone" in js, "work-capstone not emitted from JS"
    assert "_turnDidWork && elapsedSeconds >= 3" in js, \
        "capstone must be gated on real work + a non-trivial duration"
    # CSS: the quiet capstone has its own rule.
    assert ".work-capstone" in css, "work-capstone CSS rule missing"


def test_phase_e_typography_whitespace():
    html = _read_all_static()

    # feed-card hover state
    assert ".feed-card:not(.is-active):hover" in html
    # line-height bumped to 1.6
    assert "line-height: 1.6;" in html
    # status shown as a small calm dot (running pulses); no loud text labels
    assert ".history-dot.running" in html
    assert "dot-pulse" in html
    # worker-tag colors reduced (workers 2-5 color lines removed)
    assert ".worker-tag.worker-2" not in html


def test_copy_task_button_present_and_wired():
    html = _read_all_static()

    assert ".history-retask" in html
    assert ".history-item:hover .history-retask" in html
    assert "isTerminal" in html
    assert "retask.textContent = '\\u21bb Copy task'" in html
    assert "e.stopPropagation()" in html
    assert "inp.value = taskRecord.goal" in html
    assert "inp.focus()" in html


def test_dashboard_chrome_uses_text_nodes_for_dynamic_rows():
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")

    assert "sb.innerHTML" not in js
    assert "mp.innerHTML" not in js
    assert "list.innerHTML = rows.map" not in js
    assert "item.innerHTML = `\n      <span class=\"history-dot" not in js
    assert "row.querySelector('.folder-entry-name').textContent" not in js
    assert "history-meta\">" not in js
    assert "folder-entry-meta\">${" not in js
    assert "desktop-access-badge\">${" not in js
    assert "statusText.textContent = map[key] || humanize(key)" in js
    assert "modeValue.textContent = label" in js
    assert "badgeEl.textContent = badge" in js
    assert "goal.textContent = historyTitle(taskRecord.goal)" in js
    assert "meta.textContent = relTimeShort(taskRecord.created_at || taskRecord.timestamp || taskRecord.finished_at) || ''" in js
    # folder-picker dropdown rows render via textContent (XSS-safe)
    assert "lbl.textContent = label" in js
    assert "s.textContent = sub" in js


def test_assistant_markdown_is_sanitized_and_links_are_protocol_gated():
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8", errors="replace")

    assert "const safeMarkdownHref" in js
    assert "if (!/^(https?:\\/\\/|mailto:)/i.test(trimmed)) return '';" in js
    assert "['http:', 'https:', 'mailto:'].includes(parsed.protocol)" in js
    assert "const sanitizeRenderedMarkdown" in js
    assert "const allowedTags = new Set(['A', 'BR', 'CODE', 'EM', 'LI', 'P', 'PRE', 'STRONG', 'UL', 'OL', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HR']);" in js
    assert "child.setAttribute('target', '_blank')" in js
    assert "child.setAttribute('rel', 'noopener noreferrer')" in js
    assert "return sanitizeRenderedMarkdown(text);" in js
    assert "el.innerHTML = renderMarkdown(text)" in js
    assert ".message.assistant a.md-link" in css


def test_settings_modal_surfaces_coding_backends():
    html = _read_all_static()

    assert 'id="coding-backends-section"' in html
    assert 'id="coding-backends-grid"' in html
    assert 'id="coding-backend-count"' in html
    assert "loadCodingBackends" in html
    assert "renderCodingBackends" in html


def test_settings_modal_surfaces_readiness_preflight():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert 'id="readiness-section"' in html
    assert 'id="readiness-grid"' in html
    assert 'id="readiness-score"' in html
    assert "loadReadiness" in js
    assert "renderReadiness" in js
    assert "/api/readiness" in js
    assert "await keyReady;\n      readinessState = await api('/api/readiness')" in js
    assert "detail.textContent = check.detail || check.fix || ''" in js
    assert ".readiness-grid" in css
    assert ".readiness-item" in css
    assert "#readiness-score[data-status='ready']" in css


def test_settings_modal_surfaces_trust_center():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    for dom_id in (
        'id="trust-section"',
        'id="trust-grid"',
        'id="trust-status"',
        'id="trust-pending-list"',
        'id="trust-ledger-list"',
        'id="trust-active-list"',
    ):
        assert dom_id in html

    assert "loadTrustReport" in js
    assert "renderTrustReport" in js
    assert "/api/trust/report" in js
    assert "grid.replaceChildren(" in js
    assert "pendingList.replaceChildren" in js
    assert "ledgerList.replaceChildren" in js
    assert "activeList.replaceChildren" in js
    assert "trustTaskControl" in js
    assert "`/api/tasks/${encoded}/${action}`" in js
    assert "`/api/tasks/${encoded}`, 'DELETE'" in js
    assert "titleEl.textContent = title" in js
    assert "detailEl.textContent = detail" in js
    assert "badgeEl.textContent = badge" in js
    assert "btn.textContent = action.label" in js
    assert "btn.addEventListener('click', action.onClick)" in js
    assert ".trust-grid" in css
    assert ".trust-card" in css
    assert ".trust-row" in css
    assert ".trust-row-actions" in css
    assert ".trust-action-btn.danger" in css
    assert "#trust-status[data-status='attention']" in css


def test_task_start_is_gated_by_readiness_preflight():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    for dom_id in (
        'id="readiness-preflight"',
        'id="readiness-preflight-list"',
        'id="readiness-preflight-settings"',
        'id="readiness-preflight-cancel"',
        'id="readiness-preflight-continue"',
    ):
        assert dom_id in html

    assert "const requestReadinessPreflight" in js
    assert "const ensureTaskReadiness" in js
    assert "const handleServerPreflightRejection" in js
    assert "let modelSelectionTouched = false" in js
    assert "const selectedModelForRequest" in js
    assert "const taskReadinessIssues" not in js
    assert "/api/tasks/preflight" in js
    assert "const readinessDecision = await ensureTaskReadiness({" in js
    assert "const requestedMode = 'auto'" in js
    assert "const requestedModel = selectedModelForRequest(requestedMode)" in js
    assert "if ((mode || 'auto') === 'auto' && !modelSelectionTouched) return null" in js
    assert "modelSelectionTouched = true" in js
    assert "const effectiveMode = requestedMode" in js
    assert "const effectiveIsolatedApp = ''" in js
    assert "const displayModel = requestedModel || readinessDecision.preflight?.selected_model || null" in js
    assert "setTaskTitle(goal, { mode: effectiveMode, model: displayModel, status: 'running' })" in js
    assert "event.permission_kind === 'desktop_control'" in js
    assert "window.pendingDesktopPermission = true" in js
    assert "Orynn is asking for desktop control." in js
    assert "setDesktopSessionActive(true, 'computer', target)" in js
    assert "await handleServerPreflightRejection(err, taskPayload)" in js
    assert "taskPayload.readiness_override = true" in js
    assert "readiness_override: !!readinessDecision.override" in js
    assert "const originalTaskId = currentViewedTask" in js
    assert "const startRetriedTask = (result) =>" in js
    assert "const readinessDecision = await ensureTaskReadiness({" in js
    assert "`/api/tasks/${originalTaskId}/retry`, 'POST', { readiness_override: readinessOverride }" in js
    assert "`/api/tasks/${originalTaskId}/retry`, 'POST', { readiness_override: true }" in js
    assert "requestDesktopAccess({ mode: effectiveMode, isolatedApp: effectiveIsolatedApp })" not in js
    assert "const displayModel = preflight.selected_model || result.model || $('model-id').value" in js
    assert "const effectiveMode = 'auto'" in js
    assert "btnContinue.hidden = blocked" in js
    assert "name.textContent = issue.label" in js
    assert "detail.textContent = issue.detail" in js
    assert ".readiness-preflight-row.blocked" in css
    assert ".readiness-preflight-row.warning" in css


def test_mermaid_dependency_is_removed():
    html = _read_all_static()

    assert "mermaid" not in html.lower()
    assert "cdn.jsdelivr.net/npm/mermaid" not in html
    assert '/static/vendor/mermaid.min.js' not in html


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


def test_control_trace_surface_present():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert "overlayTraceParts" in js
    assert "renderControlTrace" in js
    assert "overlayRectText" in js
    assert "overlayPointText" in js
    assert "control_layer" in js
    assert "control_reason" in js
    assert "fallback_reason" in js
    assert 'id="topbar-control"' in html
    assert 'id="topbar-control-layer"' in html
    assert "const controlLayerClass" in js
    assert "const setControlSurface" in js
    assert "const setControlProfileSurface" in js
    assert "event.type === 'control_profile'" in js
    assert "setControlProfileSurface(event)" in js
    assert "setLiveStatus('Control route'" not in js
    assert "setControlSurface({" in js
    assert ".topbar-control" in css
    assert ".topbar-control.uia" in css
    assert ".topbar-control.vision" in css
    assert "app_rect" in js
    assert "control-trace-chip" in css
    assert ".control-trace-chip.layer" in css
    assert "demoUiaOverlayStart" in js
    assert "demoUiaOverlayResult" in js
    assert 'id="btn-control-report"' in html
    assert "showControlReport" in js
    assert "/control-trace" in js
    assert "summary.profile_route" in js
    assert "summary.used_profile_route" in js
    assert "summary.route_changed" in js
    assert "report.profiles || []" in js
    assert "control-report-grid" in css


def test_trust_timeout_events_update_waiting_cards():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")

    assert "event.type === 'approval_timeout' || event.type === 'permission_timeout'" in js
    assert "setActionState(entry, 'Timed out', 'fail')" in js
    assert "Approval timed out" in js
    assert "Permission timed out" in js
    assert "$('approval')?.classList.remove('show')" in js
    assert "$('permission')?.classList.remove('show')" in js


def test_dashboard_recovers_active_task_after_reload():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert "const recoverActiveTask" in js
    assert "/api/active-tasks?cb=" in js
    assert "await recoverActiveTask()" in js
    assert "const stillLive = loadTaskLog(activeId, events, item, { live: true, record, silent: true })" in js
    assert "if (!stillLive) return false" in js
    assert "streamCursor = streamCursorAfter(events)" in js
    assert "showLiveTaskControls(record || { status: 'running' }, meta)" in js
    assert "restorePendingTrustModal(pendingTrustRequest(events), taskId)" in js
    assert "events.forEach((e) => processTaskEvent(e, { replay: true, taskId, suppressToasts: true }))" in js
    assert "setDesktopSessionActive(!queued && isDesktopMode(meta.mode)" in js
    assert "openStream(activeId)" in js
    assert "setLiveStatus('Reconnected'" in js
    assert ".history-dot.paused" in css


def test_reconnect_only_reopens_unresolved_trust_prompts():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")

    assert "const pendingTrustRequest" in js
    assert "const restorePendingTrustModal" in js
    assert "pending.delete(id)" in js
    assert "pending.delete('__plan__')" in js
    assert "if (type === 'done' || type === 'error' || type === 'cancelled') pending.clear()" in js
    assert "wrap.dataset.trustControls = '1'" in js
    assert "pWrap.dataset.trustControls = '1'" in js
    assert "clearTrustControls(entry, event.action_id || '')" in js
    assert "clearTrustControls(actionCards.__plan__, '__plan__')" in js


def test_liquid_glass_capsule_widget_present():
    """The floating widget is a single liquid-glass command capsule."""
    html = STATIC_HTML.read_text(encoding="utf-8")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # capsule HTML structure
    assert 'class="vcap"' in html
    assert 'id="vcap-wave"' in html          # dot-matrix waveform canvas
    assert 'id="vpanel-text"' in html        # composer input (funnels to pipeline)
    assert 'id="vcap-reply"' in html         # reply grows the capsule
    assert 'id="vcap-context"' in html       # adaptive scope / state surface
    assert 'id="vcap-actions"' in html       # app-aware quick actions
    assert 'id="vcap-stop"' in html          # hard stop always lives on capsule
    assert 'rel="icon"' in html              # browser/native shell avoids favicon 404 noise
    # widget-shell mode + behaviours in JS
    assert "widgetShell" in js
    assert "params.get('widget') === '1'" in js
    assert "ai-computer.vorb-position.v2" in js
    assert "e.ctrlKey && e.shiftKey && e.code === 'Space'" in js
    assert "CAPSULE_CONTEXT_ACTIONS" in js
    assert "deriveCapsuleState" in js
    assert "renderCapsuleState({ state: capState" in js
    assert "capsuleControlLayer" in js
    assert "setControlProfileSurface" in js
    assert "overlayControlLayer" in js
    assert "control_layer" in js
    # capsule CSS + widget-shell overrides
    assert ".vcap {" in css
    assert ".vcap-context" in css
    assert ".vcap-action" in css
    assert "body.widget-shell #vorb-root" in css


def test_free_model_premium_controls_present():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert 'id="plan-first-toggle"' in html
    assert 'id="notify-toggle"' in html
    assert 'id="checkpoint-toggle"' in html
    assert 'id="autonomy-level"' in html
    assert 'id="app-plan-edit"' in html
    assert "plan_first" in js
    assert "notify_on_completion" in js
    assert "auto_commit" in js
    assert "plan_override" in js
    assert "provider_info" in js
    assert ".composer-options" in css
    assert ".approval-plan-edit" in css


def test_dynamic_widget_library_present():
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    for widget in (
        "clutter_sweeper",
        "smart_organizer",
        "file_preview",
        "resource_radar",
        "quick_settings",
        "network_guardian",
        "action_approver",
        "email_summary",
        "source_grid",
        "data_table",
    ):
        assert widget in js

    assert "renderAgentWidget" in js
    assert "playWidgetGallery" in js
    assert 'button data-v="widgets"' in html
    assert ".ai-widget-card" in css
    assert ".source-grid" in css
    assert ".widget-table" in css
    assert "body:not(.widget-shell) #vorb-root" in css


def test_desktop_launcher_has_frameless_widget_mode():
    root = STATIC_HTML.parents[0].parent
    launcher = (root / "run_desktop.py").read_text(encoding="utf-8")
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")

    # run_desktop.py defaults to Qt sidekick; --dashboard opts into pywebview
    assert '"--dashboard"' in launcher
    assert "qt_shell" in launcher

    # the Qt shell is a frameless, translucent, always-on-top capsule built
    # from NATIVE Qt widgets (QtWebEngine can't render transparent on Windows)
    # — it funnels tasks to the local server over HTTP.
    assert "Orynn Sidekick" in qt_shell
    assert "/api/tasks" in qt_shell
    assert "FramelessWindowHint" in qt_shell
    assert "WindowStaysOnTopHint" in qt_shell
    assert "/api/tasks/preflight" in qt_shell
    assert "payload[\"readiness_override\"] = True" in qt_shell
    assert "_retry_after_preflight_rejection" in qt_shell
    assert "readiness_preflight_warning" in qt_shell
    assert "Capability fallback" in qt_shell
    assert "Setup needed before this task can run." in qt_shell
    assert "ORYNN_TOPMOST" in qt_shell
    assert "ORYNN_TOOL_WINDOW" in qt_shell
    assert "WA_TranslucentBackground" in qt_shell
    assert "_apply_pill_glass" in qt_shell
    assert "context_bar" in qt_shell
    assert "_set_capsule_state" in qt_shell
    assert "_capsule_scope" in qt_shell
    assert "_last_control_layer" in qt_shell
    assert "_last_control_reason" in qt_shell
    assert "_pause_or_resume" in qt_shell


def test_qt_capsule_light_glass_is_readable_on_busy_backdrops():
    root = STATIC_HTML.parents[0].parent
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")

    assert "QColor(255, 255, 255, 196)" in qt_shell
    assert "QColor(248, 250, 253, 210)" in qt_shell
    assert "QColor(238, 242, 249, 224)" in qt_shell
    assert "QColor(70, 84, 110, 36)" in qt_shell


def test_qt_capsule_surfaces_trust_prompts():
    root = STATIC_HTML.parents[0].parent
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")
    capsule_widgets = (root / "app" / "widget" / "capsule_widgets.py").read_text(encoding="utf-8")

    assert "def _emit_approval_widget" in qt_shell
    assert "def _emit_permission_widget" in qt_shell
    assert "def _emit_trust_timeout_widget" in qt_shell
    assert 't == "approval_required"' in qt_shell
    assert 't == "permission_required"' in qt_shell
    assert 't in ("approval_timeout", "permission_timeout")' in qt_shell
    assert '"/api/approvals"' in qt_shell
    assert '"/api/permissions"' in qt_shell
    assert '"approve": True' in qt_shell
    assert '"grant": True' in qt_shell
    assert "Waiting for approval..." in qt_shell
    assert "Waiting on permission..." in qt_shell
    assert '"waiting_approval"' in qt_shell
    assert 'client.post(f"{_API_BASE}/api/session")' in capsule_widgets


def test_qt_capsule_recovers_active_tasks_on_restart():
    root = STATIC_HTML.parents[0].parent
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")
    capsule_widgets = (root / "app" / "widget" / "capsule_widgets.py").read_text(encoding="utf-8")

    assert "def recover_active" in qt_shell
    assert "def _recover_active" in qt_shell
    assert 'f"{BASE}/api/active-tasks"' in qt_shell
    assert "def _latest_active_record" in qt_shell
    assert "def _emit_recovered_snapshot" in qt_shell
    assert "def _poll_recovered_task" in qt_shell
    assert "def _handle_task_event" in qt_shell
    assert "controlProfile = Signal(dict)" in qt_shell
    assert "self.runner.controlProfile.connect(self._on_control_profile)" in qt_shell
    assert 't == "control_profile"' in qt_shell
    assert "def _on_control_profile" in qt_shell
    assert "for attempt in range(10)" in qt_shell
    assert "Reconnected to running task" in qt_shell
    assert "_pending_trust_event(log)" in qt_shell
    assert "self.current_task_id = tid" in qt_shell
    assert "self.runningChanged.emit(True)" in qt_shell
    assert "QTimer.singleShot(950, self.runner.recover_active)" in qt_shell
    assert "_df.save_pending_task(goal, payload.get(\"mode\", \"auto\"), tid)" in qt_shell
    assert 'payload if isinstance(payload, dict) else {"url": str(payload or "")}' in capsule_widgets


def test_qt_capsule_prefers_task_event_stream_with_poll_fallback():
    root = STATIC_HTML.parents[0].parent
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")

    assert "def _stream_task_events" in qt_shell
    assert "keepalive_timeout_seconds=8" in qt_shell
    # The stream must use a finite read timeout (not None) so a stalled
    # connection raises and falls back to polling instead of hanging forever.
    assert "timeout=None" not in qt_shell.split("def _stream_task_events", 1)[1][:1500]
    assert "httpx.Timeout(" in qt_shell
    # And a safety net: if the backend goes terminal without the 'done' event
    # arriving over the stream, bail to the poll fallback.
    assert "self._is_terminal_status(st)" in qt_shell
    assert 'line.startswith("data:")' in qt_shell
    assert "json.loads(line[5:].strip())" in qt_shell
    assert "cursor = max(cursor, int(ev.get(\"seq\")) + 1)" in qt_shell
    assert "self._handle_task_event(tid, ev, state)" in qt_shell
    assert "Live stream interrupted - falling back..." in qt_shell
    assert "outcome, active_tid, cursor = self._stream_task_events(c, tid, cursor, state)" in qt_shell
    assert "self._poll_recovered_task(c, active_tid, cursor, state)" in qt_shell
    assert "outcome, stream_tid, stream_cursor = self._stream_task_events(" in qt_shell
    assert "c, stream_tid, stream_cursor, stream_state, payload=payload)" in qt_shell
    assert "seen = stream_cursor" in qt_shell


def test_qt_capsule_dynamic_labels_are_plain_text_and_links_are_safe():
    root = STATIC_HTML.parents[0].parent
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")
    capsule_widgets = (root / "app" / "widget" / "capsule_widgets.py").read_text(encoding="utf-8")

    assert "def _plain_label" in qt_shell
    assert "setTextFormat(Qt.PlainText)" in qt_shell
    assert "setOpenExternalLinks(False)" in qt_shell
    assert "self.status = _plain_label" in qt_shell
    assert "self.scope_chip = _plain_label" in qt_shell
    assert "self.vision_chip = _plain_label" in qt_shell
    assert "self.phase_chip = _plain_label" in qt_shell
    assert "self.reply = _plain_label" in qt_shell
    # The answer-card body renders a SAFE markdown subset (rich text), so the
    # streaming update uses _set_rich_text — which escapes HTML before applying
    # any tags (see _md_to_html below), so it can't be an injection vector.
    assert "_set_rich_text(labels[-1], text[:4000])" in qt_shell
    assert "def _safe_external_url" in qt_shell
    assert "safe_url = _safe_external_url(u)" in qt_shell
    assert "QPushButton(_shorten_url(safe_url))" in qt_shell
    assert "setOpenExternalLinks(True)" not in qt_shell

    assert "def _plain_label" in capsule_widgets
    assert "title = _plain_label(title_text)" in capsule_widgets
    # Card body renders safe Markdown (rich text). The renderer MUST escape HTML
    # first so model/agent output can't inject markup — that's the safety gate.
    assert "_set_rich_text(body, body_text)" in capsule_widgets
    assert "def _md_to_html" in capsule_widgets
    assert "_html.escape(" in capsule_widgets
    assert "_set_plain_text(self._status, msg)" in capsule_widgets
    assert "url = _safe_external_url(payload_obj.get(\"url\", \"\"))" in capsule_widgets
    assert "def _safe_local_folder_path" in capsule_widgets
    assert "path = _safe_local_folder_path(payload_obj.get(\"path\", payload_obj.get(\"folder_path\", \"\")))" in capsule_widgets


def test_qt_capsule_open_folder_payload_is_local_directory_only(tmp_path):
    from app.markdown_render import safe_local_folder_path

    folder = tmp_path / "folder"
    folder.mkdir()
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a folder", encoding="utf-8")

    assert safe_local_folder_path(str(folder)) == str(folder.resolve())
    assert safe_local_folder_path(str(file_path)) == ""
    assert safe_local_folder_path(str(tmp_path / "missing")) == ""
    assert safe_local_folder_path("https://example.com/folder") == ""
    assert safe_local_folder_path(f"{folder}\n--bad") == ""


def test_desktop_dashboard_launch_stays_native_not_browser_fallback():
    root = STATIC_HTML.parents[0].parent
    launcher = (root / "run_desktop.py").read_text(encoding="utf-8")
    qt_shell = (root / "app" / "widget" / "qt_shell.py").read_text(encoding="utf-8")

    assert '"--dashboard"' in launcher
    assert "import webview" in launcher
    assert "pywebview is not installed" in launcher
    assert "def _server_healthy" in launcher
    assert "def _wait_for_server" in launcher
    assert "def _start_backend" in launcher
    assert "_server_already_running" not in launcher
    assert 'f"http://127.0.0.1:{port}"' in launcher
    assert "webbrowser.open(f\"http://127.0.0.1:{port}\")" not in qt_shell
    assert "Dashboard desktop window failed to launch" in qt_shell


def test_dashboard_titlebar_is_native_shell_only():
    html = STATIC_HTML.read_text(encoding="utf-8")

    assert 'class="titlebar" id="titlebar" data-os="win" hidden' in html
    assert "function setDesktopChrome(enabled)" in html
    assert "bar.hidden = !enabled" in html
    assert "shell.classList.toggle('no-titlebar', !enabled)" in html
    assert "window.addEventListener('pywebviewready'" in html


def test_live_reasoning_not_filtered_by_step_announcement(monkeypatch):
    """Live reasoning events (thought tokens, composing…) bypass the step-announcement
    filter so they display immediately via setLiveStatus (AI-17)."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    # The live-pass-through guard must appear BEFORE the isStepAnnouncement filter
    live_guard = "if (event.live) { renderReasoning(event); return; }"
    step_filter = "if (_isStepAnnouncement(event)) return;"
    assert live_guard in js, "live guard missing from processTaskEvent reasoning block"
    assert js.index(live_guard) < js.index(step_filter), "live guard must precede step filter"


def test_ai28_liquid_glass_css_static_asset():
    """AI-28: liquid-glass.css is present, non-empty, and linked from index.html."""
    css_path = _STATIC / "liquid-glass.css"
    assert css_path.exists(), "static/liquid-glass.css not found"
    assert css_path.stat().st_size > 0, "static/liquid-glass.css is empty"
    html = STATIC_HTML.read_text(encoding="utf-8")
    assert "liquid-glass.css" in html, "liquid-glass.css not linked from index.html"


def test_onboarding_hidden_steps_are_hard_hidden():
    """The onboarding wizard must never render multiple hidden flex steps at once."""
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")

    assert "[hidden] { display: none !important; }" in css
    assert ".onb-step[hidden]" in css
    assert ".onb-overlay[hidden]" in css
    assert "display: none !important;" in css
    assert "steps.forEach(s => { s.hidden = (Number(s.dataset.step) !== cur); });" in js


def test_liquid_glass_styles_dashboard_surfaces():
    """Liquid glass covers the dashboard, not only the floating capsule."""
    css = (_STATIC / "liquid-glass.css").read_text(encoding="utf-8")

    assert "FLOATING WIDGET ONLY" not in css
    assert "body[data-glass=\"on\"]:not(.widget-shell)" in css
    for selector in (
        ".sidebar",
        ".topbar",
        ".composer",
        ".modal",
        ".onb-card",
        ".cmdk-panel",
    ):
        assert f"body[data-glass=\"on\"]:not(.widget-shell) {selector}" in css


def test_focused_dashboard_idle_state_is_minimal():
    html = STATIC_HTML.read_text(encoding="utf-8")
    css = (_STATIC / "dynamic.css").read_text(encoding="utf-8")
    glass = (_STATIC / "liquid-glass.css").read_text(encoding="utf-8")

    assert 'placeholder="Message Orynn"' in html
    assert 'rows="1"' in html
    assert "body:not(.task-active) .welcome p" in css
    assert "display: none;" in css
    assert ".app-shell {\n  grid-template-columns: 244px minmax(0, 1fr);" in css
    assert "body:not(.task-active) .app-shell.sb-on .main {\n  grid-row: 1 / 3;" in css
    assert "body:not(.task-active) .app-shell.sb-on .statusbar {\n  display: none;" in css
    assert "body:not(.task-active) .topbar {\n  position: absolute;" in css
    assert "body:not(.task-active) .topbar .nav-toggle {\n  pointer-events: auto;" in css
    assert "body[data-glass=\"on\"]:not(.widget-shell):not(.task-active) .topbar {\n  border-color: transparent !important;" in css
    assert "background-image: none !important;" in css
    assert ".composer textarea {\n  display: block;" in css
    assert "overflow: hidden;" in css
    assert ".main::before {\n  display: none;" in css
    assert "body[data-glass=\"on\"]:not(.widget-shell) {\n  background: var(--bg-0);" in glass
    assert "body[data-glass=\"on\"]:not(.widget-shell)::before {\n  display: none;" in glass


def test_focused_dashboard_active_stream_is_single_column():
    css = (_STATIC / "dynamic.css").read_text(encoding="utf-8")

    assert ".feed {\n  width: min(740px, 100%);" in css
    assert ".feed-card::before,\n.feed-card.is-active::before,\n.turn-summary::before,\n.status-row::before" in css
    assert "display: none !important;\n  content: none !important;" in css
    assert ".feed-card,\n.turn-summary,\n.status-row {\n  padding-left: 0;" in css
    assert ".topbar .control-btn" in css


def test_focused_dashboard_chrome_hides_secondary_noise():
    css = (_STATIC / "dynamic.css").read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")

    assert ".topbar #btn-control-report,\n.topbar #btn-copy-log,\n.topbar #btn-download-log" in css
    assert "visibility: hidden;" in css
    assert ".topbar:hover #btn-control-report" in css
    assert ".topbar:focus-within #btn-control-report" in css
    assert ".history-item {\n  gap: 0;" in css
    assert ".history-list .history-item:hover {\n  transform: none;\n  box-shadow: none;" in css
    assert ".history-dot {\n  width: 0;\n  min-width: 0;\n  opacity: 0;" in css
    assert ".history-dot.running,\n.history-dot.paused" in css
    assert ".sidebar-button {\n  min-height: 34px;\n  background: transparent;" in css
    assert "body[data-glass=\"on\"]:not(.widget-shell) .sidebar-button {\n  background: transparent !important;" in css
    assert ".sidebar-button span:last-child {\n  opacity: 0;" in css
    assert ".sidebar:hover .sidebar-button span:last-child" in css
    assert ".history-header,\n.history-group-name {\n  text-transform: none;" in css
    assert ".example-btn.suggestion-recent {\n  max-width: 230px;" in css
    assert ".example-btn.suggestion-recent .sug-ico {\n  display: none;" in css
    assert ".project-folder-path {\n  max-height: 0;\n  opacity: 0;" in css
    assert ".project-folder-trigger:hover .project-folder-path,\n.project-folder-trigger:focus-visible .project-folder-path" in css
    assert "#history-count,\n.history-header span:last-child,\n.history-group-count {\n  opacity: 0;" in css
    assert ".sidebar:hover .history-header span:last-child" in css
    assert ".sidebar:hover .history-group-count" in css
    assert ".provider-chips {\n  gap: 8px;\n  height: 0;" in css
    assert ".sidebar-foot:hover .provider-chips,\n.sidebar-foot:focus-within .provider-chips" in css
    assert "opacity: 1;\n  transform: translateY(0);" in css
    assert ".provider-chip span:not(.chip-dot) {\n  display: none;" in css
    assert ".provider-chip {\n  width: 8px;\n  height: 8px;" in css
    assert "more.textContent = expanded ? 'Show less' : 'Show more';" in js


def test_ai15_voice_widget_v2_drag_strip_hotkey():
    """AI-15: drag persists, live activity strip, Ctrl+Shift+Space toggles pill."""
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # drag-to-reposition: position key and localStorage save
    assert "ai-computer.vorb-position.v2" in js, "POS_KEY missing"
    assert "localStorage.setItem(POS_KEY" in js, "drag position not saved"

    # live activity strip: element in HTML, styles in CSS, logic in JS
    assert 'id="vcap-strip"' in html, "vcap-strip element missing from index.html"
    assert "vcap-strip" in css, "vcap-strip styles missing from style.css"
    assert "vcap-step" in css, "vcap-step styles missing from style.css"
    assert "stripLines" in js, "strip line buffer missing from app.js"
    assert "renderStrip" in js, "strip render helper missing"
    assert "stripEl.hidden = stripLines.length === 0" in js, "strip show logic missing"

    # hotkey toggles visibility (summon/dismiss), not just focus
    assert "root.hidden = !root.hidden" in js, "hotkey must toggle root.hidden"
    assert "e.ctrlKey && e.shiftKey && e.code === 'Space'" in js, "hotkey combo missing"


def test_ai23_thinking_budget_ui():
    """AI-23: thinking-budget select in HTML and thinking_budget in app.js payload."""
    html = STATIC_HTML.read_text(encoding="utf-8")
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    assert 'id="thinking-budget"' in html, "thinking-budget select missing from HTML"
    assert 'value="extended"' in html, "Extended option missing from thinking-budget select"
    assert "thinking_budget" in js, "thinking_budget missing from task payload in app.js"
    assert "usage_update" in js, "usage_update event handler missing from app.js"
    assert "usage-badge" in js, "usage-badge class missing from app.js"
    assert "usage-badge" in css, ".usage-badge CSS missing from style.css"


def test_stall_watchdog_prevents_silent_running_hang():
    """A running task must never sit on a silent 'running' with zero feedback.

    Guards the stall watchdog (arm on running / disarm otherwise, heartbeat-excluded
    progress notes) and the rate-limit retry message surfacing — the fix for the
    free-model queue/backoff hang where the dashboard showed only 'running'.
    """
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")

    # watchdog primitives
    assert "STALL_HINT_MS" in js, "stall watchdog threshold missing"
    assert "armStallWatch" in js and "disarmStallWatch" in js, "stall watchdog arm/disarm missing"
    assert "noteProgress" in js, "stall watchdog progress note missing"
    assert "this can take a moment on free models" in js, "stall reassurance message missing"

    # lifecycle: armed only while running, torn down on stop
    assert "if (key === 'running') armStallWatch();" in js, "watchdog not armed on running status"
    assert "else disarmStallWatch();" in js, "watchdog not disarmed off running status"
    assert "disarmStallWatch();" in js.split("const stopEverything")[1][:400], (
        "watchdog not disarmed in stopEverything"
    )

    # heartbeats must NOT count as progress (so a silent backoff still trips the hint)
    assert "event.type === 'status' && event.heartbeat)) noteProgress()" in js, (
        "heartbeat-excluded progress note missing"
    )

    # rate-limit backoff message is surfaced (not swallowed into a generic 'Thinking')
    assert "event.retrying && event.message" in js, "retry message branch missing"
    assert "setLiveStatus(String(event.message))" in js, "retry message not surfaced"


def test_codex_contextual_hero_and_folder_tree():
    """Codex-inspired: the idle hero greets the active project by name, and the
    sidebar history groups read as folder/project rows (glyph + nested chats)."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "dynamic.css").read_text(encoding="utf-8")

    # contextual hero
    assert "renderWelcomeHero" in js, "contextual hero helper missing"
    assert "What should we build in ${pathLeaf(folder)}?" in js, "project-named hero missing"
    assert "'What can I help you with?'" in js, "generic hero fallback missing"

    # folder-tree sidebar
    assert "history-group-lead" in js, "group lead wrapper missing"
    assert "history-group-icon" in js, "folder glyph missing"
    assert ".history-group-lead" in css, "group lead CSS missing"
    assert ".history-group-items {\n  padding-left: 7px;" in css, "tree indent missing"


def test_codex_files_changed_capstone():
    """Codex-style "N files changed" capstone: real edited paths captured from
    action_result events and rendered in the done-state with state tags."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # capture from real file actions (reads excluded), render + clear lifecycle
    assert "const editedFiles = new Map()" in js, "edited-files store missing"
    assert "noteEditedFile" in js and "_extractEditedPath" in js, "capture helpers missing"
    assert "!/read_file|view_file/i.test(_t)" in js, "reads must be excluded from changed files"
    assert "renderFilesChanged" in js, "capstone renderer missing"
    assert "editedFiles.clear();" in js, "edited files not cleared between tasks"
    assert "files changed" in js, "capstone label missing"

    # styling: monospace path rows + state tags
    assert ".files-changed-head" in css, "capstone header CSS missing"
    assert ".fc-path {" in css, "file path row CSS missing"
    assert ".fc-row.fc-new .fc-tag" in css and ".fc-row.fc-deleted .fc-tag" in css, "state tag CSS missing"


def test_codex_message_actions_and_contextual_suggestions():
    """Codex-style message footer (copy + thumbs wired to feedback) and the
    contextual idle state (recent-session quick-resume chips)."""
    js = (_STATIC / "app.js").read_text(encoding="utf-8", errors="replace")
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # message footer: copy + thumbs wired to the real feedback API, hover-revealed
    assert "attachMessageActions" in js, "message-actions helper missing"
    assert "finalizeAssistantDelta(finalReply, { taskId })" in js, "streamed reply not finalized"
    assert "attachMessageActions(appendMessage(finalReply, 'assistant')" in js, "actions not attached to fallback reply"
    assert "sendMessageFeedback" in js, "feedback sender missing"
    assert "/feedback`, 'POST', { rating" in js, "thumbs not wired to feedback API"
    assert ".message:hover .msg-actions" in css, "hover-reveal rule missing"
    assert ".msg-action-btn.chosen" in css, "chosen-thumb style missing"

    # contextual idle suggestions: recent sessions -> quick-resume chips
    assert "renderIdleSuggestions" in js, "idle-suggestions helper missing"
    assert "suggestion-recent" in js, "recent-chip class missing"
    assert "if (picks.length >= 2) break;" in js, "recent suggestions should stay capped"
    assert "if (picks.length < 2) return;" in js, "new-user fallback to examples missing"
    assert ".example-btn.suggestion-recent .sug-text" in css, "recent-chip text style missing"


def test_professional_motion_and_keyboard_focus():
    """Calm, professional motion (no flashy blur/throb) and a keyboard focus ring."""
    css = (_STATIC / "style.css").read_text(encoding="utf-8")

    # the feed entrance must not use a blur reveal or scale overshoot
    feed_kf = css[css.index("@keyframes feed-item-in"):css.index("@keyframes feed-item-in") + 200]
    assert "blur(" not in feed_kf, "feed entrance should not blur (flashy/janky)"
    assert "scale(" not in feed_kf, "feed entrance should not scale-overshoot"

    # the thinking dot breathes on opacity only (no size throb)
    think_kf = css[css.index("@keyframes think-dot"):css.index("@keyframes think-dot") + 160]
    assert "scale(" not in think_kf, "thinking dot should not throb in size"

    # keyboard-only focus ring exists and excludes text inputs
    assert ":where(button, a, [role=\"button\"], [tabindex], summary):focus-visible" in css, "focus ring missing"
    assert "outline: 2px solid var(--accent)" in css, "focus ring style missing"
    assert ":where(input, textarea, select):focus-visible { outline: none; }" in css, "inputs should opt out of the ring"
