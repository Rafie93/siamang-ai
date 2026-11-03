"""ProgrammingRequest: "propose code changes for this existing task". Never execution or write authority.

Validation reuses the Analyst/Planner helpers, which reuse the Task Engine
policy (strict IDs; ``input``/``metadata`` as JSON objects with command,
argv, path, cwd, shell, credential, agent, tool, model, provider, retry,
workflow, callback, executor, Git-mutation, and file-selection keys refused
at any depth). The programmer adds the keys that would smuggle in mutation
or approval authority (``apply``, ``approval``, ``write_file``,
``delete_file``, ``rename_file``, ``filesystem``, ``process``, ...), also at
any depth.

``plan`` and ``analysis`` are required and must be validated
:class:`~siamang.planner.Plan` and :class:`~siamang.analyst.SystemAnalysis`
objects for the same task and project: immutable data snapshots, never live
services. The programmer never calls the Planner or the System Analyst.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.analyst.request import ANALYST_KEYS
from siamang.analyst.request import FORBIDDEN_FIELDS as ANALYST_FORBIDDEN_FIELDS
from siamang.planner.plan import Plan
from siamang.planner.request import INSTRUCTION_MAX, json_object, text_field, text_lines
from siamang.programmer.errors import ProgrammingRequestError
from siamang.programmer.safety import deep_freeze, thaw
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

REQUIRED = ("task_id", "project_id", "instruction", "plan", "analysis")

# Refused at any depth in input/metadata, on top of the Analyst/Planner/Task Engine key rules.
PROGRAMMER_KEYS = ANALYST_KEYS | frozenset({
    "command", "commands", "argv", "shell", "cwd", "executor", "callback", "process", "subprocess", "tool",
    "tool_id", "agent", "agent_id", "orchestrator", "approval", "approve", "approved", "apply", "commit",
    "branch", "worktree", "git_write", "filesystem", "write_file", "delete_file", "rename_file", "move_file",
    "patch_apply", "apply_patch", "chmod", "symlink", "exec", "eval",
})
FORBIDDEN_FIELDS = ANALYST_FORBIDDEN_FIELDS | PROGRAMMER_KEYS


def _programmer_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in PROGRAMMER_KEYS:
                raise ProgrammingRequestError("execution- or mutation-style key is not accepted", details=(where,))
            _programmer_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _programmer_keys(item, where)


@dataclass(frozen=True)
class ProgrammingRequest:
    task_id: str
    project_id: str
    instruction: str
    plan: Plan
    analysis: SystemAnalysis
    input: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ProgrammingRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        err = ProgrammingRequestError
        object.__setattr__(self, "instruction", text_field(self.instruction, "instruction", INSTRUCTION_MAX,
                                                           multiline=True, error=err))
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=err))
        _programmer_keys(self.input, "input")
        _programmer_keys(self.metadata, "metadata")
        object.__setattr__(self, "input", deep_freeze(json_object(thaw(self.input), "input", error=err)))
        object.__setattr__(self, "metadata", deep_freeze(json_object(thaw(self.metadata), "metadata", error=err)))
        # Exact types: a subclass could carry behaviour; these are data snapshots only.
        if type(self.plan) is not Plan:
            raise ProgrammingRequestError("plan must be a validated Plan", details=("plan",))
        if type(self.analysis) is not SystemAnalysis:
            raise ProgrammingRequestError("analysis must be a validated SystemAnalysis", details=("analysis",))

    @classmethod
    def from_dict(cls, data: Any, *, plan: Plan, analysis: SystemAnalysis) -> ProgrammingRequest:
        """Strict constructor for untrusted input. ``plan``/``analysis`` are passed as objects, never parsed."""
        if not isinstance(data, Mapping):
            raise ProgrammingRequestError("request must be an object")
        known = set(cls.__dataclass_fields__) - {"plan", "analysis"}
        for key in data:
            if not isinstance(key, str) or key not in known:
                name = str(key)
                if isinstance(key, str) and key.lower() in FORBIDDEN_FIELDS:
                    raise ProgrammingRequestError("execution-style field is not accepted", details=(name,))
                raise ProgrammingRequestError("unknown request field", details=(name,))
        missing = [f for f in ("task_id", "project_id", "instruction") if f not in data]
        if missing:
            raise ProgrammingRequestError("missing required field", details=tuple(missing))
        values = dict(data)
        if isinstance(values.get("constraints"), list):
            values["constraints"] = tuple(values["constraints"])
        return cls(**values, plan=plan, analysis=analysis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "instruction": self.instruction,
            "input": thaw(self.input),
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
            "plan_id": self.plan.plan_id,
            "analysis_id": self.analysis.analysis_id,
        }
