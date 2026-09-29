"""The API key stays in its box: files, repr(), logs and exception text (docs/SPEC.md rule 1.7, acceptance A1/A8).

Providers, proxies and HTTP libraries sometimes echo request headers or a partially masked key inside their error
text. These tests make every failure path hostile on purpose and assert that neither the key nor the shape a
provider echoes back ever reaches an exception message, a repr, a log line, or a file under the data directory.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

import openai
import pytest

from autoapply.config import AppConfig, AppPaths, save_config
from autoapply.llm import BudgetedLLM, FakeLLM, LLMCallError, LLMRequest, OpenAIClient, build_llm
from autoapply.models import Profile
from autoapply.readiness import (
    ReadinessError,
    check_readiness,
    ensure_ready_or_raise,
    format_report,
)
from autoapply.secrets import (
    KeyResolution,
    KeyringCredentialStore,
    MemoryCredentialStore,
    resolve_openai_key,
)

KEY = "sk-test-FICTIONAL0123456789abcdefghijklmnopqrstuvwx"
MASKED_ECHO = f"{KEY[:8]}****{KEY[-4:]}"  # what OpenAI itself echoes in a 401
PROMPT_MARKER = "ZXQ-PRIVATE-PROFILE-DATA-ALEX-RIVERA"
ENV = {"OPENAI_API_KEY": KEY}


@pytest.fixture
def workbook_file(tmp_path: Path) -> Path:
    path = tmp_path / "Verified Opportunities.xlsx"
    path.write_bytes(b"PK fictional workbook")
    return path


def hostile_text() -> str:
    return (
        f"Incorrect API key provided: {KEY}. Request header was 'Authorization: Bearer {KEY}'. "
        f"Masked form: {MASKED_ECHO}. Prompt began: {PROMPT_MARKER}."
    )


def assert_clean(text: str) -> None:
    assert KEY not in text
    assert MASKED_ECHO not in text
    assert f"Bearer {KEY}" not in text
    assert PROMPT_MARKER not in text


def exception_text(exc: BaseException) -> str:
    """Every textual view of an exception a log handler or a crash report might render."""
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"{exc}\n{exc!r}\n{exc.args!r}\n{rendered}"


def hostile_failures(kit: Any) -> dict[str, Exception]:
    text = hostile_text()
    return {
        "401": kit.status_error(401, message=text, code="invalid_api_key"),
        "403": kit.status_error(403, message=text),
        "404": kit.status_error(404, message=text, code="model_not_found"),
        "400": kit.status_error(400, message=text, code="invalid_request_error", param="messages"),
        "400-key-in-code-and-param": kit.status_error(400, message=text, code=KEY, param=KEY),
        "422": kit.status_error(422, message=text),
        "429-rate-limit": kit.status_error(429, message=text, code="rate_limit_exceeded"),
        "429-quota": kit.status_error(429, message=text, code="insufficient_quota"),
        "500": kit.status_error(500, message=text),
        "502": kit.status_error(502, message=text, code=KEY),
        "408": kit.status_error(408, message=text),
        "timeout": kit.timeout_error(),
        "connection": kit.connection_error(),
        "runtime": RuntimeError(text),
        "value": ValueError(text),
        "key-error": KeyError(text),
        "connection-reset": ConnectionResetError(text),
        "builtin-timeout": TimeoutError(text),
        "os-error": OSError(text),
    }


FAILURE_IDS = [
    "401", "403", "404", "400", "400-key-in-code-and-param", "422", "429-rate-limit", "429-quota", "500",
    "502", "408", "timeout", "connection", "runtime", "value", "key-error", "connection-reset",
    "builtin-timeout", "os-error",
]  # fmt: skip


def run_failure(kit: Any, error: Exception, *, json_mode: bool) -> LLMCallError:
    """Drive one hostile failure (twice, so retryable ones exercise a retry) and return the LLMCallError."""
    sdk = kit.FakeSDK(error, error)
    client, _ = kit.make_client(sdk, max_retries=1)
    with pytest.raises(LLMCallError) as info:
        if json_mode:
            client.complete_json(
                purpose="tailor_resume", system=PROMPT_MARKER, user=PROMPT_MARKER, schema=kit.Plan
            )
        else:
            client.complete_text(purpose="cover_letter", system=PROMPT_MARKER, user=PROMPT_MARKER)
    return info.value


# ------------------------------------------------------------------------------------------- exceptions


@pytest.mark.parametrize("failure", FAILURE_IDS)
@pytest.mark.parametrize("json_mode", [False, True], ids=["text", "json"])
def test_no_failure_leaks_the_key_into_exception_text(
    kit: Any, failure: str, json_mode: bool
) -> None:
    error = run_failure(kit, hostile_failures(kit)[failure], json_mode=json_mode)
    assert_clean(exception_text(error))
    assert error.__cause__ is None
    assert error.kind != "unknown"


def test_a_provider_that_echoes_the_key_in_a_model_refusal_or_output_leaks_nothing(
    kit: Any,
) -> None:
    text = hostile_text()
    both = (True, False)
    json_only = (True,)  # in text mode, any non-empty content is a valid answer
    outputs = {
        "refusal": (kit.completion(parsed=None, refusal=text), both),
        "empty-with-refusal": (kit.completion(content=None, refusal=text), both),
        "bad-json": (kit.completion(parsed=None, content=text), json_only),
        "schema-mismatch": (
            kit.completion(parsed=None, content=f'{{"title": "{KEY}", "score": "x"}}'),
            json_only,
        ),
        "truncated": (kit.completion(content=text, finish_reason="length"), both),
        "filtered": (kit.completion(content=text, finish_reason="content_filter"), both),
    }
    for completion, modes in outputs.values():
        for use_json in modes:
            client, _ = kit.make_client(kit.FakeSDK(completion))
            with pytest.raises(LLMCallError) as info:
                if use_json:
                    client.complete_json(
                        purpose="p", system="", user=PROMPT_MARKER, schema=kit.Plan
                    )
                else:
                    client.complete_text(purpose="p", system="", user=PROMPT_MARKER)
            assert_clean(exception_text(info.value))


def test_a_transport_error_quoting_the_authorization_header_leaks_nothing(kit: Any) -> None:
    transport_error = kit.http.ConnectError(f"cannot connect; Authorization: Bearer {KEY}")
    error = run_failure(kit, transport_error, json_mode=False)
    assert error.kind == "unexpected"  # not an SDK connection error: an arbitrary library exception
    assert_clean(exception_text(error))


def test_the_malformed_key_message_does_not_echo_the_key(kit: Any) -> None:
    bad = f"{KEY} trailing words"
    client, _ = kit.make_client(kit.FakeSDK(), key=bad)
    with pytest.raises(LLMCallError) as info:
        client.complete_text(purpose="p", system="", user="u")
    assert info.value.kind == "bad_key"
    assert KEY not in exception_text(info.value)


# ------------------------------------------------------------------------------------------- logs


def test_no_failure_leaks_the_key_or_prompts_into_logs(
    kit: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for json_mode in (False, True):
        for error in hostile_failures(kit).values():
            run_failure(kit, error, json_mode=json_mode)
    assert caplog.records, (
        "the client should have logged something to prove the assertion is meaningful"
    )
    assert_clean(caplog.text)
    for record in caplog.records:
        assert_clean(record.getMessage())
        assert_clean(repr(record.args))


def test_successful_calls_log_metadata_only(kit: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sdk = kit.FakeSDK(
        kit.completion(parsed=kit.Plan(title=PROMPT_MARKER, score=1)),
        kit.completion(content=f"letter mentioning {PROMPT_MARKER} and {KEY}"),
    )
    client, _ = kit.make_client(sdk)
    client.complete_json(purpose="p", system=PROMPT_MARKER, user=PROMPT_MARKER, schema=kit.Plan)
    client.complete_text(purpose="p", system=PROMPT_MARKER, user=PROMPT_MARKER)
    assert "llm ok" in caplog.text
    assert_clean(caplog.text)


def test_a_real_sdk_round_trip_at_debug_level_leaks_nothing(
    kit: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def respond(request: Any) -> Any:
        return kit.http.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"title": "t", "score": 1}',
                            "refusal": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    sdk = openai.OpenAI(
        api_key=KEY,
        base_url="http://localhost:9/v1",
        http_client=kit.http.Client(transport=kit.http.MockTransport(respond)),
        max_retries=0,
    )
    client, _ = kit.make_client(sdk)
    client.complete_json(purpose="p", system=PROMPT_MARKER, user=PROMPT_MARKER, schema=kit.Plan)
    assert caplog.records
    assert_clean(caplog.text)


def test_secrets_and_readiness_log_nothing_at_all(
    caplog: pytest.LogCaptureFixture, paths: AppPaths
) -> None:
    caplog.set_level(logging.DEBUG)
    store = MemoryCredentialStore()
    resolve_openai_key(ENV, store)
    resolve_openai_key({}, store)
    check_readiness(AppConfig(), paths, ENV, store)
    assert caplog.records == []


# ------------------------------------------------------------------------------------------- repr / str


def test_no_object_that_touches_the_key_shows_it_in_repr_or_str(kit: Any, paths: AppPaths) -> None:
    resolution = resolve_openai_key(ENV)
    client = OpenAIClient(KEY, "test-model", client=kit.FakeSDK())
    built = build_llm(AppConfig(), KEY, env={})
    report = check_readiness(AppConfig(), paths, ENV)
    objects: list[object] = [
        resolution,
        KeyResolution(key=KEY, source="credential_store"),
        client,
        built,
        BudgetedLLM(client, 3),
        report,
        ReadinessError(report),
        MemoryCredentialStore({("autoapply:openai", "api_key"): KEY}),
        KeyringCredentialStore(),
        FakeLLM({"p": KEY}),
        LLMRequest("p", KEY, KEY),
        LLMCallError("boom", kind="auth"),
    ]
    for obj in objects:
        for text in (repr(obj), str(obj), f"{obj}", f"{obj!r:>200}"):
            assert KEY not in text, type(obj).__name__
            assert KEY[8:-4] not in text, type(obj).__name__


def test_the_config_and_profile_never_hold_the_key(paths: AppPaths) -> None:
    config = AppConfig()
    check_readiness(config, paths, ENV)
    assert KEY not in repr(config)
    assert KEY not in config.model_dump_json()
    assert KEY not in repr(Profile())


# ------------------------------------------------------------------------------------------- files and stores


def test_the_env_key_is_never_persisted_by_the_whole_start_up_flow(
    kit: Any,
    paths: AppPaths,
    workbook_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """resolve -> readiness -> build -> call (success and failure) -> save config: the key lands nowhere."""
    caplog.set_level(logging.DEBUG)
    config = AppConfig()
    config.profile.first_name = "Alex"
    config.profile.last_name = "Rivera"
    config.workbook.path = str(workbook_file)
    store = MemoryCredentialStore()

    sdk = kit.FakeSDK(
        kit.completion(content="a cover letter"),
        kit.status_error(401, message=hostile_text()),
    )
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: sdk)

    resolution = resolve_openai_key(ENV, store)
    assert resolution.source == "env"
    check_readiness(config, paths, ENV, store)
    with pytest.raises(ReadinessError) as not_ready:
        ensure_ready_or_raise(config, paths, ENV, store)
    format_report(not_ready.value.report)

    llm = build_llm(config, resolution.key, env={})
    assert llm.complete_text(purpose="cover_letter", system="s", user="u") == "a cover letter"
    with pytest.raises(LLMCallError):
        llm.complete_text(purpose="cover_letter", system="s", user="u")

    save_config(paths, config)
    assert store.snapshot() == {}  # the credential store was never written
    for path in paths.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), f"key found in {path}"
    assert_clean(caplog.text)
