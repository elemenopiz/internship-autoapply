"""Deterministic answers: map a question to a stored fact, or decline to answer.

``resolve`` never guesses. It returns a ``Resolution`` whose ``value`` is None
when no stored fact answers the question; ``category`` then says whether the
question may go to the LLM drafter ("none") or must never be generated
("eeo", "consent", "credential", "file", "fact").

Screening-answer keys read from config.json ``profile.screening_answers``:
    work_authorization, visa_sponsorship (Yes = needs sponsorship),
    major, graduation_date, gpa, class_standing, currently_enrolled,
    earliest_start_date, willing_to_relocate, how_did_you_hear,
    salary_expectation, us_citizen, over_18, onsite_ok,
    acknowledge_policies (Yes = may tick standard privacy/acknowledgment boxes),
    gender, race, hispanic_latino (legacy: ethnicity), veteran_status,
    disability_status
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from bot.apply.smart.candidate import Candidate, normalize
from bot.apply.smart.questions import Question

#: Categories whose questions are never sent to the LLM drafter.
NEVER_DRAFT = frozenset({"eeo", "consent", "credential", "file", "fact", "follow_up"})

_DECLINE = ("decline", "prefer not", "don t wish", "do not wish", "choose not",
            "not wish to", "rather not", "not to answer", "not to disclose",
            "i don t want to")

_EEO_TERMS = ("gender", "sex", "race", "ethnicity", "ethnic", "hispanic", "latino",
              "latinx", "veteran", "disability", "disabled", "pronoun", "pronouns",
              "sexual orientation", "transgender", "lgbtq", "lgbtqia", "military")

#: Optional messaging opt-ins only. Not "marketing" alone ("marketing
#: experience?") and not "future opportunities" (being considered helps).
_OPT_IN_TERMS = ("sms", "text message", "text messages", "whatsapp", "marketing emails",
                 "marketing communications", "marketing messages", "newsletter")

#: Acknowledgments that are really commitments (to relocate, to work on site)
#: — never covered by the acknowledge_policies opt-in.
_COMMITMENT_TERMS = ("in person", "in-person", "on site", "onsite", "relocate", "relocation",
                     "commute", "located in", "work from the office", "hybrid")

_CONSENT_TERMS = ("consent", "acknowledge", "acknowledgement", "acknowledgment",
                  "i agree", "agree to", "agreement", "privacy", "terms and conditions",
                  "terms of", "certify", "attest", "signature", "e signature",
                  "arbitration", "gdpr", "data processing", "i understand",
                  "read and understood")


@dataclass(frozen=True)
class Resolution:
    value: str | None
    source: str = ""
    category: str = "none"
    note: str = ""

    @property
    def answered(self) -> bool:
        return self.value is not None


_NO_ANSWER = Resolution(None)


def _has(hay: str, *terms: str) -> bool:
    """Whole-word (or whole-phrase) match of any term in a normalized haystack."""
    return any(re.search(rf"(?<![a-z0-9]){re.escape(normalize(t))}(?![a-z0-9])", hay)
               for t in terms)


def _hay(q: Question) -> str:
    return normalize(f"{q.label} {q.name.replace('_', ' ')}")


def pick_option(options: tuple[str, ...], *wanted: str) -> str | None:
    """The option matching the first satisfiable ``wanted`` value, or None.

    Per wanted value: exact (normalized) match, else the single option that
    starts with it as whole words, else the single option containing it as
    whole words. Ambiguity returns None — never a near-miss.
    """
    normed = [(opt, normalize(opt)) for opt in options]
    for want in wanted:
        w = normalize(want)
        if not w:
            continue
        exact = [o for o, n in normed if n == w]
        if exact:
            return exact[0]
        starts = [o for o, n in normed if n.startswith(w + " ")]
        if len(starts) == 1:
            return starts[0]
        inner = [o for o, n in normed
                 if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", n)]
        if len(inner) == 1:
            return inner[0]
    return None


def _choice(q: Question, value: str, *alternatives: str) -> str | None:
    """Free-text fields take ``value``; option fields must match one option."""
    if not q.options:
        return value if q.kind not in ("radio", "checkboxes", "select") else None
    return pick_option(q.options, value, *alternatives)


def _yes_no(q: Question, flag: bool) -> str | None:
    if q.options:
        return pick_option(q.options, "yes" if flag else "no")
    return "Yes" if flag else "No"


def _decline_option(q: Question) -> str | None:
    for opt in q.options:
        n = normalize(opt)
        if any(d in n for d in _DECLINE):
            return opt
    return None


def _format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return phone


# --- sensitive categories ------------------------------------------------------


def _eeo(q: Question, hay: str, cand: Candidate) -> Resolution:
    """Self-identification: the configured answer, else the form's decline option."""
    if _has(hay, "hispanic", "latino", "latinx"):
        key, value = "hispanic_latino", cand.answer("hispanic_latino") or cand.answer("ethnicity")
        # "Not Hispanic or Latino" answers a Yes/No "Are you Hispanic/Latino?"
        if value and q.options and pick_option(q.options, value) is None:
            is_not = normalize(value).startswith(("not ", "no "))
            yes_no = pick_option(q.options, "no" if is_not else "yes")
            if yes_no:
                return Resolution(yes_no, f"screening.{key}", "eeo")
    elif _has(hay, "gender", "sex"):
        key, value = "gender", cand.answer("gender")
    elif _has(hay, "race", "ethnicity", "ethnic"):
        key, value = "race", cand.answer("race")
    elif _has(hay, "military"):
        # Military service status is not the same fact as protected-veteran status.
        key, value = "military_status", cand.answer("military_status")
    elif _has(hay, "veteran"):
        key, value = "veteran_status", cand.answer("veteran_status")
    elif _has(hay, "disability", "disabled"):
        key, value = "disability_status", cand.answer("disability_status")
    else:
        key, value = "undisclosed", ""
    if q.options:
        picked = (pick_option(q.options, value) if value else None) or _decline_option(q)
        if picked:
            return Resolution(picked, f"screening.{key}" if picked != _decline_option(q)
                              else "decline", "eeo")
        return Resolution(None, category="eeo", note="no matching or decline option")
    if value and q.kind in ("text", "textarea"):
        return Resolution(value, f"screening.{key}", "eeo")
    return Resolution(None, category="eeo", note="self-identification left to you")


def _consent(q: Question, cand: Candidate) -> Resolution:
    """Only standard acknowledgment boxes, and only when the user opted in."""
    if cand.flag("acknowledge_policies") is not True:
        return Resolution(None, category="consent",
                          note="acknowledgment — set acknowledge_policies: 'Yes' to allow")
    if q.kind == "checkbox":
        return Resolution("Yes", "screening.acknowledge_policies", "consent")
    if q.options:
        picked = pick_option(q.options, "I agree", "I acknowledge", "I understand",
                             "I consent", "Yes", "Agree", "Acknowledge")
        if picked:
            return Resolution(picked, "screening.acknowledge_policies", "consent")
    return Resolution(None, category="consent",
                      note="signature or free-text attestation — never auto-filled")


def _file(q: Question, hay: str, resume: Path | None, cover: Path | None) -> Resolution:
    if _has(hay, "cover letter", "cover"):
        if cover is not None:
            return Resolution(str(cover), "generated cover letter", "file")
        return Resolution(None, category="file", note="cover_letter")
    if _has(hay, "resume", "cv", "resumé") or not hay.strip() or hay.strip() in ("file", "attach"):
        if resume is not None:
            return Resolution(str(resume), "base resume", "file")
        return Resolution(None, category="file", note="no resume file")
    return Resolution(None, category="file", note=f"needs a document: {q.label}")


# --- factual rules -------------------------------------------------------------


#: Words marking a name field that asks about SOMEONE ELSE (a referrer, a
#: relative, a reference) — never filled with the candidate's own name.
_THIRD_PARTY = ("referred", "referral", "referrer", "associate", "family", "relative",
                "reference", "emergency", "recruiter", "manager", "supervisor", "spouse",
                "parent", "guardian", "employee who", "contact person")


def _identity(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    p = cand.profile
    own_name = not _has(hay, *_THIRD_PARTY)
    if own_name and _has(hay, "first name", "given name", "preferred name", "preferred first name"):
        return Resolution(p.first_name, "profile.first_name", "fact")
    if own_name and _has(hay, "last name", "surname", "family name"):
        return Resolution(p.last_name, "profile.last_name", "fact")
    if _has(hay, "middle name", "middle initial"):
        return Resolution(None, category="fact")
    if _has(hay, "email", "e mail"):
        return Resolution(p.email, "profile.email", "fact")
    if _has(hay, "phone", "mobile", "cell"):
        if q.options or _has(hay, "country code", "country"):
            picked = pick_option(q.options, "United States", "United States +1", "+1", "US")
            if not picked and not q.options and q.kind == "combobox" and _is_us(p.country):
                picked = "United States"  # searchable country-code picker
            return Resolution(picked, "profile.phone_country", "fact") if picked else None
        return Resolution(_format_phone(p.phone), "profile.phone", "fact")
    if _has(hay, "linkedin"):
        return Resolution(p.linkedin_url or None, "profile.linkedin_url", "fact")
    if _has(hay, "github"):
        return Resolution(cand.answer("github_url") or None, "screening.github_url", "fact")
    if _has(hay, "website", "portfolio", "personal site", "personal url"):
        return Resolution(p.portfolio_url or None, "profile.portfolio_url", "fact")
    if _has(hay, "twitter", "x handle", "instagram"):
        return Resolution(None, category="fact")
    if own_name and _has(hay, "full name", "legal name", "name") and not _has(
            hay, "company", "employer", "school", "university", "college", "file",
            "user name", "username", "pronounce", "nickname"):
        return Resolution(p.full_name, "profile.full_name", "fact")
    return None


def _is_us(country: str) -> bool:
    return normalize(country) in ("united states", "united states of america", "usa", "us", "u s")


#: "Are you currently located in the Fort Wayne, IN area?" (normalized text)
_LOCATED_IN = re.compile(r"\b(?:located|based|living|residing|reside|live)\s+(?:in|within|near)\s+(.+)$")
_US_WORDS = ("united states", "usa", "u s", "us", "north america", "the states")
_FOREIGN_WORDS = ("canada", "mexico", "europe", "eu", "uk", "united kingdom", "emea", "apac", "asia",
                  "india", "germany", "france", "ireland", "australia", "latin america", "latam",
                  "south america", "central america")


def _located_in(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    """'Are you currently located in <place>?' answered from the profile's city.

    Yes when <place> names the candidate's city, state or country; No only
    when it names a different US state/city or a foreign region; else unknown.
    """
    if not q.options or {normalize(o) for o in q.options} - {"yes", "no"}:
        return None
    m = _LOCATED_IN.search(hay)
    if not m or _has(hay, "relocate", "willing", "able to", "commute"):
        return None
    place = m.group(1)
    p = cand.profile
    abbrev = _STATE_ABBREV.get(p.state.lower(), "")
    if (p.city and _has(place, p.city)) or (p.state and _has(place, p.state)) \
            or (abbrev and re.search(rf"\b{abbrev}\b", q.label)):
        return Resolution(pick_option(q.options, "yes"), "profile.location", "fact")
    if _is_us(p.country) and _has(place, *_US_WORDS):
        return Resolution(pick_option(q.options, "yes"), "profile.country", "fact")
    other_state = any(_has(place, name) for name in _STATE_ABBREV) or any(
        re.search(rf",\s*{ab}\b", q.label) for ab in _STATE_ABBREV.values())
    if other_state or (_is_us(p.country) and _has(place, *_FOREIGN_WORDS)):
        return Resolution(pick_option(q.options, "no"), "profile.location", "fact")
    return None


_FULL_ADDRESS = ("full address", "permanent address", "legal address", "complete address",
                 "residential address", "permanent legal address", "current address")


def _location(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    p = cand.profile
    located = _located_in(q, hay, cand)
    if located is not None:
        return located
    if _has(hay, "street", "address line", "mailing address", "home address") or (
            _has(hay, "address") and not _has(hay, "email")):
        if (q.kind == "textarea" or _has(hay, *_FULL_ADDRESS)) and p.address_line1 and p.city:
            full = f"{p.address_line1}, {p.city}, {p.state} {p.zip_code}".strip().rstrip(",")
            return Resolution(full, "profile.address", "fact")
        return Resolution(p.address_line1 or None, "profile.address_line1", "fact")
    if _has(hay, "zip", "postal"):
        return Resolution(p.zip_code or None, "profile.zip_code", "fact")
    if q.kind == "combobox" and _has(hay, "location"):
        # location autocompletes list "City, State, Country" — search the pair
        return Resolution(f"{p.city}, {p.state}", "profile.location", "fact")
    if _has(hay, "city") and _has(hay, "country") and not q.options and p.city and p.country:
        # "In which city and country are you currently located?"
        return Resolution(", ".join(x for x in (p.city, p.state, p.country) if x),
                          "profile.location", "fact")
    if _has(hay, "city") and not _has(hay, "ethnicity"):
        return Resolution(_choice(q, p.city), "profile.city", "fact")
    if _has(hay, "state", "province") and not _has(hay, "statement"):
        return Resolution(_choice(q, p.state, _STATE_ABBREV.get(p.state.lower(), "")),
                          "profile.state", "fact")
    if _has(hay, "country") and not _has(hay, "countries you"):
        return Resolution(_choice(q, p.country, "United States of America", "USA", "US"),
                          "profile.country", "fact")
    if _has(hay, "location", "where are you located", "where do you live", "based in",
            "current city"):
        loc = f"{p.city}, {p.state}" if p.city and p.state else p.location
        return Resolution(_choice(q, loc, p.city) if q.options else loc,
                          "profile.location", "fact")
    return None


_GRAD_TERMS = ("graduation", "grad date", "grad year", "expected to graduate",
               "expecting to graduate", "when do you graduate", "when will you graduate",
               "anticipated graduation", "expected completion", "graduating")
#: "graduate degree/student/school" is about grad school, not the graduation date.
_GRAD_DEGREE = ("graduate degree", "graduate program", "graduate school", "graduate student",
                "graduate studies")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def _education_dates(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    """Start/End date month/year inside an EDUCATION section of the form.

    End = graduation; start = screening 'education_start_date' ("August 2024").
    Outside an education section these labels are ambiguous and left alone.
    """
    if _has(hay, "start") and _has(hay, "current school", "at school", "university",
                                   "college", "enrollment", "enrolled"):
        # "Start Date at Current School:" with term options ("Fall 2024")
        start = cand.answer("education_start_date")
        if not start:
            return Resolution(None, category="fact", note="education start date unknown")
        value = date_choice(q.options, start, start=True) if q.options else start
        return Resolution(value, "screening.education_start_date", "fact")
    m = re.search(r"\b(start|end)\b.*\b(month|year)\b", hay)
    if not m or "education" not in f"{q.context} {hay}":
        return None
    which, part = m.groups()
    source = cand.answer("graduation_date") if which == "end" else cand.answer("education_start_date")
    if not source:
        return Resolution(None, category="fact", note=f"education {which} date unknown")
    if part == "year":
        value = (re.findall(r"\d{4}", source) or [""])[0]
    else:
        value = next((mo.capitalize() for mo in _MONTHS if mo in source.lower()), "")
    if not value:
        return Resolution(None, category="fact")
    key = "graduation_date" if which == "end" else "education_start_date"
    return Resolution(_choice(q, value), f"screening.{key}", "fact")


def _education(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    ed = cand.education
    if _has(hay, "gpa", "grade point"):
        gpa = cand.answer("gpa") or (ed.gpa if ed else "")
        if not gpa:
            return Resolution(None, category="fact")
        return Resolution(_gpa_choice(q, gpa), "education.gpa", "fact")
    if _has(hay, "high school") and _has(hay, "graduate", "graduated", "graduation", "year"):
        v = cand.answer("high_school_graduation_year")
        return Resolution(_choice(q, v) if v else None,
                          "screening.high_school_graduation_year", "fact")
    if _has(hay, "highest") and _has(hay, "achieved", "completed", "obtained", "attained"):
        # The degree in progress is NOT the highest completed level.
        v = cand.answer("highest_education_completed")
        return Resolution(_choice(q, v) if v else None,
                          "screening.highest_education_completed", "fact")
    if _has(hay, *_GRAD_TERMS) and not _has(hay, *_GRAD_DEGREE):
        grad = cand.answer("graduation_date") or (ed.graduation if ed else "")
        if not grad:
            return Resolution(None, category="fact")
        year = (re.findall(r"\d{4}", grad) or [""])[0]
        if q.input_type == "date":
            return Resolution(None, category="fact", note="exact graduation day unknown")
        if q.options:
            return Resolution(date_choice(q.options, grad), "screening.graduation_date", "fact")
        return Resolution(year if _has(hay, "year") and not _has(hay, "month") else grad,
                          "screening.graduation_date", "fact")
    if _has(hay, "class standing", "year in school", "academic year", "class year",
            "current year of study", "year of study", "school year"):
        return Resolution(_choice(q, cand.answer("class_standing")) if cand.answer(
            "class_standing") else None, "screening.class_standing", "fact")
    if _has(hay, "currently enrolled", "current student", "enrolled in",
            "currently a student", "are you a student"):
        flag = cand.flag("currently_enrolled")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.currently_enrolled", "fact")
    if _has(hay, "minor"):
        minors = ", ".join(ed.minors) if ed else ""
        return Resolution(_choice(q, minors) if minors else None, "education.minors", "fact")
    if _has(hay, "major", "field of study", "discipline", "area of study", "concentration",
            "program of study"):
        major = cand.answer("major") or (ed.major if ed else "")
        if not major:
            return Resolution(None, category="fact")
        return Resolution(_choice(q, major, "Information Systems", "Management Information Systems",
                                  "MIS", "Business"), "screening.major", "fact")
    if _has(hay, "degree", "level of education", "highest education", "education level") \
            and not _has(hay, *_GRAD_DEGREE):
        if not ed:
            return Resolution(None, category="fact")
        return Resolution(_choice(q, ed.degree, "Bachelor's Degree", "Bachelor's", "Bachelors",
                                  "Bachelor", "BBA", "Undergraduate"), "education.degree", "fact")
    if _has(hay, "school", "university", "college", "institution") and not _has(hay, "high school"):
        if not ed:
            return Resolution(None, category="fact")
        return Resolution(_choice(q, ed.school, "University of Texas at Austin",
                                  "University of Texas - Austin", "UT Austin"),
                          "education.school", "fact")
    return None


def _authorization(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    # Canadian work authorization is a different fact from US authorization.
    canada = _has(hay, "canada", "canadian")
    if canada and _has(hay, "sponsor", "sponsorship", "authorized", "authorised", "eligible",
                       "legally", "work permit", "right to work"):
        key = ("visa_sponsorship_canada" if _has(hay, "sponsor", "sponsorship")
               else "work_authorization_canada")
        flag = cand.flag(key)
        if flag is None:
            return Resolution(None, category="fact", note=f"answer {key} in config")
        answer = (not flag) if _has(hay, "without") else flag
        return Resolution(_yes_no(q, answer), f"screening.{key}", "fact")
    # "...require us to file a petition or application for employment-based status"
    petition = _has(hay, "petition", "employment based status", "employment based visa",
                    "h 1b", "h1b", "work visa") and _has(hay, "require", "need", "file")
    if _has(hay, "sponsor", "sponsorship") or petition or (
            _has(hay, "visa") and _has(hay, "require", "need")):
        needs = cand.flag("visa_sponsorship")
        if needs is None:
            return Resolution(None, category="fact")
        answer = (not needs) if _has(hay, "without") else needs
        return Resolution(_yes_no(q, answer), "screening.visa_sponsorship", "fact")
    if _has(hay, "authorized to work", "authorised to work", "legally authorized",
            "eligible to work", "right to work", "work authorization", "legally eligible",
            "legally able to work"):
        ok = cand.flag("work_authorization")
        if ok is None:
            return Resolution(None, category="fact")
        value = _yes_no(q, ok)
        if value is None and q.options and ok:
            # Status options: "I am a U.S. Citizen", "Lawful Permanent Resident", ...
            status = cand.answer("citizenship_status")
            if status:
                value = pick_option(q.options, status)
            if value is None and cand.flag("us_citizen") is True:
                value = pick_option(q.options, "U.S. Citizen", "US Citizen", "citizen")
        if value is None and ok and cand.flag("visa_sponsorship") is False:
            # Sentence options: "I am authorized to work in the U.S. for any employer"
            value = pick_option(q.options, "for any employer", "any employer",
                                "without sponsorship", "without restriction",
                                "will not require sponsorship", "do not require sponsorship",
                                "not require sponsorship", "no sponsorship required")
        return Resolution(value, "screening.work_authorization", "fact")
    if _has(hay, "citizen", "citizenship"):
        yes_no_only = {normalize(o) for o in q.options} <= {"yes", "no"}
        if q.options and not yes_no_only:  # "Citizenship Status": several categories
            status = cand.answer("citizenship_status")
            return Resolution(pick_option(q.options, status) if status else None,
                              "screening.citizenship_status", "fact")
        flag = cand.flag("us_citizen")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.us_citizen", "fact")
    if _has(hay, "essential functions"):
        flag = cand.flag("essential_functions")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.essential_functions", "fact")
    if _has(hay, "18 years", "at least 18", "over 18", "age of 18"):
        flag = cand.flag("over_18")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.over_18", "fact")
    if _has(hay, "security clearance", "clearance"):
        return Resolution(None, category="fact")
    return None


def _logistics(q: Question, hay: str, cand: Candidate, company: str) -> Resolution | None:
    if _has(hay, "start date", "earliest start", "available to start", "when can you start",
            "able to start", "start your internship", "available to begin", "begin employment",
            "available from", "availability date", "date available", "date of availability",
            "when are you available"):
        v = cand.answer("earliest_start_date")
        if not v:
            return Resolution(None, category="fact")
        value = date_choice(q.options, v, start=True) if q.options else v
        return Resolution(value, "screening.earliest_start_date", "fact")
    if _has(hay, "relocate", "relocation"):
        flag = cand.flag("willing_to_relocate")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.willing_to_relocate", "fact")
    if _has(hay, "on site", "onsite", "in person", "in office", "commute", "hybrid"):
        flag = cand.flag("onsite_ok")
        return Resolution(_yes_no(q, flag) if flag is not None else None,
                          "screening.onsite_ok", "fact")
    if _has(hay, "hear about", "heard about", "hear of", "how did you find", "referral source",
            "where did you find", "how did you learn about", "applicant source",
            "source of application", "where did you learn") or normalize(q.label) == "source":
        v = cand.answer("how_did_you_hear")
        if not v:
            return Resolution(None, category="fact")
        return Resolution(_choice(q, v, "University", "Career Fair", "Job Board", "Other"),
                          "screening.how_did_you_hear", "fact")
    if _has(hay, "referred", "referral", "referrer", "who referred"):
        if q.options:  # "Were you referred?" Yes/No
            flag = cand.flag("was_referred")
            return Resolution(_yes_no(q, flag) if flag is not None else None,
                              "screening.was_referred", "fact")
        return Resolution(None, category="fact")  # referrer's name: never invented
    if _asks_prior_employment(hay, company):
        worked_here = normalize(company) in cand.organizations() if company else False
        if worked_here:
            return Resolution(None, category="fact", note="you worked with this company")
        never = _yes_no(q, False) if not q.options else (
            pick_option(q.options, "no", "i have never", "never", "i have not", "no i have not"))
        return Resolution(never, "record: no prior employment here", "fact")
    if _has(hay, "salary", "compensation", "pay expectation", "expected pay", "hourly rate",
            "desired pay"):
        v = cand.answer("salary_expectation")
        return Resolution(_choice(q, v) if v else None, "screening.salary_expectation", "fact")
    if _has(hay, "text message", "sms", "whatsapp", "text messages"):
        if q.kind == "checkbox":
            return Resolution(None, category="fact")  # opt-ins stay unticked
        return Resolution(_yes_no(q, False), "default: no marketing opt-in", "fact")
    return None


def is_follow_up(q: Question) -> bool:
    """A free-text 'If yes, please explain…' box that depends on the previous answer.

    Only text fields count, and only when the label STARTS with the cue — a
    yes/no question whose long label merely mentions 'if so' is a real question.
    """
    if q.kind not in ("text", "textarea"):
        return False
    raw = re.sub(r"^\s*\(?[a-z0-9]{1,2}[).]\s*", "", q.label.lower())  # drop "(a)" / "1."
    return normalize(raw).startswith("if ")


def _asks_prior_employment(hay: str, company: str) -> bool:
    """'Have you worked for <us> before?' — not 'Have you ever worked with SQL?'."""
    if _has(hay, "previously worked for", "previously worked at", "previously been employed",
            "previously employed", "ever been employed", "ever worked for", "ever worked at",
            "former employee", "worked for this company", "worked here before",
            "prior employment", "currently or previously", "previously interned",
            "ever interned"):
        return True
    if company and normalize(company) in hay:
        return _has(hay, "worked for", "worked at", "employed by", "employee of", "interned at",
                    "employment history")
    return False


def _prepared(q: Question, hay: str, cand: Candidate) -> Resolution | None:
    """User-written answers win over every rule (except credentials/EEO)."""
    for key, answer in cand.facts.prepared_answers.items():
        k = normalize(key)
        if k and (k in hay or hay in k) and answer:
            value = pick_option(q.options, answer) if q.options else answer
            if value is not None:
                return Resolution(value, f"prepared_answers[{key!r}]", "fact")
    return None


def resolve(q: Question, cand: Candidate, *, resume: Path | None = None,
            cover_letter: Path | None = None, company: str = "") -> Resolution:
    """Answer ``q`` from stored facts, or say why it has no stored answer."""
    hay = _hay(q)
    if q.kind == "password" or _has(hay, "password"):
        return Resolution(None, category="credential", note="password — never filled")
    if q.kind == "file":
        return _file(q, hay, resume, cover_letter)
    if _has(hay, *_EEO_TERMS):
        return _eeo(q, hay, cand)
    prepared = _prepared(q, hay, cand)
    if prepared:
        return prepared
    if is_follow_up(q):
        # "If yes, please specify..." depends on the previous answer — the
        # applier fills "N/A" when that answer was No, else leaves it.
        return Resolution(None, category="follow_up", note="follow-up to another answer")
    if _has(hay, *_OPT_IN_TERMS):
        # Declining an optional marketing/SMS opt-in is always a safe answer.
        if q.kind == "checkbox":
            return Resolution(None, category="fact", note="opt-in left unticked")
        no = _yes_no(q, False)
        return Resolution(no, "default: decline opt-in", "fact") if no else Resolution(
            None, category="fact")
    if _has(hay, *_CONSENT_TERMS):
        if _has(hay, *_COMMITMENT_TERMS):
            return Resolution(None, category="consent",
                              note="work-location commitment — answer this yourself")
        return _consent(q, cand)
    # Order matters: authorization/logistics sentences often mention a
    # "location" or "country", so they are matched before the location rule;
    # education "Start date month" must not be read as the internship start.
    for rule in (_identity, _authorization, _education_dates):
        hit = rule(q, hay, cand)
        if hit is not None:
            return hit
    hit = _logistics(q, hay, cand, company)
    if hit is not None:
        return hit
    for rule in (_education, _location):
        hit = rule(q, hay, cand)
        if hit is not None:
            return hit
    return _NO_ANSWER


# --- helpers -----------------------------------------------------------------

_STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _month_index(text: str) -> int | None:
    low = text.lower()
    for i, name in enumerate(_MONTHS, start=1):
        if name in low or re.search(rf"\b{name[:3]}\b", low):
            return i
    return None


def date_choice(options: tuple[str, ...], date_text: str, start: bool = False) -> str | None:
    """Pick the option describing ``date_text`` ('May 2028'), in any common shape:
    'May 2028', 'Spring 2028', '2028 - Spring', '2028', or a range such as
    'January 2028 - July 2028'. None when no option clearly matches."""
    year = (re.findall(r"\d{4}", date_text) or [""])[0]
    season = _season(date_text, start)
    season_word = season.split(" ")[0] if season else ""
    candidates = [date_text, season, f"{year} {season_word}".strip(), f"{year} - {season_word}".strip()]
    hit = pick_option(options, *[c for c in candidates if c])
    if hit:
        return hit
    month = _month_index(date_text)
    if month:  # month-only dropdown ("January" ... "December")
        hit = pick_option(options, _MONTHS[month - 1])
        if hit:
            return hit
    if year and month:
        target = int(year) * 12 + month
        for opt in options:
            ends = re.findall(r"([A-Za-z]+)\s+(\d{4})", opt)
            if len(ends) == 2:
                (m1, y1), (m2, y2) = ends
                i1, i2 = _month_index(m1), _month_index(m2)
                if i1 and i2 and int(y1) * 12 + i1 <= target <= int(y2) * 12 + i2:
                    return opt
    return pick_option(options, year) if year else None


def _season(text: str, start: bool = False) -> str:
    """Academic term of a month: 'May 2028' -> 'Spring 2028'.

    An August START is the Fall term; an August GRADUATION is Summer.
    """
    month = normalize(text).split(" ")[0] if text else ""
    year = (re.findall(r"\d{4}", text) or [""])[0]
    terms = {"january": "Spring", "jan": "Spring", "may": "Spring", "june": "Spring",
             "september": "Fall", "december": "Fall", "dec": "Fall"}
    terms["august"] = terms["aug"] = "Fall" if start else "Summer"
    season = terms.get(month, "")
    return f"{season} {year}".strip() if season else ""


def _gpa_choice(q: Question, gpa: str) -> str | None:
    """GPA text, or the option range that contains it ('3.5 - 4.0')."""
    if not q.options:
        return gpa
    exact = pick_option(q.options, gpa)
    if exact:
        return exact
    try:
        value = float(gpa)
    except ValueError:
        return None
    # Single-value options ("4.0", "3.9", "3.8"): the highest one NOT above the
    # real GPA — 3.85 reports 3.8, never rounds up to 3.9.
    singles = []
    for opt in q.options:
        m = re.fullmatch(r"\s*(\d\.\d+)\s*", opt)
        if m:
            singles.append((float(m.group(1)), opt))
    if singles:
        below = [s for s in singles if s[0] <= value]
        return max(below)[1] if below else None
    for opt in q.options:
        nums = [float(n) for n in re.findall(r"\d\.\d+|\d", opt)]
        if len(nums) >= 2 and min(nums[:2]) <= value <= max(nums[:2]):
            return opt
        if len(nums) == 1 and ("+" in opt or "above" in opt.lower()) and value >= nums[0]:
            return opt
    return None
