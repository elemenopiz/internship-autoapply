"""secrets.py: credential stores, key normalisation/masking/redaction, OpenAI key resolution."""

from __future__ import annotations

import os
import threading
from typing import Any

import keyring
import keyring.backends.fail
import keyring.errors
import pytest

from autoapply.secrets import (
    OPENAI_KEY_USERNAME,
    SERVICE_ATS_PREFIX,
    SERVICE_IMAP,
    SERVICE_OPENAI,
    CredentialStoreError,
    KeyResolution,
    KeyringCredentialStore,
    MemoryCredentialStore,
    ats_service,
    clear_stored_openai_key,
    key_is_well_formed,
    mask,
    normalize_key,
    redact,
    resolve_openai_key,
    set_stored_openai_key,
)

KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"
OTHER_KEY = "sk-test-OTHERFICTIONAL9876543210zyxwvutsrqponmlkji"


class ExplodingStore:
    """A store that fails the test if anything is written or read when it should not be."""

    def __init__(self, *, allow_reads: bool = False) -> None:
        self.allow_reads = allow_reads
        self.reads = 0

    def get_secret(self, service: str, username: str) -> str | None:
        if not self.allow_reads:
            raise AssertionError("the credential store must not be consulted")
        self.reads += 1
        return None

    def set_secret(self, service: str, username: str, secret: str) -> None:
        raise AssertionError("resolving a key must never write to the credential store")

    def delete_secret(self, service: str, username: str) -> None:
        raise AssertionError("resolving a key must never delete from the credential store")


class InMemoryKeyring:
    """Keyring-compatible backend used to exercise KeyringCredentialStore without the OS keyring."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.deletes = 0

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.deletes += 1
        del self.data[(service, username)]


class RaisingKeyring:
    """Backend whose every operation raises the given exception."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_password(self, service: str, username: str) -> str | None:
        raise self.error

    def set_password(self, service: str, username: str, password: str) -> None:
        raise self.error

    def delete_password(self, service: str, username: str) -> None:
        raise self.error


# ----------------------------------------------------------------------------------------------- names


def test_service_names_match_the_spec() -> None:
    assert SERVICE_OPENAI == "autoapply:openai"
    assert SERVICE_IMAP == "autoapply:imap"
    assert SERVICE_ATS_PREFIX == "autoapply:ats:"
    assert ats_service("acme.wd5.myworkdayjobs.com") == "autoapply:ats:acme.wd5.myworkdayjobs.com"


def test_ats_service_lowercases_and_strips_the_host() -> None:
    assert (
        ats_service("  Acme.WD5.MyWorkdayJobs.com ") == "autoapply:ats:acme.wd5.myworkdayjobs.com"
    )


@pytest.mark.parametrize("host", ["", "   ", "\t"])
def test_ats_service_rejects_a_blank_host(host: str) -> None:
    with pytest.raises(ValueError, match="host"):
        ats_service(host)


# ----------------------------------------------------------------------------------------------- mask


def test_mask_shows_prefix_and_last_four_only() -> None:
    masked = mask("sk-proj-abcdefghijklmnop1234")
    assert masked == "sk-...1234"


def test_mask_of_a_non_sk_key_drops_the_prefix() -> None:
    assert mask("abcdefghijklmnopqrstuvwxyz") == "...wxyz"


def test_mask_never_leaks_the_middle_of_a_key() -> None:
    masked = mask(KEY)
    assert KEY not in masked
    assert KEY[4:-4] not in masked
    assert masked.endswith(KEY[-4:])


@pytest.mark.parametrize("short", ["sk-abc", "abcdefghijk", "x"])
def test_mask_fully_hides_keys_shorter_than_twelve_characters(short: str) -> None:
    assert mask(short) == "***"


def test_mask_boundary_at_twelve_characters() -> None:
    assert mask("sk-12345abcd") == "sk-...abcd"


@pytest.mark.parametrize("nothing", [None, ""])
def test_mask_of_no_key_is_empty(nothing: str | None) -> None:
    assert mask(nothing) == ""


# ----------------------------------------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    "raw",
    [
        KEY,
        f"  {KEY}  ",
        f'"{KEY}"',
        f"'{KEY}'",
        f'  "{KEY}"\r\n',
        f'""{KEY}""',
        f"“{KEY}”",
        f"‘{KEY}’",
        f"﻿{KEY}",
        f"{KEY}\r\n",
        f'" {KEY} "',
        f"{KEY[:10]}​{KEY[10:]}",
    ],
)
def test_normalize_key_strips_whitespace_quotes_and_invisible_characters(raw: str) -> None:
    assert normalize_key(raw) == KEY


@pytest.mark.parametrize("raw", [None, "", "   ", '""', "''", "﻿", '" "', "\r\n", "“”"])
def test_normalize_key_of_nothing_is_empty(raw: str | None) -> None:
    assert normalize_key(raw) == ""


def test_normalize_key_peels_any_depth_of_quotes_in_linear_time() -> None:
    import time

    deep = '"' * 40_000 + KEY + '"' * 40_000
    started = time.perf_counter()
    assert normalize_key(deep) == KEY
    assert normalize_key('"' * 100_001) == '"'  # an odd count leaves one unmatched quote
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"\" ' {KEY} ' \"", KEY),  # mixed quote layers with whitespace between them
        (f"“ '{KEY}' ”", KEY),
        (f"'\"{KEY}\"'", KEY),
        (f"'{KEY}\"", f"'{KEY}\""),  # mismatched pair is not a quote layer
        (f"\"{KEY}'", f"\"{KEY}'"),
    ],
)
def test_normalize_key_only_peels_matching_quote_pairs(raw: str, expected: str) -> None:
    assert normalize_key(raw) == expected


def test_normalize_key_leaves_unbalanced_quotes_alone() -> None:
    assert normalize_key('"sk-abc') == '"sk-abc'


def test_normalize_key_preserves_inner_whitespace() -> None:
    assert normalize_key("sk-a bc") == "sk-a bc"


def test_key_is_well_formed() -> None:
    assert key_is_well_formed(KEY)
    assert not key_is_well_formed("")
    assert not key_is_well_formed("sk-a bc")
    assert not key_is_well_formed("sk-abc\n")
    assert not key_is_well_formed("sk-éabc")


# ----------------------------------------------------------------------------------------------- redact


def test_redact_replaces_exact_secrets_everywhere() -> None:
    text = f"boom {KEY} and again {KEY}!"
    assert redact(text, KEY) == "boom [redacted] and again [redacted]!"


def test_redact_handles_several_secrets_and_ignores_empty_ones() -> None:
    assert (
        redact("a hunter2 b pw123 c", "hunter2", None, "", "pw123") == "a [redacted] b [redacted] c"
    )


def test_redact_catches_key_shaped_and_bearer_tokens_without_being_told() -> None:
    text = "Authorization: Bearer abcdef1234567890 failed for sk-proj-ABCDEF123456"
    cleaned = redact(text)
    assert "abcdef1234567890" not in cleaned
    assert "sk-proj-ABCDEF123456" not in cleaned


def test_redact_leaves_ordinary_text_alone() -> None:
    assert redact("nothing secret here", KEY) == "nothing secret here"


# ----------------------------------------------------------------------------------------------- memory store


def test_memory_store_round_trip_overwrite_and_delete() -> None:
    store = MemoryCredentialStore()
    assert store.get_secret("svc", "user") is None
    store.set_secret("svc", "user", "one")
    assert store.get_secret("svc", "user") == "one"
    store.set_secret("svc", "user", "two")
    assert store.get_secret("svc", "user") == "two"
    store.delete_secret("svc", "user")
    assert store.get_secret("svc", "user") is None


def test_memory_store_delete_of_a_missing_entry_is_a_no_op() -> None:
    MemoryCredentialStore().delete_secret("svc", "nobody")


def test_memory_store_isolates_entries_by_service_and_username() -> None:
    store = MemoryCredentialStore()
    store.set_secret(ats_service("a.example"), "alex@example.test", "pw-a")
    store.set_secret(ats_service("b.example"), "alex@example.test", "pw-b")
    store.set_secret(ats_service("a.example"), "other@example.test", "pw-c")
    assert store.get_secret(ats_service("a.example"), "alex@example.test") == "pw-a"
    assert store.get_secret(ats_service("b.example"), "alex@example.test") == "pw-b"
    assert store.get_secret(ats_service("a.example"), "other@example.test") == "pw-c"
    assert store.get_secret(ats_service("c.example"), "alex@example.test") is None


@pytest.mark.parametrize(("service", "username"), [("", "u"), ("s", ""), ("  ", "u"), ("s", "  ")])
def test_memory_store_rejects_blank_service_or_username(service: str, username: str) -> None:
    store = MemoryCredentialStore()
    with pytest.raises(CredentialStoreError):
        store.get_secret(service, username)
    with pytest.raises(CredentialStoreError):
        store.set_secret(service, username, "x")
    with pytest.raises(CredentialStoreError):
        store.delete_secret(service, username)


def test_memory_store_refuses_an_empty_secret() -> None:
    with pytest.raises(CredentialStoreError, match="empty"):
        MemoryCredentialStore().set_secret("svc", "user", "")


def test_memory_store_repr_shows_only_the_entry_count() -> None:
    store = MemoryCredentialStore()
    store.set_secret("svc", "user", "super-secret-value")
    text = f"{store!r} {store}"
    assert "super-secret-value" not in text
    assert "entries=1" in text


def test_memory_store_snapshot_is_a_copy_and_initial_mapping_is_copied() -> None:
    initial = {("svc", "user"): "one"}
    store = MemoryCredentialStore(initial)
    initial[("svc", "user")] = "mutated"
    snapshot = store.snapshot()
    snapshot[("svc", "user")] = "also mutated"
    assert store.get_secret("svc", "user") == "one"


def test_an_empty_memory_store_is_still_truthy() -> None:
    # `store or default` must never silently swap out an empty test store.
    assert bool(MemoryCredentialStore())


def test_memory_store_is_thread_safe() -> None:
    store = MemoryCredentialStore()

    def worker(index: int) -> None:
        for round_number in range(50):
            store.set_secret("svc", f"user-{index}", f"secret-{index}-{round_number}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.snapshot()) == 8
    assert all(store.get_secret("svc", f"user-{i}") == f"secret-{i}-49" for i in range(8))


# ----------------------------------------------------------------------------------------------- keyring store


def test_keyring_store_round_trip_through_an_injected_backend() -> None:
    backend = InMemoryKeyring()
    store = KeyringCredentialStore(backend)
    assert store.get_secret("autoapply:imap", "alex@example.test") is None
    store.set_secret("autoapply:imap", "alex@example.test", "app-password")
    assert backend.data == {("autoapply:imap", "alex@example.test"): "app-password"}
    assert store.get_secret("autoapply:imap", "alex@example.test") == "app-password"
    store.delete_secret("autoapply:imap", "alex@example.test")
    assert store.get_secret("autoapply:imap", "alex@example.test") is None


def test_keyring_store_delete_of_a_missing_entry_is_a_no_op() -> None:
    backend = InMemoryKeyring()
    KeyringCredentialStore(backend).delete_secret("svc", "user")
    assert backend.deletes == 0  # looked first; never asked the backend to delete what is not there


def test_keyring_store_refuses_an_empty_secret_without_touching_the_backend() -> None:
    backend = InMemoryKeyring()
    with pytest.raises(CredentialStoreError, match="empty"):
        KeyringCredentialStore(backend).set_secret("svc", "user", "")
    assert backend.data == {}


def test_keyring_store_rejects_blank_service_or_username() -> None:
    store = KeyringCredentialStore(InMemoryKeyring())
    with pytest.raises(CredentialStoreError):
        store.get_secret("", "user")
    with pytest.raises(CredentialStoreError):
        store.set_secret("svc", " ", "x")


def test_keyring_store_wraps_the_real_no_backend_error() -> None:
    store = KeyringCredentialStore(keyring.backends.fail.Keyring())
    for call in (
        lambda: store.get_secret("svc", "user"),
        lambda: store.set_secret("svc", "user", "x"),
        lambda: store.delete_secret("svc", "user"),
    ):
        with pytest.raises(CredentialStoreError, match="no keyring backend available") as info:
            call()
        assert info.value.__cause__ is None


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (keyring.errors.NoKeyringError("x"), "no keyring backend available"),
        (keyring.errors.KeyringLocked("x"), "locked"),
        (keyring.errors.InitError("x"), "could not be initialised"),
        (keyring.errors.PasswordSetError("quota"), "PasswordSetError"),
        (RuntimeError("dbus went away"), "RuntimeError"),
    ],
)
def test_keyring_store_maps_backend_errors_to_actionable_messages(
    error: Exception, fragment: str
) -> None:
    store = KeyringCredentialStore(RaisingKeyring(error))
    with pytest.raises(CredentialStoreError) as info:
        store.get_secret("svc", "user")
    assert fragment in str(info.value)


def test_keyring_store_never_puts_the_secret_into_an_error_message() -> None:
    secret = "correct-horse-battery-staple-fictional"
    store = KeyringCredentialStore(RaisingKeyring(RuntimeError(f"cannot store {secret} right now")))
    with pytest.raises(CredentialStoreError) as info:
        store.set_secret("svc", "user", secret)
    assert secret not in str(info.value)
    assert secret not in repr(info.value)
    assert info.value.__cause__ is None


def test_keyring_store_construction_never_touches_the_os_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> Any:
        raise AssertionError("the OS keyring must not be initialised at construction")

    monkeypatch.setattr(keyring, "get_keyring", boom)
    store = KeyringCredentialStore()
    assert repr(store) == "KeyringCredentialStore()"


def test_keyring_store_uses_the_process_keyring_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = InMemoryKeyring()
    lookups: list[int] = []

    def fake_get_keyring() -> InMemoryKeyring:
        lookups.append(1)
        return backend

    monkeypatch.setattr(keyring, "get_keyring", fake_get_keyring)
    store = KeyringCredentialStore()
    assert lookups == []
    store.set_secret("svc", "user", "pw")
    assert store.get_secret("svc", "user") == "pw"
    assert backend.data == {("svc", "user"): "pw"}
    assert lookups


def test_keyring_store_reports_a_keyring_that_cannot_be_initialised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> Any:
        raise ImportError("entry point exploded")

    monkeypatch.setattr(keyring, "get_keyring", boom)
    with pytest.raises(CredentialStoreError, match="no keyring backend available"):
        KeyringCredentialStore().get_secret("svc", "user")


# ----------------------------------------------------------------------------------------------- resolve


def test_env_key_is_used_and_reported_as_env() -> None:
    resolution = resolve_openai_key({"OPENAI_API_KEY": KEY})
    assert resolution.key == KEY
    assert resolution.source == "env"
    assert resolution.present
    assert resolution.store_error is None


def test_env_beats_the_credential_store() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, OTHER_KEY)
    resolution = resolve_openai_key({"OPENAI_API_KEY": KEY}, store)
    assert (resolution.key, resolution.source) == (KEY, "env")


def test_env_key_short_circuits_so_the_store_is_never_consulted() -> None:
    resolution = resolve_openai_key({"OPENAI_API_KEY": KEY}, ExplodingStore())
    assert resolution.source == "env"


def test_store_key_is_used_when_the_environment_has_none() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, OTHER_KEY)
    resolution = resolve_openai_key({}, store)
    assert (resolution.key, resolution.source) == (OTHER_KEY, "credential_store")


@pytest.mark.parametrize("empty", ["", "   ", '""', "''", "\r\n", "﻿"])
def test_an_empty_env_value_counts_as_absent_and_falls_back_to_the_store(empty: str) -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, OTHER_KEY)
    resolution = resolve_openai_key({"OPENAI_API_KEY": empty}, store)
    assert (resolution.key, resolution.source) == (OTHER_KEY, "credential_store")
    assert resolve_openai_key({"OPENAI_API_KEY": empty}).key is None


@pytest.mark.parametrize("raw", [f"  {KEY}  ", f'"{KEY}"', f"'{KEY}'", f"{KEY}\r\n", f'﻿"{KEY}"'])
def test_env_value_is_cleaned(raw: str) -> None:
    assert resolve_openai_key({"OPENAI_API_KEY": raw}).key == KEY


def test_no_env_no_store_means_no_key() -> None:
    resolution = resolve_openai_key({})
    assert (resolution.key, resolution.source, resolution.present) == (None, None, False)
    assert resolution.masked == ""


def test_an_empty_store_means_no_key() -> None:
    resolution = resolve_openai_key({}, MemoryCredentialStore())
    assert resolution.key is None
    assert resolution.store_error is None


def test_store_none_means_the_store_is_not_consulted() -> None:
    assert resolve_openai_key({}, None).key is None


def test_a_credential_store_error_degrades_to_no_key_with_a_reason() -> None:
    store = KeyringCredentialStore(keyring.backends.fail.Keyring())
    resolution = resolve_openai_key({}, store)
    assert resolution.key is None
    assert resolution.store_error is not None
    assert "no keyring backend available" in resolution.store_error


def test_an_arbitrary_store_failure_reports_only_the_exception_type() -> None:
    class BrokenStore(ExplodingStore):
        def get_secret(self, service: str, username: str) -> str | None:
            raise RuntimeError(f"leaky failure containing {KEY}")

    resolution = resolve_openai_key({}, BrokenStore())
    assert resolution.key is None
    assert resolution.store_error == "credential store failed (RuntimeError)"
    assert KEY not in repr(resolution)


def test_resolving_never_writes_the_key_to_the_store_or_the_environment() -> None:
    env = {"OPENAI_API_KEY": KEY}
    before_environ = dict(os.environ)
    resolve_openai_key(env, ExplodingStore(allow_reads=True))  # env wins: no read at all
    resolve_openai_key({}, ExplodingStore(allow_reads=True))  # reads, but any write would raise
    assert env == {"OPENAI_API_KEY": KEY}
    assert dict(os.environ) == before_environ


def test_env_defaults_to_the_live_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", f' "{KEY}" ')
    assert resolve_openai_key().key == KEY
    monkeypatch.delenv("OPENAI_API_KEY")
    assert resolve_openai_key().key is None


def test_key_resolution_repr_and_str_never_contain_the_key() -> None:
    for resolution in (
        resolve_openai_key({"OPENAI_API_KEY": KEY}),
        KeyResolution(key=KEY, source="credential_store"),
    ):
        text = f"{resolution!r} | {resolution}"
        assert KEY not in text
        assert KEY[8:-4] not in text
        assert "<set>" in text


def test_key_resolution_masked_is_display_safe() -> None:
    resolution = resolve_openai_key({"OPENAI_API_KEY": KEY})
    assert resolution.masked == mask(KEY)
    assert KEY not in resolution.masked


# ----------------------------------------------------------------------------------------------- save / clear


def test_set_stored_openai_key_uses_the_documented_service_and_username() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, KEY)
    assert store.snapshot() == {(SERVICE_OPENAI, OPENAI_KEY_USERNAME): KEY}
    assert (SERVICE_OPENAI, OPENAI_KEY_USERNAME) == ("autoapply:openai", "api_key")


def test_set_stored_openai_key_normalises_the_value() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, f'  "{KEY}"\r\n')
    assert store.get_secret(SERVICE_OPENAI, OPENAI_KEY_USERNAME) == KEY


@pytest.mark.parametrize("bad", ["", "   ", '""'])
def test_set_stored_openai_key_rejects_an_empty_key(bad: str) -> None:
    store = MemoryCredentialStore()
    with pytest.raises(ValueError, match="empty"):
        set_stored_openai_key(store, bad)
    assert store.snapshot() == {}


def test_set_stored_openai_key_rejects_a_malformed_key_without_echoing_it() -> None:
    bad = "sk-test-FICTIONAL with a space inside"
    store = MemoryCredentialStore()
    with pytest.raises(ValueError, match="whitespace") as info:
        set_stored_openai_key(store, bad)
    assert bad not in str(info.value)
    assert store.snapshot() == {}


def test_set_stored_openai_key_propagates_store_failures() -> None:
    store = KeyringCredentialStore(keyring.backends.fail.Keyring())
    with pytest.raises(CredentialStoreError):
        set_stored_openai_key(store, KEY)


def test_a_saved_key_resolves_from_the_store_and_env_still_wins() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, OTHER_KEY)
    assert resolve_openai_key({}, store).source == "credential_store"
    assert resolve_openai_key({"OPENAI_API_KEY": KEY}, store).key == KEY


def test_clear_stored_openai_key_removes_it_and_is_idempotent() -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, KEY)
    clear_stored_openai_key(store)
    clear_stored_openai_key(store)
    assert resolve_openai_key({}, store).key is None
