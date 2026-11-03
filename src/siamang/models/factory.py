"""Build a :class:`ModelRegistry` from validated configuration.

The only module that connects ``siamang.config`` to concrete adapters.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from siamang.config import Config
from siamang.models.credentials import resolve_credential
from siamang.models.definition import ModelDefinition, ProviderInfo
from siamang.models.providers import PROVIDER_TYPES
from siamang.models.registry import ModelRegistry
from siamang.models.router import ModelRouter


def build_registry(config: Config, env: Mapping[str, str] | None = None) -> ModelRegistry:
    env = os.environ if env is None else env
    registry = ModelRegistry()
    for pid, pconf in sorted(config.providers.items()):
        models = [
            ModelDefinition(
                model_id=mid,
                provider_id=pid,
                capabilities=frozenset(mconf.capabilities),
                display_name=mconf.display_name,
                provider_model=mconf.provider_model,
                context_window=mconf.context_window,
                priority=mconf.priority,
                enabled=mconf.enabled,
                metadata=mconf.metadata,
            )
            for mid, mconf in sorted(config.models.items())
            if mconf.provider == pid
        ]
        adapter = PROVIDER_TYPES[pconf.type]
        registry.register_provider(
            adapter(
                ProviderInfo(provider_id=pid, type=pconf.type, name=pconf.name, enabled=pconf.enabled),
                models,
                credential=resolve_credential(pconf.api_key_env, env),
                base_url=pconf.base_url,
                timeout_secs=pconf.timeout_secs,
            )
        )
    return registry


def build_router(config: Config, env: Mapping[str, str] | None = None) -> ModelRouter:
    return ModelRouter(build_registry(config, env), check_health=config.router.health_check)
