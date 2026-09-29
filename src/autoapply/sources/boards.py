"""Public job-board sources: Greenhouse, Lever and Ashby (docs/SPEC.md section 5.3).

Each provider reads the *public, unauthenticated* JSON job-board API of every company token listed in
``config.boards.<platform>`` while ``config.platforms.<platform>`` is on:

* Greenhouse ``GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`` (+ the board name
  from ``/v1/boards/{token}``),
* Lever ``GET https://api.lever.co/v0/postings/{token}?mode=json``,
* Ashby ``GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true``.

Behaviour that matters to callers:

* **Internships only.** A posting is kept when its title, department, team or employment type says intern /
  internship / co-op (or "summer analyst"-style wording), and its text mentions ``search.target_term`` or
  no other term at all. A different term in the title, or only other terms in the text, drops it.
* **Tolerant parsing.** Schemas drift and tenants are messy: every field is optional, ids may be numbers,
  HTML may be entity-escaped (Greenhouse) or double-escaped, dates may be ISO strings or epoch
  milliseconds. A malformed record is skipped (and logged at debug level), never fatal.
* **Isolation.** One token failing (404, timeout, invalid JSON, wrong shape) never loses the others. Only when
  *every* token of a platform failed does ``fetch`` raise ``BoardFetchError`` so ``ingest_all`` can show it;
  ``fetch_with_report`` exposes the per-token errors for callers that want them.
* **Polite and credential-free.** One shared ``httpx.Client`` (``ctx.http``; a private one is created and closed
  when the caller injected none), explicit timeouts, an identifying ``User-Agent``. Client-level
  ``Authorization`` / ``Cookie`` headers are stripped from every request; nothing secret is ever sent.
* **Safe values.** Tokens are validated before they reach a URL path, only ``http(s)`` links are accepted
  as posting URLs, descriptions are converted to plain text (scripts/styles dropped) and capped.
* **Fields.** ``ats``, ``source``, ``apply_url``, ``posted_date``, ``last_verified`` (fetch day), plain-text
  ``description``, ``term`` (the target term when the text names it) and an ``extra`` dict (board token, job
  id, department, team, employment type, offices) that the scorer reads for internship evidence. The company
  name is the board's own name when the API provides one (Greenhouse), else the prettified token.
"""

from __future__ import annotations

import html
import logging
import re
import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from autoapply.clock import local_day
from autoapply.config import AppConfig
from autoapply.contracts import OpportunityProvider, SourceContext
from autoapply.models import ATS, Opportunity, OpportunitySource, SearchProfile
from autoapply.normalize import canonical_url
from autoapply.scoring import find_terms, parse_target_term, signals_internship

__all__ = [
    "PROVIDERS",
    "AshbyProvider",
    "BoardFetchError",
    "BoardFetchReport",
    "GreenhouseProvider",
    "LeverProvider",
    "html_to_text",
    "prettify_token",
]

log = logging.getLogger("autoapply.sources.boards")

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
LEVER_API = "https://api.lever.co/v0/postings"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board"

USER_AGENT = "autoapply-internship-finder/0.1 (personal job search; public job-board APIs only)"
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_DESCRIPTION_CHARS = 20_000  # stored text cap per posting
_MAX_HTML_CHARS = 500_000  # never feed more than this to the HTML parser
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization")


class BoardFetchError(RuntimeError):
    """Every configured board token of one platform failed (nothing usable was fetched)."""


@dataclass(frozen=True)
class BoardFetchReport:
    """Result of ``fetch_with_report``: the opportunities plus a short error string per failed token."""

    opportunities: list[Opportunity]
    errors: dict[str, str] = field(default_factory=dict)
    attempted: int = 0  # tokens tried; ``len(errors) == attempted`` means the whole platform failed


# --------------------------------------------------------------------------------------------- values


def _text(value: object) -> str:
    """Stripped text of a JSON string or integer id; everything else (None, dicts, bools) -> ""."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return value.strip() if isinstance(value, str) else ""


def _first(*values: object) -> str:
    return next((t for v in values if (t := _text(v))), "")


def _http_url(value: object) -> str:
    """``value`` when it is an absolute http(s) URL with a host, else "" (blocks javascript:, data:, ...)."""
    url = _text(value)
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in {"http", "https"} and parts.netloc and " " not in url else ""


def _parse_date(value: object) -> date | None:
    """UTC calendar date of an ISO-8601 string, epoch seconds/milliseconds or digit string; else None."""
    if isinstance(value, bool):
        return None
    moment: datetime
    if isinstance(value, int | float):
        try:
            seconds = value / 1000 if abs(value) > 10_000_000_000 else float(value)
            moment = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return _parse_date(int(text)) if len(text) <= 16 else None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is not None:
            moment = moment.astimezone(UTC)
    else:
        return None
    return moment.date() if 2000 <= moment.year <= 2100 else None


# Paragraph-level tags are separated by a blank line, line-level tags by a single line break.
_PARAGRAPH_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote"]
_LINE_TAGS = ["div", "tr", "section", "article", "header", "footer"]
# Sentinel characters marking a line / paragraph break; real control characters are stripped from the input.
_LINE_BREAK = "\x00"
_PARA_BREAK = "\x01"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BREAK_RUN = re.compile(r"[\x00\x01]+")
_INLINE_SPACE = re.compile(r"[ \t\r\f\v\u00a0]+")


def _break_for(run: str) -> str:
    return "\n\n" if _PARA_BREAK in run else "\n"


def html_to_text(value: object) -> str:
    """Plain text of an HTML fragment, or "" for anything that is not text.

    Greenhouse ships its ``content`` HTML entity-escaped (``&lt;p&gt;``); some tenants escape twice. The text
    is unescaped, parsed with the stdlib ``html.parser`` (no scripts run; ``script`` / ``style`` / comments are
    dropped), paragraphs are separated by a blank line, other blocks and ``<br>`` by a line break, list items
    become "- " lines, and whitespace is normalised. The result is capped at ``MAX_DESCRIPTION_CHARS``.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    text = html.unescape(value[:_MAX_HTML_CHARS])
    if "<" not in text and ("&lt;" in text or "&gt;" in text):
        text = html.unescape(text)  # double-escaped tenant
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(_CONTROL_CHARS.sub(" ", text), "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "head"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before(f"{_LINE_BREAK}- ")
    for names, mark in ((_LINE_TAGS, _LINE_BREAK), (_PARAGRAPH_TAGS, _PARA_BREAK)):
        for block in soup.find_all(names):
            block.insert_before(mark)
            block.insert_after(mark)
    flat = _BREAK_RUN.sub(lambda m: _break_for(m.group(0)), soup.get_text(""))
    lines = [_INLINE_SPACE.sub(" ", line).strip() for line in flat.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    return "\n".join(cleaned).strip()[:MAX_DESCRIPTION_CHARS]


def prettify_token(token: str) -> str:
    """Company-name fallback: ``"acme-corp"`` -> ``"Acme Corp"`` (inner capitals such as SpaceX are kept)."""
    words = re.split(r"[-_.\s]+", token.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def _valid_tokens(raw: Iterable[object], platform: str) -> list[str]:
    """Configured tokens, stripped, de-duplicated (order kept) and validated for use in a URL path."""
    tokens: list[str] = []
    for item in raw:
        token = _text(item)
        if not token or token in tokens:
            continue
        if _TOKEN_RE.match(token):
            tokens.append(token)
        else:
            log.warning("ignoring invalid %s board token %r", platform, token[:40])
    return tokens


# --------------------------------------------------------------------------------------------- HTTP


def _new_client() -> httpx.Client:
    """The private client used only when the caller injected none (tests replace this function)."""
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,  # _get_json follows redirects itself, without credentials
    )


_MAX_REDIRECTS = 3


def _get_json(client: httpx.Client, url: str, params: Mapping[str, str] | None = None) -> Any:
    """GET ``url`` and decode JSON. Raises ``httpx.HTTPError`` (status, timeout, transport) or
    ``ValueError`` (invalid JSON).

    Credentials configured on the shared client (auth, ``Authorization`` / ``Cookie`` headers, cookie jar) are
    never sent: they are stripped from every request, and redirects (at most three, http(s) only) are followed
    here rather than by httpx because httpx re-adds the client's cookies to redirected requests.
    """
    target: str | httpx.URL = url
    query = params
    request: httpx.Request | None = None
    for _ in range(_MAX_REDIRECTS + 1):
        request = client.build_request(
            "GET",
            target,
            params=query,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        for name in _CREDENTIAL_HEADERS:
            request.headers.pop(name, None)
        response = client.send(request, auth=None, follow_redirects=False)
        if not (response.is_redirect and response.headers.get("location")):
            response.raise_for_status()
            return response.json()
        target = request.url.join(response.headers["location"])
        query = None
        if target.scheme not in {"http", "https"}:
            raise ValueError(f"redirect to unsupported scheme {target.scheme!r}")
    raise httpx.TooManyRedirects("too many redirects", request=request)


def _describe(exc: BaseException) -> str:
    """Short, secret-free description of a per-token failure."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return f"network error ({type(exc).__name__})"
    if isinstance(exc, ValueError):
        return f"invalid response ({str(exc)[:80] or type(exc).__name__})"
    return f"{type(exc).__name__}: {str(exc)[:80]}"


# --------------------------------------------------------------------------------------------- parsing


@dataclass
class _Raw:
    """One posting in the common shape shared by the three platform parsers."""

    job_id: str
    title: str
    url: str  # posting page
    apply_url: str  # application form
    location: str | None = None
    posted: date | None = None
    description: str = ""
    department: str = ""
    team: str = ""
    employment_type: str = ""  # raw text: "Intern", "Internship", "Full-time", ...
    company: str = ""  # only when the record itself names the company
    extra: dict[str, Any] = field(default_factory=dict)  # platform specifics, JSON-serialisable


def _collect(records: list[Any], parse: Callable[[Any], _Raw | None], platform: str) -> list[_Raw]:
    """Parse every record; a malformed one is skipped (debug log), never fatal."""
    parsed: list[_Raw] = []
    for record in records:
        try:
            raw = parse(record)
        except Exception:
            log.debug("skipping malformed %s record", platform, exc_info=True)
            continue
        if raw is not None:
            parsed.append(raw)
    return parsed


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _names(items: object, key: str = "name") -> list[str]:
    """Distinct non-empty names from a list of ``{"name": ...}`` objects (or plain strings)."""
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        name = _text(item.get(key)) if isinstance(item, dict) else _text(item)
        if name and name not in names:
            names.append(name)
    return names


def _drop_empty(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v not in ("", None, [], {})}


def _with_remote_flag(location: str, remote: bool) -> str:
    """Make a remote posting say so in its location ("New York, NY" -> "New York, NY (Remote)")."""
    if not remote:
        return location
    if not location:
        return "Remote"
    return location if "remote" in location.lower() else f"{location} (Remote)"


# ---- Greenhouse

_EMPLOYMENT_KEY = re.compile(r"employment|job type|position type|commitment|contract type", re.I)


def _greenhouse_employment_type(metadata: object) -> str:
    """Custom "Employment Type" style metadata entries some boards publish."""
    if not isinstance(metadata, list):
        return ""
    for entry in metadata:
        if not isinstance(entry, dict) or not _EMPLOYMENT_KEY.search(_text(entry.get("name"))):
            continue
        value = entry.get("value")
        if isinstance(value, list):
            value = ", ".join(t for v in value if (t := _text(v)))
        if text := _text(value):
            return text
    return ""


def _greenhouse_job(job: Any, token: str) -> _Raw | None:
    if not isinstance(job, dict):
        return None
    job_id, title = _text(job.get("id")), _text(job.get("title"))
    fallback = (
        f"https://boards.greenhouse.io/{quote(token, safe='')}/jobs/{job_id}" if job_id else ""
    )
    url = _http_url(job.get("absolute_url")) or fallback
    if not title or not url:
        return None
    place = job.get("location")
    location = _text(place.get("name")) if isinstance(place, dict) else _text(place)
    offices = _as_list(job.get("offices"))
    if not location:
        location = next(
            (
                t
                for o in offices
                if isinstance(o, dict) and (t := _first(o.get("location"), o.get("name")))
            ),
            "",
        )
    departments = _names(job.get("departments"))
    employment = _greenhouse_employment_type(job.get("metadata"))
    return _Raw(
        job_id=job_id,
        title=title,
        url=url,
        apply_url=url,  # the application form is embedded in the posting page
        location=location or None,
        posted=_parse_date(job.get("first_published")) or _parse_date(job.get("updated_at")),
        description=html_to_text(job.get("content")),
        department=departments[0] if departments else "",
        employment_type=employment,
        company=_text(job.get("company_name")),
        extra=_drop_empty(
            {
                "board_token": token,
                "job_id": job_id,
                "departments": departments,
                "offices": _names(offices),
                "requisition_id": _text(job.get("requisition_id")),
            }
        ),
    )


def parse_greenhouse_jobs(payload: Any, token: str) -> list[_Raw]:
    """Postings of a Greenhouse ``/jobs?content=true`` response. Raises ``ValueError`` on a wrong shape."""
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("unexpected Greenhouse response: expected an object with a 'jobs' list")
    return _collect(jobs, lambda job: _greenhouse_job(job, token), "greenhouse")


# ---- Lever


def _lever_description(posting: dict[str, Any]) -> str:
    parts = [_text(posting.get("descriptionPlain")) or html_to_text(posting.get("description"))]
    lists = posting.get("lists")
    for item in lists if isinstance(lists, list) else []:
        if isinstance(item, dict):
            heading = _text(item.get("text"))
            body = html_to_text(item.get("content"))
            parts.append("\n".join(p for p in (heading, body) if p))
    parts.append(_text(posting.get("additionalPlain")) or html_to_text(posting.get("additional")))
    return "\n\n".join(p for p in parts if p)[:MAX_DESCRIPTION_CHARS]


def _lever_posting(posting: Any, token: str) -> _Raw | None:
    if not isinstance(posting, dict):
        return None
    job_id, title = _text(posting.get("id")), _first(posting.get("text"), posting.get("title"))
    quoted = quote(token, safe="")
    url = _http_url(posting.get("hostedUrl")) or (
        f"https://jobs.lever.co/{quoted}/{job_id}" if job_id else ""
    )
    if not title or not url:
        return None
    apply_url = _http_url(posting.get("applyUrl")) or f"{url.rstrip('/')}/apply"
    cats = _as_dict(posting.get("categories"))
    location = _text(cats.get("location")) or ", ".join(_names(cats.get("allLocations")))
    remote = _text(posting.get("workplaceType")).lower() == "remote"
    return _Raw(
        job_id=job_id,
        title=title,
        url=url,
        apply_url=apply_url,
        location=_with_remote_flag(location, remote) or None,
        posted=_parse_date(posting.get("createdAt")),
        description=_lever_description(posting),
        department=_text(cats.get("department")),
        team=_text(cats.get("team")),
        employment_type=_text(cats.get("commitment")),
        extra=_drop_empty(
            {
                "board_token": token,
                "job_id": job_id,
                "workplace_type": _text(posting.get("workplaceType")),
            }
        ),
    )


def parse_lever_postings(payload: Any, token: str) -> list[_Raw]:
    """Postings of a Lever ``?mode=json`` response (a bare array). Raises ``ValueError`` on a wrong shape."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        payload = payload["data"]  # tolerate the paginated envelope
    if not isinstance(payload, list):
        raise ValueError("unexpected Lever response: expected a JSON array of postings")
    return _collect(payload, lambda posting: _lever_posting(posting, token), "lever")


# ---- Ashby


def _ashby_location(job: dict[str, Any]) -> str:
    """Primary location plus secondary ones ("Austin, TX; New York, NY")."""
    parts = [_text(job.get("location"))]
    secondary = job.get("secondaryLocations")
    for item in secondary if isinstance(secondary, list) else []:
        parts.append(_text(item.get("location")) if isinstance(item, dict) else _text(item))
    return "; ".join(dict.fromkeys(p for p in parts if p))


def _ashby_job(job: Any, token: str) -> _Raw | None:
    if not isinstance(job, dict) or job.get("isListed") is False:
        return None
    job_id, title = _text(job.get("id")), _text(job.get("title"))
    url = _http_url(job.get("jobUrl")) or (
        f"https://jobs.ashbyhq.com/{quote(token, safe='')}/{job_id}" if job_id else ""
    )
    if not title or not url:
        return None
    apply_url = _http_url(job.get("applyUrl")) or f"{url.rstrip('/')}/application"
    workplace = _text(job.get("workplaceType"))
    remote = job.get("isRemote") is True or workplace.lower() == "remote"
    comp = job.get("compensation")
    summary = (
        _first(comp.get("compensationTierSummary"), comp.get("scrapeableCompensationSalarySummary"))
        if isinstance(comp, dict)
        else ""
    )
    return _Raw(
        job_id=job_id,
        title=title,
        url=url,
        apply_url=apply_url,
        location=_with_remote_flag(_ashby_location(job), remote) or None,
        posted=_parse_date(job.get("publishedAt")),
        description=_text(job.get("descriptionPlain"))[:MAX_DESCRIPTION_CHARS]
        or html_to_text(job.get("descriptionHtml")),
        department=_text(job.get("department")),
        team=_text(job.get("team")),
        employment_type=_text(job.get("employmentType")),
        extra=_drop_empty(
            {
                "board_token": token,
                "job_id": job_id,
                "workplace_type": workplace,
                "compensation": summary,
            }
        ),
    )


def parse_ashby_jobs(payload: Any, token: str) -> list[_Raw]:
    """Postings of an Ashby job-board response. Unlisted postings are skipped. Raises ``ValueError`` on a
    wrong shape."""
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("unexpected Ashby response: expected an object with a 'jobs' list")
    return _collect(jobs, lambda job: _ashby_job(job, token), "ashby")


# --------------------------------------------------------------------------------------------- selection


def _is_internship(raw: _Raw) -> bool:
    """Title, department, team or employment type says intern / internship / co-op / summer analyst."""
    return any(
        signals_internship(text)
        for text in (raw.title, raw.department, raw.team, raw.employment_type)
    )


def _term_verdict(raw: _Raw, search: SearchProfile) -> tuple[bool, str | None]:
    """(keep, term). Keep unless the posting names other terms and never the target term; ``term`` is the
    configured target term when the title or description mentions it, else None."""
    target = parse_target_term(search.target_term)
    if target is None:
        return True, None
    title_terms = find_terms(raw.title)
    if title_terms and not any(t.matches(target) for t in title_terms):
        return False, None
    text_terms = find_terms(f"{raw.title}\n{raw.description}")
    if not text_terms:
        return True, None
    if any(t.matches(target) for t in text_terms):
        return True, search.target_term.strip() or target.label()
    return False, None


def _select(raws: Iterable[_Raw], search: SearchProfile) -> list[tuple[_Raw, str | None]]:
    """Postings that are internships for the target term (or for no stated term), with their term."""
    selected: list[tuple[_Raw, str | None]] = []
    for raw in raws:
        if not _is_internship(raw):
            continue
        keep, term = _term_verdict(raw, search)
        if keep:
            selected.append((raw, term))
    return selected


def _to_opportunity(
    raw: _Raw,
    term: str | None,
    *,
    company: str,
    source: OpportunitySource,
    ats: ATS,
    today: date,
) -> Opportunity:
    extra = dict(raw.extra)
    extra.update(
        _drop_empty(
            {
                "department": raw.department,
                "team": raw.team,
                "employment_type": raw.employment_type,
            }
        )
    )
    return Opportunity(
        company=company,
        title=raw.title,
        url=raw.url,
        apply_url=raw.apply_url,
        location=raw.location,
        term=term,
        source=source,
        ats=ats,
        is_open=True,  # it is listed on the live board right now
        posted_date=raw.posted,
        last_verified=today,
        description=raw.description or None,
        extra=extra,
    )


def _today(ctx: SourceContext) -> date:
    """The fetch day in the user's timezone (falls back to the UTC date for an unknown timezone)."""
    now = ctx.clock.now()
    try:
        return local_day(now, ctx.config.timezone)
    except (KeyError, ValueError, OSError):
        return now.date()


# --------------------------------------------------------------------------------------------- providers


class _BoardProvider:
    """Shared fetch loop; a platform supplies its endpoint (``load``) and, optionally, the board name."""

    name: str = ""
    source: OpportunitySource = OpportunitySource.MANUAL
    ats: ATS = ATS.UNKNOWN

    def configured_tokens(self, config: AppConfig) -> list[str]:
        """Valid, de-duplicated tokens configured for this platform (may be empty)."""
        return _valid_tokens(getattr(config.boards, self.name, []), self.name)

    def enabled(self, config: AppConfig) -> bool:
        """Platform toggle on AND at least one usable token."""
        return bool(getattr(config.platforms, self.name, False)) and bool(
            self.configured_tokens(config)
        )

    def load(self, client: httpx.Client, token: str) -> list[_Raw]:
        raise NotImplementedError

    def board_name(self, client: httpx.Client, token: str) -> str:
        """The board's own company name when the API offers one; "" otherwise (best effort, never raises)."""
        return ""

    def fetch(self, ctx: SourceContext) -> list[Opportunity]:
        """Internship opportunities of every configured token. A failing token is skipped and logged; raises
        ``BoardFetchError`` only when every token failed."""
        report = self.fetch_with_report(ctx)
        if report.attempted and len(report.errors) == report.attempted:
            detail = "; ".join(f"{token}: {why}" for token, why in report.errors.items())
            raise BoardFetchError(f"{self.name}: every configured board failed ({detail})")
        return report.opportunities

    def fetch_with_report(self, ctx: SourceContext) -> BoardFetchReport:
        """Like ``fetch`` but never raises for token failures: they are returned in ``errors``."""
        tokens = self.configured_tokens(ctx.config)
        if not tokens:
            return BoardFetchReport([], {}, 0)
        owns_client = ctx.http is None
        client = _new_client() if ctx.http is None else ctx.http
        found: list[Opportunity] = []
        errors: dict[str, str] = {}
        seen: set[str] = set()
        try:
            for token in tokens:
                try:
                    batch = self._fetch_token(client, token, ctx)
                except Exception as exc:  # isolation: one bad board never loses the others
                    errors[token] = _describe(exc)
                    ctx.log.warning("%s board %r skipped: %s", self.name, token, errors[token])
                    continue
                for op in batch:
                    if op.id not in seen:
                        seen.add(op.id)
                        found.append(op)
        finally:
            if owns_client:
                client.close()
        return BoardFetchReport(found, errors, len(tokens))

    def _fetch_token(
        self, client: httpx.Client, token: str, ctx: SourceContext
    ) -> list[Opportunity]:
        selected = _select(self.load(client, token), ctx.config.search)
        if not selected:
            return []
        board = self.board_name(client, token)
        today = _today(ctx)
        opportunities: list[Opportunity] = []
        seen_jobs: set[str] = set()
        seen_ids: set[str] = set()
        for raw, term in selected:
            job_key = raw.job_id or canonical_url(raw.apply_url)
            if job_key in seen_jobs:
                continue
            company = board or raw.company or prettify_token(token)
            try:
                op = _to_opportunity(
                    raw, term, company=company, source=self.source, ats=self.ats, today=today
                )
            except ValueError:  # pydantic validation failure of one odd record
                log.debug("skipping invalid %s posting %r", self.name, raw.job_id, exc_info=True)
                continue
            if op.id in seen_ids:
                continue
            seen_jobs.add(job_key)
            seen_ids.add(op.id)
            opportunities.append(op)
        ctx.log.debug(
            "%s board %r: %d internship posting(s) kept", self.name, token, len(opportunities)
        )
        return opportunities


class GreenhouseProvider(_BoardProvider):
    name = "greenhouse"
    source = OpportunitySource.GREENHOUSE
    ats = ATS.GREENHOUSE

    def load(self, client: httpx.Client, token: str) -> list[_Raw]:
        url = f"{GREENHOUSE_API}/{quote(token, safe='')}/jobs"
        return parse_greenhouse_jobs(_get_json(client, url, {"content": "true"}), token)

    def board_name(self, client: httpx.Client, token: str) -> str:
        try:
            data = _get_json(client, f"{GREENHOUSE_API}/{quote(token, safe='')}")
        except Exception:
            log.debug("greenhouse board name for %r unavailable", token, exc_info=True)
            return ""
        return _text(data.get("name"))[:200] if isinstance(data, dict) else ""


class LeverProvider(_BoardProvider):
    name = "lever"
    source = OpportunitySource.LEVER
    ats = ATS.LEVER

    def load(self, client: httpx.Client, token: str) -> list[_Raw]:
        url = f"{LEVER_API}/{quote(token, safe='')}"
        return parse_lever_postings(_get_json(client, url, {"mode": "json"}), token)


class AshbyProvider(_BoardProvider):
    name = "ashby"
    source = OpportunitySource.ASHBY
    ats = ATS.ASHBY

    def load(self, client: httpx.Client, token: str) -> list[_Raw]:
        url = f"{ASHBY_API}/{quote(token, safe='')}"
        return parse_ashby_jobs(_get_json(client, url, {"includeCompensation": "true"}), token)


PROVIDERS: list[OpportunityProvider] = [GreenhouseProvider(), LeverProvider(), AshbyProvider()]
