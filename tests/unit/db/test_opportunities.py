"""upsert_opportunity merge rules, scores, JSON hygiene."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from autoapply.clock import FakeClock
from autoapply.db import Repo, merge_opportunities
from autoapply.models import ATS, Opportunity, OpportunitySource, ScoreResult

MakeOp = Callable[..., Opportunity]

D1, D2 = date(2026, 9, 1), date(2026, 9, 10)


def _score(value: float = 80.0, passed: bool = True, **fields: Any) -> ScoreResult:
    return ScoreResult(score=value, passed=passed, reasons=["title matches"], **fields)


# ------------------------------------------------------------------------------------ insert / timestamps


def test_new_opportunity_is_inserted_with_seen_timestamps(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    op = make_op()
    stored, is_new = repo.upsert_opportunity(op)
    assert is_new is True
    assert stored.id == op.id
    assert stored.first_seen == stored.last_seen == fake_clock.now()
    assert stored.first_seen is not None and stored.first_seen.tzinfo is not None
    assert stored.score is None
    assert repo.get_opportunity(op.id) == stored


def test_upsert_round_trips_every_field(repo: Repo) -> None:
    op = Opportunity(
        company="Globex Corporation",
        title="Technical Program Manager Intern",
        url="https://boards.greenhouse.io/globex/jobs/1",
        apply_url="https://boards.greenhouse.io/globex/jobs/1/apply",
        location="Remote (US)",
        term="Summer 2027",
        source=OpportunitySource.GREENHOUSE,
        ats=ATS.GREENHOUSE,
        is_open=True,
        posted_date=date(2026, 9, 20),
        last_verified=date(2026, 9, 25),
        deadline=date(2026, 12, 1),
        description="Line one.\r\nLine two — with ünïcode ✓",
        extra={"notes": "great team", "n": 3, "nested": {"a": [1, 2, {"b": None}]}},
    )
    stored, _ = repo.upsert_opportunity(op)
    for name in [
        "id",
        "company",
        "title",
        "url",
        "apply_url",
        "location",
        "term",
        "source",
        "ats",
        "is_open",
        "posted_date",
        "last_verified",
        "deadline",
        "description",
        "extra",
        "fingerprint",
    ]:
        assert getattr(stored, name) == getattr(op, name), name


def test_reingest_keeps_first_seen_and_bumps_last_seen(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    op = make_op()
    first, _ = repo.upsert_opportunity(op)
    fake_clock.advance(timedelta(days=2, hours=3))
    again, is_new = repo.upsert_opportunity(op)
    assert is_new is False
    assert again.first_seen == first.first_seen
    assert again.last_seen == fake_clock.now()
    assert again.last_seen > first.last_seen


def test_a_new_row_keeps_an_explicit_first_seen_but_never_one_from_the_future(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    long_ago = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
    stored, is_new = repo.upsert_opportunity(make_op(first_seen=long_ago))
    assert is_new and stored.first_seen == long_ago
    assert stored.last_seen == fake_clock.now()  # "last seen" is always now

    future = fake_clock.now() + timedelta(days=30)
    clamped, _ = repo.upsert_opportunity(make_op(first_seen=future))
    assert clamped.first_seen == fake_clock.now()  # a first_seen after now is impossible: clamp

    naive = datetime(
        2026, 3, 1, 12, 0
    )  # a provider forgot the timezone: treated as UTC, not a crash
    assumed_utc, _ = repo.upsert_opportunity(make_op(first_seen=naive))
    assert assumed_utc.first_seen == naive.replace(tzinfo=UTC)


def test_an_existing_row_keeps_its_stored_first_seen_whatever_the_record_claims(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    op = make_op()
    first, _ = repo.upsert_opportunity(op)
    fake_clock.advance(timedelta(days=1))
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    again, is_new = repo.upsert_opportunity(op.model_copy(update={"first_seen": earlier}))
    assert not is_new and again.first_seen == first.first_seen


def test_same_id_is_one_row_even_when_url_noise_differs(repo: Repo, make_op: MakeOp) -> None:
    a = make_op(url="https://www.Acme.example.test/jobs/7?utm_source=x")
    b = make_op(url="https://acme.example.test/jobs/7/")
    assert a.id == b.id
    repo.upsert_opportunity(a)
    _, is_new = repo.upsert_opportunity(b)
    assert is_new is False
    assert repo.count_opportunities() == 1


def test_different_ids_with_same_fingerprint_stay_separate_rows(
    repo: Repo, make_op: MakeOp
) -> None:
    a = make_op(title="Strategy Intern", url="https://acme.example.test/a")
    b = make_op(title="Strategy Intern", url="https://acme.example.test/b")
    assert a.fingerprint == b.fingerprint and a.id != b.id
    repo.upsert_opportunity(a)
    repo.upsert_opportunity(b)
    assert repo.count_opportunities() == 2


# ------------------------------------------------------------------------------------ field merging


def test_non_empty_incoming_fields_override_and_empty_ones_do_not(repo: Repo) -> None:
    base = Opportunity(
        company="Acme",
        title="PM Intern",
        url="https://acme.example.test/j/1",
        apply_url="https://acme.example.test/j/1/apply",
        location="Austin, TX",
        term="Summer 2027",
        posted_date=date(2026, 9, 1),
        deadline=date(2026, 11, 1),
    )
    repo.upsert_opportunity(base)
    sparse = Opportunity(
        id=base.id, company="Acme", title="PM Intern", url="", location="", term=None
    )
    stored, _ = repo.upsert_opportunity(sparse)
    assert stored.url == base.url
    assert stored.apply_url == base.apply_url
    assert stored.location == "Austin, TX"
    assert stored.term == "Summer 2027"
    assert stored.posted_date == date(2026, 9, 1)
    assert stored.deadline == date(2026, 11, 1)

    rich = base.model_copy(
        update={
            "title": "Product Management Intern",
            "url": "https://acme.example.test/j/1?x=1",
            "location": "Remote",
            "term": "Summer 2027 (Cohort B)",
            "posted_date": date(2026, 9, 5),
            "deadline": date(2026, 12, 15),
            "apply_url": "https://acme.example.test/j/1/apply2",
        }
    )
    stored, _ = repo.upsert_opportunity(rich)
    assert (stored.title, stored.location, stored.term) == (
        "Product Management Intern",
        "Remote",
        "Summer 2027 (Cohort B)",
    )
    assert stored.url == "https://acme.example.test/j/1?x=1"
    assert stored.apply_url == "https://acme.example.test/j/1/apply2"
    assert stored.posted_date == date(2026, 9, 5)
    assert stored.deadline == date(2026, 12, 15)


def test_unknown_ats_never_wipes_a_known_one_but_a_known_one_updates(repo: Repo) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/1")
    repo.upsert_opportunity(base.model_copy(update={"ats": ATS.WORKDAY}))
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"ats": ATS.UNKNOWN}))
    assert stored.ats == ATS.WORKDAY
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"ats": ATS.GREENHOUSE}))
    assert stored.ats == ATS.GREENHOUSE


def test_source_follows_the_incoming_record(repo: Repo) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/2")
    repo.upsert_opportunity(base.model_copy(update={"source": OpportunitySource.LINKEDIN}))
    stored, _ = repo.upsert_opportunity(
        base.model_copy(update={"source": OpportunitySource.GREENHOUSE})
    )
    assert stored.source == OpportunitySource.GREENHOUSE


@pytest.mark.parametrize(
    ("stored_text", "incoming_text", "expected"),
    [
        ("short", "a much longer description", "a much longer description"),
        ("a much longer description", "short", "a much longer description"),
        ("same length A", "same length B", "same length A"),  # tie keeps what we have
        (None, "first description", "first description"),
        ("kept", None, "kept"),
        ("kept", "", "kept"),
        ("", "now filled", "now filled"),
    ],
)
def test_description_keeps_the_longer_text(
    repo: Repo, stored_text: str | None, incoming_text: str | None, expected: str
) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/3")
    repo.upsert_opportunity(base.model_copy(update={"description": stored_text}))
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"description": incoming_text}))
    assert stored.description == expected


def test_extra_is_dict_merged_with_incoming_winning_conflicts(repo: Repo) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/4")
    repo.upsert_opportunity(
        base.model_copy(update={"extra": {"keep": 1, "clash": "old", "n": {"a": 1}}})
    )
    stored, _ = repo.upsert_opportunity(
        base.model_copy(update={"extra": {"clash": "new", "added": True, "n": {"b": 2}}})
    )
    # shallow merge: nested values are replaced, not deep-merged
    assert stored.extra == {"keep": 1, "clash": "new", "added": True, "n": {"b": 2}}
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"extra": {}}))
    assert stored.extra == {"keep": 1, "clash": "new", "added": True, "n": {"b": 2}}


def test_extra_with_non_json_values_degrades_gracefully(repo: Repo, tmp_path: Path) -> None:
    op = Opportunity(
        company="Acme",
        title="PM Intern",
        url="https://x.example.test/5",
        extra={
            "opens": datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
            "closes": date(2026, 12, 1),
            "tags": {"b", "a"},
            "path": tmp_path / "resume.pdf",
            "pay": Decimal("21.50"),
            "raw": b"\x00\x01",
        },
    )
    stored, _ = repo.upsert_opportunity(op)
    assert stored.extra["opens"] == "2026-09-01T08:30:00+00:00"
    assert stored.extra["closes"] == "2026-12-01"
    assert stored.extra["tags"] == ["a", "b"]
    assert stored.extra["path"] == str(tmp_path / "resume.pdf")
    assert stored.extra["pay"] == "21.50"
    assert isinstance(stored.extra["raw"], str)


def test_extra_values_survive_lone_surrogates_nul_bytes_and_emoji(repo: Repo) -> None:
    # pydantic rejects lone surrogates in str fields and dict keys, but ``extra`` VALUES are arbitrary
    nasty = 'bad \ud800 surrogate, NUL \u0000, emoji 🚀, CRLF \r\n, quote " and backslash \\'
    extra = {"raw": nasty, "nested": [nasty, {"k": nasty}]}
    op = Opportunity(
        company="Acme", title="PM Intern", url="https://x.example.test/nasty", extra=extra
    )
    stored, _ = repo.upsert_opportunity(op)
    assert stored.extra == extra
    assert repo.get_opportunity(op.id).extra == extra


def test_last_verified_never_moves_backwards(repo: Repo) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/6")
    repo.upsert_opportunity(base.model_copy(update={"last_verified": D2}))
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"last_verified": D1}))
    assert stored.last_verified == D2
    stored, _ = repo.upsert_opportunity(base.model_copy(update={"last_verified": None}))
    assert stored.last_verified == D2
    stored, _ = repo.upsert_opportunity(
        base.model_copy(update={"last_verified": date(2026, 10, 1)})
    )
    assert stored.last_verified == date(2026, 10, 1)


def test_fingerprint_tracks_merged_fields_unless_the_provider_set_its_own(repo: Repo) -> None:
    located = Opportunity(
        company="Acme, Inc.",
        title="Strategy Intern",
        url="https://x.example.test/8",
        location="Austin, TX",
    )
    stored, _ = repo.upsert_opportunity(located)
    assert stored.fingerprint == "acme|strategy intern|austin"
    # incoming record without a location: the merged record keeps Austin, so the fingerprint must too
    stored, _ = repo.upsert_opportunity(
        Opportunity(id=located.id, company="Acme, Inc.", title="Strategy Intern", url=located.url)
    )
    assert stored.location == "Austin, TX"
    assert stored.fingerprint == "acme|strategy intern|austin"
    stored, _ = repo.upsert_opportunity(located.model_copy(update={"fingerprint": "custom-key"}))
    assert stored.fingerprint == "custom-key"


# ------------------------------------------------------------------------------------ is_open evidence


@pytest.mark.parametrize(
    ("stored_open", "stored_verified", "incoming_open", "incoming_verified", "expected"),
    [
        # both dated: the fresher (or equally fresh) record decides
        (True, D1, False, D2, False),
        (False, D1, True, D2, True),
        (True, D1, False, D1, False),
        (False, D1, True, D1, True),
        (True, D1, True, D2, True),
        (False, D1, False, D2, False),
        # both dated: a staler record can neither close nor re-open
        (True, D2, False, D1, True),
        (False, D2, True, D1, False),
        # a missing date: a CLOSED claim is believed (the safe direction) ...
        (True, None, False, D2, False),
        (True, D1, False, None, False),
        (True, None, False, None, False),
        # ... an OPEN claim proves nothing against a stored closed state
        (False, None, True, D2, False),
        (False, D1, True, None, False),
        (False, None, True, None, False),
        # open stays open
        (True, None, True, None, True),
        (True, D1, True, None, True),
        (True, None, True, D2, True),
    ],
)
def test_is_open_follows_the_evidence_rule(
    repo: Repo,
    stored_open: bool,
    stored_verified: date | None,
    incoming_open: bool,
    incoming_verified: date | None,
    expected: bool,
) -> None:
    base = Opportunity(company="Acme", title="PM Intern", url="https://x.example.test/open")
    repo.upsert_opportunity(
        base.model_copy(update={"is_open": stored_open, "last_verified": stored_verified})
    )
    stored, _ = repo.upsert_opportunity(
        base.model_copy(update={"is_open": incoming_open, "last_verified": incoming_verified})
    )
    assert stored.is_open is expected
    assert repo.get_opportunity(base.id).is_open is expected


def test_a_closed_new_record_is_stored_closed(repo: Repo, make_op: MakeOp) -> None:
    stored, _ = repo.upsert_opportunity(make_op(is_open=False))
    assert stored.is_open is False


# ------------------------------------------------------------------------------------ score handling


def test_reingest_never_wipes_the_stored_score(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    op = make_op()
    repo.upsert_opportunity(op)
    result = _score(
        77.5, True, role_family="strategy", matched_keywords=["strategy"], penalties=["p"]
    )
    assert repo.set_score(op.id, result) is True
    fake_clock.advance(timedelta(hours=6))
    longer = "A fresh and considerably longer description than the stored one. " * 3
    stored, _ = repo.upsert_opportunity(op.model_copy(update={"description": longer.strip()}))
    assert stored.score == result
    assert stored.description == longer.strip()
    assert repo.list_opportunities(min_score=77, passed_only=True) == [stored]


def test_incoming_score_replaces_the_stored_one(repo: Repo, make_op: MakeOp) -> None:
    op = make_op()
    repo.upsert_opportunity(op)
    repo.set_score(op.id, _score(50, False))
    replacement = _score(91, True, role_family="analytics")
    stored, _ = repo.upsert_opportunity(op.model_copy(update={"score": replacement}))
    assert stored.score == replacement


def test_a_new_record_can_arrive_already_scored(repo: Repo, make_op: MakeOp) -> None:
    op = make_op(score=_score(64, True))
    stored, is_new = repo.upsert_opportunity(op)
    assert is_new and stored.score is not None and stored.score.score == 64
    assert repo.count_opportunities(passed_only=True) == 1


def test_set_score_round_trips_the_whole_result(repo: Repo, make_op: MakeOp) -> None:
    op = make_op()
    repo.upsert_opportunity(op)
    result = ScoreResult(
        score=61.25,
        passed=False,
        role_family="business_analysis",
        matched_keywords=["business analyst", "ünï"],
        reasons=["Title matches business analysis.", "Location outside preferred list."],
        penalties=["non-US"],
    )
    repo.set_score(op.id, result)
    assert repo.get_opportunity(op.id).score == result


def test_set_score_replaces_and_reports_unknown_ids(repo: Repo, make_op: MakeOp) -> None:
    op = make_op()
    repo.upsert_opportunity(op)
    assert repo.set_score(op.id, _score(10, False)) is True
    assert repo.set_score(op.id, _score(90, True)) is True
    assert repo.get_opportunity(op.id).score.score == 90
    assert repo.set_score("does-not-exist", _score()) is False


def test_set_scores_updates_many_in_one_transaction(repo: Repo, make_op: MakeOp) -> None:
    ops = [make_op() for _ in range(4)]
    repo.upsert_opportunities(ops)
    updated = repo.set_scores(
        [(o.id, _score(10.0 * (i + 1))) for i, o in enumerate(ops)] + [("ghost", _score())]
    )
    assert updated == 4
    assert [repo.get_opportunity(o.id).score.score for o in ops] == [10.0, 20.0, 30.0, 40.0]


# ------------------------------------------------------------------------------------ bulk / misc


def test_bulk_upsert_returns_per_record_flags_in_order(repo: Repo, make_op: MakeOp) -> None:
    a, b = make_op(), make_op()
    repo.upsert_opportunity(a)
    results = repo.upsert_opportunities([a, b, b])
    assert [(r[0].id, r[1]) for r in results] == [(a.id, False), (b.id, True), (b.id, False)]


def test_bulk_upsert_is_atomic(repo: Repo, make_op: MakeOp) -> None:
    good = make_op()
    with pytest.raises(AttributeError):
        repo.upsert_opportunities([good, None])
    assert repo.get_opportunity(good.id) is None
    assert repo.count_opportunities() == 0


def test_get_unknown_opportunity_is_none(repo: Repo) -> None:
    assert repo.get_opportunity("nope") is None


def test_merge_opportunities_is_pure(make_op: MakeOp) -> None:
    current = make_op(description="old text", extra={"a": 1}, last_verified=D1)
    incoming = make_op(
        id=current.id,
        url=current.url,
        description="a longer replacement",
        extra={"b": 2},
        last_verified=D2,
    )
    before = (current.model_copy(deep=True), incoming.model_copy(deep=True))
    merged = merge_opportunities(current, incoming)
    assert merged.description == "a longer replacement"
    assert merged.extra == {"a": 1, "b": 2}
    assert merged.last_verified == D2
    assert (current, incoming) == before
    assert merged.first_seen == current.first_seen  # timestamps belong to the Repo, not the merge


def test_upserting_a_blank_padded_record_is_stripped_by_the_model(repo: Repo) -> None:
    op = Opportunity(company="  Acme  ", title="\tPM Intern\n", url="  https://x.example.test/pad ")
    stored, _ = repo.upsert_opportunity(op)
    assert (stored.company, stored.title, stored.url) == (
        "Acme",
        "PM Intern",
        "https://x.example.test/pad",
    )
