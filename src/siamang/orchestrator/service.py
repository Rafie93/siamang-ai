"""Orchestrator: coordinates exactly one agent invocation for one running task.

::

    OrchestrationRequest
          │  execute(request)
          ▼
    Orchestrator ──► TaskEngine.get        (read only; never start/wait/complete/fail/cancel/retry)
          │      ──► ProjectRegistry.get   (exists, active)
          │      ──► AgentRegistry.get     (registered, enabled)
          │      ──► build_context         (Phase 11: Task snapshot, project view, model/tool facades)
          ▼
    AgentRegistry.invoke(agent_id, AgentRequest, AgentContext)   ← called exactly once
          ▼
    OrchestrationResult (mirrors the AgentResult; task untouched)

Steps (each can only refuse; the first refusal is the result):

 1. not already inside an orchestration       -> policy_denied (no nesting)
 2. request is an OrchestrationRequest         -> request_invalid
 3. agent allowed by the policy                -> policy_denied
 4. task exists (Task Engine)                  -> task_not_found
 5. task belongs to request.project_id         -> request_invalid
 6. task is running (policy)                   -> task_not_running
 7. project exists and is active (Registry)    -> project_not_found / project_archived
 8. agent registered and enabled               -> agent_not_found / agent_disabled
 9. build the AgentContext (Phase 11)          -> context_invalid
10. AgentRegistry.invoke, once                 -> agent errors -> agent_invocation_failed (cause kept) ...
11. result identity and type                   -> result_invalid

It plans nothing, picks no "next" agent, retries nothing, loops over
nothing, changes no task state, persists nothing, and emits no events. It
never calls a model, a tool, Git, or a provider: the model router and tool
registry are passed through to ``build_context`` untouched, so agents reach
them only through ``ModelAccess`` / ``ToolAccess``.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from siamang.agents.context import build_context
from siamang.agents.errors import AgentError, AgentErrorCode, AgentNotFoundError
from siamang.agents.registry import AgentRegistry
from siamang.agents.result import AgentResult, AgentStatus
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.orchestrator.errors import (
    AgentInvocationError,
    OrchestrationAgentDisabledError,
    OrchestrationAgentNotFoundError,
    OrchestrationContextError,
    OrchestrationError,
    OrchestrationInternalError,
    OrchestrationProjectArchivedError,
    OrchestrationProjectNotFoundError,
    OrchestrationRequestError,
    OrchestrationResultError,
    OrchestrationTaskNotFoundError,
    PolicyDeniedError,
)
from siamang.orchestrator.policy import DEFAULT_POLICY, OrchestrationPolicy
from siamang.orchestrator.request import OrchestrationRequest
from siamang.orchestrator.result import OrchestrationResult, OrchestrationStatus
from siamang.projects import ProjectNotFoundError
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("orchestrator")

# The orchestration running in this thread/async context, if any. One level only.
_ACTIVE: ContextVar[str | None] = ContextVar("siamang_active_orchestration", default=None)

# Agent-layer codes with a direct orchestration equivalent; all others are agent_invocation_failed.
_AGENT_CODES: dict[AgentErrorCode, type[OrchestrationError]] = {
    AgentErrorCode.AGENT_NOT_FOUND: OrchestrationAgentNotFoundError,
    AgentErrorCode.AGENT_DISABLED: OrchestrationAgentDisabledError,
    AgentErrorCode.AGENT_CONTEXT_INVALID: OrchestrationContextError,
    AgentErrorCode.AGENT_RESULT_INVALID: OrchestrationResultError,
}

_STATUS = {
    AgentStatus.COMPLETED: OrchestrationStatus.COMPLETED,
    AgentStatus.WAITING: OrchestrationStatus.WAITING,
    AgentStatus.FAILED: OrchestrationStatus.FAILED,
}


def translate(error: AgentError) -> OrchestrationError:
    """Agent error -> orchestration error; the agent code is kept as ``cause``."""
    cls = _AGENT_CODES.get(error.code, AgentInvocationError)
    message = error.message if cls is not AgentInvocationError else "agent invocation failed"
    return cls(message, cause=str(error.code), retryable=error.retryable, details=error.details)


class Orchestrator:
    """``router`` and ``tools`` are handed to Phase 11 ``build_context`` as they are (a ModelRouter /
    ToolRegistry or ``None``); the orchestrator itself never uses them."""

    def __init__(
        self,
        tasks: TaskEngine,
        agents: AgentRegistry,
        *,
        router: Any = None,
        tools: Any = None,
        policy: OrchestrationPolicy = DEFAULT_POLICY,
    ):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if type(agents) is not AgentRegistry:
            raise TypeError("agents must be the AgentRegistry")
        if not isinstance(policy, OrchestrationPolicy):
            raise TypeError("policy must be an OrchestrationPolicy")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against (one database)
        self._agents = agents
        self._router = router
        self._tools = tools
        self.policy = policy

    def execute(self, request: OrchestrationRequest) -> OrchestrationResult:
        orchestration_id = new_id("orchestration")
        started = time.monotonic()
        known = request if type(request) is OrchestrationRequest else None
        agent_result: AgentResult | None = None
        token = None
        try:
            if _ACTIVE.get() is not None:  # 1
                raise PolicyDeniedError("nested orchestration is not allowed")
            token = _ACTIVE.set(orchestration_id)
            agent_result = self._execute(orchestration_id, request)
            error = None
        except OrchestrationError as exc:
            error = exc
        except Exception as exc:  # never leak a traceback or message from below
            error = OrchestrationInternalError("orchestration failed", cause=type(exc).__name__)
        finally:
            if token is not None:
                _ACTIVE.reset(token)
        if error is None and agent_result.status is AgentStatus.FAILED:
            error = translate(agent_result.error)
        status = OrchestrationStatus.FAILED if error is not None else _STATUS[agent_result.status]
        ms = int((time.monotonic() - started) * 1000)
        result = OrchestrationResult(
            orchestration_id,
            known.task_id if known else None,
            known.project_id if known else None,
            known.agent_id if known else None,
            status,
            agent_result,
            error,
            {"duration_ms": ms, "agents_invoked": 0 if agent_result is None else 1},
        )
        log.info("orchestration %s task=%s project=%s agent=%s: %s%s", orchestration_id, result.task_id,
                 result.project_id, result.agent_id, status, f" ({error.code})" if error else "")
        return result

    def _execute(self, orchestration_id: str, request: Any) -> AgentResult:
        if type(request) is not OrchestrationRequest:  # 2
            raise OrchestrationRequestError("request must be an OrchestrationRequest")
        self.policy.check_agent(request.agent_id)  # 3
        task = self._task(request.task_id)  # 4
        if task.project_id != request.project_id:  # 5
            raise OrchestrationRequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 6
        self._active_project(request.project_id)  # 7
        definition = self._agent(request.agent_id)  # 8
        try:  # 9
            context = build_context(definition, task=task, projects=self._projects, router=self._router,
                                    tools=self._tools, metadata={"orchestration_id": orchestration_id})
        except AgentError as exc:
            raise OrchestrationContextError("agent context could not be built", cause=str(exc.code)) from None
        result = self._agents.invoke(request.agent_id, request.to_agent_request(), context)  # 10: exactly once
        if type(result) is not AgentResult or result.agent_id not in (request.agent_id, None) or (  # 11
            result.task_id not in (request.task_id, None)
        ):
            raise OrchestrationResultError("agent result does not match the orchestration")
        return result

    # -- lookups (read only) -------------------------------------------------------------

    def _task(self, task_id: str) -> Task:
        failure: OrchestrationError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = OrchestrationTaskNotFoundError("unknown task")
        except TaskError:
            failure = OrchestrationRequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> None:
        failure: OrchestrationError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = OrchestrationProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise OrchestrationProjectArchivedError("project is archived; restore it first")

    def _agent(self, agent_id: str):
        failure: OrchestrationError | None = None
        try:
            definition = self._agents.get(agent_id)
        except AgentNotFoundError:
            failure = OrchestrationAgentNotFoundError("unknown agent")
        if failure is not None:
            raise failure
        if not definition.enabled:
            raise OrchestrationAgentDisabledError("agent is disabled")
        return definition
