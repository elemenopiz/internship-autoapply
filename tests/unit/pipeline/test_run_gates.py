"""``run_once`` safety gates: readiness, lock, kill switch, budget, daily cap (local calendar day)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autoapply.models import ApplicationStatus, ApplyResult, Reason, RunMode

S, R = ApplicationStatus, Reason
CHICAGO_MIDNIGHT_UTC = datetime(2026, 9, 30, 5, 0, tzinfo=UTC)  # 00:00 CDT on Sep 30


# ------------------------------------------------------------------------------------------ readiness


@pytest.mark.parametrize("breakage", ["profile", "resume", "key", "attestation", "source"])
def test_not_ready_means_zero_work_and_lists_the_issues(world: Any, breakage: str) -> None:
    world.add_ops(2)
    if breakage == "profile":
        world.config.profile.phone = ""
    elif breakage == "resume":
        world.paths.resume_file.unlink()
    elif breakage == "key":
        world.env.clear()
    elif breakage == "attestation":
        world.config.apply.attestations_authorized = False
    else:
        world.config.workbook.path = None
    report = world.run()
    assert report.stopped_reason == "not_ready" and report.attempted == 0
    assert report.errors and all(e.startswith("[") for e in report.errors)
    assert world.ingest_calls == 0 and world.runner_builds == 0
    assert world.repo.count_applications() == 0 and world.repo.get_run_lock() is None
    assert world.repo.get_run(report.run_id) is not None


def test_dry_run_does_not_need_the_attestation_flag(world: Any) -> None:
    world.config.apply.attestations_authorized = False
    world.add_ops(1)
    world.runner.default = ApplyResult(status=S.DRY_RUN_OK)
    assert world.run(mode=RunMode.DRY_RUN).dry_run_ok == 1


def test_an_unloadable_config_is_reported_not_raised(world: Any) -> None:
    def broken() -> Any:
        raise ValueError("config.json is not valid JSON")

    report = world.run(load_config=broken)
    assert report.stopped_reason == "error" and "not valid JSON" in report.errors[0]
    assert world.repo.list_runs(1)[0].stopped_reason == "error"


def test_an_invalid_timezone_is_reported(world: Any) -> None:
    world.config.timezone = "Mars/Olympus"
    report = world.run()
    assert report.stopped_reason == "error" and "time zone" in report.errors[0]
    assert world.ingest_calls == 0


# ------------------------------------------------------------------------------------------ run lock


def test_a_held_lock_yields_already_running_and_is_not_disturbed(world: Any) -> None:
    world.add_ops(1)
    assert world.repo.acquire_run_lock("someone-else", 600)
    report = world.run()
    assert report.stopped_reason == "already_running" and report.attempted == 0
    assert world.ingest_calls == 0
    lock = world.repo.get_run_lock()
    assert lock is not None and lock.owner == "someone-else"
    assert world.repo.get_run(report.run_id) is not None


def test_an_expired_lock_is_taken_over(world: Any) -> None:
    world.add_ops(1)
    world.repo.acquire_run_lock("crashed", 60)
    world.clock.advance(timedelta(minutes=5))
    assert world.run().submitted == 1


def test_two_concurrent_runs_one_wins_the_lock(world: Any) -> None:
    world.add_ops(2)
    entered, release = threading.Event(), threading.Event()

    def slow_ingest() -> None:
        entered.set()
        assert release.wait(10)

    world.ingest_hook = slow_ingest
    results: list[Any] = []
    first = threading.Thread(target=lambda: results.append(world.run()))
    first.start()
    assert entered.wait(10)
    second = world.run()
    release.set()
    first.join(10)
    assert second.stopped_reason == "already_running" and second.attempted == 0
    assert results[0].submitted == 2 and results[0].stopped_reason is None
    assert world.repo.get_run_lock() is None
    assert world.repo.count_applications(S.SUBMITTED) == 2
    assert world.run().stopped_reason == "no_candidates", "a finished run leaves the lock free"


def test_the_lock_is_heartbeated_before_every_attempt(world: Any) -> None:
    world.add_ops(3)
    beats: list[str] = []
    real = world.repo.heartbeat_run_lock
    world.repo.heartbeat_run_lock = lambda owner, ttl_s=None: (
        beats.append(owner) or real(owner, ttl_s)
    )  # type: ignore[method-assign]
    world.run()
    assert len(beats) >= 3 + 2 and len(set(beats)) == 1


def test_losing_the_lock_stops_the_run(world: Any) -> None:
    world.add_ops(3)

    def steal(op: Any) -> None:
        world.repo.release_run_lock(world.repo.get_run_lock().owner)  # type: ignore[union-attr]
        world.repo.acquire_run_lock("thief", 600)

    world.runner.before_call = steal
    report = world.run()
    assert report.stopped_reason == "error" and report.attempted == 1
    assert "lock was lost" in report.errors[-1]
    lock = world.repo.get_run_lock()
    assert lock is not None and lock.owner == "thief", "we never release somebody else's lock"


def test_lock_owner_is_unique_per_run(world: Any) -> None:
    owners: list[str] = []
    world.add_ops(1)
    world.runner.before_call = lambda op: owners.append(world.repo.get_run_lock().owner)  # type: ignore[union-attr]
    world.run()
    world.add_ops(1)
    world.run()
    assert len(owners) == 2 and owners[0] != owners[1]


# ------------------------------------------------------------------------------------------ kill switch


def test_stop_file_at_start_blocks_everything(world: Any) -> None:
    world.add_ops(2)
    world.paths.stop_file.write_text("stop")
    for mode in RunMode:
        report = world.run(mode=mode)
        assert report.stopped_reason == "kill_switch" and report.attempted == 0
    assert world.ingest_calls == 0 and world.runner_builds == 0


def test_stop_file_appearing_mid_run_stops_before_the_next_attempt(world: Any) -> None:
    world.add_ops(5)
    world.runner.before_call = lambda op: (
        world.paths.stop_file.write_text("x") if len(world.runner.calls) == 2 else None
    )
    report = world.run()
    assert report.stopped_reason == "kill_switch"
    assert (report.attempted, report.submitted) == (2, 2)
    assert world.runner.close_calls == 1 and world.repo.get_run_lock() is None


def test_stop_flag_mid_run(world: Any) -> None:
    world.add_ops(5)
    world.runner.before_call = lambda op: (
        world.stop_flag.set() if len(world.runner.calls) == 1 else None
    )
    report = world.run()
    assert report.stopped_reason == "kill_switch" and report.attempted == 1


def test_stop_appearing_during_the_pacing_pause_prevents_the_next_attempt(world: Any) -> None:
    world.add_ops(3)
    world.run(sleep=lambda seconds: world.stop_flag.set())
    assert len(world.runner.calls) == 1
    assert world.repo.count_applications() == 1


def test_stop_during_tailoring_prevents_the_attempt(world: Any) -> None:
    world.add_ops(2)
    original = world.tailor

    def tailor_then_stop(*args: Any) -> Any:
        docs = original(*args)
        world.stop_flag.set()
        return docs

    report = world.run(tailor=tailor_then_stop)
    assert report.stopped_reason == "kill_switch" and report.attempted == 0
    assert world.repo.count_applications() == 0 and world.runner_builds == 0


# ------------------------------------------------------------------------------------------ budget


def test_attempt_budget_from_the_limit_argument(world: Any) -> None:
    world.add_ops(5)
    report = world.run(limit=2)
    assert report.stopped_reason == "attempt_budget" and report.attempted == 2


def test_attempt_budget_from_the_config_and_min_with_limit(world: Any) -> None:
    world.add_ops(10)
    world.config.daily_cap = 100
    world.config.apply.max_attempts_per_run = 3
    assert world.run().attempted == 3
    assert world.run(limit=100).attempted == 3, "a limit can lower the budget, never raise it"
    report = world.run(limit=0)
    assert report.attempted == 0 and report.stopped_reason == "attempt_budget"


def test_finishing_all_candidates_inside_the_budget_is_not_a_stop(world: Any) -> None:
    world.add_ops(2)
    assert world.run(limit=2).stopped_reason is None


# ------------------------------------------------------------------------------------------ daily cap


def test_cap_stops_a_full_auto_run(world: Any) -> None:
    world.config.daily_cap = 2
    world.add_ops(5)
    report = world.run()
    assert report.stopped_reason == "cap_reached" and report.submitted == 2
    assert report.attempted == 2 and report.cap_remaining == 0
    assert len(world.sleeps) == 1, "no pause after the attempt that exhausted the cap"


def test_cap_already_reached_at_start(world: Any) -> None:
    world.submit_history(5)
    world.add_ops(3)
    report = world.run()
    assert report.stopped_reason == "cap_reached" and report.attempted == 0
    assert report.cap_remaining == 0 and world.runner_builds == 0


def test_failed_and_needs_manual_attempts_do_not_use_the_cap(world: Any) -> None:
    world.config.daily_cap = 2
    ops = world.add_ops(6)
    world.scores = {op.id: 90.0 - i for i, op in enumerate(ops)}  # attempt in list order
    world.runner.script = {
        ops[0].id: ApplyResult(status=S.NEEDS_MANUAL, reason=R.UNSUPPORTED_PORTAL),
        ops[1].id: ApplyResult(status=S.FAILED, reason=R.TIMEOUT),
        ops[2].id: ApplyResult(status=S.SKIPPED, reason=R.POSTING_CLOSED),
    }
    report = world.run()
    assert (report.attempted, report.submitted, report.stopped_reason) == (5, 2, "cap_reached")
    assert (report.needs_manual, report.failed, report.skipped) == (1, 1, 1)


def test_dry_runs_neither_count_toward_nor_are_blocked_by_the_cap(world: Any) -> None:
    world.submit_history(5)  # the real cap is used up
    world.add_ops(3)
    world.runner.default = ApplyResult(status=S.DRY_RUN_OK)
    report = world.run(mode=RunMode.DRY_RUN)
    assert report.dry_run_ok == 3 and report.stopped_reason is None
    assert report.cap_remaining == 0, "dry runs did not change the real cap"
    assert world.repo.count_submitted_on(world.clock.now().date(), "America/Chicago") == 5


def test_cap_counts_the_local_calendar_day_not_the_utc_day(world: Any) -> None:
    world.config.daily_cap = 2
    world.submit_history(2, datetime(2026, 9, 30, 3, 0, tzinfo=UTC))  # 22:00 CDT on Sep 29
    world.add_ops(3)
    world.clock.set(datetime(2026, 9, 30, 4, 30, tzinfo=UTC))  # 23:30 CDT, still Sep 29 locally
    blocked = world.run()
    assert blocked.stopped_reason == "cap_reached" and blocked.attempted == 0
    world.clock.set(CHICAGO_MIDNIGHT_UTC + timedelta(minutes=1))  # 00:01 CDT Sep 30, same UTC day
    fresh = world.run()
    assert fresh.submitted == 2 and fresh.stopped_reason == "cap_reached"


def test_the_cap_resets_when_a_run_crosses_local_midnight(world: Any) -> None:
    world.config.daily_cap = 2
    world.add_ops(4)
    world.clock.set(CHICAGO_MIDNIGHT_UTC - timedelta(minutes=1))
    world.sleep = lambda seconds: world.clock.advance(timedelta(minutes=2))  # type: ignore[method-assign]
    report = world.run(sleep=world.sleep)
    # One before midnight (Sep 29), two after (Sep 30): the new local day has a fresh cap.
    assert report.submitted == 3 and report.stopped_reason == "cap_reached"


def test_cap_is_read_from_the_database_after_a_restart(world: Any) -> None:
    from autoapply.db import Database, Repo

    world.config.daily_cap = 3
    world.add_ops(6)
    assert world.run().submitted == 3
    world.repo.db.close()
    reopened = Repo(Database(world.paths.db_file), world.clock)
    report = world.run(repo=reopened)
    assert report.stopped_reason == "no_candidates" or report.attempted == 0
    assert report.cap_remaining == 0
