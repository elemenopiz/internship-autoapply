"""Database: pragmas, per-thread connections, close/re-open, transactions, hostile paths."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from autoapply.clock import FakeClock
from autoapply.db import BUSY_TIMEOUT_MS, Database, Repo
from autoapply.models import Opportunity


def test_pragmas_are_applied_to_every_connection(db: Database) -> None:
    conn = db.connect()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS == 10000
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conn.row_factory is sqlite3.Row

    seen: list[tuple[object, ...]] = []

    def probe() -> None:
        other = db.connect()
        seen.append(
            (
                other.execute("PRAGMA journal_mode").fetchone()[0],
                other.execute("PRAGMA foreign_keys").fetchone()[0],
                other.execute("PRAGMA busy_timeout").fetchone()[0],
                other.row_factory is sqlite3.Row,
            )
        )

    worker = threading.Thread(target=probe)
    worker.start()
    worker.join()
    assert seen == [("wal", 1, 10000, True)]


def test_connect_is_cached_per_thread_and_distinct_across_threads(db: Database) -> None:
    main = db.connect()
    assert db.connect() is main
    others: list[sqlite3.Connection] = []

    def grab() -> None:
        first = db.connect()
        assert db.connect() is first
        others.append(first)

    workers = [threading.Thread(target=grab) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(others) == 3
    assert all(c is not main for c in others)
    assert len({id(c) for c in others}) == 3


def test_close_closes_connections_opened_by_other_threads(db: Database) -> None:
    captured: list[sqlite3.Connection] = []
    worker = threading.Thread(target=lambda: captured.append(db.connect()))
    worker.start()
    worker.join()
    main = db.connect()
    db.close()
    for conn in (captured[0], main):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert db.open_connections() == 0


def test_close_is_idempotent_and_database_reopens_lazily(db: Database) -> None:
    db.close()  # never connected: no-op
    first = db.connect()
    db.close()
    db.close()
    second = db.connect()
    assert second is not first
    assert second.execute("SELECT 1").fetchone()[0] == 1


def test_close_invalidates_cached_connections_of_other_live_threads(db: Database) -> None:
    ready, proceed = threading.Event(), threading.Event()
    result: dict[str, object] = {}

    def worker() -> None:
        before = db.connect()
        ready.set()
        proceed.wait(timeout=10)
        after = db.connect()  # must notice close() and not hand back the dead connection
        result["fresh"] = after is not before
        result["works"] = after.execute("SELECT 1").fetchone()[0]

    t = threading.Thread(target=worker)
    t.start()
    assert ready.wait(timeout=10)
    db.close()
    proceed.set()
    t.join(timeout=10)
    assert result == {"fresh": True, "works": 1}


def test_context_manager_closes_all_connections(tmp_path: Path) -> None:
    with Database(tmp_path / "ctx.db") as database:
        conn = database.connect()
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connections_of_finished_threads_are_pruned(db: Database) -> None:
    for _ in range(6):
        t = threading.Thread(target=db.connect)
        t.start()
        t.join()
    db.connect()  # a new connection prunes the dead threads' ones
    assert db.open_connections() == 1


def test_constructor_touches_no_disk_until_first_use(tmp_path: Path) -> None:
    target = tmp_path / "lazy" / "x.db"
    database = Database(target)
    assert not target.parent.exists()
    database.connect()
    assert target.exists()
    database.close()


@pytest.mark.parametrize("bad", ["", "   ", ":memory:", "file:memdb1?mode=memory"])
def test_non_file_paths_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="real file path"):
        Database(bad)


def test_relative_path_is_pinned_to_the_cwd_at_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    database = Database("rel.db")
    monkeypatch.chdir(second)
    database.connect()
    database.close()
    assert (first / "rel.db").exists()
    assert not (second / "rel.db").exists()
    assert database.path == (first / "rel.db")


def test_accepts_str_and_pathlike(tmp_path: Path) -> None:
    for candidate in (str(tmp_path / "a.db"), tmp_path / "b.db"):
        with Database(candidate) as database:
            assert database.connect().execute("SELECT 1").fetchone()[0] == 1


def test_wal_readers_are_not_blocked_by_an_open_write_transaction(db: Database) -> None:
    db.migrate()
    writer = db.connect()
    writer.execute("BEGIN IMMEDIATE")
    try:
        reader = Database(db.path)
        try:
            # WAL: a reader in another connection is not blocked by an open write transaction
            assert reader.connect().execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0
        finally:
            reader.close()
    finally:
        writer.execute("ROLLBACK")
    assert Path(str(db.path) + "-wal").exists()


def test_transaction_commits_on_success_and_rolls_back_on_error(db: Database) -> None:
    db.migrate()
    with db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('a', '1', 't')")
    with pytest.raises(RuntimeError, match="boom"), db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('b', '2', 't')")
        raise RuntimeError("boom")
    keys = [r["key"] for r in db.connect().execute("SELECT key FROM kv ORDER BY key")]
    assert keys == ["a"]
    assert not db.connect().in_transaction


def test_transaction_rolls_back_on_base_exception(db: Database) -> None:
    db.migrate()
    with pytest.raises(KeyboardInterrupt), db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('k', 'v', 't')")
        raise KeyboardInterrupt
    assert db.connect().execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0
    assert not db.connect().in_transaction


def test_nested_transactions_join_the_outer_one(db: Database) -> None:
    db.migrate()
    with pytest.raises(RuntimeError), db.transaction() as outer:
        outer.execute("INSERT INTO kv (key, value, updated_at) VALUES ('outer', '1', 't')")
        with db.transaction() as inner:
            assert inner is outer
            inner.execute("INSERT INTO kv (key, value, updated_at) VALUES ('inner', '1', 't')")
        assert outer.in_transaction  # the inner block did not commit
        raise RuntimeError("abort everything")
    assert db.connect().execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0


def test_failed_commit_path_leaves_no_open_transaction(db: Database) -> None:
    db.migrate()
    conn = db.connect()
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as tx:
        tx.execute("PRAGMA defer_foreign_keys = ON")  # violation is only detected at COMMIT
        tx.execute(
            "INSERT INTO applications (opportunity_id, attempt_no, status, mode, started_at) "
            "VALUES ('missing', 1, 'applying', 'full_auto', 't')"
        )
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_snapshot_gives_a_consistent_view_while_another_connection_commits(db: Database) -> None:
    db.migrate()
    writer = Database(db.path)
    try:
        with db.snapshot() as conn:
            assert conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0
            with writer.transaction() as w:
                w.execute("INSERT INTO kv (key, value, updated_at) VALUES ('x', '1', 't')")
            # still the old snapshot
            assert conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 0
        assert not db.connect().in_transaction
        assert db.connect().execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 1
    finally:
        writer.close()


def test_every_repo_write_ends_with_no_open_transaction(
    repo: Repo, make_op: Callable[..., Opportunity]
) -> None:
    repo.upsert_opportunity(make_op())
    repo.set_kv("k", "v")
    repo.acquire_run_lock("o", 10)
    repo.start_run("dry_run", "test")
    assert not repo.db.connect().in_transaction


def test_two_database_instances_share_committed_data(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    a, b = Database(path), Database(path)
    try:
        repo_a, repo_b = Repo(a), Repo(b)
        repo_a.set_kv("greeting", "hello")
        assert repo_b.get_kv("greeting") == "hello"
        repo_b.set_kv("greeting", "hi")
        assert repo_a.get_kv("greeting") == "hi"
    finally:
        a.close()
        b.close()


def test_reopen_after_close_sees_previous_data(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = Database(path)
    Repo(first).set_kv("k", "v")
    first.close()
    second = Database(path)
    try:
        assert Repo(second).get_kv("k") == "v"
    finally:
        second.close()


def test_windows_hostile_unicode_and_space_path(tmp_path: Path, fake_clock: FakeClock) -> None:
    hostile = (
        tmp_path
        / "Zoë's données 日本語 (backup) #1 & 100% [x]+y=z; ok!"
        / "auto apply — données.db"
    )
    with Database(hostile) as database:
        repo = Repo(database, fake_clock)
        repo.set_kv("ключ", "значение ✓")
        assert repo.get_kv("ключ") == "значение ✓"
    assert hostile.exists()
    with Database(hostile) as again:
        assert Repo(again).get_kv("ключ") == "значение ✓"


def test_long_path_component_is_fine(tmp_path: Path) -> None:
    long_dir = tmp_path / ("d" * 120)
    with Database(long_dir / "x.db") as database:
        Repo(database).set_kv("k", "v")
    assert (long_dir / "x.db").exists()


def test_a_directory_in_place_of_the_file_fails_with_the_path_in_the_message(
    tmp_path: Path,
) -> None:
    impostor = tmp_path / "i-am-a-directory.db"
    impostor.mkdir()
    database = Database(impostor)
    with pytest.raises(sqlite3.OperationalError, match="i-am-a-directory.db"):
        database.connect()
    assert database.open_connections() == 0  # the half-opened connection was closed, not leaked


def test_a_file_that_is_not_a_database_fails_with_the_path_in_the_message(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is definitely not a sqlite database file. " * 40)
    database = Database(garbage)
    with pytest.raises(sqlite3.DatabaseError, match="garbage.db"):
        database.connect()
    assert database.open_connections() == 0
    assert garbage.read_bytes().startswith(b"this is definitely")  # and we left the file alone


def test_repr_names_the_file(db: Database) -> None:
    assert "autoapply.db" in repr(db)
