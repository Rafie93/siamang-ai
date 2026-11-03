"""Test-only model provider. No network, no SDK, fully scripted."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from siamang.models import (
    Capability,
    HealthStatus,
    ModelDefinition,
    ModelError,
    ModelProvider,
    ProviderHealth,
    ProviderInfo,
    ProviderOutput,
    RateLimitedError,
    ModelAuthenticationError,
    Usage,
)

FAKE_SECRET = "sk-fake-DO-NOT-LEAK-1234567890"


class VendorTimeout(Exception):
    """Stands in for e.g. a vendor SDK's timeout exception."""


class VendorRateLimit(Exception):
    pass


class VendorAuthError(Exception):
    pass


def fake_model(
    model_id: str = "fake-a",
    provider_id: str = "fake",
    caps: Iterable[Capability | str] = (Capability.TEXT_GENERATION,),
    **kw,
) -> ModelDefinition:
    return ModelDefinition(model_id=model_id, provider_id=provider_id, capabilities=frozenset(caps), **kw)


class FakeProvider(ModelProvider):
    """Scriptable provider.

    * ``status`` / ``available_models``: what ``health()`` reports.
    * ``health_raises``: exception thrown by the health probe.
    * ``fail_with``: exception thrown by ``_generate`` (any type).
    * ``reply``: callable(model, request) -> ProviderOutput | str.
    """

    def __init__(
        self,
        provider_id: str = "fake",
        models: Iterable[ModelDefinition] = (),
        *,
        enabled: bool = True,
        status: HealthStatus = HealthStatus.AVAILABLE,
        available_models: Iterable[str] | None = None,
        health_raises: Exception | None = None,
        fail_with: Exception | None = None,
        reply: Callable | None = None,
        **kw,
    ):
        super().__init__(ProviderInfo(provider_id, "fake", enabled=enabled), list(models), **kw)
        self.status = status
        self.available_models = None if available_models is None else frozenset(available_models)
        self.health_raises = health_raises
        self.fail_with = fail_with
        self.reply = reply
        self.calls: list[tuple[str, str]] = []
        self.health_calls = 0

    def _health(self) -> ProviderHealth:
        self.health_calls += 1
        if self.health_raises is not None:
            raise self.health_raises
        return ProviderHealth(self.provider_id, self.status, available_models=self.available_models)

    def _generate(self, model, request) -> ProviderOutput:
        self.calls.append((model.model_id, request.request_id))
        if self.fail_with is not None:
            raise self.fail_with
        if self.reply is not None:
            out = self.reply(model, request)
            return ProviderOutput(content=out) if isinstance(out, str) else out
        return ProviderOutput(content=f"[{model.model_id}] {request.messages[-1].content}", usage=Usage(3, 2))

    def _normalize_error(self, exc: Exception) -> ModelError | None:
        # How a real adapter maps its vendor's exceptions.
        if isinstance(exc, VendorTimeout):
            from siamang.models import ModelTimeoutError

            return ModelTimeoutError(str(exc))
        if isinstance(exc, VendorRateLimit):
            return RateLimitedError(str(exc))
        if isinstance(exc, VendorAuthError):
            return ModelAuthenticationError(str(exc))
        return None
