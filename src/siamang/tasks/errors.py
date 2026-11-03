"""Typed Task Engine errors (``code`` + ``to_dict``, like model/git/tool errors).

Messages never include task descriptions, metadata, or paths.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class TaskErrorCode(StrEnum):
    INVALID_TASK = "invalid_task"
    TASK_NOT_FOUND = "task_not_found"
    DUPLICATE_TASK = "duplicate_task"
    INVALID_TRANSITION = "invalid_transition"
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_ARCHIVED = "project_archived"
    RETRY_LIMIT_REACHED = "retry_limit_reached"
    NOT_RETRYABLE = "not_retryable"


class TaskError(Exception):
    code: TaskErrorCode = TaskErrorCode.INVALID_TASK

    def __init__(self, message: str, *, task_id: str | None = None):
        self.message = message
        self.task_id = task_id
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (task={self.task_id})" if self.task_id else "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message, "task_id": self.task_id}


class InvalidTaskError(TaskError, ValueError):
    """A field is invalid. ``field`` names it."""

    code = TaskErrorCode.INVALID_TASK

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "field": self.field}


class TaskNotFoundError(TaskError, LookupError):
    code = TaskErrorCode.TASK_NOT_FOUND


class DuplicateTaskError(TaskError):
    """``task_id`` exists, or ``request_id`` was reused for a different request."""

    code = TaskErrorCode.DUPLICATE_TASK

    def __init__(self, field: str, existing_task_id: str):
        self.field = field
        super().__init__(f"{field} is already used by task {existing_task_id}", task_id=existing_task_id)


class InvalidTransitionError(TaskError):
    """The action is not allowed from the task's current status (or the task changed concurrently)."""

    code = TaskErrorCode.INVALID_TRANSITION

    def __init__(self, action: str, current: str, *, task_id: str | None = None):
        self.action = action
        self.current = current
        super().__init__(f"cannot {action} a task that is {current}", task_id=task_id)


class TaskProjectNotFoundError(TaskError, LookupError):
    code = TaskErrorCode.PROJECT_NOT_FOUND


class TaskProjectArchivedError(TaskError):
    code = TaskErrorCode.PROJECT_ARCHIVED


class RetryLimitReachedError(TaskError):
    code = TaskErrorCode.RETRY_LIMIT_REACHED


class NotRetryableError(TaskError):
    code = TaskErrorCode.NOT_RETRYABLE
