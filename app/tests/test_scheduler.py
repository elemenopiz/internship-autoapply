"""Unit tests for core.scheduler.

Requirement traceability:
    FR-060 — Schedule configuration (days + time window)
    FR-061 — Auto-start bot when schedule window opens
    FR-062 — Auto-stop bot when schedule window closes
    FR-063 — Schedule API endpoints
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from config.settings import ScheduleConfig
from core.scheduler import BotScheduler, is_within_schedule

# ===================================================================
# is_within_schedule
# ===================================================================


class TestIsWithinSchedule:
    """FR-060: Time window checks."""

    def test_disabled_schedule_returns_false(self):
        """FR-060: Disabled schedule always returns False."""
        sched = ScheduleConfig(enabled=False)
        assert is_within_schedule(sched) is False

    def test_within_weekday_window(self):
        """FR-060: Returns True during configured weekday + time window."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon", "tue", "wed", "thu", "fri"],
            start_time="09:00",
            end_time="17:00",
        )
        # Wednesday 10:30 AM
        now = datetime(2026, 3, 11, 10, 30)  # Wednesday
        assert is_within_schedule(sched, now=now) is True

    def test_outside_time_window(self):
        """FR-060: Returns False outside the time window."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon", "tue", "wed", "thu", "fri"],
            start_time="09:00",
            end_time="17:00",
        )
        # Wednesday 8:00 AM (before window)
        now = datetime(2026, 3, 11, 8, 0)
        assert is_within_schedule(sched, now=now) is False

    def test_after_time_window(self):
        """FR-060: Returns False after the time window."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon"],
            start_time="09:00",
            end_time="17:00",
        )
        # Monday 17:30
        now = datetime(2026, 3, 9, 17, 30)
        assert is_within_schedule(sched, now=now) is False

    def test_wrong_day_of_week(self):
        """FR-060: Returns False on non-scheduled day."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon", "wed", "fri"],
            start_time="09:00",
            end_time="17:00",
        )
        # Tuesday 10:00 AM
        now = datetime(2026, 3, 10, 10, 0)  # Tuesday
        assert is_within_schedule(sched, now=now) is False

    def test_weekend_schedule(self):
        """FR-060: Weekend days work correctly."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["sat", "sun"],
            start_time="10:00",
            end_time="14:00",
        )
        # Saturday 12:00
        now = datetime(2026, 3, 14, 12, 0)  # Saturday
        assert is_within_schedule(sched, now=now) is True

    def test_exact_start_time_included(self):
        """FR-060: Start time is inclusive."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon"],
            start_time="09:00",
            end_time="17:00",
        )
        now = datetime(2026, 3, 9, 9, 0)  # Monday 09:00 exactly
        assert is_within_schedule(sched, now=now) is True

    def test_exact_end_time_excluded(self):
        """FR-060: End time is exclusive (bot should stop)."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon"],
            start_time="09:00",
            end_time="17:00",
        )
        now = datetime(2026, 3, 9, 17, 0)  # Monday 17:00 exactly
        assert is_within_schedule(sched, now=now) is False

    def test_overnight_window(self):
        """FR-060: Overnight window (start > end) works correctly."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon"],
            start_time="22:00",
            end_time="06:00",
        )
        # Monday 23:30
        now = datetime(2026, 3, 9, 23, 30)
        assert is_within_schedule(sched, now=now) is True

        # Monday 3:00 AM (still in overnight window)
        now2 = datetime(2026, 3, 9, 3, 0)
        assert is_within_schedule(sched, now=now2) is True

        # Monday 12:00 (outside overnight window)
        now3 = datetime(2026, 3, 9, 12, 0)
        assert is_within_schedule(sched, now=now3) is False

    def test_invalid_time_format_returns_false(self):
        """FR-060: Invalid time format returns False gracefully."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=["mon"],
            start_time="invalid",
            end_time="17:00",
        )
        now = datetime(2026, 3, 9, 10, 0)
        assert is_within_schedule(sched, now=now) is False

    def test_empty_days_returns_false(self):
        """FR-060: No days selected means schedule never matches."""
        sched = ScheduleConfig(
            enabled=True,
            days_of_week=[],
            start_time="09:00",
            end_time="17:00",
        )
        now = datetime(2026, 3, 9, 10, 0)
        assert is_within_schedule(sched, now=now) is False


# ===================================================================
# BotScheduler
# ===================================================================


class TestBotSchedulerStartStop:
    """FR-061/062: Scheduler auto-starts and stops bot."""

    def test_scheduler_starts_bot_when_within_window(self):
        """FR-061: Scheduler calls start_bot when in schedule window."""
        sched = ScheduleConfig(
            enabled=True, days_of_week=["mon"],
            start_time="09:00", end_time="17:00",
        )
        start_bot = MagicMock()
        stop_bot = MagicMock()
        is_running = MagicMock(return_value=False)

        scheduler = BotScheduler(
            get_schedule=lambda: sched,
            start_bot=start_bot,
            stop_bot=stop_bot,
            is_bot_running=is_running,
        )

        with patch("core.scheduler.is_within_schedule", return_value=True):
            scheduler._tick()

        start_bot.assert_called_once()
        assert scheduler.auto_started is True

    def test_scheduler_stops_bot_when_outside_window(self):
        """FR-062: Scheduler calls stop_bot when leaving schedule window."""
        sched = ScheduleConfig(
            enabled=True, days_of_week=["mon"],
            start_time="09:00", end_time="17:00",
        )
        start_bot = MagicMock()
        stop_bot = MagicMock()
        is_running = MagicMock(return_value=True)

        scheduler = BotScheduler(
            get_schedule=lambda: sched,
            start_bot=start_bot,
            stop_bot=stop_bot,
            is_bot_running=is_running,
        )
        scheduler._auto_started = True  # Simulate previous auto-start

        with patch("core.scheduler.is_within_schedule", return_value=False):
            scheduler._tick()

        stop_bot.assert_called_once()
        assert scheduler.auto_started is False

    def test_scheduler_does_not_start_if_already_running(self):
        """FR-061: Scheduler does not double-start the bot."""
        sched = ScheduleConfig(
            enabled=True, days_of_week=["mon"],
            start_time="09:00", end_time="17:00",
        )
        start_bot = MagicMock()

        scheduler = BotScheduler(
            get_schedule=lambda: sched,
            start_bot=start_bot,
            stop_bot=MagicMock(),
            is_bot_running=lambda: True,
        )

        with patch("core.scheduler.is_within_schedule", return_value=True):
            scheduler._tick()

        start_bot.assert_not_called()

    def test_scheduler_does_not_stop_manually_started_bot(self):
        """FR-062: Scheduler only stops bots IT started, not manual starts."""
        sched = ScheduleConfig(
            enabled=True, days_of_week=["mon"],
            start_time="09:00", end_time="17:00",
        )
        stop_bot = MagicMock()

        scheduler = BotScheduler(
            get_schedule=lambda: sched,
            start_bot=MagicMock(),
            stop_bot=stop_bot,
            is_bot_running=lambda: True,
        )
        # auto_started is False — bot was started manually
        scheduler._auto_started = False

        with patch("core.scheduler.is_within_schedule", return_value=False):
            scheduler._tick()

        stop_bot.assert_not_called()

    def test_scheduler_handles_none_schedule(self):
        """FR-061: Scheduler handles missing config gracefully."""
        scheduler = BotScheduler(
            get_schedule=lambda: None,
            start_bot=MagicMock(),
            stop_bot=MagicMock(),
            is_bot_running=lambda: False,
        )
        # Should not raise
        scheduler._tick()

    def test_scheduler_stops_bot_when_schedule_disabled(self):
        """FR-062: Disabling schedule stops auto-started bot."""
        sched = ScheduleConfig(enabled=False)
        stop_bot = MagicMock()

        scheduler = BotScheduler(
            get_schedule=lambda: sched,
            start_bot=MagicMock(),
            stop_bot=stop_bot,
            is_bot_running=lambda: True,
        )
        scheduler._auto_started = True

        scheduler._tick()

        stop_bot.assert_called_once()
        assert scheduler.auto_started is False


class TestBotSchedulerThread:
    """FR-061: Scheduler thread lifecycle."""

    def test_start_creates_thread(self):
        """FR-061: start() creates a daemon thread."""
        scheduler = BotScheduler(
            get_schedule=lambda: None,
            start_bot=MagicMock(),
            stop_bot=MagicMock(),
            is_bot_running=lambda: False,
            check_interval=1,
        )
        scheduler.start()
        assert scheduler.running is True
        scheduler.stop()
        assert scheduler.running is False

    def test_start_is_idempotent(self):
        """FR-061: Calling start() twice doesn't create duplicate threads."""
        scheduler = BotScheduler(
            get_schedule=lambda: None,
            start_bot=MagicMock(),
            stop_bot=MagicMock(),
            is_bot_running=lambda: False,
            check_interval=1,
        )
        scheduler.start()
        thread1 = scheduler._thread
        scheduler.start()  # Should be a no-op
        assert scheduler._thread is thread1
        scheduler.stop()


# ===================================================================
# Schedule API tests (FR-063)
# ===================================================================


class TestScheduleAPI:
    """FR-063: Schedule API endpoints."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        """Flask test client with isolated config."""
        monkeypatch.setattr("config.settings.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("app.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("routes.profile.get_data_dir", lambda: tmp_path)
        (tmp_path / "profile" / "experiences").mkdir(parents=True)

        from db.database import Database
        test_db = Database(tmp_path / "test.db")
        monkeypatch.setattr("app.db", test_db)
        monkeypatch.setattr("app_state.db", test_db)

        from app import app as flask_app
        from app import bot_scheduler
        flask_app.config["TESTING"] = True
        if bot_scheduler.running:
            bot_scheduler.stop()
        return flask_app.test_client()

    def _write_config(self, tmp_path):
        """Write a minimal config to tmp_path."""
        import json
        minimal_config = {
            "profile": {
                "first_name": "Test", "last_name": "U",
                "email": "t@t.com", "phone": "555",
                "city": "NY", "state": "NY", "bio": "x",
            },
            "search_criteria": {"job_titles": ["Dev"], "locations": ["NY"]},
            "bot": {"enabled_platforms": ["linkedin"]},
        }
        (tmp_path / "config.json").write_text(
            json.dumps(minimal_config), encoding="utf-8",
        )

    def test_get_schedule_default(self, client):
        """FR-063: GET /api/bot/schedule returns defaults when no config."""
        res = client.get("/api/bot/schedule")
        assert res.status_code == 200
        data = res.get_json()
        assert data["enabled"] is False
        assert "mon" in data["days_of_week"]

    def test_put_schedule_valid(self, client, tmp_path):
        """FR-063: PUT /api/bot/schedule saves schedule config."""
        self._write_config(tmp_path)

        res = client.put("/api/bot/schedule", json={
            "enabled": True,
            "days_of_week": ["mon", "wed", "fri"],
            "start_time": "10:00",
            "end_time": "16:00",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["enabled"] is True
        assert data["days_of_week"] == ["mon", "wed", "fri"]
        assert data["start_time"] == "10:00"
        assert data["end_time"] == "16:00"

    def test_put_schedule_invalid_day(self, client, tmp_path):
        """FR-063: PUT rejects invalid day names."""
        self._write_config(tmp_path)
        res = client.put("/api/bot/schedule", json={
            "days_of_week": ["monday"],
        })
        assert res.status_code == 400

    def test_put_schedule_invalid_time(self, client, tmp_path):
        """FR-063: PUT rejects invalid time format."""
        self._write_config(tmp_path)
        res = client.put("/api/bot/schedule", json={
            "start_time": "25:00",
        })
        assert res.status_code == 400

    def test_put_schedule_no_config(self, client):
        """FR-063: PUT returns 400 when no config exists."""
        res = client.put("/api/bot/schedule", json={"enabled": True})
        assert res.status_code == 400

    def test_put_schedule_empty_body(self, client):
        """FR-063: PUT returns 400 with empty body."""
        res = client.put("/api/bot/schedule",
                         data="", content_type="application/json")
        assert res.status_code == 400
