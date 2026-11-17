"""Claude: the shared ``claude`` CLI subscription login, the one the claude_cli provider already uses.

Siamang starts ``claude auth login`` on the owner's machine and reports ``claude auth status``. It never logs
out, because that login also belongs to the owner's own Claude Code.
"""
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .common import BASE_ENV, STATUS_TIMEOUT, text
from .errors import AccountError

URL_RE = re.compile(r'https://(?:[A-Za-z0-9-]+\.)*(?:claude\.ai|claude\.com|anthropic\.com)/[^\s"\'<>]{1,2000}')
LOGIN_TIMEOUT = 300
# The code claude.ai shows after login ("Tempel ini ke Claude Code"): token characters and '#', one line.
CODE_RE = re.compile(r'[A-Za-z0-9._~#-]{8,1000}')


class ClaudeAccount:
    def __init__(self, timeout=LOGIN_TIMEOUT):
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc = None
        self._login = {'state': 'idle', 'url': None}

    @staticmethod
    def _env():
        return {k: os.environ[k] for k in BASE_ENV + ('CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME') if k in os.environ}

    @staticmethod
    def _binary(env):
        found = shutil.which('claude', path=env.get('PATH'))
        if found:
            return found
        fallback = Path.home() / '.local' / 'bin' / 'claude'
        return str(fallback) if fallback.is_file() else None

    def _login_view(self):
        with self._lock:
            return dict(self._login)

    def status(self):
        view = {'installed': False, 'logged_in': False, 'method': None, 'email': None, 'plan': None,
                'expires_at': None, 'login': self._login_view()}
        env = self._env()
        program = self._binary(env)
        if program is None:
            return view
        view['installed'] = True
        try:
            done = subprocess.run([program, 'auth', 'status'], capture_output=True, encoding='utf-8', errors='replace',
                                  shell=False, check=False, env=env, timeout=STATUS_TIMEOUT, stdin=subprocess.DEVNULL)
            data = json.loads(done.stdout)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return view
        if not isinstance(data, dict):
            return view
        return view | {'logged_in': data.get('loggedIn') is True, 'method': text(data.get('authMethod'), 40),
                       'email': text(data.get('email')), 'plan': text(data.get('subscriptionType'), 40),
                       'expires_at': text(data.get('loginExpiresAt'), 40)}

    def start_login(self):
        env = self._env()
        program = self._binary(env)
        if program is None:
            raise AccountError('claude_not_installed', 503)
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise AccountError('claude_login_running', 409)
            try:
                # stdin stays open and silent: the CLI may wait there for a pasted code instead of giving up.
                proc = subprocess.Popen([program, 'auth', 'login', '--claudeai'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                                        errors='replace', shell=False, env=env, start_new_session=True)
            except OSError:
                raise AccountError('claude_not_installed', 503) from None
            self._proc, self._login = proc, {'state': 'waiting', 'url': None}
        threading.Thread(target=self._watch, args=(proc,), daemon=True).start()
        return self.status()

    def submit_code(self, code):
        """Types the code claude.ai shows into the waiting ``claude auth login``, as the owner would in a terminal."""
        code = code.strip() if isinstance(code, str) else ''
        if not CODE_RE.fullmatch(code):
            raise AccountError('claude_code_invalid', 422)
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None or proc.stdin.closed:
                raise AccountError('claude_login_not_running', 409)
            try:
                proc.stdin.write(code + '\n')
                proc.stdin.flush()
            except (OSError, ValueError):
                raise AccountError('claude_login_not_running', 409) from None
        return self.status()

    def _watch(self, proc):
        timer = threading.Timer(self._timeout, proc.kill)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                match = URL_RE.search(line)
                if match:
                    with self._lock:
                        if self._proc is proc and not self._login['url']:
                            self._login['url'] = match.group(0)
            code = proc.wait()
        finally:
            timer.cancel()
            with self._lock:  # submit_code writes under the same lock
                for stream in (proc.stdin, proc.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass
        with self._lock:
            if self._proc is proc:
                self._login = {'state': 'done' if code == 0 else 'failed', 'url': None}

    def close(self):
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
