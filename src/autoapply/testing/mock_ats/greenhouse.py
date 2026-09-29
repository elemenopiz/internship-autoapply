"""Mock Greenhouse job boards: the new React board, the legacy Rails board and the embedded iframe board.

``make_site(company="acme", jobs=None, *, variant="new"|"legacy"|"embed", ...)`` returns a ``GreenhouseSite``.
``company`` is the board token; job ids are numeric strings (default job id ``4100200``).

Hosts: ``variant="new"`` -> ``job-boards.greenhouse.io``; ``"legacy"`` and ``"embed"`` -> ``boards.greenhouse.io``
(the browser reaches ``<host>.localhost:<port>``; use ``site.job_url(id)``).

Options (keyword only): ``variant``, ``name`` (site name, default "greenhouse"), ``company_name``,
``require_captcha`` (VISIBLE challenge that must be solved by a human; ``captcha_provider`` recaptcha|hcaptcha|
turnstile|arkose, ``captcha_placement`` inline|overlay|on_submit: "on_submit" shows the modal challenge only
when the submit button is pressed without a solved token, and completes the submit once a human solves it),
``cookie_consent`` (None|"bar"|"modal": OneTrust style consent UI, see ``blockers.cookie_banner``),
``invisible_recaptcha`` (adds the real
``.grecaptcha-badge``; never blocks), ``render_delay_s`` (form fields are injected by script after the delay,
a "Loading" placeholder is shown meanwhile), ``rerender_on_input`` (new variant: every input is REPLACED by a
fresh node on each keystroke/fill, staling element handles; locators keep working), ``cover_letter``
none|optional|required, ``eeo`` (self-identification section), ``phone_required``, ``max_upload_bytes``,
``security_code`` (new variant: after a valid submit the form asks for an emailed code, delivered to the hub
mailbox as a 6-digit number; the site must be attached to a ``MockHub``).

Pages and selectors guaranteed - variant "new" (``/{token}/jobs/{id}``, single page, inline form)
    h1.section-header (title), ``form#application-form`` (novalidate, submitted with fetch, NO page reload),
    ``#first_name #last_name #preferred_name #email #phone`` (input ids == names; ``#phone`` sits in an
    ``.iti`` intl-tel wrapper), ``#country`` = react-select combobox (see below), resume block
    ``.file-upload[data-field=resume]`` containing buttons "Attach" / "Dropbox" / "Google Drive" / "Enter
    manually", hidden ``input#resume[type=file][name=resume]`` (visually hidden, so wait for state=attached or use
    set_input_files; the "Attach" button opens the file chooser), manual text ``textarea#resume_text``; the same
    for ``#cover_letter`` / ``#cover_letter_text``; custom questions ``#question_<id>`` (text/textarea inputs,
    react-select for select questions, radio ids ``question_<id>_<optionId>``, checkbox groups
    ``name=question_<id>[]``, single checkbox ``#question_<id>``); EEO section "Voluntary Self-Identification"
    with comboboxes ``#gender #hispanic_ethnicity #race #veteran_status #disability_status`` (options such as
    "Decline To Self Identify", "I don't wish to answer", "I do not want to answer"); button
    ``button[type=submit]`` labelled "Submit application"; success state ``#application-confirmation`` with the
    text "Thank you for applying." replacing the form (URL unchanged).
    react-select markup (react-select v5, classNamePrefix ``select``): ``div.select-shell > div.select__control >
    div.select__value-container (div.select__placeholder | div.select__single-value) + div.select__input-container
    > input.select__input[role=combobox][aria-expanded]``; clicking the control opens ``div.select__menu >
    div.select__menu-list[role=listbox] > div.select__option[role=option]``; typing filters (contains, case
    insensitive), ArrowUp/Down move ``select__option--is-focused``, Enter/Tab select, Escape/blur close; the value
    is submitted through ``input[type=hidden][name=<field>]`` holding the option id. NOT a native <select>.
    Validation: empty required fields get ``aria-invalid="true"``, the wrapper gains an error paragraph
    ``p.helper-text.helper-text--error[role=alert]`` ("This field is required") and nothing is sent; server side
    validation answers HTTP 422 ``{"errors": {...}}`` and the same paragraphs are shown. A failed request (e.g.
    503) shows ``div.flash-error[role=alert]`` and keeps the form filled so the click can be repeated.
    Closed jobs redirect to ``/{token}?error=true`` ("The job you are looking for is no longer open.").

variant "legacy" (``/{token}/jobs/{id}``): server rendered page, ``form#application_form`` (POST, multipart,
    novalidate), inputs ``#first_name`` etc. named ``job_application[first_name]`` ..., resume/cover letter file
    inputs ``#resume_fileupload`` / ``#cover_letter_fileupload`` (names ``job_application[resume]``,
    ``job_application[cover_letter]``) with "Attach" / "Enter manually" buttons and ``#resume_text`` /
    ``#cover_letter_text`` (``job_application[resume_text]``); custom questions
    ``job_application[answers_attributes][N][text_value|boolean_value|answer_selected_options_attributes...]``
    (native <select> whose first option is "--", radios, checkboxes) with hidden ``question_id`` inputs; EEO
    ``select#job_application_gender`` etc.; submit ``input#submit_app`` ("Submit Application"). Errors are
    ``label.error`` ("This field is required.") next to the field inside ``div.field.error``; a server-side
    rejection re-renders the page with values kept (files are lost, as in a real browser). Success: 303 to
    ``/{token}/jobs/{id}/confirmation`` showing "Thank you for applying.".

variant "embed": everything of "legacy" plus a company page ``/careers?gh_jid=<id>`` (served on any hostname; use
    ``site.embed_page_url(id)`` for a cross-origin company host) that injects ``iframe#grnhse_iframe`` into
    ``div#grnhse_app`` with ``src=/embed/job_app?for=<token>&token=<id>`` on boards.greenhouse.io, auto-resized
    through postMessage. Inside the iframe the legacy DOM is used; success redirects to
    ``/embed/job_app/confirmation``. Without ``gh_jid`` the iframe shows the job list.

Recording: only a submission that passes server validation is recorded. ``Submission.fields`` holds the raw posted
    names, ``files`` the uploads, and ``meta`` holds variant-independent views: ``job_id``, ``company``, ``variant``,
    ``standard`` (first_name, last_name, preferred_name, email, phone, country), ``answers`` (MockQuestion.key ->
    labels/text), ``eeo`` (key -> option label), ``uploads`` (resume/cover_letter -> filename), ``resume_text``,
    ``cover_letter_text``.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

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
    recaptcha_badge,
)

NEW_HOST = "job-boards.greenhouse.io"
LEGACY_HOST = "boards.greenhouse.io"

Variant = Literal["new", "legacy", "embed"]
CoverLetter = Literal["none", "optional", "required"]

ALLOWED_EXTENSIONS: tuple[str, ...] = ("pdf", "doc", "docx", "txt", "rtf")

LINKEDIN_QUESTION = MockQuestion("linkedin", "LinkedIn Profile", "text", required=False)

COUNTRY_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("AU", "Australia"),
            ("BR", "Brazil"),
            ("CA", "Canada"),
            ("CN", "China"),
            ("FR", "France"),
            ("DE", "Germany"),
            ("IN", "India"),
            ("IE", "Ireland"),
            ("IT", "Italy"),
            ("JP", "Japan"),
            ("MX", "Mexico"),
            ("NL", "Netherlands"),
            ("NZ", "New Zealand"),
            ("PL", "Poland"),
            ("SG", "Singapore"),
            ("KR", "South Korea"),
            ("ES", "Spain"),
            ("SE", "Sweden"),
            ("CH", "Switzerland"),
            ("GB", "United Kingdom"),
            ("US", "United States"),
        ),
        key=lambda pair: pair[1],
    )
)

_EEO_DEFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("gender", "Gender", ("Male", "Female", "Decline To Self Identify")),
    ("hispanic_ethnicity", "Are you Hispanic/Latino?", ("Yes", "No", "Decline To Self Identify")),
    (
        "race",
        "Race",
        (
            "American Indian or Alaskan Native",
            "Asian",
            "Black or African American",
            "Native Hawaiian or Other Pacific Islander",
            "White",
            "Two or More Races",
            "Decline To Self Identify",
        ),
    ),
    (
        "veteran_status",
        "Veteran Status",
        (
            "I am not a protected veteran",
            "I identify as one or more of the classifications of protected veteran",
            "I don't wish to answer",
        ),
    ),
    (
        "disability_status",
        "Disability Status",
        (
            "Yes, I have a disability, or have had one in the past",
            "No, I do not have a disability and have not had one in the past",
            "I do not want to answer",
        ),
    ),
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def default_jobs() -> list[MockJob]:
    """The single open job served when ``jobs`` is not given."""
    return [
        MockJob(
            id="4100200",
            title="Product Management Intern, Summer 2027",
            location="Austin, TX",
            description=(
                "Summer 2027 internship. Work with product, engineering and operations teams.\n\n"
                "You will own a scoped project, present to leadership and ship something real."
            ),
            questions=(
                LINKEDIN_QUESTION,
                STANDARD_QUESTIONS["work_auth"],
                STANDARD_QUESTIONS["sponsorship"],
                STANDARD_QUESTIONS["referral"],
                STANDARD_QUESTIONS["why_role"],
            ),
        )
    ]


def _num_id(*parts: str) -> str:
    """Deterministic 10-digit id in the style of Greenhouse question / option ids."""
    return str(4_000_000_000 + zlib.crc32("|".join(parts).encode()) % 900_000_000)


# ------------------------------------------------------------------------------------ form model


@dataclass(frozen=True)
class _Field:
    """One form field, independent of the variant that renders it."""

    key: (
        str  # main: "first_name"; custom: "q:<MockQuestion.key>"; eeo: "eeo:<key>"; files: "resume"
    )
    label: str
    kind: str  # text|email|tel|textarea|select|radio|checkbox|multiselect|file
    required: bool = False
    options: tuple[tuple[str, str], ...] = ()  # (submitted value, visible label)
    max_length: int | None = None
    group: str = "main"  # main | files | custom | eeo
    qid: str = ""
    index: int = 0  # position among custom questions (legacy names)

    def dom_id(self, legacy: bool) -> str:
        if self.group == "custom":
            if not legacy:
                return f"question_{self.qid}"
            n = self.index
            if self.kind in {"text", "textarea"}:
                return f"job_application_answers_attributes_{n}_text_value"
            if self.kind == "checkbox":
                return f"job_application_answers_attributes_{n}_boolean_value"
            return (
                f"job_application_answers_attributes_{n}_answer_selected_options_attributes_{n}"
                "_question_option_id"
            )
        bare = self.key.split(":")[-1]
        if self.group == "files":
            return f"{bare}_fileupload" if legacy else bare
        if self.group == "eeo":
            return f"job_application_{bare}" if legacy else bare
        return bare

    def dom_name(self, legacy: bool) -> str:
        if self.group == "custom":
            if not legacy:
                return (
                    f"question_{self.qid}[]"
                    if self.kind == "multiselect"
                    else f"question_{self.qid}"
                )
            base = f"job_application[answers_attributes][{self.index}]"
            if self.kind in {"text", "textarea"}:
                return f"{base}[text_value]"
            if self.kind == "checkbox":
                return f"{base}[boolean_value]"
            if self.kind == "multiselect":
                return f"{base}[answer_selected_options_attributes][][question_option_id]"
            return f"{base}[answer_selected_options_attributes][{self.index}][question_option_id]"
        bare = self.key.split(":")[-1]
        return f"job_application[{bare}]" if legacy else bare

    def label_for(self, value: str) -> str:
        return next((label for val, label in self.options if val == value), value)


def _custom_field(job: MockJob, question: MockQuestion, index: int) -> _Field:
    qid = _num_id(job.id, question.key)
    options = tuple(
        (_num_id(job.id, question.key, str(i)), label) for i, label in enumerate(question.options)
    )
    return _Field(
        key=f"q:{question.key}",
        label=question.label,
        kind=question.kind,
        required=question.required,
        options=options,
        max_length=question.max_length,
        group="custom",
        qid=qid,
        index=index,
    )


def _eeo_fields(job: MockJob) -> list[_Field]:
    return [
        _Field(
            key=f"eeo:{key}",
            label=label,
            kind="select",
            options=tuple((_num_id(job.id, "eeo", key, str(i)), o) for i, o in enumerate(options)),
            group="eeo",
        )
        for key, label, options in _EEO_DEFS
    ]


# ------------------------------------------------------------------------------------ site


class GreenhouseSite(MockSite):
    """Mock Greenhouse board (see the module docstring for the selector contract)."""

    def __init__(
        self,
        company: str,
        jobs: Sequence[MockJob],
        *,
        variant: Variant,
        name: str,
        company_name: str,
        require_captcha: bool,
        captcha_provider: Provider,
        captcha_placement: Placement,
        invisible_recaptcha: bool,
        render_delay_s: float,
        rerender_on_input: bool,
        cover_letter: CoverLetter,
        eeo: bool,
        phone_required: bool,
        max_upload_bytes: int,
        security_code: bool,
        cookie_consent: CookieBanner | None,
    ) -> None:
        super().__init__(name, NEW_HOST if variant == "new" else LEGACY_HOST)
        self.company = company
        self.company_name = company_name
        self.variant: Variant = variant
        self.require_captcha = require_captcha
        self.captcha_provider: Provider = captcha_provider
        self.captcha_placement: Placement = captcha_placement
        self.invisible_recaptcha = invisible_recaptcha
        self.render_delay_s = render_delay_s
        self.rerender_on_input = rerender_on_input
        self.cover_letter = cover_letter
        self.eeo = eeo
        self.phone_required = phone_required
        self.max_upload_bytes = max_upload_bytes
        self.security_code = security_code
        self.cookie_consent = cookie_consent
        for job in jobs:
            self.jobs[job.id] = job
        self.state["security_codes"] = {}
        install_captcha_routes(self)
        self._install_routes()

    # ---- addressing helpers for tests / adapters ----------------------------------------------
    @property
    def legacy(self) -> bool:
        return self.variant != "new"

    def job_url(self, job_id: str) -> str:
        return self.url(f"/{self.company}/jobs/{job_id}")

    def board_url(self) -> str:
        return self.url(f"/{self.company}")

    def embed_page_url(self, job_id: str | None = None, host: str | None = None) -> str:
        """Company page embedding the board, on a DIFFERENT (cross-origin) host by default."""
        company_host = host or f"careers.{self.company}.example"
        query = f"?gh_jid={job_id}" if job_id else ""
        return f"http://{company_host}.localhost:{self._require_port()}/careers{query}"

    def embed_frame_url(self, job_id: str) -> str:
        return self.url(f"/embed/job_app?for={self.company}&token={job_id}")

    def specs_for(self, job: MockJob) -> list[_Field]:
        specs: list[_Field] = [
            _Field("first_name", "First Name", "text", True),
            _Field("last_name", "Last Name", "text", True),
            _Field("preferred_name", "Preferred First Name", "text"),
            _Field("email", "Email", "email", True),
            _Field("phone", "Phone", "tel", self.phone_required),
        ]
        if not self.legacy:
            specs.append(_Field("country", "Country", "select", True, options=COUNTRY_OPTIONS))
        specs.append(_Field("resume", "Resume/CV", "file", True, group="files"))
        if self.cover_letter != "none":
            specs.append(
                _Field(
                    "cover_letter",
                    "Cover Letter",
                    "file",
                    self.cover_letter == "required",
                    group="files",
                )
            )
        specs += [_custom_field(job, q, i) for i, q in enumerate(job.questions)]
        if self.eeo:
            specs += _eeo_fields(job)
        return specs

    def field_id(self, job_id: str, question_key: str) -> str:
        """DOM id of the input for ``MockQuestion.key`` on this variant."""
        job = self.jobs[job_id]
        for spec in self.specs_for(job):
            if spec.key == f"q:{question_key}":
                return spec.dom_id(self.legacy)
        raise KeyError(question_key)

    def field_name(self, job_id: str, question_key: str) -> str:
        """Submitted input name of the custom question ``question_key`` on this variant."""
        for spec in self.specs_for(self.jobs[job_id]):
            if spec.key == f"q:{question_key}":
                return spec.dom_name(self.legacy)
        raise KeyError(question_key)

    def option_value(self, job_id: str, question_key: str, label: str) -> str:
        """The submitted value (option id) of ``label`` for a choice question."""
        for spec in self.specs_for(self.jobs[job_id]):
            if spec.key == f"q:{question_key}":
                for value, option_label in spec.options:
                    if option_label == label:
                        return value
        raise KeyError((question_key, label))

    # ---- routes ----------------------------------------------------------------------------------
    def _install_routes(self) -> None:
        app = self.app
        token = self.company

        @app.get("/careers")
        def careers_page(request: Request) -> Response:
            if self.variant != "embed":
                return self._not_found()
            return self._embed_parent(request)

        @app.get("/embed/job_board")
        def embed_board(request: Request) -> Response:
            if self.variant != "embed":
                return self._not_found()
            return self._board_page(request, embedded=True)

        @app.get("/embed/job_app")
        def embed_job_app(request: Request) -> Response:
            job = self._embedded_job(request)
            if job is None:
                return self._not_found()
            if job.closed:
                return self._closed_redirect()
            return self._render_legacy(request, job, embedded=True)

        @app.post("/embed/job_app")
        async def embed_submit(request: Request) -> Response:
            job = self._embedded_job(request)
            if job is None or job.closed:
                return self._not_found()
            return await self._submit(request, job, embedded=True)

        @app.get("/embed/job_app/confirmation")
        def embed_confirmation() -> Response:
            if self.variant != "embed":
                return self._not_found()
            return self._confirmation_page(embedded=True)

        @app.get("/{board}")
        def board_page(board: str, request: Request) -> Response:
            if board != token:
                return self._not_found()
            return self._board_page(request, embedded=False)

        @app.get("/{board}/jobs/{job_id}")
        def job_page(board: str, job_id: str, request: Request) -> Response:
            job = self._hosted_job(board, job_id)
            if job is None:
                return self._not_found()
            if job.closed:
                return self._closed_redirect()
            if self.legacy:
                return self._render_legacy(request, job, embedded=False)
            return self._render_new(request, job)

        @app.post("/{board}/jobs/{job_id}")
        async def submit(board: str, job_id: str, request: Request) -> Response:
            job = self._hosted_job(board, job_id)
            if job is None or job.closed:
                return self._not_found()
            return await self._submit(request, job, embedded=False)

        @app.get("/{board}/jobs/{job_id}/confirmation")
        def confirmation(board: str, job_id: str) -> Response:
            if self._hosted_job(board, job_id) is None:
                return self._not_found()
            return self._confirmation_page(embedded=False)

    def _hosted_job(self, board: str, job_id: str) -> MockJob | None:
        return self.jobs.get(job_id) if board == self.company else None

    def _embedded_job(self, request: Request) -> MockJob | None:
        if self.variant != "embed" or request.query_params.get("for") != self.company:
            return None
        return self.jobs.get(request.query_params.get("token", ""))

    def _not_found(self) -> Response:
        return html_response(
            "Job not found",
            "<div id='main'><h1>Sorry, but we can't find that page.</h1></div>",
            status=404,
        )

    def _closed_redirect(self) -> Response:
        return RedirectResponse(f"/{self.company}?error=true", status_code=302)

    # ---- board / listing --------------------------------------------------------------------------
    def _board_page(self, request: Request, *, embedded: bool) -> Response:
        error = request.query_params.get("error") == "true"
        banner = (
            "<div class='flash-error' id='flash_error' role='alert'>The job you are looking for is no "
            "longer open.</div>"
            if error
            else ""
        )
        open_jobs = [job for job in self.jobs.values() if not job.closed]
        if self.legacy:
            target = " target='_top'" if embedded else ""
            rows = "".join(
                f"<div class='opening' department_id='1' office_id='1'>"
                f"<a data-mapped='true' href='{self._board_link(job, embedded)}'{target}>{esc(job.title)}</a>"
                f"<span class='location'>{esc(job.location)}</span></div>"
                for job in open_jobs
            )
            body = (
                f"<div id='wrapper'><div id='main'>{banner}<h1>Current openings at "
                f"{esc(self.company_name)}</h1><section class='level-0'>{rows}</section></div></div>"
            )
            return html_response(f"Jobs at {self.company_name}", body, _LEGACY_CSS)
        rows = "".join(
            f"<tr class='job-post'><td class='cell'><a href='/{esc(self.company)}/jobs/{esc(job.id)}'>"
            f"<p class='body body--medium'>{esc(job.title)}</p>"
            f"<p class='body body__secondary body--metadata'>{esc(job.location)}</p></a></td></tr>"
            for job in open_jobs
        )
        body = (
            "<div class='page'><main class='container'>"
            f"{banner}<h1 class='section-header section-header--large'>Current openings at "
            f"{esc(self.company_name)}</h1><div class='job-posts'><table class='job-posts--table'>"
            f"<tbody>{rows}</tbody></table></div></main></div>"
        )
        return html_response(f"Jobs at {self.company_name}", body, _NEW_CSS)

    def _board_link(self, job: MockJob, embedded: bool) -> str:
        if embedded:
            return f"/careers?gh_jid={job.id}"
        return f"/{self.company}/jobs/{job.id}"

    # ---- embed parent page --------------------------------------------------------------------------
    def _embed_parent(self, request: Request) -> Response:
        origin = Origin.of(request)
        frame_base = origin.on(LEGACY_HOST)
        job_id = request.query_params.get("gh_jid")
        src = (
            f"{frame_base}/embed/job_app?for={self.company}&token={job_id}"
            if job_id
            else f"{frame_base}/embed/job_board?for={self.company}"
        )
        cfg = json_for_script({"src": src, "delay": self.render_delay_s})
        body = (
            f"<header class='site'><a href='/careers'>{esc(self.company_name)}</a> "
            "<span>Careers</span></header>"
            f"<main><h1>Join {esc(self.company_name)}</h1>"
            "<p>We are hiring interns for Summer 2027.</p>"
            "<div id='grnhse_app'></div></main>"
            "<script>(function(){var cfg=" + cfg + ";function load(){"
            "var f=document.createElement('iframe');f.id='grnhse_iframe';f.name='grnhse_iframe';"
            "f.src=cfg.src;f.setAttribute('frameborder','0');f.setAttribute('scrolling','no');"
            "f.style.cssText='width:100%;height:700px;border:0';"
            "f.title='Greenhouse Job Board';"
            "document.getElementById('grnhse_app').appendChild(f);"
            "window.addEventListener('message',function(e){var d=e.data;"
            "if(d&&d.type==='grnhse-resize'&&e.source===f.contentWindow){"
            "f.style.height=(d.height+8)+'px';}});}"
            "if(cfg.delay>0){setTimeout(load,cfg.delay*1000);}else{load();}})();</script>"
        )
        head = (
            "<style>body{margin:0;font-family:Georgia,serif;background:#fbfaf7}"
            "header.site{background:#243b53;color:#fff;padding:16px 28px}"
            "main{max-width:900px;margin:0 auto;padding:24px}</style>"
        )
        return html_response(f"Careers at {self.company_name}", body, head)

    # ---- new board page -------------------------------------------------------------------------------
    def _render_new(self, request: Request, job: MockJob) -> Response:
        origin = Origin.of(request)
        form_html = self._new_form(job, origin)
        if self.render_delay_s > 0:
            mount = (
                "<div id='form-mount'><div class='loading' role='status'>Loading application form...</div>"
                f"</div><template id='deferred-form'>{form_html}</template>"
            )
        else:
            mount = f"<div id='form-mount'>{form_html}</div>"
        description = _paragraphs(job.description)
        extras = self._page_extras(origin)
        body = (
            "<div class='page'>"
            f"<nav class='board-nav'><a class='logo' href='/{esc(self.company)}'>{esc(self.company_name)}</a>"
            f"<a href='/{esc(self.company)}'>Current openings</a></nav>"
            "<main class='container' id='main'>"
            "<div class='job__header'>"
            f"<h1 class='section-header section-header--large font-primary'>{esc(job.title)}</h1>"
            f"<div class='job__location'><div>{esc(job.location)}</div></div>"
            "<a class='btn btn--pill' href='#application-form' id='apply-top'>Apply</a></div>"
            f"<div class='job__description body'>{description}</div>"
            "<div class='job__application' id='application-container'>"
            "<h2 class='section-header section-header--large font-primary'>Apply for this job</h2>"
            "<p class='required-note'>* indicates a required field</p>"
            f"{mount}</div></main></div>{extras}"
            f"<script id='gh-config' type='application/json'>{self._config_json(job, embedded=False)}</script>"
            f"<script>{_GH_JS}\n{CAPTCHA_LISTENER_JS}</script>"
        )
        return html_response(
            f"Job Application for {job.title} at {self.company_name}", body, _NEW_CSS
        )

    def _page_extras(self, origin: Origin) -> str:
        """Overlay / challenge template, invisible badge and cookie banner appended to the page body."""
        extras = ""
        if self.require_captcha and self.captcha_placement == "overlay":
            extras += captcha_overlay(self.captcha_provider, origin)
        elif self.require_captcha and self.captcha_placement == "on_submit":
            extras += captcha_overlay_template(self.captcha_provider, origin)
        if self.invisible_recaptcha:
            extras += recaptcha_badge(origin)
        return extras + cookie_banner(self.cookie_consent)

    def _config_json(self, job: MockJob, *, embedded: bool) -> str:
        action = (
            f"/embed/job_app?for={self.company}&token={job.id}"
            if embedded
            else f"/{self.company}/jobs/{job.id}"
        )
        return json_for_script(
            {
                "variant": "legacy" if self.legacy else "new",
                "action": action,
                "delay": self.render_delay_s,
                "rerender": self.rerender_on_input,
                "invisible": self.invisible_recaptcha,
                "captchaGate": self.require_captcha and self.captcha_placement == "on_submit",
                "captchaField": captcha_response_field(self.captcha_provider),
                "exts": list(ALLOWED_EXTENSIONS),
                "maxBytes": self.max_upload_bytes,
                "embedded": embedded,
            }
        )

    def _new_form(self, job: MockJob, origin: Origin) -> str:
        parts: list[str] = []
        specs = self.specs_for(job)
        main = [s for s in specs if s.group in {"main", "files", "custom"}]
        eeo = [s for s in specs if s.group == "eeo"]
        parts += [_new_field(s) for s in main]
        if eeo:
            parts.append(
                "<div class='eeoc__container' id='eeoc'>"
                "<h3 class='section-header'>Voluntary Self-Identification</h3>"
                "<p class='eeoc__intro'>For government reporting purposes, we ask candidates to respond to "
                "the below self-identification survey. Completion of the form is entirely voluntary. "
                "Whatever your decision, it will not be considered in the hiring process.</p>"
                + "".join(_new_field(s) for s in eeo)
                + "</div>"
            )
        parts.append(self._captcha_block(origin))
        parts.append(
            "<div class='security-code-slot' id='security-code-slot'></div>"
            "<div class='form-actions'><button type='submit' class='btn btn--pill' id='submit-application'>"
            "Submit application</button></div>"
        )
        return (
            f"<form id='application-form' class='application--form' method='post' "
            f"enctype='multipart/form-data' action='/{esc(self.company)}/jobs/{esc(job.id)}' novalidate>"
            f"{''.join(parts)}</form>"
        )

    def _captcha_block(self, origin: Origin) -> str:
        if not self.require_captcha:
            return ""
        if self.captcha_placement in {"overlay", "on_submit"}:
            field = captcha_response_field(self.captcha_provider)
            return f"<input type='hidden' name='{field}' value=''>"
        return (
            "<div class='captcha-block' data-field='captcha'>"
            f"{captcha_widget(self.captcha_provider, origin)}</div>"
        )

    # ---- legacy / embedded page -------------------------------------------------------------------------
    def _render_legacy(
        self,
        request: Request,
        job: MockJob,
        *,
        embedded: bool,
        values: dict[str, list[str]] | None = None,
        errors: dict[str, str] | None = None,
    ) -> Response:
        origin = Origin.of(request)
        values = values or {}
        errors = errors or {}
        specs = self.specs_for(job)
        main = "".join(
            _legacy_field(s, values, errors) for s in specs if s.group in {"main", "files"}
        )
        custom_specs = [s for s in specs if s.group == "custom"]
        custom = "".join(_legacy_field(s, values, errors, job) for s in custom_specs)
        eeo_specs = [s for s in specs if s.group == "eeo"]
        eeo = ""
        if eeo_specs:
            eeo = (
                "<div id='eeoc_fields'><h4>Voluntary Self-Identification</h4>"
                "<p>For government reporting purposes, we ask candidates to respond to the below "
                "self-identification survey. Completion of the form is entirely voluntary.</p>"
                + "".join(_legacy_field(s, values, errors) for s in eeo_specs)
                + "</div>"
            )
        action = (
            f"/embed/job_app?for={self.company}&token={job.id}"
            if embedded
            else f"/{self.company}/jobs/{job.id}"
        )
        captcha = ""
        if self.require_captcha:
            captcha = self._captcha_block(origin)
            if "captcha" in errors:
                captcha += (
                    f"<label class='error' id='captcha-error'>{esc(errors['captcha'])}</label>"
                )
        code_field = ""
        if self.security_code and "security_code" in errors:
            code_field = (
                "<div class='field error' data-field='security_code'>"
                "<label for='security_code'>Security code</label>"
                "<input id='security_code' name='security_code' type='text' autocomplete='one-time-code'>"
                f"<label class='error' for='security_code'>{esc(errors['security_code'])}</label></div>"
            )
        form = (
            f"<form id='application_form' action='{esc(action)}' method='post' "
            "enctype='multipart/form-data' accept-charset='UTF-8' novalidate>"
            "<input type='hidden' name='utf8' value='&#x2713;'>"
            f"<input type='hidden' name='mapped_url_token' value='{esc(self.company)}-{esc(job.id)}'>"
            f"<div id='main_fields'>{main}</div><div id='custom_fields'>{custom}</div>{eeo}{captcha}"
            f"{code_field}"
            "<div id='submit_buttons'><input type='submit' id='submit_app' class='button' "
            "value='Submit Application'></div></form>"
        )
        if self.render_delay_s > 0:
            mount = (
                "<div id='form-mount'><div class='loading' role='status'>Loading application...</div>"
                f"</div><template id='deferred-form'>{form}</template>"
            )
        else:
            mount = f"<div id='form-mount'>{form}</div>"
        extras = self._page_extras(origin)
        header = (
            "" if embedded else f"<span class='company-name'>at {esc(self.company_name)}</span>"
        )
        body = (
            "<div id='wrapper'><div id='main'>"
            f"<div id='header'>{header}<h1 class='app-title'>{esc(job.title)}</h1>"
            f"<div class='location'>{esc(job.location)}</div></div>"
            f"<div id='content'>{_paragraphs(job.description)}</div>"
            "<div id='application'><h2 id='application_title'>Apply for this Job</h2>"
            "<p class='required-note'><span class='asterisk'>*</span> Required</p>"
            f"{mount}</div></div></div>{extras}"
            f"<script id='gh-config' type='application/json'>{self._config_json(job, embedded=embedded)}"
            f"</script><script>{_GH_JS}\n{CAPTCHA_LISTENER_JS}</script>"
        )
        return html_response(
            f"Job Application for {job.title} at {self.company_name}",
            body,
            _LEGACY_CSS,
            body_attrs="class='embedded'" if embedded else "",
        )

    def _confirmation_page(self, *, embedded: bool) -> Response:
        if self.legacy:
            body = (
                "<div id='wrapper'><div id='main'><div id='application_confirmation' "
                "class='confirmation' role='status'><h1>Thank you for applying.</h1>"
                "<p>Your application has been submitted.</p></div></div></div>"
            )
            if embedded:
                body += (
                    "<script>parent.postMessage({type:'grnhse-resize',height:"
                    "document.documentElement.scrollHeight},'*');</script>"
                )
            return html_response("Confirmation", body, _LEGACY_CSS)
        return html_response(
            "Confirmation",
            "<div id='application-confirmation'><h2>Thank you for applying.</h2></div>",
            _NEW_CSS,
        )

    # ---- submission --------------------------------------------------------------------------------------
    async def _submit(self, request: Request, job: MockJob, *, embedded: bool) -> Response:
        fields, files = await self.read_form(request)
        specs = self.specs_for(job)
        errors = self._validate(specs, fields, files)
        if errors:
            if self.legacy:
                return self._render_legacy(
                    request, job, embedded=embedded, values=fields, errors=errors
                )
            return JSONResponse({"ok": False, "errors": errors}, status_code=422)
        if self.security_code:
            challenge = self._security_code_step(job, fields)
            if challenge is not None:
                if self.legacy:
                    return self._render_legacy(
                        request, job, embedded=embedded, values=fields, errors=challenge
                    )
                return JSONResponse({"ok": False, "needs_code": True, "errors": challenge})
        path = request.url.path
        self.record_submission(path, fields, files, **self._meta(job, specs, fields, files))
        if self.legacy:
            target = (
                f"/embed/job_app/confirmation?for={self.company}&token={job.id}"
                if embedded
                else f"/{self.company}/jobs/{job.id}/confirmation"
            )
            return RedirectResponse(target, status_code=303)
        return JSONResponse({"ok": True})

    def _security_code_step(
        self, job: MockJob, fields: dict[str, list[str]]
    ) -> dict[str, str] | None:
        """None when the emailed code was supplied correctly; otherwise the (re)issued challenge."""
        email = (fields.get("job_application[email]" if self.legacy else "email") or [""])[
            0
        ].strip()
        issued: dict[str, str] = self.state["security_codes"]
        supplied = (fields.get("security_code") or [""])[0].strip()
        if email in issued and supplied:
            if supplied == issued[email]:
                return None
            return {"security_code": "Incorrect security code."}
        code = f"{zlib.crc32(f'{email}|{job.id}'.encode()) % 900_000 + 100_000}"
        issued[email] = code
        self.mailbox.deliver(
            email,
            f"Security code for your application to {self.company_name}",
            "Copy and paste this code into the security code field on your application: "
            f"{code}\n\nAfter you enter the code, resubmit your application.",
            sender="no-reply@us.greenhouse-mail.io",
        )
        return {"security_code": "Enter the security code we emailed you to finish applying."}

    def _values(self, spec: _Field, fields: dict[str, list[str]]) -> list[str]:
        raw = [v for v in fields.get(spec.dom_name(self.legacy), []) if v.strip() != ""]
        if spec.kind == "checkbox" and self.legacy:
            return ["1"] if "1" in raw else []
        return raw

    def _validate(
        self, specs: list[_Field], fields: dict[str, list[str]], files: list[UploadedFile]
    ) -> dict[str, str]:
        required_msg = "This field is required." if self.legacy else "This field is required"
        errors: dict[str, str] = {}
        for spec in specs:
            message = (
                self._validate_file(spec, fields, files, required_msg)
                if spec.kind == "file"
                else self._validate_value(spec, self._values(spec, fields), required_msg)
            )
            if message:
                errors[spec.key] = message
        if self.require_captcha:
            token = (fields.get(captcha_response_field(self.captcha_provider)) or [""])[0]
            if not captcha_token_ok(self, token):
                errors["captcha"] = "Please complete the captcha challenge."
        return errors

    @staticmethod
    def _validate_value(spec: _Field, values: list[str], required_msg: str) -> str | None:
        if not values:
            return required_msg if spec.required else None
        if spec.kind == "email" and not _EMAIL_RE.match(values[0].strip()):
            return "Please enter a valid email address."
        if spec.max_length is not None and len(values[0]) > spec.max_length:
            return f"Answer is too long (maximum is {spec.max_length} characters)."
        if spec.options and spec.kind != "checkbox":
            allowed = {value for value, _ in spec.options}
            if any(v not in allowed for v in values):
                return "Invalid selection."
        return None

    def _validate_file(
        self,
        spec: _Field,
        fields: dict[str, list[str]],
        files: list[UploadedFile],
        required_msg: str,
    ) -> str | None:
        upload = next((f for f in files if f.field == spec.dom_name(self.legacy)), None)
        text_name = f"{spec.key}_text"
        text_name = f"job_application[{text_name}]" if self.legacy else text_name
        text = "".join(fields.get(text_name, [])).strip()
        if upload is None:
            return None if text or not spec.required else required_msg
        extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            return f"Unsupported file type. Accepted file types: {', '.join(ALLOWED_EXTENSIONS)}"
        if len(upload.data) > self.max_upload_bytes:
            return "File is too large."
        if not upload.data:
            return "File is empty."
        return None

    def _meta(
        self,
        job: MockJob,
        specs: list[_Field],
        fields: dict[str, list[str]],
        files: list[UploadedFile],
    ) -> dict[str, Any]:
        standard: dict[str, str] = {}
        answers: dict[str, list[str]] = {}
        eeo: dict[str, str] = {}
        for spec in specs:
            if spec.kind == "file":
                continue
            values = self._values(spec, fields)
            if spec.group == "main":
                labels = [spec.label_for(v) for v in values]
                standard[spec.key] = labels[0] if labels else ""
            elif spec.group == "eeo":
                eeo[spec.key.split(":", 1)[1]] = spec.label_for(values[0]) if values else ""
            else:
                if spec.kind == "checkbox":
                    answers[spec.key.split(":", 1)[1]] = ["checked"] if values else []
                else:
                    answers[spec.key.split(":", 1)[1]] = [spec.label_for(v) for v in values]
        uploaded: dict[str, str] = {}
        for spec in specs:
            if spec.kind != "file":
                continue
            match = next((f for f in files if f.field == spec.dom_name(self.legacy)), None)
            if match:
                uploaded[spec.key] = match.filename
        text_prefix = "job_application[{}]" if self.legacy else "{}"
        return {
            "job_id": job.id,
            "company": self.company,
            "variant": self.variant,
            "standard": standard,
            "answers": answers,
            "eeo": eeo,
            "uploads": uploaded,
            "resume_text": "".join(fields.get(text_prefix.format("resume_text"), [])),
            "cover_letter_text": "".join(fields.get(text_prefix.format("cover_letter_text"), [])),
        }


# ------------------------------------------------------------------------------------ rendering


def _paragraphs(text: str) -> str:
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return "".join(f"<p>{esc(c)}</p>" for c in chunks)


def _star(required: bool, legacy: bool = False) -> str:
    if not required:
        return ""
    return "<span class='asterisk'>*</span>" if legacy else "<span class='required'>*</span>"


def _attrs(spec: _Field, legacy: bool) -> str:
    parts = []
    if spec.required:
        parts.append("required aria-required='true'")
    if spec.max_length is not None:
        parts.append(f"maxlength='{spec.max_length}'")
    if not legacy:
        parts.append("aria-invalid='false'")
    return " ".join(parts)


_AUTOCOMPLETE = {
    "first_name": "given-name",
    "last_name": "family-name",
    "email": "email",
    "phone": "tel",
}


def _wrapper_attrs(spec: _Field, kind: str) -> str:
    return (
        f"data-field='{esc(spec.key)}' data-kind='{kind}' "
        f"data-required='{'true' if spec.required else 'false'}'"
    )


def _select_widget(spec: _Field, field_id: str, name: str) -> str:
    options = json_for_script([{"value": v, "label": label} for v, label in spec.options])
    aria = " aria-required='true' aria-invalid='false'" if spec.required else ""
    return (
        f"<div class='select-shell' data-options='{esc(options)}' data-placeholder='Select...'>"
        "<div class='select__control'><div class='select__value-container'>"
        f"<div class='select__placeholder' id='react-select-{field_id}-placeholder'>Select...</div>"
        "<div class='select__input-container' data-value=''>"
        f"<input class='select__input' id='{field_id}' type='text' role='combobox' "
        "aria-expanded='false' aria-haspopup='true' aria-autocomplete='list' "
        f"aria-labelledby='{field_id}-label' autocapitalize='none' autocomplete='off' "
        f"autocorrect='off' spellcheck='false' tabindex='0' value=''{aria}></div></div>"
        "<div class='select__indicators'><span class='select__indicator-separator'></span>"
        "<div class='select__indicator select__dropdown-indicator' aria-hidden='true'>"
        "<svg height='20' width='20' viewBox='0 0 20 20' aria-hidden='true' focusable='false'>"
        "<path d='M4.516 7.548c0.436-0.446 1.043-0.481 1.576 0l3.908 3.747 3.908-3.747c0.533-0.481 "
        "1.141-0.446 1.574 0 0.436 0.445 0.408 1.197 0 1.615-0.406 0.418-4.695 4.502-4.695 4.502"
        "-0.217 0.223-0.502 0.335-0.787 0.335s-0.57-0.112-0.789-0.335c0 0-4.287-4.084-4.695-4.502"
        "s-0.436-1.17 0-1.615z'></path></svg></div></div></div>"
        f"<input name='{esc(name)}' type='hidden' value=''></div>"
    )


def _file_block(spec: _Field, legacy: bool) -> str:
    fid = spec.dom_id(legacy)
    name = spec.dom_name(legacy)
    bare = spec.key
    text_id = f"{bare}_text"
    text_name = f"job_application[{text_id}]" if legacy else text_id
    return (
        f"<div class='file-upload{' field' if legacy else ''}' {_wrapper_attrs(spec, 'file')} role='group' "
        f"aria-labelledby='upload-label-{bare}'>"
        f"<div class='upload-label'><label id='upload-label-{bare}' class='label'>{esc(spec.label)}"
        f"{_star(spec.required, legacy)}</label></div>"
        "<div class='file-upload__wrapper'>"
        "<div class='file-upload__actions'>"
        "<button type='button' class='btn btn--pill btn--secondary' data-source='attach'>Attach</button>"
        "<button type='button' class='btn btn--pill btn--secondary' data-source='dropbox'>Dropbox</button>"
        "<button type='button' class='btn btn--pill btn--secondary' data-source='google-drive'>"
        "Google Drive</button>"
        "<button type='button' class='btn btn--pill btn--secondary' data-source='paste'>"
        "Enter manually</button></div>"
        "<div class='file-upload__filename' hidden><span class='filename'></span> "
        "<button type='button' class='btn-link' data-action='remove' aria-label='Remove attachment'>"
        "Remove</button></div>"
        f"<div class='file-upload__manual' hidden><textarea id='{text_id}' name='{esc(text_name)}' "
        f"class='input input__multi-line' rows='6' aria-label='{esc(spec.label)} text'></textarea></div>"
        f"<p class='helper-text'>Accepted file types: {', '.join(ALLOWED_EXTENSIONS)}</p>"
        f"<input type='file' id='{fid}' name='{esc(name)}' class='visually-hidden' "
        f"accept='{','.join('.' + e for e in ALLOWED_EXTENSIONS)}' tabindex='-1'>"
        "</div></div>"
    )


def _new_field(spec: _Field) -> str:
    fid = spec.dom_id(False)
    name = spec.dom_name(False)
    label = f"{esc(spec.label)}{_star(spec.required)}"
    if spec.kind == "file":
        return _file_block(spec, False)
    if spec.kind in {"text", "email", "tel"}:
        input_type = {"text": "text", "email": "text", "tel": "tel"}[spec.kind]
        control = (
            f"<input type='{input_type}' id='{fid}' name='{esc(name)}' "
            f"class='input input__single-line' {_attrs(spec, False)} "
            f"autocomplete='{_AUTOCOMPLETE.get(fid, 'off')}' value=''>"
        )
        if spec.kind == "tel":
            control = (
                "<div class='iti iti--allow-dropdown iti--show-flags'><div class='iti__country-container'>"
                "<button type='button' class='iti__selected-country' aria-label='Selected country: "
                "United States +1' title='United States: +1'><div class='iti__selected-country-primary'>"
                "<div class='iti__flag iti__us'></div><div class='iti__arrow'></div></div>"
                "<div class='iti__selected-dial-code'>+1</div></button></div>" + control + "</div>"
            )
        return (
            f"<div class='text-input-wrapper' {_wrapper_attrs(spec, spec.kind)}>"
            f"<label id='{fid}-label' for='{fid}' class='label'>{label}</label>"
            f"<div class='input-wrapper'>{control}</div></div>"
        )
    if spec.kind == "textarea":
        return (
            f"<div class='text-input-wrapper' {_wrapper_attrs(spec, 'textarea')}>"
            f"<label id='{fid}-label' for='{fid}' class='label'>{label}</label>"
            f"<div class='input-wrapper'><textarea id='{fid}' name='{esc(name)}' "
            f"class='input input__multi-line' rows='4' {_attrs(spec, False)}></textarea></div></div>"
        )
    if spec.kind == "select":
        return (
            f"<div class='select' {_wrapper_attrs(spec, 'select')}>"
            f"<label id='{fid}-label' for='{fid}' class='label select__label'>{label}</label>"
            f"{_select_widget(spec, fid, name)}</div>"
        )
    if spec.kind in {"radio", "multiselect"}:
        input_type = "radio" if spec.kind == "radio" else "checkbox"
        cls = "radio-group" if spec.kind == "radio" else "checkbox-group"
        boxes = "".join(
            f"<div class='{input_type}__wrapper'><input type='{input_type}' id='{fid}_{value}' "
            f"name='{esc(name)}' value='{value}' class='{input_type}__input'>"
            f"<label for='{fid}_{value}' class='{input_type}__label'>{esc(option)}</label></div>"
            for value, option in spec.options
        )
        return (
            f"<fieldset class='{cls}' {_wrapper_attrs(spec, spec.kind)} id='{fid}'>"
            f"<legend class='label'>{label}</legend>{boxes}</fieldset>"
        )
    return (
        f"<div class='checkbox' {_wrapper_attrs(spec, 'checkbox')}><div class='checkbox__wrapper'>"
        f"<input type='checkbox' id='{fid}' name='{esc(name)}' value='1' class='checkbox__input' "
        f"{_attrs(spec, False)}><label for='{fid}' class='checkbox__label'>{label}</label></div></div>"
    )


def _legacy_field(
    spec: _Field,
    values: dict[str, list[str]],
    errors: dict[str, str],
    job: MockJob | None = None,
) -> str:
    fid = spec.dom_id(True)
    name = spec.dom_name(True)
    posted = values.get(name, [])
    error = errors.get(spec.key)
    err_html = (
        f"<label class='error' for='{fid}' id='{fid}-error'>{esc(error)}</label>" if error else ""
    )
    field_cls = "field error" if error else "field"
    label = f"{esc(spec.label)} {_star(spec.required, True)}"
    hidden = ""
    if spec.group == "custom":
        hidden = (
            f"<input type='hidden' name='job_application[answers_attributes][{spec.index}][question_id]' "
            f"value='{spec.qid}'>"
        )
    attrs = _wrapper_attrs(spec, spec.kind)
    if spec.kind == "file":
        block = _file_block(spec, True)
        return block + (
            f"<label class='error' id='{fid}-error'>{esc(error)}</label>" if error else ""
        )
    if spec.kind in {"text", "email", "tel"}:
        value = esc(posted[0]) if posted else ""
        input_type = "tel" if spec.kind == "tel" else "text"
        default_max = "maxlength='255' " if spec.max_length is None else ""
        return (
            f"<div class='{field_cls}' {attrs}>{hidden}<label for='{fid}'>{label}</label>"
            f"<input {_attrs(spec, True)} autocomplete='off' id='{fid}' "
            f"{default_max}name='{esc(name)}' "
            f"type='{input_type}' value='{value}'>{err_html}</div>"
        )
    if spec.kind == "textarea":
        value = esc(posted[0]) if posted else ""
        return (
            f"<div class='{field_cls}' {attrs}>{hidden}<label for='{fid}'>{label}</label>"
            f"<textarea id='{fid}' name='{esc(name)}' rows='4' {_attrs(spec, True)}>{value}</textarea>"
            f"{err_html}</div>"
        )
    if spec.kind == "select":
        opts = "<option value=''>--</option>" + "".join(
            f"<option value='{value}'{' selected' if value in posted else ''}>{esc(opt)}</option>"
            for value, opt in spec.options
        )
        return (
            f"<div class='{field_cls}' {attrs}>{hidden}<label for='{fid}'>{label}</label>"
            f"<select id='{fid}' name='{esc(name)}' {_attrs(spec, True)}>{opts}</select>{err_html}</div>"
        )
    if spec.kind in {"radio", "multiselect"}:
        input_type = "radio" if spec.kind == "radio" else "checkbox"
        boxes = "".join(
            f"<li><label><input type='{input_type}' name='{esc(name)}' value='{value}'"
            f"{' checked' if value in posted else ''}> {esc(opt)}</label></li>"
            for value, opt in spec.options
        )
        return (
            f"<div class='{field_cls}' {attrs} id='{fid}'>{hidden}<fieldset><legend>{label}</legend>"
            f"<ul class='option-list'>{boxes}</ul></fieldset>{err_html}</div>"
        )
    checked = " checked" if "1" in posted else ""
    return (
        f"<div class='{field_cls}' {attrs}>{hidden}<input type='hidden' name='{esc(name)}' value='0'>"
        f"<input type='checkbox' id='{fid}' name='{esc(name)}' value='1'{checked}>"
        f"<label for='{fid}'>{label}</label>{err_html}</div>"
    )


# ------------------------------------------------------------------------------------ assets

_NEW_CSS = """<style>
*{box-sizing:border-box}
body{margin:0;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;color:#2b3440;background:#fff;
 font-size:16px;line-height:1.5}
[hidden]{display:none!important}
.board-nav{border-bottom:1px solid #e3e7ec;padding:14px 28px;display:flex;gap:22px;align-items:center}
.board-nav a{color:#1f5fbf;text-decoration:none}
.container{max-width:780px;margin:0 auto;padding:28px 18px 120px}
.section-header{margin:10px 0}
.section-header--large{font-size:28px}
.job__location{color:#5c6b7a;margin-bottom:14px}
.job__description{margin-bottom:34px}
.btn{display:inline-block;border:0;border-radius:24px;padding:10px 24px;font:600 15px inherit;
 cursor:pointer;background:#1f5fbf;color:#fff;text-decoration:none}
.btn--secondary{background:#fff;color:#1f5fbf;border:1px solid #1f5fbf;padding:7px 16px;margin:0 8px 8px 0}
.btn[disabled]{opacity:.55;cursor:default}
.btn-link{background:none;border:0;color:#1f5fbf;text-decoration:underline;cursor:pointer}
.required-note{color:#5c6b7a;font-size:14px}
.label{display:block;font-weight:600;margin:18px 0 6px}
.required{margin-left:2px}
.input{width:100%;padding:10px 12px;border:1px solid #aab4c0;border-radius:4px;font:inherit;background:#fff}
.input[aria-invalid=true]{border-color:#c0392b}
.helper-text{font-size:13px;color:#5c6b7a;margin:4px 0 0}
.helper-text--error{color:#c0392b;font-size:14px}
.flash-error{background:#fdecea;border-left:4px solid #c0392b;padding:12px 16px;margin:14px 0}
.loading{padding:32px;color:#5c6b7a}
.visually-hidden{position:absolute!important;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;
 clip:rect(0,0,0,0);white-space:nowrap;border:0}
.iti{position:relative;display:block}
.iti__country-container{position:absolute;top:0;bottom:0;left:0;display:flex;align-items:center;z-index:1}
.iti__selected-country{display:flex;align-items:center;gap:4px;border:0;background:none;padding:0 8px;height:100%}
.iti input{padding-left:82px}
.iti__flag{width:20px;height:14px;background:linear-gradient(#b22234 50%,#3c3b6e 50%)}
.select{margin:0}
.select-shell{position:relative}
.select__control{display:flex;align-items:center;min-height:42px;border:1px solid #aab4c0;border-radius:4px;
 background:#fff;cursor:default;position:relative}
.select__control--menu-is-open{border-color:#1f5fbf}
.select__value-container{display:flex;flex:1 1 auto;align-items:center;position:relative;padding:2px 8px;
 overflow:hidden;min-height:38px}
.select__placeholder,.select__single-value{position:absolute;left:8px;right:8px;color:#5c6b7a;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.select__single-value{color:#2b3440}
.select__input-container{flex:1 1 auto;display:block;position:relative;z-index:1}
.select__input{width:100%;border:0;outline:0;background:transparent;font:inherit;padding:6px 0;
 color:#2b3440;opacity:1}
.select__indicators{display:flex;align-items:center;align-self:stretch}
.select__indicator{padding:8px;color:#8593a1;display:flex}
.select__indicator-separator{width:1px;background:#d5dbe1;align-self:stretch;margin:8px 0}
.select__menu{position:absolute;top:100%;left:0;right:0;margin:8px 0;background:#fff;border-radius:4px;
 box-shadow:0 0 0 1px rgba(0,0,0,.1),0 4px 11px rgba(0,0,0,.1);z-index:20}
.select__menu-list{max-height:300px;overflow-y:auto;padding:4px 0}
.select__option{padding:8px 12px;cursor:default}
.select__option--is-focused{background:#deebff}
.select__option--is-selected{background:#1f5fbf;color:#fff}
.select__menu-notice{padding:10px 12px;color:#8593a1;text-align:center}
fieldset{border:0;padding:0;margin:18px 0 0}
legend{font-weight:600;padding:0;margin-bottom:6px}
.radio__wrapper,.checkbox__wrapper{display:flex;align-items:center;gap:8px;margin:4px 0}
.checkbox{margin:16px 0}
.file-upload{margin:18px 0}
.file-upload__filename{margin:6px 0}
.eeoc__container{margin-top:34px;border-top:1px solid #e3e7ec;padding-top:8px}
.form-actions{margin-top:30px}
.captcha-block{margin:22px 0}
.application--confirmation,#application-confirmation{padding:24px;background:#eaf6ee;border-radius:6px}
</style>"""

_LEGACY_CSS = """<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#333;background:#fff}
[hidden]{display:none!important}
#wrapper{max-width:800px;margin:0 auto;padding:20px}
#header .company-name{color:#777}
h1.app-title{font-size:28px;margin:6px 0}
.location{color:#777;margin-bottom:16px}
#application{margin-top:30px;border-top:1px solid #ddd;padding-top:10px}
.field{margin:14px 0}
.field label,fieldset legend{display:block;font-weight:bold;margin-bottom:4px}
.field input[type=text],.field input[type=tel],.field select,.field textarea{width:100%;padding:7px;
 border:1px solid #aaa;font:inherit}
.field.error input,.field.error select,.field.error textarea{border-color:#d00}
label.error{color:#d00;font-weight:normal;display:block;margin-top:3px}
.field.error>label:first-child{color:#d00}
.asterisk{color:#d00}
.option-list{list-style:none;margin:0;padding:0}
.option-list label{font-weight:normal;display:block}
.btn,.button{background:#2a7ab0;color:#fff;border:0;padding:9px 18px;cursor:pointer;font:inherit}
.btn--secondary{background:#eee;color:#333;margin:0 6px 6px 0}
.btn-link{background:none;border:0;color:#2a7ab0;text-decoration:underline;cursor:pointer}
.file-upload{margin:14px 0}
.upload-label label{font-weight:bold}
.helper-text{color:#777;font-size:12px}
.visually-hidden{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
#eeoc_fields{margin-top:24px;border-top:1px solid #ddd;padding-top:8px}
.flash-error{background:#fdecea;border-left:4px solid #d00;padding:10px 14px;margin:10px 0}
.loading{padding:24px;color:#777}
.confirmation{padding:20px;background:#eaf6ee}
.opening{margin:8px 0}
.opening .location{margin-left:12px;display:inline}
</style>"""

_GH_JS = r"""
(function () {
  'use strict';
  var CFG = JSON.parse(document.getElementById('gh-config').textContent);
  var LEGACY = CFG.variant !== 'new';
  var form = null;

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }

  /* ---------------------------------------------------------------- react-select style combobox */
  function Select(root) {
    var self = this;
    this.root = root;
    this.control = root.querySelector('.select__control');
    this.valueContainer = root.querySelector('.select__value-container');
    this.inputContainer = root.querySelector('.select__input-container');
    this.input = root.querySelector('input.select__input');
    this.indicators = root.querySelector('.select__indicators');
    this.hidden = root.querySelector('input[type=hidden]');
    this.options = JSON.parse(root.getAttribute('data-options'));
    this.placeholder = root.getAttribute('data-placeholder') || 'Select...';
    this.selected = null;
    this.open = false;
    this.focused = 0;
    this.text = '';
    this.menu = null;
    this.listboxId = 'react-select-' + this.input.id + '-listbox';

    this.control.addEventListener('mousedown', function (e) {
      var onInput = e.target === self.input;
      if (e.target.closest('.select__clear-indicator')) { self.clear(); e.preventDefault(); return; }
      if (!self.open) { self.openMenu(); self.input.focus(); }
      else if (!onInput) { self.closeMenu(); }
      if (!onInput) { e.preventDefault(); }
    });
    this.input.addEventListener('input', function () {
      self.text = self.input.value;
      self.open = true;
      self.focused = 0;
      self.render();
    });
    this.input.addEventListener('blur', function () {
      self.open = false;
      self.text = '';
      self.input.value = '';
      self.render();
    });
    this.input.addEventListener('keydown', function (e) { self.onKey(e); });
    this.render();
  }
  Select.prototype.filtered = function () {
    var t = this.text.trim().toLowerCase();
    if (!t) { return this.options.slice(); }
    return this.options.filter(function (o) { return (o.label + ' ' + o.value).toLowerCase().indexOf(t) !== -1; });
  };
  Select.prototype.openMenu = function () {
    this.open = true;
    var opts = this.filtered();
    var idx = 0;
    for (var i = 0; i < opts.length; i++) { if (this.selected && opts[i].value === this.selected.value) { idx = i; } }
    this.focused = idx;
    this.render();
  };
  Select.prototype.closeMenu = function () { this.open = false; this.text = ''; this.input.value = ''; this.render(); };
  Select.prototype.select = function (o) {
    this.selected = o;
    this.hidden.value = o.value;
    this.text = '';
    this.input.value = '';
    this.open = false;
    this.render();
    this.hidden.dispatchEvent(new Event('change', {bubbles: true}));
  };
  Select.prototype.clear = function () {
    this.selected = null;
    this.hidden.value = '';
    this.render();
    this.hidden.dispatchEvent(new Event('change', {bubbles: true}));
  };
  Select.prototype.move = function (delta) {
    var n = this.filtered().length;
    if (!n) { return; }
    this.focused = (this.focused + delta + n) % n;
    this.updateFocus();
  };
  Select.prototype.onKey = function (e) {
    var opts = this.filtered();
    switch (e.key) {
      case 'ArrowDown': if (!this.open) { this.openMenu(); } else { this.move(1); } e.preventDefault(); break;
      case 'ArrowUp': if (!this.open) { this.openMenu(); } else { this.move(-1); } e.preventDefault(); break;
      case 'Home': if (this.open) { this.focused = 0; this.updateFocus(); e.preventDefault(); } break;
      case 'End': if (this.open) { this.focused = Math.max(opts.length - 1, 0); this.updateFocus(); e.preventDefault(); } break;
      case 'PageDown': if (this.open) { this.focused = Math.min(this.focused + 5, Math.max(opts.length - 1, 0)); this.updateFocus(); e.preventDefault(); } break;
      case 'PageUp': if (this.open) { this.focused = Math.max(this.focused - 5, 0); this.updateFocus(); e.preventDefault(); } break;
      case 'Enter':
        if (this.open) { if (opts[this.focused]) { this.select(opts[this.focused]); } e.preventDefault(); }
        break;
      case 'Tab':
        if (this.open && !e.shiftKey && opts[this.focused]) { this.select(opts[this.focused]); e.preventDefault(); }
        break;
      case 'Escape': if (this.open) { this.closeMenu(); e.preventDefault(); } break;
      case ' ': if (!this.text) { if (!this.open) { this.openMenu(); } e.preventDefault(); } break;
      case 'Backspace': case 'Delete':
        if (!this.text && this.selected) { this.clear(); e.preventDefault(); }
        break;
    }
  };
  Select.prototype.updateFocus = function () {
    if (!this.menu) { return; }
    var self = this;
    this.menu.querySelectorAll('.select__option').forEach(function (el, i) {
      el.classList.toggle('select__option--is-focused', i === self.focused);
      if (i === self.focused) {
        self.input.setAttribute('aria-activedescendant', el.id);
        el.scrollIntoView({block: 'nearest'});
      }
    });
  };
  Select.prototype.render = function () {
    var self = this;
    var ph = this.valueContainer.querySelector('.select__placeholder');
    var sv = this.valueContainer.querySelector('.select__single-value');
    if (this.selected && !this.text) {
      if (ph) { ph.remove(); }
      if (!sv) { sv = h('div', 'select__single-value'); this.valueContainer.insertBefore(sv, this.inputContainer); }
      sv.textContent = this.selected.label;
    } else {
      if (sv) { sv.remove(); }
      if (!this.text && !this.selected) {
        if (!ph) {
          ph = h('div', 'select__placeholder', this.placeholder);
          ph.id = 'react-select-' + this.input.id + '-placeholder';
          this.valueContainer.insertBefore(ph, this.inputContainer);
        }
      } else if (ph) { ph.remove(); }
    }
    this.valueContainer.classList.toggle('select__value-container--has-value', !!this.selected);
    this.inputContainer.setAttribute('data-value', this.text);
    this.input.setAttribute('aria-expanded', this.open ? 'true' : 'false');
    var clear = this.indicators.querySelector('.select__clear-indicator');
    if (this.selected && !clear) {
      clear = h('div', 'select__indicator select__clear-indicator');
      clear.setAttribute('aria-hidden', 'true');
      clear.textContent = '×';
      this.indicators.insertBefore(clear, this.indicators.firstChild);
    } else if (!this.selected && clear) { clear.remove(); }
    if (this.menu) { this.menu.remove(); this.menu = null; }
    this.control.classList.toggle('select__control--menu-is-open', this.open);
    if (!this.open) {
      this.input.removeAttribute('aria-controls');
      this.input.removeAttribute('aria-activedescendant');
      return;
    }
    var opts = this.filtered();
    if (this.focused >= opts.length) { this.focused = opts.length - 1; }
    if (this.focused < 0) { this.focused = 0; }
    var menu = h('div', 'select__menu');
    menu.addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); self.input.focus(); });
    if (!opts.length) {
      menu.appendChild(h('div', 'select__menu-notice select__menu-notice--no-options', 'No options'));
    } else {
      var list = h('div', 'select__menu-list');
      list.id = this.listboxId;
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-multiselectable', 'false');
      opts.forEach(function (o, i) {
        var isSel = !!(self.selected && self.selected.value === o.value);
        var d = h('div', 'select__option' + (isSel ? ' select__option--is-selected' : ''), o.label);
        d.id = 'react-select-' + self.input.id + '-option-' + i;
        d.setAttribute('role', 'option');
        d.setAttribute('aria-selected', isSel ? 'true' : 'false');
        d.setAttribute('aria-disabled', 'false');
        d.tabIndex = -1;
        d.addEventListener('click', function () { self.select(o); });
        d.addEventListener('mousemove', function () {
          if (self.focused !== i) { self.focused = i; self.updateFocus(); }
        });
        list.appendChild(d);
      });
      menu.appendChild(list);
      this.input.setAttribute('aria-controls', this.listboxId);
    }
    this.root.appendChild(menu);
    this.menu = menu;
    this.updateFocus();
  };

  /* ---------------------------------------------------------------- file upload blocks */
  function initUploads() {
    form.querySelectorAll('.file-upload').forEach(function (block) {
      var input = block.querySelector('input[type=file]');
      var actions = block.querySelector('.file-upload__actions');
      var fname = block.querySelector('.file-upload__filename');
      var manual = block.querySelector('.file-upload__manual');
      var text = manual.querySelector('textarea');
      block.querySelector('[data-source="attach"]').addEventListener('click', function () { input.click(); });
      block.querySelector('[data-source="paste"]').addEventListener('click', function () {
        manual.hidden = false;
        text.focus();
      });
      input.addEventListener('change', function () {
        clearError(block);
        var f = input.files && input.files[0];
        if (!f) { fname.hidden = true; actions.hidden = false; return; }
        var ext = f.name.indexOf('.') >= 0 ? f.name.split('.').pop().toLowerCase() : '';
        if (CFG.exts.indexOf(ext) === -1) {
          input.value = '';
          showError(block, 'Unsupported file type. Accepted file types: ' + CFG.exts.join(', '));
          return;
        }
        if (f.size > CFG.maxBytes) { input.value = ''; showError(block, 'File is too large.'); return; }
        fname.querySelector('.filename').textContent = f.name;
        fname.hidden = false;
        actions.hidden = true;
        manual.hidden = true;
      });
      fname.querySelector('[data-action=remove]').addEventListener('click', function () {
        input.value = '';
        fname.hidden = true;
        actions.hidden = false;
      });
    });
  }

  /* ---------------------------------------------------------------- validation + errors */
  function primary(wrapper) {
    return wrapper.querySelector('input.select__input') ||
      wrapper.querySelector('input:not([type=hidden]):not([type=file]), textarea, select') ||
      wrapper.querySelector('button');
  }
  function isBlank(wrapper) {
    var kind = wrapper.getAttribute('data-kind');
    if (kind === 'select' && wrapper.querySelector('.select-shell')) {
      return !wrapper.querySelector('input[type=hidden]').value;
    }
    if (kind === 'select') { return !wrapper.querySelector('select').value; }
    if (kind === 'radio' || kind === 'multiselect') { return !wrapper.querySelector('input:checked'); }
    if (kind === 'checkbox') { return !wrapper.querySelector('input[type=checkbox]:checked'); }
    if (kind === 'file') {
      var fi = wrapper.querySelector('input[type=file]');
      var ta = wrapper.querySelector('textarea');
      return !(fi.files && fi.files.length) && !ta.value.trim();
    }
    var el = wrapper.querySelector('input:not([type=hidden]), textarea');
    return !el.value.trim();
  }
  function clearError(wrapper) {
    wrapper.classList.remove('error', 'has-error');
    wrapper.querySelectorAll('.helper-text--error, label.error').forEach(function (e) { e.remove(); });
    wrapper.querySelectorAll('[aria-invalid]').forEach(function (e) { e.setAttribute('aria-invalid', 'false'); });
  }
  function showError(wrapper, message) {
    clearError(wrapper);
    var key = wrapper.getAttribute('data-field') || 'field';
    var ctl = primary(wrapper);
    var id = (ctl && ctl.id ? ctl.id : key.replace(/[^a-z0-9_]/gi, '_')) + '-error';
    var msg;
    if (LEGACY) {
      wrapper.classList.add('error');
      msg = h('label', 'error', message);
      if (ctl && ctl.id) { msg.setAttribute('for', ctl.id); }
    } else {
      wrapper.classList.add('has-error');
      msg = h('p', 'helper-text helper-text--error', message);
      msg.setAttribute('role', 'alert');
    }
    msg.id = id;
    wrapper.appendChild(msg);
    wrapper.querySelectorAll('input:not([type=hidden]):not([type=file]), textarea, select').forEach(function (e) {
      e.setAttribute('aria-invalid', 'true');
    });
    if (ctl && !LEGACY) { ctl.setAttribute('aria-describedby', id); }
  }
  function validate() {
    var bad = [];
    form.querySelectorAll('[data-field]').forEach(function (w) {
      var key = w.getAttribute('data-field');
      if (key === 'captcha') { return; }
      clearError(w);
      var required = w.getAttribute('data-required') === 'true';
      if (isBlank(w)) {
        if (required) { showError(w, LEGACY ? 'This field is required.' : 'This field is required'); bad.push(w); }
        return;
      }
      if (key === 'email') {
        var v = w.querySelector('input').value.trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { showError(w, 'Please enter a valid email address.'); bad.push(w); }
      }
    });
    if (bad.length) {
      var first = primary(bad[0]);
      if (first) { first.scrollIntoView({block: 'center'}); first.focus(); }
    }
    return bad;
  }
  function banner(text) {
    var old = document.getElementById('submit-error');
    if (old) { old.remove(); }
    var b = h('div', 'flash-error', text);
    b.id = 'submit-error';
    b.setAttribute('role', 'alert');
    form.insertBefore(b, form.querySelector('.form-actions') || form.lastChild);
  }
  function applyServerErrors(errors) {
    var first = null;
    Object.keys(errors).forEach(function (key) {
      var w = form.querySelector('[data-field="' + key + '"]');
      if (w) { showError(w, errors[key]); first = first || w; }
      else { banner(errors[key]); }
    });
    if (first) { var c = primary(first); if (c) { c.scrollIntoView({block: 'center'}); } }
  }
  function showSecurityCode(message) {
    var slot = document.getElementById('security-code-slot');
    if (!slot) { return; }
    slot.innerHTML = '<div class="text-input-wrapper" data-field="security_code" data-kind="text" data-required="true">' +
      '<label id="security_code-label" for="security_code" class="label">Security code<span class="required">*</span></label>' +
      '<div class="input-wrapper"><input type="text" id="security_code" name="security_code" ' +
      'class="input input__single-line" autocomplete="one-time-code" maxlength="8"></div>' +
      '<p class="helper-text">' + message + '</p></div>';
    document.getElementById('security_code').focus();
  }

  /* ---------------------------------------------------------------- submit */
  function showConfirmation() {
    var box = document.getElementById('application-container');
    box.innerHTML = '<div id="application-confirmation" class="application--confirmation" role="status">' +
      '<h2 class="section-header section-header--large font-primary">Thank you for applying.</h2>' +
      '<p>Your application has been submitted.</p></div>';
    window.scrollTo(0, 0);
  }
  function captchaSolved() {
    var f = form.querySelector('[name="' + CFG.captchaField + '"]');
    return !!(f && f.value);
  }
  function send() {
    var button = form.querySelector('button[type=submit]');
    var data = new FormData(form);
    if (CFG.invisible) { data.append('g-recaptcha-response', 'mock-invisible-recaptcha-token'); }
    button.disabled = true;
    fetch(CFG.action, {method: 'POST', body: data, headers: {'x-requested-with': 'fetch'}})
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return {status: r.status, body: j}; });
      })
      .then(function (res) {
        button.disabled = false;
        if (res.status === 200 && res.body.ok) { showConfirmation(); return; }
        if (res.body && res.body.needs_code) {
          if (document.getElementById('security_code')) { applyServerErrors(res.body.errors || {}); }
          else { showSecurityCode('We emailed you a security code. Enter it and submit again.'); }
          return;
        }
        if (res.status === 422 && res.body.errors) { applyServerErrors(res.body.errors); return; }
        banner('Something went wrong submitting your application. Please try again.');
      })
      .catch(function () {
        button.disabled = false;
        banner('Something went wrong submitting your application. Please try again.');
      });
  }
  function onSubmit(ev) {
    var bad = validate();
    if (LEGACY) {
      if (bad.length) { ev.preventDefault(); return; }
      if (CFG.captchaGate && !captchaSolved()) {
        ev.preventDefault();
        window.__requireCaptcha(CFG.captchaField, function () { form.requestSubmit(); });
      }
      return;
    }
    ev.preventDefault();
    var old = document.getElementById('submit-error');
    if (old) { old.remove(); }
    if (bad.length) { return; }
    if (CFG.captchaGate) { window.__requireCaptcha(CFG.captchaField, send); return; }
    send();
  }
  function rerender(ev) {
    var t = ev.target;
    var textual = t instanceof HTMLTextAreaElement ||
      (t instanceof HTMLInputElement && (t.type === 'text' || t.type === 'tel'));
    if (!textual || t.classList.contains('select__input')) { return; }
    var clone = t.cloneNode(true);
    clone.value = t.value;
    var pos = t.selectionStart;
    t.replaceWith(clone);
    clone.focus();
    try { clone.setSelectionRange(pos, pos); } catch (err) { /* not selectable */ }
  }

  function boot() {
    form = document.getElementById('application-form') || document.getElementById('application_form');
    if (!form) { return; }
    form.querySelectorAll('.select-shell').forEach(function (root) { new Select(root); });
    initUploads();
    form.addEventListener('submit', onSubmit);
    var clearer = function (ev) {
      var w = ev.target.closest && ev.target.closest('[data-field]');
      if (w && (w.classList.contains('error') || w.classList.contains('has-error'))) {
        if (!isBlank(w)) { clearError(w); }
      }
    };
    form.addEventListener('input', clearer);
    form.addEventListener('change', clearer);
    if (CFG.rerender && !LEGACY) { form.addEventListener('input', rerender); }
  }

  if (CFG.embedded) {
    var post = function () {
      parent.postMessage({type: 'grnhse-resize', height: document.documentElement.scrollHeight}, '*');
    };
    setInterval(post, 300);
    post();
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
    variant: Variant = "new",
    name: str | None = None,
    company_name: str | None = None,
    require_captcha: bool = False,
    captcha_provider: Provider = "recaptcha",
    captcha_placement: Placement = "inline",
    invisible_recaptcha: bool = False,
    render_delay_s: float = 0.0,
    rerender_on_input: bool = False,
    cover_letter: CoverLetter = "optional",
    eeo: bool = True,
    phone_required: bool = True,
    max_upload_bytes: int = 10 * 1024 * 1024,
    security_code: bool = False,
    cookie_consent: CookieBanner | None = None,
) -> GreenhouseSite:
    """Build a mock Greenhouse board (see the module docstring for what every option guarantees)."""
    return GreenhouseSite(
        company,
        list(jobs) if jobs else default_jobs(),
        variant=variant,
        name=name or "greenhouse",
        company_name=company_name or company.replace("-", " ").replace("_", " ").title(),
        require_captcha=require_captcha,
        captcha_provider=captcha_provider,
        captcha_placement=captcha_placement,
        invisible_recaptcha=invisible_recaptcha,
        render_delay_s=render_delay_s,
        rerender_on_input=rerender_on_input,
        cover_letter=cover_letter,
        eeo=eeo,
        phone_required=phone_required,
        max_upload_bytes=max_upload_bytes,
        security_code=security_code,
        cookie_consent=cookie_consent,
    )
