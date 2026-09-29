"""``run_once`` failure paths: every problem is recorded and the run moves on (or stops cleanly)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from autoapply.models import ApplicationStatus, ApplyResult, Reason

S, R = ApplicationStatus, Reason


def test_runner_exception_becomes_failed_internal_error_and_the_run_continues(world: Any) -> None:
    ops = world.add_ops(3)
    world.runner.script = {ops[1].id: RuntimeError("page exploded")}
    report = world.run()
    assert (report.attempted, report.submitted, report.failed) == (3, 2, 1)
    failed = world.repo.latest_application(ops[1].id)
    assert failed is not None
    assert (failed.status, failed.reason) == (S.FAILED, R.INTERNAL_ERROR)
    assert "RuntimeError: page exploded" in failed.message and "Traceback" not in failed.message
    assert failed.finished_at is not None
    assert any("page exploded" in e for e in report.errors)


def test_no_attempt_row_is_left_applying(world: Any) -> None:
    ops = world.add_ops(2)
    world.runner.script = {ops[0].id: RuntimeError("x"), ops[1].id: ValueError("y")}
    world.run()
    assert world.repo.count_applications(S.APPLYING) == 0


def test_technical_failures_are_retried_after_the_backoff_only(world: Any) -> None:
    ops = world.add_ops(1)
    world.runner.script = {ops[0].id: RuntimeError("boom")}
    world.run()
    assert world.run().stopped_reason == "no_candidates"
    world.clock.advance(timedelta(minutes=30))
    world.runner.script = {}
    assert world.run().submitted == 1


def test_bounded_retries(world: Any) -> None:
    ops = world.add_ops(1)
    world.runner.script = {ops[0].id: RuntimeError("boom")}
    for _ in range(5):
        world.run()
        world.clock.advance(timedelta(hours=1))
    assert world.repo.count_applications(opportunity_id=ops[0].id) == 3


def test_tailoring_failure_is_recorded_without_calling_the_runner(world: Any) -> None:
    (op,) = world.add_ops(1)
    world.tailor_error = ValueError("no resume and no knowledge base")
    report = world.run()
    assert (report.attempted, report.failed) == (1, 1) and world.runner.calls == []
    app = world.repo.latest_application(op.id)
    assert app is not None and (app.status, app.reason) == (S.FAILED, R.INTERNAL_ERROR)
    assert "tailoring failed: ValueError" in app.message


def test_a_runner_that_cannot_start_stops_the_run_without_burning_attempts(world: Any) -> None:
    world.add_ops(3)

    def no_browser(config: Any) -> Any:
        raise OSError("chromium missing")

    report = world.run(runner_factory=no_browser)
    assert report.stopped_reason == "error" and report.attempted == 0
    assert "could not start" in report.errors[0] and "chromium missing" in report.errors[0]
    assert world.repo.count_applications() == 0 and world.repo.get_run_lock() is None


def test_a_missing_runner_factory_is_a_clear_error(world: Any) -> None:
    world.add_ops(1)
    report = world.run(runner_factory=None)
    assert report.stopped_reason == "error" and "runner_factory" in report.errors[0]


def test_broken_llm_factory_and_kb_loader_degrade_gracefully(world: Any) -> None:
    world.add_ops(1)

    def bad_llm(config: Any) -> Any:
        raise RuntimeError("no key")

    def bad_kb(paths: Any) -> Any:
        raise ValueError("knowledge_base.json is corrupt")

    report = world.run(llm_factory=bad_llm, load_kb=bad_kb)
    assert report.submitted == 1 and len(report.errors) == 2
    assert world.tailor_calls[0][4] is None and world.tailor_calls[0][1].experiences == []


def test_runner_returning_garbage_or_applying_is_a_failure(world: Any) -> None:
    ops = world.add_ops(2)
    world.runner.script = {ops[0].id: None, ops[1].id: ApplyResult(status=S.APPLYING)}
    report = world.run()
    assert report.failed == 2
    assert {a.reason for a in world.repo.list_applications()} == {R.INTERNAL_ERROR}


def test_dry_run_reporting_a_submission_is_flagged(world: Any) -> None:
    world.add_ops(1)
    world.runner.default = ApplyResult(status=S.SUBMITTED)
    report = world.run(mode="dry_run")
    assert any("during a dry run" in e for e in report.errors)


def test_exception_text_is_redacted_and_truncated(world: Any) -> None:
    (op,) = world.add_ops(1)
    world.runner.script = {
        op.id: RuntimeError("bad header Bearer abcdef123456 sk-live-ABCDEFGH12345 " + "x" * 2000)
    }
    report = world.run()
    app = world.repo.latest_application(op.id)
    assert app is not None
    for text in (app.message, *report.errors):
        assert "abcdef123456" not in text and "sk-live-ABCDEFGH12345" not in text
        assert len(text) <= 400


def test_the_environment_key_is_redacted_even_when_not_key_shaped(world: Any) -> None:
    world.env["OPENAI_API_KEY"] = "plainsecretvalue99"
    (op,) = world.add_ops(1)
    world.runner.script = {op.id: RuntimeError("echoed plainsecretvalue99")}
    world.run()
    app = world.repo.latest_application(op.id)
    assert app is not None and "plainsecretvalue99" not in app.message


def test_keyboard_interrupt_cleans_up_and_propagates(world: Any) -> None:
    (op,) = world.add_ops(1)
    world.runner.script = {op.id: KeyboardInterrupt()}
    with pytest.raises(KeyboardInterrupt):
        world.run()
    app = world.repo.latest_application(op.id)
    assert app is not None and (app.status, app.reason) == (S.FAILED, R.INTERRUPTED)
    assert world.repo.get_run_lock() is None and world.runner.close_calls == 1
    run = world.repo.list_runs(1)[0]
    assert run.finished_at is not None and run.stopped_reason == "error"


def test_a_runner_whose_close_fails_does_not_break_the_report(world: Any) -> None:
    world.add_ops(1)
    world.runner.close = lambda: (_ for _ in ()).throw(OSError("already gone"))  # type: ignore[method-assign]
    report = world.run()
    assert report.submitted == 1 and any("closing the runner failed" in e for e in report.errors)
    assert world.repo.get_run_lock() is None


def test_finish_application_failure_is_recorded_not_raised(world: Any) -> None:
    (op,) = world.add_ops(1)
    world.repo.finish_application = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))  # type: ignore[method-assign]
    report = world.run()
    assert report.attempted == 1 and any("disk full" in e for e in report.errors)
    assert world.repo.latest_application(op.id).status is S.APPLYING  # type: ignore[union-attr]


def test_unexpected_pipeline_bug_is_an_error_report(world: Any) -> None:
    world.add_ops(1)
    world.repo.upsert_opportunities = lambda ops: (_ for _ in ()).throw(RuntimeError("db gone"))  # type: ignore[method-assign]
    world.repo.upsert_opportunity = lambda op: (_ for _ in ()).throw(RuntimeError("db gone"))  # type: ignore[method-assign]
    report = world.run()
    assert report.discovered == 1 and report.new == 0
    assert any("could not store" in e for e in report.errors)
