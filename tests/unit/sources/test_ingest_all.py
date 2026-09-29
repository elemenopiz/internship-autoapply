"""Provider discovery (pkgutil), failure isolation and cross-provider de-duplication in ``ingest_all``."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
import textwrap
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest

from autoapply import sources
from autoapply.clock import FakeClock
from autoapply.config import AppConfig, AppPaths, PlatformsConfig, WorkbookConfig
from autoapply.contracts import OpportunityProvider, SourceContext
from autoapply.models import ATS, Opportunity, OpportunitySource
from autoapply.normalize import canonical_url
from autoapply.sources import IngestResult, discover_providers, ingest_all
from autoapply.sources.workbook import PROVIDER as WORKBOOK
from autoapply.testing.fixtures import SAMPLE_TODAY, build_sample_workbook

GH = "https://boards.greenhouse.io/acme/jobs/1"
WORKDAY = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/Product-Intern_R1"
LINKEDIN = "https://www.linkedin.com/jobs/view/3900000001"


def opp(
    url: str = GH,
    *,
    company: str = "Acme",
    title: str = "Product Intern",
    source: OpportunitySource = OpportunitySource.WORKBOOK,
    **kwargs: object,
) -> Opportunity:
    return Opportunity(
        company=company, title=title, url=url, location="Austin, TX", source=source, **kwargs
    )  # type: ignore[arg-type]


class Fake:
    """A minimal ``OpportunityProvider``."""

    def __init__(
        self,
        name: str,
        records: Sequence[object] = (),
        *,
        enabled: bool = True,
        fetch_error: BaseException | None = None,
        enabled_error: BaseException | None = None,
    ) -> None:
        self.name = name
        self._records = list(records)
        self._enabled = enabled
        self._fetch_error = fetch_error
        self._enabled_error = enabled_error
        self.fetch_calls = 0

    def enabled(self, config: AppConfig) -> bool:
        if self._enabled_error:
            raise self._enabled_error
        return self._enabled

    def fetch(self, ctx: SourceContext) -> list[Opportunity]:
        self.fetch_calls += 1
        if self._fetch_error:
            raise self._fetch_error
        return self._records  # type: ignore[return-value]


@dataclass
class Report:
    opportunities: list[Opportunity]
    dropped: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def rejection_summary(self) -> dict[str, list[str]]:
        return self.dropped


class Reporting(Fake):
    def __init__(self, name: str, report: Report) -> None:
        super().__init__(name, report.opportunities)
        self.report = report
        self.report_calls = 0

    def fetch_report(self, ctx: SourceContext) -> Report:
        self.report_calls += 1
        return self.report


def ctx_for(tmp_path: Path, cfg: AppConfig | None = None) -> SourceContext:
    paths = AppPaths(root=tmp_path / "data")
    paths.ensure()
    return SourceContext(config=cfg or AppConfig(), paths=paths, clock=FakeClock())


def only_workbook_config(path: Path | None) -> AppConfig:
    """Every other platform off, so providers other workers add later cannot interfere."""
    return AppConfig(
        workbook=WorkbookConfig(path=str(path) if path else None),
        platforms=PlatformsConfig(
            workbook=True, greenhouse=False, lever=False, ashby=False, linkedin=False, indeed=False
        ),
    )


# --------------------------------------------------------------------------------------------- discovery


@pytest.fixture
def fake_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch directory appended to ``autoapply.sources.__path__`` so pkgutil sees extra provider modules."""
    folder = tmp_path / "extra_sources"
    folder.mkdir()
    monkeypatch.setattr(sources, "__path__", [*sources.__path__, str(folder)])
    yield folder
    for name in [
        n
        for n in sys.modules
        if n.startswith("autoapply.sources.fake_") or n == "autoapply.sources._fake_hidden"
    ]:
        del sys.modules[name]
    importlib.invalidate_caches()


def write_module(folder: Path, name: str, body: str) -> None:
    (folder / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    importlib.invalidate_caches()


PROVIDER_MODULE = """
    from autoapply.models import Opportunity

    class _Provider:
        name = "{name}"

        def enabled(self, config):
            return True

        def fetch(self, ctx):
            return [Opportunity(company="Fake Co", title="{name} Intern", url="https://boards.greenhouse.io/{name}/jobs/1")]

    PROVIDER = _Provider()
"""


def test_the_workbook_provider_is_discovered() -> None:
    providers = discover_providers()
    assert WORKBOOK in providers
    assert providers[0] is WORKBOOK  # curated sources come first
    names = [p.name for p in providers]
    assert len(names) == len(set(names))
    for provider in providers:
        assert (
            isinstance(provider.name, str)
            and callable(provider.enabled)
            and callable(provider.fetch)
        )


def test_discovery_finds_provider_and_providers_attributes(fake_modules: Path) -> None:
    write_module(fake_modules, "fake_single", PROVIDER_MODULE.format(name="fake_single"))
    write_module(
        fake_modules,
        "fake_multi",
        """
        class _P:
            def __init__(self, name):
                self.name = name

            def enabled(self, config):
                return False

            def fetch(self, ctx):
                return []

        PROVIDERS = [_P("fake_multi_a"), _P("fake_multi_b")]
        """,
    )
    names = [p.name for p in discover_providers()]
    assert names[0] == "workbook"
    assert names.index("fake_multi_a") + 1 == names.index("fake_multi_b")
    assert names.index("fake_multi_b") < names.index(
        "fake_single"
    )  # modules alphabetically after the priority ones


def test_discovery_skips_private_and_provider_less_modules(fake_modules: Path) -> None:
    write_module(fake_modules, "_fake_hidden", PROVIDER_MODULE.format(name="hidden"))
    write_module(fake_modules, "fake_nothing", "VALUE = 1\n")
    names = [p.name for p in discover_providers()]
    assert "hidden" not in names
    assert "dedupe" not in names


def test_discovery_survives_a_module_that_fails_to_import(
    fake_modules: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_module(fake_modules, "fake_broken", "raise RuntimeError('cannot start')\n")
    write_module(fake_modules, "fake_good", PROVIDER_MODULE.format(name="fake_good"))
    with caplog.at_level(logging.WARNING, logger="autoapply.sources"):
        names = [p.name for p in discover_providers()]
    assert "fake_good" in names and "workbook" in names
    assert any(
        "fake_broken" in r.getMessage() and "cannot start" in r.getMessage() for r in caplog.records
    )


def test_ingest_reports_import_failures_and_invalid_providers(
    fake_modules: Path, tmp_path: Path
) -> None:
    write_module(fake_modules, "fake_broken", "raise RuntimeError('cannot start')\n")
    write_module(fake_modules, "fake_invalid", "PROVIDER = object()\n")
    write_module(fake_modules, "fake_good", PROVIDER_MODULE.format(name="fake_good"))
    result = ingest_all(ctx_for(tmp_path, only_workbook_config(None)))
    assert "fake_broken" in result.provider_errors
    assert "import failed: RuntimeError: cannot start" in result.provider_errors["fake_broken"]
    assert (
        "fake_invalid" in result.provider_errors
        and "invalid provider" in result.provider_errors["fake_invalid"]
    )
    assert any(e.startswith("fake_broken: ") for e in result.errors)
    assert [o.title for o in result.opportunities] == [
        "fake_good Intern"
    ]  # the healthy provider still ran
    assert result.per_provider_counts == {"fake_good": 1}


def test_module_names_order(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = ["indeed", "zzz", "boards", "dedupe", "workbook", "linkedin", "_hidden", "aaa"]
    monkeypatch.setattr(
        pkgutil, "iter_modules", lambda path: [pkgutil.ModuleInfo(None, n, False) for n in listing]
    )  # type: ignore[arg-type]
    assert sources._module_names() == ["workbook", "boards", "linkedin", "indeed", "aaa", "zzz"]


# --------------------------------------------------------------------------------------------- ingest_all with fakes


def test_no_providers_no_result(tmp_path: Path) -> None:
    result = ingest_all(ctx_for(tmp_path), providers=[])
    assert result == IngestResult()
    assert result.ok and result.raw_count == 0


def test_results_are_combined_counted_and_deduplicated(tmp_path: Path) -> None:
    workbook = Fake(
        "workbook",
        [
            opp(WORKDAY, ats=ATS.WORKDAY, last_verified=date(2026, 9, 10)),
            opp(GH, title="Other Intern"),
        ],
    )
    board = Fake(
        "boards",
        [
            opp(GH, title="Other Intern", source=OpportunitySource.GREENHOUSE),
            opp(LINKEDIN, source=OpportunitySource.LINKEDIN, last_verified=date(2026, 9, 25)),
        ],
    )
    result = ingest_all(ctx_for(tmp_path), providers=[workbook, board])
    assert result.per_provider_counts == {"workbook": 2, "boards": 2}
    assert result.raw_count == 4
    assert [o.title for o in result.opportunities] == ["Product Intern", "Other Intern"]
    assert result.duplicates_removed == 2
    merged = result.opportunities[0]
    assert merged.url == WORKDAY  # the direct ATS URL beat the LinkedIn listing of the same role
    assert merged.last_verified == date(2026, 9, 25)
    assert merged.extra["alt_urls"] == [LINKEDIN]
    assert result.errors == [] and result.ok


def test_disabled_providers_are_not_run_or_counted(tmp_path: Path) -> None:
    off = Fake("off", [opp()], enabled=False)
    on = Fake("on", [opp(WORKDAY, title="Other Intern")])
    result = ingest_all(ctx_for(tmp_path), providers=[off, on])
    assert off.fetch_calls == 0 and on.fetch_calls == 1
    assert result.per_provider_counts == {"on": 1}


def test_a_failing_provider_is_isolated(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    good = Fake("good", [opp()])
    boom = Fake("boards", fetch_error=TimeoutError("timed out talking to the API"))
    other = Fake("other", [opp(WORKDAY, title="Other Intern")])
    with caplog.at_level(logging.WARNING, logger="autoapply.sources"):
        result = ingest_all(ctx_for(tmp_path), providers=[good, boom, other])
    assert [o.title for o in result.opportunities] == ["Product Intern", "Other Intern"]
    assert result.per_provider_counts == {"good": 1, "boards": 0, "other": 1}
    assert result.errors == ["boards: TimeoutError: timed out talking to the API"]
    assert result.provider_errors == {"boards": "TimeoutError: timed out talking to the API"}
    assert not result.ok
    assert any("boards failed" in r.getMessage() for r in caplog.records)


def test_a_provider_whose_enabled_check_raises_is_isolated(tmp_path: Path) -> None:
    bad = Fake("bad", enabled_error=RuntimeError("config is odd"))
    good = Fake("good", [opp()])
    result = ingest_all(ctx_for(tmp_path), providers=[bad, good])
    assert result.provider_errors == {"bad": "RuntimeError: config is odd"}
    assert len(result.opportunities) == 1


def test_every_provider_failing_still_returns_a_result(tmp_path: Path) -> None:
    result = ingest_all(
        ctx_for(tmp_path),
        providers=[Fake("a", fetch_error=ValueError("x")), Fake("b", fetch_error=OSError("y"))],
    )
    assert result.opportunities == [] and len(result.errors) == 2
    assert result.per_provider_counts == {"a": 0, "b": 0}


def test_none_and_junk_results_are_handled(tmp_path: Path) -> None:
    none_provider = Fake("none")
    none_provider.fetch = lambda ctx: None  # type: ignore[method-assign,assignment,return-value]
    junk = Fake("junk", [opp(), {"company": "not an Opportunity"}, None, "text"])
    result = ingest_all(ctx_for(tmp_path), providers=[none_provider, junk])
    assert (
        "TypeError" in result.provider_errors["none"] and "None" in result.provider_errors["none"]
    )
    assert result.per_provider_counts == {"none": 0, "junk": 1}
    assert len(result.opportunities) == 1
    assert any("junk: ignored 3 record(s)" in w for w in result.warnings)


def test_keyboard_interrupt_is_not_swallowed(tmp_path: Path) -> None:
    with pytest.raises(KeyboardInterrupt):
        ingest_all(ctx_for(tmp_path), providers=[Fake("x", fetch_error=KeyboardInterrupt())])


def test_duplicate_provider_names_get_unique_keys(tmp_path: Path) -> None:
    a = Fake("same", [opp(GH)])
    b = Fake("same", [opp(WORKDAY, title="Other Intern")])
    c = Fake("same", fetch_error=ValueError("nope"))
    result = ingest_all(ctx_for(tmp_path), providers=[a, b, c])
    assert result.per_provider_counts == {"same": 1, "same#2": 1, "same#3": 0}
    assert list(result.provider_errors) == ["same#3"]


def test_very_long_error_messages_are_capped(tmp_path: Path) -> None:
    result = ingest_all(
        ctx_for(tmp_path), providers=[Fake("x", fetch_error=RuntimeError("y" * 5000))]
    )
    assert len(result.provider_errors["x"]) <= 500 and result.provider_errors["x"].endswith("…")


def test_providers_offering_a_report_are_asked_for_it(tmp_path: Path) -> None:
    report = Report(
        [opp(GH)],
        dropped={"closed": ["Old Intern"], "wrong_term": ["Fall Intern"]},
        warnings=["column_map: no column matches"],
    )
    provider = Reporting("workbook", report)
    silent = Reporting("quiet", Report([opp(WORKDAY, title="Other Intern")]))
    result = ingest_all(ctx_for(tmp_path), providers=[provider, silent])
    assert provider.report_calls == 1 and provider.fetch_calls == 0
    assert result.rejections == {
        "workbook": {"closed": ["Old Intern"], "wrong_term": ["Fall Intern"]}
    }  # nothing for "quiet"
    assert result.warnings == ["workbook: column_map: no column matches"]
    assert result.per_provider_counts == {"workbook": 1, "quiet": 1}


def test_ingest_result_helpers() -> None:
    result = IngestResult()
    result.fail("boards", "boom")
    result.per_provider_counts = {"a": 2, "b": 3}
    assert result.raw_count == 5 and not result.ok
    assert result.errors == ["boards: boom"] and result.provider_errors == {"boards": "boom"}


def test_discovery_is_used_when_no_providers_are_given(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    build_sample_workbook(tmp_path / "s.xlsx")
    result = ingest_all(ctx_for(tmp_path, only_workbook_config(tmp_path / "s.xlsx")))
    assert result.per_provider_counts == {"workbook": 19}
    assert result.errors == []


# --------------------------------------------------------------------------------------------- the sample workbook


@pytest.fixture
def sample(tmp_path: Path):  # type: ignore[no-untyped-def]
    info = build_sample_workbook(tmp_path / "Verified Opportunities — sample.xlsx")
    ctx = ctx_for(tmp_path, only_workbook_config(info.path))
    return info, ctx


def test_sample_workbook_through_ingest_all(sample) -> None:  # type: ignore[no-untyped-def]
    info, ctx = sample
    result = ingest_all(ctx, providers=[WORKBOOK])
    assert result.errors == [] and result.provider_errors == {} and result.warnings == []
    assert [o.id for o in result.opportunities] == info.expected_ids
    assert len({o.id for o in result.opportunities}) == len(result.opportunities)
    assert result.per_provider_counts == {"workbook": len(info.workbook_kept)}
    assert result.duplicates_removed == sum(len(v) for v in info.expected_duplicates.values()) == 4
    assert result.rejections == {"workbook": info.expected_rejections}
    kept_titles = {o.title for o in result.opportunities}
    for titles in info.expected_rejections.values():
        assert kept_titles.isdisjoint(titles)
    assert "Senior Product Manager" not in kept_titles
    assert (
        "Marketing Intern" in kept_titles
    )  # not a target role, but scoring (not ingest) rejects it


def test_sample_workbook_through_discovery_gives_the_same_result(sample) -> None:  # type: ignore[no-untyped-def]
    info, ctx = sample
    discovered = ingest_all(ctx)
    explicit = ingest_all(ctx, providers=[WORKBOOK])
    assert (
        [o.id for o in discovered.opportunities]
        == [o.id for o in explicit.opportunities]
        == info.expected_ids
    )
    assert discovered.rejections == explicit.rejections
    assert discovered.errors == []


def test_sample_duplicates_are_merged_the_documented_way(sample) -> None:  # type: ignore[no-untyped-def]
    info, ctx = sample
    by_id = {o.id: o for o in ingest_all(ctx, providers=[WORKBOOK]).opportunities}
    for survivor_id, dropped_urls in info.expected_duplicates.items():
        merged = by_id[survivor_id]
        survivor = next(r for r in info.rows if r.id == survivor_id and r.outcome == "ingested")
        assert merged.url == survivor.url  # the direct ATS / first-listed URL is what survives
        alt = merged.extra.get("alt_urls", [])
        for url in dropped_urls:
            if canonical_url(url) == canonical_url(survivor.url):
                assert (
                    url not in alt
                )  # a tracking-parameter copy of the same page is not an alternative
            else:
                assert url in alt, (survivor.title, url)
    kestrel = by_id[info.row("Business Analyst Intern", "Kestrel Aerospace").id]
    assert kestrel.extra["alt_urls"] == [info.by_key("kestrel_ba_linkedin").url]
    assert kestrel.last_verified == info.today - timedelta(
        days=3
    )  # the LinkedIn listing was verified more recently
    assert kestrel.location == "Austin, TX"
    ironbridge = by_id[info.row("Business Operations Intern", "Ironbridge Foods").id]
    assert (
        "ashbyhq" in ironbridge.url or "ashby" in ironbridge.url
    )  # the direct ATS row beat the earlier Indeed row
    assert ironbridge.extra["alt_urls"] == [info.by_key("iron_biz_indeed").url]
    assert ironbridge.ats == ATS.ASHBY


def test_sample_ingest_never_emits_junk_sheet_rows(sample) -> None:  # type: ignore[no-untyped-def]
    _, ctx = sample
    urls = {o.url for o in ingest_all(ctx, providers=[WORKBOOK]).opportunities}
    assert not any("rejected.example.test" in u for u in urls)


def test_a_broken_workbook_does_not_stop_other_providers(tmp_path: Path) -> None:
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"definitely not a workbook")
    other = Fake("boards", [opp(GH)])
    result = ingest_all(ctx_for(tmp_path, only_workbook_config(bad)), providers=[WORKBOOK, other])
    assert (
        "workbook" in result.provider_errors
        and "not a readable .xlsx" in result.provider_errors["workbook"]
    )
    assert [o.url for o in result.opportunities] == [GH]
    assert result.per_provider_counts == {"workbook": 0, "boards": 1}


def test_a_missing_workbook_file_is_reported_not_raised(tmp_path: Path) -> None:
    result = ingest_all(
        ctx_for(tmp_path, only_workbook_config(tmp_path / "gone.xlsx")), providers=[WORKBOOK]
    )
    assert result.opportunities == []
    assert "workbook not found" in result.provider_errors["workbook"]


def test_workbook_is_skipped_when_no_path_is_configured(tmp_path: Path) -> None:
    result = ingest_all(ctx_for(tmp_path, only_workbook_config(None)), providers=[WORKBOOK])
    assert result.per_provider_counts == {} and result.errors == []


def test_opportunity_provider_protocol_is_satisfied() -> None:
    provider: OpportunityProvider = WORKBOOK  # static check via mypy; runtime attributes below
    assert provider.name == "workbook"
    assert date(2026, 9, 29) == SAMPLE_TODAY
