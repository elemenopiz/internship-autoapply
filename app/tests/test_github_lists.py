"""Tests for the SimplifyJobs / vanshb03 GitHub-list job source."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from bot.search.github_lists import GitHubListsSearcher, row_cycle, rows_to_jobs


def _row(**kw):
    base = {"active": True, "is_visible": True, "category": "Product", "company_name": "Acme",
            "title": "Product Manager Intern", "url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "locations": ["New York, NY"], "terms": ["Summer 2027"], "id": "r1",
            "sponsorship": "Offers Sponsorship"}
    base.update(kw)
    return base


class TestCycle:
    def test_simplify_terms(self):
        assert row_cycle(_row(), "simplify") == "Summer 2027"
        assert row_cycle(_row(terms=["Spring 2027"], title="Supply Chain Co-op"), "simplify") \
            == "Spring 2027 co-op"
        assert row_cycle(_row(terms=["Spring 2027"]), "simplify") is None      # not a co-op
        assert row_cycle(_row(terms=["Summer 2026", "Fall 2026"]), "simplify") is None

    def test_vansh_bare_season(self):
        assert row_cycle({"season": "Summer", "title": "x"}, "vansh") == "Summer 2027"
        assert row_cycle({"season": "Spring/Summer", "title": "x"}, "vansh") == "Summer 2027"
        assert row_cycle({"season": "Fall", "title": "x"}, "vansh") is None


class TestRows:
    def test_filters_inactive_category_and_bad_urls(self):
        rows = [_row(), _row(id="r2", active=False), _row(id="r3", category="Software"),
                _row(id="r4", url="not-a-url"), _row(id="r5", is_visible=False)]
        jobs = list(rows_to_jobs(rows, "simplify"))
        assert [j.external_id for j in jobs] == ["simplify:r1"]
        job = jobs[0]
        assert job.platform == "github_list" and job.location == "New York, NY"
        assert "Cycle: Summer 2027" in job.description

    def test_vansh_has_no_category_filter(self):
        jobs = list(rows_to_jobs([{"active": True, "title": "Data Analyst Intern",
                                   "company_name": "B", "url": "https://jobs.lever.co/b/1",
                                   "season": "Summer", "id": "v1", "locations": []}], "vansh"))
        assert len(jobs) == 1


def test_search_dedupes_across_sources_and_uses_cache(tmp_path):
    same = _row(url="https://jobs.lever.co/acme/1?utm_source=Simplify")
    session = MagicMock()
    session.get.side_effect = lambda url, **kw: MagicMock(
        text=json.dumps([same] if "Simplify" in url else
                        [{**same, "season": "Summer", "id": "v9", "url": "https://jobs.lever.co/acme/1"}]),
        raise_for_status=lambda: None)
    searcher = GitHubListsSearcher(cache_dir=tmp_path, session=session)
    assert len(list(searcher.search(None))) == 1
    calls = session.get.call_count
    list(searcher.search(None))                      # fresh cache: no new downloads
    assert session.get.call_count == calls
