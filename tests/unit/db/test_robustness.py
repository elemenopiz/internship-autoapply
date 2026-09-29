"""Hostile and oversized data: huge JSON blobs, thousands of rows, SQL-looking text, corrupt columns."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

import pytest

from autoapply.clock import FakeClock
from autoapply.db import Repo
from autoapply.models import (
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    PendingQuestion,
    QuestionKind,
    RunMode,
    RunReport,
    ScoreResult,
    ScreeningAnswer,
)

MakeOp = Callable[..., Opportunity]
MB = 1024 * 1024


# ------------------------------------------------------------------------------------ very large blobs


def test_very_large_opportunity_fields_round_trip(repo: Repo, make_op: MakeOp) -> None:
    big_text = ("Responsibilities: ünï 日本語 🚀 line\r\n" * 200_000).strip()  # ~8 MB
    extra = {
        "blob": "y" * (3 * MB),
        "many": {f"col_{i}": f"value {i}" for i in range(50_000)},
        "list": list(range(100_000)),
        "deep": {"a": {"b": {"c": [{"d": "e"} for _ in range(1_000)]}}},
    }
    op = make_op(description=big_text, extra=extra)
    stored, is_new = repo.upsert_opportunity(op)
    assert is_new
    assert stored.description == big_text
    assert stored.extra == extra
    again = repo.get_opportunity(op.id)
    assert again is not None and again.description == big_text and again.extra == extra


def test_merging_two_large_extras_keeps_both(repo: Repo, make_op: MakeOp) -> None:
    op = make_op(extra={f"a{i}": "v" * 100 for i in range(20_000)})
    repo.upsert_opportunity(op)
    merged, _ = repo.upsert_opportunity(
        op.model_copy(update={"extra": {f"b{i}": i for i in range(20_000)}})
    )
    assert len(merged.extra) == 40_000
    assert merged.extra["a19999"] == "v" * 100 and merged.extra["b19999"] == 19_999


def test_very_large_application_audit_trail_round_trips(repo: Repo, make_op: MakeOp) -> None:
    op = repo.upsert_opportunity(make_op())[0]
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    steps = [
        f"step {i}: filled field {i} with a moderately long description of what happened"
        for i in range(150_000)
    ]
    fields = {f"field_{i}": " ".join([f"value {i}"] * 5) for i in range(20_000)}
    artifacts = [f"artifacts/{app.id}/shot_{i}.png" for i in range(50_000)]
    done = repo.finish_application(
        app.id,
        ApplyResult(
            status=ApplicationStatus.SUBMITTED,
            steps=steps,
            filled_fields=fields,
            artifacts=artifacts,
            message="m" * MB,
        ),
        docs={f"doc{i}": "p" * 100 for i in range(5_000)},
    )
    assert done.steps == steps
    assert done.filled_fields == fields
    assert done.artifacts == artifacts
    assert len(done.message) == MB
    assert len(done.docs) == 5_000
    assert repo.get_application(app.id) == done


def test_very_large_score_run_report_and_pending_options_round_trip(
    repo: Repo, make_op: MakeOp
) -> None:
    op = repo.upsert_opportunity(make_op())[0]
    score = ScoreResult(
        score=71.5,
        passed=True,
        matched_keywords=[f"kw{i}" for i in range(20_000)],
        reasons=[f"reason number {i} explaining the score in words" for i in range(20_000)],
        penalties=[f"penalty {i}" for i in range(5_000)],
    )
    repo.set_score(op.id, score)
    assert repo.get_opportunity(op.id).score == score

    run_id = repo.start_run(RunMode.FULL_AUTO, "test")
    report = RunReport(errors=[f"error {i}: " + "e" * 200 for i in range(20_000)], submitted=1)
    assert repo.finish_run(run_id, report).errors == report.errors

    options = [f"Option {i} — ünï 日本語" for i in range(30_000)]
    queued = repo.add_pending_question(
        PendingQuestion(question="Pick one", kind=QuestionKind.SINGLE_CHOICE, options=options)
    )
    assert queued.options == options
    assert repo.list_pending_questions()[0].options == options


def test_a_huge_kv_value_and_answer_round_trip(repo: Repo) -> None:
    payload = "z" * (10 * MB)
    repo.set_kv("huge", payload)
    assert repo.get_kv("huge") == payload
    saved = repo.upsert_answer(ScreeningAnswer(question="Tell us everything", answer=payload))
    assert repo.find_answer(question_norm="tell us everything").answer == payload
    assert saved.answer == payload


# ------------------------------------------------------------------------------------ many rows


def test_thousands_of_opportunities_bulk_upsert_page_and_filter(
    repo: Repo, make_op: MakeOp, fake_clock: FakeClock
) -> None:
    total = 3_000
    ops = [make_op(title=f"Intern {i}", company=f"Company {i % 50}") for i in range(total)]
    started = time.monotonic()
    results = repo.upsert_opportunities(ops)
    repo.set_scores(
        [(o.id, ScoreResult(score=float(i % 100), passed=i % 100 >= 55)) for i, o in enumerate(ops)]
    )
    # a whole ingest is ONE transaction, not thousands of fsyncs
    assert time.monotonic() - started < 60
    assert all(is_new for _, is_new in results)
    assert repo.count_opportunities() == total

    seen: list[str] = []
    for offset in range(0, total, 250):
        seen += [o.id for o in repo.list_opportunities(limit=250, offset=offset)]
    # paging over many score ties: no gaps, no repeats
    assert len(seen) == total and len(set(seen)) == total
    top = repo.list_opportunities(limit=3)
    assert [o.score.score for o in top] == [99.0, 99.0, 99.0]
    assert repo.count_opportunities(passed_only=True) == sum(
        1 for i in range(total) if i % 100 >= 55
    )
    assert repo.count_opportunities(search="company 7") == len(
        [i for i in range(total) if f"company {i % 50}".startswith("company 7")]
    )
    assert len(repo.list_opportunities(search="Intern 2999")) == 1

    started = time.monotonic()
    again = repo.upsert_opportunities(ops)  # a re-ingest of the very same rows
    assert time.monotonic() - started < 60
    assert not any(is_new for _, is_new in again)
    assert repo.count_opportunities(passed_only=True) > 0  # scores untouched by the re-ingest


def test_many_applications_per_opportunity_number_correctly(repo: Repo, make_op: MakeOp) -> None:
    op = repo.upsert_opportunity(make_op())[0]
    numbers = [repo.create_application(op.id, RunMode.DRY_RUN).attempt_no for _ in range(300)]
    assert numbers == list(range(1, 301))
    assert repo.latest_application(op.id).attempt_no == 300


# ------------------------------------------------------------------------------------ hostile text


HOSTILE = [
    "Robert'); DROP TABLE opportunities;--",
    '"; DELETE FROM applications; --',
    "%_\\ wildcard soup",
    "null\x00byte",
    "tabs\tand\nnewlines\r\nand\x0bvertical\x0cform-feeds",
    "emoji 🚀👩‍💻 CJK 日本語 RTL עברית العربية combining é zero​width",
    "'" * 500,
    "{{7*7}} ${jndi:ldap://x} <script>alert(1)</script>",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_is_data_never_sql(repo: Repo, make_op: MakeOp, text: str) -> None:
    op = make_op(
        company=f"Acme {text}",
        title=f"PM {text}",
        location=text,
        description=text,
        extra={"k": text},
    )
    stored, _ = repo.upsert_opportunity(op)
    assert (stored.company, stored.title, stored.location, stored.description) == (
        op.company,
        op.title,
        op.location,
        op.description,
    )
    assert stored.extra == {"k": text}
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    done = repo.finish_application(
        app.id,
        ApplyResult(
            status=ApplicationStatus.FAILED, message=text, steps=[text], filled_fields={text: text}
        ),
    )
    assert done.message == text and done.steps == [text] and done.filled_fields == {text: text}
    repo.set_kv(text, text)
    assert repo.get_kv(text) == text
    # nothing was dropped or deleted
    assert repo.count_opportunities() == 1 and repo.count_applications() == 1
    assert repo.count_opportunities(search=text) >= 1  # searching for it is safe too


# ------------------------------------------------------------------------------------ corrupt columns


def test_corrupt_json_columns_do_not_take_the_readers_down(repo: Repo, make_op: MakeOp) -> None:
    op = repo.upsert_opportunity(make_op(extra={"a": 1}))[0]
    repo.set_score(op.id, ScoreResult(score=80, passed=True))
    app = repo.create_application(op.id, RunMode.FULL_AUTO)
    repo.finish_application(
        app.id, ApplyResult(status=ApplicationStatus.FAILED, steps=["s"], artifacts=["a"])
    )
    queued = repo.add_pending_question(PendingQuestion(question="Q?", options=["x"]))
    run_id = repo.start_run(RunMode.FULL_AUTO, "test")
    repo.finish_run(run_id, RunReport(submitted=3))
    with repo.transaction() as conn:
        conn.execute("UPDATE opportunities SET extra = '{not json', score_detail = 'garbage'")
        conn.execute(
            "UPDATE applications SET steps = '[1,', filled_fields = 'nope', artifacts = '\"str\"', docs = '[]'"
        )
        conn.execute("UPDATE pending_questions SET options = 'oops' WHERE id = ?", (queued.id,))
        conn.execute("UPDATE runs SET report = '<<<'")
    broken = repo.get_opportunity(op.id)
    assert broken is not None and broken.extra == {} and broken.score is None
    assert len(repo.list_opportunities()) == 1
    row = repo.get_application(app.id)
    assert row is not None and (row.steps, row.filled_fields, row.artifacts, row.docs) == (
        [],
        {},
        [],
        {},
    )
    assert repo.list_pending_questions()[0].options == []
    [report] = repo.list_runs()
    # unreadable report -> defaults, row identity kept
    assert report.run_id == run_id and report.submitted == 0
    repo.stats()  # still computable


def test_a_garbled_timestamp_reads_as_unknown_instead_of_breaking_the_listing(
    repo: Repo, make_op: MakeOp, caplog: pytest.LogCaptureFixture
) -> None:
    op = repo.upsert_opportunity(make_op())[0]
    repo.upsert_ats_account("boards.greenhouse.io", "alex.rivera@example.test")
    with repo.transaction() as conn:
        conn.execute("UPDATE opportunities SET first_seen = 'yesterday-ish'")
        conn.execute("UPDATE ats_accounts SET created_at = 'sometime'")
    with caplog.at_level("WARNING", logger="autoapply.db"):
        stored = repo.get_opportunity(op.id)
    assert stored is not None and stored.first_seen is None and stored.last_seen is not None
    assert "unparseable timestamp" in caplog.text
    assert len(repo.list_opportunities()) == 1
    account = repo.get_ats_account("boards.greenhouse.io", "alex.rivera@example.test")
    assert account is not None and account.created_at.year == 1970


def test_a_run_lock_with_an_unreadable_expiry_cannot_wedge_the_application(repo: Repo) -> None:
    repo.acquire_run_lock("crashed", 3600)
    with repo.transaction() as conn:
        conn.execute("UPDATE run_lock SET expires_at = 'garbage'")
    info = repo.get_run_lock()
    assert info is not None and info.expired is True and info.owner == "crashed"
    assert repo.acquire_run_lock("heir", 60) is True  # treated as expired, so it can be taken over
    assert repo.get_run_lock().owner == "heir"


def test_pending_question_timestamps_use_the_injected_clock(
    repo: Repo, fake_clock: FakeClock
) -> None:
    first = repo.add_pending_question(PendingQuestion(question="First question?"))
    fake_clock.advance(timedelta(days=3))
    second = repo.add_pending_question(PendingQuestion(question="Second question?"))
    assert second.created_at - first.created_at == timedelta(days=3)
