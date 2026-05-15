import pytest
from unittest.mock import patch, MagicMock
from app.memory import MemoryStore


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
