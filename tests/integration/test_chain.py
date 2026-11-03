"""Phase 19.5: the whole chain, end to end, from the real services (only the model is scripted).

    Task -> Plan -> SystemAnalysis -> Programmer -> ProposedPatch -> QA -> Reviewer -> Loop -> Approval

Covers identity and hash stability across every boundary, the positive
approval, explicit rejection, QA / Reviewer / Loop failures (each blocking
approval), every Loop terminal state, and stale-round evidence.
"""

import dataclasses

import pytest

from siamang.approval import ApprovalDecision, ApprovalResult, ApprovalStatus
from siamang.approval.errors import ApprovalError, ApprovalErrorCode
from siamang.approval.policy import REQUIREMENTS
from siamang.loop import LoopError, LoopPolicy
from siamang.loop.result import LoopStatus, StopReason
from siamang.qa import QAPolicy, QAService, QAStatus
from siamang.reviewer import ReviewerPolicy, ReviewerService, ReviewStatus
from tests.support.analyst import sample_project
from tests.support.approval import (
    APPROVER,
    CLEAN,
    CONCERN,
    approval_request,
    approve,
    fabricate,
    loop_result,
    reject,
    service,
    unrelated,
)
from tests.support.chain import build, files
from tests.support.programmer import make_setup, reply
from tests.support.qa import forge

C = ApprovalErrorCode


@pytest.fixture
def root(tmp_path):
    return sample_project(tmp_path / "banuva")


def refused(fn, *args):
    with pytest.raises(ApprovalError) as info:
        fn(*args)
    return info.value.code


# --- 7. cross-phase integration: identities and hashes stay stable across every boundary ----------------------


def test_identities_and_hashes_are_stable_across_the_whole_chain(db, root):
    before = files(root)
    c = build(db, root, CONCERN, CLEAN)  # round 1: review concern -> feedback; round 2: pass
    task_id, project_id = c.task.task_id, c.project.project_id
    plan, analysis, loop = c.plan, c.analysis, c.loop
    patch, qa, review = loop.final_patch, loop.final_qa_result, loop.final_review_result
    last = loop.iterations[-1]

    # task and project identity: one value end to end
    assert {plan.task_id, analysis.task_id, loop.task_id, patch.task_id, qa.task_id, review.task_id,
            *(i.task_id for i in loop.iterations)} == {task_id}
    assert {plan.project_id, analysis.project_id, loop.project_id, patch.project_id, qa.project_id,
            review.project_id, *(i.project_id for i in loop.iterations)} == {project_id}
    # plan and analysis identity: by ID and by the content hash every downstream stage recorded
    assert analysis.metadata["plan_content_hash"] == plan.content_hash
    assert loop.plan_id == review.plan_id == plan.plan_id
    assert loop.analysis_id == review.analysis_id == analysis.analysis_id
    for meta in (qa.metadata, review.metadata, loop.metadata):
        assert meta["plan_content_hash"] == plan.content_hash
        assert meta["analysis_content_hash"] == analysis.content_hash
    # patch identity and hash: recomputed, identical at every stage that names it
    assert patch.content_hash == patch.content_hash  # pure: stable on recomputation
    assert last.patch_id == qa.patch_id == review.patch_id == patch.patch_id
    assert last.patch_hash == qa.patch_hash == review.patch_hash == patch.content_hash
    # QA bound to the patch; review bound to QA + patch; loop bound to its final round
    assert last.qa_id == review.qa_id == qa.qa_id and review.qa_hash == qa.content_hash
    assert review.qa_status is qa.status is last.qa_status is QAStatus.PASS
    assert last.review_id == review.review_id and last.review_status is review.status is ReviewStatus.PASS
    assert [i.number for i in loop.iterations] == [1, 2] and loop.iterations[0].patch_id != patch.patch_id
    assert c.model.calls == 2  # one Programmer model call per round, and no other model call

    # approval binds every one of them
    result = c.service.decide(c.request, approve(reason="QA and review pass."))
    p = result.provenance
    assert (p.task_id, p.project_id) == (task_id, project_id)
    assert (p.plan_id, p.plan_hash) == (plan.plan_id, plan.content_hash)
    assert (p.analysis_id, p.analysis_hash) == (analysis.analysis_id, analysis.content_hash)
    assert (p.loop_id, p.loop_hash, p.final_iteration) == (loop.loop_id, loop.content_hash, 2)
    assert (p.patch_id, p.patch_hash) == (patch.patch_id, patch.content_hash)
    assert (p.qa_id, p.qa_hash) == (qa.qa_id, qa.content_hash)
    assert (p.review_id, p.review_hash) == (review.review_id, review.content_hash)
    c.service.verify(result, c.request)
    assert files(root) == before  # the proposal was never applied


# --- 8. positive end to end ----------------------------------------------------------------------------------


def test_positive_end_to_end_approval(db, root):
    c = build(db, root, CLEAN)
    loop = c.loop
    assert loop.final_patch is not None and loop.final_patch.changes  # Programmer -> valid patch
    assert loop.final_qa_result.status is QAStatus.PASS  # QA -> PASS
    assert loop.final_review_result.status is ReviewStatus.PASS  # Reviewer -> PASS
    assert (loop.status, loop.stop_reason) == (LoopStatus.PASS, StopReason.SUCCESS)  # Loop -> PASS / success
    result = c.service.decide(c.request, approve())
    assert result.status is ApprovalStatus.APPROVED and result.approved  # Approval -> APPROVED
    assert result.approver_id == APPROVER and APPROVER in c.service.policy.authorized_approvers
    assert result.requirements == REQUIREMENTS  # every mandatory evidence requirement was checked
    assert dict(result.evidence) == {"qa_status": "pass", "review_status": "pass", "loop_status": "pass",
                                     "loop_stop_reason": "success"}
    assert result.provenance == c.request.provenance and result.seal.startswith("hmac-sha256:")
    c.service.verify(result, c.request)


def test_approval_is_never_implicit(db, root):
    c = build(db, root, CLEAN)
    with pytest.raises(TypeError):
        c.service.decide(c.request)  # a passing loop is only eligible; no decision, no result
    for fake in ({"decision": "approve", "approver_id": APPROVER}, "approve", True, None):
        assert refused(c.service.decide, c.request, fake) is C.REQUEST_INVALID


# --- 9. rejection --------------------------------------------------------------------------------------------


def test_explicit_rejection(db, root):
    c = build(db, root, CLEAN)
    result = c.service.decide(c.request, reject(reason="Not for this release."))
    assert result.status is ApprovalStatus.REJECTED and not result.approved
    assert result.requirements == () and result.reason == "Not for this release."
    c.service.verify(result, c.request)  # a valid rejection verifies as a rejection, nothing more


def test_rejection_needs_a_reason():
    for reason in (None, "", "   "):
        with pytest.raises(ApprovalError) as info:
            ApprovalDecision("reject", APPROVER, reason)
        assert info.value.code is C.REQUEST_INVALID


def test_rejection_reason_limits(db, root):
    c = build(db, root, CLEAN)
    assert c.service.policy.max_reason_length == 300
    assert c.service.decide(c.request, reject(reason="x" * 300)).status is ApprovalStatus.REJECTED
    assert refused(c.service.decide, c.request, reject(reason="x" * 301)) is C.REQUEST_INVALID  # policy bound
    with pytest.raises(ApprovalError):
        reject(reason="x" * 601)  # hard cap, before any policy


@pytest.mark.parametrize("reason", [
    "token sk-SECRET-DO-NOT-LEAK-31337", "password=hunter2hunter2", "see /etc/passwd", "see ~/.ssh/id_rsa",
    "C:\\Users\\x", "\\\\server\\share", "two\nlines", "bell\x07", "nul\x00", "esc\x1b[31m",
    "-----BEGIN PRIVATE KEY-----",
])
def test_unsafe_rejection_reason_is_refused(reason):
    with pytest.raises(ApprovalError) as info:
        ApprovalDecision("reject", APPROVER, reason)
    assert info.value.code is C.REQUEST_INVALID and reason not in str(info.value.to_dict())


def test_rejection_cannot_become_approval(db, root):
    c = build(db, root, CLEAN)
    rejected = c.service.decide(c.request, reject())
    # the evidence passes, so an "approved" record over it is constructible, but it was never sealed as one
    flipped = dataclasses.replace(rejected, status="approved", requirements=REQUIREMENTS)
    assert flipped.approved and refused(c.service.verify, flipped, c.request) is C.RESULT_INVALID
    flipped_no_reason = dataclasses.replace(rejected, status="approved", requirements=REQUIREMENTS, reason=None)
    assert refused(c.service.verify, flipped_no_reason, c.request) is C.RESULT_INVALID
    # forcing the status past validation is refused too
    assert refused(c.service.verify, forge(rejected, status=ApprovalStatus.APPROVED), c.request) is C.RESULT_INVALID


def test_rejected_result_is_never_approval_evidence(db, root):
    c = build(db, root, CLEAN)
    rejected = c.service.decide(c.request, reject())
    c.service.verify(rejected, c.request)
    assert not rejected.approved and rejected.status is ApprovalStatus.REJECTED and rejected.requirements == ()
    with pytest.raises(ApprovalError):  # an "approved" record must list every requirement
        ApprovalResult(**{**rejected.__dict__, "status": "approved"})


# --- 10. QA failure ----------------------------------------------------------------------------------------


def test_qa_failure_blocks_approval(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _run(s, CLEAN, qa=QAService(s.world.engine, policy=QAPolicy(max_changes=1)),
                policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_qa_result.status is QAStatus.FAIL and loop.status is not LoopStatus.PASS
    req = approval_request(s, loop)
    svc = _svc(s)
    assert refused(svc.decide, req, approve()) is C.NOT_ELIGIBLE  # QA FAIL is never QA PASS
    assert svc.decide(req, reject()).status is ApprovalStatus.REJECTED  # but a consistent chain can be rejected


def test_qa_fail_mutated_to_pass_in_place_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _run(s, CLEAN, qa=QAService(s.world.engine, policy=QAPolicy(max_changes=1)),
                policy=LoopPolicy(retry_statuses=set()))
    req = approval_request(s, loop)
    object.__setattr__(loop.final_qa_result, "status", QAStatus.PASS)  # after the request was made
    assert refused(_svc(s).decide, req, approve()) is C.PROVENANCE_INVALID


def test_qa_fail_forged_to_pass_before_the_request_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _run(s, CLEAN, qa=QAService(s.world.engine, policy=QAPolicy(max_changes=1)),
                policy=LoopPolicy(retry_statuses=set()))
    with pytest.raises(ApprovalError) as info:
        approval_request(s, forge(loop, final_qa_result=forge(loop.final_qa_result, status=QAStatus.PASS)))
    assert info.value.code is C.PROVENANCE_INVALID


def test_qa_fail_rewritten_as_a_consistent_pass_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _run(s, CLEAN, qa=QAService(s.world.engine, policy=QAPolicy(max_changes=1)),
                policy=LoopPolicy(retry_statuses=set()))
    fake = fabricate(loop, qa={"status": "pass", "findings": ()}, review={"status": "pass", "findings": ()})
    req = approval_request(s, fake)  # every ID and hash binds
    with pytest.raises(ApprovalError) as info:
        _svc(s).decide(req, approve())
    assert info.value.code is C.PROVENANCE_INVALID and info.value.cause == "omits_static_findings"


# --- 11. Reviewer failure ------------------------------------------------------------------------------------


def _review_fail(s):
    loop = _run(s, unrelated(1), policy=LoopPolicy(retry_statuses=set()))
    assert loop.final_qa_result.status is QAStatus.PASS and loop.final_review_result.status is ReviewStatus.FAIL
    return loop


def test_review_failure_blocks_approval(db, tmp_path):
    s = make_setup(db, tmp_path)
    req = approval_request(s, _review_fail(s))
    assert refused(_svc(s).decide, req, approve()) is C.NOT_ELIGIBLE
    assert _svc(s).decide(req, reject()).status is ApprovalStatus.REJECTED


def test_review_fail_mutated_to_pass_in_place_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _review_fail(s)
    req = approval_request(s, loop)
    object.__setattr__(loop.final_review_result, "status", ReviewStatus.PASS)
    assert refused(_svc(s).decide, req, approve()) is C.PROVENANCE_INVALID


def test_review_fail_forged_to_pass_before_the_request_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _review_fail(s)
    with pytest.raises(ApprovalError) as info:
        approval_request(s, forge(loop, final_review_result=forge(loop.final_review_result,
                                                                  status=ReviewStatus.PASS)))
    assert info.value.code is C.PROVENANCE_INVALID


def test_review_fail_rewritten_as_a_consistent_pass_is_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    fake = fabricate(_review_fail(s), review={"status": "pass", "findings": ()})
    with pytest.raises(ApprovalError) as info:
        _svc(s).decide(approval_request(s, fake), approve())
    assert info.value.code is C.PROVENANCE_INVALID and info.value.cause == "findings_not_rederivable"


# --- 12. every Loop terminal state ------------------------------------------------------------------------


def _run(s, *replies, policy=None, qa=None, reviewer=None):
    return loop_result(s, *replies, policy=policy, qa=qa, reviewer=reviewer)


def _svc(s):
    return service(s)


TERMINAL = {
    # name: (replies, loop policy, reviewer policy, expected status, expected stop reason)
    "not_retryable": ((CONCERN,), LoopPolicy(retry_statuses=set()), None, "concern", "not_retryable"),
    "inconclusive": ((CONCERN,), None, ReviewerPolicy(max_findings=1), "inconclusive", "inconclusive"),
    "max_iterations": ((unrelated(1), unrelated(2), unrelated(3)), None, None, "fail", "max_iterations"),
    "call_budget": ((CONCERN,), LoopPolicy(max_programmer_calls=1), None, "concern", "call_budget"),
    "no_progress": ((CONCERN, CONCERN), None, None, "concern", "no_progress"),
    "programmer_failed_after_a_round": ((CONCERN, "not json"), None, None, "concern", "programmer_failed"),
    "programmer_waiting_after_a_round": ((CONCERN, reply([], status="waiting", unknowns=["Which rule?"])), None,
                                         None, "concern", "programmer_waiting"),
}


def test_loop_pass_is_eligible(db, tmp_path):
    s = make_setup(db, tmp_path)
    loop = _run(s, CLEAN)
    assert (loop.status.value, loop.stop_reason.value) == ("pass", "success")
    assert _svc(s).decide(approval_request(s, loop), approve()).approved


@pytest.mark.parametrize("name", sorted(TERMINAL))
def test_loop_terminal_state_never_becomes_approval(db, tmp_path, name):
    replies, policy, reviewer_policy, status, stop = TERMINAL[name]
    s = make_setup(db, tmp_path)
    reviewer = None if reviewer_policy is None else ReviewerService(s.world.engine, policy=reviewer_policy)
    loop = _run(s, *replies, policy=policy, reviewer=reviewer)
    assert (loop.status.value, loop.stop_reason.value) == (status, stop)
    req = approval_request(s, loop)
    svc = _svc(s)
    assert refused(svc.decide, req, approve()) is C.NOT_ELIGIBLE
    rejected = svc.decide(req, reject())
    assert rejected.status is ApprovalStatus.REJECTED and rejected.evidence["loop_stop_reason"] == stop
    # dressed up as success: a LoopResult cannot be built that way, and a forced one is refused
    with pytest.raises(LoopError):
        dataclasses.replace(loop, status="pass", stop_reason="success")
    with pytest.raises(ApprovalError) as info:
        approval_request(s, forge(loop, status=LoopStatus.PASS, stop_reason=StopReason.SUCCESS))
    assert info.value.code is C.PROVENANCE_INVALID


@pytest.mark.parametrize("reply_text", ["not json", reply([], status="waiting", unknowns=["Which rule?"])],
                         ids=["programmer_failed", "programmer_waiting"])
def test_loop_without_any_patch_gives_nothing_to_approve(db, tmp_path, reply_text):
    s = make_setup(db, tmp_path)
    loop = _run(s, reply_text)
    assert loop.status is LoopStatus.INCONCLUSIVE and loop.final_patch is None
    with pytest.raises(ApprovalError) as info:
        approval_request(s, loop)
    assert info.value.code is C.PROVENANCE_INVALID


class _Boom(Exception):
    pass


@pytest.mark.parametrize("component", ["qa", "reviewer"])
def test_component_failure_stops_the_loop_safely(db, tmp_path, component):
    s = make_setup(db, tmp_path)
    before = files(s.root)

    class BrokenQA(QAService):
        def verify(self, request):
            raise _Boom("/Users/someone/.ssh/id_rsa sk-SECRET-DO-NOT-LEAK-31337")

    class BrokenReviewer(ReviewerService):
        def review(self, request):
            raise _Boom("/Users/someone/.ssh/id_rsa sk-SECRET-DO-NOT-LEAK-31337")

    kw = {"qa": BrokenQA(s.world.engine)} if component == "qa" else {"reviewer": BrokenReviewer(s.world.engine)}
    with pytest.raises(LoopError) as info:
        _run(s, CLEAN, **kw)
    text = str(info.value.to_dict())
    assert info.value.code.value == "loop_internal" and info.value.cause == "_Boom"
    assert "sk-SECRET" not in text and "/Users/someone" not in text
    assert files(s.root) == before  # no LoopResult exists, so there is nothing to approve; nothing was written


# --- stale round -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("artifact", ["final_patch", "final_qa_result", "final_review_result"])
def test_previous_round_evidence_is_not_final_round_evidence(db, tmp_path, artifact):
    s = make_setup(db, tmp_path)
    two_rounds = _run(s, CONCERN, CLEAN)
    round_one = _run(s, CONCERN, policy=LoopPolicy(retry_statuses=set()))  # the same round-1 proposal on its own
    assert round_one.final_patch.content_hash == two_rounds.iterations[0].patch_hash
    with pytest.raises(ApprovalError) as info:
        approval_request(s, forge(two_rounds, **{artifact: round_one.__dict__[artifact]}))
    assert info.value.code is C.PROVENANCE_INVALID


def test_approval_of_an_earlier_loop_does_not_cover_a_later_one(db, tmp_path):
    s = make_setup(db, tmp_path)
    svc = _svc(s)
    early = approval_request(s, _run(s, CLEAN))
    late = approval_request(s, _run(s, CONCERN, CLEAN))
    approval = svc.decide(early, approve())
    assert refused(svc.verify, approval, late) is C.RESULT_INVALID
    assert late.provenance.final_iteration == 2 and approval.provenance.final_iteration == 1
