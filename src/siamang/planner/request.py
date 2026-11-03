"""PlanningRequest: "create a plan for this existing task". Data, never instructions to execute.

Validation reuses the Task Engine rules instead of a second policy: strict
task/project IDs, and ``input``/``metadata`` as JSON objects under the
Project Registry metadata policy with command/argv/path/credential keys
refused at any depth. The planner adds the execution-oriented keys a plan
request must never carry (``agent_id``, ``tool``, ``model``, ``provider``,
``retry``, ``workflow``, ``callback``, ``executor``, ``git``, ``module``,
...), also at any depth.

The instruction is opaque text. "run rm -rf /" in it is something to plan
around, not something anybody runs.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.planner.errors import PlanningRequestError
from siamang.projects.definition import freeze_metadata
from siamang.tasks.definition import (
    FORBIDDEN_REQUEST_FIELDS,
    canonical_json_object,
    validate_project_id,
    validate_task_id,
)
from siamang.tasks.errors import InvalidTaskError

INSTRUCTION_MAX = 10_000  # hard cap; the policy is usually stricter
CONSTRAINT_MAX = 500
CONSTRAINTS_MAX = 20
REQUIRED = ("task_id", "project_id", "instruction")

# Refused at any depth in input/metadata, on top of the Task Engine key rules.
EXECUTION_KEYS = frozenset({
    "agent", "agent_id", "agents", "agent_ids", "tool", "tool_id", "tools", "model", "model_id", "provider",
    "provider_id", "retry", "retries", "workflow", "callback", "callbacks", "executor", "execute", "run", "git",
    "git_command", "module", "modules", "import", "imports", "function", "functions", "class", "class_path",
    "handler", "orchestrator",
})
# Refused as top-level request fields with a dedicated message.
FORBIDDEN_FIELDS = FORBIDDEN_REQUEST_FIELDS | EXECUTION_KEYS | {
    "arguments", "executable", "binary", "program", "url", "shell_command", "steps", "plan",
}


def has_control(text: str, allow: str = "") -> bool:
    return any(unicodedata.category(c).startswith("C") and c not in allow for c in text)


def text_field(value: Any, name: str, limit: int, *, multiline: bool = False,
               error: type[Exception] = PlanningRequestError) -> str:
    """Trimmed text of 1..limit characters; control characters refused (``\\n``/``\\t`` if multiline)."""
    if not isinstance(value, str) or has_control(value, allow="\n\t" if multiline else ""):
        raise error(f"{name} must be text without control characters", details=(name,))
    text = value.strip() if multiline else " ".join(value.split())
    if not 1 <= len(text) <= limit:
        raise error(f"{name} must be 1-{limit} characters", details=(name,))
    return text


def text_lines(value: Any, name: str, *, error: type[Exception] = PlanningRequestError) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise error(f"{name} must be a list of strings", details=(name,))
    if len(value) > CONSTRAINTS_MAX:
        raise error(f"{name}: at most {CONSTRAINTS_MAX} lines", details=(name,))
    return tuple(text_field(v, name, CONSTRAINT_MAX, error=error) for v in value)


def _execution_keys(value: Any, where: str, error: type[Exception]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in EXECUTION_KEYS:
                raise error("execution-style key is not accepted", details=(where,))
            _execution_keys(item, where, error)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _execution_keys(item, where, error)


def json_object(value: Any, where: str, *, error: type[Exception] = PlanningRequestError) -> Mapping[str, Any]:
    """Task Engine metadata policy + planner execution keys. Returns a read-only copy."""
    try:
        text = canonical_json_object(value, where)
    except InvalidTaskError as exc:
        raise error(f"{where}: {exc.message.split(': ', 1)[-1]}", details=(where,)) from None
    _execution_keys(value or {}, where, error)
    return freeze_metadata(text)


@dataclass(frozen=True)
class PlanningRequest:
    task_id: str
    project_id: str
    instruction: str
    input: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise PlanningRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        object.__setattr__(self, "instruction", text_field(self.instruction, "instruction", INSTRUCTION_MAX,
                                                           multiline=True))
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints"))
        object.__setattr__(self, "input", json_object(self.input, "input"))
        object.__setattr__(self, "metadata", json_object(self.metadata, "metadata"))

    @classmethod
    def from_dict(cls, data: Any) -> PlanningRequest:
        """Strict constructor for untrusted input: unknown or execution-style fields are refused."""
        if not isinstance(data, Mapping):
            raise PlanningRequestError("request must be an object")
        known = set(cls.__dataclass_fields__)
        for key in data:
            if not isinstance(key, str) or key not in known:
                name = str(key)
                if isinstance(key, str) and key.lower() in FORBIDDEN_FIELDS:
                    raise PlanningRequestError("execution-style field is not accepted", details=(name,))
                raise PlanningRequestError("unknown request field", details=(name,))
        missing = [f for f in REQUIRED if f not in data]
        if missing:
            raise PlanningRequestError("missing required field", details=tuple(missing))
        values = dict(data)
        if isinstance(values.get("constraints"), list):
            values["constraints"] = tuple(values["constraints"])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "instruction": self.instruction,
            "input": thaw(self.input),
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
        }
