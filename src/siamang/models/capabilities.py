"""Model capabilities: explicit, typed, extensible.

Adding a member is a non-breaking (additive) change. Routing compares
capability sets only; it never inspects model or vendor names.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Capability(StrEnum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    STREAMING = "streaming"
    REASONING = "reasoning"


def parse_capabilities(values: Iterable[str | Capability]) -> frozenset[Capability]:
    """Strict parse; raises ``ValueError`` naming the unknown capability."""
    out = set()
    for v in values:
        try:
            out.add(Capability(v))
        except ValueError:
            known = ", ".join(c.value for c in Capability)
            raise ValueError(f"unknown capability {v!r} (known: {known})") from None
    return frozenset(out)


def format_capabilities(caps: Iterable[Capability]) -> str:
    return ", ".join(sorted(str(c) for c in caps)) or "-"
