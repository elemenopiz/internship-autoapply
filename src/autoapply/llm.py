"""LLM access: the OpenAI client, a scripted fake, a per-application call budget (docs/SPEC.md section 5.2).

Rules (SPEC section 1.7 and 1.9):

* The LLM is an enhancer, never a dependency of correctness. Every failure of every client in this module is an
  ``LLMError`` (``LLMCallError`` carries a machine-readable ``kind``), so call sites can always fall back.
* The API key never appears in an exception message, ``repr()`` or log line. Messages are built from HTTP status
  codes and API error codes, never from provider text, and are additionally passed through ``redact``.
* Logs carry only the purpose, model, latency, attempt count and token counts. Prompt and response bodies (which
  contain profile data) are never logged at any level, and ``LLMRequest.__repr__`` shows sizes, not text.

``OpenAIClient`` uses the SDK's structured-output helper (``chat.completions.parse`` with a pydantic model). When
the model or schema cannot use strict structured outputs (for example a schema with free-form ``dict`` fields) it
falls back, per schema and remembered for the client, to JSON mode plus ``model_validate_json``. The SDK's own retry
loop is disabled so backoff, jitter and attempt counting are ours and testable.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from autoapply.config import AppConfig
from autoapply.contracts import LLMClient, LLMError, T
from autoapply.secrets import key_is_well_formed, normalize_key, redact

__all__ = [
    "FAKE_LLM_ENV",
    "TESTING_ENV",
    "BudgetedLLM",
    "FakeLLM",
    "LLMCallError",
    "LLMRequest",
    "LLMUsage",
    "OpenAIClient",
    "build_llm",
]

log = logging.getLogger("autoapply.llm")

TESTING_ENV = "AUTOAPPLY_TESTING"
FAKE_LLM_ENV = "AUTOAPPLY_FAKE_LLM"
_FAKE_BRAIN_MODULE = "autoapply.testing.fake_llm"
_FAKE_BRAIN_FACTORY = "build_fake_brain"

_QUOTA_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"})
_TEMPERATURE_REJECTION_HINTS = (
    "unsupported",
    "not supported",
    "does not support",
    "only the default",
)
_SCHEMA_REJECTION_HINTS = ("response_format", "json_schema", "structured output", "invalid schema")
_MAX_RETRY_AFTER_S = 3600.0
_MAX_LISTED_VALIDATION_ERRORS = 5


class LLMCallError(LLMError):
    """An ``LLMError`` with machine-readable diagnostics. The message never contains the API key or prompt text.

    ``kind`` is one of: ``no_key``, ``bad_key``, ``bad_config``, ``sdk_missing``, ``auth``, ``permission``,
    ``not_found``, ``quota``, ``rate_limit``, ``server``, ``timeout``, ``connection``, ``bad_request``,
    ``too_long``, ``refusal``, ``empty``, ``truncated``, ``invalid_json``, ``schema``, ``bad_response``,
    ``budget``, ``scripted``, ``no_handler``, ``fake_llm_missing``, ``unexpected``. ``status`` is the HTTP status
    when there was one; ``retryable`` says whether the failure class is transient.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, repr=False)
class LLMRequest:
    """One recorded call to a ``FakeLLM``. ``schema`` is ``None`` for ``complete_text`` calls.

    ``repr()`` shows sizes rather than text so prompts (which hold profile data) cannot leak through logging.
    """

    purpose: str
    system: str
    user: str
    schema: type[BaseModel] | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    @property
    def is_text(self) -> bool:
        return self.schema is None

    def __repr__(self) -> str:
        schema = self.schema.__name__ if self.schema is not None else None
        return (
            f"LLMRequest(purpose={self.purpose!r}, schema={schema!r}, "
            f"system_chars={len(self.system)}, user_chars={len(self.user)})"
        )


@dataclass
class LLMUsage:
    """Running totals of an ``OpenAIClient`` (successful calls, failures, retries, tokens)."""

    calls: int = 0
    failures: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class _Outcome:
    value: Any
    attempts: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _SchemaRejected(Exception):
    """Internal: strict structured outputs cannot be used for this schema/model; switch to JSON mode."""


# ------------------------------------------------------------------------------------------ helpers


def _describe_validation(exc: ValidationError) -> str:
    """Structural summary of a pydantic error: locations and error types only, never the offending values."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    listed = ", ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'} ({item['type']})"
        for item in errors[:_MAX_LISTED_VALIDATION_ERRORS]
    )
    more = len(errors) - _MAX_LISTED_VALIDATION_ERRORS
    tail = f" and {more} more" if more > 0 else ""
    return f"{len(errors)} validation error(s): {listed}{tail}"


def _strip_code_fence(text: str) -> str:
    """Remove one surrounding markdown code fence (```json ... ```), if present.

    Plain string handling on purpose: a backtracking regex over model output can go quadratic on degenerate
    text (say, a fence followed by tens of thousands of blanks), and a run must never hang on the LLM.
    """
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return stripped
    inner = stripped[3:-3].lstrip()
    if inner[:4].lower() == "json" and not inner[4:5].isalnum():
        inner = inner[4:]
    return inner.strip()


def _messages(system: str, user: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages


def _json_mode_system(system: str, schema: type[BaseModel]) -> str:
    """System prompt for JSON mode: the caller's prompt plus the schema the answer must satisfy."""
    schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"), sort_keys=True)
    instruction = (
        "Respond with a single JSON object and nothing else: no markdown fences, no commentary. "
        f"It must validate against this JSON Schema:\n{schema_text}"
    )
    return f"{system}\n\n{instruction}" if system.strip() else instruction


def _token_counts(completion: Any) -> tuple[int | None, int | None]:
    usage = getattr(completion, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    generated = getattr(usage, "completion_tokens", None)
    return (
        prompt if isinstance(prompt, int) else None,
        generated if isinstance(generated, int) else None,
    )


def _find_parse(client: Any) -> Callable[..., Any] | None:
    """The SDK's structured-output entry point: ``chat.completions.parse`` (older SDKs: ``beta.chat...``)."""
    chat = getattr(client, "chat", None)
    parse = getattr(getattr(chat, "completions", None), "parse", None)
    if callable(parse):
        return parse
    beta_chat = getattr(getattr(client, "beta", None), "chat", None)
    beta_parse = getattr(getattr(beta_chat, "completions", None), "parse", None)
    return beta_parse if callable(beta_parse) else None


def _api_fields(exc: Exception) -> tuple[int | None, str, str, str]:
    """``(status, code, param, lower-cased message)`` of an API error, tolerating any exception type."""
    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    param = getattr(exc, "param", None)
    message = getattr(exc, "message", None) or str(exc)
    return (
        status if isinstance(status, int) else None,
        str(code or ""),
        str(param or ""),
        str(message).lower(),
    )


def _rejects_temperature(exc: Exception) -> bool:
    """A 400 saying this model does not accept ``temperature`` (or not that value)."""
    status, _code, param, message = _api_fields(exc)
    if status not in (400, 422):
        return False
    if param == "temperature":
        return True
    return "temperature" in message and any(h in message for h in _TEMPERATURE_REJECTION_HINTS)


def _rejects_schema(exc: Exception) -> bool:
    """A 400 saying the schema/model cannot be used with strict ``json_schema`` response formats."""
    status, code, param, message = _api_fields(exc)
    if status != 400:
        return False
    if code == "invalid_json_schema" or param.startswith("response_format"):
        return True
    return any(hint in message for hint in _SCHEMA_REJECTION_HINTS)


def _response_headers(exc: Exception) -> Any:
    return getattr(getattr(exc, "response", None), "headers", None)


def _retry_after_seconds(exc: Exception) -> float | None:
    headers = _response_headers(exc)
    if headers is None:
        return None
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        try:
            raw = headers.get(name)
            seconds = float(raw) / scale if raw is not None else None
        except (TypeError, ValueError, AttributeError):
            continue
        if seconds is not None and 0 <= seconds <= _MAX_RETRY_AFTER_S:
            return seconds
    return None


def _should_retry_header(exc: Exception) -> bool | None:
    """The provider's explicit ``x-should-retry`` hint, when present."""
    headers = _response_headers(exc)
    try:
        raw = headers.get("x-should-retry") if headers is not None else None
    except AttributeError:
        return None
    text = str(raw).strip().lower() if raw is not None else ""
    return {"true": True, "false": False}.get(text)


def _try_import_openai() -> Any | None:
    try:
        return importlib.import_module("openai")
    except ImportError:
        return None


# ------------------------------------------------------------------------------------------ OpenAI


class OpenAIClient(LLMClient):
    """``LLMClient`` over the ``openai`` SDK.

    ``api_key`` may be ``None`` or malformed: construction never fails, the first call raises ``LLMError`` with
    an actionable message. ``client`` injects an SDK-compatible object (tests); otherwise the real SDK client is
    built lazily on first use with its own retries disabled.

    Failures are retried up to ``max_retries`` times (so ``max_retries + 1`` attempts) with exponential backoff and
    jitter for rate limits (429, except exhausted quota), HTTP 408/409/5xx, timeouts and connection errors. Delays
    are ``base_delay_s * 2**n`` capped at ``max_delay_s``, jittered into the upper half of that range, and never
    shorter than a ``Retry-After`` hint (itself capped). ``sleep`` and ``jitter`` are injectable so tests are
    instant and deterministic. Authentication, quota, bad-request, refusal and invalid-output failures are not
    retried. A model that rejects ``temperature`` gets one immediate retry without it (not counted against
    ``max_retries``) and the client remembers to omit it from then on.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        client: Any | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self._api_key: str | None = normalize_key(api_key) or None
        self._model = model.strip()
        self._timeout_s = float(timeout_s)
        self._max_retries = max_retries
        self._client: Any | None = client
        self._owns_client = client is None
        self._sleep = sleep
        self._jitter = jitter
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s
        self._temperature_rejected = False
        self._json_mode_schemas: set[type[BaseModel]] = set()
        self._usage = LLMUsage()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        key = "<set>" if self._api_key else "<missing>"
        return f"OpenAIClient(model={self._model!r}, api_key={key})"

    @property
    def model(self) -> str:
        return self._model

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def omits_temperature(self) -> bool:
        """True once the model has rejected ``temperature``; it is no longer sent by this client."""
        return self._temperature_rejected

    @property
    def usage(self) -> LLMUsage:
        """Snapshot of the running totals."""
        with self._lock:
            return LLMUsage(**vars(self._usage))

    def close(self) -> None:
        """Release the SDK client's connection pool (only when this object created the client)."""
        with self._lock:
            client = self._client if self._owns_client else None
            if self._owns_client:
                self._client = None
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()

    # -- public API -----------------------------------------------------------------------------

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> T:
        """Return ``schema`` parsed from the model's answer; every failure is an ``LLMError``."""
        outcome = self._run(
            purpose,
            lambda: self._complete_json(purpose, system, user, schema, temperature, max_tokens),
        )
        return outcome.value

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        """Return the model's plain-text answer (stripped); every failure is an ``LLMError``."""
        messages = _messages(system, user)

        def call(client: Any, temp: float | None) -> Any:
            return client.chat.completions.create(**self._kwargs(messages, temp, max_tokens))

        def work() -> _Outcome:
            completion, attempts = self._transact(purpose, temperature, call, structured=False)
            text = self._answer_text(self._first_choice(completion))
            prompt_tokens, completion_tokens = _token_counts(completion)
            return _Outcome(text.strip(), attempts, prompt_tokens, completion_tokens)

        return str(self._run(purpose, work).value)

    def _run(self, purpose: str, work: Callable[[], _Outcome]) -> _Outcome:
        """Common envelope: readiness check, bookkeeping, and the guarantee that only ``LLMError`` escapes."""
        started = time.monotonic()
        try:
            self._require_ready()
            outcome = work()
        except LLMCallError as known:
            self._record_failure(purpose, known)
            raise
        except Exception as exc:  # a bug here must still not crash a run (SPEC 1.9)
            unexpected = self._error(
                f"Unexpected error in the OpenAI client ({type(exc).__name__}).", kind="unexpected"
            )
            self._record_failure(purpose, unexpected)
            raise unexpected from None
        self._record_success(purpose, started, outcome)
        return outcome

    # -- structured / JSON-mode flows ------------------------------------------------------------

    def _complete_json(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None,
        max_tokens: int | None,
    ) -> _Outcome:
        if schema not in self._json_mode_schemas:
            try:
                return self._structured_json(purpose, system, user, schema, temperature, max_tokens)
            except _SchemaRejected:
                with self._lock:
                    self._json_mode_schemas.add(schema)
                log.info(
                    "llm purpose=%s model=%s: strict structured outputs unavailable for %s; using JSON mode",
                    purpose,
                    self._model,
                    schema.__name__,
                )
        return self._json_mode(purpose, system, user, schema, temperature, max_tokens)

    def _structured_json(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None,
        max_tokens: int | None,
    ) -> _Outcome:
        messages = _messages(system, user)

        def call(client: Any, temp: float | None) -> Any:
            parse = _find_parse(client)
            if parse is None:
                raise _SchemaRejected
            return parse(**self._kwargs(messages, temp, max_tokens), response_format=schema)

        completion, attempts = self._transact(purpose, temperature, call, structured=True)
        choice = self._first_choice(completion)
        self._raise_for_choice(choice)
        message = getattr(choice, "message", None)
        parsed = getattr(message, "parsed", None)
        if parsed is None:
            value = self._validate_json_text(self._answer_text(choice), schema)
        elif isinstance(parsed, schema):
            value = parsed
        else:
            try:
                value = schema.model_validate(parsed)
            except ValidationError as exc:
                raise self._validation_failure(exc) from None
        prompt_tokens, completion_tokens = _token_counts(completion)
        return _Outcome(value, attempts, prompt_tokens, completion_tokens)

    def _json_mode(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None,
        max_tokens: int | None,
    ) -> _Outcome:
        try:
            system_with_schema = _json_mode_system(system, schema)
        except Exception:
            raise self._error(
                f"Cannot derive a JSON schema from {schema.__name__}.", kind="schema"
            ) from None
        messages = _messages(system_with_schema, user)

        def call(client: Any, temp: float | None) -> Any:
            return client.chat.completions.create(
                **self._kwargs(messages, temp, max_tokens),
                response_format={"type": "json_object"},
            )

        completion, attempts = self._transact(purpose, temperature, call, structured=False)
        text = self._answer_text(self._first_choice(completion))
        value = self._validate_json_text(text, schema)
        prompt_tokens, completion_tokens = _token_counts(completion)
        return _Outcome(value, attempts, prompt_tokens, completion_tokens)

    # -- transport: retries, temperature fallback ------------------------------------------------

    def _kwargs(
        self, messages: list[dict[str, str]], temperature: float | None, max_tokens: int | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "timeout": self._timeout_s,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            # ``max_completion_tokens`` is accepted by every current chat model; ``max_tokens`` is not.
            kwargs["max_completion_tokens"] = max_tokens
        return kwargs

    def _transact(
        self,
        purpose: str,
        temperature: float | None,
        call: Callable[[Any, float | None], Any],
        *,
        structured: bool,
    ) -> tuple[Any, int]:
        """Run ``call`` with retry/backoff; return ``(completion, attempts)`` or raise ``LLMCallError``."""
        client = self._get_client()
        send_temperature = None if self._temperature_rejected else temperature
        attempts = 0
        retries = 0
        while True:
            attempts += 1
            try:
                return call(client, send_temperature), attempts
            except (LLMError, _SchemaRejected):
                raise
            # The SDK, its HTTP layer and pydantic raise many types; every one becomes an LLMError below.
            except Exception as exc:
                if send_temperature is not None and _rejects_temperature(exc):
                    self._temperature_rejected = True
                    send_temperature = None
                    log.info(
                        "llm purpose=%s model=%s rejects temperature; retrying without it",
                        purpose,
                        self._model,
                    )
                    continue
                if structured and _rejects_schema(exc):
                    raise _SchemaRejected from None
                error = self._classify(exc, attempts)
                if not error.retryable or retries >= self._max_retries:
                    raise error from None
                delay = self._backoff_delay(retries, exc)
                retries += 1
                with self._lock:
                    self._usage.retries += 1
                log.warning(
                    "llm retry purpose=%s attempt=%d/%d kind=%s status=%s delay_s=%.2f",
                    purpose,
                    retries,
                    self._max_retries,
                    error.kind,
                    error.status,
                    delay,
                )
                self._sleep(delay)

    def _backoff_delay(self, retry_index: int, exc: Exception) -> float:
        ceiling = min(self._max_delay_s, self._base_delay_s * (2**retry_index))
        spread = min(1.0, max(0.0, self._jitter()))
        delay = ceiling * (0.5 + 0.5 * spread)
        hint = _retry_after_seconds(exc)
        if hint is not None:
            delay = max(delay, min(hint, self._max_delay_s))
        return delay

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._build_sdk_client()
            return self._client

    def _build_sdk_client(self) -> Any:
        openai = _try_import_openai()
        if openai is None:
            raise self._error(
                "The 'openai' package is not installed; run pip install -e . again.",
                kind="sdk_missing",
            )
        try:
            # max_retries=0: retries are ours (see class docstring). The key is passed explicitly so the SDK
            # never falls back to reading OPENAI_API_KEY itself.
            return openai.OpenAI(api_key=self._api_key, timeout=self._timeout_s, max_retries=0)
        except Exception as exc:
            raise self._error(
                f"Could not initialise the OpenAI client ({type(exc).__name__}).", kind="unexpected"
            ) from None

    # -- validation / error construction ---------------------------------------------------------

    def _require_ready(self) -> None:
        if not self._api_key:
            raise self._error(
                "No OpenAI API key is configured. Set the OPENAI_API_KEY environment variable "
                "(run set_openai_key.ps1) and restart.",
                kind="no_key",
            )
        if not key_is_well_formed(self._api_key):
            raise self._error(
                "The configured OpenAI API key is malformed (whitespace or non-ASCII characters). "
                "Set OPENAI_API_KEY again.",
                kind="bad_key",
            )
        if not self._model:
            raise self._error(
                "No OpenAI model is configured (llm.model in config.json).", kind="bad_config"
            )

    def _error(
        self, message: str, *, kind: str, status: int | None = None, retryable: bool = False
    ) -> LLMCallError:
        return LLMCallError(
            redact(message, self._api_key), kind=kind, status=status, retryable=retryable
        )

    def _validation_failure(self, exc: ValidationError) -> LLMCallError:
        details = _describe_validation(exc)
        if any(e["type"] == "json_invalid" for e in exc.errors(include_input=False)):
            return self._error(
                f"The model output for {exc.title} was not valid JSON: {details}.",
                kind="invalid_json",
            )
        return self._error(
            f"The model output did not match schema {exc.title}: {details}.", kind="schema"
        )

    def _validate_json_text(self, text: str, schema: type[T]) -> T:
        try:
            return schema.model_validate_json(_strip_code_fence(text))
        except ValidationError as exc:
            raise self._validation_failure(exc) from None

    def _first_choice(self, completion: Any) -> Any:
        choices = getattr(completion, "choices", None)
        if not choices:
            raise self._error("The model returned no choices.", kind="empty")
        return choices[0]

    def _raise_for_choice(self, choice: Any) -> None:
        message = getattr(choice, "message", None)
        finish = getattr(choice, "finish_reason", None)
        if getattr(message, "refusal", None):
            raise self._error("The model refused to answer.", kind="refusal")
        if finish == "content_filter":
            raise self._error(
                "The response was blocked by the provider's content filter.", kind="refusal"
            )
        if finish == "length":
            raise self._error(
                "The response was cut off at the token limit; raise max_tokens or shorten the prompt.",
                kind="truncated",
            )

    def _answer_text(self, choice: Any) -> str:
        self._raise_for_choice(choice)
        content = getattr(getattr(choice, "message", None), "content", None)
        if not isinstance(content, str) or not content.strip():
            raise self._error("The model returned an empty response.", kind="empty")
        return content

    def _classify(self, exc: Exception, attempts: int) -> LLMCallError:
        """Map any exception raised while calling the SDK to an ``LLMCallError`` (never echoing provider text)."""
        oa = _try_import_openai()

        def is_a(*names: str) -> bool:
            return oa is not None and any(
                isinstance(exc, getattr(oa, name)) for name in names if hasattr(oa, name)
            )

        if isinstance(exc, ValidationError):
            return self._validation_failure(exc)
        if is_a("APITimeoutError") or isinstance(exc, TimeoutError):
            return self._error(
                f"OpenAI request timed out ({attempts} attempt(s), {self._timeout_s:g}s each).",
                kind="timeout",
                retryable=True,
            )
        if is_a("APIConnectionError") or isinstance(exc, ConnectionError):
            return self._error(
                f"Could not reach OpenAI ({attempts} attempt(s)); check the network connection.",
                kind="connection",
                retryable=True,
            )
        if is_a("LengthFinishReasonError"):
            return self._error(
                "The response was cut off at the token limit; raise max_tokens or shorten the prompt.",
                kind="truncated",
            )
        if is_a("ContentFilterFinishReasonError"):
            return self._error(
                "The response was blocked by the provider's content filter.", kind="refusal"
            )
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and is_a("APIStatusError"):
            return self._status_error(exc, status, attempts)
        if is_a("OpenAIError"):
            return self._error("OpenAI returned an unusable response.", kind="bad_response")
        return self._error(
            f"Unexpected error while calling OpenAI ({type(exc).__name__}).", kind="unexpected"
        )

    def _status_error(self, exc: Exception, status: int, attempts: int) -> LLMCallError:
        _, code, param, _message = _api_fields(exc)
        error_type = str(getattr(exc, "type", "") or "")
        if status == 401:
            return self._error(
                "OpenAI rejected the API key (HTTP 401). Check OPENAI_API_KEY.",
                kind="auth",
                status=status,
            )
        if status == 403:
            return self._error(
                f"OpenAI denied access (HTTP 403): the key's project may lack access to model "
                f"{self._model!r}, or the region is unsupported.",
                kind="permission",
                status=status,
            )
        if status == 404:
            return self._error(
                f"OpenAI answered HTTP 404: model {self._model!r} or the endpoint was not found. "
                "Check llm.model in config.json.",
                kind="not_found",
                status=status,
            )
        hint = _should_retry_header(exc)
        transient = hint if hint is not None else True
        if status == 429:
            if code in _QUOTA_CODES or error_type in _QUOTA_CODES:
                return self._error(
                    "OpenAI quota exhausted or billing inactive (HTTP 429 insufficient_quota). "
                    "Add credit or raise the project spend limit.",
                    kind="quota",
                    status=status,
                )
            return self._error(
                f"OpenAI rate limit (HTTP 429) persisted after {attempts} attempt(s).",
                kind="rate_limit",
                status=status,
                retryable=transient,
            )
        if status in (408, 409) or status >= 500:
            return self._error(
                f"OpenAI server error (HTTP {status}) after {attempts} attempt(s).",
                kind="server",
                status=status,
                retryable=transient,
            )
        if code == "context_length_exceeded":
            return self._error(
                f"The prompt is too long for model {self._model!r} (HTTP {status}).",
                kind="too_long",
                status=status,
            )
        details = ", ".join(
            p for p in (f"code={code}" if code else "", f"param={param}" if param else "") if p
        )
        suffix = f", {details}" if details else ""
        return self._error(
            f"OpenAI rejected the request (HTTP {status}{suffix}).",
            kind="bad_request",
            status=status,
        )

    # -- bookkeeping -----------------------------------------------------------------------------

    def _record_success(self, purpose: str, started: float, outcome: _Outcome) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        with self._lock:
            self._usage.calls += 1
            self._usage.prompt_tokens += outcome.prompt_tokens or 0
            self._usage.completion_tokens += outcome.completion_tokens or 0
        log.info(
            "llm ok purpose=%s model=%s latency_ms=%d attempts=%d prompt_tokens=%s completion_tokens=%s",
            purpose,
            self._model,
            latency_ms,
            outcome.attempts,
            outcome.prompt_tokens,
            outcome.completion_tokens,
        )

    def _record_failure(self, purpose: str, error: LLMCallError) -> None:
        with self._lock:
            self._usage.failures += 1
        log.warning(
            "llm failed purpose=%s model=%s kind=%s status=%s",
            purpose,
            self._model,
            error.kind,
            error.status,
        )


# ------------------------------------------------------------------------------------------ budget


class BudgetedLLM(LLMClient):
    """Wrap an ``LLMClient`` with a call budget (``config.llm.max_calls_per_application`` per application).

    Every ``complete_json`` / ``complete_text`` call spends one unit *before* delegating, whether or not it
    later fails, because a failed call still costs time and money. Once the budget is spent every call raises
    ``LLMCallError(kind="budget")`` without touching the wrapped client. ``max_calls=0`` therefore disables the
    LLM entirely (call sites take their deterministic path). Thread-safe.
    """

    def __init__(self, llm: LLMClient, max_calls: int) -> None:
        if max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        self._llm = llm
        self._max_calls = max_calls
        self._used = 0
        self._lock = threading.Lock()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self._used)

    def _spend(self, purpose: str) -> None:
        with self._lock:
            if self._used >= self._max_calls:
                raise LLMCallError(
                    f"LLM call budget exhausted ({self._max_calls} per application); "
                    f"refused call for purpose {purpose!r}.",
                    kind="budget",
                )
            self._used += 1

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> T:
        self._spend(purpose)
        return self._llm.complete_json(
            purpose=purpose,
            system=system,
            user=user,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        self._spend(purpose)
        return self._llm.complete_text(
            purpose=purpose,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def __repr__(self) -> str:
        return f"BudgetedLLM(used={self._used}/{self._max_calls})"


# ------------------------------------------------------------------------------------------ fake


class FakeLLM(LLMClient):
    """Scripted ``LLMClient`` for tests and the hermetic end-to-end run. Never talks to a network.

    ``register(purpose, handler_or_value)`` scripts the answer for a purpose. A callable is a handler and receives
    the recorded ``LLMRequest``; anything else is returned as is. For ``complete_json`` the result may be an
    instance of the schema, another pydantic model, a ``dict`` or a JSON ``str``/``bytes``; it is validated against
    ``schema`` and a mismatch raises ``LLMError`` (a bad script fails loudly instead of leaking invalid data). For
    ``complete_text`` the result must be a ``str``. An exception instance (or a handler that raises) is raised, which
    is how failures are scripted; handlers raising anything other than ``LLMError`` are bugs and propagate as is.

    Every call is appended to ``calls`` first, even when it then fails. A purpose with no handler raises
    ``LLMError``; ``fail_all()`` makes every call fail until ``fail_all(False)``.
    """

    def __init__(self, handlers: Mapping[str, object] | None = None) -> None:
        self.calls: list[LLMRequest] = []
        self._handlers: dict[str, object] = {}
        self._fail_all = False
        self._fail_message = "FakeLLM: scripted failure (fail_all)"
        for purpose, handler in (handlers or {}).items():
            self.register(purpose, handler)

    def register(self, purpose: str, handler_or_value: object) -> None:
        """Script the answer for ``purpose`` (replacing any earlier script)."""
        self._handlers[purpose] = handler_or_value

    def register_sequence(
        self, purpose: str, responses: Sequence[object], *, repeat_last: bool = False
    ) -> None:
        """Script successive answers for ``purpose``: the n-th call gets the n-th item (value or handler).

        When the sequence runs out the call raises ``LLMError``, unless ``repeat_last`` keeps returning the last.
        """
        queue = list(responses)
        lock = threading.Lock()

        def handler(request: LLMRequest) -> object:
            with lock:
                if not queue:
                    raise LLMCallError(
                        f"FakeLLM: scripted responses for purpose {purpose!r} are exhausted",
                        kind="scripted",
                    )
                item = queue[0] if repeat_last and len(queue) == 1 else queue.pop(0)
            return item(request) if callable(item) else item

        self._handlers[purpose] = handler

    def unregister(self, purpose: str) -> None:
        self._handlers.pop(purpose, None)

    def fail_all(self, enabled: bool = True, message: str | None = None) -> None:
        """Make every call raise ``LLMError`` (``enabled=False`` restores the scripted behaviour)."""
        self._fail_all = enabled
        if message is not None:
            self._fail_message = message

    def calls_for(self, purpose: str) -> list[LLMRequest]:
        return [call for call in self.calls if call.purpose == purpose]

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> T:
        request = LLMRequest(purpose, system, user, schema, temperature, max_tokens)
        raw = self._produce(request)
        try:
            if isinstance(raw, schema):
                return raw
            if isinstance(raw, BaseModel):
                return schema.model_validate(raw.model_dump())
            if isinstance(raw, str | bytes | bytearray):
                return schema.model_validate_json(raw)
            if isinstance(raw, Mapping):
                return schema.model_validate(dict(raw))
        except ValidationError as exc:
            raise LLMCallError(
                f"FakeLLM: scripted response for purpose {purpose!r} does not match "
                f"{schema.__name__}: {_describe_validation(exc)}",
                kind="schema",
            ) from None
        raise LLMCallError(
            f"FakeLLM: scripted response for purpose {purpose!r} has unsupported type "
            f"{type(raw).__name__}",
            kind="scripted",
        )

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        request = LLMRequest(purpose, system, user, None, temperature, max_tokens)
        raw = self._produce(request)
        if not isinstance(raw, str):
            raise LLMCallError(
                f"FakeLLM: scripted text response for purpose {purpose!r} must be a str, "
                f"not {type(raw).__name__}",
                kind="scripted",
            )
        return raw

    def _produce(self, request: LLMRequest) -> object:
        self.calls.append(request)
        if self._fail_all:
            raise LLMCallError(self._fail_message, kind="scripted")
        try:
            handler = self._handlers[request.purpose]
        except KeyError:
            raise LLMCallError(
                f"FakeLLM: no handler registered for purpose {request.purpose!r}", kind="no_handler"
            ) from None
        result = handler(request) if callable(handler) else handler
        if isinstance(result, BaseException):
            raise result
        return result

    def __repr__(self) -> str:
        purposes = sorted(self._handlers)
        return f"FakeLLM(purposes={purposes}, calls={len(self.calls)}, fail_all={self._fail_all})"


# ------------------------------------------------------------------------------------------ factory


def build_llm(
    config: AppConfig, key: str | None, env: Mapping[str, str] | None = None
) -> LLMClient:
    """Build the ``LLMClient`` for a run.

    Normally an ``OpenAIClient`` configured from ``config.llm``. A missing key is not an error here: the client
    raises ``LLMError`` on its first call, so discovery-only runs and deterministic fallbacks keep working.

    Only when BOTH ``AUTOAPPLY_TESTING=1`` and ``AUTOAPPLY_FAKE_LLM=1`` are set in ``env`` (default
    ``os.environ``) does it return the scripted "fake brain" from ``autoapply.testing.fake_llm.build_fake_brain``
    (called with ``config`` when it accepts an argument, else with none). One flag alone is ignored, so a stray
    variable can never silently replace the real model. A missing fake-brain module is an ``LLMError``.
    """
    source = os.environ if env is None else env
    if source.get(TESTING_ENV) == "1" and source.get(FAKE_LLM_ENV) == "1":
        return _load_fake_brain(config)
    return OpenAIClient(
        api_key=key,
        model=config.llm.model,
        timeout_s=config.llm.timeout_s,
        max_retries=config.llm.max_retries,
    )


def _load_fake_brain(config: AppConfig) -> LLMClient:
    try:
        module = importlib.import_module(_FAKE_BRAIN_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _FAKE_BRAIN_MODULE:
            raise LLMCallError(
                f"{FAKE_LLM_ENV}=1 requested the fake LLM but {_FAKE_BRAIN_MODULE} failed to import "
                f"(missing module {exc.name!r}).",
                kind="fake_llm_missing",
            ) from None
        raise LLMCallError(
            f"{FAKE_LLM_ENV}=1 requested the fake LLM but {_FAKE_BRAIN_MODULE} could not be found.",
            kind="fake_llm_missing",
        ) from None
    factory = getattr(module, _FAKE_BRAIN_FACTORY, None)
    if not callable(factory):
        raise LLMCallError(
            f"{_FAKE_BRAIN_MODULE} does not define {_FAKE_BRAIN_FACTORY}().",
            kind="fake_llm_missing",
        )
    try:
        takes_config = any(
            p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
            for p in inspect.signature(factory).parameters.values()
        )
    except (TypeError, ValueError):
        takes_config = True
    brain = factory(config) if takes_config else factory()
    if not (hasattr(brain, "complete_json") and hasattr(brain, "complete_text")):
        raise LLMCallError(
            f"{_FAKE_BRAIN_FACTORY}() did not return an LLMClient.", kind="fake_llm_missing"
        )
    return brain
