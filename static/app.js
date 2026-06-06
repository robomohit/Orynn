  /* ---------------- tweak defaults (persisted via host protocol) ---------------- */
  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "theme": "dark",
    "accentHue": 176,
    "glow": 0,
    "density": 1,
    "grain": "off",
    "budgetPct": 44
  }/*EDITMODE-END*/;

  const tweakState = { ...TWEAK_DEFAULTS };

  const applyTweaks = () => {
    document.documentElement.setAttribute('data-theme', tweakState.theme);
    document.documentElement.style.setProperty('--accent-h', String(tweakState.accentHue));
    document.documentElement.style.setProperty('--glow', String(tweakState.glow));
    document.documentElement.style.setProperty('--density', String(tweakState.density));
    document.documentElement.setAttribute('data-grain', tweakState.grain);
    setBudget(tweakState.budgetPct);
  };

  const postHost = (edits) => {
    Object.assign(tweakState, edits);
    applyTweaks();
    try { window.parent.postMessage({ type: '__edit_mode_set_keys', edits }, '*'); } catch(_) {}
  };

  /* ---------------- token budget ---------------- */
  function setBudget(pct) {
    const val = Math.max(0, Math.min(100, Math.round(pct)));
    document.documentElement.style.setProperty('--budget-used', val + '%');
    const bv = document.getElementById('budget-val'); if (bv) bv.textContent = val + '%';
    const sbv = document.getElementById('sb-budget-val'); if (sbv) sbv.textContent = val + '%';
    const level = val >= 90 ? 'crit' : val >= 70 ? 'warn' : '';
    const bar = document.getElementById('budget-bar'); if (bar) bar.setAttribute('data-level', level);
    const sbb = document.getElementById('sb-budget'); if (sbb) sbb.setAttribute('data-level', level);
    const pillEl = document.getElementById('budget-pill');
    if (pillEl) {
      pillEl.classList.toggle('warn', level === 'warn');
      pillEl.classList.toggle('crit', level === 'crit');
    }
  }

  const makeChevronIcon = () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '12');
    svg.setAttribute('height', '12');
    svg.setAttribute('viewBox', '0 0 12 12');
    svg.setAttribute('fill', 'none');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M4 2.5L7.5 6L4 9.5');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    return svg;
  };

  const safeMermaidId = (value, fallback) => {
    const cleaned = String(value || '').replace(/[^a-zA-Z0-9]/g, '');
    return cleaned || fallback;
  };

  const safeMermaidLabel = (value) => {
    const compact = String(value || '')
      .replace(/[<>{}\[\]()"`'\\|;:\n\r]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    return compact.slice(0, 30) + (compact.length > 30 ? '...' : '');
  };

  /* ---------------- tweaks UI wiring ---------------- */
  const ACCENT_HUES = [220, 262, 300, 340, 10, 160, 40];

  function buildTweaks() {
    // swatches
    const sw = document.getElementById('t-swatches');
    sw.innerHTML = '';
    ACCENT_HUES.forEach(h => {
      const b = document.createElement('button');
      b.className = 't-swatch';
      b.style.background = `hsl(${h} 92% 58%)`;
      b.dataset.hue = h;
      b.onclick = () => {
        postHost({ accentHue: h });
        syncTweaks();
      };
      sw.appendChild(b);
    });
    const seg = (id, key, cast = v => v) => {
      document.querySelectorAll(`#${id} button`).forEach(btn => {
        btn.onclick = () => {
          postHost({ [key]: cast(btn.dataset.v) });
          syncTweaks();
        };
      });
    };
    seg('t-theme', 'theme');
    seg('t-density', 'density', v => parseFloat(v));
    seg('t-grain', 'grain');
    document.getElementById('t-glow').oninput = (e) => {
      postHost({ glow: parseFloat(e.target.value) });
      syncTweaks();
    };
    document.getElementById('t-budget').oninput = (e) => {
      postHost({ budgetPct: parseInt(e.target.value, 10) });
      syncTweaks();
    };
    document.querySelectorAll('#t-demo button').forEach(btn => {
      btn.onclick = () => {
        if (btn.dataset.v === 'on') playDemoStream();
        else if (btn.dataset.v === 'widgets') playWidgetGallery();
        else clearDemoStream();
      };
    });
    document.getElementById('tweaks-close').onclick = () => {
      document.getElementById('tweaks').classList.remove('show');
      try { window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*'); } catch(_) {}
    };
    document.getElementById('btn-tweaks').onclick = () => {
      document.getElementById('tweaks').classList.toggle('show');
    };
  }

  /* settings modal open/close */
  (function wireSettingsModal() {
    const overlay = document.getElementById('settings-overlay');
    const openBtn = document.getElementById('open-settings');
    const closeBtn = document.getElementById('settings-close');
    if (!overlay || !openBtn) return;
    const open = () => {
      overlay.classList.add('show');
      loadReadiness();
      loadTrustReport();
      loadCodingBackends();
    };
    const close = () => overlay.classList.remove('show');
    openBtn.onclick = open;
    if (closeBtn) closeBtn.onclick = close;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && overlay.classList.contains('show')) {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    }, true);
  })();

  /* ---------------- voice: dictation (STT) + read-aloud (TTS) ----------------
     Uses the browser's built-in Web Speech API — free, local, no API keys.
     Both controls feature-detect and hide themselves where unsupported. */
  let speakAgentReply = () => {};  // reassigned below if TTS is available
  (function wireVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const micBtn = document.getElementById('mic-btn');
    const readBtn = document.getElementById('read-aloud-toggle');
    const input = document.getElementById('input');

    // --- speech-to-text: click mic to dictate into the composer ---
    if (!SR || !micBtn || !input) {
      if (micBtn) micBtn.style.display = 'none';
    } else {
      let recognition = null;
      let listening = false;
      micBtn.onclick = () => {
        if (listening && recognition) { try { recognition.stop(); } catch (_) {} return; }
        recognition = new SR();
        recognition.lang = 'en-US';
        recognition.interimResults = true;
        recognition.continuous = false;
        const baseText = input.value.trim();
        recognition.onstart = () => { listening = true; micBtn.classList.add('active'); };
        recognition.onresult = (e) => {
          let transcript = '';
          for (let i = 0; i < e.results.length; i++) transcript += e.results[i][0].transcript;
          input.value = baseText ? `${baseText} ${transcript}` : transcript;
          input.dispatchEvent(new Event('input'));
        };
        recognition.onerror = () => {};
        recognition.onend = () => { listening = false; micBtn.classList.remove('active'); input.focus(); };
        try { recognition.start(); }
        catch (_) { listening = false; micBtn.classList.remove('active'); }
      };
    }

    // --- text-to-speech: read agent replies aloud when toggled on ---
    const synth = window.speechSynthesis;
    if (!synth || !readBtn) {
      if (readBtn) readBtn.style.display = 'none';
    } else {
      let readAloud = localStorage.getItem('ai-computer.read-aloud') === '1';
      readBtn.classList.toggle('active', readAloud);
      readBtn.onclick = () => {
        readAloud = !readAloud;
        localStorage.setItem('ai-computer.read-aloud', readAloud ? '1' : '0');
        readBtn.classList.toggle('active', readAloud);
        if (!readAloud) synth.cancel();
      };
      speakAgentReply = (text) => {
        if (!readAloud || !text) return;
        // Strip markdown so the voice doesn't read backticks/asterisks/fences.
        const plain = String(text)
          .replace(/```[\s\S]*?```/g, ' (code block) ')
          .replace(/`([^`]+)`/g, '$1')
          .replace(/[*_#>`]/g, '')
          .replace(/\s+/g, ' ')
          .trim();
        if (!plain) return;
        synth.cancel();
        const utterance = new SpeechSynthesisUtterance(plain.slice(0, 4000));
        utterance.lang = 'en-US';
        synth.speak(utterance);
      };
    }
  })();

  function syncTweaks() {
    document.getElementById('t-accent-val').textContent = tweakState.accentHue + '°';
    document.getElementById('t-glow-val').textContent = Number(tweakState.glow).toFixed(1) + '×';
    document.getElementById('t-budget-val').textContent = tweakState.budgetPct + '%';
    document.getElementById('t-glow').value = tweakState.glow;
    document.getElementById('t-budget').value = tweakState.budgetPct;

    document.querySelectorAll('#t-theme button').forEach(b => b.classList.toggle('on', b.dataset.v === tweakState.theme));
    document.querySelectorAll('#t-density button').forEach(b => b.classList.toggle('on', parseFloat(b.dataset.v) === tweakState.density));
    document.querySelectorAll('#t-grain button').forEach(b => b.classList.toggle('on', b.dataset.v === tweakState.grain));
    document.querySelectorAll('#t-swatches .t-swatch').forEach(b => b.classList.toggle('on', parseInt(b.dataset.hue, 10) === tweakState.accentHue));
  }

  // Host protocol
  function _hostMessageHandler(e) {
    const data = e?.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === '__activate_edit_mode') document.getElementById('tweaks').classList.add('show');
    if (data.type === '__deactivate_edit_mode') document.getElementById('tweaks').classList.remove('show');
  }
  window.removeEventListener('message', _hostMessageHandler);
  window.addEventListener('message', _hostMessageHandler);
  try { window.parent.postMessage({ type: '__edit_mode_available' }, window.location.origin); } catch(_) {}

  /* ================================================================
     ORIGINAL DASHBOARD LOGIC — preserved, minimal edits
     (adapted for: collapsible grid-row transitions, active-card glow)
     ================================================================ */
  const $ = (id) => document.getElementById(id);

  let KEY = '';
  let keyReady = Promise.resolve('');

  let task = null;
  let currentViewedTask = null;
  let sse = null;
  let streamCursor = 0;
  let reconnectAttempts = 0;
  let reconnectTimer = null;
  let streamClosedManually = false;
  let isPaused = false;
  let timer = null;
  let startTime = 0;

  // Stall watchdog: a running task must never look frozen. If no meaningful
  // event arrives for STALL_HINT_MS (e.g. free models queued/rate-limited during
  // a silent backoff), surface a calm "still working" status so the UI never
  // sits on a dead "running" with zero feedback.
  const STALL_HINT_MS = 15000;
  let lastProgressTs = 0;
  let stallWatch = null;
  let stallActive = false;

  let historyItems = [];
  let activeHistoryItem = null;
  const historyExpandedGroups = new Set();   // folder keys whose "see more" is expanded
  const HISTORY_GROUP_LIMIT = 5;             // show 5 most recent per folder before "See more"
  let suppressHistoryReflow = false;         // batch flag: skip per-item reflow during bulk load
  let activePlanCard = null;
  let liveStatusCard = null;
  let liveStatusMessage = '';
  let capsuleControlLayer = '';
  let currentControlLayer = '';
  let currentControlReason = '';
  let currentControlTarget = '';
  let planSubtasks = [];
  let currentSubtaskIdx = 0;
  let subtaskEls = {};
  let screenshots = [];
  let currentMode = 'coding';
  let currentBackgroundMode = true;
  let currentStatus = 'ready';
  let currentIsolatedApp = '';
  let modelSelectionTouched = false;
  let desktopAccessResolver = null;
  let lastActionId = null;
  let terminalStateKey = null;
  const actionCards = {};
  let lastActiveCard = null;
  let activeTurnSummary = null; // Phase C1: groups tool actions between reasoning events

  const _actionTypeLabel = (type = '') => {
    if (/run_command|bash|terminal/i.test(type)) return 'command';
    if (/read_file|write_file|edit_file|str_replace|create_file|delete_file|undo_edit|view_file/i.test(type)) return 'file';
    if (/^browser_|browser_/i.test(type)) return 'browser';
    if (/web_search|^search$/i.test(type)) return 'search';
    if (/computer|click|type|scroll|screenshot|key/i.test(type)) return 'action';
    return 'step';
  };

  // Phase C1: a "Step N" reasoning note is just an action announcement —
  // the turn summary already covers it. Skipping it also prevents the
  // turn from being fragmented into one-tool-per-summary.
  const _isStepAnnouncement = (e) => /^step\s*\d+$/i.test(String(e && e.stage || '').trim());

  const _turnSummaryText = (types, live) => {
    const buckets = {};
    types.forEach(t => { buckets[t] = (buckets[t] || 0) + 1; });
    const parts = Object.entries(buckets).map(([t, n]) => {
      if (t === 'command') return live ? `Running ${n > 1 ? n + ' commands' : 'command'}…` : `Ran ${n} command${n > 1 ? 's' : ''}`;
      if (t === 'file') return live ? `Editing ${n > 1 ? n + ' files' : 'file'}…` : `Edited ${n} file${n > 1 ? 's' : ''}`;
      if (t === 'search') return live ? 'Searching…' : `Searched ${n} time${n > 1 ? 's' : ''}`;
      if (t === 'browser') return live ? 'Browsing…' : `${n} browser action${n > 1 ? 's' : ''}`;
      if (t === 'action') return live ? 'Acting…' : `${n} action${n > 1 ? 's' : ''}`;
      return live ? 'Working…' : `${n} step${n > 1 ? 's' : ''}`;
    });
    return parts.join(', ') || (live ? 'Working…' : `${types.length} step${types.length !== 1 ? 's' : ''}`);
  };

  // Phase C2: build a step-timeline inside the turn-summary body on first expand.
  // Raw tool cards are hidden; one icon-gutter row per step is shown instead.
  const _STEP_ICONS = { command: '⟩', file: '✎', search: '◎', browser: '⊞', action: '⊙', step: '·' };
  const _buildTurnTimeline = (body, steps) => {
    if (!steps.length) return;
    Array.from(body.children).forEach(c => { if (!c.classList.contains('turn-timeline')) c.style.display = 'none'; });
    const tl = document.createElement('div');
    tl.className = 'turn-timeline';
    steps.forEach(s => {
      const row = document.createElement('div');
      row.className = 'turn-step';
      const icon = document.createElement('span');
      icon.className = 'turn-step-icon';
      icon.textContent = _STEP_ICONS[_actionTypeLabel(s.actionType)] || _STEP_ICONS.step;
      const content = document.createElement('div');
      content.className = 'turn-step-content';
      const lbl = document.createElement('span');
      lbl.className = 'turn-step-label';
      const finalState = s.stateEl ? s.stateEl.textContent : '';
      lbl.textContent = (finalState && finalState !== 'Running') ? `${s.label} — ${finalState}` : s.label;
      content.appendChild(lbl);
      const sub = s.subtitleEl ? s.subtitleEl.textContent.trim() : s.summary;
      if (sub) {
        const subEl = document.createElement('span');
        subEl.className = 'turn-step-sub';
        subEl.textContent = sub;
        content.appendChild(subEl);
      }
      const trace = s.traceEl ? (s.traceEl.dataset.traceSummary || s.traceEl.textContent.trim()) : '';
      if (trace) {
        const traceEl = document.createElement('span');
        traceEl.className = 'turn-step-trace';
        traceEl.textContent = trace;
        content.appendChild(traceEl);
      }
      const rawOutput = s.outputEl ? s.outputEl.textContent.trim() : '';
      if (rawOutput) {
        const wrap = document.createElement('div');
        wrap.className = 'turn-step-output-wrap';
        const out = document.createElement('pre');
        out.className = 'turn-step-output';
        out.textContent = rawOutput.length > 2000 ? rawOutput.slice(0, 2000) + '\n…' : rawOutput;
        const copyBtn = document.createElement('button');
        copyBtn.className = 'ts-copy-btn';
        copyBtn.type = 'button';
        copyBtn.title = 'Copy output';
        copyBtn.textContent = 'Copy';
        copyBtn.onclick = () => {
          navigator.clipboard.writeText(out.textContent).catch(() => {});
          copyBtn.textContent = 'Copied!';
          setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
        };
        wrap.appendChild(out);
        wrap.appendChild(copyBtn);
        content.appendChild(wrap);
      }
      row.appendChild(icon);
      row.appendChild(content);
      tl.appendChild(row);
    });
    body.appendChild(tl);
  };

  const startTurnSummary = () => {
    if (activeTurnSummary) return activeTurnSummary;
    removeWelcome();
    const card = document.createElement('div');
    card.className = 'turn-summary';
    const headerEl = document.createElement('div');
    headerEl.className = 'turn-summary-head';
    const textSpan = document.createElement('span');
    textSpan.className = 'turn-summary-text';
    textSpan.textContent = 'Working…';
    const chevronEl = document.createElement('span');
    chevronEl.className = 'turn-summary-chevron';
    chevronEl.textContent = '›';
    headerEl.appendChild(textSpan);
    headerEl.appendChild(chevronEl);
    const body = document.createElement('div');
    body.className = 'turn-summary-body collapsed';
    const steps = []; // Phase C2: step data for timeline
    headerEl.addEventListener('click', () => {
      const isCollapsed = body.classList.toggle('collapsed');
      card.classList.toggle('expanded', !isCollapsed);
      if (!isCollapsed && !body.querySelector('.turn-timeline')) _buildTurnTimeline(body, steps); // Phase C2
    });
    card.appendChild(headerEl);
    card.appendChild(body);
    $('feed').appendChild(card);
    scrollFeed();
    activeTurnSummary = { card, body, textSpan, types: [], steps }; // Phase C2: steps
    return activeTurnSummary;
  };

  const finalizeTurnSummary = () => {
    if (!activeTurnSummary) return;
    const { textSpan, types } = activeTurnSummary;
    textSpan.textContent = _turnSummaryText(types, false) + ' ›';
    activeTurnSummary = null;
  };

  const WELCOME_HTML = document.getElementById('welcome').outerHTML;
  const PROJECT_FOLDER_STORAGE_KEY = 'ai-computer.project-folder.v1';
  const projectFolderState = {
    selectedPath: '',
    browsingPath: '',
    shortcuts: []
  };

  const pathLeaf = (value = '') => {
    const cleaned = String(value || '').replace(/[\\/]+$/, '');
    if (!cleaned) return 'General mode';
    const bits = cleaned.split(/[\\/]/).filter(Boolean);
    return bits[bits.length - 1] || cleaned;
  };

  const shortcutLabel = (id = '', fallback = '') => {
    const labels = { home: 'Home', desktop: 'Desktop', downloads: 'Downloads', repo: 'Current Repo' };
    return labels[id] || fallback || humanize(id || 'folder');
  };

  const persistProjectFolder = (value) => {
    try {
      if (value) localStorage.setItem(PROJECT_FOLDER_STORAGE_KEY, value);
      else localStorage.removeItem(PROJECT_FOLDER_STORAGE_KEY);
    } catch (_) {}
  };

  const storedProjectFolder = () => {
    try { return localStorage.getItem(PROJECT_FOLDER_STORAGE_KEY) || ''; }
    catch (_) { return ''; }
  };

  // Codex-style: the idle hero greets the active project by name.
  const renderWelcomeHero = () => {
    const h = document.querySelector('#welcome h3');
    if (!h) return;
    const folder = projectFolderState.selectedPath;
    h.textContent = folder ? `What should we build in ${pathLeaf(folder)}?` : 'What can I help you with?';
  };

  const renderProjectFolderSummary = () => {
    const selected = projectFolderState.selectedPath;
    $('project-folder-name').textContent = selected ? pathLeaf(selected) : 'General mode';
    $('project-folder-path').textContent = selected ? selected : 'Desktop + Home access';
    renderWelcomeHero();
    if (!task) setTaskTitle();
  };

  const setProjectFolder = (value, { persist = true } = {}) => {
    projectFolderState.selectedPath = value || '';
    if (persist) persistProjectFolder(projectFolderState.selectedPath);
    renderProjectFolderSummary();
  };

  // ---- Lightweight folder picker (Codex-style dropdown, not a file explorer) ----
  let folderMenuOpen = false;

  const loadFolderShortcuts = async () => {
    await keyReady;
    try {
      const payload = await api('/api/browse-directory');   // we only want .shortcuts
      projectFolderState.shortcuts = payload.shortcuts || [];
    } catch (_) { /* keep whatever we already have */ }
    return projectFolderState.shortcuts;
  };

  const nativePickFolder = async () => {
    try {
      if (window.pywebview?.api?.pick_folder) {
        const res = await window.pywebview.api.pick_folder();
        return res && res.path ? res.path : '';
      }
    } catch (_) {}
    return '';
  };

  const renderFolderMenu = () => {
    const list = $('folder-menu-items');
    if (!list) return;
    list.innerHTML = '';
    const cur = projectFolderState.selectedPath;
    const addItem = ({ label, sub, path = '', isGeneral = false, isBrowse = false }) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'folder-menu-item' + (isBrowse ? ' is-browse' : '');
      b.setAttribute('role', 'menuitem');
      const active = isBrowse ? false : (isGeneral ? !cur : (!!path && cur === path));
      if (active) b.classList.add('active');
      const check = document.createElement('span');
      check.className = 'fm-check';
      check.setAttribute('aria-hidden', 'true');
      check.textContent = active ? '✓' : '';
      const txt = document.createElement('span');
      txt.className = 'fm-text';
      const lbl = document.createElement('span');
      lbl.className = 'fm-label';
      lbl.textContent = label;
      txt.appendChild(lbl);
      if (sub) {
        const s = document.createElement('span');
        s.className = 'fm-sub';
        s.textContent = sub;
        s.title = sub;
        txt.appendChild(s);
      }
      b.append(check, txt);
      b.addEventListener('click', async () => {
        closeFolderMenu();
        if (isBrowse) { const p = await nativePickFolder(); if (p) setProjectFolder(p); return; }
        setProjectFolder(isGeneral ? '' : path);
      });
      list.appendChild(b);
    };
    addItem({ label: 'General mode', sub: 'Desktop + Home access', isGeneral: true });
    projectFolderState.shortcuts.forEach((s) => addItem({ label: shortcutLabel(s.id, s.label), sub: s.path, path: s.path }));
    if (window.pywebview?.api?.pick_folder) addItem({ label: 'Browse for a folder…', isBrowse: true });
  };

  const positionFolderMenu = () => {
    const menu = $('project-folder-menu');
    const trig = $('project-folder-trigger');
    if (!menu || !trig) return;
    const r = trig.getBoundingClientRect();
    const width = Math.max(240, Math.round(r.width));
    menu.style.minWidth = `${width}px`;
    menu.style.left = `${Math.round(Math.min(r.left, window.innerWidth - width - 12))}px`;
    menu.style.top = `${Math.round(r.bottom + 6)}px`;
  };

  const openFolderMenu = async () => {
    const menu = $('project-folder-menu');
    if (!menu) return;
    renderFolderMenu();              // render immediately from cached shortcuts
    menu.hidden = false;
    positionFolderMenu();
    folderMenuOpen = true;
    $('project-folder-trigger')?.setAttribute('aria-expanded', 'true');
    await loadFolderShortcuts();     // refresh shortcuts, then re-render if still open
    if (folderMenuOpen) renderFolderMenu();
  };

  const closeFolderMenu = () => {
    const menu = $('project-folder-menu');
    if (menu) menu.hidden = true;
    folderMenuOpen = false;
    $('project-folder-trigger')?.setAttribute('aria-expanded', 'false');
  };

  const toggleFolderMenu = () => { folderMenuOpen ? closeFolderMenu() : openFolderMenu(); };

  const api = (path, method = 'GET', body = null) => fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { 'Authorization': `Bearer ${KEY || ''}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : null
  }).then(async (response) => {
    if (response.ok) return response.json();
    const err = await response.json().catch(() => ({}));
    throw { status: response.status, detail: err.detail || response.statusText || 'Unknown error' };
  });

  const safeDate = (value) => {
    const parsed = typeof value === 'number' ? value : Date.parse(value || '');
    return Number.isNaN(parsed) ? null : parsed;
  };
  const relTime = (value) => {
    const ts = safeDate(value); if (ts === null) return '';
    const diff = Math.floor((Date.now() - ts) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  };
  // Compact age for the session list — Codex-style "16h / 3d / 2w / 1mo".
  const relTimeShort = (value) => {
    const ts = safeDate(value); if (ts === null) return '';
    const d = Math.floor((Date.now() - ts) / 1000);
    if (d < 60) return 'now';
    if (d < 3600) return `${Math.floor(d / 60)}m`;
    if (d < 86400) return `${Math.floor(d / 3600)}h`;
    if (d < 604800) return `${Math.floor(d / 86400)}d`;
    if (d < 2592000) return `${Math.floor(d / 604800)}w`;
    if (d < 31536000) return `${Math.floor(d / 2592000)}mo`;
    return `${Math.floor(d / 31536000)}y`;
  };
  // Turn a raw goal into a short, clean session title (strip context banners,
  // first line only, capped + capitalized) — so the list reads like Codex's.
  const historyTitle = (goal) => {
    let g = String(goal || '').trim();
    g = g.replace(/^\s*(?:\[[^\]]*\]\s*)+/, '').trim();      // drop [Clipboard]/[Attached]/[Linked] prefixes
    g = (g.split(/\r?\n/).find((l) => l.trim()) || g).trim(); // first non-empty line
    g = g.replace(/\s+/g, ' ');
    if (!g) return 'Untitled task';
    if (g.length > 52) g = g.slice(0, 51).replace(/\s+\S*$/, '').trimEnd() + '…';
    return g.charAt(0).toUpperCase() + g.slice(1);
  };
  const humanize = (value = '') => value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const truncate = (value = '', limit = 500) => value.length > limit ? `${value.slice(0, limit)}\n…` : value;
  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const isTerminalStatus = (value = '') => /done|complete|failed|error|cancelled/i.test(value);

  const toast = (message, kind = 'info', ttl = 3200) => {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = message;
    $('toast-stack').appendChild(el);
    if (ttl > 0) setTimeout(() => el.remove(), ttl);
  };

  const markFeedActive = () => {
    const feed = $('feed');
    if (feed) feed.classList.add('has-events');
    // Drive idle→active chrome (centered hero collapses, topbar/controls show).
    document.body.classList.add('task-active');
  };

  const removeWelcome = () => {
    const w = $('welcome');
    if (w) w.remove();
    markFeedActive();
  };

  const bindExamples = () => {
    document.querySelectorAll('.example-btn').forEach((btn) => {
      btn.onclick = () => {
        $('input').value = btn.dataset.example || '';
        $('input').focus();
        autoGrow(); updateCharCount();
      };
    });
  };

  // Codex-style contextual idle state: returning users see quick-resume chips of
  // their recent sessions; new users keep the capability examples.
  const renderIdleSuggestions = () => {
    const grid = document.querySelector('#welcome .example-grid');
    if (!grid) return;  // not on the idle screen
    const recents = [...historyItems]
      .filter((it) => it.dataset.taskId && it.querySelector('.history-goal'))
      .sort((a, b) => (Number(b.dataset.created) || 0) - (Number(a.dataset.created) || 0));
    const seen = new Set();
    const picks = [];
    for (const it of recents) {
      const goal = (it.querySelector('.history-goal').textContent || '').trim();
      const full = (it.title || '').split('\n')[0].trim() || goal;
      const key = goal.toLowerCase();
      if (!goal || seen.has(key)) continue;
      seen.add(key);
      picks.push({ goal, full });
      if (picks.length >= 2) break;
    }
    if (picks.length < 2) return;  // too little history → keep the capability examples
    grid.innerHTML = '';
    grid.classList.add('suggestions-recent');
    picks.forEach((p) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'example-btn suggestion-recent';
      btn.dataset.example = p.full;
      btn.title = p.full;
      const ico = document.createElement('span');
      ico.className = 'sug-ico';
      ico.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 4v4h4"/></svg>';
      const txt = document.createElement('span');
      txt.className = 'sug-text';
      txt.textContent = p.goal;
      btn.append(ico, txt);
      grid.appendChild(btn);
    });
    bindExamples();
  };

  let _scrollPending = false;
  const scrollFeed = () => {
    if (_scrollPending) return;
    _scrollPending = true;
    requestAnimationFrame(() => { _scrollPending = false; const fs = $('feed-scroll'); if (fs) fs.scrollTop = fs.scrollHeight; });
  };

  const safeMarkdownHref = (href = '') => {
    const trimmed = String(href || '').trim();
    if (!/^(https?:\/\/|mailto:)/i.test(trimmed)) return '';
    try {
      const parsed = new URL(trimmed);
      return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (_) {
      return '';
    }
  };

  const sanitizeRenderedMarkdown = (html = '') => {
    if (typeof DOMParser === 'undefined') return String(html || '');
    const allowedTags = new Set(['A', 'BR', 'CODE', 'EM', 'LI', 'P', 'PRE', 'STRONG', 'UL']);
    const allowedClasses = {
      A: new Set(['md-link']),
      CODE: new Set(['md-code']),
      PRE: new Set(['md-pre']),
      UL: new Set(['md-ul']),
    };
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
    const root = doc.body.firstElementChild;
    const walk = (node) => {
      Array.from(node.childNodes).forEach((child) => {
        if (child.nodeType === 3) return;
        if (child.nodeType !== 1) {
          child.remove();
          return;
        }
        const tag = child.tagName;
        if (!allowedTags.has(tag)) {
          child.replaceWith(doc.createTextNode(child.textContent || ''));
          return;
        }
        const originalHref = child.getAttribute('href') || '';
        const originalClasses = Array.from(child.classList || []);
        Array.from(child.attributes).forEach((attr) => child.removeAttribute(attr.name));
        const classAllow = allowedClasses[tag];
        if (classAllow) {
          const kept = originalClasses.filter((name) => classAllow.has(name));
          if (kept.length) child.className = kept.join(' ');
        }
        if (tag === 'A') {
          const safeHref = safeMarkdownHref(originalHref);
          if (!safeHref) {
            child.replaceWith(doc.createTextNode(child.textContent || ''));
            return;
          }
          child.setAttribute('href', safeHref);
          child.setAttribute('target', '_blank');
          child.setAttribute('rel', 'noopener noreferrer');
        }
        walk(child);
      });
    };
    walk(root);
    return root.innerHTML;
  };

  // Minimal, injection-safe markdown renderer for agent replies.
  // HTML is escaped FIRST; only known-safe tags are then inserted.
  const renderMarkdown = (raw) => {
    const esc = escapeHtml;
    let text = String(raw || '');
    const blocks = [];
    const stash = (html, kind) => {
      blocks.push(html);
      return `@@AIC@${kind}${blocks.length - 1}@@AIC@`;
    };
    // 1. fenced code blocks ```lang\n…\n``` — pull out, escape, stash
    text = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_, code) => {
      return stash('<pre class="md-pre"><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>', 'B');
    });
    // 2. escape everything else
    text = esc(text);
    // 3. inline code `…` (content already escaped)
    text = text.replace(/`([^`\n]+)`/g, (_, c) => stash('<code class="md-code">' + c + '</code>', 'I'));
    // 4. bold then italic
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
      const safeHref = safeMarkdownHref(href);
      if (!safeHref) return label;
      return `<a class="md-link" href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    // 5. bullet lists — group consecutive "- "/"* " lines
    text = text.replace(/(?:^|\n)((?:[-*] .+(?:\n|$))+)/g, (_, list) => {
      const items = list.trim().split(/\n/).map((l) => '<li>' + l.replace(/^[-*] /, '') + '</li>').join('');
      return '\n' + stash('<ul class="md-ul">' + items + '</ul>', 'U');
    });
    // 6. paragraphs + line breaks
    text = text.split(/\n{2,}/).map((p) => {
      const t = p.trim();
      if (!t) return '';
      if (/^@@AIC@[BIU]\d+@@AIC@$/.test(t)) return t;
      return '<p>' + t.replace(/\n/g, '<br>') + '</p>';
    }).join('');
    // 7. restore stashed blocks/lists
    text = text.replace(/@@AIC@[BIU](\d+)@@AIC@/g, (_, i) => blocks[+i] || '');
    return sanitizeRenderedMarkdown(text);
  };

  const appendMessage = (text, kind = 'system-note') => {
    removeWelcome();
    const el = document.createElement('div');
    el.className = `message ${kind}`;
    if (kind === 'assistant') {
      el.innerHTML = renderMarkdown(text);
      speakAgentReply(text);  // read aloud if the user enabled it (no-op otherwise)
    } else {
      el.textContent = text;
    }
    $('feed').appendChild(el);
    scrollFeed();
    return el;
  };

  // Codex-style message footer: hover-revealed Copy + thumbs, attached to a reply.
  const _MSG_ICONS = {
    copy: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    up: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v11"/><path d="M18 21H7V10l5-7a2 2 0 0 1 2 1.5L13 9h5a2 2 0 0 1 2 2.3l-1 7A2 2 0 0 1 17 21z"/></svg>',
    down: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V3"/><path d="M6 3h11v11l-5 7a2 2 0 0 1-2-1.5L11 15H6a2 2 0 0 1-2-2.3l1-7A2 2 0 0 1 7 3z"/></svg>',
  };
  const attachMessageActions = (el, { text = '', taskId = '' } = {}) => {
    if (!el || el.querySelector(':scope > .msg-actions')) return;
    const bar = document.createElement('div');
    bar.className = 'msg-actions';
    const mk = (icon, label, onClick) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'msg-action-btn';
      b.title = label;
      b.setAttribute('aria-label', label);
      b.innerHTML = _MSG_ICONS[icon];
      b.addEventListener('click', onClick);
      return b;
    };
    const copy = mk('copy', 'Copy', () => {
      navigator.clipboard.writeText(text).then(() => {
        copy.classList.add('done');
        setTimeout(() => copy.classList.remove('done'), 1200);
      }).catch(() => {});
    });
    bar.appendChild(copy);
    if (taskId) {
      const up = mk('up', 'Good response', () => sendMessageFeedback(taskId, 'up', up, down));
      const down = mk('down', 'Needs work', () => sendMessageFeedback(taskId, 'down', down, up));
      bar.append(up, down);
    }
    el.appendChild(bar);
  };
  const sendMessageFeedback = (taskId, rating, btn, other) => {
    if (btn.classList.contains('chosen')) return;
    btn.classList.add('chosen');
    other?.classList.remove('chosen');
    api(`/api/tasks/${encodeURIComponent(taskId)}/feedback`, 'POST', { rating, note: '' })
      .then(() => toast(rating === 'up' ? 'Thanks for the feedback.' : 'Noted — thanks for the feedback.', 'ok', 1800))
      .catch(() => { btn.classList.remove('chosen'); toast('Could not send feedback.', 'warn', 2000); });
  };

  const setActiveCard = (card) => {
    if (lastActiveCard && lastActiveCard !== card) lastActiveCard.classList.remove('is-active');
    if (card) card.classList.add('is-active');
    lastActiveCard = card || null;
  };

  const createFeedCard = (className = '') => {
    removeWelcome();
    const card = document.createElement('div');
    card.className = `feed-card ${className}`.trim();
    $('feed').appendChild(card);
    setActiveCard(card);
    scrollFeed();
    return card;
  };

  // ---- stall watchdog (never let a running task look frozen) ----
  const noteProgress = () => { lastProgressTs = Date.now(); stallActive = false; };
  const armStallWatch = () => {
    noteProgress();
    if (stallWatch) clearInterval(stallWatch);
    stallWatch = setInterval(() => {
      if (currentStatus !== 'running') return;
      if (stallActive) return;  // hint already shown; don't re-spam until real progress
      if (Date.now() - lastProgressTs < STALL_HINT_MS) return;
      stallActive = true;
      setLiveStatus('Still working — this can take a moment on free models.');
    }, 3000);
  };
  const disarmStallWatch = () => {
    if (stallWatch) { clearInterval(stallWatch); stallWatch = null; }
    stallActive = false;
  };

  const setStatus = (status) => {
    const map = { ready: 'Ready', queued: 'Queued', pending: 'Pending', running: 'Running', paused: 'Paused', complete: 'Complete', failed: 'Failed', error: 'Error', cancelled: 'Cancelled' };
    const key = (status || 'ready').toLowerCase();
    currentStatus = key;
    if (key === 'running') armStallWatch();
    else disarmStallWatch();
    const sb = $('sb-status');
    if (sb) {
      const statusClass = /^[a-z0-9_-]+$/.test(key) ? key : 'ready';
      sb.className = `sb-item sb-status sb-status-${statusClass}`;
      const statusDot = document.createElement('span');
      statusDot.className = 'sb-dot';
      const statusText = document.createElement('span');
      statusText.className = 'sb-val';
      statusText.textContent = map[key] || humanize(key);
      sb.replaceChildren(statusDot, statusText);
    }
    const dotCls = { queued: 'running', pending: 'running', running: 'running', paused: 'paused', complete: 'done', failed: 'failed', error: 'failed' };
    const dot = $('topbar-dot');
    if (dot) dot.className = 'topbar-dot' + (dotCls[key] ? ' ' + dotCls[key] : '');
  };

  const isDesktopMode = (mode = currentMode) => mode === 'computer' || mode === 'computer_isolated';

  const desktopModeLabel = (mode = currentMode) => mode === 'computer_isolated' ? 'Isolated App control' : 'Desktop control';

  const controlLayerClass = (layer = '') => {
    const text = String(layer || '').toLowerCase();
    if (text.includes('uia')) return 'uia';
    if (text.includes('screenshot') || text.includes('ocr') || text.includes('vision')) return 'vision';
    if (text.includes('electron')) return 'electron';
    if (text.includes('miss') || text.includes('fail')) return 'miss';
    return '';
  };

  const renderDesktopControlDetail = (mode = currentMode, isolatedApp = currentIsolatedApp) => {
    const detail = $('desktop-control-detail');
    if (!detail) return;
    const scope = mode === 'computer_isolated'
      ? `${isolatedApp || 'Selected app'} only`
      : 'Full desktop view and control';
    const layer = currentControlLayer ? ` - ${currentControlLayer}` : '';
    const reason = currentControlReason ? ` - ${currentControlReason}` : '';
    detail.textContent = `${desktopModeLabel(mode)} - ${scope}${layer}${reason}`;
  };

  const setControlSurface = ({ layer = '', reason = '', target = '', phase = '' } = {}) => {
    currentControlLayer = String(layer || '').trim();
    currentControlReason = String(reason || '').trim();
    currentControlTarget = String(target || '').trim();
    capsuleControlLayer = currentControlLayer;
    const pill = $('topbar-control');
    const label = $('topbar-control-layer');
    if (pill && label) {
      const hasLayer = Boolean(currentControlLayer);
      pill.hidden = !hasLayer;
      pill.className = `topbar-control ${controlLayerClass(currentControlLayer)}`.trim();
      label.textContent = currentControlLayer || 'Control';
      pill.title = [currentControlTarget, currentControlReason, phase].filter(Boolean).join(' - ');
    }
    renderDesktopControlDetail();
  };

  const setControlProfileSurface = (profile = {}) => {
    if (!profile || typeof profile !== 'object') return;
    const layer = String(profile.primary_route || '').trim();
    if (!layer) return;
    const count = Number(profile.uia_control_count || 0);
    const ocr = profile.ocr_available ? 'OCR ready' : 'OCR unavailable';
    const electron = profile.electron_hint && typeof profile.electron_hint === 'object';
    const reason = electron
      ? 'Electron app may need renderer accessibility unlock'
      : `${count} UIA controls visible - ${ocr}`;
    setControlSurface({
      layer,
      reason,
      target: profile.target_app || (profile.isolated ? 'Selected app' : 'Desktop'),
      phase: 'Ready',
    });
  };

  const setDesktopSessionActive = (active, mode = currentMode, isolatedApp = currentIsolatedApp) => {
    const banner = $('desktop-control-banner');
    if (!banner) return;
    banner.classList.toggle('show', !!active && isDesktopMode(mode));
    $('desktop-control-title').textContent = active ? 'Orynn is using your computer' : 'Desktop control inactive';
    renderDesktopControlDetail(mode, isolatedApp);
  };

  const requestDesktopAccess = ({ mode, isolatedApp }) => new Promise((resolve) => {
    const overlay = $('desktop-access');
    const list = $('desktop-access-list');
    const fullDesktop = mode === 'computer';
    $('desktop-access-title').textContent = fullDesktop ? 'Allow full desktop control?' : `Allow control of ${isolatedApp || 'selected app'}?`;
    $('desktop-access-reason').textContent = fullDesktop
      ? 'Orynn will be able to see the desktop and use mouse, keyboard, screenshots, and windows during this task.'
      : `Orynn will lock control to ${isolatedApp || 'the selected app'} where possible and use screenshots, focus, and keyboard input for this task.`;
    const rows = fullDesktop
      ? [
          ['Desktop screen', 'Visible to Orynn during this session', 'Full control'],
          ['Mouse and keyboard', 'Can click, type, and use shortcuts while the task runs', 'Full control'],
          ['Other windows', 'May be visible in screenshots unless you use isolated mode', 'Visible']
        ]
      : [
          [isolatedApp || 'Target app', 'Primary app Orynn may view and control', 'Full control'],
          ['Other windows', 'Not targeted; may only appear if Windows focus changes', 'Limited'],
          ['Stop control', 'Pause or cancel from the top bar at any time', 'Available']
        ];
    list.innerHTML = '';
    rows.forEach(([title, copy, badge]) => {
      const row = document.createElement('div');
      row.className = 'desktop-access-row';
      const text = document.createElement('div');
      const strong = document.createElement('strong');
      strong.textContent = title;
      const span = document.createElement('span');
      span.textContent = copy;
      text.appendChild(strong);
      text.appendChild(span);
      const badgeEl = document.createElement('div');
      badgeEl.className = 'desktop-access-badge';
      badgeEl.textContent = badge;
      row.appendChild(text);
      row.appendChild(badgeEl);
      list.appendChild(row);
    });
    desktopAccessResolver = resolve;
    overlay.classList.add('show');
  });

  const openSettingsFromPreflight = () => {
    $('readiness-preflight')?.classList.remove('show');
    $('settings-overlay')?.classList.add('show');
    loadReadiness();
  };

  const requestReadinessPreflight = (preflight) => new Promise((resolve) => {
    const overlay = $('readiness-preflight');
    const list = $('readiness-preflight-list');
    const issues = Array.isArray(preflight) ? preflight : (preflight?.issues || []);
    if (!overlay || !list || !issues.length) { resolve(true); return; }
    const blocked = typeof preflight?.blocked === 'boolean'
      ? preflight.blocked
      : issues.some((issue) => issue.severity === 'blocked');
    $('readiness-preflight-title').textContent = blocked ? 'Setup needed before running' : 'Run with degraded capabilities?';
    $('readiness-preflight-reason').textContent = blocked
      ? 'This task is likely to fail until the blocked setup items are fixed.'
      : 'The task can start, but one or more fallback paths are degraded.';
    $('readiness-preflight-eyebrow').textContent = blocked ? 'Blocked preflight' : 'Capability warning';
    list.innerHTML = '';
    issues.forEach((issue) => {
      const row = document.createElement('div');
      row.className = `desktop-access-row readiness-preflight-row ${issue.severity}`;
      const copy = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = issue.label;
      const detail = document.createElement('span');
      detail.textContent = issue.detail;
      copy.append(name, detail);
      const badge = document.createElement('div');
      badge.className = 'desktop-access-badge';
      badge.textContent = issue.severity === 'blocked' ? 'Fix first' : 'Degraded';
      row.append(copy, badge);
      list.appendChild(row);
    });
    const btnSettings = $('readiness-preflight-settings');
    const btnCancel = $('readiness-preflight-cancel');
    const btnContinue = $('readiness-preflight-continue');
    btnContinue.hidden = blocked;
    const cleanup = (result) => {
      overlay.classList.remove('show');
      btnSettings.onclick = null;
      btnCancel.onclick = null;
      btnContinue.onclick = null;
      resolve(result);
    };
    btnSettings.onclick = () => { openSettingsFromPreflight(); cleanup(false); };
    btnCancel.onclick = () => cleanup(false);
    btnContinue.onclick = () => cleanup(true);
    overlay.classList.add('show');
  });

  const ensureTaskReadiness = async ({ goal, mode, model, isolatedApp }) => {
    await keyReady;
    let preflight;
    try {
      preflight = await api('/api/tasks/preflight', 'POST', {
        goal,
        mode,
        model: model || null,
        isolated_app: isolatedApp || null,
      });
    } catch (_) {
      preflight = {
        blocked: false,
        can_override: true,
        issues: [{
          key: 'readiness_error',
          label: 'Readiness',
          severity: 'warning',
          status: 'warning',
          detail: 'Could not run local task preflight. The task may still fail to start.',
        }],
      };
    }
    const issues = Array.isArray(preflight.issues) ? preflight.issues : [];
    if (!issues.length) return { ok: true, override: false, preflight };
    const allowed = await requestReadinessPreflight(preflight);
    return {
      ok: allowed,
      override: !!allowed && !!preflight.can_override,
      preflight,
    };
  };

  const handleServerPreflightRejection = async (err, taskPayload) => {
    const detail = err?.detail;
    if (!detail || typeof detail !== 'object' || !detail.preflight) return 'unhandled';
    const code = detail.code || '';
    if (code !== 'readiness_preflight_warning' && code !== 'readiness_preflight_blocked') return 'unhandled';
    const allowed = await requestReadinessPreflight(detail.preflight);
    if (allowed && code === 'readiness_preflight_warning' && detail.preflight?.can_override) {
      taskPayload.readiness_override = true;
      await api('/api/tasks', 'POST', taskPayload);
      return 'retried';
    }
    return 'cancelled';
  };

  const setMode = (mode, isolated = false, isolatedApp = '') => {
    currentMode = mode || 'coding';
    currentIsolatedApp = isolatedApp || (currentMode === 'computer_isolated' ? ($('isolated-app-id')?.value || '').trim() : '');
    const modeLabels = { computer: 'desktop', computer_use: 'browser', computer_isolated: 'isolated', coding: 'coding' };
    let label = modeLabels[currentMode] ?? humanize(currentMode).toLowerCase();
    if (isolated && currentMode === 'computer') {
      const appName = isolatedApp ? ` · ${isolatedApp}` : '';
      label += ` (iso${appName})`;
    }
    const mp = $('mode-pill');
    if (mp) {
      const modeLabel = document.createElement('span');
      modeLabel.className = 'pill-label';
      modeLabel.textContent = 'Mode';
      const modeValue = document.createElement('span');
      modeValue.className = 'pill-val';
      modeValue.textContent = label;
      mp.replaceChildren(modeLabel, modeValue);
    }
    const sbm = $('sb-mode-val'); if (sbm) sbm.textContent = label;
    setDesktopSessionActive(currentStatus === 'running' || currentStatus === 'paused', currentMode, currentIsolatedApp);
    selectPreferredModelForMode(currentMode);
  };

  const selectPreferredModelForMode = (mode) => {
    const select = $('model-id');
    if (!select || !select.options.length || !select.value) return;
    // A speed tier is an explicit user choice — never override it with a
    // mode-preferred raw model.
    if (select.value.startsWith('tier:')) return;
    const preferred = mode === 'coding'
      ? ['openrouter/qwen/qwen3-coder:free', 'openrouter/nvidia/nemotron-3-super-120b-a12b:free']
      : ['openrouter/nvidia/nemotron-3-super-120b-a12b:free', 'openrouter/google/gemma-4-31b-it:free'];
    const match = preferred.find((model) => Array.from(select.options).some((option) => option.value === model));
    if (!match || select.value === match) return;
    select.value = match;
    const sbm = $('sb-model-val');
    if (sbm) sbm.textContent = select.options[select.selectedIndex]?.textContent || '—';
  };

  const selectedModelForRequest = (mode) => {
    const selected = $('model-id')?.value || null;
    if ((mode || 'auto') === 'auto' && !modelSelectionTouched) return null;
    return selected;
  };

  const setBackgroundMode = (background, message = '') => {
    currentBackgroundMode = !!background;
    if (message) appendMessage(message, 'system-note');
  };

  let _statusPollBusy = false;
  // Safety net: if the SSE 'done' event is missed, the UI can look stuck on
  // "Running" forever. While a task runs, poll its status every 8s — if the
  // backend says terminal, synthesize the terminal event. The done/error
  // handlers dedupe via terminalStateKey, so this never double-fires.
  const pollTaskStatusSafety = async (listenTask) => {
    if (_statusPollBusy || !listenTask) return;
    _statusPollBusy = true;
    try {
      const record = await api(`/api/tasks/${listenTask}`);
      if (task !== listenTask) return;
      const st = record && record.status;
      if (st && st !== 'running' && !record.paused) {
        if (st === 'done' || st === 'complete') processTaskEvent({ type: 'done', complete: true, reason: record.reason || '' }, { taskId: listenTask, suppressToasts: true });
        else if (st === 'failed' || st === 'error') processTaskEvent({ type: 'done', complete: false, reason: record.reason || '' }, { taskId: listenTask, suppressToasts: true });
        else if (st === 'cancelled') processTaskEvent({ type: 'cancelled', message: record.reason || 'Task was cancelled.' }, { taskId: listenTask, suppressToasts: true });
      }
    } catch (_) {}
    finally { _statusPollBusy = false; }
  };

  const updateClock = () => {
    if (!startTime) { const e = $('sb-elapsed-val'); if (e) e.textContent = '00:00'; return; }
    const total = Math.floor((Date.now() - startTime) / 1000);
    const mins = String(Math.floor(total / 60)).padStart(2, '0');
    const secs = String(total % 60).padStart(2, '0');
    const fmt = `${mins}:${secs}`;
    const e = $('sb-elapsed-val'); if (e) e.textContent = fmt;
    // Every 8s, verify the task is actually still running.
    if (total > 0 && total % 8 === 0 && task && !isTerminalStatus(currentStatus)) {
      pollTaskStatusSafety(task);
    }
  };

  const setTaskTitle = (title, ctx = {}) => {
    const idleName = projectFolderState.selectedPath ? pathLeaf(projectFolderState.selectedPath) : 'Stream';
    $('task-title').textContent = title || idleName;
    const dot = $('topbar-dot');
    if (dot) dot.className = 'topbar-dot' + (ctx.status ? ' ' + ctx.status : '');
    const ctxEl = $('topbar-ctx');
    if (ctxEl) {
      const parts = [ctx.mode, ctx.model].filter(Boolean);
      ctxEl.textContent = parts.length ? '· ' + parts.join(' · ') : '';
    }
  };

  const renderHistoryItem = (taskRecord, makeActive = false) => {
    const item = document.createElement('button');
    item.type = 'button';
    const status = (taskRecord.status || '').toLowerCase();
    const isTerminal = ['done', 'complete', 'failed', 'error', 'cancelled'].includes(status);
    item.className = `history-item${makeActive ? ' active' : ''}${isTerminal ? ' terminal' : ''}`;
    item.dataset.taskId = taskRecord.id || '';
    const dotState = (status === 'running' || status === 'queued' || status === 'pending') ? 'running'
      : status === 'paused' ? 'paused'
      : (status === 'done' || status === 'complete') ? 'done'
      : (status === 'failed' || status === 'error') ? 'failed'
      : (status === 'cancelled') ? 'cancelled' : '';
    const dot = document.createElement('span');
    dot.className = `history-dot ${dotState}`.trim();
    const copy = document.createElement('span');
    copy.className = 'history-copy';
    const goal = document.createElement('span');
    goal.className = 'history-goal';
    goal.textContent = historyTitle(taskRecord.goal);
    const meta = document.createElement('span');
    meta.className = 'history-meta';
    meta.textContent = relTimeShort(taskRecord.created_at || taskRecord.timestamp || taskRecord.finished_at) || '';
    const retask = document.createElement('button');
    retask.type = 'button';
    retask.className = 'history-retask';
    retask.tabIndex = -1;
    retask.textContent = '\u21bb Copy task';
    retask.title = 'Copy this task into the prompt';
    copy.appendChild(goal);
    copy.appendChild(meta);
    item.appendChild(dot);
    item.appendChild(copy);
    item.appendChild(retask);
    // Full goal + mode/model on hover (the visible title is shortened).
    const fullGoal = String(taskRecord.goal || '').replace(/^\s*(?:\[[^\]]*\]\s*)+/, '').trim();
    item.title = [fullGoal, [taskRecord.mode, taskRecord.model].filter(Boolean).join(' / ')].filter(Boolean).join('\n');
    retask.addEventListener('click', (e) => {
      e.stopPropagation();
      const inp = $('input');
      inp.value = taskRecord.goal || '';
      inp.dispatchEvent(new Event('input'));
      inp.focus();
    });
    // Tag the item so reflow can group it by working folder and sort by recency.
    item.dataset.folder = historyGroupKey(taskRecord);
    item.dataset.created = String(Date.parse(taskRecord.created_at || taskRecord.timestamp || taskRecord.finished_at || '') || Date.now());
    historyItems.unshift(item);
    bindHistoryItem(item, taskRecord.id);
    reflowHistoryGroups();
    return item;
  };

  // ----- Codex-style sidebar: group sessions by the folder they ran in, show
  // only the 5 most recent per folder, and tuck the rest behind "See more". -----
  const historyGroupKey = (rec = {}) => (rec.context && rec.context.project_folder) || rec.project_folder || '';
  const historyGroupLabel = (key) => (key ? pathLeaf(key) : 'General');

  const reflowHistoryGroups = () => {
    if (suppressHistoryReflow) return;
    const container = $('task-history');
    if (!container) return;
    const query = ($('history-search')?.value || '').trim().toLowerCase();
    // Drop only the group wrappers / empty placeholder; the item nodes live in
    // historyItems and get re-appended below, so their state (active, dot) survives.
    container.querySelectorAll('.history-group, .history-empty, .history-seemore').forEach((el) => el.remove());
    if (!historyItems.length) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = 'Recent runs appear here. Click any session to replay the full stream.';
      container.appendChild(empty);
      return;
    }
    // Bucket items by folder key, remembering each group's most-recent timestamp.
    const groups = new Map();
    historyItems.forEach((item) => {
      const key = item.dataset.folder || '';
      if (!groups.has(key)) groups.set(key, { key, items: [], recent: 0 });
      const g = groups.get(key);
      g.items.push(item);
      g.recent = Math.max(g.recent, Number(item.dataset.created) || 0);
    });
    // Most recently active folder first.
    const ordered = [...groups.values()].sort((a, b) => b.recent - a.recent);
    ordered.forEach((g) => {
      g.items.sort((a, b) => (Number(b.dataset.created) || 0) - (Number(a.dataset.created) || 0));
      // When searching, ignore the cap and only show matches.
      const matches = query
        ? g.items.filter((it) => (it.querySelector('.history-goal')?.textContent || '').toLowerCase().includes(query))
        : g.items;
      if (!matches.length) return;
      const expanded = historyExpandedGroups.has(g.key) || !!query;
      const wrap = document.createElement('div');
      wrap.className = 'history-group';
      wrap.dataset.folder = g.key;
      const head = document.createElement('div');
      head.className = 'history-group-head';
      // Codex-style: a folder glyph turns each group into a "project" row.
      const lead = document.createElement('span');
      lead.className = 'history-group-lead';
      lead.innerHTML = '<svg class="history-group-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2V16a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
      const name = document.createElement('span');
      name.className = 'history-group-name';
      name.textContent = historyGroupLabel(g.key);
      name.title = g.key || 'General mode · Desktop + Home';
      lead.appendChild(name);
      const count = document.createElement('span');
      count.className = 'history-group-count';
      count.textContent = String(matches.length);
      head.append(lead, count);
      const list = document.createElement('div');
      list.className = 'history-group-items';
      matches.forEach((it, idx) => {
        it.classList.toggle('history-overflow', !expanded && idx >= HISTORY_GROUP_LIMIT);
        list.appendChild(it);
      });
      wrap.append(head, list);
      // "See more / Show less" only when a folder has more than the cap (never while searching).
      if (!query && matches.length > HISTORY_GROUP_LIMIT) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'history-seemore';
        more.textContent = expanded ? 'Show less' : 'Show more';
        more.addEventListener('click', () => {
          if (historyExpandedGroups.has(g.key)) historyExpandedGroups.delete(g.key);
          else historyExpandedGroups.add(g.key);
          reflowHistoryGroups();
        });
        wrap.appendChild(more);
      }
      container.appendChild(wrap);
    });
  };

  const refreshHistoryCount = () => { $('history-count').textContent = String(historyItems.length); };

  const bindHistoryItem = (element, taskId) => {
    if (!taskId) return;
    element.onclick = async () => {
      if (task && sse) { toast('Cancel the running task before opening another session.', 'warn'); return; }
      if (task === taskId) return;
      try {
        const result = await api(`/api/tasks/${taskId}/log`);
        loadTaskLog(taskId, result.log || [], element);
        if (window.innerWidth <= 1080) document.body.classList.remove('nav-open');
      } catch (_) { toast('Could not load that task log.', 'err'); }
    };
  };

  const filterHistory = () => { reflowHistoryGroups(); };

  const addActiveHistoryItem = (goal) => {
    const empty = $('task-history').querySelector('.history-empty');
    if (empty) empty.remove();
    historyItems.forEach((item) => item.classList.remove('active'));
    const item = renderHistoryItem({
      id: task, goal, status: 'running', created_at: new Date().toISOString(),
      context: { project_folder: projectFolderState.selectedPath || null },
    }, true);
    activeHistoryItem = item;
    refreshHistoryCount();
    return item;
  };

  const markHistoryFinal = (state) => {
    if (!activeHistoryItem) return;
    activeHistoryItem.classList.remove('active');
    const dot = activeHistoryItem.querySelector('.history-dot');
    if (!dot) return;
    dot.className = `history-dot ${state}`;
  };

  const taskRecordId = (record = {}) => record.task_id || record.id || '';

  const findHistoryItem = (taskId) => historyItems.find((item) => item.dataset.taskId === taskId) || null;

  const activateHistoryItem = (item) => {
    historyItems.forEach((historyItem) => historyItem.classList.remove('active'));
    item?.classList.add('active');
    activeHistoryItem = item || null;
  };

  const streamCursorAfter = (events = []) => {
    let cursor = 0;
    events.forEach((event, idx) => {
      const seq = Number(event?.seq);
      cursor = Math.max(cursor, Number.isFinite(seq) ? seq + 1 : idx + 1);
    });
    return cursor;
  };

  const activeTaskMeta = (record = {}, events = []) => {
    const created = events.find((event) => event.type === 'task_created') || {};
    const preflight = created.preflight || record.preflight || {};
    const mode = created.effective_mode || preflight.effective_mode || created.mode || record.effective_mode || record.mode || 'coding';
    const isolatedApp = created.isolated_app || preflight.isolated_app || record.isolated_app || record.context?.isolated_app || '';
    return {
      goal: record.goal || created.goal || record.context?.goal || 'Running task',
      mode,
      model: created.model || record.model || preflight.selected_model || '',
      isolatedApp,
      createdAt: created.created_at || record.created_at || ''
    };
  };

  const showLiveTaskControls = (record = {}, meta = {}) => {
    const rawStatus = String(record.status || 'running').toLowerCase();
    const queued = rawStatus === 'queued' || rawStatus === 'pending';
    const paused = !!record.paused || rawStatus === 'paused';
    isPaused = !queued && paused;
    $('btn-pause').textContent = isPaused ? 'Resume' : 'Pause';
    $('btn-pause').classList.toggle('hidden', queued);
    $('btn-cancel').classList.remove('hidden');
    $('btn-retry').classList.add('hidden');
    $('btn-control-report').classList.add('hidden');
    $('btn-copy-log').classList.add('hidden');
    $('btn-download-log').classList.add('hidden');
    $('send').classList.add('hidden');
    setStatus(queued ? rawStatus : (isPaused ? 'paused' : 'running'));
    setDesktopSessionActive(!queued && isDesktopMode(meta.mode), meta.mode, meta.isolatedApp || '');
  };

  const clearTrustControls = (entry, actionId = '') => {
    entry?.details?.querySelectorAll('[data-trust-controls="1"]').forEach((node) => node.remove());
    if (actionId && window.pendingApprovalId === actionId) {
      $('approval')?.classList.remove('show');
      window.pendingApprovalId = null;
    }
    if (actionId && window.pendingPermissionId === actionId) {
      $('permission')?.classList.remove('show');
      window.pendingPermissionId = null;
      window.pendingPermissionScope = null;
    }
  };

  const pendingTrustRequest = (events = []) => {
    const pending = new Map();
    events.forEach((event) => {
      const type = event?.type || '';
      const id = event?.action_id || '';
      if ((type === 'approval_required' || type === 'permission_required') && id) {
        pending.set(id, event);
        return;
      }
      if (id && (type === 'action_start' || type === 'action_result' || type === 'approval_timeout' || type === 'permission_timeout')) {
        pending.delete(id);
      }
      if (pending.has('__plan__') && (type === 'plan' || type === 'subtask' || type === 'action_start')) {
        pending.delete('__plan__');
      }
      if (type === 'done' || type === 'error' || type === 'cancelled') pending.clear();
    });
    const pendingEvents = Array.from(pending.values());
    return pendingEvents[pendingEvents.length - 1] || null;
  };

  const restorePendingTrustModal = (event, taskId) => {
    if (!event) return;
    if (event.type === 'approval_required') {
      const reason = event.reason || event.action?.explanation || 'High risk action requires approval.';
      $('app-title').textContent = `${humanize(event.action?.type || 'Action')} needs approval`;
      $('app-reason').textContent = reason;
      $('app-code').textContent = JSON.stringify(event.action?.args || {}, null, 2);
      const planEdit = $('app-plan-edit');
      const isPlanReview = event.action?.type === 'plan_review' || event.action?.args?.plan_text;
      if (planEdit) {
        planEdit.classList.toggle('hidden', !isPlanReview);
        planEdit.value = isPlanReview ? (event.action?.args?.plan_text || '') : '';
      }
      $('approval').classList.add('show');
      window.pendingTaskId = taskId;
      window.pendingApprovalId = event.action_id;
      return;
    }
    if (event.type === 'permission_required') {
      const detail = event.reason || event.explanation || `The agent needs ${event.scope || 'additional'} access.`;
      $('perm-title').textContent = `Allow ${event.scope || 'access'}?`;
      $('perm-reason').textContent = detail;
      $('perm-code').textContent = JSON.stringify({ scope: event.scope, explanation: event.explanation || '' }, null, 2);
      $('permission').classList.add('show');
      window.pendingTaskId = taskId;
      window.pendingPermissionId = event.action_id;
      window.pendingPermissionScope = event.scope;
    }
  };

  const ensureStatusCard = () => {
    if (liveStatusCard && liveStatusCard.isConnected) return liveStatusCard;
    const row = document.createElement('div');
    row.className = 'status-row';
    const copy = document.createElement('div');
    copy.className = 'status-copy';
    const line = document.createElement('div');
    line.className = 'status-line';
    const title = document.createElement('div');
    title.className = 'status-title';
    title.textContent = 'Agent update';
    const age = document.createElement('div');
    age.className = 'status-age';
    const subtitle = document.createElement('div');
    subtitle.className = 'status-subtitle';
    line.append(title, age);
    copy.append(line, subtitle);
    row.appendChild(copy);
    $('feed').appendChild(row);
    liveStatusCard = row;
    scrollFeed();
    return row;
  };

  const setLiveStatus = (title, detail = '', age = '') => {
    const card = ensureStatusCard();
    liveStatusMessage = detail || title || '';
    card.querySelector('.status-title').textContent = title || 'Agent update';
    card.querySelector('.status-subtitle').textContent = detail || '';
    card.querySelector('.status-age').textContent = age || '';
    scrollFeed();
  };

  const finalizeLiveStatus = () => {
    if (!liveStatusCard || !liveStatusCard.isConnected) return;
    liveStatusCard.style.display = 'none';
    liveStatusCard = null;
    liveStatusMessage = '';
  };

  const fmtWorkDuration = (sec) => {
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  };

  // Codex-style: when a turn finishes, fold the working steps (status notes,
  // reasoning, tool chatter) under a collapsed "Worked for Xm Ys ›" toggle and
  // leave the final answer below it.
  const summarizeWork = (durationSec) => {
    const feed = $('feed');
    if (!feed) return;
    const kids = Array.from(feed.children);
    let startIdx = -1;
    for (let i = kids.length - 1; i >= 0; i--) {
      if (kids[i].classList?.contains('message') && kids[i].classList.contains('user')) { startIdx = i; break; }
    }
    const isStep = (el) => el.classList && (
      el.classList.contains('status-row') ||
      (el.classList.contains('message') && (
        el.classList.contains('system-note') ||
        el.classList.contains('system-success') ||
        el.classList.contains('reasoning')))
    );
    const steps = [];
    for (let i = startIdx + 1; i < kids.length; i++) {
      if (isStep(kids[i]) && kids[i].style.display !== 'none') steps.push(kids[i]);
    }
    const summary = document.createElement('div');
    summary.className = 'work-summary';
    const header = document.createElement('button');
    header.type = 'button';
    header.className = 'work-summary-head';
    const dur = document.createElement('span');
    dur.className = 'work-dur';
    dur.textContent = `Worked for ${fmtWorkDuration(durationSec)}`;
    const chev = document.createElement('span');
    chev.className = 'work-chevron';
    chev.textContent = '›';
    header.append(dur, chev);
    const body = document.createElement('div');
    body.className = 'work-summary-body';
    body.hidden = true;
    if (steps.length) {
      feed.insertBefore(summary, steps[0]);
      steps.forEach((s) => body.appendChild(s));
      header.addEventListener('click', () => {
        body.hidden = !body.hidden;
        summary.classList.toggle('open', !body.hidden);
      });
    } else {
      feed.appendChild(summary);
      header.classList.add('no-expand');
    }
    summary.append(header, body);
  };

  // ---- Codex-style "N files changed" capstone (real edited paths only) ----
  const editedFiles = new Map();  // path -> 'new' | 'edited' | 'deleted'
  const _extractEditedPath = (summary, type) => {
    let s = String(summary || '').trim();
    const dash = s.indexOf(' - ');                       // strip a control-layer prefix
    if (dash >= 0 && /exact|layer|uia|fallback/i.test(s.slice(0, dash))) s = s.slice(dash + 3);
    s = s.split(/\s+[·—-]\s+/)[0].trim();                // strip trailing " · 42 lines" / " — note"
    if (/text_editor|str_replace|edit_file|insert|create/i.test(type)) {
      const toks = s.split(/\s+/);                        // "<command> <path>" -> path
      const last = toks[toks.length - 1];
      if (last && (/[\\/]/.test(last) || /\.\w{1,6}$/.test(last))) s = last;
    }
    return s;
  };
  const noteEditedFile = (path, state) => {
    if (!path || path.length > 200) return;
    const prev = editedFiles.get(path);
    if (state === 'deleted') editedFiles.set(path, 'deleted');
    else if (prev === 'new' || state === 'new') editedFiles.set(path, 'new');
    else editedFiles.set(path, 'edited');
  };
  const renderFilesChanged = () => {
    const feed = $('feed');
    if (!feed || !editedFiles.size) return;
    const n = editedFiles.size;
    const wrap = document.createElement('div');
    wrap.className = 'files-changed';
    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'files-changed-head';
    head.innerHTML =
      '<svg class="fc-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M5 3h9l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M9 13h6M12 10v6"/></svg>'
      + `<span class="fc-title">${n} file${n > 1 ? 's' : ''} changed</span>`
      + '<span class="fc-chevron">›</span>';
    const body = document.createElement('div');
    body.className = 'files-changed-body';
    editedFiles.forEach((state, path) => {
      const row = document.createElement('div');
      row.className = `fc-row fc-${state}`;
      const name = document.createElement('span');
      name.className = 'fc-path';
      name.textContent = path;
      name.title = path;
      const tag = document.createElement('span');
      tag.className = 'fc-tag';
      tag.textContent = state;
      row.append(name, tag);
      body.appendChild(row);
    });
    const expanded = n <= 5;                  // few files: show them; many: tuck away
    body.hidden = !expanded;
    wrap.classList.toggle('open', expanded);
    head.addEventListener('click', () => {
      body.hidden = !body.hidden;
      wrap.classList.toggle('open', !body.hidden);
    });
    wrap.append(head, body);
    feed.appendChild(wrap);
  };

  const createStateChip = (label, cls = '') => {
    const chip = document.createElement('span');
    // A bare duration ("20s", "2.4s") is a time, not a state — render it quiet.
    if (!cls && /^[\d.]+\s*s$/i.test(String(label).trim())) {
      chip.className = 'time-chip';
      chip.textContent = String(label).trim().toLowerCase();
    } else {
      chip.className = `state-chip ${cls}`.trim();
      chip.textContent = label;
    }
    return chip;
  };

  const createCardHead = ({ eyebrow, title, subtitle, stateLabel = '', stateClass = '' }) => {
    const head = document.createElement('div');
    head.className = 'card-head';

    const meta = document.createElement('div');
    meta.className = 'card-meta';

    const eyebrowEl = document.createElement('div');
    eyebrowEl.className = 'eyebrow';
    eyebrowEl.textContent = eyebrow;

    const titleEl = document.createElement('div');
    titleEl.className = 'card-title';
    titleEl.textContent = title;

    const subtitleEl = document.createElement('div');
    subtitleEl.className = 'card-subtitle';
    subtitleEl.textContent = subtitle || '';

    meta.appendChild(eyebrowEl);
    meta.appendChild(titleEl);
    if (subtitle) meta.appendChild(subtitleEl);
    head.appendChild(meta);
    const stateEl = stateLabel ? createStateChip(stateLabel, stateClass) : null;
    if (stateEl) head.appendChild(stateEl);

    return { head, eyebrowEl, titleEl, subtitleEl, stateEl };
  };

  const addWorkerTag = (eyebrowEl, workerId) => {
    if (!workerId) return;
    const tag = document.createElement('span');
    const workerNum = workerId.split('-').pop();
    tag.className = `worker-tag worker-${workerNum}`;
    tag.textContent = workerId.toUpperCase();
    eyebrowEl.prepend(tag);
  };

  const cleanSummary = (raw = '') => {
    return raw
      .replace(/^Worker\s+worker-\d+\s+/i, '')
      .replace(/^(worker-\d+)\s+/i, '')
      .replace(/\.{1,}$/, '')
      .trim() || raw;
  };

  const renderReasoning = (note) => {
    const rawSummary = note.summary || humanize(note.stage || 'thinking');
    const summary = cleanSummary(rawSummary);
    const detail = note.detail || '';
    const age = Number.isFinite(note.elapsed_seconds) ? `${note.elapsed_seconds}s` : '';

    if (note.live) { setLiveStatus(summary, detail, age); return; }

    finalizeLiveStatus();
    const card = createFeedCard('reasoning-note');
    const eyebrowLabel = note.stage
      ? note.stage.replace(/\s*\(WORKER-\d+\)\s*/i, '').trim() || 'Thinking'
      : 'Thinking';
    const bits = createCardHead({ eyebrow: eyebrowLabel, title: summary, subtitle: detail, stateLabel: age });
    addWorkerTag(bits.eyebrowEl, note.worker_id);
    card.appendChild(bits.head);
  };


  window.subtaskStates = {};

  const updateMermaidGraph = () => {
    if (!planSubtasks || planSubtasks.length === 0) return;
    const container = document.getElementById('plan-mermaid-container');
    if (!container) return;

    let graphDef = 'graph TD\n';
    planSubtasks.forEach((subtask, index) => {
       const state = window.subtaskStates[subtask.id] || 'pending';
       let color = '#334155'; // default/pending
       let stroke = '#475569';
       if (state === 'done') { color = '#065f46'; stroke = '#10b981'; } // green
       else if (state === 'running') { color = '#1e3a8a'; stroke = '#3b82f6'; } // blue
       else if (state === 'failed') { color = '#7f1d1d'; stroke = '#ef4444'; } // red

       const safeId = safeMermaidId(subtask.id, `task${index}`);
       const label = safeMermaidLabel(subtask.description);
       graphDef += `  ${safeId}["${label}"]\n`;
       graphDef += `  style ${safeId} fill:${color},color:#f8fafc,stroke:${stroke},stroke-width:2px,rx:6,ry:6\n`;

       if (subtask.dependencies && subtask.dependencies.length > 0) {
           subtask.dependencies.forEach(dep => {
               const safeDep = safeMermaidId(dep, '');
               if (!safeDep) return;
               graphDef += `  ${safeDep} --> ${safeId}\n`;
           });
       } else if (index > 0 && !subtask.dependencies && planSubtasks[index-1]) {
           // fallback sequential link if no deps explicitly defined
            const prevSafeId = safeMermaidId(planSubtasks[index-1].id, `task${index - 1}`);
            graphDef += `  ${prevSafeId} --> ${safeId}\n`;
       }
    });

    const renderId = 'mermaid-' + Math.random().toString(36).substr(2, 9);
    mermaid.render(renderId, graphDef).then(({svg}) => {
       container.innerHTML = svg;
    }).catch(e => console.error("Mermaid error:", e));
  };

  const renderPlan = (plan) => {
    finalizeLiveStatus();
    const replacingPlan = !!(activePlanCard && activePlanCard.isConnected);
    if (replacingPlan) activePlanCard.classList.add('superseded');

    activePlanCard = createFeedCard('plan-card');
    const headBits = createCardHead({
      eyebrow: 'Plan',
      title: replacingPlan ? 'Updated plan' : 'Execution plan',
      subtitle: plan.reasoning || '',
      stateLabel: `${(plan.sub_tasks || []).length} steps`
    });

    const body = document.createElement('div');
    body.className = 'card-body';
    const inner = document.createElement('div');
    inner.className = 'card-body-inner';
    body.appendChild(inner);

    const chevron = document.createElement('span');
    chevron.className = 'card-chevron open';
    chevron.appendChild(makeChevronIcon());
    headBits.head.appendChild(chevron);
    headBits.head.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      chevron.classList.toggle('open', !collapsed);
    });

    activePlanCard.appendChild(headBits.head);

    const mermaidContainer = document.createElement('div');
    mermaidContainer.id = 'plan-mermaid-container';
    mermaidContainer.style.cssText = 'width:100%; overflow-x:auto; margin-bottom:16px; padding:16px; background:var(--bg-deep); border-radius:8px; border:1px solid var(--border);';
    inner.appendChild(mermaidContainer);

    const list = document.createElement('div');
    list.className = 'subtask-list';
    subtaskEls = {};
    planSubtasks = plan.sub_tasks || [];
    currentSubtaskIdx = 0;
    window.subtaskStates = {};

    planSubtasks.forEach((subtask, index) => {
      window.subtaskStates[subtask.id] = 'pending';
      const row = document.createElement('div');
      row.className = 'subtask-item';
      const icon = document.createElement('div');
      icon.className = 'subtask-icon';
      icon.textContent = String(index + 1).padStart(2, '0');
      const text = document.createElement('div');
      text.className = 'subtask-text';
      text.textContent = subtask.description;
      row.append(icon, text);
      subtaskEls[subtask.id] = row;
      list.appendChild(row);
    });

    inner.appendChild(list);
    activePlanCard.appendChild(body);
    scrollFeed();
    
    // Initial render
    setTimeout(updateMermaidGraph, 100);
  };

  const markSubtask = (id, state) => {
    window.subtaskStates[id] = state;
    updateMermaidGraph();

    const row = subtaskEls[id];
    if (!row) return;
    row.className = `subtask-item ${state}`.trim();
    const icon = row.querySelector('.subtask-icon');
    if (!icon) return;
    const fallback = icon.textContent;
    icon.textContent = state === 'done' ? '✓' : state === 'running' ? '●' : state === 'failed' ? '✕' : fallback;
  };

  const ensureActionCard = (actionId, actionType = 'action', summary = '') => {
    if (actionId && actionCards[actionId]) {
      const existing = actionCards[actionId];
      if (summary) existing.subtitleEl.textContent = summary;
      setActiveCard(existing.card);
      return existing;
    }

    // Phase C1: group into active turn summary
    const turn = startTurnSummary();
    turn.types.push(_actionTypeLabel(actionType));
    turn.textSpan.textContent = _turnSummaryText(turn.types, true);
    // Phase C2: record step data for timeline; refs filled after parts are created
    const stepData = {
      label: humanize(actionType || 'action'),
      actionType: actionType || '',
      summary: summary || '',
      stateEl: null,
      subtitleEl: null,
      outputEl: null,
      traceEl: null,
    };
    turn.steps.push(stepData);

    removeWelcome();
    const card = document.createElement('div');
    card.className = 'feed-card tool-card';
    setActiveCard(card);
    const label = humanize(actionType || 'action');
    const parts = createCardHead({
      eyebrow: 'Tool',
      title: label,
      subtitle: summary || 'Waiting for result…',
      stateLabel: 'Running',
      stateClass: 'running'
    });

    const chevron = document.createElement('span');
    chevron.className = 'card-chevron';
    chevron.appendChild(makeChevronIcon());
    parts.head.appendChild(chevron);
    card.appendChild(parts.head);

    const body = document.createElement('div');
    body.className = 'card-body collapsed';
    const inner = document.createElement('div');
    inner.className = 'card-body-inner';
    body.appendChild(inner);

    const output = document.createElement('pre');
    output.className = 'tool-output hidden';
    const trace = document.createElement('div');
    trace.className = 'control-trace hidden';
    const details = document.createElement('div');
    details.className = 'detail-list';

    inner.appendChild(trace);
    inner.appendChild(output);
    inner.appendChild(details);
    card.appendChild(body);
    turn.body.appendChild(card);

    parts.head.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      chevron.classList.toggle('open', !collapsed);
    });

    // Phase C2: wire live DOM refs into stepData so timeline reads current values
    stepData.stateEl = parts.stateEl;
    stepData.subtitleEl = parts.subtitleEl;
    stepData.outputEl = output;
    stepData.traceEl = trace;

    const entry = {
      card, output, traceEl: trace, details,
      titleEl: parts.titleEl,
      subtitleEl: parts.subtitleEl,
      stateEl: parts.stateEl,
      terminalRows: {},
      body, chevron
    };

    if (actionId) actionCards[actionId] = entry;
    return entry;
  };

  const setActionState = (entry, label, cls = '') => {
    if (!entry || !entry.stateEl) return;
    entry.stateEl.className = `state-chip ${cls}`.trim();
    entry.stateEl.textContent = label;
  };

  const openEntryBody = (entry) => {
    if (!entry) return;
    entry.body.classList.remove('collapsed');
    if (entry.chevron) entry.chevron.classList.add('open');
  };

  const appendPreviewBlock = (container, preview, className = 'detail-preview') => {
    if (!preview) return;
    if (preview.length <= 360) {
      const code = document.createElement('pre');
      code.className = className;
      code.textContent = preview;
      container.appendChild(code);
      return;
    }
    const wrap = document.createElement('details');
    wrap.className = 'preview-wrap';
    const summary = document.createElement('summary');
    summary.className = 'preview-toggle';
    summary.textContent = `Open preview · ${preview.length} chars`;
    const code = document.createElement('pre');
    code.className = className;
    code.textContent = preview;
    wrap.appendChild(summary);
    wrap.appendChild(code);
    container.appendChild(wrap);
  };

  const overlayRectText = (label, rect) => {
    if (!rect || typeof rect !== 'object') return '';
    const left = Number(rect.left);
    const top = Number(rect.top);
    const width = Number(rect.width);
    const height = Number(rect.height);
    if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return '';
    return `${label} ${Math.round(left)},${Math.round(top)} ${Math.round(width)}x${Math.round(height)}`;
  };

  const overlayPointText = (point) => {
    if (!point || typeof point !== 'object') return '';
    const x = Number(point.x);
    const y = Number(point.y);
    if (![x, y].every(Number.isFinite)) return '';
    return `point ${Math.round(x)},${Math.round(y)}`;
  };

  const overlayTraceParts = (overlay, phase = '') => {
    if (!overlay || typeof overlay !== 'object') return [];
    const parts = [];
    const add = (label, value) => {
      const text = String(value || '').trim();
      if (text) parts.push([label, text]);
    };
    add('Layer', overlay.control_layer);
    add('Reason', overlay.control_reason || overlay.fallback_reason);
    add('Target', overlay.target || overlay.label);
    add('Phase', phase || overlay.phase);
    add('Rect', overlayRectText('control', overlay.rect));
    add('App', overlayRectText('app', overlay.app_rect));
    add('Point', overlayPointText(overlay.point));
    return parts;
  };

  const renderControlTrace = (entry, overlay, phase = '') => {
    if (!entry || !entry.traceEl) return;
    const parts = overlayTraceParts(overlay, phase);
    entry.traceEl.replaceChildren();
    if (!parts.length) {
      entry.traceEl.classList.add('hidden');
      entry.traceEl.dataset.traceSummary = '';
      return;
    }
    entry.traceEl.classList.remove('hidden');
    const summaryParts = [];
    parts.forEach(([label, value], index) => {
      const chip = document.createElement('span');
      chip.className = `control-trace-chip ${label.toLowerCase()}`;
      const key = document.createElement('span');
      key.className = 'control-trace-key';
      key.textContent = label;
      const val = document.createElement('span');
      val.className = 'control-trace-value';
      val.textContent = value;
      chip.appendChild(key);
      chip.appendChild(val);
      entry.traceEl.appendChild(chip);
      if (index < 4) summaryParts.push(value);
    });
    entry.traceEl.dataset.traceSummary = summaryParts.join(' / ');
  };

  const appendDetailRow = (entry, eyebrow, title, copy = '', preview = '') => {
    if (!entry) return null;
    const row = document.createElement('div');
    row.className = 'detail-row';
    const head = document.createElement('div');
    head.className = 'detail-row-head';
    const group = document.createElement('div');
    const labelEl = document.createElement('div');
    labelEl.className = 'detail-label';
    labelEl.textContent = eyebrow;
    const titleEl = document.createElement('div');
    titleEl.className = 'detail-title';
    titleEl.textContent = title || '';
    group.append(labelEl, titleEl);
    head.appendChild(group);
    row.appendChild(head);
    if (copy) {
      const body = document.createElement('div');
      body.className = 'detail-copy';
      body.textContent = copy;
      row.appendChild(body);
    }
    if (preview) appendPreviewBlock(row, preview);
    entry.details.appendChild(row);
    openEntryBody(entry);
    scrollFeed();
    return row;
  };

  const appendTerminalOutput = (entry, command, output, ok, channel = 'stdout') => {
    if (!entry) return;
    const key = `${command || 'Command output'}:${channel}`;
    if (!entry.terminalRows[key]) {
      const row = document.createElement('div');
      row.className = 'detail-row';
      const head = document.createElement('div');
      head.className = 'detail-row-head';
      const group = document.createElement('div');
      const label = document.createElement('div');
      label.className = 'detail-label';
      label.textContent = 'Terminal';
      const title = document.createElement('div');
      title.className = 'detail-title';
      title.textContent = command || 'Command output';
      group.append(label, title);
      head.appendChild(group);
      row.appendChild(head);
      const channelEl = document.createElement('span');
      channelEl.className = 'terminal-channel';
      channelEl.textContent = channel;
      title.appendChild(channelEl);
      const pre = document.createElement('pre');
      pre.className = 'detail-preview';
      row.appendChild(pre);
      entry.details.appendChild(row);
      entry.terminalRows[key] = pre;
      openEntryBody(entry);
    }
    const pre = entry.terminalRows[key];
    pre.textContent = (pre.textContent + (output || '')).slice(-4000);
    if (!ok) pre.style.color = 'var(--err)';
    pre.scrollTop = pre.scrollHeight;
    scrollFeed();
  };

  const renderStandaloneArtifact = ({ eyebrow, title, copy = '', preview = '', className = '' }) => {
    const card = createFeedCard(className);
    const bits = createCardHead({ eyebrow, title, subtitle: copy, stateLabel: 'Inline' });
    card.appendChild(bits.head);
    if (preview) appendPreviewBlock(card, preview, 'artifact-preview');
    return card;
  };

  const makeEl = (tag, className = '', text = '') => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null && text !== '') el.textContent = text;
    return el;
  };

  const setPct = (el, value) => {
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    el.style.setProperty('--pct', pct);
    el.style.setProperty('--pct-text', `"${Math.round(pct)}%"`);
  };

  const widgetButton = (label, variant = '') => {
    const btn = makeEl('button', `widget-btn ${variant}`.trim(), label);
    btn.type = 'button';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      btn.classList.add('pressed');
      setTimeout(() => btn.classList.remove('pressed'), 180);
    });
    return btn;
  };

  const renderMetricStrip = (root, metrics = []) => {
    const strip = makeEl('div', 'widget-metrics');
    metrics.forEach((m) => {
      const item = makeEl('div', 'widget-metric');
      item.appendChild(makeEl('span', 'widget-metric-label', m.label || 'Metric'));
      item.appendChild(makeEl('strong', '', m.value || '0'));
      if (m.detail) item.appendChild(makeEl('span', 'widget-metric-detail', m.detail));
      strip.appendChild(item);
    });
    root.appendChild(strip);
    return strip;
  };

  const renderWidgetList = (root, rows = [], opts = {}) => {
    const list = makeEl('div', `widget-list ${opts.checks ? 'checks' : ''} ${opts.className || ''}`.trim());
    rows.forEach((row) => {
      const item = makeEl('div', `widget-row ${row.intent || ''}`.trim());
      if (opts.checks) {
        const check = makeEl('button', `widget-check ${row.checked === false ? '' : 'on'}`.trim(), row.checked === false ? '' : '✓');
        check.type = 'button';
        check.addEventListener('click', () => {
          const on = check.classList.toggle('on');
          check.textContent = on ? '✓' : '';
        });
        item.appendChild(check);
      }
      const copy = makeEl('div', 'widget-row-copy');
      copy.appendChild(makeEl('strong', '', row.title || 'Item'));
      if (row.subtitle) copy.appendChild(makeEl('span', '', row.subtitle));
      item.appendChild(copy);
      if (row.meta) item.appendChild(makeEl('span', 'widget-row-meta', row.meta));
      if (row.action) item.appendChild(widgetButton(row.action, row.intent === 'danger' ? 'danger' : ''));
      list.appendChild(item);
    });
    root.appendChild(list);
    return list;
  };

  const widgetDefaults = {
    clutter_sweeper: {
      metrics: [
        { label: 'Recoverable', value: '8.4 GB', detail: 'safe to review' },
        { label: 'Duplicates', value: '126', detail: 'grouped' },
        { label: 'Largest', value: '2.1 GB', detail: 'video cache' }
      ],
      rows: [
        { title: 'Screen recordings cache', subtitle: '%USERPROFILE%\\Videos\\Captures', meta: '2.1 GB', action: 'Wipe', intent: 'danger' },
        { title: 'Duplicate installer bundle', subtitle: 'Downloads\\setup-copy.exe', meta: '940 MB', action: 'Wipe', intent: 'danger' },
        { title: 'Old model temp files', subtitle: 'AppData\\Local\\Temp\\ai-cache', meta: '730 MB', action: 'Wipe', intent: 'danger' },
        { title: 'Repeated screenshots', subtitle: 'Desktop\\captures\\*.png', meta: '418 MB', action: 'Wipe', intent: 'danger' }
      ]
    },
    smart_organizer: {
      messy: ['invoice_q4.pdf', 'cap-idle.png', 'meeting-notes.md', 'agent_trace.jsonl'],
      folders: ['Documents / Finance', 'Pictures / UI References', 'Notes / Meetings', 'Logs / Agent Runs'],
      moves: [
        { title: 'invoice_q4.pdf', subtitle: 'Downloads -> Documents / Finance', meta: '96%' },
        { title: 'cap-idle.png', subtitle: 'Desktop -> Pictures / UI References', meta: '91%' },
        { title: 'agent_trace.jsonl', subtitle: 'Downloads -> Logs / Agent Runs', meta: '88%' }
      ]
    },
    file_preview: {
      name: 'Q3 Product Review.pdf',
      meta: ['18 pages', 'Last edited today', '4 callouts'],
      preview: 'Executive summary\n\nRevenue quality improved while support volume stayed flat. The biggest risk is onboarding friction in the first 10 minutes. Recommended next move: simplify the first-run workspace picker, then measure task completion rate by cohort.'
    },
    resource_radar: {
      meters: [
        { label: 'CPU', value: 38 },
        { label: 'RAM', value: 67 },
        { label: 'GPU', value: 24 }
      ],
      bars: [34, 44, 28, 61, 48, 76, 45, 58, 37, 64, 42, 52],
      processes: [
        { title: 'python.exe', subtitle: 'Orynn server', meta: '418 MB', action: 'Keep' },
        { title: 'msedgewebview2.exe', subtitle: 'background webview', meta: '311 MB', action: 'Kill', intent: 'danger' },
        { title: 'Code.exe', subtitle: 'extension host', meta: '268 MB', action: 'Inspect' }
      ]
    },
    quick_settings: {
      toggles: [
        { title: 'Focus', active: true },
        { title: 'Dark', active: true },
        { title: 'Mute', active: false },
        { title: 'Power saver', active: false },
        { title: 'VPN', active: true },
        { title: 'Clipboard', active: true }
      ]
    },
    network_guardian: {
      rows: [
        { title: 'OneDrive.exe', subtitle: 'Syncing screenshots', meta: '2.4 MB/s up', pct: 82, action: 'Throttle' },
        { title: 'Chrome.exe', subtitle: 'Streaming media tab', meta: '5.8 MB/s down', pct: 68, action: 'Inspect' },
        { title: 'Unknown helper', subtitle: 'Unsigned background process', meta: '640 KB/s up', pct: 42, action: 'Block', intent: 'danger' }
      ]
    },
    action_approver: {
      title: 'PowerShell deletion request',
      reason: 'The agent wants to remove duplicate files from Downloads.',
      code: 'Remove-Item -LiteralPath "$env:USERPROFILE\\Downloads\\setup-copy.exe" -Force\nRemove-Item -LiteralPath "$env:LOCALAPPDATA\\Temp\\ai-cache" -Recurse -Force',
      risks: ['Deletes files', 'No recycle bin', '2 paths']
    },
    email_summary: {
      title: 'Client escalation thread',
      bullets: [
        'Customer is blocked by login redirects after SSO migration.',
        'They need a status update before 4 PM Mountain.',
        'Engineering suspects stale callback URLs in the tenant config.'
      ],
      replies: ['Send calm status', 'Ask for logs', 'Schedule triage']
    },
    source_grid: {
      sources: [
        { title: 'Microsoft Learn', host: 'learn.microsoft.com', snippet: 'DWM system backdrop guidance for Windows 11 windows.' },
        { title: 'FastAPI docs', host: 'fastapi.tiangolo.com', snippet: 'Lifespan and dependency patterns for app startup.' },
        { title: 'Playwright', host: 'playwright.dev', snippet: 'Locator-first testing guidance for reliable UI checks.' },
        { title: 'MDN Web APIs', host: 'developer.mozilla.org', snippet: 'SpeechRecognition compatibility and graceful fallback notes.' }
      ]
    },
    data_table: {
      columns: ['GPU', 'Price', 'VRAM', 'Store'],
      rows: [
        ['RTX 4070 Super', '$579', '12 GB', 'BestBuy'],
        ['RX 7900 GRE', '$529', '16 GB', 'Newegg'],
        ['RTX 4060 Ti', '$379', '16 GB', 'Amazon'],
        ['Arc B580', '$249', '12 GB', 'Micro Center']
      ]
    }
  };

  const renderClutterSweeper = (root, data = {}) => {
    const d = { ...widgetDefaults.clutter_sweeper, ...data };
    renderMetricStrip(root, d.metrics);
    renderWidgetList(root, d.rows, { checks: true });
    const actions = makeEl('div', 'widget-actions');
    actions.appendChild(widgetButton('Review selected'));
    actions.appendChild(widgetButton('Wipe selected', 'danger'));
    root.appendChild(actions);
  };

  const renderSmartOrganizer = (root, data = {}) => {
    const d = { ...widgetDefaults.smart_organizer, ...data };
    const cols = makeEl('div', 'widget-columns organizer-columns');
    [['Messy', d.messy], ['Homes', d.folders]].forEach(([label, items]) => {
      const col = makeEl('div', 'widget-column');
      col.appendChild(makeEl('span', 'widget-column-label', label));
      (items || []).forEach((item) => col.appendChild(makeEl('div', 'organizer-chip', item)));
      cols.appendChild(col);
    });
    root.appendChild(cols);
    renderWidgetList(root, d.moves || []);
    const actions = makeEl('div', 'widget-actions');
    actions.appendChild(widgetButton('Approve moves'));
    root.appendChild(actions);
  };

  const renderFilePreviewer = (root, data = {}) => {
    const d = { ...widgetDefaults.file_preview, ...data };
    const top = makeEl('div', 'file-preview-top');
    top.appendChild(makeEl('div', 'file-preview-icon', 'PDF'));
    const copy = makeEl('div', 'file-preview-copy');
    copy.appendChild(makeEl('strong', '', d.name));
    const meta = makeEl('div', 'widget-chip-row');
    (d.meta || []).forEach((m) => meta.appendChild(makeEl('span', 'widget-chip', m)));
    copy.appendChild(meta);
    top.appendChild(copy);
    root.appendChild(top);
    root.appendChild(makeEl('pre', 'file-preview-text', d.preview || ''));
  };

  const renderResourceRadar = (root, data = {}) => {
    const d = { ...widgetDefaults.resource_radar, ...data };
    const radar = makeEl('div', 'radar-grid');
    (d.meters || []).forEach((m) => {
      const meter = makeEl('div', 'radar-meter');
      const ring = makeEl('div', 'radar-ring');
      setPct(ring, m.value);
      ring.appendChild(makeEl('span', '', `${Math.round(Number(m.value) || 0)}%`));
      meter.appendChild(ring);
      meter.appendChild(makeEl('span', 'radar-label', m.label || 'Usage'));
      radar.appendChild(meter);
    });
    const spark = makeEl('div', 'radar-spark');
    (d.bars || []).forEach((value) => {
      const bar = makeEl('span');
      bar.style.height = `${Math.max(8, Math.min(100, value))}%`;
      spark.appendChild(bar);
    });
    radar.appendChild(spark);
    root.appendChild(radar);
    renderWidgetList(root, d.processes || []);
  };

  const renderQuickSettings = (root, data = {}) => {
    const d = { ...widgetDefaults.quick_settings, ...data };
    const grid = makeEl('div', 'widget-toggle-grid');
    (d.toggles || []).forEach((t) => {
      const btn = makeEl('button', `widget-toggle ${t.active ? 'on' : ''}`.trim());
      btn.type = 'button';
      btn.appendChild(makeEl('span', 'widget-toggle-dot'));
      btn.appendChild(makeEl('strong', '', t.title || 'Toggle'));
      btn.addEventListener('click', () => btn.classList.toggle('on'));
      grid.appendChild(btn);
    });
    root.appendChild(grid);
  };

  const renderNetworkGuardian = (root, data = {}) => {
    const d = { ...widgetDefaults.network_guardian, ...data };
    const list = makeEl('div', 'network-list');
    (d.rows || []).forEach((row) => {
      const item = makeEl('div', `network-row ${row.intent || ''}`.trim());
      const copy = makeEl('div', 'network-copy');
      copy.appendChild(makeEl('strong', '', row.title || 'Process'));
      copy.appendChild(makeEl('span', '', row.subtitle || 'Network activity'));
      const bar = makeEl('div', 'network-bar');
      setPct(bar, row.pct || 0);
      copy.appendChild(bar);
      item.appendChild(copy);
      item.appendChild(makeEl('span', 'widget-row-meta', row.meta || '0 KB/s'));
      item.appendChild(widgetButton(row.action || 'Inspect', row.intent === 'danger' ? 'danger' : ''));
      list.appendChild(item);
    });
    root.appendChild(list);
  };

  const renderActionApprover = (root, data = {}) => {
    const d = { ...widgetDefaults.action_approver, ...data };
    const panel = makeEl('div', 'approver-panel');
    panel.appendChild(makeEl('strong', '', d.title || 'Action needs approval'));
    panel.appendChild(makeEl('p', '', d.reason || 'Review the requested action before it runs.'));
    const risks = makeEl('div', 'widget-chip-row');
    (d.risks || []).forEach((risk) => risks.appendChild(makeEl('span', 'widget-chip danger', risk)));
    panel.appendChild(risks);
    panel.appendChild(makeEl('pre', 'approver-code', d.code || ''));
    const actions = makeEl('div', 'widget-actions');
    actions.appendChild(widgetButton('Deny'));
    actions.appendChild(widgetButton('Approve', 'danger'));
    panel.appendChild(actions);
    root.appendChild(panel);
  };

  const renderEmailSummary = (root, data = {}) => {
    const d = { ...widgetDefaults.email_summary, ...data };
    const box = makeEl('div', 'summary-widget');
    box.appendChild(makeEl('strong', '', d.title || 'Context summary'));
    const list = makeEl('ul', 'summary-bullets');
    (d.bullets || []).forEach((b) => {
      const li = makeEl('li', '', b);
      list.appendChild(li);
    });
    box.appendChild(list);
    const actions = makeEl('div', 'widget-actions');
    (d.replies || []).forEach((reply) => actions.appendChild(widgetButton(reply)));
    box.appendChild(actions);
    root.appendChild(box);
  };

  const renderSourceGrid = (root, data = {}) => {
    const d = { ...widgetDefaults.source_grid, ...data };
    const grid = makeEl('div', 'source-grid');
    (d.sources || []).forEach((source) => {
      const card = makeEl('a', 'source-card');
      if (source.url) card.href = source.url;
      card.target = '_blank';
      card.rel = 'noreferrer';
      card.appendChild(makeEl('span', 'source-favicon', (source.host || source.title || '?').slice(0, 1).toUpperCase()));
      card.appendChild(makeEl('strong', '', source.title || 'Source'));
      card.appendChild(makeEl('span', 'source-host', source.host || 'web'));
      card.appendChild(makeEl('p', '', source.snippet || ''));
      grid.appendChild(card);
    });
    root.appendChild(grid);
  };

  const renderDataTable = (root, data = {}) => {
    const d = { ...widgetDefaults.data_table, ...data };
    const toolbar = makeEl('div', 'table-toolbar');
    const search = makeEl('input', 'table-filter');
    search.type = 'search';
    search.placeholder = 'Filter rows';
    toolbar.appendChild(search);
    root.appendChild(toolbar);
    const table = makeEl('table', 'widget-table');
    const thead = makeEl('thead');
    const header = makeEl('tr');
    const tbody = makeEl('tbody');
    let sortIndex = -1;
    let sortDir = 1;
    const rows = (d.rows || []).map((row) => row.slice());
    (d.columns || []).forEach((col, idx) => {
      const th = makeEl('th');
      const btn = makeEl('button', '', col);
      btn.type = 'button';
      btn.addEventListener('click', () => {
        sortDir = sortIndex === idx ? -sortDir : 1;
        sortIndex = idx;
        draw();
      });
      th.appendChild(btn);
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);
    table.appendChild(tbody);
    root.appendChild(table);
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      tbody.innerHTML = '';
      rows
        .filter((row) => !q || row.join(' ').toLowerCase().includes(q))
        .sort((a, b) => sortIndex < 0 ? 0 : String(a[sortIndex]).localeCompare(String(b[sortIndex]), undefined, { numeric: true }) * sortDir)
        .forEach((row) => {
          const tr = makeEl('tr');
          row.forEach((cell) => tr.appendChild(makeEl('td', '', cell)));
          tbody.appendChild(tr);
        });
    };
    search.addEventListener('input', draw);
    draw();
  };

  const widgetRenderers = {
    clutter_sweeper: renderClutterSweeper,
    smart_organizer: renderSmartOrganizer,
    file_preview: renderFilePreviewer,
    resource_radar: renderResourceRadar,
    quick_settings: renderQuickSettings,
    network_guardian: renderNetworkGuardian,
    action_approver: renderActionApprover,
    email_summary: renderEmailSummary,
    source_grid: renderSourceGrid,
    data_table: renderDataTable
  };

  const widgetTitles = {
    clutter_sweeper: 'Clutter sweeper',
    smart_organizer: 'Smart organizer',
    file_preview: 'File preview',
    resource_radar: 'Resource radar',
    quick_settings: 'Quick settings',
    network_guardian: 'Network guardian',
    action_approver: 'Action approver',
    email_summary: 'Context summary',
    source_grid: 'Source grid',
    data_table: 'Interactive table'
  };

  const renderAgentWidget = (event = {}) => {
    finalizeLiveStatus();
    const rawType = event.widget || event.widget_type || event.kind || event.type || 'widget';
    const widgetType = String(rawType).replace(/-/g, '_');
    const data = event.data || event.payload || event;
    const card = createFeedCard(`ai-widget-card widget-${widgetType}`);
    const bits = createCardHead({
      eyebrow: event.eyebrow || 'Widget',
      title: event.title || widgetTitles[widgetType] || humanize(widgetType),
      subtitle: event.subtitle || event.summary || '',
      stateLabel: event.stateLabel || event.state || ''
    });
    card.appendChild(bits.head);
    const surface = makeEl('div', 'widget-surface');
    card.appendChild(surface);
    const renderer = widgetRenderers[widgetType];
    if (renderer) renderer(surface, data);
    else renderMetricStrip(surface, [{ label: 'Payload', value: 'Ready', detail: 'generic widget' }]);
    return card;
  };

  const renderReflection = (reflection) => {
    finalizeLiveStatus();
    const success = reflection.success === true;
    const failure = reflection.success === false;
    const card = createFeedCard(`reflection-card${success ? ' success' : failure ? ' failure' : ''}`);
    const bits = createCardHead({
      eyebrow: 'Reflection',
      title: success ? 'Subtask passed' : failure ? 'Needs another pass' : 'Reflection',
      subtitle: reflection.reason || reflection.summary || reflection.explanation || '',
      stateLabel: success ? 'OK' : failure ? 'Retry' : 'Note',
      stateClass: success ? 'ok' : failure ? 'waiting' : ''
    });
    addWorkerTag(bits.eyebrowEl, reflection.worker_id);
    card.appendChild(bits.head);
  };

  const screenshotStore = new Map();
  let screenshotIdCounter = 0;

  const _base64ToBlob = (data) => {
    const b64 = data.startsWith('data:') ? data.split(',')[1] : data;
    const mime = data.startsWith('data:') ? data.split(';')[0].slice(5) : 'image/png';
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime });
  };

  const renderScreenshot = (payload) => {
    finalizeLiveStatus();
    const sid = ++screenshotIdCounter;
    const blob = _base64ToBlob(payload.data);
    const blobUrl = URL.createObjectURL(blob);
    screenshotStore.set(sid, { blobUrl, window_rect: payload.window_rect || null, createdAt: Date.now() });
    if (screenshotStore.size > 5) {
      const oldKey = screenshotStore.keys().next().value;
      const oldEntry = screenshotStore.get(oldKey);
      if (oldEntry && oldEntry.blobUrl) URL.revokeObjectURL(oldEntry.blobUrl);
      screenshotStore.delete(oldKey);
    }

    const card = createFeedCard('screenshot-card');
    const isIsolated = payload.isolated || false;
    const bits = createCardHead({
      eyebrow: 'Preview',
      title: isIsolated ? 'Isolated snapshot' : (currentMode === 'computer_use' ? 'Browser snapshot' : 'Screen snapshot'),
      stateLabel: 'Shot',
      stateClass: 'ok'
    });
    addWorkerTag(bits.eyebrowEl, payload.worker_id);
    card.appendChild(bits.head);

    const img = document.createElement('img');
    img.className = 'screenshot-preview';
    img.src = blobUrl;
    img.loading = 'lazy';
    img.onclick = () => {
      const entry = screenshotStore.get(sid);
      if (entry) openLightbox(entry.blobUrl, entry.window_rect);
    };
    card.appendChild(img);
  };

  const openLightbox = (data, window_rect) => {
    $('lightbox-img').src = data;
    const oldCanvases = $('lightbox').querySelectorAll('canvas');
    oldCanvases.forEach(c => c.remove());
    if (window_rect) {
      const img = $('lightbox-img');
      const drawGlow = () => {
        const canvas = document.createElement('canvas');
        canvas.style.position = 'absolute';
        canvas.style.top = img.offsetTop + 'px';
        canvas.style.left = img.offsetLeft + 'px';
        canvas.style.width = img.offsetWidth + 'px';
        canvas.style.height = img.offsetHeight + 'px';
        canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
        img.parentNode.appendChild(canvas);
        const ctx = canvas.getContext('2d');
        const px = window_rect.x * canvas.width;
        const py = window_rect.y * canvas.height;
        const pw = window_rect.w * canvas.width;
        const ph = window_rect.h * canvas.height;
        ctx.shadowColor = 'var(--accent)';
        ctx.shadowBlur = 40;
        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
        ctx.lineWidth = 4;
        ctx.strokeRect(px, py, pw, ph);
        ctx.fillStyle = 'rgba(255,255,255,0.08)';
        ctx.fillRect(px, py, pw, ph);
      };
      if (img.complete) drawGlow(); else img.onload = drawGlow;
    }
    $('lightbox').classList.add('show');
  };
  const closeLightbox = () => $('lightbox').classList.remove('show');

  const resetTaskView = ({ replay = false, keepFeed = false } = {}) => {
    clearInterval(timer);
    clearReconnectTimer();
    if (sse) { sse.close(); sse = null; }
    streamClosedManually = replay;
    reconnectAttempts = 0; streamCursor = 0; startTime = 0; isPaused = false;
    activePlanCard = null; liveStatusCard = null; liveStatusMessage = '';
    setControlSurface();
    planSubtasks = []; currentSubtaskIdx = 0; subtaskEls = {};
    screenshotStore.clear(); lastActionId = null; terminalStateKey = null;
    editedFiles.clear();
    Object.keys(actionCards).forEach((k) => delete actionCards[k]);
    lastActiveCard = null; activeTurnSummary = null;

    setStatus('ready');
    setMode($('mode-id').value || 'coding');
    $('elapsed-time') && ($('elapsed-time').textContent = replay ? '--:--' : '00:00');
    const sbe = $('sb-elapsed-val'); if (sbe) sbe.textContent = replay ? '--:--' : '00:00';
    $('btn-pause').classList.add('hidden');
    $('btn-cancel').classList.add('hidden');
    $('btn-retry').classList.add('hidden');
    $('btn-control-report').classList.add('hidden');
    $('btn-copy-log').classList.add('hidden');
    $('btn-download-log').classList.add('hidden');

    if (!keepFeed) {
      $('feed').innerHTML = WELCOME_HTML;
      $('feed').classList.remove('has-events');
      document.body.classList.remove('task-active');  // back to the centered idle hero
      bindExamples();
      renderWelcomeHero();  // re-personalize the restored hero to the active project
      renderIdleSuggestions();  // re-apply recent-session chips after returning to idle
    }
  };

  const clearReconnectTimer = () => { if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; } };

  const updateCharCount = () => {
    const count = $('input').value.length;
    $('char-count').textContent = count ? `${count} chars` : '';
  };

  const autoGrow = () => {
    const box = $('input');
    box.style.height = 'auto';
    box.style.height = `${Math.min(box.scrollHeight, 180)}px`;
  };

  const showPostRunControls = () => {
    $('btn-pause').classList.add('hidden');
    $('btn-cancel').classList.add('hidden');
    $('btn-retry').classList.remove('hidden');
    $('btn-control-report').classList.remove('hidden');
    $('btn-copy-log').classList.remove('hidden');
    $('btn-download-log').classList.remove('hidden');
    $('send').classList.remove('hidden');
  };

  const statusPhase = (message = '') => {
    if (/reflecting/i.test(message)) return 'Reflecting';
    if (/evaluating/i.test(message)) return 'Evaluating';
    if (/re-planning/i.test(message)) return 'Re-planning';
    if (/thinking|planning/i.test(message)) return 'Planning';
    if (/executing sub-task/i.test(message)) return 'Executing';
    if (/initializing/i.test(message)) return 'Initializing';
    if (/paused/i.test(message)) return 'Paused';
    if (/resumed/i.test(message)) return 'Running';
    return 'Working';
  };

  const shouldReuseLiveStatus = (m = '') => /thinking|planning|reflecting|evaluating|executing sub-task|initializing|re-planning|still|scanning|sending request|model responded/i.test(m);

  const overlayControlLayer = (overlay) => {
    if (!overlay || typeof overlay !== 'object') return '';
    return String(overlay.control_layer || '').trim();
  };

  const processTaskEvent = (event, { replay = false, taskId = task, suppressToasts = false } = {}) => {
    // Any real (non-heartbeat) event is forward progress — keep the stall
    // watchdog quiet. Heartbeats deliberately don't count, so a model stuck in a
    // silent rate-limit backoff still trips the "still working" reassurance.
    if (!replay && !(event.type === 'status' && event.heartbeat)) noteProgress();
    if (event.type === 'task_created') return;

    if (event.type === 'reasoning') {
      // Live reasoning (thought tokens, composing) shows immediately via setLiveStatus.
      if (event.live) { renderReasoning(event); return; }
      // C1: non-live step announcements are noise — the turn summary covers them.
      if (_isStepAnnouncement(event)) return;
      finalizeTurnSummary(); renderReasoning(event); return;
    }
    if (event.type === 'plan') { clearTrustControls(actionCards.__plan__, '__plan__'); finalizeTurnSummary(); renderPlan(event); return; }

    if (event.type === 'status') {
      if (event.heartbeat) return;
      // C1: suppress internal/announce noise — the turn summary covers it.
      //  - "Executing <tool>: <args>" duplicates the turn summary.
      //  - "Thinking: model responded, parsing step N…" is parser-internal state.
      {
        const _m = String(event.message || '').trim();
        if (/^executing\b/i.test(_m)) return;
        if (/parsing step \d+/i.test(_m) || /model responded/i.test(_m)) return;
      }
      if (shouldReuseLiveStatus(event.message || '')) {
        setLiveStatus(statusPhase(event.message || ''), event.message || '', Number.isFinite(event.elapsed_seconds) ? `${event.elapsed_seconds}s` : '');
      } else {
        finalizeLiveStatus();
        appendMessage(event.message || 'Agent update', 'system-note');
      }
      return;
    }

    if (event.type === 'action_start') {
      finalizeLiveStatus();
      const layer = overlayControlLayer(event.overlay);
      const detail = event.args_summary || event.explanation || 'Working...';
      if (layer) setControlSurface({
        layer,
        reason: event.overlay?.control_reason || event.overlay?.fallback_reason || '',
        target: event.overlay?.target || event.overlay?.label || detail,
        phase: 'Running',
      });
      const entry = ensureActionCard(event.action_id, event.action_type, event.args_summary || event.explanation || '');
      clearTrustControls(entry, event.action_id || '');
      setActionState(entry, 'Running', 'running');
      entry.subtitleEl.textContent = layer ? `${layer} - ${detail}` : detail;
      renderControlTrace(entry, event.overlay, 'start');
      liveStatusMessage = layer ? `${layer}: ${detail}` : detail;
      lastActionId = event.action_id || null;
      return;
    }

    if (event.type === 'action_result') {
      const layer = overlayControlLayer(event.overlay);
      if (layer) setControlSurface({
        layer,
        reason: event.overlay?.control_reason || event.overlay?.fallback_reason || '',
        target: event.overlay?.target || event.overlay?.label || event.args_summary || '',
        phase: event.ok ? 'Complete' : 'Failed',
      });
      // Record real files the agent created/edited for the done-state capstone.
      if (event.ok) {
        const _t = String(event.action_type || '');
        if (_actionTypeLabel(_t) === 'file' && !/read_file|view_file/i.test(_t)) {
          const _p = _extractEditedPath(event.args_summary || '', _t);
          if (_p) noteEditedFile(_p, /delete/i.test(_t) ? 'deleted' : /create/i.test(_t) ? 'new' : 'edited');
        }
      }
      const entry = ensureActionCard(event.action_id, event.action_type, event.args_summary || '');
      clearTrustControls(entry, event.action_id || '');
      setActionState(entry, event.ok ? 'OK' : 'Fail', event.ok ? 'ok' : 'fail');
      if (event.args_summary) entry.subtitleEl.textContent = layer ? `${layer} - ${event.args_summary}` : event.args_summary;
      renderControlTrace(entry, event.overlay, 'result');
      if (event.output && !event.action_type?.match(/run_command|bash/)) {
        // C1: load the output but keep it collapsed — the turn timeline stays
        // a clean list of one-line rows; click a row to reveal its output.
        // Auto-open only on failure, where the error matters immediately.
        entry.output.classList.remove('hidden');
        entry.output.textContent = truncate(event.output, 1400);
        if (!event.ok) openEntryBody(entry);
      }
      return;
    }

    if (event.type === 'subtask') {
      if (event.status === 'running') {
        markSubtask(event.subtask_id, 'running');
        if (event.worker_id) {
          const row = subtaskEls[event.subtask_id];
          const textEl = row?.querySelector('.subtask-text');
          if (textEl && !textEl.querySelector('.worker-tag')) {
            const workerNum = String(event.worker_id).split('-').pop().replace(/[^A-Za-z0-9_-]/g, '');
            textEl.appendChild(document.createTextNode(' '));
            const tag = document.createElement('span');
            tag.className = `worker-tag worker-${workerNum}`;
            tag.textContent = event.worker_id;
            textEl.appendChild(tag);
          }
        }
      } else if (event.status === 'done') markSubtask(event.subtask_id, 'done');
      else if (event.status === 'failed') markSubtask(event.subtask_id, 'failed');
      return;
    }

    if (event.type === 'mode') { setMode(event.mode, !!event.isolated, event.isolated_app || ''); return; }

    if (event.type === 'control_profile') {
      setControlProfileSurface(event);
      setLiveStatus('Control route', `${event.primary_route || 'UIA exact'}${event.target_app ? ` for ${event.target_app}` : ''}`);
      return;
    }

    if (event.type === 'cowork_status') {
      currentBackgroundMode = !!event.background;
      if (!replay) appendMessage(event.message || '', 'system-note');
      return;
    }

    if (event.type === 'file_change') {
      const entry = lastActionId && actionCards[lastActionId] ? actionCards[lastActionId] : null;
      const preview = truncate(event.content || '', 1200);
      if (entry) appendDetailRow(entry, 'File change', event.path || 'Updated file', humanize(event.action || 'write_file'), preview);
      else renderStandaloneArtifact({ eyebrow: 'File change', title: event.path || 'Updated file', copy: humanize(event.action || 'write_file'), preview });
      return;
    }

    if (event.type === 'file_commit') {
      const entry = lastActionId && actionCards[lastActionId] ? actionCards[lastActionId] : null;
      const revertBtn = document.createElement('button');
      revertBtn.className = 'history-retask';
      revertBtn.textContent = '↩ Revert';
      revertBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        revertBtn.disabled = true;
        revertBtn.textContent = 'Reverting…';
        try {
          await api(`/api/tasks/${task?.id}/git/revert`, 'POST', { commit_hash: event.commit_hash });
          revertBtn.textContent = '✓ Reverted';
        } catch { revertBtn.textContent = '✗ Failed'; revertBtn.disabled = false; }
      });
      if (entry) {
        appendDetailRow(entry, 'Committed', event.commit_hash || '', event.path || '', '');
        const rows = entry.el?.querySelector('.detail-rows');
        if (rows) rows.appendChild(revertBtn);
      } else {
        renderStandaloneArtifact({ eyebrow: 'Git commit', title: event.path || '', copy: event.commit_hash || '' });
      }
      return;
    }

    if (event.type === 'terminal_output') {
      const target = (event.action_id && actionCards[event.action_id])
        ? actionCards[event.action_id]
        : (lastActionId && actionCards[lastActionId])
          ? actionCards[lastActionId]
          : ensureActionCard(event.action_id || `terminal-${Date.now()}`, 'run_command', event.command || '');
      if (event.command && !target.subtitleEl.textContent) target.subtitleEl.textContent = event.command;
      appendTerminalOutput(target, event.command || 'Command output', event.output || '', event.ok !== false, event.channel || 'stdout');
      return;
    }

    if (event.type === 'browser_event') {
      const label = event.kind === 'page_read' ? 'Read page' : humanize(event.kind || 'browser_event');
      const copy = event.url || event.selector || truncate(event.excerpt || '', 240);
      const entry = lastActionId && actionCards[lastActionId] ? actionCards[lastActionId] : null;
      if (entry) appendDetailRow(entry, 'Browser', label, copy);
      else renderStandaloneArtifact({ eyebrow: 'Browser', title: label, copy });
      return;
    }

    if (event.type === 'screenshot') { finalizeTurnSummary(); renderScreenshot(event); return; }
    if (event.type === 'reflection') { finalizeTurnSummary(); renderReflection(event); return; }

    if (event.type === 'token_usage' || event.type === 'budget') {
      if (Number.isFinite(event.percent)) setBudget(event.percent);
      return;
    }

    if (event.type === 'usage_update') {
      const tok = event.total_tokens || 0;
      const label = tok >= 1000 ? `${(tok / 1000).toFixed(1)}k tokens` : `${tok} tokens`;
      const activeItem = $('task-history')?.querySelector('.history-item.active');
      if (activeItem) {
        let badge = activeItem.querySelector('.usage-badge');
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'usage-badge';
          activeItem.querySelector('.history-copy')?.appendChild(badge);
        }
        badge.textContent = label;
      }
      return;
    }

    if (event.type === 'widget' || event.type === 'ui_widget') {
      finalizeTurnSummary();
      renderAgentWidget(event);
      return;
    }

    if (widgetRenderers[event.type]) {
      finalizeTurnSummary();
      renderAgentWidget({ ...event, widget: event.type });
      return;
    }

    if (event.type === 'provider_info') {
      // A rate-limit backoff carries a real "retrying in Ns…" message — show it so
      // the wait is explained. Otherwise keep it a calm "Thinking" (the raw model
      // id stays in the status bar / Settings for the curious).
      if (event.retrying && event.message) {
        setLiveStatus(String(event.message));
        noteProgress();  // an explicit retry notice is progress — don't double up with the stall hint
      } else {
        setLiveStatus('Thinking');
      }
      const sbm = $('sb-model-val'); if (sbm) sbm.textContent = event.tier || event.model || sbm.textContent;
      return;
    }

    if (event.type === 'approval_required') {
      finalizeTurnSummary();
      finalizeLiveStatus();
      const reason = event.reason || event.action?.explanation || 'High risk action requires approval.';
      const entry = ensureActionCard(event.action_id, event.action?.type || 'approval', reason);
      setActionState(entry, 'Waiting', 'waiting');
      appendDetailRow(entry, 'Approval', humanize(event.action?.type || 'Action'), reason, JSON.stringify(event.action?.args || {}, null, 2));

      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;gap:8px;margin-top:8px;width:100%';
      wrap.dataset.trustControls = '1';
      const btnApprove = document.createElement('button');
      btnApprove.className = 'modal-btn primary';
      btnApprove.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnApprove.textContent = 'Approve';
      const btnDeny = document.createElement('button');
      btnDeny.className = 'modal-btn';
      btnDeny.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnDeny.textContent = 'Deny';
      const doApp = (approve) => {
        const edit = $('app-plan-edit');
        const payload = { task_id: taskId, action_id: event.action_id, approve };
        if (edit && !edit.classList.contains('hidden')) payload.plan_override = edit.value;
        api('/api/approvals', 'POST', payload).catch(() => {});
        setActionState(entry, approve ? 'Approved' : 'Denied', approve ? 'ok' : 'fail');
        wrap.remove();
        $('approval').classList.remove('show');
      };
      btnApprove.onclick = (e) => { e.stopPropagation(); doApp(true); };
      btnDeny.onclick = (e) => { e.stopPropagation(); doApp(false); };
      wrap.appendChild(btnDeny); wrap.appendChild(btnApprove);
      entry.details.appendChild(wrap);
      openEntryBody(entry);

      if (!replay) {
        $('app-title').textContent = `${humanize(event.action?.type || 'Action')} needs approval`;
        $('app-reason').textContent = reason;
        $('app-code').textContent = JSON.stringify(event.action?.args || {}, null, 2);
        const planEdit = $('app-plan-edit');
        const isPlanReview = event.action?.type === 'plan_review' || event.action?.args?.plan_text;
        if (planEdit) {
          planEdit.classList.toggle('hidden', !isPlanReview);
          planEdit.value = isPlanReview ? (event.action?.args?.plan_text || '') : '';
        }
        $('approval').classList.add('show');
        window.pendingTaskId = taskId;
        window.pendingApprovalId = event.action_id;
        if (!suppressToasts) toast('Agent is waiting for approval.', 'warn', 5000);
      }
      return;
    }

    if (event.type === 'permission_required') {
      finalizeTurnSummary();
      finalizeLiveStatus();
      const detail = event.reason || event.explanation || `The agent needs ${event.scope || 'additional'} access.`;
      const entry = ensureActionCard(event.action_id, 'request_permission', detail);
      setActionState(entry, 'Waiting', 'waiting');
      appendDetailRow(entry, 'Permission', event.scope || 'Requested scope', detail, JSON.stringify({ scope: event.scope, explanation: event.explanation || '' }, null, 2));

      const pWrap = document.createElement('div');
      pWrap.style.cssText = 'display:flex;gap:8px;margin-top:8px;width:100%';
      pWrap.dataset.trustControls = '1';
      const btnAllow = document.createElement('button');
      btnAllow.className = 'modal-btn primary';
      btnAllow.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnAllow.textContent = 'Allow';
      const btnDenyP = document.createElement('button');
      btnDenyP.className = 'modal-btn';
      btnDenyP.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnDenyP.textContent = 'Deny';
      const doP = (grant) => {
        api('/api/permissions', 'POST', { task_id: taskId, action_id: event.action_id, grant, scope: event.scope }).catch(() => {});
        setActionState(entry, grant ? 'Allowed' : 'Denied', grant ? 'ok' : 'fail');
        pWrap.remove();
        $('permission').classList.remove('show');
      };
      btnAllow.onclick = (e) => { e.stopPropagation(); doP(true); };
      btnDenyP.onclick = (e) => { e.stopPropagation(); doP(false); };
      pWrap.appendChild(btnDenyP); pWrap.appendChild(btnAllow);
      entry.details.appendChild(pWrap);
      openEntryBody(entry);

      if (!replay) {
        $('perm-title').textContent = `Allow ${event.scope || 'access'}?`;
        $('perm-reason').textContent = detail;
        $('perm-code').textContent = JSON.stringify({ scope: event.scope, explanation: event.explanation || '' }, null, 2);
        $('permission').classList.add('show');
        window.pendingTaskId = taskId;
        window.pendingPermissionId = event.action_id;
        window.pendingPermissionScope = event.scope;
        if (!suppressToasts) toast('Agent is waiting on a permission choice.', 'warn', 5000);
      }
      return;
    }

    if (event.type === 'approval_timeout' || event.type === 'permission_timeout') {
      finalizeLiveStatus();
      const isApproval = event.type === 'approval_timeout';
      const entry = event.action_id && actionCards[event.action_id]
        ? actionCards[event.action_id]
        : ensureActionCard(event.action_id || `timeout-${Date.now()}`, isApproval ? 'approval' : 'request_permission', 'Timed out waiting for a response.');
      setActionState(entry, 'Timed out', 'fail');
      const seconds = Number.isFinite(event.timeout_seconds) ? `${event.timeout_seconds}s` : '';
      const title = isApproval ? 'Approval timed out' : 'Permission timed out';
      const copy = seconds
        ? `No response was received within ${seconds}.`
        : 'No response was received before the request expired.';
      appendDetailRow(entry, isApproval ? 'Approval' : 'Permission', title, copy);
      openEntryBody(entry);
      if (!replay) {
        if (isApproval) $('approval')?.classList.remove('show');
        else $('permission')?.classList.remove('show');
        if (!suppressToasts) toast(title, 'warn', 5000);
      }
      return;
    }

    if (event.type === 'error') {
      const errorKey = `error:${event.message || ''}`;
      if (terminalStateKey === errorKey) return;
      terminalStateKey = errorKey;
      finalizeTurnSummary();
      finalizeLiveStatus();
      clearLiveIndicators();
      setStatus('error');
      appendMessage(`Error: ${event.message}`, 'system-error');
      if (!replay) { markHistoryFinal('failed'); stopEverything(); }
      else showPostRunControls();
      return;
    }

    if (event.type === 'done') {
      const doneKey = `done:${event.complete ? '1' : '0'}:${event.reason || ''}:${event.blocked ? '1' : '0'}`;
      if (terminalStateKey === doneKey) return;
      terminalStateKey = doneKey;
      finalizeTurnSummary();
      finalizeLiveStatus();
      clearLiveIndicators();
      if (event.complete) {
        setStatus('complete');
        // Fold the working steps under a "Worked for Xm Ys ›" toggle (Codex).
        const elapsed = startTime ? (Date.now() - startTime) / 1000 : 0;
        summarizeWork(elapsed);
        renderFilesChanged();  // Codex-style "N files changed" capstone (if any)
        // Show the model's actual final reply as a primary assistant message.
        // Fall back to the generic note only when there's no real answer.
        const reply = String(event.reason || '').trim();
        const isRealAnswer = reply.length > 12 && !/^(done|complete|completed|finished|task complete|ok)\.?$/i.test(reply);
        if (isRealAnswer) attachMessageActions(appendMessage(reply, 'assistant'), { text: reply, taskId });
        else appendMessage('Task completed successfully.', 'system-success');
        if (!replay) { markHistoryFinal('done'); stopEverything(); if (!suppressToasts) toast('Task complete.', 'ok'); }
        else showPostRunControls();
      } else {
        setStatus('failed');
        renderFilesChanged();  // surface what was touched before the failure (if any)
        appendMessage(event.blocked ? `Request blocked: ${event.reason || 'This request could not be completed.'}` : `Task failed: ${event.reason || 'Unknown failure.'}`, 'system-error');
        if (!replay) { markHistoryFinal('failed'); stopEverything(); if (!suppressToasts) toast('Task failed.', 'err'); }
        else showPostRunControls();
      }
      return;
    }

    if (event.type === 'cancelled') {
      const key = `cancelled:${event.message || ''}`;
      if (terminalStateKey === key) return;
      terminalStateKey = key;
      finalizeTurnSummary();
      finalizeLiveStatus();
      clearLiveIndicators();
      setStatus('cancelled');
      appendMessage(event.message || 'Task was cancelled.', 'system-cancelled');
      if (!replay) { markHistoryFinal('cancelled'); stopEverything(); }
    }
  };

  const scheduleReconnect = (listenTask) => {
    clearReconnectTimer();
    reconnectAttempts += 1;
    // First attempt is near-instant: a stream close usually means the task
    // just finished — poll status fast so the UI never looks stuck "running".
    const delay = reconnectAttempts === 1 ? 500 : Math.min(3000 * reconnectAttempts, 15000);
    reconnectTimer = setTimeout(async () => {
      try {
        const record = await api(`/api/tasks/${listenTask}`);
        if (record.status && record.status !== 'running' && !record.paused) {
          if (record.status === 'done' || record.status === 'complete') processTaskEvent({ type: 'done', complete: true, reason: record.reason || '' }, { taskId: listenTask, suppressToasts: true });
          else if (record.status === 'failed' || record.status === 'error') processTaskEvent({ type: 'done', complete: false, reason: record.reason || '' }, { taskId: listenTask, suppressToasts: true });
          else if (record.status === 'cancelled') processTaskEvent({ type: 'cancelled', message: record.reason || 'Task was cancelled.' }, { taskId: listenTask, suppressToasts: true });
          return;
        }
      } catch (_) {}
      if (task === listenTask && !streamClosedManually) openStream(listenTask);
    }, delay);
  };

  const openStream = (listenTask) => {
    if (sse) { sse.close(); sse = null; }
    sse = new EventSource(`/api/tasks/${listenTask}/stream?since=${streamCursor}`, { withCredentials: true });
    sse.onmessage = (message) => {
      if (task !== listenTask) return;
      reconnectAttempts = 0;
      let data;
      try {
        data = JSON.parse(message.data);
      } catch (_) {
        return;
      }
      if (Number.isFinite(data.seq)) streamCursor = Math.max(streamCursor, data.seq + 1);
      else streamCursor += 1;
      processTaskEvent(data, { taskId: listenTask });
    };
    sse.onerror = () => {
      if (task !== listenTask || streamClosedManually) return;
      if (reconnectAttempts < 2) toast(`Stream interrupted. Reconnecting (attempt ${reconnectAttempts + 1}).`, 'warn', 2200);
      scheduleReconnect(listenTask);
    };
  };

  const start = async () => {
    const goal = $('input').value.trim();
    if (!goal) return;
    if (task && sse) { toast('A task is already running. Cancel it before starting another.', 'warn'); return; }
    const requestedMode = $('mode-id').value;
    const requestedIsolatedApp = ($('isolated-app-id').value || '').trim();
    const requestedModel = selectedModelForRequest(requestedMode);
    const readinessDecision = await ensureTaskReadiness({
      goal,
      mode: requestedMode,
      model: requestedModel,
      isolatedApp: requestedIsolatedApp,
    });
    if (!readinessDecision.ok) return;
    const effectiveMode = readinessDecision.preflight?.effective_mode || requestedMode;
    const effectiveIsolatedApp = requestedIsolatedApp || readinessDecision.preflight?.isolated_app || '';
    const displayModel = requestedModel || readinessDecision.preflight?.selected_model || null;
    if (isDesktopMode(effectiveMode)) {
      const allowed = await requestDesktopAccess({ mode: effectiveMode, isolatedApp: effectiveIsolatedApp });
      if (!allowed) return;
    }

    task = Math.random().toString(36).slice(2);
    currentViewedTask = task;
    resetTaskView();

    appendMessage(goal, 'user');
    $('input').value = ''; updateCharCount(); autoGrow();

    setTaskTitle(goal, { mode: effectiveMode, model: displayModel, status: 'running' });
    setStatus('running');
    $('btn-pause').classList.remove('hidden');
    $('btn-cancel').classList.remove('hidden');
    $('send').classList.add('hidden');

    $('btn-retry').classList.add('hidden');
    $('btn-copy-log').classList.add('hidden');
    $('btn-download-log').classList.add('hidden');
    activeHistoryItem = addActiveHistoryItem(goal);

    startTime = Date.now();
    timer = setInterval(updateClock, 1000);
    updateClock();
    setLiveStatus('Initializing', 'Starting task…');
    const taskPayload = {
      task_id: task,
      goal,
      model: requestedModel,
      mode: requestedMode,
      isolated_app: requestedIsolatedApp || null,
      active_skills: Array.from(activeSkillIds),
      project_folder: projectFolderState.selectedPath || null,
      plan_first: !!$('plan-first-toggle')?.checked,
      notify_on_completion: !!$('notify-toggle')?.checked,
      auto_commit: !!$('checkpoint-toggle')?.checked,
      autonomy_level: $('autonomy-level')?.value || 'balanced',
      thinking_budget: $('thinking-budget')?.value || 'off',
      readiness_override: !!readinessDecision.override
    };

    try {
      await keyReady;
      if (isDesktopMode(effectiveMode)) setDesktopSessionActive(true, effectiveMode, effectiveIsolatedApp || '');
      streamClosedManually = false;
      openStream(task);
      await api('/api/tasks', 'POST', taskPayload);
      if (window.innerWidth <= 1080) document.body.classList.remove('nav-open');
    } catch (err) {
      try {
        const outcome = await handleServerPreflightRejection(err, taskPayload);
        if (outcome === 'retried') {
          if (window.innerWidth <= 1080) document.body.classList.remove('nav-open');
          return;
        }
        if (outcome === 'cancelled') {
          finalizeLiveStatus();
          setStatus('ready');
          appendMessage('Task was not started. Fix readiness checks in Settings and try again.', 'system');
          markHistoryFinal('cancelled');
          stopEverything();
          return;
        }
      } catch (retryErr) {
        err = retryErr;
      }
      finalizeLiveStatus();
      setStatus('error');
      const detail = (typeof err.detail === 'object') ? JSON.stringify(err.detail) : (err.detail || 'Unknown error.');
      appendMessage(`Failed to start task: ${detail}`, 'system-error');
      markHistoryFinal('failed');
      showPostRunControls();
    }
  };

  // Clear lingering in-progress indicators — runs on BOTH live completion
  // and history replay, so a finished task never shows a stuck "active" dot.
  const clearLiveIndicators = () => {
    document.querySelectorAll('.feed-card.is-active').forEach(c => c.classList.remove('is-active'));
    lastActiveCard = null;
    // Any tool chip still stuck on "running" never got an action_result — mark it done.
    document.querySelectorAll('.state-chip.running').forEach(chip => {
      chip.classList.remove('running');
      chip.classList.add('ok');
      chip.textContent = 'Done';
    });
  };

  const stopEverything = () => {
    clearInterval(timer);
    disarmStallWatch();
    clearReconnectTimer();
    streamClosedManually = true;
    if (sse) { sse.close(); sse = null; }
    finalizeLiveStatus();
    setDesktopSessionActive(false);
    clearLiveIndicators();
    showPostRunControls();
  };

  const cancelTask = async () => {
    try { await api(`/api/tasks/${task}`, 'DELETE'); } catch (_) {}
    processTaskEvent({ type: 'cancelled', message: 'Task cancelled by user.' }, { suppressToasts: true });
    stopEverything();
  };

  const togglePause = async () => {
    if (!task) return;
    isPaused = !isPaused;
    await api(`/api/tasks/${task}/${isPaused ? 'pause' : 'resume'}`, 'POST').catch(() => {});
    $('btn-pause').textContent = isPaused ? 'Resume' : 'Pause';
    setStatus(isPaused ? 'paused' : 'running');
    setLiveStatus(isPaused ? 'Paused' : 'Running', isPaused ? 'Task is waiting.' : 'Task resumed.');
  };

  const retryTask = async () => {
    if (!currentViewedTask) { toast('No task selected to retry.', 'warn'); return; }
    const originalTaskId = currentViewedTask;
    const title = $('task-title').textContent || 'Retry';
    const startRetriedTask = (result) => {
      const preflight = result.preflight || {};
      const effectiveMode = preflight.effective_mode || result.mode || $('mode-id').value;
      const effectiveIsolatedApp = preflight.isolated_app || '';
      const displayModel = preflight.selected_model || result.model || $('model-id').value;
      task = result.task_id; currentViewedTask = task;
      resetTaskView();
      appendMessage(title, 'user');
      setTaskTitle(title, { mode: effectiveMode, model: displayModel, status: 'running' });
      setStatus('running');
      $('btn-pause').classList.remove('hidden');
      $('btn-cancel').classList.remove('hidden');
      $('send').classList.add('hidden');

      activeHistoryItem = addActiveHistoryItem(title);
      startTime = Date.now();
      timer = setInterval(updateClock, 1000);
      updateClock();
      setLiveStatus('Initializing', 'Retrying task...');
      if (isDesktopMode(effectiveMode)) setDesktopSessionActive(true, effectiveMode, effectiveIsolatedApp || '');
      streamClosedManually = false;
      openStream(task);
      toast(`Retried as ${task}.`, 'ok');
    };
    try {
      let readinessOverride = false;
      try {
        const record = await api(`/api/tasks/${originalTaskId}`);
        const retryGoal = record.goal || record.context?.goal || title;
        const retryMode = record.mode || 'auto';
        const retryModel = record.model || null;
        const retryIsolatedApp = record.context?.isolated_app || '';
        const readinessDecision = await ensureTaskReadiness({
          goal: retryGoal,
          mode: retryMode,
          model: retryModel,
          isolatedApp: retryIsolatedApp,
        });
        if (!readinessDecision.ok) {
          toast('Retry was not started.', 'warn');
          return;
        }
        readinessOverride = !!readinessDecision.override;
        const effectiveMode = readinessDecision.preflight?.effective_mode || retryMode;
        const effectiveIsolatedApp = retryIsolatedApp || readinessDecision.preflight?.isolated_app || '';
        if (isDesktopMode(effectiveMode)) {
          const allowed = await requestDesktopAccess({ mode: effectiveMode, isolatedApp: effectiveIsolatedApp });
          if (!allowed) return;
        }
      } catch (_) {
        // The server retry endpoint still performs the authoritative preflight.
      }
      const result = await api(`/api/tasks/${originalTaskId}/retry`, 'POST', { readiness_override: readinessOverride });
      startRetriedTask(result);
      return;
    } catch (err) {
      const detail = err?.detail;
      const code = detail && typeof detail === 'object' ? detail.code : '';
      if ((code === 'readiness_preflight_warning' || code === 'readiness_preflight_blocked') && detail.preflight) {
        const allowed = await requestReadinessPreflight(detail.preflight);
        if (allowed && code === 'readiness_preflight_warning' && detail.preflight?.can_override) {
          try {
            const result = await api(`/api/tasks/${originalTaskId}/retry`, 'POST', { readiness_override: true });
            startRetriedTask(result);
            return;
          } catch (retryErr) {
            err = retryErr;
          }
        } else {
          toast('Retry was not started.', 'warn');
          return;
        }
      }
      const message = typeof err.detail === 'object' ? (err.detail.message || JSON.stringify(err.detail)) : (err.detail || 'Retry failed.');
      toast(message, 'err');
    }
  };

  const copyCurrentLog = async () => {
    if (!currentViewedTask) { toast('No task selected.', 'warn'); return; }
    try {
      const result = await api(`/api/tasks/${currentViewedTask}/log`);
      await navigator.clipboard.writeText(JSON.stringify(result.log || [], null, 2));
      toast('Copied log to clipboard.', 'ok');
    } catch (_) { toast('Could not copy the task log.', 'err'); }
  };

  const downloadCurrentLog = () => {
    if (!currentViewedTask) { toast('No task selected.', 'warn'); return; }
    const link = document.createElement('a');
    link.href = `/api/tasks/${currentViewedTask}/log/download`;
    link.download = `${currentViewedTask}.jsonl`;
    document.body.appendChild(link);
    link.click(); link.remove();
  };

  const showControlReport = async () => {
    if (!currentViewedTask) { toast('No task selected.', 'warn'); return; }
    try {
      const report = await api(`/api/tasks/${currentViewedTask}/control-trace`);
      const summary = report.summary || {};
      const entries = Array.isArray(report.entries) ? report.entries : [];
      const card = createFeedCard('control-report-card');
      const parts = createCardHead({
        eyebrow: 'Evidence',
        title: 'Control report',
        subtitle: `${summary.trace_events || 0} trace events · ${summary.primary_layer || 'No control layer'}`,
        stateLabel: summary.failures ? `${summary.failures} Fail` : 'Verified',
        stateClass: summary.failures ? 'fail' : 'ok',
      });
      card.appendChild(parts.head);
      const body = document.createElement('div');
      body.className = 'card-body';
      const inner = document.createElement('div');
      inner.className = 'card-body-inner';
      const metrics = document.createElement('div');
      metrics.className = 'control-report-grid';
      [
        ['Planned', summary.profile_route || 'None'],
        ['Primary', summary.primary_layer || 'None'],
        ['Target', summary.profile_target_app || 'Desktop'],
        ['Route match', summary.profile_route ? (summary.used_profile_route ? 'Yes' : (summary.route_changed ? 'Changed' : 'No actions')) : 'n/a'],
        ['UIA', summary.used_uia ? 'Used' : 'No'],
        ['OCR ready', summary.profile_ocr_available ? 'Yes' : 'No'],
        ['Fallbacks', String(summary.fallbacks || 0)],
        ['Misses', String(summary.misses || 0)],
        ['Success', String(summary.successes || 0)],
        ['Failures', String(summary.failures || 0)],
      ].forEach(([label, value]) => {
        const item = document.createElement('div');
        item.className = 'control-report-metric';
        const k = document.createElement('span');
        k.textContent = label;
        const v = document.createElement('b');
        v.textContent = value;
        item.appendChild(k);
        item.appendChild(v);
        metrics.appendChild(item);
      });
      inner.appendChild(metrics);
      const detail = document.createElement('pre');
      detail.className = 'detail-preview control-report-json';
      detail.textContent = JSON.stringify({ summary, profiles: report.profiles || [], entries: entries.slice(0, 24) }, null, 2);
      inner.appendChild(detail);
      body.appendChild(inner);
      card.appendChild(body);
      toast('Control report loaded.', 'ok');
    } catch (_) {
      toast('Could not load control report.', 'err');
    }
  };

  const loadTaskLog = (taskId, events = [], sourceEl, { live = false, record = null, silent = false } = {}) => {
    if (live) task = taskId;
    currentViewedTask = taskId;
    resetTaskView({ replay: !live });
    activateHistoryItem(sourceEl || null);
    requestAnimationFrame(() => { const fs = $('feed-scroll'); if (fs) fs.scrollTop = 0; });

    const createdEvent = events.find((e) => e.type === 'task_created');
    const meta = activeTaskMeta(record || {}, events);
    const firstGoal = createdEvent?.goal || meta.goal;
    const title = sourceEl?.querySelector('.history-goal')?.textContent || firstGoal || 'Past task';
    if (live) setMode(meta.mode, meta.mode === 'computer_isolated' || !!meta.isolatedApp, meta.isolatedApp);
    setTaskTitle(title, { mode: live ? meta.mode : createdEvent?.mode, model: live ? meta.model : createdEvent?.model, status: live ? 'running' : '' });
    appendMessage(title, 'user');
    if (!live) {
      $('btn-retry').classList.remove('hidden');
      $('btn-control-report').classList.remove('hidden');
      $('btn-copy-log').classList.remove('hidden');
      $('btn-download-log').classList.remove('hidden');
    }

    events.forEach((e) => processTaskEvent(e, { replay: true, taskId, suppressToasts: true }));
    if (live) {
      streamCursor = streamCursorAfter(events);
      showLiveTaskControls(record || { status: 'running' }, meta);
      restorePendingTrustModal(pendingTrustRequest(events), taskId);
      const startedAt = Date.parse(meta.createdAt || '');
      startTime = Number.isFinite(startedAt) ? startedAt : Date.now();
      timer = setInterval(updateClock, 1000);
      updateClock();
      const rawStatus = String(record?.status || 'running').toLowerCase();
      if (rawStatus === 'queued' || rawStatus === 'pending') setLiveStatus('Queued', 'Waiting for an available task slot.');
      else if (record?.paused || rawStatus === 'paused') setLiveStatus('Paused', 'Task is waiting.');
      else setLiveStatus('Reconnected', 'Live task controls restored.');
    } else if (!silent) {
      toast('Loaded task log.', 'info', 1800);
    }
  };

  const recoverActiveTask = async () => {
    if (task && sse) return false;
    let activePayload;
    try {
      activePayload = await api(`/api/active-tasks?cb=${Date.now()}`);
    } catch (_) {
      return false;
    }
    const activeTasks = (activePayload.tasks || [])
      .filter((record) => taskRecordId(record) && !isTerminalStatus(record.status || ''))
      .sort((a, b) => (Date.parse(a.created_at || '') || 0) - (Date.parse(b.created_at || '') || 0));
    const record = activeTasks[activeTasks.length - 1];
    if (!record) return false;

    const activeId = taskRecordId(record);
    let item = findHistoryItem(activeId);
    if (!item) {
      item = renderHistoryItem({
        id: activeId,
        goal: record.goal || record.context?.goal || 'Running task',
        status: record.status || 'running',
        created_at: record.created_at,
        mode: record.mode,
        model: record.model,
        context: record.context,
      }, true);
      refreshHistoryCount();
    }
    activateHistoryItem(item);

    let events = [];
    try {
      const log = await api(`/api/tasks/${activeId}/log`);
      events = log.log || [];
    } catch (_) {}

    loadTaskLog(activeId, events, item, { live: true, record, silent: true });
    streamClosedManually = false;
    openStream(activeId);
    toast('Reconnected to running task.', 'ok', 2400);
    return true;
  };

  const hydrateModelSelect = (models) => {
    const select = $('model-id');
    const list = Array.isArray(models) ? models : [
      'openrouter/qwen/qwen3-coder:free',
      'openrouter/nvidia/nemotron-3-super-120b-a12b:free',
      'openrouter/meta-llama/llama-3.3-70b-instruct:free',
      'claude-3-5-sonnet-20241022',
      'gpt-4o-mini'
    ];
    modelSelectionTouched = false;
    select.innerHTML = '';
    if (!list.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No configured models';
      select.appendChild(option);
      const sbm = $('sb-model-val'); if (sbm) sbm.textContent = option.textContent;
      return;
    }
    // Speed tiers first — the recommended way to choose. Each is a curated
    // free-model fallback chain. Raw models stay below for power users.
    [
      { value: 'tier:balanced', label: '⚖  Balanced — best free quality' },
      { value: 'tier:quick', label: '⚡  Quick — fast, lighter' },
    ].forEach((tier) => {
      const option = document.createElement('option');
      option.value = tier.value;
      option.textContent = tier.label;
      select.appendChild(option);
    });
    list.forEach((model) => {
      const option = document.createElement('option');
      option.value = model;
      let label = model.replace('openrouter/', '');
      if (label.includes('/')) label = label.split('/').slice(-1)[0];
      option.textContent = label;
      select.appendChild(option);
    });
    if (!select.value && select.options.length) select.value = 'tier:balanced';
    selectPreferredModelForMode($('mode-id')?.value || 'auto');
    const sbm = $('sb-model-val'); if (sbm) sbm.textContent = select.options[select.selectedIndex]?.textContent || '—';
    select.onchange = () => {
      modelSelectionTouched = true;
      const sbm = $('sb-model-val');
      if (sbm) sbm.textContent = select.options[select.selectedIndex]?.textContent || '—';
    };
  };

  const newSession = () => {
    if (task && sse) { toast('Cancel the running task before starting fresh.', 'warn'); return; }
    task = null; currentViewedTask = null; activeHistoryItem = null;
    historyItems.forEach((item) => item.classList.remove('active'));
    resetTaskView();
    setTaskTitle();
    $('input').value = ''; updateCharCount(); autoGrow(); $('input').focus();
  };

  const sendApproval = (approve) => {
    const edit = $('app-plan-edit');
    const payload = { task_id: window.pendingTaskId, action_id: window.pendingApprovalId, approve };
    if (edit && !edit.classList.contains('hidden')) payload.plan_override = edit.value;
    api('/api/approvals', 'POST', payload).catch(() => {});
    $('approval').classList.remove('show');
  };
  const sendPermission = (grant) => {
    api('/api/permissions', 'POST', { task_id: window.pendingTaskId, action_id: window.pendingPermissionId, grant, scope: window.pendingPermissionScope }).catch(() => {});
    $('permission').classList.remove('show');
  };
  const resolveDesktopAccess = (allow) => {
    $('desktop-access').classList.remove('show');
    if (desktopAccessResolver) desktopAccessResolver(allow);
    desktopAccessResolver = null;
  };

  /* ================================================================
     DEMO STREAM — optional, off by default. Backend drives the real UI.
     ================================================================ */
  let demoTimers = [];
  const delay = (t) => new Promise((r) => demoTimers.push(setTimeout(r, t)));
  const clearDemoStream = () => {
    demoTimers.forEach((t) => clearTimeout(t));
    demoTimers = [];
    resetTaskView();
    setBudget(tweakState.budgetPct);
  };

  async function playDemoStream() {
    clearDemoStream();
    task = 'demo-' + Math.random().toString(36).slice(2, 8);
    currentViewedTask = task;
    resetTaskView();
    const goal = 'Bootstrap a FastAPI dashboard with SQLite, auth, and a test harness.';
    appendMessage(goal, 'user');
    setTaskTitle(goal, { status: 'running' });
    setStatus('running');
    addActiveHistoryItem(goal);
    startTime = Date.now();
    timer = setInterval(updateClock, 1000);
    updateClock();
    setLiveStatus('Initializing', 'Starting task…');

    await delay(600);
    setLiveStatus('Planning', 'Drafting sub-tasks and file plan.', '3s');

    let b = tweakState.budgetPct;
    const budgetInterval = setInterval(() => {
      b = Math.min(96, b + 1.4);
      setBudget(b);
    }, 800);
    demoTimers.push(budgetInterval);

    await delay(1200);
    processTaskEvent({
      type: 'plan',
      reasoning: 'Scaffold the project, wire SQLite + JWT, then ship a vitest-style harness. Keep each step small.',
      sub_tasks: [
        { id: 's1', description: 'Scaffold FastAPI project (app/, requirements.txt, .env)' },
        { id: 's2', description: 'Add SQLite + SQLAlchemy models and migrations' },
        { id: 's3', description: 'Implement /auth/login with JWT & bcrypt' },
        { id: 's4', description: 'Write pytest harness + two CRUD tests' },
        { id: 's5', description: 'Run tests and verify 200s' }
      ]
    });

    await delay(700);
    processTaskEvent({ type: 'subtask', subtask_id: 's1', status: 'running', worker_id: 'worker-1' });
    await delay(500);
    processTaskEvent({ type: 'reasoning', stage: 'Thinking', summary: 'Start from a minimal FastAPI layout', detail: 'Create app/main.py + router/ + schemas/. Avoid premature abstraction.', elapsed_seconds: 2, worker_id: 'worker-1' });

    const demoUiaOverlayStart = {
      type: 'status',
      tool: 'uia_find',
      kind: 'find',
      phase: 'start',
      label: 'Locating editor',
      target: 'editor',
      app: 'Workspace',
      control_layer: 'UIA exact',
      control_reason: 'querying Windows accessibility tree',
    };
    const demoUiaOverlayResult = {
      type: 'uia_control',
      tool: 'uia_find',
      kind: 'find',
      phase: 'result',
      label: 'Found editor',
      target: 'editor',
      rect: { left: 420, top: 180, width: 640, height: 420 },
      app_rect: { left: 300, top: 90, width: 900, height: 720 },
      control_layer: 'UIA exact',
      control_reason: 'Windows accessibility tree',
    };
    processTaskEvent({ type: 'action_start', action_id: 'a0', action_type: 'uia_find', args_summary: 'editor', overlay: demoUiaOverlayStart });
    await delay(350);
    processTaskEvent({ type: 'action_result', action_id: 'a0', action_type: 'uia_find', ok: true, args_summary: 'editor', overlay: demoUiaOverlayResult, output: 'Found editor by accessible name.' });

    await delay(500);
    processTaskEvent({ type: 'action_start', action_id: 'a1', action_type: 'write_file', args_summary: 'app/main.py · 42 lines' });
    await delay(800);
    processTaskEvent({ type: 'file_change', action: 'write_file', path: 'app/main.py', content: 'from fastapi import FastAPI\nfrom .router import api\n\napp = FastAPI(title="Orynn")\napp.include_router(api, prefix="/api")\n' });
    processTaskEvent({ type: 'action_result', action_id: 'a1', action_type: 'write_file', ok: true, args_summary: 'app/main.py · wrote 42 lines' });

    await delay(400);
    processTaskEvent({ type: 'subtask', subtask_id: 's1', status: 'done' });
    processTaskEvent({ type: 'subtask', subtask_id: 's2', status: 'running', worker_id: 'worker-2' });

    await delay(400);
    processTaskEvent({ type: 'action_start', action_id: 'a2', action_type: 'run_command', args_summary: 'pip install fastapi uvicorn sqlalchemy' });
    await delay(400);
    processTaskEvent({ type: 'terminal_output', action_id: 'a2', command: 'pip install fastapi uvicorn sqlalchemy', output: 'Collecting fastapi\n  Using cached fastapi-0.115.2-py3-none-any.whl\n', channel: 'stdout', ok: true });
    await delay(500);
    processTaskEvent({ type: 'terminal_output', action_id: 'a2', command: 'pip install fastapi uvicorn sqlalchemy', output: 'Collecting sqlalchemy\n  Using cached SQLAlchemy-2.0.36-py3-none-any.whl\nSuccessfully installed fastapi-0.115.2 sqlalchemy-2.0.36 uvicorn-0.32.0\n', channel: 'stdout', ok: true });
    processTaskEvent({ type: 'action_result', action_id: 'a2', action_type: 'run_command', ok: true, args_summary: 'install · 3 packages · 7.2s' });
    processTaskEvent({ type: 'subtask', subtask_id: 's2', status: 'done' });

    await delay(400);
    processTaskEvent({ type: 'subtask', subtask_id: 's3', status: 'running', worker_id: 'worker-3' });
    processTaskEvent({ type: 'approval_required', action_id: 'a3', action: { type: 'run_command', args: { cmd: 'openssl rand -hex 32' }, explanation: 'Generate JWT signing secret.' }, reason: 'Writes a secret to .env — approve before running.' });

    await delay(1600);
    // simulate approval
    const approvalEntry = actionCards['a3'];
    if (approvalEntry) setActionState(approvalEntry, 'Approved', 'ok');
    $('approval').classList.remove('show');

    await delay(400);
    processTaskEvent({ type: 'action_start', action_id: 'a4', action_type: 'write_file', args_summary: 'app/auth.py · JWT + bcrypt' });
    await delay(500);
    processTaskEvent({ type: 'file_change', action: 'write_file', path: 'app/auth.py', content: 'from datetime import datetime, timedelta\nfrom jose import jwt\nimport bcrypt\n\nSECRET = os.environ["JWT_SECRET"]\n\ndef hash_password(pw: str) -> str:\n    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()\n\ndef make_token(user_id: str) -> str:\n    return jwt.encode({"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=12)}, SECRET, algorithm="HS256")\n' });
    processTaskEvent({ type: 'action_result', action_id: 'a4', action_type: 'write_file', ok: true, args_summary: 'app/auth.py · wrote 28 lines' });
    processTaskEvent({ type: 'subtask', subtask_id: 's3', status: 'done' });

    await delay(400);
    processTaskEvent({ type: 'subtask', subtask_id: 's4', status: 'running', worker_id: 'worker-4' });
    processTaskEvent({ type: 'reasoning', stage: 'Reflecting', summary: 'Test harness should not depend on a live DB', detail: 'Use sqlite in-memory + override get_db. Two tests only: login happy path + 401.', elapsed_seconds: 4, worker_id: 'worker-4' });

    await delay(500);
    processTaskEvent({ type: 'action_start', action_id: 'a5', action_type: 'run_command', args_summary: 'pytest -q' });
    await delay(500);
    processTaskEvent({ type: 'terminal_output', action_id: 'a5', command: 'pytest -q', output: '..\n2 passed in 0.41s\n', channel: 'stdout', ok: true });
    processTaskEvent({ type: 'action_result', action_id: 'a5', action_type: 'run_command', ok: true, args_summary: '2 passed · 0.41s' });
    processTaskEvent({ type: 'subtask', subtask_id: 's4', status: 'done' });
    processTaskEvent({ type: 'subtask', subtask_id: 's5', status: 'done' });

    await delay(400);
    processTaskEvent({ type: 'reflection', success: true, reason: 'All 5 sub-tasks completed. Tests green.', worker_id: 'worker-1' });

    await delay(400);
    clearInterval(budgetInterval);
    processTaskEvent({ type: 'done', complete: true });
  }

  async function playWidgetGallery() {
    clearDemoStream();
    task = 'widgets-' + Math.random().toString(36).slice(2, 8);
    currentViewedTask = task;
    resetTaskView();
    const goal = 'Show the full dynamic widget library for an AI desktop agent.';
    appendMessage(goal, 'user');
    setTaskTitle(goal, { status: 'running' });
    setStatus('running');
    activeHistoryItem = addActiveHistoryItem(goal);
    startTime = Date.now();
    timer = setInterval(updateClock, 1000);
    updateClock();
    setLiveStatus('Composing UI', 'Selecting the right widgets for the task context.', '1s');

    const show = async (widget, title, subtitle, data = {}, state = 'Ready') => {
      await delay(220);
      processTaskEvent({ type: 'widget', widget, title, subtitle, data, state });
    };

    await show('source_grid', 'Research sources', 'Evidence cards the agent can cite and reopen.');
    await show('data_table', 'GPU comparison', 'Sortable, filterable rows for live research results.');
    await show('resource_radar', 'Resource radar', 'Live system pressure with process-level controls.');
    await show('network_guardian', 'Network guardian', 'Traffic by process, with fast containment actions.');
    await show('quick_settings', 'Focus setup', 'System toggles the agent can propose together.');
    await show('clutter_sweeper', 'Clutter sweeper', 'Reviewable cleanup candidates before deletion.');
    await show('smart_organizer', 'Smart organizer', 'AI-suggested file moves with confidence scores.');
    await show('file_preview', 'Document preview', 'Inline file reading without leaving the stream.');
    await show('email_summary', 'Context summary', 'Screen or email summaries with response chips.');
    await show('action_approver', 'Action approver', 'Inline safety review for destructive commands.');

    await delay(300);
    processTaskEvent({
      type: 'done',
      complete: true,
      reason: 'Widget gallery rendered. The dashboard now has reusable stream widgets for cleanup, organization, previews, system monitoring, settings, network activity, approvals, summaries, sources, and data tables.'
    });
  }

  window.__aiComputerPlayWidgetGallery = playWidgetGallery;
  window.__aiComputerPlayDemoStream = playDemoStream;
  window.__aiComputerClearDemo = clearDemoStream;

  /* ---------------- init ---------------- */
  const init = async () => {
    applyTweaks();
    buildTweaks();
    syncTweaks();
    bindExamples();
    hydrateModelSelect();
    const _savedMode = localStorage.getItem('orynn_mode') || localStorage.getItem('ai_computer_mode');
    const _modeSelect = $('mode-id');
    if (_savedMode && _modeSelect && Array.from(_modeSelect.options).some((o) => o.value === _savedMode)) {
      _modeSelect.value = _savedMode;
    }
    setMode($('mode-id').value || 'auto');
    setBudget(tweakState.budgetPct);

    keyReady = fetch(`/api/session?cb=${Date.now()}`, { method: 'POST', credentials: 'same-origin' })
      .then((r) => r.json())
      .then(() => { KEY = ''; return KEY; })
      .catch(() => '');

    try {
      await keyReady;
      setProjectFolder(storedProjectFolder() || '', { persist: false });
      loadFolderShortcuts();  // prime the picker's quick folders (non-blocking)
      api(`/api/models?cb=${Date.now()}`).then((r) => hydrateModelSelect(r.models)).catch(() => {});
      refreshProviderChips();
      await api(`/api/tasks?cb=${Date.now()}`).then((r) => {
        const tasks = [...(r.tasks || [])].reverse();
        if (tasks.length) { $('task-history').innerHTML = ''; historyItems = []; historyExpandedGroups.clear(); }
        suppressHistoryReflow = true;
        tasks.forEach((it) => renderHistoryItem(it));
        suppressHistoryReflow = false;
        reflowHistoryGroups();
        refreshHistoryCount();
        renderIdleSuggestions();  // returning users: show recent sessions as quick-resume chips
      }).catch(() => { suppressHistoryReflow = false; });
      await recoverActiveTask();
      loadSkills();
      loadReadiness();
      loadTrustReport();
      loadMCP();
      loadCodingBackends();
    } catch (_) {}
  };

  const refreshProviderChips = async () => {
    const container = $('provider-chips');
    if (!container) return;
    try {
      const data = await api('/healthz');
      const providers = data.providers || {};
      container.innerHTML = '';
      Object.entries(providers).forEach(([name, status]) => {
        const chip = document.createElement('span');
        chip.className = `provider-chip${status === 'ok' ? ' ok' : ''}`;
        chip.title = `${name}: ${status}`;
        const dot = document.createElement('span');
        dot.className = 'chip-dot';
        const label = document.createElement('span');
        label.textContent = name;
        chip.append(dot, label);
        container.appendChild(chip);
      });
    } catch (_) {}
  };
  setInterval(refreshProviderChips, 60000);

  let allSkills = [];
  let activeSkillIds = new Set();

  const loadSkills = async () => {
    try {
      const r = await fetch('/api/skills');
      const d = await r.json();
      allSkills = d.skills || [];
      renderSkills();
    } catch (e) { console.error("Failed to load skills", e); }
  };

  const renderSkills = () => {
    const grid = $('skills-grid');
    if (!grid) return;
    grid.innerHTML = '';
    allSkills.forEach((s) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `skill-item ${activeSkillIds.has(s.id) ? 'active' : ''}`;
      item.addEventListener('click', () => toggleSkill(s.id));

      const info = document.createElement('div');
      info.className = 'skill-info';
      const name = document.createElement('div');
      name.className = 'skill-name';
      name.textContent = s.name || s.id || 'Skill';
      const desc = document.createElement('div');
      desc.className = 'skill-desc';
      desc.textContent = s.description || '';
      info.append(name, desc);

      const toggle = document.createElement('div');
      toggle.className = 'skill-toggle';
      item.append(info, toggle);
      grid.appendChild(item);
    });
    $('skill-count').textContent = activeSkillIds.size;
  };

  window.toggleSkill = (id) => {
    if (activeSkillIds.has(id)) activeSkillIds.delete(id);
    else activeSkillIds.add(id);
    renderSkills();
  };

  let readinessState = { checks: [], overall: 'unknown', score: 0, summary: {} };
  let allMCPServers = [];
  let codingBackendState = { backends: [], default: '' };

  const READINESS_LABELS = {
    ready: 'Ready',
    warning: 'Check',
    blocked: 'Blocked',
    unavailable: 'N/A',
  };

  const loadReadiness = async () => {
    try {
      await keyReady;
      readinessState = await api('/api/readiness');
      renderReadiness();
    } catch (e) {
      readinessState = {
        checks: [{
          key: 'readiness_error',
          label: 'Readiness',
          status: 'warning',
          detail: 'Could not load local capability checks.',
          category: 'core',
        }],
        overall: 'warning',
        score: 0,
        summary: {},
      };
      renderReadiness();
    }
  };

  const renderReadiness = () => {
    const grid = $('readiness-grid');
    const score = $('readiness-score');
    if (!grid) return;
    grid.innerHTML = '';
    const checks = Array.isArray(readinessState.checks) ? readinessState.checks : [];
    if (score) {
      const value = Number.isFinite(readinessState.score) ? readinessState.score : 0;
      score.textContent = `${value}%`;
      score.dataset.status = readinessState.overall || 'unknown';
    }
    checks.forEach((check) => {
      const item = document.createElement('div');
      const status = check.status || 'warning';
      item.className = `readiness-item ${status}`;
      item.dataset.category = check.category || 'core';

      const dot = document.createElement('span');
      dot.className = 'readiness-dot';

      const info = document.createElement('div');
      info.className = 'readiness-info';
      const name = document.createElement('div');
      name.className = 'readiness-name';
      name.textContent = check.label || check.key || 'Capability';
      const detail = document.createElement('div');
      detail.className = 'readiness-detail';
      detail.textContent = check.detail || check.fix || '';
      info.append(name, detail);

      const badge = document.createElement('span');
      badge.className = 'readiness-status';
      badge.textContent = READINESS_LABELS[status] || status;

      item.append(dot, info, badge);
      grid.appendChild(item);
    });
  };

  let trustReportState = { overall: 'unknown', pending_trust: {}, consent_ledger: [], active_tasks: [] };

  const TRUST_LABELS = {
    ready: 'Ready',
    attention: 'Attention',
    warning: 'Check',
    blocked: 'Blocked',
    unknown: '--',
  };

  const trustStatusClass = (value = '') => {
    const key = String(value || '').toLowerCase();
    return ['ready', 'attention', 'warning', 'blocked'].includes(key) ? key : 'warning';
  };

  const trustList = (value) => Array.isArray(value) ? value : [];

  const makeTrustCard = ({ label, value, detail, status = 'ready' }) => {
    const card = document.createElement('div');
    card.className = `trust-card ${trustStatusClass(status)}`;

    const labelEl = document.createElement('div');
    labelEl.className = 'trust-card-label';
    labelEl.textContent = label;

    const valueEl = document.createElement('div');
    valueEl.className = 'trust-card-value';
    valueEl.textContent = value;

    const detailEl = document.createElement('div');
    detailEl.className = 'trust-card-detail';
    detailEl.textContent = detail;

    card.append(labelEl, valueEl, detailEl);
    return card;
  };

  const makeTrustEmpty = (text) => {
    const empty = document.createElement('div');
    empty.className = 'trust-empty';
    empty.textContent = text;
    return empty;
  };

  const makeTrustRow = ({ title, detail, badge, actions = [] }) => {
    const row = document.createElement('div');
    row.className = 'trust-row';

    const main = document.createElement('div');
    main.className = 'trust-row-main';
    const titleEl = document.createElement('div');
    titleEl.className = 'trust-row-title';
    titleEl.textContent = title;
    const detailEl = document.createElement('div');
    detailEl.className = 'trust-row-detail';
    detailEl.textContent = detail;
    main.append(titleEl, detailEl);

    const badgeEl = document.createElement('span');
    badgeEl.className = 'trust-row-badge';
    badgeEl.textContent = badge;

    if (actions.length) {
      const actionWrap = document.createElement('div');
      actionWrap.className = 'trust-row-actions';
      actionWrap.appendChild(badgeEl);
      actions.forEach((action) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `trust-action-btn${action.danger ? ' danger' : ''}`;
        btn.textContent = action.label;
        btn.addEventListener('click', action.onClick);
        actionWrap.appendChild(btn);
      });
      row.append(main, actionWrap);
    } else {
      row.append(main, badgeEl);
    }
    return row;
  };

  const trustTaskControl = async (taskId, action) => {
    const id = String(taskId || '').trim();
    if (!id) return;
    const encoded = encodeURIComponent(id);
    const label = action === 'kill' ? 'killed' : action === 'cancel' ? 'cancelled' : action === 'pause' ? 'paused' : 'resumed';
    try {
      if (action === 'cancel') await api(`/api/tasks/${encoded}`, 'DELETE');
      else await api(`/api/tasks/${encoded}/${action}`, 'POST');
      toast(`Task ${label}.`, action === 'kill' || action === 'cancel' ? 'warn' : 'info', 2200);
      await loadTrustReport();
      if (typeof recoverActiveTask === 'function') recoverActiveTask().catch(() => {});
    } catch (err) {
      const detail = typeof err?.detail === 'string' ? err.detail : `Could not ${action} task.`;
      toast(detail, 'warn', 2600);
      await loadTrustReport();
    }
  };

  const renderTrustReport = () => {
    const grid = $('trust-grid');
    if (!grid) return;
    const report = trustReportState || {};
    const pendingTrust = report.pending_trust || {};
    const approvals = trustList(pendingTrust.approvals);
    const permissions = trustList(pendingTrust.permissions);
    const pendingCount = Number.isFinite(pendingTrust.count) ? pendingTrust.count : approvals.length + permissions.length;
    const activeTasks = trustList(report.active_tasks);
    const ledger = trustList(report.consent_ledger);
    const audit = report.audit || {};
    const auditCount = Object.values(audit).filter(Boolean).length;
    const trustChecks = trustList(report.readiness?.trust_checks);
    const blockedChecks = trustChecks.filter((item) => item?.status === 'blocked').length;
    const warningChecks = trustChecks.filter((item) => item?.status === 'warning').length;
    const killSwitch = report.kill_switch || {};
    const killRoutes = trustList(killSwitch.routes);
    const overall = String(report.overall || 'unknown').toLowerCase();

    const statusEl = $('trust-status');
    if (statusEl) {
      statusEl.textContent = TRUST_LABELS[overall] || humanize(overall);
      statusEl.dataset.status = trustStatusClass(overall);
    }
    const pendingCountEl = $('trust-pending-count');
    if (pendingCountEl) pendingCountEl.textContent = String(pendingCount);
    const ledgerCountEl = $('trust-ledger-count');
    if (ledgerCountEl) ledgerCountEl.textContent = String(ledger.length);
    const activeCountEl = $('trust-active-count');
    if (activeCountEl) activeCountEl.textContent = String(activeTasks.length);
    const updatedEl = $('trust-updated');
    if (updatedEl) {
      const age = report.generated_at ? relTime(report.generated_at) : '';
      updatedEl.textContent = age ? `Updated ${age}` : 'Not loaded yet';
    }

    grid.replaceChildren(
      makeTrustCard({
        label: 'Pending',
        value: String(pendingCount),
        detail: `${approvals.length} approvals, ${permissions.length} permissions`,
        status: pendingCount ? 'attention' : 'ready',
      }),
      makeTrustCard({
        label: 'Active Tasks',
        value: String(activeTasks.length),
        detail: activeTasks.length ? 'Kill and pause routes available' : 'No live automation',
        status: activeTasks.length ? 'attention' : 'ready',
      }),
      makeTrustCard({
        label: 'Audit',
        value: `${auditCount}/4`,
        detail: 'Logs, control trace, permissions, capsule auth',
        status: auditCount >= 4 ? 'ready' : 'warning',
      }),
      makeTrustCard({
        label: 'Kill Switch',
        value: killSwitch.available ? 'On' : 'Off',
        detail: `${killRoutes.length} protected stop routes`,
        status: killSwitch.available ? 'ready' : 'blocked',
      }),
      makeTrustCard({
        label: 'Trust Checks',
        value: String(trustChecks.length),
        detail: `${blockedChecks} blocked, ${warningChecks} warnings`,
        status: blockedChecks ? 'blocked' : (warningChecks ? 'warning' : 'ready'),
      })
    );

    const pendingList = $('trust-pending-list');
    if (pendingList) {
      const rows = [
        ...approvals.map((item) => makeTrustRow({
          title: item.task_id || 'Task',
          detail: item.action_id || 'Approval required',
          badge: 'approval',
        })),
        ...permissions.map((item) => makeTrustRow({
          title: item.task_id || 'Task',
          detail: item.action_id || 'Permission required',
          badge: 'access',
        })),
      ];
      pendingList.replaceChildren(...(rows.length ? rows : [makeTrustEmpty('No pending approvals or permissions.')]));
    }

    const ledgerList = $('trust-ledger-list');
    if (ledgerList) {
      const rows = ledger.map((item) => {
        const granted = trustList(item.granted).join(', ') || 'none';
        const denied = trustList(item.denied).join(', ') || 'none';
        return makeTrustRow({
          title: item.task_id || 'Task',
          detail: `Allowed: ${granted} | Denied: ${denied}`,
          badge: `${trustList(item.granted).length}/${trustList(item.denied).length}`,
        });
      });
      ledgerList.replaceChildren(...(rows.length ? rows : [makeTrustEmpty('No task-scoped permissions recorded.')]));
    }

    const activeList = $('trust-active-list');
    if (activeList) {
      const rows = activeTasks.map((item) => {
        const taskId = item.id || item.task_id || '';
        const status = String(item.status || (item.paused ? 'paused' : 'running')).toLowerCase();
        const paused = status === 'paused' || item.paused === true;
        const queued = status === 'queued' || status === 'pending';
        const modeBits = [item.mode, item.model].filter(Boolean).join(' | ');
        const detail = [item.goal || 'Untitled task', modeBits].filter(Boolean).join(' - ');
        const actions = queued
          ? [{ label: 'Cancel', danger: true, onClick: () => trustTaskControl(taskId, 'cancel') }]
          : [
              { label: paused ? 'Resume' : 'Pause', onClick: () => trustTaskControl(taskId, paused ? 'resume' : 'pause') },
              { label: 'Kill', danger: true, onClick: () => trustTaskControl(taskId, 'kill') },
            ];
        return makeTrustRow({
          title: taskId || 'Task',
          detail,
          badge: status || 'live',
          actions,
        });
      });
      activeList.replaceChildren(...(rows.length ? rows : [makeTrustEmpty('No active tasks.')]));
    }
  };

  const loadTrustReport = async () => {
    try {
      await keyReady;
      trustReportState = await api('/api/trust/report');
      renderTrustReport();
    } catch (e) {
      trustReportState = {
        overall: 'warning',
        pending_trust: { approvals: [], permissions: [], count: 0 },
        consent_ledger: [],
        active_tasks: [],
        audit: {},
        kill_switch: { available: false, routes: [] },
        readiness: { trust_checks: [] },
      };
      renderTrustReport();
    }
  };

  setInterval(() => {
    if ($('settings-overlay')?.classList.contains('show')) loadTrustReport();
  }, 10000);

  const loadMCP = async () => {
    try {
      const r = await fetch('/api/mcp');
      const d = await r.json();
      allMCPServers = d.servers || [];
      renderMCP();
    } catch (e) { console.error("Failed to load MCP servers", e); }
  };

  const loadCodingBackends = async () => {
    try {
      const r = await fetch('/api/coding-backends');
      const d = await r.json();
      codingBackendState = {
        backends: d.backends || [],
        default: d.default || '',
      };
      renderCodingBackends();
    } catch (e) { console.error("Failed to load coding backends", e); }
  };

  const renderMCP = () => {
    const grid = $('mcp-grid');
    if (!grid) return;
    grid.innerHTML = '';
    allMCPServers.forEach((s, idx) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'skill-item';
      item.addEventListener('click', () => openMCPModal(idx));

      const info = document.createElement('div');
      info.className = 'skill-info';
      const name = document.createElement('div');
      name.className = 'skill-name';
      name.textContent = s.name || 'MCP server';
      const desc = document.createElement('div');
      desc.className = 'skill-desc';
      desc.textContent = `${(s.tools || []).length} tools available`;
      info.append(name, desc);

      const toggle = document.createElement('div');
      toggle.className = 'skill-toggle';
      toggle.style.cssText = 'background:transparent; color:var(--muted); font-size:16px; display:flex; align-items:center; justify-content:flex-end;';
      toggle.textContent = '>';
      item.append(info, toggle);
      grid.appendChild(item);
    });
    const countEl = $('mcp-count');
    if (countEl) countEl.textContent = allMCPServers.length;
  };

  const renderCodingBackends = () => {
    const grid = $('coding-backends-grid');
    if (!grid) return;
    const backends = codingBackendState.backends || [];
    grid.innerHTML = '';
    backends.forEach((backend) => {
      const item = document.createElement('div');
      item.className = 'skill-item';
      item.style.cursor = 'default';

      const info = document.createElement('div');
      info.className = 'skill-info';
      const name = document.createElement('div');
      name.className = 'skill-name';
      const isDefault = backend.name && backend.name === codingBackendState.default;
      name.textContent = `${backend.name || 'Backend'}${isDefault ? ' (default)' : ''}`;
      const desc = document.createElement('div');
      desc.className = 'skill-desc';
      const bits = [];
      if (backend.type) bits.push(backend.type);
      if (backend.model) bits.push(backend.model);
      if (backend.version) bits.push(backend.version);
      bits.push(backend.available ? 'available' : (backend.detail || 'unavailable'));
      desc.textContent = bits.filter(Boolean).join(' · ');
      info.append(name, desc);

      const toggle = document.createElement('div');
      toggle.className = 'skill-toggle';
      toggle.style.cssText = 'background:transparent; color:var(--muted); width:auto; min-width:fit-content; font-size:11px; display:flex; align-items:center; justify-content:flex-end; margin-left:12px;';
      toggle.textContent = backend.available ? 'ready' : 'offline';
      item.append(info, toggle);
      grid.appendChild(item);
    });
    const countEl = $('coding-backend-count');
    if (countEl) countEl.textContent = backends.length;
  };

  window.openMCPModal = (idx) => {
    const server = allMCPServers[idx];
    if (!server) return;
    $('mcp-modal-title').textContent = server.name;
    const toolsContainer = $('mcp-modal-tools');
    toolsContainer.innerHTML = '';
    (server.tools || []).forEach((t) => {
      const card = document.createElement('div');
      card.style.cssText = 'background: var(--bg-3); border: 1px solid var(--line-2); padding: 12px 14px; border-radius: var(--r-sm);';
      const name = document.createElement('div');
      name.style.cssText = 'font-weight: 600; color: var(--ink); font-size: var(--fs-sm); margin-bottom: 4px;';
      name.textContent = t.name || 'Tool';
      const desc = document.createElement('div');
      desc.style.cssText = 'font-size: var(--fs-micro); color: var(--muted); line-height: 1.5;';
      desc.textContent = t.description || 'No description provided.';
      card.append(name, desc);
      const props = t.inputSchema?.properties || {};
      if (Object.keys(props).length > 0) {
        const pre = document.createElement('pre');
        pre.style.cssText = 'font-size: 11px; color: var(--muted-2); margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line-2); overflow-x: auto; font-family: var(--font-mono);';
        pre.textContent = JSON.stringify(props, null, 2);
        card.appendChild(pre);
      }
      toolsContainer.appendChild(card);
    });
    $('mcp-overlay').classList.add('show');
  };

  $('mcp-modal-close').onclick = () => $('mcp-overlay').classList.remove('show');
  $('project-folder-trigger').onclick = (e) => { e.stopPropagation(); toggleFolderMenu(); };
  // Close the folder menu on outside click or resize.
  document.addEventListener('click', (e) => {
    if (!folderMenuOpen) return;
    const menu = $('project-folder-menu');
    const trig = $('project-folder-trigger');
    if (menu && !menu.contains(e.target) && trig && !trig.contains(e.target)) closeFolderMenu();
  });
  window.addEventListener('resize', () => { if (folderMenuOpen) positionFolderMenu(); });

  /* ---------------- event wiring ---------------- */
  $('send').onclick = start;
  $('btn-cancel').onclick = () => { stopEverything(); cancelTask(); };
  $('btn-pause').onclick = togglePause;
  $('btn-retry').onclick = retryTask;
  $('btn-control-report').onclick = showControlReport;
  $('btn-copy-log').onclick = copyCurrentLog;
  $('btn-download-log').onclick = downloadCurrentLog;
  $('new-session-btn').onclick = newSession;
  $('history-search').addEventListener('input', filterHistory);
  $('nav-toggle').onclick = () => document.body.classList.toggle('nav-open');
  $('lightbox-close').onclick = closeLightbox;
  $('lightbox').onclick = (e) => { if (e.target === $('lightbox')) closeLightbox(); };
  $('app-approve').onclick = () => sendApproval(true);
  $('app-deny').onclick = () => sendApproval(false);
  $('perm-approve').onclick = () => sendPermission(true);
  $('perm-deny').onclick = () => sendPermission(false);
  $('desktop-access-allow').onclick = () => resolveDesktopAccess(true);
  $('desktop-access-deny').onclick = () => resolveDesktopAccess(false);
  $('desktop-control-stop').onclick = () => { stopEverything(); cancelTask(); };

  $('mode-id').onchange = (e) => {
    const val = e.target.value;
    const isolatedApp = ($('isolated-app-id').value || '').trim();
    localStorage.setItem('orynn_mode', val);
    localStorage.removeItem('ai_computer_mode');
    setMode(val, val === 'computer_isolated', isolatedApp);
    $('isolated-app-wrap').style.display = (val === 'computer_isolated') ? '' : 'none';
  };
  $('isolated-app-id').addEventListener('input', () => {
    if (($('mode-id').value || '') === 'computer_isolated') setMode('computer_isolated', true, ($('isolated-app-id').value || '').trim());
  });

  $('input').addEventListener('input', () => { autoGrow(); updateCharCount(); });
  $('input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); start(); }
  });

  window.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') { event.preventDefault(); newSession(); return; }
    if ($('approval').classList.contains('show')) {
      if (event.key === 'Enter') { event.preventDefault(); sendApproval(true); }
      if (event.key === 'Escape') { event.preventDefault(); sendApproval(false); }
      return;
    }
    if ($('permission').classList.contains('show')) {
      if (event.key === 'Enter') { event.preventDefault(); sendPermission(true); }
      if (event.key === 'Escape') { event.preventDefault(); sendPermission(false); }
      return;
    }
    if ($('desktop-access').classList.contains('show')) {
      if (event.key === 'Enter') { event.preventDefault(); resolveDesktopAccess(true); }
      if (event.key === 'Escape') { event.preventDefault(); resolveDesktopAccess(false); }
      return;
    }
    if (folderMenuOpen && event.key === 'Escape') {
      event.preventDefault();
      closeFolderMenu();
      return;
    }
    if ($('lightbox').classList.contains('show') && event.key === 'Escape') { event.preventDefault(); closeLightbox(); return; }

    if (event.key === 'Escape' && task && !isTerminalStatus(currentStatus)) {
      event.preventDefault();
      toast('Task cancelled via Escape.', 'warn');
      cancelTask();
    }
  });

  // focus-loss auto-pause
  const shouldAutoPauseOnFocusLoss = () => currentMode === 'computer' || currentMode === 'computer_isolated';
  let _focusLossTimer = null;
  let _pausedByFocusLoss = false;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (shouldAutoPauseOnFocusLoss() && task && !isTerminalStatus(currentStatus) && !isPaused) {
        _focusLossTimer = setTimeout(async () => {
          if (shouldAutoPauseOnFocusLoss() && task && !isTerminalStatus(currentStatus) && !isPaused) {
            _pausedByFocusLoss = true;
            isPaused = true;
            await api(`/api/tasks/${task}/pause`, 'POST').catch(() => {});
            $('btn-pause').textContent = 'Resume';
            setStatus('paused');
          }
        }, 5000);
      }
    } else {
      clearTimeout(_focusLossTimer);
      _focusLossTimer = null;
      if (_pausedByFocusLoss && task && isPaused) {
        _pausedByFocusLoss = false; isPaused = false;
        api(`/api/tasks/${task}/resume`, 'POST').catch(() => {});
        $('btn-pause').textContent = 'Pause';
        setStatus('running');
      }
    }
  });

  /* ================================================================
     Command palette (⌘K)
     ================================================================ */
  const cmdk = $('cmdk');
  const cmdkInput = $('cmdk-input');
  const cmdkList = $('cmdk-list');

  const buildCommands = () => {
    const modeOpts = [
      { value: 'coding', label: 'coding' },
      { value: 'computer', label: 'desktop' },
      { value: 'computer_use', label: 'browser' },
      { value: 'computer_isolated', label: 'isolated' },
    ];
    const modelSel = $('model-id');
    const models = modelSel ? Array.from(modelSel.options).map(o => ({ value: o.value, label: o.textContent })) : [];
    const cmds = [];
    modeOpts.forEach(m => cmds.push({
      group: 'Mode', label: `Set mode: ${m.label}`, hint: m.value,
      action: () => { const sel = $('mode-id'); if (sel) { sel.value = m.value; sel.dispatchEvent(new Event('change', { bubbles: true })); } setMode(m.value); }
    }));
    models.forEach(m => cmds.push({
      group: 'Model', label: `Use model: ${m.label}`, hint: m.value,
      action: () => { const sel = $('model-id'); if (sel) { sel.value = m.value; sel.dispatchEvent(new Event('change', { bubbles: true })); } const sbm = $('sb-model-val'); if (sbm) sbm.textContent = m.label; }
    }));
    cmds.push(
      { group: 'Task', label: 'Start task', hint: 'Ctrl ↵', action: () => { const b = $('send'); if (b) b.click(); } },
      { group: 'Task', label: 'Pause / resume', hint: '', action: () => { const b = $('btn-pause'); if (b) b.click(); } },
      { group: 'Task', label: 'Cancel task', hint: 'esc', action: () => { const b = $('btn-cancel'); if (b) b.click(); } },
      { group: 'Project Folder', label: 'Choose project folder', hint: projectFolderState.selectedPath || 'General mode', action: () => openFolderMenu() },
      { group: 'Project Folder', label: 'Clear project folder', hint: 'Desktop + Home', action: () => { setProjectFolder('', { persist: true }); toast('Project folder cleared.', 'info', 1800); } },
      { group: 'View', label: 'Focus prompt', hint: 'Ctrl L', action: () => { const el = $('input'); if (el) el.focus(); } },
      { group: 'View', label: 'Show widget gallery', hint: 'demo', action: () => playWidgetGallery() },
      { group: 'View', label: 'Toggle history', hint: '', action: () => { const el = $('btn-history'); if (el) el.click(); } },
      { group: 'Settings', label: 'Open settings', hint: '', action: () => { const b = $('open-settings'); if (b) b.click(); } },
      { group: 'Settings', label: 'Toggle theme', hint: 'dark / light / system', action: () => {
          const sel = $('pref-theme'); if (!sel) return;
          const order = ['auto', 'dark', 'light'];
          const nx = order[(order.indexOf(sel.value) + 1) % order.length];
          sel.value = nx; sel.dispatchEvent(new Event('change', { bubbles: true }));
        } },
    );
    return cmds;
  };

  let cmdkCmds = [];
  let cmdkFiltered = [];
  let cmdkIdx = 0;

  const renderCmdk = () => {
    cmdkList.innerHTML = '';
    if (!cmdkFiltered.length) {
      cmdkList.innerHTML = '<div class="cmdk-empty">No matches</div>';
      return;
    }
    let lastGroup = '';
    cmdkFiltered.forEach((c, i) => {
      if (c.group !== lastGroup) {
        const h = document.createElement('div');
        h.className = 'cmdk-section-label';
        h.textContent = c.group;
        cmdkList.appendChild(h);
        lastGroup = c.group;
      }
      const row = document.createElement('div');
      row.className = 'cmdk-item' + (i === cmdkIdx ? ' on' : '');
      row.dataset.i = i;
      const icon = document.createElement('span');
      icon.className = 'cmdk-icon';
      icon.textContent = '>';
      const label = document.createElement('span');
      label.className = 'cmdk-label';
      label.textContent = c.label;
      row.append(icon, label);
      if (c.hint) {
        const hint = document.createElement('span');
        hint.className = 'cmdk-hint';
        hint.textContent = c.hint;
        row.appendChild(hint);
      }
      row.addEventListener('mousemove', () => { cmdkIdx = i; renderCmdk(); });
      row.addEventListener('click', () => { c.action(); closeCmdk(); });
      cmdkList.appendChild(row);
    });
    const active = cmdkList.querySelector('.cmdk-item.on');
    if (active) active.scrollIntoView({ block: 'nearest' });
  };

  const filterCmdk = (q) => {
    q = (q || '').trim().toLowerCase();
    cmdkFiltered = !q ? cmdkCmds.slice() : cmdkCmds.filter(c => (c.label + ' ' + (c.hint||'') + ' ' + c.group).toLowerCase().includes(q));
    cmdkIdx = 0;
    renderCmdk();
  };

  const openCmdk = () => {
    cmdkCmds = buildCommands();
    cmdkInput.value = '';
    filterCmdk('');
    cmdk.classList.add('show');
    setTimeout(() => cmdkInput.focus(), 10);
  };
  const closeCmdk = () => { cmdk.classList.remove('show'); };
  const shortcutHelp = $('shortcut-help');
  const openShortcutHelp = () => { if (shortcutHelp) shortcutHelp.classList.add('show'); };
  const closeShortcutHelp = () => { if (shortcutHelp) shortcutHelp.classList.remove('show'); };
  const isTextEntryTarget = (target) => {
    if (!target) return false;
    const tag = (target.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || !!target.isContentEditable;
  };

  cmdkInput.addEventListener('input', (e) => filterCmdk(e.target.value));
  cmdkInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); cmdkIdx = Math.min(cmdkFiltered.length - 1, cmdkIdx + 1); renderCmdk(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); cmdkIdx = Math.max(0, cmdkIdx - 1); renderCmdk(); }
    else if (e.key === 'Enter') { e.preventDefault(); const c = cmdkFiltered[cmdkIdx]; if (c) { c.action(); closeCmdk(); } }
    else if (e.key === 'Escape') { e.preventDefault(); closeCmdk(); }
  });
  cmdk.addEventListener('click', (e) => { if (e.target === cmdk) closeCmdk(); });
  if (shortcutHelp) shortcutHelp.addEventListener('click', (e) => { if (e.target === shortcutHelp) closeShortcutHelp(); });

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (cmdk.classList.contains('show')) closeCmdk(); else openCmdk();
    } else if (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (isTextEntryTarget(e.target)) return;
      e.preventDefault();
      openShortcutHelp();
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'l') {
      e.preventDefault();
      const el = $('prompt'); if (el) el.focus();
    } else if (e.key === 'Escape' && shortcutHelp && shortcutHelp.classList.contains('show')) {
      e.preventDefault();
      closeShortcutHelp();
    }
  });
  const cmdkBtn = $('tb-cmdk'); if (cmdkBtn) cmdkBtn.addEventListener('click', openCmdk);
  const sbCmdk = $('sb-cmdk'); if (sbCmdk) sbCmdk.addEventListener('click', openCmdk);
  const sbModeBtn = $('sb-mode'); if (sbModeBtn) sbModeBtn.addEventListener('click', () => { openCmdk(); setTimeout(() => { cmdkInput.value = 'mode'; filterCmdk('mode'); }, 20); });
  const sbModelBtn = $('sb-model'); if (sbModelBtn) sbModelBtn.addEventListener('click', () => { openCmdk(); setTimeout(() => { cmdkInput.value = 'model'; filterCmdk('model'); }, 20); });

  // Production Safety Layer (Wired to Codex UI)
  let focusSafetyTimer = null;
  window.addEventListener('blur', () => {
    if (shouldAutoPauseOnFocusLoss() && task && !isTerminalStatus(currentStatus) && !isPaused) {
      focusSafetyTimer = setTimeout(() => {
        if (shouldAutoPauseOnFocusLoss() && !isPaused) {
          console.warn("Safety: Focus lost for 5s. Auto-pausing.");
          togglePause();
        }
      }, 5000);
    }
  });
  window.addEventListener('focus', () => {
    if (focusSafetyTimer) {
      clearTimeout(focusSafetyTimer);
      focusSafetyTimer = null;
    }
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && task && !isTerminalStatus(currentStatus)) {
      console.warn("Emergency Kill-Switch (ESC) triggered.");
      stopEverything();
      cancelTask();
    }
  });

  /* ---------------- Liquid-Glass command capsule ----------------
     One ambient pill. Funnels into the existing composer pipeline,
     mirrors agent state, renders a dot-matrix waveform, and shows
     the agent's reply inside the capsule. */
  (function capsuleWidget() {
    const root = $('vorb-root');
    if (!root) return;

    const params = new URLSearchParams(location.search);
    const widgetShell = params.get('widget') === '1' || params.get('sidekick') === '1';
    if (widgetShell) {
      document.documentElement.classList.add('widget-shell');
      document.body.classList.add('widget-shell');
      root.classList.add('widget-shell');
      // dark frosted glass reads best floating over an arbitrary desktop
      document.documentElement.setAttribute('data-theme', 'dark');
      // Qt shell: the OS window provides the rounded edge + Acrylic blur,
      // so the capsule drops its own border/blur to avoid a double outline.
      if (params.get('shell') === 'qt') {
        document.documentElement.classList.add('qt-shell');
        document.body.classList.add('qt-shell');
      }
    }

    const textIn = $('vpanel-text');
    const statusEl = $('vpanel-activity-text');
    const sendBtn = $('vpanel-send');
    const micBtn = $('vmic');
    const closeShell = $('vpanel-close-shell');
    const reply = $('vcap-reply');
    const replyText = $('vcap-reply-text');
    const wave = $('vcap-wave');
    const contextEl = $('vcap-context');
    const scopeEl = $('vcap-scope');
    const visionEl = $('vcap-vision');
    const phaseEl = $('vcap-phase');
    const actionsEl = $('vcap-actions');
    const pauseBtn = $('vcap-pause');
    const stopBtn = $('vcap-stop');
    const detailsBtn = $('vcap-details');
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (closeShell) closeShell.onclick = () => {
      try { if (window.__qtShell && window.__qtShell.closeWindow) { window.__qtShell.closeWindow(); return; } } catch (_) {}
      try { if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) window.pywebview.api.close_window(); } catch (_) {}
      try { window.close(); } catch (_) {}
    };

    // --- Qt shell: drag the native window by the capsule logo ---
    const wireQtDrag = () => {
      const grip = root.querySelector('.vcap-logo');
      if (!grip || !window.__qtShell || !window.__qtShell.moveBy) return;
      grip.style.cursor = 'grab';
      grip.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        let px = e.screenX, py = e.screenY;
        const mv = (ev) => {
          try { window.__qtShell.moveBy(ev.screenX - px, ev.screenY - py); } catch (_) {}
          px = ev.screenX; py = ev.screenY;
        };
        const up = () => {
          document.removeEventListener('pointermove', mv);
          document.removeEventListener('pointerup', up);
        };
        document.addEventListener('pointermove', mv);
        document.addEventListener('pointerup', up);
      });
    };
    if (window.__qtShell) wireQtDrag();
    else window.addEventListener('qt-shell-ready', wireQtDrag);

    const setStatus = (t) => { if (statusEl) statusEl.textContent = t || ''; };
    const CAPSULE_PHASE_LABELS = {
      idle: 'Idle',
      focused: 'Ready',
      context_ready: 'Context ready',
      listening: 'Listening',
      submitting: 'Starting',
      planning: 'Planning',
      acting: 'Acting',
      waiting_approval: 'Needs approval',
      blocked: 'Blocked',
      paused: 'Paused',
      done: 'Done',
      error: 'Error'
    };
    const CAPSULE_CONTEXT_ACTIONS = [
      { key: /chrome|edge|browser|web/i, actions: [
        ['Summarize page', 'Summarize the active browser page and list key actions.'],
        ['Extract links', 'Extract the important links from the active browser page.'],
        ['Fill form', 'Use the current browser page and help fill the visible form.']
      ] },
      { key: /code|workspace|repo|project|vs code|cursor/i, actions: [
        ['Run tests', 'Run the project tests, diagnose failures, and propose the smallest fix.'],
        ['Explain error', 'Explain the visible error or failing command in this project.'],
        ['Fix failure', 'Find and fix the current failing test or runtime error.']
      ] },
      { key: /file|folder|downloads|explorer/i, actions: [
        ['Clean folder', 'Scan the selected folder, group clutter, and ask before moving files.'],
        ['Find file', 'Find the file I describe in the selected folder.'],
        ['Summarize files', 'Summarize the important files in the selected folder.']
      ] },
      { key: /desktop|computer|screen|auto/i, actions: [
        ['Explain screen', 'Look at my screen and explain what is open.'],
        ['Open app', 'Open the app I name and get it ready for work.'],
        ['Do visible task', 'Use the visible app to complete the task I describe.']
      ] }
    ];
    let capsuleUiState = 'idle';
    let capsuleLastAction = '';
    let capsuleFocused = false;

    const capsuleScope = () => {
      const isolated = (typeof currentIsolatedApp !== 'undefined' && currentIsolatedApp) || '';
      if (isolated) return isolated;
      const mode = (typeof currentMode !== 'undefined' && currentMode) || 'auto';
      if (/browser/.test(mode)) return 'Browser';
      if (/computer/.test(mode)) return 'Desktop';
      if (/coding/.test(mode)) return 'Workspace';
      return 'Computer';
    };

    const capsuleSees = (state, scope) => {
      const mode = (typeof currentMode !== 'undefined' && currentMode) || '';
      const controlLayer = (typeof capsuleControlLayer !== 'undefined' && capsuleControlLayer) || '';
      if (controlLayer && ['planning', 'acting', 'waiting_approval', 'done', 'error'].includes(state)) return controlLayer;
      if (state === 'listening') return 'Voice input';
      if (state === 'waiting_approval') return 'Approval paused';
      if (state === 'blocked') return 'Needs help';
      if (state === 'done') return 'Verified result';
      if (state === 'error') return 'Recovery needed';
      if (/computer|browser/.test(mode) || /desktop|browser/i.test(scope)) return 'Seeing screen';
      if (/workspace/i.test(scope)) return 'Workspace context';
      return 'Ready';
    };

    const capsuleActionsFor = (scope) => {
      const hay = `${scope} ${(typeof currentMode !== 'undefined' && currentMode) || ''}`;
      const found = CAPSULE_CONTEXT_ACTIONS.find((group) => group.key.test(hay));
      return (found || CAPSULE_CONTEXT_ACTIONS[CAPSULE_CONTEXT_ACTIONS.length - 1]).actions;
    };

    const setCapsulePrompt = (prompt) => {
      if (textIn) {
        textIn.value = prompt;
        textIn.focus();
      }
      capsuleFocused = true;
      renderCapsuleState({ state: 'focused' });
    };

    const renderCapsuleActions = (items, visible) => {
      if (!actionsEl) return;
      actionsEl.replaceChildren();
      if (!visible) {
        actionsEl.hidden = true;
        return;
      }
      items.forEach(([label, prompt]) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'vcap-action';
        btn.textContent = label;
        btn.title = prompt;
        btn.addEventListener('click', () => setCapsulePrompt(prompt));
        actionsEl.appendChild(btn);
      });
      actionsEl.hidden = false;
    };

    const renderCapsuleState = ({ state = capsuleUiState, action = capsuleLastAction } = {}) => {
      capsuleUiState = state;
      capsuleLastAction = action || capsuleLastAction || '';
      const scope = capsuleScope();
      root.dataset.capState = state;
      root.classList.toggle('busy', ['submitting', 'planning', 'acting', 'waiting_approval'].includes(state));
      root.classList.toggle('listening', state === 'listening');
      if (scopeEl) scopeEl.textContent = scope;
      if (visionEl) visionEl.textContent = capsuleSees(state, scope);
      if (phaseEl) phaseEl.textContent = CAPSULE_PHASE_LABELS[state] || state;
      const showContext = state !== 'idle' || capsuleFocused || Boolean(capsuleLastAction);
      if (contextEl) contextEl.hidden = !showContext;
      const showActions = !['submitting', 'planning', 'acting', 'waiting_approval'].includes(state)
        && state !== 'listening'
        && (capsuleFocused || state === 'context_ready' || state === 'done' || state === 'error');
      renderCapsuleActions(capsuleActionsFor(scope), showActions);
      if (capsuleLastAction && ['acting', 'planning', 'waiting_approval'].includes(state)) {
        setStatus(capsuleLastAction);
      }
      requestAnimationFrame(syncShellHeight);
    };

    // In the desktop shell the OS window hugs the capsule — resize it to the
    // capsule's actual height so there is no empty window "cube" around it.
    const syncShellHeight = () => {
      if (!widgetShell) return;
      try {
        const cap = document.querySelector('.vcap');
        if (!cap) return;
        const h = Math.ceil(cap.getBoundingClientRect().height) + 22;  // top+bottom inset
        const api = window.pywebview && window.pywebview.api;
        if (api && api.set_capsule_height) api.set_capsule_height(h);
      } catch (_) {}
    };
    const showReply = (t) => {
      if (!widgetShell) return;
      if (reply && replyText) { replyText.textContent = t; reply.hidden = false; }
      requestAnimationFrame(syncShellHeight);
    };
    const hideReply = () => { if (reply) reply.hidden = true; requestAnimationFrame(syncShellHeight); };
    // keep the window glued to the capsule whenever it changes size
    if (widgetShell) {
      window.addEventListener('pywebviewready', () => setTimeout(syncShellHeight, 60));
      setTimeout(syncShellHeight, 400);
      try {
        const cap = document.querySelector('.vcap');
        if (cap && window.ResizeObserver) new ResizeObserver(syncShellHeight).observe(cap);
      } catch (_) {}
    }

    // --- funnel into the existing composer pipeline ---
    const submitGoal = (text) => {
      text = (text || '').trim();
      if (!text) return;
      const mainInput = $('input');
      if (!mainInput) return;
      if (task && sse) {
        if (widgetShell) showReply('A task is already running — let it finish first.');
        else toast('A task is already running.', 'warn', 2200);
        return;
      }
      mainInput.value = text;
      mainInput.dispatchEvent(new Event('input'));
      const sb = $('send');
      if (sb) sb.click();
      if (textIn) textIn.value = '';
      hideReply();
      renderCapsuleState({ state: 'submitting', action: 'Starting task...' });
    };
    if (sendBtn) sendBtn.onclick = () => submitGoal(textIn && textIn.value);
    if (textIn) textIn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); submitGoal(textIn.value); }
    });
    if (textIn) {
      textIn.addEventListener('focus', () => {
        capsuleFocused = true;
        renderCapsuleState({ state: 'context_ready' });
      });
      textIn.addEventListener('blur', () => {
        capsuleFocused = false;
        if (!textIn.value && currentStatus === 'ready') renderCapsuleState({ state: 'idle', action: '' });
      });
      textIn.addEventListener('input', () => {
        renderCapsuleState({ state: textIn.value.trim() ? 'focused' : 'context_ready' });
      });
    }
    if (pauseBtn) pauseBtn.addEventListener('click', () => {
      const b = $('btn-pause');
      if (b) b.click();
    });
    if (stopBtn) stopBtn.addEventListener('click', () => {
      const b = $('btn-cancel');
      if (b) b.click();
    });
    if (detailsBtn) detailsBtn.addEventListener('click', () => {
      if (widgetShell) {
        const detail = [capsuleLastAction, ...stripLines].filter(Boolean).slice(-4).join('\n');
        showReply(detail || 'No task details yet.');
      } else {
        const feed = $('feed');
        if (feed) feed.scrollIntoView({ behavior: 'smooth', block: 'end' });
      }
    });

    // --- voice: tap to talk, auto-submit on the final transcript ---
    if (!SR) { if (micBtn) micBtn.style.display = 'none'; }
    else {
      let rec = null, listening = false, finalText = '';
      micBtn.onclick = () => {
        if (listening && rec) { try { rec.stop(); } catch (_) {} return; }
        rec = new SR();
        rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false;
        finalText = '';
        rec.onstart = () => { listening = true; renderCapsuleState({ state: 'listening', action: 'Listening...' }); kickWave(); };
        rec.onresult = (e) => {
          let t = ''; for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
          finalText = t; if (textIn) textIn.value = t;
        };
        rec.onerror = () => {};
        rec.onend = () => {
          listening = false; root.classList.remove('listening');
          if (finalText.trim()) submitGoal(finalText);
          else setStatus('Didn’t catch that — try again.');
          if (!finalText.trim()) renderCapsuleState({ state: 'focused', action: '' });
        };
        try { rec.start(); } catch (_) { listening = false; root.classList.remove('listening'); }
      };
    }

    // --- dot-matrix waveform (canvas) — animates only while active ---
    let kickWave = () => {};
    if (wave && wave.getContext) {
      const ctx = wave.getContext('2d');
      const COLS = 16, ROWS = 5, W = wave.width, H = wave.height;
      const cw = W / COLS, dot = Math.min(cw, H / ROWS) * 0.42;
      const phase = Array.from({ length: COLS }, (_, i) => i * 0.6);
      let looping = false;
      const draw = () => {
        const active = root.classList.contains('busy') || root.classList.contains('listening');
        const col = getComputedStyle(root).getPropertyValue('--cap-accent').trim() || '#5be0d0';
        const now = performance.now() / 300;
        ctx.clearRect(0, 0, W, H);
        for (let c = 0; c < COLS; c++) {
          const lit = active ? 1 + Math.round(((Math.sin(now + phase[c]) + 1) / 2) * (ROWS - 1)) : 1;
          for (let r = 0; r < ROWS; r++) {
            ctx.beginPath();
            ctx.arc(c * cw + cw / 2, H - (r + 0.5) * (H / ROWS), dot, 0, Math.PI * 2);
            ctx.fillStyle = col;
            ctx.globalAlpha = r >= ROWS - lit ? (active ? 0.95 : 0.46) : 0.12;
            ctx.fill();
          }
        }
        ctx.globalAlpha = 1;
      };
      const frame = () => {
        draw();
        if (root.classList.contains('busy') || root.classList.contains('listening')) requestAnimationFrame(frame);
        else looping = false;
      };
      kickWave = () => { if (!looping) { looping = true; requestAnimationFrame(frame); } };
      draw();  // initial idle render
    }

    // --- mirror agent state every 700ms + live activity strip ---
    let lastStatus = '', lastLive = '';
    const stripEl = document.getElementById('vcap-strip');
    const stripLines = [];
    const renderStrip = () => {
      if (!stripEl) return;
      stripEl.replaceChildren();
      stripLines.forEach((line) => {
        const span = document.createElement('span');
        span.className = 'vcap-step';
        span.textContent = line;
        stripEl.appendChild(span);
      });
      stripEl.hidden = stripLines.length === 0;
    };
    const deriveCapsuleState = (st, live) => {
      const approvalOpen = $('approval')?.classList.contains('show') || $('permission')?.classList.contains('show');
      if (approvalOpen) return 'waiting_approval';
      if (st === 'running') {
        if (/planning|thinking|drafting|model|initializing/i.test(live)) return 'planning';
        return 'acting';
      }
      if (st === 'paused') return 'paused';
      if (st === 'complete' || st === 'done') return 'done';
      if (st === 'failed' || st === 'error') return 'error';
      if (st === 'cancelled') return 'blocked';
      if (capsuleFocused) return 'context_ready';
      return 'idle';
    };
    setInterval(() => {
      const st = (typeof currentStatus !== 'undefined' && currentStatus) || 'ready';
      const live = (typeof liveStatusMessage !== 'undefined' && liveStatusMessage) || '';
      const capState = deriveCapsuleState(st, live);
      const actionText = live || (st === 'running' ? 'Working on it...' : '');
      renderCapsuleState({ state: capState, action: actionText });
      kickWave();
      if (st !== lastStatus || live !== lastLive) {
        lastStatus = st; lastLive = live;
        const labels = { ready: '', running: live || 'Working on it…', complete: 'Done.',
          failed: 'That task failed.', error: 'Something went wrong.', paused: 'Paused.', cancelled: 'Cancelled.' };
        setStatus(labels[st] !== undefined ? labels[st] : st);
        // update activity strip: show last 3 live status lines while running
        if (stripEl) {
          if (st === 'running' && live) {
            stripLines.push(live);
            if (stripLines.length > 3) stripLines.shift();
            renderStrip();
          } else if (st !== 'running') {
            stripLines.length = 0;
            renderStrip();
          }
        }
      }
    }, 700);
    renderCapsuleState({ state: 'idle', action: '' });

    // --- capture agent replies into the capsule (keeps read-aloud intact) ---
    const _speak = speakAgentReply;
    speakAgentReply = function (text) {
      try {
        let clean = String(text || '').trim();
        if (clean.startsWith('{') && clean.endsWith('}')) {
          try { const o = JSON.parse(clean); clean = o.reason || o.answer || o.text || o.message || clean; } catch (_) {}
        }
        if (clean) showReply(clean.slice(0, 700));
      } catch (_) {}
      return _speak.apply(this, arguments);
    };

    // --- drag-to-reposition (dashboard mode only; grab the logo) ---
    const POS_KEY = 'ai-computer.vorb-position.v2';
    if (!widgetShell) {
      try {
        const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
        if (saved && typeof saved.right === 'number') {
          root.style.right = saved.right + 'px';
          root.style.bottom = saved.bottom + 'px';
        }
      } catch (_) {}
      let dragging = false, ox = 0, oy = 0;
      const onMove = (e) => {
        if (!dragging) return;
        const cr = Math.max(8, Math.min(window.innerWidth - 130,
          parseFloat(root.style.right || '26') - (e.clientX - ox)));
        const cb = Math.max(8, Math.min(window.innerHeight - 90,
          parseFloat(root.style.bottom || '30') - (e.clientY - oy)));
        ox = e.clientX; oy = e.clientY;
        root.style.right = cr + 'px'; root.style.bottom = cb + 'px';
      };
      const onUp = () => {
        dragging = false;
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        try { localStorage.setItem(POS_KEY, JSON.stringify({
          right: parseFloat(root.style.right) || 26, bottom: parseFloat(root.style.bottom) || 30 })); } catch (_) {}
      };
      const grip = root.querySelector('.vcap-logo');
      if (grip) {
        grip.style.cursor = 'grab';
        grip.addEventListener('pointerdown', (e) => {
          dragging = true; ox = e.clientX; oy = e.clientY;
          document.addEventListener('pointermove', onMove);
          document.addEventListener('pointerup', onUp);
        });
      }
    }

    // --- global shortcut: Ctrl+Shift+Space toggles the capsule (summon/dismiss) ---
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.code === 'Space') {
        e.preventDefault();
        root.hidden = !root.hidden;
        if (!root.hidden && !isTextEntryTarget(e.target) && textIn) textIn.focus();
      }
    });
  })();

  init();

/* ---------------- User preferences (theme, mode, voice…) ---------------- */
(function initPreferences(){
  let prefs = null;

  async function ensureSession(){ try { await fetch('/api/session', {method:'POST'}); } catch(_){} }

  function resolveTheme(t){
    if (t === 'light' || t === 'dark') return t;
    // auto → follow the OS
    try {
      return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } catch(_) { return 'dark'; }
  }

  function applyTheme(t){
    document.documentElement.setAttribute('data-theme', resolveTheme(t));
  }

  // React to OS theme changes while in "auto"
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (prefs && prefs.theme === 'auto') applyTheme('auto');
    });
  } catch(_){}

  function fillControls(){
    if (!prefs) return;
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    const chk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    set('pref-theme', prefs.theme);
    set('pref-default-mode', prefs.default_mode);
    set('pref-desktop-model', prefs.desktop_model || '');
    chk('pref-speak', prefs.speak_replies);
    chk('pref-voice-input', prefs.voice_input);
    chk('pref-glow', prefs.show_action_glow);
    chk('pref-confirm', prefs.confirm_sensitive);
  }

  function flashSaved(){
    const hint = document.getElementById('pref-saved-hint');
    if (!hint) return;
    hint.style.opacity = '1';
    clearTimeout(flashSaved._t);
    flashSaved._t = setTimeout(() => { hint.style.opacity = '0'; }, 1400);
  }

  async function save(patch){
    Object.assign(prefs, patch);
    if ('theme' in patch) applyTheme(patch.theme);
    try {
      await ensureSession();
      await fetch('/api/preferences', {
        method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ preferences: patch }),
      });
      flashSaved();
    } catch(e){ console.error('[prefs] save failed', e); }
  }

  function bind(){
    const onSel = (id, key) => document.getElementById(id)
      ?.addEventListener('change', e => save({ [key]: e.target.value }));
    const onChk = (id, key) => document.getElementById(id)
      ?.addEventListener('change', e => save({ [key]: e.target.checked }));
    const onTxt = (id, key) => document.getElementById(id)
      ?.addEventListener('change', e => save({ [key]: e.target.value.trim() }));
    onSel('pref-theme', 'theme');
    onSel('pref-default-mode', 'default_mode');
    onChk('pref-speak', 'speak_replies');
    onChk('pref-voice-input', 'voice_input');
    onChk('pref-glow', 'show_action_glow');
    onChk('pref-confirm', 'confirm_sensitive');
    onTxt('pref-desktop-model', 'desktop_model');
    // Refresh controls each time Settings opens — onboarding or the capsule may
    // have changed prefs since page load, so the cached values can be stale.
    document.getElementById('open-settings')
      ?.addEventListener('click', () => { setTimeout(load, 30); });
  }

  async function load(){
    try {
      await ensureSession();
      const r = await fetch('/api/preferences', { credentials:'include' });
      if (!r.ok) return;
      prefs = (await r.json()).preferences || {};
      applyTheme(prefs.theme);
      fillControls();
      // Make the default-mode preference actually drive the mode selector, so
      // new tasks start in the user's chosen mode.
      if (prefs.default_mode && prefs.default_mode !== 'auto') {
        const modeSel = document.getElementById('mode-id');
        if (modeSel && modeSel.value !== prefs.default_mode) {
          modeSel.value = prefs.default_mode;
          modeSel.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
    } catch(e){ console.error('[prefs] load failed', e); }
  }

  function start(){ bind(); load(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
})();

/* ---------------- First-run onboarding wizard ---------------- */
(function initOnboarding(){
  // Never run inside the floating capsule (it loads ?widget=1) — only the dashboard.
  if (new URLSearchParams(location.search).get('widget')) return;

  const overlay = document.getElementById('onboarding');
  if (!overlay) return;
  const steps = Array.from(overlay.querySelectorAll('.onb-step'));
  const TOTAL = steps.length;
  let cur = 1;
  const draft = { voice: false };

  async function ensureSession(){ try { await fetch('/api/session', {method:'POST'}); } catch(_){} }

  function renderProgress(){
    const p = document.getElementById('onb-progress');
    if (!p) return;
    p.innerHTML = '';
    for (let i = 1; i <= TOTAL; i++){
      const dot = document.createElement('span');
      if (i === cur) dot.className = 'active';
      else if (i < cur) dot.className = 'done';
      p.appendChild(dot);
    }
  }

  function show(n){
    cur = Math.max(1, Math.min(TOTAL, n));
    steps.forEach(s => { s.hidden = (Number(s.dataset.step) !== cur); });
    renderProgress();
  }

  function next(){ show(cur + 1); }

  // ── Step 2: API key ──
  async function saveKey(){
    const input = document.getElementById('onb-key');
    const status = document.getElementById('onb-key-status');
    const key = (input?.value || '').trim();
    if (!key){ next(); return; } // empty = treat as "later"
    status.className = 'onb-status'; status.textContent = 'Checking…';
    try {
      await ensureSession();
      const r = await fetch('/api/setup/provider-key', {
        method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ provider:'openrouter', key }),
      });
      if (r.ok){ status.className = 'onb-status ok'; status.textContent = 'Connected ✓'; setTimeout(next, 500); }
      else { const d = await r.json().catch(()=>({})); status.className = 'onb-status err'; status.textContent = d.detail || 'That key didn’t work — double-check and try again.'; }
    } catch(_){ status.className = 'onb-status err'; status.textContent = 'Couldn’t reach the app. Try again.'; }
  }

  // ── Step 3: preferences ──
  function applyThemeNow(t){
    let r = t;
    if (t === 'auto'){ try { r = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light':'dark'; } catch(_){ r='dark'; } }
    document.documentElement.setAttribute('data-theme', r);
  }
  async function savePrefs(patch){
    try {
      await ensureSession();
      await fetch('/api/preferences', { method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({ preferences: patch }) });
    } catch(_){}
  }

  // ── Finish ──
  async function finish(){
    await savePrefs({ onboarded: true, speak_replies: draft.voice, voice_input: draft.voice });
    overlay.hidden = true;
  }

  function wire(){
    overlay.querySelectorAll('[data-onb-next]').forEach(b => b.addEventListener('click', next));
    overlay.querySelectorAll('[data-onb-skip]').forEach(b => b.addEventListener('click', next));
    document.getElementById('onb-key-save')?.addEventListener('click', saveKey);
    document.getElementById('onb-key')?.addEventListener('keydown', e => { if (e.key === 'Enter') saveKey(); });
    document.getElementById('onb-get-key')?.addEventListener('click', () => {
      try { window.open('https://openrouter.ai/keys', '_blank'); } catch(_){}
    });
    // theme segmented control
    const seg = document.getElementById('onb-theme');
    seg?.querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
      seg.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const t = btn.dataset.theme;
      applyThemeNow(t);
      savePrefs({ theme: t });
    }));
    document.getElementById('onb-voice')?.addEventListener('change', e => { draft.voice = e.target.checked; });
    document.getElementById('onb-finish')?.addEventListener('click', finish);
  }

  async function maybeShow(){
    try {
      await ensureSession();
      const r = await fetch('/api/preferences', { credentials:'include' });
      const prefs = r.ok ? (await r.json()).preferences : {};
      if (prefs && prefs.onboarded) return; // already done
    } catch(_){ /* if we can't tell, show it — better than hiding setup */ }
    wire();
    show(1);
    overlay.hidden = false;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeShow);
  } else { maybeShow(); }
})();

/* ---------------- Effort slider (Low / Medium / High / Max) ----------------
   Free models can't tune reasoning, so this just trades speed for a bigger
   model. Persists to /api/preferences as `effort`. Max goes rainbow (CSS). */
(function initEffort(){
  if (new URLSearchParams(location.search).get('widget')) return; // dashboard only
  const root  = document.getElementById('effort');
  const track = document.getElementById('effort-track');
  const fill  = document.getElementById('effort-fill');
  const thumb = document.getElementById('effort-thumb');
  const nameEl= document.getElementById('effort-name');
  const hintEl= document.getElementById('effort-hint');
  if (!root || !track) return;

  const LEVELS = ['low','medium','high','max'];
  const META = {
    low:    { name: 'Low',    hint: 'Fastest — a snappy small model' },
    medium: { name: 'Medium', hint: 'Best all-round free model' },
    high:   { name: 'High',   hint: 'Stronger — a bigger free model' },
    max:    { name: 'Max',    hint: 'The smartest model available' },
  };
  const stops = Array.from(track.querySelectorAll('.effort-stop'));
  let idx = 1;
  let saveTimer = null;

  async function ensureSession(){ try { await fetch('/api/session', {method:'POST'}); } catch(_){} }

  function persist(){
    clearTimeout(saveTimer);
    const effort = LEVELS[idx];
    saveTimer = setTimeout(async () => {
      try {
        await ensureSession();
        await fetch('/api/preferences', { method:'POST', credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ preferences: { effort } }) });
      } catch(_){}
    }, 280);
  }

  function paint(){
    const lvl = LEVELS[idx];
    const pct = (idx / 3) * 100;
    root.dataset.level = lvl;
    fill.style.width = pct + '%';
    thumb.style.left = pct + '%';
    nameEl.textContent = META[lvl].name;
    hintEl.textContent = META[lvl].hint;
    track.setAttribute('aria-valuenow', String(idx));
    track.setAttribute('aria-valuetext', META[lvl].name);
    stops.forEach((s, i) => s.classList.toggle('passed', i <= idx));
  }

  function setIdx(n, save){
    const clamped = Math.max(0, Math.min(3, n));
    const changed = clamped !== idx;
    idx = clamped;
    paint();
    if (save && changed) persist();
  }

  function ratioFromX(clientX){
    const r = track.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - r.left) / Math.max(1, r.width)));
  }

  // Live 1:1 drag — the thumb tracks the cursor exactly (no transition), and
  // the label snaps to the nearest stop. On release we re-enable the spring.
  function dragTo(ratio){
    const pct = ratio * 100;
    const near = Math.round(ratio * 3);
    const lvl = LEVELS[near];
    root.classList.add('dragging');
    root.dataset.level = lvl;
    fill.style.width = pct + '%';
    thumb.style.left = pct + '%';
    nameEl.textContent = META[lvl].name;
    hintEl.textContent = META[lvl].hint;
    stops.forEach((s, i) => s.classList.toggle('passed', (i / 3) <= ratio + 0.001));
  }

  // ── drag + click ──
  let dragging = false;
  track.addEventListener('pointerdown', (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch(_){}
    dragTo(ratioFromX(e.clientX));
  });
  track.addEventListener('pointermove', (e) => { if (dragging) dragTo(ratioFromX(e.clientX)); });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    root.classList.remove('dragging');          // re-enable the spring transition
    setIdx(Math.round(ratioFromX(e.clientX) * 3), true);  // snap + persist
  };
  track.addEventListener('pointerup', endDrag);
  track.addEventListener('pointercancel', () => { dragging = false; root.classList.remove('dragging'); paint(); });
  stops.forEach((s) => s.addEventListener('click', (e) => { e.stopPropagation(); setIdx(Number(s.dataset.i), true); }));

  // ── keyboard ──
  track.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown')  { e.preventDefault(); setIdx(idx - 1, true); }
    if (e.key === 'ArrowRight'|| e.key === 'ArrowUp')    { e.preventDefault(); setIdx(idx + 1, true); }
    if (e.key === 'Home') { e.preventDefault(); setIdx(0, true); }
    if (e.key === 'End')  { e.preventDefault(); setIdx(3, true); }
  });

  // ── load saved value ──
  async function load(){
    try {
      await ensureSession();
      const r = await fetch('/api/preferences', { credentials:'include' });
      if (!r.ok) { paint(); return; }
      const prefs = (await r.json()).preferences || {};
      const saved = LEVELS.indexOf(String(prefs.effort || 'medium'));
      setIdx(saved >= 0 ? saved : 1, false);
    } catch(_){ paint(); }
  }
  load();
  // Re-sync when Settings opens (the capsule may have changed effort meanwhile).
  document.getElementById('open-settings')
    ?.addEventListener('click', () => { setTimeout(load, 30); });
})();

/* ---------------- Provider key (Settings) ---------------- */
(function initProviderKey(){
  if (new URLSearchParams(location.search).get('widget')) return; // dashboard only
  const stateEl = document.getElementById('key-state');
  const input   = document.getElementById('provider-key-input');
  const saveBtn = document.getElementById('provider-key-save');
  const statusEl= document.getElementById('provider-key-status');
  if (!stateEl || !input || !saveBtn) return;

  async function ensureSession(){ try { await fetch('/api/session', {method:'POST'}); } catch(_){} }

  async function refreshState(){
    try {
      await ensureSession();
      const r = await fetch('/api/setup/status', { credentials:'include' });
      const d = r.ok ? await r.json() : {};
      const set = !!(d.providers && d.providers.openrouter);
      stateEl.textContent = set ? 'Key set ✓' : 'No key yet';
      stateEl.className = 'key-state ' + (set ? 'set' : 'unset');
    } catch(_){
      stateEl.textContent = '—'; stateEl.className = 'key-state';
    }
  }

  async function save(){
    const key = (input.value || '').trim();
    if (!key){ statusEl.textContent = 'Paste a key first.'; statusEl.className = 'key-status err'; return; }
    saveBtn.disabled = true; statusEl.textContent = 'Checking…'; statusEl.className = 'key-status';
    try {
      await ensureSession();
      const r = await fetch('/api/setup/provider-key', {
        method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ provider:'openrouter', key }),
      });
      if (r.ok){
        statusEl.textContent = 'Saved ✓'; statusEl.className = 'key-status ok';
        input.value = '';
        refreshState();
        setTimeout(() => { statusEl.textContent = ''; }, 2500);
      } else {
        const d = await r.json().catch(()=>({}));
        statusEl.textContent = d.detail || 'That key didn’t work — double-check and try again.';
        statusEl.className = 'key-status err';
      }
    } catch(_){
      statusEl.textContent = 'Couldn’t reach the app. Try again.'; statusEl.className = 'key-status err';
    } finally { saveBtn.disabled = false; }
  }

  saveBtn.addEventListener('click', save);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
  document.getElementById('open-settings')?.addEventListener('click', () => { setTimeout(refreshState, 30); });
  refreshState();
})();
