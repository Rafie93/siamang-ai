"""Normalized tool errors.

Every failure at the tool boundary is a :class:`ToolError` with a typed
``code``. Messages are fixed text: no tracebacks, command lines, paths,
environment, or input values. A lower-layer error (for example a
``GitError``) is kept only as its code in ``cause``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class ToolErrorCode(StrEnum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_DISABLED = "tool_disabled"
    TOOL_INPUT_INVALID = "tool_input_invalid"
    TOOL_CONTEXT_INVALID = "tool_context_invalid"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    TOOL_PROJECT_REQUIRED = "tool_project_required"
    TOOL_PROJECT_NOT_FOUND = "tool_project_not_found"
    TOOL_PROJECT_ARCHIVED = "tool_project_archived"
    TOOL_SCOPE_DENIED = "tool_scope_denied"
    TOOL_IMPLEMENTATION_NOT_FOUND = "tool_implementation_not_found"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_RESULT_INVALID = "tool_result_invalid"
    TOOL_DEFINITION_INVALID = "tool_definition_invalid"
    TOOL_DUPLICATE = "tool_duplicate"


class ToolError(Exception):
    code: ToolErrorCode = ToolErrorCode.TOOL_EXECUTION_FAILED
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        tool_id: str | None = None,
        cause: str | None = None,
        retryable: bool | None = None,
        details: tuple[str, ...] = (),
    ):
        self.message = redact(message)
        self.tool_id = tool_id
        self.cause = cause  # lower-layer error code (e.g. "not_repository") or exception type name
        self.details = tuple(redact(d) for d in details)  # e.g. missing permissions, field violations
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (tool={self.tool_id})" if self.tool_id else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "tool_id": self.tool_id,
            "cause": self.cause,
            "retryable": self.retryable,
            "details": list(self.details),
        }


class ToolNotFoundError(ToolError):
    code = ToolErrorCode.TOOL_NOT_FOUND


class ToolDisabledError(ToolError):
    code = ToolErrorCode.TOOL_DISABLED


class ToolInputError(ToolError):
    code = ToolErrorCode.TOOL_INPUT_INVALID


class ToolContextError(ToolError):
    code = ToolErrorCode.TOOL_CONTEXT_INVALID


class ToolPermissionError(ToolError):
    code = ToolErrorCode.TOOL_PERMISSION_DENIED


class ToolProjectRequiredError(ToolError):
    code = ToolErrorCode.TOOL_PROJECT_REQUIRED


class ToolProjectNotFoundError(ToolError):
    code = ToolErrorCode.TOOL_PROJECT_NOT_FOUND


class ToolProjectArchivedError(ToolError):
    code = ToolErrorCode.TOOL_PROJECT_ARCHIVED


class ToolScopeError(ToolError):
    code = ToolErrorCode.TOOL_SCOPE_DENIED


class ToolImplementationNotFoundError(ToolError):
    code = ToolErrorCode.TOOL_IMPLEMENTATION_NOT_FOUND


class ToolExecutionError(ToolError):
    code = ToolErrorCode.TOOL_EXECUTION_FAILED


class ToolResultError(ToolError):
    code = ToolErrorCode.TOOL_RESULT_INVALID


class ToolDefinitionError(ToolError):
    code = ToolErrorCode.TOOL_DEFINITION_INVALID


class ToolDuplicateError(ToolError):
    code = ToolErrorCode.TOOL_DUPLICATE


ERROR_TYPES: dict[ToolErrorCode, type[ToolError]] = {
    cls.code: cls
    for cls in (
        ToolNotFoundError, ToolDisabledError, ToolInputError, ToolContextError, ToolPermissionError,
        ToolProjectRequiredError, ToolProjectNotFoundError, ToolProjectArchivedError, ToolScopeError,
        ToolImplementationNotFoundError, ToolExecutionError, ToolResultError, ToolDefinitionError,
        ToolDuplicateError,
    )
}
