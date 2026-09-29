"""Hermetic mock of a generic, employer-specific careers portal (Tesla / Cemex / Keurig Dr Pepper style).

The point of this site is that NOTHING about its DOM is standard: no ``data-automation-id``, no ``name="email"``, no
stable ids. A form filler has to find fields by their label text, ``aria-label``, placeholder or visible caption, and to
recognise the buttons by their wording. Everything below is guaranteed by ``tests/unit/testing/test_mock_employer_portal.py``.

Usage::

    site = make_site("acme", jobs=None, variant="confirmation", validation_quirks=False, cover_letter=True)
    hub = MockHub(); hub.add(site); hub.start()          # host careers.<company>.com, site.name == "employer-portal-<company>"
    page.goto(site.job_url())                            # /careers/jobs/<id>-<slug>   (site.apply_url() starts the form)

Options: ``variant`` in {"confirmation", "silent", "signup"}; ``validation_quirks`` (bool); ``cover_letter`` (bool, an
optional upload on the Questions page); ``host`` / ``name`` overrides; ``seed`` (drives the random field names);
``latency_ms`` (int or (lo, hi), default 0, applied to every page request).

Pages (a classic multi-page wizard: every step is a server round trip, the URL is ``/careers/apply/<job id>/<n>``)
    1 "Personal details"   first name, last name, email, phone, street address, city, state (select), ZIP, LinkedIn URL,
                           resume upload (required)
    2 "Education"          school, degree (select), major, GPA, expected graduation, "currently enrolled?" (radios)
    3 "Questions"          one control per ``MockJob.questions`` entry (select, radio group, checkbox, checkbox group,
                           textarea, text, multiselect) and, with ``cover_letter``, an optional cover-letter upload
    4 "Review & submit"    read-only summary, a required certification checkbox, an optional privacy checkbox, and the
                           final button. There are no ``id`` / ``name`` values a script can guess: ``name`` is ``fld_<4
                           digits>`` (stable per site + seed, different per field) and ids look like ``c_1f9a``.
    Every page shows ``<h1>`` = the page title and "Step n of 4". Reaching a page whose predecessors are not saved redirects
    to the first unsaved page. Values entered earlier are re-displayed on every visit (never the uploaded files).

How fields are labelled (each field uses ONE of these, fixed per field)
    for          ``<label for=id>First name *</label><input id=id>``
    aria         ``<input aria-label="Last name / surname">`` with no visible label
    placeholder  ``<input placeholder="Email address">`` with no label at all (selects: the first option is the caption)
    wrap         ``<label>Street address <input></label>``
    adjacent     ``<span>Mobile phone</span>`` in front of the control, NOT associated with it (the control only has a
                 format placeholder such as "(555) 555-0123")
    labelledby   ``<span id=x>LinkedIn profile</span><input aria-labelledby=x>``
    legend       ``<fieldset><legend>Are you currently enrolled?</legend>`` with one ``<label><input type=radio> Yes</label>`` each
    A trailing " *" marks a required field wherever a visible label exists; the placeholder / aria styles carry no marker.

Buttons (wording is the only handle; the three forward buttons differ on purpose)
    page 1   ``<button type=submit>Save draft</button>`` (BEFORE) ``<button type=submit>Next</button>``
    page 2   ``<a>Back</a>`` and ``<input type=submit value="Continue">``
    page 3   ``<button type=submit>Previous</button>`` and ``<a role=button>Next</a>`` (a link that submits the form by script)
    page 4   ``<button type=submit>Edit application</button>``, ``<a>Cancel</a>`` and ``<button type=submit>Submit application</button>``
    "Save draft" keeps the page (and shows "Your progress has been saved."); Back / Previous / Edit application go one page
    back without validation; "Cancel" leaves for the job list. Only Next / Continue / Submit application move forward.

Validation is server side and only visible after the forward button was pressed: the page is shown again (HTTP 200, same
URL) with ``<div role=alert>Please correct the highlighted fields and try again.</div>`` and a ``<div class="fe">`` message under
each bad field (``aria-invalid`` only on the ``for``-labelled ones). Required fields always are checked (email must
look like an email). With ``validation_quirks=True`` the formats are strict too: phone ``(555) 555-0123``, ZIP exactly 5
digits, graduation ``MM/YYYY``, GPA 0.00-4.00, names without digits; and a failed POST DISCARDS the uploaded files,
so the resume has to be attached again together with the corrected fields (the error says so).

Variants (what happens after "Submit application")
    confirmation   redirect to ``/careers/apply/<id>/thank-you``: "Thank you for applying!" and "Your application has been
                   received. Your reference number is APP-#####."
    silent         redirect to the job list ``/careers`` with NO confirmation text whatsoever (the submission is still recorded)
    signup         like confirmation, but ``/careers/apply/<id>`` first sends anonymous visitors to ``/account/create``
                   (email, password, confirm password, "Create account"; "Sign in" at ``/account/login``). Fields are
                   label-only as well. An existing email is refused ("An account already exists ...").
    Submitting a job a second time in the same session records nothing new and lands on the same page as the first time.

Job pages: ``/careers`` lists open jobs; ``/careers/jobs/<id>-<slug>`` shows the description with an "Apply now" link;
a closed job says "This position is no longer accepting applications." and has no apply link; unknown ids are 404.

Recorded submission (``site.submissions[-1]``): ``fields`` are keyed by LOGICAL names (not the random ones): first_name,
last_name, email, phone, address_line1, city, state, postal_code, linkedin, school, degree, major, gpa, graduation,
enrolled, every ``MockQuestion.key``, certify ("true"), privacy ("true" when ticked); ``files`` are UploadedFile(field=
"resume" | "cover_letter"); ``meta`` has variant, job_id, reference (APP-#####, also for "silent"), account and
``raw_names`` {logical name: random name}. Python helpers: ``site.job_url(id)``, ``site.apply_url(id)``,
``site.add_account(email, password)``, ``site.accounts``, ``site.references``, ``site.events``, ``site.names``.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from autoapply.testing.mock_ats.base import (
    STANDARD_QUESTIONS,
    MockJob,
    MockQuestion,
    MockSite,
    UploadedFile,
    html_page,
)
from autoapply.testing.mock_ats.workday import US_STATES

Variant = Literal["confirmation", "silent", "signup"]
PAGE_TITLES = ("Personal details", "Education", "Questions", "Review & submit")
DEGREES = ("Associate", "Bachelor's", "Master's", "Doctorate", "Other")
COOKIE = "SESSIONREF"
ERROR_BANNER = "Please correct the highlighted fields and try again."
_ESC = html.escape


@dataclass(frozen=True)
class Fld:
    """One form field: logical key (used for recording), control kind and how its label is attached."""

    key: str
    kind: str  # text | email | tel | password | textarea | select | multiselect | radio | checkbox | checkboxes | file
    label: str
    style: str  # for | aria | placeholder | wrap | adjacent | labelledby | legend
    required: bool = False
    options: tuple[str, ...] = ()
    hint: str = ""  # format placeholder for controls whose caption is not the placeholder
    accept: str = ""
    max_length: int | None = None


@dataclass
class PortalSession:
    sid: str
    email: str | None = None
    values: dict[str, dict[int, dict[str, list[str]]]] = field(default_factory=dict)
    files: dict[str, dict[str, UploadedFile]] = field(default_factory=dict)
    done: dict[str, set[int]] = field(default_factory=dict)  # job id -> saved pages
    submitted: dict[str, str] = field(default_factory=dict)  # job id -> reference


def _default_jobs() -> list[MockJob]:
    q = STANDARD_QUESTIONS
    return [
        MockJob(
            id="4421",
            title="Business Operations Intern - Summer 2027",
            location="Fremont, CA",
            questions=(
                q["work_auth"],
                q["sponsorship"],
                q["relocate"],
                q["referral"],
                q["why_role"],
                q["salary"],
            ),
        )
    ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_PHONE_STRICT = re.compile(r"\(\d{3}\) \d{3}-\d{4}")
_GRAD_STRICT = re.compile(r"(0[1-9]|1[0-2])/\d{4}")
_GPA_STRICT = re.compile(r"[0-4](\.\d{1,2})?")


class EmployerPortalSite(MockSite):
    """A mock employer careers portal with a label-only, multi-page application form."""

    def __init__(
        self,
        company: str = "acme",
        jobs: list[MockJob] | None = None,
        *,
        variant: Variant = "confirmation",
        validation_quirks: bool = False,
        cover_letter: bool = True,
        host: str | None = None,
        name: str | None = None,
        company_name: str | None = None,
        seed: int = 7,
        latency_ms: int | tuple[int, int] = 0,
    ) -> None:
        if variant not in ("confirmation", "silent", "signup"):
            raise ValueError(f"unknown variant {variant!r}")
        slug = _slug(company) or "company"
        super().__init__(name or f"employer-portal-{slug}", host or f"careers.{slug}.com")
        self.company_name = company_name or company.replace("-", " ").replace("_", " ").title()
        self.variant: Variant = variant
        self.validation_quirks = validation_quirks
        self.cover_letter = cover_letter
        self.latency_ms: tuple[int, int] = (
            (latency_ms, latency_ms) if isinstance(latency_ms, int) else latency_ms
        )
        self.seed = seed
        self._rng = random.Random(seed)
        self.names: dict[str, str] = {}  # logical field key -> random form name
        self.references: list[str] = []
        for job in jobs if jobs is not None else _default_jobs():
            self.jobs[job.id] = job
        self.state.update(accounts={}, sessions={}, events=[])
        self._install_routes()

    # -- public helpers ------------------------------------------------------------------------------
    @property
    def accounts(self) -> dict[str, str]:
        """email -> password of the accounts of the ``signup`` variant."""
        accounts: dict[str, str] = self.state["accounts"]
        return accounts

    @property
    def events(self) -> list[str]:
        events: list[str] = self.state["events"]
        return events

    def add_account(self, email: str, password: str) -> None:
        self.accounts[email.strip().lower()] = password

    def job_path(self, job: MockJob) -> str:
        return f"/careers/jobs/{job.id}-{_slug(job.title)}"

    def _job(self, job_id: str | None) -> MockJob:
        return next(iter(self.jobs.values())) if job_id is None else self.jobs[job_id]

    def job_url(self, job_id: str | None = None) -> str:
        return self.url(self.job_path(self._job(job_id)))

    def apply_url(self, job_id: str | None = None, page: int | None = None) -> str:
        job = self._job(job_id)
        return self.url(f"/careers/apply/{job.id}" + (f"/{page}" if page else ""))

    # -- randomised, stable identifiers ----------------------------------------------------------------
    def _name(self, key: str) -> str:
        if key not in self.names:
            number = random.Random(f"{self.seed}:name:{key}").randint(1000, 9999)
            taken = set(self.names.values())
            while f"fld_{number}" in taken:
                number += 1
            self.names[key] = f"fld_{number}"
        return self.names[key]

    def _cid(self, key: str) -> str:
        return "c_" + format(random.Random(f"{self.seed}:id:{key}").getrandbits(16), "04x")

    # -- session plumbing --------------------------------------------------------------------------------
    def _session(self, request: Request) -> tuple[PortalSession, bool]:
        sessions: dict[str, PortalSession] = self.state["sessions"]
        sid = request.cookies.get(COOKIE, "")
        if sid in sessions:
            return sessions[sid], False
        sess = PortalSession(sid=secrets.token_hex(12))
        sessions[sess.sid] = sess
        return sess, True

    def _stamp(self, resp: Response, sess: PortalSession, new: bool) -> Response:
        if new:
            resp.set_cookie(COOKIE, sess.sid, httponly=True, samesite="lax", path="/")
        return resp

    async def _delay(self) -> None:
        low, high = self.latency_ms
        if high > 0:
            await asyncio.sleep(self._rng.uniform(low, high) / 1000)

    # -- fields ----------------------------------------------------------------------------------------------------
    def _page_fields(self, job: MockJob, page: int) -> list[Fld]:
        if page == 1:
            fields = [
                Fld("first_name", "text", "First name", "for", True),
                Fld("last_name", "text", "Last name / surname", "aria", True),
                Fld("email", "email", "Email address", "placeholder", True),
                Fld("phone", "tel", "Mobile phone", "adjacent", True, hint="(555) 555-0123"),
                Fld("address_line1", "text", "Street address", "wrap", True),
                Fld("city", "text", "City", "for", True),
                Fld("state", "select", "State / Province", "placeholder", True, tuple(US_STATES)),
                Fld("postal_code", "text", "ZIP / Postal code", "placeholder", True),
                Fld("linkedin", "text", "LinkedIn profile", "labelledby", False, hint="https://"),
                Fld(
                    "resume",
                    "file",
                    "Upload your resume / CV",
                    "for",
                    True,
                    accept=".pdf,.doc,.docx",
                ),
            ]
            return fields
        if page == 2:
            return [
                Fld("school", "text", "University / College", "for", True),
                Fld("degree", "select", "Degree type", "wrap", True, DEGREES),
                Fld("major", "text", "Field of study / major", "aria", True),
                Fld("gpa", "text", "GPA (4.0 scale)", "placeholder", False),
                Fld("graduation", "text", "Expected graduation", "adjacent", True, hint="MM/YYYY"),
                Fld(
                    "enrolled",
                    "radio",
                    "Are you currently enrolled?",
                    "legend",
                    False,
                    ("Yes", "No"),
                ),
            ]
        if page == 3:
            fields = [self._question_field(q, i) for i, q in enumerate(job.questions)]
            if self.cover_letter:
                fields.append(
                    Fld(
                        "cover_letter",
                        "file",
                        "Cover letter (optional)",
                        "for",
                        False,
                        accept=".pdf,.doc,.docx",
                    )
                )
            return fields
        return []

    def _question_field(self, q: MockQuestion, index: int) -> Fld:
        common: dict[str, Any] = {
            "required": q.required,
            "options": q.options,
            "max_length": q.max_length,
        }
        if q.kind == "select":
            return Fld(q.key, "select", q.label, ("for", "wrap")[index % 2], **common)
        if q.kind == "radio":
            return Fld(q.key, "radio", q.label, "legend", **common)
        if q.kind == "checkbox":
            if q.options:
                return Fld(q.key, "checkboxes", q.label, "legend", **common)
            return Fld(q.key, "checkbox", q.label, "wrap", **common)
        if q.kind == "multiselect":
            return Fld(q.key, "multiselect", q.label, "for", **common)
        if q.kind == "textarea":
            return Fld(q.key, "textarea", q.label, ("for", "aria")[index % 2], **common)
        return Fld(q.key, "text", q.label, ("placeholder", "for")[index % 2], **common)

    def _control(self, f: Fld, values: list[str], invalid: bool, extra: str) -> str:
        name = self._name(f.key if f.kind != "file" else f"file:{f.key}")
        bad = ' aria-invalid="true"' if invalid and f.style == "for" else ""
        value = values[0] if values else ""
        placeholder = f.label if f.style == "placeholder" else f.hint
        ph = f' placeholder="{_ESC(placeholder)}"' if placeholder else ""
        if f.kind in ("text", "email", "tel", "password"):
            maxlen = f' maxlength="{f.max_length}"' if f.max_length else ""
            return f'<input type="{f.kind}" name="{name}" value="{_ESC(value)}"{ph}{maxlen}{bad} {extra}>'
        if f.kind == "textarea":
            maxlen = f' maxlength="{f.max_length}"' if f.max_length else ""
            return f'<textarea name="{name}" rows="4"{ph}{maxlen}{bad} {extra}>{_ESC(value)}</textarea>'
        if f.kind in ("select", "multiselect"):
            multiple = " multiple" if f.kind == "multiselect" else ""
            caption = f.label if f.style == "placeholder" else "Select..."
            blank = "" if multiple else f'<option value="">{_ESC(caption)}</option>'
            opts = "".join(
                f'<option value="{_ESC(o)}"{" selected" if o in values else ""}>{_ESC(o)}</option>'
                for o in f.options
            )
            return f'<select name="{name}"{multiple}{bad} {extra}>{blank}{opts}</select>'
        if f.kind == "file":
            accept = f' accept="{f.accept}"' if f.accept else ""
            return f'<input type="file" name="{name}"{accept} {extra}>'
        raise ValueError(f.kind)  # pragma: no cover

    def _field_html(self, f: Fld, values: list[str], error: str | None) -> str:
        cid = self._cid(f.key)
        mark = " *" if f.required else ""
        text = _ESC(f.label + (mark if f.style not in ("aria", "placeholder") else ""))
        if f.kind in ("radio", "checkboxes"):
            name = self._name(f.key)
            kind = "radio" if f.kind == "radio" else "checkbox"
            items = "".join(
                f'<label><input type="{kind}" name="{name}" value="{_ESC(o)}"'
                f"{' checked' if o in values else ''}> {_ESC(o)}</label> "
                for o in f.options
            )
            inner = f"<fieldset><legend>{text}</legend>{items}</fieldset>"
        elif f.kind == "checkbox":
            name = self._name(f.key)
            checked = " checked" if values else ""
            inner = (
                f'<label><input type="checkbox" name="{name}" value="yes"{checked}> {text}</label>'
            )
        else:
            invalid = error is not None
            if f.style == "for":
                inner = f'<label for="{cid}">{text}</label>' + self._control(
                    f, values, invalid, f'id="{cid}"'
                )
            elif f.style == "aria":
                inner = self._control(f, values, invalid, f'aria-label="{_ESC(f.label)}"')
            elif f.style == "placeholder":
                inner = self._control(f, values, invalid, "")
            elif f.style == "wrap":
                inner = f"<label>{text} {self._control(f, values, invalid, '')}</label>"
            elif f.style == "adjacent":
                inner = f'<span class="cap">{text}</span>' + self._control(f, values, invalid, "")
            else:  # labelledby
                inner = f'<span id="lb_{cid}">{text}</span>' + self._control(
                    f, values, invalid, f'aria-labelledby="lb_{cid}"'
                )
        message = f'<div class="fe">{_ESC(error)}</div>' if error else ""
        return f'<div class="row">{inner}{message}</div>'

    # -- page shells ----------------------------------------------------------------------------------------------------
    _CSS = (
        "body{font-family:Arial,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}"
        ".row{margin:12px 0}.row label,.row .cap{display:block;font-weight:600;margin-bottom:4px}"
        ".row label>input,.row label>select{display:block}fieldset{border:1px solid #ccc}"
        "fieldset label{display:inline-block;font-weight:400;margin-right:12px}"
        "input[type=text],input[type=email],input[type=tel],input[type=password],select,textarea{width:100%;max-width:420px;padding:8px}"
        ".fe{color:#b00020;margin-top:4px}.alert{background:#fde7e9;border:1px solid #b00020;padding:8px;margin:12px 0}"
        ".note{background:#e8f4fd;padding:8px;margin:12px 0}.btns{margin-top:24px;display:flex;gap:12px;align-items:center}"
        "button,input[type=submit],a.btn{padding:10px 18px;border:1px solid #124;background:#124;color:#fff;cursor:pointer;text-decoration:none;font:inherit}"
        "a.plain{color:#124}"
    )

    def _shell(self, title: str, body: str, *, status: int = 200) -> HTMLResponse:
        page = html_page(
            f"{title} - {self.company_name} Careers",
            f"<header><strong>{_ESC(self.company_name)}</strong> Careers</header>{body}",
            f"<style>{self._CSS}</style>",
        )
        page.status_code = status
        return page

    def _form_page(
        self,
        job: MockJob,
        page: int,
        fields_html: str,
        buttons: str,
        *,
        errors: bool = False,
        note: str = "",
    ) -> HTMLResponse:
        banner = f'<div class="alert" role="alert">{ERROR_BANNER}</div>' if errors else ""
        notice = f'<div class="note" role="status">{_ESC(note)}</div>' if note else ""
        body = (
            f"<h1>{PAGE_TITLES[page - 1]}</h1><p>Step {page} of 4 &middot; {_ESC(job.title)}</p>{banner}{notice}"
            f'<form method="post" enctype="multipart/form-data" novalidate action="/careers/apply/{job.id}/{page}">'
            f'{fields_html}<div class="btns">{buttons}</div></form>'
        )
        return self._shell(PAGE_TITLES[page - 1], body)

    _BUTTONS = {
        1: '<button type="submit" name="nav" value="save">Save draft</button>'
        '<button type="submit" name="nav" value="next">Next</button>',
        2: '<a class="plain" href="/careers/apply/{job}/1">Back</a>'
        '<input type="submit" name="nav" value="Continue">',
        3: '<button type="submit" name="nav" value="back">Previous</button>'
        '<a class="btn" href="#" role="button" '
        "onclick=\"this.closest('form').submit();return false;\">Next</a>",
        4: '<button type="submit" name="nav" value="back">Edit application</button>'
        '<a class="plain" href="/careers">Cancel</a>'
        '<button type="submit" name="nav" value="submit">Submit application</button>',
    }

    # -- routes --------------------------------------------------------------------------------------------------------------
    def _install_routes(self) -> None:
        add = self.app.add_api_route
        add("/", self._h_root, methods=["GET"], include_in_schema=False)
        add("/favicon.ico", self._h_favicon, methods=["GET"], include_in_schema=False)
        add("/careers", self._h_list, methods=["GET"], include_in_schema=False)
        add("/careers/jobs/{slug}", self._h_job, methods=["GET"], include_in_schema=False)
        add("/careers/apply/{job_id}", self._h_apply, methods=["GET"], include_in_schema=False)
        add(
            "/careers/apply/{job_id}/thank-you",
            self._h_thanks,
            methods=["GET"],
            include_in_schema=False,
        )
        add(
            "/careers/apply/{job_id}/{page}", self._h_page, methods=["GET"], include_in_schema=False
        )
        add(
            "/careers/apply/{job_id}/{page}",
            self._h_post,
            methods=["POST"],
            include_in_schema=False,
        )
        add("/account/create", self._h_create_form, methods=["GET"], include_in_schema=False)
        add("/account/create", self._h_create, methods=["POST"], include_in_schema=False)
        add("/account/login", self._h_login_form, methods=["GET"], include_in_schema=False)
        add("/account/login", self._h_login, methods=["POST"], include_in_schema=False)

    async def _h_favicon(self) -> Response:
        return Response(status_code=204)

    async def _h_root(self) -> Response:
        return RedirectResponse("/careers", status_code=302)

    async def _h_list(self, request: Request) -> Response:
        await self._delay()
        sess, new = self._session(request)
        items = "".join(
            f'<li><a href="{self.job_path(j)}">{_ESC(j.title)}</a> &mdash; {_ESC(j.location)}</li>'
            for j in self.jobs.values()
            if not j.closed
        )
        body = f"<h1>Open positions</h1><ul>{items}</ul>"
        return self._stamp(self._shell("Open positions", body), sess, new)

    def _job_from_slug(self, slug: str) -> MockJob | None:
        job_id = slug.split("-", 1)[0]
        return self.jobs.get(job_id)

    async def _h_job(self, request: Request, slug: str) -> Response:
        await self._delay()
        sess, new = self._session(request)
        job = self._job_from_slug(slug)
        if job is None:
            return self._shell("Not found", "<h1>Page not found</h1>", status=404)
        if job.closed:
            body = (
                f"<h1>{_ESC(job.title)}</h1><p>This position is no longer accepting applications.</p>"
                '<p><a href="/careers">Back to open positions</a></p>'
            )
        else:
            paragraphs = "".join(f"<p>{_ESC(p)}</p>" for p in job.description.split("\n") if p)
            body = (
                f"<h1>{_ESC(job.title)}</h1><p>{_ESC(job.location)}</p>{paragraphs}"
                f'<p><a class="btn" href="/careers/apply/{job.id}">Apply now</a></p>'
            )
        return self._stamp(self._shell(job.title, body), sess, new)

    def _open_job(self, job_id: str) -> MockJob | None:
        job = self.jobs.get(job_id)
        return None if job is None or job.closed else job

    def _login_gate(self, sess: PortalSession, job: MockJob) -> Response | None:
        if self.variant == "signup" and sess.email is None:
            return RedirectResponse(
                f"/account/create?next=/careers/apply/{job.id}", status_code=303
            )
        return None

    async def _h_apply(self, request: Request, job_id: str) -> Response:
        await self._delay()
        sess, new = self._session(request)
        job = self._open_job(job_id)
        if job is None:
            return self._shell("Not found", "<h1>Page not found</h1>", status=404)
        if (gate := self._login_gate(sess, job)) is not None:
            return self._stamp(gate, sess, new)
        first = self._first_open_page(sess, job)
        return self._stamp(
            RedirectResponse(f"/careers/apply/{job.id}/{first}", status_code=303), sess, new
        )

    def _first_open_page(self, sess: PortalSession, job: MockJob) -> int:
        done = sess.done.get(job.id, set())
        return next((p for p in (1, 2, 3) if p not in done), 4)

    def _errors_for(
        self, job: MockJob, page: int, values: dict[str, list[str]], has_file: dict[str, bool]
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        for f in self._page_fields(job, page):
            if f.kind == "file":
                if f.required and not has_file.get(f.key):
                    errors[f.key] = "This field is required."
                continue
            value = [v for v in values.get(f.key, []) if v.strip()]
            if not value:
                if f.required:
                    errors[f.key] = "This field is required."
                continue
            text = value[0].strip()
            if f.key == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                errors[f.key] = "Please enter a valid email address."
            elif self.validation_quirks and (message := self._strict_error(f.key, text)):
                errors[f.key] = message
        return errors

    @staticmethod
    def _strict_error(key: str, text: str) -> str | None:
        if key == "phone" and not _PHONE_STRICT.fullmatch(text):
            return "Enter your phone number in the format (555) 555-0123."
        if key == "postal_code" and not re.fullmatch(r"\d{5}", text):
            return "ZIP code must be 5 digits."
        if key == "graduation" and not _GRAD_STRICT.fullmatch(text):
            return "Enter the date as MM/YYYY."
        if key == "gpa" and not (_GPA_STRICT.fullmatch(text) and float(text) <= 4.0):
            return "GPA must be a number between 0.00 and 4.00."
        if key in ("first_name", "last_name") and re.search(r"\d", text):
            return "Names may not contain digits."
        return None

    def _render_page(
        self, sess: PortalSession, job: MockJob, page: int, *, errors: dict[str, str] | None = None,
        overrides: dict[str, list[str]] | None = None, note: str = "",
    ) -> HTMLResponse:  # fmt: skip
        stored = sess.values.get(job.id, {}).get(page, {})
        if page == 4:
            return self._review_page(sess, job, errors or {}, note)
        rows = []
        for f in self._page_fields(job, page):
            values = (overrides or stored).get(f.key, [])
            if f.key == "email" and not values and sess.email:
                values = [sess.email]
            rows.append(self._field_html(f, values, (errors or {}).get(f.key)))
        buttons = self._BUTTONS[page].replace("{job}", job.id)
        return self._form_page(job, page, "".join(rows), buttons, errors=bool(errors), note=note)

    def _review_page(
        self, sess: PortalSession, job: MockJob, errors: dict[str, str], note: str
    ) -> HTMLResponse:
        rows = []
        for page in (1, 2, 3):
            stored = sess.values.get(job.id, {}).get(page, {})
            for f in self._page_fields(job, page):
                if f.kind == "file":
                    upload = sess.files.get(job.id, {}).get(f.key)
                    shown = upload.filename if upload else ""
                else:
                    shown = ", ".join(stored.get(f.key, []))
                if shown:
                    rows.append(f"<dt>{_ESC(f.label)}</dt><dd>{_ESC(shown)}</dd>")
        certify = Fld(
            "certify",
            "checkbox",
            "I certify that the information provided is true and complete.",
            "wrap",
            True,
        )
        privacy = Fld(
            "privacy",
            "checkbox",
            "I agree to the processing of my data as described in the privacy notice.",
            "wrap",
        )
        fields_html = (
            f"<dl>{''.join(rows)}</dl>"
            + self._field_html(
                certify,
                sess.values.get(job.id, {}).get(4, {}).get("certify", []),
                errors.get("certify"),
            )
            + self._field_html(
                privacy, sess.values.get(job.id, {}).get(4, {}).get("privacy", []), None
            )
        )
        return self._form_page(
            job, 4, fields_html, self._BUTTONS[4], errors=bool(errors), note=note
        )

    async def _h_page(self, request: Request, job_id: str, page: str) -> Response:
        await self._delay()
        sess, new = self._session(request)
        job = self._open_job(job_id)
        if job is None or page not in ("1", "2", "3", "4"):
            return self._shell("Not found", "<h1>Page not found</h1>", status=404)
        if (gate := self._login_gate(sess, job)) is not None:
            return self._stamp(gate, sess, new)
        number = int(page)
        first = self._first_open_page(sess, job)
        if number > first:
            return self._stamp(
                RedirectResponse(f"/careers/apply/{job.id}/{first}", status_code=303), sess, new
            )
        return self._stamp(self._render_page(sess, job, number), sess, new)

    async def _h_post(self, request: Request, job_id: str, page: str) -> Response:
        await self._delay()
        sess, new = self._session(request)
        job = self._open_job(job_id)
        if job is None or page not in ("1", "2", "3", "4"):
            return self._shell("Not found", "<h1>Page not found</h1>", status=404)
        if (gate := self._login_gate(sess, job)) is not None:
            return self._stamp(gate, sess, new)
        number = int(page)
        first = self._first_open_page(sess, job)
        if number > first:
            return self._stamp(
                RedirectResponse(f"/careers/apply/{job.id}/{first}", status_code=303), sess, new
            )
        form, uploads = await self.read_form(request)
        nav = (form.get("nav") or ["next"])[0]
        return self._stamp(self._handle_post(sess, job, number, nav, form, uploads), sess, new)

    def _collect(
        self, job: MockJob, number: int, form: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        if number == 4:
            keys = ["certify", "privacy"]
        else:
            keys = [f.key for f in self._page_fields(job, number) if f.kind != "file"]
        return {k: [v.strip() for v in form.get(self._name(k), []) if v != ""] for k in keys}

    def _handle_post(
        self,
        sess: PortalSession,
        job: MockJob,
        number: int,
        nav: str,
        form: dict[str, list[str]],
        uploads: list[UploadedFile],
    ) -> Response:
        target = f"/careers/apply/{job.id}"
        values = self._collect(job, number, form)
        raw_files = {
            f.key: self._name(f"file:{f.key}")
            for f in self._page_fields(job, number)
            if f.kind == "file"
        }
        received: dict[str, UploadedFile | None] = {
            key: next((u for u in uploads if u.field == raw), None)
            for key, raw in raw_files.items()
        }
        move = nav.strip().lower()
        if move == "back":
            self._store(sess, job, number, values, received, mark_done=False)
            return RedirectResponse(f"{target}/{max(1, number - 1)}", status_code=303)
        if move == "save":
            self._store(sess, job, number, values, received, mark_done=False)
            self.events.append(f"draft_saved:{number}")
            return self._render_page(sess, job, number, note="Your progress has been saved.")
        # forward navigation: Next / Continue / Submit application / the scripted "Next" link
        stored = sess.files.get(job.id, {})
        has_file = {k: received[k] is not None or k in stored for k in raw_files}
        if number == 4:
            errors = self._review_errors(values)
        else:
            errors = self._errors_for(job, number, values, has_file)
        if errors:
            self.events.append(f"validation_failed:{number}:{len(errors)}")
            if (
                self.validation_quirks
            ):  # a redisplayed page cannot carry the files: they must be attached again
                for key, upload in received.items():
                    if upload is not None:
                        received[key] = None
                        errors[key] = (
                            "Please attach the file again: files are not kept when a page is redisplayed."
                        )
            self._store(sess, job, number, values, received, mark_done=False)
            return self._render_page(sess, job, number, errors=errors, overrides=values)
        self._store(sess, job, number, values, received, mark_done=True)
        self.events.append(f"page_saved:{number}")
        if number < 4:
            return RedirectResponse(f"{target}/{number + 1}", status_code=303)
        return self._finish(sess, job)

    def _review_errors(self, values: dict[str, list[str]]) -> dict[str, str]:
        return (
            {}
            if values.get("certify")
            else {"certify": "You must certify the information to submit."}
        )

    def _store(
        self,
        sess: PortalSession,
        job: MockJob,
        number: int,
        values: dict[str, list[str]],
        received: dict[str, UploadedFile | None],
        *,
        mark_done: bool,
    ) -> None:
        sess.values.setdefault(job.id, {})[number] = values
        for key, upload in received.items():
            if upload is not None:
                sess.files.setdefault(job.id, {})[key] = upload
        if mark_done:
            sess.done.setdefault(job.id, set()).add(number)

    def _finish(self, sess: PortalSession, job: MockJob) -> Response:
        if job.id not in sess.submitted:
            reference = f"APP-{10000 + (self.seed * 7919 + len(self.references) * 104729) % 90000}"
            single_boxes = {"certify", "privacy"} | {
                f.key
                for page in (1, 2, 3)
                for f in self._page_fields(job, page)
                if f.kind == "checkbox"
            }
            fields: dict[str, list[str]] = {}
            for page in (1, 2, 3, 4):
                for key, values in sess.values.get(job.id, {}).get(page, {}).items():
                    if values:
                        fields[key] = ["true"] if key in single_boxes else values
            if sess.email and "email" not in fields:
                fields["email"] = [sess.email]
            files = [
                UploadedFile(
                    field=key, filename=u.filename, content_type=u.content_type, data=u.data
                )
                for key, u in sess.files.get(job.id, {}).items()
            ]
            self.record_submission(
                f"/careers/apply/{job.id}/4", fields, files,
                variant=self.variant, job_id=job.id, reference=reference, account=sess.email,
                raw_names=dict(self.names),
            )  # fmt: skip
            sess.submitted[job.id] = reference
            self.references.append(reference)
            self.events.append(f"submitted:{job.id}")
        if self.variant == "silent":
            return RedirectResponse("/careers", status_code=303)
        return RedirectResponse(f"/careers/apply/{job.id}/thank-you", status_code=303)

    async def _h_thanks(self, request: Request, job_id: str) -> Response:
        await self._delay()
        sess, new = self._session(request)
        reference = sess.submitted.get(job_id)
        if reference is None or self.variant == "silent":
            return self._stamp(
                RedirectResponse(f"/careers/apply/{job_id}", status_code=303), sess, new
            )
        body = (
            "<h1>Thank you for applying!</h1>"
            f"<p>Your application has been received. Your reference number is <strong>{reference}</strong>.</p>"
            '<p><a href="/careers">Back to open positions</a></p>'
        )
        return self._stamp(self._shell("Thank you", body), sess, new)

    # -- accounts (variant "signup") -----------------------------------------------------------------------------------------------
    def _account_page(
        self, mode: str, error: str = "", next_url: str = "/careers", email: str = ""
    ) -> HTMLResponse:
        creating = mode == "create"
        email_field = Fld("acct_email", "email", "Email address", "placeholder", True)
        pw_field = Fld(
            "acct_password",
            "password",
            "Choose a password" if creating else "Password",
            "aria",
            True,
        )
        confirm = Fld("acct_confirm", "password", "Confirm password", "for", True)
        rows = self._field_html(email_field, [email] if email else [], None) + self._field_html(
            pw_field, [], None
        )
        if creating:
            rows += self._field_html(confirm, [], None)
        alert = f'<div class="alert" role="alert">{_ESC(error)}</div>' if error else ""
        title = "Create your account" if creating else "Sign in"
        button = "Create account" if creating else "Sign in"
        other = (
            f'<a class="plain" href="/account/login?next={_ESC(next_url)}">Already registered? Sign in</a>'
            if creating
            else f'<a class="plain" href="/account/create?next={_ESC(next_url)}">New here? Create an account</a>'
        )
        body = (
            f'<h1>{title}</h1>{alert}<form method="post" action="/account/{mode}?next={_ESC(next_url)}" novalidate>'
            f'{rows}<div class="btns"><button type="submit">{button}</button>{other}</div></form>'
        )
        return self._shell(title, body)

    @staticmethod
    def _safe_next(request: Request) -> str:
        target = request.query_params.get("next", "/careers")
        return target if target.startswith("/") and not target.startswith("//") else "/careers"

    async def _h_create_form(self, request: Request) -> Response:
        await self._delay()
        sess, new = self._session(request)
        return self._stamp(
            self._account_page("create", next_url=self._safe_next(request)), sess, new
        )

    async def _h_login_form(self, request: Request) -> Response:
        await self._delay()
        sess, new = self._session(request)
        return self._stamp(
            self._account_page("login", next_url=self._safe_next(request)), sess, new
        )

    async def _account_form(self, request: Request) -> tuple[str, str, str]:
        form, _ = await self.read_form(request)

        def get(key: str) -> str:
            return (form.get(self._name(key)) or [""])[0].strip()

        return get("acct_email").lower(), get("acct_password"), get("acct_confirm")

    async def _h_create(self, request: Request) -> Response:
        await self._delay()
        sess, new = self._session(request)
        target = self._safe_next(request)
        email, password, confirm = await self._account_form(request)

        def again(message: str) -> Response:
            return self._stamp(self._account_page("create", message, target, email), sess, new)

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return again("Please enter a valid email address.")
        if email in self.accounts:
            return again("An account already exists for this email address. Please sign in.")
        if len(password) < 8:
            return again("Your password must be at least 8 characters long.")
        if password != confirm:
            return again("The passwords do not match.")
        self.add_account(email, password)
        sess.email = email
        self.events.append(f"account_created:{email}")
        return self._stamp(RedirectResponse(target, status_code=303), sess, new)

    async def _h_login(self, request: Request) -> Response:
        await self._delay()
        sess, new = self._session(request)
        target = self._safe_next(request)
        email, password, _ = await self._account_form(request)
        if self.accounts.get(email) != password or not password:
            self.events.append(f"login_failed:{email}")
            return self._stamp(
                self._account_page("login", "Incorrect email or password.", target, email),
                sess,
                new,
            )
        sess.email = email
        self.events.append(f"login_ok:{email}")
        return self._stamp(RedirectResponse(target, status_code=303), sess, new)


def make_site(
    company: str = "acme", jobs: list[MockJob] | None = None, **options: Any
) -> EmployerPortalSite:
    """Build ``careers.<company>.com``. See the module docstring for the options."""
    return EmployerPortalSite(company, jobs, **options)
