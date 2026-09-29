"""Resume versioning routes.

Implements: FR-111 (resume list API), FR-112 (resume detail API),
            FR-113 (resume PDF serve), FR-114 (resume metrics API),
            FR-120 (favorite toggle), FR-123 (comparison API).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file

import app_state
from config.settings import get_data_dir
from core.i18n import t

resumes_bp = Blueprint("resumes", __name__)


def _get_db():
    """Return the database instance or abort 503 if not initialized."""
    db = app_state.db
    if db is None:
        abort(503, description=t("errors.database_not_initialized"))
    return db


def _is_safe_resume_path(file_path: str | Path) -> bool:
    """Verify file_path is within the autoapply data directory (no traversal)."""
    try:
        resolved = Path(file_path).resolve()
        allowed = get_data_dir().resolve()
        return resolved.is_relative_to(allowed) and resolved.exists()
    except (ValueError, OSError):
        return False


# Static routes before parameterized routes (lesson 11.2)

@resumes_bp.route("/api/resumes/metrics", methods=["GET"])
def resume_metrics():
    return jsonify(_get_db().get_resume_metrics())


@resumes_bp.route("/api/resumes", methods=["GET"])
def list_resumes():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    search = request.args.get("search")
    sort = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")

    items, total_count = _get_db().get_resume_versions(
        page=page, per_page=per_page, search=search, sort=sort, order=order
    )
    return jsonify({
        "items": items,
        "total_count": total_count,
        "page": max(1, page),
        "per_page": per_page,
    })


@resumes_bp.route("/api/resumes/compare", methods=["GET"])
def resume_compare():
    left_id = request.args.get("left", type=int)
    right_id = request.args.get("right", type=int)
    if not left_id or not right_id:
        abort(400, description=t("errors.bad_request"))

    db = _get_db()

    def _read_side(version_id):
        version = db.get_resume_version(version_id)
        if version is None:
            return None
        md_path = version.get("resume_md_path", "")
        content = None
        file_missing = False
        if md_path and _is_safe_resume_path(md_path):
            try:
                content = Path(md_path).read_text(encoding="utf-8")
            except OSError:
                file_missing = True
        elif md_path:
            file_missing = True
        return {
            "id": version["id"],
            "company": version["company"],
            "job_title": version["job_title"],
            "created_at": version["created_at"],
            "resume_md_content": content,
            "file_missing": file_missing,
        }

    left = _read_side(left_id)
    right = _read_side(right_id)
    if left is None or right is None:
        abort(404, description=t("errors.not_found"))

    return jsonify({"left": left, "right": right})


@resumes_bp.route("/api/resumes/<int:version_id>", methods=["GET"])
def resume_detail(version_id: int):
    version = _get_db().get_resume_version(version_id)
    if version is None:
        abort(404, description=t("errors.not_found"))

    # Read markdown content from disk
    md_path = version.get("resume_md_path", "")
    version["resume_md_content"] = None
    version["file_missing"] = False

    if md_path and _is_safe_resume_path(md_path):
        try:
            version["resume_md_content"] = Path(md_path).read_text(
                encoding="utf-8"
            )
        except OSError:
            version["file_missing"] = True
    elif md_path:
        # Path exists in DB but file is missing or traversal blocked
        version["file_missing"] = True

    return jsonify(version)


@resumes_bp.route("/api/resumes/<int:version_id>/favorite", methods=["PUT"])
def toggle_favorite(version_id: int):
    result = _get_db().toggle_favorite(version_id)
    if result is None:
        abort(404, description=t("errors.not_found"))
    return jsonify({"id": version_id, "is_favorite": result})


@resumes_bp.route("/api/resumes/<int:version_id>/pdf", methods=["GET"])
def resume_pdf(version_id: int):
    version = _get_db().get_resume_version(version_id)
    if version is None:
        abort(404, description=t("errors.not_found"))

    pdf_path = version.get("resume_pdf_path", "")
    if not pdf_path or not _is_safe_resume_path(pdf_path):
        abort(404, description=t("resumes.file_missing"))

    download = request.args.get("download", "").lower() == "true"
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=download,
        download_name=f"resume_{version_id}.pdf",
    )
