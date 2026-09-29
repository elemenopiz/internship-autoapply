"""The FastAPI application: JSON API + server-rendered HTML pages + static assets + artifact serving.

``create_app(runtime)`` wires everything around a ``DashboardRuntime``. Pages are rendered with Jinja2
(autoescape ON, no inline scripts or styles, no external resources) and are thin views over the same view
functions the JSON API uses; the JavaScript in ``static/app.js`` talks to the API with the CSRF header.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import jinja2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from autoapply.config import AppConfig
from autoapply.dashboard.api import build_api_router
from autoapply.dashboard.deps import DashboardRuntime, FeatureUnavailableError
from autoapply.dashboard.errors import ApiError
from autoapply.dashboard.files import resolve_served_file, serve_file
from autoapply.dashboard.security import SecurityMiddleware, SessionSigner
from autoapply.dashboard.validation import api_error_for_config
from autoapply.dashboard.views import (
    AUTOMATION_RISK_MESSAGE,
    REASON_LABELS,
    answer_view,
    application_view,
    build_state,
    opportunity_view,
    pending_view,
    resume_view,
    risky_platforms,
    run_view,
    timezone_or_utc,
)
from autoapply.models import ApplicationStatus, OpportunitySource, RunMode

log = logging.getLogger("autoapply.dashboard")

__all__ = ["create_app"]

_HERE = Path(__file__).parent
TEMPLATE_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

NAV: tuple[tuple[str, str, str], ...] = (
    ("overview", "/", "Overview"),
    ("profile", "/profile", "Profile"),
    ("answers", "/answers", "Screening answers"),
    ("resume", "/resume", "Resume & experiences"),
    ("search", "/search", "Search"),
    ("opportunities", "/opportunities", "Opportunities"),
    ("applications", "/applications", "Applications"),
    ("runs", "/runs", "Runs"),
    ("settings", "/settings", "Settings"),
)
# (name, label, input kind, help). "tri" is a yes / no / not-answered choice.
_Field = tuple[str, str, str, str]
PROFILE_SECTIONS: tuple[tuple[str, tuple[_Field, ...]], ...] = (
    (
        "Contact",
        (
            ("first_name", "First name", "text", ""),
            ("last_name", "Last name", "text", ""),
            ("preferred_name", "Preferred name", "text", "Optional."),
            ("pronouns", "Pronouns", "text", "Optional."),
            ("email", "Email", "email", ""),
            ("phone", "Phone", "tel", "At least 10 digits."),
            ("phone_country", "Phone country", "text", ""),
        ),
    ),
    (
        "Address",
        (
            ("address_line1", "Street address", "text", ""),
            ("address_line2", "Address line 2", "text", "Optional."),
            ("city", "City", "text", ""),
            ("state", "State / region", "text", ""),
            ("postal_code", "Postal code", "text", ""),
            ("country", "Country", "text", ""),
        ),
    ),
    (
        "Links",
        (
            ("linkedin_url", "LinkedIn URL", "url", "Optional."),
            ("github_url", "GitHub URL", "url", "Optional."),
            ("portfolio_url", "Portfolio / website URL", "url", "Optional."),
        ),
    ),
    (
        "Education",
        (
            ("school", "School", "text", ""),
            ("degree", "Degree", "text", "For example: Bachelor of Science."),
            ("major", "Major", "text", ""),
            ("minor", "Minor", "text", "Optional."),
            ("gpa", "GPA", "text", "Optional."),
            ("education_start_date", "Education start (YYYY-MM)", "text", "Optional."),
            ("graduation_date", "Graduation (YYYY-MM)", "text", "For example 2028-05."),
        ),
    ),
    (
        "Work eligibility and availability",
        (
            (
                "authorized_to_work_us",
                "Authorized to work in the US",
                "tri",
                "Answered from here, never guessed.",
            ),
            ("requires_sponsorship", "Requires visa sponsorship now or in the future", "tri", ""),
            ("willing_to_relocate", "Willing to relocate", "tri", ""),
            ("is_18_or_older", "18 years or older", "tri", ""),
            (
                "available_start_date",
                "Available from (YYYY-MM-DD)",
                "text",
                "For example 2027-05-17.",
            ),
            ("available_end_date", "Available until (YYYY-MM-DD)", "text", "Optional."),
            ("referral_source", "How did you hear about us", "text", ""),
        ),
    ),
)
EEO_FIELDS: tuple[_Field, ...] = (
    ("gender", "Gender", "eeo", ""),
    ("race_ethnicity", "Race / ethnicity", "eeo", ""),
    ("hispanic_latino", "Hispanic / Latino", "eeo", ""),
    ("veteran_status", "Veteran status", "eeo", ""),
    ("disability_status", "Disability status", "eeo", ""),
)
COMMON_TIMEZONES = (
    "America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles", "America/Phoenix",
    "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "America/Vancouver", "Europe/London",
    "Europe/Berlin", "Europe/Paris", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo",
    "Australia/Sydney", "UTC",
)  # fmt: skip
COMMON_INTENTS = (
    "previously_employed_here", "known_employee", "felony_conviction", "security_clearance",
    "non_compete", "export_control_us_person", "accommodation_needed", "salary_expectation",
    "drug_test_consent", "background_check_consent",
)  # fmt: skip
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ------------------------------------------------------------------------------------------ templates


def _localdt(value: Any, tz_name: str = "UTC") -> str:
    """``YYYY-MM-DD HH:MM`` in ``tz_name`` for a datetime or ISO string; ``""`` when empty."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    try:
        zone = ZoneInfo(tz_name)
    except (KeyError, ValueError, OSError):
        zone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _safe_url(value: Any) -> str:
    """The URL if it is plain http(s), else ``""`` (blocks javascript:, data: and friends in links)."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme in {"http", "https"} and parts.netloc else ""


def _make_templates() -> Jinja2Templates:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(default=True, default_for_string=True),
        undefined=jinja2.StrictUndefined,
    )
    env.filters["localdt"] = _localdt
    env.filters["safe_url"] = _safe_url
    env.filters["reason_label"] = lambda value: REASON_LABELS.get(str(value), str(value))
    env.filters["status_label"] = lambda value: str(value).replace("_", " ")
    return Jinja2Templates(env=env)


# ------------------------------------------------------------------------------------------ app factory


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(("/api/", "/healthz"))


def create_app(
    runtime: DashboardRuntime,
    *,
    allowed_hosts: Iterable[str] = (),
    session_secret: bytes | None = None,
) -> FastAPI:
    """Build the dashboard app. ``allowed_hosts`` adds Host names to the loopback allow-list."""
    app = FastAPI(title="AutoApply dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    templates = _make_templates()

    def read_config() -> AppConfig:
        try:
            return runtime.read_config()
        except (ValueError, OSError) as exc:
            raise api_error_for_config(exc) from exc

    def render(
        request: Request, name: str, page: str, config: AppConfig, **context: Any
    ) -> Response:
        tz, _ = timezone_or_utc(config)
        base: dict[str, Any] = {
            "page": page,
            "nav": NAV,
            "csrf_token": request.state.csrf_token,
            "stop_active": runtime.stop_active,
            "risky_platforms": risky_platforms(config),
            "risk_message": AUTOMATION_RISK_MESSAGE,
            "tz": tz,
        }
        return templates.TemplateResponse(request, name, {**base, **context})

    # -- errors ------------------------------------------------------------------------------------
    def error_response(
        request: Request, status: int, code: str, message: str, body: dict[str, Any]
    ) -> Response:
        if _wants_json(request):
            return JSONResponse(body, status_code=status)
        html = templates.TemplateResponse(
            request,
            "error.html",
            {
                "page": "error",
                "nav": NAV,
                "csrf_token": request.state.csrf_token,
                "stop_active": False,
                "risky_platforms": [],
                "risk_message": "",
                "tz": "UTC",
                "status": status,
                "code": code,
                "message": message,
            },  # fmt: skip
            status_code=status,
        )
        return html

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> Response:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.body())

    @app.exception_handler(FeatureUnavailableError)
    async def _unavailable(request: Request, exc: FeatureUnavailableError) -> Response:
        api = ApiError(501, "feature_unavailable", str(exc))
        return error_response(request, 501, api.code, api.message, api.body())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, f"http_{exc.status_code}"
        )
        message = "Not found." if exc.status_code == 404 else str(exc.detail)
        body = {"code": code, "message": message, "detail": message}
        response = error_response(request, exc.status_code, code, message, body)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        errors = [
            {"loc": list(e["loc"]), "msg": str(e["msg"]), "type": str(e["type"])}
            for e in exc.errors()
        ]
        message = "The submitted data is not valid."
        body = {"code": "validation_error", "message": message, "detail": errors, "errors": errors}
        return error_response(request, 422, "validation_error", message, body)

    # -- API ---------------------------------------------------------------------------------------
    app.include_router(build_api_router(runtime))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/files/{kind}/{path:path}")
    def files(kind: str, path: str) -> Response:
        resolved = resolve_served_file(runtime.paths, kind, path)
        if resolved is None:
            raise StarletteHTTPException(404)
        return serve_file(resolved)

    # -- pages -------------------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> Response:
        config = read_config()
        return render(request, "overview.html", "overview", config, state=build_state(runtime, config),
                      modes=[m.value for m in RunMode], weekdays=WEEKDAYS)  # fmt: skip

    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request) -> Response:
        config = read_config()
        return render(
            request, "profile.html", "profile", config,
            profile=config.profile.model_dump(mode="json"), sections=PROFILE_SECTIONS,
            eeo_fields=EEO_FIELDS, attest=config.apply.attestations_authorized,
        )  # fmt: skip

    @app.get("/answers", response_class=HTMLResponse)
    def answers(request: Request) -> Response:
        config = read_config()
        return render(
            request, "answers.html", "answers", config,
            answers=[answer_view(a) for a in runtime.repo.list_answers()],
            pending=[pending_view(q) for q in runtime.repo.list_pending_questions()],
            intents=COMMON_INTENTS,
        )  # fmt: skip

    @app.get("/resume", response_class=HTMLResponse)
    def resume(request: Request) -> Response:
        config = read_config()
        kb_json = "null"
        kb_error = ""
        meta: dict[str, Any] = {"warnings": []}
        try:
            kb = runtime.load_knowledge_base()
            kb_json = json.dumps(kb.model_dump(mode="json"))
            files_present = _experience_files(runtime)
            if files_present:
                meta["warnings"] = [
                    "Experience files in data/profile/experiences take precedence over the saved "
                    "knowledge base, so edits saved here are not used while those files exist."
                ]
        except (FeatureUnavailableError, OSError, ValueError) as exc:
            kb_error = f"The knowledge base could not be loaded: {exc}"
        return render(
            request, "resume.html", "resume", config,
            resume=resume_view(runtime, config), kb_json=kb_json, kb_error=kb_error, meta=meta,
        )  # fmt: skip

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request) -> Response:
        config = read_config()
        return render(
            request, "search.html", "search", config,
            search=config.search.model_dump(mode="json"),
            platforms=config.platforms.model_dump(mode="json"),
            boards=config.boards.model_dump(mode="json"),
            workbook=config.workbook.model_dump(mode="json"),
        )  # fmt: skip

    @app.get("/opportunities", response_class=HTMLResponse)
    def opportunities(request: Request) -> Response:
        config = read_config()
        params = request.query_params
        problems: list[str] = []
        min_score = _float_param(params.get("min_score"), "min_score", 0, 100, problems)
        limit = int(_float_param(params.get("limit"), "limit", 1, 200, problems) or 50)
        offset = int(_float_param(params.get("offset"), "offset", 0, 10**9, problems) or 0)
        status = (params.get("status") or "").strip().lower()
        source = (params.get("source") or "").strip().lower()
        search_text = (params.get("search") or "").strip()[:200]
        passed_only = params.get("passed_only") in {"1", "true", "on"}
        if status and status not in {s.value for s in ApplicationStatus} | {
            "unapplied",
            "open",
            "closed",
        }:
            problems.append("Ignored an unknown status filter.")
            status = ""
        if source and source not in {s.value for s in OpportunitySource}:
            problems.append("Ignored an unknown source filter.")
            source = ""
        filters: dict[str, Any] = {
            "min_score": min_score, "status": status or None, "source": source or None,
            "search": search_text or None, "passed_only": passed_only,
        }  # fmt: skip
        repo = runtime.repo
        rows = repo.list_opportunities(**filters, limit=limit, offset=offset)
        total = repo.count_opportunities(**filters)
        query = {
            k: v for k, v in {
                "min_score": params.get("min_score") if min_score is not None else "",
                "status": status, "source": source, "search": search_text,
                "passed_only": "1" if passed_only else "", "limit": str(limit),
            }.items() if v
        }  # fmt: skip
        return render(
            request, "opportunities.html", "opportunities", config,
            items=[opportunity_view(op, repo.latest_application(op.id)) for op in rows],
            total=total, limit=limit, offset=offset, problems=problems,
            form={"min_score": params.get("min_score", ""), "status": status, "source": source,
                  "search": search_text, "passed_only": passed_only},
            statuses=[s.value for s in ApplicationStatus],
            sources=[s.value for s in OpportunitySource],
            prev_url=("/opportunities?" + urlencode({**query, "offset": max(0, offset - limit)}))
            if offset > 0 else "",
            next_url=("/opportunities?" + urlencode({**query, "offset": offset + limit}))
            if offset + limit < total else "",
        )  # fmt: skip

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request) -> Response:
        config = read_config()
        params = request.query_params
        status = (params.get("status") or "").strip().lower()
        valid = {s.value for s in ApplicationStatus}
        if status not in valid:
            status = ""
        problems: list[str] = []
        limit = int(_float_param(params.get("limit"), "limit", 1, 200, problems) or 50)
        offset = int(_float_param(params.get("offset"), "offset", 0, 10**9, problems) or 0)
        repo = runtime.repo
        rows = repo.list_applications(status=status or None, limit=limit, offset=offset)
        cache: dict[str, Any] = {}
        items = []
        for row in rows:
            if row.opportunity_id not in cache:
                cache[row.opportunity_id] = repo.get_opportunity(row.opportunity_id)
            items.append(application_view(runtime, row, cache[row.opportunity_id]))
        total = repo.count_applications(status=status or None)
        base = {"status": status} if status else {}
        return render(
            request, "applications.html", "applications", config,
            items=items, total=total, limit=limit, offset=offset, status=status,
            statuses=sorted(valid),
            prev_url=("/applications?" + urlencode({**base, "offset": max(0, offset - limit)}))
            if offset > 0 else "",
            next_url=("/applications?" + urlencode({**base, "offset": offset + limit}))
            if offset + limit < total else "",
        )  # fmt: skip

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> Response:
        config = read_config()
        return render(request, "runs.html", "runs", config,
                      runs=[run_view(r) for r in runtime.repo.list_runs(50)])  # fmt: skip

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> Response:
        config = read_config()
        return render(
            request, "settings.html", "settings", config,
            cfg=config.model_dump(mode="json"), modes=[m.value for m in RunMode],
            timezones=COMMON_TIMEZONES, weekdays=WEEKDAYS,
        )  # fmt: skip

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_middleware(
        SecurityMiddleware, signer=SessionSigner(session_secret), allowed_hosts=tuple(allowed_hosts)
    )
    return app


def _experience_files(runtime: DashboardRuntime) -> list[str]:
    try:
        return sorted(
            p.name
            for p in runtime.paths.experiences_dir.iterdir()
            if p.suffix.lower() in {".json", ".md"}
        )
    except OSError:
        return []


def _float_param(
    raw: str | None, name: str, lo: float, hi: float, problems: list[str]
) -> float | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        problems.append(f"Ignored an invalid {name} filter.")
        return None
    if not lo <= value <= hi:
        problems.append(f"Ignored an out-of-range {name} filter.")
        return None
    return value
