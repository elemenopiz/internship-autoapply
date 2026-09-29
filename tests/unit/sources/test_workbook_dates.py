"""Date cells: real dates, Excel serials, text in many formats, day-first detection."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from autoapply.sources.workbook import infer_day_first, parse_date_value

TODAY = date(2026, 9, 29)
SEP_1 = date(2026, 9, 1)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 1),
        datetime(2026, 9, 1, 23, 59, 59),
        date(2026, 9, 1),
        46266,  # Excel serial (1900 system)
        46266.0,
        46266.75,  # serial with a time-of-day fraction
        "46266",
        "46266.0",
        20260901,  # YYYYMMDD integer
        "20260901",
        "2026-09-01",
        "2026-9-1",
        "2026/09/01",
        "2026.09.01",
        "2026-09-01T10:00:00Z",
        "2026-09-01 10:00:00",
        "2026-09-01T10:00:00.123+00:00",
        "9/1/26",
        "9/1/2026",
        "09/01/2026",
        "9-1-2026",
        "1.9.2026",  # dotted = day first
        "01.09.2026",
        "Sep 1 2026",
        "Sep 1, 2026",
        "sep 1 2026",
        "SEP 1, 2026",
        "September 1, 2026",
        "September 1st, 2026",
        "Sept. 1, 2026",
        "1 Sep 2026",
        "1st September 2026",
        "01-Sep-2026",
        "1-Sep-26",
        "Tue, Sep 1, 2026",
        "Tuesday, September 1, 2026",
        "2026 Sep 1",
        "Sep 1 2026 10:30 AM",
        "9/1/26 10:30 AM",
        "  Sep 1 2026  ",
        "(Sep 1, 2026)",
        "Verified 9/1/2026",
        "Verified: Sep 1, 2026",
        "posted Sep 1 2026",
        "last verified 2026-09-01",
        "as of 9/1/26",
    ],
)
def test_parses_the_first_of_september_in_every_format(value: object) -> None:
    assert parse_date_value(value, today=TODAY) == SEP_1


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        True,
        False,
        "TBD",
        "N/A",
        "recently",
        "Rolling",
        "ASAP",
        "Sep",  # a month with nothing else
        "Feb 30 2026",  # impossible day
        "13/13/2026",
        "2026-13-01",
        "0/0/00",
        1,  # far too small to be a serial
        2026,  # a year, not a date
        123456789,
        -5,
        float("nan"),
        float("inf"),
        time(10, 30),
        timedelta(days=3),
        "Sep 1 2026 and Oct 5 2026",
        "Sep-Oct 2026",
        [2026],
        {"d": 1},
    ],
)
def test_returns_none_for_things_that_are_not_dates(value: object) -> None:
    assert parse_date_value(value, today=TODAY) is None


def test_serial_numbers_map_to_the_right_day() -> None:
    assert parse_date_value(45658) == date(2025, 1, 1)
    assert parse_date_value(61) is None  # 1900: before the plausible range
    assert parse_date_value(32874) == date(1990, 1, 1)
    assert parse_date_value(73415) == date(2100, 12, 31)
    assert parse_date_value(73416) is None
    assert parse_date_value(32873) is None


def test_serial_numbers_respect_the_1904_epoch() -> None:
    epoch_1904 = datetime(1904, 1, 1)
    assert parse_date_value(44804, epoch=epoch_1904) == date(2026, 9, 1)
    assert parse_date_value(44804) != date(
        2026, 9, 1
    )  # same number, 1900 system, is a different day


def test_month_and_year_alone_resolve_to_the_last_day_of_the_month() -> None:
    assert parse_date_value("Sep 2026") == date(2026, 9, 30)
    assert parse_date_value("February 2028") == date(2028, 2, 29)
    assert parse_date_value("2026-09") == date(2026, 9, 30)


@pytest.mark.parametrize(
    ("text", "day_first", "expected"),
    [
        ("1/9/26", False, date(2026, 1, 9)),  # ambiguous: US month-first by default
        ("1/9/26", True, date(2026, 9, 1)),
        ("25/9/2026", False, date(2026, 9, 25)),  # cannot be month-first
        ("9/25/2026", True, date(2026, 9, 25)),  # cannot be day-first
        ("01.09.2026", False, date(2026, 9, 1)),  # dotted is European whatever the default
        ("13/1/26", False, date(2026, 1, 13)),
    ],
)
def test_ambiguous_slash_dates(text: str, day_first: bool, expected: date) -> None:
    assert parse_date_value(text, day_first=day_first) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1/1/90", date(1990, 1, 1)),
        ("1/1/89", None),  # 1989 is outside the plausible range
        ("1/1/69", date(2069, 1, 1)),
        ("1-Jan-26", date(2026, 1, 1)),
    ],
)
def test_two_digit_years(text: str, expected: date | None) -> None:
    assert parse_date_value(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", TODAY),
        ("Today", TODAY),
        ("yesterday", date(2026, 9, 28)),
        ("3 days ago", date(2026, 9, 26)),
        ("2 weeks ago", date(2026, 9, 15)),
        ("1 month ago", date(2026, 8, 30)),
        ("posted 5 days ago", date(2026, 9, 24)),
    ],
)
def test_relative_dates_need_today(text: str, expected: date) -> None:
    assert parse_date_value(text, today=TODAY) == expected
    assert parse_date_value(text) is None


def test_year_less_dates_pick_the_most_recent_or_next_upcoming_day() -> None:
    assert parse_date_value("Sep 20", today=TODAY) == date(2026, 9, 20)
    assert parse_date_value("Dec 5", today=TODAY) == date(
        2025, 12, 5
    )  # would be in the future: last year
    assert parse_date_value("Oct 15", today=TODAY, prefer_future=True) == date(2026, 10, 15)
    assert parse_date_value("Sep 15", today=TODAY, prefer_future=True) == date(
        2026, 9, 15
    )  # within a 30 day grace
    assert parse_date_value("Jan 15", today=TODAY, prefer_future=True) == date(2027, 1, 15)
    assert parse_date_value("Sep 20") is None  # no reference day, no guess
    assert parse_date_value("9/20", today=TODAY) == date(2026, 9, 20)


def test_infer_day_first() -> None:
    assert infer_day_first(["25/09/2026", "1/2/2026"]) is True
    assert infer_day_first(["9/25/2026", "1/2/2026"]) is False
    assert (
        infer_day_first(["25/09/2026", "9/25/2026"]) is False
    )  # contradictory: stay with the default
    assert infer_day_first(["1/2/2026", "3/4/2026"]) is False  # nothing proves it
    assert infer_day_first([46266, None, "Sep 1 2026", datetime(2026, 9, 1)]) is False
    assert infer_day_first([]) is False
