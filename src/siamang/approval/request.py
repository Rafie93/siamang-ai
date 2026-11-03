"""ApprovalRequest: "this loop's final proposal, for this task, is up for a decision". Data only.

The final patch, QA result, and review come from ``loop_result`` itself (no
separate copies that could disagree). ``plan`` and ``analysis`` are exact,
immutable snapshots. On construction the whole chain is verified and its
:class:`~siamang.approval.provenance.ApprovalProvenance` is recorded; the
service recomputes it at decision time, so an artifact altered in between
makes the request stale and it is refused.

There is deliberately no field for an approver, role, decision, override,
or authorization claim: authority comes from the service's policy, never
from the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.approval.errors import ApprovalRequestError
from siamang.approval.provenance import ApprovalProvenance, bind
from siamang.loop.result import LoopResult
from siamang.planner.plan import Plan
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError


@dataclass(frozen=True)
class ApprovalRequest:
    task_id: str
    project_id: str
    plan: Plan
    analysis: SystemAnalysis
    loop_result: LoopResult
    provenance: ApprovalProvenance = field(init=False)

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ApprovalRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        for name, value, cls in (("plan", self.plan, Plan), ("analysis", self.analysis, SystemAnalysis),
                                 ("loop_result", self.loop_result, LoopResult)):
            if type(value) is not cls:  # exact types: a subclass could carry behaviour
                raise ApprovalRequestError(f"{name} must be a validated {cls.__name__}", details=(name,))
        object.__setattr__(self, "provenance", bind(self.task_id, self.project_id, self.plan, self.analysis,
                                                    self.loop_result))

    def to_dict(self) -> dict[str, Any]:
        return {"provenance": self.provenance.to_dict(), "provenance_hash": self.provenance.provenance_hash}
