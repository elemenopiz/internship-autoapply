"""HTTP-level protections for the local dashboard (docs/SPEC.md section 5.13).

One pure-ASGI middleware (``SecurityMiddleware``) wraps the whole app, so no route can forget a check and even
error responses carry the security headers:

* **Host allow-list** (DNS-rebinding defence): only ``127.0.0.1``, ``localhost`` and ``[::1]`` (plus hosts the
  operator explicitly adds) are served; anything else is HTTP 400. A page on ``evil.example`` that re-points its
  DNS name at 127.0.0.1 still sends ``Host: evil.example`` and is refused.
* **Cross-site defence**: a request with an ``Origin`` that is not this server's own origin, or with fetch
  metadata (``Sec-Fetch-Site``) saying it came from another site, is refused (HTTP 403). Cross-site top-level
  navigations (a link click) to a page are the only cross-site requests allowed, and only for reads.
* **CSRF**: an HttpOnly, SameSite=Strict session cookie holds a random session id; the per-session CSRF token
  is ``HMAC(server secret, session id)``. Every POST/PUT/PATCH/DELETE must carry it as ``X-CSRF-Token``. The
  server secret lives in memory only, so a restart simply invalidates old pages (reload fixes it).
* **Security headers** on every response: CSP, nosniff, frame denial, no referrer, no caching, same-origin
  resource policy.
* **Body size limits**, enforced while the body streams: 2 MiB for JSON, ~10.25 MiB for the resume upload.
* Unhandled exceptions become a generic JSON 500 (no traceback, no internals) that still carries the headers.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
from collections.abc import Iterable

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("autoapply.dashboard")

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "CSRF_HEADER",
    "DEFAULT_ALLOWED_HOSTS",
    "MAX_JSON_BODY_BYTES",
    "MAX_UPLOAD_BYTES",
    "SECURITY_HEADERS",
    "SESSION_COOKIE",
    "SecurityMiddleware",
    "SessionSigner",
    "host_allowed",
    "is_loopback_bind_host",
    "split_host_header",
]

SESSION_COOKIE = "autoapply_session"
CSRF_HEADER = "X-CSRF-Token"
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # the resume PDF itself
_MULTIPART_OVERHEAD_BYTES = 256 * 1024  # boundaries and part headers around the file
MAX_JSON_BODY_BYTES = 2 * 1024 * 1024

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SID_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_HOST_PATTERN = re.compile(
    r"(?P<host>[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\])(?::(?P<port>[0-9]{1,5}))?"
)
_NO_COOKIE_PREFIXES = ("/static/",)


# ------------------------------------------------------------------------------------------ host helpers


def split_host_header(value: str | None) -> tuple[str, int | None] | None:
    """Parse a ``Host`` header into ``(lower-cased host, port)``; ``None`` when it is malformed.

    IPv6 literals keep their brackets (``[::1]``), matching how browsers send them.
    """
    if not value:
        return None
    match = _HOST_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    port = int(match["port"]) if match["port"] else None
    if port is not None and not 0 < port < 65536:
        return None
    return match["host"].lower(), port


def host_allowed(value: str | None, allowed: Iterable[str] = DEFAULT_ALLOWED_HOSTS) -> bool:
    """Whether a ``Host`` header names one of the allowed hosts (any port)."""
    parsed = split_host_header(value)
    if parsed is None:
        return False
    return parsed[0] in {host.lower() for host in allowed}


def is_loopback_bind_host(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals (127.0.0.0/8, ::1); False for everything else."""
    candidate = host.strip().strip("[]").lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------ CSRF tokens


class SessionSigner:
    """Derives the per-session CSRF token from the session id with a secret that never leaves memory."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret if secret else secrets.token_bytes(32)

    @staticmethod
    def new_session_id() -> str:
        return secrets.token_urlsafe(32)  # 43 url-safe characters

    @staticmethod
    def valid_session_id(value: str | None) -> bool:
        return bool(value) and _SID_PATTERN.fullmatch(value or "") is not None

    def token_for(self, session_id: str) -> str:
        return hmac.new(self._secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()

    def verify(self, session_id: str, token: str) -> bool:
        if not self.valid_session_id(session_id) or not token:
            return False
        expected = self.token_for(session_id).encode("ascii")
        return hmac.compare_digest(expected, token.encode("utf-8", "replace"))


# ------------------------------------------------------------------------------------------ middleware


class _BodyTooLargeError(Exception):
    """Raised from ``receive`` when a request body exceeds its limit while streaming."""


class SecurityMiddleware:
    """See the module docstring. ``upload_paths`` are the only routes allowed the large multipart limit."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        signer: SessionSigner,
        allowed_hosts: Iterable[str] = (),
        upload_paths: Iterable[str] = ("/api/resume", "/api/resume/upload"),
        max_json_bytes: int = MAX_JSON_BODY_BYTES,
        max_upload_bytes: int = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES,
    ) -> None:
        self.app = app
        self.signer = signer
        self.allowed_hosts = frozenset(h.lower() for h in (*DEFAULT_ALLOWED_HOSTS, *allowed_hosts))
        self.upload_paths = frozenset(upload_paths)
        self.max_json_bytes = max_json_bytes
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":  # the dashboard has no websocket endpoints
                await send({"type": "websocket.close", "code": 1008})
            return
        await self._http(scope, receive, send)

    # -- http --------------------------------------------------------------------------------------
    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = Headers(scope=scope)
        method = str(scope["method"]).upper()
        path = str(scope["path"])
        session_id: str | None = None
        issue_cookie = False
        started = False
        overflow = False

        async def emit(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                out = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    out[name] = value
                if issue_cookie and session_id and not path.startswith(_NO_COOKIE_PREFIXES):
                    out.append(
                        "set-cookie",
                        f"{SESSION_COOKIE}={session_id}; HttpOnly; Path=/; SameSite=Strict",
                    )
            await send(message)

        async def guarded_send(message: Message) -> None:
            # FastAPI turns a failed body read into its own HTTP 400; once the limit was hit that
            # reaction is discarded and the client gets the 413 below instead.
            if not overflow:
                await emit(message)

        async def reject(status: int, code: str, message: str, *, close: bool = False) -> None:
            response = JSONResponse(
                {"code": code, "message": message, "detail": message},
                status_code=status,
                headers={"Connection": "close"} if close else None,
            )
            await response(scope, receive, emit)

        host_header = headers.get("host")
        if not host_allowed(host_header, self.allowed_hosts):
            await reject(
                400,
                "bad_host",
                "This dashboard only answers to 127.0.0.1, localhost and [::1].",
            )
            return
        assert host_header is not None  # host_allowed() is False for a missing header

        blocked = self._cross_site_verdict(scope, headers, method, host_header)
        if blocked is not None:
            await reject(403, blocked[0], blocked[1])
            return

        cookies = cookie_parser(headers.get("cookie", ""))
        supplied_sid = cookies.get(SESSION_COOKIE)
        has_session = self.signer.valid_session_id(supplied_sid)
        session_id = supplied_sid if has_session and supplied_sid else self.signer.new_session_id()
        issue_cookie = not has_session
        token = self.signer.token_for(session_id)
        state = scope.setdefault("state", {})
        state["csrf_token"] = token
        state["session_id"] = session_id

        if method not in _SAFE_METHODS and (
            not has_session or not self.signer.verify(session_id, headers.get(CSRF_HEADER, ""))
        ):
            await reject(
                403,
                "csrf_failed",
                "Missing or invalid CSRF token. Reload the page and try again.",
            )
            return

        limit = self._body_limit(method, path, headers)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await reject(413, "body_too_large", "The request body is too large.", close=True)
            return

        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen, overflow
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    overflow = True
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, guarded_send)
            if overflow:
                await reject(413, "body_too_large", "The request body is too large.", close=True)
        except _BodyTooLargeError:
            if started:
                raise
            await reject(413, "body_too_large", "The request body is too large.", close=True)
        except Exception:
            if overflow and not started:
                await reject(413, "body_too_large", "The request body is too large.", close=True)
                return
            log.exception("unhandled error while serving %s %s", method, path)
            if started:
                raise
            await reject(500, "internal_error", "Unexpected error. See the application log.")

    # -- helpers -----------------------------------------------------------------------------------
    def _cross_site_verdict(
        self, scope: Scope, headers: Headers, method: str, host_header: str
    ) -> tuple[str, str] | None:
        """``(code, message)`` when the request is cross-site and must be refused, else ``None``."""
        origin = headers.get("origin")
        if origin is not None:
            expected = f"{scope.get('scheme', 'http')}://{host_header}"
            if origin.lower() != expected.lower():
                return ("cross_origin", "Requests from another origin are not allowed.")
        site = (headers.get("sec-fetch-site") or "").lower()
        if not site:
            return None
        if method in _SAFE_METHODS:
            navigation = (
                headers.get("sec-fetch-mode", "").lower() == "navigate"
                and headers.get("sec-fetch-dest", "").lower() == "document"
            )
            if site in {"cross-site", "same-site"} and not navigation:
                return ("cross_site", "Cross-site requests are not allowed.")
            return None
        if site not in {"same-origin", "none"}:
            return ("cross_site", "Cross-site requests are not allowed.")
        return None

    def _body_limit(self, method: str, path: str, headers: Headers) -> int:
        multipart = headers.get("content-type", "").lower().startswith("multipart/form-data")
        if method == "POST" and multipart and path in self.upload_paths:
            return self.max_upload_bytes
        return self.max_json_bytes
