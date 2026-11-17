"""GitHub: a token pasted once, kept by :mod:`siamang.repos.account`, checked with ``gh api user``.

The owner's own gh login (its config folder and the system keychain) is never written or logged out.
"""
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from siamang.repos.account import TOKEN_RE, forget_token, read_token, token_path, write_token

from .common import BASE_ENV, STATUS_TIMEOUT, text
from .errors import AccountError

LOGIN_RE = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})')


class GitHubAccount:
    def __init__(self, token_file=None, timeout=STATUS_TIMEOUT):
        self._file, self._timeout = token_file, timeout
        self._lock = threading.Lock()

    @property
    def token_file(self):
        return Path(self._file) if self._file else token_path()

    def _user(self, token):
        """``{'login', 'name'}`` of whoever gh is logged in as (``token`` or the owner's own gh), or None."""
        env = {k: os.environ[k] for k in BASE_ENV + ('GH_CONFIG_DIR', 'XDG_CONFIG_HOME') if k in os.environ}
        env |= {'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1'}
        if token:
            env['GH_TOKEN'] = token
        program = shutil.which('gh', path=env.get('PATH'))
        if program is None:
            raise AccountError('gh_not_installed', 503)
        try:
            done = subprocess.run([program, 'api', 'user'], capture_output=True, encoding='utf-8', errors='replace',
                                  shell=False, check=False, env=env, timeout=self._timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise AccountError('repository_timeout', 504) from None
        except OSError:
            raise AccountError('gh_not_installed', 503) from None
        if done.returncode != 0:
            return None
        try:
            data = json.loads(done.stdout)
        except ValueError:
            return None
        login = data.get('login') if isinstance(data, dict) else None
        if not isinstance(login, str) or not LOGIN_RE.fullmatch(login):
            return None
        return {'login': login, 'name': text(data.get('name'))}

    def status(self):
        token = read_token(self.token_file)
        view = {'installed': True, 'connected': False, 'stored': token is not None, 'source': None, 'login': None,
                'name': None}
        try:
            user = self._user(token)
        except AccountError as refusal:
            if refusal.code != 'gh_not_installed':
                raise
            return view | {'installed': False}
        if user:
            view |= user | {'connected': True, 'source': 'siamang' if token else 'gh'}
        return view

    def connect(self, token):
        token = token.strip() if isinstance(token, str) else ''
        if not TOKEN_RE.fullmatch(token):
            raise AccountError('github_token_invalid', 422)
        with self._lock:
            if self._user(token) is None:  # checked before anything is written
                raise AccountError('github_token_invalid', 422)
            write_token(token, self.token_file)
        return self.status()

    def disconnect(self):
        with self._lock:
            forget_token(self.token_file)
        return self.status()
