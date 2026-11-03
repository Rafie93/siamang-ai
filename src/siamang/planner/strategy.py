"""Planning strategies: where proposed steps come from. The boundary for a future model-backed planner.

A :class:`PlanningStrategy` gets the validated request and a read-only
:class:`~siamang.tasks.Task` snapshot and *proposes* steps. It gets no
registry, engine, database, filesystem, model, tool, or agent. Whatever it
proposes is then bounded by the policy and validated as a :class:`Plan`, so
a strategy can never produce an unbounded or cyclic plan.

Strategies are bound by ID from the code-owned :data:`KNOWN_STRATEGIES`
allowlist; the planner instantiates them itself. Phase 13 has exactly one,
``template.basic``: deterministic, not intelligent, and it states no facts
about the project (project-aware analysis is the System Analyst's job, a
later phase). A future model-backed strategy would sit here, propose steps
through the Model Router, and pass the same validation. It is **not**
implemented.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import MappingProxyType

from siamang.planner.request import PlanningRequest
from siamang.planner.step import PlanStep, StepRole, step_id
from siamang.tasks.definition import Task


class PlanningStrategy(ABC):
    strategy_id: str = ""
    version: str = ""

    @abstractmethod
    def propose(self, request: PlanningRequest, task: Task) -> tuple[PlanStep, ...]:
        """Proposed steps, in execution order. Pure: no I/O, no clock, no randomness."""


# (title, role, description). Generic on purpose: they describe work, not the project.
_TEMPLATE: tuple[tuple[str, StepRole, str], ...] = (
    ("Analyze the requested change", StepRole.ANALYSIS,
     "Restate the objective and agree on acceptance criteria from the task request. "
     "No project files are inspected at planning time."),
    ("Identify affected components", StepRole.ANALYSIS,
     "Determine which parts of the project the change touches. "
     "This needs project-aware analysis, which is not performed by the planner."),
    ("Define implementation approach", StepRole.DESIGN,
     "Choose how to make the change within the identified components and the request's constraints."),
    ("Define validation and test requirements", StepRole.TESTING,
     "Decide how the change will be verified: tests to add or run and checks that must pass."),
)


class TemplateStrategy(PlanningStrategy):
    """Deterministic four-step decomposition. Same normalized request -> same steps."""

    strategy_id = "template.basic"
    version = "1"

    def propose(self, request: PlanningRequest, task: Task) -> tuple[PlanStep, ...]:
        return template_steps(request.constraints)


def template_steps(constraints: tuple[str, ...] = ()) -> tuple[PlanStep, ...]:
    steps = []
    for position, (title, role, description) in enumerate(_TEMPLATE, start=1):
        steps.append(PlanStep(
            step_id=step_id(position),
            title=title,
            description=description,
            role=role,
            dependencies=(step_id(position - 1),) if position > 1 else (),
            constraints=constraints,
        ))
    return tuple(steps)


KNOWN_STRATEGIES: Mapping[str, type[PlanningStrategy]] = MappingProxyType({
    TemplateStrategy.strategy_id: TemplateStrategy,
})
