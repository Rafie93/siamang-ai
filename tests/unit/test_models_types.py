import json
from dataclasses import FrozenInstanceError

import pytest

from siamang.models import (
    Capability,
    FinishReason,
    GenerationOptions,
    HealthStatus,
    InvalidModelRequestError,
    Message,
    ModelDefinition,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    ProviderHealth,
    ProviderInfo,
    ResponseFormat,
    Role,
    Usage,
)
from siamang.models.capabilities import parse_capabilities
from siamang.models.errors import ERROR_TYPES
from tests.support.fake_models import fake_model


# --- capabilities ----------------------------------------------------------


def test_minimum_capabilities_present():
    assert {c.value for c in Capability} >= {
        "text_generation", "structured_output", "tool_calling", "vision", "streaming", "reasoning",
    }


def test_parse_capabilities_strict():
    assert parse_capabilities(["vision", Capability.STREAMING]) == {Capability.VISION, Capability.STREAMING}
    with pytest.raises(ValueError, match="telepathy"):
        parse_capabilities(["telepathy"])


# --- model definition ------------------------------------------------------


def test_model_definition_defaults_and_flags():
    m = fake_model(caps=["text_generation", "tool_calling", "vision"])
    assert m.display_name == m.provider_model == "fake-a"
    assert m.enabled and m.priority == 100
    assert m.supports_tools and m.supports_vision
    assert not (m.supports_streaming or m.supports_structured_output or m.supports_reasoning)
    assert m.supports("vision", Capability.TEXT_GENERATION)
    assert not m.supports("reasoning")
    d = m.to_dict()
    assert d["capabilities"] == ["text_generation", "tool_calling", "vision"]
    json.dumps(d)


def test_model_definition_is_immutable():
    m = fake_model(metadata={"family": "x"})
    with pytest.raises(FrozenInstanceError):
        m.enabled = False
    with pytest.raises(TypeError):
        m.metadata["family"] = "y"


@pytest.mark.parametrize(
    "kw, match",
    [
        ({"model_id": "bad id"}, "model_id"),
        ({"provider_id": ""}, "provider_id"),
        ({"caps": []}, "at least one capability"),
        ({"caps": ["nope"]}, "unknown capability"),
        ({"context_window": 0}, "context_window"),
        ({"context_window": True}, "context_window"),
        ({"priority": "high"}, "priority"),
        ({"enabled": "yes"}, "enabled"),
        ({"metadata": {"api_key": "x"}}, "credential"),
    ],
)
def test_model_definition_validation(kw, match):
    with pytest.raises(ValueError, match=match):
        fake_model(**kw)


def test_provider_info_and_health():
    info = ProviderInfo("local", "echo")
    assert info.name == "local" and info.enabled
    with pytest.raises(ValueError):
        ProviderInfo("bad id", "echo")
    m = fake_model(provider_model="tag:1")
    h = ProviderHealth("fake", HealthStatus.DEGRADED, available_models=frozenset({"tag:1"}))
    assert h.usable and h.serves(m)
    assert not ProviderHealth("fake", HealthStatus.UNAVAILABLE).usable
    assert not ProviderHealth("fake", HealthStatus.AVAILABLE, available_models=frozenset()).serves(m)


# --- request ---------------------------------------------------------------


def test_request_defaults():
    r = ModelRequest.from_prompt("halo")
    assert r.request_id.startswith("mrq_")
    assert r.messages == (Message(Role.USER, "halo"),)
    assert r.effective_capabilities == {Capability.TEXT_GENERATION}
    assert r.response_format is ResponseFormat.TEXT


def test_request_json_requires_structured_output():
    r = ModelRequest.from_prompt("x", response_format="json", required_capabilities=["reasoning"])
    assert r.effective_capabilities == {
        Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT, Capability.REASONING,
    }


def test_request_accepts_message_dicts():
    r = ModelRequest(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                               {"role": "user", "content": "c"}])
    assert [m.role for m in r.messages] == [Role.USER, Role.ASSISTANT, Role.USER]


@pytest.mark.parametrize(
    "kw",
    [
        {"messages": ()},
        {"messages": (Message("user", "a"), Message("assistant", "b"))},
        {"messages": ({"role": "system", "content": "x"}, Message("user", "a"))},
        {"messages": (Message("user", "a"),), "request_id": "bad id"},
        {"messages": (Message("user", "a"),), "model_id": "bad id"},
        {"messages": (Message("user", "a"),), "required_capabilities": ["telepathy"]},
        {"messages": (Message("user", "a"),), "response_format": "xml"},
        {"messages": (Message("user", "a"),), "json_schema": {"type": "object"}},
        {"messages": (Message("user", "a"),), "timeout_secs": 0},
        {"messages": (Message("user", "a"),), "metadata": {"password": "x"}},
        {"messages": ({"role": "user", "content": 5},)},
    ],
)
def test_invalid_requests(kw):
    with pytest.raises(InvalidModelRequestError) as info:
        ModelRequest(**kw)
    assert info.value.code is ModelErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "kw",
    [{"temperature": 3}, {"temperature": True}, {"top_p": 0}, {"max_tokens": 0}, {"max_tokens": 1.5}, {"stop": [""]}],
)
def test_invalid_options(kw):
    with pytest.raises(InvalidModelRequestError):
        GenerationOptions(**kw)


# --- response --------------------------------------------------------------


def test_response_to_dict_excludes_raw_metadata():
    r = ModelResponse("mrq_1", "fake", "fake-a", "hi", FinishReason.STOP, Usage(1, 2),
                      raw_provider_metadata={"vendor_object": "x"})
    d = r.to_dict()
    assert d["usage"]["total_tokens"] == 3
    assert "raw_provider_metadata" not in d
    json.dumps(d)
    assert Usage(1, None).total_tokens is None


def test_every_error_code_has_a_type():
    assert set(ERROR_TYPES) == set(ModelErrorCode)
    assert len(ModelErrorCode) == 10
