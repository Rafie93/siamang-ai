"""Typed event envelope + validation (contract v0.1, §3).

Wire field names follow the Phase 2 contract exactly:
``contract_version``, ``type``, ``event_id``, ``request_id``, ``task_id``,
``seq``, ``timestamp``, ``producer``, ``payload``.

Every :class:`Envelope` instance is valid by construction: both
``Envelope(...)`` and ``Envelope.from_dict(...)`` run the validator.
Unknown envelope/payload fields are ignored, never rejected.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from siamang.contracts.events import INBOUND, PAYLOADS, EventType
from siamang.contracts.schema import F, SchemaViolation, check_object, is_utc_timestamp
from siamang.contracts.version import CONTRACT_VERSION, is_supported, parse_version
from siamang.ids import new_id

# Validation error codes (internal; distinct from task.failed error.code).
INVALID_ENVELOPE = "INVALID_ENVELOPE"
INVALID_PAYLOAD = "INVALID_PAYLOAD"
UNSUPPORTED_CONTRACT_VERSION = "UNSUPPORTED_CONTRACT_VERSION"
UNKNOWN_EVENT_TYPE = "UNKNOWN_EVENT_TYPE"

ENVELOPE_FIELDS = (
    "contract_version",
    "type",
    "event_id",
    "request_id",
    "task_id",
    "seq",
    "timestamp",
    "producer",
    "payload",
)


class ContractError(ValueError):
    """A message violates the contract. ``code`` is deterministic for a given input."""

    def __init__(self, code: str, violations: list[SchemaViolation]):
        self.code = code
        self.violations = violations
        detail = "; ".join(str(v) for v in violations)
        super().__init__(f"{code}: {detail}")

    @property
    def ignorable(self) -> bool:
        """Contract §8: consumers must ignore unknown event types (not crash)."""
        return self.code == UNKNOWN_EVENT_TYPE


def validate_message(data: Any) -> None:
    """Validate a decoded JSON message. Raises :class:`ContractError`."""
    if not isinstance(data, dict):
        raise ContractError(INVALID_ENVELOPE, [SchemaViolation("$", "message must be a JSON object")])

    # 1. Version first: a different MAJOR may have a different shape entirely.
    version = data.get("contract_version")
    try:
        parse_version(version)
    except ValueError as exc:
        raise ContractError(INVALID_ENVELOPE, [SchemaViolation("contract_version", str(exc))]) from None
    if not is_supported(version):
        raise ContractError(
            UNSUPPORTED_CONTRACT_VERSION,
            [SchemaViolation("contract_version", f"major version of {version!r} is not supported")],
        )

    # 2. Event type.
    etype = data.get("type")
    if not isinstance(etype, str) or not etype:
        raise ContractError(INVALID_ENVELOPE, [SchemaViolation("type", "required string field")])
    try:
        etype = EventType(etype)
    except ValueError:
        raise ContractError(UNKNOWN_EVENT_TYPE, [SchemaViolation("type", f"unknown event type {etype!r}")]) from None

    # 3. Envelope fields.
    inbound = etype in INBOUND
    spec = {
        "event_id": F("id", required=True),
        "request_id": F("id", required=True),
        "task_id": F("id", required=etype is not EventType.TASK_REQUEST, nullable=etype is EventType.TASK_REQUEST),
        "seq": F("int", required=not inbound, nullable=inbound, min=1),
        "timestamp": F("timestamp", required=True),
        "producer": F("str", required=True),
        "payload": F("object", required=True),
    }
    errors = check_object(data, spec, path="$")
    if errors:
        raise ContractError(INVALID_ENVELOPE, [SchemaViolation(e.path.removeprefix("$."), e.message) for e in errors])

    # 4. Payload.
    errors = check_object(data["payload"], PAYLOADS[etype])
    if errors:
        raise ContractError(INVALID_PAYLOAD, errors)


def parse_timestamp(value: str) -> datetime:
    if not is_utc_timestamp(value):
        raise ValueError(f"not an ISO-8601 UTC timestamp: {value!r}")
    return datetime.fromisoformat(value).astimezone(UTC)


def format_timestamp(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Envelope:
    contract_version: str
    type: EventType
    event_id: str
    request_id: str
    timestamp: datetime
    producer: str
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    seq: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.type, str) and not isinstance(self.type, EventType):
            try:
                object.__setattr__(self, "type", EventType(self.type))
            except ValueError:
                pass  # reported by validate_message below
        if isinstance(self.timestamp, str):
            try:
                object.__setattr__(self, "timestamp", parse_timestamp(self.timestamp))
            except ValueError as exc:
                raise ContractError(INVALID_ENVELOPE, [SchemaViolation("timestamp", str(exc))]) from None
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ContractError(INVALID_ENVELOPE, [SchemaViolation("timestamp", "must be a timezone-aware datetime")])
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        validate_message(self.to_dict())

    @classmethod
    def from_dict(cls, data: Any) -> Envelope:
        validate_message(data)
        return cls(
            contract_version=data["contract_version"],
            type=EventType(data["type"]),
            event_id=data["event_id"],
            request_id=data["request_id"],
            task_id=data.get("task_id"),
            seq=data.get("seq"),
            timestamp=parse_timestamp(data["timestamp"]),
            producer=data["producer"],
            payload=data["payload"],
        )

    @classmethod
    def from_json(cls, text: str) -> Envelope:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(INVALID_ENVELOPE, [SchemaViolation("$", f"invalid JSON: {exc}")]) from None
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict. Absent optional fields (``task_id``/``seq``) are omitted."""
        out: dict[str, Any] = {
            "contract_version": self.contract_version,
            "type": str(self.type),
            "event_id": self.event_id,
            "request_id": self.request_id,
        }
        if self.task_id is not None:
            out["task_id"] = self.task_id
        if self.seq is not None:
            out["seq"] = self.seq
        out["timestamp"] = format_timestamp(self.timestamp) if isinstance(self.timestamp, datetime) else self.timestamp
        out["producer"] = self.producer
        out["payload"] = copy.deepcopy(self.payload)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False)


def new_event(
    type: EventType | str,
    payload: dict[str, Any],
    *,
    request_id: str,
    task_id: str | None = None,
    seq: int | None = None,
    producer: str = "siamang",
    timestamp: datetime | None = None,
    event_id: str | None = None,
    contract_version: str = CONTRACT_VERSION,
) -> Envelope:
    """Convenience constructor that fills ``event_id``/``timestamp``."""
    return Envelope(
        contract_version=contract_version,
        type=type,  # type: ignore[arg-type]
        event_id=event_id or new_id("event"),
        request_id=request_id,
        task_id=task_id,
        seq=seq,
        timestamp=timestamp or utc_now(),
        producer=producer,
        payload=payload,
    )
