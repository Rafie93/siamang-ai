"""Project persistence: SQL only.

Works with plain :class:`ProjectRow` values (strings), not the domain type,
so the dependency direction stays ``projects.registry -> storage -> SQLite``.
Methods do not open transactions; the caller (the registry) wraps
check-then-write sequences in :meth:`Database.transaction` so they are atomic.
There is no delete: the schema forbids it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from siamang.storage.database import Database, DuplicateError, NotFoundError

COLUMNS = (
    "project_id",
    "name",
    "name_key",
    "description",
    "project_type",
    "status",
    "root_path",
    "metadata",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    name: str
    name_key: str
    description: str
    project_type: str
    status: str
    root_path: str
    metadata: str  # canonical JSON object
    created_at: str
    updated_at: str


def _row(r: sqlite3.Row) -> ProjectRow:
    return ProjectRow(**{c: r[c] for c in COLUMNS})


class ProjectStore:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, row: ProjectRow) -> None:
        try:
            self.db.conn.execute(
                f"INSERT INTO projects ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                tuple(getattr(row, c) for c in COLUMNS),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) or "PRIMARY KEY" in str(exc):
                raise DuplicateError(str(exc)) from exc
            raise

    def update(self, row: ProjectRow) -> None:
        """Replace every mutable column of an existing row (identity columns are untouched)."""
        mutable = [c for c in COLUMNS if c not in ("project_id", "created_at")]
        try:
            cur = self.db.conn.execute(
                f"UPDATE projects SET {', '.join(f'{c} = ?' for c in mutable)} WHERE project_id = ?",
                (*(getattr(row, c) for c in mutable), row.project_id),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateError(str(exc)) from exc
            raise
        if cur.rowcount == 0:
            raise NotFoundError(f"project not found: {row.project_id}")

    def get(self, project_id: str) -> ProjectRow | None:
        r = self.db.conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        return _row(r) if r else None

    def active_by_name_key(self, name_key: str) -> ProjectRow | None:
        r = self.db.conn.execute(
            "SELECT * FROM projects WHERE name_key = ? AND status = 'active'", (name_key,)
        ).fetchone()
        return _row(r) if r else None

    def active_by_root(self, root_path: str) -> ProjectRow | None:
        r = self.db.conn.execute(
            "SELECT * FROM projects WHERE root_path = ? AND status = 'active'", (root_path,)
        ).fetchone()
        return _row(r) if r else None

    def list(self, *, status: str | None = None, project_type: str | None = None) -> list[ProjectRow]:
        sql, where, params = "SELECT * FROM projects", [], []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if project_type is not None:
            where.append("project_type = ?")
            params.append(project_type)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY name_key, project_id"
        return [_row(r) for r in self.db.conn.execute(sql, params).fetchall()]

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
