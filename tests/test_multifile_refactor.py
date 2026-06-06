"""
Multi-file refactoring test for agent capability evaluation.
This test requires the agent to coordinate changes across multiple files.
"""

import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from app.agent import AgentService
import os

@pytest.fixture
def challenge_workspace():
    """Create a temporary workspace with multi-file refactoring challenge."""
    tmpdir = tempfile.mkdtemp()

    # Create the challenge files
    challenge_path = Path(tmpdir) / "refactor_challenge"
    challenge_path.mkdir(exist_ok=True)

    # File 1: Old logger utility (BROKEN - inconsistent API)
    logger_py = challenge_path / "logger.py"
    logger_py.write_text('''"""Legacy logger module - needs refactoring."""

class Logger:
    def log(self, msg):
        """Old API - single method."""
        print(f"[LOG] {msg}")

    def error(self, msg):
        """Inconsistent - sometimes uses this."""
        print(f"[ERROR] {msg}")
''')

    # File 2: Service A - uses logger inconsistently
    service_a_py = challenge_path / "service_a.py"
    service_a_py.write_text('''"""Service A - uses logger module."""
from logger import Logger

logger_instance = Logger()

def process_data(data):
    """Process data with logging."""
    logger_instance.log("Processing data")
    if not data:
        logger_instance.error("Empty data received")
        return None
    logger_instance.log(f"Processed: {len(data)} items")
    return len(data)

def validate(x):
    logger_instance.log("Validating")
    if x < 0:
        logger_instance.error("Invalid value")
    return x > 0
''')

    # File 3: Service B - uses logger inconsistently
    service_b_py = challenge_path / "service_b.py"
    service_b_py.write_text('''"""Service B - uses logger module."""
from logger import Logger

logger_instance = Logger()

def fetch_resource(resource_id):
    """Fetch a resource with logging."""
    logger_instance.log(f"Fetching resource {resource_id}")
    if resource_id is None:
        logger_instance.error("Resource ID is None")
        return None
    logger_instance.log(f"Retrieved resource: {resource_id}")
    return {"id": resource_id, "data": "value"}

def cache_result(key, value):
    logger_instance.log(f"Caching {key}")
    if not value:
        logger_instance.error("Cannot cache empty value")
    return True
''')

    # File 4: Service C - uses logger differently
    service_c_py = challenge_path / "service_c.py"
    service_c_py.write_text('''"""Service C - uses logger module."""
from logger import Logger

class ServiceC:
    def __init__(self):
        self.logger = Logger()

    def execute(self, command):
        """Execute a command with logging."""
        self.logger.log(f"Executing: {command}")
        if not command:
            self.logger.error("Empty command")
            return False
        self.logger.log("Command executed successfully")
        return True

    def cleanup(self):
        self.logger.log("Cleaning up resources")
''')

    # File 5: Test file - MUST PASS
    test_file = challenge_path / "test_services.py"
    test_file.write_text('''"""Tests for services - MUST PASS."""
from service_a import process_data, validate
from service_b import fetch_resource, cache_result
from service_c import ServiceC

def test_process_data():
    assert process_data([1, 2, 3]) == 3
    assert process_data(None) is None
    assert process_data([]) == 0

def test_validate():
    assert validate(5) is True
    assert validate(-1) is False
    assert validate(0) is False

def test_fetch_resource():
    result = fetch_resource("res-123")
    assert result is not None
    assert result["id"] == "res-123"
    assert fetch_resource(None) is None

def test_cache_result():
    assert cache_result("key1", "value1") is True

def test_service_c():
    svc = ServiceC()
    assert svc.execute("cmd1") is True
    assert svc.execute("") is False
    svc.cleanup()
''')

    # Task description file
    task_desc = challenge_path / "CHALLENGE.md"
    task_desc.write_text('''# Multi-File Refactoring Challenge

## Objective
Refactor the logger module and all dependent services to implement a unified, modern logging API.

## Current Problem
- logger.py has an inconsistent API (log() for some, error() for others)
- Services use it differently
- Need a clean, unified approach

## Requirements
1. Refactor logger.py to have unified API with methods:
   - info(msg) - for general logging
   - error(msg) - for error logging
   - debug(msg) - for debug logging
   - warning(msg) - for warning logging

2. Update all services (service_a.py, service_b.py, service_c.py) to:
   - Use the new unified logger API
   - Replace log() calls with info()
   - Ensure all error logging uses error()
   - Add appropriate debug/warning logs

3. Ensure tests pass - test_services.py must pass without modification

4. Fix singleton issue - use module-level logger instance
''')

    yield challenge_path
    shutil.rmtree(tmpdir)


@pytest.mark.asyncio
async def test_multifile_refactoring_challenge(challenge_workspace):
    """
    Test that agent can handle multi-file refactoring.
    Requires: understanding problem across files, creating plan,
    coordinating changes across 4 files, ensuring tests pass.
    """

    engine = AgentService(workspace=challenge_workspace.parent, log_emitter=MagicMock())

    task_desc = f"""
    You are given a Python project in: {challenge_workspace}
    Read CHALLENGE.md and refactor the logger module and services.
    Update service_a.py, service_b.py, and service_c.py to use new API.
    Run tests to ensure everything works.
    Report which files you modified.
    """

    events = []

    async def capture_event(task_id, event_type, data):
        events.append({"type": event_type, "data": data})

    engine._emit = capture_event
    engine._emit_reasoning = AsyncMock()

    mock_provider = MagicMock()

    async def mock_stream(*args, **kwargs):
        yield '<action type="finish">{"reason":"complete"}</action>'

    mock_provider.stream_chat = mock_stream

    try:
        await asyncio.wait_for(
            engine.run_task("multifile_challenge", task_desc, mock_provider),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        pass

    assert len(events) > 0
