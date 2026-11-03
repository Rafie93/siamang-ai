"""Normalized planning errors.

Same convention as the model/tool/agent/orchestration layers: a typed
``code``, a fixed redacted ``message``, ``cause`` (a lower-layer error code
or an exception type name), and ``details`` (field names or step IDs, never
values). No traceback, instruction, input, path, or credential appears.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class PlanningErrorCode(StrEnum):
    REQUEST_INVALID = "planning_request_invalid"
    TASK_NOT_FOUND = "planning_task_not_found"
    PROJECT_NOT_FOUND = "planning_project_not_found"
    PROJECT_ARCHIVED = "planning_project_archived"
    POLICY_DENIED = "planning_policy_denied"
    INVALID_PLAN = "planning_invalid_plan"
    DEPENDENCY_CYCLE = "planning_dependency_cycle"
    TOO_MANY_STEPS = "planning_too_many_steps"
    INTERNAL = "planning_internal"


class PlanningError(Exception):
    code: PlanningErrorCode = PlanningErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class PlanningRequestError(PlanningError, ValueError):
    code = PlanningErrorCode.REQUEST_INVALID


class PlanningTaskNotFoundError(PlanningError):
    code = PlanningErrorCode.TASK_NOT_FOUND


class PlanningProjectNotFoundError(PlanningError):
    code = PlanningErrorCode.PROJECT_NOT_FOUND


class PlanningProjectArchivedError(PlanningError):
    code = PlanningErrorCode.PROJECT_ARCHIVED


class PlanningPolicyError(PlanningError):
    code = PlanningErrorCode.POLICY_DENIED


class InvalidPlanError(PlanningError, ValueError):
    code = PlanningErrorCode.INVALID_PLAN


class DependencyCycleError(InvalidPlanError):
    code = PlanningErrorCode.DEPENDENCY_CYCLE


class TooManyStepsError(PlanningError):
    code = PlanningErrorCode.TOO_MANY_STEPS


class PlanningInternalError(PlanningError):
    code = PlanningErrorCode.INTERNAL


ERROR_TYPES: dict[PlanningErrorCode, type[PlanningError]] = {
    cls.code: cls
    for cls in (
        PlanningRequestError, PlanningTaskNotFoundError, PlanningProjectNotFoundError, PlanningProjectArchivedError,
        PlanningPolicyError, InvalidPlanError, DependencyCycleError, TooManyStepsError, PlanningInternalError,
    )
}
