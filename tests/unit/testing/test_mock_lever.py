"""Behaviour of the mock Lever site: DOM contract, resume parsing, validation, recording, quirks, faults."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from autoapply.normalize import host_of
from autoapply.testing.mock_ats import lever
from autoapply.testing.mock_ats.base import STANDARD_QUESTIONS, MockJob, MockQuestion, running_hub
from autoapply.testing.mock_ats.lever import LeverSite

LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE *.localhost, EXCLUDE localhost, EXCLUDE 127.0.0.1"
PDF = b"%PDF-1.4\n% fictional resume for Alex Rivera\n%%EOF\n"
JOB_ID = lever.DEFAULT_JOB_ID


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


def only_job(site: LeverSite) -> MockJob:
    return next(iter(site.jobs.values()))


def soup(response: httpx.Response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "html.parser")


def fill_form(page: Page, site: LeverSite, job: MockJob) -> None:
    """Everything except the resume, the way an adapter would fill it (by ``name``)."""
    page.fill("input[name=name]", "Alex Rivera")
    page.fill("input[name=email]", "alex.rivera@example.test")
    page.fill("input[name=phone]", "(512) 555-0142")
    page.fill("input[name='urls[LinkedIn]']", "https://www.linkedin.com/in/alex-rivera-example")
    page.fill("input[name='urls[GitHub]']", "https://github.com/alex-rivera-example")
    for key, label in (
        ("work_auth", "Yes"),
        ("sponsorship", "No"),
        ("referral", "Company website"),
    ):
        page.select_option(f"select[name='{site.card_field(job.id, key)}']", label=label)
    page.fill(
        f"textarea[name='{site.card_field(job.id, 'why_role')}']", "I enjoy building products."
    )


def attach_resume(page: Page, resume: Path) -> None:
    page.set_input_files("#resume-upload-input", str(resume))
    page.wait_for_selector(".resume-upload-success", state="visible")


def open_apply(page: Page, site: LeverSite) -> MockJob:
    job = only_job(site)
    page.goto(site.apply_url(job.id))
    return job


# --------------------------------------------------------------------------- structure (no browser)


def test_host_default_job_and_deterministic_card_names() -> None:
    first = lever.make_site()
    second = lever.make_site()
    assert first.host == "jobs.lever.co"
    job = only_job(first)
    assert job.id == JOB_ID
    name = first.card_field(job.id, "work_auth")
    assert name == second.card_field(job.id, "work_auth")
    assert name.startswith("cards[") and name.endswith("][field0]")
    # two questions per card by default: same uuid, field0 / field1
    sponsorship = first.card_field(job.id, "sponsorship")
    assert sponsorship == name.replace("[field0]", "[field1]")
    referral = first.card_field(job.id, "referral")
    assert referral.endswith("[field0]") and referral != name
    single = lever.make_site(card_size=1)
    assert single.card_field(job.id, "sponsorship").endswith("[field0]")


def test_posting_page_has_two_apply_links_that_point_at_apply() -> None:
    site = lever.make_site()
    with running_hub(site):
        response = httpx.get(site.direct_url(f"/acme/{JOB_ID}"))
    doc = soup(response)
    links = doc.select("a.postings-btn")
    assert [a.text for a in links] == ["Apply for this job", "Apply for this job"]
    assert {a["href"].rsplit(":", 1)[0] for a in links} == {"http://jobs.lever.co.localhost"}
    assert all(a["href"].endswith(f"/acme/{JOB_ID}/apply") for a in links)
    assert doc.select_one(".posting-headline h2").text == "Product Management Intern (Summer 2027)"  # type: ignore[union-attr]
    assert doc.select_one("[data-qa=job-description]") is not None
    assert doc.title is not None and doc.title.text.startswith("Acme - ")


def test_job_listing_page() -> None:
    site = lever.make_site(
        jobs=[MockJob(id="j1", title="Ops Intern"), MockJob(id="j2", title="X", closed=True)]
    )
    with running_hub(site):
        doc = soup(httpx.get(site.direct_url("/acme")))
    assert [h.text for h in doc.select("div.posting a.posting-title h5[data-qa=posting-name]")] == [
        "Ops Intern"
    ]


def test_apply_page_selector_contract() -> None:
    site = lever.make_site()
    job = only_job(site)
    with running_hub(site):
        doc = soup(httpx.get(site.direct_url(f"/acme/{JOB_ID}/apply")))
    form = doc.select_one("form#application-form")
    assert (
        form is not None and form["method"] == "POST" and form["enctype"] == "multipart/form-data"
    )
    assert form.get("novalidate") is None  # native constraint validation is active
    for name in ("name", "email", "phone"):
        assert form.select_one(f"input[name={name}][required]") is not None, name
    assert form.select_one("input[name=org]") is not None
    assert form.select_one("input[name=org][required]") is None
    for network in ("LinkedIn", "GitHub", "Portfolio", "Other"):
        assert form.select_one(f"input[name='urls[{network}]']") is not None, network
    assert form.select_one("textarea[name=comments]") is not None
    resume = form.select_one("input#resume-upload-input[type=file][name=resume]")
    assert resume is not None and "display:none" in resume["style"].replace(" ", "")
    assert form.select_one("button.resume-upload-btn").text == "Attach resume/CV"  # type: ignore[union-attr]
    submit = form.select_one("button#btn-submit[type=submit]")
    assert submit is not None and submit.text == "Submit application"
    # custom questions: cards[<uuid>][fieldN] with a hidden baseTemplate JSON per card
    work_auth = form.select_one(f"select[name='{site.card_field(job.id, 'work_auth')}'][required]")
    assert work_auth is not None
    assert [o.text for o in work_auth.select("option")] == ["Select...", "Yes", "No"]
    card = site.card_field(job.id, "work_auth").split("]")[0].removeprefix("cards[")
    template = form.select_one(f"input[type=hidden][name='cards[{card}][baseTemplate]']")
    assert template is not None
    described = json.loads(template["value"])
    assert described["id"] == card and len(described["fields"]) == 2
    assert described["fields"][0]["type"] == "dropdown"
    # EEO survey
    for eeo in ("gender", "race", "veteran", "disability"):
        assert form.select_one(f"select[name='eeo[{eeo}]']") is not None, eeo
    assert "Decline to self-identify" in [
        o.text for o in form.select("select[name='eeo[gender]'] option")
    ]
    assert form.select_one(".h-captcha, iframe") is None


def test_closed_and_unknown_jobs_are_404_everywhere() -> None:
    site = lever.make_site(jobs=[MockJob(id="gone", title="Old Intern", closed=True)])
    with running_hub(site):
        for path in ("/acme/gone", "/acme/gone/apply", "/acme/gone/thanks", "/acme/nope", "/other"):
            response = httpx.get(site.direct_url(path))
            assert response.status_code == 404, path
            assert "Sorry, we couldn't find anything here" in response.text
        assert httpx.post(site.direct_url("/acme/gone/apply"), data={}).status_code == 404


def test_parse_resume_endpoint_delays_issues_ids_and_reports_fills() -> None:
    site = lever.make_site(parse_delay_s=0.3, parse_fills={"org": "Fictional Labs"})
    with running_hub(site):
        started = time.monotonic()
        response = httpx.post(
            site.direct_url("/parseResume"), files={"resume": ("cv.pdf", PDF, "application/pdf")}
        )
        assert time.monotonic() - started >= 0.3
        body = response.json()
        assert body["fills"] == {"org": "Fictional Labs"}
        assert body["resumeStorageId"] in site.state["parsed_resumes"]
        assert (
            httpx.post(site.direct_url("/parseResume"), files={"x": ("a", b"1")}).status_code == 422
        )
    assert site.state["parse_requests"] == 2


def _valid_fields(site: LeverSite, job: MockJob) -> dict[str, str]:
    data = {
        "name": "Alex Rivera",
        "email": "alex.rivera@example.test",
        "phone": "5125550142",
        site.card_field(job.id, "work_auth"): "Yes",
        site.card_field(job.id, "sponsorship"): "No",
        site.card_field(job.id, "referral"): "Company website",
        site.card_field(job.id, "why_role"): "I enjoy building products.",
    }
    return data


def test_server_requires_a_finished_resume_parse_and_valid_fields() -> None:
    site = lever.make_site(parse_delay_s=0)
    job = only_job(site)
    files = {"resume": ("cv.pdf", PDF, "application/pdf")}
    with running_hub(site), httpx.Client(follow_redirects=False) as client:
        url = site.direct_url(f"/acme/{job.id}/apply")
        # no resume at all
        page = client.post(url, data=_valid_fields(site, job))
        assert page.status_code == 200 and "Resume/CV is required." in page.text
        # resume without a completed parse ("still uploading")
        page = client.post(url, data=_valid_fields(site, job), files=files)
        assert "still uploading" in page.text
        # now parse first, then submit with the storage id
        storage = client.post(site.direct_url("/parseResume"), files=files).json()[
            "resumeStorageId"
        ]
        bad_email = {**_valid_fields(site, job), "email": "nope", "resumeStorageId": storage}
        page = client.post(url, data=bad_email, files=files)
        assert "Please provide a valid email address." in page.text
        missing = {k: v for k, v in _valid_fields(site, job).items() if k != "phone"}
        page = client.post(url, data={**missing, "resumeStorageId": storage}, files=files)
        assert "Phone is required." in soup(page).select_one("#error-banner").text  # type: ignore[union-attr]
        assert soup(page).select_one("input[name=name]")["value"] == "Alex Rivera"  # type: ignore[index]
        assert site.submissions == []
        ok = client.post(
            url, data={**_valid_fields(site, job), "resumeStorageId": storage}, files=files
        )
    assert ok.status_code == 303 and ok.headers["location"] == f"/acme/{job.id}/thanks"
    sub = site.submissions[0]
    assert sub.meta["standard"]["name"] == "Alex Rivera"
    assert sub.meta["answers"]["work_auth"] == ["Yes"]
    assert sub.meta["uploads"] == {"resume": "cv.pdf"}
    assert sub.file("resume") is not None and sub.file("resume").data == PDF  # type: ignore[union-attr]


def test_faults_delay_and_one_off_503_apply_to_lever_paths() -> None:
    site = lever.make_site()
    path = f"/acme/{JOB_ID}/apply"
    site.faults.fail_once.add(path)
    site.faults.delay_s[path] = 0.25
    with running_hub(site):
        assert httpx.get(site.direct_url(path)).status_code == 503
        started = time.monotonic()
        assert httpx.get(site.direct_url(path)).status_code == 200
        assert time.monotonic() - started >= 0.25


# --------------------------------------------------------------------------- browser


@pytest.mark.browser
def test_posting_to_apply_to_thanks_happy_path_records_everything(page: Page, resume: Path) -> None:
    site = lever.make_site()
    with running_hub(site):
        job = only_job(site)
        page.goto(site.job_url(job.id))
        apply_link = page.locator("a.postings-btn").first
        assert apply_link.inner_text() == "APPLY FOR THIS JOB"  # upper-cased by CSS
        assert apply_link.text_content() == "Apply for this job"
        with pytest.raises(Exception, match="strict mode violation"):
            page.get_by_role("link", name="Apply for this job").click(timeout=2_000)
        apply_link.click()
        page.wait_for_url(f"**/acme/{job.id}/apply")
        fill_form(page, site, job)
        page.fill("textarea[name=comments]", "Thanks for considering my application.")
        attach_resume(page, resume)
        page.select_option("select[name='eeo[gender]']", label="Decline to self-identify")
        page.select_option("select[name='eeo[veteran]']", label="Decline to self-identify")
        page.click("#btn-submit")
        page.wait_for_url(f"**/acme/{job.id}/thanks")
        expect(page.locator("h2")).to_have_text("Application submitted!")
    assert len(site.submissions) == 1
    sub = site.submissions[0]
    assert sub.meta["standard"] == {
        "name": "Alex Rivera",
        "email": "alex.rivera@example.test",
        "phone": "(512) 555-0142",
        "org": "",
        "location": "",
        "comments": "Thanks for considering my application.",
    }
    assert sub.meta["urls"]["LinkedIn"] == "https://www.linkedin.com/in/alex-rivera-example"
    assert sub.meta["urls"]["GitHub"] == "https://github.com/alex-rivera-example"
    assert sub.meta["answers"] == {
        "work_auth": ["Yes"],
        "sponsorship": ["No"],
        "referral": ["Company website"],
        "why_role": ["I enjoy building products."],
    }
    assert sub.meta["eeo"]["gender"] == "Decline to self-identify"
    assert sub.meta["eeo"]["race"] == ""
    upload = sub.file("resume")
    assert upload is not None
    assert upload.filename == "Alex Rivera Résumé (final).pdf"
    assert upload.content_type == "application/pdf" and upload.data == PDF
    assert sub.first("resumeStorageId", "").startswith("resume-")  # type: ignore[union-attr]
    assert (
        f"/acme/{job.id}/apply" in site.page_views and f"/acme/{job.id}/thanks" in site.page_views
    )


@pytest.mark.browser
def test_empty_submit_is_blocked_by_native_validation(page: Page) -> None:
    site = lever.make_site()
    with running_hub(site):
        job = open_apply(page, site)
        url = page.url
        page.click("#btn-submit")
        page.wait_for_timeout(300)
        assert page.url == url and site.submissions == []
        invalid = page.locator("form :invalid")
        assert invalid.count() >= 6
        assert page.evaluate("document.activeElement.name") == "name"
        assert page.evaluate("document.querySelector('input[name=name]').validationMessage") != ""
        page.fill("input[name=name]", "Alex Rivera")
        assert page.locator("input[name=name]:invalid").count() == 0
        assert job.id in url


@pytest.mark.browser
def test_missing_resume_is_reported_by_the_page_not_the_browser(page: Page) -> None:
    site = lever.make_site()
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        page.click("#btn-submit")
        expect(page.locator("#client-error")).to_have_text("Resume/CV is required.")
        assert site.submissions == []


@pytest.mark.browser
def test_resume_parse_shows_progress_then_success_and_can_autofill_empty_fields(
    page: Page, resume: Path
) -> None:
    site = lever.make_site(
        parse_delay_s=0.8, parse_fills={"org": "Fictional Labs", "name": "Parsed Name"}
    )
    with running_hub(site):
        open_apply(page, site)
        page.fill("input[name=name]", "Alex Rivera")  # typed BEFORE the parse finishes
        page.set_input_files("#resume-upload-input", str(resume))
        assert page.locator(".resume-upload-loading").is_visible()
        assert page.locator(".resume-upload-success").is_hidden()
        started = time.monotonic()
        page.wait_for_selector(".resume-upload-success", state="visible")
        assert time.monotonic() - started >= 0.5
        assert page.locator(".resume-upload-loading").is_hidden()
        assert page.locator(".resume-upload-success .success-label").inner_text() == "Success!"
        assert page.locator(".resume-filename").inner_text() == resume.name
        assert page.input_value("input[name=org]") == "Fictional Labs"  # empty field autofilled
        assert page.input_value("input[name=name]") == "Alex Rivera"  # typed value NOT overwritten


@pytest.mark.browser
def test_submit_before_parse_finishes_is_blocked_and_counted(page: Page, resume: Path) -> None:
    site = lever.make_site(parse_delay_s=1.5)
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        page.set_input_files("#resume-upload-input", str(resume))
        url = page.url
        page.click("#btn-submit")
        expect(page.locator("#client-error")).to_have_text(
            "Please wait until your resume has finished uploading."
        )
        assert page.url == url and site.submissions == []
        assert site.state["blocked_submits"] == 1
        page.wait_for_selector(".resume-upload-success", state="visible")
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_failed_parse_shows_failure_blocks_submit_and_a_new_attach_recovers(
    page: Page, resume: Path
) -> None:
    site = lever.make_site(parse_delay_s=0.1)
    site.faults.fail_once.add("/parseResume")
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        page.set_input_files("#resume-upload-input", str(resume))
        expect(page.locator(".resume-upload-failure")).to_contain_text("Upload failed")
        assert page.locator(".resume-upload-success").is_hidden()
        page.click("#btn-submit")
        expect(page.locator("#client-error")).to_contain_text("Resume upload failed")
        assert site.submissions == []
        page.set_input_files("#resume-upload-input", str(resume))  # second attempt succeeds
        page.wait_for_selector(".resume-upload-success", state="visible")
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    assert len(site.submissions) == 1
    assert site.state["parse_requests"] == 1  # the 503 never reached the parser
    assert len(site.state["parsed_resumes"]) == 1


@pytest.mark.browser
def test_attach_button_opens_the_file_chooser(page: Page, resume: Path) -> None:
    site = lever.make_site()
    with running_hub(site):
        open_apply(page, site)
        assert page.locator("#resume-upload-input").is_hidden()  # display:none: wait for "attached"
        with page.expect_file_chooser() as chooser:
            page.get_by_role("button", name="Attach resume/CV").click()
        chooser.value.set_files(str(resume))
        page.wait_for_selector(".resume-upload-success", state="visible")


@pytest.mark.browser
def test_submit_503_shows_a_raw_error_page_and_the_form_must_be_redone(
    page: Page, resume: Path
) -> None:
    site = lever.make_site(parse_delay_s=0.1)
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        attach_resume(page, resume)
        site.faults.fail_once.add(f"/acme/{job.id}/apply")  # armed after load: the POST fails
        page.click("#btn-submit")
        expect(page.locator("body")).to_contain_text("temporarily unavailable")
        assert site.submissions == []
        page.goto(site.apply_url(job.id))  # a real browser needs a fresh page: the form is gone
        assert page.input_value("input[name=name]") == ""
        fill_form(page, site, job)
        attach_resume(page, resume)
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_slow_render_form_appears_after_the_delay(page: Page, resume: Path) -> None:
    site = lever.make_site(render_delay_s=1.5, parse_delay_s=0.1)
    with running_hub(site):
        job = only_job(site)
        page.goto(site.apply_url(job.id))
        assert page.locator("input[name=name]").count() == 0
        expect(page.locator("#form-mount .loading")).to_be_visible()
        page.locator("input[name=name]").wait_for(state="visible")
        fill_form(page, site, job)
        attach_resume(page, resume)
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")


@pytest.mark.browser
def test_visible_hcaptcha_blocks_until_a_human_solves_it(page: Page, resume: Path) -> None:
    site = lever.make_site(require_captcha=True, parse_delay_s=0.1)
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        attach_resume(page, resume)
        frame = page.locator("div.h-captcha iframe")
        expect(frame).to_be_visible()
        assert (
            frame.get_attribute("title")
            == "Widget containing checkbox for hCaptcha security challenge"
        )
        assert host_of(frame.get_attribute("src") or "") == "newassets.hcaptcha.com"
        page.click("#btn-submit")
        expect(page.locator("#client-error")).to_have_text("Please complete the captcha challenge.")
        assert site.submissions == [] and site.state["captcha_interactions"] == []
        page.frame_locator("div.h-captcha iframe").get_by_role("checkbox").click()
        expect(page.locator("textarea[name=h-captcha-response]")).not_to_have_value("")
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    assert len(site.state["captcha_interactions"]) == 1


@pytest.mark.browser
def test_overlay_captcha_intercepts_pointer_events(page: Page) -> None:
    site = lever.make_site(
        require_captcha=True, captcha_provider="recaptcha", captcha_placement="overlay"
    )
    with running_hub(site):
        open_apply(page, site)
        expect(page.locator("#captcha-overlay")).to_be_visible()
        with pytest.raises(PlaywrightTimeout):
            page.click("input[name=name]", timeout=1_000)
        assert site.state["captcha_interactions"] == []


@pytest.mark.browser
def test_location_typeahead_requires_picking_a_suggestion(page: Page, resume: Path) -> None:
    site = lever.make_site(location_field=True, parse_delay_s=0.1)
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        attach_resume(page, resume)
        location = page.locator("#location-input")
        location.press_sequentially("Aus")
        suggestions = page.locator("#location-results li[role=option]")
        expect(suggestions).to_have_count(2)
        assert suggestions.first.inner_text() == "Austin, TX, United States"
        # typed text without choosing a suggestion is rejected by the server
        page.click("#btn-submit")
        expect(page.locator("#error-banner")).to_contain_text("select a location")
        assert site.submissions == []
        job = open_apply(page, site)
        fill_form(page, site, job)
        attach_resume(page, resume)
        page.locator("#location-input").press_sequentially("Aus")
        page.locator("#location-results li[role=option]").first.click()
        assert page.input_value("#location-input") == "Austin, TX, United States"
        assert (
            json.loads(page.input_value("#selected-location"))["name"]
            == "Austin, TX, United States"
        )
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    assert site.submissions[0].meta["standard"]["location"] == "Austin, TX, United States"


@pytest.mark.browser
def test_checkbox_radio_and_multiselect_cards_round_trip(page: Page, resume: Path) -> None:
    job = MockJob(
        id="rich-1",
        title="Analyst Intern",
        questions=(
            STANDARD_QUESTIONS["relocate"],  # radio
            MockQuestion(
                "langs",
                "Which languages do you know?",
                "multiselect",
                ("Python", "SQL", "Go"),
                False,
            ),
            STANDARD_QUESTIONS["certify"],  # single checkbox, required
            MockQuestion(
                "bio", "Tell us about yourself", "textarea", required=False, max_length=30
            ),
        ),
    )
    site = lever.make_site(jobs=[job], parse_delay_s=0.1, card_size=3)
    with running_hub(site):
        page.goto(site.apply_url(job.id))
        page.fill("input[name=name]", "Alex Rivera")
        page.fill("input[name=email]", "alex.rivera@example.test")
        page.fill("input[name=phone]", "5125550142")
        attach_resume(page, resume)
        radio = site.card_field(job.id, "relocate")
        page.check(f"input[type=radio][name='{radio}'][value=No]")
        page.check(f"input[type=checkbox][name='{site.card_field(job.id, 'langs')}'][value=Python]")
        page.check(f"input[type=checkbox][name='{site.card_field(job.id, 'langs')}'][value=SQL]")
        page.get_by_label("I agree").check()
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    answers = site.submissions[0].meta["answers"]
    assert answers == {
        "relocate": ["No"],
        "langs": ["Python", "SQL"],
        "certify": ["I agree"],
        "bio": [],
    }


@pytest.mark.browser
def test_required_radio_group_blocks_natively(page: Page, resume: Path) -> None:
    job = MockJob(id="r1", title="Intern", questions=(STANDARD_QUESTIONS["relocate"],))
    site = lever.make_site(jobs=[job], parse_delay_s=0.1)
    with running_hub(site):
        page.goto(site.apply_url(job.id))
        page.fill("input[name=name]", "Alex Rivera")
        page.fill("input[name=email]", "alex.rivera@example.test")
        page.fill("input[name=phone]", "5125550142")
        attach_resume(page, resume)
        page.click("#btn-submit")
        page.wait_for_timeout(300)
        assert (
            page.locator(
                f"input[type=radio][name='{site.card_field(job.id, 'relocate')}']:invalid"
            ).count()
            == 2
        )
        assert site.submissions == []


# --------------------------------------------------------------------------- later additions


@pytest.mark.browser
def test_tricky_text_survives_the_round_trip(page: Page, resume: Path) -> None:
    site = lever.make_site(parse_delay_s=0.1)
    essay = "Line one\nLine two \u2014 \u201cquoted\u201d & <b>tags</b> \u65e5\u672c\u8a9e"
    with running_hub(site):
        job = open_apply(page, site)
        fill_form(page, site, job)
        page.fill("input[name=name]", "Zo\u00eb O'Neil-\u00d1u\u00f1ez")
        page.fill("textarea[name=comments]", essay)
        attach_resume(page, resume)
        page.click("#btn-submit")
        page.wait_for_url("**/thanks")
    sub = site.submissions[0]
    assert sub.meta["standard"]["name"] == "Zo\u00eb O'Neil-\u00d1u\u00f1ez"
    assert sub.meta["standard"]["comments"] == essay.replace("\n", "\r\n")


@pytest.mark.browser
def test_on_submit_hcaptcha_pops_up_after_the_click_and_finishes_the_submit(
    page: Page, resume: Path
) -> None:
    site = lever.make_site(require_captcha=True, captcha_placement="on_submit", parse_delay_s=0.1)
    with running_hub(site):
        job = open_apply(page, site)
        assert page.locator("iframe, #captcha-overlay").count() == 0
        fill_form(page, site, job)
        attach_resume(page, resume)
        page.click("#btn-submit")
        expect(page.locator("#captcha-overlay[role=dialog]")).to_be_visible()
        assert site.submissions == [] and site.state["captcha_interactions"] == []
        frame = page.frame_locator("#captcha-overlay iframe")
        frame.get_by_role("button", name="Verify").click()
        page.wait_for_url("**/thanks")
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_cookie_consent_banner_on_lever(page: Page) -> None:
    site = lever.make_site(cookie_consent="modal")
    with running_hub(site):
        open_apply(page, site)
        expect(page.locator("#onetrust-banner-sdk")).to_be_visible()
        with pytest.raises(PlaywrightTimeout):
            page.click("input[name=name]", timeout=1_000)
        page.get_by_role("button", name="Accept All Cookies").click()
        page.fill("input[name=name]", "Alex Rivera")
