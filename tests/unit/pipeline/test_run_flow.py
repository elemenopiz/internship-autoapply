"""``run_once`` happy paths, modes, counters and recorded data."""

from __future__ import annotations

from typing import Any

import pytest

from autoapply.llm import BudgetedLLM, FakeLLM
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    KnowledgeBase,
    PendingQuestion,
    QuestionKind,
    Reason,
    RunMode,
)

S, R = ApplicationStatus, Reason


def test_full_auto_happy_path(world: Any) -> None:
    ops = world.add_ops(3)
    report = world.run()
    assert (report.discovered, report.new, report.eligible) == (3, 3, 3)
    assert (report.attempted, report.submitted, report.failed) == (3, 3, 0)
    assert report.stopped_reason is None and report.errors == []
    assert report.mode is RunMode.FULL_AUTO and report.trigger == "test"
    assert report.cap_remaining == 2 and report.run_id is not None
    assert report.started_at is not None and report.finished_at is not None
    assert [op.id for op, _, _ in world.runner.calls].count(ops[0].id) == 1
    assert all(dry is False for _, _, dry in world.runner.calls)
    assert world.repo.count_applications(S.SUBMITTED) == 3
    assert world.repo.get_run_lock() is None, "the lock is released"
    stored = world.repo.get_run(report.run_id)
    assert stored is not None and stored.submitted == 3 and stored.finished_at is not None
    assert world.runner.close_calls == 1 and world.runner_builds == 1


def test_scores_are_persisted_and_new_vs_known_counts(world: Any) -> None:
    world.add_ops(2)
    world.run()
    assert all(o.score is not None for o in world.repo.list_opportunities())
    world.add_ops(1)
    second = world.run()
    assert (second.discovered, second.new) == (3, 1)


def test_the_documents_and_their_mode_are_recorded(world: Any) -> None:
    world.add_ops(1)
    world.run()
    app = world.repo.list_applications()[0]
    assert app.docs["mode"] == "tailored" and app.docs["resume"].endswith("resume.pdf")
    assert "cover_letter" in app.docs and app.run_id is not None
    world.tailor_mode = "fallback_uploaded_resume"
    world.add_ops(1)
    world.run()
    fallback = [a for a in world.repo.list_applications() if a.docs["mode"] != "tailored"]
    assert len(fallback) == 1 and "cover_letter" not in fallback[0].docs


def test_tailor_receives_kb_profile_resume_and_a_budgeted_llm(world: Any) -> None:
    world.add_ops(2)
    llm = FakeLLM()
    kb = KnowledgeBase()
    world.config.llm.max_calls_per_application = 4
    world.run(llm_factory=lambda cfg: llm, load_kb=lambda paths: kb)
    (op, got_kb, profile, paths, budgeted, resume), second = world.tailor_calls
    assert got_kb is kb and profile == world.config.profile and paths == world.paths
    assert resume == world.paths.resume_file
    assert isinstance(budgeted, BudgetedLLM) and budgeted.max_calls == 4
    assert second[4] is not budgeted, "every application gets a fresh budget"


def test_kb_and_llm_are_built_once_per_run(world: Any) -> None:
    world.add_ops(3)
    built: list[str] = []
    world.run(
        llm_factory=lambda cfg: built.append("llm"),
        load_kb=lambda paths: built.append("kb") or KnowledgeBase(),
    )
    assert built == ["kb", "llm"] or sorted(built) == ["kb", "llm"]


def test_no_llm_is_passed_as_none(world: Any) -> None:
    world.add_ops(1)
    world.run()
    assert world.tailor_calls[0][4] is None


def test_dry_run_mode_records_dry_run_rows_and_passes_the_flag(world: Any) -> None:
    world.add_ops(2)
    world.runner.default = ApplyResult(status=S.DRY_RUN_OK)
    report = world.run(mode=RunMode.DRY_RUN)
    assert (report.attempted, report.dry_run_ok, report.submitted) == (2, 2, 0)
    assert all(dry for _, _, dry in world.runner.calls)
    assert {a.mode for a in world.repo.list_applications()} == {RunMode.DRY_RUN}
    assert report.mode is RunMode.DRY_RUN


def test_the_argument_mode_overrides_the_config_mode(world: Any) -> None:
    world.config.mode = RunMode.DRY_RUN
    world.add_ops(1)
    world.runner.default = ApplyResult(status=S.DRY_RUN_OK)
    assert world.run().mode is RunMode.DRY_RUN
    assert world.run(mode=RunMode.DISCOVER_ONLY).mode is RunMode.DISCOVER_ONLY
    assert world.run(mode="dry_run").mode is RunMode.DRY_RUN  # type: ignore[arg-type]


def test_discover_only_never_builds_a_runner_or_reads_readiness(world: Any) -> None:
    world.add_ops(3)
    world.config.profile.first_name = ""  # would fail the readiness gate
    world.env.clear()
    world.paths.resume_file.unlink()
    report = world.run(mode=RunMode.DISCOVER_ONLY)
    assert report.stopped_reason is None and report.errors == []
    assert (report.discovered, report.new, report.eligible, report.attempted) == (3, 3, 3, 0)
    assert world.runner_builds == 0 and world.tailor_calls == []
    assert world.repo.count_applications() == 0
    assert len(world.repo.list_opportunities(passed_only=True)) == 3


@pytest.mark.parametrize(
    ("status", "reason", "counter"),
    [
        (S.SUBMITTED, None, "submitted"),
        (S.SUBMITTED_UNCONFIRMED, None, "submitted"),
        (S.DRY_RUN_OK, None, "dry_run_ok"),
        (S.NEEDS_MANUAL, R.UNSUPPORTED_PORTAL, "needs_manual"),
        (S.FAILED, R.TIMEOUT, "failed"),
        (S.SKIPPED, R.POSTING_CLOSED, "skipped"),
    ],
)
def test_every_runner_status_maps_to_one_counter_and_is_stored(
    world: Any, status: S, reason: R | None, counter: str
) -> None:
    world.add_ops(1)
    world.runner.default = ApplyResult(
        status=status, reason=reason, message="why", confirmation="C-1" if reason is None else None,
        steps=["did a thing"], artifacts=["artifacts/1/shot.png"], filled_fields={"email": "x"},
    )  # fmt: skip
    report = world.run()
    counts = {
        k: getattr(report, k)
        for k in ("submitted", "dry_run_ok", "needs_manual", "failed", "skipped")
    }
    assert counts == {k: int(k == counter) for k in counts}
    assert report.attempted == 1
    app = world.repo.list_applications()[0]
    assert (app.status, app.reason, app.message) == (status, reason, "why")
    assert app.steps == ["did a thing"] and app.artifacts == ["artifacts/1/shot.png"]


def test_pending_questions_are_queued_with_opportunity_and_company(world: Any) -> None:
    op = world.add_ops(1, company="Asker")[0]
    question = PendingQuestion(
        question="Do you have a security clearance?", kind=QuestionKind.BOOLEAN
    )
    world.runner.default = ApplyResult(
        status=S.NEEDS_MANUAL, reason=R.MISSING_ANSWER, pending_questions=[question, question]
    )
    world.run()
    queued = world.repo.list_pending_questions()
    assert len(queued) == 1, "deduplicated by the repo"
    assert (queued[0].opportunity_id, queued[0].company) == (op.id, "Asker")


def test_missing_answer_is_retried_after_the_user_answers(world: Any) -> None:
    world.add_ops(1)
    world.runner.default = ApplyResult(
        status=S.NEEDS_MANUAL,
        reason=R.MISSING_ANSWER,
        pending_questions=[PendingQuestion(question="Clearance?")],
    )
    assert world.run().needs_manual == 1
    assert world.run().stopped_reason == "no_candidates"
    world.repo.resolve_pending_question(world.repo.list_pending_questions()[0].id, "No")
    world.runner.default = ApplyResult(status=S.SUBMITTED)
    report = world.run()
    assert report.submitted == 1
    assert [a.attempt_no for a in world.repo.list_applications()] == [2, 1]


def test_no_candidates_and_empty_ingest(world: Any) -> None:
    report = world.run()
    assert report.stopped_reason == "no_candidates" and report.attempted == 0
    assert world.runner_builds == 0


def test_ingest_errors_are_reported_and_the_run_goes_on(world: Any) -> None:
    world.add_ops(1)
    world.ingest_errors = ["boards: TimeoutError: slow"]
    report = world.run()
    assert report.errors == ["boards: TimeoutError: slow"] and report.submitted == 1


def test_a_plain_list_from_ingest_is_accepted(world: Any) -> None:
    ops = world.add_ops(2)
    assert world.run(ingest=lambda ctx: list(ops)).submitted == 2


def test_ingest_crash_still_applies_to_known_opportunities(world: Any) -> None:
    world.seed(2)

    def boom(ctx: Any) -> Any:
        raise ConnectionError("no network")

    report = world.run(ingest=boom)
    assert report.submitted == 2 and report.discovered == 0
    assert report.errors == ["ingest failed: ConnectionError: no network"]


def test_scoring_failure_stops_before_any_attempt(world: Any) -> None:
    world.seed(2)

    def boom(ops: Any, search: Any, profile: Any) -> Any:
        raise ValueError("bad scorer")

    report = world.run(score=boom)
    assert report.stopped_reason == "error" and report.attempted == 0
    assert "scoring failed: ValueError: bad scorer" in report.errors[0]


def test_profile_edits_apply_to_known_postings_through_rescoring(world: Any) -> None:
    (op,) = world.seed(1, company="Denied")
    world.scores[op.id] = 10.0  # e.g. the user added the company to the denylist
    report = world.run(ingest=lambda ctx: [])
    assert report.stopped_reason == "no_candidates"


def test_config_is_reread_at_the_start_of_every_run(world: Any) -> None:
    world.add_ops(4)
    world.config.daily_cap = 1
    assert world.run().submitted == 1
    world.config.daily_cap = 3
    assert world.run().submitted == 2
    assert world.config_loads == 2


def test_unknown_trigger_is_recorded_as_manual(world: Any) -> None:
    assert world.run(trigger="dashboard").trigger == "manual"
    assert world.run(trigger="cli").trigger == "cli"


def test_pacing_sleeps_between_attempts_only(world: Any) -> None:
    world.config.apply.min_delay_s, world.config.apply.max_delay_s = 20, 90
    world.add_ops(4)
    report = world.run()
    assert report.attempted == 4 and len(world.sleeps) == 3
    assert all(20 <= s <= 90 for s in world.sleeps)
    assert len(set(world.sleeps)) > 1, "delays are drawn from the rng, not constant"


def test_no_pause_for_a_single_attempt_and_swapped_delays_still_work(world: Any) -> None:
    world.add_ops(1)
    world.run()
    assert world.sleeps == []
    world.config.apply.min_delay_s, world.config.apply.max_delay_s = 60, 10
    world.add_ops(2)
    world.run()
    assert len(world.sleeps) == 1 and 10 <= world.sleeps[0] <= 60


def test_default_pause_is_interruptible_by_the_stop_flag(world: Any) -> None:
    deps = world.deps(sleep=None)
    deps.stop_flag.set()
    deps.pause(30)  # returns immediately instead of sleeping 30 s


def test_the_work_is_ordered_best_score_first(world: Any) -> None:
    ops = world.add_ops(3)
    world.scores = {ops[0].id: 60.0, ops[1].id: 90.0, ops[2].id: 75.0}
    world.run()
    assert world.runner.names == [ops[1].company, ops[2].company, ops[0].company]


def test_the_data_dir_may_contain_spaces_and_unicode(world: Any) -> None:
    assert "ü" in str(world.paths.root) and " " in str(world.paths.root)
    world.add_ops(1)
    assert world.run().submitted == 1
