"""Contract event types and payload schemas (v0.2).

Source of truth: docs/CONTRACT.md §4. v0.1 defined 12 event
types. v0.2 (Phase 10, additive MINOR) adds ``task.waiting``,
``task.requeued``, ``task.retry_requested`` and optional ``status`` /
``project`` / ``details`` fields on Siamang-produced task events.
"""

from __future__ import annotations

from enum import StrEnum

from siamang.contracts.schema import F


class EventType(StrEnum):
    TASK_REQUEST = "task.request"
    TASK_ACCEPTED = "task.accepted"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_WAITING_APPROVAL = "task.waiting_approval"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCEL = "task.cancel"
    TASK_CANCELLED = "task.cancelled"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    # v0.2
    TASK_WAITING = "task.waiting"
    TASK_REQUEUED = "task.requeued"
    TASK_RETRY_REQUESTED = "task.retry_requested"


# Direction (§4): inbound = client (CLI/API/...) -> Siamang; the rest are
# produced by Siamang and carry a per-task ``seq``.
INBOUND = frozenset(
    {
        EventType.TASK_REQUEST,
        EventType.TASK_CANCEL,
        EventType.APPROVAL_GRANTED,
        EventType.APPROVAL_REJECTED,
    }
)
OUTBOUND = frozenset(EventType) - INBOUND

SOURCES = ("voice", "text", "api", "schedule")
RISKS = ("low", "medium", "high")
OUTCOMES = ("success", "partial")
TASK_STATUSES = ("queued", "running", "waiting", "completed", "failed", "cancelled")

_SUMMARY = F("str", required=True)
# v0.2: optional on every Siamang-produced task event, so history can be rebuilt from events alone.
_STATUS = F("enum", choices=TASK_STATUSES)
_PROJECT = F("object", fields={"id": F("id"), "name": F("str")})
_APPROVAL_ID = F("id", required=True)

_APPROVAL_DECISION = {
    "approval_id": _APPROVAL_ID,
    "decided_by": F("str", required=True),
    "channel": F("str", required=True),
    "reason": F("text"),
    "utterance": F("text"),
}

PAYLOADS: dict[EventType, dict[str, F]] = {
    EventType.TASK_REQUEST: {
        "instruction": F("str", required=True),
        "source": F("enum", required=True, choices=SOURCES),
        "project": F("object", fields={"hint": F("str"), "id": F("id")}),
        "locale": F("str"),
        "requested_by": F("str"),
        "context": F(
            "object",
            fields={"utterance": F("text"), "conversation": F("list", item=F("text"))},
        ),
        "constraints": F(
            "object",
            fields={"approval_policy": F("str"), "deadline": F("timestamp")},
        ),
    },
    EventType.TASK_ACCEPTED: {
        "status": F("enum", required=True, choices=("queued",)),
        "summary": _SUMMARY,
        "project": F("object", fields={"id": F("id"), "name": F("str")}),
        "duplicate": F("bool"),
    },
    EventType.TASK_STARTED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "plan_outline": F("list", item=F("str"), max_items=7),
    },
    EventType.TASK_PROGRESS: {
        "summary": _SUMMARY,
        "percent": F("number", nullable=True, min=0, max=100),
        "step": F(
            "object",
            fields={
                "index": F("int", required=True, min=1),
                "total": F("int", required=True, min=1),
                "title": F("str", required=True),
            },
        ),
        "stage": F("str"),
    },
    EventType.TASK_WAITING_APPROVAL: {
        "approval_id": _APPROVAL_ID,
        "summary": _SUMMARY,
    },
    EventType.APPROVAL_REQUIRED: {
        "approval_id": _APPROVAL_ID,
        "action": F("str", required=True),
        "risk": F("enum", required=True, choices=RISKS),
        "question": F("str", required=True),
        "expires_at": F("timestamp", required=True),
        "details": F("object"),
        "allowed_channels": F("list", item=F("str")),
        "default_on_timeout": F("enum", choices=("reject",)),
    },
    EventType.APPROVAL_GRANTED: dict(_APPROVAL_DECISION),
    EventType.APPROVAL_REJECTED: dict(_APPROVAL_DECISION),
    EventType.TASK_COMPLETED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "details": F("object"),
        "outcome": F("enum", required=True, choices=OUTCOMES),
        "result": F(
            "object",
            fields={
                "changes": F(
                    "object",
                    fields={
                        "files_changed": F("int", min=0),
                        "commits": F("int", min=0),
                        "branch": F("str"),
                    },
                ),
                "tests": F("object", fields={"passed": F("int", min=0), "failed": F("int", min=0)}),
                "artifacts": F("list", item=F("str")),
                "next_steps": F("text"),
            },
        ),
        "duration_secs": F("number", min=0),
    },
    EventType.TASK_CANCEL: {
        "reason": F("text", required=True),
    },
    EventType.TASK_CANCELLED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "rolled_back": F("bool"),
    },
    EventType.TASK_FAILED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "error": F(
            "object",
            required=True,
            fields={
                "code": F("str", required=True),
                "retryable": F("bool", required=True),
                "message": F("text"),
                "step": F("any"),
                "details": F("object"),
            },
        ),
        "rolled_back": F("bool"),
    },
    # v0.2
    EventType.TASK_WAITING: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "reason": F("text"),
    },
    EventType.TASK_REQUEUED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
    },
    EventType.TASK_RETRY_REQUESTED: {
        "summary": _SUMMARY,
        "status": _STATUS,
        "project": _PROJECT,
        "retry_count": F("int", required=True, min=1),
        "max_retries": F("int", required=True, min=1),
    },
}
