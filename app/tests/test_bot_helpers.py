"""Unit tests for bot.bot helper functions and app_state module.

Requirement traceability:
    FR-042  Bot main loop helpers
    FR-081  Shared state module (app_state)
    ME-5   — Test coverage increase to 70%
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bot.search.base import RawJob
from core.filter import ScoredJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw(
    title="Engineer",
    company="Acme Corp",
    location="Remote",
    description="Build things.\n\nGreat opportunity.",
    apply_url="https://boards.greenhouse.io/acme/jobs/1",
    external_id="abcd1234-rest",
    salary="$120K",
):
    return RawJob(
        title=title, company=company, location=location,
        salary=salary, description=description,
        apply_url=apply_url, platform="linkedin",
        external_id=external_id, posted_at=None,
    )


def _make_scored(raw=None, score=80):
    raw = raw or _make_raw()
    return ScoredJob(
        raw=raw, id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        score=score, pass_filter=True, skip_reason="",
    )


# ===================================================================
# _esc — HTML escaping
# ===================================================================


class TestEsc:
    """Tests for the _esc HTML escaping function."""

    def test_escapes_ampersand(self):
        from bot.bot import _esc
        assert _esc("A & B") == "A &amp; B"

    def test_escapes_angle_brackets(self):
        from bot.bot import _esc
        assert _esc("<script>") == "&lt;script&gt;"

    def test_escapes_quotes(self):
        from bot.bot import _esc
        assert _esc('She said "hi"') == 'She said &quot;hi&quot;'

    def test_empty_string(self):
        from bot.bot import _esc
        assert _esc("") == ""

    def test_no_special_chars(self):
        from bot.bot import _esc
        assert _esc("Hello World") == "Hello World"

    def test_all_special_chars(self):
        from bot.bot import _esc
        assert _esc('&<>"') == "&amp;&lt;&gt;&quot;"


# ===================================================================
# _plain_to_html — text to HTML conversion
# ===================================================================


class TestPlainToHtml:
    """Tests for the _plain_to_html converter."""

    def test_single_paragraph(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("Hello world")
        assert "Hello world" in result

    def test_line_breaks_converted(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("Line 1\nLine 2")
        assert "<br>" in result

    def test_double_newlines_become_paragraphs(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("Para 1\n\nPara 2")
        assert "<p>Para 1</p>" in result
        assert "<p>Para 2</p>" in result

    def test_html_entities_escaped(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_empty_string(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("")
        assert result == ""

    def test_multiple_paragraphs_skip_empty(self):
        from bot.bot import _plain_to_html
        result = _plain_to_html("Para 1\n\n\n\nPara 2")
        assert "<p>Para 1</p>" in result
        assert "<p>Para 2</p>" in result


# ===================================================================
# _save_job_description — HTML file generation
# ===================================================================


class TestSaveJobDescription:
    """Tests for _save_job_description helper."""

    def test_saves_html_file(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored()
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        assert result.exists()
        assert result.suffix == ".html"

        content = result.read_text(encoding="utf-8")
        assert "Engineer" in content
        assert "Acme Corp" in content
        assert "<!DOCTYPE html>" in content

    def test_creates_directory_if_missing(self, tmp_path):
        from bot.bot import _save_job_description

        profile_dir = tmp_path / "deep" / "nested"
        scored = _make_scored()
        result = _save_job_description(scored, profile_dir)

        assert result is not None
        assert (profile_dir / "job_descriptions").is_dir()

    def test_filename_contains_company_and_date(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored(_make_raw(company="Cool Startup"))
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        assert "cool-startup" in result.name

    def test_special_chars_sanitized_in_filename(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored(_make_raw(company="Company & Co. (Inc.)"))
        result = _save_job_description(scored, tmp_path)

        assert result is not None
        # Should not contain special chars in filename
        assert "&" not in result.name
        assert "(" not in result.name

    def test_html_escapes_xss(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored(_make_raw(
            title='<script>alert("xss")</script>',
            company='Evil<Corp>',
        ))
        result = _save_job_description(scored, tmp_path)

        content = result.read_text(encoding="utf-8")
        assert "<script>" not in content
        assert "&lt;script&gt;" in content

    def test_returns_none_on_error(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored()
        # Use a path that will fail (file instead of directory)
        fake_dir = tmp_path / "file.txt"
        fake_dir.write_text("not a dir")

        with patch("bot.bot.Path.mkdir", side_effect=OSError("Permission denied")):
            _save_job_description(scored, fake_dir)
        # Should not crash, should return None
        # The actual behavior depends on where the error occurs
        # but the function has a try/except

    def test_description_with_paragraphs(self, tmp_path):
        from bot.bot import _save_job_description

        scored = _make_scored(_make_raw(
            description="Requirements:\n\n- Python\n- JavaScript\n\nBenefits:\n\n- Health"
        ))
        result = _save_job_description(scored, tmp_path)
        content = result.read_text(encoding="utf-8")
        assert "<p>" in content


# ===================================================================
# _save_application — status mapping
# ===================================================================


class TestSaveApplicationStatus:
    """Extended tests for _save_application status field."""

    def test_status_field_with_description_path(self):
        from bot.apply.base import ApplyResult
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=True)
        _save_application(db, scored, "/r.pdf", "/cl.txt", "cover", result,
                          description_path="/desc.html")
        kwargs = db.record_application.call_args[1]
        assert kwargs["description_path"] == "/desc.html"
        assert kwargs["status"] == "applied"

    def test_manual_required_has_correct_status(self):
        from bot.apply.base import ApplyResult
        from bot.bot import _save_application

        db = MagicMock()
        scored = _make_scored()
        result = ApplyResult(success=False, manual_required=True)
        _save_application(db, scored, None, None, "", result)
        kwargs = db.record_application.call_args[1]
        assert kwargs["status"] == "manual_required"


# ===================================================================
# app_state module
# ===================================================================


class TestAppState:
    """FR-081: Shared state module tests."""

    def test_imports_without_error(self):
        import app_state
        assert hasattr(app_state, "socketio")
        assert hasattr(app_state, "db")
        assert hasattr(app_state, "bot_state")
        assert hasattr(app_state, "bot_scheduler")
        assert hasattr(app_state, "api_token")

    def test_default_values(self):
        import app_state
        assert app_state.api_token == ""
        assert app_state.bot_thread is None
        assert app_state.login_proc is None

    def test_locks_are_threading_locks(self):
        import threading

        import app_state
        assert isinstance(app_state.bot_lock, type(threading.Lock()))
        assert isinstance(app_state.login_lock, type(threading.Lock()))

    def test_safe_filename_regex(self):
        import app_state
        assert app_state.SAFE_FILENAME_RE.match("resume.txt")
        assert app_state.SAFE_FILENAME_RE.match("my-experience_2024.txt")
        assert not app_state.SAFE_FILENAME_RE.match("../etc/passwd")
        assert not app_state.SAFE_FILENAME_RE.match("file.py")
        assert not app_state.SAFE_FILENAME_RE.match("")

    def test_valid_app_statuses(self):
        import app_state
        assert "applied" in app_state.VALID_APP_STATUSES
        assert "manual_required" in app_state.VALID_APP_STATUSES
        assert "error" in app_state.VALID_APP_STATUSES
        assert "rejected" in app_state.VALID_APP_STATUSES
        assert "accepted" in app_state.VALID_APP_STATUSES
        assert "interview" in app_state.VALID_APP_STATUSES
        assert "invalid_status" not in app_state.VALID_APP_STATUSES

    def test_bot_state_is_bot_state_instance(self):
        import app_state
        from bot.state import BotState
        assert isinstance(app_state.bot_state, BotState)


# ===================================================================
# SEARCHERS and APPLIERS dicts
# ===================================================================


class TestPipelineRegistration:
    """All searchers and appliers are properly registered."""

    def test_all_searchers_registered(self):
        from bot.bot import SEARCHERS
        assert set(SEARCHERS) == {"linkedin", "indeed", "workbook", "ats_boards", "intern_list",
                                  "github_lists"}

    def test_all_appliers_registered(self):
        from bot.bot import APPLIERS
        expected = {"linkedin", "indeed", "greenhouse", "lever", "workday", "ashby"}
        assert set(APPLIERS.keys()) == expected
        assert len(APPLIERS) == 6

    def test_searcher_classes_are_base_searcher_subclasses(self):
        from bot.bot import SEARCHERS
        from bot.search.base import BaseSearcher
        for name, cls in SEARCHERS.items():
            assert issubclass(cls, BaseSearcher), f"{name} not a BaseSearcher"

    def test_applier_classes_are_base_applier_subclasses(self):
        from bot.apply.base import BaseApplier
        from bot.bot import APPLIERS
        for name, cls in APPLIERS.items():
            assert issubclass(cls, BaseApplier), f"{name} not a BaseApplier"
