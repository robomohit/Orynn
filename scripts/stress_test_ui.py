import asyncio
import time
import random
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:8000/v2"
API_KEY = "test-api-key-12345"

TASKS = [
    "write hello world to stress_1.txt",
    "mkdir stress_dir_test",
    "Calculate the square root of 123456789 and save to math.txt",
    "List all files in the current directory and find the largest one.",
]

async def run_individual_stress_task(browser, index):
    context = await browser.new_context()
    page = await context.new_page()
    
    print(f"[Task {index}] Navigating to {BASE_URL}")
    await page.goto(BASE_URL)
    
    # Wait for the UI to load
    await page.wait_for_selector("#input")
    
    goal = random.choice(TASKS)
    print(f"[Task {index}] Starting goal: {goal}")
    
    await page.fill("#input", goal)
    await page.click("#send")
    
    # Wait for some progress
    await asyncio.sleep(10)
    
    # Take a snapshot of the state
    screenshot_path = f"stress_test_task_{index}.png"
    await page.screenshot(path=screenshot_path)
    print(f"[Task {index}] Saved screenshot to {screenshot_path}")
    
    # Check if we see the planning or execution
    content = await page.content()
    if "Planning" in content or "Executing" in content:
        print(f"[Task {index}] UI confirmed task started and rendering.")
    else:
        print(f"[Task {index}] WARNING: UI might not be showing task progress.")
    
    await asyncio.sleep(20) # Let it run for a bit
    await context.close()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        print("Launching 5 concurrent UI tasks...")
        tasks = [run_individual_stress_task(browser, i) for i in range(5)]
        await asyncio.gather(*tasks)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
