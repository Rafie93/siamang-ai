"""Reviewer (Phase 17): independent, static review of a QA-verified proposed change; returns a ReviewResult.

The Reviewer takes the Task, Plan, SystemAnalysis, Phase 15 Patch, and the
Phase 16 QAResult as immutable data, refuses any artifact that does not
re-validate or whose identity or recorded hash disagrees, and makes a
deterministic engineering judgment over seven dimensions: verification (QA
as evidence), alignment, scope, maintainability, consistency, risk, and
feasibility. Status is ``pass``, ``concern``, ``fail``, or ``inconclusive``.

QA verifies; the Reviewer judges. QA ``pass`` is not review ``pass``, and
review ``pass`` is not approval. The Reviewer does not approve changes, does
not apply changes, does not repair changes, and does not execute code. It
reads no project file, uses no model, and calls no other service.
"""

from siamang.reviewer.errors import ReviewerError, ReviewerErrorCode
from siamang.reviewer.finding import Category, ReviewFinding, Severity
from siamang.reviewer.policy import DEFAULT_POLICY, ReviewerPolicy
from siamang.reviewer.request import ReviewRequest
from siamang.reviewer.result import ReviewResult, ReviewStatus
from siamang.reviewer.service import ReviewerService

__all__ = [
    "DEFAULT_POLICY",
    "Category",
    "ReviewFinding",
    "ReviewRequest",
    "ReviewResult",
    "ReviewStatus",
    "ReviewerError",
    "ReviewerErrorCode",
    "ReviewerPolicy",
    "ReviewerService",
    "Severity",
]
