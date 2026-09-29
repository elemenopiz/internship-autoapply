"""readiness.py: what stands between the user and a first run (docs/SPEC.md 5.2, acceptance A1)."""

from __future__ import annotations

import dataclasses
import json
import os
import pickle
from pathlib import Path
from typing import Any

import pytest

from autoapply.config import AppConfig, AppPaths, save_config
from autoapply.models import REQUIRED_PROFILE_FIELDS, Profile, RunMode
from autoapply.readiness import (
    ReadinessCode,
    ReadinessError,
    ReadinessIssue,
    ReadinessReport,
    check_readiness,
    ensure_ready_or_raise,
    format_report,
)
from autoapply.secrets import MemoryCredentialStore, set_stored_openai_key

KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"
ENV = {"OPENAI_API_KEY": KEY}


def filled_profile() -> Profile:
    """A fictional, fully filled-in profile."""
    return Profile(
        first_name="Alex",
        last_name="Rivera",
        email="alex.rivera@example.test",
        phone="+1 (512) 555-0142",
        address_line1="1234 Example Street",
        city="Austin",
        state="TX",
        postal_code="78701",
        country="United States",
        school="The University of Texas at Austin",
        degree="B.B.A.",
        major="Management Information Systems",
        graduation_date="2028-05",
        authorized_to_work_us=True,
        requires_sponsorship=False,
    )


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    path = tmp_path / "Verified Opportunities.xlsx"
    path.write_bytes(b"PK\x03\x04 fictional workbook")
    return path


@pytest.fixture
def ready_config(paths: AppPaths, workbook: Path) -> AppConfig:
    """Everything a run needs, except the key (that comes from ``ENV``)."""
    config = AppConfig()
    config.profile = filled_profile()
    config.workbook.path = str(workbook)
    config.apply.attestations_authorized = True
    paths.resume_file.write_bytes(b"%PDF-1.4\n% fictional resume\n")
    return config


def codes(report: ReadinessReport) -> list[str]:
    return [issue.code for issue in report.issues]


def pairs(report: ReadinessReport) -> list[tuple[str, str]]:
    return [(issue.code, issue.field) for issue in report.issues]


# ------------------------------------------------------------------------------------------- the happy path


def test_a_fully_filled_profile_with_resume_and_key_is_ready(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    report = check_readiness(ready_config, paths, ENV)
    assert report.ok
    assert report.issues == []
    assert format_report(report) == "Ready: all readiness checks passed."
    assert json.loads(report.to_json()) == {"ok": True, "issues": []}


def test_ensure_ready_returns_the_report_when_everything_is_fine(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    report = ensure_ready_or_raise(ready_config, paths, ENV)
    assert report.ok


def test_readiness_is_ready_in_every_mode_when_fully_configured(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    for mode in RunMode:
        assert check_readiness(ready_config, paths, ENV, mode=mode).ok


# ------------------------------------------------------------------------------------------- profile fields


@pytest.mark.parametrize("name", REQUIRED_PROFILE_FIELDS)
def test_every_required_profile_field_is_reported_when_missing(
    ready_config: AppConfig, paths: AppPaths, name: str
) -> None:
    blank: Any = None if name in {"authorized_to_work_us", "requires_sponsorship"} else ""
    setattr(ready_config.profile, name, blank)
    report = check_readiness(ready_config, paths, ENV)
    assert not report.ok
    assert pairs(report) == [("profile_field_missing", name)]
    assert name in report.issues[0].message


def test_all_required_fields_missing_are_listed_together_in_declared_order(paths: AppPaths) -> None:
    config = AppConfig()
    config.profile = Profile(country="", school="")
    report = check_readiness(config, paths, {})
    missing = [i.field for i in report.issues if i.code == "profile_field_missing"]
    assert missing == list(REQUIRED_PROFILE_FIELDS)


def test_the_default_profile_is_missing_everything_a_user_must_supply(paths: AppPaths) -> None:
    report = check_readiness(AppConfig(), paths, {})
    missing = {i.field for i in report.issues if i.code == "profile_field_missing"}
    # country and school ship with defaults; everything else must come from the user.
    assert missing == set(REQUIRED_PROFILE_FIELDS) - {"country", "school"}


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_whitespace_only_text_counts_as_missing(
    ready_config: AppConfig, paths: AppPaths, blank: str
) -> None:
    ready_config.profile.first_name = blank
    assert pairs(check_readiness(ready_config, paths, ENV)) == [
        ("profile_field_missing", "first_name")
    ]


def test_false_is_a_real_answer_for_the_yes_no_fields(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.profile.authorized_to_work_us = False
    ready_config.profile.requires_sponsorship = True
    assert check_readiness(ready_config, paths, ENV).ok


@pytest.mark.parametrize(
    "email",
    [
        "alex",
        "alex@",
        "@example.test",
        "alex@example",
        "a b@example.test",
        "alex@@example.test",
        "alex@example..test",
        "alex@.test",
        "alex@example.",
        "alex.rivera.example.test",
    ],
)
def test_a_malformed_email_is_invalid_not_missing(
    ready_config: AppConfig, paths: AppPaths, email: str
) -> None:
    ready_config.profile.email = email
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("profile_field_invalid", "email")]
    assert email not in report.issues[0].message  # never echo the user's value


@pytest.mark.parametrize(
    "email",
    [
        "alex.rivera@example.test",
        "a@b.co",
        "alex+jobs@mail.example.test",
        "o'brien@example.test",
        "josé@example.test",
    ],
)
def test_ordinary_emails_are_valid(ready_config: AppConfig, paths: AppPaths, email: str) -> None:
    ready_config.profile.email = email
    assert check_readiness(ready_config, paths, ENV).ok


@pytest.mark.parametrize(
    "phone", ["555-0142", "12345", "call me", "+1 555", "123456789", "(512) 555-01"]
)
def test_a_phone_with_fewer_than_ten_digits_is_invalid(
    ready_config: AppConfig, paths: AppPaths, phone: str
) -> None:
    ready_config.profile.phone = phone
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("profile_field_invalid", "phone")]
    assert "10 digits" in report.issues[0].message


@pytest.mark.parametrize(
    "phone",
    [
        "5125550142",
        "512-555-0142",
        "(512) 555-0142",
        "+1 512 555 0142",
        "512.555.0142",
        "1-512-555-0142 ext. 5",
        "+44 20 7946 0958",
    ],
)
def test_phones_with_ten_or_more_digits_are_valid(
    ready_config: AppConfig, paths: AppPaths, phone: str
) -> None:
    ready_config.profile.phone = phone
    assert check_readiness(ready_config, paths, ENV).ok


@pytest.mark.parametrize(
    "date", ["2028", "soon", "2028-13", "2028-00", "28-05", "2028/05/x", "May", "20285"]
)
def test_a_graduation_date_that_is_not_year_month_is_invalid(
    ready_config: AppConfig, paths: AppPaths, date: str
) -> None:
    ready_config.profile.graduation_date = date
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("profile_field_invalid", "graduation_date")]
    assert "YYYY-MM" in report.issues[0].message


@pytest.mark.parametrize(
    "date", ["2028-05", "May 2028", "05/2028", "2028-5", "2028-05-15", "Dec 2027"]
)
def test_graduation_dates_the_profile_can_normalise_are_valid(
    ready_config: AppConfig, paths: AppPaths, date: str
) -> None:
    ready_config.profile.graduation_date = date  # Profile normalises parseable dates to YYYY-MM
    assert check_readiness(ready_config, paths, ENV).ok


def test_a_missing_field_is_never_also_reported_invalid(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.profile.email = ""
    ready_config.profile.phone = ""
    ready_config.profile.graduation_date = ""
    assert codes(check_readiness(ready_config, paths, ENV)) == ["profile_field_missing"] * 3


def test_missing_and_invalid_issues_follow_the_field_order(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.profile.first_name = ""
    ready_config.profile.email = "nope"
    ready_config.profile.graduation_date = "soon"
    assert pairs(check_readiness(ready_config, paths, ENV)) == [
        ("profile_field_missing", "first_name"),
        ("profile_field_invalid", "email"),
        ("profile_field_invalid", "graduation_date"),
    ]


# ------------------------------------------------------------------------------------------- resume


def test_a_missing_resume_is_reported_with_both_ways_to_fix_it(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    paths.resume_file.unlink()
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("resume_missing", "resume")]
    message = report.issues[0].message
    assert str(paths.resume_file) in message
    assert "fallback_resume_path" in message
    assert "dashboard" in message


def test_a_configured_but_wrong_resume_path_is_named_in_the_message(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    paths.resume_file.unlink()
    ready_config.profile.fallback_resume_path = str(tmp_path / "gone.pdf")
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("resume_missing", "resume")]
    assert str(tmp_path / "gone.pdf") in report.issues[0].message


def test_a_resume_that_is_not_a_pdf_does_not_count(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    paths.resume_file.unlink()
    document = tmp_path / "resume.docx"
    document.write_bytes(b"not a pdf")
    ready_config.profile.fallback_resume_path = str(document)
    assert codes(check_readiness(ready_config, paths, ENV)) == ["resume_missing"]


def test_a_resume_directory_does_not_count(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    paths.resume_file.unlink()
    folder = tmp_path / "resume.pdf"
    folder.mkdir()
    ready_config.profile.fallback_resume_path = str(folder)
    assert codes(check_readiness(ready_config, paths, ENV)) == ["resume_missing"]


def test_a_fallback_resume_path_with_spaces_and_unicode_is_accepted(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    paths.resume_file.unlink()
    folder = tmp_path / "Mi Carpeta \u00dcn\u00efc\u00f6de"
    folder.mkdir()
    resume = folder / "R\u00e9sum\u00e9 (final v2).PDF"
    resume.write_bytes(b"%PDF-1.4")
    ready_config.profile.fallback_resume_path = str(resume)
    assert check_readiness(ready_config, paths, ENV).ok


def test_a_tilde_in_the_fallback_resume_path_is_expanded(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.resume_file.unlink()
    home = tmp_path / "home"
    home.mkdir()
    (home / "resume.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    ready_config.profile.fallback_resume_path = "~/resume.pdf"
    assert check_readiness(ready_config, paths, ENV).ok


def test_an_unexpandable_tilde_user_resume_path_is_reported_not_raised(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    paths.resume_file.unlink()
    ready_config.profile.fallback_resume_path = "~no_such_user_zq9/resume.pdf"
    assert codes(check_readiness(ready_config, paths, ENV)) == ["resume_missing"]


def test_a_resume_lookup_that_raises_is_reported_not_propagated(
    ready_config: AppConfig, paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(config: AppConfig, paths: AppPaths) -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr("autoapply.readiness.resolve_resume_path", explode)
    assert codes(check_readiness(ready_config, paths, ENV)) == ["resume_missing"]


def test_the_uploaded_resume_is_used_when_the_fallback_path_is_wrong(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    ready_config.profile.fallback_resume_path = str(tmp_path / "gone.pdf")
    assert check_readiness(ready_config, paths, ENV).ok  # paths.resume_file exists


# ------------------------------------------------------------------------------------------- OpenAI key


def test_a_missing_key_is_reported_with_the_way_to_set_it(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    report = check_readiness(ready_config, paths, {})
    assert pairs(report) == [("openai_key_missing", "OPENAI_API_KEY")]
    assert "set_openai_key.ps1" in report.issues[0].message


@pytest.mark.parametrize("value", ["", "   ", '""', "\r\n"])
def test_an_empty_key_variable_counts_as_missing(
    ready_config: AppConfig, paths: AppPaths, value: str
) -> None:
    assert codes(check_readiness(ready_config, paths, {"OPENAI_API_KEY": value})) == [
        "openai_key_missing"
    ]


def test_a_quoted_key_in_the_environment_is_accepted(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    assert check_readiness(ready_config, paths, {"OPENAI_API_KEY": f' "{KEY}"\r\n'}).ok


def test_a_key_in_the_credential_store_satisfies_readiness(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    store = MemoryCredentialStore()
    set_stored_openai_key(store, KEY)
    assert check_readiness(ready_config, paths, {}, store).ok


def test_the_credential_store_is_not_consulted_without_a_store(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    assert codes(check_readiness(ready_config, paths, {}, None)) == ["openai_key_missing"]


def test_a_broken_credential_store_is_explained_in_the_message_without_leaking(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    class Broken:
        def get_secret(self, service: str, username: str) -> str | None:
            raise RuntimeError(f"leaky failure {KEY}")

        def set_secret(self, service: str, username: str, secret: str) -> None:
            raise AssertionError("readiness must never write")

        def delete_secret(self, service: str, username: str) -> None:
            raise AssertionError("readiness must never delete")

    report = check_readiness(ready_config, paths, {}, Broken())
    assert pairs(report) == [("openai_key_missing", "OPENAI_API_KEY")]
    assert "Credential store problem" in report.issues[0].message
    assert KEY not in format_report(report) + report.to_json() + repr(report)


def test_the_environment_defaults_to_the_live_process_environment(
    ready_config: AppConfig, paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert codes(check_readiness(ready_config, paths)) == ["openai_key_missing"]
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert check_readiness(ready_config, paths).ok


# ------------------------------------------------------------------------------------------- sources


def apply_sources(
    config: AppConfig,
    tmp_path: Path,
    *,
    workbook: str,
    platforms: dict[str, bool] | None = None,
    boards: dict[str, list[str]] | None = None,
) -> None:
    """Set the workbook path from a named scenario and override platform toggles / board tokens."""
    good = tmp_path / "wb"
    good.mkdir(exist_ok=True)
    (good / "sheet.xlsx").write_bytes(b"PK")
    (good / "SHEET.XLSX").write_bytes(b"PK")
    (good / "macro.xlsm").write_bytes(b"PK")
    (good / "old.xls").write_bytes(b"x")
    (good / "data.csv").write_bytes(b"x")
    unicode_dir = good / "Carpeta \u00dcn\u00efc\u00f6de (v2)"
    unicode_dir.mkdir(exist_ok=True)
    (unicode_dir / "Verified Opportunities \u2013 2027.xlsx").write_bytes(b"PK")
    (good / "folder.xlsx").mkdir(exist_ok=True)
    scenarios: dict[str, str | None] = {
        "valid": str(good / "sheet.xlsx"),
        "upper": str(good / "SHEET.XLSX"),
        "macro": str(good / "macro.xlsm"),
        "unicode": str(unicode_dir / "Verified Opportunities \u2013 2027.xlsx"),
        "unset": None,
        "blank": "   ",
        "missing": str(good / "nope.xlsx"),
        "directory": str(good / "folder.xlsx"),
        "xls": str(good / "old.xls"),
        "csv": str(good / "data.csv"),
        "nul": "bad\0name.xlsx",
        # Path.expanduser() raises RuntimeError for a ~user it cannot resolve.
        "tilde_user": "~no_such_user_zq9/Verified Opportunities.xlsx",
    }
    config.workbook.path = scenarios[workbook]
    for name, value in (platforms or {}).items():
        setattr(config.platforms, name, value)
    for name, tokens in (boards or {}).items():
        setattr(config.boards, name, tokens)


SOURCE_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    ("valid workbook", {"workbook": "valid"}, []),
    ("uppercase extension", {"workbook": "upper"}, []),
    ("macro-enabled workbook", {"workbook": "macro"}, []),
    ("unicode and spaces in the path", {"workbook": "unicode"}, []),
    (
        "default: workbook on, unset, no boards",
        {"workbook": "unset"},
        ["workbook_missing", "no_source"],
    ),
    ("blank path is unset", {"workbook": "blank"}, ["workbook_missing", "no_source"]),
    ("path set but missing", {"workbook": "missing"}, ["workbook_missing", "no_source"]),
    ("path is a directory", {"workbook": "directory"}, ["workbook_missing", "no_source"]),
    ("legacy .xls is not readable", {"workbook": "xls"}, ["workbook_missing", "no_source"]),
    ("csv is not a workbook", {"workbook": "csv"}, ["workbook_missing", "no_source"]),
    ("path with a NUL character", {"workbook": "nul"}, ["workbook_missing", "no_source"]),
    (
        "a ~user path that cannot be expanded",
        {"workbook": "tilde_user"},
        ["workbook_missing", "no_source"],
    ),
    (
        "missing path is flagged even when boards work",
        {"workbook": "missing", "boards": {"greenhouse": ["acme"]}},
        ["workbook_missing"],
    ),
    (
        "missing path is flagged even when linkedin works",
        {"workbook": "missing", "platforms": {"linkedin": True}},
        ["workbook_missing"],
    ),
    (
        "unset path is fine when boards work",
        {"workbook": "unset", "boards": {"greenhouse": ["acme"]}},
        [],
    ),
    (
        "unset path is fine when linkedin works",
        {"workbook": "unset", "platforms": {"linkedin": True}},
        [],
    ),
    (
        "workbook platform off, nothing else",
        {"workbook": "valid", "platforms": {"workbook": False}},
        ["no_source"],
    ),
    (
        "workbook off, lever token",
        {"workbook": "unset", "platforms": {"workbook": False}, "boards": {"lever": ["acme"]}},
        [],
    ),
    (
        "workbook off, ashby token",
        {"workbook": "unset", "platforms": {"workbook": False}, "boards": {"ashby": ["acme"]}},
        [],
    ),
    (
        "tokens whose platform is off do not count",
        {
            "workbook": "unset",
            "platforms": {"workbook": False, "greenhouse": False},
            "boards": {"greenhouse": ["acme"]},
        },
        ["no_source"],
    ),
    (
        "blank tokens do not count",
        {
            "workbook": "unset",
            "platforms": {"workbook": False},
            "boards": {"greenhouse": ["", "  "]},
        },
        ["no_source"],
    ),
    (
        "indeed alone is a source",
        {"workbook": "unset", "platforms": {"workbook": False, "indeed": True}},
        [],
    ),
    (
        "a workbook that exists satisfies the source rule even with everything else off",
        {"workbook": "valid", "platforms": {"greenhouse": False, "lever": False, "ashby": False}},
        [],
    ),
]


@pytest.mark.parametrize(
    ("case", "options", "expected"), SOURCE_CASES, ids=[c[0] for c in SOURCE_CASES]
)
def test_discovery_source_rules(
    ready_config: AppConfig,
    paths: AppPaths,
    tmp_path: Path,
    case: str,
    options: dict[str, Any],
    expected: list[str],
) -> None:
    apply_sources(ready_config, tmp_path, **options)
    report = check_readiness(ready_config, paths, ENV)
    assert codes(report) == expected, case


def test_a_whitespace_token_added_in_place_does_not_count_as_a_board(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    # Assigning a list re-validates (and strips) it; appending in place bypasses that, so the check
    # must not rely on the config having cleaned the tokens.
    apply_sources(ready_config, tmp_path, workbook="unset", platforms={"workbook": False})
    ready_config.boards.greenhouse.append("   ")
    assert codes(check_readiness(ready_config, paths, ENV)) == ["no_source"]
    ready_config.boards.greenhouse.append("acme")
    assert check_readiness(ready_config, paths, ENV).ok


def test_source_issues_name_the_field_to_fix(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    apply_sources(ready_config, tmp_path, workbook="missing")
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("workbook_missing", "workbook.path"), ("no_source", "platforms")]
    assert str(tmp_path / "wb" / "nope.xlsx") in report.issues[0].message


# ------------------------------------------------------------------------------------------- attestation & modes


def test_full_auto_needs_the_attestation_authorisation(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.apply.attestations_authorized = False
    report = check_readiness(ready_config, paths, ENV)
    assert pairs(report) == [("attestation_not_authorized", "apply.attestations_authorized")]
    assert "dry_run" in report.issues[0].message


@pytest.mark.parametrize("mode", [RunMode.DRY_RUN, RunMode.DISCOVER_ONLY])
def test_only_full_auto_needs_the_attestation_authorisation(
    ready_config: AppConfig, paths: AppPaths, mode: RunMode
) -> None:
    ready_config.apply.attestations_authorized = False
    ready_config.mode = mode
    assert check_readiness(ready_config, paths, ENV).ok


def test_dry_run_still_needs_a_resume_and_a_key(ready_config: AppConfig, paths: AppPaths) -> None:
    ready_config.mode = RunMode.DRY_RUN
    paths.resume_file.unlink()
    assert codes(check_readiness(ready_config, paths, {})) == [
        "resume_missing",
        "openai_key_missing",
    ]


def test_discover_only_skips_resume_key_and_attestation(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.mode = RunMode.DISCOVER_ONLY
    ready_config.apply.attestations_authorized = False
    paths.resume_file.unlink()
    assert check_readiness(ready_config, paths, {}).ok


def test_discover_only_still_needs_profile_fields_and_a_source(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    ready_config.mode = RunMode.DISCOVER_ONLY
    ready_config.profile.city = ""
    apply_sources(ready_config, tmp_path, workbook="unset")
    assert codes(check_readiness(ready_config, paths, {})) == [
        "profile_field_missing",
        "workbook_missing",
        "no_source",
    ]


def test_the_mode_argument_overrides_the_configured_mode(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    ready_config.apply.attestations_authorized = False
    paths.resume_file.unlink()
    assert not check_readiness(ready_config, paths, {}).ok  # config says full_auto
    assert check_readiness(ready_config, paths, {}, mode=RunMode.DISCOVER_ONLY).ok
    dry = check_readiness(ready_config, paths, {}, mode=RunMode.DRY_RUN)
    assert codes(dry) == ["resume_missing", "openai_key_missing"]


def test_the_default_mode_is_full_auto_and_lists_everything_in_a_stable_order(
    paths: AppPaths,
) -> None:
    config = AppConfig()
    assert config.mode is RunMode.FULL_AUTO
    report = check_readiness(config, paths, {})
    non_profile = [c for c in codes(report) if not c.startswith("profile_")]
    assert non_profile == [
        "resume_missing",
        "openai_key_missing",
        "workbook_missing",
        "no_source",
        "attestation_not_authorized",
    ]
    assert codes(report)[: len(REQUIRED_PROFILE_FIELDS) - 2] == ["profile_field_missing"] * (
        len(REQUIRED_PROFILE_FIELDS) - 2
    )


# ------------------------------------------------------------------------------------------- errors and reports


def broken_report(ready_config: AppConfig, paths: AppPaths) -> ReadinessReport:
    """Exactly three issues: a missing field, an invalid field and the attestation flag."""
    ready_config.profile.first_name = ""
    ready_config.profile.email = "nope"
    ready_config.apply.attestations_authorized = False
    return check_readiness(ready_config, paths, ENV)


def test_ensure_ready_or_raise_lists_every_issue(ready_config: AppConfig, paths: AppPaths) -> None:
    ready_config.profile.first_name = ""
    ready_config.profile.email = "nope"
    ready_config.apply.attestations_authorized = False
    with pytest.raises(ReadinessError) as info:
        ensure_ready_or_raise(ready_config, paths, {})
    error = info.value
    assert len(error.report.issues) == 4
    text = str(error)
    for issue in error.report.issues:
        assert issue.code in text
        assert issue.field in text
        assert issue.message in text
    assert "4 issue(s)" in text


def test_ensure_ready_or_raise_honours_the_mode_override(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    paths.resume_file.unlink()
    with pytest.raises(ReadinessError):
        ensure_ready_or_raise(ready_config, paths, ENV)
    assert ensure_ready_or_raise(ready_config, paths, ENV, mode=RunMode.DISCOVER_ONLY).ok


def test_readiness_errors_survive_pickling(ready_config: AppConfig, paths: AppPaths) -> None:
    report = broken_report(ready_config, paths)
    restored = pickle.loads(pickle.dumps(ReadinessError(report)))
    assert isinstance(restored, ReadinessError)
    assert restored.report == report
    assert str(restored) == format_report(report)


def test_format_report_is_numbered_ascii_text_with_a_next_step(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    report = broken_report(ready_config, paths)
    text = format_report(report)
    lines = text.splitlines()
    assert lines[0] == "NOT READY: 3 issue(s) must be fixed before the applier can run."
    assert lines[1].startswith("  1. [profile_field_missing] first_name: ")
    assert lines[2].startswith("  2. [profile_field_invalid] email: ")
    assert lines[3].startswith("  3. [attestation_not_authorized] apply.attestations_authorized: ")
    assert "then check again" in lines[-1]
    assert text.isascii()
    assert "\r" not in text


def test_format_report_copes_with_an_empty_but_not_ok_report() -> None:
    text = format_report(ReadinessReport(ok=False, issues=[]))
    assert text.startswith("NOT READY")


def test_unicode_in_a_path_never_breaks_text_or_json_output(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    paths.resume_file.unlink()
    ready_config.profile.fallback_resume_path = (
        "C:\\Users\\Jos\u00e9\\R\u00e9sum\u00e9 \u2013 final.pdf"
    )
    report = check_readiness(ready_config, paths, ENV)
    text = format_report(report)
    assert "Jos\u00e9" in text
    text.encode("utf-8")
    payload = report.to_json()
    assert payload.isascii()  # safe for any console/pipe encoding
    assert json.loads(payload) == report.to_dict()
    assert json.loads(payload)["issues"][0]["message"].count("Jos\u00e9") == 1


def test_report_json_shape(ready_config: AppConfig, paths: AppPaths) -> None:
    report = broken_report(ready_config, paths)
    data = json.loads(report.to_json())
    assert set(data) == {"ok", "issues"}
    assert data["ok"] is False
    assert [set(item) for item in data["issues"]] == [{"code", "field", "message"}] * 3
    assert data["issues"][0] == report.issues[0].to_dict()
    compact = report.to_json(indent=None)
    assert "\n" not in compact
    assert json.loads(compact) == data


def test_report_helpers_and_immutability() -> None:
    issues = [
        ReadinessIssue("a", "f1", "m1"),
        ReadinessIssue("b", "f2", "m2"),
        ReadinessIssue("a", "f3", "m3"),
    ]
    report = ReadinessReport(ok=False, issues=issues)
    assert report.codes == ["a", "b"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.ok = True  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        issues[0].code = "z"  # type: ignore[misc]
    assert ReadinessReport(ok=True).issues == []


def test_issue_codes_are_plain_strings_matching_the_documented_names(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    assert {c.value for c in ReadinessCode} == {
        "profile_field_missing",
        "profile_field_invalid",
        "resume_missing",
        "openai_key_missing",
        "no_source",
        "workbook_missing",
        "attestation_not_authorized",
    }
    report = broken_report(ready_config, paths)
    assert all(type(issue.code) is str for issue in report.issues)
    assert report.issues[0].code == ReadinessCode.PROFILE_FIELD_MISSING


# ------------------------------------------------------------------------------------------- purity


def test_checking_readiness_changes_nothing(
    ready_config: AppConfig, paths: AppPaths, tmp_path: Path
) -> None:
    def tree() -> list[tuple[str, int]]:
        return sorted(
            (str(p.relative_to(tmp_path)), p.stat().st_size)
            for p in tmp_path.rglob("*")
            if p.is_file()
        )

    before_config = ready_config.model_dump()
    before_files = tree()
    before_environ = dict(os.environ)
    env = dict(ENV)
    check_readiness(ready_config, paths, env, MemoryCredentialStore())
    check_readiness(ready_config, paths, {})
    assert ready_config.model_dump() == before_config
    assert tree() == before_files
    assert env == ENV
    assert dict(os.environ) == before_environ


def test_the_key_never_lands_in_config_json_or_any_data_file(
    ready_config: AppConfig, paths: AppPaths
) -> None:
    check_readiness(ready_config, paths, ENV)
    ensure_ready_or_raise(ready_config, paths, ENV)
    save_config(paths, ready_config)
    for path in paths.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    assert KEY not in paths.config_file.read_text(encoding="utf-8")
