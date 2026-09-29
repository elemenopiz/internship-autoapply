"""``is_retry_eligible``: the SPEC 5.10 retry table, boundary by boundary."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from autoapply.models import (
    Application,
    ApplicationStatus,
    PendingQuestion,
    Reason,
    RunMode,
    ScreeningAnswer,
)
from autoapply.pipeline import attempts_used, is_retry_eligible

S, R = ApplicationStatus, Reason
MIN, H = timedelta(minutes=1), timedelta(hours=1)


def app_at(
    world: Any, status: S, reason: R | None, age: timedelta, mode: RunMode = RunMode.FULL_AUTO
) -> Application:
    now = world.clock.now()
    return Application(
        id=1, opportunity_id="opp-1", status=status, reason=reason, mode=mode,
        started_at=now - age - MIN, finished_at=now - age,
    )  # fmt: skip


def eligible(world: Any, application: Application, **kw: Any) -> bool:
    return is_retry_eligible(application, world.clock.now(), world.config, world.repo, **kw)


# (status, reason, age, expected)
TIME_TABLE = [
    (S.FAILED, R.TIMEOUT, 30 * MIN, True),
    (S.FAILED, R.TIMEOUT, 30 * MIN - timedelta(seconds=1), False),
    (S.FAILED, R.NETWORK_ERROR, 31 * MIN, True),
    (S.FAILED, R.INTERNAL_ERROR, 5 * MIN, False),
    (S.FAILED, R.INTERNAL_ERROR, 2 * H, True),
    (S.FAILED, R.UNEXPECTED_FLOW, 40 * MIN, True),
    (S.FAILED, None, 40 * MIN, True),
    (S.FAILED, None, 1 * MIN, False),
    (S.NEEDS_MANUAL, R.BOT_CHECK, 24 * H, True),
    (S.NEEDS_MANUAL, R.BOT_CHECK, 24 * H - timedelta(seconds=1), False),
    (S.FAILED, R.BOT_CHECK, 25 * H, True),
    (S.NEEDS_MANUAL, R.EMAIL_VERIFICATION, 1 * H, True),
    (S.NEEDS_MANUAL, R.EMAIL_VERIFICATION, 1 * H - timedelta(seconds=1), False),
]


@pytest.mark.parametrize(("status", "reason", "age", "expected"), TIME_TABLE)
def test_time_based_rules(
    world: Any, status: S, reason: R | None, age: timedelta, expected: bool
) -> None:
    world.config.apply.email.enabled = True
    assert eligible(world, app_at(world, status, reason, age)) is expected


def test_email_verification_needs_a_configured_verifier(world: Any) -> None:
    application = app_at(world, S.NEEDS_MANUAL, R.EMAIL_VERIFICATION, 5 * H)
    world.config.apply.email.enabled = False
    assert not eligible(world, application)
    world.config.apply.email.enabled = True
    assert eligible(world, application)


def test_attestation_retry_follows_the_authorisation_flag(world: Any) -> None:
    application = app_at(world, S.NEEDS_MANUAL, R.ATTESTATION_NOT_AUTHORIZED, timedelta(0))
    world.config.apply.attestations_authorized = False
    assert not eligible(world, application)
    world.config.apply.attestations_authorized = True
    assert eligible(world, application)


@pytest.mark.parametrize(
    "reason",
    [
        R.UNSUPPORTED_PORTAL, R.LOGIN_REQUIRED, R.ACCOUNT_PROBLEM, R.DOCUMENT_REJECTED,
        R.VALIDATION_ERROR, R.POSTING_CLOSED, R.ALREADY_APPLIED, R.INELIGIBLE, R.DUPLICATE,
        R.INTERRUPTED, R.OTHER,
    ],
)  # fmt: skip
@pytest.mark.parametrize("status", [S.NEEDS_MANUAL, S.FAILED, S.SKIPPED])
def test_everything_else_is_never_retried(world: Any, status: S, reason: R) -> None:
    assert not eligible(world, app_at(world, status, reason, 400 * H))


@pytest.mark.parametrize(
    "reason", [R.TIMEOUT, R.NETWORK_ERROR, R.INTERNAL_ERROR, R.UNEXPECTED_FLOW]
)
def test_technical_reasons_only_retry_when_the_status_is_failed(world: Any, reason: R) -> None:
    assert not eligible(world, app_at(world, S.NEEDS_MANUAL, reason, 400 * H))


@pytest.mark.parametrize("status", [S.APPLYING, S.SUBMITTED, S.SUBMITTED_UNCONFIRMED, S.SKIPPED])
def test_in_flight_submitted_and_skipped_are_never_retried(world: Any, status: S) -> None:
    assert not eligible(world, app_at(world, status, None, 400 * H))


def test_a_dry_run_success_never_blocks_the_real_attempt(world: Any) -> None:
    assert eligible(
        world, app_at(world, S.DRY_RUN_OK, None, timedelta(0), RunMode.DRY_RUN), dry_run=False
    )


def test_missing_answer_waits_for_every_pending_question(world: Any) -> None:
    op = world.seed(1)[0]
    application = Application(id=1, opportunity_id=op.id, status=S.NEEDS_MANUAL, reason=R.MISSING_ANSWER,
                              started_at=world.clock.now(), finished_at=world.clock.now())  # fmt: skip
    assert not eligible(world, application), "no pending question recorded: nothing to resolve"
    first = world.repo.add_pending_question(
        PendingQuestion(question="Do you hold a clearance?", opportunity_id=op.id)
    )
    second = world.repo.add_pending_question(
        PendingQuestion(question="Expected salary?", opportunity_id=op.id)
    )
    assert not eligible(world, application)
    world.repo.resolve_pending_question(first.id, "No")
    assert not eligible(world, application), "one question is still open"
    world.repo.resolve_pending_question(second.id, "Negotiable")
    assert eligible(world, application)


def test_missing_answer_is_also_cleared_by_a_saved_answer_for_the_same_question(world: Any) -> None:
    op = world.seed(1)[0]
    world.repo.add_pending_question(
        PendingQuestion(question="Do you hold a clearance?", opportunity_id=op.id)
    )
    application = Application(id=1, opportunity_id=op.id, status=S.NEEDS_MANUAL, reason=R.MISSING_ANSWER,
                              started_at=world.clock.now())  # fmt: skip
    assert not eligible(world, application)
    world.repo.upsert_answer(ScreeningAnswer(question="do you hold a clearance", answer="No"))
    assert eligible(world, application)


def test_attempt_bound_counts_only_attempts_of_the_same_kind(world: Any) -> None:
    op = world.seed(1)[0]
    world.config.apply.max_attempts_per_job = 2
    for _ in range(2):
        world.attempt(op, S.FAILED, R.TIMEOUT, mode=RunMode.DRY_RUN)
    assert attempts_used(world.repo, op.id, dry_run=True) == 2
    assert attempts_used(world.repo, op.id, dry_run=False) == 0
    world.clock.advance(2 * H)
    latest = world.repo.latest_application(op.id)
    assert latest is not None
    assert eligible(world, latest, dry_run=False)
    assert not eligible(world, latest, dry_run=True)
    assert not eligible(world, latest), (
        "default kind = the kind of the application itself (dry run)"
    )
    world.attempt(op, S.FAILED, R.TIMEOUT)
    world.attempt(op, S.FAILED, R.TIMEOUT)
    world.clock.advance(2 * H)
    latest = world.repo.latest_application(op.id)
    assert latest is not None
    assert not eligible(world, latest, dry_run=False)


def test_naive_timestamps_are_read_as_utc(world: Any) -> None:
    naive = world.clock.now().replace(tzinfo=None) - 2 * H
    application = Application(
        id=1, opportunity_id="x", status=S.FAILED, reason=R.TIMEOUT, finished_at=naive
    )
    assert eligible(world, application)


def test_an_attempt_without_timestamps_is_not_retried_on_time_rules(world: Any) -> None:
    application = Application(id=1, opportunity_id="x", status=S.FAILED, reason=R.TIMEOUT)
    assert not eligible(world, application)
