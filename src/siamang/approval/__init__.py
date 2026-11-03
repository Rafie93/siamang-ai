"""Approval (Phase 19): the authorization boundary for a verified proposal.

An authorized approver makes an explicit decision (``approve`` or
``reject``) about exactly one loop's final proposal. Before any decision
the service verifies the whole evidence chain (Task, Plan, SystemAnalysis,
LoopResult, final Patch, QAResult, ReviewResult) by IDs and recomputed
content hashes, re-derives the Reviewer's judgment and QA's static checks
from the artifacts, and refuses anything stale, tampered, fabricated, or
unauthorized. An approval also requires passing evidence; nothing is ever
approved automatically and nothing can be overridden.

Approval authorizes. Approval does not apply. Approval does not execute.
Approval does not commit. Approval does not deploy.
"""

from siamang.approval.decision import ApprovalDecision, Decision
from siamang.approval.errors import ApprovalError, ApprovalErrorCode
from siamang.approval.policy import DEFAULT_POLICY, ApprovalPolicy
from siamang.approval.provenance import ApprovalProvenance
from siamang.approval.request import ApprovalRequest
from siamang.approval.result import ApprovalResult, ApprovalStatus
from siamang.approval.service import ApprovalService

__all__ = [
    "DEFAULT_POLICY",
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalErrorCode",
    "ApprovalPolicy",
    "ApprovalProvenance",
    "ApprovalRequest",
    "ApprovalResult",
    "ApprovalService",
    "ApprovalStatus",
    "Decision",
]
