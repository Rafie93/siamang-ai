"""QAResult: the immutable outcome of verifying one proposed patch.

``status`` is derived from the findings and validated on construction, so
it can never disagree with them:

* ``fail``: at least one ``error`` finding (blocking);
* ``inconclusive``: no error, but at least one inconclusive warning: QA
  could not safely establish a condition it needed;
* ``pass``: every deterministic check ran and passed.

``pass`` is **not** proof of correctness: ``not_verified`` lists what static
QA never establishes (runtime behaviour, semantic task fulfilment, ...).
``pass`` is not approval and ``fail`` is not rejection.

``qa_id`` (``qa_<ULID>``) is identity, new on every call. :meth:`content` /
:attr:`content_hash` exclude it and the patch's random ``patch_id``; they
include ``patch_hash``. Proposed file contents and secrets are never part of
a result, only hashes and safe paths.
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

from siamang.planner.request import json_object
from siamang.programmer.patch import PATCH_ID_RE
from siamang.programmer.safety import deep_freeze, thaw
from siamang.qa.errors import QAResultError
from siamang.qa.finding import QAFinding
from siamang.qa.request import HASH_RE
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

QA_ID_RE = re.compile(r"^qa_[A-Za-z0-9]{1,64}\Z")
FINDINGS_LIMIT = 500
CHECKS = ("patch_integrity", "security", "policy", "task_consistency", "plan_consistency", "analysis_consistency",
          "project_consistency", "scope")
NOT_VERIFIED = ("runtime behaviour (no code is executed)", "semantic fulfilment of the task",
                "correctness of the proposed code", "approval (QA pass is not approval)")
RESULT_LIMIT = 4 * 1024 * 1024


class QAStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


def derive_status(findings: tuple[QAFinding, ...]) -> QAStatus:
    if any(f.blocking for f in findings):
        return QAStatus.FAIL
    if any(f.inconclusive for f in findings):
        return QAStatus.INCONCLUSIVE
    return QAStatus.PASS


def _frozen(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QAResultError(f"{where} must be an object", details=(where,))
    try:
        text = json.dumps(thaw(value), sort_keys=True)
    except (TypeError, ValueError):
        raise QAResultError(f"{where} must be JSON data", details=(where,)) from None
    return deep_freeze(json.loads(text))


@dataclass(frozen=True)
class QAResult:
    qa_id: str
    task_id: str
    project_id: str
    patch_id: str
    patch_hash: str | None
    status: QAStatus
    findings: tuple[QAFinding, ...] = ()
    checks: tuple[str, ...] = CHECKS
    not_verified: tuple[str, ...] = NOT_VERIFIED
    policy: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    snapshot: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.qa_id, str) or not QA_ID_RE.fullmatch(self.qa_id):
            raise QAResultError("qa_id must look like qa_<id>", details=("qa_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise QAResultError(f"{name} is not a valid identifier", details=(name,)) from None
        if not isinstance(self.patch_id, str) or not PATCH_ID_RE.fullmatch(self.patch_id):
            raise QAResultError("patch_id must look like pch_<id>", details=("patch_id",))
        if self.patch_hash is not None and (not isinstance(self.patch_hash, str)
                                            or not HASH_RE.fullmatch(self.patch_hash)):
            raise QAResultError("patch_hash must look like sha256:<64 hex>", details=("patch_hash",))
        findings = self.findings
        if isinstance(findings, (str, bytes)) or not isinstance(findings, (list, tuple)):
            raise QAResultError("findings must be a list of QAFinding", details=("findings",))
        if len(findings) > FINDINGS_LIMIT or not all(type(f) is QAFinding for f in findings):
            raise QAResultError(f"findings must be at most {FINDINGS_LIMIT} QAFinding objects", details=("findings",))
        findings = tuple(findings)
        object.__setattr__(self, "findings", findings)
        try:
            status = QAStatus(self.status)
        except ValueError:
            raise QAResultError("status must be pass, fail, or inconclusive", details=("status",)) from None
        if status is not derive_status(findings):
            raise QAResultError("status does not match the findings", details=("status",))
        if self.patch_hash is None and status is not QAStatus.FAIL:
            raise QAResultError("a result without a verifiable patch hash must fail", details=("patch_hash",))
        object.__setattr__(self, "status", status)
        for name in ("checks", "not_verified"):
            values = self.__dict__[name]
            if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or not all(
                    isinstance(v, str) and 0 < len(v) <= 120 for v in values) or len(values) > 20:
                raise QAResultError(f"{name} must be a short list of strings", details=(name,))
            object.__setattr__(self, name, tuple(values))
        object.__setattr__(self, "policy", _frozen(self.policy, "policy"))
        object.__setattr__(self, "snapshot", _frozen(self.snapshot, "snapshot"))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "result.metadata",
                                                                     error=QAResultError)))
        if len(self._canonical().encode("utf-8")) > RESULT_LIMIT:
            raise QAResultError(f"result exceeds {RESULT_LIMIT} bytes", details=("result",))

    @property
    def passed(self) -> bool:
        return self.status is QAStatus.PASS

    @property
    def errors(self) -> tuple[QAFinding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    def content(self) -> dict[str, Any]:
        """Everything except ``qa_id`` and ``patch_id``: the deterministic, comparable part."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "patch_hash": self.patch_hash,
            "status": str(self.status),
            "findings": [f.to_dict() for f in self.findings],
            "checks": list(self.checks),
            "not_verified": list(self.not_verified),
            "policy": thaw(self.policy),
            "snapshot": thaw(self.snapshot),
            "metadata": thaw(self.metadata),
        }

    def _canonical(self) -> str:
        return json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"qa_id": self.qa_id, "patch_id": self.patch_id, **self.content(), "content_hash": self.content_hash}
