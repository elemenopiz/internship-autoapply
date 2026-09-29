"""The workbook provider (path resolution, enabled/fetch, clock and time zone) and ``inspect_workbook``."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths, PlatformsConfig, WorkbookConfig
from autoapply.contracts import SourceContext
from autoapply.models import OpportunitySource, SearchProfile
from autoapply.sources.workbook import (
    PROVIDER,
    WorkbookError,
    WorkbookProvider,
    WorkbookReport,
    inspect_workbook,
    resolve_workbook_path,
)

HEADER = ["Company", "Role", "Link", "Location", "Term", "Status", "Last Verified"]
DAY = date(2026, 9, 29)


def row(n: int, verified: date, **over: object) -> list[object]:
    base: dict[str, object] = {
        "Company": "Acme",
        "Role": f"Product Intern {n}",
        "Link": f"https://jobs.acme.example/{n}",
        "Location": "Austin, TX",
        "Term": "Summer 2027",
        "Status": "Open",
        "Last Verified": verified,
    }
    base.update(over)
    return [base[h] for h in HEADER]


def write(path: Path, *sheets: tuple[str, list[list[object]]]) -> Path:
    wb = Workbook()
    first = wb.active
    assert first is not None
    wb.remove(first)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def context(tmp_path: Path, cfg: AppConfig, clock: FakeClock | None = None) -> SourceContext:
    paths = AppPaths(root=tmp_path / "data")
    paths.ensure()
    return SourceContext(config=cfg, paths=paths, clock=clock or FakeClock())


def config_for(path: Path | str, **kwargs: object) -> AppConfig:
    return AppConfig(workbook=WorkbookConfig(path=str(path)), **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------- path resolution


def test_absolute_paths_are_kept(tmp_path: Path) -> None:
    target = tmp_path / "book.xlsx"
    assert resolve_workbook_path(str(target)) == target
    assert resolve_workbook_path(target) == target


@pytest.mark.parametrize("wrapper", ['"{}"', "'{}'", '  "{}"  ', "{}"])
def test_quotes_from_copy_as_path_are_stripped(tmp_path: Path, wrapper: str) -> None:
    target = tmp_path / "My Verified List.xlsx"
    assert resolve_workbook_path(wrapper.format(target)) == target


def test_relative_paths_are_tried_against_cwd_data_dir_and_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    paths = AppPaths(root=project / "data")

    (project / "data" / "in_data.xlsx").write_bytes(b"x")
    (project / "in_project.xlsx").write_bytes(b"x")
    (elsewhere / "in_cwd.xlsx").write_bytes(b"x")

    assert resolve_workbook_path("in_data.xlsx", paths) == project / "data" / "in_data.xlsx"
    assert resolve_workbook_path("in_project.xlsx", paths) == project / "in_project.xlsx"
    assert resolve_workbook_path("in_cwd.xlsx", paths) == elsewhere / "in_cwd.xlsx"
    # nothing exists: the first candidate, so the error message names something sensible
    assert resolve_workbook_path("missing.xlsx", paths) == elsewhere / "missing.xlsx"


def test_home_and_environment_variables_are_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    assert resolve_workbook_path("~/list.xlsx") == tmp_path / "list.xlsx"
    assert resolve_workbook_path("$JOBS_DIR/list.xlsx") == tmp_path / "jobs" / "list.xlsx"


@pytest.mark.parametrize("raw", [None, "", "   ", '""'])
def test_empty_paths_are_an_error(raw: str | None) -> None:
    with pytest.raises(WorkbookError, match="not configured"):
        resolve_workbook_path(raw)


# --------------------------------------------------------------------------------------------- provider


def test_provider_identity() -> None:
    assert PROVIDER.name == "workbook" == OpportunitySource.WORKBOOK.value
    assert isinstance(PROVIDER, WorkbookProvider)


@pytest.mark.parametrize(
    ("path", "toggle", "expected"),
    [
        (None, True, False),
        ("", True, False),
        ("   ", True, False),
        ("book.xlsx", True, True),
        ("book.xlsx", False, False),
        (
            "does/not/exist.xlsx",
            True,
            True,
        ),  # a bad path is reported by fetch, not silently ignored
    ],
)
def test_enabled(path: str | None, toggle: bool, expected: bool) -> None:
    cfg = AppConfig(workbook=WorkbookConfig(path=path), platforms=PlatformsConfig(workbook=toggle))
    assert PROVIDER.enabled(cfg) is expected


def test_fetch_reads_the_configured_workbook(tmp_path: Path) -> None:
    path = write(
        tmp_path / "book.xlsx",
        ("Verified Opportunities", [HEADER, row(1, DAY - timedelta(days=3))]),
    )
    opps = PROVIDER.fetch(context(tmp_path, config_for(path)))
    assert [o.title for o in opps] == ["Product Intern 1"]
    assert opps[0].source == OpportunitySource.WORKBOOK


def test_fetch_report_explains_the_dropped_rows(tmp_path: Path) -> None:
    path = write(
        tmp_path / "book.xlsx",
        (
            "Verified Opportunities",
            [HEADER, row(1, DAY), row(2, DAY, Status="Closed"), row(3, DAY, Term="Fall 2026")],
        ),
    )
    report = PROVIDER.fetch_report(context(tmp_path, config_for(path)))
    assert [o.title for o in report.opportunities] == ["Product Intern 1"]
    assert report.rejection_summary() == {
        "closed": ["Product Intern 2"],
        "wrong_term": ["Product Intern 3"],
    }


def test_fetch_logs_through_the_context_logger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write(tmp_path / "book.xlsx", ("Verified Opportunities", [HEADER, row(1, DAY)]))
    with caplog.at_level(logging.INFO, logger="autoapply.sources"):
        PROVIDER.fetch(context(tmp_path, config_for(path)))
    assert any("kept 1 of 1 rows" in r.getMessage() for r in caplog.records)


def test_fetch_raises_a_workbook_error_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkbookError, match="workbook not found"):
        PROVIDER.fetch(context(tmp_path, config_for(tmp_path / "gone.xlsx")))


def test_fetch_without_a_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(WorkbookError, match="not configured"):
        PROVIDER.fetch(context(tmp_path, AppConfig()))


def test_relative_path_in_config_resolves_against_the_data_directory(tmp_path: Path) -> None:
    ctx = context(tmp_path, config_for("verified.xlsx"))
    write(ctx.paths.root / "verified.xlsx", ("Verified Opportunities", [HEADER, row(1, DAY)]))
    assert len(PROVIDER.fetch(ctx)) == 1


def test_the_day_comes_from_the_clock_in_the_configured_time_zone(tmp_path: Path) -> None:
    """One row is exactly 46 UTC days old at 2026-09-30 03:00Z, but only 45 days old in Chicago (22:00 local)."""
    boundary = date(2026, 8, 15)
    path = write(tmp_path / "book.xlsx", ("Verified Opportunities", [HEADER, row(1, boundary)]))
    late_utc = FakeClock(datetime(2026, 9, 30, 3, 0, tzinfo=UTC))
    chicago = config_for(path, timezone="America/Chicago")
    utc = config_for(path, timezone="UTC")
    assert len(PROVIDER.fetch(context(tmp_path, chicago, late_utc))) == 1
    assert len(PROVIDER.fetch(context(tmp_path, utc, late_utc))) == 0


def test_an_unknown_time_zone_falls_back_to_the_utc_day(tmp_path: Path) -> None:
    path = write(tmp_path / "book.xlsx", ("Verified Opportunities", [HEADER, row(1, DAY)]))
    cfg = config_for(path)
    object.__setattr__(
        cfg, "timezone", "Mars/Olympus_Mons"
    )  # bypass validation: a hand-edited config
    assert (
        len(
            PROVIDER.fetch(
                context(tmp_path, cfg, FakeClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC)))
            )
        )
        == 1
    )


def test_search_profile_flows_into_the_provider(tmp_path: Path) -> None:
    path = write(
        tmp_path / "book.xlsx",
        ("Verified Opportunities", [HEADER, row(1, DAY - timedelta(days=20), Term="Fall 2026")]),
    )
    cfg = config_for(path, search=SearchProfile(target_term="Fall 2026", recent_days=30))
    (opp,) = PROVIDER.fetch(context(tmp_path, cfg))
    assert opp.term == "Fall 2026"
    assert PROVIDER.fetch(context(tmp_path, config_for(path))) == []


# --------------------------------------------------------------------------------------------- inspect_workbook


def _junk_and_data(tmp_path: Path) -> Path:
    return write(
        tmp_path / "inspect.xlsx",
        ("Summary", [["Metric", "Value"], ["Rows", 3]]),
        (
            "Verified Opportunities",
            [
                ["Title row"],
                [],
                [*HEADER, "Pay"],
                [*row(1, DAY - timedelta(days=2)), "$30"],
                [*row(2, DAY - timedelta(days=2), Status="Closed"), "$31"],
                [*row(3, DAY - timedelta(days=90)), "$32"],
                [*row(4, DAY - timedelta(days=2), Term="Fall 2026"), None],
            ],
        ),
        (
            "Rejected",
            [["Company", "Role", "Link"], ["Bad Co", "Bad Intern", "https://bad.example/1"]],
        ),
    )


def test_inspect_reports_sheets_header_mapping_and_samples(tmp_path: Path) -> None:
    report = inspect_workbook(_junk_and_data(tmp_path), today=DAY)
    assert isinstance(report, WorkbookReport)
    assert report.sheets == ["Summary", "Verified Opportunities", "Rejected"]
    assert report.sheet == "Verified Opportunities"
    assert report.header_row == 3
    assert report.headers[:3] == ["Company", "Role", "Link"] and report.headers[-1] == "Pay"
    assert report.mapping["company"] == "Company"
    assert report.mapping["verified"] == "Last Verified"
    assert "Pay" not in report.mapping.values()
    assert [(c.column, c.header, c.maps_to) for c in report.columns][:3] == [
        ("A", "Company", "company"),
        ("B", "Role", "title"),
        ("C", "Link", "url"),
    ]
    assert report.columns[-1].maps_to is None  # "Pay" becomes an extra
    assert len(report.sample_rows) == 4
    assert report.sample_rows[0]["Role"] == "Product Intern 1"
    assert report.sample_rows[0]["Pay"] == "$30"
    assert report.data_rows == 4 and report.kept == 1
    assert report.rejected == {"closed": 1, "stale": 1, "wrong_term": 1}
    assert report.rejected_rows["closed"] == ["Product Intern 2"]
    by_name = {s.name: s for s in report.sheet_details}
    assert by_name["Verified Opportunities"].header_row == 3
    assert by_name["Verified Opportunities"].name_score == 100.0
    assert by_name["Summary"].header_row is None
    assert by_name["Rejected"].header_row == 1 and by_name["Rejected"].name_score < 50
    assert report.warnings == []


def test_inspect_sample_size_and_json(tmp_path: Path) -> None:
    report = inspect_workbook(_junk_and_data(tmp_path), today=DAY, sample_size=2)
    assert len(report.sample_rows) == 2
    restored = WorkbookReport.model_validate_json(report.model_dump_json())
    assert restored == report


def test_inspect_uses_the_config_for_sheet_and_column_map(tmp_path: Path) -> None:
    path = write(
        tmp_path / "custom.xlsx",
        (
            "Main",
            [
                ["Firm", "Gig", "Click here", "Term"],
                ["Acme", "A Intern", "https://acme.example/1", "Summer 2027"],
            ],
        ),
    )
    plain = inspect_workbook(path, today=DAY)
    assert plain.sheet is None and plain.warnings and "no header row found" in plain.warnings[0]
    assert plain.sheets == ["Main"]  # the sheet list is still reported
    cfg = AppConfig(
        workbook=WorkbookConfig(
            sheet="main", column_map={"company": "Firm", "title": "Gig", "url": "Click here"}
        )
    )
    mapped = inspect_workbook(path, cfg, today=DAY)
    assert mapped.sheet == "Main" and mapped.kept == 1
    assert mapped.mapping["title"] == "Gig"


def test_inspect_defaults_today_to_the_system_date(tmp_path: Path) -> None:
    path = write(tmp_path / "now.xlsx", ("Verified Opportunities", [HEADER, row(1, date.today())]))
    assert inspect_workbook(path).kept == 1


def test_inspect_raises_only_for_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(WorkbookError):
        inspect_workbook(tmp_path / "missing.xlsx")
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"nope")
    with pytest.raises(WorkbookError):
        inspect_workbook(bad)
    with pytest.raises(WorkbookError):
        inspect_workbook(str(tmp_path / "missing.xlsx"))


def test_render_is_readable(tmp_path: Path) -> None:
    text = inspect_workbook(_junk_and_data(tmp_path), today=DAY).render()
    assert "Sheets: Summary, Verified Opportunities, Rejected" in text
    assert "Using sheet 'Verified Opportunities', header on row 3" in text
    assert "Company" in text and "company" in text and "-> extra" in text
    assert "rejected closed: 1" in text and "would be kept" in text
    empty = inspect_workbook(write(tmp_path / "e.xlsx", ("Only", [["x", "y"]])), today=DAY).render()
    assert "No usable sheet / header row found." in empty and "Warning:" in empty
