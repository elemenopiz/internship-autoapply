"""Resolve intern-list leads to the employer's original posting URL.

intern-list.com links every lead to jobright.ai, which hides the employer's
own posting behind a login. This module finds that posting on the employer's
applicant-tracking system (ATS) with public, unauthenticated, read-only
requests. For one lead (company, title, location, jobright URL) the first hit
wins:

  1. Cache — data/profile/posting_resolution_cache.json keeps each lead's
     answer (hits for a week, misses for a day) and each company's discovered
     boards (two weeks), so reruns are fast.
  2. Public Summer 2027 internship lists (SimplifyJobs, vanshb03): their `url`
     is the direct ATS link; matched on company + title.
  3. The company's ATS boards, searched by title. Boards come from
       a. an employer -> ATS directory (zshah101 companies.json: Greenhouse /
          Lever / Ashby / Workday tenant+site / Oracle host+site / ...),
       b. every ATS URL the internship lists carry for the same company, and
       c. discovery, when (a)+(b) find no match: board tokens guessed from the
          name (verified against the board's own company name), and the
          company's careers site (domain from Clearbit's public autocomplete)
          scanned for ATS links and known career-site platforms.
     Supported boards: Greenhouse, Lever, Ashby (full listings); Workday (CXS
     search), Oracle Recruiting Cloud, iCIMS, SmartRecruiters, SAP
     SuccessFactors career sites, Jibe, Jobvite, amazon.jobs, TikTok (title
     search); Workable, Recruitee, Breezy, Rippling (full listings).
  4. Optional — the jobright page's "Original Job Post" link, read in the
     bot's persistent browser profile after the USER logged in to jobright
     (open_jobright_login()). Off unless AUTOAPPLY_JOBRIGHT_BROWSER=1.

Titles are matched with match_posting(): exact (normalized) titles first, else
a token-overlap score with year / season / internship guards, location as the
tie-breaker, and ambiguous matches rejected.

Politeness: at most ~2 requests/second per host, timeouts on every request,
listing downloads refreshed at most daily. A bot-detection or challenge
response marks that host unavailable for the rest of the run; it is never
worked around. Workday and TikTok searches are read-only POSTs of a search
form, exactly as their public career pages send them.
"""

from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse

import requests

from bot.search.ats_boards import _ENDPOINTS, _parse

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140 Safari/537.36")
_TIMEOUT = 20
HOST_INTERVAL = 0.5              # seconds between requests to one host (<= 2/s)
HIT_TTL = 7 * 86400
MISS_TTL = 86400
COMPANY_TTL = 14 * 86400
EMPTY_COMPANY_TTL = 3 * 86400
LISTINGS_TTL = 86400
DIRECTORY_TTL = 7 * 86400
MIN_SCORE = 0.6
STRICT_SCORE = 0.85            # boards guessed without a company-name check
JOBRIGHT_ENV = "AUTOAPPLY_JOBRIGHT_BROWSER"
JOBRIGHT_HOME = "https://jobright.ai/"
_JOBRIGHT_INTERVAL = 4.0
_MAX_CRAWL_PAGES = 8
_WD_PODS = ("wd1", "wd5", "wd3", "wd12", "wd501", "wd103")   # ~95% of Workday tenants

SOURCES = {
    "directory": "https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-"
                 "and-Fall-2026-Tech-Internships/HEAD/data/companies.json",
    "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/HEAD/"
                ".github/scripts/listings.json",
    "vansh": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/HEAD/"
             ".github/scripts/listings.json",
}
_SOURCE_TTL = {"directory": DIRECTORY_TTL, "simplify": LISTINGS_TTL, "vansh": LISTINGS_TTL}

# Hosts whose URLs are never "the original posting".
_NOT_ORIGINAL = ("jobright.ai", "simplify.jobs", "linkedin.com", "indeed.com", "glassdoor.",
                 "intern-list.com", "handshake")


# --- text normalization -------------------------------------------------------------


def _ascii(text: str | None) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


_CO_SUFFIX = re.compile(
    r"\b(the|inc|incorporated|llc|llp|lp|plc|pbc|corp|corporation|co|company|companies|ltd|"
    r"limited|pvt|private|group|holding|holdings|us|usa|na|sa|ag|gmbh|se|nv|bv)\b")


def normalize_company(name: str | None) -> str:
    """'The TJX Companies, Inc.' -> 'tjx'; 'Procter & Gamble' -> 'procter and gamble'."""
    s = _ascii(name).lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"['`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(_CO_SUFFIX.sub(" ", s).split())


def _index_keys(name: str | None) -> set[str]:
    key = normalize_company(name)
    return {k for k in (key, key.replace(" ", "")) if len(k) >= 2}


def company_keys(name: str | None) -> list[str]:
    """Lookup variants: the full name, the part before a separator
    ('BerryDunn — Assurance, Tax and Consulting' -> 'berrydunn'), a leading
    acronym ('KPMG Financial Reporting View' -> 'kpmg'), each also without spaces."""
    raw = (name or "").strip()
    keys = [normalize_company(raw)]
    for sep in (" — ", " – ", " - ", ",", " | ", ":", "("):
        if sep in raw:
            keys.append(normalize_company(raw.split(sep)[0]))
    first = raw.split()[0] if raw.split() else ""
    if re.fullmatch(r"[A-Z][A-Z&]{1,5}", first):
        keys.append(normalize_company(first))
    out: list[str] = []
    for key in keys:
        out += [key, key.replace(" ", "")]
    return [k for k in dict.fromkeys(out) if len(k) >= 2]


_NAME_TAIL = {"railway", "railroad", "bank", "insurance", "foods", "food", "financial",
              "services", "solutions", "technologies", "technology", "systems", "health",
              "healthcare", "energy", "industries", "international", "global", "brands",
              "partners", "consulting", "manufacturing", "retail", "communications", "labs"}


def workday_tenants(company: str | None) -> list[str]:
    """Likely Workday tenant ids: the squashed name, without a generic tail
    word ('BNSF Railway' -> bnsf), and the initials ('Keurig Dr Pepper' -> kdp)."""
    words = [w for w in normalize_company(company).split() if w != "and"]
    if not words:
        return []
    out = ["".join(words)]
    core = [w for w in words if w not in _NAME_TAIL]
    if core and core != words:
        out.append("".join(core))
    if len(words) >= 3:
        out.append("".join(w[0] for w in words if w not in ("of", "the")))
    return [t for t in dict.fromkeys(out) if 3 <= len(t) <= 30]


def same_company(a: str | None, b: str | None) -> bool:
    """Whether two spellings name the same employer (conservative)."""
    x, y = normalize_company(a), normalize_company(b)
    if not x or not y:
        return False
    if x == y or x.replace(" ", "") == y.replace(" ", ""):
        return True
    short, long_ = sorted((x, y), key=len)
    if len(short) >= 4 and (long_.startswith(short + " ") or long_.replace(" ", "") == short):
        return True
    xs, ys = set(x.split()), set(y.split())
    return len(xs & ys) / len(xs | ys) >= 0.67


_STOP = {"the", "and", "of", "a", "an", "for", "in", "to", "at", "with", "on", "or",
         "program", "programme", "intern", "interns", "internship", "internships", "coop",
         "summer", "fall", "autumn", "spring", "winter"}
_SEASON_ALIASES = {"summer": "summer", "fall": "fall", "autumn": "fall", "spring": "spring",
                   "winter": "winter"}
_EARLY = re.compile(r"intern|co-?\s?op\b|\bcoop\b|student|trainee|apprentic|summer|campus|"
                    r"graduate|fellow|scholar|placement", re.I)


def _words(title: str | None) -> list[str]:
    s = _ascii(title).lower().replace("&", " and ")
    s = re.sub(r"\s*\bjob (details|description)\s*\|.*$", "", s)   # scraped page-title suffix
    s = re.sub(r"\bco-?\s?op\b", " coop ", s)
    return re.findall(r"[a-z0-9]+", s)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def _flat(title: str | None) -> str:
    return " ".join(_words(title))


def title_core(title: str | None) -> set[str]:
    """Distinctive title words: no filler, years, seasons, or 'intern'."""
    return {_stem(w) for w in _words(title) if w not in _STOP and not re.fullmatch(r"20\d\d", w)}


def _years(title: str | None) -> set[str]:
    return set(re.findall(r"20\d\d", title or ""))


def _seasons(title: str | None) -> set[str]:
    return {_SEASON_ALIASES[w] for w in _words(title) if w in _SEASON_ALIASES}


def title_score(want: str, have: str, places: set[str] | frozenset = frozenset()) -> float:
    """1.0 for the same title; else core-word overlap (0 when the year or
    season contradicts, e.g. 'Summer 2027' vs 'Fall 2027' or '2026').
    `places` (city/state words) are ignored, so '... Intern' matches
    '... Intern - Rome, NY'; location then decides between requisitions."""
    if _flat(want) == _flat(have):
        return 1.0
    wy, hy = _years(want), _years(have)
    if wy and hy and not wy & hy:
        return 0.0
    ws, hs = _seasons(want), _seasons(have)
    if ws and hs and not ws & hs:
        return 0.0
    a, b = title_core(want), title_core(have)
    if places and (a - places) and (b - places):
        a, b = a - places, b - places
    if not a or not b:
        return 0.0
    score = len(a & b) / len(a | b)
    if len(a) >= 2 and a <= b and len(b - a) <= 2:      # posting title adds a detail or two
        score = max(score, MIN_SCORE)
    return score


_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}
_STATE_CODE = {name: code for code, name in _STATES.items()}
_STATE_NAME = re.compile(r"\b(" + "|".join(sorted(_STATE_CODE, key=len, reverse=True)) + r")\b")
_LOC_NOISE = {"united", "states", "america", "usa", "us", "multi", "multiple", "location",
              "locations", "remote", "hybrid", "onsite", "of", "the", "and"}
# Place words too common in titles to prove a location mismatch on their own.
_WEAK_PLACE = {"new", "north", "south", "east", "west", "san", "saint", "st", "fort", "lake",
               "city", "park", "port", "beach", "mount", "la", "los", "las", "el", "de",
               "center", "valley", "spring", "falls", "height", "island", "county", "global"}


def _place_words(text: str | None) -> set[str]:
    """{'rome', 'ny', 'new', 'york'} for 'Rome, NY' (state codes also spelled out)."""
    words = set()
    low = _ascii(text).lower()
    for part in re.split(r"[,\n;|/()-]", low):
        part = part.strip()
        if part in _STATES:                                  # 'MN' -> mn + minnesota
            words.update(_STATES[part].split())
        words.update(re.findall(r"[a-z]+", part))
    for m in _STATE_NAME.finditer(low):                      # 'Minnesota' -> + mn
        words.add(_STATE_CODE[m.group(0)])
    return words - _LOC_NOISE


def location_score(want: str | None, have: str | None) -> float:
    a, b = _place_words(want), _place_words(have)
    return len(a & b) / len(a) if a and b else 0.0


# --- postings and boards -----------------------------------------------------------


@dataclass
class Posting:
    title: str
    url: str
    location: str = ""
    description: str = ""


@dataclass
class Resolution:
    url: str
    method: str                # "listing", "board:workday", "jobright", "cache"
    description: str = ""


def match_posting(title: str, location: str, postings: list[Posting],
                  min_score: float = MIN_SCORE) -> Posting | None:
    """The posting that is clearly the lead's internship, or None.

    Same-titled postings (one role, several requisitions) are told apart by
    location; differently titled postings with the same score are ambiguous
    unless location separates them.
    """
    early = bool(_EARLY.search(title or ""))
    lead_places = {_stem(w) for w in _place_words(location)}
    lead_core = title_core(title)
    seen, scored = set(), []
    for post in postings:
        if not post.title or not post.url or post.url in seen:
            continue
        seen.add(post.url)
        if early and not _EARLY.search(post.title):
            continue
        if _flat(title) == _flat(post.title):
            score = 1.0
        else:
            post_places = {_stem(w) for w in _place_words(post.location)}
            places = lead_places | post_places
            named = (title_core(post.title) & places) - _WEAK_PLACE
            if lead_places and named - lead_places:
                continue                        # the posting names a different place
            if post_places and (lead_core & places) - _WEAK_PLACE - post_places - named:
                continue                        # the lead names a place the posting lacks
            score = title_score(title, post.title, places)
        if score >= min_score:
            scored.append((score, location_score(location, f"{post.location} {post.title}"), post))
    if not scored:
        return None
    scored.sort(key=lambda s: (-s[0], -s[1]))
    best = scored[0]
    rivals = [s for s in scored[1:] if s[0] == best[0]]
    if not rivals or all(_flat(s[2].title) == _flat(best[2].title) for s in rivals):
        return best[2]
    return best[2] if best[1] > max(s[1] for s in rivals) else None


@dataclass(frozen=True)
class Board:
    """One employer board: ats + token, plus host/site where the ATS needs them
    (Workday: host '{tenant}.wd5.myworkdayjobs.com', site 'External').
    `strict` boards were guessed without a company-name check and only accept
    near-exact titles."""

    ats: str
    token: str
    host: str = ""
    site: str = ""
    strict: bool = False

    @property
    def key(self) -> str:
        return ":".join((self.ats, self.token, self.host, self.site))

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict) -> "Board":
        return cls(data["ats"], data.get("token", ""), data.get("host", ""), data.get("site", ""),
                   bool(data.get("strict")))


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_GENERIC_SUB = {"www", "app", "api", "apply", "jobs", "careers", "cdn", "static", "login",
                "help", "support", "marketing", "blog", "developer", "developers", "community"}


def board_from_url(url: str | None) -> Board | None:
    """The ATS board a posting / careers URL belongs to, or None."""
    try:
        parsed = urlparse(_html.unescape(url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    first = parts[0] if parts else ""
    query = parse_qs(parsed.query)

    def ok(token: str) -> bool:
        return bool(token) and bool(_TOKEN.match(token)) and token.lower() not in _GENERIC_SUB

    if host.endswith("greenhouse.io") and ".eu." not in f".{host}":
        token = (query.get("for") or [""])[0]
        if not token and parts[:2] == ["v1", "boards"] and len(parts) > 2:
            token = parts[2]
        elif not token and first not in ("embed", "v1", "boards"):
            token = first
        return Board("greenhouse", token.lower()) if ok(token) else None
    if host == "jobs.lever.co":
        return Board("lever", first) if ok(first) else None
    if host == "api.lever.co" and parts[:2] == ["v0", "postings"] and len(parts) > 2:
        return Board("lever", parts[2])
    if host == "jobs.ashbyhq.com":
        return Board("ashby", first) if ok(first) else None
    if host == "api.ashbyhq.com" and parts[:2] == ["posting-api", "job-board"] and len(parts) > 2:
        return Board("ashby", parts[2])
    m = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m:
        rest = [p for p in parts if not _LOCALE.match(p)]
        if rest[:2] == ["wday", "cxs"] and len(rest) >= 4:
            return Board("workday", rest[2], host=host, site=rest[3])
        if rest and rest[0] not in ("wday",) and ok(rest[0]):
            return Board("workday", m.group(1), host=host, site=rest[0])
        return None
    if re.fullmatch(r"wd\d+\.myworkdaysite\.com", host):
        rest = [p for p in parts if not _LOCALE.match(p)]
        if rest[:1] in (["recruiting"], ["cxs"]) and len(rest) >= 3:
            return Board("workday", rest[1], host=host, site=rest[2])
        if rest[:2] == ["wday", "cxs"] and len(rest) >= 4:
            return Board("workday", rest[2], host=host, site=rest[3])
        return None
    if host.endswith(".oraclecloud.com"):
        m = re.search(r"/sites/([^/?#]+)", parsed.path)
        return Board("oracle", host, host=host, site=m.group(1)) if m else None
    if host.endswith(".icims.com") and host.count(".") == 2 and host.split(".")[0] not in _GENERIC_SUB:
        return Board("icims", host, host=host)
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        return Board("smartrecruiters", first) if ok(first) and first not in ("oneclick-ui", "sr-jobs") else None
    if host == "api.smartrecruiters.com" and parts[:2] == ["v1", "companies"] and len(parts) > 2:
        return Board("smartrecruiters", parts[2])
    if host == "apply.workable.com":
        return Board("workable", first) if ok(first) and first != "j" else None
    if host.endswith(".workable.com") and host.count(".") == 2:
        sub = host.split(".")[0]
        return Board("workable", sub) if ok(sub) else None
    for suffix, ats in ((".recruitee.com", "recruitee"), (".breezy.hr", "breezy")):
        if host.endswith(suffix):
            sub = host[: -len(suffix)]
            return Board(ats, sub) if ok(sub) and "." not in sub else None
    if host == "ats.rippling.com":
        return Board("rippling", first) if ok(first) else None
    if host in ("jobs.jobvite.com", "careers.jobvite.com"):
        return Board("jobvite", first) if ok(first) else None
    if host.endswith("amazon.jobs"):
        return Board("amazon", "amazon")
    if host.endswith("lifeattiktok.com") or host == "careers.tiktok.com":
        return Board("tiktok", "tiktok")
    return None


def _directory_board(entry: dict) -> Board | None:
    ats, slug = str(entry.get("ats") or ""), str(entry.get("slug") or "")
    if not slug:
        return None
    if ats == "workday":
        wd, site = entry.get("wd"), entry.get("site")
        return Board("workday", slug, host=f"{slug.lower()}.{wd}.myworkdayjobs.com", site=site) \
            if wd and site else None
    if ats == "oracle":
        host, site = entry.get("host") or slug, entry.get("site")
        return Board("oracle", host, host=host, site=site) if site else None
    if ats == "amazon":
        return Board("amazon", "amazon")
    if ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee",
               "breezy", "rippling", "jobvite"):
        return Board(ats, slug)
    return None


# Links on a careers page that point at an ATS.
_ATS_LINK = re.compile(
    r"https?://[^\s\"'<>\\]*?(?:greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|"
    r"myworkdaysite\.com|oraclecloud\.com|icims\.com|smartrecruiters\.com|workable\.com|"
    r"recruitee\.com|breezy\.hr|rippling\.com|jobvite\.com|amazon\.jobs|lifeattiktok\.com)"
    r"[^\s\"'<>\\]*", re.I)
_CAREER_WORDS = re.compile(r"career|jobs?\b|join[- ]?us|work[- ]with[- ]us|opportunit|intern|"
                           r"student|universit|campus|early[- ]career|open[- ]positions", re.I)
_CHALLENGE = re.compile(r"captcha|cf-chl|challenge-platform|/_jr/security/challenge|"
                        r"are you a robot|access denied|request unsuccessful", re.I)
_NOT_COMPANY_DOMAINS = ("linkedin.", "facebook.", "wikipedia.", "glassdoor.", "indeed.",
                        "crunchbase.", "bloomberg.", "youtube.", "twitter.", "x.com")


def _base_domain(host: str) -> str:
    labels = host.lower().split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# --- HTTP with per-host pacing ---------------------------------------------------------


class _Http:
    """requests wrapper: per-host minimum interval, timeouts, one retry on
    429/5xx, and a per-run block list for hosts that answer with a challenge."""

    def __init__(self, session, interval: float = HOST_INTERVAL,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.session = session
        self.interval = interval
        self.sleep = sleep
        self.errors = 0
        self.blocked: set[str] = set()
        self._next: dict[str, float] = {}

    def pace(self, host: str, interval: float | None = None) -> None:
        wait = self._next.get(host, 0.0) - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._next[host] = time.monotonic() + (self.interval if interval is None else interval)

    def request(self, method: str, url: str, **kwargs) -> requests.Response | None:
        host = (urlparse(url).hostname or "").lower()
        if host in self.blocked:
            return None
        kwargs.setdefault("timeout", _TIMEOUT)
        resp = None
        for attempt in range(2):
            self.pace(host)
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                logger.debug("%s %s failed: %s", method, url, exc)
                self.errors += 1
                return None
            status = getattr(resp, "status_code", 0)
            if status in (429, 502, 503, 504) and attempt == 0:
                retry = str((getattr(resp, "headers", None) or {}).get("Retry-After") or "2")
                self.sleep(min(float(retry) if retry.isdigit() else 2.0, 10.0))
                continue
            break
        status = getattr(resp, "status_code", 0)
        if status == 429 or status >= 500:
            self.errors += 1
        if status in (403, 429) and _CHALLENGE.search(str(getattr(resp, "text", ""))[:5000]):
            logger.info("%s answered with a bot challenge; skipping it for this run", host)
            self.blocked.add(host)
            return None
        return resp

    def json(self, method: str, url: str, **kwargs) -> Any:
        resp = self.request(method, url, **kwargs)
        if resp is None or resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def text(self, url: str, **kwargs) -> tuple[str, str] | None:
        """(final URL, body) of a 200 GET, else None."""
        resp = self.request("GET", url, **kwargs)
        if resp is None or resp.status_code != 200:
            return None
        return str(getattr(resp, "url", "") or url), resp.text or ""


# --- resolver --------------------------------------------------------------------------


def _jobright_enabled() -> bool:
    return os.environ.get(JOBRIGHT_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class PostingResolver:
    """Find the original posting URL for intern-list leads (see module docstring)."""

    def __init__(self, session=None, cache_path: Path | None = None,
                 sources_dir: Path | None = None, *, use_listings: bool = True,
                 use_directory: bool = True, discover: bool = True,
                 jobright_browser: bool | None = None, interval: float = HOST_INTERVAL,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time) -> None:
        if cache_path is None:
            from config.settings import get_data_dir
            cache_path = get_data_dir() / "profile" / "posting_resolution_cache.json"
        self.cache_path = Path(cache_path)
        self.sources_dir = Path(sources_dir) if sources_dir else self.cache_path.parent / "resolver_sources"
        if session is None:
            session = requests.Session()
        headers = getattr(session, "headers", None)
        if headers is not None and "python-requests" in str(headers.get("User-Agent", "python-requests")):
            headers["User-Agent"] = _UA                # requests' default UA is refused by many sites
        self.http = _Http(session, interval=interval, sleep=sleep)
        self.use_listings, self.use_directory, self.discover = use_listings, use_directory, discover
        self.jobright_browser = _jobright_enabled() if jobright_browser is None else jobright_browser
        self.now = now
        self.cache = self._load_cache()
        self._sources: dict[str, list] = {}
        self._dir_index: dict[str, list[Board]] | None = None
        self._list_index: dict[str, list[dict]] | None = None
        self._list_boards: dict[str, list[Board]] | None = None
        self._memo: dict[tuple[str, str], list[Posting]] = {}
        self._unsaved = 0
        self._jobright_blocked = False
        self._jobright_misses = 0
        self.stats: dict[str, int] = defaultdict(int)

    # -- cache --

    def _load_cache(self) -> dict:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("leads", {})
        data.setdefault("companies", {})
        return data

    def save(self) -> None:
        """Write the cache (expired entries dropped)."""
        now = self.now()
        leads = {k: v for k, v in self.cache["leads"].items()
                 if now - v.get("checked", 0) < (HIT_TTL if v.get("url") else MISS_TTL)}
        companies = {k: v for k, v in self.cache["companies"].items()
                     if now - v.get("checked", 0) < (COMPANY_TTL if v.get("boards") else EMPTY_COMPANY_TTL)}
        self.cache = {"leads": leads, "companies": companies}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
            os.replace(tmp, self.cache_path)
            self._unsaved = 0
        except OSError as exc:
            logger.warning("Posting resolution cache not saved: %s", exc)

    @staticmethod
    def lead_key(company: str, title: str, jobright_url: str = "") -> str:
        m = re.search(r"jobright\.ai/jobs/info/([0-9a-zA-Z]+)", jobright_url or "")
        if m:
            return f"jobright:{m.group(1)}"
        return f"{normalize_company(company)}|{_flat(title)}"

    # -- public API --

    def resolve(self, company: str, title: str, location: str = "",
                jobright_url: str = "", page=None) -> str | None:
        """The original posting URL for a lead, or None."""
        found = self.resolve_detail(company, title, location, jobright_url, page)
        return found.url if found else None

    def resolve_detail(self, company: str, title: str, location: str = "",
                       jobright_url: str = "", page=None) -> Resolution | None:
        """Like resolve(), with how it was found and the posting text (when the
        board provides it)."""
        key = self.lead_key(company, title, jobright_url)
        hit = self.cache["leads"].get(key)
        if hit and self.now() - hit.get("checked", 0) < (HIT_TTL if hit.get("url") else MISS_TTL):
            self.stats["cached"] += 1
            return Resolution(hit["url"], "cache") if hit.get("url") else None
        errors = self.http.errors
        try:
            found = self._resolve_fresh(company or "", title or "", location or "", jobright_url or "", page)
        except Exception as exc:                       # never break the search loop
            logger.warning("Resolving %r at %r failed: %s", title, company, exc)
            return None
        self.stats[found.method if found else "unresolved"] += 1
        if found or self.http.errors == errors:        # a miss after network errors is not cached
            self.cache["leads"][key] = {"url": found.url if found else None,
                                        "method": found.method if found else None,
                                        "checked": self.now()}
            self._unsaved += 1
            if self._unsaved >= 25:
                self.save()
        return found

    def _resolve_fresh(self, company: str, title: str, location: str,
                       jobright_url: str, page) -> Resolution | None:
        if not title or not company:
            return None
        if self.use_listings:
            post = self._from_listings(company, title, location)
            if post:
                return Resolution(post.url, "listing", post.description)
        searched: set[str] = set()
        for board in self._known_boards(company):
            searched.add(board.key)
            post = self._from_board(board, title, location)
            if post:
                return Resolution(post.url, f"board:{board.ats}", post.description)
        if self.discover:
            for board in self._discovered_boards(company):
                if board.key in searched:
                    continue
                searched.add(board.key)
                post = self._from_board(board, title, location)
                if post:
                    return Resolution(post.url, f"discovered:{board.ats}", post.description)
        if self.jobright_browser and page is not None and jobright_url:
            url = self._jobright_original(jobright_url, page)
            if url:
                return Resolution(url, "jobright")
        return None

    # -- public data sources (downloaded, cached on disk) --

    def _source(self, name: str) -> list:
        if name in self._sources:
            return self._sources[name]
        path = self.sources_dir / f"{name}.json"
        data = None
        fresh = path.is_file() and time.time() - path.stat().st_mtime < _SOURCE_TTL[name]
        if not fresh:
            resp = self.http.request("GET", SOURCES[name], timeout=90)
            if resp is not None and resp.status_code == 200:
                try:
                    data = resp.json()
                    self.sources_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data), encoding="utf-8")
                except (ValueError, OSError) as exc:
                    logger.warning("Resolver source %s unusable: %s", name, exc)
                    data = None
        if data is None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("Resolver source %s unreadable: %s", name, exc)
        if isinstance(data, dict):                         # tolerate {"companies": [...]}
            data = next((v for v in data.values() if isinstance(v, list)), [])
        self._sources[name] = data if isinstance(data, list) else []
        return self._sources[name]

    def _directory_index(self) -> dict[str, list[Board]]:
        if self._dir_index is None:
            index: dict[str, list[Board]] = defaultdict(list)
            for entry in self._source("directory") if self.use_directory else []:
                board = _directory_board(entry) if isinstance(entry, dict) else None
                if board:
                    for key in _index_keys(entry.get("name")):
                        index[key].append(board)
            self._dir_index = index
        return self._dir_index

    def _listing_rows(self) -> list[dict]:
        rows = []
        for name in ("simplify", "vansh"):
            rows += [r for r in self._source(name) if isinstance(r, dict)]
        return rows

    def _listing_index(self) -> dict[str, list[dict]]:
        if self._list_index is None:
            index: dict[str, list[dict]] = defaultdict(list)
            boards: dict[str, dict[str, list]] = defaultdict(dict)
            for row in self._listing_rows():
                name, url = row.get("company_name"), str(row.get("url") or "")
                if not name or not url.startswith("http") or row.get("is_visible") is False:
                    continue
                board = board_from_url(url)
                for key in _index_keys(name):
                    index[key].append(row)
                    if board:
                        slot = boards[key].setdefault(board.key, [board, 0])
                        slot[1] += 1
            self._list_index = index
            self._list_boards = {k: [b for b, _ in sorted(v.values(), key=lambda s: -s[1])]
                                 for k, v in boards.items()}
        return self._list_index

    def _from_listings(self, company: str, title: str, location: str) -> Posting | None:
        index = self._listing_index()
        rows, seen = [], set()
        for key in company_keys(company):
            for row in index.get(key, []):
                if id(row) not in seen:
                    seen.add(id(row))
                    rows.append(row)
        candidates = []
        for row in sorted(rows, key=lambda r: not r.get("active", True)):   # active first
            terms = " ".join(str(t) for t in (row.get("terms") or [row.get("season") or ""]))
            url = str(row["url"])
            if terms and "2027" not in terms and "2027" not in str(row.get("title")):
                continue
            if any(bad in url.lower() for bad in _NOT_ORIGINAL):
                continue
            candidates.append(Posting(str(row.get("title") or ""), url,
                                      "; ".join(str(x) for x in row.get("locations") or [])))
        return match_posting(title, location, candidates)

    def _known_boards(self, company: str) -> list[Board]:
        boards: dict[str, Board] = {}
        for key in company_keys(company):
            for board in self._directory_index().get(key, []):
                boards.setdefault(board.key, board)
        if self.use_listings:
            self._listing_index()
            for key in company_keys(company):
                for board in (self._list_boards or {}).get(key, []):
                    boards.setdefault(board.key, board)
        return [b for b in boards.values() if b.ats in _ADAPTERS]

    # -- discovery (cached per company) --

    def _discovered_boards(self, company: str) -> list[Board]:
        ckey = normalize_company(company) or company.lower()
        entry = self.cache["companies"].get(ckey)
        if entry:
            ttl = COMPANY_TTL if entry.get("boards") else EMPTY_COMPANY_TTL
            if self.now() - entry.get("checked", 0) < ttl:
                return [Board.from_dict(b) for b in entry.get("boards", [])]
        boards: dict[str, Board] = {}
        domain = None
        for board in self._guess_boards(company):
            boards.setdefault(board.key, board)
        if not boards:
            domain = self._company_domain(company)
            for board in self._careers_boards(domain) if domain else []:
                boards.setdefault(board.key, board)
        if not boards:
            for board in self._guess_workday(company):
                boards.setdefault(board.key, board)
        found = [b for b in boards.values() if b.ats in _ADAPTERS]
        self.cache["companies"][ckey] = {"boards": [b.to_dict() for b in found],
                                         "domain": domain, "checked": self.now()}
        self.stats["discovery"] += 1
        return found

    def _guess_boards(self, company: str) -> list[Board]:
        """Greenhouse / Lever / Ashby boards named after the company, kept only
        when the board's own company name matches."""
        words = normalize_company(company).split()
        if not words:
            return []
        found = []
        for token in dict.fromkeys(["".join(words), "-".join(words)]):
            info = self.http.json("GET", f"https://boards-api.greenhouse.io/v1/boards/{token}")
            if isinstance(info, dict) and same_company(info.get("name"), company):
                found.append(Board("greenhouse", token))
            for ats, check in (("lever", f"https://jobs.lever.co/{token}"),
                               ("ashby", f"https://jobs.ashbyhq.com/{token}")):
                data = self.http.json("GET", _ENDPOINTS[ats].format(token=token))
                jobs = data if ats == "lever" else (data or {}).get("jobs") if isinstance(data, dict) else None
                if not jobs:                         # no such board, or nothing posted
                    continue
                page = self.http.text(check)
                title = re.search(r"<title[^>]*>([^<]*)</title>", page[1], re.I) if page else None
                name = _html.unescape(title.group(1)) if title else ""
                if any(same_company(part, company)
                       for part in re.split(r"\s[-|@–—]\s|\bjobs\b|\bcareers\b", name, flags=re.I)):
                    found.append(Board(ats, token))
        return found

    def _company_domain(self, company: str) -> str | None:
        """The employer's web domain from Clearbit's public name autocomplete:
        a same-named suggestion, preferring a domain spelled like the name
        ('Henkel' -> henkel.com over henkel-adhesives.com)."""
        squashed = {k.replace(" ", "") for k in company_keys(company)}
        queries = [company] + [q for q in dict.fromkeys(
            [normalize_company(company)] + [normalize_company(company.split(sep)[0])
                                            for sep in (" - ", " — ", ",", "(") if sep in company])
            if q and q != company.lower()]
        for query in queries[:3]:
            data = self.http.json("GET", "https://autocomplete.clearbit.com/v1/companies/suggest",
                                  params={"query": query})
            best, best_rank = None, 0
            for item in data if isinstance(data, list) else []:
                domain = str(item.get("domain") or "").lower()
                if not domain or any(bad in domain for bad in _NOT_COMPANY_DOMAINS):
                    continue
                label = domain.split(".")[0].replace("-", "")
                rank = 2 if label in squashed else 1 if same_company(item.get("name"), company) else 0
                if rank > best_rank:
                    best, best_rank = domain, rank
            if best:
                return best
        return None

    def _guess_workday(self, company: str) -> list[Board]:
        """Workday tenants named after the company. A tenant's robots.txt lists
        its career sites; tenants that keep it private get a few common site
        names tried. Guessed boards are `strict`."""
        for tenant in workday_tenants(company):
            for pod in _WD_PODS:
                host = f"{tenant}.{pod}.myworkdayjobs.com"
                resp = self.http.request("GET", f"https://{host}/robots.txt")
                status = getattr(resp, "status_code", 0)
                if status == 200:
                    sites = re.findall(r"Sitemap:\s*https?://[^/\s]+/([^/\s]+)/siteMap\.xml",
                                       resp.text or "", re.I)
                    sites = [s for s in dict.fromkeys(sites)
                             if not re.search(r"agency|internal|contractor|vendor|preview|test", s, re.I)]
                    sites.sort(key=lambda s: not re.search(r"career|extern|intern|universit|campus|"
                                                          r"student|early|job", s, re.I))
                    return [Board("workday", tenant, host=host, site=s, strict=True) for s in sites[:4]]
                if status == 401:                          # tenant lives here; robots.txt private
                    for site in (tenant, "External", "Careers", f"{tenant}careers",
                                 "External_Careers", f"{tenant}_careers"):
                        data = self.http.json(
                            "POST", f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                            headers={"Accept": "application/json", "Content-Type": "application/json"})
                        if isinstance(data, dict) and "jobPostings" in data:
                            return [Board("workday", tenant, host=host, site=site, strict=True)]
                    return []
        return []

    def _careers_boards(self, domain: str) -> list[Board]:
        """Crawl a few pages of the company site for ATS links / platforms."""
        base = _base_domain(domain)
        queue = [f"https://www.{domain}/" if domain.count(".") == 1 else f"https://{domain}/",
                 f"https://careers.{base}/", f"https://jobs.{base}/", f"https://{domain}/careers"]
        seen: set[str] = set()
        boards: dict[str, Board] = {}
        pages = 0
        while queue and pages < _MAX_CRAWL_PAGES and not boards:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            pages += 1
            got = self.http.text(url)
            if not got:
                continue
            final, body = got
            body = _html.unescape(body[:2_000_000])
            for m in _ATS_LINK.finditer(body):
                board = board_from_url(m.group(0))
                if board:
                    boards.setdefault(board.key, board)
            platform = _site_platform(final, body)
            if platform:
                boards.setdefault(platform.key, platform)
            if boards:
                break
            links = []
            for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", body, re.I | re.S):
                href = urljoin(final, m.group(1).strip())
                label = re.sub(r"<[^>]+>", " ", m.group(2))[:80]
                host = (urlparse(href).hostname or "").lower()
                if not href.startswith("http") or href in seen:
                    continue
                related = _base_domain(host) == base or re.search(r"career|jobs", host)
                if related and _CAREER_WORDS.search(f"{urlparse(href).path} {label}"):
                    rank = 0 if re.search(r"career|jobs", host) else 1
                    rank += 0 if re.search(r"search|intern|student|universit|campus|open", href + label, re.I) else 1
                    links.append((rank, href))
            for _, href in sorted(links)[:4]:
                if href not in queue:
                    queue.append(href)
        return list(boards.values())

    # -- board search --

    def _from_board(self, board: Board, title: str, location: str) -> Posting | None:
        adapter = _ADAPTERS.get(board.ats)
        if adapter is None:
            return None
        kind, fn = adapter
        min_score = STRICT_SCORE if board.strict else MIN_SCORE
        if kind == "list":
            memo = (board.key, "")
            if memo not in self._memo:
                self._memo[memo] = fn(self, board, "") or []
            return match_posting(title, location, self._memo[memo], min_score)
        for query in _queries(title, board.ats):
            memo = (board.key, query)
            if memo not in self._memo:
                self._memo[memo] = fn(self, board, query) or []
            post = match_posting(title, location, self._memo[memo], min_score)
            if post:
                return post
        return None

    # listing boards (whole board fetched once per run)

    def _list_classic(self, board: Board, _query: str) -> list[Posting]:
        data = self.http.json("GET", _ENDPOINTS[board.ats].format(token=board.token))
        if data is None:
            return []
        return [Posting(j.title, j.apply_url, j.location, j.description)
                for j in _parse(board.ats, board.token, "", data)]

    def _list_workable(self, board: Board, _query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://apply.workable.com/api/v1/widget/accounts/{board.token}")
        out = []
        for job in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
            url = job.get("url") or (f"https://apply.workable.com/{board.token}/j/{job.get('shortcode')}/"
                                     if job.get("shortcode") else "")
            loc = ", ".join(str(job.get(k) or "") for k in ("city", "state", "country") if job.get(k))
            out.append(Posting(str(job.get("title") or ""), url, loc))
        return out

    def _list_recruitee(self, board: Board, _query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://{board.token}.recruitee.com/api/offers/")
        return [Posting(str(o.get("title") or ""), str(o.get("careers_url") or ""), str(o.get("location") or ""))
                for o in (data or {}).get("offers", [])] if isinstance(data, dict) else []

    def _list_breezy(self, board: Board, _query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://{board.token}.breezy.hr/json")
        return [Posting(str(j.get("name") or ""), str(j.get("url") or ""),
                        str((j.get("location") or {}).get("name") or ""))
                for j in data if isinstance(j, dict)] if isinstance(data, list) else []

    def _list_rippling(self, board: Board, _query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://ats.rippling.com/api/v2/board/{board.token}/jobs")
        items = (data or {}).get("items", []) if isinstance(data, dict) else []
        return [Posting(str(j.get("name") or ""), str(j.get("url") or ""),
                        "; ".join(str(l.get("name") or "") for l in j.get("locations") or []))
                for j in items if isinstance(j, dict)]

    # search boards (one request per distinct query)

    def _search_workday(self, board: Board, query: str) -> list[Posting]:
        data = self.http.json(
            "POST", f"https://{board.host}/wday/cxs/{board.token}/{board.site}/jobs",
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": query},
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        base = (f"https://{board.host}/recruiting/{board.token}/{board.site}"
                if "myworkdaysite" in board.host else f"https://{board.host}/{board.site}")
        return [Posting(str(j.get("title") or ""), base + str(j.get("externalPath") or ""),
                        str(j.get("locationsText") or ""))
                for j in (data or {}).get("jobPostings", []) or [] if j.get("externalPath")] \
            if isinstance(data, dict) else []

    def _search_oracle(self, board: Board, query: str) -> list[Posting]:
        words = re.sub(r"[\",;=]", " ", query).split()
        finder = (f"findReqs;siteNumber={board.site},limit=25,"
                  f"keyword=\"{' '.join(words)}\",sortBy=RELEVANCY")
        url = (f"https://{board.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&expand=requisitionList&finder={quote(finder, safe=';=,')}")
        data = self.http.json("GET", url)
        out = []
        for item in (data or {}).get("items", []) if isinstance(data, dict) else []:
            for req in item.get("requisitionList") or []:
                if req.get("Id"):
                    out.append(Posting(
                        str(req.get("Title") or ""),
                        f"https://{board.host}/hcmUI/CandidateExperience/en/sites/{board.site}/job/{req['Id']}",
                        str(req.get("PrimaryLocation") or "")))
        return out

    def _search_icims(self, board: Board, query: str) -> list[Posting]:
        got = self.http.text(f"https://{board.host}/jobs/search?ss=1&searchKeyword="
                             f"{quote_plus(query)}&in_iframe=1")
        out = []
        for tag in re.findall(r"<a\b[^>]*>", got[1] if got else ""):
            href = re.search(r'href="([^"]*/jobs/\d+/[^"]*)"', tag)
            title = re.search(r'title="([^"]*)"', tag)
            if href and title:
                url = _html.unescape(href.group(1)).split("?")[0]
                url = urljoin(f"https://{board.host}/", url)
                name = re.sub(r"^\s*\d+\s*-\s*", "", _html.unescape(title.group(1)))
                out.append(Posting(name, url))
        return out

    def _search_smartrecruiters(self, board: Board, query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://api.smartrecruiters.com/v1/companies/{board.token}/postings",
                              params={"q": query, "limit": 100})
        out = []
        for job in (data or {}).get("content", []) if isinstance(data, dict) else []:
            loc = job.get("location") or {}
            out.append(Posting(str(job.get("name") or ""),
                               f"https://jobs.smartrecruiters.com/{board.token}/{job.get('id')}",
                               ", ".join(str(loc.get(k)) for k in ("city", "region") if loc.get(k))))
        return out

    def _search_amazon(self, board: Board, query: str) -> list[Posting]:
        data = self.http.json("GET", "https://www.amazon.jobs/en/search.json",
                              params={"base_query": query, "result_limit": 20, "sort": "relevant"})
        return [Posting(str(j.get("title") or ""), "https://www.amazon.jobs" + str(j.get("job_path")),
                        str(j.get("normalized_location") or j.get("location") or ""))
                for j in (data or {}).get("jobs", []) if j.get("job_path")] \
            if isinstance(data, dict) else []

    def _search_tiktok(self, board: Board, query: str) -> list[Posting]:
        data = self.http.json(
            "POST", "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts",
            json={"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
                  "location_code_list": [], "keyword": query, "limit": 20, "offset": 0},
            headers={"Content-Type": "application/json", "Origin": "https://lifeattiktok.com",
                     "Referer": "https://lifeattiktok.com/", "website-path": "tiktok"})
        posts = ((data or {}).get("data") or {}).get("job_post_list", []) if isinstance(data, dict) else []
        return [Posting(str(j.get("title") or ""), f"https://lifeattiktok.com/search/{j.get('id')}",
                        str((j.get("city_info") or {}).get("en_name") or ""),
                        str(j.get("description") or "")[:6000])
                for j in posts if j.get("id")]

    def _search_sapcsb(self, board: Board, query: str) -> list[Posting]:
        """SAP SuccessFactors career-site builder ('/search/?q=' result table)."""
        got = self.http.text(f"https://{board.host}/search/?q={quote_plus(query)}")
        out = []
        for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", got[1] if got else "", re.S):
            attrs = m.group(1)
            href = re.search(r'href="([^"]+)"', attrs)
            if "jobTitle-link" in attrs and href:
                name = re.sub(r"<[^>]+>", " ", m.group(2))
                out.append(Posting(" ".join(_html.unescape(name).split()),
                                   urljoin(f"https://{board.host}/", _html.unescape(href.group(1)))))
        return out

    def _search_jibe(self, board: Board, query: str) -> list[Posting]:
        data = self.http.json("GET", f"https://{board.host}/api/jobs",
                              params={"keywords": query, "page": 1, "limit": 20})
        out = []
        for job in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
            info = job.get("data") or {}
            url = str(info.get("apply_url") or "")
            if re.search(r"icims\.com/jobs/\d+/login$", url):
                url = url[: -len("login")] + "job"           # the posting, not its sign-in step
            if not url and info.get("slug"):
                url = f"https://{board.host}/jobs/{info['slug']}"
            out.append(Posting(str(info.get("title") or ""), url,
                               str(info.get("full_location") or info.get("location_name") or "")))
        return out

    def _search_phenom(self, board: Board, query: str) -> list[Posting]:
        """Phenom career sites embed the first page of search results as JSON
        (phApp.ddo.eagerLoadRefineSearch) in the search-results page."""
        base = f"https://{board.host}/{board.site + '/' if board.site else ''}"
        got = self.http.text(f"{base}search-results?keywords={quote_plus(query)}")
        m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp\.", got[1] if got else "", re.S)
        try:
            data = json.loads(m.group(1)) if m else {}
        except ValueError:
            data = {}
        jobs = ((data.get("eagerLoadRefineSearch") or {}).get("data") or {}).get("jobs") or []
        out = []
        for job in jobs:
            title, job_id = str(job.get("title") or ""), job.get("jobId")
            if not job_id:
                continue
            apply = str(job.get("applyUrl") or "")
            ats = board_from_url(apply)
            # The ATS application URL when it is a real one, else the career-site page.
            url = apply if ats and ats.ats in ("workday", "icims", "greenhouse", "lever", "ashby",
                                               "oracle", "smartrecruiters") else \
                f"{base}job/{job_id}/{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')}"
            out.append(Posting(title, url, str(job.get("cityStateCountry") or job.get("location") or "")))
        return out

    def _search_jobvite(self, board: Board, query: str) -> list[Posting]:
        got = self.http.text(f"https://jobs.jobvite.com/{board.token}/search?q={quote_plus(query)}")
        out = []
        for m in re.finditer(rf'<a\b[^>]*href="(/{re.escape(board.token)}/job/[A-Za-z0-9]+)"[^>]*>(.*?)</a>',
                             got[1] if got else "", re.S):
            name = re.sub(r"<[^>]+>", " ", m.group(2))
            out.append(Posting(" ".join(_html.unescape(name).split()), "https://jobs.jobvite.com" + m.group(1)))
        return out

    # -- optional: jobright's own "Original Job Post" link (user-logged-in profile) --

    def _jobright_original(self, jobright_url: str, page) -> str | None:
        """Read the 'Original Job Post' link on the jobright page in the bot's
        browser profile. Works only after the user logged in to jobright
        themselves; a challenge page or repeated misses disable it for the run."""
        if self._jobright_blocked or "jobright.ai/" not in jobright_url:
            return None
        tab = None
        try:
            self.http.pace("jobright.ai", _JOBRIGHT_INTERVAL)
            tab = page.context.new_page()
            tab.goto(jobright_url, wait_until="domcontentloaded", timeout=30000)
            if _CHALLENGE.search(tab.url or ""):
                logger.warning("jobright answered with a challenge page; jobright lookups off for this run")
                self._jobright_blocked = True
                return None
            link = tab.get_by_text(re.compile(r"original\s+job\s+post", re.I)).first
            link.wait_for(timeout=8000)
            href = link.get_attribute("href") or ""
            if not href:
                anchor = link.locator("xpath=ancestor-or-self::a[1]")
                href = (anchor.get_attribute("href") if anchor.count() else "") or ""
            if not href:                              # a button that opens the posting
                with tab.context.expect_page(timeout=10000) as popup:
                    link.click()
                new = popup.value
                href = new.url
                new.close()
            url = urljoin(jobright_url, href)
            if url.startswith("http") and "jobright.ai" not in (urlparse(url).hostname or ""):
                self._jobright_misses = 0
                return url
        except Exception as exc:
            logger.debug("jobright original-post lookup failed for %s: %s", jobright_url, exc)
        finally:
            if tab is not None:
                try:
                    tab.close()
                except Exception as exc:
                    logger.debug("jobright tab close failed: %s", exc)
        self._jobright_misses += 1
        if self._jobright_misses >= 5:
            logger.info("No 'Original Job Post' links on jobright (not logged in?); run "
                        "`python -m bot.search.posting_resolver jobright-login` once. "
                        "jobright lookups off for this run.")
            self._jobright_blocked = True
        return None


def _site_platform(url: str, body: str) -> Board | None:
    """Career-site platforms recognized from page markup (not from links)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    if "j2w." in body and ("jobTitle-link" in body or "/go/" in body or "rmkcdn" in body):
        return Board("sapcsb", host, host=host)
    if len(re.findall(r"jibe", body, re.I)) >= 5:
        return Board("jibe", host, host=host)
    if "phApp" in body and re.search(r"phenom", body, re.I):
        m = re.search(r'"baseUrl"\s*:\s*"https?://' + re.escape(host) + r'/([^"]*)"', body)
        prefix = (m.group(1) if m else "").strip("/")
        return Board("phenom", host, host=host, site=prefix)
    return None


def _queries(title: str, ats: str) -> list[str]:
    """Search texts to try: the title, its distinctive words, then 'intern'
    (for keyword engines that AND every word)."""
    clean = " ".join(re.sub(r"[^A-Za-z0-9&+#/.' -]+", " ", _ascii(title)).split())[:120]
    core = " ".join(w for w in _words(title) if w not in _STOP and not re.fullmatch(r"20\d\d", w))[:80]
    plan = [clean, core]
    if ats not in ("amazon", "tiktok", "smartrecruiters"):
        plan.append("intern")
    return [q for q in dict.fromkeys(plan) if q]


_ADAPTERS: dict[str, tuple[str, Callable]] = {
    "greenhouse": ("list", PostingResolver._list_classic),
    "lever": ("list", PostingResolver._list_classic),
    "ashby": ("list", PostingResolver._list_classic),
    "workable": ("list", PostingResolver._list_workable),
    "recruitee": ("list", PostingResolver._list_recruitee),
    "breezy": ("list", PostingResolver._list_breezy),
    "rippling": ("list", PostingResolver._list_rippling),
    "workday": ("search", PostingResolver._search_workday),
    "oracle": ("search", PostingResolver._search_oracle),
    "icims": ("search", PostingResolver._search_icims),
    "smartrecruiters": ("search", PostingResolver._search_smartrecruiters),
    "amazon": ("search", PostingResolver._search_amazon),
    "tiktok": ("search", PostingResolver._search_tiktok),
    "sapcsb": ("search", PostingResolver._search_sapcsb),
    "jibe": ("search", PostingResolver._search_jibe),
    "phenom": ("search", PostingResolver._search_phenom),
    "jobvite": ("search", PostingResolver._search_jobvite),
}


# --- jobright login helper (the USER logs in; nothing is typed for them) -------------


def open_jobright_login(profile_dir: Path | None = None, url: str = JOBRIGHT_HOME,
                        playwright_factory=None) -> None:
    """Open the bot's browser profile in a visible window at jobright.ai so the
    user can log in themselves; returns when the window is closed.

    Close the bot first: a Chromium profile can only be open once. Afterwards,
    set AUTOAPPLY_JOBRIGHT_BROWSER=1 to let the resolver read "Original Job
    Post" links in that profile.
    """
    if profile_dir is None:
        from config.settings import get_data_dir
        profile_dir = get_data_dir() / "browser_profile"
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright
        playwright_factory = sync_playwright
    from bot.browser import _find_system_chrome

    kwargs: dict[str, Any] = dict(
        user_data_dir=str(profile_dir), headless=False, viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled", "--no-first-run",
              "--no-default-browser-check"],
        ignore_default_args=["--enable-automation"])
    chrome = _find_system_chrome()
    if chrome:
        kwargs["executable_path"] = chrome             # same browser as BrowserManager
    with playwright_factory() as pw:
        context = pw.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url)
        logger.info("Log in to jobright.ai in the opened window, then close the window.")
        try:
            while context.pages:
                context.pages[0].wait_for_event("close", timeout=0)
        finally:
            try:
                context.close()
            except Exception as exc:
                logger.debug("Browser close failed: %s", exc)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m bot.search.posting_resolver")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("jobright-login", help="open the bot's browser profile so you can log in to jobright.ai")
    one = sub.add_parser("resolve", help="resolve one lead")
    one.add_argument("company")
    one.add_argument("title")
    one.add_argument("--location", default="")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.cmd == "jobright-login":
        print("A browser window will open. Log in to jobright.ai yourself, then close the window.")
        open_jobright_login()
        return 0
    resolver = PostingResolver()
    found = resolver.resolve_detail(args.company, args.title, args.location)
    resolver.save()
    print(f"{found.method}: {found.url}" if found else "unresolved")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
