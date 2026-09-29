"""AccountManagerImpl: per-tenant passwords kept only in the credential store (SPEC 5.9, rule 1.7, A8)."""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from autoapply.apply.accounts import (
    ATS_SAFE_SYMBOLS,
    AccountArgumentError,
    AccountError,
    AccountManagerImpl,
    generate_password,
    normalize_account_email,
    normalize_tenant_host,
)
from autoapply.clock import FakeClock
from autoapply.contracts import AccountManager
from autoapply.db import Database, Repo
from autoapply.models import AtsCredentials
from autoapply.secrets import CredentialStoreError, MemoryCredentialStore, ats_service

HOST = "acme.wd5.myworkdayjobs.com"
EMAIL = "alex.rivera@example.test"


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "autoapply.db"


@pytest.fixture
def repo(db_file: Path, fake_clock: FakeClock) -> Repo:
    return Repo(Database(db_file), fake_clock)


@pytest.fixture
def store() -> MemoryCredentialStore:
    return MemoryCredentialStore()


@pytest.fixture
def manager(repo: Repo, store: MemoryCredentialStore) -> AccountManagerImpl:
    return AccountManagerImpl(repo, store)


def secret(creds: AtsCredentials) -> str:
    return creds.password.get_secret_value()


class ScriptedStore(MemoryCredentialStore):
    """Memory store whose operations can be made to fail or lie."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_get: Exception | None = None
        self.fail_set: Exception | None = None
        self.fail_delete: Exception | None = None
        self.drop_writes = False
        self.set_calls = 0
        self.get_delay = 0.0

    def get_secret(self, service: str, username: str) -> str | None:
        if self.fail_get:
            raise self.fail_get
        if self.get_delay:
            time.sleep(self.get_delay)
        return super().get_secret(service, username)

    def set_secret(self, service: str, username: str, secret: str) -> None:
        self.set_calls += 1
        if self.fail_set:
            raise self.fail_set
        if self.drop_writes:
            return
        super().set_secret(service, username, secret)

    def delete_secret(self, service: str, username: str) -> None:
        if self.fail_delete:
            raise self.fail_delete
        super().delete_secret(service, username)


# ------------------------------------------------------------------------------------------ passwords


def _policy_problems(password: str, email: str) -> list[str]:
    problems = []
    if not 20 <= len(password) <= 24:
        problems.append("length")
    for name, pattern in (("upper", "[A-Z]"), ("lower", "[a-z]"), ("digit", "[0-9]")):
        if len(re.findall(pattern, password)) < 2:
            problems.append(name)
    if sum(c in ATS_SAFE_SYMBOLS for c in password) < 2:
        problems.append("symbol")
    if re.search(r"[^A-Za-z0-9!@#$%&*?]", password):
        problems.append("charset")
    if re.search(r"[\s'\"`\\<>]", password):
        problems.append("forbidden")
    if re.search(r"(.)\1\1", password):
        problems.append("run")
    if email.partition("@")[0].lower() in password.lower():
        problems.append("local-part")
    return problems


def test_password_policy_holds_over_thousands_of_passwords() -> None:
    seen = set()
    for i in range(4000):
        email = f"user{i % 50}.name@example.test"
        password = generate_password(email)
        assert _policy_problems(password, email) == [], password
        seen.add(password)
    assert len(seen) == 4000


@pytest.mark.parametrize(
    "local", ["a", "A", "1", "!", "ab", "aa", "alex", "ALEX.RIVERA", "x" * 40, "9?", "q1"]
)
def test_password_never_contains_the_email_local_part(local: str) -> None:
    for _ in range(300):
        assert local.lower() not in generate_password(f"{local}@example.test").lower()


def test_password_uses_every_class_and_symbol() -> None:
    joined = "".join(generate_password() for _ in range(500))
    assert set(ATS_SAFE_SYMBOLS) <= set(joined)
    assert set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") <= set(joined)


def test_injected_rng_is_deterministic() -> None:
    first = generate_password(EMAIL, rng=random.Random(7))
    assert first == generate_password(EMAIL, rng=random.Random(7))
    assert first != generate_password(EMAIL, rng=random.Random(8))
    assert _policy_problems(first, EMAIL) == []


def test_a_broken_rng_fails_instead_of_looping_forever() -> None:
    class Constant(random.Random):
        def randint(self, a: int, b: int) -> int:
            return a

        def choice(self, seq: Any) -> Any:
            return seq[0]

        def shuffle(self, x: Any) -> None:
            return None

    with pytest.raises(AccountError):
        generate_password(EMAIL, rng=Constant())


# ------------------------------------------------------------------------------------------ normalisation


@pytest.mark.parametrize(
    "raw",
    [
        "acme.wd5.myworkdayjobs.com",
        " ACME.WD5.MyWorkdayJobs.com ",
        "www.acme.wd5.myworkdayjobs.com",
        "acme.wd5.myworkdayjobs.com:443",
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/x?y=1",
        "acme.wd5.myworkdayjobs.com.",
    ],
)
def test_host_forms_share_one_key(raw: str) -> None:
    assert normalize_tenant_host(raw) == HOST


def test_mock_hosts_stay_distinct_from_production_tenants() -> None:
    assert normalize_tenant_host("acme.wd5.myworkdayjobs.com.localhost:8123") == (
        "acme.wd5.myworkdayjobs.com.localhost"
    )
    assert normalize_tenant_host("http://www.acme.wd5.myworkdayjobs.com.localhost:1/x") == (
        "acme.wd5.myworkdayjobs.com.localhost"
    )
    assert normalize_tenant_host("localhost:8000") == "localhost"
    assert normalize_tenant_host("globex.wd1.myworkdayjobs.com") != HOST


@pytest.mark.parametrize("bad", ["", "   ", "a b.com", ".localhost", "https:///path"])
def test_bad_hosts_are_rejected(bad: str) -> None:
    with pytest.raises(AccountArgumentError):
        normalize_tenant_host(bad)


@pytest.mark.parametrize("bad", ["", "alex", "@example.test", "alex@", "a lex@example.test"])
def test_bad_emails_are_rejected(bad: str) -> None:
    with pytest.raises(AccountArgumentError):
        normalize_account_email(bad)


def test_argument_errors_are_account_errors_and_value_errors(
    manager: AccountManagerImpl,
) -> None:
    with pytest.raises(AccountError):
        manager.credentials_for("", EMAIL)
    with pytest.raises(ValueError):
        manager.credentials_for(HOST, "nope")


# ------------------------------------------------------------------------------------------ credentials_for


def test_first_call_generates_stores_and_records(
    manager: AccountManagerImpl, store: MemoryCredentialStore, repo: Repo
) -> None:
    acct: AccountManager = manager  # structural conformance to the contract
    creds = acct.credentials_for(HOST, EMAIL)
    assert creds.created is True
    assert (creds.host, creds.email) == (HOST, EMAIL)
    assert _policy_problems(secret(creds), EMAIL) == []
    assert store.snapshot() == {(f"autoapply:ats:{HOST}", EMAIL): secret(creds)}
    assert ats_service(HOST) == f"autoapply:ats:{HOST}"
    record = repo.get_ats_account(HOST, EMAIL)
    assert record is not None and record.verified is False and record.last_login_ok_at is None


def test_second_call_returns_the_same_password_without_writing(
    repo: Repo, fake_clock: FakeClock
) -> None:
    store = ScriptedStore()
    manager = AccountManagerImpl(repo, store)
    first = manager.credentials_for(HOST, EMAIL)
    manager.mark_verified(HOST, EMAIL)
    again = manager.credentials_for(HOST, EMAIL)
    assert again.created is False and secret(again) == secret(first)
    assert store.set_calls == 1
    record = repo.get_ats_account(HOST, EMAIL)
    assert record is not None and record.verified is True  # not reset by a later lookup


def test_lookup_is_case_and_form_insensitive(manager: AccountManagerImpl) -> None:
    first = manager.credentials_for(f"https://{HOST.upper()}:443/x", "  Alex.Rivera@Example.TEST ")
    again = manager.credentials_for(HOST, EMAIL)
    assert again.created is False and secret(again) == secret(first)


def test_each_tenant_and_email_gets_its_own_password(
    manager: AccountManagerImpl, store: MemoryCredentialStore
) -> None:
    creds = [
        manager.credentials_for(HOST, EMAIL),
        manager.credentials_for("globex.wd1.myworkdayjobs.com", EMAIL),
        manager.credentials_for(HOST, "other@example.test"),
        manager.credentials_for(f"{HOST}.localhost:9000", EMAIL),
    ]
    assert all(c.created for c in creds)
    assert len({secret(c) for c in creds}) == 4
    assert len(store.snapshot()) == 4


def test_existing_password_without_a_db_row_heals_the_row(
    repo: Repo, store: MemoryCredentialStore
) -> None:
    store.set_secret(ats_service(HOST), EMAIL, "Existing-Pass-1234!!xyz")
    creds = AccountManagerImpl(repo, store).credentials_for(HOST, EMAIL)
    assert creds.created is False and secret(creds) == "Existing-Pass-1234!!xyz"
    assert repo.get_ats_account(HOST, EMAIL) is not None


def test_stored_password_is_returned_verbatim(repo: Repo, store: MemoryCredentialStore) -> None:
    store.set_secret(ats_service(HOST), EMAIL, " odd pass ")
    assert secret(AccountManagerImpl(repo, store).credentials_for(HOST, EMAIL)) == " odd pass "


# ------------------------------------------------------------------------------------------ failures


def test_store_read_failure_is_an_account_error_and_nothing_is_generated(repo: Repo) -> None:
    store = ScriptedStore()
    store.fail_get = CredentialStoreError("the OS keyring is locked: unlock it")
    manager = AccountManagerImpl(repo, store)
    with pytest.raises(AccountError, match="keyring is locked"):
        manager.credentials_for(HOST, EMAIL)
    assert store.set_calls == 0
    assert repo.get_ats_account(HOST, EMAIL) is None


def test_store_write_failure_creates_no_account(repo: Repo) -> None:
    store = ScriptedStore()
    store.fail_set = CredentialStoreError("write refused")
    manager = AccountManagerImpl(repo, store)
    with pytest.raises(AccountError, match="could not save"):
        manager.credentials_for(HOST, EMAIL)
    assert repo.get_ats_account(HOST, EMAIL) is None
    assert store.snapshot() == {}


def test_a_store_that_silently_drops_writes_is_detected(repo: Repo) -> None:
    store = ScriptedStore()
    store.drop_writes = True
    manager = AccountManagerImpl(repo, store)
    with pytest.raises(AccountError, match="did not keep"):
        manager.credentials_for(HOST, EMAIL)
    assert repo.get_ats_account(HOST, EMAIL) is None


def test_hostile_store_exceptions_never_leak_the_password(repo: Repo) -> None:
    captured: list[str] = []

    class Hostile(MemoryCredentialStore):
        def set_secret(self, service: str, username: str, secret: str) -> None:
            captured.append(secret)
            raise RuntimeError(f"backend exploded while storing {secret}")

    with pytest.raises(AccountError) as info:
        AccountManagerImpl(repo, Hostile()).credentials_for(HOST, EMAIL)
    exc = info.value
    rendered = "".join(traceback.format_exception(exc)) + repr(exc) + str(exc.args)
    assert captured and captured[0] not in rendered
    assert "RuntimeError" in str(exc)
    assert exc.__cause__ is None and exc.__suppress_context__


def test_database_failure_is_not_fatal_and_is_logged(
    repo: Repo, store: MemoryCredentialStore, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db down")

    repo.upsert_ats_account = boom  # type: ignore[method-assign]
    caplog.set_level(logging.DEBUG)
    creds = AccountManagerImpl(repo, store).credentials_for(HOST, EMAIL)
    assert creds.created is True and store.snapshot()
    assert "RuntimeError" in caplog.text and secret(creds) not in caplog.text


# ------------------------------------------------------------------------------------------ metadata


def test_mark_verified_and_login_ok_update_only_metadata(
    manager: AccountManagerImpl, repo: Repo, store: MemoryCredentialStore, fake_clock: FakeClock
) -> None:
    manager.credentials_for(HOST, EMAIL)
    before = store.snapshot()
    fake_clock.advance(timedelta(hours=2))
    manager.mark_verified(f"https://{HOST}/x", EMAIL.upper())
    manager.record_login_ok(HOST, EMAIL)
    record = repo.get_ats_account(HOST, EMAIL)
    assert record is not None
    assert record.verified is True and record.last_login_ok_at == fake_clock.now()
    assert store.snapshot() == before


def test_metadata_calls_create_a_missing_row(manager: AccountManagerImpl, repo: Repo) -> None:
    manager.mark_verified("jobs.lever.co", EMAIL)
    manager.record_login_ok("boards.greenhouse.io", EMAIL)
    verified = repo.get_ats_account("jobs.lever.co", EMAIL)
    assert verified is not None and verified.verified is True
    login = repo.get_ats_account("boards.greenhouse.io", EMAIL)
    assert login is not None and login.last_login_ok_at is not None


# ------------------------------------------------------------------------------------------ replace / forget


def test_replace_password_generates_or_uses_the_given_one(
    manager: AccountManagerImpl, store: MemoryCredentialStore
) -> None:
    old = manager.credentials_for(HOST, EMAIL)
    generated = manager.replace_password(HOST, EMAIL)
    assert generated.created is False and secret(generated) != secret(old)
    assert _policy_problems(secret(generated), EMAIL) == []
    chosen = manager.replace_password(HOST, EMAIL, "Tenant-Limited-16!a")
    assert secret(chosen) == "Tenant-Limited-16!a"
    assert manager.credentials_for(HOST, EMAIL).created is False
    assert store.snapshot()[(ats_service(HOST), EMAIL)] == "Tenant-Limited-16!a"
    from pydantic import SecretStr

    assert secret(manager.replace_password(HOST, EMAIL, SecretStr("Another-Pass-77#z"))) == (
        "Another-Pass-77#z"
    )


@pytest.mark.parametrize("bad", ["", " lead", "trail ", "new\nline", "tab\there", "x" * 200])
def test_replace_password_rejects_unusable_passwords(
    manager: AccountManagerImpl, store: MemoryCredentialStore, bad: str
) -> None:
    old = manager.credentials_for(HOST, EMAIL)
    with pytest.raises(AccountArgumentError) as info:
        manager.replace_password(HOST, EMAIL, bad)
    assert bad.strip() == "" or bad not in str(info.value)
    assert store.snapshot()[(ats_service(HOST), EMAIL)] == secret(old)


def test_failed_replace_keeps_the_old_password(repo: Repo) -> None:
    store = ScriptedStore()
    manager = AccountManagerImpl(repo, store)
    old = manager.credentials_for(HOST, EMAIL)
    store.drop_writes = True
    with pytest.raises(AccountError):
        manager.replace_password(HOST, EMAIL, "Brand-New-Pass-99!")
    assert store.snapshot()[(ats_service(HOST), EMAIL)] == secret(old)


def test_failed_create_rolls_back_a_partial_write(repo: Repo) -> None:
    class Mangling(ScriptedStore):
        def get_secret(self, service: str, username: str) -> str | None:
            value = super().get_secret(service, username)
            return value + "x" if value and self.set_calls else value

    store = Mangling()
    with pytest.raises(AccountError):
        AccountManagerImpl(repo, store).credentials_for(HOST, EMAIL)
    assert store.snapshot() == {}


def test_forget_deletes_the_password_and_unverifies(
    manager: AccountManagerImpl, repo: Repo, store: MemoryCredentialStore
) -> None:
    first = manager.credentials_for(HOST, EMAIL)
    manager.mark_verified(HOST, EMAIL)
    manager.forget(HOST, EMAIL)
    assert store.snapshot() == {}
    record = repo.get_ats_account(HOST, EMAIL)
    assert record is not None and record.verified is False
    second = manager.credentials_for(HOST, EMAIL)
    assert second.created is True and secret(second) != secret(first)


def test_forget_unknown_account_is_a_no_op(manager: AccountManagerImpl, repo: Repo) -> None:
    manager.forget(HOST, EMAIL)
    assert repo.get_ats_account(HOST, EMAIL) is None


def test_forget_store_failure_is_an_account_error(repo: Repo) -> None:
    store = ScriptedStore()
    manager = AccountManagerImpl(repo, store)
    manager.credentials_for(HOST, EMAIL)
    store.fail_delete = CredentialStoreError("locked")
    with pytest.raises(AccountError, match="could not delete"):
        manager.forget(HOST, EMAIL)


# ------------------------------------------------------------------------------------------ concurrency


def test_concurrent_callers_never_generate_two_passwords(repo: Repo) -> None:
    store = ScriptedStore()
    store.get_delay = 0.02  # widen the check-then-write window
    manager = AccountManagerImpl(repo, store)
    start = threading.Barrier(12)
    results: list[AtsCredentials] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            start.wait(timeout=5)
            results.append(manager.credentials_for(HOST, EMAIL))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    assert len(results) == 12
    assert store.set_calls == 1
    assert len({secret(c) for c in results}) == 1
    assert sum(c.created for c in results) == 1


def test_different_tenants_do_not_block_each_other(repo: Repo) -> None:
    gate = threading.Event()
    entered = threading.Event()

    class Blocking(MemoryCredentialStore):
        def get_secret(self, service: str, username: str) -> str | None:
            if service.endswith("slow.example.test"):
                entered.set()
                gate.wait(timeout=10)
            return super().get_secret(service, username)

    manager = AccountManagerImpl(repo, Blocking())
    slow = threading.Thread(target=lambda: manager.credentials_for("slow.example.test", EMAIL))
    slow.start()
    assert entered.wait(timeout=5)
    assert manager.credentials_for("fast.example.test", EMAIL).created is True
    gate.set()
    slow.join(timeout=10)


# ------------------------------------------------------------------------------------------ no leaks


def _db_bytes(db_file: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(db_file.parent.glob(db_file.name + "*")))


def test_password_never_leaks_outside_the_credential_store(
    repo: Repo,
    store: MemoryCredentialStore,
    db_file: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    manager = AccountManagerImpl(repo, store)
    creds = manager.credentials_for(HOST, EMAIL)
    password = secret(creds)
    manager.credentials_for(HOST, EMAIL)
    manager.mark_verified(HOST, EMAIL)
    manager.record_login_ok(HOST, EMAIL)
    replaced = manager.replace_password(HOST, EMAIL)
    manager.forget(HOST, EMAIL)
    for other in (password, secret(replaced)):
        surfaces = [
            repr(creds),
            str(creds),
            repr(replaced),
            creds.model_dump_json(),
            str(creds.model_dump()),
            repr(manager),
            repr(store),
            repr(repo.get_ats_account(HOST, EMAIL)),
            caplog.text,
            "".join(str(r.args) + r.getMessage() for r in caplog.records),
        ]
        assert all(other not in s for s in surfaces)
        repo.db.connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert other.encode() not in _db_bytes(db_file)
    assert '"password":"**********"' in creds.model_dump_json()
