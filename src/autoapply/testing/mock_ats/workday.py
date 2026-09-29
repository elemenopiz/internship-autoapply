"""Hermetic mock of a Workday candidate-experience tenant (``<tenant>.wd5.myworkdayjobs.com``).

Purpose: develop and prove the Workday adapter with zero network access. The mock reproduces the markup, widgets and
quirks that break scripted browsers on the real product (React-style inputs, custom dropdowns, prompt/typeahead
widgets, an overlay in front of the sign-in button, unstable element ids, full re-renders, delayed XHRs, session
timeouts). The mock is the contract: everything documented here is guaranteed by ``tests/unit/testing/test_mock_workday.py``.
Ids that are marked "(best effort)" come from memory of the real site and may differ per tenant, so prefer the
``data-automation-id`` values listed here, then labels; never CSS classes, ``id`` attributes (regenerated on every
render: ``input-17``) or positions.

Usage::

    site = make_site("acme", jobs=None, **options)   # -> WorkdaySite (a MockSite); default job R0012345
    hub = MockHub(); hub.add(site); hub.start()      # base.py; site.name == "workday-<tenant>"
    page.goto(site.job_url())                        # http://acme.wd5.myworkdayjobs.com.localhost:<port>/en-US/External/job/...
    site.apply_url(job_id, path)                     # .../apply[/applyManually]

Browser notes: launch Chromium with ``--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost,
EXCLUDE 127.0.0.1"`` (the SPEC 1.8 rule without ``EXCLUDE *.localhost`` makes every mock host unreachable).
Playwright's ``page.request`` / ``context.request`` resolve DNS in Node and cannot reach ``*.localhost``: use an in-page
``fetch`` (``page.evaluate``) when a script needs raw HTTP. The site loads nothing from any other host.

Options (keyword arguments of ``make_site``)
    tenant / wd / site_name     host ``<tenant>.<wd>.myworkdayjobs.com`` (tenant defaults to the company slug), path
                                segment after the locale (default ``External``); ``name`` overrides the hub name
                                ``workday-<tenant>``; ``company_name`` the display name ("Acme").
    verify_email=False          True: "Create Account" leaves the account inactive and delivers a mail (subject
                                "Verify your email", sender ``<tenant>@myworkday.com``, one URL
                                ``<base_url>/en-US/External/activate/<token>``) to ``hub.mailbox``. Signing in before the
                                link is opened is refused; opening it redirects to the apply page (``?verified=1``).
    captcha_on_signin=False     True: sign-in / create-account show a visible "Verify you are human" challenge
                                (``iframe[title="reCAPTCHA"]`` whose src contains ``recaptcha``); submitting without
                                ticking it is refused. Only a human ticks it: adapters must stop with BOT_CHECK.
    session_expires_after_s     None; else the session dies this many seconds after sign-in. It expires at most
    max_session_expiries=1      ``max_session_expiries`` times per site. ``site.expire_sessions()`` expires every
                                signed-in session at its next request (deterministic, does not count).
    latency_ms=(200, 600)       simulated server latency of every XHR (uniform, seeded by ``seed``); an int is fixed.
    resume_required / education_required / source_required / require_consent / require_terms
                                (all True) which validations and checkboxes exist; ``cover_letter_slot=False`` adds
                                a second upload section; ``allow_signup=True``; ``lockout_after=5`` failed sign-ins lock
                                the account; ``cookie_banner=False`` pins a cookie notice to the bottom of the viewport;
                                ``skip_steps=()`` drops wizard pages (never the first or the Review page);
                                ``schools`` replaces the school catalogue; ``max_upload_bytes`` (5 MiB).
    ``site.faults`` (base.py) works on the XHR paths: job data ``/wday/cxs/<tenant>/<site>/...``, flow ``/wday/app/...``.

URLs
    /en-US/<site>                                   job list (``jobTitle`` links); ``/<site>`` redirects to it
    /en-US/<site>/job/<Location>/<Title>_<req id>   job page; ``/apply`` opens the chooser, ``/apply/<path>`` the flow
                                                    with ``<path>`` in autofillWithResume | applyManually |
                                                    useMyLastApplication. Closed / unknown postings: HTTP 404 with
                                                    "The page you are looking for doesn't exist."
    Slugs follow Workday: spaces become ``-`` (so " - " becomes ``---``), punctuation is dropped. The SPA keeps the
    URL of the flow constant from the chooser to the confirmation; the wizard page is NOT in the URL.
    The page is an empty shell until its XHRs return: wait for the selectors below instead of sleeping.

Job page: ``jobPostingPage``, ``jobPostingHeader`` (h2), ``adventureButton`` (the Apply link, appears after the job
XHR), ``locations``, ``time``, ``postedOn``, ``requisitionId``, ``jobPostingDescription``.

Chooser (a ``role=dialog`` "Start Your Application" over the job page): ``autofillWithResume``, ``applyManually``
and, only for a signed-in candidate with an earlier submitted application, ``useMyLastApplication``. They are anchors
with real hrefs. A signed-in candidate who already has a draft for the job skips the chooser and lands in the wizard
on the page where they stopped. Autofill pre-fills My Information with wrong parsed data ("Autofilled Applicant"); Use My
Last Application copies every saved page except Application Questions, resume included. The choice is recorded in
``Submission.meta["apply_path"]``.

Sign in / Create account (dialog after the chooser, when there is no session)
    ``signInContent`` -> ``email``, ``password`` (inputs), ``signInSubmitButton``, ``createAccountLink`` (absent when
    ``allow_signup=False``), ``forgotPasswordLink`` (-> ``forgotPasswordContent``: ``email``,
    ``forgotPasswordSubmitButton``; delivers a "Reset your password" mail with a working link).
    ``createAccountContent`` -> ``email``, ``password``, ``verifyPassword``, ``createAccountCheckbox`` (consent, absent
    when ``require_consent=False``), ``createAccountSubmitButton``, ``signInLink``. Password policy: 8+ characters with
    upper, lower, digit and special character; the verify field must match.
    QUIRK: ``signInSubmitButton`` / ``createAccountSubmitButton`` are ``div[role=button]`` covered by a transparent
    sibling ``div[role=button][data-automation-id="click_filter"]`` with the same aria-label. ``click()`` on the button
    times out with "intercepts pointer events", ``get_by_role("button", name="Sign In")`` is a strict-mode violation.
    What works: click ``click_filter``; ``click(force=True)`` on the button; Enter in the password field; focusing the
    button and pressing Enter. A synthetic ``dispatch_event("click")`` on the underlying button does nothing.
    Messages appear in ``errorMessage`` (role=alert): wrong credentials "The username or password you entered is
    incorrect. Please try again."; locked "Your account has been locked ..."; unverified "Your account has not been
    verified yet ..."; duplicate "An account with this email address already exists ..."; also invalid email, weak
    password, mismatch, missing consent, missing CAPTCHA. Info banners: ``verifyEmailNotice`` (after creating an account
    with ``verify_email``), ``accountVerifiedNotice``, ``forgotPasswordNotice``, ``sessionExpiredNotice``. Without
    email verification a created account is signed in immediately. Accounts are per tenant.

Wizard: the frame (progress bar, page heading) is painted first and the form one request later, so the page container
exists before its fields; wait for ``bottom-navigation-next-button`` (only present once the form is there).
    ``progressBar`` > ``progressBarCompletedStep`` / ``progressBarActiveStep`` / ``progressBarInactiveStep`` (li; the text
    contains screen-reader text such as "current step 1 of 6" before the label). Pages, in order, each a container
    with the given id: My Information ``applyFlowMyInfoPage``, My Experience ``applyFlowMyExpPage``, Application
    Questions ``applyFlowPrimaryQuestionsPage`` (only when the job has non-EEO questions), Voluntary Disclosures
    ``applyFlowVoluntaryDisclosuresPage``, Self Identify ``applyFlowSelfIdentifyPage``, Review
    ``applyFlowReviewPage``. ``bottom-navigation-footer`` holds ``bottom-navigation-next-button`` ("Save and Continue",
    "Submit" on Review) and, after the first page, ``bottom-navigation-back-button``. While a save is in flight the button
    is disabled and a translucent overlay swallows clicks. Saved pages live on the server: reload, Back and a re-login
    resume where the candidate stopped; the values typed on the unsaved page are lost.
    Every field sits in ``[data-automation-id="formField-<name>"]`` (best effort names; application questions use an
    opaque ``formField-<32 hex>`` built from the question, so find them by label text). Labels are ``<label for>``
    (``fieldset``/``legend`` for radio groups); a trailing ``*`` marks required fields; ids in ``id``/``for`` change on
    every render.

    My Information: ``source--source`` (prompt, "How Did You Hear About Us?"; tree of Company Website, Job Board >
    Indeed/LinkedIn/Glassdoor/ZipRecruiter, University / Campus > Career Fair/Handshake/University Career Center,
    Employee Referral, Social Media > Facebook/Instagram/X (Twitter), Other; single value), ``previousWorker`` (two
    radios, values "true"/"false", labels Yes/No), ``countryDropdown`` ("Country", default United States of America;
    changing it resets the region), ``legalNameSection_firstName``, ``legalNameSection_middleName``,
    ``legalNameSection_lastName``, ``addressSection_addressLine1``, ``addressSection_addressLine2``,
    ``addressSection_city``, ``addressSection_countryRegion`` (dropdown "State", options depend on the country, absent
    for countries without a list), ``addressSection_postalCode`` (ZIP format for the US), ``phone-device-type``
    (dropdown Home/Mobile/Work), ``country-phone-code`` (dropdown "United States of America (+1)" ...),
    ``phone-number`` (7-15 digits), ``phone-extension``.
    My Experience: ``workExperienceSection`` (Add button, groups ``workExperience-1`` ...; inside: ``jobTitle``,
    ``company``, ``location``, ``currentlyWorkHere`` (checkbox; hides the end date), dates ``startDate`` and ``endDate``
    as ``<name>-dateSectionMonth-input`` + ``<name>-dateSectionYear-input``, ``roleDescription`` (max 2000);
    ``panel-set-delete-button``), ``educationSection`` (groups ``education-1`` ...: ``school`` (typeahead), ``degree``
    (dropdown), ``fieldOfStudy`` (multi prompt), ``gpa`` (0-4.0), ``firstYearAttended-dateSectionYear-input``,
    ``lastYearAttended-dateSectionYear-input``; at least one entry when ``education_required``), ``skillsSection``
    (input ``skills``, multi prompt), ``resumeSection`` (+ ``coverLetterSection`` with ``cover_letter_slot``),
    ``websiteSection`` (groups ``websitePanelSet-1`` ...: ``url``), ``linkedinQuestion``; both must be http(s):// URLs.
    Add buttons: ``add-button`` inside each section (label "Add", then "Add Another"); the three sections all use the
    same id, so scope by section. Nothing is pre-created: click Add first.
    Application Questions: one block per ``MockJob.questions`` entry except the EEO keys gender / race / veteran /
    disability: text -> input (``maxlength``), textarea -> textarea (``maxlength``; the browser truncates longer text),
    select -> dropdown (list starts with "Select One"), radio -> radio group, checkbox -> one checkbox (or a checkbox
    group when the question has options), multiselect -> search prompt. Required flags follow ``MockQuestion.required``.
    Voluntary Disclosures: dropdowns ``gender``, ``ethnicity``, ``veteranStatus`` (each with a "Decline to Self
    Identify" / "I don't wish to answer" option) and, with ``require_terms``, the checkbox ``agreementCheckbox``.
    Self Identify (disability form): ``selfIdentifiedDisabilityData--name`` (text),
    ``selfIdentifiedDisabilityData--dateSignedOn-dateSection{Month,Day,Year}-input``, radios
    ``selfIdentifiedDisabilityData--disabilityStatus`` (values are the three option texts, the last is "I do not want
    to answer"). Review: ``reviewSection`` > ``reviewRow`` (dt label, dd value) for every saved page.

Widgets (all of them ignore Playwright's ``select_option`` and only react to real events)
    Dropdown: ``button[aria-haspopup=listbox]`` with ``data-automation-id`` = field id, text = the current value or
    "Select One", ``aria-label`` = "<Label> <value> Required". Click opens ``activeListContainer`` >
    ``ul[role=listbox]`` > ``li[role=option]`` > ``menuItem`` (``data-automation-label`` = text). Country, state,
    phone code and degree lists are fetched on open (a "Loading..." item, ``loadingText``, first). Arrow keys move,
    Enter picks, Escape / outside click / moving focus closes, typing letters highlights the first matching option.
    Picking re-renders the whole page (old element handles go stale).
    Prompt / multiselect: an ``input`` (role=combobox) with ``data-automation-id`` = field id inside
    ``multiSelectContainer``; results are ``activeListContainer`` > ``li[role=option]`` > ``promptOption``
    (``data-automation-label``; folders carry an arrow and open on click, a back button returns). Chosen values are pills
    ``selectedItemList`` > ``selectedItem`` with a ``DELETE_charm`` button. ``source--source`` (tree): CLICK opens the
    top level, typing + Enter searches every level. ``fieldOfStudy`` / ``skills`` and the multiselect questions
    (search): nothing opens until Enter is pressed. ``school`` (typeahead): results appear by themselves ~350 ms after
    the last keystroke, Enter searches at once. Search matches when every typed word is the start of some word of the
    option, so a leading "The" finds nothing ("No Items."). Only a picked option counts: typed text is never part of the saved
    value (an open list is closed, and the text cleared, when focus moves on). The school catalogue has "University of Texas at Austin" (no "The") plus six
    similar UT campuses.
    Date: two or three ``<name>-dateSection{Month,Day,Year}-input`` text inputs (placeholders MM DD YYYY): digits only,
    max 2/2/4 characters, a full month or day moves focus to the next box.
    Text inputs: the app only learns about a value from a real input event. Assigning ``el.value`` (even followed by a
    synthetic ``input`` event) is ignored and undone by the next render; ``fill``, typing, and the native
    ``HTMLInputElement.prototype`` value setter followed by an ``input`` event work.
    Radios / checkboxes: visually custom, the real ``<input>`` is transparent on top: ``check()``, ``click()`` and label
    clicks work. Upload: ``input[type=file][data-automation-id="file-upload-input-ref"]`` is hidden (``display:none``;
    use ``set_input_files`` or click ``select-files`` under ``expect_file_chooser``); ``file-upload-drop-zone`` shows
    until a file is accepted (after a simulated upload request), then ``file-upload-successful`` (containing
    ``file-upload-item-name`` and ``delete-file``) replaces it and the input disappears. PDF/DOC/DOCX/TXT/RTF up to
    ``max_upload_bytes``; other files show an ``errorMessage`` in that section and are not stored.

Validation: "Save and Continue" validates on the server. Failure keeps the page and shows ``errorBanner`` (role=alert,
h3 "Errors Found", one button per problem "Error - The field <Label> is required and must have a value." or a format
message such as "Error - Postal Code is not a valid ZIP code."; clicking one focuses the field) plus an inline
``errorMessage`` (role=alert) inside each field, ``aria-invalid="true"`` on the control. Typing in a field removes its
inline message (the banner stays until the next save). The typed values of a failed save are kept. A page whose XHR
fails (HTTP 503 by ``site.faults``) shows ``errorMessage`` "We are experiencing technical difficulties ..." and can be
retried.

Session expiry: the next authenticated XHR returns 401 and the SPA swaps to the sign-in dialog with
``sessionExpiredNotice`` (same URL, job page behind it). After signing in again the wizard resumes on the same page.

Confirmation: ``applicationSubmittedPage`` with the heading "Application Submitted", "Congratulations! Your application
for <title> (<req id>) has been submitted." and "Thank you for applying to <Company>." Repeating the application with the
same account (new session or replayed request) shows ``alreadyApplied`` ("Already Applied", "You have already applied for
this job ...") and records nothing.

Recorded submission (``site.submissions[-1]``, recorded once, at the final Submit): ``path`` is
``/wday/app/apply/<req id>/submit``; ``meta`` has tenant, job_id, email, apply_path, confirmation; ``files`` are
``UploadedFile(field="resume" | "coverLetter", original filename, bytes)``; ``fields`` maps names to lists of strings,
omitting empty optional fields: ``email``, ``job_id``, ``source``, ``previousWorker`` (Yes/No), ``country``,
``legalNameSection_*``, ``addressSection_*``, ``phone-device-type``, ``country-phone-code``, ``phone-number``,
``phone-extension``, ``workExperience-<n>.<jobTitle|company|location|currentlyWorkHere ("true"/"false")|startDate
("MM/YYYY")|endDate|roleDescription>``, ``education-<n>.<school|degree|fieldOfStudy|gpa|firstYearAttended|
lastYearAttended>``, ``skills``, ``websitePanelSet-<n>.url``, ``linkedinQuestion``, each ``MockQuestion.key`` (the
chosen option text(s); "true"/"false" for a lone checkbox), ``gender``, ``race`` (the Ethnicity dropdown), ``veteran``,
``agreementCheckbox``, ``selfIdentifiedDisabilityData--name``, ``selfIdentifiedDisabilityData--dateSignedOn``
("MM/DD/YYYY"), ``disability``. Hidden fields (end date of a current job) are not recorded.

Python helpers: ``site.job_url(id)``, ``site.apply_url(id, path)``, ``site.add_account(email, password, verified=True)``,
``site.accounts`` (state["accounts"], per tenant), ``site.drafts`` (state["drafts"], keyed (email, job id)),
``site.submitted_applications()``, ``site.events`` (chronological log, e.g. ``sign_in_ok:<email>``, ``choose:applyManually``,
``step_saved:myInformation``, ``validation_failed:<page>:<n>``, ``session_expired:<email>``, ``upload:<slot>:<name>``,
``submitted:<req id>``), ``site.expire_sessions()``, ``site.state`` (``timed_expiries`` ...).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from autoapply.testing.mock_ats.base import (
    STANDARD_QUESTIONS,
    MockJob,
    MockQuestion,
    MockSite,
    UploadedFile,
    html_page,
)

COOKIE = "PLAY_SESSION"
LOCALE = "en-US"
APP_API = "/wday/app"
APPLY_PATHS = ("autofillWithResume", "applyManually", "useMyLastApplication")
FILE_SLOTS = ("resume", "coverLetter")
ALLOWED_UPLOAD_EXT = (".pdf", ".doc", ".docx", ".txt", ".rtf")
REQUIRED_MSG = "The field {label} is required and must have a value."

# (step id, progress-bar label, data-automation-id of the page container)
STEP_DEFS: tuple[tuple[str, str, str], ...] = (
    ("myInformation", "My Information", "applyFlowMyInfoPage"),
    ("myExperience", "My Experience", "applyFlowMyExpPage"),
    ("applicationQuestions", "Application Questions", "applyFlowPrimaryQuestionsPage"),
    ("voluntaryDisclosures", "Voluntary Disclosures", "applyFlowVoluntaryDisclosuresPage"),
    ("selfIdentify", "Self Identify", "applyFlowSelfIdentifyPage"),
    ("review", "Review", "applyFlowReviewPage"),
)
# Standard EEO questions are answered on the dedicated Voluntary Disclosures / Self Identify steps.
EEO_KEYS = frozenset({"gender", "race", "veteran", "disability"})

COUNTRIES = [
    "Australia", "Austria", "Belgium", "Brazil", "Canada", "Chile", "China", "Colombia",
    "Costa Rica", "Czech Republic", "Denmark", "Egypt", "Finland", "France", "Germany", "Ghana",
    "Greece", "Hong Kong", "Hungary", "India", "Indonesia", "Ireland", "Israel", "Italy", "Japan",
    "Kenya", "Malaysia", "Mexico", "Netherlands", "New Zealand", "Nigeria", "Norway", "Pakistan",
    "Peru", "Philippines", "Poland", "Portugal", "Saudi Arabia", "Singapore", "South Africa",
    "South Korea", "Spain", "Sweden", "Switzerland", "Taiwan", "Thailand", "Turkey",
    "United Arab Emirates", "United Kingdom", "United States Minor Outlying Islands",
    "United States of America", "Vietnam",
]  # fmt: skip
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
    "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]  # fmt: skip
CA_PROVINCES = [
    "Alberta", "British Columbia", "Manitoba", "New Brunswick", "Newfoundland and Labrador",
    "Nova Scotia", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
]  # fmt: skip
REGIONS = {"United States of America": US_STATES, "Canada": CA_PROVINCES}
PHONE_TYPES = ["Home", "Mobile", "Work"]
PHONE_CODES = [
    "Australia (+61)", "Brazil (+55)", "Canada (+1)", "China (+86)", "France (+33)",
    "Germany (+49)", "India (+91)", "Ireland (+353)", "Italy (+39)", "Japan (+81)",
    "Mexico (+52)", "Netherlands (+31)", "Nigeria (+234)", "Singapore (+65)", "South Korea (+82)",
    "Spain (+34)", "United Kingdom (+44)", "United States of America (+1)",
]  # fmt: skip
DEGREES = [
    "High School Diploma or GED", "Associate's Degree", "Bachelor's Degree", "Bachelor of Arts",
    "Bachelor of Business Administration", "Bachelor of Science", "Master's Degree",
    "Master of Arts", "Master of Business Administration", "Master of Science",
    "Doctor of Philosophy", "Other",
]  # fmt: skip
FIELDS_OF_STUDY = [
    "Accounting", "Business Administration", "Business Analytics", "Chemical Engineering",
    "Civil Engineering", "Computer Engineering", "Computer Science", "Data Science", "Economics",
    "Electrical Engineering", "Finance", "Industrial Engineering", "Information Systems",
    "Information Technology", "Management", "Management Information Systems", "Marketing",
    "Mathematics", "Mechanical Engineering", "Operations Management", "Physics",
    "Political Science", "Psychology", "Statistics", "Supply Chain Management", "Undeclared",
]  # fmt: skip
SKILLS = [
    "Agile Methodologies", "Business Analysis", "Data Analysis", "Excel", "Java", "JavaScript",
    "JIRA", "Machine Learning", "Microsoft Office", "Power BI", "Product Management",
    "Project Management", "Python", "R", "SQL", "Stakeholder Management", "Tableau",
    "User Research",
]  # fmt: skip
SCHOOLS = [
    "Austin Community College", "Baylor University", "Cornell University",
    "Georgia Institute of Technology", "Harvard University", "Massachusetts Institute of Technology",
    "Purdue University", "Rice University", "Southern Methodist University", "Stanford University",
    "Texas A&M University", "Texas State University", "Texas Tech University",
    "University of California, Berkeley", "University of Houston", "University of Michigan",
    "University of North Texas", "University of Texas at Arlington",
    "University of Texas at Austin", "University of Texas at Dallas",
    "University of Texas at El Paso", "University of Texas at San Antonio",
    "University of Texas Rio Grande Valley", "Other",
]  # fmt: skip
GENDERS = ["Male", "Female", "Non-Binary", "Decline to Self Identify"]
ETHNICITIES = [
    "Hispanic or Latino (United States of America)",
    "White (Not Hispanic or Latino) (United States of America)",
    "Black or African American (Not Hispanic or Latino) (United States of America)",
    "Asian (Not Hispanic or Latino) (United States of America)",
    "American Indian or Alaska Native (Not Hispanic or Latino) (United States of America)",
    "Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino) (United States of America)",
    "Two or More Races (Not Hispanic or Latino) (United States of America)",
    "Decline to Self Identify",
]
VETERAN_STATUSES = [
    "I identify as one or more of the classifications of protected veteran",
    "I am not a protected veteran",
    "I don't wish to answer",
]
DISABILITY_STATUSES = [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
]


def _slug_id(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _tree(label: str, *children: str) -> dict[str, Any]:
    node: dict[str, Any] = {"id": _slug_id(label), "label": label}
    if children:
        node["children"] = [{"id": _slug_id(f"{label}-{c}"), "label": c} for c in children]
    return node


SOURCE_TREE = [
    _tree("Company Website"),
    _tree("Job Board", "Indeed", "LinkedIn", "Glassdoor", "ZipRecruiter"),
    _tree("University / Campus", "Career Fair", "Handshake", "University Career Center"),
    _tree("Employee Referral"),
    _tree("Social Media", "Facebook", "Instagram", "X (Twitter)"),
    _tree("Other"),
]

# The recorded / displayed name of an apply path when the user clicked it.
_AUTOFILL_JUNK = {
    "legalNameSection_firstName": "Autofilled",
    "legalNameSection_lastName": "Applicant",
    "addressSection_city": "Parseville",
    "phone-number": "0000000000",
}


def _default_jobs() -> list[MockJob]:
    q = STANDARD_QUESTIONS
    return [
        MockJob(
            id="R0012345",
            title="Product Management Intern - Summer 2027",
            location="Austin, TX",
            questions=(q["work_auth"], q["sponsorship"], q["relocate"], q["salary"], q["certify"]),
        )
    ]


# --------------------------------------------------------------------------------- field specs


def _f(t: str, id_: str, label: str = "", **kw: Any) -> dict[str, Any]:
    """One field spec of the (JSON) form schema the browser app renders and the server validates."""
    spec: dict[str, Any] = {"t": t, "id": id_, "label": label}
    spec.update({k: v for k, v in kw.items() if v is not None})
    return spec


def _key(f: dict[str, Any]) -> str:
    return str(f.get("key", f["id"]))


def _is_input(f: dict[str, Any]) -> bool:
    return f["t"] not in ("info", "heading")


def _visible(f: dict[str, Any], scope: dict[str, Any]) -> bool:
    cond = f.get("show")
    if not cond:
        return True
    value = scope.get(cond["k"])
    if "in" in cond:
        return value in cond["in"]
    if cond.get("not"):
        return not value
    return bool(value)


def _blank_for(f: dict[str, Any]) -> Any:
    if "default" in f:
        return copy.deepcopy(f["default"])
    t = f["t"]
    if t == "checkbox":
        return False
    if t in ("checkboxes", "prompt", "repeater"):
        return []
    if t == "date":
        return {"m": "", "d": "", "y": ""}
    if t == "file":
        return None
    return ""


def _blank_values(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {_key(f): _blank_for(f) for f in fields if _is_input(f)}


def _repeater(
    id_: str, group: str, title: str, fields: list[dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    kw.setdefault("key", group)
    return _f(
        "repeater", id_, title, group=group, title=title, fields=fields,
        blank=_blank_values(fields), **kw,
    )  # fmt: skip


def _clean_value(f: dict[str, Any], raw: Any) -> Any:
    """Coerce a value received from the browser to the shape the schema expects."""
    t = f["t"]
    if t in ("text", "textarea", "dropdown", "radio"):
        return raw.strip()[:5000] if isinstance(raw, str) else ""
    if t == "checkbox":
        return raw is True
    if t == "checkboxes":
        return [x for x in raw if isinstance(x, str)][:50] if isinstance(raw, list) else []
    if t == "prompt":
        if not isinstance(raw, list):
            return []
        return [
            {"id": str(o.get("id", "")), "label": str(o.get("label", ""))}
            for o in raw
            if isinstance(o, dict) and o.get("label")
        ][:50]
    if t == "date":
        d = raw if isinstance(raw, dict) else {}
        return {k: re.sub(r"\D", "", str(d.get(k, "")))[:4] for k in ("m", "d", "y")}
    if t == "repeater":
        if not isinstance(raw, list):
            return []
        return [_clean_values(f["fields"], e) for e in raw[:20] if isinstance(e, dict)]
    return None


def _clean_values(fields: list[dict[str, Any]], raw: dict[str, Any]) -> dict[str, Any]:
    return {_key(f): _clean_value(f, raw.get(_key(f))) for f in fields if _is_input(f)}


def _fmt_date(parts: str, d: dict[str, Any]) -> str:
    if not any(d.get(p) for p in parts):
        return ""
    fields = {
        "m": str(d.get("m", "")).zfill(2),
        "d": str(d.get("d", "")).zfill(2),
        "y": d.get("y", ""),
    }
    return "/".join(str(fields[p]) for p in parts)


def _option_pair(o: Any) -> tuple[str, str]:
    return (o, o) if isinstance(o, str) else (o[0], o[1])


def _strings(f: dict[str, Any], v: Any) -> list[str]:
    """Human-readable values of a field (recorded submission + review page)."""
    t = f["t"]
    if t in ("text", "textarea", "dropdown"):
        return [v] if isinstance(v, str) and v else []
    if t == "radio":
        for o in f.get("options", []):
            value, label = _option_pair(o)
            if value == v:
                return [label]
        return []
    if t == "checkbox":
        return ["true" if v else "false"]
    if t == "checkboxes":
        return list(v or [])
    if t == "prompt":
        return [o["label"] for o in v or []]
    if t == "date":
        text = _fmt_date(f.get("parts", "my"), v or {})
        return [text] if text else []
    return []


def _walk(
    fields: list[dict[str, Any]], values: dict[str, Any], prefix: str = "", where: str = ""
) -> Any:
    """Yield (field, record key, value, context label) for every visible input field, entering repeaters."""
    for f in fields:
        if not _is_input(f) or not _visible(f, values):
            continue
        key = _key(f)
        if f["t"] == "repeater":
            for i, entry in enumerate(values.get(key) or []):
                yield from _walk(
                    f["fields"], entry, f"{f['group']}-{i + 1}.", f"{f['title']} {i + 1}"
                )
        else:
            yield f, prefix + key, values.get(key), where


def _validate_text(kind: str, text: str, scope: dict[str, Any]) -> str | None:
    if kind == "phone":
        digits = re.sub(r"\D", "", text)
        return None if 7 <= len(digits) <= 15 else "{label} is not a valid phone number."
    if kind == "postal":
        if scope.get("country") != "United States of America":
            return None
        return None if re.fullmatch(r"\d{5}(-\d{4})?", text) else "{label} is not a valid ZIP code."
    if kind == "gpa":
        ok = re.fullmatch(r"\d(\.\d{1,2})?", text) is not None and float(text) <= 4.0
        return None if ok else "{label} must be a number between 0 and 4.0."
    if kind == "url":
        return (
            None
            if re.fullmatch(r"https?://\S+\.\S+", text)
            else "{label} must be a valid URL starting with http:// or https://."
        )
    return None


def _check_date(f: dict[str, Any], d: dict[str, Any]) -> str | None:
    parts = f.get("parts", "my")
    present = [p for p in parts if d.get(p)]
    if not present:
        return REQUIRED_MSG if f.get("req") else None
    if len(present) < len(parts):
        return "{label} is not a complete date."
    year = int(d["y"]) if "y" in parts else 2000
    if not 1950 <= year <= 2100:
        return "{label}: the year must be between 1950 and 2100."
    if "m" in parts and not 1 <= int(d["m"]) <= 12:
        return "{label}: the month must be between 1 and 12."
    if "d" in parts:
        try:
            date(year, int(d["m"]), int(d["d"]))
        except ValueError:
            return "{label} is not a valid date."
    return None


def _wd_slug(text: str) -> str:
    """Workday style URL slug: spaces become '-', most punctuation is dropped (' - ' becomes '---')."""
    cleaned = re.sub(r"[^\w\s\-.]", "", text).strip()
    return re.sub(r"\s", "-", cleaned)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _leaves(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("children"):
            out.extend(_leaves(n["children"]))
        else:
            out.append(n)
    return out


def _find_node(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for n in nodes:
        if n["id"] == node_id:
            return n
        if n.get("children") and (hit := _find_node(n["children"], node_id)):
            return hit
    return None


def _search_nodes(nodes: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Every query word must be the start of some word of the option label (so a leading 'The' matches nothing)."""
    wanted = _tokens(query)
    if not wanted:
        return []
    hits = []
    for n in _leaves(nodes):
        words = _tokens(n["label"])
        if all(any(w.startswith(t) for w in words) for t in wanted):
            hits.append(n)
    return hits[:30]


# --------------------------------------------------------------------------------- state


@dataclass
class Account:
    """A candidate account of this tenant (Workday accounts are per tenant)."""

    email: str
    password: str
    verified: bool = True
    locked: bool = False
    failed_logins: int = 0
    last_application: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class Draft:
    """One account's application to one job: saved wizard steps live on the server, like the real site."""

    email: str
    job_id: str
    path: str
    cursor: int = 0
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    prefill: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, UploadedFile] = field(default_factory=dict)
    submitted: bool = False
    confirmation: str | None = None


@dataclass
class Session:
    sid: str
    email: str | None = None
    signed_in_at: float = 0.0
    captcha_ok: bool = False
    force_expire: bool = False


R = TypeVar("R", bound=Response)


def _password_problem(password: str) -> str | None:
    ok = (
        len(password) >= 8
        and re.search(r"[a-z]", password)
        and re.search(r"[A-Z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password)
    )
    if ok:
        return None
    return (
        "The password must be at least 8 characters long and contain an uppercase letter, "
        "a lowercase letter, a number and a special character."
    )


class WorkdaySite(MockSite):
    """A hermetic Workday candidate-experience tenant. Build it with ``make_site``; see the module docstring."""

    def __init__(
        self,
        company: str = "acme",
        jobs: list[MockJob] | None = None,
        *,
        tenant: str | None = None,
        wd: str = "wd5",
        site_name: str = "External",
        name: str | None = None,
        company_name: str | None = None,
        verify_email: bool = False,
        captcha_on_signin: bool = False,
        session_expires_after_s: float | None = None,
        max_session_expiries: int = 1,
        latency_ms: int | tuple[int, int] = (200, 600),
        seed: int = 1,
        resume_required: bool = True,
        education_required: bool = True,
        source_required: bool = True,
        require_consent: bool = True,
        require_terms: bool = True,
        cover_letter_slot: bool = False,
        allow_signup: bool = True,
        cookie_banner: bool = False,
        lockout_after: int | None = 5,
        skip_steps: tuple[str, ...] = (),
        schools: list[str] | None = None,
        max_upload_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.tenant = tenant or re.sub(r"[^a-z0-9]", "", company.lower()) or "tenant"
        super().__init__(name or f"workday-{self.tenant}", f"{self.tenant}.{wd}.myworkdayjobs.com")
        self.company_name = company_name or company.replace("-", " ").replace("_", " ").title()
        self.site_name = site_name
        self.verify_email = verify_email
        self.captcha_on_signin = captcha_on_signin
        self.session_expires_after_s = session_expires_after_s
        self.max_session_expiries = max_session_expiries
        self.latency_ms: tuple[int, int] = (
            (latency_ms, latency_ms) if isinstance(latency_ms, int) else latency_ms
        )
        self.resume_required = resume_required
        self.education_required = education_required
        self.source_required = source_required
        self.require_consent = require_consent
        self.require_terms = require_terms
        self.cover_letter_slot = cover_letter_slot
        self.allow_signup = allow_signup
        self.cookie_banner = cookie_banner
        self.lockout_after = lockout_after
        self.skip_steps = tuple(skip_steps)
        self.schools = list(schools) if schools is not None else list(SCHOOLS)
        self.max_upload_bytes = max_upload_bytes
        self._rng = random.Random(seed)
        for job in jobs if jobs is not None else _default_jobs():
            self.jobs[job.id] = job
        self.state.update(
            accounts={}, sessions={}, drafts={}, events=[], verify_tokens={}, reset_tokens={},
            timed_expiries=0,
        )  # fmt: skip
        self._install_routes()

    # -- public helpers for tests / adapter authors ------------------------------------------------
    @property
    def accounts(self) -> dict[str, Account]:
        """Candidate accounts of this tenant, keyed by lower-cased email."""
        accounts: dict[str, Account] = self.state["accounts"]
        return accounts

    @property
    def drafts(self) -> dict[tuple[str, str], Draft]:
        """Applications (in progress or submitted), keyed by (account email, job id)."""
        drafts: dict[tuple[str, str], Draft] = self.state["drafts"]
        return drafts

    @property
    def events(self) -> list[str]:
        """Chronological server-side event log, e.g. ``choose:applyManually``, ``sign_in_ok:<email>``."""
        events: list[str] = self.state["events"]
        return events

    def add_account(self, email: str, password: str, *, verified: bool = True) -> Account:
        account = Account(email=email.strip().lower(), password=password, verified=verified)
        self.accounts[account.email] = account
        return account

    def expire_sessions(self) -> None:
        """Every signed-in session is dropped at its next authenticated request (deterministic timeout)."""
        for sess in self.state["sessions"].values():
            sess.force_expire = True

    def submitted_applications(self) -> list[Draft]:
        return [d for d in self.drafts.values() if d.submitted]

    def job_path(self, job: MockJob) -> str:
        return (
            f"/{LOCALE}/{self.site_name}/job/{_wd_slug(job.location)}/"
            f"{_wd_slug(job.title)}_{job.id}"
        )

    def job_url(self, job_id: str | None = None) -> str:
        return self.url(self.job_path(self._job(job_id)))

    def apply_url(self, job_id: str | None = None, path: str | None = None) -> str:
        return self.job_url(job_id) + "/apply" + (f"/{path}" if path else "")

    def _job(self, job_id: str | None) -> MockJob:
        if job_id is None:
            return next(iter(self.jobs.values()))
        return self.jobs[job_id]

    # -- plumbing --------------------------------------------------------------------------------------
    def _event(self, name: str, detail: str = "") -> None:
        self.events.append(f"{name}:{detail}" if detail else name)

    async def _delay(self) -> None:
        low, high = self.latency_ms
        if high > 0:
            await asyncio.sleep(self._rng.uniform(low, high) / 1000)

    def _session(self, request: Request) -> tuple[Session, bool]:
        sessions: dict[str, Session] = self.state["sessions"]
        sid = request.cookies.get(COOKIE, "")
        if sid in sessions:
            return sessions[sid], False
        sess = Session(sid=secrets.token_hex(16))
        sessions[sess.sid] = sess
        return sess, True

    def _stamp(self, resp: R, sess: Session, new: bool) -> R:
        if new:
            resp.set_cookie(COOKIE, sess.sid, httponly=True, samesite="lax", path="/")
        return resp

    def _json(
        self, sess: Session, new: bool, data: dict[str, Any], status: int = 200
    ) -> JSONResponse:
        resp = JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})
        return self._stamp(resp, sess, new)

    async def _body(self, request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _current_account(self, sess: Session) -> tuple[Account | None, bool]:
        """(account, just_expired). Applies the ``expire_sessions`` / ``session_expires_after_s`` timeouts."""
        if not sess.email:
            return None, False
        timed_out = (
            self.session_expires_after_s is not None
            and self.state["timed_expiries"] < self.max_session_expiries
            and time.monotonic() - sess.signed_in_at > self.session_expires_after_s
        )
        if sess.force_expire or timed_out:
            if timed_out:
                self.state["timed_expiries"] += 1
            self._event("session_expired", sess.email)
            sess.email = None
            sess.force_expire = False
            return None, True
        return self.accounts.get(sess.email), False

    def _unauth(self, sess: Session, new: bool, expired: bool) -> JSONResponse:
        payload: dict[str, Any] = {
            "view": "signin",
            "captcha": self.captcha_on_signin,
            "allowSignup": self.allow_signup,
        }
        if expired:
            payload["notice"] = {
                "kind": "warn",
                "aid": "sessionExpiredNotice",
                "text": "Your session has expired. Please sign in again to continue your application.",
            }
        return self._json(sess, new, payload, 401)

    def _parse_rest(self, rest: str) -> tuple[MockJob | None, list[str]]:
        """``<location>/<title>_<req id>[/apply[/<path>]]`` -> (job, remaining segments)."""
        segs = [s for s in rest.split("/") if s]
        if len(segs) >= 2:
            for job in self.jobs.values():
                if segs[1].endswith(f"_{job.id}"):
                    return job, segs[2:]
        return None, []

    # -- routes ------------------------------------------------------------------------------------------
    def _install_routes(self) -> None:
        add = self.app.add_api_route
        cxs = f"/wday/cxs/{self.tenant}/{self.site_name}"
        add("/favicon.ico", self._h_favicon, methods=["GET"], include_in_schema=False)
        add("/assets/wd-app.js", self._h_js, methods=["GET"], include_in_schema=False)
        add("/assets/wd-app.css", self._h_css, methods=["GET"], include_in_schema=False)
        add(f"{cxs}/jobs", self._h_jobs, methods=["POST"], include_in_schema=False)
        add(f"{cxs}/job/{{rest:path}}", self._h_job_json, methods=["GET"], include_in_schema=False)
        add(f"{APP_API}/auth/sign-in", self._h_sign_in, methods=["POST"], include_in_schema=False)
        add(
            f"{APP_API}/auth/create-account",
            self._h_create,
            methods=["POST"],
            include_in_schema=False,
        )
        add(
            f"{APP_API}/auth/forgot-password",
            self._h_forgot,
            methods=["POST"],
            include_in_schema=False,
        )
        base = f"{APP_API}/apply/{{job_id}}"
        add(f"{base}/state", self._h_state, methods=["POST"], include_in_schema=False)
        add(f"{base}/page", self._h_page, methods=["GET"], include_in_schema=False)
        add(f"{base}/save", self._h_save, methods=["POST"], include_in_schema=False)
        add(f"{base}/back", self._h_back, methods=["POST"], include_in_schema=False)
        add(f"{base}/submit", self._h_submit, methods=["POST"], include_in_schema=False)
        add(f"{base}/upload/{{slot}}", self._h_upload, methods=["POST"], include_in_schema=False)
        add(
            f"{base}/upload/{{slot}}",
            self._h_upload_delete,
            methods=["DELETE"],
            include_in_schema=False,
        )
        add(f"{base}/options", self._h_options, methods=["GET"], include_in_schema=False)
        add(f"{base}/prompt/{{pid}}", self._h_prompt, methods=["GET"], include_in_schema=False)
        add(
            "/captcha/recaptcha/api2/anchor",
            self._h_captcha_frame,
            methods=["GET"],
            include_in_schema=False,
        )
        add("/captcha/solve", self._h_captcha_solve, methods=["POST"], include_in_schema=False)
        add(
            "/{locale}/{site}/activate/{token}",
            self._h_activate,
            methods=["GET"],
            include_in_schema=False,
        )
        add(
            "/{locale}/{site}/reset/{token}",
            self._h_reset_page,
            methods=["GET"],
            include_in_schema=False,
        )
        add(
            "/{locale}/{site}/reset/{token}",
            self._h_reset_submit,
            methods=["POST"],
            include_in_schema=False,
        )
        add(
            "/{locale}/{site}/job/{rest:path}",
            self._h_job_page,
            methods=["GET"],
            include_in_schema=False,
        )
        add("/{locale}/{site}", self._h_list_page, methods=["GET"], include_in_schema=False)
        add(
            "/{site}/job/{rest:path}",
            self._h_locale_redirect_job,
            methods=["GET"],
            include_in_schema=False,
        )
        add("/{site}", self._h_locale_redirect, methods=["GET"], include_in_schema=False)
        add("/", self._h_root, methods=["GET"], include_in_schema=False)

    async def _h_favicon(self) -> Response:
        return Response(status_code=204)

    async def _h_js(self) -> Response:
        return Response(
            _APP_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"}
        )

    async def _h_css(self) -> Response:
        return Response(_APP_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})

    async def _h_root(self) -> Response:
        return RedirectResponse(f"/{LOCALE}/{self.site_name}", status_code=302)

    async def _h_locale_redirect(self, site: str) -> Response:
        if site != self.site_name:
            return self._not_found_page()
        return RedirectResponse(f"/{LOCALE}/{self.site_name}", status_code=302)

    async def _h_locale_redirect_job(self, site: str, rest: str) -> Response:
        if site != self.site_name:
            return self._not_found_page()
        return RedirectResponse(f"/{LOCALE}/{self.site_name}/job/{rest}", status_code=302)

    def _not_found_page(self) -> HTMLResponse:
        page = html_page(
            f"{self.company_name} Careers",
            '<div data-automation-id="pageNotFound" style="font-family:Arial;max-width:640px;margin:80px auto">'
            "<h2>The page you are looking for doesn't exist.</h2>"
            f'<p><a href="/{LOCALE}/{self.site_name}">Search for Jobs</a></p></div>',
        )
        page.status_code = 404
        return page

    def _spa(
        self,
        request: Request,
        *,
        mode: str,
        job: MockJob | None = None,
        hint: str | None = None,
        verified: bool = False,
    ) -> HTMLResponse:
        sess, new = self._session(request)
        boot: dict[str, Any] = {
            "mode": mode,
            "tenant": self.tenant,
            "company": self.company_name,
            "locale": LOCALE,
            "site": self.site_name,
            "cxs": f"/wday/cxs/{self.tenant}/{self.site_name}",
            "app": APP_API,
            "listPath": f"/{LOCALE}/{self.site_name}",
            "jobId": job.id if job else None,
            "jobPath": self.job_path(job) if job else None,
            "jobRest": "/".join(self.job_path(job).split("/job/", 1)[1].split("/")[:2])
            if job
            else None,
            "pathHint": hint,
            "verified": verified,
            "requireConsent": self.require_consent,
            "cookieBanner": self.cookie_banner,
        }
        payload = json.dumps(boot).replace("<", "\\u003c")
        title = html.escape(
            f"{job.title} - {self.company_name} Careers" if job else f"{self.company_name} Careers"
        )
        body = (
            '<div id="wd-root"></div>'
            '<div id="wd-busy" class="wd-busy" role="progressbar" aria-label="Loading" aria-busy="true" hidden></div>'
            "<noscript>JavaScript is required to use this site.</noscript>"
            f'<script type="application/json" id="wd-bootstrap">{payload}</script>'
            '<script src="/assets/wd-app.js" defer></script>'
        )
        head = (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<link rel="stylesheet" href="/assets/wd-app.css">'
        )
        return self._stamp(html_page(title, body, head), sess, new)

    async def _h_list_page(self, request: Request, locale: str, site: str) -> Response:
        if site != self.site_name:
            return self._not_found_page()
        return self._spa(request, mode="list")

    async def _h_job_page(self, request: Request, locale: str, site: str, rest: str) -> Response:
        if site != self.site_name:
            return self._not_found_page()
        job, tail = self._parse_rest(rest)
        if job is None or job.closed:
            return self._not_found_page()
        verified = request.query_params.get("verified") == "1"
        if tail and tail[0] == "apply":
            return self._spa(
                request,
                mode="apply",
                job=job,
                hint=tail[1] if len(tail) > 1 else None,
                verified=verified,
            )
        return self._spa(request, mode="job", job=job)

    async def _h_jobs(self, request: Request) -> Response:
        sess, new = self._session(request)
        await self._delay()
        postings = [
            {
                "title": j.title,
                "externalPath": self.job_path(j).split(f"/{self.site_name}", 1)[1],
                "locationsText": j.location,
                "postedOn": "Posted 2 Days Ago",
                "bulletFields": [j.id],
            }
            for j in self.jobs.values()
            if not j.closed
        ]
        return self._json(sess, new, {"total": len(postings), "jobPostings": postings})

    async def _h_job_json(self, request: Request, rest: str) -> Response:
        sess, new = self._session(request)
        await self._delay()
        job, _ = self._parse_rest(rest)
        if job is None or job.closed:
            return self._json(sess, new, {"error": "not_found"}, 404)
        info = {
            "id": job.id,
            "title": job.title,
            "location": job.location,
            "timeType": "Intern",
            "postedOn": "Posted 2 Days Ago",
            "jobReqId": job.id,
            "jobDescription": "".join(
                f"<p>{html.escape(p)}</p>" for p in job.description.split("\n") if p
            ),
            "externalUrl": self.job_url(job.id),
            "canApply": True,
        }
        return self._json(
            sess, new, {"jobPostingInfo": info, "hiringOrganization": {"name": self.company_name}}
        )

    # -- captcha (only when captcha_on_signin) -------------------------------------------------------------
    async def _h_captcha_frame(self, request: Request) -> Response:
        sess, new = self._session(request)
        checked = " checked" if sess.captcha_ok else ""
        page = html_page(
            "reCAPTCHA",
            '<div style="font-family:Arial;padding:14px;border:1px solid #ccc;width:280px">'
            f'<label><input type="checkbox" id="recaptcha-anchor" role="checkbox"{checked}> I am not a robot</label>'
            '<div style="font-size:10px;color:#666;margin-top:8px">reCAPTCHA (mock)</div></div>'
            "<script>document.getElementById('recaptcha-anchor').addEventListener('change',function(){"
            "fetch('/captcha/solve',{method:'POST',credentials:'same-origin'});});</script>",
        )
        return self._stamp(page, sess, new)

    async def _h_captcha_solve(self, request: Request) -> Response:
        sess, new = self._session(request)
        sess.captcha_ok = True
        self._event("captcha_solved")
        return self._json(sess, new, {"ok": True})

    # -- accounts --------------------------------------------------------------------------------------------
    async def _h_sign_in(self, request: Request) -> Response:
        body = await self._body(request)
        await self._delay()
        sess, new = self._session(request)
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        if self.captcha_on_signin and not sess.captcha_ok:
            self._event("sign_in_blocked_captcha", email)
            return self._json(
                sess,
                new,
                {"ok": False, "error": "Please verify that you are human before signing in."},
            )
        account = self.accounts.get(email)
        if account is not None and account.locked:
            self._event("sign_in_locked", email)
            return self._json(
                sess, new,
                {"ok": False, "error": "Your account has been locked after too many failed sign-in attempts. Reset your password or contact support."},
            )  # fmt: skip
        if account is None or account.password != password:
            if account is not None:
                account.failed_logins += 1
                if self.lockout_after is not None and account.failed_logins >= self.lockout_after:
                    account.locked = True
            self._event("sign_in_failed", email)
            return self._json(
                sess,
                new,
                {
                    "ok": False,
                    "error": "The username or password you entered is incorrect. Please try again.",
                },
            )
        if not account.verified:
            self._event("sign_in_unverified", email)
            return self._json(
                sess, new,
                {"ok": False, "error": "Your account has not been verified yet. Open the verification link we emailed you, then sign in."},
            )  # fmt: skip
        account.failed_logins = 0
        sess.email = account.email
        sess.signed_in_at = time.monotonic()
        self._event("sign_in_ok", email)
        return self._json(sess, new, {"ok": True})

    async def _h_create(self, request: Request) -> Response:
        body = await self._body(request)
        await self._delay()
        sess, new = self._session(request)
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        verify = str(body.get("verifyPassword", ""))

        def fail(message: str) -> Response:
            self._event("create_account_rejected", email)
            return self._json(sess, new, {"ok": False, "error": message})

        if not self.allow_signup:
            return fail("Account creation is not available for this site. Please contact support.")
        if self.captcha_on_signin and not sess.captcha_ok:
            return fail("Please verify that you are human before creating an account.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return fail("Please enter a valid email address.")
        if email in self.accounts:
            return fail(
                "An account with this email address already exists. Sign in instead, or use a different email address."
            )
        if problem := _password_problem(password):
            return fail(problem)
        if password != verify:
            return fail("The passwords you entered do not match.")
        if self.require_consent and body.get("consent") is not True:
            return fail("You must accept the terms and conditions to create an account.")
        account = self.add_account(email, password, verified=not self.verify_email)
        self._event("account_created", email)
        if self.verify_email:
            self._send_verification(account, str(body.get("returnTo") or ""))
            return self._json(sess, new, {"ok": True, "verify": True})
        sess.email = account.email
        sess.signed_in_at = time.monotonic()
        self._event("sign_in_ok", email)
        return self._json(sess, new, {"ok": True, "verify": False})

    def _send_verification(self, account: Account, return_to: str) -> None:
        token = secrets.token_urlsafe(18)
        self.state["verify_tokens"][token] = {"email": account.email, "return_to": return_to}
        link = self.url(f"/{LOCALE}/{self.site_name}/activate/{token}")
        self.mailbox.deliver(
            to=account.email,
            subject="Verify your email",
            body=(
                "Hello,\n\n"
                f"Thank you for creating a candidate account with {self.company_name}. "
                "To activate your account, please verify your email address by opening this link:\n\n"
                f"{link}\n\n"
                "If you did not create this account you can ignore this message.\n"
            ),
            sender=f"{self.tenant}@myworkday.com",
        )
        self._event("verification_sent", account.email)

    async def _h_activate(self, request: Request, locale: str, site: str, token: str) -> Response:
        record = self.state["verify_tokens"].get(token)
        account = self.accounts.get(record["email"]) if record else None
        if record is None or account is None:
            page = html_page(
                "Link expired", "<h2>This verification link is invalid or has expired.</h2>"
            )
            page.status_code = 410
            return page
        account.verified = True
        self._event("account_verified", account.email)
        target = record.get("return_to") or f"/{LOCALE}/{self.site_name}"
        if not target.startswith("/"):
            target = f"/{LOCALE}/{self.site_name}"
        return RedirectResponse(
            target + ("&" if "?" in target else "?") + "verified=1", status_code=302
        )

    async def _h_forgot(self, request: Request) -> Response:
        body = await self._body(request)
        await self._delay()
        sess, new = self._session(request)
        email = str(body.get("email", "")).strip().lower()
        account = self.accounts.get(email)
        if account is not None:
            token = secrets.token_urlsafe(18)
            self.state["reset_tokens"][token] = account.email
            link = self.url(f"/{LOCALE}/{self.site_name}/reset/{token}")
            self.mailbox.deliver(
                to=account.email,
                subject="Reset your password",
                body=f"Hello,\n\nTo choose a new password open this link:\n\n{link}\n",
                sender=f"{self.tenant}@myworkday.com",
            )
            self._event("password_reset_sent", email)
        return self._json(sess, new, {"ok": True})

    async def _h_reset_page(self, request: Request, locale: str, site: str, token: str) -> Response:
        if token not in self.state["reset_tokens"]:
            page = html_page(
                "Link expired", "<h2>This password reset link is invalid or has expired.</h2>"
            )
            page.status_code = 410
            return page
        return html_page(
            "Reset password",
            '<form method="post" style="font-family:Arial;max-width:420px;margin:60px auto"><h2>Reset Password</h2>'
            '<label>New password <input type="password" name="password"></label><br><br>'
            '<button type="submit">Reset password</button></form>',
        )

    async def _h_reset_submit(
        self, request: Request, locale: str, site: str, token: str
    ) -> Response:
        email = self.state["reset_tokens"].get(token)
        account = self.accounts.get(email) if email else None
        fields, _ = await self.read_form(request)
        password = (fields.get("password") or [""])[0]
        if account is None or _password_problem(password):
            return html_page(
                "Reset password", "<h2>The password does not meet the requirements.</h2>"
            )
        account.password = password
        account.locked = False
        account.failed_logins = 0
        account.verified = True
        del self.state["reset_tokens"][token]
        self._event("password_reset_done", account.email)
        return RedirectResponse(f"/{LOCALE}/{self.site_name}", status_code=302)

    # -- application flow ---------------------------------------------------------------------------------------
    def _step_ids(self, job: MockJob) -> list[str]:
        ids = []
        for sid, _, _ in STEP_DEFS:
            if sid in self.skip_steps and sid not in ("myInformation", "review"):
                continue
            if sid == "applicationQuestions" and not self._question_fields(job):
                continue
            ids.append(sid)
        return ids

    def _paths_for(self, account: Account | None) -> list[str]:
        paths = ["autofillWithResume", "applyManually"]
        if account is not None and account.last_application:
            paths.append("useMyLastApplication")
        return paths

    def _flow_for(self, account: Account, job: MockJob, hint: str | None) -> dict[str, Any]:
        draft = self.drafts.get((account.email, job.id))
        if draft is not None and draft.submitted:
            return {"view": "alreadyApplied", "reqId": job.id}
        if draft is None:
            if hint not in self._paths_for(account):
                return {"view": "chooser", "options": self._paths_for(account)}
            draft = self._new_draft(account, job, str(hint))
        return self._wizard_payload(draft, job)

    def _new_draft(self, account: Account, job: MockJob, path: str) -> Draft:
        draft = Draft(email=account.email, job_id=job.id, path=path)
        if path == "autofillWithResume":
            draft.prefill["myInformation"] = dict(_AUTOFILL_JUNK)
        elif path == "useMyLastApplication" and account.last_application:
            last = account.last_application
            draft.prefill = {
                k: copy.deepcopy(v) for k, v in last["steps"].items() if k != "applicationQuestions"
            }
            draft.files = dict(last["files"])
        self.drafts[(account.email, job.id)] = draft
        self._event("choose", path)
        return draft

    async def _h_state(self, request: Request, job_id: str) -> Response:
        body = await self._body(request)
        await self._delay()
        sess, new = self._session(request)
        job = self.jobs.get(job_id)
        if job is None or job.closed:
            return self._json(sess, new, {"view": "closed"}, 404)
        hint = body.get("path") if isinstance(body.get("path"), str) else None
        account, expired = self._current_account(sess)
        if account is not None:
            return self._json(sess, new, self._flow_for(account, job, hint))
        if hint not in APPLY_PATHS:
            return self._json(sess, new, {"view": "chooser", "options": self._paths_for(None)})
        payload: dict[str, Any] = {
            "view": "signin",
            "captcha": self.captcha_on_signin,
            "allowSignup": self.allow_signup,
        }
        if expired:
            payload["notice"] = {
                "kind": "warn",
                "aid": "sessionExpiredNotice",
                "text": "Your session has expired. Please sign in again to continue your application.",
            }
        elif body.get("verified") is True:
            payload["notice"] = {
                "kind": "info",
                "aid": "accountVerifiedNotice",
                "text": "Your email address has been verified. Sign in to continue your application.",
            }
        return self._json(sess, new, payload)

    async def _authed(
        self, request: Request, job_id: str
    ) -> tuple[Session, bool, Account | None, MockJob | None, Draft | None, JSONResponse | None]:
        """Common preamble of the authenticated XHR endpoints (after the simulated latency)."""
        await self._delay()
        sess, new = self._session(request)
        account, expired = self._current_account(sess)
        if account is None:
            return sess, new, None, None, None, self._unauth(sess, new, expired)
        job = self.jobs.get(job_id)
        if job is None or job.closed:
            return (
                sess,
                new,
                account,
                None,
                None,
                self._json(sess, new, {"ok": False, "view": {"view": "closed"}}, 404),
            )
        return sess, new, account, job, self.drafts.get((account.email, job_id)), None

    async def _h_save(self, request: Request, job_id: str) -> Response:
        body = await self._body(request)
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert account is not None and job is not None
        if draft is None or draft.submitted:
            return self._json(sess, new, {"ok": True, "view": self._flow_for(account, job, None)})
        ids = self._step_ids(job)
        step = body.get("step")
        if step != ids[draft.cursor] or step == "review":
            return self._json(sess, new, {"ok": True, "view": self._wizard_payload(draft, job)})
        fields = self._schema(step, job)
        raw = body.get("values")
        values = _clean_values(fields, raw if isinstance(raw, dict) else {})
        for slot in FILE_SLOTS:
            if slot in values:
                values[slot] = None  # files are managed by the upload endpoint only
        errors = self._validate(step, fields, values, draft)
        if errors:
            self._event("validation_failed", f"{step}:{len(errors)}")
            return self._json(sess, new, {"ok": False, "errors": errors})
        draft.steps[step] = values
        draft.cursor += 1
        self._event("step_saved", step)
        return self._json(sess, new, {"ok": True, "view": self._wizard_payload(draft, job)})

    async def _h_back(self, request: Request, job_id: str) -> Response:
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert account is not None and job is not None
        if draft is None or draft.submitted:
            return self._json(sess, new, {"ok": True, "view": self._flow_for(account, job, None)})
        draft.cursor = max(0, draft.cursor - 1)
        return self._json(sess, new, {"ok": True, "view": self._wizard_payload(draft, job)})

    async def _h_submit(self, request: Request, job_id: str) -> Response:
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert account is not None and job is not None
        if draft is None:
            return self._json(sess, new, {"ok": True, "view": self._flow_for(account, job, None)})
        if draft.submitted:
            self._event("submit_rejected_already_applied", account.email)
            return self._json(
                sess, new, {"ok": True, "view": {"view": "alreadyApplied", "reqId": job.id}}
            )
        ids = self._step_ids(job)
        if ids[draft.cursor] != "review" or any(sid not in draft.steps for sid in ids[:-1]):
            return self._json(sess, new, {"ok": True, "view": self._wizard_payload(draft, job)})
        fields = self._flatten(draft, job)
        files = [
            UploadedFile(field=slot, filename=f.filename, content_type=f.content_type, data=f.data)
            for slot, f in draft.files.items()
        ]
        draft.submitted = True
        draft.confirmation = "Application Submitted"
        account.last_application = {"steps": copy.deepcopy(draft.steps), "files": dict(draft.files)}
        self.record_submission(
            request.url.path, fields, files,
            tenant=self.tenant, job_id=job.id, email=account.email, apply_path=draft.path,
            confirmation=draft.confirmation,
        )  # fmt: skip
        self._event("submitted", job.id)
        view = {
            "view": "confirmation",
            "title": job.title,
            "reqId": job.id,
            "company": self.company_name,
        }
        return self._json(sess, new, {"ok": True, "view": view})

    async def _h_upload(self, request: Request, job_id: str, slot: str) -> Response:
        _, files = await self.read_form(request)
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert account is not None and job is not None
        if (
            draft is None
            or draft.submitted
            or slot not in FILE_SLOTS
            or (slot == "coverLetter" and not self.cover_letter_slot)
        ):
            return self._json(sess, new, {"ok": False, "error": "Unknown upload slot."}, 404)
        upload = next((f for f in files if f.field == "file"), None)
        if upload is None or not upload.data:
            return self._json(sess, new, {"ok": False, "error": "The selected file is empty."})
        if not upload.filename.lower().endswith(ALLOWED_UPLOAD_EXT):
            self._event("upload_rejected_type", upload.filename)
            return self._json(
                sess, new,
                {"ok": False, "error": "The file type is not supported. Please upload a PDF, DOC, DOCX, TXT or RTF file."},
            )  # fmt: skip
        if len(upload.data) > self.max_upload_bytes:
            self._event("upload_rejected_size", upload.filename)
            limit_mb = self.max_upload_bytes // (1024 * 1024)
            return self._json(
                sess,
                new,
                {
                    "ok": False,
                    "error": f"The file is too large. The maximum size is {limit_mb} MB.",
                },
            )
        draft.files[slot] = UploadedFile(
            field=slot, filename=upload.filename, content_type=upload.content_type, data=upload.data
        )
        self._event("upload", f"{slot}:{upload.filename}")
        return self._json(
            sess, new, {"ok": True, "file": {"name": upload.filename, "size": len(upload.data)}}
        )

    async def _h_upload_delete(self, request: Request, job_id: str, slot: str) -> Response:
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        if draft is not None and not draft.submitted:
            draft.files.pop(slot, None)
            self._event("upload_deleted", slot)
        return self._json(sess, new, {"ok": True})

    def _remote_options(self, fid: str, dep: str) -> list[str]:
        if fid == "countryDropdown":
            return COUNTRIES
        if fid == "addressSection_countryRegion":
            return REGIONS.get(dep, [])
        if fid == "country-phone-code":
            return PHONE_CODES
        if fid == "degree":
            return DEGREES
        return []

    async def _h_options(self, request: Request, job_id: str) -> Response:
        sess, new, _account, _job, _draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        options = self._remote_options(
            request.query_params.get("fid", ""), request.query_params.get("dep", "")
        )
        return self._json(sess, new, {"options": options})

    def _prompt_nodes(self, pid: str, job: MockJob) -> list[dict[str, Any]]:
        def flat(labels: list[str]) -> list[dict[str, Any]]:
            return [{"id": _slug_id(x), "label": x} for x in labels]

        if pid == "source":
            return SOURCE_TREE
        if pid == "school":
            return flat(self.schools)
        if pid == "fieldOfStudy":
            return flat(FIELDS_OF_STUDY)
        if pid == "skills":
            return flat(SKILLS)
        if pid.startswith("q:"):
            question = next((q for q in job.questions if q.key == pid[2:]), None)
            return flat(list(question.options)) if question else []
        return []

    async def _h_prompt(self, request: Request, job_id: str, pid: str) -> Response:
        sess, new, _account, job, _draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert job is not None
        nodes = self._prompt_nodes(pid, job)
        query = request.query_params.get("q", "")
        parent = request.query_params.get("parent", "")
        if query:
            found = _search_nodes(nodes, query)
        elif parent:
            node = _find_node(nodes, parent)
            found = list(node.get("children", [])) if node else []
        else:
            found = nodes if pid == "source" else []
        options = [
            {"id": n["id"], "label": n["label"], "folder": bool(n.get("children"))} for n in found
        ]
        return self._json(sess, new, {"options": options})

    # -- schema -----------------------------------------------------------------------------------------------------
    def _question_fields(self, job: MockJob) -> list[dict[str, Any]]:
        fields = []
        for q in job.questions:
            if q.key in EEO_KEYS:
                continue
            fields.append(self._question_field(job, q))
        return fields

    def _question_field(self, job: MockJob, q: MockQuestion) -> dict[str, Any]:
        digest = hashlib.sha1(f"{job.id}/{q.key}".encode()).hexdigest()[:32]
        common: dict[str, Any] = {"key": q.key, "req": q.required, "wrap": f"formField-{digest}"}
        if q.kind == "text":
            return _f("text", digest, q.label, max=q.max_length, **common)
        if q.kind == "textarea":
            return _f("textarea", digest, q.label, max=q.max_length, **common)
        if q.kind == "select":
            return _f(
                "dropdown",
                digest,
                q.label,
                options=list(q.options),
                ph="Select One",
                phOption=True,
                **common,
            )
        if q.kind == "radio":
            return _f("radio", digest, q.label, options=list(q.options), **common)
        if q.kind == "multiselect":
            return _f(
                "prompt", digest, q.label, prompt=f"q:{q.key}", mode="search", multi=True, **common
            )
        if q.options:
            return _f("checkboxes", digest, q.label, options=list(q.options), **common)
        return _f("checkbox", digest, q.label, **common)

    def _schema(self, step_id: str, job: MockJob) -> list[dict[str, Any]]:
        if step_id == "myInformation":
            return self._schema_info()
        if step_id == "myExperience":
            return self._schema_experience()
        if step_id == "applicationQuestions":
            return self._question_fields(job)
        if step_id == "voluntaryDisclosures":
            return self._schema_disclosures()
        if step_id == "selfIdentify":
            return self._schema_self_identify()
        return []

    def _schema_info(self) -> list[dict[str, Any]]:
        return [
            _f("prompt", "source--source", "How Did You Hear About Us?", key="source", req=self.source_required, prompt="source", mode="tree", multi=False, hid="source--source", wrap="formField-source"),
            _f("radio", "previousWorker", f"Have you previously worked at {self.company_name}?", req=True, options=[["true", "Yes"], ["false", "No"]], rname="candidateIsPreviousWorker", wrap="formField-candidateIsPreviousWorker"),
            _f("dropdown", "countryDropdown", "Country", key="country", req=True, remote=True, default="United States of America", resets=["addressSection_countryRegion"], wrap="formField-country"),
            _f("text", "legalNameSection_firstName", "First Name", req=True, wrap="formField-legalName--firstName"),
            _f("text", "legalNameSection_middleName", "Middle Name", wrap="formField-legalName--middleName"),
            _f("text", "legalNameSection_lastName", "Last Name", req=True, wrap="formField-legalName--lastName"),
            _f("text", "addressSection_addressLine1", "Address Line 1", req=True, wrap="formField-addressLine1"),
            _f("text", "addressSection_addressLine2", "Address Line 2", wrap="formField-addressLine2"),
            _f("text", "addressSection_city", "City", req=True, wrap="formField-city"),
            _f("dropdown", "addressSection_countryRegion", "State", req=True, remote=True, depends="country", show={"k": "country", "in": list(REGIONS)}, wrap="formField-countryRegion"),
            _f("text", "addressSection_postalCode", "Postal Code", req=True, validate="postal", wrap="formField-postalCode"),
            _f("dropdown", "phone-device-type", "Phone Device Type", req=True, options=PHONE_TYPES, wrap="formField-phone-device-type"),
            _f("dropdown", "country-phone-code", "Country Phone Code", req=True, remote=True, default="United States of America (+1)", wrap="formField-country-phone-code"),
            _f("text", "phone-number", "Phone Number", req=True, validate="phone", wrap="formField-phone-number"),
            _f("text", "phone-extension", "Phone Extension", wrap="formField-phone-extension"),
        ]  # fmt: skip

    def _schema_experience(self) -> list[dict[str, Any]]:
        work = [
            _f("text", "jobTitle", "Job Title", req=True),
            _f("text", "company", "Company", req=True),
            _f("text", "location", "Location"),
            _f("checkbox", "currentlyWorkHere", "I currently work here", rerender=True),
            _f("date", "startDate", "From", req=True, parts="my"),
            _f("date", "endDate", "To", req=True, parts="my", show={"k": "currentlyWorkHere", "not": True}),
            _f("textarea", "roleDescription", "Role Description", max=2000),
        ]  # fmt: skip
        education = [
            _f("prompt", "school", "School or University", req=True, prompt="school", mode="typeahead", multi=False),
            _f("dropdown", "degree", "Degree", req=True, remote=True, ph="Select One", phOption=True),
            _f("prompt", "fieldOfStudy", "Field of Study", prompt="fieldOfStudy", mode="search", multi=True),
            _f("text", "gpa", "Overall Result (GPA)", validate="gpa"),
            _f("date", "firstYearAttended", "First Year Attended", parts="y"),
            _f("date", "lastYearAttended", "Last Year Attended (Actual or Expected)", parts="y"),
        ]  # fmt: skip
        fields = [
            _repeater("workExperienceSection", "workExperience", "Work Experience", work),
            _repeater("educationSection", "education", "Education", education),
            _f("prompt", "skills", "Type to Add Skills", prompt="skills", mode="search", multi=True, section="skillsSection", sectionTitle="Skills", wrap="formField-skills"),
            _f("file", "resume", "Resume/CV", slot="resume", req=self.resume_required, section="resumeSection", sectionTitle="Resume/CV", wrap="formField-resume"),
        ]  # fmt: skip
        if self.cover_letter_slot:
            fields.append(
                _f(
                    "file",
                    "coverLetter",
                    "Cover Letter",
                    slot="coverLetter",
                    section="coverLetterSection",
                    sectionTitle="Cover Letter",
                    wrap="formField-coverLetter",
                )
            )
        fields.append(
            _repeater(
                "websiteSection",
                "websitePanelSet",
                "Website",
                [_f("text", "url", "URL", req=True, validate="url")],
                heading="Websites",
                key="websites",
            )
        )
        fields.append(
            _f(
                "text",
                "linkedinQuestion",
                "LinkedIn Profile",
                validate="url",
                wrap="formField-linkedinQuestion",
            )
        )
        return fields

    def _schema_disclosures(self) -> list[dict[str, Any]]:
        fields = [
            _f("info", "vdIntro", html=(
                "<p>We are an equal opportunity employer. Providing this information is voluntary and "
                "declining to answer will not affect your application.</p>"
            )),
            _f("dropdown", "gender", "Gender", key="gender", req=True, options=GENDERS, ph="Select One", phOption=True),
            _f("dropdown", "ethnicity", "Ethnicity", key="race", req=True, options=ETHNICITIES, ph="Select One", phOption=True),
            _f("dropdown", "veteranStatus", "Veteran Status", key="veteran", req=True, options=VETERAN_STATUSES, ph="Select One", phOption=True),
        ]  # fmt: skip
        if self.require_terms:
            fields.append(
                _f(
                    "checkbox",
                    "agreementCheckbox",
                    "I have read and consent to the terms and conditions of this application.",
                    req=True,
                )
            )
        return fields

    def _schema_self_identify(self) -> list[dict[str, Any]]:
        return [
            _f("info", "cc305Intro", html=(
                "<p><strong>Voluntary Self-Identification of Disability</strong><br>Form CC-305. "
                "Because we do business with the government, we ask you to tell us if you have a disability. "
                "Your answer is voluntary and confidential.</p>"
            )),
            _f("text", "selfIdentifiedDisabilityData--name", "Name", req=True),
            _f("date", "selfIdentifiedDisabilityData--dateSignedOn", "Date", req=True, parts="mdy"),
            _f("radio", "selfIdentifiedDisabilityData--disabilityStatus", "Please check one of the boxes below:", key="disability", req=True, options=DISABILITY_STATUSES, rname="disabilityStatus"),
        ]  # fmt: skip

    # -- validation ---------------------------------------------------------------------------------------------------
    def _allowed(self, f: dict[str, Any], scope: dict[str, Any]) -> list[str]:
        if f.get("remote"):
            dep = str(scope.get(f["depends"], "")) if f.get("depends") else ""
            return self._remote_options(f["id"], dep)
        return [_option_pair(o)[0] for o in f.get("options", [])]

    def _check(
        self,
        fields: list[dict[str, Any]],
        values: dict[str, Any],
        prefix: str,
        errors: list[dict[str, str]],
        draft: Draft,
    ) -> None:
        for f in fields:
            if not _is_input(f) or not _visible(f, values):
                continue
            path = f"{prefix}.{_key(f)}"
            value = values.get(_key(f))
            label = f.get("label") or f.get("title", "")

            def add(message: str, *, path: str = path, label: str = label) -> None:
                errors.append(
                    {"path": path, "label": label, "message": message.format(label=label)}
                )

            t = f["t"]
            required = bool(f.get("req"))
            if t in ("text", "textarea", "dropdown", "radio"):
                text = value if isinstance(value, str) else ""
                if not text:
                    if required:
                        add(REQUIRED_MSG)
                elif f.get("max") and len(text) > f["max"]:
                    add(f"{{label}} must be {f['max']} characters or fewer.")
                elif t in ("dropdown", "radio") and text not in self._allowed(f, values):
                    add("The value selected for {label} is not valid.")
                elif problem := _validate_text(f.get("validate", ""), text, values):
                    add(problem)
            elif t in ("checkbox", "checkboxes", "prompt"):
                if required and not value:
                    add(REQUIRED_MSG)
            elif t == "date":
                if problem := _check_date(f, value or {}):
                    add(problem)
            elif t == "file":
                if required and f["slot"] not in draft.files:
                    add(REQUIRED_MSG)
            elif t == "repeater":
                for i, entry in enumerate(value or []):
                    self._check(f["fields"], entry, f"{path}.{i}", errors, draft)

    def _validate(
        self, step: str, fields: list[dict[str, Any]], values: dict[str, Any], draft: Draft
    ) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        self._check(fields, values, "values", errors, draft)
        if step == "myExperience":
            if self.education_required and not values.get("education"):
                errors.append(
                    {
                        "path": "values.education",
                        "label": "Education",
                        "message": REQUIRED_MSG.format(label="Education"),
                    }
                )
            for i, entry in enumerate(values.get("workExperience") or []):
                start, end = entry.get("startDate") or {}, entry.get("endDate") or {}
                if (
                    not entry.get("currentlyWorkHere")
                    and start.get("y") and end.get("y") and start.get("m") and end.get("m")
                    and (int(end["y"]), int(end["m"])) < (int(start["y"]), int(start["m"]))
                ):  # fmt: skip
                    errors.append({
                        "path": f"values.workExperience.{i}.endDate", "label": "To",
                        "message": "The To date must not be earlier than the From date.",
                    })  # fmt: skip
        return errors

    # -- wizard payload / recording ----------------------------------------------------------------------------------
    def _values_for(self, step: str, draft: Draft, job: MockJob) -> dict[str, Any]:
        fields = self._schema(step, job)
        values = _blank_values(fields)
        saved = draft.steps.get(step) or draft.prefill.get(step) or {}
        values.update({k: copy.deepcopy(v) for k, v in saved.items() if k in values})
        for slot in FILE_SLOTS:
            if slot in values:
                upload = draft.files.get(slot)
                values[slot] = (
                    {"name": upload.filename, "size": len(upload.data)} if upload else None
                )
        return values

    def _wizard_payload(self, draft: Draft, job: MockJob) -> dict[str, Any]:
        """The page frame of the current step; the form itself is fetched separately (``/page``)."""
        ids = self._step_ids(job)
        draft.cursor = max(0, min(draft.cursor, len(ids) - 1))
        current = ids[draft.cursor]
        meta = {sid: (label, aid) for sid, label, aid in STEP_DEFS}
        steps = [
            {
                "id": sid,
                "label": meta[sid][0],
                "state": "active"
                if sid == current
                else ("completed" if sid in draft.steps else "inactive"),
            }
            for sid in ids
        ]
        return {
            "view": "wizard",
            "pending": True,
            "steps": steps,
            "current": current,
            "page": {"aid": meta[current][1], "title": meta[current][0]},
            "nextLabel": "Submit" if current == "review" else "Save and Continue",
            "canGoBack": draft.cursor > 0,
        }

    async def _h_page(self, request: Request, job_id: str) -> Response:
        sess, new, account, job, draft, failure = await self._authed(request, job_id)
        if failure is not None:
            return failure
        assert account is not None and job is not None
        if draft is None or draft.submitted:
            return self._json(sess, new, {"view": self._flow_for(account, job, None)})
        current = self._step_ids(job)[draft.cursor]
        if request.query_params.get("step") != current:
            return self._json(sess, new, {"view": self._wizard_payload(draft, job)})
        payload: dict[str, Any] = {
            "schema": self._schema(current, job),
            "values": self._values_for(current, draft, job),
        }
        if current == "review":
            payload["review"] = self._review(draft, job)
        return self._json(sess, new, payload)

    def _review(self, draft: Draft, job: MockJob) -> list[dict[str, Any]]:
        labels = {sid: label for sid, label, _ in STEP_DEFS}
        sections = []
        for sid in self._step_ids(job):
            if sid == "review" or sid not in draft.steps:
                continue
            rows: list[list[str]] = []
            for f, _rec, value, where in _walk(self._schema(sid, job), draft.steps[sid]):
                if f["t"] == "file":
                    upload = draft.files.get(f["slot"])
                    shown = [upload.filename] if upload else []
                else:
                    shown = _strings(f, value)
                if shown:
                    label = f"{where}: {f['label']}" if where else f["label"]
                    rows.append([label, ", ".join(shown)])
            sections.append({"title": labels[sid], "rows": rows})
        return sections

    def _flatten(self, draft: Draft, job: MockJob) -> dict[str, list[str]]:
        """Recorded submission fields: {record key: [values]} for every visible, non-empty wizard field."""
        out: dict[str, list[str]] = {"email": [draft.email], "job_id": [job.id]}
        for sid in self._step_ids(job):
            if sid == "review":
                continue
            for f, rec, value, _where in _walk(self._schema(sid, job), draft.steps.get(sid, {})):
                if f["t"] != "file" and (strings := _strings(f, value)):
                    out[rec] = strings
        return out


def make_site(
    company: str = "acme", jobs: list[MockJob] | None = None, **options: Any
) -> WorkdaySite:
    """Build a Workday tenant ``<company>.wd5.myworkdayjobs.com``. See the module docstring for the options."""
    return WorkdaySite(company, jobs, **options)


_APP_JS = r"""/* Mock candidate-experience SPA (server-driven wizard, client-held form state). */
(function () {
  'use strict';
  var B = JSON.parse(document.getElementById('wd-bootstrap').textContent);
  var root = document.getElementById('wd-root');
  var busyEl = document.getElementById('wd-busy');
  var S = {
    view: 'boot', job: null, jobs: [], flow: null, notice: null, values: {}, errors: [], ui: {},
    pq: {}, fileErr: {}, busy: 0, saving: false, hint: B.pathHint || null,
    signin: { mode: 'signin', email: '', password: '', verifyPassword: '', consent: false }
  };
  var REG = {}; // field path -> field spec (rebuilt on every render)
  var uid = 0; // element ids are regenerated on every render, like the real thing
  var tokSeq = 0;
  var timers = {};
  var PATH_LABEL = {
    autofillWithResume: 'Autofill with Resume',
    applyManually: 'Apply Manually',
    useMyLastApplication: 'Use My Last Application'
  };

  // ------------------------------------------------------------------------------------ utilities
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function enc(s) { return encodeURIComponent(s); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function A(o) {
    var s = '';
    Object.keys(o).forEach(function (k) {
      var v = o[k];
      if (v === null || v === undefined || v === false) return;
      s += ' ' + k + (v === true ? '' : '="' + esc(v) + '"');
    });
    return s;
  }
  function getPath(o, p) {
    var ks = p.split('.');
    for (var i = 0; i < ks.length; i++) {
      if (o === null || o === undefined) return undefined;
      o = o[ks[i]];
    }
    return o;
  }
  function setPath(o, p, v) {
    var ks = p.split('.');
    for (var i = 0; i < ks.length - 1; i++) o = o[ks[i]];
    o[ks[ks.length - 1]] = v;
  }
  function parentPath(p) { return p.substring(0, p.lastIndexOf('.')); }
  function visible(f, scope) {
    var c = f.show;
    if (!c) return true;
    var v = scope[c.k];
    if (c['in']) return c['in'].indexOf(v) >= 0;
    if (c.not) return !v;
    return !!v;
  }
  function paintBusy() { busyEl.hidden = S.busy === 0; }
  function jobApi(suffix) { return B.app + '/apply/' + enc(B.jobId) + suffix; }

  async function api(method, url, body) {
    S.busy++;
    paintBusy();
    try {
      var init = { method: method, credentials: 'same-origin', headers: {} };
      if (typeof FormData !== 'undefined' && body instanceof FormData) init.body = body;
      else if (body !== undefined) {
        init.body = JSON.stringify(body);
        init.headers['Content-Type'] = 'application/json';
      }
      var res;
      try { res = await fetch(url, init); } catch (e) { return { status: 0, data: null }; }
      var data = null;
      try { data = await res.json(); } catch (e2) { data = null; }
      if (res.status === 401) onExpired(data);
      return { status: res.status, data: data };
    } finally {
      S.busy--;
      paintBusy();
    }
  }
  function techError() {
    S.saving = false;
    S.notice = { kind: 'error', text: 'We are experiencing technical difficulties. Please try again in a few minutes.' };
    render();
  }
  function onExpired(d) {
    S.saving = false;
    S.signin.mode = 'signin';
    S.signin.password = '';
    S.flow = {
      view: 'signin', captcha: !!(d && d.captcha), allowSignup: !d || d.allowSignup !== false
    };
    S.view = 'apply';
    S.notice = (d && d.notice) || null;
    S.ui = {};
    render();
  }

  // ------------------------------------------------------------------------------------ html helpers
  function reqMark() { return '<abbr class="wd-req" title="required" aria-hidden="true">*</abbr>'; }
  function labelHTML(f, forId) {
    return '<label' + A({ 'for': forId, 'class': 'wd-label' }) + '>' + esc(f.label) + (f.req ? reqMark() : '') + '</label>';
  }
  function errHTML(msg, id) {
    return '<div' + A({ 'data-automation-id': 'errorMessage', id: id, role: 'alert', 'class': 'wd-err' }) +
      '><span class="wd-sr">Error: </span>' + esc(msg) + '</div>';
  }
  function wrapHTML(f, inner, err, cls) {
    return '<div' + A({
      'data-automation-id': f.wrap || ('formField-' + f.id),
      'class': 'wd-field' + (err ? ' wd-invalid' : '') + (cls ? ' ' + cls : '')
    }) + '>' + inner + (err ? errHTML(err) : '') + '</div>';
  }
  function ctlId(f, ctx) { return f.hid || (ctx.gid ? ctx.gid + '--' + f.id : 'input-' + (++uid)); }
  function noticeHTML() {
    var n = S.notice;
    if (!n) return '';
    return '<div' + A({
      'data-automation-id': n.aid || (n.kind === 'error' ? 'errorMessage' : 'infoMessage'),
      role: n.kind === 'error' ? 'alert' : 'status',
      'class': 'wd-banner wd-banner-' + n.kind
    }) + '>' + esc(n.text) + '</div>';
  }

  // ------------------------------------------------------------------------------------ form fields
  function errIndex() {
    var m = {};
    S.errors.forEach(function (e) { m[e.path] = e.message; });
    return m;
  }

  function fieldsHTML(fields, sp, scope, ctx) {
    return fields.filter(function (f) { return visible(f, scope); }).map(function (f) {
      return fieldHTML(f, sp, scope, ctx);
    }).join('');
  }

  function fieldHTML(f, sp, scope, ctx) {
    var key = f.key || f.id;
    var path = sp + '.' + key;
    var val = scope[key];
    var err = ctx.errs[path];
    var id, inner, out;
    REG[path] = f;
    switch (f.t) {
      case 'info': return '<div class="wd-info">' + f.html + '</div>';
      case 'heading': return '<h3 class="wd-h3">' + esc(f.label) + '</h3>';
      case 'text':
      case 'textarea':
        id = ctlId(f, ctx);
        var common = {
          id: id, name: f.name || f.id, 'data-automation-id': f.id, 'data-fk': path,
          'aria-required': f.req ? 'true' : null, 'aria-invalid': err ? 'true' : null,
          'aria-describedby': err ? id + '-err' : null, maxlength: f.max || null,
          placeholder: f.ph || null, autocomplete: 'off', 'class': 'wd-input'
        };
        inner = labelHTML(f, id) + (f.t === 'textarea'
          ? '<textarea' + A(common) + ' rows="4">' + esc(val || '') + '</textarea>'
          : '<input' + A(Object.assign({ type: f.type || 'text', value: val || '' }, common)) + '>');
        out = wrapHTML(f, inner, err);
        break;
      case 'dropdown':
        id = ctlId(f, ctx);
        var open = !!(S.ui.dd && S.ui.dd.fk === path);
        var shown = val || f.ph || 'Select One';
        inner = labelHTML(f, id) + '<div class="wd-anchor"><button' + A({
          type: 'button', id: id, name: f.id, 'data-automation-id': f.id, 'data-fk': path, 'data-act': 'dd',
          'data-uxi-widget-type': 'selectinput', 'aria-haspopup': 'listbox', 'aria-expanded': open ? 'true' : 'false',
          'aria-required': f.req ? 'true' : null, 'aria-invalid': err ? 'true' : null,
          'aria-label': f.label + ' ' + shown + (f.req ? ' Required' : ''), 'class': 'wd-dd'
        }) + '>' + esc(shown) + '</button>' + (open ? ddPopup(f, val) : '') + '</div>';
        out = wrapHTML(f, inner, err);
        break;
      case 'radio':
        var rid = 'input-' + (++uid);
        var rname = f.rname || ('radio-' + rid);
        inner = '<fieldset' + A({ role: 'radiogroup', 'aria-labelledby': rid + '-legend', 'aria-required': f.req ? 'true' : null, 'class': 'wd-fieldset' }) +
          '><legend id="' + rid + '-legend" class="wd-label">' + esc(f.label) + (f.req ? reqMark() : '') + '</legend>' +
          (f.options || []).map(function (o) {
            var pair = typeof o === 'string' ? [o, o] : o;
            var oid = 'input-' + (++uid);
            return '<div class="wd-radio"><input' + A({
              type: 'radio', id: oid, name: rname, value: pair[0], 'data-automation-id': f.id, 'data-fk': path,
              checked: val === pair[0] ? true : null, 'aria-invalid': err ? 'true' : null
            }) + '><span class="wd-fake wd-fake-radio" aria-hidden="true"></span><label for="' + oid + '">' + esc(pair[1]) + '</label></div>';
          }).join('') + '</fieldset>';
        out = wrapHTML(f, inner, err);
        break;
      case 'checkbox':
        id = ctlId(f, ctx);
        inner = '<div class="wd-check"><input' + A({
          type: 'checkbox', id: id, name: f.id, 'data-automation-id': f.id, 'data-fk': path, checked: val ? true : null,
          'aria-required': f.req ? 'true' : null, 'aria-invalid': err ? 'true' : null
        }) + '><span class="wd-fake wd-fake-check" aria-hidden="true"></span><label for="' + id + '">' + esc(f.label) + (f.req ? reqMark() : '') + '</label></div>';
        out = wrapHTML(f, inner, err);
        break;
      case 'checkboxes':
        var gid0 = 'input-' + (++uid);
        inner = '<fieldset' + A({ 'aria-labelledby': gid0 + '-legend', 'class': 'wd-fieldset' }) + '><legend id="' + gid0 + '-legend" class="wd-label">' +
          esc(f.label) + (f.req ? reqMark() : '') + '</legend>' + (f.options || []).map(function (o) {
            var oid = 'input-' + (++uid);
            return '<div class="wd-check"><input' + A({
              type: 'checkbox', id: oid, name: f.id, value: o, 'data-automation-id': f.id, 'data-fk': path,
              checked: (val || []).indexOf(o) >= 0 ? true : null
            }) + '><span class="wd-fake wd-fake-check" aria-hidden="true"></span><label for="' + oid + '">' + esc(o) + '</label></div>';
          }).join('') + '</fieldset>';
        out = wrapHTML(f, inner, err);
        break;
      case 'prompt':
        id = f.hid || ctlId(f, ctx);
        var pr = S.ui.prompt && S.ui.prompt.fk === path;
        var sel = val || [];
        var pills = sel.length ? '<ul data-automation-id="selectedItemList" class="wd-pills">' + sel.map(function (o, i) {
          return '<li data-automation-id="selectedItem" class="wd-pill"><span class="wd-pill-text">' + esc(o.label) + '</span><span' + A({
            role: 'button', tabindex: '0', 'aria-label': 'Delete ' + o.label, 'data-automation-id': 'DELETE_charm',
            'data-act': 'prompt-del', 'data-fk': path, 'data-idx': String(i), 'class': 'wd-pill-x'
          }) + '>&times;</span></li>';
        }).join('') + '</ul>' : '';
        inner = labelHTML(f, id) + '<div data-automation-id="multiSelectContainer" class="wd-multi wd-anchor"><div data-automation-id="multiselectInputContainer" class="wd-multi-in">' +
          pills + '<input' + A({
            type: 'text', id: id, role: 'combobox', 'aria-expanded': pr ? 'true' : 'false', 'aria-haspopup': 'listbox',
            'aria-autocomplete': 'list', 'data-automation-id': f.id, 'data-fk': path, 'data-prompt': '1', 'data-uxi-widget-type': 'selectinput',
            placeholder: f.ph || 'Search', value: S.pq[path] || '', autocomplete: 'off',
            'aria-required': f.req ? 'true' : null, 'aria-invalid': err ? 'true' : null, 'class': 'wd-input wd-prompt-input'
          }) + '></div>' + (pr ? promptPopup(f, path) : '') + '</div>';
        out = wrapHTML(f, inner, err);
        break;
      case 'date':
        out = dateHTML(f, path, val || {}, err, ctx);
        break;
      case 'file':
        out = fileHTML(f, path, val, err, ctx);
        break;
      case 'repeater':
        out = repeaterHTML(f, path, val || [], err, ctx);
        break;
      default:
        return '';
    }
    if (f.section) {
      out = '<section' + A({ 'data-automation-id': f.section, role: 'group', 'class': 'wd-section' }) + '><h3 class="wd-h3">' +
        esc(f.sectionTitle || f.label) + '</h3>' + out + '</section>';
    }
    return out;
  }

  function ddAll(f, d) {
    var opts = d.opts || [];
    return f.phOption ? [''].concat(opts) : opts;
  }
  function ddPopup(f, val) {
    var d = S.ui.dd;
    var items;
    if (d.loading) {
      items = '<li class="wd-loading" role="presentation" data-automation-id="loadingText">Loading...</li>';
    } else {
      var all = ddAll(f, d);
      items = all.length ? all.map(function (o, i) {
        var label = o === '' ? (f.ph || 'Select One') : o;
        return '<li' + A({
          role: 'option', id: 'option-' + (++uid), 'aria-selected': o === (val || '') ? 'true' : 'false',
          'data-act': 'dd-pick', 'data-val': o, tabindex: '-1', 'class': 'wd-opt' + (i === d.active ? ' wd-active' : '')
        }) + '><div' + A({ 'data-automation-id': 'menuItem', 'data-automation-label': label }) + '>' + esc(label) + '</div></li>';
      }).join('') : '<li class="wd-noitems" role="presentation">No Items.</li>';
    }
    return '<div data-automation-widget="wd-popup" data-automation-id="activeListContainer" class="wd-popup"><ul' +
      A({ role: 'listbox', tabindex: '-1', 'aria-label': f.label, 'class': 'wd-list' }) + '>' + items + '</ul></div>';
  }

  function promptPopup(f, path) {
    var p = S.ui.prompt;
    var crumb = p.trail.length ? '<div class="wd-crumb"><button type="button" data-act="prompt-back" data-fk="' + esc(path) +
      '" class="wd-linkbtn">&lsaquo; ' + esc(p.trail[p.trail.length - 1].label) + '</button></div>' : '';
    var body;
    if (p.loading) body = '<div class="wd-loading" data-automation-id="loadingText">Loading...</div>';
    else if (!p.options.length) body = '<div class="wd-noitems">No Items.</div>';
    else {
      var cur = getPath(S, path) || [];
      body = '<ul role="listbox" tabindex="-1" aria-label="' + esc(f.label) + '" class="wd-list">' + p.options.map(function (o, i) {
        var chosen = cur.some(function (c) { return c.id === o.id; });
        return '<li' + A({
          role: 'option', id: 'option-' + (++uid), 'aria-selected': chosen ? 'true' : 'false', 'data-act': 'prompt-pick',
          'data-fk': path, 'data-oid': o.id, tabindex: '-1', 'class': 'wd-opt' + (i === p.active ? ' wd-active' : '')
        }) + '><div' + A({ 'data-automation-id': 'promptOption', 'data-automation-label': o.label, 'class': 'wd-promptopt' }) + '>' +
          esc(o.label) + (o.folder ? '<span class="wd-arrow" aria-hidden="true">&rsaquo;</span>' : '') + '</div></li>';
      }).join('') + '</ul>';
    }
    return '<div data-automation-widget="wd-popup" data-automation-id="activeListContainer" class="wd-popup">' + crumb + body + '</div>';
  }

  function dateHTML(f, path, val, err, ctx) {
    var parts = (f.parts || 'my').split('');
    var base = ctx.gid ? ctx.gid + '--' + f.id : 'date-' + (++uid);
    var names = { m: 'Month', d: 'Day', y: 'Year' };
    var phs = { m: 'MM', d: 'DD', y: 'YYYY' };
    var inputs = parts.map(function (c) {
      return '<input' + A({
        type: 'text', id: base + '-dateSection' + names[c] + '-input', inputmode: 'numeric', role: 'spinbutton',
        'aria-label': names[c], placeholder: phs[c], maxlength: c === 'y' ? '4' : '2', autocomplete: 'off',
        'data-automation-id': f.id + '-dateSection' + names[c] + '-input', 'data-fk': path + '.' + c, 'data-date': c,
        value: val[c] || '', 'aria-invalid': err ? 'true' : null, 'aria-required': f.req ? 'true' : null,
        'class': 'wd-input wd-date wd-date-' + c
      }) + '>';
    }).join('<span class="wd-datesep" aria-hidden="true">/</span>');
    var first = base + '-dateSection' + names[parts[0]] + '-input';
    var inner = '<label' + A({ 'for': first, id: base + '-label', 'class': 'wd-label' }) + '>' + esc(f.label) + (f.req ? reqMark() : '') + '</label>' +
      '<div data-automation-id="dateInputWrapper" role="group" aria-labelledby="' + base + '-label" class="wd-daterow">' + inputs + '</div>';
    return wrapHTML(f, inner, err);
  }

  function fileHTML(f, path, val, err, ctx) {
    var id = 'input-' + (++uid);
    var fe = S.fileErr[f.slot];
    var inner;
    if (val) {
      inner = '<div data-automation-id="file-upload-successful" class="wd-file-ok"><span class="wd-file-ic" aria-hidden="true">&#10003;</span>' +
        '<div class="wd-file-meta"><div data-automation-id="file-upload-item-name" class="wd-file-name">' + esc(val.name) + '</div>' +
        '<div class="wd-muted">Successfully Uploaded!</div></div><button' + A({
          type: 'button', 'data-automation-id': 'delete-file', 'data-act': 'del-file', 'data-slot': f.slot,
          'aria-label': 'Delete ' + val.name, 'class': 'wd-linkbtn'
        }) + '>Delete</button></div>';
    } else {
      inner = '<div data-automation-id="file-upload-drop-zone" class="wd-drop"><span>Drop file here</span><span class="wd-muted">or</span>' +
        '<button' + A({ type: 'button', 'data-automation-id': 'select-files', 'data-act': 'select-files', 'class': 'wd-btn wd-btn-secondary' }) + '>Select file</button></div>' +
        '<input' + A({
          type: 'file', id: id, 'data-automation-id': 'file-upload-input-ref', 'data-slot': f.slot, 'aria-label': f.label,
          accept: '.pdf,.doc,.docx,.txt,.rtf', style: 'display:none', tabindex: '-1'
        }) + '>';
    }
    return wrapHTML(f, inner, fe || err);
  }

  function repeaterHTML(f, path, arr, err, ctx) {
    var groups = arr.map(function (entry, i) {
      var gid = f.group + '-' + (i + 1);
      return '<div' + A({ 'data-automation-id': gid, role: 'group', 'aria-label': f.title + ' ' + (i + 1), 'class': 'wd-panel' }) +
        '><div class="wd-panel-head"><h4 class="wd-h4">' + esc(f.title) + ' ' + (i + 1) + '</h4><button' + A({
          type: 'button', 'data-automation-id': 'panel-set-delete-button', 'data-act': 'del', 'data-fk': path, 'data-idx': String(i),
          'aria-label': 'Delete ' + f.title + ' ' + (i + 1), 'class': 'wd-linkbtn'
        }) + '>Delete</button></div>' + fieldsHTML(f.fields, path + '.' + i, entry, { errs: ctx.errs, gid: gid }) + '</div>';
    }).join('');
    return '<section' + A({ 'data-automation-id': f.id, role: 'group', 'aria-label': f.title, 'class': 'wd-section' }) + '><h3 class="wd-h3">' + esc(f.heading || f.title) + '</h3>' +
      groups + '<button' + A({
        type: 'button', 'data-automation-id': 'add-button', 'data-act': 'add', 'data-fk': path, 'aria-label': 'Add ' + f.title,
        'class': 'wd-btn wd-btn-secondary'
      }) + '>' + (arr.length ? 'Add Another' : 'Add') + '</button>' + (err ? errHTML(err) : '') + '</section>';
  }

  // ------------------------------------------------------------------------------------ views
  function cookieHTML() {
    if (!B.cookieBanner || S.ui.cookieOk) return '';
    try { if (window.localStorage.getItem('wd-cookie-ack')) return ''; } catch (e) { /* storage blocked: keep showing */ }
    return '<div role="region" aria-label="Cookie notice" data-automation-id="legalNotice" class="wd-cookie"><p>We use cookies to improve your experience on this site.</p>' +
      '<button type="button" data-automation-id="legalNoticeAcceptButton" data-act="cookie-accept" class="wd-btn wd-btn-primary">Accept Cookies</button></div>';
  }
  function shell(inner, extra, notice) {
    return '<div class="wd-shell' + (cookieHTML() ? ' wd-has-cookie' : '') + '"><header class="wd-header"><a class="wd-brand" href="' + esc(B.listPath) + '">' + esc(B.company) +
      ' Careers</a></header><main class="wd-main">' + (notice ? noticeHTML() : '') + inner + '</main>' + (extra || '') + cookieHTML() + '</div>';
  }

  function listHTML() {
    return '<div data-automation-id="jobSearchPage"><h2 class="wd-h2">Search for Jobs</h2><ul class="wd-joblist">' + S.jobs.map(function (j) {
      return '<li class="wd-jobitem"><h3 class="wd-h3"><a' + A({ href: j.externalPath, 'data-automation-id': 'jobTitle' }) + '>' + esc(j.title) +
        '</a></h3><div class="wd-muted">' + esc(j.locationsText) + ' &middot; ' + esc(j.postedOn) + '</div></li>';
    }).join('') + '</ul></div>';
  }
  function notFoundHTML() {
    return '<div data-automation-id="pageNotFound" class="wd-page"><h2 class="wd-h2">The page you are looking for doesn\'t exist.</h2><p><a href="' +
      esc(B.listPath) + '">Search for Jobs</a></p></div>';
  }

  function jobHTML() {
    var j = S.job;
    return '<div data-automation-id="jobPostingPage" class="wd-page"><div data-automation-id="jobPostingHeaderRow" class="wd-jobhead">' +
      '<h2 data-automation-id="jobPostingHeader" class="wd-h2">' + esc(j.title) + '</h2><a' + A({
        href: j.applyUrl, 'data-automation-id': 'adventureButton', 'data-uxi-widget-type': 'action', 'data-act': 'apply', 'class': 'wd-btn wd-btn-primary'
      }) + '>Apply</a></div><dl class="wd-facts"><div><dt>locations</dt><dd data-automation-id="locations">' + esc(j.location) +
      '</dd></div><div><dt>time type</dt><dd data-automation-id="time">' + esc(j.timeType) + '</dd></div><div><dt>posted on</dt><dd data-automation-id="postedOn">' +
      esc(j.posted) + '</dd></div><div><dt>job requisition id</dt><dd data-automation-id="requisitionId">' + esc(j.reqId) + '</dd></div></dl>' +
      '<div data-automation-id="jobPostingDescription" class="wd-desc">' + j.descriptionHtml + '</div></div>';
  }

  function applyUrl(path) { return B.jobPath + '/apply' + (path ? '/' + path : ''); }

  function chooserHTML() {
    return '<h2 id="wd-modal-title" class="wd-modal-title" tabindex="-1">Start Your Application</h2>' + noticeHTML() + '<div class="wd-choices">' +
      S.flow.options.map(function (o) {
        return '<a' + A({
          role: 'button', href: applyUrl(o), 'data-automation-id': o, 'data-act': 'choose', 'data-path': o,
          'class': 'wd-btn wd-btn-block ' + (o === 'applyManually' ? 'wd-btn-primary' : 'wd-btn-secondary')
        }) + '>' + PATH_LABEL[o] + '</a>';
      }).join('') + '</div>';
  }

  function authInput(aid, label, path, type, ac) {
    var id = 'input-' + (++uid);
    return '<div' + A({ 'data-automation-id': 'formField-' + aid, 'class': 'wd-field' }) + '><label' + A({ 'for': id, 'class': 'wd-label' }) + '>' + esc(label) + reqMark() +
      '</label><input' + A({
        id: id, type: type, name: aid, 'data-automation-id': aid, 'data-fk': path, value: getPath(S, path) || '',
        autocomplete: ac, 'aria-required': 'true', 'class': 'wd-input'
      }) + '></div>';
  }
  function overlayBtn(aid, label, act) {
    // The real widget: a div[role=button] with a transparent click_filter div stacked on top that receives the pointer events.
    return '<div class="wd-btnwrap"><div' + A({
      role: 'button', tabindex: '0', 'aria-label': label, 'data-automation-id': aid, 'data-key-act': act, 'class': 'wd-btn wd-btn-primary wd-btn-block'
    }) + '>' + esc(label) + '</div><div' + A({
      role: 'button', tabindex: '0', 'aria-label': label, 'data-automation-id': 'click_filter', 'data-act': act, 'class': 'wd-filter'
    }) + '></div></div>';
  }
  function captchaHTML() {
    return '<div class="wd-captcha" data-automation-id="captchaContainer"><p class="wd-captcha-text">Verify you are human</p>' +
      '<iframe title="reCAPTCHA" src="/captcha/recaptcha/api2/anchor?ar=1&amp;k=mock-site-key" width="304" height="78" frameborder="0"></iframe></div>';
  }
  function authHTML() {
    var m = S.signin.mode, f = S.flow, cap = f.captcha ? captchaHTML() : '';
    if (m === 'create') {
      return '<div data-automation-id="createAccountContent" class="wd-auth"><h2 id="wd-modal-title" class="wd-modal-title" tabindex="-1">Create Account</h2>' +
        noticeHTML() + '<form novalidate>' + authInput('email', 'Email Address', 'signin.email', 'text', 'username') +
        authInput('password', 'Password', 'signin.password', 'password', 'new-password') +
        authInput('verifyPassword', 'Verify New Password', 'signin.verifyPassword', 'password', 'new-password') +
        (B.requireConsent ? '<div class="wd-check wd-consent"><input' + A({
          type: 'checkbox', id: 'input-' + (++uid), name: 'createAccountCheckbox', 'data-automation-id': 'createAccountCheckbox', 'data-fk': 'signin.consent',
          checked: S.signin.consent ? true : null, 'aria-required': 'true'
        }) + '><span class="wd-fake wd-fake-check" aria-hidden="true"></span><label for="input-' + uid + '">I have read and agree to the ' + esc(B.company) +
          ' Candidate Privacy Notice and the Terms and Conditions.' + reqMark() + '</label></div>' : '') +
        cap + overlayBtn('createAccountSubmitButton', 'Create Account', 'create') + '</form><div class="wd-auth-links"><span>Already have an account? </span>' +
        '<button type="button" data-automation-id="signInLink" data-act="to-signin" class="wd-linkbtn">Sign In</button></div></div>';
    }
    if (m === 'forgot') {
      return '<div data-automation-id="forgotPasswordContent" class="wd-auth"><h2 id="wd-modal-title" class="wd-modal-title" tabindex="-1">Forgot Password</h2>' +
        noticeHTML() + '<p>Enter your email address and we will send you a link to reset your password.</p><form novalidate>' +
        authInput('email', 'Email Address', 'signin.email', 'text', 'username') +
        '<button type="button" data-automation-id="forgotPasswordSubmitButton" data-act="forgot" class="wd-btn wd-btn-primary wd-btn-block">Submit</button></form>' +
        '<div class="wd-auth-links"><button type="button" data-automation-id="signInLink" data-act="to-signin" class="wd-linkbtn">Back to Sign In</button></div></div>';
    }
    return '<div data-automation-id="signInContent" class="wd-auth"><h2 id="wd-modal-title" class="wd-modal-title" tabindex="-1">Sign In</h2>' + noticeHTML() +
      '<form novalidate>' + authInput('email', 'Email Address', 'signin.email', 'text', 'username') +
      authInput('password', 'Password', 'signin.password', 'password', 'current-password') + cap +
      overlayBtn('signInSubmitButton', 'Sign In', 'signin') + '</form><div class="wd-auth-links">' +
      (f.allowSignup ? '<span>Don\'t have an account? </span><button type="button" data-automation-id="createAccountLink" data-act="to-create" class="wd-linkbtn">Create Account</button>' : '') +
      '<button type="button" data-automation-id="forgotPasswordLink" data-act="to-forgot" class="wd-linkbtn">Forgot your password?</button></div></div>';
  }
  function modalHTML() {
    var f = S.flow, inner;
    if (f.view === 'chooser') inner = chooserHTML();
    else if (f.view === 'signin') inner = authHTML();
    else return '';
    return '<div class="wd-backdrop"><div' + A({ role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'wd-modal-title', 'class': 'wd-modal' }) + '>' + inner + '</div></div>';
  }

  function progressHTML() {
    var st = S.flow.steps, n = st.length;
    return '<nav data-automation-id="progressBar" aria-label="Application progress" class="wd-progress"><ol>' + st.map(function (s, i) {
      var aid = s.state === 'active' ? 'progressBarActiveStep' : (s.state === 'completed' ? 'progressBarCompletedStep' : 'progressBarInactiveStep');
      var word = s.state === 'active' ? 'current step' : (s.state === 'completed' ? 'completed step' : 'step');
      return '<li' + A({ 'data-automation-id': aid, 'aria-current': s.state === 'active' ? 'step' : null, 'class': 'wd-step wd-step-' + s.state }) +
        '><span class="wd-step-idx" aria-hidden="true">' + (s.state === 'completed' ? '&#10003;' : (i + 1)) + '</span><span class="wd-sr">' + word + ' ' + (i + 1) +
        ' of ' + n + '</span><span class="wd-step-label">' + esc(s.label) + '</span></li>';
    }).join('') + '</ol></nav>';
  }
  function bannerHTML() {
    if (!S.errors.length) return '';
    return '<div' + A({ 'data-automation-id': 'errorBanner', role: 'alert', tabindex: '-1', 'class': 'wd-banner wd-banner-error' }) +
      '><h3 class="wd-banner-title">Errors Found</h3><ul>' + S.errors.map(function (e) {
        return '<li><button' + A({ type: 'button', 'data-act': 'err-jump', 'data-fk': e.path, 'class': 'wd-linkbtn' }) + '>Error - ' + esc(e.message) + '</button></li>';
      }).join('') + '</ul></div>';
  }
  function reviewHTML() {
    return S.flow.review.map(function (sec) {
      return '<section data-automation-id="reviewSection" class="wd-section"><h3 class="wd-h3">' + esc(sec.title) + '</h3><dl class="wd-review">' +
        sec.rows.map(function (r) {
          return '<div data-automation-id="reviewRow" class="wd-review-row"><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>';
        }).join('') + '</dl></section>';
    }).join('');
  }
  function wizardHTML() {
    var fl = S.flow;
    REG = {};
    var ctx = { errs: errIndex(), gid: null };
    var head = progressHTML() + '<div' + A({ 'data-automation-id': fl.page.aid, 'class': 'wd-page' }) + '><h2 tabindex="-1" class="wd-h2">' + esc(fl.page.title) + '</h2>';
    if (fl.pending) return head + noticeHTML() + '<div class="wd-loading-block" role="status" aria-live="polite">Loading...</div></div>';
    var body = fl.current === 'review' ? reviewHTML() : fieldsHTML(fl.schema, 'values', S.values, ctx);
    var note = fl.current === 'review' ? '' : '<p class="wd-reqnote">* Indicates a required field</p>';
    return head +
      note + noticeHTML() + bannerHTML() + body + '<div data-automation-id="bottom-navigation-footer" class="wd-footer">' +
      (fl.canGoBack ? '<button type="button" data-automation-id="bottom-navigation-back-button" data-act="back" class="wd-btn wd-btn-secondary">Back</button>' : '<span></span>') +
      '<button' + A({
        type: 'button', 'data-automation-id': 'bottom-navigation-next-button', 'data-act': 'next', disabled: S.saving ? true : null, 'class': 'wd-btn wd-btn-primary'
      }) + '>' + esc(fl.nextLabel) + '</button></div>' + (S.saving ? '<div class="wd-blocker" aria-hidden="true"></div>' : '') + '</div>';
  }
  function confirmationHTML() {
    var f = S.flow;
    return '<div data-automation-id="applicationSubmittedPage" class="wd-page wd-confirm"><h2 tabindex="-1" class="wd-h2">Application Submitted</h2>' +
      '<p data-automation-id="applicationSubmittedMessage">Congratulations! Your application for <strong>' + esc(f.title) + '</strong> (' + esc(f.reqId) +
      ') has been submitted.</p><p>Thank you for applying to ' + esc(f.company) + '. We will contact you if your qualifications match our needs.</p>' +
      '<p><a href="' + esc(B.listPath) + '">Search for more jobs</a></p></div>';
  }
  function alreadyHTML() {
    var f = S.flow;
    return '<div data-automation-id="alreadyApplied" class="wd-page"><h2 tabindex="-1" class="wd-h2">Already Applied</h2>' +
      '<p>You have already applied for this job (' + esc(f.reqId) + '). You can only submit one application per job posting.</p>' +
      '<p><a href="' + esc(B.listPath) + '">Search for more jobs</a></p></div>';
  }

  function currentHTML() {
    if (S.view === 'list') return shell(listHTML(), '', true);
    if (S.view === 'notfound') return shell(notFoundHTML());
    var f = S.flow;
    if (S.view === 'apply' && f) {
      if (f.view === 'wizard') return shell(wizardHTML());
      if (f.view === 'confirmation') return shell(confirmationHTML());
      if (f.view === 'alreadyApplied') return shell(alreadyHTML());
    }
    var job = S.job ? jobHTML() : '<div class="wd-page wd-muted" data-automation-id="loadingPage">Loading...</div>';
    return shell(job, S.view === 'apply' && f ? modalHTML() : '', !(S.view === 'apply' && f));
  }

  // ------------------------------------------------------------------------------------ rendering
  function captureFocus() {
    var a = document.activeElement;
    if (!a || a === document.body || !root.contains(a)) return null;
    return {
      aid: a.getAttribute('data-automation-id'), fk: a.getAttribute('data-fk'), value: a.getAttribute('value'),
      s: typeof a.selectionStart === 'number' ? a.selectionStart : null,
      e: typeof a.selectionEnd === 'number' ? a.selectionEnd : null
    };
  }
  function restoreFocus(want) {
    if (!want) return;
    var sel = '';
    if (want.aid) sel += '[data-automation-id="' + CSS.escape(want.aid) + '"]';
    if (want.fk) sel += '[data-fk="' + CSS.escape(want.fk) + '"]';
    if (!sel) return;
    var el = root.querySelector(sel);
    if (!el) return;
    try { el.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
    if (want.s !== null && el.setSelectionRange) {
      try { el.setSelectionRange(want.s, want.e); } catch (e2) { /* ignore */ }
    }
  }
  // React ignores an `input` event when the DOM value equals the value it last saw, and it learns about values
  // assigned through the element's own `value` property. So `el.value = 'x'` (+ a synthetic event) never registers;
  // typing / fill() does, and so does the native prototype setter followed by an `input` event.
  var nativeInputValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  var nativeAreaValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  function trackInputs() {
    Array.prototype.forEach.call(root.querySelectorAll('input[data-fk],textarea[data-fk]'), function (el) {
      if (el.type === 'checkbox' || el.type === 'radio' || el.type === 'file') return;
      var desc = el.tagName === 'TEXTAREA' ? nativeAreaValue : nativeInputValue;
      el.__tracked = desc.get.call(el);
      Object.defineProperty(el, 'value', {
        configurable: true,
        get: function () { return desc.get.call(this); },
        set: function (v) { this.__tracked = String(v); desc.set.call(this, v); }
      });
    });
  }
  function render() {
    var y = window.scrollY;
    var want = S.ui.focus || captureFocus();
    S.ui.focus = null;
    REG = {};
    root.innerHTML = currentHTML();
    trackInputs();
    restoreFocus(want);
    var active = root.querySelector('.wd-active');
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
    window.scrollTo(0, y);
  }
  function afterStep() {
    window.scrollTo(0, 0);
    var h = root.querySelector('h2[tabindex="-1"]');
    if (h) h.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------------------------ flow
  function setFlow(d) {
    S.flow = d;
    S.errors = [];
    S.ui = {};
    S.pq = {};
    S.fileErr = {};
    S.saving = false;
    S.notice = d.notice || null;
    if (d.view === 'wizard') S.values = d.values ? clone(d.values) : {};
    if (d.view === 'signin') { S.signin.password = ''; S.signin.verifyPassword = ''; }
    render();
    afterStep();
    if (d.view === 'wizard' && d.pending) loadPage(d.current);
  }
  async function loadPage(step) {
    // The page frame (progress bar, heading) is painted first; the form itself arrives with a second request.
    var my = ++tokSeq;
    S.ui.pageTok = my;
    var r = await api('GET', jobApi('/page?step=' + enc(step)));
    if (r.status === 401 || S.ui.pageTok !== my) return;
    if (r.data && r.data.view) { setFlow(r.data.view); return; }
    if (!r.data || !r.data.schema) return techError();
    S.flow.schema = r.data.schema;
    S.flow.review = r.data.review;
    S.flow.pending = false;
    S.values = clone(r.data.values);
    render();
  }
  async function loadFlow(hint) {
    var r = await api('POST', jobApi('/state'), { path: hint || null, verified: !!B.verified });
    if (r.status === 401) return;
    if (r.status === 404) { S.view = 'notfound'; render(); return; }
    if (!r.data || !r.data.view) return techError();
    setFlow(r.data);
  }
  async function loadJob() {
    var r = await api('GET', B.cxs + '/job/' + B.jobRest);
    if (r.status === 404) { S.view = 'notfound'; render(); return false; }
    if (!r.data || !r.data.jobPostingInfo) { techError(); return false; }
    var i = r.data.jobPostingInfo;
    S.job = {
      title: i.title, location: i.location, timeType: i.timeType, posted: i.postedOn, reqId: i.jobReqId,
      descriptionHtml: i.jobDescription, applyUrl: applyUrl('')
    };
    render();
    return true;
  }
  async function loadList() {
    var r = await api('POST', B.cxs + '/jobs', { appliedFacets: {}, limit: 20, offset: 0, searchText: '' });
    if (!r.data) return techError();
    S.jobs = r.data.jobPostings.map(function (p) {
      return { title: p.title, locationsText: p.locationsText, postedOn: p.postedOn, externalPath: '/' + B.locale + '/' + B.site + p.externalPath };
    });
    render();
  }
  async function boot() {
    if (B.mode === 'list') { S.view = 'list'; render(); await loadList(); return; }
    S.view = 'job';
    render();
    if (!(await loadJob())) return;
    if (B.mode === 'apply') {
      S.view = 'apply';
      await loadFlow(B.pathHint || null);
    }
  }

  async function onApply() {
    S.hint = null;
    history.pushState({}, '', applyUrl(''));
    S.view = 'apply';
    S.notice = null;
    await loadFlow(null);
  }
  async function onChoose(path) {
    S.hint = path;
    history.pushState({}, '', applyUrl(path));
    await loadFlow(path);
  }
  async function doSignIn() {
    var r = await api('POST', B.app + '/auth/sign-in', { email: S.signin.email, password: S.signin.password });
    if (r.status === 0 || r.status >= 500) return techError();
    if (r.data && r.data.ok) { S.signin.password = ''; await loadFlow(S.hint); return; }
    S.notice = { kind: 'error', text: (r.data && r.data.error) || 'Sign in failed.' };
    render();
  }
  async function doCreate() {
    var s = S.signin;
    var r = await api('POST', B.app + '/auth/create-account', {
      email: s.email, password: s.password, verifyPassword: s.verifyPassword, consent: !!s.consent, returnTo: location.pathname
    });
    if (r.status === 0 || r.status >= 500) return techError();
    if (r.data && r.data.ok && r.data.verify) {
      s.mode = 'signin'; s.password = ''; s.verifyPassword = ''; s.consent = false;
      S.notice = { kind: 'info', aid: 'verifyEmailNotice', text: 'Your account has been created. We have sent a verification email to ' + s.email + '. Verify your email address, then sign in to continue your application.' };
      render();
      return;
    }
    if (r.data && r.data.ok) { s.password = ''; s.verifyPassword = ''; await loadFlow(S.hint); return; }
    S.notice = { kind: 'error', text: (r.data && r.data.error) || 'Unable to create the account.' };
    render();
  }
  async function doForgot() {
    var r = await api('POST', B.app + '/auth/forgot-password', { email: S.signin.email });
    if (r.status === 0 || r.status >= 500) return techError();
    S.notice = { kind: 'info', aid: 'forgotPasswordNotice', text: 'If an account exists for that email address, we have sent you a link to reset your password.' };
    render();
  }
  async function doNext() {
    if (S.saving) return;
    var step = S.flow.current;
    S.saving = true;
    render();
    var r = await api('POST', jobApi(step === 'review' ? '/submit' : '/save'), { step: step, values: S.values });
    if (r.status === 401) return;
    S.saving = false;
    if (r.data && r.data.ok) { setFlow(r.data.view); return; }
    if (r.data && r.data.errors) {
      S.errors = r.data.errors;
      S.notice = null;
      render();
      window.scrollTo(0, 0);
      var b = root.querySelector('[data-automation-id="errorBanner"]');
      if (b) b.focus({ preventScroll: true });
      return;
    }
    if (r.data && r.data.view) { setFlow(r.data.view); return; }
    techError();
  }
  async function doBack() {
    if (S.saving) return;
    S.saving = true;
    render();
    var r = await api('POST', jobApi('/back'), {});
    if (r.status === 401) return;
    if (r.data && r.data.view) return setFlow(r.data.view);
    techError();
  }

  // ------------------------------------------------------------------------------------ dropdowns
  function closePopups() {
    var had = S.ui.dd || S.ui.prompt;
    var pf = S.ui.prompt ? S.ui.prompt.fk : null;
    S.ui.ddBuf = '';
    S.ui.dd = null;
    S.ui.prompt = null;
    if (!had) return;
    Array.prototype.forEach.call(root.querySelectorAll('.wd-popup'), function (e) { e.parentNode.removeChild(e); });
    Array.prototype.forEach.call(root.querySelectorAll('[aria-expanded="true"]'), function (e) { e.setAttribute('aria-expanded', 'false'); });
    if (pf) {
      S.pq[pf] = '';
      var inp = root.querySelector('input[data-prompt][data-fk="' + CSS.escape(pf) + '"]');
      if (inp) inp.value = '';
    }
  }
  async function openDropdown(path) {
    var f = REG[path];
    if (!f) return;
    var my = ++tokSeq;
    var typed = S.ui.ddBuf || '';
    closePopups();
    S.ui.ddBuf = typed;
    S.ui.dd = { fk: path, opts: f.remote ? null : (f.options || []), loading: !!f.remote, active: -1, tok: my };
    render();
    if (!f.remote) { applyTypeAhead(); return; }
    var scope = getPath(S, parentPath(path)) || {};
    var r = await api('GET', jobApi('/options?fid=' + enc(f.id) + '&dep=' + enc(f.depends ? (scope[f.depends] || '') : '')));
    if (!S.ui.dd || S.ui.dd.tok !== my || r.status === 401) return;
    S.ui.dd.opts = (r.data && r.data.options) || [];
    S.ui.dd.loading = false;
    render();
    applyTypeAhead();
  }
  function ddPick(val) {
    var d = S.ui.dd;
    if (!d) return;
    var path = d.fk, f = REG[path];
    S.ui.ddBuf = '';
    setPath(S, path, val);
    var sc = getPath(S, parentPath(path));
    (f.resets || []).forEach(function (k) { if (sc) sc[k] = ''; });
    clearErr(path);
    S.ui.dd = null;
    S.ui.focus = { aid: f.id, fk: path };
    render();
  }
  function moveActive(delta) {
    var d = S.ui.dd;
    if (!d || d.loading) return;
    var n = ddAll(REG[d.fk], d).length;
    if (!n) return;
    d.active = Math.max(0, Math.min(n - 1, d.active + delta));
    render();
  }
  function applyTypeAhead() {
    var d = S.ui.dd;
    if (!d || d.loading || !S.ui.ddBuf) return;
    var all = ddAll(REG[d.fk], d);
    var want = S.ui.ddBuf.toLowerCase();
    for (var i = 0; i < all.length; i++) {
      if (all[i] !== '' && all[i].toLowerCase().indexOf(want) === 0) { d.active = i; render(); return; }
    }
  }
  function typeAhead(path, ch) {
    S.ui.ddBuf = (S.ui.ddBuf || '') + ch;
    clearTimeout(timers.dd);
    timers.dd = setTimeout(function () { S.ui.ddBuf = ''; }, 900);
    if (!S.ui.dd || S.ui.dd.fk !== path) { openDropdown(path); return; }
    applyTypeAhead();
  }

  // ------------------------------------------------------------------------------------ prompts
  function promptUrl(f, extra) { return jobApi('/prompt/' + enc(f.prompt) + '?' + extra); }
  async function openPrompt(path) {
    var f = REG[path];
    if (!f || (S.ui.prompt && S.ui.prompt.fk === path)) return;
    closePopups();
    var my = ++tokSeq;
    if (f.mode !== 'tree') return; // search / typeahead prompts show nothing until a query is run
    S.ui.prompt = { fk: path, options: [], trail: [], loading: true, active: -1, tok: my };
    render();
    var r = await api('GET', promptUrl(f, 'parent='));
    if (!S.ui.prompt || S.ui.prompt.tok !== my || r.status === 401) return;
    S.ui.prompt.options = (r.data && r.data.options) || [];
    S.ui.prompt.loading = false;
    render();
  }
  async function runSearch(path) {
    var f = REG[path];
    if (!f) return;
    var q = (S.pq[path] || '').trim();
    if (!q) { openPrompt(path); return; }
    var my = ++tokSeq;
    S.ui.dd = null;
    S.ui.prompt = { fk: path, options: [], trail: [], loading: true, active: -1, tok: my, search: true };
    S.ui.focus = { aid: f.id, fk: path };
    render();
    var r = await api('GET', promptUrl(f, 'q=' + enc(q)));
    if (!S.ui.prompt || S.ui.prompt.tok !== my || r.status === 401) return;
    S.ui.prompt.options = (r.data && r.data.options) || [];
    S.ui.prompt.loading = false;
    render();
  }
  async function loadChildren(path, parentId) {
    var f = REG[path];
    var p = S.ui.prompt;
    var my = ++tokSeq;
    p.tok = my; p.loading = true; p.options = []; p.active = -1;
    render();
    var r = await api('GET', promptUrl(f, 'parent=' + enc(parentId || '')));
    if (!S.ui.prompt || S.ui.prompt.tok !== my || r.status === 401) return;
    S.ui.prompt.options = (r.data && r.data.options) || [];
    S.ui.prompt.loading = false;
    render();
  }
  function promptPick(path, oid) {
    var p = S.ui.prompt;
    if (!p) return;
    var o = null;
    p.options.forEach(function (x) { if (x.id === oid) o = x; });
    if (!o) return;
    var f = REG[path];
    if (o.folder) { p.trail.push({ id: o.id, label: o.label }); loadChildren(path, o.id); return; }
    var cur = getPath(S, path) || [];
    if (f.multi) {
      if (!cur.some(function (c) { return c.id === o.id; })) cur = cur.concat([{ id: o.id, label: o.label }]);
    } else cur = [{ id: o.id, label: o.label }];
    setPath(S, path, cur);
    S.pq[path] = '';
    S.ui.prompt = null;
    clearErr(path);
    S.ui.focus = { aid: f.id, fk: path };
    render();
  }
  function promptBack(path) {
    var p = S.ui.prompt;
    if (!p || !p.trail.length) return;
    p.trail.pop();
    loadChildren(path, p.trail.length ? p.trail[p.trail.length - 1].id : '');
  }
  function schedulePromptSearch(path) {
    var f = REG[path];
    if (!f || f.mode !== 'typeahead') return;
    clearTimeout(timers.prompt);
    if (!(S.pq[path] || '').trim()) { closePopups(); return; }
    timers.prompt = setTimeout(function () { runSearch(path); }, 350);
  }

  // ------------------------------------------------------------------------------------ misc actions
  function clearErr(path) {
    var before = S.errors.length;
    S.errors = S.errors.filter(function (e) { return e.path !== path && e.path.indexOf(path + '.') !== 0; });
    if (before === S.errors.length) return;
    var nodes = root.querySelectorAll('[data-fk="' + CSS.escape(path) + '"], [data-fk^="' + CSS.escape(path + '.') + '"]');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.closest('[data-automation-id="errorBanner"]')) continue; // the banner keeps its entries until the next save
      var wrap = n.closest('.wd-field');
      var holder = wrap || n.closest('.wd-section');
      var e = holder ? holder.querySelector(':scope > .wd-err') : null;
      if (e) e.parentNode.removeChild(e);
      if (wrap) {
        wrap.classList.remove('wd-invalid');
        Array.prototype.forEach.call(wrap.querySelectorAll('[aria-invalid]'), function (x) { x.removeAttribute('aria-invalid'); });
      }
      break;
    }
  }
  async function onFile(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    var slot = input.getAttribute('data-slot');
    var fd = new FormData();
    fd.append('file', file, file.name);
    var r = await api('POST', jobApi('/upload/' + enc(slot)), fd);
    if (r.status === 401) return;
    if (r.data && r.data.ok) { S.values[slot] = r.data.file; delete S.fileErr[slot]; clearErr('values.' + slot); }
    else S.fileErr[slot] = (r.data && r.data.error) || 'The file could not be uploaded.';
    render();
  }
  async function delFile(slot) {
    var r = await api('DELETE', jobApi('/upload/' + enc(slot)));
    if (r.status === 401) return;
    S.values[slot] = null;
    render();
  }
  function jumpTo(path) {
    var nodes = root.querySelectorAll('[data-fk="' + CSS.escape(path) + '"], [data-fk^="' + CSS.escape(path + '.') + '"]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest('[data-automation-id="errorBanner"]')) continue;
      el.scrollIntoView({ block: 'center' });
      el.focus({ preventScroll: true });
      return;
    }
  }

  function act(name, el, ev) {
    var path = el ? el.getAttribute('data-fk') : null;
    switch (name) {
      case 'apply': if (ev) ev.preventDefault(); return onApply();
      case 'choose': if (ev) ev.preventDefault(); return onChoose(el.getAttribute('data-path'));
      case 'signin': return doSignIn();
      case 'create': return doCreate();
      case 'forgot': return doForgot();
      case 'to-create': S.signin.mode = 'create'; S.notice = null; return render();
      case 'to-signin': S.signin.mode = 'signin'; S.notice = null; return render();
      case 'to-forgot': S.signin.mode = 'forgot'; S.notice = null; return render();
      case 'next': return doNext();
      case 'back': return doBack();
      case 'dd':
        if (S.ui.dd && S.ui.dd.fk === path) { closePopups(); S.ui.focus = { aid: el.getAttribute('data-automation-id'), fk: path }; return render(); }
        return openDropdown(path);
      case 'dd-pick': return ddPick(el.getAttribute('data-val'));
      case 'prompt-pick': return promptPick(path, el.getAttribute('data-oid'));
      case 'prompt-back': return promptBack(path);
      case 'prompt-del':
        var cur = getPath(S, path) || [];
        cur.splice(+el.getAttribute('data-idx'), 1);
        clearErr(path);
        return render();
      case 'add':
        clearErr(path);
        var arr = getPath(S, path) || [];
        arr.push(clone(REG[path].blank));
        setPath(S, path, arr);
        return render();
      case 'del':
        var list = getPath(S, path);
        list.splice(+el.getAttribute('data-idx'), 1);
        S.errors = S.errors.filter(function (e) { return e.path.indexOf(path + '.') !== 0; });
        return render();
      case 'select-files':
        var inp = el.closest('.wd-field').querySelector('input[type="file"]');
        if (inp) inp.click();
        return;
      case 'del-file': return delFile(el.getAttribute('data-slot'));
      case 'err-jump': return jumpTo(path);
      case 'cookie-accept':
        try { window.localStorage.setItem('wd-cookie-ack', '1'); } catch (e) { /* ignore */ }
        S.ui.cookieOk = true;
        return render();
      default: return;
    }
  }

  // ------------------------------------------------------------------------------------ events
  root.addEventListener('click', function (ev) {
    var el = ev.target.closest ? ev.target.closest('[data-act]') : null;
    if (!el || !root.contains(el)) return;
    act(el.getAttribute('data-act'), el, ev);
  });
  root.addEventListener('mousedown', function (ev) {
    // keep keyboard focus in the field while an option in its popup is being clicked
    if (ev.target.closest && ev.target.closest('.wd-popup')) ev.preventDefault();
  });
  document.addEventListener('mousedown', function (ev) {
    if (!S.ui.dd && !S.ui.prompt) return;
    if (ev.target.closest && ev.target.closest('.wd-anchor')) return;
    closePopups();
  });
  root.addEventListener('submit', function (ev) { ev.preventDefault(); });
  root.addEventListener('focusin', function (ev) {
    if (!S.ui.dd && !S.ui.prompt) return;
    if (ev.target.closest && ev.target.closest('.wd-anchor')) return;
    closePopups();
  });
  root.addEventListener('input', function (ev) {
    var el = ev.target;
    var fk = el.getAttribute ? el.getAttribute('data-fk') : null;
    if (!fk || el.type === 'checkbox' || el.type === 'radio' || el.type === 'file') return;
    if (el.__tracked !== undefined) {
      if (el.__tracked === el.value) return; // same value the app already knows: not a change
      el.__tracked = el.value;
    }
    if (el.hasAttribute('data-prompt')) { S.pq[fk] = el.value; schedulePromptSearch(fk); return; }
    var v = el.value;
    if (el.hasAttribute('data-date')) {
      var digits = v.replace(/\D/g, '').slice(0, el.getAttribute('data-date') === 'y' ? 4 : 2);
      if (digits !== v) { el.value = digits; v = digits; }
      setPath(S, fk, v);
      clearErr(parentPath(fk));
      if (el.getAttribute('data-date') !== 'y' && v.length === 2) {
        var nxt = el.nextElementSibling ? el.nextElementSibling.nextElementSibling : null;
        if (nxt && nxt.focus) nxt.focus();
      }
      return;
    }
    setPath(S, fk, v);
    clearErr(fk);
  }, true);
  root.addEventListener('change', function (ev) {
    var el = ev.target;
    if (el.type === 'file') { onFile(el); return; }
    var fk = el.getAttribute ? el.getAttribute('data-fk') : null;
    if (!fk) return;
    var f = REG[fk];
    if (el.type === 'checkbox') {
      if (f && f.t === 'checkboxes') {
        var cur = (getPath(S, fk) || []).slice();
        var i = cur.indexOf(el.value);
        if (el.checked && i < 0) cur.push(el.value);
        if (!el.checked && i >= 0) cur.splice(i, 1);
        setPath(S, fk, cur);
      } else setPath(S, fk, el.checked);
    } else if (el.type === 'radio') setPath(S, fk, el.value);
    else return;
    clearErr(fk);
    if (f && f.rerender) { S.ui.focus = { aid: f.id, fk: fk }; render(); }
  });
  root.addEventListener('keydown', function (ev) {
    var t = ev.target;
    if (ev.key === 'Escape') { if (S.ui.dd || S.ui.prompt) { closePopups(); render(); } return; }
    var tag = t.tagName;
    if ((tag === 'DIV' || tag === 'SPAN') && t.getAttribute('role') === 'button' && (ev.key === 'Enter' || ev.key === ' ')) {
      var a = t.getAttribute('data-key-act') || t.getAttribute('data-act');
      if (a) { ev.preventDefault(); act(a, t, null); }
      return;
    }
    var fk = t.getAttribute ? t.getAttribute('data-fk') : null;
    if (fk && fk.indexOf('signin.') === 0 && ev.key === 'Enter') {
      ev.preventDefault();
      act(S.signin.mode === 'create' ? 'create' : (S.signin.mode === 'forgot' ? 'forgot' : 'signin'), null, null);
      return;
    }
    if (t.getAttribute && t.getAttribute('data-act') === 'dd') {
      var d = S.ui.dd;
      var isOpen = d && d.fk === fk;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (!isOpen) openDropdown(fk); else moveActive(ev.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if (ev.key === 'Enter' && isOpen && !d.loading && d.active >= 0) {
        ev.preventDefault();
        ddPick(ddAll(REG[fk], d)[d.active]);
        return;
      }
      if (ev.key.length === 1 && ev.key !== ' ' && !ev.ctrlKey && !ev.metaKey && !ev.altKey) typeAhead(fk, ev.key);
      return;
    }
    if (t.hasAttribute && t.hasAttribute('data-prompt')) {
      var p = S.ui.prompt;
      var open = p && p.fk === fk;
      if (ev.key === 'Enter') {
        ev.preventDefault();
        if (open && !p.loading && p.active >= 0 && p.options[p.active]) promptPick(fk, p.options[p.active].id);
        else runSearch(fk);
        return;
      }
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (!open) { openPrompt(fk); return; }
        if (p.loading || !p.options.length) return;
        p.active = Math.max(0, Math.min(p.options.length - 1, p.active + (ev.key === 'ArrowDown' ? 1 : -1)));
        render();
      }
      return;
    }
  });
  root.addEventListener('click', function (ev) {
    var t = ev.target;
    if (t.hasAttribute && t.hasAttribute('data-prompt')) openPrompt(t.getAttribute('data-fk'));
  });
  window.addEventListener('popstate', function () { window.location.reload(); });

  boot();
})();
"""

_APP_CSS = r"""*,*::before,*::after{box-sizing:border-box}
html{font-size:14px}
body{margin:0;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;color:#333;background:#f2f4f7;line-height:1.4}
[hidden]{display:none!important}
a{color:#0b66c3}
.wd-shell{min-height:100vh}
.wd-header{position:sticky;top:0;z-index:30;background:#fff;border-bottom:1px solid #d9dde3;padding:14px 28px}
.wd-brand{font-weight:700;font-size:20px;color:#0b2f66;text-decoration:none}
.wd-main{max-width:940px;margin:24px auto;padding:0 16px}
.wd-page{background:#fff;border:1px solid #d9dde3;border-radius:4px;padding:24px 28px;margin-bottom:24px}
.wd-h2{font-size:26px;margin:0 0 16px;outline:none}
.wd-h3{font-size:18px;margin:22px 0 10px}
.wd-h4{font-size:15px;margin:0}
.wd-muted{color:#6b7280}
.wd-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.wd-reqnote{color:#6b7280;margin:0 0 14px}
.wd-jobhead{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.wd-facts{display:flex;flex-wrap:wrap;gap:8px 32px;margin:8px 0 16px}
.wd-facts div{min-width:140px}
.wd-facts dt{font-size:12px;color:#6b7280;text-transform:capitalize}
.wd-facts dd{margin:0;font-weight:600}
.wd-desc{border-top:1px solid #eceff3;padding-top:12px}
.wd-joblist{list-style:none;padding:0;margin:0}
.wd-jobitem{background:#fff;border:1px solid #d9dde3;border-radius:4px;padding:10px 16px;margin-bottom:10px}
.wd-btn{display:inline-block;padding:10px 22px;border-radius:4px;border:1px solid #0b66c3;font:inherit;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;user-select:none}
.wd-btn-primary{background:#0b66c3;color:#fff}
.wd-btn-secondary{background:#fff;color:#0b66c3}
.wd-btn-block{display:block;width:100%;margin:10px 0}
.wd-btn[disabled]{opacity:.55;cursor:default}
.wd-btnwrap{position:relative;display:block;margin:16px 0 8px}
.wd-btnwrap .wd-btn{margin:0}
.wd-filter{position:absolute;top:0;right:0;bottom:0;left:0;z-index:2;background:transparent;cursor:pointer}
.wd-linkbtn{background:none;border:0;color:#0b66c3;font:inherit;cursor:pointer;text-decoration:underline;padding:2px 4px}
.wd-backdrop{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(15,23,42,.5);display:flex;justify-content:center;align-items:flex-start;padding-top:7vh;z-index:100}
.wd-modal{background:#fff;border-radius:6px;width:460px;max-width:94vw;max-height:86vh;overflow:auto;padding:28px}
.wd-modal-title{font-size:22px;margin:0 0 14px;outline:none}
.wd-auth-links{margin-top:12px;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.wd-field{margin:0 0 16px;position:relative}
.wd-label{display:block;font-weight:600;margin:0 0 5px;padding:0}
.wd-req{color:#c62828;margin-left:2px;text-decoration:none}
.wd-input,.wd-dd{display:block;width:100%;max-width:460px;padding:9px 10px;border:1px solid #8a94a3;border-radius:4px;font:inherit;background:#fff;color:#222}
textarea.wd-input{max-width:100%;resize:vertical}
.wd-dd{text-align:left;cursor:pointer;position:relative;padding-right:28px}
.wd-dd::after{content:"\25BE";position:absolute;right:10px;top:9px;color:#555}
.wd-invalid .wd-input,.wd-invalid .wd-dd{border-color:#c62828}
.wd-invalid>.wd-label{color:#c62828}
.wd-err{color:#c62828;margin-top:4px;font-size:13px}
.wd-anchor{position:relative;max-width:460px}
.wd-popup{position:absolute;top:100%;left:0;right:0;z-index:60;background:#fff;border:1px solid #8a94a3;border-radius:4px;box-shadow:0 6px 18px rgba(0,0,0,.18);max-height:260px;overflow:auto}
.wd-list{list-style:none;margin:0;padding:0}
.wd-opt{padding:9px 12px;cursor:pointer;border-bottom:1px solid #f1f3f6}
.wd-opt:hover,.wd-active{background:#e6f0fb}
.wd-opt[aria-selected="true"]{font-weight:700}
.wd-promptopt{display:flex;justify-content:space-between}
.wd-arrow{color:#6b7280;font-size:18px;line-height:14px}
.wd-loading,.wd-noitems{padding:10px 12px;color:#6b7280;list-style:none}
.wd-crumb{padding:6px 8px;border-bottom:1px solid #eceff3}
.wd-multi-in{border:1px solid #8a94a3;border-radius:4px;background:#fff;padding:4px;display:flex;flex-wrap:wrap;gap:4px}
.wd-multi-in .wd-input{border:0;flex:1 1 140px;padding:5px 6px;min-width:120px}
.wd-invalid .wd-multi-in{border-color:#c62828}
.wd-pills{display:contents;list-style:none;margin:0;padding:0}
.wd-pill{display:inline-flex;align-items:center;gap:6px;background:#e6f0fb;border-radius:12px;padding:3px 4px 3px 10px}
.wd-pill-x{cursor:pointer;padding:0 6px;font-weight:700}
.wd-daterow{display:flex;align-items:center;gap:6px}
.wd-date{width:64px}
.wd-date-y{width:84px}
.wd-datesep{color:#6b7280}
.wd-fieldset{border:0;padding:0;margin:0}
.wd-radio,.wd-check{position:relative;display:flex;align-items:center;gap:8px;margin:6px 0;min-height:24px}
.wd-radio input,.wd-check input{position:absolute;left:0;top:0;width:22px;height:22px;margin:0;opacity:0;cursor:pointer;z-index:1}
.wd-fake{flex:0 0 22px;width:22px;height:22px;border:2px solid #6b7280;background:#fff}
.wd-fake-radio{border-radius:50%}
.wd-fake-check{border-radius:3px}
.wd-radio input:checked+.wd-fake,.wd-check input:checked+.wd-fake{background:#0b66c3;border-color:#0b66c3;box-shadow:inset 0 0 0 3px #fff}
.wd-check label,.wd-radio label{cursor:pointer;font-weight:400}
.wd-section{margin:20px 0;padding-top:4px;border-top:1px solid #eceff3}
.wd-panel{border:1px solid #d9dde3;border-radius:4px;padding:14px 16px;margin:10px 0 14px;background:#fafbfc}
.wd-panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.wd-drop{border:2px dashed #b6bfcc;border-radius:6px;padding:22px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:8px;max-width:460px}
.wd-file-ok{display:flex;align-items:center;gap:12px;border:1px solid #2e7d32;border-radius:6px;padding:10px 14px;max-width:460px;background:#f3faf3}
.wd-file-ic{color:#2e7d32;font-size:20px}
.wd-file-meta{flex:1}
.wd-file-name{font-weight:600;word-break:break-all}
.wd-banner{border-radius:4px;padding:10px 14px;margin:0 0 16px;border:1px solid}
.wd-banner-error{background:#fdecea;border-color:#c62828;color:#7f1d1d}
.wd-banner-warn{background:#fff4e5;border-color:#ed6c02;color:#663c00}
.wd-banner-info{background:#e8f4fd;border-color:#0b66c3;color:#0b3d75}
.wd-banner-title{margin:0 0 6px;font-size:16px}
.wd-banner ul{margin:0;padding-left:18px}
.wd-progress{margin:0 0 18px}
.wd-progress ol{list-style:none;margin:0;padding:0;display:flex;gap:6px;flex-wrap:wrap}
.wd-step{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#fff;border:1px solid #d9dde3;border-radius:20px;color:#6b7280}
.wd-step-idx{display:inline-flex;width:22px;height:22px;border-radius:50%;background:#d9dde3;align-items:center;justify-content:center;font-size:12px;color:#333}
.wd-step-active{border-color:#0b66c3;color:#0b2f66;font-weight:700}
.wd-step-active .wd-step-idx{background:#0b66c3;color:#fff}
.wd-step-completed .wd-step-idx{background:#2e7d32;color:#fff}
.wd-footer{display:flex;justify-content:space-between;align-items:center;margin-top:28px;padding-top:16px;border-top:1px solid #eceff3}
.wd-review{margin:0}
.wd-review-row{display:flex;gap:16px;padding:4px 0;border-bottom:1px solid #f1f3f6}
.wd-review-row dt{flex:0 0 260px;color:#6b7280}
.wd-review-row dd{margin:0;font-weight:600}
.wd-info{background:#f5f7fa;border-radius:4px;padding:10px 14px;margin:0 0 16px;color:#4b5563}
.wd-captcha{margin:12px 0;border:1px solid #d9dde3;border-radius:4px;padding:10px}
.wd-captcha-text{margin:0 0 6px;font-weight:600}
.wd-busy{position:fixed;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,#0b66c3,#7ab4ee);z-index:200}
.wd-loading-block{padding:36px 0;text-align:center;color:#6b7280}
.wd-cookie{position:fixed;left:0;right:0;bottom:0;z-index:90;background:#1f2937;color:#fff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px}
.wd-cookie p{margin:0}
.wd-has-cookie{padding-bottom:84px}
.wd-blocker{position:fixed;top:0;right:0;bottom:0;left:0;z-index:120;background:rgba(255,255,255,.55);cursor:progress}
"""
