"""Normalized QA errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a lower-layer error code or an exception type
name), and ``details`` (field names or IDs, never values, file contents, or
absolute machine paths).

Refusals of the *input* (request, task, project, plan, analysis, and the
patch's identity) are raised. Defects *inside* the patch under test are not
errors: they are QA findings in a ``fail`` result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class QAErrorCode(StrEnum):
    REQUEST_INVALID = "qa_request_invalid"
    TASK_NOT_FOUND = "qa_task_not_found"
    PROJECT_NOT_FOUND = "qa_project_not_found"
    PROJECT_ARCHIVED = "qa_project_archived"
    PROJECT_UNAVAILABLE = "qa_project_unavailable"
    POLICY_DENIED = "qa_policy_denied"
    PLAN_INVALID = "qa_plan_invalid"
    ANALYSIS_INVALID = "qa_analysis_invalid"
    PATCH_INVALID = "qa_patch_invalid"
    RESULT_INVALID = "qa_result_invalid"
    INTERNAL = "qa_internal"


class QAError(Exception):
    code: QAErrorCode = QAErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class QARequestError(QAError, ValueError):
    code = QAErrorCode.REQUEST_INVALID


class QATaskNotFoundError(QAError):
    code = QAErrorCode.TASK_NOT_FOUND


class QAProjectNotFoundError(QAError):
    code = QAErrorCode.PROJECT_NOT_FOUND


class QAProjectArchivedError(QAError):
    code = QAErrorCode.PROJECT_ARCHIVED


class QAProjectUnavailableError(QAError):
    code = QAErrorCode.PROJECT_UNAVAILABLE


class QAPolicyError(QAError):
    code = QAErrorCode.POLICY_DENIED


class QAPlanInvalidError(QAError, ValueError):
    code = QAErrorCode.PLAN_INVALID


class QAAnalysisInvalidError(QAError, ValueError):
    code = QAErrorCode.ANALYSIS_INVALID


class QAPatchInvalidError(QAError, ValueError):
    """The patch is not a Patch, or its identity (ID, task, project) does not match the request."""

    code = QAErrorCode.PATCH_INVALID


class QAResultError(QAError, ValueError):
    code = QAErrorCode.RESULT_INVALID


class QAInternalError(QAError):
    code = QAErrorCode.INTERNAL


ERROR_TYPES: dict[QAErrorCode, type[QAError]] = {
    cls.code: cls
    for cls in (
        QARequestError, QATaskNotFoundError, QAProjectNotFoundError, QAProjectArchivedError,
        QAProjectUnavailableError, QAPolicyError, QAPlanInvalidError, QAAnalysisInvalidError, QAPatchInvalidError,
        QAResultError, QAInternalError,
    )
}
