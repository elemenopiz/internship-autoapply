"""The candidate facts an application answer is allowed to rest on.

Two sources, never duplicated:
  * config.json ``profile`` — contact details and short screening answers
    (work authorization, sponsorship, EEO choices, GPA, start date, ...).
  * data/profile/candidate.yaml — resume facts (education, experience,
    projects, honors, skills), the user's own motivation statement, and
    prepared answers keyed by question text.

``Candidate.corpus`` and ``Candidate.numbers`` are the allow-lists the drafting
verifier checks generated text against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from config.settings import UserProfile

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Education(BaseModel):
    model_config = ConfigDict(frozen=True)

    school: str
    degree: str = ""
    major: str = ""
    minors: tuple[str, ...] = ()
    gpa: str = ""
    graduation: str = ""
    location: str = ""


class Role(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization: str
    title: str
    location: str = ""
    dates: str = ""
    bullets: tuple[str, ...] = ()


class Project(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    dates: str = ""
    bullets: tuple[str, ...] = ()


class CandidateFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    education: tuple[Education, ...] = ()
    experience: tuple[Role, ...] = ()
    projects: tuple[Project, ...] = ()
    honors: tuple[str, ...] = ()
    skills: dict[str, tuple[str, ...]] = {}
    languages: tuple[str, ...] = ()
    certifications: tuple[str, ...] = ()
    interests: tuple[str, ...] = ()
    # The user's own words on why they want these roles. Never generated:
    # motivation questions are only drafted when this is filled in.
    motivation: str = ""
    # Answers the user wrote for specific questions, keyed by (part of) the
    # question text. Checked before any rule or draft.
    prepared_answers: dict[str, str] = {}


def load_facts(path: str | Path) -> CandidateFacts:
    """Load candidate.yaml, failing with a message that names the problem."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc
    return CandidateFacts.model_validate(raw)


def normalize(text: str) -> str:
    """Comparison key: lowercase words, punctuation dropped, '&' == 'and'."""
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep decimal points only
    return " ".join(re.sub(r"[^a-z0-9%$+.]+", " ", text).split())


def number_values(text: str) -> set[str]:
    """Numeric tokens with separators stripped ('1,000+' -> '1000')."""
    return {tok.replace(",", "").rstrip(".") for tok in _NUMBER.findall(text or "")}


@dataclass(frozen=True)
class Candidate:
    """Contact + screening answers (config) joined with resume facts (yaml)."""

    profile: "UserProfile"
    facts: CandidateFacts

    # --- screening answers --------------------------------------------------

    def answer(self, key: str) -> str:
        """A screening answer from config.json, '' when absent."""
        value = self.profile.screening_answers.get(key, "")
        return "" if value is None else str(value).strip()

    def flag(self, key: str) -> bool | None:
        """A yes/no screening answer: True, False, or None when unset."""
        value = self.answer(key).lower()
        if value in ("yes", "true", "y"):
            return True
        if value in ("no", "false", "n"):
            return False
        return None

    @property
    def education(self) -> Education | None:
        return self.facts.education[0] if self.facts.education else None

    # --- grounding allow-lists ---------------------------------------------

    def fact_lines(self) -> list[str]:
        """Every statement the candidate's record makes, one per line."""
        p, f = self.profile, self.facts
        lines = [
            f"Name: {p.full_name}", f"Email: {p.email}", f"Phone: {p.phone_full}",
            f"Location: {p.location}",
        ]
        if p.linkedin_url:
            lines.append(f"LinkedIn: {p.linkedin_url}")
        for key, value in p.screening_answers.items():
            if value not in (None, ""):
                lines.append(f"{key.replace('_', ' ')}: {value}")
        for ed in f.education:
            lines.append(
                f"Education: {ed.degree}, {ed.major} at {ed.school}"
                f"{' (minors: ' + ', '.join(ed.minors) + ')' if ed.minors else ''}"
                f"{', GPA ' + ed.gpa if ed.gpa else ''}"
                f"{', graduating ' + ed.graduation if ed.graduation else ''}")
        for role in f.experience:
            lines.append(f"Experience: {role.title} at {role.organization}, "
                         f"{role.location} ({role.dates})")
            lines.extend(f"  - {b}" for b in role.bullets)
        for proj in f.projects:
            lines.append(f"Project: {proj.name} ({proj.dates}) {proj.description}".strip())
            lines.extend(f"  - {b}" for b in proj.bullets)
        lines.extend(f"Honor: {h}" for h in f.honors)
        for group, items in f.skills.items():
            lines.append(f"Skills ({group}): {', '.join(items)}")
        if f.languages:
            lines.append(f"Languages: {'; '.join(f.languages)}")
        lines.extend(f"Certification: {c}" for c in f.certifications)
        if f.interests:
            lines.append(f"Interests: {'; '.join(f.interests)}")
        if f.motivation:
            lines.append(f"Motivation (candidate's own words): {f.motivation}")
        return lines

    def corpus(self) -> str:
        """Normalized text of every fact — the evidence pool for drafts."""
        return normalize("\n".join(self.fact_lines()))

    def numbers(self) -> set[str]:
        """Every number the candidate's record contains."""
        return number_values("\n".join(self.fact_lines()))

    def organizations(self) -> set[str]:
        """Organizations the candidate has actually worked with or built."""
        names = {normalize(r.organization) for r in self.facts.experience}
        names |= {normalize(p.name) for p in self.facts.projects}
        names |= {normalize(e.school) for e in self.facts.education}
        return {n for n in names if n}


def load_candidate(profile: "UserProfile", facts_path: str | Path) -> Candidate | None:
    """The Candidate, or None when candidate.yaml doesn't exist yet."""
    facts_path = Path(facts_path)
    if not facts_path.is_file():
        return None
    return Candidate(profile=profile, facts=load_facts(facts_path))
