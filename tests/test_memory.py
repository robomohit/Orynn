import pytest
from unittest.mock import patch, MagicMock
from app.memory import MemoryStore, _FallbackCollection


def test_memory_store(workspace):
    m = MemoryStore(workspace / "db.sqlite")
    assert m.search("anything") == []
    m.add("note", "DNS resolution failed on page load")
    m.add("note", "User logged in")
    m.add("note", "Payment button missing")
    m.add("note", "Network timeout on API")
    m.add("note", "UI rendered")
    res = m.search("network error", limit=3)
    assert any("DNS" in r.content or "Network" in r.content for r in res)
    recent = m.recent(3)
    assert len(recent) == 3
    rid = m.add_action_result("t", "a", "ok")
    found = [x for x in m.recent(10) if x.id == rid][0]
    assert found.kind == "action_result"


def test_maybe_auto_consolidate_fires_at_threshold(workspace):
    """consolidation triggers only when _summaries_since_consolidate hits AUTO_CONSOLIDATE_EVERY"""
    m = MemoryStore(workspace / "db.sqlite")
    # below threshold → no-op
    m._summaries_since_consolidate = m.AUTO_CONSOLIDATE_EVERY - 1
    assert m.maybe_auto_consolidate() is None
    assert m._summaries_since_consolidate == m.AUTO_CONSOLIDATE_EVERY - 1
    # at threshold → consolidation runs and returns a result dict
    m._summaries_since_consolidate = m.AUTO_CONSOLIDATE_EVERY
    result = m.maybe_auto_consolidate()
    assert result is not None
    assert "merged" in result


def test_recall_sessions_logs_warning_when_metadata_update_fails(workspace, caplog, monkeypatch):
    m = MemoryStore(workspace / "db.sqlite")
    m.add("session_summary", "Network debugging summary", {"task_id": "t1", "recall_count": 0})

    def boom(**kwargs):
        raise RuntimeError("db is read-only")

    monkeypatch.setattr(m.collection, "update", boom, raising=False)
    with caplog.at_level("WARNING"):
        items = m.recall_sessions("network debugging", n=1)

    assert items
    assert "Failed to update memory recall_count metadata" in caplog.text


def test_recall_sessions_parity_between_primary_and_fallback_collection(workspace):
    def seed(store):
        store.add("session_summary", "Network timeout while loading dashboard", {"task_id": "t1", "recall_count": 0})
        store.add("session_summary", "Login page CSS polish and typography cleanup", {"task_id": "t2", "recall_count": 0})
        store.add("session_summary", "Network retry logic for websocket reconnect", {"task_id": "t3", "recall_count": 0})
        return [item.content for item in store.recall_sessions("network reconnect timeout", n=2)]

    primary = MemoryStore(workspace / "primary.sqlite")
    fallback = MemoryStore(workspace / "fallback.sqlite")
    fallback.collection = _FallbackCollection()

    primary_hits = seed(primary)
    fallback_hits = seed(fallback)

    assert primary_hits
    assert fallback_hits
    assert primary_hits[0] == fallback_hits[0]
    assert set(primary_hits[:2]) == set(fallback_hits[:2])
