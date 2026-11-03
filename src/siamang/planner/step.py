"""PlanStep: one unit of work, described. Never an agent, tool, or command.

``role`` says what *kind* of work a step is (analysis, design, ...). It is
not an agent ID: which agent performs a step is an orchestration concern.
``step_id`` is positional and deterministic within a plan (``step_01``,
``step_02``, ...).
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

from siamang.planner.errors import InvalidPlanError
from siamang.planner.request import json_object, text_field, text_lines

STEP_ID_RE = re.compile(r"^step_[0-9]{2,3}\Z")
TITLE_MAX = 120
DESCRIPTION_MAX = 2000  # hard cap; the policy is usually stricter


class StepRole(StrEnum):
    """Kind of work. Describes the step; assigns nothing."""

    ANALYSIS = "analysis"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    REVIEW = "review"
    DOCUMENTATION = "documentation"


def step_id(position: int) -> str:
    """Deterministic ID for the 1-based ``position`` in a plan."""
    return f"step_{position:02d}"


def validate_step_id(value: Any, where: str = "step_id") -> str:
    if not isinstance(value, str) or not STEP_ID_RE.fullmatch(value):
        raise InvalidPlanError(f"{where} must look like step_01", details=(where,))
    return value


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    title: str
    description: str
    role: StepRole
    dependencies: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        sid = validate_step_id(self.step_id)
        object.__setattr__(self, "title", text_field(self.title, "title", TITLE_MAX, error=InvalidPlanError))
        object.__setattr__(self, "description", text_field(self.description, "description", DESCRIPTION_MAX,
                                                           multiline=True, error=InvalidPlanError))
        try:
            object.__setattr__(self, "role", StepRole(self.role))
        except ValueError:
            raise InvalidPlanError(f"role must be one of {[r.value for r in StepRole]}", details=(sid,)) from None
        deps = self.dependencies
        if isinstance(deps, (str, bytes)) or not isinstance(deps, (list, tuple)):
            raise InvalidPlanError("dependencies must be a list of step IDs", details=(sid,))
        deps = tuple(validate_step_id(d, "dependencies") for d in deps)
        if len(set(deps)) != len(deps):
            raise InvalidPlanError("dependencies must not repeat", details=(sid,))
        if sid in deps:
            raise InvalidPlanError("a step cannot depend on itself", details=(sid,))
        object.__setattr__(self, "dependencies", deps)
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=InvalidPlanError))
        object.__setattr__(self, "metadata", json_object(self.metadata, "step.metadata", error=InvalidPlanError))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "role": str(self.role),
            "dependencies": list(self.dependencies),
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
        }
