"""ApprovalService: explicit decisions, strict evidence, authority, provenance, replay, tampering, fabrication."""

import dataclasses
import json

import pytest

from siamang.approval import DEFAULT_POLICY, ApprovalPolicy, ApprovalRequest, ApprovalService, ApprovalStatus
from siamang.approval.errors import ApprovalError, ApprovalErrorCode
from siamang.loop import LoopError, LoopPolicy
from siamang.qa import QAPolicy
from siamang.reviewer import ReviewerPolicy
from siamang.storage import Database
from siamang.tasks import TaskResult
from tests.support.approval import (
    APPROVER,
    CLEAN,
    CONCERN,
    approval_request,
    approve,
    fabricate,
    fabricate_patch,
    loop_result,
    reject,
    service,
    unrelated,
)
from tests.support.loop import CountingQA, CountingReviewer
from tests.support.programmer import make_setup, tree
from tests.support.qa import forge

C = ApprovalErrorCode


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def other(tmp_path):
    database = Database(tmp_path / "other.db")
    (tmp_path / "o").mkdir()
    yield make_setup(database, tmp_path / "o")
    database.close()


def refused(fn, *args):
    with pytest.raises(ApprovalError) as info:
        fn(*args)
    return info.value.code


def snapshot(req: ApprovalRequest):
    loop = req.loop_result
    return json.dumps([req.plan.to_dict(), req.analysis.to_dict(), loop.to_dict(), loop.final_patch.to_dict(),
                       loop.final_qa_result.to_dict(), loop.final_review_result.to_dict()], sort_keys=True)


# --- positive and explicit ------------------------------------------------------------------


def test_positive_approval(setup):
    req = approval_request(setup)
    before, files = snapshot(req), tree(setup.root)
    task = setup.world.engine.get(setup.world.task.task_id)
    svc = service(setup)
    result = svc.decide(req, approve(reason="QA and review both pass."))
    assert result.status is ApprovalStatus.APPROVED and result.approver_id == APPROVER
    assert result.provenance == req.provenance and dict(result.evidence) == {
        "qa_status": "pass", "review_status": "pass", "loop_status": "pass", "loop_stop_reason": "success"}
    assert result.approval_id.startswith("apr_") and result.reason == "QA and review both pass."
    svc.verify(result, req)
    assert snapshot(req) == before and tree(setup.root) == files and setup.world.engine.get(task.task_id) == task


def test_explicit_rejection_of_a_valid_chain(setup):
    req = approval_request(setup)
    before = snapshot(req)
    result = service(setup).decide(req, reject(reason="Wrong approach for this release."))
    assert result.status is ApprovalStatus.REJECTED and result.requirements == ()
    assert result.reason == "Wrong approach for this release." and snapshot(req) == before
    service(setup).verify(result, req)


def test_approval_binds_the_final_round_not_an_earlier_one(setup):
    loop = loop_result(setup, CONCERN, CLEAN)  # round 1 patch A (concern), round 2 patch B (pass)
    req = approval_request(setup, loop)
    result = service(setup).decide(req, approve())
    assert result.patch_id == loop.iterations[1].patch_id != loop.iterations[0].patch_id
    assert result.provenance.final_iteration == 2 and result.patch_hash == loop.final_patch.content_hash


def test_no_automatic_approval(setup):
    svc = service(setup)
    assert {n for n in dir(svc) if not n.startswith("_")} == {"decide", "verify", "policy"}
    with pytest.raises(TypeError):
        svc.decide(approval_request(setup))  # no decision -> nothing happens
    assert refused(svc.decide, approval_request(setup), {"decision": "approve", "approver_id": APPROVER}) \
        is C.REQUEST_INVALID


# --- evidence (strict default, no override) ------------------------------------------------------


def _loops(setup):
    qa_fails = CountingQA(setup.world.engine, policy=QAPolicy(max_changes=1))
    return {
        "review concern / no progress": loop_result(setup, CONCERN, CONCERN),
        "review fail / max_iterations": loop_result(setup, unrelated(1), unrelated(2), unrelated(3)),
        "QA fail": loop_result(setup, CLEAN, qa=qa_fails, policy=LoopPolicy(retry_statuses=set())),
        "inconclusive": loop_result(setup, CONCERN, reviewer=CountingReviewer(
            setup.world.engine, policy=ReviewerPolicy(max_findings=1))),
    }


def test_failing_evidence_cannot_be_approved_but_can_be_rejected(setup):
    svc = service(setup)
    for name, loop in _loops(setup).items():
        req = approval_request(setup, loop)
        assert refused(svc.decide, req, approve()) is C.NOT_ELIGIBLE, name
        result = svc.decide(req, reject())
        assert result.status is ApprovalStatus.REJECTED and result.evidence["loop_stop_reason"] != "success", name


def test_programmer_without_patch_gives_nothing_to_decide(setup):
    loop = loop_result(setup, "not json")
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, loop)
    assert info.value.code is C.PROVENANCE_INVALID


def test_closed_task_or_archived_project(db, tmp_path, other):
    s = make_setup(db, tmp_path, start=True)
    req = approval_request(s)
    s.world.engine.complete(s.world.task.task_id, TaskResult(summary="done"))
    assert refused(service(s).decide, req, approve()) is C.NOT_ELIGIBLE
    req2 = approval_request(other)
    other.world.projects.archive(other.world.project.project_id)
    assert refused(service(other).decide, req2, approve()) is C.PROJECT_ARCHIVED


# --- adversarial authorization (fabricated but self-consistent chains) ---------------------------


def test_qa_fail_dressed_as_pass_is_refused(setup):
    qa_fails = CountingQA(setup.world.engine, policy=QAPolicy(max_changes=1))
    honest = loop_result(setup, CLEAN, qa=qa_fails, policy=LoopPolicy(retry_statuses=set()))
    fake = fabricate(honest, qa={"status": "pass", "findings": ()}, review={"status": "pass", "findings": ()})
    req = approval_request(setup, fake)  # consistent: every ID and hash binds
    with pytest.raises(ApprovalError) as info:
        service(setup).decide(req, approve())
    assert info.value.code is C.PROVENANCE_INVALID and info.value.cause == "omits_static_findings"


def test_review_fail_or_concern_dressed_as_pass_is_refused(setup):
    for honest in (loop_result(setup, CONCERN, policy=LoopPolicy(retry_statuses=set())),
                   loop_result(setup, unrelated(1), policy=LoopPolicy(retry_statuses=set()))):
        fake = fabricate(honest, review={"status": "pass", "findings": ()})
        with pytest.raises(ApprovalError) as info:
            service(setup).decide(approval_request(setup, fake), approve())
        assert info.value.code is C.PROVENANCE_INVALID and info.value.cause == "findings_not_rederivable"


def test_loop_max_iterations_dressed_as_success_is_refused(setup):
    honest = loop_result(setup, unrelated(1), unrelated(2), CLEAN, policy=LoopPolicy(max_iterations=3))
    assert honest.status.value == "pass"
    stopped = loop_result(setup, unrelated(1), unrelated(2), unrelated(3))
    with pytest.raises(LoopError):  # a LoopResult cannot even be built that way
        dataclasses.replace(stopped, status="pass", stop_reason="success")
    with pytest.raises(ApprovalError) as info:  # forced past validation: refused as provenance
        approval_request(setup, forge(stopped, status="pass", stop_reason="success"))
    assert info.value.code is C.PROVENANCE_INVALID
    assert refused(service(setup).decide, approval_request(setup, stopped), approve()) is C.NOT_ELIGIBLE


def test_unauthorized_approvers(setup):
    req = approval_request(setup)
    for svc, who in ((service(setup), "mallory"), (service(setup), "admin"), (service(setup), "root"),
                     (service(setup, policy=DEFAULT_POLICY), APPROVER),
                     (service(setup, policy=ApprovalPolicy()), "rafie")):
        assert refused(svc.decide, req, approve(who)) is C.UNAUTHORIZED
        assert refused(svc.decide, req, reject(who)) is C.UNAUTHORIZED


def test_reason_bound_comes_from_the_policy(setup):
    req = approval_request(setup)
    svc = service(setup, policy=ApprovalPolicy(authorized_approvers={APPROVER}, max_reason_length=20))
    assert refused(svc.decide, req, reject(reason="x" * 21)) is C.REQUEST_INVALID
    assert svc.decide(req, reject(reason="x" * 20)).status is ApprovalStatus.REJECTED


def test_all_evidence_passes_but_patch_hash_changed(setup):
    req = approval_request(setup)
    patch = req.loop_result.final_patch
    object.__setattr__(patch, "summary", "Changed after the request was made.")  # in-place tampering
    assert refused(service(setup).decide, req, approve()) is C.PROVENANCE_INVALID


# --- provenance mismatches -----------------------------------------------------------------------


def test_identity_mismatches(setup, other):
    loop = loop_result(setup, CLEAN)
    foreign = loop_result(other, CLEAN)
    patch, qa, review = loop.final_patch, loop.final_qa_result, loop.final_review_result
    attempts = {
        "task": dict(task_id=other.world.task.task_id),
        "project": dict(project_id=other.world.project.project_id),
        "plan": dict(plan=other.plan),
        "analysis": dict(analysis=other.analysis),
        "plan content": dict(plan=dataclasses.replace(setup.plan, objective="Something else")),
        "analysis content": dict(analysis=dataclasses.replace(setup.analysis, summary="Another analysis.")),
        "loop of another task": dict(loop_result=foreign),
    }
    for name, over in attempts.items():
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, over.pop("loop_result", loop), **over)
        assert info.value.code in (C.PROVENANCE_INVALID, C.REQUEST_INVALID), name
    forged = {
        "patch id (same hash)": dict(final_patch=forge(patch, patch_id="pch_01OTHER")),
        "patch content (same id)": dict(final_patch=forge(patch, summary="Altered.")),
        "QA of another chain": dict(final_qa_result=foreign.final_qa_result),
        "QA hash": dict(final_qa_result=forge(qa, snapshot={"files": []})),
        "review of another chain": dict(final_review_result=foreign.final_review_result),
        "review hash": dict(final_review_result=forge(review, dimensions=review.dimensions[:-1])),
        "loop id": dict(loop_id="lop_../x"),
    }
    for name, fields in forged.items():
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, forge(loop, **fields))
        assert info.value.code is C.PROVENANCE_INVALID, name


def test_stale_round_evidence_is_refused(setup):
    loop = loop_result(setup, CONCERN, CLEAN)
    first = loop_result(setup, CONCERN, policy=LoopPolicy(retry_statuses=set()))
    for fields in ({"final_qa_result": first.final_qa_result}, {"final_review_result": first.final_review_result},
                   {"final_patch": first.final_patch}):
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, forge(loop, **fields))  # patch B with QA/review/patch of another round
        assert info.value.code is C.PROVENANCE_INVALID


# --- each layer on its own (the rest of the chain stays consistent) ---------------------------------


def test_request_swapped_to_another_valid_loop_is_stale(setup):
    req = approval_request(setup, loop_result(setup, CLEAN))
    other_loop = loop_result(setup, CONCERN, CLEAN)  # valid, same task: binds on its own
    object.__setattr__(req, "loop_result", other_loop)
    assert refused(service(setup).decide, req, approve()) is C.PROVENANCE_INVALID


def test_fully_rebound_invalid_patch_is_refused_at_request_time(setup):
    loop = loop_result(setup, CLEAN)
    patch = loop.final_patch
    for fields in ({"path": ".env"}, {"path": "../../x.py"}, {"metadata": {"a": [1]}}):
        bad = forge(patch, changes=(forge(patch.changes[0], **fields), *patch.changes[1:]))
        fake = fabricate_patch(loop, bad)  # every ID and hash in the chain points at the bad patch
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, fake)
        assert info.value.code is C.PROVENANCE_INVALID, fields


def test_qa_made_against_another_plan_or_analysis_is_refused(setup):
    loop = loop_result(setup, CLEAN)
    for key in ("plan_content_hash", "analysis_content_hash"):
        meta = {**loop.final_qa_result.metadata, key: "sha256:" + "0" * 64}
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, fabricate(loop, qa={"metadata": meta}))
        assert info.value.code is C.PROVENANCE_INVALID, key


def test_review_of_another_plan_or_analysis_is_refused(setup):
    loop = loop_result(setup, CLEAN)
    review_meta = loop.final_review_result.metadata
    for fields in ({"plan_id": "pln_01OTHER"}, {"analysis_id": "ana_01OTHER"},
                   {"metadata": {**review_meta, "plan_content_hash": "sha256:" + "0" * 64}},
                   {"metadata": {**review_meta, "analysis_content_hash": "sha256:" + "0" * 64}}):
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, fabricate(loop, review=fields))
        assert info.value.code is C.PROVENANCE_INVALID, fields


def test_loop_of_another_plan_or_analysis_is_refused(setup):
    loop = loop_result(setup, CLEAN)
    for fields in ({"plan_id": "pln_01OTHER"}, {"analysis_id": "ana_01OTHER"},
                   {"metadata": {**loop.metadata, "plan_content_hash": "sha256:" + "0" * 64}},
                   {"metadata": {**loop.metadata, "analysis_content_hash": "sha256:" + "0" * 64}}):
        with pytest.raises(ApprovalError) as info:
            approval_request(setup, dataclasses.replace(loop, **fields))  # a valid LoopResult on its own
        assert info.value.code is C.PROVENANCE_INVALID, fields


def test_final_artifacts_from_another_loop_are_refused(setup):
    run_a = loop_result(setup, CLEAN)
    run_b = loop_result(setup, CLEAN)  # same content, different identities
    swapped = forge(run_b, final_patch=run_a.final_patch, final_qa_result=run_a.final_qa_result,
                    final_review_result=run_a.final_review_result)  # consistent among themselves, not B's last round
    with pytest.raises(ApprovalError) as info:
        approval_request(setup, swapped)
    assert info.value.code is C.PROVENANCE_INVALID


def test_mutable_state_forced_into_evidence_is_refused(setup):
    loop = loop_result(setup, CLEAN)
    qa, review = loop.final_qa_result, loop.final_review_result
    for fields in ({"final_qa_result": forge(qa, findings=list(qa.findings))},
                   {"final_review_result": forge(review, findings=list(review.findings))}):
        with pytest.raises(ApprovalError) as info:  # same content and hash, but no longer immutable
            approval_request(setup, forge(loop, **fields))
        assert info.value.code is C.PROVENANCE_INVALID


# --- replay and verification ----------------------------------------------------------------------


def test_approval_cannot_be_reused_for_another_chain(setup, other):
    svc = service(setup)
    req_a = approval_request(setup, loop_result(setup, CLEAN))
    req_b = approval_request(setup, loop_result(setup, CONCERN, CLEAN))  # another patch, same task
    approval_a = svc.decide(req_a, approve())
    assert refused(svc.verify, approval_a, req_b) is C.RESULT_INVALID
    retargeted = dataclasses.replace(approval_a, provenance=req_b.provenance)  # same approval_id, other scope
    assert refused(svc.verify, retargeted, req_b) is C.RESULT_INVALID
    other_svc = service(other)
    req_other = approval_request(other)
    assert refused(other_svc.verify, approval_a, req_other) is C.RESULT_INVALID  # another task and project


def test_forged_or_altered_results_are_refused(setup):
    svc = service(setup)
    req = approval_request(setup)
    rejected = svc.decide(req, reject())
    for fields in ({"status": "approved", "reason": None, "requirements": rejected.requirements},
                   {"approver_id": "ops.lead"}, {"reason": "Different words."}):
        try:
            altered = dataclasses.replace(rejected, **fields)
        except ApprovalError:
            continue
        assert refused(svc.verify, altered, req) is C.RESULT_INVALID
    elsewhere = ApprovalService(setup.world.engine, policy=svc.policy)  # another service, another key
    assert refused(elsewhere.verify, rejected, req) is C.RESULT_INVALID
    assert refused(svc.verify, forge(rejected, seal="hmac-sha256:" + "0" * 64), req) is C.RESULT_INVALID


def test_verify_refuses_evidence_altered_after_approval(setup):
    svc = service(setup)
    for target in ("final_qa_result", "final_review_result", "final_patch"):
        req = approval_request(setup)
        approval = svc.decide(req, approve())
        artifact = req.loop_result.__dict__[target]
        name = {"final_qa_result": "metadata", "final_review_result": "metadata", "final_patch": "summary"}[target]
        object.__setattr__(artifact, name, {"strategy": "x"} if name == "metadata" else "Altered.")
        assert refused(svc.verify, approval, req) is C.PROVENANCE_INVALID, target


def test_verify_honours_the_current_policy(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, approve())
    narrowed = ApprovalService(setup.world.engine, policy=ApprovalPolicy(authorized_approvers={"ops.lead"}),
                               seal_key=b"k" * 32)
    assert refused(narrowed.verify, approval, req) is C.UNAUTHORIZED


def test_request_mutation_after_approval_does_not_change_the_result(setup):
    req = approval_request(setup)
    approval = service(setup).decide(req, approve())
    before = json.dumps(approval.to_dict(), sort_keys=True)
    object.__setattr__(req.loop_result.final_patch, "summary", "Mutated later.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.provenance = None
    assert json.dumps(approval.to_dict(), sort_keys=True) == before


# --- statelessness -------------------------------------------------------------------------------


def test_service_is_stateless(setup):
    svc = service(setup)
    state = dict(vars(svc))
    a = svc.decide(approval_request(setup), approve())
    b = svc.decide(approval_request(setup, loop_result(setup, CONCERN, CONCERN)), reject())
    with pytest.raises(ApprovalError):
        svc.decide(approval_request(setup, loop_result(setup, CONCERN, CONCERN)), approve())
    c = svc.decide(approval_request(setup), approve())
    assert dict(vars(svc)) == state
    assert (a.status, b.status, c.status) == (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
                                              ApprovalStatus.APPROVED)
    assert len({a.approval_id, b.approval_id, c.approval_id}) == 3 and a.patch_id != c.patch_id


def test_constructor_guards(setup):
    with pytest.raises(TypeError):
        ApprovalService(object())
    with pytest.raises(TypeError):
        ApprovalService(setup.world.engine, policy={"authorized_approvers": ["*"]})
    for key in (b"short", "k" * 32, bytearray(32)):
        with pytest.raises(TypeError):
            ApprovalService(setup.world.engine, seal_key=key)
