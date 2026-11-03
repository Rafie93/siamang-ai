"""Normalized programming errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a lower-layer error code or an exception type
name), and ``details`` (field names, change IDs, or project-relative paths,
never values, file contents, or model text). No traceback, secret, or
absolute machine path appears.

Refusals of the *input* (request, task, project, plan, analysis) are raised.
Failures of the *attempt* (the model call and its output) are returned as a
``failed`` :class:`~siamang.programmer.result.ProgrammingResult` carrying the
same ``to_dict()`` shape, so model output can never surface as an exception.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class ProgrammingErrorCode(StrEnum):
    REQUEST_INVALID = "programmer_request_invalid"
    TASK_NOT_FOUND = "programmer_task_not_found"
    PROJECT_NOT_FOUND = "programmer_project_not_found"
    PROJECT_ARCHIVED = "programmer_project_archived"
    PROJECT_UNAVAILABLE = "programmer_project_unavailable"
    POLICY_DENIED = "programmer_policy_denied"
    PLAN_INVALID = "programmer_plan_invalid"
    ANALYSIS_INVALID = "programmer_analysis_invalid"
    CHANGE_REJECTED = "programmer_change_rejected"
    PATCH_INVALID = "programmer_patch_invalid"
    RESULT_INVALID = "programmer_result_invalid"
    MODEL_UNAVAILABLE = "programmer_model_unavailable"
    MODEL_TIMEOUT = "programmer_model_timeout"
    MODEL_REJECTED = "programmer_model_rejected"
    MODEL_INVALID_OUTPUT = "programmer_model_invalid_output"
    INTERNAL = "programmer_internal"


class ProgrammingError(Exception):
    code: ProgrammingErrorCode = ProgrammingErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class ProgrammingRequestError(ProgrammingError, ValueError):
    code = ProgrammingErrorCode.REQUEST_INVALID


class ProgrammingTaskNotFoundError(ProgrammingError):
    code = ProgrammingErrorCode.TASK_NOT_FOUND


class ProgrammingProjectNotFoundError(ProgrammingError):
    code = ProgrammingErrorCode.PROJECT_NOT_FOUND


class ProgrammingProjectArchivedError(ProgrammingError):
    code = ProgrammingErrorCode.PROJECT_ARCHIVED


class ProgrammingProjectUnavailableError(ProgrammingError):
    """The registered root is missing or not a directory."""

    code = ProgrammingErrorCode.PROJECT_UNAVAILABLE


class ProgrammingPolicyError(ProgrammingError):
    code = ProgrammingErrorCode.POLICY_DENIED


class InvalidPlanInputError(ProgrammingError, ValueError):
    code = ProgrammingErrorCode.PLAN_INVALID


class InvalidAnalysisInputError(ProgrammingError, ValueError):
    code = ProgrammingErrorCode.ANALYSIS_INVALID


class ChangeRejectedError(ProgrammingError, ValueError):
    """A proposed change is unsafe: bad path, forbidden file, secret material, binary or oversized content."""

    code = ProgrammingErrorCode.CHANGE_REJECTED


class InvalidPatchError(ProgrammingError, ValueError):
    """A change set is not self-consistent: duplicate or conflicting paths, too many changes, too large."""

    code = ProgrammingErrorCode.PATCH_INVALID


class ProgrammingResultError(ProgrammingError, ValueError):
    code = ProgrammingErrorCode.RESULT_INVALID


class ProgrammerModelUnavailableError(ProgrammingError):
    code = ProgrammingErrorCode.MODEL_UNAVAILABLE


class ProgrammerModelTimeoutError(ProgrammingError):
    code = ProgrammingErrorCode.MODEL_TIMEOUT


class ProgrammerModelRejectedError(ProgrammingError):
    """The model layer refused the request (authentication, capability, context limit, invalid request)."""

    code = ProgrammingErrorCode.MODEL_REJECTED


class ProgrammerModelOutputError(ProgrammingError, ValueError):
    code = ProgrammingErrorCode.MODEL_INVALID_OUTPUT


class ProgrammingInternalError(ProgrammingError):
    code = ProgrammingErrorCode.INTERNAL


ERROR_TYPES: dict[ProgrammingErrorCode, type[ProgrammingError]] = {
    cls.code: cls
    for cls in (
        ProgrammingRequestError, ProgrammingTaskNotFoundError, ProgrammingProjectNotFoundError,
        ProgrammingProjectArchivedError, ProgrammingProjectUnavailableError, ProgrammingPolicyError,
        InvalidPlanInputError, InvalidAnalysisInputError, ChangeRejectedError, InvalidPatchError,
        ProgrammingResultError, ProgrammerModelUnavailableError, ProgrammerModelTimeoutError,
        ProgrammerModelRejectedError, ProgrammerModelOutputError, ProgrammingInternalError,
    )
}
