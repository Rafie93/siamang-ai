"""ProgrammingResult: the immutable outcome of one programming attempt. A proposal, never an applied change.

``status`` is one of:

* ``completed``: a :class:`Patch` with at least one proposed change;
* ``waiting``: no patch; the model could not responsibly propose code and
  says what must be established first (at least one ``unknown``);
* ``failed``: no patch; ``error`` holds a structured programmer error
  (``code``, ``message``, ``cause``, ``details``), e.g. the model was
  unavailable or its output was invalid.

``programming_id`` (``prg_<id>``) is identity, new on every call.
:meth:`content` / :attr:`content_hash` exclude it and the patch's
``patch_id``, so the same input and the same generated content give the same
hash, and different proposed content a different one.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.log import redact
from siamang.planner.request import json_object, text_field
from siamang.programmer.errors import ProgrammingErrorCode, ProgrammingResultError
from siamang.programmer.patch import SUMMARY_LIMIT, Patch
from siamang.programmer.safety import deep_freeze, find_secret, thaw
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

PROGRAMMING_ID_RE = re.compile(r"^prg_[A-Za-z0-9]{1,64}\Z")
NOTE_MAX = 500
NOTES_LIMIT = 100
RESULT_LIMIT = 12 * 1024 * 1024  # serialized result; JSON escaping can grow content


class ProgrammingStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


def notes(values: Any, name: str, *, error: type[Exception] = ProgrammingResultError,
          limit: int = NOTES_LIMIT) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or len(values) > limit:
        raise error(f"{name} must be a list of at most {limit} lines", details=(name,))
    out = []
    for value in values:
        text = text_field(value, name, NOTE_MAX, multiline=True, error=error)
        if find_secret(text):
            raise error(f"{name} contain credential material", details=(name,))
        out.append(redact(text))
    return tuple(out)


def _error(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"code", "message", "cause", "details"}:
        raise ProgrammingResultError("error must be a programmer error dictionary", details=("error",))
    try:
        code = ProgrammingErrorCode(value["code"])
    except ValueError:
        raise ProgrammingResultError("error.code must be a programmer error code", details=("error",)) from None
    cause, details = value["cause"], value["details"]
    if cause is not None and not isinstance(cause, str):
        raise ProgrammingResultError("error.cause must be text", details=("error",))
    if isinstance(details, (str, bytes)) or not isinstance(details, (list, tuple)):
        raise ProgrammingResultError("error.details must be a list", details=("error",))
    return MappingProxyType({
        "code": str(code),
        "message": redact(text_field(value["message"], "error.message", NOTE_MAX, error=ProgrammingResultError)),
        "cause": None if cause is None else redact(text_field(cause, "error.cause", 120,
                                                              error=ProgrammingResultError)),
        "details": tuple(redact(text_field(d, "error.details", 300, error=ProgrammingResultError))
                         for d in details[:20]),
    })


@dataclass(frozen=True)
class ProgrammingResult:
    programming_id: str
    task_id: str
    project_id: str
    status: ProgrammingStatus
    summary: str
    patch: Patch | None = None
    warnings: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    error: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.programming_id, str) or not PROGRAMMING_ID_RE.fullmatch(self.programming_id):
            raise ProgrammingResultError("programming_id must look like prg_<id>", details=("programming_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ProgrammingResultError(f"{name} is not a valid identifier", details=(name,)) from None
        try:
            status = ProgrammingStatus(self.status)
        except ValueError:
            raise ProgrammingResultError("status must be completed, failed, or waiting", details=("status",)) from None
        object.__setattr__(self, "status", status)
        summary = text_field(self.summary, "summary", SUMMARY_LIMIT, multiline=True, error=ProgrammingResultError)
        if find_secret(summary):
            raise ProgrammingResultError("summary contains credential material", details=("summary",))
        object.__setattr__(self, "summary", redact(summary))
        object.__setattr__(self, "warnings", notes(self.warnings, "warnings"))
        object.__setattr__(self, "unknowns", notes(self.unknowns, "unknowns"))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "result.metadata",
                                                                     error=ProgrammingResultError)))
        patch, err = self.patch, self.error
        if patch is not None:
            if type(patch) is not Patch:
                raise ProgrammingResultError("patch must be a Patch", details=("patch",))
            if patch.task_id != self.task_id or patch.project_id != self.project_id:
                raise ProgrammingResultError("patch is for a different task or project", details=("patch",))
        if status is ProgrammingStatus.COMPLETED:
            if patch is None or not patch.changes or err is not None:
                raise ProgrammingResultError("a completed result has a non-empty patch and no error",
                                             details=("status",))
        elif status is ProgrammingStatus.WAITING:
            if patch is not None or err is not None or not self.unknowns:
                raise ProgrammingResultError("a waiting result has no patch or error and names an unknown",
                                             details=("status",))
        else:
            if patch is not None or err is None:
                raise ProgrammingResultError("a failed result has an error and no patch", details=("status",))
        object.__setattr__(self, "error", None if err is None else _error(err))
        if self.size > RESULT_LIMIT:
            raise ProgrammingResultError(f"result exceeds {RESULT_LIMIT} bytes", details=("result",))

    @property
    def changes(self) -> tuple:
        return () if self.patch is None else self.patch.changes

    def content(self) -> dict[str, Any]:
        """Everything except ``programming_id`` and ``patch_id``."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "status": str(self.status),
            "summary": self.summary,
            "patch": None if self.patch is None else self.patch.content(),
            "warnings": list(self.warnings),
            "unknowns": list(self.unknowns),
            "error": None if self.error is None else {**self.error, "details": list(self.error["details"])},
            "metadata": thaw(self.metadata),
        }

    def _canonical(self) -> str:
        return json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        return len(self._canonical().encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        out = {"programming_id": self.programming_id, **self.content(), "content_hash": self.content_hash}
        if self.patch is not None:
            out["patch"] = self.patch.to_dict()
        return out
