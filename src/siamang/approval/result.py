"""ApprovalResult: an immutable record that an authorized approval context decided on exactly one proposal.

It answers WHO (``approver_id``), WHAT and WHERE (``provenance``: task,
project, plan, analysis, loop, final round, patch ID + hash, QA ID + hash,
review ID + hash), ON WHAT EVIDENCE (``evidence``: QA, review, and loop
statuses), UNDER WHICH POLICY (``policy_version``), and WHICH DECISION
(``status``: ``approved`` or ``rejected``). It never answers "has it been
applied?": nothing is applied.

* ``approved`` requires passing evidence (QA pass, review pass, loop pass
  with stop reason ``success``) and lists every requirement checked;
  validated here, so a hand-built "approved" over failing evidence cannot
  exist. ``rejected`` is a human decision and always carries a reason.
* ``approval_hash`` is the ``sha256:`` of the canonical content (everything
  except ``approval_id`` and ``seal``). ``seal`` is an HMAC-SHA256 over the
  approval ID and that hash with a key only the issuing ApprovalService
  holds: it lets that service recognise results it issued and refuse forged
  or re-targeted ones. It is a process-local integrity seal, **not** a
  digital signature and **not** proof of a person's identity.
* No file contents, patch text, or secrets: IDs, hashes, statuses, and a
  checked reason only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.approval.decision import APPROVER_ID_RE, check_reason
from siamang.approval.errors import ApprovalResultError
from siamang.approval.policy import REQUIREMENTS
from siamang.approval.provenance import ApprovalProvenance

APPROVAL_ID_RE = re.compile(r"^apr_[A-Za-z0-9]{1,64}\Z")
SEAL_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}\Z")
PASSING = MappingProxyType({"qa_status": "pass", "review_status": "pass", "loop_status": "pass",
                            "loop_stop_reason": "success"})
_EVIDENCE = {"qa_status": ("pass", "fail", "inconclusive"),
             "review_status": ("pass", "concern", "fail", "inconclusive"),
             "loop_status": ("pass", "concern", "fail", "inconclusive"),
             "loop_stop_reason": ("success", "not_retryable", "inconclusive", "max_iterations", "call_budget",
                                  "no_progress", "programmer_failed", "programmer_waiting")}
NOT_DONE = ("patch_application", "code_execution", "commit", "merge", "deployment", "authentication")


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovalResult:
    approval_id: str
    status: ApprovalStatus
    approver_id: str
    reason: str | None
    provenance: ApprovalProvenance
    evidence: Mapping[str, str]
    policy_version: str
    requirements: tuple[str, ...]
    seal: str

    def __post_init__(self) -> None:
        if not isinstance(self.approval_id, str) or not APPROVAL_ID_RE.fullmatch(self.approval_id):
            raise ApprovalResultError("approval_id must look like apr_<id>", details=("approval_id",))
        try:
            status = ApprovalStatus(self.status)
        except ValueError:
            raise ApprovalResultError("status must be approved or rejected", details=("status",)) from None
        object.__setattr__(self, "status", status)
        if not isinstance(self.approver_id, str) or not APPROVER_ID_RE.fullmatch(self.approver_id):
            raise ApprovalResultError("approver_id must be a plain identifier", details=("approver_id",))
        try:
            object.__setattr__(self, "reason", check_reason(self.reason, status is ApprovalStatus.REJECTED))
        except ValueError:
            raise ApprovalResultError("reason is missing or unsafe", details=("reason",)) from None
        if type(self.provenance) is not ApprovalProvenance:
            raise ApprovalResultError("provenance must be an ApprovalProvenance", details=("provenance",))
        evidence = self.evidence
        if not isinstance(evidence, Mapping) or set(evidence) != set(_EVIDENCE) or any(
                evidence[k] not in allowed for k, allowed in _EVIDENCE.items()):
            raise ApprovalResultError("evidence must state the QA, review, and loop outcomes", details=("evidence",))
        evidence = MappingProxyType({k: str(evidence[k]) for k in sorted(_EVIDENCE)})
        object.__setattr__(self, "evidence", evidence)
        if not isinstance(self.policy_version, str) or not re.fullmatch(r"[0-9]{1,4}", self.policy_version):
            raise ApprovalResultError("policy_version must be a version number", details=("policy_version",))
        requirements = self.requirements
        if not isinstance(requirements, (list, tuple)) or not all(r in REQUIREMENTS for r in requirements):
            raise ApprovalResultError("requirements must name known requirements", details=("requirements",))
        object.__setattr__(self, "requirements", tuple(requirements))
        if status is ApprovalStatus.APPROVED and (dict(evidence) != dict(PASSING)
                                                  or self.requirements != REQUIREMENTS):
            raise ApprovalResultError("an approval needs passing evidence and every requirement",
                                      details=("status",))
        if not isinstance(self.seal, str) or not SEAL_RE.fullmatch(self.seal):
            raise ApprovalResultError("seal must look like hmac-sha256:<64 hex>", details=("seal",))

    # scope, for readers
    @property
    def task_id(self) -> str:
        return self.provenance.task_id

    @property
    def project_id(self) -> str:
        return self.provenance.project_id

    @property
    def patch_id(self) -> str:
        return self.provenance.patch_id

    @property
    def patch_hash(self) -> str:
        return self.provenance.patch_hash

    @property
    def qa_id(self) -> str:
        return self.provenance.qa_id

    @property
    def review_id(self) -> str:
        return self.provenance.review_id

    @property
    def loop_id(self) -> str:
        return self.provenance.loop_id

    @property
    def approved(self) -> bool:
        return self.status is ApprovalStatus.APPROVED

    def content(self) -> dict[str, Any]:
        """Everything except ``approval_id`` and ``seal``: deterministic for the same decision and chain."""
        return {
            "status": str(self.status),
            "approver_id": self.approver_id,
            "reason": self.reason,
            "provenance": self.provenance.to_dict(),
            "evidence": dict(self.evidence),
            "policy_version": self.policy_version,
            "requirements": list(self.requirements),
            "not_done": list(NOT_DONE),
        }

    @property
    def approval_hash(self) -> str:
        text = json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"approval_id": self.approval_id, **self.content(), "approval_hash": self.approval_hash,
                "seal": self.seal}
