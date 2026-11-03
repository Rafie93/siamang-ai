"""Choosing each agent's model over HTTP. Edits the operator's TOML in place and keeps its comments."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from siamang.config import MODEL_AGENTS, ConfigError, load_config
from siamang.models.definition import HealthStatus
from siamang.models.errors import ModelError
from siamang.models.factory import build_registry

from .runtime import RuntimeRefusal

CAPABILITIES = ('text_generation', 'structured_output')  # what every agent asks of its model
_write = threading.Lock()


def _installed(config, provider_id):
    """Tags the Ollama server has right now; ``None`` when it cannot be asked."""
    only = replace(config, providers={provider_id: config.providers[provider_id]}, models={})
    try:
        health = build_registry(only).get_provider(provider_id).health()
    except (ModelError, ValueError):
        return None
    return health.available_models if health.status == HealthStatus.AVAILABLE else None


def _cloud(provider, tag):
    if provider.type != 'ollama':
        return True
    host = urlsplit(provider.base_url or 'http://127.0.0.1').hostname
    return tag.endswith('cloud') or host not in ('127.0.0.1', 'localhost', '::1')  # Ollama names hosted models *:cloud


def _options(config):
    """Configured models plus whatever Ollama has installed. Echo is never offered: it is not a model."""
    options, unreachable = {}, []
    for pid, provider in config.providers.items():
        if provider.type == 'echo' or not provider.enabled:
            continue
        installed = None
        if provider.type == 'ollama':
            installed = _installed(config, pid)
            if installed is None:
                unreachable.append(pid)
        for mid, model in config.models.items():
            tag = model.provider_model or mid
            if model.provider == pid and model.enabled and (pid, tag) not in options:
                available = provider.type != 'ollama' or tag in (installed or ())
                options[pid, tag] = dict(provider=pid, provider_type=provider.type, provider_model=tag, model_id=mid,
                                         available=available, cloud=_cloud(provider, tag))
        for tag in sorted(installed or ()):
            options.setdefault((pid, tag), dict(provider=pid, provider_type=provider.type, provider_model=tag, model_id=None,
                                                available=True, cloud=_cloud(provider, tag)))
    return options, unreachable


def _load(path):
    if not path:
        raise RuntimeRefusal('config_not_editable', 409)
    try:
        return load_config(path)
    except ConfigError:
        raise RuntimeRefusal('config_invalid', 409) from None


def catalog(path):
    config = _load(path)
    options, unreachable = _options(config)
    agents = []
    for agent in MODEL_AGENTS:
        pin = config.agents.get(agent)
        model = config.models[pin.model] if pin else None
        agents.append(dict(agent=agent, model_id=pin.model if pin else None, provider=model.provider if model else None,
                           provider_model=(model.provider_model or pin.model) if model else None))
    return dict(agents=agents, options=list(options.values()), unreachable=unreachable)


def _new_id(config, provider_id, tag):
    base = re.sub(r'[^a-z0-9]+', '-', f'{provider_id}-{tag}'.lower()).strip('-')[:100]  # also a bare TOML key
    candidate, n = base, 1
    while candidate in config.models:
        n += 1
        candidate = f'{base}-{n}'
    return candidate


def _pin(text, agent, model_id):
    """``text`` with the agent on ``model_id``; everything else stays byte for byte."""
    lines = text.splitlines(keepends=True)
    head = re.compile(rf'\s*\[agents\.{agent}\]\s*(#.*)?$')
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return f'{text.rstrip()}\n\n[agents.{agent}]\nmodel = "{model_id}"\n'
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith('['):
            break
        if re.match(r'\s*model\s*=', lines[i]):
            lines[i] = re.sub(r'''^(\s*model\s*=\s*)("[^"]*"|'[^']*')''', rf'\g<1>"{model_id}"', lines[i])
            break
    return ''.join(lines)


def _replace_verified(target, text, check):
    """Refuses unless the new text loads and passes ``check``; the file is swapped whole or not at all."""
    fd, name = tempfile.mkstemp(dir=target.parent, prefix='.siamang-', suffix='.toml')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.chmod(name, target.stat().st_mode & 0o777)
        try:
            if not check(load_config(name)):
                raise RuntimeRefusal('config_not_editable', 409)
        except ConfigError:
            raise RuntimeRefusal('config_not_editable', 409) from None
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def assign(path, agent, provider, provider_model):
    """Pin ``agent`` to a model the catalog offers. Anything else in the request is refused, never written."""
    if agent not in MODEL_AGENTS:
        raise RuntimeRefusal('invalid_agent', 422)
    with _write:
        config = _load(path)
        option = _options(config)[0].get((provider, provider_model))
        if option is None or not option['available']:
            raise RuntimeRefusal('model_not_available', 422)
        target = Path(path).expanduser()
        text = target.read_text(encoding='utf-8')
        model_id = option['model_id']
        if model_id is None:
            model_id = _new_id(config, provider, provider_model)
            text = (f'{text.rstrip()}\n\n[models.{model_id}]\nprovider = {json.dumps(provider)}\n'
                    f'provider_model = {json.dumps(provider_model)}\ncapabilities = {json.dumps(list(CAPABILITIES))}\n')
        others = {k: v for k, v in config.agents.items() if k != agent}

        def same_otherwise(after):
            pin, model = after.agents.get(agent), after.models.get(model_id)
            return (pin is not None and pin.model == model_id and model is not None and model.provider == provider
                    and (model.provider_model or model_id) == provider_model
                    and {k: v for k, v in after.agents.items() if k != agent} == others
                    and dict(after.providers) == dict(config.providers))

        _replace_verified(target, _pin(text, agent, model_id), same_otherwise)
    return dict(agent=agent, model_id=model_id, provider=provider, provider_model=provider_model)
