"""QA (Phase 16): verifies a Phase 15 proposed patch and returns an immutable QAResult.

Static and read-only: deterministic checks of patch integrity (recorded hash,
Phase 15 re-validation, conflicts), security (paths, forbidden files,
secrets, symlinks), policy limits, plan/analysis/task consistency, project
consistency (recorded before-state vs a read-only snapshot through the
Phase 14 ``ProjectInspector``), and scope. Status is ``pass``, ``fail``, or
``inconclusive``; ``pass`` is not proof of correctness and not approval.

QA does not modify the project, execute project code, use a model, invoke
the Programmer, retry, review the result, or approve the change.
"""

from siamang.qa.errors import QAError, QAErrorCode
from siamang.qa.finding import Category, QAFinding, Severity
from siamang.qa.policy import DEFAULT_POLICY, QAPolicy
from siamang.qa.request import QARequest
from siamang.qa.result import QAResult, QAStatus
from siamang.qa.service import QAService

__all__ = [
    "DEFAULT_POLICY",
    "Category",
    "QAError",
    "QAErrorCode",
    "QAFinding",
    "QAPolicy",
    "QARequest",
    "QAResult",
    "QAService",
    "QAStatus",
    "Severity",
]
