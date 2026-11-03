"""LoopResult: the immutable, auditable outcome of one bounded correction run. The patch is still only *proposed*.

Two separate answers:

* ``status``: what the **last evidence** says, never better:

  - ``pass`` / ``concern`` / ``fail`` / ``inconclusive``: the final
    ReviewResult's status (the last fully evaluated round);
  - ``inconclusive`` when no round was ever evaluated (the Programmer
    produced no patch).

* ``stop_reason``: why the loop stopped (``success``, ``not_retryable``,
  ``inconclusive``, ``max_iterations``, ``call_budget``, ``no_progress``,
  ``programmer_failed``, ``programmer_waiting``).

Validated on construction: ``pass`` if and only if ``stop_reason`` is
``success`` and the final QA and review both passed; the final artifacts
are exactly those of the last evaluated round and belong together; rounds
are numbered 1..n without gaps, for one task and project; call counts match
the rounds. So a result can never turn a failure, an inconclusive round, or
an exhausted budget into a pass.

``final_patch`` is a :class:`~siamang.programmer.Patch`, a *proposal*: not
applied, approved, or committed. ``loop_id`` (``lop_<ULID>``) is identity;
:meth:`content` / :attr:`content_hash` exclude it and every other random ID.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import ANALYSIS_ID_RE
from siamang.loop.errors import LoopResultError
from siamang.loop.iteration import LoopIteration, Outcome
from siamang.loop.policy import ITERATIONS_LIMIT
from siamang.planner.plan import PLAN_ID_RE
from siamang.planner.request import json_object
from siamang.programmer.patch import Patch
from siamang.programmer.safety import deep_freeze, thaw
from siamang.qa.result import QAResult, QAStatus
from siamang.reviewer.result import ReviewResult, ReviewStatus
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

LOOP_ID_RE = re.compile(r"^lop_[A-Za-z0-9]{1,64}\Z")
NOT_DONE = ("patch_application", "approval", "commit", "deployment", "code_execution", "runtime_behavior")


class LoopStatus(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class StopReason(StrEnum):
    SUCCESS = "success"  # QA pass and review pass
    NOT_RETRYABLE = "not_retryable"  # the review status is not in the policy's retry_statuses
    INCONCLUSIVE = "inconclusive"  # the review was inconclusive and the policy does not retry that
    MAX_ITERATIONS = "max_iterations"
    CALL_BUDGET = "call_budget"  # a Programmer, QA, or Reviewer budget could not pay for another round
    NO_PROGRESS = "no_progress"  # the Programmer repeated an earlier patch (same content hash)
    PROGRAMMER_FAILED = "programmer_failed"
    PROGRAMMER_WAITING = "programmer_waiting"


@dataclass(frozen=True)
class LoopResult:
    loop_id: str
    task_id: str
    project_id: str
    plan_id: str
    analysis_id: str
    status: LoopStatus
    stop_reason: StopReason
    iterations: tuple[LoopIteration, ...]
    final_patch: Patch | None = None
    final_qa_result: QAResult | None = None
    final_review_result: ReviewResult | None = None
    calls: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    not_done: tuple[str, ...] = NOT_DONE
    policy: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.loop_id, str) or not LOOP_ID_RE.fullmatch(self.loop_id):
            raise LoopResultError("loop_id must look like lop_<id>", details=("loop_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise LoopResultError(f"{name} is not a valid identifier", details=(name,)) from None
        for name, value, pattern in (("plan_id", self.plan_id, PLAN_ID_RE),
                                     ("analysis_id", self.analysis_id, ANALYSIS_ID_RE)):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise LoopResultError(f"{name} is not a valid identifier", details=(name,))
        try:
            status, reason = LoopStatus(self.status), StopReason(self.stop_reason)
        except ValueError:
            raise LoopResultError("unknown loop status or stop reason", details=("status",)) from None
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stop_reason", reason)
        rounds = self.iterations
        if isinstance(rounds, (str, bytes)) or not isinstance(rounds, (list, tuple)) or not 1 <= len(rounds) <= (
                ITERATIONS_LIMIT) or not all(type(i) is LoopIteration for i in rounds):
            raise LoopResultError(f"iterations must be 1-{ITERATIONS_LIMIT} LoopIteration records",
                                  details=("iterations",))
        rounds = tuple(rounds)
        object.__setattr__(self, "iterations", rounds)
        if [i.number for i in rounds] != list(range(1, len(rounds) + 1)) or any(
                (i.task_id, i.project_id) != (self.task_id, self.project_id) for i in rounds):
            raise LoopResultError("iterations must be numbered 1..n for this task and project",
                                  details=("iterations",))
        if any(i.outcome is not Outcome.EVALUATED for i in rounds[:-1]):
            raise LoopResultError("only the last round may end without an evaluation", details=("iterations",))
        if any(i.feedback for i in rounds[-1:]):
            raise LoopResultError("the last round hands feedback to no one", details=("iterations",))
        self._check_final(rounds, status, reason)
        self._check_calls(rounds)
        if not isinstance(self.not_done, tuple) or not set(NOT_DONE) <= set(self.not_done) or len(self.not_done) > 20:
            raise LoopResultError("not_done cannot drop what the loop never does", details=("not_done",))
        object.__setattr__(self, "policy", deep_freeze(json.loads(json.dumps(thaw(self.policy)))))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "result.metadata",
                                                                     error=LoopResultError)))

    def _check_final(self, rounds: tuple[LoopIteration, ...], status: LoopStatus, reason: StopReason) -> None:
        evaluated = [i for i in rounds if i.outcome is Outcome.EVALUATED]
        patch, qa, review = self.final_patch, self.final_qa_result, self.final_review_result
        if not evaluated:
            if patch is not None or qa is not None or review is not None or status is not LoopStatus.INCONCLUSIVE:
                raise LoopResultError("without an evaluated round there is no final artifact and no verdict",
                                      details=("status",))
        else:
            last = evaluated[-1]
            if type(patch) is not Patch or type(qa) is not QAResult or type(review) is not ReviewResult:
                raise LoopResultError("the final patch, QA result, and review result are required",
                                      details=("final",))
            same = (patch.patch_id == last.patch_id == qa.patch_id == review.patch_id
                    and patch.content_hash == last.patch_hash == qa.patch_hash == review.patch_hash
                    and qa.qa_id == last.qa_id == review.qa_id and qa.content_hash == review.qa_hash
                    and review.review_id == last.review_id and qa.status is last.qa_status is review.qa_status
                    and review.status is last.review_status
                    and {patch.task_id, qa.task_id, review.task_id} == {self.task_id}
                    and {patch.project_id, qa.project_id, review.project_id} == {self.project_id})
            if not same:
                raise LoopResultError("the final artifacts must be the last evaluated round's, and belong together",
                                      details=("final",))
            if status.value != review.status.value:
                raise LoopResultError("status must be the final review's status", details=("status",))
        passed = status is LoopStatus.PASS
        if passed != (reason is StopReason.SUCCESS):
            raise LoopResultError("pass if and only if the loop stopped on success", details=("status",))
        if passed and (qa is None or qa.status is not QAStatus.PASS or review.status is not ReviewStatus.PASS
                       or rounds[-1].outcome is not Outcome.EVALUATED):
            raise LoopResultError("a pass needs a passing QA result and review in the last round",
                                  details=("status",))

    def _check_calls(self, rounds: tuple[LoopIteration, ...]) -> None:
        calls = self.calls
        if not isinstance(calls, Mapping) or set(calls) != {"programmer", "qa", "reviewer"} or not all(
                type(v) is int for v in calls.values()):
            raise LoopResultError("calls must count programmer, qa, and reviewer calls", details=("calls",))
        evaluated = sum(1 for i in rounds if i.outcome is Outcome.EVALUATED)
        if (calls["programmer"], calls["qa"], calls["reviewer"]) != (len(rounds), evaluated, evaluated):
            raise LoopResultError("call counts must match the recorded rounds", details=("calls",))
        object.__setattr__(self, "calls", MappingProxyType(dict(calls)))

    @property
    def passed(self) -> bool:
        return self.status is LoopStatus.PASS

    def content(self) -> dict[str, Any]:
        """Everything except ``loop_id`` and the random artifact IDs: the deterministic, comparable part."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "status": str(self.status),
            "stop_reason": str(self.stop_reason),
            "iterations": [i.content() for i in self.iterations],
            "final_patch_hash": None if self.final_patch is None else self.final_patch.content_hash,
            "final_qa_hash": None if self.final_qa_result is None else self.final_qa_result.content_hash,
            "final_review_hash": None if self.final_review_result is None else self.final_review_result.content_hash,
            "calls": dict(self.calls),
            "not_done": list(self.not_done),
            "policy": thaw(self.policy),
            "metadata": thaw(self.metadata),
        }

    @property
    def content_hash(self) -> str:
        text = json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Auditable summary: identities, hashes, statuses, feedback, and why the loop stopped. No file contents."""
        return {
            "loop_id": self.loop_id,
            "plan_id": self.plan_id,
            "analysis_id": self.analysis_id,
            **self.content(),
            "iterations": [i.to_dict() for i in self.iterations],
            "final_patch_id": None if self.final_patch is None else self.final_patch.patch_id,
            "final_qa_id": None if self.final_qa_result is None else self.final_qa_result.qa_id,
            "final_review_id": None if self.final_review_result is None else self.final_review_result.review_id,
            "content_hash": self.content_hash,
        }
