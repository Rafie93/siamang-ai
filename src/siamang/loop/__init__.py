"""Loop (Phase 18): a bounded coordinator for Programmer -> QA -> Reviewer correction rounds.

The loop asks the Programmer for a proposal, has QA verify it and the
Reviewer judge it, and, when the review is not a pass and the policy
allows another round, hands bounded, re-checked feedback to the next
Programmer call. It stops on success, a non-retryable outcome, repeated
content (no progress), the round limit, or an exhausted call budget, and
returns an immutable LoopResult whose status is never better than the last
evidence.

Programmer proposes, QA verifies, Reviewer judges, the Loop coordinates
bounded iteration. The Loop does not apply patches, does not approve
patches, does not execute code, does not commit, does not deploy, and does
not run indefinitely. Its final patch is still only a proposal.
"""

from siamang.loop.errors import LoopError, LoopErrorCode
from siamang.loop.feedback import FeedbackSource, LoopFeedback
from siamang.loop.iteration import LoopIteration, Outcome
from siamang.loop.policy import DEFAULT_POLICY, LoopPolicy
from siamang.loop.request import LoopRequest
from siamang.loop.result import LoopResult, LoopStatus, StopReason
from siamang.loop.service import LoopService

__all__ = [
    "DEFAULT_POLICY",
    "FeedbackSource",
    "LoopError",
    "LoopErrorCode",
    "LoopFeedback",
    "LoopIteration",
    "LoopPolicy",
    "LoopRequest",
    "LoopResult",
    "LoopService",
    "LoopStatus",
    "Outcome",
    "StopReason",
]
