"""Internship leads from intern-list.com, resolved to applyable ATS postings.

intern-list.com publishes one Airtable shared view per category (?k=pm, da,
ba, mk, cst, psg, sc, ...). This searcher reads those views exactly as the
site's embedded table does (one request per category per run), then keeps only:
  * TARGET CYCLE   — Summer 2027, or a Spring 2027 co-op (see cycle_label)
  * PAID roles     — a listed pay above $0 (unlisted pay is skipped unless
                     include_unlisted_pay is set; explicit unpaid is always skipped)
  * ELIGIBLE roles — Graduate Time, when given, covers the candidate's graduation

Apply links on intern-list go to jobright.ai, which hides the original posting.
Each lead is therefore resolved to the employer's own posting by
bot.search.posting_resolver (public internship lists, the company's ATS boards
searched by title, careers-site discovery; cached in
data/profile/posting_resolution_cache.json). A hit yields the real posting URL
the appliers can use. Unresolved leads keep the jobright link and are recorded
by the bot as manual-apply leads.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterator

import requests

from bot.search.base import BaseSearcher, RawJob
from bot.search.posting_resolver import PostingResolver

logger = logging.getLogger(__name__)

HOME = "https://www.intern-list.com/"
DEFAULT_CATEGORIES = ("pm", "da", "ba", "mk", "cst", "psg", "sc")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140 Safari/537.36")
_TIMEOUT = 30

_SEASON_WORDS = {
    "summer": ("summer", "may", "june", "july"),
    "spring": ("spring", "january", "february", "march", "april", "jan", "feb"),
    "fall": ("fall", "autumn", "august", "september", "october", "november", "december"),
    "winter": ("winter",),
}
CO_OP = re.compile(r"\bco-?\s?op\b|\bcoop\b", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}


# --- row filters (pure) --------------------------------------------------------


def pay_floor(salary: str | None) -> float | None:
    """Lowest listed pay in dollars, 0.0 for unpaid, None when not listed."""
    text = (salary or "").strip().lower()
    if not text or text in ("n/a", "na", "none", "-"):
        return None
    if "unpaid" in text or "volunteer" in text:
        return 0.0
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    return min(nums) if nums else None


def _seasons(text: str) -> set[str]:
    low = (text or "").lower()
    return {season for season, words in _SEASON_WORDS.items()
            if any(re.search(rf"\b{w}\b", low) for w in words)}


def cycle_label(title: str, hire_time: str | None, posted: str | None = None) -> str | None:
    """'Summer 2027' or 'Spring 2027 co-op' when the listing is in a target
    cycle, else None.

    The season comes from Hire Time when it names one (it is the start date),
    else from the title. With no year anywhere, a listing posted Aug 2026 -
    May 2027 is taken to mean the 2027 cycle it is recruiting for.
    """
    hire, title = hire_time or "", title or ""
    seasons = _seasons(hire) or _seasons(title)
    if not seasons:
        return None
    years = set(re.findall(r"20\d\d", f"{hire} {title}"))
    if not years and posted and "2026-08" <= str(posted)[:7] <= "2027-05":
        years = {"2027"}
    if "2027" not in years:
        return None
    if "summer" in seasons:
        return "Summer 2027"
    if "spring" in seasons and (CO_OP.search(title) or CO_OP.search(hire)):
        return "Spring 2027 co-op"
    return None


def _grad_points(text: str) -> list[tuple[int, int, int]]:
    """'2027-December / 2028-June' -> [(2027,12,12), (2028,6,6)]; a bare year
    spans the whole year: '2028' -> (2028, 1, 12)."""
    points = []
    for part in re.split(r"[/,]", text or ""):
        m = re.search(r"(20\d\d)(?:\s*-\s*([A-Za-z]+))?", part)
        if not m:
            continue
        year, month = int(m.group(1)), (m.group(2) or "").lower()
        idx = _MONTHS.get(month) or next((v for k, v in _MONTHS.items() if k.startswith(month[:3])), 0) \
            if month else 0
        points.append((year, idx or 1, idx or 12))
    return points


def grad_eligible(graduate_time: str | None, grad_year: int, grad_month: int) -> bool:
    """True when the listing states no class-year window, or its window covers
    the candidate's graduation."""
    points = _grad_points(graduate_time or "")
    if not points:
        return True
    target = grad_year * 12 + grad_month
    lo = min(y * 12 + a for y, a, _ in points)
    hi = max(y * 12 + b for y, _, b in points)
    return lo <= target <= hi


# --- searcher -------------------------------------------------------------------


class InternListSearcher(BaseSearcher):
    """intern-list leads as RawJobs, with apply URLs resolved to the original
    posting where possible. `cache_path` is the resolution cache (default
    data/profile/posting_resolution_cache.json); `resolver` may be injected."""

    def __init__(self, categories=None, grad_year: int = 2028, grad_month: int = 5,
                 include_unlisted_pay: bool = False, cache_path: Path | None = None,
                 session=None, resolver: PostingResolver | None = None) -> None:
        self.categories = tuple(categories or DEFAULT_CATEGORIES)
        self.grad_year, self.grad_month = grad_year, grad_month
        self.include_unlisted_pay = include_unlisted_pay
        self.cache_path = Path(cache_path) if cache_path is not None else None
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _UA)
        self.resolver = resolver

    # -- intern-list views --

    def _category_views(self) -> dict[str, str]:
        html = self.session.get(HOME, timeout=_TIMEOUT).text
        views = {}
        for m in re.finditer(r'<h2[^>]*data-job-path="/us/[^"]*"[^>]*>', html):
            tag = m.group(0)
            link = re.search(r'airtable-link="([^"]+)"', tag)
            key = re.search(r'short-link="([^"]+)"', tag)
            if link and key:
                views.setdefault(key.group(1), link.group(1))
        return views

    def _read_view(self, embed_url: str) -> tuple[dict, list[dict]]:
        page = self.session.get(embed_url, timeout=_TIMEOUT).text
        m = re.search(r'urlWithParams:\s*"([^"]+)"', page)
        if not m:
            raise RuntimeError("shared view data URL not found")
        url = "https://airtable.com" + json.loads(f'"{m.group(1)}"')
        app_id = re.search(r"app[A-Za-z0-9]{14}", embed_url).group(0)
        resp = self.session.get(url, timeout=_TIMEOUT, headers={
            "x-airtable-application-id": app_id, "x-requested-with": "XMLHttpRequest",
            "x-time-zone": "America/Chicago", "x-user-locale": "en", "Referer": embed_url})
        resp.raise_for_status()
        table = resp.json().get("data", {}).get("table", {})
        return {c["id"]: c for c in table.get("columns", [])}, table.get("rows", [])

    @staticmethod
    def _row(columns: dict, row: dict) -> dict:
        out = {}
        for cid, value in row.get("cellValuesByColumnId", {}).items():
            col = columns.get(cid)
            if not col:
                continue
            choices = (col.get("typeOptions") or {}).get("choices") or {}
            if isinstance(value, str) and value in choices:
                value = choices[value].get("name", value)
            elif isinstance(value, list):
                value = [choices.get(v, {}).get("name", v) if isinstance(v, str) else v for v in value]
            out[col["name"]] = value
        out["_id"] = row.get("id", "")
        return out

    # -- resolution to the original posting --

    def _resolver(self) -> PostingResolver:
        if self.resolver is None:
            self.resolver = PostingResolver(cache_path=self.cache_path)
        return self.resolver

    # -- main --

    def leads(self) -> Iterator[dict]:
        """Filtered intern-list rows (summer, paid, eligible), deduplicated."""
        views = self._category_views()
        seen = set()
        for key in self.categories:
            if key not in views:
                logger.warning("intern-list category %r not found", key)
                continue
            try:
                columns, rows = self._read_view(views[key])
            except Exception as exc:
                logger.warning("intern-list %s unavailable: %s", key, exc)
                continue
            for raw in rows:
                row = self._row(columns, raw)
                title = str(row.get("Position Title") or "")
                company = str(row.get("Company") or "")
                ident = (company.lower(), title.lower())
                if not title or ident in seen:
                    continue
                seen.add(ident)
                pay = pay_floor(row.get("Salary"))
                if pay == 0.0 or (pay is None and not self.include_unlisted_pay):
                    continue
                cycle = cycle_label(title, row.get("Hire Time"), row.get("Date"))
                if cycle is None:
                    continue
                if not grad_eligible(row.get("Graduate Time"), self.grad_year, self.grad_month):
                    continue
                row["_category"], row["_cycle"] = key, cycle
                yield row

    def search(self, criteria, page=None) -> Iterator[RawJob]:
        """Leads as RawJobs. `page` (the bot's browser page) is only used by the
        optional jobright "Original Job Post" lookup (AUTOAPPLY_JOBRIGHT_BROWSER=1)."""
        resolver = self._resolver()
        try:
            for row in self.leads():
                title, company = str(row["Position Title"]), str(row.get("Company") or "")
                location = str(row.get("Location") or "")
                apply = row.get("Apply") or {}
                url = apply.get("url", "") if isinstance(apply, dict) else ""
                description = "\n".join(str(x) for x in (
                    f"Cycle: {row['_cycle']}",
                    f"Hire time: {row.get('Hire Time') or 'n/a'}",
                    f"Graduating class: {row.get('Graduate Time') or 'any'}",
                    f"Work model: {row.get('Work Model') or ''}",
                    row.get("Qualifications") or "") if x)
                found = resolver.resolve_detail(company, title, location, url, page=page) \
                    if company else None
                if found is not None:
                    url = found.url
                    if found.description:
                        description = f"{found.description}\n\n{description}"
                yield RawJob(
                    title=title, company=company, location=location,
                    salary=str(row.get("Salary") or "") or None,
                    description=description[:8000], apply_url=url, platform="intern_list",
                    external_id=str(row["_id"]), posted_at=str(row.get("Date") or "") or None)
        finally:
            resolver.save()
