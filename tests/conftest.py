"""Shared pytest fixtures. CONTRACT FILE: owned by the orchestrator."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path

import pytest

from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths

_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _is_local_host(host: object) -> bool:
    if not isinstance(host, str):
        return True  # bytes / None / unix sockets: not an internet destination
    if host in {"localhost", ""} or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _block_external_network(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Unit tests may only talk to loopback / *.localhost. No test can reach a real employer or OpenAI.

    Opt out (never needed for the product's own tests) with ``@pytest.mark.allow_network``.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    def guarded_connect(self: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_local_host(host):
            raise RuntimeError(f"External network access is blocked in tests: {host!r}")
        return _real_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if not _is_local_host(host):
            raise RuntimeError(f"External DNS lookup is blocked in tests: {host!r}")
        return _real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "allow_network: opt out of the external-network guard")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def paths(data_dir: Path) -> AppPaths:
    app_paths = AppPaths(root=data_dir)
    app_paths.ensure()
    return app_paths


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
