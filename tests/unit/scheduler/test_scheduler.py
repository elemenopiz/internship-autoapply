"""``Scheduler``: tick decisions, live config, restart safety, background thread."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from autoapply.models import RunMode
from autoapply.scheduler import LAST_SLOT_KEY, RunManager, Scheduler

SLOT = datetime(2026, 9, 29, 14, 30, tzinfo=UTC)  # 09:30 in Chicago


def make(env: Any, manager: Any, **kw: Any) -> Scheduler:
    return Scheduler(manager, env.load_config, env.repo, env.clock, **kw)


def enable(env: Any, *times: str) -> None:
    env.config.schedule.enabled = True
    env.config.schedule.run_times = list(times or ["09:30"])


def test_disabled_schedule_never_starts_anything(env: Any, manager: Any) -> None:
    assert make(env, manager).tick() is False
    assert manager.calls == [] and env.repo.get_kv(LAST_SLOT_KEY) is None


def test_a_due_slot_starts_one_scheduled_run_and_is_remembered(env: Any, manager: Any) -> None:
    enable(env)
    scheduler = make(env, manager)
    assert scheduler.tick() is True
    assert manager.calls == [(None, "schedule")], "mode None: the run reads config.mode itself"
    assert scheduler.last_slot() == SLOT
    assert scheduler.tick() is False and len(manager.calls) == 1


def test_nothing_is_due_before_the_slot(env: Any, manager: Any) -> None:
    enable(env, "23:00")
    env.clock.set(datetime(2026, 9, 29, 3, 0, tzinfo=UTC))  # 22:00 Sep 28 Chicago; 23:00 slot ahead
    assert make(env, manager).tick() is False


def test_live_toggle_takes_effect_on_the_next_tick(env: Any, manager: Any) -> None:
    scheduler = make(env, manager)
    assert scheduler.tick() is False
    enable(env)  # dashboard flips the switch; slot passed 10 minutes ago: catch-up
    assert scheduler.tick() is True
    env.config.schedule.enabled = False
    env.clock.advance(timedelta(days=1))
    assert scheduler.tick() is False
    env.config.schedule.enabled = True
    assert scheduler.tick() is True and len(manager.calls) == 2


def test_live_change_of_run_times_and_days(env: Any, manager: Any) -> None:
    enable(env, "09:50")
    scheduler = make(env, manager)
    assert scheduler.tick() is False
    env.config.schedule.run_times = ["09:00"]
    assert scheduler.tick() is True
    env.clock.advance(timedelta(days=1))  # Wednesday: not allowed any more
    env.config.schedule.days_of_week = [0]
    assert scheduler.tick() is False


def test_a_restart_does_not_repeat_the_slot(env: Any, manager: Any) -> None:
    enable(env)
    assert make(env, manager).tick() is True
    again = make(env, manager)  # new process, same database
    assert again.tick() is False and len(manager.calls) == 1
    env.clock.advance(timedelta(days=1))
    assert again.tick() is True


def test_catch_up_after_downtime_runs_one_slot_only(env: Any, manager: Any) -> None:
    enable(env, "06:00", "08:00", "09:00")
    scheduler = make(env, manager)
    assert scheduler.tick() is True
    assert scheduler.last_slot() == datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
    assert scheduler.tick() is False, "the two older missed slots were retired"


def test_a_slot_missed_for_more_than_twelve_hours_is_dropped(env: Any, manager: Any) -> None:
    enable(env)
    env.clock.set(datetime(2026, 9, 29, 22, 0, tzinfo=UTC))  # 17:00, 7.5 h after the slot
    assert make(env, manager).tick() is True
    env2_clock = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)  # 23:00 Sep 30: last slot 13.5 h ago
    env.repo.delete_kv(LAST_SLOT_KEY)
    env.clock.set(env2_clock)
    assert make(env, manager).tick() is False


def test_a_busy_manager_keeps_the_slot_pending(env: Any, manager: Any) -> None:
    enable(env)
    scheduler = make(env, manager)
    manager.accept = False
    assert scheduler.tick() is False
    assert scheduler.last_slot() is None
    manager.accept = True
    assert scheduler.tick() is True


def test_broken_config_or_timezone_never_raises(env: Any, manager: Any) -> None:
    enable(env)
    env.config.timezone = "Nowhere/Land"
    assert make(env, manager).tick() is False

    def boom() -> Any:
        raise ValueError("bad json")

    assert Scheduler(manager, boom, env.repo, env.clock).tick() is False
    assert manager.calls == []


def test_a_corrupt_stored_slot_is_ignored(env: Any, manager: Any) -> None:
    enable(env)
    env.repo.set_kv(LAST_SLOT_KEY, "not a date")
    assert make(env, manager).tick() is True


def test_next_run_at_reflects_enabled_state_and_last_slot(env: Any, manager: Any) -> None:
    scheduler = make(env, manager)
    assert scheduler.next_run_at() is None
    enable(env)
    assert scheduler.next_run_at() == SLOT + timedelta(days=1)  # today's slot already passed
    env.clock.set(datetime(2026, 9, 29, 8, 0, tzinfo=UTC))
    assert scheduler.next_run_at() == SLOT
    env.repo.set_kv(LAST_SLOT_KEY, SLOT.isoformat())
    assert scheduler.next_run_at() == SLOT + timedelta(days=1)


def test_the_scheduler_attaches_itself_to_a_run_manager(env: Any) -> None:
    real = RunManager(env.deps, repo=env.repo, clock=env.clock)
    enable(env)
    make(env, real)
    assert real.status()["next_run_at"] == (SLOT + timedelta(days=1)).isoformat()


def test_scheduled_run_end_to_end_through_the_real_manager(env: Any) -> None:
    enable(env)
    real = RunManager(env.deps, repo=env.repo, clock=env.clock)
    scheduler = make(env, real)
    assert scheduler.tick() is True
    real.join(20)
    report = real.last_report()
    assert report is not None and report.trigger == "schedule" and report.submitted == 1, report
    assert scheduler.tick() is False
    assert env.repo.list_runs(1)[0].trigger == "schedule"


def test_scheduled_run_uses_the_configured_mode(env: Any) -> None:
    env.config.mode = RunMode.DISCOVER_ONLY
    enable(env)
    real = RunManager(env.deps, repo=env.repo, clock=env.clock)
    assert make(env, real).tick()
    real.join(20)
    report = real.last_report()
    assert report is not None and report.mode is RunMode.DISCOVER_ONLY and env.submissions == 0


def test_background_thread_ticks_until_stopped(env: Any, manager: Any) -> None:
    enable(env)
    sleeps: list[float] = []
    three = threading.Event()
    holder: list[Scheduler] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        env.clock.advance(timedelta(seconds=seconds))
        if len(sleeps) == 3:
            three.set()
            holder[0].stop()

    scheduler = make(env, manager, tick_interval_s=30, sleep=fake_sleep)
    holder.append(scheduler)
    scheduler.start()
    scheduler.start()  # a second start while alive is a no-op
    assert three.wait(10)
    scheduler.stop()
    assert not scheduler.running
    assert sleeps == [30, 30, 30] and len(manager.calls) == 1
    scheduler.stop()  # idempotent


def test_stop_interrupts_the_default_wait_promptly_and_can_restart(env: Any, manager: Any) -> None:
    scheduler = make(env, manager, tick_interval_s=3600)
    scheduler.start()
    assert scheduler.running
    scheduler.stop(timeout=5)
    assert not scheduler.running
    scheduler.start()
    assert scheduler.running
    scheduler.stop(timeout=5)
    assert not scheduler.running


def test_a_crashing_tick_does_not_kill_the_thread(env: Any, manager: Any) -> None:
    calls = {"n": 0}
    done = threading.Event()

    def flaky_config() -> Any:
        calls["n"] += 1
        if calls["n"] >= 3:
            done.set()
        raise RuntimeError("boom")

    scheduler = Scheduler(manager, flaky_config, env.repo, env.clock, sleep=lambda s: None)
    scheduler.start()
    assert done.wait(10)
    scheduler.stop()
