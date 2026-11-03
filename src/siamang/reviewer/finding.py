"""ReviewFinding: one review judgment. Immutable, bounded, and safe to show.

* ``code``: a stable dotted slug (``qa.failed``, ``risk.deletion``, ...).
* ``severity``: ``error`` (blocking review concern: the review fails),
  ``warning`` (a concern), or ``info``.
* ``category``: verification, alignment, scope, maintainability,
  consistency, risk, security, or feasibility.
* ``inconclusive``: a *warning* saying the evidence is not enough for a safe
  judgment (the review cannot pass or merely raise a concern). Only warnings
  may be inconclusive.
* ``path``: a safe project-relative path, or ``None``. A path that is itself
  unsafe (absolute, traversal, control characters, ...) is never echoed.
* ``message``: fixed template text. Messages with credential material or
  machine paths are refused; secrets never appear in findings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.log import redact
from siamang.planner.request import json_object, text_field
from siamang.programmer.safety import deep_freeze, find_secret, thaw
from siamang.qa.finding import safe_path
from siamang.reviewer.errors import ReviewResultError

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_]{1,40}\Z")
MESSAGE_MAX = 300
METADATA_MAX = 1024
_MACHINE_RE = re.compile(r"(?:^|[\s(\"'])(?:/|~/|[A-Za-z]:[\\/]|\\\\)")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Category(StrEnum):
    VERIFICATION = "verification"  # what the QAResult says, taken as evidence
    ALIGNMENT = "alignment"
    SCOPE = "scope"
    MAINTAINABILITY = "maintainability"
    CONSISTENCY = "consistency"
    RISK = "risk"
    SECURITY = "security"
    FEASIBILITY = "feasibility"


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    severity: Severity
    category: Category
    message: str
    path: str | None = None
    inconclusive: bool = False
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not CODE_RE.fullmatch(self.code):
            raise ReviewResultError("finding code must look like area.name", details=("code",))
        try:
            object.__setattr__(self, "severity", Severity(self.severity))
            object.__setattr__(self, "category", Category(self.category))
        except ValueError:
            raise ReviewResultError("unknown finding severity or category", details=(self.code,)) from None
        message = text_field(self.message, "message", MESSAGE_MAX, error=ReviewResultError)
        if find_secret(message) or _MACHINE_RE.search(message):
            raise ReviewResultError("finding message contains credential material or a machine path",
                                    details=(self.code,))
        object.__setattr__(self, "message", redact(message))
        if self.path is not None and safe_path(self.path) is None:
            raise ReviewResultError("finding path must be a safe project-relative path", details=(self.code,))
        if not isinstance(self.inconclusive, bool):
            raise ReviewResultError("inconclusive must be a boolean", details=(self.code,))
        if self.inconclusive and self.severity is not Severity.WARNING:
            raise ReviewResultError("only warnings can be inconclusive", details=(self.code,))
        metadata = deep_freeze(json_object(thaw(self.metadata), "finding.metadata", error=ReviewResultError))
        if len(repr(thaw(metadata))) > METADATA_MAX:
            raise ReviewResultError(f"finding metadata exceeds {METADATA_MAX} characters", details=(self.code,))
        object.__setattr__(self, "metadata", metadata)

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": str(self.severity),
            "category": str(self.category),
            "message": self.message,
            "path": self.path,
            "inconclusive": self.inconclusive,
            "metadata": thaw(self.metadata),
        }
