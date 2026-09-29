"""Tests for the public ATS job-board feed and cross-source URL dedup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bot.search.ats_boards import AtsBoardSearcher, board_from_url, normalize_url
from db.database import Database


class TestBoardFromUrl:
    def test_known_boards(self):
        assert board_from_url("https://job-boards.greenhouse.io/perpay/jobs/1") == ("greenhouse", "perpay")
        assert board_from_url("https://boards.greenhouse.io/acme/jobs/2?gh_src=x") == ("greenhouse", "acme")
        assert board_from_url("https://jobs.lever.co/CesiumAstro/uuid") == ("lever", "CesiumAstro")
        assert board_from_url("https://jobs.ashbyhq.com/notion/uuid") == ("ashby", "notion")

    def test_other_sites(self):
        assert board_from_url("https://careers.example.com/job/1") is None
        assert board_from_url("https://greenhouse.io/") is None


def _resp(payload, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = payload
    return r


GH = {"jobs": [{"id": 11, "title": "Strategy Intern - Summer 2027",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/11",
                "location": {"name": "Austin, TX"}, "content": "&lt;p&gt;Own &amp;amp; ship&lt;/p&gt;"},
               {"id": 12, "title": "Listed in workbook",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/12/",
                "location": {"name": "Remote"}, "content": ""}]}
LEVER = [{"id": "u1", "text": "Product Intern", "hostedUrl": "https://jobs.lever.co/beta/u1",
          "categories": {"location": "Dallas, TX"}, "descriptionPlain": "Summer 2027"}]
ASHBY = {"jobs": [{"id": "a1", "title": "Ops Intern", "location": "Remote",
                   "jobUrl": "https://jobs.ashbyhq.com/gamma/a1", "descriptionPlain": "d"},
                  {"id": "a2", "title": "Hidden", "isListed": False,
                   "jobUrl": "https://jobs.ashbyhq.com/gamma/a2"}]}


def test_search_parses_all_three_and_skips_workbook_postings(tmp_path):
    boards = tmp_path / "ats_boards.txt"
    boards.write_text("greenhouse:acme\nlever: beta  # comment\nashby:gamma\nbogus:x\n", encoding="utf-8")
    session = MagicMock()
    session.get.side_effect = lambda url, **kw: _resp(
        GH if "greenhouse" in url else LEVER if "lever" in url else ASHBY)
    workbook_job = MagicMock(apply_url="https://job-boards.greenhouse.io/acme/jobs/12",
                             company="Acme")
    with patch("bot.search.workbook.WorkbookSearcher.search", return_value=[workbook_job]):
        jobs = list(AtsBoardSearcher(boards, session=session).search(None))
    ids = {j.external_id for j in jobs}
    assert ids == {"greenhouse:acme:11", "lever:beta:u1", "ashby:gamma:a1"}
    gh = next(j for j in jobs if j.external_id == "greenhouse:acme:11")
    assert gh.description == "Own & ship" and gh.company == "Acme" and gh.platform == "ats_board"
    assert session.get.call_count == 3          # bogus ATS ignored, acme queried once


def test_unavailable_board_is_skipped(tmp_path):
    boards = tmp_path / "b.txt"
    boards.write_text("greenhouse:gone\nlever:ok\n", encoding="utf-8")
    session = MagicMock()
    session.get.side_effect = lambda url, **kw: _resp({}, 404) if "gone" in url else _resp(LEVER)
    with patch("bot.search.workbook.WorkbookSearcher.search", return_value=[]):
        jobs = list(AtsBoardSearcher(boards, session=session).search(None))
    assert [j.external_id for j in jobs] == ["lever:ok:u1"]


def test_normalize_url():
    assert normalize_url("https://X.com/a/?q=1#f") == "https://x.com/a"


def test_same_posting_from_another_source_is_done(tmp_path):
    db = Database(tmp_path / "t.db")
    db.record_application(
        external_id="R1", platform="workbook", job_title="Intern", company="Acme", location=None,
        salary=None, apply_url="https://job-boards.greenhouse.io/acme/jobs/11", match_score=70,
        resume_path=None, cover_letter_path=None, cover_letter_text=None, status="applied",
        error_message=None)
    assert db.is_done("greenhouse:acme:11", "ats_board",
                      apply_url="https://job-boards.greenhouse.io/acme/jobs/11/")
    assert not db.is_done("greenhouse:acme:99", "ats_board",
                          apply_url="https://job-boards.greenhouse.io/acme/jobs/99")
