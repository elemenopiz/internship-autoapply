"""list_opportunities / count_opportunities filters, ordering, paging, search escaping, stats()."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from autoapply.clock import FakeClock
from autoapply.db import Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    OpportunitySource,
    Reason,
    RunMode,
    ScoreResult,
)

MakeOp = Callable[..., Opportunity]


def _score(value: float, passed: bool) -> ScoreResult:
    return ScoreResult(score=value, passed=passed, reasons=[f"score {value}"])


@pytest.fixture
def seeded(repo: Repo, make_op: MakeOp, fake_clock: FakeClock) -> dict[str, Opportunity]:
    """Seven opportunities, upserted one minute apart (so a < b < ... < g by first_seen)."""
    specs = {
        # name: (company, title, source, score, passed, is_open)
        "a": ("Acme Robotics", "Product Management Intern", "workbook", 90, True, True),
        "b": ("Globex", "Technical Program Manager Intern", "greenhouse", 72, True, True),
        "c": ("Initech", "Strategy Intern", "lever", 55, True, True),
        "d": ("Umbrella 100% Corp", "Business Analyst Intern", "workbook", 40, False, True),
        "e": ("Hooli", "Data_Analyst Intern", "ashby", None, None, True),
        "f": ("Café Société", "Ingénieur Stagiaire", "workbook", 65, True, False),
        "g": ("ÉCOLE Labs", "Operations Intern", "linkedin", 80, True, False),
    }
    out: dict[str, Opportunity] = {}
    for name, (company, title, source, score, passed, is_open) in specs.items():
        op = make_op(
            company=company,
            title=title,
            source=OpportunitySource(source),
            is_open=is_open,
            url=f"https://seed.example.test/{name}",
        )
        stored, _ = repo.upsert_opportunity(op)
        if score is not None:
            assert passed is not None
            repo.set_score(stored.id, _score(score, passed))
        out[name] = repo.get_opportunity(stored.id)
        fake_clock.advance(timedelta(minutes=1))
    return out


def _ids(ops: list[Opportunity], seeded: dict[str, Opportunity]) -> list[str]:
    by_id = {o.id: name for name, o in seeded.items()}
    return [by_id[o.id] for o in ops]


# ------------------------------------------------------------------------------------ ordering


def test_default_order_is_score_descending_with_unscored_last(
    repo: Repo, seeded: dict[str, Opportunity]
) -> None:
    assert _ids(repo.list_opportunities(), seeded) == list("agbfcde")


def test_score_ties_are_broken_by_oldest_first_seen(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    first, second, third = make_op(), make_op(), make_op()
    for op in (first, second, third):
        repo.upsert_opportunity(op)
        repo.set_score(op.id, _score(70, True))
        fake_clock.advance(timedelta(minutes=1))
    assert [o.id for o in repo.list_opportunities()] == [first.id, second.id, third.id]


def test_seen_desc_orders_by_last_seen_and_a_reingest_moves_a_row_to_the_top(
    repo: Repo, seeded: dict[str, Opportunity], fake_clock: FakeClock
) -> None:
    assert _ids(repo.list_opportunities(order="seen_desc"), seeded) == list("gfedcba")
    repo.upsert_opportunity(seeded["a"])  # seen again now
    assert _ids(repo.list_opportunities(order="seen_desc"), seeded)[0] == "a"
    # first_seen_desc is about discovery time, unaffected by re-ingest
    assert _ids(repo.list_opportunities(order="first_seen_desc"), seeded) == list("gfedcba")


def test_unknown_order_is_rejected(repo: Repo) -> None:
    with pytest.raises(ValueError, match="unknown order"):
        repo.list_opportunities(order="score_desc; DROP TABLE opportunities")


# ------------------------------------------------------------------------------------ simple filters


def test_min_score_is_inclusive_and_excludes_unscored(
    repo: Repo, seeded: dict[str, Opportunity]
) -> None:
    assert _ids(repo.list_opportunities(min_score=65), seeded) == list("agbf")
    assert _ids(repo.list_opportunities(min_score=55), seeded) == list("agbfc")
    assert _ids(repo.list_opportunities(min_score=0), seeded) == list("agbfcd")  # not e: unscored
    assert repo.list_opportunities(min_score=101) == []


def test_passed_only_uses_the_stored_verdict_not_the_number(
    repo: Repo, seeded: dict[str, Opportunity]
) -> None:
    assert _ids(repo.list_opportunities(passed_only=True), seeded) == list("agbfc")
    repo.set_score(seeded["d"].id, _score(99, False))  # high number, but the scorer said no
    assert _ids(repo.list_opportunities(passed_only=True), seeded) == list("agbfc")
    assert _ids(repo.list_opportunities(min_score=95), seeded) == ["d"]


def test_passed_only_combined_with_min_score(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert _ids(repo.list_opportunities(passed_only=True, min_score=70), seeded) == list("agb")


def test_source_filter_accepts_enum_or_string(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert _ids(repo.list_opportunities(source=OpportunitySource.WORKBOOK), seeded) == list("afd")
    assert _ids(repo.list_opportunities(source="greenhouse"), seeded) == ["b"]
    assert _ids(repo.list_opportunities(source="LinkedIn"), seeded) == ["g"]
    assert repo.list_opportunities(source="manual") == []
    with pytest.raises(ValueError):
        repo.list_opportunities(source="carrier-pigeon")


def test_is_open_filter(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert _ids(repo.list_opportunities(is_open=True), seeded) == list("abcde")
    assert _ids(repo.list_opportunities(is_open=False), seeded) == list("gf")
    assert repo.count_opportunities(is_open=True) == 5
    assert repo.count_opportunities(is_open=False) == 2
    assert repo.count_opportunities() == 7


def test_blank_string_filters_mean_no_filter(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert repo.count_opportunities(source="", search="   ", status="") == 7


# ------------------------------------------------------------------------------------ search


def test_search_matches_company_or_title_case_insensitively(
    repo: Repo, seeded: dict[str, Opportunity]
) -> None:
    assert _ids(repo.list_opportunities(search="ACME"), seeded) == ["a"]  # company
    assert _ids(repo.list_opportunities(search="program manager"), seeded) == ["b"]  # title
    assert _ids(repo.list_opportunities(search="  intern "), seeded) == list("agbcde")
    assert repo.list_opportunities(search="no such thing") == []


def test_search_treats_like_wildcards_literally(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert _ids(repo.list_opportunities(search="%"), seeded) == ["d"]  # only "100%" contains one
    assert _ids(repo.list_opportunities(search="100%"), seeded) == ["d"]
    assert _ids(repo.list_opportunities(search="_"), seeded) == ["e"]  # only "Data_Analyst"
    assert _ids(repo.list_opportunities(search="data_a"), seeded) == ["e"]
    assert repo.list_opportunities(search="Data.Analyst") == []
    assert repo.list_opportunities(search="\\") == []
    # would match "Acme Robotics ... Intern" as a wildcard
    assert repo.list_opportunities(search="a%t") == []


def test_search_folds_unicode_case(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert _ids(repo.list_opportunities(search="café"), seeded) == ["f"]
    assert _ids(repo.list_opportunities(search="CAFÉ SOCIÉTÉ"), seeded) == ["f"]
    assert _ids(repo.list_opportunities(search="école"), seeded) == ["g"]  # stored as "ÉCOLE"
    assert _ids(repo.list_opportunities(search="ingénieur"), seeded) == ["f"]


def test_search_matches_composed_and_decomposed_forms_alike(repo: Repo, make_op: MakeOp) -> None:
    repo.upsert_opportunity(make_op(company="Café Central"))  # 'e' + combining acute
    assert len(repo.list_opportunities(search="café central")) == 1  # precomposed query
    assert len(repo.list_opportunities(search="CAFÉ")) == 1


def test_search_is_injection_safe(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    assert repo.list_opportunities(search="'; DROP TABLE opportunities; --") == []
    assert repo.count_opportunities() == 7


# ------------------------------------------------------------------------------------ status filter


def test_status_filters_on_the_latest_application(
    repo: Repo, seeded: dict[str, Opportunity], fake_clock: FakeClock
) -> None:
    b, c = seeded["b"].id, seeded["c"].id
    app = repo.create_application(b, RunMode.FULL_AUTO)
    repo.finish_application(app.id, ApplyResult(status=ApplicationStatus.SUBMITTED))
    first = repo.create_application(c, RunMode.FULL_AUTO)
    repo.finish_application(
        first.id, ApplyResult(status=ApplicationStatus.FAILED, reason=Reason.TIMEOUT)
    )
    assert _ids(repo.list_opportunities(status="submitted"), seeded) == ["b"]
    assert _ids(repo.list_opportunities(status=ApplicationStatus.FAILED), seeded) == ["c"]
    second = repo.create_application(c, RunMode.FULL_AUTO)  # attempt 2 is now the latest
    assert _ids(repo.list_opportunities(status="applying"), seeded) == ["c"]
    assert repo.list_opportunities(status="failed") == []
    repo.finish_application(
        second.id, ApplyResult(status=ApplicationStatus.NEEDS_MANUAL, reason=Reason.MISSING_ANSWER)
    )
    assert _ids(repo.list_opportunities(status="needs_manual"), seeded) == ["c"]
    assert _ids(repo.list_opportunities(status="unapplied"), seeded) == list("agfde")
    assert repo.count_opportunities(status="unapplied") == 5


def test_status_open_and_closed_are_aliases_for_is_open(
    repo: Repo, seeded: dict[str, Opportunity]
) -> None:
    assert repo.count_opportunities(status="open") == 5
    assert _ids(repo.list_opportunities(status="closed"), seeded) == list("gf")


def test_unknown_status_is_rejected(repo: Repo) -> None:
    with pytest.raises(ValueError):
        repo.list_opportunities(status="teleported")


# ------------------------------------------------------------------------------------ paging & counts


def test_limit_and_offset_page_without_overlap(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    everything = _ids(repo.list_opportunities(), seeded)
    pages = [_ids(repo.list_opportunities(limit=3, offset=o), seeded) for o in (0, 3, 6)]
    assert pages == [everything[0:3], everything[3:6], everything[6:7]]
    assert repo.list_opportunities(limit=3, offset=7) == []
    assert repo.list_opportunities(limit=0) == []
    # offset without a limit
    assert _ids(repo.list_opportunities(offset=5), seeded) == everything[5:]


@pytest.mark.parametrize(("limit", "offset"), [(-1, 0), (1, -1)])
def test_negative_paging_is_rejected(repo: Repo, limit: int, offset: int) -> None:
    with pytest.raises(ValueError):
        repo.list_opportunities(limit=limit, offset=offset)


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"min_score": 60},
        {"passed_only": True},
        {"source": "workbook"},
        {"search": "intern"},
        {"is_open": True},
        {"is_open": False, "min_score": 60},
        {"search": "%"},
        {"status": "unapplied", "passed_only": True},
        {"source": "workbook", "is_open": True, "search": "a"},
    ],
)
def test_count_always_equals_the_unpaged_list_length(
    repo: Repo, seeded: dict[str, Opportunity], filters: dict[str, object]
) -> None:
    assert repo.count_opportunities(**filters) == len(repo.list_opportunities(**filters))


def test_listed_rows_carry_their_score_detail(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    top = repo.list_opportunities(limit=1)[0]
    assert top.score is not None and top.score.reasons == ["score 90"] and top.score.passed is True
    unscored = repo.list_opportunities(search="hooli")[0]
    assert unscored.score is None


def test_combined_filters_intersect(repo: Repo, seeded: dict[str, Opportunity]) -> None:
    got = repo.list_opportunities(min_score=60, source="workbook", is_open=True, search="acme")
    assert _ids(got, seeded) == ["a"]
    assert repo.list_opportunities(min_score=95, source="workbook") == []


# ------------------------------------------------------------------------------------ stats


def test_stats_on_an_empty_database_are_zero_filled(repo: Repo) -> None:
    stats = repo.stats()
    assert (
        stats["opportunities_total"] == stats["applications_total"] == stats["submitted_today"] == 0
    )
    assert stats["opportunities_by_source"] == {s.value: 0 for s in OpportunitySource}
    assert stats["applications_by_status"] == {s.value: 0 for s in ApplicationStatus}
    assert stats["day"] == "2026-09-29"


def test_stats_counts_by_status_source_and_today(
    repo: Repo, seeded: dict[str, Opportunity], fake_clock: FakeClock
) -> None:
    fake_clock.set(datetime(2026, 9, 29, 20, 0, tzinfo=UTC))  # 15:00 in Chicago
    for name, status in (
        ("a", ApplicationStatus.SUBMITTED),
        ("b", ApplicationStatus.SUBMITTED_UNCONFIRMED),
        ("c", ApplicationStatus.FAILED),
        ("d", ApplicationStatus.DRY_RUN_OK),
    ):
        mode = RunMode.DRY_RUN if status == ApplicationStatus.DRY_RUN_OK else RunMode.FULL_AUTO
        app = repo.create_application(seeded[name].id, mode)
        repo.finish_application(app.id, ApplyResult(status=status))
    repo.create_application(seeded["e"].id, RunMode.FULL_AUTO)  # still applying
    stats = repo.stats("America/Chicago")
    assert stats["opportunities_total"] == 7
    assert stats["opportunities_open"] == 5
    assert stats["opportunities_scored"] == 6
    assert stats["opportunities_passed"] == 5
    assert stats["opportunities_by_source"]["workbook"] == 3
    assert stats["opportunities_by_source"]["greenhouse"] == 1
    assert stats["applications_total"] == 5
    assert stats["applications_by_status"]["submitted"] == 1
    assert stats["applications_by_status"]["submitted_unconfirmed"] == 1
    assert stats["applications_by_status"]["failed"] == 1
    assert stats["applications_by_status"]["dry_run_ok"] == 1
    assert stats["applications_by_status"]["applying"] == 1
    assert stats["submitted_today"] == 2  # dry run / failed / applying never count


def test_stats_today_follows_the_requested_timezone(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    op = make_op()
    repo.upsert_opportunity(op)
    fake_clock.set(datetime(2026, 9, 30, 1, 0, tzinfo=UTC))  # 20:00 on 09-29 in Chicago
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    repo.finish_application(app.id, ApplyResult(status=ApplicationStatus.SUBMITTED))
    fake_clock.set(datetime(2026, 9, 30, 6, 0, tzinfo=UTC))  # 01:00 on 09-30 in Chicago
    chicago, utc = repo.stats("America/Chicago"), repo.stats("UTC")
    assert (chicago["day"], chicago["submitted_today"]) == ("2026-09-30", 0)
    assert (utc["day"], utc["submitted_today"]) == ("2026-09-30", 1)
    assert repo.stats()["day"] == "2026-09-30"  # default zone is Chicago
