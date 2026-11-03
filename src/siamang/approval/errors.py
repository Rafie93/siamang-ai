"""Normalized Approval errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a lower-layer error code or an exception type name),
and ``details`` (field names, never values, actor names, reasons, file
contents, or machine paths).

An error is never a decision. Invalid input, broken provenance, tampering,
an unauthorized approver, or evidence that does not qualify for approval
all raise, so they can never be mistaken for a human ``rejected``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class ApprovalErrorCode(StrEnum):
    REQUEST_INVALID = "approval_request_invalid"
    TASK_NOT_FOUND = "approval_task_not_found"
    PROJECT_NOT_FOUND = "approval_project_not_found"
    PROJECT_ARCHIVED = "approval_project_archived"
    UNAUTHORIZED = "approval_unauthorized"
    PROVENANCE_INVALID = "approval_provenance_invalid"
    NOT_ELIGIBLE = "approval_not_eligible"
    RESULT_INVALID = "approval_result_invalid"
    INTERNAL = "approval_internal"


class ApprovalError(Exception):
    code: ApprovalErrorCode = ApprovalErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class ApprovalRequestError(ApprovalError, ValueError):
    code = ApprovalErrorCode.REQUEST_INVALID


class ApprovalTaskNotFoundError(ApprovalError):
    code = ApprovalErrorCode.TASK_NOT_FOUND


class ApprovalProjectNotFoundError(ApprovalError):
    code = ApprovalErrorCode.PROJECT_NOT_FOUND


class ApprovalProjectArchivedError(ApprovalError):
    code = ApprovalErrorCode.PROJECT_ARCHIVED


class ApprovalUnauthorizedError(ApprovalError):
    """The approver is not in the service policy's authorized set (whatever the request claims)."""

    code = ApprovalErrorCode.UNAUTHORIZED


class ApprovalProvenanceError(ApprovalError, ValueError):
    """An artifact does not re-validate, was altered, does not belong to the chain, or contradicts its evidence."""

    code = ApprovalErrorCode.PROVENANCE_INVALID


class ApprovalNotEligibleError(ApprovalError):
    """The chain is consistent, but its evidence does not qualify for approval (QA, review, or loop not passing)."""

    code = ApprovalErrorCode.NOT_ELIGIBLE


class ApprovalResultError(ApprovalError, ValueError):
    code = ApprovalErrorCode.RESULT_INVALID


class ApprovalInternalError(ApprovalError):
    code = ApprovalErrorCode.INTERNAL


ERROR_TYPES: dict[ApprovalErrorCode, type[ApprovalError]] = {
    cls.code: cls
    for cls in (ApprovalRequestError, ApprovalTaskNotFoundError, ApprovalProjectNotFoundError,
                ApprovalProjectArchivedError, ApprovalUnauthorizedError, ApprovalProvenanceError,
                ApprovalNotEligibleError, ApprovalResultError, ApprovalInternalError)
}
