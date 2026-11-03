"""OrchestrationPolicy: a few fixed admission rules. Not a planner, router, retry, or permission engine.

* Which task statuses admit an agent invocation. Phase 12 allows exactly
  ``running``: the caller starts the task through the Task Engine first,
  and the orchestrator never transitions it.
* Optionally, which registered agents may be orchestrated at all (an
  allowlist of agent IDs; ``None`` means every registered agent).
* One agent per orchestration (a constant, not a setting).

Tool permissions stay in the Phase 09 PermissionPolicy and model choice in
the Phase 06 router; this policy grants nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.agents.definition import AGENT_ID_RE
from siamang.orchestrator.errors import PolicyDeniedError, TaskNotRunningError
from siamang.tasks.lifecycle import TaskStatus

AGENTS_PER_ORCHESTRATION = 1
PHASE12_TASK_STATUSES = frozenset({TaskStatus.RUNNING})


@dataclass(frozen=True)
class OrchestrationPolicy:
    allowed_task_statuses: frozenset[TaskStatus] = PHASE12_TASK_STATUSES
    allowed_agents: frozenset[str] | None = None

    def __post_init__(self) -> None:
        try:
            statuses = frozenset(TaskStatus(s) for s in self.allowed_task_statuses)
        except (TypeError, ValueError):
            raise ValueError("allowed_task_statuses must be task statuses") from None
        if statuses != PHASE12_TASK_STATUSES:
            raise ValueError("Phase 12 orchestrates running tasks only")
        object.__setattr__(self, "allowed_task_statuses", statuses)
        if self.allowed_agents is not None:
            agents = self.allowed_agents
            if isinstance(agents, (str, bytes)) or not isinstance(agents, Iterable):
                raise ValueError("allowed_agents must be a collection of agent IDs")
            agents = frozenset(agents)
            if not all(isinstance(a, str) and AGENT_ID_RE.fullmatch(a) for a in agents):
                raise ValueError("allowed_agents must contain agent IDs like agent.<role>")
            object.__setattr__(self, "allowed_agents", agents)

    def check_agent(self, agent_id: str) -> None:
        if self.allowed_agents is not None and agent_id not in self.allowed_agents:
            raise PolicyDeniedError("agent is not allowed by the orchestration policy")

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.allowed_task_statuses:
            raise TaskNotRunningError(f"task is {status}; start it through the Task Engine first")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_task_statuses": sorted(str(s) for s in self.allowed_task_statuses),
            "allowed_agents": None if self.allowed_agents is None else sorted(self.allowed_agents),
            "agents_per_orchestration": AGENTS_PER_ORCHESTRATION,
            "transitions_task": False,
            "retries": False,
            "chains_agents": False,
        }


DEFAULT_POLICY = OrchestrationPolicy()
