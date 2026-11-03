"""OrchestrationResult: what one orchestration step produced. Not task state.

``status`` mirrors the single :class:`~siamang.agents.AgentResult` it
carries (``completed`` / ``failed`` / ``waiting``), or ``failed`` when the
orchestration was refused before any agent ran (then ``agent_result`` is
``None``). It is a subset of the Phase 10 vocabulary and never changes the
task: the Task Engine stays authoritative, and a later workflow decides
what a result means for the task.
"""

from __future__ import annotations

from siamang.jsondata import freeze, thaw

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.agents.result import AgentResult
from siamang.orchestrator.errors import OrchestrationError, OrchestrationResultError


class OrchestrationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass(frozen=True)
class OrchestrationResult:
    orchestration_id: str
    task_id: str | None
    project_id: str | None
    agent_id: str | None
    status: OrchestrationStatus
    agent_result: AgentResult | None = None
    error: OrchestrationError | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "status", OrchestrationStatus(self.status))
        except ValueError:
            raise OrchestrationResultError("unknown orchestration status") from None
        if not isinstance(self.orchestration_id, str) or not self.orchestration_id.startswith("orc_"):
            raise OrchestrationResultError("orchestration_id must look like orc_<id>")
        if self.agent_result is not None and type(self.agent_result) is not AgentResult:
            raise OrchestrationResultError("agent_result must be an AgentResult")
        if self.error is not None and not isinstance(self.error, OrchestrationError):
            raise OrchestrationResultError("error must be an OrchestrationError")
        if (self.status is OrchestrationStatus.FAILED) != (self.error is not None):
            raise OrchestrationResultError("a failed result needs an error, and only a failed result has one")
        if self.status is not OrchestrationStatus.FAILED and (
            self.agent_result is None or str(self.agent_result.status) != str(self.status)
        ):
            raise OrchestrationResultError("status must mirror the agent result")
        object.__setattr__(self, "metadata", freeze(json.loads(json.dumps(thaw(self.metadata)))))

    @property
    def success(self) -> bool:
        return self.status is OrchestrationStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "orchestration_id": self.orchestration_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "success": self.success,
            "status": str(self.status),
            "agent_result": self.agent_result.to_dict() if self.agent_result else None,
            "error": self.error.to_dict() if self.error else None,
            "metadata": thaw(self.metadata),
        }
