"""Greenhouse / Lever / Ashby board providers: parsing, filtering, isolation, HTTP hygiene.

The sandbox has no route to the real APIs, so every payload below is a realistic fixture written from the
public schemas and served through ``httpx.MockTransport`` (or respx in one test).
"""

from __future__ import annotations

import copy
import itertools
import json
import logging
import random
import warnings
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths
from autoapply.contracts import OpportunityProvider, SourceContext
from autoapply.models import ATS, Opportunity, OpportunitySource, SearchProfile
from autoapply.normalize import opportunity_id
from autoapply.scoring import score_opportunity
from autoapply.sources import boards
from autoapply.sources.boards import (
    PROVIDERS,
    AshbyProvider,
    BoardFetchError,
    GreenhouseProvider,
    LeverProvider,
    html_to_text,
    parse_ashby_jobs,
    parse_greenhouse_jobs,
    parse_lever_postings,
    prettify_token,
)

# ------------------------------------------------------------------------------------------ fixtures

GH_TOKEN = "acmerobotics"
GH_INTRO = (
    "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;Acme Robotics builds warehouse robots "
    "&amp;amp; the software that runs them.&lt;/p&gt;&lt;/div&gt;"
    "&lt;h3&gt;What you&amp;#39;ll do&lt;/h3&gt;"
    "&lt;ul&gt;&lt;li&gt;Own the roadmap for one feature area&lt;/li&gt;"
    "&lt;li&gt;Write SQL to size opportunities&lt;/li&gt;&lt;/ul&gt;"
    "&lt;p&gt;This internship runs Summer 2027 (May&amp;ndash;August).&lt;br&gt;Apply today!&lt;/p&gt;"
    "&lt;script&gt;window.track('x')&lt;/script&gt;"
)


def _gh_job(job_id: int, title: str, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": job_id,
        "internal_job_id": job_id - 1_000_000_000,
        "title": title,
        "updated_at": "2026-09-02T09:15:44-04:00",
        "requisition_id": f"REQ-{job_id}",
        "location": {"name": "Austin, TX"},
        "absolute_url": f"https://boards.greenhouse.io/{GH_TOKEN}/jobs/{job_id}",
        "content": "&lt;p&gt;A role at Acme.&lt;/p&gt;",
        "departments": [{"id": 101, "name": "Product", "child_ids": [], "parent_id": None}],
        "offices": [
            {
                "id": 201,
                "name": "Austin",
                "location": "Austin, TX, United States",
                "child_ids": [],
                "parent_id": None,
            }
        ],
        "metadata": None,
        "data_compliance": [],
    }
    job.update(overrides)
    return job


GH_PM_INTERN = _gh_job(
    5012345001,
    "Product Management Intern (Summer 2027)",
    first_published="2026-08-20T13:00:00-04:00",
    content=GH_INTRO,
    metadata=[
        {"id": 1, "name": "Employment Type", "value": "Intern", "value_type": "single_select"}
    ],
)
GREENHOUSE_JOBS: dict[str, Any] = {
    "jobs": [
        GH_PM_INTERN,
        _gh_job(5012345002, "Software Engineer, Backend"),
        _gh_job(
            5012345003,
            "Strategy & Operations Intern",
            location={"name": "Remote - US"},
            departments=[{"id": 102, "name": "Strategy", "child_ids": [], "parent_id": None}],
        ),
        _gh_job(5012345004, "Data Analytics Intern (Fall 2026)"),
        _gh_job(
            5012345005,
            "Technology Consulting Summer Analyst",
            location={"name": "New York, NY"},
            content="&lt;p&gt;Applications for Summer 2027 are open.&lt;/p&gt;",
        ),
        _gh_job(
            5012345006,
            "Business Operations Analyst",
            departments=[
                {"id": 103, "name": "University Interns", "child_ids": [], "parent_id": None}
            ],
        ),
        _gh_job(5012345007, "Senior Product Manager"),
    ],
    "meta": {"total": 7},
}
GREENHOUSE_BOARD = {"name": "Acme Robotics", "content": "&lt;p&gt;About us&lt;/p&gt;"}

LV_TOKEN = "globex-labs"
LV_CREATED_MS = 1_788_000_000_000  # an epoch-millisecond timestamp as Lever sends it


def _lv_posting(posting_id: str, title: str, **overrides: Any) -> dict[str, Any]:
    posting: dict[str, Any] = {
        "id": posting_id,
        "text": title,
        "createdAt": LV_CREATED_MS,
        "hostedUrl": f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}/apply",
        "categories": {
            "commitment": "Intern",
            "department": "Engineering",
            "location": "Austin, TX",
            "team": "Program Management",
            "allLocations": ["Austin, TX"],
        },
        "country": "US",
        "workplaceType": "hybrid",
        "description": "<div>Summer 2027 internship on our program team.</div>",
        "descriptionPlain": "Summer 2027 internship on our program team.",
        "lists": [
            {
                "text": "What you'll do:",
                "content": "<li>Coordinate launches</li><li>Track risks &amp; dependencies</li>",
            },
            {"text": "What we look for:", "content": "<li>Curiosity</li>"},
        ],
        "additional": "<div>Globex is an equal opportunity employer.</div>",
        "additionalPlain": "Globex is an equal opportunity employer.",
    }
    posting.update(overrides)
    return posting


LEVER_POSTINGS: list[dict[str, Any]] = [
    _lv_posting("0b2c4d6e-1111-4aaa-8bbb-000000000001", "Technical Program Manager Intern"),
    _lv_posting(
        "0b2c4d6e-1111-4aaa-8bbb-000000000002",
        "Product Manager",
        categories={
            "commitment": "Full-time",
            "department": "Product",
            "location": "Remote",
            "team": "Core",
            "allLocations": ["Remote"],
        },
    ),
    _lv_posting(
        "0b2c4d6e-1111-4aaa-8bbb-000000000003",
        "Business Analyst",
        categories={
            "commitment": "Internship",
            "department": "Operations",
            "location": "New York, NY",
            "team": "BizOps",
            "allLocations": ["New York, NY", "Austin, TX"],
        },
        workplaceType="remote",
        description="",
        descriptionPlain="",
    ),
    _lv_posting("0b2c4d6e-1111-4aaa-8bbb-000000000004", "Strategy Intern (Summer 2026)"),
]

AS_TOKEN = "initrode"


def _as_job(job_id: str, title: str, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": job_id,
        "title": title,
        "location": "Austin, TX",
        "secondaryLocations": [],
        "department": "Operations",
        "team": "Business Operations",
        "isListed": True,
        "isRemote": False,
        "workplaceType": "Hybrid",
        "descriptionHtml": "<p>Join <b>Initrode</b> for Summer 2027.</p><ul><li>Analyse funnels</li></ul>",
        "descriptionPlain": "Join Initrode for Summer 2027.\n- Analyse funnels",
        "publishedAt": "2026-09-03T15:20:00.000+00:00",
        "employmentType": "Intern",
        "address": {
            "postalAddress": {
                "addressLocality": "Austin",
                "addressRegion": "Texas",
                "addressCountry": "USA",
            }
        },
        "jobUrl": f"https://jobs.ashbyhq.com/{AS_TOKEN}/{job_id}",
        "applyUrl": f"https://jobs.ashbyhq.com/{AS_TOKEN}/{job_id}/application",
        "compensation": {
            "compensationTierSummary": "$38 - $44 / hour",
            "summaryComponents": [{"compensationType": "Salary", "currencyCode": "USD"}],
        },
    }
    job.update(overrides)
    return job


ASHBY_BOARD: dict[str, Any] = {
    "apiVersion": "1",
    "jobs": [
        _as_job(
            "7f1c2a10-0000-4000-8000-00000000000a",
            "Business Analyst Intern",
            secondaryLocations=[{"location": "New York, NY", "address": {}}],
        ),
        _as_job("7f1c2a10-0000-4000-8000-00000000000b", "Hidden Intern Role", isListed=False),
        _as_job(
            "7f1c2a10-0000-4000-8000-00000000000c",
            "Senior Product Manager",
            employmentType="FullTime",
            department="Product",
        ),
        _as_job(
            "7f1c2a10-0000-4000-8000-00000000000d",
            "Data Analyst",
            isRemote=True,
            location="",
            descriptionPlain="",
        ),
    ],
}

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


# ------------------------------------------------------------------------------------------ helpers

Handler = Callable[[httpx.Request], httpx.Response]


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


class Recorder:
    """Wraps a handler, remembering every request the provider sent."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _routes(routes: dict[str, Any]) -> Handler:
    """Handler serving ``{path: payload-or-Response-or-callable}``; anything else is a 404."""

    def handle(request: httpx.Request) -> httpx.Response:
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, json={"error": "not found"})
        if callable(target):
            return target(request)  # type: ignore[no-any-return]
        if isinstance(target, httpx.Response):
            return target
        return _json(target)

    return handle


def _ctx(
    handler: Handler,
    *,
    greenhouse: list[str] | None = None,
    lever: list[str] | None = None,
    ashby: list[str] | None = None,
    search: SearchProfile | None = None,
    clock: FakeClock | None = None,
    client: httpx.Client | None = None,
) -> tuple[SourceContext, Recorder]:
    recorder = Recorder(handler)
    config = AppConfig()
    config.boards.greenhouse = greenhouse or []
    config.boards.lever = lever or []
    config.boards.ashby = ashby or []
    if search is not None:
        config.search = search
    http = client or httpx.Client(transport=httpx.MockTransport(recorder))
    ctx = SourceContext(
        config=config, paths=AppPaths(Path("unused")), clock=clock or FakeClock(NOW), http=http
    )
    return ctx, recorder


def _greenhouse_routes(token: str = GH_TOKEN, jobs: Any = None, board: Any = None) -> Handler:
    return _routes(
        {
            f"/v1/boards/{token}/jobs": GREENHOUSE_JOBS if jobs is None else jobs,
            f"/v1/boards/{token}": GREENHOUSE_BOARD if board is None else board,
        }
    )


def _by_title(ops: list[Opportunity]) -> dict[str, Opportunity]:
    return {o.title: o for o in ops}


# ------------------------------------------------------------------------------------------ html_to_text


def test_greenhouse_entity_escaped_content_becomes_plain_text() -> None:
    text = html_to_text(GH_INTRO)
    assert "Acme Robotics builds warehouse robots & the software that runs them." in text
    assert "What you'll do" in text
    assert "- Own the roadmap for one feature area\n- Write SQL to size opportunities" in text
    assert "This internship runs Summer 2027 (May–August).\nApply today!" in text
    assert "window.track" not in text  # scripts never leak into descriptions
    assert "<" not in text and "&lt;" not in text and "&amp;" not in text


def test_double_escaped_content_is_unwrapped_once_more() -> None:
    once = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"
    twice = once.replace("&", "&amp;")
    assert html_to_text(once) == "Hello & welcome"
    assert html_to_text(twice) == "Hello & welcome"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>One</p><p>Two</p>", "One\n\nTwo"),
        ("Line one<br>Line two<br/>Line three", "Line one\nLine two\nLine three"),
        ("<div>a</div><div>b</div>", "a\nb"),
        ("Hello <b>bold</b> and <i>it</i>alic!", "Hello bold and italic!"),
        ("<ul><li>x</li><li>y</li></ul>", "- x\n- y"),
        ("  lots   of \t spaces here  ", "lots of spaces here"),
        ("<p>a</p>\n\n\n\n<p>b</p>", "a\n\nb"),
        ("Plain text, no tags.", "Plain text, no tags."),
        ("5 &lt; 6 and 7 &gt; 3", "5 < 6 and 7 > 3"),
        ("<style>p{color:red}</style><p>Styled</p>", "Styled"),
        ("<!-- hidden comment --><p>Visible</p>", "Visible"),
        ("<p>Unclosed <b>tags", "Unclosed tags"),
        ("<noscript>Enable JS</noscript>Real text", "Real text"),
    ],
)
def test_html_to_text_cases(raw: str, expected: str) -> None:
    assert html_to_text(raw) == expected


@pytest.mark.parametrize("value", [None, "", "   ", 5, 3.5, [], {}, True, b"<p>x</p>"])
def test_html_to_text_non_text_inputs(value: object) -> None:
    assert html_to_text(value) == ""


def test_html_to_text_is_safe_on_hostile_and_huge_input() -> None:
    evil = '<img src=x onerror="alert(1)"><a href="javascript:alert(2)">click</a><script>alert(3)</script>'
    text = html_to_text(evil)
    assert text == "click"
    huge = "<p>" + "word " * 400_000 + "</p>"
    assert len(html_to_text(huge)) <= boards.MAX_DESCRIPTION_CHARS


def test_url_or_filename_shaped_text_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert html_to_text("https://example.test/job") == "https://example.test/job"
        assert html_to_text("resume.pdf") == "resume.pdf"


# ------------------------------------------------------------------------------------------ small helpers


@pytest.mark.parametrize(
    ("token", "pretty"),
    [
        ("acme-corp", "Acme Corp"),
        ("acme_corp", "Acme Corp"),
        ("airbnb", "Airbnb"),
        ("SpaceX", "SpaceX"),
        ("IBM", "IBM"),
        ("  wells.fargo  ", "Wells Fargo"),
        ("3m", "3m"),
        ("a--b", "A B"),
        ("", ""),
    ],
)
def test_prettify_token(token: str, pretty: str) -> None:
    assert prettify_token(token) == pretty


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-01T14:33:22-04:00", date(2026, 9, 1)),
        ("2026-09-01T23:30:00-05:00", date(2026, 9, 2)),  # normalised to UTC
        ("2026-09-02T17:03:11.000+00:00", date(2026, 9, 2)),
        ("2026-09-02T17:03:11Z", date(2026, 9, 2)),
        ("2026-09-02", date(2026, 9, 2)),
        (1_788_000_000_000, datetime.fromtimestamp(1_788_000_000, UTC).date()),
        (1_788_000_000, datetime.fromtimestamp(1_788_000_000, UTC).date()),
        ("1788000000000", datetime.fromtimestamp(1_788_000_000, UTC).date()),
        (1_788_000_000_000.0, datetime.fromtimestamp(1_788_000_000, UTC).date()),
        ("yesterday", None),
        ("", None),
        (None, None),
        (True, None),
        ({}, None),
        (["2026-09-01"], None),
        (10**30, None),
        (-5, None),
        ("1999-12-31", None),  # implausible years are rejected, not stored
        ("2201-01-01", None),
        ("\u00b2\u00b3", None),  # str.isdigit() is true for superscripts, int() would raise
        ("9" * 5000, None),  # beyond Python's int-string limit
        ("9" * 40, None),
        (10**400, None),
        (float("inf"), None),
        (float("nan"), None),
    ],
)
def test_parse_date(value: object, expected: date | None) -> None:
    assert boards._parse_date(value) == expected


def test_token_validation_blocks_path_tricks_and_dedupes() -> None:
    raw = [
        "acme",
        " acme ",
        "",
        "  ",
        "../etc",
        "a/b",
        "a b",
        "ok-1.2_x",
        "acme",
        "x?y=1",
        None,
        5,
        "-lead",
    ]
    assert boards._valid_tokens(raw, "greenhouse") == ["acme", "ok-1.2_x", "5"]
    long_token = "a" * 101
    assert boards._valid_tokens([long_token, "a" * 100], "lever") == ["a" * 100]


# ------------------------------------------------------------------------------------------ parse: greenhouse


def test_parse_greenhouse_maps_every_field() -> None:
    [raw] = parse_greenhouse_jobs({"jobs": [GH_PM_INTERN]}, GH_TOKEN)
    assert raw.job_id == "5012345001"
    assert raw.title == "Product Management Intern (Summer 2027)"
    assert raw.url == raw.apply_url == f"https://boards.greenhouse.io/{GH_TOKEN}/jobs/5012345001"
    assert raw.location == "Austin, TX"
    assert raw.posted == date(2026, 8, 20), "first_published wins over updated_at"
    assert raw.department == "Product"
    assert raw.employment_type == "Intern"
    assert "Write SQL to size opportunities" in raw.description
    assert raw.extra["board_token"] == GH_TOKEN
    assert raw.extra["offices"] == ["Austin"]
    assert raw.extra["requisition_id"] == "REQ-5012345001"


def test_parse_greenhouse_falls_back_gracefully() -> None:
    bare = {"id": "abc", "title": "Intern", "offices": [{"name": "Denver", "location": ""}]}
    [raw] = parse_greenhouse_jobs({"jobs": [bare]}, "co")
    assert (
        raw.url == "https://boards.greenhouse.io/co/jobs/abc"
    )  # built when absolute_url is missing
    assert raw.location == "Denver"  # from the office when location is missing
    assert raw.posted is None and raw.description == "" and raw.department == ""
    dated = _gh_job(9, "X Intern", updated_at="2026-09-02T09:15:44-04:00", location=None)
    [raw] = parse_greenhouse_jobs({"jobs": [dated]}, "co")
    assert raw.posted == date(2026, 9, 2), "updated_at is used when first_published is absent"
    assert raw.location == "Austin, TX, United States"


def test_parse_greenhouse_metadata_and_company_name() -> None:
    job = _gh_job(
        11,
        "Intern",
        company_name="Acme Robotics Inc",
        metadata=[
            {"name": "Hiring Manager", "value": "Alex Rivera", "value_type": "short_text"},
            {"name": "Job Type", "value": ["Internship", "Seasonal"], "value_type": "multi_select"},
        ],
    )
    [raw] = parse_greenhouse_jobs({"jobs": [job]}, "co")
    assert raw.employment_type == "Internship, Seasonal"
    assert raw.company == "Acme Robotics Inc"


def test_parse_greenhouse_skips_bad_records_and_keeps_good_ones() -> None:
    jobs = [
        None,
        "nope",
        42,
        [],
        {},
        {"id": 1},  # no title
        {"title": "No id and no url"},  # nothing to build a URL from
        {"id": 2, "title": "Bad url", "absolute_url": "javascript:alert(1)"},
        {"id": 3, "title": "  ", "absolute_url": "https://boards.greenhouse.io/co/jobs/3"},
        _gh_job(4, "Good Intern", location="Austin, TX"),  # location as a plain string
        _gh_job(
            5, "Odd Types Intern", departments="Product", offices={"x": 1}, metadata=7, content=99
        ),
    ]
    raws = parse_greenhouse_jobs({"jobs": jobs}, "co")
    assert [r.title for r in raws] == ["Bad url", "Good Intern", "Odd Types Intern"]
    assert raws[0].url == "https://boards.greenhouse.io/co/jobs/2", (
        "unsafe URLs are replaced, not trusted"
    )
    assert raws[1].location == "Austin, TX"


@pytest.mark.parametrize(
    "payload", [None, [], "jobs", 5, {}, {"jobs": None}, {"jobs": {"a": 1}}, {"error": "x"}]
)
def test_parse_greenhouse_wrong_shape_raises_value_error(payload: object) -> None:
    with pytest.raises(ValueError, match="Greenhouse"):
        parse_greenhouse_jobs(payload, "co")


def test_parse_greenhouse_empty_board_is_fine() -> None:
    assert parse_greenhouse_jobs({"jobs": []}, "co") == []


# ------------------------------------------------------------------------------------------ parse: lever


def test_parse_lever_maps_every_field() -> None:
    [raw] = parse_lever_postings([LEVER_POSTINGS[0]], LV_TOKEN)
    posting_id = "0b2c4d6e-1111-4aaa-8bbb-000000000001"
    assert raw.job_id == posting_id
    assert raw.title == "Technical Program Manager Intern"
    assert raw.url == f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}"
    assert raw.apply_url == f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}/apply"
    assert raw.location == "Austin, TX"
    assert raw.posted == datetime.fromtimestamp(LV_CREATED_MS / 1000, UTC).date()
    assert (raw.department, raw.team, raw.employment_type) == (
        "Engineering",
        "Program Management",
        "Intern",
    )
    assert raw.extra["workplace_type"] == "hybrid"
    assert raw.company == "", "Lever never says the company name"


def test_parse_lever_description_includes_lists_and_additional_text() -> None:
    [raw] = parse_lever_postings([LEVER_POSTINGS[0]], LV_TOKEN)
    assert raw.description.startswith("Summer 2027 internship on our program team.")
    assert "What you'll do:\n- Coordinate launches\n- Track risks & dependencies" in raw.description
    assert "What we look for:\n- Curiosity" in raw.description
    assert raw.description.endswith("Globex is an equal opportunity employer.")


def test_parse_lever_falls_back_to_html_fields_and_all_locations() -> None:
    posting = _lv_posting(
        "p1",
        "Analyst Intern",
        descriptionPlain="",
        description="<p>HTML only &amp; proud</p>",
        additionalPlain="",
        additional="<p>Extra <b>info</b></p>",
        lists=None,
        categories={"commitment": "Intern", "allLocations": ["Austin, TX", "Denver, CO"]},
        hostedUrl=None,
        applyUrl=None,
    )
    [raw] = parse_lever_postings([posting], "co")
    assert raw.description == "HTML only & proud\n\nExtra info"
    assert raw.location == "Austin, TX, Denver, CO"
    assert raw.url == "https://jobs.lever.co/co/p1"
    assert raw.apply_url == "https://jobs.lever.co/co/p1/apply"


def test_parse_lever_remote_workplace_is_visible_in_the_location() -> None:
    remote = _lv_posting("r1", "Intern", workplaceType="remote")
    [raw] = parse_lever_postings([remote], "co")
    assert raw.location == "Austin, TX (Remote)"
    nowhere = _lv_posting(
        "r2", "Intern", workplaceType="remote", categories={"commitment": "Intern"}
    )
    [raw] = parse_lever_postings([nowhere], "co")
    assert raw.location == "Remote"
    already = _lv_posting(
        "r3", "Intern", workplaceType="remote", categories={"location": "Remote - US"}
    )
    [raw] = parse_lever_postings([already], "co")
    assert raw.location == "Remote - US"


def test_parse_lever_skips_bad_records() -> None:
    postings = [None, 3, "x", {}, {"id": "a"}, {"text": "No id"}, _lv_posting("ok", "Fine Intern")]
    assert [r.title for r in parse_lever_postings(postings, "co")] == ["Fine Intern"]


def test_parse_lever_accepts_the_paginated_envelope() -> None:
    wrapped = {"data": [LEVER_POSTINGS[0]], "hasNext": False}
    assert len(parse_lever_postings(wrapped, LV_TOKEN)) == 1


@pytest.mark.parametrize(
    "payload", [None, {}, {"ok": False, "error": "Document not found"}, "x", 5]
)
def test_parse_lever_wrong_shape_raises_value_error(payload: object) -> None:
    with pytest.raises(ValueError, match="Lever"):
        parse_lever_postings(payload, "co")


@pytest.mark.parametrize("created", [None, "soon", -1, 10**30, {}, True])
def test_parse_lever_bad_dates_leave_posted_date_empty(created: object) -> None:
    [raw] = parse_lever_postings([_lv_posting("d1", "Intern", createdAt=created)], "co")
    assert raw.posted is None


# ------------------------------------------------------------------------------------------ parse: ashby


def test_parse_ashby_maps_every_field_and_skips_unlisted() -> None:
    raws = parse_ashby_jobs(ASHBY_BOARD, AS_TOKEN)
    assert [r.title for r in raws] == [
        "Business Analyst Intern",
        "Senior Product Manager",
        "Data Analyst",
    ]
    first = raws[0]
    assert first.job_id == "7f1c2a10-0000-4000-8000-00000000000a"
    assert first.url == f"https://jobs.ashbyhq.com/{AS_TOKEN}/{first.job_id}"
    assert first.apply_url == f"https://jobs.ashbyhq.com/{AS_TOKEN}/{first.job_id}/application"
    assert first.location == "Austin, TX; New York, NY"
    assert first.posted == date(2026, 9, 3)
    assert first.department == "Operations" and first.team == "Business Operations"
    assert first.employment_type == "Intern"
    assert first.description == "Join Initrode for Summer 2027.\n- Analyse funnels"
    assert first.extra["compensation"] == "$38 - $44 / hour"
    assert first.extra["workplace_type"] == "Hybrid"


def test_parse_ashby_uses_html_when_plain_text_is_missing_and_flags_remote() -> None:
    raws = parse_ashby_jobs(ASHBY_BOARD, AS_TOKEN)
    remote = raws[2]
    assert remote.location == "Remote"
    assert remote.description == "Join Initrode for Summer 2027.\n\n- Analyse funnels"


def test_parse_ashby_urls_fall_back_to_the_board_pattern() -> None:
    job = _as_job("abc", "Intern", jobUrl=None, applyUrl="ftp://bad")
    [raw] = parse_ashby_jobs({"jobs": [job]}, "co")
    assert raw.url == "https://jobs.ashbyhq.com/co/abc"
    assert raw.apply_url == "https://jobs.ashbyhq.com/co/abc/application"


def test_parse_ashby_skips_bad_records_and_tolerates_odd_types() -> None:
    jobs = [
        None,
        1,
        "x",
        {},
        {"id": "a"},
        {"title": "no id"},
        _as_job("ok", "Fine Intern", compensation="n/a"),
    ]
    assert [r.title for r in parse_ashby_jobs({"jobs": jobs}, "co")] == ["Fine Intern"]
    assert parse_ashby_jobs({"jobs": [_as_job("z", "Z Intern", isListed=None)]}, "co")


@pytest.mark.parametrize("payload", [None, [], {}, {"jobs": "x"}, {"apiVersion": "1"}])
def test_parse_ashby_wrong_shape_raises_value_error(payload: object) -> None:
    with pytest.raises(ValueError, match="Ashby"):
        parse_ashby_jobs(payload, "co")


def test_parsers_never_raise_on_random_json_records() -> None:
    """Fuzz: arbitrary junk inside a well-shaped envelope yields only skipped records, never an exception."""
    rng = random.Random(7)

    def junk(depth: int = 0) -> Any:
        kind = rng.randrange(8 if depth < 3 else 5)
        if kind == 0:
            return None
        if kind == 1:
            return rng.choice([True, False])
        if kind == 2:
            return rng.randrange(-(10**12), 10**12)
        if kind == 3:
            return rng.choice(["", "x", "https://a.test/b", "2026-09-01", "☃", "<p>hi</p>"])
        if kind == 4:
            return rng.random() * 1e15
        if kind == 5:
            return [junk(depth + 1) for _ in range(rng.randrange(3))]
        keys = [
            "id",
            "title",
            "text",
            "absolute_url",
            "hostedUrl",
            "jobUrl",
            "location",
            "categories",
            "offices",
            "departments",
            "metadata",
            "lists",
            "createdAt",
            "publishedAt",
            "isListed",
            "secondaryLocations",
            "compensation",
            "content",
            "description",
            "descriptionPlain",
        ]
        return {rng.choice(keys): junk(depth + 1) for _ in range(rng.randrange(1, 8))}

    for _ in range(400):
        records = [junk() for _ in range(rng.randrange(1, 6))]
        parse_greenhouse_jobs({"jobs": records}, "co")
        parse_lever_postings(records, "co")
        parse_ashby_jobs({"jobs": records}, "co")


# ------------------------------------------------------------------------------------------ providers: basics


def test_providers_expose_one_provider_per_platform() -> None:
    assert [p.name for p in PROVIDERS] == ["greenhouse", "lever", "ashby"]
    assert [type(p) for p in PROVIDERS] == [GreenhouseProvider, LeverProvider, AshbyProvider]
    for provider in PROVIDERS:
        assert callable(provider.enabled) and callable(provider.fetch)


def test_providers_satisfy_the_provider_protocol() -> None:
    providers: list[OpportunityProvider] = [GreenhouseProvider(), LeverProvider(), AshbyProvider()]
    assert {p.name for p in providers} == {"greenhouse", "lever", "ashby"}


@pytest.mark.parametrize("platform", ["greenhouse", "lever", "ashby"])
def test_enabled_needs_the_platform_toggle_and_a_token(platform: str) -> None:
    provider = next(p for p in PROVIDERS if p.name == platform)
    config = AppConfig()
    assert config.platforms.model_dump()[platform] is True
    assert not provider.enabled(config), "no tokens configured"
    setattr(config.boards, platform, ["acme"])
    assert provider.enabled(config)
    setattr(config.platforms, platform, False)
    assert not provider.enabled(config), "toggle off wins over tokens"
    setattr(config.platforms, platform, True)
    setattr(config.boards, platform, ["", "   ", "../etc", "a/b"])
    assert not provider.enabled(config), "blank or invalid tokens do not count"
    setattr(config.boards, platform, ["", "acme"])
    assert provider.enabled(config)


def test_other_platforms_tokens_do_not_enable_a_provider() -> None:
    config = AppConfig()
    config.boards.lever = ["globex-labs"]
    assert LeverProvider().enabled(config)
    assert not GreenhouseProvider().enabled(config)
    assert not AshbyProvider().enabled(config)


def test_fetch_without_tokens_makes_no_requests() -> None:
    ctx, rec = _ctx(_greenhouse_routes())
    assert GreenhouseProvider().fetch(ctx) == []
    assert rec.requests == []


# ------------------------------------------------------------------------------------------ providers: greenhouse


def test_greenhouse_fetch_requests_the_documented_urls_and_keeps_only_internships() -> None:
    ctx, rec = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    ops = GreenhouseProvider().fetch(ctx)
    assert rec.urls == [
        f"https://boards-api.greenhouse.io/v1/boards/{GH_TOKEN}/jobs?content=true",
        f"https://boards-api.greenhouse.io/v1/boards/{GH_TOKEN}",
    ]
    assert [o.title for o in ops] == [
        "Product Management Intern (Summer 2027)",
        "Strategy & Operations Intern",
        "Technology Consulting Summer Analyst",
        "Business Operations Analyst",  # kept via its "University Interns" department
    ]


def test_greenhouse_opportunity_fields() -> None:
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    op = _by_title(GreenhouseProvider().fetch(ctx))["Product Management Intern (Summer 2027)"]
    url = f"https://boards.greenhouse.io/{GH_TOKEN}/jobs/5012345001"
    assert op.company == "Acme Robotics"  # the board's own name
    assert (op.source, op.ats) == (OpportunitySource.GREENHOUSE, ATS.GREENHOUSE)
    assert op.url == op.apply_url == url
    assert op.location == "Austin, TX"
    assert op.term == "Summer 2027"
    assert op.posted_date == date(2026, 8, 20)
    assert op.last_verified == date(2026, 9, 29)
    assert op.is_open is True
    assert op.description is not None and "Write SQL to size opportunities" in op.description
    assert "<" not in op.description and "&amp;" not in op.description
    assert op.id == opportunity_id(url, "Acme Robotics", op.title, "Austin, TX")
    assert op.extra["board_token"] == GH_TOKEN
    assert op.extra["job_id"] == "5012345001"
    assert op.extra["department"] == "Product"
    assert op.extra["employment_type"] == "Intern"
    assert op.extra["offices"] == ["Austin"]
    json.dumps(op.extra)  # JSON-serialisable: it goes into SQLite


def test_greenhouse_term_is_only_set_when_the_text_names_it() -> None:
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    ops = _by_title(GreenhouseProvider().fetch(ctx))
    assert (
        ops["Strategy & Operations Intern"].term is None
    )  # says no term anywhere: kept, term unknown
    assert ops["Technology Consulting Summer Analyst"].term == "Summer 2027"  # from the description
    assert ops["Business Operations Analyst"].extra["department"] == "University Interns"


def test_greenhouse_board_name_is_fetched_only_when_something_is_kept() -> None:
    nothing = {"jobs": [_gh_job(1, "Software Engineer")]}
    ctx, rec = _ctx(_greenhouse_routes(jobs=nothing), greenhouse=[GH_TOKEN])
    assert GreenhouseProvider().fetch(ctx) == []
    assert len(rec.requests) == 1


@pytest.mark.parametrize(
    ("board_response", "job_company", "expected"),
    [
        (httpx.Response(404, json={"error": "nope"}), "", "Acmerobotics"),
        (httpx.Response(500), "", "Acmerobotics"),
        (httpx.Response(200, content=b"not json"), "", "Acmerobotics"),
        (_json({"name": ""}), "", "Acmerobotics"),
        (_json({"content": "no name"}), "", "Acmerobotics"),
        (_json(["unexpected"]), "", "Acmerobotics"),
        (httpx.Response(404), "Acme From Job Record", "Acme From Job Record"),
        (_json({"name": "Acme Robotics"}), "Ignored Record Name", "Acme Robotics"),
    ],
)
def test_greenhouse_company_name_fallbacks(
    board_response: httpx.Response, job_company: str, expected: str
) -> None:
    jobs = {"jobs": [_gh_job(1, "Product Intern", company_name=job_company)]}
    routes = {f"/v1/boards/{GH_TOKEN}/jobs": jobs, f"/v1/boards/{GH_TOKEN}": board_response}
    ctx, _ = _ctx(_routes(routes), greenhouse=[GH_TOKEN])
    [op] = GreenhouseProvider().fetch(ctx)
    assert op.company == expected


# ------------------------------------------------------------------------------------------ providers: lever


def test_lever_fetch_requests_the_documented_url_and_maps_fields() -> None:
    ctx, rec = _ctx(_routes({f"/v0/postings/{LV_TOKEN}": LEVER_POSTINGS}), lever=[LV_TOKEN])
    ops = LeverProvider().fetch(ctx)
    assert rec.urls == [f"https://api.lever.co/v0/postings/{LV_TOKEN}?mode=json"]
    assert [o.title for o in ops] == ["Technical Program Manager Intern", "Business Analyst"]
    tpm, analyst = ops
    posting_id = "0b2c4d6e-1111-4aaa-8bbb-000000000001"
    assert tpm.company == "Globex Labs"  # Lever has no board name: prettified token
    assert (tpm.source, tpm.ats) == (OpportunitySource.LEVER, ATS.LEVER)
    assert tpm.url == f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}"
    assert tpm.apply_url == f"https://jobs.lever.co/{LV_TOKEN}/{posting_id}/apply"
    assert tpm.posted_date == datetime.fromtimestamp(LV_CREATED_MS / 1000, UTC).date()
    assert tpm.term == "Summer 2027"
    assert tpm.extra["employment_type"] == "Intern"
    assert tpm.extra["department"] == "Engineering" and tpm.extra["team"] == "Program Management"
    assert tpm.description is not None and "Coordinate launches" in tpm.description
    assert analyst.extra["employment_type"] == "Internship"
    assert analyst.location == "New York, NY (Remote)"
    assert analyst.term is None
    # the /apply suffix is the same job for dedup purposes
    assert tpm.id == opportunity_id(tpm.url, tpm.company, tpm.title, tpm.location)


# ------------------------------------------------------------------------------------------ providers: ashby


def test_ashby_fetch_requests_the_documented_url_and_maps_fields() -> None:
    ctx, rec = _ctx(_routes({f"/posting-api/job-board/{AS_TOKEN}": ASHBY_BOARD}), ashby=[AS_TOKEN])
    ops = AshbyProvider().fetch(ctx)
    assert rec.urls == [
        f"https://api.ashbyhq.com/posting-api/job-board/{AS_TOKEN}?includeCompensation=true"
    ]
    assert [o.title for o in ops] == ["Business Analyst Intern", "Data Analyst"]
    first = ops[0]
    assert first.company == "Initrode"
    assert (first.source, first.ats) == (OpportunitySource.ASHBY, ATS.ASHBY)
    assert first.url == f"https://jobs.ashbyhq.com/{AS_TOKEN}/7f1c2a10-0000-4000-8000-00000000000a"
    assert first.apply_url == first.url + "/application"
    assert first.location == "Austin, TX; New York, NY"
    assert first.posted_date == date(2026, 9, 3)
    assert first.term == "Summer 2027"
    assert first.extra["employment_type"] == "Intern"
    assert first.extra["compensation"] == "$38 - $44 / hour"
    assert "Analyse funnels" in (first.description or "")
    assert ops[1].location == "Remote"


# ------------------------------------------------------------------------------------------ filters


def _lever_ctx(postings: list[dict[str, Any]], **kwargs: Any) -> SourceContext:
    ctx, _ = _ctx(_routes({"/v0/postings/acme": postings}), lever=["acme"], **kwargs)
    return ctx


_POSTING_IDS = itertools.count(1)


def _posting(
    title: str, *, department: str = "", team: str = "", commitment: str = ""
) -> dict[str, Any]:
    cats = {
        "department": department,
        "team": team,
        "commitment": commitment,
        "location": "Austin, TX",
    }
    return _lv_posting(
        f"id-{next(_POSTING_IDS)}",
        title,
        categories=cats,
        description="",
        descriptionPlain="",
        lists=[],
        additional="",
        additionalPlain="",
    )


@pytest.mark.parametrize(
    ("title", "department", "team", "commitment", "kept"),
    [
        ("Product Intern", "", "", "", True),
        ("Product Management Interns", "", "", "", True),
        ("Product Management Internship Program", "", "", "", True),
        ("INTERN - Strategy", "", "", "", True),
        ("Data Analyst Co-op", "", "", "", True),
        ("Data Analyst Coop", "", "", "", True),
        ("Technology Consulting Summer Analyst", "", "", "", True),
        ("Strategy Summer Associate", "", "", "", True),
        ("Business Analyst", "Interns", "", "", True),
        ("Business Analyst", "", "Summer Internship Program", "", True),
        ("Business Analyst", "", "", "Intern", True),
        ("Business Analyst", "", "", "Internship", True),
        ("Business Analyst", "", "", "Co-op", True),
        ("Business Analyst", "Operations", "Core", "Full-time", False),
        ("Product Manager", "Product", "", "", False),
        ("Internal Tools Engineer", "", "", "", False),
        ("International Business Analyst", "", "", "", False),
        ("Senior Data Scientist", "Data", "", "Full-time", False),
        ("Summer Sales Manager", "", "", "", False),
    ],
)
def test_internship_filter(
    title: str, department: str, team: str, commitment: str, kept: bool
) -> None:
    ctx = _lever_ctx([_posting(title, department=department, team=team, commitment=commitment)])
    assert (len(LeverProvider().fetch(ctx)) == 1) is kept


@pytest.mark.parametrize(
    ("title", "description", "kept", "term"),
    [
        ("Product Intern", "", True, None),
        ("Product Intern (Summer 2027)", "", True, "Summer 2027"),
        ("Product Intern", "The program runs Summer 2027.", True, "Summer 2027"),
        ("Product Intern", "the program runs summer 2027.", True, "Summer 2027"),
        ("Product Intern", "Program runs Summer '27 in Austin.", True, "Summer 2027"),
        ("Product Intern", "2027 Summer cohort", True, "Summer 2027"),
        ("Product Intern (Summer 2026)", "", False, None),
        ("Product Intern (Fall 2027)", "", False, None),
        ("Product Intern (Summer 2028)", "", False, None),
        ("Product Intern", "Applications for Fall 2026 are open.", False, None),
        ("Product Intern", "Last year's Summer 2026 interns loved it.", False, None),
        ("Product Intern", "Fall 2026 or Summer 2027 cohorts.", True, "Summer 2027"),
        ("Product Intern (Summer 2026)", "We also hire for Summer 2027.", False, None),
        ("Product Intern (Summer/Fall 2027)", "", True, "Summer 2027"),
        ("Product Intern", "Class of 2027 students welcome.", True, None),
        ("Product Intern", "A 12 week summer program.", True, None),
    ],
)
def test_term_filter_with_the_default_target(
    title: str, description: str, kept: bool, term: str | None
) -> None:
    posting = _posting(title, commitment="Intern")
    posting["descriptionPlain"] = description
    posting["description"] = f"<p>{description}</p>" if description else ""
    ops = LeverProvider().fetch(_lever_ctx([posting]))
    assert bool(ops) is kept
    if kept:
        assert ops[0].term == term


def test_term_filter_follows_a_custom_or_unparseable_target_term() -> None:
    summer = _posting("Product Intern (Summer 2027)", commitment="Intern")
    fall = _posting("Product Intern (Fall 2027)", commitment="Intern")
    ctx = _lever_ctx([summer, fall], search=SearchProfile(target_term="Fall 2027"))
    assert [o.title for o in LeverProvider().fetch(ctx)] == ["Product Intern (Fall 2027)"]
    assert LeverProvider().fetch(ctx)[0].term == "Fall 2027"
    ctx = _lever_ctx([summer, fall], search=SearchProfile(target_term="whenever"))
    ops = LeverProvider().fetch(ctx)
    assert [o.term for o in ops] == [None, None]  # no term rule: both kept, term unknown


# ------------------------------------------------------------------------------------------ dedupe


def test_the_same_job_listed_twice_in_one_board_is_returned_once() -> None:
    twice = {"jobs": [GH_PM_INTERN, copy.deepcopy(GH_PM_INTERN)]}
    ctx, _ = _ctx(_greenhouse_routes(jobs=twice), greenhouse=[GH_TOKEN])
    assert len(GreenhouseProvider().fetch(ctx)) == 1


def test_different_ids_pointing_at_the_same_url_are_returned_once() -> None:
    a = _gh_job(1, "Product Intern", absolute_url="https://boards.greenhouse.io/acme/jobs/1")
    b = _gh_job(
        2, "Product Intern", absolute_url="https://job-boards.greenhouse.io/acme/jobs/1?gh_src=x"
    )
    ctx, _ = _ctx(_greenhouse_routes(jobs={"jobs": [a, b]}), greenhouse=[GH_TOKEN])
    assert len(GreenhouseProvider().fetch(ctx)) == 1


def test_lever_hosted_and_apply_urls_are_the_same_job() -> None:
    hosted = _lv_posting("dup", "Product Intern", applyUrl="https://jobs.lever.co/acme/dup")
    other = _lv_posting("dup2", "Product Intern", hostedUrl="https://jobs.lever.co/acme/dup/apply")
    other["applyUrl"] = "https://jobs.lever.co/acme/dup/apply"
    ops = LeverProvider().fetch(_lever_ctx([hosted, other]))
    assert len(ops) == 1


def _lever_at(token: str, posting_id: str) -> dict[str, Any]:
    return _lv_posting(
        posting_id,
        "Product Intern",
        hostedUrl=f"https://jobs.lever.co/{token}/{posting_id}",
        applyUrl=f"https://jobs.lever.co/{token}/{posting_id}/apply",
    )


def test_the_same_posting_under_two_tokens_is_returned_once() -> None:
    routes = {
        "/v0/postings/one": [_lever_at("one", "x1")],
        "/v0/postings/two": [_lever_at("two", "x1")],
    }
    ctx, _ = _ctx(_routes(routes), lever=["one", "two"])
    assert len(LeverProvider().fetch(ctx)) == 2, "different URLs are different postings"
    routes["/v0/postings/two"] = [
        _lever_at("one", "x1")
    ]  # the very same posting URL under token two
    ctx, _ = _ctx(_routes(routes), lever=["one", "two"])
    assert len(LeverProvider().fetch(ctx)) == 1


def test_postings_with_distinct_ids_and_urls_are_all_kept_in_api_order() -> None:
    jobs = {"jobs": [_gh_job(n, f"Product Intern {n}") for n in (9, 3, 7, 1)]}
    ctx, _ = _ctx(_greenhouse_routes(jobs=jobs), greenhouse=[GH_TOKEN])
    assert [o.title for o in GreenhouseProvider().fetch(ctx)] == [
        "Product Intern 9",
        "Product Intern 3",
        "Product Intern 7",
        "Product Intern 1",
    ]


# ------------------------------------------------------------------------------------------ isolation


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow board", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _blocked(request: httpx.Request) -> httpx.Response:
    raise RuntimeError("External network access is blocked in tests: 'api.lever.co'")


BAD_RESPONSES: dict[str, Any] = {
    "404": httpx.Response(404, json={"ok": False, "error": "Document not found"}),
    "403": httpx.Response(403, text="forbidden"),
    "429": httpx.Response(429, headers={"Retry-After": "30"}),
    "500": httpx.Response(500, text="boom"),
    "503": httpx.Response(503),
    "html": httpx.Response(200, content=b"<html><body>maintenance</body></html>"),
    "empty": httpx.Response(200, content=b""),
    "truncated": httpx.Response(200, content=b'[{"id": "a", "text": "Intern"'),
    "bad-bytes": httpx.Response(200, content=b"\xff\xfe\x00garbage"),
    "wrong-shape": httpx.Response(200, json={"error": "unexpected"}),
    "null": httpx.Response(200, json=None),
    "timeout": _timeout,
    "refused": _refused,
    "guard": _blocked,
}


@pytest.mark.parametrize("failure", list(BAD_RESPONSES))
def test_one_failing_token_never_loses_the_others(failure: str) -> None:
    good = [_lever_at("good", "g1"), _lever_at("good", "g2")]
    routes = {"/v0/postings/bad": BAD_RESPONSES[failure], "/v0/postings/good": good}
    for tokens in (["bad", "good"], ["good", "bad"]):
        ctx, _ = _ctx(_routes(routes), lever=tokens)
        ops = LeverProvider().fetch(ctx)
        assert [o.extra["job_id"] for o in ops] == ["g1", "g2"], (failure, tokens)


@pytest.mark.parametrize("failure", list(BAD_RESPONSES))
def test_every_token_failing_raises_one_board_fetch_error_naming_them(failure: str) -> None:
    routes = {
        "/v0/postings/bad-one": BAD_RESPONSES[failure],
        "/v0/postings/bad-two": BAD_RESPONSES[failure],
    }
    ctx, _ = _ctx(_routes(routes), lever=["bad-one", "bad-two"])
    with pytest.raises(BoardFetchError) as excinfo:
        LeverProvider().fetch(ctx)
    message = str(excinfo.value)
    assert "lever" in message and "bad-one" in message and "bad-two" in message


def test_fetch_with_report_returns_errors_instead_of_raising() -> None:
    routes = {
        "/v0/postings/bad": BAD_RESPONSES["404"],
        "/v0/postings/good": [_lever_at("good", "g1")],
    }
    ctx, _ = _ctx(_routes(routes), lever=["bad", "good"])
    report = LeverProvider().fetch_with_report(ctx)
    assert [o.extra["job_id"] for o in report.opportunities] == ["g1"]
    assert report.errors == {"bad": "HTTP 404"}
    assert report.attempted == 2
    all_bad = LeverProvider().fetch_with_report(_ctx(_routes({}), lever=["x", "y"])[0])
    assert (
        all_bad.opportunities == [] and set(all_bad.errors) == {"x", "y"} and all_bad.attempted == 2
    )


def test_error_descriptions_are_short_and_secret_free() -> None:
    routes = {
        "/v0/postings/a": BAD_RESPONSES["timeout"],
        "/v0/postings/b": BAD_RESPONSES["refused"],
        "/v0/postings/c": BAD_RESPONSES["html"],
        "/v0/postings/d": BAD_RESPONSES["503"],
        "/v0/postings/e": BAD_RESPONSES["wrong-shape"],
    }
    ctx, _ = _ctx(_routes(routes), lever=list("abcde"))
    errors = LeverProvider().fetch_with_report(ctx).errors
    assert errors["a"] == "timeout"
    assert errors["b"].startswith("network error")
    assert errors["c"].startswith("invalid response")
    assert errors["d"] == "HTTP 503"
    assert "unexpected Lever response" in errors["e"]
    assert all(len(v) < 160 for v in errors.values())


def test_a_skipped_token_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    routes = {
        "/v0/postings/bad": BAD_RESPONSES["404"],
        "/v0/postings/good": [_lever_at("good", "g1")],
    }
    ctx, _ = _ctx(_routes(routes), lever=["bad", "good"])
    with caplog.at_level(logging.WARNING, logger="autoapply.sources"):
        LeverProvider().fetch(ctx)
    warnings_logged = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings_logged) == 1
    assert (
        "'bad'" in warnings_logged[0].getMessage() and "HTTP 404" in warnings_logged[0].getMessage()
    )


def test_greenhouse_jobs_failure_does_not_touch_the_next_board() -> None:
    routes = {
        "/v1/boards/gone/jobs": httpx.Response(404, json={"status": 404}),
        f"/v1/boards/{GH_TOKEN}/jobs": GREENHOUSE_JOBS,
        f"/v1/boards/{GH_TOKEN}": GREENHOUSE_BOARD,
    }
    ctx, rec = _ctx(_routes(routes), greenhouse=["gone", GH_TOKEN])
    ops = GreenhouseProvider().fetch(ctx)
    assert len(ops) == 4 and {o.company for o in ops} == {"Acme Robotics"}
    assert not any(url.endswith("/v1/boards/gone") for url in rec.urls), (
        "no board-name call after a failure"
    )


def test_malformed_records_inside_a_good_payload_are_skipped_not_fatal() -> None:
    jobs = {"jobs": [None, "junk", {"id": 1}, _gh_job(2, "Product Intern"), 17, {"title": "x"}]}
    ctx, _ = _ctx(_greenhouse_routes(jobs=jobs), greenhouse=[GH_TOKEN])
    assert [o.title for o in GreenhouseProvider().fetch(ctx)] == ["Product Intern"]


def test_invalid_configured_tokens_are_never_requested() -> None:
    routes = {"/v0/postings/good": [_lever_at("good", "g1")]}
    ctx, rec = _ctx(_routes(routes), lever=["../../admin", "a/b", "good", "x?y=1", "  "])
    assert [o.extra["job_id"] for o in LeverProvider().fetch(ctx)] == ["g1"]
    assert rec.urls == ["https://api.lever.co/v0/postings/good?mode=json"]


# ------------------------------------------------------------------------------------------ HTTP hygiene


def test_no_credentials_are_ever_sent_even_if_the_shared_client_carries_them() -> None:
    routes = {"/v0/postings/acme": [_lever_at("acme", "a1")]}
    recorder = Recorder(_routes(routes))
    client = httpx.Client(
        transport=httpx.MockTransport(recorder),
        headers={"Authorization": "Bearer SECRET-TOKEN", "Cookie": "session=SECRET-COOKIE"},
        cookies={"sid": "SECRET-SID"},
        auth=("alex.rivera@example.test", "SECRET-PASSWORD"),
    )
    ctx, _ = _ctx(recorder, lever=["acme"], client=client)
    assert len(LeverProvider().fetch(ctx)) == 1
    assert recorder.requests
    for request in recorder.requests:
        sent = {k.lower(): v for k, v in request.headers.items()}
        assert (
            "authorization" not in sent
            and "cookie" not in sent
            and "proxy-authorization" not in sent
        )
        assert "SECRET" not in str(request.url) and "SECRET" not in repr(sent)
        assert sent["accept"] == "application/json"
        assert sent["user-agent"].startswith("autoapply-internship-finder/")


def test_every_request_is_a_plain_get_with_an_explicit_timeout() -> None:
    ctx, rec = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    GreenhouseProvider().fetch(ctx)
    assert {r.method for r in rec.requests} == {"GET"}
    assert all(r.content == b"" for r in rec.requests)
    for request in rec.requests:
        timeout = request.extensions["timeout"]
        assert timeout["read"] == 20.0 and timeout["connect"] == 10.0


def _redirecting(location: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/postings/moved":
            return httpx.Response(301, headers={"Location": location})
        if request.url.path == "/v0/postings/new-home":
            return _json([_lever_at("new-home", "h1")])
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize(
    "location",
    [
        "https://api.lever.co/v0/postings/new-home?mode=json",  # absolute
        "/v0/postings/new-home?mode=json",  # relative
        "https://api.eu.lever.co/v0/postings/new-home?mode=json",  # another host
    ],
)
def test_redirects_are_followed_without_leaking_credentials(location: str) -> None:
    recorder = Recorder(_redirecting(location))
    client = httpx.Client(
        transport=httpx.MockTransport(recorder),
        headers={"Authorization": "Bearer SECRET"},
        cookies={"sid": "SECRET-SID"},
        auth=("alex.rivera@example.test", "SECRET-PASSWORD"),
    )
    ctx, _ = _ctx(recorder, lever=["moved"], client=client)
    assert [o.extra["job_id"] for o in LeverProvider().fetch(ctx)] == ["h1"]
    assert len(recorder.requests) == 2
    for request in recorder.requests:
        assert not {"authorization", "cookie"} & {k.lower() for k in request.headers}


def test_redirect_loops_fail_the_token_cleanly() -> None:
    loop = _routes(
        {"/v0/postings/moved": httpx.Response(302, headers={"Location": "/v0/postings/moved"})}
    )
    ctx, rec = _ctx(loop, lever=["moved"])
    errors = LeverProvider().fetch_with_report(ctx).errors
    assert list(errors) == ["moved"] and errors["moved"].startswith("network error")
    assert len(rec.requests) == 4, "the first request plus three redirects, then give up"


@pytest.mark.parametrize(
    "location",
    [
        "ftp://files.test/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://evil.test/v0/postings/new-home",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "https://api.lever.co.evil.test/v0/postings/new-home",  # suffix trick
        "https://notlever.co/v0/postings/new-home",
        "http://api.eu.lever.co/v0/postings/new-home",  # downgrade to http on another host
    ],
)
def test_redirects_to_other_hosts_or_schemes_are_refused_and_never_requested(location: str) -> None:
    recorder = Recorder(_redirecting(location))
    ctx, _ = _ctx(recorder, lever=["moved"])
    errors = LeverProvider().fetch_with_report(ctx).errors
    assert "refused" in errors["moved"]
    assert len(recorder.requests) == 1, "the redirect target must not be contacted"


# ------------------------------------------------------------------------------------------ response size


def test_oversized_responses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boards, "_MAX_RESPONSE_BYTES", 2_000)
    big = json.dumps([_lever_at("big", f"j{n}") for n in range(20)]).encode()
    assert len(big) > 2_000
    routes = {
        "/v0/postings/big": httpx.Response(200, content=big),
        "/v0/postings/declared": httpx.Response(
            200, content=b"[]", headers={"Content-Length": "999999"}
        ),
        "/v0/postings/small": [_lever_at("small", "s1")],
    }
    ctx, _ = _ctx(_routes(routes), lever=["big", "declared", "small"])
    report = LeverProvider().fetch_with_report(ctx)
    assert [o.extra["job_id"] for o in report.opportunities] == ["s1"]
    assert "too large" in report.errors["big"] and "too large" in report.errors["declared"]


def test_an_injected_client_is_shared_and_left_open() -> None:
    ctx, _ = _ctx(_routes({"/v0/postings/acme": [_lever_at("acme", "a1")]}), lever=["acme"])
    client = ctx.http
    assert client is not None
    LeverProvider().fetch(ctx)
    LeverProvider().fetch(ctx)
    assert not client.is_closed


def test_without_an_injected_client_a_private_one_is_created_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[httpx.Client] = []
    serving: dict[str, Any] = {"/v0/postings/acme": [_lever_at("acme", "a1")]}

    def factory() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(_routes(serving)))
        made.append(client)
        return client

    monkeypatch.setattr(boards, "_new_client", factory)
    ctx, _ = _ctx(_routes({}), lever=["acme"])
    ctx.http = None
    assert len(LeverProvider().fetch(ctx)) == 1
    assert len(made) == 1 and made[0].is_closed
    # ...and it is closed even when every token fails
    serving.clear()
    with pytest.raises(BoardFetchError):
        LeverProvider().fetch(ctx)
    assert len(made) == 2 and made[1].is_closed


def test_the_default_private_client_is_polite() -> None:
    client = boards._new_client()
    try:
        assert client.headers["user-agent"].startswith("autoapply-internship-finder/")
        assert client.timeout.read == 20.0
        assert client.follow_redirects is False  # redirects are followed by hand, credential-free
        assert "authorization" not in client.headers
    finally:
        client.close()


@respx.mock
def test_works_with_respx_mocked_clients() -> None:
    respx.get(f"https://api.lever.co/v0/postings/{LV_TOKEN}", params={"mode": "json"}).respond(
        200, json=LEVER_POSTINGS
    )
    config = AppConfig()
    config.boards.lever = [LV_TOKEN]
    ctx = SourceContext(
        config=config, paths=AppPaths(Path("unused")), clock=FakeClock(NOW), http=httpx.Client()
    )
    ops = LeverProvider().fetch(ctx)
    assert [o.title for o in ops] == ["Technical Program Manager Intern", "Business Analyst"]


# ------------------------------------------------------------------------------------------ clock / config


def test_last_verified_is_the_fetch_day_in_the_users_timezone() -> None:
    late_utc = FakeClock(
        datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    )  # still Sept 29 in Chicago (UTC-5)
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN], clock=late_utc)
    assert {o.last_verified for o in GreenhouseProvider().fetch(ctx)} == {date(2026, 9, 29)}
    ctx.config.timezone = "Asia/Tokyo"  # UTC+9 -> already Sept 30
    assert {o.last_verified for o in GreenhouseProvider().fetch(ctx)} == {date(2026, 9, 30)}


def test_an_unknown_timezone_falls_back_to_the_utc_date() -> None:
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    ctx.config.timezone = "Not/AZone"
    assert {o.last_verified for o in GreenhouseProvider().fetch(ctx)} == {date(2026, 9, 29)}


# ------------------------------------------------------------------------------------------ with scoring


def test_board_opportunities_score_sensibly_with_the_default_search() -> None:
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    search = ctx.config.search
    results = {o.title: score_opportunity(o, search) for o in GreenhouseProvider().fetch(ctx)}
    assert results["Product Management Intern (Summer 2027)"].role_family == "product_management"
    assert results["Strategy & Operations Intern"].role_family == "strategy"
    assert results["Technology Consulting Summer Analyst"].role_family == "technology_consulting"
    assert results["Business Operations Analyst"].role_family == "business_operations"
    assert all(r.passed for r in results.values()), results


def test_employment_type_metadata_from_boards_vouches_for_manager_titles() -> None:
    posting = _lv_posting(
        "m1", "Product Manager", categories={"commitment": "Intern", "location": "Austin, TX"}
    )
    [op] = LeverProvider().fetch(_lever_ctx([posting]))
    result = score_opportunity(op, SearchProfile())
    assert result.passed and result.role_family == "product_management"
    full_time = _lv_posting("m2", "Product Manager", categories={"commitment": "Full-time"})
    assert LeverProvider().fetch(_lever_ctx([full_time])) == [], "never reaches the scorer"


def test_lever_remote_flag_reaches_the_scorers_remote_rule() -> None:
    posting = _lv_posting("r1", "Business Analyst Intern", workplaceType="remote")
    [op] = LeverProvider().fetch(_lever_ctx([posting]))
    assert op.location == "Austin, TX (Remote)"
    on = score_opportunity(op, SearchProfile(remote_ok=True))
    off = score_opportunity(op, SearchProfile(remote_ok=False))
    assert on.score > off.score


# ------------------------------------------------------------------------------------------ safety / scale


def test_unsafe_posting_urls_are_replaced_by_the_board_pattern() -> None:
    evil = _gh_job(77, "Product Intern", absolute_url="javascript:alert(document.cookie)")
    ctx, _ = _ctx(_greenhouse_routes(jobs={"jobs": [evil]}), greenhouse=[GH_TOKEN])
    [op] = GreenhouseProvider().fetch(ctx)
    assert op.url == op.apply_url == f"https://boards.greenhouse.io/{GH_TOKEN}/jobs/77"


def test_descriptions_are_plain_text_and_capped() -> None:
    body = (
        "&lt;p&gt;"
        + ("Lorem ipsum dolor sit amet. " * 5000)
        + "&lt;/p&gt;&lt;script&gt;steal()&lt;/script&gt;"
    )
    ctx, _ = _ctx(
        _greenhouse_routes(jobs={"jobs": [_gh_job(5, "Product Intern", content=body)]}),
        greenhouse=[GH_TOKEN],
    )
    [op] = GreenhouseProvider().fetch(ctx)
    assert op.description is not None
    assert len(op.description) <= boards.MAX_DESCRIPTION_CHARS
    assert "steal" not in op.description and "<" not in op.description


def test_a_large_board_is_handled_and_filtered_correctly() -> None:
    jobs = [
        _gh_job(n, f"Product Intern {n}" if n % 3 == 0 else f"Software Engineer {n}")
        for n in range(1, 901)
    ]
    ctx, _ = _ctx(_greenhouse_routes(jobs={"jobs": jobs}), greenhouse=[GH_TOKEN])
    ops = GreenhouseProvider().fetch(ctx)
    assert len(ops) == 300
    assert len({o.id for o in ops}) == 300


def test_fetching_twice_gives_identical_results() -> None:
    ctx, _ = _ctx(_greenhouse_routes(), greenhouse=[GH_TOKEN])
    first = [o.model_dump(mode="json") for o in GreenhouseProvider().fetch(ctx)]
    second = [o.model_dump(mode="json") for o in GreenhouseProvider().fetch(ctx)]
    assert first == second
