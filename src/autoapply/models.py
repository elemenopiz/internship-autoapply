"""Shared domain models. CONTRACT FILE: owned by the orchestrator.

Workers must not edit this file. If a model needs a new field, report a CONTRACT-REQUEST in your result and
work around it locally (e.g. with a module-private model). See docs/SPEC.md section 3.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)

from autoapply.normalize import fingerprint, opportunity_id, parse_year_month


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True, str_strip_whitespace=True)


# --------------------------------------------------------------------------------------------- enums


class RunMode(StrEnum):
    FULL_AUTO = "full_auto"  # discover, tailor and SUBMIT
    DRY_RUN = "dry_run"  # discover, tailor, fill every form, stop before the final submit click
    DISCOVER_ONLY = "discover_only"  # ingest + score only; never opens an application form


class OpportunitySource(StrEnum):
    WORKBOOK = "workbook"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    MANUAL = "manual"


class ATS(StrEnum):
    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    ICIMS = "icims"
    SMARTRECRUITERS = "smartrecruiters"
    TALEO = "taleo"
    SUCCESSFACTORS = "successfactors"
    ORACLE = "oracle"
    CUSTOM = "custom"  # employer-specific portal (Tesla, Cemex, Keurig Dr Pepper, ...)
    UNKNOWN = "unknown"


class ApplicationStatus(StrEnum):
    APPLYING = "applying"  # attempt in flight (row is written BEFORE the browser opens)
    SUBMITTED = "submitted"  # submitted AND a confirmation was observed
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"  # final click done, no confirmation observed
    DRY_RUN_OK = "dry_run_ok"  # everything filled, stopped before final submit
    NEEDS_MANUAL = "needs_manual"  # a human must finish it; see ``reason``
    FAILED = "failed"  # technical failure; may be retried per the retry policy
    SKIPPED = "skipped"  # deliberately not applied (closed, already applied, ineligible, duplicate)


SUBMITTED_STATUSES = frozenset(
    {ApplicationStatus.SUBMITTED, ApplicationStatus.SUBMITTED_UNCONFIRMED}
)


class Reason(StrEnum):
    """Why an attempt ended in NEEDS_MANUAL / FAILED / SKIPPED."""

    UNSUPPORTED_PORTAL = "unsupported_portal"
    BOT_CHECK = "bot_check"  # CAPTCHA / "verify you are human" / access denied by bot protection
    LOGIN_REQUIRED = "login_required"  # SSO-only or otherwise un-automatable sign-in
    EMAIL_VERIFICATION = "email_verification"  # needs a mailbox link/code we cannot read
    MISSING_ANSWER = "missing_answer"  # a required factual question has no saved answer
    ATTESTATION_NOT_AUTHORIZED = "attestation_not_authorized"
    ACCOUNT_PROBLEM = "account_problem"  # locked account, wrong password, tenant refuses signup
    DOCUMENT_REJECTED = "document_rejected"
    VALIDATION_ERROR = "validation_error"  # site rejected our data and we cannot fix it
    UNEXPECTED_FLOW = "unexpected_flow"  # page structure not understood
    POSTING_CLOSED = "posting_closed"
    ALREADY_APPLIED = "already_applied"
    INELIGIBLE = "ineligible"
    DUPLICATE = "duplicate"
    INTERRUPTED = "interrupted"  # process died mid-attempt (recovered at next start)
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    INTERNAL_ERROR = "internal_error"
    OTHER = "other"


class QuestionKind(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SINGLE_CHOICE = "single_choice"  # select / radio group / custom dropdown
    MULTI_CHOICE = "multi_choice"  # checkbox group / multi-select
    BOOLEAN = "boolean"  # lone checkbox or yes/no toggle
    NUMBER = "number"
    DATE = "date"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------------------------- profile

DECLINE = "decline"  # sentinel: adapters map this to the site's "prefer not to say" option


class EEOPreferences(_Model):
    """Voluntary self-identification. Default is to decline every question."""

    gender: str = DECLINE
    race_ethnicity: str = DECLINE
    hispanic_latino: str = DECLINE
    veteran_status: str = DECLINE
    disability_status: str = DECLINE


class Profile(_Model):
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    pronouns: str = ""
    email: str = ""
    phone: str = ""
    phone_country: str = "United States"
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "United States"
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    school: str = "The University of Texas at Austin"
    degree: str = ""
    major: str = ""
    minor: str = ""
    gpa: str = ""
    education_start_date: str = ""  # normalised to YYYY-MM when parseable
    graduation_date: str = ""  # normalised to YYYY-MM when parseable
    authorized_to_work_us: bool | None = None
    requires_sponsorship: bool | None = None  # now OR in the future
    willing_to_relocate: bool | None = None
    is_18_or_older: bool | None = None
    available_start_date: str = ""  # ISO date, e.g. 2027-05-17
    available_end_date: str = ""
    referral_source: str = "Company website"
    eeo: EEOPreferences = Field(default_factory=EEOPreferences)
    fallback_resume_path: str = ""  # README: config.json -> profile.fallback_resume_path

    @field_validator("education_start_date", "graduation_date", mode="before")
    @classmethod
    def _normalise_year_month(cls, value: Any) -> Any:
        if isinstance(value, str) and (parsed := parse_year_month(value)):
            return f"{parsed[0]:04d}-{parsed[1]:02d}"
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def phone_digits(self) -> str:
        return "".join(c for c in self.phone if c.isdigit())

    @property
    def phone_national(self) -> str:
        """National number without country code (US: last 10 digits)."""
        digits = self.phone_digits
        return digits[-10:] if len(digits) > 10 else digits


# Fields that must be non-empty / non-None before the applier may start (see readiness.py).
REQUIRED_PROFILE_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "country",
    "school",
    "degree",
    "major",
    "graduation_date",
    "authorized_to_work_us",
    "requires_sponsorship",
)


# --------------------------------------------------------------------------------------------- search profile


class RoleFamily(_Model):
    keywords: list[str]
    weight: float = 1.0  # 0..1 multiplier applied to a title match in this family


def _default_role_families() -> dict[str, RoleFamily]:
    return {
        "product_management": RoleFamily(
            keywords=[
                "product manager",
                "product management",
                "associate product manager",
                "apm",
                "product intern",
                "product owner",
                "product operations",
                "product strategy",
            ]
        ),
        "technical_program_management": RoleFamily(
            keywords=[
                "technical program manager",
                "tpm",
                "program manager",
                "program management",
                "technical program",
                "project manager",
                "project management",
                "delivery manager",
            ]
        ),
        "technology_consulting": RoleFamily(
            keywords=[
                "technology consulting",
                "technology consultant",
                "it consulting",
                "digital consulting",
                "consulting analyst",
                "technology advisory",
                "solutions consultant",
                "business technology analyst",
                "management consulting",
                "consultant",
            ]
        ),
        "strategy": RoleFamily(
            keywords=[
                "strategy",
                "strategic",
                "corporate strategy",
                "strategy and operations",
                "business strategy",
                "corporate development",
                "strategic planning",
            ]
        ),
        "business_operations": RoleFamily(
            keywords=[
                "business operations",
                "biz ops",
                "bizops",
                "operations analyst",
                "operations intern",
                "revenue operations",
                "sales operations",
                "strategic operations",
                "operations excellence",
            ]
        ),
        "business_analysis": RoleFamily(
            keywords=[
                "business analyst",
                "business analysis",
                "business intelligence",
                "systems analyst",
                "process analyst",
                "requirements analyst",
                "functional analyst",
            ]
        ),
        "analytics": RoleFamily(
            keywords=[
                "data analyst",
                "analytics",
                "data analytics",
                "business analytics",
                "product analytics",
                "insights analyst",
                "quantitative analyst",
                "data science",
            ],
            weight=0.7,  # adjacent to the core families
        ),
    }


class SearchProfile(_Model):
    target_term: str = "Summer 2027"
    recent_days: int = 45
    role_families: dict[str, RoleFamily] = Field(default_factory=_default_role_families)
    include_keywords: list[str] = Field(default_factory=list)  # bonus points when present
    exclude_title_keywords: list[str] = Field(
        default_factory=lambda: [
            "senior",
            "sr.",
            "staff",
            "principal",
            "director",
            "vp",
            "vice president",
            "phd",
            "postdoc",
            "postdoctoral",
        ]
    )
    preferred_locations: list[str] = Field(default_factory=list)  # empty = anywhere
    us_only: bool = True
    remote_ok: bool = True
    company_allowlist: list[str] = Field(default_factory=list)  # bonus points
    company_denylist: list[str] = Field(default_factory=list)  # never apply
    min_score: float = 55.0  # 0..100; below this an opportunity is never applied to


# --------------------------------------------------------------------------------------------- opportunities


class ScoreResult(_Model):
    score: float  # 0..100
    passed: bool
    role_family: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)  # human-readable, shown in the dashboard
    penalties: list[str] = Field(default_factory=list)


class Opportunity(_Model):
    id: str = ""  # derived from URL/fingerprint when empty
    company: str
    title: str
    url: str = ""  # posting page (or best URL to start from)
    apply_url: str | None = None  # direct application URL when known
    location: str | None = None
    term: str | None = None  # e.g. "Summer 2027"
    source: OpportunitySource = OpportunitySource.WORKBOOK
    ats: ATS = ATS.UNKNOWN
    is_open: bool = True
    posted_date: date | None = None
    last_verified: date | None = None
    deadline: date | None = None
    description: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # unknown source columns, notes, etc.
    fingerprint: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    score: ScoreResult | None = None

    @model_validator(mode="after")
    def _derive_keys(self) -> Opportunity:
        if not self.fingerprint:
            object.__setattr__(
                self, "fingerprint", fingerprint(self.company, self.title, self.location)
            )
        if not self.id:
            object.__setattr__(
                self,
                "id",
                opportunity_id(self.apply_url or self.url, self.company, self.title, self.location),
            )
        return self

    @property
    def start_url(self) -> str:
        return self.apply_url or self.url


# --------------------------------------------------------------------------------------------- knowledge base


class Experience(_Model):
    """One entry of the user's real background. The ONLY source of truth for tailored documents."""

    id: str
    kind: Literal["work", "project", "education", "leadership", "award", "skill", "other"] = "work"
    title: str
    organization: str | None = None
    location: str | None = None
    start: str | None = None  # YYYY-MM
    end: str | None = None  # YYYY-MM or "present"
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class KnowledgeBase(_Model):
    source: Literal["experience_files", "resume", "none"] = "none"
    experiences: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    def corpus(self) -> str:
        """All KB text, lower-cased; used by the grounding validator."""
        parts: list[str] = list(self.skills)
        for e in self.experiences:
            parts += [e.title, e.organization or "", e.location or "", *e.bullets, *e.skills]
        return "\n".join(parts).lower()


class GroundingReport(_Model):
    ok: bool = True
    violations: list[str] = Field(default_factory=list)
    replaced_with_source: int = 0  # rephrased bullets reverted to the original text


class TailoredDocs(_Model):
    mode: Literal["tailored", "fallback_uploaded_resume"]
    resume_pdf: Path
    cover_letter_pdf: Path | None = None
    cover_letter_text: str | None = None
    grounding: GroundingReport | None = None
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------- answers


class FormQuestion(_Model):
    """A question found on an application form, extracted by an adapter."""

    label: str
    kind: QuestionKind = QuestionKind.TEXT
    options: list[str] = Field(default_factory=list)  # visible option labels for choice questions
    required: bool = False
    max_length: int | None = None
    hint: str | None = None  # placeholder / help text
    field_id: str | None = None  # name / id / data-automation-id, for logs only


class AnswerDecision(_Model):
    status: Literal["answered", "unanswerable"]
    value: str | list[str] | bool | None = None  # for choice questions: exact option label(s)
    intent: str | None = None  # canonical intent key, e.g. "work_authorization_us"
    source: Literal["profile", "saved_answer", "generated", "default"] | None = None
    confidence: float = 0.0
    reason: str | None = None


class ScreeningAnswer(_Model):
    id: int | None = None
    intent: str | None = None
    question: str  # exemplar wording
    question_norm: str = ""  # normalised wording used for matching
    answer: str
    answer_kind: Literal["boolean", "text", "choice", "number"] = "text"
    source: Literal["user", "profile", "generated"] = "user"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    use_count: int = 0


class PendingQuestion(_Model):
    """A required factual question the engine could not answer; shown in the dashboard until answered."""

    id: int | None = None
    question: str
    kind: QuestionKind = QuestionKind.TEXT
    options: list[str] = Field(default_factory=list)
    opportunity_id: str | None = None
    company: str | None = None
    created_at: datetime | None = None
    resolved: bool = False


class AtsCredentials(_Model):
    host: str
    email: str
    password: SecretStr
    created: bool = False  # True when this call generated a brand-new account


# --------------------------------------------------------------------------------------------- applications


class ApplyResult(_Model):
    status: ApplicationStatus
    reason: Reason | None = None
    message: str = ""
    ats: ATS = ATS.UNKNOWN
    confirmation: str | None = None  # confirmation text / reference number / URL observed
    filled_fields: dict[str, str] = Field(
        default_factory=dict
    )  # audit trail; never contains secrets
    pending_questions: list[PendingQuestion] = Field(default_factory=list)
    artifacts: list[str] = Field(
        default_factory=list
    )  # screenshots / traces (paths, relative to data dir)
    steps: list[str] = Field(default_factory=list)  # human-readable trace of what happened


class Application(_Model):
    id: int | None = None
    opportunity_id: str
    attempt_no: int = 1
    status: ApplicationStatus = ApplicationStatus.APPLYING
    reason: Reason | None = None
    message: str = ""
    mode: RunMode = RunMode.FULL_AUTO
    ats: ATS = ATS.UNKNOWN
    run_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    submitted_at: datetime | None = None  # set only for SUBMITTED / SUBMITTED_UNCONFIRMED
    confirmation: str | None = None
    docs: dict[str, str] = Field(
        default_factory=dict
    )  # {"resume": path, "cover_letter": path, "mode": ...}
    artifacts: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    filled_fields: dict[str, str] = Field(default_factory=dict)


class RunReport(_Model):
    run_id: int | None = None
    trigger: Literal["manual", "schedule", "cli", "test"] = "manual"
    mode: RunMode = RunMode.FULL_AUTO
    started_at: datetime | None = None
    finished_at: datetime | None = None
    discovered: int = 0
    new: int = 0
    eligible: int = 0
    attempted: int = 0
    submitted: int = 0
    dry_run_ok: int = 0
    needs_manual: int = 0
    failed: int = 0
    skipped: int = 0
    cap_remaining: int | None = None
    stopped_reason: str | None = (
        None  # cap_reached | kill_switch | no_candidates | attempt_budget | not_ready | error
    )
    errors: list[str] = Field(default_factory=list)
