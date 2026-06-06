.PHONY: setup run desktop test lint clean

setup:
	pip install -r requirements.txt
	playwright install chromium
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env — add your API key."; fi

run:
	python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

desktop:
	pip install -r requirements-desktop.txt
	python run_desktop.py

test:
	python -m pytest -q --tb=short

lint:
	python -m py_compile app/agent.py app/main.py app/providers.py app/tools.py app/log_emitter.py
	@echo "Syntax OK"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
