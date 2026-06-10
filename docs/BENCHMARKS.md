# Benchmark Plan

Orynn should publish honest numbers, not vibes. This document defines the benchmark suite to run before claiming speed/cost wins.

## What We Measure

- **Steps to completion**: fewer model turns means faster and cheaper.
- **Time to completion**: wall-clock seconds from task start to `finish`.
- **Vision-token avoidance**: number of screenshots sent to a model.
- **Local reliability**: pass/fail over repeated runs.
- **Recovery quality**: whether failures are reflected and retried safely.

## Benchmark Tasks

| Task | Mode | Success Criteria |
|---|---|---|
| Notepad write + save | Desktop/UIA | File exists with expected text |
| Calculator expression | Desktop/UIA | Correct displayed result |
| Discord message draft | Desktop/UIA | Text appears in input without sending |
| GitHub release summary | Browser | Correct release title + summary |
| Small code edit | Coding | Tests pass |
| Long multi-step desktop form | Desktop/UIA | All fields filled correctly |

## Baselines To Compare

- Orynn UIA-first path with free model.
- Orynn screenshot fallback path.
- A screenshot/pixel-only agent, if available.
- A paid stronger model path (`DESKTOP_MODEL`) for reliability comparison.

## Reporting Rules

- Run each task at least 10 times.
- Include hardware, Windows version, model ids, and date.
- Report failures, not just wins.
- Do not claim "best" without published methodology and raw results.
- **Verify the path, not just the answer.** A desktop task only counts as a
  desktop-control success if the agent actually interacted with the app UI
  (`verified_desktop_path: true` in the harness output). A correct answer
  computed via the shell (e.g. evaluating 47*89 in PowerShell instead of
  pressing Calculator buttons) is a task success but NOT a desktop-control
  win, and must be reported separately. The harness flags these runs with a
  `route.warning`.

## Automation

Use `scripts/benchmark_tasks.py` to:

1. Targets a running local Orynn server.
2. Submits each task through `/api/tasks`.
3. Consumes SSE until completion.
4. Records steps, duration, screenshots, tool failures, and final status.
5. Writes `benchmark-results/YYYY-MM-DD.json` and a Markdown summary.

Example:

```bash
python scripts/benchmark_tasks.py --base http://127.0.0.1:8080 --api-key "$AGENT_API_KEY"
```
