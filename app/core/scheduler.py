"""Bot scheduler — auto-starts and stops the bot based on a time schedule.

Implements: FR-060 (bot scheduling), FR-061 (time-based auto-start/stop).

The scheduler runs in a background thread and checks once per minute whether
the bot should be running based on the configured days and time window.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from config.settings import ScheduleConfig

logger = logging.getLogger(__name__)

DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def is_within_schedule(schedule: "ScheduleConfig", now: datetime | None = None) -> bool:
    """Check if the current time falls within the schedule window.

    Args:
        schedule: Schedule configuration with days, start_time, end_time.
        now: Override for testing. Defaults to datetime.now().

    Returns:
        True if the current time is within the scheduled window.
    """
    if not schedule.enabled:
        return False

    if now is None:
        now = datetime.now()

    # Check day of week
    current_day = now.weekday()  # 0=Monday, 6=Sunday
    allowed_days = {DAY_MAP[d.lower()] for d in schedule.days_of_week if d.lower() in DAY_MAP}
    if current_day not in allowed_days:
        return False

    # Check time window
    try:
        start_h, start_m = map(int, schedule.start_time.split(":"))
        end_h, end_m = map(int, schedule.end_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning("Invalid schedule time format: %s - %s", schedule.start_time, schedule.end_time)
        return False

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    current_minutes = now.hour * 60 + now.minute

    # Handle same-day window (e.g., 09:00 - 17:00)
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes < end_minutes

    # Handle overnight window (e.g., 22:00 - 06:00)
    return current_minutes >= start_minutes or current_minutes < end_minutes


class BotScheduler:
    """Background scheduler that auto-starts/stops the bot on a time schedule.

    Args:
        get_schedule: Callable that returns the current ScheduleConfig.
        start_bot: Callable to start the bot.
        stop_bot: Callable to stop the bot.
        is_bot_running: Callable that returns True if the bot is currently running.
        check_interval: Seconds between schedule checks (default: 60).
    """

    def __init__(
        self,
        get_schedule: Callable[[], "ScheduleConfig | None"],
        start_bot: Callable[[], object],
        stop_bot: Callable[[], None],
        is_bot_running: Callable[[], bool],
        check_interval: int = 60,
    ) -> None:
        self._get_schedule = get_schedule
        self._start_bot = start_bot
        self._stop_bot = stop_bot
        self._is_bot_running = is_bot_running
        self._check_interval = check_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._auto_started = False  # Track if WE started the bot

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._auto_started = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="scheduler",
        )
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler background thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        """Whether the scheduler thread is active."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def auto_started(self) -> bool:
        """Whether the scheduler auto-started the bot (vs. manual start)."""
        return self._auto_started

    def _run(self) -> None:
        """Main scheduler loop — checks schedule every interval."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error("Scheduler tick error: %s", e)
            self._stop_event.wait(timeout=self._check_interval)

    def _tick(self) -> None:
        """Single schedule check — start or stop the bot as needed."""
        schedule = self._get_schedule()
        if schedule is None or not schedule.enabled:
            # Schedule disabled — if we auto-started the bot, stop it
            if self._auto_started and self._is_bot_running():
                logger.info("Schedule disabled — stopping bot")
                self._stop_bot()
                self._auto_started = False
            return

        within = is_within_schedule(schedule)

        if within and not self._is_bot_running():
            logger.info("Schedule window active — starting bot")
            self._start_bot()
            self._auto_started = True
        elif not within and self._auto_started and self._is_bot_running():
            logger.info("Schedule window ended — stopping bot")
            self._stop_bot()
            self._auto_started = False
