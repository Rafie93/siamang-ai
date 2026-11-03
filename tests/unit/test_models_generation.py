import pytest

from siamang.models import (
    Capability,
    ContextLimitError,
    FinishReason,
    GenerationError,
    GenerationOptions,
    InvalidModelRequestError,
    ModelError,
    ModelErrorCode,
    ModelRegistry,
    ModelRequest,
    ModelRouter,
    ModelTimeoutError,
    ProviderOutput,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
    Usage,
)
from tests.support.fake_models import (
    FakeProvider,
    VendorAuthError,
    VendorRateLimit,
    VendorTimeout,
    fake_model,
)

TG, SO = Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT


def _gen(provider, request, model_id=None):
    model = provider.list_models()[0] if model_id is None else next(m for m in provider.list_models() if m.model_id == model_id)
    return provider.generate(model, request)


def _err(provider, request) -> ModelError:
    with pytest.raises(ModelError) as info:
        _gen(provider, request)
    return info.value


# --- success ---------------------------------------------------------------


def test_successful_generation_via_router():
    p = FakeProvider("fake", [fake_model("a")])
    reg = ModelRegistry()
    reg.register_provider(p)
    req = ModelRequest.from_prompt("halo", request_id="mrq_test1")
    selection = ModelRouter(reg).select(req)
    resp = selection.generate(req)
    assert resp.content == "[a] halo"
    assert p.calls == [("a", "mrq_test1")]


def test_normalized_response_and_propagation():
    p = FakeProvider("fake", [fake_model("a", provider_model="vendor-tag")])
    resp = _gen(p, ModelRequest.from_prompt("x", request_id="mrq_42"))
    assert (resp.request_id, resp.provider_id, resp.model_id) == ("mrq_42", "fake", "a")
    assert resp.finish_reason is FinishReason.STOP
    assert resp.usage == Usage(3, 2) and resp.usage.total_tokens == 5
    assert resp.metadata["provider_type"] == "fake"
    assert resp.metadata["provider_model"] == "vendor-tag"
    assert resp.metadata["duration_ms"] >= 0


def test_adapter_cannot_mislabel_ids():
    # The adapter only returns ProviderOutput; IDs are filled by the base class.
    p = FakeProvider("fake", [fake_model("a")], reply=lambda m, r: ProviderOutput("ok", raw_provider_metadata={"id": "vendor-123"}))
    resp = _gen(p, ModelRequest.from_prompt("x", request_id="mrq_7"))
    assert resp.request_id == "mrq_7" and resp.model_id == "a"
    assert resp.raw_provider_metadata["id"] == "vendor-123"


def test_structured_output_parsed():
    p = FakeProvider("fake", [fake_model("j", caps=[TG, SO])], reply=lambda m, r: '{"ok": true}')
    resp = _gen(p, ModelRequest.from_prompt("x", response_format="json"))
    assert resp.structured_output == {"ok": True}


def test_structured_output_invalid_json():
    p = FakeProvider("fake", [fake_model("j", caps=[TG, SO])], reply=lambda m, r: "not json")
    e = _err(p, ModelRequest.from_prompt("x", response_format="json"))
    assert isinstance(e, GenerationError) and e.code is ModelErrorCode.GENERATION_ERROR


def test_adapter_returning_garbage():
    p = FakeProvider("fake", [fake_model("a")], reply=lambda m, r: ProviderOutput(content=None))
    assert _err(p, ModelRequest.from_prompt("x")).code is ModelErrorCode.GENERATION_ERROR


def test_echo_provider():
    from siamang.models import ProviderInfo
    from siamang.models.providers.echo import EchoProvider

    p = EchoProvider(ProviderInfo("local", "echo"), [fake_model("e", provider_id="local", caps=[TG, SO])])
    assert _gen(p, ModelRequest.from_prompt("satu dua tiga")).content == "satu dua tiga"
    cut = _gen(p, ModelRequest.from_prompt("satu dua tiga", options=GenerationOptions(max_tokens=2)))
    assert (cut.content, cut.finish_reason) == ("satu dua", FinishReason.LENGTH)
    assert _gen(p, ModelRequest.from_prompt("hai", response_format="json")).structured_output == {"echo": "hai"}


# --- errors ----------------------------------------------------------------


def test_timeout_normalization_stdlib():
    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=TimeoutError("read timed out")), ModelRequest.from_prompt("x"))
    assert isinstance(e, ModelTimeoutError) and e.retryable
    assert e.cause == "TimeoutError"


def test_timeout_normalization_vendor():
    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=VendorTimeout("deadline")), ModelRequest.from_prompt("x"))
    assert e.code is ModelErrorCode.TIMEOUT
    assert e.cause == "VendorTimeout"


def test_vendor_rate_limit_and_auth():
    rate = _err(FakeProvider("fake", [fake_model("a")], fail_with=VendorRateLimit("429")), ModelRequest.from_prompt("x"))
    assert rate.code is ModelErrorCode.RATE_LIMITED and rate.retryable
    auth = _err(FakeProvider("fake", [fake_model("a")], fail_with=VendorAuthError("401")), ModelRequest.from_prompt("x"))
    assert auth.code is ModelErrorCode.AUTHENTICATION_ERROR and not auth.retryable


def test_unavailable_provider_on_generate():
    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=ConnectionRefusedError("refused")), ModelRequest.from_prompt("x"))
    assert isinstance(e, ProviderUnavailableError)
    disabled = FakeProvider("fake", [fake_model("a")], enabled=False)
    assert _err(disabled, ModelRequest.from_prompt("x")).code is ModelErrorCode.PROVIDER_UNAVAILABLE
    assert disabled.calls == []


def test_invalid_request_on_generate():
    p = FakeProvider("fake", [fake_model("a"), fake_model("b")])
    e = _err(p, ModelRequest.from_prompt("x", model_id="b"))  # pinned b, called with a
    assert e.code is ModelErrorCode.INVALID_REQUEST
    with pytest.raises(InvalidModelRequestError):
        p.generate(fake_model("other", provider_id="elsewhere"), ModelRequest.from_prompt("x"))
    with pytest.raises(InvalidModelRequestError):
        p.generate(p.list_models()[0], {"prompt": "x"})


def test_context_limit():
    p = FakeProvider("fake", [fake_model("small", context_window=100)])
    e = _err(p, ModelRequest.from_prompt("x", options=GenerationOptions(max_tokens=500)))
    assert isinstance(e, ContextLimitError) and e.code is ModelErrorCode.CONTEXT_LIMIT
    assert p.calls == []
    # adapters may also report it themselves
    p2 = FakeProvider("fake", [fake_model("a")], fail_with=ContextLimitError("prompt too long"))
    assert _err(p2, ModelRequest.from_prompt("x")).code is ModelErrorCode.CONTEXT_LIMIT


def test_unsupported_capability_on_generate():
    p = FakeProvider("fake", [fake_model("a")])
    e = _err(p, ModelRequest.from_prompt("x", required_capabilities=["vision"]))
    assert isinstance(e, UnsupportedCapabilityError)
    assert p.calls == []


def test_generic_provider_error():
    class WeirdSDKError(Exception):
        pass

    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=WeirdSDKError("boom")), ModelRequest.from_prompt("x"))
    assert e.code is ModelErrorCode.UNKNOWN_ERROR
    assert e.cause == "WeirdSDKError"


def test_errors_carry_context_and_serialize():
    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=TimeoutError("t")), ModelRequest.from_prompt("x", request_id="mrq_9"))
    assert (e.provider_id, e.model_id, e.request_id) == ("fake", "a", "mrq_9")
    d = e.to_dict()
    assert d["code"] == "timeout" and d["request_id"] == "mrq_9"
    assert "timeout: t" in str(e)


def test_vendor_exception_does_not_escape_or_chain():
    e = _err(FakeProvider("fake", [fake_model("a")], fail_with=VendorTimeout("x")), ModelRequest.from_prompt("x"))
    assert not isinstance(e, VendorTimeout)
    assert e.__cause__ is None and e.__context__ is None
