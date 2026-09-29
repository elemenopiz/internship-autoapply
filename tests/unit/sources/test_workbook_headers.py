"""Header recognition: alias tables, fuzzy matching, header-row detection, duplicate headers, overrides."""

from __future__ import annotations

import pytest

from autoapply.sources.workbook import (
    FIELD_ALIASES,
    FIELDS,
    HEADER_SCAN_ROWS,
    Cell,
    base_field,
    canonical_field,
    detect_header,
    match_header,
)


def grid(*rows: list[object]) -> list[list[Cell]]:
    return [[Cell(v) for v in row] for row in rows]


def test_every_alias_belongs_to_exactly_one_field() -> None:
    seen: dict[str, str] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            assert alias == alias.strip() and alias, (field_name, alias)
            assert alias not in seen, f"{alias!r} is in both {seen[alias]} and {field_name}"
            seen[alias] = field_name
    assert set(FIELDS) == set(FIELD_ALIASES)


def test_spec_alias_lists_are_covered() -> None:
    """docs/SPEC.md 5.3 lists these aliases; extensions are fine, omissions are not."""
    spec = {
        "company": ["Company", "Employer", "Organization"],
        "title": ["Role", "Position", "Job Title", "Title"],
        "url": ["URL", "Link", "Apply Link", "Application URL", "Job Link", "Posting"],
        "location": ["Location", "City"],
        "term": ["Term", "Season", "Internship Term", "Cohort"],
        "status": ["Status", "Open?", "State", "Open/Closed"],
        "posted": ["Posted", "Date Posted", "Date Added"],
        "verified": ["Last Verified", "Verified On", "Date Verified"],
        "deadline": ["Deadline"],
        "ats": ["ATS", "Platform"],
        "notes": ["Notes"],
        "description": ["Description"],
    }
    for expected_field, headers in spec.items():
        for header in headers:
            match = match_header(header)
            assert match is not None, header
            assert base_field(match.field) == expected_field, (header, match)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Company", "company"),
        ("company", "company"),
        ("COMPANY", "company"),
        ("  Company  ", "company"),
        ("Company:", "company"),
        ("Company?", "company"),
        ("Company Name", "company"),
        ("Company Name (legal)", "company"),
        ("Employer / Organization", "company"),
        ("Org.", "company"),
        ("Job-Title", "title"),
        ("job_title", "title"),
        ("JobTitle", "title"),
        ("Job Title (required)", "title"),
        ("Role / Position", "title"),
        ("Position*", "title"),
        ("Internship", "title"),
        ("URL", "url"),
        ("Link", "url"),
        ("Job Link", "url"),
        ("Posting", "url"),
        ("URL / Link", "url"),
        ("Apply Link", "apply_url"),
        ("apply_link", "apply_url"),
        ("Application URL", "apply_url"),
        ("Apply Here", "apply_url"),
        ("Location", "location"),
        ("City, State", "location"),
        ("Location (City, State)", "location"),
        ("Term", "term"),
        ("Term / Season", "term"),
        ("Internship Term", "term"),
        ("Cohort", "term"),
        ("Status", "status"),
        ("Open?", "status"),
        ("Open (Y/N)", "status"),
        ("Open/Closed", "status"),
        ("State", "status"),
        ("Closed?", "closed"),
        ("Filled?", "closed"),
        ("Posted", "posted"),
        ("Date Posted", "posted"),
        ("Date Posted (MM/DD)", "posted"),
        ("Last Verified", "verified"),
        ("last verified:", "verified"),
        ("Verified On", "verified"),
        ("Last Checked", "verified"),
        ("Deadline", "deadline"),
        ("Application Deadline (EST)", "deadline"),
        ("Apply By", "deadline"),
        ("ATS", "ats"),
        ("Platform", "ats"),
        ("Notes", "notes"),
        ("Notes / Comments", "notes"),
        ("Description", "description"),
        ("Job Type", "type"),
        # typos
        ("Last\nVerified", "verified"),  # a wrapped header cell
        ("  LAST   VERIFIED  ", "verified"),
        ("\u2705 Status", "status"),  # emoji decoration
        ("\U0001f4c5 Last Verified", "verified"),
        ("Link\u00a0", "url"),  # trailing non-breaking space
        ("Compnay", "company"),
        ("Locaton", "location"),
        ("Postion", "title"),
        ("Aplly Link", "apply_url"),
        ("Deadlin", "deadline"),
        ("Employr", "company"),
    ],
)
def test_match_header_recognises_variants(header: str, expected: str) -> None:
    match = match_header(header)
    assert match is not None, header
    assert match.field == expected


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "Company Size",  # a different column that merely mentions "company"
        "Company Location",  # ambiguous: two fields
        "Verified By",  # a person, not a date
        "Number of Open Positions",
        "Pay (hourly)",
        "Sponsorship",
        "Referral?",
        "Contact",
        "x" * 200,
        "Summer 2027 internship opportunities for students at the university of somewhere",
    ],
)
def test_match_header_leaves_unrelated_headers_alone(header: str) -> None:
    assert match_header(header) is None


@pytest.mark.parametrize("value", [None, 5, 3.5, True, ["Company"]])
def test_match_header_ignores_non_text(value: object) -> None:
    assert match_header(value) is None


def test_exact_alias_beats_fuzzy() -> None:
    exact = match_header("Company")
    fuzzy = match_header("Compnay")
    assert exact is not None and fuzzy is not None
    assert exact.score > fuzzy.score


def test_canonical_field_accepts_names_and_aliases() -> None:
    assert canonical_field("company") == "company"
    assert canonical_field("Role") == "title"
    assert canonical_field("apply_url") == "apply_url"
    assert canonical_field("Apply URL") == "apply_url"
    assert canonical_field("last verified") == "verified"
    assert canonical_field("nonsense") is None


STD = ["Company", "Role", "Link", "Location"]


@pytest.mark.parametrize("position", [1, 2, 4, 9, HEADER_SCAN_ROWS])
def test_header_row_may_sit_anywhere_in_the_first_15_rows(position: int) -> None:
    rows: list[list[object]] = [["Some title", None, None, None]] * (position - 1) + [STD]
    info = detect_header(grid(*rows))
    assert info is not None
    assert info.row == position
    assert info.headers == tuple(STD)
    assert info.fields["company"] == (0,)
    assert info.fields["title"] == (1,)
    assert info.fields["url"] == (2,)
    assert info.fields["location"] == (3,)


def test_header_beyond_row_15_is_not_found() -> None:
    rows: list[list[object]] = [["junk", None, None, None]] * HEADER_SCAN_ROWS + [STD]
    assert detect_header(grid(*rows)) is None


def test_needs_three_recognised_columns() -> None:
    assert detect_header(grid(["Company", "Role", "Pay", "Sponsorship"])) is None
    assert detect_header(grid(["Company", "Role", "Link"])) is not None


def test_three_url_columns_are_only_one_field() -> None:
    assert detect_header(grid(["Link", "URL", "Apply Link", "Company"])) is None


def test_first_qualifying_row_wins() -> None:
    info = detect_header(grid(["Company", "Role", "Link"], ["Employer", "Position", "URL", "City"]))
    assert info is not None and info.row == 1


def test_title_row_with_alias_words_is_not_a_header() -> None:
    info = detect_header(
        grid(
            ["Verified opportunities: company, role and link list", None, None],
            [None, None, None],
            ["Company", "Role", "Link"],
        )
    )
    assert info is not None and info.row == 3


def test_non_string_cells_never_form_a_header() -> None:
    assert detect_header(grid([1, 2, 3, 4], [True, False, None, 5.5])) is None


def test_duplicate_headers_keep_every_column_best_alias_first() -> None:
    info = detect_header(grid(["Role", "Title", "Company", "Link", "Notes", "Notes"]))
    assert info is not None
    assert info.fields["title"] == (1, 0)  # "title" is the preferred alias, "role" the fallback
    assert info.fields["notes"] == (4, 5)  # equal aliases: left to right
    assert info.primary_headers()["title"] == "Title"


def test_unmapped_headers_become_unique_extra_keys() -> None:
    info = detect_header(grid(["Company", "Role", "Link", "Pay", "Pay", "", "Pay"]))
    assert info is not None
    assert info.extras == {3: "Pay", 4: "Pay (2)", 6: "Pay (3)"}


def test_column_map_overrides_win_over_aliases() -> None:
    header = ["Firm Name", "Gig", "Where to click", "Company"]
    assert detect_header(grid(header)) is None  # only "Company" is recognised without help
    info = detect_header(
        grid(header), {"company": "Firm Name", "title": "Gig", "url": "Where to click"}
    )
    assert info is not None
    assert info.fields["company"][0] == 0  # the override outranks the plain "Company" column
    assert info.fields["title"] == (1,)
    assert info.fields["url"] == (2,)
    assert info.warnings == ()


def test_column_map_matches_headers_loosely_and_accepts_aliased_keys() -> None:
    info = detect_header(
        grid(["FIRM  name:", "gig", "Where To Click"]),
        {"employer": "firm name", "role": "GIG", "link": "where to click"},
    )
    assert info is not None
    assert set(info.fields) == {"company", "title", "url"}


def test_column_map_accepts_column_letters() -> None:
    info = detect_header(grid(["a", "b", "c", "Company"]), {"title": "B", "url": "C"})
    assert info is not None
    assert info.fields["title"] == (1,)
    assert info.fields["url"] == (2,)
    assert info.fields["company"] == (3,)


def test_column_map_problems_are_reported_not_fatal() -> None:
    info = detect_header(
        grid(["Company", "Role", "Link"]), {"bogus": "Company", "location": "Nowhere"}
    )
    assert info is not None
    assert any("unknown field 'bogus'" in w for w in info.warnings)
    assert any("'location'" in w for w in info.warnings)
