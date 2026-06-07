"""The Effort ladder: a free-model speed/capability dial (Low→Max). OpenRouter
free models can't tune reasoning, so effort just picks a bigger model. These
tests pin the mapping + that the slider value round-trips through preferences."""
from app.providers import (
    EFFORT_ORDER,
    EFFORT_LABELS,
    effort_model,
    normalize_effort,
)


def test_effort_order_and_labels():
    assert EFFORT_ORDER == ["low", "medium", "high", "max"]
    assert set(EFFORT_LABELS) == set(EFFORT_ORDER)


def test_normalize_effort_falls_back_to_medium():
    assert normalize_effort("low") == "low"
    assert normalize_effort("MAX") == "max"
    assert normalize_effort("  High ") == "high"
    assert normalize_effort("banana") == "medium"
    assert normalize_effort(None) == "medium"
    assert normalize_effort("") == "medium"


def test_effort_is_monotonic_distinct_per_mode():
    # Every level resolves to a non-empty model id, for chat + desktop.
    for mode in ("auto", "computer"):
        models = [effort_model(e, mode) for e in EFFORT_ORDER]
        assert all(models), f"empty model in {mode}: {models}"
        # low and max must differ — the dial has to actually do something.
        assert models[0] != models[-1], f"{mode}: low == max ({models[0]})"


def test_low_is_a_fast_small_model_and_max_is_heavy():
    assert "20b" in effort_model("low")            # snappy small model
    assert "120b" in effort_model("max").lower()   # heaviest free


def test_desktop_keeps_tool_accurate_models():
    # Low stays snappy; Medium desktop uses the reliable-first UIA chain.
    assert effort_model("low", "computer") == "openai/gpt-oss-20b:free"
    assert effort_model("medium", "computer_isolated") == "tier:uia"


def test_preferences_round_trip(tmp_path, monkeypatch):
    import app.preferences as P
    monkeypatch.setattr(P, "store_path", lambda: tmp_path / "preferences.json")
    assert P.get_all()["effort"] == "medium"          # default
    P.update({"effort": "max"})
    assert P.get_all()["effort"] == "max"
    P.update({"effort": "nonsense"})                  # coerced back to default
    assert P.get_all()["effort"] == "medium"
