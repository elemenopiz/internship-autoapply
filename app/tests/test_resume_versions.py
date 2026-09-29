"""Unit and integration tests for resume versioning (FR-110 to FR-119)
and resume comparison/favorites (FR-120 to FR-125).

Tests: Database resume version CRUD, GET /api/resumes endpoints,
       resume metrics computation, generate_documents() version metadata,
       favorite toggle, and comparison API.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from db.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path):
    """Create a fresh database for each test."""
    return Database(tmp_path / "test.db")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """Create Flask test client with database."""
    monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.applications.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.resumes.get_data_dir", lambda: tmp_path)

    (tmp_path / "profile" / "experiences").mkdir(parents=True)
    (tmp_path / "profile" / "resumes").mkdir(parents=True)
    minimal_config = {
        "profile": {"first_name": "Test", "last_name": "User", "email": "t@e.com",
                     "phone": "555", "city": "Remote", "state": "", "bio": "Test"},
        "search_criteria": {"job_titles": ["Engineer"], "locations": ["Remote"]},
        "bot": {"enabled_platforms": ["linkedin"]},
    }
    (tmp_path / "config.json").write_text(json.dumps(minimal_config), encoding="utf-8")

    test_db = Database(tmp_path / "test.db")
    monkeypatch.setattr("app.db", test_db)
    monkeypatch.setattr("app_state.db", test_db)

    from app import app
    app.config["TESTING"] = True
    yield app.test_client()


def _insert_app(db, **overrides):
    """Helper to insert an application, returns the id."""
    defaults = {
        "external_id": f"ext-{id(overrides)}",
        "platform": "linkedin",
        "job_title": "Software Engineer",
        "company": "TestCorp",
        "location": "Remote",
        "salary": None,
        "apply_url": "https://example.com/apply",
        "match_score": 80,
        "resume_path": "/tmp/resume.pdf",
        "cover_letter_path": "/tmp/cl.txt",
        "cover_letter_text": "Dear Hiring Manager...",
        "status": "applied",
        "error_message": None,
    }
    defaults.update(overrides)
    return db.save_application(**defaults)


def _insert_version(db, app_id, **overrides):
    """Helper to insert a resume version, returns the id."""
    defaults = {
        "application_id": app_id,
        "job_title": "Software Engineer",
        "company": "TestCorp",
        "resume_md_path": "/tmp/resume.md",
        "resume_pdf_path": "/tmp/resume.pdf",
        "match_score": 80,
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)
    return db.save_resume_version(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# FR-110: Resume Version Storage
# ═══════════════════════════════════════════════════════════════════════


class TestResumeVersionStorage:
    """FR-110: Save and retrieve resume version records."""

    def test_save_resume_version(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id)
        assert rv_id is not None
        assert rv_id > 0

    def test_save_multiple_versions_same_app(self, db):
        app_id = _insert_app(db)
        rv1 = _insert_version(db, app_id, llm_provider="anthropic")
        rv2 = _insert_version(db, app_id, llm_provider="openai")
        assert rv1 != rv2

    def test_save_version_nullable_fields(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id, match_score=None, llm_provider=None, llm_model=None)
        assert rv_id > 0

    def test_retrieve_version_by_id(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id, company="Google", job_title="SRE")
        result = db.get_resume_version(rv_id)
        assert result is not None
        assert result["company"] == "Google"
        assert result["job_title"] == "SRE"
        assert result["application_id"] == app_id

    def test_retrieve_nonexistent_version(self, db):
        assert db.get_resume_version(9999) is None


# ═══════════════════════════════════════════════════════════════════════
# FR-111: Resume Library API (list)
# ═══════════════════════════════════════════════════════════════════════


class TestResumeVersionsList:
    """FR-111: Paginated list of resume versions."""

    def test_empty_list(self, db):
        items, total = db.get_resume_versions()
        assert items == []
        assert total == 0

    def test_list_with_versions(self, db):
        app1 = _insert_app(db, external_id="e1")
        app2 = _insert_app(db, external_id="e2")
        _insert_version(db, app1, company="Alpha")
        _insert_version(db, app2, company="Beta")
        items, total = db.get_resume_versions()
        assert total == 2
        assert len(items) == 2

    def test_pagination(self, db):
        for i in range(5):
            app_id = _insert_app(db, external_id=f"e{i}")
            _insert_version(db, app_id, company=f"Company{i}")
        items, total = db.get_resume_versions(page=1, per_page=2)
        assert total == 5
        assert len(items) == 2
        items2, _ = db.get_resume_versions(page=3, per_page=2)
        assert len(items2) == 1

    def test_search_filter(self, db):
        app1 = _insert_app(db, external_id="e1")
        app2 = _insert_app(db, external_id="e2")
        _insert_version(db, app1, company="Google")
        _insert_version(db, app2, company="Amazon")
        items, total = db.get_resume_versions(search="google")
        assert total == 1
        assert items[0]["company"] == "Google"

    def test_sort_by_company(self, db):
        app1 = _insert_app(db, external_id="e1")
        app2 = _insert_app(db, external_id="e2")
        _insert_version(db, app1, company="Zebra")
        _insert_version(db, app2, company="Alpha")
        items, _ = db.get_resume_versions(sort="company", order="asc")
        assert items[0]["company"] == "Alpha"
        assert items[1]["company"] == "Zebra"

    def test_invalid_sort_column_defaults(self, db):
        app_id = _insert_app(db)
        _insert_version(db, app_id)
        items, _ = db.get_resume_versions(sort="invalid_column")
        assert len(items) == 1  # Should not error

    def test_invalid_page_defaults_to_1(self, db):
        app_id = _insert_app(db)
        _insert_version(db, app_id)
        items, _ = db.get_resume_versions(page=-1)
        assert len(items) == 1

    def test_application_status_included(self, db):
        app_id = _insert_app(db, status="interview")
        _insert_version(db, app_id)
        items, _ = db.get_resume_versions()
        assert items[0]["application_status"] == "interview"


# ═══════════════════════════════════════════════════════════════════════
# FR-114: Resume Effectiveness Metrics
# ═══════════════════════════════════════════════════════════════════════


class TestResumeMetrics:
    """FR-114: Aggregate resume effectiveness metrics."""

    def test_empty_metrics(self, db):
        metrics = db.get_resume_metrics()
        assert metrics["total_versions"] == 0
        assert metrics["tailored_interview_rate"] == 0.0
        assert metrics["fallback_interview_rate"] == 0.0
        assert metrics["by_provider"] == []

    def test_tailored_interview_rate(self, db):
        app1 = _insert_app(db, external_id="e1", status="interview")
        app2 = _insert_app(db, external_id="e2", status="rejected")
        app3 = _insert_app(db, external_id="e3", status="applied")
        _insert_version(db, app1)
        _insert_version(db, app2)
        _insert_version(db, app3)
        metrics = db.get_resume_metrics()
        assert metrics["total_versions"] == 3
        # 1 out of 3 is interview = 33.3%
        assert metrics["tailored_interview_rate"] == 33.3

    def test_fallback_rate(self, db):
        # Apps with resume_path but no resume_version = fallback
        _insert_app(db, external_id="e1", status="interview",
                     resume_path="/tmp/fallback.pdf")
        _insert_app(db, external_id="e2", status="rejected",
                     resume_path="/tmp/fallback2.pdf")
        metrics = db.get_resume_metrics()
        assert metrics["fallback_interview_rate"] == 50.0

    def test_offer_counts_as_positive(self, db):
        app1 = _insert_app(db, external_id="e1", status="offer")
        app2 = _insert_app(db, external_id="e2", status="accepted")
        _insert_version(db, app1)
        _insert_version(db, app2)
        metrics = db.get_resume_metrics()
        assert metrics["tailored_interview_rate"] == 100.0

    def test_avg_scores(self, db):
        app1 = _insert_app(db, external_id="e1", status="interview")
        app2 = _insert_app(db, external_id="e2", status="rejected")
        _insert_version(db, app1, match_score=90)
        _insert_version(db, app2, match_score=50)
        metrics = db.get_resume_metrics()
        assert metrics["avg_score_interviewed"] == 90.0
        assert metrics["avg_score_rejected"] == 50.0

    def test_by_provider(self, db):
        app1 = _insert_app(db, external_id="e1", status="interview")
        app2 = _insert_app(db, external_id="e2", status="rejected")
        _insert_version(db, app1, llm_provider="anthropic")
        _insert_version(db, app2, llm_provider="openai")
        metrics = db.get_resume_metrics()
        providers = {p["provider"]: p for p in metrics["by_provider"]}
        assert "anthropic" in providers
        assert "openai" in providers
        assert providers["anthropic"]["interview_rate"] == 100.0
        assert providers["openai"]["interview_rate"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# API Integration Tests
# ═══════════════════════════════════════════════════════════════════════


class TestResumeListAPI:
    """FR-111: GET /api/resumes integration."""

    def test_empty_list(self, app_client):
        resp = app_client.get("/api/resumes")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["total_count"] == 0

    def test_list_with_data(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app_id = _insert_app(db, external_id="api-1")
        _insert_version(db, app_id, company="APITestCo")
        resp = app_client.get("/api/resumes")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_count"] == 1
        assert data["items"][0]["company"] == "APITestCo"

    def test_search_param(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app1 = _insert_app(db, external_id="api-s1")
        app2 = _insert_app(db, external_id="api-s2")
        _insert_version(db, app1, company="Google")
        _insert_version(db, app2, company="Amazon")
        resp = app_client.get("/api/resumes?search=google")
        data = resp.get_json()
        assert data["total_count"] == 1

    def test_pagination_params(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        for i in range(3):
            app_id = _insert_app(db, external_id=f"api-p{i}")
            _insert_version(db, app_id)
        resp = app_client.get("/api/resumes?page=1&per_page=2")
        data = resp.get_json()
        assert len(data["items"]) == 2
        assert data["total_count"] == 3


class TestResumeDetailAPI:
    """FR-112: GET /api/resumes/<id> integration."""

    def test_not_found(self, app_client):
        resp = app_client.get("/api/resumes/9999")
        assert resp.status_code == 404

    def test_detail_with_md_file(self, app_client, tmp_path):
        import app_state
        db = app_state.db

        # Create resume files inside allowed data dir
        resumes_dir = tmp_path / "profile" / "resumes"
        md_path = resumes_dir / "test.md"
        pdf_path = resumes_dir / "test.pdf"
        md_path.write_text("# Test Resume\nContent here", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        app_id = _insert_app(db, external_id="detail-1")
        rv_id = _insert_version(db, app_id,
                                 resume_md_path=str(md_path),
                                 resume_pdf_path=str(pdf_path))
        resp = app_client.get(f"/api/resumes/{rv_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["resume_md_content"] is not None
        assert "Test Resume" in data["resume_md_content"]
        assert data["file_missing"] is False

    def test_detail_missing_file(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app_id = _insert_app(db, external_id="detail-2")
        rv_id = _insert_version(db, app_id,
                                 resume_md_path="/nonexistent/path.md",
                                 resume_pdf_path="/nonexistent/path.pdf")
        resp = app_client.get(f"/api/resumes/{rv_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["resume_md_content"] is None
        assert data["file_missing"] is True


class TestResumePdfAPI:
    """FR-113: GET /api/resumes/<id>/pdf integration."""

    def test_pdf_not_found_record(self, app_client):
        resp = app_client.get("/api/resumes/9999/pdf")
        assert resp.status_code == 404

    def test_pdf_file_missing(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app_id = _insert_app(db, external_id="pdf-1")
        rv_id = _insert_version(db, app_id, resume_pdf_path="/nonexistent/resume.pdf")
        resp = app_client.get(f"/api/resumes/{rv_id}/pdf")
        assert resp.status_code == 404

    def test_pdf_serve_inline(self, app_client, tmp_path):
        import app_state
        db = app_state.db

        resumes_dir = tmp_path / "profile" / "resumes"
        pdf_path = resumes_dir / "serve_test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test content")

        app_id = _insert_app(db, external_id="pdf-2")
        rv_id = _insert_version(db, app_id, resume_pdf_path=str(pdf_path))
        resp = app_client.get(f"/api/resumes/{rv_id}/pdf")
        assert resp.status_code == 200
        assert resp.content_type == "application/pdf"

    def test_pdf_serve_download(self, app_client, tmp_path):
        import app_state
        db = app_state.db

        resumes_dir = tmp_path / "profile" / "resumes"
        pdf_path = resumes_dir / "dl_test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 download content")

        app_id = _insert_app(db, external_id="pdf-3")
        rv_id = _insert_version(db, app_id, resume_pdf_path=str(pdf_path))
        resp = app_client.get(f"/api/resumes/{rv_id}/pdf?download=true")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_path_traversal_blocked(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        # Create a file outside data dir
        outside_file = tmp_path.parent / "secret.pdf"
        outside_file.write_bytes(b"%PDF-secret")

        app_id = _insert_app(db, external_id="pdf-trav")
        rv_id = _insert_version(db, app_id,
                                 resume_pdf_path=str(outside_file))
        resp = app_client.get(f"/api/resumes/{rv_id}/pdf")
        assert resp.status_code == 404  # path traversal → not safe → 404


class TestResumeMetricsAPI:
    """FR-114: GET /api/resumes/metrics integration."""

    def test_empty_metrics(self, app_client):
        resp = app_client.get("/api/resumes/metrics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_versions"] == 0

    def test_metrics_with_data(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app1 = _insert_app(db, external_id="m1", status="interview")
        app2 = _insert_app(db, external_id="m2", status="rejected")
        _insert_version(db, app1, llm_provider="anthropic")
        _insert_version(db, app2, llm_provider="anthropic")
        resp = app_client.get("/api/resumes/metrics")
        data = resp.get_json()
        assert data["total_versions"] == 2
        assert data["tailored_interview_rate"] == 50.0


# ═══════════════════════════════════════════════════════════════════════
# FR-118: Version metadata from generate_documents
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateDocumentsVersionMeta:
    """FR-118: generate_documents returns version metadata."""

    def test_returns_version_meta(self, tmp_path):
        from unittest.mock import MagicMock

        # Mock the LLM and renderer
        with patch("core.ai_engine.invoke_llm", return_value="# Resume\nContent"), \
             patch("core.resume_renderer.render_resume_to_pdf"):
            from core.ai_engine import generate_documents

            job = MagicMock()
            job.id = "test-123"
            job.raw.company = "TestCo"
            job.raw.description = "A test job"

            profile = MagicMock()
            profile.full_name = "Test User"
            profile.email = "test@example.com"
            profile.phone_full = "555-0100"
            profile.location = "Remote"
            profile.linkedin_url = None
            profile.portfolio_url = None
            profile.bio = "Test bio"

            llm_config = MagicMock()
            llm_config.provider = "anthropic"
            llm_config.model = "claude-sonnet-4-20250514"

            exp_dir = tmp_path / "experiences"
            exp_dir.mkdir()
            (exp_dir / "experience.txt").write_text("Placeholder experience.", encoding="utf-8")
            resume_dir = tmp_path / "resumes"
            cl_dir = tmp_path / "cover_letters"

            resume_path, cl_path, version_meta = generate_documents(
                job, profile, exp_dir, resume_dir, cl_dir, llm_config
            )

            assert version_meta is not None
            assert version_meta["llm_provider"] == "anthropic"
            assert version_meta["llm_model"] == "claude-sonnet-4-20250514"
            assert "resume_md_path" in version_meta
            assert "resume_pdf_path" in version_meta

    def test_returns_none_provider_without_config(self, tmp_path):
        with patch("core.ai_engine.invoke_llm", return_value="# Resume\nContent"), \
             patch("core.resume_renderer.render_resume_to_pdf"):
            from unittest.mock import MagicMock

            from core.ai_engine import generate_documents

            job = MagicMock()
            job.id = "test-456"
            job.raw.company = "NoCo"
            job.raw.description = "A test"

            profile = MagicMock()
            profile.full_name = "Test"
            profile.email = "t@e.com"
            profile.phone_full = "555"
            profile.location = "Remote"
            profile.linkedin_url = None
            profile.portfolio_url = None
            profile.bio = "Bio"

            exp_dir = tmp_path / "experiences"
            exp_dir.mkdir()
            (exp_dir / "experience.txt").write_text("Placeholder experience.", encoding="utf-8")

            _, _, version_meta = generate_documents(
                job, profile, exp_dir, tmp_path / "r", tmp_path / "c", None
            )
            assert version_meta["llm_provider"] is None
            assert version_meta["llm_model"] is None


# ═══════════════════════════════════════════════════════════════════════
# FR-110/NFR-018-06: Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """NFR-018-06: Existing apps without resume versions work fine."""

    def test_existing_app_without_version(self, db):
        """Apps created before resume versioning have no version records."""
        _insert_app(db)
        items, total = db.get_resume_versions()
        assert total == 0
        assert items == []

    def test_metrics_with_fallback_only(self, db):
        """Metrics work when only fallback resumes exist (no versions)."""
        _insert_app(db, external_id="e1", status="interview",
                     resume_path="/tmp/fallback.pdf")
        metrics = db.get_resume_metrics()
        assert metrics["total_versions"] == 0
        assert metrics["fallback_interview_rate"] == 100.0


# ═══════════════════════════════════════════════════════════════════════
# FR-120: Favorite Toggle
# ═══════════════════════════════════════════════════════════════════════


class TestFavoriteToggleDB:
    """FR-120: Toggle is_favorite on resume versions (DB layer)."""

    def test_default_not_favorite(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id)
        result = db.get_resume_version(rv_id)
        assert result["is_favorite"] == 0

    def test_toggle_on(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id)
        result = db.toggle_favorite(rv_id)
        assert result is True
        row = db.get_resume_version(rv_id)
        assert row["is_favorite"] == 1

    def test_toggle_off(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id)
        db.toggle_favorite(rv_id)  # on
        result = db.toggle_favorite(rv_id)  # off
        assert result is False
        row = db.get_resume_version(rv_id)
        assert row["is_favorite"] == 0

    def test_toggle_nonexistent(self, db):
        assert db.toggle_favorite(9999) is None

    def test_sort_by_favorites(self, db):
        app1 = _insert_app(db, external_id="e1")
        app2 = _insert_app(db, external_id="e2")
        _insert_version(db, app1, company="NotFav")
        rv2 = _insert_version(db, app2, company="IsFav")
        db.toggle_favorite(rv2)  # mark as favorite
        items, _ = db.get_resume_versions(sort="is_favorite", order="desc")
        assert items[0]["company"] == "IsFav"
        assert items[0]["is_favorite"] == 1

    def test_is_favorite_in_list(self, db):
        app_id = _insert_app(db)
        rv_id = _insert_version(db, app_id)
        db.toggle_favorite(rv_id)
        items, _ = db.get_resume_versions()
        assert items[0]["is_favorite"] == 1


class TestFavoriteToggleAPI:
    """FR-120: PUT /api/resumes/<id>/favorite integration."""

    def test_toggle_favorite_on(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app_id = _insert_app(db, external_id="fav-1")
        rv_id = _insert_version(db, app_id)
        resp = app_client.put(f"/api/resumes/{rv_id}/favorite")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == rv_id
        assert data["is_favorite"] is True

    def test_toggle_favorite_off(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app_id = _insert_app(db, external_id="fav-2")
        rv_id = _insert_version(db, app_id)
        app_client.put(f"/api/resumes/{rv_id}/favorite")  # on
        resp = app_client.put(f"/api/resumes/{rv_id}/favorite")  # off
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_favorite"] is False

    def test_toggle_nonexistent(self, app_client):
        resp = app_client.put("/api/resumes/9999/favorite")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# FR-123: Resume Comparison API
# ═══════════════════════════════════════════════════════════════════════


class TestComparisonAPI:
    """FR-123: GET /api/resumes/compare?left=X&right=Y."""

    def test_compare_success(self, app_client, tmp_path):
        import app_state
        db = app_state.db

        resumes_dir = tmp_path / "profile" / "resumes"
        md1 = resumes_dir / "left.md"
        md2 = resumes_dir / "right.md"
        md1.write_text("# Resume A\nLine 1", encoding="utf-8")
        md2.write_text("# Resume B\nLine 2", encoding="utf-8")

        app1 = _insert_app(db, external_id="cmp-1")
        app2 = _insert_app(db, external_id="cmp-2")
        rv1 = _insert_version(db, app1, company="Left", resume_md_path=str(md1))
        rv2 = _insert_version(db, app2, company="Right", resume_md_path=str(md2))

        resp = app_client.get(f"/api/resumes/compare?left={rv1}&right={rv2}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["left"]["company"] == "Left"
        assert data["right"]["company"] == "Right"
        assert data["left"]["resume_md_content"] is not None
        assert data["right"]["resume_md_content"] is not None

    def test_compare_missing_params(self, app_client):
        resp = app_client.get("/api/resumes/compare")
        assert resp.status_code == 400

    def test_compare_left_only(self, app_client):
        resp = app_client.get("/api/resumes/compare?left=1")
        assert resp.status_code == 400

    def test_compare_not_found(self, app_client):
        resp = app_client.get("/api/resumes/compare?left=9999&right=9998")
        assert resp.status_code == 404

    def test_compare_file_missing(self, app_client, tmp_path):
        import app_state
        db = app_state.db
        app1 = _insert_app(db, external_id="cmp-3")
        app2 = _insert_app(db, external_id="cmp-4")
        rv1 = _insert_version(db, app1, company="A",
                               resume_md_path="/nonexistent/a.md")
        rv2 = _insert_version(db, app2, company="B",
                               resume_md_path="/nonexistent/b.md")
        resp = app_client.get(f"/api/resumes/compare?left={rv1}&right={rv2}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["left"]["resume_md_content"] is None
        assert data["left"]["file_missing"] is True
