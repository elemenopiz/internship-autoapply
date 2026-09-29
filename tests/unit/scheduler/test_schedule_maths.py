"""``next_run_at`` / ``due_slot``: weekdays, several times a day, jitter, DST, catch-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from autoapply.config import ScheduleConfig
from autoapply.scheduler import due_slot, next_run_at, slot_start

CHI = ZoneInfo("America/Chicago")


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def local(*a: int) -> datetime:
    return datetime(*a, tzinfo=CHI)


def sched(
    times: list[str] | None = None, days: list[int] | None = None, jitter: int = 0
) -> ScheduleConfig:
    return ScheduleConfig(
        enabled=True,
        run_times=["09:30"] if times is None else times,
        days_of_week=list(range(7)) if days is None else days,
        jitter_minutes=jitter,
    )


def nxt(s: ScheduleConfig, now: datetime, **kw: object) -> datetime | None:
    return next_run_at(s, "America/Chicago", now, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------ next_run_at


def test_next_run_is_today_when_the_time_is_still_ahead() -> None:
    assert nxt(sched(), local(2026, 9, 29, 8, 0)) == local(2026, 9, 29, 9, 30).astimezone(UTC)


def test_next_run_is_tomorrow_once_today_has_passed_and_is_strictly_after_now() -> None:
    assert nxt(sched(), local(2026, 9, 29, 9, 31)) == utc(2026, 9, 30, 14, 30)
    assert nxt(sched(), utc(2026, 9, 29, 14, 30)) == utc(2026, 9, 30, 14, 30)


def test_result_is_aware_utc() -> None:
    result = nxt(sched(), local(2026, 9, 29, 8, 0))
    assert result is not None and result.utcoffset() == timedelta(0)


def test_several_times_a_day_pick_the_next_one_in_order() -> None:
    s = sched(["21:00", "07:15", "13:45"])
    assert nxt(s, local(2026, 9, 29, 7, 0)) == local(2026, 9, 29, 7, 15).astimezone(UTC)
    assert nxt(s, local(2026, 9, 29, 8, 0)) == local(2026, 9, 29, 13, 45).astimezone(UTC)
    assert nxt(s, local(2026, 9, 29, 22, 0)) == local(2026, 9, 30, 7, 15).astimezone(UTC)


def test_weekdays_only_skips_the_weekend() -> None:
    s = sched(days=[0, 1, 2, 3, 4])  # Mon-Fri
    friday_evening = local(2026, 10, 2, 18, 0)
    assert friday_evening.weekday() == 4
    assert nxt(s, friday_evening) == local(2026, 10, 5, 9, 30).astimezone(UTC)  # Monday


def test_a_single_weekday_is_found_a_week_ahead() -> None:
    s = sched(days=[2])  # Wednesday
    tuesday = local(2026, 9, 29, 12, 0)
    assert nxt(s, tuesday) == local(2026, 9, 30, 9, 30).astimezone(UTC)
    wednesday_after = local(2026, 9, 30, 10, 0)
    assert nxt(s, wednesday_after) == local(2026, 10, 7, 9, 30).astimezone(UTC)


def test_weekday_is_judged_in_the_local_zone_not_utc() -> None:
    s = sched(["21:00"], days=[0])  # Monday 21:00 CDT is already Tuesday in UTC
    result = nxt(s, local(2026, 9, 27, 12, 0))  # Sunday
    assert result == utc(2026, 9, 29, 2, 0)
    assert result is not None and result.astimezone(CHI).weekday() == 0


@pytest.mark.parametrize(
    "s",
    [
        sched(times=[]), sched(days=[]), sched(times=["25:00", "9:5", "abc", ""]), sched(days=[7, -1, 99]),
    ],
)  # fmt: skip
def test_unusable_schedules_yield_none(s: ScheduleConfig) -> None:
    assert nxt(s, utc(2026, 9, 29, 12, 0)) is None


def test_bad_entries_are_ignored_but_good_ones_still_count() -> None:
    s = sched(["nope", " 9:30 ", "24:00"], days=[1, 99, 1])
    assert nxt(s, local(2026, 9, 29, 8, 0)) == local(2026, 9, 29, 9, 30).astimezone(UTC)


def test_naive_now_is_read_as_utc() -> None:
    assert nxt(sched(), datetime(2026, 9, 29, 8, 0)) == utc(2026, 9, 29, 14, 30)


def test_other_time_zones_work_and_accept_zoneinfo_objects() -> None:
    result = next_run_at(sched(), ZoneInfo("Asia/Kolkata"), utc(2026, 9, 29, 0, 0))
    assert result == utc(2026, 9, 29, 4, 0)


def test_last_slot_prevents_a_repeat_even_if_the_clock_goes_backwards() -> None:
    s = sched()
    ran = utc(2026, 9, 29, 14, 30)
    assert nxt(s, utc(2026, 9, 29, 14, 0), last_slot=ran) == utc(2026, 9, 30, 14, 30)
    assert nxt(s, utc(2026, 9, 29, 14, 0)) == ran


# ------------------------------------------------------------------------------------------ jitter


def test_jitter_is_deterministic_bounded_and_never_early() -> None:
    s = sched(jitter=10)
    now = local(2026, 9, 29, 8, 0)
    results = {nxt(s, now) for _ in range(5)}
    assert len(results) == 1
    (start,) = results
    assert start is not None
    nominal = local(2026, 9, 29, 9, 30).astimezone(UTC)
    assert nominal <= start <= nominal + timedelta(minutes=10)


def test_jitter_differs_between_slots_and_stays_in_range() -> None:
    s = sched(jitter=10)
    offsets = set()
    for day in range(1, 29):
        nominal = local(2026, 10, day, 9, 30).astimezone(UTC)
        offset = slot_start(s, nominal) - nominal
        assert timedelta(0) <= offset <= timedelta(minutes=10)
        offsets.add(offset)
    assert len(offsets) > 5


def test_jitter_zero_and_negative_mean_no_offset() -> None:
    nominal = utc(2026, 9, 29, 14, 30)
    assert slot_start(sched(jitter=0), nominal) == nominal
    assert slot_start(sched(jitter=-5), nominal) == nominal


def test_jitter_does_not_depend_on_the_evaluation_time() -> None:
    s = sched(jitter=15)
    a = nxt(s, local(2026, 9, 29, 1, 0))
    b = nxt(s, local(2026, 9, 29, 9, 0))
    assert a == b


# ------------------------------------------------------------------------------------------ DST


def test_spring_forward_nonexistent_time_runs_once_at_the_shifted_instant() -> None:
    s = sched(["02:30"])  # 2027-03-14 02:30 does not exist in Chicago
    result = nxt(s, local(2027, 3, 14, 0, 0))
    assert result == utc(2027, 3, 14, 8, 30)
    assert result is not None and result.astimezone(CHI).hour == 3
    after = nxt(s, result)
    assert after == local(2027, 3, 15, 2, 30).astimezone(UTC)


def test_two_times_collapsing_onto_one_instant_run_once() -> None:
    s = sched(["02:30", "03:30"])
    first = nxt(s, local(2027, 3, 14, 0, 0))
    assert first == utc(2027, 3, 14, 8, 30)
    assert nxt(s, first) == local(2027, 3, 15, 2, 30).astimezone(UTC)  # not 08:30 again


def test_fall_back_ambiguous_time_runs_once_at_the_first_occurrence() -> None:
    s = sched(["01:30"])  # 2026-11-01 01:30 happens twice
    first = nxt(s, utc(2026, 11, 1, 3, 0))
    assert first == utc(2026, 11, 1, 6, 30)  # 01:30 CDT
    assert nxt(s, first) == utc(
        2026, 11, 2, 7, 30
    )  # next day 01:30 CST; no second run at 07:30Z on Nov 1


def test_daily_slots_keep_their_local_time_across_the_transitions() -> None:
    s = sched()
    before = nxt(s, local(2027, 3, 13, 12, 0))
    after = nxt(s, local(2027, 3, 14, 12, 0))
    assert before == utc(2027, 3, 14, 15, 30) or before == local(2027, 3, 14, 9, 30).astimezone(UTC)
    assert after is not None and after.astimezone(CHI).strftime("%H:%M") == "09:30"
    assert after - utc(2027, 3, 14, 14, 30) == timedelta(hours=24)


# ------------------------------------------------------------------------------------------ due_slot


def due(
    s: ScheduleConfig, now: datetime, last: datetime | None = None, hours: float = 12
) -> datetime | None:
    return due_slot(s, "America/Chicago", now, last, hours)


def test_nothing_is_due_before_the_time() -> None:
    assert due(sched(), local(2026, 9, 29, 9, 29)) is None


def test_a_slot_is_due_from_its_start_and_returns_the_nominal_slot() -> None:
    assert due(sched(), local(2026, 9, 29, 9, 30)) == utc(2026, 9, 29, 14, 30)
    s = sched(jitter=10)
    nominal = utc(2026, 9, 29, 14, 30)
    start = slot_start(s, nominal)
    assert due(s, start - timedelta(seconds=1)) is None
    assert due(s, start) == nominal


def test_a_started_slot_is_not_due_again() -> None:
    slot = utc(2026, 9, 29, 14, 30)
    assert due(sched(), local(2026, 9, 29, 9, 45), last=slot) is None
    assert due(sched(), local(2026, 9, 29, 9, 45), last=slot - timedelta(days=1)) == slot


def test_catch_up_within_twelve_hours_but_not_beyond() -> None:
    assert due(sched(), local(2026, 9, 29, 21, 30)) == utc(2026, 9, 29, 14, 30)  # exactly 12 h late
    assert due(sched(), local(2026, 9, 29, 21, 31)) is None
    assert due(sched(), local(2026, 9, 29, 21, 31), hours=13) == utc(2026, 9, 29, 14, 30)


def test_at_most_one_missed_slot_is_caught_up() -> None:
    s = sched(["06:00", "08:00", "10:00"])
    now = local(2026, 9, 29, 11, 0)
    slot = due(s, now)
    assert slot == local(2026, 9, 29, 10, 0).astimezone(UTC)
    assert due(s, now, last=slot) is None, "the two older missed slots are retired with it"


def test_catch_up_after_several_days_of_downtime_runs_only_the_recent_slot() -> None:
    s = sched()
    slot = due(s, local(2026, 10, 3, 9, 40), last=utc(2026, 9, 29, 14, 30))
    assert slot == local(2026, 10, 3, 9, 30).astimezone(UTC)


def test_disallowed_days_are_never_due() -> None:
    s = sched(days=[0])  # Mondays only; 2026-09-29 is a Tuesday
    assert due(s, local(2026, 9, 29, 10, 0)) is None
    assert due(s, local(2026, 9, 28, 10, 0)) == local(2026, 9, 28, 9, 30).astimezone(UTC)


def test_due_slot_on_the_nonexistent_spring_forward_time() -> None:
    s = sched(["02:30"])
    assert due(s, utc(2027, 3, 14, 8, 29)) is None
    assert due(s, utc(2027, 3, 14, 8, 30)) == utc(2027, 3, 14, 8, 30)


def test_zero_catch_up_window_only_matches_the_exact_start() -> None:
    assert due(sched(), utc(2026, 9, 29, 14, 30), hours=0) == utc(2026, 9, 29, 14, 30)
    assert due(sched(), utc(2026, 9, 29, 14, 31), hours=0) is None
