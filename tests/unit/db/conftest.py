"""Fixtures for the data-layer tests. All data is obviously fictional."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from autoapply.clock import FakeClock
from autoapply.db import Database, Repo
from autoapply.models import ApplicationStatus, ApplyResult, Opportunity


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "autoapply.db"


@pytest.fixture
def db(db_path: Path) -> Iterator[Database]:
    database = Database(db_path)
    yield database
    database.close()


@pytest.fixture
def repo(db: Database, fake_clock: FakeClock) -> Repo:
    """A migrated Repo whose clock is the shared FakeClock (2026-09-29 15:00 UTC)."""
    return Repo(db, fake_clock)


@pytest.fixture
def make_op() -> Callable[..., Opportunity]:
    """Factory for distinct opportunities (own URL => own id); keyword overrides win."""
    counter = itertools.count(1)

    def _make(**overrides: Any) -> Opportunity:
        n = next(counter)
        fields: dict[str, Any] = {
            "company": "Acme Robotics",
            "title": f"Product Management Intern {n}",
            "url": f"https://jobs.acme.example.test/postings/{n}",
            "location": "Austin, TX",
            "term": "Summer 2027",
            "description": "Work with product and engineering.",
        }
        fields.update(overrides)
        return Opportunity(**fields)

    return _make


@pytest.fixture
def make_result() -> Callable[..., ApplyResult]:
    def _make(
        status: ApplicationStatus = ApplicationStatus.SUBMITTED, **fields: Any
    ) -> ApplyResult:
        return ApplyResult(status=status, **fields)

    return _make
