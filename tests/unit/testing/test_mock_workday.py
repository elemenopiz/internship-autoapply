"""Behavioural tests of the mock Workday tenant, driven like a careful scripted user with real Chromium.

The ``Wd`` driver below doubles as executable documentation: every selector and interaction an adapter needs
(see the module docstring of ``autoapply.testing.mock_ats.workday``) is used here at least once.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    expect,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from autoapply.apply.matching import best_option
from autoapply.models import DECLINE
from autoapply.testing.mock_ats.base import (
    MailboxEmailVerifier,
    MockHub,
    MockJob,
    MockQuestion,
    UploadedFile,
)
from autoapply.testing.mock_ats.workday import (
    DISABILITY_STATUSES,
    ETHNICITIES,
    GENDERS,
    VETERAN_STATUSES,
    Draft,
    WorkdaySite,
    make_site,
)

# SPEC 1.8 loopback restriction. NOTE: ``EXCLUDE *.localhost`` is required for the mock hosts
# (``<real host>.localhost``); the rule without it makes every mock site unreachable.
LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost, EXCLUDE 127.0.0.1"
EMAIL = "alex.rivera@example.test"
PASSWORD = "Str0ng!Passw0rd#1"
JOB_ID = "R0012345"
FAST = (5, 15)  # simulated XHR latency (ms) for tests that are not about latency
NEXT = "bottom-navigation-next-button"

expect.set_options(timeout=15_000)
browser_test = pytest.mark.browser

# ---------------------------------------------------------------------------------------- saved-step data

INFO_VALUES: dict[str, Any] = {
    "source": [{"id": "company-website", "label": "Company Website"}],
    "previousWorker": "false",
    "country": "United States of America",
    "legalNameSection_firstName": "Alex",
    "legalNameSection_middleName": "",
    "legalNameSection_lastName": "Rivera",
    "addressSection_addressLine1": "123 Example Street",
    "addressSection_addressLine2": "",
    "addressSection_city": "Austin",
    "addressSection_countryRegion": "Texas",
    "addressSection_postalCode": "78701",
    "phone-device-type": "Mobile",
    "country-phone-code": "United States of America (+1)",
    "phone-number": "5125550123",
    "phone-extension": "",
}
EXPERIENCE_VALUES: dict[str, Any] = {
    "workExperience": [
        {
            "jobTitle": "Product Intern",
            "company": "Example Corp",
            "location": "Austin, TX",
            "currentlyWorkHere": False,
            "startDate": {"m": "06", "d": "", "y": "2025"},
            "endDate": {"m": "08", "d": "", "y": "2025"},
            "roleDescription": "Built a roadmap.",
        }
    ],
    "education": [
        {
            "school": [
                {"id": "university-of-texas-at-austin", "label": "University of Texas at Austin"}
            ],
            "degree": "Bachelor of Science",
            "fieldOfStudy": [{"id": "computer-science", "label": "Computer Science"}],
            "gpa": "3.8",
            "firstYearAttended": {"m": "", "d": "", "y": "2024"},
            "lastYearAttended": {"m": "", "d": "", "y": "2028"},
        }
    ],
    "skills": [],
    "resume": None,
    "coverLetter": None,
    "websites": [],
    "linkedinQuestion": "",
}
QUESTION_VALUES: dict[str, Any] = {
    "work_auth": "Yes",
    "sponsorship": "No",
    "relocate": "Yes",
    "salary": "",
    "certify": True,
}
DISCLOSURE_VALUES: dict[str, Any] = {
    "gender": "Decline to Self Identify",
    "race": "Decline to Self Identify",
    "veteran": "I don't wish to answer",
    "agreementCheckbox": True,
}
SELF_ID_VALUES: dict[str, Any] = {
    "selfIdentifiedDisabilityData--name": "Alex Rivera",
    "selfIdentifiedDisabilityData--dateSignedOn": {"m": "09", "d": "29", "y": "2026"},
    "disability": "I do not want to answer",
}
SAVED = {
    "myInformation": INFO_VALUES,
    "myExperience": EXPERIENCE_VALUES,
    "applicationQuestions": QUESTION_VALUES,
    "voluntaryDisclosures": DISCLOSURE_VALUES,
    "selfIdentify": SELF_ID_VALUES,
}
RESUME_BYTES = b"%PDF-1.4\n% mock resume of Alex Rivera\n%%EOF\n"


def seed_draft(
    site: WorkdaySite,
    upto: str,
    *,
    job_id: str = JOB_ID,
    email: str = EMAIL,
    path: str = "applyManually",
    resume: bool = True,
) -> Draft:
    """Create the account and a draft whose steps before ``upto`` are saved, so a test can start mid-wizard."""
    if email not in site.accounts:
        site.add_account(email, PASSWORD)
    job = site.jobs[job_id]
    ids = site._step_ids(job)
    draft = Draft(email=email, job_id=job_id, path=path, cursor=ids.index(upto))
    for step in ids[: draft.cursor]:
        draft.steps[step] = dict(SAVED[step])
    if resume and draft.cursor > ids.index("myExperience"):
        draft.files["resume"] = UploadedFile(
            "resume", "Alex Rivera Resume.pdf", "application/pdf", RESUME_BYTES
        )
    site.drafts[(email, job_id)] = draft
    return draft


# ---------------------------------------------------------------------------------------- environment


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        chromium = pw.chromium.launch(headless=True, args=[LOOPBACK_ONLY])
        yield chromium
        chromium.close()


@dataclass
class Env:
    site: WorkdaySite
    hub: MockHub
    browser: Browser
    tmp: Path
    contexts: list[BrowserContext] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def driver(self, **context_options: Any) -> Wd:
        options = {"viewport": {"width": 1280, "height": 1000}, **context_options}
        context = self.browser.new_context(**options)
        context.set_default_timeout(15_000)
        self.contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        return Wd(page, self.site, self.tmp)


StartFn = Callable[..., Env]


@pytest.fixture
def start(browser: Browser, tmp_path: Path) -> Iterator[StartFn]:
    hubs: list[MockHub] = []
    envs: list[Env] = []

    def _start(company: str = "acme", jobs: list[MockJob] | None = None, **options: Any) -> Env:
        options.setdefault("latency_ms", FAST)
        site = make_site(company, jobs, **options)
        hub = MockHub()
        hub.add(site)
        hub.start()
        hubs.append(hub)
        env = Env(site, hub, browser, tmp_path)
        envs.append(env)
        return env

    yield _start
    for env in envs:
        for context in env.contexts:
            context.close()
    for hub in hubs:
        hub.stop()
    for env in envs:
        assert not env.errors, f"uncaught page errors: {env.errors}"


class Wd:
    """A careful scripted user of the mock Workday site: the moves an adapter has to make."""

    def __init__(self, page: Page, site: WorkdaySite, tmp: Path) -> None:
        self.page = page
        self.site = site
        self.resume = tmp / "Alex Rivera Resume.pdf"
        self.resume.write_bytes(RESUME_BYTES)

    # -- locators ------------------------------------------------------------------------------------
    def aid(self, name: str, scope: Locator | None = None) -> Locator:
        return (scope or self.page).locator(f'[data-automation-id="{name}"]')

    def field(self, text: str) -> Locator:
        """The ``formField-*`` block whose label contains ``text`` (application questions have no stable ids)."""
        return self.page.locator('[data-automation-id^="formField-"]').filter(has_text=text)

    # -- navigation and sign-in --------------------------------------------------------------------------
    def open_job(self, job_id: str | None = None) -> None:
        self.page.goto(self.site.job_url(job_id))
        self.aid("adventureButton").wait_for()

    def start(self, path: str = "applyManually", job_id: str | None = None) -> None:
        self.open_job(job_id)
        self.aid("adventureButton").click()
        self.aid(path).click()

    def fill_credentials(self, email: str = EMAIL, password: str = PASSWORD) -> None:
        self.aid("signInContent").wait_for()
        self.aid("email").fill(email)
        self.aid("password").fill(password)

    def sign_in(self, email: str = EMAIL, password: str = PASSWORD, *, via: str = "filter") -> None:
        self.fill_credentials(email, password)
        button = self.aid("signInSubmitButton")
        if via == "filter":
            self.aid("click_filter").click()
        elif via == "force":
            button.click(force=True)
        elif via == "enter":
            self.aid("password").press("Enter")
        elif via == "keyboard":
            button.focus()
            self.page.keyboard.press("Enter")
        else:  # pragma: no cover - test helper misuse
            raise ValueError(via)

    def create_account(
        self,
        email: str = EMAIL,
        password: str = PASSWORD,
        *,
        verify_password: str | None = None,
        consent: bool = True,
        submit: bool = True,
    ) -> None:
        self.aid("createAccountLink").click()
        self.aid("createAccountContent").wait_for()
        self.aid("email").fill(email)
        self.aid("password").fill(password)
        self.aid("verifyPassword").fill(password if verify_password is None else verify_password)
        if consent:
            self.aid("createAccountCheckbox").check()
        if submit:
            self.aid("click_filter").click()

    def wait_page(self, page_aid: str) -> None:
        """Wait for a wizard page: the frame appears first, the form (and its footer) a request later."""
        self.aid(page_aid).wait_for()
        self.aid(NEXT).wait_for()

    def next(self) -> None:
        """Click Save and Continue / Submit and wait for the response to be rendered (banner or next page frame)."""
        with self.page.expect_response(
            lambda r: (
                r.request.method == "POST"
                and re.search(r"/apply/[^/]+/(save|submit)$", r.url) is not None
            )
        ):
            self.aid(NEXT).click()
        self.page.wait_for_function(
            "() => !document.querySelector('[data-automation-id=\"bottom-navigation-next-button\"]:disabled')"
        )

    # -- widgets ---------------------------------------------------------------------------------------------
    def open_dropdown(self, button: Locator) -> None:
        """Click a dropdown button and wait until its list has loaded (remote lists show "Loading..." first)."""
        button.click()
        self.aid("activeListContainer").wait_for()
        self.aid("loadingText", self.aid("activeListContainer")).wait_for(state="detached")

    def choose(self, scope: Locator, name: str, option: str) -> None:
        """Custom dropdown: click the button, then click the option (never ``select_option``)."""
        scope.locator(f'button[data-automation-id="{name}"]').click()
        self.page.get_by_role("option", name=option, exact=True).click()

    def prompt(
        self, scope: Locator, name: str, query: str, option: str, *, enter: bool = True
    ) -> None:
        """Prompt / typeahead: type, press Enter to run the search, click the ``promptOption``."""
        box = scope.locator(f'input[data-automation-id="{name}"]')
        box.click()
        box.fill(query)
        if enter:
            box.press("Enter")
        self.page.locator(
            f'[data-automation-id="promptOption"][data-automation-label="{option}"]'
        ).click()

    def text(self, scope: Locator, name: str, value: str) -> None:
        scope.locator(f'[data-automation-id="{name}"]').fill(value)

    def date(self, scope: Locator, name: str, **parts: str) -> None:
        names = {"m": "Month", "d": "Day", "y": "Year"}
        for key, value in parts.items():
            scope.locator(f'[data-automation-id="{name}-dateSection{names[key]}-input"]').fill(
                value
            )

    # -- wizard pages ------------------------------------------------------------------------------------------
    def fill_my_information(
        self,
        *,
        source: tuple[str, ...] = ("Job Board", "LinkedIn"),
        previous_worker: str = "No",
        first: str = "Alex",
        last: str = "Rivera",
        state: str = "Texas",
        postal: str = "78701",
        phone: str = "5125550123",
    ) -> None:
        page = self.aid("applyFlowMyInfoPage")
        self.aid("source--source", page).click()
        for label in source:
            self.page.locator(
                f'[data-automation-id="promptOption"][data-automation-label="{label}"]'
            ).click()
        page.get_by_role("radiogroup", name=re.compile("previously worked")).get_by_label(
            previous_worker, exact=True
        ).check()
        self.text(page, "legalNameSection_firstName", first)
        self.text(page, "legalNameSection_lastName", last)
        self.text(page, "addressSection_addressLine1", "123 Example Street")
        self.text(page, "addressSection_city", "Austin")
        self.choose(page, "addressSection_countryRegion", state)
        self.text(page, "addressSection_postalCode", postal)
        self.choose(page, "phone-device-type", "Mobile")
        self.text(page, "phone-number", phone)

    def add_work_experience(self) -> Locator:
        section = self.aid("workExperienceSection")
        section.locator('[data-automation-id="add-button"]').click()
        count = section.locator('[data-automation-id^="workExperience-"]').count()
        group = self.aid(f"workExperience-{count}", section)
        group.wait_for()
        return group

    def fill_work_experience(self) -> None:
        group = self.add_work_experience()
        self.text(group, "jobTitle", "Product Intern")
        self.text(group, "company", "Example Corp")
        self.text(group, "location", "Austin, TX")
        self.date(group, "startDate", m="06", y="2025")
        self.date(group, "endDate", m="08", y="2025")
        self.text(group, "roleDescription", "Built a roadmap.")

    def add_education(self) -> Locator:
        section = self.aid("educationSection")
        section.locator('[data-automation-id="add-button"]').click()
        count = section.locator('[data-automation-id^="education-"]').count()
        group = self.aid(f"education-{count}", section)
        group.wait_for()
        return group

    def fill_education(self) -> None:
        group = self.add_education()
        box = group.locator('input[data-automation-id="school"]')
        box.click()
        box.fill("University of Texas at Austin")
        self.page.locator(
            '[data-automation-id="promptOption"][data-automation-label="University of Texas at Austin"]'
        ).click()
        self.choose(group, "degree", "Bachelor of Science")
        self.prompt(group, "fieldOfStudy", "Computer", "Computer Science")
        self.text(group, "gpa", "3.8")
        self.date(group, "firstYearAttended", y="2024")
        self.date(group, "lastYearAttended", y="2028")

    def upload_resume(self, path: Path | None = None) -> None:
        section = self.aid("resumeSection")
        section.locator('input[data-automation-id="file-upload-input-ref"]').set_input_files(
            str(path or self.resume)
        )
        self.aid("file-upload-successful", section).wait_for()

    def fill_my_experience(self) -> None:
        self.fill_work_experience()
        self.fill_education()
        self.prompt(self.aid("skillsSection"), "skills", "Python", "Python")
        self.upload_resume()
        website = self.aid("websiteSection")
        website.locator('[data-automation-id="add-button"]').click()
        self.text(self.aid("websitePanelSet-1"), "url", "https://example.test/alex")

    def dropdown_question(self, label: str, option: str) -> None:
        field = self.field(label)
        field.locator('button[aria-haspopup="listbox"]').click()
        self.page.get_by_role("option", name=option, exact=True).click()

    def fill_default_questions(self) -> None:
        self.dropdown_question("legally authorized", "Yes")
        self.dropdown_question("sponsorship", "No")
        self.field("willing to relocate").get_by_label("Yes", exact=True).check()
        self.page.get_by_label("I certify").check()

    def fill_disclosures(self) -> None:
        page = self.aid("applyFlowVoluntaryDisclosuresPage")
        self.choose(page, "gender", "Decline to Self Identify")
        self.choose(page, "ethnicity", "Decline to Self Identify")
        self.choose(page, "veteranStatus", "I don't wish to answer")
        self.aid("agreementCheckbox", page).check()

    def fill_self_identify(self) -> None:
        page = self.aid("applyFlowSelfIdentifyPage")
        self.text(page, "selfIdentifiedDisabilityData--name", "Alex Rivera")
        self.date(page, "selfIdentifiedDisabilityData--dateSignedOn", m="09", d="29", y="2026")
        page.get_by_label("I do not want to answer").check()

    def complete_application(self) -> None:
        """From a fresh My Information page all the way to the confirmation."""
        self.fill_my_information()
        self.next()
        self.wait_page("applyFlowMyExpPage")
        self.fill_my_experience()
        self.next()
        self.wait_page("applyFlowPrimaryQuestionsPage")
        self.fill_default_questions()
        self.next()
        self.wait_page("applyFlowVoluntaryDisclosuresPage")
        self.fill_disclosures()
        self.next()
        self.wait_page("applyFlowSelfIdentifyPage")
        self.fill_self_identify()
        self.next()
        self.wait_page("applyFlowReviewPage")
        self.next()
        self.aid("applicationSubmittedPage").wait_for()

    def banner_messages(self) -> list[str]:
        return [t.strip() for t in self.aid("errorBanner").locator("li").all_inner_texts()]


def finish_from_questions(d: Wd) -> None:
    """Application Questions (default job) -> confirmation."""
    d.fill_default_questions()
    d.next()
    d.wait_page("applyFlowVoluntaryDisclosuresPage")
    d.fill_disclosures()
    d.next()
    d.wait_page("applyFlowSelfIdentifyPage")
    d.fill_self_identify()
    d.next()
    d.wait_page("applyFlowReviewPage")
    d.next()
    d.aid("applicationSubmittedPage").wait_for()


def wait_until(condition: Callable[[], bool], timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.05)


def sign_in_to_wizard(d: Wd, page_aid: str) -> None:
    """Chooser -> Apply Manually -> sign in with the pre-created account -> the given wizard page."""
    d.start()
    d.sign_in()
    d.wait_page(page_aid)


# ============================================================================================ http level


def test_site_addressing_and_helpers() -> None:
    site = make_site("acme")
    assert site.host == "acme.wd5.myworkdayjobs.com"
    assert site.name == "workday-acme"
    assert site.tenant == "acme"
    assert set(site.jobs) == {JOB_ID}
    path = site.job_path(site.jobs[JOB_ID])
    assert path == "/en-US/External/job/Austin-TX/Product-Management-Intern---Summer-2027_R0012345"
    other = make_site(
        "Keurig Dr Pepper", tenant="kdp", wd="wd1", site_name="KDP_Careers", name="wd-kdp"
    )
    assert other.host == "kdp.wd1.myworkdayjobs.com" and other.name == "wd-kdp"
    assert other.company_name == "Keurig Dr Pepper"
    assert other.job_path(other.jobs[JOB_ID]).startswith("/en-US/KDP_Careers/job/")
    assert make_site("Bank of X").tenant == "bankofx"


def test_job_slug_drops_punctuation_and_keeps_requisition_id() -> None:
    job = MockJob(
        id="JR-77",
        title="Strategy & Operations Intern (Summer 2027) - Remote",
        location="Remote, USA",
    )
    site = make_site("acme", [job])
    assert site.job_path(job).endswith(
        "/job/Remote-USA/Strategy--Operations-Intern-Summer-2027---Remote_JR-77"
    )


def test_http_shell_redirects_and_closed_jobs() -> None:
    closed = MockJob(id="R9", title="Old Role", closed=True)
    open_job = MockJob(id="R1", title="Open Role")
    site = make_site("acme", [open_job, closed])
    hub = MockHub()
    hub.add(site)
    with hub, httpx.Client(base_url=site.direct_url(""), follow_redirects=False) as http:
        resp = http.get("/")
        assert resp.status_code == 302 and resp.headers["location"] == "/en-US/External"
        resp = http.get("/External/job/Austin-TX/Open-Role_R1")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/en-US/External/job/Austin-TX/Open-Role_R1"
        ok = http.get("/en-US/External/job/Austin-TX/Open-Role_R1")
        assert ok.status_code == 200 and "wd-bootstrap" in ok.text
        assert (
            "PLAY_SESSION" in ok.headers["set-cookie"]
            and "HttpOnly" in ok.headers["set-cookie"]
        )
        gone = http.get("/en-US/External/job/Austin-TX/Old-Role_R9")
        assert gone.status_code == 404
        assert "The page you are looking for doesn't exist." in gone.text
        assert http.get("/en-US/External/job/Austin-TX/Old-Role_R9/apply").status_code == 404
        assert http.get("/en-US/OtherSite/job/Austin-TX/Open-Role_R1").status_code == 404
        assert http.get("/en-US/External/job/Austin-TX/Unknown-Role_R404").status_code == 404
        listing = http.post(f"/wday/cxs/{site.tenant}/External/jobs", json={})
        data = listing.json()
        assert data["total"] == 1
        assert data["jobPostings"][0]["externalPath"] == "/job/Austin-TX/Open-Role_R1"
        assert data["jobPostings"][0]["bulletFields"] == ["R1"]
        info = http.get(f"/wday/cxs/{site.tenant}/External/job/Austin-TX/Open-Role_R1").json()
        assert info["jobPostingInfo"]["jobReqId"] == "R1"
        assert info["hiringOrganization"]["name"] == "Acme"
        missing = http.get(f"/wday/cxs/{site.tenant}/External/job/Austin-TX/Old-Role_R9")
        assert missing.status_code == 404


def test_flow_endpoints_require_a_session_and_accounts_are_per_tenant() -> None:
    first = make_site("acme", latency_ms=0)
    second = make_site("boeing", latency_ms=0)
    first.add_account(EMAIL, PASSWORD)
    hub = MockHub()
    hub.add(first)
    hub.add(second)
    with hub:
        with httpx.Client(base_url=first.direct_url("")) as http:
            for path in ("save", "back", "submit", "page"):
                method = http.get if path == "page" else http.post
                resp = method(f"/wday/app/apply/{JOB_ID}/{path}")
                assert resp.status_code == 401, path
                assert resp.json()["view"] == "signin"
            assert http.get(f"/wday/app/apply/{JOB_ID}/prompt/source").status_code == 401
            assert (
                http.post(
                    "/wday/app/auth/sign-in", json={"email": EMAIL, "password": "wrong"}
                ).json()["ok"]
                is False
            )
            assert (
                http.post(
                    "/wday/app/auth/sign-in", json={"email": EMAIL, "password": PASSWORD}
                ).json()["ok"]
                is True
            )
            state = http.post(
                f"/wday/app/apply/{JOB_ID}/state", json={"path": "applyManually"}
            ).json()
            assert (
                state["view"] == "wizard"
                and state["current"] == "myInformation"
                and state["pending"] is True
            )
        with httpx.Client(base_url=second.direct_url("")) as other:
            resp = other.post("/wday/app/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
            assert resp.json()["ok"] is False  # Workday accounts live in one tenant only
    assert EMAIL in first.accounts and EMAIL not in second.accounts


def test_create_account_endpoint_rules_and_disabled_signup() -> None:
    site = make_site("acme", latency_ms=0)
    locked = make_site("nosignup", latency_ms=0, allow_signup=False)
    hub = MockHub()
    hub.add(site)
    hub.add(locked)
    body = {
        "email": "sam.lee@example.test",
        "password": PASSWORD,
        "verifyPassword": PASSWORD,
        "consent": True,
    }
    with hub:
        with httpx.Client(base_url=site.direct_url("")) as http:

            def attempt(**changes: Any) -> dict[str, Any]:
                return http.post("/wday/app/auth/create-account", json={**body, **changes}).json()  # type: ignore[no-any-return]

            assert "valid email" in attempt(email="not-an-email")["error"]
            assert (
                "at least 8 characters"
                in attempt(password="short", verifyPassword="short")["error"]
            )
            assert "do not match" in attempt(verifyPassword=PASSWORD + "x")["error"]
            assert "terms and conditions" in attempt(consent=False)["error"]
            assert attempt() == {"ok": True, "verify": False}
            assert "already exists" in attempt()["error"]
            assert site.accounts["sam.lee@example.test"].verified is True
        with httpx.Client(base_url=locked.direct_url("")) as http:
            resp = http.post("/wday/app/auth/create-account", json=body).json()
            assert resp["ok"] is False and "not available" in resp["error"]
    assert not locked.accounts


# ============================================================================================ job + chooser


@browser_test
def test_job_page_is_rendered_by_an_xhr_and_exposes_the_apply_button(start: StartFn) -> None:
    env = start(latency_ms=(300, 300))
    d = env.driver()
    d.page.goto(env.site.job_url())
    assert d.aid("adventureButton").count() == 0  # the shell is empty until the job XHR returns
    d.aid("adventureButton").wait_for()
    assert d.aid("adventureButton").inner_text() == "Apply"
    assert (
        d.aid("adventureButton").get_attribute("href")
        == env.site.job_path(env.site.jobs[JOB_ID]) + "/apply"
    )
    assert d.aid("jobPostingHeader").inner_text() == "Product Management Intern - Summer 2027"
    assert d.aid("locations").inner_text() == "Austin, TX"
    assert d.aid("requisitionId").inner_text() == JOB_ID
    assert "Summer 2027 internship" in d.aid("jobPostingDescription").inner_text()
    assert d.page.title() == "Product Management Intern - Summer 2027 - Acme Careers"


@browser_test
def test_job_listing_page_and_closed_posting(start: StartFn) -> None:
    jobs = [MockJob(id="R1", title="Open Role"), MockJob(id="R2", title="Closed Role", closed=True)]
    env = start(jobs=jobs)
    d = env.driver()
    d.page.goto(env.site.url("/External"))  # locale-less URLs redirect like the real thing
    d.aid("jobTitle").first.wait_for()
    assert d.aid("jobTitle").all_inner_texts() == ["Open Role"]
    d.aid("jobTitle").click()
    d.aid("adventureButton").wait_for()
    assert d.page.url.endswith("Open-Role_R1")
    d.page.goto(env.site.job_url("R2"))
    assert d.page.get_by_text("The page you are looking for doesn't exist.").is_visible()
    assert d.aid("adventureButton").count() == 0


@browser_test
def test_apply_opens_the_chooser_with_two_options_when_signed_out(start: StartFn) -> None:
    env = start()
    d = env.driver()
    d.open_job()
    d.aid("adventureButton").click()
    for option in ("autofillWithResume", "applyManually"):
        d.aid(option).wait_for()
    assert d.aid("useMyLastApplication").count() == 0
    assert (
        d.page.get_by_role("dialog").get_by_role("heading").inner_text() == "Start Your Application"
    )
    assert d.page.url.endswith("/apply")
    base = env.site.job_path(env.site.jobs[JOB_ID])
    assert d.aid("applyManually").get_attribute("href") == base + "/apply/applyManually"
    assert d.aid("autofillWithResume").inner_text() == "Autofill with Resume"
    d.aid("applyManually").click()
    d.aid("signInContent").wait_for()
    assert d.page.url.endswith("/apply/applyManually")
    assert env.site.events == []  # nothing is recorded until an account exists


@browser_test
def test_deep_links_show_the_flow_step_directly(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.page.goto(env.site.apply_url())
    d.aid("applyManually").wait_for()  # /apply -> chooser
    d.page.goto(env.site.apply_url(path="applyManually"))
    d.aid("signInContent").wait_for()  # signed out -> sign in, job page still behind the dialog
    assert d.aid("jobPostingHeader").is_visible()
    d.sign_in()
    d.wait_page("applyFlowMyInfoPage")
    d.page.goto(env.site.apply_url())  # signed in with a draft: straight back into the wizard
    d.wait_page("applyFlowMyInfoPage")
    assert d.aid("applyManually").count() == 0


# ============================================================================================ sign in / up


@browser_test
def test_sign_in_button_is_covered_by_the_click_filter_overlay(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    d.fill_credentials()
    button = d.aid("signInSubmitButton")
    assert button.evaluate("el => el.tagName") == "DIV" and button.get_attribute("role") == "button"
    overlay = d.aid("click_filter")
    assert (
        overlay.get_attribute("aria-label") == "Sign In"
        and overlay.get_attribute("role") == "button"
    )
    with pytest.raises(PlaywrightTimeout) as excinfo:
        button.click(timeout=1500)
    assert "click_filter" in str(excinfo.value) and "intercepts pointer events" in str(
        excinfo.value
    )
    with pytest.raises(PlaywrightError, match="strict mode violation"):
        d.page.get_by_role("button", name="Sign In").click(timeout=1500)
    assert "sign_in_ok" not in " ".join(env.site.events)
    overlay.click()
    d.wait_page("applyFlowMyInfoPage")
    assert f"sign_in_ok:{EMAIL}" in env.site.events


@browser_test
@pytest.mark.parametrize("via", ["filter", "force", "enter", "keyboard"])
def test_every_way_of_submitting_the_sign_in_form_that_a_real_user_has(
    start: StartFn, via: str
) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    d.sign_in(via=via)
    d.wait_page("applyFlowMyInfoPage")


@browser_test
def test_wrong_password_is_reported_and_the_account_locks_after_repeated_failures(
    start: StartFn,
) -> None:
    env = start(lockout_after=3)
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    for _ in range(3):
        d.sign_in(password="Wrong-Passw0rd!")
        expect(d.aid("errorMessage")).to_have_text(
            "The username or password you entered is incorrect. Please try again."
        )
        d.aid("password").fill("")
    assert env.site.accounts[EMAIL].locked
    d.sign_in()  # even the right password is refused now
    expect(d.aid("errorMessage")).to_contain_text("has been locked")
    assert d.aid("applyFlowMyInfoPage").count() == 0
    assert env.site.events.count(f"sign_in_failed:{EMAIL}") == 3


@browser_test
def test_create_account_validation_messages_then_success_signs_the_user_in(start: StartFn) -> None:
    env = start()
    d = env.driver()
    d.start()
    d.create_account("nope", PASSWORD)
    expect(d.aid("errorMessage")).to_contain_text("valid email address")
    d.aid("email").fill(EMAIL)
    d.aid("password").fill("weak")
    d.aid("verifyPassword").fill("weak")
    d.aid("click_filter").click()
    expect(d.aid("errorMessage")).to_contain_text("at least 8 characters")
    d.aid("password").fill(PASSWORD)
    d.aid("verifyPassword").fill(PASSWORD + "!")
    d.aid("click_filter").click()
    expect(d.aid("errorMessage")).to_contain_text("do not match")
    d.aid("verifyPassword").fill(PASSWORD)
    assert d.aid("createAccountCheckbox").is_checked() is True
    d.aid("createAccountCheckbox").uncheck()
    d.aid("click_filter").click()
    expect(d.aid("errorMessage")).to_contain_text("terms and conditions")
    d.aid("createAccountCheckbox").check()
    d.aid("click_filter").click()
    d.wait_page("applyFlowMyInfoPage")
    assert env.site.accounts[EMAIL].verified
    assert env.hub.mailbox.snapshot() == []  # no verification mail when verification is off
    assert [e for e in env.site.events if e.startswith(("account_created", "sign_in_ok"))] == [
        f"account_created:{EMAIL}",
        f"sign_in_ok:{EMAIL}",
    ]


@browser_test
def test_create_account_rejects_an_existing_email_and_offers_sign_in(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    d.create_account()
    expect(d.aid("errorMessage")).to_contain_text("already exists")
    d.aid("signInLink").click()
    d.sign_in()
    d.wait_page("applyFlowMyInfoPage")


@browser_test
def test_email_verification_link_delivered_to_the_mailbox(start: StartFn) -> None:
    env = start(verify_email=True)
    d = env.driver()
    d.start()
    d.create_account()
    expect(d.aid("verifyEmailNotice")).to_contain_text(EMAIL)
    assert env.site.accounts[EMAIL].verified is False
    # signing in before verifying is refused
    d.sign_in()
    expect(d.aid("errorMessage")).to_contain_text("has not been verified")
    # the "Verify your email" mail carries a real URL back to this mock
    (mail,) = env.hub.mailbox.snapshot()
    assert mail.to == EMAIL and mail.subject == "Verify your email" and "workday" in mail.sender
    verifier = MailboxEmailVerifier(env.hub.mailbox)
    link = verifier.wait_for_link(
        to_address=EMAIL, subject_contains="verify", sender_contains="workday", timeout_s=2
    )
    assert link is not None and link.startswith(env.site.base_url + "/en-US/External/activate/")
    d.page.goto(link)  # the link bounces back to the apply page with a "verified" notice
    d.aid("accountVerifiedNotice").wait_for()
    assert env.site.accounts[EMAIL].verified is True
    assert d.page.url.endswith("/apply/applyManually?verified=1")
    d.sign_in()
    d.wait_page("applyFlowMyInfoPage")
    with httpx.Client(follow_redirects=False) as http:
        assert http.get(env.site.direct_url("/en-US/External/activate/bogus")).status_code == 410


@browser_test
def test_forgot_password_sends_a_reset_mail_and_the_new_password_works(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    d.aid("forgotPasswordLink").click()
    d.aid("forgotPasswordContent").wait_for()
    d.aid("email").fill(EMAIL)
    d.aid("forgotPasswordSubmitButton").click()
    expect(d.aid("forgotPasswordNotice")).to_contain_text("we have sent you a link")
    link = MailboxEmailVerifier(env.hub.mailbox).wait_for_link(
        to_address=EMAIL, subject_contains="reset", timeout_s=2
    )
    assert link is not None and "/reset/" in link
    d.page.goto(link)
    d.page.locator("input[name=password]").fill("N3w!Passw0rd#2")
    d.page.get_by_role("button", name="Reset password").click()
    d.page.wait_for_url(re.compile(r"/en-US/External$"))
    d2 = env.driver()
    d2.start()
    d2.sign_in(password=PASSWORD)
    expect(d2.aid("errorMessage")).to_contain_text("incorrect")
    d2.aid("password").fill("N3w!Passw0rd#2")
    d2.aid("click_filter").click()
    d2.wait_page("applyFlowMyInfoPage")


@browser_test
def test_captcha_on_sign_in_shows_a_visible_challenge_that_blocks_sign_in(start: StartFn) -> None:
    env = start(captcha_on_signin=True)
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start()
    d.fill_credentials()
    assert d.page.get_by_text("Verify you are human").is_visible()
    frame = d.page.locator("iframe[title='reCAPTCHA']")
    assert "recaptcha" in (frame.get_attribute("src") or "")
    d.aid("click_filter").click()
    expect(d.aid("errorMessage")).to_contain_text("verify that you are human")
    assert d.aid("applyFlowMyInfoPage").count() == 0
    box = d.page.frame_locator("iframe[title='reCAPTCHA']").locator("#recaptcha-anchor")
    box.check()  # only a human is supposed to do this
    wait_until(lambda: "captcha_solved" in env.site.events)
    d.aid("click_filter").click()
    d.wait_page("applyFlowMyInfoPage")


@browser_test
def test_signup_can_be_disabled_by_the_tenant(start: StartFn) -> None:
    env = start(allow_signup=False)
    d = env.driver()
    d.start()
    d.aid("signInContent").wait_for()
    assert d.aid("createAccountLink").count() == 0
    assert d.aid("forgotPasswordLink").count() == 1


# ============================================================================================ full flow


@browser_test
@pytest.mark.slow
def test_full_application_with_realistic_latency_and_the_recorded_submission(
    start: StartFn,
) -> None:
    env = start(latency_ms=(200, 600))
    d = env.driver()
    started = time.monotonic()
    d.start()
    d.create_account()
    d.wait_page("applyFlowMyInfoPage")
    # the progress bar lists the six standard pages of a US tenant
    steps = d.page.locator('[data-automation-id^="progressBar"][data-automation-id$="Step"]')
    assert [s.split("\n")[-1] for s in steps.all_inner_texts()] == [
        "My Information",
        "My Experience",
        "Application Questions",
        "Voluntary Disclosures",
        "Self Identify",
        "Review",
    ]
    assert "current step 1 of 6" in d.aid("progressBarActiveStep").inner_text()
    assert d.aid(NEXT).inner_text() == "Save and Continue"
    d.complete_application()
    elapsed = time.monotonic() - started
    assert elapsed > 6, "every XHR takes 200-600 ms, so the whole flow cannot be quick"
    text = d.aid("applicationSubmittedPage").inner_text()
    assert "Application Submitted" in text and "has been submitted" in text
    assert f"submitted:{JOB_ID}" in env.site.events

    (sub,) = env.site.submissions
    assert sub.site == "workday-acme"
    assert sub.path == f"/wday/app/apply/{JOB_ID}/submit"
    expected = {
        "email": [EMAIL],
        "job_id": [JOB_ID],
        "source": ["LinkedIn"],
        "previousWorker": ["No"],
        "country": ["United States of America"],
        "legalNameSection_firstName": ["Alex"],
        "legalNameSection_lastName": ["Rivera"],
        "addressSection_addressLine1": ["123 Example Street"],
        "addressSection_city": ["Austin"],
        "addressSection_countryRegion": ["Texas"],
        "addressSection_postalCode": ["78701"],
        "phone-device-type": ["Mobile"],
        "country-phone-code": ["United States of America (+1)"],
        "phone-number": ["5125550123"],
        "workExperience-1.jobTitle": ["Product Intern"],
        "workExperience-1.company": ["Example Corp"],
        "workExperience-1.location": ["Austin, TX"],
        "workExperience-1.currentlyWorkHere": ["false"],
        "workExperience-1.startDate": ["06/2025"],
        "workExperience-1.endDate": ["08/2025"],
        "workExperience-1.roleDescription": ["Built a roadmap."],
        "education-1.school": ["University of Texas at Austin"],
        "education-1.degree": ["Bachelor of Science"],
        "education-1.fieldOfStudy": ["Computer Science"],
        "education-1.gpa": ["3.8"],
        "education-1.firstYearAttended": ["2024"],
        "education-1.lastYearAttended": ["2028"],
        "skills": ["Python"],
        "websitePanelSet-1.url": ["https://example.test/alex"],
        "work_auth": ["Yes"],
        "sponsorship": ["No"],
        "relocate": ["Yes"],
        "certify": ["true"],
        "gender": ["Decline to Self Identify"],
        "race": ["Decline to Self Identify"],
        "veteran": ["I don't wish to answer"],
        "agreementCheckbox": ["true"],
        "selfIdentifiedDisabilityData--name": ["Alex Rivera"],
        "selfIdentifiedDisabilityData--dateSignedOn": ["09/29/2026"],
        "disability": ["I do not want to answer"],
    }
    assert (
        sub.fields == expected
    )  # empty optional fields (middle name, salary, ...) are not recorded
    (resume,) = sub.files
    assert (resume.field, resume.filename, resume.data) == (
        "resume",
        "Alex Rivera Resume.pdf",
        RESUME_BYTES,
    )
    assert sub.meta["email"] == EMAIL and sub.meta["apply_path"] == "applyManually"
    assert sub.meta["tenant"] == "acme" and sub.meta["job_id"] == JOB_ID
    (applied,) = env.site.submitted_applications()
    assert applied.submitted and applied.email == EMAIL


# ============================================================================================ validation


@browser_test
def test_save_and_continue_validates_required_fields_with_banner_and_inline_errors(
    start: StartFn,
) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    d.next()
    banner = d.aid("errorBanner")
    banner.wait_for()
    assert banner.locator("h3").inner_text() == "Errors Found"
    labels = {
        m.group(1)
        for text in d.banner_messages()
        if (m := re.fullmatch(r"Error - The field (.+) is required and must have a value\.", text))
    }
    assert labels == {
        "How Did You Hear About Us?",
        "Have you previously worked at Acme?",
        "First Name",
        "Last Name",
        "Address Line 1",
        "City",
        "State",
        "Postal Code",
        "Phone Device Type",
        "Phone Number",
    }
    assert d.aid("progressBarActiveStep").inner_text().endswith("My Information")  # did not advance
    assert "validation_failed:myInformation:10" in env.site.events
    field = d.aid("formField-legalName--firstName")
    inline = field.locator('[data-automation-id="errorMessage"]')
    assert "is required and must have a value" in inline.inner_text()
    assert d.aid("legalNameSection_firstName").get_attribute("aria-invalid") == "true"
    d.aid("legalNameSection_firstName").fill("Alex")  # typing clears that field's inline error only
    assert inline.count() == 0 and banner.is_visible()
    banner.get_by_role(
        "button", name=re.compile("Last Name")
    ).click()  # banner entries jump to the field
    assert (
        d.page.evaluate("document.activeElement.getAttribute('data-automation-id')")
        == "legalNameSection_lastName"
    )


@browser_test
def test_format_validation_of_postal_code_and_phone_number(start: StartFn) -> None:
    env = start()
    draft = seed_draft(env.site, "myInformation")
    draft.prefill["myInformation"] = {
        **INFO_VALUES,
        "addressSection_postalCode": "ABCDE",
        "phone-number": "12",
    }
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    assert (
        d.aid("legalNameSection_firstName").input_value() == "Alex"
    )  # prefilled from the saved draft
    d.next()
    d.aid("errorBanner").wait_for()
    assert d.banner_messages() == [
        "Error - Postal Code is not a valid ZIP code.",
        "Error - Phone Number is not a valid phone number.",
    ]
    d.text(d.aid("applyFlowMyInfoPage"), "addressSection_postalCode", "78701-1234")
    d.text(d.aid("applyFlowMyInfoPage"), "phone-number", "(512) 555-0123")
    d.next()
    d.wait_page("applyFlowMyExpPage")


@browser_test
def test_inputs_only_register_on_real_input_events(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "myInformation")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    first = d.aid("legalNameSection_firstName")
    # what a naive script does: assign .value and dispatch an event -> the app never learns about it
    first.evaluate(
        "el => { el.value = 'Ghost'; el.dispatchEvent(new Event('input', {bubbles: true})); }"
    )
    assert first.input_value() == "Ghost"
    d.next()
    d.aid("errorBanner").wait_for()
    assert any("First Name" in m for m in d.banner_messages())  # it was submitted empty
    assert first.input_value() == ""  # the re-render restored the app's own state
    # the native prototype setter + input event (the known React workaround) registers
    first.evaluate(
        "el => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
        " set.call(el, 'Alex'); el.dispatchEvent(new Event('input', {bubbles: true})); }"
    )
    d.next()
    d.aid("errorBanner").wait_for()
    assert first.input_value() == "Alex"
    assert not any("First Name" in m for m in d.banner_messages())
    # ordinary typing of course works as well
    d.aid("legalNameSection_lastName").press_sequentially("Rivera", delay=5)
    d.next()
    d.aid("errorBanner").wait_for()
    assert not any("Last Name" in m for m in d.banner_messages())


# ============================================================================================ widgets


@browser_test
def test_dropdowns_are_custom_widgets_that_ignore_select_option(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    button = d.page.locator('button[data-automation-id="phone-device-type"]')
    assert d.page.locator("select").count() == 0
    assert (
        button.get_attribute("aria-haspopup") == "listbox"
        and button.get_attribute("aria-expanded") == "false"
    )
    assert button.get_attribute("aria-label") == "Phone Device Type Select One Required"
    with pytest.raises(PlaywrightError, match="not a <select> element"):
        button.select_option("Mobile", timeout=2000)
    assert (
        d.page.get_by_role("option").count() == 0
    )  # nothing is in the DOM until the widget is opened
    button.click()
    assert button.get_attribute("aria-expanded") == "true"
    assert d.page.get_by_role("option").all_inner_texts() == ["Home", "Mobile", "Work"]
    assert d.aid("activeListContainer").locator("ul[role=listbox]").count() == 1
    assert d.aid("menuItem").first.get_attribute("data-automation-label") == "Home"
    d.page.keyboard.press("Escape")
    assert d.page.get_by_role("option").count() == 0
    d.choose(d.aid("applyFlowMyInfoPage"), "phone-device-type", "Mobile")
    assert button.inner_text() == "Mobile"
    assert button.get_attribute("aria-label") == "Phone Device Type Mobile Required"


@browser_test
def test_dropdown_keyboard_interaction_and_outside_click(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    region = d.page.locator('button[data-automation-id="addressSection_countryRegion"]')
    region.focus()
    region.press_sequentially(
        "Tex", delay=30
    )  # type-ahead opens the (remote) list and highlights the match
    d.page.locator("li[role=option].wd-active").wait_for()
    assert d.page.locator("li[role=option].wd-active").inner_text() == "Texas"
    region.press("Enter")
    assert region.inner_text() == "Texas" and d.page.get_by_role("option").count() == 0
    region.press("ArrowDown")
    d.page.get_by_role("option", name="Alabama", exact=True).wait_for()
    region.press("ArrowDown")
    region.press("Enter")
    assert region.inner_text() == "Alabama"
    d.aid("phone-device-type").click()
    assert d.page.get_by_role("option").count() == 3
    d.aid(
        "legalNameSection_lastName"
    ).click()  # clicking elsewhere closes the popup without choosing
    assert d.page.get_by_role("option").count() == 0
    assert d.aid("phone-device-type").inner_text() == "Select One"


@browser_test
def test_region_options_follow_the_selected_country(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    page = d.aid("applyFlowMyInfoPage")
    region = d.page.locator('button[data-automation-id="addressSection_countryRegion"]')
    d.choose(page, "addressSection_countryRegion", "Texas")
    d.choose(page, "countryDropdown", "Canada")
    assert region.inner_text() == "Select One"  # the dependent value is reset
    d.open_dropdown(region)
    assert "Ontario" in d.page.get_by_role("option").all_inner_texts()
    assert "Texas" not in d.page.get_by_role("option").all_inner_texts()
    d.page.get_by_role("option", name="Ontario", exact=True).click()
    d.choose(page, "countryDropdown", "Mexico")
    assert region.count() == 0  # no region list for this country, so the field disappears
    d.choose(page, "countryDropdown", "United States of America")
    assert region.inner_text() == "Select One"
    d.open_dropdown(d.aid("countryDropdown"))
    similar = d.page.get_by_role("option", name=re.compile("^United States"))
    assert similar.all_inner_texts() == [
        "United States Minor Outlying Islands",
        "United States of America",
    ]


@browser_test
def test_element_ids_change_on_every_render_and_old_handles_go_stale(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    first = d.aid("legalNameSection_firstName")
    before = first.get_attribute("id")
    assert before and re.fullmatch(r"input-\d+", before)
    assert d.page.locator(f'label[for="{before}"]').inner_text().startswith("First Name")
    handle = first.element_handle()
    d.choose(
        d.aid("applyFlowMyInfoPage"), "phone-device-type", "Mobile"
    )  # any dropdown pick re-renders the page
    after = first.get_attribute("id")
    assert after != before
    assert d.page.locator(f'label[for="{after}"]').inner_text().startswith("First Name")
    assert handle.evaluate("el => el.isConnected") is False  # the old element was thrown away


@browser_test
def test_source_prompt_is_a_hierarchical_multiselect_that_searches_on_enter(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    box = d.aid("source--source")
    options = d.aid("promptOption")
    assert box.get_attribute("id") == "source--source"
    box.fill("Indeed")  # typing alone opens nothing
    assert options.count() == 0
    box.fill("")
    box.click()  # clicking opens the top level of the tree
    options.first.wait_for()
    assert [o.get_attribute("data-automation-label") for o in options.all()] == [
        "Company Website",
        "Job Board",
        "University / Campus",
        "Employee Referral",
        "Social Media",
        "Other",
    ]
    d.page.locator('[data-automation-label="Job Board"]').click()  # a folder: descend
    d.page.locator('[data-automation-label="Indeed"]').wait_for()
    assert [o.get_attribute("data-automation-label") for o in options.all()] == [
        "Indeed",
        "LinkedIn",
        "Glassdoor",
        "ZipRecruiter",
    ]
    d.page.get_by_role("button", name=re.compile("Job Board")).click()  # back
    d.page.locator('[data-automation-label="Company Website"]').wait_for()
    d.page.locator('[data-automation-label="Company Website"]').click()
    pills = d.aid("selectedItem")
    assert pills.all_inner_texts() == ["Company Website\n×"] and options.count() == 0
    d.aid("DELETE_charm").click()
    assert pills.count() == 0
    box.fill("Link")
    box.press("Enter")  # Enter runs the search across every level of the tree
    options.first.wait_for()
    assert [o.get_attribute("data-automation-label") for o in options.all()] == ["LinkedIn"]
    options.first.click()
    assert pills.all_inner_texts() == ["LinkedIn\n×"]
    box.click()
    d.page.locator('[data-automation-label="Other"]').click()
    assert pills.all_inner_texts() == [
        "Other\n×"
    ]  # single-select: the new choice replaces the old one


@browser_test
def test_search_prompt_needs_enter_and_supports_several_values(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    group = d.add_education()
    box = group.locator('input[data-automation-id="fieldOfStudy"]')
    options = d.aid("promptOption")
    box.click()
    box.fill("Comp")
    d.page.wait_for_timeout(700)
    assert options.count() == 0  # no search-as-you-type on this prompt
    box.press("Enter")
    options.first.wait_for()
    assert [o.get_attribute("data-automation-label") for o in options.all()] == [
        "Computer Engineering",
        "Computer Science",
    ]
    d.page.locator('[data-automation-label="Computer Science"]').click()
    box.fill("Stat")
    box.press("Enter")
    d.page.locator('[data-automation-label="Statistics"]').click()
    pills = d.aid("selectedItem", group)
    assert [t.split("\n")[0] for t in pills.all_inner_texts()] == ["Computer Science", "Statistics"]
    d.aid("DELETE_charm", group).first.click()
    assert [t.split("\n")[0] for t in pills.all_inner_texts()] == ["Statistics"]
    box.fill("zzzz")
    box.press("Enter")
    d.page.get_by_text("No Items.").wait_for()


@browser_test
def test_school_typeahead_only_accepts_a_selected_option(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    group = d.add_education()
    box = group.locator('input[data-automation-id="school"]')
    options = d.aid("promptOption")
    box.click()
    box.press_sequentially(
        "University of Texas", delay=20
    )  # suggestions arrive by themselves (debounced)
    options.first.wait_for()
    labels = [o.get_attribute("data-automation-label") for o in options.all()]
    assert "University of Texas at Austin" in labels and "University of Texas at Dallas" in labels
    assert "Texas A&M University" not in labels and len(labels) == 7
    box.fill(
        "The University of Texas at Austin"
    )  # the catalogue has no leading "The": nothing matches
    box.press("Enter")
    d.page.get_by_text("No Items.").wait_for()
    box.fill("University of Texas at Austin")
    options.first.wait_for()
    d.choose(
        group, "degree", "Bachelor of Science"
    )  # moving on without picking discards the typed text
    assert box.input_value() == "" and d.aid("selectedItem", group).count() == 0
    d.next()
    d.aid("errorBanner").wait_for()
    assert (
        "Error - The field School or University is required and must have a value."
        in d.banner_messages()
    )
    box.click()
    box.fill("Texas A&M")
    d.page.locator('[data-automation-label="Texas A&M University"]').click()
    assert d.aid("selectedItem", group).inner_text().startswith("Texas A&M University")


@browser_test
def test_work_experience_repeater_dates_and_currently_work_here(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    section = d.aid("workExperienceSection")
    add = d.aid("add-button", section)
    assert add.inner_text() == "Add" and d.aid("workExperience-1").count() == 0
    first = d.add_work_experience()
    assert add.inner_text() == "Add Another"
    d.text(first, "jobTitle", "First Job")
    second = d.add_work_experience()
    d.text(second, "jobTitle", "Second Job")
    assert d.aid("workExperience-2").is_visible()
    assert first.get_by_role("heading").inner_text() == "Work Experience 1"
    d.aid("panel-set-delete-button", first).click()
    assert d.aid("workExperience-2").count() == 0
    assert (
        d.aid("jobTitle", d.aid("workExperience-1")).input_value() == "Second Job"
    )  # entries are renumbered
    group = d.aid("workExperience-1")
    month = d.aid("startDate-dateSectionMonth-input", group)
    year = d.aid("startDate-dateSectionYear-input", group)
    month.click()
    d.page.keyboard.type("0a6")  # digits only; the second digit hands focus to the year box
    assert month.input_value() == "06"
    assert (
        d.page.evaluate("document.activeElement.getAttribute('data-automation-id')")
        == "startDate-dateSectionYear-input"
    )
    d.page.keyboard.type("2o025x")
    assert year.input_value() == "2025"
    assert (
        month.get_attribute("placeholder") == "MM" and year.get_attribute("placeholder") == "YYYY"
    )
    assert d.aid("endDate-dateSectionMonth-input", group).count() == 1
    group.get_by_label("I currently work here").check()
    assert (
        d.aid("endDate-dateSectionMonth-input", group).count() == 0
    )  # the end date is dropped from the form
    group.get_by_label("I currently work here").uncheck()
    assert d.aid("endDate-dateSectionMonth-input", group).count() == 1
    d.date(group, "endDate", m="05", y="2025")
    d.text(group, "company", "Example Corp")
    d.next()
    d.aid("errorBanner").wait_for()
    assert "Error - The To date must not be earlier than the From date." in d.banner_messages()
    d.date(group, "endDate", m="13")
    d.next()
    d.aid("errorBanner").wait_for()
    assert "Error - To: the month must be between 1 and 12." in d.banner_messages()


# ============================================================================================ uploads


@browser_test
def test_resume_upload_hidden_input_success_row_and_delete(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    section = d.aid("resumeSection")
    file_input = section.locator('input[data-automation-id="file-upload-input-ref"]')
    assert (
        file_input.count() == 1 and not file_input.is_visible()
    )  # hidden, driven with set_input_files
    assert d.aid("file-upload-successful").count() == 0
    with (
        d.page.expect_file_chooser() as chooser
    ):  # the visible "Select file" button opens the native chooser
        d.aid("select-files", section).click()
    chooser.value.set_files(str(d.resume))
    row = d.aid("file-upload-successful", section)
    row.wait_for()
    assert d.aid("file-upload-item-name", section).inner_text() == "Alex Rivera Resume.pdf"
    assert "Successfully Uploaded!" in row.inner_text()
    draft = env.site.drafts[(EMAIL, JOB_ID)]
    assert draft.files["resume"].data == RESUME_BYTES
    assert file_input.count() == 0  # the drop zone is replaced by the uploaded-file row
    d.aid("delete-file", section).click()
    d.aid("file-upload-drop-zone", section).wait_for()
    assert "resume" not in draft.files and env.site.events[-1] == "upload_deleted:resume"


@browser_test
def test_resume_is_required_and_bad_files_are_rejected(start: StartFn) -> None:
    env = start(max_upload_bytes=1024)
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    section = d.aid("resumeSection")
    file_input = section.locator('input[data-automation-id="file-upload-input-ref"]')
    bad = d.resume.with_name("payload.exe")
    bad.write_bytes(b"MZ")
    file_input.set_input_files(str(bad))
    expect(d.aid("errorMessage", section)).to_contain_text("file type is not supported")
    assert d.aid("file-upload-successful").count() == 0
    big = d.resume.with_name("Big Resume.pdf")
    big.write_bytes(b"%PDF" + b"x" * 2048)
    file_input.set_input_files(str(big))
    expect(d.aid("errorMessage", section)).to_contain_text("too large")
    empty = d.resume.with_name("Empty.pdf")
    empty.write_bytes(b"")
    file_input.set_input_files(str(empty))
    expect(d.aid("errorMessage", section)).to_contain_text("is empty")
    assert env.site.drafts[(EMAIL, JOB_ID)].files == {}
    d.fill_work_experience()
    d.fill_education()
    d.next()  # everything but the resume is filled in
    d.aid("errorBanner").wait_for()
    assert d.banner_messages() == ["Error - The field Resume/CV is required and must have a value."]
    unicode_name = d.resume.with_name("Résumé – Alex Rivera.pdf")
    unicode_name.write_bytes(RESUME_BYTES)
    d.upload_resume(unicode_name)
    assert d.aid("file-upload-item-name").inner_text() == "Résumé – Alex Rivera.pdf"
    d.next()
    d.wait_page("applyFlowPrimaryQuestionsPage")
    assert env.site.drafts[(EMAIL, JOB_ID)].files["resume"].filename == "Résumé – Alex Rivera.pdf"


@browser_test
def test_cover_letter_slot_is_a_second_hidden_file_input(start: StartFn) -> None:
    env = start(cover_letter_slot=True)
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    inputs = d.page.locator('input[data-automation-id="file-upload-input-ref"]')
    assert inputs.count() == 2  # scope by section: resumeSection / coverLetterSection
    cover = d.resume.with_name("Cover Letter.pdf")
    cover.write_bytes(b"%PDF-1.4 cover letter")
    d.aid("coverLetterSection").locator("input[type=file]").set_input_files(str(cover))
    d.aid("file-upload-successful", d.aid("coverLetterSection")).wait_for()
    assert d.aid("file-upload-successful", d.aid("resumeSection")).count() == 0
    d.fill_my_experience()
    d.next()
    d.wait_page("applyFlowPrimaryQuestionsPage")
    finish_from_questions(d)
    (sub,) = env.site.submissions
    assert {f.field: (f.filename, f.data) for f in sub.files} == {
        "resume": ("Alex Rivera Resume.pdf", RESUME_BYTES),
        "coverLetter": ("Cover Letter.pdf", b"%PDF-1.4 cover letter"),
    }


# ============================================================================================ questions


CUSTOM_QUESTIONS = (
    MockQuestion("nick", "Preferred nickname", "text", max_length=8),
    MockQuestion("why", "Why are you interested in this role?", "textarea", max_length=40),
    MockQuestion(
        "work_auth",
        "Are you legally authorized to work in the United States?",
        "select",
        ("Yes", "No"),
    ),
    MockQuestion("relocate", "Are you willing to relocate?", "radio", ("Yes", "No")),
    MockQuestion("bgcheck", "I agree to a background screening.", "checkbox"),
    MockQuestion(
        "langs", "Which languages do you speak?", "checkbox", ("English", "Spanish", "Hindi")
    ),
    MockQuestion(
        "interests", "Areas of interest", "multiselect", ("Strategy", "Analytics", "Operations")
    ),
    MockQuestion("salary", "Hourly compensation expectation", "text", required=False),
    MockQuestion(
        "gender", "Gender", "select", ("Male", "Female", "Decline to self-identify"), required=False
    ),
)


@browser_test
def test_application_questions_render_every_kind_and_are_recorded(start: StartFn) -> None:
    job = MockJob(id=JOB_ID, title="Strategy Intern - Summer 2027", questions=CUSTOM_QUESTIONS)
    env = start(jobs=[job])
    seed_draft(env.site, "applicationQuestions")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowPrimaryQuestionsPage")
    blocks = d.page.locator("[data-automation-id^='formField-']")
    assert (
        blocks.count() == 8
    )  # the standard EEO question lives on the Voluntary Disclosures page instead
    assert all(
        re.fullmatch(r"formField-[0-9a-f]{32}", b.get_attribute("data-automation-id") or "")
        for b in blocks.all()
    )
    assert all(
        str(job.id) not in (b.get_attribute("data-automation-id") or "") for b in blocks.all()
    )
    nick = d.field("Preferred nickname").locator("input")
    assert nick.get_attribute("maxlength") == "8" and nick.get_attribute("type") == "text"
    why = d.field("Why are you interested").locator("textarea")
    assert why.get_attribute("maxlength") == "40"
    d.next()  # every required question is reported, optional ones are not
    d.aid("errorBanner").wait_for()
    assert d.banner_messages() == [
        f"Error - The field {label} is required and must have a value."
        for label in (
            "Preferred nickname",
            "Why are you interested in this role?",
            "Are you legally authorized to work in the United States?",
            "Are you willing to relocate?",
            "I agree to a background screening.",
            "Which languages do you speak?",
            "Areas of interest",
        )
    ]
    nick.fill("TooLongNickname")  # the browser enforces maxlength, like on the real form
    assert nick.input_value() == "TooLongN"
    why.fill("x" * 100)
    assert len(why.input_value()) == 40
    why.fill("Because I like strategy")
    d.field("legally authorized").locator("button[aria-haspopup=listbox]").click()
    assert d.page.get_by_role("option").all_inner_texts() == ["Select One", "Yes", "No"]
    d.page.get_by_role("option", name="Yes", exact=True).click()
    d.field("willing to relocate").get_by_label("No", exact=True).check()
    d.page.get_by_label("I agree to a background screening").check()
    languages = d.field("Which languages")
    languages.get_by_label("English").check()
    languages.get_by_label("Hindi").check()
    d.prompt(
        d.field("Areas of interest"), _hex_id(d.field("Areas of interest")), "Ana", "Analytics"
    )
    d.next()
    d.wait_page("applyFlowVoluntaryDisclosuresPage")
    d.fill_disclosures()
    d.next()
    d.wait_page("applyFlowSelfIdentifyPage")
    d.fill_self_identify()
    d.next()
    d.wait_page("applyFlowReviewPage")
    reviewed = d.aid("reviewRow").all_inner_texts()
    assert any(
        r.startswith("Which languages do you speak?") and "English, Hindi" in r for r in reviewed
    )
    d.next()
    d.aid("applicationSubmittedPage").wait_for()
    (sub,) = env.site.submissions
    for key, value in {
        "nick": ["TooLongN"],
        "why": ["Because I like strategy"],
        "work_auth": ["Yes"],
        "relocate": ["No"],
        "bgcheck": ["true"],
        "langs": ["English", "Hindi"],
        "interests": ["Analytics"],
    }.items():
        assert sub.fields[key] == value, key
    assert "salary" not in sub.fields  # optional and left empty


def _hex_id(block: Locator) -> str:
    return (block.get_attribute("data-automation-id") or "").removeprefix("formField-")


@browser_test
def test_eeo_pages_offer_a_decline_option_the_shared_matcher_can_find(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "voluntaryDisclosures")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowVoluntaryDisclosuresPage")
    page = d.aid("applyFlowVoluntaryDisclosuresPage")
    for name, options in (
        ("gender", GENDERS),
        ("ethnicity", ETHNICITIES),
        ("veteranStatus", VETERAN_STATUSES),
    ):
        page.locator(f'button[data-automation-id="{name}"]').click()
        shown = d.page.get_by_role("option").all_inner_texts()
        assert shown == ["Select One", *options]
        wanted = best_option(DECLINE, shown)
        assert wanted is not None and wanted != "Select One"
        d.page.get_by_role("option", name=wanted, exact=True).click()
    d.next()
    d.aid("errorBanner").wait_for()
    assert d.banner_messages() == [
        "Error - The field I have read and consent to the terms and conditions of this application. is required and must have a value."
    ]
    d.aid("agreementCheckbox", page).check()
    d.next()
    d.wait_page("applyFlowSelfIdentifyPage")
    radios = d.aid("selfIdentifiedDisabilityData--disabilityStatus").all()
    assert [r.get_attribute("value") for r in radios] == DISABILITY_STATUSES
    assert best_option(DECLINE, DISABILITY_STATUSES) == "I do not want to answer"
    d.next()
    d.aid("errorBanner").wait_for()
    assert d.banner_messages() == [
        "Error - The field Name is required and must have a value.",
        "Error - The field Date is required and must have a value.",
        "Error - The field Please check one of the boxes below: is required and must have a value.",
    ]
    d.date(
        d.aid("applyFlowSelfIdentifyPage"),
        "selfIdentifiedDisabilityData--dateSignedOn",
        m="02",
        d="30",
        y="2026",
    )
    d.next()
    d.aid("errorBanner").wait_for()
    assert "Error - Date is not a valid date." in d.banner_messages()


# ============================================================================================ session + state


@browser_test
def test_session_timeout_drops_the_user_back_to_sign_in_and_the_saved_steps_survive(
    start: StartFn,
) -> None:
    env = start()
    seed_draft(env.site, "applicationQuestions")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowPrimaryQuestionsPage")
    assert d.aid("progressBarCompletedStep").count() == 2
    d.dropdown_question("legally authorized", "Yes")  # an answer that has not been saved yet
    url = d.page.url
    env.site.expire_sessions()  # the server forgets everybody at their next request
    d.next()
    d.aid("signInContent").wait_for()
    notice = d.aid("sessionExpiredNotice")
    assert "session has expired" in notice.inner_text()
    assert d.aid("applyFlowPrimaryQuestionsPage").count() == 0 and d.page.url == url
    assert d.aid("jobPostingHeader").is_visible()  # the posting is still behind the dialog
    assert f"session_expired:{EMAIL}" in env.site.events
    d.sign_in()
    d.wait_page("applyFlowPrimaryQuestionsPage")  # resumes on the page that was open
    assert d.aid("progressBarCompletedStep").count() == 2
    assert (
        d.field("legally authorized").locator("button").inner_text() == "Select One"
    )  # unsaved input is gone
    finish_from_questions(d)
    assert len(env.site.submissions) == 1


@browser_test
def test_timed_session_expiry_happens_once_mid_flow(start: StartFn) -> None:
    env = start(session_expires_after_s=0.6)
    seed_draft(env.site, "applicationQuestions")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowPrimaryQuestionsPage")
    d.fill_default_questions()
    time.sleep(0.8)  # the session outlives its timeout while the user is busy filling in the form
    d.next()
    d.aid("sessionExpiredNotice").wait_for()
    d.sign_in()
    d.wait_page("applyFlowPrimaryQuestionsPage")
    d.fill_default_questions()
    time.sleep(0.8)  # max_session_expiries=1: the second session no longer expires
    d.next()
    d.wait_page("applyFlowVoluntaryDisclosuresPage")
    assert env.site.state["timed_expiries"] == 1


@browser_test
def test_page_reload_after_expiry_shows_the_sign_in_notice(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "applicationQuestions")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowPrimaryQuestionsPage")
    env.site.expire_sessions()
    d.page.reload()
    d.aid("signInContent").wait_for()
    assert "session has expired" in d.aid("sessionExpiredNotice").inner_text()


@browser_test
def test_back_button_restores_saved_values_and_first_page_has_none(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "applicationQuestions")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowPrimaryQuestionsPage")
    d.aid("bottom-navigation-back-button").click()
    d.wait_page("applyFlowMyExpPage")
    assert d.aid("jobTitle", d.aid("workExperience-1")).input_value() == "Product Intern"
    assert d.aid("file-upload-item-name").inner_text() == "Alex Rivera Resume.pdf"
    assert d.aid("progressBarActiveStep").inner_text().endswith("My Experience")
    d.aid("bottom-navigation-back-button").click()
    d.wait_page("applyFlowMyInfoPage")
    assert d.aid("legalNameSection_lastName").input_value() == "Rivera"
    assert d.aid("selectedItem").inner_text().startswith("Company Website")
    assert d.aid("bottom-navigation-back-button").count() == 0
    d.next()  # saving again moves forward one page at a time
    d.wait_page("applyFlowMyExpPage")


@browser_test
def test_review_page_summarises_every_saved_page_and_offers_submit(start: StartFn) -> None:
    env = start()
    seed_draft(env.site, "review")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowReviewPage")
    assert d.aid(NEXT).inner_text() == "Submit"
    assert d.aid("progressBarCompletedStep").count() == 5
    titles = [t.inner_text() for t in d.aid("reviewSection").locator("h3").all()]
    assert titles == [
        "My Information",
        "My Experience",
        "Application Questions",
        "Voluntary Disclosures",
        "Self Identify",
    ]
    rows = {
        r.locator("dt").inner_text(): r.locator("dd").inner_text() for r in d.aid("reviewRow").all()
    }
    assert rows["First Name"] == "Alex" and rows["Last Name"] == "Rivera"
    assert rows["Work Experience 1: Job Title"] == "Product Intern"
    assert rows["Work Experience 1: From"] == "06/2025"
    assert rows["Education 1: School or University"] == "University of Texas at Austin"
    assert rows["Resume/CV"] == "Alex Rivera Resume.pdf"
    assert rows["I certify that the information provided is true and complete."] == "true"
    d.next()
    d.aid("applicationSubmittedPage").wait_for()
    (sub,) = env.site.submissions
    assert sub.first("legalNameSection_firstName") == "Alex"


@browser_test
def test_repeat_application_with_the_same_account_is_reported_as_already_applied(
    start: StartFn,
) -> None:
    env = start()
    seed_draft(env.site, "review")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowReviewPage")
    d.next()
    d.aid("applicationSubmittedPage").wait_for()
    assert len(env.site.submissions) == 1
    second = env.driver()  # a brand new browser context: no cookies, the account is the only link
    second.start()
    second.sign_in()
    applied = second.aid("alreadyApplied")
    applied.wait_for()
    assert "You have already applied for this job" in applied.inner_text()
    assert second.aid(NEXT).count() == 0 and second.aid("progressBar").count() == 0
    reply = second.page.evaluate(  # page.request resolves DNS in Node, which cannot see *.localhost
        """async (url) => (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({step: 'review', values: {}})})).json()""",
        f"/wday/app/apply/{JOB_ID}/submit",
    )
    assert reply["view"]["view"] == "alreadyApplied"  # replaying the final request records nothing
    assert len(env.site.submissions) == 1
    assert "submit_rejected_already_applied:" + EMAIL in env.site.events
    third = env.driver()  # deep link to the apply page while signed in shows the same state
    third.page.goto(env.site.apply_url(path="applyManually"))
    third.sign_in()
    third.aid("alreadyApplied").wait_for()
    other = env.site.add_account("sam.lee@example.test", PASSWORD)
    assert other.email not in {d.email for d in env.site.submitted_applications()}


@browser_test
def test_use_my_last_application_copies_the_previous_answers(start: StartFn) -> None:
    jobs = [
        MockJob(id=JOB_ID, title="Product Management Intern - Summer 2027"),
        MockJob(id="R0000002", title="Operations Intern - Summer 2027", questions=()),
        MockJob(id="R0000003", title="Strategy Intern - Summer 2027", questions=()),
    ]
    env = start(jobs=jobs)
    seed_draft(env.site, "review")
    first = env.driver()
    sign_in_to_wizard(first, "applyFlowReviewPage")
    first.next()
    first.aid("applicationSubmittedPage").wait_for()
    d = env.driver()
    d.open_job("R0000002")
    d.aid("adventureButton").click()
    d.aid("applyManually").wait_for()
    assert d.aid("useMyLastApplication").count() == 0  # only offered to a signed-in candidate
    d.aid("applyManually").click()
    d.sign_in()
    d.wait_page("applyFlowMyInfoPage")
    d.open_job("R0000003")  # still signed in, no draft for this posting yet: three choices
    d.aid("adventureButton").click()
    d.aid("useMyLastApplication").wait_for()
    assert d.aid("autofillWithResume").count() == 1 and d.aid("applyManually").count() == 1
    d.aid("useMyLastApplication").click()
    d.wait_page("applyFlowMyInfoPage")
    assert d.aid("legalNameSection_firstName").input_value() == "Alex"
    assert d.aid("addressSection_countryRegion").inner_text() == "Texas"
    for page_aid in (
        "applyFlowMyExpPage",
        "applyFlowVoluntaryDisclosuresPage",
        "applyFlowSelfIdentifyPage",
    ):
        d.next()
        d.wait_page(page_aid)
        if page_aid == "applyFlowMyExpPage":
            assert d.aid("file-upload-item-name").inner_text() == "Alex Rivera Resume.pdf"
    d.next()
    d.wait_page("applyFlowReviewPage")
    d.next()
    d.aid("applicationSubmittedPage").wait_for()
    second = env.site.submissions[-1]
    assert (
        second.meta["apply_path"] == "useMyLastApplication" and second.meta["job_id"] == "R0000003"
    )
    assert (
        second.first("legalNameSection_firstName") == "Alex"
        and second.first("gender") == "Decline to Self Identify"
    )
    assert (
        "work_auth" not in second.fields
    )  # answers to job specific questions are not carried over
    (resume,) = second.files
    assert resume.data == RESUME_BYTES


@browser_test
def test_autofill_with_resume_prefills_junk_and_is_recorded_as_such(start: StartFn) -> None:
    env = start()
    env.site.add_account(EMAIL, PASSWORD)
    d = env.driver()
    d.start(path="autofillWithResume")
    d.sign_in()
    d.wait_page("applyFlowMyInfoPage")
    assert d.aid("legalNameSection_firstName").input_value() == "Autofilled"
    assert d.aid("legalNameSection_lastName").input_value() == "Applicant"
    assert "choose:autofillWithResume" in env.site.events
    assert env.site.drafts[(EMAIL, JOB_ID)].path == "autofillWithResume"


# ============================================================================================ realism knobs


@browser_test
def test_every_step_transition_costs_two_simulated_round_trips(start: StartFn) -> None:
    env = start(latency_ms=(200, 600))
    draft = seed_draft(env.site, "myInformation")
    draft.prefill["myInformation"] = INFO_VALUES
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyInfoPage")
    began = time.monotonic()
    d.aid(NEXT).click()
    d.aid(
        "applyFlowMyExpPage"
    ).wait_for()  # the frame (progress bar + heading) comes back with the save
    frame_after = time.monotonic() - began
    assert (
        d.aid(NEXT).count() == 0 and d.aid("workExperienceSection").count() == 0
    )  # ... the form does not yet
    d.aid(NEXT).wait_for()
    form_after = time.monotonic() - began
    assert frame_after >= 0.19 and form_after >= 0.38 and form_after < 5


@browser_test
def test_injected_faults_first_request_503_and_slow_job_xhr(start: StartFn) -> None:
    env = start()
    env.site.faults.fail_once.add("/wday/app/apply/")
    env.site.faults.delay_s["/wday/cxs/"] = 1.0
    d = env.driver()
    began = time.monotonic()
    d.page.goto(env.site.job_url() + "?source=Company%20Website")  # tracking parameters are ignored
    d.aid("adventureButton").wait_for()
    assert time.monotonic() - began >= 0.95
    d.aid("adventureButton").click()  # the first request of the flow fails with HTTP 503
    expect(d.aid("errorMessage")).to_contain_text("technical difficulties")
    assert d.aid("applyManually").count() == 0
    d.aid("adventureButton").click()  # a second attempt goes through
    d.aid("applyManually").wait_for()
    assert d.aid("errorMessage").count() == 0


@browser_test
def test_cookie_banner_overlaps_the_page_until_accepted(start: StartFn) -> None:
    env = start(cookie_banner=True)
    d = env.driver()
    d.open_job()
    accept = d.aid("legalNoticeAcceptButton")
    accept.wait_for()
    box = d.aid("legalNotice").bounding_box()
    assert (
        box is not None and box["y"] + box["height"] >= 999
    )  # pinned to the bottom of the 1000px viewport
    accept.click()
    assert accept.count() == 0
    d.page.reload()
    d.aid("adventureButton").wait_for()
    assert d.aid("legalNoticeAcceptButton").count() == 0  # remembered in the browser profile


@browser_test
def test_relaxed_tenant_options_let_a_bare_experience_page_through(start: StartFn) -> None:
    env = start(
        resume_required=False,
        education_required=False,
        source_required=False,
        require_terms=False,
        require_consent=False,
    )
    seed_draft(env.site, "myExperience")
    d = env.driver()
    sign_in_to_wizard(d, "applyFlowMyExpPage")
    d.next()
    d.wait_page("applyFlowPrimaryQuestionsPage")
    d.fill_default_questions()
    d.next()
    d.wait_page("applyFlowVoluntaryDisclosuresPage")
    assert d.aid("agreementCheckbox").count() == 0
    for name, option in (
        ("gender", "Decline to Self Identify"),
        ("ethnicity", "Decline to Self Identify"),
        ("veteranStatus", "I don't wish to answer"),
    ):
        d.choose(d.aid("applyFlowVoluntaryDisclosuresPage"), name, option)
    d.next()
    d.wait_page("applyFlowSelfIdentifyPage")
    d.fill_self_identify()
    d.next()
    d.wait_page("applyFlowReviewPage")
    d.next()
    d.aid("applicationSubmittedPage").wait_for()
    (sub,) = env.site.submissions
    assert (
        sub.files == []
        and "education-1.school" not in sub.fields
        and "agreementCheckbox" not in sub.fields
    )


@browser_test
def test_create_account_without_consent_checkbox_when_the_tenant_has_none(start: StartFn) -> None:
    env = start(require_consent=False)
    d = env.driver()
    d.start()
    d.create_account(consent=False, submit=False)
    assert d.aid("createAccountCheckbox").count() == 0
    d.aid("click_filter").click()
    d.wait_page("applyFlowMyInfoPage")


@browser_test
def test_the_mock_never_talks_to_another_host(start: StartFn) -> None:
    env = start(captcha_on_signin=True)
    seed_draft(env.site, "review")
    d = env.driver()
    urls: list[str] = []
    d.page.on("request", lambda r: urls.append(r.url))
    d.start()
    d.fill_credentials()
    d.page.frame_locator("iframe[title='reCAPTCHA']").locator("#recaptcha-anchor").check()
    wait_until(lambda: "captcha_solved" in env.site.events)
    d.aid("click_filter").click()
    d.wait_page("applyFlowReviewPage")
    hosts = {re.match(r"https?://([^/:]+)", u).group(1) for u in urls if u.startswith("http")}  # type: ignore[union-attr]
    assert hosts == {"acme.wd5.myworkdayjobs.com.localhost"}


# ============================================================================================ tenant options (http)


def test_skipped_pages_and_questionless_jobs_shorten_the_progress_bar() -> None:
    job = MockJob(id="R1", title="Intern", questions=())
    site = make_site(
        "acme", [job], skip_steps=("selfIdentify", "myInformation", "review"), latency_ms=0
    )
    site.add_account(EMAIL, PASSWORD)
    hub = MockHub()
    hub.add(site)
    with hub, httpx.Client(base_url=site.direct_url("")) as http:
        http.post("/wday/app/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
        state = http.post("/wday/app/apply/R1/state", json={"path": "applyManually"}).json()
        # the first and last page can never be skipped; a job without questions has no question page
        assert [s["label"] for s in state["steps"]] == [
            "My Information",
            "My Experience",
            "Voluntary Disclosures",
            "Review",
        ]
        assert [s["state"] for s in state["steps"]] == [
            "active",
            "inactive",
            "inactive",
            "inactive",
        ]
        page = http.get("/wday/app/apply/R1/page", params={"step": "myInformation"}).json()
        assert {f["id"] for f in page["schema"]} >= {
            "source--source",
            "legalNameSection_firstName",
            "phone-number",
        }
        stale = http.get("/wday/app/apply/R1/page", params={"step": "review"}).json()
        assert (
            stale["view"]["current"] == "myInformation"
        )  # asking for another page re-syncs with the server
        wrong = http.post("/wday/app/apply/R1/save", json={"step": "review", "values": {}}).json()
        assert wrong["ok"] is True and wrong["view"]["current"] == "myInformation"
        skipped = http.post(
            "/wday/app/apply/R1/submit"
        ).json()  # cannot submit before the review page
        assert skipped["view"]["current"] == "myInformation"
    assert site.submissions == []


def test_upload_endpoint_validates_slots_and_types() -> None:
    site = make_site("acme", latency_ms=0)
    site.add_account(EMAIL, PASSWORD)
    hub = MockHub()
    hub.add(site)
    with hub, httpx.Client(base_url=site.direct_url("")) as http:
        http.post("/wday/app/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
        http.post(f"/wday/app/apply/{JOB_ID}/state", json={"path": "applyManually"})
        ok = http.post(
            f"/wday/app/apply/{JOB_ID}/upload/resume",
            files={"file": ("cv.docx", b"docx bytes", "application/msword")},
        )
        assert ok.json() == {"ok": True, "file": {"name": "cv.docx", "size": 10}}
        assert (
            http.post(
                f"/wday/app/apply/{JOB_ID}/upload/coverLetter", files={"file": ("c.pdf", b"x")}
            ).status_code
            == 404
        )
        assert (
            http.post(
                f"/wday/app/apply/{JOB_ID}/upload/passport", files={"file": ("c.pdf", b"x")}
            ).status_code
            == 404
        )
        bad = http.post(
            f"/wday/app/apply/{JOB_ID}/upload/resume", files={"file": ("cv.exe", b"x")}
        ).json()
        assert bad["ok"] is False and "not supported" in bad["error"]
        assert (
            site.drafts[(EMAIL, JOB_ID)].files["resume"].filename == "cv.docx"
        )  # a rejected upload keeps the old file
        assert http.delete(f"/wday/app/apply/{JOB_ID}/upload/resume").json() == {"ok": True}
        assert site.drafts[(EMAIL, JOB_ID)].files == {}
