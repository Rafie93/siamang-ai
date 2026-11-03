"""Agent Interface (Phase 11).

Defines what an agent is (``AgentDefinition``), what it receives
(``AgentRequest``, ``AgentContext``), what it returns (``AgentResult``), how
it can reach models (``ModelAccess`` → ModelRouter) and tools
(``ToolAccess`` → ToolRegistry → PermissionPolicy), and the in-memory
``AgentRegistry`` that validates every invocation.

No specialized agent exists: the only implementation is the deterministic
reference ``agent.echo``. Nothing here plans, loops, retries, calls another
agent, changes task state, emits task events, or persists anything.
"""

from siamang.agents.context import AgentContext, AgentProject, ModelAccess, ToolAccess, build_context
from siamang.agents.definition import AgentCapability, AgentDefinition
from siamang.agents.errors import AgentError, AgentErrorCode
from siamang.agents.interface import Agent
from siamang.agents.registry import AgentRegistry, build_agent_registry
from siamang.agents.request import AgentRequest
from siamang.agents.result import AgentResult, AgentStatus

__all__ = [
    "Agent",
    "AgentCapability",
    "AgentContext",
    "AgentDefinition",
    "AgentError",
    "AgentErrorCode",
    "AgentProject",
    "AgentRegistry",
    "AgentRequest",
    "AgentResult",
    "AgentStatus",
    "ModelAccess",
    "ToolAccess",
    "build_agent_registry",
    "build_context",
]
