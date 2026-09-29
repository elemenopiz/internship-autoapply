"""More postings from the public Greenhouse / Lever / Ashby job-board APIs.

Boards searched:
  1. every Greenhouse/Lever/Ashby company found in the internship workbook
     (a company with one listed internship often posts others), and
  2. data/profile/ats_boards.txt — one "greenhouse:<token>", "lever:<site>",
     or "ashby:<org>" per line (# comments allowed).

These are the boards' documented, unauthenticated JSON endpoints. Every posting
still goes through is_target_internship and scoring in the bot loop. Postings
already listed in the workbook are skipped here so a job is never queued twice.
"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests

from bot.search.base import BaseSearcher, RawJob

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")

GREENHOUSE_EMBED = "https://job-boards.greenhouse.io/embed/job_app?for={token}&token={job_id}"

_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


def board_from_url(url: str) -> tuple[str, str] | None:
    """('greenhouse', 'notion') from a posting URL, or None for other sites."""
    parsed = urlparse(url or "")
    host, parts = parsed.netloc.lower(), [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if host.endswith("greenhouse.io") and host.split(".")[0] in ("boards", "job-boards"):
        return "greenhouse", parts[0]
    if host == "jobs.lever.co":
        return "lever", parts[0]
    if host == "jobs.ashbyhq.com":
        return "ashby", parts[0]
    return None


def normalize_url(url: str) -> str:
    return (url or "").split("?")[0].split("#")[0].rstrip("/").lower()


def _plain(text: str) -> str:
    return " ".join(_TAG.sub(" ", html.unescape(html.unescape(text or ""))).split())


def _parse(ats: str, token: str, company: str, data) -> Iterator[RawJob]:
    if ats == "greenhouse":
        for job in (data or {}).get("jobs", []):
            url = job.get("absolute_url", "")
            if "greenhouse.io" not in url:
                # Company career sites embed the Greenhouse form in an iframe
                # (and some block headless browsers); go to the form directly.
                url = GREENHOUSE_EMBED.format(token=token, job_id=job.get("id"))
            yield RawJob(
                title=job.get("title", ""), company=company,
                location=(job.get("location") or {}).get("name", ""), salary=None,
                description=_plain(job.get("content", ""))[:6000],
                apply_url=url, platform="ats_board",
                external_id=f"greenhouse:{token}:{job.get('id')}", posted_at=job.get("updated_at"))
    elif ats == "lever":
        for job in data if isinstance(data, list) else []:
            cats = job.get("categories") or {}
            yield RawJob(
                title=job.get("text", ""), company=company, location=cats.get("location", ""),
                salary=None, description=(job.get("descriptionPlain") or "")[:6000],
                apply_url=job.get("hostedUrl", ""), platform="ats_board",
                external_id=f"lever:{token}:{job.get('id')}", posted_at=None)
    elif ats == "ashby":
        for job in (data or {}).get("jobs", []):
            if job.get("isListed") is False:
                continue
            yield RawJob(
                title=job.get("title", ""), company=company, location=job.get("location", ""),
                salary=None, description=(job.get("descriptionPlain") or "")[:6000],
                apply_url=job.get("jobUrl", ""), platform="ats_board",
                external_id=f"ashby:{token}:{job.get('id')}", posted_at=job.get("publishedAt"))


def _with_listed_urls(ats: str, data, jobs: Iterator[RawJob]):
    """Pair each parsed job with the URL the board LISTS (before any embed
    rewrite) — the URL the workbook would contain for the same posting."""
    listed = {}
    if ats == "greenhouse":
        listed = {str(j.get("id")): j.get("absolute_url", "") for j in (data or {}).get("jobs", [])}
    for job in jobs:
        job_id = job.external_id.rsplit(":", 1)[-1]
        yield job, listed.get(job_id, job.apply_url)


class AtsBoardSearcher(BaseSearcher):
    def __init__(self, boards_file: Path | None = None, session=None) -> None:
        if boards_file is None:
            from config.settings import get_data_dir
            boards_file = get_data_dir() / "profile" / "ats_boards.txt"
        self.boards_file = Path(boards_file)
        self.session = session or requests.Session()

    def _boards(self) -> tuple[dict[tuple[str, str], str], set[str]]:
        """{(ats, token): company name} and the workbook's own posting URLs."""
        boards: dict[tuple[str, str], str] = {}
        workbook_urls: set[str] = set()
        from bot.search.workbook import WorkbookSearcher
        for job in WorkbookSearcher().search(None, include_stale=True):
            workbook_urls.add(normalize_url(job.apply_url))
            board = board_from_url(job.apply_url)
            if board:
                boards.setdefault(board, job.company)
        if self.boards_file.is_file():
            for line in self.boards_file.read_text(encoding="utf-8").splitlines():
                line = line.split("#")[0].strip()
                if ":" in line:
                    ats, token = (s.strip() for s in line.split(":", 1))
                    if ats in _ENDPOINTS and token:
                        boards.setdefault((ats, token), token)
        return boards, workbook_urls

    def search(self, criteria, page=None) -> Iterator[RawJob]:
        boards, workbook_urls = self._boards()
        for (ats, token), company in sorted(boards.items()):
            url = _ENDPOINTS[ats].format(token=token)
            try:
                resp = self.session.get(url, timeout=_TIMEOUT,
                                        headers={"User-Agent": "internship-autoapply/1.0"})
                if resp.status_code != 200:
                    logger.info("Board %s:%s returned %s", ats, token, resp.status_code)
                    continue
                data = resp.json()
            except Exception as exc:
                logger.warning("Board %s:%s unavailable: %s", ats, token, exc)
                continue
            for job, listed_url in _with_listed_urls(ats, data, _parse(ats, token, company, data)):
                if job.apply_url and normalize_url(listed_url) not in workbook_urls:
                    yield job
