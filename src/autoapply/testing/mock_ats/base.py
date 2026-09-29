"""Shared infrastructure for the hermetic mock ATS / employer sites. CONTRACT FILE: owned by the orchestrator.

A ``MockSite`` is a FastAPI app served by uvicorn on a loopback port in a background thread of the test
process. The browser reaches it through a production-like hostname ``<host>.localhost:<port>`` (Chromium
resolves ``*.localhost`` to 127.0.0.1), e.g. ``acme.wd5.myworkdayjobs.com.localhost:51234``, so URL based ATS
detection is exercised with realistic hosts. Python clients should use ``site.direct_url()`` instead.

SAFETY: mock sites exist so that no test can ever submit a real application. Anything that drives a browser at
them must use the loopback-restricted browser (``apply.browser``) so a mis-configured test cannot leave the machine.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import UploadFile


@dataclass
class UploadedFile:
    field: str
    filename: str
    content_type: str
    data: bytes


@dataclass
class Submission:
    """A FINAL submission received by a mock site (intermediate wizard saves are not recorded here)."""

    site: str
    path: str
    fields: dict[str, list[str]]
    files: list[UploadedFile] = field(default_factory=list)
    received_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def first(self, name: str, default: str | None = None) -> str | None:
        values = self.fields.get(name)
        return values[0] if values else default

    def file(self, field_name: str) -> UploadedFile | None:
        return next((f for f in self.files if f.field == field_name), None)


@dataclass
class MailMessage:
    to: str
    subject: str
    body: str
    sender: str = "no-reply@mock.test"
    received_at: float = field(default_factory=time.time)


class Mailbox:
    """Thread-safe in-memory mailbox the mock sites deliver verification mails into."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.messages: list[MailMessage] = []

    def deliver(self, to: str, subject: str, body: str, sender: str = "no-reply@mock.test") -> None:
        with self._lock:
            self.messages.append(MailMessage(to=to, subject=subject, body=body, sender=sender))

    def snapshot(self) -> list[MailMessage]:
        with self._lock:
            return list(self.messages)


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_CODE_RE = re.compile(r"\b(\d{6})\b")


class MailboxEmailVerifier:
    """``contracts.EmailVerifier`` backed by a ``Mailbox`` (the test double for the IMAP verifier)."""

    def __init__(self, mailbox: Mailbox, poll_interval_s: float = 0.1) -> None:
        self.mailbox = mailbox
        self.poll_interval_s = poll_interval_s

    def _match(
        self, to_address: str, subject_contains: str | None, sender_contains: str | None
    ) -> Iterator[MailMessage]:
        for msg in reversed(self.mailbox.snapshot()):
            if msg.to.lower() != to_address.lower():
                continue
            if subject_contains and subject_contains.lower() not in msg.subject.lower():
                continue
            if sender_contains and sender_contains.lower() not in msg.sender.lower():
                continue
            yield msg

    def wait_for_link(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            for msg in self._match(to_address, subject_contains, sender_contains):
                if found := _URL_RE.search(msg.body):
                    return found.group(0).rstrip(".,)")
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval_s)

    def wait_for_code(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            for msg in self._match(to_address, subject_contains, sender_contains):
                if found := _CODE_RE.search(msg.body):
                    return found.group(1)
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval_s)


@dataclass
class FaultPlan:
    """Chaos knobs, matched by path prefix. Lets tests prove adapters cope with slow / flaky sites."""

    delay_s: dict[str, float] = field(default_factory=dict)  # path prefix -> artificial latency
    fail_once: set[str] = field(default_factory=set)  # path prefix -> first request gets HTTP 503
    _failed: set[str] = field(default_factory=set)

    def should_fail(self, path: str) -> bool:
        for prefix in self.fail_once:
            if path.startswith(prefix) and prefix not in self._failed:
                self._failed.add(prefix)
                return True
        return False

    def delay_for(self, path: str) -> float:
        return max((d for p, d in self.delay_s.items() if path.startswith(p)), default=0.0)


class MockSite:
    """One mock ATS / employer portal. Subclass or configure ``self.app`` (FastAPI) with routes."""

    def __init__(self, name: str, host: str) -> None:
        self.name = name
        self.host = host  # production-like host, WITHOUT the .localhost suffix
        self.app = FastAPI(title=f"mock-{name}", docs_url=None, redoc_url=None, openapi_url=None)
        self.submissions: list[Submission] = []
        self.page_views: list[str] = []  # request paths of every GET, for assertions
        self.state: dict[str, Any] = {}  # free-form per-site state (accounts, sessions, ...)
        self.faults = FaultPlan()
        self.hub: MockHub | None = None
        self.port: int | None = None
        self._install_middleware()

    # -- addressing --------------------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        """Browser-facing URL using the production-like hostname."""
        return f"http://{self.host}.localhost:{self._require_port()}"

    def url(self, path: str = "/") -> str:
        return self.base_url + path

    def direct_url(self, path: str = "/") -> str:
        """Loopback URL for Python HTTP clients (no DNS involved)."""
        return f"http://127.0.0.1:{self._require_port()}{path}"

    def _require_port(self) -> int:
        if self.port is None:
            raise RuntimeError(f"mock site {self.name!r} is not started; use MockHub.start()")
        return self.port

    # -- helpers for subclasses -----------------------------------------------------------------------
    @property
    def mailbox(self) -> Mailbox:
        if self.hub is None:
            raise RuntimeError("site is not attached to a MockHub")
        return self.hub.mailbox

    async def read_form(self, request: Request) -> tuple[dict[str, list[str]], list[UploadedFile]]:
        """Parse urlencoded or multipart bodies into ({name: [values]}, [uploaded files])."""
        form = await request.form()
        fields: dict[str, list[str]] = {}
        files: list[UploadedFile] = []
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                data = await value.read()
                if value.filename:
                    files.append(
                        UploadedFile(
                            field=key,
                            filename=value.filename,
                            content_type=value.content_type or "application/octet-stream",
                            data=data,
                        )
                    )
            else:
                fields.setdefault(key, []).append(str(value))
        return fields, files

    def record_submission(
        self,
        path: str,
        fields: dict[str, list[str]],
        files: list[UploadedFile] | None = None,
        **meta: Any,
    ) -> Submission:
        submission = Submission(
            site=self.name, path=path, fields=fields, files=files or [], meta=meta
        )
        self.submissions.append(submission)
        return submission

    def _install_middleware(self) -> None:
        @self.app.middleware("http")
        async def _faults(request: Request, call_next):  # type: ignore[no-untyped-def]
            path = request.url.path
            if request.method == "GET":
                self.page_views.append(path)
            if (delay := self.faults.delay_for(path)) > 0:
                await asyncio.sleep(delay)
            if self.faults.should_fail(path):
                return Response("temporarily unavailable", status_code=503)
            return await call_next(request)


def html_page(title: str, body: str, head: str = "") -> HTMLResponse:
    """Wrap ``body`` in a minimal HTML document."""
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title>{head}</head>"
        f"<body>{body}</body></html>"
    )


class _ThreadedServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # not on the main thread
        return None


class MockHub:
    """Starts a set of mock sites on random loopback ports and tears them down again."""

    def __init__(self) -> None:
        self.sites: dict[str, MockSite] = {}
        self.mailbox = Mailbox()
        self._servers: list[tuple[_ThreadedServer, threading.Thread]] = []

    def add(self, site: MockSite) -> MockSite:
        if site.name in self.sites:
            raise ValueError(f"duplicate mock site name {site.name!r}")
        site.hub = self
        self.sites[site.name] = site
        return site

    def start(self) -> MockHub:
        for site in self.sites.values():
            config = uvicorn.Config(
                site.app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
            )
            server = _ThreadedServer(config)
            thread = threading.Thread(target=server.run, name=f"mock-{site.name}", daemon=True)
            thread.start()
            deadline = time.monotonic() + 15
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"mock site {site.name!r} failed to start")
                time.sleep(0.02)
            site.port = server.servers[0].sockets[0].getsockname()[1]
            self._servers.append((server, thread))
        return self

    def stop(self) -> None:
        for server, _ in self._servers:
            server.should_exit = True
        for _, thread in self._servers:
            thread.join(timeout=10)
        self._servers.clear()
        for site in self.sites.values():
            site.port = None

    def __enter__(self) -> MockHub:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def site(self, name: str) -> MockSite:
        return self.sites[name]

    def all_submissions(self) -> list[Submission]:
        return sorted(
            (s for site in self.sites.values() for s in site.submissions),
            key=lambda s: s.received_at,
        )


@contextmanager
def running_hub(*sites: MockSite) -> Iterator[MockHub]:
    hub = MockHub()
    for site in sites:
        hub.add(site)
    with hub:
        yield hub
