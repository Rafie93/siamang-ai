"""ApprovalPolicy: who may decide, and the fixed evidence an approval requires. Given to the service, never a request.

* ``authorized_approvers``: an explicit, finite set of approver IDs. Empty
  means **nobody** (fail closed): there is no wildcard, no "everyone", and
  no built-in or magic actor (``admin``/``root`` are ordinary IDs that are
  authorized only when listed).
* ``max_reason_length``: bound on a decision's reason (hard cap 600).
* The evidence requirements are fixed for this policy version and cannot be
  switched off: there is no override, force, emergency, or skip setting.

No environment, config, CLI, or request input can change a policy, and a
policy cannot change after construction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.approval.decision import APPROVER_ID_RE, REASON_LIMIT

POLICY_VERSION = "1"
APPROVERS_LIMIT = 50
# What an approval requires, always (a reject needs a consistent chain, not passing evidence).
REQUIREMENTS = ("chain_verified", "patch_hash_verified", "review_rederived", "qa_static_checks_rederived",
                "final_patch_present", "qa_pass", "review_pass", "loop_pass", "loop_success", "approver_authorized")


@dataclass(frozen=True)
class ApprovalPolicy:
    authorized_approvers: frozenset[str] = frozenset()
    max_reason_length: int = 300

    def __post_init__(self) -> None:
        approvers = self.authorized_approvers
        if isinstance(approvers, (str, bytes)) or not isinstance(approvers, Iterable):
            raise ValueError("authorized_approvers must be a collection of approver IDs")
        approvers = tuple(approvers)
        if len(approvers) > APPROVERS_LIMIT:
            raise ValueError(f"at most {APPROVERS_LIMIT} authorized approvers")
        if not all(isinstance(a, str) and APPROVER_ID_RE.fullmatch(a) for a in approvers):
            raise ValueError("authorized approvers must be plain approver IDs (no wildcard)")
        object.__setattr__(self, "authorized_approvers", frozenset(approvers))
        value = self.max_reason_length
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= REASON_LIMIT:
            raise ValueError(f"max_reason_length must be an integer in [1, {REASON_LIMIT}]")

    def authorizes(self, approver_id: str) -> bool:
        return approver_id in self.authorized_approvers

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "authorized_approvers": sorted(self.authorized_approvers),
            "max_reason_length": self.max_reason_length,
            "requirements": list(REQUIREMENTS),
            "reject_requires_reason": True,
            "overrides": False,
            "applies_patches": False,
            "executes_code": False,
            "writes": False,
            "persists": False,
            "expires": False,
        }


DEFAULT_POLICY = ApprovalPolicy()  # nobody is authorized until a policy names approvers
