"""Tests for the intern-list.com lead filters and posting resolution wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.search.intern_list import InternListSearcher, cycle_label, grad_eligible, pay_floor
from bot.search.posting_resolver import Resolution


class TestPay:
    @pytest.mark.parametrize("text,expected", [
        ("$23.75-$23.75 /hr", 23.75), ("$30-$50 /hr", 30.0), ("$1,200/week", 1200.0),
        ("N/A", None), ("", None), (None, None), ("Unpaid", 0.0), ("$0-$0 /hr", 0.0),
    ])
    def test_pay_floor(self, text, expected):
        assert pay_floor(text) == expected


class TestCycle:
    def test_summer_from_hire_time(self):
        assert cycle_label("Data Intern", "2027-Summer") == "Summer 2027"
        assert cycle_label("Data Intern", "2027-June") == "Summer 2027"     # the May/June bug
        assert cycle_label("Data Intern", "2026-Fall") is None

    def test_hire_time_without_season_falls_back_to_title(self):
        assert cycle_label("Summer 2027 Strategy Intern", "2027") == "Summer 2027"

    def test_spring_only_as_co_op(self):
        assert cycle_label("Supply Chain Co-op", "2027-January") == "Spring 2027 co-op"
        assert cycle_label("Spring 2027 Co-op - Operations", None) == "Spring 2027 co-op"
        assert cycle_label("Marketing Intern", "2027-Spring") is None       # spring internship

    def test_year_implied_by_posting_date(self):
        assert cycle_label("Summer Analyst", "Summer", posted="2026-09-28") == "Summer 2027"
        assert cycle_label("Summer Analyst", "Summer", posted="2025-09-28") is None

    def test_wrong_year_or_no_season(self):
        assert cycle_label("Summer 2026 Intern", None) is None
        assert cycle_label("Strategy Intern", None) is None
        assert cycle_label("Summer/Fall 2027 Co-op", None) == "Summer 2027"


class TestGradEligibility:
    @pytest.mark.parametrize("window,ok", [
        (None, True), ("", True),
        ("2027-December / 2028-June", True), ("2028-May", True), ("2027 / 2028", True),
        ("2027-December", False), ("2027-August", False), ("2029", False),
    ])
    def test_may_2028_graduate(self, window, ok):
        assert grad_eligible(window, 2028, 5) is ok


class FakeResolver:
    def __init__(self, answers):
        self.answers, self.calls, self.saved = answers, [], 0

    def resolve_detail(self, company, title, location="", jobright_url="", page=None):
        self.calls.append((company, title, location, jobright_url, page))
        return self.answers.get(company)

    def save(self):
        self.saved += 1


class TestResolution:
    def _searcher(self, tmp_path, resolver):
        searcher = InternListSearcher(categories=["pm"], cache_path=tmp_path / "c.json",
                                      resolver=resolver)
        rows = [
            {"Position Title": "PM Intern - Summer 2027", "Company": "A", "Salary": "$30 /hr",
             "Hire Time": "2027-Summer", "Location": "Austin, TX",
             "Apply": {"url": "https://jobright.ai/jobs/info/a1"}},
            {"Position Title": "PM Intern - Summer 2027", "Company": "B", "Salary": "$30 /hr",
             "Hire Time": "2027-Summer", "Apply": {"url": "https://jobright.ai/jobs/info/b2"}},
        ]
        searcher._category_views = lambda: {"pm": "embed"}
        searcher._read_view = lambda url: ({}, rows)
        searcher._row = lambda cols, r: {**r, "_id": r["Company"]}
        return searcher

    def test_resolved_url_replaces_jobright_link(self, tmp_path):
        resolver = FakeResolver({"A": Resolution("https://boards.greenhouse.io/a/jobs/1",
                                                 "board:greenhouse", "Posting text")})
        page = object()
        jobs = list(self._searcher(tmp_path, resolver).search(None, page=page))
        assert [j.apply_url for j in jobs] == ["https://boards.greenhouse.io/a/jobs/1",
                                               "https://jobright.ai/jobs/info/b2"]
        assert jobs[0].description.startswith("Posting text\n\nCycle: Summer 2027")
        assert jobs[1].description.startswith("Cycle: Summer 2027")
        assert resolver.calls[0] == ("A", "PM Intern - Summer 2027", "Austin, TX",
                                     "https://jobright.ai/jobs/info/a1", page)
        assert resolver.saved == 1

    def test_cache_saved_even_when_search_stops_early(self, tmp_path):
        resolver = FakeResolver({})
        gen = self._searcher(tmp_path, resolver).search(None)
        next(gen)
        gen.close()
        assert resolver.saved == 1

    def test_default_resolver_uses_given_cache_path(self, tmp_path):
        searcher = InternListSearcher(cache_path=tmp_path / "c.json")
        resolver = searcher._resolver()
        assert resolver.cache_path == tmp_path / "c.json"
        assert searcher._resolver() is resolver


class TestRowFilters:
    def test_leads_keep_only_summer_paid_eligible(self, tmp_path):
        searcher = InternListSearcher(categories=["pm"], cache_path=tmp_path / "c.json")
        rows = [
            {"Position Title": "PM Intern - Summer 2027", "Company": "A", "Salary": "$30-$40 /hr",
             "Hire Time": "2027-Summer", "Graduate Time": None},
            {"Position Title": "PM Intern", "Company": "B", "Salary": "N/A",
             "Hire Time": "2027-Summer", "Graduate Time": None},                 # unlisted pay
            {"Position Title": "PM Intern", "Company": "C", "Salary": "Unpaid",
             "Hire Time": "2027-Summer", "Graduate Time": None},                 # unpaid
            {"Position Title": "PM Intern", "Company": "D", "Salary": "$30 /hr",
             "Hire Time": "2026-Fall", "Graduate Time": None},                   # wrong cycle
            {"Position Title": "PM Intern", "Company": "E", "Salary": "$30 /hr",
             "Hire Time": "2027-Summer", "Graduate Time": "2027-December"},      # ineligible
        ]
        searcher._category_views = lambda: {"pm": "embed"}
        searcher._read_view = lambda url: ({}, rows)
        searcher._row = lambda cols, r: {**r, "_id": r["Company"]}
        assert [r["Company"] for r in searcher.leads()] == ["A"]
        searcher.include_unlisted_pay = True
        assert [r["Company"] for r in searcher.leads()] == ["A", "B"]


def test_make_searcher_reads_graduation_from_config():
    from bot.bot import _make_searcher
    config = SimpleNamespace(
        profile=SimpleNamespace(screening_answers={"graduation_date": "December 2027"}),
        bot=SimpleNamespace(intern_list_categories=["pm"], include_unlisted_pay=False))
    s = _make_searcher("intern_list", config)
    assert (s.grad_year, s.grad_month, s.categories) == (2027, 12, ("pm",))
