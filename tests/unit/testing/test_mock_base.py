from __future__ import annotations

import httpx
import pytest
from fastapi import Request

from autoapply.testing.mock_ats.base import MailboxEmailVerifier, MockHub, MockSite, html_page


def _site() -> MockSite:
    site = MockSite("demo", "boards.greenhouse.io")

    @site.app.get("/")
    def index():  # type: ignore[no-untyped-def]
        return html_page(
            "Demo",
            "<form id='f' method='post' action='/submit' enctype='multipart/form-data'>"
            "<input name='first_name'><input type='file' name='resume'><button id='go'>Go</button></form>",
        )

    @site.app.post("/submit")
    async def submit(request: Request):  # type: ignore[no-untyped-def]
        fields, files = await site.read_form(request)
        site.record_submission("/submit", fields, files)
        return html_page("Thanks", "<h1>Thank you for applying</h1>")

    return site


def test_hub_records_multipart_submission() -> None:
    site = _site()
    hub = MockHub()
    hub.add(site)
    with hub:
        resp = httpx.post(
            site.direct_url("/submit"),
            data={"first_name": "Ada"},
            files={"resume": ("cv.pdf", b"%PDF-1.4 hello", "application/pdf")},
        )
        assert resp.status_code == 200
    assert len(site.submissions) == 1
    sub = site.submissions[0]
    assert sub.first("first_name") == "Ada"
    assert sub.file("resume") is not None and sub.file("resume").data.startswith(b"%PDF")


def test_fault_plan_fail_once_then_recover() -> None:
    site = _site()
    site.faults.fail_once.add("/")
    hub = MockHub()
    hub.add(site)
    with hub:
        assert httpx.get(site.direct_url("/")).status_code == 503
        assert httpx.get(site.direct_url("/")).status_code == 200


def test_mailbox_verifier_finds_link_and_code() -> None:
    hub = MockHub()
    hub.mailbox.deliver(
        "a@b.c", "Verify your account", "Click https://x.test/verify?t=1. Code 123456"
    )
    verifier = MailboxEmailVerifier(hub.mailbox)
    assert verifier.wait_for_link(to_address="a@b.c", timeout_s=1) == "https://x.test/verify?t=1"
    assert verifier.wait_for_code(to_address="a@b.c", timeout_s=1) == "123456"
    assert verifier.wait_for_link(to_address="nobody@b.c", timeout_s=0) is None


@pytest.mark.browser
def test_browser_reaches_site_via_localhost_subdomain_and_submits() -> None:
    from playwright.sync_api import sync_playwright

    site = _site()
    hub = MockHub()
    hub.add(site)
    with hub, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(site.url("/"))
        assert ".localhost" in page.url
        page.fill("input[name=first_name]", "Grace")
        page.click("#go")
        page.wait_for_selector("text=Thank you for applying")
        browser.close()
    assert site.submissions[0].first("first_name") == "Grace"
