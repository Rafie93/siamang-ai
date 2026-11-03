"""Planner: turns a request for an existing task into a validated Plan. Decomposition only.

::

    PlanningRequest
          │  plan(request)
          ▼
    Planner ──► TaskEngine.get        (read only; the task may stay queued)
          │ ──► ProjectRegistry.get   (exists, active)
          │ ──► PlanningStrategy.propose (allowlisted; pure)
          │ ──► PlanningPolicy         (bounds)
          ▼
    Plan (validated: unique IDs, known dependencies, acyclic, ordered)

Steps (each can only refuse; the first refusal is raised as a PlanningError):

 1. request is a PlanningRequest               -> planning_request_invalid
 2. instruction within policy                  -> planning_policy_denied
 3. task exists (Task Engine)                  -> planning_task_not_found
 4. task belongs to request.project_id         -> planning_request_invalid
 5. task status plannable (not final)          -> planning_policy_denied
 6. project exists and is active (Registry)    -> planning_project_not_found / planning_project_archived
 7. strategy proposes steps                    -> planning_internal (strategy crashed)
 8. policy bounds the steps                    -> planning_too_many_steps / planning_policy_denied
 9. the Plan validates itself                  -> planning_invalid_plan / planning_dependency_cycle

The planner executes nothing: no agent, orchestrator, tool, model, Git,
filesystem, or process. It never changes the task or the project, persists
nothing, and emits no events. A plan is data for a later phase to consume.
"""

from __future__ import annotations

from siamang.ids import new_id
from siamang.log import get_logger
from siamang.planner.errors import (
    PlanningError,
    PlanningInternalError,
    PlanningProjectArchivedError,
    PlanningProjectNotFoundError,
    PlanningRequestError,
    PlanningTaskNotFoundError,
)
from siamang.planner.plan import Plan
from siamang.planner.policy import DEFAULT_POLICY, PlanningPolicy
from siamang.planner.request import PlanningRequest
from siamang.planner.step import PlanStep
from siamang.planner.strategy import KNOWN_STRATEGIES, PlanningStrategy, TemplateStrategy
from siamang.projects import ProjectNotFoundError
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("planning")


class Planner:
    def __init__(
        self,
        tasks: TaskEngine,
        *,
        policy: PlanningPolicy = DEFAULT_POLICY,
        strategy: str = TemplateStrategy.strategy_id,
    ):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if not isinstance(policy, PlanningPolicy):
            raise TypeError("policy must be a PlanningPolicy")
        cls = KNOWN_STRATEGIES.get(strategy) if isinstance(strategy, str) else None
        if cls is None:
            raise ValueError("strategy must be a known strategy ID")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against
        self._strategy: PlanningStrategy = cls()
        self.policy = policy

    @property
    def strategy_id(self) -> str:
        return self._strategy.strategy_id

    def plan(self, request: PlanningRequest) -> Plan:
        if type(request) is not PlanningRequest:  # 1
            raise PlanningRequestError("request must be a PlanningRequest")
        try:
            plan = self._plan(request)
        except PlanningError as exc:
            log.info("planning task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("planning task=%s project=%s plan=%s steps=%d: planned", plan.task_id, plan.project_id,
                 plan.plan_id, len(plan.steps))
        return plan

    def _plan(self, request: PlanningRequest) -> Plan:
        self.policy.check_request(request)  # 2
        task = self._task(request.task_id)  # 3
        if task.project_id != request.project_id:  # 4
            raise PlanningRequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 5
        self._active_project(request.project_id)  # 6
        steps = self._propose(request, task)  # 7
        self.policy.check_steps(steps)  # 8
        return Plan(  # 9
            plan_id=new_id("plan"),
            task_id=request.task_id,
            project_id=request.project_id,
            objective=request.instruction,
            steps=steps,
            constraints=request.constraints,
            metadata={"strategy": self._strategy.strategy_id, "strategy_version": self._strategy.version},
        )

    # -- lookups (read only) -------------------------------------------------------------

    def _task(self, task_id: str) -> Task:
        failure: PlanningError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = PlanningTaskNotFoundError("unknown task")
        except TaskError:
            failure = PlanningRequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> None:
        failure: PlanningError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = PlanningProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise PlanningProjectArchivedError("project is archived; restore it first")

    def _propose(self, request: PlanningRequest, task: Task) -> tuple[PlanStep, ...]:
        failure: PlanningError | None = None
        try:
            steps = self._strategy.propose(request, task)
        except PlanningError:
            raise
        except Exception as exc:  # never leak a traceback or message from a strategy
            failure = PlanningInternalError("planning strategy failed", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if not isinstance(steps, tuple):
            raise PlanningInternalError("planning strategy must return a tuple of steps")
        return steps
