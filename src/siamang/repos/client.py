"""Write access to a project's GitHub repository: a fixed set of operations, nothing else.

The read-only :mod:`siamang.git.client` stays as it is. This is the second Git boundary:

* argument lists only, ``shell=False``, a timeout on every call, a fixed child environment;
* user and system Git config are ignored, hooks and fsmonitor are off, and only the
  configured transport is allowed;
* credentials come from ``gh`` per call; the user's Git config is never changed;
* **pushes only reach ``siamang/*`` branches**, and never the default branch, with one exception:
  the first commit of a repository that has no branch at all. A pull request needs a base, and
  there is nothing to overwrite. Nothing here merges, force-pushes, deletes a remote branch, or
  changes repository settings;
* errors carry a fixed code. Git and gh output is never repeated, as it can hold credentials.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}')
BRANCH_RE = re.compile(r'siamang/[a-z0-9][a-z0-9-]{0,80}')
# Default branches come from GitHub and reach `git fetch` as data: no options, no refspec syntax.
BASE_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,200}')
ENV_ALLOWLIST = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'GH_CONFIG_DIR', 'XDG_CONFIG_HOME')
CLONE_TIMEOUT = 300
PUSH_TIMEOUT = 180
TIMEOUT = 60


class RepoError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RepoInfo:
    repository: str
    default_branch: str | None  # None: the repository has no commit yet
    private: bool
    can_write: bool


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str  # OPEN, CLOSED, MERGED


def _check_repository(repository):
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository) or repository.endswith('.git'):
        raise RepoError('invalid_repository')


def _check_branch(branch):
    if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch):
        raise RepoError('invalid_branch')


def _check_base(base):
    if not isinstance(base, str) or not BASE_RE.fullmatch(base) or '..' in base:
        raise RepoError('invalid_branch')


class RepoClient:
    def __init__(self, *, remote='https://github.com/{}.git', protocol='https'):
        self._remote, self._protocol = remote, protocol
        self._fetch_guard, self._fetch_locks = threading.Lock(), {}

    def _env(self):
        env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
        return env | {'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                      'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1'}

    def _run(self, argv, *, cwd=None, timeout=TIMEOUT, code='git_failed'):
        env = self._env()
        program = shutil.which(argv[0], path=env.get('PATH'))
        if program is None:
            raise RepoError(f'{argv[0]}_not_installed')
        try:
            done = subprocess.run([program, *argv[1:]], capture_output=True, encoding='utf-8', errors='replace',
                                  shell=False, check=False, cwd=cwd, env=env, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RepoError('repository_timeout') from None
        except OSError:
            raise RepoError(code) from None
        if done.returncode != 0:
            raise RepoError(code)
        return done.stdout

    def _git(self, *args, cwd=None, timeout=TIMEOUT, code='git_failed'):
        options = ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never',
                   '-c', f'protocol.{self._protocol}.allow=always', '-c', 'credential.helper=',
                   '-c', 'credential.helper=!gh auth git-credential', '-c', 'commit.gpgsign=false']
        return self._run(['git', *options, *args], cwd=cwd, timeout=timeout, code=code)

    def _gh_json(self, *args, code):
        try:
            return json.loads(self._run(['gh', *args], code=code))
        except ValueError:
            raise RepoError(code) from None

    # -- repository ------------------------------------------------------------

    def info(self, repository) -> RepoInfo:
        _check_repository(repository)
        data = self._gh_json('repo', 'view', repository, '--json',
                             'nameWithOwner,defaultBranchRef,visibility,viewerPermission', code='repository_not_found')
        branch = (data.get('defaultBranchRef') or {}).get('name') or None
        return RepoInfo(data.get('nameWithOwner') or repository, branch, data.get('visibility') != 'PUBLIC',
                        data.get('viewerPermission') in ('WRITE', 'MAINTAIN', 'ADMIN'))

    def clone(self, repository, dest: Path):
        _check_repository(repository)
        self._git('clone', '--no-tags', '--', self._remote.format(repository), str(dest),
                  timeout=CLONE_TIMEOUT, code='clone_failed')

    def seed_empty(self, root, title, *, author_name, author_email, branch='main'):
        """The base a pull request needs, only while the remote truly has no branch. Never a force."""
        _check_base(branch)
        if self._git('ls-remote', '--heads', 'origin', cwd=root, code='fetch_failed').strip():
            raise RepoError('repository_not_empty')
        Path(root, 'README.md').write_text(f'# {title}\n', encoding='utf-8')
        self._git('symbolic-ref', 'HEAD', f'refs/heads/{branch}', cwd=root)
        self._git('add', '--', 'README.md', cwd=root)
        self._git('-c', f'user.name={author_name}', '-c', f'user.email={author_email}',
                  'commit', '--no-verify', '-m', 'Initial commit', cwd=root, code='commit_failed')
        self._git('push', '--no-verify', 'origin', f'refs/heads/{branch}:refs/heads/{branch}',
                  cwd=root, timeout=PUSH_TIMEOUT, code='push_failed')

    def files(self, root, limit=300):
        return self._git('ls-files', '-z', cwd=root).split('\0')[:-1][:limit]

    def _fetch(self, root, code):
        # A task and the Lead share one clone, and two fetches at once can fail on a ref lock.
        with self._fetch_guard:
            lock = self._fetch_locks.setdefault(str(root), threading.Lock())
        with lock:
            self._git('fetch', '--prune', '--no-tags', 'origin', cwd=root, timeout=PUSH_TIMEOUT, code=code)

    def refresh(self, root) -> bool:
        """Update what the clone knows of the remote. Only remote refs move: the checkout a task uses stays."""
        try:
            self._fetch(root, 'fetch_failed')
        except RepoError:
            return False
        return True

    def revision(self, root, branch) -> str | None:
        """The commit of a remote branch as last fetched, or None when the clone has no such branch."""
        _check_base(branch)
        try:
            return self._git('rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}^{{commit}}', cwd=root).strip()
        except RepoError:
            return None

    def tree(self, root, commit, limit=300) -> dict[str, str]:
        """Each file of a commit with its Git mode, without touching the checkout."""
        entries = {}
        for line in self._git('ls-tree', '-r', '-z', commit, cwd=root).split('\0')[:-1]:
            meta, name = line.split('\t', 1)
            mode, kind, _ = meta.split(' ')
            if kind == 'blob':
                entries[name] = mode
        return dict(list(entries.items())[:limit])

    def blob(self, root, commit, name) -> str:
        return self._git('cat-file', 'blob', f'{commit}:{name}', cwd=root)

    # -- branches --------------------------------------------------------------

    def prepare(self, root, branch, base):
        """Put the managed clone exactly on ``branch``: its remote state, else fresh from ``base``.

        Discards local state, so callers must only pass a clone that Siamang owns.
        """
        _check_branch(branch)
        _check_base(base)
        self._fetch(root, 'fetch_failed')
        refs = self._git('for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin', cwd=root).split()
        start = f'origin/{branch}' if f'origin/{branch}' in refs else f'origin/{base}'
        if start not in refs:
            raise RepoError('base_branch_missing')
        self._git('checkout', '--force', '-B', branch, start, '--', cwd=root, code='checkout_failed')
        self._git('reset', '--hard', start, cwd=root, code='checkout_failed')
        self._git('clean', '-fdx', cwd=root, code='checkout_failed')

    def commit_paths(self, root, branch, *, base, paths, message, author_name, author_email):
        _check_branch(branch)
        _check_base(base)
        if branch == base or not paths:
            raise RepoError('nothing_to_publish' if not paths else 'refused_default_branch')
        current = self._git('rev-parse', '--abbrev-ref', 'HEAD', cwd=root).strip()
        if current != branch:
            raise RepoError('refused_wrong_branch')
        self._git('add', '--all', '--', *paths, cwd=root)
        if not self._git('diff', '--cached', '--name-only', cwd=root).strip():
            raise RepoError('nothing_to_publish')
        self._git('-c', f'user.name={author_name}', '-c', f'user.email={author_email}',
                  'commit', '--no-verify', '-m', message, cwd=root, code='commit_failed')

    def commit_and_push(self, root, branch, *, base, paths, message, author_name, author_email) -> str:
        self.commit_paths(root, branch, base=base, paths=paths, message=message, author_name=author_name, author_email=author_email)
        return self.push(root, branch)

    def push(self, root, branch) -> str:
        _check_branch(branch)
        # An explicit refspec, never a force: a moved remote branch fails instead of being overwritten.
        self._git('push', '--no-verify', 'origin', f'refs/heads/{branch}:refs/heads/{branch}',
                  cwd=root, timeout=PUSH_TIMEOUT, code='push_failed')
        return self._git('rev-parse', 'HEAD', cwd=root).strip()

    # -- continuous integration: read only --------------------------------------

    def ci_runs(self, repository, branch, commit_sha):
        """The workflow runs of one commit, with their jobs and what they uploaded."""
        _check_repository(repository)
        _check_branch(branch)
        rows = self._gh_json('run', 'list', '--repo', repository, '--branch', branch, '--limit', '20', '--json',
                             'databaseId,name,status,conclusion,url,headSha', code='ci_lookup_failed')
        out = []
        for row in rows if isinstance(rows, list) else ():
            if row.get('headSha') != commit_sha or type(row.get('databaseId')) is not int:
                continue
            run_id = row['databaseId']
            view = self._gh_json('run', 'view', str(run_id), '--repo', repository, '--json', 'jobs', code='ci_lookup_failed')
            found = self._gh_json('api', f'repos/{repository}/actions/runs/{run_id}/artifacts', code='ci_lookup_failed')
            out.append(dict(id=run_id, name=row.get('name'), status=row.get('status'), conclusion=row.get('conclusion'),
                            url=row.get('url'),
                            jobs=[dict(name=j.get('name'), status=j.get('status'), conclusion=j.get('conclusion'))
                                  for j in view.get('jobs') or ()],
                            artifacts=[dict(name=a.get('name'), size=a.get('size_in_bytes'), expired=a.get('expired'))
                                       for a in found.get('artifacts') or ()]))
        return out

    def ci_failed_log(self, repository, run_id, limit=6_000):
        _check_repository(repository)
        if type(run_id) is not int:
            raise RepoError('ci_lookup_failed')
        return self._run(['gh', 'run', 'view', str(run_id), '--repo', repository, '--log-failed'],
                         timeout=PUSH_TIMEOUT, code='ci_lookup_failed')[-limit:]

    # -- pull requests ---------------------------------------------------------

    def identity(self):
        data = self._gh_json('api', 'user', code='github_identity_failed')
        login, uid = data.get('login'), data.get('id')
        if not isinstance(login, str) or type(uid) is not int:
            raise RepoError('github_identity_failed')
        return login, f'{uid}+{login}@users.noreply.github.com'

    def find_pull_request(self, repository, branch) -> PullRequest | None:
        _check_repository(repository)
        _check_branch(branch)
        rows = self._gh_json('pr', 'list', '--repo', repository, '--head', branch, '--state', 'all', '--limit', '1',
                             '--json', 'number,url,state', code='pull_request_lookup_failed')
        if not isinstance(rows, list) or not rows:
            return None
        return PullRequest(rows[0]['number'], rows[0]['url'], rows[0]['state'])

    def open_pull_request(self, repository, branch, *, base, title, body) -> PullRequest:
        _check_repository(repository)
        _check_branch(branch)
        _check_base(base)
        self._run(['gh', 'pr', 'create', '--repo', repository, '--base', base, '--head', branch,
                   '--title', title, '--body', body], timeout=PUSH_TIMEOUT, code='pull_request_failed')
        found = self.find_pull_request(repository, branch)
        if found is None:
            raise RepoError('pull_request_failed')
        return found
