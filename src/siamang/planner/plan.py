"""Plan: an ordered, acyclic, validated list of steps for one task of one project. Data only.

A Plan validates itself on construction, so an invalid plan cannot exist:

* identity: ``plan_id`` (``pln_<id>``), task and project IDs are well formed;
* steps: at least zero, at most :data:`MAX_STEPS_LIMIT`, exact ``PlanStep`` objects;
* step IDs unique;
* every dependency names a step of this plan, never the step itself;
* the dependency graph is acyclic (cycles are reported with their step IDs);
* ordering: every dependency is listed *before* its dependent, so the list
  order is itself a valid execution order.

``plan_id`` is identity, generated per planning call and **not**
deterministic. :meth:`content` / :attr:`content_hash` exclude it and are
deterministic for the same normalized input.
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

from siamang.planner.errors import DependencyCycleError, InvalidPlanError, TooManyStepsError
from siamang.planner.request import INSTRUCTION_MAX, json_object, text_field, text_lines
from siamang.planner.step import PlanStep
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

PLAN_ID_RE = re.compile(r"^pln_[A-Za-z0-9]{1,64}\Z")
MAX_STEPS_LIMIT = 50  # absolute cap; the policy is usually stricter


def _cycle(steps: tuple[PlanStep, ...]) -> tuple[str, ...]:
    """Step IDs on some dependency cycle, or ``()``. Iterative DFS (no recursion limit)."""
    deps = {s.step_id: s.dependencies for s in steps}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    for root in deps:
        if state.get(root):
            continue
        path: list[str] = []
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, i = stack.pop()
            if i == 0:
                state[node] = 1
                path.append(node)
            children = deps.get(node, ())
            if i < len(children):
                stack.append((node, i + 1))
                child = children[i]
                if state.get(child) == 1:
                    return tuple(sorted(path[path.index(child):]))
                if not state.get(child):
                    stack.append((child, 0))
            else:
                state[node] = 2
                path.pop()
    return ()


def validate_graph(steps: tuple[PlanStep, ...]) -> None:
    ids = [s.step_id for s in steps]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise InvalidPlanError("step IDs must be unique", details=tuple(duplicates))
    known = set(ids)
    for s in steps:
        if s.step_id in s.dependencies:
            raise InvalidPlanError("a step cannot depend on itself", details=(s.step_id,))
        missing = [d for d in s.dependencies if d not in known]
        if missing:
            raise InvalidPlanError("dependency names a step that is not in the plan",
                                   details=(s.step_id, *missing))
    cycle = _cycle(steps)
    if cycle:
        raise DependencyCycleError("dependencies form a cycle", details=cycle)
    position = {sid: i for i, sid in enumerate(ids)}
    for s in steps:
        late = [d for d in s.dependencies if position[d] > position[s.step_id]]
        if late:
            raise InvalidPlanError("a dependency must be listed before its dependent", details=(s.step_id, *late))


@dataclass(frozen=True)
class Plan:
    plan_id: str
    task_id: str
    project_id: str
    objective: str
    steps: tuple[PlanStep, ...]
    constraints: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not PLAN_ID_RE.fullmatch(self.plan_id):
            raise InvalidPlanError("plan_id must look like pln_<id>", details=("plan_id",))
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise InvalidPlanError(f"{name} is not a valid identifier", details=(name,)) from None
        object.__setattr__(self, "objective", text_field(self.objective, "objective", INSTRUCTION_MAX,
                                                         multiline=True, error=InvalidPlanError))
        steps = self.steps
        if isinstance(steps, (str, bytes)) or not isinstance(steps, (list, tuple)):
            raise InvalidPlanError("steps must be a list of PlanStep", details=("steps",))
        if len(steps) > MAX_STEPS_LIMIT:
            raise TooManyStepsError(f"a plan has at most {MAX_STEPS_LIMIT} steps", details=("steps",))
        if not all(type(s) is PlanStep for s in steps):
            raise InvalidPlanError("steps must be PlanStep objects", details=("steps",))
        steps = tuple(steps)
        validate_graph(steps)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=InvalidPlanError))
        object.__setattr__(self, "metadata", json_object(self.metadata, "plan.metadata", error=InvalidPlanError))

    def step(self, step_id: str) -> PlanStep:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(step_id)

    def execution_order(self) -> tuple[str, ...]:
        """A valid order to perform the steps in (the validated list order)."""
        return tuple(s.step_id for s in self.steps)

    def content(self) -> dict[str, Any]:
        """Everything except ``plan_id``: the deterministic, comparable part of a plan."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "steps": [s.to_dict() for s in self.steps],
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
        }

    @property
    def content_hash(self) -> str:
        text = json.dumps(self.content(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, **self.content(), "content_hash": self.content_hash}
