import hashlib
import json
import pytest

from siamang.api.settings import APISettings, Principal
from siamang.api.codec import decode
from siamang.jsondata import freeze, thaw


def principal(**kwargs):
    return Principal(**({'identity': 'rafie', 'token_sha256': hashlib.sha256(b'test-token' * 8).hexdigest(),
                         'permissions': {'read'}, 'projects': {'prj_one'}} | kwargs))


@pytest.mark.parametrize('change', [{'identity': '*'}, {'token_sha256': 'plain secret'},
                                  {'permissions': {'shell'}}, {'projects': {'/tmp/project'}}])
def test_invalid_principals_fail_closed(change):
    with pytest.raises(ValueError):
        principal(**change)


@pytest.mark.parametrize('change', [{'seal_key': b'short'}, {'principals': ()}, {'origins': ('*',)},
                                  {'origins': ('http://localhost:5173/path',)}])
def test_settings_refuse_unsafe_defaults(tmp_path, change):
    args = {'database': tmp_path / 'db', 'principals': (principal(),), 'seal_key': b'k' * 32} | change
    with pytest.raises(ValueError):
        APISettings(**args)


def test_authentication_and_server_owned_scopes(tmp_path):
    p = principal()
    settings = APISettings(tmp_path / 'db', (p,), b'k' * 32)
    assert settings.authenticate('test-token' * 8) is p
    for token in ['', 'x' * 31, 'test-token' * 7, 'x' * 513]:
        assert settings.authenticate(token) is None
    assert p.allows_project('prj_one') and not p.allows_project('prj_other')
    assert 'test-token' not in repr(settings) and str(settings.seal_key) not in repr(settings)


def test_empty_environment_does_not_start_unauthenticated(monkeypatch):
    with pytest.raises(ValueError, match='invalid API configuration'):
        APISettings.from_env()


def test_codec_never_imports_or_calls_arbitrary_names():
    for hostile in [{'type': 'os.system', 'fields': {'command': 'id'}}, {'type': 'Plan', 'fields': {}},
                    {'__reduce__': 'run'}]:
        with pytest.raises(ValueError):
            decode(hostile)


def test_nested_json_is_immutable_and_serializes_without_changing_values():
    data = {'a': [{'b': [1, 2, 3]}]}
    frozen = freeze(data)
    data['a'][0]['b'].append(4)
    with pytest.raises(TypeError):
        frozen['a'][0]['b'][0] = 9
    assert thaw(frozen) == {'a': [{'b': [1, 2, 3]}]}
