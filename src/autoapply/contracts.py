"""Interfaces between modules. CONTRACT FILE: owned by the orchestrator.

Every cross-module dependency is expressed here as a Protocol so modules can be built and tested in
parallel, and so tests can inject fakes. Implementations live in their own modules (see docs/SPEC.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel

from autoapply.clock import Clock
from autoapply.config import AppConfig, AppPaths
from autoapply.models import (
    ATS,
    AnswerDecision,
    ApplyResult,
    AtsCredentials,
    FormQuestion,
    Opportunity,
    Profile,
    TailoredDocs,
)

if TYPE_CHECKING:
    import httpx
    from playwright.sync_api import BrowserContext, Page

T = TypeVar("T", bound=BaseModel)


# ------------------------------------------------------------------------------------------------ LLM


class LLMError(Exception):
    """Any failure to obtain a usable LLM response: missing key, network, quota, refusal, bad schema.

    RULE: the LLM is an enhancer, never a single point of failure. Every call site must catch ``LLMError``
    and use a deterministic fallback or return a defined failure outcome. A run must never crash on it.
    """


class LLMClient(Protocol):
    """Provider-agnostic LLM access. ``purpose`` is a short stable tag ("tailor_resume", "classify_question",
    "map_form_fields", "cover_letter", "kb_from_resume", "free_text_answer") used for routing, logging, budgets
    and by FakeLLM to select a scripted response.
    """

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> T:
        """Return an instance of ``schema``. Raise ``LLMError`` on any failure (including invalid output)."""
        ...

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str: ...


# ------------------------------------------------------------------------------------------------ secrets


class CredentialStore(Protocol):
    """OS credential store abstraction (Windows Credential Manager via ``keyring``; in-memory for tests)."""

    def get_secret(self, service: str, username: str) -> str | None: ...

    def set_secret(self, service: str, username: str, secret: str) -> None: ...

    def delete_secret(self, service: str, username: str) -> None: ...


# ------------------------------------------------------------------------------------------------ discovery


@dataclass
class SourceContext:
    config: AppConfig
    paths: AppPaths
    clock: Clock
    http: httpx.Client | None = (
        None  # shared client for public JSON APIs; tests inject a mock transport
    )
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("autoapply.sources"))
    browser_context_factory: object | None = (
        None  # provided by apply.browser for browser-based sources
    )


class OpportunityProvider(Protocol):
    """A discovery source. Modules under ``autoapply.sources`` expose ``PROVIDER`` (or ``PROVIDERS``)."""

    name: str

    def enabled(self, config: AppConfig) -> bool: ...

    def fetch(self, ctx: SourceContext) -> list[Opportunity]:
        """Return normalised, UN-filtered-by-score opportunities. Must not raise for a single bad record;
        skip and log it. May raise for a source-wide failure (the caller isolates providers)."""
        ...


# ------------------------------------------------------------------------------------------------ applying


@dataclass
class Trace:
    """Step log attached to every attempt. Secrets registered via ``add_secret`` are masked."""

    steps: list[str] = field(default_factory=list)
    secrets: set[str] = field(default_factory=set)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("autoapply.apply"))

    def add_secret(self, value: str) -> None:
        if value:
            self.secrets.add(value)

    def step(self, message: str, **fields: object) -> None:
        line = message
        if fields:
            line += " " + " ".join(f"{k}={v!r}" for k, v in fields.items())
        for secret in self.secrets:
            line = line.replace(secret, "***")
        self.steps.append(line)
        self.logger.info(line)


class AnswerEngine(Protocol):
    """Decides the answer to a form question. NEVER guesses factual/legal answers (docs/SPEC.md section 6)."""

    def answer(self, question: FormQuestion, *, opportunity: Opportunity) -> AnswerDecision:
        """For choice questions ``value`` is an exact member of ``question.options``."""
        ...


class AccountManager(Protocol):
    """Per-ATS-tenant accounts. Passwords are generated once and kept ONLY in the credential store."""

    def credentials_for(self, host: str, email: str) -> AtsCredentials:
        """Return existing credentials for (host, email) or generate+store new ones (``created=True``)."""
        ...

    def mark_verified(self, host: str, email: str) -> None: ...

    def record_login_ok(self, host: str, email: str) -> None: ...


class EmailVerifier(Protocol):
    """Reads verification mails. Production: IMAP with the user's app password. Tests: mock mailbox."""

    def wait_for_link(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None:
        """Return the first verification URL found in a matching, recent mail; None on timeout."""
        ...

    def wait_for_code(
        self,
        *,
        to_address: str,
        subject_contains: str | None = None,
        sender_contains: str | None = None,
        timeout_s: int = 120,
    ) -> str | None: ...


@dataclass
class ApplyContext:
    """Everything an adapter needs. The framework has ALREADY navigated ``page`` to the posting/apply URL."""

    page: Page
    opportunity: Opportunity
    profile: Profile
    docs: TailoredDocs
    answers: AnswerEngine
    accounts: AccountManager
    config: AppConfig
    clock: Clock
    trace: Trace
    artifacts_dir: Path
    dry_run: bool = False  # fill everything, then STOP before the irreversible final submit
    email: EmailVerifier | None = None
    llm: LLMClient | None = None
    deadline_epoch_s: float | None = (
        None  # wall-clock budget for the attempt (time.monotonic based)
    )


class ApplyAdapter(Protocol):
    """One ATS. Modules under ``autoapply.apply.adapters`` expose ``ADAPTER`` (an instance)."""

    ats: ATS
    name: str

    def matches_url(self, url: str) -> float:
        """0..1 confidence from the URL alone (no I/O)."""
        ...

    def matches_page(self, page: Page) -> float:
        """0..1 confidence from the loaded DOM (used after redirects / for embedded boards)."""
        ...

    def apply(self, ctx: ApplyContext) -> ApplyResult:
        """Complete the application. Must never raise for expected conditions: return an ApplyResult with
        NEEDS_MANUAL + Reason. In ``dry_run`` stop before the final submit and return DRY_RUN_OK."""
        ...


class BrowserProvider(Protocol):
    """Hands out Playwright browser contexts (persistent profile under data/browser_profile)."""

    def open_context(self) -> BrowserContext: ...
