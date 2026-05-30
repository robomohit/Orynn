# AI Computer

An autonomous AI agent that controls your computer using plain English. Give it a goal — it plans, acts, and shows you exactly what it's doing in real time.

> Coding and browser modes run on Windows, macOS, and Linux; desktop control is Windows-focused. Free to run with OpenRouter free-tier models, subject to OpenRouter's limits.

---

## Quick Start (3 steps)

### 1. Clone & setup

**Windows** — double-click `setup.bat`, or run in terminal:
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
OPENROUTER_API_KEY=sk-or-v1-...   # free tier — recommended
```

> Get a free-tier key at [openrouter.ai](https://openrouter.ai/). Availability and rate limits are controlled by OpenRouter.

### 3. Launch

**Windows:** double-click **`start.bat`** — the floating glass capsule appears at the top of your screen.

- Press **`Ctrl+Shift+Space`** any time to show/hide it.
- Prefer a browser dashboard instead? Run **`start_web.bat`** and open **http://localhost:8080**.

---

## Modes

| Mode | What it does |
|---|---|
| **Coding** | Writes, edits, and runs code. No screenshots — fast and accurate. |
| **Browser** | Controls a headless Chrome browser via the accessibility tree. Fills forms, navigates sites, reads pages. |
| **Desktop** | Drives native + Electron apps (Notepad, Discord, VS Code, Spotify…) through **Windows UI Automation** — by control name, **no screenshots, no pixel guessing**. Glows the edge of the app it's working in so you can see what it's doing. |

The mode is **auto-detected** from your goal, or you can pick it manually.

---

## The floating capsule (main product)

`start.bat` launches the native glass capsule (`run_desktop.py`) — a frameless,
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
| `OPENROUTER_API_KEY` | OpenRouter | **Free tier available** ✓ |
| `ANTHROPIC_API_KEY` | Claude (Anthropic) | Paid |
| `OPENAI_API_KEY` | GPT-4o (OpenAI) | Paid |
| `GOOGLE_API_KEY` | Gemini (Google) | Paid |
| `GROQ_API_KEY` | Llama (Groq) | Free tier available |
| `AGENT_API_KEY` | Internal auth | Auto-generated if blank |

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

```
Browser UI  ──SSE──►  FastAPI (main.py)
                           │
                      AgentService (agent.py)
                      ├── PlannerProvider  → LLM APIs
                      ├── ToolExecutor     → shell / files / browser / desktop
                      ├── SafetyManager    → blocks dangerous commands
                      └── LogEmitter       → streams events to UI
```

---

## License

MIT — [robomohit](https://github.com/robomohit)
