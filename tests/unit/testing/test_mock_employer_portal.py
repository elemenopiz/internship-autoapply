"""Browser + HTTP tests of the label-only employer portal mock (testing/mock_ats/employer_portal.py)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

from autoapply.testing.mock_ats.base import MockHub, MockJob, MockQuestion
from autoapply.testing.mock_ats.employer_portal import EmployerPortalSite, make_site

LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost, EXCLUDE 127.0.0.1"
RESUME = b"%PDF-1.4\n% mock resume\n"
COVER = b"%PDF-1.4\n% mock cover letter\n"
browser_test = pytest.mark.browser
expect.set_options(timeout=15_000)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        chromium = pw.chromium.launch(headless=True, args=[LOOPBACK_ONLY])
        yield chromium
        chromium.close()


@dataclass
class Env:
    site: EmployerPortalSite
    browser: Browser
    tmp: Path
    contexts: list[BrowserContext] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def page(self) -> Page:
        ctx = self.browser.new_context(viewport={"width": 1200, "height": 900})
        ctx.set_default_timeout(10_000)
        self.contexts.append(ctx)
        page = ctx.new_page()
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        return page

    def file(self, name: str, data: bytes) -> str:
        path = self.tmp / name
        path.write_bytes(data)
        return str(path)


StartFn = Callable[..., Env]


@pytest.fixture
def start(browser: Browser, tmp_path: Path) -> Iterator[StartFn]:
    hubs: list[MockHub] = []
    envs: list[Env] = []

    def _start(company: str = "acme", jobs: list[MockJob] | None = None, **options: Any) -> Env:
        site = make_site(company, jobs, **options)
        hub = MockHub()
        hub.add(site)
        hub.start()
        hubs.append(hub)
        envs.append(Env(site, browser, tmp_path))
        return envs[-1]

    yield _start
    for env in envs:
        for ctx in env.contexts:
            ctx.close()
    for hub in hubs:
        hub.stop()
    for env in envs:
        assert not env.errors, env.errors


def fill_page1(env: Env, page: Page, *, phone: str = "5125550123", zip_code: str = "94538") -> None:
    """Fields found by label / aria-label / placeholder / caption only, like a generic form filler must."""
    page.get_by_label("First name").fill("Alex")  # <label for>
    page.get_by_label("Last name / surname").fill("Rivera")  # aria-label only
    page.get_by_placeholder("Email address").fill("alex.rivera@example.test")  # placeholder only
    page.get_by_placeholder("(555) 555-0123").fill(phone)  # caption is a detached <span>
    page.get_by_label("Street address").fill("123 Example Street")  # wrapping <label>
    page.get_by_label(re.compile(r"^City")).fill("Fremont")
    page.locator("select").first.select_option(
        "California"
    )  # first option is the caption "State / Province"
    page.get_by_placeholder("ZIP / Postal code").fill(zip_code)
    page.get_by_label("LinkedIn profile").fill(
        "https://www.linkedin.com/in/alex-rivera-example"
    )  # aria-labelledby
    page.get_by_label("Upload your resume").set_input_files(
        env.file("Alex Rivera Resume.pdf", RESUME)
    )


def fill_page2(page: Page, *, graduation: str = "05/2028", gpa: str = "3.8") -> None:
    page.get_by_label("University / College").fill("The University of Texas at Austin")
    page.get_by_label("Degree type").select_option("Bachelor's")  # wrapping label around a select
    page.get_by_label("Field of study / major").fill("Computer Science")
    page.get_by_placeholder("GPA (4.0 scale)").fill(gpa)
    page.get_by_placeholder("MM/YYYY").fill(graduation)
    page.get_by_role("group", name="Are you currently enrolled?").get_by_label("Yes").check()


def fill_page3(env: Env, page: Page, *, cover: bool = True) -> None:
    page.get_by_label("Are you legally authorized").select_option("Yes")
    page.get_by_label("Will you now or in the future").select_option("No")
    page.get_by_role("group", name="Are you willing to relocate").get_by_label("Yes").check()
    page.get_by_label("How did you hear about us?").select_option("Company website")
    page.get_by_label("Why are you interested").fill("I like operations.")
    if cover:
        page.get_by_label("Cover letter").set_input_files(env.file("cover.pdf", COVER))


def run_application(env: Env, page: Page, *, cover: bool = True) -> None:
    page.goto(env.site.job_url())
    page.get_by_role("link", name="Apply now").click()
    page.wait_for_url(re.compile(r"/careers/apply/4421/1$"))
    fill_page1(env, page)
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/2$"))
    fill_page2(page)
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(re.compile(r"/3$"))
    fill_page3(env, page, cover=cover)
    page.get_by_role(
        "button", name="Next", exact=True
    ).click()  # an <a role=button> that submits by script
    page.wait_for_url(re.compile(r"/4$"))
    page.get_by_label("I certify").check()
    page.get_by_role("button", name="Submit application").click()


@browser_test
def test_dom_has_no_standard_ids_and_random_field_names(start: StartFn) -> None:
    env = start()
    page = env.page()
    page.goto(env.site.apply_url(page=1))
    names = page.eval_on_selector_all("input,select,textarea", "els => els.map(e => e.name)")
    assert (
        len(names) == 10
        and all(re.fullmatch(r"fld_\d{4}", n) for n in names)
        and len(set(names)) == 10
    )
    ids = page.eval_on_selector_all("[id]", "els => els.map(e => e.id)")
    assert not {"email", "first_name", "firstName", "phone", "resume"} & set(ids)
    assert page.locator("[data-automation-id]").count() == 0
    assert page.get_by_role("heading", name="Personal details").is_visible()
    # a differently seeded site names the same field differently
    other = make_site("acme", seed=99)
    assert other._name("email") != env.site._name("email")
    assert other._name("email") == make_site("acme", seed=99)._name("email")


@browser_test
def test_confirmation_variant_end_to_end_records_logical_fields_and_files(start: StartFn) -> None:
    env = start(variant="confirmation")
    page = env.page()
    run_application(env, page)
    page.get_by_role("heading", name="Thank you for applying!").wait_for()
    ref = re.search(r"reference number is (APP-\d{5})", page.inner_text("body"))
    assert ref and ref.group(1) == env.site.references[0]
    (sub,) = env.site.submissions
    assert sub.site == "employer-portal-acme" and sub.meta["variant"] == "confirmation"
    assert sub.meta["reference"] == ref.group(1) and sub.meta["job_id"] == "4421"
    assert sub.fields == {
        "first_name": ["Alex"],
        "last_name": ["Rivera"],
        "email": ["alex.rivera@example.test"],
        "phone": ["5125550123"],
        "address_line1": ["123 Example Street"],
        "city": ["Fremont"],
        "state": ["California"],
        "postal_code": ["94538"],
        "linkedin": ["https://www.linkedin.com/in/alex-rivera-example"],
        "school": ["The University of Texas at Austin"],
        "degree": ["Bachelor's"],
        "major": ["Computer Science"],
        "gpa": ["3.8"],
        "graduation": ["05/2028"],
        "enrolled": ["Yes"],
        "work_auth": ["Yes"],
        "sponsorship": ["No"],
        "relocate": ["Yes"],
        "referral": ["Company website"],
        "why_role": ["I like operations."],
        "certify": ["true"],
    }
    assert {f.field: (f.filename, f.data) for f in sub.files} == {
        "resume": ("Alex Rivera Resume.pdf", RESUME),
        "cover_letter": ("cover.pdf", COVER),
    }
    assert sub.meta["raw_names"]["email"].startswith("fld_")
    page.goto(env.site.apply_url(page=4))  # applying again in the same session records nothing new
    page.get_by_role("button", name="Submit application").click()
    page.get_by_role("heading", name="Thank you for applying!").wait_for()
    assert len(env.site.submissions) == 1


@browser_test
def test_silent_variant_shows_no_confirmation_but_records(start: StartFn) -> None:
    env = start(variant="silent", cover_letter=False)
    page = env.page()
    run_application(env, page, cover=False)
    page.wait_for_url(re.compile(r"/careers$"))
    text = page.inner_text("body").lower()
    for word in ("thank", "received", "submitted", "success", "confirmation", "app-", "reference"):
        assert word not in text
    assert len(env.site.submissions) == 1 and env.site.submissions[0].meta["reference"].startswith(
        "APP-"
    )
    assert [f.field for f in env.site.submissions[0].files] == ["resume"]


@browser_test
def test_signup_variant_requires_an_account_first(start: StartFn) -> None:
    env = start(variant="signup")
    env.site.add_account("sam.lee@example.test", "Existing-Pass1")
    page = env.page()
    page.goto(env.site.job_url())
    page.get_by_role("link", name="Apply now").click()
    page.wait_for_url(re.compile(r"/account/create\?next=/careers/apply/4421$"))
    page.get_by_placeholder("Email address").fill("sam.lee@example.test")
    page.get_by_label("Choose a password").fill("Whatever-123")
    page.get_by_label("Confirm password").fill("Whatever-123")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("alert")).to_contain_text("An account already exists")
    page.get_by_placeholder("Email address").fill("alex.rivera@example.test")
    page.get_by_label("Choose a password").fill("Whatever-123")
    page.get_by_label("Confirm password").fill("Different-123")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("alert")).to_contain_text("do not match")
    page.get_by_label("Choose a password").fill("Whatever-123")  # passwords are never echoed back
    page.get_by_label("Confirm password").fill("Whatever-123")
    page.get_by_role("button", name="Create account").click()
    page.wait_for_url(re.compile(r"/careers/apply/4421/1$"))
    assert page.get_by_placeholder("Email address").input_value() == "alex.rivera@example.test"
    # a fresh browser can sign in with the account and gets sent to the form
    other = env.page()
    other.goto(env.site.apply_url())
    other.get_by_role("link", name="Already registered? Sign in").click()
    other.get_by_placeholder("Email address").fill("alex.rivera@example.test")
    other.get_by_label("Password").fill("wrong")
    other.get_by_role("button", name="Sign in").click()
    expect(other.get_by_role("alert")).to_contain_text("Incorrect")
    other.get_by_label("Password").fill("Whatever-123")
    other.get_by_role("button", name="Sign in").click()
    other.wait_for_url(re.compile(r"/careers/apply/4421/1$"))
    run_application(env, page)
    page.get_by_role("heading", name="Thank you for applying!").wait_for()
    assert env.site.submissions[0].meta["account"] == "alex.rivera@example.test"


@browser_test
def test_validation_only_appears_after_next_and_files_survive_without_quirks(
    start: StartFn,
) -> None:
    env = start()
    page = env.page()
    page.goto(env.site.apply_url())
    assert page.get_by_role("alert").count() == 0 and page.locator(".fe").count() == 0
    page.get_by_role("button", name="Next", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(
        "Please correct the highlighted fields and try again."
    )
    assert page.locator(".fe").count() == 9  # everything required but LinkedIn
    assert page.get_by_label("First name").get_attribute("aria-invalid") == "true"
    page.get_by_placeholder("Email address").fill("not-an-email")
    page.get_by_label("Upload your resume").set_input_files(env.file("cv.pdf", RESUME))
    page.get_by_role("button", name="Next", exact=True).click()
    assert (
        page.get_by_placeholder("Email address").input_value() == "not-an-email"
    )  # entered values come back
    assert "valid email address" in page.locator(".fe", has_text="valid email").inner_text()
    assert (
        page.locator(".fe", has_text="required").count() == 7
    )  # the resume was kept (lenient formats too)
    fill_page1(env, page, phone="512.555.0123", zip_code="94538-1234")
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/2$"))


@browser_test
def test_quirks_strict_formats_and_files_are_discarded_after_an_error(start: StartFn) -> None:
    env = start(validation_quirks=True)
    page = env.page()
    page.goto(env.site.apply_url())
    fill_page1(env, page, phone="5125550123", zip_code="94538-1234")
    page.get_by_role("button", name="Next", exact=True).click()
    expect(page.get_by_role("alert")).to_be_visible()
    messages = page.locator(".fe").all_inner_texts()
    assert "Enter your phone number in the format (555) 555-0123." in messages
    assert "ZIP code must be 5 digits." in messages
    assert any(
        "attach the file again" in m for m in messages
    )  # the upload was lost with the redisplay
    assert page.get_by_label("First name").input_value() == "Alex"
    page.get_by_placeholder("(555) 555-0123").fill("(512) 555-0123")
    page.get_by_placeholder("ZIP / Postal code").fill("94538")
    page.get_by_label("Upload your resume").set_input_files(env.file("cv.pdf", RESUME))
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/2$"))
    fill_page2(page, graduation="May 2028", gpa="4.5")
    page.get_by_role("button", name="Continue").click()
    messages = page.locator(".fe").all_inner_texts()
    assert messages == [
        "GPA must be a number between 0.00 and 4.00.",
        "Enter the date as MM/YYYY.",
    ] or set(messages) == {
        "GPA must be a number between 0.00 and 4.00.",
        "Enter the date as MM/YYYY.",
    }
    page.get_by_placeholder("GPA (4.0 scale)").fill("3.85")
    page.get_by_placeholder("MM/YYYY").fill("05/2028")
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(re.compile(r"/3$"))


@browser_test
def test_buttons_save_draft_back_and_direct_navigation(start: StartFn) -> None:
    env = start()
    page = env.page()
    page.goto(env.site.apply_url())
    buttons = page.locator("form button, form input[type=submit], form a")
    assert [
        b.inner_text() if b.evaluate("e => e.tagName") != "INPUT" else b.get_attribute("value")
        for b in buttons.all()
    ] == [
        "Save draft",
        "Next",
    ]
    page.get_by_label("First name").fill("Alex")
    page.get_by_role("button", name="Save draft").click()
    expect(page.get_by_role("status")).to_have_text("Your progress has been saved.")
    assert page.url.endswith("/1") and page.get_by_label("First name").input_value() == "Alex"
    page.goto(env.site.apply_url(page=3))  # pages cannot be skipped
    assert page.url.endswith("/1")
    fill_page1(env, page)
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/2$"))
    page.get_by_role("link", name="Back").click()
    page.wait_for_url(re.compile(r"/1$"))
    assert page.get_by_label(re.compile(r"^City")).input_value() == "Fremont"
    page.goto(env.site.apply_url(page=2))
    fill_page2(page)
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(re.compile(r"/3$"))
    page.get_by_role("button", name="Previous").click()
    page.wait_for_url(re.compile(r"/2$"))
    assert page.get_by_label("Field of study / major").input_value() == "Computer Science"
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(re.compile(r"/3$"))
    fill_page3(env, page, cover=False)
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/4$"))
    assert (
        page.get_by_text("Alex Rivera Resume.pdf").count() == 1  # the summary lists the upload
    )  # resume name is uploaded as cv... see summary
    page.get_by_role("button", name="Submit application").click()
    expect(page.locator(".fe")).to_have_text("You must certify the information to submit.")
    assert env.site.submissions == []
    page.get_by_role("button", name="Edit application").click()
    page.wait_for_url(re.compile(r"/3$"))
    page.goto(env.site.apply_url(page=4))
    page.get_by_role("link", name="Cancel").click()
    page.wait_for_url(re.compile(r"/careers$"))


@browser_test
def test_all_question_kinds_and_closed_jobs(start: StartFn) -> None:
    questions = (
        MockQuestion("t", "Preferred name", "text"),
        MockQuestion("area", "Tell us more", "textarea", max_length=30),
        MockQuestion("ok", "I accept the background screening.", "checkbox"),
        MockQuestion("langs", "Languages spoken", "checkbox", ("English", "Spanish")),
        MockQuestion(
            "topics", "Topics of interest", "multiselect", ("Strategy", "Analytics", "Ops")
        ),
    )
    jobs = [
        MockJob(id="7", title="Analyst Intern", questions=questions),
        MockJob(id="8", title="Old Intern", closed=True),
    ]
    env = start(jobs=jobs, cover_letter=False)
    page = env.page()
    page.goto(env.site.job_url("8"))
    assert "no longer accepting applications" in page.inner_text("body")
    assert page.get_by_role("link", name="Apply now").count() == 0
    page.goto(env.site.url("/careers"))
    assert page.get_by_role("link").all_inner_texts() == ["Analyst Intern"]
    page.goto(env.site.apply_url("7", page=1))
    fill_page1(env, page)
    page.get_by_role("button", name="Next", exact=True).click()
    fill_page2(page)
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(re.compile(r"/7/3$"))
    assert page.locator("textarea").get_attribute("maxlength") == "30"
    page.get_by_placeholder("Preferred name").fill("Al")
    page.get_by_label("Tell us more").fill("y" * 60)
    assert len(page.get_by_label("Tell us more").input_value()) == 30
    page.get_by_label("I accept the background screening.").check()
    page.get_by_role("group", name="Languages spoken").get_by_label("Spanish").check()
    page.get_by_label("Topics of interest").select_option(["Strategy", "Ops"])
    page.get_by_role("button", name="Next", exact=True).click()
    page.wait_for_url(re.compile(r"/7/4$"))
    page.get_by_label("I certify").check()
    page.get_by_label("privacy notice").check()
    page.get_by_role("button", name="Submit application").click()
    page.get_by_role("heading", name="Thank you for applying!").wait_for()
    fields = env.site.submissions[0].fields
    assert fields["t"] == ["Al"] and fields["ok"] == ["true"] and fields["langs"] == ["Spanish"]
    assert fields["topics"] == ["Strategy", "Ops"] and fields["privacy"] == ["true"]


def test_http_level_edges() -> None:
    site = make_site("Keurig Dr Pepper", latency_ms=0)
    assert (
        site.host == "careers.keurig-dr-pepper.com"
        and site.name == "employer-portal-keurig-dr-pepper"
    )
    with pytest.raises(ValueError):
        make_site("acme", variant="nope")  # type: ignore[arg-type]
    hub = MockHub()
    hub.add(site)
    with hub, httpx.Client(base_url=site.direct_url(""), follow_redirects=False) as http:
        assert http.get("/").headers["location"] == "/careers"
        assert http.get("/careers/jobs/999-x").status_code == 404
        assert http.get("/careers/apply/999").status_code == 404
        assert http.get("/careers/apply/4421/9").status_code == 404
        assert http.get("/careers/apply/4421/thank-you").status_code == 303  # nothing submitted yet
        assert (
            http.post("/careers/apply/4421/3", data={}).headers["location"]
            == "/careers/apply/4421/1"
        )
        assert http.get("/account/create?next=//evil.example").status_code == 200
        create = http.get("/careers/apply/4421/1")
        assert create.status_code == 200 and "Step 1 of 4" in create.text
    assert site.submissions == []
