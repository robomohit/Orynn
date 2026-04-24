import httpx
import time
import os

API_URL = "http://localhost:8000/api/tasks"
API_KEY = "test-api-key-12345"

def trigger_test():
    headers = {"Authorization": f"Bearer {API_KEY}"}
    payload = {
        "task_id": f"final-verify-{int(time.time())}",
        "goal": "Open Notepad, type 'ANTIGRAVITY SYSTEM VERIFIED', and save it to 'FINAL_REPORT.txt' in the project root.",
        "mode": "computer"
    }
    
    print(f"Triggering task: {payload['goal']}")
    try:
        resp = httpx.post(API_URL, json=payload, headers=headers, timeout=15.0)
        resp.raise_for_status()
        print(f"Task started. ID: {payload['task_id']}")
        return payload['task_id']
    except Exception as e:
        print(f"Error triggering task: {e}")
        return None

def poll_task(task_id):
    url = f"{API_URL}/{task_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    
    print("Polling for completion (max 240s)...")
    start_time = time.time()
    while time.time() - start_time < 240:
        try:
            resp = httpx.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            print(f"Status: {status}")
            if status in ["done", "failed", "error", "complete"]:
                return data
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(10)
    return None

if __name__ == "__main__":
    tid = trigger_test()
    if tid:
        result = poll_task(tid)
        print(f"Final Result: {result}")
