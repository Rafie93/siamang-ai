"""SystemAnalysis: structured, immutable, bounded result of analyzing one project for one task.

Validated on construction: identity, unique finding/component IDs, hard caps
on collection sizes, and a hard cap on serialized size. ``analysis_id`` is
identity (new on every call); :meth:`content` / :attr:`content_hash` exclude
it and are deterministic for the same request, policy, strategy, and project
snapshot. The hash is a change fingerprint, not a security mechanism.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.analyst.component import Component
from siamang.analyst.errors import InvalidAnalysisError
from siamang.analyst.finding import Finding
from siamang.log import redact
from siamang.planner.request import INSTRUCTION_MAX, json_object, text_field, text_lines
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

ANALYSIS_ID_RE = re.compile(r"^ana_[A-Za-z0-9]{1,64}\Z")
SUMMARY_MAX = 2000
NOTE_MAX = 500
NOTES_MAX = 50
MAX_FINDINGS_LIMIT = 200
MAX_COMPONENTS_LIMIT = 100
MAX_OUTPUT_LIMIT = 512 * 1024
PLANNED_PATHS = "planned_paths"  # metadata key: files the owner's specification or approved design names


def _notes(values: Any, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or len(values) > NOTES_MAX:
        raise InvalidAnalysisError(f"{name} must be a list of at most {NOTES_MAX} lines", details=(name,))
    return tuple(redact(text_field(v, name, NOTE_MAX, error=InvalidAnalysisError)) for v in values)


def _typed(values: Any, cls: type, name: str, limit: int) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise InvalidAnalysisError(f"{name} must be a list", details=(name,))
    if len(values) > limit:
        raise InvalidAnalysisError(f"at most {limit} {name}", details=(name,))
    if not all(type(v) is cls for v in values):
        raise InvalidAnalysisError(f"{name} must be {cls.__name__} objects", details=(name,))
    return tuple(values)


@dataclass(frozen=True)
class SystemAnalysis:
    analysis_id: str
    task_id: str
    project_id: str
    objective: str
    summary: str
    components: tuple[Component, ...] = ()
    findings: tuple[Finding, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_id, str) or not ANALYSIS_ID_RE.fullmatch(self.analysis_id):
            raise InvalidAnalysisError("analysis_id must look like ana_<id>", details=("analysis_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise InvalidAnalysisError(f"{name} is not a valid identifier", details=(name,)) from None
        err = InvalidAnalysisError
        object.__setattr__(self, "objective", text_field(self.objective, "objective", INSTRUCTION_MAX,
                                                         multiline=True, error=err))
        object.__setattr__(self, "summary", redact(text_field(self.summary, "summary", SUMMARY_MAX,
                                                              multiline=True, error=err)))
        components = _typed(self.components, Component, "components", MAX_COMPONENTS_LIMIT)
        findings = _typed(self.findings, Finding, "findings", MAX_FINDINGS_LIMIT)
        for name, ids in (("components", [c.component_id for c in components]),
                          ("findings", [f.finding_id for f in findings])):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise InvalidAnalysisError(f"{name} IDs must be unique", details=tuple(duplicates))
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=err))
        for name, values in (("risks", self.risks), ("unknowns", self.unknowns),
                             ("recommendations", self.recommendations)):
            object.__setattr__(self, name, _notes(values, name))
        object.__setattr__(self, "metadata", json_object(self.metadata, "analysis.metadata", error=err))
        if len(self._canonical().encode("utf-8")) > MAX_OUTPUT_LIMIT:
            raise InvalidAnalysisError(f"analysis exceeds {MAX_OUTPUT_LIMIT} bytes", details=("analysis",))

    def content(self) -> dict[str, Any]:
        """Everything except ``analysis_id``: the deterministic, comparable part."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "summary": self.summary,
            "components": [c.to_dict() for c in self.components],
            "findings": [f.to_dict() for f in self.findings],
            "constraints": list(self.constraints),
            "risks": list(self.risks),
            "unknowns": list(self.unknowns),
            "recommendations": list(self.recommendations),
            "metadata": thaw(self.metadata),
        }

    def _canonical(self) -> str:
        return json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        """Serialized content size in bytes (what the policy's max_output_size bounds)."""
        return len(self._canonical().encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {"analysis_id": self.analysis_id, **self.content(), "content_hash": self.content_hash}
