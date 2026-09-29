"""``RunManager``: single flight, worker thread, stop, status and reports."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from autoapply.contracts import RunController
from autoapply.models import RunMode, RunReport
from autoapply.pipeline import PipelineDeps
from autoapply.scheduler import RunManager


def blocking_run_fn() -> tuple[Any, threading.Event, threading.Event, list[Any]]:
    entered, release = threading.Event(), threading.Event()
    seen: list[Any] = []

    def run_fn(deps: PipelineDeps, *, trigger: str, mode: RunMode | None) -> RunReport:
        seen.append((deps, trigger, mode))
        entered.set()
        assert release.wait(10)
        return RunReport(trigger="manual", submitted=1)

    return run_fn, entered, release, seen


def test_satisfies_the_run_controller_protocol(env: Any) -> None:
    manager: RunController = RunManager(env.deps)
    assert manager.is_running() is False and manager.last_report() is None


def test_single_flight_and_status_while_running(env: Any) -> None:
    run_fn, entered, release, seen = blocking_run_fn()
    manager = RunManager(env.deps, clock=env.clock, run_fn=run_fn)
    assert manager.run_now(RunMode.DRY_RUN, trigger="schedule") is True
    assert entered.wait(10)
    assert manager.run_now() is False, "a run is already in progress"
    assert manager.is_running()
    status = manager.status()
    assert (
        status["running"] is True
        and status["mode"] == "dry_run"
        and status["trigger"] == "schedule"
    )
    assert status["started_at"] == env.clock.now().isoformat() and status["last_report"] is None
    release.set()
    assert manager.join(10) is True
    assert not manager.is_running()
    assert seen[0][1:] == ("schedule", RunMode.DRY_RUN)
    assert manager.run_now(), "idle again"
    manager.join(10)


def test_last_report_and_json_friendly_status(env: Any) -> None:
    run_fn, _, release, _ = blocking_run_fn()
    release.set()
    manager = RunManager(env.deps, run_fn=run_fn)
    manager.run_now()
    manager.join(10)
    report = manager.last_report()
    assert report is not None and report.submitted == 1
    import json

    status = manager.status()
    json.dumps(status)
    assert status["running"] is False and status["last_report"]["submitted"] == 1  # type: ignore[index]


def test_each_run_gets_fresh_deps_with_the_managers_stop_flag(env: Any) -> None:
    flags: list[Any] = []
    built: list[PipelineDeps] = []

    def factory() -> PipelineDeps:
        deps = env.deps()
        built.append(deps)
        return deps

    def run_fn(deps: PipelineDeps, *, trigger: str, mode: Any) -> RunReport:
        flags.append(deps.stop_flag)
        return RunReport()

    manager = RunManager(factory, run_fn=run_fn)
    for _ in range(2):
        assert manager.run_now()
        manager.join(10)
    assert len(built) == 2 and built[0] is not built[1]
    assert flags[0] is not flags[1], "stop requests never leak into the next run"


def test_request_stop_reaches_the_running_run_and_is_a_noop_when_idle(env: Any) -> None:
    stopped = threading.Event()

    def run_fn(deps: PipelineDeps, *, trigger: str, mode: Any) -> RunReport:
        assert deps.stop_flag.wait(10)
        stopped.set()
        return RunReport(stopped_reason="kill_switch")

    manager = RunManager(env.deps, run_fn=run_fn)
    manager.request_stop()  # idle: nothing happens
    assert manager.run_now()
    assert manager.status()["stop_requested"] is False
    manager.request_stop()
    assert stopped.wait(10)
    manager.join(10)
    assert manager.last_report().stopped_reason == "kill_switch"  # type: ignore[union-attr]
    assert manager.status()["stop_requested"] is False, "a finished run leaves no stale request"


def test_a_failing_deps_factory_or_run_becomes_an_error_report(env: Any) -> None:
    def broken() -> PipelineDeps:
        raise OSError("cannot open the database")

    manager = RunManager(broken, clock=env.clock)
    assert manager.run_now(RunMode.DRY_RUN)
    manager.join(10)
    report = manager.last_report()
    assert (
        report is not None and report.stopped_reason == "error" and report.mode is RunMode.DRY_RUN
    )
    assert "cannot open the database" in report.errors[0]
    assert not manager.is_running() and manager.run_now(), "the manager recovers"
    manager.join(10)


def test_the_real_pipeline_runs_in_the_worker_thread(env: Any) -> None:
    manager = RunManager(env.deps, repo=env.repo, clock=env.clock)
    assert manager.run_now(trigger="manual")
    manager.join(20)
    report = manager.last_report()
    assert report is not None and report.submitted == 1, report
    assert env.submissions == 1 and env.repo.get_run_lock() is None


def test_a_lock_held_by_another_process_blocks_run_now(env: Any) -> None:
    manager = RunManager(env.deps, repo=env.repo)
    assert env.repo.acquire_run_lock("other-process", 600)
    assert manager.is_running() and manager.run_now() is False
    status = manager.status()
    assert status["running"] is True and status["external"] is True
    env.repo.release_run_lock("other-process")
    assert not manager.is_running() and manager.run_now()
    manager.join(20)


def test_an_expired_foreign_lock_does_not_block(env: Any) -> None:
    from datetime import timedelta

    manager = RunManager(env.deps, repo=env.repo)
    env.repo.acquire_run_lock("crashed", 30)
    env.clock.advance(timedelta(minutes=1))
    assert not manager.is_running()


def test_two_managers_share_the_database_lock(env: Any) -> None:
    env.gate = threading.Event()
    a = RunManager(env.deps, repo=env.repo, clock=env.clock)
    b = RunManager(env.deps, repo=env.repo, clock=env.clock)
    assert a.run_now()
    assert env.entered.wait(10)
    assert b.run_now() is False
    env.gate.set()
    a.join(20)
    assert b.run_now()
    b.join(20)


def test_next_run_is_reported_once_a_schedule_is_attached(env: Any) -> None:
    manager = RunManager(env.deps)
    assert manager.status()["next_run_at"] is None
    manager.attach_schedule(lambda: datetime(2026, 9, 30, 14, 30, tzinfo=UTC))
    assert manager.status()["next_run_at"] == "2026-09-30T14:30:00+00:00"
    manager.attach_schedule(lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert manager.status()["next_run_at"] is None
