"""Mock Ashby hosted job board (``jobs.ashbyhq.com``): a script-rendered single page app.

``make_site(company="acme", jobs=None, *, ...)`` returns an ``AshbySite``. ``company`` is the board slug; job ids
are UUIDs (default job id ``8f5d1c7a-2b4e-4c1a-9d3e-6a7b8c9d0e1f``). The browser reaches
``jobs.ashbyhq.com.localhost:<port>``; use ``site.job_url(id)`` / ``site.application_url(id)``.

Options (keyword only): ``name`` (default "ashby"), ``company_name``, ``render_delay_s`` (the SPA shows a
"Loading..." spinner and renders the page only after the delay), ``rerender_on_input`` (every input is REPLACED by a
fresh node on each fill/keystroke, staling element handles), ``select_widget`` ("native" <select>, or "combobox" =
ARIA combobox with a listbox popup), ``autofill`` (adds the "Autofill from resume" panel with its own file input;
the parse takes ``autofill_delay_s`` and writes ``autofill_fills`` into EMPTY fields), ``invisible_recaptcha`` (the real
``.grecaptcha-badge``), ``reject_as_spam`` (every otherwise valid submission is refused with "Your application
submission was flagged as possible spam."), ``cookie_consent`` (None|"bar"|"modal": OneTrust style consent UI),
``max_upload_bytes``.

Pages and selectors guaranteed
    ``/{co}``                  board: ``a[href="/{co}/{id}"]`` inside ``.ashby-job-posting-brief-list``.
    ``/{co}/{id}``             Overview tab; ``/{co}/{id}/application`` Application tab. Both routes serve the same shell
                               (``div#root``); the page is rendered by script AFTER ``GET /api/job-posting/{id}``
                               (so ``site.faults`` on ``/api/job-posting`` produce the SPA's "Something went wrong"
                               state until the page is reloaded). Tabs are ``a[role=tab]`` links ("Overview",
                               "Application") that switch views with ``history.pushState`` - NO page reload - and
                               the overview has an "Apply for this Job" link to the Application tab.
                               ``h1.ashby-job-posting-heading`` holds the title; ``<title>`` is "<title> @ <Company>".
    Application form           ``form.ashby-application-form`` (no native validation, submitted with ``fetch``, the page
                               never reloads). Every field is ``div.ashby-application-form-field-entry`` with a
                               ``label.ashby-application-form-question-title[for=<id>]`` (required marker is CSS
                               generated). System fields: ``input#_systemfield_name`` (Name), ``input#_systemfield_email``
                               (Email), ``input#_systemfield_resume[type=file]`` (Resume; ``display:none`` - use
                               set_input_files or state=attached; the sibling button "Upload File" opens the chooser,
                               afterwards ``.ashby-application-form-file-name`` shows the file name). Custom
                               questions have generated uuid ids/names (``site.field_id(job_id, key)``): text ->
                               ``input``, textarea -> ``textarea``, Yes/No -> a ``div[role=group]`` containing two
                               ``button[type=button]`` labelled "Yes" / "No" (``aria-pressed`` reflects the
                               choice; the value travels in a hidden input), other single choice -> ``select`` (first
                               option "Select...") or combobox, multiselect -> checkbox group
                               (``input[type=checkbox][name=<uuid>]``), single checkbox -> ``input[type=checkbox]``.
                               Button ``button.ashby-application-form-submit-button`` ("Submit Application").
                               CSS-module style classes (``_input_<hash>_12``) use a hash that is RANDOM PER SITE
                               INSTANCE: never select by them. Invalid submit: no request is sent, each bad field
                               gets ``div[role=alert]`` "Missing entry for required field: <label>" and
                               ``aria-invalid="true"``, plus a bottom banner "Your form needs corrections.".
                               Server rejections (HTTP 422) render the same messages; a network/5xx failure shows
                               a banner "Something went wrong submitting your application. Please try again." and
                               keeps the form filled. Success: ``div.ashby-application-form-success-container``
                               with "Your application was successfully submitted." (URL unchanged).
    Submission endpoint        ``POST /api/non-user-graphql?op=ApiSubmitSingleApplicationForm`` (multipart).

Recording: only valid submissions are recorded. ``Submission.fields`` holds raw names/values; ``files`` the resume
    (field ``_systemfield_resume``); ``meta``: ``job_id``, ``company``, ``standard`` (name, email, phone, linkedin),
    ``answers`` (MockQuestion.key -> list of labels; Yes/No -> ["Yes"] / ["No"]), ``uploads``.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from autoapply.testing.mock_ats.base import (
    STANDARD_QUESTIONS,
    MockJob,
    MockQuestion,
    MockSite,
    UploadedFile,
)
from autoapply.testing.mock_ats.blockers import (
    CookieBanner,
    Origin,
    cookie_banner,
    esc,
    html_response,
    install_captcha_routes,
    json_for_script,
    recaptcha_badge,
)

HOST = "jobs.ashbyhq.com"
_NS = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")
DEFAULT_JOB_ID = "8f5d1c7a-2b4e-4c1a-9d3e-6a7b8c9d0e1f"
ALLOWED_EXTENSIONS: tuple[str, ...] = ("pdf", "doc", "docx", "txt", "rtf")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SelectWidget = Literal["native", "combobox"]

LINKEDIN_QUESTION = MockQuestion("linkedin", "LinkedIn Profile", "text", required=False)
PHONE_QUESTION = MockQuestion("phone", "Phone number", "text", required=True)


def default_jobs() -> list[MockJob]:
    """The single open job served when ``jobs`` is not given."""
    return [
        MockJob(
            id=DEFAULT_JOB_ID,
            title="Product Management Intern, Summer 2027",
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
                STANDARD_QUESTIONS["relocate"],
            ),
        )
    ]


@dataclass(frozen=True)
class _AField:
    path: str  # id AND name of the input
    title: str
    type: str  # String|Email|Phone|LongText|Boolean|ValueSelect|MultiValueSelect|File|Checkbox
    required: bool
    key: str  # "name" | "email" | "resume" | "q:<MockQuestion.key>"
    options: tuple[str, ...] = ()
    max_length: int | None = None
    system: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.path,
            "title": self.title,
            "type": self.type,
            "required": self.required,
            "options": list(self.options),
            "maxLength": self.max_length,
        }


def _question_type(question: MockQuestion) -> str:
    if question.kind in {"select", "radio"} and tuple(question.options) == ("Yes", "No"):
        return "Boolean"
    return {
        "text": "String",
        "textarea": "LongText",
        "select": "ValueSelect",
        "radio": "ValueSelect",
        "checkbox": "Checkbox",
        "multiselect": "MultiValueSelect",
    }[question.kind]


class AshbySite(MockSite):
    """Mock Ashby board (see the module docstring for the selector contract)."""

    def __init__(
        self,
        company: str,
        jobs: Sequence[MockJob],
        *,
        name: str,
        company_name: str,
        render_delay_s: float,
        rerender_on_input: bool,
        select_widget: SelectWidget,
        autofill: bool,
        autofill_delay_s: float,
        autofill_fills: dict[str, str] | None,
        invisible_recaptcha: bool,
        reject_as_spam: bool,
        max_upload_bytes: int,
        cookie_consent: CookieBanner | None,
    ) -> None:
        super().__init__(name, HOST)
        self.company = company
        self.company_name = company_name
        self.render_delay_s = render_delay_s
        self.rerender_on_input = rerender_on_input
        self.select_widget: SelectWidget = select_widget
        self.autofill = autofill
        self.autofill_delay_s = autofill_delay_s
        self.autofill_fills = dict(autofill_fills or {})
        self.invisible_recaptcha = invisible_recaptcha
        self.reject_as_spam = reject_as_spam
        self.max_upload_bytes = max_upload_bytes
        self.cookie_consent = cookie_consent
        self.css_hash = secrets.token_hex(3)[:5]
        for job in jobs:
            self.jobs[job.id] = job
        self.state["api_submits"] = 0
        install_captcha_routes(self)
        self._install_routes()

    # ---- addressing ----------------------------------------------------------------------------------
    def job_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/{job_id}")

    def application_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/{job_id}/application")

    def fields_for(self, job: MockJob) -> list[_AField]:
        fields = [
            _AField("_systemfield_name", "Name", "String", True, "name", system=True),
            _AField("_systemfield_email", "Email", "Email", True, "email", system=True),
            _AField("_systemfield_resume", "Resume", "File", True, "resume", system=True),
        ]
        extras = [PHONE_QUESTION, LINKEDIN_QUESTION, *job.questions]
        for question in extras:
            fields.append(
                _AField(
                    path=str(uuid.uuid5(_NS, f"{job.id}:{question.key}")),
                    title=question.label,
                    type="Phone" if question.key == "phone" else _question_type(question),
                    required=question.required,
                    key=f"q:{question.key}",
                    options=tuple(question.options),
                    max_length=question.max_length,
                )
            )
        return fields

    def field_id(self, job_id: str, question_key: str) -> str:
        """uuid id/name of the custom question ``question_key`` (also "phone" and "linkedin")."""
        for field in self.fields_for(self.jobs[job_id]):
            if field.key == f"q:{question_key}":
                return field.path
        raise KeyError(question_key)

    # ---- routes ------------------------------------------------------------------------------------------
    def _install_routes(self) -> None:
        app = self.app

        @app.get("/api/job-posting/{job_id}")
        def job_posting(job_id: str) -> Response:
            job = self.jobs.get(job_id)
            if job is None or job.closed:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return JSONResponse(
                {
                    "id": job.id,
                    "title": job.title,
                    "company": self.company_name,
                    "location": job.location,
                    "description": [c for c in job.description.split("\n\n") if c.strip()],
                    "fields": [f.as_json() for f in self.fields_for(job)],
                }
            )

        @app.post("/api/autofill")
        async def autofill(request: Request) -> Response:
            await asyncio.sleep(self.autofill_delay_s)
            return JSONResponse({"fills": self.autofill_fills})

        @app.post("/api/non-user-graphql")
        async def graphql(request: Request) -> Response:
            return await self._submit(request)

        @app.get("/{co}")
        def board(co: str) -> Response:
            if co != self.company:
                return self._not_found()
            return self._board_page()

        @app.get("/{co}/{job_id}")
        def overview(co: str, job_id: str, request: Request) -> Response:
            return self._shell(co, job_id, request)

        @app.get("/{co}/{job_id}/application")
        def application(co: str, job_id: str, request: Request) -> Response:
            return self._shell(co, job_id, request)

    def _not_found(self) -> Response:
        return html_response(
            "Jobs",
            "<div id='root'><h1>Page not found</h1></div>",
            _CSS,
            status=404,
        )

    def _board_page(self) -> Response:
        rows = "".join(
            f"<a class='_posting_link ashby-job-posting-brief' href='/{esc(self.company)}/{esc(j.id)}'>"
            f"<h3 class='ashby-job-posting-brief-title'>{esc(j.title)}</h3>"
            f"<p class='ashby-job-posting-brief-details'>{esc(j.location)} • Intern</p></a>"
            for j in self.jobs.values()
            if not j.closed
        )
        body = (
            f"<div id='root'><div class='ashby-job-board'><h1>{esc(self.company_name)} Jobs</h1>"
            f"<div class='ashby-job-posting-brief-list'>{rows}</div></div></div>"
        )
        return html_response(f"Jobs at {self.company_name}", body, _CSS)

    def _shell(self, company: str, job_id: str, request: Request) -> Response:
        job = self.jobs.get(job_id)
        found = company == self.company and job is not None and not job.closed
        title = (
            f"{job.title} @ {self.company_name}" if job is not None and found else "Job not found"
        )
        config = json_for_script(
            {
                "company": self.company,
                "companyName": self.company_name,
                "jobId": job_id,
                "hash": self.css_hash,
                "delay": self.render_delay_s,
                "rerender": self.rerender_on_input,
                "selectWidget": self.select_widget,
                "autofill": self.autofill,
                "exts": list(ALLOWED_EXTENSIONS),
                "maxBytes": self.max_upload_bytes,
                "invisible": self.invisible_recaptcha,
            }
        )
        badge = recaptcha_badge(Origin.of(request)) if self.invisible_recaptcha else ""
        badge += cookie_banner(self.cookie_consent)
        body = (
            "<div id='root'><div class='_loading' role='status'>Loading...</div></div>"
            f"{badge}<script id='ab-config' type='application/json'>{config}</script>"
            f"<script>{_JS}</script>"
        )
        return html_response(title, body, _CSS, status=200 if found else 404)

    # ---- submission ----------------------------------------------------------------------------------------
    async def _submit(self, request: Request) -> Response:
        fields, files = await self.read_form(request)
        self.state["api_submits"] += 1
        job = self.jobs.get(fields.get("jobPostingId", [""])[0]) or self.jobs.get(
            request.query_params.get("jobPostingId", "")
        )
        if job is None or job.closed:
            return JSONResponse(
                {"success": False, "errors": {"_form": "Job not found."}}, status_code=404
            )
        errors = self._validate(job, fields, files)
        if errors:
            return JSONResponse({"success": False, "errors": errors}, status_code=422)
        if self.reject_as_spam:
            return JSONResponse(
                {
                    "success": False,
                    "errors": {
                        "_form": "Your application submission was flagged as possible spam. "
                        "Please try again later."
                    },
                },
                status_code=422,
            )
        self.record_submission(
            f"{request.url.path}?op=ApiSubmitSingleApplicationForm",
            fields,
            files,
            **self._meta(job, fields, files),
        )
        return JSONResponse({"success": True})

    def _values(self, fields: dict[str, list[str]], path: str) -> list[str]:
        return [v for v in fields.get(path, []) if v.strip() != ""]

    def _validate(
        self, job: MockJob, fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        for field in self.fields_for(job):
            if field.type == "File":
                message = self._validate_file(field, files)
                if message:
                    errors[field.path] = message
                continue
            values = self._values(fields, field.path)
            if not values:
                if field.required:
                    errors[field.path] = f"Missing entry for required field: {field.title}"
                continue
            if field.type == "Checkbox" and values[0] != "true" and field.required:
                errors[field.path] = f"Missing entry for required field: {field.title}"
            elif field.type == "Email" and not _EMAIL_RE.match(values[0].strip()):
                errors[field.path] = "Please enter a valid email address."
            elif field.max_length is not None and len(values[0]) > field.max_length:
                errors[field.path] = (
                    f"Response is too long (maximum {field.max_length} characters)."
                )
            elif (
                field.type == "Boolean"
                and values[0] not in {"true", "false"}
                or field.type in {"ValueSelect", "MultiValueSelect"}
                and any(v not in field.options for v in values)
            ):
                errors[field.path] = "Invalid selection."
        return errors

    def _validate_file(self, field: _AField, files: list[UploadedFile]) -> str | None:
        upload = next((f for f in files if f.field == field.path), None)
        if upload is None:
            return f"Missing entry for required field: {field.title}" if field.required else None
        extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            return f"Unsupported file type. Accepted: {', '.join(ALLOWED_EXTENSIONS)}"
        if not upload.data or len(upload.data) > self.max_upload_bytes:
            return "The file is empty or too large."
        return None

    def _meta(
        self, job: MockJob, fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> dict[str, Any]:
        standard: dict[str, str] = {}
        answers: dict[str, list[str]] = {}
        for field in self.fields_for(job):
            if field.type == "File":
                continue
            values = self._values(fields, field.path)
            if field.key in {"name", "email"}:
                standard[field.key] = values[0] if values else ""
                continue
            key = field.key.split(":", 1)[1]
            if field.type == "Boolean":
                answers[key] = ["Yes" if v == "true" else "No" for v in values]
            elif field.type == "Checkbox":
                answers[key] = ["checked"] if values and values[0] == "true" else []
            else:
                answers[key] = values
            if key in {"phone", "linkedin"}:
                standard[key] = values[0] if values else ""
        upload = next((f for f in files if f.field == "_systemfield_resume"), None)
        return {
            "job_id": job.id,
            "company": self.company,
            "standard": standard,
            "answers": answers,
            "uploads": {"resume": upload.filename} if upload else {},
        }


_CSS = """<style>
*{box-sizing:border-box}
body{margin:0;font-family:"Inter","Helvetica Neue",Helvetica,Arial,sans-serif;color:#1f2933;background:#fff;
 font-size:15px;line-height:1.5}
[hidden]{display:none!important}
#root{max-width:880px;margin:0 auto;padding:32px 20px 100px}
._loading{padding:60px 0;text-align:center;color:#616e7c}
.ashby-job-posting-heading{font-size:30px;margin:0 0 6px}
.ashby-job-posting-details{color:#616e7c;margin:0 0 18px}
.ashby-job-posting-brief{display:block;border:1px solid #d9e2ec;border-radius:6px;padding:14px 18px;margin:10px 0;
 text-decoration:none;color:inherit}
.ashby-job-posting-brief-title{margin:0}
[role=tablist]{display:flex;gap:6px;border-bottom:1px solid #d9e2ec;margin:20px 0}
[role=tab]{padding:10px 18px;text-decoration:none;color:#616e7c;border-bottom:2px solid transparent}
[role=tab][aria-selected=true]{color:#1f2933;border-bottom-color:#4b3fe0;font-weight:600}
.apply-link{display:inline-block;background:#4b3fe0;color:#fff;padding:10px 20px;border-radius:6px;
 text-decoration:none;font-weight:600;margin-top:16px}
.ashby-application-form-field-entry{margin:22px 0}
.ashby-application-form-question-title{display:block;font-weight:600;margin-bottom:6px}
.ashby-application-form-question-title[class*=_required_]::after{content:"*";color:#c0392b;margin-left:3px}
.ashby-application-form-field-entry input[type=text],.ashby-application-form-field-entry input[type=email],
.ashby-application-form-field-entry input[type=tel],.ashby-application-form-field-entry textarea,
.ashby-application-form-field-entry select{width:100%;padding:10px 12px;border:1px solid #bcccdc;border-radius:6px;
 font:inherit;background:#fff}
.ashby-application-form-field-entry [aria-invalid=true]{border-color:#c0392b}
._yesno{display:flex;gap:10px}
._yesno button{min-width:90px;padding:9px 18px;border:1px solid #bcccdc;background:#fff;border-radius:6px;
 font:inherit;cursor:pointer}
._yesno button[aria-pressed=true]{background:#4b3fe0;color:#fff;border-color:#4b3fe0}
._checkboxes label{display:flex;gap:8px;align-items:center;margin:4px 0;font-weight:400}
._upload{display:flex;align-items:center;gap:12px}
._button{background:#fff;border:1px solid #4b3fe0;color:#4b3fe0;border-radius:6px;padding:8px 16px;cursor:pointer;
 font:inherit;font-weight:600}
.ashby-application-form-submit-button{background:#4b3fe0;color:#fff;border:0;border-radius:6px;padding:12px 26px;
 font:inherit;font-weight:600;cursor:pointer}
.ashby-application-form-submit-button[disabled]{opacity:.6;cursor:default}
._error{color:#c0392b;font-size:14px;margin-top:5px}
._banner{background:#fdecea;border-left:4px solid #c0392b;padding:12px 16px;margin:18px 0}
.ashby-application-form-success-container{background:#e6f6ec;border-radius:8px;padding:28px;margin:22px 0}
.ashby-application-form-autofill-input-root{border:1px dashed #9fb3c8;border-radius:8px;padding:16px;margin:0 0 10px}
._combobox{position:relative}
._combobox [role=listbox]{position:absolute;left:0;right:0;top:100%;z-index:10;background:#fff;margin:4px 0 0;
 padding:4px 0;list-style:none;border:1px solid #bcccdc;border-radius:6px;max-height:240px;overflow:auto}
._combobox [role=option]{padding:8px 12px;cursor:pointer}
._combobox [role=option][aria-selected=true]{background:#e5e3ff}
._combobox [role=option][data-active=true]{background:#f0f4f8}
</style>"""

_JS = r"""
(function () {
  'use strict';
  var CFG = JSON.parse(document.getElementById('ab-config').textContent);
  var H = CFG.hash;
  var root = document.getElementById('root');
  var data = null;
  var state = 'loading';
  var form = null;

  function c(name, n) { return '_' + name + '_' + H + '_' + n; }
  function h(tag, props, kids) {
    var el = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) { return; }
      if (k === 'text') { el.textContent = v; }
      else if (k === 'class') { el.className = v; }
      else { el.setAttribute(k, v === true ? '' : v); }
    });
    (kids || []).forEach(function (kid) { if (kid) { el.appendChild(kid); } });
    return el;
  }
  function T(text) { return document.createTextNode(text); }

  function tabs(active) {
    var base = '/' + CFG.company + '/' + CFG.jobId;
    var list = h('div', {role: 'tablist', class: c('tabs', 12)});
    [['Overview', base, 'overview'], ['Application', base + '/application', 'application']].forEach(function (t) {
      var a = h('a', {role: 'tab', href: t[1], 'aria-selected': active === t[2] ? 'true' : 'false',
                      class: c('tab', 14) + (active === t[2] ? ' ' + c('active', 15) : ''), text: t[0]});
      a.addEventListener('click', function (e) {
        e.preventDefault();
        history.pushState(null, '', t[1]);
        render();
      });
      list.appendChild(a);
    });
    return list;
  }

  function overview() {
    var wrap = h('div', {class: 'ashby-job-posting-overview'});
    wrap.appendChild(h('p', {class: 'ashby-job-posting-details',
      text: data.location + ' · Intern · Hybrid'}));
    var desc = h('div', {class: 'ashby-job-posting-description ' + c('description', 21)});
    data.description.forEach(function (p) { desc.appendChild(h('p', {text: p})); });
    wrap.appendChild(desc);
    var apply = h('a', {class: 'apply-link ' + c('applyButton', 33), href: '/' + CFG.company + '/' + CFG.jobId + '/application',
                        text: 'Apply for this Job'});
    apply.addEventListener('click', function (e) {
      e.preventDefault();
      history.pushState(null, '', apply.getAttribute('href'));
      render();
    });
    wrap.appendChild(apply);
    return wrap;
  }

  /* ---------------------------------------------------------- field builders */
  function entry(f, control, opts) {
    var e = h('div', {class: 'ashby-application-form-field-entry ' + c('fieldEntry', 29), 'data-field-path': f.id});
    if (!(opts && opts.noLabel)) {
      e.appendChild(h('label', {id: f.id + '-label', 'for': f.id,
        class: 'ashby-application-form-question-title ' + c('heading', 53) + (f.required ? ' ' + c('required', 92) : ''),
        text: f.title}));
    }
    e.appendChild(control);
    return e;
  }
  function textControl(f) {
    var type = f.type === 'Email' ? 'email' : (f.type === 'Phone' ? 'tel' : 'text');
    return h('input', {id: f.id, name: f.id, type: type, class: c('input', 12), placeholder: 'Type here...',
                       maxlength: f.maxLength, 'aria-required': f.required ? 'true' : null});
  }
  function longTextControl(f) {
    return h('textarea', {id: f.id, name: f.id, class: c('input', 13), rows: '4', placeholder: 'Type here...',
                          maxlength: f.maxLength, 'aria-required': f.required ? 'true' : null});
  }
  function yesNoControl(f) {
    var group = h('div', {id: f.id, role: 'group', 'aria-labelledby': f.id + '-label', class: '_yesno ' + c('yesno', 20)});
    var hidden = h('input', {type: 'hidden', name: f.id, value: ''});
    ['Yes', 'No'].forEach(function (label) {
      var b = h('button', {type: 'button', class: c('option', 35), 'aria-pressed': 'false', text: label});
      b.addEventListener('click', function () {
        hidden.value = label === 'Yes' ? 'true' : 'false';
        group.querySelectorAll('button').forEach(function (other) {
          other.setAttribute('aria-pressed', other === b ? 'true' : 'false');
          other.classList.toggle(c('active', 36), other === b);
        });
        clearFieldError(group);
      });
      group.appendChild(b);
    });
    group.appendChild(hidden);
    return group;
  }
  function nativeSelect(f) {
    var sel = h('select', {id: f.id, name: f.id, class: c('select', 40), 'aria-required': f.required ? 'true' : null});
    sel.appendChild(h('option', {value: '', text: 'Select...'}));
    f.options.forEach(function (o) { sel.appendChild(h('option', {value: o, text: o})); });
    return sel;
  }
  function comboboxSelect(f) {
    var wrap = h('div', {class: '_combobox ' + c('combobox', 41)});
    var input = h('input', {id: f.id, type: 'text', role: 'combobox', 'aria-expanded': 'false', 'aria-autocomplete': 'list',
                            'aria-controls': f.id + '-listbox', autocomplete: 'off', placeholder: 'Start typing...',
                            class: c('input', 12), 'aria-required': f.required ? 'true' : null});
    var hidden = h('input', {type: 'hidden', name: f.id, value: ''});
    var list = h('ul', {id: f.id + '-listbox', role: 'listbox', hidden: true});
    var active = -1;
    var shown = [];
    var typing = false;
    function paint() {
      list.replaceChildren();
      var q = typing ? input.value.trim().toLowerCase() : '';
      shown = f.options.filter(function (o) { return !q || o.toLowerCase().indexOf(q) !== -1; });
      shown.forEach(function (o, i) {
        var li = h('li', {role: 'option', id: f.id + '-opt-' + i, text: o, 'aria-selected': hidden.value === o ? 'true' : 'false',
                          'data-active': i === active ? 'true' : 'false'});
        li.addEventListener('mousedown', function (e) { e.preventDefault(); choose(o); });
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }
    function close() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); }
    function choose(o) {
      hidden.value = o; input.value = o; typing = false; close(); clearFieldError(input);
    }
    input.addEventListener('focus', function () { typing = false; active = 0; paint(); });
    input.addEventListener('click', function () { if (list.hidden) { typing = false; active = 0; paint(); } });
    input.addEventListener('input', function () { hidden.value = ''; typing = true; active = 0; paint(); });
    input.addEventListener('blur', function () { close(); if (!hidden.value) { input.value = ''; } });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) { paint(); } active = Math.min(active + 1, shown.length - 1); paint(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); paint(); }
      else if (e.key === 'Enter') { if (!list.hidden && shown[active]) { e.preventDefault(); choose(shown[active]); } }
      else if (e.key === 'Escape') { close(); }
    });
    wrap.appendChild(input); wrap.appendChild(hidden); wrap.appendChild(list);
    return wrap;
  }
  function checkboxGroup(f) {
    var box = h('div', {id: f.id, role: 'group', 'aria-labelledby': f.id + '-label', class: '_checkboxes ' + c('checkboxGroup', 50)});
    f.options.forEach(function (o, i) {
      var id = f.id + '-' + i;
      var input = h('input', {type: 'checkbox', id: id, name: f.id, value: o});
      box.appendChild(h('label', {'for': id}, [input, h('span', {text: o})]));
    });
    return box;
  }
  function singleCheckbox(f) {
    var input = h('input', {type: 'checkbox', id: f.id, name: f.id, value: 'true'});
    var box = h('div', {class: '_checkboxes ' + c('checkbox', 51)});
    box.appendChild(h('label', {'for': f.id, class: 'ashby-application-form-question-title' + (f.required ? ' ' + c('required', 92) : '')},
      [input, h('span', {text: f.title})]));
    return box;
  }
  function fileControl(f) {
    var wrap = h('div', {class: '_upload ' + c('fileUpload', 60)});
    var input = h('input', {id: f.id, name: f.id, type: 'file', style: 'display:none',
                            accept: CFG.exts.map(function (e) { return '.' + e; }).join(',')});
    var button = h('button', {type: 'button', class: '_button ' + c('button', 61), text: 'Upload File'});
    var chip = h('div', {class: 'ashby-application-form-file-name ' + c('fileName', 62), hidden: true});
    var label = h('span', {});
    var remove = h('button', {type: 'button', 'aria-label': 'Remove file', class: '_button', text: '×'});
    chip.appendChild(label); chip.appendChild(remove);
    button.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      clearFieldError(input);
      var file = input.files && input.files[0];
      if (!file) { chip.hidden = true; button.hidden = false; return; }
      var ext = file.name.indexOf('.') >= 0 ? file.name.split('.').pop().toLowerCase() : '';
      if (CFG.exts.indexOf(ext) === -1 || file.size > CFG.maxBytes) {
        input.value = '';
        showFieldError(input, 'Unsupported file type or file too large.');
        return;
      }
      label.textContent = file.name;
      chip.hidden = false;
      button.hidden = true;
    });
    remove.addEventListener('click', function () { input.value = ''; chip.hidden = true; button.hidden = false; });
    wrap.appendChild(input); wrap.appendChild(button); wrap.appendChild(chip);
    return wrap;
  }

  function control(f) {
    switch (f.type) {
      case 'LongText': return entry(f, longTextControl(f));
      case 'Boolean': return entry(f, yesNoControl(f));
      case 'ValueSelect': return entry(f, CFG.selectWidget === 'combobox' ? comboboxSelect(f) : nativeSelect(f));
      case 'MultiValueSelect': return entry(f, checkboxGroup(f));
      case 'Checkbox': return entry(f, singleCheckbox(f), {noLabel: true});
      case 'File': return entry(f, fileControl(f));
      default: return entry(f, textControl(f));
    }
  }

  /* ---------------------------------------------------------- validation */
  function entryOf(el) { return el.closest('.ashby-application-form-field-entry'); }
  function clearFieldError(el) {
    var e = entryOf(el);
    if (!e) { return; }
    e.querySelectorAll('[role=alert]').forEach(function (a) { a.remove(); });
    e.querySelectorAll('[aria-invalid]').forEach(function (i) { i.removeAttribute('aria-invalid'); });
  }
  function showFieldError(el, message) {
    var e = entryOf(el);
    clearFieldError(el);
    var path = e.getAttribute('data-field-path');
    var err = h('div', {role: 'alert', id: path + '-error', class: '_error ' + c('error', 70), text: message});
    e.appendChild(err);
    var target = e.querySelector('input:not([type=hidden]):not([type=file]), textarea, select, [role=group]');
    if (target) { target.setAttribute('aria-invalid', 'true'); target.setAttribute('aria-describedby', err.id); }
  }
  function valueOf(f) {
    var e = form.querySelector('[data-field-path="' + f.id + '"]');
    if (f.type === 'File') { var fi = e.querySelector('input[type=file]'); return fi.files && fi.files.length ? 'file' : ''; }
    if (f.type === 'MultiValueSelect') { return e.querySelector('input:checked') ? 'x' : ''; }
    if (f.type === 'Checkbox') { return e.querySelector('input:checked') ? 'true' : ''; }
    if (f.type === 'Boolean' || (f.type === 'ValueSelect' && CFG.selectWidget === 'combobox')) {
      return e.querySelector('input[type=hidden]').value;
    }
    var i = e.querySelector('input, textarea, select');
    return (i.value || '').trim();
  }
  function validate() {
    var bad = [];
    data.fields.forEach(function (f) {
      var e = form.querySelector('[data-field-path="' + f.id + '"]');
      clearFieldError(e);
      var v = valueOf(f);
      if (!v) {
        if (f.required) { showFieldError(e, 'Missing entry for required field: ' + f.title); bad.push(f); }
        return;
      }
      if (f.type === 'Email' && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) {
        showFieldError(e, 'Please enter a valid email address.'); bad.push(f);
      }
    });
    return bad;
  }
  function banner(text) {
    var old = document.getElementById('form-banner');
    if (old) { old.remove(); }
    if (!text) { return; }
    var b = h('div', {id: 'form-banner', role: 'alert', class: '_banner ' + c('banner', 80), text: text});
    form.insertBefore(b, form.querySelector('.ashby-application-form-submit-button').parentNode);
  }

  function applyFills(fills) {
    Object.keys(fills || {}).forEach(function (id) {
      var el = form.querySelector('[id="' + id + '"]');
      if (el && !el.value) {
        el.value = fills[id];
        el.dispatchEvent(new Event('input', {bubbles: true}));
      }
    });
  }
  function autofillPanel() {
    var panel = h('div', {class: 'ashby-application-form-autofill-input-root ' + c('autofill', 90)});
    panel.appendChild(h('h3', {text: 'Autofill from resume'}));
    panel.appendChild(h('p', {text: 'Upload your resume here to autofill key application fields.'}));
    var input = h('input', {type: 'file', id: '_autofill_resume', style: 'display:none'});
    var button = h('button', {type: 'button', class: '_button', text: 'Upload File'});
    var status = h('div', {role: 'status', class: 'autofill-status'});
    button.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file) { return; }
      status.textContent = 'Autofilling from your resume...';
      var fd = new FormData();
      fd.append('resume', file);
      fetch('/api/autofill', {method: 'POST', body: fd}).then(function (r) { return r.json(); }).then(function (j) {
        applyFills(j.fills);
        var target = form.querySelector('#_systemfield_resume');
        if (target && window.DataTransfer) {
          var dt = new DataTransfer(); dt.items.add(file); target.files = dt.files;
          target.dispatchEvent(new Event('change', {bubbles: true}));
        }
        status.textContent = 'Your resume was uploaded. We filled in what we could.';
      }).catch(function () { status.textContent = 'We could not read that file.'; });
    });
    panel.appendChild(input); panel.appendChild(button); panel.appendChild(status);
    return panel;
  }

  /* ---------------------------------------------------------- application form */
  function applicationForm() {
    var container = h('div', {class: 'ashby-application-form-container ' + c('container', 71)});
    form = h('form', {class: 'ashby-application-form ' + c('form', 72), novalidate: true, autocomplete: 'on'});
    if (CFG.autofill) { form.appendChild(autofillPanel()); }
    data.fields.forEach(function (f) { form.appendChild(control(f)); });
    var footer = h('div', {class: c('footer', 73)});
    footer.appendChild(h('button', {type: 'submit', class: 'ashby-application-form-submit-button ' + c('button', 74)},
      [h('span', {text: 'Submit Application'})]));
    form.appendChild(footer);
    form.addEventListener('submit', onSubmit);
    form.addEventListener('input', function (ev) {
      if (CFG.rerender) { rerender(ev); }
      clearFieldError(ev.target);
    });
    form.addEventListener('change', function (ev) { clearFieldError(ev.target); });
    container.appendChild(form);
    return container;
  }
  function rerender(ev) {
    var t = ev.target;
    var textual = t instanceof HTMLTextAreaElement ||
      (t instanceof HTMLInputElement && (t.type === 'text' || t.type === 'tel' || t.type === 'email') && t.getAttribute('role') !== 'combobox');
    if (!textual) { return; }
    var clone = t.cloneNode(true);
    clone.value = t.value;
    var pos = t.selectionStart;
    t.replaceWith(clone);
    clone.focus();
    try { clone.setSelectionRange(pos, pos); } catch (err) { /* not selectable */ }
  }
  function showSuccess() {
    var box = h('div', {class: 'ashby-application-form-success-container ' + c('success', 75), role: 'status'});
    box.appendChild(h('h2', {text: 'Application submitted'}));
    box.appendChild(h('p', {text: "Your application was successfully submitted. We'll contact you if there is a fit."}));
    form.parentNode.replaceChildren(box);
    window.scrollTo(0, 0);
  }
  function onSubmit(ev) {
    ev.preventDefault();
    banner('');
    var bad = validate();
    if (bad.length) {
      banner('Your form needs corrections. Please review the highlighted fields and try again.');
      var first = form.querySelector('[data-field-path="' + bad[0].id + '"] input:not([type=hidden]):not([type=file]), ' +
        '[data-field-path="' + bad[0].id + '"] textarea, [data-field-path="' + bad[0].id + '"] select, ' +
        '[data-field-path="' + bad[0].id + '"] button');
      if (first) { first.scrollIntoView({block: 'center'}); first.focus(); }
      return;
    }
    var button = form.querySelector('.ashby-application-form-submit-button');
    var body = new FormData(form);
    body.append('jobPostingId', CFG.jobId);
    if (CFG.invisible) { body.append('g-recaptcha-response', 'mock-invisible-recaptcha-token'); }
    button.disabled = true;
    fetch('/api/non-user-graphql?op=ApiSubmitSingleApplicationForm', {method: 'POST', body: body})
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return {status: r.status, body: j}; });
      })
      .then(function (res) {
        button.disabled = false;
        if (res.status === 200 && res.body.success) { showSuccess(); return; }
        if (res.status === 422 && res.body.errors) {
          Object.keys(res.body.errors).forEach(function (id) {
            var e = form.querySelector('[data-field-path="' + id + '"]');
            if (e) { showFieldError(e, res.body.errors[id]); } else { banner(res.body.errors[id]); }
          });
          return;
        }
        banner('Something went wrong submitting your application. Please try again.');
      })
      .catch(function () {
        button.disabled = false;
        banner('Something went wrong submitting your application. Please try again.');
      });
  }

  /* ---------------------------------------------------------- page */
  function currentTab() {
    return location.pathname.replace(/\/+$/, '').slice(-12) === '/application' ? 'application' : 'overview';
  }
  function render() {
    root.replaceChildren();
    if (state === 'error') {
      root.appendChild(h('div', {role: 'alert', class: '_banner'}, [h('strong', {text: 'Something went wrong. '}),
        T('We could not load this job. Please refresh the page.')]));
      return;
    }
    if (state === 'notfound') {
      root.appendChild(h('div', {}, [h('h1', {text: 'Job not found'}),
        h('p', {text: 'The job you requested was not found. It may no longer be accepting applications.'})]));
      return;
    }
    if (state !== 'ready') { root.appendChild(h('div', {class: '_loading', role: 'status', text: 'Loading...'})); return; }
    var tab = currentTab();
    var page = h('div', {class: 'ashby-job-posting ' + c('page', 1)});
    page.appendChild(h('h1', {class: 'ashby-job-posting-heading ' + c('title', 2), text: data.title}));
    page.appendChild(h('p', {class: 'ashby-job-posting-company', text: data.company}));
    page.appendChild(tabs(tab));
    page.appendChild(tab === 'application' ? applicationForm() : overview());
    root.appendChild(page);
  }
  window.addEventListener('popstate', render);

  fetch('/api/job-posting/' + CFG.jobId).then(function (r) {
    if (r.status === 404) { state = 'notfound'; return null; }
    if (!r.ok) { throw new Error('status ' + r.status); }
    return r.json();
  }).then(function (json) {
    if (json) { data = json; state = 'ready'; }
    setTimeout(render, CFG.delay * 1000);
  }).catch(function () {
    state = 'error';
    setTimeout(render, CFG.delay * 1000);
  });
})();
"""


def make_site(
    company: str = "acme",
    jobs: Sequence[MockJob] | None = None,
    *,
    name: str | None = None,
    company_name: str | None = None,
    render_delay_s: float = 0.0,
    rerender_on_input: bool = False,
    select_widget: SelectWidget = "native",
    autofill: bool = False,
    autofill_delay_s: float = 0.6,
    autofill_fills: dict[str, str] | None = None,
    invisible_recaptcha: bool = False,
    reject_as_spam: bool = False,
    max_upload_bytes: int = 10 * 1024 * 1024,
    cookie_consent: CookieBanner | None = None,
) -> AshbySite:
    """Build a mock Ashby board (see the module docstring for what every option guarantees)."""
    return AshbySite(
        company,
        list(jobs) if jobs else default_jobs(),
        name=name or "ashby",
        company_name=company_name or company.replace("-", " ").replace("_", " ").title(),
        render_delay_s=render_delay_s,
        rerender_on_input=rerender_on_input,
        select_widget=select_widget,
        autofill=autofill,
        autofill_delay_s=autofill_delay_s,
        autofill_fills=autofill_fills,
        invisible_recaptcha=invisible_recaptcha,
        reject_as_spam=reject_as_spam,
        max_upload_bytes=max_upload_bytes,
        cookie_consent=cookie_consent,
    )
