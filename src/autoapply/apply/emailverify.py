"""Email verification: read the mail an ATS sends and pull out the link or code (docs/SPEC.md 5.9).

Two layers:

* pure extraction on ``email.message.Message`` (``extract_verification_link``, ``extract_code``): handles
  multipart/alternative, HTML-only mails, quoted-printable/base64 bodies, tracking-redirect links and buttons;
* ``ImapEmailVerifier``: polls a mailbox READ-ONLY (``EXAMINE`` + ``BODY.PEEK[]``: nothing is ever marked read,
  flagged, deleted, expunged or moved) and hands matching mails to the extractors. Every failure becomes
  ``None``; only the exception class is logged, never credentials, links, codes or mail content.

``build_email_verifier`` returns ``None`` when verification is not configured, so adapters fall back to
``Reason.EMAIL_VERIFICATION``.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import logging
import re
import ssl
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

from autoapply.clock import Clock, SystemClock
from autoapply.config import AppConfig
from autoapply.contracts import CredentialStore, EmailVerifier
from autoapply.secrets import SERVICE_IMAP, redact

__all__ = [
    "ImapConnection",
    "ImapEmailVerifier",
    "build_email_verifier",
    "extract_code",
    "extract_verification_link",
]

log = logging.getLogger("autoapply.apply.emailverify")

FRESHNESS_SLACK = timedelta(
    minutes=2
)  # tolerated clock skew / mails that arrive just before the wait
IMAP_TIMEOUT_S = 30.0
_MIN_LINK_SCORE = 3

# ============================================================================================ extraction

_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_HINT_TEXT = re.compile(
    r"verif|confirm|activat|validat|complete[\W_]+(?:your[\W_]+)?registration", re.I
)
_HINT_URL = re.compile(r"verif|confirm|activat|validat", re.IGNORECASE)
_DENY = re.compile(
    r"unsubscrib|opt[\W_]*out|privacy|(?<![a-z])terms|preferences?|(?<![a-z])logo|do[\W_]*not[\W_]*sell"
    r"|cookie|view[\W_]*(?:in[\W_]*(?:your[\W_]*)?browser|online|(?:this[\W_]*)?e[\W_]*mail)"
    r"|web[\W_]*version|manage[\W_]*(?:your[\W_]*)?(?:e[\W_]*mail|subscription|notification)",
    re.IGNORECASE,
)
_IMAGE_PATH = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp)$", re.IGNORECASE)
_SKIP_TAGS = frozenset({"script", "style", "title", "noscript"})
_BLOCK_TAGS = frozenset(
    [
        "p",
        "div",
        "br",
        "tr",
        "td",
        "th",
        "li",
        "ul",
        "ol",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "hr",
        "pre",
        "center",
        "body",
    ]
)
_WS = re.compile(r"\s+")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­͏"), None)


@dataclass(frozen=True)
class _Link:
    url: str
    text: str = ""  # anchor text (HTML only)
    before: str = ""  # nearby visible text
    after: str = ""


@dataclass(frozen=True)
class _Body:
    text: str
    links: tuple[_Link, ...]


@dataclass
class _Anchor:
    href: str
    start: int
    parts: list[str] = field(default_factory=list)
    end: int = 0


class _HtmlDigest(HTMLParser):
    """Visible text (block tags become newlines) plus every ``<a href>`` with its text and position."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._length = 0
        self._skip = 0
        self._open: _Anchor | None = None
        self._done: list[_Anchor] = []

    def _emit(self, text: str) -> None:
        self._chunks.append(text)
        self._length += len(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            self._close_anchor()
            self._open = _Anchor(dict(attrs).get("href") or "", self._length)
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt and self._open is not None:
                self._open.parts.append(alt)
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            self._close_anchor()
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        collapsed = _WS.sub(" ", data)
        if collapsed:
            self._emit(collapsed)
            if self._open is not None:
                self._open.parts.append(collapsed)

    def _close_anchor(self) -> None:
        if self._open is not None:
            self._open.end = self._length
            self._done.append(self._open)
            self._open = None

    def digest(self, html: str) -> _Body:
        try:
            self.feed(html)
            self.close()
        except Exception:  # noqa: BLE001 - malformed markup: keep whatever was collected
            pass
        self._close_anchor()
        full = "".join(self._chunks)
        links = []
        for anchor in self._done:
            url = re.sub(r"[\s]+", "", anchor.href)
            if not url:
                continue
            links.append(
                _Link(
                    url,
                    _WS.sub(" ", " ".join(anchor.parts)).strip(),
                    full[max(0, anchor.start - 120) : anchor.start],
                    full[anchor.end : anchor.end + 60],
                )
            )
        return _Body(_tidy(full), tuple(links))


def _tidy(text: str) -> str:
    text = text.translate(_INVISIBLE).replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _trim_url(url: str) -> str:
    pairs = {")": "(", "]": "[", "}": "{"}
    while url:
        last = url[-1]
        if last in ".,;:!?" or last in pairs and url.count(last) > url.count(pairs[last]):
            url = url[:-1]
        else:
            break
    return url


def _decode_part(part: Message) -> str | None:
    try:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - a broken part must not break the whole mail
        return None


def _bodies(message: Message) -> list[_Body]:
    """Text bodies of a mail: text/plain parts first, then text/html parts (attachments are skipped)."""
    plain: list[_Body] = []
    html: list[_Body] = []
    try:
        parts = list(message.walk())
    except Exception:  # noqa: BLE001
        parts = [message]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        text = _decode_part(part)
        if not text:
            continue
        if kind == "text/html":
            html.append(_HtmlDigest().digest(text))
            continue
        text = _tidy(text)
        links = tuple(
            _Link(
                _trim_url(m.group(0)),
                "",
                text[max(0, m.start() - 120) : m.start()],
                text[m.end() : m.end() + 60],
            )
            for m in _URL.finditer(text)
        )
        plain.append(_Body(text, links))
    return plain + html


def _score_link(link: _Link, hints: tuple[str, ...]) -> int | None:
    """Evidence that ``link`` is the verification link; ``None`` for ignorable links (unsubscribe, ...)."""
    parts = urlsplit(link.url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    decoded = unquote(unquote(link.url))
    # the link itself plus every URL wrapped by a click tracker
    targets = re.split(r"(?=https?://)", decoded, flags=re.IGNORECASE)
    paths = [unquote(urlsplit(t).path) for t in targets]
    if _DENY.search(link.text) or any(_DENY.search(p) for p in paths):
        return None
    if any(_IMAGE_PATH.search(p) for p in paths):
        return None
    lowered = decoded.lower()
    score = 0
    if _HINT_TEXT.search(link.text):
        score += 8
    if _HINT_URL.search(lowered):
        score += 5
    if "token" in lowered:
        score += 3
    if hints and any(h in lowered or h in link.text.lower() for h in hints):
        score += 3
    if _HINT_TEXT.search(link.before):
        score += 5
    if _HINT_TEXT.search(link.after[:40]):
        score += 1
    return score


def extract_verification_link(message: Message, hints: Iterable[str] = ()) -> str | None:
    """The most likely email-verification URL in ``message`` or ``None``.

    Candidates are HTML anchors and bare URLs in text parts. Evidence: anchor text saying
    Verify/Confirm/Activate/Validate, those words (or ``token``) in the URL or in a click-tracker's wrapped
    target, nearby prose, and any extra ``hints``. Unsubscribe/privacy/terms/preferences/logo/image links and
    non-http(s) schemes are ignored. A mail with no positive evidence yields ``None`` (so a poller keeps
    looking) rather than a random link. The URL is returned as written in the mail (trackers not unwrapped).
    """
    extra = tuple(h.strip().lower() for h in hints if h and h.strip())
    best: dict[str, int] = {}
    order: list[str] = []
    for body in _bodies(message):
        for link in body.links:
            score = _score_link(link, extra)
            if score is None or score < _MIN_LINK_SCORE:
                continue
            if link.url not in best:
                order.append(link.url)
            best[link.url] = max(best.get(link.url, 0), score)
    if not order:
        return None
    return max(order, key=lambda url: (best[url], -order.index(url)))


_NUM = r"(?<![\w.\-#$/@])({d})(?![\w@\-]|[.,]\d|\s\d)"
_CUE = re.compile(r"\b(?:code|passcode|otp|one[- ]?time|verification)\b", re.IGNORECASE)
_DIGITS = re.compile(_NUM.format(d=r"[0-9]{4,8}"))
_GROUPED = re.compile(r"(?<![\w.\-#$/@])([0-9]{3,4})[ \-]([0-9]{3,4})(?![\w@\-]|[.,]\d|\s\d)")
_NUMBER_IS_CODE = re.compile(
    _NUM.format(d=r"[0-9]{4,8}") + r"\s+(?:is|=)\s+(?:your|the|my)\b[^\n.]{0,40}?"
    r"\b(?:code|passcode|otp|pin)\b",
    re.IGNORECASE,
)
_STANDALONE = re.compile(r"(?<![\w.\-#$/@])([0-9]{6})(?![\w@\-]|[.,]\d|\s\d)")
_ALNUM_CUE = re.compile(
    r"\b(?:security|verification|confirmation|access|one[- ]?time|login|sign[- ]?in)\s+"
    r"(?:code|passcode)\b",
    re.IGNORECASE,
)
_ALNUM_TOKEN = re.compile(r"(?:(?<=:)|(?<=\n))[ \t]*([A-Za-z0-9]{6,10})[ \t]*(?=\n|$)")
_YEAR_LIKE = re.compile(r"(?:19|20)\d\d")
_FORWARD_WINDOW = 100


def _digit_candidates(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, int, str]] = []
    for m in _GROUPED.finditer(text):
        joined = m.group(1) + m.group(2)
        if 4 <= len(joined) <= 8:
            found.append((m.start(), m.end(), joined))
    grouped = [(s, e) for s, e, _ in found]
    for m in _DIGITS.finditer(text):
        if not any(s <= m.start() < e for s, e in grouped):
            found.append((m.start(), m.end(), m.group(1)))
    return [(s, digits) for s, _e, digits in sorted(found)]


def _cued_digits(text: str) -> str | None:
    match = _NUMBER_IS_CODE.search(text)
    if match:
        return match.group(1)
    cues = [m.end() for m in _CUE.finditer(text)]
    best: tuple[int, int, str] | None = None
    for start, digits in _digit_candidates(text):
        for cue_end in cues:
            gap = start - cue_end
            if not 0 <= gap <= _FORWARD_WINDOW:
                continue
            rank = gap
            if len(digits) == 4 and _YEAR_LIKE.fullmatch(digits):
                rank += 60
            if re.search(r"[.!?]\s", text[cue_end:start]):
                rank += 25
            if best is None or (rank, start) < best[:2]:
                best = (rank, start, digits)
    return best[2] if best else None


def _looks_like_code(token: str) -> bool:
    if re.search(r"[0-9]", token):
        return True
    upper, lower = re.search(r"[A-Z]", token), re.search(r"[a-z]", token)
    return bool(upper and lower and not re.fullmatch(r"[A-Z][a-z]+", token))


def _cued_alphanumeric(text: str) -> str | None:
    for cue in _ALNUM_CUE.finditer(text):
        segment = text[cue.end() : cue.end() + 200]
        for match in _ALNUM_TOKEN.finditer(segment):
            if _looks_like_code(match.group(1)):
                return match.group(1)
    return None


def _standalone_digits(text: str) -> str | None:
    match = _STANDALONE.search(text)
    return match.group(1) if match else None


def extract_code(message: Message) -> str | None:
    """A one-time verification code in ``message`` or ``None``.

    In order: an alphanumeric 6-10 character code on its own line or after a colon following "security /
    verification code" (Greenhouse style); 4-8 digits near "code / passcode / one-time / verification / OTP"
    (also "123456 is your code" and "123 456" groupings; year-like numbers and URLs are ignored); else the first
    standalone 6-digit number. Returns digits/letters only.
    """
    texts = [_URL.sub(" ", body.text) for body in _bodies(message)]
    for finder in (_cued_alphanumeric, _cued_digits, _standalone_digits):
        for text in texts:
            code = finder(text)
            if code:
                return code
    return None


# ============================================================================================ IMAP


class ImapConnection(Protocol):
    """The slice of ``imaplib.IMAP4`` the verifier uses (fakes implement exactly this)."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]: ...

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[Any]]: ...

    def noop(self) -> tuple[str, list[Any]]: ...

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]: ...

    def logout(self) -> tuple[str, list[Any]]: ...


ImapFactory = Callable[[str, int], ImapConnection]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_INTERNALDATE = re.compile(
    r'INTERNALDATE\s+"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+([+-])(\d{2})(\d{2})"',
    re.IGNORECASE,
)
_UID_ITEM = re.compile(r"\bUID\s+(\d+)", re.IGNORECASE)
_RECIPIENT_HEADERS = (
    "To",
    "Cc",
    "Delivered-To",
    "X-Original-To",
    "Envelope-To",
    "X-Envelope-To",
    "Resent-To",
)
_SENDER_HEADERS = ("From", "Sender", "Reply-To", "Return-Path")
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_BATCH = 100


class _LoginRejected(Exception):
    """Permanent: bad credentials / unusable mailbox. Retrying would only hammer the server."""


def _imap_date(moment: datetime) -> str:
    """``dd-Mon-yyyy`` with English month names regardless of the OS locale (IMAP SEARCH format)."""
    utc = moment.astimezone(UTC)
    return f"{utc.day:02d}-{_MONTHS[utc.month - 1]}-{utc.year:04d}"


def _parse_internaldate(text: str) -> datetime | None:
    m = _INTERNALDATE.search(text)
    if not m or m.group(2).title() not in _MONTHS:
        return None
    day, month = int(m.group(1)), _MONTHS.index(m.group(2).title()) + 1
    offset = timedelta(hours=int(m.group(8)), minutes=int(m.group(9)))
    zone = timezone(-offset if m.group(7) == "-" else offset)
    try:
        return datetime(
            int(m.group(3)),
            month,
            day,
            int(m.group(4)),
            int(m.group(5)),
            int(m.group(6)),
            tzinfo=zone,
        ).astimezone(UTC)
    except ValueError:
        return None


def _response_texts(data: Iterable[Any]) -> Iterator[str]:
    for item in data:
        head = item[0] if isinstance(item, tuple) and item else item
        if isinstance(head, bytes):
            yield head.decode("ascii", errors="replace")


def _header_date(message: Message) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError, IndexError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _decode_header_value(value: object) -> str:
    text = str(value)
    with contextlib.suppress(Exception):  # keep the raw text when it cannot be decoded
        text = str(make_header(decode_header(text)))
    return " ".join(text.split())


def _header_text(message: Message, names: Iterable[str]) -> str:
    return " ".join(
        _decode_header_value(v) for name in names for v in (message.get_all(name) or [])
    )


def _recipients(message: Message) -> set[str]:
    values = [str(v) for name in _RECIPIENT_HEADERS for v in (message.get_all(name) or [])]
    return {addr.strip().lower() for _name, addr in getaddresses(values) if addr.strip()}


def _quote_mailbox(name: str) -> str:
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class _Query:
    to_address: str
    subject: str | None
    sender: str | None
    cutoff: datetime
    extract: Callable[[Message], str | None]

    def matches(self, message: Message) -> bool:
        if self.to_address not in _recipients(message):
            return False
        if self.subject and self.subject not in _header_text(message, ("Subject",)).casefold():
            return False
        return not (
            self.sender and self.sender not in _header_text(message, _SENDER_HEADERS).casefold()
        )


@dataclass
class _Progress:
    seen: set[int] = field(default_factory=set)  # UIDs already judged (old, fetched or gone)


def _default_factory(use_ssl: bool) -> ImapFactory:
    def make(host: str, port: int) -> ImapConnection:
        if use_ssl:  # certificate + hostname verification on: the app password crosses this channel
            return imaplib.IMAP4_SSL(
                host, port, ssl_context=ssl.create_default_context(), timeout=IMAP_TIMEOUT_S
            )
        return imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT_S)

    return make


class ImapEmailVerifier:
    """``contracts.EmailVerifier`` over IMAP (read-only; see module docstring).

    Each ``wait_for_*`` call opens its own connection, polls every ``poll_interval_s`` until ``timeout_s`` has
    elapsed (the last sleep is shortened so the deadline is met exactly) and always returns ``None`` instead
    of raising. Only mails whose arrival time is at most 2 minutes before the wait started are considered; a
    mail matches when ``to_address`` is among its To/Cc/Delivered-To/X-Original-To (and similar) addresses
    (plus-addresses match exactly) and the optional subject/sender substrings occur (case-insensitive,
    RFC 2047 decoded). The newest matching mail that yields a link/code wins. A rejected login or missing
    mailbox ends the wait at once; network/protocol errors trigger a reconnect on the next poll.

    ``use_ssl=False`` (plaintext) is only allowed for loopback hosts. ``imap_factory(host, port)`` returns an
    ``ImapConnection`` (tests inject a fake).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        mailbox: str = "INBOX",
        use_ssl: bool = True,
        clock: Clock | None = None,
        poll_interval_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        imap_factory: ImapFactory | None = None,
    ) -> None:
        if not host.strip() or not username.strip() or not password or not mailbox.strip():
            raise ValueError("IMAP host, username, password and mailbox are required")
        if not 0 < port < 65536 or poll_interval_s <= 0:
            raise ValueError("IMAP port or poll interval is out of range")
        if not use_ssl and host.strip().lower() not in _LOOPBACK:
            raise ValueError("plaintext IMAP is only allowed for loopback hosts")
        self._host = host.strip()
        self._port = port
        self._username = username
        self._password = SecretStr(password)
        self._mailbox = mailbox
        self._use_ssl = use_ssl
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._interval = poll_interval_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._factory: ImapFactory = imap_factory or _default_factory(use_ssl)

    def __repr__(self) -> str:
        return (
            f"ImapEmailVerifier(host={self._host!r}, port={self._port}, "
            f"mailbox={self._mailbox!r}, use_ssl={self._use_ssl})"
        )

    # -- EmailVerifier ---------------------------------------------------------------------------------
    def wait_for_link(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None:
        return self._wait(
            extract_verification_link, to_address, subject_contains, sender_contains, timeout_s
        )

    def wait_for_code(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None:
        return self._wait(extract_code, to_address, subject_contains, sender_contains, timeout_s)

    # -- polling ---------------------------------------------------------------------------------------
    def _wait(
        self,
        extract: Callable[[Message], str | None],
        to_address: str,
        subject: str | None,
        sender: str | None,
        timeout_s: float,
    ) -> str | None:
        try:
            wanted = to_address.strip().lower()
            if not wanted:
                log.warning("no recipient address given; not waiting for a verification mail")
                return None
            query = _Query(
                wanted,
                subject.casefold() if subject else None,
                sender.casefold() if sender else None,
                self._clock.now() - FRESHNESS_SLACK,
                extract,
            )
            return self._poll_until(query, max(0.0, float(timeout_s)))
        except Exception as exc:  # noqa: BLE001 - the adapter must never see an exception
            log.warning("waiting for a verification mail failed (%s)", type(exc).__name__)
            return None

    def _poll_until(self, query: _Query, timeout_s: float) -> str | None:
        deadline = self._monotonic() + timeout_s
        progress = _Progress()
        conn: ImapConnection | None = None
        try:
            while True:
                try:
                    if conn is None:
                        conn = self._connect()
                    else:
                        conn.noop()
                    found = self._poll(conn, query, progress)
                except _LoginRejected as exc:
                    log.warning("IMAP unusable, giving up: %s", exc)
                    return None
                except Exception as exc:  # noqa: BLE001 - reconnect on the next poll
                    log.warning("IMAP poll failed (%s); will reconnect", type(exc).__name__)
                    self._close(conn)
                    conn = None
                else:
                    if found is not None:
                        log.info("found a matching verification mail")
                        return found
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return None
                self._sleep(min(self._interval, remaining))
        finally:
            self._close(conn)

    def _connect(self) -> ImapConnection:
        conn = self._factory(self._host, self._port)
        try:
            try:
                conn.login(self._username, self._password.get_secret_value())
            except imaplib.IMAP4.abort:
                raise
            except (imaplib.IMAP4.error, UnicodeError):
                raise _LoginRejected("login was rejected; check the IMAP app password") from None
            typ, _data = conn.select(_quote_mailbox(self._mailbox), readonly=True)
            if typ != "OK":
                raise _LoginRejected("the configured mailbox could not be opened")
        except BaseException:
            self._close(conn)
            raise
        return conn

    @staticmethod
    def _close(conn: ImapConnection | None) -> None:
        if conn is None:
            return
        with contextlib.suppress(Exception):  # the connection is being discarded anyway
            conn.logout()

    def _poll(self, conn: ImapConnection, query: _Query, progress: _Progress) -> str | None:
        typ, data = conn.uid("SEARCH", "SINCE", _imap_date(query.cutoff - timedelta(days=1)))
        if typ != "OK":
            raise imaplib.IMAP4.error("search failed")
        listed = b" ".join(x for x in data if isinstance(x, bytes)).split()
        unseen = [int(u) for u in listed if u.isdigit() and int(u) not in progress.seen]
        hits: list[tuple[datetime, int, str]] = []
        for uid, arrived in self._fresh(conn, unseen, query, progress):
            message = self._fetch(conn, uid)
            progress.seen.add(uid)
            if message is None:
                continue
            when = arrived or _header_date(message)
            if when is None or when < query.cutoff:
                continue
            try:
                value = query.extract(message) if query.matches(message) else None
            except Exception as exc:  # noqa: BLE001 - one odd mail must not stop the wait
                log.debug("skipping a mail that could not be evaluated (%s)", type(exc).__name__)
                continue
            if value:
                hits.append((when, uid, value))
        return max(hits)[2] if hits else None

    def _fresh(
        self, conn: ImapConnection, uids: list[int], query: _Query, progress: _Progress
    ) -> list[tuple[int, datetime | None]]:
        """UIDs whose arrival time is not older than the cutoff (cheap INTERNALDATE fetch, no bodies)."""
        fresh: list[tuple[int, datetime | None]] = []
        for i in range(0, len(uids), _BATCH):
            chunk = uids[i : i + _BATCH]
            typ, data = conn.uid("FETCH", ",".join(map(str, chunk)), "(INTERNALDATE)")
            if typ != "OK":
                raise imaplib.IMAP4.error("fetch failed")
            dates: dict[int, datetime | None] = {}
            for text in _response_texts(data):
                uid_match = _UID_ITEM.search(text)
                if uid_match:
                    dates[int(uid_match.group(1))] = _parse_internaldate(text)
            for uid in chunk:
                if uid not in dates:
                    progress.seen.add(uid)  # vanished between SEARCH and FETCH
                elif (arrived := dates[uid]) is not None and arrived < query.cutoff:
                    progress.seen.add(uid)
                else:
                    fresh.append((uid, arrived))
        return sorted(fresh, reverse=True)

    @staticmethod
    def _fetch(conn: ImapConnection, uid: int) -> Message | None:
        typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")  # PEEK: never sets \Seen
        if typ != "OK":
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return email.message_from_bytes(item[1])
        return None


def build_email_verifier(
    config: AppConfig, store: CredentialStore | None, clock: Clock | None = None
) -> EmailVerifier | None:
    """The configured IMAP verifier, or ``None`` (with the reason logged at INFO, never a secret).

    ``None`` when ``apply.email.enabled`` is false, the IMAP host or username is missing, or the credential
    store has no password under service ``autoapply:imap`` / the configured username. Always uses IMAPS.
    """
    cfg = config.apply.email
    host, username = (cfg.imap_host or "").strip(), (cfg.username or "").strip()
    if not cfg.enabled:
        log.info("email verification is off (apply.email.enabled is false)")
        return None
    if not host or not username:
        log.info("email verification is not configured: IMAP host and username are required")
        return None
    if store is None:
        log.info("email verification unavailable: no credential store")
        return None
    try:
        password = store.get_secret(SERVICE_IMAP, username)
    except Exception as exc:  # noqa: BLE001
        log.info("email verification unavailable: credential store failed (%s)", type(exc).__name__)
        return None
    if not password:
        log.info("email verification unavailable: no IMAP app password is stored")
        return None
    try:
        return ImapEmailVerifier(
            host, cfg.imap_port, username, password, mailbox=cfg.mailbox, clock=clock
        )
    except ValueError as exc:
        log.info("email verification unavailable: %s", redact(str(exc), password))
        return None
