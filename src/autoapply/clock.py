"""Injectable clock so cap/scheduling logic is testable. CONTRACT FILE: owned by the orchestrator."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Manually driven clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 29, 15, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware datetime")
        self._now = value


def local_day(moment: datetime, tz: str | ZoneInfo) -> date:
    """Calendar day of ``moment`` in the user's timezone (the daily cap counts by this day)."""
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return moment.astimezone(zone).date()


def local_day_bounds_utc(day: date, tz: str | ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of ``day`` in ``tz`` expressed in UTC (DST-safe)."""
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)
