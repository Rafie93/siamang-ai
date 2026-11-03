"""Choosing an agent's model over HTTP: what is offered, what is written, what is refused."""
import hashlib
import tomllib

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.app import create_app
from siamang.api.settings import APISettings, Principal
from siamang.config import load_config
from siamang.models.definition import HealthStatus, ProviderHealth
from siamang.models.providers.ollama import OllamaProvider

TOKENS = {'owner': 'test-owner-credential-' + 'a' * 32, 'operator': 'test-operator-credential-' + 'b' * 32,
          'scoped': 'test-scoped-credential-' + 'c' * 32}
CONFIG = '''# top comment stays
[agents.qa] # why qa
model = "claude-sonnet"  # keep this note
parallel = 2

[agents.programmer]
model = "claude-sonnet"
parallel = 4

# a model
[models.claude-sonnet]
provider = "claude"
provider_model = "sonnet"
capabilities = ["text_generation", "structured_output"]

[models.ollama-local]
provider = "ollama"
provider_model = "qwen3:4b"
capabilities = ["text_generation", "structured_output"]

[providers.claude]
type = "claude_cli"

[providers.ollama]
type = "ollama"
base_url = "http://127.0.0.1:11434"

[providers.local]
type = "echo"

[models.echo-small]
provider = "local"
'''
INSTALLED = {'qwen3:4b', 'phi3:mini', 'gemini-3-flash-preview:cloud'}


def bearer(who='owner'):
    return {'Authorization': 'Bearer ' + TOKENS[who]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / 'siamang.toml'
    path.write_text(CONFIG)
    monkeypatch.setattr(OllamaProvider, '_health', lambda self: ProviderHealth(
        self.provider_id, HealthStatus.AVAILABLE, available_models=frozenset(INSTALLED)))
    grants = {'owner': ({'read', 'configure'}, {'*'}), 'operator': ({'read', 'submit', 'run', 'approve'}, {'*'}),
              'scoped': ({'read', 'configure'}, {'prj_one'})}
    principals = tuple(Principal(n, hashlib.sha256(TOKENS[n].encode()).hexdigest(), frozenset(p), frozenset(s))
                       for n, (p, s) in grants.items())
    settings = APISettings(tmp_path / 'siamang.db', principals, b'test-seal-material' * 3, str(path))
    with TestClient(create_app(settings)) as client:
        yield client, path


def choose(client, agent='qa', provider='ollama', provider_model='phi3:mini', who='owner'):
    return client.post('/v0/settings/models', headers=bearer(who),
                       json={'agent': agent, 'provider': provider, 'provider_model': provider_model})


def test_catalog_offers_configured_models_and_ollama_tags_but_not_echo(world):
    client, _ = world
    data = client.get('/v0/settings/models', headers=bearer()).json()
    offered = {(o['provider'], o['provider_model']): o for o in data['options']}
    assert set(offered) == {('claude', 'sonnet'), ('ollama', 'qwen3:4b'), ('ollama', 'phi3:mini'),
                            ('ollama', 'gemini-3-flash-preview:cloud')}
    assert offered['ollama', 'qwen3:4b']['model_id'] == 'ollama-local'
    assert offered['ollama', 'phi3:mini'] | {'model_id': None} == offered['ollama', 'phi3:mini']
    assert not offered['ollama', 'phi3:mini']['cloud']
    assert offered['ollama', 'gemini-3-flash-preview:cloud']['cloud'] and offered['claude', 'sonnet']['cloud']
    agents = {a['agent']: a for a in data['agents']}
    assert agents['qa']['provider_model'] == 'sonnet' and agents['lead']['model_id'] is None


def test_ollama_down_is_reported_and_its_models_cannot_be_chosen(world, monkeypatch):
    client, _ = world
    def down(self):
        raise OSError('refused')
    monkeypatch.setattr(OllamaProvider, '_health', down)
    data = client.get('/v0/settings/models', headers=bearer()).json()
    assert data['unreachable'] == ['ollama']
    assert [o['available'] for o in data['options'] if o['provider'] == 'ollama'] == [False]
    assert choose(client, provider_model='qwen3:4b').status_code == 422


def test_new_tag_adds_a_model_and_leaves_the_rest_of_the_file_alone(world):
    client, path = world
    assert choose(client).json()['model_id'] == 'ollama-phi3-mini'
    text = path.read_text()
    assert '[agents.qa] # why qa\nmodel = "ollama-phi3-mini"  # keep this note\nparallel = 2\n' in text
    assert text.startswith(CONFIG.split('model = "claude-sonnet"')[0])
    assert text[text.index('[agents.programmer]'):].startswith(CONFIG[CONFIG.index('[agents.programmer]'):])
    config = load_config(path)
    assert config.agents['qa'].parallel == 2 and config.agents['programmer'].model == 'claude-sonnet'
    assert config.models['ollama-phi3-mini'].provider_model == 'phi3:mini'
    assert config.models['ollama-phi3-mini'].capabilities == ('text_generation', 'structured_output')
    assert choose(client, agent='programmer').json()['model_id'] == 'ollama-phi3-mini'  # reused, not duplicated
    assert path.read_text().count('[models.ollama-phi3-mini]') == 1


def test_configured_model_is_reused_and_a_missing_agent_table_is_added(world):
    client, path = world
    before = path.read_text()
    assert choose(client, agent='lead', provider_model='qwen3:4b').json()['model_id'] == 'ollama-local'
    assert path.read_text() == before.rstrip() + '\n\n[agents.lead]\nmodel = "ollama-local"\n'


def test_the_choice_is_what_capabilities_report_without_a_restart(world):
    client, _ = world
    choose(client, provider_model='qwen3:4b')
    qa = client.get('/v0/capabilities', headers=bearer()).json()['agent_models']['qa']
    assert qa == {'model_id': 'ollama-local', 'provider_model': 'qwen3:4b', 'provider_type': 'ollama'}


@pytest.mark.parametrize('body', [
    dict(provider_model='not-installed'),
    dict(provider_model='x"\n[providers.evil]\ntype = "echo'),
    dict(provider='local', provider_model='echo-small'),
    dict(provider='nope'),
    dict(agent='root'),
])
def test_anything_not_offered_is_refused_and_writes_nothing(world, body):
    client, path = world
    assert choose(client, **body).status_code == 422
    assert path.read_text() == CONFIG


@pytest.mark.parametrize('who', ['operator', 'scoped'])
def test_changing_a_model_needs_configure_and_the_whole_scope(world, who):
    client, path = world
    assert choose(client, who=who).status_code == 403
    assert path.read_text() == CONFIG


def test_an_unusual_file_is_refused_rather_than_damaged(world):
    client, path = world
    odd = CONFIG.replace('[agents.qa] # why qa\nmodel = "claude-sonnet"  # keep this note\nparallel = 2\n',
                         '[agents]\nqa = { model = "claude-sonnet" }\n')
    path.write_text(odd)
    assert choose(client, provider_model='qwen3:4b').json() == {'error': {'code': 'config_not_editable'}}
    assert path.read_text() == odd
    assert [p.name for p in path.parent.glob('.siamang-*')] == []


def test_no_config_file_means_nothing_to_edit(tmp_path):
    principal = Principal('owner', hashlib.sha256(TOKENS['owner'].encode()).hexdigest(),
                          frozenset({'read', 'configure'}), frozenset({'*'}))
    with TestClient(create_app(APISettings(tmp_path / 'siamang.db', (principal,), b'test-seal-material' * 3))) as client:
        assert client.get('/v0/settings/models', headers=bearer()).status_code == 409
        assert choose(client).status_code == 409


def test_written_file_stays_valid_toml(world):
    client, path = world
    choose(client, provider_model='gemini-3-flash-preview:cloud')
    data = tomllib.loads(path.read_text())
    assert data['models']['ollama-gemini-3-flash-preview-cloud']['provider_model'] == 'gemini-3-flash-preview:cloud'
