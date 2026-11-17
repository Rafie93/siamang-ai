"""Connecting GitHub and logging in to Claude over HTTP: who may, and that no token ever comes back."""
import hashlib
import logging

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from siamang.api.accounts import Accounts
from siamang.api.app import create_app
from siamang.accounts import AccountError
from siamang.api.settings import APISettings, Principal

TOKENS = {'owner': 'test-owner-credential-' + 'a' * 32, 'reader': 'test-reader-credential-' + 'b' * 32,
          'scoped': 'test-scoped-credential-' + 'c' * 32}
GITHUB = 'ghp_' + 'z' * 36


def bearer(who='owner'):
    return {'Authorization': 'Bearer ' + TOKENS[who]}


class FakeGitHub:
    def __init__(self):
        self.token = None

    def status(self):
        return {'installed': True, 'connected': self.token is not None, 'stored': self.token is not None,
                'source': 'siamang' if self.token else None, 'login': 'rafie' if self.token else None, 'name': None}

    def connect(self, token):
        if token != GITHUB:
            raise AccountError('github_token_invalid', 422)
        self.token = token
        return self.status()

    def disconnect(self):
        self.token = None
        return self.status()


class FakeClaude:
    def __init__(self):
        self.started = 0

    def status(self):
        return {'installed': True, 'logged_in': False, 'method': None, 'email': None, 'plan': None,
                'expires_at': None, 'login': {'state': 'waiting' if self.started else 'idle', 'url': None}}

    def start_login(self):
        if self.started:
            raise AccountError('claude_login_running', 409)
        self.started += 1
        return self.status()

    def submit_code(self, code):
        if not self.started:
            raise AccountError('claude_login_not_running', 409)
        self.code = code
        return self.status()

    def close(self):
        pass


@pytest.fixture
def world(tmp_path):
    grants = {'owner': ({'read', 'configure'}, {'*'}), 'reader': ({'read'}, {'*'}),
              'scoped': ({'read', 'configure'}, {'prj_one'})}
    principals = tuple(Principal(n, hashlib.sha256(TOKENS[n].encode()).hexdigest(), frozenset(p), frozenset(s))
                       for n, (p, s) in grants.items())
    settings = APISettings(tmp_path / 'siamang.db', principals, b'test-seal-material' * 3, None)
    accounts = Accounts(FakeGitHub(), FakeClaude())
    with TestClient(create_app(settings, accounts=accounts)) as client:
        yield client, accounts


def test_owner_connects_and_disconnects_github_and_the_token_never_comes_back(world, caplog):
    client, accounts = world
    caplog.set_level(logging.DEBUG)
    assert client.get('/v0/settings/github', headers=bearer('reader')).json()['connected'] is False
    done = client.post('/v0/settings/github', headers=bearer(), json={'token': GITHUB})
    assert done.status_code == 200 and done.json()['login'] == 'rafie'
    assert GITHUB not in done.text and GITHUB not in caplog.text
    assert client.post('/v0/settings/github/disconnect', headers=bearer()).json()['connected'] is False


def test_a_wrong_token_is_refused_without_echoing_it(world):
    client, _ = world
    wrong = 'ghp_' + 'q' * 36
    refused = client.post('/v0/settings/github', headers=bearer(), json={'token': wrong})
    assert refused.status_code == 422 and refused.json() == {'error': {'code': 'github_token_invalid'}}
    invalid = client.post('/v0/settings/github', headers=bearer(), json={'token': 'x' * 400})
    assert invalid.status_code == 422 and 'xxxx' not in invalid.text


@pytest.mark.parametrize('who,status', [('reader', 403), ('scoped', 403)])
def test_only_an_owner_of_every_project_may_change_accounts(world, who, status):
    client, accounts = world
    assert client.post('/v0/settings/github', headers=bearer(who), json={'token': GITHUB}).status_code == status
    assert client.post('/v0/settings/github/disconnect', headers=bearer(who)).status_code == status
    assert client.post('/v0/settings/claude/login', headers=bearer(who)).status_code == status
    assert client.post('/v0/settings/claude/code', headers=bearer(who), json={'code': 'abcdefgh#x'}).status_code == status
    assert accounts.github.token is None and accounts.claude.started == 0


def test_claude_login_starts_once(world):
    client, _ = world
    assert client.get('/v0/settings/claude', headers=bearer('reader')).json()['login']['state'] == 'idle'
    assert client.post('/v0/settings/claude/login', headers=bearer()).json()['login']['state'] == 'waiting'
    again = client.post('/v0/settings/claude/login', headers=bearer())
    assert again.status_code == 409 and again.json()['error']['code'] == 'claude_login_running'


def test_accounts_need_a_bearer(world):
    client, _ = world
    assert client.get('/v0/settings/github').status_code == 401
    assert client.get('/v0/settings/claude').status_code == 401


def test_the_login_code_goes_to_the_waiting_login_and_is_not_echoed(world):
    client, accounts = world
    code = 'secret-code-123#state'
    refused = client.post('/v0/settings/claude/code', headers=bearer(), json={'code': code})
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'claude_login_not_running'
    client.post('/v0/settings/claude/login', headers=bearer())
    done = client.post('/v0/settings/claude/code', headers=bearer(), json={'code': code})
    assert done.status_code == 200 and accounts.claude.code == code and code not in done.text
