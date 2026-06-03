"""Connector registry invariants: every connector ships a skill manual (the
"how-to" the agent reads), nothing is orphaned, icons all resolve, and the
progressive-disclosure matcher hands back the right manual for a goal."""
from pathlib import Path

import app.connectors as C

_ROOT = Path(__file__).resolve().parents[1]


def test_every_connector_has_a_skill_manual():
    ids = [c["id"] for c in C.CONNECTORS]
    missing = [i for i in ids if i not in C.CONNECTOR_SKILLS]
    assert missing == [], f"connectors with no skill manual: {missing}"


def test_no_orphan_skills_and_no_dupes():
    ids = [c["id"] for c in C.CONNECTORS]
    orphans = [k for k in C.CONNECTOR_SKILLS if k not in ids]
    assert orphans == [], f"skills with no connector: {orphans}"
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert dupes == [], f"duplicate connector ids: {dupes}"


def test_every_skill_has_keywords_and_text():
    for cid, meta in C.CONNECTOR_SKILLS.items():
        assert meta.get("skill"), f"{cid}: empty skill manual"
        assert meta.get("keywords"), f"{cid}: no keywords (won't ever match a goal)"


# NOTE: The connector picker UI (dashboard section + onboarding step) was removed
# pending a real account-linking flow — web tasks just drive the browser for now.
# The backend registry below stays (skills/manuals still feed the agent), so these
# registry-invariant tests remain. The dashboard-icon test was dropped with the UI.


def test_relevant_briefs_matches_only_relevant_linked_connectors(monkeypatch):
    # Pretend Excel + Maps are the linked connectors.
    monkeypatch.setattr(
        C, "linked_only",
        lambda: [c for c in C.list_with_state() if c["id"] in ("excel", "maps")])
    labels = {lbl for lbl, _ in C.relevant_briefs("add a SUM formula in excel")}
    assert labels == {"Excel"}
    labels2 = {lbl for lbl, _ in C.relevant_briefs("driving directions home")}
    assert labels2 == {"Google Maps"}
    # An unrelated goal pulls in nothing.
    assert C.relevant_briefs("what's the weather") == []
