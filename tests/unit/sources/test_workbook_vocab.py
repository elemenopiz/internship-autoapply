"""Small vocabularies: status words, internship-ish titles, ATS detection, URLs, text cleaning, formulas."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from autoapply.models import ATS
from autoapply.sources.workbook import (
    classify_status,
    clean_text,
    detect_ats,
    extract_url,
    is_aggregator_url,
    looks_like_internship,
    parse_ats_hint,
    parse_hyperlink_formula,
    score_sheet_name,
    section_state,
)

# --------------------------------------------------------------------------------------------- status


@pytest.mark.parametrize(
    "value",
    [
        "Open",
        "open",
        "OPEN",
        " Open ",
        "Active",
        "Verified",
        "Yes",
        "Y",
        "yes",
        True,
        1,
        1.0,
        "TRUE",
        "Live",
        "Accepting applications",
        "Currently hiring",
        "Rolling",
        "Open - closes 10/15",  # "closes" is a deadline, not a closure
        "Open until filled",
        "Open (rolling basis)",
        "Not closed",
        "\u2713",
        "\u2705",
    ],
)
def test_open_statuses(value: object) -> None:
    assert classify_status(value) == "open"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("\U0001f7e2 Open", "open"),
        ("\U0001f534 Closed", "closed"),
        ("\u2705 Open", "open"),
        ("\u274c Closed", "closed"),
        ("Open \u2705", "open"),
        ("\u2611", "open"),
        ("\u2612", "closed"),
    ],
)
def test_emoji_statuses(value: str, expected: str) -> None:
    assert classify_status(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "Closed",
        "closed",
        "CLOSED",
        "Filled",
        "Position filled",
        "Expired",
        "Inactive",
        "No",
        "N",
        "no",
        False,
        0,
        0.0,
        "FALSE",
        "Cancelled",
        "Canceled",
        "Withdrawn",
        "Removed",
        "Archived",
        "Unavailable",
        "On hold",
        "Paused",
        "No longer accepting applications",
        "Not accepting",
        "Not open",
        "Not yet open",
        "Coming soon",
        "Opens soon",
        "Closed (filled internally)",
        "\u2717",
        "\u274c",
    ],
)
def test_closed_statuses(value: object) -> None:
    assert classify_status(value) == "closed"


@pytest.mark.parametrize(
    "value", [None, "", "  ", "TBD", "N/A", "Pending", "Check back", "Closing soon", 5, 2.5, "-"]
)
def test_unknown_statuses_are_not_evidence(value: object) -> None:
    assert classify_status(value) == "unknown"


@pytest.mark.parametrize(
    "value", ["Applied", "applied", "Already applied", "Submitted", "Application submitted"]
)
def test_applied_status(value: str) -> None:
    assert classify_status(value) == "applied"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Yes", "closed"),  # "Closed?" -> Yes
        ("No", "open"),
        (True, "closed"),
        (False, "open"),
        (1, "closed"),
        (0, "open"),
        ("\u2713", "closed"),
        ("Open", "open"),  # explicit words keep their meaning even in a "Closed?" column
        ("Closed", "closed"),
        ("Filled", "closed"),
        ("", "unknown"),
    ],
)
def test_inverted_yes_no_for_closed_flag_columns(value: object, expected: str) -> None:
    assert classify_status(value, yes_means="closed") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Closed", "closed"),
        ("CLOSED / ARCHIVED", "closed"),
        ("Expired listings", "closed"),
        ("Past opportunities", "closed"),
        ("Open roles", "open"),
        ("Active", "open"),
        ("Verified openings", "open"),
        ("Product & Program Management", None),
        ("Consulting", None),
        ("", None),
        ("This is a rather long footer sentence about closed roles being removed", None),
    ],
)
def test_section_state(text: str, expected: str | None) -> None:
    assert section_state(text) == expected


# --------------------------------------------------------------------------------------------- internship-ish


@pytest.mark.parametrize(
    "title",
    [
        "Product Management Intern",
        "Product Management Internship",
        "Summer Analyst - Technology",
        "Summer Associate",
        "Business Operations Co-op",
        "Strategy Coop",
        "Senior Intern",  # intern wording always wins
        "Intern, Senior Living Analytics",
        "Data Analyst Intern (Full-Time hours)",
        "APM Intern",
        "Business Analyst Trainee",
        "Extern Program",
        "Product Fellowship",
        "Student Worker - Analytics",
    ],
)
def test_intern_wording_accepts(title: str) -> None:
    assert looks_like_internship(title)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Product Manager",
        "Sr. Business Analyst",
        "Sr Analyst",
        "Staff Program Manager",
        "Principal Consultant",
        "Director of Strategy",
        "VP, Operations",
        "Vice President Strategy",
        "Head of Product",
        "Chief of Staff",
        "Team Lead, Operations",
        "Business Analyst II",
        "Business Analyst III",
        "Product Manager (Full-Time)",
        "Full Time Analyst",
        "Fulltime Analyst",
        "Business Analyst - New Grad",
        "Business Analyst, New Graduate Program",
        "Entry Level Analyst",
        "Experienced Consultant",
    ],
)
def test_non_intern_wording_rejects(title: str) -> None:
    accepted, reason = looks_like_internship(title)
    assert accepted is False
    assert "non-internship wording" in reason


@pytest.mark.parametrize(
    "title",
    [
        "Product Manager",  # no evidence either way: keep
        "Associate Product Manager",
        "Business Analyst",
        "Technology Consulting Analyst",
        "Product Manager, Summer 2027",
        "Leadership Development Program",  # "lead" must be a whole word
        "Executive Assistant Intern",
    ],
)
def test_no_evidence_is_kept(title: str) -> None:
    assert looks_like_internship(title)[0] is True


def test_type_and_notes_can_supply_intern_evidence() -> None:
    assert looks_like_internship("Business Analyst", "Internship")[0] is True
    assert looks_like_internship("Senior Business Analyst", "Internship")[0] is True
    assert looks_like_internship("Business Analyst", "", "12 week internship")[0] is True
    assert looks_like_internship("Senior Business Analyst", "", "12 week internship")[0] is False
    assert looks_like_internship("Business Analyst", "Full-time")[0] is False


def test_extra_non_intern_keywords_come_from_config() -> None:
    assert looks_like_internship("Program Coordinator")[0] is True
    assert (
        looks_like_internship("Program Coordinator", extra_non_intern=["coordinator"])[0] is False
    )
    assert (
        looks_like_internship("Program Coordinator Intern", extra_non_intern=["coordinator"])[0]
        is True
    )
    assert (
        looks_like_internship("Postdoctoral Fellow", extra_non_intern=["postdoctoral", "phd"])[0]
        is False
    )


# --------------------------------------------------------------------------------------------- ATS


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/PM-Intern_R1",
            ATS.WORKDAY,
        ),
        ("https://wd1.myworkdaysite.com/recruiting/acme/External/job/x", ATS.WORKDAY),
        ("https://www.acme.wd3.myworkdayjobs.com/x", ATS.WORKDAY),
        ("https://boards.greenhouse.io/acme/jobs/123", ATS.GREENHOUSE),
        ("https://job-boards.greenhouse.io/acme/jobs/123", ATS.GREENHOUSE),
        ("https://boards.eu.greenhouse.io/acme/jobs/123", ATS.GREENHOUSE),
        ("https://acme.com/careers/open?gh_jid=4012345", ATS.GREENHOUSE),
        ("https://jobs.lever.co/acme/1111-2222", ATS.LEVER),
        ("https://jobs.eu.lever.co/acme/1111-2222/apply", ATS.LEVER),
        ("https://jobs.ashbyhq.com/acme/1111-2222", ATS.ASHBY),
        ("https://careers-acme.icims.com/jobs/1234/job", ATS.ICIMS),
        ("https://jobs.smartrecruiters.com/Acme/743999", ATS.SMARTRECRUITERS),
        ("https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=1", ATS.TALEO),
        ("https://career5.successfactors.com/career?company=acme", ATS.SUCCESSFACTORS),
        ("https://acme.sapsf.eu/x", ATS.SUCCESSFACTORS),
        (
            "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1",
            ATS.ORACLE,
        ),
        # employer hosted careers pages
        ("https://careers.tesla.com/en_US/careers/jobdetail/1", ATS.CUSTOM),
        ("https://jobs.cemex.com/us/en/job/1", ATS.CUSTOM),
        ("https://www.acme.com/careers/product-intern", ATS.CUSTOM),
        ("https://acme.com/en/jobs/12345", ATS.CUSTOM),
        ("https://acmecareers.com/x", ATS.CUSTOM),
        ("https://talent.acme.com/x", ATS.CUSTOM),
        ("http://127.0.0.1:8123/careers/jobs/1", ATS.CUSTOM),
        # everything else
        ("https://www.linkedin.com/jobs/view/3900000001", ATS.UNKNOWN),
        ("https://www.indeed.com/viewjob?jk=abc", ATS.UNKNOWN),
        ("https://jobs.jobvite.com/acme/job/1", ATS.UNKNOWN),
        ("https://example.test/anything", ATS.UNKNOWN),
        ("https://portal.example.test/", ATS.UNKNOWN),
        ("", ATS.UNKNOWN),
        ("   ", ATS.UNKNOWN),
        (None, ATS.UNKNOWN),
        ("not a url at all", ATS.UNKNOWN),
    ],
)
def test_detect_ats(url: str | None, expected: ATS) -> None:
    assert detect_ats(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://acme.wd5.myworkdayjobs.com.localhost:51234/en-US/External/job/x", ATS.WORKDAY),
        ("http://boards.greenhouse.io.localhost:1/acme/jobs/1", ATS.GREENHOUSE),
        ("http://jobs.lever.co.localhost:1/acme/1", ATS.LEVER),
        ("http://jobs.ashbyhq.com.localhost:1/acme/1", ATS.ASHBY),
        ("http://careers.example-employer.com.localhost:1/jobs/1", ATS.CUSTOM),
    ],
)
def test_detect_ats_understands_localhost_mock_hosts(url: str, expected: ATS) -> None:
    assert detect_ats(url) == expected


def test_detect_ats_does_not_confuse_lookalike_hosts() -> None:
    assert detect_ats("https://notlever.co/x") == ATS.UNKNOWN
    assert detect_ats("https://myworkdayjobs.com.evil.example/x") != ATS.WORKDAY
    assert detect_ats("https://greenhouse.io.example.test/x") == ATS.UNKNOWN


def test_is_aggregator_url() -> None:
    assert is_aggregator_url("https://www.linkedin.com/jobs/view/1")
    assert is_aggregator_url("https://uk.indeed.com/viewjob?jk=1")
    assert is_aggregator_url("https://app.joinhandshake.com/jobs/1")
    assert not is_aggregator_url("https://boards.greenhouse.io/acme/jobs/1")
    assert not is_aggregator_url("")
    assert not is_aggregator_url(None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Workday", ATS.WORKDAY),
        ("workday (Wells Fargo)", ATS.WORKDAY),
        ("Greenhouse", ATS.GREENHOUSE),
        ("Lever", ATS.LEVER),
        ("Ashby", ATS.ASHBY),
        ("AshbyHQ", ATS.ASHBY),
        ("iCIMS", ATS.ICIMS),
        ("Smart Recruiters", ATS.SMARTRECRUITERS),
        ("SmartRecruiters", ATS.SMARTRECRUITERS),
        ("Taleo", ATS.TALEO),
        ("SAP SuccessFactors", ATS.SUCCESSFACTORS),
        ("Oracle Recruiting Cloud", ATS.ORACLE),
        ("Custom", ATS.CUSTOM),
        ("Company site", ATS.CUSTOM),
        ("Employer portal", ATS.CUSTOM),
        ("Careers page", ATS.CUSTOM),
        ("", None),
        (None, None),
        ("???", None),
        ("Bespoke thing", None),
    ],
)
def test_parse_ats_hint(text: str | None, expected: ATS | None) -> None:
    assert parse_ats_hint(text) == expected


# --------------------------------------------------------------------------------------------- URLs and text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://jobs.acme.example/1", "https://jobs.acme.example/1"),
        ("http://acme.example.localhost:8000/x", "http://acme.example.localhost:8000/x"),
        ("  https://jobs.acme.example/1  ", "https://jobs.acme.example/1"),
        ("<https://jobs.acme.example/1>", "https://jobs.acme.example/1"),
        ("#https://jobs.acme.example/1#", "https://jobs.acme.example/1"),
        ("www.acme.example/careers/1", "https://www.acme.example/careers/1"),
        ("acme.example/careers/1", "https://acme.example/careers/1"),
        ("Apply at https://jobs.acme.example/1.", "https://jobs.acme.example/1"),
        ("(https://jobs.acme.example/a_(b))", "https://jobs.acme.example/a_(b)"),
        ("see https://a.example/1 or https://b.example/2", "https://a.example/1"),
        ("https://jobs.acme.example/1?x=1&y=2#frag", "https://jobs.acme.example/1?x=1&y=2#frag"),
        ("http://localhost:8000/x", "http://localhost:8000/x"),
    ],
)
def test_extract_url(text: str, expected: str) -> None:
    assert extract_url(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Apply",
        "click here",
        "mailto:hr@acme.example",
        "javascript:alert(1)",
        "#Sheet2!A1",
        "file:///C:/x.pdf",
        "ftp://acme.example/x",
        "/careers/1",
        "https://",
        "https://intranet/x",
        "TBD",
        "N/A",
        "https://acme.example/" + "x" * 5000,
        12345,
    ],
)
def test_extract_url_rejects_non_urls(text: object) -> None:
    assert extract_url(text) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, ""),
        ("  Product\u00a0 Intern \u200b", "Product Intern"),
        ("line one\nline two\r\nline three", "line one line two line three"),
        ("N/A", ""),
        ("n/a", ""),
        ("TBD", ""),
        ("-", ""),
        ("\u2014", ""),
        ("#N/A", ""),
        ("#REF!", ""),
        ("#VALUE!", ""),
        (3.0, "3"),
        (34.5, "34.5"),
        (2027, "2027"),
        (datetime(2026, 9, 1, 10, 0), "2026-09-01"),
        (date(2026, 9, 1), "2026-09-01"),
        ("\ufeffBOM", "BOM"),
    ],
)
def test_clean_text(value: object, expected: str) -> None:
    assert clean_text(value) == expected


def test_clean_text_multiline_and_limit() -> None:
    assert clean_text("a  b\n\n\n\nc\r\nd", multiline=True) == "a b\n\nc\nd"
    text = clean_text("x" * 50, limit=10)
    assert len(text) == 10 and text.endswith("\u2026")
    assert clean_text("short", limit=10) == "short"


# --------------------------------------------------------------------------------------------- formulas


def test_hyperlink_formula_with_literals() -> None:
    assert parse_hyperlink_formula('=HYPERLINK("https://jobs.acme.example/1","Apply")') == (
        "https://jobs.acme.example/1",
        "Apply",
    )
    assert parse_hyperlink_formula(
        '=hyperlink( "https://jobs.acme.example/1" , "Apply here" )'
    ) == (
        "https://jobs.acme.example/1",
        "Apply here",
    )
    assert parse_hyperlink_formula('=HYPERLINK("https://jobs.acme.example/1")') == (
        "https://jobs.acme.example/1",
        None,
    )
    assert parse_hyperlink_formula('=HYPERLINK("https://jobs.acme.example/1";"Apply")') == (
        "https://jobs.acme.example/1",
        "Apply",
    )


def test_hyperlink_formula_with_quotes_commas_and_concatenation() -> None:
    cells = {(2, 8): "4711", (2, 1): "Acme"}

    def lookup(row: int, col: int) -> object:
        return cells.get((row, col))

    assert parse_hyperlink_formula('=HYPERLINK("https://x.example/j?a=1,2","Say ""hi""")') == (
        "https://x.example/j?a=1,2",
        'Say "hi"',
    )
    assert parse_hyperlink_formula(
        '=HYPERLINK("https://x.example/jobs/"&H2,"Apply "&A2)', lookup
    ) == (
        "https://x.example/jobs/4711",
        "Apply Acme",
    )
    assert parse_hyperlink_formula("=HYPERLINK(H2)", lookup) == (
        None,
        None,
    )  # a bare id is not a URL
    assert parse_hyperlink_formula('=HYPERLINK("https://x.example/"&Z9,"x")', lookup) == (
        None,
        "x",
    )  # unresolvable


@pytest.mark.parametrize(
    "formula",
    [
        "=A1+B1",
        '=CONCAT("a","b")',
        "not a formula",
        "",
        "=HYPERLINK()",
        '=IFERROR(HYPERLINK("https://x.example"),"")',
    ],
)
def test_other_formulas_are_not_hyperlinks(formula: str) -> None:
    assert parse_hyperlink_formula(formula) is None


# --------------------------------------------------------------------------------------------- sheet names


@pytest.mark.parametrize(
    "name",
    [
        "Verified Opportunities",
        "verified opportunities",
        "VERIFIED OPPORTUNITIES",
        "Verified-Opportunities",
        "verified_opportunities",
        "VerifiedOpportunities",
        "Verified  Opportunities ",
        "Verified Opps",
        "Verified Opportunity",
        "Verified Opportunities (2026)",
        "Opportunities - Verified",
    ],
)
def test_sheet_name_variants_score_high(name: str) -> None:
    assert score_sheet_name(name) >= 90


def test_sheet_name_scoring_orders_candidates() -> None:
    assert score_sheet_name("Opportunities") >= 50
    assert score_sheet_name("Verified") >= 50
    for junk in (
        "Summary",
        "Sheet1",
        "Rejected",
        "Notes",
        "Archive",
        "Rejected Opportunities",
        "Unverified Opportunities",
    ):
        assert score_sheet_name(junk) < 50, junk
    assert score_sheet_name("Verified Opportunities") > score_sheet_name("Opportunities")
    assert score_sheet_name("", "x") == 0
    assert score_sheet_name("Internships 2027", "internships 2027") == 100
