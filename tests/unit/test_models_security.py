import io
import pickle
import re
import traceback
from pathlib import Path

import pytest

from siamang.config import LoggingConfig
from siamang.log import configure_logging
from siamang.models import (
    ModelAuthenticationError,
    ModelError,
    ModelRegistry,
    ModelRequest,
    ModelRouter,
    ProviderInfo,
    Secret,
)
from siamang.models.credentials import resolve_credential
from tests.support.fake_models import FAKE_SECRET, FakeProvider, VendorAuthError, fake_model

SRC = Path(__file__).parents[2] / "src"


def _provider(**kw):
    return FakeProvider("fake", [fake_model("a")], credential=Secret(FAKE_SECRET), **kw)


def _everything(err: BaseException) -> str:
    return " ".join([str(err), repr(err), str(getattr(err, "to_dict", dict)()), "".join(traceback.format_exception(err))])


def test_secret_wrapper():
    s = Secret(FAKE_SECRET)
    assert FAKE_SECRET not in repr(s) + str(s) + f"{s}" + str([s]) + str({"k": s})
    assert s.reveal() == FAKE_SECRET
    with pytest.raises(TypeError):
        pickle.dumps(s)
    with pytest.raises(ValueError):
        Secret("")


def test_resolve_credential():
    assert resolve_credential(None, {}) is None
    assert resolve_credential("MY_KEY", {}) is None
    assert resolve_credential("MY_KEY", {"MY_KEY": ""}) is None
    assert resolve_credential("MY_KEY", {"MY_KEY": "v"}).reveal() == "v"
    with pytest.raises(ValueError):
        resolve_credential("sk-not-a-name", {})


def test_credentials_never_in_logs():
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG"), stream=stream)
    ok = _provider()
    bad = _provider(fail_with=VendorAuthError(f"401 invalid api key {FAKE_SECRET}"))
    reg = ModelRegistry()
    reg.register_provider(ok)
    ModelRouter(reg).select(ModelRequest.from_prompt("x"))
    ok.generate(ok.list_models()[0], ModelRequest.from_prompt("x"))
    with pytest.raises(ModelError):
        bad.generate(bad.list_models()[0], ModelRequest.from_prompt("x"))
    logged = stream.getvalue()
    assert "generate failed" in logged
    assert FAKE_SECRET not in logged


def test_credentials_never_in_model_metadata():
    p = _provider()
    resp = p.generate(p.list_models()[0], ModelRequest.from_prompt("x"))
    blob = " ".join([str(resp), str(resp.to_dict()), str(p.list_models()), repr(p), str(p.info), str(p.health())])
    assert FAKE_SECRET not in blob
    assert p.has_credential


@pytest.mark.parametrize(
    "exc",
    [
        VendorAuthError(f"invalid key {FAKE_SECRET}"),  # adapter-mapped vendor error
        RuntimeError(f"request failed with header Authorization: Bearer {FAKE_SECRET}"),  # unmapped
        ConnectionError(f"cannot reach https://u:{FAKE_SECRET}@host"),
        ModelAuthenticationError(f"bad key {FAKE_SECRET}"),  # adapter raised a ModelError itself
    ],
)
def test_credentials_never_in_exceptions(exc):
    p = _provider(fail_with=exc)
    with pytest.raises(ModelError) as info:
        p.generate(p.list_models()[0], ModelRequest.from_prompt("x"))
    assert FAKE_SECRET not in _everything(info.value)


def test_credentials_never_in_health_detail():
    p = _provider(health_raises=ConnectionError(f"auth {FAKE_SECRET} rejected"))
    assert FAKE_SECRET not in p.health().detail


def test_provider_info_has_no_credential_field():
    assert "credential" not in ProviderInfo.__dataclass_fields__
    assert not any("key" in f or "secret" in f for f in ProviderInfo.__dataclass_fields__)


KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # OpenAI/Anthropic-style
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),  # Google
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*[\"'][^\"']{8,}[\"']"),
]


def test_no_hard_coded_api_keys_in_source():
    hits = []
    for path in list(SRC.rglob("*.py")) + list(SRC.rglob("*.toml")) + [SRC.parent / "siamang.example.toml"]:
        text = path.read_text(encoding="utf-8")
        hits += [(path.name, p.pattern) for p in KEY_PATTERNS if p.search(text)]
    assert hits == []
