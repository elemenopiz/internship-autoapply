"""Profile and experience file routes.

Implements: FR-011 (experience file CRUD), FR-012 (profile status).
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

import app_state
from config.settings import get_data_dir
from core.i18n import t
from routes.bot import check_ai_available

profile_bp = Blueprint("profile", __name__)


def validate_filename(filename: str) -> str | None:
    """Returns error message if filename is invalid, None if valid."""
    if not filename:
        return t("errors.filename_required")
    if ".." in filename or "/" in filename or "\\" in filename:
        return t("errors.invalid_filename")
    if not app_state.SAFE_FILENAME_RE.match(filename):
        return t("errors.invalid_filename_detail")
    return None


@profile_bp.route("/api/profile/experiences", methods=["GET"])
def list_experiences():
    experiences_dir = get_data_dir() / "profile" / "experiences"
    experiences_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    for file_path in sorted(experiences_dir.glob("*.txt")):
        stat = file_path.stat()
        files.append({
            "name": file_path.name,
            "content": file_path.read_text(encoding="utf-8"),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return jsonify({"files": files})


@profile_bp.route("/api/profile/experiences", methods=["POST"])
def create_experience():
    data = request.get_json()
    if not data or "filename" not in data or "content" not in data:
        return jsonify({"error": t("errors.filename_content_required")}), 400
    filename: str = data["filename"]
    content: str = data["content"]
    error = validate_filename(filename)
    if error:
        return jsonify({"error": error}), 400
    experiences_dir = get_data_dir() / "profile" / "experiences"
    experiences_dir.mkdir(parents=True, exist_ok=True)
    (experiences_dir / filename).write_text(content, encoding="utf-8")
    return jsonify({"success": True})


@profile_bp.route("/api/profile/experiences/<filename>", methods=["PUT"])
def update_experience(filename: str):
    error = validate_filename(filename)
    if error:
        return jsonify({"error": error}), 400
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": t("errors.content_required")}), 400
    content: str = data["content"]
    experiences_dir = get_data_dir() / "profile" / "experiences"
    file_path = experiences_dir / filename
    if not file_path.exists():
        return jsonify({"error": t("errors.file_not_found")}), 404
    file_path.write_text(content, encoding="utf-8")
    return jsonify({"success": True})


@profile_bp.route("/api/profile/experiences/<filename>", methods=["DELETE"])
def delete_experience(filename: str):
    error = validate_filename(filename)
    if error:
        return jsonify({"error": error}), 400
    experiences_dir = get_data_dir() / "profile" / "experiences"
    file_path = experiences_dir / filename
    if not file_path.exists():
        return jsonify({"error": t("errors.file_not_found")}), 404
    file_path.unlink()
    return jsonify({"success": True})


@profile_bp.route("/api/profile/status", methods=["GET"])
def profile_status():
    experiences_dir = get_data_dir() / "profile" / "experiences"
    experiences_dir.mkdir(parents=True, exist_ok=True)
    txt_files = list(experiences_dir.glob("*.txt"))
    total_words = 0
    for file_path in txt_files:
        total_words += len(file_path.read_text(encoding="utf-8").split())
    return jsonify({
        "file_count": len(txt_files),
        "total_words": total_words,
        "ai_available": check_ai_available(),
    })
