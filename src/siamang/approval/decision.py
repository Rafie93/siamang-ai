"""ApprovalDecision: one explicit decision by one approver. Nothing is decided by default.

* ``decision`` is ``approve`` or ``reject``; there is no default, ``maybe``,
  ``auto``, ``force``, or override value.
* ``approver_id`` is a plain identifier (lower-case letters, digits,
  ``.``, ``_``, ``-``; at most 64 characters). It is the caller's statement
  of who decides. It is **not** authentication: whether that ID may decide
  is the service policy's call, and whether a real person stands behind it
  is outside Phase 19.
* ``reason`` is optional for ``approve`` and required for ``reject``. It is
  single-line text within :data:`REASON_LIMIT`; control characters,
  credential material, or machine paths are **refused**, never silently
  rewritten. A reason is a human note, not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from siamang.approval.errors import ApprovalRequestError
from siamang.planner.request import has_control
from siamang.programmer.safety import find_secret

APPROVER_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}\Z")
REASON_LIMIT = 600  # hard cap; the policy is usually stricter
_MACHINE_RE = re.compile(r"(?:^|[\s(\"'])(?:/|~/|[A-Za-z]:[\\/]|\\\\)")


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


def check_reason(reason: Any, required: bool) -> str | None:
    if reason is None:
        if required:
            raise ApprovalRequestError("a rejection needs a reason", details=("reason",))
        return None
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_LIMIT or has_control(reason)
            or find_secret(reason) or _MACHINE_RE.search(reason)):
        raise ApprovalRequestError(f"reason must be 1-{REASON_LIMIT} characters of plain, single-line text "
                                   "without credentials or machine paths", details=("reason",))
    return reason.strip()


@dataclass(frozen=True)
class ApprovalDecision:
    decision: Decision
    approver_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        try:
            decision = Decision(self.decision)
        except ValueError:
            raise ApprovalRequestError("decision must be approve or reject", details=("decision",)) from None
        object.__setattr__(self, "decision", decision)
        if not isinstance(self.approver_id, str) or not APPROVER_ID_RE.fullmatch(self.approver_id):
            raise ApprovalRequestError("approver_id must be a plain identifier", details=("approver_id",))
        object.__setattr__(self, "reason", check_reason(self.reason, decision is Decision.REJECT))

    def to_dict(self) -> dict[str, Any]:
        return {"decision": str(self.decision), "approver_id": self.approver_id, "reason": self.reason}
