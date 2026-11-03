"""QAService: verifies one proposed patch and returns an immutable QAResult. Static, read-only, model-free.

::

    QARequest (Plan + SystemAnalysis + Patch as data snapshots, recorded patch_hash)
          │  verify(request)
          ▼
    QAService ──► TaskEngine.get        (read only; any non-final task)
          │   ──► ProjectRegistry.get   (exists, active)
          │   ──► ProjectSnapshot       (Phase 15, via ProjectInspector: read only, no symlinks, no secret files)
          │   ──► deterministic checks  (checks.py)
          ▼
    QAResult (pass | fail | inconclusive; validated, immutable, bounded)
          │
          X  nothing proceeds automatically: no Reviewer, retry, repair, Programmer call, or approval

Input refusals are raised as a QAError (the first one wins):

 1. request is a QARequest                        -> qa_request_invalid
 2. task exists (Task Engine)                     -> qa_task_not_found
 3. task belongs to request.project_id            -> qa_request_invalid
 4. task status verifiable (not final)            -> qa_policy_denied
 5. project exists and is active (Registry)       -> qa_project_not_found / qa_project_archived
 6. plan re-validates, same task and project      -> qa_plan_invalid
 7. analysis re-validates, same task and project  -> qa_analysis_invalid
 8. patch identity: ID, same task and project     -> qa_patch_invalid
 9. project root usable                           -> qa_project_unavailable

Everything wrong *inside* the patch is a finding, never an exception, and
never a pass. An unexpected failure of a check raises ``qa_internal`` with
the exception type only.

QA writes nothing, executes nothing (no project code, tests, shell, Git, or
model), calls no other service (Programmer, Planner, Analyst, agents,
Orchestrator, tools), never changes the task or project, persists nothing,
and emits no events.
"""

from __future__ import annotations

import dataclasses

from siamang.analyst.analysis import SystemAnalysis
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.planner.plan import Plan
from siamang.programmer.errors import ProgrammingError
from siamang.programmer.patch import PATCH_ID_RE, Patch
from siamang.programmer.policy import ProgrammingPolicy
from siamang.programmer.snapshot import ProjectSnapshot
from siamang.projects import Project, ProjectNotFoundError
from siamang.qa.checks import Findings, bounded, check_analysis, check_patch, check_plan, check_project
from siamang.qa.errors import (
    QAAnalysisInvalidError,
    QAError,
    QAInternalError,
    QAPatchInvalidError,
    QAPlanInvalidError,
    QAProjectArchivedError,
    QAProjectNotFoundError,
    QAProjectUnavailableError,
    QARequestError,
    QATaskNotFoundError,
)
from siamang.qa.policy import DEFAULT_POLICY, QAPolicy
from siamang.qa.request import QARequest
from siamang.qa.result import QAResult, derive_status
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("qa")
STRATEGY = "qa.static"
VERSION = "1"


class QAService:
    def __init__(self, tasks: TaskEngine, *, policy: QAPolicy = DEFAULT_POLICY):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if not isinstance(policy, QAPolicy):
            raise TypeError("policy must be a QAPolicy")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against
        self.policy = policy

    def verify(self, request: QARequest) -> QAResult:
        if type(request) is not QARequest:  # 1
            raise QARequestError("request must be a QARequest")
        try:
            result = self._verify(request)
        except QAError as exc:
            log.info("qa task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("qa task=%s project=%s qa=%s status=%s findings=%d", result.task_id, result.project_id,
                 result.qa_id, result.status, len(result.findings))
        return result

    def _verify(self, request: QARequest) -> QAResult:
        task = self._task(request.task_id)  # 2
        if task.project_id != request.project_id:  # 3
            raise QARequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 4
        project = self._active_project(request.project_id)  # 5
        plan = self._plan(request.plan, task)  # 6
        analysis = self._analysis(request.analysis, task)  # 7
        patch = self._patch_identity(request.patch, task)  # 8
        snapshot = self._snapshot(project)  # 9
        failure: QAError | None = None
        try:
            return self._run(request, plan, analysis, patch, snapshot)
        except QAError:
            raise
        except Exception as exc:  # never leak a traceback, path, or content
            failure = QAInternalError("QA check failed", cause=type(exc).__name__)
        raise failure

    def _run(self, request: QARequest, plan: Plan, analysis: SystemAnalysis, patch: Patch,
             snapshot: ProjectSnapshot) -> QAResult:
        out = Findings()
        patch_hash, changes = check_patch(patch, request.patch_hash, self.policy, out)
        check_plan(changes, plan, out)
        check_analysis(changes, plan, analysis, out)
        entries = check_project(changes, snapshot, out)
        findings = bounded(out.items, self.policy.max_findings)
        return QAResult(
            qa_id=new_id("qa"),
            task_id=request.task_id,
            project_id=request.project_id,
            patch_id=patch.patch_id,
            patch_hash=patch_hash,
            status=derive_status(findings),
            findings=findings,
            policy=self.policy.to_dict(),
            snapshot={"files": entries, "usage": snapshot.usage()},
            metadata={"strategy": STRATEGY, "strategy_version": VERSION, "plan_content_hash": plan.content_hash,
                      "analysis_content_hash": analysis.content_hash, "changes_verified": len(changes),
                      "changes_in_patch": len(patch.changes) if isinstance(patch.changes, tuple) else 0},
        )

    # -- input validation (raises) -------------------------------------------------------

    def _task(self, task_id: str) -> Task:
        failure: QAError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = QATaskNotFoundError("unknown task")
        except TaskError:
            failure = QARequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> Project:
        failure: QAError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = QAProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise QAProjectArchivedError("project is archived; restore it first")
        return project

    @staticmethod
    def _plan(plan: Plan, task: Task) -> Plan:
        failure: QAError | None = None
        try:  # rebuild so every Plan invariant runs again; never repaired
            checked = dataclasses.replace(plan, steps=tuple(dataclasses.replace(s) for s in plan.steps))
        except Exception as exc:
            failure = QAPlanInvalidError("plan does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise QAPlanInvalidError("plan is for a different task or project", details=("plan",))
        return checked

    @staticmethod
    def _analysis(analysis: SystemAnalysis, task: Task) -> SystemAnalysis:
        failure: QAError | None = None
        try:
            checked = dataclasses.replace(
                analysis, findings=tuple(dataclasses.replace(f) for f in analysis.findings),
                components=tuple(dataclasses.replace(c) for c in analysis.components))
        except Exception as exc:
            failure = QAAnalysisInvalidError("analysis does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise QAAnalysisInvalidError("analysis is for a different task or project", details=("analysis",))
        return checked

    @staticmethod
    def _patch_identity(patch: Patch, task: Task) -> Patch:
        """Only the identity is checked here; everything inside the patch becomes findings."""
        if not isinstance(patch.patch_id, str) or not PATCH_ID_RE.fullmatch(patch.patch_id):
            raise QAPatchInvalidError("patch_id must look like pch_<id>", details=("patch",))
        if patch.task_id != task.task_id or patch.project_id != task.project_id:
            raise QAPatchInvalidError("patch is for a different task or project", details=("patch",))
        return patch

    def _snapshot(self, project: Project) -> ProjectSnapshot:
        p = self.policy
        budget = ProgrammingPolicy(  # the snapshot's read budget: one before-state read per change
            max_changes=p.max_changes, max_file_size=p.max_file_size,
            max_total_output=max(p.max_file_size, p.max_total_bytes), max_context_files=1,
            max_context_file_size=1024,
        )
        failure: QAError | None = None
        try:
            return ProjectSnapshot(project.root_path, budget)
        except ProgrammingError as exc:
            failure = QAProjectUnavailableError("project root is not usable", cause=exc.cause)
        raise failure
