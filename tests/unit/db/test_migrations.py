"""Migrations: append-only list, PRAGMA user_version, idempotency, atomicity, concurrency."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import autoapply.db as db_module
from autoapply.clock import FakeClock
from autoapply.db import (
    MIGRATIONS,
    Database,
    Migration,
    Repo,
    SchemaVersionError,
    latest_schema_version,
)

EXPECTED_TABLES = {
    "opportunities",
    "applications",
    "screening_answers",
    "pending_questions",
    "ats_accounts",
    "runs",
    "run_lock",
    "kv",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows if not r["name"].startswith("sqlite_")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_shipped_migrations_are_contiguous_from_one() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == list(range(1, len(MIGRATIONS) + 1))
    assert latest_schema_version() == len(MIGRATIONS)
    for migration in MIGRATIONS:
        assert migration.description.strip()
        assert migration.statements
        assert all(isinstance(s, str) and s.strip() for s in migration.statements)


def test_fresh_database_starts_at_zero_and_migrates_to_latest(db: Database) -> None:
    assert db.schema_version() == 0
    assert db.migrate() == latest_schema_version()
    assert db.schema_version() == latest_schema_version()
    assert _tables(db.connect()) == EXPECTED_TABLES


def test_v1_schema_has_the_documented_shape(db: Database) -> None:
    db.migrate()
    conn = db.connect()
    assert {"id", "company", "title", "score", "score_detail", "first_seen", "last_seen"} <= set(
        _columns(conn, "opportunities")
    )
    fks = conn.execute("PRAGMA foreign_key_list(applications)").fetchall()
    assert [(r["table"], r["from"], r["on_delete"]) for r in fks] == [
        ("opportunities", "opportunity_id", "CASCADE")
    ]
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(screening_answers)")}
    assert "ux_answers_intent" in indexes


def test_schema_never_stores_passwords_or_secrets(db: Database) -> None:
    db.migrate()
    conn = db.connect()
    secretish = re.compile(r"password|passwd|passphrase|secret|token|api_?key|credential")
    for table in EXPECTED_TABLES:
        for column in _columns(conn, table):
            assert not secretish.search(column.lower()), (
                f"{table}.{column} looks like a secret column"
            )


def test_migrate_twice_is_idempotent_and_keeps_data(db: Database) -> None:
    db.migrate()
    with db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('k', 'v', 't')")
    assert db.migrate() == latest_schema_version()
    assert db.migrate() == latest_schema_version()
    assert db.connect().execute("SELECT value FROM kv WHERE key = 'k'").fetchone()[0] == "v"


def test_migrate_on_a_new_instance_of_a_migrated_file_is_a_no_op(db_path: Path) -> None:
    first = Database(db_path)
    first.migrate()
    first.close()
    second = Database(db_path)
    try:
        assert second.migrate() == latest_schema_version()
    finally:
        second.close()


def test_repo_constructor_migrates(db: Database, fake_clock: FakeClock) -> None:
    assert db.schema_version() == 0
    Repo(db, fake_clock)
    assert db.schema_version() == latest_schema_version()


def test_up_to_date_migrate_takes_no_write_lock(db: Database) -> None:
    db.migrate()
    holder = Database(db.path)
    try:
        conn = holder.connect()
        conn.execute("BEGIN IMMEDIATE")  # somebody else is mid-write
        started = time.monotonic()
        assert db.migrate() == latest_schema_version()
        assert time.monotonic() - started < 2.0  # would block for the 10 s busy timeout otherwise
        conn.execute("ROLLBACK")
    finally:
        holder.close()


def test_upgrade_applies_only_pending_migrations_and_preserves_data(
    db: Database, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.migrate()
    with db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('old', 'row', 't')")
    db.close()

    v2 = Migration(2, "add opportunity notes", ("ALTER TABLE opportunities ADD COLUMN notes TEXT",))
    v3 = Migration(
        3,
        "add kv index",
        ("CREATE INDEX idx_kv_updated ON kv(updated_at)", "UPDATE kv SET value = value || '!'"),
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", (*MIGRATIONS, v2, v3))

    upgraded = Database(db_path)
    try:
        assert upgraded.schema_version() == 1
        assert upgraded.migrate() == 3
        conn = upgraded.connect()
        assert "notes" in _columns(conn, "opportunities")
        assert conn.execute("SELECT value FROM kv WHERE key = 'old'").fetchone()[0] == "row!"
        # a second call must not re-run anything (the ALTER / UPDATE would fail or double-apply)
        assert upgraded.migrate() == 3
        assert conn.execute("SELECT value FROM kv WHERE key = 'old'").fetchone()[0] == "row!"
    finally:
        upgraded.close()


def test_failed_migration_rolls_back_every_pending_step(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.migrate()
    good = Migration(2, "creates a table", ("CREATE TABLE extra_a (id INTEGER)",))
    bad = Migration(3, "breaks half way", ("CREATE TABLE extra_b (id INTEGER)", "THIS IS NOT SQL"))
    monkeypatch.setattr(db_module, "MIGRATIONS", (*MIGRATIONS, good, bad))
    with pytest.raises(sqlite3.OperationalError):
        db.migrate()
    assert db.schema_version() == 1  # all-or-nothing: not even v2 stuck
    assert not {"extra_a", "extra_b"} & _tables(db.connect())
    assert not db.connect().in_transaction


def test_database_from_a_newer_application_is_refused(db: Database) -> None:
    db.migrate()
    with db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('k', 'v', 't')")
        conn.execute(f"PRAGMA user_version = {latest_schema_version() + 41}")
    with pytest.raises(SchemaVersionError, match="newer"):
        db.migrate()
    with pytest.raises(SchemaVersionError):
        Repo(db)
    # untouched
    assert db.connect().execute("SELECT value FROM kv WHERE key = 'k'").fetchone()[0] == "v"
    assert db.schema_version() == latest_schema_version() + 41


def test_migration_v1_tolerates_a_preexisting_unversioned_schema(db: Database) -> None:
    db.migrate()
    with db.transaction() as conn:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES ('keep', 'me', 't')")
        conn.execute("PRAGMA user_version = 0")  # tables exist but the file was never versioned
    assert db.schema_version() == 0
    assert db.migrate() == latest_schema_version()
    assert db.connect().execute("SELECT value FROM kv WHERE key = 'keep'").fetchone()[0] == "me"


def test_concurrent_migrate_from_many_threads_and_instances(db_path: Path) -> None:
    instances = [Database(db_path) for _ in range(8)]
    barrier = threading.Barrier(len(instances))
    errors: list[BaseException] = []
    versions: list[int] = []

    def run(instance: Database) -> None:
        try:
            barrier.wait(timeout=10)
            versions.append(instance.migrate())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in instances]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert versions == [latest_schema_version()] * 8
        assert _tables(instances[0].connect()) == EXPECTED_TABLES
    finally:
        for i in instances:
            i.close()


def test_migrated_database_is_immediately_usable_through_the_repo(
    db: Database, fake_clock: FakeClock
) -> None:
    repo = Repo(db, fake_clock)
    repo.set_kv("ready", "yes")
    assert repo.get_kv("ready") == "yes"
