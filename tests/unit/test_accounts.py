"""Accounts connected from Settings: a GitHub token in Siamang's own file, and the shared Claude login."""
import json
import os
import stat
import time

import pytest

from siamang.accounts import AccountError, ClaudeAccount, GitHubAccount
from siamang.repos.account import read_token
from siamang.repos.client import RepoClient

GOOD = 'ghp_' + 'a' * 36
BAD = 'ghp_' + 'b' * 36


def script(folder, name, body):
    path = folder / name
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    folder = tmp_path / 'bin'
    folder.mkdir()
    monkeypatch.setenv('PATH', f'{folder}:/usr/bin:/bin')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return folder


@pytest.fixture
def gh(bin_dir, tmp_path):
    log = tmp_path / 'gh.log'
    script(bin_dir, 'gh', f'''echo "$@" >> {log}
if [ "$GH_TOKEN" = "{GOOD}" ]; then echo '{{"login":"rafie","name":"Rafie","email":"x"}}'; exit 0; fi
if [ -z "$GH_TOKEN" ] && [ -f {tmp_path}/global-login ]; then echo '{{"login":"global-user","name":null}}'; exit 0; fi
exit 1
''')
    return log


def test_connect_stores_token_privately_and_only_on_success(gh, tmp_path):
    file = tmp_path / 'cfg' / 'github-token'
    account = GitHubAccount(token_file=file)
    with pytest.raises(AccountError) as refused:
        account.connect(BAD)
    assert refused.value.code == 'github_token_invalid' and not file.exists()

    view = account.connect('  ' + GOOD + '\n')
    assert view == {'installed': True, 'connected': True, 'stored': True, 'source': 'siamang', 'login': 'rafie',
                    'name': 'Rafie'}
    assert read_token(file) == GOOD
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert GOOD not in gh.read_text()  # the token reaches gh only through its environment
    assert GOOD not in json.dumps(view)


@pytest.mark.parametrize('token', ['', 'hello', 'ghp_short', 'ghp_' + 'a' * 36 + ' x', 'sk-ant-' + 'a' * 40])
def test_connect_refuses_what_is_not_a_github_token_without_running_gh(gh, tmp_path, token):
    with pytest.raises(AccountError) as refused:
        GitHubAccount(token_file=tmp_path / 't').connect(token)
    assert refused.value.code == 'github_token_invalid'
    assert not gh.exists()


def test_status_falls_back_to_the_owners_own_gh_and_disconnect_returns_there(gh, tmp_path):
    file = tmp_path / 'github-token'
    account = GitHubAccount(token_file=file)
    assert account.status()['connected'] is False
    (tmp_path / 'global-login').touch()
    assert account.status() == {'installed': True, 'connected': True, 'stored': False, 'source': 'gh',
                                     'login': 'global-user', 'name': None}
    account.connect(GOOD)
    assert account.status()['source'] == 'siamang'
    view = account.disconnect()
    assert not file.exists() and view['source'] == 'gh'


def test_a_revoked_stored_token_shows_as_stored_but_not_connected(gh, tmp_path):
    file = tmp_path / 'github-token'
    file.write_text(BAD + '\n')
    view = GitHubAccount(token_file=file).status()
    assert view['stored'] is True and view['connected'] is False


def test_status_without_gh_says_not_installed(bin_dir, tmp_path):
    assert GitHubAccount(token_file=tmp_path / 't').status()['installed'] is False


def test_repo_client_hands_the_stored_token_to_gh_and_git_only_when_there_is_one(tmp_path, monkeypatch):
    file = tmp_path / 'github-token'
    monkeypatch.delenv('GH_TOKEN', raising=False)
    client = RepoClient(token_file=file)
    assert 'GH_TOKEN' not in client._env()
    file.write_text(GOOD + '\n')
    assert client._env()['GH_TOKEN'] == GOOD
    file.write_text('not a token\n')
    assert 'GH_TOKEN' not in client._env()


def test_repo_client_never_inherits_a_caller_gh_token(tmp_path, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', BAD)
    assert 'GH_TOKEN' not in RepoClient(token_file=tmp_path / 'none')._env()


# -- Claude --------------------------------------------------------------------

STATUS = {'loggedIn': True, 'authMethod': 'claude.ai', 'email': 'owner@example.com', 'subscriptionType': 'pro',
          'loginExpiresAt': '2026-12-01T00:00:00Z', 'orgId': 'secret-org', 'configDirectory': '/Users/x/.claude'}


@pytest.fixture
def claude(bin_dir, tmp_path):
    control = tmp_path / 'login-mode'
    control.write_text('ok')
    script(bin_dir, 'claude', f'''if [ "$1 $2" = "auth status" ]; then echo '{json.dumps(STATUS)}'; exit 0; fi
if [ "$1 $2" = "auth login" ]; then
  echo "Opening browser to sign in"
  echo "If the browser didn't open, visit: https://claude.ai/oauth/authorize?code=true&state=abc"
  mode=$(cat {control})
  if [ "$mode" = "hang" ]; then exec sleep 30; fi
  if [ "$mode" = "fail" ]; then exit 1; fi
  if [ "$mode" = "code" ]; then
    printf "Paste code here if prompted > "
    read pasted
    if [ "$pasted" = "good-code#state123" ]; then exit 0; fi
    exit 1
  fi
  exit 0
fi
exit 2
''')
    return control


def wait_for(account, state, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        view = account.status()
        if view['login']['state'] == state:
            return view
        time.sleep(0.05)
    raise AssertionError(f'login never reached {state}: {account.status()}')


def test_claude_status_passes_only_the_fields_settings_shows(claude):
    view = ClaudeAccount().status()
    assert view == {'installed': True, 'logged_in': True, 'method': 'claude.ai', 'email': 'owner@example.com',
                    'plan': 'pro', 'expires_at': '2026-12-01T00:00:00Z', 'login': {'state': 'idle', 'url': None}}


def test_claude_not_installed(bin_dir):
    account = ClaudeAccount()
    assert account.status()['installed'] is False
    with pytest.raises(AccountError) as refused:
        account.start_login()
    assert refused.value.code == 'claude_not_installed'


def test_claude_login_finishes_and_reports_done(claude):
    account = ClaudeAccount()
    assert account.start_login()['login']['state'] in ('waiting', 'done')
    assert wait_for(account, 'done')['login'] == {'state': 'done', 'url': None}


def test_claude_login_shows_the_fallback_url_and_refuses_a_second_login(claude):
    claude.write_text('hang')
    account = ClaudeAccount(timeout=1)
    account.start_login()
    deadline = time.monotonic() + 5
    while not account.status()['login']['url'] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert account.status()['login']['url'] == 'https://claude.ai/oauth/authorize?code=true&state=abc'
    with pytest.raises(AccountError) as refused:
        account.start_login()
    assert refused.value.code == 'claude_login_running'
    assert wait_for(account, 'failed')['login']['url'] is None  # killed after the timeout


def test_claude_login_failure_is_reported(claude):
    claude.write_text('fail')
    account = ClaudeAccount()
    account.start_login()
    wait_for(account, 'failed')


def test_claude_login_never_sees_an_api_key(claude, bin_dir, tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-should-not-pass')
    assert 'ANTHROPIC_API_KEY' not in ClaudeAccount._env()
    assert os.environ['ANTHROPIC_API_KEY']


def test_the_pasted_code_reaches_the_waiting_login_and_finishes_it(claude):
    claude.write_text('code')
    account = ClaudeAccount()
    account.start_login()
    assert account.submit_code('  good-code#state123\n')['login']['state'] in ('waiting', 'done')
    wait_for(account, 'done')


def test_a_wrong_code_fails_the_login(claude):
    claude.write_text('code')
    account = ClaudeAccount()
    account.start_login()
    account.submit_code('wrong-code#state123')
    wait_for(account, 'failed')


@pytest.mark.parametrize('code', ['', 'short', 'has space inside', 'two\nlines-here', 'x' * 1001, None])
def test_codes_that_cannot_be_one_are_refused_before_anything_is_typed(claude, code):
    claude.write_text('code')
    account = ClaudeAccount()
    account.start_login()
    with pytest.raises(AccountError) as refused:
        account.submit_code(code)
    assert refused.value.code == 'claude_code_invalid'
    account.close()


def test_a_code_without_a_waiting_login_is_refused(claude):
    with pytest.raises(AccountError) as refused:
        ClaudeAccount().submit_code('good-code#state123')
    assert refused.value.code == 'claude_login_not_running'
