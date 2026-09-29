from __future__ import annotations

from pathlib import Path

from autoapply.models import REQUIRED_PROFILE_FIELDS
from autoapply.tailor.knowledge import extract_resume_text
from autoapply.testing.sample_profile import (
    make_sample_resume_pdf,
    sample_experiences,
    sample_knowledge_base,
    sample_profile,
)


def test_sample_profile_is_complete_and_fictional() -> None:
    profile = sample_profile()
    for name in REQUIRED_PROFILE_FIELDS:
        assert getattr(profile, name) not in ("", None), name
    assert profile.authorized_to_work_us is True and profile.requires_sponsorship is False
    assert profile.email.endswith("@example.test") and "555" in profile.phone


def test_sample_experiences_cover_every_kind_and_are_unique() -> None:
    experiences = sample_experiences()
    kinds = [e.kind for e in experiences]
    assert kinds.count("work") == 2 and kinds.count("project") == 2
    assert kinds.count("leadership") == 1 and kinds.count("education") == 1
    assert len({e.id for e in experiences}) == len(experiences)
    assert sample_knowledge_base().skills


def test_sample_resume_pdf_text_matches_the_background(tmp_path: Path) -> None:
    text = extract_resume_text(make_sample_resume_pdf(tmp_path / "r" / "resume.pdf"))
    assert "Alex Rivera" in text
    for exp in sample_experiences():
        assert exp.title in text
        assert all(b in " ".join(text.split()) for b in exp.bullets)
