"""Application lifecycle: create-before-browser, finish, listing, never-apply-twice guard, stale recovery."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from autoapply.clock import FakeClock
from autoapply.db import NotFoundError, Repo
from autoapply.models import (
    ATS,
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    Reason,
    RunMode,
    TailoredDocs,
)

MakeOp = Callable[..., Opportunity]
MakeResult = Callable[..., ApplyResult]

S = ApplicationStatus


@pytest.fixture
def op(repo: Repo, make_op: MakeOp) -> Opportunity:
    return repo.upsert_opportunity(make_op(ats=ATS.WORKDAY))[0]


# ------------------------------------------------------------------------------------ create


def test_create_writes_an_applying_row_before_anything_else(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO, run_id=7)
    assert app.id is not None and app.id >= 1
    assert app.opportunity_id == op.id
    assert app.attempt_no == 1
    assert app.status == S.APPLYING
    assert app.mode == RunMode.FULL_AUTO
    assert app.run_id == 7
    # the opportunity's ATS is the best guess until the adapter reports
    assert app.ats == ATS.WORKDAY
    assert app.started_at == fake_clock.now()
    assert app.finished_at is None and app.submitted_at is None
    assert (app.reason, app.confirmation, app.message) == (None, None, "")
    assert (app.docs, app.artifacts, app.steps, app.filled_fields) == ({}, [], [], {})
    # durable immediately: a brand-new connection sees it
    other = Repo.open(repo.db.path)
    try:
        assert other.get_application(app.id) == app
    finally:
        other.db.close()


def test_attempt_numbers_increment_per_opportunity(repo: Repo, make_op: MakeOp) -> None:
    a, b = (repo.upsert_opportunity(make_op())[0] for _ in range(2))
    numbers = [repo.create_application(a.id, RunMode.DRY_RUN).attempt_no for _ in range(3)]
    assert numbers == [1, 2, 3]
    assert repo.create_application(b.id, RunMode.FULL_AUTO).attempt_no == 1
    # dry runs count as attempts
    assert repo.create_application(a.id, RunMode.FULL_AUTO).attempt_no == 4


def test_mode_may_be_given_as_a_string(repo: Repo, op: Opportunity) -> None:
    assert repo.create_application(op.id, "dry_run").mode == RunMode.DRY_RUN
    with pytest.raises(ValueError):
        repo.create_application(op.id, "yolo")


def test_create_for_an_unknown_opportunity_raises_and_writes_nothing(repo: Repo) -> None:
    with pytest.raises(NotFoundError, match="unknown opportunity"):
        repo.create_application("ghost", RunMode.FULL_AUTO)
    assert repo.count_applications() == 0


def test_get_and_latest_application(repo: Repo, op: Opportunity) -> None:
    assert repo.latest_application(op.id) is None
    assert repo.get_application(12345) is None
    first = repo.create_application(op.id, RunMode.FULL_AUTO)
    second = repo.create_application(op.id, RunMode.FULL_AUTO)
    assert repo.get_application(first.id) == first
    assert repo.latest_application(op.id) == second
    assert repo.latest_application("ghost") is None


# ------------------------------------------------------------------------------------ finish


def test_finish_submitted_records_everything_and_stamps_submitted_at(
    repo: Repo, op: Opportunity, make_result: MakeResult, fake_clock: FakeClock
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(minutes=4, seconds=12))
    result = make_result(
        S.SUBMITTED,
        message="Application received",
        ats=ATS.GREENHOUSE,
        confirmation="Thank you for applying (ref 8841)",
        filled_fields={"first_name": "Alex", "school": "The University of Texas at Austin"},
        artifacts=["artifacts/1/final.png"],
        steps=["opened form", "uploaded resume", "clicked submit"],
    )
    done = repo.finish_application(app.id, result)
    assert done.id == app.id and done.attempt_no == 1
    assert done.status == S.SUBMITTED
    assert done.message == "Application received"
    assert done.ats == ATS.GREENHOUSE
    assert done.confirmation == "Thank you for applying (ref 8841)"
    assert done.filled_fields == result.filled_fields
    assert done.artifacts == ["artifacts/1/final.png"]
    assert done.steps == ["opened form", "uploaded resume", "clicked submit"]
    assert done.started_at == app.started_at
    assert done.finished_at == done.submitted_at == fake_clock.now()
    assert repo.get_application(app.id) == done


def test_submitted_unconfirmed_also_sets_submitted_at(
    repo: Repo, op: Opportunity, make_result: MakeResult
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(app.id, make_result(S.SUBMITTED_UNCONFIRMED))
    assert done.status == S.SUBMITTED_UNCONFIRMED and done.submitted_at is not None


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (S.DRY_RUN_OK, None),
        (S.NEEDS_MANUAL, Reason.MISSING_ANSWER),
        (S.FAILED, Reason.TIMEOUT),
        (S.SKIPPED, Reason.POSTING_CLOSED),
    ],
)
def test_submitted_at_is_only_set_for_submitted_statuses(
    repo: Repo,
    op: Opportunity,
    make_result: MakeResult,
    status: ApplicationStatus,
    reason: Reason | None,
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(app.id, make_result(status, reason=reason))
    assert done.status == status and done.reason == reason
    assert done.submitted_at is None
    assert done.finished_at is not None


def test_finish_keeps_the_known_ats_when_the_result_does_not_know_it(
    repo: Repo, op: Opportunity, make_result: MakeResult
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(app.id, make_result(S.FAILED, reason=Reason.NETWORK_ERROR))
    assert done.ats == ATS.WORKDAY


def test_finish_stores_docs_from_tailored_docs_or_a_mapping_or_keeps_them(
    repo: Repo, op: Opportunity, make_result: MakeResult, tmp_path: Path
) -> None:
    docs = TailoredDocs(
        mode="tailored",
        resume_pdf=tmp_path / "documents" / "r.pdf",
        cover_letter_pdf=tmp_path / "documents" / "c.pdf",
        cover_letter_text="Dear team",
    )
    a1 = repo.create_application(op.id, RunMode.FULL_AUTO)
    d1 = repo.finish_application(a1.id, make_result(S.SUBMITTED), docs)
    assert d1.docs == {
        "mode": "tailored",
        "resume": str(tmp_path / "documents" / "r.pdf"),
        "cover_letter": str(tmp_path / "documents" / "c.pdf"),
    }
    a2 = repo.create_application(op.id, RunMode.FULL_AUTO)
    fallback = TailoredDocs(mode="fallback_uploaded_resume", resume_pdf=tmp_path / "resume.pdf")
    d2 = repo.finish_application(a2.id, make_result(S.NEEDS_MANUAL), fallback)
    assert d2.docs == {"mode": "fallback_uploaded_resume", "resume": str(tmp_path / "resume.pdf")}
    a3 = repo.create_application(op.id, RunMode.FULL_AUTO)
    d3 = repo.finish_application(
        a3.id, make_result(S.FAILED), {"resume": "x.pdf", "mode": "tailored"}
    )
    assert d3.docs == {"resume": "x.pdf", "mode": "tailored"}
    a4 = repo.create_application(op.id, RunMode.FULL_AUTO)
    d4 = repo.finish_application(a4.id, make_result(S.FAILED))
    assert d4.docs == {}  # nothing to keep
    again = repo.finish_application(a3.id, make_result(S.FAILED))
    assert again.docs == {"resume": "x.pdf", "mode": "tailored"}  # docs=None keeps what was stored


def test_finish_unknown_application_raises(repo: Repo, make_result: MakeResult) -> None:
    with pytest.raises(NotFoundError):
        repo.finish_application(999, make_result(S.FAILED))


def test_finish_rejects_a_non_terminal_status(
    repo: Repo, op: Opportunity, make_result: MakeResult
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    with pytest.raises(ValueError, match="terminal"):
        repo.finish_application(app.id, make_result(S.APPLYING))
    assert repo.get_application(app.id).status == S.APPLYING


def test_a_recorded_submission_can_never_be_overwritten_by_a_failure(
    repo: Repo, op: Opportunity, make_result: MakeResult
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(app.id, make_result(S.SUBMITTED, confirmation="ok"))
    with pytest.raises(ValueError, match="refusing to overwrite"):
        repo.finish_application(app.id, make_result(S.FAILED, reason=Reason.INTERNAL_ERROR))
    assert repo.get_application(app.id) == done
    assert repo.has_submitted(op.id)


def test_unconfirmed_can_be_upgraded_keeping_the_original_submitted_at(
    repo: Repo, op: Opportunity, make_result: MakeResult, fake_clock: FakeClock
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    first = repo.finish_application(app.id, make_result(S.SUBMITTED_UNCONFIRMED))
    fake_clock.advance(timedelta(hours=2))
    upgraded = repo.finish_application(app.id, make_result(S.SUBMITTED, confirmation="email seen"))
    assert upgraded.status == S.SUBMITTED
    assert upgraded.submitted_at == first.submitted_at
    assert upgraded.confirmation == "email seen"


def test_a_late_result_beats_the_interrupted_marker(
    repo: Repo, op: Opportunity, make_result: MakeResult, fake_clock: FakeClock
) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(hours=3))
    assert repo.recover_stale_applications(timedelta(hours=1)) == 1
    assert repo.get_application(app.id).reason == Reason.INTERRUPTED
    done = repo.finish_application(app.id, make_result(S.SUBMITTED))
    assert done.status == S.SUBMITTED and done.submitted_at is not None


def test_finished_application_json_survives_unicode_and_control_characters(
    repo: Repo, op: Opportunity, make_result: MakeResult
) -> None:
    nasty = 'line1\r\nline2\t\u0000 NUL, emoji 🚀, quote " backslash \\, ünï, 日本語'
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(
        app.id,
        make_result(
            S.NEEDS_MANUAL,
            reason=Reason.OTHER,
            steps=[nasty],
            filled_fields={nasty: nasty},
            artifacts=[nasty],
        ),
    )
    assert (
        done.steps == [nasty] and done.filled_fields == {nasty: nasty} and done.artifacts == [nasty]
    )


# ------------------------------------------------------------------------------------ listing


def _seed_history(repo: Repo, make_op: MakeOp, fake_clock: FakeClock) -> dict[str, int]:
    """Three opportunities with a mix of outcomes, one minute apart."""
    ids: dict[str, int] = {}
    plan = [
        ("sub", S.SUBMITTED, RunMode.FULL_AUTO),
        ("fail", S.FAILED, RunMode.FULL_AUTO),
        ("dry", S.DRY_RUN_OK, RunMode.DRY_RUN),
        ("man", S.NEEDS_MANUAL, RunMode.FULL_AUTO),
    ]
    for name, status, mode in plan:
        o = repo.upsert_opportunity(make_op())[0]
        app = repo.create_application(o.id, mode)
        repo.finish_application(app.id, ApplyResult(status=status))
        ids[name] = app.id
        ids[name + "_opp"] = o.id
        fake_clock.advance(timedelta(minutes=1))
    return ids


def test_list_applications_newest_first_and_filters(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    start = fake_clock.now()
    seeded = _seed_history(repo, make_op, fake_clock)
    every = repo.list_applications()
    assert [a.id for a in every] == [seeded["man"], seeded["dry"], seeded["fail"], seeded["sub"]]
    assert [a.id for a in repo.list_applications(status=S.SUBMITTED)] == [seeded["sub"]]
    assert [a.id for a in repo.list_applications(status="failed")] == [seeded["fail"]]
    both = repo.list_applications(status=[S.FAILED, "needs_manual"])
    assert [a.id for a in both] == [seeded["man"], seeded["fail"]]
    assert repo.list_applications(status=[]) == []
    assert [a.id for a in repo.list_applications(opportunity_id=seeded["dry_opp"])] == [
        seeded["dry"]
    ]
    # since is inclusive and applies to started_at
    assert len(repo.list_applications(since=start)) == 4
    assert [a.id for a in repo.list_applications(since=start + timedelta(minutes=2))] == [
        seeded["man"],
        seeded["dry"],
    ]
    assert repo.list_applications(since=start + timedelta(hours=1)) == []
    with pytest.raises(ValueError):
        repo.list_applications(status="nonsense")


def test_list_applications_paging_and_count(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    seeded = _seed_history(repo, make_op, fake_clock)
    assert [a.id for a in repo.list_applications(limit=2)] == [seeded["man"], seeded["dry"]]
    assert [a.id for a in repo.list_applications(limit=2, offset=2)] == [
        seeded["fail"],
        seeded["sub"],
    ]
    assert repo.list_applications(limit=2, offset=4) == []
    assert repo.count_applications() == 4
    assert repo.count_applications(status=[S.FAILED, S.SUBMITTED]) == 2
    assert repo.count_applications(opportunity_id=seeded["sub_opp"]) == 1
    with pytest.raises(ValueError):
        repo.list_applications(limit=-1)


def test_since_requires_an_aware_datetime(repo: Repo) -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.list_applications(since=datetime(2026, 9, 1))


def test_same_started_at_is_ordered_by_id_descending(repo: Repo, make_op: MakeOp) -> None:
    o = repo.upsert_opportunity(make_op())[0]
    ids = [
        repo.create_application(o.id, RunMode.FULL_AUTO).id for _ in range(3)
    ]  # clock never moves
    assert [a.id for a in repo.list_applications()] == ids[::-1]


# ------------------------------------------------------------------------------------ has_submitted


@pytest.mark.parametrize(
    ("status", "reason", "mode", "expected"),
    [
        (S.SUBMITTED, None, RunMode.FULL_AUTO, True),
        (S.SUBMITTED_UNCONFIRMED, None, RunMode.FULL_AUTO, True),
        (S.SUBMITTED, None, RunMode.DRY_RUN, False),  # a dry run never counts, whatever it says
        (S.DRY_RUN_OK, None, RunMode.DRY_RUN, False),
        (S.FAILED, Reason.TIMEOUT, RunMode.FULL_AUTO, False),
        (S.NEEDS_MANUAL, Reason.MISSING_ANSWER, RunMode.FULL_AUTO, False),
        (S.SKIPPED, Reason.POSTING_CLOSED, RunMode.FULL_AUTO, False),
        (S.SKIPPED, Reason.DUPLICATE, RunMode.FULL_AUTO, False),
        (
            S.SKIPPED,
            Reason.ALREADY_APPLIED,
            RunMode.FULL_AUTO,
            True,
        ),  # the site says we already did
        (S.APPLYING, None, RunMode.FULL_AUTO, False),
    ],
)
def test_has_submitted_only_for_real_applications(
    repo: Repo,
    make_op: MakeOp,
    status: ApplicationStatus,
    reason: Reason | None,
    mode: RunMode,
    expected: bool,
) -> None:
    o = repo.upsert_opportunity(make_op())[0]
    assert repo.has_submitted(o.id) is False
    app = repo.create_application(o.id, mode)
    if status != S.APPLYING:
        repo.finish_application(app.id, ApplyResult(status=status, reason=reason))
    assert repo.has_submitted(o.id) is expected


def test_has_submitted_ignores_a_failed_retry_after_a_success_and_other_jobs(
    repo: Repo, make_op: MakeOp
) -> None:
    done, other = (repo.upsert_opportunity(make_op())[0] for _ in range(2))
    app = repo.create_application(done.id, RunMode.FULL_AUTO)
    repo.finish_application(app.id, ApplyResult(status=S.SUBMITTED))
    repo.finish_application(
        repo.create_application(done.id, RunMode.DRY_RUN).id, ApplyResult(status=S.DRY_RUN_OK)
    )
    assert repo.has_submitted(done.id) is True
    assert repo.has_submitted(other.id) is False


def test_has_submitted_by_fingerprint_catches_the_same_role_from_another_source(
    repo: Repo, make_op: MakeOp
) -> None:
    applied = repo.upsert_opportunity(
        make_op(title="Strategy Intern", url="https://acme.example.test/direct")
    )[0]
    twin = repo.upsert_opportunity(
        make_op(title="Strategy Intern (Summer 2027)", url="https://linkedin.example.test/view/1")
    )[0]
    stranger = repo.upsert_opportunity(
        make_op(title="Something Else", url="https://acme.example.test/x")
    )[0]
    assert applied.fingerprint == twin.fingerprint != stranger.fingerprint
    repo.finish_application(
        repo.create_application(applied.id, RunMode.FULL_AUTO).id, ApplyResult(status=S.SUBMITTED)
    )
    assert repo.has_submitted(twin.id) is False  # by id alone the twin is still open ...
    # ... by fingerprint it is not
    assert repo.has_submitted(twin.id, fingerprint=twin.fingerprint) is True
    assert repo.has_submitted(stranger.id, fingerprint=stranger.fingerprint) is False
    assert repo.has_submitted(twin.id, fingerprint=None) is False


@pytest.mark.parametrize("blank", ["", "||", "  ", " | | "])
def test_a_blank_fingerprint_never_matches_anything(
    repo: Repo, make_op: MakeOp, blank: str
) -> None:
    submitted = repo.upsert_opportunity(
        make_op(company="", title="", location=None, url="https://blank.example.test/1")
    )[0]
    assert submitted.fingerprint == "||"  # the degenerate key a careless guard would match on
    other = repo.upsert_opportunity(make_op(company="", title="", location=None))[0]
    repo.finish_application(
        repo.create_application(submitted.id, RunMode.FULL_AUTO).id, ApplyResult(status=S.SUBMITTED)
    )
    assert repo.has_submitted(other.id, fingerprint=blank) is False


# ------------------------------------------------------------------------------------ manual marks


def test_mark_manually_applied_records_a_skipped_already_applied_attempt(
    repo: Repo, op: Opportunity, fake_clock: FakeClock, make_result: MakeResult
) -> None:
    failed = repo.create_application(op.id, RunMode.FULL_AUTO)
    repo.finish_application(failed.id, make_result(S.NEEDS_MANUAL, reason=Reason.MISSING_ANSWER))
    fake_clock.advance(timedelta(minutes=5))
    marked = repo.mark_manually_applied(op.id)
    assert marked.status == S.SKIPPED and marked.reason == Reason.ALREADY_APPLIED
    assert marked.attempt_no == 2
    assert "manually" in marked.message.lower()
    assert marked.submitted_at is None  # never counts toward the cap
    assert marked.started_at == marked.finished_at == fake_clock.now()
    assert repo.latest_application(op.id) == marked
    assert repo.has_submitted(op.id) is True


def test_mark_manually_applied_is_idempotent(repo: Repo, op: Opportunity) -> None:
    first = repo.mark_manually_applied(op.id)
    assert repo.mark_manually_applied(op.id) == first
    assert repo.count_applications(opportunity_id=op.id) == 1


def test_mark_manually_applied_unknown_opportunity(repo: Repo) -> None:
    with pytest.raises(NotFoundError):
        repo.mark_manually_applied("ghost")
    assert repo.count_applications() == 0


# ------------------------------------------------------------------------------------ stale recovery


def test_recover_stale_applications_fails_old_applying_rows_as_interrupted(
    repo: Repo, make_op: MakeOp, make_result: MakeResult, fake_clock: FakeClock
) -> None:
    stale, fresh, done = (repo.upsert_opportunity(make_op())[0] for _ in range(3))
    stale_app = repo.create_application(stale.id, RunMode.FULL_AUTO)
    done_app = repo.create_application(done.id, RunMode.FULL_AUTO)
    repo.finish_application(done_app.id, make_result(S.SUBMITTED))
    fake_clock.advance(timedelta(minutes=50))
    fresh_app = repo.create_application(fresh.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(minutes=20))  # stale is 70 min old, fresh 20 min
    assert repo.recover_stale_applications(timedelta(minutes=60)) == 1
    recovered = repo.get_application(stale_app.id)
    assert recovered is not None
    assert recovered.status == S.FAILED and recovered.reason == Reason.INTERRUPTED
    assert "interrupted" in recovered.message.lower() and "unknown" in recovered.message.lower()
    assert recovered.finished_at == fake_clock.now()
    assert recovered.submitted_at is None
    assert any("recovered" in step for step in recovered.steps)
    assert repo.get_application(fresh_app.id).status == S.APPLYING
    assert repo.get_application(done_app.id).status == S.SUBMITTED
    assert repo.recover_stale_applications(timedelta(minutes=60)) == 0  # idempotent


def test_recover_threshold_is_strict(repo: Repo, op: Opportunity, fake_clock: FakeClock) -> None:
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(minutes=30))
    assert repo.recover_stale_applications(timedelta(minutes=30)) == 0  # exactly at the threshold
    fake_clock.advance(timedelta(microseconds=1))
    assert repo.recover_stale_applications(timedelta(minutes=30)) == 1
    assert repo.get_application(app.id).status == S.FAILED


def test_recover_with_zero_threshold_takes_every_started_row(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    ops = [repo.upsert_opportunity(make_op())[0] for _ in range(3)]
    for o in ops:
        repo.create_application(o.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(seconds=1))
    assert repo.recover_stale_applications(timedelta(0)) == 3


def test_recover_rejects_a_negative_threshold(repo: Repo) -> None:
    with pytest.raises(ValueError):
        repo.recover_stale_applications(timedelta(seconds=-1))


def test_recovered_rows_are_not_counted_as_submissions(
    repo: Repo, op: Opportunity, fake_clock: FakeClock
) -> None:
    repo.create_application(op.id, RunMode.FULL_AUTO)
    fake_clock.advance(timedelta(hours=5))
    repo.recover_stale_applications(timedelta(hours=1))
    assert repo.has_submitted(op.id) is False
    assert repo.count_submitted_on(fake_clock.now().date(), "UTC") == 0


# ------------------------------------------------------------------------------------ referential integrity


def test_deleting_an_opportunity_cascades_to_its_applications(
    repo: Repo, make_op: MakeOp, make_result: MakeResult
) -> None:
    doomed, safe = (repo.upsert_opportunity(make_op())[0] for _ in range(2))
    for o in (doomed, doomed, safe):
        app = repo.create_application(o.id, RunMode.FULL_AUTO)
        repo.finish_application(app.id, make_result(S.FAILED))
    assert repo.count_applications() == 3
    with repo.transaction() as conn:
        conn.execute("DELETE FROM opportunities WHERE id = ?", (doomed.id,))
    assert repo.count_applications() == 1
    assert repo.count_applications(opportunity_id=doomed.id) == 0
    assert repo.count_applications(opportunity_id=safe.id) == 1


def test_foreign_keys_are_enforced_at_the_sql_level(repo: Repo) -> None:
    with pytest.raises(sqlite3.IntegrityError), repo.transaction() as conn:
        conn.execute(
            "INSERT INTO applications (opportunity_id, attempt_no, status, mode, started_at) "
            "VALUES ('ghost', 1, 'applying', 'full_auto', 't')"
        )


def test_attempt_number_is_unique_per_opportunity(repo: Repo, op: Opportunity) -> None:
    repo.create_application(op.id, RunMode.FULL_AUTO)
    with pytest.raises(sqlite3.IntegrityError), repo.transaction() as conn:
        conn.execute(
            "INSERT INTO applications (opportunity_id, attempt_no, status, mode, started_at) "
            "VALUES (?, 1, 'applying', 'full_auto', 't')",
            (op.id,),
        )


def test_failed_create_inside_a_grouped_transaction_leaves_no_partial_state(
    repo: Repo, make_op: MakeOp
) -> None:
    good = make_op()
    with pytest.raises(NotFoundError), repo.transaction():
        repo.upsert_opportunity(good)
        repo.create_application("ghost", RunMode.FULL_AUTO)
    assert repo.get_opportunity(good.id) is None  # the whole group rolled back
