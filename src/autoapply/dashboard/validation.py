"""Validation and patch-merging for everything the dashboard lets the user edit.

Requests are PARTIAL updates: keys that are present replace the stored value (nested models such as ``eeo``,
``schedule`` or ``apply.email`` are merged key by key; lists and free-form dicts such as ``role_families`` are
replaced whole). Unknown keys are rejected (a typo must never silently do nothing), values are normalised, the
merged document is validated through the pydantic models, and range/format rules that the models do not
express are enforced here. All failures surface as ``ApiError`` 422 with field errors relative to the body.
"""

from __future__ import annotations

import re
import types
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Union, get_args, get_origin
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from autoapply.config import AppConfig
from autoapply.dashboard.errors import ApiError, FieldError, validation_error
from autoapply.models import KnowledgeBase, Profile, SearchProfile

__all__ = [
    "check_answer_fields",
    "check_timezone",
    "clean_kb_payload",
    "patch_profile",
    "patch_search",
    "patch_settings",
    "validate_kb",
]

SETTINGS_KEYS = (
    "mode",
    "daily_cap",
    "timezone",
    "schedule",
    "platforms",
    "workbook",
    "boards",
    "apply",
    "llm",
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+")
_PHONE = re.compile(r"[0-9+().\-\s]+")
_YEAR_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_HH_MM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_TOKEN = re.compile(r"(?!\.+$)[A-Za-z0-9._-]{1,100}")
_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,100}")
_EXPERIENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_INTENT = re.compile(r"[a-z][a-z0-9_]{0,63}")

Path_ = tuple[str | int, ...]


# ------------------------------------------------------------------------------------------ primitives


def _msg(text: str) -> str:
    return text.removeprefix("Value error, ")


def pydantic_errors(exc: ValidationError, prefix: Path_ = ()) -> list[FieldError]:
    """Field errors from a pydantic failure, without echoing the submitted values."""
    errors: list[FieldError] = []
    for item in exc.errors(include_url=False, include_context=False, include_input=False):
        loc: Path_ = tuple(item["loc"])
        if prefix and loc[: len(prefix)] == prefix:
            loc = loc[len(prefix) :]
        errors.append(FieldError(loc or ("body",), _msg(str(item["msg"])), str(item["type"])))
    return errors


def _model_of(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        options = [a for a in get_args(annotation) if a is not type(None)]
        return _model_of(options[0]) if len(options) == 1 else None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def unknown_key_errors(
    model: type[BaseModel],
    data: Mapping[str, Any],
    path: Path_ = (),
    ignore: frozenset[str] = frozenset(),
) -> list[FieldError]:
    """Errors for keys that are not fields of ``model`` (recursing into nested models, lists and dicts)."""
    errors: list[FieldError] = []
    fields = model.model_fields
    for key, value in data.items():
        if key not in fields:
            if key not in ignore:
                errors.append(FieldError((*path, key), "Unknown field.", "extra_forbidden"))
            continue
        annotation = fields[key].annotation
        sub = _model_of(annotation)
        if sub is not None and isinstance(value, Mapping):
            errors += unknown_key_errors(sub, value, (*path, key))
            continue
        origin, args = get_origin(annotation), get_args(annotation)
        if origin is list and args and _model_of(args[0]) and isinstance(value, list):
            item_model = _model_of(args[0])
            assert item_model is not None
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    errors += unknown_key_errors(item_model, item, (*path, key, index))
        elif (
            origin is dict and len(args) == 2 and _model_of(args[1]) and isinstance(value, Mapping)
        ):
            item_model = _model_of(args[1])
            assert item_model is not None
            for name, item in value.items():
                if isinstance(item, Mapping):
                    errors += unknown_key_errors(item_model, item, (*path, key, str(name)))
    return errors


def merge_patch(current: BaseModel, patch: Mapping[str, Any]) -> tuple[dict[str, Any], list[Path_]]:
    """``current`` as a plain dict with ``patch`` applied; also the leaf paths the patch touched."""
    raw = current.model_dump(mode="python")
    touched: list[Path_] = []

    def walk(
        model: BaseModel, target: dict[str, Any], delta: Mapping[str, Any], path: Path_
    ) -> None:
        for key, value in delta.items():
            if key not in type(model).model_fields:
                continue
            child = getattr(model, key)
            if isinstance(child, BaseModel) and isinstance(value, Mapping):
                walk(child, target[key], value, (*path, key))
            else:
                target[key] = value
                touched.append((*path, key))

    walk(current, raw, patch, ())
    return raw, touched


def _plain_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text_problem(value: Any, max_len: int, *, single_line: bool = True) -> str | None:
    if not isinstance(value, str):
        return "Must be text."
    if len(value) > max_len:
        return f"Must be at most {max_len} characters."
    if single_line and _CONTROL.search(value):
        return "Must not contain line breaks or control characters."
    return None


def _clean_list(
    value: Any,
    loc: Path_,
    errors: list[FieldError],
    *,
    max_items: int,
    max_len: int,
    unique: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        errors.append(FieldError(loc, "Must be a list of text values.", "list_type"))
        return []
    if len(value) > max_items:
        errors.append(FieldError(loc, f"At most {max_items} entries are allowed.", "too_long"))
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        problem = _text_problem(item, max_len)
        if problem:
            errors.append(FieldError(loc, problem, "string_type"))
            return []
        text = item.strip()
        key = text.lower()
        if not text or (unique and key in seen):
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def check_timezone(name: Any) -> str | None:
    """Why ``name`` is not a usable IANA timezone, or ``None`` when it is."""
    if not isinstance(name, str) or not name.strip():
        return "Enter an IANA timezone such as America/Chicago."
    try:
        ZoneInfo(name.strip())
    except (KeyError, ValueError, OSError):
        return "Not a known IANA timezone (for example America/Chicago or UTC)."
    return None


def _first_pydantic(
    model: type[BaseModel], raw: dict[str, Any], prefix: Path_, errors: list[FieldError]
) -> Any:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors.extend(pydantic_errors(exc, prefix))
        return None


def _require_object(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise validation_error([FieldError(("body",), "Send a JSON object.", "dict_type")])
    return payload


# ------------------------------------------------------------------------------------------ profile

_PROFILE_TEXT_LIMITS: dict[str, int] = {
    "first_name": 100, "last_name": 100, "preferred_name": 100, "pronouns": 50, "email": 254,
    "phone": 30, "phone_country": 60, "address_line1": 200, "address_line2": 200, "city": 100,
    "state": 60, "postal_code": 20, "country": 60, "linkedin_url": 300, "github_url": 300,
    "portfolio_url": 300, "school": 200, "degree": 200, "major": 200, "minor": 200, "gpa": 20,
    "education_start_date": 30, "graduation_date": 30, "available_start_date": 30,
    "available_end_date": 30, "referral_source": 100, "fallback_resume_path": 500,
}  # fmt: skip
_EEO_FIELDS = ("gender", "race_ethnicity", "hispanic_latino", "veteran_status", "disability_status")


def _normalise_url(value: str) -> str:
    text = value.strip()
    if text and "://" not in text:
        text = "https://" + text
    return text


def patch_profile(config: AppConfig, payload: Any) -> AppConfig:
    """``config`` with the profile patch applied and validated; raises ``ApiError`` (422)."""
    patch = _require_object(payload)
    errors = unknown_key_errors(Profile, patch, ignore=frozenset({"full_name"}))
    body: dict[str, Any] = {k: v for k, v in patch.items() if k != "full_name"}
    for key, value in body.items():
        if key in _PROFILE_TEXT_LIMITS:
            problem = _text_problem(value, _PROFILE_TEXT_LIMITS[key])
            if problem:
                errors.append(FieldError((key,), problem, "string_type"))
        elif key == "eeo" and isinstance(value, Mapping):
            for name, entry in value.items():
                if name in _EEO_FIELDS and (problem := _text_problem(entry, 100)):
                    errors.append(FieldError(("eeo", name), problem, "string_type"))
    if errors:
        raise validation_error(errors)
    for key in ("linkedin_url", "github_url", "portfolio_url"):
        if isinstance(body.get(key), str):
            body[key] = _normalise_url(body[key])
    raw, _ = merge_patch(config.profile, body)
    raw.pop("full_name", None)
    profile = _first_pydantic(Profile, raw, (), errors)
    checked = profile
    if (
        checked is None
    ):  # still report format problems of the other fields alongside the type errors
        broken = {str(e.loc[0]) for e in errors if e.loc}
        checked = _first_pydantic(
            Profile, {k: v for k, v in raw.items() if k not in broken}, (), []
        )
    if checked is not None:
        errors += _profile_format_errors(checked, set(body))
    if errors:
        raise validation_error(errors)
    updated = config.model_copy(deep=True)
    updated.profile = profile
    return AppConfig.model_validate(updated.model_dump(mode="python"))


def _profile_format_errors(profile: Profile, touched: set[str]) -> list[FieldError]:
    errors: list[FieldError] = []

    def bad(name: str, message: str) -> None:
        errors.append(FieldError((name,), message))

    if "email" in touched and profile.email and not _EMAIL.fullmatch(profile.email):
        bad("email", "Enter an email address like name@example.com.")
    phone_bad = not _PHONE.fullmatch(profile.phone) or len(profile.phone_digits) < 10
    if "phone" in touched and profile.phone and phone_bad:
        bad("phone", "Enter a phone number with at least 10 digits (digits, spaces, + ( ) - only).")
    for name in ("education_start_date", "graduation_date"):
        value = getattr(profile, name)
        if name in touched and value and not _YEAR_MONTH.fullmatch(value):
            bad(name, "Use a year and month as YYYY-MM, for example 2028-05.")
    for name in ("available_start_date", "available_end_date"):
        value = getattr(profile, name)
        if name in touched and value:
            try:
                date.fromisoformat(value)
            except ValueError:
                bad(name, "Use a date as YYYY-MM-DD, for example 2027-05-17.")
    start, end = profile.available_start_date, profile.available_end_date
    if not errors and start and end and end < start:
        bad("available_end_date", "The end date must not be before the start date.")
    for name in ("linkedin_url", "github_url", "portfolio_url"):
        value = getattr(profile, name)
        if name in touched and value:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                bad(name, "Enter a web address starting with http:// or https://.")
    resume_path = profile.fallback_resume_path
    if (
        "fallback_resume_path" in touched
        and resume_path
        and not resume_path.lower().endswith(".pdf")
    ):
        bad("fallback_resume_path", "The resume must be a .pdf file.")
    return errors


# ------------------------------------------------------------------------------------------ settings


def _range_int(value: Any, lo: int, hi: int) -> str | None:
    if not _plain_int(value) and not (isinstance(value, float) and value.is_integer()):
        return "Must be a whole number."
    if not lo <= int(value) <= hi:
        return f"Must be between {lo} and {hi}."
    return None


def _rule_run_times(value: Any) -> str | None:
    if not isinstance(value, list) or not value or len(value) > 24:
        return "Give between 1 and 24 times as HH:MM."
    if not all(isinstance(v, str) and _HH_MM.fullmatch(v.strip()) for v in value):
        return "Times must look like 09:30 (24-hour HH:MM)."
    return None


def _rule_days(value: Any) -> str | None:
    if not isinstance(value, list) or not value:
        return "Choose at least one day (0 = Monday .. 6 = Sunday)."
    if not all(_plain_int(v) and 0 <= v <= 6 for v in value):
        return "Days must be whole numbers from 0 (Monday) to 6 (Sunday)."
    return None


def _rule_host(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _HOSTNAME.fullmatch(value.strip()):
        return "Enter a host name such as imap.example.com."
    return None


def _rule_text(max_len: int, *, nullable: bool = False) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        if nullable and value is None:
            return None
        return _text_problem(value, max_len)

    return check


def _rule_model(value: Any) -> str | None:
    if not isinstance(value, str) or not _MODEL_NAME.fullmatch(value.strip()):
        return "Enter a model name such as gpt-4.1-mini."
    return None


def _int_rule(lo: int, hi: int) -> Callable[[Any], str | None]:
    return lambda value: _range_int(value, lo, hi)


_SETTINGS_RULES: dict[Path_, Callable[[Any], str | None]] = {
    ("daily_cap",): _int_rule(0, 50),
    ("timezone",): check_timezone,
    ("schedule", "run_times"): _rule_run_times,
    ("schedule", "days_of_week"): _rule_days,
    ("schedule", "jitter_minutes"): _int_rule(0, 120),
    ("apply", "nav_timeout_s"): _int_rule(5, 300),
    ("apply", "attempt_timeout_s"): _int_rule(60, 3600),
    ("apply", "max_attempts_per_job"): _int_rule(1, 10),
    ("apply", "max_attempts_per_run"): _int_rule(1, 100),
    ("apply", "min_delay_s"): _int_rule(0, 3600),
    ("apply", "max_delay_s"): _int_rule(0, 7200),
    ("apply", "email", "imap_host"): _rule_host,
    ("apply", "email", "imap_port"): _int_rule(1, 65535),
    ("apply", "email", "username"): _rule_text(200, nullable=True),
    ("apply", "email", "mailbox"): _rule_text(100),
    ("apply", "email", "timeout_s"): _int_rule(10, 1800),
    ("llm", "model"): _rule_model,
    ("llm", "timeout_s"): _int_rule(5, 600),
    ("llm", "max_retries"): _int_rule(0, 10),
    ("llm", "max_calls_per_application"): _int_rule(1, 100),
    ("workbook", "path"): _rule_text(500, nullable=True),
    ("workbook", "sheet"): _rule_text(100, nullable=True),
}
_BLANK_TO_NONE: frozenset[Path_] = frozenset(
    {
        ("workbook", "path"),
        ("workbook", "sheet"),
        ("apply", "email", "imap_host"),
        ("apply", "email", "username"),
    }
)


def _flatten(patch: Mapping[str, Any], path: Path_ = ()) -> dict[Path_, Any]:
    flat: dict[Path_, Any] = {}
    for key, value in patch.items():
        if isinstance(value, Mapping) and key not in {"column_map"}:
            flat.update(_flatten(value, (*path, key)))
        else:
            flat[(*path, key)] = value
    return flat


def _set_path(target: dict[str, Any], path: Path_, value: Any) -> None:
    node = target
    for key in path[:-1]:
        node = node[str(key)]
    node[str(path[-1])] = value


def _normalise_settings(patch: dict[str, Any], errors: list[FieldError]) -> None:
    """Trim/dedupe in place: blank optional text -> None, run times, board tokens."""
    flat = _flatten(patch)
    for path in _BLANK_TO_NONE:
        if path in flat and isinstance(flat[path], str) and not flat[path].strip():
            _set_path(patch, path, None)
    if ("schedule", "run_times") in flat and _rule_run_times(
        flat[("schedule", "run_times")]
    ) is None:
        times = [v.strip() for v in flat[("schedule", "run_times")]]
        _set_path(patch, ("schedule", "run_times"), list(dict.fromkeys(times)))
    if ("schedule", "days_of_week") in flat and _rule_days(
        flat[("schedule", "days_of_week")]
    ) is None:
        days = [int(v) for v in flat[("schedule", "days_of_week")]]
        _set_path(patch, ("schedule", "days_of_week"), sorted(set(days)))
    for board in ("greenhouse", "lever", "ashby"):
        path = ("boards", board)
        if path not in flat:
            continue
        tokens = _clean_list(flat[path], path, errors, max_items=200, max_len=100)
        for token in tokens:
            if not _TOKEN.fullmatch(token):
                errors.append(
                    FieldError(
                        path,
                        f"'{token}' is not a board token. Enter just the token (for example 'stripe'), "
                        "using letters, digits, dots, dashes and underscores.",
                    )
                )
                break
        else:
            _set_path(patch, path, tokens)
    column_map = flat.get(("workbook", "column_map"))
    if ("workbook", "column_map") in flat:
        ok = (
            isinstance(column_map, Mapping)
            and len(column_map) <= 50
            and all(
                isinstance(k, str) and isinstance(v, str) and len(k) <= 100 and len(v) <= 100
                for k, v in column_map.items()
            )
        )
        if not ok:
            errors.append(
                FieldError(("workbook", "column_map"), "Must map field names to header text.")
            )


def patch_settings(config: AppConfig, payload: Any) -> AppConfig:
    """Apply a settings patch (``SETTINGS_KEYS`` only); raises ``ApiError`` (422)."""
    patch = dict(_require_object(payload))
    errors: list[FieldError] = []
    for key in list(patch):
        if key not in SETTINGS_KEYS:
            hint = (
                " Use /api/profile."
                if key == "profile"
                else " Use /api/search."
                if key == "search"
                else ""
            )
            errors.append(FieldError((key,), "Unknown field." + hint, "extra_forbidden"))
    if errors:
        raise validation_error(errors)
    errors += unknown_key_errors(AppConfig, patch)
    for key in ("schedule", "platforms", "workbook", "boards", "apply", "llm"):
        if key in patch and not isinstance(patch[key], Mapping):
            errors.append(FieldError((key,), "Expected an object.", "dict_type"))
            patch.pop(key)
    patch = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in patch.items()}
    for value in patch.values():
        if isinstance(value, dict) and isinstance(value.get("email"), Mapping):
            value["email"] = dict(value["email"])
    _normalise_settings(patch, errors)
    flat = _flatten(patch)
    for path, rule in _SETTINGS_RULES.items():
        if path in flat and (problem := rule(flat[path])):
            errors.append(FieldError(path, problem))
    if errors:
        raise validation_error(errors)
    raw, _ = merge_patch(config, patch)
    candidate = _first_pydantic(AppConfig, raw, (), errors)
    if candidate is not None and not errors:
        low, high = candidate.apply.min_delay_s, candidate.apply.max_delay_s
        touched_delay = ("apply", "min_delay_s") in flat or ("apply", "max_delay_s") in flat
        if touched_delay and low > high:
            errors.append(
                FieldError(
                    ("apply", "min_delay_s"), "The minimum delay must not exceed the maximum."
                )
            )
    if errors:
        raise validation_error(errors)
    assert candidate is not None
    return candidate


# ------------------------------------------------------------------------------------------ search


def patch_search(config: AppConfig, payload: Any) -> AppConfig:
    """Apply a search-profile patch; raises ``ApiError`` (422)."""
    patch = dict(_require_object(payload))
    errors = unknown_key_errors(SearchProfile, patch)
    lists = {
        "include_keywords": (100, 100),
        "exclude_title_keywords": (100, 100),
        "preferred_locations": (100, 100),
        "company_allowlist": (300, 150),
        "company_denylist": (300, 150),
    }
    for key, (max_items, max_len) in lists.items():
        if key in patch:
            patch[key] = _clean_list(
                patch[key], (key,), errors, max_items=max_items, max_len=max_len
            )
    if "target_term" in patch:
        problem = _text_problem(patch["target_term"], 100)
        if problem or not str(patch["target_term"]).strip():
            errors.append(
                FieldError(("target_term",), problem or "Enter a term such as Summer 2027.")
            )
    if "recent_days" in patch and (problem := _range_int(patch["recent_days"], 1, 365)):
        errors.append(FieldError(("recent_days",), problem))
    if "min_score" in patch and (
        not _plain_number(patch["min_score"]) or not 0 <= patch["min_score"] <= 100
    ):
        errors.append(FieldError(("min_score",), "Must be a number between 0 and 100."))
    if "role_families" in patch:
        patch["role_families"] = _clean_families(patch["role_families"], errors)
    if errors:
        raise validation_error(errors)
    raw, _ = merge_patch(config.search, patch)
    search = _first_pydantic(SearchProfile, raw, (), errors)
    if errors:
        raise validation_error(errors)
    updated = config.model_copy(deep=True)
    updated.search = search
    return AppConfig.model_validate(updated.model_dump(mode="python"))


def _clean_families(value: Any, errors: list[FieldError]) -> Any:
    if not isinstance(value, Mapping) or len(value) > 50:
        errors.append(
            FieldError(("role_families",), "Must be an object of at most 50 role families.")
        )
        return value
    cleaned: dict[str, Any] = {}
    for name, family in value.items():
        label = str(name).strip()
        loc: Path_ = ("role_families", str(name))
        if not label or len(label) > 60 or _CONTROL.search(label):
            errors.append(
                FieldError(loc, "Family names must be 1-60 characters without control characters.")
            )
            continue
        if not isinstance(family, Mapping):
            errors.append(
                FieldError(loc, "Expected an object with keywords and weight.", "dict_type")
            )
            continue
        entry = dict(family)
        if "keywords" in entry:
            entry["keywords"] = _clean_list(
                entry["keywords"], (*loc, "keywords"), errors, max_items=50, max_len=100
            )
            if not entry["keywords"] and not any(e.loc == (*loc, "keywords") for e in errors):
                errors.append(
                    FieldError((*loc, "keywords"), "Add at least one keyword or remove the family.")
                )
        else:
            errors.append(FieldError((*loc, "keywords"), "Keywords are required."))
        if "weight" in entry and (
            not _plain_number(entry["weight"]) or not 0 <= entry["weight"] <= 1
        ):
            errors.append(FieldError((*loc, "weight"), "Weight must be a number between 0 and 1."))
        cleaned[label] = entry
    return cleaned


# ------------------------------------------------------------------------------------------ knowledge base


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


def clean_kb_payload(payload: Any) -> KnowledgeBase:
    """Validate a knowledge base submitted by the editor; raises ``ApiError`` (422). Full replacement."""
    body = _require_object(payload)
    errors = unknown_key_errors(KnowledgeBase, body, ignore=frozenset({"meta"}))
    if "experiences" not in body or not isinstance(body["experiences"], list):
        errors.append(FieldError(("experiences",), "experiences is required and must be a list."))
        raise validation_error(errors)
    data: dict[str, Any] = {k: v for k, v in body.items() if k != "meta"}
    if len(data["experiences"]) > 100:
        errors.append(FieldError(("experiences",), "At most 100 entries are allowed."))
    experiences: list[Any] = []
    for index, item in enumerate(data["experiences"]):
        if not isinstance(item, Mapping):
            errors.append(FieldError(("experiences", index), "Expected an object.", "dict_type"))
            continue
        entry = dict(item)
        for key in ("organization", "location", "start", "end"):
            if key in entry:
                entry[key] = _blank_to_none(entry[key])
        for key, max_items, max_len in (
            ("bullets", 40, 1000),
            ("skills", 100, 100),
            ("links", 20, 500),
        ):
            if key in entry:
                entry[key] = _clean_list(
                    entry[key], ("experiences", index, key), errors,
                    max_items=max_items, max_len=max_len, unique=key != "bullets",
                )  # fmt: skip
        experiences.append(entry)
    data["experiences"] = experiences
    if "skills" in data:
        data["skills"] = _clean_list(
            data["skills"], ("skills",), errors, max_items=200, max_len=100
        )
    if errors:
        raise validation_error(errors)
    kb = _first_pydantic(KnowledgeBase, data, (), errors)
    if kb is not None:
        errors += validate_kb(kb)
    if errors:
        raise validation_error(errors)
    assert kb is not None
    source = "none" if not kb.experiences else (kb.source if kb.source != "none" else "resume")
    return kb.model_copy(update={"source": source})


def validate_kb(kb: KnowledgeBase) -> list[FieldError]:
    """Semantic checks: unique safe ids, titles present, ``YYYY-MM`` dates, http(s) links."""
    errors: list[FieldError] = []
    seen: set[str] = set()
    for index, exp in enumerate(kb.experiences):
        loc: Path_ = ("experiences", index)
        if not _EXPERIENCE_ID.fullmatch(exp.id):
            errors.append(
                FieldError((*loc, "id"), "Ids use letters, digits, dot, dash, underscore (max 64).")
            )
        elif exp.id.lower() in seen:
            errors.append(FieldError((*loc, "id"), "Ids must be unique."))
        seen.add(exp.id.lower())
        if not exp.title.strip():
            errors.append(FieldError((*loc, "title"), "A title is required."))
        for key, value in (
            ("title", exp.title),
            ("organization", exp.organization),
            ("location", exp.location),
        ):
            if value and (problem := _text_problem(value, 200)):
                errors.append(FieldError((*loc, key), problem))
        if exp.start and not _YEAR_MONTH.fullmatch(exp.start):
            errors.append(FieldError((*loc, "start"), "Use YYYY-MM."))
        if exp.end and not (_YEAR_MONTH.fullmatch(exp.end) or exp.end.lower() == "present"):
            errors.append(FieldError((*loc, "end"), "Use YYYY-MM or 'present'."))
        for link in exp.links:
            if urlsplit(link).scheme not in {"http", "https"}:
                errors.append(
                    FieldError((*loc, "links"), "Links must start with http:// or https://.")
                )
                break
    return errors


# ------------------------------------------------------------------------------------------ answers


def check_answer_fields(body: Mapping[str, Any], *, require_all: bool) -> dict[str, Any]:
    """Validated/normalised fields of a saved-answer request; raises ``ApiError`` (422)."""
    allowed = {"question", "answer", "intent", "answer_kind"}
    errors = [
        FieldError((k,), "Unknown field.", "extra_forbidden") for k in body if k not in allowed
    ]
    out: dict[str, Any] = {}
    for key, limit in (("question", 500), ("answer", 2000)):
        if key not in body:
            continue
        value = body[key]
        problem = _text_problem(value, limit, single_line=key == "question")
        if (
            problem is None
            and isinstance(value, str)
            and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
        ):
            problem = "Must not contain control characters."
        if problem is None and key == "answer" and not value.strip():
            problem = "An answer is required."
        if problem:
            errors.append(FieldError((key,), problem))
        else:
            out[key] = value.strip()
    if "intent" in body:
        intent = body["intent"]
        if intent in (None, ""):
            out["intent"] = None
        elif not isinstance(intent, str) or not _INTENT.fullmatch(intent.strip()):
            errors.append(
                FieldError(
                    ("intent",),
                    "Use lower-case letters, digits and underscores, e.g. felony_conviction.",
                )
            )
        else:
            out["intent"] = intent.strip()
    if "answer_kind" in body:
        if body["answer_kind"] not in {"boolean", "text", "choice", "number"}:
            errors.append(FieldError(("answer_kind",), "Choose boolean, text, choice or number."))
        else:
            out["answer_kind"] = body["answer_kind"]
    if require_all:
        if "answer" not in body:
            errors.append(FieldError(("answer",), "An answer is required."))
        if not out.get("question") and not out.get("intent"):
            errors.append(FieldError(("question",), "Give the question wording or an intent."))
    if errors:
        raise validation_error(errors)
    return out


def api_error_for_config(exc: Exception) -> ApiError:
    """A clean 500 for an unusable ``config.json`` (message only; no traceback)."""
    text = str(exc).splitlines()[0][:300] if str(exc) else "unreadable"
    return ApiError(
        500,
        "config_invalid",
        f"config.json could not be loaded ({text}). Fix or remove the file, then reload.",
    )
