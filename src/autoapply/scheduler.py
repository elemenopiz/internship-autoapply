"""Scheduling: next-run maths, a single-flight run manager and a background ticker (docs/SPEC.md section 5.11).

Vocabulary. A *slot* is one nominal occurrence of a configured local ``HH:MM`` on an allowed weekday, held as an
aware UTC instant. A slot STARTS at ``slot + jitter`` where the jitter is a whole number of seconds in
``[0, jitter_minutes * 60]`` derived from a SHA-256 of the slot, so it is identical in every evaluation, process and
restart (and never earlier than the configured time). The persisted ``last_slot`` is the nominal slot.

DST: slots are built from local wall-clock times. A nonexistent time (02:30 on the spring-forward day) runs once, at
the instant the clock would have shown it (03:30 local); an ambiguous time (01:30 on the fall-back day) runs once, at
its first occurrence. Slots that collapse onto the same instant are merged.

Everything here uses threads only (no signals, no fork), so it works the same on Windows.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from autoapply.clock import Clock, SystemClock
from autoapply.config import AppConfig, ScheduleConfig
from autoapply.contracts import RunController
from autoapply.db import Repo
from autoapply.models import RunMode, RunReport
from autoapply.pipeline import PipelineDeps, run_once

__all__ = [
    "DEFAULT_CATCHUP_HOURS",
    "LAST_SLOT_KEY",
    "RunManager",
    "Scheduler",
    "due_slot",
    "next_run_at",
    "slot_start",
]

log = logging.getLogger("autoapply.scheduler")

DEFAULT_CATCHUP_HOURS = 12.0
LAST_SLOT_KEY = "scheduler.last_slot"
_MAX_JITTER_MINUTES = 720
_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


# ------------------------------------------------------------------------------------------ schedule maths


def _zone(tz: str | ZoneInfo) -> ZoneInfo:
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _run_times(values: Iterable[object]) -> list[time]:
    """Valid ``H:MM`` / ``HH:MM`` entries (24 h clock); anything else is ignored."""
    result: set[time] = set()
    for raw in values:
        match = _HHMM.fullmatch(str(raw).strip())
        if match and int(match[1]) < 24 and int(match[2]) < 60:
            result.add(time(int(match[1]), int(match[2])))
    return sorted(result)


def _weekdays(values: Iterable[object]) -> set[int]:
    return {v for v in values if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6}


def _slots_between(
    schedule: ScheduleConfig, zone: ZoneInfo, first_day: date, last_day: date
) -> list[datetime]:
    """Nominal slots (UTC, sorted, de-duplicated) whose LOCAL date lies in ``[first_day, last_day]``."""
    times, days = _run_times(schedule.run_times), _weekdays(schedule.days_of_week)
    slots: set[datetime] = set()
    day = first_day
    while day <= last_day:
        if day.weekday() in days:
            for at in times:
                # fold=0: an ambiguous time takes its first occurrence, a nonexistent one is shifted forward.
                slots.add(datetime.combine(day, at, tzinfo=zone).astimezone(UTC))
        day += timedelta(days=1)
    return sorted(slots)


def _jitter(slot: datetime, jitter_minutes: int) -> timedelta:
    span = max(0, min(int(jitter_minutes), _MAX_JITTER_MINUTES)) * 60
    if span == 0:
        return timedelta(0)
    key = f"autoapply-schedule-jitter:{slot.astimezone(UTC).isoformat()}".encode()
    return timedelta(seconds=int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % (span + 1))


def slot_start(schedule: ScheduleConfig, slot: datetime) -> datetime:
    """When ``slot`` actually starts: the nominal time plus its deterministic jitter (UTC)."""
    slot = _aware(slot).astimezone(UTC)
    return slot + _jitter(slot, schedule.jitter_minutes)


def next_run_at(
    schedule: ScheduleConfig,
    tz: str | ZoneInfo,
    now: datetime,
    *,
    last_slot: datetime | None = None,
) -> datetime | None:
    """Start instant (aware UTC) of the earliest slot that starts strictly after ``now``.

    Slots at or before ``last_slot`` are skipped (they already ran). Pure schedule maths: ``schedule.enabled`` is
    NOT consulted (the ``Scheduler`` does). ``None`` when no valid run time or weekday is configured.
    """
    zone, now_utc = _zone(tz), _aware(now).astimezone(UTC)
    today = now_utc.astimezone(zone).date()
    last = _aware(last_slot).astimezone(UTC) if last_slot is not None else None
    best: datetime | None = None
    for slot in _slots_between(
        schedule, zone, today - timedelta(days=2), today + timedelta(days=9)
    ):
        if last is not None and slot <= last:
            continue
        start = slot_start(schedule, slot)
        if start > now_utc and (best is None or start < best):
            best = start
    return best


def due_slot(
    schedule: ScheduleConfig,
    tz: str | ZoneInfo,
    now: datetime,
    last_slot: datetime | None,
    catchup_hours: float = DEFAULT_CATCHUP_HOURS,
) -> datetime | None:
    """The nominal slot to run now, or ``None``.

    A slot is due when it started (``slot + jitter <= now``), is newer than ``last_slot`` and started at most
    ``catchup_hours`` ago. When several are due only the most recent is returned: at most ONE missed slot is caught
    up, and persisting it as ``last_slot`` retires the older ones.
    """
    zone, now_utc = _zone(tz), _aware(now).astimezone(UTC)
    window = timedelta(hours=max(0.0, catchup_hours))
    today = now_utc.astimezone(zone).date()
    days_back = math.ceil(window / timedelta(days=1)) + 2
    last = _aware(last_slot).astimezone(UTC) if last_slot is not None else None
    due = [
        slot
        for slot in _slots_between(
            schedule, zone, today - timedelta(days=days_back), today + timedelta(days=1)
        )
        if (last is None or slot > last)
        and slot_start(schedule, slot) <= now_utc
        and now_utc - slot_start(schedule, slot) <= window
    ]
    return max(due) if due else None


# ------------------------------------------------------------------------------------------ run manager


class RunManager:
    """``contracts.RunController``: at most one pipeline run at a time, executed in a worker thread.

    Single flight is enforced twice: an in-process lock (``run_now`` returns False while a worker is alive) and, when
    ``repo`` is given, the cross-process DB run lock (``run_now`` also returns False while another process holds an
    unexpired lease; ``run_once`` re-checks it atomically anyway). Each run gets a FRESH ``PipelineDeps`` from
    ``deps_factory`` whose ``stop_flag`` is replaced by the manager's, so ``request_stop`` reaches it.
    ``run_fn`` (default ``pipeline.run_once``) exists for tests.
    """

    def __init__(
        self,
        deps_factory: Callable[[], PipelineDeps],
        *,
        repo: Repo | None = None,
        clock: Clock | None = None,
        run_fn: Callable[..., RunReport] | None = None,
    ) -> None:
        self._deps_factory = deps_factory
        self._repo = repo
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._run_fn: Callable[..., RunReport] = run_fn if run_fn is not None else run_once
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._current: dict[str, Any] = {}
        self._last_report: RunReport | None = None
        self._schedule_provider: Callable[[], datetime | None] | None = None

    def attach_schedule(self, provider: Callable[[], datetime | None]) -> None:
        """Let ``status()`` report the next scheduled run (called by ``Scheduler``)."""
        self._schedule_provider = provider

    # -- RunController ------------------------------------------------------------------------------
    def run_now(self, mode: RunMode | None = None, *, trigger: str = "manual") -> bool:
        """Start a run in the background; False if one is already in progress (here or in another process)."""
        with self._lock:
            if self._thread is not None or self._external_run_active():
                return False
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._worker,
                args=(mode, trigger, stop_event),
                name="autoapply-run",
                daemon=True,
            )
            self._thread, self._stop_event = thread, stop_event
            self._current = {"mode": mode, "trigger": trigger, "started_at": self._clock.now()}
            try:
                thread.start()
            except RuntimeError:  # cannot start a thread
                log.exception("could not start the run thread")
                self._thread = self._stop_event = None
                self._current = {}
                return False
        return True

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None or self._external_run_active()

    def request_stop(self) -> None:
        """Ask the current run to stop before its next step (no effect while idle)."""
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()

    def status(self) -> dict[str, object]:
        """JSON-friendly snapshot: ``running``, ``external``, ``mode``, ``trigger``, ``started_at``,
        ``stop_requested``, ``next_run_at`` (ISO or None) and ``last_report`` (``RunReport`` dict or None)."""
        with self._lock:
            in_process = self._thread is not None
            external = not in_process and self._external_run_active()
            current = dict(self._current)
            stop_requested = self._stop_event.is_set() if self._stop_event is not None else False
            report = self._last_report
        mode = current.get("mode")
        started = current.get("started_at")
        return {
            "running": in_process or external,
            "external": external,
            "mode": RunMode(mode).value if mode is not None else None,
            "trigger": current.get("trigger"),
            "started_at": started.isoformat() if started is not None else None,
            "stop_requested": stop_requested,
            "next_run_at": self._next_run_iso(),
            "last_report": report.model_dump(mode="json") if report is not None else None,
        }

    def last_report(self) -> RunReport | None:
        with self._lock:
            return self._last_report

    # -- extras -------------------------------------------------------------------------------------
    def join(self, timeout: float | None = None) -> bool:
        """Wait for the current run (if any) to finish; True when the manager is idle afterwards."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self._thread is None

    # -- internals ----------------------------------------------------------------------------------
    def _external_run_active(self) -> bool:
        if self._repo is None:
            return False
        try:
            info = self._repo.get_run_lock()
        except Exception:
            log.warning("could not read the run lock", exc_info=True)
            return False
        return info is not None and not info.expired

    def _next_run_iso(self) -> str | None:
        provider = self._schedule_provider
        if provider is None:
            return None
        try:
            upcoming = provider()
        except Exception:
            log.warning("could not compute the next run", exc_info=True)
            return None
        return upcoming.isoformat() if upcoming is not None else None

    def _worker(self, mode: RunMode | None, trigger: str, stop_event: threading.Event) -> None:
        report: RunReport | None = None
        try:
            deps = self._deps_factory()
            deps.stop_flag = stop_event
            report = self._run_fn(deps, trigger=trigger, mode=mode)
        except Exception as exc:
            log.exception("run failed before or outside the pipeline")
            report = RunReport(
                mode=mode or RunMode.FULL_AUTO,
                started_at=self._current.get("started_at"),
                finished_at=self._clock.now(),
                stopped_reason="error",
                errors=[f"{type(exc).__name__}: {exc}"[:400]],
            )
        finally:
            with self._lock:
                if report is not None:
                    self._last_report = report
                self._thread = None
                self._stop_event = None


# ------------------------------------------------------------------------------------------ scheduler


class Scheduler:
    """Turns the configured schedule into ``manager.run_now(trigger="schedule")`` calls.

    ``tick()`` re-reads the config every time (dashboard toggles apply live), asks ``due_slot`` what should run,
    starts it and persists the slot in the repo's kv store (``scheduler.last_slot``) so a restart never repeats it.
    A due slot that cannot start because a run is in progress stays pending (until the catch-up window closes).
    Enabling the schedule shortly after a slot's time therefore starts that slot's run at once (catch-up).
    ``start()``/``stop()`` run ``tick`` every ``tick_interval_s`` in a daemon thread; ``sleep`` (default: wait on the
    stop event) is injectable for tests.
    """

    def __init__(
        self,
        manager: RunController,
        load_config: Callable[[], AppConfig],
        repo: Repo,
        clock: Clock,
        tick_interval_s: float = 30.0,
        sleep: Callable[[float], None] | None = None,
        *,
        catchup_hours: float = DEFAULT_CATCHUP_HOURS,
    ) -> None:
        self._manager = manager
        self._load_config = load_config
        self._repo = repo
        self._clock = clock
        self.tick_interval_s = tick_interval_s
        self._catchup_hours = catchup_hours
        self._stop = threading.Event()
        self._sleep = sleep
        self._tick_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        attach = getattr(manager, "attach_schedule", None)
        if callable(attach):
            attach(self.next_run_at)

    # -- persisted state ----------------------------------------------------------------------------
    def last_slot(self) -> datetime | None:
        raw = self._repo.get_kv(LAST_SLOT_KEY)
        if not raw:
            return None
        try:
            return _aware(datetime.fromisoformat(raw))
        except ValueError:
            return None

    def _remember(self, slot: datetime) -> None:
        self._repo.set_kv(LAST_SLOT_KEY, _aware(slot).astimezone(UTC).isoformat())

    # -- decisions ----------------------------------------------------------------------------------
    def next_run_at(self) -> datetime | None:
        """Start of the next scheduled run, or None when the schedule is off/empty."""
        config = self._load_config()
        if not config.schedule.enabled:
            return None
        return next_run_at(
            config.schedule, config.timezone, self._clock.now(), last_slot=self.last_slot()
        )

    def tick(self) -> bool:
        """Start the due run, if any. True when a run was started by this tick. Never raises."""
        with self._tick_lock:
            try:
                config = self._load_config()
                if not config.schedule.enabled:
                    return False
                slot = due_slot(
                    config.schedule,
                    config.timezone,
                    self._clock.now(),
                    self.last_slot(),
                    self._catchup_hours,
                )
                if slot is None or not self._manager.run_now(None, trigger="schedule"):
                    return False
            except Exception:
                log.warning("scheduler tick failed", exc_info=True)
                return False
            try:
                self._remember(slot)
            except Exception:
                log.warning("could not persist the started slot", exc_info=True)
            log.info("scheduled run started for slot %s", slot.isoformat())
            return True

    # -- background thread --------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the ticking thread (no-op if it is already alive)."""
        with self._thread_lock:
            if self.running:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="autoapply-scheduler", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop ticking (an in-flight run keeps going; use the manager to stop it). Safe from any thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            if self._stop.is_set():
                break
            if self._sleep is not None:
                self._sleep(self.tick_interval_s)
            else:
                self._stop.wait(max(0.0, self.tick_interval_s))
