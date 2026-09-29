"""Cross-source de-duplication of opportunities (docs/SPEC.md section 5.3).

Records are merged when they share an ``id`` (same canonical URL) or a ``fingerprint`` (same company, title and
city). Grouping is transitive (union-find), so the result does not depend on input order; only tie-breaks
between otherwise equal records fall back to it. One record per group survives:

* the "winner" is the one whose start URL is best: a known ATS host, then an employer-hosted careers page, then
  any other host, then a job-board aggregator (LinkedIn / Indeed ...), then no URL at all; ties go to the
  cleaner URL (no tracking parameters), the freshest ``last_verified``, the more trusted source, the more
  complete record, and finally input order;
* it keeps the freshest ``last_verified`` and gap-fills location / term / description / dates from the others;
* ``extra`` is the union (the winner's values win); ``alt_urls`` and ``merged_sources`` record what was merged.

A record without a city (fingerprint ``acme|product intern|``) joins the located group of the same company and
title when there is exactly one; if several cities exist it is ambiguous and stays separate.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from autoapply.models import ATS, Opportunity, OpportunitySource
from autoapply.normalize import canonical_url
from autoapply.sources.workbook import detect_ats, is_aggregator_url

# Lower is better. Only decides between records whose start URLs rank equally.
_SOURCE_RANK: dict[OpportunitySource, int] = {
    OpportunitySource.MANUAL: 0,
    OpportunitySource.WORKBOOK: 1,
    OpportunitySource.GREENHOUSE: 2,
    OpportunitySource.LEVER: 2,
    OpportunitySource.ASHBY: 2,
    OpportunitySource.LINKEDIN: 4,
    OpportunitySource.INDEED: 4,
}
_LIST_KEYS = ("alt_urls", "merged_sources")


def url_rank(url: str | None) -> int:
    """0 known ATS host, 1 employer-hosted, 2 other host, 3 aggregator (LinkedIn / Indeed ...), 4 no URL."""
    if not url or not url.strip():
        return 4
    if is_aggregator_url(url):
        return 3
    ats = detect_ats(url)
    if ats == ATS.CUSTOM:
        return 1
    return 2 if ats == ATS.UNKNOWN else 0


def _ats_rank(ats: ATS) -> int:
    return {ATS.UNKNOWN: 2, ATS.CUSTOM: 1}.get(ats, 0)


def _completeness(op: Opportunity) -> int:
    filled = (op.location, op.term, op.description, op.posted_date, op.last_verified, op.deadline)
    return sum(1 for value in filled if value)


def _noise(url: str) -> int:
    """Characters canonicalisation would strip (tracking parameters, ``/apply`` tails, locale, ``www.``)."""
    raw = url.strip()
    return max(0, len(raw) - len(canonical_url(raw))) if raw else 0


def _preference(op: Opportunity, index: int) -> tuple[int, int, int, int, int, int]:
    verified = op.last_verified.toordinal() if op.last_verified else 0
    start = op.start_url
    return (
        url_rank(start),
        _noise(start),
        -verified,
        _SOURCE_RANK.get(op.source, 3),
        -_completeness(op),
        index,
    )


def _city(fingerprint: str) -> str:
    return fingerprint.rsplit("|", 1)[-1]


def _company_title(fingerprint: str) -> str:
    return fingerprint.rsplit("|", 1)[0]


def _usable_fingerprint(fingerprint: str) -> bool:
    parts = fingerprint.split("|")
    return len(parts) == 3 and bool(parts[0]) and bool(parts[1])


class _Groups:
    """Minimal union-find over record indexes."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)  # the smallest index stays the root


def _group(items: Sequence[Opportunity]) -> list[list[int]]:
    groups = _Groups(len(items))
    by_key: dict[str, int] = {}
    for i, op in enumerate(items):
        for key in (
            f"id:{op.id}",
            f"fp:{op.fingerprint}" if _usable_fingerprint(op.fingerprint) else "",
        ):
            if key:
                if key in by_key:
                    groups.union(by_key[key], i)
                else:
                    by_key[key] = i
    _join_cityless(items, groups)
    members: dict[int, list[int]] = {}
    for i in range(len(items)):
        members.setdefault(groups.find(i), []).append(i)
    return [members[root] for root in sorted(members)]


def _join_cityless(items: Sequence[Opportunity], groups: _Groups) -> None:
    located: dict[str, set[int]] = {}
    cityless: dict[str, set[int]] = {}
    for i, op in enumerate(items):
        if not _usable_fingerprint(op.fingerprint):
            continue
        target = located if _city(op.fingerprint) else cityless
        target.setdefault(_company_title(op.fingerprint), set()).add(groups.find(i))
    for key, roots in cityless.items():
        located_roots = {groups.find(r) for r in located.get(key, ())}
        if len(located_roots) == 1:
            (anchor,) = located_roots
            for root in roots:
                groups.union(anchor, root)


def _earliest(values: Iterable[date | None]) -> date | None:
    present = [v for v in values if v]
    return min(present) if present else None


def _latest(values: Iterable[date | None]) -> date | None:
    present = [v for v in values if v]
    return max(present) if present else None


def _first(values: Iterable[Any]) -> Any:
    return next((v for v in values if v), None)


def _datetime_extreme(values: Iterable[datetime | None], pick: Any) -> datetime | None:
    present = [v for v in values if v]
    return pick(present) if present else None


def _merge_extra(ordered: Sequence[Opportunity]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for op in reversed(ordered):  # least preferred first, so the winner's values overwrite
        merged.update(copy.deepcopy(op.extra))
    for key in _LIST_KEYS:  # provenance lists are unioned rather than overwritten
        union: list[str] = []
        for op in ordered:
            for item in op.extra.get(key, ()) or ():
                if item not in union:
                    union.append(item)
        if union:
            merged[key] = union
    return merged


def _alt_urls(ordered: Sequence[Opportunity]) -> list[str]:
    winner = ordered[0]
    seen = {canonical_url(u) for u in (winner.url, winner.apply_url) if u}
    urls: list[str] = []
    for op in ordered[1:]:
        for url in (op.apply_url, op.url):
            key = canonical_url(url)
            if url and key not in seen:
                seen.add(key)
                urls.append(url)
    return urls


def _merge(ordered: Sequence[Opportunity]) -> Opportunity:
    """Merge records (best first) into a copy of the best one."""
    winner = ordered[0]
    extra = _merge_extra(ordered)
    urls = list(dict.fromkeys([*extra.get("alt_urls", []), *_alt_urls(ordered)]))
    if urls:
        extra["alt_urls"] = urls
    sources = sorted({op.source.value for op in ordered} | set(extra.get("merged_sources", [])))
    extra["merged_sources"] = sources

    posted = winner.posted_date or _earliest(op.posted_date for op in ordered)
    verified = _latest(op.last_verified for op in ordered)
    if verified is not None or posted is not None:
        extra.pop("date_unknown", None)
    if extra.get("term_assumed") and any(
        op.term and not op.extra.get("term_assumed") for op in ordered
    ):
        extra.pop("term_assumed")

    fingerprint = winner.fingerprint
    if not _city(fingerprint):  # prefer the fingerprint that names a city
        fingerprint = next((op.fingerprint for op in ordered if _city(op.fingerprint)), fingerprint)
    ats = min((op.ats for op in ordered), key=_ats_rank) if _ats_rank(winner.ats) else winner.ats
    return winner.model_copy(
        deep=True,
        update={
            "location": _first(op.location for op in ordered),
            "term": _first(op.term for op in ordered),
            "posted_date": posted,
            "last_verified": verified,
            "deadline": winner.deadline or _earliest(op.deadline for op in ordered),
            "description": max((op.description or "" for op in ordered), key=len) or None,
            "is_open": any(op.is_open for op in ordered),
            "ats": ats,
            "fingerprint": fingerprint,
            "first_seen": _datetime_extreme((op.first_seen for op in ordered), min),
            "last_seen": _datetime_extreme((op.last_seen for op in ordered), max),
            "score": _first(op.score for op in ordered),
            "extra": extra,
        },
    )


def dedupe(opps: Iterable[Opportunity]) -> list[Opportunity]:
    """Merge duplicates (same ``id``, then same ``fingerprint``) into one record each; see the module docstring.

    Never mutates its input. Output order follows the first appearance of each group. Idempotent:
    ``dedupe(dedupe(x)) == dedupe(x)``.
    """
    items = list(opps)
    result: list[Opportunity] = []
    for members in _group(items):
        if len(members) == 1:
            result.append(items[members[0]])
            continue
        ordered = [items[i] for i in sorted(members, key=lambda i: _preference(items[i], i))]
        result.append(_merge(ordered))
    return result
