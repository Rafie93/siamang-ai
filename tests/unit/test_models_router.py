import pytest

from siamang.models import (
    Capability,
    HealthStatus,
    ModelErrorCode,
    ModelRegistry,
    ModelRequest,
    ModelRouter,
    ModelUnavailableError,
    PriorityPolicy,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
)
from tests.support.fake_models import FakeProvider, fake_model

TG, SO, VIS, TOOLS = Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT, Capability.VISION, Capability.TOOL_CALLING


def _router(*providers, **kw):
    reg = ModelRegistry()
    for p in providers:
        reg.register_provider(p)
    return ModelRouter(reg, **kw)


def _req(**kw):
    return ModelRequest.from_prompt("halo", **kw)


def test_explicit_model_selection():
    p = FakeProvider("fake", [fake_model("cheap", priority=1), fake_model("pinned", priority=99)])
    sel = _router(p).select(_req(model_id="pinned"))
    assert sel.model.model_id == "pinned"
    assert sel.provider is p
    assert sel.reason == "explicit model_id"


def test_explicit_unknown_model():
    with pytest.raises(ModelUnavailableError, match="not registered"):
        _router(FakeProvider("fake", [fake_model()])).select(_req(model_id="ghost"))


def test_explicit_disabled_model_no_fallback():
    r = _router(FakeProvider("fake", [fake_model("a", enabled=False), fake_model("b")]))
    with pytest.raises(ModelUnavailableError, match="disabled"):
        r.select(_req(model_id="a"))


def test_explicit_model_missing_capability():
    r = _router(FakeProvider("fake", [fake_model("text-only"), fake_model("json", caps=[TG, SO])]))
    with pytest.raises(UnsupportedCapabilityError) as info:
        r.select(_req(model_id="text-only", response_format="json"))
    assert info.value.code is ModelErrorCode.UNSUPPORTED_CAPABILITY
    assert info.value.model_id == "text-only"


def test_explicit_model_provider_down():
    r = _router(FakeProvider("fake", [fake_model("a")], status=HealthStatus.UNAVAILABLE))
    with pytest.raises(ProviderUnavailableError):
        r.select(_req(model_id="a"))


def test_capability_based_selection():
    p = FakeProvider("fake", [fake_model("text", priority=1), fake_model("eyes", caps=[TG, VIS], priority=50)])
    r = _router(p)
    assert r.select(_req()).model.model_id == "text"
    sel = r.select(_req(required_capabilities=[VIS]))
    assert sel.model.model_id == "eyes"
    assert ("text", "missing capabilities") in {(x.model_id, x.reason) for x in sel.rejected}


def test_disabled_model_filtering():
    r = _router(FakeProvider("fake", [fake_model("best", priority=1, enabled=False), fake_model("next", priority=2)]))
    sel = r.select(_req())
    assert sel.model.model_id == "next"
    assert ("best", "disabled") in {(x.model_id, x.reason) for x in sel.rejected}


def test_all_capable_models_disabled():
    r = _router(FakeProvider("fake", [fake_model("a", enabled=False)]))
    with pytest.raises(ModelUnavailableError, match="disabled"):
        r.select(_req())


def test_unavailable_provider_filtering():
    down = FakeProvider("down", [fake_model("fast", provider_id="down", priority=1)], status=HealthStatus.UNAVAILABLE)
    up = FakeProvider("up", [fake_model("slow", provider_id="up", priority=9)])
    sel = _router(down, up).select(_req())
    assert sel.model.model_id == "slow"
    assert any(x.model_id == "fast" and "unavailable" in x.reason for x in sel.rejected)


def test_disabled_provider_filtering():
    off = FakeProvider("off", [fake_model("a", provider_id="off", priority=1)], enabled=False)
    on = FakeProvider("on", [fake_model("b", provider_id="on", priority=2)])
    assert _router(off, on).select(_req()).model.model_id == "b"


def test_degraded_provider_still_usable():
    p = FakeProvider("fake", [fake_model("a")], status=HealthStatus.DEGRADED)
    assert _router(p).select(_req()).model.model_id == "a"


def test_model_not_served_by_provider():
    p = FakeProvider("fake", [fake_model("a", provider_model="tag:a", priority=1), fake_model("b", provider_model="tag:b")],
                     available_models=["tag:b"])
    sel = _router(p).select(_req())
    assert sel.model.model_id == "b"
    with pytest.raises(ModelUnavailableError, match="does not serve"):
        _router(FakeProvider("fake", [fake_model("a", provider_model="tag:a")], available_models=[])).select(
            _req(model_id="a")
        )


def test_all_providers_unavailable():
    p = FakeProvider("fake", [fake_model("a")], health_raises=ConnectionError("refused"))
    with pytest.raises(ProviderUnavailableError) as info:
        _router(p).select(_req())
    assert info.value.retryable


def test_health_check_can_be_disabled():
    p = FakeProvider("fake", [fake_model("a")], status=HealthStatus.UNAVAILABLE)
    assert _router(p, check_health=False).select(_req()).model.model_id == "a"
    assert p.health_calls == 0


def test_health_probed_once_per_provider_per_select():
    p = FakeProvider("fake", [fake_model("a"), fake_model("b"), fake_model("c")])
    _router(p).select(_req())
    assert p.health_calls == 1


def test_priority_ordering():
    p = FakeProvider("fake", [fake_model("low", priority=100), fake_model("high", priority=1), fake_model("mid", priority=50)])
    assert _router(p).select(_req()).model.model_id == "high"
    assert [m.model_id for m in PriorityPolicy().order(p.list_models())] == ["high", "mid", "low"]


def test_priority_across_providers():
    a = FakeProvider("a", [fake_model("a-model", provider_id="a", priority=20)])
    b = FakeProvider("b", [fake_model("b-model", provider_id="b", priority=10)])
    assert _router(a, b).select(_req()).model.model_id == "b-model"


def test_deterministic_tie_breaking():
    models = [fake_model(m) for m in ("zeta", "alpha", "mid")]
    picks = set()
    for order in (models, list(reversed(models)), [models[2], models[0], models[1]]):
        picks.add(_router(FakeProvider("fake", order)).select(_req()).model.model_id)
    assert picks == {"alpha"}  # equal priority -> lowest model_id, regardless of registration order


def test_selection_is_repeatable():
    r = _router(FakeProvider("fake", [fake_model(f"m{i}", priority=i % 3) for i in range(9)]))
    assert len({r.select(_req()).model.model_id for _ in range(20)}) == 1


def test_no_models_registered():
    with pytest.raises(ModelUnavailableError, match="no models"):
        _router().select(_req())


def test_unsupported_capability():
    r = _router(FakeProvider("fake", [fake_model("a")]))
    with pytest.raises(UnsupportedCapabilityError, match="tool_calling"):
        r.select(_req(required_capabilities=[TOOLS]))


def test_router_does_not_generate():
    p = FakeProvider("fake", [fake_model("a")])
    r = _router(p)
    r.select(_req())
    assert p.calls == []
    assert not hasattr(r, "generate")


def test_custom_policy_plugs_in():
    class ReverseAlpha:
        name = "reverse"

        def order(self, candidates):
            return sorted(candidates, key=lambda m: m.model_id, reverse=True)

    p = FakeProvider("fake", [fake_model("a"), fake_model("z")])
    sel = _router(p, policy=ReverseAlpha()).select(_req())
    assert sel.model.model_id == "z" and "policy=reverse" in sel.reason
