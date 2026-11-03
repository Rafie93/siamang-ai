"""Normalized analysis errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a lower-layer error code or an exception type
name), and ``details`` (field names, relative paths, or IDs, never values
or file contents). No traceback, secret, or absolute machine path appears.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class AnalysisErrorCode(StrEnum):
    REQUEST_INVALID = "analysis_request_invalid"
    TASK_NOT_FOUND = "analysis_task_not_found"
    PROJECT_NOT_FOUND = "analysis_project_not_found"
    PROJECT_ARCHIVED = "analysis_project_archived"
    PROJECT_UNAVAILABLE = "analysis_project_unavailable"
    POLICY_DENIED = "analysis_policy_denied"
    PATH_REJECTED = "analysis_path_rejected"
    LIMIT_REACHED = "analysis_limit_reached"
    INVALID_ANALYSIS = "analysis_invalid"
    INTERNAL = "analysis_internal"


class AnalysisError(Exception):
    code: AnalysisErrorCode = AnalysisErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class AnalysisRequestError(AnalysisError, ValueError):
    code = AnalysisErrorCode.REQUEST_INVALID


class AnalysisTaskNotFoundError(AnalysisError):
    code = AnalysisErrorCode.TASK_NOT_FOUND


class AnalysisProjectNotFoundError(AnalysisError):
    code = AnalysisErrorCode.PROJECT_NOT_FOUND


class AnalysisProjectArchivedError(AnalysisError):
    code = AnalysisErrorCode.PROJECT_ARCHIVED


class AnalysisProjectUnavailableError(AnalysisError):
    """The registered root is missing, not a directory, or a protected repository."""

    code = AnalysisErrorCode.PROJECT_UNAVAILABLE


class AnalysisPolicyError(AnalysisError):
    code = AnalysisErrorCode.POLICY_DENIED


class PathRejectedError(AnalysisError, ValueError):
    """A path would leave the project root, follow a symlink, or name a secret file."""

    code = AnalysisErrorCode.PATH_REJECTED


class LimitReachedError(AnalysisError):
    """An inspection budget (files, bytes, entries) is spent. Strategies record it as an unknown."""

    code = AnalysisErrorCode.LIMIT_REACHED


class InvalidAnalysisError(AnalysisError, ValueError):
    code = AnalysisErrorCode.INVALID_ANALYSIS


class AnalysisInternalError(AnalysisError):
    code = AnalysisErrorCode.INTERNAL


ERROR_TYPES: dict[AnalysisErrorCode, type[AnalysisError]] = {
    cls.code: cls
    for cls in (
        AnalysisRequestError, AnalysisTaskNotFoundError, AnalysisProjectNotFoundError, AnalysisProjectArchivedError,
        AnalysisProjectUnavailableError, AnalysisPolicyError, PathRejectedError, LimitReachedError,
        InvalidAnalysisError, AnalysisInternalError,
    )
}
