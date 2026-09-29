"""FakeLLM (scripted client), BudgetedLLM (per-application call budget) and build_llm (factory)."""

from __future__ import annotations

import importlib
import sys
import threading
import types
from typing import Any

import openai
import pytest
from pydantic import BaseModel, ConfigDict

from autoapply.config import AppConfig
from autoapply.contracts import LLMClient, LLMError
from autoapply.llm import (
    FAKE_LLM_ENV,
    TESTING_ENV,
    BudgetedLLM,
    FakeLLM,
    LLMCallError,
    LLMRequest,
    OpenAIClient,
    build_llm,
)

FAKE_MODULE = "autoapply.testing.fake_llm"


class Plan(BaseModel):
    title: str
    score: int


class SamePlanShape(BaseModel):
    title: str
    score: int


class Forbidding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


def json_call(
    llm: LLMClient, purpose: str = "tailor_resume", schema: Any = Plan, **extra: Any
) -> Any:
    return llm.complete_json(purpose=purpose, system="sys", user="usr", schema=schema, **extra)


def text_call(llm: LLMClient, purpose: str = "cover_letter", **extra: Any) -> str:
    return llm.complete_text(purpose=purpose, system="sys", user="usr", **extra)


# ------------------------------------------------------------------------------------------- FakeLLM values


@pytest.mark.parametrize(
    "scripted",
    [
        Plan(title="PM", score=7),
        {"title": "PM", "score": 7},
        '{"title": "PM", "score": 7}',
        b'{"title": "PM", "score": 7}',
        SamePlanShape(title="PM", score=7),
    ],
    ids=["instance", "dict", "json-str", "json-bytes", "other-model"],
)
def test_a_scripted_value_is_validated_into_the_requested_schema(scripted: Any) -> None:
    fake = FakeLLM()
    fake.register("tailor_resume", scripted)
    result = json_call(fake)
    assert type(result) is Plan
    assert result == Plan(title="PM", score=7)


def test_an_instance_of_the_schema_is_returned_as_is() -> None:
    plan = Plan(title="PM", score=7)
    fake = FakeLLM({"tailor_resume": plan})
    assert json_call(fake) is plan


def test_a_handler_receives_the_recorded_request_and_can_compute_the_answer() -> None:
    seen: list[LLMRequest] = []

    def handler(request: LLMRequest) -> dict[str, Any]:
        seen.append(request)
        return {"title": request.user.upper(), "score": len(request.system)}

    fake = FakeLLM()
    fake.register("tailor_resume", handler)
    result = fake.complete_json(
        purpose="tailor_resume",
        system="four",
        user="hello",
        schema=Plan,
        temperature=0.9,
        max_tokens=12,
    )
    assert result == Plan(title="HELLO", score=4)
    (request,) = seen
    assert (request.purpose, request.system, request.user) == ("tailor_resume", "four", "hello")
    assert request.schema is Plan
    assert (request.temperature, request.max_tokens) == (0.9, 12)


def test_calls_are_recorded_in_order_with_every_field() -> None:
    fake = FakeLLM({"a": {"title": "t", "score": 1}, "b": "text"})
    json_call(fake, "a")
    text_call(fake, "b", temperature=0.1, max_tokens=5)
    first, second = fake.calls
    assert (first.purpose, first.system, first.user, first.schema) == ("a", "sys", "usr", Plan)
    assert not first.is_text
    assert (second.purpose, second.schema, second.temperature, second.max_tokens) == (
        "b",
        None,
        0.1,
        5,
    )
    assert second.is_text
    assert [call.purpose for call in fake.calls] == ["a", "b"]
    assert fake.calls_for("b") == [second]
    assert fake.calls_for("zzz") == []


def test_default_temperatures_are_recorded_as_the_contract_defaults() -> None:
    fake = FakeLLM({"a": {"title": "t", "score": 1}, "b": "x"})
    json_call(fake, "a")
    text_call(fake, "b")
    assert fake.calls[0].temperature == 0.2
    assert fake.calls[1].temperature == 0.4


def test_a_later_registration_replaces_an_earlier_one_and_unregister_removes_it() -> None:
    fake = FakeLLM()
    fake.register("p", "one")
    fake.register("p", "two")
    assert text_call(fake, "p") == "two"
    fake.unregister("p")
    fake.unregister("p")  # idempotent
    with pytest.raises(LLMError):
        text_call(fake, "p")


def test_constructor_handlers_are_registered() -> None:
    fake = FakeLLM({"a": "alpha", "b": lambda request: request.purpose + "!"})
    assert text_call(fake, "a") == "alpha"
    assert text_call(fake, "b") == "b!"


# ------------------------------------------------------------------------------------------- FakeLLM failures


def test_an_unregistered_purpose_raises_llm_error_but_is_still_recorded() -> None:
    fake = FakeLLM({"known": "x"})
    with pytest.raises(LLMError, match="no handler registered for purpose 'mystery'") as info:
        text_call(fake, "mystery")
    assert isinstance(info.value, LLMCallError)
    assert info.value.kind == "no_handler"
    with pytest.raises(LLMError):
        json_call(fake, "mystery2")
    assert [call.purpose for call in fake.calls] == ["mystery", "mystery2"]


def test_fail_all_fails_every_call_until_switched_off() -> None:
    fake = FakeLLM({"a": {"title": "t", "score": 1}, "b": "text"})
    fake.fail_all()
    for call in (lambda: json_call(fake, "a"), lambda: text_call(fake, "b")):
        with pytest.raises(LLMError, match="scripted failure"):
            call()
    assert len(fake.calls) == 2  # attempts are recorded even while failing
    fake.fail_all(False)
    assert json_call(fake, "a") == Plan(title="t", score=1)
    assert text_call(fake, "b") == "text"


def test_fail_all_message_can_be_customised() -> None:
    fake = FakeLLM({"a": "x"})
    fake.fail_all(message="OpenAI is down (simulated)")
    with pytest.raises(LLMError, match="OpenAI is down"):
        text_call(fake, "a")
    fake.fail_all(False)
    fake.fail_all()  # message sticks until changed
    with pytest.raises(LLMError, match="OpenAI is down"):
        text_call(fake, "a")


@pytest.mark.parametrize(
    "scripted",
    [
        {"title": "PM"},
        {"title": "PM", "score": "not a number"},
        "not json",
        '{"title": "PM"}',
        "[]",
        b"\xff\xfe",
        42,
        None,
        ["title", "score"],
    ],
    ids=[
        "missing-field",
        "wrong-type",
        "garbage-json",
        "json-missing-field",
        "json-array",
        "bad-bytes",
        "int",
        "none",
        "list",
    ],
)
def test_invalid_scripted_output_is_an_llm_error(scripted: Any) -> None:
    fake = FakeLLM({"p": scripted})
    with pytest.raises(LLMCallError) as info:
        json_call(fake, "p")
    assert info.value.kind in {"schema", "invalid_json", "scripted"}
    assert "'p'" in str(info.value)


def test_extra_fields_rejected_by_the_schema_are_an_llm_error() -> None:
    fake = FakeLLM({"p": {"title": "ok", "surprise": 1}})
    with pytest.raises(LLMError, match="Forbidding"):
        json_call(fake, "p", schema=Forbidding)


def test_validation_messages_show_locations_not_values() -> None:
    fake = FakeLLM({"p": {"title": "PM", "score": "SECRET-LOOKING-VALUE"}})
    with pytest.raises(LLMError) as info:
        json_call(fake, "p")
    assert "score (int_parsing)" in str(info.value)
    assert "SECRET-LOOKING-VALUE" not in str(info.value)


def test_text_purposes_require_a_string() -> None:
    fake = FakeLLM({"num": 7, "model": Plan(title="t", score=1), "none": None, "ok": "fine"})
    for purpose in ("num", "model", "none"):
        with pytest.raises(LLMCallError, match="must be a str"):
            text_call(fake, purpose)
    assert text_call(fake, "ok") == "fine"


def test_scripted_exception_instances_are_raised() -> None:
    fake = FakeLLM({"down": LLMError("provider outage"), "bug": RuntimeError("test bug")})
    with pytest.raises(LLMError, match="provider outage"):
        text_call(fake, "down")
    with pytest.raises(LLMError, match="provider outage"):
        json_call(fake, "down")
    with pytest.raises(
        RuntimeError, match="test bug"
    ):  # a non-LLM error is a bug and must stay visible
        text_call(fake, "bug")


def test_handlers_that_raise_propagate_unchanged() -> None:
    def failing(request: LLMRequest) -> str:
        raise LLMError("handler says no")

    def buggy(request: LLMRequest) -> str:
        raise KeyError("oops")

    fake = FakeLLM({"failing": failing, "buggy": buggy})
    with pytest.raises(LLMError, match="handler says no"):
        text_call(fake, "failing")
    with pytest.raises(KeyError):
        text_call(fake, "buggy")


# ------------------------------------------------------------------------------------------- FakeLLM sequences


def test_a_sequence_serves_successive_answers_then_reports_exhaustion() -> None:
    fake = FakeLLM()
    fake.register_sequence("p", [{"title": "one", "score": 1}, {"title": "two", "score": 2}])
    assert json_call(fake, "p").title == "one"
    assert json_call(fake, "p").title == "two"
    with pytest.raises(LLMError, match="exhausted"):
        json_call(fake, "p")


def test_repeat_last_keeps_serving_the_final_answer() -> None:
    fake = FakeLLM()
    fake.register_sequence("p", ["first", "last"], repeat_last=True)
    assert [text_call(fake, "p") for _ in range(4)] == ["first", "last", "last", "last"]


def test_sequence_items_may_be_handlers_or_exceptions() -> None:
    fake = FakeLLM()
    fake.register_sequence(
        "p", [LLMError("first attempt fails"), lambda request: f"echo:{request.user}", "plain"]
    )
    with pytest.raises(LLMError, match="first attempt fails"):
        text_call(fake, "p")
    assert text_call(fake, "p") == "echo:usr"
    assert text_call(fake, "p") == "plain"


def test_an_empty_sequence_is_immediately_exhausted() -> None:
    fake = FakeLLM()
    fake.register_sequence("p", [])
    with pytest.raises(LLMError, match="exhausted"):
        text_call(fake, "p")


# ------------------------------------------------------------------------------------------- FakeLLM hygiene


def test_request_repr_shows_sizes_not_prompt_text() -> None:
    request = LLMRequest("cover_letter", "SYSTEM-SECRET-TEXT", "USER-SECRET-TEXT", Plan)
    text = repr(request)
    assert "SECRET" not in text
    assert "cover_letter" in text
    assert "Plan" in text
    assert "system_chars=18" in text
    assert "user_chars=16" in text


def test_fake_repr_lists_purposes_and_counts() -> None:
    fake = FakeLLM({"b": "x", "a": "y"})
    text_call(fake, "a")
    assert repr(fake) == "FakeLLM(purposes=['a', 'b'], calls=1, fail_all=False)"


def test_requests_are_immutable_records() -> None:
    request = LLMRequest("p", "s", "u")
    with pytest.raises(AttributeError):
        request.purpose = "other"  # type: ignore[misc]


# ------------------------------------------------------------------------------------------- BudgetedLLM


def test_the_budget_allows_exactly_max_calls_then_refuses() -> None:
    fake = FakeLLM({"a": {"title": "t", "score": 1}})
    budgeted = BudgetedLLM(fake, 3)
    for _ in range(3):
        json_call(budgeted, "a")
    with pytest.raises(LLMCallError) as info:
        json_call(budgeted, "a")
    assert info.value.kind == "budget"
    assert "3" in str(info.value)
    assert "'a'" in str(info.value)
    assert len(fake.calls) == 3  # the refused call never reached the wrapped client


def test_json_and_text_calls_share_one_budget() -> None:
    fake = FakeLLM({"j": {"title": "t", "score": 1}, "t": "x"})
    budgeted = BudgetedLLM(fake, 2)
    json_call(budgeted, "j")
    text_call(budgeted, "t")
    for call in (lambda: json_call(budgeted, "j"), lambda: text_call(budgeted, "t")):
        with pytest.raises(LLMError, match="budget"):
            call()


def test_failed_calls_still_spend_budget() -> None:
    fake = FakeLLM()
    fake.fail_all()
    budgeted = BudgetedLLM(fake, 2)
    for _ in range(2):
        with pytest.raises(LLMError, match="scripted failure"):
            text_call(budgeted, "a")
    with pytest.raises(LLMError, match="budget"):
        text_call(budgeted, "a")
    assert len(fake.calls) == 2
    assert (budgeted.used, budgeted.remaining) == (2, 0)


def test_a_zero_budget_disables_the_llm_entirely() -> None:
    fake = FakeLLM({"a": "x"})
    budgeted = BudgetedLLM(fake, 0)
    with pytest.raises(LLMError, match="budget"):
        text_call(budgeted, "a")
    assert fake.calls == []


def test_a_negative_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        BudgetedLLM(FakeLLM(), -1)


def test_budget_counters() -> None:
    budgeted = BudgetedLLM(FakeLLM({"a": "x"}), 5)
    assert (budgeted.max_calls, budgeted.used, budgeted.remaining) == (5, 0, 5)
    text_call(budgeted, "a")
    assert (budgeted.used, budgeted.remaining) == (1, 4)
    assert repr(budgeted) == "BudgetedLLM(used=1/5)"


def test_parameters_are_passed_through_unchanged() -> None:
    fake = FakeLLM({"a": {"title": "t", "score": 1}, "b": "x"})
    budgeted = BudgetedLLM(fake, 5)
    budgeted.complete_json(
        purpose="a", system="S", user="U", schema=Plan, temperature=0.7, max_tokens=99
    )
    budgeted.complete_text(purpose="b", system="S2", user="U2", temperature=None, max_tokens=7)
    first, second = fake.calls
    assert (first.system, first.user, first.schema, first.temperature, first.max_tokens) == (
        "S",
        "U",
        Plan,
        0.7,
        99,
    )
    assert (second.system, second.user, second.temperature, second.max_tokens) == (
        "S2",
        "U2",
        None,
        7,
    )


def test_each_wrapper_has_its_own_budget() -> None:
    fake = FakeLLM({"a": "x"})
    first, second = BudgetedLLM(fake, 1), BudgetedLLM(fake, 1)
    text_call(first, "a")
    text_call(second, "a")  # a fresh application gets a fresh budget
    with pytest.raises(LLMError):
        text_call(first, "a")


def test_the_budget_is_thread_safe() -> None:
    fake = FakeLLM({"a": "x"})
    budgeted = BudgetedLLM(fake, 25)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(10):
            try:
                text_call(budgeted, "a")
                ok = True
            except LLMError:
                ok = False
            with lock:
                outcomes.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(outcomes) == 80
    assert outcomes.count(True) == 25
    assert len(fake.calls) == 25


# ------------------------------------------------------------------------------------------- build_llm


def brain_module(factory: Any) -> types.ModuleType:
    module = types.ModuleType(FAKE_MODULE)
    module.build_fake_brain = factory  # type: ignore[attr-defined]
    return module


def test_build_llm_returns_an_openai_client_configured_from_the_config() -> None:
    config = AppConfig()
    config.llm.model = "custom-model"
    config.llm.timeout_s = 33
    config.llm.max_retries = 5
    client = build_llm(config, "sk-test-FICTIONAL0123456789abcdefghijkl", env={})
    assert isinstance(client, OpenAIClient)
    assert (client.model, client.timeout_s, client.max_retries) == ("custom-model", 33.0, 5)


def test_build_llm_without_a_key_fails_at_the_first_call_not_at_construction() -> None:
    client = build_llm(AppConfig(), None, env={})  # must not raise
    assert isinstance(client, OpenAIClient)
    with pytest.raises(LLMCallError) as info:
        text_call(client)
    assert info.value.kind == "no_key"


def test_build_llm_hands_the_key_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    class Recording:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kw: types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(
                                    content="hi", refusal=None, parsed=None
                                ),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    )
                )
            )

    monkeypatch.setattr(openai, "OpenAI", Recording)
    key = "sk-test-FICTIONAL0123456789abcdefghijkl"
    assert text_call(build_llm(AppConfig(), key, env={})) == "hi"
    assert built[0]["api_key"] == key
    assert built[0]["max_retries"] == 0


def test_fake_brain_requires_both_flags_and_is_loaded_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = FakeLLM({"a": "brain"})
    monkeypatch.setitem(sys.modules, FAKE_MODULE, brain_module(lambda config: brain))
    env = {TESTING_ENV: "1", FAKE_LLM_ENV: "1"}
    assert build_llm(AppConfig(), None, env=env) is brain


@pytest.mark.parametrize(
    "env",
    [
        {TESTING_ENV: "1"},
        {FAKE_LLM_ENV: "1"},
        {TESTING_ENV: "0", FAKE_LLM_ENV: "1"},
        {TESTING_ENV: "1", FAKE_LLM_ENV: "0"},
        {TESTING_ENV: "true", FAKE_LLM_ENV: "true"},
        {TESTING_ENV: "", FAKE_LLM_ENV: ""},
        {},
    ],
)
def test_one_flag_alone_never_replaces_the_real_client(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    monkeypatch.setitem(sys.modules, FAKE_MODULE, brain_module(lambda config: FakeLLM()))
    assert isinstance(
        build_llm(AppConfig(), "sk-test-FICTIONAL0123456789abcdefghijkl", env=env), OpenAIClient
    )


def test_the_environment_defaults_to_the_live_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = FakeLLM()
    monkeypatch.setitem(sys.modules, FAKE_MODULE, brain_module(lambda config: brain))
    monkeypatch.delenv(TESTING_ENV, raising=False)
    monkeypatch.delenv(FAKE_LLM_ENV, raising=False)
    assert isinstance(build_llm(AppConfig(), None), OpenAIClient)
    monkeypatch.setenv(TESTING_ENV, "1")
    monkeypatch.setenv(FAKE_LLM_ENV, "1")
    assert build_llm(AppConfig(), None) is brain


def test_the_fake_brain_factory_may_take_the_config_or_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {TESTING_ENV: "1", FAKE_LLM_ENV: "1"}
    received: list[Any] = []
    brain = FakeLLM()

    def with_config(config: AppConfig) -> FakeLLM:
        received.append(config)
        return brain

    def without_config() -> FakeLLM:
        received.append("no-arg")
        return brain

    def optional_config(config: AppConfig | None = None) -> FakeLLM:
        received.append(config)
        return brain

    config = AppConfig()
    for factory, expected in (
        (with_config, config),
        (without_config, "no-arg"),
        (optional_config, config),
    ):
        monkeypatch.setitem(sys.modules, FAKE_MODULE, brain_module(factory))
        received.clear()
        assert build_llm(config, None, env=env) is brain
        assert received == [expected]


def test_a_missing_fake_brain_module_is_a_clear_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules, FAKE_MODULE, None
    )  # simulates "not importable" whatever the checkout has
    with pytest.raises(LLMCallError) as info:
        build_llm(AppConfig(), None, env={TESTING_ENV: "1", FAKE_LLM_ENV: "1"})
    assert info.value.kind == "fake_llm_missing"
    assert FAKE_MODULE in str(info.value)
    assert FAKE_LLM_ENV in str(info.value)


def test_a_fake_brain_module_that_fails_to_import_reports_the_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = importlib.import_module

    def flaky_import(name: str, package: str | None = None) -> Any:
        if name == FAKE_MODULE:
            raise ModuleNotFoundError("No module named 'reportlab'", name="reportlab")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", flaky_import)
    with pytest.raises(LLMCallError, match="reportlab") as info:
        build_llm(AppConfig(), None, env={TESTING_ENV: "1", FAKE_LLM_ENV: "1"})
    assert info.value.kind == "fake_llm_missing"


def test_a_fake_brain_module_without_the_factory_is_a_clear_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, FAKE_MODULE, types.ModuleType(FAKE_MODULE))
    with pytest.raises(LLMCallError, match="build_fake_brain"):
        build_llm(AppConfig(), None, env={TESTING_ENV: "1", FAKE_LLM_ENV: "1"})


def test_a_factory_that_returns_something_else_is_a_clear_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, FAKE_MODULE, brain_module(lambda config: object()))
    with pytest.raises(LLMCallError, match="did not return an LLMClient"):
        build_llm(AppConfig(), None, env={TESTING_ENV: "1", FAKE_LLM_ENV: "1"})
