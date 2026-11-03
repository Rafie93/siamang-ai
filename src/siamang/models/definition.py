"""Canonical model and provider descriptions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.contracts.schema import ID_RE
from siamang.log import is_secret_name
from siamang.models.capabilities import Capability, parse_capabilities

DEFAULT_PRIORITY = 100


def check_id(kind: str, value: Any) -> None:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValueError(f"invalid {kind} {value!r}")


def freeze_metadata(metadata: Mapping[str, Any] | None, where: str) -> Mapping[str, Any]:
    """Read-only copy; credential-looking keys are rejected (metadata is shown and logged)."""
    data = dict(metadata or {})
    for key in data:
        if not isinstance(key, str):
            raise ValueError(f"{where}: metadata keys must be strings")
        if is_secret_name(key):
            raise ValueError(f"{where}: metadata key {key!r} looks like a credential; use api_key_env")
    return MappingProxyType(data)


@dataclass(frozen=True)
class ModelDefinition:
    """A model SIAMANG can route to.

    ``model_id`` is SIAMANG's own unique ID; ``provider_model`` is the name the
    provider uses (e.g. an Ollama tag). The ``supports_*`` flags are derived
    from ``capabilities`` so they can never disagree with it.
    ``priority``: lower value = preferred (see routing policy).
    """

    model_id: str
    provider_id: str
    capabilities: frozenset[Capability]
    display_name: str = ""
    provider_model: str = ""
    context_window: int | None = None
    priority: int = DEFAULT_PRIORITY
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check_id("model_id", self.model_id)
        check_id("provider_id", self.provider_id)
        object.__setattr__(self, "capabilities", parse_capabilities(self.capabilities))
        if not self.capabilities:
            raise ValueError(f"model {self.model_id!r} must declare at least one capability")
        if not self.display_name:
            object.__setattr__(self, "display_name", self.model_id)
        if not self.provider_model:
            object.__setattr__(self, "provider_model", self.model_id)
        if self.context_window is not None and (
            isinstance(self.context_window, bool) or not isinstance(self.context_window, int) or self.context_window < 1
        ):
            raise ValueError(f"model {self.model_id!r}: context_window must be a positive integer")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError(f"model {self.model_id!r}: priority must be an integer")
        if not isinstance(self.enabled, bool):
            raise ValueError(f"model {self.model_id!r}: enabled must be a boolean")
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata, f"model {self.model_id!r}"))

    def supports(self, *caps: Capability | str) -> bool:
        return parse_capabilities(caps) <= self.capabilities

    @property
    def supports_streaming(self) -> bool:
        return Capability.STREAMING in self.capabilities

    @property
    def supports_tools(self) -> bool:
        return Capability.TOOL_CALLING in self.capabilities

    @property
    def supports_structured_output(self) -> bool:
        return Capability.STRUCTURED_OUTPUT in self.capabilities

    @property
    def supports_vision(self) -> bool:
        return Capability.VISION in self.capabilities

    @property
    def supports_reasoning(self) -> bool:
        return Capability.REASONING in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_model": self.provider_model,
            "capabilities": sorted(str(c) for c in self.capabilities),
            "context_window": self.context_window,
            "priority": self.priority,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProviderInfo:
    """Identity of a provider instance. Holds no credentials, only whether one is configured."""

    provider_id: str
    type: str
    name: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        check_id("provider_id", self.provider_id)
        if not isinstance(self.type, str) or not self.type:
            raise ValueError("provider type must be a non-empty string")
        if not self.name:
            object.__setattr__(self, "name", self.provider_id)


class HealthStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"  # usable, but something is off
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderHealth:
    """Result of a provider health probe.

    ``available_models`` lists ``provider_model`` names the provider reports
    as servable; ``None`` means "not reported" (no per-model filtering).
    """

    provider_id: str
    status: HealthStatus
    detail: str = ""
    available_models: frozenset[str] | None = None
    checked_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.status is not HealthStatus.UNAVAILABLE

    def serves(self, model: ModelDefinition) -> bool:
        return self.available_models is None or model.provider_model in self.available_models
