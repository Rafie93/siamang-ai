"""Normalized agent errors.

Every failure at the agent boundary is an :class:`AgentError` with a typed
``code``. Messages are fixed text: no tracebacks, prompts, input values,
paths, environment, or credentials. A lower-layer error (a ``ModelError``,
``ToolError``, or an arbitrary exception) is kept only as its code or type
name in ``cause``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class AgentErrorCode(StrEnum):
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_DISABLED = "agent_disabled"
    AGENT_INPUT_INVALID = "agent_input_invalid"
    AGENT_CONTEXT_INVALID = "agent_context_invalid"
    AGENT_PERMISSION_DENIED = "agent_permission_denied"
    AGENT_MODEL_UNAVAILABLE = "agent_model_unavailable"
    AGENT_TOOL_UNAVAILABLE = "agent_tool_unavailable"
    AGENT_EXECUTION_FAILED = "agent_execution_failed"
    AGENT_RESULT_INVALID = "agent_result_invalid"
    AGENT_DEFINITION_INVALID = "agent_definition_invalid"
    AGENT_DUPLICATE = "agent_duplicate"
    AGENT_IMPLEMENTATION_NOT_FOUND = "agent_implementation_not_found"


class AgentError(Exception):
    code: AgentErrorCode = AgentErrorCode.AGENT_EXECUTION_FAILED
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        agent_id: str | None = None,
        cause: str | None = None,
        retryable: bool | None = None,
        details: tuple[str, ...] = (),
    ):
        self.message = redact(message)
        self.agent_id = agent_id
        self.cause = cause  # lower-layer error code (e.g. "model_unavailable") or exception type name
        self.details = tuple(redact(d) for d in details)  # e.g. field violations, missing permissions
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (agent={self.agent_id})" if self.agent_id else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "agent_id": self.agent_id,
            "cause": self.cause,
            "retryable": self.retryable,
            "details": list(self.details),
        }


class AgentNotFoundError(AgentError):
    code = AgentErrorCode.AGENT_NOT_FOUND


class AgentDisabledError(AgentError):
    code = AgentErrorCode.AGENT_DISABLED


class AgentInputError(AgentError):
    code = AgentErrorCode.AGENT_INPUT_INVALID


class AgentContextError(AgentError):
    code = AgentErrorCode.AGENT_CONTEXT_INVALID


class AgentPermissionError(AgentError):
    code = AgentErrorCode.AGENT_PERMISSION_DENIED


class AgentModelUnavailableError(AgentError):
    code = AgentErrorCode.AGENT_MODEL_UNAVAILABLE


class AgentToolUnavailableError(AgentError):
    code = AgentErrorCode.AGENT_TOOL_UNAVAILABLE


class AgentExecutionError(AgentError):
    code = AgentErrorCode.AGENT_EXECUTION_FAILED


class AgentResultError(AgentError):
    code = AgentErrorCode.AGENT_RESULT_INVALID


class AgentDefinitionError(AgentError):
    code = AgentErrorCode.AGENT_DEFINITION_INVALID


class AgentDuplicateError(AgentError):
    code = AgentErrorCode.AGENT_DUPLICATE


class AgentImplementationNotFoundError(AgentError):
    code = AgentErrorCode.AGENT_IMPLEMENTATION_NOT_FOUND


ERROR_TYPES: dict[AgentErrorCode, type[AgentError]] = {
    cls.code: cls
    for cls in (
        AgentNotFoundError, AgentDisabledError, AgentInputError, AgentContextError, AgentPermissionError,
        AgentModelUnavailableError, AgentToolUnavailableError, AgentExecutionError, AgentResultError,
        AgentDefinitionError, AgentDuplicateError, AgentImplementationNotFoundError,
    )
}
