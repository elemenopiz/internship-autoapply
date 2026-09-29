"""Fixtures shared by the tailoring tests: sample data, a scripted LLM double, PDF text helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from autoapply.config import AppPaths
from autoapply.contracts import LLMError
from autoapply.models import KnowledgeBase, Opportunity, Profile
from autoapply.tailor.knowledge import extract_resume_text
from autoapply.testing.sample_profile import (
    make_sample_resume_pdf,
    sample_knowledge_base,
    sample_profile,
)


@dataclass
class Call:
    purpose: str
    system: str
    user: str
    schema: type


@dataclass
class ScriptedLLM:
    """``LLMClient`` double. ``replies`` maps a purpose to a dict / model / exception / callable(user)."""

    replies: dict[str, Any] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type,
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> Any:
        self.calls.append(Call(purpose, system, user, schema))
        reply = self.replies.get(purpose)
        if reply is None:
            raise LLMError(f"no scripted reply for {purpose!r}")
        if callable(reply):
            reply = reply(user)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, dict):
            return schema.model_validate(reply)
        return reply

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        raise LLMError("text completions are not scripted")

    def purposes(self) -> list[str]:
        return [c.purpose for c in self.calls]


@pytest.fixture
def make_llm() -> Callable[..., ScriptedLLM]:
    def factory(**replies: Any) -> ScriptedLLM:
        return ScriptedLLM(replies=dict(replies))

    return factory


@pytest.fixture
def failing_llm() -> ScriptedLLM:
    """An LLM that is down: every call raises ``LLMError``."""
    return ScriptedLLM(
        replies={
            "tailor_resume": LLMError("network down"),
            "cover_letter": LLMError("network down"),
            "kb_from_resume": LLMError("network down"),
        }
    )


@pytest.fixture
def profile() -> Profile:
    return sample_profile()


@pytest.fixture
def kb() -> KnowledgeBase:
    return sample_knowledge_base()


@pytest.fixture
def opportunity() -> Opportunity:
    return Opportunity(
        company="Acme Robotics",
        title="Product Management Intern",
        location="Austin, TX",
        term="Summer 2027",
        url="https://jobs.example.test/acme/pm-intern",
        description=(
            "Work with product and engineering teams to build dashboards and analyze data with SQL and "
            "Tableau. Collaborate with stakeholders, run workshops and present recommendations. "
            "Experience with Kubernetes is a plus."
        ),
    )


@pytest.fixture
def resume_pdf(tmp_path: Path) -> Path:
    """The user's own resume (the fallback document)."""
    return make_sample_resume_pdf(tmp_path / "uploads" / "My Résumé (final).pdf")


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(root=tmp_path / "data dir ünï")
    paths.ensure()
    return paths


def normalise(text: str) -> str:
    """NFC + single spaces: extracted PDF text wraps lines, so comparisons ignore whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


@pytest.fixture
def pdf_text() -> Callable[[Path], str]:
    def read(path: Path) -> str:
        return normalise(extract_resume_text(path))

    return read


@pytest.fixture
def pdf_pages() -> Callable[[Path | bytes], int]:
    def count(source: Path | bytes) -> int:
        stream = BytesIO(source) if isinstance(source, bytes) else str(source)
        return len(PdfReader(stream).pages)

    return count
