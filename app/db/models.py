from __future__ import annotations

from pydantic import BaseModel


class Application(BaseModel):
    id: int
    external_id: str
    platform: str
    job_title: str
    company: str
    location: str | None
    salary: str | None
    apply_url: str
    match_score: int
    resume_path: str | None
    cover_letter_path: str | None
    cover_letter_text: str | None
    description_path: str | None
    status: str
    error_message: str | None
    applied_at: str
    updated_at: str
    notes: str | None
    attempts: int = 1


class ResumeVersion(BaseModel):
    id: int
    application_id: int
    job_title: str
    company: str
    resume_md_path: str
    resume_pdf_path: str
    match_score: int | None
    llm_provider: str | None
    llm_model: str | None
    is_favorite: bool = False
    created_at: str


class FeedEvent(BaseModel):
    id: int
    event_type: str
    job_title: str | None
    company: str | None
    platform: str | None
    message: str | None
    created_at: str
