"""Real-browser smoke test of the pages and their JavaScript (loopback only, headless Chromium)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from autoapply.config import AppPaths, load_config
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.dashboard.server import create_server
from autoapply.db import Repo
from autoapply.models import PendingQuestion, QuestionKind

from .conftest import PDF_BYTES, FakeController, StubHooks

pytestmark = pytest.mark.browser

sync_api = pytest.importorskip("playwright.sync_api")

CSP_LISTENER = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
"""
PAGES = [
    "/",
    "/profile",
    "/answers",
    "/resume",
    "/search",
    "/opportunities",
    "/applications",
    "/runs",
    "/settings",
]


@pytest.fixture
def live(runtime: DashboardRuntime) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = create_server(runtime, "127.0.0.1", port, log_level="warning")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=15)


@pytest.fixture
def page(live: str) -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE *.localhost",
                ],
            )
        except Exception as exc:  # browsers not installed on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        context = browser.new_context(accept_downloads=False)
        context.add_init_script(CSP_LISTENER)
        tab = context.new_tab() if hasattr(context, "new_tab") else context.new_page()
        tab.problems = []  # type: ignore[attr-defined]
        tab.on("pageerror", lambda error: tab.problems.append(f"pageerror: {error}"))
        tab.on(
            "console",
            lambda m: (
                tab.problems.append(f"console: {m.text}")
                if m.type == "error" and not m.text.startswith("Failed to load resource")
                else None
            ),
        )
        yield tab
        browser.close()


def until(predicate: Any, timeout: float = 10.0) -> None:
    """Poll a Python predicate (Playwright's string-eval waits are blocked by the page's own CSP)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


def csp_violations(tab: Any) -> list[str]:
    return list(tab.evaluate("window.__csp || []"))


def test_every_page_loads_without_errors_or_csp_violations(page: Any, live: str) -> None:
    for path in PAGES:
        page.goto(live + path)
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").count() == 1, path
        assert csp_violations(page) == [], path
    assert page.problems == []


def test_profile_form_saves_and_shows_inline_errors(page: Any, live: str, paths: AppPaths) -> None:
    page.goto(live + "/profile")
    page.fill("#f-first_name", "Ada")
    page.fill("#f-email", "not-an-email")
    page.select_option("#f-authorized_to_work_us", "true")
    page.click("text=Save profile")
    page.wait_for_selector(".field-error")
    assert "email address" in page.locator(".field-error").first.inner_text().lower()
    assert page.locator("#f-email").get_attribute("aria-invalid") == "true"
    page.fill("#f-email", "ada@example.test")
    page.click("text=Save profile")
    page.wait_for_selector(".status-ok")
    assert page.locator(".field-error").count() == 0
    profile = load_config(paths).profile
    assert profile.first_name == "Ada" and profile.email == "ada@example.test"
    assert profile.authorized_to_work_us is True
    page.check("input[name='apply.attestations_authorized']")
    page.click("text=Save authorisation")
    until(lambda: page.locator(".status-ok").count() >= 2)
    assert load_config(paths).apply.attestations_authorized is True
    assert csp_violations(page) == [] and page.problems == []


def test_overview_controls_stop_resume_and_run(
    page: Any, live: str, paths: AppPaths, controller: FakeController
) -> None:
    page.goto(live + "/")
    assert not page.locator("#stop-banner").is_visible()
    page.click("button[data-action=stop]")
    page.wait_for_selector("#stop-banner", state="visible")
    assert paths.stop_file.exists() and controller.stop_requests == 1
    page.locator("#stop-banner button").click()
    page.wait_for_selector("#stop-banner", state="hidden")
    assert not paths.stop_file.exists()
    page.click("button[data-mode=dry_run]")  # not ready: the failure is shown, nothing starts
    page.wait_for_selector(".flash-error")
    assert (
        "profile_field" in page.locator("#flash").inner_text()
        or "first_name" in page.locator("#flash").inner_text()
    )
    assert controller.calls == []
    page.select_option("#mode-select", "dry_run")
    page.wait_for_selector(".flash-ok")
    assert load_config(paths).mode.value == "dry_run"
    page.click("button[data-mode=discover_only]")
    until(lambda: page.locator(".flash-ok, .flash-error").count() > 0)
    assert page.problems == [] and csp_violations(page) == []


def test_schedule_toggle_refuses_and_reverts_when_not_ready(
    page: Any, live: str, paths: AppPaths
) -> None:
    page.goto(live + "/")
    page.check("#schedule-toggle")
    page.wait_for_selector(".flash-error")
    until(lambda: not page.locator("#schedule-toggle").is_checked())
    assert load_config(paths).schedule.enabled is False


def test_answers_add_edit_delete_and_resolve(page: Any, live: str, repo: Repo) -> None:
    repo.add_pending_question(
        PendingQuestion(
            question="Which office?", kind=QuestionKind.SINGLE_CHOICE, options=["Austin", "Remote"]
        )
    )
    page.goto(live + "/answers")
    page.select_option("[data-question-id] select", "Remote")
    page.click("[data-action=resolve-question]")
    page.wait_for_selector("[data-question-id]", state="detached")
    assert repo.list_pending_questions() == []
    page.fill("#f-question", "Non-compete?")
    page.fill("#f-answer", "No")
    page.click("text=Save answer >> nth=-1")
    page.wait_for_selector("#answers-table tr[data-answer-id] >> nth=1")
    assert {a.answer for a in repo.list_answers()} == {"Remote", "No"}
    row = page.locator("tr[data-answer-id]", has_text="Non-compete?")
    row.locator("[data-action=edit-answer]").click()
    row.locator("textarea").fill("Yes")
    row.locator("[data-action=save-answer]").click()
    page.wait_for_load_state("networkidle")
    assert "Yes" in {a.answer for a in repo.list_answers()}
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("tr[data-answer-id]", has_text="Non-compete?").locator(
        "[data-action=delete-answer]"
    ).click()
    until(lambda: page.locator("tr[data-answer-id]").count() == 1)
    assert len(repo.list_answers()) == 1


def test_resume_upload_and_knowledge_base_editor(
    page: Any, live: str, paths: AppPaths, hooks: StubHooks, tmp_path: Path
) -> None:
    pdf = tmp_path / "My Resume.pdf"
    pdf.write_bytes(PDF_BYTES)
    page.goto(live + "/resume")
    page.set_input_files("#resume-file", str(pdf))
    page.click("text=Upload resume")
    page.wait_for_selector(".status-ok")
    assert paths.resume_file.read_bytes() == PDF_BYTES
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    page.set_input_files("#resume-file", str(bad))
    page.click("text=Upload resume")
    page.wait_for_selector(".status-error")
    page.click("button[data-action=add-experience]")
    page.locator(".experience [data-k=id]").fill("acme")
    page.locator(".experience [data-k=title]").fill("Product Intern")
    page.locator(".experience [data-k=bullets]").fill("Shipped a feature\nWrote docs")
    page.click("text=Save knowledge base")
    page.wait_for_selector("#kb-form .status-ok")
    assert hooks.saved[-1].experiences[0].bullets == ["Shipped a feature", "Wrote docs"]
    page.locator(".experience [data-k=id]").fill("bad id!")
    page.click("text=Save knowledge base")
    page.wait_for_selector("#kb-form .status-error")
    assert page.problems == [] or all("422" in p or "415" in p for p in page.problems)
    assert csp_violations(page) == []


def test_search_page_families_and_workbook_inspection(
    page: Any, live: str, paths: AppPaths, tmp_path: Path, hooks: StubHooks
) -> None:
    book = tmp_path / "book.xlsx"
    book.write_bytes(b"PK")
    hooks.inspect_result = {
        "sheet": "Verified Opportunities",
        "header_row": 1,
        "kept": 2,
        "data_rows": 3,
        "mapping": {"company": "Company"},
        "sample_rows": [{"Company": "<b>Acme</b>"}],
    }
    page.goto(live + "/search")
    page.click("[data-action=add-family]")
    page.locator("[data-family]").last.locator("[data-k=name]").fill("finance")
    page.locator("[data-family]").last.locator("[data-k=keywords]").fill("analyst\nfp&a")
    page.fill("#f-min_score", "70")
    page.click("text=Save search profile")
    page.wait_for_selector(".status-ok")
    search = load_config(paths).search
    assert (
        search.role_families["finance"].keywords == ["analyst", "fp&a"] and search.min_score == 70
    )
    page.fill("[name='workbook.path']", str(book))
    page.click("[data-action=inspect-workbook]")
    page.wait_for_selector("#inspect-result table")
    assert "Verified Opportunities" in page.locator("#inspect-result").inner_text()
    assert page.locator("#inspect-result b").count() == 0  # rendered as text, not HTML
    page.fill("#f-min_score", "500")
    page.click("text=Save search profile")
    page.wait_for_selector(".field-error")
    assert csp_violations(page) == []
