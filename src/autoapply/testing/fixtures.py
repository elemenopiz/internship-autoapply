"""Shared, deterministic sample world for the test suites (docs/SPEC.md sections 5.14 and 8).

``build_sample_workbook`` writes a realistic multi-sheet "verified opportunities" workbook and returns a
``SampleWorkbookInfo`` that says, row by row, what ingest must do with it: which opportunity ids survive
(``expected_ids``), which rows the workbook filters reject and why (``expected_rejections``), which rows
de-duplication merges away (``expected_duplicates``), plus the logical mock site and README role family of every
row so the end-to-end suite can register matching mock postings and check routing.

All names, companies and URLs are fictional. Nothing here touches the network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from autoapply.models import ATS
from autoapply.normalize import opportunity_id, slugify

SAMPLE_TODAY = date(2026, 9, 29)  # equals ``FakeClock``'s default day, so relative ages line up
SAMPLE_SHEET = "Verified Opportunities"
SAMPLE_TARGET_TERM = "Summer 2027"
SAMPLE_HEADERS = (
    "Employer",
    "Position",
    "Apply Link",
    "Location",
    "Internship Term",
    "Status",
    "Date Posted",
    "Last Verified",
    "Deadline",
    "ATS",
    "Notes",
    "Pay (hourly)",
)
SITES = ("workday", "greenhouse", "lever", "ashby", "portal", "captcha", "closed", "sso")
# Sites whose posting URL is ``base + generated path``. The others host exactly one sample row: when their base
# URL has no path of its own a path is generated, otherwise the base URL is the posting URL verbatim.
_PATH_APPENDED = frozenset({"workday", "greenhouse", "lever", "ashby"})
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_LONG = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_UUID_NAMESPACE = uuid.UUID("6f1c2d3e-0000-4000-8000-000000000027")
_SITE_ATS = {
    "workday": ATS.WORKDAY,
    "greenhouse": ATS.GREENHOUSE,
    "lever": ATS.LEVER,
    "ashby": ATS.ASHBY,
    "portal": ATS.CUSTOM,
    "captcha": ATS.CUSTOM,
    "closed": ATS.CUSTOM,
    "sso": ATS.CUSTOM,
}


@dataclass(frozen=True)
class SampleUrls:
    """Base URLs of the logical mock sites the sample rows point at (placeholders by default).

    ``workday`` / ``greenhouse`` / ``lever`` / ``ashby`` are site roots; job paths are appended (real layouts:
    ``/en-US/External/job/Austin-TX/<title>_R-1001``, ``/<company>/jobs/<id>``, ``/<company>/<uuid>``). ``portal``,
    ``captcha``, ``closed`` and ``sso`` each host one sample row: give a root and a path is generated, or give
    the full posting URL (anything with a path) and it is used verbatim.
    """

    workday: str = "https://workday.example.test"
    greenhouse: str = "https://greenhouse.example.test"
    lever: str = "https://lever.example.test"
    ashby: str = "https://ashby.example.test"
    portal: str = "https://portal.example.test"
    captcha: str = "https://captcha.example.test"
    closed: str = "https://closed.example.test"
    sso: str = "https://sso.example.test"

    def for_site(self, site: str) -> str:
        if site not in SITES:
            raise KeyError(f"unknown sample site {site!r}; expected one of {', '.join(SITES)}")
        return str(getattr(self, site))


@dataclass(frozen=True)
class SampleRow:
    """One data row of the sample sheet and what the pipeline must do with it."""

    key: str
    row_number: int  # 1-based sheet row
    company: str
    title: str
    location: str
    site: str  # logical mock site, "aggregator", or "none" (no link)
    url: str  # what the row's link resolves to ("" = no link)
    path: str  # part of ``url`` after the site base URL ("" when the base URL is verbatim)
    job_id: (
        str  # posting id on its site (Workday requisition, Greenhouse id, Lever/Ashby uuid, slug)
    )
    id: str  # Opportunity.id this row yields ("" when it has no link)
    outcome: str  # "ingested" | a workbook ``RejectReason`` value | "duplicate"
    family: str | None  # README role family the scorer must assign; None = not a target role
    ats: ATS  # expected Opportunity.ats
    term: str  # text in the Internship Term cell ("" = blank)
    status: str  # text in the Status cell ("" = blank)
    last_verified: date | None = None
    posted: date | None = None
    deadline: date | None = None
    duplicate_of: str = ""  # id of the surviving record (outcome == "duplicate")
    flags: tuple[str, ...] = ()  # expected ``extra`` flags: "term_assumed", "date_unknown"


@dataclass(frozen=True)
class SampleWorkbookInfo:
    """Everything a test needs to know about a workbook written by ``build_sample_workbook``."""

    path: Path
    today: date
    urls: SampleUrls
    sheet: str
    header_row: int
    headers: tuple[str, ...]
    target_term: str
    rows: tuple[SampleRow, ...]
    sheets: tuple[str, ...] = ("Summary", SAMPLE_SHEET, "Rejected")

    @property
    def expected_ids(self) -> list[str]:
        """Ids of the opportunities that must survive ingest (workbook filters + de-duplication), sheet order."""
        return [r.id for r in self.rows if r.outcome == "ingested"]

    @property
    def expected_rejections(self) -> dict[str, list[str]]:
        """``{reason: [row titles]}`` for rows the WORKBOOK filters reject, in sheet order.

        Reasons are ``RejectReason`` values: closed, deadline_passed, wrong_term, stale, not_internship,
        missing_fields, no_url. Rows merged away by de-duplication are listed in ``expected_duplicates``.
        """
        out: dict[str, list[str]] = {}
        for row in self.rows:
            if row.outcome not in ("ingested", "duplicate"):
                out.setdefault(row.outcome, []).append(row.title)
        return out

    @property
    def expected_duplicates(self) -> dict[str, list[str]]:
        """``{surviving opportunity id: [URLs of the rows merged into it]}``."""
        out: dict[str, list[str]] = {}
        for row in self.rows:
            if row.outcome == "duplicate":
                out.setdefault(row.duplicate_of, []).append(row.url)
        return out

    @property
    def workbook_kept(self) -> list[SampleRow]:
        """Rows the workbook provider returns: survivors plus the duplicates (dedupe happens afterwards)."""
        return [r for r in self.rows if r.outcome in ("ingested", "duplicate")]

    @property
    def eligible(self) -> list[SampleRow]:
        """Surviving rows that are target roles (the scorer should let these through)."""
        return [r for r in self.rows if r.outcome == "ingested" and r.family is not None]

    def row(self, title: str, company: str | None = None) -> SampleRow:
        """The row with this Position text (and company); rows merged away as duplicates are only used when
        nothing else matches. ``KeyError`` when there is no such row or several."""
        matches = [
            r for r in self.rows if r.title == title and (company is None or r.company == company)
        ]
        primary = [r for r in matches if r.outcome != "duplicate"] or matches
        if len(primary) != 1:
            raise KeyError(f"{len(primary)} sample rows match {title!r} / {company!r}")
        return primary[0]

    def by_key(self, key: str) -> SampleRow:
        """The row with this internal key (stable across releases, unlike titles)."""
        for row in self.rows:
            if row.key == key:
                return row
        raise KeyError(key)

    def by_site(self, site: str) -> list[SampleRow]:
        return [r for r in self.rows if r.site == site and r.outcome == "ingested"]


# ------------------------------------------------------------------------------------------------ row specs


@dataclass(frozen=True)
class _Spec:
    key: str
    company: str
    title: str
    site: str = "workday"
    location: str = "Austin, TX"
    term: str = "Summer 2027"
    status: str = "Open"
    posted: int | None = None  # days before "today"
    verified: int | None = 3
    deadline: int | None = None  # days from "today" (negative = in the past)
    style: str = "date"  # how the date cells are written, see _write_date
    ats_text: str = ""
    notes: str = ""
    pay: str = ""
    link: str = "hyperlink"  # hyperlink | text | formula | shown_url | none
    family: str | None = None
    outcome: str = "ingested"
    duplicate_of: str = ""  # key of the surviving spec
    reuse_url_of: str = ""  # key of the spec whose URL is reused (with ``variant``)
    variant: str = ""  # tracking | workday_locale
    aggregator: str = ""  # linkedin | indeed
    ragged: bool = False  # write only company, title, link, location
    blank_company: bool = False
    flags: tuple[str, ...] = ()


_LONG_NOTES = (
    "Rotational analytics program with weekly mentor check-ins, a capstone presentation and optional "
    "conversion interviews. Applications open Fall 2026; the internship itself runs Summer 2027. "
) * 60

# Sheet order. "-" separator rows and blank rows are added by ``_LAYOUT`` below.
_SPECS: tuple[_Spec, ...] = (
    _Spec(
        "alder_pm",
        "Alder Systems",
        "Product Management Intern",
        "workday",
        posted=20,
        verified=5,
        ats_text="Workday",
        notes="Referral welcome",
        pay="$34",
        family="product_management",
    ),
    _Spec(
        "birch_apm",
        "Birchwood Health",
        "Associate Product Manager Intern",
        "greenhouse",
        location="Dallas, TX",
        term="Summer '27",
        status="Active",
        posted=25,
        verified=9,
        style="us",
        ats_text="Greenhouse",
        pay="$30",
        family="product_management",
    ),
    _Spec(
        "cobalt_fin",
        "Cobalt Freight",
        "Strategic Finance Intern",
        "greenhouse",
        location="Houston, TX",
        status="Closed",
        verified=5,
        ats_text="Greenhouse",
        outcome="closed",
    ),
    _Spec(
        "cobalt_tpm",
        "Cobalt Freight",
        "Technical Program Manager Intern",
        "lever",
        location="Houston, TX",
        term="Sum 2027",
        status="Verified",
        verified=11,
        style="mdy",
        ats_text="Lever",
        link="text",
        family="technical_program_management",
    ),
    _Spec(
        "dunmore_pgm",
        "Dunmore Energy",
        "Program Management Intern",
        "ashby",
        location="Remote",
        term="2027 Summer",
        status="Yes",
        posted=30,
        verified=12,
        deadline=30,
        style="dmy",
        ats_text="Ashby",
        notes="Remote friendly; 12 weeks",
        link="formula",
        family="technical_program_management",
    ),
    _Spec(
        "elm_tc",
        "Elmwood Retail",
        "Technology Consulting Summer Analyst",
        "workday",
        location="Chicago, IL",
        term="SUMMER 2027",
        verified=3,
        style="serial",
        ats_text="Workday",
        family="technology_consulting",
    ),
    _Spec(
        "fern_tc",
        "Fernhill Software",
        "Technology Consultant Intern",
        "portal",
        location="Seattle, WA",
        verified=20,
        style="iso",
        ats_text="Custom",
        family="technology_consulting",
    ),
    _Spec(
        "granite_strat",
        "Granite Bay Capital",
        "Corporate Strategy Intern",
        "greenhouse",
        location="New York, NY",
        posted=14,
        verified=None,
        style="date_us",
        ats_text="Greenhouse",
        family="strategy",
    ),
    _Spec(
        "granite_adv",
        "Granite Bay Capital",
        "Technology Advisory Intern",
        "greenhouse",
        location="New York, NY",
        verified=5,
        deadline=-14,
        ats_text="Greenhouse",
        outcome="deadline_passed",
    ),
    _Spec(
        "harbor_ops",
        "Harbor Point Media",
        "Strategy & Operations Intern",
        "sso",
        location="Los Angeles, CA",
        status="Live",
        verified=30,
        style="long",
        ats_text="Employer site",
        link="shown_url",
        family="strategy",
    ),
    _Spec(
        "harbor_dev",
        "Harbor Point Media",
        "Corporate Development Intern",
        "greenhouse",
        location="Los Angeles, CA",
        term="Fall 2026",
        verified=6,
        ats_text="Greenhouse",
        outcome="wrong_term",
    ),
    _Spec(
        "iron_biz_indeed",
        "Ironbridge Foods",
        "Business Operations Intern",
        "aggregator",
        location="Denver, CO",
        verified=15,
        link="text",
        aggregator="indeed",
        outcome="duplicate",
        duplicate_of="iron_biz",
    ),
    _Spec(
        "iron_biz",
        "Ironbridge Foods",
        "Business Operations Intern",
        "ashby",
        location="Denver, CO",
        verified=2,
        ats_text="Ashby",
        family="business_operations",
    ),
    _Spec(
        "iron_sc",
        "Ironbridge Foods",
        "Supply Chain Analyst Intern",
        "ashby",
        location="Denver, CO",
        term="Summer 2028",
        verified=6,
        ats_text="Ashby",
        outcome="wrong_term",
    ),
    _Spec(
        "juniper_rev",
        "Juniper Cloud",
        "Revenue Operations Intern",
        "lever",
        location="San Francisco, CA",
        verified=40,
        ats_text="Lever",
        family="business_operations",
    ),
    _Spec(
        "juniper_ins",
        "Juniper Cloud",
        "Insights Analyst Intern",
        "lever",
        location="San Francisco, CA",
        verified=60,
        ats_text="Lever",
        outcome="stale",
    ),
    _Spec(
        "kestrel_ba",
        "Kestrel Aerospace",
        "Business Analyst Intern",
        "workday",
        verified=7,
        ats_text="Workday",
        family="business_analysis",
    ),
    _Spec(
        "kestrel_ba_linkedin",
        "Kestrel Aerospace",
        "Business Analyst Internship (Summer 2027)",
        "aggregator",
        location="Austin, Texas",
        verified=3,
        aggregator="linkedin",
        outcome="duplicate",
        duplicate_of="kestrel_ba",
    ),
    _Spec(
        "kestrel_sys",
        "Kestrel Aerospace",
        "Systems Analyst Intern",
        "workday",
        posted=120,
        verified=None,
        ats_text="Workday",
        outcome="stale",
    ),
    _Spec(
        "lake_bsa",
        "Lakeshore Insurance",
        "Business Systems Analyst Intern",
        "captcha",
        location="Atlanta, GA",
        term="",
        status="",
        verified=None,
        ragged=True,
        family="business_analysis",
        flags=("term_assumed", "date_unknown"),
    ),
    _Spec(
        "lake_senior",
        "Lakeshore Insurance",
        "Senior Product Manager",
        "lever",
        location="Atlanta, GA",
        verified=5,
        outcome="not_internship",
    ),
    _Spec(
        "alder_data",
        "Alder Systems",
        "Data Analytics Intern",
        "greenhouse",
        verified=6,
        ats_text="Greenhouse",
        notes=_LONG_NOTES,
        family="analytics",
    ),
    _Spec(
        "alder_ft",
        "Alder Systems",
        "Business Analyst (Full-Time, New Grad)",
        "workday",
        verified=5,
        ats_text="Workday",
        outcome="not_internship",
    ),
    _Spec(
        "birch_pa",
        "Birchwood Health",
        "Product Analytics Intern",
        "closed",
        location="Dallas, TX",
        verified=8,
        ats_text="Custom",
        family="analytics",
    ),
    _Spec(
        "birch_nourl",
        "Birchwood Health",
        "Finance Strategy Intern",
        "none",
        location="Dallas, TX",
        verified=8,
        link="none",
        outcome="no_url",
    ),
    _Spec(
        "elm_mkt",
        "Elmwood Retail",
        "Marketing Intern",
        "lever",
        location="Chicago, IL",
        verified=10,
        ats_text="Lever",
        family=None,
    ),
    _Spec(
        "dunmore_proc",
        "Dunmore Energy",
        "Process Analyst Intern",
        "lever",
        location="Remote",
        status="Filled",
        verified=4,
        ats_text="Lever",
        outcome="closed",
    ),
    _Spec(
        "elm_da",
        "Elmwood Retail",
        "Data Analyst Intern",
        "workday",
        location="Chicago, IL",
        status="Inactive",
        verified=4,
        ats_text="Workday",
        outcome="closed",
    ),
    _Spec(
        "fern_po",
        "Fernhill Software",
        "Product Operations Intern",
        "ashby",
        location="Seattle, WA",
        status="No",
        verified=4,
        ats_text="Ashby",
        outcome="closed",
    ),
    _Spec(
        "granite_strat_dup",
        "Granite Bay Capital",
        "Corporate Strategy Intern",
        "greenhouse",
        location="New York, NY",
        verified=4,
        link="text",
        reuse_url_of="granite_strat",
        variant="tracking",
        outcome="duplicate",
        duplicate_of="granite_strat",
    ),
    _Spec(
        "alder_pm_dup",
        "Alder Systems",
        "Product Management Intern",
        "workday",
        verified=4,
        link="text",
        reuse_url_of="alder_pm",
        variant="workday_locale",
        outcome="duplicate",
        duplicate_of="alder_pm",
    ),
    _Spec(
        "nocompany",
        "",
        "Operations Intern",
        "greenhouse",
        location="Austin, TX",
        verified=5,
        blank_company=True,
        outcome="missing_fields",
    ),
)
# Layout: keys in sheet order, "|" separators are blank rows, "#..." are single-cell section rows.
_LAYOUT: tuple[str, ...] = (
    "#Product & Program Management",
    "alder_pm",
    "birch_apm",
    "cobalt_fin",
    "cobalt_tpm",
    "dunmore_pgm",
    "|",
    "#Consulting & Strategy",
    "elm_tc",
    "fern_tc",
    "granite_strat",
    "harbor_ops",
    "harbor_dev",
    "granite_adv",
    "iron_biz_indeed",
    "iron_biz",
    "iron_sc",
    "juniper_rev",
    "juniper_ins",
    "#Analytics & Business Analysis",
    "kestrel_ba",
    "kestrel_ba_linkedin",
    "kestrel_sys",
    "lake_bsa",
    "lake_senior",
    "alder_data",
    "alder_ft",
    "birch_pa",
    "birch_nourl",
    "elm_mkt",
    "dunmore_proc",
    "elm_da",
    "fern_po",
    "granite_strat_dup",
    "alder_pm_dup",
    "nocompany",
    "|",
    "#Source: student-maintained list. Verify each posting before applying.",
)


# ------------------------------------------------------------------------------------------------ helpers


def _title_slug(title: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in title).split()
    return "-".join(w.capitalize() for w in words)


def _has_path(base: str) -> bool:
    return urlsplit(base).path.strip("/") != ""


def _job_url(urls: SampleUrls, spec: _Spec, serial: int) -> tuple[str, str, str]:
    """(url, path appended to the site base, job id) of a spec's own posting."""
    if spec.site == "none":
        return "", "", ""
    if spec.site == "aggregator":
        if spec.aggregator == "linkedin":
            job = str(3_900_000_000 + serial)
            return f"https://www.linkedin.com/jobs/view/{job}", "", job
        job = uuid.uuid5(_UUID_NAMESPACE, f"indeed|{spec.company}|{spec.title}").hex[:16]
        return f"https://www.indeed.com/viewjob?jk={job}", "", job
    base = urls.for_site(spec.site).rstrip("/")
    company = slugify(spec.company)
    if spec.site == "workday":
        city = "-".join(p.strip().replace(" ", "-") for p in spec.location.split(","))
        job = f"R-{1000 + serial}"
        path = f"/en-US/External/job/{city}/{_title_slug(spec.title)}_{job}"
    elif spec.site == "greenhouse":
        job = str(4_000_000 + serial)
        path = f"/{company}/jobs/{job}"
    elif spec.site in ("lever", "ashby"):
        job = str(uuid.uuid5(_UUID_NAMESPACE, f"{spec.site}|{spec.company}|{spec.title}"))
        path = f"/{company}/{job}"
    else:  # portal / captcha / closed / sso: a single row each
        job = slugify(spec.title)
        if _has_path(urls.for_site(spec.site)):
            return base, "", job
        path = f"/careers/jobs/{job}" if spec.site == "portal" else f"/jobs/{job}"
    return base + path, path, job


def _variant(url: str, variant: str) -> str:
    if variant == "tracking":
        return url + "?gh_src=abc123&utm_source=newsletter"
    if variant == "workday_locale":
        head, _, tail = url.partition("/en-US")
        return head + tail + "/apply?source=LinkedIn"
    return url


def _date_text(value: date, style: str) -> str | int | datetime:
    if style == "us":
        return f"{value.month}/{value.day}/{value.year % 100:02d}"
    if style == "mdy":
        return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"
    if style == "dmy":
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    if style == "long":
        return f"{_MONTHS_LONG[value.month - 1]} {value.day}, {value.year}"
    if style == "iso":
        return value.isoformat()
    if style == "serial":
        return (value - date(1899, 12, 30)).days
    return datetime(value.year, value.month, value.day)


def _write_date(ws: Worksheet, row: int, col: int, value: date | None, style: str) -> None:
    if value is None:
        return
    cell = ws.cell(row=row, column=col, value=_date_text(value, style))
    if style in ("date", "date_us"):
        cell.number_format = "yyyy-mm-dd" if style == "date" else "mm/dd/yyyy"


def _write_link(ws: Worksheet, row: int, col: int, url: str, style: str) -> None:
    if not url or style == "none":
        return
    cell = ws.cell(row=row, column=col)
    if style == "text":
        cell.value = url
    elif style == "formula":
        cell.value = f'=HYPERLINK("{url}","Apply")'
    elif (
        style == "shown_url"
    ):  # the visible text is a different (short) URL; the hyperlink target wins
        cell.value = "https://short.example.test/apply"
        cell.hyperlink = url
    else:
        cell.value = "LinkedIn posting" if "linkedin.com" in url else "Apply"
        cell.hyperlink = url
    if style in ("hyperlink", "shown_url"):
        cell.font = Font(color="0563C1", underline="single")


# ------------------------------------------------------------------------------------------------ builder


def _summary_sheet(wb: Workbook, today: date) -> None:
    ws = wb.active
    assert isinstance(ws, Worksheet)
    ws.title = "Summary"
    ws["A1"] = "Summer 2027 internship tracker - summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:C1")
    for col, text in enumerate(("Metric", "Value", "Comment"), start=1):
        ws.cell(row=3, column=col, value=text).font = Font(bold=True)
    ws["A4"], ws["B4"] = "Rows listed", f"=COUNTA('{SAMPLE_SHEET}'!A5:A60)"
    ws["A5"], ws["B5"] = "Open roles", f"=COUNTIF('{SAMPLE_SHEET}'!F5:F60,\"Open\")"
    ws["A6"], ws["B6"] = "Last refreshed", datetime(today.year, today.month, today.day)
    ws["B6"].number_format = "yyyy-mm-dd"
    ws["A7"], ws["B7"] = "Maintainer", "sample data (fictional)"
    ws["C7"] = "Not a real list"
    ws.column_dimensions["A"].width = 28


def _rejected_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("Rejected")
    ws.append(["Company", "Role", "Link", "Reason"])
    ws.append(
        [
            "Alder Systems",
            "Product Management Intern",
            "https://rejected.example.test/1",
            "Requires citizenship",
        ]
    )
    ws.append(
        [
            "Cobalt Freight",
            "Program Management Intern",
            "https://rejected.example.test/2",
            "Onsite only",
        ]
    )
    ws.append(
        [
            "Dunmore Energy",
            "Business Analyst Intern",
            "https://rejected.example.test/3",
            "Duplicate of #14",
        ]
    )


def build_sample_workbook(
    path: Path | str, urls: SampleUrls | None = None, today: date | None = None
) -> SampleWorkbookInfo:
    """Write the sample workbook to ``path`` and describe what ingest must make of it.

    Sheets: ``Summary`` (junk, first), ``Verified Opportunities`` (a merged title row, a note and a blank row
    above the header on row 4, then ~36 rows: open Summer 2027 internships across all seven README role families
    spread over the logical mock sites, closed / Fall 2026 / Summer 2028 / stale / past-deadline rows, a senior
    and a new-grad role, a non-target ``Marketing Intern``, duplicate URLs that differ only in tracking
    parameters, the same role listed twice under different URLs (direct ATS vs LinkedIn / Indeed), blank, ragged,
    separator and footer rows, dates as Excel dates / serials / text, links as hyperlinks / plain text /
    ``=HYPERLINK()`` formulas) and ``Rejected`` (junk that must never be ingested).

    ``today`` (default ``SAMPLE_TODAY``, the ``FakeClock`` default day) anchors every relative date, so pass the
    same day to the clock / ``read_workbook``. ``urls`` decides which hosts the rows point at.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    site_urls = urls or SampleUrls()
    now = today or SAMPLE_TODAY

    wb = Workbook()
    wb.properties.creator = "sample"
    wb.properties.created = wb.properties.modified = datetime(2026, 9, 29, 12, 0)
    _summary_sheet(wb, now)
    ws: Worksheet = wb.create_sheet(SAMPLE_SHEET)
    ws["A1"] = "UT Austin - Verified Internship Opportunities (Summer 2027)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A1:L1")
    ws["A2"] = f"Last refreshed {now.isoformat()}. Fictional sample data."
    header_row = 4
    for col, text in enumerate(SAMPLE_HEADERS, start=1):
        ws.cell(row=header_row, column=col, value=text).font = Font(bold=True)
    for letter, width in zip(
        "ABCDEFGHIJKL", (22, 38, 18, 18, 16, 10, 14, 14, 14, 14, 30, 12), strict=True
    ):
        ws.column_dimensions[letter].width = width

    specs = {s.key: s for s in _SPECS}
    built: dict[str, tuple[str, str, str]] = {}  # key -> (url, path, job id)
    rows: list[SampleRow] = []
    ids: dict[str, str] = {}
    serial = 0
    row_no = header_row
    for item in _LAYOUT:
        row_no += 1
        if item == "|":
            continue
        if item.startswith("#"):
            ws.cell(row=row_no, column=1, value=item[1:]).font = Font(italic=True)
            continue
        spec = specs[item]
        serial += 1
        if spec.reuse_url_of:
            base_url, base_path, job = built[spec.reuse_url_of]
            url, path = (
                _variant(base_url, spec.variant),
                _variant(base_path, spec.variant) if base_path else "",
            )
        else:
            url, path, job = _job_url(site_urls, spec, serial)
        built[spec.key] = (url, path, job)
        posted = now - timedelta(days=spec.posted) if spec.posted is not None else None
        verified = now - timedelta(days=spec.verified) if spec.verified is not None else None
        deadline = now + timedelta(days=spec.deadline) if spec.deadline is not None else None
        values = [
            "" if spec.blank_company else spec.company,
            spec.title,
            None,
            spec.location,
            spec.term or None,
            spec.status or None,
            None,
            None,
            None,
            spec.ats_text or None,
            spec.notes or None,
            spec.pay or None,
        ]
        for col, value in enumerate(values, start=1):
            if value is not None and value != "" and not (spec.ragged and col > 4):
                ws.cell(row=row_no, column=col, value=value)
        _write_link(ws, row_no, 3, url, spec.link)
        if not spec.ragged:
            _write_date(ws, row_no, 7, posted, spec.style)
            _write_date(ws, row_no, 8, verified, spec.style)
            _write_date(ws, row_no, 9, deadline, spec.style)
        row_id = opportunity_id(url, spec.company, spec.title, spec.location) if url else ""
        ids[spec.key] = row_id
        rows.append(
            SampleRow(
                key=spec.key,
                row_number=row_no,
                company=spec.company,
                title=spec.title,
                location=spec.location,
                site=spec.site,
                url=url,
                path=path,
                job_id=job,
                id=row_id,
                outcome=spec.outcome,
                family=spec.family,
                ats=_SITE_ATS.get(spec.site, ATS.UNKNOWN),
                term=spec.term,
                status=spec.status,
                last_verified=verified,
                posted=posted,
                deadline=deadline,
                duplicate_of=spec.duplicate_of,
                flags=spec.flags,
            )
        )
    rows = [replace(r, duplicate_of=ids[r.duplicate_of]) if r.duplicate_of else r for r in rows]
    _rejected_sheet(wb)
    wb.save(target)
    return SampleWorkbookInfo(
        path=target,
        today=now,
        urls=site_urls,
        sheet=SAMPLE_SHEET,
        header_row=header_row,
        headers=SAMPLE_HEADERS,
        target_term=SAMPLE_TARGET_TERM,
        rows=tuple(rows),
    )
