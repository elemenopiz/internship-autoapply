"""Opportunity sources: provider discovery and the ingest entry point (docs/SPEC.md section 5.3).

Every module in this package may expose ``PROVIDER`` or ``PROVIDERS`` (``contracts.OpportunityProvider``
objects). ``discover_providers()`` finds them with ``pkgutil`` so a new source needs no registration, and
``ingest_all(ctx)`` runs the enabled ones, isolating each failure, and de-duplicates across sources.

A provider may additionally offer ``fetch_report(ctx)`` returning an object with ``opportunities``,
``warnings`` and ``rejection_summary()`` (see ``ProviderReport``); ``ingest_all`` then also surfaces why
records were dropped (the workbook provider does this).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from autoapply.contracts import OpportunityProvider, SourceContext
from autoapply.models import Opportunity
from autoapply.sources.dedupe import dedupe as dedupe_opportunities
from autoapply.sources.workbook import (
    RejectReason,
    WorkbookError,
    WorkbookProvider,
    WorkbookReport,
    detect_ats,
    inspect_workbook,
)

__all__ = [
    "IngestResult",
    "ProviderReport",
    "RejectReason",
    "WorkbookError",
    "WorkbookProvider",
    "WorkbookReport",
    "detect_ats",
    "discover_providers",
    "ingest_all",
    "inspect_workbook",
]

_LOG = logging.getLogger("autoapply.sources")
_PRIORITY = ("workbook", "boards", "linkedin", "indeed")  # discovery order: curated sources first
_NOT_PROVIDERS = frozenset({"dedupe"})
_MESSAGE_LIMIT = 500


class ProviderReport(Protocol):
    """Optional richer result of a provider (``fetch_report``): its records plus what it dropped and why."""

    opportunities: list[Opportunity]
    warnings: list[str]

    def rejection_summary(self) -> dict[str, list[str]]:
        """``{reason: [labels of dropped records]}``."""
        ...


@dataclass
class IngestResult:
    """What ``ingest_all`` produced.

    ``opportunities`` is the de-duplicated union. ``per_provider_counts`` is what each ENABLED provider returned
    before de-duplication (0 for one that failed). ``errors`` are human-readable lines ("boards: TimeoutError:
    ...") ready for ``RunReport.errors``; ``provider_errors`` has the same information keyed by provider.
    """

    opportunities: list[Opportunity] = field(default_factory=list)
    per_provider_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    provider_errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rejections: dict[str, dict[str, list[str]]] = field(
        default_factory=dict
    )  # provider -> reason -> labels
    duplicates_removed: int = 0

    @property
    def raw_count(self) -> int:
        """Records returned by all providers before de-duplication."""
        return sum(self.per_provider_counts.values())

    @property
    def ok(self) -> bool:
        return not self.errors

    def fail(self, provider: str, message: str) -> None:
        text = message if len(message) <= _MESSAGE_LIMIT else message[: _MESSAGE_LIMIT - 1] + "…"
        self.provider_errors[provider] = text
        self.errors.append(f"{provider}: {text}")


def _module_names() -> list[str]:
    names = [
        m.name
        for m in pkgutil.iter_modules(__path__)
        if not m.name.startswith("_") and m.name not in _NOT_PROVIDERS
    ]
    return sorted(
        names, key=lambda n: (_PRIORITY.index(n) if n in _PRIORITY else len(_PRIORITY), n)
    )


def _declared(module: object) -> list[object]:
    found: list[object] = []
    for attr in ("PROVIDER", "PROVIDERS"):
        value = getattr(module, attr, None)
        if value is None:
            continue
        found.extend(value if isinstance(value, list | tuple | set | frozenset) else [value])
    return found


def _looks_like_provider(obj: object) -> bool:
    return (
        isinstance(getattr(obj, "name", None), str)
        and callable(getattr(obj, "enabled", None))
        and callable(getattr(obj, "fetch", None))
    )


def _discover() -> tuple[list[OpportunityProvider], list[tuple[str, str]]]:
    providers: list[OpportunityProvider] = []
    problems: list[tuple[str, str]] = []
    seen: set[int] = set()
    for name in _module_names():
        try:
            module = importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # one broken module must not take the other sources down
            _LOG.warning("sources: cannot import %s: %s: %s", name, type(exc).__name__, exc)
            problems.append((name, f"import failed: {type(exc).__name__}: {exc}"))
            continue
        for obj in _declared(module):
            if not _looks_like_provider(obj):
                problems.append((name, f"ignored invalid provider object {obj!r}"))
            elif id(obj) not in seen:
                seen.add(id(obj))
                providers.append(obj)  # type: ignore[arg-type]
    return providers, problems


def discover_providers() -> list[OpportunityProvider]:
    """Every provider exposed as ``PROVIDER`` / ``PROVIDERS`` by a module of this package (pkgutil).

    Order: workbook, boards, linkedin, indeed, then any other module alphabetically. A module that fails to
    import is logged and skipped (``ingest_all`` also reports it in ``IngestResult.errors``); the ``dedupe``
    module and names starting with ``_`` are ignored.
    """
    return _discover()[0]


def _unique_name(provider: object, taken: set[str]) -> str:
    base = str(getattr(provider, "name", "") or type(provider).__name__)
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}#{n}"
    taken.add(name)
    return name


def _collect(
    provider: OpportunityProvider, ctx: SourceContext, result: IngestResult, name: str
) -> list[Opportunity]:
    """Run one provider (preferring ``fetch_report``) and validate what it returned."""
    reporter = getattr(provider, "fetch_report", None)
    if callable(reporter):
        report: ProviderReport = reporter(ctx)
        fetched = report.opportunities
        if summary := report.rejection_summary():
            result.rejections[name] = summary
        result.warnings.extend(f"{name}: {w}" for w in report.warnings)
    else:
        fetched = provider.fetch(ctx)
    if fetched is None:
        raise TypeError("fetch() returned None instead of a list of opportunities")
    valid = [o for o in fetched if isinstance(o, Opportunity)]
    if len(valid) != len(fetched):
        result.warnings.append(
            f"{name}: ignored {len(fetched) - len(valid)} record(s) that are not Opportunity"
        )
    return valid


def ingest_all(
    ctx: SourceContext, providers: Sequence[OpportunityProvider] | None = None
) -> IngestResult:
    """Run every enabled provider, isolate failures, de-duplicate across sources.

    ``providers`` defaults to ``discover_providers()``. A provider whose ``enabled`` or ``fetch`` raises (or
    whose module cannot be imported) is recorded in ``errors`` / ``provider_errors`` and the rest still run.
    Never raises for a provider problem.
    """
    result = IngestResult()
    if providers is None:
        found, problems = _discover()
        providers = found
        for name, message in problems:
            result.fail(name, message)
    raw: list[Opportunity] = []
    taken: set[str] = set()
    for provider in providers:
        name = _unique_name(provider, taken)
        try:
            if not provider.enabled(ctx.config):
                ctx.log.debug("ingest: provider %s is disabled", name)
                continue
            records = _collect(provider, ctx, result, name)
        except Exception as exc:  # isolate: one failing source must not stop the run
            ctx.log.warning("ingest: provider %s failed: %s: %s", name, type(exc).__name__, exc)
            result.fail(name, f"{type(exc).__name__}: {exc}")
            result.per_provider_counts[name] = 0
            continue
        ctx.log.info("ingest: provider %s returned %d record(s)", name, len(records))
        result.per_provider_counts[name] = len(records)
        raw.extend(records)
    result.opportunities = dedupe_opportunities(raw)
    result.duplicates_removed = len(raw) - len(result.opportunities)
    return result
