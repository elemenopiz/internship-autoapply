"""OpenAIClient against a fake ``openai`` client: requests, retries, backoff, fallbacks, error mapping, logs."""

from __future__ import annotations

import logging
import sys
from typing import Any

import openai
import pytest
from pydantic import BaseModel, ValidationError

from autoapply.contracts import LLMError
from autoapply.llm import LLMCallError, OpenAIClient

PROMPT_MARKER = "ZXQ-PRIVATE-PROFILE-DATA-ALEX-RIVERA"
RESPONSE_MARKER = "ZXQ-PRIVATE-MODEL-OUTPUT"


def json_call(client: OpenAIClient, kit: Any, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "purpose": "tailor_resume",
        "system": "You are terse.",
        "user": "Pick the best bullets.",
        "schema": kit.Plan,
    }
    args.update(overrides)
    return client.complete_json(**args)


def text_call(client: OpenAIClient, **overrides: Any) -> str:
    args: dict[str, Any] = {"purpose": "cover_letter", "system": "Be brief.", "user": "Write."}
    args.update(overrides)
    return client.complete_text(**args)


# ------------------------------------------------------------------------------------------- construction


def test_missing_key_is_not_an_error_until_the_first_call(kit: Any) -> None:
    sdk = kit.FakeSDK()
    client, _ = kit.make_client(sdk, key=None)  # construction must not raise
    with pytest.raises(LLMCallError) as json_info:
        json_call(client, kit)
    with pytest.raises(LLMCallError) as text_info:
        text_call(client)
    for info in (json_info, text_info):
        assert info.value.kind == "no_key"
        assert "OPENAI_API_KEY" in str(info.value)
    assert sdk.calls == []


@pytest.mark.parametrize("nothing", ["", "   ", '""'])
def test_a_blank_key_counts_as_missing(kit: Any, nothing: str) -> None:
    client, _ = kit.make_client(kit.FakeSDK(), key=nothing)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "no_key"


def test_a_malformed_key_is_rejected_before_any_request_and_never_echoed(kit: Any) -> None:
    bad = "sk-test-FICTIONAL key with spaces"
    sdk = kit.FakeSDK()
    client, _ = kit.make_client(sdk, key=bad)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "bad_key"
    assert bad not in str(info.value)
    assert sdk.calls == []


def test_a_blank_model_is_a_configuration_error(kit: Any) -> None:
    sdk = kit.FakeSDK()
    client, _ = kit.make_client(sdk, model="  ")
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "bad_config"
    assert sdk.calls == []


def test_constructor_rejects_nonsensical_limits(kit: Any) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        OpenAIClient(kit.KEY, "m", 30, -1)
    with pytest.raises(ValueError, match="timeout_s"):
        OpenAIClient(kit.KEY, "m", 0, 1)


def test_key_is_normalised_at_construction(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(content="hello"))
    client, _ = kit.make_client(sdk, key=f'  "{kit.KEY}"\r\n')
    assert text_call(client) == "hello"


def test_accessors_expose_configuration_but_never_the_key(kit: Any) -> None:
    client = OpenAIClient(kit.KEY, "model-x", 12, 4)
    assert (client.model, client.timeout_s, client.max_retries) == ("model-x", 12.0, 4)
    assert kit.KEY not in repr(client)
    assert kit.KEY not in str(client)
    assert "<set>" in repr(client)
    assert "<missing>" in repr(OpenAIClient(None, "model-x"))


def test_the_sdk_client_is_built_lazily_once_with_sdk_retries_disabled(
    monkeypatch: pytest.MonkeyPatch, kit: Any
) -> None:
    built: list[dict[str, Any]] = []
    sdk = kit.FakeSDK(kit.completion(content="one"), kit.completion(content="two"))

    def fake_openai(**kwargs: Any) -> Any:
        built.append(kwargs)
        return sdk

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    client = OpenAIClient(kit.KEY, "test-model", 45, 5)
    assert built == []
    assert text_call(client) == "one"
    assert text_call(client) == "two"
    # max_retries=0: backoff and attempt counting belong to our loop, not the SDK's.
    assert built == [{"api_key": kit.KEY, "timeout": 45.0, "max_retries": 0}]


def test_a_failure_to_build_the_sdk_client_is_an_llm_error(
    monkeypatch: pytest.MonkeyPatch, kit: Any
) -> None:
    def broken(**kwargs: Any) -> Any:
        raise RuntimeError(f"bad env with {kit.KEY}")

    monkeypatch.setattr(openai, "OpenAI", broken)
    with pytest.raises(LLMCallError) as info:
        text_call(OpenAIClient(kit.KEY, "test-model"))
    assert info.value.kind == "unexpected"
    assert kit.KEY not in str(info.value)


def test_a_missing_sdk_package_is_an_llm_error(monkeypatch: pytest.MonkeyPatch, kit: Any) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(LLMCallError) as info:
        text_call(OpenAIClient(kit.KEY, "test-model"))
    assert info.value.kind == "sdk_missing"


def test_close_releases_only_a_client_it_created(monkeypatch: pytest.MonkeyPatch, kit: Any) -> None:
    closed: list[str] = []

    class Closable(kit.FakeSDK):
        def close(self) -> None:
            closed.append("closed")

    created = Closable(kit.completion(content="hi"))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: created)
    owning = OpenAIClient(kit.KEY, "m")
    text_call(owning)
    owning.close()
    owning.close()  # idempotent
    assert closed == ["closed"]

    injected = Closable()
    borrowing = OpenAIClient(kit.KEY, "m", client=injected)
    borrowing.close()
    assert closed == ["closed"]  # not ours to close


# ------------------------------------------------------------------------------------------- happy paths


def test_complete_json_returns_the_parsed_model_and_sends_the_expected_request(kit: Any) -> None:
    plan = kit.Plan(title="PM Intern", score=9)
    sdk = kit.FakeSDK(kit.completion(parsed=plan, prompt_tokens=120, completion_tokens=30))
    client, sleeper = kit.make_client(sdk, model="test-model", timeout_s=42)

    result = json_call(client, kit, temperature=0.3, max_tokens=200)

    assert result == plan
    (call,) = sdk.calls
    assert call["method"] == "parse"
    assert call["response_format"] is kit.Plan
    assert call["model"] == "test-model"
    assert call["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Pick the best bullets."},
    ]
    assert call["temperature"] == 0.3
    assert call["max_completion_tokens"] == 200
    assert call["timeout"] == 42.0
    assert "max_tokens" not in call
    assert sleeper.delays == []
    usage = client.usage
    assert (usage.calls, usage.failures, usage.retries) == (1, 0, 0)
    assert (usage.prompt_tokens, usage.completion_tokens) == (120, 30)


def test_default_temperatures_follow_the_contract(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.completion(parsed=kit.Plan(title="a", score=1)), kit.completion(content="text")
    )
    client, _ = kit.make_client(sdk)
    json_call(client, kit)
    text_call(client)
    assert sdk.calls[0]["temperature"] == 0.2
    assert sdk.calls[1]["temperature"] == 0.4


def test_optional_parameters_are_omitted_rather_than_sent_as_null(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(parsed=kit.Plan(title="a", score=1)))
    client, _ = kit.make_client(sdk)
    json_call(client, kit, temperature=None, max_tokens=None)
    assert "temperature" not in sdk.calls[0]
    assert "max_completion_tokens" not in sdk.calls[0]


def test_a_blank_system_prompt_sends_only_the_user_message(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(content="ok"))
    client, _ = kit.make_client(sdk)
    text_call(client, system="   ", user="hello")
    assert sdk.calls[0]["messages"] == [{"role": "user", "content": "hello"}]


def test_complete_text_uses_plain_create_and_strips_the_answer(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(content="  Dear team,\nHello.\n\n"))
    client, _ = kit.make_client(sdk)
    assert text_call(client, temperature=0.9, max_tokens=64) == "Dear team,\nHello."
    (call,) = sdk.calls
    assert call["method"] == "create"
    assert "response_format" not in call
    assert call["temperature"] == 0.9
    assert call["max_completion_tokens"] == 64


def test_a_lenient_client_returning_a_dict_as_parsed_is_validated(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(parsed={"title": "PM", "score": 3}))
    client, _ = kit.make_client(sdk)
    assert json_call(client, kit) == kit.Plan(title="PM", score=3)


def test_an_invalid_dict_as_parsed_is_a_schema_error(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(parsed={"title": "PM"}))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        json_call(client, kit)
    assert info.value.kind == "schema"
    assert "score (missing)" in str(info.value)


@pytest.mark.parametrize(
    "content",
    [
        '{"title": "PM", "score": 4}',
        '```json\n{"title": "PM", "score": 4}\n```',
        '  {"title": "PM", "score": 4}\n',
    ],
)
def test_unparsed_json_content_is_validated_manually(kit: Any, content: str) -> None:
    sdk = kit.FakeSDK(kit.completion(parsed=None, content=content))
    client, _ = kit.make_client(sdk)
    assert json_call(client, kit) == kit.Plan(title="PM", score=4)


# ------------------------------------------------------------------------------------------- retries


def test_rate_limits_are_retried_with_exponential_backoff_then_succeed(kit: Any) -> None:
    plan = kit.Plan(title="ok", score=1)
    sdk = kit.FakeSDK(kit.status_error(429), kit.status_error(429), kit.completion(parsed=plan))
    client, sleeper = kit.make_client(sdk, jitter=1.0)
    assert json_call(client, kit) == plan
    assert len(sdk.calls) == 3
    assert sleeper.delays == [1.0, 2.0]
    assert (client.usage.calls, client.usage.retries) == (1, 2)


def test_jitter_moves_each_delay_within_the_upper_half_of_its_ceiling(kit: Any) -> None:
    jitters = iter([0.0, 0.5, 1.0])
    sdk = kit.FakeSDK(*[kit.status_error(503)] * 3, kit.completion(content="ok"))
    sleeper = kit.Sleeper()
    client = OpenAIClient(
        kit.KEY, "m", 30, 3, client=sdk, sleep=sleeper, jitter=lambda: next(jitters)
    )
    text_call(client)
    assert sleeper.delays == [0.5, 1.5, 4.0]


@pytest.mark.parametrize(("jitter", "expected"), [(5.0, [1.0]), (-3.0, [0.5])])
def test_out_of_range_jitter_is_clamped(kit: Any, jitter: float, expected: list[float]) -> None:
    sdk = kit.FakeSDK(kit.status_error(500), kit.completion(content="ok"))
    client, sleeper = kit.make_client(sdk, jitter=jitter)
    text_call(client)
    assert sleeper.delays == expected


def test_backoff_doubles_until_the_cap(kit: Any) -> None:
    sdk = kit.FakeSDK(*[kit.status_error(503)] * 7, kit.completion(content="ok"))
    client, sleeper = kit.make_client(sdk, max_retries=7, base_delay_s=1.0, max_delay_s=20.0)
    text_call(client)
    assert sleeper.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 20.0, 20.0]


def test_retries_are_bounded_by_max_retries(kit: Any) -> None:
    sdk = kit.FakeSDK(*[kit.status_error(429)] * 3, kit.completion(content="never reached"))
    client, sleeper = kit.make_client(sdk, max_retries=2)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert len(sdk.calls) == 3  # first try + two retries
    assert len(sleeper.delays) == 2
    assert (info.value.kind, info.value.status, info.value.retryable) == ("rate_limit", 429, True)
    assert "3 attempt" in str(info.value)
    assert (client.usage.calls, client.usage.failures) == (0, 1)


def test_max_retries_zero_means_a_single_attempt(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.status_error(500), kit.completion(content="never reached"))
    client, sleeper = kit.make_client(sdk, max_retries=0)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert len(sdk.calls) == 1
    assert sleeper.delays == []
    assert info.value.kind == "server"


@pytest.mark.parametrize("status", [408, 409, 500, 502, 503, 504, 522])
def test_transient_http_statuses_are_retried(kit: Any, status: int) -> None:
    sdk = kit.FakeSDK(kit.status_error(status), kit.completion(content="ok"))
    client, sleeper = kit.make_client(sdk)
    assert text_call(client) == "ok"
    assert len(sleeper.delays) == 1


def test_a_persistent_server_error_maps_to_kind_server(kit: Any) -> None:
    sdk = kit.FakeSDK(*[kit.status_error(502)] * 2)
    client, _ = kit.make_client(sdk, max_retries=1)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert (info.value.kind, info.value.status) == ("server", 502)


@pytest.mark.parametrize(
    ("make_error", "kind"),
    [
        ("timeout_error", "timeout"),
        ("connection_error", "connection"),
    ],
)
def test_timeouts_and_connection_errors_are_retried(kit: Any, make_error: str, kind: str) -> None:
    error = getattr(kit, make_error)
    sdk = kit.FakeSDK(error(), error(), kit.completion(content="ok"))
    client, sleeper = kit.make_client(sdk)
    assert text_call(client) == "ok"
    assert len(sleeper.delays) == 2

    exhausted = kit.FakeSDK(*[error()] * 3)
    client2, _ = kit.make_client(exhausted, max_retries=2)
    with pytest.raises(LLMCallError) as info:
        text_call(client2)
    assert info.value.kind == kind
    assert info.value.status is None
    assert len(exhausted.calls) == 3


def test_builtin_timeout_and_connection_errors_are_retried_too(kit: Any) -> None:
    sdk = kit.FakeSDK(
        TimeoutError("read timed out"), ConnectionResetError("reset"), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk)
    assert text_call(client) == "ok"
    assert len(sleeper.delays) == 2


def test_the_timeout_message_reports_attempts_and_the_configured_timeout(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.timeout_error())
    client, _ = kit.make_client(sdk, max_retries=0, timeout_s=7)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert "1 attempt" in str(info.value)
    assert "7s" in str(info.value)


def test_retry_after_seconds_sets_a_floor_under_the_delay(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"retry-after": "7"}), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk, jitter=0.0)  # jitter alone would wait 0.5s
    text_call(client)
    assert sleeper.delays == [7.0]


def test_retry_after_milliseconds_is_understood(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"retry-after-ms": "2500"}), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk, jitter=0.0)
    text_call(client)
    assert sleeper.delays == [2.5]


def test_an_enormous_retry_after_is_capped_at_max_delay(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"retry-after": "900"}), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk, max_delay_s=30.0)
    text_call(client)
    assert sleeper.delays == [30.0]


@pytest.mark.parametrize("header", ["soon", "-5", "", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_unusable_retry_after_values_are_ignored(kit: Any, header: str) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"retry-after": header}), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk, jitter=0.0)
    text_call(client)
    assert sleeper.delays == [0.5]


def test_a_small_retry_after_never_shortens_the_backoff(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"retry-after": "0.1"}), kit.completion(content="ok")
    )
    client, sleeper = kit.make_client(sdk, jitter=0.0)
    text_call(client)
    assert sleeper.delays == [0.5]


def test_the_providers_x_should_retry_false_is_respected(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(500, headers={"x-should-retry": "false"}), kit.completion(content="no")
    )
    client, sleeper = kit.make_client(sdk)
    with pytest.raises(LLMCallError):
        text_call(client)
    assert len(sdk.calls) == 1
    assert sleeper.delays == []


def test_the_providers_x_should_retry_true_is_respected(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(429, headers={"x-should-retry": "true"}), kit.completion(content="ok")
    )
    client, _ = kit.make_client(sdk)
    assert text_call(client) == "ok"


@pytest.mark.parametrize(
    ("status", "options", "kind"),
    [
        (401, {"code": "invalid_api_key"}, "auth"),
        (403, {}, "permission"),
        (404, {"code": "model_not_found"}, "not_found"),
        (400, {"code": "invalid_request_error"}, "bad_request"),
        (400, {"code": "context_length_exceeded"}, "too_long"),
        (422, {}, "bad_request"),
        (429, {"code": "insufficient_quota", "error_type": "insufficient_quota"}, "quota"),
        (429, {"code": "billing_hard_limit_reached"}, "quota"),
        (429, {"error_type": "insufficient_quota"}, "quota"),
    ],
)
def test_non_transient_failures_are_not_retried(
    kit: Any, status: int, options: dict[str, Any], kind: str
) -> None:
    sdk = kit.FakeSDK(kit.status_error(status, **options), kit.completion(content="never"))
    client, sleeper = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert (info.value.kind, info.value.status, info.value.retryable) == (kind, status, False)
    assert len(sdk.calls) == 1
    assert sleeper.delays == []


def test_error_messages_are_actionable(kit: Any) -> None:
    def message_for(status: int, **options: Any) -> str:
        client, _ = kit.make_client(
            kit.FakeSDK(kit.status_error(status, **options)), model="test-model"
        )
        with pytest.raises(LLMCallError) as info:
            text_call(client)
        return str(info.value)

    assert "OPENAI_API_KEY" in message_for(401)
    assert "test-model" in message_for(403)
    assert "llm.model" in message_for(404)
    assert "credit" in message_for(429, code="insufficient_quota")
    assert "code=some_code" in message_for(400, code="some_code", param="temperature_x")
    assert "param=temperature_x" in message_for(400, code="some_code", param="temperature_x")


# ------------------------------------------------------------------------------------------- temperature


@pytest.mark.parametrize(
    "rejection",
    [
        {
            "message": "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported.",
            "code": "unsupported_value",
            "param": "temperature",
        },
        {
            "message": "Unsupported parameter: 'temperature' is not supported with this model.",
            "code": "unsupported_parameter",
            "param": "temperature",
        },
        {"message": "This model does not support temperature.", "code": None, "param": None},
    ],
)
def test_a_model_that_rejects_temperature_is_retried_once_without_it(
    kit: Any, rejection: dict[str, Any]
) -> None:
    plan = kit.Plan(title="ok", score=1)
    sdk = kit.FakeSDK(kit.status_error(400, **rejection), kit.completion(parsed=plan))
    client, sleeper = kit.make_client(sdk)
    assert json_call(client, kit, temperature=0.2) == plan
    assert "temperature" in sdk.calls[0]
    assert "temperature" not in sdk.calls[1]
    assert sleeper.delays == []  # an immediate parameter fix, not a backoff
    assert client.omits_temperature


def test_a_422_temperature_rejection_from_a_compatible_server_is_handled_too(kit: Any) -> None:
    reject = kit.status_error(422, message="temperature is not supported", param="temperature")
    sdk = kit.FakeSDK(reject, kit.completion(content="ok"))
    client, _ = kit.make_client(sdk)
    assert text_call(client) == "ok"
    assert "temperature" not in sdk.calls[1]


def test_the_client_remembers_that_temperature_is_rejected(kit: Any) -> None:
    reject = kit.status_error(400, message="temperature unsupported", param="temperature")
    sdk = kit.FakeSDK(
        reject,
        kit.completion(content="first"),
        kit.completion(content="second"),
        kit.completion(parsed=kit.Plan(title="third", score=3)),
    )
    client, _ = kit.make_client(sdk)
    assert text_call(client, temperature=0.4) == "first"
    assert text_call(client, temperature=0.4) == "second"
    json_call(client, kit, temperature=0.2)
    assert len(sdk.calls) == 4  # only the very first request was wasted
    assert [("temperature" in call) for call in sdk.calls] == [True, False, False, False]


def test_temperature_fallback_does_not_consume_the_retry_budget(kit: Any) -> None:
    reject = kit.status_error(400, message="temperature unsupported", param="temperature")
    sdk = kit.FakeSDK(reject, kit.completion(content="ok"))
    client, _ = kit.make_client(sdk, max_retries=0)
    assert text_call(client) == "ok"


def test_the_temperature_memory_is_per_client(kit: Any) -> None:
    reject = kit.status_error(400, message="temperature unsupported", param="temperature")
    first, _ = kit.make_client(kit.FakeSDK(reject, kit.completion(content="ok")))
    text_call(first)
    other_sdk = kit.FakeSDK(kit.completion(content="ok"))
    other, _ = kit.make_client(other_sdk)
    text_call(other)
    assert first.omits_temperature
    assert not other.omits_temperature
    assert other_sdk.calls[0]["temperature"] == 0.4


def test_a_temperature_rejection_that_persists_without_temperature_is_an_error(kit: Any) -> None:
    reject = kit.status_error(400, message="temperature unsupported", param="temperature")
    sdk = kit.FakeSDK(reject, reject, kit.completion(content="never"))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "bad_request"
    assert len(sdk.calls) == 2  # exactly one fallback, no loop


def test_a_rejected_temperature_of_none_never_triggers_the_fallback(kit: Any) -> None:
    reject = kit.status_error(400, message="temperature unsupported", param="temperature")
    sdk = kit.FakeSDK(reject)
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError):
        text_call(client, temperature=None)
    assert len(sdk.calls) == 1
    assert not client.omits_temperature


def test_an_unrelated_400_is_not_mistaken_for_a_temperature_rejection(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.status_error(400, message="messages must be non-empty", param="messages"))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        text_call(client, temperature=0.2)
    assert info.value.kind == "bad_request"
    assert len(sdk.calls) == 1
    assert not client.omits_temperature


# ------------------------------------------------------------------------------------------- JSON mode


SCHEMA_REJECTION = {
    "message": "Invalid schema for response_format 'LooseDict': 'additionalProperties' is required to be false.",
    "code": "invalid_json_schema",
    "param": "response_format",
}


def test_a_schema_strict_mode_cannot_express_falls_back_to_json_mode(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(400, **SCHEMA_REJECTION),
        kit.completion(content='{"data": {"a": "b"}}'),
    )
    client, _ = kit.make_client(sdk)
    result = client.complete_json(
        purpose="map_form_fields", system="Map fields.", user="Form here.", schema=kit.LooseDict
    )
    assert result == kit.LooseDict(data={"a": "b"})
    parse_call, create_call = sdk.calls
    assert parse_call["method"] == "parse"
    assert create_call["method"] == "create"
    assert create_call["response_format"] == {"type": "json_object"}
    system_text = create_call["messages"][0]["content"]
    assert system_text.startswith("Map fields.")
    assert "JSON" in system_text  # JSON mode requires the word to appear in the messages
    assert '"data"' in system_text  # the schema is embedded
    assert create_call["messages"][1] == {"role": "user", "content": "Form here."}


def test_json_mode_is_remembered_per_schema(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(400, **SCHEMA_REJECTION),
        kit.completion(content='{"data": {}}'),
        kit.completion(content='{"data": {"x": "y"}}'),
        kit.completion(parsed=kit.Plan(title="p", score=1)),
    )
    client, _ = kit.make_client(sdk)
    args = {"purpose": "p", "system": "s", "user": "u"}
    client.complete_json(schema=kit.LooseDict, **args)
    client.complete_json(
        schema=kit.LooseDict, **args
    )  # straight to JSON mode: no wasted parse call
    client.complete_json(schema=kit.Plan, **args)  # other schemas still use strict parse
    assert [call["method"] for call in sdk.calls] == ["parse", "create", "create", "parse"]


@pytest.mark.parametrize(
    "rejection",
    [
        {
            "message": "Invalid parameter: 'response_format' of type 'json_schema' is not supported with this model.",
            "code": None,
            "param": None,
        },
        {
            "message": "Structured Outputs are not supported by this model.",
            "code": None,
            "param": None,
        },
        {
            "message": "Invalid schema: additionalProperties must be false.",
            "code": None,
            "param": None,
        },
        {"message": "boom", "code": "invalid_json_schema", "param": None},
        {"message": "boom", "code": None, "param": "response_format.json_schema"},
    ],
)
def test_other_signals_that_strict_outputs_are_unavailable_also_trigger_json_mode(
    kit: Any, rejection: dict[str, Any]
) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(400, **rejection), kit.completion(content='{"title": "t", "score": 2}')
    )
    client, _ = kit.make_client(sdk)
    assert json_call(client, kit) == kit.Plan(title="t", score=2)
    assert sdk.calls[1]["response_format"] == {"type": "json_object"}


def test_an_unrelated_400_does_not_trigger_json_mode(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.status_error(400, message="bad messages", param="messages"))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        json_call(client, kit)
    assert info.value.kind == "bad_request"
    assert len(sdk.calls) == 1


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("this is not json at all", "invalid_json"),
        ('{"data": {"a": 1}}', "schema"),
        ("[]", "schema"),
    ],
)
def test_invalid_json_mode_output_is_an_llm_error(kit: Any, content: str, kind: str) -> None:
    sdk = kit.FakeSDK(kit.status_error(400, **SCHEMA_REJECTION), kit.completion(content=content))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=kit.LooseDict)
    assert info.value.kind == kind


def test_json_mode_output_wrapped_in_a_code_fence_is_accepted(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(400, **SCHEMA_REJECTION),
        kit.completion(content='```json\n{"data": {"k": "v"}}\n```'),
    )
    client, _ = kit.make_client(sdk)
    result = client.complete_json(purpose="p", system="", user="u", schema=kit.LooseDict)
    assert result.data == {"k": "v"}


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"data": {"k": "v"}}\n```',
        '```\n{"data": {"k": "v"}}\n```',
        '```JSON\n{"data": {"k": "v"}}```',
        '```{"data": {"k": "v"}}```',
        '```json {"data": {"k": "v"}} ```',
        '\n\n  ```json\r\n{"data": {"k": "v"}}\r\n```  \n',
    ],
)
def test_code_fence_variants_are_all_unwrapped(kit: Any, content: str) -> None:
    sdk = kit.FakeSDK(kit.status_error(400, **SCHEMA_REJECTION), kit.completion(content=content))
    client, _ = kit.make_client(sdk)
    result = client.complete_json(purpose="p", system="", user="u", schema=kit.LooseDict)
    assert result.data == {"k": "v"}


@pytest.mark.parametrize(
    "content",
    [
        "```" + " " * 200_000,
        "```json" + "\n" * 200_000,
        "```" * 60_000,
        " " * 200_000 + "```",
        "```json\n" + "x" * 200_000,
    ],
    ids=[
        "fence-then-blanks",
        "json-tag-then-newlines",
        "many-fences",
        "blanks-then-fence",
        "unterminated-fence",
    ],
)
def test_degenerate_model_output_cannot_stall_the_parser(kit: Any, content: str) -> None:
    import time

    sdk = kit.FakeSDK(kit.status_error(400, **SCHEMA_REJECTION), kit.completion(content=content))
    client, _ = kit.make_client(sdk)
    started = time.perf_counter()
    with pytest.raises(
        LLMCallError
    ):  # not valid JSON, but it must fail fast (a quadratic regex would not)
        client.complete_json(purpose="p", system="", user="u", schema=kit.LooseDict)
    assert time.perf_counter() - started < 5.0


def test_json_mode_uses_the_same_retry_and_temperature_handling(kit: Any) -> None:
    sdk = kit.FakeSDK(
        kit.status_error(400, **SCHEMA_REJECTION),
        kit.status_error(429),
        kit.status_error(400, message="temperature unsupported", param="temperature"),
        kit.completion(content='{"data": {"a": "b"}}'),
    )
    client, sleeper = kit.make_client(sdk)
    result = client.complete_json(
        purpose="p", system="s", user="u", schema=kit.LooseDict, temperature=0.2
    )
    assert result.data == {"a": "b"}
    assert len(sleeper.delays) == 1
    assert "temperature" not in sdk.calls[-1]
    assert sdk.calls[-1]["response_format"] == {"type": "json_object"}


def test_older_sdks_expose_parse_under_beta(kit: Any) -> None:
    import types

    plan = kit.Plan(title="beta", score=1)
    calls: list[dict[str, Any]] = []

    def beta_parse(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return kit.completion(parsed=plan)

    sdk = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace()),
        beta=types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=beta_parse))
        ),
    )
    client, _ = kit.make_client(sdk)
    assert json_call(client, kit) == plan
    assert calls[0]["response_format"] is kit.Plan


def test_a_schema_that_cannot_be_described_in_json_mode_is_an_llm_error(kit: Any) -> None:
    import types
    from collections.abc import Callable

    class Undescribable(BaseModel):
        model_config = {"arbitrary_types_allowed": True}
        hook: Callable[[int], int]

    sdk = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **kw: None))
    )
    client, _ = kit.make_client(sdk)  # no parse helper: goes straight to JSON mode
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=Undescribable)
    assert info.value.kind == "schema"
    assert "Undescribable" in str(info.value)


def test_an_sdk_without_any_parse_helper_uses_json_mode(kit: Any) -> None:
    import types

    created: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return kit.completion(content='{"title": "t", "score": 5}')

    sdk = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    client, _ = kit.make_client(sdk)
    assert json_call(client, kit) == kit.Plan(title="t", score=5)
    assert created[0]["response_format"] == {"type": "json_object"}


# ------------------------------------------------------------------------------------------- output mapping


def test_a_refusal_is_an_llm_error_that_does_not_quote_the_refusal(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(parsed=None, refusal=f"I can't help with {RESPONSE_MARKER}"))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        json_call(client, kit)
    assert info.value.kind == "refusal"
    assert RESPONSE_MARKER not in str(info.value)


def test_a_text_refusal_is_an_llm_error(kit: Any) -> None:
    sdk = kit.FakeSDK(kit.completion(content=None, refusal="no"))
    client, _ = kit.make_client(sdk)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "refusal"


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_empty_output_is_an_llm_error(kit: Any, content: str | None) -> None:
    for call in (lambda c: json_call(c, kit), text_call):
        client, _ = kit.make_client(kit.FakeSDK(kit.completion(parsed=None, content=content)))
        with pytest.raises(LLMCallError) as info:
            call(client)
        assert info.value.kind == "empty"


def test_a_response_without_choices_is_an_llm_error(kit: Any) -> None:
    import types

    empty = types.SimpleNamespace(choices=[], usage=None)
    for call in (lambda c: json_call(c, kit), text_call):
        client, _ = kit.make_client(kit.FakeSDK(empty))
        with pytest.raises(LLMCallError) as info:
            call(client)
        assert info.value.kind == "empty"


@pytest.mark.parametrize(
    ("finish_reason", "kind"), [("length", "truncated"), ("content_filter", "refusal")]
)
def test_finish_reasons_that_signal_unusable_output_are_llm_errors(
    kit: Any, finish_reason: str, kind: str
) -> None:
    for call in (lambda c: json_call(c, kit), text_call):
        sdk = kit.FakeSDK(kit.completion(content="partial", finish_reason=finish_reason))
        client, _ = kit.make_client(sdk)
        with pytest.raises(LLMCallError) as info:
            call(client)
        assert info.value.kind == kind


def test_sdk_raised_length_and_content_filter_errors_are_mapped(kit: Any) -> None:
    import types

    length = openai.LengthFinishReasonError(completion=types.SimpleNamespace(usage=None))
    filtered = openai.ContentFilterFinishReasonError()
    for error, kind in ((length, "truncated"), (filtered, "refusal")):
        client, _ = kit.make_client(kit.FakeSDK(error))
        with pytest.raises(LLMCallError) as info:
            json_call(client, kit)
        assert info.value.kind == kind


def test_a_validation_error_from_the_sdk_parse_is_mapped_without_echoing_the_value(
    kit: Any,
) -> None:
    class Strict(BaseModel):
        score: int

    try:
        Strict.model_validate_json(f'{{"score": "{RESPONSE_MARKER}"}}')
    except ValidationError as exc:
        mismatch = exc
    try:
        Strict.model_validate_json("definitely not json")
    except ValidationError as exc:
        garbage = exc

    for error, kind, fragment in (
        (mismatch, "schema", "score (int_parsing)"),
        (garbage, "invalid_json", "<root> (json_invalid)"),
    ):
        client, _ = kit.make_client(kit.FakeSDK(error))
        with pytest.raises(LLMCallError) as info:
            client.complete_json(purpose="p", system="", user="u", schema=Strict)
        assert info.value.kind == kind
        assert fragment in str(info.value)
        assert RESPONSE_MARKER not in str(info.value)
        assert "definitely not json" not in str(info.value)
        assert "Strict" in str(info.value)


def test_a_validation_error_message_lists_at_most_five_locations(kit: Any) -> None:
    class Wide(BaseModel):
        a: int
        b: int
        c: int
        d: int
        e: int
        f: int
        g: int

    with pytest.raises(ValidationError) as raised:
        Wide.model_validate({})
    client, _ = kit.make_client(kit.FakeSDK(raised.value))
    with pytest.raises(LLMCallError) as info:
        client.complete_json(purpose="p", system="", user="u", schema=Wide)
    assert "7 validation error(s)" in str(info.value)
    assert "and 2 more" in str(info.value)


# ------------------------------------------------------------------------------------------- every failure


UNEXPECTED_ERRORS = [
    RuntimeError("boom"),
    TypeError("Unable to automatically parse response format type"),
    ValueError("bad value"),
    KeyError("choices"),
    AttributeError("no attribute"),
    OSError("disk on fire"),
    FileNotFoundError("ca-bundle.pem"),
    ZeroDivisionError(),
]


@pytest.mark.parametrize("error", UNEXPECTED_ERRORS, ids=lambda e: type(e).__name__)
def test_any_unexpected_exception_becomes_an_llm_error(kit: Any, error: Exception) -> None:
    for call in (lambda c: json_call(c, kit), text_call):
        sdk = kit.FakeSDK(error, kit.completion(content="never"))
        client, sleeper = kit.make_client(sdk)
        with pytest.raises(LLMError) as info:
            call(client)
        assert isinstance(info.value, LLMCallError)
        assert info.value.kind == "unexpected"
        assert type(error).__name__ in str(info.value)
        assert str(error) not in str(info.value) or str(error) == ""
        assert len(sdk.calls) == 1  # not retried
        assert sleeper.delays == []
        assert info.value.__cause__ is None


def test_keyboard_interrupt_is_never_swallowed(kit: Any) -> None:
    client, _ = kit.make_client(kit.FakeSDK(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        text_call(client)


def test_a_bug_in_response_handling_still_surfaces_as_an_llm_error(kit: Any) -> None:
    class Hostile:
        @property
        def choices(self) -> Any:
            raise RuntimeError("attribute access exploded")

    client, _ = kit.make_client(kit.FakeSDK(Hostile()))
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "unexpected"


# ------------------------------------------------------------------------------------------- logging


def test_success_is_logged_with_metadata_only(kit: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sdk = kit.FakeSDK(
        kit.status_error(429),
        kit.completion(
            content=f"letter {RESPONSE_MARKER}", prompt_tokens=321, completion_tokens=45
        ),
    )
    client, _ = kit.make_client(sdk, model="log-model")
    text_call(client, purpose="cover_letter", system=PROMPT_MARKER, user=PROMPT_MARKER)
    text = caplog.text
    assert "purpose=cover_letter" in text
    assert "model=log-model" in text
    assert "latency_ms=" in text
    assert "attempts=2" in text
    assert "prompt_tokens=321" in text
    assert "completion_tokens=45" in text
    assert "retry" in text
    assert PROMPT_MARKER not in text
    assert RESPONSE_MARKER not in text
    assert kit.KEY not in text


def test_failures_are_logged_without_prompts_or_provider_text(
    kit: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    error = kit.status_error(401, message=f"Incorrect API key {kit.KEY} for {PROMPT_MARKER}")
    client, _ = kit.make_client(kit.FakeSDK(error))
    with pytest.raises(LLMCallError):
        text_call(client, system=PROMPT_MARKER, user=PROMPT_MARKER)
    assert "kind=auth" in caplog.text
    assert "status=401" in caplog.text
    assert PROMPT_MARKER not in caplog.text
    assert kit.KEY not in caplog.text


def test_a_failed_call_updates_the_failure_counter(kit: Any) -> None:
    client, _ = kit.make_client(kit.FakeSDK(kit.status_error(401)))
    with pytest.raises(LLMCallError):
        text_call(client)
    assert (client.usage.calls, client.usage.failures) == (0, 1)
