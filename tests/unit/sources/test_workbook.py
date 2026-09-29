"""Reading real .xlsx files: layouts, hyperlinks, formulas, dates, filters, sheet selection, failure paths."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904
from openpyxl.worksheet.worksheet import Worksheet

from autoapply.config import AppConfig, WorkbookConfig
from autoapply.models import ATS, OpportunitySource, SearchProfile
from autoapply.sources.workbook import (
    Cell,
    RejectReason,
    SheetGrid,
    WorkbookError,
    WorkbookParseResult,
    detect_header,
    parse_sheet,
    read_workbook,
)

TODAY = date(2026, 9, 29)
SHEET = "Verified Opportunities"
HEADER = ["Company", "Role", "Link", "Location", "Term", "Status", "Last Verified"]


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def good_row(n: int = 1, **over: object) -> list[object]:
    values: dict[str, object] = {
        "Company": "Acme",
        "Role": f"Product Intern {n}",
        "Link": f"https://jobs.acme.example/{n}",
        "Location": "Austin, TX",
        "Term": "Summer 2027",
        "Status": "Open",
        "Last Verified": ago(5),
    }
    values.update(over)
    return [values[h] for h in HEADER]


class Book:
    """Tiny builder around openpyxl."""

    def __init__(self) -> None:
        self.wb = Workbook()
        first = self.wb.active
        assert first is not None
        self.wb.remove(first)

    def sheet(
        self, name: str, rows: Sequence[Sequence[object]] = (), *, state: str = "visible"
    ) -> Worksheet:
        ws: Worksheet = self.wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
        ws.sheet_state = state  # type: ignore[assignment]
        return ws

    def save(self, path: Path) -> Path:
        self.wb.save(path)
        return path


def make(
    tmp_path: Path,
    rows: Sequence[Sequence[object]],
    *,
    header: Sequence[object] = HEADER,
    name: str = "book.xlsx",
    sheet: str = SHEET,
) -> Path:
    book = Book()
    book.sheet(sheet, [header, *rows])
    return book.save(tmp_path / name)


def config(**search: object) -> AppConfig:
    return AppConfig(search=SearchProfile(**search))  # type: ignore[arg-type]


def parse(path: Path, cfg: AppConfig | None = None, **kwargs: object) -> WorkbookParseResult:
    return read_workbook(path, cfg or AppConfig(), today=TODAY, **kwargs)  # type: ignore[arg-type]


def inject_cached_values(path: Path, sheet_file: str, values: dict[str, str]) -> None:
    """Give formula cells cached string results, as Excel does (openpyxl writes formulas without them)."""
    with zipfile.ZipFile(path) as src:
        parts = {name: src.read(name) for name in src.namelist()}
    xml = parts[sheet_file].decode("utf-8")
    for ref, text in values.items():
        pattern = re.compile(rf'<c r="{ref}"([^>]*)><f>(.*?)</f><v\s*/></c>', re.S)
        xml, count = pattern.subn(
            lambda m, t=text, r=ref: f'<c r="{r}"{m[1]} t="str"><f>{m[2]}</f><v>{t}</v></c>', xml
        )
        assert count == 1, ref
    parts[sheet_file] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in parts.items():
            out.writestr(name, data)


def titles(result: WorkbookParseResult) -> list[str]:
    return [o.title for o in result.opportunities]


# --------------------------------------------------------------------------------------------- basics


def test_reads_a_minimal_sheet(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [good_row(1)]))
    (opp,) = result.opportunities
    assert (opp.company, opp.title, opp.url) == (
        "Acme",
        "Product Intern 1",
        "https://jobs.acme.example/1",
    )
    assert opp.apply_url is None
    assert opp.location == "Austin, TX"
    assert opp.term == "Summer 2027"
    assert opp.source == OpportunitySource.WORKBOOK
    assert opp.is_open is True
    assert opp.last_verified == ago(5)
    assert opp.posted_date is None and opp.deadline is None
    assert opp.extra["sheet"] == SHEET and opp.extra["sheet_row"] == 2
    assert "term_assumed" not in opp.extra and "date_unknown" not in opp.extra
    assert result.sheet == SHEET and result.header_row == 1
    assert result.mapping["company"] == "Company"
    assert result.rejections == [] and result.data_rows == 1 and result.junk_rows == 0


@pytest.mark.parametrize(
    "header",
    [
        ["Employer", "Position", "URL", "City", "Season", "Open?", "Verified On"],
        [
            "ORGANIZATION",
            "job title",
            "apply link",
            "location",
            "internship term",
            "state",
            "date verified",
        ],
        [
            "Company:",
            "Role:",
            "Application URL:",
            "Location:",
            "Cohort:",
            "Open/Closed:",
            "Last Verified:",
        ],
        [
            "Company Name",
            "Job-Title",
            "Job Link",
            "Locations",
            "Term / Season",
            "Status?",
            "Last Checked",
        ],
    ],
)
def test_alias_headers_produce_the_same_result(tmp_path: Path, header: list[str]) -> None:
    result = parse(make(tmp_path, [good_row(1)], header=header))
    (opp,) = result.opportunities
    assert (opp.company, opp.title, opp.url, opp.location) == (
        "Acme",
        "Product Intern 1",
        "https://jobs.acme.example/1",
        "Austin, TX",
    )
    assert opp.last_verified == ago(5)


def test_columns_may_come_in_any_order_and_have_gaps(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(SHEET)
    ws["C3"], ws["E3"], ws["G3"], ws["H3"] = "Role", "Company", "Link", "Term"
    ws["C4"], ws["E4"], ws["G4"], ws["H4"] = (
        "Product Intern",
        "Acme",
        "https://jobs.acme.example/1",
        "Summer 2027",
    )
    result = parse(book.save(tmp_path / "gaps.xlsx"))
    (opp,) = result.opportunities
    assert (opp.company, opp.title, opp.url) == (
        "Acme",
        "Product Intern",
        "https://jobs.acme.example/1",
    )
    assert result.header_row == 3


def test_header_row_below_a_merged_title_and_notes(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET,
        [
            ["UT Austin - Verified Internship Opportunities (Summer 2027)"],
            ["Last refreshed 2026-09-28"],
            [],
            HEADER,
            good_row(1),
        ],
    )
    ws.merge_cells("A1:G1")
    result = parse(book.save(tmp_path / "title.xlsx"))
    assert result.header_row == 4
    assert titles(result) == ["Product Intern 1"]


def test_workbook_without_a_header_row_fails_with_a_helpful_message(tmp_path: Path) -> None:
    path = make(tmp_path, [good_row(1)], header=["Foo", "Bar", "Baz", "Qux", "Company", "Role"])
    with pytest.raises(WorkbookError, match=r"no header row found.*first 15 rows"):
        parse(path)


def test_apply_and_posting_links_are_kept_apart(tmp_path: Path) -> None:
    header = ["Company", "Role", "Job Link", "Apply Link", "Term"]
    rows = [
        [
            "Acme",
            "Product Intern",
            "https://acme.example/jobs/1",
            "https://jobs.lever.co/acme/1111/apply",
            "Summer 2027",
        ],
        [
            "Acme",
            "Analyst Intern",
            "https://acme.example/jobs/2",
            "https://acme.example/jobs/2",
            "Summer 2027",
        ],
        ["Acme", "Strategy Intern", None, "https://acme.example/jobs/3", "Summer 2027"],
    ]
    result = parse(make(tmp_path, rows, header=header))
    first, same, apply_only = result.opportunities
    assert (first.url, first.apply_url) == (
        "https://acme.example/jobs/1",
        "https://jobs.lever.co/acme/1111/apply",
    )
    assert first.start_url == "https://jobs.lever.co/acme/1111/apply"
    assert first.ats == ATS.LEVER
    assert (same.url, same.apply_url) == (
        "https://acme.example/jobs/2",
        None,
    )  # identical links: no apply_url
    assert (apply_only.url, apply_only.apply_url) == ("https://acme.example/jobs/3", None)


# --------------------------------------------------------------------------------------------- hyperlinks


def test_hyperlink_target_beats_display_text(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET,
        [HEADER, good_row(1, Link="Apply"), good_row(2, Link="https://display.example/shown")],
    )
    ws["C2"].hyperlink = "https://jobs.real.example/1?utm_source=sheet"
    ws["C3"].hyperlink = "https://jobs.real.example/2"
    result = parse(book.save(tmp_path / "links.xlsx"))
    assert [o.url for o in result.opportunities] == [
        "https://jobs.real.example/1?utm_source=sheet",
        "https://jobs.real.example/2",
    ]


def test_display_text_is_used_when_there_is_no_hyperlink(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [good_row(1, Link="https://jobs.acme.example/plain")]))
    assert result.opportunities[0].url == "https://jobs.acme.example/plain"


def test_internal_and_mail_hyperlinks_are_ignored(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET,
        [
            HEADER,
            good_row(1, Link="See tab"),
            good_row(2, Link="Email us"),
            good_row(3, Link="https://jobs.acme.example/3"),
        ],
    )
    ws["C2"].hyperlink = "#'Other Tab'!A1"
    ws["C3"].hyperlink = "mailto:hr@acme.example"
    result = parse(book.save(tmp_path / "links.xlsx"))
    assert titles(result) == ["Product Intern 3"]
    assert [(r.label, r.reason) for r in result.rejections] == [
        ("Product Intern 1", RejectReason.NO_URL),
        ("Product Intern 2", RejectReason.NO_URL),
    ]


def test_hyperlink_formula_without_a_cached_value(tmp_path: Path) -> None:
    """openpyxl writes formulas without results, so ``data_only`` sees nothing: the formula text is used."""
    book = Book()
    book.sheet(
        SHEET,
        [
            HEADER,
            good_row(1, Link='=HYPERLINK("https://jobs.formula.example/1","Apply")'),
            good_row(2, Link='=HYPERLINK("https://jobs.formula.example/"&"2")'),
        ],
    )
    result = parse(book.save(tmp_path / "formula.xlsx"))
    assert [o.url for o in result.opportunities] == [
        "https://jobs.formula.example/1",
        "https://jobs.formula.example/2",
    ]


def test_hyperlink_formula_with_a_cached_value(tmp_path: Path) -> None:
    book = Book()
    book.sheet(
        SHEET, [HEADER, good_row(1, Link='=HYPERLINK("https://jobs.formula.example/1","Apply")')]
    )
    path = book.save(tmp_path / "formula.xlsx")
    inject_cached_values(path, "xl/worksheets/sheet1.xml", {"C2": "Apply"})
    (opp,) = parse(path).opportunities
    assert (
        opp.url == "https://jobs.formula.example/1"
    )  # the cached text "Apply" is not a URL, the formula is


def test_hyperlink_formula_may_reference_other_cells(tmp_path: Path) -> None:
    header = [*HEADER, "Job ID"]
    book = Book()
    book.sheet(
        SHEET,
        [
            header,
            [*good_row(1, Link='=HYPERLINK("https://jobs.formula.example/"&H2,"Apply")'), "4711"],
        ],
    )
    (opp,) = parse(book.save(tmp_path / "ref.xlsx")).opportunities
    assert opp.url == "https://jobs.formula.example/4711"
    assert opp.extra["Job ID"] == "4711"


def test_formulas_with_cached_values_are_read_from_the_cache(tmp_path: Path) -> None:
    book = Book()
    book.sheet(
        SHEET, [HEADER, good_row(1, Role='=B1&" Intern"'), good_row(2, Role='=B1&" Intern"')]
    )
    path = book.save(tmp_path / "cached.xlsx")
    inject_cached_values(
        path, "xl/worksheets/sheet1.xml", {"B2": "Role Intern", "B3": "Role Intern"}
    )
    result = parse(path)
    assert titles(result) == ["Role Intern", "Role Intern"]


def test_formula_cells_without_cached_values_count_as_empty(tmp_path: Path) -> None:
    book = Book()
    book.sheet(SHEET, [HEADER, good_row(1, Role='=B1&" Intern"')])
    result = parse(book.save(tmp_path / "nocache.xlsx"))
    assert result.opportunities == []
    assert [(r.reason, r.detail) for r in result.rejections] == [
        (RejectReason.MISSING_FIELDS, "no title")
    ]


def test_a_title_hyperlink_is_the_fallback_url(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(SHEET, [HEADER, good_row(1, Link=None)])
    ws["B2"].hyperlink = "https://jobs.title.example/1"
    (opp,) = parse(book.save(tmp_path / "title.xlsx")).opportunities
    assert opp.url == "https://jobs.title.example/1"


def test_a_company_hyperlink_is_never_the_job_url(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(SHEET, [HEADER, good_row(1, Link=None)])
    ws["A2"].hyperlink = "https://www.acme.example/"  # the employer's home page
    result = parse(book.save(tmp_path / "company.xlsx"))
    assert result.opportunities == []
    assert result.rejections[0].reason == RejectReason.NO_URL


def test_a_link_in_an_unrecognised_column_is_the_last_resort(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(SHEET, [[*HEADER, "Go"], [*good_row(1, Link=None), "click"]])
    ws["H2"].hyperlink = "https://jobs.stray.example/1"
    (opp,) = parse(book.save(tmp_path / "stray.xlsx")).opportunities
    assert opp.url == "https://jobs.stray.example/1"


def test_streaming_reader_has_no_hyperlinks_but_still_reads_text(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET, [HEADER, good_row(1, Link="Apply"), good_row(2, Link="https://jobs.acme.example/2")]
    )
    ws["C2"].hyperlink = "https://jobs.real.example/1"
    path = book.save(tmp_path / "stream.xlsx")
    normal = parse(path)
    streamed = parse(path, streaming=True)
    assert [o.url for o in normal.opportunities] == [
        "https://jobs.real.example/1",
        "https://jobs.acme.example/2",
    ]
    assert [o.url for o in streamed.opportunities] == ["https://jobs.acme.example/2"]
    assert streamed.rejections[0].reason == RejectReason.NO_URL


def test_urls_in_messy_cells(tmp_path: Path) -> None:
    rows = [
        good_row(1, Link="<https://jobs.acme.example/1>"),
        good_row(2, Link="www.acme.example/careers/2"),
        good_row(3, Link="Apply at https://jobs.acme.example/3."),
        good_row(4, Link="jobs.acme.example/careers/4"),
        good_row(5, Link="https://jobs.acme.example/5 https://backup.example/5"),
    ]
    urls = [o.url for o in parse(make(tmp_path, rows)).opportunities]
    assert urls == [
        "https://jobs.acme.example/1",
        "https://www.acme.example/careers/2",
        "https://jobs.acme.example/3",
        "https://jobs.acme.example/careers/4",
        "https://jobs.acme.example/5",
    ]


# --------------------------------------------------------------------------------------------- dates


def test_dates_in_every_style_in_one_column(tmp_path: Path) -> None:
    target = ago(9)  # 2026-09-20
    excel_serial = (target - date(1899, 12, 30)).days
    values = [
        target,  # a real Excel date
        excel_serial,  # serial number in a General cell
        target.isoformat(),
        f"{target.month}/{target.day}/{target.year % 100}",
        "Sep 20, 2026",
        "20 Sep 2026",
        "September 20th, 2026",
    ]
    rows = [good_row(i, **{"Last Verified": v}) for i, v in enumerate(values, start=1)]
    result = parse(make(tmp_path, rows))
    assert [o.last_verified for o in result.opportunities] == [target] * len(values)


def test_1904_date_system_serials(tmp_path: Path) -> None:
    book = Book()
    book.wb.epoch = CALENDAR_MAC_1904
    book.sheet(
        SHEET, [HEADER, good_row(1, **{"Last Verified": 44800})]
    )  # 2026-08-28 in the 1904 system
    (opp,) = parse(book.save(tmp_path / "mac.xlsx")).opportunities
    assert opp.last_verified == date(2026, 8, 28)


def test_day_first_sheets_are_detected_from_unambiguous_dates(tmp_path: Path) -> None:
    rows = [
        good_row(1, **{"Last Verified": "25/09/2026"}),  # proves day-first
        good_row(2, **{"Last Verified": "01/09/2026"}),  # ambiguous, so also 1 September
    ]
    result = parse(make(tmp_path, rows))
    assert [o.last_verified for o in result.opportunities] == [date(2026, 9, 25), date(2026, 9, 1)]


def test_us_sheets_default_to_month_first(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [good_row(1, **{"Last Verified": "9/1/26"})]))
    assert result.opportunities[0].last_verified == date(2026, 9, 1)


def test_posted_and_deadline_columns(tmp_path: Path) -> None:
    header = [*HEADER, "Date Posted", "Deadline"]
    rows = [[*good_row(1), ago(20), TODAY + timedelta(days=30)]]
    (opp,) = parse(make(tmp_path, rows, header=header)).opportunities
    assert opp.posted_date == ago(20)
    assert opp.deadline == TODAY + timedelta(days=30)


def test_unparseable_dates_are_kept_verbatim_in_extra(tmp_path: Path) -> None:
    header = [*HEADER, "Posted", "Deadline"]
    rows = [[*good_row(1, **{"Last Verified": "recently"}), "a while back", "Rolling"]]
    (opp,) = parse(make(tmp_path, rows, header=header)).opportunities
    assert opp.last_verified is None and opp.posted_date is None and opp.deadline is None
    assert opp.extra["last_verified_raw"] == "recently"
    assert opp.extra["posted_raw"] == "a while back"
    assert opp.extra["deadline_raw"] == "Rolling"
    assert opp.extra["date_unknown"] is True


# --------------------------------------------------------------------------------------------- filters


@pytest.mark.parametrize(
    ("status", "kept"),
    [
        ("Open", True),
        ("Active", True),
        ("Verified", True),
        ("Yes", True),
        ("Y", True),
        (True, True),
        ("Live", True),
        ("Rolling", True),
        (None, True),  # blank: no evidence
        ("TBD", True),
        ("Closed", False),
        ("Filled", False),
        ("Expired", False),
        ("Inactive", False),
        ("No", False),
        (False, False),
        ("No longer accepting", False),
        ("Not open", False),
        ("Applied", False),
    ],
)
def test_status_vocabulary(tmp_path: Path, status: object, kept: bool) -> None:
    result = parse(make(tmp_path, [good_row(1, Status=status)]))
    assert bool(result.opportunities) is kept
    if not kept:
        expected = RejectReason.ALREADY_APPLIED if status == "Applied" else RejectReason.CLOSED
        assert result.rejections[0].reason == expected


def test_closed_flag_columns_are_inverted(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Term", "Closed?"]
    rows = [
        ["Acme", "Open Intern", "https://acme.example/1", "Summer 2027", "No"],
        ["Acme", "Shut Intern", "https://acme.example/2", "Summer 2027", "Yes"],
        ["Acme", "Blank Intern", "https://acme.example/3", "Summer 2027", None],
        ["Acme", "Bool Intern", "https://acme.example/4", "Summer 2027", True],
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert titles(result) == ["Open Intern", "Blank Intern"]
    assert [r.label for r in result.rejections] == ["Shut Intern", "Bool Intern"]


def test_any_closed_status_column_wins(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Term", "Status", "Open?"]
    rows = [["Acme", "Mixed Intern", "https://acme.example/1", "Summer 2027", "Open", "No"]]
    result = parse(make(tmp_path, rows, header=header))
    assert result.rejections[0].reason == RejectReason.CLOSED


def test_rows_below_a_closed_section_are_closed_until_an_open_section(tmp_path: Path) -> None:
    rows = [
        good_row(1),
        ["Closed / Archived"],
        good_row(2, Status=None),  # inherits "closed" from the section
        good_row(3, Status="Open"),  # its own status wins
        ["Open roles"],
        good_row(4, Status=None),
    ]
    result = parse(make(tmp_path, rows))
    assert titles(result) == ["Product Intern 1", "Product Intern 3", "Product Intern 4"]
    assert result.rejections[0].label == "Product Intern 2"
    assert result.rejections[0].reason == RejectReason.CLOSED
    assert result.junk_rows == 2


@pytest.mark.parametrize(
    ("verified_days_ago", "kept"),
    [(0, True), (30, True), (45, True), (46, False), (90, False), (-10, True)],
)
def test_recent_window_is_inclusive(tmp_path: Path, verified_days_ago: int, kept: bool) -> None:
    result = parse(make(tmp_path, [good_row(1, **{"Last Verified": ago(verified_days_ago)})]))
    assert bool(result.opportunities) is kept
    if not kept:
        assert result.rejections[0].reason == RejectReason.STALE


def test_recent_days_follows_the_search_profile(tmp_path: Path) -> None:
    path = make(tmp_path, [good_row(1, **{"Last Verified": ago(20)})])
    assert len(parse(path, config(recent_days=30)).opportunities) == 1
    assert len(parse(path, config(recent_days=10)).opportunities) == 0


def test_freshness_uses_the_newer_of_verified_and_posted(tmp_path: Path) -> None:
    header = [*HEADER, "Posted"]
    rows = [
        [*good_row(1, **{"Last Verified": ago(100)}), ago(3)],  # posted recently
        [*good_row(2, **{"Last Verified": ago(3)}), ago(300)],  # re-verified recently
        [*good_row(3, **{"Last Verified": ago(100)}), ago(200)],  # stale on both counts
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert titles(result) == ["Product Intern 1", "Product Intern 2"]


def test_posted_alone_can_make_a_row_stale(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Term", "Date Posted"]
    rows = [["Acme", "Old Intern", "https://acme.example/1", "Summer 2027", ago(120)]]
    result = parse(make(tmp_path, rows, header=header))
    assert result.rejections[0].reason == RejectReason.STALE


def test_rows_without_any_date_are_kept_and_flagged(tmp_path: Path) -> None:
    (opp,) = parse(make(tmp_path, [good_row(1, **{"Last Verified": None})])).opportunities
    assert opp.extra["date_unknown"] is True
    assert opp.last_verified is None


@pytest.mark.parametrize(("offset", "kept"), [(-1, False), (0, True), (1, True), (400, True)])
def test_deadline_must_not_be_in_the_past(tmp_path: Path, offset: int, kept: bool) -> None:
    header = [*HEADER, "Deadline"]
    result = parse(make(tmp_path, [[*good_row(1), TODAY + timedelta(days=offset)]], header=header))
    assert bool(result.opportunities) is kept
    if not kept:
        assert result.rejections[0].reason == RejectReason.DEADLINE_PASSED


def test_year_less_deadlines_look_ahead(tmp_path: Path) -> None:
    header = [*HEADER, "Deadline"]
    rows = [[*good_row(1), "Oct 15"], [*good_row(2), "Jan 15"], [*good_row(3), "Sep 1"]]
    result = parse(make(tmp_path, rows, header=header))
    assert titles(result) == ["Product Intern 1", "Product Intern 2"]
    assert (
        result.rejections[0].label == "Product Intern 3"
    )  # Sep 1 was four weeks ago... wait: 28 days is inside the grace


@pytest.mark.parametrize(
    ("term", "title", "expected"),
    [
        ("Summer 2027", "Product Intern", "kept"),
        ("summer '27", "Product Intern", "kept"),
        ("Sum 2027", "Product Intern", "kept"),
        ("2027 Summer", "Product Intern", "kept"),
        ("Fall 2026", "Product Intern", RejectReason.WRONG_TERM),
        ("Summer 2028", "Product Intern", RejectReason.WRONG_TERM),
        ("Spring 2027", "Product Intern", RejectReason.WRONG_TERM),
        ("", "Product Intern (Summer 2027)", "kept"),
        ("", "Product Intern (Fall 2026)", RejectReason.WRONG_TERM),
        ("", "Product Intern", "assumed"),
        ("Summer", "Product Intern", "assumed"),
        ("Rolling", "Product Intern", "assumed"),
    ],
)
def test_term_filter(tmp_path: Path, term: str, title: str, expected: object) -> None:
    result = parse(make(tmp_path, [good_row(1, Term=term or None, Role=title)]))
    if isinstance(expected, RejectReason):
        assert result.opportunities == []
        assert result.rejections[0].reason == expected
        return
    (opp,) = result.opportunities
    assert opp.term == "Summer 2027"  # the canonical target term, whatever the cell said
    assert bool(opp.extra.get("term_assumed")) is (expected == "assumed")
    if term:
        assert opp.extra["term_raw"] == term


def test_a_sheet_without_a_term_column_uses_the_title_or_assumes(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Status"]
    rows = [
        ["Acme", "Product Intern - Summer 2027", "https://acme.example/1", "Open"],
        ["Acme", "Product Intern - Fall 2026", "https://acme.example/2", "Open"],
        ["Acme", "Product Intern", "https://acme.example/3", "Open"],
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert titles(result) == ["Product Intern - Summer 2027", "Product Intern"]
    assert "term_assumed" not in result.opportunities[0].extra
    assert result.opportunities[1].extra["term_assumed"] is True
    assert result.rejections[0].reason == RejectReason.WRONG_TERM


def test_notes_can_state_the_term_but_application_windows_do_not_count(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Notes"]
    rows = [
        ["Acme", "A Intern", "https://acme.example/1", "Applications open Fall 2026"],
        ["Acme", "B Intern", "https://acme.example/2", "Fall 2026 cohort"],
        ["Acme", "C Intern", "https://acme.example/3", "Summer 2027 cohort"],
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert titles(result) == ["A Intern", "C Intern"]
    assert result.opportunities[0].extra["term_assumed"] is True
    assert result.rejections[0].label == "B Intern"


def test_target_term_comes_from_the_search_profile(tmp_path: Path) -> None:
    path = make(tmp_path, [good_row(1, Term="Fall 2026"), good_row(2, Term="Summer 2027")])
    result = parse(path, config(target_term="Fall 2026"))
    assert titles(result) == ["Product Intern 1"]
    assert result.opportunities[0].term == "Fall 2026"


@pytest.mark.parametrize(
    ("title", "term", "kept"),
    [
        ("Product Management Intern", "Summer 2027", True),
        ("Summer Analyst", "Summer 2027", True),
        ("Product Manager", "Summer 2027", True),  # no evidence against: keep
        ("Senior Product Manager", "Summer 2027", False),
        ("Sr. Business Analyst", "Summer 2027", False),
        ("Director of Strategy", "Summer 2027", False),
        ("Business Analyst II", "Summer 2027", False),
        ("Business Analyst (Full-Time)", "Summer 2027", False),
        ("Business Analyst - New Grad", "Summer 2027", False),
        ("Senior Intern", "Summer 2027", True),
    ],
)
def test_internship_filter(tmp_path: Path, title: str, term: str, kept: bool) -> None:
    result = parse(make(tmp_path, [good_row(1, Role=title, Term=term)]))
    assert bool(result.opportunities) is kept
    if not kept:
        assert result.rejections[0].reason == RejectReason.NOT_INTERNSHIP


def test_type_column_and_config_keywords_feed_the_internship_filter(tmp_path: Path) -> None:
    header = [*HEADER, "Job Type"]
    rows = [
        [*good_row(1, Role="Business Analyst"), "Internship"],
        [*good_row(2, Role="Business Analyst"), "Full-time"],
        [*good_row(3, Role="Program Coordinator"), ""],
    ]
    path = make(tmp_path, rows, header=header)
    assert titles(parse(path)) == ["Business Analyst", "Program Coordinator"]
    strict = parse(path, config(exclude_title_keywords=["coordinator"]))
    assert titles(strict) == ["Business Analyst"]


def test_every_rejection_records_a_primary_reason_and_the_others(tmp_path: Path) -> None:
    header = [*HEADER, "Deadline"]
    row = good_row(1, Status="Closed", Term="Fall 2026", **{"Last Verified": ago(200)})
    result = parse(make(tmp_path, [[*row, ago(5)]], header=header))
    (rej,) = result.rejections
    assert rej.reason == RejectReason.CLOSED
    assert rej.also == (RejectReason.DEADLINE_PASSED, RejectReason.WRONG_TERM, RejectReason.STALE)


def test_rejection_summary_groups_titles_by_reason(tmp_path: Path) -> None:
    rows = [
        good_row(1),
        good_row(2, Status="Closed"),
        good_row(3, Status="Filled"),
        good_row(4, Term="Fall 2026"),
        good_row(5, Link=None),
        [None, "Orphan Intern", "https://acme.example/6", "Austin", "Summer 2027", "Open", ago(2)],
    ]
    result = parse(make(tmp_path, rows))
    assert result.rejection_summary() == {
        "closed": ["Product Intern 2", "Product Intern 3"],
        "wrong_term": ["Product Intern 4"],
        "no_url": ["Product Intern 5"],
        "missing_fields": ["Orphan Intern"],
    }
    assert result.rejection_counts() == {
        "closed": 2,
        "wrong_term": 1,
        "no_url": 1,
        "missing_fields": 1,
    }


# --------------------------------------------------------------------------------------------- messy layouts


def test_blank_ragged_and_separator_rows(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET,
        [
            HEADER,
            good_row(1),
            [],
            [None, None, None],
            ["   ", "\u00a0", None],
            ["N/A", "-", "TBD"],
            ["Section heading"],
            ["Acme", "Short Intern", "https://acme.example/short"],  # ragged: 3 of 7 cells
            good_row(2) + ["stray cell beyond the header", "another"],
            [],
        ],
    )
    ws.row_dimensions[3].hidden = True
    result = parse(book.save(tmp_path / "ragged.xlsx"))
    assert titles(result) == ["Product Intern 1", "Short Intern", "Product Intern 2"]
    short = result.opportunities[1]
    assert short.location is None and short.term == "Summer 2027"
    assert short.extra["term_assumed"] is True and short.extra["date_unknown"] is True
    assert result.rejections == []
    # empty row, blank cells, whitespace cells, placeholders row, section heading (trailing empty rows are trimmed)
    assert result.junk_rows == 5


def test_repeated_header_rows_are_ignored(tmp_path: Path) -> None:
    result = parse(
        make(tmp_path, [good_row(1), HEADER, good_row(2), ["Employer", "Position", "URL"]])
    )
    assert titles(result) == ["Product Intern 1", "Product Intern 2"]
    assert result.rejections == [] and result.junk_rows == 2


def test_duplicate_column_names_fall_back_left_to_right(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Term", "Notes", "Notes"]
    rows = [
        ["Acme", "A Intern", "https://acme.example/1", "Summer 2027", "first note", "second note"]
    ]
    (opp,) = parse(make(tmp_path, rows, header=header)).opportunities
    assert opp.extra["notes"] == "first note second note"
    assert opp.description == "first note second note"


def test_duplicate_url_columns_use_the_first_usable_link(tmp_path: Path) -> None:
    header = ["Company", "Role", "Link", "Link", "Term"]
    rows = [
        ["Acme", "A Intern", None, "https://acme.example/second", "Summer 2027"],
        [
            "Acme",
            "B Intern",
            "https://acme.example/first",
            "https://acme.example/second",
            "Summer 2027",
        ],
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert [o.url for o in result.opportunities] == [
        "https://acme.example/second",
        "https://acme.example/first",
    ]


def test_merged_company_cells_are_filled_down(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET,
        [
            HEADER,
            ["Acme", "A Intern", "https://acme.example/1", "Austin", "Summer 2027", "Open", ago(3)],
            [None, "B Intern", "https://acme.example/2", "Austin", "Summer 2027", "Open", ago(3)],
            [None, "C Intern", "https://acme.example/3", "Austin", "Summer 2027", "Open", ago(3)],
            [
                "Globex",
                "D Intern",
                "https://globex.example/4",
                "Austin",
                "Summer 2027",
                "Open",
                ago(3),
            ],
        ],
    )
    ws.merge_cells("A2:A4")
    path = book.save(tmp_path / "merged.xlsx")
    result = parse(path)
    assert [(o.company, o.title) for o in result.opportunities] == [
        ("Acme", "A Intern"),
        ("Acme", "B Intern"),
        ("Acme", "C Intern"),
        ("Globex", "D Intern"),
    ]
    streamed = parse(path, streaming=True)  # the streaming reader cannot see merges
    assert [o.title for o in streamed.opportunities] == ["A Intern", "D Intern"]
    assert [r.reason for r in streamed.rejections] == [RejectReason.MISSING_FIELDS] * 2


def test_a_horizontally_merged_note_row_is_not_data(tmp_path: Path) -> None:
    book = Book()
    ws = book.sheet(
        SHEET, [HEADER, good_row(1), ["This whole row is one merged note"], good_row(2)]
    )
    ws.merge_cells("A3:G3")
    result = parse(book.save(tmp_path / "hmerge.xlsx"))
    assert titles(result) == ["Product Intern 1", "Product Intern 2"]
    assert result.rejections == []


def test_unknown_columns_go_to_extra_with_unique_keys(tmp_path: Path) -> None:
    header = [*HEADER, "Pay", "Pay", "sheet", "Notes"]
    rows = [[*good_row(1), "$30/hr", "$1200/wk", "tab 3", "referral"]]
    (opp,) = parse(make(tmp_path, rows, header=header)).opportunities
    assert opp.extra["Pay"] == "$30/hr"
    assert opp.extra["Pay (2)"] == "$1200/wk"
    assert opp.extra["sheet (column)"] == "tab 3"  # "sheet" would collide with the provenance key
    assert opp.extra["sheet"] == SHEET
    assert opp.extra["notes"] == "referral"


def test_section_titles_are_recorded_in_extra(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [["Product"], good_row(1), ["Strategy"], good_row(2)]))
    assert [o.extra["section"] for o in result.opportunities] == ["Product", "Strategy"]


def test_description_notes_and_location_placeholders(tmp_path: Path) -> None:
    header = [*HEADER, "Description", "Notes"]
    rows = [
        [*good_row(1, Location="N/A"), "Line one\nline two", "a note"],
        [*good_row(2), None, "only a note"],
        [*good_row(3), None, None],
    ]
    first, second, third = parse(make(tmp_path, rows, header=header)).opportunities
    assert first.location is None
    assert first.description == "Line one\nline two"
    assert first.extra["notes"] == "a note"
    assert second.description == "only a note"
    assert third.description is None and "notes" not in third.extra


def test_long_cells_are_truncated_not_fatal(tmp_path: Path) -> None:
    header = [*HEADER, "Notes", "Pay"]
    long_title = "Product Intern " + "x" * 2000
    rows = [
        [*good_row(1, Role=long_title), "n" * 30_000, "p" * 5000],
        good_row(2, Link="https://acme.example/" + "x" * 5000),
    ]
    result = parse(make(tmp_path, rows, header=header))
    (opp,) = result.opportunities
    assert len(opp.title) == 300 and opp.title.endswith("\u2026")
    assert opp.description is not None and len(opp.description) == 20_000
    assert len(opp.extra["notes"]) == 1000
    assert len(opp.extra["Pay"]) == 1000
    assert result.rejections[0].reason == RejectReason.NO_URL  # an absurdly long URL is not usable


def test_unicode_and_odd_whitespace_survive(tmp_path: Path) -> None:
    rows = [
        good_row(
            1,
            Company="  Caf\u00e9 \u200bM\u00fcller\u00a0GmbH ",
            Role="Analyst\u2013Intern\n(M/F/D)",
            Location="M\u00fcnchen",
        )
    ]
    (opp,) = parse(make(tmp_path, rows)).opportunities
    assert opp.company == "Caf\u00e9 M\u00fcller GmbH"
    assert opp.title == "Analyst\u2013Intern (M/F/D)"
    assert opp.location == "M\u00fcnchen"


def test_numbers_in_text_columns(tmp_path: Path) -> None:
    (opp,) = parse(
        make(tmp_path, [good_row(1, Company=3, Location=78701, Term=2027.0)])
    ).opportunities
    assert opp.company == "3" and opp.location == "78701"
    assert opp.extra["term_assumed"] is True  # a bare year is only partial evidence


# --------------------------------------------------------------------------------------------- ATS


def test_ats_comes_from_the_url_then_from_the_ats_column(tmp_path: Path) -> None:
    header = [*HEADER, "ATS"]
    rows = [
        [
            *good_row(1, Link="https://acme.wd5.myworkdayjobs.com/en-US/External/job/x_R1"),
            "Lever",
        ],  # the URL wins
        [
            *good_row(2, Link="https://careers.wellsfargo.com/job/2"),
            "Workday",
        ],  # employer page that redirects
        [*good_row(3, Link="https://portal.example.test/3"), "Greenhouse"],
        [*good_row(4, Link="https://portal.example.test/4"), "Company site"],
        [*good_row(5, Link="https://portal.example.test/5"), "Bespoke thing"],
        [*good_row(6, Link="https://portal.example.test/6"), None],
        [*good_row(7, Link="https://www.linkedin.com/jobs/view/7"), "Custom"],
    ]
    result = parse(make(tmp_path, rows, header=header))
    assert [o.ats for o in result.opportunities] == [
        ATS.WORKDAY,
        ATS.WORKDAY,
        ATS.GREENHOUSE,
        ATS.CUSTOM,
        ATS.UNKNOWN,
        ATS.UNKNOWN,
        ATS.CUSTOM,
    ]
    assert result.opportunities[4].extra["ats_raw"] == "Bespoke thing"


def test_a_url_in_the_ats_column_is_used_as_the_link(tmp_path: Path) -> None:
    header = ["Company", "Role", "Term", "Application Portal"]
    rows = [["Acme", "A Intern", "Summer 2027", "https://jobs.lever.co/acme/1111"]]
    (opp,) = parse(make(tmp_path, rows, header=header)).opportunities
    assert opp.url == "https://jobs.lever.co/acme/1111" and opp.ats == ATS.LEVER


# --------------------------------------------------------------------------------------------- sheet selection


@pytest.mark.parametrize(
    "name",
    [
        "Verified Opportunities",
        "verified opportunities",
        "Verified-Opportunities",
        "VERIFIED_OPPORTUNITIES",
        "Verified Opps",
        "Verified Opportunities (2026)",
        "Opportunities",
    ],
)
def test_the_verified_opportunities_sheet_is_found_among_junk(tmp_path: Path, name: str) -> None:
    book = Book()
    book.sheet("Summary", [["Metric", "Value"], ["Rows", 3]])
    book.sheet(name, [HEADER, good_row(1)])
    book.sheet(
        "Rejected", [["Company", "Role", "Link"], ["Bad Co", "Bad Intern", "https://bad.example/1"]]
    )
    result = parse(book.save(tmp_path / "sheets.xlsx"))
    assert result.sheet == name
    assert titles(result) == ["Product Intern 1"]


def test_configured_sheet_wins_and_is_matched_loosely(tmp_path: Path) -> None:
    book = Book()
    book.sheet(SHEET, [HEADER, good_row(1)])
    book.sheet("Backup List", [HEADER, good_row(2)])
    path = book.save(tmp_path / "two.xlsx")
    assert parse(path).sheet == SHEET
    assert titles(parse(path, AppConfig(workbook=WorkbookConfig(sheet="Backup List")))) == [
        "Product Intern 2"
    ]
    assert titles(parse(path, AppConfig(workbook=WorkbookConfig(sheet="backup list ")))) == [
        "Product Intern 2"
    ]
    assert titles(parse(path, AppConfig(workbook=WorkbookConfig(sheet="Backup-List")))) == [
        "Product Intern 2"
    ]
    with pytest.raises(WorkbookError, match="not found"):  # a partial name is not a match
        parse(path, AppConfig(workbook=WorkbookConfig(sheet="Backup")))


def test_a_missing_configured_sheet_is_an_error_listing_the_sheets(tmp_path: Path) -> None:
    path = make(tmp_path, [good_row(1)])
    with pytest.raises(
        WorkbookError, match=r"sheet 'Nope' not found; sheets are: Verified Opportunities"
    ):
        parse(path, AppConfig(workbook=WorkbookConfig(sheet="Nope")))


def test_configured_sheet_without_a_header_is_an_error(tmp_path: Path) -> None:
    book = Book()
    book.sheet(SHEET, [HEADER, good_row(1)])
    book.sheet("Empty", [["nothing", "here"]])
    with pytest.raises(WorkbookError, match="no header row found in sheet 'Empty'"):
        parse(book.save(tmp_path / "e.xlsx"), AppConfig(workbook=WorkbookConfig(sheet="Empty")))


def test_unnamed_sheets_are_chosen_by_content(tmp_path: Path) -> None:
    book = Book()
    book.sheet(
        "Sheet1",
        [["Company", "Role", "Link"], ["Small Co", "Tiny Intern", "https://small.example/1"]],
    )
    book.sheet("Sheet2", [HEADER, good_row(1), good_row(2), good_row(3)])
    book.sheet("Sheet3", [["Notes"], ["nothing to see"]])
    result = parse(book.save(tmp_path / "content.xlsx"))
    assert result.sheet == "Sheet2"  # richest header, then most rows
    assert len(result.opportunities) == 3


def test_a_single_unnamed_sheet_is_used(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [good_row(1)], sheet="Sheet1"))
    assert result.sheet == "Sheet1" and len(result.opportunities) == 1


def test_a_matching_sheet_without_a_header_falls_back_to_other_sheets(tmp_path: Path) -> None:
    book = Book()
    book.sheet(SHEET, [["just", "notes"]])
    book.sheet("Data", [HEADER, good_row(1)])
    assert parse(book.save(tmp_path / "fallback.xlsx")).sheet == "Data"


def test_hidden_sheets_are_not_picked_by_name(tmp_path: Path) -> None:
    book = Book()
    book.sheet(SHEET, [HEADER, good_row(1, Role="Hidden Intern")], state="hidden")
    book.sheet("Live Data", [HEADER, good_row(2, Role="Visible Intern")])
    assert titles(parse(book.save(tmp_path / "hidden.xlsx"))) == ["Visible Intern"]


def test_all_sheets_without_headers_is_an_error(tmp_path: Path) -> None:
    book = Book()
    book.sheet("A", [["x", "y"]])
    book.sheet("B", [["z"]])
    with pytest.raises(WorkbookError, match=r"no header row found in any sheet \(A, B\)"):
        parse(book.save(tmp_path / "none.xlsx"))


def test_column_map_from_config_overrides_detection(tmp_path: Path) -> None:
    header = ["Firm", "Gig", "Click here", "Company", "Term"]
    rows = [["Acme", "Real Intern", "https://acme.example/1", "not the company", "Summer 2027"]]
    path = make(tmp_path, rows, header=header)
    with pytest.raises(WorkbookError):
        parse(path)  # only "Company" and "Term" are recognised
    cfg = AppConfig(
        workbook=WorkbookConfig(column_map={"company": "Firm", "title": "Gig", "url": "Click here"})
    )
    result = parse(path, cfg)
    (opp,) = result.opportunities
    assert (opp.company, opp.title, opp.url) == ("Acme", "Real Intern", "https://acme.example/1")
    assert result.warnings == []


def test_column_map_problems_surface_as_warnings(tmp_path: Path) -> None:
    cfg = AppConfig(workbook=WorkbookConfig(column_map={"location": "Nowhere", "bogus": "Company"}))
    result = parse(make(tmp_path, [good_row(1)]), cfg)
    assert len(result.opportunities) == 1
    assert any("no column matches" in w for w in result.warnings)
    assert any("unknown field 'bogus'" in w for w in result.warnings)


# --------------------------------------------------------------------------------------------- failure paths


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkbookError, match="workbook not found"):
        parse(tmp_path / "nope.xlsx")


def test_a_directory_is_not_a_workbook(tmp_path: Path) -> None:
    with pytest.raises(WorkbookError, match="cannot read"):
        parse(tmp_path)


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"")
    with pytest.raises(WorkbookError, match="is empty"):
        parse(path)


@pytest.mark.parametrize("payload", [b"this is not a zip file", b"PK\x03\x04garbage"])
def test_corrupt_files(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(payload)
    with pytest.raises(WorkbookError, match="not a readable .xlsx"):
        parse(path)


def test_encrypted_or_legacy_containers_get_a_specific_message(tmp_path: Path) -> None:
    path = tmp_path / "protected.xlsx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(WorkbookError, match="password-protected"):
        parse(path)


def test_streaming_mode_warns_that_links_and_merges_are_unavailable(tmp_path: Path) -> None:
    result = parse(make(tmp_path, [good_row(1)]), streaming=True)
    assert any("streaming mode" in w for w in result.warnings)
    assert parse(make(tmp_path, [good_row(1)], name="again.xlsx")).warnings == []


def test_excel_lock_files_and_csv_are_not_workbooks(tmp_path: Path) -> None:
    lock = tmp_path / "~$Verified.xlsx"
    lock.write_bytes(b"\x0bMYSELF" + b" " * 50)
    with pytest.raises(WorkbookError, match="not a readable .xlsx"):
        parse(lock)
    csv = tmp_path / "list.csv"
    csv.write_text("Company,Role,Link\nAcme,Intern,https://acme.example/1\n", encoding="utf-8")
    with pytest.raises(WorkbookError, match="not a readable .xlsx"):
        parse(csv)


def test_legacy_xls_is_rejected_with_advice(tmp_path: Path) -> None:
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(WorkbookError, match=r"\.xls format is not supported; save it as \.xlsx"):
        parse(path)


def test_workbook_without_rows_below_the_header(tmp_path: Path) -> None:
    result = parse(make(tmp_path, []))
    assert result.opportunities == [] and result.rejections == [] and result.data_rows == 0


def test_windows_hostile_paths(tmp_path: Path) -> None:
    folder = tmp_path / "My R\u00e9sum\u00e9 & Jobs (2027) \u2014 final"
    folder.mkdir()
    path = make(folder, [good_row(1)], name="Verified opps \u2014 Summer '27 (v2).xlsx")
    assert titles(parse(path)) == ["Product Intern 1"]


def test_the_file_handle_is_released(tmp_path: Path) -> None:
    path = make(tmp_path, [good_row(1)])
    parse(path)
    parse(path, streaming=True)
    path.unlink()  # would fail on Windows if a handle were left open
    assert not path.exists()


# --------------------------------------------------------------------------------------------- bad rows never abort


class Boom:
    def __str__(self) -> str:
        raise RuntimeError("cell exploded")


def test_a_row_that_raises_is_skipped_and_reported() -> None:
    rows = [
        [Cell(v) for v in HEADER],
        [Cell(v) for v in good_row(1)],
        [
            Cell("Acme"),
            Cell(Boom()),
            Cell("https://acme.example/2"),
            Cell(None),
            Cell("Summer 2027"),
            Cell(None),
            Cell(None),
        ],
        [Cell(v) for v in good_row(3)],
    ]
    grid = SheetGrid("Test", rows)
    header = detect_header(grid.rows)
    assert header is not None
    result = parse_sheet(grid, header, AppConfig(), today=TODAY)
    assert titles(result) == ["Product Intern 1", "Product Intern 3"]
    (rej,) = result.rejections
    assert rej.reason == RejectReason.ERROR and rej.row == 3
    assert "RuntimeError" in rej.detail and "cell exploded" in rej.detail
    assert rej.label == "row 3"


def test_parse_sheet_logs_a_summary(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    path = make(tmp_path, [good_row(1), good_row(2, Status="Closed")])
    with caplog.at_level(logging.INFO, logger="autoapply.sources.workbook"):
        parse(path)
    assert any(
        "kept 1 of 2 rows" in r.getMessage() and "closed=1" in r.getMessage()
        for r in caplog.records
    )
