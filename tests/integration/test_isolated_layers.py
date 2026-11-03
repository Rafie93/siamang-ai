"""Phase 19.5: each approval layer on its own (found by mutation testing).

Layered checks mask each other: a scenario that several layers refuse cannot
show that any single layer works. Every test here keeps the rest of the chain
consistent and breaks exactly one thing, so exactly one check can refuse it.
Each has a positive control proving the forged chain is otherwise accepted.

Where an Approval check repeats a Phase 18 LoopResult invariant (which
Approval re-runs), the LoopResult re-validation is bypassed for that one test
(``_loop`` returns its input), as a stand-in for a regression in Phase 18:
the Approval check must still hold on its own.
"""

import dataclasses
import hashlib
import hmac
from types import MappingProxyType

import pytest

from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.approval import ApprovalRequest, ApprovalStatus
from siamang.approval import provenance as provenance_mod
from siamang.approval.errors import ApprovalError, ApprovalErrorCode
from siamang.loop import LoopPolicy
from siamang.loop.result import NOT_DONE
from siamang.planner import Planner, PlanningRequest
from siamang.programmer.safety import thaw
from siamang.tasks import TaskRequest
from tests.support.analyst import SECRET
from tests.support.approval import (
    CLEAN,
    CONCERN,
    KEY,
    approval_request,
    approve,
    fabricate,
    fabricate_patch,
    loop_result,
    reject,
    service,
)
from tests.support.chain import rebind
from tests.support.programmer import INSTRUCTION, make_setup
from tests.support.qa import forge

C = ApprovalErrorCode
ZERO = "sha256:" + "0" * 64


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


def refused_at_request(s, task_id, project_id, plan, analysis, loop):
    with pytest.raises(ApprovalError) as info:
        ApprovalRequest(task_id, project_id, plan, analysis, loop)
    return info.value


def other_task_artifacts(s):
    engine = s.world.engine
    task = engine.create(TaskRequest(project_id=s.world.project.project_id, description="Another task",
                                     source="test")).task
    ids = dict(task_id=task.task_id, project_id=s.world.project.project_id, instruction=INSTRUCTION)
    plan = Planner(engine).plan(PlanningRequest(**ids))
    return plan, SystemAnalyst(engine).analyze(AnalysisRequest(**ids, plan=plan))


def with_analysis_plan_hash(analysis, plan, **fields):
    return dataclasses.replace(analysis, **fields,
                               metadata={**thaw(analysis.metadata), "plan_content_hash": plan.content_hash})


# --- chain-wide identity (the Task Engine is the only outside reference) ----------------------------------------


def test_control_rebind_onto_the_same_identities_is_approvable(setup):
    s = setup
    loop = rebind(loop_result(s, CLEAN), plan=s.plan, analysis=s.analysis, task_id=s.world.task.task_id,
                  project_id=s.world.project.project_id)
    assert service(s).decide(approval_request(s, loop), approve()).approved


def test_chain_restamped_onto_another_project_than_the_tasks_is_refused(setup):
    """Every artifact says project B; the Task Engine says the task belongs to project A."""
    s = setup
    root_b = s.root.parent / "project_b"
    root_b.mkdir()
    project_b = s.world.projects.register(name="Project B", root_path=str(root_b)).project_id
    plan = dataclasses.replace(s.plan, project_id=project_b)
    analysis = with_analysis_plan_hash(s.analysis, plan, project_id=project_b)
    loop = rebind(loop_result(s, CLEAN), plan=plan, analysis=analysis, task_id=s.world.task.task_id,
                  project_id=project_b)
    req = ApprovalRequest(s.world.task.task_id, project_b, plan, analysis, loop)  # consistent on its own
    with pytest.raises(ApprovalError) as info:
        service(s).decide(req, approve())
    assert info.value.code is C.REQUEST_INVALID and info.value.details == ("project_id",)


def test_plan_of_another_task_fully_rebound_is_refused(setup):
    s = setup
    plan_b, _ = other_task_artifacts(s)
    analysis = with_analysis_plan_hash(s.analysis, plan_b)
    loop = rebind(loop_result(s, CLEAN), plan=plan_b, analysis=analysis, task_id=s.world.task.task_id,
                  project_id=s.world.project.project_id)  # loop, QA, review all name plan B by ID and hash
    error = refused_at_request(s, s.world.task.task_id, s.world.project.project_id, plan_b, analysis, loop)
    assert error.code is C.PROVENANCE_INVALID and error.details == ("plan",)


def test_analysis_of_another_task_fully_rebound_is_refused(setup):
    s = setup
    _, analysis_b = other_task_artifacts(s)
    analysis_b = with_analysis_plan_hash(analysis_b, s.plan)
    loop = rebind(loop_result(s, CLEAN), plan=s.plan, analysis=analysis_b, task_id=s.world.task.task_id,
                  project_id=s.world.project.project_id)
    error = refused_at_request(s, s.world.task.task_id, s.world.project.project_id, s.plan, analysis_b, loop)
    assert error.code is C.PROVENANCE_INVALID and error.details == ("analysis",)


# --- Approval bindings that repeat a LoopResult invariant (Phase 18 re-validation bypassed) ----------------------


@pytest.fixture
def no_loop_revalidation(monkeypatch):
    monkeypatch.setattr(provenance_mod, "_loop", lambda loop: loop)


def _request(s, loop):
    return ApprovalRequest(s.world.task.task_id, s.world.project.project_id, s.plan, s.analysis, loop)


def test_control_bypass_still_accepts_a_genuine_chain(setup, no_loop_revalidation):
    assert service(setup).decide(_request(setup, loop_result(setup, CLEAN)), approve()).approved


@pytest.mark.parametrize("field", ["patch_id", "patch_hash", "qa_id", "review_id"])
def test_final_round_record_not_matching_the_final_artifacts_is_refused(setup, no_loop_revalidation, field):
    loop = loop_result(setup, CLEAN)
    value = {"patch_id": "pch_01OTHER", "patch_hash": ZERO, "qa_id": "qa_01OTHER", "review_id": "rev_01OTHER"}[field]
    forged = forge(loop, iterations=(forge(loop.iterations[0], **{field: value}),))
    with pytest.raises(ApprovalError) as info:
        _request(setup, forged)
    assert info.value.details == ("final_iteration",)


def test_patch_of_another_task_fully_rebound_is_refused(setup, no_loop_revalidation):
    s = setup
    loop = loop_result(s, CLEAN)
    other_task = s.world.engine.create(TaskRequest(project_id=s.world.project.project_id, description="Other",
                                                   source="test")).task.task_id
    patch = dataclasses.replace(loop.final_patch, task_id=other_task)
    qa = dataclasses.replace(loop.final_qa_result, patch_hash=patch.content_hash)
    review = dataclasses.replace(loop.final_review_result, patch_hash=patch.content_hash, qa_hash=qa.content_hash)
    last = dataclasses.replace(loop.iterations[0], patch_hash=patch.content_hash)
    forged = forge(loop, iterations=(last,), final_patch=patch, final_qa_result=qa, final_review_result=review)
    with pytest.raises(ApprovalError) as info:
        _request(s, forged)
    assert info.value.details == ("patch",)


def test_qa_for_another_patch_hash_is_refused(setup, no_loop_revalidation):
    loop = loop_result(setup, CLEAN)
    qa = dataclasses.replace(loop.final_qa_result, patch_hash=ZERO)
    review = dataclasses.replace(loop.final_review_result, qa_hash=qa.content_hash)  # review agrees with that QA
    with pytest.raises(ApprovalError) as info:
        _request(setup, forge(loop, final_qa_result=qa, final_review_result=review))
    assert info.value.details == ("qa_result",)


def test_review_of_another_qa_hash_is_refused(setup, no_loop_revalidation):
    loop = loop_result(setup, CLEAN)
    review = dataclasses.replace(loop.final_review_result, qa_hash=ZERO)
    with pytest.raises(ApprovalError) as info:
        _request(setup, forge(loop, final_review_result=review))
    assert info.value.details == ("review_result",)


# --- validated form ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("summary", [f"Validate login. API_KEY={SECRET}", "  Validate login input.  "],
                         ids=["secret", "unnormalized"])
def test_patch_summary_forced_past_validation_and_rebound_is_refused(setup, summary):
    """Patch redacts/normalizes its summary instead of raising, so only re-hashing the rebuilt patch can tell."""
    loop = loop_result(setup, CLEAN)
    forged = forge(loop.final_patch, summary=summary)
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, fabricate_patch(loop, forged))  # every hash in the chain names the forged patch
    assert info.value.code is C.PROVENANCE_INVALID and info.value.details == ("patch",)
    assert SECRET not in str(info.value.to_dict())


def test_control_fabricated_but_unchanged_chain_is_approvable(setup):
    assert service(setup).decide(approval_request(setup, fabricate(loop_result(setup, CLEAN))), approve()).approved


@pytest.mark.parametrize("which", ["qa", "review"])
def test_evidence_not_from_its_producer_is_refused(setup, which):
    loop = loop_result(setup, CLEAN)
    artifact = loop.final_qa_result if which == "qa" else loop.final_review_result
    metadata = {**thaw(artifact.metadata), "strategy": f"{which}.handwritten"}
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, fabricate(loop, **{which: {"metadata": metadata}}))
    assert info.value.code is C.PROVENANCE_INVALID


def test_loop_with_extra_not_done_entries_is_refused(setup):
    loop = dataclasses.replace(loop_result(setup, CLEAN), not_done=(*NOT_DONE, "telemetry"))  # a valid LoopResult
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, loop)
    assert info.value.code is C.PROVENANCE_INVALID and info.value.details == ("loop_result",)


def test_loop_state_not_in_its_validated_form_is_refused(setup):
    """Same JSON hash, but not what validation would have produced ({1: ...} instead of {"1": ...})."""
    loop = loop_result(setup, CLEAN)
    forged = forge(loop, policy=MappingProxyType({**loop.policy, "extra": MappingProxyType({1: "x"})}))
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, forged)
    assert info.value.code is C.PROVENANCE_INVALID and info.value.details == ("loop_result",)


# --- verify: the recorded evidence must still describe the chain --------------------------------------------


def test_sealed_result_misstating_the_evidence_is_refused(setup):
    """A key holder (or a bug) seals a rejection that claims passing evidence for a failing chain."""
    s = setup
    req = approval_request(s, loop_result(s, CONCERN, policy=LoopPolicy(retry_statuses=set())))
    svc = service(s)
    honest = svc.decide(req, reject())
    assert honest.evidence["review_status"] == "concern"
    claimed = dataclasses.replace(honest, evidence={"qa_status": "pass", "review_status": "pass",
                                                    "loop_status": "pass", "loop_stop_reason": "success"})
    seal = "hmac-sha256:" + hmac.new(KEY, f"{claimed.approval_id}\n{claimed.approval_hash}".encode(),
                                     hashlib.sha256).hexdigest()
    sealed = dataclasses.replace(claimed, seal=seal)
    assert sealed.status is ApprovalStatus.REJECTED
    with pytest.raises(ApprovalError) as info:
        svc.verify(sealed, req)
    assert info.value.code is C.RESULT_INVALID
