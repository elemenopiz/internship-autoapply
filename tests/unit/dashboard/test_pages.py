"""HTML pages: rendering, escaping of hostile data, and the no-inline-script/style/CDN rules."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoapply.dashboard.app import STATIC_DIR, TEMPLATE_DIR
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    Experience,
    KnowledgeBase,
    PendingQuestion,
    QuestionKind,
    Reason,
    RunMode,
    RunReport,
    ScoreResult,
)

from .conftest import HOSTILE, csrf_from_html

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
HOSTILE_ATTR = "' onmouseover='alert(1)' x='"
JS_URL = "javascript:alert(document.cookie)"


class Audit(HTMLParser):
    """Collects everything that could execute or load something external."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.inline_scripts = 0
        self.style_tags = 0
        self.style_attrs = 0
        self.event_attrs: list[str] = []
        self.external: list[str] = []
        self.js_hrefs: list[str] = []
        self.meta_csrf: str | None = None
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        attributes = dict(attrs)
        if tag == "script" and "src" not in attributes:
            self.inline_scripts += 1
        if tag == "style":
            self.style_tags += 1
        for name, value in attrs:
            if name == "style":
                self.style_attrs += 1
            if name.startswith("on"):
                self.event_attrs.append(name)
            if name in {"href", "src", "action"} and value:
                if re.match(r"(?i)\s*(javascript|data:text/html|vbscript):", value):
                    self.js_hrefs.append(value)
                if re.match(r"(?i)\s*(https?:)?//", value) and tag in {
                    "script",
                    "link",
                    "img",
                    "iframe",
                }:
                    self.external.append(value)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.meta_csrf = attributes.get("content")


def audit(html: str) -> Audit:
    parser = Audit()
    parser.feed(html)
    return parser


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_and_obeys_the_csp_rules(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    result = audit(response.text)
    assert result.inline_scripts == 0 and result.style_tags == 0 and result.style_attrs == 0
    assert result.event_attrs == [] and result.external == [] and result.js_hrefs == []
    assert result.meta_csrf == csrf_from_html(response.text)
    assert '<script src="/static/app.js"' in response.text
    assert 'rel="stylesheet" href="/static/app.css"' in response.text
    for nav in (
        "/profile",
        "/answers",
        "/resume",
        "/search",
        "/opportunities",
        "/applications",
        "/runs",
    ):
        assert f'href="{nav}"' in response.text


def test_templates_and_static_files_contain_no_external_urls_or_inline_handlers() -> None:
    for path in [*TEMPLATE_DIR.glob("*.html"), *STATIC_DIR.glob("*")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"https?://|//cdn", text), path
        assert "innerHTML" not in text and "eval(" not in text and "document.write" not in text, (
            path
        )
    for path in TEMPLATE_DIR.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert (
            not re.search(r"\|\s*safe\b(?!_)", text) and "autoescape false" not in text.lower()
        ), path
        assert not re.search(r"\son[a-z]+=", text), path
        assert "<style" not in text and ' style="' not in text, path


def test_page_token_works_for_posts(bare_client: TestClient) -> None:
    token = csrf_from_html(bare_client.get("/profile").text)
    assert (
        bare_client.put(
            "/api/profile", json={"city": "Austin"}, headers={"X-CSRF-Token": token}
        ).status_code
        == 200
    )


# ------------------------------------------------------------------------------------------ escaping


def hostile_world(client: TestClient, seed: Any, repo: Any, paths: Any, hooks: Any) -> None:
    profile = dict.fromkeys(
        ("preferred_name", "pronouns", "address_line2", "referral_source"), HOSTILE_ATTR
    )
    profile.update(
        {
            "first_name": HOSTILE,
            "last_name": HOSTILE,
            "city": HOSTILE,
            "school": HOSTILE,
            "major": HOSTILE,
            "gpa": "<b>9</b>",
        }
    )
    assert client.put("/api/profile", json=profile).status_code == 200
    assert (
        client.put(
            "/api/search",
            json={
                "target_term": HOSTILE,
                "include_keywords": [HOSTILE],
                "company_denylist": [HOSTILE],
                "role_families": {HOSTILE: {"keywords": [HOSTILE], "weight": 1}},
            },
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/settings",
            json={
                "workbook": {"path": HOSTILE, "sheet": HOSTILE},
                "boards": {},
                "llm": {"model": "gpt-4.1-mini"},
                "apply": {"email": {"username": HOSTILE, "imap_host": "imap.example.test"}},
            },
        ).status_code
        == 200
    )
    op = seed(HOSTILE, HOSTILE, url=JS_URL, location=HOSTILE, description=HOSTILE)
    repo.set_score(
        op.id, ScoreResult(score=70, passed=True, reasons=[HOSTILE], penalties=[HOSTILE])
    )
    op2 = seed(HOSTILE, "Safe Title", url="https://jobs.example.test/x?a=1&b=2")
    app = repo.create_application(op2.id, RunMode.FULL_AUTO)
    repo.finish_application(
        app.id,
        ApplyResult(
            status=ApplicationStatus.NEEDS_MANUAL,
            reason=Reason.OTHER,
            message=HOSTILE,
            confirmation=HOSTILE,
            artifacts=[f"artifacts/{HOSTILE_ATTR}.png".replace("/", "_")],
        ),
        docs={"resume": HOSTILE},
    )
    client.post(
        "/api/answers", json={"question": HOSTILE, "answer": HOSTILE, "intent": "non_compete"}
    )
    repo.add_pending_question(
        PendingQuestion(
            question=HOSTILE,
            company=HOSTILE,
            kind=QuestionKind.SINGLE_CHOICE,
            options=[HOSTILE, HOSTILE_ATTR],
        )
    )
    rid = repo.start_run(RunMode.DRY_RUN, "manual")
    repo.finish_run(rid, RunReport(errors=[HOSTILE], stopped_reason="error"))
    hooks.kb = KnowledgeBase(
        source="resume",
        skills=[HOSTILE],
        experiences=[Experience(id="e1", title=HOSTILE, organization=HOSTILE, bullets=[HOSTILE])],
    )


@pytest.mark.parametrize(
    "path", [*PAGES, "/opportunities?search=" + "%3Cscript%3E", "/applications?status=needs_manual"]
)
def test_hostile_strings_are_escaped_on_every_page(
    client: TestClient, seed: Any, repo: Any, paths: Any, hooks: Any, path: str
) -> None:
    hostile_world(client, seed, repo, paths, hooks)
    response = client.get(path)
    assert response.status_code == 200
    html = response.text
    for raw in ("<script>alert", "<img src=x", "onerror=alert(1)>", HOSTILE_ATTR):
        assert raw not in html, (path, raw)
    result = audit(html)
    assert result.inline_scripts == 0 and result.event_attrs == [] and result.style_attrs == 0
    assert result.tags.count("img") == 0 and result.js_hrefs == []
    assert result.tags.count("script") == 1  # only the app bundle


def test_hostile_data_appears_only_in_escaped_form(
    client: TestClient, seed: Any, repo: Any, paths: Any, hooks: Any
) -> None:
    hostile_world(client, seed, repo, paths, hooks)
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in client.get("/opportunities").text
    assert "&lt;script&gt;" in client.get("/profile").text
    assert "&lt;script&gt;" in client.get("/answers").text
    assert "&lt;script&gt;" in client.get("/applications").text
    assert "&lt;script&gt;" in client.get("/runs").text
    assert "&lt;script&gt;" in client.get("/search").text
    assert "&lt;script&gt;" in client.get("/resume").text
    assert "&lt;script&gt;" in client.get("/settings").text


def test_unsafe_link_schemes_are_not_rendered_as_links(client: TestClient, seed: Any) -> None:
    seed("Acme", "Bad Link Role", url=JS_URL)
    seed("Acme", "Good Link Role", url="https://jobs.example.test/ok")
    html = client.get("/opportunities").text
    assert "javascript:" not in html
    assert 'href="https://jobs.example.test/ok"' in html and 'rel="noopener noreferrer"' in html


def test_hostile_filter_parameters_are_escaped_and_ignored(client: TestClient) -> None:
    html = client.get(
        f"/opportunities?search={HOSTILE}&min_score=abc&status=%3Cb%3E&source=x&limit=9999"
    ).text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "Ignored" in html


# ------------------------------------------------------------------------------------------ page content


def test_overview_shows_readiness_checklist_and_controls(client: TestClient) -> None:
    html = client.get("/").text
    for label in ("Run now", "Dry run", "Discover only", "Stop", "Resume"):
        assert f">{label}</button>" in html
    assert 'data-action="run" data-mode="dry_run"' in html and 'data-action="unstop"' in html
    assert "profile_field_missing" in html and "set_openai_key.ps1" in html
    assert 'id="schedule-toggle"' in html and "<progress" in html
    assert re.search(r'name="[^"]*key[^"]*"', html, re.I) is None  # no key input anywhere


def test_overview_ready_state_and_cap_usage(client: TestClient, make_ready: Any, seed: Any) -> None:
    make_ready()
    seed(status=ApplicationStatus.SUBMITTED)
    html = client.get("/").text
    assert 'id="ready-ok">' in html or 'id="ready-ok" ' in html
    assert re.search(r'<progress[^>]*max="5"[^>]*value="1"', html)


def test_stop_banner_visibility(client: TestClient, paths: Any) -> None:
    hidden = client.get("/").text
    assert re.search(r'id="stop-banner"[^>]*\shidden', hidden)
    client.post("/api/stop")
    shown = client.get("/").text
    assert not re.search(r'id="stop-banner"[^>]*\shidden', shown)


def test_automation_risk_banner_only_when_platforms_enabled(client: TestClient) -> None:
    assert 'id="risk-banner"' not in client.get("/").text
    client.put("/api/settings", json={"platforms": {"indeed": True}})
    for path in ("/", "/search"):
        html = client.get(path).text
        assert 'id="risk-banner"' in html and "Indeed" in html and "at your own risk" in html


def test_profile_page_has_all_fields_and_the_attestation_text(client: TestClient) -> None:
    html = client.get("/profile").text
    for name in (
        "first_name",
        "email",
        "phone",
        "address_line1",
        "graduation_date",
        "authorized_to_work_us",
        "requires_sponsorship",
        "linkedin_url",
        "eeo.gender",
        "eeo.disability_status",
        "referral_source",
    ):
        assert f'name="{name}"' in html, name
    assert 'name="apply.attestations_authorized"' in html
    assert (
        "tick certification, consent and signature boxes" in html and "electronic signature" in html
    )
    client.put("/api/settings", json={"apply": {"attestations_authorized": True}})
    assert re.search(r'name="apply.attestations_authorized" checked', client.get("/profile").text)


def test_profile_values_are_prefilled(client: TestClient) -> None:
    client.put(
        "/api/profile",
        json={"first_name": "Ada", "authorized_to_work_us": True, "requires_sponsorship": False},
    )
    html = client.get("/profile").text
    assert 'value="Ada"' in html
    assert re.search(
        r'name="authorized_to_work_us"[^>]*>\s*<option value="">Not answered</option>\s*<option value="true" selected>',
        html,
    )
    assert re.search(r'<option value="false" selected>', html)


def test_answers_page_lists_saved_answers_and_pending_questions(
    client: TestClient, repo: Any
) -> None:
    client.post(
        "/api/answers", json={"question": "Felony?", "answer": "No", "intent": "felony_conviction"}
    )
    q = repo.add_pending_question(
        PendingQuestion(
            question="Which office?",
            company="Acme",
            kind=QuestionKind.SINGLE_CHOICE,
            options=["Austin", "Remote"],
        )
    )
    html = client.get("/answers").text
    assert "felony_conviction" in html and "Felony?" in html
    assert f'data-question-id="{q.id}"' in html and '<option value="Austin">' in html
    assert 'data-action="resolve-question"' in html and 'data-action="delete-answer"' in html
    assert 'data-action="edit-answer"' in html


def test_resume_page_embeds_the_kb_as_an_escaped_data_attribute(
    client: TestClient, hooks: Any
) -> None:
    hooks.kb = KnowledgeBase(
        source="resume", experiences=[Experience(id="e1", title='Quote " and <b>')]
    )
    html = client.get("/resume").text
    assert 'data-kb="{&#34;source&#34;: &#34;resume&#34;' in html
    assert "<b>" not in html and 'id="exp-template"' in html
    assert (
        'data-action="build-kb"' in html
        and 'type="file"' in html
        and 'accept="application/pdf,.pdf"' in html
    )


def test_resume_page_survives_a_missing_knowledge_base_module(
    client: TestClient, runtime: Any
) -> None:
    from autoapply.dashboard.deps import FeatureUnavailableError

    def broken(paths: Any) -> Any:
        raise FeatureUnavailableError("tailoring is not available")

    runtime.load_kb = broken
    response = client.get("/resume")
    assert response.status_code == 200 and "tailoring is not available" in response.text
    assert client.get("/api/kb").status_code == 501
    assert client.get("/api/kb").json()["code"] == "feature_unavailable"


def test_search_page_has_families_platforms_boards_and_inspector(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={"boards": {"greenhouse": ["stripe", "notion"]}, "workbook": {"path": "/x/book.xlsx"}},
    )
    html = client.get("/search").text
    assert html.count("data-family") >= 7 and "product manager" in html
    for name in (
        "platforms.workbook",
        "platforms.linkedin",
        "platforms.indeed",
        "boards.greenhouse",
        "workbook.path",
        "min_score",
        "include_keywords",
        "company_denylist",
    ):
        assert f'name="{name}"' in html, name
    assert "stripe\nnotion" in html and 'value="/x/book.xlsx"' in html
    assert 'data-action="inspect-workbook"' in html


def test_opportunities_page_filters_paging_and_reasons(
    client: TestClient, seed: Any, repo: Any
) -> None:
    for i in range(3):
        op = seed(f"Company {i}", f"Role {i}")
        repo.set_score(
            op.id, ScoreResult(score=90 - i, passed=True, reasons=[f"reason number {i}"])
        )
    html = client.get("/opportunities?limit=2").text
    assert "reason number 0" in html and "Company 2" not in html
    assert 'href="/opportunities?limit=2&amp;offset=2"' in html and "1-2 of 3" in html
    second = client.get("/opportunities?limit=2&offset=2").text
    assert "Company 2" in second and "Previous" in second and "Next" not in second
    assert "Company 0" not in client.get("/opportunities?search=company+1").text
    assert 'data-action="mark-applied"' in html


def test_applications_page_shows_reason_files_and_manual_button(
    client: TestClient, seed: Any, repo: Any, paths: Any
) -> None:
    op = seed("Acme", "Role")
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    (paths.artifacts_dir / "1").mkdir(parents=True)
    (paths.artifacts_dir / "1" / "shot.png").write_bytes(b"x")
    repo.finish_application(
        app.id,
        ApplyResult(
            status=ApplicationStatus.NEEDS_MANUAL,
            reason=Reason.MISSING_ANSWER,
            artifacts=["artifacts/1/shot.png"],
        ),
    )
    html = client.get("/applications").text
    assert "A required question has no saved answer" in html
    assert 'href="/files/artifacts/1/shot.png"' in html
    assert f'data-opportunity-id="{op.id}"' in html and "Mark applied manually" in html
    assert "Acme" in client.get("/applications?status=needs_manual").text
    assert "Acme" not in client.get("/applications?status=submitted").text
    done = seed("Done Co", "Done Role", status=ApplicationStatus.SUBMITTED)
    assert f'data-opportunity-id="{done.id}"' not in client.get("/applications").text


def test_runs_and_settings_pages(client: TestClient, repo: Any) -> None:
    rid = repo.start_run(RunMode.FULL_AUTO, "schedule")
    repo.finish_run(rid, RunReport(discovered=12, submitted=3, stopped_reason="cap_reached"))
    html = client.get("/runs").text
    assert ">12<" in html and "cap reached" in html and "schedule" in html
    settings = client.get("/settings").text
    for name in (
        "daily_cap",
        "timezone",
        "schedule.run_times",
        "schedule.days_of_week",
        "apply.min_delay_s",
        "apply.email.imap_host",
        "apply.screenshots",
        "llm.model",
        "apply.headless",
    ):
        assert f'name="{name}"' in settings, name
    assert "never handles it" in settings


def test_unknown_page_is_a_404_html_and_api_404_is_json(client: TestClient) -> None:
    page = client.get("/nope")
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")
    assert client.get("/api/nope").headers["content-type"].startswith("application/json")
    assert client.get("/static/nope.js").status_code == 404
    assert (
        client.post(
            "/profile", headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]}
        ).status_code
        == 405
    )


def test_static_assets_are_served(client: TestClient) -> None:
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "X-CSRF-Token" in js.text
    css = client.get("/static/app.css")
    assert css.status_code == 200 and "prefers-color-scheme" in css.text
    assert client.get("/static/../security.py").status_code == 404
    assert Path(STATIC_DIR / "app.js").is_file()
