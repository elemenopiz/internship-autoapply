"""SQLite persistence layer (docs/SPEC.md section 5.1).

Design in one screen:

* ``Database`` owns the file. One connection per thread (``threading.local``), WAL, ``foreign_keys=ON``,
  ``busy_timeout=10000``, ``sqlite3.Row`` rows and *manual* transaction control (``isolation_level=None``),
  so every write is an explicit ``BEGIN IMMEDIATE`` .. ``COMMIT`` (the write lock is taken up front, so
  read-modify-write sequences never fail half way with ``SQLITE_BUSY_SNAPSHOT``). ``migrate()`` applies the
  append-only ``MIGRATIONS`` list keyed by ``PRAGMA user_version`` and is safe to call at every start, even from
  several threads or processes at once.
* ``Repo`` is what the rest of the application talks to. It never leaks SQL and speaks the pydantic models of
  ``autoapply.models``.
* Timestamps are stored as fixed-width UTC ISO-8601 strings (``2026-09-29T15:00:00.000000+00:00``) so that
  lexicographic order equals chronological order; every timestamp read back is an aware UTC ``datetime``.
  JSON blobs (``extra``, score detail, docs, steps, filled fields, artifacts) are TEXT.
* The daily cap (``count_submitted_on``), the never-apply-twice guard (``has_submitted``) and the cross-process
  run lock all live in the file, never in memory, so they survive restarts and are shared by every process.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
import unicodedata
import weakref
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from autoapply.clock import Clock, SystemClock, local_day, local_day_bounds_utc
from autoapply.models import (
    ATS,
    SUBMITTED_STATUSES,
    Application,
    ApplicationStatus,
    ApplyResult,
    Opportunity,
    OpportunitySource,
    PendingQuestion,
    QuestionKind,
    Reason,
    RunMode,
    RunReport,
    ScoreResult,
    ScreeningAnswer,
    TailoredDocs,
)
from autoapply.normalize import fingerprint as make_fingerprint
from autoapply.normalize import norm_text

log = logging.getLogger("autoapply.db")

BUSY_TIMEOUT_MS = 10_000
_WAL_SWITCH_TIMEOUT_S = 15.0
_ANSWER_KIND_BY_QUESTION_KIND: dict[QuestionKind, str] = {
    QuestionKind.BOOLEAN: "boolean",
    QuestionKind.SINGLE_CHOICE: "choice",
    QuestionKind.MULTI_CHOICE: "choice",
    QuestionKind.NUMBER: "number",
}


# --------------------------------------------------------------------------------------------- errors


class NotFoundError(LookupError):
    """A row the caller named (opportunity, application, run, pending question, answer) does not exist."""


class SchemaVersionError(RuntimeError):
    """The database was written by a newer version of the application than the one running."""


# --------------------------------------------------------------------------------------------- migrations


@dataclass(frozen=True)
class Migration:
    """One schema step. ``version`` is the ``PRAGMA user_version`` the database has AFTER the step."""

    version: int
    description: str
    statements: tuple[str, ...]


_SCHEMA_V1: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id            TEXT PRIMARY KEY,
        company       TEXT NOT NULL,
        title         TEXT NOT NULL,
        url           TEXT NOT NULL DEFAULT '',
        apply_url     TEXT,
        location      TEXT,
        term          TEXT,
        source        TEXT NOT NULL,
        ats           TEXT NOT NULL DEFAULT 'unknown',
        is_open       INTEGER NOT NULL DEFAULT 1 CHECK (is_open IN (0, 1)),
        posted_date   TEXT,
        last_verified TEXT,
        deadline      TEXT,
        description   TEXT,
        extra         TEXT NOT NULL DEFAULT '{}',
        fingerprint   TEXT NOT NULL DEFAULT '',
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL,
        score         REAL,
        score_passed  INTEGER CHECK (score_passed IN (0, 1)),
        score_detail  TEXT,
        scored_at     TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opportunities_fingerprint ON opportunities(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(score)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_source ON opportunities(source)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_last_seen ON opportunities(last_seen)",
    """
    CREATE TABLE IF NOT EXISTS applications (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
        attempt_no     INTEGER NOT NULL,
        status         TEXT NOT NULL,
        reason         TEXT,
        message        TEXT NOT NULL DEFAULT '',
        mode           TEXT NOT NULL,
        ats            TEXT NOT NULL DEFAULT 'unknown',
        run_id         INTEGER,
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        submitted_at   TEXT,
        confirmation   TEXT,
        docs           TEXT NOT NULL DEFAULT '{}',
        artifacts      TEXT NOT NULL DEFAULT '[]',
        steps          TEXT NOT NULL DEFAULT '[]',
        filled_fields  TEXT NOT NULL DEFAULT '{}',
        UNIQUE (opportunity_id, attempt_no)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)",
    "CREATE INDEX IF NOT EXISTS idx_applications_started ON applications(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_applications_run ON applications(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_applications_submitted ON applications(submitted_at) "
    "WHERE submitted_at IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS screening_answers (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        intent        TEXT,
        question      TEXT NOT NULL,
        question_norm TEXT NOT NULL DEFAULT '',
        answer        TEXT NOT NULL,
        answer_kind   TEXT NOT NULL DEFAULT 'text',
        source        TEXT NOT NULL DEFAULT 'user',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        use_count     INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_answers_intent ON screening_answers(intent) "
    "WHERE intent IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_answers_question_norm ON screening_answers(question_norm)",
    """
    CREATE TABLE IF NOT EXISTS pending_questions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        question       TEXT NOT NULL,
        question_norm  TEXT NOT NULL,
        kind           TEXT NOT NULL DEFAULT 'text',
        options        TEXT NOT NULL DEFAULT '[]',
        opportunity_id TEXT,
        company        TEXT,
        created_at     TEXT NOT NULL,
        resolved       INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
        resolved_at    TEXT,
        answer_text    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_lookup "
    "ON pending_questions(question_norm, opportunity_id, resolved)",
    "CREATE INDEX IF NOT EXISTS idx_pending_resolved ON pending_questions(resolved, created_at)",
    # No password column, by design: credentials live only in the OS credential store.
    """
    CREATE TABLE IF NOT EXISTS ats_accounts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        host             TEXT NOT NULL COLLATE NOCASE,
        email            TEXT NOT NULL COLLATE NOCASE,
        verified         INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
        created_at       TEXT NOT NULL,
        last_login_ok_at TEXT,
        UNIQUE (host, email)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        mode        TEXT NOT NULL,
        trigger     TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        report      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_lock (
        id           INTEGER PRIMARY KEY CHECK (id = 1),
        owner        TEXT NOT NULL,
        acquired_at  TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at   TEXT NOT NULL,
        ttl_s        REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kv (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)

# APPEND-ONLY: never edit or reorder an entry that has shipped; add a new ``Migration`` with the next version.
MIGRATIONS: tuple[Migration, ...] = (Migration(1, "initial schema", _SCHEMA_V1),)


def latest_schema_version() -> int:
    """The ``user_version`` a fully migrated database has (looked up at call time so tests can extend it)."""
    return MIGRATIONS[-1].version if MIGRATIONS else 0


# --------------------------------------------------------------------------------------------- helpers


def _iso(moment: datetime) -> str:
    """Fixed-width UTC ISO-8601 text; naive datetimes are rejected instead of silently guessed."""
    if moment.tzinfo is None:
        raise ValueError(f"timestamps must be timezone-aware, got naive {moment!r}")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _parse_dt(value: str | None) -> datetime | None:
    """Aware UTC datetime from stored text; None for empty or unparseable values.

    Lenient on purpose: a garbled timestamp must never take a whole listing down or wedge the run lock
    (a lock whose expiry cannot be read counts as expired).
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        log.warning("ignoring unparseable timestamp %r in the database", value)
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _json_default(value: object) -> Any:
    """Degrade gracefully for provider data that is not JSON (spreadsheet dates, sets, paths, ...)."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    return str(value)


def _dumps(value: Any) -> str:
    # ensure_ascii keeps lone surrogates / NULs escaped, so any Python str round-trips through SQLite.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=_json_default)


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _loads_dict(text: str | None) -> dict[str, Any]:
    value = _loads(text)
    return value if isinstance(value, dict) else {}


def _loads_list(text: str | None) -> list[Any]:
    value = _loads(text)
    return value if isinstance(value, list) else []


def _fold(value: str | None) -> str | None:
    """Unicode-aware case folding for the search filter (SQLite's own LIKE only folds ASCII)."""
    return None if value is None else unicodedata.normalize("NFC", value).casefold()


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fetchone(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
) -> sqlite3.Row | None:
    cursor = conn.execute(sql, params)
    try:
        row: sqlite3.Row | None = cursor.fetchone()
    finally:
        cursor.close()  # never leave a read statement pending: it would pin a stale WAL snapshot
    return row


def _fetchall(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
) -> list[sqlite3.Row]:
    cursor = conn.execute(sql, params)
    try:
        rows: list[sqlite3.Row] = cursor.fetchall()
    finally:
        cursor.close()
    return rows


def _pragma(conn: sqlite3.Connection, statement: str) -> Any:
    """First column of the first row a PRAGMA returns (None if it returns nothing)."""
    row = _fetchone(conn, statement)
    return None if row is None else row[0]


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


# --------------------------------------------------------------------------------------------- Database


class Database:
    """A SQLite file plus a per-thread connection cache. Cheap to construct; touches the disk lazily."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        raw = os.fspath(path)
        if not str(raw).strip() or str(raw) == ":memory:" or str(raw).startswith("file:"):
            raise ValueError(
                "Database needs a real file path: connections are per thread, so an in-memory "
                f"database would be a different database in every thread (got {raw!r})"
            )
        self._path = Path(raw).absolute()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._generation = 0
        self._conns: list[tuple[weakref.ReferenceType[threading.Thread], sqlite3.Connection]] = []

    @property
    def path(self) -> Path:
        return self._path

    # -- connections -------------------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        """The calling thread's connection, opened (and configured) on first use.

        The connection is only ever used by its own thread; ``close()`` may close it from another one
        (``check_same_thread=False``) once nobody uses it any more.
        """
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None and getattr(self._local, "generation", -1) == self._generation:
            return conn
        conn = self._open()
        with self._lock:
            self._prune_dead_threads_locked()
            self._conns.append((weakref.ref(threading.current_thread()), conn))
            self._local.conn = conn
            self._local.generation = self._generation
        return conn

    def _open(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._open_configured()
        except sqlite3.Error as exc:
            # sqlite's own messages ("unable to open database file", "file is not a database") never say
            # WHICH file; the readiness report and dashboard need to.
            raise type(exc)(f"{self._path}: {exc}") from exc

    def _open_configured(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._path),
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,  # autocommit: transactions are explicit (BEGIN IMMEDIATE ... COMMIT)
            check_same_thread=False,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            _enable_wal(conn)
            # FULL: a committed application/cap row must survive even an OS crash; the write rate is tiny.
            conn.execute("PRAGMA synchronous = FULL")
            conn.create_function("autoapply_fold", 1, _fold, deterministic=True)
        except BaseException:
            conn.close()
            raise
        return conn

    def _prune_dead_threads_locked(self) -> None:
        alive: list[tuple[weakref.ReferenceType[threading.Thread], sqlite3.Connection]] = []
        for ref, conn in self._conns:
            thread = ref()
            if thread is not None and thread.is_alive():
                alive.append((ref, conn))
            else:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
        self._conns = alive

    def open_connections(self) -> int:
        """Number of live connections this instance holds (one per thread that has used it)."""
        with self._lock:
            self._prune_dead_threads_locked()
            return len(self._conns)

    def close(self) -> None:
        """Close every connection this instance opened, in any thread. Idempotent.

        The instance stays usable: the next ``connect()`` in any thread opens a fresh connection. Do not
        call it while another thread is in the middle of a transaction.
        """
        with self._lock:
            conns = [conn for _, conn in self._conns]
            self._conns = []
            self._generation += 1
        self._local.conn = None
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Database({str(self._path)!r})"

    # -- transactions ------------------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` .. ``COMMIT`` (``ROLLBACK`` on any exception).

        Nested use joins the outer transaction, so a caller can group several Repo calls atomically.
        """
        conn = self.connect()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            _rollback(conn)
            raise
        try:
            conn.execute("COMMIT")
        except BaseException:
            _rollback(conn)
            raise

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """A deferred read transaction: every SELECT inside sees one consistent snapshot."""
        conn = self.connect()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            _rollback(conn)  # nothing to commit; ends the read transaction

    # -- schema ------------------------------------------------------------------------------------
    def schema_version(self) -> int:
        """The ``PRAGMA user_version`` of the file (0 for a brand-new one)."""
        return int(_pragma(self.connect(), "PRAGMA user_version") or 0)

    def migrate(self) -> int:
        """Bring the schema to ``latest_schema_version()`` and return the resulting version.

        Idempotent and cheap when up to date (one PRAGMA read, no write lock). All pending migrations run
        in ONE ``BEGIN IMMEDIATE`` transaction, so a failure leaves the file at its previous version, and
        concurrent callers (threads or processes) serialise: the loser re-reads the version under the lock.
        Raises ``SchemaVersionError`` for a database written by a newer application.
        """
        target = latest_schema_version()
        current = self.schema_version()
        if current == target:
            return current
        if current > target:
            raise _newer_schema(self._path, current, target)
        with self.transaction() as conn:
            current = int(_pragma(conn, "PRAGMA user_version") or 0)
            if current > target:
                raise _newer_schema(self._path, current, target)
            for migration in MIGRATIONS:
                if migration.version <= current:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                log.info(
                    "database migrated to schema v%d (%s)", migration.version, migration.description
                )
                current = migration.version
        return current


def _newer_schema(path: Path, current: int, target: int) -> SchemaVersionError:
    return SchemaVersionError(
        f"{path} has schema version {current} but this application only knows up to {target}; "
        "refusing to touch a database written by a newer version"
    )


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch the file to WAL. Concurrent first-time switches can raise "locked", so retry within a budget.

    On a filesystem without WAL support the app keeps working in the default journal mode (with a warning).
    """
    deadline = time.monotonic() + _WAL_SWITCH_TIMEOUT_S
    attempts = 0
    while True:
        attempts += 1
        try:
            mode = str(_pragma(conn, "PRAGMA journal_mode")).lower()
            if mode != "wal":
                mode = str(_pragma(conn, "PRAGMA journal_mode=WAL")).lower()
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
            continue
        if mode == "wal":
            return
        if attempts >= 20 or time.monotonic() >= deadline:
            log.warning("could not enable WAL (journal_mode=%s); continuing without it", mode)
            return
        time.sleep(0.05)


# --------------------------------------------------------------------------------------------- records


class AtsAccountRecord(BaseModel):
    """Metadata about an ATS tenant account. There is deliberately no password field."""

    model_config = ConfigDict(frozen=True)

    host: str
    email: str
    verified: bool = False
    created_at: datetime
    last_login_ok_at: datetime | None = None


class RunLockInfo(BaseModel):
    """Who holds the cross-process run lock (for status displays)."""

    model_config = ConfigDict(frozen=True)

    owner: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    ttl_s: float
    expired: bool


class RepoStats(TypedDict):
    day: str  # local calendar day used for ``submitted_today`` (ISO date)
    opportunities_total: int
    opportunities_open: int
    opportunities_scored: int
    opportunities_passed: int
    opportunities_by_source: dict[str, int]  # every OpportunitySource present, zero-filled
    applications_total: int
    applications_by_status: dict[str, int]  # every ApplicationStatus present, zero-filled
    submitted_today: int  # SUBMITTED + SUBMITTED_UNCONFIRMED, non-dry-run, local day of "now"


# --------------------------------------------------------------------------------------------- row mapping


def _opportunity_from_row(row: sqlite3.Row) -> Opportunity:
    score: ScoreResult | None = None
    if row["score_detail"]:
        with contextlib.suppress(ValueError):  # a corrupt blob must not take the whole list down
            score = ScoreResult.model_validate_json(row["score_detail"])
    return Opportunity(
        id=row["id"],
        company=row["company"],
        title=row["title"],
        url=row["url"],
        apply_url=row["apply_url"],
        location=row["location"],
        term=row["term"],
        source=OpportunitySource(row["source"]),
        ats=ATS(row["ats"]),
        is_open=bool(row["is_open"]),
        posted_date=_parse_date(row["posted_date"]),
        last_verified=_parse_date(row["last_verified"]),
        deadline=_parse_date(row["deadline"]),
        description=row["description"],
        extra=_loads_dict(row["extra"]),
        fingerprint=row["fingerprint"],
        first_seen=_parse_dt(row["first_seen"]),
        last_seen=_parse_dt(row["last_seen"]),
        score=score,
    )


def _application_from_row(row: sqlite3.Row) -> Application:
    return Application(
        id=row["id"],
        opportunity_id=row["opportunity_id"],
        attempt_no=row["attempt_no"],
        status=ApplicationStatus(row["status"]),
        reason=Reason(row["reason"]) if row["reason"] else None,
        message=row["message"],
        mode=RunMode(row["mode"]),
        ats=ATS(row["ats"]),
        run_id=row["run_id"],
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        submitted_at=_parse_dt(row["submitted_at"]),
        confirmation=row["confirmation"],
        docs=_loads_dict(row["docs"]),
        artifacts=_loads_list(row["artifacts"]),
        steps=_loads_list(row["steps"]),
        filled_fields=_loads_dict(row["filled_fields"]),
    )


def _answer_from_row(row: sqlite3.Row) -> ScreeningAnswer:
    return ScreeningAnswer(
        id=row["id"],
        intent=row["intent"],
        question=row["question"],
        question_norm=row["question_norm"],
        answer=row["answer"],
        answer_kind=row["answer_kind"],
        source=row["source"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        use_count=row["use_count"],
    )


def _pending_from_row(row: sqlite3.Row) -> PendingQuestion:
    return PendingQuestion(
        id=row["id"],
        question=row["question"],
        kind=QuestionKind(row["kind"]),
        options=[str(o) for o in _loads_list(row["options"])],
        opportunity_id=row["opportunity_id"],
        company=row["company"],
        created_at=_parse_dt(row["created_at"]),
        resolved=bool(row["resolved"]),
    )


def _account_from_row(row: sqlite3.Row) -> AtsAccountRecord:
    return AtsAccountRecord(
        host=row["host"],
        email=row["email"],
        verified=bool(row["verified"]),
        created_at=_parse_dt(row["created_at"]) or _EPOCH,
        last_login_ok_at=_parse_dt(row["last_login_ok_at"]),
    )


def _run_from_row(row: sqlite3.Row) -> RunReport:
    """Rebuild a RunReport; the row's own columns are authoritative over whatever the stored JSON says."""
    stored = _loads_dict(row["report"])
    try:
        report = RunReport.model_validate(stored)
    except ValueError:
        report = RunReport()
    return report.model_copy(
        update={
            "run_id": row["id"],
            "mode": RunMode(row["mode"]),
            "trigger": row["trigger"],
            "started_at": _parse_dt(row["started_at"]),
            "finished_at": _parse_dt(row["finished_at"]),
        }
    )


# --------------------------------------------------------------------------------------------- merging


def _resolve_is_open(
    current_open: bool,
    current_verified: date | None,
    incoming_open: bool,
    incoming_verified: date | None,
) -> bool:
    """Decide ``is_open`` on re-ingest; a stale or undated record can close a posting but never re-open it.

    * both records dated: the incoming state wins iff it is at least as fresh as the stored evidence;
    * otherwise (a date is missing): only a CLOSED claim is believed, an "open" claim proves nothing.
    """
    if incoming_verified is not None and current_verified is not None:
        return incoming_open if incoming_verified >= current_verified else current_open
    return False if not incoming_open else current_open


def merge_opportunities(current: Opportunity, incoming: Opportunity) -> Opportunity:
    """Pure merge of a stored opportunity with a freshly ingested record (see ``Repo.upsert_opportunity``).

    ``first_seen`` / ``last_seen`` are left as they are on ``current``; the Repo owns those.
    """

    def text(new: str | None, old: str | None) -> str | None:
        return new if new is not None and new.strip() else old

    company = text(incoming.company, current.company) or ""
    title = text(incoming.title, current.title) or ""
    location = text(incoming.location, current.location)
    description = current.description
    if incoming.description and (not description or len(incoming.description) > len(description)):
        description = incoming.description

    verified_dates = [d for d in (current.last_verified, incoming.last_verified) if d is not None]
    derived_incoming = make_fingerprint(incoming.company, incoming.title, incoming.location)
    if incoming.fingerprint and incoming.fingerprint != derived_incoming:
        fingerprint_value = incoming.fingerprint  # a provider supplied its own key: respect it
    else:
        fingerprint_value = make_fingerprint(company, title, location)

    return current.model_copy(
        update={
            "company": company,
            "title": title,
            "url": text(incoming.url, current.url) or "",
            "apply_url": text(incoming.apply_url, current.apply_url),
            "location": location,
            "term": text(incoming.term, current.term),
            "source": incoming.source,
            "ats": incoming.ats if incoming.ats != ATS.UNKNOWN else current.ats,
            "is_open": _resolve_is_open(
                current.is_open, current.last_verified, incoming.is_open, incoming.last_verified
            ),
            "posted_date": incoming.posted_date or current.posted_date,
            "last_verified": max(verified_dates) if verified_dates else None,
            "deadline": incoming.deadline or current.deadline,
            "description": description,
            "extra": {**current.extra, **incoming.extra},
            "fingerprint": fingerprint_value,
            "score": incoming.score if incoming.score is not None else current.score,
        }
    )


_OPPORTUNITY_COLUMNS: tuple[str, ...] = (
    "company",
    "title",
    "url",
    "apply_url",
    "location",
    "term",
    "source",
    "ats",
    "is_open",
    "posted_date",
    "last_verified",
    "deadline",
    "description",
    "extra",
    "fingerprint",
    "first_seen",
    "last_seen",
    "score",
    "score_passed",
    "score_detail",
    "scored_at",
)
_INSERT_OPPORTUNITY = (
    f"INSERT INTO opportunities (id, {', '.join(_OPPORTUNITY_COLUMNS)}) "
    f"VALUES (:id, {', '.join(':' + c for c in _OPPORTUNITY_COLUMNS)})"
)
_UPDATE_OPPORTUNITY = f"UPDATE opportunities SET {', '.join(f'{c} = :{c}' for c in _OPPORTUNITY_COLUMNS)} WHERE id = :id"


def _opportunity_params(
    op: Opportunity, *, first_seen: str, last_seen: str, scored_at: str | None
) -> dict[str, Any]:
    return {
        "id": op.id,
        "company": op.company,
        "title": op.title,
        "url": op.url,
        "apply_url": op.apply_url or None,
        "location": op.location,
        "term": op.term,
        "source": op.source.value,
        "ats": op.ats.value,
        "is_open": 1 if op.is_open else 0,
        "posted_date": op.posted_date.isoformat() if op.posted_date else None,
        "last_verified": op.last_verified.isoformat() if op.last_verified else None,
        "deadline": op.deadline.isoformat() if op.deadline else None,
        "description": op.description,
        "extra": _dumps(op.extra),
        "fingerprint": op.fingerprint,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "score": op.score.score if op.score else None,
        "score_passed": (1 if op.score.passed else 0) if op.score else None,
        "score_detail": op.score.model_dump_json() if op.score else None,
        "scored_at": scored_at if op.score else None,
    }


_OPPORTUNITY_ORDER_SQL: dict[str, str] = {
    "score_desc": "(o.score IS NULL), o.score DESC, o.first_seen ASC, o.id ASC",
    "seen_desc": "o.last_seen DESC, o.first_seen DESC, o.id ASC",
    "first_seen_desc": "o.first_seen DESC, o.last_seen DESC, o.id ASC",
}
_LATEST_APPLICATION_STATUS = (
    "(SELECT a.status FROM applications a WHERE a.opportunity_id = o.id "
    "ORDER BY a.attempt_no DESC LIMIT 1)"
)


def _opportunity_filters(
    *,
    min_score: float | None,
    passed_only: bool,
    status: ApplicationStatus | str | None,
    source: OpportunitySource | str | None,
    search: str | None,
    is_open: bool | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if min_score is not None:
        clauses.append("o.score >= ?")
        params.append(float(min_score))
    if passed_only:
        clauses.append("o.score_passed = 1")
    if is_open is not None:
        clauses.append("o.is_open = ?")
        params.append(1 if is_open else 0)
    # A blank string means "no filter" (what an HTML form sends for "any"), never an error.
    if source is not None and str(source).strip():
        clauses.append("o.source = ?")
        params.append(OpportunitySource(str(source).strip().lower()).value)
    if search is not None and search.strip():
        pattern = "%" + _escape_like(_fold(search.strip()) or "") + "%"
        clauses.append(
            "(autoapply_fold(o.company) LIKE ? ESCAPE '\\' OR autoapply_fold(o.title) LIKE ? ESCAPE '\\')"
        )
        params += [pattern, pattern]
    if status is not None and str(status).strip():
        value = str(status).strip().lower()
        if value in ("open", "closed"):
            clauses.append("o.is_open = ?")
            params.append(1 if value == "open" else 0)
        elif value in ("unapplied", "none"):
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM applications a WHERE a.opportunity_id = o.id)"
            )
        else:
            clauses.append(f"{_LATEST_APPLICATION_STATUS} = ?")
            params.append(ApplicationStatus(value).value)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _application_filters(
    status: ApplicationStatus | str | Iterable[ApplicationStatus | str] | None,
    opportunity_id: str | None,
    since: datetime | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        values = (
            [ApplicationStatus(status).value]
            if isinstance(status, str)
            else [ApplicationStatus(s).value for s in status]
        )
        if not values:
            clauses.append("0")
        else:
            clauses.append(f"status IN ({', '.join('?' for _ in values)})")
            params += values
    if opportunity_id is not None:
        clauses.append("opportunity_id = ?")
        params.append(opportunity_id)
    if since is not None:
        clauses.append("started_at >= ?")
        params.append(_iso(since))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _limit_offset(limit: int | None, offset: int) -> tuple[int, int]:
    if offset < 0 or (limit is not None and limit < 0):
        raise ValueError("limit and offset must be non-negative")
    return (-1 if limit is None else limit), offset


def _docs_to_dict(docs: TailoredDocs | Mapping[str, str]) -> dict[str, str]:
    if isinstance(docs, TailoredDocs):
        result = {"mode": docs.mode, "resume": str(docs.resume_pdf)}
        if docs.cover_letter_pdf is not None:
            result["cover_letter"] = str(docs.cover_letter_pdf)
        return result
    return {str(k): str(v) for k, v in docs.items()}


# --------------------------------------------------------------------------------------------- Repo


class Repo:
    """All persistence operations. Thread-safe: every method uses the calling thread's connection.

    ``clock`` supplies "now" for every timestamp, TTL and staleness decision (inject ``FakeClock`` in tests).
    Constructing a Repo migrates the schema, so ``Repo(Database(path))`` is enough to get a working store.
    """

    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self.db = db
        self.clock: Clock = clock if clock is not None else SystemClock()
        db.migrate()

    @classmethod
    def open(cls, path: str | os.PathLike[str], clock: Clock | None = None) -> Repo:
        """Convenience: ``Repo(Database(path), clock)``."""
        return cls(Database(path), clock)

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]:
        """Group several Repo calls into one atomic transaction (nested calls join it)."""
        return self.db.transaction()

    def _now(self) -> datetime:
        return self.clock.now()

    # ================================================================================== opportunities
    def upsert_opportunity(self, op: Opportunity) -> tuple[Opportunity, bool]:
        """Insert ``op`` or merge it into the stored row with the same id. Returns ``(stored, is_new)``.

        Merge rules for an existing row: ``first_seen`` is kept and ``last_seen`` bumped; non-empty incoming
        fields override; ``description`` keeps the longer text; ``extra`` is dict-merged (incoming keys win);
        ``last_verified`` never moves backwards; ``is_open`` follows the incoming record only if it is at
        least as fresh as the stored evidence (an undated record may close a posting but never re-open it);
        the stored score and score detail are NEVER wiped by a record that carries none. A NEW row gets
        ``first_seen`` = the record's own ``first_seen`` when it carries one (never later than now), else now.
        """
        with self.db.transaction() as conn:
            return self._upsert_opportunity(conn, op, self._now())

    def upsert_opportunities(self, ops: Iterable[Opportunity]) -> list[tuple[Opportunity, bool]]:
        """``upsert_opportunity`` for a whole ingest in ONE transaction (all or nothing, one fsync)."""
        with self.db.transaction() as conn:
            now = self._now()
            return [self._upsert_opportunity(conn, op, now) for op in ops]

    def _upsert_opportunity(
        self, conn: sqlite3.Connection, op: Opportunity, now: datetime
    ) -> tuple[Opportunity, bool]:
        now_iso = _iso(now)
        row = _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (op.id,))
        if row is None:
            # A new row keeps an explicit first_seen (historic import, fixtures) but never one from the
            # future; ``last_seen`` is always "now". Existing rows keep whatever first_seen they have.
            first_seen = now_iso
            if op.first_seen is not None:
                given = op.first_seen if op.first_seen.tzinfo else op.first_seen.replace(tzinfo=UTC)
                first_seen = _iso(min(given, now))
            params = _opportunity_params(
                op, first_seen=first_seen, last_seen=now_iso, scored_at=now_iso
            )
            conn.execute(_INSERT_OPPORTUNITY, params)
            is_new = True
        else:
            merged = merge_opportunities(_opportunity_from_row(row), op)
            scored_at = now_iso if op.score is not None else row["scored_at"]
            params = _opportunity_params(
                merged, first_seen=row["first_seen"], last_seen=now_iso, scored_at=scored_at
            )
            conn.execute(_UPDATE_OPPORTUNITY, params)
            is_new = False
        stored = _fetchone(conn, "SELECT * FROM opportunities WHERE id = ?", (op.id,))
        assert stored is not None
        return _opportunity_from_row(stored), is_new

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """The stored opportunity (``.score`` populated when scored), or None."""
        row = _fetchone(
            self.db.connect(), "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
        )
        return _opportunity_from_row(row) if row else None

    def list_opportunities(
        self,
        min_score: float | None = None,
        status: ApplicationStatus | str | None = None,
        source: OpportunitySource | str | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str = "score_desc",
        *,
        passed_only: bool = False,
        is_open: bool | None = None,
    ) -> list[Opportunity]:
        """Filtered, ordered, paged opportunities; ``.score`` is populated for scored rows.

        * ``min_score``: ``score >= min_score`` (unscored rows are excluded); ``passed_only``: only rows
          whose stored ScoreResult passed.
        * ``status``: the status of the opportunity's LATEST application (an ApplicationStatus value), or
          ``"unapplied"`` (no application yet). ``"open"`` / ``"closed"`` are accepted as aliases of ``is_open``.
        * ``search``: case-insensitive (Unicode aware) substring of company or title; ``%``, ``_`` and ``\\``
          are literal characters, not wildcards.
        * ``order``: ``"score_desc"`` (unscored last, ties: oldest first), ``"seen_desc"`` (last seen first)
          or ``"first_seen_desc"`` (newly discovered first). ``limit=None`` means no limit.
        """
        order_sql = _OPPORTUNITY_ORDER_SQL.get(order)
        if order_sql is None:
            raise ValueError(
                f"unknown order {order!r}; expected one of {sorted(_OPPORTUNITY_ORDER_SQL)}"
            )
        where, params = _opportunity_filters(
            min_score=min_score,
            passed_only=passed_only,
            status=status,
            source=source,
            search=search,
            is_open=is_open,
        )
        lim, off = _limit_offset(limit, offset)
        rows = _fetchall(
            self.db.connect(),
            f"SELECT o.* FROM opportunities o{where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, lim, off],
        )
        return [_opportunity_from_row(r) for r in rows]

    def count_opportunities(
        self,
        min_score: float | None = None,
        status: ApplicationStatus | str | None = None,
        source: OpportunitySource | str | None = None,
        search: str | None = None,
        *,
        passed_only: bool = False,
        is_open: bool | None = None,
    ) -> int:
        """Number of opportunities ``list_opportunities`` would return with the same filters (no paging)."""
        where, params = _opportunity_filters(
            min_score=min_score,
            passed_only=passed_only,
            status=status,
            source=source,
            search=search,
            is_open=is_open,
        )
        row = _fetchone(
            self.db.connect(), f"SELECT COUNT(*) AS n FROM opportunities o{where}", params
        )
        assert row is not None
        return int(row["n"])

    def set_score(self, opportunity_id: str, result: ScoreResult) -> bool:
        """Store ``result`` as the opportunity's score. False if the opportunity does not exist."""
        with self.db.transaction() as conn:
            return self._set_score(conn, opportunity_id, result, _iso(self._now()))

    def set_scores(self, items: Iterable[tuple[str, ScoreResult]]) -> int:
        """``set_score`` for many opportunities in ONE transaction; returns how many rows were updated."""
        with self.db.transaction() as conn:
            now_iso = _iso(self._now())
            return sum(1 for oid, result in items if self._set_score(conn, oid, result, now_iso))

    @staticmethod
    def _set_score(
        conn: sqlite3.Connection, opportunity_id: str, result: ScoreResult, now_iso: str
    ) -> bool:
        cursor = conn.execute(
            "UPDATE opportunities SET score = ?, score_passed = ?, score_detail = ?, scored_at = ? "
            "WHERE id = ?",
            (
                result.score,
                1 if result.passed else 0,
                result.model_dump_json(),
                now_iso,
                opportunity_id,
            ),
        )
        return cursor.rowcount > 0

    # ================================================================================== applications
    def create_application(
        self, opportunity_id: str, mode: RunMode | str, run_id: int | None = None
    ) -> Application:
        """Write an ``APPLYING`` row (``attempt_no`` = previous + 1) BEFORE the browser opens.

        Raises ``NotFoundError`` for an unknown opportunity.
        """
        run_mode = RunMode(mode)
        with self.db.transaction() as conn:
            opp = _fetchone(conn, "SELECT ats FROM opportunities WHERE id = ?", (opportunity_id,))
            if opp is None:
                raise NotFoundError(f"unknown opportunity {opportunity_id!r}")
            new_id = self._insert_application(
                conn,
                opportunity_id,
                status=ApplicationStatus.APPLYING,
                mode=run_mode,
                ats=opp["ats"],
                run_id=run_id,
            )
            return self._get_application(conn, new_id)

    def _insert_application(
        self,
        conn: sqlite3.Connection,
        opportunity_id: str,
        *,
        status: ApplicationStatus,
        mode: RunMode,
        ats: str,
        run_id: int | None,
        reason: Reason | None = None,
        message: str = "",
        finished: bool = False,
    ) -> int:
        row = _fetchone(
            conn,
            "SELECT COALESCE(MAX(attempt_no), 0) AS n FROM applications WHERE opportunity_id = ?",
            (opportunity_id,),
        )
        assert row is not None
        now_iso = _iso(self._now())
        cursor = conn.execute(
            "INSERT INTO applications (opportunity_id, attempt_no, status, reason, message, mode, ats, "
            "run_id, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                opportunity_id,
                int(row["n"]) + 1,
                status.value,
                reason.value if reason else None,
                message,
                mode.value,
                ats,
                run_id,
                now_iso,
                now_iso if finished else None,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    @staticmethod
    def _get_application(conn: sqlite3.Connection, application_id: int) -> Application:
        row = _fetchone(conn, "SELECT * FROM applications WHERE id = ?", (application_id,))
        if row is None:
            raise NotFoundError(f"unknown application {application_id!r}")
        return _application_from_row(row)

    def get_application(self, application_id: int) -> Application | None:
        """The application with this id, or None."""
        row = _fetchone(
            self.db.connect(), "SELECT * FROM applications WHERE id = ?", (application_id,)
        )
        return _application_from_row(row) if row else None

    def finish_application(
        self,
        application_id: int,
        result: ApplyResult,
        docs: TailoredDocs | Mapping[str, str] | None = None,
    ) -> Application:
        """Record the outcome of an attempt (status, reason, message, confirmation, audit trail, docs).

        ``submitted_at`` is set (once) only for ``SUBMITTED`` / ``SUBMITTED_UNCONFIRMED``. ``docs`` may be
        the ``TailoredDocs`` used or a plain ``{"resume": path, ...}`` mapping; ``None`` keeps what is stored.
        ``result.pending_questions`` are NOT persisted here (call ``add_pending_question``).
        Raises ``NotFoundError`` for an unknown id and ``ValueError`` for a non-terminal status or an attempt
        to overwrite a recorded submission with a non-submitted outcome (a submission is never forgotten).
        """
        if result.status == ApplicationStatus.APPLYING:
            raise ValueError("finish_application needs a terminal status, not 'applying'")
        with self.db.transaction() as conn:
            row = _fetchone(conn, "SELECT * FROM applications WHERE id = ?", (application_id,))
            if row is None:
                raise NotFoundError(f"unknown application {application_id!r}")
            stored_status = ApplicationStatus(row["status"])
            if stored_status in SUBMITTED_STATUSES and result.status not in SUBMITTED_STATUSES:
                raise ValueError(
                    f"application {application_id} is already {stored_status.value}; "
                    f"refusing to overwrite it with {result.status.value}"
                )
            now_iso = _iso(self._now())
            submitted_at = row["submitted_at"]
            if result.status in SUBMITTED_STATUSES and not submitted_at:
                submitted_at = now_iso
            docs_json = _dumps(_docs_to_dict(docs)) if docs is not None else row["docs"]
            conn.execute(
                "UPDATE applications SET status = ?, reason = ?, message = ?, ats = ?, finished_at = ?, "
                "submitted_at = ?, confirmation = ?, docs = ?, artifacts = ?, steps = ?, "
                "filled_fields = ? WHERE id = ?",
                (
                    result.status.value,
                    result.reason.value if result.reason else None,
                    result.message,
                    result.ats.value if result.ats != ATS.UNKNOWN else row["ats"],
                    now_iso,
                    submitted_at,
                    result.confirmation,
                    docs_json,
                    _dumps(result.artifacts),
                    _dumps(result.steps),
                    _dumps(result.filled_fields),
                    application_id,
                ),
            )
            return self._get_application(conn, application_id)

    def list_applications(
        self,
        status: ApplicationStatus | str | Iterable[ApplicationStatus | str] | None = None,
        opportunity_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Application]:
        """Applications, newest attempt first. ``status`` may be one status or several; ``since`` filters
        on ``started_at`` (timezone-aware datetime)."""
        where, params = _application_filters(status, opportunity_id, since)
        lim, off = _limit_offset(limit, offset)
        rows = _fetchall(
            self.db.connect(),
            f"SELECT * FROM applications{where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, lim, off],
        )
        return [_application_from_row(r) for r in rows]

    def count_applications(
        self,
        status: ApplicationStatus | str | Iterable[ApplicationStatus | str] | None = None,
        opportunity_id: str | None = None,
        since: datetime | None = None,
    ) -> int:
        """Number of applications ``list_applications`` would return with the same filters (no paging)."""
        where, params = _application_filters(status, opportunity_id, since)
        row = _fetchone(self.db.connect(), f"SELECT COUNT(*) AS n FROM applications{where}", params)
        assert row is not None
        return int(row["n"])

    def latest_application(self, opportunity_id: str) -> Application | None:
        """The attempt with the highest ``attempt_no`` for the opportunity, or None."""
        row = _fetchone(
            self.db.connect(),
            "SELECT * FROM applications WHERE opportunity_id = ? ORDER BY attempt_no DESC LIMIT 1",
            (opportunity_id,),
        )
        return _application_from_row(row) if row else None

    def count_submitted_on(self, day: date, tz: str | ZoneInfo) -> int:
        """Submissions counted against the daily cap on local calendar ``day`` in ``tz``.

        Counts ``SUBMITTED`` + ``SUBMITTED_UNCONFIRMED`` rows whose mode is not ``dry_run`` and whose
        ``submitted_at`` lies in ``[start, end)`` of that local day (``clock.local_day_bounds_utc``, DST safe:
        the day may be 23 or 25 hours long). Read straight from the file, so it survives restarts.
        """
        start, end = local_day_bounds_utc(day, tz)
        row = _fetchone(
            self.db.connect(),
            "SELECT COUNT(*) AS n FROM applications WHERE status IN (?, ?) AND mode <> ? "
            "AND submitted_at >= ? AND submitted_at < ?",
            (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.SUBMITTED_UNCONFIRMED.value,
                RunMode.DRY_RUN.value,
                _iso(start),
                _iso(end),
            ),
        )
        assert row is not None
        return int(row["n"])

    def has_submitted(self, opportunity_id: str, fingerprint: str | None = None) -> bool:
        """The never-apply-twice guard: True if a real application already exists for this job.

        "Exists" means a non-dry-run ``SUBMITTED`` / ``SUBMITTED_UNCONFIRMED`` attempt, or a
        ``SKIPPED``/``ALREADY_APPLIED`` record (the user marked it applied, or the site said so), for
        ``opportunity_id`` or - when ``fingerprint`` is given - for ANY opportunity with that fingerprint.
        Dry runs, failures and needs-manual attempts never count.
        """
        fp = fingerprint if fingerprint and fingerprint.strip("| ") else None
        row = _fetchone(
            self.db.connect(),
            "SELECT 1 FROM applications a JOIN opportunities o ON o.id = a.opportunity_id "
            "WHERE (a.opportunity_id = :id OR (:fp IS NOT NULL AND o.fingerprint = :fp)) "
            "AND ((a.status IN ('submitted', 'submitted_unconfirmed') AND a.mode <> 'dry_run') "
            "     OR (a.status = 'skipped' AND a.reason = 'already_applied')) LIMIT 1",
            {"id": opportunity_id, "fp": fp},
        )
        return row is not None

    def recover_stale_applications(self, older_than: timedelta) -> int:
        """Turn ``APPLYING`` rows started strictly more than ``older_than`` ago into ``FAILED``/``INTERRUPTED``.

        Such rows belong to a process that died mid-attempt. The message says the outcome is unknown, and
        ``INTERRUPTED`` is not auto-retried by the pipeline (an unobserved submit must not be repeated).
        Returns the number of rows recovered.
        """
        if older_than < timedelta(0):
            raise ValueError("older_than must not be negative")
        now = self._now()
        with self.db.transaction() as conn:
            rows = _fetchall(
                conn,
                "SELECT id, steps FROM applications WHERE status = ? AND started_at < ?",
                (ApplicationStatus.APPLYING.value, _iso(now - older_than)),
            )
            for row in rows:
                steps = _loads_list(row["steps"])
                steps.append("recovered: still 'applying' at startup; the outcome is unknown")
                conn.execute(
                    "UPDATE applications SET status = ?, reason = ?, message = ?, finished_at = ?, "
                    "steps = ? WHERE id = ? AND status = ?",
                    (
                        ApplicationStatus.FAILED.value,
                        Reason.INTERRUPTED.value,
                        "Interrupted: the process ended before this attempt finished, so its outcome is "
                        "unknown. Check the employer's site before applying again.",
                        _iso(now),
                        _dumps(steps),
                        row["id"],
                        ApplicationStatus.APPLYING.value,
                    ),
                )
            return len(rows)

    def mark_manually_applied(self, opportunity_id: str) -> Application:
        """Record that the user applied by hand: a ``SKIPPED``/``ALREADY_APPLIED`` attempt.

        Idempotent (returns the existing record if there is one). Never counts toward the daily cap, and
        ``has_submitted`` treats the job as applied. Raises ``NotFoundError`` for an unknown opportunity.
        """
        with self.db.transaction() as conn:
            opp = _fetchone(conn, "SELECT ats FROM opportunities WHERE id = ?", (opportunity_id,))
            if opp is None:
                raise NotFoundError(f"unknown opportunity {opportunity_id!r}")
            existing = _fetchone(
                conn,
                "SELECT id FROM applications WHERE opportunity_id = ? AND status = ? AND reason = ? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (opportunity_id, ApplicationStatus.SKIPPED.value, Reason.ALREADY_APPLIED.value),
            )
            if existing is not None:
                return self._get_application(conn, existing["id"])
            new_id = self._insert_application(
                conn,
                opportunity_id,
                status=ApplicationStatus.SKIPPED,
                mode=RunMode.FULL_AUTO,
                ats=opp["ats"],
                run_id=None,
                reason=Reason.ALREADY_APPLIED,
                message="Marked as applied manually by the user.",
                finished=True,
            )
            return self._get_application(conn, new_id)

    # ================================================================================== screening answers
    def upsert_answer(self, answer: ScreeningAnswer) -> ScreeningAnswer:
        """Insert or update a saved answer and return the stored row.

        The row to update is: the one with ``answer.id`` (``NotFoundError`` if gone), else the one with the
        same ``intent``, else the one with the same normalised question text. ``question_norm`` is always
        ``norm_text`` of the given ``question_norm`` (or of ``question`` when empty), so writers and readers
        agree. An update keeps ``id``, ``created_at`` and ``use_count``, and keeps the stored ``intent`` when
        the incoming one is empty. Raises ``ValueError`` when there is neither an intent nor question text.
        """
        with self.db.transaction() as conn:
            return self._upsert_answer(conn, answer)

    def _upsert_answer(self, conn: sqlite3.Connection, answer: ScreeningAnswer) -> ScreeningAnswer:
        norm = norm_text(answer.question_norm or answer.question)
        intent = answer.intent.strip() if answer.intent and answer.intent.strip() else None
        if intent is None and not norm:
            raise ValueError("a saved answer needs an intent or question text to be matched by")
        now_iso = _iso(self._now())
        row: sqlite3.Row | None = None
        if answer.id is not None:
            row = _fetchone(conn, "SELECT * FROM screening_answers WHERE id = ?", (answer.id,))
            if row is None:
                raise NotFoundError(f"unknown saved answer {answer.id!r}")
        elif intent is not None:
            row = _fetchone(conn, "SELECT * FROM screening_answers WHERE intent = ?", (intent,))
        else:
            row = _fetchone(
                conn,
                "SELECT * FROM screening_answers WHERE question_norm = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                (norm,),
            )
        try:
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO screening_answers (intent, question, question_norm, answer, answer_kind, "
                    "source, created_at, updated_at, use_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        intent,
                        answer.question,
                        norm,
                        answer.answer,
                        answer.answer_kind,
                        answer.source,
                        now_iso,
                        now_iso,
                        max(0, answer.use_count),
                    ),
                )
                answer_id = cursor.lastrowid
            else:
                answer_id = row["id"]
                conn.execute(
                    "UPDATE screening_answers SET intent = ?, question = ?, question_norm = ?, answer = ?, "
                    "answer_kind = ?, source = ?, updated_at = ? WHERE id = ?",
                    (
                        intent if intent is not None else row["intent"],
                        answer.question,
                        norm,
                        answer.answer,
                        answer.answer_kind,
                        answer.source,
                        now_iso,
                        answer_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"another saved answer already uses intent {intent!r}") from exc
        stored = _fetchone(conn, "SELECT * FROM screening_answers WHERE id = ?", (answer_id,))
        assert stored is not None
        return _answer_from_row(stored)

    def find_answer(
        self, intent: str | None = None, question_norm: str | None = None
    ) -> ScreeningAnswer | None:
        """Exact lookup: the answer saved for ``intent`` first, else for the normalised question text.

        No fuzzy matching here (the answer engine does that). ``question_norm`` is passed through
        ``norm_text`` (idempotent), so raw question text works too.
        """
        conn = self.db.connect()
        if intent and intent.strip():
            row = _fetchone(
                conn, "SELECT * FROM screening_answers WHERE intent = ?", (intent.strip(),)
            )
            if row is not None:
                return _answer_from_row(row)
        norm = norm_text(question_norm)
        if norm:
            row = _fetchone(
                conn,
                "SELECT * FROM screening_answers WHERE question_norm = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                (norm,),
            )
            if row is not None:
                return _answer_from_row(row)
        return None

    def list_answers(self) -> list[ScreeningAnswer]:
        """Every saved answer, most recently updated first."""
        rows = _fetchall(
            self.db.connect(), "SELECT * FROM screening_answers ORDER BY updated_at DESC, id DESC"
        )
        return [_answer_from_row(r) for r in rows]

    def delete_answer(self, answer_id: int) -> bool:
        """Delete a saved answer (its intent becomes free again). False if the id is unknown."""
        with self.db.transaction() as conn:
            return (
                conn.execute("DELETE FROM screening_answers WHERE id = ?", (answer_id,)).rowcount
                > 0
            )

    def touch_answer(self, answer_id: int) -> bool:
        """Increment ``use_count`` (an answer was just used on a form). False if the id is unknown."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE screening_answers SET use_count = use_count + 1 WHERE id = ?", (answer_id,)
            )
            return cursor.rowcount > 0

    # ================================================================================== pending questions
    def add_pending_question(self, question: PendingQuestion) -> PendingQuestion:
        """Queue a question for the user, deduplicated by (``norm_text(question)``, ``opportunity_id``).

        If an UNRESOLVED question with the same key exists it is returned instead of adding a duplicate; a
        resolved one is history, so the same question can be queued again. Raises ``ValueError`` for blank
        question text (an answer saved for "no text" would match every unlabeled field).
        """
        norm = norm_text(question.question)
        if not norm:
            raise ValueError("pending question text is empty")
        opportunity_id = question.opportunity_id or None
        with self.db.transaction() as conn:
            existing = _fetchone(
                conn,
                "SELECT * FROM pending_questions WHERE question_norm = ? AND opportunity_id IS ? "
                "AND resolved = 0 ORDER BY id LIMIT 1",
                (norm, opportunity_id),
            )
            if existing is not None:
                return _pending_from_row(existing)
            cursor = conn.execute(
                "INSERT INTO pending_questions (question, question_norm, kind, options, opportunity_id, "
                "company, created_at, resolved) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    question.question,
                    norm,
                    question.kind.value,
                    _dumps(list(question.options)),
                    opportunity_id,
                    question.company,
                    _iso(self._now()),
                ),
            )
            stored = _fetchone(
                conn, "SELECT * FROM pending_questions WHERE id = ?", (cursor.lastrowid,)
            )
            assert stored is not None
            return _pending_from_row(stored)

    def list_pending_questions(
        self, unresolved_only: bool = True, opportunity_id: str | None = None
    ) -> list[PendingQuestion]:
        """Pending questions, oldest first (the dashboard queue). Optionally only one opportunity's."""
        clauses: list[str] = []
        params: list[Any] = []
        if unresolved_only:
            clauses.append("resolved = 0")
        if opportunity_id is not None:
            clauses.append("opportunity_id = ?")
            params.append(opportunity_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = _fetchall(
            self.db.connect(),
            f"SELECT * FROM pending_questions{where} ORDER BY created_at ASC, id ASC",
            params,
        )
        return [_pending_from_row(r) for r in rows]

    def resolve_pending_question(self, question_id: int, answer_text: str) -> PendingQuestion:
        """Mark the question resolved AND save the answer (source ``"user"``) under its normalised text.

        The saved answer is then found by ``find_answer(question_norm=...)``. Only this row is resolved
        (the same wording pending for another company stays in the queue: the answer may differ per company).
        Raises ``NotFoundError`` for an unknown id and ``ValueError`` for a blank answer.
        """
        text = answer_text.strip()
        if not text:
            raise ValueError("answer_text is empty")
        with self.db.transaction() as conn:
            row = _fetchone(conn, "SELECT * FROM pending_questions WHERE id = ?", (question_id,))
            if row is None:
                raise NotFoundError(f"unknown pending question {question_id!r}")
            kind = _ANSWER_KIND_BY_QUESTION_KIND.get(QuestionKind(row["kind"]), "text")
            self._upsert_answer(
                conn,
                ScreeningAnswer.model_validate(
                    {
                        "question": row["question"],
                        "question_norm": row["question_norm"],
                        "answer": text,
                        "answer_kind": kind,
                        "source": "user",
                    }
                ),
            )
            conn.execute(
                "UPDATE pending_questions SET resolved = 1, resolved_at = ?, answer_text = ? WHERE id = ?",
                (_iso(self._now()), text, question_id),
            )
            resolved = _fetchone(
                conn, "SELECT * FROM pending_questions WHERE id = ?", (question_id,)
            )
            assert resolved is not None
            return _pending_from_row(resolved)

    # ================================================================================== ATS accounts
    def get_ats_account(self, host: str, email: str) -> AtsAccountRecord | None:
        """Account metadata for a tenant host + email (case-insensitive). Never any password."""
        row = _fetchone(
            self.db.connect(),
            "SELECT * FROM ats_accounts WHERE host = ? AND email = ?",
            (host.strip(), email.strip()),
        )
        return _account_from_row(row) if row else None

    def upsert_ats_account(
        self, host: str, email: str, verified: bool | None = None, login_ok: bool = False
    ) -> AtsAccountRecord:
        """Create the record or update it. ``verified=None`` leaves the flag as is (new rows: False);
        ``login_ok=True`` stamps ``last_login_ok_at`` with now."""
        host, email = host.strip(), email.strip()
        if not host or not email:
            raise ValueError("host and email are required")
        with self.db.transaction() as conn:
            now_iso = _iso(self._now())
            row = _fetchone(
                conn, "SELECT * FROM ats_accounts WHERE host = ? AND email = ?", (host, email)
            )
            if row is None:
                conn.execute(
                    "INSERT INTO ats_accounts (host, email, verified, created_at, last_login_ok_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (host, email, 1 if verified else 0, now_iso, now_iso if login_ok else None),
                )
            else:
                conn.execute(
                    "UPDATE ats_accounts SET verified = ?, last_login_ok_at = ? WHERE id = ?",
                    (
                        row["verified"] if verified is None else (1 if verified else 0),
                        now_iso if login_ok else row["last_login_ok_at"],
                        row["id"],
                    ),
                )
            stored = _fetchone(
                conn, "SELECT * FROM ats_accounts WHERE host = ? AND email = ?", (host, email)
            )
            assert stored is not None
            return _account_from_row(stored)

    # ================================================================================== runs
    def start_run(self, mode: RunMode | str, trigger: str) -> int:
        """Open a run record and return its id. ``trigger`` is one of manual/schedule/cli/test."""
        RunReport.model_validate(
            {"mode": RunMode(mode), "trigger": trigger}
        )  # fail early, not on read
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (mode, trigger, started_at) VALUES (?, ?, ?)",
                (RunMode(mode).value, trigger, _iso(self._now())),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def finish_run(self, run_id: int, report: RunReport) -> RunReport:
        """Store the final ``report`` for the run and stamp ``finished_at`` (``report.finished_at`` or now).

        The run's own id, mode, trigger and start time win over whatever the report carries. Returns the
        stored report. Raises ``NotFoundError`` for an unknown run.
        """
        with self.db.transaction() as conn:
            if _fetchone(conn, "SELECT 1 FROM runs WHERE id = ?", (run_id,)) is None:
                raise NotFoundError(f"unknown run {run_id!r}")
            finished = report.finished_at or self._now()
            conn.execute(
                "UPDATE runs SET finished_at = ?, report = ? WHERE id = ?",
                (_iso(finished), report.model_dump_json(), run_id),
            )
            stored = _fetchone(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
            assert stored is not None
            return _run_from_row(stored)

    def get_run(self, run_id: int) -> RunReport | None:
        """One run's report (unfinished runs have ``finished_at=None``), or None."""
        row = _fetchone(self.db.connect(), "SELECT * FROM runs WHERE id = ?", (run_id,))
        return _run_from_row(row) if row else None

    def list_runs(self, limit: int | None = 50) -> list[RunReport]:
        """Runs, newest first. Unfinished runs come back with ``finished_at=None`` and zeroed counters."""
        lim, _ = _limit_offset(limit, 0)
        rows = _fetchall(self.db.connect(), "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (lim,))
        return [_run_from_row(r) for r in rows]

    # -- cross-process run lock ------------------------------------------------------------------
    def acquire_run_lock(self, owner: str, ttl_s: float = 600.0) -> bool:
        """Try to take the single run lock for ``ttl_s`` seconds. Atomic across threads AND processes.

        Succeeds if nobody holds it or the holder's lease has expired (a crashed run is taken over once its
        TTL lapses). Fails - even for the same ``owner`` - while an unexpired lease exists, so ``owner``
        should be unique per run/process (e.g. hostname + pid + uuid). Extend with ``heartbeat_run_lock``.
        """
        _check_lock_args(owner, ttl_s)
        with self.db.transaction() as conn:
            now = self._now()
            row = _fetchone(conn, "SELECT expires_at FROM run_lock WHERE id = 1")
            if row is not None:
                expires = _parse_dt(row["expires_at"])
                if expires is not None and expires > now:
                    return False
            now_iso = _iso(now)
            conn.execute(
                "INSERT INTO run_lock (id, owner, acquired_at, heartbeat_at, expires_at, ttl_s) "
                "VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET owner = excluded.owner, "
                "acquired_at = excluded.acquired_at, heartbeat_at = excluded.heartbeat_at, "
                "expires_at = excluded.expires_at, ttl_s = excluded.ttl_s",
                (owner, now_iso, now_iso, _iso(now + timedelta(seconds=ttl_s)), ttl_s),
            )
            return True

    def heartbeat_run_lock(self, owner: str, ttl_s: float | None = None) -> bool:
        """Extend the lease to now + ``ttl_s`` (default: the TTL it was acquired with).

        True while ``owner`` is still the recorded holder (even if the lease lapsed but nobody took over
        yet); False once another owner took the lock or it was released - the run must then stop.
        """
        if ttl_s is not None:
            _check_lock_args(owner, ttl_s)
        with self.db.transaction() as conn:
            row = _fetchone(conn, "SELECT owner, ttl_s FROM run_lock WHERE id = 1")
            if row is None or row["owner"] != owner:
                return False
            ttl = float(row["ttl_s"]) if ttl_s is None else ttl_s
            now = self._now()
            conn.execute(
                "UPDATE run_lock SET heartbeat_at = ?, expires_at = ?, ttl_s = ? WHERE id = 1",
                (_iso(now), _iso(now + timedelta(seconds=ttl)), ttl),
            )
            return True

    def release_run_lock(self, owner: str) -> bool:
        """Release the lock, only if ``owner`` holds it. Returns whether it was released."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM run_lock WHERE id = 1 AND owner = ?", (owner,))
            return cursor.rowcount > 0

    def get_run_lock(self) -> RunLockInfo | None:
        """The current lock record (``expired`` says whether its lease has lapsed), or None."""
        row = _fetchone(self.db.connect(), "SELECT * FROM run_lock WHERE id = 1")
        if row is None:
            return None
        acquired = _parse_dt(row["acquired_at"]) or _EPOCH
        heartbeat = _parse_dt(row["heartbeat_at"]) or _EPOCH
        expires = _parse_dt(row["expires_at"]) or _EPOCH  # unreadable expiry == long expired
        return RunLockInfo(
            owner=row["owner"],
            acquired_at=acquired,
            heartbeat_at=heartbeat,
            expires_at=expires,
            ttl_s=float(row["ttl_s"]),
            expired=expires <= self._now(),
        )

    # ================================================================================== kv
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        """The stored string, or ``default`` when the key is missing (an empty string IS a value)."""
        row = _fetchone(self.db.connect(), "SELECT value FROM kv WHERE key = ?", (key,))
        return default if row is None else str(row["value"])

    def set_kv(self, key: str, value: str) -> None:
        """Insert or overwrite ``key``. Values are strings: encode structured data (JSON) yourself."""
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE "
                "SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _iso(self._now())),
            )

    def delete_kv(self, key: str) -> bool:
        """Remove a key. False if it was not there."""
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM kv WHERE key = ?", (key,)).rowcount > 0

    # ================================================================================== stats
    def stats(self, tz: str | ZoneInfo = "America/Chicago") -> RepoStats:
        """Dashboard numbers from one consistent snapshot; ``submitted_today`` uses the local day in ``tz``."""
        today = local_day(self._now(), tz)
        with self.db.snapshot() as conn:
            opp = _fetchone(
                conn,
                "SELECT COUNT(*) AS total, COALESCE(SUM(is_open), 0) AS open_n, "
                "COUNT(score) AS scored, COALESCE(SUM(score_passed), 0) AS passed FROM opportunities",
            )
            assert opp is not None
            by_source = {s.value: 0 for s in OpportunitySource}
            for r in _fetchall(
                conn, "SELECT source, COUNT(*) AS n FROM opportunities GROUP BY source"
            ):
                by_source[r["source"]] = int(r["n"])
            by_status = {s.value: 0 for s in ApplicationStatus}
            for r in _fetchall(
                conn, "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
            ):
                by_status[r["status"]] = int(r["n"])
            submitted_today = self.count_submitted_on(today, tz)
        return RepoStats(
            day=today.isoformat(),
            opportunities_total=int(opp["total"]),
            opportunities_open=int(opp["open_n"]),
            opportunities_scored=int(opp["scored"]),
            opportunities_passed=int(opp["passed"]),
            opportunities_by_source=by_source,
            applications_total=sum(by_status.values()),
            applications_by_status=by_status,
            submitted_today=submitted_today,
        )


def _check_lock_args(owner: str, ttl_s: float) -> None:
    if not owner or not owner.strip():
        raise ValueError("run lock owner must be a non-empty string")
    if not ttl_s > 0:
        raise ValueError("run lock ttl_s must be positive")


__all__ = [
    "BUSY_TIMEOUT_MS",
    "MIGRATIONS",
    "AtsAccountRecord",
    "Database",
    "Migration",
    "NotFoundError",
    "Repo",
    "RepoStats",
    "RunLockInfo",
    "SchemaVersionError",
    "latest_schema_version",
    "merge_opportunities",
]
