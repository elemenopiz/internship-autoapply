"""Application configuration and on-disk layout. CONTRACT FILE: owned by the orchestrator.

``data/config.json`` holds settings and the profile fields, never secrets. The OpenAI key comes from the
``OPENAI_API_KEY`` environment variable (or the OS credential store) and is NEVER written here.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoapply.models import Profile, RunMode, SearchProfile

DATA_DIR_ENV = "AUTOAPPLY_DATA_DIR"
OPENAI_KEY_ENV = "OPENAI_API_KEY"


class _Cfg(BaseModel):
    # extra="allow": hand-edited / newer config files round-trip without losing unknown keys.
    model_config = ConfigDict(extra="allow", validate_assignment=True, str_strip_whitespace=True)


class ScheduleConfig(_Cfg):
    enabled: bool = (
        False  # README: disabled until the profile is complete; enabled from the dashboard
    )
    run_times: list[str] = Field(default_factory=lambda: ["09:30"])  # local HH:MM (config.timezone)
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon=0
    jitter_minutes: int = 10


class PlatformsConfig(_Cfg):
    workbook: bool = True
    greenhouse: bool = True
    lever: bool = True
    ashby: bool = True
    linkedin: bool = False  # opt-in: discovery only, guarded (see docs/SPEC.md section 7)
    indeed: bool = False  # opt-in: discovery only, guarded


class WorkbookConfig(_Cfg):
    path: str | None = None
    sheet: str | None = None  # default: best fuzzy match for "verified opportunities"
    column_map: dict[str, str] = Field(
        default_factory=dict
    )  # canonical field -> header text override


class BoardsConfig(_Cfg):
    """Company board tokens for the public Greenhouse / Lever / Ashby job-board APIs."""

    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


class LLMConfig(_Cfg):
    provider: Literal["openai"] = "openai"
    model: str = "gpt-4.1-mini"
    timeout_s: int = 60
    max_retries: int = 3
    max_calls_per_application: int = 15


class EmailVerificationConfig(_Cfg):
    enabled: bool = False
    imap_host: str | None = None
    imap_port: int = 993
    username: str | None = None  # password lives in the credential store: service "autoapply:imap"
    mailbox: str = "INBOX"
    timeout_s: int = 180


class ApplyConfig(_Cfg):
    headless: bool = True
    nav_timeout_s: int = 45
    attempt_timeout_s: int = 600
    max_attempts_per_job: int = 3
    max_attempts_per_run: int = 15
    min_delay_s: int = 20  # polite pacing between applications
    max_delay_s: int = 90
    generic_portal: bool = True  # LLM-assisted filler for employer-specific portals
    attestations_authorized: bool = False  # user authorises ticking certification/consent boxes
    screenshots: Literal["never", "on_failure", "always"] = "on_failure"
    email: EmailVerificationConfig = Field(default_factory=EmailVerificationConfig)


class AppConfig(_Cfg):
    mode: RunMode = RunMode.FULL_AUTO
    daily_cap: int = 5  # counted from the local DB by calendar day in ``timezone``
    timezone: str = "America/Chicago"
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    workbook: WorkbookConfig = Field(default_factory=WorkbookConfig)
    boards: BoardsConfig = Field(default_factory=BoardsConfig)
    search: SearchProfile = Field(default_factory=SearchProfile)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    profile: Profile = Field(default_factory=Profile)


@dataclass(frozen=True)
class AppPaths:
    """Where everything lives on disk. ``root`` is the data directory."""

    root: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> AppPaths:
        source = os.environ if env is None else env
        override = source.get(DATA_DIR_ENV)
        return cls(Path(override).expanduser() if override else (cwd or Path.cwd()) / "data")

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def db_file(self) -> Path:
        return self.root / "autoapply.db"

    @property
    def profile_dir(self) -> Path:
        return self.root / "profile"

    @property
    def experiences_dir(self) -> Path:
        return self.profile_dir / "experiences"

    @property
    def knowledge_base_file(self) -> Path:
        return self.profile_dir / "knowledge_base.json"

    @property
    def resume_file(self) -> Path:
        return self.profile_dir / "resume.pdf"

    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def browser_profile_dir(self) -> Path:
        return self.root / "browser_profile"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def stop_file(self) -> Path:
        return self.root / "STOP"

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.profile_dir,
            self.experiences_dir,
            self.documents_dir,
            self.artifacts_dir,
            self.browser_profile_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def load_config(paths: AppPaths) -> AppConfig:
    """Load ``config.json``; a missing file yields defaults. Invalid JSON raises ``ValueError``."""
    if not paths.config_file.exists():
        return AppConfig()
    try:
        raw: Any = json.loads(paths.config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{paths.config_file} is not valid JSON: {exc}") from exc
    return AppConfig.model_validate(raw)


def save_config(paths: AppPaths, config: AppConfig) -> None:
    """Atomically write ``config.json`` (temp file + replace) so a crash never leaves a torn file."""
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=paths.root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        Path(tmp_name).replace(paths.config_file)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def resolve_resume_path(config: AppConfig, paths: AppPaths) -> Path | None:
    """The user's own resume PDF: ``profile.fallback_resume_path`` first, else the uploaded copy."""
    candidates: list[Path] = []
    if config.profile.fallback_resume_path:
        candidates.append(Path(config.profile.fallback_resume_path).expanduser())
    candidates.append(paths.resume_file)
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate
    return None
