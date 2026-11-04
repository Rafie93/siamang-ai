"""Typed, validated configuration.

Precedence (lowest to highest): built-in defaults < TOML file < environment.

* File: passed explicitly (``--config`` / ``SIAMANG_CONFIG``). There is no
  implicit search path, so the same inputs always give the same config.
* Environment: ``SIAMANG_<SECTION>_<KEY>``, e.g. ``SIAMANG_LOGGING_LEVEL``.
* Unknown sections/keys are errors (catches typos instead of ignoring them).
* Relative paths in a file resolve against the file's directory; relative
  paths from env/defaults resolve against the current working directory.
* ``[providers.<id>]`` and ``[models.<id>]`` are keyed tables (one per
  provider/model). They come from the file only; env overrides apply to the
  flat sections (``app``, ``storage``, ``logging``, ``contract``, ``router``).
* ``[agents.<id>]`` pins the model an agent uses; ids are :data:`MODEL_AGENTS`.

Secrets never belong here. A provider names the environment variable that
holds its credential (``api_key_env``); the value is never read into config.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from siamang.contracts.version import CONTRACT_VERSION, is_supported, parse_version

ENV_PREFIX = "SIAMANG_"
CONFIG_ENV = "SIAMANG_CONFIG"

ENVIRONMENTS = ("development", "test", "production")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MODEL_AGENTS = ("lead", "planner", "analyst", "programmer", "qa", "reviewer")


class ConfigError(ValueError):
    """Configuration could not be loaded or is invalid."""


@dataclass(frozen=True)
class AppConfig:
    name: str = "siamang"
    environment: str = "development"


@dataclass(frozen=True)
class StorageConfig:
    path: Path = field(default_factory=lambda: Path.home() / ".siamang" / "siamang.db")


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    console: bool = True
    file: Path | None = None


@dataclass(frozen=True)
class ContractConfig:
    version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class RouterConfig:
    health_check: bool = True  # probe provider health during model selection


@dataclass(frozen=True)
class SandboxConfig:
    """Phase 25-26: where project code runs and which commands count as evidence. Off unless ``enabled``."""

    enabled: bool = False
    runtime: str = "podman"  # podman or docker
    image: str = ""  # must already be present locally: the sandbox has no network to pull it
    test_command: str = ""  # one command line, run without a shell
    build_command: str = ""
    timeout_secs: int = 300
    memory_mb: int = 1024
    cpus: int = 1
    verify_attempts: int = 2  # times a failed verification returns to the Programmer (0-3)
    tools: bool = False  # Phase 27: the Programmer reads, edits, and runs commands in the sandbox before proposing
    tool_turns: int = 12  # tool calls per Programmer attempt (1-40)
    tool_runs: int = 6  # commands per Programmer attempt (1-20)


@dataclass(frozen=True)
class RecoveryConfig:
    """Phase 28: what a run does when delivery fails. Off keeps the plain verify-and-retry loop."""

    enabled: bool = False
    retries: int = 1  # tier 1: the Programmer again with the failure as feedback (0-3)
    advisor_rounds: int = 1  # tier 2: advice on a different approach or a split (0-2); needs the lead model
    replans: int = 1  # tier 3: plan the remaining work again (0-2)
    max_attempts: int = 8  # all delivery attempts in one run (1-40)


@dataclass(frozen=True)
class AutonomyConfig:
    """Phase 29-30: which gates pass without the owner, and what the Lead may settle. Off keeps every gate human."""

    level: int = 0  # 0 owner decides everything, 1 low-risk design, 2 also the pull request, 3 also a low-risk merge
    lead: bool = False  # the Lead answers what the owner already decided and sends held work back
    lead_answers: int = 6  # questions the Lead answers per task (0-20)
    lead_revisions: int = 1  # times per task the Lead sends held work back (0-3)
    merge_method: str = "squash"  # level 3: squash, merge, or rebase


@dataclass(frozen=True)
class ProviderConfig:
    type: str
    name: str = ""
    enabled: bool = True
    api_key_env: str | None = None  # NAME of the env var holding the credential, never the value
    base_url: str | None = None
    timeout_secs: float | None = None


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    display_name: str = ""
    provider_model: str = ""
    capabilities: tuple[str, ...] = ("text_generation",)
    context_window: int | None = None
    priority: int = 100
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class AgentConfig:
    model: str
    parallel: int = 3  # the Programmer only: how many work packages run at the same time
    ci_repair: int = 2  # the Programmer only: how many times a failed build returns to it (0 = never)


@dataclass(frozen=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    providers: Mapping[str, ProviderConfig] = field(default_factory=lambda: MappingProxyType({}))
    models: Mapping[str, ModelConfig] = field(default_factory=lambda: MappingProxyType({}))
    agents: Mapping[str, AgentConfig] = field(default_factory=lambda: MappingProxyType({}))
    source: str = "defaults"


_SECTIONS: dict[str, type] = {
    "app": AppConfig,
    "storage": StorageConfig,
    "logging": LoggingConfig,
    "contract": ContractConfig,
    "router": RouterConfig,
    "sandbox": SandboxConfig,
    "recovery": RecoveryConfig,
    "autonomy": AutonomyConfig,
}

# Keyed-table sections: [<section>.<id>] -> entry dataclass.
_TABLE_SECTIONS: dict[str, type] = {
    "providers": ProviderConfig,
    "models": ModelConfig,
    "agents": AgentConfig,
}

# Typed keys of table entries: key -> (kind, required). Kinds: str, bool, int, number, strlist, table.
_ENTRY_KEYS: dict[str, dict[str, tuple[str, bool]]] = {
    "providers": {
        "type": ("str", True),
        "name": ("str", False),
        "enabled": ("bool", False),
        "api_key_env": ("str", False),
        "base_url": ("str", False),
        "timeout_secs": ("number", False),
    },
    "models": {
        "provider": ("str", True),
        "display_name": ("str", False),
        "provider_model": ("str", False),
        "capabilities": ("strlist", False),
        "context_window": ("int", False),
        "priority": ("int", False),
        "enabled": ("bool", False),
        "metadata": ("table", False),
    },
    "agents": {
        "model": ("str", True),
        "parallel": ("int", False),
        "ci_repair": ("int", False),
    },
}


def load_config(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Build a validated :class:`Config`.

    ``env`` defaults to ``os.environ``; tests pass an explicit mapping.
    ``path`` falls back to ``$SIAMANG_CONFIG`` when not given.
    """
    env = os.environ if env is None else env
    raw: dict[str, dict[str, tuple[Any, Path | None]]] = {s: {} for s in _SECTIONS}
    tables: dict[str, dict[str, Any]] = {s: {} for s in _TABLE_SECTIONS}
    sources = ["defaults"]

    if path is None and env.get(CONFIG_ENV):
        path = env[CONFIG_ENV]
    if path is not None:
        file_path = Path(path).expanduser()
        for section, values in _read_file(file_path).items():
            if section in _TABLE_SECTIONS:
                tables[section] = _build_entries(section, values)
                continue
            for key, value in values.items():
                raw[section][key] = (value, file_path.resolve().parent)
        sources.append(str(file_path))

    env_hits = False
    for section, cls in _SECTIONS.items():
        for f in fields(cls):
            name = f"{ENV_PREFIX}{section.upper()}_{f.name.upper()}"
            if name in env:
                raw[section][f.name] = (env[name], None)
                env_hits = True
    if env_hits:
        sources.append("env")

    sections: dict[str, Any] = {s: _build_section(s, values) for s, values in raw.items()}
    sections.update({s: MappingProxyType(dict(sorted(entries.items()))) for s, entries in tables.items()})
    config = Config(**sections, source=" < ".join(sources))
    validate_config(config)
    return config


def _read_file(path: Path) -> dict[str, dict[str, Any]]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file is not valid TOML: {path}: {exc}") from exc

    for section, values in data.items():
        if section not in _SECTIONS and section not in _TABLE_SECTIONS:
            raise ConfigError(f"unknown config section [{section}]")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] must be a table")
        if section in _TABLE_SECTIONS:
            continue  # entries are checked by _build_entries
        known = {f.name for f in fields(_SECTIONS[section])}
        for key in values:
            if key not in known:
                raise ConfigError(f"unknown config key {section}.{key}")
    return data


def _build_entries(section: str, values: dict[str, Any]) -> dict[str, Any]:
    keys = _ENTRY_KEYS[section]
    cls = _TABLE_SECTIONS[section]
    entries = {}
    for entry_id, table in values.items():
        where = f"{section}.{entry_id}"
        if not isinstance(table, dict):
            raise ConfigError(f"[{where}] must be a table")
        for key in table:
            if key not in keys:
                hint = "; put the credential in an env var and set api_key_env" if _looks_secret(key) else ""
                raise ConfigError(f"unknown config key {where}.{key}{hint}")
        for key, (_, required) in keys.items():
            if required and key not in table:
                raise ConfigError(f"{where}.{key} is required")
        kwargs = {key: _typed(f"{where}.{key}", kind, table[key]) for key, (kind, _) in keys.items() if key in table}
        entries[entry_id] = cls(**kwargs)
    return entries


def _typed(name: str, kind: str, value: Any) -> Any:
    ok = {
        "str": isinstance(value, str),
        "bool": isinstance(value, bool),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "strlist": isinstance(value, list) and all(isinstance(v, str) for v in value),
        "table": isinstance(value, dict),
    }[kind]
    if not ok:
        expected = {"strlist": "a list of strings", "table": "a table", "int": "an integer"}.get(kind, f"a {kind}")
        raise ConfigError(f"{name} must be {expected}, got {value!r}")
    if kind == "strlist":
        return tuple(value)
    if kind == "table":
        return MappingProxyType(dict(value))
    return value


def _looks_secret(key: str) -> bool:
    from siamang.log import is_secret_name  # late import: siamang.log imports this module

    return is_secret_name(key) and key != "api_key_env"


def _build_section(section: str, values: dict[str, tuple[Any, Path | None]]) -> Any:
    cls = _SECTIONS[section]
    obj = cls()
    updates = {}
    for f in fields(cls):
        if f.name not in values:
            continue
        value, base = values[f.name]
        updates[f.name] = _coerce(f"{section}.{f.name}", getattr(obj, f.name), value, base, section, f.name)
    return replace(obj, **updates)


def _coerce(name: str, default: Any, value: Any, base: Path | None, section: str, key: str) -> Any:
    is_path = (section, key) in (("storage", "path"), ("logging", "file"))
    if is_path:
        if value is None or value == "":
            if key == "file":
                return None
            raise ConfigError(f"{name} must not be empty")
        if not isinstance(value, str):
            raise ConfigError(f"{name} must be a string path")
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = (base or Path.cwd()) / p
        return p
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("1", "true", "yes", "on"):
            return True
        if isinstance(value, str) and value.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"{name} must be a boolean, got {value!r}")
    if isinstance(default, int):
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ConfigError(f"{name} must be an integer, got {value!r}")
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"{name} must be a string, got {type(value).__name__}")
        return value
    raise ConfigError(f"{name}: unsupported value {value!r}")  # pragma: no cover


def validate_config(config: Config) -> None:
    """Raise :class:`ConfigError` if any value is out of range."""
    box = config.sandbox
    if box.runtime not in ("podman", "docker"):
        raise ConfigError("sandbox.runtime must be podman or docker")
    if box.enabled and not box.image.strip():
        raise ConfigError("sandbox.image is required when the sandbox is enabled")
    if not 0 <= box.verify_attempts <= 3:
        raise ConfigError("sandbox.verify_attempts must be between 0 and 3")
    for key in ("timeout_secs", "memory_mb", "cpus"):
        if getattr(box, key) < 1:
            raise ConfigError(f"sandbox.{key} must be at least 1")
    if not 1 <= box.tool_turns <= 40 or not 1 <= box.tool_runs <= 20:
        raise ConfigError("sandbox.tool_turns must be 1-40 and sandbox.tool_runs 1-20")
    if box.tools and not box.enabled:
        raise ConfigError("sandbox.tools needs sandbox.enabled")
    rec = config.recovery
    for key, low, high in (("retries", 0, 3), ("advisor_rounds", 0, 2), ("replans", 0, 2), ("max_attempts", 1, 40)):
        if not low <= getattr(rec, key) <= high:
            raise ConfigError(f"recovery.{key} must be between {low} and {high}")
    auto = config.autonomy
    if auto.level not in (0, 1, 2, 3):
        raise ConfigError("autonomy.level must be 0, 1, 2 or 3")
    if not 0 <= auto.lead_answers <= 20 or not 0 <= auto.lead_revisions <= 3:
        raise ConfigError("autonomy.lead_answers must be 0-20 and autonomy.lead_revisions 0-3")
    if auto.merge_method not in ("squash", "merge", "rebase"):
        raise ConfigError("autonomy.merge_method must be squash, merge, or rebase")
    if auto.level >= 2 and not (box.enabled and box.test_command.strip()):  # the pull request gate needs a test that ran
        raise ConfigError("autonomy.level 2 and above need sandbox.enabled and sandbox.test_command")
    if not config.app.name.strip():
        raise ConfigError("app.name must not be empty")
    if config.app.environment not in ENVIRONMENTS:
        raise ConfigError(f"app.environment must be one of {ENVIRONMENTS}, got {config.app.environment!r}")
    level = config.logging.level.upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {LOG_LEVELS}, got {config.logging.level!r}")
    try:
        parse_version(config.contract.version)
    except ValueError as exc:
        raise ConfigError(f"contract.version: {exc}") from exc
    if not is_supported(config.contract.version):
        raise ConfigError(f"contract.version {config.contract.version!r} is not supported by this build")
    _validate_models(config)


def _validate_models(config: Config) -> None:
    # Late imports: siamang.models imports siamang.log, which imports this module.
    from siamang.contracts.schema import ID_RE
    from siamang.models.capabilities import parse_capabilities
    from siamang.models.credentials import ENV_NAME_RE
    from siamang.models.providers import PROVIDER_TYPES

    for pid, p in config.providers.items():
        where = f"providers.{pid}"
        if not ID_RE.match(pid):
            raise ConfigError(f"[{where}]: invalid provider id")
        if p.type not in PROVIDER_TYPES:
            raise ConfigError(f"{where}.type {p.type!r} is not a known provider type ({', '.join(sorted(PROVIDER_TYPES))})")
        if p.api_key_env is not None and not ENV_NAME_RE.match(p.api_key_env):
            raise ConfigError(f"{where}.api_key_env must be an environment variable NAME like MY_API_KEY")
        if p.timeout_secs is not None and p.timeout_secs <= 0:
            raise ConfigError(f"{where}.timeout_secs must be > 0")
    for mid, m in config.models.items():
        where = f"models.{mid}"
        if not ID_RE.match(mid):
            raise ConfigError(f"[{where}]: invalid model id")
        if m.provider not in config.providers:
            raise ConfigError(f"{where}.provider {m.provider!r} is not a configured provider")
        try:
            caps = parse_capabilities(m.capabilities)
        except ValueError as exc:
            raise ConfigError(f"{where}.capabilities: {exc}") from None
        if not caps:
            raise ConfigError(f"{where}.capabilities must not be empty")
        if m.context_window is not None and m.context_window < 1:
            raise ConfigError(f"{where}.context_window must be >= 1")
        for key in m.metadata:
            if _looks_secret(key):
                raise ConfigError(f"{where}.metadata.{key} looks like a credential; use the provider's api_key_env")
    for aid, a in config.agents.items():
        where = f"agents.{aid}"
        if aid not in MODEL_AGENTS:
            raise ConfigError(f"[{where}]: unknown agent (configurable: {', '.join(MODEL_AGENTS)})")
        if a.model not in config.models:
            raise ConfigError(f"{where}.model {a.model!r} is not a configured model")
        if not 1 <= a.parallel <= 6:
            raise ConfigError(f"{where}.parallel must be between 1 and 6")
        if not 0 <= a.ci_repair <= 3:
            raise ConfigError(f"{where}.ci_repair must be between 0 and 3")


def describe(config: Config) -> dict[str, dict[str, Any]]:
    """Plain dict view (for CLI output). Contains no secrets by construction
    (``api_key_env`` is a variable name, not its value)."""
    out: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        obj = getattr(config, section)
        out[section] = {f.name: (str(v) if isinstance(v := getattr(obj, f.name), Path) else v) for f in fields(obj)}
    for section in _TABLE_SECTIONS:
        out[section] = {
            entry_id: {f.name: _plain(getattr(entry, f.name)) for f in fields(entry)}
            for entry_id, entry in getattr(config, section).items()
        }
    return out


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    return value
