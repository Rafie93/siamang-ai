"""Task Engine (Phase 10): task state, persistence, lifecycle, and events.

Manages state only. It never executes anything: no agent, orchestrator,
planner, model, tool, Git, subprocess, network, or channel integration.
"""

from siamang.tasks.definition import Task, TaskFailure, TaskPriority, TaskRequest, TaskResult, TaskSource
from siamang.tasks.engine import RecoveryReport, TaskEngine, TaskSubmission
from siamang.tasks.errors import (
    DuplicateTaskError,
    InvalidTaskError,
    InvalidTransitionError,
    NotRetryableError,
    RetryLimitReachedError,
    TaskError,
    TaskErrorCode,
    TaskNotFoundError,
    TaskProjectArchivedError,
    TaskProjectNotFoundError,
)
from siamang.tasks.lifecycle import TRANSITIONS, TaskAction, TaskStatus

__all__ = [
    "TRANSITIONS",
    "DuplicateTaskError",
    "InvalidTaskError",
    "InvalidTransitionError",
    "NotRetryableError",
    "RecoveryReport",
    "RetryLimitReachedError",
    "Task",
    "TaskAction",
    "TaskEngine",
    "TaskError",
    "TaskErrorCode",
    "TaskFailure",
    "TaskNotFoundError",
    "TaskPriority",
    "TaskProjectArchivedError",
    "TaskProjectNotFoundError",
    "TaskRequest",
    "TaskResult",
    "TaskSource",
    "TaskStatus",
    "TaskSubmission",
]
