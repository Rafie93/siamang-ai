"""ApprovalService: the authorization boundary. Explicit decisions on verified proposals; never applies anything.

::

    ApprovalRequest (Task IDs + Plan + SystemAnalysis + LoopResult; provenance recorded at construction)
    ApprovalDecision (approve | reject, approver_id, reason)
          │  decide(request, decision)
          ▼
    ApprovalService ──► policy: approver authorized?                     (else approval_unauthorized)
          │         ──► TaskEngine.get / ProjectRegistry.get (read only) (task exists, open; project active)
          │         ──► provenance recomputed == recorded                (else stale/tampered: provenance_invalid)
          │         ──► Reviewer judgment and QA static checks re-derived (else fabricated: provenance_invalid)
          │         ──► approve only: QA pass, review pass, loop pass + success (else approval_not_eligible)
          ▼
    ApprovalResult (approved | rejected; provenance, evidence, policy version, sealed)
          │
          X  nothing is applied, executed, committed, merged, deployed, persisted, or emitted

``verify(result, request)`` re-runs every check and confirms the result was
issued by this service (seal), for exactly this chain, by an approver the
policy still authorizes. Any failure raises; nothing fails open.

There is no automatic approval: a passing loop only makes a proposal
*eligible*. There is no override, force, emergency, or skip path, and no
magic approver. The service holds only the task engine, the policy, and its
seal key; it keeps no record of past decisions.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from siamang.approval.decision import ApprovalDecision, Decision
from siamang.approval.errors import (
    ApprovalError,
    ApprovalInternalError,
    ApprovalNotEligibleError,
    ApprovalProjectArchivedError,
    ApprovalProjectNotFoundError,
    ApprovalProvenanceError,
    ApprovalRequestError,
    ApprovalResultError,
    ApprovalTaskNotFoundError,
    ApprovalUnauthorizedError,
)
from siamang.approval.policy import DEFAULT_POLICY, POLICY_VERSION, REQUIREMENTS, ApprovalPolicy
from siamang.approval.provenance import bind, rederive
from siamang.approval.request import ApprovalRequest
from siamang.approval.result import PASSING, ApprovalResult, ApprovalStatus
from siamang.ids import new_id
from siamang.log import get_logger
from siamang.projects import ProjectNotFoundError
from siamang.tasks.engine import TaskEngine
from siamang.tasks.errors import TaskError, TaskNotFoundError
from siamang.tasks.lifecycle import TaskStatus

log = get_logger("approval")
SEAL_KEY_MIN = 32
CLOSED = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


class ApprovalService:
    def __init__(self, tasks: TaskEngine, *, policy: ApprovalPolicy = DEFAULT_POLICY, seal_key: bytes | None = None):
        if not isinstance(tasks, TaskEngine):
            raise TypeError("tasks must be the TaskEngine")
        if not isinstance(policy, ApprovalPolicy):
            raise TypeError("policy must be an ApprovalPolicy")
        if seal_key is not None and (type(seal_key) is not bytes or len(seal_key) < SEAL_KEY_MIN):
            raise TypeError(f"seal_key must be at least {SEAL_KEY_MIN} bytes")
        self._tasks = tasks
        self._seal_key = seal_key if seal_key is not None else secrets.token_bytes(SEAL_KEY_MIN)
        self._policy = policy

    @property
    def policy(self) -> ApprovalPolicy:
        return self._policy

    # -- decide ---------------------------------------------------------------------------------

    def decide(self, request: ApprovalRequest, decision: ApprovalDecision) -> ApprovalResult:
        if type(request) is not ApprovalRequest or type(decision) is not ApprovalDecision:
            raise ApprovalRequestError("decide needs an ApprovalRequest and an explicit ApprovalDecision")
        try:
            result = self._decide(request, decision)
        except ApprovalError as exc:
            log.info("approval task=%s project=%s: refused (%s)", request.task_id, request.project_id, exc.code)
            raise
        log.info("approval task=%s project=%s approval=%s status=%s patch=%s", result.task_id, result.project_id,
                 result.approval_id, result.status, result.patch_id)
        return result

    def _decide(self, request: ApprovalRequest, decision: ApprovalDecision) -> ApprovalResult:
        self._authorize(decision.approver_id, decision.reason)
        approving = decision.decision is Decision.APPROVE
        evidence = self._verified_evidence(request, approving)
        status = ApprovalStatus.APPROVED if approving else ApprovalStatus.REJECTED
        failure: ApprovalError | None = None
        try:
            draft = ApprovalResult(approval_id=new_id("approval"), status=status,
                                   approver_id=decision.approver_id, reason=decision.reason,
                                   provenance=request.provenance, evidence=evidence, policy_version=POLICY_VERSION,
                                   requirements=REQUIREMENTS if approving else (), seal="hmac-sha256:" + "0" * 64)
            return ApprovalResult(**{**draft.__dict__, "seal": self._seal(draft)})
        except ApprovalError:
            raise
        except Exception as exc:  # never leak a traceback, path, or content
            failure = ApprovalInternalError("approval result could not be built", cause=type(exc).__name__)
        raise failure

    # -- verify ---------------------------------------------------------------------------------

    def verify(self, result: ApprovalResult, request: ApprovalRequest) -> None:
        """Raise unless ``result`` was issued here, for exactly ``request``'s chain, and still holds."""
        if type(result) is not ApprovalResult or type(request) is not ApprovalRequest:
            raise ApprovalResultError("verify needs an ApprovalResult and its ApprovalRequest")
        failure: ApprovalError | None = None
        try:
            again = ApprovalResult(**result.__dict__)  # every invariant runs again
        except ApprovalError as exc:
            failure = ApprovalResultError("the approval result does not validate", cause=str(exc.code))
        if failure is not None:
            raise failure
        if again.content() != result.content() or not hmac.compare_digest(self._seal(again), result.seal):
            raise ApprovalResultError("the approval result was not issued by this service or was altered")
        if result.provenance != request.provenance or result.policy_version != POLICY_VERSION:
            raise ApprovalResultError("the approval result is for a different chain or policy")
        self._authorize(result.approver_id, result.reason)
        evidence = self._verified_evidence(request, result.approved)
        if dict(evidence) != dict(result.evidence):
            raise ApprovalResultError("the recorded evidence no longer matches the chain")

    # -- checks (raise; fail closed) ----------------------------------------------------------------

    def _seal(self, result: ApprovalResult) -> str:
        message = f"{result.approval_id}\n{result.approval_hash}".encode()
        return "hmac-sha256:" + hmac.new(self._seal_key, message, hashlib.sha256).hexdigest()

    def _authorize(self, approver_id: str, reason: str | None) -> None:
        if not self.policy.authorizes(approver_id):
            raise ApprovalUnauthorizedError("this approver is not authorized by the approval policy",
                                            details=("approver_id",))
        if reason is not None and len(reason) > self.policy.max_reason_length:
            raise ApprovalRequestError(f"reason exceeds {self.policy.max_reason_length} characters",
                                       details=("reason",))

    def _verified_evidence(self, request: ApprovalRequest, approving: bool) -> dict[str, str]:
        self._open_task(request)
        failure: ApprovalError | None = None
        try:  # stale or altered since the request was made? recompute from the artifacts themselves
            fresh = bind(request.task_id, request.project_id, request.plan, request.analysis, request.loop_result)
            if fresh != request.provenance:
                failure = ApprovalProvenanceError("the evidence changed after the request was made (stale request)",
                                                  details=("provenance",))
            else:
                rederive(request.plan, request.analysis, request.loop_result)
        except ApprovalError as exc:
            failure = exc
        except Exception as exc:
            failure = ApprovalInternalError("evidence could not be verified", cause=type(exc).__name__)
        if failure is not None:
            raise failure
        loop = request.loop_result
        evidence = {"qa_status": str(loop.final_qa_result.status),
                    "review_status": str(loop.final_review_result.status),
                    "loop_status": str(loop.status), "loop_stop_reason": str(loop.stop_reason)}
        if approving and evidence != dict(PASSING):
            raise ApprovalNotEligibleError("the evidence does not qualify for approval (QA, review, and loop must "
                                           "pass and the loop must stop on success)", details=("evidence",))
        return evidence

    def _open_task(self, request: ApprovalRequest) -> None:
        failure: ApprovalError | None = None
        try:
            task = self._tasks.get(request.task_id)
        except TaskNotFoundError:
            failure = ApprovalTaskNotFoundError("unknown task")
        except TaskError:
            failure = ApprovalRequestError("task_id is not valid", details=("task_id",))
        if failure is not None:
            raise failure
        if task.project_id != request.project_id:
            raise ApprovalRequestError("task belongs to a different project", details=("project_id",))
        if task.status in CLOSED:
            raise ApprovalNotEligibleError(f"a {task.status} task cannot be decided on", details=("task_id",))
        try:
            project = self._tasks.projects.get(request.project_id)
        except ProjectNotFoundError:
            failure = ApprovalProjectNotFoundError("unknown project")
        if failure is not None:
            raise failure
        if not project.is_active:
            raise ApprovalProjectArchivedError("project is archived; restore it first")
