"""Fictional, fully populated sample data shared by every test suite (unit, browser, end-to-end).

Everything here is invented: "Alex Rivera", example.test addresses, 555 phone numbers and made-up employers.
Never replace it with real personal data. The pieces are consistent with each other on purpose:

* ``sample_profile()``: a complete ``Profile`` (every ``REQUIRED_PROFILE_FIELDS`` entry is filled,
  authorised to work in the US, no sponsorship needed);
* ``sample_experiences()``: six knowledge-base entries (2 work, 2 projects, 1 leadership, 1 education);
* ``sample_knowledge_base()``: those entries assembled as a KB;
* ``write_sample_experience_files(dir)``: the same entries as ``.md`` and ``.json`` experience files, in an
  order that makes ``load_kb`` return exactly ``sample_experiences()``;
* ``make_sample_resume_pdf(path)``: a realistic single-page PDF resume whose text is the same background.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Table, TableStyle

from autoapply.models import Experience, KnowledgeBase, Profile
from autoapply.tailor.knowledge import experience_to_markdown, kb_from_experiences
from autoapply.tailor.render import markup


def sample_profile() -> Profile:
    """A complete, fictional profile (Alex Rivera, Austin TX) that passes every readiness check."""
    return Profile(
        first_name="Alex",
        last_name="Rivera",
        preferred_name="Alex",
        pronouns="they/them",
        email="alex.rivera@example.test",
        phone="(512) 555-0142",
        phone_country="United States",
        address_line1="1200 Example Street",
        address_line2="Apt 4B",
        city="Austin",
        state="TX",
        postal_code="78701",
        country="United States",
        linkedin_url="https://www.linkedin.com/in/alex-rivera-example",
        github_url="https://github.com/alex-rivera-example",
        portfolio_url="https://alex-rivera.example.test",
        school="The University of Texas at Austin",
        degree="Bachelor of Science",
        major="Management Information Systems",
        minor="Business Analytics",
        gpa="3.7",
        education_start_date="2024-08",
        graduation_date="2028-05",
        authorized_to_work_us=True,
        requires_sponsorship=False,
        willing_to_relocate=True,
        is_18_or_older=True,
        available_start_date="2027-05-17",
        available_end_date="2027-08-13",
        referral_source="Company website",
    )


def sample_experiences() -> list[Experience]:
    """Six realistic entries: two jobs, two projects, one leadership role and the degree."""
    return [
        Experience(
            id="lone-star-analyst",
            kind="work",
            title="Business Analyst Intern",
            organization="Lone Star Logistics Co.",
            location="Austin, TX",
            start="2026-06",
            end="2026-08",
            bullets=[
                "Built a Tableau dashboard tracking on-time delivery across 14 regional routes, "
                "cutting weekly reporting time by 6 hours",
                "Analyzed 18 months of shipment data in SQL and Excel to identify three recurring "
                "bottlenecks, informing a pilot that reduced late deliveries by 12%",
                "Presented findings to a panel of 8 operations managers and documented a rollout "
                "checklist adopted by two regional teams",
                "Automated a weekly KPI summary email with Python, saving the analytics team about "
                "2 hours per week",
            ],
            skills=["SQL", "Excel", "Tableau", "Python"],
        ),
        Experience(
            id="bluebonnet-it-support",
            kind="work",
            title="Technology Support Assistant",
            organization="Bluebonnet Campus Technology Services",
            location="Austin, TX",
            start="2024-09",
            end="2025-05",
            bullets=[
                "Resolved 30+ support tickets per week for students and faculty with a 96% "
                "satisfaction rating",
                "Wrote 12 step-by-step troubleshooting guides in Confluence that reduced repeat "
                "tickets by 20%",
                "Trained 6 new student employees on the Jira ticketing workflow",
            ],
            skills=["Windows", "Jira", "Confluence"],
        ),
        Experience(
            id="course-planner",
            kind="project",
            title="Campus Course Planner",
            start="2025-09",
            end="2025-12",
            bullets=[
                "Built a Flask web app that lets students compare course schedules and flags time "
                "conflicts, used by 40 classmates during registration",
                "Designed a SQLite schema for 300 courses and wrote 25 unit tests that caught "
                "scheduling edge cases",
                "Presented the app at a department showcase attended by 60 people",
            ],
            skills=["Python", "Flask", "SQLite"],
        ),
        Experience(
            id="demand-forecast",
            kind="project",
            title="Retail Demand Forecasting Model",
            start="2026-01",
            end="2026-04",
            bullets=[
                "Trained a gradient boosting model on two years of public retail sales data, "
                "reducing forecast error (MAPE) from 18% to 11% versus a baseline",
                "Visualized results in a Tableau dashboard and wrote a 5-page memo with "
                "recommendations for inventory planning",
                "Cleaned and merged 4 datasets with pandas, documenting every assumption in a "
                "shared notebook",
            ],
            skills=["Python", "pandas", "scikit-learn", "Tableau"],
        ),
        Experience(
            id="longhorn-product-club",
            kind="leadership",
            title="Vice President of Programming",
            organization="Longhorn Product Club",
            location="Austin, TX",
            start="2025-01",
            end="present",
            bullets=[
                "Organized a 6-week workshop series on product discovery attended by 120 students",
                "Led a team of 5 officers and managed a $3,500 semester budget",
                "Recruited 9 industry speakers, growing average event attendance by 35%",
            ],
            skills=["Figma"],
        ),
        Experience(
            id="ut-austin-mis",
            kind="education",
            title="Bachelor of Science in Management Information Systems",
            organization="The University of Texas at Austin",
            location="Austin, TX",
            start="2024-08",
            end="2028-05",
            bullets=[
                "Relevant coursework: Data Structures, Database Management, Statistics for "
                "Business, Operations Management",
                "Honors: Dean's List (Fall 2024, Spring 2025)",
            ],
            skills=[],
        ),
    ]


def sample_knowledge_base() -> KnowledgeBase:
    """``sample_experiences()`` assembled as a KB (skills are the de-duplicated union)."""
    return kb_from_experiences(sample_experiences())


def write_sample_experience_files(directory: Path) -> list[Path]:
    """Write the sample experiences into ``directory`` as ``.md`` and ``.json`` experience files.

    File names sort in the same order as ``sample_experiences()``, so ``load_kb`` reproduces that list
    exactly. Returns the created paths. The directory is created when missing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    by_id = {e.id: e for e in sample_experiences()}

    def dump(items: list[Experience]) -> str:
        return json.dumps([e.model_dump(mode="json") for e in items], indent=2) + "\n"

    written: list[Path] = []

    def write(name: str, content: str) -> None:
        path = directory / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)

    write("01-lone-star-analyst.md", experience_to_markdown(by_id["lone-star-analyst"]))
    write("02-bluebonnet-it-support.md", experience_to_markdown(by_id["bluebonnet-it-support"]))
    write("03-projects.json", dump([by_id["course-planner"], by_id["demand-forecast"]]))
    write(
        "04-longhorn-product-club.json",
        json.dumps(by_id["longhorn-product-club"].model_dump(mode="json"), indent=2) + "\n",
    )
    write("05-education.md", experience_to_markdown(by_id["ut-austin-mis"]))
    return written


# --------------------------------------------------------------------------------------------- resume PDF


_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _short_url(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")


def _month_year(value: str | None) -> str:
    if not value:
        return ""
    if value == "present":
        return "Present"
    year, _, month = value.partition("-")
    return f"{_MONTH_ABBREVIATIONS[int(month) - 1]} {year}" if month else year


def make_sample_resume_pdf(path: Path) -> Path:
    """Write a realistic one-page resume PDF (Times, human-style layout) for the sample background.

    Its text matches ``sample_profile()`` / ``sample_experiences()`` exactly, so parsing it with
    ``build_kb_from_resume`` (heuristic parser or a faithful LLM) must reproduce the same entries. The layout
    intentionally differs from ``tailor.render``: serif font, "Title, Organization" headers, location on its own
    line. Returns ``path``.
    """
    profile, experiences = sample_profile(), sample_experiences()
    size = 10.5
    style = ParagraphStyle(
        "body", fontName="Times-Roman", fontSize=size, leading=size * 1.2, textColor=black
    )
    bold = ParagraphStyle("bold", parent=style, fontName="Times-Bold")
    italic = ParagraphStyle("italic", parent=style, fontName="Times-Italic")
    right = ParagraphStyle("right", parent=style, alignment=TA_RIGHT)
    centre = ParagraphStyle("centre", parent=style, alignment=TA_CENTER)
    name = ParagraphStyle(
        "name", parent=centre, fontName="Times-Bold", fontSize=18, leading=21, spaceAfter=2
    )
    heading = ParagraphStyle(
        "heading", parent=bold, fontSize=11, spaceBefore=7, spaceAfter=0, leading=13
    )
    bullet = ParagraphStyle(
        "bullet",
        parent=style,
        leftIndent=14,
        bulletIndent=3,
        bulletFontName="Symbol",
        spaceBefore=1,
    )
    width = LETTER[0] - 2 * 54 - 12

    def row(left: str, date: str, left_style: ParagraphStyle = bold) -> Any:
        if not date:
            return Paragraph(markup(left), left_style)
        date_width = stringWidth(date, "Times-Roman", size) + 8
        table = Table(
            [[Paragraph(markup(left), left_style), Paragraph(markup(date), right)]],
            colWidths=[width - date_width, date_width],
            hAlign="LEFT",
            spaceBefore=4,
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

    contact = " | ".join(
        [
            f"{profile.city}, {profile.state}",
            profile.phone,
            profile.email,
            _short_url(profile.linkedin_url),
        ]
    )
    story: list[Any] = [
        Paragraph(markup(profile.full_name), name),
        Paragraph(markup(contact), centre),
    ]

    def section(title: str) -> None:
        story.append(Paragraph(title.upper(), heading))
        story.append(HRFlowable(width="100%", thickness=0.6, spaceBefore=1, spaceAfter=1))

    def bullets(items: list[str]) -> None:
        story.extend(Paragraph(markup(b), bullet, bulletText="•") for b in items)

    def entries(kinds: tuple[str, ...]) -> list[Experience]:
        return [e for e in experiences if e.kind in kinds]

    education = entries(("education",))[0]
    section("Education")
    story.append(row(str(education.organization), education.location or "", bold))
    story.append(
        row(
            education.title,
            f"{_month_year(education.start)} - Expected {_month_year(education.end)}",
            style,
        )
    )
    story.append(Paragraph(markup(f"GPA: {profile.gpa}/4.0 | Minor: {profile.minor}"), style))
    bullets(education.bullets)

    for title, kind in (
        ("Experience", "work"),
        ("Projects", "project"),
        ("Leadership", "leadership"),
    ):
        section(title)
        for exp in entries((kind,)):
            dates = f"{_month_year(exp.start)} - {_month_year(exp.end)}"
            if exp.kind == "project":
                story.append(row(f"{exp.title} | {', '.join(exp.skills)}", dates))
            else:
                story.append(row(f"{exp.title}, {exp.organization}", dates))
                story.append(Paragraph(markup(exp.location or ""), italic))
            bullets(exp.bullets)

    section("Skills")
    skills = kb_from_experiences(experiences).skills
    story.append(Paragraph(markup("Tools and skills: " + ", ".join(skills)), style))

    path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=54,
        rightMargin=54,
        topMargin=40,
        bottomMargin=40,
        title=f"{profile.full_name} - Resume",
        author=profile.full_name,
        invariant=1,
    )
    document.build(story)
    return path
