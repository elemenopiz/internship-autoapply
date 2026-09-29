"""Readiness gate: may the applier run? (docs/SPEC.md section 5.2 and rule 1.6).

``check_readiness`` answers with a report instead of raising: a bad configuration is the expected input. It never
prints, logs or stores a secret; the OpenAI key is only checked for presence (environment first, then the optional
credential store). Issues come back in a stable order: profile fields (in ``REQUIRED_PROFILE_FIELDS`` order), resume,
OpenAI key, discovery sources, attestation.

Required for a run (the effective mode is ``mode`` if given, else ``config.mode``):

* every ``REQUIRED_PROFILE_FIELDS`` entry filled in and well formed (always);
* at least one usable discovery source (always): a workbook path that exists, board tokens for an enabled
  Greenhouse/Lever/Ashby platform, or an enabled LinkedIn/Indeed browser platform;
* a resume PDF (``resolve_resume_path``) and an OpenAI key, except in ``discover_only`` mode, which never tailors
  documents or opens a form;
* ``apply.attestations_authorized`` in ``full_auto`` mode only.

Source rules in detail: a set-but-missing ``workbook.path`` is reported as ``workbook_missing`` when the workbook
platform is on, an unset path only when nothing else is usable, and ``no_source`` whenever nothing at all is usable.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoapply.config import AppConfig, AppPaths, resolve_resume_path
from autoapply.contracts import CredentialStore
from autoapply.models import REQUIRED_PROFILE_FIELDS, Profile, RunMode
from autoapply.secrets import resolve_openai_key

__all__ = [
    "ReadinessCode",
    "ReadinessError",
    "ReadinessIssue",
    "ReadinessReport",
    "check_readiness",
    "ensure_ready_or_raise",
    "format_report",
]

_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+")
_YEAR_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_MIN_PHONE_DIGITS = 10
_WORKBOOK_SUFFIXES = frozenset({".xlsx", ".xlsm"})

_FIELD_LABELS: dict[str, str] = {
    "first_name": "first name",
    "last_name": "last name",
    "email": "email address",
    "phone": "phone number",
    "address_line1": "street address",
    "city": "city",
    "state": "state or region",
    "postal_code": "postal code",
    "country": "country",
    "school": "school",
    "degree": "degree",
    "major": "major",
    "graduation_date": "graduation date, YYYY-MM",
    "authorized_to_work_us": "authorized to work in the US, yes or no",
    "requires_sponsorship": "requires visa sponsorship now or later, yes or no",
}


class ReadinessCode(StrEnum):
    """Every ``ReadinessIssue.code`` this module can produce."""

    PROFILE_FIELD_MISSING = "profile_field_missing"
    PROFILE_FIELD_INVALID = "profile_field_invalid"
    RESUME_MISSING = "resume_missing"
    OPENAI_KEY_MISSING = "openai_key_missing"
    NO_SOURCE = "no_source"
    WORKBOOK_MISSING = "workbook_missing"
    ATTESTATION_NOT_AUTHORIZED = "attestation_not_authorized"


@dataclass(frozen=True)
class ReadinessIssue:
    """One thing standing between the user and a first run.

    ``code`` is a ``ReadinessCode`` value. ``field`` names what to fix: the ``Profile`` field name for profile
    issues (``first_name``), else a config path or name (``workbook.path``, ``platforms``,
    ``apply.attestations_authorized``, ``resume``, ``OPENAI_API_KEY``).
    """

    code: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class ReadinessReport:
    """Result of ``check_readiness``: ``ok`` is true exactly when there are no issues."""

    ok: bool
    issues: list[ReadinessIssue] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        """The distinct issue codes, in order of first appearance."""
        return list(dict.fromkeys(issue.code for issue in self.issues))

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}

    def to_json(self, *, indent: int | None = 2) -> str:
        """JSON text of ``to_dict()``. ASCII only, so any Windows console or pipe encoding can carry it."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=True)


class ReadinessError(Exception):
    """Raised by ``ensure_ready_or_raise``; the message lists every issue and ``report`` holds them."""

    def __init__(self, report: ReadinessReport) -> None:
        super().__init__(format_report(report))
        self.report = report

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.report,))


# ------------------------------------------------------------------------------------------ public API


def check_readiness(
    config: AppConfig,
    paths: AppPaths,
    env: Mapping[str, str] | None = None,
    store: CredentialStore | None = None,
    *,
    mode: RunMode | None = None,
) -> ReadinessReport:
    """Report everything that must be fixed before a run; never raises for a bad configuration.

    ``env`` defaults to ``os.environ``; ``store=None`` means the credential store is not consulted for the OpenAI
    key. ``mode`` overrides ``config.mode`` for callers that run in a mode other than the configured one.
    """
    effective = mode if mode is not None else config.mode
    source_env = os.environ if env is None else env
    issues = _profile_issues(config.profile)
    if effective is not RunMode.DISCOVER_ONLY:
        issues += _resume_issues(config, paths)
        issues += _openai_key_issues(source_env, store)
    issues += _source_issues(config)
    if effective is RunMode.FULL_AUTO:
        issues += _attestation_issues(config)
    return ReadinessReport(ok=not issues, issues=issues)


def ensure_ready_or_raise(
    config: AppConfig,
    paths: AppPaths,
    env: Mapping[str, str] | None = None,
    store: CredentialStore | None = None,
    *,
    mode: RunMode | None = None,
) -> ReadinessReport:
    """Return the (clean) report, or raise ``ReadinessError`` listing every issue."""
    report = check_readiness(config, paths, env, store, mode=mode)
    if not report.ok:
        raise ReadinessError(report)
    return report


def format_report(report: ReadinessReport) -> str:
    """Human-readable, ASCII-only text of a report (used by the CLI and the PowerShell wrapper)."""
    if report.ok and not report.issues:
        return "Ready: all readiness checks passed."
    lines = [f"NOT READY: {len(report.issues)} issue(s) must be fixed before the applier can run."]
    lines += [
        f"  {number}. [{issue.code}] {issue.field}: {issue.message}"
        for number, issue in enumerate(report.issues, start=1)
    ]
    lines.append(
        "Fix these on the dashboard (Profile, Resume and Search profile pages) or in config.json, "
        "then check again."
    )
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ checks


def _issue(code: ReadinessCode, field_name: str, message: str) -> ReadinessIssue:
    return ReadinessIssue(code=code.value, field=field_name, message=message)


def _is_blank(value: object) -> bool:
    """None is blank; so is text with nothing but whitespace. ``False`` and ``0`` are real answers."""
    return value is None or (isinstance(value, str) and not value.strip())


def _profile_issues(profile: Profile) -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    for name in REQUIRED_PROFILE_FIELDS:
        value = getattr(profile, name, None)
        label = _FIELD_LABELS.get(name, name.replace("_", " "))
        if _is_blank(value):
            issues.append(
                _issue(
                    ReadinessCode.PROFILE_FIELD_MISSING,
                    name,
                    f"Profile field '{name}' ({label}) is required. Fill it in on the dashboard "
                    f"Profile page or in config.json under profile.{name}.",
                )
            )
            continue
        problem = _invalid_reason(profile, name, value)
        if problem:
            issues.append(_issue(ReadinessCode.PROFILE_FIELD_INVALID, name, problem))
    return issues


def _invalid_reason(profile: Profile, name: str, value: object) -> str | None:
    """Why a filled-in field is unusable (without echoing the value), or ``None`` when it is fine."""
    if name == "email" and not _EMAIL.fullmatch(str(value).strip()):
        return (
            "Profile field 'email' does not look like an email address (expected name@domain.tld)."
        )
    if name == "phone" and len(profile.phone_digits) < _MIN_PHONE_DIGITS:
        return (
            f"Profile field 'phone' needs at least {_MIN_PHONE_DIGITS} digits "
            f"(found {len(profile.phone_digits)})."
        )
    if name == "graduation_date" and not _YEAR_MONTH.fullmatch(str(value).strip()):
        return "Profile field 'graduation_date' must be a year and month as YYYY-MM, for example 2028-05."
    return None


def _resume_issues(config: AppConfig, paths: AppPaths) -> list[ReadinessIssue]:
    try:
        resolved = resolve_resume_path(config, paths)
    except (OSError, RuntimeError, ValueError):
        resolved = None
    if resolved is not None:
        return []
    configured = config.profile.fallback_resume_path.strip()
    if configured:
        message = (
            f"profile.fallback_resume_path points to '{configured}', which is not an existing .pdf file, "
            f"and no uploaded resume exists at '{paths.resume_file}'."
        )
    else:
        message = (
            "No resume PDF found. Upload one on the dashboard Resume page (it is saved as "
            f"'{paths.resume_file}') or set profile.fallback_resume_path in config.json to an existing "
            ".pdf file."
        )
    return [_issue(ReadinessCode.RESUME_MISSING, "resume", message)]


def _openai_key_issues(
    env: Mapping[str, str], store: CredentialStore | None
) -> list[ReadinessIssue]:
    resolution = resolve_openai_key(env, store)
    if resolution.present:
        return []
    message = (
        "No OpenAI API key found. Set the OPENAI_API_KEY environment variable (run set_openai_key.ps1) "
        "or save one with 'autoapply set-key'."
    )
    if resolution.store_error:
        message += f" Credential store problem: {resolution.store_error}."
    return [_issue(ReadinessCode.OPENAI_KEY_MISSING, "OPENAI_API_KEY", message)]


def _is_workbook_file(raw_path: str) -> bool:
    try:
        candidate = Path(raw_path).expanduser()
        return candidate.is_file() and candidate.suffix.lower() in _WORKBOOK_SUFFIXES
    except (OSError, RuntimeError, ValueError):
        return False


def _source_issues(config: AppConfig) -> list[ReadinessIssue]:
    platforms, boards = config.platforms, config.boards
    usable: list[str] = []
    workbook_state = "disabled"
    workbook_path = (config.workbook.path or "").strip()
    if platforms.workbook:
        if not workbook_path:
            workbook_state = "unset"
        elif _is_workbook_file(workbook_path):
            workbook_state = "ok"
            usable.append("workbook")
        else:
            workbook_state = "missing"
    for name, enabled, tokens in (
        ("greenhouse", platforms.greenhouse, boards.greenhouse),
        ("lever", platforms.lever, boards.lever),
        ("ashby", platforms.ashby, boards.ashby),
    ):
        if enabled and any(token.strip() for token in tokens):
            usable.append(name)
    for name, enabled in (("linkedin", platforms.linkedin), ("indeed", platforms.indeed)):
        if enabled:
            usable.append(name)

    issues: list[ReadinessIssue] = []
    if workbook_state == "missing":
        issues.append(
            _issue(
                ReadinessCode.WORKBOOK_MISSING,
                "workbook.path",
                f"workbook.path '{workbook_path}' is not an existing .xlsx file. Fix the path on the "
                "dashboard Search profile page, or turn the workbook platform off.",
            )
        )
    elif workbook_state == "unset" and not usable:
        issues.append(
            _issue(
                ReadinessCode.WORKBOOK_MISSING,
                "workbook.path",
                "The workbook platform is enabled but workbook.path is not set. Set it to your "
                "verified-opportunities .xlsx on the dashboard Search profile page, or turn the "
                "workbook platform off.",
            )
        )
    if not usable:
        issues.append(
            _issue(
                ReadinessCode.NO_SOURCE,
                "platforms",
                "No usable discovery source. Configure at least one: an existing workbook path, "
                "Greenhouse/Lever/Ashby board tokens (with that platform turned on), or the "
                "LinkedIn/Indeed browser platforms.",
            )
        )
    return issues


def _attestation_issues(config: AppConfig) -> list[ReadinessIssue]:
    if config.apply.attestations_authorized:
        return []
    return [
        _issue(
            ReadinessCode.ATTESTATION_NOT_AUTHORIZED,
            "apply.attestations_authorized",
            "Full-auto mode needs your one-time authorization to tick certification, consent and "
            "signature boxes on application forms. Turn on 'attestations authorized' on the dashboard "
            "Profile page (apply.attestations_authorized in config.json), or use dry_run mode.",
        )
    ]
