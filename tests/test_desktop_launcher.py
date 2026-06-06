import pytest

import run_desktop


class _ThreadRecorder:
    def __init__(self):
        self.calls = []

    def thread(self, target, args, daemon):
        self.calls.append({"target": target, "args": args, "daemon": daemon})
        return self

    def start(self):
        self.calls[-1]["started"] = True


def test_start_backend_reuses_healthy_preferred_port(monkeypatch):
    threads = _ThreadRecorder()
    monkeypatch.setattr(run_desktop, "_server_healthy", lambda port: True)
    monkeypatch.setattr(run_desktop.threading, "Thread", threads.thread)

    assert run_desktop._start_backend(8000) == 8000
    assert threads.calls == []


def test_start_backend_starts_preferred_port_when_it_becomes_healthy(monkeypatch):
    threads = _ThreadRecorder()
    monkeypatch.setattr(run_desktop, "_server_healthy", lambda port: False)
    monkeypatch.setattr(run_desktop, "_wait_for_server", lambda port: port == 8000)
    monkeypatch.setattr(run_desktop.threading, "Thread", threads.thread)

    assert run_desktop._start_backend(8000) == 8000
    assert threads.calls == [
        {"target": run_desktop.run_server, "args": (8000,), "daemon": True, "started": True}
    ]


def test_start_backend_falls_back_to_free_port(monkeypatch):
    threads = _ThreadRecorder()
    waited_ports = []

    def fake_wait(port):
        waited_ports.append(port)
        return port == 54321

    monkeypatch.setattr(run_desktop, "_server_healthy", lambda port: False)
    monkeypatch.setattr(run_desktop, "_wait_for_server", fake_wait)
    monkeypatch.setattr(run_desktop, "_free_port", lambda: 54321)
    monkeypatch.setattr(run_desktop.threading, "Thread", threads.thread)

    assert run_desktop._start_backend(8000) == 54321
    assert waited_ports == [8000, 54321]
    assert threads.calls == [
        {"target": run_desktop.run_server, "args": (8000,), "daemon": True, "started": True},
        {"target": run_desktop.run_server, "args": (54321,), "daemon": True, "started": True},
    ]


def test_start_backend_exits_when_no_port_becomes_healthy(monkeypatch):
    threads = _ThreadRecorder()
    monkeypatch.setattr(run_desktop, "_server_healthy", lambda port: False)
    monkeypatch.setattr(run_desktop, "_wait_for_server", lambda port: False)
    monkeypatch.setattr(run_desktop, "_free_port", lambda: 54321)
    monkeypatch.setattr(run_desktop.threading, "Thread", threads.thread)

    with pytest.raises(SystemExit) as exc:
        run_desktop._start_backend(8000)

    assert exc.value.code == 1
    assert threads.calls == [
        {"target": run_desktop.run_server, "args": (8000,), "daemon": True, "started": True},
        {"target": run_desktop.run_server, "args": (54321,), "daemon": True, "started": True},
    ]
