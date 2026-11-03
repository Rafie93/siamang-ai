"""Normalized orchestration errors.

Same convention as the model/tool/agent layers: a typed ``code``, a fixed
redacted ``message``, ``cause`` (the lower-layer error code, e.g.
``agent_model_unavailable``, or an exception type name), ``retryable``, and
``details``. No traceback, instruction, input, path, raw output, or
credential ever appears, so a result is safe to forward to a future external
channel.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class OrchestrationErrorCode(StrEnum):
    REQUEST_INVALID = "request_invalid"
    TASK_NOT_FOUND = "task_not_found"
    TASK_NOT_RUNNING = "task_not_running"
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_ARCHIVED = "project_archived"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_DISABLED = "agent_disabled"
    AGENT_INVOCATION_FAILED = "agent_invocation_failed"
    POLICY_DENIED = "policy_denied"
    CONTEXT_INVALID = "context_invalid"
    RESULT_INVALID = "result_invalid"
    INTERNAL = "internal"


class OrchestrationError(Exception):
    code: OrchestrationErrorCode = OrchestrationErrorCode.INTERNAL
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        cause: str | None = None,
        retryable: bool | None = None,
        details: tuple[str, ...] = (),
    ):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "cause": self.cause,
            "retryable": self.retryable,
            "details": list(self.details),
        }


class OrchestrationRequestError(OrchestrationError):
    code = OrchestrationErrorCode.REQUEST_INVALID


class OrchestrationTaskNotFoundError(OrchestrationError):
    code = OrchestrationErrorCode.TASK_NOT_FOUND


class TaskNotRunningError(OrchestrationError):
    code = OrchestrationErrorCode.TASK_NOT_RUNNING


class OrchestrationProjectNotFoundError(OrchestrationError):
    code = OrchestrationErrorCode.PROJECT_NOT_FOUND


class OrchestrationProjectArchivedError(OrchestrationError):
    code = OrchestrationErrorCode.PROJECT_ARCHIVED


class OrchestrationAgentNotFoundError(OrchestrationError):
    code = OrchestrationErrorCode.AGENT_NOT_FOUND


class OrchestrationAgentDisabledError(OrchestrationError):
    code = OrchestrationErrorCode.AGENT_DISABLED


class AgentInvocationError(OrchestrationError):
    code = OrchestrationErrorCode.AGENT_INVOCATION_FAILED


class PolicyDeniedError(OrchestrationError):
    code = OrchestrationErrorCode.POLICY_DENIED


class OrchestrationContextError(OrchestrationError):
    code = OrchestrationErrorCode.CONTEXT_INVALID


class OrchestrationResultError(OrchestrationError):
    code = OrchestrationErrorCode.RESULT_INVALID


class OrchestrationInternalError(OrchestrationError):
    code = OrchestrationErrorCode.INTERNAL


ERROR_TYPES: dict[OrchestrationErrorCode, type[OrchestrationError]] = {
    cls.code: cls
    for cls in (
        OrchestrationRequestError, OrchestrationTaskNotFoundError, TaskNotRunningError,
        OrchestrationProjectNotFoundError, OrchestrationProjectArchivedError, OrchestrationAgentNotFoundError,
        OrchestrationAgentDisabledError, AgentInvocationError, PolicyDeniedError, OrchestrationContextError,
        OrchestrationResultError, OrchestrationInternalError,
    )
}
