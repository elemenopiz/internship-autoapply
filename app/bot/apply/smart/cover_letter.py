"""Per-role cover letters, grounded in the candidate record and verified.

Written only when a form asks for one. The body goes through the same
fabrication checks as screening answers (drafting.verify_text); a letter that
fails them is discarded — the application then proceeds without one if the
field is optional, or is held if the field is required.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from bot.apply.smart.candidate import Candidate
from bot.apply.smart.drafting import Generate, verify_text

logger = logging.getLogger(__name__)

_PROMPT = """Write the body of a cover letter for a real internship applicant.
It is submitted without further review, so every sentence must be true.

RULES
- Use ONLY facts from the CANDIDATE RECORD. Never invent experience, numbers,
  employers, coursework, or personal stories. Cite only numbers that appear in
  the record.
- Three short paragraphs, 200-280 words total, first person, plain and specific.
- Paragraph 1: the role and the candidate's most relevant real experience.
- Paragraph 2: two concrete examples from the record mapped to needs named in
  the JOB POSTING.
- Paragraph 3: {motivation_rule}
- No salutation, no sign-off, no date, no placeholders, no em dashes. Do not
  claim to have used the company's products or to know anyone there.

CANDIDATE RECORD
{record}

JOB POSTING ({company}, {title})
{posting}

Return only the three paragraphs."""

_WITH_MOTIVATION = ("why this role, using the candidate's own motivation statement from "
                    "the record, tied to specifics of the posting.")
_WITHOUT_MOTIVATION = ("a brief, factual close about what the candidate would contribute, "
                       "without claiming personal feelings about the company.")


def write_cover_letter(cand: Candidate, generate: Generate, company: str, title: str,
                       posting: str) -> str | None:
    """The verified letter body, or None when drafting or verification fails."""
    prompt = _PROMPT.format(
        motivation_rule=_WITH_MOTIVATION if cand.facts.motivation.strip() else _WITHOUT_MOTIVATION,
        record="\n".join(cand.fact_lines()), company=company or "the company",
        title=title or "the role", posting=(posting or "(not available)")[:6000])
    try:
        text = generate(prompt).strip()
    except Exception as exc:
        logger.warning("Cover letter generation failed: %s", exc)
        return None
    text = text.replace("—", ", ").replace("–", "-")
    text = re.sub(r"^(dear [^\n]*\n+)", "", text, flags=re.IGNORECASE).strip()
    problems = verify_text(text, cand, posting)
    words = len(text.split())
    if words < 120 or words > 400:
        problems.append(f"length {words} words is outside 120-400")
    if problems:
        logger.warning("Cover letter for %s rejected: %s", company, "; ".join(problems))
        return None
    return text


def full_letter(body: str, cand: Candidate, company: str) -> str:
    """Salutation + body + sign-off, for text fields and the PDF."""
    greeting = f"Dear {company} Recruiting Team," if company else "Dear Hiring Team,"
    return f"{greeting}\n\n{body}\n\nSincerely,\n{cand.profile.full_name}"


def render_pdf(body: str, cand: Candidate, company: str, out_path: Path) -> Path:
    """Render a one-page letter PDF with the candidate's contact header."""
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    out_path.parent.mkdir(parents=True, exist_ok=True)
    p = cand.profile
    name = ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=15,
                          alignment=TA_CENTER, spaceAfter=4)
    contact = ParagraphStyle("contact", fontName="Helvetica", fontSize=9.5,
                             alignment=TA_CENTER, spaceAfter=14)
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=15,
                                spaceAfter=10)
    contact_line = " • ".join(x for x in (p.location, p.email, p.phone_full,
                                          p.linkedin_url or "") if x)
    story = [Paragraph(escape(p.full_name), name), Paragraph(escape(contact_line), contact),
             Paragraph(date.today().strftime("%B %d, %Y").replace(" 0", " "), body_style),
             Spacer(1, 4)]
    for para in full_letter(body, cand, company).split("\n\n"):
        story.append(Paragraph(escape(para).replace("\n", "<br/>"), body_style))
    SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=inch, rightMargin=inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch).build(story)
    return out_path
