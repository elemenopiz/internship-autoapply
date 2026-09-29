from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from autoapply.clock import local_day, local_day_bounds_utc
from autoapply.config import AppConfig, AppPaths, load_config, resolve_resume_path, save_config
from autoapply.models import Opportunity, Profile
from autoapply.normalize import (
    canonical_url,
    fingerprint,
    host_of,
    norm_company,
    norm_title,
    parse_year_month,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.Acme.com/jobs/1/?utm_source=x&b=2&a=1#frag",
            "https://acme.com/jobs/1?a=1&b=2",
        ),
        (
            "https://job-boards.greenhouse.io/acme/jobs/123?gh_src=abc",
            "https://boards.greenhouse.io/acme/jobs/123",
        ),
        ("jobs.lever.co/acme/1111-2222/apply", "https://jobs.lever.co/acme/1111-2222"),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/PM-Intern_R1/apply/applyManually",
            "https://acme.wd5.myworkdayjobs.com/External/job/Austin-TX/PM-Intern_R1",
        ),
        ("", ""),
    ],
)
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_host_of_strips_localhost_suffix_and_port() -> None:
    assert (
        host_of("http://acme.wd5.myworkdayjobs.com.localhost:51234/x")
        == "acme.wd5.myworkdayjobs.com"
    )
    assert host_of("https://www.example.com/") == "example.com"


def test_fingerprint_folds_term_and_intern_wording() -> None:
    a = fingerprint("Acme, Inc.", "Product Management Internship - Summer 2027", "Austin, TX, USA")
    b = fingerprint("ACME", "Product Management Intern (Summer 2027)", "Austin")
    assert a == b == "acme|product management intern|austin"
    assert norm_company("The Home Depot, Inc.") == "home depot"
    assert norm_title("Co-op") == "coop"


def test_opportunity_id_is_stable_across_url_noise() -> None:
    a = Opportunity(company="Acme", title="PM Intern", url="https://acme.com/jobs/1?utm_source=a")
    b = Opportunity(company="Acme", title="PM Intern", url="https://www.acme.com/jobs/1/apply")
    assert a.id == b.id and len(a.id) == 16


def test_parse_year_month() -> None:
    assert parse_year_month("May 2028") == (2028, 5)
    assert parse_year_month("05/2028") == (2028, 5)
    assert parse_year_month("2028-05-15") == (2028, 5)
    assert parse_year_month("nonsense") is None


def test_profile_normalises_dates_and_phone() -> None:
    profile = Profile(
        first_name="Ada", last_name="L", graduation_date="May 2028", phone="+1 (512) 555-0100"
    )
    assert profile.graduation_date == "2028-05"
    assert profile.phone_national == "5125550100"
    assert profile.full_name == "Ada L"


def test_config_defaults_match_readme() -> None:
    config = AppConfig()
    assert config.mode == "full_auto"
    assert config.daily_cap == 5
    assert config.schedule.enabled is False
    assert config.platforms.workbook is True


def test_config_round_trip_keeps_unknown_keys_and_no_secrets(paths: AppPaths) -> None:
    paths.config_file.write_text(
        json.dumps({"daily_cap": 3, "future_key": {"a": 1}}), encoding="utf-8"
    )
    config = load_config(paths)
    assert config.daily_cap == 3
    save_config(paths, config)
    saved = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert saved["future_key"] == {"a": 1}
    assert "sk-" not in paths.config_file.read_text(encoding="utf-8")


def test_resume_resolution_prefers_fallback_path(paths: AppPaths, tmp_path) -> None:
    config = AppConfig()
    assert resolve_resume_path(config, paths) is None
    paths.resume_file.write_bytes(b"%PDF-1.4")
    assert resolve_resume_path(config, paths) == paths.resume_file
    mine = tmp_path / "mine.pdf"
    mine.write_bytes(b"%PDF-1.4")
    config.profile.fallback_resume_path = str(mine)
    assert resolve_resume_path(config, paths) == mine


def test_local_day_uses_user_timezone() -> None:
    late_utc = datetime(2026, 9, 30, 3, 30, tzinfo=UTC)  # still Sep 29 in Chicago
    assert local_day(late_utc, "America/Chicago") == date(2026, 9, 29)
    start, end = local_day_bounds_utc(date(2026, 9, 29), "America/Chicago")
    assert start <= late_utc < end
