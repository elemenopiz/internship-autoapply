"""Pipeline orchestration: one discover -> score -> tailor -> apply run (docs/SPEC.md section 5.10).

``run_once(deps, trigger=..., mode=..., limit=...)`` never raises for an ordinary failure: everything that goes wrong
is recorded on the returned ``RunReport`` (and in the database) and the run moves on. Order of a run:

1. load the config (re-read on EVERY run, so dashboard edits apply live), resolve the mode, ``start_run``;
2. kill switch (``STOP`` file or ``deps.stop_flag``) -> ``kill_switch``;
3. readiness gate (``full_auto`` / ``dry_run`` only) -> ``not_ready`` with the issues in ``errors``. ``discover_only``
   never applies, so it skips the gate entirely (SPEC 5.10 "discover_only exempt") and never builds a runner;
4. the DB run lock (unique owner) -> ``already_running`` when another run holds it;
5. recover stale ``APPLYING`` rows (``FAILED/INTERRUPTED``, never auto-retried);
6. ingest -> ``upsert_opportunities`` -> score EVERY stored opportunity (so profile edits such as a new denylist entry
   apply to already known postings) -> ``set_scores``;
7. select candidates (``select_candidates``), then loop while nothing says stop. Before every attempt the loop
   re-checks, in this order: kill switch, attempt budget, daily cap (``full_auto`` only).

``stopped_reason`` says why a run ended BEFORE working through its candidates: ``cap_reached``, ``kill_switch``,
``no_candidates``, ``attempt_budget``, ``not_ready``, ``already_running`` or ``error``. A run that processes every
candidate (or a ``discover_only`` run) leaves it ``None`` even when the cap happens to be exhausted by the last
attempt (``cap_remaining == 0`` shows that).

Interpretations worth knowing (all on the safe side):

* attempt budget = ``min(limit, apply.max_attempts_per_run)`` (a CLI ``--limit`` can lower it, never raise it);
* the per-job attempt bound (``apply.max_attempts_per_job``) counts attempts of the SAME kind as the current run:
  dry-run attempts never eat the real attempts of a job and vice versa;
* a candidate is re-checked with ``has_submitted`` right before its attempt, so two rows of the same job (same
  fingerprint) can never both be submitted in one run;
* the runner is built lazily just before the first attempt (after tailoring, before the row is written, so a runner
  that cannot start does not burn a job's attempt); the application row is always written before ``apply_to``, the
  first employer-facing call.
"""

from __future__ import annotations

import importlib
import logging
import os
import random
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo

from autoapply.clock import Clock, local_day
from autoapply.config import AppConfig, AppPaths, resolve_resume_path
from autoapply.contracts import ApplicationRunner, CredentialStore, LLMClient, SourceContext
from autoapply.db import Repo
from autoapply.llm import BudgetedLLM
from autoapply.models import (
    SUBMITTED_STATUSES,
    Application,
    ApplicationStatus,
    ApplyResult,
    KnowledgeBase,
    Opportunity,
    Profile,
    Reason,
    RunMode,
    RunReport,
    SearchProfile,
    TailoredDocs,
)
from autoapply.readiness import check_readiness
from autoapply.secrets import OPENAI_KEY_ENV, normalize_key, redact

__all__ = [
    "BOT_CHECK_RETRY_AFTER",
    "DRY_RUN_OK_SKIP_WINDOW",
    "EMAIL_VERIFICATION_RETRY_AFTER",
    "FAILED_RETRY_AFTER",
    "IngestOutcome",
    "PipelineDeps",
    "StopReason",
    "attempts_used",
    "is_retry_eligible",
    "run_once",
    "select_candidates",
]

log = logging.getLogger("autoapply.pipeline")

FAILED_RETRY_AFTER = timedelta(minutes=30)
EMAIL_VERIFICATION_RETRY_AFTER = timedelta(hours=1)
BOT_CHECK_RETRY_AFTER = timedelta(hours=24)
DRY_RUN_OK_SKIP_WINDOW = timedelta(days=7)
STALE_ATTEMPT_MARGIN = timedelta(minutes=5)

_TECHNICAL_REASONS = frozenset(
    {Reason.TIMEOUT, Reason.NETWORK_ERROR, Reason.INTERNAL_ERROR, Reason.UNEXPECTED_FLOW}
)
_TRIGGERS = ("manual", "schedule", "cli", "test")
_Trigger = Literal["manual", "schedule", "cli", "test"]
_MAX_MESSAGE_CHARS = 400
_MAX_ERRORS = 50
_MIN_LOCK_TTL_S = 900.0
_LOCK_TTL_MARGIN_S = 300.0


class StopReason(StrEnum):
    """Every value ``RunReport.stopped_reason`` can take."""

    CAP_REACHED = "cap_reached"
    KILL_SWITCH = "kill_switch"
    NO_CANDIDATES = "no_candidates"
    ATTEMPT_BUDGET = "attempt_budget"
    NOT_READY = "not_ready"
    ALREADY_RUNNING = "already_running"
    ERROR = "error"


# ------------------------------------------------------------------------------------------ dependencies


class IngestOutcome(Protocol):
    """What an ``ingest`` callable returns (``sources.IngestResult`` satisfies it)."""

    @property
    def opportunities(self) -> Sequence[Opportunity]: ...

    @property
    def errors(self) -> Sequence[str]: ...


def _resolve_default(what: str, candidates: Sequence[tuple[str, str]]) -> Callable[..., Any]:
    """Import ``module.attr`` at CALL time (never at import time); ``RuntimeError`` naming the module if absent."""
    problems: list[str] = []
    for module_name, attr in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            problems.append(f"module {module_name!r} is not available ({exc})")
            continue
        func = getattr(module, attr, None)
        if callable(func):
            return cast(Callable[..., Any], func)
        problems.append(f"module {module_name!r} has no callable {attr!r}")
    raise RuntimeError(
        f"the pipeline needs {what}, but " + "; ".join(problems) + ". Pass the matching "
        "PipelineDeps field explicitly or add the module."
    )


@dataclass
class PipelineDeps:
    """Everything ``run_once`` touches, injectable so tests (and the CLI/dashboard) can swap any part.

    Required: ``paths``, ``repo``, ``clock`` (share the Repo's clock) and ``load_config`` (called at the start of
    EVERY run). The rest is optional:

    * ``runner_factory(config) -> ApplicationRunner``: built lazily, only when a ``full_auto``/``dry_run`` run
      reaches its first attempt, and ``close()``d when the run ends. Without one such a run stops with an error.
    * ``llm_factory(config) -> LLMClient | None``: called once per run, only when tailoring starts; ``None`` or a
      failure means the deterministic (no-LLM) tailoring path. The pipeline never closes the client.
    * ``source_ctx_factory(config) -> SourceContext``: default is a bare ``SourceContext``.
    * ``ingest(ctx)``, ``score(ops, search, profile)``, ``tailor(op, kb, profile, paths, llm, resume_fallback)`` and
      ``load_kb(paths)``: default to ``autoapply.sources.ingest_all``, ``autoapply.scoring.score_all``,
      ``autoapply.tailor.generate_documents`` and ``autoapply.tailor.load_kb``, imported lazily when first needed.
    * ``env`` (default ``os.environ``) and ``store`` feed the readiness gate; ``sleep`` paces attempts (default:
      wait on ``stop_flag`` so a stop request interrupts the pause); ``rng`` draws the pacing delays;
      ``stop_flag`` is set by ``RunManager.request_stop``.
    """

    paths: AppPaths
    repo: Repo
    clock: Clock
    load_config: Callable[[], AppConfig]
    runner_factory: Callable[[AppConfig], ApplicationRunner] | None = None
    llm_factory: Callable[[AppConfig], LLMClient | None] | None = None
    source_ctx_factory: Callable[[AppConfig], SourceContext] | None = None
    ingest: Callable[[SourceContext], IngestOutcome] | None = None
    score: Callable[[Sequence[Opportunity], SearchProfile, Profile], list[Opportunity]] | None = (
        None
    )
    tailor: (
        Callable[
            [Opportunity, KnowledgeBase, Profile, AppPaths, LLMClient | None, Path | None],
            TailoredDocs,
        ]
        | None
    ) = None
    load_kb: Callable[[AppPaths], KnowledgeBase] | None = None
    env: Mapping[str, str] | None = None
    store: CredentialStore | None = None
    sleep: Callable[[float], None] | None = None
    rng: random.Random = field(default_factory=random.Random)
    stop_flag: threading.Event = field(default_factory=threading.Event)

    def resolve_ingest(self) -> Callable[[SourceContext], IngestOutcome]:
        if self.ingest is not None:
            return self.ingest
        return _resolve_default("sources.ingest_all", [("autoapply.sources", "ingest_all")])

    def resolve_score(
        self,
    ) -> Callable[[Sequence[Opportunity], SearchProfile, Profile], list[Opportunity]]:
        if self.score is not None:
            return self.score
        return _resolve_default("scoring.score_all", [("autoapply.scoring", "score_all")])

    def resolve_tailor(
        self,
    ) -> Callable[
        [Opportunity, KnowledgeBase, Profile, AppPaths, LLMClient | None, Path | None], TailoredDocs
    ]:
        if self.tailor is not None:
            return self.tailor
        return _resolve_default(
            "tailor.generate_documents",
            [
                ("autoapply.tailor", "generate_documents"),
                ("autoapply.tailor.generate", "generate_documents"),
            ],
        )

    def resolve_load_kb(self) -> Callable[[AppPaths], KnowledgeBase]:
        if self.load_kb is not None:
            return self.load_kb
        return _resolve_default(
            "tailor.load_kb",
            [("autoapply.tailor", "load_kb"), ("autoapply.tailor.knowledge", "load_kb")],
        )

    def build_source_ctx(self, config: AppConfig) -> SourceContext:
        if self.source_ctx_factory is not None:
            return self.source_ctx_factory(config)
        return SourceContext(config=config, paths=self.paths, clock=self.clock)

    def pause(self, seconds: float) -> None:
        """Pacing pause between two attempts (interruptible by ``stop_flag`` unless ``sleep`` is injected)."""
        if self.sleep is not None:
            self.sleep(seconds)
        else:
            self.stop_flag.wait(max(0.0, seconds))


# ------------------------------------------------------------------------------------------ retry policy


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _waited(application: Application, now: datetime, delay: timedelta) -> bool:
    stamp = application.finished_at or application.started_at
    return stamp is not None and _as_utc(now) - _as_utc(stamp) >= delay


def _questions_answered(repo: Repo, opportunity_id: str) -> bool:
    """True when the job has pending questions and every one is resolved (or answered by a saved answer)."""
    questions = repo.list_pending_questions(unresolved_only=False, opportunity_id=opportunity_id)
    if not questions:
        return False
    return all(
        q.resolved or repo.find_answer(question_norm=q.question) is not None for q in questions
    )


def _retry_rule_allows(
    application: Application, now: datetime, config: AppConfig, repo: Repo
) -> bool:
    status, reason = application.status, application.reason
    if status is ApplicationStatus.DRY_RUN_OK:
        return True  # a proven dry run never blocks the real attempt
    if status not in (ApplicationStatus.NEEDS_MANUAL, ApplicationStatus.FAILED):
        return False  # applying (in flight), submitted, skipped
    if reason is Reason.MISSING_ANSWER:
        return _questions_answered(repo, application.opportunity_id)
    if reason is Reason.ATTESTATION_NOT_AUTHORIZED:
        return config.apply.attestations_authorized
    if reason is Reason.EMAIL_VERIFICATION:
        return config.apply.email.enabled and _waited(
            application, now, EMAIL_VERIFICATION_RETRY_AFTER
        )
    if reason is Reason.BOT_CHECK:
        return _waited(application, now, BOT_CHECK_RETRY_AFTER)
    if status is ApplicationStatus.FAILED and (reason is None or reason in _TECHNICAL_REASONS):
        return _waited(application, now, FAILED_RETRY_AFTER)
    return False  # unsupported portal, login, closed, ineligible, interrupted, other, ...


def attempts_used(repo: Repo, opportunity_id: str, *, dry_run: bool) -> int:
    """Attempts already made on the job of the same kind (dry-run vs real) as the current run."""
    return sum(
        1
        for app in repo.list_applications(opportunity_id=opportunity_id)
        if (app.mode is RunMode.DRY_RUN) == dry_run
    )


def is_retry_eligible(
    application: Application,
    now: datetime,
    config: AppConfig,
    repo: Repo,
    *,
    dry_run: bool | None = None,
) -> bool:
    """May the job whose LATEST attempt is ``application`` be attempted again? (SPEC 5.10 retry table)

    ==============================  =========================================================
    latest attempt                  auto-retry
    ==============================  =========================================================
    DRY_RUN_OK                      yes (a real run may follow a dry run)
    NEEDS_MANUAL / MISSING_ANSWER   once every pending question of the job is resolved
    ...ATTESTATION_NOT_AUTHORIZED   once ``apply.attestations_authorized`` is true
    ...EMAIL_VERIFICATION           after >= 1 h and only if ``apply.email.enabled``
    ...BOT_CHECK                    after >= 24 h
    FAILED (timeout, network,       after >= 30 min (also FAILED without a reason)
    internal, unexpected flow)
    everything else                 never (APPLYING, submitted, skipped, INTERRUPTED, ...)
    ==============================  =========================================================

    Every "yes" also needs ``attempts_used < apply.max_attempts_per_job`` where attempts are counted for the same
    kind as the current run (``dry_run``; default: the kind of ``application``). Elapsed times are measured from
    ``finished_at`` (else ``started_at``) and are inclusive: exactly 30 min qualifies.
    """
    if not _retry_rule_allows(application, now, config, repo):
        return False
    dry = (application.mode is RunMode.DRY_RUN) if dry_run is None else dry_run
    return attempts_used(repo, application.opportunity_id, dry_run=dry) < (
        config.apply.max_attempts_per_job
    )


def select_candidates(
    repo: Repo, config: AppConfig, mode: RunMode, now: datetime
) -> list[Opportunity]:
    """Opportunities a run in ``mode`` would attempt, best first (score desc, then oldest ``first_seen``).

    A candidate passed its score, is open, was never submitted or marked applied (by id or fingerprint), has
    attempts left and satisfies the retry policy. In ``dry_run`` mode jobs with a DRY_RUN_OK younger than 7 days
    are skipped.
    """
    dry_run = mode is RunMode.DRY_RUN
    chosen: list[Opportunity] = []
    for op in repo.list_opportunities(passed_only=True, is_open=True, order="score_desc"):
        if repo.has_submitted(op.id, op.fingerprint):
            continue
        latest = repo.latest_application(op.id)
        if latest is None:
            if config.apply.max_attempts_per_job <= 0:
                continue
        elif not is_retry_eligible(latest, now, config, repo, dry_run=dry_run):
            continue
        if dry_run and repo.list_applications(
            status=ApplicationStatus.DRY_RUN_OK,
            opportunity_id=op.id,
            since=_as_utc(now) - DRY_RUN_OK_SKIP_WINDOW,
            limit=1,
        ):
            continue
        chosen.append(op)
    return chosen


# ------------------------------------------------------------------------------------------ the run


def _valid_trigger(value: str) -> _Trigger:
    return cast(_Trigger, value if value in _TRIGGERS else "manual")


def _failure(reason: Reason, message: str, *steps: str) -> ApplyResult:
    return ApplyResult(
        status=ApplicationStatus.FAILED, reason=reason, message=message, steps=list(steps)
    )


@dataclass
class _TailoringKit:
    tailor: Callable[..., TailoredDocs]
    kb: KnowledgeBase
    llm: LLMClient | None


class _PipelineRun:
    """State of ONE ``run_once`` call (private; use ``run_once``)."""

    def __init__(
        self, deps: PipelineDeps, trigger: str, mode: RunMode | str | None, limit: int | None
    ) -> None:
        self.deps = deps
        self.repo = deps.repo
        self.requested_mode = mode
        self.limit = limit
        self.report = RunReport(trigger=_valid_trigger(trigger), started_at=deps.clock.now())
        self.config: AppConfig | None = None
        self.mode = RunMode.FULL_AUTO
        self.run_id: int | None = None
        self.owner: str | None = None
        self.lock_held = False
        self._runner: ApplicationRunner | None = None
        self._kit: _TailoringKit | None = None

    # -- small helpers ------------------------------------------------------------------------------
    def _redact(self, text: str) -> str:
        env = self.deps.env if self.deps.env is not None else os.environ
        return redact(text, normalize_key(env.get(OPENAI_KEY_ENV)))

    def _describe(self, exc: BaseException) -> str:
        detail = " ".join(str(exc).split())
        text = self._redact(f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__)
        return text if len(text) <= _MAX_MESSAGE_CHARS else text[: _MAX_MESSAGE_CHARS - 3] + "..."

    def _note(self, message: str) -> None:
        errors = self.report.errors
        if len(errors) < _MAX_ERRORS:
            errors.append(self._redact(message)[:_MAX_MESSAGE_CHARS])
        elif len(errors) == _MAX_ERRORS:
            errors.append("... further errors omitted")

    def _fail(self, message: str) -> None:
        self._note(message)
        self.report.stopped_reason = StopReason.ERROR.value

    def _stop(self, reason: StopReason) -> None:
        self.report.stopped_reason = reason.value

    def _kill_switch(self) -> bool:
        if self.deps.stop_flag.is_set():
            return True
        try:
            return self.deps.paths.stop_file.exists()
        except OSError:  # cannot tell: fail closed
            return True

    def _cap_used(self, config: AppConfig) -> int:
        now = self.deps.clock.now()
        return self.repo.count_submitted_on(local_day(now, config.timezone), config.timezone)

    # -- entry point --------------------------------------------------------------------------------
    def execute(self) -> RunReport:
        try:
            try:
                self._run()
            except Exception as exc:
                log.debug("pipeline run failed", exc_info=exc)
                self._fail(f"unexpected error: {self._describe(exc)}")
            except BaseException as exc:  # KeyboardInterrupt / SystemExit: clean up, then propagate
                self._fail(f"run interrupted: {type(exc).__name__}")
                raise
        finally:
            self._cleanup()
        return self.report

    def _cleanup(self) -> None:
        """Close the runner, release the lock, finish the run record. Each step is independent and never raises."""
        if self._runner is not None:
            try:
                self._runner.close()
            except Exception as exc:
                self._note(f"closing the runner failed: {self._describe(exc)}")
            self._runner = None
        if self.lock_held and self.config is not None:
            try:
                self.report.cap_remaining = max(
                    0, self.config.daily_cap - self._cap_used(self.config)
                )
            except Exception as exc:
                log.warning("could not compute the remaining cap: %s", self._describe(exc))
        self.report.finished_at = self.deps.clock.now()
        if self.lock_held and self.owner is not None:
            try:
                self.repo.release_run_lock(self.owner)
            except Exception as exc:
                log.warning("could not release the run lock: %s", self._describe(exc))
            self.lock_held = False
        if self.run_id is not None:
            try:
                self.repo.finish_run(self.run_id, self.report)
            except Exception as exc:
                log.warning("could not record the run: %s", self._describe(exc))

    # -- phases -------------------------------------------------------------------------------------
    def _run(self) -> None:
        deps, report = self.deps, self.report
        config_error: Exception | None = None
        try:
            self.config = deps.load_config()
        except Exception as exc:
            config_error = exc
        if self.requested_mode is not None:
            self.mode = RunMode(self.requested_mode)
        elif self.config is not None:
            self.mode = self.config.mode
        report.mode = self.mode
        self.run_id = self.repo.start_run(self.mode, report.trigger)
        report.run_id = self.run_id
        config = self.config
        if config is None:
            detail = self._describe(config_error) if config_error else "unknown error"
            self._fail(f"configuration could not be loaded: {detail}")
            return
        try:
            ZoneInfo(config.timezone)
        except Exception:
            self._fail(f"config.timezone {config.timezone!r} is not a valid IANA time zone")
            return
        try:
            deps.paths.ensure()
        except OSError as exc:
            self._fail(f"data directory unusable: {self._describe(exc)}")
            return
        if self._kill_switch():
            self._stop(StopReason.KILL_SWITCH)
            return
        if self.mode is not RunMode.DISCOVER_ONLY and not self._ready(config):
            return
        if not self._acquire_lock(config):
            return
        self._recover_stale(config)
        if not self._discover_and_score(config):
            return
        candidates = select_candidates(self.repo, config, self.mode, deps.clock.now())
        report.eligible = len(candidates)
        if self.mode is RunMode.DISCOVER_ONLY:
            return
        if not candidates:
            self._stop(StopReason.NO_CANDIDATES)
            return
        self._apply_loop(config, candidates)

    def _ready(self, config: AppConfig) -> bool:
        deps = self.deps
        readiness = check_readiness(config, deps.paths, deps.env, deps.store, mode=self.mode)
        if readiness.ok:
            return True
        self._stop(StopReason.NOT_READY)
        for issue in readiness.issues:
            self._note(f"[{issue.code}] {issue.field}: {issue.message}")
        return False

    def _acquire_lock(self, config: AppConfig) -> bool:
        self.owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        ttl = max(
            _MIN_LOCK_TTL_S,
            float(config.apply.attempt_timeout_s + config.apply.max_delay_s) + _LOCK_TTL_MARGIN_S,
        )
        if not self.repo.acquire_run_lock(self.owner, ttl):
            self._stop(StopReason.ALREADY_RUNNING)
            return False
        self.lock_held = True
        return True

    def _heartbeat(self) -> bool:
        assert self.owner is not None
        if self.repo.heartbeat_run_lock(self.owner):
            return True
        self._fail("the run lock was lost (another run took it over); stopping")
        return False

    def _recover_stale(self, config: AppConfig) -> None:
        older = timedelta(seconds=max(0, config.apply.attempt_timeout_s)) + STALE_ATTEMPT_MARGIN
        recovered = self.repo.recover_stale_applications(older)
        if recovered:
            log.info("recovered %d interrupted attempt(s)", recovered)

    def _discover_and_score(self, config: AppConfig) -> bool:
        self._ingest(config)
        if self._kill_switch():
            self._stop(StopReason.KILL_SWITCH)
            return False
        if not self._heartbeat():
            return False
        try:
            stored = self.repo.list_opportunities()
            scored = self.deps.resolve_score()(stored, config.search, config.profile)
            self.repo.set_scores((op.id, op.score) for op in scored if op.score is not None)
        except Exception as exc:
            # Stale scores must never drive an application (a new denylist entry would be ignored).
            self._fail(f"scoring failed: {self._describe(exc)}")
            return False
        if self._kill_switch():
            self._stop(StopReason.KILL_SWITCH)
            return False
        return self._heartbeat()

    def _ingest(self, config: AppConfig) -> None:
        try:
            outcome = self.deps.resolve_ingest()(self.deps.build_source_ctx(config))
            if isinstance(outcome, list | tuple):
                found, problems = list(outcome), []
            else:
                found = list(outcome.opportunities)
                problems = [str(e) for e in getattr(outcome, "errors", [])]
        except Exception as exc:
            self._note(f"ingest failed: {self._describe(exc)}")
            return
        for problem in problems:
            self._note(problem)
        try:
            stored = self.repo.upsert_opportunities(found)
        except Exception as exc:
            self._note(f"bulk store failed ({self._describe(exc)}); storing one by one")
            stored = []
            for op in found:
                try:
                    stored.append(self.repo.upsert_opportunity(op))
                except Exception as inner:
                    self._note(f"could not store {op.company!r}: {self._describe(inner)}")
        self.report.discovered = len(found)
        self.report.new = sum(1 for _, is_new in stored if is_new)

    # -- the attempt loop ---------------------------------------------------------------------------
    def _stop_reason(self, config: AppConfig, budget: int) -> StopReason | None:
        if self._kill_switch():
            return StopReason.KILL_SWITCH
        if self.report.attempted >= budget:
            return StopReason.ATTEMPT_BUDGET
        if self.mode is RunMode.FULL_AUTO and self._cap_used(config) >= config.daily_cap:
            return StopReason.CAP_REACHED
        return None

    def _apply_loop(self, config: AppConfig, candidates: Sequence[Opportunity]) -> None:
        budget = config.apply.max_attempts_per_run
        if self.limit is not None:
            budget = min(budget, self.limit)
        budget = max(0, budget)
        for op in candidates:
            reason = self._stop_reason(config, budget)
            if reason is None and self.report.attempted > 0:
                low, high = sorted((config.apply.min_delay_s, config.apply.max_delay_s))
                self.deps.pause(self.deps.rng.uniform(low, high))
                reason = self._stop_reason(
                    config, budget
                )  # STOP may have appeared during the pause
            if reason is not None:
                self._stop(reason)
                return
            if not self._heartbeat():
                return
            if self.repo.has_submitted(op.id, op.fingerprint):
                continue  # a duplicate row of a job submitted moments ago in this very run
            if not self._attempt(config, op):
                return

    def _prepare_kit(self, config: AppConfig) -> bool:
        if self._kit is not None:
            return True
        deps = self.deps
        try:
            tailor = deps.resolve_tailor()
        except RuntimeError as exc:
            self._fail(f"tailoring is unavailable: {exc}")
            return False
        llm: LLMClient | None = None
        if deps.llm_factory is not None:
            try:
                llm = deps.llm_factory(config)
            except Exception as exc:
                self._note(f"LLM unavailable, using deterministic tailoring: {self._describe(exc)}")
        try:
            kb = deps.resolve_load_kb()(deps.paths)
        except Exception as exc:
            self._note(
                f"knowledge base could not be loaded ({self._describe(exc)}); "
                "using the uploaded resume unchanged"
            )
            kb = KnowledgeBase()
        self._kit = _TailoringKit(tailor=tailor, kb=kb, llm=llm)
        return True

    def _get_runner(self, config: AppConfig) -> ApplicationRunner:
        if self._runner is None:
            factory = self.deps.runner_factory
            if factory is None:
                raise RuntimeError("PipelineDeps.runner_factory is not set")
            runner = factory(config)
            if runner is None:
                raise RuntimeError("runner_factory returned no runner")
            self._runner = runner
        return self._runner

    def _tailor(self, config: AppConfig, op: Opportunity) -> tuple[TailoredDocs | None, str | None]:
        assert self._kit is not None
        llm = self._kit.llm
        budgeted = BudgetedLLM(llm, max(0, config.llm.max_calls_per_application)) if llm else None
        try:
            docs = self._kit.tailor(
                op,
                self._kit.kb,
                config.profile,
                self.deps.paths,
                budgeted,
                resolve_resume_path(config, self.deps.paths),
            )
        except Exception as exc:
            log.debug("tailoring failed", exc_info=exc)
            return None, f"tailoring failed: {self._describe(exc)}"
        return docs, None

    def _attempt(self, config: AppConfig, op: Opportunity) -> bool:
        """One candidate. Returns False when the whole run must stop (``stopped_reason`` already set)."""
        if not self._prepare_kit(config):
            return False
        docs, tailor_error = self._tailor(config, op)
        if self._kill_switch():  # tailoring can be slow; nothing employer-facing has happened yet
            self._stop(StopReason.KILL_SWITCH)
            return False
        runner: ApplicationRunner | None = None
        if tailor_error is None:
            try:
                runner = self._get_runner(config)
            except Exception as exc:
                self._fail(f"the application runner could not start: {self._describe(exc)}")
                return False
        try:
            app = self.repo.create_application(op.id, self.mode, self.run_id)
        except Exception as exc:
            self._note(f"could not record an attempt for {op.company!r}: {self._describe(exc)}")
            return True
        self.report.attempted += 1
        result: ApplyResult | None = None
        try:
            if tailor_error is not None:
                result = _failure(Reason.INTERNAL_ERROR, tailor_error, "pipeline: " + tailor_error)
                self._note(f"{op.company} - {op.title}: {tailor_error}")
            else:
                assert runner is not None
                result = self._call_runner(runner, op, docs)
        finally:
            # Whatever happened (even KeyboardInterrupt) the row must not stay APPLYING.
            final = result or _failure(
                Reason.INTERRUPTED,
                "Interrupted before the attempt finished; the outcome is unknown.",
            )
            self._finish(app.id, final, docs)
            self._count(final)
            self._persist_questions(op, final)
        return True

    def _call_runner(
        self, runner: ApplicationRunner, op: Opportunity, docs: TailoredDocs | None
    ) -> ApplyResult:
        dry_run = self.mode is RunMode.DRY_RUN
        try:
            assert docs is not None
            result = runner.apply_to(op, docs, dry_run=dry_run)
        except Exception as exc:
            log.debug("runner raised", exc_info=exc)
            text = self._describe(exc)
            self._note(f"{op.company} - {op.title}: runner error: {text}")
            return _failure(Reason.INTERNAL_ERROR, text, f"pipeline: runner raised {text}")
        if not isinstance(result, ApplyResult):
            return _failure(
                Reason.INTERNAL_ERROR,
                f"runner returned {type(result).__name__}, not an ApplyResult",
            )
        if result.status is ApplicationStatus.APPLYING:
            return _failure(
                Reason.INTERNAL_ERROR, "runner returned the non-terminal status 'applying'"
            )
        if dry_run and result.status in SUBMITTED_STATUSES:
            self._note(
                f"{op.company} - {op.title}: runner reported '{result.status.value}' during a dry run"
            )
        log.info("%s - %s -> %s", op.company, op.title, result.status.value)
        return result

    def _finish(
        self, application_id: int | None, result: ApplyResult, docs: TailoredDocs | None
    ) -> None:
        if application_id is None:
            return
        try:
            self.repo.finish_application(application_id, result, docs)
        except Exception as exc:  # the row stays APPLYING and is recovered as INTERRUPTED later
            self._note(
                f"could not record the outcome of attempt {application_id}: {self._describe(exc)}"
            )

    def _count(self, result: ApplyResult) -> None:
        report, status = self.report, result.status
        if status in SUBMITTED_STATUSES:
            report.submitted += 1
        elif status is ApplicationStatus.DRY_RUN_OK:
            report.dry_run_ok += 1
        elif status is ApplicationStatus.NEEDS_MANUAL:
            report.needs_manual += 1
        elif status is ApplicationStatus.FAILED:
            report.failed += 1
        elif status is ApplicationStatus.SKIPPED:
            report.skipped += 1

    def _persist_questions(self, op: Opportunity, result: ApplyResult) -> None:
        for question in result.pending_questions:
            try:
                self.repo.add_pending_question(
                    question.model_copy(
                        update={
                            "opportunity_id": question.opportunity_id or op.id,
                            "company": question.company or op.company,
                        }
                    )
                )
            except Exception as exc:
                self._note(f"could not queue a question for the user: {self._describe(exc)}")


def run_once(
    deps: PipelineDeps,
    *,
    trigger: str = "manual",
    mode: RunMode | str | None = None,
    limit: int | None = None,
) -> RunReport:
    """Run the whole pipeline once (module docstring). Never raises for an ordinary failure.

    ``mode`` defaults to ``config.mode``; ``limit`` lowers the attempt budget for this run. An unknown ``trigger``
    is recorded as ``"manual"``. ``KeyboardInterrupt``/``SystemExit`` still clean up (application row finished,
    runner closed, lock released, run recorded) and then propagate.
    """
    return _PipelineRun(deps, trigger, mode, limit).execute()
