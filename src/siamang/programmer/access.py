"""ProgrammerModelAccess: the Programmer's only way to reach a model. Single use, ModelRouter only.

::

    Programmer ──► ProgrammerModelAccess ──► ModelRouter.select ──► Selection.generate ──► ModelProvider

The Phase 11 :class:`~siamang.agents.ModelAccess` is bound to an
``AgentDefinition``; the Programmer is deliberately not an Agent, so it gets
this equivalent narrow facade instead:

* the **router selects**: a request may not pin ``model_id``, and no
  provider, adapter, SDK, or network client is reachable from here;
* only ``text_generation`` and ``structured_output`` may be required
  (no tool calling, vision, or streaming);
* **a fixed number of generations per instance** (one unless the strategy
  declares more, as the tool strategy does for its bounded turns). The service
  builds a fresh access for every programming attempt, so one ``program()``
  call makes at most that many model generations: no retry, fallback, repair,
  or reflection call can hide behind it;
* every :class:`~siamang.models.ModelError` becomes a programmer error with
  the model error code as ``cause`` (no vendor detail, message, or
  credential).
"""

from __future__ import annotations

from siamang.models.capabilities import Capability
from siamang.models.errors import ModelError, ModelErrorCode
from siamang.models.request import ModelRequest
from siamang.models.response import ModelResponse
from siamang.models.router import ModelRouter
from siamang.programmer.errors import (
    ProgrammerModelRejectedError,
    ProgrammerModelTimeoutError,
    ProgrammerModelUnavailableError,
    ProgrammingError,
    ProgrammingInternalError,
)

ALLOWED_CAPABILITIES = frozenset({Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT})
_REJECTED = frozenset({ModelErrorCode.AUTHENTICATION_ERROR, ModelErrorCode.INVALID_REQUEST,
                       ModelErrorCode.CONTEXT_LIMIT, ModelErrorCode.UNSUPPORTED_CAPABILITY})


def model_failure(exc: ModelError) -> ProgrammingError:
    code = exc.code
    if code is ModelErrorCode.TIMEOUT:
        return ProgrammerModelTimeoutError("model generation timed out", cause=str(code))
    if code in _REJECTED:
        return ProgrammerModelRejectedError("model layer rejected the request", cause=str(code))
    return ProgrammerModelUnavailableError("model generation failed", cause=str(code))


class ProgrammerModelAccess:
    __slots__ = ("_router", "_limit", "_calls")

    def __init__(self, router: ModelRouter, limit: int = 1):
        if not isinstance(router, ModelRouter):
            raise TypeError("model access needs a ModelRouter")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 40:
            raise ValueError("limit must be an integer in [1, 40]")
        self._router = router
        self._limit = limit
        self._calls = 0

    @property
    def used(self) -> bool:
        return self._calls > 0

    @property
    def calls(self) -> int:
        return self._calls

    def structured_output_available(self) -> bool:
        """Does an enabled model offer structured output? Reads the registry; generates nothing."""
        caps = (Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT)
        return bool(self._router.registry.find_by_capabilities(caps))

    def generate(self, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise ProgrammingInternalError("model request must be a ModelRequest")
        if self._calls >= self._limit:
            raise ProgrammingInternalError(
                "one model generation per programming attempt" if self._limit == 1
                else f"at most {self._limit} model generations per programming attempt")
        if request.model_id is not None:
            raise ProgrammerModelRejectedError("the Programmer may not pin a model; the ModelRouter selects")
        extra = request.effective_capabilities - ALLOWED_CAPABILITIES
        if extra:
            raise ProgrammerModelRejectedError("model request needs capabilities the Programmer may not use",
                                               details=tuple(sorted(map(str, extra))))
        self._calls += 1  # spent before the call: a failure is not a licence to call again
        failure: ProgrammingError | None = None
        try:
            return self._router.select(request).generate(request)
        except ModelError as exc:
            failure = model_failure(exc)
        except Exception as exc:  # the model layer normalizes errors; never leak anything else either
            failure = ProgrammerModelUnavailableError("model generation failed", cause=type(exc).__name__)
        raise failure
