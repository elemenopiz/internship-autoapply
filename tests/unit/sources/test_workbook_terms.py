"""Term cells and free-text term mentions: "Summer '27", "Sum 2027", "2027 Summer", application windows..."""

from __future__ import annotations

import pytest

from autoapply.sources.workbook import (
    Term,
    compare_term,
    decide_term,
    parse_target_term,
    parse_terms,
)

SUMMER_27 = Term("summer", 2027)


@pytest.mark.parametrize(
    "text",
    [
        "Summer 2027",
        "summer 2027",
        "SUMMER 2027",
        "Summer '27",
        "Summer ’27",  # typographic apostrophe
        "Summer’27",
        "Summer'27",
        "Sum 2027",
        "Sum. 2027",
        "Sum '27",
        "2027 Summer",
        "2027-Summer",
        "'27 Summer",
        "Summer, 2027",
        "Summer - 2027",
        "Summer of 2027",
        "Summer 2027 Internship",
        "Summer Internship 2027",
        "Summer Analyst Program 2027",
        "Summer2027",
        "SUMMER2027",
        "Summer 27",  # bare two-digit year hugging the season
        "Sum27",
        "Product Intern (Summer 2027)",
        "Product Intern - Summer 2027 (Austin)",
    ],
)
def test_summer_2027_in_many_spellings(text: str) -> None:
    assert SUMMER_27 in parse_terms(text)
    assert SUMMER_27 in parse_terms(text, strict=True)
    assert decide_term("Summer 2027", text, "", "").status == "ok"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fall 2026", [Term("fall", 2026)]),
        ("Autumn 2026", [Term("fall", 2026)]),
        ("Spring 2027", [Term("spring", 2027)]),
        ("Winter '28", [Term("winter", 2028)]),
        ("Summer 2028", [Term("summer", 2028)]),
        ("Summer/Fall 2027", [Term("summer", 2027), Term("fall", 2027)]),
        ("Summer & Fall 2027", [Term("summer", 2027), Term("fall", 2027)]),
        ("Summer or Fall 2027", [Term("summer", 2027), Term("fall", 2027)]),
        ("Summer 2026 - Summer 2027", [Term("summer", 2026), Term("summer", 2027)]),
        ("Spring 2027 - Summer 2027", [Term("spring", 2027), Term("summer", 2027)]),
        ("Summer", [Term("summer", None)]),
        ("2027", [Term(None, 2027)]),
        ("Summer 12-week program", [Term("summer", None)]),  # "12" is a duration, not a year
        ("Summer 10 weeks", [Term("summer", None)]),
        ("Summer 30 hrs", [Term("summer", None)]),
        ("Sum of parts", []),  # the word "sum"
        ("nothing to see", []),
        ("", []),
    ],
)
def test_parse_terms_lenient(text: str, expected: list[Term]) -> None:
    assert parse_terms(text) == expected


def test_parse_terms_strict_needs_season_and_year() -> None:
    assert parse_terms("Summer", strict=True) == []
    assert parse_terms("2027", strict=True) == []
    assert parse_terms("Spring Boot Developer Intern", strict=True) == []
    assert parse_terms(None, strict=True) == []


def test_strict_mode_ignores_application_windows() -> None:
    assert parse_terms("Applications open Fall 2026", strict=True) == []
    assert parse_terms("Apply by Fall 2026", strict=True) == []
    assert parse_terms("Deadline: Fall 2026", strict=True) == []
    assert parse_terms("Posted Fall 2026", strict=True) == []
    assert parse_terms("Applications open Fall 2026; internship runs Summer 2027", strict=True) == [
        SUMMER_27
    ]
    # a sentence break resets the window
    assert parse_terms("Applications are open. Summer 2027", strict=True) == [SUMMER_27]
    # the loose parser (a Term cell) does not care
    assert parse_terms("Applications open Fall 2026") == [Term("fall", 2026)]


def test_seasons_far_from_the_year_are_not_paired() -> None:
    text = "Summer analyst positions in Tokyo start January 2027"
    assert Term("summer", 2027) not in parse_terms(text)
    assert parse_terms("Summer Intern (Fall 2027 start)", strict=True) == [Term("fall", 2027)]


def test_target_term_parsing() -> None:
    assert parse_target_term("Summer 2027") == SUMMER_27
    assert parse_target_term("summer '27") == SUMMER_27
    assert parse_target_term("2027") == Term(None, 2027)
    assert parse_target_term("Fall 2026") == Term("fall", 2026)
    assert parse_target_term("Co-op") == Term(None, None)


def test_compare_term_verdicts() -> None:
    assert compare_term(SUMMER_27, [Term("summer", 2027)]) == "match"
    assert compare_term(SUMMER_27, [Term("fall", 2026), Term("summer", 2027)]) == "match"
    assert compare_term(SUMMER_27, [Term("summer", None)]) == "weak"
    assert compare_term(SUMMER_27, [Term(None, 2027)]) == "weak"
    assert compare_term(SUMMER_27, [Term("fall", 2026), Term("summer", None)]) == "weak"
    assert compare_term(SUMMER_27, [Term("fall", 2027)]) == "other"
    assert compare_term(SUMMER_27, [Term("summer", 2028)]) == "other"
    assert compare_term(SUMMER_27, [Term(None, 2026)]) == "other"
    assert compare_term(SUMMER_27, []) == "none"
    assert compare_term(Term(None, None), [SUMMER_27]) == "none"
    assert compare_term(Term(None, 2027), [Term("fall", 2027)]) == "match"  # year-only target


@pytest.mark.parametrize(
    ("cell", "title", "notes", "status"),
    [
        # a Term cell is authoritative and must match
        ("Summer 2027", "", "", "ok"),
        ("Fall 2026", "", "", "wrong"),
        ("Summer 2028", "", "", "wrong"),
        ("Spring 2027", "", "", "wrong"),
        ("Fall 2027", "", "", "wrong"),
        ("2026", "", "", "wrong"),
        ("Summer 2026 - Summer 2027", "", "", "ok"),
        ("Summer/Fall 2027", "", "", "ok"),
        # partial evidence: keep, flagged
        ("Summer", "", "", "assumed"),
        ("2027", "", "", "assumed"),
        # ... unless the title settles it either way
        ("Summer", "Product Intern - Summer 2027", "", "ok"),
        ("Summer", "Product Intern - Fall 2026", "", "wrong"),
        ("2027", "Product Intern Summer 2027", "", "ok"),
        # unrecognisable Term text falls back to title / notes
        ("Rolling", "Product Intern", "", "assumed"),
        ("TBD", "Product Intern - Summer 2027", "", "ok"),
        ("Rolling", "Product Intern - Fall 2026", "", "wrong"),
        # no Term cell: title / notes decide
        ("", "Product Intern - Summer 2027", "", "ok"),
        ("", "Product Intern (Summer '27)", "", "ok"),
        ("", "Product Intern - Fall 2026", "", "wrong"),
        ("", "Product Intern - Summer 2028", "", "wrong"),
        ("", "Product Intern", "Summer 2027 cohort", "ok"),
        ("", "Product Intern", "Fall 2026 cohort", "wrong"),
        ("", "Product Intern", "", "assumed"),
        ("", "", "", "assumed"),
        # application windows never count as the cohort
        ("", "Product Intern", "Applications open Fall 2026", "assumed"),
        ("", "Product Intern", "Applications open Fall 2026; role runs Summer 2027", "ok"),
        ("", "Product Intern", "Deadline Sep 2026", "assumed"),
        # the Term cell wins over a conflicting title
        ("Summer 2027", "Product Intern (Fall 2026 recruiting)", "", "ok"),
    ],
)
def test_decide_term(cell: str, title: str, notes: str, status: str) -> None:
    assert decide_term("Summer 2027", cell, title, notes).status == status


def test_decide_term_follows_the_configured_target() -> None:
    assert decide_term("Fall 2026", "Fall 2026", "", "").status == "ok"
    assert decide_term("Fall 2026", "Summer 2027", "", "").status == "wrong"
    assert decide_term("2027", "Summer 2027", "", "").status == "ok"
    assert decide_term("2027", "Fall 2027", "", "").status == "ok"  # year-only target: any season
    assert decide_term("2027", "Fall 2026", "", "").status == "wrong"


def test_unparseable_target_falls_back_to_plain_text() -> None:
    assert decide_term("Co-op", "Co-op", "", "").status == "ok"
    assert decide_term("Co-op", "Fall 2026", "", "").status == "wrong"
    assert decide_term("Co-op", "", "Product Intern", "").status == "assumed"


def test_wrong_term_details_name_the_offending_text() -> None:
    decision = decide_term("Summer 2027", "Fall 2026", "", "")
    assert "Fall 2026" in decision.detail and "Summer 2027" in decision.detail
