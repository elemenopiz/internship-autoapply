"""Location fit, us_only / remote_ok, allowlist and include-keyword bonuses, plus the weight table itself."""

from __future__ import annotations

import re

import pytest

from autoapply.models import Opportunity, Profile, ScoreResult, SearchProfile
from autoapply.scoring import score_opportunity

# _op() is a core-family internship with the target term: 50 (title) + 15 (intern) + 10 (term) = 75 before
# any location, keyword or allowlist points.
BASE = 75.0


def _op(**fields: object) -> Opportunity:
    base: dict[str, object] = {
        "company": "Acme Corp",
        "title": "Product Management Intern",
        "term": "Summer 2027",
    }
    base.update(fields)
    return Opportunity(**base)  # type: ignore[arg-type]


def _score(
    search: SearchProfile | None = None, profile: Profile | None = None, **fields: object
) -> ScoreResult:
    return score_opportunity(_op(**fields), search or SearchProfile(), profile)


# ------------------------------------------------------------------------------------------ weights


def test_documented_weight_table_adds_up_to_exactly_100() -> None:
    search = SearchProfile(
        preferred_locations=["Austin, TX"],
        include_keywords=["sql", "python", "excel"],
        company_allowlist=["Acme"],
    )
    op = _op(
        title="Product Management Intern SQL Python Excel",
        location="Austin, TX",
        description="",
    )
    result = score_opportunity(op, search)
    assert result.score == 100.0
    assert result.passed


def test_title_only_perfect_default_case_is_81() -> None:
    assert _score(location="Austin, TX").score == BASE + 6


@pytest.mark.parametrize(
    ("kwargs", "score"),
    [
        ({"location": "Austin, TX"}, 81.0),  # known location, no preference
        ({"location": None}, 79.0),  # not listed
        ({"location": ""}, 79.0),
        ({"location": "   "}, 79.0),
    ],
)
def test_location_points_without_preferences(kwargs: dict[str, object], score: float) -> None:
    assert _score(**kwargs).score == score


def test_scores_never_leave_the_0_to_100_range() -> None:
    search = SearchProfile(
        include_keywords=[f"kw{i}" for i in range(30)],
        company_allowlist=["Acme"],
        preferred_locations=["Austin"],
    )
    title = "Product Management Intern " + " ".join(f"kw{i}" for i in range(30))
    assert score_opportunity(_op(title=title, location="Austin, TX"), search).score == 100.0
    worst = score_opportunity(
        Opportunity(company="Acme", title="Zebra Keeper", location="London, UK"),
        SearchProfile(),
    )
    assert worst.score == 0.0
    assert not worst.passed


def test_min_score_boundary_is_inclusive() -> None:
    op = _op(location="Austin, TX")
    assert score_opportunity(op, SearchProfile(min_score=81.0)).passed
    assert not score_opportunity(op, SearchProfile(min_score=81.5)).passed
    assert score_opportunity(op, SearchProfile(min_score=0.0)).passed
    assert not score_opportunity(op, SearchProfile(min_score=101.0)).passed


def test_min_score_never_rescues_a_hard_fail() -> None:
    result = score_opportunity(_op(is_open=False), SearchProfile(min_score=0.0))
    assert result.score == 0.0 and not result.passed


# ------------------------------------------------------------------------------------------ us_only


@pytest.mark.parametrize(
    "location",
    [
        "London, UK",
        "Toronto, ON",
        "Toronto, Ontario, Canada",
        "Bengaluru, India",
        "Berlin, Germany",
        "Mexico City",
        "Remote (Canada)",
        "Remote - EMEA",
        "Sydney, Australia",
        "Dublin, Ireland",
        "Singapore",
        "Tel Aviv, Israel",
    ],
)
def test_clearly_non_us_locations_are_penalised_when_us_only(location: str) -> None:
    result = _score(location=location)
    assert result.score == BASE - 40
    assert not result.passed
    assert any("outside the United States" in p and "-40" in p for p in result.penalties)
    # us_only off: the same place is just an unremarkable location
    relaxed = _score(SearchProfile(us_only=False), location=location)
    assert relaxed.score == BASE + 6
    assert relaxed.passed
    assert not relaxed.penalties


@pytest.mark.parametrize(
    "location",
    [
        "Austin, TX",
        "Austin, Texas, United States",
        "New York, NY, USA",
        "Remote - US",
        "Remote, U.S.",
        "Remote",
        "Hybrid - Seattle, WA",
        "Multiple Locations",
        "Nationwide",
        "Paris, TX",  # a US Paris
        "London, KY",
        "Vancouver, WA",
        "New Mexico",  # the state, not Mexico
        "Albuquerque, New Mexico",
        "London, UK; New York, NY",  # one US site is enough
        "Toronto, ON or Remote (US)",
        "Portland, OR",
        "Georgia",
        "Washington, DC",
    ],
)
def test_us_or_unclear_locations_are_not_penalised(location: str) -> None:
    result = _score(location=location)
    assert result.score == BASE + 6, result
    assert not result.penalties


def test_state_codes_that_are_everyday_words_need_capitals() -> None:
    # lower-case "or" / "in" / "me" are words, not Oregon / Indiana / Maine
    assert _score(location="Toronto, or anywhere in Canada").score == BASE - 40
    assert _score(location="Remote in Canada").score == BASE - 40
    assert _score(location="Toronto, ON or Remote in Canada").score == BASE - 40
    # ...but capitals are trusted: "Portland, OR" is Oregon (US), so no us_only penalty
    assert _score(location="Portland, OR").score == BASE + 6


# ------------------------------------------------------------------------------------------ preferred


PREFERRED = SearchProfile(preferred_locations=["Austin, TX", "Remote"])


@pytest.mark.parametrize(
    "location",
    [
        "Austin, TX",
        "Austin",
        "Austin, Texas",
        "AUSTIN, TX",
        "austin, tx",
        "Austin, TX (Hybrid)",
        "Austin, TX, USA",
        "Remote",
        "Remote - US",
        "Remote or Austin, TX",
        "Work from home",
    ],
)
def test_preferred_locations_earn_the_full_10(location: str) -> None:
    result = _score(PREFERRED, location=location)
    assert result.score == BASE + 10, result
    assert any("preferred location" in r for r in result.reasons)


@pytest.mark.parametrize(
    "location",
    ["Austin, MN", "Round Rock, TX", "Dallas, TX", "Seattle, WA", "New York, NY", "Houston"],
)
def test_other_places_lose_15_by_default(location: str) -> None:
    result = _score(PREFERRED, location=location)
    assert result.score == BASE - 15, result
    assert any("not in your preferred locations" in p and "-15" in p for p in result.penalties)


def test_unlisted_location_with_a_preference_list_is_neutral_not_penalised() -> None:
    result = _score(PREFERRED, location=None)
    assert result.score == BASE + 4
    assert not result.penalties


@pytest.mark.parametrize(
    ("entry", "location", "matches"),
    [
        ("Texas", "Austin, TX", True),
        ("Texas", "Dallas, Texas", True),
        ("Texas", "Austin", False),  # cannot tell the state
        ("Texas", "Portland, OR", False),
        ("TX", "Houston, TX", True),
        ("New York", "New York, NY", True),
        ("New York City", "New York, NY", True),
        ("NYC", "New York, NY", True),
        ("New York, NY", "New York City", True),
        ("New York, NY", "Brooklyn, NY", True),  # a city named like its state is matched by state
        ("Dallas, TX", "Austin, TX", False),
        ("Austin, TX", "Dallas, TX", False),
        ("San Francisco Bay Area", "San Francisco, CA", True),
        ("SF", "San Francisco, CA", True),
        ("Washington, DC", "Washington, DC", True),
        ("Greater Austin Area", "Austin, TX", True),
        ("United States", "Boston, MA", True),
        ("USA", "Boston, MA", True),
        ("United States", "London, UK", False),
        ("Boston, MA", "Boston, MA; Austin, TX", True),
        ("Austin, TX", "Boston, MA; Austin, TX", True),
        ("Portland, OR", "Portland, ME", False),
        ("Portland", "Portland, ME", True),
        ("Remote", "Austin, TX", False),
        ("London", "London, UK", True),
    ],
)
def test_preferred_entry_matching(entry: str, location: str, matches: bool) -> None:
    result = _score(SearchProfile(preferred_locations=[entry], us_only=False), location=location)
    assert (result.score == BASE + 10) is matches, (entry, location, result)


def test_blank_preferred_entries_are_ignored() -> None:
    search = SearchProfile(preferred_locations=["", "   "])
    assert _score(search, location="Seattle, WA").score == BASE + 6


def test_remote_role_with_a_preference_list_but_no_remote_entry() -> None:
    search = SearchProfile(preferred_locations=["Austin, TX"])
    result = _score(search, location="Remote - US")
    assert result.score == BASE + 8
    assert any("Remote role" in r for r in result.reasons)


def test_remote_role_without_a_preference_list_is_neutral() -> None:
    assert _score(location="Remote").score == BASE + 6


# ------------------------------------------------------------------------------------------ remote_ok


def test_remote_only_roles_lose_15_when_remote_is_off() -> None:
    off = SearchProfile(remote_ok=False)
    result = _score(off, location="Remote - US")
    assert result.score == BASE - 15
    assert any("remote_ok is off" in p and "-15" in p for p in result.penalties)
    assert _score(off, location="Austin, TX").score == BASE + 6  # on-site is unaffected


def test_remote_off_still_honours_an_explicit_city_in_the_same_listing() -> None:
    search = SearchProfile(remote_ok=False, preferred_locations=["Austin, TX"])
    assert _score(search, location="Remote or Austin, TX").score == BASE + 10
    assert _score(search, location="Remote").score == BASE - 15


def test_a_remote_preference_entry_is_ignored_when_remote_is_off() -> None:
    search = SearchProfile(remote_ok=False, preferred_locations=["Remote"])
    assert _score(search, location="Remote").score == BASE - 15


# ------------------------------------------------------------------------------------------ relocation


@pytest.mark.parametrize(("willing", "penalty"), [(True, 8.0), (None, 15.0), (False, 30.0)])
def test_off_preference_penalty_follows_the_profiles_relocation_answer(
    willing: bool | None, penalty: float
) -> None:
    profile = Profile(willing_to_relocate=willing)
    result = _score(PREFERRED, profile, location="Seattle, WA")
    assert result.score == BASE - penalty
    assert any(f"-{penalty:g}" in p for p in result.penalties)


def test_relocation_answer_is_ignored_without_a_preference_list() -> None:
    profile = Profile(willing_to_relocate=False)
    assert _score(None, profile, location="Seattle, WA").score == BASE + 6


def test_location_ordering() -> None:
    preferred = _score(PREFERRED, location="Austin, TX").score
    remote = _score(SearchProfile(preferred_locations=["Austin, TX"]), location="Remote").score
    neutral = _score(location="Seattle, WA").score
    unknown = _score(location=None).score
    off_pref = _score(PREFERRED, location="Seattle, WA").score
    non_us = _score(location="London, UK").score
    assert preferred > remote > neutral > unknown > off_pref > non_us


# ------------------------------------------------------------------------------------------ allowlist


@pytest.mark.parametrize(
    ("company", "entry", "bonus"),
    [
        ("Acme Corp", "Acme Corp", 5.0),
        ("ACME CORP", "acme corp", 5.0),
        ("Acme, Inc.", "Acme", 5.0),
        ("Amazon Web Services", "Amazon", 5.0),
        ("JPMorgan Chase & Co.", "JPMorgan Chase", 5.0),
        ("Wells Fargo & Company", "Wells Fargo", 5.0),
        ("Other Corp", "Acme", 0.0),
        ("Acmeco Labs", "Acme", 0.0),
    ],
)
def test_allowlist_bonus(company: str, entry: str, bonus: float) -> None:
    result = _score(
        SearchProfile(company_allowlist=[entry]), company=company, location="Austin, TX"
    )
    assert result.score == BASE + 6 + bonus
    assert any("allowlist" in r for r in result.reasons) is (bonus > 0)


def test_allowlist_cannot_rescue_a_bad_role() -> None:
    search = SearchProfile(company_allowlist=["Acme"])
    result = score_opportunity(_op(title="Zebra Keeper Intern", location="Austin, TX"), search)
    assert not result.passed  # 15 + 10 + 6 + 5 = 36


# ------------------------------------------------------------------------------------------ include keywords


def test_include_keywords_in_title_earn_4_each() -> None:
    search = SearchProfile(include_keywords=["SQL", "Python"])
    result = score_opportunity(_op(title="Product Management Intern - SQL & Python"), search)
    assert result.score == BASE + 4 + 4 + 4  # + unlisted location
    assert result.matched_keywords[-2:] == ["SQL", "Python"]
    assert any("Include keywords found" in r and "(title)" in r for r in result.reasons)


def test_include_keywords_only_in_the_description_earn_2_each() -> None:
    search = SearchProfile(include_keywords=["sql", "python"])
    result = score_opportunity(_op(description="You will use SQL and Python daily."), search)
    assert result.score == BASE + 4 + 2 + 2
    assert any("(description)" in r for r in result.reasons)


def test_a_keyword_in_both_places_counts_once_at_the_title_rate() -> None:
    search = SearchProfile(include_keywords=["sql"])
    result = score_opportunity(
        _op(title="Product Management Intern (SQL)", description="sql"), search
    )
    assert result.score == BASE + 4 + 4


def test_include_keyword_bonus_is_capped_at_10() -> None:
    words = ["alpha", "beta", "gamma", "delta"]
    search = SearchProfile(include_keywords=words)
    result = score_opportunity(_op(title="Product Management Intern " + " ".join(words)), search)
    assert result.score == BASE + 4 + 10
    assert any("+10)" in r for r in result.reasons)


@pytest.mark.parametrize(
    ("keyword", "text"),
    [
        ("machine learning", "Machine-Learning platform"),
        ("analytics", "Analytic tools"),  # plural / singular
        ("R&D", "R & D partnership"),
        ("C++", "C++ services"),
        ("SQL", "sql"),
        ("data analysis", "Data analysis"),
    ],
)
def test_include_keywords_match_whole_words_case_and_punctuation_insensitively(
    keyword: str, text: str
) -> None:
    search = SearchProfile(include_keywords=[keyword])
    result = score_opportunity(_op(description=text), search)
    assert keyword in result.matched_keywords, result


@pytest.mark.parametrize(
    ("keyword", "text"),
    [
        ("sql", "mysql"),
        ("java", "javascript"),
        ("art", "smart"),
        ("go", "google"),
        ("c++", "c and c#"),
    ],
)
def test_include_keywords_never_match_inside_other_words(keyword: str, text: str) -> None:
    search = SearchProfile(include_keywords=[keyword])
    result = score_opportunity(_op(description=text), search)
    assert keyword not in result.matched_keywords


def test_duplicate_and_blank_include_keywords_count_once() -> None:
    search = SearchProfile(include_keywords=["sql", "SQL", " sql ", "", "  "])
    result = score_opportunity(_op(title="Product Management Intern SQL"), search)
    assert result.score == BASE + 4 + 4
    assert result.matched_keywords.count("sql") + result.matched_keywords.count("SQL") == 1


# ------------------------------------------------------------------------------------------ explainability

_PLUS = re.compile(r"\(\+([0-9.]+)\)")
_MINUS = re.compile(r"\(-([0-9.]+)\)")


def _explained_total(result: ScoreResult) -> float:
    plus = sum(float(m) for line in result.reasons for m in _PLUS.findall(line))
    minus = sum(float(m) for line in result.penalties for m in _MINUS.findall(line))
    return round(plus - minus, 1)


@pytest.mark.parametrize(
    "fields",
    [
        {"location": "Austin, TX"},
        {"location": None, "term": None},
        {"location": "London, UK"},
        {"title": "Data Analytics Intern", "location": "Remote"},
        {
            "title": "Strategy Intern",
            "term": None,
            "description": "Fall 2026 applications are open.",
        },
        {"title": "Business Analyst", "term": None},
        {"title": "Summer Intern", "description": "product management and product manager roles"},
        {"title": "Zebra Keeper Intern"},
        {"title": "Business Analyst", "term": "Summer 2027"},
        {"title": "Product Management Intern", "description": "an internship in Summer 2027"},
    ],
)
def test_reasons_and_penalties_add_up_to_the_score(fields: dict[str, object]) -> None:
    search = SearchProfile(
        preferred_locations=["Austin, TX"],
        include_keywords=["sql"],
        company_allowlist=["Acme"],
    )
    result = score_opportunity(_op(**fields), search)
    assert not any(p.startswith("Hard fail:") for p in result.penalties)
    expected = max(0.0, min(100.0, _explained_total(result)))
    assert result.score == expected, result
