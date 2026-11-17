"""Normalized model response."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.models.definition import freeze_metadata


class FinishReason(StrEnum):
    STOP = "stop"  # natural end or stop sequence
    LENGTH = "length"  # hit max_tokens
    CONTENT_FILTER = "content_filter"
    TOOL_CALL = "tool_call"  # reserved for tool calling (not used before Phase 09)
    OTHER = "other"


@dataclass(frozen=True)
class Usage:
    """Tokens as the provider reported them; ``None`` means it reported nothing.

    ``input_tokens`` is every input token the model read, prompt cache included. ``cache_read_tokens`` and
    ``cache_write_tokens`` say how much of that input came from, or went into, a prompt cache; ``None`` when the
    provider reports no cache.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderOutput:
    """What an adapter's ``_generate`` returns. The base class turns it into a
    :class:`ModelResponse`, filling in request/provider/model IDs itself so an
    adapter cannot get them wrong."""

    content: str
    finish_reason: FinishReason = FinishReason.STOP
    usage: Usage = field(default_factory=Usage)
    structured_output: Any = None
    raw_provider_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ModelResponse:
    request_id: str
    provider_id: str
    model_id: str
    content: str
    finish_reason: FinishReason
    usage: Usage = field(default_factory=Usage)
    structured_output: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Vendor-specific extras, kept for debugging only. Core code must not read it.
    raw_provider_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata, "response"))
        if self.raw_provider_metadata is not None:
            object.__setattr__(self, "raw_provider_metadata", MappingProxyType(dict(self.raw_provider_metadata)))

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready. ``raw_provider_metadata`` is deliberately excluded."""
        return {
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "content": self.content,
            "structured_output": self.structured_output,
            "finish_reason": str(self.finish_reason),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "total_tokens": self.usage.total_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "cache_write_tokens": self.usage.cache_write_tokens,
            },
            "metadata": dict(self.metadata),
        }
