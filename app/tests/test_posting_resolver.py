"""Tests for bot.search.posting_resolver (all HTTP mocked)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

import bot.search.posting_resolver as pr
from bot.search.posting_resolver import (
    Board, Posting, PostingResolver, board_from_url, company_keys, location_score,
    match_posting, normalize_company, open_jobright_login, same_company, title_score,
    workday_tenants,
)


# --- fakes ------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status=200, data=None, text=None, url="", headers=None):
        self.status_code = status
        self._data = data
        self.text = text if text is not None else (json.dumps(data) if data is not None else "")
        self.url = url
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    """Routes (method, url-substring) -> FakeResp | callable; records calls."""

    def __init__(self, routes=None):
        self.headers = {"User-Agent": "python-requests/2.32"}
        self.routes = list((routes or {}).items())
        self.calls = []

    def add(self, key, resp):
        self.routes.insert(0, (key, resp))

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (want_method, fragment), resp in self.routes:
            if want_method == method and fragment in url:
                return resp(method, url, kwargs) if callable(resp) else resp
        return FakeResp(404, text="not found")


def _resolver(tmp_path, session, *, directory=None, simplify=None, vansh=None, **kwargs):
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    for name, data in (("directory", directory), ("simplify", simplify), ("vansh", vansh)):
        (sources / f"{name}.json").write_text(json.dumps(data or []), encoding="utf-8")
    kwargs.setdefault("jobright_browser", False)
    return PostingResolver(session=session, cache_path=tmp_path / "cache.json",
                           sources_dir=sources, interval=0, sleep=lambda s: None, **kwargs)


def _post(title, url=None, location=""):
    return Posting(title, url or f"https://x/{re.sub(r'\W+', '-', title)}{location}", location)


# --- names ------------------------------------------------------------------------


class TestNames:
    @pytest.mark.parametrize("raw,norm", [
        ("The TJX Companies, Inc.", "tjx"), ("Procter & Gamble", "procter and gamble"),
        ("Keurig Dr Pepper Inc.", "keurig dr pepper"), ("L'Oréal", "loreal"),
        ("KPMG Financial Reporting View (FRV)", "kpmg financial reporting view"),
    ])
    def test_normalize_company(self, raw, norm):
        assert normalize_company(raw) == norm

    def test_company_keys_variants(self):
        keys = company_keys("BerryDunn — Assurance, Tax and Consulting")
        assert "berrydunn" in keys
        assert "kpmg" in company_keys("KPMG Financial Reporting View (FRV)")
        assert "procterandgamble" in company_keys("Procter & Gamble")

    def test_same_company(self):
        assert same_company("Palantir Technologies", "Palantir")
        assert same_company("Southern Glazer's Wine & Spirits", "Southern Glazers Wine and Spirits")
        assert not same_company("Tetra Tech", "Tetra Pak")
        assert not same_company("", "X")

    def test_workday_tenants(self):
        assert workday_tenants("Keurig Dr Pepper Inc.") == ["keurigdrpepper", "kdp"]
        assert workday_tenants("BNSF Railway") == ["bnsfrailway", "bnsf"]
        assert workday_tenants("") == []


# --- title matching ---------------------------------------------------------------


class TestMatching:
    def test_exact_title_wins(self):
        board = [_post("Summer 2027 Internship - Data Analytics - Michigan"),
                 _post("Summer 2027 Internship - Data Analytics - Customer One - Illinois")]
        got = match_posting("Summer 2027 Internship - Data Analytics - Michigan", "Portage, MI", board)
        assert got.title.endswith("Michigan")

    def test_rewritten_title_matches(self):
        board = [_post("Product Manager Intern - Ads Interface and Platform"),
                 _post("Product Manager Intern - TikTok LIVE")]
        got = match_posting("Product Manager Intern (Ads Interface and Platform) - 2027 Summer", "", board)
        assert got.title.endswith("Platform")

    def test_ambiguous_or_unrelated_is_none(self):
        board = [_post("Marketing Intern, Brand"), _post("Marketing Intern, Growth")]
        assert match_posting("Marketing Intern", "", board) is None
        assert match_posting("Supply Chain Intern", "", board) is None

    def test_year_and_season_guards(self):
        assert title_score("Spring 2027 Co-op - Operations", "Fall 2027 Co-op - Operations") == 0
        assert title_score("Data Analyst Intern 2027", "Data Analyst Intern 2026") == 0
        assert match_posting("Operations Co-op", "", [_post("Operations Manager")]) is None

    def test_same_role_several_requisitions_picks_location(self):
        board = [_post("Distribution Operations Internship", location="Phoenix, AZ"),
                 _post("Distribution Operations Internship", location="Tucson, AZ")]
        got = match_posting("Distribution Operations Internship", "Tucson, AZ, United States", board)
        assert got.location == "Tucson, AZ"

    def test_location_in_title_is_ignored_but_must_agree(self):
        board = [_post("University - 2027 Summer Games Data Scientist Intern - McLean, VA", location="McLean, VA"),
                 _post("University, 2027 Summer Games Data Scientist Intern - Rome, NY", location="Rome, NY")]
        title = "University, 2027 Summer Games Data Scientist Intern"
        assert match_posting(title, "Rome, NY, United States", board).location == "Rome, NY"
        assert match_posting(title, "Honolulu, HI, United States", board) is None
        other_city = [_post("Capital Markets Summer 2027 Internship - San Francisco, CA", location="San Francisco, CA")]
        assert match_posting("Capital Markets Summer 2027 Internship - San Diego, CA",
                             "San Diego, CA", other_city) is None
        state_spelled = [_post("Inventory Analyst Intern", location="Minneapolis, Minnesota")]
        assert match_posting("Inventory Analyst Intern - Minneapolis, MN (Starting Summer, 2027)",
                             "Minneapolis, MN, United States", state_spelled) is not None

    def test_strict_threshold(self):
        board = [_post("Data Analyst Intern - Supply Chain")]
        assert match_posting("Data Analyst Intern", "", board) is not None
        assert match_posting("Data Analyst Intern", "", board, min_score=0.85) is None

    def test_page_title_suffix_ignored(self):
        assert title_score("Corporate Communications Summer Intern 2027 Job Details | BNSF",
                           "Corporate Communications Summer Intern 2027") == 1.0

    def test_location_score(self):
        assert location_score("Rome, NY", "Rome, New York") > 0.5
        assert location_score("", "Rome, NY") == 0


# --- board URLs -------------------------------------------------------------------


class TestBoardFromUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://boards.greenhouse.io/robinhood/jobs/1", Board("greenhouse", "robinhood")),
        ("https://job-boards.greenhouse.io/embed/job_app?for=Notion&token=1", Board("greenhouse", "notion")),
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", Board("greenhouse", "acme")),
        ("https://jobs.lever.co/palantir/abc", Board("lever", "palantir")),
        ("https://jobs.ashbyhq.com/ramp/123", Board("ashby", "ramp")),
        ("https://stryker.wd1.myworkdayjobs.com/en-US/StrykerCareers/job/X/Y_R1",
         Board("workday", "stryker", host="stryker.wd1.myworkdayjobs.com", site="StrykerCareers")),
        ("https://rb.wd5.myworkdayjobs.com/FRS?hiringCompany=1",
         Board("workday", "rb", host="rb.wd5.myworkdayjobs.com", site="FRS")),
        ("https://wd5.myworkdaysite.com/en-US/recruiting/guidewire/external/job/X",
         Board("workday", "guidewire", host="wd5.myworkdaysite.com", site="external")),
        ("https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26011",
         Board("oracle", "egug.fa.us2.oraclecloud.com", host="egug.fa.us2.oraclecloud.com", site="CX_1")),
        ("https://careers-harpercollins.icims.com/jobs/5471/x/job",
         Board("icims", "careers-harpercollins.icims.com", host="careers-harpercollins.icims.com")),
        ("https://jobs.smartrecruiters.com/RRDonnelley/7440", Board("smartrecruiters", "RRDonnelley")),
        ("https://apply.workable.com/altom-transport/j/3B7/", Board("workable", "altom-transport")),
        ("https://wavetronix.breezy.hr/p/1-x", Board("breezy", "wavetronix")),
        ("https://1x.recruitee.com/o/data", Board("recruitee", "1x")),
        ("https://ats.rippling.com/mikata/jobs/1", Board("rippling", "mikata")),
        ("https://jobs.jobvite.com/weisiger/job/owb", Board("jobvite", "weisiger")),
        ("https://www.amazon.jobs/en/jobs/1/x", Board("amazon", "amazon")),
        ("https://lifeattiktok.com/search/1", Board("tiktok", "tiktok")),
        ("https://www.icims.com/", None), ("https://example.com/careers", None), ("", None),
        ("https://stryker.wd1.myworkdayjobs.com/", None),
    ])
    def test_shapes(self, url, expected):
        assert board_from_url(url) == expected


# --- resolution flows ---------------------------------------------------------------


WORKDAY_DIR = [{"name": "Stryker", "slug": "stryker", "ats": "workday", "wd": "wd1",
                "site": "StrykerCareers"}]


def _workday_search(postings):
    return FakeResp(200, {"total": len(postings), "jobPostings": postings})


class TestResolve:
    def test_listing_match(self, tmp_path):
        simplify = [{"company_name": "TikTok", "title": "Data Science Intern - TikTok Product",
                     "url": "https://lifeattiktok.com/search/77", "terms": ["Summer 2027"],
                     "locations": ["San Jose, CA"], "active": True},
                    {"company_name": "TikTok", "title": "Data Science Intern - TikTok Product",
                     "url": "https://lifeattiktok.com/search/66", "terms": ["Summer 2026"]}]
        session = FakeSession()
        r = _resolver(tmp_path, session, simplify=simplify)
        got = r.resolve_detail("TikTok", "Data Science Intern (TikTok Product) - 2027 Summer",
                               "San Jose, CA", "https://jobright.ai/jobs/info/abc123?utm=x")
        assert (got.url, got.method) == ("https://lifeattiktok.com/search/77", "listing")
        assert session.calls == []

    def test_workday_directory_board(self, tmp_path):
        session = FakeSession({("POST", "stryker.wd1.myworkdayjobs.com/wday/cxs/stryker/StrykerCareers/jobs"):
                               _workday_search([{"title": "Summer 2027 Internship - Data Science - Remote",
                                                 "externalPath": "/job/Florida/Data-Science_R1",
                                                 "locationsText": "Florida"}])})
        r = _resolver(tmp_path, session, directory=WORKDAY_DIR)
        url = r.resolve("Stryker", "Summer 2027 Internship - Data Science - Remote", "Florida, United States")
        assert url == "https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Florida/Data-Science_R1"
        method, _, kwargs = session.calls[0]
        assert kwargs["json"]["searchText"].startswith("Summer 2027 Internship")
        assert "timeout" in kwargs

    def test_board_from_listing_urls_and_greenhouse_description(self, tmp_path):
        simplify = [{"company_name": "Robinhood", "title": "Software Engineer Intern",
                     "url": "https://boards.greenhouse.io/robinhood/jobs/1", "terms": ["Summer 2026"]}]
        jobs = {"jobs": [{"id": 9, "title": "Business Analyst Intern (Summer 2027)",
                          "absolute_url": "https://boards.greenhouse.io/robinhood/jobs/9",
                          "location": {"name": "Menlo Park"}, "content": "&lt;p&gt;Analyze&lt;/p&gt;"}]}
        session = FakeSession({("GET", "boards-api.greenhouse.io/v1/boards/robinhood/jobs"): FakeResp(200, jobs)})
        r = _resolver(tmp_path, session, simplify=simplify)
        got = r.resolve_detail("Robinhood", "Business Analyst Intern (Summer 2027)")
        assert got.url == "https://boards.greenhouse.io/robinhood/jobs/9"
        assert got.method == "board:greenhouse" and "Analyze" in got.description

    def test_discovery_via_careers_page(self, tmp_path):
        session = FakeSession({
            ("GET", "autocomplete.clearbit.com"): FakeResp(200, [
                {"name": "Federal Reserve Bank of San Francisco", "domain": "frbsf.org"}]),
            ("GET", "https://www.frbsf.org/work-with-us/careers/"): FakeResp(
                200, text='<a href="https://rb.wd5.myworkdayjobs.com/FRS?hiringCompany=1">Jobs</a>',
                url="https://www.frbsf.org/work-with-us/careers/"),
            ("GET", "https://www.frbsf.org/"): FakeResp(200, text='<a href="/work-with-us/careers/">Careers</a>',
                                                       url="https://www.frbsf.org/"),
            ("POST", "rb.wd5.myworkdayjobs.com/wday/cxs/rb/FRS/jobs"): _workday_search(
                [{"title": "2027 Summer Internship - Research", "externalPath": "/job/SF/Research_R1",
                  "locationsText": "San Francisco, CA"}]),
        })
        r = _resolver(tmp_path, session)
        got = r.resolve_detail("Federal Reserve Bank of San Francisco", "2027 Summer Internship - Research",
                               "San Francisco, CA")
        assert got.method == "discovered:workday"
        assert got.url == "https://rb.wd5.myworkdayjobs.com/FRS/job/SF/Research_R1"
        entry = r.cache["companies"]["federal reserve bank of san francisco"]
        assert entry["domain"] == "frbsf.org" and entry["boards"][0]["site"] == "FRS"

    def test_guessed_greenhouse_needs_matching_name(self, tmp_path):
        session = FakeSession({
            ("GET", "boards-api.greenhouse.io/v1/boards/tetrapak"): FakeResp(200, {"name": "Tetra Tech"}),
        })
        r = _resolver(tmp_path, session)
        assert r._guess_boards("Tetra Pak") == []
        session.add(("GET", "boards-api.greenhouse.io/v1/boards/tetrapak"), FakeResp(200, {"name": "Tetra Pak"}))
        assert r._guess_boards("Tetra Pak") == [Board("greenhouse", "tetrapak")]

    def test_guessed_lever_verified_by_page_title(self, tmp_path):
        session = FakeSession({
            ("GET", "api.lever.co/v0/postings/palantir"): FakeResp(200, [{"text": "x"}]),
            ("GET", "jobs.lever.co/palantir"): FakeResp(200, text="<title>Palantir Technologies</title>"),
        })
        assert _resolver(tmp_path, session)._guess_boards("Palantir") == [Board("lever", "palantir")]

    def test_workday_tenant_guess_from_robots(self, tmp_path):
        session = FakeSession({
            ("GET", "bnsf.wd1.myworkdayjobs.com/robots.txt"): FakeResp(
                200, text="Sitemap: https://bnsf.wd1.myworkdayjobs.com/BNSF_Agency/siteMap.xml\n"
                          "Sitemap: https://bnsf.wd1.myworkdayjobs.com/BNSF_Careers/siteMap.xml\n"),
            ("GET", "myworkdayjobs.com/robots.txt"): FakeResp(422, {"errorCode": "HTTP_422"}),
        })
        boards = _resolver(tmp_path, session)._guess_workday("BNSF Railway")
        assert [b.site for b in boards] == ["BNSF_Careers"] and boards[0].strict

    def test_workday_private_robots_tries_common_sites(self, tmp_path):
        session = FakeSession({
            ("GET", "kdp.wd1.myworkdayjobs.com/robots.txt"): FakeResp(401, {"errorCode": "HTTP_401"}),
            ("POST", "kdp.wd1.myworkdayjobs.com/wday/cxs/kdp/External/jobs"): _workday_search([]),
            ("POST", "myworkdayjobs.com/wday/cxs"): FakeResp(401, {}),
            ("GET", "myworkdayjobs.com/robots.txt"): FakeResp(422, {}),
        })
        boards = _resolver(tmp_path, session)._guess_workday("KDP")
        assert boards == [Board("workday", "kdp", host="kdp.wd1.myworkdayjobs.com", site="External", strict=True)]

    def test_unresolved_is_none(self, tmp_path):
        r = _resolver(tmp_path, FakeSession())
        assert r.resolve("Nobody Co", "Marketing Intern", "", "https://jobright.ai/jobs/info/zz") is None
        assert r.stats["unresolved"] == 1


class TestAdapters:
    def _one(self, tmp_path, session, board, title, location=""):
        return _resolver(tmp_path, session)._from_board(board, title, location)

    def test_oracle(self, tmp_path):
        data = {"items": [{"requisitionList": [{"Id": "26011605", "Title": "Product Development Intern",
                                                "PrimaryLocation": "New York"}]}]}
        session = FakeSession({("GET", "hcmRestApi/resources/latest/recruitingCEJobRequisitions"): FakeResp(200, data)})
        board = Board("oracle", "egug.fa.us2.oraclecloud.com", host="egug.fa.us2.oraclecloud.com", site="CX_1")
        got = self._one(tmp_path, session, board, "Product Development Intern")
        assert got.url == "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26011605"
        assert "siteNumber=CX_1" in session.calls[0][1]

    def test_icims(self, tmp_path):
        html = ('<a href="https://careers-h.icims.com/jobs/5446/2027-summer-internship/job?in_iframe=1" '
                'class="iCIMS_Anchor" title="5446 - 2027 Summer Internship - Business Analyst (NYC)">')
        session = FakeSession({("GET", "careers-h.icims.com/jobs/search"): FakeResp(200, text=html)})
        board = Board("icims", "careers-h.icims.com", host="careers-h.icims.com")
        got = self._one(tmp_path, session, board, "2027 Summer Internship - Business Analyst (NYC)")
        assert got.url == "https://careers-h.icims.com/jobs/5446/2027-summer-internship/job"

    def test_sapcsb(self, tmp_path):
        html = ('<a href="/job/Shelton-2027-Summer-Intern/1427/" class="jobTitle-link">'
                '2027 Summer Intern: Marketing - Commercial Analytics</a>')
        session = FakeSession({("GET", "careers.hubbell.com/search/"): FakeResp(200, text=html)})
        board = Board("sapcsb", "careers.hubbell.com", host="careers.hubbell.com")
        got = self._one(tmp_path, session, board, "2027 Summer Intern: Marketing - Commercial Analytics")
        assert got.url == "https://careers.hubbell.com/job/Shelton-2027-Summer-Intern/1427/"

    def test_phenom_prefers_real_ats_apply_url(self, tmp_path):
        ddo = {"eagerLoadRefineSearch": {"data": {"jobs": [
            {"title": "Marketing (B2B) Summer Intern 2027", "jobId": "95793",
             "applyUrl": "https://preview.sapsf.com/career?x=1", "cityStateCountry": "Fort Worth, TX"},
            {"title": "Finance Summer Intern 2027", "jobId": "95794",
             "applyUrl": "https://acme.wd1.myworkdayjobs.com/External/job/X/Finance_R1/apply"}]}}}
        page = f"<script>phApp.ddo = {json.dumps(ddo)}; phApp.sessionParams = {{}};</script>"
        session = FakeSession({("GET", "jobs.bnsf.com/us/en/search-results"): FakeResp(200, text=page)})
        board = Board("phenom", "jobs.bnsf.com", host="jobs.bnsf.com", site="us/en")
        got = self._one(tmp_path, session, board, "Marketing (B2B) Summer Intern 2027")
        assert got.url == "https://jobs.bnsf.com/us/en/job/95793/Marketing-B2B-Summer-Intern-2027"
        got = self._one(tmp_path, session, board, "Finance Summer Intern 2027")
        assert "myworkdayjobs.com" in got.url

    def test_jibe_turns_icims_login_into_posting(self, tmp_path):
        data = {"jobs": [{"data": {"title": "2027 Summer Intern: Supply Chain",
                                   "apply_url": "https://uscampus-pepsico.icims.com/jobs/461436/login",
                                   "full_location": "Purchase, New York"}}]}
        session = FakeSession({("GET", "www.pepsicojobs.com/api/jobs"): FakeResp(200, data)})
        board = Board("jibe", "www.pepsicojobs.com", host="www.pepsicojobs.com")
        got = self._one(tmp_path, session, board, "2027 Summer Intern: Supply Chain")
        assert got.url == "https://uscampus-pepsico.icims.com/jobs/461436/job"

    def test_tiktok_amazon_smartrecruiters(self, tmp_path):
        session = FakeSession({
            ("POST", "api.lifeattiktok.com"): FakeResp(200, {"data": {"job_post_list": [
                {"id": "767", "title": "Data Science Intern (TikTok Product) - 2027 Summer",
                 "city_info": {"en_name": "San Jose"}, "description": "About"}]}}),
            ("GET", "amazon.jobs/en/search.json"): FakeResp(200, {"jobs": [
                {"title": "Area Manager Intern - Summer 2027", "job_path": "/en/jobs/1/area"}]}),
            ("GET", "api.smartrecruiters.com/v1/companies/Acme/postings"): FakeResp(200, {"content": [
                {"id": "74", "name": "Marketing Intern", "location": {"city": "Austin", "region": "TX"}}]}),
        })
        r = _resolver(tmp_path, session)
        assert r._from_board(Board("tiktok", "tiktok"), "Data Science Intern (TikTok Product) - 2027 Summer",
                             "").url == "https://lifeattiktok.com/search/767"
        assert r._from_board(Board("amazon", "amazon"), "Area Manager Intern - Summer 2027",
                             "").url == "https://www.amazon.jobs/en/jobs/1/area"
        assert r._from_board(Board("smartrecruiters", "Acme"), "Marketing Intern",
                             "Austin, TX").url == "https://jobs.smartrecruiters.com/Acme/74"

    def test_listing_boards_fetched_once_per_run(self, tmp_path):
        session = FakeSession({("GET", "api.lever.co/v0/postings/acme"): FakeResp(200, [
            {"text": "Marketing Intern", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {}},
            {"text": "Finance Intern", "hostedUrl": "https://jobs.lever.co/acme/2", "categories": {}}])})
        r = _resolver(tmp_path, session)
        assert r._from_board(Board("lever", "acme"), "Marketing Intern", "").url.endswith("/1")
        assert r._from_board(Board("lever", "acme"), "Finance Intern", "").url.endswith("/2")
        assert len(session.calls) == 1


# --- cache, pacing, politeness ------------------------------------------------------------


class TestCache:
    def test_hits_and_misses_are_cached_with_ttl(self, tmp_path):
        clock = [1_000_000.0]
        session = FakeSession({("POST", "wday/cxs/stryker"): _workday_search(
            [{"title": "Data Intern", "externalPath": "/job/A/Data_R1"}])})
        r = _resolver(tmp_path, session, directory=WORKDAY_DIR, discover=False, now=lambda: clock[0])
        assert r.resolve("Stryker", "Data Intern", "", "https://jobright.ai/jobs/info/aa1") is not None
        assert r.resolve("Stryker", "Payroll Intern", "", "https://jobright.ai/jobs/info/bb2") is None
        r.save()
        calls = len(session.calls)
        again = _resolver(tmp_path, session, directory=WORKDAY_DIR, discover=False, now=lambda: clock[0])
        assert again.resolve_detail("Stryker", "Data Intern", "", "https://jobright.ai/jobs/info/aa1").method == "cache"
        assert again.resolve("Stryker", "Payroll Intern", "", "https://jobright.ai/jobs/info/bb2") is None
        assert len(session.calls) == calls                    # both answered from the cache
        clock[0] += pr.MISS_TTL + 1                           # misses expire first
        again.resolve("Stryker", "Payroll Intern", "", "https://jobright.ai/jobs/info/bb2")
        assert len(session.calls) > calls
        data = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
        assert data["leads"]["jobright:aa1"]["url"].endswith("Data_R1")

    def test_miss_after_network_error_not_cached(self, tmp_path):
        import requests

        def boom(method, url, kwargs):
            raise requests.ConnectionError("down")
        session = FakeSession({("POST", "wday/cxs"): boom})
        r = _resolver(tmp_path, session, directory=WORKDAY_DIR, discover=False)
        assert r.resolve("Stryker", "Data Intern") is None
        assert r.cache["leads"] == {}

    def test_save_prunes_expired(self, tmp_path):
        r = _resolver(tmp_path, FakeSession(), now=lambda: 10 * pr.HIT_TTL)
        r.cache["leads"] = {"old": {"url": "u", "checked": 0}, "new": {"url": "u", "checked": 10 * pr.HIT_TTL}}
        r.cache["companies"] = {"gone": {"boards": [], "checked": 0}}
        r.save()
        saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
        assert list(saved["leads"]) == ["new"] and saved["companies"] == {}

    def test_lead_key(self):
        assert PostingResolver.lead_key("A", "T", "https://jobright.ai/jobs/info/6a7c?utm=1") == "jobright:6a7c"
        assert PostingResolver.lead_key("The A Co.", "Data Intern!") == "a|data intern"

    def test_sources_downloaded_when_stale(self, tmp_path):
        session = FakeSession({("GET", "raw.githubusercontent.com/zshah101"): FakeResp(200, WORKDAY_DIR)})
        r = PostingResolver(session=session, cache_path=tmp_path / "c.json", sources_dir=tmp_path / "src",
                            interval=0, sleep=lambda s: None, use_listings=False, jobright_browser=False)
        assert r._known_boards("Stryker")[0].site == "StrykerCareers"
        assert (tmp_path / "src" / "directory.json").is_file()
        assert session.headers["User-Agent"].startswith("Mozilla/")    # not python-requests


class TestHttp:
    def test_paces_per_host_and_retries_429(self):
        sleeps = []
        responses = [FakeResp(429, text="slow down", headers={"Retry-After": "3"}), FakeResp(200, {"ok": 1})]
        session = SimpleNamespace(headers={}, request=lambda m, u, **k: responses.pop(0))
        http = pr._Http(session, interval=0.5, sleep=sleeps.append)
        assert http.json("GET", "https://a.example/x") == {"ok": 1}
        assert 3.0 in sleeps                                   # honoured Retry-After
        assert any(0 < s <= 0.5 for s in sleeps)               # paced the retry

    def test_challenge_blocks_host_for_the_run(self):
        calls = []

        def request(method, url, **kwargs):
            calls.append(url)
            return FakeResp(403, text="<html>Please complete the captcha</html>")
        http = pr._Http(SimpleNamespace(headers={}, request=request), interval=0)
        assert http.text("https://www.blocked.example/") is None
        assert http.text("https://www.blocked.example/careers") is None
        assert calls == ["https://www.blocked.example/"]


# --- optional jobright path (mocked browser; never logs in) ----------------------------


class FakeLocator:
    def __init__(self, href=None, fail=False):
        self.href, self.fail = href, fail
        self.first = self

    def wait_for(self, timeout=None):
        if self.fail:
            raise TimeoutError("not found")

    def get_attribute(self, name):
        return self.href

    def locator(self, selector):
        return SimpleNamespace(count=lambda: 0, get_attribute=lambda n: None)


class FakeTab:
    def __init__(self, url_after="https://jobright.ai/jobs/info/1", href=None, fail=False):
        self.url, self._after, self.closed = "", url_after, False
        self._locator = FakeLocator(href, fail)
        self.visited = []

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = self._after

    def get_by_text(self, pattern):
        assert pattern.search("Original Job Post")
        return self._locator

    def close(self):
        self.closed = True


class TestJobright:
    def _page(self, tab):
        return SimpleNamespace(context=SimpleNamespace(new_page=lambda: tab))

    def test_disabled_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv(pr.JOBRIGHT_ENV, raising=False)
        r = _resolver(tmp_path, FakeSession(), jobright_browser=None)
        assert r.jobright_browser is False
        tab = FakeTab(href="https://acme.wd1.myworkdayjobs.com/External/job/1")
        assert r.resolve("Nobody", "Intern", "", "https://jobright.ai/jobs/info/1", page=self._page(tab)) is None
        assert tab.visited == []

    def test_env_enables_and_reads_original_link(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pr.JOBRIGHT_ENV, "1")
        r = _resolver(tmp_path, FakeSession(), jobright_browser=None, discover=False)
        tab = FakeTab(href="https://acme.wd1.myworkdayjobs.com/External/job/1")
        got = r.resolve_detail("Nobody", "Intern", "", "https://jobright.ai/jobs/info/1", page=self._page(tab))
        assert (got.url, got.method) == ("https://acme.wd1.myworkdayjobs.com/External/job/1", "jobright")
        assert tab.closed

    def test_challenge_page_disables_lookup(self, tmp_path):
        r = _resolver(tmp_path, FakeSession(), jobright_browser=True, discover=False)
        tab = FakeTab(url_after="https://jobright.ai/_jr/security/challenge?return=x", href="https://a/1")
        assert r._jobright_original("https://jobright.ai/jobs/info/1", self._page(tab)) is None
        assert r._jobright_blocked

    def test_repeated_misses_disable_lookup(self, tmp_path):
        r = _resolver(tmp_path, FakeSession(), jobright_browser=True, discover=False)
        for _ in range(5):
            assert r._jobright_original("https://jobright.ai/jobs/info/1", self._page(FakeTab(fail=True))) is None
        assert r._jobright_blocked

    def test_jobright_link_to_itself_is_ignored(self, tmp_path):
        r = _resolver(tmp_path, FakeSession(), jobright_browser=True, discover=False)
        tab = FakeTab(href="/jobs/recommend")
        assert r._jobright_original("https://jobright.ai/jobs/info/1", self._page(tab)) is None

    def test_login_helper_opens_visible_profile_and_waits(self, tmp_path, monkeypatch):
        events = []

        class Page:
            def goto(self, url):
                events.append(("goto", url))

            def wait_for_event(self, name, timeout=None):
                events.append(("wait", name))
                context.pages.clear()                  # the user closed the window

        class Context:
            def __init__(self):
                self.pages = [Page()]

            def close(self):
                events.append(("close",))

        context = Context()

        class Chromium:
            def launch_persistent_context(self, **kwargs):
                events.append(("launch", kwargs["headless"], kwargs["user_data_dir"]))
                return context

        class PW:
            chromium = Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("bot.browser._find_system_chrome", lambda: None)
        open_jobright_login(tmp_path / "profile", playwright_factory=PW)
        assert events[0] == ("launch", False, str(tmp_path / "profile"))
        assert ("goto", pr.JOBRIGHT_HOME) in events and events[-1] == ("close",)
