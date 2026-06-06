from app.state_store import read_json, workspace_state_path, write_json


def test_state_store_writes_and_reads_json_atomically(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_COMPUTER_WORKSPACE", str(tmp_path))
    path = workspace_state_path("sample.json")

    write_json(path, {"items": [1, 2, 3]})

    assert read_json(path, {}) == {"items": [1, 2, 3]}
    assert not list(tmp_path.glob(".sample.json.*.tmp"))


def test_state_store_returns_default_for_corrupt_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    assert read_json(path, {"ok": False}) == {"ok": False}
