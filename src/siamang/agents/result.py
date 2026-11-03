"""AgentResult: what one agent execution produced. Not the task's state.

``AgentStatus`` is local to one execution:

* ``completed``: the agent produced ``output`` (checked against its output contract);
* ``failed``: the agent could not; ``error`` says why;
* ``waiting``: the agent needs something (input, approval) before it can go on.

The Task Engine stays authoritative for the task lifecycle. Nothing here
changes a task; a future orchestrator decides what an AgentResult means for
the task (``complete``, ``fail``, ``wait``, ...).
"""

from __future__ import annotations

from siamang.jsondata import thaw

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.agents.definition import AGENT_ID_RE, canonical, plain_output
from siamang.agents.errors import AgentError, AgentResultError
from siamang.projects.definition import freeze_metadata
from siamang.tasks.definition import validate_task_id
from siamang.tasks.errors import InvalidTaskError


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass(frozen=True)
class AgentResult:
    agent_id: str | None  # None only for a failed result whose agent could not be identified
    task_id: str | None  # None only for a failed result whose request could not be read
    status: AgentStatus
    output: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    error: AgentError | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "status", AgentStatus(self.status))
        except ValueError:
            raise AgentResultError("unknown agent status", agent_id=_safe(self.agent_id)) from None
        identified = self.status is not AgentStatus.FAILED
        if (identified or self.agent_id is not None) and (
            not isinstance(self.agent_id, str) or not AGENT_ID_RE.fullmatch(self.agent_id)
        ):
            raise AgentResultError("agent_id must look like agent.<role>")
        if identified or self.task_id is not None:
            try:
                validate_task_id(self.task_id)
            except InvalidTaskError:
                raise AgentResultError("task_id must look like tsk_<id>", agent_id=_safe(self.agent_id)) from None
        if self.error is not None and not isinstance(self.error, AgentError):
            raise AgentResultError("error must be an AgentError", agent_id=_safe(self.agent_id))
        if (self.status is AgentStatus.FAILED) != (self.error is not None):
            raise AgentResultError("a failed result needs an error, and only a failed result has one",
                                   agent_id=_safe(self.agent_id))
        try:
            output = plain_output(self.output)
        except ValueError as exc:
            raise AgentResultError(str(exc), agent_id=_safe(self.agent_id)) from None
        object.__setattr__(self, "output", freeze_metadata(output))
        metadata = canonical(self.metadata, "metadata", _safe(self.agent_id), AgentResultError)
        object.__setattr__(self, "metadata", freeze_metadata(metadata))

    @property
    def success(self) -> bool:
        return self.status is AgentStatus.COMPLETED

    @classmethod
    def completed(cls, agent_id: str, task_id: str, output: Mapping[str, Any], **kw: Any) -> AgentResult:
        return cls(agent_id, task_id, AgentStatus.COMPLETED, output, **kw)

    @classmethod
    def waiting(cls, agent_id: str, task_id: str, output: Mapping[str, Any] | None = None, **kw: Any) -> AgentResult:
        return cls(agent_id, task_id, AgentStatus.WAITING, output or {}, **kw)

    @classmethod
    def failed(cls, agent_id: str | None, task_id: str | None, error: AgentError, **kw: Any) -> AgentResult:
        return cls(agent_id, task_id, AgentStatus.FAILED, error=error, **kw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "success": self.success,
            "status": str(self.status),
            "output": thaw(self.output),
            "error": self.error.to_dict() if self.error else None,
            "metadata": thaw(self.metadata),
        }


def _safe(agent_id: Any) -> str | None:
    return agent_id if isinstance(agent_id, str) else None
