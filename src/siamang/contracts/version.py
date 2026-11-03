"""Contract versioning: ``MAJOR.MINOR``.

* MINOR = additive only (new optional fields, event types, error codes).
* MAJOR = breaking. Siamang rejects a MAJOR it does not support.
"""

from __future__ import annotations

import re

CONTRACT_VERSION = "0.2"  # 0.2 (Phase 10): additive task lifecycle events
SUPPORTED_MAJORS = frozenset({0})

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not _VERSION_RE.match(value):
        raise ValueError(f"contract version must be 'MAJOR.MINOR', got {value!r}")
    major, minor = value.split(".")
    return int(major), int(minor)


def is_supported(value: str) -> bool:
    """A newer MINOR of a supported MAJOR is accepted (unknown fields are ignored)."""
    major, _ = parse_version(value)
    return major in SUPPORTED_MAJORS
