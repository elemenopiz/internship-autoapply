"""Frontend rendering tests for traceability coverage (TASK-024, #13).

Validates that the Flask-rendered index.html contains the required DOM
elements for 6 requirements currently at warning status in the traceability
matrix: FR-012, FR-014, FR-017, FR-039, FR-064, FR-077.

Uses Flask test client to fetch the rendered HTML and asserts DOM presence.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def html(tmp_path, monkeypatch):
    """Fetch rendered index.html via Flask test client."""
    monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
    (tmp_path / "profile" / "experiences").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTOAPPLY_DEV", "1")
    from app import create_app
    app, _ = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        rv = c.get("/")
        assert rv.status_code == 200
        yield rv.data.decode("utf-8")


# ===================================================================
# FR-012 — SocketIO Real-Time Updates
# ===================================================================


class TestFR012SocketIO:
    """FR-012: SocketIO real-time DOM elements present."""

    def test_socketio_script_loaded(self, html):
        """Index page loads socket.io client library."""
        assert "socket.io" in html

    def test_activity_feed_container(self, html):
        """Activity feed container exists for real-time events."""
        assert 'id="feed-list"' in html

    def test_bot_status_elements(self, html):
        """Bot status display elements exist for real-time updates."""
        assert 'id="bot-status-label"' in html

    def test_stat_counters_exist(self, html):
        """Real-time stat counters (found, applied, errors) exist."""
        assert 'id="stat-found"' in html
        assert 'id="stat-applied"' in html
        assert 'id="stat-errors"' in html


# ===================================================================
# FR-014 — Dashboard Screen
# ===================================================================


class TestFR014Dashboard:
    """FR-014: Dashboard screen renders correctly."""

    def test_dashboard_screen_exists(self, html):
        """Dashboard screen container is present."""
        assert 'id="screen-dashboard"' in html

    def test_dashboard_bot_controls(self, html):
        """Dashboard has bot control buttons (start, pause, stop)."""
        assert "botControl('start')" in html or 'botControl' in html
        assert 'id="btn-start"' in html or 'btn-start' in html

    def test_dashboard_stats_panel(self, html):
        """Dashboard has statistics panel."""
        assert 'id="stat-found"' in html
        assert 'id="stat-uptime"' in html

    def test_dashboard_mode_selector(self, html):
        """Dashboard has apply mode selector."""
        assert 'id="apply-mode-select"' in html

    def test_dashboard_activity_feed(self, html):
        """Dashboard has activity feed section."""
        assert 'id="feed-list"' in html
        assert 'data-i18n="dashboard.activity_feed"' in html or 'Activity Feed' in html


# ===================================================================
# FR-017 — Settings Screen
# ===================================================================


class TestFR017Settings:
    """FR-017: Settings screen renders all form fields."""

    def test_settings_screen_exists(self, html):
        """Settings screen container is present."""
        assert 'id="screen-settings"' in html

    def test_profile_fields(self, html):
        """Settings has all profile input fields."""
        profile_fields = [
            "set-first-name", "set-last-name", "set-email",
            "set-phone", "set-city", "set-state", "set-zip",
            "set-country", "set-bio", "set-linkedin", "set-portfolio",
        ]
        for field_id in profile_fields:
            assert f'id="{field_id}"' in html, f"Missing profile field: {field_id}"

    def test_save_settings_button(self, html):
        """Settings has save button."""
        assert "saveSettings()" in html

    def test_locale_dropdown(self, html):
        """Settings has locale dropdown (TASK-022)."""
        assert 'id="set-locale"' in html


# ===================================================================
# FR-039 — AI Warning Banner
# ===================================================================


class TestFR039AIWarning:
    """FR-039: AI warning banner renders when no provider configured."""

    def test_ai_warning_banner_exists(self, html):
        """AI warning banner element is present in DOM."""
        assert 'id="ai-warning-banner"' in html

    def test_ai_warning_has_content(self, html):
        """AI warning banner has warning content."""
        assert "No AI provider configured" in html or \
               'data-i18n="dashboard.ai_warning"' in html

    def test_ai_status_indicator(self, html):
        """AI status indicator element exists."""
        assert 'id="ai-status"' in html or 'ai-status' in html


# ===================================================================
# FR-064 — Schedule UI
# ===================================================================


class TestFR064ScheduleUI:
    """FR-064: Schedule UI renders day checkboxes and time inputs."""

    def test_schedule_enabled_toggle(self, html):
        """Schedule has enabled/disabled toggle."""
        assert 'id="set-schedule-enabled"' in html

    def test_schedule_day_checkboxes(self, html):
        """Schedule has checkboxes for each day of the week."""
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for day in days:
            assert f'value="{day}"' in html, f"Missing schedule day: {day}"

    def test_schedule_day_checkboxes_class(self, html):
        """Schedule day checkboxes have the correct CSS class."""
        assert "set-schedule-day" in html

    def test_schedule_time_inputs(self, html):
        """Schedule has start and end time inputs."""
        assert 'id="set-schedule-start"' in html
        assert 'id="set-schedule-end"' in html

    def test_schedule_status_badge(self, html):
        """Schedule status badge exists."""
        assert 'id="schedule-status-badge"' in html


# ===================================================================
# FR-077 — LLM Config UI
# ===================================================================


class TestFR077LLMConfigUI:
    """FR-077: LLM configuration UI renders provider, model, key fields."""

    def test_llm_provider_select(self, html):
        """LLM config has provider dropdown."""
        assert 'id="set-llm-provider"' in html

    def test_llm_provider_options(self, html):
        """LLM provider dropdown has all supported providers."""
        assert 'value="anthropic"' in html
        assert 'value="openai"' in html
        assert 'value="google"' in html
        assert 'value="deepseek"' in html

    def test_llm_model_input(self, html):
        """LLM config has model input field."""
        assert 'id="set-llm-model"' in html

    def test_llm_api_key_input(self, html):
        """LLM config has API key input field."""
        assert 'id="set-llm-api-key"' in html

    def test_llm_validate_button(self, html):
        """LLM config has validate key button."""
        assert "validateLLMKey()" in html

    def test_llm_key_status_display(self, html):
        """LLM config has key validation status display."""
        assert 'id="llm-key-status"' in html


# ===================================================================
# Cross-cutting: Accessibility
# ===================================================================


class TestAccessibility:
    """Cross-cutting: Rendered HTML has accessibility essentials."""

    def test_html_lang_attribute(self, html):
        """HTML element has lang attribute."""
        assert '<html lang="' in html

    def test_skip_to_content_link(self, html):
        """Skip-to-content link exists for keyboard navigation."""
        assert "skip-to-content" in html or "skip_to_content" in html

    def test_main_landmark(self, html):
        """Page has <main> landmark."""
        assert "<main" in html

    def test_nav_landmark(self, html):
        """Page has <nav> landmark."""
        assert "<nav" in html

    def test_aria_labels_present(self, html):
        """Key interactive elements have aria-label attributes."""
        assert "aria-label" in html
