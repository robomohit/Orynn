# AI Computer

[![CI](https://github.com/robomohit/Ai_computer/actions/workflows/ci.yml/badge.svg)](https://github.com/robomohit/Ai_computer/actions/workflows/ci.yml)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-5BE0D0)](LICENSE)

**An autonomous AI agent that controls your computer using plain English.** Give it a goal - it plans, acts, and shows you exactly what it is doing in real time, in a floating glass capsule that sits on top of your desktop.

> Free to run on [OpenRouter](https://openrouter.ai)'s free-tier models (subject to their limits). Coding and browser modes work on Windows, macOS, and Linux; native desktop control is Windows-only.

> **Security note:** AI Computer is local automation software that can read local
> context, call external LLM providers, run code, browse the web, and control
> desktop apps when you allow it. Do not expose the dashboard to the public
> internet, never commit `.env`, and review sensitive actions before approval.
> See [SECURITY.md](SECURITY.md) before publishing or packaging it.

### What makes it different

Most computer-use agents take a **screenshot every step** and guess pixel coordinates - slow, expensive, and brittle. AI Computer drives native Windows apps through **UI Automation**: it clicks controls **by name** (no screenshots, no pixel guessing), so it is faster, cheaper, and far more reliable. It only falls back to on-screen-text OCR, then pixels, when a control genuinely is not in the accessibility tree.

- **UIA-first desktop control** - drives Notepad, Excel, Word, Discord, Spotify, VS Code, and other Windows apps by control name.
- **Floating glass capsule** - frameless, translucent, always-on-top; press `Ctrl+Shift+Space` to summon it.
- **Runs free** - defaults to OpenRouter `:free` models end to end.
- **Uses your browser** - for web tasks (Gmail, Maps, GitHub, and more) it drives Chrome the way you would, with no extra accounts to connect.
- **Watch it work** - live action ticker and an aqua glow around the app it is touching.

---

## Requirements

- **Windows 10 / 11** for the floating capsule and native desktop control. macOS/Linux get coding and browser modes via the web dashboard.
- **Python 3.10 or newer** - [python.org/downloads](https://python.org/downloads) (tick *"Add python.exe to PATH"* during install).
- One LLM API key - a **free** OpenRouter key is all you need.

---

## Quick Start (3 steps)

### 1. Clone and setup

**Windows** - double-click `setup.bat`, or run in terminal:

```cmd
git clone https://github.com/robomohit/Ai_computer.git
cd Ai_computer
setup.bat
```

**Mac / Linux:**

```bash
git clone https://github.com/robomohit/Ai_computer.git
cd Ai_computer
chmod +x setup.sh && ./setup.sh
```

### 2. Add your API key

Open `.env` and paste in at least one key:

```env
OPENROUTER_API_KEY=sk-or-v1-...   # free tier - recommended
```

> Get a free-tier key at [openrouter.ai](https://openrouter.ai/). Availability and rate limits are controlled by OpenRouter.

### 3. Launch

Two native desktop surfaces (no browser needed):

- **`start.bat`** - the floating glass **capsule** (the main product). Press **`Ctrl+Shift+Space`** any time to show/hide it.
- **`start_dashboard.bat`** - the full **dashboard** in its own native window (sessions, models, MCP, skills).

> Advanced: `start_web.bat` serves the dashboard over HTTP (http://localhost:8080) for a browser or another device.

---

## Modes

| Mode | What it does |
|---|---|
| **Coding** | Writes, edits, and runs code. No screenshots - fast and accurate. |
| **Browser** | Controls a headless Chrome browser via the accessibility tree. Fills forms, navigates sites, reads pages. |
| **Desktop** | Drives native and Electron apps (Notepad, Discord, VS Code, Spotify, and more) through **Windows UI Automation** by control name. It avoids screenshots and pixel guessing unless the accessibility tree cannot expose the target. |

The mode is **auto-detected** from your goal, or you can pick it manually.

---

## The floating capsule (main product)

`start.bat` launches the native glass capsule (`run_desktop.py`) - a frameless,
translucent, always-on-top window with real Windows Acrylic blur that adapts to
light/dark backdrops. Type a goal, watch the agent work with a live action
ticker and an aqua glow around the target app.

```bash
# Manual launch / dashboard window:
python run_desktop.py              # floating capsule
python run_desktop.py --dashboard  # full dashboard in a native window
```

> Desktop control (UI Automation) is **Windows-only**. Coding and browser modes
> run on Windows, macOS, and Linux via the web dashboard (`start_web.bat`).

---

## Semantic Memory (optional)

For richer memory that uses vector search instead of keyword matching:

```bash
pip install -r requirements-memory.txt
```

Then add to `.env`:

```env
USE_CHROMA=1
```

---

## API Keys

| Variable | Provider | Cost |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter | Free tier available |
| `ANTHROPIC_API_KEY` | Claude (Anthropic) | Paid |
| `OPENAI_API_KEY` | GPT-4o (OpenAI) | Paid |
| `GOOGLE_API_KEY` | Gemini (Google) | Paid |
| `GROQ_API_KEY` | Llama (Groq) | Free tier available |
| `AGENT_API_KEY` | Internal auth | Auto-generated if blank |

### Desktop reliability (optional)

Desktop control runs on the **free** UIA tier by default. The free models are
fast and handle single/moderate tasks well, but can derail on long multi-step
sequences. For maximum reliability, opt in to a stronger model **for desktop
tasks only** - free stays the default everywhere else:

```bash
DESKTOP_MODEL=claude-3-5-sonnet-20241022   # or gpt-4o, or any OpenRouter id
```

Leave it blank to stay fully free.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Enter` | Send task |
| `Shift+Enter` | New line |
| `Ctrl+K` | Command palette |
| `Space` | Pause / resume |
| `Esc` | Close modal |

---

## Docker

```bash
docker-compose up --build
```

---

## Architecture

```text
Capsule / Dashboard (PySide6 + pywebview)
   |
   | SSE (live action stream)
   v
FastAPI (app/main.py)
   |
   +-- AgentService (app/agent.py)
       +-- Providers -> OpenRouter / OpenAI / Anthropic / other model providers
       +-- ToolExecutor -> shell / files / browser / UIA desktop (app/tools.py)
       |   +-- Hybrid resolver: UIA control -> on-screen-text OCR -> pixel
       +-- SafetyManager -> blocks dangerous / irreversible actions
       +-- LogEmitter -> streams every step to the UI
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Python not found` when running `setup.bat` | Install Python 3.10+ from [python.org](https://python.org/downloads) and tick **"Add python.exe to PATH"**, then reopen the terminal. |
| Capsule does not appear after `start.bat` | It is always-on-top but may be behind a fullscreen window - press **`Ctrl+Shift+Space`** to summon it. |
| "No API key" on first run | Paste a free OpenRouter key when prompted (get one at [openrouter.ai/keys](https://openrouter.ai/keys)), or add `OPENROUTER_API_KEY=` to `.env`. |
| Browser mode does nothing | Run `python -m playwright install chromium` to fetch the browser. |
| Desktop agent cannot find a control | Make sure the target app is open and focused; for Electron apps (Discord, Slack, and similar apps) it auto-unlocks accessibility, which may need the app restarted once. |
| Rate-limited / model busy | Free OpenRouter models have shared limits - wait a moment, or set `DESKTOP_MODEL` / a paid key for headroom. |

---

## Contributing

Issues and PRs are welcome - see **[CONTRIBUTING.md](CONTRIBUTING.md)** for the
full guide. In short, run the test suite before submitting (CI runs it on Windows
and Ubuntu for every PR):

```bash
python -m pytest -q
```

---

## License

**[PolyForm Noncommercial 1.0.0](LICENSE)** (c) [robomohit](https://github.com/robomohit)

Free to use, modify, and share for **any noncommercial purpose** - personal projects, study, research, hobby use, and nonprofits/education/government. **Commercial use is not permitted** under this license; for a commercial license, contact the author.

> This is a *source-available* license, not OSI "open source." You can read and learn from the code, but you may not ship it (or a derivative) commercially without a separate agreement. *Not legal advice - consult a lawyer for specifics.*
