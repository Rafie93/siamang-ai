"""Programmer (Phase 15): proposes code changes for a task. Proposes; never applies.

Answers "given the Task, Plan, and System Analysis, what code changes should
be made?" with an immutable, bounded :class:`ProgrammingResult` holding a
:class:`Patch` of :class:`ProposedChange` objects (create / modify / delete
of project-relative text files). Models are reached only through
:class:`ProgrammerModelAccess` → ModelRouter, at most once per attempt, and
their output is untrusted data that is strictly parsed and validated.
Current files are read only through the Phase 14 ``ProjectInspector``.
Nothing is written, applied, executed, persisted, or published: a future
Approval System decides whether a proposed patch may be applied.
"""

from siamang.programmer.access import ProgrammerModelAccess
from siamang.programmer.change import BeforeState, Operation, ProposedChange
from siamang.programmer.errors import ProgrammingError, ProgrammingErrorCode
from siamang.programmer.patch import Patch
from siamang.programmer.policy import DEFAULT_POLICY, ProgrammingPolicy
from siamang.programmer.request import ProgrammingRequest
from siamang.programmer.result import ProgrammingResult, ProgrammingStatus
from siamang.programmer.service import Programmer
from siamang.programmer.strategy import KNOWN_STRATEGIES, ModelStrategy, ProgrammingStrategy

__all__ = [
    "DEFAULT_POLICY",
    "KNOWN_STRATEGIES",
    "BeforeState",
    "ModelStrategy",
    "Operation",
    "Patch",
    "Programmer",
    "ProgrammerModelAccess",
    "ProgrammingError",
    "ProgrammingErrorCode",
    "ProgrammingPolicy",
    "ProgrammingRequest",
    "ProgrammingResult",
    "ProgrammingStatus",
    "ProgrammingStrategy",
    "ProposedChange",
]
