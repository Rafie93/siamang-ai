"""ProposedChange: one proposed file change. A proposal in memory, never an applied modification.

The mutation vocabulary is deliberately small: ``create``, ``modify``, and
``delete`` of one text file. No rename, move, chmod, symlink, binary patch,
submodule, or Git operation exists.

* ``path`` is project-relative and safe (:func:`~siamang.programmer.safety.validate_path`),
  and never a forbidden file (``.git/``, ``.env*``, keys, credentials, ...).
* ``after_content`` is the complete proposed text for ``create``/``modify``,
  and ``None`` for ``delete``. It must be UTF-8 text without binary control
  characters, within :data:`MAX_FILE_LIMIT` bytes, and free of credential
  material. ``after_hash`` is computed from it, never trusted from outside.
* ``before`` says what is known about the current file:

  - ``absent``: verified not to exist (only valid for ``create``);
  - ``observed``: read through the inspection boundary; ``before_hash`` is
    the SHA-256 of its exact bytes (lets a later Approval System detect
    "the project changed after this proposal");
  - ``unverified``: could not be established (too large, budget spent, not
    valid UTF-8; for ``create``: absence not confirmed). ``before_hash`` is
    ``None``: a hash is never invented.

* ``step_id`` ties the change to a step of the supplied Plan; ``reason`` and
  ``evidence`` say why. Evidence is short, redacted, project-relative text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.log import redact
from siamang.planner.request import json_object, text_field
from siamang.planner.step import STEP_ID_RE
from siamang.programmer.errors import ChangeRejectedError
from siamang.programmer.safety import (
    HASH_RE,
    check_text,
    content_hash,
    deep_freeze,
    find_secret,
    forbidden_reason,
    thaw,
    validate_path,
)

CHANGE_ID_RE = re.compile(r"^change_[0-9]{2,3}\Z")
MAX_FILE_LIMIT = 1024 * 1024  # hard cap per file; the policy is usually stricter
REASON_MAX = 500
EVIDENCE_MAX = 300
EVIDENCE_LINES_MAX = 10
_PARENT_RE = re.compile(r"(?:^|[/\s])\.\.(?:[/\s]|$)")  # a ".." path segment (not "a..b")


class Operation(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class BeforeState(StrEnum):
    ABSENT = "absent"
    OBSERVED = "observed"
    UNVERIFIED = "unverified"


def change_id(position: int) -> str:
    return f"change_{position:02d}"


def _evidence(values: Any, owner: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ChangeRejectedError("evidence must be a list of strings", details=(owner,))
    if len(values) > EVIDENCE_LINES_MAX:
        raise ChangeRejectedError(f"at most {EVIDENCE_LINES_MAX} evidence lines", details=(owner,))
    out = []
    for value in values:
        text = text_field(value, "evidence", EVIDENCE_MAX, error=ChangeRejectedError)
        if text.startswith(("/", "~", "\\")) or _PARENT_RE.search(text) or re.match(r"^[A-Za-z]:[\\/]", text):
            raise ChangeRejectedError("evidence must use project-relative paths", details=(owner,))
        if find_secret(text):
            raise ChangeRejectedError("evidence contains credential material", details=(owner,))
        out.append(redact(text))
    return tuple(out)


@dataclass(frozen=True)
class ProposedChange:
    change_id: str
    path: str
    operation: Operation
    step_id: str
    reason: str
    after_content: str | None = None
    before: BeforeState = BeforeState.UNVERIFIED
    before_hash: str | None = None
    evidence: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    after_hash: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.change_id, str) or not CHANGE_ID_RE.fullmatch(self.change_id):
            raise ChangeRejectedError("change_id must look like change_01", details=("change_id",))
        cid = self.change_id
        try:
            path = validate_path(self.path)
        except ValueError as exc:
            raise ChangeRejectedError(f"path rejected: {exc}", details=(cid,)) from None
        reason = forbidden_reason(path)
        if reason is not None:
            raise ChangeRejectedError(f"path rejected: {reason}", details=(cid, path))
        try:
            operation = Operation(self.operation)
            before = BeforeState(self.before)
        except ValueError:
            raise ChangeRejectedError("operation must be create, modify, or delete; before must be "
                                      "absent, observed, or unverified", details=(cid,)) from None
        if not isinstance(self.step_id, str) or not STEP_ID_RE.fullmatch(self.step_id):
            raise ChangeRejectedError("step_id must look like step_01", details=(cid,))
        text = text_field(self.reason, "reason", REASON_MAX, multiline=True, error=ChangeRejectedError)
        if find_secret(text):
            raise ChangeRejectedError("reason contains credential material", details=(cid,))
        object.__setattr__(self, "reason", redact(text))
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "before", before)

        if operation is Operation.CREATE and before is BeforeState.OBSERVED:
            raise ChangeRejectedError("cannot create a file that already exists", details=(cid, path))
        if operation is not Operation.CREATE and before is BeforeState.ABSENT:
            raise ChangeRejectedError(f"cannot {operation} a file that does not exist", details=(cid, path))
        if before is BeforeState.OBSERVED:
            if not isinstance(self.before_hash, str) or not HASH_RE.fullmatch(self.before_hash):
                raise ChangeRejectedError("an observed file needs its sha256 before_hash", details=(cid,))
        elif self.before_hash is not None:
            raise ChangeRejectedError("before_hash is only set for an observed file", details=(cid,))

        if operation is Operation.DELETE:
            if self.after_content is not None:
                raise ChangeRejectedError("a delete has no after_content", details=(cid,))
            object.__setattr__(self, "after_hash", None)
        else:
            try:
                data = check_text(self.after_content, MAX_FILE_LIMIT)
            except ValueError as exc:
                raise ChangeRejectedError(f"after_content rejected: {exc}", details=(cid, path)) from None
            kind = find_secret(self.after_content)
            if kind is not None:
                raise ChangeRejectedError(f"after_content contains credential material ({kind})", details=(cid, path))
            after_hash = content_hash(data)
            if operation is Operation.MODIFY and after_hash == self.before_hash:
                raise ChangeRejectedError("a modification must change the file", details=(cid, path))
            object.__setattr__(self, "after_hash", after_hash)
        object.__setattr__(self, "evidence", _evidence(self.evidence, cid))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "change.metadata",
                                                                     error=ChangeRejectedError)))

    @property
    def size(self) -> int:
        """Bytes of proposed content (0 for a delete)."""
        return 0 if self.after_content is None else len(self.after_content.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "path": self.path,
            "operation": str(self.operation),
            "step_id": self.step_id,
            "reason": self.reason,
            "before": str(self.before),
            "before_hash": self.before_hash,
            "after_content": self.after_content,
            "after_hash": self.after_hash,
            "evidence": list(self.evidence),
            "metadata": thaw(self.metadata),
        }
