"""Programmer: turns Task + Plan + SystemAnalysis into a proposed Patch. Proposes; never applies.

::

    ProgrammingRequest (Plan + SystemAnalysis as data snapshots)
          │  program(request)
          ▼
    Programmer ──► TaskEngine.get            (read only; any non-final task)
          │    ──► ProjectRegistry.get       (exists, active)
          │    ──► ProjectSnapshot           (read only, via ProjectInspector: before-state, context files)
          │    ──► ProgrammingStrategy       (allowlisted by ID)
          │            └─► ProgrammerModelAccess ──► ModelRouter ──► ModelProvider   (at most one generation)
          ▼
    ProgrammingResult (completed + Patch | waiting | failed; validated, immutable, bounded)
          │
          X  nothing is written: a future Approval System decides whether a Patch may be applied

Input refusals are raised as a ProgrammingError (the first one wins):

 1. request is a ProgrammingRequest              -> programmer_request_invalid
 2. instruction within policy                    -> programmer_policy_denied
 3. task exists (Task Engine)                    -> programmer_task_not_found
 4. task belongs to request.project_id           -> programmer_request_invalid
 5. task status programmable (not final)         -> programmer_policy_denied
 6. project exists and is active (Registry)      -> programmer_project_not_found / _archived
 7. plan re-validates, same task and project     -> programmer_plan_invalid
 8. analysis re-validates, same task and project -> programmer_analysis_invalid
 9. project root usable                          -> programmer_project_unavailable
10. model context within policy                  -> programmer_policy_denied

The attempt itself never raises; it returns a ``failed`` result instead:

11. a model router is configured                 -> programmer_model_unavailable
12. the strategy asks the model once             -> programmer_model_unavailable / _timeout / _rejected
13. the reply parses strictly                    -> programmer_model_invalid_output
14. every change is safe and tied to a plan step -> programmer_change_rejected / programmer_model_invalid_output
15. the patch is consistent and within policy    -> programmer_patch_invalid / programmer_policy_denied
16. the result validates itself                  -> programmer_result_invalid

The Programmer writes nothing, executes nothing (no shell, Git, tool, agent,
orchestrator, planner, analyst service, QA, reviewer, or approval), never
changes the task or project, persists nothing, and emits no events.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.models.router import ModelRouter
from siamang.planner.plan import Plan
from siamang.programmer.access import ProgrammerModelAccess
from siamang.programmer.change import BeforeState, Operation, ProposedChange, change_id
from siamang.programmer.context import ProgrammingContext, build_context
from siamang.programmer.errors import (
    ChangeRejectedError,
    InvalidAnalysisInputError,
    InvalidPlanInputError,
    ProgrammerModelOutputError,
    ProgrammerModelUnavailableError,
    ProgrammingError,
    ProgrammingInternalError,
    ProgrammingPolicyError,
    ProgrammingProjectArchivedError,
    ProgrammingProjectNotFoundError,
    ProgrammingRequestError,
    ProgrammingTaskNotFoundError,
)
from siamang.programmer.patch import Patch
from siamang.programmer.policy import DEFAULT_POLICY, ProgrammingPolicy
from siamang.programmer.request import ProgrammingRequest
from siamang.programmer.result import NOTE_MAX, ProgrammingResult, ProgrammingStatus
from siamang.programmer.safety import find_secret
from siamang.programmer.snapshot import ProjectSnapshot
from siamang.programmer.strategy import KNOWN_STRATEGIES, ModelStrategy, ProgrammingDraft, ProgrammingStrategy
from siamang.programmer.tools import ToolRunner
from siamang.projects import Project, ProjectNotFoundError
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("programmer")


def _revalidated_plan(plan: Plan) -> Plan:
    """Rebuild the snapshot so its invariants run again (a frozen dataclass can still be forced)."""
    return dataclasses.replace(plan, steps=tuple(dataclasses.replace(s) for s in plan.steps))


def _revalidated_analysis(analysis: SystemAnalysis) -> SystemAnalysis:
    return dataclasses.replace(analysis, findings=tuple(dataclasses.replace(f) for f in analysis.findings),
                               components=tuple(dataclasses.replace(c) for c in analysis.components))


def _note(text: str) -> str:
    """A carried-over note that always fits a result: bounded, single-spaced, never credential-shaped."""
    text = " ".join(text.split())
    if find_secret(text):
        return "A note was withheld because it appears to contain credential material."
    return text if len(text) <= NOTE_MAX else text[: NOTE_MAX - 3] + "..."


def _capped(notes: list[str], limit: int, what: str) -> tuple[str, ...]:
    unique = list(dict.fromkeys(notes))
    if len(unique) <= limit:
        return tuple(unique)
    return (*unique[: limit - 1], f"{len(unique) - limit + 1} further {what} were omitted (policy limit).")


class Programmer:
    def __init__(
        self,
        tasks: TaskEngine,
        *,
        models: ModelRouter | None = None,
        policy: ProgrammingPolicy = DEFAULT_POLICY,
        strategy: str = ModelStrategy.strategy_id,
        tools: ToolRunner | None = None,
    ):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if models is not None and not isinstance(models, ModelRouter):
            raise TypeError("models must be the ModelRouter")
        if not isinstance(policy, ProgrammingPolicy):
            raise TypeError("policy must be a ProgrammingPolicy")
        cls = KNOWN_STRATEGIES.get(strategy) if isinstance(strategy, str) else None
        if cls is None:
            raise ValueError("strategy must be a known strategy ID")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against
        self._router = models
        self._strategy: ProgrammingStrategy = cls(tools) if cls.uses_tools else cls()
        self.policy = policy

    @property
    def strategy_id(self) -> str:
        return self._strategy.strategy_id

    def program(self, request: ProgrammingRequest) -> ProgrammingResult:
        if type(request) is not ProgrammingRequest:  # 1
            raise ProgrammingRequestError("request must be a ProgrammingRequest")
        try:
            result = self._program(request)
        except ProgrammingError as exc:
            log.info("programming task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("programming task=%s project=%s programming=%s status=%s changes=%d", result.task_id,
                 result.project_id, result.programming_id, result.status, len(result.changes))
        return result

    # -- input validation (raises) -------------------------------------------------------

    def _program(self, request: ProgrammingRequest) -> ProgrammingResult:
        self.policy.check_request(request)  # 2
        task = self._task(request.task_id)  # 3
        if task.project_id != request.project_id:  # 4
            raise ProgrammingRequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 5
        project = self._active_project(request.project_id)  # 6
        plan = self._plan(request.plan, task)  # 7
        analysis = self._analysis(request.analysis, task)  # 8
        snapshot = ProjectSnapshot(project.root_path, self.policy)  # 9
        context = build_context(request, task, project, snapshot, self.policy)  # 10
        notes = self._input_notes(plan, analysis, context)
        return self._attempt(context, snapshot, notes)

    def _task(self, task_id: str) -> Task:
        failure: ProgrammingError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = ProgrammingTaskNotFoundError("unknown task")
        except TaskError:
            failure = ProgrammingRequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> Project:
        failure: ProgrammingError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = ProgrammingProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise ProgrammingProjectArchivedError("project is archived; restore it first")
        return project

    @staticmethod
    def _plan(plan: Plan, task: Task) -> Plan:
        failure: ProgrammingError | None = None
        try:
            checked = _revalidated_plan(plan)
        except Exception as exc:  # PlanningError, TypeError, ...: never repaired, never trusted
            failure = InvalidPlanInputError("plan does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise InvalidPlanInputError("plan is for a different task or project", details=("plan",))
        if not checked.steps:
            raise InvalidPlanInputError("plan has no steps to implement", details=("plan",))
        return checked

    @staticmethod
    def _analysis(analysis: SystemAnalysis, task: Task) -> SystemAnalysis:
        failure: ProgrammingError | None = None
        try:
            checked = _revalidated_analysis(analysis)
        except Exception as exc:  # AnalysisError, TypeError, ...
            failure = InvalidAnalysisInputError("analysis does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise InvalidAnalysisInputError("analysis is for a different task or project", details=("analysis",))
        return checked

    @staticmethod
    def _input_notes(plan: Plan, analysis: SystemAnalysis, context: ProgrammingContext) -> dict[str, list[str]]:
        """What the inputs leave open. Analysis unknowns stay unknowns; inferences are flagged, never promoted."""
        warnings, unknowns = [], []
        planned = analysis.metadata.get("plan_content_hash")
        if planned is not None and planned != plan.content_hash:
            warnings.append("The system analysis was made against a different plan than the one supplied.")
        inferred = sum(1 for item in (*analysis.findings, *analysis.components) if str(item.confidence) == "inferred")
        if inferred:
            warnings.append(f"{inferred} analysis items are inferred, not observed; changes relying on them rest "
                            "on assumptions.")
        unknowns += [f"From analysis: {u}" for u in analysis.unknowns]
        unknowns += [f"Not shown to the model: {w}." for w in context.withheld]
        return {"warnings": [_note(w) for w in warnings], "unknowns": [_note(u) for u in unknowns]}

    # -- the attempt (returns a result; never raises for model output) -------------------

    def _attempt(self, context: ProgrammingContext, snapshot: ProjectSnapshot,
                 notes: dict[str, list[str]]) -> ProgrammingResult:
        request = context.request
        metadata: dict[str, Any] = {
            "strategy": self._strategy.strategy_id,
            "strategy_version": self._strategy.version,
            "plan_content_hash": context.plan.content_hash,
            "analysis_content_hash": context.analysis.content_hash,
            "context_files": [f.path for f in context.files],
            "withheld_files": len(context.withheld),
            "model_calls": 0,
        }
        if self._router is None:  # 11
            return self._failed(request, ProgrammerModelUnavailableError("no model router is configured",
                                                                         cause="no_router"), notes, metadata)
        access = ProgrammerModelAccess(self._router, self._strategy.generations)  # fresh; a fixed number of calls
        failure: ProgrammingError | None = None
        try:
            draft = self._strategy.propose(context, access)  # 12-13
            if type(draft) is not ProgrammingDraft:
                raise ProgrammingInternalError("strategy must return a ProgrammingDraft")
            metadata.update(draft.metadata)
            metadata["model_calls"] = access.calls
            return self._result(context, snapshot, draft, notes, metadata)  # 14-16
        except ProgrammingError as exc:
            failure = exc
        except Exception as exc:  # never leak a traceback, path, or model text from a strategy
            failure = ProgrammingInternalError("programming strategy failed", cause=type(exc).__name__)
        metadata["model_calls"] = access.calls
        return self._failed(request, failure, notes, metadata)

    def _result(self, context: ProgrammingContext, snapshot: ProjectSnapshot, draft: ProgrammingDraft,
                notes: dict[str, list[str]], metadata: dict[str, Any]) -> ProgrammingResult:
        policy, request = self.policy, context.request
        if len(draft.summary) > policy.max_summary:
            raise ProgrammingPolicyError(f"summary exceeds {policy.max_summary} characters", cause="model_output",
                                         details=("summary",))
        if len(draft.warnings) > policy.max_warnings or len(draft.unknowns) > policy.max_unknowns:
            raise ProgrammingPolicyError("too many warnings or unknowns", cause="model_output")
        warnings, unknowns = list(notes["warnings"]), list(notes["unknowns"])
        changes = []
        for i, item in enumerate(draft.changes, 1):
            changes.append(self._change(i, item, context, snapshot, warnings, unknowns))
        sizes = [c.size for c in changes]
        if any(s > policy.max_file_size for s in sizes):
            raise ProgrammingPolicyError(f"a proposed file exceeds {policy.max_file_size} bytes", cause="model_output")
        if sum(sizes) > policy.max_total_output:
            raise ProgrammingPolicyError(f"proposed content exceeds {policy.max_total_output} bytes",
                                         cause="model_output")
        warnings += list(draft.warnings)
        unknowns += list(draft.unknowns)
        status = ProgrammingStatus(draft.status)
        patch = None
        if status is ProgrammingStatus.COMPLETED:
            patch = Patch(new_id("patch"), request.task_id, request.project_id, draft.summary, tuple(changes))
        metadata["usage"] = snapshot.usage()
        return ProgrammingResult(
            programming_id=new_id("programming"),
            task_id=request.task_id,
            project_id=request.project_id,
            status=status,
            summary=draft.summary,
            patch=patch,
            warnings=_capped(warnings, policy.max_warnings, "warnings"),
            unknowns=_capped(unknowns, policy.max_unknowns, "unknowns"),
            metadata=metadata,
        )

    @staticmethod
    def _change(position: int, item: Any, context: ProgrammingContext, snapshot: ProjectSnapshot,
                warnings: list[str], unknowns: list[str]) -> ProposedChange:
        cid = change_id(position)
        if item["step_id"] not in context.step_ids:
            raise ProgrammerModelOutputError("change names a step that is not in the plan", details=(cid,))
        try:
            operation = Operation(item["operation"])
        except ValueError:
            raise ProgrammerModelOutputError("operation must be create, modify, or delete", details=(cid,)) from None
        if operation is Operation.DELETE and "content" in item:
            raise ProgrammerModelOutputError("a delete has no content", details=(cid,))
        if operation is not Operation.DELETE and "content" not in item:
            raise ProgrammerModelOutputError(f"a {operation} needs content", details=(cid,))
        # Path safety first (pure), so an unsafe path never reaches the filesystem boundary.
        probe = ProposedChange(cid, item["path"], Operation.DELETE, item["step_id"], "path check")
        before = snapshot.before(probe.path, cid)
        if before.state is BeforeState.UNVERIFIED:
            unknowns.append(_note(f"{probe.path}: current state not verified ({before.note}); re-check before "
                                  "applying."))
        if operation is Operation.CREATE and before.state is BeforeState.OBSERVED:
            # A file the model read and rewrote is a mislabelled modification. One it never saw would be overwritten blind.
            if not any(shown.path == probe.path for shown in context.files):
                raise ChangeRejectedError("cannot create a file that already exists and was not shown to the model",
                                          details=(cid, probe.path))
            operation = Operation.MODIFY
            warnings.append(_note(f"{probe.path}: proposed as create, but the file exists; treated as a modification."))
        if operation is not Operation.CREATE and before.state is BeforeState.ABSENT:
            raise ChangeRejectedError(f"cannot {operation} a file that does not exist", details=(cid, probe.path))
        return ProposedChange(cid, probe.path, operation, item["step_id"], item["reason"],
                              after_content=item.get("content"), before=before.state, before_hash=before.hash,
                              evidence=item.get("evidence", ()))

    def _failed(self, request: ProgrammingRequest, error: ProgrammingError, notes: dict[str, list[str]],
                metadata: dict[str, Any]) -> ProgrammingResult:
        return ProgrammingResult(
            programming_id=new_id("programming"),
            task_id=request.task_id,
            project_id=request.project_id,
            status=ProgrammingStatus.FAILED,
            summary="No changes were proposed.",
            warnings=_capped(notes["warnings"], self.policy.max_warnings, "warnings"),
            unknowns=_capped(notes["unknowns"], self.policy.max_unknowns, "unknowns"),
            error=error.to_dict(),
            metadata=metadata,
        )
