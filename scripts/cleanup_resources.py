#!/usr/bin/env python3
"""Clean common orphan processes and print a small RAM snapshot.

This is intentionally conservative: it targets development/test leftovers for
AI Computer only (server, pytest, and browser automation children) and avoids
matching the current Python process.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path


BROWSER_PROCESS_NAMES = {"chromium", "chrome", "chrome_crashpad", "msedge"}
SHELL_PROCESS_NAMES = {"bash", "sh", "zsh", "tmux"}


def _mem_available_kb() -> str:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return line.split()[1]
    except Exception:
        pass
    return "unknown"


def _cmdline(pid: str) -> str:
    try:
        return Path("/proc", pid, "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except Exception:
        return ""


def _comm(pid: str) -> str:
    try:
        return Path("/proc", pid, "comm").read_text(encoding="utf-8").strip().lower()
    except Exception:
        return ""


def _parent_pid(pid: int) -> int:
    try:
        stat = Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
        return int(stat.split()[3])
    except Exception:
        return 0


def _ancestor_pids() -> set[int]:
    ancestors = {os.getpid()}
    pid = os.getppid()
    while pid and pid not in ancestors:
        ancestors.add(pid)
        pid = _parent_pid(pid)
    return ancestors


def _should_kill(proc_name: str, cmd: str) -> bool:
    if proc_name in SHELL_PROCESS_NAMES:
        return False
    if proc_name in BROWSER_PROCESS_NAMES or proc_name.startswith("chrom"):
        return True
    if "playwright" in cmd:
        return True
    if "uvicorn" in cmd and ("python" in proc_name or "uvicorn" in proc_name):
        return True
    if "pytest" in cmd and ("python" in proc_name or "pytest" in proc_name):
        return True
    return False


def main() -> int:
    protected_pids = _ancestor_pids()
    killed: list[str] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) in protected_pids:
            continue
        proc_name = _comm(proc.name)
        cmd = _cmdline(proc.name).lower()
        if not cmd or not _should_kill(proc_name, cmd):
            continue
        try:
            os.kill(int(proc.name), signal.SIGTERM)
            killed.append(proc.name)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    print(f"mem_available_kb={_mem_available_kb()}")
    print(f"killed_processes={len(killed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
