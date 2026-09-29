"""ImapEmailVerifier + build_email_verifier against an in-process fake IMAP server (real RFC-822 mails)."""

from __future__ import annotations

import imaplib
import logging
import re
import ssl
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest

from autoapply.apply import emailverify
from autoapply.apply.emailverify import ImapEmailVerifier, build_email_verifier
from autoapply.clock import FakeClock
from autoapply.config import AppConfig
from autoapply.contracts import EmailVerifier
from autoapply.secrets import (
    SERVICE_IMAP,
    CredentialStoreError,
    MemoryCredentialStore,
)

USER = "alex.rivera@example.test"
PASSWORD = "app-Pass-ZQX-9911-fictional"
TO = "alex.rivera@example.test"
LINK = "https://acme.wd5.myworkdayjobs.com/en-US/External/verifyEmail/Zx81QdLm"
INTERVAL = 5.0


def make_mail(
    *,
    to: str = TO,
    subject: str = "Verify your account",
    body: str | None = None,
    link: str | None = LINK,
    sender: str = "Acme Talent <no-reply@acme.example.test>",
    headers: dict[str, str] | None = None,
    date: datetime | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    for name, value in (headers or {}).items():
        msg[name] = value
    if date is not None:
        msg["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S +0000")
    text = body if body is not None else f"Please verify your email:\n\n{link}\n"
    msg.set_content(text)
    return msg.as_bytes()


@dataclass
class Stored:
    uid: int
    raw: bytes
    arrived: datetime
    flags: set[str] = field(default_factory=set)


class FakeTime:
    """Injected ``sleep``/``monotonic``; sleeping also moves the wall clock so mail ages stay consistent."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self.clock.advance(timedelta(seconds=seconds))


class FakeServer:
    def __init__(self, clock: FakeClock, *, mailbox: str = "INBOX") -> None:
        self.clock = clock
        self.mailbox = mailbox
        self.messages: list[Stored] = []
        self.next_uid = 4200  # UIDs deliberately unrelated to positions
        self.commands: list[str] = []
        self.violations: list[str] = []
        self.searches = 0
        self.connects = 0
        self.logouts = 0
        self.selects: list[tuple[str, bool]] = []
        self.search_args: list[tuple[str, ...]] = []
        self.body_fetches: list[int] = []
        self.on_search: dict[int, Callable[[], None]] = {}
        self.faults: dict[str, deque[BaseException]] = defaultdict(deque)
        self.select_status = "OK"
        self.vanish_on_body: set[int] = set()

    def deliver(self, raw: bytes, *, ago: timedelta = timedelta(0)) -> int:
        uid = self.next_uid
        self.next_uid += 1
        self.messages.append(Stored(uid, raw, self.clock.now() - ago))
        return uid

    def fault(self, where: str, exc: BaseException) -> None:
        self.faults[where].append(exc)

    def factory(self, host: str, port: int) -> FakeConnection:
        self.connects += 1
        self.commands.append("CONNECT")
        if self.faults["connect"]:
            raise self.faults["connect"].popleft()
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.state = "NONAUTH"

    def _enter(self, name: str, fault: str | None = None) -> None:
        self.server.commands.append(name)
        if self.server.faults[fault or name.lower()]:
            raise self.server.faults[fault or name.lower()].popleft()

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        self._enter("LOGIN")
        if (user, password) != (USER, PASSWORD):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        self.state = "AUTH"
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[Any]]:
        self._enter("SELECT")
        assert self.state in ("AUTH", "SELECTED")
        self.server.selects.append((mailbox, readonly))
        if not readonly:
            self.server.violations.append("SELECT without readonly")
        if self.server.select_status != "OK":
            return self.server.select_status, [b"[NONEXISTENT] Unknown Mailbox"]
        self.state = "SELECTED"
        return "OK", [b"1"]

    def noop(self) -> tuple[str, list[Any]]:
        self._enter("NOOP")
        return "OK", [b"NOOP completed"]

    def logout(self) -> tuple[str, list[Any]]:
        self.server.commands.append("LOGOUT")
        self.server.logouts += 1
        return "BYE", [b"bye"]

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        assert self.state == "SELECTED"
        verb = command.upper()
        if verb == "SEARCH":
            return self._search(args)
        if verb == "FETCH":
            return self._fetch(args)
        self.server.commands.append(f"UID {verb}")
        self.server.violations.append(f"UID {verb}")
        raise imaplib.IMAP4.error("BAD read-only session")

    def _search(self, args: tuple[str, ...]) -> tuple[str, list[Any]]:
        server = self.server
        server.searches += 1
        server.commands.append("UID SEARCH")
        server.search_args.append(args)
        if callback := server.on_search.get(server.searches):
            callback()
        if server.faults["search"]:
            raise server.faults["search"].popleft()
        assert args[0] == "SINCE" and re.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}", args[1]), args
        since = datetime.strptime(args[1], "%d-%b-%Y").date()  # noqa: DTZ007
        uids = [m.uid for m in server.messages if m.arrived.astimezone(UTC).date() >= since]
        return "OK", [" ".join(map(str, uids)).encode()]

    def _fetch(self, args: tuple[str, ...]) -> tuple[str, list[Any]]:
        server = self.server
        uid_set, items = args
        server.commands.append("UID FETCH")
        if server.faults["fetch"]:
            raise server.faults["fetch"].popleft()
        wanted = {int(u) for u in uid_set.split(",")}
        found = [m for m in server.messages if m.uid in wanted]
        if items == "(INTERNALDATE)":
            stamp = lambda m: m.arrived.astimezone(UTC).strftime("%d-%b-%Y %H:%M:%S +0000")  # noqa: E731
            return "OK", [
                f'{i} (UID {m.uid} INTERNALDATE "{stamp(m)}")'.encode()
                for i, m in enumerate(found, 1)
            ]
        if items == "(BODY.PEEK[])":
            data: list[Any] = []
            for m in (m for m in found if m.uid not in server.vanish_on_body):
                server.body_fetches.append(m.uid)
                data.append((f"1 (UID {m.uid} BODY[] {{{len(m.raw)}}}".encode(), m.raw))
                data.append(b")")
            return "OK", data or [None]
        for m in found:
            m.flags.add("\\Seen")  # a non-PEEK fetch would mark the mail as read
        server.violations.append(f"FETCH {items}")
        return "OK", []


@dataclass
class Rig:
    clock: FakeClock
    time: FakeTime
    server: FakeServer
    verifier: ImapEmailVerifier


@pytest.fixture
def rig(fake_clock: FakeClock) -> Rig:
    return make_rig(fake_clock)


def make_rig(clock: FakeClock, **kwargs: Any) -> Rig:
    fake_time = FakeTime(clock)
    server = FakeServer(clock, mailbox=kwargs.get("mailbox", "INBOX"))
    verifier = ImapEmailVerifier(
        "imap.example.test",
        993,
        USER,
        PASSWORD,
        clock=clock,
        poll_interval_s=INTERVAL,
        sleep=fake_time.sleep,
        monotonic=fake_time.monotonic,
        imap_factory=server.factory,
        **kwargs,
    )
    return Rig(clock, fake_time, server, verifier)


# ------------------------------------------------------------------------------------------ happy paths


def test_link_is_found_and_the_mailbox_is_left_untouched(rig: Rig) -> None:
    uid = rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=60) == LINK
    server = rig.server
    assert set(server.commands) <= {
        "CONNECT",
        "LOGIN",
        "SELECT",
        "UID SEARCH",
        "UID FETCH",
        "LOGOUT",
    }
    assert server.selects == [('"INBOX"', True)]
    assert server.violations == []
    assert server.body_fetches == [uid]
    assert all(not m.flags for m in server.messages)
    assert server.logouts == server.connects == 1
    assert rig.time.sleeps == []


def test_code_is_found(rig: Rig) -> None:
    rig.server.deliver(make_mail(subject="Your code", body="Your verification code is 482913\n"))
    assert rig.verifier.wait_for_code(to_address=TO) == "482913"


def test_search_is_bounded_by_an_english_date_a_day_before_the_cutoff(rig: Rig) -> None:
    rig.server.deliver(make_mail())
    rig.verifier.wait_for_link(to_address=TO)
    assert rig.server.search_args == [("SINCE", "28-Sep-2026")]


def test_mailbox_names_with_spaces_are_quoted(fake_clock: FakeClock) -> None:
    rig = make_rig(fake_clock, mailbox="[Gmail]/All Mail")
    rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO) == LINK
    assert rig.server.selects == [('"[Gmail]/All Mail"', True)]


def test_verifier_satisfies_the_contract(rig: Rig) -> None:
    verifier: EmailVerifier = rig.verifier
    assert verifier.wait_for_code(to_address=TO, timeout_s=0) is None


# ------------------------------------------------------------------------------------------ selection


def test_newest_matching_mail_wins(rig: Rig) -> None:
    older = "https://acme.example.test/verify/old"
    newer = "https://acme.example.test/verify/new"
    rig.server.deliver(make_mail(link=older), ago=timedelta(seconds=50))
    rig.server.deliver(make_mail(link=newer), ago=timedelta(seconds=10))
    assert rig.verifier.wait_for_link(to_address=TO) == newer


def test_newest_mail_without_a_link_falls_back_to_an_older_match(rig: Rig) -> None:
    rig.server.deliver(
        make_mail(link="https://acme.example.test/verify/a"), ago=timedelta(seconds=40)
    )
    rig.server.deliver(
        make_mail(subject="Welcome", body="Welcome to Acme!"), ago=timedelta(seconds=5)
    )
    assert rig.verifier.wait_for_link(to_address=TO) == "https://acme.example.test/verify/a"


def test_old_mails_are_ignored_and_never_downloaded(rig: Rig) -> None:
    old = rig.server.deliver(
        make_mail(link="https://acme.example.test/verify/stale"), ago=timedelta(hours=3)
    )
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=0) is None
    assert old not in rig.server.body_fetches


def test_freshness_slack_is_two_minutes_before_the_wait_started(rig: Rig) -> None:
    rig.server.deliver(
        make_mail(link="https://acme.example.test/verify/in"), ago=timedelta(minutes=2)
    )
    rig.server.deliver(
        make_mail(link="https://acme.example.test/verify/out"), ago=timedelta(minutes=2, seconds=1)
    )
    assert (
        rig.verifier.wait_for_link(to_address=TO, timeout_s=0)
        == "https://acme.example.test/verify/in"
    )
    assert rig.server.body_fetches == [4200]


def test_the_wait_start_not_the_construction_time_sets_the_window(fake_clock: FakeClock) -> None:
    rig = make_rig(fake_clock)
    rig.server.deliver(make_mail(), ago=timedelta(seconds=30))
    fake_clock.advance(timedelta(minutes=30))  # verifier built long ago, mail is now stale
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=0) is None


def test_mail_dated_before_the_window_by_header_only_is_still_judged_by_arrival(rig: Rig) -> None:
    rig.server.deliver(make_mail(date=datetime(2020, 1, 1, tzinfo=UTC)))
    assert rig.verifier.wait_for_link(to_address=TO) == LINK  # INTERNALDATE (server time) wins


@pytest.mark.parametrize(
    ("headers", "to_header", "wanted", "expected"),
    [
        ({}, TO, TO, True),
        ({}, "Alex Rivera <alex.rivera@example.test>", "ALEX.RIVERA@EXAMPLE.TEST", True),
        ({}, "someone@example.test", TO, False),
        ({"Cc": "Team <alex.rivera@example.test>"}, "someone@example.test", TO, True),
        ({"Delivered-To": "alex.rivera@example.test"}, "list@example.test", TO, True),
        ({"X-Original-To": "alex.rivera@example.test"}, "list@example.test", TO, True),
        ({}, "alex.rivera+acme@example.test", "alex.rivera+acme@example.test", True),
        ({}, "alex.rivera+globex@example.test", "alex.rivera+acme@example.test", False),
        ({}, "alex.rivera+acme@example.test", TO, False),
        ({}, TO, "alex.rivera+acme@example.test", False),
        ({}, '"alex.rivera@example.test" <phish@evil.test>', TO, False),
        ({}, "undisclosed-recipients:;", TO, False),
    ],
)
def test_recipient_matching(
    rig: Rig, headers: dict[str, str], to_header: str, wanted: str, expected: bool
) -> None:
    rig.server.deliver(make_mail(to=to_header, headers=headers))
    got = rig.verifier.wait_for_link(to_address=wanted, timeout_s=0)
    assert (got == LINK) is expected


def test_blank_recipient_returns_immediately(rig: Rig) -> None:
    rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address="  ", timeout_s=30) is None
    assert rig.server.connects == 0


def test_subject_and_sender_hints(rig: Rig) -> None:
    rig.server.deliver(
        make_mail(
            subject="Verify your account",
            sender="Globex <hr@globex.example.test>",
            link="https://g.example.test/verify/1",
        )
    )
    rig.server.deliver(
        make_mail(
            subject="Security notice",
            sender="Acme <hr@acme.example.test>",
            link="https://a.example.test/verify/2",
        )
    )
    ask = rig.verifier.wait_for_link
    assert (
        ask(to_address=TO, subject_contains="VERIFY", timeout_s=0)
        == "https://g.example.test/verify/1"
    )
    assert (
        ask(to_address=TO, sender_contains="acme.example", timeout_s=0)
        == "https://a.example.test/verify/2"
    )
    assert (
        ask(to_address=TO, subject_contains="verify", sender_contains="acme", timeout_s=0) is None
    )
    assert ask(to_address=TO, subject_contains="nothing like this", timeout_s=0) is None


def test_encoded_word_subject_and_sender_are_decoded(rig: Rig) -> None:
    raw = (
        make_mail(subject="x")
        .replace(b"Subject: x", b"Subject: =?utf-8?B?VsOpcmlmaWV6IHZvdHJlIGNvbXB0ZQ==?=")
        .replace(
            b"From: Acme Talent <no-reply@acme.example.test>",
            b"From: =?utf-8?q?R=C3=A9sum=C3=A9_Bot?= <bot@acme.example.test>",
        )
    )
    rig.server.deliver(raw)
    ask = rig.verifier.wait_for_link
    assert ask(to_address=TO, subject_contains="Vérifiez", timeout_s=0) == LINK
    assert ask(to_address=TO, sender_contains="résumé bot", timeout_s=0) == LINK


# ------------------------------------------------------------------------------------------ timing


def test_mail_arriving_on_the_third_poll(rig: Rig) -> None:
    rig.server.on_search[3] = lambda: rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=120) == LINK
    assert rig.server.searches == 3
    assert rig.time.sleeps == [INTERVAL, INTERVAL]
    assert "NOOP" in rig.server.commands  # later polls refresh the selected mailbox
    assert rig.server.connects == 1  # one connection reused across polls


@pytest.mark.parametrize(
    ("timeout", "sleeps", "polls"),
    [
        (0, [], 1),
        (3, [3.0], 2),
        (5, [5.0], 2),
        (7, [5.0, 2.0], 3),
        (12, [5.0, 5.0, 2.0], 4),
        (15, [5.0, 5.0, 5.0], 4),
    ],
)
def test_timeout_is_honoured_exactly(
    rig: Rig, timeout: int, sleeps: list[float], polls: int
) -> None:
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=timeout) is None
    assert rig.time.sleeps == sleeps
    assert rig.time.now == 1000.0 + timeout
    assert rig.server.searches == polls
    assert rig.server.logouts == rig.server.connects


def test_slow_polls_never_cause_oversleeping(rig: Rig) -> None:
    def slow() -> None:
        rig.time.now += 4.0  # each poll itself takes 4 (fake) seconds

    for n in range(1, 10):
        rig.server.on_search[n] = slow
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=10) is None
    assert rig.time.sleeps == [
        INTERVAL
    ]  # 4s poll, 5s sleep, 4s poll: the deadline (10s) has passed
    assert rig.server.searches == 2


def test_late_mail_after_the_deadline_is_not_returned(rig: Rig) -> None:
    rig.server.on_search[5] = lambda: rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=12) is None
    assert rig.server.searches == 4


# ------------------------------------------------------------------------------------------ failures


def test_imap_errors_then_recovery(rig: Rig) -> None:
    rig.server.deliver(make_mail())
    rig.server.fault("select", imaplib.IMAP4.abort("socket error"))
    rig.server.fault("search", ConnectionResetError("reset by peer"))
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=60) == LINK
    assert rig.server.connects == 3
    assert rig.server.logouts == 3  # broken connections are closed too
    assert rig.time.sleeps == [INTERVAL, INTERVAL]


def test_connect_errors_are_retried_until_the_timeout(rig: Rig) -> None:
    for _ in range(10):
        rig.server.fault("connect", OSError("network unreachable"))
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=10) is None
    assert rig.server.connects == 3
    assert rig.time.sleeps == [INTERVAL, INTERVAL]


def test_socket_timeout_and_ssl_errors_are_survivable(rig: Rig) -> None:
    rig.server.deliver(make_mail())
    rig.server.fault("login", TimeoutError("timed out"))
    rig.server.fault("login", ssl.SSLError("EOF occurred in violation of protocol"))
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=30) == LINK


def test_login_rejection_ends_the_wait_at_once(
    fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    rig = make_rig(fake_clock)
    verifier = ImapEmailVerifier(
        "imap.example.test",
        993,
        USER,
        "wrong-Pass-fictional-000",
        clock=fake_clock,
        sleep=rig.time.sleep,
        monotonic=rig.time.monotonic,
        imap_factory=rig.server.factory,
    )
    caplog.set_level(logging.DEBUG)
    assert verifier.wait_for_link(to_address=TO, timeout_s=120) is None
    assert rig.server.connects == 1 and rig.time.sleeps == []
    assert rig.server.logouts == 1
    assert "rejected" in caplog.text
    assert "wrong-Pass-fictional-000" not in caplog.text
    assert "AUTHENTICATIONFAILED" not in caplog.text  # server text is never echoed


def test_non_ascii_password_is_a_permanent_login_failure(fake_clock: FakeClock) -> None:
    rig = make_rig(fake_clock)

    class AsciiOnly(FakeConnection):
        def login(self, user: str, password: str) -> tuple[str, list[Any]]:
            password.encode("ascii")
            return super().login(user, password)

    verifier = ImapEmailVerifier(
        "h.example.test",
        993,
        USER,
        "pässwörd",
        clock=fake_clock,
        sleep=rig.time.sleep,
        monotonic=rig.time.monotonic,
        imap_factory=lambda h, p: AsciiOnly(rig.server),
    )
    assert verifier.wait_for_link(to_address=TO, timeout_s=60) is None
    assert rig.time.sleeps == []


def test_missing_mailbox_ends_the_wait_at_once(rig: Rig) -> None:
    rig.server.select_status = "NO"
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=60) is None
    assert rig.server.connects == 1 and rig.time.sleeps == []


def test_factory_that_raises_returns_none(fake_clock: FakeClock) -> None:
    fake_time = FakeTime(fake_clock)

    def boom(host: str, port: int) -> Any:
        raise RuntimeError("cannot build a connection")

    verifier = ImapEmailVerifier(
        "h.example.test",
        993,
        USER,
        PASSWORD,
        clock=fake_clock,
        sleep=fake_time.sleep,
        monotonic=fake_time.monotonic,
        imap_factory=boom,
    )
    assert verifier.wait_for_code(to_address=TO, timeout_s=6) is None
    assert fake_time.sleeps == [INTERVAL, 1.0]


def test_a_crashing_extractor_never_reaches_the_caller(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig.server.deliver(make_mail())

    def explode(message: Any, hints: Any = ()) -> str:
        raise ValueError("kaboom")

    monkeypatch.setattr(emailverify, "extract_verification_link", explode)
    fresh = ImapEmailVerifier(
        "h.example.test",
        993,
        USER,
        PASSWORD,
        clock=rig.clock,
        sleep=rig.time.sleep,
        monotonic=rig.time.monotonic,
        imap_factory=rig.server.factory,
    )
    caplog.set_level(logging.DEBUG)
    assert fresh.wait_for_link(to_address=TO, timeout_s=0) is None


def test_a_mail_that_vanishes_between_search_and_fetch_is_skipped(rig: Rig) -> None:
    rig.server.deliver(
        make_mail(link="https://acme.example.test/verify/ok"), ago=timedelta(seconds=30)
    )
    gone = rig.server.deliver(make_mail(link="https://acme.example.test/verify/gone"))
    rig.server.vanish_on_body.add(gone)
    assert (
        rig.verifier.wait_for_link(to_address=TO, timeout_s=0)
        == "https://acme.example.test/verify/ok"
    )


def test_garbage_message_bytes_are_skipped(rig: Rig) -> None:
    rig.server.deliver(b"\x00\xff\xfe not an email at all \x01")
    rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=0) == LINK


def test_uids_already_judged_are_not_fetched_again(rig: Rig) -> None:
    rig.server.deliver(make_mail(subject="Welcome", body="hello, nothing to see"))
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=12) is None
    assert len(rig.server.body_fetches) == 1  # fetched once across 4 polls


# ------------------------------------------------------------------------------------------ secrets


def test_password_never_appears_in_repr_or_logs(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig.server.fault("select", imaplib.IMAP4.abort(f"server echoed {PASSWORD}"))
    rig.server.deliver(make_mail())
    assert rig.verifier.wait_for_link(to_address=TO, timeout_s=30) == LINK
    for text in (repr(rig.verifier), str(vars(rig.verifier)), caplog.text):
        assert PASSWORD not in text
    assert LINK not in caplog.text  # one-time links are not logged either
    assert "imap.example.test" in repr(rig.verifier)


# ------------------------------------------------------------------------------------------ construction


def test_default_factory_verifies_certificates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class Recorder:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            calls.append((self.kind, args, kwargs))
            raise OSError("no network in tests")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Recorder("ssl"))
    monkeypatch.setattr(imaplib, "IMAP4", Recorder("plain"))
    ImapEmailVerifier(
        "imap.example.test", 993, USER, PASSWORD, monotonic=lambda: 0.0, sleep=lambda s: None
    ).wait_for_link(to_address=TO, timeout_s=0)
    ImapEmailVerifier(
        "localhost",
        1143,
        USER,
        PASSWORD,
        use_ssl=False,
        monotonic=lambda: 0.0,
        sleep=lambda s: None,
    ).wait_for_code(to_address=TO, timeout_s=0)
    (kind, args, kwargs), (kind2, args2, kwargs2) = calls
    assert (kind, args) == ("ssl", ("imap.example.test", 993))
    context = kwargs["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert kwargs["timeout"] > 0
    assert (kind2, args2) == ("plain", ("localhost", 1143)) and kwargs2["timeout"] > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": " "},
        {"username": ""},
        {"password": ""},
        {"port": 0},
        {"port": 70000},
        {"poll_interval_s": 0},
        {"mailbox": " "},
        {"host": "imap.example.test", "use_ssl": False},
    ],
)
def test_bad_construction_arguments(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {
        "host": "imap.example.test",
        "port": 993,
        "username": USER,
        "password": PASSWORD,
    }
    args.update(kwargs)
    with pytest.raises(ValueError) as info:
        ImapEmailVerifier(**args)
    assert PASSWORD not in str(info.value)


# ------------------------------------------------------------------------------------------ build


def _config(**email: Any) -> AppConfig:
    config = AppConfig()
    values = {"enabled": True, "imap_host": "imap.example.test", "username": USER, **email}
    for key, value in values.items():
        setattr(config.apply.email, key, value)
    return config


def _stored() -> MemoryCredentialStore:
    return MemoryCredentialStore({(SERVICE_IMAP, USER): PASSWORD})


def test_build_returns_a_working_verifier_wired_from_config_and_store(
    monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    server = FakeServer(fake_clock, mailbox="Careers")
    server.deliver(make_mail())
    seen: list[tuple[str, int]] = []

    def fake_ssl(host: str, port: int, **kwargs: Any) -> FakeConnection:
        seen.append((host, port))
        return server.factory(host, port)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", fake_ssl)
    verifier = build_email_verifier(
        _config(imap_port=1993, mailbox="Careers"), _stored(), fake_clock
    )
    assert verifier is not None
    assert verifier.wait_for_link(to_address=TO, timeout_s=0) == LINK
    assert seen == [("imap.example.test", 1993)]
    assert server.selects == [
        ('"Careers"', True)
    ]  # login used the stored password (FakeConnection checks it)


@pytest.mark.parametrize(
    ("config", "store", "reason"),
    [
        (_config(enabled=False), _stored(), "off"),
        (_config(imap_host=None), _stored(), "host"),
        (_config(imap_host="  "), _stored(), "host"),
        (_config(username=None), _stored(), "username"),
        (_config(), None, "no credential store"),
        (_config(), MemoryCredentialStore(), "no IMAP app password"),
        (_config(username="other@example.test"), _stored(), "no IMAP app password"),
    ],
)
def test_build_returns_none_and_says_why(
    config: AppConfig,
    store: MemoryCredentialStore | None,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    assert build_email_verifier(config, store) is None
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert infos and reason in infos[-1].getMessage()
    assert PASSWORD not in caplog.text


def test_build_survives_a_failing_credential_store(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(MemoryCredentialStore):
        def get_secret(self, service: str, username: str) -> str | None:
            raise CredentialStoreError(f"locked {PASSWORD}")

    caplog.set_level(logging.INFO)
    assert build_email_verifier(_config(), Broken()) is None
    assert "CredentialStoreError" in caplog.text and PASSWORD not in caplog.text


def test_build_uses_the_documented_service_and_username() -> None:
    asked: list[tuple[str, str]] = []

    class Spy(MemoryCredentialStore):
        def get_secret(self, service: str, username: str) -> str | None:
            asked.append((service, username))
            return PASSWORD

    verifier = build_email_verifier(_config(), Spy())
    assert asked == [("autoapply:imap", USER)]
    assert verifier is not None and PASSWORD not in repr(verifier)
