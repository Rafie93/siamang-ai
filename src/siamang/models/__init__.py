"""Model provider + router foundation (Phase 06).

Dependency direction: router -> registry/policy -> provider abstraction ->
adapters (``siamang.models.providers``). Nothing outside ``providers`` may
import a vendor SDK. This package does not run agents, tools, or tasks.

``siamang.models.factory`` (config -> registry) is intentionally not
re-exported here, to keep ``siamang.config`` free of import cycles.
"""

from siamang.models.capabilities import Capability
from siamang.models.credentials import Secret
from siamang.models.definition import HealthStatus, ModelDefinition, ProviderHealth, ProviderInfo
from siamang.models.errors import (
    ContextLimitError,
    GenerationError,
    InvalidModelRequestError,
    ModelAuthenticationError,
    ModelError,
    ModelErrorCode,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderUnavailableError,
    RateLimitedError,
    UnexpectedModelError,
    UnsupportedCapabilityError,
)
from siamang.models.policy import PriorityPolicy, RoutingPolicy
from siamang.models.provider import ModelProvider
from siamang.models.registry import (
    DuplicateModelError,
    DuplicateProviderError,
    ModelNotFoundError,
    ModelRegistry,
    ProviderNotFoundError,
    RegistryError,
)
from siamang.models.request import GenerationOptions, Message, ModelRequest, ResponseFormat, Role
from siamang.models.response import FinishReason, ModelResponse, ProviderOutput, Usage
from siamang.models.router import ModelRouter, Rejection, Selection

__all__ = [
    "Capability",
    "ContextLimitError",
    "DuplicateModelError",
    "DuplicateProviderError",
    "FinishReason",
    "GenerationError",
    "GenerationOptions",
    "HealthStatus",
    "InvalidModelRequestError",
    "Message",
    "ModelAuthenticationError",
    "ModelDefinition",
    "ModelError",
    "ModelErrorCode",
    "ModelNotFoundError",
    "ModelProvider",
    "ModelRegistry",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "PriorityPolicy",
    "ProviderHealth",
    "ProviderInfo",
    "ProviderNotFoundError",
    "ProviderOutput",
    "ProviderUnavailableError",
    "RateLimitedError",
    "RegistryError",
    "Rejection",
    "ResponseFormat",
    "Role",
    "RoutingPolicy",
    "Secret",
    "Selection",
    "UnexpectedModelError",
    "UnsupportedCapabilityError",
    "Usage",
]
