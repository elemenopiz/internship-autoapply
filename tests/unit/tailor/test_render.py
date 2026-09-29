from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from autoapply.tailor.render import (
    BASE_FONT_PT,
    MIN_FONT_PT,
    LetterDoc,
    RenderError,
    ResumeDoc,
    ResumeEntry,
    ResumeSection,
    count_unrenderable,
    markup,
    pdf_safe,
    render_cover_letter,
    render_cover_letter_bytes,
    render_resume,
    render_resume_bytes,
)


def raw_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return re.sub(r"\s+", " ", "\n".join(p.extract_text() for p in reader.pages)).strip()


def pages_of(data: bytes) -> int:
    return len(PdfReader(BytesIO(data)).pages)


def entry(i: int, bullets: int = 4, words: int = 22) -> ResumeEntry:
    body = " ".join(f"word{i}x{j}" for j in range(words))
    return ResumeEntry(
        heading=f"Role {i}",
        dates="Jun 2025 \N{EN DASH} Aug 2025",
        subheading=f"Company {i}, Austin, TX",
        bullets=tuple(f"Bullet {i}-{b} {body}" for b in range(bullets)),
    )


def small_doc() -> ResumeDoc:
    return ResumeDoc(
        name="Alex Rivera",
        contact=("alex.rivera@example.test", "(512) 555-0142", "Austin, TX"),
        sections=(
            ResumeSection(
                "Education",
                (
                    ResumeEntry(
                        "The University of Texas at Austin", "Expected May 2028", "B.S. in MIS"
                    ),
                ),
                protected=True,
            ),
            ResumeSection("Experience", (entry(1, 3, 8), entry(2, 2, 8))),
        ),
        skills=("SQL", "Excel"),
    )


# ------------------------------------------------------------------------------------------ text safety


@pytest.mark.parametrize(
    "text",
    [
        "Plain ASCII, with punctuation: (ok) 100% & more!",
        "Accents: café naïve Ångström Señor Øystein",
        "Dashes \N{EN DASH} and \N{EM DASH}",
        "Quotes \N{LEFT SINGLE QUOTATION MARK}a\N{RIGHT SINGLE QUOTATION MARK} \N{LEFT DOUBLE QUOTATION MARK}b\N{RIGHT DOUBLE QUOTATION MARK}",
        "Euro \N{EURO SIGN}5 and pound £5, degree 30°, trademark\N{TRADE MARK SIGN}",
        "O'Brien and D'Angelo",
    ],
)
def test_pdf_safe_keeps_everything_a_standard_font_can_draw(text: str) -> None:
    assert pdf_safe(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a\N{ZERO WIDTH SPACE}b", "ab"),
        ("a\N{NO-BREAK SPACE}b", "a b"),
        ("a\N{SOFT HYPHEN}b", "ab"),
        ("a  \t\n b", "a b"),
        ("x \N{RIGHTWARDS ARROW} y", "x -> y"),
        ("x \N{GREATER-THAN OR EQUAL TO} 2", "x >= 2"),
        ("non\N{NON-BREAKING HYPHEN}breaking", "non-breaking"),
        ("Łódź", "Lódz"),
        ("\N{LATIN SMALL LETTER L WITH STROKE}", "l"),
        ("\N{CJK UNIFIED IDEOGRAPH-4E2D}", "?"),
        ("smile \U0001f600 end", "smile ? end"),
        ("ctrl\x00\x07char", "ctrlchar"),
        ("e\N{COMBINING ACUTE ACCENT}", "é"),
        ("\N{COMBINING ACUTE ACCENT}alone", "alone"),
        ("\N{BLACK CIRCLE} item", "\N{BULLET} item"),
        ("  padded  ", "padded"),
    ],
)
def test_pdf_safe_transliterates_or_drops_the_rest(text: str, expected: str) -> None:
    assert pdf_safe(text) == expected


def test_pdf_safe_is_idempotent_and_reports_lossy_characters() -> None:
    nasty = "Ałé \N{CJK UNIFIED IDEOGRAPH-4E2D} \N{RIGHTWARDS ARROW} \U0001f600 \N{BULLET}"
    once = pdf_safe(nasty)
    assert pdf_safe(once) == once
    assert count_unrenderable(nasty) == 2  # the ideograph and the emoji
    assert count_unrenderable("plain é?") == 0  # a literal "?" is not a loss


def test_markup_escapes_xml_and_draws_bullets_with_the_symbol_font() -> None:
    assert markup("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert markup("x \N{BULLET} y") == 'x <font name="Symbol">\N{BULLET}</font> y'


# ------------------------------------------------------------------------------------------ resume


def test_resume_is_one_page_selectable_text_that_round_trips_through_pypdf() -> None:
    data, info = render_resume_bytes(small_doc())
    text = raw_text(data)
    assert pages_of(data) == 1 and info.pages == 1
    assert not info.trimmed and info.font_pt == BASE_FONT_PT
    for expected in (
        "Alex Rivera",
        "alex.rivera@example.test | (512) 555-0142 | Austin, TX",
        "EDUCATION",
        "The University of Texas at Austin",
        "Expected May 2028",
        "EXPERIENCE",
        "Role 1",
        "Jun 2025 \N{EN DASH} Aug 2025",
        "Company 1, Austin, TX",
        "Bullet 1-0 word1x0 word1x1",
        "SKILLS",
        "SQL, Excel",
    ):
        assert expected in text, expected
    assert data.startswith(b"%PDF-")


def test_bullets_extract_as_real_bullet_characters_not_control_codes() -> None:
    data, _ = render_resume_bytes(small_doc())
    text = PdfReader(BytesIO(data)).pages[0].extract_text()
    assert "\N{BULLET} Bullet 1-0" in text
    assert "\x7f" not in text


def test_unicode_and_apostrophes_survive_the_round_trip() -> None:
    doc = ResumeDoc(
        name="Zoë O'Brien-Núñez",
        contact=("zoe@example.test",),
        sections=(
            ResumeSection(
                "Experience",
                (
                    ResumeEntry(
                        "Café Manager \N{EN DASH} Señor Tacos",
                        "2024 \N{EN DASH} 2025",
                        "Universität München, München",
                        bullets=(
                            "Grew sales by 30% \N{EM DASH} “best team” award; l'équipe & résumé <b>",
                            "Managed \N{BULLET} inventory \N{BULLET} orders",
                        ),
                    ),
                ),
            ),
        ),
    )
    data, _ = render_resume_bytes(doc)
    text = raw_text(data)
    assert "Zoë O'Brien-Núñez" in text
    assert "Café Manager \N{EN DASH} Señor Tacos" in text
    assert "Universität München" in text
    assert "Grew sales by 30% \N{EM DASH} “best team” award; l'équipe & résumé <b>" in text
    assert "Managed \N{BULLET} inventory \N{BULLET} orders" in text


def test_unsupported_characters_become_question_marks_never_boxes() -> None:
    doc = ResumeDoc(
        name="Alex \N{CJK UNIFIED IDEOGRAPH-4E2D} Rivera",
        sections=(ResumeSection("Experience", (ResumeEntry("Role \U0001f600", bullets=("ok",)),)),),
    )
    text = raw_text(render_resume_bytes(doc)[0])
    assert "Alex ? Rivera" in text and "Role ?" in text
    assert "\N{BLACK SQUARE}" not in text


def test_output_is_byte_for_byte_deterministic() -> None:
    first, _ = render_resume_bytes(small_doc())
    second, _ = render_resume_bytes(small_doc())
    assert first == second


def test_pdf_metadata_names_the_applicant_only() -> None:
    data, _ = render_resume_bytes(small_doc())
    meta = PdfReader(BytesIO(data)).metadata
    assert meta is not None
    assert meta.title == "Alex Rivera - Resume" and meta.author == "Alex Rivera"


def test_overflow_drops_low_priority_bullets_before_shrinking_the_font() -> None:
    doc = ResumeDoc(
        name="Alex Rivera",
        sections=(
            ResumeSection("Experience", tuple(entry(i, bullets=5, words=30) for i in range(1, 6))),
        ),
    )
    data, info = render_resume_bytes(doc)
    text = raw_text(data)
    assert pages_of(data) == 1
    assert info.dropped_bullets > 0
    # highest-priority bullets survive, lowest-priority ones are the first to go
    for i in range(1, 6):
        assert f"Bullet {i}-0 " in text
    assert "Bullet 1-4 " not in text


def test_font_shrinks_only_after_the_droppable_bullets_are_gone() -> None:
    doc = ResumeDoc(
        name="Alex Rivera",
        sections=(
            ResumeSection("Experience", tuple(entry(i, bullets=6, words=34) for i in range(1, 9))),
        ),
    )
    data, info = render_resume_bytes(doc)
    droppable_at_base = sum(max(0, 6 - 2) for _ in range(8))
    assert pages_of(data) == 1
    assert MIN_FONT_PT <= info.font_pt < BASE_FONT_PT
    assert (
        info.dropped_bullets >= droppable_at_base
    )  # phase 1 (keep two per entry) was exhausted first


def test_huge_knowledge_base_still_fits_one_page_at_or_above_the_font_floor() -> None:
    sections = (
        ResumeSection(
            "Education",
            (ResumeEntry("State University", "May 2028", "B.S.", bullets=("Honors",)),),
            True,
        ),
        ResumeSection("Experience", tuple(entry(i, bullets=8, words=40) for i in range(1, 16))),
        ResumeSection("Projects", tuple(entry(100 + i, bullets=6, words=40) for i in range(1, 11))),
    )
    doc = ResumeDoc(
        name="Alex Rivera", sections=sections, skills=tuple(f"Skill{i}" for i in range(30))
    )
    data, info = render_resume_bytes(doc)
    text = raw_text(data)
    assert pages_of(data) == 1
    assert info.font_pt >= MIN_FONT_PT
    assert info.dropped_bullets > 0
    assert "State University" in text and "Honors" in text  # protected education is never dropped
    assert "Role 1" in text  # the first (highest-priority) entry survives


def test_whole_entries_are_dropped_last_and_from_the_end() -> None:
    doc = ResumeDoc(
        name="Alex Rivera",
        sections=(
            ResumeSection("Education", (ResumeEntry("State University", "2028"),), protected=True),
            ResumeSection("Experience", tuple(entry(i, bullets=1, words=25) for i in range(1, 60))),
        ),
    )
    data, info = render_resume_bytes(doc)
    text = raw_text(data)
    assert pages_of(data) == 1 and info.dropped_entries > 0
    assert "Role 1 " in text and "Role 59 " not in text
    assert "State University" in text


def test_resume_that_cannot_fit_even_when_minimal_raises() -> None:
    education = tuple(
        ResumeEntry(
            f"School {i}",
            "2028",
            "Degree",
            bullets=tuple(f"Detail {i}-{j} " + "x " * 60 for j in range(6)),
        )
        for i in range(30)
    )
    doc = ResumeDoc(name="Alex Rivera", sections=(ResumeSection("Education", education, True),))
    with pytest.raises(RenderError):
        render_resume_bytes(doc)


def test_resume_without_a_name_is_refused() -> None:
    with pytest.raises(RenderError):
        render_resume_bytes(ResumeDoc(name="  "))


def test_text_stays_inside_the_page_margins() -> None:
    data, _ = render_resume_bytes(
        ResumeDoc(
            name="Alex Rivera",
            sections=(
                ResumeSection(
                    "Experience", tuple(entry(i, bullets=4, words=30) for i in range(1, 6))
                ),
            ),
        )
    )
    page = PdfReader(BytesIO(data)).pages[0]
    boxes: list[tuple[float, float, str]] = []

    def visit(text: str, cm: list[float], tm: list[float], font_dict: object, size: float) -> None:
        if text.strip():
            boxes.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5], text))

    page.extract_text(visitor_text=visit)
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    assert boxes
    for x, y, text in boxes:
        assert 40 <= x <= width - 40, (x, text)
        assert 30 <= y <= height - 30, (y, text)


def test_render_resume_writes_atomically_to_hostile_paths(tmp_path: Path) -> None:
    target = tmp_path / "docs ünï cödé" / "opp 1" / "resume.pdf"
    info = render_resume(small_doc(), target)
    assert info.path == target and target.read_bytes().startswith(b"%PDF-")
    assert [p.name for p in target.parent.iterdir()] == ["resume.pdf"]  # no temp files left behind
    render_resume(small_doc(), target)  # overwriting an existing file works
    assert pages_of(target.read_bytes()) == 1


# ------------------------------------------------------------------------------------------ cover letter


def letter(paragraphs: int = 3, words: int = 60, **kwargs: str) -> LetterDoc:
    body = tuple(" ".join(f"para{p}w{w}" for w in range(words)) + "." for p in range(paragraphs))
    return LetterDoc(
        sender_name="Alex Rivera",
        contact=("alex.rivera@example.test", "(512) 555-0142"),
        recipient_lines=("Hiring Manager", "Acme Robotics"),
        subject="Re: Product Management Intern (Summer 2027)",
        paragraphs=body,
        signature="Alex Rivera",
        **kwargs,
    )


def test_cover_letter_renders_all_parts_on_one_page() -> None:
    data, info = render_cover_letter_bytes(letter())
    text = raw_text(data)
    assert pages_of(data) == 1 and info.font_pt == 11.0
    for expected in (
        "Alex Rivera",
        "Hiring Manager",
        "Acme Robotics",
        "Re: Product Management Intern (Summer 2027)",
        "Dear Hiring Manager,",
        "para0w0 para0w1",
        "Sincerely,",
    ):
        assert expected in text, expected


def test_long_cover_letter_shrinks_then_drops_trailing_paragraphs() -> None:
    doc = letter(paragraphs=8, words=90)
    data, info = render_cover_letter_bytes(doc)
    text = raw_text(data)
    assert pages_of(data) == 1
    assert info.font_pt < 11.0 or info.dropped_bullets > 0
    assert "para0w0" in text and "para7w0" not in text
    assert "Sincerely," in text


def test_cover_letter_date_line_and_plain_text() -> None:
    doc = letter(date_line="September 29, 2026")
    assert "September 29, 2026" in raw_text(render_cover_letter_bytes(doc)[0])
    plain = doc.plain_text()
    assert plain.startswith("Dear Hiring Manager,\n\npara0w0")
    assert plain.endswith("Sincerely,\nAlex Rivera")


def test_cover_letter_needs_a_sender_and_a_body(tmp_path: Path) -> None:
    with pytest.raises(RenderError):
        render_cover_letter_bytes(LetterDoc(sender_name="", paragraphs=("x",)))
    with pytest.raises(RenderError):
        render_cover_letter_bytes(LetterDoc(sender_name="Alex", paragraphs=()))
    info = render_cover_letter(letter(), tmp_path / "cover letter.pdf")
    assert info.path is not None and info.path.is_file()


def test_no_text_is_lost_to_normalisation_surprises() -> None:
    doc = letter()
    text = unicodedata.normalize("NFC", raw_text(render_cover_letter_bytes(doc)[0]))
    assert "para2w59." in text
