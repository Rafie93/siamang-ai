"""ReviewResult: the immutable outcome of reviewing one QA-verified proposed change.

``status`` is derived from the findings and validated on construction, so
it can never disagree with them:

* ``fail``: at least one ``error`` finding (a blocking review concern, e.g.
  QA failed or nothing in the patch relates to the task);
* ``inconclusive``: no error, but at least one inconclusive warning: the
  evidence is not enough for a safe judgment (e.g. QA was inconclusive);
* ``concern``: no error and nothing inconclusive, but at least one warning;
* ``pass``: no blocking concern, nothing inconclusive, and no warning, based
  on the evidence available.

``pass`` is **not** proof of correctness and **not** approval:
``not_verified`` lists what a static review never establishes. QA ``pass``
does not imply review ``pass``; QA ``fail`` always makes the review ``fail``.

``review_id`` (``rev_<ULID>``) is identity, new on every call.
:meth:`content` / :attr:`content_hash` exclude it and the random artifact
IDs (``plan_id``, ``analysis_id``, ``patch_id``, ``qa_id``); they include
the artifacts' content hashes. Proposed file contents and secrets are never
part of a result, only hashes, counts, and safe paths.
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

from siamang.analyst.analysis import ANALYSIS_ID_RE
from siamang.planner.plan import PLAN_ID_RE
from siamang.planner.request import json_object
from siamang.programmer.patch import PATCH_ID_RE
from siamang.programmer.safety import deep_freeze, thaw
from siamang.qa.request import HASH_RE
from siamang.qa.result import QA_ID_RE, QAStatus
from siamang.reviewer.errors import ReviewResultError
from siamang.reviewer.finding import ReviewFinding
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

REVIEW_ID_RE = re.compile(r"^rev_[A-Za-z0-9]{1,64}\Z")
FINDINGS_LIMIT = 500
DIMENSIONS = ("verification", "alignment", "scope", "maintainability", "consistency", "risk", "feasibility")
NOT_VERIFIED = ("runtime_behavior", "semantic_correctness", "integration_behavior", "production_behavior",
                "user_acceptance", "approval")
RESULT_LIMIT = 1024 * 1024


class ReviewStatus(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


def derive_status(findings: tuple[ReviewFinding, ...]) -> ReviewStatus:
    if any(f.blocking for f in findings):
        return ReviewStatus.FAIL
    if any(f.inconclusive for f in findings):
        return ReviewStatus.INCONCLUSIVE
    if any(f.severity.value == "warning" for f in findings):
        return ReviewStatus.CONCERN
    return ReviewStatus.PASS


def _frozen(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewResultError(f"{where} must be an object", details=(where,))
    try:
        text = json.dumps(thaw(value), sort_keys=True)
    except (TypeError, ValueError):
        raise ReviewResultError(f"{where} must be JSON data", details=(where,)) from None
    return deep_freeze(json.loads(text))


def _identifier(value: Any, pattern: re.Pattern[str], name: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ReviewResultError(f"{name} is not a valid identifier", details=(name,))


@dataclass(frozen=True)
class ReviewResult:
    review_id: str
    task_id: str
    project_id: str
    plan_id: str
    analysis_id: str
    patch_id: str
    patch_hash: str
    qa_id: str
    qa_hash: str
    qa_status: QAStatus
    status: ReviewStatus
    findings: tuple[ReviewFinding, ...] = ()
    dimensions: tuple[str, ...] = DIMENSIONS
    not_verified: tuple[str, ...] = NOT_VERIFIED
    policy: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _identifier(self.review_id, REVIEW_ID_RE, "review_id")
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ReviewResultError(f"{name} is not a valid identifier", details=(name,)) from None
        _identifier(self.plan_id, PLAN_ID_RE, "plan_id")
        _identifier(self.analysis_id, ANALYSIS_ID_RE, "analysis_id")
        _identifier(self.patch_id, PATCH_ID_RE, "patch_id")
        _identifier(self.qa_id, QA_ID_RE, "qa_id")
        _identifier(self.patch_hash, HASH_RE, "patch_hash")
        _identifier(self.qa_hash, HASH_RE, "qa_hash")
        findings = self.findings
        if isinstance(findings, (str, bytes)) or not isinstance(findings, (list, tuple)):
            raise ReviewResultError("findings must be a list of ReviewFinding", details=("findings",))
        if len(findings) > FINDINGS_LIMIT or not all(type(f) is ReviewFinding for f in findings):
            raise ReviewResultError(f"findings must be at most {FINDINGS_LIMIT} ReviewFinding objects",
                                    details=("findings",))
        findings = tuple(findings)
        object.__setattr__(self, "findings", findings)
        try:
            qa_status = QAStatus(self.qa_status)
            status = ReviewStatus(self.status)
        except ValueError:
            raise ReviewResultError("status must be pass, concern, fail, or inconclusive; qa_status pass, fail, "
                                    "or inconclusive", details=("status",)) from None
        if status is not derive_status(findings):
            raise ReviewResultError("status does not match the findings", details=("status",))
        if qa_status is QAStatus.FAIL and status is not ReviewStatus.FAIL:
            raise ReviewResultError("a review of a failed QA result must fail", details=("status",))
        if qa_status is QAStatus.INCONCLUSIVE and status in (ReviewStatus.PASS, ReviewStatus.CONCERN):
            raise ReviewResultError("a review of an inconclusive QA result cannot pass", details=("status",))
        object.__setattr__(self, "qa_status", qa_status)
        object.__setattr__(self, "status", status)
        for name in ("dimensions", "not_verified"):
            values = self.__dict__[name]
            if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or not all(
                    isinstance(v, str) and 0 < len(v) <= 120 for v in values) or len(values) > 20:
                raise ReviewResultError(f"{name} must be a short list of strings", details=(name,))
            object.__setattr__(self, name, tuple(values))
        if not set(NOT_VERIFIED) <= set(self.not_verified):
            raise ReviewResultError("not_verified cannot drop what a static review never establishes",
                                    details=("not_verified",))
        object.__setattr__(self, "policy", _frozen(self.policy, "policy"))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "result.metadata",
                                                                     error=ReviewResultError)))
        if len(self._canonical().encode("utf-8")) > RESULT_LIMIT:
            raise ReviewResultError(f"result exceeds {RESULT_LIMIT} bytes", details=("result",))

    @property
    def errors(self) -> tuple[ReviewFinding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def warnings(self) -> tuple[ReviewFinding, ...]:
        return tuple(f for f in self.findings if f.severity.value == "warning")

    def content(self) -> dict[str, Any]:
        """Everything except ``review_id`` and the random artifact IDs: the deterministic, comparable part."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "patch_hash": self.patch_hash,
            "qa_hash": self.qa_hash,
            "qa_status": str(self.qa_status),
            "status": str(self.status),
            "findings": [f.to_dict() for f in self.findings],
            "dimensions": list(self.dimensions),
            "not_verified": list(self.not_verified),
            "policy": thaw(self.policy),
            "metadata": thaw(self.metadata),
        }

    def _canonical(self) -> str:
        return json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"review_id": self.review_id, "plan_id": self.plan_id, "analysis_id": self.analysis_id,
                "patch_id": self.patch_id, "qa_id": self.qa_id, **self.content(), "content_hash": self.content_hash}
