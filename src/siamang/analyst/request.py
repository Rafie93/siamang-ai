"""AnalysisRequest: "analyze the project for this existing task". Never execution authority.

Validation reuses the Planner's helpers, which reuse the Task Engine policy
(strict IDs; ``input``/``metadata`` as JSON objects with command, argv,
path, credential, agent, tool, model, provider, retry, workflow, callback,
and executor keys refused at any depth). The analyst adds Git-mutation and
file-selection keys (``commit``, ``branch``, ``worktree``, ``file``,
``glob``, ...): a request can never choose which files are read.

``plan`` optionally carries a validated Phase 13 :class:`~siamang.planner.Plan`
as immutable context. The analyst never calls the Planner.

``focus`` names files the runtime already selected from the repository's tracked
list. Like ``plan`` it is an in-process argument: :meth:`AnalysisRequest.from_dict`
refuses it, and the analyst still verifies each path against the project. ``scope``
is the same kind of argument: the paths a work package owns, new or not.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.analyst.errors import AnalysisRequestError
from siamang.planner.plan import Plan
from siamang.planner.request import FORBIDDEN_FIELDS as PLANNER_FORBIDDEN_FIELDS
from siamang.planner.request import INSTRUCTION_MAX, json_object, text_field, text_lines
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

REQUIRED = ("task_id", "project_id", "instruction")
FOCUS_MAX = 20

# Refused at any depth in input/metadata, on top of the Planner/Task Engine key rules.
ANALYST_KEYS = frozenset({
    "git_write", "commit", "commits", "branch", "checkout", "worktree", "merge", "reset", "push", "pull", "fetch",
    "stash", "rebase", "file", "files", "filename", "filenames", "glob", "pattern", "include", "exclude", "root",
    "root_dir", "folder", "write", "delete", "install",
})
FORBIDDEN_FIELDS = PLANNER_FORBIDDEN_FIELDS | ANALYST_KEYS


def _analyst_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in ANALYST_KEYS:
                raise AnalysisRequestError("execution- or file-selection key is not accepted", details=(where,))
            _analyst_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _analyst_keys(item, where)


@dataclass(frozen=True)
class AnalysisRequest:
    task_id: str
    project_id: str
    instruction: str
    input: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    plan: Plan | None = None
    focus: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise AnalysisRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        err = AnalysisRequestError
        object.__setattr__(self, "instruction", text_field(self.instruction, "instruction", INSTRUCTION_MAX,
                                                           multiline=True, error=err))
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=err))
        _analyst_keys(self.input, "input")
        _analyst_keys(self.metadata, "metadata")
        object.__setattr__(self, "input", json_object(self.input, "input", error=err))
        object.__setattr__(self, "metadata", json_object(self.metadata, "metadata", error=err))
        for name, paths in (("focus", self.focus), ("scope", self.scope)):
            if not isinstance(paths, tuple) or len(paths) > FOCUS_MAX or not all(isinstance(p, str) for p in paths):
                raise AnalysisRequestError(f"{name} must be a tuple of at most {FOCUS_MAX} paths", details=(name,))
        if self.plan is not None:
            if type(self.plan) is not Plan:
                raise AnalysisRequestError("plan must be a validated Plan", details=("plan",))
            if self.plan.task_id != self.task_id or self.plan.project_id != self.project_id:
                raise AnalysisRequestError("plan is for a different task or project", details=("plan",))

    @classmethod
    def from_dict(cls, data: Any) -> AnalysisRequest:
        """Strict constructor for untrusted input. ``plan`` is not accepted here (pass a Plan object)."""
        if not isinstance(data, Mapping):
            raise AnalysisRequestError("request must be an object")
        known = set(cls.__dataclass_fields__) - {"plan", "focus", "scope"}
        for key in data:
            if not isinstance(key, str) or key not in known:
                name = str(key)
                if isinstance(key, str) and key.lower() in FORBIDDEN_FIELDS:
                    raise AnalysisRequestError("execution-style field is not accepted", details=(name,))
                raise AnalysisRequestError("unknown request field", details=(name,))
        missing = [f for f in REQUIRED if f not in data]
        if missing:
            raise AnalysisRequestError("missing required field", details=tuple(missing))
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
            "plan_id": self.plan.plan_id if self.plan else None,
        }
