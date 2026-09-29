"""Behaviour of the blocker pages: what adapters must refuse (bot walls, SSO, closed, applied) or survive."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from autoapply.normalize import host_of
from autoapply.testing.mock_ats import blockers, greenhouse, lever
from autoapply.testing.mock_ats.base import MockHub, MockJob, MockSite, running_hub

LOOPBACK_ONLY = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE *.localhost, EXCLUDE localhost, EXCLUDE 127.0.0.1"
PDF = b"%PDF-1.4\n% fictional resume for Alex Rivera\n%%EOF\n"


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
    path = tmp_path / "Alex Rivera Resume.pdf"
    path.write_bytes(PDF)
    return path


def first_job(site: MockSite) -> MockJob:
    return next(iter(site.jobs.values()))


# --------------------------------------------------------------------------- helpers / dispatch


def test_escape_and_script_json_helpers_neutralise_markup() -> None:
    assert blockers.esc("<a href='x'>&") == "&lt;a href=&#x27;x&#x27;&gt;&amp;"
    assert blockers.esc(None) == ""
    dumped = blockers.json_for_script({"x": "</script><script>alert(1)</script>", "y": "a&b"})
    assert "</script>" not in dumped and "<" not in dumped and "&" not in dumped
    document = blockers.render_document("<Title>", "<p>x</p>")
    assert "<title>&lt;Title&gt;</title>" in document and "rel='icon'" in document


def test_origin_builds_localhost_urls_and_keeps_non_default_ports() -> None:
    origin = blockers.Origin("http", 4321)
    assert origin.on("www.google.com") == "http://www.google.com.localhost:4321"
    assert blockers.Origin("https", 443).on("a.b") == "https://a.b.localhost"
    assert host_of(origin.on("newassets.hcaptcha.com")) == "newassets.hcaptcha.com"


def test_make_site_dispatches_every_kind_and_rejects_unknown_ones() -> None:
    assert blockers.BLOCKER_KINDS == (
        "captcha_visible",
        "invisible_badge",
        "cloudflare_wall",
        "sso_only",
        "closed_posting",
        "already_applied",
        "employer_redirect",
    )
    names = set()
    for kind in blockers.BLOCKER_KINDS:
        site = blockers.make_site(kind, company="globex")
        assert isinstance(site, MockSite)
        names.add(site.name)
        assert site.jobs, kind
    assert names == set(blockers.BLOCKER_KINDS)  # unique default names: all fit in one hub
    with pytest.raises(ValueError, match="unknown blocker"):
        blockers.make_site("nope")
    custom = blockers.make_site(
        "closed_posting", jobs=[MockJob(id="x1", title="X")], variant="filled"
    )
    assert list(custom.jobs) == ["x1"]


def test_default_hosts_are_production_like() -> None:
    assert blockers.captcha_visible().host == "job-boards.greenhouse.io"
    assert blockers.captcha_visible(base="lever").host == "jobs.lever.co"
    assert blockers.invisible_badge().host == "job-boards.greenhouse.io"
    assert blockers.cloudflare_wall("globex").host == "careers.globex.com"
    assert blockers.sso_only(host="jobs.example.org").host == "jobs.example.org"


def test_all_blockers_run_together_in_one_hub() -> None:
    sites = [blockers.make_site(kind) for kind in blockers.BLOCKER_KINDS]
    with running_hub(*sites) as hub:
        assert len(hub.sites) == len(sites)
        assert all(s.port for s in sites)
        # employer_redirect can point at any of its siblings by name
        employer = hub.site("employer_redirect")
        assert employer.url("/").startswith("http://careers.acme.com.localhost:")


def test_captcha_token_checks_and_route_installation_are_idempotent() -> None:
    site = blockers.closed_posting()  # any site without captcha routes yet
    blockers.install_captcha_routes(site)
    routes = len(site.app.routes)
    blockers.install_captcha_routes(site)
    assert len(site.app.routes) == routes
    assert blockers.captcha_token_ok(site, None) is False
    assert blockers.captcha_token_ok(site, "mock-captcha-hcaptcha-1") is False
    site.state["captcha_tokens"].add("mock-captcha-hcaptcha-1")
    assert blockers.captcha_token_ok(site, "mock-captcha-hcaptcha-1") is True
    assert blockers.captcha_interactions(site) == []


def test_captcha_widget_markup_matches_the_vendors_for_every_provider() -> None:
    origin = blockers.Origin("http", 1234)
    expectations = {
        "hcaptcha": (
            "div.h-captcha iframe",
            "Widget containing checkbox for hCaptcha",
            "textarea[name=h-captcha-response]",
        ),
        "recaptcha": ("div.g-recaptcha iframe", "reCAPTCHA", "textarea[name=g-recaptcha-response]"),
        "turnstile": (
            "div.cf-turnstile iframe",
            "Widget containing a Cloudflare security challenge",
            "input[name=cf-turnstile-response]",
        ),
        "arkose": (
            "div#arkose-enforcement iframe",
            "Verification challenge",
            "input[name=fc-token]",
        ),
    }
    for provider, (frame_selector, title, response_selector) in expectations.items():
        doc = BeautifulSoup(blockers.captcha_widget(provider, origin), "html.parser")
        frame = doc.select_one(frame_selector)
        assert frame is not None, provider
        assert title in frame["title"]
        assert doc.select_one(response_selector) is not None, provider
        vendor_host = host_of(frame["src"])
        assert vendor_host in {
            "newassets.hcaptcha.com",
            "www.google.com",
            "google.com",
            "challenges.cloudflare.com",
            "client-api.arkoselabs.com",
        }
        assert blockers.captcha_response_field(provider) in response_selector
    overlay = BeautifulSoup(blockers.captcha_overlay("hcaptcha", origin), "html.parser")
    dialog = overlay.select_one("#captcha-overlay[role=dialog][aria-modal=true]")
    assert dialog is not None and "Verify you are human" in dialog.text
    badge = BeautifulSoup(blockers.recaptcha_badge(origin), "html.parser")
    assert (
        badge.select_one(".grecaptcha-badge iframe[title=reCAPTCHA]")["src"].count("size=invisible")
        == 1
    )  # type: ignore[index]


# --------------------------------------------------------------------------- captcha_visible


@pytest.mark.browser
@pytest.mark.parametrize(
    ("provider", "title", "vendor"),
    [
        ("hcaptcha", "Main content of the hCaptcha challenge", "newassets.hcaptcha.com"),
        ("recaptcha", "recaptcha challenge expires in two minutes", "google.com"),
        (
            "turnstile",
            "Widget containing a Cloudflare security challenge",
            "challenges.cloudflare.com",
        ),
        ("arkose", "Verification challenge", "client-api.arkoselabs.com"),
    ],
)
def test_overlay_challenge_blocks_the_form_and_records_no_interaction(
    page: Page, provider: str, title: str, vendor: str
) -> None:
    site = blockers.captcha_visible(provider=provider)  # type: ignore[arg-type]
    with running_hub(site):
        page.goto(site.job_url(first_job(site).id))  # type: ignore[attr-defined]
        overlay = page.locator("#captcha-overlay[role=dialog]")
        expect(overlay).to_be_visible()
        frame = overlay.locator("iframe")
        assert frame.get_attribute("title") == title
        assert host_of(frame.get_attribute("src") or "") == vendor
        assert frame.bounding_box() is not None and frame.bounding_box()["width"] > 250  # type: ignore[index]
        assert "Verify you are human" in overlay.inner_text()
        with pytest.raises(PlaywrightTimeout):
            page.click("#first_name", timeout=1_000)  # covered by the overlay
        assert blockers.captcha_interactions(site) == [] and site.submissions == []


@pytest.mark.browser
@pytest.mark.parametrize(
    ("provider", "container", "title_part"),
    [
        ("hcaptcha", "div.h-captcha", "hCaptcha"),
        ("recaptcha", "div.g-recaptcha", "reCAPTCHA"),
        ("turnstile", "div.cf-turnstile", "Cloudflare"),
        ("arkose", "div#arkose-enforcement", "Verification challenge"),
    ],
)
def test_inline_challenge_widget_sits_in_the_form_above_the_submit_button(
    page: Page, provider: str, container: str, title_part: str
) -> None:
    site = blockers.captcha_visible(provider=provider, placement="inline")  # type: ignore[arg-type]
    with running_hub(site):
        page.goto(site.job_url(first_job(site).id))  # type: ignore[attr-defined]
        widget = page.locator(f"form#application-form {container} iframe")
        expect(widget).to_be_attached()
        assert title_part in (widget.get_attribute("title") or "")
        assert page.locator("#captcha-overlay").count() == 0
        assert blockers.captcha_interactions(site) == []


@pytest.mark.browser
def test_a_human_can_solve_the_overlay_and_only_then_the_form_submits(
    page: Page, resume: Path
) -> None:
    site = blockers.captcha_visible(provider="recaptcha")
    with running_hub(site):
        job = first_job(site)
        page.goto(site.job_url(job.id))  # type: ignore[attr-defined]
        page.frame_locator("#captcha-overlay iframe").get_by_role("button", name="Verify").click()
        expect(page.locator("#captcha-overlay")).to_have_count(0)
        assert len(blockers.captcha_interactions(site)) == 1
        page.fill("#first_name", "Alex")
        page.fill("#last_name", "Rivera")
        page.fill("#email", "alex.rivera@example.test")
        page.fill("#phone", "5125550142")
        page.click("#country")
        page.get_by_role("option", name="United States", exact=True).click()
        page.locator("#resume").set_input_files(str(resume))
        for key, label in (
            ("work_auth", "Yes"),
            ("sponsorship", "No"),
            ("referral", "Company website"),
        ):
            page.click(f"#{site.field_id(job.id, key)}")  # type: ignore[attr-defined]
            page.get_by_role("option", name=label, exact=True).click()
        page.fill(f"#{site.field_id(job.id, 'why_role')}", "I enjoy building products.")  # type: ignore[attr-defined]
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_be_visible()
    assert len(site.submissions) == 1


@pytest.mark.browser
def test_lever_based_challenge_uses_hcaptcha_by_default(page: Page) -> None:
    site = blockers.captcha_visible(base="lever", placement="inline")
    assert isinstance(site, lever.LeverSite)
    with running_hub(site):
        page.goto(site.apply_url(first_job(site).id))
        frame = page.locator("div.h-captcha iframe")
        assert host_of(frame.get_attribute("src") or "") == "newassets.hcaptcha.com"
        assert blockers.captcha_interactions(site) == []


@pytest.mark.browser
def test_vendor_frames_are_reachable_only_through_localhost_hosts(page: Page) -> None:
    site = blockers.captcha_visible(provider="turnstile", placement="inline")
    with running_hub(site):
        page.goto(site.job_url(first_job(site).id))  # type: ignore[attr-defined]
        frames = [f for f in page.frames if f != page.main_frame]
        assert frames and all(".localhost" in f.url for f in frames)
        assert all(f.url.startswith("http://challenges.cloudflare.com.localhost:") for f in frames)


# --------------------------------------------------------------------------- invisible_badge


@pytest.mark.browser
def test_invisible_badge_is_only_a_badge_and_the_form_stays_submittable(
    page: Page, resume: Path
) -> None:
    site = blockers.invisible_badge()
    with running_hub(site):
        job = first_job(site)
        page.goto(site.job_url(job.id))  # type: ignore[attr-defined]
        badge = page.locator("div.grecaptcha-badge")
        assert badge.count() == 1
        frame = badge.locator("iframe[title=reCAPTCHA]")
        src = frame.get_attribute("src") or ""
        assert host_of(src) == "google.com" and "/recaptcha/enterprise/anchor" in src
        assert "size=invisible" in src
        # partially off screen, fixed bottom right, like the real badge
        style = badge.get_attribute("style") or ""
        assert "position:fixed" in style.replace(" ", "") and "right:-186px" in style.replace(
            " ", ""
        )
        # nothing that looks like a challenge
        assert (
            page.locator(
                ".g-recaptcha, .h-captcha, .cf-turnstile, #captcha-overlay, [role=dialog]"
            ).count()
            == 0
        )
        assert page.frame_locator("div.grecaptcha-badge iframe").locator("#checkbox").count() == 0
        page.fill("#first_name", "Alex")
        page.fill("#last_name", "Rivera")
        page.fill("#email", "alex.rivera@example.test")
        page.fill("#phone", "5125550142")
        page.click("#country")
        page.get_by_role("option", name="United States", exact=True).click()
        page.locator("#resume").set_input_files(str(resume))
        for key, label in (
            ("work_auth", "Yes"),
            ("sponsorship", "No"),
            ("referral", "Company website"),
        ):
            page.click(f"#{site.field_id(job.id, key)}")  # type: ignore[attr-defined]
            page.get_by_role("option", name=label, exact=True).click()
        page.fill(f"#{site.field_id(job.id, 'why_role')}", "I enjoy building products.")  # type: ignore[attr-defined]
        page.get_by_role("button", name="Submit application").click()
        expect(page.locator("#application-confirmation")).to_contain_text("Thank you for applying.")
    assert len(site.submissions) == 1
    assert blockers.captcha_interactions(site) == []


# --------------------------------------------------------------------------- cloudflare_wall


def test_cloudflare_wall_answers_every_request_with_the_managed_challenge() -> None:
    site = blockers.cloudflare_wall()
    with running_hub(site):
        for method, path in (
            ("GET", "/"),
            ("GET", "/jobs/7001"),
            ("GET", "/a/b/c?x=1"),
            ("POST", "/apply"),
        ):
            response = httpx.request(method, site.direct_url(path))
            assert response.status_code == 403, (method, path)
            assert response.headers["cf-mitigated"] == "challenge"
            assert "cloudflare" in response.headers["server"]  # uvicorn prepends its own token
            doc = BeautifulSoup(response.text, "html.parser")
            assert doc.title is not None and doc.title.text == "Just a moment..."
            assert "Verify you are human" in doc.text
            assert "Ray ID" in doc.text and "Cloudflare" in doc.text
            frame = doc.select_one("#challenge-stage iframe")
            assert frame is not None
            assert frame["title"] == "Widget containing a Cloudflare security challenge"
            assert host_of(frame["src"]) == "challenges.cloudflare.com"
        assert httpx.get(site.direct_url("/jobs/1")).status_code == 403
    assert site.submissions == []


def test_cloudflare_wall_status_is_configurable() -> None:
    site = blockers.cloudflare_wall(status=503)
    with running_hub(site):
        assert httpx.get(site.direct_url("/")).status_code == 503


@pytest.mark.browser
def test_cloudflare_wall_in_a_browser_never_lets_a_solve_attempt_through(page: Page) -> None:
    site = blockers.cloudflare_wall("globex")
    with running_hub(site):
        response = page.goto(site.url("/jobs/7001"))
        assert response is not None and response.status == 403
        assert page.title() == "Just a moment..."
        expect(page.locator("#challenge-stage iframe")).to_be_visible()
        expect(page.locator("body")).to_contain_text(
            "careers.globex.com needs to review the security"
        )
        # even a human click on the widget is rejected: there is no way through
        frame = page.frame_locator("#challenge-stage iframe")
        frame.get_by_role("checkbox").click()
        expect(frame.locator("#label")).to_have_text("Verification failed. Try again.")
        interactions = blockers.captcha_interactions(site)
        assert len(interactions) == 1 and interactions[0]["accepted"] is False
        assert page.title() == "Just a moment..."


# --------------------------------------------------------------------------- sso_only


def test_sso_only_pages_offer_nothing_but_the_sso_button() -> None:
    site = blockers.sso_only()
    with running_hub(site):
        for path in ("/", "/jobs/7001", "/careers/anything/here"):
            response = httpx.get(site.direct_url(path))
            assert response.status_code == 200
            doc = BeautifulSoup(response.text, "html.parser")
            assert "Sign in with your company SSO" in doc.text
            assert [a["href"] for a in doc.select("a")] == ["/sso/start"]
            assert doc.select_one("a#sso-login").text == "Sign in with your company SSO"  # type: ignore[union-attr]
            assert doc.select("form, input, textarea, select") == []
        assert site.state["idp_visits"] == []
        start = httpx.get(site.direct_url("/sso/start"), follow_redirects=False)
        assert start.status_code == 302 and start.headers["location"] == "/sso/idp"
        idp = httpx.get(site.direct_url("/sso/idp"))
        assert "Okta" in idp.text and BeautifulSoup(idp.text, "html.parser").select_one(
            "input[type=password]"
        )
        assert httpx.post(site.direct_url("/sso/idp"), data={"username": "x"}).status_code == 401
    assert len(site.state["idp_visits"]) == 1 and site.submissions == []


@pytest.mark.browser
def test_sso_only_in_a_browser(page: Page) -> None:
    site = blockers.sso_only("globex", idp_name="Acme Identity")
    with running_hub(site):
        page.goto(site.url("/jobs/7001"))
        expect(page.get_by_role("link", name="Sign in with your company SSO")).to_be_visible()
        assert page.locator("input").count() == 0
        page.get_by_role("link", name="Sign in with your company SSO").click()
        page.wait_for_url("**/sso/idp")
        expect(page.locator("h1")).to_have_text("Acme Identity")


# --------------------------------------------------------------------------- closed / applied


@pytest.mark.parametrize(
    ("variant", "text"),
    [
        ("closed", "This job is no longer accepting applications."),
        ("filled", "Sorry, this position has been filled."),
        ("expired", "This posting has expired and can no longer be applied to."),
    ],
)
def test_closed_posting_texts_come_with_http_200_and_no_form(variant: str, text: str) -> None:
    site = blockers.closed_posting(variant=variant)  # type: ignore[arg-type]
    with running_hub(site):
        for path in ("/", "/jobs/7001", "/acme/jobs/7001/apply"):
            response = httpx.get(site.direct_url(path))
            assert response.status_code == 200, path
            doc = BeautifulSoup(response.text, "html.parser")
            assert doc.select_one("#posting-closed[role=alert]").text == text  # type: ignore[union-attr]
            assert doc.select("form, input, textarea") == []
        title = BeautifulSoup(httpx.get(site.direct_url("/jobs/7001")).text, "html.parser")
        assert "Business Operations Intern" in title.h1.text  # type: ignore[union-attr]
    assert site.submissions == []


def test_closed_posting_not_found_variant_is_http_404() -> None:
    site = blockers.closed_posting(variant="not_found")
    with running_hub(site):
        for path in ("/", "/jobs/7001"):
            response = httpx.get(site.direct_url(path))
            assert response.status_code == 404
            assert "Sorry, we couldn't find anything here" in response.text


def test_closed_posting_uses_the_requested_job_title_and_survives_faults() -> None:
    jobs = [MockJob(id="a1", title="Alpha Intern"), MockJob(id="b2", title="Beta Intern")]
    site = blockers.closed_posting(jobs=jobs)
    site.faults.fail_once.add("/jobs/b2")
    with running_hub(site):
        assert httpx.get(site.direct_url("/jobs/b2")).status_code == 503
        assert "Beta Intern" in httpx.get(site.direct_url("/jobs/b2")).text
        assert "Alpha Intern" in httpx.get(site.direct_url("/jobs/a1")).text


@pytest.mark.browser
def test_closed_posting_in_a_browser(page: Page) -> None:
    site = blockers.closed_posting()
    with running_hub(site):
        response = page.goto(site.url("/jobs/7001"))
        assert response is not None and response.status == 200
        expect(page.locator("body")).to_contain_text("This job is no longer accepting applications")
        assert page.get_by_role("button", name="Submit").count() == 0


def test_already_applied_shows_the_message_and_no_form() -> None:
    site = blockers.already_applied(message="You've already applied to this job!")
    with running_hub(site):
        response = httpx.get(site.direct_url("/jobs/7001"))
    doc = BeautifulSoup(response.text, "html.parser")
    assert response.status_code == 200
    assert (
        doc.select_one("#already-applied[role=status]").text
        == "You've already applied to this job!"
    )  # type: ignore[union-attr]
    assert "Under review" in doc.text
    assert doc.select("form, input, textarea") == []


def test_already_applied_default_wording() -> None:
    site = blockers.already_applied()
    with running_hub(site):
        text = httpx.get(site.direct_url("/anything")).text
    assert "You have already applied to this job." in text


# --------------------------------------------------------------------------- employer_redirect


def _employer_with_greenhouse(**options: object) -> tuple[MockSite, greenhouse.GreenhouseSite]:
    target = greenhouse.make_site(company="globex")
    job = next(iter(target.jobs.values()))
    employer = blockers.employer_redirect(
        target="greenhouse",
        target_path=f"/globex/jobs/{job.id}",
        jobs=[MockJob(id="7001", title="Business Operations Intern")],
        **options,  # type: ignore[arg-type]
    )
    return employer, target


def test_employer_page_links_to_the_redirector_and_redirects_lazily_to_the_hub_target() -> None:
    employer, target = _employer_with_greenhouse()
    with running_hub(employer, target), httpx.Client(follow_redirects=False) as client:
        page = client.get(employer.direct_url("/jobs/7001"))
        doc = BeautifulSoup(page.text, "html.parser")
        link = doc.select_one("a#apply-now")
        assert link is not None and link.text == "Apply now" and link["href"] == "/jobs/7001/apply"
        assert link.get("target") is None
        first = client.get(employer.direct_url("/jobs/7001/apply"))
        assert first.status_code == 302 and first.headers["location"] == "/jobs/7001/apply/hop/1"
        last = client.get(employer.direct_url(first.headers["location"]))
        assert last.status_code == 302
        assert last.headers["location"] == target.url("/globex/jobs/4100200")
        assert host_of(last.headers["location"]) == "job-boards.greenhouse.io"
    assert employer.state["redirects_served"] == 1


def test_redirect_hops_status_and_callable_target_path_are_honoured() -> None:
    target = lever.make_site()
    employer = blockers.employer_redirect(
        target="lever",
        target_path=lambda other: f"/acme/{next(iter(other.jobs))}/apply",
        hops=3,
        status=307,
    )
    with running_hub(employer, target), httpx.Client(follow_redirects=False) as client:
        location = "/jobs/7001/apply"
        hops = 0
        while True:
            response = client.get(employer.direct_url(location))
            assert response.status_code == 307
            location = response.headers["location"]
            if location.startswith("http"):
                break
            hops += 1
        assert (
            hops == 3
        )  # three tracking hops (same site) before the final redirect to the hub target
        # the callable is evaluated per request: a job added later is picked up
        target.jobs.clear()
        target.jobs["fresh-job"] = MockJob(id="fresh-job", title="Fresh")
        again = client.get(employer.direct_url("/jobs/7001/apply/hop/3"))
        assert again.headers["location"].endswith("/acme/fresh-job/apply")


def test_zero_hops_redirects_straight_away_and_missing_targets_answer_502() -> None:
    target = lever.make_site()
    direct = blockers.employer_redirect(target="lever", hops=0, name="direct")
    orphan = blockers.employer_redirect(
        target="workday", name="orphan"
    )  # nobody registered "workday"
    with running_hub(direct, orphan, target), httpx.Client(follow_redirects=False) as client:
        response = client.get(direct.direct_url("/jobs/7001/apply"))
        assert response.status_code == 302 and response.headers["location"] == target.url("/")
        bad = client.get(orphan.direct_url("/jobs/7001/apply/hop/1"))
        assert bad.status_code == 502 and "workday" in bad.text
    assert orphan.state["redirects_served"] == 0


def test_a_detached_site_answers_502_instead_of_raising() -> None:
    employer = blockers.employer_redirect(target="lever", hops=0)
    hub = MockHub()
    hub.add(employer)
    hub.start()
    try:
        assert employer.hub is hub
        response = httpx.get(employer.direct_url("/jobs/7001/apply"), follow_redirects=False)
        assert response.status_code == 502
    finally:
        hub.stop()


@pytest.mark.browser
def test_apply_now_link_lands_on_the_other_site_in_a_real_browser(page: Page) -> None:
    employer, target = _employer_with_greenhouse(hops=2)
    with running_hub(employer, target):
        page.goto(employer.url("/jobs/7001"))
        page.get_by_role("link", name="Apply now").click()
        page.wait_for_url("**/globex/jobs/4100200")
        assert host_of(page.url) == "job-boards.greenhouse.io"
        expect(page.locator("form#application-form")).to_be_visible()
    assert "/jobs/7001/apply" in employer.page_views
    assert target.page_views == ["/globex/jobs/4100200"]


@pytest.mark.browser
def test_new_tab_variant_opens_the_application_in_a_popup(page: Page) -> None:
    employer, target = _employer_with_greenhouse(new_tab=True)
    with running_hub(employer, target):
        page.goto(employer.url("/jobs/7001"))
        link = page.locator("a#apply-now")
        assert link.get_attribute("target") == "_blank"
        with page.context.expect_page() as popup_info:
            link.click()
        popup = popup_info.value
        popup.wait_for_url("**/globex/jobs/4100200")
        assert host_of(popup.url) == "job-boards.greenhouse.io"
        assert host_of(page.url) == "careers.acme.com"  # the employer tab stays where it was


# --------------------------------------------------------------------------- slow render for the simple blockers


@pytest.mark.browser
@pytest.mark.parametrize(
    ("factory", "path", "selector", "text"),
    [
        (
            blockers.closed_posting,
            "/jobs/7001",
            "#posting-closed",
            "no longer accepting applications",
        ),
        (blockers.sso_only, "/jobs/7001", "#sso-login", "Sign in with your company SSO"),
        (blockers.already_applied, "/jobs/7001", "#already-applied", "already applied"),
        (blockers.employer_redirect, "/jobs/7001", "#apply-now", "Apply now"),
    ],
)
def test_slow_render_hides_the_blocker_content_until_the_delay_has_passed(
    page: Page, factory: object, path: str, selector: str, text: str
) -> None:
    site = factory(render_delay_s=1.2)  # type: ignore[operator]
    with running_hub(site):
        response = page.goto(site.url(path))
        assert response is not None and response.status == 200
        assert page.locator(selector).count() == 0  # an early check sees nothing yet
        expect(page.locator("#content-mount .loading")).to_be_visible()
        expect(page.locator(selector)).to_contain_text(text)


@pytest.mark.browser
def test_captcha_visible_passes_render_delay_and_cookie_options_through(page: Page) -> None:
    site = blockers.captcha_visible(placement="inline", render_delay_s=1.2, cookie_consent="bar")
    with running_hub(site):
        page.goto(site.job_url(first_job(site).id))  # type: ignore[attr-defined]
        assert (
            page.locator("iframe[title=reCAPTCHA]").count() == 0
        )  # the form (and widget) come later
        expect(page.locator("#onetrust-banner-sdk")).to_be_visible()
        expect(page.locator(".g-recaptcha iframe")).to_be_attached()
