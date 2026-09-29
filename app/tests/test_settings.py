"""Comprehensive unit tests for config.settings module.

Follows Arrange-Act-Assert pattern. Each test links to an SRS requirement
via inline comment. Filesystem tests use tmp_path via mock_data_dir fixture
so the real ~/.autoapply/ is never touched.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.settings import (
    AppConfig,
    BotConfig,
    SearchCriteria,
    UserProfile,
    get_data_dir,
    is_first_run,
    load_config,
    save_config,
)

# ─── Model Tests ────────────────────────────────────────────────────────────


class TestUserProfile:
    """Tests for the UserProfile Pydantic model."""

    def test_user_profile_valid_data(self, valid_profile_data):
        """Valid data produces a UserProfile with correct field values.  # FR-003"""
        # Arrange — data from fixture
        # Act
        profile = UserProfile(**valid_profile_data)
        # Assert
        assert profile.first_name == "Jane"
        assert profile.last_name == "Doe"
        assert profile.full_name == "Jane Doe"
        assert profile.email == "jane@example.com"
        assert profile.phone == "555-0100"
        assert profile.phone_full == "+1555-0100"
        assert profile.city == "New York"
        assert profile.state == "NY"
        assert profile.location == "New York, NY"
        assert profile.bio == "Experienced software engineer."
        assert profile.linkedin_url == "https://linkedin.com/in/janedoe"

    def test_user_profile_missing_required_raises_validation_error(self):
        """Omitting a required field raises ValidationError.  # AC-003-N2"""
        # Arrange — incomplete data (missing 'email' and others)
        data = {"first_name": "Jane", "last_name": "Doe"}
        # Act / Assert
        with pytest.raises(ValidationError):
            UserProfile(**data)

    def test_user_profile_optional_defaults(self, valid_profile_data):
        """Optional fields default to None / empty when not supplied.  # FR-003"""
        # Arrange — remove optional keys
        valid_profile_data.pop("linkedin_url", None)
        # Act
        profile = UserProfile(**valid_profile_data)
        # Assert
        assert profile.linkedin_url is None
        assert profile.portfolio_url is None
        assert profile.fallback_resume_path is None
        assert profile.screening_answers == {}


class TestSearchCriteria:
    """Tests for the SearchCriteria Pydantic model."""

    def test_search_criteria_valid_data(self, valid_search_criteria_data):
        """Valid data creates a SearchCriteria instance.  # FR-003"""
        # Act
        sc = SearchCriteria(**valid_search_criteria_data)
        # Assert
        assert sc.job_titles == ["Software Engineer"]
        assert sc.locations == ["Remote"]

    def test_search_criteria_defaults(self, valid_search_criteria_data):
        """Default values are applied for optional fields.  # FR-003"""
        # Act
        sc = SearchCriteria(**valid_search_criteria_data)
        # Assert
        assert sc.remote_only is False
        assert sc.salary_min is None
        assert sc.keywords_include == []
        assert sc.keywords_exclude == []
        assert sc.experience_levels == ["mid", "senior"]


class TestBotConfig:
    """Tests for the BotConfig Pydantic model."""

    def test_bot_config_defaults(self):
        """All BotConfig fields have sensible defaults.  # FR-003"""
        # Act
        bot = BotConfig()
        # Assert
        assert bot.enabled_platforms == ["linkedin", "indeed"]
        assert bot.min_match_score == 75
        assert bot.max_applications_per_day == 50
        assert bot.delay_between_applications_seconds == 45
        assert bot.search_interval_seconds == 1800
        assert bot.watch_mode is False
        assert bot.cover_letter_template == ""


class TestAppConfig:
    """Tests for the AppConfig Pydantic model."""

    def test_app_config_full_valid(self, valid_app_config_data):
        """A complete valid dict produces an AppConfig.  # FR-003"""
        # Act
        cfg = AppConfig(**valid_app_config_data)
        # Assert
        assert cfg.profile.full_name == "Jane Doe"
        assert cfg.search_criteria.job_titles == ["Software Engineer"]
        assert cfg.bot.min_match_score == 75
        assert cfg.company_blacklist == []
        assert cfg.version == "2.0"

    def test_app_config_missing_required_raises(self):
        """Missing required nested models raises ValidationError.  # AC-003-N2"""
        # Arrange — no profile or search_criteria
        # Act / Assert
        with pytest.raises(ValidationError):
            AppConfig()


# ─── Function Tests ─────────────────────────────────────────────────────────


class TestGetDataDir:
    """Tests for get_data_dir()."""

    def test_get_data_dir_returns_home_autoapply(self):
        """get_data_dir() returns ~/.autoapply.  # FR-003"""
        from pathlib import Path

        # Act
        result = get_data_dir()
        # Assert
        assert result == Path.home() / ".autoapply"


class TestSaveConfig:
    """Tests for save_config() — all use mock_data_dir for isolation."""

    def test_save_config_creates_file(self, mock_data_dir, valid_app_config_data):
        """save_config writes config.json inside the data dir.  # AC-003-1"""
        # Arrange
        cfg = AppConfig(**valid_app_config_data)
        # Act
        save_config(cfg)
        # Assert
        config_path = mock_data_dir / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["profile"]["first_name"] == "Jane"
        assert data["profile"]["last_name"] == "Doe"

    def test_save_config_creates_parent_dirs(self, tmp_path, monkeypatch):
        """save_config creates intermediate directories if missing.  # FR-003"""
        # Arrange — point to a nested non-existent directory
        nested = tmp_path / "deep" / "nested" / "dir"
        monkeypatch.setattr("config.settings.get_data_dir", lambda: nested)
        cfg = AppConfig(
            profile=UserProfile(
                first_name="A",
                last_name="B",
                email="a@b.com",
                phone="1",
                city="X",
                state="Y",
                bio="Z",
            ),
            search_criteria=SearchCriteria(job_titles=["Dev"], locations=["Remote"]),
            bot=BotConfig(),
        )
        # Act
        save_config(cfg)
        # Assert
        assert (nested / "config.json").exists()


class TestLoadConfig:
    """Tests for load_config() — all use mock_data_dir for isolation."""

    def test_load_config_returns_config(self, mock_data_dir, valid_app_config_data):
        """load_config returns an AppConfig when config.json exists.  # AC-003-2"""
        # Arrange — write valid config to disk
        cfg = AppConfig(**valid_app_config_data)
        save_config(cfg)
        # Act
        loaded = load_config()
        # Assert
        assert loaded is not None
        assert loaded.profile.email == "jane@example.com"
        assert loaded.search_criteria.job_titles == ["Software Engineer"]

    def test_load_config_no_file_returns_none(self, mock_data_dir):
        """load_config returns None when no config.json exists.  # AC-003-3"""
        # Act
        result = load_config()
        # Assert
        assert result is None

    def test_load_config_invalid_json_raises(self, mock_data_dir):
        """load_config raises on malformed JSON.  # AC-003-N1"""
        # Arrange — write invalid JSON
        config_path = mock_data_dir / "config.json"
        config_path.write_text("{not valid json", encoding="utf-8")
        # Act / Assert
        with pytest.raises(json.JSONDecodeError):
            load_config()

    def test_load_config_missing_fields_raises(self, mock_data_dir):
        """load_config raises ValidationError when required fields are absent.  # AC-003-N2"""
        # Arrange — valid JSON but missing required model fields
        config_path = mock_data_dir / "config.json"
        config_path.write_text('{"version": "2.0"}', encoding="utf-8")
        # Act / Assert
        with pytest.raises(ValidationError):
            load_config()


class TestIsFirstRun:
    """Tests for is_first_run()."""

    def test_is_first_run_true_when_no_config(self, mock_data_dir):
        """is_first_run returns True when config.json does not exist.  # AC-003-4"""
        # Act
        result = is_first_run()
        # Assert
        assert result is True

    def test_is_first_run_false_when_config_exists(
        self, mock_data_dir, valid_app_config_data
    ):
        """is_first_run returns False after save_config.  # AC-003-5"""
        # Arrange
        cfg = AppConfig(**valid_app_config_data)
        save_config(cfg)
        # Act
        result = is_first_run()
        # Assert
        assert result is False
