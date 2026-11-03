"""Task persistence (SQL only). Replaces the Phase 3 ``TaskStore``.

``TaskRepository`` stores and loads :class:`TaskRow` values (plain strings
and integers). It knows nothing about the task lifecycle and imports nothing
from :mod:`siamang.tasks`: rules, validation, and events live in the Task
Engine (``tasks.engine``), which is the only intended caller.

Writes join the caller's transaction (``Database.transaction`` is
re-entrant), so the engine can update a task and append its event
atomically. ``update`` is compare-and-set: it only writes if the row still
has the state the caller read, so two concurrent writers cannot both win.
"""

from __future__ import annotations

import sqlite3
from dataclasses import astuple, dataclass, fields

from siamang.storage.database import Database, DuplicateError

LIST_LIMIT_MAX = 1000


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    request_id: str
    project_id: str
    parent_task_id: str | None
    title: str
    description: str
    status: str
    priority: str
    source: str
    retry_count: int
    max_retries: int
    metadata: str  # canonical JSON object
    last_error: str | None  # JSON object or None
    result: str | None  # JSON object or None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    failed_at: str | None
    cancelled_at: str | None


COLUMNS = tuple(f.name for f in fields(TaskRow))
_MUTABLE = tuple(c for c in COLUMNS if c not in ("task_id", "request_id", "project_id", "parent_task_id",
                                                  "created_at"))


def _row(r: sqlite3.Row) -> TaskRow:
    return TaskRow(*(r[c] for c in COLUMNS))


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, row: TaskRow) -> None:
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    f"INSERT INTO tasks ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                    astuple(row),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) or "PRIMARY KEY" in str(exc):
                raise DuplicateError("task_id or request_id already exists") from None
            raise

    def update(self, new: TaskRow, *, expected: TaskRow) -> bool:
        """Write ``new`` only if the stored row still equals ``expected``. Returns whether it did."""
        if new.task_id != expected.task_id:
            raise ValueError("update cannot change task_id")
        sets = ", ".join(f"{c} = ?" for c in _MUTABLE)
        where = " AND ".join(f"{c} IS ?" for c in _MUTABLE)
        values = [getattr(new, c) for c in _MUTABLE] + [new.task_id] + [getattr(expected, c) for c in _MUTABLE]
        with self.db.transaction() as conn:
            cur = conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = ? AND {where}", values)
        return cur.rowcount == 1

    def get(self, task_id: str) -> TaskRow | None:
        r = self.db.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _row(r) if r else None

    def get_by_request_id(self, request_id: str) -> TaskRow | None:
        r = self.db.conn.execute("SELECT * FROM tasks WHERE request_id = ?", (request_id,)).fetchone()
        return _row(r) if r else None

    def exists(self, task_id: str) -> bool:
        return self.db.conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is not None

    def list(
        self,
        *,
        project_id: str | None = None,
        status: str | tuple[str, ...] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskRow]:
        """Ordered by ``created_at``, then ``task_id``. One query; filters are bound parameters."""
        for name, value, low, high in (("limit", limit, 1, LIST_LIMIT_MAX), ("offset", offset, 0, 2**31)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        where, params = [], []
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if status is not None:
            statuses = (status,) if isinstance(status, str) else tuple(status)
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            params.extend(statuses)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created_at, task_id LIMIT ? OFFSET ?"
        rows = self.db.conn.execute(sql, [*params, limit, offset]).fetchall()
        return [_row(r) for r in rows]

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
