"""LoopService: a bounded coordinator for Programmer -> QA -> Reviewer correction rounds. Proposes; never applies.

::

    LoopRequest (task IDs, instruction, Plan + SystemAnalysis as data)
          │  run(request)
          ▼
    LoopService ──► TaskEngine.get (read only)
          │
          │   round n (n <= max_iterations, and only if every call budget can pay for a whole round)
          │     PROGRAM ──► Programmer.program(ProgrammingRequest + feedback of round n-1 as typed input data)
          │        ├── no patch (failed / waiting) ─────────────────────────► STOP programmer_failed / _waiting
          │        ├── patch content hash seen before ──────────────────────► STOP no_progress
          │     QA ─────► QAService.verify(patch, its own content hash)
          │     REVIEW ─► ReviewerService.review(patch, QA result, their own content hashes)
          │        ├── pass ────────────────────────────────────────────────► STOP success
          │        ├── status not retryable (policy) ───────────────────────► STOP not_retryable / inconclusive
          │        ├── n == max_iterations ─────────────────────────────────► STOP max_iterations
          │        └── otherwise: bounded feedback(n) ──► PROGRAM (round n+1)
          ▼
    LoopResult (status = last evidence; stop_reason; rounds; final proposed artifacts)
          │
          X  nothing is applied, approved, committed, executed, persisted, or emitted

Every artifact a component returns is checked before it is used: exact
types, re-validation, the same task and project, the same patch (by ID and
content hash) for QA and Reviewer, the same QA result (by ID and content
hash) for the Reviewer, and no identity reused from an earlier round. A
broken chain or a component refusal raises a :class:`LoopError` and the run
stops; an unexpected component exception raises ``loop_internal`` with the
exception type only. Nothing is retried outside the round budget.

All run state (rounds, seen hashes and IDs, call counts, pending feedback)
lives in local variables of one ``run`` call; the service itself holds only
the injected components and the policy, so runs never affect each other.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from siamang.ids import new_id
from siamang.log import get_logger
from siamang.loop.errors import (
    LoopChainError,
    LoopComponentError,
    LoopError,
    LoopInternalError,
    LoopRequestError,
    LoopTaskNotFoundError,
)
from siamang.loop.feedback import FEEDBACK_KEY, as_input, collect
from siamang.loop.iteration import LoopIteration, Outcome
from siamang.loop.policy import DEFAULT_POLICY, LoopPolicy
from siamang.loop.request import LoopRequest
from siamang.loop.result import LoopResult, LoopStatus, StopReason
from siamang.programmer.errors import ProgrammingError
from siamang.programmer.patch import PATCH_ID_RE, Patch
from siamang.programmer.request import ProgrammingRequest
from siamang.programmer.result import ProgrammingResult, ProgrammingStatus
from siamang.programmer.safety import thaw
from siamang.programmer.service import Programmer
from siamang.qa.checks import deep_frozen
from siamang.qa.errors import QAError
from siamang.qa.request import QARequest
from siamang.qa.result import QAResult, QAStatus
from siamang.qa.service import QAService
from siamang.reviewer.errors import ReviewerError
from siamang.reviewer.request import ReviewRequest
from siamang.reviewer.result import ReviewResult, ReviewStatus
from siamang.reviewer.service import ReviewerService
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError

log = get_logger("loop")
STRATEGY = "loop.bounded"
VERSION = "1"
COMPONENT_ERRORS = (ProgrammingError, QAError, ReviewerError)


def _validated_form(result: QAResult | ReviewResult) -> bool:
    """Still exactly what its own validation produced: enum status, tuple of findings with enum fields.

    A value forced past validation (``object.__setattr__``) compares equal to its validated form, so equality
    alone cannot tell them apart; the types can.
    """
    status = QAStatus if type(result) is QAResult else ReviewStatus
    return (type(result.status) is status and type(result.findings) is tuple
            and all(type(f.severity) is not str and type(f.category) is not str for f in result.findings))


class LoopService:
    def __init__(self, tasks: TaskEngine, programmer: Programmer, qa: QAService, reviewer: ReviewerService, *,
                 policy: LoopPolicy = DEFAULT_POLICY):
        for name, value, cls in (("tasks", tasks, TaskEngine), ("programmer", programmer, Programmer),
                                 ("qa", qa, QAService), ("reviewer", reviewer, ReviewerService)):
            if not isinstance(value, cls):
                raise TypeError(f"{name} must be the {cls.__name__}")
        if not isinstance(policy, LoopPolicy):
            raise TypeError("policy must be a LoopPolicy")
        self._tasks = tasks
        self._programmer = programmer
        self._qa = qa
        self._reviewer = reviewer
        self.policy = policy

    def run(self, request: LoopRequest) -> LoopResult:
        if type(request) is not LoopRequest:
            raise LoopRequestError("request must be a LoopRequest")
        try:
            result = self._run(request)
        except LoopError as exc:
            log.info("loop task=%s project=%s: stopped (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("loop task=%s project=%s loop=%s status=%s stop=%s rounds=%d", result.task_id, result.project_id,
                 result.loop_id, result.status, result.stop_reason, len(result.iterations))
        return result

    # -- the state machine -----------------------------------------------------------------

    def _run(self, request: LoopRequest) -> LoopResult:
        self._check_request(request)
        policy = self.policy
        rounds: list[LoopIteration] = []
        calls = {"programmer": 0, "qa": 0, "reviewer": 0}
        seen_hashes: set[str] = set()
        seen_ids: set[str] = set()
        final: tuple[Patch, QAResult, ReviewResult] | None = None
        pending: dict[str, Any] | None = None  # set only by a round that continues; read only by the next round
        reason = StopReason.MAX_ITERATIONS
        for number in range(1, policy.max_iterations + 1):
            if (calls["programmer"] >= policy.max_programmer_calls or calls["qa"] >= policy.max_qa_calls
                    or calls["reviewer"] >= policy.max_reviewer_calls):
                reason = StopReason.CALL_BUDGET
                break
            # PROGRAM
            programming = self._programming_request(request, pending, number)  # round n-1's feedback, or none
            calls["programmer"] += 1
            produced = self._call("programmer", number, lambda: self._programmer.program(programming))
            self._check_programming(produced, request, seen_ids, number)
            base = dict(number=number, task_id=request.task_id, project_id=request.project_id,
                        programming_id=produced.programming_id, programming_status=produced.status)
            if produced.status is not ProgrammingStatus.COMPLETED:
                failed = produced.status is ProgrammingStatus.FAILED
                rounds.append(LoopIteration(**base, outcome=Outcome.PROGRAMMER_FAILED if failed
                                            else Outcome.PROGRAMMER_WAITING))
                reason = StopReason.PROGRAMMER_FAILED if failed else StopReason.PROGRAMMER_WAITING
                break
            patch = produced.patch
            patch_hash = self._patch_hash(patch, number)
            if patch_hash in seen_hashes:  # the same content again: nothing new to verify or review
                rounds.append(LoopIteration(**base, outcome=Outcome.NO_PROGRESS, patch_id=patch.patch_id,
                                            patch_hash=patch_hash))
                reason = StopReason.NO_PROGRESS
                break
            seen_hashes.add(patch_hash)
            # QA
            calls["qa"] += 1
            qa = self._call("qa", number, lambda: self._qa.verify(QARequest(
                request.task_id, request.project_id, request.plan, request.analysis, patch, patch_hash)))
            qa_hash = self._check_qa(qa, request, patch, patch_hash, seen_ids, number)
            # REVIEW
            calls["reviewer"] += 1
            review = self._call("reviewer", number, lambda: self._reviewer.review(ReviewRequest(
                request.task_id, request.project_id, request.plan, request.analysis, patch, patch_hash, qa, qa_hash)))
            self._check_review(review, request, patch, patch_hash, qa, qa_hash, seen_ids, number)
            final = (patch, qa, review)
            # DECIDE
            if review.status is ReviewStatus.PASS:
                reason, go_on = StopReason.SUCCESS, False
            elif review.status not in policy.retry_statuses:
                reason, go_on = (StopReason.INCONCLUSIVE if review.status is ReviewStatus.INCONCLUSIVE
                                 else StopReason.NOT_RETRYABLE), False
            elif number >= policy.max_iterations:
                reason, go_on = StopReason.MAX_ITERATIONS, False
            elif (calls["programmer"] >= policy.max_programmer_calls or calls["qa"] >= policy.max_qa_calls
                  or calls["reviewer"] >= policy.max_reviewer_calls):
                reason, go_on = StopReason.CALL_BUDGET, False
            else:
                go_on = True
            feedback, truncated = collect(qa, review, number, policy) if go_on else ((), False)
            rounds.append(LoopIteration(**base, outcome=Outcome.EVALUATED, patch_id=patch.patch_id,
                                        patch_hash=patch_hash, qa_id=qa.qa_id, qa_status=qa.status,
                                        review_id=review.review_id, review_status=review.status,
                                        feedback=feedback, feedback_truncated=truncated))
            if not go_on:
                break
            pending = as_input(feedback, truncated, number, patch_hash, str(review.status))
        return self._result(request, rounds, calls, final, reason)

    def _result(self, request: LoopRequest, rounds: list[LoopIteration], calls: dict[str, int],
                final: tuple[Patch, QAResult, ReviewResult] | None, reason: StopReason) -> LoopResult:
        patch, qa, review = final if final is not None else (None, None, None)
        status = LoopStatus.INCONCLUSIVE if review is None else LoopStatus(review.status.value)
        return LoopResult(
            loop_id=new_id("loop"), task_id=request.task_id, project_id=request.project_id,
            plan_id=request.plan.plan_id, analysis_id=request.analysis.analysis_id, status=status,
            stop_reason=reason, iterations=tuple(rounds), final_patch=patch, final_qa_result=qa,
            final_review_result=review, calls=calls, policy=self.policy.to_dict(),
            metadata={"strategy": STRATEGY, "strategy_version": VERSION, "plan_content_hash": request.plan.content_hash,
                      "analysis_content_hash": request.analysis.content_hash})

    # -- input -----------------------------------------------------------------------------

    def _check_request(self, request: LoopRequest) -> None:
        failure: LoopError | None = None
        try:
            task = self._tasks.get(request.task_id)
        except TaskNotFoundError:
            failure = LoopTaskNotFoundError("unknown task")
        except TaskError:
            failure = LoopRequestError("task_id is not valid", details=("task_id",))
        if failure is not None:
            raise failure
        if task.project_id != request.project_id:
            raise LoopRequestError("task belongs to a different project", details=("project_id",))
        for name, artifact in (("plan", request.plan), ("analysis", request.analysis)):
            if (artifact.task_id, artifact.project_id) != (request.task_id, request.project_id):
                raise LoopRequestError(f"{name} is for a different task or project", details=(name,))

    @staticmethod
    def _programming_request(request: LoopRequest, pending: dict[str, Any] | None, number: int) -> ProgrammingRequest:
        data = thaw(request.input)
        if pending is not None:
            data[FEEDBACK_KEY] = pending
        failure: LoopError | None = None
        try:
            return ProgrammingRequest(request.task_id, request.project_id, request.instruction, request.plan,
                                      request.analysis, input=data, constraints=request.constraints,
                                      metadata=request.metadata)
        except ProgrammingError as exc:
            failure = LoopComponentError("the programming request was refused", cause=str(exc.code),
                                         details=("programmer", f"iteration_{number}"))
        raise failure

    # -- component calls and the artifact chain ----------------------------------------------------

    @staticmethod
    def _call(component: str, number: int, call: Callable[[], Any]) -> Any:
        failure: LoopError | None = None
        try:
            return call()
        except COMPONENT_ERRORS as exc:
            failure = LoopComponentError(f"{component} refused its input", cause=str(exc.code),
                                         details=(component, f"iteration_{number}"))
        except Exception as exc:  # never leak a traceback, path, model output, or secret
            failure = LoopInternalError(f"{component} failed unexpectedly", cause=type(exc).__name__,
                                        details=(component, f"iteration_{number}"))
        raise failure

    @staticmethod
    def _broken(what: str, number: int) -> LoopChainError:
        return LoopChainError(f"{what} does not belong to this round's chain", details=(f"iteration_{number}",))

    def _check_programming(self, produced: Any, request: LoopRequest, seen: set[str], number: int) -> None:
        if type(produced) is not ProgrammingResult:
            raise self._broken("the programming result", number)
        try:  # every ProgrammingResult invariant runs again
            again = dataclasses.replace(produced)
        except Exception:
            raise self._broken("the programming result", number) from None
        if ((again.task_id, again.project_id) != (request.task_id, request.project_id)
                or produced.programming_id in seen):
            raise self._broken("the programming result", number)
        seen.add(produced.programming_id)
        if produced.status is ProgrammingStatus.COMPLETED:
            patch = produced.patch
            if (type(patch) is not Patch or not isinstance(patch.patch_id, str)
                    or not PATCH_ID_RE.fullmatch(patch.patch_id) or patch.patch_id in seen
                    or (patch.task_id, patch.project_id) != (request.task_id, request.project_id)):
                raise self._broken("the proposed patch", number)
            seen.add(patch.patch_id)

    def _patch_hash(self, patch: Patch, number: int) -> str:
        try:
            return patch.content_hash
        except Exception:
            raise self._broken("the proposed patch", number) from None

    def _check_qa(self, qa: Any, request: LoopRequest, patch: Patch, patch_hash: str, seen: set[str],
                  number: int) -> str:
        if type(qa) is not QAResult:
            raise self._broken("the QA result", number)
        try:  # every QAResult invariant runs again (status derived from its findings), and nothing was forced in
            again = dataclasses.replace(qa, findings=tuple(dataclasses.replace(f) for f in qa.findings))
            intact = (again.content() == qa.content() and _validated_form(qa)
                      and deep_frozen(qa.policy) and deep_frozen(qa.snapshot)
                      and deep_frozen(qa.metadata) and all(deep_frozen(f.metadata) for f in qa.findings))
            qa_hash = qa.content_hash
        except Exception:
            raise self._broken("the QA result", number) from None
        meta = qa.metadata
        if not (intact and (qa.task_id, qa.project_id) == (request.task_id, request.project_id)
                and qa.patch_id == patch.patch_id and qa.patch_hash == patch_hash and qa.qa_id not in seen
                and meta.get("plan_content_hash") == request.plan.content_hash
                and meta.get("analysis_content_hash") == request.analysis.content_hash):
            raise self._broken("the QA result", number)
        seen.add(qa.qa_id)
        return qa_hash

    def _check_review(self, review: Any, request: LoopRequest, patch: Patch, patch_hash: str, qa: QAResult,
                      qa_hash: str, seen: set[str], number: int) -> None:
        if type(review) is not ReviewResult:
            raise self._broken("the review result", number)
        try:  # every ReviewResult invariant runs again (status vs findings and vs QA), nothing forced in
            again = dataclasses.replace(review, findings=tuple(dataclasses.replace(f) for f in review.findings))
            intact = (again.content() == review.content() and _validated_form(review)
                      and type(review.qa_status) is QAStatus and deep_frozen(review.policy)
                      and deep_frozen(review.metadata) and all(deep_frozen(f.metadata) for f in review.findings))
        except Exception:
            raise self._broken("the review result", number) from None
        if not (intact and (review.task_id, review.project_id) == (request.task_id, request.project_id)
                and review.plan_id == request.plan.plan_id and review.analysis_id == request.analysis.analysis_id
                and review.patch_id == patch.patch_id and review.patch_hash == patch_hash
                and review.qa_id == qa.qa_id and review.qa_hash == qa_hash and review.qa_status is qa.status
                and review.review_id not in seen):
            raise self._broken("the review result", number)
        seen.add(review.review_id)
