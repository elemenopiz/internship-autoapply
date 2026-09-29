"""JSON view models shared by the API endpoints and the HTML pages (pages are thin views over the API)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder

from autoapply.clock import local_day
from autoapply.config import AppConfig, resolve_resume_path
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.files import served_file_ref
from autoapply.models import (
    REQUIRED_PROFILE_FIELDS,
    Application,
    ApplicationStatus,
    Opportunity,
    PendingQuestion,
    RunReport,
    ScreeningAnswer,
)
from autoapply.readiness import ReadinessReport, check_readiness

log = logging.getLogger("autoapply.dashboard")

AUTOMATION_RISK_MESSAGE = (
    "LinkedIn and Indeed restrict automated access in their terms of service. Discovery through them is "
    "opt-in and at your own risk: your account can be limited or banned. The applier only reads listings "
    "and follows the employer's own apply link (never Easy Apply), and it stops at any challenge or login "
    "wall. Turn these platforms off on the Search page if you do not accept that risk."
)
REASON_LABELS: dict[str, str] = {
    "unsupported_portal": "Employer portal not supported (apply by hand)",
    "bot_check": "Blocked by a CAPTCHA / bot check",
    "login_required": "Needs a sign-in that cannot be automated",
    "email_verification": "Needs an email verification we could not read",
    "missing_answer": "A required question has no saved answer",
    "attestation_not_authorized": "Certification boxes need your authorisation (Profile page)",
    "account_problem": "Problem with the employer account",
    "document_rejected": "The site rejected a document",
    "validation_error": "The site rejected the data and it could not be fixed",
    "unexpected_flow": "The page was not understood",
    "posting_closed": "Posting closed",
    "already_applied": "Already applied",
    "ineligible": "Not eligible",
    "duplicate": "Duplicate of another application",
    "interrupted": "Interrupted before it finished (outcome unknown)",
    "timeout": "Timed out",
    "network_error": "Network error",
    "internal_error": "Internal error",
    "other": "Other",
}
_PROFILE_ISSUE_FIELDS = set(REQUIRED_PROFILE_FIELDS)


def timezone_or_utc(config: AppConfig) -> tuple[str, bool]:
    """``(usable timezone name, was the configured one valid)``."""
    try:
        ZoneInfo(config.timezone)
    except (KeyError, ValueError, OSError):
        return "UTC", False
    return config.timezone, True


def readiness_for(
    runtime: DashboardRuntime, config: AppConfig, mode: Any = None
) -> ReadinessReport:
    return check_readiness(config, runtime.paths, runtime.env, runtime.store, mode=mode)


def issue_link(field: str) -> str | None:
    """The dashboard page where a readiness issue is fixed (``None``: fixed outside the dashboard)."""
    if field in _PROFILE_ISSUE_FIELDS or field == "apply.attestations_authorized":
        return "/profile"
    if field == "resume":
        return "/resume"
    if field in {"workbook.path", "platforms"}:
        return "/search"
    return None


def readiness_view(report: ReadinessReport) -> dict[str, Any]:
    data = report.to_dict()
    for issue in data["issues"]:
        issue["link"] = issue_link(issue["field"])
    return data


def risky_platforms(config: AppConfig) -> list[str]:
    return [n for n in ("linkedin", "indeed") if getattr(config.platforms, n)]


def build_state(runtime: DashboardRuntime, config: AppConfig) -> dict[str, Any]:
    """Everything the Overview needs. Never contains any part of a secret."""
    tz, tz_valid = timezone_or_utc(config)
    today = local_day(runtime.clock.now(), tz)
    submitted = runtime.repo.count_submitted_on(today, tz)
    stats = runtime.repo.stats(tz)
    try:
        controller_status: dict[str, Any] = dict(jsonable_encoder(runtime.controller.status()))
    except Exception:
        log.exception("controller.status() failed")
        controller_status = {"error": "The run controller could not report its status."}
    try:
        running = bool(runtime.controller.is_running())
    except Exception:
        log.exception("controller.is_running() failed")
        running = False
    resolution = runtime.openai_key_resolution()
    schedule = config.schedule.model_dump(mode="json")
    schedule["next_run_at"] = controller_status.get("next_run_at")
    pending = len(runtime.repo.list_pending_questions())
    return {
        "mode": config.mode.value,
        "daily_cap": config.daily_cap,
        "timezone": config.timezone,
        "timezone_valid": tz_valid,
        "today": today.isoformat(),
        "submitted_today": submitted,
        "cap_remaining": max(0, config.daily_cap - submitted),
        "cap_reached": submitted >= config.daily_cap,
        "readiness": readiness_view(readiness_for(runtime, config)),
        "running": running,
        "controller": controller_status,
        "applications_by_status": stats["applications_by_status"],
        "opportunities": {
            "total": stats["opportunities_total"],
            "open": stats["opportunities_open"],
            "scored": stats["opportunities_scored"],
            "passed": stats["opportunities_passed"],
        },
        "pending_questions": pending,
        "schedule": schedule,
        "platforms": config.platforms.model_dump(mode="json"),
        "automation_risk_platforms": risky_platforms(config),
        "automation_risk_message": AUTOMATION_RISK_MESSAGE,
        "stop_file": runtime.stop_active,
        "secrets": {"openai_key": {"present": resolution.present, "source": resolution.source}},
    }


# ------------------------------------------------------------------------------------------ rows


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def opportunity_view(
    op: Opportunity, latest: Application | None = None, *, full: bool = False
) -> dict[str, Any]:
    data = op.model_dump(mode="json", exclude={"description", "extra"})
    data["latest_application"] = (
        {
            "id": latest.id,
            "status": latest.status.value,
            "reason": latest.reason.value if latest.reason else None,
        }
        if latest
        else None
    )
    description = op.description or ""
    data["description_snippet"] = description[:240]
    if full:
        data["description"] = op.description
        data["extra"] = jsonable_encoder(op.extra)
    return data


def _file_link(runtime: DashboardRuntime, raw: str, label: str | None = None) -> dict[str, Any]:
    ref = served_file_ref(runtime.paths, raw)
    name = PurePath(raw.replace("\\", "/")).name
    return {
        "label": label or name,
        "name": name,
        "url": f"/files/{ref[0]}/{ref[1]}" if ref else None,
        "path": f"{ref[0]}/{ref[1]}" if ref else None,
    }


def application_view(
    runtime: DashboardRuntime,
    app: Application,
    opp: Opportunity | None,
    *,
    full: bool = False,
) -> dict[str, Any]:
    data = app.model_dump(mode="json", exclude={"docs", "artifacts", "steps", "filled_fields"})
    data["company"] = opp.company if opp else None
    data["title"] = opp.title if opp else None
    data["url"] = (opp.apply_url or opp.url) if opp else None
    data["reason_label"] = REASON_LABELS.get(app.reason.value) if app.reason else None
    files = [
        _file_link(runtime, app.docs[k], k.replace("_", " "))
        for k in ("resume", "cover_letter")
        if app.docs.get(k)
    ]
    data["docs_mode"] = app.docs.get("mode")
    data["documents"] = files
    data["artifacts"] = [_file_link(runtime, a) for a in app.artifacts]
    data["steps_count"] = len(app.steps)
    if full:
        data["steps"] = list(app.steps)
        data["filled_fields"] = dict(app.filled_fields)
    return data


def run_view(report: RunReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def answer_view(answer: ScreeningAnswer) -> dict[str, Any]:
    return answer.model_dump(mode="json")


def pending_view(question: PendingQuestion) -> dict[str, Any]:
    return question.model_dump(mode="json")


def resume_view(runtime: DashboardRuntime, config: AppConfig) -> dict[str, Any]:
    """Resume status. Never reveals the path of a file outside the data directory."""
    resolved = resolve_resume_path(config, runtime.paths)
    view: dict[str, Any] = {
        "present": resolved is not None,
        "size": None,
        "modified": None,
        "filename": None,
        "source": None,
        "location": None,
        "max_bytes": 10 * 1024 * 1024,
    }
    if resolved is None:
        return view
    try:
        stat = resolved.stat()
    except OSError:
        view["present"] = False
        return view
    view["size"] = stat.st_size
    view["modified"] = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
    try:
        relative = resolved.resolve().relative_to(runtime.paths.root.resolve())
    except (ValueError, OSError):
        view["source"] = "configured_path"
        view["filename"] = "your configured resume"
        return view
    view["source"] = "uploaded" if resolved == runtime.paths.resume_file else "data_directory"
    view["filename"] = resolved.name
    view["location"] = relative.as_posix()
    return view


def dry_run_count(runtime: DashboardRuntime) -> int:
    return runtime.repo.count_applications(status=ApplicationStatus.DRY_RUN_OK)
