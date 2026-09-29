"""Start the dashboard with uvicorn, bound to loopback only unless the caller explicitly opts out."""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable

import uvicorn

from autoapply.dashboard.app import create_app
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.security import is_loopback_bind_host

log = logging.getLogger("autoapply.dashboard")

__all__ = ["NonLoopbackHostError", "create_server", "run_server"]


class NonLoopbackHostError(ValueError):
    """Refused to bind a non-loopback address without ``allow_remote=True``."""


def create_server(
    runtime: DashboardRuntime,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_remote: bool = False,
    allowed_hosts: Iterable[str] = (),
    log_level: str = "info",
) -> uvicorn.Server:
    """A configured (not started) uvicorn server. Raises ``NonLoopbackHostError`` for a remote bind."""
    if not allow_remote and not is_loopback_bind_host(host):
        raise NonLoopbackHostError(
            f"Refusing to bind {host!r}: the dashboard has no login, so it only listens on loopback "
            "(127.0.0.1, localhost, ::1). Pass allow_remote=True only if you understand the risk."
        )
    if not 0 <= port <= 65535:
        raise ValueError(f"invalid port {port}")
    if allow_remote and not is_loopback_bind_host(host):
        log.warning(
            "dashboard bound to %s with allow_remote=True: it has NO login; anyone who can reach this "
            "port can control the applier",
            host,
        )
    app = create_app(runtime, allowed_hosts=allowed_hosts)
    config = uvicorn.Config(
        app, host=host, port=port, log_level=log_level, access_log=False, server_header=False
    )
    return uvicorn.Server(config)


def _url_for(host: str, port: int) -> str:
    shown = host.strip("[]")
    if shown in {"0.0.0.0", "::", ""}:
        shown = "127.0.0.1"
    return f"http://[{shown}]:{port}/" if ":" in shown else f"http://{shown}:{port}/"


def run_server(
    runtime: DashboardRuntime,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    *,
    allow_remote: bool = False,
    allowed_hosts: Iterable[str] = (),
    browser_opener: Callable[[str], object] = webbrowser.open,
) -> None:
    """Serve the dashboard until interrupted. Blocks. Opens the browser once the server is up if asked."""
    server = create_server(
        runtime, host, port, allow_remote=allow_remote, allowed_hosts=allowed_hosts
    )
    if open_browser:
        url = _url_for(host, port)

        def open_when_up() -> None:
            deadline = time.monotonic() + 30
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.1)
            if server.started:
                browser_opener(url)

        threading.Thread(target=open_when_up, name="dashboard-open-browser", daemon=True).start()
    server.run()
