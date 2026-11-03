"""Patch: a self-consistent, immutable set of proposed changes for one task. Never applied here.

Validated on construction:

* identity: ``patch_id`` (``pch_<id>``), task and project IDs;
* changes: exact :class:`ProposedChange` objects, at most :data:`MAX_CHANGES_LIMIT`,
  unique change IDs;
* **one operation per path**: the same path twice (create+create,
  modify+modify, delete+modify, create+delete, ...) is a conflict, and so
  are two paths that differ only by case (one file on case-insensitive
  filesystems);
* **file/directory conflicts**: a path that is a directory prefix of another
  changed path (``src`` and ``src/app.py``) is refused;
* bounded total proposed content (:data:`MAX_TOTAL_LIMIT`).

``content()`` / ``content_hash`` exclude ``patch_id``: the same changes give
the same hash, different proposed content a different one.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from siamang.log import redact
from siamang.planner.request import text_field
from siamang.programmer.change import ProposedChange
from siamang.programmer.errors import InvalidPatchError
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

PATCH_ID_RE = re.compile(r"^pch_[A-Za-z0-9]{1,64}\Z")
MAX_CHANGES_LIMIT = 50
MAX_TOTAL_LIMIT = 4 * 1024 * 1024
SUMMARY_LIMIT = 4000


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_changes(changes: Any) -> tuple[ProposedChange, ...]:
    if isinstance(changes, (str, bytes)) or not isinstance(changes, (list, tuple)):
        raise InvalidPatchError("changes must be a list of ProposedChange", details=("changes",))
    if len(changes) > MAX_CHANGES_LIMIT:
        raise InvalidPatchError(f"a patch has at most {MAX_CHANGES_LIMIT} changes", details=("changes",))
    if not all(type(c) is ProposedChange for c in changes):
        raise InvalidPatchError("changes must be ProposedChange objects", details=("changes",))
    changes = tuple(changes)
    ids = [c.change_id for c in changes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise InvalidPatchError("change IDs must be unique", details=tuple(duplicates))
    seen: dict[str, ProposedChange] = {}
    for c in changes:
        key = c.path.casefold()
        if key in seen:
            raise InvalidPatchError("one operation per path: conflicting changes", details=(seen[key].change_id,
                                                                                            c.change_id))
        seen[key] = c
    for key, c in seen.items():
        parts = key.split("/")
        for i in range(1, len(parts)):
            parent = seen.get("/".join(parts[:i]))
            if parent is not None:
                raise InvalidPatchError("a changed file cannot also be a directory of another change",
                                        details=(parent.change_id, c.change_id))
    total = sum(c.size for c in changes)
    if total > MAX_TOTAL_LIMIT:
        raise InvalidPatchError(f"proposed content exceeds {MAX_TOTAL_LIMIT} bytes", details=("changes",))
    return changes


@dataclass(frozen=True)
class Patch:
    patch_id: str
    task_id: str
    project_id: str
    summary: str
    changes: tuple[ProposedChange, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.patch_id, str) or not PATCH_ID_RE.fullmatch(self.patch_id):
            raise InvalidPatchError("patch_id must look like pch_<id>", details=("patch_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise InvalidPatchError(f"{name} is not a valid identifier", details=(name,)) from None
        object.__setattr__(self, "summary", redact(text_field(self.summary, "summary", SUMMARY_LIMIT, multiline=True,
                                                              error=InvalidPatchError)))
        object.__setattr__(self, "changes", validate_changes(self.changes))

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes)

    @property
    def size(self) -> int:
        """Total bytes of proposed content."""
        return sum(c.size for c in self.changes)

    def content(self) -> dict[str, Any]:
        """Everything except ``patch_id``: the deterministic, comparable part."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "summary": self.summary,
            "changes": [c.to_dict() for c in self.changes],
        }

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical(self.content()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"patch_id": self.patch_id, **self.content(), "content_hash": self.content_hash}
