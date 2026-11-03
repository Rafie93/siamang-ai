"""LoopIteration: the immutable record of one Programmer -> QA -> Reviewer round. A record, never session state.

It holds identities, content hashes, statuses, and the feedback that was
handed to the *next* round (empty when the loop stopped after this one).
No file contents, model output, or findings text other than the bounded,
re-checked feedback lines.

``outcome`` says how far the round got:

* ``evaluated``: a patch was proposed, verified by QA, and reviewed;
* ``no_progress``: the proposed patch's content hash repeats an earlier
  one, so it was neither verified nor reviewed again;
* ``programmer_failed`` / ``programmer_waiting``: the Programmer returned
  no patch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from siamang.loop.errors import LoopResultError
from siamang.loop.feedback import LoopFeedback
from siamang.loop.policy import ITERATIONS_LIMIT
from siamang.programmer.patch import PATCH_ID_RE
from siamang.programmer.result import PROGRAMMING_ID_RE, ProgrammingStatus
from siamang.qa.request import HASH_RE
from siamang.qa.result import QA_ID_RE, QAStatus
from siamang.reviewer.result import REVIEW_ID_RE, ReviewStatus
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError


class Outcome(StrEnum):
    EVALUATED = "evaluated"
    NO_PROGRESS = "no_progress"
    PROGRAMMER_FAILED = "programmer_failed"
    PROGRAMMER_WAITING = "programmer_waiting"


def _optional(value: Any, pattern: re.Pattern[str], name: str, present: bool) -> None:
    if present:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise LoopResultError(f"{name} is required here and must be a valid identifier", details=(name,))
    elif value is not None:
        raise LoopResultError(f"{name} must be empty for this outcome", details=(name,))


@dataclass(frozen=True)
class LoopIteration:
    number: int
    task_id: str
    project_id: str
    outcome: Outcome
    programming_id: str
    programming_status: ProgrammingStatus
    patch_id: str | None = None
    patch_hash: str | None = None
    qa_id: str | None = None
    qa_status: QAStatus | None = None
    review_id: str | None = None
    review_status: ReviewStatus | None = None
    feedback: tuple[LoopFeedback, ...] = ()
    feedback_truncated: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.number, bool) or not isinstance(self.number, int) or not 1 <= self.number <= ITERATIONS_LIMIT:
            raise LoopResultError(f"number must be in [1, {ITERATIONS_LIMIT}]", details=("number",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise LoopResultError(f"{name} is not a valid identifier", details=(name,)) from None
        try:
            outcome = Outcome(self.outcome)
            status = ProgrammingStatus(self.programming_status)
        except ValueError:
            raise LoopResultError("unknown outcome or programming status", details=("outcome",)) from None
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "programming_status", status)
        _optional(self.programming_id, PROGRAMMING_ID_RE, "programming_id", True)
        patched = outcome in (Outcome.EVALUATED, Outcome.NO_PROGRESS)
        evaluated = outcome is Outcome.EVALUATED
        expected = {Outcome.PROGRAMMER_FAILED: ProgrammingStatus.FAILED,
                    Outcome.PROGRAMMER_WAITING: ProgrammingStatus.WAITING}.get(outcome, ProgrammingStatus.COMPLETED)
        if status is not expected:
            raise LoopResultError("programming status does not match the outcome", details=("programming_status",))
        _optional(self.patch_id, PATCH_ID_RE, "patch_id", patched)
        _optional(self.patch_hash, HASH_RE, "patch_hash", patched)
        _optional(self.qa_id, QA_ID_RE, "qa_id", evaluated)
        _optional(self.review_id, REVIEW_ID_RE, "review_id", evaluated)
        try:
            object.__setattr__(self, "qa_status", None if self.qa_status is None else QAStatus(self.qa_status))
            object.__setattr__(self, "review_status",
                               None if self.review_status is None else ReviewStatus(self.review_status))
        except ValueError:
            raise LoopResultError("unknown QA or review status", details=("status",)) from None
        if (self.qa_status is None) == evaluated or (self.review_status is None) == evaluated:
            raise LoopResultError("QA and review statuses are recorded exactly for evaluated rounds",
                                  details=("status",))
        feedback = self.feedback
        if isinstance(feedback, (str, bytes)) or not isinstance(feedback, (list, tuple)) or not all(
                type(f) is LoopFeedback and f.iteration == self.number for f in feedback):
            raise LoopResultError("feedback must be this round's LoopFeedback lines", details=("feedback",))
        object.__setattr__(self, "feedback", tuple(feedback))
        if self.feedback and not evaluated:
            raise LoopResultError("only an evaluated round can produce feedback", details=("feedback",))
        if not isinstance(self.feedback_truncated, bool):
            raise LoopResultError("feedback_truncated must be a boolean", details=("feedback_truncated",))

    def content(self) -> dict[str, Any]:
        """Without the random identities (programming, patch, QA, and review IDs)."""
        return {
            "number": self.number,
            "outcome": str(self.outcome),
            "programming_status": str(self.programming_status),
            "patch_hash": self.patch_hash,
            "qa_status": None if self.qa_status is None else str(self.qa_status),
            "review_status": None if self.review_status is None else str(self.review_status),
            "feedback": [f.to_dict() for f in self.feedback],
            "feedback_truncated": self.feedback_truncated,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "project_id": self.project_id, "programming_id": self.programming_id,
                "patch_id": self.patch_id, "qa_id": self.qa_id, "review_id": self.review_id, **self.content()}
