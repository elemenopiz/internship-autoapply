"""Comprehensive unit tests for app.py Flask routes.

Requirement traceability:
    FR-006  Bot control endpoints
    FR-007  Application management endpoints
    FR-008  Experience / profile file management
    FR-009  Configuration endpoints
    FR-010  Analytics endpoints
    FR-011  Setup status endpoint
    FR-018  AI provider detection
    NFR-007 Path traversal security
    NFR-009 Error handler JSON responses
"""

from __future__ import annotations

import json

import pytest

from db.database import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def insert_app(db: Database, **kwargs) -> int:
    """Insert a test application with sensible defaults.  Returns the new row id."""
    defaults = dict(
        external_id="job1",
        platform="linkedin",
        job_title="Engineer",
        company="Acme",
        location="NYC",
        salary="100k",
        apply_url="https://example.com",
        match_score=85,
        resume_path=None,
        cover_letter_path=None,
        cover_letter_text="Dear Hiring Manager...",
        status="applied",
        error_message=None,
    )
    defaults.update(kwargs)
    return db.save_application(**defaults)


def _make_config_payload() -> dict:
    """Return a minimal valid AppConfig payload."""
    return {
        "profile": {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "phone": "555-0100",
            "city": "NYC",
            "state": "NY",
            "bio": "A test user",
        },
        "search_criteria": {
            "job_titles": ["Engineer"],
            "locations": ["NYC"],
        },
        "bot": {},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Yield (test_client, db, tmp_path) with all paths redirected to tmp_path."""
    # Redirect data dir to tmp_path everywhere it is referenced
    monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.applications.get_data_dir", lambda: tmp_path)

    # Create required directory structure
    (tmp_path / "profile" / "experiences").mkdir(parents=True)

    # Write a minimal config so load_config() returns a valid AppConfig
    import json
    minimal_config = {
        "profile": {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
            "phone": "555-0100",
            "city": "Remote",
            "state": "",
            "bio": "Test bio",
        },
        "search_criteria": {
            "job_titles": ["Software Engineer"],
            "locations": ["Remote"],
        },
        "bot": {
            "enabled_platforms": ["linkedin"],
        },
    }
    (tmp_path / "config.json").write_text(json.dumps(minimal_config), encoding="utf-8")

    # Re-initialise database in the tmp location
    test_db = Database(tmp_path / "test.db")
    monkeypatch.setattr("app.db", test_db)
    monkeypatch.setattr("app_state.db", test_db)

    from app import app

    app.config["TESTING"] = True
    return app.test_client(), test_db, tmp_path


# ===================================================================
# FR-006 — Bot Control
# ===================================================================


class TestBotControl:
    """FR-006: Bot start / pause / stop / status."""

    def test_bot_start(self, app_client):
        """AC-006-1: POST /api/bot/start returns running status."""
        client, _db, _tmp = app_client
        resp = client.post("/api/bot/start")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "running"

    def test_bot_pause(self, app_client):
        """AC-006-2: POST /api/bot/pause returns paused status."""
        client, _db, _tmp = app_client
        client.post("/api/bot/start")
        resp = client.post("/api/bot/pause")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "paused"

    def test_bot_stop(self, app_client):
        """AC-006-3: POST /api/bot/stop returns stopped status."""
        client, _db, _tmp = app_client
        client.post("/api/bot/start")
        resp = client.post("/api/bot/stop")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "stopped"

    def test_bot_status_structure(self, app_client):
        """AC-006-4: GET /api/bot/status returns full status dict with expected keys."""
        client, _db, _tmp = app_client
        resp = client.get("/api/bot/status")
        assert resp.status_code == 200
        data = resp.get_json()
        expected_keys = {
            "status",
            "stop_flag",
            "jobs_found_today",
            "applied_today",
            "errors_today",
            "start_time",
            "uptime_seconds",
            "ai_available",
            "awaiting_review",
        }
        assert expected_keys.issubset(data.keys())


# ===================================================================
# FR-053 — Review Gate
# ===================================================================


class TestReviewGate:
    """FR-053: Review mode pause and decision endpoints."""

    def test_review_approve_no_pending(self, app_client):
        """AC-053-N1: Approve with no pending review returns 409."""
        client, _db, _tmp = app_client
        resp = client.post("/api/bot/review/approve")
        assert resp.status_code == 409

    def test_review_skip_no_pending(self, app_client):
        """AC-053-N2: Skip with no pending review returns 409."""
        client, _db, _tmp = app_client
        resp = client.post("/api/bot/review/skip")
        assert resp.status_code == 409

    def test_review_edit_no_pending(self, app_client):
        """AC-053-N3: Edit with no pending review returns 409."""
        client, _db, _tmp = app_client
        resp = client.post(
            "/api/bot/review/edit",
            json={"cover_letter": "edited"},
        )
        assert resp.status_code == 409

    def test_review_edit_missing_cover_letter(self, app_client, monkeypatch):
        """AC-053-N4: Edit without cover_letter field returns 400."""
        client, _db, _tmp = app_client
        from app import bot_state
        monkeypatch.setattr(bot_state, "_awaiting_review", True)
        resp = client.post("/api/bot/review/edit", json={})
        assert resp.status_code == 400

    def test_review_approve_pending(self, app_client, monkeypatch):
        """AC-053-1: Approve a pending review returns 200."""
        client, _db, _tmp = app_client
        from app import bot_state
        monkeypatch.setattr(bot_state, "_awaiting_review", True)
        resp = client.post("/api/bot/review/approve")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "approved"

    def test_review_skip_pending(self, app_client, monkeypatch):
        """AC-053-2: Skip a pending review returns 200."""
        client, _db, _tmp = app_client
        from app import bot_state
        monkeypatch.setattr(bot_state, "_awaiting_review", True)
        resp = client.post("/api/bot/review/skip")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "skipped"

    def test_review_edit_pending(self, app_client, monkeypatch):
        """AC-053-3: Edit a pending review with cover letter returns 200."""
        client, _db, _tmp = app_client
        from app import bot_state
        monkeypatch.setattr(bot_state, "_awaiting_review", True)
        resp = client.post(
            "/api/bot/review/edit",
            json={"cover_letter": "Updated cover letter text"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "edited"

    def test_review_manual_no_pending(self, app_client):
        """Manual submit with no pending review returns 409."""
        client, _db, _tmp = app_client
        resp = client.post("/api/bot/review/manual")
        assert resp.status_code == 409

    def test_review_manual_pending(self, app_client, monkeypatch):
        """Manual submit with pending review returns 200."""
        client, _db, _tmp = app_client
        from app import bot_state
        monkeypatch.setattr(bot_state, "_awaiting_review", True)
        resp = client.post("/api/bot/review/manual")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "manual"


# ===================================================================
# FR-007 — Applications
# ===================================================================


class TestApplications:
    """FR-007: Application listing, filtering, updating, export."""

    def test_get_applications_empty(self, app_client):
        """FR-007: Empty DB returns empty list."""
        client, _db, _tmp = app_client
        resp = client.get("/api/applications")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_get_applications_with_data(self, app_client):
        """AC-007-1: Returns applications after insert."""
        client, db, _tmp = app_client
        insert_app(db)
        resp = client.get("/api/applications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["job_title"] == "Engineer"

    def test_get_applications_filter_status(self, app_client):
        """AC-007-1: Filter by status query param."""
        client, db, _tmp = app_client
        insert_app(db, external_id="a1", status="applied")
        insert_app(db, external_id="a2", status="rejected")
        resp = client.get("/api/applications?status=rejected")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["status"] == "rejected"

    def test_update_application_status(self, app_client):
        """AC-007-2: PATCH updates status."""
        client, db, _tmp = app_client
        app_id = insert_app(db)
        resp = client.patch(
            f"/api/applications/{app_id}",
            data=json.dumps({"status": "interviewed"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_update_application_notes_only_preserves_status(self, app_client):
        """FR-007: PATCH with notes only preserves existing status."""
        client, db, _tmp = app_client
        app_id = insert_app(db, status="applied")
        resp = client.patch(
            f"/api/applications/{app_id}",
            data=json.dumps({"notes": "hello"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        updated = db.get_application(app_id)
        assert updated.status == "applied"
        assert updated.notes == "hello"

    def test_get_cover_letter(self, app_client):
        """AC-007-3: Retrieve cover letter text for an application."""
        client, db, _tmp = app_client
        app_id = insert_app(db, cover_letter_text="Custom letter")
        resp = client.get(f"/api/applications/{app_id}/cover_letter")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cover_letter_text"] == "Custom letter"

    def test_get_cover_letter_not_found(self, app_client):
        """AC-007-N1: Cover letter for non-existent application returns 404."""
        client, _db, _tmp = app_client
        resp = client.get("/api/applications/99999/cover_letter")
        assert resp.status_code == 404

    def test_get_resume_not_found(self, app_client):
        """AC-007-N2: Resume for non-existent application returns 404."""
        client, _db, _tmp = app_client
        resp = client.get("/api/applications/99999/resume")
        assert resp.status_code == 404

    def test_get_resume_no_file(self, app_client):
        """AC-007-N2: Application exists but has no resume file returns 404."""
        client, db, _tmp = app_client
        app_id = insert_app(db, resume_path=None)
        resp = client.get(f"/api/applications/{app_id}/resume")
        assert resp.status_code == 404

    def test_export_csv(self, app_client):
        """AC-007-5: Export CSV returns file download."""
        client, db, _tmp = app_client
        insert_app(db)
        resp = client.get("/api/applications/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        body = resp.data.decode("utf-8")
        # CSV should contain header and at least one data row
        lines = body.strip().splitlines()
        assert len(lines) >= 2

    def test_export_csv_empty(self, app_client):
        """AC-007-5: Export CSV with no data still succeeds."""
        client, _db, _tmp = app_client
        resp = client.get("/api/applications/export")
        assert resp.status_code == 200


# ===================================================================
# FR-008 — Experience Files
# ===================================================================


class TestExperienceFiles:
    """FR-008: CRUD for experience text files."""

    def test_list_experiences_empty(self, app_client):
        """FR-008: Empty experiences dir returns empty list."""
        client, _db, _tmp = app_client
        resp = client.get("/api/profile/experiences")
        assert resp.status_code == 200
        assert resp.get_json()["files"] == []

    def test_list_experiences_with_files(self, app_client):
        """AC-008-1: Lists .txt files from the experiences directory."""
        client, _db, tmp = app_client
        exp_dir = tmp / "profile" / "experiences"
        (exp_dir / "work.txt").write_text("My work experience", encoding="utf-8")
        resp = client.get("/api/profile/experiences")
        data = resp.get_json()
        assert len(data["files"]) == 1
        assert data["files"][0]["name"] == "work.txt"
        assert data["files"][0]["content"] == "My work experience"

    def test_create_experience(self, app_client):
        """AC-008-2: POST creates a new experience file."""
        client, _db, tmp = app_client
        resp = client.post(
            "/api/profile/experiences",
            data=json.dumps({"filename": "project.txt", "content": "Built a thing"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        file_path = tmp / "profile" / "experiences" / "project.txt"
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == "Built a thing"

    def test_update_experience(self, app_client):
        """AC-008-3: PUT updates an existing experience file."""
        client, _db, tmp = app_client
        exp_dir = tmp / "profile" / "experiences"
        (exp_dir / "work.txt").write_text("old content", encoding="utf-8")
        resp = client.put(
            "/api/profile/experiences/work.txt",
            data=json.dumps({"content": "new content"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert (exp_dir / "work.txt").read_text(encoding="utf-8") == "new content"

    def test_update_experience_not_found(self, app_client):
        """AC-008-N1: PUT on non-existent file returns 404."""
        client, _db, _tmp = app_client
        resp = client.put(
            "/api/profile/experiences/nope.txt",
            data=json.dumps({"content": "whatever"}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_delete_experience(self, app_client):
        """AC-008-4: DELETE removes the experience file."""
        client, _db, tmp = app_client
        exp_dir = tmp / "profile" / "experiences"
        (exp_dir / "old.txt").write_text("bye", encoding="utf-8")
        resp = client.delete("/api/profile/experiences/old.txt")
        assert resp.status_code == 200
        assert not (exp_dir / "old.txt").exists()

    def test_delete_experience_not_found(self, app_client):
        """AC-008-N2: DELETE on non-existent file returns 404."""
        client, _db, _tmp = app_client
        resp = client.delete("/api/profile/experiences/ghost.txt")
        assert resp.status_code == 404

    def test_profile_status(self, app_client):
        """AC-008-5: Profile status returns file count and word count."""
        client, _db, tmp = app_client
        exp_dir = tmp / "profile" / "experiences"
        (exp_dir / "a.txt").write_text("one two three", encoding="utf-8")
        (exp_dir / "b.txt").write_text("four five", encoding="utf-8")
        resp = client.get("/api/profile/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["file_count"] == 2
        assert data["total_words"] == 5
        assert "ai_available" in data


# ===================================================================
# NFR-007 — Path Traversal Security
# ===================================================================


class TestPathTraversalSecurity:
    """NFR-007: Filename validation prevents path traversal."""

    def test_create_experience_path_traversal_rejected(self, app_client):
        """NFR-007: ../etc/passwd style traversal rejected on create."""
        client, _db, _tmp = app_client
        resp = client.post(
            "/api/profile/experiences",
            data=json.dumps({"filename": "../etc/passwd", "content": "pwned"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_experience_backslash_traversal_rejected(self, app_client):
        """NFR-007: Backslash traversal rejected on create."""
        client, _db, _tmp = app_client
        resp = client.post(
            "/api/profile/experiences",
            data=json.dumps({"filename": "..\\secret.txt", "content": "pwned"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_experience_non_txt_rejected(self, app_client):
        """NFR-007: Non-.txt extension rejected."""
        client, _db, _tmp = app_client
        resp = client.post(
            "/api/profile/experiences",
            data=json.dumps({"filename": "exploit.py", "content": "import os"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_update_experience_path_traversal_rejected(self, app_client):
        """NFR-007: Traversal rejected on update."""
        client, _db, _tmp = app_client
        resp = client.put(
            "/api/profile/experiences/..secret.txt",
            data=json.dumps({"content": "pwned"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_delete_experience_path_traversal_rejected(self, app_client):
        """NFR-007: Traversal rejected on delete."""
        client, _db, _tmp = app_client
        resp = client.delete("/api/profile/experiences/../../../etc/passwd")
        # Flask may return 404 for the nested path OR 400 from validation
        assert resp.status_code in (400, 404)


# ===================================================================
# FR-009 — Configuration
# ===================================================================


class TestConfig:
    """FR-009: Configuration get / put / merge."""

    def test_get_config_empty(self, app_client):
        """AC-009-2: No config file yet returns empty dict."""
        client, _db, _tmp = app_client
        # Remove the config file created by the fixture
        config_path = _tmp / "config.json"
        if config_path.exists():
            config_path.unlink()
        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_put_config_full(self, app_client):
        """AC-009-3: PUT a full config succeeds."""
        client, _db, _tmp = app_client
        payload = _make_config_payload()
        resp = client.put(
            "/api/config",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_get_config_after_save(self, app_client):
        """AC-009-1: GET returns previously saved config."""
        client, _db, _tmp = app_client
        payload = _make_config_payload()
        client.put("/api/config", data=json.dumps(payload), content_type="application/json")
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["profile"]["first_name"] == "Test"
        assert data["profile"]["last_name"] == "User"
        assert data["search_criteria"]["job_titles"] == ["Engineer"]

    def test_put_config_partial_merge(self, app_client):
        """AC-009-4: PUT with partial data merges into existing config."""
        client, _db, _tmp = app_client
        # Save initial config
        payload = _make_config_payload()
        client.put("/api/config", data=json.dumps(payload), content_type="application/json")

        # Partial update — only change profile.first_name via nested merge
        client.put(
            "/api/config",
            data=json.dumps({"profile": {"first_name": "Updated"}}),
            content_type="application/json",
        )
        resp = client.get("/api/config")
        data = resp.get_json()
        # The merged profile should carry the updated name and original email
        assert data["profile"]["first_name"] == "Updated"
        assert data["profile"]["email"] == "test@example.com"


# ===================================================================
# FR-010 — Analytics
# ===================================================================


class TestAnalytics:
    """FR-010: Analytics summary and daily breakdown."""

    def test_analytics_summary_empty(self, app_client):
        """AC-010-1: Summary with no data returns zero total."""
        client, _db, _tmp = app_client
        resp = client.get("/api/analytics/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["by_status"] == {}
        assert data["by_platform"] == {}

    def test_analytics_summary_with_data(self, app_client):
        """AC-010-1: Summary reflects inserted applications."""
        client, db, _tmp = app_client
        insert_app(db, external_id="a1", status="applied", platform="linkedin")
        insert_app(db, external_id="a2", status="rejected", platform="indeed")
        resp = client.get("/api/analytics/summary")
        data = resp.get_json()
        assert data["total"] == 2
        assert data["by_status"]["applied"] == 1
        assert data["by_platform"]["indeed"] == 1

    def test_analytics_daily(self, app_client):
        """AC-010-2: Daily analytics returns list (may be empty)."""
        client, _db, _tmp = app_client
        resp = client.get("/api/analytics/daily")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_analytics_daily_with_data(self, app_client):
        """AC-010-2: Daily analytics includes today's applications."""
        client, db, _tmp = app_client
        insert_app(db, external_id="d1")
        resp = client.get("/api/analytics/daily?days=1")
        data = resp.get_json()
        assert isinstance(data, list)
        if data:  # Depending on timezone the row may land on today
            assert "date" in data[0]
            assert "count" in data[0]


# ===================================================================
# FR-051 — Feed Events
# ===================================================================


class TestFeedEvents:
    """FR-051: Feed event API returns recent activity."""

    def test_feed_empty(self, app_client):
        """No events returns empty list."""
        client, _db, _tmp = app_client
        resp = client.get("/api/feed")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_feed_returns_saved_events(self, app_client):
        """Saved feed events appear in API response."""
        client, db, _tmp = app_client
        db.save_feed_event(
            event_type="APPLIED",
            job_title="Software Engineer",
            company="Acme Corp",
            platform="linkedin",
            message="Applied to Software Engineer at Acme Corp",
        )
        db.save_feed_event(
            event_type="ERROR",
            message="Search cycle error: timeout",
        )
        resp = client.get("/api/feed?limit=10")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        types = {e["event_type"] for e in data}
        assert "APPLIED" in types
        assert "ERROR" in types
        applied = next(e for e in data if e["event_type"] == "APPLIED")
        assert applied["job_title"] == "Software Engineer"
        assert applied["company"] == "Acme Corp"

    def test_feed_limit_parameter(self, app_client):
        """Limit parameter caps results."""
        client, db, _tmp = app_client
        for i in range(5):
            db.save_feed_event(event_type="FOUND", job_title=f"Job {i}")
        resp = client.get("/api/feed?limit=3")
        data = resp.get_json()
        assert len(data) == 3


# ===================================================================
# FR-011 — Setup
# ===================================================================


class TestSetup:
    """FR-011: Setup status endpoint."""

    def test_setup_status_first_run(self, app_client):
        """AC-011-1: No config.json means is_first_run is True."""
        client, _db, tmp = app_client
        # Ensure no config file
        config_path = tmp / "config.json"
        if config_path.exists():
            config_path.unlink()
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_first_run"] is True
        assert "ai_available" in data

    def test_setup_status_after_config(self, app_client):
        """AC-011-2: After saving config, is_first_run is False."""
        client, _db, tmp = app_client
        # Save a config to mark setup as complete
        payload = _make_config_payload()
        client.put("/api/config", data=json.dumps(payload), content_type="application/json")
        resp = client.get("/api/setup/status")
        data = resp.get_json()
        assert data["is_first_run"] is False


# ===================================================================
# FR-018 — AI Provider Detection
# ===================================================================


class TestAIDetection:
    """FR-018: Check whether AI provider is configured."""

    def test_ai_available_true(self, app_client, monkeypatch):
        """AC-031-1: AI configured -> ai_available is True."""
        client, _db, _tmp = app_client
        monkeypatch.setattr("routes.bot.check_ai_available", lambda: True)
        resp = client.get("/api/bot/status")
        assert resp.get_json()["ai_available"] is True

    def test_ai_available_false(self, app_client, monkeypatch):
        """AC-031-2: AI not configured -> ai_available is False."""
        client, _db, _tmp = app_client
        monkeypatch.setattr("routes.bot.check_ai_available", lambda: False)
        resp = client.get("/api/bot/status")
        assert resp.get_json()["ai_available"] is False


# ===================================================================
# NFR-009 — Error Handlers
# ===================================================================


class TestErrorHandlers:
    """NFR-009: Custom error handlers return JSON."""

    def test_404_returns_json(self, app_client):
        """NFR-009: Non-existent route returns JSON 404."""
        client, _db, _tmp = app_client
        resp = client.get("/api/nonexistent-route-xyz")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_405_returns_json(self, app_client):
        """NFR-009: Wrong HTTP method returns JSON 405."""
        client, _db, _tmp = app_client
        resp = client.delete("/api/bot/start")
        assert resp.status_code == 405
        data = resp.get_json()
        assert "error" in data


# ===================================================================
# FR-025 — Health Endpoint
# ===================================================================

class TestHealth:
    """Validates FR-025: GET /api/health returns ok status."""

    def test_health_returns_ok(self, app_client):
        """AC-025-1: Health endpoint returns {"status": "ok"} with 200."""
        client, _db, _tmp = app_client
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_health_post_not_allowed(self, app_client):
        """FR-025 negative: POST to health endpoint returns 405."""
        client, _db, _tmp = app_client
        resp = client.post("/api/health")
        assert resp.status_code == 405


# ===================================================================
# FR-026 — Shutdown Endpoint
# ===================================================================

class TestShutdown:
    """Validates FR-026: POST /api/shutdown for graceful termination."""

    def test_shutdown_returns_shutting_down(self, app_client, monkeypatch):
        """AC-026-1: POST /api/shutdown from localhost returns shutting_down."""
        client, _db, _tmp = app_client
        # Prevent actual shutdown by monkeypatching os.kill
        monkeypatch.setattr("app.os.kill", lambda pid, sig: None)
        monkeypatch.setattr("routes.lifecycle.os.kill", lambda pid, sig: None)
        resp = client.post("/api/shutdown")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "shutting_down"

    def test_shutdown_get_not_allowed(self, app_client):
        """FR-026 negative: GET to shutdown endpoint returns 405."""
        client, _db, _tmp = app_client
        resp = client.get("/api/shutdown")
        assert resp.status_code == 405
