"""Normalized Reviewer errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a lower-layer error code or an exception type
name), and ``details`` (field names or IDs, never values, file contents, or
absolute machine paths).

Refusals of the *input* are raised: a malformed request, an unknown task or
project, an artifact that does not re-validate, identities that disagree, a
recorded hash that does not match (tampering), or a policy bound exceeded.
The Reviewer never judges artifacts that are not consistent with each other.
Concerns *about* a consistent change are not errors: they are review
findings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class ReviewerErrorCode(StrEnum):
    REQUEST_INVALID = "reviewer_request_invalid"
    TASK_NOT_FOUND = "reviewer_task_not_found"
    PROJECT_NOT_FOUND = "reviewer_project_not_found"
    PROJECT_ARCHIVED = "reviewer_project_archived"
    POLICY_DENIED = "reviewer_policy_denied"
    PLAN_INVALID = "reviewer_plan_invalid"
    ANALYSIS_INVALID = "reviewer_analysis_invalid"
    PATCH_INVALID = "reviewer_patch_invalid"
    QA_INVALID = "reviewer_qa_invalid"
    RESULT_INVALID = "reviewer_result_invalid"
    INTERNAL = "reviewer_internal"


class ReviewerError(Exception):
    code: ReviewerErrorCode = ReviewerErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class ReviewRequestError(ReviewerError, ValueError):
    code = ReviewerErrorCode.REQUEST_INVALID


class ReviewTaskNotFoundError(ReviewerError):
    code = ReviewerErrorCode.TASK_NOT_FOUND


class ReviewProjectNotFoundError(ReviewerError):
    code = ReviewerErrorCode.PROJECT_NOT_FOUND


class ReviewProjectArchivedError(ReviewerError):
    code = ReviewerErrorCode.PROJECT_ARCHIVED


class ReviewPolicyError(ReviewerError):
    code = ReviewerErrorCode.POLICY_DENIED


class ReviewPlanInvalidError(ReviewerError, ValueError):
    code = ReviewerErrorCode.PLAN_INVALID


class ReviewAnalysisInvalidError(ReviewerError, ValueError):
    code = ReviewerErrorCode.ANALYSIS_INVALID


class ReviewPatchInvalidError(ReviewerError, ValueError):
    """The patch does not re-validate, was altered, or does not match its recorded hash or identity."""

    code = ReviewerErrorCode.PATCH_INVALID


class ReviewQAInvalidError(ReviewerError, ValueError):
    """The QAResult does not re-validate, was altered, or was produced for different artifacts."""

    code = ReviewerErrorCode.QA_INVALID


class ReviewResultError(ReviewerError, ValueError):
    code = ReviewerErrorCode.RESULT_INVALID


class ReviewInternalError(ReviewerError):
    code = ReviewerErrorCode.INTERNAL


ERROR_TYPES: dict[ReviewerErrorCode, type[ReviewerError]] = {
    cls.code: cls
    for cls in (
        ReviewRequestError, ReviewTaskNotFoundError, ReviewProjectNotFoundError, ReviewProjectArchivedError,
        ReviewPolicyError, ReviewPlanInvalidError, ReviewAnalysisInvalidError, ReviewPatchInvalidError,
        ReviewQAInvalidError, ReviewResultError, ReviewInternalError,
    )
}
