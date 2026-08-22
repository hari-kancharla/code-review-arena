"""SQLite connection lifecycle with versioned, idempotent migrations."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from arena.core.errors import StorageError

SCHEMA_VERSION = 2
# Bounded retry for the one-off WAL switch when several connections open a brand
# new database at once (see _enable_wal); ~0.5s worst case.
_WAL_ATTEMPTS = 10
_WAL_RETRY_SECONDS = 0.05


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    _enable_wal(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    _migrate(connection)
    return connection


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Put the database in WAL mode, tolerating a concurrent first touch.

    Changing journal mode needs a brief exclusive lock, and SQLite answers
    SQLITE_BUSY for it without consulting the busy handler, so several
    connections opening a brand-new database in the same instant could not all
    perform the switch and the losers raised `database is locked` out of
    connect(). The journal mode is a persistent property of the database file,
    so a connection that loses this race still ends up using the mode the winner
    set; retry briefly, then continue in whatever mode is in force.
    """
    for attempt in range(_WAL_ATTEMPTS):
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == _WAL_ATTEMPTS - 1:
                return
            time.sleep(_WAL_RETRY_SECONDS)


def _migrate_v1(connection: sqlite3.Connection) -> None:
    """Base schema plus the columns that were added before versioning existed."""
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    # Executed statement by statement rather than through executescript(), which
    # issues an implicit COMMIT and would end the BEGIN IMMEDIATE transaction
    # that serializes this migration, reopening the race it exists to close.
    # schema.sql is repo-owned and holds only simple terminated statements.
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)
    _add_columns(
        connection,
        "runs",
        {
            "beta": "REAL",
            "deterministic_precision": "REAL",
            "deterministic_recall": "REAL",
            "deterministic_f1": "REAL",
            "deterministic_f_beta": "REAL",
            "patch_apply_rate": "REAL",
            "test_pass_rate": "REAL",
            "structural_pass_rate": "REAL",
            "false_positives_per_case": "REAL",
            "cost_per_true_positive": "REAL",
            "detection_precision": "REAL",
            "detection_recall": "REAL",
            "detection_f1": "REAL",
            "detection_f_beta": "REAL",
            "validated_precision": "REAL",
            "validated_recall": "REAL",
            "validated_f1": "REAL",
            "validated_f_beta": "REAL",
            "deterministic_pass_rate": "REAL",
            "cost_per_validated_fix": "REAL",
            "latency_per_case_ms": "REAL",
        },
    )
    _add_columns(
        connection,
        "case_results",
        {
            "deterministic_pass": "BOOLEAN",
            "patch_provided": "BOOLEAN",
            "patch_applied": "BOOLEAN",
            "tests_ran": "BOOLEAN",
            "tests_passed": "BOOLEAN",
            "structural_validation_ran": "BOOLEAN",
            "structural_validation_passed": "BOOLEAN",
            "failure_reasons_json": "TEXT",
            "patch_error": "TEXT",
        },
    )


def _migrate_v2(connection: sqlite3.Connection) -> None:
    """Run-validity and coverage columns, plus a coherent case-level metric.

    Every row that already exists when this runs predates run validity, so it is
    marked legacy: it stays queryable but is excluded from v2 comparisons.
    """
    _add_columns(
        connection,
        "runs",
        {
            "schema_version": "INTEGER",
            "run_status": "TEXT",
            "execution_backend": "TEXT",
            "eligible_case_count": "INTEGER",
            "completed_case_count": "INTEGER",
            "failed_case_count": "INTEGER",
            "skipped_case_count": "INTEGER",
            "coverage_rate": "REAL",
            "validated_case_rate": "REAL",
        },
    )
    connection.execute(
        "UPDATE runs SET run_status = 'legacy', schema_version = 1 WHERE run_status IS NULL"
    )


# Ordered migration steps; entry N migrates a version-(N-1) database to N.
_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1, _migrate_v2]


def _too_new(version: int) -> StorageError:
    return StorageError(
        f"database schema version {version} is newer than this arena understands "
        f"({SCHEMA_VERSION}); upgrade codereview-arena instead of downgrading the database"
    )


def _migrate(connection: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, serialized against other openers.

    The migration is guarded by BEGIN IMMEDIATE, which takes SQLite's write lock
    up front, and re-reads the version inside that transaction (double-checked
    locking). Without it, two connections first-touching the same database both
    saw an unmigrated version and both ran ALTER TABLE, and the loser died with
    `duplicate column name`, which is not an ArenaError and so escaped the CLI's
    error handling entirely. busy_timeout (set in connect) makes the loser wait
    for the winner rather than fail, after which it finds nothing left to do.
    """
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise _too_new(version)

    previous_isolation = connection.isolation_level
    connection.isolation_level = None  # explicit transaction control below
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise _too_new(version)
            if version < SCHEMA_VERSION:
                for step in range(version, SCHEMA_VERSION):
                    _MIGRATIONS[step](connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.isolation_level = previous_isolation


def _add_columns(connection: sqlite3.Connection, table: str, expected: dict[str, str]) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, data_type in expected.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {data_type}")
