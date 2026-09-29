"""Unit tests for core.filter — job scoring and ATS detection.

Requirement traceability:
    FR-044  Job scoring engine (score_job, _extract_salary_number)
    FR-045  ATS platform detection (detect_ats, ATS_FINGERPRINTS)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from core.filter import (
    ScoredJob,
    _extract_salary_number,
    _title_score,
    detect_ats,
    score_job,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeRawJob:
    """Minimal stand-in for RawJob to avoid importing bot.search.base."""

    title: str = "Software Engineer"
    company: str = "Acme Corp"
    location: str = "San Francisco, CA"
    salary: str | None = None
    description: str = "Build awesome Python APIs."
    apply_url: str = "https://example.com/apply"
    platform: str = "linkedin"
    external_id: str = "job-001"
    posted_at: str | None = None


def _make_config(
    job_titles=None,
    locations=None,
    remote_only=False,
    salary_min=None,
    keywords_include=None,
    keywords_exclude=None,
    min_match_score=50,
    company_blacklist=None,
):
    """Create a minimal mock AppConfig."""
    cfg = MagicMock()
    cfg.search_criteria.job_titles = job_titles or ["Software Engineer"]
    cfg.search_criteria.locations = locations or ["San Francisco"]
    cfg.search_criteria.remote_only = remote_only
    cfg.search_criteria.salary_min = salary_min
    cfg.search_criteria.keywords_include = keywords_include or []
    cfg.search_criteria.keywords_exclude = keywords_exclude or []
    cfg.bot.min_match_score = min_match_score
    cfg.company_blacklist = company_blacklist or []
    return cfg


# ===================================================================
# detect_ats
# ===================================================================


class TestDetectATS:
    """ATS fingerprint detection from URLs."""

    def test_greenhouse(self):
        assert detect_ats("https://boards.greenhouse.io/company/jobs/123") == "greenhouse"

    def test_lever(self):
        assert detect_ats("https://jobs.lever.co/company/abc") == "lever"

    def test_workday(self):
        assert detect_ats("https://company.myworkdayjobs.com/en-US/External") == "workday"

    def test_linkedin(self):
        assert detect_ats("https://www.linkedin.com/jobs/view/123") == "linkedin"

    def test_indeed(self):
        assert detect_ats("https://www.indeed.com/viewjob?jk=abc") == "indeed"

    def test_unknown_url(self):
        assert detect_ats("https://company.com/careers") is None

    def test_case_insensitive(self):
        assert detect_ats("https://BOARDS.GREENHOUSE.IO/jobs/1") == "greenhouse"


# ===================================================================
# _extract_salary_number
# ===================================================================


class TestExtractSalaryNumber:
    """Salary string parsing."""

    def test_plain_number(self):
        assert _extract_salary_number("$120,000") == 120000

    def test_k_suffix(self):
        assert _extract_salary_number("$120K") == 120000

    def test_hourly_to_annual(self):
        result = _extract_salary_number("$60/hr")
        assert result == 60 * 2080

    def test_range_takes_lower(self):
        result = _extract_salary_number("$120,000 - $150,000")
        assert result == 120000

    def test_unparsable(self):
        assert _extract_salary_number("Competitive") is None

    def test_empty_string(self):
        assert _extract_salary_number("") is None

    def test_per_hour_text(self):
        result = _extract_salary_number("55 per hour")
        assert result == 55 * 2080


# ===================================================================
# score_job — Happy Path
# ===================================================================


class TestScoreJobHappyPath:
    """Scoring logic for various job characteristics."""

    def test_perfect_match_scores_high(self):
        job = FakeRawJob(
            title="Software Engineer",
            location="San Francisco, CA",
            description="We need a Python developer to build Flask APIs.",
        )
        cfg = _make_config(
            job_titles=["Software Engineer"],
            locations=["San Francisco"],
            keywords_include=["Python", "Flask"],
        )
        result = score_job(job, cfg)
        # Title 35 + Salary 20 (no min) + Location 20 + Keywords 10 = 85
        assert result.score == 85
        assert result.pass_filter is True

    def test_returns_scored_job_dataclass(self):
        job = FakeRawJob()
        cfg = _make_config()
        result = score_job(job, cfg)
        assert isinstance(result, ScoredJob)
        assert result.raw is job
        assert isinstance(result.id, str)
        assert len(result.id) == 36  # UUID format

    def test_no_salary_min_gives_full_salary_points(self):
        job = FakeRawJob()
        cfg = _make_config(salary_min=None)
        result = score_job(job, cfg)
        # Includes 20 points from salary
        assert result.score >= 20

    def test_unknown_salary_gives_partial_credit(self):
        job = FakeRawJob(salary=None)
        cfg = _make_config(salary_min=100000)
        result = score_job(job, cfg)
        # Title=35 + Salary=10 (unknown) + Location=20 + Keywords=0 = 65
        assert result.score == 65


# ===================================================================
# score_job — Hard Disqualifiers
# ===================================================================


class TestScoreJobDisqualifiers:
    """Hard disqualifiers that set score to 0."""

    def test_blacklisted_company(self):
        job = FakeRawJob(company="Evil Corp")
        cfg = _make_config(company_blacklist=["Evil Corp"])
        result = score_job(job, cfg)
        assert result.score == 0
        assert result.pass_filter is False
        assert "Blacklisted" in result.skip_reason

    def test_blacklisted_company_case_insensitive(self):
        job = FakeRawJob(company="EVIL CORP")
        cfg = _make_config(company_blacklist=["evil corp"])
        result = score_job(job, cfg)
        assert result.pass_filter is False

    def test_exclude_keyword_in_title(self):
        job = FakeRawJob(title="Senior PHP Developer")
        cfg = _make_config(keywords_exclude=["PHP"])
        result = score_job(job, cfg)
        assert result.score == 0
        assert "Excluded keyword" in result.skip_reason

    def test_exclude_keyword_in_description(self):
        job = FakeRawJob(description="Must have 10 years PHP experience")
        cfg = _make_config(keywords_exclude=["PHP"])
        result = score_job(job, cfg)
        assert result.pass_filter is False

    def test_duplicate_job_in_database(self):
        job = FakeRawJob(external_id="dup-123", platform="linkedin")
        cfg = _make_config()
        mock_db = MagicMock()
        mock_db.is_done.return_value = True
        result = score_job(job, cfg, db=mock_db)
        assert result.score == 0
        assert "Already applied" in result.skip_reason
        mock_db.is_done.assert_called_once_with("dup-123", "linkedin",
                                                apply_url=job.apply_url)


# ===================================================================
# score_job — Threshold
# ===================================================================


class TestScoreJobThreshold:
    """Score vs threshold pass/fail."""

    def test_below_threshold_fails(self):
        job = FakeRawJob(
            title="Unrelated Job",
            location="Antarctica",
            description="Nothing matching.",
        )
        cfg = _make_config(min_match_score=50)
        result = score_job(job, cfg)
        assert result.pass_filter is False
        assert "below threshold" in result.skip_reason

    def test_at_threshold_passes(self):
        job = FakeRawJob(title="Software Engineer", location="San Francisco, CA")
        cfg = _make_config(min_match_score=75)
        result = score_job(job, cfg)
        # Title=35 + Salary=20 + Location=20 = 75
        assert result.score >= 75
        assert result.pass_filter is True


# ===================================================================
# score_job — Location
# ===================================================================


class TestScoreJobLocation:
    """Location matching logic."""

    def test_remote_only_matches_remote(self):
        job = FakeRawJob(location="Remote")
        cfg = _make_config(remote_only=True)
        result = score_job(job, cfg)
        # Should get location points
        assert result.score >= 20

    def test_remote_only_rejects_onsite(self):
        job = FakeRawJob(location="New York, NY")
        cfg = _make_config(remote_only=True)
        result = score_job(job, cfg)
        # Title=35 + Salary=20 + Location=0 = 55
        assert result.score == 55

    def test_location_match(self):
        job = FakeRawJob(location="San Francisco, CA")
        cfg = _make_config(locations=["San Francisco"])
        result = score_job(job, cfg)
        assert result.score >= 20

    def test_partial_country_match(self):
        job = FakeRawJob(location="Austin, TX")
        cfg = _make_config(locations=["San Francisco, TX"])
        result = score_job(job, cfg)
        # Should get partial credit (10) for same state/country
        assert result.score >= 10


# ===================================================================
# score_job — Keywords
# ===================================================================


class TestScoreJobKeywords:
    """Keyword include matching."""

    def test_keywords_add_5_each(self):
        job = FakeRawJob(description="Python Flask React PostgreSQL Docker")
        cfg = _make_config(keywords_include=["Python", "Flask", "React", "PostgreSQL", "Docker"])
        result = score_job(job, cfg)
        # 5 keywords * 5 = 25 (max)
        assert result.score >= 25

    def test_keywords_max_at_25(self):
        job = FakeRawJob(description="Python Flask React PostgreSQL Docker Kubernetes")
        cfg = _make_config(keywords_include=["Python", "Flask", "React", "PostgreSQL", "Docker", "Kubernetes"])
        result = score_job(job, cfg)
        # 6 keywords * 5 = 30, but capped at 25
        # Title=35 + Salary=20 + Location=20 + Keywords=25 = 100
        assert result.score <= 100

    def test_no_keywords_zero_bonus(self):
        job = FakeRawJob()
        cfg = _make_config(keywords_include=[])
        result = score_job(job, cfg)
        # No keyword bonus
        assert result.score == 75  # Title=35 + Salary=20 + Location=20


# ===================================================================
# score_job — Title Partial Match
# ===================================================================


class TestScoreJobTitlePartial:
    """Title partial matching logic."""

    def test_exact_title_match_full_points(self):
        job = FakeRawJob(title="Software Engineer")
        cfg = _make_config(job_titles=["Software Engineer"])
        result = score_job(job, cfg)
        assert result.score >= 35

    def test_partial_title_overlap(self):
        job = FakeRawJob(title="Senior Software Engineer II")
        cfg = _make_config(job_titles=["Software Engineer"])
        result = score_job(job, cfg)
        # "Software Engineer" is contained in the title — full 35 points
        assert result.score >= 35

    def test_no_title_match(self):
        job = FakeRawJob(title="Marketing Manager")
        cfg = _make_config(job_titles=["Software Engineer"])
        result = score_job(job, cfg)
        # No title points
        assert result.score < 75


class TestCountryGate:
    """With countries configured, location gates (US/Canada) instead of ranking."""

    def _cfg(self):
        cfg = _make_config(locations=[], min_match_score=50)
        cfg.search_criteria.countries = ["United States", "Canada"]
        return cfg

    def test_regions(self):
        from core.filter import location_region
        assert location_region("Menlo Park, CA, United States") == "us"
        assert location_region("Washington, DC") == "us"
        assert location_region("Toronto, ON") == "canada"
        assert location_region("Vancouver, British Columbia") == "canada"
        assert location_region("London, United Kingdom") == "other"
        assert location_region("Bengaluru, India") == "other"
        assert location_region("Seattle") == "unknown"        # unclear stays eligible
        assert location_region("Remote") == "unknown"

    def test_us_and_canada_get_full_points(self):
        us = score_job(FakeRawJob(location="Pittsburgh, PA"), self._cfg())
        ca = score_job(FakeRawJob(location="Toronto, ON, Canada"), self._cfg())
        assert us.score == ca.score == 75                     # title 35 + salary 20 + location 20

    def test_outside_us_canada_is_skipped(self):
        r = score_job(FakeRawJob(location="Dublin, Ireland"), self._cfg())
        assert not r.pass_filter and "Outside" in r.skip_reason


class TestTitleScore:
    """_title_score — punctuation-insensitive, best-of-all-targets matching."""

    TARGETS = [
        "Summer 2027 Product Management Intern",
        "Summer 2027 Strategy Intern",
        "Summer 2027 Business Operations Intern",
    ]

    def test_punctuation_does_not_break_word_matching(self):
        # "(Summer 2027)" used to tokenize as "(summer" / "2027)" and score 0
        title = "Internship, FP&A & Business Operations (Summer 2027)"
        assert _title_score(title, self.TARGETS) == 35

    def test_later_exact_target_beats_earlier_partial_target(self):
        # The first target partially matches (summer/2027/intern); the second
        # matches fully and must win instead of the loop stopping at 20.
        assert _title_score("Strategy Intern - Summer 2027", self.TARGETS) == 35

    def test_internship_counts_as_intern(self):
        assert _title_score("Summer 2027 Strategy Internship", self.TARGETS) == 35

    def test_half_overlap_scores_partial(self):
        assert _title_score("Summer 2027 Sales Analyst Intern", self.TARGETS) == 20

    def test_title_without_cycle_words_matches_fully(self):
        # Real titles omit "Summer 2027"; manager == management
        assert _title_score("Associate Product Manager Intern", self.TARGETS) == 35

    def test_unrelated_title_scores_zero(self):
        assert _title_score("Senior Accountant", self.TARGETS) == 0

    def test_empty_targets_score_zero(self):
        assert _title_score("Strategy Intern", []) == 0
