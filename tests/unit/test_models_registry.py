import pytest

from siamang.models import (
    Capability,
    DuplicateModelError,
    DuplicateProviderError,
    HealthStatus,
    ModelNotFoundError,
    ModelRegistry,
    ProviderNotFoundError,
)
from tests.support.fake_models import FakeProvider, fake_model

TG, SO, VIS = Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT, Capability.VISION


def _registry():
    reg = ModelRegistry()
    reg.register_provider(FakeProvider("fake", [fake_model("fake-b"), fake_model("fake-a", caps=[TG, VIS])]))
    return reg


# --- providers -------------------------------------------------------------


def test_register_provider_registers_its_models():
    reg = _registry()
    assert [p.provider_id for p in reg.providers()] == ["fake"]
    assert [m.model_id for m in reg.list_models()] == ["fake-a", "fake-b"]  # sorted, deterministic


def test_duplicate_provider():
    reg = _registry()
    with pytest.raises(DuplicateProviderError):
        reg.register_provider(FakeProvider("fake"))


def test_provider_model_listing():
    p = FakeProvider("fake", [fake_model("z"), fake_model("a")])
    assert [m.model_id for m in p.list_models()] == ["a", "z"]


def test_provider_rejects_foreign_models():
    with pytest.raises(ValueError, match="belongs to"):
        FakeProvider("fake", [fake_model("x", provider_id="other")])


def test_provider_registration_is_atomic():
    reg = _registry()
    clash = FakeProvider("other", [fake_model("new-1", provider_id="other"), fake_model("fake-a", provider_id="other")])
    with pytest.raises(DuplicateModelError):
        reg.register_provider(clash)
    assert not reg.has_model("new-1")
    with pytest.raises(ProviderNotFoundError):
        reg.get_provider("other")


def test_unknown_provider():
    reg = _registry()
    with pytest.raises(ProviderNotFoundError):
        reg.get_provider("nope")
    with pytest.raises(ProviderNotFoundError):
        reg.register_model(fake_model("m", provider_id="nope"))
    with pytest.raises(TypeError):
        reg.register_provider(object())


def test_provider_health_and_unavailable():
    assert FakeProvider("fake").health().status is HealthStatus.AVAILABLE
    down = FakeProvider("fake", status=HealthStatus.UNAVAILABLE).health()
    assert not down.usable
    disabled = FakeProvider("fake", enabled=False)
    h = disabled.health()
    assert h.status is HealthStatus.UNAVAILABLE and "disabled" in h.detail
    assert disabled.health_calls == 0  # disabled providers are not probed


def test_health_probe_never_raises():
    h = FakeProvider("fake", health_raises=ConnectionError("connection refused")).health()
    assert h.status is HealthStatus.UNAVAILABLE
    assert "connection refused" in h.detail


# --- models ----------------------------------------------------------------


def test_register_and_lookup_model():
    reg = _registry()
    reg.register_model(fake_model("fake-c", caps=[TG, SO]))
    assert reg.get_model("fake-c").supports_structured_output
    assert reg.has_model("fake-c")


def test_duplicate_model():
    reg = _registry()
    with pytest.raises(DuplicateModelError):
        reg.register_model(fake_model("fake-a"))


def test_model_id_is_unique_across_providers():
    reg = ModelRegistry()
    reg.register_provider(FakeProvider("p1", [fake_model("same", provider_id="p1")]))
    with pytest.raises(DuplicateModelError):
        reg.register_provider(FakeProvider("p2", [fake_model("same", provider_id="p2")]))


def test_unknown_model():
    reg = _registry()
    with pytest.raises(ModelNotFoundError):
        reg.get_model("ghost")
    with pytest.raises(LookupError):  # also catchable as LookupError
        reg.remove_model("ghost")
    with pytest.raises(ModelNotFoundError):
        reg.disable_model("ghost")


def test_disable_enable_and_remove():
    reg = _registry()
    assert reg.disable_model("fake-a").enabled is False
    assert reg.get_model("fake-a").enabled is False  # still registered
    assert [m.model_id for m in reg.list_models(include_disabled=False)] == ["fake-b"]
    assert [m.model_id for m in reg.list_models()] == ["fake-a", "fake-b"]
    assert reg.enable_model("fake-a").enabled is True
    reg.remove_model("fake-b")
    assert not reg.has_model("fake-b")


def test_capability_lookup():
    reg = _registry()
    assert [m.model_id for m in reg.find_by_capabilities([TG])] == ["fake-a", "fake-b"]
    assert [m.model_id for m in reg.find_by_capabilities(["vision"])] == ["fake-a"]
    assert reg.find_by_capabilities([SO]) == []
    reg.disable_model("fake-a")
    assert reg.find_by_capabilities([VIS]) == []
    assert [m.model_id for m in reg.find_by_capabilities([VIS], include_disabled=True)] == ["fake-a"]
