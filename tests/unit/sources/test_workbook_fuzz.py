"""Property / fuzz style tests: the parsers never raise and keep their invariants on hostile input."""

from __future__ import annotations

import random
import string
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from autoapply.config import AppConfig
from autoapply.models import Opportunity
from autoapply.sources import IngestResult
from autoapply.sources.workbook import (
    FIELD_ALIASES,
    Cell,
    RejectReason,
    SheetGrid,
    classify_status,
    clean_text,
    decide_term,
    detect_header,
    extract_url,
    infer_day_first,
    looks_like_internship,
    match_header,
    parse_date_value,
    parse_hyperlink_formula,
    parse_sheet,
    parse_terms,
    read_workbook,
)

TODAY = date(2026, 9, 29)
ALPHABET = (
    string.ascii_letters
    + string.digits
    + " \t\n/-.,:;'\"()[]{}&|=+#?!*@%$~_"
    + "\u00e9\u00fc\u00f1\u2013\u2014\u2019\u201c\u201d\u00a0\u200b\u3000\u2713\u274c\U0001f7e2"
)
WORDS = [
    "Summer",
    "Fall",
    "2027",
    "2026",
    "'27",
    "Intern",
    "Senior",
    "Open",
    "Closed",
    "Sep",
    "1",
    "9/1/26",
    "https://",
    "acme.example",
    "HYPERLINK(",
    '"',
    "Company",
    "Link",
    "N/A",
    "www.",
]


def random_text(rng: random.Random, max_len: int = 40) -> str:
    if rng.random() < 0.5:
        return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, max_len)))
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 6)))


def random_value(rng: random.Random) -> object:
    kind = rng.randrange(9)
    if kind == 0:
        return None
    if kind == 1:
        return rng.choice([True, False])
    if kind == 2:
        return rng.randint(-10, 80_000)
    if kind == 3:
        return rng.random() * rng.choice([1, 1000, 100_000])
    if kind == 4:
        return datetime(2026, 9, 1) + timedelta(days=rng.randint(-400, 400))
    if kind == 5:
        return time(rng.randrange(24), rng.randrange(60))
    if kind == 6:
        return timedelta(hours=rng.randint(0, 100))
    return random_text(rng)


SEEDS = list(range(40))


@pytest.mark.parametrize("seed", SEEDS)
def test_scalar_parsers_never_raise(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(60):
        value = random_value(rng)
        text = random_text(rng, 80)
        parsed = parse_date_value(value, today=TODAY, day_first=rng.random() < 0.5)
        assert parsed is None or isinstance(parsed, date)
        parse_date_value(text, today=TODAY, prefer_future=True)  # must simply not raise
        assert classify_status(value) in {"open", "closed", "applied", "unknown"}
        assert classify_status(text, yes_means="closed") in {"open", "closed", "applied", "unknown"}
        assert isinstance(clean_text(value), str)
        assert isinstance(clean_text(text, multiline=True, limit=10), str)
        url = extract_url(text)
        assert url is None or url.startswith(("http://", "https://"))
        assert decide_term("Summer 2027", text, text, text).status in {"ok", "assumed", "wrong"}
        assert decide_term(text or "x", text, text, text).status in {"ok", "assumed", "wrong"}
        assert isinstance(parse_terms(text), list) and isinstance(
            parse_terms(text, strict=True), list
        )
        assert isinstance(looks_like_internship(text, text, text)[0], bool)
        match = match_header(value if isinstance(value, str) else text)
        assert match is None or match.field in FIELD_ALIASES
        assert isinstance(infer_day_first([value, text]), bool)
        formula = parse_hyperlink_formula(text)
        assert formula is None or len(formula) == 2
        parse_hyperlink_formula("=HYPERLINK(" + text + ")")


def test_dates_are_stable_under_reparse() -> None:
    """A parsed date, rendered in any style and parsed again, is the same date."""
    rng = random.Random(7)
    styles = [
        lambda d: d.isoformat(),
        lambda d: f"{d.month}/{d.day}/{d.year}",
        lambda d: f"{d.month:02d}/{d.day:02d}/{d.year % 100:02d}",
        lambda d: d.strftime("%b %d, %Y").replace(" 0", " "),
        lambda d: d.strftime("%d %b %Y"),
        lambda d: d.strftime("%B %d %Y"),
        lambda d: (d - date(1899, 12, 30)).days,
        lambda d: datetime(d.year, d.month, d.day),
        lambda d: f"{d.day}.{d.month}.{d.year}",
    ]
    for _ in range(300):
        day = date(2020, 1, 1) + timedelta(days=rng.randrange(0, 365 * 8))
        style = rng.choice(styles)
        # %b/%B depend on the locale; the tests run in the C locale, and month-first is the default order
        assert parse_date_value(style(day), today=TODAY) == day, (day, style(day))


@pytest.mark.parametrize("seed", SEEDS)
def test_random_grids_are_always_accounted_for(seed: int) -> None:
    rng = random.Random(1000 + seed)
    header = [
        "Company",
        "Role",
        "Link",
        "Location",
        "Term",
        "Status",
        "Last Verified",
        "Notes",
        "Deadline",
    ]
    rows = [[Cell(h) for h in header]]
    for _ in range(rng.randint(5, 40)):
        if rng.random() < 0.15:
            rows.append([Cell() for _ in header])
        else:
            rows.append(
                [
                    Cell(
                        random_value(rng),
                        link=rng.choice(
                            [None, None, "https://jobs.example.test/x", "#Sheet!A1", "mailto:a@b.c"]
                        ),
                    )
                    for _ in header
                ]
            )
    grid = SheetGrid("Fuzz", rows)
    info = detect_header(grid.rows)
    assert info is not None
    result = parse_sheet(grid, info, AppConfig(), today=TODAY)
    body = len(rows) - 1
    assert len(result.opportunities) + len(result.rejections) + result.junk_rows == body
    assert result.data_rows == len(result.opportunities) + len(result.rejections)
    for opp in result.opportunities:
        assert isinstance(opp, Opportunity)
        assert opp.company and opp.title and opp.url.startswith(("http://", "https://"))
        assert opp.is_open and opp.term == "Summer 2027"
    for rej in result.rejections:
        assert isinstance(rej.reason, RejectReason)
        assert rej.reason != RejectReason.ERROR, rej  # isolation must not be hiding a bug
        assert rej.label
    assert result.rejection_counts() == {k: len(v) for k, v in result.rejection_summary().items()}


@pytest.mark.parametrize("seed", range(10))
def test_random_workbooks_parse_the_same_after_column_shuffles(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    columns = {
        "Company": ["Acme", "Globex", "Initech", "Umbrella Labs"],
        "Role": ["Product Intern", "Strategy Intern", "Senior Analyst", "Data Analyst Intern"],
        "Link": [f"https://jobs.example.test/{n}" for n in range(4)],
        "Location": ["Austin, TX", "Remote", None, "Dallas, TX"],
        "Term": ["Summer 2027", "Fall 2026", "Summer 2027", "summer '27"],
        "Status": ["Open", "Open", "Open", "Closed"],
        "Last Verified": [TODAY - timedelta(days=n) for n in (1, 2, 3, 4)],
        "Notes": ["a", None, "c", "d"],
    }
    baseline = _run(tmp_path, columns, "base.xlsx")
    assert baseline == [("Acme", "Product Intern", "https://jobs.example.test/0", "Austin, TX")]
    for n in range(4):
        order = list(columns)
        rng.shuffle(order)
        shuffled = {name: columns[name] for name in order}
        assert _run(tmp_path, shuffled, f"shuffled{n}.xlsx") == baseline


def _run(
    tmp_path: Path, columns: dict[str, list[object]], name: str
) -> list[tuple[str, str, str, str | None]]:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Verified Opportunities"
    ws.append(list(columns))
    for i in range(4):
        ws.append([values[i] for values in columns.values()])
    path = tmp_path / name
    wb.save(path)
    result = read_workbook(path, AppConfig(), today=TODAY)
    assert sorted(r.reason for r in result.rejections) == sorted(
        [RejectReason.WRONG_TERM, RejectReason.NOT_INTERNSHIP, RejectReason.CLOSED]
    )
    return sorted((o.company, o.title, o.url, o.location) for o in result.opportunities)


def test_ingest_result_is_positional_like_the_spec() -> None:
    opp = Opportunity(company="A", title="B Intern", url="https://x.example.test/1")
    result = IngestResult([opp], {"workbook": 1}, ["boards: boom"])
    assert result.opportunities == [opp]
    assert result.per_provider_counts == {"workbook": 1}
    assert result.errors == ["boards: boom"]


def test_own_source_files_are_ascii_only() -> None:
    """Invisible / look-alike characters in code are hard to review: keep non-ASCII as visible escapes."""
    root = Path(__file__).resolve().parents[3] / "src" / "autoapply"
    for relative in (
        "sources/workbook.py",
        "sources/dedupe.py",
        "sources/__init__.py",
        "testing/fixtures.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        offenders = sorted({c for c in text if ord(c) > 127})
        assert not offenders, (relative, [hex(ord(c)) for c in offenders])
