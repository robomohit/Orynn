import time
import uuid

import httpx
import pytest


API_URL = "http://localhost:8001/api/tasks"
BASE_URL = "http://localhost:8001"
API_KEY = "test-api-key-12345"


def _require_local_server() -> None:
    try:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Local server is not running at {BASE_URL}: {exc}")


def test_model_task_flow():
    _require_local_server()

    headers = {"Authorization": f"Bearer {API_KEY}"}
    task_id = f"model-smoke-{uuid.uuid4().hex[:12]}"
    payload = {
        "task_id": task_id,
        "goal": "Write a python script called hello.py that prints 'Hello from Nemotron' inside the workspace.",
        "model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "mode": "coding",
    }

    response = httpx.post(API_URL, json=payload, headers=headers, timeout=10.0)
    response.raise_for_status()
    assert response.json()["task_id"] == task_id

    for _ in range(60):
        time.sleep(2)

        try:
            log_response = httpx.get(f"{BASE_URL}/api/tasks/{task_id}/log", headers=headers, timeout=30.0)
        except httpx.HTTPError:
            continue
        if log_response.status_code != 200:
            continue

        try:
            status_response = httpx.get(f"{BASE_URL}/api/tasks", headers=headers, timeout=30.0)
        except httpx.HTTPError:
            continue
        status_response.raise_for_status()
        tasks = status_response.json().get("tasks", [])
        for task in tasks:
            if task["id"] == task_id and task["status"] in ("done", "failed"):
                return

    pytest.fail("Task timed out before reaching a terminal status.")
