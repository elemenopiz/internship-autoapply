"""Application CRUD, export, cover letter, resume, and job description routes.

Implements: FR-007 (application CRUD), FR-008 (CSV export), FR-065 (application detail).
"""

from __future__ import annotations

import io
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import app_state
from config.settings import get_data_dir
from core.i18n import t

applications_bp = Blueprint("applications", __name__)


def _is_safe_path(file_path: str | Path) -> bool:
    """Verify file_path is within the autoapply data directory (no traversal)."""
    try:
        resolved = Path(file_path).resolve()
        allowed = get_data_dir().resolve()
        return resolved.is_relative_to(allowed) and resolved.exists()
    except (ValueError, OSError):
        return False


def _get_db():
    """Return the database instance or abort 503 if not initialized."""
    db = app_state.db
    if db is None:
        from flask import abort
        abort(503, description="Database not initialized")
    return db


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

@applications_bp.route("/api/applications", methods=["GET"])
def get_applications():
    status = request.args.get("status")
    platform_filter = request.args.get("platform")
    search = request.args.get("search")
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    applications = _get_db().get_all_applications(
        status=status,
        platform=platform_filter,
        search=search,
        limit=limit,
        offset=offset,
    )
    return jsonify([a.model_dump() for a in applications])


@applications_bp.route("/api/applications/<int:app_id>", methods=["GET"])
def get_application_detail(app_id: int):
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    return jsonify(application.model_dump())


@applications_bp.route("/api/applications/<int:app_id>/events", methods=["GET"])
def get_application_events(app_id: int):
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    events = _get_db().get_feed_events_for_job(application.job_title, application.company)
    return jsonify([e.model_dump() for e in events])


@applications_bp.route("/api/applications/export", methods=["GET"])
def export_applications():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    tmp.close()
    csv_path = Path(tmp.name)
    try:
        _get_db().export_csv(csv_path)
        data = csv_path.read_bytes()
    finally:
        csv_path.unlink(missing_ok=True)

    buf = io.BytesIO(data)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"applications_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )


@applications_bp.route("/api/applications/<int:app_id>", methods=["PATCH"])
def update_application(app_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": t("errors.request_body_required")}), 400
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    status = data.get("status", application.status)
    if status not in app_state.VALID_APP_STATUSES:
        return jsonify({"error": t("errors.invalid_status", valid_statuses=", ".join(sorted(app_state.VALID_APP_STATUSES)))}), 400
    notes = data.get("notes", application.notes)
    _get_db().update_status(app_id, status=status, notes=notes)
    return jsonify({"success": True})


@applications_bp.route("/api/applications/<int:app_id>/cover_letter", methods=["GET"])
def get_cover_letter(app_id: int):
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    return jsonify({
        "cover_letter_text": application.cover_letter_text,
        "file_path": application.cover_letter_path,
    })


@applications_bp.route("/api/applications/<int:app_id>/resume", methods=["GET"])
def get_resume(app_id: int):
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    resume_path = application.resume_path
    if not resume_path or not _is_safe_path(resume_path):
        return jsonify({"error": t("errors.resume_not_found")}), 404
    return send_file(resume_path, mimetype="application/pdf")


@applications_bp.route("/api/applications/<int:app_id>/description", methods=["GET"])
def get_job_description(app_id: int):
    application = _get_db().get_application(app_id)
    if not application:
        return jsonify({"error": t("errors.application_not_found")}), 404
    desc_path = application.description_path
    if not desc_path or not _is_safe_path(desc_path):
        return jsonify({"error": t("errors.description_not_found")}), 404
    return send_file(desc_path, mimetype="text/html")
