"""The data layer across REAL processes (``multiprocessing`` with the ``spawn`` start method).

These are the guarantees the app relies on when the dashboard, a scheduled CLI run and a crashed earlier
run all touch the same file: the run lock is exclusive, the cap survives a restart, a crash loses nothing
that was committed, and concurrent start-ups do not trip over each other.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autoapply.clock import FakeClock, local_day
from autoapply.db import Database, Repo, latest_schema_version
from autoapply.models import ApplicationStatus, Opportunity, Reason

CTX = multiprocessing.get_context("spawn")
WAIT_S = 90  # spawning + importing pydantic takes a second or two; be generous, never flaky


@pytest.fixture
def workers(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The worker module, importable by name in the children (they inherit the parent's sys.path)."""
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    import _db_process_workers

    return _db_process_workers


@pytest.fixture
def procs() -> Iterator[list[Any]]:
    """Every process started through ``spawn`` is killed on teardown, whatever the test did."""
    started: list[Any] = []
    yield started
    for proc in started:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=10)


def spawn(procs: list[Any], target: Callable[..., None], *args: Any) -> Any:
    proc = CTX.Process(target=target, args=args, daemon=True)
    proc.start()
    procs.append(proc)
    return proc


def drain(queue: Any, count: int) -> list[Any]:
    return [queue.get(timeout=WAIT_S) for _ in range(count)]


# ------------------------------------------------------------------------------------ run lock


def test_a_lock_held_by_another_process_excludes_us_until_it_releases(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    repo = Repo(Database(db_path))
    ready, release, out = CTX.Event(), CTX.Event(), CTX.Queue()
    child = spawn(procs, workers.hold_lock, str(db_path), "child-proc", 300.0, ready, release, out)
    assert ready.wait(WAIT_S), "child never reported"
    assert out.get(timeout=WAIT_S) == ("acquired", True)

    assert repo.acquire_run_lock("parent-proc", 60) is False
    assert repo.heartbeat_run_lock("parent-proc") is False
    assert repo.release_run_lock("parent-proc") is False  # only the owner may release
    info = repo.get_run_lock()
    assert info is not None and info.owner == "child-proc" and info.expired is False

    release.set()
    assert out.get(timeout=WAIT_S) == ("released", True)
    child.join(timeout=WAIT_S)
    assert child.exitcode == 0
    assert repo.acquire_run_lock("parent-proc", 60) is True
    repo.db.close()


def test_the_lock_of_a_killed_process_holds_until_its_ttl_then_is_taken_over(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    ready, release, out = CTX.Event(), CTX.Event(), CTX.Queue()
    child = spawn(procs, workers.hold_lock, str(db_path), "doomed", 30.0, ready, release, out)
    assert ready.wait(WAIT_S)
    assert out.get(timeout=WAIT_S) == ("acquired", True)
    child.kill()  # no release, no goodbye
    child.join(timeout=WAIT_S)
    assert child.exitcode != 0

    real = Repo(Database(db_path))
    assert real.acquire_run_lock("heir", 30) is False  # the dead process's lease has not lapsed yet
    # the same file, seen from a clock that is past the TTL (no sleeping in tests)
    later = Repo(Database(db_path), FakeClock(datetime.now(UTC) + timedelta(seconds=31)))
    assert later.get_run_lock().expired is True  # type: ignore[union-attr]
    assert later.acquire_run_lock("heir", 30) is True
    assert real.get_run_lock().owner == "heir"  # type: ignore[union-attr]
    assert real.heartbeat_run_lock("doomed") is False
    real.db.close()
    later.db.close()


def test_the_run_lock_gives_mutual_exclusion_across_processes(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    n_procs, rounds = 3, 6
    Repo(Database(db_path)).db.close()  # migrate once so the children only contend for the lock
    barrier, out = CTX.Barrier(n_procs), CTX.Queue()
    children = [
        spawn(procs, workers.contend, str(db_path), f"proc-{i}", rounds, barrier, out)
        for i in range(n_procs)
    ]
    results = drain(out, n_procs)
    for child in children:
        child.join(timeout=WAIT_S)
        assert child.exitcode == 0
    assert sorted(r[0] for r in results) == [f"proc-{i}" for i in range(n_procs)]
    assert all(entered == rounds for _, entered, _ in results)
    assert all(violations == 0 for _, _, violations in results), results
    repo = Repo(Database(db_path))
    assert repo.get_kv("counter") == str(n_procs * rounds)  # not a single lost update
    assert repo.get_run_lock() is None  # everybody released
    repo.db.close()


def test_exactly_one_process_wins_every_simultaneous_acquire(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    n_procs, rounds = 4, 25
    Repo(Database(db_path)).db.close()  # migrate once
    barrier, out = CTX.Barrier(n_procs), CTX.Queue()
    children = [
        spawn(procs, workers.race_rounds, str(db_path), f"racer-{i}", rounds, barrier, out)
        for i in range(n_procs)
    ]
    results = dict(drain(out, n_procs))
    for child in children:
        child.join(timeout=WAIT_S)
        assert child.exitcode == 0
    for rnd in range(rounds):
        winners = [name for name, outcomes in results.items() if outcomes[rnd]]
        assert len(winners) == 1, f"round {rnd}: {winners}"


# ------------------------------------------------------------------------------------ migration


def test_processes_starting_at_the_same_instant_migrate_a_fresh_file_safely(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    n_procs = 4
    barrier, out = CTX.Barrier(n_procs), CTX.Queue()
    children = [
        spawn(procs, workers.migrate_concurrently, str(db_path), barrier, out)
        for _ in range(n_procs)
    ]
    versions = drain(out, n_procs)
    for child in children:
        child.join(timeout=WAIT_S)
        assert child.exitcode == 0
    assert versions == [latest_schema_version()] * n_procs
    database = Database(db_path)
    assert database.schema_version() == latest_schema_version()
    database.close()


# ------------------------------------------------------------------------------------ restart / crash safety


def test_submissions_recorded_by_a_previous_process_still_count_after_the_restart(
    db_path: Path, workers: ModuleType, procs: list[Any], make_op: Callable[..., Opportunity]
) -> None:
    repo = Repo(Database(db_path))
    ops = [repo.upsert_opportunity(make_op())[0] for _ in range(3)]
    out = CTX.Queue()
    child = spawn(procs, workers.record_submissions, str(db_path), [o.id for o in ops], out)
    stamps = [datetime.fromisoformat(s) for s in out.get(timeout=WAIT_S)]
    child.join(timeout=WAIT_S)
    assert child.exitcode == 0 and len(stamps) == 3

    tz = "America/Chicago"
    days = {local_day(s, tz) for s in stamps}
    for day in days:  # normally one day; two only if the test straddled local midnight
        assert repo.count_submitted_on(day, tz) == sum(1 for s in stamps if local_day(s, tz) == day)
    assert sum(repo.count_submitted_on(d, tz) for d in days) == 3
    assert all(repo.has_submitted(o.id) for o in ops)
    assert repo.count_submitted_on(min(days) - timedelta(days=1), tz) == 0
    repo.db.close()


def test_a_process_that_crashes_mid_attempt_leaves_a_durable_applying_row_that_recovery_fixes(
    db_path: Path, workers: ModuleType, procs: list[Any], make_op: Callable[..., Opportunity]
) -> None:
    repo = Repo(Database(db_path))
    op = repo.upsert_opportunity(make_op())[0]
    ready = CTX.Event()
    child = spawn(procs, workers.die_mid_attempt, str(db_path), op.id, ready)
    assert ready.wait(WAIT_S)
    child.join(timeout=WAIT_S)
    assert child.exitcode == 17  # it really died without cleaning up

    survivor = Repo(Database(db_path))  # the "restarted" application
    [row] = survivor.list_applications()
    # written BEFORE the browser opened, and it survived
    assert row.status == ApplicationStatus.APPLYING
    assert row.opportunity_id == op.id
    assert survivor.has_submitted(op.id) is False
    assert survivor.recover_stale_applications(timedelta(0)) == 1
    recovered = survivor.latest_application(op.id)
    assert recovered is not None
    assert (recovered.status, recovered.reason) == (ApplicationStatus.FAILED, Reason.INTERRUPTED)
    repo.db.close()
    survivor.db.close()


def test_a_writer_waits_for_another_processes_write_transaction_instead_of_failing(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    repo = Repo(Database(db_path))  # migrated and in WAL mode
    ready = CTX.Event()
    child = spawn(procs, workers.hold_write_transaction, str(db_path), 1.5, ready)
    assert ready.wait(WAIT_S)
    started = time.monotonic()
    repo.set_kv("from-parent", "yes")  # blocked by the child's lock; busy_timeout makes it wait
    waited = time.monotonic() - started
    child.join(timeout=WAIT_S)
    assert child.exitcode == 0
    assert waited > 0.3  # it really was blocked, not lucky
    assert repo.get_kv("from-parent") == "yes" and repo.get_kv("from-child") == "yes"
    repo.db.close()


def test_readers_are_not_blocked_by_another_processes_write_transaction(
    db_path: Path, workers: ModuleType, procs: list[Any]
) -> None:
    hold_s = 4.0
    repo = Repo(Database(db_path))
    repo.set_kv("seen-before", "1")
    ready = CTX.Event()
    child = spawn(procs, workers.hold_write_transaction, str(db_path), hold_s, ready)
    assert ready.wait(WAIT_S)
    started = time.monotonic()
    assert repo.get_kv("seen-before") == "1"
    assert repo.get_kv("from-child") is None  # uncommitted data is invisible
    assert repo.count_submitted_on(date(2026, 9, 29), "UTC") == 0
    # answered while the child still held the write lock: a blocked reader would have waited ~hold_s
    assert time.monotonic() - started < hold_s / 2
    child.join(timeout=WAIT_S)
    assert child.exitcode == 0
    repo.db.close()


def test_this_module_uses_the_spawn_start_method_like_windows() -> None:
    assert CTX.get_start_method() == "spawn"
