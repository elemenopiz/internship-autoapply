"""Fixtures for the pipeline tests: a fully fake, fictional world (ingest, scorer, tailor, runner, clock)."""

from __future__ import annotations

import itertools
import random
import threading
from collections.abc import Callable, Iterator, Sequence
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
    Reason,
    RunMode,
    RunReport,
    ScoreResult,
    SearchProfile,
    TailoredDocs,
)
from autoapply.pipeline import PipelineDeps, run_once

KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"
S = ApplicationStatus
NOON_UTC = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)  # 12:00 in Chicago


def filled_profile() -> Profile:
    return Profile(
        first_name="Alex",
        last_name="Rivera",
        email="alex.rivera@example.test",
        phone="+1 (512) 555-0142",
        address_line1="1234 Example Street",
        city="Austin",
        state="TX",
        postal_code="78701",
        country="United States",
        school="The University of Texas at Austin",
        degree="B.B.A.",
        major="Management Information Systems",
        graduation_date="2028-05",
        authorized_to_work_us=True,
        requires_sponsorship=False,
    )


class FakeRunner:
    """ApplicationRunner double. ``script`` maps opportunity id/company -> ApplyResult | Exception | callable."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.calls: list[tuple[Opportunity, TailoredDocs, bool]] = []
        self.script: dict[str, Any] = {}
        self.default: Any = ApplyResult(status=S.SUBMITTED, message="ok")
        self.before_call: Callable[[Opportunity], None] | None = None
        self.close_calls = 0

    def apply_to(
        self, opportunity: Opportunity, docs: TailoredDocs, *, dry_run: bool
    ) -> ApplyResult:
        self.calls.append((opportunity, docs, dry_run))
        # The application row must exist (APPLYING) before the runner is called.
        latest = self.world.repo.latest_application(opportunity.id)
        assert latest is not None and latest.status is S.APPLYING
        if self.before_call:
            self.before_call(opportunity)
        outcome = self.script.get(
            opportunity.id, self.script.get(opportunity.company, self.default)
        )
        if callable(outcome):
            outcome = outcome(opportunity, dry_run)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[no-any-return]

    def close(self) -> None:
        self.close_calls += 1

    @property
    def names(self) -> list[str]:
        return [op.company for op, _, _ in self.calls]


class World:
    """Everything a run needs. ``ops`` is what the fake ingest returns; ``scores`` overrides the default 70."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.paths = AppPaths(root=tmp_path / "data dir ü")
        self.paths.ensure()
        self.clock = FakeClock(NOON_UTC)
        self.repo = Repo(Database(self.paths.db_file), self.clock)
        self.config = AppConfig()
        self.config.profile = filled_profile()
        workbook = tmp_path / "Verified Opportunities.xlsx"
        workbook.write_bytes(b"PK\x03\x04 fictional workbook")
        self.config.workbook.path = str(workbook)
        self.config.apply.attestations_authorized = True
        self.paths.resume_file.write_bytes(b"%PDF-1.4\n% fictional resume\n")
        self.env = {"OPENAI_API_KEY": KEY}
        self.ops: list[Opportunity] = []
        self.ingest_errors: list[str] = []
        self.scores: dict[str, float] = {}
        self.sleeps: list[float] = []
        self.runner = FakeRunner(self)
        self.runner_builds = 0
        self.tailor_calls: list[tuple[Any, ...]] = []
        self.tailor_mode = "tailored"
        self.tailor_error: Exception | None = None
        self.ingest_hook: Callable[[], None] | None = None
        self.ingest_calls = 0
        self.config_loads = 0
        self._counter = itertools.count(1)
        self.stop_flag = threading.Event()

    # -- building blocks -------------------------------------------------------------------------
    def make_op(self, company: str | None = None, **fields: Any) -> Opportunity:
        n = next(self._counter)
        base: dict[str, Any] = {
            "company": company or f"Company {n:02d}",
            "title": f"Product Management Intern {n}",
            "url": f"https://jobs.example.test/postings/{n}",
            "location": "Austin, TX",
            "term": "Summer 2027",
        }
        base.update(fields)
        return Opportunity(**base)

    def add_ops(self, count: int, **fields: Any) -> list[Opportunity]:
        made = [self.make_op(**fields) for _ in range(count)]
        self.ops.extend(made)
        return made

    def seed(self, count: int = 1, **fields: Any) -> list[Opportunity]:
        """Add ops AND store+score them in the DB now (as if an earlier run found them)."""
        made = self.add_ops(count, **fields)
        for op in made:
            self.repo.upsert_opportunity(op)
            self.repo.set_score(op.id, self._score_of(op, self.config.search))
        return made

    def _score_of(self, op: Opportunity, search: SearchProfile) -> ScoreResult:
        value = self.scores.get(op.id, self.scores.get(op.company, 70.0))
        return ScoreResult(score=value, passed=value >= search.min_score)

    # -- injected callables ----------------------------------------------------------------------
    def load_config(self) -> AppConfig:
        self.config_loads += 1
        return self.config

    def ingest(self, ctx: Any) -> Any:
        self.ingest_calls += 1
        if self.ingest_hook:
            self.ingest_hook()
        return SimpleNamespace(opportunities=list(self.ops), errors=list(self.ingest_errors))

    def score(
        self, ops: Sequence[Opportunity], search: SearchProfile, profile: Profile
    ) -> list[Opportunity]:
        return [op.model_copy(update={"score": self._score_of(op, search)}) for op in ops]

    def tailor(
        self,
        op: Opportunity,
        kb: KnowledgeBase,
        profile: Profile,
        paths: AppPaths,
        llm: Any,
        resume: Path | None,
    ) -> TailoredDocs:
        self.tailor_calls.append((op, kb, profile, paths, llm, resume))
        if self.tailor_error:
            raise self.tailor_error
        folder = paths.documents_dir / op.id
        return TailoredDocs(
            mode=self.tailor_mode,  # type: ignore[arg-type]
            resume_pdf=folder / "resume.pdf",
            cover_letter_pdf=folder / "cover_letter.pdf"
            if self.tailor_mode == "tailored"
            else None,
        )

    def build_runner(self, config: AppConfig) -> FakeRunner:
        self.runner_builds += 1
        return self.runner

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    # -- deps / running --------------------------------------------------------------------------
    def deps(self, repo: Repo | None = None, **overrides: Any) -> PipelineDeps:
        fields: dict[str, Any] = {
            "paths": self.paths,
            "repo": repo or self.repo,
            "clock": self.clock,
            "load_config": self.load_config,
            "runner_factory": self.build_runner,
            "llm_factory": None,
            "ingest": self.ingest,
            "score": self.score,
            "tailor": self.tailor,
            "load_kb": lambda paths: KnowledgeBase(),
            "env": self.env,
            "sleep": self.sleep,
            "rng": random.Random(7),
            "stop_flag": self.stop_flag,
        }
        fields.update(overrides)
        return PipelineDeps(**fields)

    def run(
        self,
        *,
        trigger: str = "test",
        mode: RunMode | None = None,
        limit: int | None = None,
        **overrides: Any,
    ) -> RunReport:
        return run_once(self.deps(**overrides), trigger=trigger, mode=mode, limit=limit)

    def submit_history(self, count: int, when: datetime | None = None) -> None:
        """Record ``count`` real submissions (on other jobs) at ``when``."""
        self.clock.set(when or NOON_UTC)
        for op in self.make_history_ops(count):
            app = self.repo.create_application(op.id, RunMode.FULL_AUTO)
            self.repo.finish_application(app.id, ApplyResult(status=S.SUBMITTED))

    def make_history_ops(self, count: int) -> list[Opportunity]:
        stored = []
        for _ in range(count):
            op = self.make_op()
            stored.append(self.repo.upsert_opportunity(op)[0])
        return stored

    def attempt(
        self,
        op: Opportunity,
        status: ApplicationStatus,
        reason: Reason | None = None,
        mode: RunMode = RunMode.FULL_AUTO,
        when: datetime | None = None,
    ) -> None:
        if when is not None:
            self.clock.set(when)
        app = self.repo.create_application(op.id, mode)
        self.repo.finish_application(app.id, ApplyResult(status=status, reason=reason))


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path)
    yield w
    w.repo.db.close()
