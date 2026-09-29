"""Tests for the Summer 2027 internship workflow: policy filter, workbook feed,
readiness gate, database-backed daily cap, and document-generation guards.

All personal data here is placeholder test data.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from openpyxl import Workbook

from bot.bot import _applied_today, run_bot
from bot.search.workbook import WorkbookSearcher
from bot.state import BotState
from config.settings import AppConfig, LLMConfig, SearchCriteria, UserProfile
from core.internship_policy import is_target_internship, missing_profile_fields
from db.database import Database


@dataclass
class _Job:
    title: str
    description: str = "Summer 2027 internship"


def _config(tmp_path, *, api_key="test-key", resume=True, **profile_overrides) -> AppConfig:
    resume_path = tmp_path / "resume.pdf"
    if resume:
        resume_path.write_bytes(b"%PDF-1.4 test")
    profile = dict(
        first_name="Test", last_name="User", email="test.user@example.com",
        phone="5125550100", city="Austin", state="TX", bio="",
        fallback_resume_path=str(resume_path),
        screening_answers={
            "major": "Test Major", "graduation_date": "May 2027",
            "work_authorization": "Yes", "visa_sponsorship": "No",
        },
    )
    profile.update(profile_overrides)
    return AppConfig(
        profile=UserProfile(**profile),
        search_criteria=SearchCriteria(job_titles=["Intern"], locations=["Austin, TX"]),
        llm=LLMConfig(provider="openai", api_key=api_key, model="gpt-4o-mini"),
    )


# ---------------------------------------------------------------------------
# Scope filter
# ---------------------------------------------------------------------------


class TestIsTargetInternship:
    @pytest.mark.parametrize("title", [
        "Health Systems Optimization Intern - Summer 2027",
        "Internship, FP&A & Business Operations (Summer 2027)",
        "Summer 2027 Intern - Sales Analyst",
        "2027 COO Global Operations Summer Internship - Early Careers",
        "Summer 2027 Product Management Intern",
        "Summer 2027 Technical Program Manager Intern",
    ])
    def test_in_scope(self, title):
        assert is_target_internship(_Job(title))

    @pytest.mark.parametrize("title", [
        "Software Engineer Intern, Summer 2027",
        "Strategy Manager",                      # not an internship
        "Summer 2026 Strategy Intern",           # wrong cycle
    ])
    def test_out_of_scope(self, title):
        assert not is_target_internship(_Job(title, description="Summer 2026"))

    def test_requires_summer_2027_somewhere(self):
        assert not is_target_internship(_Job("Strategy Intern", description="Fall 2027"))

    @pytest.mark.parametrize("title", [
        "Product Development Intern - Summer 2027",
        "Product Specialist Intern",
        "Social Media Marketing Intern (Summer 2027)",
        "Supply Chain Planning Intern",
        "Government Affairs & Public Policy Intern",
    ])
    def test_broader_role_families_in_scope(self, title):
        assert is_target_internship(_Job(title))

    def test_spring_2027_co_op_in_scope_but_not_spring_internship(self):
        assert is_target_internship(_Job("Supply Chain Co-op", description="Spring 2027 co-op"))
        assert not is_target_internship(_Job("Marketing Intern", description="Spring 2027"))

    @pytest.mark.parametrize("title", ["Growth Engineer Intern", "Data Scientist Intern",
                                       "Software Developer Intern"])
    def test_engineering_roles_excluded(self, title):
        assert not is_target_internship(_Job(title))

    def test_intern_list_leads_trust_the_chosen_category(self):
        lead = _Job("Summer Analyst")
        lead.platform = "intern_list"
        assert is_target_internship(lead)          # no "intern" or role term needed


# ---------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------


class TestMissingProfileFields:
    def test_complete_config_is_ready(self, tmp_path):
        assert missing_profile_fields(_config(tmp_path)) == []

    def test_missing_api_key_blocks(self, tmp_path):
        assert missing_profile_fields(_config(tmp_path, api_key="")) == [
            "OpenAI API key (OPENAI_API_KEY)"
        ]

    def test_missing_resume_file_blocks(self, tmp_path):
        assert "resume PDF file" in missing_profile_fields(_config(tmp_path, resume=False))

    def test_blank_profile_lists_everything(self, tmp_path):
        missing = missing_profile_fields(_config(
            tmp_path, first_name="", email="", fallback_resume_path=None,
            screening_answers={},
        ))
        for name in ("first name", "email", "resume file", "major",
                     "graduation month/year", "work authorization", "visa sponsorship"):
            assert name in missing


class TestRunBotGate:
    def test_refuses_to_start_without_api_key(self, tmp_path):
        with patch("bot.bot.BrowserManager") as browser:
            run_bot(BotState(), _config(tmp_path, api_key=""), MagicMock())
        browser.assert_not_called()

    def test_refuses_to_start_with_incomplete_profile(self, tmp_path):
        with patch("bot.bot.BrowserManager") as browser:
            run_bot(BotState(), _config(tmp_path, phone=""), MagicMock())
        browser.assert_not_called()


# ---------------------------------------------------------------------------
# Workbook feed
# ---------------------------------------------------------------------------

_HEADERS = [
    "Record ID", "Employer / Organization", "Program / Position", "City", "State",
    "Pay / Stipend", "Application Status", "Date Verified", "Application Deadline",
    "Official Posting / Application URL", "Recruiting Cycle", "Role / Function",
    "Field / Major", "Class Year Eligibility", "Major Eligibility",
    "Other Eligibility / Notes",
]


def _row(record_id, *, status="Open", verified=None, deadline=None,
         url="https://example.com/job/1", company="Acme", title="Strategy Intern - Summer 2027"):
    verified = verified or date.today().isoformat()
    return [record_id, company, title, "Austin", "TX", None, status, verified, deadline,
            url, "Summer 2027", "Strategy", None, None, None, None]


@pytest.fixture
def workbook_path(tmp_path, monkeypatch):
    def build(*rows):
        wb = Workbook()
        wb.active.title = "Verified Opportunities"
        wb.active.append(_HEADERS)
        for row in rows:
            wb.active.append(row)
        path = tmp_path / "jobs.xlsx"
        wb.save(path)
        monkeypatch.setenv("AUTOAPPLY_WORKBOOK", str(path))
        return path
    return build


class TestWorkbookSearcher:
    def _ids(self):
        return [job.external_id for job in WorkbookSearcher().search(None)]

    def test_yields_recent_open_roles_as_rawjobs(self, workbook_path):
        workbook_path(_row("R1"))
        job = next(iter(WorkbookSearcher().search(None)))
        assert (job.platform, job.company, job.location) == ("workbook", "Acme", "Austin, TX")
        assert job.apply_url == "https://example.com/job/1"

    def test_deadline_approaching_is_kept(self, workbook_path):
        workbook_path(_row("R1", status="Deadline Approaching"))
        assert self._ids() == ["R1"]

    def test_filters(self, workbook_path):
        today = date.today()
        workbook_path(
            _row("closed", status="Closed"),
            _row("stale", verified=(today - timedelta(days=8)).isoformat()),
            _row("expired", deadline=(today - timedelta(days=1)).isoformat()),
            _row("http", url="http://example.com/job"),
            _row("nourl", url=""),
            _row("", ),
            _row("ok", deadline=(today + timedelta(days=3)).isoformat()),
        )
        assert self._ids() == ["ok"]

    def test_missing_workbook_yields_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOAPPLY_WORKBOOK", str(tmp_path / "absent.xlsx"))
        assert self._ids() == []


# ---------------------------------------------------------------------------
# Daily cap
# ---------------------------------------------------------------------------


def _save(db, external_id, status="applied"):
    db.save_application(
        external_id=external_id, platform="workbook", job_title="Intern", company="Acme",
        location=None, salary=None, apply_url="https://example.com", match_score=70,
        resume_path=None, cover_letter_path=None, cover_letter_text=None,
        status=status, error_message=None,
    )


class TestDailyCap:
    def test_count_applied_today_counts_only_todays_submissions(self, tmp_path):
        db = Database(tmp_path / "t.db")
        _save(db, "a")
        _save(db, "b")
        _save(db, "c", status="manual_required")
        _save(db, "d", status="error")
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO applications (external_id, platform, job_title, company, "
                "apply_url, match_score, status, applied_at) VALUES "
                "('old', 'workbook', 'Intern', 'Acme', 'https://example.com', 70, "
                "'applied', datetime('now', '-2 days'))"
            )
        assert db.count_applied_today() == 2

    def test_db_count_is_authoritative_over_memory(self, tmp_path):
        db = Database(tmp_path / "t.db")
        state = BotState()
        state._applied_today = 99   # stale in-memory counter from a previous day
        assert _applied_today(state, db) == 0

    def test_falls_back_to_memory_without_a_real_db(self):
        state = BotState()
        state._applied_today = 3
        assert _applied_today(state, MagicMock()) == 3

    def test_restart_cannot_exceed_cap(self, tmp_path):
        """A fresh process (in-memory counter 0) still honours today's DB history."""
        db = Database(tmp_path / "t.db")
        for i in range(5):
            _save(db, f"job-{i}")

        state = BotState()
        state.start()
        calls = {"n": 0}

        cfg = MagicMock()
        cfg.bot.enabled_platforms = ["workbook"]
        cfg.bot.apply_mode = "full_auto"
        cfg.bot.max_applications_per_day = 5
        cfg.bot.delay_between_applications_seconds = 0
        cfg.bot.search_interval_seconds = 0

        job = MagicMock(title="Strategy Intern", description="Summer 2027",
                        company="Acme", platform="workbook", external_id="new-1",
                        apply_url="https://example.com/x", location="", salary=None)

        class _OneJob:
            def search(self, criteria, page=None):
                calls["n"] += 1
                if calls["n"] > 1:
                    state.stop()
                    return iter([])
                return iter([job])

        with patch("bot.bot.missing_profile_fields", return_value=[]), \
             patch("bot.bot.BrowserManager"), \
             patch("bot.bot.SEARCHERS", {"workbook": _OneJob}), \
             patch("bot.bot._apply_to_job") as apply_mock, \
             patch("bot.bot.score_job") as score_mock:
            run_bot(state, cfg, db)

        apply_mock.assert_not_called()
        score_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Environment-supplied API key
# ---------------------------------------------------------------------------


class TestEnvKeyHandling:
    def _write_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOAPPLY_DATA_DIR", str(tmp_path))
        cfg = _config(tmp_path, api_key="")
        (tmp_path / "config.json").write_text(json.dumps(cfg.model_dump()), encoding="utf-8")

    def test_load_config_reads_openai_key_from_environment(self, tmp_path, monkeypatch):
        from config import settings
        self._write_config(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "env-key-123")
        monkeypatch.setattr(settings, "_check_keyring", lambda: False)
        assert settings.load_config().llm.api_key == "env-key-123"

    def test_save_config_never_persists_an_environment_key(self, tmp_path, monkeypatch):
        from config import settings
        self._write_config(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "env-key-123")
        monkeypatch.setattr(settings, "_check_keyring", lambda: True)
        fake_keyring = MagicMock()
        fake_keyring.get_password.return_value = None   # nothing stored yet
        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            config = settings.load_config()
            assert config.llm.api_key == "env-key-123"
            settings.save_config(config)

        fake_keyring.set_password.assert_not_called()
        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == ""


# ---------------------------------------------------------------------------
# No fabricated resumes
# ---------------------------------------------------------------------------


class TestGenerateDocumentsWithoutExperience:
    def test_refuses_instead_of_letting_the_model_invent_a_background(self, tmp_path):
        from core.ai_engine import generate_documents

        job = MagicMock(id="j1")
        job.raw.company = "Acme"
        job.raw.description = "Summer 2027 strategy internship"
        with patch("core.ai_engine.invoke_llm") as llm:
            with pytest.raises(RuntimeError, match="No experience files"):
                generate_documents(
                    job=job, profile=MagicMock(),
                    experience_dir=tmp_path / "experiences",
                    output_dir_resumes=tmp_path / "resumes",
                    output_dir_cover_letters=tmp_path / "cover_letters",
                    llm_config=LLMConfig(provider="openai", api_key="k"),
                )
        llm.assert_not_called()
