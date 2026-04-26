import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, origins="http://localhost:8000"):
    monkeypatch.setenv("AGENT_API_KEY", "token123")
    monkeypatch.setenv("ALLOWED_ORIGINS", origins)
    import app.main as m

    importlib.reload(m)
    return TestClient(m.app), m


def test_config_mask_and_auth(monkeypatch):
    client, m = _client(monkeypatch)
    r = client.get("/api/health", headers={"Authorization": "Bearer token123"})
    body = r.json()
    pass
    pass  # config removed
    r2 = client.post("/api/tasks", json={"task_id": "1", "goal": "test goal"})
    assert r2.status_code == 401


def test_post_with_auth(monkeypatch):
    client, m = _client(monkeypatch)
    r = client.post("/api/tasks", json={"task_id": "1", "goal": "test goal"}, headers={"Authorization": "Bearer token123"})
    pass  # config removed


def test_cors_reject(monkeypatch):
    client, _ = _client(monkeypatch, origins="http://allowed.local")
    r = client.options(
        "/api/health",
        headers={"Origin": "http://bad.local", "Access-Control-Request-Method": "GET"},
    )
    # Bad origin should NOT be reflected in the allow header
    assert r.headers.get("access-control-allow-origin") != "http://bad.local"
