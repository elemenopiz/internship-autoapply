"""run_server / create_server: loopback-only binding and a real uvicorn round trip."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from autoapply.dashboard import server as server_module
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.server import NonLoopbackHostError, create_server, run_server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.com", "", "10.0.0.1"])
def test_non_loopback_hosts_are_refused_before_binding(
    runtime: DashboardRuntime, host: str
) -> None:
    with pytest.raises(NonLoopbackHostError):
        create_server(runtime, host, free_port())
    with pytest.raises(NonLoopbackHostError):
        run_server(runtime, host, free_port())


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_loopback_hosts_are_accepted(runtime: DashboardRuntime, host: str) -> None:
    assert create_server(runtime, host, free_port()).config.host == host


def test_remote_bind_needs_the_explicit_flag(
    runtime: DashboardRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="autoapply.dashboard"):
        server = create_server(runtime, "0.0.0.0", free_port(), allow_remote=True)
    assert server.config.host == "0.0.0.0"
    assert "NO login" in caplog.text


def test_invalid_ports_are_rejected(runtime: DashboardRuntime) -> None:
    for port in (-1, 65536):
        with pytest.raises(ValueError):
            create_server(runtime, "127.0.0.1", port)


def test_run_server_opens_the_browser_once_started(
    runtime: DashboardRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    class FakeServer:
        started = False

        def run(self) -> None:
            time.sleep(0.3)
            self.started = True
            time.sleep(0.6)

    monkeypatch.setattr(server_module, "create_server", lambda *a, **k: FakeServer())
    run_server(runtime, "::1", 8123, open_browser=True, browser_opener=opened.append)
    assert opened == ["http://[::1]:8123/"]
    opened.clear()
    run_server(runtime, "127.0.0.1", 8124, open_browser=False, browser_opener=opened.append)
    assert opened == []


@pytest.fixture
def live_server(runtime: DashboardRuntime) -> Iterator[str]:
    port = free_port()
    server = create_server(runtime, "127.0.0.1", port, log_level="warning")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=15)


def test_real_server_round_trip_and_host_defence(live_server: str) -> None:
    with httpx.Client(base_url=live_server) as client:
        response = client.get("/healthz")
        assert response.status_code == 200 and "server" not in response.headers
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
        page = client.get("/")
        token = page.text.split('name="csrf-token" content="')[1].split('"')[0]
        assert client.post("/api/stop").status_code == 403
        assert client.post("/api/stop", headers={"X-CSRF-Token": token}).status_code == 200
        assert (
            client.post(
                "/api/unstop", headers={"X-CSRF-Token": token, "Origin": "http://evil.example"}
            ).status_code
            == 403
        )


def test_real_server_cuts_oversized_uploads(live_server: str) -> None:
    with httpx.Client(base_url=live_server, timeout=30) as client:
        token = client.get("/").text.split('name="csrf-token" content="')[1].split('"')[0]
        big = b"%PDF-" + b"0" * (12 * 1024 * 1024)
        files: Any = {"file": ("big.pdf", big, "application/pdf")}
        try:
            response = client.post(
                "/api/resume/upload", files=files, headers={"X-CSRF-Token": token}
            )
        except httpx.TransportError:
            return  # the server closed the connection while the client was still sending: also fine
        assert response.status_code == 413
