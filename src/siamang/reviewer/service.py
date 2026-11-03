"""ReviewerService: independent, static review of one QA-verified proposed change. Read-only, model-free.

::

    ReviewRequest (Task IDs + Plan + SystemAnalysis + Patch + QAResult as data, recorded patch_hash and qa_hash)
          │  review(request)
          ▼
    ReviewerService ──► TaskEngine.get        (read only; any non-final task)
          │         ──► ProjectRegistry.get   (exists, active; the project tree is never read)
          │         ──► re-validation and identity of every artifact (refusals raise)
          │         ──► deterministic judgments (checks.py)
          ▼
    ReviewResult (pass | concern | fail | inconclusive; validated, immutable, bounded)
          │
          X  nothing proceeds automatically: no approval, apply, repair, retry, or Programmer call

Input refusals are raised as a ReviewerError (the first one wins):

  1. request is a ReviewRequest                               -> reviewer_request_invalid
  2. task exists (Task Engine)                                -> reviewer_task_not_found
  3. task belongs to request.project_id                       -> reviewer_request_invalid
  4. task status reviewable (not final)                       -> reviewer_policy_denied
  5. project exists and is active (Registry)                  -> reviewer_project_not_found / _archived
  6. plan re-validates, same task and project                 -> reviewer_plan_invalid
  7. analysis re-validates, same task and project             -> reviewer_analysis_invalid
  8. patch: identity, within policy bounds, every change
     re-validates unaltered, recorded patch_hash matches      -> reviewer_patch_invalid / reviewer_policy_denied
  9. QAResult: re-validates unaltered, recorded qa_hash
     matches, made for this task, patch, plan, and analysis   -> reviewer_qa_invalid

Concerns about a consistent change are findings, never exceptions. An
unexpected failure of a judgment raises ``reviewer_internal`` with the
exception type only.

The Reviewer writes nothing and reads no project file. It executes nothing
(no project code, tests, shell, Git, or model), calls no other service
(Programmer, QA, Planner, Analyst, agents, Orchestrator, tools), never
changes the task, project, or any input artifact, persists nothing, and
emits no events. It does not approve, apply, or repair anything.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from siamang.analyst.analysis import SystemAnalysis
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.planner.plan import Plan
from siamang.programmer.change import ProposedChange
from siamang.programmer.patch import PATCH_ID_RE, Patch
from siamang.projects import Project, ProjectNotFoundError
from siamang.qa.checks import deep_frozen
from siamang.qa.result import QA_ID_RE, QAResult
from siamang.reviewer.checks import judge
from siamang.reviewer.errors import (
    ReviewAnalysisInvalidError,
    ReviewerError,
    ReviewInternalError,
    ReviewPatchInvalidError,
    ReviewPlanInvalidError,
    ReviewPolicyError,
    ReviewProjectArchivedError,
    ReviewProjectNotFoundError,
    ReviewQAInvalidError,
    ReviewRequestError,
    ReviewTaskNotFoundError,
)
from siamang.reviewer.policy import DEFAULT_POLICY, ReviewerPolicy
from siamang.reviewer.request import ReviewRequest
from siamang.reviewer.result import ReviewResult, derive_status
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("reviewer")
STRATEGY = "review.static"
VERSION = "1"
QA_STRATEGY = "qa.static"  # the only QA strategy whose results are accepted as evidence (qa.service.STRATEGY)


class ReviewerService:
    def __init__(self, tasks: TaskEngine, *, policy: ReviewerPolicy = DEFAULT_POLICY):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if not isinstance(policy, ReviewerPolicy):
            raise TypeError("policy must be a ReviewerPolicy")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against
        self.policy = policy

    def review(self, request: ReviewRequest) -> ReviewResult:
        if type(request) is not ReviewRequest:  # 1
            raise ReviewRequestError("request must be a ReviewRequest")
        try:
            result = self._review(request)
        except ReviewerError as exc:
            log.info("review task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("review task=%s project=%s review=%s qa=%s status=%s findings=%d", result.task_id,
                 result.project_id, result.review_id, result.qa_status, result.status, len(result.findings))
        return result

    def _review(self, request: ReviewRequest) -> ReviewResult:
        task = self._task(request.task_id)  # 2
        if task.project_id != request.project_id:  # 3
            raise ReviewRequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 4
        self._active_project(request.project_id)  # 5
        plan = self._plan(request.plan, task)  # 6
        analysis = self._analysis(request.analysis, task)  # 7
        changes = self._patch(request.patch, request.patch_hash, task)  # 8
        qa = self._qa(request.qa_result, request.qa_hash, request, plan, analysis)  # 9
        failure: ReviewerError | None = None
        try:
            return self._run(request, plan, analysis, changes, qa)
        except ReviewerError:
            raise
        except Exception as exc:  # never leak a traceback, path, or content
            failure = ReviewInternalError("review judgment failed", cause=type(exc).__name__)
        raise failure

    def _run(self, request: ReviewRequest, plan: Plan, analysis: SystemAnalysis,
             changes: tuple[ProposedChange, ...], qa: QAResult) -> ReviewResult:
        findings = judge(changes, plan, analysis, qa, self.policy)
        operations = {op: sum(1 for c in changes if c.operation.value == op) for op in ("create", "modify", "delete")}
        return ReviewResult(
            review_id=new_id("review"),
            task_id=request.task_id,
            project_id=request.project_id,
            plan_id=plan.plan_id,
            analysis_id=analysis.analysis_id,
            patch_id=request.patch.patch_id,
            patch_hash=request.patch_hash,
            qa_id=qa.qa_id,
            qa_hash=request.qa_hash,
            qa_status=qa.status,
            status=derive_status(findings),
            findings=findings,
            policy=self.policy.to_dict(),
            metadata={"strategy": STRATEGY, "strategy_version": VERSION, "plan_content_hash": plan.content_hash,
                      "analysis_content_hash": analysis.content_hash, "changes_reviewed": len(changes),
                      "operations": operations, "proposed_bytes": sum(c.size for c in changes)},
        )

    # -- input validation (raises) -------------------------------------------------------

    def _task(self, task_id: str) -> Task:
        failure: ReviewerError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = ReviewTaskNotFoundError("unknown task")
        except TaskError:
            failure = ReviewRequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> Project:
        failure: ReviewerError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = ReviewProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise ReviewProjectArchivedError("project is archived; restore it first")
        return project

    @staticmethod
    def _plan(plan: Plan, task: Task) -> Plan:
        failure: ReviewerError | None = None
        try:  # rebuild so every Plan invariant runs again; never repaired
            checked = dataclasses.replace(plan, steps=tuple(dataclasses.replace(s) for s in plan.steps))
        except Exception as exc:
            failure = ReviewPlanInvalidError("plan does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise ReviewPlanInvalidError("plan is for a different task or project", details=("plan",))
        return checked

    @staticmethod
    def _analysis(analysis: SystemAnalysis, task: Task) -> SystemAnalysis:
        failure: ReviewerError | None = None
        try:
            checked = dataclasses.replace(
                analysis, findings=tuple(dataclasses.replace(f) for f in analysis.findings),
                components=tuple(dataclasses.replace(c) for c in analysis.components))
        except Exception as exc:
            failure = ReviewAnalysisInvalidError("analysis does not validate", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if checked.task_id != task.task_id or checked.project_id != task.project_id:
            raise ReviewAnalysisInvalidError("analysis is for a different task or project", details=("analysis",))
        return checked

    def _patch(self, patch: Patch, patch_hash: str, task: Task) -> tuple[ProposedChange, ...]:
        """The patch's changes, re-validated and unaltered, or a refusal. A bad patch is never reviewed."""
        if not isinstance(patch.patch_id, str) or not PATCH_ID_RE.fullmatch(patch.patch_id):
            raise ReviewPatchInvalidError("patch_id must look like pch_<id>", details=("patch",))
        if patch.task_id != task.task_id or patch.project_id != task.project_id:
            raise ReviewPatchInvalidError("patch is for a different task or project", details=("patch",))
        changes = patch.changes
        if not isinstance(changes, tuple) or not all(type(c) is ProposedChange for c in changes):
            raise ReviewPatchInvalidError("patch changes are not a list of proposed changes", details=("patch",))
        if len(changes) > self.policy.max_changes:
            raise ReviewPolicyError(f"the patch has more than {self.policy.max_changes} changes to review",
                                    details=("patch",))
        failure: ReviewerError | None = None
        fresh: list[ProposedChange] = []
        total = 0
        for change in changes:
            try:  # every Phase 15 rule runs again (paths, forbidden files, secrets, text, hashes)
                again = dataclasses.replace(change)
                unaltered = (again.to_dict() == change.to_dict() and again.after_hash == change.after_hash
                             and deep_frozen(change.metadata) and deep_frozen(change.evidence))
            except Exception as exc:
                failure = ReviewPatchInvalidError("a change does not validate", cause=type(exc).__name__,
                                                  details=("patch",))
                break
            if not unaltered:
                failure = ReviewPatchInvalidError("a change was altered after it was validated", details=("patch",))
                break
            total += again.size
            if total > self.policy.max_total_bytes:
                failure = ReviewPolicyError(f"the patch proposes more than {self.policy.max_total_bytes} bytes",
                                            details=("patch",))
                break
            fresh.append(again)
        if failure is not None:
            raise failure
        try:  # conflicts (same path, case-only, file/directory), bounds, and summary, all again
            rebuilt = Patch(patch.patch_id, patch.task_id, patch.project_id, patch.summary, tuple(fresh))
            recomputed = patch.content_hash
        except Exception as exc:
            failure = ReviewPatchInvalidError("the patch does not validate", cause=type(exc).__name__,
                                              details=("patch",))
        if failure is not None:
            raise failure
        if recomputed != patch_hash or rebuilt.content_hash != patch_hash:
            raise ReviewPatchInvalidError("the patch does not match its recorded hash (altered or forged)",
                                          details=("patch_hash",))
        return rebuilt.changes

    @staticmethod
    def _qa(qa: QAResult, qa_hash: str, request: ReviewRequest, plan: Plan,
            analysis: SystemAnalysis) -> QAResult:
        """The QAResult, re-validated and bound to exactly these artifacts, or a refusal."""
        failure: ReviewerError | None = None
        try:  # rebuild so every QAResult invariant runs again (status derived from findings, bounds, ...)
            checked = dataclasses.replace(qa, findings=tuple(dataclasses.replace(f) for f in qa.findings))
            unaltered = (checked.content() == qa.content() and deep_frozen(qa.policy) and deep_frozen(qa.snapshot)
                         and deep_frozen(qa.metadata) and all(deep_frozen(f.metadata) for f in qa.findings))
            recomputed = qa.content_hash
        except Exception as exc:
            failure = ReviewQAInvalidError("the QA result does not validate", cause=type(exc).__name__,
                                           details=("qa_result",))
        if failure is not None:
            raise failure
        if not unaltered:
            raise ReviewQAInvalidError("the QA result was altered after it was produced", details=("qa_result",))
        if recomputed != qa_hash or checked.content_hash != qa_hash:
            raise ReviewQAInvalidError("the QA result does not match its recorded hash (altered or forged)",
                                       details=("qa_hash",))
        if not isinstance(checked.qa_id, str) or not QA_ID_RE.fullmatch(checked.qa_id):
            raise ReviewQAInvalidError("qa_id must look like qa_<id>", details=("qa_result",))
        meta = checked.metadata
        same = (checked.task_id == request.task_id and checked.project_id == request.project_id
                and checked.patch_id == request.patch.patch_id and checked.patch_hash == request.patch_hash
                and meta.get("strategy") == QA_STRATEGY and meta.get("plan_content_hash") == plan.content_hash
                and meta.get("analysis_content_hash") == analysis.content_hash
                and meta.get("changes_in_patch") == len(request.patch.changes))
        if not same:
            raise ReviewQAInvalidError("the QA result was produced for a different task, patch, plan, or analysis",
                                       details=("qa_result",))
        files = checked.snapshot.get("files", ())
        known = {(c.change_id, c.path) for c in request.patch.changes}
        if not isinstance(files, tuple) or not all(
                isinstance(e, Mapping) and (e.get("change_id"), e.get("path")) in known for e in files):
            raise ReviewQAInvalidError("the QA snapshot describes changes that are not in the patch",
                                       details=("qa_result",))
        return checked
