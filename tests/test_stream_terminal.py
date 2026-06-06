"""Regression: the task SSE stream must always deliver the terminal `done`
event. A subscribe-after-replay race used to drop it for fast-finishing tasks,
leaving the capsule stuck "running" forever even though the task had completed
(the answer sat in /log, never shown). The endpoint now subscribes before
replaying and dedups by seq."""
import json
import time
import uuid

from fastapi.testclient import TestClient

import app.main as _m

def _wait_flush(tid: str, n: int, timeout: float = 4.0) -> None:
    """emit() persists on a background thread; wait until the log has n rows."""
    end = time.time() + timeout
    while time.time() < end:
        if len(_m.log_emitter.read_log(tid, since=0)) >= n:
            return
        time.sleep(0.02)


def _stream_types(tid: str, since: int = 0):
    client = TestClient(_m.app)
    auth = {"Authorization": f"Bearer {_m.API_KEY}"}
    types = []
    with client.stream(
        "GET",
        f"/api/tasks/{tid}/stream?since={since}&keepalive_timeout_seconds=5",
        headers=auth,
    ) as r:
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            types.append((ev.get("seq"), ev.get("type")))
            if ev.get("type") in ("done", "error", "cancelled"):
                break
    return types


def _fresh_task_id(prefix: str) -> str:
    tid = f"{prefix}-{uuid.uuid4().hex}"
    _m.log_emitter._seqs.pop(tid, None)
    _m.log_emitter._offsets.pop(tid, None)
    _m.log_emitter._disk_logging_disabled.discard(tid)
    _m.log_emitter._queues.pop(tid, None)
    return tid


def test_stream_delivers_terminal_done_from_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(_m.log_emitter, "log_dir", tmp_path)
    tid = _fresh_task_id("cap-strmdone")
    le = _m.log_emitter
    le.emit(tid, "status", {"message": "starting"})
    le.emit(tid, "action_start", {"action_id": "a1", "action_type": "open_app"})
    le.emit(tid, "action_result", {"action_id": "a1", "ok": True, "output": "opened"})
    le.emit(tid, "done", {"complete": True, "reason": "The result is 423"})
    _wait_flush(tid, 4)

    seen = _stream_types(tid)
    kinds = [t for _, t in seen]
    assert "done" in kinds, f"terminal event missing: {kinds}"
    assert kinds.count("done") == 1, f"done delivered more than once: {kinds}"
    # The terminal event must be last.
    assert kinds[-1] == "done", kinds


def test_stream_does_not_duplicate_or_drop_seqs(tmp_path, monkeypatch):
    monkeypatch.setattr(_m.log_emitter, "log_dir", tmp_path)
    tid = _fresh_task_id("cap-strmdedup")
    le = _m.log_emitter
    for i in range(5):
        le.emit(tid, "status", {"message": f"step {i}"})
    le.emit(tid, "done", {"complete": True, "reason": "done"})
    _wait_flush(tid, 6)

    seen = _stream_types(tid)
    seqs = [s for s, _ in seen if s is not None]
    # No seq delivered twice (the subscribe-first + dedup must not double-send).
    assert len(seqs) == len(set(seqs)), f"duplicate seqs streamed: {seqs}"
    # Contiguous from 0 — nothing dropped.
    assert seqs == sorted(seqs), seqs
