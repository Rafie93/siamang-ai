"""Deterministic model router: *selects* a model, never runs it.

Order of decisions:

1. explicitly requested model (``request.model_id``): used or rejected, no fallback;
2. required capabilities (``request.effective_capabilities``);
3. enabled models only;
4. provider healthy / model served by provider (optional, ``check_health``);
5. routing policy (configured priority);
6. deterministic tie-breaker (inside the policy).

Generation is a separate step::

    selection = router.select(request)
    response = selection.generate(request)   # == selection.provider.generate(selection.model, request)
"""

from __future__ import annotations

from dataclasses import dataclass

from siamang.log import get_logger
from siamang.models.capabilities import format_capabilities
from siamang.models.definition import ModelDefinition, ProviderHealth
from siamang.models.errors import (
    InvalidModelRequestError,
    ModelUnavailableError,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
)
from siamang.models.policy import PriorityPolicy, RoutingPolicy
from siamang.models.provider import ModelProvider
from siamang.models.registry import ModelNotFoundError, ModelRegistry
from siamang.models.request import ModelRequest
from siamang.models.response import ModelResponse

log = get_logger("models.router")


@dataclass(frozen=True)
class Rejection:
    model_id: str
    reason: str


@dataclass(frozen=True)
class Selection:
    model: ModelDefinition
    provider: ModelProvider
    reason: str
    rejected: tuple[Rejection, ...] = ()

    def generate(self, request: ModelRequest) -> ModelResponse:
        return self.provider.generate(self.model, request)


class ModelRouter:
    def __init__(self, registry: ModelRegistry, policy: RoutingPolicy | None = None, *, check_health: bool = True):
        self.registry = registry
        self.policy = policy or PriorityPolicy()
        self.check_health = check_health

    def select(self, request: ModelRequest) -> Selection:
        if not isinstance(request, ModelRequest):
            raise InvalidModelRequestError("request must be a ModelRequest")
        health: dict[str, ProviderHealth] = {}  # one probe per provider per select()
        if request.model_id is not None:
            selection = self._select_explicit(request, health)
        else:
            selection = self._select_by_policy(request, health)
        log.debug(
            "selected model=%s provider=%s for request=%s (%s)",
            selection.model.model_id,
            selection.provider.provider_id,
            request.request_id,
            selection.reason,
        )
        return selection

    # -- steps -----------------------------------------------------------------

    def _select_explicit(self, request: ModelRequest, health: dict[str, ProviderHealth]) -> Selection:
        rid, mid = request.request_id, request.model_id
        try:
            model = self.registry.get_model(mid)
        except ModelNotFoundError:
            raise ModelUnavailableError("requested model is not registered", model_id=mid, request_id=rid) from None
        where = {"model_id": mid, "provider_id": model.provider_id, "request_id": rid}
        if not model.enabled:
            raise ModelUnavailableError("requested model is disabled", **where)
        missing = request.effective_capabilities - model.capabilities
        if missing:
            raise UnsupportedCapabilityError(
                f"requested model lacks capabilities: {format_capabilities(missing)}", **where
            )
        provider = self.registry.get_provider(model.provider_id)
        problem = self._availability_problem(model, provider, health)
        if problem is not None:
            raise problem(where)
        return Selection(model, provider, "explicit model_id")

    def _select_by_policy(self, request: ModelRequest, health: dict[str, ProviderHealth]) -> Selection:
        rid = request.request_id
        caps = request.effective_capabilities
        models = self.registry.list_models()
        if not models:
            raise ModelUnavailableError("no models are registered", request_id=rid)

        capable = [m for m in models if caps <= m.capabilities]
        rejected = [Rejection(m.model_id, "missing capabilities") for m in models if not caps <= m.capabilities]
        if not capable:
            raise UnsupportedCapabilityError(
                f"no registered model supports: {format_capabilities(caps)}", request_id=rid
            )

        enabled = [m for m in capable if m.enabled]
        rejected += [Rejection(m.model_id, "disabled") for m in capable if not m.enabled]
        if not enabled:
            raise ModelUnavailableError(
                f"every model supporting {format_capabilities(caps)} is disabled", request_id=rid
            )

        available: list[ModelDefinition] = []
        for m in enabled:
            problem = self._availability_problem(m, self.registry.get_provider(m.provider_id), health)
            if problem is None:
                available.append(m)
            else:
                rejected.append(Rejection(m.model_id, problem({}).message))
        if not available:
            raise ProviderUnavailableError(
                f"no available provider for a model supporting {format_capabilities(caps)}", request_id=rid
            )

        chosen = self.policy.order(available)[0]
        return Selection(
            chosen,
            self.registry.get_provider(chosen.provider_id),
            f"policy={self.policy.name} among {len(available)} candidate(s)",
            tuple(rejected),
        )

    def _availability_problem(self, model: ModelDefinition, provider: ModelProvider, health: dict[str, ProviderHealth]):
        """Return a factory for the matching error, or ``None`` if usable."""
        if not provider.info.enabled:
            return lambda where: ProviderUnavailableError("provider disabled", **where)
        if not self.check_health:
            return None
        if provider.provider_id not in health:
            health[provider.provider_id] = provider.health()
        h = health[provider.provider_id]
        if not h.usable:
            detail = h.detail or str(h.status)
            return lambda where: ProviderUnavailableError(f"provider unavailable: {detail}", **where)
        if not h.serves(model):
            return lambda where: ModelUnavailableError(
                f"provider does not serve {model.provider_model!r}", **where
            )
        return None


__all__ = ["ModelRouter", "Rejection", "Selection"]
