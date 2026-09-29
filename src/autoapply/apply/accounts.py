"""ATS tenant accounts (docs/SPEC.md 5.9).

One account per (tenant host, email). The generated password lives ONLY in the credential store, under the
service ``autoapply:ats:<host>`` and the username ``<email>``. The SQLite row (``Repo.upsert_ats_account``)
carries metadata (verified, last successful login) and never a secret. The password leaves this module only
inside ``AtsCredentials.password`` (a ``SecretStr``): it is never logged, never put in an exception message
and never kept in memory beyond the call that returns it.

Safety rules implemented here:

* A password is stored (and read back) BEFORE anything else happens. If the store cannot keep it the call fails
  with ``AccountError``: an account registered with a password nobody kept is unrecoverable.
* Concurrent callers for the same (host, email) are serialised, so two threads never generate two passwords.
* Host names keep the full tenant (``acme.wd5.myworkdayjobs.com``); port, ``www.`` and a trailing dot are
  dropped as ``normalize.host_of`` does. A mock host (``<tenant>.localhost``) keeps its ``.localhost`` marker so
  test runs can never share credentials with the real tenant (deliberate difference from ``host_of``).
* Metadata writes to the database are best effort (logged, never fatal): the flags are informational.
"""

from __future__ import annotations

import logging
import random
import re
import string
import threading
from secrets import SystemRandom
from urllib.parse import urlsplit

from pydantic import SecretStr

from autoapply.contracts import AccountManager, CredentialStore
from autoapply.db import Repo
from autoapply.models import AtsCredentials
from autoapply.normalize import host_of
from autoapply.secrets import CredentialStoreError, ats_service, redact

__all__ = [
    "ATS_SAFE_SYMBOLS",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "AccountArgumentError",
    "AccountError",
    "AccountManagerImpl",
    "generate_password",
    "normalize_account_email",
    "normalize_tenant_host",
]

log = logging.getLogger("autoapply.apply.accounts")

PASSWORD_MIN_LENGTH = 20
PASSWORD_MAX_LENGTH = 24
ATS_SAFE_SYMBOLS = "!@#$%&*?"  # other symbols are rejected by some tenants
_MIN_PER_CLASS = 2
_MAX_ATTEMPTS = 100
_MAX_SUPPLIED_LENGTH = 128
_CLASSES = (string.ascii_uppercase, string.ascii_lowercase, string.digits, ATS_SAFE_SYMBOLS)
_ALPHABET = "".join(_CLASSES)
_RUN_OF_THREE = re.compile(r"(.)\1\1")
_SYSTEM_RNG = SystemRandom()
_MOCK_SUFFIX = ".localhost"


class AccountError(Exception):
    """The account manager could not provide or record credentials (message is safe to show the user)."""


class AccountArgumentError(AccountError, ValueError):
    """A host, email or password argument is unusable (a caller bug, not a store failure)."""


# ------------------------------------------------------------------------------------------ normalisation


def _raw_hostname(text: str) -> str:
    raw = text if "://" in text else "https://" + text
    try:
        return (urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def normalize_tenant_host(host: str) -> str:
    """Canonical credential key for a tenant: lower-case host without port, ``www.`` or trailing dot.

    Accepts a bare host or a full URL. A ``<tenant>.localhost`` mock host keeps the ``.localhost`` suffix so it
    stays distinct from the production tenant. Raises ``AccountArgumentError`` for an empty/garbled host.
    """
    text = (host or "").strip()
    raw = _raw_hostname(text) if text else ""
    mock = raw.endswith(_MOCK_SUFFIX) and len(raw) > len(_MOCK_SUFFIX)
    tenant = host_of(raw[: -len(_MOCK_SUFFIX)] if mock else raw)
    if not tenant or re.search(r"[\s/\\@?#]", tenant):
        raise AccountArgumentError("an ATS tenant host (or URL) is required")
    return tenant + _MOCK_SUFFIX if mock else tenant


def normalize_account_email(email: str) -> str:
    """Lower-cased, trimmed email; raises ``AccountArgumentError`` unless it looks like ``local@domain``."""
    text = (email or "").strip().lower()
    local, at, domain = text.rpartition("@")
    if not (at and local and domain) or re.search(r"\s", text):
        raise AccountArgumentError("a valid account email address is required")
    return text


# ------------------------------------------------------------------------------------------ passwords


def _local_part(email: str) -> str:
    text = email.strip().lower()
    local, at, _domain = text.rpartition("@")
    return local if at else text


def _draw(rng: random.Random) -> str:
    length = rng.randint(PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH)
    chars = [rng.choice(pool) for pool in _CLASSES for _ in range(_MIN_PER_CLASS)]
    chars.extend(rng.choice(_ALPHABET) for _ in range(length - len(chars)))
    rng.shuffle(chars)
    return "".join(chars)


def generate_password(email: str = "", *, rng: random.Random | None = None) -> str:
    """A random ATS-safe password (20-24 chars).

    Contains at least 2 upper-case, 2 lower-case, 2 digits and 2 symbols from ``ATS_SAFE_SYMBOLS``; never
    whitespace, quotes, backslash or angle brackets; never a run of 3+ identical characters; never the local
    part of ``email`` (case-insensitive). Uses the OS CSPRNG unless ``rng`` is injected (tests only: a seeded
    ``random.Random`` is NOT secure). Raises ``AccountError`` if an injected rng cannot satisfy the policy.
    """
    source = rng if rng is not None else _SYSTEM_RNG
    local = _local_part(email)
    for _ in range(_MAX_ATTEMPTS):
        candidate = _draw(source)
        if not _RUN_OF_THREE.search(candidate) and not (local and local in candidate.lower()):
            return candidate
    raise AccountError("could not generate a password that satisfies the password policy")


def _check_supplied(password: str) -> None:
    if not password or password != password.strip():
        raise AccountArgumentError(
            "the new password must be non-empty without surrounding whitespace"
        )
    if len(password) > _MAX_SUPPLIED_LENGTH or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise AccountArgumentError("the new password is too long or contains control characters")


# ------------------------------------------------------------------------------------------ manager


class AccountManagerImpl(AccountManager):
    """``contracts.AccountManager`` on top of the credential store (secrets) and the repo (metadata).

    ``rng`` injects the random source for deterministic tests; production uses the OS CSPRNG.
    """

    def __init__(
        self, repo: Repo, store: CredentialStore, *, rng: random.Random | None = None
    ) -> None:
        self._repo = repo
        self._store = store
        self._rng = rng
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    # -- public API ----------------------------------------------------------------------------------
    def credentials_for(self, host: str, email: str) -> AtsCredentials:
        """Stored credentials for (host, email), or a freshly generated + stored password (``created=True``).

        Raises ``AccountError`` if the credential store fails or does not keep the new password (nothing is
        recorded in that case), ``AccountArgumentError`` for an unusable host/email.
        """
        tenant, address = self._key(host, email)
        with self._lock_for(tenant, address):
            existing = self._read(tenant, address)
            if existing is not None:
                self._ensure_record(tenant, address)
                return self._credentials(tenant, address, existing, created=False)
            password = generate_password(address, rng=self._rng)
            self._save(tenant, address, password, previous=None)
            self._record(tenant, address, verified=False)
            log.info("generated credentials for a new ATS account on %s", tenant)
            return self._credentials(tenant, address, password, created=True)

    def mark_verified(self, host: str, email: str) -> None:
        """Record that the account's email was verified (metadata only; creates the row if missing)."""
        tenant, address = self._key(host, email)
        self._record(tenant, address, verified=True)

    def record_login_ok(self, host: str, email: str) -> None:
        """Stamp a successful sign-in (metadata only; creates the row if missing)."""
        tenant, address = self._key(host, email)
        self._record(tenant, address, login_ok=True)

    def replace_password(
        self, host: str, email: str, new_password: str | SecretStr | None = None
    ) -> AtsCredentials:
        """Store a new password after a reset flow; ``None`` generates one. Returns the credentials.

        The store is written and read back first; on any failure the previous password is restored and
        ``AccountError`` is raised. Call this BEFORE submitting the new password to the tenant.
        """
        tenant, address = self._key(host, email)
        supplied: str | None = None
        if new_password is not None:
            supplied = (
                new_password.get_secret_value()
                if isinstance(new_password, SecretStr)
                else new_password
            )
            _check_supplied(supplied)
        with self._lock_for(tenant, address):
            previous = self._read(tenant, address)
            password = supplied or generate_password(address, rng=self._rng)
            self._save(tenant, address, password, previous=previous)
            self._ensure_record(tenant, address)
            return self._credentials(tenant, address, password, created=False)

    def forget(self, host: str, email: str) -> None:
        """Delete the stored password (no-op if none) and flag the metadata row unverified."""
        tenant, address = self._key(host, email)
        with self._lock_for(tenant, address):
            try:
                self._store.delete_secret(ats_service(tenant), address)
            except Exception as exc:
                raise self._failure("delete", tenant, exc) from None
            try:
                known = self._repo.get_ats_account(tenant, address) is not None
            except Exception as exc:
                self._log_metadata_failure(tenant, exc)
                return
            if known:
                self._record(tenant, address, verified=False)

    def __repr__(self) -> str:
        return "AccountManagerImpl()"

    # -- internals -----------------------------------------------------------------------------------
    @staticmethod
    def _key(host: str, email: str) -> tuple[str, str]:
        return normalize_tenant_host(host), normalize_account_email(email)

    def _lock_for(self, tenant: str, address: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((tenant, address), threading.Lock())

    @staticmethod
    def _credentials(tenant: str, address: str, password: str, *, created: bool) -> AtsCredentials:
        return AtsCredentials(
            host=tenant, email=address, password=SecretStr(password), created=created
        )

    @staticmethod
    def _failure(
        action: str, tenant: str, exc: Exception, secret: str | None = None
    ) -> AccountError:
        """User-facing error for a store failure. Never chained (a hostile store could echo the secret)."""
        detail = ""
        if isinstance(exc, CredentialStoreError):
            detail = ": " + redact(str(exc), secret).strip()[:200]
        return AccountError(
            f"the credential store could not {action} the ATS password for {tenant} "
            f"({type(exc).__name__}){detail}"
        )

    def _read(self, tenant: str, address: str) -> str | None:
        try:
            value = self._store.get_secret(ats_service(tenant), address)
        except Exception as exc:
            raise self._failure("read", tenant, exc) from None
        return value or None

    def _save(self, tenant: str, address: str, password: str, *, previous: str | None) -> None:
        """Write, read back, and roll back on any failure (delete a new entry / restore the old one)."""
        service = ats_service(tenant)
        try:
            self._store.set_secret(service, address, password)
            stored = self._store.get_secret(service, address)
        except Exception as exc:
            self._rollback(service, address, previous)
            raise self._failure("save", tenant, exc, password) from None
        if stored != password:
            self._rollback(service, address, previous)
            raise AccountError(
                f"the credential store did not keep the new ATS password for {tenant}; refusing to "
                "continue because an account with a lost password cannot be recovered"
            )

    def _rollback(self, service: str, address: str, previous: str | None) -> None:
        try:
            if previous is None:
                self._store.delete_secret(service, address)
            else:
                self._store.set_secret(service, address, previous)
        except Exception as exc:
            log.warning("could not roll back credential entry (%s)", type(exc).__name__)

    def _record(
        self,
        tenant: str,
        address: str,
        *,
        verified: bool | None = None,
        login_ok: bool = False,
    ) -> None:
        try:
            self._repo.upsert_ats_account(tenant, address, verified=verified, login_ok=login_ok)
        except Exception as exc:
            self._log_metadata_failure(tenant, exc)

    def _ensure_record(self, tenant: str, address: str) -> None:
        try:
            if self._repo.get_ats_account(tenant, address) is None:
                self._repo.upsert_ats_account(tenant, address)
        except Exception as exc:
            self._log_metadata_failure(tenant, exc)

    @staticmethod
    def _log_metadata_failure(tenant: str, exc: Exception) -> None:
        log.warning("could not record ATS account metadata for %s (%s)", tenant, type(exc).__name__)
