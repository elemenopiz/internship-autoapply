"""Mock Lever hosted job site (``jobs.lever.co``).

``make_site(company="acme", jobs=None, *, ...)`` returns a ``LeverSite``. ``company`` is the site slug; job ids are
UUIDs (default job id ``3f9c2a54-6b1e-4d0a-9a7e-1c2d3e4f5a6b``). The browser reaches ``jobs.lever.co.localhost:<port>``;
use ``site.job_url(id)`` / ``site.apply_url(id)``.

Options (keyword only): ``name`` (default "lever"), ``company_name``, ``require_captcha`` (VISIBLE hCaptcha that a
human must solve; ``captcha_provider`` / ``captcha_placement`` inline|overlay|on_submit, the latter opens the modal
challenge only when submit is pressed unsolved and finishes the submit once a human solves it), ``cookie_consent``
(None|"bar"|"modal" OneTrust style consent UI), ``render_delay_s`` (the form is
injected by script after the delay), ``parse_delay_s`` (server side "resume parsing" time, default 0.6 s),
``parse_fills`` (input name -> value written into EMPTY inputs when parsing finishes, e.g. ``{"org": "Parsed Co"}``),
``eeo`` (survey section), ``phone_required``, ``org_required``, ``card_size`` (custom questions per card, default 2),
``location_field`` (adds the "Current location" typeahead; the user must pick a suggestion), ``max_upload_bytes``.

Pages and selectors guaranteed
    ``/{co}``                 job list: ``div.posting > a.posting-title > h5[data-qa=posting-name]``
    ``/{co}/{id}``            posting: ``div.posting-headline h2``, ``div.posting-categories``,
                              TWO ``a.postings-btn`` links "Apply for this job" (top and bottom; strict-mode
                              locators must use ``.first``), both pointing at ``/{co}/{id}/apply``; the button
                              text is upper-cased by CSS (``inner_text`` returns "APPLY FOR THIS JOB").
    ``/{co}/{id}/apply``      ``form#application-form`` (multipart POST to the same URL, NATIVE constraint
                              validation through ``required`` attributes - an invalid field blocks the submit and
                              matches ``:invalid``). Inputs: ``input[name=name]`` (Full name), ``name=email``,
                              ``name=phone``, ``name=org`` (Current company), ``name="urls[LinkedIn]"``,
                              ``urls[GitHub]``, ``urls[Portfolio]``, ``urls[Other]``, ``textarea[name=comments]``
                              (Additional information); optional ``input[name=location]#location-input`` +
                              hidden ``selectedLocation``. Resume: ``input#resume-upload-input[type=file]
                              [name=resume]`` (``display:none``: use set_input_files / wait for state=attached; the
                              ``button.resume-upload-btn`` "Attach resume/CV" opens the chooser). After a file is
                              chosen ``.resume-upload-loading`` shows, ``POST /parseResume`` runs (``parse_delay_s``,
                              subject to ``site.faults``) and then ``.resume-upload-success`` ("Success!") replaces
                              it; on a failed parse ``.resume-upload-failure`` shows. Submitting while the parse is
                              pending (or failed) is BLOCKED with ``.application-error`` text ("Please wait until
                              your resume has finished uploading.") and counted in ``site.state["blocked_submits"]``;
                              the server also rejects a resume whose parse never completed.
                              Custom questions: ``cards[<uuid>][field0..]`` (text input, textarea, radio group for
                              radio, ``select`` with first option "Select..." for select, checkbox group for
                              multiselect, one "I agree" checkbox for checkbox) preceded by a hidden
                              ``cards[<uuid>][baseTemplate]`` JSON. EEO survey ``select[name="eeo[gender]"]``,
                              ``eeo[race]``, ``eeo[veteran]``, ``eeo[disability]`` with "Decline to self-identify"
                              style options. Captcha (if enabled): ``div.h-captcha``. Submit:
                              ``button#btn-submit`` ("Submit application", upper-cased by CSS).
    ``/{co}/{id}/thanks``     303 target after a successful POST; ``h2`` "Application submitted!".
    A closed job answers HTTP 404 ("Sorry, we couldn't find anything here") on the posting, /apply and /thanks.

Recording: only valid POSTs are recorded. ``Submission.fields`` holds raw names; ``files`` holds the resume upload;
    ``meta``: ``job_id``, ``company``, ``standard`` (name, email, phone, org, location, comments), ``urls``
    (LinkedIn, GitHub, Portfolio, Other), ``answers`` (MockQuestion.key -> list of values), ``eeo`` (short key ->
    value), ``uploads`` (resume -> filename), ``resume_storage_id``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from autoapply.testing.mock_ats.base import (
    STANDARD_QUESTIONS,
    MockJob,
    MockQuestion,
    MockSite,
    UploadedFile,
)
from autoapply.testing.mock_ats.blockers import (
    CAPTCHA_LISTENER_JS,
    CookieBanner,
    Origin,
    Placement,
    Provider,
    captcha_overlay,
    captcha_overlay_template,
    captcha_response_field,
    captcha_token_ok,
    captcha_widget,
    cookie_banner,
    esc,
    html_response,
    install_captcha_routes,
    json_for_script,
)

HOST = "jobs.lever.co"
_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
DEFAULT_JOB_ID = "3f9c2a54-6b1e-4d0a-9a7e-1c2d3e4f5a6b"
ALLOWED_EXTENSIONS: tuple[str, ...] = ("pdf", "doc", "docx", "txt", "rtf", "odt", "pages")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_URL_LABELS: tuple[tuple[str, str], ...] = (
    ("LinkedIn", "LinkedIn URL"),
    ("GitHub", "GitHub URL"),
    ("Portfolio", "Portfolio URL"),
    ("Other", "Other website"),
)

_LOCATIONS: tuple[str, ...] = (
    "Austin, TX, United States",
    "Austin, MN, United States",
    "Boston, MA, United States",
    "Dallas, TX, United States",
    "Houston, TX, United States",
    "New York, NY, United States",
    "San Francisco, CA, United States",
    "Seattle, WA, United States",
)

_EEO_DEFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("gender", "Gender", ("Female", "Male", "Decline to self-identify")),
    (
        "race",
        "Race",
        (
            "Hispanic or Latino",
            "White (Not Hispanic or Latino)",
            "Black or African American (Not Hispanic or Latino)",
            "Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino)",
            "Asian (Not Hispanic or Latino)",
            "American Indian or Alaska Native (Not Hispanic or Latino)",
            "Two or More Races (Not Hispanic or Latino)",
            "Decline to self-identify",
        ),
    ),
    (
        "veteran",
        "Veteran status",
        (
            "I am not a protected veteran",
            "I identify as one or more of the classifications of protected veteran",
            "Decline to self-identify",
        ),
    ),
    (
        "disability",
        "Disability status",
        (
            "Yes, I have a disability, or have had one in the past",
            "No, I do not have a disability and have not had one in the past",
            "I do not want to answer",
        ),
    ),
)


def default_jobs() -> list[MockJob]:
    """The single open job served when ``jobs`` is not given."""
    return [
        MockJob(
            id=DEFAULT_JOB_ID,
            title="Product Management Intern (Summer 2027)",
            location="Austin, TX",
            description=(
                "Summer 2027 internship. Work with product, engineering and operations teams.\n\n"
                "You will own a scoped project, present to leadership and ship something real."
            ),
            questions=(
                STANDARD_QUESTIONS["work_auth"],
                STANDARD_QUESTIONS["sponsorship"],
                STANDARD_QUESTIONS["referral"],
                STANDARD_QUESTIONS["why_role"],
            ),
        )
    ]


# ------------------------------------------------------------------------------------ form model


@dataclass(frozen=True)
class _LField:
    key: str  # std: "name"; url: "urls[LinkedIn]"; card: "q:<MockQuestion.key>"; eeo: "eeo:<short>"
    name: str  # submitted input name
    label: str
    kind: str  # text|email|tel|textarea|select|radio|checkbox|multiselect
    required: bool = False
    options: tuple[str, ...] = ()
    max_length: int | None = None
    group: str = "std"  # std | url | card | eeo
    card_id: str = ""


@dataclass(frozen=True)
class _Card:
    id: str
    fields: tuple[_LField, ...]
    template: str


def _field_type(question: MockQuestion) -> str:
    return {
        "text": "text",
        "textarea": "textarea",
        "select": "dropdown",
        "radio": "multiple-choice",
        "checkbox": "multiple-select",
        "multiselect": "multiple-select",
    }[question.kind]


def _cards_for(job: MockJob, card_size: int) -> list[_Card]:
    cards: list[_Card] = []
    size = max(card_size, 1)
    for start in range(0, len(job.questions), size):
        chunk = job.questions[start : start + size]
        card_id = str(uuid.uuid5(_NS, f"{job.id}:card:{start // size}"))
        fields = tuple(
            _LField(
                key=f"q:{q.key}",
                name=f"cards[{card_id}][field{i}]",
                label=q.label,
                kind=q.kind,
                required=q.required,
                options=q.options if q.kind != "checkbox" else ("I agree",),
                max_length=q.max_length,
                group="card",
                card_id=card_id,
            )
            for i, q in enumerate(chunk)
        )
        template = json_for_script(
            {
                "id": card_id,
                "text": "Additional questions",
                "fields": [
                    {
                        "text": q.label,
                        "type": _field_type(q),
                        "description": "",
                        "required": q.required,
                        "options": [{"text": o} for o in q.options],
                    }
                    for q in chunk
                ],
            }
        )
        cards.append(_Card(card_id, fields, template))
    return cards


# ------------------------------------------------------------------------------------ site


class LeverSite(MockSite):
    """Mock Lever site (see the module docstring for the selector contract)."""

    def __init__(
        self,
        company: str,
        jobs: Sequence[MockJob],
        *,
        name: str,
        company_name: str,
        require_captcha: bool,
        captcha_provider: Provider,
        captcha_placement: Placement,
        render_delay_s: float,
        parse_delay_s: float,
        parse_fills: dict[str, str] | None,
        eeo: bool,
        phone_required: bool,
        org_required: bool,
        card_size: int,
        location_field: bool,
        max_upload_bytes: int,
        cookie_consent: CookieBanner | None,
    ) -> None:
        super().__init__(name, HOST)
        self.company = company
        self.company_name = company_name
        self.require_captcha = require_captcha
        self.captcha_provider: Provider = captcha_provider
        self.captcha_placement: Placement = captcha_placement
        self.render_delay_s = render_delay_s
        self.parse_delay_s = parse_delay_s
        self.parse_fills = dict(parse_fills or {})
        self.eeo = eeo
        self.phone_required = phone_required
        self.org_required = org_required
        self.card_size = card_size
        self.location_field = location_field
        self.max_upload_bytes = max_upload_bytes
        self.cookie_consent = cookie_consent
        for job in jobs:
            self.jobs[job.id] = job
        self.state["parsed_resumes"] = {}
        self.state["parse_requests"] = 0
        self.state["blocked_submits"] = 0
        install_captcha_routes(self)
        self._install_routes()

    # ---- addressing --------------------------------------------------------------------------------
    def job_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/{job_id}")

    def apply_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/{job_id}/apply")

    def thanks_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/{job_id}/thanks")

    # ---- form model ----------------------------------------------------------------------------------
    def std_fields(self) -> list[_LField]:
        fields = [
            _LField("name", "name", "Full name", "text", True),
            _LField("email", "email", "Email", "email", True),
            _LField("phone", "phone", "Phone", "tel", self.phone_required),
        ]
        if self.location_field:
            fields.append(_LField("location", "location", "Current location", "text", True))
        fields.append(_LField("org", "org", "Current company", "text", self.org_required))
        return fields

    def url_fields(self) -> list[_LField]:
        return [
            _LField(f"urls[{key}]", f"urls[{key}]", label, "text", group="url")
            for key, label in _URL_LABELS
        ]

    def eeo_fields(self) -> list[_LField]:
        if not self.eeo:
            return []
        return [
            _LField(f"eeo:{key}", f"eeo[{key}]", label, "select", options=options, group="eeo")
            for key, label, options in _EEO_DEFS
        ]

    def cards_for(self, job: MockJob) -> list[_Card]:
        return _cards_for(job, self.card_size)

    def card_field(self, job_id: str, question_key: str) -> str:
        """Input name (``cards[<uuid>][fieldN]``) of the custom question ``question_key``."""
        for card in self.cards_for(self.jobs[job_id]):
            for field in card.fields:
                if field.key == f"q:{question_key}":
                    return field.name
        raise KeyError(question_key)

    def all_fields(self, job: MockJob) -> list[_LField]:
        fields = self.std_fields() + self.url_fields()
        for card in self.cards_for(job):
            fields += list(card.fields)
        return fields + self.eeo_fields()

    # ---- routes -------------------------------------------------------------------------------------------
    def _install_routes(self) -> None:
        app = self.app

        @app.post("/parseResume")
        async def parse_resume(request: Request) -> Response:
            _, files = await self.read_form(request)
            self.state["parse_requests"] += 1
            await asyncio.sleep(self.parse_delay_s)
            upload = next((f for f in files if f.field == "resume"), None)
            if upload is None or not upload.data:
                return JSONResponse({"error": "no file"}, status_code=422)
            storage_id = f"resume-{uuid.uuid4()}"
            self.state["parsed_resumes"][storage_id] = upload.filename
            return JSONResponse({"resumeStorageId": storage_id, "fills": self.parse_fills})

        @app.post("/apply-events/blocked")
        def blocked() -> Response:
            self.state["blocked_submits"] += 1
            return JSONResponse({"ok": True})

        @app.get("/locations/search")
        def locations(query: str = "") -> Response:
            needle = query.strip().lower()
            hits = [loc for loc in _LOCATIONS if needle and needle in loc.lower()]
            return JSONResponse(
                {"results": [{"name": h, "id": f"loc-{i}"} for i, h in enumerate(hits)]}
            )

        @app.get("/{co}")
        def postings(co: str) -> Response:
            if co != self.company:
                return self._not_found()
            return self._postings_page()

        @app.get("/{co}/{job_id}")
        def posting(co: str, job_id: str, request: Request) -> Response:
            job = self._job(co, job_id)
            if job is None:
                return self._not_found()
            return self._posting_page(request, job)

        @app.get("/{co}/{job_id}/apply")
        def apply_page(co: str, job_id: str, request: Request) -> Response:
            job = self._job(co, job_id)
            if job is None:
                return self._not_found()
            return self._apply_page(request, job)

        @app.post("/{co}/{job_id}/apply")
        async def apply_submit(co: str, job_id: str, request: Request) -> Response:
            job = self._job(co, job_id)
            if job is None:
                return self._not_found()
            return await self._submit(request, job)

        @app.get("/{co}/{job_id}/thanks")
        def thanks(co: str, job_id: str) -> Response:
            job = self._job(co, job_id)
            if job is None:
                return self._not_found()
            return self._thanks_page(job)

    def _job(self, company: str, job_id: str) -> MockJob | None:
        job = self.jobs.get(job_id)
        if company != self.company or job is None or job.closed:
            return None
        return job

    def _not_found(self) -> Response:
        body = (
            "<div class='error-page page-centered'><h1>Sorry, we couldn't find anything here</h1>"
            f"<p><a href='/{esc(self.company)}'>See all openings</a></p></div>"
        )
        return html_response("404 Not Found", body, _CSS, status=404)

    # ---- pages ---------------------------------------------------------------------------------------------
    def _header(self) -> str:
        return (
            "<div class='main-header'><div class='main-header-content page-centered'>"
            f"<a class='main-header-logo' href='/{esc(self.company)}'>{esc(self.company_name)}</a>"
            "</div></div>"
        )

    def _postings_page(self) -> Response:
        rows = "".join(
            f"<div class='posting' data-qa-posting-id='{esc(j.id)}'>"
            f"<a class='posting-title' href='/{esc(self.company)}/{esc(j.id)}'>"
            f"<h5 data-qa='posting-name'>{esc(j.title)}</h5><div class='posting-categories'>"
            f"<span class='sort-by-location posting-category small-category-label location'>"
            f"{esc(j.location)}</span></div></a></div>"
            for j in self.jobs.values()
            if not j.closed
        )
        body = (
            f"{self._header()}<div class='content-wrapper'><div class='postings-wrapper page-centered'>"
            f"<div class='postings-group'>{rows}</div></div></div>"
        )
        return html_response(f"{self.company_name} jobs", body, _CSS)

    def _apply_link(self, request: Request, job: MockJob) -> str:
        origin = Origin.of(request).on(self.host)
        return f"{origin}/{self.company}/{job.id}/apply"

    def _posting_page(self, request: Request, job: MockJob) -> Response:
        link = self._apply_link(request, job)
        desc = "".join(f"<p>{esc(c)}</p>" for c in job.description.split("\n\n") if c.strip())
        button = (
            f"<div class='postings-btn-wrapper'><a class='postings-btn template-btn-submit cerulean' "
            f"href='{esc(link)}' data-qa='btn-apply'>Apply for this job</a></div>"
        )
        body = (
            f"{self._header()}<div class='content-wrapper posting-page'><div class='content'>"
            "<div class='section-wrapper accent-section page-full-width'>"
            "<div class='section page-centered posting-header'><div class='posting-headline'>"
            f"<h2>{esc(job.title)}</h2><div class='posting-categories'>"
            f"<div class='sort-by-location posting-category medium-category-label'>{esc(job.location)}</div>"
            "<div class='sort-by-commitment posting-category medium-category-label'>Intern</div>"
            f"</div></div>{button}</div></div>"
            "<div class='section-wrapper page-full-width'>"
            f"<div class='section page-centered' data-qa='job-description'>{desc}</div></div>"
            f"<div class='section page-centered last-section-apply'>{button}</div>"
            "</div></div>"
        )
        return html_response(f"{self.company_name} - {job.title}", body, _CSS)

    def _thanks_page(self, job: MockJob) -> Response:
        body = (
            f"{self._header()}<div class='content-wrapper'><div class='page-centered thanks' "
            "id='application-thanks'><h2 data-qa='thanks-heading'>Application submitted!</h2>"
            f"<p>We have received your application for <strong>{esc(job.title)}</strong>. "
            f"Thanks for your interest in {esc(self.company_name)}!</p>"
            f"<p><a href='/{esc(self.company)}'>Back to all jobs</a></p></div></div>"
        )
        return html_response(f"{self.company_name} - Application submitted", body, _CSS)

    def _apply_page(
        self,
        request: Request,
        job: MockJob,
        *,
        values: dict[str, list[str]] | None = None,
        errors: list[str] | None = None,
    ) -> Response:
        origin = Origin.of(request)
        form = self._form(job, origin, values or {}, errors or [])
        if self.render_delay_s > 0:
            mount = (
                "<div id='form-mount'><div class='loading' role='status'>Loading application...</div></div>"
                f"<template id='deferred-form'>{form}</template>"
            )
        else:
            mount = f"<div id='form-mount'>{form}</div>"
        extras = ""
        if self.require_captcha and self.captcha_placement == "overlay":
            extras = captcha_overlay(self.captcha_provider, origin)
        elif self.require_captcha and self.captcha_placement == "on_submit":
            extras = captcha_overlay_template(self.captcha_provider, origin)
        extras += cookie_banner(self.cookie_consent)
        config = json_for_script(
            {
                "delay": self.render_delay_s,
                "captcha": self.require_captcha,
                "captchaGate": self.require_captcha and self.captcha_placement == "on_submit",
                "captchaField": captcha_response_field(self.captcha_provider),
                "locations": self.location_field,
            }
        )
        body = (
            f"{self._header()}<div class='content-wrapper application-page'>"
            "<div class='section-wrapper accent-section page-full-width'>"
            "<div class='section page-centered posting-header'><div class='posting-headline'>"
            f"<h2>{esc(job.title)}</h2><div class='posting-categories'>"
            f"<div class='sort-by-location posting-category medium-category-label'>{esc(job.location)}</div>"
            "</div></div><div class='postings-btn-wrapper'>"
            f"<a class='back-link' href='/{esc(self.company)}/{esc(job.id)}'>Back to job posting</a>"
            "</div></div></div>"
            "<div class='section-wrapper page-full-width'>"
            f"<div class='section page-centered application-form-section'>{mount}</div></div></div>"
            f"{extras}<script id='lv-config' type='application/json'>{config}</script>"
            f"<script>{_JS}\n{CAPTCHA_LISTENER_JS}</script>"
        )
        return html_response(f"{self.company_name} - {job.title}", body, _CSS)

    # ---- form HTML -------------------------------------------------------------------------------------------
    def _form(
        self, job: MockJob, origin: Origin, values: dict[str, list[str]], errors: list[str]
    ) -> str:
        error_html = ""
        if errors:
            items = "".join(f"<li>{esc(e)}</li>" for e in errors)
            error_html = (
                "<div class='application-error error-messages' id='error-banner' role='alert'>"
                f"<p>There were errors with your application:</p><ul>{items}</ul></div>"
            )
        parts: list[str] = [
            "<h4 class='application-title'>Submit your application</h4>",
            error_html,
            "<ul class='application-fields'>",
            self._resume_block(),
        ]
        for field in self.std_fields():
            parts.append(self._field_li(field, values))
        parts.append("</ul><h4 class='section-title'>Links</h4><ul class='application-fields'>")
        parts += [self._field_li(f, values) for f in self.url_fields()]
        parts.append("</ul>")
        cards = self.cards_for(job)
        if cards:
            parts.append(
                "<h4 class='section-title'>Additional questions</h4><ul class='application-fields'>"
            )
            for card in cards:
                for i, field in enumerate(card.fields):
                    hidden = (
                        f"<input type='hidden' name='cards[{card.id}][baseTemplate]' "
                        f"value='{esc(card.template)}'>"
                        if i == 0
                        else ""
                    )
                    parts.append(self._field_li(field, values, prefix=hidden))
            parts.append("</ul>")
        parts.append(
            "<ul class='application-fields'><li class='application-question comments'>"
            "<label><div class='application-label'>Additional information</div>"
            "<div class='application-field'><textarea name='comments' id='additional-information' "
            "placeholder='Add a cover letter or anything else you want to share.' rows='6'>"
            f"{esc((values.get('comments') or [''])[0])}</textarea></div></label></li></ul>"
        )
        eeo = self.eeo_fields()
        if eeo:
            parts.append(
                "<div class='eeo-survey' id='eeo-survey'><h4 class='section-title'>U.S. Equal Employment "
                "Opportunity Information (Completion is voluntary)</h4><p>We are an equal opportunity "
                "employer. Submission of this information is voluntary and refusal to provide it will not "
                "subject you to any adverse treatment.</p><ul class='application-fields'>"
                + "".join(self._field_li(f, values) for f in eeo)
                + "</ul></div>"
            )
        parts.append(self._captcha_block(origin))
        parts.append(
            "<input type='hidden' name='origin' value=''><input type='hidden' name='source' value=''>"
            "<input type='hidden' name='resumeStorageId' id='resumeStorageId' value=''>"
            "<div class='application-submit'><button id='btn-submit' class='postings-btn template-btn-submit "
            "cerulean' type='submit' data-qa='btn-submit'>Submit application</button></div>"
        )
        return (
            f"<form id='application-form' method='POST' enctype='multipart/form-data' "
            f"action='/{esc(self.company)}/{esc(job.id)}/apply'>{''.join(parts)}</form>"
        )

    def _resume_block(self) -> str:
        return (
            "<li class='application-question resume'><label for='resume-upload-input'>"
            "<div class='application-label'>Resume/CV<span class='required'>✱</span></div></label>"
            "<div class='application-field'><div class='resume-upload'>"
            "<button type='button' class='postings-btn resume-upload-btn' data-qa='resume-upload-btn'>"
            "Attach resume/CV</button>"
            f"<input type='file' id='resume-upload-input' name='resume' data-qa='resume-upload-input' "
            f"accept='{','.join('.' + e for e in ALLOWED_EXTENSIONS)}' style='display:none'>"
            "<span class='resume-upload-loading' data-qa='resume-upload-loading' hidden>Uploading...</span>"
            "<span class='resume-upload-success' data-qa='resume-upload-success' hidden>"
            "<span class='success-label'>Success!</span> <span class='resume-filename'></span></span>"
            "<span class='resume-upload-failure' data-qa='resume-upload-failure' hidden>"
            "Upload failed. Please try attaching your resume again.</span>"
            "</div></div></li>"
        )

    def _captcha_block(self, origin: Origin) -> str:
        if not self.require_captcha:
            return ""
        if self.captcha_placement in {"overlay", "on_submit"}:
            field = captcha_response_field(self.captcha_provider)
            return f"<input type='hidden' name='{field}' value=''>"
        return (
            "<div class='application-question captcha-container' id='captcha-container'>"
            f"{captcha_widget(self.captcha_provider, origin)}"
            "<div class='captcha-error application-error' hidden></div></div>"
        )

    def _field_li(self, field: _LField, values: dict[str, list[str]], prefix: str = "") -> str:
        posted = values.get(field.name, [])
        star = "<span class='required'>\u2731</span>" if field.required else ""
        req = " required" if field.required else ""
        label = f"<div class='application-label'>{esc(field.label)}{star}</div>"
        cls = (
            "application-question custom-question"
            if field.group == "card"
            else "application-question"
        )
        name = esc(field.name)
        if field.kind in {"text", "email", "tel"}:
            value = esc(posted[0]) if posted else ""
            input_type = "tel" if field.kind == "tel" else "text"
            if field.name == "location":
                control = (
                    f"<input type='text' name='location' id='location-input' data-qa='location-input' "
                    f"autocomplete='off' value='{value}'{req}>"
                    "<input type='hidden' name='selectedLocation' id='selected-location' value=''>"
                    "<ul class='dropdown-results' id='location-results' hidden></ul>"
                )
            else:
                control = f"<input type='{input_type}' name='{name}' value='{value}'{req}>"
            return (
                f"<li class='{cls}'>{prefix}<label>{label}<div class='application-field'>{control}"
                "</div></label></li>"
            )
        if field.kind == "textarea":
            value = esc(posted[0]) if posted else ""
            max_attr = f" maxlength='{field.max_length}'" if field.max_length else ""
            return (
                f"<li class='{cls}'>{prefix}<label>{label}<div class='application-field'>"
                f"<textarea name='{name}' rows='4' class='card-field-input'{max_attr}{req}>{value}"
                "</textarea></div></label></li>"
            )
        if field.kind == "select":
            opts = "<option value=''>Select...</option>" + "".join(
                f"<option value='{esc(o)}'{' selected' if o in posted else ''}>{esc(o)}</option>"
                for o in field.options
            )
            return (
                f"<li class='{cls}'>{prefix}<label>{label}<div class='application-field'>"
                f"<select name='{name}'{req}>{opts}</select></div></label></li>"
            )
        input_type = "radio" if field.kind == "radio" else "checkbox"
        req_radio = req if field.kind == "radio" else ""
        alts = "".join(
            f"<li><label><input type='{input_type}' name='{name}' value='{esc(o)}'"
            f"{' checked' if o in posted else ''}{req_radio}>"
            f"<span class='application-answer-alternative'>{esc(o)}</span></label></li>"
            for o in field.options
        )
        return (
            f"<li class='{cls}'>{prefix}{label}<div class='application-field'>"
            f"<ul data-qa='multiple-choice' class='card-field-options'>{alts}</ul></div></li>"
        )

    # ---- submission -------------------------------------------------------------------------------------------
    async def _submit(self, request: Request, job: MockJob) -> Response:
        fields, files = await self.read_form(request)
        errors = self._validate(job, fields, files)
        if errors:
            return self._apply_page(request, job, values=fields, errors=errors)
        self.record_submission(request.url.path, fields, files, **self._meta(job, fields, files))
        return RedirectResponse(f"/{self.company}/{job.id}/thanks", status_code=303)

    def _first(self, fields: dict[str, list[str]], name: str) -> str:
        values = fields.get(name) or [""]
        return values[0].strip()

    def _validate(
        self, job: MockJob, fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> list[str]:
        errors: list[str] = []
        for field in self.all_fields(job):
            values = [v for v in fields.get(field.name, []) if v.strip()]
            if field.required and not values:
                errors.append(f"{field.label} is required.")
                continue
            if not values:
                continue
            if field.kind == "email" and not _EMAIL_RE.match(values[0].strip()):
                errors.append("Please provide a valid email address.")
            if field.max_length is not None and len(values[0]) > field.max_length:
                errors.append(
                    f"{field.label} is too long (maximum is {field.max_length} characters)."
                )
            if field.options and field.kind != "checkbox":
                bad = [v for v in values if v not in field.options]
                if bad:
                    errors.append(f"{field.label}: invalid selection.")
        if (
            self.location_field
            and self._first(fields, "location")
            and not self._first(fields, "selectedLocation")
        ):
            errors.append("Please select a location from the list.")
        errors += self._validate_resume(fields, files)
        if self.require_captcha:
            token = self._first(fields, captcha_response_field(self.captcha_provider))
            if not captcha_token_ok(self, token):
                errors.append("Please complete the captcha challenge.")
        return errors

    def _validate_resume(
        self, fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> list[str]:
        upload = next((f for f in files if f.field == "resume"), None)
        if upload is None:
            return ["Resume/CV is required."]
        extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            return ["Unsupported resume file type."]
        if len(upload.data) > self.max_upload_bytes or not upload.data:
            return ["The resume file is empty or too large."]
        storage_id = self._first(fields, "resumeStorageId")
        if storage_id not in self.state["parsed_resumes"]:
            return ["Your resume is still uploading. Please wait until it has finished."]
        return []

    def _meta(
        self, job: MockJob, fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> dict[str, Any]:
        answers: dict[str, list[str]] = {}
        eeo: dict[str, str] = {}
        for card in self.cards_for(job):
            for field in card.fields:
                answers[field.key.split(":", 1)[1]] = [
                    v for v in fields.get(field.name, []) if v.strip()
                ]
        for field in self.eeo_fields():
            eeo[field.key.split(":", 1)[1]] = self._first(fields, field.name)
        upload = next((f for f in files if f.field == "resume"), None)
        return {
            "job_id": job.id,
            "company": self.company,
            "standard": {
                key: self._first(fields, key)
                for key in ("name", "email", "phone", "org", "location", "comments")
            },
            "urls": {key: self._first(fields, f"urls[{key}]") for key, _ in _URL_LABELS},
            "answers": answers,
            "eeo": eeo,
            "uploads": {"resume": upload.filename} if upload else {},
            "resume_storage_id": self._first(fields, "resumeStorageId"),
        }


_CSS = """<style>
*{box-sizing:border-box}
body{margin:0;font-family:Lato,"Helvetica Neue",Helvetica,Arial,sans-serif;color:#333;background:#fff;
 font-size:16px;line-height:1.5}
[hidden]{display:none!important}
.page-centered{max-width:900px;margin:0 auto;padding:0 20px}
.main-header{border-bottom:1px solid #e5e5e5;padding:16px 0}
.main-header-logo{font-size:22px;color:#333;text-decoration:none;font-weight:bold}
.accent-section{background:#f7f7f7;border-bottom:1px solid #e5e5e5;padding:26px 0}
.posting-headline h2{margin:0 0 6px;font-size:28px}
.posting-category{display:inline-block;color:#777;margin-right:16px;font-size:14px}
.postings-btn{display:inline-block;background:#3d78bd;color:#fff;border:0;padding:12px 22px;border-radius:4px;
 text-transform:uppercase;font-weight:700;font-size:14px;cursor:pointer;text-decoration:none;letter-spacing:.5px}
.postings-btn-wrapper{margin-top:14px}
.section{padding:22px 20px}
.last-section-apply{padding-bottom:70px}
.application-title,.section-title{margin:26px 0 8px;font-size:18px}
.application-fields{list-style:none;margin:0;padding:0}
.application-question{margin:16px 0}
.application-label{font-weight:700;margin-bottom:5px}
.required{color:#c0392b;margin-left:4px;font-size:12px}
.application-field input[type=text],.application-field input[type=email],.application-field input[type=tel],
.application-field select,.application-field textarea{width:100%;padding:9px 10px;border:1px solid #bbb;
 border-radius:3px;font:inherit}
.card-field-options{list-style:none;margin:0;padding:0}
.card-field-options label{font-weight:400;display:block;margin:3px 0}
.resume-upload{display:flex;align-items:center;gap:14px}
.resume-upload-success{color:#2e7d32;font-weight:700}
.resume-upload-failure,.application-error{color:#c0392b}
.error-messages{background:#fdecea;border-left:4px solid #c0392b;padding:8px 16px;margin:10px 0}
.dropdown-results{list-style:none;margin:2px 0 0;padding:0;border:1px solid #bbb;background:#fff}
.dropdown-results li{padding:7px 10px;cursor:pointer}
.dropdown-results li:hover{background:#eef4fb}
.eeo-survey{margin-top:30px;border-top:1px solid #e5e5e5;padding-top:6px}
.application-submit{margin:34px 0 70px}
.thanks{padding:70px 20px;text-align:center}
.error-page{padding:90px 20px;text-align:center}
.loading{padding:40px;color:#777}
.captcha-container{margin:24px 0}
</style>"""

_JS = r"""
(function () {
  'use strict';
  var CFG = JSON.parse(document.getElementById('lv-config').textContent);
  var form = null;
  var parseState = 'idle';

  function showError(message) {
    var old = document.getElementById('client-error');
    if (old) { old.remove(); }
    var box = document.createElement('div');
    box.id = 'client-error';
    box.className = 'application-error error-messages';
    box.setAttribute('role', 'alert');
    box.textContent = message;
    var anchor = form.querySelector('.application-submit');
    form.insertBefore(box, anchor);
    box.scrollIntoView({block: 'center'});
  }

  function applyFills(fills) {
    Object.keys(fills || {}).forEach(function (name) {
      var el = form.querySelector('[name="' + name + '"]');
      if (el && !el.value) {
        el.value = fills[name];
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
      }
    });
  }

  function initResume() {
    var input = document.getElementById('resume-upload-input');
    var button = form.querySelector('.resume-upload-btn');
    var loading = form.querySelector('.resume-upload-loading');
    var success = form.querySelector('.resume-upload-success');
    var failure = form.querySelector('.resume-upload-failure');
    var storage = document.getElementById('resumeStorageId');
    button.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      loading.hidden = true; success.hidden = true; failure.hidden = true;
      storage.value = '';
      var file = input.files && input.files[0];
      if (!file) { parseState = 'idle'; return; }
      parseState = 'loading';
      loading.hidden = false;
      var data = new FormData();
      data.append('resume', file);
      fetch('/parseResume', {method: 'POST', body: data})
        .then(function (r) {
          if (!r.ok) { throw new Error('parse failed ' + r.status); }
          return r.json();
        })
        .then(function (json) {
          parseState = 'success';
          loading.hidden = true;
          success.querySelector('.resume-filename').textContent = file.name;
          success.hidden = false;
          storage.value = json.resumeStorageId;
          applyFills(json.fills);
        })
        .catch(function () {
          parseState = 'failed';
          loading.hidden = true;
          failure.hidden = false;
          input.value = '';  // like the real widget: the same file can be attached again
        });
    });
  }

  function initLocation() {
    var input = document.getElementById('location-input');
    if (!input) { return; }
    var list = document.getElementById('location-results');
    var chosen = document.getElementById('selected-location');
    var timer = null;
    input.addEventListener('input', function () {
      chosen.value = '';
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { list.hidden = true; return; }
      timer = setTimeout(function () {
        fetch('/locations/search?query=' + encodeURIComponent(q)).then(function (r) { return r.json(); })
          .then(function (j) {
            list.innerHTML = '';
            j.results.forEach(function (item) {
              var li = document.createElement('li');
              li.textContent = item.name;
              li.setAttribute('role', 'option');
              li.addEventListener('mousedown', function (e) {
                e.preventDefault();
                input.value = item.name;
                chosen.value = JSON.stringify(item);
                list.hidden = true;
              });
              list.appendChild(li);
            });
            list.hidden = j.results.length === 0;
          });
      }, 150);
    });
    input.addEventListener('blur', function () { setTimeout(function () { list.hidden = true; }, 100); });
  }

  function reportBlocked() {
    try { fetch('/apply-events/blocked', {method: 'POST'}); } catch (e) { /* ignore */ }
  }

  function boot() {
    form = document.getElementById('application-form');
    if (!form) { return; }
    initResume();
    initLocation();
    form.addEventListener('submit', function (ev) {
      var input = document.getElementById('resume-upload-input');
      if (parseState === 'loading') {
        ev.preventDefault();
        reportBlocked();
        showError('Please wait until your resume has finished uploading.');
        return;
      }
      if (parseState === 'failed') {
        ev.preventDefault();
        showError('Resume upload failed. Please attach your resume again.');
        return;
      }
      if (!input.files || !input.files.length) {
        ev.preventDefault();
        showError('Resume/CV is required.');
        return;
      }
      if (CFG.captcha) {
        var field = form.querySelector('[name="' + CFG.captchaField + '"]');
        if (!field || !field.value) {
          ev.preventDefault();
          if (CFG.captchaGate) {
            window.__requireCaptcha(CFG.captchaField, function () { form.requestSubmit(); });
          } else {
            showError('Please complete the captcha challenge.');
          }
        }
      }
    });
  }

  if (CFG.delay > 0 && document.getElementById('deferred-form')) {
    setTimeout(function () {
      var tpl = document.getElementById('deferred-form');
      document.getElementById('form-mount').replaceChildren(tpl.content.cloneNode(true));
      boot();
    }, CFG.delay * 1000);
  } else {
    boot();
  }
})();
"""


def make_site(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    name: str | None = None,
    company_name: str | None = None,
    require_captcha: bool = False,
    captcha_provider: Provider = "hcaptcha",
    captcha_placement: Placement = "inline",
    render_delay_s: float = 0.0,
    parse_delay_s: float = 0.6,
    parse_fills: dict[str, str] | None = None,
    eeo: bool = True,
    phone_required: bool = True,
    org_required: bool = False,
    card_size: int = 2,
    location_field: bool = False,
    max_upload_bytes: int = 10 * 1024 * 1024,
    cookie_consent: CookieBanner | None = None,
) -> LeverSite:
    """Build a mock Lever site (see the module docstring for what every option guarantees)."""
    return LeverSite(
        company,
        list(jobs) if jobs else default_jobs(),
        name=name or "lever",
        company_name=company_name or company.replace("-", " ").replace("_", " ").title(),
        require_captcha=require_captcha,
        captcha_provider=captcha_provider,
        captcha_placement=captcha_placement,
        render_delay_s=render_delay_s,
        parse_delay_s=parse_delay_s,
        parse_fills=parse_fills,
        eeo=eeo,
        phone_required=phone_required,
        org_required=org_required,
        card_size=card_size,
        location_field=location_field,
        max_upload_bytes=max_upload_bytes,
        cookie_consent=cookie_consent,
    )
