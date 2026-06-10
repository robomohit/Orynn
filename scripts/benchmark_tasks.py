"""Run repeatable Orynn task benchmarks against a local server.

Example:
    python scripts/benchmark_tasks.py --base http://127.0.0.1:8080 --api-key "$env:AGENT_API_KEY"

This intentionally records raw outcomes instead of claiming headline numbers.
Use it to build honest benchmark reports over repeated runs.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from urllib import request
from urllib.error import HTTPError


DEFAULT_TASKS = [
    {
        "name": "notepad_release_note",
        "mode": "desktop",
        "goal": "Open Notepad, type 'Orynn benchmark run', save it to Desktop as orynn-benchmark.txt, then finish.",
    },
    {
        "name": "browser_release_summary",
        "mode": "browser",
        "goal": "Open the Orynn GitHub releases page and summarize the latest release in three bullets.",
    },
    {
        "name": "coding_smoke",
        "mode": "coding",
        "goal": "Inspect the repository and tell me the command to run the test suite. Do not edit files.",
    },
]


def _json_request(base: str, method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {body}") from exc


# Successful actions in these types prove the DESKTOP path was exercised. A
# "done" whose answer came from run_command (e.g. the model computing 47*89 in
# the shell instead of the Calculator UI) must NOT count as a desktop win — an
# early e2e run scored exactly that as a pass.
DESKTOP_INTERACTION_TYPES = {
    "uia_click", "uia_click_sequence", "uia_type",
    "mouse_click", "double_click", "right_click", "middle_click",
    "left_click_drag", "keyboard_type", "type_with_delay", "computer",
}


def _route_summary(action_results: list[dict], mode: str) -> dict:
    interactions = sum(
        1 for a in action_results
        if a.get("ok") and a.get("action_type") in DESKTOP_INTERACTION_TYPES
    )
    shell_answers = sum(
        1 for a in action_results
        if a.get("ok") and a.get("action_type") in {"run_command", "bash"}
        and "Launched (fire-and-forget)" not in str(a.get("output") or "")
    )
    summary = {
        "desktop_interactions": interactions,
        "non_launch_shell_results": shell_answers,
    }
    if mode == "desktop":
        summary["verified_desktop_path"] = interactions > 0
        if interactions == 0 and shell_answers > 0:
            summary["warning"] = (
                "result likely computed via shell, not the app UI — "
                "do not report this as a desktop-control success"
            )
    return summary


def _stream_until_done(base: str, api_key: str, task_id: str, timeout_s: float,
                       mode: str = "auto") -> dict:
    req = request.Request(
        base.rstrip("/") + f"/api/tasks/{task_id}/stream",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    started = time.perf_counter()
    events: list[dict] = []
    action_results: list[dict] = []
    screenshots = 0
    tool_failures = 0
    with request.urlopen(req, timeout=timeout_s) as resp:
        event_type = "message"
        for raw in resp:
            if time.perf_counter() - started > timeout_s:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                payload = {}
            events.append({"type": event_type, "data": payload})
            if event_type == "screenshot":
                screenshots += 1
            if event_type == "action_result":
                action_results.append(payload)
                if payload.get("ok") is False:
                    tool_failures += 1
            if event_type in {"done", "error", "cancelled"}:
                return {
                    "terminal_event": event_type,
                    "duration_s": round(time.perf_counter() - started, 3),
                    "events": len(events),
                    "screenshots": screenshots,
                    "tool_failures": tool_failures,
                    "route": _route_summary(action_results, mode),
                    "final": payload,
                }
    return {
        "terminal_event": "timeout",
        "duration_s": round(time.perf_counter() - started, 3),
        "events": len(events),
        "screenshots": screenshots,
        "tool_failures": tool_failures,
        "route": _route_summary(action_results, mode),
        "final": {},
    }


def run_benchmark(base: str, api_key: str, tasks: list[dict], timeout_s: float) -> dict:
    results = []
    for task in tasks:
        task_id = f"bench-{task['name']}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        _json_request(
            base,
            "POST",
            "/api/tasks",
            api_key,
            {
                "task_id": task_id,
                "goal": task["goal"],
                "mode": task.get("mode", "auto"),
                "autonomy_level": "careful",
                "plan_first": False,
            },
        )
        stream = _stream_until_done(base, api_key, task_id, timeout_s,
                                    mode=task.get("mode", "auto"))
        stream["submit_to_terminal_s"] = round(time.perf_counter() - started, 3)
        results.append({"task": task, "task_id": task_id, **stream})
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": base,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Orynn benchmark tasks against a local server.")
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", default=os.environ.get("AGENT_API_KEY") or os.environ.get("ORYNN_API_KEY") or "")
    parser.add_argument("--tasks-json", help="Optional JSON file with [{name, mode, goal}, ...].")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Set --api-key or AGENT_API_KEY/ORYNN_API_KEY.")

    tasks = DEFAULT_TASKS
    if args.tasks_json:
        tasks = json.loads(Path(args.tasks_json).read_text(encoding="utf-8"))

    report = run_benchmark(args.base, args.api_key, tasks, args.timeout)
    out = Path(args.out) if args.out else Path("benchmark-results") / f"{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
