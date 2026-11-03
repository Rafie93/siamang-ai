"""Test helpers for Approval: a real loop run (real Programmer with a scripted model, QA, Reviewer)."""

from __future__ import annotations

from siamang.approval import ApprovalDecision, ApprovalPolicy, ApprovalRequest, ApprovalService
from tests.support.loop import CLEAN, CONCERN, loop_request, make_loop, scripted, unrelated  # noqa: F401

APPROVER = "rafie"
POLICY = ApprovalPolicy(authorized_approvers={APPROVER, "ops.lead"})
KEY = b"k" * 32


def loop_result(setup, *replies, **kw):
    return make_loop(setup, scripted(*replies), **kw).run(loop_request(setup))


def approval_request(setup, loop=None, **over) -> ApprovalRequest:
    data = dict(task_id=setup.world.task.task_id, project_id=setup.world.project.project_id, plan=setup.plan,
                analysis=setup.analysis, loop_result=loop if loop is not None else loop_result(setup, CLEAN))
    data.update(over)
    return ApprovalRequest(**data)


def service(setup, policy=POLICY, key=KEY) -> ApprovalService:
    return ApprovalService(setup.world.engine, policy=policy, seal_key=key)


def approve(approver=APPROVER, reason=None):
    return ApprovalDecision("approve", approver, reason)


def reject(approver=APPROVER, reason="Not the change we want."):
    return ApprovalDecision("reject", approver, reason)


def fabricate(loop, *, qa=None, review=None):
    """A fully self-consistent but false chain: final QA/review replaced and every binding recomputed.

    Built only with legitimate constructors (dataclasses.replace), so it passes every consistency check; only
    re-deriving the evidence from the artifacts can expose it.
    """
    import dataclasses

    final_qa = dataclasses.replace(loop.final_qa_result, **(qa or {}))
    final_review = dataclasses.replace(loop.final_review_result, qa_hash=final_qa.content_hash,
                                       qa_status=final_qa.status, **(review or {}))
    last = dataclasses.replace(loop.iterations[-1], qa_status=final_qa.status, review_status=final_review.status,
                               feedback=(), feedback_truncated=False)
    passed = final_review.status.value == "pass"
    return dataclasses.replace(loop, iterations=(*loop.iterations[:-1], last), final_qa_result=final_qa,
                               final_review_result=final_review, status=final_review.status.value,
                               stop_reason="success" if passed else loop.stop_reason)


def fabricate_patch(loop, patch):
    """Swap the final patch and re-bind every hash and ID to it (QA, review, last round, loop): consistent, but false."""
    import dataclasses

    patch_hash = patch.content_hash
    qa = dataclasses.replace(loop.final_qa_result, patch_id=patch.patch_id, patch_hash=patch_hash)
    review = dataclasses.replace(loop.final_review_result, patch_id=patch.patch_id, patch_hash=patch_hash,
                                 qa_hash=qa.content_hash)
    last = dataclasses.replace(loop.iterations[-1], patch_id=patch.patch_id, patch_hash=patch_hash)
    return dataclasses.replace(loop, iterations=(*loop.iterations[:-1], last), final_patch=patch,
                               final_qa_result=qa, final_review_result=review)
