"""Provider abstraction: the boundary between SIAMANG and any model backend.

Adapters subclass :class:`ModelProvider` and implement ``_generate`` and
(optionally) ``_health``. The public methods are template methods that:

* validate that the model belongs to this provider and supports the request;
* convert *every* non-:class:`ModelError` exception into a normalized
  :class:`ModelError` (``_normalize_error`` hook), raised outside the
  ``except`` block so the vendor exception is not even chained;
* build the :class:`ModelResponse` themselves, so ``request_id``,
  ``provider_id`` and ``model_id`` always propagate correctly;
* parse JSON for ``response_format='json'`` when the adapter did not.

Vendor SDK imports, if ever needed, belong only in modules under
``siamang.models.providers``.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from siamang.log import REDACTED, get_logger
from siamang.models.credentials import Secret
from siamang.models.definition import HealthStatus, ModelDefinition, ProviderHealth, ProviderInfo
from siamang.models.errors import (
    ContextLimitError,
    GenerationError,
    InvalidModelRequestError,
    ModelError,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
    normalize_exception,
)
from siamang.models.capabilities import format_capabilities
from siamang.models.request import ModelRequest, ResponseFormat
from siamang.models.response import ModelResponse, ProviderOutput

log = get_logger("models.provider")


class ModelProvider(ABC):
    def __init__(
        self,
        info: ProviderInfo,
        models: Sequence[ModelDefinition] = (),
        *,
        credential: Secret | None = None,
        base_url: str | None = None,
        timeout_secs: float | None = None,
    ):
        self.info = info
        self._credential = credential
        self.base_url = base_url
        self.timeout_secs = timeout_secs
        for m in models:
            if m.provider_id != info.provider_id:
                raise ValueError(f"model {m.model_id!r} belongs to {m.provider_id!r}, not {info.provider_id!r}")
        self._models = tuple(sorted(models, key=lambda m: m.model_id))

    # -- identity ----------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return self.info.provider_id

    @property
    def has_credential(self) -> bool:
        return self._credential is not None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider_id={self.provider_id!r}, type={self.info.type!r})"

    # -- public API (template methods) -------------------------------------

    def list_models(self) -> list[ModelDefinition]:
        """Models this provider declares (from configuration)."""
        return list(self._models)

    def health(self) -> ProviderHealth:
        """Never raises: any failure is reported as ``UNAVAILABLE``."""
        now = datetime.now(UTC)
        if not self.info.enabled:
            return ProviderHealth(self.provider_id, HealthStatus.UNAVAILABLE, "provider disabled", checked_at=now)
        try:
            result = self._health()
        except Exception as exc:  # noqa: BLE001 - boundary: nothing escapes
            err = self._normalize(exc)
        else:
            if isinstance(result, ProviderHealth) and result.provider_id == self.provider_id:
                if self._credential is not None and self._credential.reveal() in result.detail:
                    result = replace(result, detail=self._scrub(result.detail))
                return result
            err = ProviderUnavailableError("adapter returned an invalid health result")
        return ProviderHealth(self.provider_id, HealthStatus.UNAVAILABLE, self._scrub(err.message), checked_at=now)

    def generate(self, model: ModelDefinition, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise InvalidModelRequestError("request must be a ModelRequest", provider_id=self.provider_id)
        if not isinstance(model, ModelDefinition):
            raise InvalidModelRequestError("model must be a ModelDefinition", provider_id=self.provider_id)
        where = {"provider_id": self.provider_id, "model_id": model.model_id, "request_id": request.request_id}
        self._precheck(model, request, where)

        started = time.monotonic()
        error: ModelError | None = None
        try:
            output = self._generate(model, request)
        except Exception as exc:  # noqa: BLE001 - boundary: nothing escapes
            error = self._normalize(exc, **where)
        if error is not None:
            log.warning("generate failed: %s", error)
            raise error  # raised outside `except`: no vendor exception in __context__

        if not isinstance(output, ProviderOutput):
            raise GenerationError(f"adapter returned {type(output).__name__}, expected ProviderOutput", **where)
        if not isinstance(output.content, str):
            raise GenerationError("adapter returned non-string content", **where)

        structured = output.structured_output
        if request.response_format is ResponseFormat.JSON and structured is None:
            try:
                structured = json.loads(output.content)
            except ValueError:
                structured = None
            if structured is None:
                raise GenerationError("model did not return valid JSON for response_format='json'", **where)

        duration_ms = round((time.monotonic() - started) * 1000, 3)
        log.debug(
            "generated request=%s provider=%s model=%s finish=%s in %sms",
            request.request_id,
            self.provider_id,
            model.model_id,
            output.finish_reason,
            duration_ms,
        )
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model_id=model.model_id,
            content=output.content,
            structured_output=structured,
            finish_reason=output.finish_reason,
            usage=output.usage,
            metadata={"provider_type": self.info.type, "provider_model": model.provider_model, "duration_ms": duration_ms},
            raw_provider_metadata=output.raw_provider_metadata,
        )

    # -- adapter hooks -------------------------------------------------------

    @abstractmethod
    def _generate(self, model: ModelDefinition, request: ModelRequest) -> ProviderOutput:
        """Call the backend. May raise anything; the base class normalizes it."""

    def _health(self) -> ProviderHealth:
        return ProviderHealth(self.provider_id, HealthStatus.AVAILABLE, checked_at=datetime.now(UTC))

    def _normalize_error(self, exc: Exception) -> ModelError | None:
        """Override to map vendor exceptions. Return ``None`` to use the default mapping."""
        return None

    # -- internals -------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """Remove this provider's own credential value, wherever it appears."""
        if self._credential is None or not text:
            return text
        return text.replace(self._credential.reveal(), REDACTED)

    def _normalize(self, exc: Exception, **where: str) -> ModelError:
        err = self._normalize_unscrubbed(exc, **where)
        err.message = self._scrub(err.message)
        err.args = (err.message,)
        return err

    def _normalize_unscrubbed(self, exc: Exception, **where: str) -> ModelError:
        if isinstance(exc, ModelError):
            for key, value in where.items():
                if getattr(exc, key, None) is None:
                    setattr(exc, key, value)
            return exc
        mapped = self._normalize_error(exc)
        if mapped is not None:
            for key, value in where.items():
                if getattr(mapped, key, None) is None:
                    setattr(mapped, key, value)
            mapped.cause = mapped.cause or type(exc).__name__
            return mapped
        return normalize_exception(exc, **where)

    def _precheck(self, model: ModelDefinition, request: ModelRequest, where: dict[str, str]) -> None:
        if model.provider_id != self.provider_id:
            raise InvalidModelRequestError(f"model belongs to provider {model.provider_id!r}", **where)
        if request.model_id is not None and request.model_id != model.model_id:
            raise InvalidModelRequestError(f"request pins model {request.model_id!r}", **where)
        if not self.info.enabled:
            raise ProviderUnavailableError("provider disabled", **where)
        missing = request.effective_capabilities - model.capabilities
        if missing:
            raise UnsupportedCapabilityError(f"model lacks capabilities: {format_capabilities(missing)}", **where)
        max_tokens = request.options.max_tokens
        if max_tokens is not None and model.context_window is not None and max_tokens > model.context_window:
            raise ContextLimitError(
                f"max_tokens {max_tokens} exceeds context window {model.context_window}", **where
            )
