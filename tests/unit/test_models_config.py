from types import SimpleNamespace

import pytest

from siamang.api.runtime import Runtime, RuntimeRefusal
from siamang.cli.main import main
from siamang.config import ConfigError, describe, load_config
from siamang.models import Capability
from siamang.models.factory import build_registry, build_router
from siamang.models.providers.echo import EchoProvider

BASE = """
[providers.local]
type = "echo"
name = "Local"
api_key_env = "SIAMANG_TEST_KEY"
timeout_secs = 30

[models.small]
provider = "local"
display_name = "Small"
provider_model = "echo:small"
capabilities = ["text_generation", "structured_output"]
context_window = 4096
priority = 10
metadata = { family = "echo" }

[models.off]
provider = "local"
enabled = false
"""


def test_defaults_have_no_models():
    cfg = load_config(env={})
    assert cfg.router.health_check is True
    assert dict(cfg.providers) == {} and dict(cfg.models) == {}
    assert build_registry(cfg, env={}).list_models() == []


def test_toml_providers_and_models(config_file):
    cfg = load_config(config_file(BASE), env={})
    p = cfg.providers["local"]
    assert (p.type, p.name, p.api_key_env, p.timeout_secs, p.enabled) == ("echo", "Local", "SIAMANG_TEST_KEY", 30, True)
    m = cfg.models["small"]
    assert m.capabilities == ("text_generation", "structured_output")
    assert (m.provider_model, m.context_window, m.priority, dict(m.metadata)) == ("echo:small", 4096, 10, {"family": "echo"})
    assert cfg.models["off"].capabilities == ("text_generation",)  # default
    assert cfg.models["off"].enabled is False


def test_env_override_router(config_file):
    cfg = load_config(config_file('[router]\nhealth_check = true\n'), env={"SIAMANG_ROUTER_HEALTH_CHECK": "false"})
    assert cfg.router.health_check is False
    router = build_router(cfg, env={})
    assert router.check_health is False


@pytest.mark.parametrize(
    "text, match",
    [
        ('[router]\nhealth_checks = true\n', "unknown config key router.health_checks"),
        ('[providers.local]\ntype = "echo"\ncolour = "red"\n', "unknown config key providers.local.colour"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\nspeed = 1\n', "unknown config key models.m.speed"),
        ('[providers.local]\ntype = "echo"\napi_key = "sk-123"\n', "api_key_env"),
        ('[providers.local]\nname = "x"\n', "providers.local.type is required"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nenabled = true\n', "models.m.provider is required"),
        ('[providers.local]\ntype = "openai-magic"\n', "not a known provider type"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "ghost"\n', "not a configured provider"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\ncapabilities = ["mind_reading"]\n', "unknown capability"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\ncapabilities = []\n', "must not be empty"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\ncontext_window = 0\n', "context_window"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\npriority = "high"\n', "integer"),
        ('[providers.local]\ntype = "echo"\nenabled = "yes"\n', "bool"),
        ('[providers.local]\ntype = "echo"\napi_key_env = "sk-live-abc"\n', "environment variable NAME"),
        ('[providers.local]\ntype = "echo"\ntimeout_secs = -1\n', "timeout_secs"),
        ('[providers."bad id"]\ntype = "echo"\n', "invalid provider id"),
        ('[providers]\nlocal = 5\n', "must be a table"),
        ('[providers.local]\ntype = "echo"\n[models.m]\nprovider = "local"\nmetadata = { auth_token = "x" }\n', "credential"),
    ],
)
def test_invalid_model_config(config_file, text, match):
    with pytest.raises(ConfigError, match=match):
        load_config(config_file(text), env={})


def test_factory_builds_registry(config_file):
    reg = build_registry(load_config(config_file(BASE), env={}), env={})
    (provider,) = reg.providers()
    assert isinstance(provider, EchoProvider)
    assert provider.timeout_secs == 30
    small = reg.get_model("small")
    assert small.provider_model == "echo:small" and small.supports(Capability.STRUCTURED_OUTPUT)
    assert reg.get_model("off").enabled is False


def test_credential_reference_resolved_not_stored(config_file):
    cfg = load_config(config_file(BASE), env={})
    with_key = build_registry(cfg, env={"SIAMANG_TEST_KEY": "sk-secret-value-123"}).get_provider("local")
    without = build_registry(cfg, env={}).get_provider("local")
    assert with_key.has_credential and not without.has_credential
    assert "sk-secret-value-123" not in repr(cfg) + str(describe(cfg))
    assert describe(cfg)["providers"]["local"]["api_key_env"] == "SIAMANG_TEST_KEY"


def test_no_api_key_needed_for_echo(config_file):
    router = build_router(load_config(config_file(BASE), env={}), env={})
    from siamang.models import ModelRequest

    req = ModelRequest.from_prompt("halo")
    assert router.select(req).generate(req).content == "halo"


# --- CLI -------------------------------------------------------------------


def test_cli_models_empty(capsys, config_file, tmp_path):
    code = main(["--config", str(config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')), "models"])
    assert code == 0
    assert "No model providers configured" in capsys.readouterr().out


def test_cli_models_lists(capsys, config_file, monkeypatch):
    monkeypatch.setenv("SIAMANG_TEST_KEY", "sk-cli-secret-999")
    code = main(["--config", str(config_file(BASE)), "models", "--check"])
    out = capsys.readouterr().out
    assert code == 0
    assert "small" in out and "off" in out and "available" in out
    assert "SIAMANG_TEST_KEY (set)" in out
    assert "sk-cli-secret-999" not in out


def test_cli_models_check_reports_disabled_provider(capsys, config_file):
    path = config_file('[providers.local]\ntype = "echo"\nenabled = false\n[models.m]\nprovider = "local"\n')
    assert main(["--config", str(path), "models", "--check"]) == 1
    assert "provider disabled" in capsys.readouterr().out


def test_cli_health_and_validate_include_models(capsys, config_file, tmp_path):
    path = config_file(BASE + f'\n[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert main(["--config", str(path), "health"]) == 0
    out = capsys.readouterr().out
    assert any(line.startswith("Models") and " OK" in line for line in out.splitlines())
    assert main(["--config", str(path), "validate"]) == 0
    assert "Models" in capsys.readouterr().out


AGENT_MODELS = """
[providers.a]
type = "ollama"

[providers.b]
type = "ollama"

[models.first]
provider = "a"
priority = 1

[models.second]
provider = "b"
priority = 2
"""


def _agent_config(tmp_path, extra):
    path = tmp_path / "agents.toml"
    path.write_text(AGENT_MODELS + extra)
    return path


def test_agent_model_is_loaded_and_described(tmp_path):
    config = load_config(_agent_config(tmp_path, '[agents.programmer]\nmodel = "second"\n'), env={})
    assert config.agents["programmer"].model == "second"
    assert describe(config)["agents"] == {"programmer": {"model": "second", "parallel": 3}}


@pytest.mark.parametrize("extra, message", [
    ('[agents.programmer]\nmodel = "missing"\n', "not a configured model"),
    ('[agents.programer]\nmodel = "first"\n', "unknown agent"),
    ("[agents.programmer]\n", "is required"),
    ('[agents.programmer]\nmodel = "first"\nskill = "x"\n', "unknown config key"),
])
def test_invalid_agent_model_is_refused(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_agent_config(tmp_path, extra), env={})


def _runtime_models(path):
    return Runtime.models(SimpleNamespace(_models=None, settings=SimpleNamespace(config=str(path))))


def test_runtime_registers_only_the_pinned_model(tmp_path):
    router = _runtime_models(_agent_config(tmp_path, '[agents.programmer]\nmodel = "second"\n'))
    assert [m.model_id for m in router.registry.list_models()] == ["second"]


def test_runtime_without_a_pin_keeps_priority_routing(tmp_path):
    router = _runtime_models(_agent_config(tmp_path, ""))
    assert {m.model_id for m in router.registry.list_models()} == {"first", "second"}


def test_runtime_refuses_a_pin_on_a_disabled_model_instead_of_falling_back(tmp_path):
    path = _agent_config(tmp_path, '[agents.programmer]\nmodel = "second"\n')
    path.write_text(path.read_text().replace('[providers.b]\ntype = "ollama"', '[providers.b]\ntype = "ollama"\nenabled = false'))
    with pytest.raises(RuntimeRefusal) as caught:
        _runtime_models(path)
    assert caught.value.code == "model_not_configured"
