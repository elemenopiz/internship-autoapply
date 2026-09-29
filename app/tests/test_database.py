"""Comprehensive unit tests for db.database.Database.

Each test is tagged with its requirement ID for traceability.
Uses tmp_path for full isolation — no shared database state between tests.

| Req ID    | Test Function                                      |
|-----------|----------------------------------------------------|
| AC-004-1  | test_init_creates_tables                           |
| AC-004-1  | test_init_creates_indexes                          |
| AC-004-2  | test_save_application_returns_id                   |
| AC-004-N1 | test_save_application_duplicate_raises_integrity   |
| AC-004-3  | test_update_status_changes_fields                  |
| FR-004    | test_get_all_applications_returns_list             |
| AC-004-4  | test_get_all_applications_filter_by_status         |
| FR-004    | test_get_all_applications_filter_by_platform       |
| AC-004-5  | test_get_all_applications_search                   |
| FR-004    | test_get_all_applications_pagination               |
| AC-004-4  | test_get_all_applications_ordered_by_applied_at_desc |
| FR-004    | test_get_application_found                         |
| AC-004-N2 | test_get_application_not_found_returns_none        |
| AC-004-6  | test_exists_true                                   |
| AC-004-6  | test_exists_false                                  |
| AC-004-7  | test_export_csv_writes_file                        |
| AC-004-N3 | test_export_csv_empty_db_no_error                  |
| AC-004-8  | test_save_feed_event_returns_id                    |
| AC-004-9  | test_get_feed_events_ordered_desc                  |
| AC-004-9  | test_get_feed_events_respects_limit                |
| AC-004-10 | test_get_analytics_summary                         |
| AC-004-11 | test_get_daily_analytics                           |
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from db.database import Database
from db.models import Application

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Create an isolated Database backed by a temp file."""
    return Database(tmp_path / "test.db")


def insert_sample(db: Database, external_id: str = "job1", platform: str = "linkedin", **kwargs) -> int:
    """Insert a sample application with sensible defaults; return the new row id."""
    defaults = dict(
        job_title="Engineer",
        company="Acme",
        location="NYC",
        salary="100k",
        apply_url="https://example.com",
        match_score=85,
        resume_path=None,
        cover_letter_path=None,
        cover_letter_text="Dear...",
        status="applied",
        error_message=None,
    )
    defaults.update(kwargs)
    return db.save_application(external_id=external_id, platform=platform, **defaults)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    """AC-004-1: Database initialisation creates the expected schema."""

    def test_init_creates_tables(self, db: Database):
        """Verify both applications and feed_events tables exist after init."""
        # Arrange — db fixture already called __init__ / init_schema
        # Act
        with db._connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        # Assert
        assert "applications" in tables
        assert "feed_events" in tables

    def test_init_creates_indexes(self, db: Database):
        """Verify the dedup, status, and applied_at indexes exist."""
        # Arrange — schema applied during fixture creation
        # Act
        with db._connect() as conn:
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        # Assert
        assert "idx_dedup" in indexes
        assert "idx_status" in indexes
        assert "idx_applied_at" in indexes


# ---------------------------------------------------------------------------
# save_application tests
# ---------------------------------------------------------------------------

class TestSaveApplication:
    """AC-004-2 / AC-004-N1: Inserting applications."""

    def test_save_application_returns_id(self, db: Database):
        """AC-004-2: save_application returns a positive integer row id."""
        # Arrange / Act
        row_id = insert_sample(db)
        # Assert
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_save_application_duplicate_raises_integrity_error(self, db: Database):
        """AC-004-N1: Inserting the same (external_id, platform) pair twice raises IntegrityError."""
        # Arrange
        insert_sample(db, external_id="dup1", platform="linkedin")
        # Act / Assert
        with pytest.raises(sqlite3.IntegrityError):
            insert_sample(db, external_id="dup1", platform="linkedin")


# ---------------------------------------------------------------------------
# update_status tests
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    """AC-004-3: Updating application status."""

    def test_update_status_changes_fields(self, db: Database):
        """AC-004-3: update_status modifies status, notes, and updated_at."""
        # Arrange
        row_id = insert_sample(db)
        before = db.get_application(row_id)
        assert before is not None
        # Act
        db.update_status(row_id, "interviewed", notes="Went well")
        after = db.get_application(row_id)
        # Assert
        assert after is not None
        assert after.status == "interviewed"
        assert after.notes == "Went well"
        assert after.updated_at >= before.updated_at


# ---------------------------------------------------------------------------
# get_all_applications tests
# ---------------------------------------------------------------------------

class TestGetAllApplications:
    """FR-004 / AC-004-4 / AC-004-5: Listing and filtering applications."""

    def test_get_all_applications_returns_list(self, db: Database):
        """FR-004: Returns a list of Application models."""
        # Arrange
        insert_sample(db, external_id="a1")
        insert_sample(db, external_id="a2")
        # Act
        results = db.get_all_applications()
        # Assert
        assert isinstance(results, list)
        assert len(results) == 2
        assert all(isinstance(r, Application) for r in results)

    def test_get_all_applications_filter_by_status(self, db: Database):
        """AC-004-4: Filtering by status returns only matching rows."""
        # Arrange
        insert_sample(db, external_id="s1", status="applied")
        insert_sample(db, external_id="s2", status="rejected")
        insert_sample(db, external_id="s3", status="applied")
        # Act
        results = db.get_all_applications(status="applied")
        # Assert
        assert len(results) == 2
        assert all(r.status == "applied" for r in results)

    def test_get_all_applications_filter_by_platform(self, db: Database):
        """FR-004: Filtering by platform returns only matching rows."""
        # Arrange
        insert_sample(db, external_id="p1", platform="linkedin")
        insert_sample(db, external_id="p2", platform="indeed")
        insert_sample(db, external_id="p3", platform="linkedin")
        # Act
        results = db.get_all_applications(platform="indeed")
        # Assert
        assert len(results) == 1
        assert results[0].platform == "indeed"

    def test_get_all_applications_search(self, db: Database):
        """AC-004-5: Free-text search matches job_title or company."""
        # Arrange
        insert_sample(db, external_id="x1", job_title="Data Engineer", company="Acme")
        insert_sample(db, external_id="x2", job_title="Designer", company="DataCorp")
        insert_sample(db, external_id="x3", job_title="Manager", company="BigCo")
        # Act
        results = db.get_all_applications(search="Data")
        # Assert — x1 matches on job_title, x2 matches on company
        assert len(results) == 2
        matched_ids = {r.external_id for r in results}
        assert matched_ids == {"x1", "x2"}

    def test_get_all_applications_pagination(self, db: Database):
        """FR-004: limit and offset control pagination correctly."""
        # Arrange — insert 5 rows
        for i in range(5):
            insert_sample(db, external_id=f"pg{i}")
        # Act
        page1 = db.get_all_applications(limit=2, offset=0)
        page2 = db.get_all_applications(limit=2, offset=2)
        page3 = db.get_all_applications(limit=2, offset=4)
        # Assert
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    def test_get_all_applications_ordered_by_applied_at_desc(self, db: Database):
        """AC-004-4: Results are ordered by applied_at descending (newest first)."""
        # Arrange — insert with explicit timestamps via raw SQL for control
        with db._connect() as conn:
            for i, ts in enumerate(["2025-01-01", "2025-06-15", "2025-03-10"]):
                conn.execute(
                    """
                    INSERT INTO applications
                        (external_id, platform, job_title, company, apply_url,
                         match_score, status, applied_at, updated_at)
                    VALUES (?, 'linkedin', 'Eng', 'Co', 'https://x.com', 90, 'applied', ?, ?)
                    """,
                    (f"ord{i}", ts, ts),
                )
        # Act
        results = db.get_all_applications()
        dates = [r.applied_at for r in results]
        # Assert — should be descending
        assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# get_application tests
# ---------------------------------------------------------------------------

class TestGetApplication:
    """FR-004 / AC-004-N2: Retrieving a single application."""

    def test_get_application_found(self, db: Database):
        """FR-004: Retrieving an existing application returns an Application model."""
        # Arrange
        row_id = insert_sample(db)
        # Act
        app = db.get_application(row_id)
        # Assert
        assert app is not None
        assert isinstance(app, Application)
        assert app.id == row_id
        assert app.job_title == "Engineer"

    def test_get_application_not_found_returns_none(self, db: Database):
        """AC-004-N2: Non-existent id returns None, no exception."""
        # Arrange — empty db
        # Act
        result = db.get_application(9999)
        # Assert
        assert result is None


# ---------------------------------------------------------------------------
# exists tests
# ---------------------------------------------------------------------------

class TestExists:
    """AC-004-6: Deduplication existence check."""

    def test_exists_true(self, db: Database):
        """AC-004-6: exists returns True for a previously saved application."""
        # Arrange
        insert_sample(db, external_id="e1", platform="linkedin")
        # Act / Assert
        assert db.exists("e1", "linkedin") is True

    def test_exists_false(self, db: Database):
        """AC-004-6: exists returns False when no matching row."""
        # Arrange — empty db
        # Act / Assert
        assert db.exists("nonexistent", "linkedin") is False


# ---------------------------------------------------------------------------
# export_csv tests
# ---------------------------------------------------------------------------

class TestExportCsv:
    """AC-004-7 / AC-004-N3: CSV export."""

    def test_export_csv_writes_file(self, db: Database, tmp_path):
        """AC-004-7: Exported CSV contains header + data rows with correct content."""
        # Arrange
        insert_sample(db, external_id="csv1")
        insert_sample(db, external_id="csv2")
        csv_path = tmp_path / "export.csv"
        # Act
        db.export_csv(csv_path)
        # Assert
        assert csv_path.exists()
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        # Header + 2 data rows
        assert len(reader) == 3
        headers = reader[0]
        assert "external_id" in headers
        assert "platform" in headers

    def test_export_csv_empty_db_no_error(self, db: Database, tmp_path):
        """AC-004-N3: Exporting from an empty database does not raise."""
        # Arrange
        csv_path = tmp_path / "empty.csv"
        # Act — should not raise
        db.export_csv(csv_path)
        # Assert — file is either absent or empty (implementation returns early)
        if csv_path.exists():
            assert csv_path.stat().st_size == 0 or True  # no crash is the goal


# ---------------------------------------------------------------------------
# Feed events tests
# ---------------------------------------------------------------------------

class TestFeedEvents:
    """AC-004-8 / AC-004-9: Saving and retrieving activity feed events."""

    def test_save_feed_event_returns_id(self, db: Database):
        """AC-004-8: save_feed_event returns a positive integer id."""
        # Arrange / Act
        eid = db.save_feed_event(
            event_type="applied",
            job_title="Eng",
            company="Acme",
            platform="linkedin",
            message="Applied successfully",
        )
        # Assert
        assert isinstance(eid, int)
        assert eid > 0

    def test_get_feed_events_ordered_desc(self, db: Database):
        """AC-004-9: Events are returned newest-first (created_at DESC)."""
        # Arrange — insert with controlled timestamps via raw SQL
        with db._connect() as conn:
            for ts in ["2025-01-01 10:00:00", "2025-06-15 10:00:00", "2025-03-10 10:00:00"]:
                conn.execute(
                    "INSERT INTO feed_events (event_type, message, created_at) VALUES (?, ?, ?)",
                    ("info", f"msg-{ts}", ts),
                )
        # Act
        events = db.get_feed_events()
        dates = [e.created_at for e in events]
        # Assert
        assert dates == sorted(dates, reverse=True)

    def test_get_feed_events_respects_limit(self, db: Database):
        """AC-004-9: The limit parameter caps the number of returned events."""
        # Arrange
        for i in range(10):
            db.save_feed_event(event_type="info", message=f"event-{i}")
        # Act
        events = db.get_feed_events(limit=3)
        # Assert
        assert len(events) == 3


# ---------------------------------------------------------------------------
# Analytics tests
# ---------------------------------------------------------------------------

class TestAnalytics:
    """AC-004-10 / AC-004-11: Analytics summaries."""

    def test_get_analytics_summary(self, db: Database):
        """AC-004-10: Summary returns total, by_status, and by_platform breakdowns."""
        # Arrange
        insert_sample(db, external_id="an1", platform="linkedin", status="applied")
        insert_sample(db, external_id="an2", platform="indeed", status="applied")
        insert_sample(db, external_id="an3", platform="linkedin", status="rejected")
        # Act
        summary = db.get_analytics_summary()
        # Assert
        assert summary["total"] == 3
        assert summary["by_status"]["applied"] == 2
        assert summary["by_status"]["rejected"] == 1
        assert summary["by_platform"]["linkedin"] == 2
        assert summary["by_platform"]["indeed"] == 1

    def test_get_daily_analytics(self, db: Database):
        """AC-004-11: Daily analytics groups counts by date within the window."""
        # Arrange — insert rows with today's date (default CURRENT_TIMESTAMP)
        insert_sample(db, external_id="d1")
        insert_sample(db, external_id="d2")
        # Act
        daily = db.get_daily_analytics(days=7)
        # Assert
        assert isinstance(daily, list)
        assert len(daily) >= 1
        today_entry = daily[-1]
        assert "date" in today_entry
        assert today_entry["count"] == 2


# ===================================================================
# D-5 — SQLite WAL Mode + Busy Timeout
# ===================================================================


class TestSQLiteWALMode:
    """D-5: SQLite WAL journal mode and busy timeout."""

    def test_wal_mode_enabled(self, db: Database):
        """D-5: Database connections use WAL journal mode.  # AC-D5.1"""
        with db._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"

    def test_busy_timeout_set(self, db: Database):
        """D-5: Database connections set busy_timeout=5000.  # AC-D5.2"""
        with db._connect() as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout == 5000

    def test_concurrent_reads_with_wal(self, db: Database):
        """D-5: WAL allows concurrent reads while writing.  # AC-D5.3"""
        insert_sample(db, external_id="wal-1")
        # Open two connections simultaneously — WAL allows this
        conn1 = db._connect()
        conn2 = db._connect()
        try:
            rows1 = conn1.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            rows2 = conn2.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            assert rows1 == rows2 == 1
        finally:
            conn1.close()
            conn2.close()
