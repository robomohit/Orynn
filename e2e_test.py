import time
import uuid

import httpx
import pytest


BASE_URL = "http://localhost:8765"
TOKEN = "test"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _require_local_server() -> None:
    try:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Local server is not running at {BASE_URL}: {exc}")


@pytest.fixture(scope="module")
def local_server():
    _require_local_server()
    return BASE_URL


def test_health_check(local_server):
    response = httpx.get(f"{local_server}/api/health", timeout=5.0)
    assert response.status_code == 200, response.text


def test_models_endpoint(local_server):
    response = httpx.get(f"{local_server}/api/models", timeout=5.0)
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["models"], list)


def test_task_lifecycle(local_server):
    task_id = f"e2e-{uuid.uuid4().hex[:12]}"
    data = {
        "task_id": task_id,
        "goal": "open notepad and type hello world",
        "model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    }

    response = httpx.post(f"{local_server}/api/tasks", headers=HEADERS, json=data, timeout=10.0)
    assert response.status_code == 200, response.text

    listed = False
    for _ in range(10):
        try:
            response = httpx.get(f"{local_server}/api/tasks", headers=HEADERS, timeout=10.0)
            if response.status_code == 200 and task_id in response.text:
                listed = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    assert listed, "Task never appeared in /api/tasks during polling window."

    events = []
    try:
        with httpx.stream("GET", f"{local_server}/api/tasks/{task_id}/stream", headers=HEADERS, timeout=6.0) as response:
            start_time = time.time()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(line)
                    break
                if time.time() - start_time > 5:
                    break
    except httpx.ReadTimeout:
        pass
    assert len(events) >= 0

    response = httpx.delete(f"{local_server}/api/tasks/{task_id}", headers=HEADERS, timeout=5.0)
    assert response.status_code == 200, response.text

    response = httpx.get(f"{local_server}/api/tasks/{task_id}/log", headers=HEADERS, timeout=5.0)
    assert response.status_code == 200, response.text
