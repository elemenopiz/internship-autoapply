"""Behaviour of the mock Greenhouse boards: DOM contract, validation, recording, quirks, faults."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from autoapply.normalize import host_of
from autoapply.testing.mock_ats import greenhouse
from autoapply.testing.mock_ats.base import (
    STANDARD_QUESTIONS,
    MailboxEmailVerifier,
    MockJob,
    MockQuestion,
    running_hub,
)
from autoapply.testing.mock_ats.greenhouse import GreenhouseSite

LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE *.localhost, EXCLUDE localhost, EXCLUDE 127.0.0.1"
PDF = b"%PDF-1.4\n% fictional resume for Alex Rivera\n%%EOF\n"

RICH_QUESTIONS = (
    STANDARD_QUESTIONS["work_auth"],
    STANDARD_QUESTIONS["relocate"],  # radio
    MockQuestion(
        "langs", "Which languages do you know?", "multiselect", ("Python", "SQL", "Go"), False
    ),
    STANDARD_QUESTIONS["certify"],  # single checkbox, required
    MockQuestion("bio", "Tell us about yourself", "textarea", required=False, max_length=40),
)


def rich_job(job_id: str = "5000100") -> MockJob:
    return MockJob(id=job_id, title="Business Analyst Intern", questions=RICH_QUESTIONS)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True, args=[LOOPBACK_ONLY])
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1100, "height": 900})
    context.set_default_timeout(10_000)
    yield context.new_page()
    context.close()


@pytest.fixture
def resume(tmp_path: Path) -> Path:
    path = tmp_path / "Alex Rivera Résumé (final).pdf"  # spaces + non-ASCII on purpose
    path.write_bytes(PDF)
    return path


def only_job(site: GreenhouseSite) -> MockJob:
    return next(iter(site.jobs.values()))


def soup(response: httpx.Response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "html.parser")


def valid_http_payload(site: GreenhouseSite, job: MockJob) -> dict[str, str]:
    """Minimal valid urlencoded/multipart text fields for the default job on ``site``."""
    legacy = site.legacy
    prefix = "job_application[{}]" if legacy else "{}"
    data = {
        prefix.format("first_name"): "Alex",
        prefix.format("last_name"): "Rivera",
        prefix.format("email"): "alex.rivera@example.test",
        prefix.format("phone"): "5125550142",
    }
    if not legacy:
        data["country"] = "US"
    for key, label in (
        ("work_auth", "Yes"),
        ("sponsorship", "No"),
        ("referral", "Company website"),
    ):
        data[site.field_name(job.id, key)] = site.option_value(job.id, key, label)
    data[site.field_name(job.id, "why_role")] = "I enjoy building useful products."
    if legacy:
        data["job_application[answers_attributes][0][text_value]"] = ""
    return data


def resume_files(site: GreenhouseSite, name: str = "cv.pdf") -> dict[str, tuple[str, bytes, str]]:
    field = "job_application[resume]" if site.legacy else "resume"
    return {field: (name, PDF, "application/pdf")}


# --------------------------------------------------------------------------- structure (no browser)


def test_hosts_follow_the_variant_and_urls_use_localhost_suffix() -> None:
    new = greenhouse.make_site(variant="new")
    legacy = greenhouse.make_site(variant="legacy")
    embed = greenhouse.make_site(variant="embed")
    assert new.host == "job-boards.greenhouse.io"
    assert legacy.host == embed.host == "boards.greenhouse.io"
    with running_hub(new):
        assert host_of(new.job_url("4100200")) == "job-boards.greenhouse.io"
        assert ".localhost:" in new.job_url("4100200")


def test_default_job_and_ids_are_deterministic_across_instances() -> None:
    first = greenhouse.make_site()
    second = greenhouse.make_site()
    job = only_job(first)
    assert job.id == "4100200" and not job.closed
    assert first.field_id(job.id, "work_auth") == second.field_id(job.id, "work_auth")
    assert first.field_id(job.id, "work_auth").startswith("question_")
    assert first.field_id(job.id, "work_auth") != first.field_id(job.id, "sponsorship")
    assert first.option_value(job.id, "work_auth", "Yes") == second.option_value(
        job.id, "work_auth", "Yes"
    )


def test_new_page_selector_contract() -> None:
    site = greenhouse.make_site()
    job = only_job(site)
    with running_hub(site):
        response = httpx.get(site.direct_url(f"/acme/jobs/{job.id}"))
    assert response.status_code == 200
    doc = soup(response)
    assert doc.title is not None and doc.title.text == (
        "Job Application for Product Management Intern, Summer 2027 at Acme"
    )
    assert doc.select_one("h1.section-header").text.startswith("Product Management Intern")  # type: ignore[union-attr]
    form = doc.select_one("form#application-form")
    assert form is not None and form.get("novalidate") is not None
    for field_id in (
        "first_name",
        "last_name",
        "email",
        "phone",
        "country",
        "resume",
        "cover_letter",
    ):
        assert form.select_one(f"#{field_id}") is not None, field_id
    assert form.select_one("input#resume[type=file][name=resume]") is not None
    assert form.select_one("textarea#resume_text[name=resume_text]") is not None
    # react-select comboboxes, not native selects
    assert form.select_one("select") is None
    combo = form.select_one("#country")
    assert combo is not None and combo.get("role") == "combobox"
    assert combo.find_parent(class_="select-shell") is not None
    assert form.select_one(f"#{site.field_id(job.id, 'work_auth')}[role=combobox]") is not None
    # buttons of the resume block
    block = form.select_one(".file-upload[data-field=resume]")
    assert block is not None
    labels = [b.text.strip() for b in block.select("button")]
    assert labels[:1] == ["Attach"] and "Enter manually" in labels
    # EEO comboboxes and the submit button
    assert "Voluntary Self-Identification" in form.text
    for eeo_id in ("gender", "hispanic_ethnicity", "race", "veteran_status", "disability_status"):
        assert form.select_one(f"#{eeo_id}[role=combobox]") is not None, eeo_id
    assert form.select_one("button[type=submit]").text.strip() == "Submit application"  # type: ignore[union-attr]
    assert form.select_one(".g-recaptcha, .grecaptcha-badge, iframe") is None


def test_legacy_page_selector_contract() -> None:
    site = greenhouse.make_site(variant="legacy", jobs=[rich_job()])
    job = only_job(site)
    with running_hub(site):
        response = httpx.get(site.direct_url(f"/acme/jobs/{job.id}"))
    doc = soup(response)
    form = doc.select_one("form#application_form")
    assert form is not None and form.get("method") == "post"
    assert form.get("enctype") == "multipart/form-data"
    for name in ("first_name", "last_name", "email", "phone"):
        field = form.select_one(f"input#{name}[name='job_application[{name}]']")
        assert field is not None, name
    assert form.select_one("input#resume_fileupload[type=file][name='job_application[resume]']")
    assert form.select_one("textarea#resume_text[name='job_application[resume_text]']")
    submit = form.select_one("input#submit_app[type=submit]")
    assert submit is not None and submit.get("value") == "Submit Application"
    # custom questions use the rails style names and carry the hidden question id
    assert form.select_one("input[name='job_application[answers_attributes][0][question_id]']")
    select = form.select_one("select[name*='answer_selected_options_attributes']")
    assert select is not None
    assert [o.text for o in select.select("option")][0] == "--"
    assert form.select_one("select#job_application_gender") is not None
    assert doc.select_one("#header h1.app-title").text == "Business Analyst Intern"  # type: ignore[union-attr]
    assert form.select_one("#country") is None  # the legacy form has no country field


def test_board_listing_and_closed_jobs_redirect_with_error_banner() -> None:
    open_job = MockJob(id="1001", title="Open Intern")
    closed = MockJob(id="1002", title="Closed Intern", closed=True)
    site = greenhouse.make_site(jobs=[open_job, closed])
    with running_hub(site), httpx.Client(follow_redirects=False) as client:
        listing = client.get(site.direct_url("/acme"))
        assert [a["href"] for a in soup(listing).select("tr.job-post a")] == ["/acme/jobs/1001"]
        redirect = client.get(site.direct_url("/acme/jobs/1002"))
        assert redirect.status_code == 302 and redirect.headers["location"] == "/acme?error=true"
        banner = soup(client.get(site.direct_url("/acme?error=true"))).select_one(".flash-error")
        assert banner is not None and "no longer open" in banner.text
        assert client.get(site.direct_url("/acme/jobs/9999")).status_code == 404
        assert client.get(site.direct_url("/other/jobs/1001")).status_code == 404
        assert client.get(site.direct_url("/other")).status_code == 404
        assert client.post(site.direct_url("/acme/jobs/1002"), data={}).status_code == 404


def test_legacy_board_listing_uses_opening_rows() -> None:
    site = greenhouse.make_site(variant="legacy", jobs=[MockJob(id="77", title="Ops Intern")])
    with running_hub(site):
        listing = httpx.get(site.direct_url("/acme"))
    link = soup(listing).select_one("div.opening a[data-mapped=true]")
    assert link is not None and link["href"] == "/acme/jobs/77"


# --------------------------------------------------------------------------- server side validation


def test_new_server_rejects_empty_post_with_422_and_records_nothing() -> None:
    site = greenhouse.make_site()
    job = only_job(site)
    with running_hub(site):
        response = httpx.post(site.direct_url(f"/acme/jobs/{job.id}"), data={})
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors["first_name"] == "This field is required"
    assert set(errors) >= {"first_name", "last_name", "email", "phone", "country", "resume"}
    assert "q:work_auth" in errors and "q:linkedin" not in errors and "cover_letter" not in errors
    assert not any(key.startswith("eeo:") for key in errors)  # voluntary
    assert site.submissions == []


def test_new_server_validation_rules() -> None:
    site = greenhouse.make_site(jobs=[rich_job()], max_upload_bytes=64)
    job = only_job(site)
    good = {
        "first_name": "Alex",
        "last_name": "Rivera",
        "email": "alex.rivera@example.test",
        "phone": "5125550142",
        "country": "US",
        site.field_name(job.id, "work_auth"): site.option_value(job.id, "work_auth", "Yes"),
        site.field_name(job.id, "relocate"): site.option_value(job.id, "relocate", "No"),
        site.field_name(job.id, "certify"): "1",
    }
    files = {"resume": ("cv.pdf", PDF, "application/pdf")}
    with running_hub(site):
        url = site.direct_url(f"/acme/jobs/{job.id}")
        assert httpx.post(url, data={**good, "email": "not-an-email"}, files=files).json()[
            "errors"
        ] == {"email": "Please enter a valid email address."}
        bad_option = {**good, site.field_name(job.id, "work_auth"): "424242"}
        assert httpx.post(url, data=bad_option, files=files).json()["errors"] == {
            "q:work_auth": "Invalid selection."
        }
        too_long = {**good, site.field_name(job.id, "bio"): "x" * 41}
        assert (
            "maximum is 40" in httpx.post(url, data=too_long, files=files).json()["errors"]["q:bio"]
        )
        no_certify = {k: v for k, v in good.items() if k != site.field_name(job.id, "certify")}
        assert list(httpx.post(url, data=no_certify, files=files).json()["errors"]) == ["q:certify"]
        exe = {"resume": ("virus.exe", PDF, "application/octet-stream")}
        assert (
            "Unsupported file type"
            in httpx.post(url, data=good, files=exe).json()["errors"]["resume"]
        )
        big = {"resume": ("big.pdf", PDF * 10, "application/pdf")}
        assert httpx.post(url, data=good, files=big).json()["errors"] == {
            "resume": "File is too large."
        }
        assert site.submissions == []
        ok = httpx.post(url, data=good, files=files)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert len(site.submissions) == 1


def test_new_valid_post_is_recorded_with_variant_independent_meta() -> None:
    site = greenhouse.make_site(jobs=[rich_job()])
    job = only_job(site)
    langs = site.field_name(job.id, "langs")
    assert langs.endswith("[]")
    data: dict[str, str | list[str]] = {
        "first_name": "Alex",
        "last_name": "Rivera",
        "preferred_name": "Lex",
        "email": "alex.rivera@example.test",
        "phone": "5125550142",
        "country": "US",
        site.field_name(job.id, "work_auth"): site.option_value(job.id, "work_auth", "Yes"),
        site.field_name(job.id, "relocate"): site.option_value(job.id, "relocate", "Yes"),
        site.field_name(job.id, "certify"): "1",
        langs: [
            site.option_value(job.id, "langs", "Python"),
            site.option_value(job.id, "langs", "SQL"),
        ],
        "gender": "",
        "cover_letter_text": "Dear team",
    }
    with running_hub(site):
        response = httpx.post(
            site.direct_url(f"/acme/jobs/{job.id}"),
            data=data,
            files={"resume": ("cv.pdf", PDF, "application/pdf")},
        )
    assert response.status_code == 200
    sub = site.submissions[0]
    assert sub.first("first_name") == "Alex"
    assert sub.meta["standard"] == {
        "first_name": "Alex",
        "last_name": "Rivera",
        "preferred_name": "Lex",
        "email": "alex.rivera@example.test",
        "phone": "5125550142",
        "country": "United States",
    }
    assert sub.meta["answers"] == {
        "work_auth": ["Yes"],
        "relocate": ["Yes"],
        "langs": ["Python", "SQL"],
        "certify": ["checked"],
        "bio": [],
    }
    assert sub.meta["uploads"] == {"resume": "cv.pdf"}
    assert sub.meta["cover_letter_text"] == "Dear team"
    assert sub.meta["variant"] == "new" and sub.meta["job_id"] == job.id
    assert sub.file("resume") is not None and sub.file("resume").data == PDF  # type: ignore[union-attr]


def test_legacy_post_redirects_to_confirmation_and_records() -> None:
    site = greenhouse.make_site(variant="legacy")
    job = only_job(site)
    with running_hub(site), httpx.Client(follow_redirects=False) as client:
        response = client.post(
            site.direct_url(f"/acme/jobs/{job.id}"),
            data=valid_http_payload(site, job),
            files=resume_files(site),
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/acme/jobs/{job.id}/confirmation"
        confirmation = client.get(site.direct_url(response.headers["location"]))
    assert "Thank you for applying." in confirmation.text
    sub = site.submissions[0]
    assert sub.first("job_application[first_name]") == "Alex"
    assert sub.meta["standard"]["email"] == "alex.rivera@example.test"
    assert sub.meta["answers"]["work_auth"] == ["Yes"]
    assert sub.file("job_application[resume]") is not None


def test_legacy_server_error_rerenders_form_keeping_text_values_but_not_files() -> None:
    site = greenhouse.make_site(variant="legacy")
    job = only_job(site)
    payload = valid_http_payload(site, job)
    payload["job_application[last_name]"] = ""
    with running_hub(site):
        response = httpx.post(
            site.direct_url(f"/acme/jobs/{job.id}"), data=payload, files=resume_files(site)
        )
    assert response.status_code == 200 and site.submissions == []
    doc = soup(response)
    error = doc.select_one("div.field.error label.error")
    assert error is not None and error.text == "This field is required."
    assert error["for"] == "last_name"
    assert doc.select_one("#first_name")["value"] == "Alex"  # type: ignore[index]
    selected = doc.select_one(
        f"select[name='{site.field_name(job.id, 'work_auth')}'] option[selected]"
    )
    assert selected is not None and selected.text == "Yes"


def test_server_side_captcha_gate_requires_a_token_solved_by_a_human() -> None:
    site = greenhouse.make_site(require_captcha=True)
    job = only_job(site)
    data = valid_http_payload(site, job)
    data["country"] = "US"
    files = resume_files(site)
    with running_hub(site):
        url = site.direct_url(f"/acme/jobs/{job.id}")
        first = httpx.post(url, data=data, files=files)
        assert first.status_code == 422 and list(first.json()["errors"]) == ["captcha"]
        forged = httpx.post(url, data={**data, "g-recaptcha-response": "forged"}, files=files)
        assert forged.status_code == 422
        token = httpx.post(
            site.direct_url("/captcha/solve"), json={"provider": "recaptcha", "mode": "checkbox"}
        ).json()["token"]
        ok = httpx.post(url, data={**data, "g-recaptcha-response": token}, files=files)
    assert ok.status_code == 200
    assert site.state["captcha_interactions"][0]["accepted"] is True


def test_security_code_flow_uses_the_hub_mailbox() -> None:
    site = greenhouse.make_site(security_code=True)
    job = only_job(site)
    data = {**valid_http_payload(site, job), "country": "US"}
    with running_hub(site) as hub:
        url = site.direct_url(f"/acme/jobs/{job.id}")
        first = httpx.post(url, data=data, files=resume_files(site))
        assert first.json()["needs_code"] is True and site.submissions == []
        code = MailboxEmailVerifier(hub.mailbox).wait_for_code(
            to_address="alex.rivera@example.test", subject_contains="security code", timeout_s=2
        )
        assert code is not None and len(code) == 6
        wrong = httpx.post(url, data={**data, "security_code": "000000"}, files=resume_files(site))
        assert wrong.json()["errors"] == {"security_code": "Incorrect security code."}
        ok = httpx.post(url, data={**data, "security_code": code}, files=resume_files(site))
    assert ok.json() == {"ok": True} and len(site.submissions) == 1


def test_faults_delay_and_one_off_503_apply_to_job_pages() -> None:
    site = greenhouse.make_site()
    job = only_job(site)
    path = f"/acme/jobs/{job.id}"
    site.faults.fail_once.add(path)
    site.faults.delay_s[path] = 0.3
    with running_hub(site):
        assert httpx.get(site.direct_url(path)).status_code == 503
        started = time.monotonic()
        assert httpx.get(site.direct_url(path)).status_code == 200
        assert time.monotonic() - started >= 0.3
    assert site.page_views.count(path) == 2


# --------------------------------------------------------------------------- browser helpers


def pick(page: Page, field_id: str, option: str) -> None:
    """Choose ``option`` in a react-select combobox the way a user does."""
    page.click(f"#{field_id}")
    page.get_by_role("option", name=option, exact=True).click()


def fill_new_form(page: Page, site: GreenhouseSite, job: MockJob, resume: Path) -> None:
    page.fill("#first_name", "Alex")
    page.fill("#last_name", "Rivera")
    page.fill("#email", "alex.rivera@example.test")
    page.fill("#phone", "(512) 555-0142")
    pick(page, "country", "United States")
    page.locator("#resume").set_input_files(str(resume))
    pick(page, site.field_id(job.id, "work_auth"), "Yes")
    pick(page, site.field_id(job.id, "sponsorship"), "No")
    pick(page, site.field_id(job.id, "referral"), "Company website")
    page.fill(f"#{site.field_id(job.id, 'why_role')}", "I enjoy building useful products.")


def open_new(page: Page, site: GreenhouseSite) -> MockJob:
    job = only_job(site)
    page.goto(site.job_url(job.id))
    return job


# --------------------------------------------------------------------------- new variant (browser)


@pytest.mark.browser
def test_new_happy_path_is_recorded_with_files_and_shows_thank_you(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        job = open_new(page, site)
        page.fill("#preferred_name", "Lex")
        fill_new_form(page, site, job, resume)
        page.fill(
            f"#{site.field_id(job.id, 'linkedin')}",
            "https://www.linkedin.com/in/alex-rivera-example",
        )
        page.locator(".file-upload[data-field=cover_letter]").get_by_role(
            "button", name="Enter manually"
        ).click()
        page.fill("#cover_letter_text", "Dear team, I would love to intern with you.")
        pick(page, "gender", "Decline To Self Identify")
        pick(page, "veteran_status", "I don't wish to answer")
        url_before = page.url
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_contain_text("Thank you for applying.")
        assert page.url == url_before  # no navigation: the state is rendered in place
        assert page.locator("form#application-form").count() == 0
    assert len(site.submissions) == 1
    sub = site.submissions[0]
    assert sub.meta["standard"] == {
        "first_name": "Alex",
        "last_name": "Rivera",
        "preferred_name": "Lex",
        "email": "alex.rivera@example.test",
        "phone": "(512) 555-0142",
        "country": "United States",
    }
    assert sub.meta["answers"]["work_auth"] == ["Yes"]
    assert sub.meta["answers"]["sponsorship"] == ["No"]
    assert sub.meta["answers"]["referral"] == ["Company website"]
    assert sub.meta["answers"]["linkedin"] == ["https://www.linkedin.com/in/alex-rivera-example"]
    assert sub.meta["eeo"]["gender"] == "Decline To Self Identify"
    assert sub.meta["eeo"]["veteran_status"] == "I don't wish to answer"
    assert sub.meta["eeo"]["disability_status"] == ""  # untouched stays unanswered
    assert sub.meta["cover_letter_text"].startswith("Dear team")
    upload = sub.file("resume")
    assert upload is not None
    assert upload.filename == "Alex Rivera Résumé (final).pdf"
    assert upload.content_type == "application/pdf" and upload.data == PDF


@pytest.mark.browser
def test_new_empty_submit_shows_required_errors_sends_nothing_and_errors_clear_on_input(
    page: Page,
) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        job = open_new(page, site)
        page.get_by_role("button", name="Submit application").click()
        errors = page.locator("p.helper-text--error[role=alert]")
        expect(errors).to_have_count(10)
        assert set(errors.all_inner_texts()) == {"This field is required"}
        assert page.locator("#first_name").get_attribute("aria-invalid") == "true"
        assert page.locator("#country").get_attribute("aria-invalid") == "true"
        assert page.locator("#preferred_name").get_attribute("aria-invalid") == "false"
        assert (
            page.evaluate("document.activeElement.id") == "first_name"
        )  # focus moves to first error
        assert site.submissions == []
        page.fill("#first_name", "Alex")
        expect(errors).to_have_count(9)
        assert page.locator("#first_name").get_attribute("aria-invalid") == "false"
        pick(page, site.field_id(job.id, "work_auth"), "Yes")
        expect(errors).to_have_count(8)
    assert site.submissions == []


@pytest.mark.browser
def test_new_client_side_email_format_error(page: Page, resume: Path) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        page.fill("#email", "alex.rivera")
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#email-error")).to_have_text("Please enter a valid email address.")
        assert site.submissions == []
        page.fill("#email", "alex.rivera@example.test")
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_react_select_behaves_like_the_real_widget(page: Page) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        open_new(page, site)
        combo = page.locator("#country")
        shell = page.locator(".select-shell").filter(has=combo)
        # closed: placeholder visible, no menu, no listbox in the DOM
        expect(shell.locator(".select__placeholder")).to_have_text("Select...")
        assert page.get_by_role("option").count() == 0
        assert combo.get_attribute("aria-expanded") == "false"
        # click opens the menu with role=option entries
        combo.click()
        expect(shell.get_by_role("listbox")).to_be_visible()
        assert combo.get_attribute("aria-expanded") == "true"
        assert shell.get_by_role("option").first.inner_text() == "Australia"
        # typing filters (case insensitive, contains); Enter selects the focused (first) match
        combo.fill("kingdom")
        assert shell.get_by_role("option").all_inner_texts() == ["United Kingdom"]
        page.keyboard.press("Enter")
        expect(shell.locator(".select__single-value")).to_have_text("United Kingdom")
        assert shell.get_by_role("option").count() == 0  # menu closed after choosing
        assert page.locator("input[name=country]").input_value() == "GB"
        # ArrowDown reopens on the selected option; ArrowDown/Enter moves the choice
        combo.press("ArrowDown")
        expect(shell.locator(".select__option--is-selected")).to_have_text("United Kingdom")
        combo.press("ArrowDown")
        combo.press("Enter")
        expect(shell.locator(".select__single-value")).to_have_text("United States")
        assert page.locator("input[name=country]").input_value() == "US"
        # Escape closes without changing; no-match shows the empty state
        combo.press("ArrowDown")
        combo.press("Escape")
        assert shell.get_by_role("option").count() == 0
        combo.fill("zzz")
        expect(shell.locator(".select__menu-notice--no-options")).to_have_text("No options")
        # blur discards the typed filter and keeps the selection
        page.fill("#first_name", "Alex")
        expect(shell.locator(".select__single-value")).to_have_text("United States")
        # Backspace on an empty input clears the value again
        combo.click()
        combo.press("Escape")
        combo.press("Backspace")
        expect(shell.locator(".select__placeholder")).to_be_visible()
        assert page.locator("input[name=country]").input_value() == ""


@pytest.mark.browser
def test_resume_block_offers_attach_chooser_remove_and_manual_entry(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        open_new(page, site)
        block = page.locator(".file-upload[data-field=resume]")
        assert page.locator("#resume").is_hidden() is False  # visually hidden, not display:none
        assert block.locator(".file-upload__actions button").all_inner_texts() == [
            "Attach",
            "Dropbox",
            "Google Drive",
            "Enter manually",
        ]
        with page.expect_file_chooser() as chooser:
            block.get_by_role("button", name="Attach", exact=True).click()
        chooser.value.set_files(str(resume))
        expect(block.locator(".filename")).to_have_text(resume.name)
        expect(block.get_by_role("button", name="Attach", exact=True)).to_be_hidden()
        block.get_by_role("button", name="Remove attachment").click()
        expect(block.get_by_role("button", name="Attach", exact=True)).to_be_visible()
        assert page.evaluate("document.querySelector('#resume').files.length") == 0
        # unsupported types are refused by the widget itself
        page.locator("#resume").set_input_files(
            files=[{"name": "notes.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"}]
        )
        expect(block.locator("p.helper-text--error")).to_contain_text("Unsupported file type")
        assert page.evaluate("document.querySelector('#resume').files.length") == 0
        # entering the resume text manually satisfies the required field
        block.get_by_role("button", name="Enter manually").click()
        page.fill("#resume_text", "Alex Rivera - The University of Texas at Austin")
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("[data-field=resume] .helper-text--error")).to_have_count(0)


@pytest.mark.browser
def test_required_cover_letter_blocks_until_provided(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(cover_letter="required")
    with running_hub(site):
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("[data-field=cover_letter] .helper-text--error")).to_have_text(
            "This field is required"
        )
        assert site.submissions == []
        page.locator("#cover_letter").set_input_files(str(resume))
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert site.submissions[0].file("cover_letter") is not None


@pytest.mark.browser
def test_no_cover_letter_and_no_eeo_options_remove_those_sections(page: Page) -> None:
    site = greenhouse.make_site(cover_letter="none", eeo=False)
    with running_hub(site):
        open_new(page, site)
        assert page.locator("#cover_letter, #cover_letter_text").count() == 0
        assert page.locator("#gender, #eeoc").count() == 0


@pytest.mark.browser
def test_radio_checkbox_and_multiselect_questions_round_trip(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(jobs=[rich_job()])
    with running_hub(site):
        job = open_new(page, site)
        page.fill("#first_name", "Alex")
        page.fill("#last_name", "Rivera")
        page.fill("#email", "alex.rivera@example.test")
        page.fill("#phone", "5125550142")
        pick(page, "country", "United States")
        page.locator("#resume").set_input_files(str(resume))
        pick(page, site.field_id(job.id, "work_auth"), "Yes")
        relocate = site.field_id(job.id, "relocate")
        page.get_by_label("No", exact=True).check()
        assert page.locator(
            f"#{relocate}_{site.option_value(job.id, 'relocate', 'No')}"
        ).is_checked()
        page.get_by_label("Python").check()
        page.get_by_label("Go").check()
        page.get_by_label("I certify that the information provided is true and complete.").check()
        page.fill(f"#{site.field_id(job.id, 'bio')}", "y" * 40)
        assert page.locator(f"#{site.field_id(job.id, 'bio')}").get_attribute("maxlength") == "40"
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    answers = site.submissions[0].meta["answers"]
    assert answers["relocate"] == ["No"]
    assert answers["langs"] == ["Python", "Go"]
    assert answers["certify"] == ["checked"]
    assert answers["bio"] == ["y" * 40]


@pytest.mark.browser
def test_slow_render_form_appears_only_after_the_delay(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(render_delay_s=1.5)
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        assert page.locator("#first_name").count() == 0  # not there yet
        expect(page.locator("#form-mount .loading")).to_contain_text("Loading")
        page.locator("#first_name").wait_for(state="visible")
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_rerender_on_input_replaces_nodes_but_locators_and_values_survive(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(rerender_on_input=True)
    with running_hub(site):
        job = open_new(page, site)
        stale = page.query_selector("#first_name")
        assert stale is not None
        page.fill("#first_name", "Alex")
        assert stale.evaluate("e => e.isConnected") is False  # the old node was swapped out
        assert page.input_value("#first_name") == "Alex"
        page.locator("#last_name").press_sequentially("Rivera")  # focus survives replacement
        assert page.input_value("#last_name") == "Rivera"
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert site.submissions[0].meta["standard"]["last_name"] == "Rivera"


@pytest.mark.browser
def test_page_load_503_then_reload_recovers(page: Page) -> None:
    site = greenhouse.make_site()
    job = only_job(site)
    site.faults.fail_once.add(f"/acme/jobs/{job.id}")
    with running_hub(site):
        response = page.goto(site.job_url(job.id))
        assert response is not None and response.status == 503
        assert page.locator("#first_name").count() == 0
        response = page.reload()
        assert response is not None and response.status == 200
        expect(page.locator("#first_name")).to_be_visible()


@pytest.mark.browser
def test_submit_503_shows_banner_keeps_form_and_a_retry_succeeds(page: Page, resume: Path) -> None:
    site = greenhouse.make_site()
    with running_hub(site):
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        site.faults.fail_once.add(
            f"/acme/jobs/{job.id}"
        )  # armed after the page loaded: hits the POST
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator(".flash-error[role=alert]")).to_contain_text("Something went wrong")
        assert site.submissions == []
        assert page.input_value("#first_name") == "Alex"  # nothing lost
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_server_side_rejection_is_rendered_inline_when_client_checks_pass(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(jobs=[rich_job()])
    with running_hub(site):
        job = open_new(page, site)
        page.fill("#first_name", "Alex")
        page.fill("#last_name", "Rivera")
        page.fill("#email", "alex.rivera@example.test")
        page.fill("#phone", "5125550142")
        pick(page, "country", "United States")
        page.locator("#resume").set_input_files(str(resume))
        pick(page, site.field_id(job.id, "work_auth"), "Yes")
        page.get_by_label("No", exact=True).check()
        page.get_by_label("I certify that the information provided is true and complete.").check()
        # a script can overshoot maxlength (fill() would be truncated): the server still enforces the limit
        page.locator(f"#{site.field_id(job.id, 'bio')}").evaluate(
            "e => { e.value = 'z'.repeat(80); }"
        )
        page.get_by_role("button", name="Submit application").click()
        error = page.locator("[data-field='q:bio'] .helper-text--error")
        expect(error).to_contain_text("maximum is 40 characters")
        assert site.submissions == []


@pytest.mark.browser
def test_inline_visible_captcha_blocks_submit_until_a_human_solves_it(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(require_captcha=True)
    with running_hub(site):
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        widget = page.locator(".g-recaptcha iframe[title=reCAPTCHA]")
        expect(widget).to_be_visible()
        assert host_of(widget.get_attribute("src") or "") == "google.com"
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("[data-field=captcha] .helper-text--error")).to_have_text(
            "Please complete the captcha challenge."
        )
        assert site.submissions == [] and site.state["captcha_interactions"] == []
        # a human clicks the checkbox inside the vendor frame
        page.frame_locator(".g-recaptcha iframe").get_by_role("checkbox").click()
        expect(page.frame_locator(".g-recaptcha iframe").locator("#checkbox")).to_have_attribute(
            "aria-checked", "true"
        )
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.state["captcha_interactions"]) == 1
    assert site.submissions[0].first("g-recaptcha-response", "").startswith("mock-captcha-")  # type: ignore[union-attr]


@pytest.mark.browser
def test_overlay_captcha_covers_the_form_and_intercepts_clicks(page: Page) -> None:
    site = greenhouse.make_site(
        require_captcha=True, captcha_provider="hcaptcha", captcha_placement="overlay"
    )
    with running_hub(site):
        open_new(page, site)
        overlay = page.locator("#captcha-overlay[role=dialog]")
        expect(overlay).to_be_visible()
        frame = page.locator("#captcha-overlay iframe")
        assert host_of(frame.get_attribute("src") or "") == "newassets.hcaptcha.com"
        with pytest.raises(PlaywrightTimeout):
            page.click("#first_name", timeout=1_000)  # the overlay intercepts pointer events
        assert site.state["captcha_interactions"] == []
        page.frame_locator("#captcha-overlay iframe").get_by_role("button", name="Verify").click()
        expect(overlay).to_have_count(0)
        page.fill("#first_name", "Alex")  # form usable once a human has solved it
        assert page.input_value("#first_name") == "Alex"


@pytest.mark.browser
def test_invisible_recaptcha_badge_is_present_but_never_blocks(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(invisible_recaptcha=True)
    with running_hub(site):
        job = open_new(page, site)
        badge = page.locator("div.grecaptcha-badge")
        assert badge.count() == 1
        src = badge.locator("iframe[title=reCAPTCHA]").get_attribute("src") or ""
        assert "size=invisible" in src and host_of(src) == "google.com"
        assert page.locator(".g-recaptcha, #captcha-overlay, [data-field=captcha]").count() == 0
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert site.submissions[0].first("g-recaptcha-response") == "mock-invisible-recaptcha-token"
    assert site.state["captcha_interactions"] == []


@pytest.mark.browser
def test_security_code_step_completes_with_the_emailed_code(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(security_code=True)
    with running_hub(site) as hub:
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        code_input = page.locator("#security_code")
        expect(code_input).to_be_visible()
        assert site.submissions == []
        code = MailboxEmailVerifier(hub.mailbox).wait_for_code(
            to_address="alex.rivera@example.test", subject_contains="Security code", timeout_s=2
        )
        assert code is not None
        code_input.fill("111111")
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("[data-field=security_code] .helper-text--error")).to_have_text(
            "Incorrect security code."
        )
        code_input.fill(code)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.submissions) == 1


# --------------------------------------------------------------------------- legacy + embed (browser)


def fill_legacy_form(root: Page | object, site: GreenhouseSite, job: MockJob, resume: Path) -> None:
    ctx = root  # a Page or a FrameLocator: both expose .locator()
    ctx.locator("#first_name").fill("Alex")  # type: ignore[attr-defined]
    ctx.locator("#last_name").fill("Rivera")  # type: ignore[attr-defined]
    ctx.locator("#email").fill("alex.rivera@example.test")  # type: ignore[attr-defined]
    ctx.locator("#phone").fill("(512) 555-0142")  # type: ignore[attr-defined]
    ctx.locator("#resume_fileupload").set_input_files(str(resume))  # type: ignore[attr-defined]
    for key, label in (
        ("work_auth", "Yes"),
        ("sponsorship", "No"),
        ("referral", "Company website"),
    ):
        ctx.locator(f"#{site.field_id(job.id, key)}").select_option(label=label)  # type: ignore[attr-defined]
    ctx.locator(f"#{site.field_id(job.id, 'why_role')}").fill("I enjoy building useful products.")  # type: ignore[attr-defined]


@pytest.mark.browser
def test_legacy_happy_path_redirects_to_confirmation(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(variant="legacy")
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        fill_legacy_form(page, site, job, resume)
        page.locator("#job_application_gender").select_option(label="Decline To Self Identify")
        page.click("#submit_app")
        page.wait_for_url(f"**/acme/jobs/{job.id}/confirmation")
        expect(page.locator("#application_confirmation")).to_contain_text("Thank you for applying.")
    sub = site.submissions[0]
    assert sub.first("job_application[first_name]") == "Alex"
    assert sub.first("job_application[gender]") == site.specs_for(job)[-5].options[2][0]
    assert sub.meta["eeo"]["gender"] == "Decline To Self Identify"
    assert sub.file("job_application[resume]").filename == resume.name  # type: ignore[union-attr]


@pytest.mark.browser
def test_legacy_empty_submit_marks_fields_and_stays_on_the_page(page: Page) -> None:
    site = greenhouse.make_site(variant="legacy")
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        page.click("#submit_app")
        errors = page.locator("div.field.error > label.error")
        expect(errors).to_have_count(9)
        assert set(errors.all_inner_texts()) == {"This field is required."}
        assert page.locator("#first_name").get_attribute("aria-required") == "true"
        assert page.url.endswith(f"/acme/jobs/{job.id}") and site.submissions == []
        page.fill("#first_name", "Alex")
        expect(errors).to_have_count(8)


@pytest.mark.browser
def test_legacy_server_rejection_rerenders_and_the_file_must_be_attached_again(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(variant="legacy", require_captcha=True)
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        fill_legacy_form(page, site, job, resume)
        page.click("#submit_app")
        expect(page.locator("#captcha-error")).to_have_text(
            "Please complete the captcha challenge."
        )
        assert page.input_value("#first_name") == "Alex"  # text survives the round trip
        assert page.evaluate("document.querySelector('#resume_fileupload').files.length") == 0
        assert site.submissions == []
        page.locator("#resume_fileupload").set_input_files(str(resume))
        page.frame_locator(".g-recaptcha iframe").get_by_role("checkbox").click()
        expect(page.frame_locator(".g-recaptcha iframe").locator("#checkbox")).to_have_attribute(
            "aria-checked", "true"
        )
        page.click("#submit_app")
        page.wait_for_url("**/confirmation")
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_embed_company_page_hosts_a_cross_origin_iframe_with_the_legacy_form(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(variant="embed")
    with running_hub(site):
        job = only_job(site)
        page.goto(site.embed_page_url(job.id))
        assert host_of(page.url) == "careers.acme.example"
        frame_element = page.locator("div#grnhse_app > iframe#grnhse_iframe")
        expect(frame_element).to_be_visible()
        src = frame_element.get_attribute("src") or ""
        assert host_of(src) == "boards.greenhouse.io"
        assert src.endswith(f"/embed/job_app?for=acme&token={job.id}")
        assert page.locator("#first_name").count() == 0  # the form lives inside the iframe only
        frame = page.frame_locator("#grnhse_iframe")
        expect(frame.locator("form#application_form")).to_be_visible()
        fill_legacy_form(frame, site, job, resume)
        frame.locator("#submit_app").click()
        expect(frame.locator("#application_confirmation")).to_contain_text(
            "Thank you for applying."
        )
        assert host_of(page.url) == "careers.acme.example"  # parent page untouched
    assert site.submissions[0].path == "/embed/job_app"
    assert site.submissions[0].meta["variant"] == "embed"


@pytest.mark.browser
def test_embed_iframe_is_resized_to_its_content_and_lists_jobs_without_gh_jid(page: Page) -> None:
    site = greenhouse.make_site(variant="embed")
    with running_hub(site):
        job = only_job(site)
        page.goto(site.embed_page_url())
        frame = page.frame_locator("#grnhse_iframe")
        expect(frame.locator("div.opening a")).to_have_text(job.title)
        page.goto(site.embed_page_url(job.id))
        frame_element = page.locator("#grnhse_iframe")
        expect(page.frame_locator("#grnhse_iframe").locator("#submit_app")).to_be_attached()
        page.wait_for_function(
            "parseInt(document.querySelector('#grnhse_iframe').style.height) > 1200"
        )
        assert frame_element.get_attribute("scrolling") == "no"


@pytest.mark.browser
def test_embed_slow_render_delays_the_iframe_and_the_form(page: Page) -> None:
    site = greenhouse.make_site(variant="embed", render_delay_s=1.0)
    with running_hub(site):
        job = only_job(site)
        page.goto(site.embed_page_url(job.id))
        assert page.locator("#grnhse_iframe").count() == 0
        page.locator("#grnhse_iframe").wait_for(state="attached")
        frame = page.frame_locator("#grnhse_iframe")
        expect(frame.locator("#first_name")).to_be_visible()


def test_variants_do_not_expose_each_others_routes() -> None:
    new = greenhouse.make_site(variant="new")
    legacy = greenhouse.make_site(variant="legacy", name="greenhouse-legacy")
    with running_hub(new, legacy):
        assert httpx.get(new.direct_url("/careers")).status_code == 404
        assert httpx.get(new.direct_url("/embed/job_app?for=acme&token=4100200")).status_code == 404
        assert httpx.get(legacy.direct_url("/careers")).status_code == 404


# --------------------------------------------------------------------------- later additions


@pytest.mark.browser
def test_tricky_text_survives_the_round_trip_with_crlf_newlines(page: Page, resume: Path) -> None:
    site = greenhouse.make_site()
    tricky_name = "Zo\u00eb O'Neil-\u00d1u\u00f1ez"
    essay = "Line one\nLine two \u2014 \u201cquoted\u201d & <b>tags</b> \u65e5\u672c\u8a9e"
    with running_hub(site):
        job = open_new(page, site)
        fill_new_form(page, site, job, resume)
        page.fill("#first_name", tricky_name)
        page.fill(f"#{site.field_id(job.id, 'why_role')}", essay)
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    sub = site.submissions[0]
    assert sub.meta["standard"]["first_name"] == tricky_name
    # browsers normalise textarea newlines to CRLF in multipart bodies: read-back checks must expect that
    assert sub.meta["answers"]["why_role"] == [essay.replace("\n", "\r\n")]


@pytest.mark.browser
def test_on_submit_challenge_appears_only_after_the_submit_click_and_completes_it(
    page: Page, resume: Path
) -> None:
    site = greenhouse.make_site(
        require_captcha=True, captcha_provider="hcaptcha", captcha_placement="on_submit"
    )
    with running_hub(site):
        job = open_new(page, site)
        assert page.locator("#captcha-overlay").count() == 0  # nothing visible up front
        assert page.locator("iframe").count() == 0
        fill_new_form(page, site, job, resume)
        page.get_by_role("button", name="Submit application").click()
        overlay = page.locator("#captcha-overlay[role=dialog]")
        expect(overlay).to_be_visible()  # the challenge pops up mid-flow
        assert site.submissions == [] and site.state["captcha_interactions"] == []
        with pytest.raises(PlaywrightTimeout):
            page.click("#first_name", timeout=1_000)
        page.frame_locator("#captcha-overlay iframe").get_by_role("button", name="Verify").click()
        expect(
            page.locator("#application-confirmation")
        ).to_be_visible()  # submit finished on its own
    assert len(site.submissions) == 1 and len(site.state["captcha_interactions"]) == 1


@pytest.mark.browser
def test_on_submit_challenge_also_gates_the_legacy_form(page: Page, resume: Path) -> None:
    site = greenhouse.make_site(
        variant="legacy", require_captcha=True, captcha_placement="on_submit"
    )
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        fill_legacy_form(page, site, job, resume)
        page.click("#submit_app")
        expect(page.locator("#captcha-overlay")).to_be_visible()
        assert site.submissions == []
        page.frame_locator("#captcha-overlay iframe").get_by_role("button", name="Verify").click()
        page.wait_for_url("**/confirmation")
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_cookie_consent_modal_blocks_the_form_until_it_is_accepted(page: Page) -> None:
    site = greenhouse.make_site(cookie_consent="modal")
    with running_hub(site):
        open_new(page, site)
        banner = page.locator("#onetrust-banner-sdk[role=region]")
        expect(banner).to_be_visible()
        assert page.locator(".onetrust-pc-dark-filter").count() == 1
        with pytest.raises(PlaywrightTimeout):
            page.click("#first_name", timeout=1_000)  # the consent dialog intercepts pointer events
        page.get_by_role("button", name="Accept All Cookies").click()
        expect(page.locator("#onetrust-consent-sdk")).to_have_count(0)
        page.fill("#first_name", "Alex")
        assert "OptanonAlertBoxClosed" in page.evaluate("document.cookie")


@pytest.mark.browser
def test_cookie_consent_bar_sits_at_the_bottom_and_can_be_rejected(page: Page) -> None:
    site = greenhouse.make_site(cookie_consent="bar")
    with running_hub(site):
        open_new(page, site)
        box = page.locator("#onetrust-banner-sdk").bounding_box()
        assert box is not None and box["y"] + box["height"] >= 899  # glued to the viewport bottom
        page.fill("#first_name", "Alex")  # a bar does not block the fields above it
        page.get_by_role("button", name="Reject All").click()
        expect(page.locator("#onetrust-banner-sdk")).to_have_count(0)
