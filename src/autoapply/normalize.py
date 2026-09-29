"""URL / text normalisation used for dedup keys and idempotency.

Pure functions, no I/O. CONTRACT FILE: owned by the orchestrator. Everything that decides "is this the same
posting?" (ingest dedup, the never-apply-twice guard, the DB primary key) must go through these helpers so the
answers agree everywhere.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM = re.compile(
    r"^(utm_.*|gh_src|lever-(source|origin)|fbclid|gclid|mc_(cid|eid)|ref|referrer|src|source|trk|"
    r"trackingid|refid|_ga|icid|sid)$",
    re.IGNORECASE,
)
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")
# Trailing path segments that address the same job but a different step of the application flow.
_FLOW_TAIL = {"apply", "applymanually", "autofillwithresume", "usemylastapplication", "application"}
_CORP_SUFFIX = re.compile(
    r"\b(inc|incorporated|llc|corp|corporation|co|company|ltd|limited|plc|gmbh|the)\b"
)
_TERM_PHRASE = re.compile(r"\b(summer|fall|spring|winter|autumn)\s*(of\s*)?20\d\d\b")
_YEAR = re.compile(r"\b20\d\d\b")


def canonical_url(url: str | None) -> str:
    """Return a canonical form of a posting URL suitable as a dedup key ("" for empty input).

    Lower-cases the host, drops ``www.``, tracking parameters, fragments, a leading locale path segment
    (``/en-US/``) and trailing application-flow segments (``/apply``), sorts the query and forces https.
    ``job-boards.greenhouse.io`` is folded into ``boards.greenhouse.io``.
    """
    if not url or not url.strip():
        return ""
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return raw.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("job-boards."):
        host = "boards." + host[len("job-boards.") :]
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) > 1 and _LOCALE_SEGMENT.match(segments[0]):
        segments = segments[1:]
    while segments and segments[-1].lower() in _FLOW_TAIL:
        segments.pop()
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not _TRACKING_PARAM.match(k)
    )
    return urlunsplit(("https", netloc, "/" + "/".join(segments), urlencode(query), ""))


def norm_text(value: str | None) -> str:
    """Lower-case, strip accents/punctuation, collapse whitespace. ``&`` becomes ``and``."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", lowered)).strip()


def norm_company(value: str | None) -> str:
    """Normalised company name without corporate suffixes ("Acme, Inc." -> "acme")."""
    return re.sub(r"\s+", " ", _CORP_SUFFIX.sub(" ", norm_text(value))).strip()


def norm_title(value: str | None) -> str:
    """Normalised job title with term/year phrases removed and intern/internship folded together."""
    text = norm_text(value)
    text = _TERM_PHRASE.sub(" ", text)
    text = _YEAR.sub(" ", text)
    text = re.sub(r"\binternships?\b", "intern", text)
    text = re.sub(r"\bco op\b", "coop", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_location(value: str | None) -> str:
    """City-level normalised location ("Austin, TX, USA" -> "austin"; anything remote -> "remote")."""
    if not value:
        return ""
    if re.search(r"\bremote\b", value, re.IGNORECASE):
        return "remote"
    return norm_text(value.split(",")[0])


def fingerprint(company: str | None, title: str | None, location: str | None = None) -> str:
    """Cross-source identity of a role: ``company|title|city``."""
    return "|".join((norm_company(company), norm_title(title), norm_location(location)))


def opportunity_id(
    url: str | None, company: str | None, title: str | None, location: str | None = None
) -> str:
    """Stable 16-hex-char id: derived from the canonical URL, else from the fingerprint."""
    key = canonical_url(url) or fingerprint(company, title, location)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - not a security use


def slugify(value: str) -> str:
    """Filesystem/URL friendly slug (``"Acme Corp!"`` -> ``"acme-corp"``)."""
    return re.sub(r"\s+", "-", norm_text(value)) or "item"


def parse_year_month(value: str | None) -> tuple[int, int] | None:
    """Parse "2028-05", "May 2028", "05/2028", "2028-05-15" into (year, month); None if unparseable."""
    if not value:
        return None
    text = value.strip()
    months = {
        m: i
        for i, names in enumerate(
            [
                ("jan", "january"),
                ("feb", "february"),
                ("mar", "march"),
                ("apr", "april"),
                ("may",),
                ("jun", "june"),
                ("jul", "july"),
                ("aug", "august"),
                ("sep", "sept", "september"),
                ("oct", "october"),
                ("nov", "november"),
                ("dec", "december"),
            ],
            start=1,
        )
        for m in names
    }
    if match := re.fullmatch(r"(20\d\d)[-/](\d{1,2})(?:[-/]\d{1,2})?", text):
        year, month = int(match[1]), int(match[2])
    elif match := re.fullmatch(r"(\d{1,2})[-/](20\d\d)", text):
        month, year = int(match[1]), int(match[2])
    elif match := re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(20\d\d)", text):
        month, year = months.get(match[1].lower(), 0), int(match[2])
    else:
        return None
    return (year, month) if 1 <= month <= 12 else None


def host_of(url: str | None) -> str:
    """Lower-cased hostname of ``url`` without port, ``www.`` or a trailing ``.localhost``.

    The hermetic mock sites are served as ``<real-ats-host>.localhost:<port>`` (Chromium resolves any
    ``*.localhost`` to loopback), so ATS detection that goes through this helper recognises
    ``acme.wd5.myworkdayjobs.com.localhost`` as a Workday host exactly like the production host.
    """
    if not url or not url.strip():
        return ""
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""
    if host.endswith(".localhost"):
        host = host[: -len(".localhost")]
    return host[4:] if host.startswith("www.") else host
