# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Orynn.

Produces a self-contained desktop app (dist/Orynn/Orynn.exe) that
bundles the engine + UI into a runnable Windows desktop app.

Build:  python -m PyInstaller Orynn.spec --noconfirm
(or just run build.bat)

Notes:
- onedir build (a folder with the exe + deps) — more reliable + faster to start
  than onefile. Wrap dist/ with Inno Setup / NSIS to make a single installer.
- Browser mode (Playwright/Chromium) is intentionally NOT bundled — it's large
  and optional. The app installs it on demand; see PACKAGING.md.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("static", "static")]
binaries = []
hiddenimports = []

# Our own package — some submodules are imported dynamically (string paths,
# late imports), so collect them all to be safe.
hiddenimports += collect_submodules("app")

# Third-party packages that PyInstaller's static analysis tends to miss
# (native bits, lazy imports, data files).
_COLLECT = [
    "webview",      # native dashboard window (pywebview)
    "uiautomation", # UIA desktop control
    "comtypes",     # UIA / SAPI backend
    "winsdk",       # Windows.Media.Ocr + SpeechRecognition
    "uvicorn",      # ASGI server
    "fastapi",
    "starlette",
    "pydantic",
    "anyio",
    "httpx",
    "plyer",        # native notifications
]
for _pkg in _COLLECT:
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as _e:
        print(f"[spec] note: could not collect {_pkg}: {_e}")

# App icon (optional)
_icon = None
for _cand in ("app_icon.ico", "orynn_app_icon.png"):
    if os.path.exists(_cand):
        _icon = _cand
        break

a = Analysis(
    ["run_desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy/optional things we deliberately keep out of the core bundle. None of
    # these are needed to run the agent — the only consumer is the OPTIONAL
    # semantic-memory feature (chromadb, gated behind USE_CHROMA), which drags in
    # the entire ML stack (torch, onnxruntime, transformers, cv2, scipy…) and
    # would balloon the build to ~1.5 GB. memory.py falls back to keyword search
    # when chromadb isn't present, so excluding these is safe.
    excludes=[
        "playwright", "tkinter", "pytest",
        # semantic-memory ML stack (optional feature)
        "chromadb", "chroma", "sentence_transformers", "transformers",
        "torch", "torchvision", "torchaudio",
        "onnxruntime", "onnxruntime_tools",
        "cv2", "scipy", "pandas", "sklearn", "scikit_learn",
        "numba", "llvmlite", "sympy", "matplotlib", "networkx",
        "lightning", "pytorch_lightning", "tensorboard", "tensorflow",
        "datasets", "tokenizers", "safetensors", "accelerate",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Orynn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX can trip antivirus heuristics — leave off for trust
    console=True,         # TEMP DEBUG
    disable_windowed_traceback=False,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Orynn",
)
