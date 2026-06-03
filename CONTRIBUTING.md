# Contributing to AI Computer

Thanks for your interest! This is a **source-available** project under the
[PolyForm Noncommercial](LICENSE) license — you're welcome to read, learn from,
fix, and extend it for any noncommercial purpose.

## Getting set up

```bash
git clone https://github.com/robomohit/Ai_computer.git
cd Ai_computer
setup.bat            # Windows  (setup.sh on macOS/Linux)
```

Copy `.env.example` to `.env` and add a free OpenRouter key. You can develop the
coding/browser modes on any OS; native desktop control (UI Automation) is
Windows-only.

## Before you open a PR

1. **Run the tests** — they must pass:
   ```bash
   python -m pytest -q
   ```
2. **Keep it focused** — one logical change per PR.
3. **Never commit secrets** — `.env`, API keys, and `*.key` are gitignored; keep
   it that way.
4. **Touch the UI? Verify it renders** — launch `start.bat` (capsule) or
   `start_dashboard.bat` and confirm nothing regressed. Screenshots help reviewers.
5. **Match the style** — the codebase favors clear names and short docstrings
   that explain *why*, not *what*.

CI runs the suite on Windows + Ubuntu (Python 3.10 and 3.12) for every PR.

## Reporting bugs

Open an issue using the **Bug report** template. Include your OS, Python version,
which surface (capsule/dashboard/web) and mode (desktop/coding/browser), and any
error output. For questions, use **Discussions** rather than an issue.

## Good first contributions

- New **connectors + skills** (see `app/connectors.py` — each connector ships a
  short "manual" skill).
- **Troubleshooting** entries in the README for issues you hit.
- Test coverage for edge cases in `tests/`.

By contributing, you agree your contributions are licensed under the project's
PolyForm Noncommercial license.
