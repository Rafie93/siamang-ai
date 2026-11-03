"""Orchestrator (Phase 12): one agent, one invocation, one running task.

Coordinates the Task Engine (read only), the Project Registry, and the
Agent Registry. It does not plan, choose a next agent, loop, retry, change
task state, persist, emit events, or reach models, tools, Git, or providers
itself.
"""

from siamang.orchestrator.errors import OrchestrationError, OrchestrationErrorCode
from siamang.orchestrator.policy import DEFAULT_POLICY, OrchestrationPolicy
from siamang.orchestrator.request import OrchestrationRequest
from siamang.orchestrator.result import OrchestrationResult, OrchestrationStatus
from siamang.orchestrator.service import Orchestrator

__all__ = [
    "DEFAULT_POLICY",
    "OrchestrationError",
    "OrchestrationErrorCode",
    "OrchestrationPolicy",
    "OrchestrationRequest",
    "OrchestrationResult",
    "OrchestrationStatus",
    "Orchestrator",
]
