"""OrchestrationRequest: run one registered agent once, for one running task.

Validation reuses the Agent layer instead of duplicating it: the task ID,
project ID, instruction, input, constraints, and metadata are checked by
constructing the :class:`~siamang.agents.AgentRequest` this request will
become (Task Engine ID validators; input/metadata refuse command, argv,
path, and credential keys at any depth). The orchestrator adds:

* ``agent_id`` must be an agent ID (``agent.<role>``), never a class name,
  module path, import path, or callable;
* a few more key names refused at any depth in ``input``/``metadata``
  (``git``, ``git_command``, ``module``, ``import``, ``function``, ...);
* :meth:`from_dict` refuses unknown and execution-style fields.

``constraints`` follow the Agent layer: a list of short text lines.
"""

from __future__ import annotations

from siamang.jsondata import freeze, thaw

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.agents.definition import AGENT_ID_RE
from siamang.agents.errors import AgentInputError
from siamang.agents.request import AgentRequest
from siamang.orchestrator.errors import OrchestrationRequestError
from siamang.tasks.definition import FORBIDDEN_REQUEST_FIELDS

# Refused at any depth in input/metadata, on top of the Task/Agent key rules.
EXTRA_FORBIDDEN_KEYS = frozenset({"git", "git_command", "git_commands", "module", "modules", "import", "imports",
                                  "function", "functions", "class", "class_path", "handler", "agent_class"})
# Refused as top-level request fields with a dedicated message.
FORBIDDEN_FIELDS = FORBIDDEN_REQUEST_FIELDS | EXTRA_FORBIDDEN_KEYS | {
    "arguments", "executable", "binary", "program", "pathspec", "url", "shell_command", "agents", "agent_ids",
}
REQUIRED = ("task_id", "project_id", "agent_id", "instruction")


def _extra_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in EXTRA_FORBIDDEN_KEYS:
                raise OrchestrationRequestError("execution-style key is not accepted", details=(where,))
            _extra_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _extra_keys(item, where)


@dataclass(frozen=True)
class OrchestrationRequest:
    task_id: str
    project_id: str
    agent_id: str
    instruction: str
    input: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not AGENT_ID_RE.fullmatch(self.agent_id):
            raise OrchestrationRequestError("agent_id must be a registered agent ID like agent.<role>",
                                            details=("agent_id",))
        _extra_keys(self.input, "input")
        _extra_keys(self.metadata, "metadata")
        try:
            agent_request = self._agent_request()
        except AgentInputError as exc:
            raise OrchestrationRequestError(exc.message, cause=str(exc.code), details=exc.details) from None
        object.__setattr__(self, "instruction", agent_request.instruction)
        object.__setattr__(self, "constraints", agent_request.constraints)
        object.__setattr__(self, "input", agent_request.input)
        object.__setattr__(self, "metadata", agent_request.metadata)

    def _agent_request(self) -> AgentRequest:
        return AgentRequest(self.task_id, self.project_id, self.instruction, self.input, self.constraints,
                            self.metadata)

    def to_agent_request(self) -> AgentRequest:
        """The AgentRequest handed to the AgentRegistry (agent_id travels separately)."""
        return self._agent_request()

    @classmethod
    def from_dict(cls, data: Any) -> OrchestrationRequest:
        """Strict constructor for untrusted input: unknown or execution-style fields are refused."""
        if not isinstance(data, Mapping):
            raise OrchestrationRequestError("request must be an object")
        known = set(cls.__dataclass_fields__)
        for key in data:
            if not isinstance(key, str) or key not in known:
                name = str(key)
                if isinstance(key, str) and key.lower() in FORBIDDEN_FIELDS:
                    raise OrchestrationRequestError("execution-style field is not accepted", details=(name,))
                raise OrchestrationRequestError("unknown request field", details=(name,))
        missing = [f for f in REQUIRED if f not in data]
        if missing:
            raise OrchestrationRequestError("missing required field", details=tuple(missing))
        values = dict(data)
        if isinstance(values.get("constraints"), list):
            values["constraints"] = tuple(values["constraints"])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "instruction": self.instruction,
            "input": thaw(self.input),
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
        }
