"""SystemAnalyst: read-only analysis of a registered project for an existing task.

::

    AnalysisRequest (+ optional validated Plan, as data)
          │  analyze(request)
          ▼
    SystemAnalyst ──► TaskEngine.get           (read only; any non-final task)
          │       ──► ProjectRegistry.get      (exists, active)  ── root_path is the only authority
          │       ──► ProjectInspector         (bounded, read-only, no symlinks, no secret files)
          │       ──► GitWorkspaceManager      (Phase 08, read-only; optional)
          │       ──► AnalysisStrategy         (allowlisted by ID)
          ▼
    SystemAnalysis (validated, immutable, bounded; content_hash excludes analysis_id)

Steps (each can only refuse; the first refusal is raised as an AnalysisError):

 1. request is an AnalysisRequest              -> analysis_request_invalid
 2. instruction within policy                  -> analysis_policy_denied
 3. task exists (Task Engine)                  -> analysis_task_not_found
 4. task belongs to request.project_id         -> analysis_request_invalid
 5. task status analyzable (not final)         -> analysis_policy_denied
 6. project exists and is active (Registry)    -> analysis_project_not_found / analysis_project_archived
 7. project root usable, not protected          -> analysis_project_unavailable
 8. strategy analyzes the snapshot             -> analysis_internal (strategy crashed)
 9. policy caps (findings, components)         -> truncated deterministically, noted as an unknown
10. the SystemAnalysis validates itself        -> analysis_invalid
11. serialized size within policy              -> analysis_policy_denied

The analyst writes nothing, executes nothing (no agent, orchestrator, tool,
model, shell, or Git command of its own), never changes the task or the
project, persists nothing, and emits no events.
"""

from __future__ import annotations

from siamang.analyst.analysis import SystemAnalysis
from siamang.analyst.errors import (
    AnalysisError,
    AnalysisInternalError,
    AnalysisPolicyError,
    AnalysisProjectArchivedError,
    AnalysisProjectNotFoundError,
    AnalysisRequestError,
    AnalysisTaskNotFoundError,
)
from siamang.analyst.git_state import read_git_state
from siamang.analyst.inspection import ProjectInspector
from siamang.analyst.policy import DEFAULT_POLICY, AnalysisPolicy
from siamang.analyst.request import AnalysisRequest
from siamang.analyst.strategy import (
    KNOWN_STRATEGIES,
    AnalysisContext,
    AnalysisDraft,
    AnalysisStrategy,
    BasicStrategy,
    ProjectView,
)
from siamang.git.workspace import GitWorkspaceManager
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.projects import Project, ProjectNotFoundError
from siamang.tasks.definition import Task
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("analysis")


class SystemAnalyst:
    def __init__(
        self,
        tasks: TaskEngine,
        *,
        git: GitWorkspaceManager | None = None,
        policy: AnalysisPolicy = DEFAULT_POLICY,
        strategy: str = BasicStrategy.strategy_id,
    ):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if git is not None and type(git) is not GitWorkspaceManager:
            raise TypeError("git must be the GitWorkspaceManager")
        if not isinstance(policy, AnalysisPolicy):
            raise TypeError("policy must be an AnalysisPolicy")
        cls = KNOWN_STRATEGIES.get(strategy) if isinstance(strategy, str) else None
        if cls is None:
            raise ValueError("strategy must be a known strategy ID")
        self._tasks = tasks
        self._projects = tasks.projects  # the registry the engine validates against
        self._git = git
        self._strategy: AnalysisStrategy = cls()
        self.policy = policy

    @property
    def strategy_id(self) -> str:
        return self._strategy.strategy_id

    def analyze(self, request: AnalysisRequest) -> SystemAnalysis:
        if type(request) is not AnalysisRequest:  # 1
            raise AnalysisRequestError("request must be an AnalysisRequest")
        try:
            analysis = self._analyze(request)
        except AnalysisError as exc:
            log.info("analysis task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("analysis task=%s project=%s analysis=%s findings=%d components=%d: analyzed", analysis.task_id,
                 analysis.project_id, analysis.analysis_id, len(analysis.findings), len(analysis.components))
        return analysis

    def _analyze(self, request: AnalysisRequest) -> SystemAnalysis:
        self.policy.check_request(request)  # 2
        task = self._task(request.task_id)  # 3
        if task.project_id != request.project_id:  # 4
            raise AnalysisRequestError("task belongs to a different project", details=("project_id",))
        self.policy.check_task(task.status)  # 5
        project = self._active_project(request.project_id)  # 6
        inspector = ProjectInspector(project.root_path, self.policy)  # 7 (root must be a usable directory)
        git = read_git_state(self._git, project)  # 7 (protected repositories refuse the analysis)
        context = AnalysisContext(request, task, ProjectView(project.project_id, project.name,
                                                             str(project.project_type)),
                                  inspector, git, self.policy)
        draft = self._run(context)  # 8
        unknowns = list(draft.unknowns)
        metadata = dict(draft.metadata)
        findings, components = draft.findings, draft.components
        if len(findings) > self.policy.max_findings:  # 9
            unknowns.append(f"{len(findings) - self.policy.max_findings} further findings were omitted "
                            "(policy limit).")
            findings = findings[: self.policy.max_findings]
            metadata["truncated"] = True
        if len(components) > self.policy.max_components:
            unknowns.append(f"{len(components) - self.policy.max_components} further components were omitted "
                            "(policy limit).")
            components = components[: self.policy.max_components]
            metadata["truncated"] = True
        metadata.update(strategy=self._strategy.strategy_id, strategy_version=self._strategy.version)
        if request.plan is not None:
            metadata["plan_content_hash"] = request.plan.content_hash
        analysis = SystemAnalysis(  # 10
            analysis_id=new_id("analysis"),
            task_id=request.task_id,
            project_id=request.project_id,
            objective=request.instruction,
            summary=draft.summary,
            components=components,
            findings=findings,
            constraints=request.constraints,
            risks=draft.risks,
            unknowns=tuple(unknowns),
            recommendations=draft.recommendations,
            metadata=metadata,
        )
        if analysis.size > self.policy.max_output_size:  # 11
            raise AnalysisPolicyError(f"analysis exceeds {self.policy.max_output_size} bytes",
                                      details=("analysis",))
        return analysis

    # -- lookups (read only) -------------------------------------------------------------

    def _task(self, task_id: str) -> Task:
        failure: AnalysisError | None = None
        try:
            return self._tasks.get(task_id)
        except TaskNotFoundError:
            failure = AnalysisTaskNotFoundError("unknown task")
        except TaskError:
            failure = AnalysisRequestError("task_id is not valid", details=("task_id",))
        raise failure

    def _active_project(self, project_id: str) -> Project:
        failure: AnalysisError | None = None
        try:
            project = self._projects.get(project_id)
        except ProjectNotFoundError:
            failure = AnalysisProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise AnalysisProjectArchivedError("project is archived; restore it first")
        return project

    def _run(self, context: AnalysisContext) -> AnalysisDraft:
        failure: AnalysisError | None = None
        try:
            draft = self._strategy.analyze(context)
        except AnalysisError:
            raise
        except Exception as exc:  # never leak a traceback, path, or content from a strategy
            failure = AnalysisInternalError("analysis strategy failed", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if type(draft) is not AnalysisDraft:
            raise AnalysisInternalError("analysis strategy must return an AnalysisDraft")
        return draft
