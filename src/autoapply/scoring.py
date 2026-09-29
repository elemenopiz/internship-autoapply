"""Deterministic opportunity scoring (docs/SPEC.md section 5.4).

``score_opportunity(op, search, profile=None)`` is a pure function: no I/O, no clock, no randomness, so the
same input always yields the same ``ScoreResult`` (0..100) and the dashboard can explain every point.
``score_all`` applies it to many opportunities and returns copies with ``.score`` set.

Weights (points, summed and clamped to 0..100)
----------------------------------------------

=====================================  ====  ==========================================================
component                              max   how it is earned
=====================================  ====  ==========================================================
role match in the TITLE                50    best role family whose keyword appears as a whole-word
                                             phrase in the title, times the family weight; a generic
                                             one-word keyword ("strategy") earns 90 percent
role match in the DESCRIPTION only     20    used only when the title matches no family: 1 distinct
                                             keyword earns 50 percent, 2 earn 75, 3 or more earn 100;
                                             times the family weight
internship signal                      15    "intern / internship / co-op / summer analyst" in the
                                             title 15; employment-type metadata 12; description 8;
                                             only the target term 5; nothing at all 0 and -20
target term                            10    target term in the title or term field 10; only in the
                                             description 6; no term stated anywhere 5 (neutral)
location fit                           10    preferred location 10; remote accepted 8 (when a
                                             preferred list exists); no preference and a known
                                             location 6; location not listed 4
``include_keywords``                   10    4 per keyword found in the title, 2 per keyword found
                                             only in the description
company allowlist                       5    flat bonus
=====================================  ====  ==========================================================

Penalties (subtracted; never a hard fail): no internship signal at all -20; the description names only
other terms -25; clearly non-US location while ``us_only`` -40; location outside a non-empty preferred list
-8 / -15 / -30 (profile willing to relocate: yes / unknown / no); remote-only role while ``remote_ok`` is
off -15.

Hard fails (score 0, ``passed=False``, each one explained in ``penalties``): company on
``company_denylist``; a title word from ``exclude_title_keywords``; closed posting (``is_open`` false or a
deadline before ``today``); a different term named in the title or term field; a seniority marker
(manager, lead, head, director, ...) or an explicit full-time marker without any internship signal in the
title or employment-type metadata; an MBA-only / PhD-only role unless ``profile.degree`` is that degree
(waiving the matching ``exclude_title_keywords`` entry too).

``passed`` is ``score >= search.min_score`` and no hard fail.

Matching is done on normalised whole-word tokens (lower-case, accents and punctuation removed, ``&`` read as
"and", a light plural stemmer, filler words dropped), never on raw substrings, so "programming" is not
"program", "Strategy Manager" is not a strategy internship and "manager" alone matches nothing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

from autoapply.models import Opportunity, Profile, RoleFamily, ScoreResult, SearchProfile

__all__ = [
    "Term",
    "find_terms",
    "parse_target_term",
    "parse_term_field",
    "score_all",
    "score_opportunity",
    "signals_internship",
    "tokenize",
]

# --------------------------------------------------------------------------------------------- weights

W_TITLE = 50.0
W_DESCRIPTION = 20.0
W_INTERNSHIP = 15.0
W_TERM = 10.0
W_LOCATION = 10.0
W_KEYWORDS = 10.0
W_ALLOWLIST = 5.0

GENERIC_KEYWORD_FACTOR = 0.9  # a lone generic word ("strategy") is weaker evidence than a phrase
INTERN_TITLE_PTS = 15.0
INTERN_METADATA_PTS = 12.0
INTERN_DESCRIPTION_PTS = 8.0
INTERN_TERM_ONLY_PTS = 5.0
TERM_EXACT_PTS = 10.0
TERM_DESCRIPTION_PTS = 6.0
TERM_UNSTATED_PTS = 5.0
LOC_PREFERRED_PTS = 10.0
LOC_REMOTE_PTS = 8.0
LOC_NEUTRAL_PTS = 6.0
LOC_UNKNOWN_PTS = 4.0
KEYWORD_TITLE_PTS = 4.0
KEYWORD_DESCRIPTION_PTS = 2.0

PENALTY_NO_INTERNSHIP = 20.0
PENALTY_DESCRIPTION_TERM = 25.0
PENALTY_NON_US = 40.0
PENALTY_REMOTE_OFF = 15.0
PENALTY_OFF_PREFERRED_RELOCATE = 8.0
PENALTY_OFF_PREFERRED_UNKNOWN = 15.0
PENALTY_OFF_PREFERRED_STAY = 30.0

# --------------------------------------------------------------------------------------------- tokens

# Filler words are dropped from keywords and text alike, so "Strategy and Operations", "Strategy & Operations"
# and "Strategy/Operations" all reduce to the same token sequence.
_STOPWORDS = frozenset({"and", "or", "the", "of", "a", "an", "for", "to", "in", "at", "with", "on"})
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\+\+|#)?")
_DOTTED_RE = re.compile(r"\b(?:[a-z]\.){2,}")  # u.s.a. m.b.a. d.c.
_PHD_RE = re.compile(r"\bph\.?\s?d\b\.?")
_COOP_RE = re.compile(r"\bco[\s-]?op\b")


_COMBINING_RE = re.compile("[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")


def _clean(text: str) -> str:
    """Lower-case, strip accents, read ``&`` as "and", fold M.B.A./Ph.D./co-op into single words."""
    decomposed = text if text.isascii() else unicodedata.normalize("NFKD", text)
    lowered = _COMBINING_RE.sub("", decomposed).lower()
    lowered = lowered.replace("&", " and ").replace("’", "'")
    lowered = _PHD_RE.sub("phd", lowered)
    lowered = _DOTTED_RE.sub(lambda m: m.group(0).replace(".", ""), lowered)
    return _COOP_RE.sub("coop", lowered)


_NO_STEM = frozenset({"seniors"})  # students ("rising seniors"), not the job level "senior"


@lru_cache(
    maxsize=100_000
)  # the vocabulary of job postings is small; stemming dominates long texts
def _stem(token: str) -> str:
    """Conservative plural stripping ("analysts" -> "analyst", "strategies" -> "strategy")."""
    if len(token) <= 3 or not token.isalpha() or token in _NO_STEM:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "shes", "ches", "xes")):
        return token[:-2]
    if token.endswith(("ss", "us", "is")):
        return token
    return token[:-1] if token.endswith("s") else token


def _tokens_of(cleaned: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall(cleaned) if t not in _STOPWORDS]


def tokenize(text: str | None) -> list[str]:
    """Normalised comparison tokens of ``text`` (empty list for empty input). Pure and deterministic."""
    return _tokens_of(_clean(text)) if text else []


def _index_of(tokens: Sequence[str], phrase: Sequence[str]) -> int:
    """Index of the first whole-token occurrence of ``phrase`` in ``tokens`` or -1."""
    size = len(phrase)
    if size == 0 or size > len(tokens):
        return -1
    first = phrase[0]
    for i in range(len(tokens) - size + 1):
        if tokens[i] == first and tuple(tokens[i : i + size]) == tuple(phrase):
            return i
    return -1


# --------------------------------------------------------------------------------------------- terms

_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_SEASON_LIST = rf"{_SEASON}(?:\s*(?:/|,|&|and|or|-)\s*{_SEASON})*"
_TERM_SEASON_FIRST = re.compile(rf"\b({_SEASON_LIST})\s*(?:of\s+)?(?:(20\d\d)|'(\d\d))\b")
_TERM_YEAR_FIRST = re.compile(rf"\b(20\d\d)\s+({_SEASON_LIST})\b")
_SEASON_WORD = re.compile(_SEASON)
_YEAR_WORD = re.compile(r"\b(20\d\d)\b")


@dataclass(frozen=True)
class Term:
    """An internship term such as "Summer 2027". ``season`` / ``year`` may be None in a term *field*."""

    season: str | None
    year: int | None

    def label(self) -> str:
        parts = [self.season.capitalize() if self.season else "", str(self.year or "")]
        return " ".join(p for p in parts if p)

    def matches(self, target: Term) -> bool:
        """True when nothing about this term contradicts ``target`` (a missing part is not a conflict)."""
        season_ok = self.season is None or target.season is None or self.season == target.season
        year_ok = self.year is None or target.year is None or self.year == target.year
        return season_ok and year_ok


def _season_names(fragment: str) -> list[str]:
    return ["fall" if s == "autumn" else s for s in _SEASON_WORD.findall(fragment)]


def find_terms(text: str | None) -> list[Term]:
    """Every "<season> <year>" phrase in free text ("Summer 2027", "Summer '27", "2027 Summer",
    "Summer/Fall 2027"). A lone season or a lone year is NOT a term here (too ambiguous in prose)."""
    return _terms_of(_clean(text)) if text else []


def _terms_of(cleaned: str) -> list[Term]:
    found: list[Term] = []
    claimed: list[tuple[int, int]] = []  # spans already read as "<season> <year>"
    for match in _TERM_SEASON_FIRST.finditer(cleaned):
        year = int(match.group(2)) if match.group(2) else 2000 + int(match.group(3))
        found.extend(Term(season, year) for season in _season_names(match.group(1)))
        claimed.append(match.span())
    for match in _TERM_YEAR_FIRST.finditer(cleaned):
        start, end = match.span(1)
        # "Fall 2026 Summer 2027": the 2026 already belongs to Fall, it is not "2026 Summer".
        if any(lo <= start and end <= hi for lo, hi in claimed):
            continue
        found.extend(Term(season, int(match.group(1))) for season in _season_names(match.group(2)))
    return list(dict.fromkeys(found))


def parse_term_field(text: str | None) -> list[Term]:
    """Terms of a dedicated *term* field/setting: like ``find_terms`` but a lone season ("Summer") or lone
    year ("2027") is accepted because the field itself says it is a term."""
    if not text:
        return []
    if terms := find_terms(text):
        return terms
    cleaned = _clean(text)
    partial = [Term(season, None) for season in _season_names(cleaned)]
    partial += [Term(None, int(year)) for year in _YEAR_WORD.findall(cleaned)]
    return list(dict.fromkeys(partial))


def parse_target_term(text: str | None) -> Term | None:
    """The configured target term ("Summer 2027") or None when it cannot be understood."""
    terms = parse_term_field(text)
    return terms[0] if terms else None


# --------------------------------------------------------------------------------------------- internship

_INTERN_TOKENS = frozenset({"intern", "internship", "coop"})
_SUMMER_ROLES = frozenset(
    {"analyst", "associate", "fellow", "fellowship", "scholar", "student", "researcher"}
)
_YEAR_TOKEN = re.compile(r"^20\d\d$")


def _tokens_signal_internship(tokens: Sequence[str]) -> bool:
    """intern / internship / co-op, or a "Summer Analyst" style title ("Summer 2027 Associate" too)."""
    if any(t in _INTERN_TOKENS for t in tokens):
        return True
    for i, token in enumerate(tokens[:-1]):
        if token != "summer":
            continue
        nxt = tokens[i + 1]
        if nxt in _SUMMER_ROLES:
            return True
        if _YEAR_TOKEN.match(nxt) and i + 2 < len(tokens) and tokens[i + 2] in _SUMMER_ROLES:
            return True
    return False


def signals_internship(text: str | None) -> bool:
    """True when a short text (title, department, commitment) says the role is an internship."""
    return _tokens_signal_internship(tokenize(text))


_METADATA_KEYS = ("employment_type", "commitment", "job_type", "type", "department", "team")


def _metadata_signals_internship(extra: Mapping[str, Any]) -> bool:
    """Employment-type style fields recorded by a source (Lever commitment, Ashby employmentType, ...)."""
    for key in _METADATA_KEYS:
        value = extra.get(key)
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and signals_internship(v) for v in values):
            return True
    return False


# --------------------------------------------------------------------------------------------- location

_US_STATES: dict[str, str] = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
}
# State codes that are also everyday words: only trusted when written in capitals ("Portland, OR").
_AMBIGUOUS_CODES = frozenset(
    {"in", "or", "me", "hi", "ok", "oh", "de", "id", "as", "la", "pa", "ma", "co", "mo", "al", "ne"}
)
_STATE_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    sorted((tuple(name.split()) for name in _US_STATES.values()), key=lambda p: (-len(p), p))
)
_US_WORDS = frozenset({"usa", "us", "america"})
_US_PHRASES: tuple[tuple[str, ...], ...] = (("united", "states"),)

_NON_US_PLACES: tuple[tuple[str, ...], ...] = tuple(
    tuple(p.split())
    for p in (
        # countries and regions
        "canada",
        "united kingdom",
        "uk",
        "england",
        "scotland",
        "wales",
        "ireland",
        "india",
        "germany",
        "france",
        "spain",
        "italy",
        "netherlands",
        "belgium",
        "switzerland",
        "austria",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "poland",
        "portugal",
        "czech republic",
        "romania",
        "hungary",
        "greece",
        "turkey",
        "israel",
        "united arab emirates",
        "uae",
        "saudi arabia",
        "egypt",
        "south africa",
        "nigeria",
        "kenya",
        "china",
        "hong kong",
        "taiwan",
        "japan",
        "south korea",
        "korea",
        "singapore",
        "malaysia",
        "indonesia",
        "thailand",
        "vietnam",
        "philippines",
        "australia",
        "new zealand",
        "brazil",
        "argentina",
        "chile",
        "colombia",
        "peru",
        "mexico",
        "costa rica",
        "pakistan",
        "bangladesh",
        "ukraine",
        "emea",
        "apac",
        "latam",
        "europe",
        "asia",
        # provinces
        "ontario",
        "quebec",
        "alberta",
        "british columbia",
        "nova scotia",
        "manitoba",
        "saskatchewan",
        # major cities that are unambiguous enough on their own
        "london",
        "toronto",
        "vancouver",
        "montreal",
        "ottawa",
        "calgary",
        "dublin",
        "edinburgh",
        "manchester",
        "berlin",
        "munich",
        "paris",
        "madrid",
        "barcelona",
        "amsterdam",
        "zurich",
        "stockholm",
        "oslo",
        "copenhagen",
        "helsinki",
        "warsaw",
        "bangalore",
        "bengaluru",
        "hyderabad",
        "mumbai",
        "pune",
        "delhi",
        "gurgaon",
        "gurugram",
        "chennai",
        "tel aviv",
        "dubai",
        "sydney",
        "melbourne",
        "tokyo",
        "seoul",
        "shanghai",
        "beijing",
        "shenzhen",
        "sao paulo",
        "mexico city",
        "buenos aires",
        "bogota",
        "nairobi",
        "lagos",
        "cairo",
        "johannesburg",
        "cape town",
    )
)
_CA_PROVINCE_RE = re.compile(r",\s*(?:ON|BC|QC|AB|MB|NS|NB|NL|PE|SK)\b")
_CODE_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{2})(?![A-Za-z])")
_LOC_WORD_RE = re.compile(r"[a-z]+")
_LOC_STOPWORDS = frozenset(
    {"the", "of", "city", "metro", "metropolitan", "area", "greater", "county"}
)
_LOC_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnyc\b"), "new york"),
    (re.compile(r"\bsf\b"), "san francisco"),
    (re.compile(r"\bbay area\b"), "san francisco"),
    (re.compile(r"\bdfw\b"), "dallas"),
    (re.compile(r"\bdc\b"), "washington"),
)
_REMOTE_WORDS = frozenset({"remote", "wfh", "telecommute", "virtual", "anywhere"})


@dataclass(frozen=True)
class _LocInfo:
    places: frozenset[str]  # city-ish words, without state names / remote / country words
    states: frozenset[str]  # full US state names mentioned ("texas"), codes expanded
    us: bool  # evidence the place is in the United States
    non_us: bool  # evidence the place is outside the United States
    remote: bool


def _state_codes(raw: str) -> list[str]:
    """Two-letter US state codes written in ``raw`` ("Austin, TX"); everyday-word codes need capitals."""
    codes: list[str] = []
    for match in _CODE_RE.finditer(raw):
        token = match.group(1)
        low = token.lower()
        if low not in _US_STATES:
            continue
        after_comma = raw[: match.start()].rstrip().endswith(",")
        if low in _AMBIGUOUS_CODES:
            trusted = token.isupper() and (after_comma or not raw.isupper())
        else:
            trusted = token.isupper() or token[0].isupper() or after_comma
        if trusted:
            codes.append(low)
    return codes


def _consume_phrases(
    words: list[str], phrases: Sequence[tuple[str, ...]]
) -> tuple[list[str], list[tuple[str, ...]]]:
    """Remove every occurrence of ``phrases`` from ``words``; also return which phrases were found."""
    found: list[tuple[str, ...]] = []
    remaining = list(words)
    for phrase in phrases:
        while (idx := _index_of(remaining, phrase)) >= 0:
            found.append(phrase)
            del remaining[idx : idx + len(phrase)]
    return remaining, found


def _analyse_location(raw: str) -> _LocInfo:
    """Read a free-text location: region evidence (US / non-US), remote flag, city words and states."""
    codes = _state_codes(raw)
    cleaned = _clean(raw)
    for pattern, replacement in _LOC_ALIASES:
        cleaned = pattern.sub(replacement, cleaned)
    words = _LOC_WORD_RE.findall(cleaned)
    rest, state_phrases = _consume_phrases(words, _STATE_PHRASES)
    rest, us_phrases = _consume_phrases(rest, _US_PHRASES)
    non_us = bool(_CA_PROVINCE_RE.search(raw)) or any(
        _index_of(rest, p) >= 0 for p in _NON_US_PLACES
    )
    us = bool(codes) or bool(state_phrases) or bool(us_phrases) or any(w in _US_WORDS for w in rest)
    remote = (
        any(w in _REMOTE_WORDS for w in words) or _index_of(words, ("work", "from", "home")) >= 0
    )
    for code in codes:  # the code letters are not city words
        if code in rest:
            rest.remove(code)
    states = {_US_STATES[c] for c in codes} | {" ".join(p) for p in state_phrases}
    ignored = _LOC_STOPWORDS | _REMOTE_WORDS | _US_WORDS
    places = frozenset(w for w in rest if w not in ignored)
    return _LocInfo(places, frozenset(states), us, non_us, remote)


def _preferred_match(info: _LocInfo, preferred: Sequence[str], remote_ok: bool) -> str | None:
    """First preferred-location entry that fits ``info``.

    An entry names cities and/or states: every city word must appear, and when both the entry and the
    posting name a state they must agree ("Austin, TX" fits "Austin" and "Austin, Texas", not "Austin, MN";
    "Texas" fits any Texas city). "Remote" fits remote roles (only when ``remote_ok``) and "United States"
    fits any US location.
    """
    for entry in preferred:
        wanted = _analyse_location(entry)
        if not wanted.places and not wanted.states:
            fits = (info.remote and remote_ok) if wanted.remote else (wanted.us and info.us)
        elif not wanted.places:
            fits = wanted.states <= info.states
        else:
            states_agree = not wanted.states or not info.states or wanted.states <= info.states
            fits = wanted.places <= info.places and states_agree
        if fits:
            return entry.strip()
    return None


@dataclass(frozen=True)
class _Fit:
    """One score component: points earned, points deducted, and the sentences explaining both."""

    points: float
    reasons: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    deduction: float = 0.0


def _relocation_penalty(profile: Profile | None) -> float:
    willing = profile.willing_to_relocate if profile is not None else None
    if willing is True:
        return PENALTY_OFF_PREFERRED_RELOCATE
    if willing is False:
        return PENALTY_OFF_PREFERRED_STAY
    return PENALTY_OFF_PREFERRED_UNKNOWN


def _location_fit(location: str | None, search: SearchProfile, profile: Profile | None) -> _Fit:
    """Location component: preferred list, remote acceptance and the ``us_only`` guard."""
    raw = (location or "").strip()
    if not raw:
        return _Fit(
            LOC_UNKNOWN_PTS, (f"No location listed; treated as neutral (+{LOC_UNKNOWN_PTS:g}).",)
        )
    info = _analyse_location(raw)
    if search.us_only and info.non_us and not info.us:
        return _Fit(
            0.0,
            penalties=(
                f"Location '{raw}' is outside the United States and us_only is on "
                f"(-{PENALTY_NON_US:g}).",
            ),
            deduction=PENALTY_NON_US,
        )
    preferred = [p for p in search.preferred_locations if p.strip()]
    if hit := _preferred_match(info, preferred, search.remote_ok):
        return _Fit(
            LOC_PREFERRED_PTS,
            (f"Location '{raw}' matches preferred location '{hit}' (+{LOC_PREFERRED_PTS:g}).",),
        )
    if info.remote:
        if not search.remote_ok:
            return _Fit(
                0.0,
                penalties=(
                    f"'{raw}' is a remote role and remote_ok is off (-{PENALTY_REMOTE_OFF:g}).",
                ),
                deduction=PENALTY_REMOTE_OFF,
            )
        pts = LOC_REMOTE_PTS if preferred else LOC_NEUTRAL_PTS
        return _Fit(pts, (f"Remote role '{raw}' is accepted (remote_ok) (+{pts:g}).",))
    if preferred:
        pen = _relocation_penalty(profile)
        return _Fit(
            0.0,
            penalties=(f"Location '{raw}' is not in your preferred locations (-{pen:g}).",),
            deduction=pen,
        )
    return _Fit(
        LOC_NEUTRAL_PTS,
        (f"Location '{raw}' is acceptable; no location preference is set (+{LOC_NEUTRAL_PTS:g}).",),
    )


# --------------------------------------------------------------------------------------------- companies

_COMPANY_NOISE = frozenset(
    {"inc", "incorporated", "llc", "corp", "corporation", "co", "company", "ltd", "limited", "plc"}
)


def _company_tokens(name: str | None) -> tuple[str, ...]:
    tokens = tokenize(name)
    return tuple([t for t in tokens if t not in _COMPANY_NOISE] or tokens)


def _first_company_match(company: str, entries: Sequence[str]) -> str | None:
    """The first list entry whose words appear, as a whole phrase, in the company name
    ("Meta" matches "Meta Platforms, Inc."; "Acme Inc" matches "ACME")."""
    tokens = _company_tokens(company)
    for entry in entries:
        wanted = _company_tokens(entry)
        if wanted and _index_of(tokens, wanted) >= 0:
            return entry.strip()
    return None


# --------------------------------------------------------------------------------------------- compiled search


@dataclass(frozen=True)
class _Phrase:
    text: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class _Keyword:
    text: str
    tokens: tuple[str, ...]
    factor: float  # 1.0, or GENERIC_KEYWORD_FACTOR for a lone generic word
    content: int  # tokens that are not internship words


@dataclass(frozen=True)
class _Family:
    name: str
    weight: float
    keywords: tuple[_Keyword, ...]
    order: int


@dataclass(frozen=True)
class _Compiled:
    families: tuple[_Family, ...]
    excludes: tuple[_Phrase, ...]
    includes: tuple[_Phrase, ...]
    target: Term | None


def _phrase(text: str) -> _Phrase | None:
    tokens = tuple(tokenize(text))
    return _Phrase(text.strip(), tokens) if tokens else None


def _keyword(text: str) -> _Keyword | None:
    """A role keyword. Keywords made only of internship words ("intern") say nothing about the role."""
    tokens = tuple(tokenize(text))
    content = [t for t in tokens if t not in _INTERN_TOKENS]
    if not content:
        return None
    generic = len(content) == 1 and len(content[0]) > 4  # "strategy" yes, "apm" / "tpm" no
    return _Keyword(text.strip(), tokens, GENERIC_KEYWORD_FACTOR if generic else 1.0, len(content))


def _family(order: int, name: str, family: RoleFamily) -> _Family | None:
    weight = min(1.0, max(0.0, float(family.weight)))
    keywords = tuple(k for text in family.keywords if (k := _keyword(text)) is not None)
    return _Family(name, weight, keywords, order) if weight > 0 and keywords else None


def _unique_phrases(texts: Sequence[str]) -> tuple[_Phrase, ...]:
    phrases = (_phrase(t) for t in texts)
    unique = {p.tokens: p for p in phrases if p is not None}
    return tuple(unique.values())


def _compile(search: SearchProfile) -> _Compiled:
    families = (
        _family(order, name, fam) for order, (name, fam) in enumerate(search.role_families.items())
    )
    return _Compiled(
        families=tuple(f for f in families if f is not None),
        excludes=_unique_phrases(search.exclude_title_keywords),
        includes=_unique_phrases(search.include_keywords),
        target=parse_target_term(search.target_term),
    )


# --------------------------------------------------------------------------------------------- role match


@dataclass(frozen=True)
class _Role:
    family: str
    points: float
    keywords: tuple[str, ...]
    in_title: bool
    reason: str  # with points, for a normal result
    plain_reason: str  # without points, for a hard-failed result


def _weight_note(weight: float) -> str:
    return f" (family weight {weight:g})" if weight < 1.0 else ""


def _title_role(comp: _Compiled, title_tokens: Sequence[str]) -> _Role | None:
    """Best family whose keyword is a whole-word phrase of the title.

    Ranking: points, then more role words matched, then earlier in the title, then family order.
    """
    best: tuple[tuple[float, int, int, int], _Role] | None = None
    for fam in comp.families:
        hits = [(kw, i) for kw in fam.keywords if (i := _index_of(title_tokens, kw.tokens)) >= 0]
        if not hits:
            continue
        kw, pos = min(hits, key=lambda h: (-h[0].factor, -h[0].content, h[1]))
        points = round(W_TITLE * fam.weight * kw.factor, 4)
        note = _weight_note(fam.weight)
        base = f"Title matches role family '{fam.name}'{note} via '{kw.text}'"
        role = _Role(
            fam.name,
            points,
            tuple(k.text for k, _ in hits),
            True,
            f"{base} (+{points:g}).",
            f"{base}.",
        )
        rank = (-points, -kw.content, pos, fam.order)
        if best is None or rank < best[0]:
            best = (rank, role)
    return best[1] if best else None


def _description_role(comp: _Compiled, desc_tokens: Sequence[str]) -> _Role | None:
    """Fallback when the title names no family: keywords found in the description, capped at 20 points."""
    if not desc_tokens:
        return None
    best: tuple[tuple[float, int], _Role] | None = None
    for fam in comp.families:
        found = [k.text for k in fam.keywords if _index_of(desc_tokens, k.tokens) >= 0]
        found = list(dict.fromkeys(found))
        if not found:
            continue
        points = round(W_DESCRIPTION * fam.weight * min(1.0, (len(found) + 1) / 4), 4)
        listed = ", ".join(f"'{k}'" for k in found[:4])
        base = (
            f"Title names no role family; description mentions {len(found)} keyword(s) of "
            f"'{fam.name}'{_weight_note(fam.weight)}: {listed}"
        )
        role = _Role(fam.name, points, tuple(found), False, f"{base} (+{points:g}).", f"{base}.")
        rank = (-points, fam.order)
        if best is None or rank < best[0]:
            best = (rank, role)
    return best[1] if best else None


# --------------------------------------------------------------------------------------------- hard fails

_SENIOR_TOKENS = frozenset(
    {
        "manager",
        "mgr",
        "lead",
        "head",
        "chief",
        "director",
        "vp",
        "president",
        "principal",
        "senior",
        "sr",
        "staff",
        "ii",
        "iii",
        "iv",
        "experienced",
        "supervisor",
    }
)
_FULL_TIME_PHRASES: tuple[tuple[str, ...], ...] = (
    ("full", "time"),
    ("new", "grad"),
    ("new", "graduate"),
    ("recent", "grad"),
    ("recent", "graduate"),
    ("entry", "level"),
    ("early", "career"),
    ("permanent",),
)
_MBA_WORDS = frozenset({"mba"})
_PHD_WORDS = frozenset({"phd", "doctoral", "doctorate"})
_DEGREE_LEAD = (
    r"(?:pursuing|enrolled in|currently enrolled in|working toward|working towards|completing|"
    r"candidates? for)\s+(?:an?\s+|the\s+)?(?:[a-z-]+\s+){0,2}"
)
_MBA_ONLY_RE = re.compile(
    rf"\b{_DEGREE_LEAD}mba\b|\bmba (?:candidates?|students?|interns?)\b|\bmust be (?:an? )?mba\b"
)
_PHD_ONLY_RE = re.compile(
    rf"\b{_DEGREE_LEAD}(?:phd|doctorate|doctoral)\b|\b(?:phd|doctoral) (?:candidates?|students?|interns?)\b"
)
_UNDERGRAD_RE = re.compile(
    r"\b(?:undergraduate|undergrad|bachelors?|juniors?|sophomores?|freshmen|freshman|rising senior|"
    r"all majors)\b"
)


def _positions(tokens: Sequence[str], phrase: Sequence[str]) -> list[int]:
    size = len(phrase)
    return [
        i for i in range(len(tokens) - size + 1) if tuple(tokens[i : i + size]) == tuple(phrase)
    ]


def _academic_senior(tokens: Sequence[str], i: int) -> bool:
    """ "rising senior" / "senior year" describe the student's class year, not the job level."""
    before = tokens[i - 1] if i > 0 else ""
    after = tokens[i + 1] if i + 1 < len(tokens) else ""
    return before == "rising" or after in {"year", "class", "standing"}


def _degree_flags(profile: Profile | None) -> tuple[bool, bool]:
    """(has_mba, has_phd) according to ``profile.degree``; unknown profile -> (False, False)."""
    tokens = tokenize(profile.degree) if profile is not None else []
    has_mba = "mba" in tokens or _index_of(tokens, ("master", "business", "administration")) >= 0
    has_phd = (
        any(t in _PHD_WORDS for t in tokens) or _index_of(tokens, ("doctor", "philosophy")) >= 0
    )
    return has_mba, has_phd


def _excluded_keywords(
    title_tokens: Sequence[str], comp: _Compiled, has_mba: bool, has_phd: bool
) -> tuple[list[str], list[str]]:
    """(hits, waived): ``exclude_title_keywords`` found in the title. An "mba" / "phd" entry is waived when
    the profile's own degree is that degree."""
    hits: list[str] = []
    waived: list[str] = []
    for ex in comp.excludes:
        found = _positions(title_tokens, ex.tokens)
        if ex.tokens == ("senior",):
            found = [i for i in found if not _academic_senior(title_tokens, i)]
        if not found:
            continue
        own_degree = (has_mba and set(ex.tokens) <= _MBA_WORDS) or (
            has_phd and set(ex.tokens) <= _PHD_WORDS
        )
        (waived if own_degree else hits).append(ex.text)
    return hits, waived


def _non_intern_marker(title_tokens: Sequence[str]) -> str | None:
    """First seniority / full-time marker in the title ("manager", "new grad"), else None."""
    found: list[tuple[int, str]] = []
    for i, token in enumerate(title_tokens):
        if token in _SENIOR_TOKENS and not (
            token == "senior" and _academic_senior(title_tokens, i)
        ):
            found.append((i, token))
    for phrase in _FULL_TIME_PHRASES:
        if (i := _index_of(title_tokens, phrase)) >= 0:
            found.append((i, " ".join(phrase)))
    return min(found)[1] if found else None


def _degree_only_failures(
    title_tokens: Sequence[str],
    desc_clean: str,
    has_mba: bool,
    has_phd: bool,
    already_flagged: set[str],
) -> list[str]:
    """MBA-only / PhD-only roles (title word, or an unmistakable description sentence)."""
    failures: list[str] = []
    inclusive = bool(_UNDERGRAD_RE.search(desc_clean))
    checks = (
        ("MBA", has_mba, _MBA_WORDS, _MBA_ONLY_RE),
        ("PhD", has_phd, _PHD_WORDS, _PHD_ONLY_RE),
    )
    for label, owned, words, pattern in checks:
        if owned:
            continue
        in_title = any(t in words for t in title_tokens)
        if in_title and not (words & already_flagged):
            failures.append(
                f"Hard fail: the role is {label}-only (named in the title) and your degree is not "
                f"{'an' if label == 'MBA' else 'a'} {label}."
            )
        elif not in_title and not inclusive and pattern.search(desc_clean):
            failures.append(
                f"Hard fail: the description says the role is for {label} students and your degree "
                f"is not {'an' if label == 'MBA' else 'a'} {label}."
            )
    return failures


# --------------------------------------------------------------------------------------------- components


@dataclass(frozen=True)
class _TermResult:
    fit: _Fit
    hard: tuple[str, ...]
    explicit: bool  # the title or term field states the target term


def _term_component(
    op: Opportunity, search: SearchProfile, comp: _Compiled, desc_clean: str
) -> _TermResult:
    """Target-term check. A different term in the title or term field is a hard fail; a description that
    names only other terms is a soft penalty (descriptions often recycle last year's boilerplate)."""
    target = comp.target
    if target is None:
        note = f"Target term '{search.target_term}' is not recognised, so the term is not checked"
        return _TermResult(
            _Fit(TERM_UNSTATED_PTS, (f"{note} (+{TERM_UNSTATED_PTS:g}).",)), (), False
        )
    title_terms = find_terms(op.title)
    field_terms = parse_term_field(op.term)
    hard: list[str] = []
    for source, terms in (("title", title_terms), ("term field", field_terms)):
        if terms and not any(t.matches(target) for t in terms):
            named = ", ".join(t.label() for t in terms)
            hard.append(
                f"Hard fail: the {source} names {named}, not the target term {target.label()}."
            )
    if hard:
        return _TermResult(_Fit(0.0), tuple(hard), False)
    if any(t.matches(target) for t in [*title_terms, *field_terms]):
        fit = _Fit(
            TERM_EXACT_PTS, (f"Term matches the target {target.label()} (+{TERM_EXACT_PTS:g}).",)
        )
        return _TermResult(fit, (), True)
    desc_terms = _terms_of(desc_clean)
    if any(t.matches(target) for t in desc_terms):
        note = f"Description mentions the target term {target.label()} (+{TERM_DESCRIPTION_PTS:g})."
        return _TermResult(_Fit(TERM_DESCRIPTION_PTS, (note,)), (), False)
    if desc_terms:
        named = ", ".join(t.label() for t in desc_terms[:3])
        note = (
            f"Description names only other terms ({named}), never {target.label()} "
            f"(-{PENALTY_DESCRIPTION_TERM:g})."
        )
        return _TermResult(
            _Fit(0.0, penalties=(note,), deduction=PENALTY_DESCRIPTION_TERM), (), False
        )
    note = f"No term is stated; assuming {target.label()} (+{TERM_UNSTATED_PTS:g})."
    return _TermResult(_Fit(TERM_UNSTATED_PTS, (note,)), (), False)


def _internship_component(
    title_intern: bool, meta_intern: bool, desc_intern: bool, term_stated: bool
) -> _Fit:
    if title_intern:
        return _Fit(INTERN_TITLE_PTS, (f"Title signals an internship (+{INTERN_TITLE_PTS:g}).",))
    if meta_intern:
        return _Fit(
            INTERN_METADATA_PTS, (f"Employment type says internship (+{INTERN_METADATA_PTS:g}).",)
        )
    if desc_intern:
        return _Fit(
            INTERN_DESCRIPTION_PTS,
            (f"Description mentions an internship (+{INTERN_DESCRIPTION_PTS:g}).",),
        )
    if term_stated:
        note = "Only the target term is stated; nothing says 'intern'"
        return _Fit(INTERN_TERM_ONLY_PTS, (f"{note} (+{INTERN_TERM_ONLY_PTS:g}).",))
    note = "No internship signal in the title, employment type or description"
    return _Fit(
        0.0, penalties=(f"{note} (-{PENALTY_NO_INTERNSHIP:g}).",), deduction=PENALTY_NO_INTERNSHIP
    )


def _keyword_component(
    comp: _Compiled, title_tokens: Sequence[str], desc_tokens: Sequence[str]
) -> tuple[_Fit, list[str]]:
    """``include_keywords`` bonus: 4 per keyword in the title, 2 per keyword only in the description."""
    found: list[tuple[str, str]] = []
    points = 0.0
    for phrase in comp.includes:
        if _index_of(title_tokens, phrase.tokens) >= 0:
            found.append((phrase.text, "title"))
            points += KEYWORD_TITLE_PTS
        elif _index_of(desc_tokens, phrase.tokens) >= 0:
            found.append((phrase.text, "description"))
            points += KEYWORD_DESCRIPTION_PTS
    if not found:
        return _Fit(0.0), []
    capped = min(W_KEYWORDS, points)
    listed = ", ".join(f"'{text}' ({where})" for text, where in found)
    fit = _Fit(capped, (f"Include keywords found: {listed} (+{capped:g}).",))
    return fit, [text for text, _ in found]


# --------------------------------------------------------------------------------------------- scoring


def _hard_fails(
    op: Opportunity,
    search: SearchProfile,
    comp: _Compiled,
    profile: Profile | None,
    today: date | None,
    facts: _Facts,
) -> tuple[list[str], list[str]]:
    """(hard-fail sentences, informational notes) for the rules that force score 0."""
    hard: list[str] = []
    notes: list[str] = []
    if entry := _first_company_match(op.company, search.company_denylist):
        hard.append(
            f"Hard fail: company '{op.company}' is on the company_denylist (entry '{entry}')."
        )
    has_mba, has_phd = _degree_flags(profile)
    excluded, waived = _excluded_keywords(facts.title_tokens, comp, has_mba, has_phd)
    hard.extend(f"Hard fail: title contains excluded keyword '{kw}'." for kw in excluded)
    degree = profile.degree if profile is not None else ""
    notes.extend(
        f"Excluded keyword '{kw}' is waived: your degree ({degree}) qualifies." for kw in waived
    )
    if not op.is_open:
        hard.append("Hard fail: the posting is closed.")
    if today is not None and op.deadline is not None and op.deadline < today:
        hard.append(f"Hard fail: the application deadline ({op.deadline.isoformat()}) has passed.")
    hard.extend(facts.term.hard)
    marker = _non_intern_marker(facts.title_tokens)
    if marker and not excluded and not (facts.title_intern or facts.meta_intern):
        hard.append(
            f"Hard fail: '{marker}' in the title marks a full-time or management role and "
            "nothing says it is an internship."
        )
    flagged = {t for kw in excluded for t in tokenize(kw)}
    hard.extend(
        _degree_only_failures(facts.title_tokens, facts.desc_clean, has_mba, has_phd, flagged)
    )
    return hard, notes


@dataclass(frozen=True)
class _Facts:
    """Text-derived facts about one opportunity, computed once and shared by the scoring steps."""

    title_tokens: list[str]
    desc_clean: str
    desc_tokens: list[str]
    term: _TermResult
    title_intern: bool
    meta_intern: bool


def _facts(op: Opportunity, search: SearchProfile, comp: _Compiled) -> _Facts:
    title_tokens = tokenize(op.title)
    desc_clean = _clean(op.description) if op.description else ""
    return _Facts(
        title_tokens=title_tokens,
        desc_clean=desc_clean,
        desc_tokens=_tokens_of(desc_clean),
        term=_term_component(op, search, comp, desc_clean),
        title_intern=_tokens_signal_internship(title_tokens),
        meta_intern=_metadata_signals_internship(op.extra),
    )


def _score(
    op: Opportunity,
    search: SearchProfile,
    comp: _Compiled,
    profile: Profile | None,
    today: date | None,
) -> ScoreResult:
    facts = _facts(op, search, comp)
    role = _title_role(comp, facts.title_tokens) or _description_role(comp, facts.desc_tokens)
    hard, notes = _hard_fails(op, search, comp, profile, today, facts)
    intern = _internship_component(
        facts.title_intern,
        facts.meta_intern,
        _tokens_signal_internship(facts.desc_tokens),
        facts.term.explicit,
    )
    location = _location_fit(op.location, search, profile)
    keywords, keyword_hits = _keyword_component(comp, facts.title_tokens, facts.desc_tokens)
    allowed = _first_company_match(op.company, search.company_allowlist)
    allow_pts = W_ALLOWLIST if allowed else 0.0

    matched = list(dict.fromkeys([*(role.keywords if role else ()), *keyword_hits]))
    soft = [*intern.penalties, *facts.term.fit.penalties, *location.penalties]
    if hard:
        return ScoreResult(
            score=0.0,
            passed=False,
            role_family=role.family if role else None,
            matched_keywords=matched,
            reasons=([role.plain_reason] if role else []) + notes,
            penalties=[*hard, *soft],
        )

    reasons: list[str] = [role.reason] if role else []
    reasons += [*intern.reasons, *facts.term.fit.reasons, *location.reasons, *keywords.reasons]
    if allowed:
        reasons.append(f"Company '{op.company}' is on your allowlist (+{W_ALLOWLIST:g}).")
    reasons += notes
    penalties = [] if role else ["No role family matched the title or description."]
    penalties += soft

    deductions = intern.deduction + facts.term.fit.deduction + location.deduction
    earned = (
        (role.points if role else 0.0)
        + intern.points
        + facts.term.fit.points
        + location.points
        + keywords.points
        + allow_pts
    )
    score = round(max(0.0, min(100.0, earned - deductions)), 1)
    return ScoreResult(
        score=score,
        passed=score >= search.min_score,
        role_family=role.family if role else None,
        matched_keywords=matched,
        reasons=reasons,
        penalties=penalties,
    )


def score_opportunity(
    op: Opportunity,
    search: SearchProfile,
    profile: Profile | None = None,
    *,
    today: date | None = None,
) -> ScoreResult:
    """Score one opportunity against the search profile (see the module docstring for the weights).

    Pure and deterministic. ``profile`` (optional) supplies the degree (MBA / PhD-only rule) and the
    relocation preference; without it MBA-only and PhD-only roles are excluded. ``today`` (optional) enables
    the deadline check; the function never reads a clock itself.
    """
    return _score(op, search, _compile(search), profile, today)


def score_all(
    ops: Iterable[Opportunity],
    search: SearchProfile,
    profile: Profile | None = None,
    *,
    today: date | None = None,
) -> list[Opportunity]:
    """Score every opportunity; returns copies (input order kept) with ``.score`` set. Inputs are untouched."""
    comp = _compile(search)
    return [op.model_copy(update={"score": _score(op, search, comp, profile, today)}) for op in ops]
