"""Floating capsule widget — Perplexity-Personal-Computer-style sidekick.

This package is the widget *product*, kept apart from the dashboard so it can
evolve without touching dashboard code.

Contract with the rest of the app:
    * Talks to the dashboard's FastAPI server over HTTP (no in-process imports
      of app.main / app.agent / app.tools).
    * The shared surface is the HTTP API (/api/tasks, /api/capsule/events) plus
      app/capsule_bridge.py — which the dashboard's tools use to push widget
      events back to the capsule. Don't move capsule_bridge in here; that
      would tie the dashboard to this package.

Rules of thumb if you're editing the widget:
    * Edit only files in this folder.
    * For new backend behavior, add an HTTP endpoint in app/main.py and call
      it from qt_shell.py — don't reach into dashboard internals directly.
"""
