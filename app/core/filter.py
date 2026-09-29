"""Job filter and scoring engine.

Implements: FR-044 (job scoring), FR-045 (ATS detection).

Scores jobs 0-100 based on title match, salary match, location match,
and keyword match. Applies hard disqualifiers for excluded keywords,
blacklisted companies, and duplicate jobs.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.search.base import RawJob
    from config.settings import AppConfig
    from db.database import Database


@dataclass
class ScoredJob:
    """A job after scoring — includes match score and pass/fail verdict."""

    id: str  # UUID for filename generation
    raw: "RawJob"
    score: int
    pass_filter: bool
    skip_reason: str | None


# ATS fingerprints for URL-based detection. First match wins, so ATS hosts
# come before the job boards (a "?src=linkedin.com" tag must not misroute).
ATS_FINGERPRINTS = {
    "greenhouse.io": "greenhouse",
    "gh_jid=": "greenhouse",  # company career page embedding a Greenhouse form
    "lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "ashbyhq.com": "ashby",
    "ashby_jid=": "ashby",  # company career page embedding an Ashby form
    "taleo.net": "taleo",
    "icims.com": "icims",
    "wellsfargojobs.com": "workday",
    # No-account ATSs, applied to by the SmartApplier
    "smartrecruiters.com": "smartrecruiters",
    "apply.workable.com": "workable",
    "jobs.workable.com": "workable",
    "jobvite.com": "jobvite",
    ".bamboohr.com/careers": "bamboohr",
    ".bamboohr.com/jobs": "bamboohr",
    "ats.rippling.com": "rippling",
    ".recruitee.com": "recruitee",
    ".breezy.hr": "breezy",
    "applytojob.com": "jazzhr",
    ".teamtailor.com": "teamtailor",
    ".pinpointhq.com": "pinpoint",
    "app.dover.com": "dover",
    "jobs.gem.com": "gem",
    ".jobs.personio.de": "personio",
    ".jobs.personio.com": "personio",
    "comeet.com/jobs": "comeet",
    "comeet.co/jobs": "comeet",
    # job boards
    "linkedin.com/jobs": "linkedin",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
}


def detect_ats(url: str) -> str | None:
    """Detect ATS platform from a job URL.

    Args:
        url: The apply URL to check.

    Returns:
        ATS name string or None if unrecognized.
    """
    url_lower = url.lower()
    for domain, ats in ATS_FINGERPRINTS.items():
        if domain in url_lower:
            return ats
    return None


def score_job(
    raw_job: "RawJob",
    config: "AppConfig",
    db: "Database | None" = None,
) -> ScoredJob:
    """Score a job against user criteria and return a ScoredJob.

    Scoring breakdown (0-100):
      - Title match: 0-35 points
      - Salary match: 0-20 points
      - Location match: 0-20 points
      - Keyword match: 0-25 points

    Hard disqualifiers (score=0):
      - Exclude keyword found in title or description
      - Company in blacklist
      - Job already in database (deduplication)

    Args:
        raw_job: The unscored job listing.
        config: Application configuration with search criteria.
        db: Optional database for deduplication check.

    Returns:
        ScoredJob with score, pass/fail, and skip reason.
    """
    job_id = str(uuid.uuid4())
    criteria = config.search_criteria

    title_lower = raw_job.title.lower()
    desc_lower = raw_job.description.lower()
    combined_lower = f"{title_lower} {desc_lower}"

    # --- Hard disqualifiers ---

    # Deduplication — retryable outcomes (login wall, held questions, errors)
    # are attempted again until Database.is_done says the job is finished.
    if db is not None and db.is_done(raw_job.external_id, raw_job.platform,
                                     apply_url=raw_job.apply_url):
        return ScoredJob(
            id=job_id, raw=raw_job, score=0,
            pass_filter=False, skip_reason="Already applied",
        )

    # Blacklisted company
    company_lower = raw_job.company.lower()
    for blacklisted in config.company_blacklist:
        if blacklisted.lower() in company_lower:
            return ScoredJob(
                id=job_id, raw=raw_job, score=0,
                pass_filter=False,
                skip_reason=f"Blacklisted company: {blacklisted}",
            )

    # Exclude keywords
    for kw in criteria.keywords_exclude:
        if kw.lower() in combined_lower:
            return ScoredJob(
                id=job_id, raw=raw_job, score=0,
                pass_filter=False,
                skip_reason=f"Excluded keyword: {kw}",
            )

    # --- Scoring ---

    score = 0

    # Title match (0-35)
    score += _title_score(raw_job.title, criteria.job_titles)

    # Salary match (0-20)
    if criteria.salary_min is None:
        score += 20  # No salary requirement — full points
    elif raw_job.salary is None:
        score += 10  # Unknown salary — partial credit
    else:
        salary_num = _extract_salary_number(raw_job.salary)
        if salary_num is not None and salary_num >= criteria.salary_min:
            score += 20
        elif salary_num is None:
            score += 10  # Couldn't parse — give benefit of doubt

    # Location match (0-20). With allowed countries configured, location is a
    # gate (outside them -> skip) and every in-country job gets full points.
    job_location_lower = raw_job.location.lower()
    countries = [c for c in (getattr(criteria, "countries", None) or []) if isinstance(c, str)]
    if countries:
        region = location_region(raw_job.location)
        if region == "other":
            return ScoredJob(
                id=job_id, raw=raw_job, score=0, pass_filter=False,
                skip_reason=f"Outside {' / '.join(countries)}: {raw_job.location}",
            )
        score += 20
    elif criteria.remote_only and "remote" in job_location_lower:
        score += 20
    elif not criteria.remote_only:
        for loc in criteria.locations:
            if loc.lower() in job_location_lower:
                score += 20
                break
        else:
            # Same state as a preferred location, or anywhere else in the US:
            # partial credit — location ranks jobs but does not exclude them.
            if any(
                loc.lower().split(",")[-1].strip() in job_location_lower
                for loc in criteria.locations
                if "," in loc
            ) or _is_us_location(raw_job.location):
                score += 10

    # Keyword match (0-25, +5 per keyword, max 25)
    kw_score = 0
    for kw in criteria.keywords_include:
        if kw.lower() in combined_lower:
            kw_score += 5
    score += min(kw_score, 25)

    # --- Threshold check ---
    min_score = config.bot.min_match_score
    pass_filter = score >= min_score

    skip_reason = None if pass_filter else f"Score {score} below threshold {min_score}"

    return ScoredJob(
        id=job_id, raw=raw_job, score=score,
        pass_filter=pass_filter, skip_reason=skip_reason,
    )


_TITLE_SYNONYMS = {"internship": "intern", "management": "manager"}
_TERM_WORDS = frozenset({"summer", "fall", "spring", "winter"})


def _title_words(text: str) -> list[str]:
    """Title tokens for matching: punctuation stripped, synonyms merged, and
    season/year words dropped.

    "(Summer 2027)" must not tokenize to "(summer"/"2027)"; and since the
    internship scope filter already enforces the recruiting cycle, a target
    "Summer 2027 Product Management Intern" should fully match "Associate
    Product Manager Intern", whose title omits the cycle.
    """
    words = re.findall(r"[a-z0-9&+]+", text.lower())
    return [_TITLE_SYNONYMS.get(w, w) for w in words
            if w not in _TERM_WORDS and not re.fullmatch(r"20\d\d", w)]


def _title_score(title: str, targets: list[str]) -> int:
    """Best title score over ALL target titles (0, 20, or 35).

    35 when every word of a target title appears in the job title (any order),
    20 when at least half do. Checking every target matters: stopping at the
    first partial hit let an earlier 20-point target mask a later exact one.
    """
    title_set = set(_title_words(title))
    best = 0
    for target in targets:
        target_set = set(_title_words(target))
        if not target_set:
            continue
        overlap = len(target_set & title_set)
        if overlap == len(target_set):
            return 35
        if overlap >= len(target_set) * 0.5:
            best = 20
    return best


_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}


def _is_us_location(location: str) -> bool:
    """True for a US place: a state name/abbreviation or 'United States'/'USA'."""
    low = (location or "").lower()
    if re.search(r"\b(united states|usa|u\.s\.a?\.?)\b", low):
        return True
    if any(re.search(rf"\b{name}\b", low) for name in _US_STATES):
        return True
    abbrevs = set(_US_STATES.values())
    return any(tok in abbrevs for tok in re.findall(r"\b[A-Z]{2}\b", location or ""))


_CANADA = re.compile(
    r"\b(canada|toronto|vancouver|montreal|montréal|ottawa|calgary|edmonton|"
    r"mississauga|ontario|british columbia|quebec|québec|alberta|nova scotia|manitoba)\b",
    re.IGNORECASE)
_CANADA_PROVINCE = re.compile(r"\b(ON|BC|QC|AB|NS|MB|SK|NB)\b")  # case-sensitive
#: Places that are unambiguously outside the US and Canada. Deliberately no
#: city names shared with US towns (Paris TX, Dublin OH, Cambridge MA, ...).
_FOREIGN = re.compile(
    r"\b(united kingdom|england|scotland|london, uk|\buk\b|ireland|india|bangalore|bengaluru|"
    r"hyderabad|mumbai|pune|singapore|germany|munich|france|netherlands|amsterdam|spain|"
    r"madrid|barcelona|poland|warsaw|israel|tel aviv|switzerland|zurich|sweden|stockholm|"
    r"japan|tokyo|china|shanghai|beijing|shenzhen|hong kong|taiwan|taipei|korea|seoul|"
    r"australia|sydney|melbourne, vic|mexico city|méxico|brazil|são paulo|sao paulo|"
    r"argentina|colombia|philippines|manila|vietnam|indonesia|malaysia|thailand|uae|dubai|"
    r"united arab emirates|egypt|nigeria|kenya|south africa|portugal|lisbon|italy|milan|"
    r"denmark|norway|finland|belgium|austria|czech|romania|ukraine|turkey|türkiye)\b",
    re.IGNORECASE)


def location_region(location: str) -> str:
    """'us', 'canada', 'unknown' (blank/remote/unclear), or 'other'.

    Only a positively foreign place is 'other' — a bare "Seattle" or "Remote"
    stays eligible rather than being dropped on a guess.
    """
    text = location or ""
    if _is_us_location(text):
        return "us"
    if _CANADA.search(text) or _CANADA_PROVINCE.search(text):
        return "canada"
    if _FOREIGN.search(text):
        return "other"
    return "unknown"


def _extract_salary_number(salary_str: str) -> int | None:
    """Extract an annual salary number from a salary string.

    Handles formats like "$120,000", "$120K", "$60/hr", "120000-150000".
    Returns the lower bound as an annual integer, or None if unparsable.
    """
    import re

    cleaned = salary_str.replace(",", "").replace("$", "").strip().lower()

    # Try to find numbers
    numbers = re.findall(r"[\d.]+", cleaned)
    if not numbers:
        return None

    try:
        value = float(numbers[0])
    except ValueError:
        return None

    # K suffix
    if "k" in cleaned:
        value *= 1000

    # Hourly -> annual (assume 2080 hours/year)
    if "/hr" in cleaned or "per hour" in cleaned or "hourly" in cleaned:
        value *= 2080

    return int(value)
