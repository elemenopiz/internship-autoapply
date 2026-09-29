"""8 threads hammering the store: no lost updates, no duplicate attempt numbers, no 'database is locked'."""

from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from autoapply.clock import FakeClock
from autoapply.db import Database, Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    PendingQuestion,
    Reason,
    RunMode,
    ScoreResult,
    ScreeningAnswer,
)

THREADS = 8
POOL = 12  # distinct opportunities the threads fight over
SEEDED = POOL // 2  # the first half exists before the race starts, the rest is created BY the race
ROUNDS = 14

S = ApplicationStatus
NOW = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)  # noon in Chicago
TODAY = date(2026, 9, 29)
CHICAGO = "America/Chicago"


def _submits(idx: int, rnd: int) -> bool:
    return (idx + rnd) % 3 == 0


@dataclass
class Ledger:
    """What the workers did, recorded under a lock, to check the database against."""

    is_new: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))
    extra_keys: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    longest: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    submissions: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def hammer(repos: list[Repo], pool: list[Opportunity], seed: int) -> Ledger:
    """THREADS workers; worker i uses repos[i % len(repos)]. Each round: merge-upsert, create, finish."""
    barrier = threading.Barrier(THREADS)
    ledger = Ledger()

    def worker(idx: int) -> None:
        repo = repos[idx % len(repos)]
        rng = random.Random(seed * 1000 + idx)
        barrier.wait(timeout=30)
        for rnd in range(ROUNDS):
            op = pool[rng.randrange(len(pool))]
            key = f"t{idx}_r{rnd}"
            size = 100 + idx * 20 + rnd  # unique per (idx, rnd) and longer than the seed text
            variant = op.model_copy(update={"description": "x" * size, "extra": {key: rnd}})
            _, is_new = repo.upsert_opportunity(variant)
            app = repo.create_application(op.id, RunMode.FULL_AUTO)
            submitted = _submits(idx, rnd)
            result = (
                ApplyResult(status=S.SUBMITTED, confirmation=f"{idx}/{rnd}")
                if submitted
                else ApplyResult(status=S.FAILED, reason=Reason.TIMEOUT)
            )
            repo.finish_application(app.id, result)
            with ledger.lock:
                ledger.is_new[op.id].append(is_new)
                ledger.extra_keys[op.id].add(key)
                ledger.longest[op.id] = max(ledger.longest[op.id], size)
                ledger.submissions += int(submitted)

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        for future in [executor.submit(worker, i) for i in range(THREADS)]:
            # re-raises whatever a worker hit ("database is locked", ...)
            future.result(timeout=120)
    return ledger


def verify(repo: Repo, pool: list[Opportunity], ledger: Ledger) -> None:
    seeded = {o.id for o in pool[:SEEDED]}
    touched = set(ledger.is_new)
    # every row was inserted exactly once, by whichever thread won the race (seeded rows: never)
    for oid, flags in ledger.is_new.items():
        assert flags.count(True) == (0 if oid in seeded else 1), oid
    assert sum(len(flags) for flags in ledger.is_new.values()) == THREADS * ROUNDS
    assert repo.count_opportunities() == len(seeded | touched)
    for oid in touched:
        stored = repo.get_opportunity(oid)
        assert stored is not None
        assert set(stored.extra) == ledger.extra_keys[oid]  # no lost merge
        assert stored.description == "x" * ledger.longest[oid]  # the longest text won, in any order
        attempts = sorted(a.attempt_no for a in repo.list_applications(opportunity_id=oid))
        assert attempts == list(range(1, len(attempts) + 1))  # unique and gap-free
    assert repo.count_applications() == THREADS * ROUNDS
    assert repo.count_applications(status=S.APPLYING) == 0  # every attempt was finished
    assert repo.count_applications(status=S.SUBMITTED) == ledger.submissions
    assert repo.count_submitted_on(TODAY, CHICAGO) == ledger.submissions


def test_the_submission_schedule_is_not_vacuous() -> None:
    # the test is not vacuous
    assert sum(_submits(i, r) for i in range(THREADS) for r in range(ROUNDS)) > 20


def _pool(repo: Repo, make_op: Callable[..., Opportunity]) -> list[Opportunity]:
    ops = [make_op() for _ in range(POOL)]
    repo.upsert_opportunities(ops[:SEEDED])
    return ops


def test_eight_threads_sharing_one_repo(
    repo: Repo, make_op: Callable[..., Opportunity], fake_clock: FakeClock
) -> None:
    pool = _pool(repo, make_op)
    fake_clock.set(NOW)
    ledger = hammer([repo], pool, seed=0)
    verify(repo, pool, ledger)
    # the finished workers' connections were closed, not leaked
    assert repo.db.open_connections() == 1


def test_eight_independent_database_instances_like_eight_processes(
    db_path: Path, repo: Repo, make_op: Callable[..., Opportunity], fake_clock: FakeClock
) -> None:
    pool = _pool(repo, make_op)
    fake_clock.set(NOW)
    databases = [Database(db_path) for _ in range(THREADS)]
    try:
        repos = [Repo(d, fake_clock) for d in databases]
        ledger = hammer(repos, pool, seed=1)
        verify(repo, pool, ledger)
    finally:
        for d in databases:
            d.close()


def test_each_thread_really_used_its_own_connection(
    repo: Repo, make_op: Callable[..., Opportunity]
) -> None:
    op = make_op()
    seen: set[int] = set()
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def work() -> None:
        barrier.wait(timeout=30)
        repo.upsert_opportunity(op)
        with lock:
            seen.add(id(repo.db.connect()))
        # stay alive until everyone has recorded theirs (ids are reused otherwise)
        barrier.wait(timeout=30)

    threads = [threading.Thread(target=work) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(seen) == THREADS
    assert repo.count_opportunities() == 1


def test_concurrent_bulk_upserts_scoring_listing_and_stats_do_not_deadlock(
    repo: Repo, make_op: Callable[..., Opportunity]
) -> None:
    ops = [make_op() for _ in range(40)]
    barrier = threading.Barrier(4)

    def ingest(offset: int) -> None:
        barrier.wait(timeout=30)
        for start in range(0, len(ops), 10):
            repo.upsert_opportunities(ops[(start + offset) % len(ops) :][:10])

    def score() -> None:
        barrier.wait(timeout=30)
        for _ in range(20):
            repo.set_scores(
                [(o.id, ScoreResult(score=50 + i, passed=True)) for i, o in enumerate(ops)]
            )
            repo.list_opportunities(min_score=10, limit=5)
            repo.stats()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(ingest, 0),
            executor.submit(ingest, 5),
            executor.submit(ingest, 20),
            executor.submit(score),
        ]
        for future in futures:
            future.result(timeout=120)
    assert repo.count_opportunities() == 40
    # after all inserts settled
    repo.set_scores([(o.id, ScoreResult(score=90, passed=True)) for o in ops])
    assert repo.count_opportunities(passed_only=True) == 40


def test_concurrent_answer_pending_and_kv_writes_stay_consistent(repo: Repo) -> None:
    barrier = threading.Barrier(THREADS)

    def work(idx: int) -> None:
        barrier.wait(timeout=30)
        for i in range(10):
            repo.add_pending_question(
                PendingQuestion(question=f"Shared question {i}?", opportunity_id="opp")
            )
            repo.upsert_answer(ScreeningAnswer(question=f"Answer topic {i}", answer=f"from {idx}"))
            repo.set_kv(f"key-{i}", str(idx))

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        for future in [executor.submit(work, i) for i in range(THREADS)]:
            future.result(timeout=120)
    assert len(repo.list_pending_questions()) == 10  # dedup held under contention
    assert len(repo.list_answers()) == 10  # one row per topic, never a duplicate
    assert all(repo.get_kv(f"key-{i}") is not None for i in range(10))


def test_contended_run_lock_hands_over_cleanly_between_threads(repo: Repo) -> None:
    """Threads take turns through the lock; the guarded read-modify-write never loses an update."""
    turns_each = 5
    barrier = threading.Barrier(THREADS)

    def work(idx: int) -> None:
        barrier.wait(timeout=30)
        for _ in range(turns_each):
            owner = f"t{idx}"
            while not repo.acquire_run_lock(owner, 30):
                time.sleep(0.001)
            repo.set_kv("counter", str(int(repo.get_kv("counter", "0") or 0) + 1))
            assert repo.release_run_lock(owner)

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        for future in [executor.submit(work, i) for i in range(THREADS)]:
            future.result(timeout=120)
    assert repo.get_kv("counter") == str(THREADS * turns_each)
