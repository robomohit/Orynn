# Packaging AI Computer for distribution

This builds AI Computer into a **runnable app that does not include your source
code** - so you can hand people the product while your repository stays private.

> **Why this works:** users download and run a compiled app. The Python source
> (the engine, the UIA resolver, your heuristics) is bundled in a form they
> install and *run*, not *read*. It is the same way you use Spotify or Discord
> without ever seeing their code. A determined expert could decompile a Python
> build, so treat it as "hard to copy," not "impossible." For maximum secrecy,
> run the engine as a hosted service instead (see "Going further" below).

---

## 1. Build it

```cmd
build.bat
```

That installs the build tools and runs PyInstaller. When it finishes you will have:

```text
dist\AI Computer\AI Computer.exe   <- double-click to run
dist\AI Computer\...               <- bundled runtime + dependencies
```

The whole `dist\AI Computer` folder **is** the app. You can zip it and share it,
and it runs on a Windows PC **without Python installed**.

> First build takes a few minutes. Re-runs are faster.

---

## 2. Make it a single installer (recommended)

Sharing a folder works, but a one-file installer is friendlier. Use **[Inno
Setup](https://jrsoftware.org/isinfo.php)** (free):

1. Install Inno Setup.
2. New Script Wizard -> point it at `dist\AI Computer\`, main exe `AI Computer.exe`.
3. Compile -> you get `AI-Computer-Setup.exe` - a normal Windows installer with
   Start-menu shortcut, uninstaller, etc.

Now you distribute **one file**: `AI-Computer-Setup.exe`.

---

## 3. What is included vs. not

| Included (works offline) | Not bundled (optional) |
|---|---|
| The agent engine + UIA desktop control | **Browser mode** (Playwright/Chromium) |
| Capsule + dashboard UI, all static assets | Semantic memory (Chroma) |
| Native notifications, voice (Windows built-in) | |

**Browser mode** is excluded to keep the download small. If a user needs it, the
app can fetch it on demand, or you bundle it by removing `playwright` from the
`excludes` list in `AI-Computer.spec` and running `python -m playwright install
chromium` into the build. Most desktop-control + coding tasks do not need it.

---

## 4. First-run config (API key)

On first launch the friendly wizard asks for a free OpenRouter key and writes it
to a `.env` next to the app. If you install into a read-only location (e.g.
`Program Files`), make sure the app's working directory is writable, or set the
key via an environment variable before launch. Running the app from a normal
folder (Desktop, Documents) just works.

---

## 5. Going further (maximum source protection)

PyInstaller hides the source well but is not bulletproof. If you want the engine
to be *truly* unreadable:

- **Hosted engine:** keep the UIA resolver on a server and have the shipped app
  call it over the network. Strongest protection, but needs internet + infra and
  weakens the "fully local & free" pitch.
- **Closed binary module:** compile the sensitive modules (e.g. the resolver)
  with Cython/Nuitka into native `.pyd` files and ship those instead of `.py`.
  Much harder to decompile than plain PyInstaller bytecode.

Either can layer on top of this build when you are ready.
