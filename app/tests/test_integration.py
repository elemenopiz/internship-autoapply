"""Integration tests for AutoApply Phase 1.

These tests exercise components working together across boundaries:
API -> DB, API -> filesystem, wizard flow across multiple endpoints.

Requirement traceability:
    FR-006  Bot control state consistency
    FR-007  Application lifecycle (DB -> API -> export)
    FR-008  Experience file CRUD lifecycle (API -> filesystem)
    FR-009  Configuration partial update / merge
    FR-010  Analytics aggregation from application data
    FR-013  Setup wizard end-to-end flow
    FR-016  Profile file management
    NFR-001 API response time SLA
    NFR-007 Path traversal security across all endpoints
"""

from __future__ import annotations

import json
import time

import pytest

from db.database import Database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Full integration environment with Flask client, real DB, and real filesystem."""
    monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
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

    test_db = Database(tmp_path / "test.db")
    monkeypatch.setattr("app.db", test_db)
    monkeypatch.setattr("app_state.db", test_db)

    from app import app, bot_state

    app.config["TESTING"] = True

    # Reset bot state to ensure clean slate for each test
    bot_state.stop()

    return app.test_client(), test_db, tmp_path


# ===================================================================
# FR-013 — Wizard Workflow
# ===================================================================


class TestWizardWorkflow:
    """FR-013: Full setup wizard flow across multiple endpoints."""

    def test_complete_wizard_flow(self, env):
        """Validates FR-013 AC-013-9: Full wizard completion saves config and transitions to dashboard."""
        client, db, tmp_path = env

        # Remove the config so we start from a clean "first run" state
        config_path = tmp_path / "config.json"
        if config_path.exists():
            config_path.unlink()

        # Step 1: Verify first run
        r = client.get("/api/setup/status")
        assert r.status_code == 200
        assert r.get_json()["is_first_run"] is True

        # Step 2: Save config (wizard completion)
        config = {
            "profile": {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phone": "555-0100",
                "city": "San Francisco",
                "state": "CA",
                "bio": "Senior engineer with 10 years experience",
            },
            "search_criteria": {
                "job_titles": ["Software Engineer", "Backend Developer"],
                "locations": ["San Francisco", "Remote"],
                "remote_only": True,
                "salary_min": 150000,
                "keywords_include": ["Python", "Flask"],
                "keywords_exclude": ["PHP"],
                "experience_levels": ["senior"],
            },
            "bot": {
                "enabled_platforms": ["linkedin", "indeed"],
                "min_match_score": 80,
                "max_applications_per_day": 30,
            },
        }
        r = client.put(
            "/api/config",
            data=json.dumps(config),
            content_type="application/json",
        )
        assert r.status_code == 200

        # Step 3: Verify not first-run anymore
        r = client.get("/api/setup/status")
        assert r.get_json()["is_first_run"] is False

        # Step 4: Verify config is actually on filesystem
        config_path = tmp_path / "config.json"
        assert config_path.exists()
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["profile"]["first_name"] == "Jane"
        assert saved["search_criteria"]["job_titles"] == [
            "Software Engineer",
            "Backend Developer",
        ]

        # Step 5: Verify config loads back via API
        r = client.get("/api/config")
        loaded = r.get_json()
        assert loaded["profile"]["first_name"] == "Jane"
        assert loaded["search_criteria"]["salary_min"] == 150000


# ===================================================================
# FR-008, FR-016 — Profile File Workflow
# ===================================================================


class TestProfileFileWorkflow:
    """FR-008, FR-016: Full CRUD lifecycle for experience files via API and filesystem."""

    def test_experience_file_full_lifecycle(self, env):
        """Validates FR-008, FR-016: Complete CRUD lifecycle for experience files."""
        client, db, tmp_path = env

        # Create
        r = client.post(
            "/api/profile/experiences",
            data=json.dumps(
                {
                    "filename": "work.txt",
                    "content": "Worked at Acme for 5 years on backend systems.",
                }
            ),
            content_type="application/json",
        )
        assert r.status_code == 200

        # List and verify
        r = client.get("/api/profile/experiences")
        files = r.get_json()["files"]
        assert len(files) == 1
        assert files[0]["name"] == "work.txt"
        assert "Worked at Acme" in files[0]["content"]

        # Check file exists on disk
        assert (tmp_path / "profile" / "experiences" / "work.txt").exists()

        # Update
        r = client.put(
            "/api/profile/experiences/work.txt",
            data=json.dumps({"content": "Updated: Senior at Acme."}),
            content_type="application/json",
        )
        assert r.status_code == 200

        # Verify update persisted
        r = client.get("/api/profile/experiences")
        assert "Updated: Senior at Acme." in r.get_json()["files"][0]["content"]

        # Profile status reflects file
        r = client.get("/api/profile/status")
        status = r.get_json()
        assert status["file_count"] >= 1
        assert status["total_words"] > 0

        # Delete
        r = client.delete("/api/profile/experiences/work.txt")
        assert r.status_code == 200

        # Verify gone
        r = client.get("/api/profile/experiences")
        # Filter out README.txt if present
        user_files = [
            f for f in r.get_json()["files"] if f["name"] != "README.txt"
        ]
        assert len(user_files) == 0
        assert not (tmp_path / "profile" / "experiences" / "work.txt").exists()


# ===================================================================
# FR-007 — Application Workflow
# ===================================================================


class TestApplicationWorkflow:
    """FR-007: Application insert via DB, query/update via API, export."""

    def test_application_lifecycle(self, env):
        """Validates FR-007: Application insert, query, update, cover letter view, export."""
        client, db, tmp_path = env

        # Insert applications via DB (simulating bot)
        id1 = db.save_application(
            external_id="ln-001",
            platform="linkedin",
            job_title="Senior Engineer",
            company="Stripe",
            location="Remote",
            salary="200k",
            apply_url="https://linkedin.com/jobs/001",
            match_score=92,
            resume_path=None,
            cover_letter_path=None,
            cover_letter_text="Dear Stripe team, I am excited...",
            status="applied",
            error_message=None,
        )
        db.save_application(
            external_id="in-002",
            platform="indeed",
            job_title="Backend Dev",
            company="Airbnb",
            location="SF",
            salary="180k",
            apply_url="https://indeed.com/jobs/002",
            match_score=78,
            resume_path=None,
            cover_letter_path=None,
            cover_letter_text="Dear Airbnb, ...",
            status="applied",
            error_message=None,
        )

        # List all
        r = client.get("/api/applications")
        apps = r.get_json()
        assert len(apps) == 2

        # Filter by platform
        r = client.get("/api/applications?platform=linkedin")
        apps = r.get_json()
        assert len(apps) == 1
        assert apps[0]["company"] == "Stripe"

        # Update status
        r = client.patch(
            f"/api/applications/{id1}",
            data=json.dumps(
                {"status": "interview", "notes": "Phone screen scheduled"}
            ),
            content_type="application/json",
        )
        assert r.status_code == 200

        # Verify update via list
        r = client.get("/api/applications?status=interview")
        apps = r.get_json()
        assert len(apps) == 1
        assert apps[0]["notes"] == "Phone screen scheduled"

        # View cover letter
        r = client.get(f"/api/applications/{id1}/cover_letter")
        assert r.status_code == 200
        assert "Dear Stripe team" in r.get_json()["cover_letter_text"]

        # Export CSV
        r = client.get("/api/applications/export")
        assert r.status_code == 200
        assert "text/csv" in r.content_type
        csv_text = r.data.decode("utf-8")
        assert "Stripe" in csv_text
        assert "Airbnb" in csv_text


# ===================================================================
# FR-010 — Analytics Integration
# ===================================================================


class TestAnalyticsIntegration:
    """FR-010: Analytics correctly aggregate data inserted via DB."""

    def test_analytics_reflect_application_data(self, env):
        """Validates FR-010 AC-010-1, AC-010-2: Analytics correctly aggregate application data."""
        client, db, tmp_path = env

        # Insert varied data
        db.save_application(
            external_id="j1",
            platform="linkedin",
            job_title="Eng",
            company="A",
            location=None,
            salary=None,
            apply_url="https://x.com/1",
            match_score=90,
            resume_path=None,
            cover_letter_path=None,
            cover_letter_text=None,
            status="applied",
            error_message=None,
        )
        db.save_application(
            external_id="j2",
            platform="indeed",
            job_title="Eng",
            company="B",
            location=None,
            salary=None,
            apply_url="https://x.com/2",
            match_score=80,
            resume_path=None,
            cover_letter_path=None,
            cover_letter_text=None,
            status="interview",
            error_message=None,
        )
        db.save_application(
            external_id="j3",
            platform="linkedin",
            job_title="Eng",
            company="C",
            location=None,
            salary=None,
            apply_url="https://x.com/3",
            match_score=70,
            resume_path=None,
            cover_letter_path=None,
            cover_letter_text=None,
            status="applied",
            error_message=None,
        )

        # Summary
        r = client.get("/api/analytics/summary")
        data = r.get_json()
        assert data["total"] == 3
        assert data["by_status"]["applied"] == 2
        assert data["by_status"]["interview"] == 1
        assert data["by_platform"]["linkedin"] == 2
        assert data["by_platform"]["indeed"] == 1

        # Daily
        r = client.get("/api/analytics/daily?days=1")
        daily = r.get_json()
        assert len(daily) >= 1
        assert daily[0]["count"] == 3


# ===================================================================
# FR-006 — Bot State Integration
# ===================================================================


class TestBotStateIntegration:
    """FR-006: Bot control API maintains consistent state across transitions."""

    def test_bot_control_state_consistency(self, env):
        """Validates FR-006: Bot start/pause/stop/status maintain consistent state."""
        client, db, tmp_path = env

        # Initial state
        r = client.get("/api/bot/status")
        assert r.get_json()["status"] == "stopped"

        # Start
        r = client.post("/api/bot/start")
        assert r.get_json()["status"] == "running"
        r = client.get("/api/bot/status")
        assert r.get_json()["status"] == "running"
        assert r.get_json()["uptime_seconds"] >= 0

        # Pause
        r = client.post("/api/bot/pause")
        assert r.get_json()["status"] == "paused"
        r = client.get("/api/bot/status")
        assert r.get_json()["status"] == "paused"

        # Stop
        r = client.post("/api/bot/stop")
        assert r.get_json()["status"] == "stopped"
        r = client.get("/api/bot/status")
        s = r.get_json()
        assert s["status"] == "stopped"
        assert s["uptime_seconds"] == 0


# ===================================================================
# FR-009 — Config Partial Update
# ===================================================================


class TestConfigPartialUpdate:
    """FR-009: Configuration merge behaviour preserves existing keys."""

    def test_config_partial_update_preserves_existing(self, env):
        """Validates FR-009 AC-009-4: Partial config update merges, doesn't overwrite."""
        client, db, tmp_path = env

        # Save full config
        full = {
            "profile": {
                "first_name": "Jane",
                "last_name": "D",
                "email": "j@x.com",
                "phone": "555",
                "city": "NYC",
                "state": "NY",
                "bio": "Bio",
            },
            "search_criteria": {"job_titles": ["Eng"], "locations": ["NYC"]},
            "bot": {"watch_mode": False, "max_applications_per_day": 50},
        }
        client.put(
            "/api/config",
            data=json.dumps(full),
            content_type="application/json",
        )

        # Partial update - only change bot settings
        client.put(
            "/api/config",
            data=json.dumps({"bot": {"watch_mode": True}}),
            content_type="application/json",
        )

        # Verify profile is preserved, bot is merged
        r = client.get("/api/config")
        cfg = r.get_json()
        assert cfg["profile"]["first_name"] == "Jane"
        assert cfg["bot"]["watch_mode"] is True


# ===================================================================
# NFR-007 — Security Integration
# ===================================================================


class TestSecurityIntegration:
    """NFR-007: Path traversal rejected across all file endpoints."""

    def test_path_traversal_all_endpoints(self, env):
        """Validates NFR-007: All file endpoints reject path traversal."""
        client, db, tmp_path = env

        payloads = [
            "../../../etc/passwd",
            "..\\..\\secret.txt",
            "foo/bar.txt",
            "test.py",
        ]

        for payload in payloads:
            r = client.post(
                "/api/profile/experiences",
                data=json.dumps({"filename": payload, "content": "x"}),
                content_type="application/json",
            )
            assert r.status_code == 400, f"POST should reject '{payload}'"

            r = client.put(
                f"/api/profile/experiences/{payload}",
                data=json.dumps({"content": "x"}),
                content_type="application/json",
            )
            assert r.status_code in (
                400,
                404,
            ), f"PUT should reject '{payload}'"

            r = client.delete(f"/api/profile/experiences/{payload}")
            assert r.status_code in (
                400,
                404,
            ), f"DELETE should reject '{payload}'"


# ===================================================================
# NFR-001 — API Response Time
# ===================================================================


class TestApiResponseTime:
    """NFR-001: All read endpoints respond within 200ms."""

    def test_api_response_times_under_200ms(self, env):
        """Validates NFR-001: All API endpoints respond within 200ms."""
        client, db, tmp_path = env

        endpoints = [
            ("GET", "/api/setup/status"),
            ("GET", "/api/bot/status"),
            ("GET", "/api/applications"),
            ("GET", "/api/profile/experiences"),
            ("GET", "/api/profile/status"),
            ("GET", "/api/config"),
            ("GET", "/api/analytics/summary"),
            ("GET", "/api/analytics/daily"),
        ]

        for method, path in endpoints:
            start = time.perf_counter()
            r = client.get(path)
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert r.status_code == 200, f"{path} returned {r.status_code}"
            assert elapsed_ms < 200, (
                f"{path} took {elapsed_ms:.1f}ms (limit: 200ms)"
            )
