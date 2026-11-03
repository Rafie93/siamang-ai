"""In-memory model registry (no persistence in Phase 06).

Behavior:

* duplicate provider / model ID -> ``DuplicateProviderError`` / ``DuplicateModelError``
  (registration is atomic: nothing is added when any model clashes);
* unknown provider / model -> ``ProviderNotFoundError`` / ``ModelNotFoundError``;
* disabled model -> still registered and listed, but ``enabled=False`` so the
  router never selects it.

All listings are sorted by ID, so iteration order is deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from siamang.models.capabilities import Capability, parse_capabilities
from siamang.models.definition import ModelDefinition
from siamang.models.provider import ModelProvider


class RegistryError(Exception):
    """Base class for registry errors (programming/config errors, not model failures)."""


class DuplicateProviderError(RegistryError):
    pass


class DuplicateModelError(RegistryError):
    pass


class ProviderNotFoundError(RegistryError, LookupError):
    pass


class ModelNotFoundError(RegistryError, LookupError):
    pass


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._models: dict[str, ModelDefinition] = {}

    # -- providers -----------------------------------------------------------

    def register_provider(self, provider: ModelProvider) -> None:
        """Register ``provider`` and every model from ``provider.list_models()``."""
        if not isinstance(provider, ModelProvider):
            raise TypeError(f"expected ModelProvider, got {type(provider).__name__}")
        pid = provider.provider_id
        if pid in self._providers:
            raise DuplicateProviderError(f"provider already registered: {pid}")
        models = provider.list_models()
        self._check_new_models(models)
        self._providers[pid] = provider
        for m in models:
            self._models[m.model_id] = m

    def get_provider(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderNotFoundError(f"unknown provider: {provider_id}") from None

    def providers(self) -> list[ModelProvider]:
        return [self._providers[k] for k in sorted(self._providers)]

    # -- models ----------------------------------------------------------------

    def register_model(self, model: ModelDefinition) -> None:
        """Add a model to an already-registered provider."""
        if model.provider_id not in self._providers:
            raise ProviderNotFoundError(f"model {model.model_id!r} references unknown provider {model.provider_id!r}")
        self._check_new_models([model])
        self._models[model.model_id] = model

    def get_model(self, model_id: str) -> ModelDefinition:
        try:
            return self._models[model_id]
        except KeyError:
            raise ModelNotFoundError(f"unknown model: {model_id}") from None

    def has_model(self, model_id: str) -> bool:
        return model_id in self._models

    def remove_model(self, model_id: str) -> ModelDefinition:
        model = self.get_model(model_id)
        del self._models[model_id]
        return model

    def set_enabled(self, model_id: str, enabled: bool) -> ModelDefinition:
        model = replace(self.get_model(model_id), enabled=enabled)
        self._models[model_id] = model
        return model

    def disable_model(self, model_id: str) -> ModelDefinition:
        return self.set_enabled(model_id, False)

    def enable_model(self, model_id: str) -> ModelDefinition:
        return self.set_enabled(model_id, True)

    def list_models(self, *, include_disabled: bool = True) -> list[ModelDefinition]:
        return [m for k, m in sorted(self._models.items()) if include_disabled or m.enabled]

    def find_by_capabilities(
        self, capabilities: Iterable[Capability | str], *, include_disabled: bool = False
    ) -> list[ModelDefinition]:
        """Models supporting *all* of ``capabilities``, sorted by ID."""
        caps = parse_capabilities(capabilities)
        return [m for m in self.list_models(include_disabled=include_disabled) if caps <= m.capabilities]

    def _check_new_models(self, models: Iterable[ModelDefinition]) -> None:
        seen = set()
        for m in models:
            if m.model_id in self._models or m.model_id in seen:
                raise DuplicateModelError(f"model already registered: {m.model_id}")
            seen.add(m.model_id)
