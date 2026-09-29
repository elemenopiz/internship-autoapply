"""PDF rendering for tailored documents (ReportLab, standard fonts, selectable text).

The renderer is deliberately dumb: it lays out a ``ResumeDoc`` / ``LetterDoc`` that ``generate.py`` assembled
from the structured knowledge base and the profile. It never sees LLM output and never decides *what* to say,
only how it fits on the page (docs/SPEC.md section 1 rule 1 and section 5.5).

Resume layout (ATS friendly): US Letter, one column, Helvetica (a standard PDF font, no embedding needed),
no images, no text boxes, no graphics other than thin rules under the section headings. Every piece of text is
real, selectable text that round-trips through ``pypdf``. Bullets are drawn with the standard Symbol font so
text extractors report a proper bullet character.

One-page policy (``render_resume``): the document always ends up on exactly one page or ``RenderError`` is
raised. Bullets carry an implicit priority (position inside the entry; earlier entries and sections matter
more). The renderer walks a monotone sequence of ever smaller states and picks the first that fits, using a
binary search over real layout passes:

1. at the base font size, drop the lowest-priority bullets while every entry keeps at least two bullets;
2. then shrink the font in 0.5pt steps down to the floor (9pt);
3. then keep dropping bullets down to one per entry;
4. finally drop whole entries (last sections first). Education entries are never dropped.

Text sanitising (``pdf_safe``): standard fonts only cover WinAnsi (Windows-1252). Accents, en/em dashes,
curly quotes, the euro sign etc. render natively; look-alike typography is mapped to ASCII; letters outside
the range are transliterated (``NFKD`` without accents); anything else becomes ``?``. Zero-width and control
characters are removed. Names with apostrophes (O'Brien) are ordinary characters and need no special care.

The output is deterministic: ReportLab's ``invariant`` mode fixes timestamps and ids, so identical inputs
produce byte-identical PDFs.
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PAGE_WIDTH: float = float(LETTER[0])
PAGE_HEIGHT: float = float(LETTER[1])

BASE_FONT_PT = 10.5
MIN_FONT_PT = 9.0
FONT_STEP_PT = 0.5
MIN_LETTER_FONT_PT = 9.5

_RESUME_MARGIN_X = 0.7 * 72
_RESUME_MARGIN_Y = 0.55 * 72
_LETTER_MARGIN_X = 1.0 * 72
_LETTER_MARGIN_Y = 0.9 * 72

_FRAME_PAD = 6.0  # ReportLab's default Frame padding on every side
_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONT_ITALIC = "Helvetica-Oblique"
_BULLET = "•"


class RenderError(RuntimeError):
    """The document cannot be laid out (for example it cannot fit on one page even when minimal)."""


# --------------------------------------------------------------------------------------------- text safety

# Characters are spelled with \N{...} names on purpose: several are invisible or look alike.
_TRANSLITERATION: dict[str, str] = {
    "\N{HYPHEN}": "-",
    "\N{NON-BREAKING HYPHEN}": "-",
    "\N{FIGURE DASH}": "-",
    "\N{HORIZONTAL BAR}": "\N{EM DASH}",
    "\N{MINUS SIGN}": "-",
    "\N{PRIME}": "'",
    "\N{DOUBLE PRIME}": '"',
    "\N{LEFTWARDS ARROW}": "<-",
    "\N{RIGHTWARDS ARROW}": "->",
    "\N{LEFT RIGHT ARROW}": "<->",
    "\N{RIGHTWARDS DOUBLE ARROW}": "=>",
    "\N{LESS-THAN OR EQUAL TO}": "<=",
    "\N{GREATER-THAN OR EQUAL TO}": ">=",
    "\N{NOT EQUAL TO}": "!=",
    "\N{ALMOST EQUAL TO}": "~",
    "\N{LATIN SMALL LETTER L WITH STROKE}": "l",
    "\N{LATIN CAPITAL LETTER L WITH STROKE}": "L",
    "\N{LATIN SMALL LETTER D WITH STROKE}": "d",
    "\N{LATIN CAPITAL LETTER D WITH STROKE}": "D",
    "\N{LATIN SMALL LETTER H WITH STROKE}": "h",
    "\N{LATIN CAPITAL LETTER H WITH STROKE}": "H",
    "\N{LATIN SMALL LETTER DOTLESS I}": "i",
    "\N{BULLET OPERATOR}": _BULLET,
    "\N{BLACK CIRCLE}": _BULLET,
    "\N{WHITE CIRCLE}": _BULLET,
    "\N{BLACK SMALL SQUARE}": _BULLET,
    "\N{WHITE SMALL SQUARE}": _BULLET,
    "\N{WHITE BULLET}": _BULLET,
    "\N{TRIANGULAR BULLET}": _BULLET,
    "\N{HYPHEN BULLET}": _BULLET,
    "\N{BLACK SQUARE}": _BULLET,
}
_DROPPED = frozenset(
    {
        "\N{ZERO WIDTH SPACE}",
        "\N{ZERO WIDTH NON-JOINER}",
        "\N{ZERO WIDTH JOINER}",
        "\N{WORD JOINER}",
        "\N{ZERO WIDTH NO-BREAK SPACE}",
        "\N{SOFT HYPHEN}",
        "\N{LEFT-TO-RIGHT MARK}",
        "\N{RIGHT-TO-LEFT MARK}",
        "\N{LEFT-TO-RIGHT EMBEDDING}",
    }
)
_SPACE_LIKE = frozenset(
    {
        "\N{NO-BREAK SPACE}",
        "\N{FIGURE SPACE}",
        "\N{THIN SPACE}",
        "\N{HAIR SPACE}",
        "\N{NARROW NO-BREAK SPACE}",
        "\N{EN SPACE}",
        "\N{EM SPACE}",
    }
)


def _encodable(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _fold_char(char: str) -> str:
    """Best-effort replacement of one character that standard PDF fonts cannot draw."""
    if char in _TRANSLITERATION:
        return _TRANSLITERATION[char]
    decomposed = unicodedata.normalize("NFKD", char)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    if stripped and all(_encodable(c) and c.isprintable() for c in stripped):
        return stripped
    return "?"


def pdf_safe(text: str) -> str:
    """Return ``text`` restricted to what the standard PDF fonts draw (see the module docstring).

    Whitespace runs collapse to single spaces, control / zero-width characters disappear, characters in
    Windows-1252 pass through, look-alikes are transliterated and everything else becomes ``?``. Pure ASCII
    and Latin-1 text is returned unchanged apart from whitespace normalisation.
    """
    out: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        if char in _DROPPED or unicodedata.combining(char):
            continue
        if char in _SPACE_LIKE or char.isspace():
            out.append(" ")
        elif unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            continue
        elif char == _BULLET or (_encodable(char) and char not in _TRANSLITERATION):
            out.append(char)
        else:
            out.append(_fold_char(char))
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def count_unrenderable(text: str) -> int:
    """Number of characters in ``text`` that ``pdf_safe`` had to replace with ``?``."""
    return max(0, pdf_safe(text).count("?") - text.count("?"))


def markup(text: str) -> str:
    """``pdf_safe`` + XML escaping + Symbol-font bullets: safe to embed in a ReportLab ``Paragraph``."""
    return escape(pdf_safe(text)).replace(_BULLET, f'<font name="Symbol">{_BULLET}</font>')


# --------------------------------------------------------------------------------------------- document model


@dataclass(frozen=True)
class ResumeEntry:
    """One resume entry. ``bullets`` are ordered by priority (most important first)."""

    heading: str
    dates: str = ""
    subheading: str = ""
    detail: str = ""
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResumeSection:
    """A titled group of entries. ``protected`` sections never lose whole entries when fitting a page."""

    title: str
    entries: tuple[ResumeEntry, ...]
    protected: bool = False


@dataclass(frozen=True)
class ResumeDoc:
    name: str
    contact: tuple[str, ...] = ()
    sections: tuple[ResumeSection, ...] = ()
    skills: tuple[str, ...] = ()
    skills_title: str = "Skills"


@dataclass(frozen=True)
class LetterDoc:
    """A cover letter: header block, salutation, body paragraphs, closing and signature."""

    sender_name: str
    contact: tuple[str, ...] = ()
    date_line: str = ""
    recipient_lines: tuple[str, ...] = ()
    subject: str = ""
    salutation: str = "Dear Hiring Manager,"
    paragraphs: tuple[str, ...] = ()
    closing: str = "Sincerely,"
    signature: str = ""

    def plain_text(self) -> str:
        """The letter as it should be pasted into a text box (no contact header)."""
        parts = [self.salutation, *self.paragraphs, f"{self.closing}\n{self.signature}".strip()]
        return "\n\n".join(p for p in parts if p)


@dataclass(frozen=True)
class RenderInfo:
    """What the fitting pass had to do; ``generate.py`` turns this into human-readable notes."""

    pages: int
    font_pt: float
    dropped_bullets: int = 0
    dropped_entries: int = 0
    path: Path | None = None

    @property
    def trimmed(self) -> bool:
        return bool(self.dropped_bullets or self.dropped_entries)


# --------------------------------------------------------------------------------------------- styles


@dataclass(frozen=True)
class _Styles:
    name: ParagraphStyle
    contact: ParagraphStyle
    heading: ParagraphStyle
    entry: ParagraphStyle
    cell: ParagraphStyle
    entry_right: ParagraphStyle
    sub: ParagraphStyle
    bullet: ParagraphStyle
    body: ParagraphStyle
    letter: ParagraphStyle
    letter_bold: ParagraphStyle
    pt: float


def _styles(pt: float) -> _Styles:
    leading = round(pt * 1.2, 2)

    def make(name: str, **kwargs: Any) -> ParagraphStyle:
        base: dict[str, Any] = {
            "fontName": _FONT,
            "fontSize": pt,
            "leading": leading,
            "textColor": black,
            "alignment": TA_LEFT,
            "spaceBefore": 0,
            "spaceAfter": 0,
        }
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    return _Styles(
        name=make(
            "name",
            fontName=_FONT_BOLD,
            fontSize=pt + 6,
            leading=round((pt + 6) * 1.15, 2),
            alignment=TA_CENTER,
            spaceAfter=1,
        ),
        contact=make("contact", alignment=TA_CENTER, spaceAfter=2),
        heading=make("heading", fontName=_FONT_BOLD, fontSize=pt + 0.5, spaceBefore=pt * 0.7),
        entry=make("entry", fontName=_FONT, spaceBefore=pt * 0.35),
        cell=make("cell", fontName=_FONT),
        entry_right=make("entry_right", alignment=TA_RIGHT),
        sub=make("sub", fontName=_FONT_ITALIC),
        bullet=make(
            "bullet",
            leftIndent=13,
            bulletIndent=2,
            bulletFontName="Symbol",
            bulletFontSize=pt,
            spaceBefore=1,
        ),
        body=make("body", leading=round(pt * 1.32, 2), spaceAfter=pt * 0.7),
        letter=make("letter", leading=round(pt * 1.25, 2)),
        letter_bold=make("letter_bold", fontName=_FONT_BOLD, leading=round(pt * 1.25, 2)),
        pt=pt,
    )


# --------------------------------------------------------------------------------------------- resume story


def _content_width(margin_x: float) -> float:
    return PAGE_WIDTH - 2 * margin_x - 2 * _FRAME_PAD


def _dated_row(left: str, right: str, styles: _Styles, width: float) -> Any:
    """Left text with a right-aligned date on the same line (a borderless two-cell table)."""
    if not right:
        return Paragraph(left, styles.entry)
    right_width = stringWidth(pdf_safe(right), _FONT, styles.pt) + 4
    table = Table(
        [[Paragraph(left, styles.cell), Paragraph(markup(right), styles.entry_right)]],
        colWidths=[width - right_width - 6, right_width + 6],
        hAlign="LEFT",
        spaceBefore=styles.entry.spaceBefore,
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _resume_story(doc: ResumeDoc, pt: float) -> list[Any]:
    styles = _styles(pt)
    width = _content_width(_RESUME_MARGIN_X)
    story: list[Any] = [Paragraph(f"<b>{markup(doc.name)}</b>", styles.name)]
    if doc.contact:
        story.append(Paragraph(markup(" | ".join(doc.contact)), styles.contact))

    def heading(title: str) -> None:
        story.append(Paragraph(markup(title.upper()), styles.heading))
        story.append(HRFlowable(width="100%", thickness=0.6, spaceBefore=1, spaceAfter=1))

    for section in doc.sections:
        heading(section.title)
        for entry in section.entries:
            story.append(_dated_row(f"<b>{markup(entry.heading)}</b>", entry.dates, styles, width))
            if entry.subheading:
                story.append(Paragraph(markup(entry.subheading), styles.sub))
            if entry.detail:
                story.append(Paragraph(markup(entry.detail), styles.sub))
            story.extend(
                Paragraph(markup(text), styles.bullet, bulletText=_BULLET) for text in entry.bullets
            )
    if doc.skills:
        heading(doc.skills_title)
        story.append(Paragraph(markup(", ".join(doc.skills)), styles.entry))
    return story


def _build_pdf(
    story: list[Any],
    *,
    margins: tuple[float, float],
    title: str,
    author: str,
    subject: str,
) -> tuple[bytes, int]:
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=margins[0],
        rightMargin=margins[0],
        topMargin=margins[1],
        bottomMargin=margins[1],
        title=pdf_safe(title),
        author=pdf_safe(author),
        subject=pdf_safe(subject),
        creator="",
        invariant=1,
    )
    template.build(story)
    return buffer.getvalue(), int(template.page)


# --------------------------------------------------------------------------------------------- fitting


def _removal_events(doc: ResumeDoc) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Bullet-removal events for phase 1 (keep >= 2 per entry) and phase 3 (keep >= 1 per entry).

    Every event names the entry whose LAST bullet is dropped next. Dropping order: highest bullet position
    first, then later sections, then later entries, so the first bullet of an entry is the last to go.
    """
    keyed: list[tuple[int, int, int]] = []
    for si, section in enumerate(doc.sections):
        for ei, entry in enumerate(section.entries):
            keyed.extend((bi, si, ei) for bi in range(1, len(entry.bullets)))
    keyed.sort(key=lambda k: (-k[0], -k[1], -k[2]))
    phase_one = [(si, ei) for bi, si, ei in keyed if bi >= 2]
    phase_three = [(si, ei) for bi, si, ei in keyed if bi == 1]
    return phase_one, phase_three


def _entry_events(doc: ResumeDoc) -> list[tuple[int, int]]:
    events = [
        (si, ei)
        for si, section in enumerate(doc.sections)
        if not section.protected
        for ei in range(len(section.entries))
    ]
    events.sort(key=lambda e: (-e[0], -e[1]))
    return events


def _reduced(
    doc: ResumeDoc,
    bullet_events: list[tuple[int, int]],
    entry_events: list[tuple[int, int]],
) -> ResumeDoc:
    kept = [[len(e.bullets) for e in s.entries] for s in doc.sections]
    for si, ei in bullet_events:
        kept[si][ei] -= 1
    removed = set(entry_events)
    sections: list[ResumeSection] = []
    for si, section in enumerate(doc.sections):
        entries = tuple(
            replace(entry, bullets=entry.bullets[: kept[si][ei]])
            for ei, entry in enumerate(section.entries)
            if (si, ei) not in removed
        )
        if entries:
            sections.append(replace(section, entries=entries))
    return replace(doc, sections=tuple(sections))


def _font_ladder(base_pt: float, min_pt: float) -> list[float]:
    ladder: list[float] = []
    size = base_pt - FONT_STEP_PT
    while size >= min_pt - 1e-9:
        ladder.append(round(size, 2))
        size -= FONT_STEP_PT
    return ladder


def render_resume_bytes(
    doc: ResumeDoc, *, base_pt: float = BASE_FONT_PT, min_pt: float = MIN_FONT_PT
) -> tuple[bytes, RenderInfo]:
    """Lay ``doc`` out on exactly one page; returns the PDF bytes and what had to be trimmed.

    Raises ``RenderError`` if even the smallest state (protected sections + skills) needs a second page.
    """
    if not pdf_safe(doc.name):
        raise RenderError("a resume needs a name")
    phase_one, phase_three = _removal_events(doc)
    entry_events = _entry_events(doc)
    ladder = _font_ladder(base_pt, min_pt)
    n_a, n_f, n_c, n_e = len(phase_one), len(ladder), len(phase_three), len(entry_events)
    total = n_a + 1 + n_f + n_c + n_e
    meta = {"title": f"{doc.name} - Resume", "author": doc.name, "subject": "Resume"}

    def state(index: int) -> tuple[ResumeDoc, float, int, int]:
        if index <= n_a:
            return _reduced(doc, phase_one[:index], []), base_pt, index, 0
        if index <= n_a + n_f:
            return _reduced(doc, phase_one, []), ladder[index - n_a - 1], n_a, 0
        floor = ladder[-1] if ladder else base_pt
        if index <= n_a + n_f + n_c:
            extra = index - n_a - n_f
            return _reduced(doc, phase_one + phase_three[:extra], []), floor, n_a + extra, 0
        extra = index - n_a - n_f - n_c
        return (
            _reduced(doc, phase_one + phase_three, entry_events[:extra]),
            floor,
            n_a + n_c,
            extra,
        )

    def build(index: int) -> tuple[bytes, int, RenderInfo]:
        reduced, pt, dropped_bullets, dropped_entries = state(index)
        data, pages = _build_pdf(
            _resume_story(reduced, pt), margins=(_RESUME_MARGIN_X, _RESUME_MARGIN_Y), **meta
        )
        return data, pages, RenderInfo(pages, pt, dropped_bullets, dropped_entries)

    if build(total - 1)[1] != 1:
        raise RenderError("the resume does not fit on one page even without any optional content")
    low, high = 0, total - 1  # invariant: state(high) fits; states below `low` do not
    while low < high:
        mid = (low + high) // 2
        if build(mid)[1] == 1:
            high = mid
        else:
            low = mid + 1
    data, _pages, info = build(low)
    return data, info


def render_resume(
    doc: ResumeDoc,
    path: Path,
    *,
    base_pt: float = BASE_FONT_PT,
    min_pt: float = MIN_FONT_PT,
) -> RenderInfo:
    """Render ``doc`` to ``path`` (atomically) as a one-page PDF."""
    data, info = render_resume_bytes(doc, base_pt=base_pt, min_pt=min_pt)
    write_bytes_atomic(path, data)
    return replace(info, path=path)


# --------------------------------------------------------------------------------------------- cover letter


def _letter_story(doc: LetterDoc, pt: float, paragraphs: tuple[str, ...]) -> list[Any]:
    styles = _styles(pt)
    story: list[Any] = [Paragraph(f"<b>{markup(doc.sender_name)}</b>", styles.letter_bold)]
    if doc.contact:
        story.append(Paragraph(markup(" | ".join(doc.contact)), styles.letter))
    if doc.date_line:
        story += [Spacer(1, pt), Paragraph(markup(doc.date_line), styles.letter)]
    story.append(Spacer(1, pt * 1.2))
    story.extend(Paragraph(markup(line), styles.letter) for line in doc.recipient_lines)
    if doc.subject:
        story += [Spacer(1, pt * 0.6), Paragraph(f"<b>{markup(doc.subject)}</b>", styles.letter)]
    story += [Spacer(1, pt * 1.2), Paragraph(markup(doc.salutation), styles.body)]
    story.extend(Paragraph(markup(text), styles.body) for text in paragraphs)
    story.append(Paragraph(markup(doc.closing), styles.letter))
    story += [Spacer(1, pt * 1.6), Paragraph(markup(doc.signature), styles.letter)]
    return story


def render_cover_letter_bytes(doc: LetterDoc) -> tuple[bytes, RenderInfo]:
    """Lay the letter out on one page: 11pt, then smaller fonts, then dropping trailing paragraphs."""
    if not pdf_safe(doc.sender_name):
        raise RenderError("a cover letter needs a sender name")
    if not doc.paragraphs:
        raise RenderError("a cover letter needs at least one paragraph")
    sizes = [11.0, 10.5, 10.0, MIN_LETTER_FONT_PT]
    meta = {
        "title": f"{doc.sender_name} - Cover Letter",
        "author": doc.sender_name,
        "subject": "Cover letter",
    }
    keep = len(doc.paragraphs)
    while keep >= min(2, len(doc.paragraphs)):
        for pt in sizes:
            data, pages = _build_pdf(
                _letter_story(doc, pt, doc.paragraphs[:keep]),
                margins=(_LETTER_MARGIN_X, _LETTER_MARGIN_Y),
                **meta,
            )
            if pages == 1:
                return data, RenderInfo(1, pt, dropped_bullets=len(doc.paragraphs) - keep)
        keep -= 1
    raise RenderError("the cover letter does not fit on one page")


def render_cover_letter(doc: LetterDoc, path: Path) -> RenderInfo:
    """Render the letter to ``path`` (atomically)."""
    data, info = render_cover_letter_bytes(doc)
    write_bytes_atomic(path, data)
    return replace(info, path=path)


# --------------------------------------------------------------------------------------------- io


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory so a crash never leaves a torn PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
