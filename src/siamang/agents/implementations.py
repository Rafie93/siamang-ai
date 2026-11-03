"""Implementation allowlist: implementation ID -> SIAMANG-owned agent class.

Code-owned and fixed. The registry instantiates the class itself from this
table; nobody passes in a callable, handler, lambda, or instance. A
definition naming an ID that is not listed here cannot be registered, and at
invocation the stored instance is re-checked to be *exactly* the listed
class (no subclasses). Nothing is discovered, scanned, or imported by name.

Phase 11 lists only the reference agent. Specialized agents (analyst,
programmer, QA, reviewer, planner) arrive in later phases, one explicit
entry each.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from siamang.agents.interface import Agent
from siamang.agents.reference import EchoAgent

KNOWN_IMPLEMENTATIONS: Mapping[str, type[Agent]] = MappingProxyType({
    "reference.echo": EchoAgent,
})


def is_owned(impl_id: str, agent: object) -> bool:
    cls = KNOWN_IMPLEMENTATIONS.get(impl_id)
    return cls is not None and type(agent) is cls
