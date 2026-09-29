"""Fixtures for the scheduler tests: fake clock/repo/config plus a deps factory backed by fake collaborators."""

from __future__ import annotations

import random
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths
from autoapply.db import Database, Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    KnowledgeBase,
    Opportunity,
    Profile,
    RunReport,
    ScoreResult,
    TailoredDocs,
)
from autoapply.pipeline import PipelineDeps

KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"


class Env:
    """A tiny world: one fake job, fake runner, config the tests can mutate live."""

    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths(root=tmp_path / "data")
        self.paths.ensure()
        self.clock = FakeClock(datetime(2026, 9, 29, 14, 40, tzinfo=UTC))  # 09:40 in Chicago
        self.repo = Repo(Database(self.paths.db_file), self.clock)
        self.config = AppConfig()
        self.config.profile = Profile(
            first_name="Alex", last_name="Rivera", email="alex@example.test", phone="5125550142",
            address_line1="1 Example St", city="Austin", state="TX", postal_code="78701",
            degree="B.B.A.", major="MIS", graduation_date="2028-05",
            authorized_to_work_us=True, requires_sponsorship=False,
        )  # fmt: skip
        workbook = tmp_path / "book.xlsx"
        workbook.write_bytes(b"PK")
        self.config.workbook.path = str(workbook)
        self.config.apply.attestations_authorized = True
        self.config.schedule.jitter_minutes = 0
        self.paths.resume_file.write_bytes(b"%PDF-1.4")
        self.job = Opportunity(
            company="Acme", title="Product Intern", url="https://x.example.test/1"
        )
        self.submissions = 0
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def load_config(self) -> AppConfig:
        return self.config

    def apply_to(self, op: Opportunity, docs: TailoredDocs, *, dry_run: bool) -> ApplyResult:
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        self.submissions += 1
        return ApplyResult(status=ApplicationStatus.SUBMITTED)

    def close(self) -> None:
        pass

    def deps(self) -> PipelineDeps:
        return PipelineDeps(
            paths=self.paths,
            repo=self.repo,
            clock=self.clock,
            load_config=self.load_config,
            runner_factory=lambda cfg: self,  # type: ignore[arg-type,return-value]
            ingest=lambda ctx: SimpleNamespace(opportunities=[self.job], errors=[]),
            score=lambda ops, s, p: [o.model_copy(update={"score": ScoreResult(score=80, passed=True)}) for o in ops],
            tailor=lambda op, kb, p, paths, llm, r: TailoredDocs(mode="fallback_uploaded_resume", resume_pdf=self.paths.resume_file),
            load_kb=lambda paths: KnowledgeBase(),
            env={"OPENAI_API_KEY": KEY},
            sleep=lambda s: None,
            rng=random.Random(1),
        )  # fmt: skip


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.repo.db.close()


class FakeManager:
    """Records ``run_now`` calls; ``accept`` decides whether it reports a started run."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.accept = True

    def run_now(self, mode: Any = None, *, trigger: str = "manual") -> bool:
        self.calls.append((mode, trigger))
        return self.accept

    def is_running(self) -> bool:
        return False

    def request_stop(self) -> None: ...

    def status(self) -> dict[str, object]:
        return {}

    def last_report(self) -> RunReport | None:
        return None


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()
