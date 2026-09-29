"""``select_candidates``: who gets attempted, in what order."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from autoapply.models import ApplicationStatus, PendingQuestion, Reason, RunMode
from autoapply.pipeline import select_candidates

S, R = ApplicationStatus, Reason
DAY = timedelta(days=1)


def pick(world: Any, mode: RunMode = RunMode.FULL_AUTO) -> list[str]:
    ops = select_candidates(world.repo, world.config, mode, world.clock.now())
    return [op.company for op in ops]


def test_sorted_by_score_desc_then_oldest_first_seen(world: Any) -> None:
    early = datetime(2026, 9, 1, tzinfo=UTC)
    a = world.make_op("A", first_seen=early + DAY)
    b = world.make_op("B", first_seen=early)
    c = world.make_op("C", first_seen=early)
    d = world.make_op("D", first_seen=early)
    world.ops = [a, b, c, d]
    world.scores = {a.id: 90.0, b.id: 80.0, c.id: 80.0, d.id: 99.0}
    for op in world.ops:
        world.repo.upsert_opportunity(op)
        world.repo.set_score(op.id, world._score_of(op, world.config.search))
    order = pick(world)
    assert order[0] == "D" and order[1] == "A"
    assert sorted(order[2:]) == ["B", "C"]


def test_not_passed_closed_and_unscored_are_excluded(world: Any) -> None:
    low = world.seed(1, company="Low")[0]
    world.scores[low.id] = 10.0
    world.repo.set_score(low.id, world._score_of(low, world.config.search))
    world.seed(1, company="Closed", is_open=False)
    unscored = world.make_op("Unscored")
    world.repo.upsert_opportunity(unscored)
    world.seed(1, company="Good")
    assert pick(world) == ["Good"]


def test_submitted_manually_applied_and_same_fingerprint_are_excluded(world: Any) -> None:
    done, manual, twin, dup = (
        world.seed(1, company="Done")[0],
        world.seed(1, company="Manual")[0],
        None,
        None,
    )
    world.seed(1, company="Free")
    world.attempt(done, S.SUBMITTED)
    world.repo.mark_manually_applied(manual.id)
    # Same company/title/city under another URL = the same job.
    twin = world.make_op("Done", title=done.title, url="https://other.example.test/x")
    world.repo.upsert_opportunity(twin)
    world.repo.set_score(twin.id, world._score_of(twin, world.config.search))
    dup = twin
    assert dup.fingerprint == done.fingerprint
    assert pick(world) == ["Free"]


def test_dry_run_submission_does_not_exclude_a_job(world: Any) -> None:
    op = world.seed(1, company="Dry")[0]
    world.attempt(op, S.DRY_RUN_OK, mode=RunMode.DRY_RUN)
    assert pick(world, RunMode.FULL_AUTO) == ["Dry"]


def test_attempt_bound_and_zero_bound(world: Any) -> None:
    op = world.seed(1, company="Flaky")[0]
    world.config.apply.max_attempts_per_job = 2
    for _ in range(2):
        world.attempt(op, S.FAILED, R.TIMEOUT)
    world.clock.advance(DAY)
    assert pick(world) == []
    world.config.apply.max_attempts_per_job = 3
    assert pick(world) == ["Flaky"]
    world.config.apply.max_attempts_per_job = 0
    world.seed(1, company="Fresh")
    assert pick(world) == []


def test_retry_policy_is_applied_to_the_latest_attempt(world: Any) -> None:
    op = world.seed(1, company="Unsupported")[0]
    world.attempt(op, S.NEEDS_MANUAL, R.UNSUPPORTED_PORTAL)
    fail = world.seed(1, company="Timeout")[0]
    world.attempt(fail, S.FAILED, R.TIMEOUT)
    assert pick(world) == []
    world.clock.advance(timedelta(minutes=31))
    assert pick(world) == ["Timeout"]


def test_missing_answer_returns_once_resolved(world: Any) -> None:
    op = world.seed(1, company="Ask")[0]
    world.attempt(op, S.NEEDS_MANUAL, R.MISSING_ANSWER)
    question = world.repo.add_pending_question(
        PendingQuestion(question="Clearance?", opportunity_id=op.id)
    )
    assert pick(world) == []
    world.repo.resolve_pending_question(question.id, "None")
    assert pick(world) == ["Ask"]


def test_dry_run_skips_a_recent_dry_run_ok_for_seven_days(world: Any) -> None:
    op = world.seed(1, company="Dry")[0]
    world.attempt(op, S.DRY_RUN_OK, mode=RunMode.DRY_RUN)
    world.clock.advance(7 * DAY)
    assert pick(world, RunMode.DRY_RUN) == [], "exactly 7 days old still counts as within 7 days"
    world.clock.advance(timedelta(seconds=1))
    assert pick(world, RunMode.DRY_RUN) == ["Dry"]
    assert pick(world, RunMode.FULL_AUTO) == ["Dry"], "full_auto ignores dry-run successes"
