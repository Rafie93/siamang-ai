"""LoopRequest: "make bounded correction rounds for this task". Data only; never a budget, approval, or command.

* ``plan`` and ``analysis`` must be **exact** :class:`~siamang.planner.Plan`
  and :class:`~siamang.analyst.SystemAnalysis` objects (immutable snapshots).
* ``instruction``, ``input``, and ``constraints`` are what every Programmer
  call receives; the loop only adds its own feedback block under a reserved
  key, so ``input`` may not use that key.
* ``input`` and ``metadata`` refuse (at any depth) the Reviewer/QA/Programmer
  key rules plus the loop's own (``max_iterations``, ``budget``,
  ``unlimited``, ``feedback``, ...): no request can widen the policy or
  smuggle a verdict. Both are frozen at every depth and copied from the
  caller; ``input`` is at most :data:`INPUT_MAX` bytes.

The services and the policy are not part of the request: they are given to
:class:`~siamang.loop.LoopService` once, so a request carries no live state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.loop.errors import LoopRequestError
from siamang.loop.feedback import FEEDBACK_KEY
from siamang.planner.plan import Plan
from siamang.planner.request import INSTRUCTION_MAX, json_object, text_field, text_lines
from siamang.programmer.safety import deep_freeze, thaw
from siamang.reviewer.request import REVIEWER_KEYS
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

INPUT_MAX = 8 * 1024
# Refused at any depth in input/metadata, on top of the Reviewer/QA/Programmer/Analyst/Planner/Task Engine key rules.
LOOP_KEYS = REVIEWER_KEYS | frozenset({
    FEEDBACK_KEY, "feedback", "iteration", "iterations", "max_iterations", "max_calls", "budget", "budgets",
    "unlimited", "infinite", "policy", "limits", "stop", "stop_reason", "continue",
})


def _loop_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in LOOP_KEYS:
                raise LoopRequestError("execution-, approval-, verdict-, or budget-style key is not accepted",
                                       details=(where,))
            _loop_keys(item, where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _loop_keys(item, where)


@dataclass(frozen=True)
class LoopRequest:
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
                raise LoopRequestError(f"{name} is not a valid identifier", details=(name,)) from None
        for name, value, cls in (("plan", self.plan, Plan), ("analysis", self.analysis, SystemAnalysis)):
            if type(value) is not cls:  # exact types: a subclass could carry behaviour
                raise LoopRequestError(f"{name} must be a validated {cls.__name__}", details=(name,))
        err = LoopRequestError
        object.__setattr__(self, "instruction", text_field(self.instruction, "instruction", INSTRUCTION_MAX,
                                                           multiline=True, error=err))
        object.__setattr__(self, "constraints", text_lines(self.constraints, "constraints", error=err))
        for name in ("input", "metadata"):
            value = self.__dict__[name]
            _loop_keys(value, name)
            frozen = deep_freeze(json_object(thaw(value), name, error=err))
            object.__setattr__(self, name, frozen)
        if len(json.dumps(thaw(self.input), ensure_ascii=False).encode("utf-8")) > INPUT_MAX:
            raise LoopRequestError(f"input exceeds {INPUT_MAX} bytes", details=("input",))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "instruction": self.instruction,
            "plan_id": self.plan.plan_id,
            "analysis_id": self.analysis.analysis_id,
            "input": thaw(self.input),
            "constraints": list(self.constraints),
            "metadata": thaw(self.metadata),
        }
