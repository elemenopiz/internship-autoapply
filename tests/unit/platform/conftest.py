"""Shared helpers for the platform-service tests: a fictional key, a fake ``openai`` client, error factories.

Everything here is hermetic. ``FakeSDK`` replays scripted outcomes; the error factories build genuine ``openai``
exception objects (so the production classification code runs against the real types) on top of the HTTP library
the installed SDK uses.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

import openai
import pytest
from pydantic import BaseModel

from autoapply.llm import OpenAIClient

try:  # openai >= 3 is built on httpx2; older SDKs use httpx
    import httpx2 as http
except ImportError:  # pragma: no cover - depends on the installed SDK
    import httpx as http

# Obviously fictional: never a real credential.
FICTIONAL_KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"

_STATUS_CLASSES: dict[int, type[Exception]] = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    403: openai.PermissionDeniedError,
    404: openai.NotFoundError,
    409: openai.ConflictError,
    422: openai.UnprocessableEntityError,
    429: openai.RateLimitError,
}


class Plan(BaseModel):
    title: str
    score: int


class LooseDict(BaseModel):
    """Free-form dict field: strict structured outputs cannot express it, so JSON mode is required."""

    data: dict[str, str]


def _request() -> Any:
    return http.Request("POST", "http://localhost:9/v1/chat/completions")


def status_error(
    status: int,
    *,
    message: str = "provider says no",
    code: str | None = None,
    param: str | None = None,
    error_type: str = "invalid_request_error",
    headers: dict[str, str] | None = None,
) -> Exception:
    """A real ``openai`` status error, exactly as the SDK builds it from an HTTP error response."""
    if status in _STATUS_CLASSES:
        cls = _STATUS_CLASSES[status]
    elif status >= 500:
        cls = openai.InternalServerError
    else:
        cls = openai.APIStatusError
    response = http.Response(status, request=_request(), headers=headers or {})
    body = {"message": message, "type": error_type, "param": param, "code": code}
    return cls(message, response=response, body=body)


def timeout_error() -> Exception:
    return openai.APITimeoutError(request=_request())


def connection_error() -> Exception:
    return openai.APIConnectionError(request=_request())


def completion(
    *,
    parsed: object = None,
    content: str | None = None,
    refusal: str | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
) -> Any:
    """A minimal chat-completion object with the attributes the client reads."""
    message = types.SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class FakeSDK:
    """Stands in for ``openai.OpenAI``: replays scripted outcomes and records each request.

    An outcome is an exception (raised), a callable (called with the request kwargs) or a completion object.
    ``calls`` holds one dict per request: ``{"method": "parse" | "create", **kwargs}``.
    """

    def __init__(self, *outcomes: object) -> None:
        self.outcomes: list[object] = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(parse=self._parse, create=self._create)
        )

    def _next(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append({"method": method, **kwargs})
        if not self.outcomes:
            raise AssertionError("FakeSDK received more requests than outcomes were scripted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(kwargs) if callable(outcome) else outcome

    def _parse(self, **kwargs: Any) -> Any:
        return self._next("parse", kwargs)

    def _create(self, **kwargs: Any) -> Any:
        return self._next("create", kwargs)


class Sleeper:
    """Injected in place of ``time.sleep``: records the requested delays and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_client(
    sdk: object,
    *,
    key: str | None = FICTIONAL_KEY,
    model: str = "test-model",
    max_retries: int = 3,
    jitter: float = 1.0,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    timeout_s: float = 30.0,
) -> tuple[OpenAIClient, Sleeper]:
    """An ``OpenAIClient`` on a fake SDK with instant sleeps and a fixed jitter (1.0 = the full ceiling)."""
    sleeper = Sleeper()
    client = OpenAIClient(
        key,
        model,
        timeout_s,
        max_retries,
        client=sdk,
        sleep=sleeper,
        jitter=lambda: jitter,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
    )
    return client, sleeper


class Kit:
    """Namespace handed to tests by the ``kit`` fixture."""

    KEY = FICTIONAL_KEY
    Plan = Plan
    LooseDict = LooseDict
    FakeSDK = FakeSDK
    Sleeper = Sleeper
    http = http

    status_error = staticmethod(status_error)
    timeout_error = staticmethod(timeout_error)
    connection_error = staticmethod(connection_error)
    completion = staticmethod(completion)
    make_client: Callable[..., tuple[OpenAIClient, Sleeper]] = staticmethod(make_client)


@pytest.fixture
def kit() -> type[Kit]:
    return Kit
