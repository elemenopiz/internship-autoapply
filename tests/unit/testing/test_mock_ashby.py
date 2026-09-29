"""Behaviour of the mock Ashby board: SPA shell, JS driven form, inline validation, recording, quirks, faults."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autoapply.normalize import host_of
from autoapply.testing.mock_ats import ashby
from autoapply.testing.mock_ats.ashby import AshbySite
from autoapply.testing.mock_ats.base import STANDARD_QUESTIONS, MockJob, MockQuestion, running_hub

LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE *.localhost, EXCLUDE localhost, EXCLUDE 127.0.0.1"
PDF = b"%PDF-1.4\n% fictional resume for Alex Rivera\n%%EOF\n"
JOB_ID = ashby.DEFAULT_JOB_ID
SUBMIT = ".ashby-application-form-submit-button"

RICH_QUESTIONS = (
    STANDARD_QUESTIONS["work_auth"],  # Yes/No -> button pair
    STANDARD_QUESTIONS["referral"],  # single select
    MockQuestion(
        "langs", "Which languages do you know?", "multiselect", ("Python", "SQL", "Go"), False
    ),
    STANDARD_QUESTIONS["certify"],  # single checkbox, required
    MockQuestion("bio", "Tell us about yourself", "textarea", required=False, max_length=30),
)


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
    path = tmp_path / "Alex Rivera Résumé (final).pdf"
    path.write_bytes(PDF)
    return path


def only_job(site: AshbySite) -> MockJob:
    return next(iter(site.jobs.values()))


def sel(site: AshbySite, job: MockJob, key: str) -> str:
    return f"[id='{site.field_id(job.id, key)}']"


def open_application(page: Page, site: AshbySite) -> MockJob:
    job = only_job(site)
    page.goto(site.application_url(job.id))
    page.locator("#_systemfield_name").wait_for()
    return job


def fill_default(page: Page, site: AshbySite, job: MockJob, resume: Path) -> None:
    page.fill("#_systemfield_name", "Alex Rivera")
    page.fill("#_systemfield_email", "alex.rivera@example.test")
    page.set_input_files("#_systemfield_resume", str(resume))
    page.fill(sel(site, job, "phone"), "(512) 555-0142")
    page.fill(sel(site, job, "linkedin"), "https://www.linkedin.com/in/alex-rivera-example")
    for key, label in (("work_auth", "Yes"), ("sponsorship", "No"), ("relocate", "Yes")):
        page.locator(sel(site, job, key)).get_by_role("button", name=label, exact=True).click()
    page.select_option(sel(site, job, "referral"), label="Company website")
    page.fill(sel(site, job, "why_role"), "I enjoy building products.")


# --------------------------------------------------------------------------- structure (no browser)


def test_host_default_job_and_uuid_field_ids_are_deterministic() -> None:
    first = ashby.make_site()
    second = ashby.make_site()
    assert first.host == "jobs.ashbyhq.com"
    job = only_job(first)
    assert job.id == JOB_ID
    field = first.field_id(job.id, "work_auth")
    assert field == second.field_id(job.id, "work_auth")
    assert len(field) == 36 and field.count("-") == 4
    assert first.field_id(job.id, "phone") != first.field_id(job.id, "linkedin")
    assert first.css_hash != second.css_hash  # class hashes are random per instance


def test_shell_is_served_for_overview_and_application_and_api_describes_the_form() -> None:
    site = ashby.make_site()
    job = only_job(site)
    with running_hub(site):
        overview = httpx.get(site.direct_url(f"/acme/{JOB_ID}"))
        application = httpx.get(site.direct_url(f"/acme/{JOB_ID}/application"))
        data = httpx.get(site.direct_url(f"/api/job-posting/{JOB_ID}")).json()
    for response in (overview, application):
        assert response.status_code == 200
        doc = BeautifulSoup(response.text, "html.parser")
        assert doc.title is not None
        assert doc.title.text == "Product Management Intern, Summer 2027 @ Acme"
        assert doc.select_one("div#root") is not None
        assert doc.select_one("form") is None  # rendered by script, not in the HTML
    fields = {f["id"]: f for f in data["fields"]}
    assert (
        fields["_systemfield_name"]["type"] == "String" and fields["_systemfield_name"]["required"]
    )
    assert fields["_systemfield_email"]["type"] == "Email"
    assert fields["_systemfield_resume"]["type"] == "File"
    assert fields[site.field_id(job.id, "work_auth")]["type"] == "Boolean"
    assert fields[site.field_id(job.id, "referral")]["type"] == "ValueSelect"
    assert fields[site.field_id(job.id, "why_role")]["type"] == "LongText"
    assert fields[site.field_id(job.id, "phone")]["type"] == "Phone"
    assert data["title"] == "Product Management Intern, Summer 2027"


def test_closed_unknown_and_foreign_jobs_are_404_but_still_serve_the_spa_shell() -> None:
    site = ashby.make_site(jobs=[MockJob(id="gone", title="Old Intern", closed=True)])
    with running_hub(site):
        for path in ("/acme/gone", "/acme/gone/application", "/acme/missing", "/other/gone"):
            assert httpx.get(site.direct_url(path)).status_code == 404, path
        assert httpx.get(site.direct_url("/api/job-posting/gone")).status_code == 404
        assert httpx.get(site.direct_url("/other")).status_code == 404


def test_board_lists_open_jobs_only() -> None:
    site = ashby.make_site(
        jobs=[MockJob(id="j1", title="Ops Intern"), MockJob(id="j2", title="Old", closed=True)]
    )
    with running_hub(site):
        doc = BeautifulSoup(httpx.get(site.direct_url("/acme")).text, "html.parser")
    links = doc.select(".ashby-job-posting-brief-list a")
    assert [a["href"] for a in links] == ["/acme/j1"]


def _multipart(site: AshbySite, job: MockJob, **overrides: str) -> dict[str, str]:
    data = {
        "jobPostingId": job.id,
        "_systemfield_name": "Alex Rivera",
        "_systemfield_email": "alex.rivera@example.test",
        site.field_id(job.id, "phone"): "5125550142",
        site.field_id(job.id, "work_auth"): "true",
        site.field_id(job.id, "sponsorship"): "false",
        site.field_id(job.id, "relocate"): "true",
        site.field_id(job.id, "referral"): "Company website",
        site.field_id(job.id, "why_role"): "I enjoy building products.",
    }
    data.update(overrides)
    return data


def test_server_validation_is_authoritative_and_records_only_valid_submissions() -> None:
    site = ashby.make_site()
    job = only_job(site)
    files = {"_systemfield_resume": ("cv.pdf", PDF, "application/pdf")}
    with running_hub(site):
        url = site.direct_url("/api/non-user-graphql?op=ApiSubmitSingleApplicationForm")
        empty = httpx.post(url, data={"jobPostingId": job.id})
        assert empty.status_code == 422
        errors = empty.json()["errors"]
        assert errors["_systemfield_name"] == "Missing entry for required field: Name"
        assert errors["_systemfield_resume"] == "Missing entry for required field: Resume"
        assert errors[site.field_id(job.id, "work_auth")].startswith(
            "Missing entry for required field"
        )
        assert site.field_id(job.id, "linkedin") not in errors  # optional
        bad_email = httpx.post(
            url, data=_multipart(site, job, _systemfield_email="nope"), files=files
        )
        assert bad_email.json()["errors"] == {
            "_systemfield_email": "Please enter a valid email address."
        }
        bad_bool = httpx.post(
            url,
            data=_multipart(site, job, **{site.field_id(job.id, "work_auth"): "maybe"}),
            files=files,
        )
        assert list(bad_bool.json()["errors"]) == [site.field_id(job.id, "work_auth")]
        exe = {"_systemfield_resume": ("cv.exe", PDF, "application/octet-stream")}
        assert (
            "Unsupported file type"
            in httpx.post(url, data=_multipart(site, job), files=exe).json()["errors"][
                "_systemfield_resume"
            ]
        )
        unknown = httpx.post(
            url, data={**_multipart(site, job), "jobPostingId": "nope"}, files=files
        )
        assert unknown.status_code == 404
        assert site.submissions == []
        ok = httpx.post(url, data=_multipart(site, job), files=files)
    assert ok.status_code == 200 and ok.json() == {"success": True}
    sub = site.submissions[0]
    assert sub.path.endswith("?op=ApiSubmitSingleApplicationForm")
    assert sub.meta["standard"] == {
        "name": "Alex Rivera",
        "email": "alex.rivera@example.test",
        "phone": "5125550142",
        "linkedin": "",
    }
    assert sub.meta["answers"]["work_auth"] == ["Yes"]
    assert sub.meta["answers"]["sponsorship"] == ["No"]
    assert sub.meta["uploads"] == {"resume": "cv.pdf"}
    assert sub.file("_systemfield_resume") is not None


def test_reject_as_spam_refuses_an_otherwise_valid_submission() -> None:
    site = ashby.make_site(reject_as_spam=True)
    job = only_job(site)
    with running_hub(site):
        response = httpx.post(
            site.direct_url("/api/non-user-graphql?op=ApiSubmitSingleApplicationForm"),
            data=_multipart(site, job),
            files={"_systemfield_resume": ("cv.pdf", PDF, "application/pdf")},
        )
    assert response.status_code == 422
    assert "flagged as possible spam" in response.json()["errors"]["_form"]
    assert site.submissions == []


def test_faults_delay_and_one_off_503_apply_to_api_and_pages() -> None:
    site = ashby.make_site()
    site.faults.fail_once.add("/api/job-posting")
    site.faults.delay_s["/api/job-posting"] = 0.25
    with running_hub(site):
        assert httpx.get(site.direct_url(f"/api/job-posting/{JOB_ID}")).status_code == 503
        started = time.monotonic()
        assert httpx.get(site.direct_url(f"/api/job-posting/{JOB_ID}")).status_code == 200
        assert time.monotonic() - started >= 0.25


# --------------------------------------------------------------------------- browser


@pytest.mark.browser
def test_overview_to_application_switch_is_client_side_and_the_form_matches_the_contract(
    page: Page,
) -> None:
    site = ashby.make_site()
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        expect(page.locator("h1.ashby-job-posting-heading")).to_have_text(job.title)
        assert page.title() == f"{job.title} @ Acme"
        assert page.get_by_role("tab", name="Overview").get_attribute("aria-selected") == "true"
        assert page.locator("form").count() == 0
        page.evaluate("window.__still_here = 'yes'")
        page.get_by_role("tab", name="Application").click()
        expect(page.locator("form.ashby-application-form")).to_be_visible()
        assert page.url == site.application_url(job.id)
        assert page.evaluate("window.__still_here") == "yes"  # no page reload happened
        assert page.get_by_role("tab", name="Application").get_attribute("aria-selected") == "true"
        # system fields, labels and the submit button
        assert page.locator("input#_systemfield_name").get_attribute("name") == "_systemfield_name"
        assert page.locator("input#_systemfield_email").get_attribute("type") == "email"
        resume = page.locator("input#_systemfield_resume[type=file]")
        assert resume.count() == 1 and resume.is_hidden()
        label = page.locator("label.ashby-application-form-question-title[for=_systemfield_name]")
        assert label.inner_text() == "Name"
        assert page.get_by_label("Name", exact=True).count() == 1
        assert page.locator(".ashby-application-form-field-entry").count() >= 8
        assert page.locator(SUBMIT).inner_text() == "Submit Application"
        # browser history moves between the tabs without reloading
        page.go_back()
        expect(page.locator("form.ashby-application-form")).to_have_count(0)
        assert page.evaluate("window.__still_here") == "yes"
        page.go_forward()
        expect(page.locator("form.ashby-application-form")).to_be_visible()
        # the overview also links to the application
        page.get_by_role("tab", name="Overview").click()
        page.get_by_role("link", name="Apply for this Job").click()
        expect(page.locator("form.ashby-application-form")).to_be_visible()


@pytest.mark.browser
def test_direct_application_url_renders_the_form_and_hashes_differ_between_sites(
    page: Page,
) -> None:
    site_a = ashby.make_site(name="ashby-a")
    site_b = ashby.make_site(name="ashby-b")
    with running_hub(site_a, site_b):
        open_application(page, site_a)
        classes_a = page.locator("#_systemfield_name").get_attribute("class") or ""
        open_application(page, site_b)
        classes_b = page.locator("#_systemfield_name").get_attribute("class") or ""
    assert classes_a.startswith("_input_") and classes_b.startswith("_input_")
    assert classes_a != classes_b


@pytest.mark.browser
def test_happy_path_submits_through_fetch_without_reloading_and_records_everything(
    page: Page, resume: Path
) -> None:
    site = ashby.make_site()
    with running_hub(site):
        job = open_application(page, site)
        page.evaluate("window.__marker = 42")
        fill_default(page, site, job, resume)
        assert (
            page.locator(".ashby-application-form-file-name").inner_text().startswith(resume.name)
        )
        for key, expected in (("work_auth", "true"), ("sponsorship", "false")):
            group = page.locator(sel(site, job, key))
            assert group.get_by_role("button", name="Yes", exact=True).get_attribute(
                "aria-pressed"
            ) == ("true" if expected == "true" else "false")
        page.click(SUBMIT)
        success = page.locator(".ashby-application-form-success-container")
        expect(success).to_contain_text("Your application was successfully submitted.")
        assert page.evaluate("window.__marker") == 42  # still the same document
        assert page.url == site.application_url(job.id)
        assert page.locator("form").count() == 0
    assert len(site.submissions) == 1
    sub = site.submissions[0]
    assert sub.meta["standard"] == {
        "name": "Alex Rivera",
        "email": "alex.rivera@example.test",
        "phone": "(512) 555-0142",
        "linkedin": "https://www.linkedin.com/in/alex-rivera-example",
    }
    assert sub.meta["answers"]["work_auth"] == ["Yes"]
    assert sub.meta["answers"]["sponsorship"] == ["No"]
    assert sub.meta["answers"]["relocate"] == ["Yes"]
    assert sub.meta["answers"]["referral"] == ["Company website"]
    upload = sub.file("_systemfield_resume")
    assert upload is not None
    assert upload.filename == "Alex Rivera Résumé (final).pdf"
    assert upload.content_type == "application/pdf" and upload.data == PDF
    assert sub.first(site.field_id(job.id, "work_auth")) == "true"  # raw Boolean value


@pytest.mark.browser
def test_inline_validation_errors_block_the_request_and_clear_as_fields_are_fixed(
    page: Page, resume: Path
) -> None:
    site = ashby.make_site()
    with running_hub(site):
        job = open_application(page, site)
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url) if r.method == "POST" else None)
        page.click(SUBMIT)
        alerts = page.locator(".ashby-application-form-field-entry [role=alert]")
        expect(alerts.first).to_have_text("Missing entry for required field: Name")
        texts = alerts.all_inner_texts()
        assert "Missing entry for required field: Email" in texts
        assert "Missing entry for required field: Resume" in texts
        assert len(texts) == 9  # name, email, resume, phone + the five required custom questions
        assert page.locator("#_systemfield_name").get_attribute("aria-invalid") == "true"
        assert page.locator("#form-banner").inner_text().startswith("Your form needs corrections.")
        assert page.evaluate("document.activeElement.id") == "_systemfield_name"
        assert requests == [] and site.submissions == []
        page.fill("#_systemfield_name", "Alex Rivera")
        expect(page.locator("#_systemfield_name")).not_to_have_attribute("aria-invalid", "true")
        assert alerts.count() == len(texts) - 1
        page.locator(sel(site, job, "work_auth")).get_by_role(
            "button", name="No", exact=True
        ).click()
        assert alerts.count() == len(texts) - 2
        page.fill("#_systemfield_email", "not-an-email")
        page.click(SUBMIT)
        expect(page.locator("#_systemfield_email ~ [role=alert]")).to_have_text(
            "Please enter a valid email address."
        )
        assert resume.exists() and requests == []


@pytest.mark.browser
def test_yes_no_buttons_toggle_and_expose_state(page: Page) -> None:
    site = ashby.make_site()
    with running_hub(site):
        job = open_application(page, site)
        group = page.locator(sel(site, job, "work_auth"))
        assert group.get_attribute("role") == "group"
        yes = group.get_by_role("button", name="Yes", exact=True)
        no = group.get_by_role("button", name="No", exact=True)
        assert [yes.get_attribute("aria-pressed"), no.get_attribute("aria-pressed")] == [
            "false",
            "false",
        ]
        yes.click()
        assert [yes.get_attribute("aria-pressed"), no.get_attribute("aria-pressed")] == [
            "true",
            "false",
        ]
        no.click()
        assert [yes.get_attribute("aria-pressed"), no.get_attribute("aria-pressed")] == [
            "false",
            "true",
        ]
        assert page.locator("form input[type=hidden]").first.input_value() == "false"


@pytest.mark.browser
def test_checkbox_groups_single_checkbox_and_select_round_trip(page: Page, resume: Path) -> None:
    job = MockJob(id="rich-1", title="Analyst Intern", questions=RICH_QUESTIONS)
    site = ashby.make_site(jobs=[job])
    with running_hub(site):
        page.goto(site.application_url(job.id))
        page.locator("#_systemfield_name").wait_for()
        page.fill("#_systemfield_name", "Alex Rivera")
        page.fill("#_systemfield_email", "alex.rivera@example.test")
        page.set_input_files("#_systemfield_resume", str(resume))
        page.fill(sel(site, job, "phone"), "5125550142")
        page.locator(sel(site, job, "work_auth")).get_by_role(
            "button", name="Yes", exact=True
        ).click()
        page.select_option(sel(site, job, "referral"), label="LinkedIn")
        group = page.locator(sel(site, job, "langs"))
        group.get_by_label("Python").check()
        group.get_by_label("Go").check()
        page.get_by_label("I certify that the information provided is true and complete.").check()
        page.fill(sel(site, job, "bio"), "y" * 30)
        assert page.locator(sel(site, job, "bio")).get_attribute("maxlength") == "30"
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    answers = site.submissions[0].meta["answers"]
    assert answers["langs"] == ["Python", "Go"]
    assert answers["certify"] == ["checked"]
    assert answers["referral"] == ["LinkedIn"]
    assert answers["bio"] == ["y" * 30]


@pytest.mark.browser
def test_required_single_checkbox_is_enforced_inline(page: Page, resume: Path) -> None:
    job = MockJob(id="c1", title="Intern", questions=(STANDARD_QUESTIONS["certify"],))
    site = ashby.make_site(jobs=[job])
    with running_hub(site):
        page.goto(site.application_url(job.id))
        page.locator("#_systemfield_name").wait_for()
        page.fill("#_systemfield_name", "Alex Rivera")
        page.fill("#_systemfield_email", "alex.rivera@example.test")
        page.set_input_files("#_systemfield_resume", str(resume))
        page.fill(sel(site, job, "phone"), "5125550142")
        page.click(SUBMIT)
        expect(page.locator("[role=alert]").first).to_contain_text("I certify that the information")
        assert site.submissions == []


@pytest.mark.browser
def test_combobox_select_widget_supports_typing_and_keyboard(page: Page, resume: Path) -> None:
    site = ashby.make_site(select_widget="combobox")
    with running_hub(site):
        job = open_application(page, site)
        box = page.locator(f"input{sel(site, job, 'referral')}")
        assert box.get_attribute("role") == "combobox"
        box.click()
        listbox = page.get_by_role("listbox")
        expect(listbox).to_be_visible()
        assert listbox.get_by_role("option").count() == 6
        box.fill("web")
        assert listbox.get_by_role("option").all_inner_texts() == ["Company website"]
        box.press("Enter")
        assert box.input_value() == "Company website"
        expect(listbox).to_be_hidden()
        box.click()
        page.get_by_role("option", name="Referral", exact=True).click()
        assert box.input_value() == "Referral"
        fill_default_without_referral(page, site, job, resume)
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    assert site.submissions[0].meta["answers"]["referral"] == ["Referral"]


def fill_default_without_referral(page: Page, site: AshbySite, job: MockJob, resume: Path) -> None:
    page.fill("#_systemfield_name", "Alex Rivera")
    page.fill("#_systemfield_email", "alex.rivera@example.test")
    page.set_input_files("#_systemfield_resume", str(resume))
    page.fill(sel(site, job, "phone"), "5125550142")
    for key, label in (("work_auth", "Yes"), ("sponsorship", "No"), ("relocate", "No")):
        page.locator(sel(site, job, key)).get_by_role("button", name=label, exact=True).click()
    page.fill(sel(site, job, "why_role"), "I enjoy building products.")


@pytest.mark.browser
def test_slow_render_shows_a_spinner_before_the_page_appears(page: Page, resume: Path) -> None:
    site = ashby.make_site(render_delay_s=1.5)
    with running_hub(site):
        job = only_job(site)
        page.goto(site.application_url(job.id))
        assert page.locator("#_systemfield_name").count() == 0
        expect(page.locator("#root [role=status]")).to_have_text("Loading...")
        page.locator("#_systemfield_name").wait_for(state="visible")
        fill_default(page, site, job, resume)
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()


@pytest.mark.browser
def test_rerender_on_input_replaces_nodes_but_values_and_focus_survive(
    page: Page, resume: Path
) -> None:
    site = ashby.make_site(rerender_on_input=True)
    with running_hub(site):
        job = open_application(page, site)
        stale = page.query_selector("#_systemfield_name")
        assert stale is not None
        page.fill("#_systemfield_name", "Alex")
        assert stale.evaluate("e => e.isConnected") is False
        page.locator("#_systemfield_name").press_sequentially(" Rivera")
        assert page.input_value("#_systemfield_name") == "Alex Rivera"
        fill_default(page, site, job, resume)
        page.fill("#_systemfield_name", "Alex Rivera")
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    assert site.submissions[0].meta["standard"]["name"] == "Alex Rivera"


@pytest.mark.browser
def test_job_data_503_shows_the_spa_error_state_until_the_page_is_reloaded(page: Page) -> None:
    site = ashby.make_site()
    site.faults.fail_once.add("/api/job-posting")
    with running_hub(site):
        job = only_job(site)
        page.goto(site.application_url(job.id))
        expect(page.locator("#root [role=alert]")).to_contain_text("Something went wrong")
        assert page.locator("#_systemfield_name").count() == 0
        page.reload()
        expect(page.locator("#_systemfield_name")).to_be_visible()


@pytest.mark.browser
def test_page_shell_503_then_reload_recovers(page: Page) -> None:
    site = ashby.make_site()
    job = only_job(site)
    site.faults.fail_once.add(f"/acme/{job.id}/application")
    with running_hub(site):
        response = page.goto(site.application_url(job.id))
        assert response is not None and response.status == 503
        response = page.reload()
        assert response is not None and response.status == 200
        expect(page.locator("#_systemfield_name")).to_be_visible()


@pytest.mark.browser
def test_submit_503_shows_a_banner_keeps_the_form_and_a_retry_succeeds(
    page: Page, resume: Path
) -> None:
    site = ashby.make_site()
    with running_hub(site):
        job = open_application(page, site)
        fill_default(page, site, job, resume)
        site.faults.fail_once.add("/api/non-user-graphql")
        page.click(SUBMIT)
        expect(page.locator("#form-banner")).to_contain_text("Something went wrong submitting")
        assert site.submissions == []
        assert page.input_value("#_systemfield_name") == "Alex Rivera"
        assert page.locator(".ashby-application-form-file-name").is_visible()  # file kept too
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_spam_rejection_is_rendered_as_a_form_level_message(page: Page, resume: Path) -> None:
    site = ashby.make_site(reject_as_spam=True)
    with running_hub(site):
        job = open_application(page, site)
        fill_default(page, site, job, resume)
        page.click(SUBMIT)
        expect(page.locator("#form-banner")).to_contain_text("flagged as possible spam")
        assert site.submissions == []


@pytest.mark.browser
def test_autofill_panel_parses_after_a_delay_and_only_fills_empty_fields(
    page: Page, resume: Path
) -> None:
    site = ashby.make_site(
        autofill=True,
        autofill_delay_s=0.6,
        autofill_fills={
            "_systemfield_name": "Parsed Name",
            "_systemfield_email": "parsed@example.test",
        },
    )
    with running_hub(site):
        open_application(page, site)
        panel = page.locator(".ashby-application-form-autofill-input-root")
        expect(panel).to_contain_text("Autofill from resume")
        page.fill("#_systemfield_name", "Alex Rivera")
        page.set_input_files("#_autofill_resume", str(resume))
        expect(panel.locator("[role=status]")).to_contain_text("Autofilling")
        expect(panel.locator("[role=status]")).to_contain_text("Your resume was uploaded")
        assert page.input_value("#_systemfield_name") == "Alex Rivera"  # typed value wins
        assert page.input_value("#_systemfield_email") == "parsed@example.test"
        # the same file was attached to the Resume field as well
        assert page.evaluate("document.querySelector('#_systemfield_resume').files.length") == 1
        assert page.locator(".ashby-application-form-file-name").is_visible()


@pytest.mark.browser
def test_invisible_recaptcha_badge_never_blocks_the_submission(page: Page, resume: Path) -> None:
    site = ashby.make_site(invisible_recaptcha=True)
    with running_hub(site):
        job = open_application(page, site)
        badge = page.locator("div.grecaptcha-badge iframe[title=reCAPTCHA]")
        assert host_of(badge.get_attribute("src") or "") == "google.com"
        fill_default(page, site, job, resume)
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    assert site.submissions[0].first("g-recaptcha-response") == "mock-invisible-recaptcha-token"


@pytest.mark.browser
def test_closed_job_shows_not_found_message(page: Page) -> None:
    site = ashby.make_site(jobs=[MockJob(id="gone", title="Old Intern", closed=True)])
    with running_hub(site):
        response = page.goto(site.job_url("gone"))
        assert response is not None and response.status == 404
        expect(page.locator("#root h1")).to_have_text("Job not found")
        expect(page.locator("#root")).to_contain_text("no longer be accepting applications")


# --------------------------------------------------------------------------- later additions


@pytest.mark.browser
def test_tricky_text_survives_the_round_trip(page: Page, resume: Path) -> None:
    site = ashby.make_site()
    essay = "Line one\nLine two \u2014 \u201cquoted\u201d & <b>tags</b> \u65e5\u672c\u8a9e"
    with running_hub(site):
        job = open_application(page, site)
        fill_default(page, site, job, resume)
        page.fill("#_systemfield_name", "Zo\u00eb O'Neil-\u00d1u\u00f1ez")
        page.fill(sel(site, job, "why_role"), essay)
        page.click(SUBMIT)
        expect(page.locator(".ashby-application-form-success-container")).to_be_visible()
    sub = site.submissions[0]
    assert sub.meta["standard"]["name"] == "Zo\u00eb O'Neil-\u00d1u\u00f1ez"
    assert sub.meta["answers"]["why_role"] == [essay.replace("\n", "\r\n")]


@pytest.mark.browser
def test_cookie_consent_modal_blocks_the_spa_until_accepted(page: Page) -> None:
    site = ashby.make_site(cookie_consent="modal")
    with running_hub(site):
        job = open_application(page, site)
        expect(page.locator("#onetrust-banner-sdk")).to_be_visible()
        with pytest.raises(PlaywrightTimeoutError):
            page.click("#_systemfield_name", timeout=1_000)
        page.get_by_role("button", name="Accept All Cookies").click()
        page.fill("#_systemfield_name", "Alex Rivera")
        assert job.id in page.url
