"""Provider-independent model request (SIAMANG-native, not any vendor's API shape)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from siamang.contracts.schema import ID_RE
from siamang.ids import new_id
from siamang.models.capabilities import Capability, parse_capabilities
from siamang.models.definition import freeze_metadata
from siamang.models.errors import InvalidModelRequestError


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ResponseFormat(StrEnum):
    TEXT = "text"
    JSON = "json"  # requires Capability.STRUCTURED_OUTPUT


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "role", Role(self.role))
        except ValueError:
            raise InvalidModelRequestError(
                f"invalid message role {self.role!r}; system text goes in ModelRequest.system_prompt"
            ) from None
        if not isinstance(self.content, str):
            raise InvalidModelRequestError("message content must be a string")


@dataclass(frozen=True)
class GenerationOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.temperature is not None and not (_num(self.temperature) and 0 <= self.temperature <= 2):
            raise InvalidModelRequestError("temperature must be a number in [0, 2]")
        if self.top_p is not None and not (_num(self.top_p) and 0 < self.top_p <= 1):
            raise InvalidModelRequestError("top_p must be a number in (0, 1]")
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens < 1
        ):
            raise InvalidModelRequestError("max_tokens must be a positive integer")
        object.__setattr__(self, "stop", tuple(self.stop))
        if not all(isinstance(s, str) and s for s in self.stop):
            raise InvalidModelRequestError("stop sequences must be non-empty strings")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class ModelRequest:
    """One generation request.

    ``model_id`` (optional) pins a model; otherwise the router picks one by
    capability and policy. :attr:`effective_capabilities` always includes
    ``text_generation``, plus ``structured_output`` for JSON responses.
    ``timeout_secs`` is passed to the adapter, which enforces it.
    """

    messages: tuple[Message, ...]
    request_id: str = field(default_factory=lambda: new_id("model_request"))
    model_id: str | None = None
    required_capabilities: frozenset[Capability] = frozenset()
    system_prompt: str | None = None
    options: GenerationOptions = field(default_factory=GenerationOptions)
    response_format: ResponseFormat = ResponseFormat.TEXT
    json_schema: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout_secs: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not ID_RE.match(self.request_id):
            raise InvalidModelRequestError(f"invalid request_id {self.request_id!r}")
        rid = self.request_id
        if self.model_id is not None and (not isinstance(self.model_id, str) or not ID_RE.match(self.model_id)):
            raise InvalidModelRequestError(f"invalid model_id {self.model_id!r}", request_id=rid)
        msgs = tuple(m if isinstance(m, Message) else Message(**m) for m in self.messages)
        if not msgs:
            raise InvalidModelRequestError("at least one message is required", request_id=rid)
        if msgs[-1].role is not Role.USER:
            raise InvalidModelRequestError("the last message must have role 'user'", request_id=rid)
        object.__setattr__(self, "messages", msgs)
        try:
            object.__setattr__(self, "required_capabilities", parse_capabilities(self.required_capabilities))
            object.__setattr__(self, "response_format", ResponseFormat(self.response_format))
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata, "request"))
        except ValueError as exc:
            raise InvalidModelRequestError(str(exc), request_id=rid) from None
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise InvalidModelRequestError("system_prompt must be a string", request_id=rid)
        if self.json_schema is not None and self.response_format is not ResponseFormat.JSON:
            raise InvalidModelRequestError("json_schema requires response_format='json'", request_id=rid)
        if self.timeout_secs is not None and not (_num(self.timeout_secs) and self.timeout_secs > 0):
            raise InvalidModelRequestError("timeout_secs must be a positive number", request_id=rid)
        if not isinstance(self.options, GenerationOptions):
            raise InvalidModelRequestError("options must be GenerationOptions", request_id=rid)

    @classmethod
    def from_prompt(cls, prompt: str, **kwargs: Any) -> ModelRequest:
        return cls(messages=(Message(Role.USER, prompt),), **kwargs)

    @property
    def effective_capabilities(self) -> frozenset[Capability]:
        caps = set(self.required_capabilities) | {Capability.TEXT_GENERATION}
        if self.response_format is ResponseFormat.JSON:
            caps.add(Capability.STRUCTURED_OUTPUT)
        return frozenset(caps)
