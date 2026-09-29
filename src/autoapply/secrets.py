"""Credential storage and OpenAI key resolution (docs/SPEC.md sections 1.7 and 5.2).

Rules enforced here:

* ``OPENAI_API_KEY`` from the environment always wins over a stored key. ``resolve_openai_key`` only reads:
  it never writes the key to ``config.json``, the database, logs, the environment or the credential store.
  The only writer is ``set_stored_openai_key``, which exists for an explicit user action ("save key").
* Every other secret (ATS passwords, the IMAP app password) lives only in the OS credential store: Windows
  Credential Manager through ``keyring``. Service names are ``autoapply:openai``, ``autoapply:imap`` and
  ``autoapply:ats:<host>``; the username slot is the account (``api_key``, the IMAP user, or the login email).
* Error messages, ``repr()`` output and log lines never contain a secret value.

Both stores implement ``contracts.CredentialStore``. Nothing here touches the OS keyring at import or
construction time, so hermetic tests can build a ``KeyringCredentialStore`` freely and inject a backend.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

import keyring
import keyring.errors

from autoapply.config import OPENAI_KEY_ENV
from autoapply.contracts import CredentialStore

__all__ = [
    "OPENAI_KEY_USERNAME",
    "SERVICE_ATS_PREFIX",
    "SERVICE_IMAP",
    "SERVICE_OPENAI",
    "CredentialStoreError",
    "KeyResolution",
    "KeyringCredentialStore",
    "MemoryCredentialStore",
    "ats_service",
    "clear_stored_openai_key",
    "key_is_well_formed",
    "mask",
    "normalize_key",
    "redact",
    "resolve_openai_key",
    "set_stored_openai_key",
]

SERVICE_OPENAI = "autoapply:openai"
SERVICE_IMAP = "autoapply:imap"
SERVICE_ATS_PREFIX = "autoapply:ats:"
OPENAI_KEY_USERNAME = "api_key"  # the username slot of the SERVICE_OPENAI entry

_R = TypeVar("_R")

# Quote pairs users typically wrap a pasted key in (ASCII and the "smart" variants chat apps produce).
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
# Byte-order mark and zero-width characters can ride along with a copy/paste; they are never part of a key.
_INVISIBLE_CHARS = "﻿​‌‍⁠"
_WELL_FORMED_KEY = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no whitespace
_KEY_SHAPED = re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_\-]{6,}")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{6,}")
_REDACTED = "[redacted]"


class CredentialStoreError(Exception):
    """The credential store could not complete an operation.

    The message is written for the user (what failed and what to do) and never contains a secret value.
    """


# ------------------------------------------------------------------------------------------ helpers


def ats_service(host: str) -> str:
    """Credential-store service name for an ATS tenant host: ``autoapply:ats:<host>`` (host lower-cased).

    Raises ``ValueError`` for a blank host, which would otherwise collide across every tenant.
    """
    cleaned = host.strip().lower()
    if not cleaned:
        raise ValueError("an ATS host is required to build a credential-store service name")
    return SERVICE_ATS_PREFIX + cleaned


def normalize_key(raw: str | None) -> str:
    """Return ``raw`` without surrounding whitespace/quotes (``""`` when nothing usable is left).

    Handles what really arrives through Windows environments and copy/paste: trailing ``\\r\\n``, a byte-order
    mark or zero-width characters, and one or more layers of ASCII or "smart" quotes. Whitespace inside the key
    is left alone (``key_is_well_formed`` rejects it).
    """
    if not raw:
        return ""
    text = raw
    for char in _INVISIBLE_CHARS:
        text = text.replace(char, "")
    text = text.strip()
    # Peel matching quote layers with indexes (linear time, however deeply they are nested), dropping
    # whitespace that sat between a quote and the key.
    start, end = 0, len(text)
    while end - start >= 2 and _QUOTE_PAIRS.get(text[start]) == text[end - 1]:
        start, end = start + 1, end - 1
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
    return text[start:end]


def key_is_well_formed(key: str) -> bool:
    """True for a non-empty run of printable ASCII without whitespace (all real API keys look like this).

    A key with a stray space or newline would make the HTTP layer raise an error that quotes the header value,
    so callers reject such keys up front instead of ever sending them.
    """
    return _WELL_FORMED_KEY.fullmatch(key) is not None


def mask(key: str | None) -> str:
    """Display-safe form of a key, e.g. ``sk-...abcd``; ``""`` for no key.

    Shows at most the last four characters, and only for keys long enough that this is a small fraction
    of the secret; anything shorter than 12 characters is fully hidden.
    """
    if not key:
        return ""
    if len(key) < 12:
        return "***"
    prefix = "sk-" if key.startswith("sk-") else ""
    return f"{prefix}...{key[-4:]}"


def redact(text: str, *secrets: str | None) -> str:
    """Replace every occurrence of the given secrets, key-shaped tokens and bearer tokens with ``[redacted]``.

    Belt and braces for text that is about to leave the process boundary (exception messages, log lines):
    third-party errors sometimes echo request headers or a partially masked key.
    """
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, _REDACTED)
    cleaned = _BEARER_TOKEN.sub(f"Bearer {_REDACTED}", cleaned)
    return _KEY_SHAPED.sub(_REDACTED, cleaned)


def _check_slot(service: str, username: str) -> None:
    if not service.strip() or not username.strip():
        raise CredentialStoreError(
            "a credential-store entry needs a non-empty service and username"
        )


# ------------------------------------------------------------------------------------------ stores


class MemoryCredentialStore(CredentialStore):
    """In-memory ``CredentialStore`` for tests and headless setups: nothing is persisted anywhere.

    Thread-safe. ``repr()`` shows only the entry count. It deliberately defines no ``__len__``: an empty store
    must stay truthy so ``store or default`` style code cannot silently swap it out.
    """

    def __init__(self, initial: Mapping[tuple[str, str], str] | None = None) -> None:
        self._data: dict[tuple[str, str], str] = dict(initial or {})
        self._lock = threading.Lock()

    def get_secret(self, service: str, username: str) -> str | None:
        _check_slot(service, username)
        with self._lock:
            return self._data.get((service, username))

    def set_secret(self, service: str, username: str, secret: str) -> None:
        _check_slot(service, username)
        if not secret:
            raise CredentialStoreError("refusing to store an empty secret")
        with self._lock:
            self._data[(service, username)] = secret

    def delete_secret(self, service: str, username: str) -> None:
        """Remove an entry; deleting one that does not exist is a no-op."""
        _check_slot(service, username)
        with self._lock:
            self._data.pop((service, username), None)

    def snapshot(self) -> dict[tuple[str, str], str]:
        """Copy of every ``(service, username) -> secret`` entry, for test assertions."""
        with self._lock:
            return dict(self._data)

    def __repr__(self) -> str:
        return f"MemoryCredentialStore(entries={len(self.snapshot())})"


class _KeyringBackend(Protocol):
    """The slice of ``keyring.backend.KeyringBackend`` this module needs (positional-only: names vary)."""

    def get_password(self, service: str, username: str, /) -> str | None: ...

    def set_password(self, service: str, username: str, password: str, /) -> None: ...

    def delete_password(self, service: str, username: str, /) -> None: ...


_NO_BACKEND_MESSAGE = (
    "no keyring backend available: the OS credential store cannot be used in this environment "
    "(Windows uses Credential Manager; on Linux install and unlock a Secret Service keyring such as "
    "gnome-keyring)"
)


class KeyringCredentialStore(CredentialStore):
    """``CredentialStore`` backed by the OS keyring (Windows Credential Manager) through ``keyring``.

    Every keyring failure (no backend, locked keyring, backend-specific errors such as an oversized secret) is
    wrapped in ``CredentialStoreError`` with an actionable message that never contains the secret. Reading a
    missing entry returns ``None``; deleting one is a no-op.

    ``backend`` injects a keyring-compatible object (tests). By default the process-wide keyring is looked up
    lazily on first use, never at construction.
    """

    def __init__(self, backend: _KeyringBackend | None = None) -> None:
        self._injected = backend

    def get_secret(self, service: str, username: str) -> str | None:
        _check_slot(service, username)
        return self._invoke("read", None, lambda kr: kr.get_password(service, username))

    def set_secret(self, service: str, username: str, secret: str) -> None:
        _check_slot(service, username)
        if not secret:
            raise CredentialStoreError("refusing to store an empty secret")
        self._invoke("write", secret, lambda kr: kr.set_password(service, username, secret))

    def delete_secret(self, service: str, username: str) -> None:
        _check_slot(service, username)

        def delete(kr: _KeyringBackend) -> None:
            # Backends disagree on what deleting a missing entry raises, so look first.
            if kr.get_password(service, username) is not None:
                kr.delete_password(service, username)

        self._invoke("delete", None, delete)

    def _backend(self) -> _KeyringBackend:
        if self._injected is not None:
            return self._injected
        try:
            return keyring.get_keyring()
        except Exception:
            raise CredentialStoreError(_NO_BACKEND_MESSAGE) from None

    def _invoke(self, action: str, secret: str | None, call: Callable[[_KeyringBackend], _R]) -> _R:
        try:
            return call(self._backend())
        except CredentialStoreError:
            raise
        # Backends raise arbitrary types (pywintypes.error, dbus errors, ...); all become CredentialStoreError.
        except Exception as exc:
            raise CredentialStoreError(_describe_failure(exc, action, secret)) from None

    def __repr__(self) -> str:
        return "KeyringCredentialStore()"


def _describe_failure(exc: Exception, action: str, secret: str | None) -> str:
    if isinstance(exc, keyring.errors.NoKeyringError):
        return _NO_BACKEND_MESSAGE
    if isinstance(exc, keyring.errors.KeyringLocked):
        return "the OS keyring is locked: unlock it (sign in to your desktop session) and try again"
    if isinstance(exc, keyring.errors.InitError):
        return "the OS keyring could not be initialised: check the keyring service is installed and running"
    detail = redact(str(exc), secret).strip()[:200]
    suffix = f": {detail}" if detail else ""
    return f"the OS credential store failed to {action} the entry ({type(exc).__name__}{suffix})"


# ------------------------------------------------------------------------------------------ OpenAI key


@dataclass(frozen=True, repr=False)
class KeyResolution:
    """Outcome of ``resolve_openai_key``.

    ``source`` is ``"env"`` or ``"credential_store"`` when a key was found, else ``None``. ``store_error``
    explains why the credential store could not be consulted (``None`` when it was fine or not used).
    ``repr()`` never shows the key; use ``masked`` for display.
    """

    key: str | None
    source: Literal["env", "credential_store"] | None
    store_error: str | None = None

    @property
    def present(self) -> bool:
        return self.key is not None

    @property
    def masked(self) -> str:
        return mask(self.key)

    def __repr__(self) -> str:
        key = "<set>" if self.key else "<none>"
        return f"KeyResolution(source={self.source!r}, key={key}, store_error={self.store_error!r})"


def resolve_openai_key(
    env: Mapping[str, str] | None = None, store: CredentialStore | None = None
) -> KeyResolution:
    """Find the OpenAI key: the environment variable first, then the credential store.

    ``env`` defaults to ``os.environ`` (read at call time). The value is stripped of surrounding whitespace and
    quotes; an empty value counts as absent. The credential store is consulted only when the environment has no
    usable key, and ``store=None`` means "no store". A failing store never raises: the resolution reports no key
    and carries the reason in ``store_error``. Read-only: this function never persists the key anywhere.
    """
    source_env = os.environ if env is None else env
    env_key = normalize_key(source_env.get(OPENAI_KEY_ENV))
    if env_key:
        return KeyResolution(key=env_key, source="env")
    if store is None:
        return KeyResolution(key=None, source=None)
    try:
        stored = store.get_secret(SERVICE_OPENAI, OPENAI_KEY_USERNAME)
    except CredentialStoreError as exc:
        return KeyResolution(key=None, source=None, store_error=str(exc))
    except Exception as exc:  # a broken OS keyring must degrade to "no key", not crash readiness
        return KeyResolution(
            key=None, source=None, store_error=f"credential store failed ({type(exc).__name__})"
        )
    stored_key = normalize_key(stored)
    if stored_key:
        return KeyResolution(key=stored_key, source="credential_store")
    return KeyResolution(key=None, source=None)


def set_stored_openai_key(store: CredentialStore, key: str) -> None:
    """Save the OpenAI key in the credential store. ONLY for an explicit user action ("save key").

    The key is normalised like an environment value. Raises ``ValueError`` (without echoing the value) for an
    empty or malformed key, and ``CredentialStoreError`` when the store refuses.
    """
    cleaned = normalize_key(key)
    if not cleaned:
        raise ValueError("the OpenAI API key is empty")
    if not key_is_well_formed(cleaned):
        raise ValueError("the OpenAI API key contains whitespace or non-ASCII characters")
    store.set_secret(SERVICE_OPENAI, OPENAI_KEY_USERNAME, cleaned)


def clear_stored_openai_key(store: CredentialStore) -> None:
    """Remove the stored OpenAI key (no-op when none is stored). Counterpart of ``set_stored_openai_key``."""
    store.delete_secret(SERVICE_OPENAI, OPENAI_KEY_USERNAME)
