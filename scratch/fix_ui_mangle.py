from pathlib import Path

p = Path('static/index_v2.html')
c = p.read_text(encoding='utf-8')

WELCOME_HTML = r"""  const WELCOME_HTML = `
    <section class="welcome" id="welcome">
      <h3>The ultimate computer agent.</h3>
      <p>A unified stream for complex planning, parallel sub-tasks, and isolated application control. Powered by Claude 3.5 Sonnet and Gemini 2.0 Flash.</p>
      <div class="example-grid">
        <button class="example-btn" type="button" data-example="Create a premium FastAPI dashboard with SQLite and JWT auth.">
          Build a coding task
          <span>Parallel workers will handle files, tests, and documentation.</span>
        </button>
        <button class="example-btn" type="button" data-example="Open Notepad in isolated mode and type a summary of the current workspace architecture.">
          Isolated App Control
          <span>Control local apps in the background while you stay productive.</span>
        </button>
        <button class="example-btn" type="button" data-example="Search the web for the latest AI coding trends and save a markdown report.">
          Web Research
          <span>Browser Cowork mode uses sandboxed Playwright for high reliability.</span>
        </button>
        <button class="example-btn" type="button" data-example="Analyze this project and suggest 3 ways to optimize agent performance.">
          System Analysis
          <span>The agent reads your workspace and suggests structural improvements.</span>
        </button>
      </div>
    </section>
  `;"""

STATUS_HELPERS = r"""  const ensureStatusCard = () => {
    if (liveStatusCard && liveStatusCard.isConnected) return liveStatusCard;
    const row = document.createElement('div');
    row.className = 'status-row active';
    row.innerHTML = `
      <div class="status-dot thinking-dot"></div>
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

  const finalizeLiveStatus = (workerId = '') => {
    if (!liveStatusCard || !liveStatusCard.isConnected) return;
    liveStatusCard.style.display = 'none';
    liveStatusCard = null;
    liveStatusMessage = '';
  };"""

# Replace the mangled section
import re
# We need to find the start of WELCOME_HTML and end of finalizeLiveStatus
# Since the middle is mangled, we'll replace the whole chunk.

start_marker = "  const WELCOME_HTML = `"
end_marker = "  const finalizeLiveStatus = (workerId = '') => {"
# Find the end of finalizeLiveStatus block
end_block_marker = "    liveStatusMessage = '';\n  };"

# Actually, let's just use the view_file ranges to be safe.
# Start of welcome is 1523. End of finalize is 1786.

lines = c.splitlines(keepends=True)
# Ensure we have the right lines
# Line 1523 is index 1522
# Line 1786 is index 1785

new_lines = lines[:1522]
new_lines.append(WELCOME_HTML + "\n\n")
# Add the intermediate stuff (api, etc.)
# I need to see what's between WELCOME_HTML and setLiveStatus
