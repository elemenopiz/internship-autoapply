"""Entry points executed in freshly spawned interpreters by ``test_multiprocess.py``.

Not a test module (pytest does not collect ``_``-prefixed files). Everything a worker needs is passed as
picklable arguments, because the ``spawn`` start method (the only one on Windows) shares no memory.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from autoapply.db import Database, Repo
from autoapply.models import ApplicationStatus, ApplyResult


def hold_lock(db_path: str, owner: str, ttl_s: float, ready: Any, release: Any, out: Any) -> None:
    """Acquire the run lock, tell the parent, then hold it until told to release (or until killed)."""
    repo = Repo(Database(Path(db_path)))
    acquired = repo.acquire_run_lock(owner, ttl_s)
    out.put(("acquired", acquired))
    ready.set()
    if acquired and release.wait(timeout=120):
        out.put(("released", repo.release_run_lock(owner)))
    repo.db.close()


def contend(db_path: str, owner: str, rounds: int, barrier: Any, out: Any) -> None:
    """Enter a deliberately NON-atomic critical section ``rounds`` times, guarded only by the run lock.

    A broken lock shows up as a lost update on the shared counter or as somebody else's name in "holder".
    """
    repo = Repo(Database(Path(db_path)))
    barrier.wait(timeout=120)
    entered = violations = 0
    deadline = time.monotonic() + 180
    for _ in range(rounds):
        while not repo.acquire_run_lock(owner, 60):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{owner} never got the lock")
            time.sleep(0.002)
        entered += 1
        before = int(repo.get_kv("counter", "0") or 0)
        repo.set_kv("holder", owner)
        time.sleep(0.004)  # every chance for a second holder to barge in
        if repo.get_kv("holder") != owner:
            violations += 1
        repo.set_kv("counter", str(before + 1))
        if not repo.release_run_lock(owner):
            violations += 1
    repo.db.close()
    out.put((owner, entered, violations))


def race_rounds(db_path: str, owner: str, rounds: int, barrier: Any, out: Any) -> None:
    """Every round all processes try ONE acquire at the same instant (released by a barrier).

    A correct lock yields exactly one winner per round; a non-atomic check-then-set yields several.
    """
    repo = Repo(Database(Path(db_path)))
    outcomes: list[bool] = []
    for _ in range(rounds):
        barrier.wait(timeout=120)  # round start: released together
        won = repo.acquire_run_lock(owner, 60)
        outcomes.append(won)
        barrier.wait(timeout=120)  # everybody has attempted; only now may the winner let go
        if won and not repo.release_run_lock(owner):
            raise AssertionError(f"{owner} won the lock but could not release it")
    repo.db.close()
    out.put((owner, outcomes))


def migrate_concurrently(db_path: str, barrier: Any, out: Any) -> None:
    """Migrate a fresh file at the same instant as the sibling processes."""
    database = Database(Path(db_path))
    barrier.wait(timeout=120)
    out.put(database.migrate())
    database.close()


def record_submissions(db_path: str, opportunity_ids: list[str], out: Any) -> None:
    """A previous run of the application: real submissions with the system clock, then a clean exit."""
    repo = Repo(Database(Path(db_path)))
    stamps: list[str] = []
    for opportunity_id in opportunity_ids:
        app = repo.create_application(opportunity_id, "full_auto")
        assert app.id is not None
        done = repo.finish_application(app.id, ApplyResult(status=ApplicationStatus.SUBMITTED))
        assert done.submitted_at is not None
        stamps.append(done.submitted_at.isoformat())
    repo.db.close()
    out.put(stamps)


def die_mid_attempt(db_path: str, opportunity_id: str, ready: Any) -> None:
    """Write the APPLYING row (as the pipeline does before opening the browser), then crash hard."""
    repo = Repo(Database(Path(db_path)))
    app = repo.create_application(opportunity_id, "full_auto")
    ready.set()
    os._exit(17)  # no cleanup, no connection close: like a power cut for the process
    raise AssertionError(app)  # pragma: no cover - unreachable


def hold_write_transaction(db_path: str, hold_s: float, ready: Any) -> None:
    """Hold the database write lock for ``hold_s`` seconds, then commit."""
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('from-child', 'yes', 't')")
    ready.set()
    time.sleep(hold_s)
    conn.execute("COMMIT")
    conn.close()
