"""ReviewRequest: "review this proposed change, which QA has verified, for this task". Data only.

* ``plan``, ``analysis``, ``patch``, and ``qa_result`` are required and must
  be **exact** :class:`~siamang.planner.Plan`,
  :class:`~siamang.analyst.SystemAnalysis`,
  :class:`~siamang.programmer.Patch`, and :class:`~siamang.qa.QAResult`
  objects: immutable snapshots, never live services. The Reviewer never asks
  the Planner, Analyst, Programmer, or QA for anything.
* ``patch_hash`` is the ``Patch.content_hash`` and ``qa_hash`` the
  ``QAResult.content_hash`` the caller recorded when each artifact was
  produced. The Reviewer recomputes both: an altered or forged artifact is
  refused, never reviewed.
* ``metadata`` follows the QA key rules (no command, path, credential, agent,
  tool, model, Git, write, repair, retry, review, or approval keys at any
  depth) plus the Reviewer's own (``merge``, ``deploy``, ``verdict``, ...),
  and is frozen at every depth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.planner.plan import Plan
from siamang.planner.request import json_object
from siamang.programmer.patch import Patch
from siamang.programmer.safety import deep_freeze, thaw
from siamang.qa.request import HASH_RE, QA_KEYS
from siamang.qa.result import QAResult
from siamang.reviewer.errors import ReviewRequestError
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

# Refused at any depth in metadata, on top of the QA/Programmer/Analyst/Planner/Task Engine key rules.
REVIEWER_KEYS = QA_KEYS | frozenset({
    "accept", "accepted", "rejected", "merge", "deploy", "release", "push", "status", "verdict", "override",
    "force", "skip_qa", "qa_status", "severity",
})


def _reviewer_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in REVIEWER_KEYS:
                raise ReviewRequestError("execution-, repair-, approval-, or verdict-style key is not accepted",
                                         details=(where,))
            _reviewer_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reviewer_keys(item, where)


@dataclass(frozen=True)
class ReviewRequest:
    task_id: str
    project_id: str
    plan: Plan
    analysis: SystemAnalysis
    patch: Patch
    patch_hash: str
    qa_result: QAResult
    qa_hash: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ReviewRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        for name, value, cls in (("plan", self.plan, Plan), ("analysis", self.analysis, SystemAnalysis),
                                 ("patch", self.patch, Patch), ("qa_result", self.qa_result, QAResult)):
            if type(value) is not cls:  # exact types: a subclass could carry behaviour
                raise ReviewRequestError(f"{name} must be a validated {cls.__name__}", details=(name,))
        for name, value in (("patch_hash", self.patch_hash), ("qa_hash", self.qa_hash)):
            if not isinstance(value, str) or not HASH_RE.fullmatch(value):
                raise ReviewRequestError(f"{name} must look like sha256:<64 hex>", details=(name,))
        _reviewer_keys(self.metadata, "metadata")
        object.__setattr__(self, "metadata",
                           deep_freeze(json_object(thaw(self.metadata), "metadata", error=ReviewRequestError)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "plan_id": self.plan.plan_id,
            "analysis_id": self.analysis.analysis_id,
            "patch_id": self.patch.patch_id,
            "patch_hash": self.patch_hash,
            "qa_id": self.qa_result.qa_id,
            "qa_hash": self.qa_hash,
            "metadata": thaw(self.metadata),
        }
