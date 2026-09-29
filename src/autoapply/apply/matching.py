"""Option matching shared by the answer engine and the ATS adapters. CONTRACT FILE: owned by the orchestrator.

Given the value we WANT to give ("Yes", ``False``, ``DECLINE``, "Company website") and the option labels a form
offers, pick the option to click, or ``None`` when no option is a confident match. Returning ``None`` is always
preferable to guessing: callers turn it into a pending question instead of submitting a wrong answer.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

from autoapply.models import DECLINE
from autoapply.normalize import norm_text

_DECLINE = tuple(
    re.compile(p)
    for p in (
        r"\bdecline\b",
        r"\bprefer not\b",
        r"\b(?:do not|don t|dont) (?:wish|want)\b",
        r"\bnot (?:wish|want) to\b",
        r"\brather not\b",
        r"\bchoose not\b",
        r"\bnot to (?:say|disclose|answer|identify|respond|state)\b",
        r"\bno answer\b",
        r"\bunwilling\b",
        r"\bundisclosed\b",
    )
)
_PLACEHOLDER = re.compile(
    r"^(select|please select|choose|choose one|pick one|none selected|-+|\.\.\.)\b"
)
_YES = re.compile(r"^(yes|y|true)\b")
_NO = re.compile(r"^(no|n|false)\b")

# When the wanted value is a referral source, these words in an option label count as the same idea.
_REFERRAL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "company website": (
        "company website",
        "career site",
        "careers site",
        "careers page",
        "corporate website",
        "website",
    ),
    "linkedin": ("linkedin",),
    "indeed": ("indeed",),
    "job board": (
        "job board",
        "indeed",
        "glassdoor",
        "ziprecruiter",
        "handshake",
        "internet",
        "online",
    ),
    "university": (
        "university",
        "college",
        "campus",
        "career fair",
        "career center",
        "handshake",
        "school",
    ),
    "referral": ("referral", "employee", "friend", "referred"),
    "other": ("other",),
}


def is_decline_option(option: str) -> bool:
    """True for "Decline to self-identify", "I don't wish to answer", "Prefer not to say", ..."""
    text = norm_text(option)
    return any(p.search(text) for p in _DECLINE)


def is_placeholder_option(option: str) -> bool:
    """True for "Select...", "-- Please choose --", empty labels."""
    text = norm_text(option)
    return not text or bool(_PLACEHOLDER.match(text))


def parse_bool(value: object) -> bool | None:
    """Interpret ``True``/"Yes"/"n"/"false" as a bool; anything else (including "maybe") -> None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = norm_text(value)
        if text in {"yes", "y", "true", "1"}:
            return True
        if text in {"no", "n", "false", "0"}:
            return False
    return None


def option_polarity(option: str) -> bool | None:
    """Polarity of an option that starts with an explicit yes/no ("Yes, I am", "No, I do not"); else None."""
    text = norm_text(option)
    if _YES.match(text):
        return True
    if _NO.match(text):
        return False
    return None


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in norm_text(text).split()
        if t not in {"a", "an", "the", "of", "to", "i", "am", "are", "is"}
    }


def _match_bool(wanted: bool, options: Sequence[str]) -> str | None:
    candidates = [
        o
        for o in options
        if not is_decline_option(o)
        and not is_placeholder_option(o)
        and option_polarity(o) is wanted
    ]
    if not candidates:
        return None
    exact = [o for o in candidates if norm_text(o) == ("yes" if wanted else "no")]
    return (exact or candidates)[0]


def _match_decline(options: Sequence[str]) -> str | None:
    declines = [o for o in options if is_decline_option(o)]
    if not declines:
        return None
    # Prefer the shortest, most explicit wording ("Decline to self-identify" over a paragraph).
    return sorted(declines, key=lambda o: (0 if norm_text(o).startswith("decline") else 1, len(o)))[
        0
    ]


def _match_referral(wanted: str, options: Sequence[str]) -> str | None:
    key = norm_text(wanted)
    for group, words in _REFERRAL_SYNONYMS.items():
        if key == group or key in words:
            for opt in options:
                text = norm_text(opt)
                if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
                    return opt
    return None


def best_option(
    wanted: str | bool | None, options: Sequence[str], *, intent: str | None = None
) -> str | None:
    """Return the member of ``options`` that best expresses ``wanted`` or ``None`` if unsure.

    Order: decline sentinel -> boolean polarity -> exact -> normalised-equal -> referral synonyms (when
    ``intent == "referral_source"``) -> token containment -> difflib ratio >= 0.85.
    """
    usable = [o for o in options if not is_placeholder_option(o)]
    if wanted is None or not usable:
        return None

    if isinstance(wanted, str) and (wanted == DECLINE or norm_text(wanted) == DECLINE):
        return _match_decline(usable)
    as_bool = parse_bool(wanted)
    if as_bool is not None:
        return _match_bool(as_bool, usable)

    target = str(wanted)
    target_norm = norm_text(target)
    if not target_norm:
        return None
    for opt in usable:  # exact (case-insensitive) then normalised equality
        if opt.strip().lower() == target.strip().lower():
            return opt
    for opt in usable:
        if norm_text(opt) == target_norm:
            return opt
    if intent == "referral_source" and (hit := _match_referral(target, usable)):
        return hit

    target_tokens = _tokens(target)
    non_decline = [o for o in usable if not is_decline_option(o)]
    contained = [
        o
        for o in non_decline
        if target_tokens and (target_tokens <= _tokens(o) or _tokens(o) <= target_tokens)
    ]
    if len(contained) >= 1:
        # the tightest containing option wins ("Asian" over "Asian or Pacific Islander" when both contain it)
        return sorted(contained, key=lambda o: abs(len(_tokens(o)) - len(target_tokens)))[0]
    scored = [
        (difflib.SequenceMatcher(None, target_norm, norm_text(o)).ratio(), o) for o in non_decline
    ]
    if scored:
        ratio, opt = max(scored, key=lambda pair: pair[0])
        if ratio >= 0.85:
            return opt
    if intent == "referral_source":
        return next((o for o in usable if norm_text(o) == "other"), None)
    return None
