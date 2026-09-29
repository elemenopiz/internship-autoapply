"""Tokenisation, term parsing and internship-signal helpers used by scoring (and by the job-board filter)."""

from __future__ import annotations

import pytest

from autoapply.scoring import (
    Term,
    find_terms,
    parse_target_term,
    parse_term_field,
    signals_internship,
    tokenize,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Product Management Intern (Summer 2027)",
            ["product", "management", "intern", "summer", "2027"],
        ),
        ("Strategy & Operations Intern", ["strategy", "operation", "intern"]),
        ("Strategy and Operations Intern", ["strategy", "operation", "intern"]),
        ("Strategy/Operations Intern", ["strategy", "operation", "intern"]),
        ("  STRATEGY,   operations;;; intern!! ", ["strategy", "operation", "intern"]),
        ("Business Operations Analyst Intern", ["business", "operation", "analyst", "intern"]),
        ("Data Analytics Intern", ["data", "analytic", "intern"]),
        ("APM Intern", ["apm", "intern"]),
        ("TPM Interns", ["tpm", "intern"]),
        ("Product-Management Intern", ["product", "management", "intern"]),
        ("Café Analyst Intern", ["cafe", "analyst", "intern"]),
    ],
)
def test_tokenize_titles(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Analysts", ["analyst"]),
        ("Managers", ["manager"]),
        ("Strategies", ["strategy"]),
        ("Operations", ["operation"]),
        ("Processes", ["process"]),
        ("Business", ["business"]),  # ends in ss: untouched
        ("Analysis", ["analysis"]),  # ends in is: untouched
        ("Status", ["status"]),  # ends in us: untouched
        ("Ops", ["ops"]),  # too short to stem
        ("Internships", ["internship"]),
        ("Programming", ["programming"]),  # never collapses onto "program"
        ("Programs", ["program"]),
    ],
)
def test_plural_stemming_is_conservative(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("M.B.A. Summer Associate", ["mba", "summer", "associate"]),
        ("Ph.D. Research Intern", ["phd", "research", "intern"]),
        ("PhD Research Intern", ["phd", "research", "intern"]),
        ("U.S. Remote", ["us", "remote"]),
        ("Co-op Analyst", ["coop", "analyst"]),
        ("Co op Analyst", ["coop", "analyst"]),
        ("C++ and C# Intern", ["c++", "c#", "intern"]),
        ("Sr. Analyst", ["sr", "analyst"]),
    ],
)
def test_dotted_abbreviations_and_special_words(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "&", "the of a"])
def test_tokenize_empty_inputs(value: str | None) -> None:
    assert tokenize(value) == []


def test_tokenize_is_deterministic() -> None:
    text = "Strategy & Operations Intern (Summer 2027) - Austin, TX"
    assert tokenize(text) == tokenize(text)


# ------------------------------------------------------------------------------------------ terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Summer 2027", [Term("summer", 2027)]),
        ("summer 2027 product intern", [Term("summer", 2027)]),
        ("SUMMER 2027", [Term("summer", 2027)]),
        ("Summer of 2027", [Term("summer", 2027)]),
        ("Summer '27 Intern", [Term("summer", 2027)]),
        ("Summer ’27", [Term("summer", 2027)]),
        ("2027 Summer Analyst", [Term("summer", 2027)]),
        ("Autumn 2027", [Term("fall", 2027)]),
        ("Fall 2026 or Summer 2027", [Term("fall", 2026), Term("summer", 2027)]),
        ("Summer/Fall 2027", [Term("summer", 2027), Term("fall", 2027)]),
        ("Spring, Summer 2027", [Term("spring", 2027), Term("summer", 2027)]),
        ("Winter 2027-2028", [Term("winter", 2027)]),
    ],
)
def test_find_terms(text: str, expected: list[Term]) -> None:
    assert find_terms(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Summer Analyst",  # a lone season is an internship word, not a term
        "12 week summer program",  # "summer 12" must not become 2012
        "Class of 2027",  # a lone year is not a term in prose
        "Fall in love with data",
        "Summer 3027",
    ],
)
def test_find_terms_ignores_ambiguous_prose(text: str) -> None:
    assert find_terms(text) == []


def test_find_terms_none_and_a_year_never_claimed_twice() -> None:
    assert find_terms(None) == []
    # "2026" belongs to Fall; it must not also be read as "2026 Summer".
    assert find_terms("Fall 2026 Summer 2027 Intern") == [Term("fall", 2026), Term("summer", 2027)]


def test_term_field_accepts_partial_terms_but_prose_does_not() -> None:
    assert parse_term_field("Summer 2027") == [Term("summer", 2027)]
    assert parse_term_field("Summer") == [Term("summer", None)]
    assert parse_term_field("2027") == [Term(None, 2027)]
    assert parse_term_field("Internship") == []
    assert parse_term_field(None) == []
    assert parse_term_field("") == []


def test_target_term_parsing() -> None:
    assert parse_target_term("Summer 2027") == Term("summer", 2027)
    assert parse_target_term("summer 2027") == Term("summer", 2027)
    assert parse_target_term("Fall '27") == Term("fall", 2027)
    assert parse_target_term("2027") == Term(None, 2027)
    assert parse_target_term("whenever") is None
    assert parse_target_term("") is None


def test_term_matching_and_labels() -> None:
    target = Term("summer", 2027)
    assert Term("summer", 2027).matches(target)
    assert not Term("fall", 2027).matches(target)
    assert not Term("summer", 2026).matches(target)
    assert Term("summer", None).matches(target)  # a missing part is not a conflict
    assert Term(None, 2027).matches(target)
    assert not Term(None, 2026).matches(target)
    assert Term("summer", 2027).label() == "Summer 2027"
    assert Term(None, 2027).label() == "2027"
    assert Term("fall", None).label() == "Fall"


# ------------------------------------------------------------------------------------------ internship


@pytest.mark.parametrize(
    "text",
    [
        "Product Management Intern",
        "Product Management Interns",
        "Product Management Internship",
        "Product Management Internships (Summer 2027)",
        "INTERN - Strategy",
        "Data Analyst Co-op",
        "Data Analyst Coop",
        "Data Analyst Co op",
        "Technology Consulting Summer Analyst",
        "Summer Associate, Strategy",
        "Summer 2027 Analyst",
        "2027 Summer Analyst Program",
        "Summer Fellow",
        "Intern",
    ],
)
def test_internship_signals_positive(text: str) -> None:
    assert signals_internship(text)


@pytest.mark.parametrize(
    "text",
    [
        "Product Manager",
        "Internal Audit Analyst",  # "internal" is not "intern"
        "International Business Analyst",
        "Summer Sales Manager",
        "Analyst",
        "",
        None,
    ],
)
def test_internship_signals_negative(text: str | None) -> None:
    assert not signals_internship(text)


@pytest.mark.timeout(20)
@pytest.mark.parametrize("chunk", ["summer/", "summer-", "summer and ", "fall, "])
def test_adversarial_season_lists_do_not_blow_up(chunk: str) -> None:
    """Unbounded "summer/summer/..." chains once backtracked quadratically (minutes for 50k repeats)."""
    assert find_terms(chunk * 50_000) == []
    find_terms(chunk * 5_000 + " 2027")  # with a year at the end: finishes, result is unspecified
