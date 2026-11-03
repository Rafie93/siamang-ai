"""SQLite connection + schema initialization.

No migration framework, just an ordered list of SQL steps
(:data:`SCHEMA_STEPS`). Step *n* brings the database from version *n-1* to
*n*; ``PRAGMA user_version`` records the version. A fresh database runs every
step; an older one runs only the missing steps, all in one transaction.
Steps are append-only: an applied step is never edited.
"""

from __future__ import annotations

import contextlib
import itertools
import sqlite3
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

SCHEMA_STEPS = (
    "schema.sql",  # v1, Phase 3/05: tasks, events
    "schema_v2.sql",  # v2, Phase 07: projects
    "schema_v3.sql",  # v3, Phase 10: tasks rebuilt for the Task Engine
)
SCHEMA_VERSION = len(SCHEMA_STEPS)
EXPECTED_TABLES = frozenset({"tasks", "events", "projects"})
EXPECTED_TRIGGERS = frozenset(
    {
        "events_no_update",
        "events_no_delete",
        "projects_no_delete",
        "projects_immutable_identity",
        "tasks_no_delete",
        "tasks_immutable_identity",
    }
)


class StorageError(Exception):
    """Base class for storage failures."""


class SchemaError(StorageError):
    """Database schema is missing, too new, or damaged."""


class DuplicateError(StorageError):
    """A unique key (task_id, request_id, event_id, (task_id, seq)) already exists."""


class SequenceError(StorageError):
    """An event's ``seq`` is not strictly greater than the task's last ``seq``."""


class NotFoundError(StorageError):
    """Referenced row does not exist."""


def _schema_sql(name: str) -> str:
    return (resources.files("siamang.storage") / name).read_text(encoding="utf-8")


class Database:
    """One SQLite connection with schema guaranteed at :data:`SCHEMA_VERSION`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._savepoints = itertools.count(1)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise SchemaError(f"database schema v{version} is newer than supported v{SCHEMA_VERSION}")
        if version == SCHEMA_VERSION:
            return
        with self.transaction():
            for step in range(version, SCHEMA_VERSION):
                for statement in _split_sql(_schema_sql(SCHEMA_STEPS[step])):
                    self.conn.execute(statement)
                self.conn.execute(f"PRAGMA user_version = {step + 1}")

    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` ... ``COMMIT``; any exception rolls back.

        Re-entrant: inside an open transaction a nested call becomes a
        ``SAVEPOINT``, so composed writes (e.g. a task state change plus its
        event append) commit or roll back together with the outermost block.
        """
        if self.conn.in_transaction:
            name = f"sp_{next(self._savepoints)}"
            self.conn.execute(f"SAVEPOINT {name}")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute(f"ROLLBACK TO {name}")
                self.conn.execute(f"RELEASE {name}")
                raise
            self.conn.execute(f"RELEASE {name}")
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def check(self) -> list[str]:
        """Return a list of schema problems (empty = healthy)."""
        problems = []
        if (v := self.schema_version()) != SCHEMA_VERSION:
            problems.append(f"schema version {v}, expected {SCHEMA_VERSION}")
        rows = self.conn.execute("SELECT type, name FROM sqlite_schema").fetchall()
        tables = {r["name"] for r in rows if r["type"] == "table"}
        triggers = {r["name"] for r in rows if r["type"] == "trigger"}
        for name in sorted(EXPECTED_TABLES - tables):
            problems.append(f"missing table {name}")
        for name in sorted(EXPECTED_TRIGGERS - triggers):
            problems.append(f"missing trigger {name}")
        result = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            problems.append(f"quick_check: {result}")
        return problems

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _split_sql(script: str) -> list[str]:
    """Split on complete statements (handles trigger bodies) using sqlite3.complete_statement."""
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        if line.lstrip().startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise SchemaError("schema.sql ends with an incomplete statement")
    return statements
