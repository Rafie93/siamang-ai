"""AgentRequest: one unit of work for one agent, for one task of one project.

Not an execution envelope. It carries identifiers, an instruction in prose,
and a JSON ``input`` object checked against the agent's input contract. It
never carries commands, argv, paths, callables, or credentials: ``input``
and ``metadata`` follow the Task Engine rule (keys naming commands, argv,
paths, or credentials are refused at any depth).
"""

from __future__ import annotations

from siamang.jsondata import thaw

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.agents.errors import AgentInputError
from siamang.projects.definition import freeze_metadata
from siamang.tasks.definition import (
    FORBIDDEN_REQUEST_FIELDS,
    canonical_json_object,
    validate_project_id,
    validate_task_id,
)
from siamang.tasks.errors import InvalidTaskError
from siamang.tools.definition import FORBIDDEN_INPUT_FIELDS

INSTRUCTION_MAX = 10_000
CONSTRAINT_MAX = 500
CONSTRAINTS_MAX = 20
REQUIRED = ("task_id", "project_id", "instruction")


def _control(text: str, allow: str = "") -> bool:
    return any((ord(c) < 32 or 127 <= ord(c) < 160) and c not in allow for c in text)


@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    project_id: str
    instruction: str
    input: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        try:
            validate_task_id(self.task_id)
            validate_project_id(self.project_id)
            input_text = canonical_json_object(self.input, "input")
            metadata_text = canonical_json_object(self.metadata, "metadata")
        except InvalidTaskError as exc:
            raise AgentInputError(f"{exc.field}: {exc.message.split(': ', 1)[-1]}", details=(exc.field,)) from None
        if not isinstance(self.instruction, str) or _control(self.instruction, allow="\n\t"):
            raise AgentInputError("instruction must be text without control characters", details=("instruction",))
        instruction = self.instruction.strip()
        if not 1 <= len(instruction) <= INSTRUCTION_MAX:
            raise AgentInputError(f"instruction must be 1-{INSTRUCTION_MAX} characters", details=("instruction",))
        if isinstance(self.constraints, (str, bytes)) or not isinstance(self.constraints, (list, tuple)):
            raise AgentInputError("constraints must be a list of strings", details=("constraints",))
        constraints = tuple(self.constraints)
        if len(constraints) > CONSTRAINTS_MAX or not all(
            isinstance(c, str) and c.strip() and len(c) <= CONSTRAINT_MAX and not _control(c) for c in constraints
        ):
            raise AgentInputError(f"constraints: at most {CONSTRAINTS_MAX} lines of 1-{CONSTRAINT_MAX} characters",
                                  details=("constraints",))
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "constraints", tuple(c.strip() for c in constraints))
        object.__setattr__(self, "input", freeze_metadata(input_text))
        object.__setattr__(self, "metadata", freeze_metadata(metadata_text))

    @classmethod
    def from_dict(cls, data: Any) -> AgentRequest:
        """Strict constructor for untrusted input: unknown or execution-style fields are refused."""
        if not isinstance(data, Mapping):
            raise AgentInputError("request must be an object")
        known = set(cls.__dataclass_fields__)
        for key in data:
            if not isinstance(key, str) or key not in known:
                name = str(key)
                if isinstance(key, str) and key.lower() in FORBIDDEN_REQUEST_FIELDS | FORBIDDEN_INPUT_FIELDS:
                    raise AgentInputError("execution-style or path field is not accepted", details=(name,))
                raise AgentInputError("unknown request field", details=(name,))
        missing = [f for f in REQUIRED if f not in data]
        if missing:
            raise AgentInputError("missing required field", details=tuple(missing))
        values = dict(data)
        if "constraints" in values and isinstance(values["constraints"], list):
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
