"""Bot control and review gate routes.

Implements: FR-006 (bot control API), FR-053 (review gate), FR-062 (schedule API).
"""

from __future__ import annotations

import logging
import threading

from flask import Blueprint, jsonify, request

import app_state
from config.settings import ScheduleConfig, load_config, save_config
from core.i18n import t

logger = logging.getLogger(__name__)

bot_bp = Blueprint("bot", __name__)


def check_ai_available() -> bool:
    """Check if an AI provider is configured with an API key."""
    from core.ai_engine import check_ai_available as _check_ai_available

    config = load_config()
    if not config:
        return False
    return _check_ai_available(config.llm)


def _get_schedule() -> ScheduleConfig | None:
    """Get current schedule config (used by scheduler)."""
    config = load_config()
    if config is None:
        return None
    return config.bot.schedule


def _scheduler_start_bot() -> str:
    """Start bot thread. Returns 'started', 'already_running', or 'no_config'."""
    with app_state.bot_lock:
        if app_state.bot_thread and app_state.bot_thread.is_alive():
            return "already_running"
        config = load_config()
        if config is None:
            return "no_config"
        app_state.bot_state.start()

        def _run():
            from bot.bot import run_bot

            sio = app_state.socketio
            db = app_state.db
            if sio is None or db is None:
                logger.error("Cannot start bot: socketio or db not initialized")
                return

            def _emit(event_name, data):
                sio.emit(event_name, data)
                status_dict = app_state.bot_state.get_status_dict()
                status_dict["ai_available"] = check_ai_available()
                sio.emit("bot_status", status_dict)

            try:
                run_bot(state=app_state.bot_state, config=config, db=db, emit_func=_emit)
            finally:
                app_state.bot_state.stop()
                status_dict = app_state.bot_state.get_status_dict()
                status_dict["ai_available"] = check_ai_available()
                sio.emit("bot_status", status_dict)

        app_state.bot_thread = threading.Thread(target=_run, daemon=True, name="bot-worker")
        app_state.bot_thread.start()
        return "started"


def _scheduler_stop_bot() -> None:
    """Stop bot from scheduler."""
    with app_state.bot_lock:
        thread = app_state.bot_thread
    app_state.bot_state.stop()
    if thread and thread.is_alive():
        thread.join(timeout=10)


def _is_bot_running() -> bool:
    """Check if bot thread is alive."""
    with app_state.bot_lock:
        return app_state.bot_thread is not None and app_state.bot_thread.is_alive()


def init_scheduler():
    """Create and optionally start the bot scheduler. Called by create_app()."""
    from core.scheduler import BotScheduler

    app_state.bot_scheduler = BotScheduler(
        get_schedule=_get_schedule,
        start_bot=_scheduler_start_bot,
        stop_bot=_scheduler_stop_bot,
        is_bot_running=_is_bot_running,
    )
    schedule = _get_schedule()
    if schedule and schedule.enabled:
        app_state.bot_scheduler.start()


# ---------------------------------------------------------------------------
# Bot Control
# ---------------------------------------------------------------------------

@bot_bp.route("/api/bot/start", methods=["POST"])
def bot_start():
    result = _scheduler_start_bot()
    if result == "already_running":
        return jsonify({"error": t("errors.bot_already_running")}), 409
    if result == "no_config":
        return jsonify({"error": t("errors.config_not_found")}), 400
    return jsonify({"status": "running"})


@bot_bp.route("/api/bot/pause", methods=["POST"])
def bot_pause():
    app_state.bot_state.pause()
    return jsonify({"status": "paused"})


@bot_bp.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    app_state.bot_state.stop()
    with app_state.bot_lock:
        thread = app_state.bot_thread
    if thread and thread.is_alive():
        thread.join(timeout=10)
    return jsonify({"status": "stopped"})


@bot_bp.route("/api/bot/status", methods=["GET"])
def bot_status():
    status_dict = app_state.bot_state.get_status_dict()
    status_dict["ai_available"] = check_ai_available()
    status_dict["schedule_enabled"] = app_state.bot_scheduler.running if app_state.bot_scheduler else False
    return jsonify(status_dict)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

@bot_bp.route("/api/bot/schedule", methods=["GET"])
def get_schedule_route():
    config = load_config()
    if config is None:
        return jsonify(ScheduleConfig().model_dump())
    return jsonify(config.bot.schedule.model_dump())


@bot_bp.route("/api/bot/schedule", methods=["PUT"])
def update_schedule():
    data = request.get_json()
    if not data:
        return jsonify({"error": t("errors.request_body_required")}), 400

    valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    if "days_of_week" in data:
        invalid = [d for d in data["days_of_week"] if d.lower() not in valid_days]
        if invalid:
            return jsonify({"error": t("errors.invalid_days", days=str(invalid))}), 400

    for field in ("start_time", "end_time"):
        if field in data:
            try:
                h, m = map(int, data[field].split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except (ValueError, AttributeError):
                return jsonify({"error": t("errors.invalid_time_format", field=field)}), 400

    config = load_config()
    if config is None:
        return jsonify({"error": t("errors.config_not_found")}), 400

    current = config.bot.schedule.model_dump()
    current.update(data)
    config.bot.schedule = ScheduleConfig(**current)
    save_config(config)

    scheduler = app_state.bot_scheduler
    if scheduler is not None:
        if config.bot.schedule.enabled:
            if not scheduler.running:
                scheduler.start()
        else:
            if scheduler.running:
                scheduler.stop()

    return jsonify(config.bot.schedule.model_dump())


# ---------------------------------------------------------------------------
# Review Gate
# ---------------------------------------------------------------------------

@bot_bp.route("/api/bot/review/approve", methods=["POST"])
def review_approve():
    if not app_state.bot_state.awaiting_review:
        return jsonify({"error": t("errors.no_review_pending")}), 409
    app_state.bot_state.set_review_decision("approve")
    return jsonify({"status": "approved"})


@bot_bp.route("/api/bot/review/skip", methods=["POST"])
def review_skip():
    if not app_state.bot_state.awaiting_review:
        return jsonify({"error": t("errors.no_review_pending")}), 409
    app_state.bot_state.set_review_decision("skip")
    return jsonify({"status": "skipped"})


@bot_bp.route("/api/bot/review/edit", methods=["POST"])
def review_edit():
    if not app_state.bot_state.awaiting_review:
        return jsonify({"error": t("errors.no_review_pending")}), 409
    data = request.get_json()
    if not data or "cover_letter" not in data:
        return jsonify({"error": t("errors.cover_letter_required")}), 400
    app_state.bot_state.set_review_decision("edit", edits=data["cover_letter"])
    return jsonify({"status": "edited"})


@bot_bp.route("/api/bot/review/manual", methods=["POST"])
def review_manual():
    """Mark current review as 'manual' — user will apply themselves."""
    if not app_state.bot_state.awaiting_review:
        return jsonify({"error": t("errors.no_review_pending")}), 409
    app_state.bot_state.set_review_decision("manual")
    return jsonify({"status": "manual"})
