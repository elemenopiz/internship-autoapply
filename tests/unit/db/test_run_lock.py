"""Run lock semantics: single holder, TTL expiry + takeover, heartbeat, owner-only release.

Cross-process behaviour (a real second interpreter) is covered in test_multiprocess.py.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from autoapply.clock import FakeClock
from autoapply.db import Database, Repo

SECOND = timedelta(seconds=1)
US = timedelta(microseconds=1)


@pytest.fixture
def two_repos(db_path: Path, fake_clock: FakeClock) -> Iterator[tuple[Repo, Repo]]:
    """Two independent Database instances on one file, like two processes, sharing one fake clock."""
    a, b = Database(db_path), Database(db_path)
    try:
        yield Repo(a, fake_clock), Repo(b, fake_clock)
    finally:
        a.close()
        b.close()


# ------------------------------------------------------------------------------------ basics


def test_first_caller_gets_the_lock_and_the_second_does_not(repo: Repo) -> None:
    assert repo.acquire_run_lock("run-a", 60) is True
    assert repo.acquire_run_lock("run-b", 60) is False


def test_the_lock_is_not_reentrant_even_for_the_same_owner(repo: Repo) -> None:
    # owners are expected to be unique per run; a constant owner string must still exclude itself
    assert repo.acquire_run_lock("scheduler", 60) is True
    assert repo.acquire_run_lock("scheduler", 60) is False


def test_get_run_lock_describes_the_holder(repo: Repo, fake_clock: FakeClock) -> None:
    assert repo.get_run_lock() is None
    repo.acquire_run_lock("run-a", 90)
    info = repo.get_run_lock()
    assert info is not None
    assert info.owner == "run-a"
    assert info.acquired_at == info.heartbeat_at == fake_clock.now()
    assert info.expires_at == fake_clock.now() + timedelta(seconds=90)
    assert info.ttl_s == 90 and info.expired is False
    fake_clock.advance(timedelta(seconds=91))
    assert repo.get_run_lock().expired is True


def test_ttl_defaults_to_a_sane_value(repo: Repo) -> None:
    assert repo.acquire_run_lock("run-a") is True
    assert repo.get_run_lock().ttl_s > 0


@pytest.mark.parametrize(
    ("owner", "ttl"), [("", 60), ("   ", 60), ("run-a", 0), ("run-a", -5), ("run-a", float("nan"))]
)
def test_bad_arguments_are_rejected(repo: Repo, owner: str, ttl: float) -> None:
    with pytest.raises(ValueError):
        repo.acquire_run_lock(owner, ttl)
    assert repo.get_run_lock() is None


# ------------------------------------------------------------------------------------ release


def test_only_the_owner_can_release(repo: Repo) -> None:
    repo.acquire_run_lock("run-a", 60)
    assert repo.release_run_lock("run-b") is False
    assert repo.get_run_lock().owner == "run-a"
    assert repo.acquire_run_lock("run-b", 60) is False  # still held
    assert repo.release_run_lock("run-a") is True
    assert repo.release_run_lock("run-a") is False  # already gone
    assert repo.get_run_lock() is None


def test_release_then_another_owner_can_acquire(repo: Repo) -> None:
    repo.acquire_run_lock("run-a", 60)
    repo.release_run_lock("run-a")
    assert repo.acquire_run_lock("run-b", 60) is True
    assert repo.get_run_lock().owner == "run-b"


def test_releasing_a_lock_nobody_holds_is_harmless(repo: Repo) -> None:
    assert repo.release_run_lock("run-a") is False


def test_a_stale_owner_cannot_release_the_new_holders_lock(
    repo: Repo, fake_clock: FakeClock
) -> None:
    repo.acquire_run_lock("old", 30)
    fake_clock.advance(timedelta(seconds=31))
    assert repo.acquire_run_lock("new", 30) is True
    assert repo.release_run_lock("old") is False
    assert repo.get_run_lock().owner == "new"


# ------------------------------------------------------------------------------------ expiry / takeover


def test_expiry_boundary_is_exclusive(repo: Repo, fake_clock: FakeClock) -> None:
    repo.acquire_run_lock("run-a", 30)
    fake_clock.advance(timedelta(seconds=30) - US)
    assert repo.acquire_run_lock("run-b", 30) is False  # one microsecond before the lease ends
    fake_clock.advance(US)
    assert repo.acquire_run_lock("run-b", 30) is True  # at expires_at the lease is over


def test_an_expired_lock_is_taken_over_and_the_old_owner_learns_it(
    repo: Repo, fake_clock: FakeClock
) -> None:
    repo.acquire_run_lock("crashed", 60)
    fake_clock.advance(timedelta(minutes=5))
    assert repo.acquire_run_lock("fresh", 60) is True
    info = repo.get_run_lock()
    assert info.owner == "fresh" and info.acquired_at == fake_clock.now()
    assert repo.heartbeat_run_lock("crashed") is False  # it must stop
    assert repo.release_run_lock("crashed") is False


def test_takeover_is_atomic_only_one_of_two_contenders_wins(
    two_repos: tuple[Repo, Repo], fake_clock: FakeClock
) -> None:
    a, b = two_repos
    a.acquire_run_lock("crashed", 10)
    fake_clock.advance(timedelta(seconds=11))
    results = [a.acquire_run_lock("a", 60), b.acquire_run_lock("b", 60)]
    assert results == [True, False]
    assert b.get_run_lock().owner == "a"


# ------------------------------------------------------------------------------------ heartbeat


def test_heartbeat_extends_the_lease(repo: Repo, fake_clock: FakeClock) -> None:
    repo.acquire_run_lock("run-a", 60)
    fake_clock.advance(timedelta(seconds=50))
    assert repo.heartbeat_run_lock("run-a") is True  # lease now runs until t+110
    info = repo.get_run_lock()
    assert info.heartbeat_at == fake_clock.now()
    assert info.expires_at == fake_clock.now() + timedelta(seconds=60)
    # t+105: past the ORIGINAL expiry, inside the extended one
    fake_clock.advance(timedelta(seconds=55))
    assert repo.acquire_run_lock("run-b", 60) is False
    fake_clock.advance(timedelta(seconds=5))  # t+110
    assert repo.acquire_run_lock("run-b", 60) is True


def test_heartbeat_can_change_the_ttl_and_remembers_it(repo: Repo, fake_clock: FakeClock) -> None:
    repo.acquire_run_lock("run-a", 60)
    assert repo.heartbeat_run_lock("run-a", ttl_s=300) is True
    assert repo.get_run_lock().ttl_s == 300
    fake_clock.advance(timedelta(seconds=100))
    assert repo.heartbeat_run_lock("run-a") is True  # reuses the 300 s now stored
    assert repo.get_run_lock().expires_at == fake_clock.now() + timedelta(seconds=300)
    with pytest.raises(ValueError):
        repo.heartbeat_run_lock("run-a", ttl_s=0)


def test_heartbeat_by_a_non_owner_or_without_a_lock_fails(repo: Repo) -> None:
    assert repo.heartbeat_run_lock("run-a") is False
    repo.acquire_run_lock("run-a", 60)
    assert repo.heartbeat_run_lock("run-b") is False
    assert repo.get_run_lock().expires_at is not None


def test_the_owner_can_revive_a_lapsed_lease_until_somebody_takes_over(
    repo: Repo, fake_clock: FakeClock
) -> None:
    repo.acquire_run_lock("run-a", 30)
    fake_clock.advance(timedelta(seconds=45))  # lapsed, but nobody has taken it
    assert repo.get_run_lock().expired is True
    assert repo.heartbeat_run_lock("run-a") is True
    assert repo.get_run_lock().expired is False
    assert repo.acquire_run_lock("run-b", 30) is False
    # ... but once someone HAS taken over, the old owner's heartbeat must fail
    fake_clock.advance(timedelta(seconds=45))
    assert repo.acquire_run_lock("run-b", 30) is True
    assert repo.heartbeat_run_lock("run-a") is False


def test_a_heartbeat_after_release_fails(repo: Repo) -> None:
    repo.acquire_run_lock("run-a", 60)
    repo.release_run_lock("run-a")
    assert repo.heartbeat_run_lock("run-a") is False


# ------------------------------------------------------------------------------------ two instances on one file


def test_two_database_instances_share_one_lock(two_repos: tuple[Repo, Repo]) -> None:
    a, b = two_repos
    assert a.acquire_run_lock("proc-a", 60) is True
    assert b.acquire_run_lock("proc-b", 60) is False
    assert b.release_run_lock("proc-b") is False
    # any handle may extend the recorded owner's lease
    assert b.heartbeat_run_lock("proc-a") is True
    assert a.release_run_lock("proc-a") is True
    assert b.acquire_run_lock("proc-b", 60) is True
    assert a.acquire_run_lock("proc-a", 60) is False


def test_exactly_one_of_many_threads_wins_every_simultaneous_acquire(
    db_path: Path, fake_clock: FakeClock
) -> None:
    n, rounds = 8, 30
    instances = [Database(db_path) for _ in range(n)]  # separate connections: real parallel access
    repos = [Repo(i, fake_clock) for i in instances]
    barrier = threading.Barrier(n)
    outcomes: list[list[bool]] = [[] for _ in range(n)]
    errors: list[BaseException] = []

    def contend(idx: int) -> None:
        try:
            for _ in range(rounds):
                barrier.wait(timeout=30)  # released together
                won = repos[idx].acquire_run_lock(f"thread-{idx}", 60)
                outcomes[idx].append(won)
                barrier.wait(timeout=30)  # all attempts are in before the winner releases
                if won:
                    assert repos[idx].release_run_lock(f"thread-{idx}")
        except BaseException as exc:
            errors.append(exc)
            barrier.abort()

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors
        for rnd in range(rounds):
            assert sum(o[rnd] for o in outcomes) == 1, f"round {rnd}"
    finally:
        for i in instances:
            i.close()


def test_the_lock_lives_in_the_file_so_it_survives_a_restart(
    db_path: Path, fake_clock: FakeClock
) -> None:
    first = Database(db_path)
    Repo(first, fake_clock).acquire_run_lock("run-a", 300)
    first.close()
    second = Database(db_path)
    try:
        after = Repo(second, fake_clock)
        # the dead process's lease still counts
        assert after.acquire_run_lock("run-b", 300) is False
        fake_clock.advance(timedelta(seconds=301))
        assert after.acquire_run_lock("run-b", 300) is True  # ... until its TTL runs out
    finally:
        second.close()


def test_other_writes_do_not_disturb_the_lock(repo: Repo) -> None:
    repo.acquire_run_lock("run-a", 60)
    repo.set_kv("k", "v")
    repo.start_run("full_auto", "manual")
    assert repo.get_run_lock().owner == "run-a"
