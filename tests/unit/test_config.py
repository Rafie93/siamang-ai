from pathlib import Path

import pytest

from siamang.config import Config, ConfigError, describe, load_config


def test_default_config():
    cfg = load_config(env={})
    assert cfg.app.name == "siamang"
    assert cfg.app.environment == "development"
    assert cfg.logging.level == "INFO"
    assert cfg.logging.console is True
    assert cfg.logging.file is None
    assert cfg.contract.version == "0.2"  # follows CONTRACT_VERSION (v0.2, Phase 10)
    assert cfg.storage.path == Path.home() / ".siamang" / "siamang.db"
    assert cfg.source == "defaults"


def test_valid_config_file(config_file):
    path = config_file(
        """
[app]
environment = "test"
[storage]
path = "data/x.db"
[logging]
level = "DEBUG"
console = false
file = "logs/siamang.jsonl"
"""
    )
    cfg = load_config(path, env={})
    assert cfg.app.environment == "test"
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.console is False
    # relative paths resolve against the config file's directory
    assert cfg.storage.path == path.parent.resolve() / "data" / "x.db"
    assert cfg.logging.file == path.parent.resolve() / "logs" / "siamang.jsonl"


def test_config_path_from_env(config_file):
    path = config_file('[app]\nenvironment = "production"\n')
    cfg = load_config(env={"SIAMANG_CONFIG": str(path)})
    assert cfg.app.environment == "production"


def test_environment_overrides_file(config_file, tmp_path):
    path = config_file('[logging]\nlevel = "DEBUG"\nconsole = true\n')
    cfg = load_config(
        path,
        env={
            "SIAMANG_LOGGING_LEVEL": "ERROR",
            "SIAMANG_LOGGING_CONSOLE": "false",
            "SIAMANG_STORAGE_PATH": str(tmp_path / "env.db"),
        },
    )
    assert cfg.logging.level == "ERROR"
    assert cfg.logging.console is False
    assert cfg.storage.path == tmp_path / "env.db"
    assert cfg.source.endswith("env")


def test_deterministic():
    assert load_config(env={}) == load_config(env={})


@pytest.mark.parametrize(
    "text, message",
    [
        ('[app]\nenvironment = "staging"\n', "app.environment"),
        ('[logging]\nlevel = "LOUD"\n', "logging.level"),
        ('[contract]\nversion = "1.0"\n', "not supported"),
        ('[contract]\nversion = "v0"\n', "contract.version"),
        ('[app]\nname = "   "\n', "app.name"),
        ("[unknown]\nx = 1\n", "unknown config section"),
        ('[app]\ncolour = "red"\n', "unknown config key app.colour"),
        ("[logging]\nconsole = 3\n", "boolean"),
        ("[app]\nname = 5\n", "string"),
        ("[app\n", "not valid TOML"),
    ],
)
def test_invalid_config(config_file, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(config_file(text), env={})


def test_invalid_env_value():
    with pytest.raises(ConfigError, match="logging.console"):
        load_config(env={"SIAMANG_LOGGING_CONSOLE": "maybe"})


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", env={})


def test_example_config_is_valid():
    example = Path(__file__).parents[2] / "siamang.example.toml"
    cfg = load_config(example, env={})
    assert isinstance(cfg, Config)


def test_describe_is_plain_data():
    d = describe(load_config(env={}))
    assert set(d) == {"app", "storage", "logging", "contract", "router", "sandbox", "providers", "models", "agents"}
    assert isinstance(d["storage"]["path"], str)


def test_sandbox_is_off_by_default_and_configurable(tmp_path):
    assert load_config(env={}).sandbox.enabled is False
    file = tmp_path / "s.toml"
    file.write_text('[sandbox]\nenabled = true\nimage = "python:3.12-slim"\ntest_command = "pytest -q"\ntimeout_secs = 60\n')
    box = load_config(file, env={}).sandbox
    assert box.enabled and box.timeout_secs == 60 and box.test_command == "pytest -q"
    assert load_config(env={"SIAMANG_SANDBOX_CPUS": "2"}).sandbox.cpus == 2


@pytest.mark.parametrize("body", ["enabled = true", 'runtime = "sh"', "verify_attempts = 9", "timeout_secs = 0",
                                  'timeout_secs = "soon"'])
def test_sandbox_config_is_validated(tmp_path, body):
    file = tmp_path / "s.toml"
    file.write_text("[sandbox]\n" + body + "\n")
    with pytest.raises(ConfigError):
        load_config(file, env={})
