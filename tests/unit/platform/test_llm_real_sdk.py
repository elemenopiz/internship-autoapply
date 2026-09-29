"""OpenAIClient driven through the REAL ``openai`` SDK over an in-process mock transport (no network).

The fake-SDK tests pin our logic; these pin that the logic matches what the installed SDK really sends and raises:
request shape, strict structured-output schemas, header hints, and the SDK's own exception classes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import openai
import pytest

from autoapply.llm import LLMCallError, OpenAIClient


def chat_payload(
    content: str | None,
    *,
    refusal: str | None = None,
    finish_reason: str = "stop",
    model: str = "test-model",
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30},
    }


def error_payload(
    message: str,
    *,
    code: str | None = None,
    param: str | None = None,
    error_type: str = "invalid_request_error",
) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": param, "code": code}}


class Transport:
    """Mock HTTP transport: records requests, replays scripted responses (or raises scripted exceptions)."""

    def __init__(self, kit: Any, *outcomes: object) -> None:
        self.kit = kit
        self.outcomes = list(outcomes)
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("more HTTP requests than scripted outcomes")
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome(request)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    def respond(self, status: int, payload: Any = None, **kwargs: Any) -> Any:
        return self.kit.http.Response(status, json=payload, **kwargs)


def build(
    kit: Any, transport: Transport, *, max_retries: int = 3, **options: Any
) -> tuple[OpenAIClient, Any]:
    """Our client on the real SDK, whose HTTP layer is the mock transport; SDK retries off, as in production."""
    sdk = openai.OpenAI(
        api_key=kit.KEY,
        base_url="http://localhost:9/v1",
        http_client=kit.http.Client(transport=kit.http.MockTransport(transport)),
        max_retries=0,
    )
    client, sleeper = kit.make_client(sdk, max_retries=max_retries, model="test-model", **options)
    return client, sleeper


def raise_read_timeout(kit: Any) -> Callable[[Any], Exception]:
    return lambda request: kit.http.ReadTimeout("read timed out", request=request)


# ------------------------------------------------------------------------------------------- requests


def test_structured_request_uses_a_strict_json_schema_and_authenticates(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes.append(transport.respond(200, chat_payload('{"title": "APM", "score": 8}')))
    client, _ = build(kit, transport)

    result = client.complete_json(
        purpose="tailor_resume",
        system="Be terse.",
        user="Plan it.",
        schema=kit.Plan,
        temperature=0.25,
        max_tokens=333,
    )

    assert result == kit.Plan(title="APM", score=8)
    (request,) = transport.requests
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == f"Bearer {kit.KEY}"
    (body,) = transport.bodies
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Plan it."},
    ]
    assert body["temperature"] == 0.25
    assert body["max_completion_tokens"] == 333
    assert "max_tokens" not in body
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "Plan"
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "score"]
    assert client.usage.prompt_tokens == 21
    assert client.usage.completion_tokens == 9


def test_text_request_has_no_response_format(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes.append(transport.respond(200, chat_payload("  Hello there.  ")))
    client, _ = build(kit, transport)
    assert (
        client.complete_text(purpose="cover_letter", system="", user="Write it.") == "Hello there."
    )
    (body,) = transport.bodies
    assert "response_format" not in body
    assert body["messages"] == [{"role": "user", "content": "Write it."}]
    assert body["temperature"] == 0.4


def test_the_sdks_own_retries_are_off_so_attempts_are_counted_by_us(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes += [
        transport.respond(500, error_payload("server exploded")) for _ in range(3)
    ]
    client, sleeper = build(kit, transport, max_retries=2)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert len(transport.requests) == 3  # 1 + our 2 retries; a live SDK retry loop would add more
    assert len(sleeper.delays) == 2
    assert (info.value.kind, info.value.status) == ("server", 500)


# ------------------------------------------------------------------------------------------- errors


def test_a_real_429_retry_after_header_is_honoured(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes += [
        transport.respond(
            429,
            error_payload("Rate limit reached", code="rate_limit_exceeded", error_type="requests"),
            headers={"retry-after": "3"},
        ),
        transport.respond(200, chat_payload("ok")),
    ]
    client, sleeper = build(kit, transport, jitter=0.0)
    assert client.complete_text(purpose="p", system="", user="u") == "ok"
    assert sleeper.delays == [3.0]


def test_exhausted_quota_is_reported_at_once_from_a_real_response(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes += [
        transport.respond(
            429,
            error_payload(
                "You exceeded your current quota",
                code="insufficient_quota",
                error_type="insufficient_quota",
            ),
        )
    ]
    client, sleeper = build(kit, transport)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert info.value.kind == "quota"
    assert len(transport.requests) == 1
    assert sleeper.delays == []


def test_a_401_that_echoes_the_key_is_reported_with_our_message_only(kit: Any) -> None:
    provider_text = (
        f"Incorrect API key provided: {kit.KEY[:8]}****{kit.KEY[-4:]}. Find yours at platform."
    )
    transport = Transport(kit)
    transport.outcomes.append(
        transport.respond(401, error_payload(provider_text, code="invalid_api_key"))
    )
    client, _ = build(kit, transport)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert info.value.kind == "auth"
    assert str(info.value) == "OpenAI rejected the API key (HTTP 401). Check OPENAI_API_KEY."
    assert "Incorrect API key" not in str(info.value)
    assert info.value.__cause__ is None


def test_a_schema_the_real_api_rejects_falls_back_to_json_mode(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes += [
        transport.respond(
            400,
            error_payload(
                "Invalid schema for response_format 'LooseDict': 'additionalProperties' is required "
                "to be supplied and to be false.",
                code="invalid_json_schema",
                param="response_format",
            ),
        ),
        transport.respond(200, chat_payload('{"data": {"answer": "yes"}}')),
    ]
    client, _ = build(kit, transport)
    result = client.complete_json(
        purpose="map_form_fields", system="Map.", user="Form.", schema=kit.LooseDict
    )
    assert result == kit.LooseDict(data={"answer": "yes"})
    strict_body, json_body = transport.bodies
    # The SDK really cannot make this schema strict: the dict field keeps a non-false additionalProperties.
    strict_schema = strict_body["response_format"]["json_schema"]["schema"]
    assert strict_schema["properties"]["data"]["additionalProperties"] != False  # noqa: E712
    assert json_body["response_format"] == {"type": "json_object"}
    assert "JSON" in json_body["messages"][0]["content"]


def test_a_model_that_rejects_temperature_is_handled_over_real_http(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes += [
        transport.respond(
            400,
            error_payload(
                "Unsupported value: 'temperature' does not support 0.2 with this model. "
                "Only the default (1) value is supported.",
                code="unsupported_value",
                param="temperature",
            ),
        ),
        transport.respond(200, chat_payload('{"title": "T", "score": 2}')),
        transport.respond(200, chat_payload('{"title": "U", "score": 3}')),
    ]
    client, sleeper = build(kit, transport)
    first = client.complete_json(
        purpose="p", system="s", user="u", schema=kit.Plan, temperature=0.2
    )
    second = client.complete_json(
        purpose="p", system="s", user="u", schema=kit.Plan, temperature=0.2
    )
    assert (first.score, second.score) == (2, 3)
    assert ["temperature" in body for body in transport.bodies] == [True, False, False]
    assert sleeper.delays == []


def test_read_timeouts_become_retried_timeout_errors(kit: Any) -> None:
    transport = Transport(kit, *[raise_read_timeout(kit)] * 3)
    client, sleeper = build(kit, transport, max_retries=2, jitter=0.0)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert info.value.kind == "timeout"
    assert sleeper.delays == [0.5, 1.0]
    assert len(transport.requests) == 3


def test_connection_failures_are_retried_and_never_leak_the_key(kit: Any) -> None:
    def refuse(request: Any) -> Exception:
        return kit.http.ConnectError(
            f"cannot connect; sent Authorization: Bearer {kit.KEY}", request=request
        )

    transport = Transport(kit, refuse, refuse)
    client, sleeper = build(kit, transport, max_retries=1)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert info.value.kind == "connection"
    assert len(sleeper.delays) == 1
    assert kit.KEY not in str(info.value)


# ------------------------------------------------------------------------------------------- outputs


def test_a_real_refusal_is_reported_as_a_refusal(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes.append(
        transport.respond(200, chat_payload(None, refusal="I'm sorry, I can't help with that."))
    )
    client, _ = build(kit, transport)
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=kit.Plan)
    assert info.value.kind == "refusal"


def test_a_response_cut_off_at_the_token_limit_is_truncated(kit: Any) -> None:
    for call_kind in ("json", "text"):
        transport = Transport(kit)
        transport.outcomes.append(
            transport.respond(200, chat_payload('{"title": "cut of', finish_reason="length"))
        )
        client, _ = build(kit, transport)
        with pytest.raises(LLMCallError) as info:
            if call_kind == "json":
                client.complete_json(purpose="p", system="", user="u", schema=kit.Plan)
            else:
                client.complete_text(purpose="p", system="", user="u")
        assert info.value.kind == "truncated"


def test_a_content_filtered_response_is_reported_as_a_refusal(kit: Any) -> None:
    transport = Transport(kit)
    transport.outcomes.append(
        transport.respond(200, chat_payload("x", finish_reason="content_filter"))
    )
    client, _ = build(kit, transport)
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=kit.Plan)
    assert info.value.kind == "refusal"


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("this is not json", "invalid_json"),
        ('{"title": "ok"}', "schema"),
        ('{"title": 5, "score": "x"}', "schema"),
    ],
)
def test_output_that_does_not_fit_the_schema_is_reported_by_kind(
    kit: Any, content: str, kind: str
) -> None:
    transport = Transport(kit)
    transport.outcomes.append(transport.respond(200, chat_payload(content)))
    client, _ = build(kit, transport)
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=kit.Plan)
    assert info.value.kind == kind
    assert len(transport.requests) == 1  # invalid output is not retried


@pytest.mark.parametrize(
    "response",
    [
        lambda t: t.respond(200, {"unexpected": "shape"}),
        lambda t: t.kit.http.Response(
            200, content=b"<html>gateway hiccup</html>", headers={"content-type": "text/html"}
        ),
        lambda t: t.respond(200, {**chat_payload("x"), "choices": []}),
    ],
    ids=["wrong-shape", "html-body", "no-choices"],
)
def test_malformed_success_responses_are_llm_errors(
    kit: Any, response: Callable[[Transport], Any]
) -> None:
    for use_json in (False, True):
        transport = Transport(kit)
        transport.outcomes.append(response(transport))
        client, _ = build(kit, transport, max_retries=0)
        with pytest.raises(LLMCallError) as info:
            if use_json:
                client.complete_json(purpose="p", system="", user="u", schema=kit.Plan)
            else:
                client.complete_text(purpose="p", system="", user="u")
        assert info.value.kind in {"empty", "unexpected", "bad_response"}
        assert len(transport.requests) == 1  # not retried
