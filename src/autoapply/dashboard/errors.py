"""Error types shared by the dashboard API and its exception handlers.

Every non-2xx JSON body has the same envelope so the JavaScript (and the tests) need one parser::

    {"code": "not_ready", "message": "Human readable text", "detail": "<same text>", ...extras}

``detail`` mirrors FastAPI's convention (a string, or the list of field errors for HTTP 422) and extras
carry structured data such as ``issues`` (readiness) or ``errors`` (field errors).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["ApiError", "FieldError", "validation_error"]


@dataclass(frozen=True)
class FieldError:
    """One problem with one submitted field. ``loc`` is relative to the request body (``("eeo", "gender")``)."""

    loc: tuple[str | int, ...]
    msg: str
    type: str = "value_error"

    def as_dict(self) -> dict[str, Any]:
        return {"loc": list(self.loc), "msg": self.msg, "type": self.type}


class ApiError(Exception):
    """A deliberate, user-facing failure: rendered as JSON with ``status_code`` by the app's handlers."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        payload["detail"] = self.message
        payload.update(self.extra)
        return payload


def validation_error(
    errors: Sequence[FieldError], message: str = "The submitted data is not valid."
) -> ApiError:
    """HTTP 422 carrying field errors as ``detail`` (FastAPI style) and ``errors``."""
    unique: list[FieldError] = []
    seen: set[tuple[str | int, ...]] = set()
    for error in errors:
        if error.loc in seen:
            continue
        seen.add(error.loc)
        unique.append(error)
    listed = [error.as_dict() for error in unique]
    return ApiError(422, "validation_error", message, detail=listed, errors=listed)
