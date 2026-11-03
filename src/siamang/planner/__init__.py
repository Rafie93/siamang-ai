"""Planner (Phase 13): decomposition only.

Answers "what work needs to be done for this task?" with a validated,
immutable, acyclic :class:`Plan`. It does not execute, invoke agents, the
orchestrator, tools, or models, inspect the filesystem or Git, change task
state, persist, or emit events.
"""

from siamang.planner.errors import PlanningError, PlanningErrorCode
from siamang.planner.plan import Plan
from siamang.planner.policy import DEFAULT_POLICY, PlanningPolicy
from siamang.planner.request import PlanningRequest
from siamang.planner.service import Planner
from siamang.planner.step import PlanStep, StepRole
from siamang.planner.strategy import KNOWN_STRATEGIES, PlanningStrategy, TemplateStrategy

__all__ = [
    "DEFAULT_POLICY",
    "KNOWN_STRATEGIES",
    "Plan",
    "PlanStep",
    "Planner",
    "PlanningError",
    "PlanningErrorCode",
    "PlanningPolicy",
    "PlanningRequest",
    "PlanningStrategy",
    "StepRole",
    "TemplateStrategy",
]
