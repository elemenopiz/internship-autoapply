"""Golden tests: every README role family, positive titles and near-miss negatives (default SearchProfile)."""

from __future__ import annotations

import pytest

from autoapply.models import Opportunity, RoleFamily, SearchProfile
from autoapply.scoring import score_opportunity

SEARCH = SearchProfile()


def _op(title: str, **fields: object) -> Opportunity:
    base: dict[str, object] = {
        "company": "Acme Corp",
        "title": title,
        "location": "Austin, TX",
        "term": "Summer 2027",
    }
    base.update(fields)
    return Opportunity(**base)  # type: ignore[arg-type]


POSITIVES: list[tuple[str, str]] = [
    # product_management
    ("Product Management Intern (Summer 2027)", "product_management"),
    ("Product Manager Intern", "product_management"),
    ("Associate Product Manager Intern", "product_management"),
    ("APM Intern", "product_management"),
    ("APM Internship - Summer 2027", "product_management"),
    ("Product Owner Intern", "product_management"),
    ("Product Operations Intern", "product_management"),
    ("Product Strategy Intern", "product_management"),
    ("Product Intern", "product_management"),
    ("Product Management Interns", "product_management"),
    ("Intern, Product Management", "product_management"),
    ("Product-Management Intern", "product_management"),
    # technical_program_management
    ("TPM Intern", "technical_program_management"),
    ("Technical Program Manager Intern", "technical_program_management"),
    ("Technical Programs Intern", "technical_program_management"),
    ("Program Management Intern", "technical_program_management"),
    ("Program Manager Intern", "technical_program_management"),
    ("Project Manager Intern", "technical_program_management"),
    ("Project Management Internship", "technical_program_management"),
    ("Delivery Manager Intern", "technical_program_management"),
    # technology_consulting
    ("Technology Consulting Summer Analyst", "technology_consulting"),
    ("Technology Consultant Intern", "technology_consulting"),
    ("IT Consulting Intern", "technology_consulting"),
    ("Digital Consulting Intern", "technology_consulting"),
    ("Consulting Analyst Intern", "technology_consulting"),
    ("Management Consulting Intern", "technology_consulting"),
    ("Solutions Consultant Intern", "technology_consulting"),
    ("Business Technology Analyst Intern", "technology_consulting"),
    ("Technology Advisory Summer Associate", "technology_consulting"),
    ("Consultant Intern", "technology_consulting"),
    # strategy
    ("Strategy Intern", "strategy"),
    ("Strategy & Operations Intern", "strategy"),
    ("Strategy and Operations Intern", "strategy"),
    ("Strategy/Operations Intern", "strategy"),
    ("Strategic Planning Intern", "strategy"),
    ("Corporate Strategy Intern", "strategy"),
    ("Business Strategy Intern", "strategy"),
    ("Corporate Development Intern", "strategy"),
    ("Strategy Summer Analyst", "strategy"),
    # business_operations
    ("Business Operations Intern", "business_operations"),
    ("Business Operations Analyst Intern", "business_operations"),
    ("BizOps Intern", "business_operations"),
    ("Biz Ops Intern", "business_operations"),
    ("Operations Analyst Intern", "business_operations"),
    ("Revenue Operations Intern", "business_operations"),
    ("Sales Operations Intern", "business_operations"),
    ("Strategic Operations Intern", "business_operations"),
    ("Operations Excellence Intern", "business_operations"),
    ("Operations Intern", "business_operations"),
    # business_analysis
    ("Business Analyst Intern", "business_analysis"),
    ("Business Analysis Intern", "business_analysis"),
    ("Business Intelligence Intern", "business_analysis"),
    ("Systems Analyst Intern", "business_analysis"),
    ("Process Analyst Intern", "business_analysis"),
    ("Requirements Analyst Intern", "business_analysis"),
    ("Functional Analyst Intern", "business_analysis"),
    ("Business Analyst Co-op", "business_analysis"),
    # analytics (weight 0.7)
    ("Data Analytics Intern", "analytics"),
    ("Data Analyst Intern", "analytics"),
    ("Business Analytics Intern", "analytics"),
    ("Product Analytics Intern", "analytics"),
    ("Insights Analyst Intern", "analytics"),
    ("Quantitative Analyst Intern", "analytics"),
    ("Data Science Intern", "analytics"),
    ("Analytics Intern", "analytics"),
]


@pytest.mark.parametrize(("title", "family"), POSITIVES)
def test_positive_titles_match_their_family_and_pass(title: str, family: str) -> None:
    result = score_opportunity(_op(title), SEARCH)
    assert result.role_family == family, result
    assert result.passed, result
    assert result.matched_keywords, "the matching keyword must be reported"
    assert result.reasons and result.reasons[0].startswith("Title matches role family")
    assert result.penalties == []


def test_every_family_has_positives_in_the_golden_table() -> None:
    covered = {family for _, family in POSITIVES}
    assert covered == set(SEARCH.role_families)


def test_every_default_keyword_alone_is_recognised_in_the_title() -> None:
    """Each shipped keyword, used as "<keyword> Intern", must match SOME family and pass the default bar."""
    for family_name, family in SEARCH.role_families.items():
        for keyword in family.keywords:
            result = score_opportunity(_op(f"{keyword.title()} Intern"), SEARCH)
            assert result.role_family is not None, (family_name, keyword, result)
            assert result.passed, (family_name, keyword, result)


# Near misses: they share words with a family but are NOT that family. Each is (title, family it must
# not be assigned to).
NEAR_MISSES: list[tuple[str, str]] = [
    ("Product Design Intern", "product_management"),
    ("Product Marketing Intern", "product_management"),
    ("Production Intern", "product_management"),
    ("Product Support Engineer Intern", "product_management"),
    ("Programming Intern", "technical_program_management"),
    ("Software Programming Intern", "technical_program_management"),
    ("Technical Programming Intern", "technical_program_management"),
    ("Program Coordinator Intern", "technical_program_management"),
    ("Manager Intern", "technical_program_management"),
    ("Technology Intern", "technology_consulting"),
    ("Technology Support Intern", "technology_consulting"),
    ("Sales Engineer Intern", "technology_consulting"),
    ("Content Strategist Intern", "strategy"),
    ("Business Development Intern", "business_operations"),
    ("Operations Manager Intern", "business_operations"),
    ("Business Development Intern", "business_analysis"),
    ("Analyst Intern", "business_analysis"),
    ("Data Entry Intern", "analytics"),
    ("Data Engineering Intern", "analytics"),
    ("Analytical Chemistry Intern", "analytics"),
]


@pytest.mark.parametrize(("title", "family"), NEAR_MISSES)
def test_near_misses_are_not_that_family(title: str, family: str) -> None:
    result = score_opportunity(_op(title), SEARCH)
    assert result.role_family != family, result
    # nothing else in the README list fits these titles either, so they must not pass the bar
    assert result.role_family is None, result
    assert not result.passed, result
    assert any("No role family" in p for p in result.penalties)


@pytest.mark.parametrize(
    "title",
    [
        "Strategy Manager",
        "Product Manager",
        "Technical Program Manager",
        "Business Operations Manager",
        "Head of Strategy",
        "Business Analyst II",
        "Full-Time Product Manager",
        "New Grad Business Analyst",
        "Lead Data Analyst",
    ],
)
def test_full_time_or_management_titles_without_intern_signal_hard_fail(title: str) -> None:
    result = score_opportunity(_op(title), SEARCH)
    assert result.score == 0.0
    assert not result.passed
    assert any(p.startswith("Hard fail:") for p in result.penalties)
    assert result.role_family is not None, (
        "the family is still reported so the dashboard can show it"
    )


def test_manager_alone_matches_nothing() -> None:
    for title in (
        "Manager Intern",
        "Manager",
        "Senior Manager",
        "Manager, Special Projects Intern",
    ):
        assert score_opportunity(_op(title), SEARCH).role_family is None


def test_strategy_manager_is_not_a_strategy_internship_but_its_intern_version_is() -> None:
    assert not score_opportunity(_op("Strategy Manager"), SEARCH).passed
    assert score_opportunity(_op("Strategy Manager Intern"), SEARCH).passed


def test_program_inside_programming_is_not_a_program_keyword() -> None:
    custom = SearchProfile(role_families={"programs": RoleFamily(keywords=["program"])})
    assert score_opportunity(_op("Program Intern"), custom).role_family == "programs"
    assert score_opportunity(_op("Programs Intern"), custom).role_family == "programs"
    assert score_opportunity(_op("Programming Intern"), custom).role_family is None


def test_title_wins_over_description_and_reports_the_keyword() -> None:
    result = score_opportunity(
        _op("APM Intern", description="You will support strategy and analytics work."), SEARCH
    )
    assert result.role_family == "product_management"
    assert result.matched_keywords == ["apm"]


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Strategy & Operations Intern", "strategy"),  # strategy AND "operations intern" both fit
        ("Technical Program Manager Intern - Product Management", "technical_program_management"),
        ("Product Manager Intern, Strategy", "product_management"),
        ("Business Operations Analyst Intern", "business_operations"),
        ("Product Strategy Intern", "product_management"),
    ],
)
def test_overlapping_titles_pick_the_most_specific_family(title: str, family: str) -> None:
    assert score_opportunity(_op(title), SEARCH).role_family == family


def test_family_declaration_order_breaks_exact_ties() -> None:
    kw = ["alpha widget"]
    first = SearchProfile(
        role_families={"first": RoleFamily(keywords=kw), "second": RoleFamily(keywords=kw)}
    )
    swapped = SearchProfile(
        role_families={"second": RoleFamily(keywords=kw), "first": RoleFamily(keywords=kw)}
    )
    assert score_opportunity(_op("Alpha Widget Intern"), first).role_family == "first"
    assert score_opportunity(_op("Alpha Widget Intern"), swapped).role_family == "second"


def test_custom_family_weight_scales_the_title_match() -> None:
    full = SearchProfile(role_families={"x": RoleFamily(keywords=["data analyst"], weight=1.0)})
    half = SearchProfile(role_families={"x": RoleFamily(keywords=["data analyst"], weight=0.5)})
    off = SearchProfile(role_families={"x": RoleFamily(keywords=["data analyst"], weight=0.0)})
    over = SearchProfile(role_families={"x": RoleFamily(keywords=["data analyst"], weight=7.0)})
    op = _op("Data Analyst Intern")
    assert score_opportunity(op, full).score - score_opportunity(op, half).score == 25.0
    assert score_opportunity(op, off).role_family is None
    assert score_opportunity(op, over).score == score_opportunity(op, full).score  # clamped to 1.0


def test_keywords_made_only_of_internship_words_are_ignored() -> None:
    silly = SearchProfile(role_families={"x": RoleFamily(keywords=["intern", "internship", ""])})
    assert score_opportunity(_op("Baker Intern"), silly).role_family is None


def test_no_role_families_configured_never_passes() -> None:
    result = score_opportunity(_op("Product Management Intern"), SearchProfile(role_families={}))
    assert result.role_family is None
    assert not result.passed


def test_description_only_match_is_reported_but_cannot_pass_by_itself() -> None:
    desc = (
        "Join our product management team. You will work with the product manager, run product "
        "operations and refine the roadmap. Summer 2027."
    )
    result = score_opportunity(_op("Summer Intern", description=desc), SEARCH)
    assert result.role_family == "product_management"
    assert result.reasons[0].startswith("Title names no role family; description mentions")
    assert not result.passed


def test_title_beats_description_for_the_same_signals() -> None:
    in_title = score_opportunity(_op("Product Management Intern"), SEARCH)
    in_description = score_opportunity(
        _op("Summer Intern", description="product management product manager product owner"), SEARCH
    )
    assert in_title.score > in_description.score
    assert in_title.passed and not in_description.passed


def test_core_family_beats_the_weight_07_analytics_family_at_equal_signals() -> None:
    core = score_opportunity(_op("Business Analyst Intern"), SEARCH)
    adjacent = score_opportunity(_op("Data Analyst Intern"), SEARCH)
    assert core.role_family == "business_analysis" and adjacent.role_family == "analytics"
    assert core.score > adjacent.score
    # even a generic single-word core keyword (90 percent) beats a two-word analytics phrase
    generic_core = score_opportunity(_op("Strategy Intern"), SEARCH)
    assert generic_core.score > score_opportunity(_op("Data Analytics Intern"), SEARCH).score
    # ...and the ordering holds when every other signal is missing
    bare_core = score_opportunity(
        Opportunity(company="Acme", title="Product Management Intern"), SEARCH
    )
    bare_adjacent = score_opportunity(
        Opportunity(company="Acme", title="Data Analytics Intern"), SEARCH
    )
    assert bare_core.score > bare_adjacent.score


def test_a_bare_analytics_internship_still_clears_the_default_bar() -> None:
    result = score_opportunity(Opportunity(company="Acme", title="Data Analytics Intern"), SEARCH)
    assert result.passed
    assert result.score >= SEARCH.min_score
