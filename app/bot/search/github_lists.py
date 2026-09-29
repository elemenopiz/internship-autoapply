"""Internships from the community-maintained GitHub lists (direct application URLs).

Sources (public JSON, refreshed at most every LIST_TTL_HOURS):
  * SimplifyJobs/Summer2027-Internships — .github/scripts/listings.json
    (fields: company_name, title, url, locations, terms, category, active, ...)
  * vanshb03/Summer2027-Internships — .github/scripts/listings.json
    (same shape, with a "season" instead of "terms")

Unlike intern-list, every row already carries the employer's own application
URL, so no lookup is needed. Rows are kept when active and in a target cycle
(Summer 2027, or Spring 2027 co-ops); SimplifyJobs rows are further limited to
the Product and AI/ML/Data categories. Role scope, US/Canada location, and
scoring are applied by the bot loop as for every source.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Iterator

import requests

from bot.search.base import BaseSearcher, RawJob

logger = logging.getLogger(__name__)

SOURCES = {
    "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/HEAD/.github/scripts/listings.json",
    "vansh": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/HEAD/.github/scripts/listings.json",
}
#: SimplifyJobs categories matching the user's role families.
SIMPLIFY_CATEGORIES = frozenset({"Product", "Product Management", "AI/ML/Data",
                                 "Data Science, AI & Machine Learning"})
LIST_TTL_HOURS = 6
_CO_OP = re.compile(r"\bco-?\s?op\b|\bcoop\b", re.IGNORECASE)


def row_cycle(row: dict, source: str) -> str | None:
    """'Summer 2027' / 'Spring 2027 co-op' for rows in a target cycle, else None."""
    if source == "vansh":  # the repo is the 2027 list; season is bare ("Summer")
        season = str(row.get("season") or "")
        terms = [f"{s.strip()} 2027" for s in season.split("/") if s.strip()]
    else:
        terms = [str(t) for t in row.get("terms") or []]
    if "Summer 2027" in terms:
        return "Summer 2027"
    if "Spring 2027" in terms and _CO_OP.search(str(row.get("title", ""))):
        return "Spring 2027 co-op"
    return None


def rows_to_jobs(rows: list[dict], source: str) -> Iterator[RawJob]:
    for row in rows:
        if not row.get("active") or row.get("is_visible") is False:
            continue
        if source == "simplify" and row.get("category") not in SIMPLIFY_CATEGORIES:
            continue
        cycle = row_cycle(row, source)
        url = str(row.get("url") or "")
        if cycle is None or not url.startswith("http"):
            continue
        description = "\n".join(x for x in (
            f"Cycle: {cycle}",
            f"Category: {row.get('category')}" if row.get("category") else "",
            f"Sponsorship: {row.get('sponsorship')}" if row.get("sponsorship") else "",
            f"Degrees: {', '.join(row.get('degrees') or [])}" if row.get("degrees") else "",
        ) if x)
        yield RawJob(
            title=str(row.get("title") or ""), company=str(row.get("company_name") or ""),
            location="; ".join(str(x) for x in row.get("locations") or []), salary=None,
            description=description, apply_url=url, platform="github_list",
            external_id=f"{source}:{row.get('id')}",
            posted_at=str(row.get("date_posted") or "") or None)


class GitHubListsSearcher(BaseSearcher):
    def __init__(self, cache_dir: Path | None = None, session=None) -> None:
        if cache_dir is None:
            from config.settings import get_data_dir
            cache_dir = get_data_dir() / "profile" / "list_cache"
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()

    def _rows(self, source: str, url: str) -> list[dict]:
        """The source's rows, from a fresh-enough local copy or the network."""
        path = self.cache_dir / f"{source}.json"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < LIST_TTL_HOURS * 3600
        if not fresh:
            try:
                resp = self.session.get(url, timeout=60)
                resp.raise_for_status()
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(resp.text, encoding="utf-8")
            except (requests.RequestException, OSError) as exc:
                logger.warning("List %s not refreshed: %s", source, exc)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def search(self, criteria, page=None) -> Iterator[RawJob]:
        seen: set[str] = set()
        for source, url in SOURCES.items():
            for job in rows_to_jobs(self._rows(source, url), source):
                key = job.apply_url.split("?")[0].rstrip("/").lower()
                if key not in seen:  # the same posting often appears in both lists
                    seen.add(key)
                    yield job
