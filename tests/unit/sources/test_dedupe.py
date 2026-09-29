"""Cross-source de-duplication: by id, by fingerprint, winner choice, field merging, invariants."""

from __future__ import annotations

import copy
import random
from datetime import date, datetime

import pytest

from autoapply.models import ATS, Opportunity, OpportunitySource, ScoreResult
from autoapply.sources.dedupe import dedupe, url_rank

GH = "https://boards.greenhouse.io/acme/jobs/1"
WORKDAY = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/Product-Intern_R1"
CAREERS = "https://careers.acme.com/jobs/product-intern"
UNKNOWN_HOST = "https://portal.example.test/roles/1"
LINKEDIN = "https://www.linkedin.com/jobs/view/3900000001"
INDEED = "https://www.indeed.com/viewjob?jk=abcdef0123456789"


def op(
    url: str = GH,
    *,
    company: str = "Acme",
    title: str = "Product Intern",
    location: str | None = "Austin, TX",
    **kwargs: object,
) -> Opportunity:
    return Opportunity(company=company, title=title, url=url, location=location, **kwargs)  # type: ignore[arg-type]


def summary(items: list[Opportunity]) -> list[tuple[str, str, date | None]]:
    return [(o.id, o.url, o.last_verified) for o in items]


# --------------------------------------------------------------------------------------------- trivial inputs


def test_empty_and_single() -> None:
    assert dedupe([]) == []
    only = op()
    (result,) = dedupe([only])
    assert result is only  # untouched records are passed through as they are


def test_accepts_any_iterable() -> None:
    assert len(dedupe(o for o in [op(GH), op(WORKDAY, title="Other Intern")])) == 2
    assert len(dedupe(iter([op(GH)]))) == 1
    assert len(dedupe((op(GH), op(GH)))) == 1


def test_distinct_records_keep_their_order() -> None:
    items = [
        op(f"https://boards.greenhouse.io/acme/jobs/{n}", title=f"Intern {n}") for n in (5, 3, 9, 1)
    ]
    assert [o.title for o in dedupe(items)] == ["Intern 5", "Intern 3", "Intern 9", "Intern 1"]


# --------------------------------------------------------------------------------------------- same id


def test_same_id_merges_records_that_differ_only_in_url_noise() -> None:
    a = op("https://boards.greenhouse.io/acme/jobs/1?gh_src=abc&utm_source=news")
    b = op("https://job-boards.greenhouse.io/acme/jobs/1")
    c = op("https://www.boards.greenhouse.io/acme/jobs/1/apply?ref=x#top")
    assert a.id == b.id == c.id
    (merged,) = dedupe([a, b, c])
    assert merged.id == a.id
    assert "alt_urls" not in merged.extra  # the variants are the same page, not alternatives


def test_exact_duplicates_collapse_and_are_idempotent() -> None:
    a = op()
    twice = dedupe([a, copy.deepcopy(a)])
    assert len(twice) == 1
    assert dedupe(twice) == twice


# --------------------------------------------------------------------------------------------- same fingerprint


@pytest.mark.parametrize("aggregator_first", [True, False])
def test_direct_ats_url_beats_the_aggregator_whatever_the_order(aggregator_first: bool) -> None:
    direct = op(WORKDAY, source=OpportunitySource.WORKBOOK, ats=ATS.WORKDAY)
    aggregator = op(LINKEDIN, source=OpportunitySource.LINKEDIN, location="Austin, Texas")
    assert direct.fingerprint == aggregator.fingerprint
    items = [aggregator, direct] if aggregator_first else [direct, aggregator]
    (merged,) = dedupe(items)
    assert merged.url == WORKDAY and merged.id == direct.id
    assert merged.source == OpportunitySource.WORKBOOK and merged.ats == ATS.WORKDAY
    assert merged.extra["alt_urls"] == [LINKEDIN]
    assert merged.extra["merged_sources"] == ["linkedin", "workbook"]


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        (GH, CAREERS),
        (WORKDAY, UNKNOWN_HOST),
        (CAREERS, UNKNOWN_HOST),
        (UNKNOWN_HOST, LINKEDIN),
        (UNKNOWN_HOST, INDEED),
        (LINKEDIN, ""),
        (GH, LINKEDIN),
        (CAREERS, INDEED),
    ],
)
def test_url_quality_ranking(better: str, worse: str) -> None:
    assert url_rank(better) < url_rank(worse)
    for order in ((better, worse), (worse, better)):
        items = [op(u) for u in order]
        (merged,) = dedupe(items)
        assert merged.url == better


def test_url_rank_values() -> None:
    assert [
        url_rank(u) for u in (GH, WORKDAY, CAREERS, UNKNOWN_HOST, LINKEDIN, INDEED, "", None, "  ")
    ] == [
        0,
        0,
        1,
        2,
        3,
        3,
        4,
        4,
        4,
    ]


def test_an_aggregator_record_with_a_direct_apply_url_counts_as_direct() -> None:
    via_linkedin = op(LINKEDIN, apply_url=WORKDAY, source=OpportunitySource.LINKEDIN)
    plain_indeed = op(INDEED, source=OpportunitySource.INDEED)
    (merged,) = dedupe([plain_indeed, via_linkedin])
    assert merged.start_url == WORKDAY
    assert merged.id == via_linkedin.id
    assert merged.source == OpportunitySource.LINKEDIN
    assert INDEED in merged.extra["alt_urls"]


def test_equal_url_rank_prefers_the_fresher_record_then_the_more_trusted_source() -> None:
    old = op("https://boards.greenhouse.io/acme/jobs/1", last_verified=date(2026, 9, 1))
    new = op("https://jobs.lever.co/acme/2222", last_verified=date(2026, 9, 20))
    assert dedupe([old, new])[0].url == new.url
    assert dedupe([new, old])[0].url == new.url

    manual = op("https://boards.greenhouse.io/acme/jobs/1", source=OpportunitySource.MANUAL)
    workbook = op("https://jobs.lever.co/acme/2222", source=OpportunitySource.WORKBOOK)
    board = op("https://jobs.ashbyhq.com/acme/3333", source=OpportunitySource.ASHBY)
    assert dedupe([board, workbook, manual])[0].source == OpportunitySource.MANUAL
    assert dedupe([board, workbook])[0].source == OpportunitySource.WORKBOOK


def test_more_complete_records_win_remaining_ties() -> None:
    thin = op(
        "https://boards.greenhouse.io/acme/jobs/1",
        location=None,
        source=OpportunitySource.GREENHOUSE,
    )
    rich = op(
        "https://jobs.lever.co/acme/2222",
        location="Austin, TX",
        description="Full text",
        term="Summer 2027",
        source=OpportunitySource.LEVER,
    )
    assert thin.fingerprint != rich.fingerprint  # only the located record carries the city ...
    (merged,) = dedupe([thin, rich])  # ... and the city-less one joins it
    assert merged.url == rich.url


def test_input_order_is_the_last_tie_break() -> None:
    a = op("https://boards.greenhouse.io/acme/jobs/1")
    b = op("https://jobs.lever.co/acme/2222")
    assert dedupe([a, b])[0].url == a.url
    assert dedupe([b, a])[0].url == b.url


# --------------------------------------------------------------------------------------------- merging fields


def test_freshest_last_verified_is_kept_even_from_the_losing_record() -> None:
    direct = op(WORKDAY, last_verified=date(2026, 9, 10))
    aggregator = op(LINKEDIN, last_verified=date(2026, 9, 27))
    (merged,) = dedupe([direct, aggregator])
    assert merged.url == WORKDAY
    assert merged.last_verified == date(2026, 9, 27)


def test_gaps_are_filled_from_the_other_records() -> None:
    winner = op(WORKDAY, location=None, source=OpportunitySource.WORKBOOK)
    other = op(
        LINKEDIN,
        location="Austin, Texas",
        term="Summer 2027",
        description="A longer job description text",
        posted_date=date(2026, 9, 1),
        deadline=date(2026, 10, 15),
        source=OpportunitySource.LINKEDIN,
    )
    third = op(
        INDEED,
        description="short",
        posted_date=date(2026, 8, 20),
        deadline=date(2026, 10, 1),
        source=OpportunitySource.INDEED,
    )
    (merged,) = dedupe([winner, other, third])
    assert merged.url == WORKDAY
    assert merged.location == "Austin, Texas"
    assert merged.term == "Summer 2027"
    assert merged.description == "A longer job description text"  # longest wins
    assert merged.posted_date == date(2026, 8, 20)  # earliest sighting of the posting
    assert merged.deadline == date(2026, 10, 1)  # the earliest stated deadline is the safe one


def test_the_winners_own_values_are_not_overwritten() -> None:
    winner = op(
        WORKDAY,
        term="Summer 2027",
        posted_date=date(2026, 9, 5),
        deadline=date(2026, 10, 30),
        description="Winner text",
    )
    other = op(
        LINKEDIN,
        term="Summer 2028",
        posted_date=date(2026, 8, 1),
        deadline=date(2026, 10, 1),
        description="Other",
    )
    (merged,) = dedupe([winner, other])
    assert merged.term == "Summer 2027"
    assert merged.posted_date == date(2026, 9, 5)
    assert merged.deadline == date(2026, 10, 30)
    assert merged.description == "Winner text"


def test_open_if_any_source_says_open() -> None:
    (merged,) = dedupe([op(WORKDAY, is_open=False), op(LINKEDIN, is_open=True)])
    assert merged.is_open is True
    (closed,) = dedupe([op(WORKDAY, is_open=False), op(LINKEDIN, is_open=False)])
    assert closed.is_open is False


def test_the_most_specific_ats_wins_when_the_winner_is_unsure() -> None:
    (merged,) = dedupe([op(UNKNOWN_HOST, ats=ATS.UNKNOWN), op(LINKEDIN, ats=ATS.LEVER)])
    assert merged.url == UNKNOWN_HOST and merged.ats == ATS.LEVER
    (kept,) = dedupe([op(WORKDAY, ats=ATS.WORKDAY), op(LINKEDIN, ats=ATS.LEVER)])
    assert kept.ats == ATS.WORKDAY  # a specific winner keeps its own
    (custom,) = dedupe([op(CAREERS, ats=ATS.CUSTOM), op(LINKEDIN, ats=ATS.UNKNOWN)])
    assert custom.ats == ATS.CUSTOM


def test_seen_timestamps_and_score() -> None:
    first = datetime(2026, 9, 1, 8, 0)
    late = datetime(2026, 9, 28, 8, 0)
    score = ScoreResult(score=80.0, passed=True)
    a = op(WORKDAY, first_seen=late, last_seen=late)
    b = op(LINKEDIN, first_seen=first, last_seen=first, score=score)
    (merged,) = dedupe([a, b])
    assert merged.first_seen == first and merged.last_seen == late
    assert merged.score == score


def test_a_merged_record_keeps_the_winners_identity() -> None:
    winner = op(WORKDAY, source=OpportunitySource.WORKBOOK)
    loser = op(LINKEDIN, source=OpportunitySource.LINKEDIN)
    (merged,) = dedupe([loser, winner])
    assert (merged.id, merged.url, merged.apply_url, merged.source) == (
        winner.id,
        winner.url,
        winner.apply_url,
        winner.source,
    )
    assert merged.fingerprint == winner.fingerprint


# --------------------------------------------------------------------------------------------- extra


def test_extra_is_the_union_with_the_winner_winning_conflicts() -> None:
    winner = op(WORKDAY, extra={"sheet_row": 12, "Pay": "$34", "notes": "from the sheet"})
    other = op(LINKEDIN, extra={"sheet_row": 3, "easy_apply": False, "notes": "from linkedin"})
    (merged,) = dedupe([other, winner])
    assert merged.extra["sheet_row"] == 12
    assert merged.extra["Pay"] == "$34"
    assert merged.extra["easy_apply"] is False
    assert merged.extra["notes"] == "from the sheet"
    assert merged.extra["alt_urls"] == [LINKEDIN]
    assert merged.extra["merged_sources"] == ["workbook"]  # both default to the workbook source


def test_alt_urls_list_every_distinct_other_url_once() -> None:
    items = [op(WORKDAY), op(LINKEDIN), op(INDEED), op(LINKEDIN + "?trk=x"), op(UNKNOWN_HOST)]
    (merged,) = dedupe(items)
    assert merged.url == WORKDAY
    assert merged.extra["alt_urls"] == [
        UNKNOWN_HOST,
        LINKEDIN,
        INDEED,
    ]  # best first, no duplicates of one page


def test_alt_urls_include_apply_urls() -> None:
    a = op(WORKDAY)
    b = op(LINKEDIN, apply_url=INDEED)
    (merged,) = dedupe([a, b])
    assert INDEED in merged.extra["alt_urls"] and LINKEDIN in merged.extra["alt_urls"]


def test_provenance_lists_survive_a_second_merge() -> None:
    first = dedupe([op(WORKDAY), op(LINKEDIN, source=OpportunitySource.LINKEDIN)])
    assert first[0].extra["alt_urls"] == [LINKEDIN]
    second = dedupe([*first, op(INDEED, source=OpportunitySource.INDEED)])
    (merged,) = second
    assert merged.extra["alt_urls"] == [LINKEDIN, INDEED]
    assert merged.extra["merged_sources"] == ["indeed", "linkedin", "workbook"]


def test_records_that_never_merge_get_no_provenance_keys() -> None:
    (only,) = dedupe([op(extra={"a": 1})])
    assert only.extra == {"a": 1}


def test_assumption_flags_are_dropped_once_another_record_has_evidence() -> None:
    assumed = op(WORKDAY, term="Summer 2027", extra={"term_assumed": True, "date_unknown": True})
    explicit = op(LINKEDIN, term="Summer 2027", last_verified=date(2026, 9, 20))
    (merged,) = dedupe([assumed, explicit])
    assert "term_assumed" not in merged.extra
    assert "date_unknown" not in merged.extra  # a date now exists


def test_assumption_flags_stay_when_nobody_has_evidence() -> None:
    a = op(WORKDAY, term="Summer 2027", extra={"term_assumed": True, "date_unknown": True})
    b = op(LINKEDIN, term=None, extra={"date_unknown": True})
    (merged,) = dedupe([a, b])
    assert merged.extra["term_assumed"] is True
    assert merged.extra["date_unknown"] is True


# --------------------------------------------------------------------------------------------- fingerprints


def test_records_without_a_city_join_the_only_located_group() -> None:
    located = op(WORKDAY, location="Austin, TX", source=OpportunitySource.WORKBOOK)
    cityless = op(LINKEDIN, location=None, source=OpportunitySource.LINKEDIN)
    assert located.fingerprint != cityless.fingerprint
    for order in ([located, cityless], [cityless, located]):
        (merged,) = dedupe(order)
        assert merged.url == WORKDAY
        assert merged.fingerprint == located.fingerprint  # the merged record names the city
        assert merged.location == "Austin, TX"


def test_ambiguous_cityless_records_stay_separate() -> None:
    austin = op(WORKDAY, location="Austin, TX")
    dallas = op(
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Dallas-TX/Product-Intern_R2",
        location="Dallas, TX",
    )
    nowhere = op(LINKEDIN, location=None)
    result = dedupe([austin, dallas, nowhere])
    assert len(result) == 3


def test_two_cityless_records_merge_with_each_other() -> None:
    assert len(dedupe([op(LINKEDIN, location=None), op(INDEED, location=None)])) == 1


def test_different_cities_are_different_roles() -> None:
    assert (
        len(dedupe([op(WORKDAY, location="Austin, TX"), op(LINKEDIN, location="Dallas, TX")])) == 2
    )


def test_remote_is_a_city_of_its_own() -> None:
    assert len(dedupe([op(WORKDAY, location="Remote"), op(LINKEDIN, location="Austin, TX")])) == 2
    assert len(dedupe([op(WORKDAY, location="Remote (US)"), op(LINKEDIN, location="remote")])) == 1


def test_company_and_title_spelling_differences_still_match() -> None:
    a = op(WORKDAY, company="Acme, Inc.", title="Product Management Internship - Summer 2027")
    b = op(LINKEDIN, company="ACME", title="Product Management Intern (Summer 2027)")
    assert a.fingerprint == b.fingerprint
    assert len(dedupe([a, b])) == 1


def test_records_with_degenerate_fingerprints_are_never_merged() -> None:
    a = op(GH, company="", title="Intern")
    b = op(WORKDAY, company="", title="Intern")
    c = op(LINKEDIN, company="Acme", title="")
    d = op(INDEED, company="Acme", title="")
    assert len(dedupe([a, b, c, d])) == 4
    same_url = [op(GH, company="", title="Intern"), op(GH, company="", title="Intern")]
    assert len(dedupe(same_url)) == 1  # ... but the same id still merges


def test_merging_is_transitive() -> None:
    # a ~ b by id (tracking noise), b ~ c by fingerprint, c ~ d by id, d ~ e city-less
    a = op("https://boards.greenhouse.io/acme/jobs/1?gh_src=x", extra={"n": "a"})
    b = op("https://boards.greenhouse.io/acme/jobs/1", extra={"n": "b"})
    c = op(LINKEDIN, extra={"n": "c"})
    d = op(LINKEDIN + "?trk=1", extra={"n": "d"})
    e = op(INDEED, location=None, extra={"n": "e"})
    unrelated = op(
        "https://boards.greenhouse.io/globex/jobs/9", company="Globex", title="Analyst Intern"
    )
    result = dedupe([a, unrelated, b, c, d, e])
    assert [o.company for o in result] == ["Acme", "Globex"]


def test_terms_do_not_split_a_fingerprint() -> None:
    a = op(WORKDAY, term="Summer 2027")
    b = op(LINKEDIN, term="Fall 2026")
    (merged,) = dedupe([a, b])
    assert merged.term == "Summer 2027"  # the winner's


# --------------------------------------------------------------------------------------------- invariants


def _population() -> list[Opportunity]:
    items: list[Opportunity] = []
    for n in range(6):
        company, title = f"Company {n}", f"Role {n} Intern"
        items.append(
            op(
                f"https://boards.greenhouse.io/c{n}/jobs/{n}",
                company=company,
                title=title,
                last_verified=date(2026, 9, 1 + n),
            )
        )
        items.append(
            op(
                f"https://boards.greenhouse.io/c{n}/jobs/{n}?gh_src=abc",
                company=company,
                title=title,
            )
        )
        items.append(
            op(
                f"https://www.linkedin.com/jobs/view/{1000 + n}",
                company=company,
                title=title,
                location="Austin, Texas",
                last_verified=date(2026, 9, 10 + n),
                source=OpportunitySource.LINKEDIN,
            )
        )
        if n % 2:
            items.append(
                op(
                    f"https://www.indeed.com/viewjob?jk={n:016x}",
                    company=company,
                    title=title,
                    location=None,
                    source=OpportunitySource.INDEED,
                )
            )
    items.append(op(WORKDAY, company="Solo", title="Only Intern"))
    return items


def _canonical(items: list[Opportunity]) -> list[tuple[object, ...]]:
    return sorted(
        (
            o.id,
            o.url,
            o.fingerprint,
            o.last_verified,
            o.location,
            tuple(o.extra.get("alt_urls", [])),
            tuple(o.extra.get("merged_sources", [])),
        )
        for o in items
    )


def test_result_is_idempotent() -> None:
    once = dedupe(_population())
    assert dedupe(once) == once


@pytest.mark.parametrize("seed", range(8))
def test_result_does_not_depend_on_input_order(seed: int) -> None:
    items = _population()
    expected = _canonical(dedupe(items))
    shuffled = items[:]
    random.Random(seed).shuffle(shuffled)
    assert _canonical(dedupe(shuffled)) == expected


def test_population_collapses_as_expected() -> None:
    result = dedupe(_population())
    assert len(result) == 7  # six roles + the solo one
    assert len({o.id for o in result}) == len({o.fingerprint for o in result}) == 7
    for o in result:
        if o.company.startswith("Company"):
            assert "greenhouse.io" in o.url  # the direct ATS record won
            assert o.last_verified is not None and o.last_verified >= date(
                2026, 9, 10
            )  # freshest of the group


def test_inputs_are_never_mutated() -> None:
    items = _population()
    before = copy.deepcopy(items)
    dedupe(items)
    assert items == before


def test_merged_records_do_not_share_mutable_state_with_the_inputs() -> None:
    winner = op(WORKDAY, extra={"nested": {"a": 1}})
    other = op(LINKEDIN)
    (merged,) = dedupe([winner, other])
    merged.extra["nested"]["a"] = 2
    merged.extra["alt_urls"].append("x")
    assert winner.extra == {"nested": {"a": 1}}


def test_output_order_follows_first_appearance_of_each_group() -> None:
    a1 = op(GH, company="A", title="Intern")
    b1 = op(WORKDAY, company="B", title="Intern")
    a2 = op(LINKEDIN, company="A", title="Intern")
    c1 = op(INDEED, company="C", title="Intern")
    b2 = op("https://jobs.lever.co/b/1", company="B", title="Intern")
    result = dedupe([a1, b1, a2, c1, b2])
    assert [o.company for o in result] == ["A", "B", "C"]
