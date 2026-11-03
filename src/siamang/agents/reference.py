"""The reference agent: ``agent.echo``. Deterministic, not AI.

It exists only to prove the path ``AgentRegistry → AgentContext →
Agent.run() → AgentResult``. It returns its input unchanged. It declares no
model requirements and no tool permissions, so its context has neither; it
never touches the task, a model, a tool, Git, or the filesystem.
"""

from __future__ import annotations

from siamang.agents.context import AgentContext
from siamang.agents.definition import AgentCapability, AgentDefinition
from siamang.agents.interface import Agent
from siamang.agents.request import AgentRequest
from siamang.agents.result import AgentResult
from siamang.contracts.schema import F

ECHO = AgentDefinition(
    agent_id="agent.echo",
    name="Echo (reference)",
    description="Deterministic reference agent: returns its input message unchanged. Not an AI agent.",
    version="1",
    role="echo",
    implementation="reference.echo",
    capabilities=frozenset({AgentCapability.TEXT_GENERATION}),
    input_schema={"message": F("text", required=True)},
    output_schema={"message": F("text", required=True)},
    metadata={"reference": True},
)


class EchoAgent(Agent):
    __slots__ = ()

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        return AgentResult.completed(self.agent_id, context.task_id, {"message": request.input["message"]})
