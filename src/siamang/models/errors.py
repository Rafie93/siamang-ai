"""Normalized model errors.

Every failure that crosses the provider boundary is a :class:`ModelError`
with a typed ``code``. Vendor exceptions never escape: adapters (and
:class:`~siamang.models.provider.ModelProvider`) convert them, keep only the
exception's type name in ``cause``, and redact credential-looking text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from siamang.log import redact


class ModelErrorCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CONTEXT_LIMIT = "context_limit"
    GENERATION_ERROR = "generation_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UNKNOWN_ERROR = "unknown_error"


class ModelError(Exception):
    code: ModelErrorCode = ModelErrorCode.UNKNOWN_ERROR
    retryable: bool = False
    # The tokens a call used before it failed (a ``Usage``), when the provider reported them: a reply that arrives
    # but cannot be used still cost its tokens. ``None`` when nothing is known, as after a timeout.
    usage: Any = None

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        request_id: str | None = None,
        cause: str | None = None,
        retryable: bool | None = None,
    ):
        self.message = redact(message)
        self.provider_id = provider_id
        self.model_id = model_id
        self.request_id = request_id
        self.cause = cause
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __str__(self) -> str:
        where = ", ".join(
            f"{k}={v}"
            for k, v in (("provider", self.provider_id), ("model", self.model_id), ("request", self.request_id))
            if v
        )
        return f"{self.code}: {self.message}" + (f" ({where})" if where else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "request_id": self.request_id,
            "cause": self.cause,
        }


class ProviderUnavailableError(ModelError):
    code = ModelErrorCode.PROVIDER_UNAVAILABLE
    retryable = True


class ModelUnavailableError(ModelError):
    code = ModelErrorCode.MODEL_UNAVAILABLE


class ModelAuthenticationError(ModelError):
    code = ModelErrorCode.AUTHENTICATION_ERROR


class InvalidModelRequestError(ModelError):
    code = ModelErrorCode.INVALID_REQUEST


class ModelTimeoutError(ModelError):
    code = ModelErrorCode.TIMEOUT
    retryable = True


class RateLimitedError(ModelError):
    code = ModelErrorCode.RATE_LIMITED
    retryable = True


class ContextLimitError(ModelError):
    code = ModelErrorCode.CONTEXT_LIMIT


class GenerationError(ModelError):
    code = ModelErrorCode.GENERATION_ERROR


class UnsupportedCapabilityError(ModelError):
    code = ModelErrorCode.UNSUPPORTED_CAPABILITY


class UnexpectedModelError(ModelError):
    code = ModelErrorCode.UNKNOWN_ERROR


ERROR_TYPES: dict[ModelErrorCode, type[ModelError]] = {
    cls.code: cls
    for cls in (
        ProviderUnavailableError,
        ModelUnavailableError,
        ModelAuthenticationError,
        InvalidModelRequestError,
        ModelTimeoutError,
        RateLimitedError,
        ContextLimitError,
        GenerationError,
        UnsupportedCapabilityError,
        UnexpectedModelError,
    )
}


def normalize_exception(exc: BaseException, **where: Any) -> ModelError:
    """Default mapping for exceptions an adapter did not translate itself.

    Only stdlib exception types are recognised here; vendor-specific mapping
    belongs in the adapter's ``_normalize_error`` override.
    """
    if isinstance(exc, ModelError):
        return exc
    cause = type(exc).__name__
    message = str(exc) or cause
    if isinstance(exc, TimeoutError):
        return ModelTimeoutError(message, cause=cause, **where)
    if isinstance(exc, (ConnectionError, OSError)):
        return ProviderUnavailableError(message, cause=cause, **where)
    return UnexpectedModelError(message, cause=cause, **where)
