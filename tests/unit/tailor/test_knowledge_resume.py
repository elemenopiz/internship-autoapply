from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from autoapply.contracts import LLMError
from autoapply.models import Experience, KnowledgeBase
from autoapply.tailor.knowledge import (
    MAX_RESUME_CHARS,
    ResumeExtractionError,
    build_kb_from_resume,
    extract_resume_text,
    ground_extraction,
    normalise_resume_text,
    parse_resume_text,
)
from autoapply.testing.sample_profile import sample_experiences

Shape = list[tuple[str, str, str | None, str | None, str | None, str | None, list[str]]]


def make_pdf(path: Path, lines: list[str]) -> Path:
    """A plain PDF whose text is ``lines`` (Helvetica, one line per row, several pages if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    page = canvas.Canvas(str(path), invariant=1)
    y = 780
    for line in lines:
        if y < 50:
            page.showPage()
            y = 780
        page.setFont("Helvetica", 10)
        page.drawString(50, y, line)
        y -= 14
    page.save()
    return path


def shape(kb: KnowledgeBase) -> Shape:
    return [
        (e.kind, e.title, e.organization, e.location, e.start, e.end, e.bullets)
        for e in kb.experiences
    ]


def sample_shape() -> Shape:
    return [
        (e.kind, e.title, e.organization, e.location, e.start, e.end, e.bullets)
        for e in sample_experiences()
    ]


def expected_shape() -> Shape:
    """The sample resume lists Education first, the KB lists it last."""
    ordered = sorted(sample_experiences(), key=lambda e: e.kind != "education")
    return [
        (e.kind, e.title, e.organization, e.location, e.start, e.end, e.bullets) for e in ordered
    ]


# ------------------------------------------------------------------------------------------ extraction


def test_extract_text_of_the_sample_resume(resume_pdf: Path) -> None:
    text = extract_resume_text(resume_pdf)
    assert text.startswith("Alex Rivera")
    assert "\N{BULLET} Built a Tableau dashboard tracking on-time delivery" in text
    assert "\x7f" not in text and "\r" not in text


def test_missing_corrupt_encrypted_and_textless_pdfs(tmp_path: Path) -> None:
    with pytest.raises(ResumeExtractionError, match="not found"):
        extract_resume_text(tmp_path / "missing.pdf")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf at all")
    with pytest.raises(ResumeExtractionError, match="cannot read"):
        extract_resume_text(broken)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    blank = tmp_path / "blank.pdf"
    with blank.open("wb") as handle:
        writer.write(handle)
    assert extract_resume_text(blank) == ""
    writer.encrypt("secret")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(ResumeExtractionError, match="password"):
        extract_resume_text(locked)


def test_normalise_maps_every_bullet_glyph_and_keeps_column_gaps() -> None:
    raw = (
        "\x7f First\n"
        + chr(0xF0B7)
        + " Second\n\N{BLACK SMALL SQUARE} Third\r\n\N{BULLET}Fourth\n"
        + "Title    Jun 2025\tAug 2025\n\n\n\nEnd\x00"
    )
    assert normalise_resume_text(raw).split("\n") == [
        "\N{BULLET} First",
        "\N{BULLET} Second",
        "\N{BULLET} Third",
        "\N{BULLET} Fourth",
        "Title | Jun 2025 | Aug 2025",
        "",
        "End",
    ]


# ------------------------------------------------------------------------------------------ heuristic parser


def test_heuristic_parser_reproduces_the_sample_background(resume_pdf: Path) -> None:
    kb = parse_resume_text(extract_resume_text(resume_pdf))
    assert kb.source == "resume"
    assert shape(kb) == expected_shape()
    projects = [e for e in kb.experiences if e.kind == "project"]
    assert [e.skills for e in projects] == [
        ["Python", "Flask", "SQLite"],
        ["Python", "pandas", "scikit-learn", "Tableau"],
    ]
    assert kb.skills[:4] == ["SQL", "Excel", "Tableau", "Python"] and "Figma" in kb.skills
    assert len({e.id for e in kb.experiences}) == len(kb.experiences)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Just a paragraph of prose without any headings or bullets at all.",
        "Alex Rivera\nAustin, TX",
    ],
)
def test_text_without_recognisable_sections_gives_an_empty_kb(text: str) -> None:
    assert parse_resume_text(text) == KnowledgeBase(source="none")


def test_heuristic_parser_handles_common_layout_variants() -> None:
    text = "\n".join(
        [
            "JANE DOE",
            "EXPERIENCE",
            "Acme Robotics | Austin, TX",
            "Data Analyst Intern  Jun 2025 \N{EN DASH} Aug 2025",
            "\N{BULLET} Cleaned 4 datasets",
            "\N{BULLET} Wrote weekly reports that saved the",
            "team 3 hours",
            "PROJECTS",
            "Budget Bot \N{EM DASH} Python, SQLite  Summer 2024",
            "- Built a chat bot",
            "ACTIVITIES",
            "Treasurer, Chess Club  2023 - Present",
            "\N{BULLET} Kept the books",
            "SKILLS",
            "Languages: Python, SQL; Tools: Excel | Git",
        ]
    )
    kb = parse_resume_text(text)
    work, project, activity = kb.experiences
    assert (work.title, work.organization, work.location) == (
        "Data Analyst Intern",
        "Acme Robotics",
        "Austin, TX",
    )
    assert (work.start, work.end) == ("2025-06", "2025-08")
    assert work.bullets == [
        "Cleaned 4 datasets",
        "Wrote weekly reports that saved the team 3 hours",
    ]
    assert (project.kind, project.title, project.skills) == (
        "project",
        "Budget Bot",
        ["Python", "SQLite"],
    )
    assert (project.start, project.end) == (None, "2024")
    assert (activity.kind, activity.title, activity.organization) == (
        "leadership",
        "Treasurer",
        "Chess Club",
    )
    assert (activity.start, activity.end) == ("2023", "present")
    assert kb.skills == ["Python", "SQL", "Excel", "Git", "SQLite"]


# ------------------------------------------------------------------------------------------ grounding of values


RESUME_TEXT = "\n".join(
    [
        "EXPERIENCE",
        "Data Analyst Intern, Acme Robotics  Jun 2025 - Aug 2025",
        "Austin, TX",
        "\N{BULLET} Built a Tableau dashboard that cut reporting time by 6 hours",
        "\N{BULLET} Wrote C++ tools for the develop-",
        "ment team",
        "SKILLS",
        "Python, C++, SQL",
    ]
)


def extraction(**overrides: Any) -> Any:
    from autoapply.tailor.knowledge import _ExtractedExperience, _ExtractedResume

    fields: dict[str, Any] = {
        "kind": "work",
        "title": "Data Analyst Intern",
        "organization": "Acme Robotics",
        "location": "Austin, TX",
        "start": "2025-06",
        "end": "2025-08",
        "bullets": ["Built a Tableau dashboard that cut reporting time by 6 hours"],
        "skills": ["SQL"],
    }
    fields.update(overrides)
    return _ExtractedResume(experiences=[_ExtractedExperience(**fields)], skills=["Python"])


def test_faithful_extraction_is_kept_verbatim() -> None:
    kb = ground_extraction(extraction(), RESUME_TEXT)
    (exp,) = kb.experiences
    assert (exp.id, exp.title, exp.organization, exp.location) == (
        "acme-robotics-data-analyst-intern",
        "Data Analyst Intern",
        "Acme Robotics",
        "Austin, TX",
    )
    assert (exp.start, exp.end) == ("2025-06", "2025-08")
    assert exp.bullets == ["Built a Tableau dashboard that cut reporting time by 6 hours"]
    assert kb.skills == ["Python", "SQL"] and kb.source == "resume"


@pytest.mark.parametrize(
    ("override", "field", "expected"),
    [
        ({"organization": "Google"}, "organization", None),
        ({"location": "Mountain View, CA"}, "location", None),
        ({"start": "2019-01"}, "start", None),
        ({"end": "2025-09"}, "end", None),
        ({"end": "present"}, "end", None),
        (
            {
                "bullets": [
                    "Increased revenue by 300%",
                    "Built a Tableau dashboard that cut reporting time by 6 hours",
                ]
            },
            "bullets",
            ["Built a Tableau dashboard that cut reporting time by 6 hours"],
        ),
        ({"skills": ["Kubernetes", "SQL"]}, "skills", ["SQL"]),
    ],
)
def test_values_missing_from_the_resume_are_dropped(
    override: dict[str, Any], field: str, expected: Any
) -> None:
    (exp,) = ground_extraction(extraction(**override), RESUME_TEXT).experiences
    assert getattr(exp, field) == expected


def test_an_entry_whose_title_is_not_in_the_resume_is_dropped_entirely() -> None:
    kb = ground_extraction(extraction(title="Chief Executive Officer"), RESUME_TEXT)
    assert kb.experiences == []
    assert kb.skills == ["Python"]  # skills are grounded separately
    assert (
        ground_extraction(extraction(title="CEO", skills=["Rust"]), "no relevant words").source
        == "none"
    )


def test_line_break_hyphenation_case_and_special_characters() -> None:
    (exp,) = ground_extraction(
        extraction(
            bullets=["Wrote C++ tools for the development team"], skills=["c++", "C#", "Java"]
        ),
        RESUME_TEXT,
    ).experiences
    assert exp.bullets == [
        "Wrote C++ tools for the development team"
    ]  # "develop-\nment" was a line break
    assert exp.skills == ["c++"]  # "C#" would only match the bare letter "C"; Java is absent


def test_month_and_year_only_dates_and_duplicates() -> None:
    text = "Intern at Acme  6/2025 - 2026\nIntern at Acme  6/2025 - 2026"
    kb = ground_extraction(
        extraction(
            title="Intern at Acme",
            start="2025-06",
            end="2026",
            organization="",
            location="",
            bullets=[],
            skills=[],
        ),
        text,
    )
    (exp,) = kb.experiences
    assert (exp.start, exp.end) == ("2025-06", "2026")
    twice = extraction(title="Intern at Acme", organization="", location="", bullets=[], skills=[])
    twice.experiences.append(twice.experiences[0].model_copy())
    assert len(ground_extraction(twice, text).experiences) == 1


def test_unknown_kinds_become_other_and_ids_are_unique() -> None:
    from autoapply.tailor.knowledge import _ExtractedExperience, _ExtractedResume

    entries = [
        _ExtractedExperience(
            kind="hobby",
            title="Chess",
            organization="",
            location="",
            start="",
            end="",
            bullets=[],
            skills=[],
        ),
        _ExtractedExperience(
            kind="Internship",
            title="Chess",
            organization="",
            location="",
            start="2024",
            end="",
            bullets=[],
            skills=[],
        ),
    ]
    kb = ground_extraction(_ExtractedResume(experiences=entries, skills=[]), "Chess\nChess 2024")
    assert [e.kind for e in kb.experiences] == ["other", "work"]
    assert [e.id for e in kb.experiences] == ["chess", "chess-2"]


# ------------------------------------------------------------------------------------------ build_kb_from_resume


def faithful_reply(_user: str) -> dict[str, Any]:
    return {
        "experiences": [
            {
                "kind": e.kind,
                "title": e.title,
                "organization": e.organization or "",
                "location": e.location or "",
                "start": e.start or "",
                "end": e.end or "",
                "bullets": e.bullets,
                "skills": e.skills,
            }
            for e in sample_experiences()
        ],
        "skills": ["SQL", "Excel"],
    }


def test_llm_extraction_is_used_and_grounded(
    resume_pdf: Path, make_llm: Callable[..., Any]
) -> None:
    llm = make_llm(kb_from_resume=faithful_reply)
    kb = build_kb_from_resume(resume_pdf, llm)
    assert llm.purposes() == ["kb_from_resume"]
    assert "Alex Rivera" in llm.calls[0].user and "copy" in llm.calls[0].system.lower()
    assert kb.source == "resume"
    assert shape(kb) == sample_shape()
    assert [e.skills for e in kb.experiences] == [e.skills for e in sample_experiences()]


def test_hostile_llm_extraction_cannot_add_anything_the_resume_does_not_say(
    resume_pdf: Path, make_llm: Callable[..., Any]
) -> None:
    def hostile(user: str) -> dict[str, Any]:
        reply = faithful_reply(user)
        reply["experiences"].append(
            {
                "kind": "work",
                "title": "Software Engineer Intern",
                "organization": "Google",
                "location": "Mountain View, CA",
                "start": "2019-01",
                "end": "2020-01",
                "bullets": ["Increased revenue by 300% using Kubernetes"],
                "skills": ["Kubernetes"],
            }
        )
        first = reply["experiences"][0]
        first.update(organization="Google", start="2015-01", end="2016-02")
        first["bullets"] = [
            *first["bullets"],
            "Invented a bullet the resume never had",
            "Presented findings to a panel of 800 managers",
        ]
        first["skills"] = [*first["skills"], "Kubernetes"]
        reply["skills"] = ["SQL", "Rust", "Kubernetes"]
        return reply

    kb = build_kb_from_resume(resume_pdf, make_llm(kb_from_resume=hostile))
    blob = kb.corpus()
    for forbidden in (
        "google",
        "kubernetes",
        "300%",
        "mountain view",
        "2019",
        "2015",
        "invented",
        "800 managers",
        "rust",
    ):
        assert forbidden not in blob, forbidden
    first = next(e for e in kb.experiences if e.id.endswith("business-analyst-intern"))
    # invented values are dropped (never "corrected" from elsewhere); real bullets survive
    assert (first.organization, first.start, first.end) == (None, None, None)
    assert (
        first.title == "Business Analyst Intern"
        and first.bullets == sample_experiences()[0].bullets
    )
    assert len(kb.experiences) == 6


@pytest.mark.parametrize("llm_kind", ["down", "none", "empty_reply", "garbage", "crash"])
def test_every_llm_failure_falls_back_to_the_heuristic_parser(
    resume_pdf: Path, make_llm: Callable[..., Any], llm_kind: str
) -> None:
    llm: Any
    if llm_kind == "down":
        llm = make_llm(kb_from_resume=LLMError("no key"))
    elif llm_kind == "none":
        llm = None
    elif llm_kind == "empty_reply":
        llm = make_llm(kb_from_resume={"experiences": [], "skills": []})
    elif llm_kind == "garbage":
        llm = make_llm(kb_from_resume={"experiences": "not a list"})
    else:
        llm = make_llm(kb_from_resume=RuntimeError("adapter bug"))
    kb = build_kb_from_resume(resume_pdf, llm)
    assert shape(kb) == expected_shape()


def test_reply_with_only_ungrounded_entries_also_falls_back(
    resume_pdf: Path, make_llm: Callable[..., Any]
) -> None:
    reply = {
        "experiences": [{"kind": "work", "title": "Astronaut", "organization": "NASA"}],
        "skills": ["Piloting"],
    }
    kb = build_kb_from_resume(resume_pdf, make_llm(kb_from_resume=reply))
    assert shape(kb) == expected_shape()


def test_scanned_pdf_without_text_yields_an_empty_kb_without_calling_the_llm(
    tmp_path: Path, make_llm: Callable[..., Any]
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    scan = tmp_path / "scan.pdf"
    with scan.open("wb") as handle:
        writer.write(handle)
    llm = make_llm(kb_from_resume=faithful_reply)
    assert build_kb_from_resume(scan, llm) == KnowledgeBase(source="none")
    assert llm.calls == []


def test_unreadable_pdf_raises_a_clear_error(tmp_path: Path) -> None:
    broken = tmp_path / "x.pdf"
    broken.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(ResumeExtractionError):
        build_kb_from_resume(broken, None)


def test_very_long_resumes_are_truncated_for_the_prompt_only(
    tmp_path: Path, make_llm: Callable[..., Any]
) -> None:
    lines = [
        "EXPERIENCE",
        "Analyst, Acme  2024 - 2025",
        *[
            f"\N{BULLET} Did thing number {i} with care and attention to the detail"
            for i in range(900)
        ],
    ]
    pdf = make_pdf(tmp_path / "long.pdf", lines)
    llm = make_llm(kb_from_resume={"experiences": [], "skills": []})
    kb = build_kb_from_resume(pdf, llm)
    assert len(llm.calls[0].user) <= MAX_RESUME_CHARS
    assert (
        kb.experiences and kb.experiences[0].title == "Analyst"
    )  # the heuristic still sees everything


def test_kb_from_resume_result_is_saveable_and_tailorable(resume_pdf: Path, app_paths: Any) -> None:
    from autoapply.tailor.knowledge import load_kb, save_kb

    kb = build_kb_from_resume(resume_pdf, None)
    save_kb(app_paths, kb)
    assert load_kb(app_paths) == kb
    assert isinstance(kb.experiences[0], Experience)
