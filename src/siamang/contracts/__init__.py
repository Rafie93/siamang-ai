"""Siamang contract as code."""

from siamang.contracts.envelope import (
    INVALID_ENVELOPE,
    INVALID_PAYLOAD,
    UNKNOWN_EVENT_TYPE,
    UNSUPPORTED_CONTRACT_VERSION,
    ContractError,
    Envelope,
    new_event,
    validate_message,
)
from siamang.contracts.events import INBOUND, OUTBOUND, PAYLOADS, EventType
from siamang.contracts.version import CONTRACT_VERSION, SUPPORTED_MAJORS, is_supported, parse_version

__all__ = [
    "CONTRACT_VERSION",
    "INBOUND",
    "INVALID_ENVELOPE",
    "INVALID_PAYLOAD",
    "OUTBOUND",
    "PAYLOADS",
    "SUPPORTED_MAJORS",
    "UNKNOWN_EVENT_TYPE",
    "UNSUPPORTED_CONTRACT_VERSION",
    "ContractError",
    "Envelope",
    "EventType",
    "is_supported",
    "new_event",
    "parse_version",
    "validate_message",
]
