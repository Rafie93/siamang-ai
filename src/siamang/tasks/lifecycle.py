"""Task lifecycle: statuses, actions, and the only allowed transitions.

::

    queued ──start──► running ──complete──► completed
      ▲  ▲              │  ▲
      │  └──requeue── waiting ◄─wait─┘   (waiting ──start──► running)
      │
      └──retry── failed ◄──fail── queued | running | waiting
                 cancelled ◄──cancel── queued | running | waiting

``completed`` and ``cancelled`` are final. ``failed`` is final except for
the explicit, counted ``retry`` action (``failed → queued``). Anything not in
:data:`TRANSITIONS` is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from siamang.contracts import EventType
from siamang.tasks.errors import InvalidTransitionError


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})
ACTIVE = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING})


class TaskAction(StrEnum):
    START = "start"
    WAIT = "wait"
    REQUEUE = "requeue"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    RETRY = "retry"


S = TaskStatus
# action -> (allowed source statuses, target status)
TRANSITIONS: Mapping[TaskAction, tuple[frozenset[TaskStatus], TaskStatus]] = MappingProxyType({
    TaskAction.START: (frozenset({S.QUEUED, S.WAITING}), S.RUNNING),
    TaskAction.WAIT: (frozenset({S.RUNNING}), S.WAITING),
    TaskAction.REQUEUE: (frozenset({S.WAITING}), S.QUEUED),
    TaskAction.COMPLETE: (frozenset({S.RUNNING}), S.COMPLETED),
    TaskAction.FAIL: (frozenset({S.QUEUED, S.RUNNING, S.WAITING}), S.FAILED),
    TaskAction.CANCEL: (frozenset({S.QUEUED, S.RUNNING, S.WAITING}), S.CANCELLED),
    TaskAction.RETRY: (frozenset({S.FAILED}), S.QUEUED),
})

# The event each action appends (task creation appends task.accepted).
EVENTS: Mapping[TaskAction, EventType] = MappingProxyType({
    TaskAction.START: EventType.TASK_STARTED,
    TaskAction.WAIT: EventType.TASK_WAITING,
    TaskAction.REQUEUE: EventType.TASK_REQUEUED,
    TaskAction.COMPLETE: EventType.TASK_COMPLETED,
    TaskAction.FAIL: EventType.TASK_FAILED,
    TaskAction.CANCEL: EventType.TASK_CANCELLED,
    TaskAction.RETRY: EventType.TASK_RETRY_REQUESTED,
})

# Every allowed (from, to) edge, derived from TRANSITIONS.
EDGES: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    (src, target) for sources, target in TRANSITIONS.values() for src in sources
)


def plan(action: TaskAction | str, current: TaskStatus | str, *, task_id: str | None = None) -> TaskStatus:
    """Target status for ``action`` from ``current``, or :class:`InvalidTransitionError`."""
    action = TaskAction(action)
    current = TaskStatus(current)
    sources, target = TRANSITIONS[action]
    if current not in sources:
        raise InvalidTransitionError(str(action), str(current), task_id=task_id)
    return target
