"""JSON API of the dashboard (docs/SPEC.md section 5.13). Every HTML page is a thin view over these routes.

Conventions: list endpoints return ``{"items": [...], "total": n, "limit": n, "offset": n}``; profile,
settings and search are partial-update PUTs that return the same shape as their GET; errors use the envelope
described in ``errors.py``. ``POST /api/resume`` is overloaded as the brief requires: a multipart body uploads
the resume PDF, anything else clears the STOP file. Unambiguous aliases exist for both
(``POST /api/resume/upload`` and ``POST /api/unstop``) and are what the web UI calls.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC
from pathlib import Path, PurePosixPath
from typing import IO, Any, Literal

from fastapi import APIRouter, Body, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from autoapply.config import AppConfig, resolve_resume_path, save_config
from autoapply.contracts import LLMError
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.errors import ApiError, FieldError, validation_error
from autoapply.dashboard.security import CSRF_HEADER, MAX_UPLOAD_BYTES
from autoapply.dashboard.validation import (
    SETTINGS_KEYS,
    api_error_for_config,
    check_answer_fields,
    clean_kb_payload,
    patch_profile,
    patch_search,
    patch_settings,
)
from autoapply.dashboard.views import (
    answer_view,
    application_view,
    build_state,
    dry_run_count,
    opportunity_view,
    pending_view,
    readiness_for,
    readiness_view,
    resume_view,
    run_view,
)
from autoapply.db import NotFoundError
from autoapply.models import (
    ApplicationStatus,
    OpportunitySource,
    RunMode,
    ScreeningAnswer,
)
from autoapply.secrets import redact

__all__ = ["build_api_router"]

_PDF_MAGIC = b"%PDF-"
_STATUS_FILTERS = {s.value for s in ApplicationStatus}
_OPPORTUNITY_STATUS_FILTERS = _STATUS_FILTERS | {"unapplied", "none", "open", "closed"}
_SOURCES = {s.value for s in OpportunitySource}
_WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}


def _query_error(name: str, message: str) -> ApiError:
    return validation_error([FieldError(("query", name), message)])


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise validation_error([FieldError(("body",), "Send a JSON object.", "dict_type")])
    return payload


def _safe_message(runtime: DashboardRuntime, exc: Exception, limit: int = 300) -> str:
    """Short, redacted text of an exception for showing to the user (never a key, never a traceback)."""
    first = (str(exc).strip().splitlines() or [""])[0]
    return redact(first, runtime.openai_key_resolution().key)[:limit]


def _report_dict(report: object) -> dict[str, Any]:
    if isinstance(report, BaseModel):
        return dict(report.model_dump(mode="json"))
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        return dict(jsonable_encoder(to_dict()))
    if isinstance(report, dict):
        return dict(jsonable_encoder(report))
    return {"report": str(report)}


def _store_pdf(source: IO[bytes], destination: Path) -> int:
    """Validate and atomically store an uploaded PDF at ``destination``; returns its size in bytes."""
    source.seek(0)
    head = source.read(len(_PDF_MAGIC))
    if head != _PDF_MAGIC:
        raise ApiError(
            415, "not_a_pdf", "The file does not look like a PDF (it must start with %PDF-)."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="resume.", suffix=".tmp", dir=destination.parent)
    size = len(head)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(head)
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ApiError(
                        413,
                        "file_too_large",
                        f"The PDF is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        Path(tmp_name).replace(destination)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return size


def build_api_router(runtime: DashboardRuntime) -> APIRouter:  # noqa: C901 - one flat route table
    router = APIRouter(prefix="/api")

    def read_config() -> AppConfig:
        try:
            return runtime.read_config()
        except (ValueError, OSError) as exc:
            raise api_error_for_config(exc) from exc

    def settings_view(config: AppConfig) -> dict[str, Any]:
        return dict(config.model_dump(mode="json", include=set(SETTINGS_KEYS)))

    # ------------------------------------------------------------------------------ session / state
    @router.get("/csrf")
    def csrf(request: Request) -> dict[str, str]:
        return {"csrf_token": request.state.csrf_token, "header": CSRF_HEADER}

    @router.get("/state")
    def state() -> dict[str, Any]:
        return build_state(runtime, read_config())

    # ------------------------------------------------------------------------------ profile
    @router.get("/profile")
    def get_profile() -> dict[str, Any]:
        return dict(read_config().profile.model_dump(mode="json"))

    @router.put("/profile")
    def put_profile(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        with runtime.config_lock:
            updated = patch_profile(read_config(), payload)
            save_config(runtime.paths, updated)
        return dict(updated.profile.model_dump(mode="json"))

    # ------------------------------------------------------------------------------ settings
    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        return settings_view(read_config())

    @router.put("/settings")
    def put_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        body = dict(_object(payload))
        acknowledged = body.pop("acknowledge_no_dry_run", False)
        if not isinstance(acknowledged, bool):
            raise validation_error(
                [FieldError(("acknowledge_no_dry_run",), "Must be true or false.")]
            )
        with runtime.config_lock:
            config = read_config()
            candidate = patch_settings(config, body)
            if candidate.schedule.enabled and not config.schedule.enabled:
                report = readiness_for(runtime, candidate)
                if not report.ok:
                    raise ApiError(
                        409,
                        "not_ready",
                        "The schedule cannot be enabled until everything in the readiness checklist is fixed.",
                        issues=readiness_view(report)["issues"],
                    )
                if (
                    candidate.mode is RunMode.FULL_AUTO
                    and not acknowledged
                    and dry_run_count(runtime) == 0
                ):
                    raise ApiError(
                        409,
                        "dry_run_recommended",
                        "No dry run has completed successfully yet. Run a dry run first, or confirm that you "
                        "want to schedule real submissions anyway (acknowledge_no_dry_run).",
                    )
            save_config(runtime.paths, candidate)
        return settings_view(candidate)

    # ------------------------------------------------------------------------------ search profile
    @router.get("/search")
    def get_search() -> dict[str, Any]:
        return dict(read_config().search.model_dump(mode="json"))

    @router.put("/search")
    def put_search(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        with runtime.config_lock:
            updated = patch_search(read_config(), payload)
            save_config(runtime.paths, updated)
        return dict(updated.search.model_dump(mode="json"))

    # ------------------------------------------------------------------------------ run control
    @router.post("/run", status_code=202)
    def run(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        body = payload or {}
        raw_mode = _object(body).get("mode")
        mode: RunMode | None = None
        if raw_mode not in (None, ""):
            try:
                mode = RunMode(raw_mode)
            except ValueError:
                raise validation_error(
                    [FieldError(("mode",), "Choose full_auto, dry_run or discover_only.", "enum")]
                ) from None
        config = read_config()
        effective = mode or config.mode
        if runtime.controller.is_running():
            raise ApiError(409, "already_running", "A run is already in progress.")
        if effective is not RunMode.DISCOVER_ONLY:
            if runtime.stop_active:
                raise ApiError(
                    409, "stop_active", "The STOP switch is on. Press Resume before starting a run."
                )
            report = readiness_for(runtime, config, mode=effective)
            if not report.ok:
                raise ApiError(
                    409,
                    "not_ready",
                    "The applier is not ready to run. Fix the items in the readiness checklist first.",
                    issues=readiness_view(report)["issues"],
                )
        if not runtime.controller.run_now(mode, trigger="manual"):
            raise ApiError(409, "already_running", "A run is already in progress.")
        return {
            "accepted": True,
            "mode": effective.value,
            "running": runtime.controller.is_running(),
        }

    @router.post("/stop")
    def stop() -> dict[str, Any]:
        try:
            runtime.paths.root.mkdir(parents=True, exist_ok=True)
            stamp = runtime.clock.now().astimezone(UTC).isoformat()
            runtime.paths.stop_file.write_text(
                f"Stopped from the dashboard at {stamp}\n", encoding="utf-8"
            )
        except OSError as exc:
            raise ApiError(500, "stop_file_error", "Could not write the STOP file.") from exc
        runtime.controller.request_stop()
        return {"stop_file": True, "running": runtime.controller.is_running()}

    def clear_stop() -> dict[str, Any]:
        try:
            runtime.paths.stop_file.unlink(missing_ok=True)
        except OSError as exc:
            raise ApiError(500, "stop_file_error", "Could not remove the STOP file.") from exc
        return {"stop_file": runtime.stop_active, "resumed": not runtime.stop_active}

    @router.post("/unstop")
    def unstop() -> dict[str, Any]:
        return clear_stop()

    # ------------------------------------------------------------------------------ opportunities
    @router.get("/opportunities")
    def opportunities(
        min_score: float | None = Query(default=None, ge=0, le=100),
        passed_only: bool = False,
        status: str | None = Query(default=None, max_length=40),
        source: str | None = Query(default=None, max_length=40),
        search: str | None = Query(default=None, max_length=200),
        order: Literal["score_desc", "seen_desc", "first_seen_desc"] = "score_desc",
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        status = (status or "").strip().lower() or None
        source = (source or "").strip().lower() or None
        if status is not None and status not in _OPPORTUNITY_STATUS_FILTERS:
            raise _query_error("status", f"Unknown status {status!r}.")
        if source is not None and source not in _SOURCES:
            raise _query_error("source", f"Unknown source {source!r}.")
        filters: dict[str, Any] = {
            "min_score": min_score, "status": status, "source": source,
            "search": search, "passed_only": passed_only,
        }  # fmt: skip
        repo = runtime.repo
        items = repo.list_opportunities(**filters, limit=limit, offset=offset, order=order)
        return {
            "items": [opportunity_view(op, repo.latest_application(op.id)) for op in items],
            "total": repo.count_opportunities(**filters),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/opportunities/{opportunity_id}")
    def opportunity(opportunity_id: str) -> dict[str, Any]:
        op = runtime.repo.get_opportunity(opportunity_id)
        if op is None:
            raise ApiError(404, "not_found", "Unknown opportunity.")
        apps = runtime.repo.list_applications(opportunity_id=opportunity_id)
        view = opportunity_view(op, apps[0] if apps else None, full=True)
        view["applications"] = [application_view(runtime, a, op) for a in apps]
        return view

    # ------------------------------------------------------------------------------ applications
    @router.get("/applications")
    def applications(
        status: str | None = Query(default=None, max_length=120),
        opportunity_id: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        wanted = [s.strip().lower() for s in (status or "").split(",") if s.strip()]
        for value in wanted:
            if value not in _STATUS_FILTERS:
                raise _query_error("status", f"Unknown status {value!r}.")
        repo = runtime.repo
        chosen: list[str] | None = wanted or None
        rows = repo.list_applications(
            status=chosen, opportunity_id=opportunity_id or None, limit=limit, offset=offset
        )
        cache: dict[str, Any] = {}
        items = []
        for row in rows:
            if row.opportunity_id not in cache:
                cache[row.opportunity_id] = repo.get_opportunity(row.opportunity_id)
            items.append(application_view(runtime, row, cache[row.opportunity_id]))
        return {
            "items": items,
            "total": repo.count_applications(status=chosen, opportunity_id=opportunity_id or None),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/applications/{application_id}")
    def application(application_id: int) -> dict[str, Any]:
        row = runtime.repo.get_application(application_id)
        if row is None:
            raise ApiError(404, "not_found", "Unknown application.")
        return application_view(
            runtime, row, runtime.repo.get_opportunity(row.opportunity_id), full=True
        )

    @router.post("/applications/{opportunity_id}/mark-applied")
    def mark_applied(opportunity_id: str) -> dict[str, Any]:
        try:
            row = runtime.repo.mark_manually_applied(opportunity_id)
        except NotFoundError:
            raise ApiError(404, "not_found", "Unknown opportunity.") from None
        return application_view(runtime, row, runtime.repo.get_opportunity(opportunity_id))

    @router.get("/runs")
    def runs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        items = runtime.repo.list_runs(limit)
        return {"items": [run_view(r) for r in items], "limit": limit}

    # ------------------------------------------------------------------------------ screening answers
    @router.get("/answers")
    def answers() -> dict[str, Any]:
        items = runtime.repo.list_answers()
        return {"items": [answer_view(a) for a in items], "total": len(items)}

    @router.post("/answers")
    def create_answer(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        fields = check_answer_fields(_object(payload), require_all=True)
        question = fields.get("question", "")
        stored = _save_answer(
            ScreeningAnswer(
                intent=fields.get("intent"),
                question=question or str(fields.get("intent", "")).replace("_", " "),
                answer=fields["answer"],
                answer_kind=fields.get("answer_kind", "text"),
                source="user",
            )
        )
        return answer_view(stored)

    def _save_answer(answer: ScreeningAnswer) -> ScreeningAnswer:
        try:
            return runtime.repo.upsert_answer(answer)
        except NotFoundError:
            raise ApiError(404, "not_found", "Unknown saved answer.") from None
        except ValueError as exc:
            raise validation_error([FieldError(("intent",), str(exc))]) from None

    @router.put("/answers/{answer_id}")
    def update_answer(answer_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        fields = check_answer_fields(_object(payload), require_all=False)
        current = next((a for a in runtime.repo.list_answers() if a.id == answer_id), None)
        if current is None:
            raise ApiError(404, "not_found", "Unknown saved answer.")
        merged = current.model_copy(update={**fields, "source": "user", "question_norm": ""})
        if "question" not in fields:
            merged = merged.model_copy(update={"question_norm": current.question_norm})
        return answer_view(_save_answer(merged))

    @router.delete("/answers/{answer_id}")
    def delete_answer(answer_id: int) -> dict[str, Any]:
        if not runtime.repo.delete_answer(answer_id):
            raise ApiError(404, "not_found", "Unknown saved answer.")
        return {"deleted": True, "id": answer_id}

    @router.get("/pending-questions")
    def pending_questions(include_resolved: bool = False) -> dict[str, Any]:
        items = runtime.repo.list_pending_questions(unresolved_only=not include_resolved)
        return {"items": [pending_view(q) for q in items], "total": len(items)}

    @router.post("/pending-questions/{question_id}/resolve")
    def resolve_question(question_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        body = _object(payload)
        fields = check_answer_fields({"answer": body.get("answer")}, require_all=False)
        if "answer" not in fields:
            raise validation_error([FieldError(("answer",), "An answer is required.")])
        try:
            resolved = runtime.repo.resolve_pending_question(question_id, fields["answer"])
        except NotFoundError:
            raise ApiError(404, "not_found", "Unknown pending question.") from None
        except ValueError as exc:
            raise validation_error([FieldError(("answer",), str(exc))]) from None
        saved = runtime.repo.find_answer(question_norm=resolved.question)
        return {
            "question": pending_view(resolved),
            "saved_answer": answer_view(saved) if saved else None,
        }

    # ------------------------------------------------------------------------------ resume
    @router.get("/resume")
    def get_resume() -> dict[str, Any]:
        return resume_view(runtime, read_config())

    async def upload_resume(request: Request) -> dict[str, Any]:
        form = await request.form(max_files=2, max_fields=8, max_part_size=64 * 1024)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise validation_error(
                [FieldError(("file",), "Send the PDF as a file field named 'file'.")]
            )
        name = PurePosixPath((upload.filename or "").replace("\\", "/")).name
        if not name.lower().endswith(".pdf"):
            raise ApiError(415, "not_a_pdf", "Only .pdf files can be uploaded.")
        await run_in_threadpool(_store_pdf, upload.file, runtime.paths.resume_file)
        await upload.close()

        def remember_path() -> AppConfig:
            with runtime.config_lock:
                config = read_config().model_copy(deep=True)
                config.profile.fallback_resume_path = str(runtime.paths.resume_file.absolute())
                save_config(runtime.paths, config)
                return config

        config = await run_in_threadpool(remember_path)
        return resume_view(runtime, config)

    @router.post("/resume")
    async def post_resume(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return await upload_resume(request)
        return clear_stop()

    @router.post("/resume/upload")
    async def post_resume_upload(request: Request) -> dict[str, Any]:
        return await upload_resume(request)

    # ------------------------------------------------------------------------------ knowledge base
    def kb_meta() -> dict[str, Any]:
        directory = runtime.paths.experiences_dir
        try:
            files = sorted(
                p.name for p in directory.iterdir() if p.suffix.lower() in {".json", ".md"}
            )
        except OSError:
            files = []
        warnings = (
            [
                "Experience files in data/profile/experiences take precedence over the saved knowledge "
                "base, so edits saved here are not used while those files exist."
            ]
            if files
            else []
        )
        return {"experience_files": files, "shadowed": bool(files), "warnings": warnings}

    @router.get("/kb")
    def get_kb() -> dict[str, Any]:
        kb = runtime.load_knowledge_base()
        return {**kb.model_dump(mode="json"), "meta": kb_meta()}

    @router.put("/kb")
    def put_kb(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        kb = clean_kb_payload(payload)
        runtime.save_knowledge_base(kb)
        return {**kb.model_dump(mode="json"), "meta": kb_meta()}

    @router.post("/kb/from-resume")
    def kb_from_resume() -> dict[str, Any]:
        config = read_config()
        resume = resolve_resume_path(config, runtime.paths)
        if resume is None:
            raise ApiError(409, "resume_missing", "Upload your resume PDF first.")
        try:
            llm = runtime.make_llm(config)
            if llm is None:
                raise ApiError(
                    409,
                    "llm_unavailable",
                    "No OpenAI key is configured. Run set_openai_key.ps1, restart the dashboard and try again.",
                )
            kb = runtime.build_knowledge_base(resume, llm)
        except LLMError as exc:
            raise ApiError(
                502,
                "llm_error",
                "The AI service could not structure the resume. Check your OpenAI key, quota and "
                f"network, then try again. ({_safe_message(runtime, exc, 200)})",
            ) from None
        except ValueError as exc:
            raise ApiError(
                422,
                "resume_unreadable",
                f"The resume could not be read: {_safe_message(runtime, exc, 200)}",
            ) from None
        return {"proposed": True, "saved": False, "kb": kb.model_dump(mode="json")}

    # ------------------------------------------------------------------------------ workbook
    @router.post("/workbook/inspect")
    def inspect_workbook(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        config = read_config()
        raw = _object(payload or {}).get("path")
        if raw is not None and not isinstance(raw, str):
            raise validation_error([FieldError(("path",), "Must be text.")])
        chosen = (raw or "").strip() or (config.workbook.path or "").strip()
        if not chosen or "\x00" in chosen:
            raise ApiError(
                422, "workbook_path_missing", "Enter the path of your .xlsx workbook first."
            )
        path = Path(chosen).expanduser()
        if path.suffix.lower() not in _WORKBOOK_SUFFIXES:
            raise ApiError(422, "workbook_not_xlsx", "The workbook must be an .xlsx or .xlsm file.")
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if not is_file:
            raise ApiError(422, "workbook_missing", "No workbook file exists at that path.")
        try:
            report = runtime.inspect_workbook_file(path, config)
        except (ValueError, OSError) as exc:
            raise ApiError(
                422,
                "workbook_unreadable",
                f"The workbook could not be read: {_safe_message(runtime, exc, 200)}",
            ) from None
        return _report_dict(report)

    return router
