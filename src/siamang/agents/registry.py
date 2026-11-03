"""Agent Registry: the single declaration and invocation boundary for agents.

::

    caller (future Orchestrator, Phase 12)
      │  invoke(agent_id, AgentRequest, AgentContext)
      ▼
    AgentRegistry ── allowlisted implementation (agents/implementations.py)
      ▼
    Agent.run(request, context) ──► AgentResult
                                     (context: ModelAccess ─► ModelRouter, ToolAccess ─► ToolRegistry)

Invocation order (every step can only deny):

 1. not already inside an agent invocation  -> agent_permission_denied (no agent-to-agent calls)
 2. resolve the agent                       -> agent_not_found
 3. agent enabled                           -> agent_disabled
 4. request is an AgentRequest              -> agent_input_invalid
 5. context is an AgentContext for this agent -> agent_context_invalid
 6. request.task_id / project_id == context's -> agent_context_invalid
 7. the context's task is running            -> agent_context_invalid
 8. strict input validation                 -> agent_input_invalid
 9. resolve the allowlisted implementation  -> agent_implementation_not_found
10. run                                     -> the agent's own AgentError, or agent_execution_failed
11. validate the result (identity, output contract) -> agent_result_invalid

``invoke`` never raises for these outcomes: it returns an
:class:`AgentResult` with ``status=failed`` and a structured error. It does
not retry, persist anything, emit task events, or change the task: the Task
Engine stays authoritative and a future orchestrator decides what a result
means. The registry is in-memory and code-owned (no persistence).
"""

from __future__ import annotations

from siamang.jsondata import thaw

import time
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

from siamang.agents.context import AgentContext
from siamang.agents.definition import AGENT_ID_RE, AgentDefinition, validate_input
from siamang.agents.errors import (
    AgentContextError,
    AgentDefinitionError,
    AgentDisabledError,
    AgentDuplicateError,
    AgentError,
    AgentExecutionError,
    AgentImplementationNotFoundError,
    AgentInputError,
    AgentNotFoundError,
    AgentPermissionError,
    AgentResultError,
)
from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS, is_owned
from siamang.agents.interface import Agent
from siamang.agents.reference import ECHO
from siamang.agents.request import AgentRequest
from siamang.agents.result import AgentResult, AgentStatus
from siamang.contracts.schema import check_object
from siamang.log import get_logger
from siamang.tasks.lifecycle import TaskStatus

log = get_logger("agents")

# The agent currently running in this thread/task, if any. One level only.
_ACTIVE: ContextVar[str | None] = ContextVar("siamang_active_agent", default=None)


class AgentRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._agents: dict[str, Agent] = {}

    # -- catalog -------------------------------------------------------------------

    def register(self, definition: AgentDefinition) -> AgentDefinition:
        if type(definition) is not AgentDefinition:
            raise AgentDefinitionError("only AgentDefinition objects can be registered")
        cls = KNOWN_IMPLEMENTATIONS.get(definition.implementation)
        if cls is None:
            raise AgentImplementationNotFoundError(f"unknown implementation {definition.implementation!r}",
                                                   agent_id=definition.agent_id)
        if definition.agent_id in self._definitions:
            raise AgentDuplicateError("agent_id is already registered", agent_id=definition.agent_id)
        self._agents[definition.agent_id] = cls(definition)
        self._definitions[definition.agent_id] = definition
        log.debug("registered agent %s", definition.agent_id)
        return definition

    def get(self, agent_id: str) -> AgentDefinition:
        definition = self._definitions.get(agent_id) if isinstance(agent_id, str) else None
        if definition is None:
            raise AgentNotFoundError("unknown agent", agent_id=_safe_id(agent_id))
        return definition

    def exists(self, agent_id: str) -> bool:
        return isinstance(agent_id, str) and agent_id in self._definitions

    def list(self) -> list[AgentDefinition]:
        """All registered agents (including disabled ones), sorted by ID."""
        return [self._definitions[k] for k in sorted(self._definitions)]

    # -- invocation ----------------------------------------------------------------

    def invoke(self, agent_id: str, request: AgentRequest, context: AgentContext) -> AgentResult:
        started = time.monotonic()
        safe_id = agent_id if self.exists(agent_id) else _safe_id(agent_id)
        task_id = request.task_id if isinstance(request, AgentRequest) else None
        token = None
        try:
            if _ACTIVE.get() is not None:  # 1
                raise AgentPermissionError("an agent may not invoke another agent; coordination belongs to the "
                                           "orchestrator", agent_id=safe_id)
            token = _ACTIVE.set(safe_id or "<invalid>")
            result = self._invoke(agent_id, request, context)
        except AgentError as exc:
            if exc.agent_id is None:
                exc.agent_id = safe_id
            result = AgentResult.failed(safe_id, task_id, exc)
        finally:
            if token is not None:
                _ACTIVE.reset(token)
        ms = int((time.monotonic() - started) * 1000)
        if result.error is not None:
            log.info("agent %s failed/denied for task %s: %s", safe_id, task_id, result.error.code)
        else:
            log.info("agent %s %s for task %s (%d ms)", safe_id, result.status, task_id, ms)
        definition = self._definitions.get(safe_id) if safe_id else None
        metadata = {
            **dict(result.metadata),
            "agent_version": definition.version if definition else None,
            "project_id": request.project_id if isinstance(request, AgentRequest) else None,
            "duration_ms": ms,
        }
        return AgentResult(result.agent_id, result.task_id, result.status, result.output, result.error,
                           MappingProxyType(metadata))

    def _invoke(self, agent_id: Any, request: Any, context: Any) -> AgentResult:
        definition = self._definitions.get(agent_id) if isinstance(agent_id, str) else None  # 2
        if definition is None:
            raise AgentNotFoundError("unknown agent")
        aid = definition.agent_id
        if not definition.enabled:  # 3
            raise AgentDisabledError("agent is disabled", agent_id=aid)
        if type(request) is not AgentRequest:  # 4
            raise AgentInputError("request must be an AgentRequest", agent_id=aid)
        if type(context) is not AgentContext or context.agent_id != aid:  # 5
            raise AgentContextError("context must be an AgentContext built for this agent", agent_id=aid)
        if request.task_id != context.task_id:  # 6
            raise AgentContextError("request is for a different task than the context", agent_id=aid)
        if request.project_id != context.project_id:
            raise AgentContextError("request is for a different project than the context", agent_id=aid)
        if context.task.status is not TaskStatus.RUNNING:  # 7
            raise AgentContextError(f"task is {context.task.status}, not running", agent_id=aid)
        validate_input(definition, request.input)  # 8
        agent = self._agents.get(aid)  # 9: re-checked against the allowlist at use
        if agent is None or not is_owned(definition.implementation, agent) or agent.definition is not definition:
            raise AgentImplementationNotFoundError("no allowlisted implementation for this agent", agent_id=aid)
        result = self._run(agent, request, context)  # 10
        self._check_result(definition, request, result)  # 11
        return result

    @staticmethod
    def _run(agent: Agent, request: AgentRequest, context: AgentContext) -> AgentResult:
        failure: AgentError | None = None
        try:
            return agent.run(request, context)
        except AgentError as exc:
            exc.agent_id = agent.agent_id
            failure = exc
        except Exception as exc:  # never leak a traceback or message from below
            failure = AgentExecutionError("agent execution failed", agent_id=agent.agent_id, cause=type(exc).__name__)
        raise failure

    @staticmethod
    def _check_result(definition: AgentDefinition, request: AgentRequest, result: Any) -> None:
        aid = definition.agent_id
        if type(result) is not AgentResult:
            raise AgentResultError("agent must return an AgentResult", agent_id=aid)
        if result.agent_id != aid or result.task_id != request.task_id:
            raise AgentResultError("result identity does not match the invocation", agent_id=aid)
        if result.status is AgentStatus.COMPLETED:
            unknown = sorted(set(result.output) - set(definition.output_schema))
            violations = check_object(thaw(result.output), definition.output_schema, "output")
            if unknown or violations:
                raise AgentResultError("output does not match the agent contract", agent_id=aid,
                                       details=tuple(unknown + [str(v) for v in violations[:10]]))


def _safe_id(agent_id: Any) -> str | None:
    return agent_id if isinstance(agent_id, str) and AGENT_ID_RE.fullmatch(agent_id) else None


def build_agent_registry() -> AgentRegistry:
    """The Phase 11 registry: only the reference agent ``agent.echo``."""
    registry = AgentRegistry()
    registry.register(ECHO)
    return registry
