"""count_submitted_on: the daily cap is counted from the file by LOCAL calendar day (submitted_at)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from autoapply.clock import FakeClock, local_day, local_day_bounds_utc
from autoapply.db import Database, Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    Reason,
    RunMode,
)

CHICAGO = "America/Chicago"
S = ApplicationStatus
US = timedelta(microseconds=1)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def op(repo: Repo, make_op: Callable[..., Opportunity]) -> Opportunity:
    return repo.upsert_opportunity(make_op())[0]


def record(
    repo: Repo,
    clock: FakeClock,
    opportunity_id: str,
    when: datetime,
    status: ApplicationStatus = S.SUBMITTED,
    mode: RunMode = RunMode.FULL_AUTO,
    reason: Reason | None = None,
) -> None:
    """Create + finish one attempt with the clock pinned to ``when`` (so submitted_at == when)."""
    clock.set(when)
    app = repo.create_application(opportunity_id, mode)
    repo.finish_application(app.id, ApplyResult(status=status, reason=reason))


# ------------------------------------------------------------------------------------ basics


def test_counts_submitted_and_unconfirmed_only(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    noon = utc(2026, 9, 29, 17, 0)  # 12:00 in Chicago
    record(repo, fake_clock, op.id, noon, S.SUBMITTED)
    record(repo, fake_clock, op.id, noon, S.SUBMITTED_UNCONFIRMED)
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 2


def test_dry_run_failed_needs_manual_skipped_and_applying_are_not_counted(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    noon = utc(2026, 9, 29, 17, 0)
    record(repo, fake_clock, op.id, noon, S.DRY_RUN_OK, RunMode.DRY_RUN)
    record(repo, fake_clock, op.id, noon, S.FAILED, reason=Reason.TIMEOUT)
    record(repo, fake_clock, op.id, noon, S.NEEDS_MANUAL, reason=Reason.MISSING_ANSWER)
    record(repo, fake_clock, op.id, noon, S.SKIPPED, reason=Reason.POSTING_CLOSED)
    repo.create_application(op.id, RunMode.FULL_AUTO)  # still applying
    repo.mark_manually_applied(op.id)  # the user's own application is not ours to count
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 0


def test_a_submission_recorded_in_dry_run_mode_is_never_counted(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    record(repo, fake_clock, op.id, utc(2026, 9, 29, 17, 0), S.SUBMITTED, RunMode.DRY_RUN)
    record(repo, fake_clock, op.id, utc(2026, 9, 29, 17, 0), S.SUBMITTED, RunMode.DISCOVER_ONLY)
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 1  # only the discover_only one


def test_the_day_is_decided_by_submitted_at_not_started_at(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    fake_clock.set(utc(2026, 9, 30, 4, 59))  # 23:59 on Sep 29 in Chicago
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    fake_clock.set(utc(2026, 9, 30, 5, 2))  # submitted after local midnight
    repo.finish_application(app.id, ApplyResult(status=S.SUBMITTED))
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 0
    assert repo.count_submitted_on(date(2026, 9, 30), CHICAGO) == 1


def test_an_empty_database_and_far_days_count_zero(repo: Repo) -> None:
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 0
    assert repo.count_submitted_on(date(1999, 1, 1), CHICAGO) == 0
    assert repo.count_submitted_on(date(2099, 12, 31), "UTC") == 0


def test_tz_may_be_a_name_or_a_zoneinfo(repo: Repo, op: Opportunity, fake_clock: FakeClock) -> None:
    record(repo, fake_clock, op.id, utc(2026, 9, 29, 17, 0))
    day = date(2026, 9, 29)
    assert (
        repo.count_submitted_on(day, CHICAGO)
        == repo.count_submitted_on(day, ZoneInfo(CHICAGO))
        == 1
    )


def test_the_cap_resets_on_the_next_local_day_and_yesterday_stays_counted(
    repo: Repo, make_op: Callable[..., Opportunity], fake_clock: FakeClock
) -> None:
    ops = [repo.upsert_opportunity(make_op())[0] for _ in range(7)]
    for i in range(5):  # a full day of five applications
        record(repo, fake_clock, ops[i].id, utc(2026, 9, 29, 15, 0) + timedelta(minutes=30 * i))
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 5
    assert repo.count_submitted_on(date(2026, 9, 30), CHICAGO) == 0  # a new local day starts fresh
    record(repo, fake_clock, ops[5].id, utc(2026, 9, 30, 15, 0))
    assert repo.count_submitted_on(date(2026, 9, 30), CHICAGO) == 1
    assert repo.count_submitted_on(date(2026, 9, 29), CHICAGO) == 5  # history is not rewritten


# ------------------------------------------------------------------------------------ restart safety


def test_the_count_survives_a_restart_with_a_brand_new_database_instance(
    db_path: Path, make_op: Callable[..., Opportunity], fake_clock: FakeClock
) -> None:
    first = Database(db_path)
    repo = Repo(first, fake_clock)
    ops = [repo.upsert_opportunity(make_op())[0] for _ in range(3)]
    for i, o in enumerate(ops):
        record(repo, fake_clock, o.id, utc(2026, 9, 29, 14 + i, 0))
    first.close()  # the process "exits"

    restarted = Database(db_path)
    try:
        after = Repo(restarted, FakeClock(utc(2026, 9, 29, 20, 0)))
        assert after.count_submitted_on(date(2026, 9, 29), CHICAGO) == 3
        assert all(after.has_submitted(o.id) for o in ops)  # and so is the never-apply-twice guard
        assert after.stats(CHICAGO)["submitted_today"] == 3
    finally:
        restarted.close()


def test_two_live_repos_on_one_file_agree_immediately(
    db_path: Path, make_op: Callable[..., Opportunity], fake_clock: FakeClock
) -> None:
    a, b = Database(db_path), Database(db_path)
    try:
        writer, reader = Repo(a, fake_clock), Repo(b, fake_clock)
        o = writer.upsert_opportunity(make_op())[0]
        assert reader.count_submitted_on(date(2026, 9, 29), CHICAGO) == 0
        record(writer, fake_clock, o.id, utc(2026, 9, 29, 17, 0))
        assert reader.count_submitted_on(date(2026, 9, 29), CHICAGO) == 1
    finally:
        a.close()
        b.close()


# ------------------------------------------------------------------------------------ midnight edges


def test_midnight_edges_in_chicago_daylight_time(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    # CDT is UTC-5: local midnight is 05:00 UTC. Day Sep 29 = [Sep 29 05:00Z, Sep 30 05:00Z).
    edges = {
        "last instant of Sep 28": utc(2026, 9, 29, 5, 0) - US,
        "first instant of Sep 29": utc(2026, 9, 29, 5, 0),
        "last instant of Sep 29": utc(2026, 9, 30, 5, 0) - US,
        "first instant of Sep 30": utc(2026, 9, 30, 5, 0),
    }
    for when in edges.values():
        record(repo, fake_clock, op.id, when)
    counts = [repo.count_submitted_on(date(2026, 9, d), CHICAGO) for d in (28, 29, 30)]
    assert counts == [1, 2, 1]
    # and the local wall clock agrees with where we put the edges
    assert local_day(edges["last instant of Sep 28"], CHICAGO) == date(2026, 9, 28)
    assert local_day(edges["first instant of Sep 29"], CHICAGO) == date(2026, 9, 29)
    assert local_day(edges["last instant of Sep 29"], CHICAGO) == date(2026, 9, 29)
    assert local_day(edges["first instant of Sep 30"], CHICAGO) == date(2026, 9, 30)


def test_midnight_edges_in_chicago_standard_time(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    # CST is UTC-6: local midnight is 06:00 UTC. Day Jan 15 = [Jan 15 06:00Z, Jan 16 06:00Z).
    for when in (utc(2027, 1, 15, 6, 0) - US, utc(2027, 1, 15, 6, 0), utc(2027, 1, 16, 6, 0) - US):
        record(repo, fake_clock, op.id, when)
    assert repo.count_submitted_on(date(2027, 1, 14), CHICAGO) == 1
    assert repo.count_submitted_on(date(2027, 1, 15), CHICAGO) == 2


def test_spring_forward_day_is_23_hours_long(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    # 2027-03-14: clocks jump 02:00 CST -> 03:00 CDT. Mar 13 = [Mar 13 06:00Z, Mar 14 06:00Z),
    # Mar 14 = [Mar 14 06:00Z, Mar 15 05:00Z) (23 h), Mar 15 = [Mar 15 05:00Z, Mar 16 05:00Z).
    assert local_day_bounds_utc(date(2027, 3, 14), CHICAGO) == (
        utc(2027, 3, 14, 6),
        utc(2027, 3, 15, 5),
    )
    for when in (
        utc(2027, 3, 14, 6, 0) - US,  # Mar 13, 23:59:59.999999 CST
        utc(2027, 3, 14, 6, 0),  # Mar 14, 00:00 CST
        utc(2027, 3, 14, 8, 30),  # 02:30 local does not exist; this instant is 03:30 CDT
        utc(2027, 3, 15, 5, 0) - US,  # Mar 14, 23:59:59.999999 CDT
        utc(2027, 3, 15, 5, 0),  # Mar 15, 00:00 CDT
    ):
        record(repo, fake_clock, op.id, when)
    counts = [repo.count_submitted_on(date(2027, 3, d), CHICAGO) for d in (13, 14, 15)]
    assert counts == [1, 3, 1]


def test_fall_back_day_is_25_hours_long(repo: Repo, op: Opportunity, fake_clock: FakeClock) -> None:
    # 2026-11-01: clocks fall back 02:00 CDT -> 01:00 CST. Oct 31 = [Oct 31 05:00Z, Nov 1 05:00Z),
    # Nov 1 = [Nov 1 05:00Z, Nov 2 06:00Z) (25 h), Nov 2 = [Nov 2 06:00Z, Nov 3 06:00Z).
    assert local_day_bounds_utc(date(2026, 11, 1), CHICAGO) == (
        utc(2026, 11, 1, 5),
        utc(2026, 11, 2, 6),
    )
    for when in (
        utc(2026, 11, 1, 5, 0) - US,  # Oct 31, 23:59:59.999999 CDT
        utc(2026, 11, 1, 5, 0),  # Nov 1, 00:00 CDT
        utc(2026, 11, 1, 6, 30),  # 01:30 CDT (first pass of the repeated hour)
        utc(2026, 11, 1, 7, 30),  # 01:30 CST (second pass)
        utc(2026, 11, 2, 6, 0) - US,  # Nov 1, 23:59:59.999999 CST
        utc(2026, 11, 2, 6, 0),  # Nov 2, 00:00 CST
    ):
        record(repo, fake_clock, op.id, when)
    counts = [
        repo.count_submitted_on(date(2026, d[0], d[1]), CHICAGO)
        for d in ((10, 31), (11, 1), (11, 2))
    ]
    assert counts == [1, 4, 1]


@pytest.mark.parametrize(
    ("tz", "day", "start", "end"),
    [
        ("UTC", date(2026, 9, 29), utc(2026, 9, 29, 0), utc(2026, 9, 30, 0)),
        ("Asia/Kolkata", date(2026, 9, 29), utc(2026, 9, 28, 18, 30), utc(2026, 9, 29, 18, 30)),
        ("Pacific/Auckland", date(2026, 9, 29), utc(2026, 9, 28, 11), utc(2026, 9, 29, 11)),
        ("America/Los_Angeles", date(2026, 9, 29), utc(2026, 9, 29, 7), utc(2026, 9, 30, 7)),
        ("America/Los_Angeles", date(2026, 11, 1), utc(2026, 11, 1, 7), utc(2026, 11, 2, 8)),
    ],
)
def test_other_timezones_use_their_own_local_midnights(
    repo: Repo,
    op: Opportunity,
    fake_clock: FakeClock,
    tz: str,
    day: date,
    start: datetime,
    end: datetime,
) -> None:
    for when in (start - US, start, end - US, end):
        record(repo, fake_clock, op.id, when)
    assert repo.count_submitted_on(day, tz) == 2  # start and end - 1us
    assert repo.count_submitted_on(day - timedelta(days=1), tz) == 1  # start - 1us: the day before
    # end: first instant of the next
    assert repo.count_submitted_on(day + timedelta(days=1), tz) == 1


@pytest.mark.parametrize(
    ("first_day", "label"),
    [(date(2026, 10, 30), "fall back"), (date(2027, 3, 12), "spring forward")],
)
def test_every_submission_belongs_to_exactly_one_local_day_across_a_dst_change(
    repo: Repo, op: Opportunity, fake_clock: FakeClock, first_day: date, label: str
) -> None:
    """Sweep half-hourly (plus the exact day bounds) over five days around a DST change. Per-day counts
    must equal what the wall-clock ``local_day`` says, and together they must partition the submissions:
    nothing lost and nothing double counted at the transition."""
    days = [first_day + timedelta(days=i) for i in range(-1, 6)]  # one spare day on each side
    instants = {
        utc(first_day.year, first_day.month, first_day.day) + timedelta(minutes=30 * i)
        for i in range(5 * 48)
    }
    for d in days[1:-1]:
        start, end = local_day_bounds_utc(d, CHICAGO)
        instants |= {start - US, start, end - US, end}
    expected = Counter(local_day(when, CHICAGO) for when in instants)
    assert set(expected) <= set(days)  # the queried days really cover every instant
    for when in sorted(instants):
        record(repo, fake_clock, op.id, when)
    got = {d: repo.count_submitted_on(d, CHICAGO) for d in days}
    assert got == {d: expected[d] for d in days}, label
    assert sum(got.values()) == len(instants)  # a partition: no gaps, no overlaps
    lengths = {d: local_day_bounds_utc(d, CHICAGO) for d in days}
    hours = sorted({(b - a) // timedelta(hours=1) for a, b in lengths.values()})
    assert hours in ([24, 25], [23, 24]), hours  # the sweep really crossed a 25h / 23h day
