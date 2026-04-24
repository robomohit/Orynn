import time
import requests

def test_delegation():
    url = "http://localhost:8000/api/tasks"
    
    config_r = requests.get("http://localhost:8000/api/config")
    api_key = config_r.json().get("api_key")
    headers = {"Authorization": f"Bearer {api_key}"}
    
    import uuid
    payload = {
        "task_id": str(uuid.uuid4()),
        "goal": "magic_test_delegation_goal",
        "model": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "mode": "coding"
    }
    
    print("Submitting delegation task...")
    r = requests.post(url, json=payload, headers=headers)
    if r.status_code != 200:
        print("Failed to start task:", r.text)
        return
        
    task_id = r.json().get("task_id")
    print(f"Task started: {task_id}")
    
    print("Waiting for completion...")
    for _ in range(30):
        time.sleep(2)
        status_url = f"http://localhost:8000/api/tasks/{task_id}"
        r2 = requests.get(status_url, headers=headers)
        if r2.status_code == 200:
            data = r2.json()
            if data.get("status") in ["done", "failed", "cancelled"]:
                print(f"Task finished with status: {data.get('status')}")
                break
                
    print("Done. Check server logs to see if delegation occurred.")

if __name__ == "__main__":
    test_delegation()
