"""Project Registry: identity, metadata, and lifecycle of engineering projects.

Synchronous and persistent (SQLite via :class:`ProjectStore`). Each write is
one ``BEGIN IMMEDIATE`` transaction: validate, check conflicts, write,
commit. Any failure rolls back, so no partial project is ever stored.

Rules:

* ``project_id`` (``prj_<ULID>``) is the only identity. Name, path, and
  metadata may all change; the ID never does.
* ``name`` is unique (case-insensitively) among **active** projects.
* ``root_path`` is unique among **active** projects.
* Archived projects are read-only and keep their name/path as history;
  ``restore`` fails if an active project now holds either.
* There is no delete (the schema forbids it).
* A write that changes nothing is a no-op: nothing is written and
  ``updated_at`` is unchanged.

The registry stores where a project lives. It never lists, reads, or runs
anything inside it, and never invokes Git, models, agents, or tasks.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from siamang.contracts.envelope import format_timestamp, parse_timestamp, utc_now
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.projects.definition import (
    Project,
    ProjectStatus,
    ProjectType,
    canonical_metadata,
    freeze_metadata,
    normalize_name,
    normalize_root_path,
    parse_type,
    validate_description,
    validate_project_id,
)
from siamang.projects.errors import (
    DuplicateProjectError,
    InvalidProjectError,
    ProjectArchivedError,
    ProjectNotFoundError,
)
from siamang.storage.database import Database, DuplicateError
from siamang.storage.projects import ProjectRow, ProjectStore

log = get_logger("projects")


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


def _to_project(row: ProjectRow) -> Project:
    return Project(
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        project_type=ProjectType(row.project_type),
        status=ProjectStatus(row.status),
        root_path=row.root_path,
        metadata=freeze_metadata(row.metadata),
        created_at=parse_timestamp(row.created_at),
        updated_at=parse_timestamp(row.updated_at),
    )


def _check_exists(path: str) -> None:
    # One stat call. The directory is never listed or read.
    if not os.path.isdir(path):
        raise InvalidProjectError("root_path", "does not exist or is not a directory")


class ProjectRegistry:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utc_now):
        self.db = db
        self.store = ProjectStore(db)
        self._clock = clock

    # -- writes ------------------------------------------------------------------

    def register(
        self,
        *,
        name: str,
        root_path: str | os.PathLike[str],
        project_type: ProjectType | str = ProjectType.OTHER,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
        project_id: str | None = None,
        check_exists: bool = False,
    ) -> Project:
        pid = validate_project_id(project_id) if project_id is not None else new_id("project")
        name, name_key = normalize_name(name)
        path = normalize_root_path(root_path)
        ptype = parse_type(project_type)
        desc = validate_description(description)
        meta = canonical_metadata(metadata)
        if check_exists:
            _check_exists(path)
        ts = format_timestamp(self._clock())
        row = ProjectRow(pid, name, name_key, desc, str(ptype), str(ProjectStatus.ACTIVE), path, meta, ts, ts)

        with self.db.transaction():
            if self.store.get(pid) is not None:
                raise DuplicateProjectError("project_id", pid)
            self._check_active_conflicts(row)
            self._insert(row)
        log.info("registered project %s (%s)", pid, ptype)
        return _to_project(row)

    def update(
        self,
        project_id: str,
        *,
        name: str = UNSET,
        description: str = UNSET,
        project_type: ProjectType | str = UNSET,
        root_path: str | os.PathLike[str] = UNSET,
        metadata: Mapping[str, Any] | None = UNSET,
        check_exists: bool = False,
    ) -> Project:
        """Change mutable fields. ``metadata`` replaces the whole object (no merge)."""
        changes: dict[str, Any] = {}
        if name is not UNSET:
            changes["name"], changes["name_key"] = normalize_name(name)
        if description is not UNSET:
            changes["description"] = validate_description(description)
        if project_type is not UNSET:
            changes["project_type"] = str(parse_type(project_type))
        if root_path is not UNSET:
            changes["root_path"] = normalize_root_path(root_path)
            if check_exists:
                _check_exists(changes["root_path"])
        if metadata is not UNSET:
            changes["metadata"] = canonical_metadata(metadata)

        with self.db.transaction():
            current = self._get_row(project_id)
            if current.status != ProjectStatus.ACTIVE:
                raise ProjectArchivedError(f"project {project_id} is archived; restore it before updating")
            new = replace(current, **changes)
            if new == current:
                return _to_project(current)
            new = replace(new, updated_at=self._next_timestamp(current))
            self._check_active_conflicts(new)
            self._write(new)
        log.info("updated project %s (%s)", project_id, ", ".join(sorted(k for k in changes if k != "name_key")))
        return _to_project(new)

    def archive(self, project_id: str) -> Project:
        return self._set_status(project_id, ProjectStatus.ARCHIVED)

    def restore(self, project_id: str) -> Project:
        return self._set_status(project_id, ProjectStatus.ACTIVE)

    # -- reads -------------------------------------------------------------------

    def get(self, project_id: str) -> Project:
        return _to_project(self._get_row(project_id))

    def exists(self, project_id: str) -> bool:
        return self.store.get(project_id) is not None

    def get_by_name(self, name: str) -> Project:
        """The active project with this name (case-insensitive, whitespace-collapsed)."""
        _, key = normalize_name(name)
        row = self.store.active_by_name_key(key)
        if row is None:
            raise ProjectNotFoundError("no active project with that name")
        return _to_project(row)

    def find_by_root_path(self, root_path: str | os.PathLike[str]) -> Project | None:
        """The active project registered at exactly this (normalized) path, if any."""
        row = self.store.active_by_root(normalize_root_path(root_path))
        return _to_project(row) if row else None

    def list(
        self,
        *,
        status: ProjectStatus | str | None = None,
        project_type: ProjectType | str | None = None,
    ) -> list[Project]:
        """Sorted by name (case-insensitive), then project_id."""
        if status is not None:
            try:
                status = ProjectStatus(status)
            except ValueError:
                raise InvalidProjectError("status", f"must be one of {[s.value for s in ProjectStatus]}") from None
        ptype = parse_type(project_type) if project_type is not None else None
        rows = self.store.list(
            status=str(status) if status is not None else None,
            project_type=str(ptype) if ptype is not None else None,
        )
        return [_to_project(r) for r in rows]

    # -- internals -----------------------------------------------------------------

    def _get_row(self, project_id: str) -> ProjectRow:
        row = self.store.get(project_id) if isinstance(project_id, str) else None
        if row is None:
            raise ProjectNotFoundError(f"unknown project: {project_id}")
        return row

    def _set_status(self, project_id: str, status: ProjectStatus) -> Project:
        with self.db.transaction():
            current = self._get_row(project_id)
            if current.status == status:
                return _to_project(current)  # idempotent no-op
            new = replace(current, status=str(status), updated_at=self._next_timestamp(current))
            if status is ProjectStatus.ACTIVE:
                self._check_active_conflicts(new)
            self._write(new)
        log.info("%s project %s", "restored" if status is ProjectStatus.ACTIVE else "archived", project_id)
        return _to_project(new)

    def _check_active_conflicts(self, row: ProjectRow) -> None:
        other = self.store.active_by_name_key(row.name_key)
        if other is not None and other.project_id != row.project_id:
            raise DuplicateProjectError("name", other.project_id)
        other = self.store.active_by_root(row.root_path)
        if other is not None and other.project_id != row.project_id:
            raise DuplicateProjectError("root_path", other.project_id)

    def _next_timestamp(self, current: ProjectRow) -> str:
        """Now, but never earlier than the previous ``updated_at`` (clock skew safe)."""
        now = self._clock()
        previous = parse_timestamp(current.updated_at)
        return format_timestamp(max(now, previous))

    def _insert(self, row: ProjectRow) -> None:
        try:
            self.store.insert(row)
        except DuplicateError:  # backstop: the unique indexes disagree with our pre-checks
            raise DuplicateProjectError("project", row.project_id) from None

    def _write(self, row: ProjectRow) -> None:
        try:
            self.store.update(row)
        except DuplicateError:
            raise DuplicateProjectError("project", row.project_id) from None
