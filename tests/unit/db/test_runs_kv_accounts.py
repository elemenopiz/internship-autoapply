"""Run history, the kv store, and ATS account metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autoapply.clock import FakeClock
from autoapply.db import NotFoundError, Repo
from autoapply.models import RunMode, RunReport

# ================================================================================== runs


def test_start_run_opens_an_unfinished_record(repo: Repo, fake_clock: FakeClock) -> None:
    run_id = repo.start_run(RunMode.FULL_AUTO, "manual")
    assert isinstance(run_id, int) and run_id >= 1
    [report] = repo.list_runs()
    assert report.run_id == run_id
    assert (report.mode, report.trigger) == (RunMode.FULL_AUTO, "manual")
    assert report.started_at == fake_clock.now()
    assert report.finished_at is None
    assert (report.discovered, report.submitted, report.errors) == (0, 0, [])


def test_finish_run_stores_the_whole_report(repo: Repo, fake_clock: FakeClock) -> None:
    run_id = repo.start_run("dry_run", "cli")
    fake_clock.advance(timedelta(minutes=12))
    stored = repo.finish_run(
        run_id,
        RunReport(
            discovered=40,
            new=12,
            eligible=9,
            attempted=5,
            submitted=3,
            dry_run_ok=0,
            needs_manual=1,
            failed=1,
            skipped=0,
            cap_remaining=2,
            stopped_reason="cap_reached",
            errors=["one thing went wrong: ünï"],
        ),
    )
    [listed] = repo.list_runs()
    assert listed == stored == repo.get_run(run_id)
    assert (stored.discovered, stored.new, stored.eligible, stored.attempted, stored.submitted) == (
        40,
        12,
        9,
        5,
        3,
    )
    assert (stored.needs_manual, stored.failed, stored.cap_remaining) == (1, 1, 2)
    assert stored.stopped_reason == "cap_reached"
    assert stored.errors == ["one thing went wrong: ünï"]
    assert stored.finished_at == fake_clock.now()


def test_the_run_rows_own_identity_wins_over_the_report(repo: Repo, fake_clock: FakeClock) -> None:
    run_id = repo.start_run(RunMode.DISCOVER_ONLY, "schedule")
    liar = RunReport(
        run_id=999,
        mode=RunMode.FULL_AUTO,
        trigger="test",
        started_at=datetime(2001, 1, 1, tzinfo=UTC),
        submitted=2,
    )
    stored = repo.finish_run(run_id, liar)
    assert stored.run_id == run_id
    assert (stored.mode, stored.trigger) == (RunMode.DISCOVER_ONLY, "schedule")
    assert stored.started_at == fake_clock.now()
    assert stored.submitted == 2  # counters come from the report


def test_finish_run_honours_an_explicit_finished_at(repo: Repo) -> None:
    run_id = repo.start_run("full_auto", "test")
    when = datetime(2026, 9, 29, 16, 45, tzinfo=UTC)
    assert repo.finish_run(run_id, RunReport(finished_at=when)).finished_at == when


def test_list_runs_is_newest_first_with_a_limit(repo: Repo, fake_clock: FakeClock) -> None:
    ids = []
    for _ in range(5):
        ids.append(repo.start_run("full_auto", "schedule"))
        fake_clock.advance(timedelta(hours=1))
    assert [r.run_id for r in repo.list_runs()] == ids[::-1]
    assert [r.run_id for r in repo.list_runs(2)] == ids[::-1][:2]
    assert [r.run_id for r in repo.list_runs(limit=1)] == [ids[-1]]
    assert repo.list_runs(limit=0) == []
    assert len(repo.list_runs(limit=None)) == 5
    with pytest.raises(ValueError):
        repo.list_runs(limit=-1)


def test_an_unfinished_run_sits_beside_finished_ones(repo: Repo) -> None:
    done = repo.start_run("full_auto", "manual")
    repo.finish_run(done, RunReport(submitted=1))
    open_run = repo.start_run("dry_run", "manual")
    reports = {r.run_id: r for r in repo.list_runs()}
    assert reports[done].finished_at is not None and reports[done].submitted == 1
    assert reports[open_run].finished_at is None


def test_start_run_rejects_bad_input_before_writing(repo: Repo) -> None:
    with pytest.raises(ValueError):
        repo.start_run("full_auto", "carrier-pigeon")
    with pytest.raises(ValueError):
        repo.start_run("hyperdrive", "manual")
    assert repo.list_runs() == []


def test_finish_or_get_unknown_run(repo: Repo) -> None:
    with pytest.raises(NotFoundError):
        repo.finish_run(404, RunReport())
    assert repo.get_run(404) is None


def test_finishing_a_run_twice_keeps_the_latest_report(repo: Repo) -> None:
    run_id = repo.start_run("full_auto", "cli")
    repo.finish_run(run_id, RunReport(submitted=1))
    assert repo.finish_run(run_id, RunReport(submitted=2)).submitted == 2


# ================================================================================== kv


def test_kv_get_set_overwrite_delete(repo: Repo) -> None:
    assert repo.get_kv("last_run") is None
    assert repo.get_kv("last_run", "never") == "never"
    repo.set_kv("last_run", "2026-09-29")
    assert repo.get_kv("last_run") == "2026-09-29"
    assert repo.get_kv("last_run", "never") == "2026-09-29"
    repo.set_kv("last_run", "2026-09-30")
    assert repo.get_kv("last_run") == "2026-09-30"
    assert repo.delete_kv("last_run") is True
    assert repo.delete_kv("last_run") is False
    assert repo.get_kv("last_run") is None


def test_kv_keeps_the_empty_string_distinct_from_missing(repo: Repo) -> None:
    repo.set_kv("blank", "")
    assert repo.get_kv("blank", "fallback") == ""


def test_kv_keys_are_case_sensitive_and_independent(repo: Repo) -> None:
    repo.set_kv("Key", "1")
    repo.set_kv("key", "2")
    assert (repo.get_kv("Key"), repo.get_kv("key")) == ("1", "2")


def test_kv_values_survive_unicode_control_characters_and_size(repo: Repo) -> None:
    for i, value in enumerate(
        ["ünï 日本語 🚀", "quote \" ' backslash \\ NUL \x00 CRLF \r\n", "x" * 2_000_000]
    ):
        repo.set_kv(f"k{i}", value)
        assert repo.get_kv(f"k{i}") == value
    repo.set_kv("ключ-🔑", "значение")
    assert repo.get_kv("ключ-🔑") == "значение"


def test_kv_updated_at_moves_with_the_clock(repo: Repo, fake_clock: FakeClock) -> None:
    repo.set_kv("k", "v1")
    fake_clock.advance(timedelta(hours=2))
    repo.set_kv("k", "v2")
    stamp = repo.db.connect().execute("SELECT updated_at FROM kv WHERE key = 'k'").fetchone()[0]
    assert stamp == fake_clock.now().isoformat(timespec="microseconds")


# ================================================================================== ATS accounts


def test_ats_account_round_trip_and_defaults(repo: Repo, fake_clock: FakeClock) -> None:
    assert repo.get_ats_account("acme.wd5.myworkdayjobs.com", "alex.rivera@example.test") is None
    created = repo.upsert_ats_account("acme.wd5.myworkdayjobs.com", "alex.rivera@example.test")
    assert created.host == "acme.wd5.myworkdayjobs.com"
    assert created.email == "alex.rivera@example.test"
    assert created.verified is False
    assert created.created_at == fake_clock.now()
    assert created.last_login_ok_at is None
    assert repo.get_ats_account("acme.wd5.myworkdayjobs.com", "alex.rivera@example.test") == created


def test_ats_accounts_are_per_host_and_email(repo: Repo) -> None:
    repo.upsert_ats_account("acme.wd5.myworkdayjobs.com", "alex.rivera@example.test", verified=True)
    assert repo.get_ats_account("globex.wd1.myworkdayjobs.com", "alex.rivera@example.test") is None
    assert repo.get_ats_account("acme.wd5.myworkdayjobs.com", "someone.else@example.test") is None


def test_ats_account_lookup_ignores_case_and_padding(repo: Repo) -> None:
    repo.upsert_ats_account("Acme.WD5.MyWorkdayJobs.com", "Alex.Rivera@Example.test")
    found = repo.get_ats_account(" acme.wd5.myworkdayjobs.com ", "alex.rivera@example.test ")
    assert found is not None
    assert found.host == "Acme.WD5.MyWorkdayJobs.com"  # the first spelling is kept for display
    again = repo.upsert_ats_account(
        "acme.wd5.myworkdayjobs.com", "ALEX.RIVERA@EXAMPLE.TEST", verified=True
    )
    assert again.verified is True
    assert repo.db.connect().execute("SELECT COUNT(*) FROM ats_accounts").fetchone()[0] == 1


def test_ats_verified_flag_semantics(repo: Repo) -> None:
    host, email = "boards.greenhouse.io", "alex.rivera@example.test"
    assert repo.upsert_ats_account(host, email, verified=True).verified is True
    assert repo.upsert_ats_account(host, email).verified is True  # None leaves it as it is
    assert repo.upsert_ats_account(host, email, verified=None).verified is True
    assert repo.upsert_ats_account(host, email, verified=False).verified is False  # explicit reset
    assert repo.upsert_ats_account("jobs.lever.co", email, verified=False).verified is False


def test_ats_login_ok_stamps_the_time_and_is_sticky(repo: Repo, fake_clock: FakeClock) -> None:
    host, email = "boards.greenhouse.io", "alex.rivera@example.test"
    repo.upsert_ats_account(host, email)
    fake_clock.advance(timedelta(days=1))
    stamped = repo.upsert_ats_account(host, email, login_ok=True)
    assert stamped.last_login_ok_at == fake_clock.now()
    fake_clock.advance(timedelta(days=1))
    later = repo.upsert_ats_account(host, email, verified=True)
    assert later.last_login_ok_at == stamped.last_login_ok_at  # not touched by other updates
    assert later.verified is True
    assert (
        repo.upsert_ats_account("jobs.lever.co", email, login_ok=True).last_login_ok_at
        == fake_clock.now()
    )


def test_ats_upsert_requires_host_and_email(repo: Repo) -> None:
    with pytest.raises(ValueError):
        repo.upsert_ats_account("", "alex.rivera@example.test")
    with pytest.raises(ValueError):
        repo.upsert_ats_account("boards.greenhouse.io", "  ")


def test_ats_account_records_are_immutable_metadata(repo: Repo) -> None:
    record = repo.upsert_ats_account("boards.greenhouse.io", "alex.rivera@example.test")
    assert set(type(record).model_fields) == {
        "host",
        "email",
        "verified",
        "created_at",
        "last_login_ok_at",
    }
    with pytest.raises(ValueError):
        record.verified = True
