"""Routing policy: orders eligible candidates. Deterministic by contract.

The router does all filtering (explicit model, capabilities, enabled,
health); a policy only decides the order of what remains. Policies must be
pure functions of their input: no randomness, load, cost, latency, or
model-based judgement (out of scope for Phase 06).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from siamang.models.definition import ModelDefinition


class RoutingPolicy(Protocol):
    name: str

    def order(self, candidates: Sequence[ModelDefinition]) -> list[ModelDefinition]: ...


class PriorityPolicy:
    """Lowest ``priority`` first; ties broken by ``model_id``, then ``provider_id``.

    ``model_id`` is unique in the registry, so the order is total.
    """

    name = "priority"

    def order(self, candidates: Sequence[ModelDefinition]) -> list[ModelDefinition]:
        return sorted(candidates, key=lambda m: (m.priority, m.model_id, m.provider_id))
