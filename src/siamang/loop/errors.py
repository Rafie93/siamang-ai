"""Normalized Loop errors.

Same convention as the other layers: a typed ``code``, a fixed redacted
``message``, ``cause`` (a component error code or an exception type name),
and ``details`` (field names, component names, iteration labels; never
values, file contents, model output, or machine paths).

Raised when the loop cannot continue *safely*: an invalid request, a
component refusing its input, or a component returning an artifact that
breaks the chain (wrong task, project, patch, QA result, or a reused
identity). Outcomes of a consistent chain (QA fail, review concern, no
progress, budget reached, ...) are never errors: they are a ``LoopResult``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class LoopErrorCode(StrEnum):
    REQUEST_INVALID = "loop_request_invalid"
    TASK_NOT_FOUND = "loop_task_not_found"
    COMPONENT_REFUSED = "loop_component_refused"
    CHAIN_BROKEN = "loop_chain_broken"
    RESULT_INVALID = "loop_result_invalid"
    INTERNAL = "loop_internal"


class LoopError(Exception):
    code: LoopErrorCode = LoopErrorCode.INTERNAL

    def __init__(self, message: str, *, cause: str | None = None, details: tuple[str, ...] = ()):
        self.message = redact(message)
        self.cause = cause
        self.details = tuple(redact(d) for d in details)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (cause={self.cause})" if self.cause else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "cause": self.cause, "details": list(self.details)}


class LoopRequestError(LoopError, ValueError):
    code = LoopErrorCode.REQUEST_INVALID


class LoopTaskNotFoundError(LoopError):
    code = LoopErrorCode.TASK_NOT_FOUND


class LoopComponentError(LoopError):
    """Programmer, QA, or Reviewer refused its input (``cause`` is the component's error code)."""

    code = LoopErrorCode.COMPONENT_REFUSED


class LoopChainError(LoopError):
    """A component returned an artifact that does not belong to this iteration's chain."""

    code = LoopErrorCode.CHAIN_BROKEN


class LoopResultError(LoopError, ValueError):
    code = LoopErrorCode.RESULT_INVALID


class LoopInternalError(LoopError):
    code = LoopErrorCode.INTERNAL


ERROR_TYPES: dict[LoopErrorCode, type[LoopError]] = {
    cls.code: cls
    for cls in (LoopRequestError, LoopTaskNotFoundError, LoopComponentError, LoopChainError, LoopResultError,
                LoopInternalError)
}
