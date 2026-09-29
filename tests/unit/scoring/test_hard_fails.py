"""Hard fails: score 0, passed False, every cause explained in ``penalties`` (docs/SPEC.md section 5.4)."""

from __future__ import annotations

from datetime import date

import pytest

from autoapply.models import Opportunity, Profile, ScoreResult, SearchProfile
from autoapply.scoring import score_opportunity

SEARCH = SearchProfile()


def _op(title: str = "Product Management Intern", **fields: object) -> Opportunity:
    base: dict[str, object] = {
        "company": "Acme Corp",
        "title": title,
        "location": "Austin, TX",
        "term": "Summer 2027",
    }
    base.update(fields)
    return Opportunity(**base)  # type: ignore[arg-type]


def _assert_hard_fail(result: ScoreResult, *fragments: str) -> None:
    assert result.score == 0.0
    assert result.passed is False
    hard = [p for p in result.penalties if p.startswith("Hard fail:")]
    assert hard, result
    for fragment in fragments:
        assert any(fragment in p for p in hard), (fragment, hard)


def test_baseline_opportunity_passes() -> None:
    result = score_opportunity(_op(), SEARCH)
    assert result.passed and result.score > 0
    assert not any(p.startswith("Hard fail:") for p in result.penalties)


# ------------------------------------------------------------------------------------------ denylist


@pytest.mark.parametrize(
    ("company", "denied"),
    [
        ("Acme Corp", "Acme Corp"),
        ("ACME CORP", "acme corp"),
        ("Acme, Inc.", "Acme"),  # corporate suffix noise is ignored
        ("Acme", "Acme Inc"),
        ("Amazon Web Services", "Amazon"),  # a phrase inside the company name
        ("Meta Platforms, Inc.", "Meta"),
        ("Wells Fargo & Company", "Wells Fargo"),
        ("Café Rio LLC", "cafe rio"),
    ],
)
def test_denylisted_company_hard_fails(company: str, denied: str) -> None:
    search = SearchProfile(company_denylist=["Somebody Else", denied])
    _assert_hard_fail(score_opportunity(_op(company=company), search), "company_denylist", denied)


@pytest.mark.parametrize(
    ("company", "denied"),
    [
        ("Metabolic Health Inc", "Meta"),  # whole words only
        (
            "Acme Corp",
            "Acme Corp Holdings",
        ),  # the entry must fit inside the company, not vice versa
        ("Acme Corp", ""),
        ("Acme Corp", "   "),
        ("Acme Corp", "&"),
    ],
)
def test_denylist_needs_whole_word_matches(company: str, denied: str) -> None:
    result = score_opportunity(_op(company=company), SearchProfile(company_denylist=[denied]))
    assert result.passed, result


def test_denylist_beats_allowlist() -> None:
    search = SearchProfile(company_denylist=["Acme"], company_allowlist=["Acme"])
    _assert_hard_fail(score_opportunity(_op(), search), "denylist")


# ------------------------------------------------------------------------------------------ excluded words


@pytest.mark.parametrize(
    ("title", "keyword"),
    [
        ("Senior Product Manager Intern", "senior"),
        ("Sr. Product Manager Intern", "sr."),
        ("Staff Product Manager Intern", "staff"),
        ("Principal Product Manager Intern", "principal"),
        ("Director of Product Management Intern", "director"),
        ("VP Product Management Intern", "vp"),
        ("Vice President, Product Management Intern", "vice president"),
        ("PhD Product Management Intern", "phd"),
        ("Ph.D. Product Management Intern", "phd"),
        ("Postdoctoral Product Management Intern", "postdoctoral"),
        ("Postdoc Product Management Intern", "postdoc"),
        ("Product Management Intern - Senior", "senior"),
    ],
)
def test_default_excluded_title_keywords_hard_fail(title: str, keyword: str) -> None:
    _assert_hard_fail(score_opportunity(_op(title), SEARCH), "excluded keyword", keyword)


@pytest.mark.parametrize(
    "title",
    [
        "Product Management Intern (Rising Senior)",
        "Product Management Intern - Senior Year Students",
        "Staffing Product Management Intern",  # "staffing" is not "staff"
        "Leadership Product Management Intern",
        "Principles of Product Management Intern",
        "Product Management Intern, Class of 2028 Seniors",  # plural = students, not a job level
    ],
)
def test_excluded_keywords_are_whole_words_and_class_year_is_not_seniority(title: str) -> None:
    result = score_opportunity(_op(title), SEARCH)
    assert result.passed, result
    assert not any("excluded keyword" in p for p in result.penalties)


def test_custom_exclude_list_replaces_the_defaults() -> None:
    search = SearchProfile(exclude_title_keywords=["sales"])
    _assert_hard_fail(score_opportunity(_op("Sales Product Management Intern"), search), "sales")
    # "senior" is no longer excluded by this profile
    assert score_opportunity(_op("Senior Product Management Intern"), search).passed


def test_empty_exclude_list_excludes_nothing() -> None:
    search = SearchProfile(exclude_title_keywords=[])
    assert score_opportunity(_op("Senior Product Management Intern"), search).passed


def test_multiple_excluded_words_are_all_explained() -> None:
    result = score_opportunity(_op("Senior Staff Product Management Intern"), SEARCH)
    _assert_hard_fail(result, "'senior'", "'staff'")


# ------------------------------------------------------------------------------------------ closed


def test_closed_posting_hard_fails() -> None:
    _assert_hard_fail(score_opportunity(_op(is_open=False), SEARCH), "closed")


def test_deadline_only_applies_when_today_is_supplied() -> None:
    op = _op(deadline=date(2026, 9, 1))
    assert score_opportunity(op, SEARCH).passed  # no clock, no deadline check
    _assert_hard_fail(
        score_opportunity(op, SEARCH, today=date(2026, 9, 29)), "deadline", "2026-09-01"
    )
    assert score_opportunity(op, SEARCH, today=date(2026, 9, 1)).passed  # the deadline day itself
    assert score_opportunity(
        _op(deadline=date(2026, 10, 1)), SEARCH, today=date(2026, 9, 29)
    ).passed
    assert score_opportunity(_op(deadline=None), SEARCH, today=date(2030, 1, 1)).passed


# ------------------------------------------------------------------------------------------ wrong term


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"term": "Fall 2027"}, "Fall 2027"),
        ({"term": "Summer 2026"}, "Summer 2026"),
        ({"term": "Summer 2028"}, "Summer 2028"),
        ({"term": "2026"}, "2026"),
        ({"term": "Fall"}, "Fall"),
        ({"term": "Spring 2027, Fall 2027"}, "Spring 2027"),
        ({"title": "Fall 2027 Product Management Intern", "term": None}, "Fall 2027"),
        ({"title": "Product Management Intern (Summer 2026)", "term": None}, "Summer 2026"),
        ({"title": "Product Management Intern - Summer '28", "term": None}, "Summer 2028"),
        ({"title": "2026 Summer Product Management Intern", "term": None}, "Summer 2026"),
        # the title wins over a term column that says otherwise
        (
            {"title": "Product Management Intern (Summer 2026)", "term": "Summer 2027"},
            "Summer 2026",
        ),
    ],
)
def test_a_different_term_in_title_or_term_field_hard_fails(
    fields: dict[str, object], named: str
) -> None:
    title = str(fields.pop("title", "Product Management Intern"))
    _assert_hard_fail(
        score_opportunity(_op(title, **fields), SEARCH), "target term Summer 2027", named
    )


@pytest.mark.parametrize(
    ("title", "term"),
    [
        ("Product Management Intern (Summer 2027)", None),
        ("Product Management Intern", "Summer 2027"),
        ("Product Management Intern", "summer 2027"),
        ("Product Management Intern", "Summer"),  # season only: no conflict
        ("Product Management Intern", "2027"),  # year only: no conflict
        ("Product Management Intern", "Summer 2027, Fall 2027"),
        ("Product Management Intern (Summer/Fall 2027)", None),
        ("Product Management Intern (Fall 2026 or Summer 2027)", None),
        ("Product Management Intern - Summer '27", None),
        ("2027 Summer Product Management Intern", None),
        ("Product Management Intern", None),
        ("Product Management Intern", "Internship"),  # unparseable term text says nothing
    ],
)
def test_matching_or_absent_terms_do_not_fail(title: str, term: str | None) -> None:
    assert score_opportunity(_op(title, term=term), SEARCH).passed


def test_other_terms_only_in_the_description_are_a_penalty_not_a_hard_fail() -> None:
    result = score_opportunity(
        _op(term=None, description="Applications for Fall 2026 close soon."), SEARCH
    )
    assert result.score > 0
    assert any("only other terms" in p and "Fall 2026" in p for p in result.penalties)
    assert not any(p.startswith("Hard fail:") for p in result.penalties)
    mentions_target = score_opportunity(
        _op(term=None, description="Fall 2026 is full. Summer 2027 is open."), SEARCH
    )
    assert not any("only other terms" in p for p in mentions_target.penalties)


def test_unparseable_target_term_disables_the_term_rule() -> None:
    search = SearchProfile(target_term="whenever")
    result = score_opportunity(_op(term="Fall 2019"), search)
    assert result.passed
    assert any("not recognised" in r for r in result.reasons)


def test_a_custom_target_term_is_honoured() -> None:
    search = SearchProfile(target_term="Fall 2027")
    assert score_opportunity(_op(term="Fall 2027"), search).passed
    _assert_hard_fail(score_opportunity(_op(term="Summer 2027"), search), "Fall 2027")


# ------------------------------------------------------------------------------------------ non-intern


@pytest.mark.parametrize(
    ("title", "marker"),
    [
        ("Product Manager", "manager"),
        ("Technical Program Manager", "manager"),
        ("Lead Business Analyst", "lead"),
        ("Head of Strategy", "head"),
        ("Chief Strategy Officer", "chief"),
        ("Business Analyst II", "ii"),
        ("Business Analyst III", "iii"),
        ("Experienced Business Analyst", "experienced"),
        ("Full-Time Business Analyst", "full time"),
        ("Business Analyst - Full Time", "full time"),
        ("New Grad Business Analyst", "new grad"),
        ("Business Analyst, New Graduate", "new graduate"),
        ("Entry Level Business Analyst", "entry level"),
        ("Early Career Business Analyst", "early career"),
        ("Permanent Business Analyst", "permanent"),
    ],
)
def test_seniority_or_full_time_markers_without_an_internship_signal_hard_fail(
    title: str, marker: str
) -> None:
    _assert_hard_fail(score_opportunity(_op(title), SEARCH), f"'{marker}'", "internship")


@pytest.mark.parametrize(
    "title",
    [
        "Product Manager Intern",
        "Product Manager, Internship",
        "Product Manager Co-op",
        "Technical Program Manager Summer Analyst",
        "Business Analyst Intern - New Grad Pipeline",
        "Head of Strategy Summer Associate",
    ],
)
def test_the_same_markers_are_fine_when_the_title_says_intern(title: str) -> None:
    assert not any(
        p.startswith("Hard fail:") for p in score_opportunity(_op(title), SEARCH).penalties
    )


def test_employment_type_metadata_can_vouch_for_an_internship() -> None:
    plain = _op("Product Manager", extra={"employment_type": "Intern"})
    assert score_opportunity(plain, SEARCH).passed
    lever = _op("Product Manager", extra={"commitment": "Internship"})
    assert score_opportunity(lever, SEARCH).passed
    listed = _op("Product Manager", extra={"employment_type": ["Full-time", "Intern"]})
    assert score_opportunity(listed, SEARCH).passed
    dept = _op("Product Manager", extra={"department": "Summer Interns"})
    assert score_opportunity(dept, SEARCH).passed
    full = _op("Product Manager", extra={"employment_type": "FullTime"})
    _assert_hard_fail(score_opportunity(full, SEARCH), "'manager'")


def test_a_description_mentioning_internships_does_not_vouch_for_a_manager_title() -> None:
    result = score_opportunity(
        _op("Product Manager", description="Prior internship experience is a plus."), SEARCH
    )
    _assert_hard_fail(result, "'manager'")


def test_a_term_alone_does_not_vouch_for_a_manager_title() -> None:
    _assert_hard_fail(
        score_opportunity(_op("Strategy Manager", term="Summer 2027"), SEARCH), "'manager'"
    )


def test_non_manager_title_without_any_internship_word_is_penalised_not_failed() -> None:
    result = score_opportunity(_op("Business Analyst", term=None, location=None), SEARCH)
    assert result.score > 0
    assert not result.passed
    assert any("No internship signal" in p and "-20" in p for p in result.penalties)
    # with the target term stated, the same title is treated as a workbook-style internship row
    assert score_opportunity(_op("Business Analyst", term="Summer 2027"), SEARCH).passed


# ------------------------------------------------------------------------------------------ MBA / PhD


def test_mba_only_title_fails_without_an_mba_profile() -> None:
    op = _op("MBA Product Management Intern")
    _assert_hard_fail(score_opportunity(op, SEARCH), "MBA-only")
    _assert_hard_fail(
        score_opportunity(op, SEARCH, Profile(degree="B.S. Computer Science")), "MBA-only"
    )
    assert score_opportunity(op, SEARCH, Profile(degree="MBA")).passed
    assert score_opportunity(op, SEARCH, Profile(degree="M.B.A., Finance")).passed
    assert score_opportunity(op, SEARCH, Profile(degree="Master of Business Administration")).passed


def test_bachelor_of_business_administration_is_not_an_mba() -> None:
    op = _op("MBA Product Management Intern")
    _assert_hard_fail(
        score_opportunity(op, SEARCH, Profile(degree="Bachelor of Business Administration")), "MBA"
    )


def test_phd_only_title_fails_without_a_phd_profile_and_waives_the_default_keyword() -> None:
    op = _op("PhD Data Science Intern")
    _assert_hard_fail(score_opportunity(op, SEARCH), "phd")
    _assert_hard_fail(score_opportunity(op, SEARCH, Profile(degree="B.S. Statistics")), "phd")
    result = score_opportunity(op, SEARCH, Profile(degree="Ph.D. in Economics"))
    assert result.passed, result
    assert any("waived" in r and "phd" in r for r in result.reasons)
    assert score_opportunity(op, SEARCH, Profile(degree="Doctor of Philosophy")).passed
    assert score_opportunity(op, SEARCH, Profile(degree="Doctorate in Physics")).passed


def test_a_phd_profile_does_not_waive_other_exclusions() -> None:
    profile = Profile(degree="PhD")
    _assert_hard_fail(
        score_opportunity(_op("Senior PhD Data Science Intern"), SEARCH, profile), "senior"
    )
    _assert_hard_fail(
        score_opportunity(_op("Postdoc Data Science Intern"), SEARCH, profile), "postdoc"
    )


def test_mba_and_phd_titles_are_not_double_reported() -> None:
    result = score_opportunity(_op("PhD Data Science Intern"), SEARCH)
    assert len([p for p in result.penalties if p.startswith("Hard fail:")]) == 1


@pytest.mark.parametrize(
    "description",
    [
        "Candidates must be currently pursuing an MBA.",
        "You are enrolled in a full-time MBA program.",
        "This role is open to MBA candidates only.",
        "Must be an M.B.A. student.",
        "Currently working toward an accredited MBA.",
    ],
)
def test_description_that_says_mba_students_only_hard_fails(description: str) -> None:
    _assert_hard_fail(score_opportunity(_op(description=description), SEARCH), "MBA students")
    assert score_opportunity(_op(description=description), SEARCH, Profile(degree="MBA")).passed


@pytest.mark.parametrize(
    "description",
    [
        "An MBA is a plus.",
        "MBA preferred but not required.",
        "Our alumni include many MBA graduates.",
        "Open to undergraduate students or those currently pursuing an MBA.",
        "Juniors, seniors and MBA students are welcome to apply.",
        "Pursuing a bachelor's degree or an MBA.",
    ],
)
def test_description_mentioning_mba_without_exclusivity_does_not_fail(description: str) -> None:
    assert score_opportunity(_op(description=description), SEARCH).passed


def test_description_that_says_phd_students_only_hard_fails() -> None:
    op = _op(description="Applicants must be currently pursuing a PhD in a quantitative field.")
    _assert_hard_fail(score_opportunity(op, SEARCH), "PhD students")
    assert score_opportunity(op, SEARCH, Profile(degree="PhD")).passed


def test_hard_fail_keeps_family_and_keywords_for_the_dashboard() -> None:
    result = score_opportunity(_op("Senior Product Management Intern"), SEARCH)
    assert result.role_family == "product_management"
    assert result.matched_keywords
    assert result.reasons and "product_management" in result.reasons[0]
    assert "(+" not in result.reasons[0], "no points are shown for a hard-failed result"
