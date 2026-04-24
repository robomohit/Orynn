from pathlib import Path

p = Path('app/agent.py')
c = p.read_text(encoding='utf-8')

# Fix runs_in_background and flags
c = c.replace(
    '        is_computer_use = mode == "computer_use"',
    '        is_computer_use = mode == "computer_use"\n        is_isolated = mode == "computer_isolated"'
)
c = c.replace(
    '        runs_in_background = is_coding or is_computer_use',
    '        runs_in_background = is_coding or is_computer_use or is_isolated'
)

# Fix isolated check
c = c.replace(
    'if mode == "computer" and isolated_app:',
    'if mode in ("computer", "computer_isolated") and isolated_app:'
)

# Fix SubTaskWorker init call
c = c.replace(
    'worker = SubTaskWorker(worker_id, task_id, st, self, mode, (screen_width, screen_height), complexity=complexity)',
    'worker = SubTaskWorker(worker_id, task_id, st, self, mode, (screen_width, screen_height), complexity=complexity)'
)

p.write_text(c, encoding='utf-8', newline='\r\n')
print("Successfully patched agent.py logic for real background control")
