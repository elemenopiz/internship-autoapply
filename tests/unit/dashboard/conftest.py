"""Fixtures for the dashboard tests. All data is obviously fictional; nothing touches a real network."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths, save_config
from autoapply.contracts import LLMClient
from autoapply.dashboard.app import create_app
from autoapply.dashboard.deps import DashboardRuntime
from autoapply.db import Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    KnowledgeBase,
    Opportunity,
    RunMode,
    RunReport,
)
from autoapply.secrets import MemoryCredentialStore

BASE_URL = "http://127.0.0.1:8765"
FICTIONAL_KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
HOSTILE = "<script>alert('x')</script>\"><img src=x onerror=alert(1)>"


class FakeController:
    """Implements ``contracts.RunController`` without running anything."""

    def __init__(self) -> None:
        self.running = False
        self.accept = True
        self.calls: list[tuple[RunMode | None, str]] = []
        self.stop_requests = 0
        self.next_run_at: str | None = None
        self.report: RunReport | None = None
        self.broken = False

    def run_now(self, mode: RunMode | None = None, *, trigger: str = "manual") -> bool:
        if self.running or not self.accept:
            return False
        self.calls.append((mode, trigger))
        self.running = True
        return True

    def is_running(self) -> bool:
        return self.running

    def request_stop(self) -> None:
        self.stop_requests += 1

    def status(self) -> dict[str, object]:
        if self.broken:
            raise RuntimeError("boom")
        return {
            "running": self.running,
            "next_run_at": self.next_run_at,
            "last_report": self.report.model_dump(mode="json") if self.report else None,
        }

    def last_report(self) -> RunReport | None:
        return self.report


class StubHooks:
    """Records calls of the injected knowledge-base / workbook hooks."""

    def __init__(self) -> None:
        self.kb = KnowledgeBase()
        self.saved: list[KnowledgeBase] = []
        self.proposed = KnowledgeBase(source="resume")
        self.build_error: Exception | None = None
        self.build_calls: list[tuple[Path, LLMClient]] = []
        self.inspect_calls: list[Path] = []
        self.inspect_result: object = {"sheet": "Verified Opportunities", "kept": 3}
        self.inspect_error: Exception | None = None

    def load(self, paths: AppPaths) -> KnowledgeBase:
        return self.kb

    def save(self, paths: AppPaths, kb: KnowledgeBase) -> None:
        self.saved.append(kb)
        self.kb = kb

    def build(self, pdf_path: Path, llm: LLMClient) -> KnowledgeBase:
        self.build_calls.append((pdf_path, llm))
        if self.build_error:
            raise self.build_error
        return self.proposed

    def inspect(self, path: Path) -> object:
        self.inspect_calls.append(path)
        if self.inspect_error:
            raise self.inspect_error
        return self.inspect_result


class _NullLLM:
    def complete_json(self, **kwargs: Any) -> Any:  # pragma: no cover - never called by stubs
        raise NotImplementedError

    def complete_text(self, **kwargs: Any) -> str:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def controller() -> FakeController:
    return FakeController()


@pytest.fixture
def hooks() -> StubHooks:
    return StubHooks()


@pytest.fixture
def repo(paths: AppPaths, fake_clock: FakeClock) -> Iterator[Repo]:
    repository = Repo.open(paths.db_file, fake_clock)
    yield repository
    repository.db.close()


@pytest.fixture
def runtime(
    paths: AppPaths,
    repo: Repo,
    controller: FakeController,
    fake_clock: FakeClock,
    hooks: StubHooks,
) -> DashboardRuntime:
    return DashboardRuntime(
        paths=paths,
        repo=repo,
        controller=controller,
        clock=fake_clock,
        env={},
        store=MemoryCredentialStore(),
        llm_factory=lambda config: _NullLLM(),
        load_kb=hooks.load,
        save_kb=hooks.save,
        build_kb_from_resume=hooks.build,
        inspect_workbook=hooks.inspect,
    )


@pytest.fixture
def app(runtime: DashboardRuntime) -> Any:
    return create_app(runtime)


@pytest.fixture
def bare_client(app: Any) -> Iterator[TestClient]:
    """A client with no CSRF header preset (for security tests)."""
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    """A same-origin browser stand-in: has the session cookie and sends the CSRF header."""
    with TestClient(app, base_url=BASE_URL) as test_client:
        token = test_client.get("/api/csrf").json()["csrf_token"]
        test_client.headers["X-CSRF-Token"] = token
        yield test_client


def csrf_from_html(html: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert match, "page has no csrf meta tag"
    return match.group(1)


def ready_profile() -> dict[str, Any]:
    return {
        "first_name": "Ada",
        "last_name": "Testperson",
        "email": "ada.testperson@example.test",
        "phone": "+1 512 555 0100",
        "address_line1": "1 Example Way",
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
        "country": "United States",
        "school": "Example University",
        "degree": "Bachelor of Science",
        "major": "Information Systems",
        "graduation_date": "2028-05",
        "authorized_to_work_us": True,
        "requires_sponsorship": False,
    }


@pytest.fixture
def make_ready(paths: AppPaths, runtime: DashboardRuntime) -> Any:
    """Make readiness pass for ``full_auto``: profile, resume, key, a source, attestation."""

    def _apply(mode: RunMode = RunMode.FULL_AUTO, *, key: bool = True) -> AppConfig:
        config = AppConfig.model_validate(
            {
                "mode": mode.value,
                "profile": {**ready_profile(), "fallback_resume_path": str(paths.resume_file)},
                "boards": {"greenhouse": ["acme"]},
                "apply": {"attestations_authorized": True},
            }
        )
        save_config(paths, config)
        paths.resume_file.parent.mkdir(parents=True, exist_ok=True)
        paths.resume_file.write_bytes(PDF_BYTES)
        if key:
            runtime.env = {"OPENAI_API_KEY": FICTIONAL_KEY}  # type: ignore[assignment]
        return config

    return _apply


@pytest.fixture
def seed(repo: Repo) -> Any:
    """Insert one scored opportunity and (optionally) an application for it."""

    def _seed(
        company: str = "Acme Robotics",
        title: str = "Product Management Intern",
        *,
        status: ApplicationStatus | None = None,
        mode: RunMode = RunMode.FULL_AUTO,
        **fields: Any,
    ) -> Opportunity:
        op, _ = repo.upsert_opportunity(
            Opportunity(
                company=company,
                title=title,
                url=fields.pop("url", f"https://jobs.acme.example.test/{title.replace(' ', '-')}"),
                **fields,
            )
        )
        if status is not None:
            application = repo.create_application(op.id, mode)
            repo.finish_application(application.id or 0, ApplyResult(status=status))
        return op

    return _seed
