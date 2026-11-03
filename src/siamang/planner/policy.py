"""PlanningPolicy: bounds that keep plans small and finite. Not a workflow, permission, or autonomy engine.

Conservative fixed defaults. Every limit is bounded by the domain's hard
caps, so no policy can permit an unbounded plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.planner.errors import PlanningPolicyError, TooManyStepsError
from siamang.planner.plan import MAX_STEPS_LIMIT
from siamang.planner.request import INSTRUCTION_MAX, PlanningRequest
from siamang.planner.step import DESCRIPTION_MAX, PlanStep
from siamang.tasks.lifecycle import TaskStatus

# Final statuses: nothing left to plan.
NOT_PLANNABLE = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


@dataclass(frozen=True)
class PlanningPolicy:
    max_steps: int = 12
    max_instruction_length: int = 4000
    max_description_length: int = 1000
    allow_empty_plan: bool = False
    plannable_statuses: frozenset[TaskStatus] = frozenset(TaskStatus) - NOT_PLANNABLE

    def __post_init__(self) -> None:
        for name, value, cap in (("max_steps", self.max_steps, MAX_STEPS_LIMIT),
                                 ("max_instruction_length", self.max_instruction_length, INSTRUCTION_MAX),
                                 ("max_description_length", self.max_description_length, DESCRIPTION_MAX)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        if not isinstance(self.allow_empty_plan, bool):
            raise ValueError("allow_empty_plan must be a boolean")
        statuses = self.plannable_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("plannable_statuses must be a collection of task statuses")
        try:
            statuses = frozenset(TaskStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("plannable_statuses must be task statuses") from None
        if statuses & NOT_PLANNABLE or not statuses:
            raise ValueError("completed and cancelled tasks cannot be planned")
        object.__setattr__(self, "plannable_statuses", statuses)

    def check_request(self, request: PlanningRequest) -> None:
        if len(request.instruction) > self.max_instruction_length:
            raise PlanningPolicyError(f"instruction exceeds {self.max_instruction_length} characters",
                                      details=("instruction",))

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.plannable_statuses:
            raise PlanningPolicyError(f"a {status} task cannot be planned")

    def check_steps(self, steps: tuple[PlanStep, ...]) -> None:
        if len(steps) > self.max_steps:
            raise TooManyStepsError(f"plan exceeds {self.max_steps} steps", details=("steps",))
        if not steps and not self.allow_empty_plan:
            raise PlanningPolicyError("an empty plan is not allowed", details=("steps",))
        long = [s.step_id for s in steps if len(s.description) > self.max_description_length]
        if long:
            raise PlanningPolicyError(f"step description exceeds {self.max_description_length} characters",
                                      details=tuple(long))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_instruction_length": self.max_instruction_length,
            "max_description_length": self.max_description_length,
            "allow_empty_plan": self.allow_empty_plan,
            "plannable_statuses": sorted(str(s) for s in self.plannable_statuses),
            "executes": False,
            "changes_task_state": False,
        }


DEFAULT_POLICY = PlanningPolicy()
