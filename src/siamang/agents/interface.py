"""The Agent interface: a typed execution capability, not an application.

Synchronous, like the rest of SIAMANG. An agent gets one
:class:`~siamang.agents.request.AgentRequest` and one
:class:`~siamang.agents.context.AgentContext` and returns one
:class:`~siamang.agents.result.AgentResult`. It does not choose the next
agent, call other agents, retry, change task state, commit, or ask for
approval; those decisions belong to the future Orchestrator (Phase 12).

Agents are not called directly by other code: :meth:`AgentRegistry.invoke`
validates the request, the context, and the result around :meth:`Agent.run`.
Concrete classes are bound by ID through the code-owned allowlist in
:mod:`siamang.agents.implementations`; nothing is discovered or imported
dynamically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from siamang.agents.definition import AgentDefinition
from siamang.agents.errors import AgentDefinitionError

if TYPE_CHECKING:
    from siamang.agents.context import AgentContext
    from siamang.agents.request import AgentRequest
    from siamang.agents.result import AgentResult


class Agent(ABC):
    """Base class for every agent implementation. Instantiated only by the registry."""

    __slots__ = ("definition",)

    def __init__(self, definition: AgentDefinition):
        if not isinstance(definition, AgentDefinition):
            raise AgentDefinitionError("an agent needs an AgentDefinition")
        self.definition = definition

    @property
    def agent_id(self) -> str:
        return self.definition.agent_id

    @abstractmethod
    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        """Do one unit of work. Return a result; never mutate the task or call another agent."""
