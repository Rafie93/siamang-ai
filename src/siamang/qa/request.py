"""QARequest: "verify this proposed patch for this task". Data only; never execution or repair authority.

* ``plan``, ``analysis``, and ``patch`` are required and must be **exact**
  :class:`~siamang.planner.Plan`, :class:`~siamang.analyst.SystemAnalysis`,
  and :class:`~siamang.programmer.Patch` objects: immutable snapshots, never
  live services. QA never asks the Planner, Analyst, or Programmer for
  anything.
* ``patch_hash`` is the hash the caller recorded when the patch was produced
  (``Patch.content_hash``). QA recomputes it and reports a mismatch as a
  blocking finding: a forged or altered patch never passes.
* ``metadata`` follows the Programmer's key rules (no command, path,
  credential, agent, tool, model, Git, write, or approval keys at any
  depth) plus QA's own (``fix``, ``repair``, ``retry``, ``review``, ...),
  and is frozen at every depth.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.planner.plan import Plan
from siamang.planner.request import json_object
from siamang.programmer.patch import Patch
from siamang.programmer.request import PROGRAMMER_KEYS
from siamang.programmer.safety import deep_freeze, thaw
from siamang.qa.errors import QARequestError
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")

# Refused at any depth in metadata, on top of the Programmer/Analyst/Planner/Task Engine key rules.
QA_KEYS = PROGRAMMER_KEYS | frozenset({
    "fix", "autofix", "auto_fix", "repair", "retry", "retries", "regenerate", "programmer", "loop", "iterate",
    "review", "reviewer", "approve", "reject", "authorize", "run_tests", "test_command", "pytest", "npm",
    "execute", "run", "script", "sandbox",
})


def _qa_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in QA_KEYS:
                raise QARequestError("execution-, repair-, or approval-style key is not accepted", details=(where,))
            _qa_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _qa_keys(item, where)


@dataclass(frozen=True)
class QARequest:
    task_id: str
    project_id: str
    plan: Plan
    analysis: SystemAnalysis
    patch: Patch
    patch_hash: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise QARequestError(f"{name} is not a valid identifier", details=(name,)) from None
        for name, value, cls in (("plan", self.plan, Plan), ("analysis", self.analysis, SystemAnalysis),
                                 ("patch", self.patch, Patch)):
            if type(value) is not cls:  # exact types: a subclass could carry behaviour
                raise QARequestError(f"{name} must be a validated {cls.__name__}", details=(name,))
        if not isinstance(self.patch_hash, str) or not HASH_RE.fullmatch(self.patch_hash):
            raise QARequestError("patch_hash must look like sha256:<64 hex>", details=("patch_hash",))
        _qa_keys(self.metadata, "metadata")
        object.__setattr__(self, "metadata",
                           deep_freeze(json_object(thaw(self.metadata), "metadata", error=QARequestError)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "plan_id": self.plan.plan_id,
            "analysis_id": self.analysis.analysis_id,
            "patch_id": self.patch.patch_id,
            "patch_hash": self.patch_hash,
            "metadata": thaw(self.metadata),
        }
