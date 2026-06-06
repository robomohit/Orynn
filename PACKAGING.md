# Packaging Orynn for distribution

This builds Orynn into a **runnable Windows app bundle** that people can
launch without installing Python first. The project is source-available under
the PolyForm Noncommercial license, so packaging is about making installation
friendlier - not replacing the license or hiding that this repository exists.

> **Important:** PyInstaller packages Python bytecode and dependencies into a
> convenient app folder. It is not a security boundary and it does not change
> the source-available license terms. A determined expert can inspect or
> decompile a Python build, so do not rely on packaging to protect secrets.

---

## 1. Build it

```cmd
build.bat
```

That installs the build tools and runs PyInstaller. When it finishes you will have:

```text
dist\Orynn\Orynn.exe   <- double-click to run
dist\Orynn\...          <- bundled runtime + dependencies
```

The whole `dist\Orynn` folder **is** the app. You can zip it and share it,
and it runs on a Windows PC **without Python installed**.

> First build takes a few minutes. Re-runs are faster.

---

## 2. Make it a single installer (recommended)

Sharing a folder works, but a one-file installer is friendlier. Use **[Inno
Setup](https://jrsoftware.org/isinfo.php)** (free):

1. Install Inno Setup.
2. New Script Wizard -> point it at `dist\Orynn\`, main exe `Orynn.exe`.
3. Compile -> you get `Orynn-Setup.exe` - a normal Windows installer with
   Start-menu shortcut, uninstaller, etc.

Now you distribute **one file**: `Orynn-Setup.exe`.

---

## 3. What is included vs. not

| Included (works offline) | Not bundled (optional) |
|---|---|
| The agent engine + UIA desktop control | **Browser mode** (Playwright/Chromium) |
| Capsule + dashboard UI, all static assets | Semantic memory (Chroma) |
| Native notifications, voice (Windows built-in) | |

**Browser mode** is excluded to keep the download small. If a user needs it, the
app can fetch it on demand, or you bundle it by removing `playwright` from the
`excludes` list in `Orynn.spec` and running `python -m playwright install
chromium` into the build. Most desktop-control + coding tasks do not need it.

---

## 4. First-run config (API key)

On first launch the friendly wizard asks for a free OpenRouter key and writes it
to a `.env` next to the app. If you install into a read-only location (e.g.
`Program Files`), make sure the app's working directory is writable, or set the
key via an environment variable before launch. Running the app from a normal
folder (Desktop, Documents) just works.

---

## 5. Going further

If you need a commercial build, stronger tamper resistance, or a hosted variant,
these are separate engineering and licensing decisions:

- **Commercial license:** the public repository is PolyForm Noncommercial. Get a
  separate license before shipping Orynn or derivatives commercially.
- **Hosted engine:** move selected execution behind a service boundary if you
  need centralized updates, policy enforcement, or enterprise audit storage.
- **Native modules:** compile sensitive modules with Cython/Nuitka if you want
  stronger tamper resistance in a distributed app.

Either can layer on top of this build when you are ready.
