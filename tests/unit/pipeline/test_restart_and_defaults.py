"""Idempotency, restarts, crash recovery, and the lazily resolved default collaborators."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import autoapply
from autoapply.db import Database, Repo
from autoapply.models import ApplicationStatus, ApplyResult, Reason
from autoapply.pipeline import PipelineDeps

S, R = ApplicationStatus, Reason


def test_running_twice_never_repeats_work(world: Any) -> None:
    world.config.daily_cap = 10
    ops = world.add_ops(3)
    world.runner.script = {
        ops[2].id: ApplyResult(status=S.NEEDS_MANUAL, reason=R.UNSUPPORTED_PORTAL)
    }
    first = world.run()
    assert (first.submitted, first.needs_manual) == (2, 1)
    second = world.run()
    assert second.attempted == 0 and second.stopped_reason == "no_candidates"
    assert (second.discovered, second.new) == (3, 0)
    assert len(world.runner.calls) == 3
    assert world.repo.count_applications() == 3


def test_a_restart_with_new_repo_and_deps_keeps_all_state(world: Any) -> None:
    world.config.daily_cap = 2
    world.add_ops(5)
    assert world.run().submitted == 2
    world.repo.db.close()
    fresh = Repo(Database(world.paths.db_file), world.clock)
    report = world.run(repo=fresh)
    assert report.attempted == 0 and report.stopped_reason == "cap_reached"
    assert report.new == 0
    world.clock.advance(timedelta(days=1))
    report = world.run(repo=fresh)
    assert report.submitted == 2, (
        "next local day: a fresh cap, and the earlier jobs are not repeated"
    )
    assert len({op.id for op, _, _ in world.runner.calls}) == 4


def test_a_crashed_attempt_is_recovered_as_interrupted_and_never_retried(world: Any) -> None:
    dead, other = world.seed(
        2,
    )
    app = world.repo.create_application(dead.id, "full_auto")  # the process died here
    assert app.status is S.APPLYING
    world.repo.acquire_run_lock("dead-process", 60)
    world.clock.advance(timedelta(minutes=20))  # lease expired; attempt older than timeout + margin
    report = world.run()
    recovered = world.repo.get_application(app.id)
    assert recovered is not None
    assert (recovered.status, recovered.reason) == (S.FAILED, R.INTERRUPTED)
    assert report.submitted == 1 and world.runner.names == [other.company]
    world.clock.advance(timedelta(days=30))
    assert world.run().attempted == 0, "INTERRUPTED is never auto-retried, however old"


def test_a_fresh_applying_row_is_not_recovered_and_blocks_its_job(world: Any) -> None:
    (op,) = world.seed(1)
    app = world.repo.create_application(op.id, "full_auto")
    world.clock.advance(timedelta(minutes=2))
    report = world.run()
    assert report.stopped_reason == "no_candidates"
    still = world.repo.get_application(app.id)
    assert still is not None and still.status is S.APPLYING


def test_stale_recovery_threshold_follows_the_attempt_timeout(world: Any) -> None:
    (op,) = world.seed(1)
    app = world.repo.create_application(op.id, "full_auto")
    world.config.apply.attempt_timeout_s = 600
    world.clock.advance(timedelta(minutes=14))
    world.run(ingest=lambda ctx: [])
    assert world.repo.get_application(app.id).status is S.APPLYING  # type: ignore[union-attr]
    world.clock.advance(timedelta(minutes=2))
    world.run(ingest=lambda ctx: [])
    assert world.repo.get_application(app.id).status is S.FAILED  # type: ignore[union-attr]


def test_two_rows_of_one_job_are_never_both_submitted_in_a_run(world: Any) -> None:
    first = world.make_op("Twin", url="https://a.example.test/1")
    second = world.make_op("Twin", title=first.title, url="https://b.example.test/2")
    assert first.fingerprint == second.fingerprint
    world.ops = [first, second]
    report = world.run()
    assert report.submitted == 1 and len(world.runner.calls) == 1


# ------------------------------------------------------------------------------------------ defaults


def test_importing_the_pipeline_pulls_in_none_of_its_collaborators() -> None:
    src = str(Path(autoapply.__file__).parents[1])
    code = (
        "import sys, autoapply.pipeline, autoapply.scheduler;"
        "bad = [m for m in ('autoapply.sources', 'autoapply.scoring', 'autoapply.tailor', 'autoapply.apply',"
        " 'autoapply.apply.engine', 'playwright', 'reportlab', 'openpyxl') if m in sys.modules];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": src},
        check=True,
    )
    assert out.stdout.strip() == "[]"


@pytest.mark.parametrize(
    ("field", "module", "attr"),
    [
        ("ingest", "autoapply.sources", "ingest_all"),
        ("score", "autoapply.scoring", "score_all"),
        ("tailor", "autoapply.tailor", "generate_documents"),
        ("load_kb", "autoapply.tailor", "load_kb"),
    ],
)
def test_defaults_resolve_to_the_real_functions_at_call_time(
    world: Any, field: str, module: str, attr: str
) -> None:
    import importlib

    deps = world.deps(**{field: None})
    resolved = getattr(deps, f"resolve_{field}")()
    assert resolved is getattr(importlib.import_module(module), attr)


@pytest.mark.parametrize(
    ("field", "module"),
    [
        ("ingest", "autoapply.sources"),
        ("score", "autoapply.scoring"),
        ("tailor", "autoapply.tailor"),
        ("load_kb", "autoapply.tailor"),
    ],
)
def test_a_missing_module_is_a_clear_runtime_error(
    world: Any, monkeypatch: pytest.MonkeyPatch, field: str, module: str
) -> None:
    monkeypatch.setitem(sys.modules, module, None)  # makes the import fail whether or not it exists
    if field in ("tailor", "load_kb"):
        monkeypatch.setitem(sys.modules, "autoapply.tailor.generate", None)
        monkeypatch.setitem(sys.modules, "autoapply.tailor.knowledge", None)
    with pytest.raises(RuntimeError, match=module):
        getattr(world.deps(**{field: None}), f"resolve_{field}")()


def test_a_module_without_the_function_names_the_module_too(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "autoapply.scoring", ModuleType("autoapply.scoring"))
    with pytest.raises(RuntimeError, match=r"autoapply\.scoring.*score_all"):
        world.deps(score=None).resolve_score()


def test_missing_collaborators_surface_in_the_report_not_as_crashes(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.seed(1)
    monkeypatch.setitem(sys.modules, "autoapply.scoring", None)
    report = world.run(score=None)
    assert report.stopped_reason == "error" and "autoapply.scoring" in report.errors[0]
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, "autoapply.sources", None)
    report = world.run(ingest=None)
    assert report.submitted == 1 and "ingest failed: RuntimeError" in report.errors[0]
    assert "autoapply.sources" in report.errors[0]


def test_default_source_context_is_a_bare_one(world: Any) -> None:
    ctx = world.deps(source_ctx_factory=None).build_source_ctx(world.config)
    assert (
        ctx.config is world.config
        and ctx.paths == world.paths
        and ctx.clock is world.clock
        and ctx.http is None
    )


def test_end_to_end_with_the_real_scorer_tailor_and_kb_loader(world: Any) -> None:
    world.add_ops(1, description="Own the product roadmap.")
    report = world.run(score=None, tailor=None, load_kb=None)
    assert report.errors == [] and report.submitted == 1, report
    app = world.repo.list_applications()[0]
    assert app.docs["mode"] == "fallback_uploaded_resume"
    assert Path(app.docs["resume"]).read_bytes() == world.paths.resume_file.read_bytes()


def test_deps_are_a_plain_mutable_dataclass(world: Any) -> None:
    deps = world.deps()
    assert isinstance(deps, PipelineDeps)
    deps.stop_flag = __import__("threading").Event()
