"""Finding: one analysis observation, with explicit confidence and safe evidence.

``confidence`` keeps facts and guesses apart:

* ``observed``: directly seen in the project snapshot ("pyproject.toml exists").
  Needs at least one piece of evidence.
* ``inferred``: concluded from observations ("project appears to use Python").
  Needs the evidence it rests on.
* ``unknown``: something the analysis could not establish. Evidence optional.

Evidence lines are short text: relative paths and extracted structured facts.
Never file contents, absolute machine paths, or secret values: evidence is
redacted, and lines that start with ``/`` or ``~`` or contain ``..`` are
refused.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.analyst.errors import InvalidAnalysisError
from siamang.log import redact
from siamang.planner.request import json_object, text_field

FINDING_ID_RE = re.compile(r"^finding_[0-9]{2,3}\Z")
TITLE_MAX = 120
DESCRIPTION_MAX = 1000
EVIDENCE_MAX = 300
EVIDENCE_LINES_MAX = 20
_PARENT_RE = re.compile(r"(?:^|[/\s])\.\.(?:[/\s]|$)")  # a ".." path segment (not "a..b")


class Confidence(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class FindingCategory(StrEnum):
    ARCHITECTURE = "architecture"
    TECHNOLOGY = "technology"
    DEPENDENCY = "dependency"
    STRUCTURE = "structure"
    CONFIGURATION = "configuration"
    SECURITY = "security"
    TESTING = "testing"
    INTEGRATION = "integration"
    RISK = "risk"
    CONSTRAINT = "constraint"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def finding_id(position: int) -> str:
    return f"finding_{position:02d}"


def safe_evidence(values: Any, owner: str) -> tuple[str, ...]:
    """Short, redacted, relative, single-line evidence lines."""
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise InvalidAnalysisError("evidence must be a list of strings", details=(owner,))
    if len(values) > EVIDENCE_LINES_MAX:
        raise InvalidAnalysisError(f"at most {EVIDENCE_LINES_MAX} evidence lines", details=(owner,))
    out = []
    for value in values:
        text = redact(text_field(value, "evidence", EVIDENCE_MAX, error=InvalidAnalysisError))
        if text.startswith(("/", "~", "\\")) or _PARENT_RE.search(text) or re.match(r"^[A-Za-z]:[\\/]", text):
            raise InvalidAnalysisError("evidence must use project-relative paths", details=(owner,))
        out.append(text)
    return tuple(out)


def enum_value(cls: type[StrEnum], value: Any, name: str, owner: str) -> Any:
    try:
        return cls(value)
    except ValueError:
        raise InvalidAnalysisError(f"{name} must be one of {[m.value for m in cls]}", details=(owner,)) from None


@dataclass(frozen=True)
class Finding:
    finding_id: str
    category: FindingCategory
    severity: Severity
    title: str
    description: str
    evidence: tuple[str, ...] = ()
    confidence: Confidence = Confidence.OBSERVED
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id, str) or not FINDING_ID_RE.fullmatch(self.finding_id):
            raise InvalidAnalysisError("finding_id must look like finding_01", details=("finding_id",))
        fid = self.finding_id
        object.__setattr__(self, "category", enum_value(FindingCategory, self.category, "category", fid))
        object.__setattr__(self, "severity", enum_value(Severity, self.severity, "severity", fid))
        object.__setattr__(self, "confidence", enum_value(Confidence, self.confidence, "confidence", fid))
        object.__setattr__(self, "title", redact(text_field(self.title, "title", TITLE_MAX,
                                                            error=InvalidAnalysisError)))
        object.__setattr__(self, "description", redact(text_field(self.description, "description",
                                                                  DESCRIPTION_MAX, multiline=True,
                                                                  error=InvalidAnalysisError)))
        evidence = safe_evidence(self.evidence, fid)
        if self.confidence is not Confidence.UNKNOWN and not evidence:
            raise InvalidAnalysisError(f"an {self.confidence} finding needs evidence", details=(fid,))
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "metadata", json_object(self.metadata, "finding.metadata",
                                                         error=InvalidAnalysisError))

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": str(self.category),
            "severity": str(self.severity),
            "title": self.title,
            "description": self.description,
            "evidence": list(self.evidence),
            "confidence": str(self.confidence),
            "metadata": thaw(self.metadata),
        }
