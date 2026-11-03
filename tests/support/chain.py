"""Phase 19.5 helpers: the whole chain, stage by stage, from the real services. Only the model is scripted.

    ProjectRegistry -> TaskEngine -> Planner -> SystemAnalyst -> LoopService(Programmer -> QA -> Reviewer)
        -> ApprovalRequest -> ApprovalService.decide / verify

Nothing here is mocked at a phase boundary: every artifact is produced by the
service that owns it and handed to the next one as-is.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.approval import ApprovalRequest, ApprovalService
from siamang.loop import LoopRequest, LoopService
from siamang.planner import Planner, PlanningRequest
from siamang.programmer import Programmer
from siamang.projects import ProjectRegistry
from siamang.qa import QAService
from siamang.reviewer import ReviewerService
from siamang.tasks import TaskEngine, TaskRequest
from tests.support.approval import KEY, POLICY
from tests.support.loop import scripted
from tests.support.programmer import INSTRUCTION
from tests.support.projects import Clock


@dataclass
class Chain:
    projects: ProjectRegistry
    engine: TaskEngine
    project: Any
    task: Any
    plan: Any
    analysis: Any
    model: Any
    loop_service: LoopService
    loop: Any
    request: ApprovalRequest
    service: ApprovalService


def build(db, root: Path, *replies: str, instruction: str = INSTRUCTION, name: str = "Banuva", loop_policy=None,
          qa: QAService | None = None, reviewer: ReviewerService | None = None, policy=POLICY,
          key: bytes = KEY) -> Chain:
    projects = ProjectRegistry(db)
    project = projects.register(name=name, root_path=str(root))
    engine = TaskEngine(db, projects, clock=Clock())
    task = engine.create(TaskRequest(project_id=project.project_id, description=instruction, source="test")).task
    ids = dict(task_id=task.task_id, project_id=project.project_id, instruction=instruction)
    plan = Planner(engine).plan(PlanningRequest(**ids))
    analysis = SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan))
    model = scripted(*replies)
    kw = {} if loop_policy is None else {"policy": loop_policy}
    loop_service = LoopService(engine, Programmer(engine, models=model.router), qa or QAService(engine),
                               reviewer or ReviewerService(engine), **kw)
    loop = loop_service.run(LoopRequest(**ids, plan=plan, analysis=analysis))
    request = ApprovalRequest(task.task_id, project.project_id, plan, analysis, loop)
    return Chain(projects, engine, project, task, plan, analysis, model, loop_service, loop, request,
                 ApprovalService(engine, policy=policy, seal_key=key))


def rebind(loop, *, plan, analysis, task_id: str, project_id: str):
    """A one-round chain re-stamped onto other identities with every recorded ID and hash recomputed.

    Built only with legitimate constructors (dataclasses.replace / Patch), so each artifact re-validates and every
    cross-reference agrees: only a check comparing the chain with something outside it (the Task Engine, another
    artifact's own task) can tell. Used to break exactly one layer while the rest of the chain stays consistent.
    """
    import dataclasses

    from siamang.programmer.patch import Patch
    from siamang.programmer.safety import thaw

    assert len(loop.iterations) == 1
    ids = dict(task_id=task_id, project_id=project_id)
    hashes = dict(plan_content_hash=plan.content_hash, analysis_content_hash=analysis.content_hash)
    old = loop.final_patch
    patch = Patch(old.patch_id, task_id, project_id, old.summary, old.changes)
    qa = dataclasses.replace(loop.final_qa_result, **ids, patch_hash=patch.content_hash,
                             metadata={**thaw(loop.final_qa_result.metadata), **hashes})
    review = dataclasses.replace(loop.final_review_result, **ids, plan_id=plan.plan_id,
                                 analysis_id=analysis.analysis_id, patch_hash=patch.content_hash,
                                 qa_hash=qa.content_hash, metadata={**thaw(loop.final_review_result.metadata),
                                                                    **hashes})
    last = dataclasses.replace(loop.iterations[0], **ids, patch_hash=patch.content_hash)
    return dataclasses.replace(loop, **ids, plan_id=plan.plan_id, analysis_id=analysis.analysis_id,
                               iterations=(last,), final_patch=patch, final_qa_result=qa, final_review_result=review,
                               metadata={**thaw(loop.metadata), **hashes})


def files(root: Path) -> dict[str, str]:
    """Every entry under ``root`` (files, dirs, symlinks) with a content or link hash: proves nothing changed."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "link:" + str(p.readlink())
        elif p.is_dir():
            out[rel] = "dir"
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "special"
    return out


def git_state(root: Path) -> tuple[str, ...]:
    """``git status --short``, ``rev-parse HEAD``, and ``diff --stat`` of a real repository (read-only commands)."""
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30,
                              check=True).stdout
    return run("status", "--short"), run("rev-parse", "HEAD"), run("diff", "--stat"), run("stash", "list"), \
        run("branch", "--list"), run("worktree", "list")
