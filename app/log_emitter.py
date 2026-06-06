from __future__ import annotations
import asyncio
import concurrent.futures
import logging
from datetime import datetime, timezone
from typing import Dict, List

import json
import os
from pathlib import Path
import re

_log = logging.getLogger("log_emitter")

MAX_LOG_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_FIELD_CHARS = 4_000
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LogEmitter:
    """Simple pub/sub bus for SSE task log streaming."""
    def __init__(self):
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        self._seqs: Dict[str, int] = {}
        self._disk_logging_disabled: set[str] = set()
        # Maps task_id -> list of byte offsets, one per event written to disk.
        # Used by read_log() to seek directly to a given event instead of scanning.
        self._offsets: Dict[str, List[int]] = {}
        # Honour the same workspace override as main.py so test runs keep their
        # task logs in an isolated tmp dir instead of leaking into ./workspace/logs.
        self.log_dir = Path(os.environ.get("AI_COMPUTER_WORKSPACE", ".")) / "workspace" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Single-worker pool for disk writes so emit() never blocks the
        # asyncio event loop on slow/AV-scanned filesystems. One worker is
        # enough because the loads are tiny and we want strict ordering.
        self._writer = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="log-writer"
        )

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue):
        if task_id in self._queues:
            try:
                self._queues[task_id].remove(q)
            except ValueError:
                pass
            if not self._queues[task_id]:
                self._queues.pop(task_id, None)

    def log_path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id or ""):
            raise ValueError("Invalid task id")
        return self.log_dir / f"{task_id}.jsonl"

    def read_log(self, task_id: str, since: int = 0) -> list[dict]:
        log_file = self.log_path(task_id)
        if not log_file.exists():
            return []

        events: list[dict] = []
        offsets = self._offsets.get(task_id)

        # Fast path: byte-offset index exists and since is within it — seek directly.
        if since > 0 and offsets and since < len(offsets):
            with open(log_file, "rb") as f:
                f.seek(offsets[since])
                for index, raw_line in enumerate(f, start=since):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        if isinstance(msg, dict):
                            msg.setdefault("task_id", task_id)
                            msg.setdefault("seq", index)
                        events.append(msg)
                    except json.JSONDecodeError:
                        continue
            return events

        # Fallback: linear scan (handles since==0 or missing/old index).
        with open(log_file, "r", encoding="utf-8") as f:
            for index, line in enumerate(f):
                if index < since:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if isinstance(msg, dict):
                        msg.setdefault("task_id", task_id)
                        msg.setdefault("seq", index)
                    events.append(msg)
                except json.JSONDecodeError:
                    continue
        return events

    def count_events(self, task_id: str) -> int:
        # Use the in-memory sequence counter when available — avoids a full file scan.
        if task_id in self._seqs:
            return self._seqs[task_id]
        # Fallback for tasks whose state was never loaded into memory (e.g. old log files).
        log_file = self.log_path(task_id)
        if not log_file.exists():
            return 0
        with open(log_file, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    def task_ids(self) -> list[str]:
        return sorted(path.stem for path in self.log_dir.glob("*.jsonl"))

    def _truncate_text(self, value: str) -> str:
        if len(value) <= MAX_TEXT_FIELD_CHARS:
            return value
        return value[:MAX_TEXT_FIELD_CHARS] + "\n...(truncated for disk log)"

    def _sanitize_for_disk(self, event_type: str, payload: dict) -> dict:
        """Keep live SSE payloads rich, but make persistent logs bounded."""
        sanitized = dict(payload)

        if event_type == "screenshot" and isinstance(sanitized.get("data"), str):
            raw = sanitized["data"]
            sanitized["data"] = "[omitted from persistent log]"
            sanitized["data_omitted"] = True
            sanitized["data_chars"] = len(raw)

        for field in ("detail", "output", "content", "reason", "message"):
            if isinstance(sanitized.get(field), str):
                sanitized[field] = self._truncate_text(sanitized[field])

        if event_type == "file_change" and isinstance(sanitized.get("content"), str):
            sanitized["content"] = self._truncate_text(sanitized["content"])

        return sanitized

    def emit(self, task_id: str, event_type: str, payload: dict):
        seq = self._seqs.get(task_id)
        if seq is None:
            seq = self.count_events(task_id)
        msg = {
            "type": event_type,
            "task_id": task_id,
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self._seqs[task_id] = seq + 1

        # Push to live SSE subscribers FIRST (instant, in-memory) so the UI
        # sees the event without waiting for any disk work.
        for q in list(self._queues.get(task_id, [])):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                _log.warning("SSE subscriber queue full for task %s — event dropped", task_id)

        # Persistent logging — offloaded to a single background writer thread
        # so emit() returns immediately. Strict FIFO order is preserved by the
        # max_workers=1 executor.
        if task_id not in self._disk_logging_disabled:
            disk_msg = self._sanitize_for_disk(event_type, msg)
            try:
                self._writer.submit(self._write_to_disk, task_id, disk_msg)
            except RuntimeError:
                # Executor already shut down (e.g. during process exit).
                pass

    def _write_to_disk(self, task_id: str, disk_msg: dict) -> None:
        """Append a single event to the persistent log file. Runs on the
        background writer thread so it never blocks the asyncio loop."""
        try:
            log_file = self.log_path(task_id)
        except ValueError:
            return
        if task_id in self._disk_logging_disabled:
            return
        try:
            if log_file.exists() and log_file.stat().st_size >= MAX_LOG_FILE_BYTES:
                self._disk_logging_disabled.add(task_id)
                truncation_notice = {
                    "type": "status",
                    "task_id": task_id,
                    "seq": disk_msg.get("seq"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "message": "Persistent log limit reached; further events omitted to protect disk space.",
                    "persistent_log_truncated": True,
                }
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(truncation_notice) + "\n")
                return
            with open(log_file, "ab") as f:
                byte_offset = f.tell()
                self._offsets.setdefault(task_id, []).append(byte_offset)
                f.write((json.dumps(disk_msg) + "\n").encode("utf-8"))
        except Exception as exc:
            _log.warning("Disk log write failed for task %s: %s", task_id, exc)

    def flush(self) -> None:
        """Block until all pending background disk writes have completed.

        Submits a no-op sentinel to the single-worker executor and waits for
        its result, which guarantees every previously submitted write has
        finished (FIFO ordering with max_workers=1).
        """
        try:
            self._writer.submit(lambda: None).result()
        except RuntimeError:
            pass  # Executor already shut down; nothing to flush.

    def cleanup_task(self, task_id: str) -> None:
        """Release in-memory state for a completed/failed task.

        Called after a task reaches a terminal state so per-task state does not
        accumulate indefinitely across many runs.
        """
        self._seqs.pop(task_id, None)
        self._disk_logging_disabled.discard(task_id)
        self._offsets.pop(task_id, None)
        # Drain and discard all subscriber queues so they don't hold references
        # to stale event data or prevent garbage collection.
        queues = self._queues.pop(task_id, [])
        for q in queues:
            # Drain any buffered items so the queue is empty before discarding.
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break


log_emitter = LogEmitter()
