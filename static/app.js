  /* ---------------- tweak defaults (persisted via host protocol) ---------------- */
  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "theme": "light",
    "accentHue": 220,
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

  let historyItems = [];
  let activeHistoryItem = null;
  let activePlanCard = null;
  let liveStatusCard = null;
  let liveStatusMessage = '';
  let planSubtasks = [];
  let currentSubtaskIdx = 0;
  let subtaskEls = {};
  let screenshots = [];
  let currentMode = 'coding';
  let currentBackgroundMode = true;
  let currentStatus = 'ready';
  let currentIsolatedApp = '';
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

  const renderProjectFolderSummary = () => {
    const selected = projectFolderState.selectedPath;
    $('project-folder-name').textContent = selected ? pathLeaf(selected) : 'General mode';
    $('project-folder-path').textContent = selected ? selected : 'Desktop + Home access';
    $('project-folder-selection').textContent = projectFolderState.browsingPath || selected || 'General mode · Desktop + Home';
    if (!task) setTaskTitle();
  };

  const setProjectFolder = (value, { persist = true } = {}) => {
    projectFolderState.selectedPath = value || '';
    if (persist) persistProjectFolder(projectFolderState.selectedPath);
    renderProjectFolderSummary();
  };

  const closeProjectFolderModal = () => $('project-folder-modal').classList.remove('show');

  const renderProjectFolderShortcuts = () => {
    const wrap = $('project-folder-shortcuts');
    if (!wrap) return;
    wrap.innerHTML = '';
    projectFolderState.shortcuts.forEach((shortcut) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'folder-shortcut';
      btn.textContent = shortcutLabel(shortcut.id, shortcut.label);
      btn.addEventListener('click', () => loadProjectFolderBrowser(shortcut.path));
      wrap.appendChild(btn);
    });
  };

  const renderProjectFolderBrowser = (payload) => {
    projectFolderState.browsingPath = payload.path || '';
    projectFolderState.shortcuts = payload.shortcuts || [];
    renderProjectFolderShortcuts();
    $('project-folder-browser-status').textContent = `${payload.path || 'Folder'} · ${(payload.entries || []).length} items${payload.truncated ? ' · capped for speed' : ''}`;
    $('project-folder-selection').textContent = projectFolderState.browsingPath || 'General mode · Desktop + Home';
    $('project-folder-up').disabled = !payload.parent;

    const crumbs = $('project-folder-breadcrumbs');
    crumbs.innerHTML = '';
    (payload.breadcrumbs || []).forEach((crumb) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'folder-crumb';
      btn.textContent = crumb.name;
      btn.addEventListener('click', () => loadProjectFolderBrowser(crumb.path));
      crumbs.appendChild(btn);
    });

    const list = $('project-folder-list');
    const empty = $('project-folder-empty');
    list.innerHTML = '';

    const entries = payload.entries || [];
    if (!entries.length) {
      empty.style.display = '';
      return;
    }

    empty.style.display = 'none';
    entries.forEach((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `folder-entry${entry.is_dir && entry.path === projectFolderState.selectedPath ? ' active' : ''}${entry.is_dir ? '' : ' disabled'}`;
      row.disabled = !entry.is_dir;
      row.innerHTML = `
        <span class="folder-entry-icon" aria-hidden="true">
          ${entry.is_dir
            ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2V16a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 10h18"/></svg>'
            : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>'}
        </span>
        <span class="folder-entry-copy">
          <span class="folder-entry-name"></span>
          <span class="folder-entry-path"></span>
        </span>
        <span class="folder-entry-meta">${entry.is_dir ? 'Open' : 'File'}</span>
      `;
      row.querySelector('.folder-entry-name').textContent = entry.name || pathLeaf(entry.path);
      row.querySelector('.folder-entry-path').textContent = entry.path || '';
      if (entry.is_dir) {
        row.addEventListener('click', () => loadProjectFolderBrowser(entry.path));
      }
      list.appendChild(row);
    });
  };

  const loadProjectFolderBrowser = async (path = '') => {
    await keyReady;
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    $('project-folder-browser-status').textContent = 'Loading folders…';
    try {
      const payload = await api(`/api/browse-directory${query}`);
      renderProjectFolderBrowser(payload);
      return payload;
    } catch (err) {
      $('project-folder-browser-status').textContent = 'Could not load this directory.';
      toast(typeof err.detail === 'string' ? err.detail : 'Failed to browse folder.', 'warn', 2200);
      throw err;
    }
  };

  const openProjectFolderModal = async () => {
    $('project-folder-modal').classList.add('show');
    const targetPath = projectFolderState.browsingPath || projectFolderState.selectedPath || storedProjectFolder() || '';
    try {
      await loadProjectFolderBrowser(targetPath);
    } catch (_) {
      if (targetPath) await loadProjectFolderBrowser('');
    }
  };

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
  const humanize = (value = '') => value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const truncate = (value = '', limit = 500) => value.length > limit ? `${value.slice(0, limit)}\n…` : value;
  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const isTerminalStatus = (value = '') => /complete|failed|error|cancelled/i.test(value);

  const toast = (message, kind = 'info', ttl = 3200) => {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = message;
    $('toast-stack').appendChild(el);
    if (ttl > 0) setTimeout(() => el.remove(), ttl);
  };

  const removeWelcome = () => { const w = $('welcome'); if (w) w.remove(); };

  const bindExamples = () => {
    document.querySelectorAll('.example-btn').forEach((btn) => {
      btn.onclick = () => {
        $('input').value = btn.dataset.example || '';
        $('input').focus();
        autoGrow(); updateCharCount();
      };
    });
  };

  let _scrollPending = false;
  const scrollFeed = () => {
    if (_scrollPending) return;
    _scrollPending = true;
    requestAnimationFrame(() => { _scrollPending = false; const fs = $('feed-scroll'); if (fs) fs.scrollTop = fs.scrollHeight; });
  };

  // Minimal, injection-safe markdown renderer for agent replies.
  // HTML is escaped FIRST; only known-safe tags are then inserted.
  const renderMarkdown = (raw) => {
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    let text = String(raw || '');
    const blocks = [];
    // 1. fenced code blocks ```lang\n…\n``` — pull out, escape, stash
    text = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_, code) => {
      blocks.push('<pre class="md-pre"><code>' + esc(code.replace(/\n$/, '')) + '</code></pre>');
      return ' B' + (blocks.length - 1) + ' ';
    });
    // 2. escape everything else
    text = esc(text);
    // 3. inline code `…` (content already escaped)
    text = text.replace(/`([^`\n]+)`/g, (_, c) => '<code class="md-code">' + c + '</code>');
    // 4. bold then italic
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    // 5. bullet lists — group consecutive "- "/"* " lines
    text = text.replace(/(?:^|\n)((?:[-*] .+(?:\n|$))+)/g, (_, list) => {
      const items = list.trim().split(/\n/).map((l) => '<li>' + l.replace(/^[-*] /, '') + '</li>').join('');
      return '\n U' + (blocks.push('<ul class="md-ul">' + items + '</ul>') - 1) + ' ';
    });
    // 6. paragraphs + line breaks
    text = text.split(/\n{2,}/).map((p) => {
      const t = p.trim();
      if (!t) return '';
      if (/^ [BU]\d+ $/.test(t)) return t;
      return '<p>' + t.replace(/\n/g, '<br>') + '</p>';
    }).join('');
    // 7. restore stashed blocks/lists
    text = text.replace(/ [BU](\d+) /g, (_, i) => blocks[+i]);
    return text;
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

  const setStatus = (status) => {
    const map = { ready: 'Ready', running: 'Running', paused: 'Paused', complete: 'Complete', failed: 'Failed', error: 'Error', cancelled: 'Cancelled' };
    const key = (status || 'ready').toLowerCase();
    currentStatus = key;
    const sb = $('sb-status');
    if (sb) { sb.className = `sb-item sb-status sb-status-${key}`; sb.innerHTML = `<span class="sb-dot"></span><span class="sb-val">${map[key] || humanize(key)}</span>`; }
    const dotCls = { running: 'running', paused: 'paused', complete: 'done', failed: 'failed', error: 'failed' };
    const dot = $('topbar-dot');
    if (dot) dot.className = 'topbar-dot' + (dotCls[key] ? ' ' + dotCls[key] : '');
  };

  const isDesktopMode = (mode = currentMode) => mode === 'computer' || mode === 'computer_isolated';

  const desktopModeLabel = (mode = currentMode) => mode === 'computer_isolated' ? 'Isolated App control' : 'Desktop control';

  const setDesktopSessionActive = (active, mode = currentMode, isolatedApp = currentIsolatedApp) => {
    const banner = $('desktop-control-banner');
    if (!banner) return;
    banner.classList.toggle('show', !!active && isDesktopMode(mode));
    $('desktop-control-title').textContent = active ? 'AI Computer is using your computer' : 'Desktop control inactive';
    const scope = mode === 'computer_isolated'
      ? `${isolatedApp || 'Selected app'} only`
      : 'Full desktop view and control';
    $('desktop-control-detail').textContent = `${desktopModeLabel(mode)} · ${scope}`;
  };

  const requestDesktopAccess = ({ mode, isolatedApp }) => new Promise((resolve) => {
    const overlay = $('desktop-access');
    const list = $('desktop-access-list');
    const fullDesktop = mode === 'computer';
    $('desktop-access-title').textContent = fullDesktop ? 'Allow full desktop control?' : `Allow control of ${isolatedApp || 'selected app'}?`;
    $('desktop-access-reason').textContent = fullDesktop
      ? 'AI Computer will be able to see the desktop and use mouse, keyboard, screenshots, and windows during this task.'
      : `AI Computer will lock control to ${isolatedApp || 'the selected app'} where possible and use screenshots, focus, and keyboard input for this task.`;
    const rows = fullDesktop
      ? [
          ['Desktop screen', 'Visible to AI Computer during this session', 'Full control'],
          ['Mouse and keyboard', 'Can click, type, and use shortcuts while the task runs', 'Full control'],
          ['Other windows', 'May be visible in screenshots unless you use isolated mode', 'Visible']
        ]
      : [
          [isolatedApp || 'Target app', 'Primary app AI Computer may view and control', 'Full control'],
          ['Other windows', 'Not targeted; may only appear if Windows focus changes', 'Limited'],
          ['Stop control', 'Pause or cancel from the top bar at any time', 'Available']
        ];
    list.innerHTML = rows.map(([title, copy, badge]) => `
      <div class="desktop-access-row">
        <div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(copy)}</span></div>
        <div class="desktop-access-badge">${escapeHtml(badge)}</div>
      </div>
    `).join('');
    desktopAccessResolver = resolve;
    overlay.classList.add('show');
  });

  const setMode = (mode, isolated = false, isolatedApp = '') => {
    currentMode = mode || 'coding';
    currentIsolatedApp = isolatedApp || (currentMode === 'computer_isolated' ? ($('isolated-app-id')?.value || '').trim() : '');
    const modeLabels = { computer: 'desktop', computer_use: 'browser', computer_isolated: 'isolated', coding: 'coding' };
    let label = modeLabels[currentMode] ?? humanize(currentMode).toLowerCase();
    if (isolated && currentMode === 'computer') {
      const appName = isolatedApp ? ` · ${isolatedApp}` : '';
      label += ` (iso${appName})`;
    }
    const mp = $('mode-pill'); if (mp) mp.innerHTML = `<span class="pill-label">Mode</span><span class="pill-val">${label}</span>`;
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
    const dotState = status === 'running' ? 'running'
      : (status === 'done' || status === 'complete') ? 'done'
      : (status === 'failed' || status === 'error') ? 'failed'
      : (status === 'cancelled') ? 'cancelled' : '';
    item.innerHTML = `
      <span class="history-dot ${dotState}"></span>
      <span class="history-copy">
        <span class="history-goal"></span>
        <span class="history-meta">${relTime(taskRecord.created_at || taskRecord.timestamp || taskRecord.finished_at) || humanize(status || 'saved')}</span>
        <button type="button" class="history-retask" tabindex="-1">↻ Copy task</button>
      </span>
    `;
    item.querySelector('.history-goal').textContent = taskRecord.goal || '(untitled)';
    item.title = [taskRecord.mode, taskRecord.model].filter(Boolean).join(' / ');
    item.querySelector('.history-retask')?.addEventListener('click', (e) => {
      e.stopPropagation();
      const inp = $('input');
      inp.value = taskRecord.goal || '';
      inp.dispatchEvent(new Event('input'));
      inp.focus();
    });
    $('task-history').prepend(item);
    historyItems.unshift(item);
    bindHistoryItem(item, taskRecord.id);
    return item;
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

  const filterHistory = () => {
    const q = $('history-search').value.trim().toLowerCase();
    historyItems.forEach((item) => {
      const goal = item.querySelector('.history-goal')?.textContent.toLowerCase() || '';
      item.style.display = !q || goal.includes(q) ? '' : 'none';
    });
  };

  const addActiveHistoryItem = (goal) => {
    const empty = $('task-history').querySelector('.history-empty');
    if (empty) empty.remove();
    historyItems.forEach((item) => item.classList.remove('active'));
    const item = renderHistoryItem({ id: task, goal, status: 'running', created_at: new Date().toISOString() }, true);
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

  const ensureStatusCard = () => {
    if (liveStatusCard && liveStatusCard.isConnected) return liveStatusCard;
    const row = document.createElement('div');
    row.className = 'status-row';
    row.innerHTML = `
      <div class="status-copy">
        <div class="status-line">
          <div class="status-title">Agent update</div>
          <div class="status-age"></div>
        </div>
        <div class="status-subtitle"></div>
      </div>
    `;
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

       const safeId = subtask.id.replace(/[^a-zA-Z0-9]/g, '');
       const label = subtask.description.replace(/"/g, "'").slice(0, 30) + (subtask.description.length > 30 ? "..." : "");
       graphDef += `  ${safeId}["${label}"]\n`;
       graphDef += `  style ${safeId} fill:${color},color:#f8fafc,stroke:${stroke},stroke-width:2px,rx:6,ry:6\n`;

       if (subtask.dependencies && subtask.dependencies.length > 0) {
           subtask.dependencies.forEach(dep => {
               const safeDep = dep.replace(/[^a-zA-Z0-9]/g, '');
               graphDef += `  ${safeDep} --> ${safeId}\n`;
           });
       } else if (index > 0 && !subtask.dependencies && planSubtasks[index-1]) {
           // fallback sequential link if no deps explicitly defined
           const prevSafeId = planSubtasks[index-1].id.replace(/[^a-zA-Z0-9]/g, '');
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
    chevron.innerHTML = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4 2.5L7.5 6L4 9.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
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
      row.innerHTML = `<div class="subtask-icon">${String(index + 1).padStart(2,'0')}</div><div class="subtask-text"></div>`;
      row.querySelector('.subtask-text').textContent = subtask.description;
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
    const stepData = { label: humanize(actionType || 'action'), actionType: actionType || '', summary: summary || '', stateEl: null, subtitleEl: null, outputEl: null };
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
    chevron.innerHTML = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4 2.5L7.5 6L4 9.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    parts.head.appendChild(chevron);
    card.appendChild(parts.head);

    const body = document.createElement('div');
    body.className = 'card-body collapsed';
    const inner = document.createElement('div');
    inner.className = 'card-body-inner';
    body.appendChild(inner);

    const output = document.createElement('pre');
    output.className = 'tool-output hidden';
    const details = document.createElement('div');
    details.className = 'detail-list';

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

    const entry = {
      card, output, details,
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

  const appendDetailRow = (entry, eyebrow, title, copy = '', preview = '') => {
    if (!entry) return null;
    const row = document.createElement('div');
    row.className = 'detail-row';
    const head = document.createElement('div');
    head.className = 'detail-row-head';
    head.innerHTML = `<div><div class="detail-label"></div><div class="detail-title"></div></div>`;
    head.querySelector('.detail-label').textContent = eyebrow;
    head.querySelector('.detail-title').textContent = title || '';
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
      row.innerHTML = `
        <div class="detail-row-head">
          <div>
            <div class="detail-label">Terminal</div>
            <div class="detail-title"></div>
          </div>
        </div>
      `;
      const title = row.querySelector('.detail-title');
      title.textContent = command || 'Command output';
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
    planSubtasks = []; currentSubtaskIdx = 0; subtaskEls = {};
    screenshotStore.clear(); lastActionId = null; terminalStateKey = null;
    Object.keys(actionCards).forEach((k) => delete actionCards[k]);
    lastActiveCard = null; activeTurnSummary = null;

    setStatus('ready');
    setMode($('mode-id').value || 'coding');
    $('elapsed-time') && ($('elapsed-time').textContent = replay ? '--:--' : '00:00');
    const sbe = $('sb-elapsed-val'); if (sbe) sbe.textContent = replay ? '--:--' : '00:00';
    $('btn-pause').classList.add('hidden');
    $('btn-cancel').classList.add('hidden');
    $('btn-retry').classList.add('hidden');
    $('btn-copy-log').classList.add('hidden');
    $('btn-download-log').classList.add('hidden');

    if (!keepFeed) {
      $('feed').innerHTML = WELCOME_HTML;
      bindExamples();
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

  const processTaskEvent = (event, { replay = false, taskId = task, suppressToasts = false } = {}) => {
    if (event.type === 'task_created') return;

    if (event.type === 'reasoning') {
      // C1: step announcements are noise — the turn summary covers them.
      // Skipping (without finalizing) lets consecutive tools group into ONE summary.
      if (_isStepAnnouncement(event)) return;
      finalizeTurnSummary(); renderReasoning(event); return;
    }
    if (event.type === 'plan') { finalizeTurnSummary(); renderPlan(event); return; }

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
      const entry = ensureActionCard(event.action_id, event.action_type, event.args_summary || event.explanation || '');
      setActionState(entry, 'Running', 'running');
      entry.subtitleEl.textContent = event.args_summary || event.explanation || 'Working…';
      lastActionId = event.action_id || null;
      return;
    }

    if (event.type === 'action_result') {
      const entry = ensureActionCard(event.action_id, event.action_type, event.args_summary || '');
      setActionState(entry, event.ok ? 'OK' : 'Fail', event.ok ? 'ok' : 'fail');
      if (event.args_summary) entry.subtitleEl.textContent = event.args_summary;
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

    if (event.type === 'approval_required') {
      finalizeTurnSummary();
      finalizeLiveStatus();
      const reason = event.reason || event.action?.explanation || 'High risk action requires approval.';
      const entry = ensureActionCard(event.action_id, event.action?.type || 'approval', reason);
      setActionState(entry, 'Waiting', 'waiting');
      appendDetailRow(entry, 'Approval', humanize(event.action?.type || 'Action'), reason, JSON.stringify(event.action?.args || {}, null, 2));

      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;gap:8px;margin-top:8px;width:100%';
      const btnApprove = document.createElement('button');
      btnApprove.className = 'modal-btn primary';
      btnApprove.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnApprove.textContent = 'Approve';
      const btnDeny = document.createElement('button');
      btnDeny.className = 'modal-btn';
      btnDeny.style.cssText = 'padding:6px 12px;font-size:12px;flex:1';
      btnDeny.textContent = 'Deny';
      const doApp = (approve) => {
        api('/api/approvals', 'POST', { task_id: taskId, action_id: event.action_id, approve }).catch(() => {});
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
        // Show the model's actual final reply as a primary assistant message.
        // Fall back to the generic note only when there's no real answer.
        const reply = String(event.reason || '').trim();
        const isRealAnswer = reply.length > 12 && !/^(done|complete|completed|finished|task complete|ok)\.?$/i.test(reply);
        if (isRealAnswer) appendMessage(reply, 'assistant');
        else appendMessage('Task completed successfully.', 'system-success');
        if (!replay) { markHistoryFinal('done'); stopEverything(); if (!suppressToasts) toast('Task complete.', 'ok'); }
        else showPostRunControls();
      } else {
        setStatus('failed');
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
    if (isDesktopMode(requestedMode)) {
      const allowed = await requestDesktopAccess({ mode: requestedMode, isolatedApp: requestedIsolatedApp });
      if (!allowed) return;
    }

    task = Math.random().toString(36).slice(2);
    currentViewedTask = task;
    resetTaskView();

    appendMessage(goal, 'user');
    $('input').value = ''; updateCharCount(); autoGrow();

    setTaskTitle(goal, { mode: requestedMode, model: $('model-id').value, status: 'running' });
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

    try {
      await keyReady;
      const model = $('model-id').value;
      const mode = requestedMode;
      const isolated_app = requestedIsolatedApp || null;
      if (isDesktopMode(mode)) setDesktopSessionActive(true, mode, isolated_app || '');
      streamClosedManually = false;
      openStream(task);
      await api('/api/tasks', 'POST', { 
        task_id: task, goal, model, mode, isolated_app,
        active_skills: Array.from(activeSkillIds),
        project_folder: projectFolderState.selectedPath || null
      });
      if (window.innerWidth <= 1080) document.body.classList.remove('nav-open');
    } catch (err) {
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
    try {
      const title = $('task-title').textContent || 'Retry';
      const result = await api(`/api/tasks/${currentViewedTask}/retry`, 'POST');
      task = result.task_id; currentViewedTask = task;
      resetTaskView();
      appendMessage(title, 'user');
      setTaskTitle(title, { mode: $('mode-id').value, model: $('model-id').value, status: 'running' });
      setStatus('running');
      $('btn-pause').classList.remove('hidden');
      $('btn-cancel').classList.remove('hidden');
      $('send').classList.add('hidden');

      activeHistoryItem = addActiveHistoryItem(title);
      startTime = Date.now();
      timer = setInterval(updateClock, 1000);
      updateClock();
      setLiveStatus('Initializing', 'Retrying task…');
      streamClosedManually = false;
      openStream(task);
      toast(`Retried as ${task}.`, 'ok');
    } catch (err) { toast(err.detail || 'Retry failed.', 'err'); }
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

  const loadTaskLog = (taskId, events, sourceEl) => {
    currentViewedTask = taskId;
    resetTaskView({ replay: true });
    historyItems.forEach((item) => item.classList.remove('active'));
    sourceEl?.classList.add('active');
    activeHistoryItem = sourceEl || null;
    requestAnimationFrame(() => { const fs = $('feed-scroll'); if (fs) fs.scrollTop = 0; });

    const createdEvent = events.find((e) => e.type === 'task_created');
    const firstGoal = createdEvent?.goal;
    const title = sourceEl?.querySelector('.history-goal')?.textContent || firstGoal || 'Past task';
    setTaskTitle(title, { mode: createdEvent?.mode, model: createdEvent?.model });
    appendMessage(title, 'user');
    $('btn-retry').classList.remove('hidden');
    $('btn-copy-log').classList.remove('hidden');
    $('btn-download-log').classList.remove('hidden');

    events.forEach((e) => processTaskEvent(e, { replay: true, taskId, suppressToasts: true }));
    toast('Loaded task log.', 'info', 1800);
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
    select.onchange = () => { const sbm = $('sb-model-val'); if (sbm) sbm.textContent = select.options[select.selectedIndex]?.textContent || '—'; };
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
    api('/api/approvals', 'POST', { task_id: window.pendingTaskId, action_id: window.pendingApprovalId, approve }).catch(() => {});
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

    await delay(500);
    processTaskEvent({ type: 'action_start', action_id: 'a1', action_type: 'write_file', args_summary: 'app/main.py · 42 lines' });
    await delay(800);
    processTaskEvent({ type: 'file_change', action: 'write_file', path: 'app/main.py', content: 'from fastapi import FastAPI\nfrom .router import api\n\napp = FastAPI(title="AI Computer")\napp.include_router(api, prefix="/api")\n' });
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

  /* ---------------- init ---------------- */
  const init = async () => {
    applyTweaks();
    buildTweaks();
    syncTweaks();
    bindExamples();
    hydrateModelSelect();
    const _savedMode = localStorage.getItem('ai_computer_mode');
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
      const storedFolder = storedProjectFolder();
      if (storedFolder) {
        try {
          const payload = await loadProjectFolderBrowser(storedFolder);
          setProjectFolder(payload.path || storedFolder, { persist: true });
        } catch (_) {
          setProjectFolder('', { persist: true });
          await loadProjectFolderBrowser('');
        }
      } else {
        setProjectFolder('', { persist: false });
        await loadProjectFolderBrowser('');
      }
      api(`/api/models?cb=${Date.now()}`).then((r) => hydrateModelSelect(r.models)).catch(() => {});
      refreshProviderChips();
      api(`/api/tasks?cb=${Date.now()}`).then((r) => {
        const tasks = [...(r.tasks || [])].reverse();
        if (tasks.length) $('task-history').innerHTML = '';
        tasks.forEach((it) => renderHistoryItem(it));
        refreshHistoryCount();
      }).catch(() => {});
      loadSkills();
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
      container.innerHTML = Object.entries(providers).map(([name, status]) =>
        `<span class="provider-chip${status === 'ok' ? ' ok' : ''}" title="${name}: ${status}"><span class="chip-dot"></span>${name}</span>`
      ).join('');
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

  let allMCPServers = [];
  let codingBackendState = { backends: [], default: '' };

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
  $('project-folder-trigger').onclick = openProjectFolderModal;
  $('project-folder-close').onclick = closeProjectFolderModal;
  $('project-folder-up').onclick = () => {
    const crumbs = Array.from($('project-folder-breadcrumbs').querySelectorAll('.folder-crumb'));
    const parentCrumb = crumbs.length >= 2 ? crumbs[crumbs.length - 2] : null;
    if (parentCrumb) parentCrumb.click();
  };
  $('project-folder-apply').onclick = () => {
    setProjectFolder(projectFolderState.browsingPath || '', { persist: true });
    closeProjectFolderModal();
    toast(projectFolderState.selectedPath ? `Project folder set to ${projectFolderState.selectedPath}` : 'Project folder updated.', 'ok', 2200);
  };
  $('project-folder-clear').onclick = () => {
    setProjectFolder('', { persist: true });
    closeProjectFolderModal();
    toast('Project folder cleared. Agent will use desktop and home mode.', 'info', 2200);
  };
  $('project-folder-modal').addEventListener('click', (event) => {
    if (event.target === $('project-folder-modal')) closeProjectFolderModal();
  });

  /* ---------------- event wiring ---------------- */
  $('send').onclick = start;
  $('btn-cancel').onclick = () => { stopEverything(); cancelTask(); };
  $('btn-pause').onclick = togglePause;
  $('btn-retry').onclick = retryTask;
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
    localStorage.setItem('ai_computer_mode', val);
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
    if ($('project-folder-modal').classList.contains('show') && event.key === 'Escape') {
      event.preventDefault();
      closeProjectFolderModal();
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
      { group: 'Project Folder', label: 'Choose project folder', hint: projectFolderState.selectedPath || 'General mode', action: () => openProjectFolderModal() },
      { group: 'Project Folder', label: 'Clear project folder', hint: 'Desktop + Home', action: () => { setProjectFolder('', { persist: true }); toast('Project folder cleared.', 'info', 1800); } },
      { group: 'View', label: 'Focus prompt', hint: 'Ctrl L', action: () => { const el = $('input'); if (el) el.focus(); } },
      { group: 'View', label: 'Toggle history', hint: '', action: () => { const el = $('btn-history'); if (el) el.click(); } },
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

  /* ---------------- Liquid-Glass Sidekick widget ----------------
     Collapsed = a pill-shaped glass toggle card; expands into the
     Sidekick panel with a live step feed. 100% additive — funnels
     into the existing composer pipeline, mirrors agent state via a
     light poll, hooks speakAgentReply, renders the step feed, and
     supports drag-to-reposition + a global keyboard shortcut. */
  (function sidekickWidget() {
    const root = $('vorb-root');
    if (!root) return;

    // Widget-shell mode (?widget=1 / ?sidekick=1): the page is loaded
    // inside the frameless pywebview window — it IS the floating widget.
    const params = new URLSearchParams(location.search);
    const widgetShell = params.get('widget') === '1' || params.get('sidekick') === '1';
    if (widgetShell) {
      document.documentElement.classList.add('widget-shell');
      document.body.classList.add('widget-shell');
      root.classList.add('widget-shell', 'open');
    }

    const toggle = $('vorb-toggle');
    const closeBtn = $('vpanel-close');
    const closeShell = $('vpanel-close-shell');
    const activity = $('vpanel-activity-text');
    const vorbState = $('vorb-state');
    const subEl = $('vpanel-sub');
    const stepsEl = $('vpanel-steps');
    const logEl = $('vpanel-log');
    const emptyState = $('vlog-empty');
    const head = $('vpanel-head');
    const textIn = $('vpanel-text');
    const sendBtn = $('vpanel-send');
    const micBtn = $('vmic');
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (closeShell) closeShell.onclick = () => {
      // Ask the pywebview shell to close the window (no-op in a plain browser).
      try { if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) window.pywebview.api.close_window(); }
      catch (_) {}
      try { window.close(); } catch (_) {}
    };

    // --- activity: sync the panel row + the collapsed-toggle state label ---
    const setActivity = (text) => {
      if (activity) activity.textContent = text || 'Ready when you are.';
      if (vorbState) vorbState.textContent = String(text || 'Ready').slice(0, 18);
    };

    const openPanel = () => { root.classList.add('open'); if (textIn) textIn.focus(); };
    const closePanel = () => {
      if (widgetShell) { setActivity('Docked and ready.'); return; }  // window stays
      root.classList.remove('open');
    };
    if (closeBtn) closeBtn.onclick = closePanel;

    // --- conversation log ---
    const addLog = (text, who) => {
      if (!logEl) return;
      if (emptyState && emptyState.parentNode === logEl) logEl.removeChild(emptyState);
      const d = document.createElement('div');
      d.className = 'vlog-msg ' + (who === 'user' ? 'user' : 'agent');
      d.textContent = text;
      logEl.appendChild(d);
      logEl.scrollTop = logEl.scrollHeight;
      while (logEl.children.length > 24) logEl.removeChild(logEl.firstChild);
    };

    const submitGoal = (text) => {
      text = (text || '').trim();
      if (!text) return;
      const mainInput = $('input');
      if (!mainInput) return;
      if (task && sse) { addLog('A task is already running — let it finish first.', 'agent'); return; }
      mainInput.value = text;
      mainInput.dispatchEvent(new Event('input'));
      addLog(text, 'user');
      const sb = $('send');
      if (sb) sb.click();
      if (textIn) textIn.value = '';
    };

    if (sendBtn) sendBtn.onclick = () => submitGoal(textIn.value);
    if (textIn) textIn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); submitGoal(textIn.value); }
    });

    // --- voice: tap to start; auto-submits on the final transcript ---
    if (!SR) { if (micBtn) micBtn.style.display = 'none'; }
    else {
      let rec = null, listening = false, finalText = '';
      micBtn.onclick = () => {
        if (listening && rec) { try { rec.stop(); } catch (_) {} return; }
        rec = new SR();
        rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false;
        finalText = '';
        rec.onstart = () => { listening = true; root.classList.add('listening'); setActivity('Listening…'); };
        rec.onresult = (e) => {
          let t = '';
          for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
          finalText = t; if (textIn) textIn.value = t;
        };
        rec.onerror = () => {};
        rec.onend = () => {
          listening = false; root.classList.remove('listening');
          if (finalText.trim()) submitGoal(finalText);
          else setActivity('Didn’t catch that — try again.');
        };
        try { rec.start(); } catch (_) { listening = false; root.classList.remove('listening'); }
      };
    }

    // --- live step feed: mirror the last 5 feed step titles into the panel ---
    const STEP_TICK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>';
    let stepsFingerprint = '';
    const syncSteps = () => {
      if (!stepsEl) return;
      const titles = [];
      document.querySelectorAll(
        '#feed .turn-summary-title, #feed .turn-summary-head, #feed .feed-card .card-title'
      ).forEach((n) => {
        const t = (n.textContent || '').replace(/\s+/g, ' ').trim();
        if (t) titles.push(t);
      });
      const last5 = titles.slice(-5);
      const fp = last5.join('|');
      if (fp === stepsFingerprint) return;
      stepsFingerprint = fp;
      stepsEl.innerHTML = '';
      last5.forEach((t, i) => {
        const row = document.createElement('div');
        row.className = 'vstep' + (i === last5.length - 1 ? ' is-last' : '');
        const tick = document.createElement('span');
        tick.className = 'vstep-tick'; tick.innerHTML = STEP_TICK;
        const label = document.createElement('span');
        label.className = 'vstep-label'; label.textContent = t.slice(0, 80);
        row.appendChild(tick); row.appendChild(label);
        stepsEl.appendChild(row);
      });
      stepsEl.scrollTop = stepsEl.scrollHeight;
    };

    // --- mirror agent state every 700ms ---
    let lastStatus = '', lastLive = '';
    setInterval(() => {
      const st = (typeof currentStatus !== 'undefined' && currentStatus) || 'ready';
      const live = (typeof liveStatusMessage !== 'undefined' && liveStatusMessage) || '';
      root.classList.toggle('busy', st === 'running');
      if (st !== lastStatus || live !== lastLive) {
        lastStatus = st; lastLive = live;
        const labels = {
          ready: 'Ready', running: live || 'Working on it…',
          complete: 'Done', failed: 'Task failed', error: 'Error',
          paused: 'Paused', cancelled: 'Cancelled'
        };
        setActivity(labels[st] !== undefined ? labels[st] : st);
        if (subEl) {
          const subs = { ready: 'sidekick', running: 'working', complete: 'done', failed: 'failed', error: 'error', paused: 'paused', cancelled: 'cancelled' };
          subEl.textContent = subs[st] || 'sidekick';
        }
      }
      syncSteps();
    }, 700);

    // --- capture agent replies into the log (keeps read-aloud intact) ---
    const _speak = speakAgentReply;
    speakAgentReply = function (text) {
      try {
        let clean = String(text || '').trim();
        if (clean.startsWith('{') && clean.endsWith('}')) {
          try { const o = JSON.parse(clean); clean = o.reason || o.answer || o.text || o.message || clean; } catch (_) {}
        }
        if (clean) addLog(clean.slice(0, 600), 'agent');
      } catch (_) {}
      return _speak.apply(this, arguments);
    };

    // --- drag-to-reposition (dashboard mode only) ---
    // Position persists under a versioned key so stale coords are ignored.
    const POS_KEY = 'ai-computer.vorb-position.v2';
    let didDrag = false;
    if (!widgetShell) {
      try {
        const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
        if (saved && typeof saved.right === 'number') {
          root.style.right = saved.right + 'px';
          root.style.bottom = saved.bottom + 'px';
        }
      } catch (_) {}
      let dragging = false, ox = 0, oy = 0, moved = 0;
      const onMove = (e) => {
        if (!dragging) return;
        moved += Math.abs(e.clientX - ox) + Math.abs(e.clientY - oy);
        const cr = Math.max(6, Math.min(window.innerWidth - 70,
          parseFloat(root.style.right || '26') - (e.clientX - ox)));
        const cb = Math.max(6, Math.min(window.innerHeight - 70,
          parseFloat(root.style.bottom || '118') - (e.clientY - oy)));
        ox = e.clientX; oy = e.clientY;
        root.style.right = cr + 'px'; root.style.bottom = cb + 'px';
      };
      const onUp = () => {
        dragging = false;
        didDrag = moved > 5;
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        try { localStorage.setItem(POS_KEY, JSON.stringify({
          right: parseFloat(root.style.right) || 26,
          bottom: parseFloat(root.style.bottom) || 118
        })); } catch (_) {}
        setTimeout(() => { didDrag = false; }, 0);
      };
      const startDrag = (e) => {
        dragging = true; moved = 0; ox = e.clientX; oy = e.clientY;
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp);
      };
      if (toggle) toggle.addEventListener('pointerdown', startDrag);
      if (head) head.addEventListener('pointerdown', startDrag);
    }

    // open the panel on toggle click — but not when the click ended a drag
    if (toggle) toggle.addEventListener('click', () => { if (!didDrag) openPanel(); });

    // --- global shortcut: Ctrl+Shift+Space toggles the Sidekick panel ---
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.code === 'Space') {
        if (widgetShell) return;  // the window handles show/hide in shell mode
        e.preventDefault();
        root.classList.toggle('open');
        if (root.classList.contains('open') && textIn) textIn.focus();
      }
    });
  })();

  init();
