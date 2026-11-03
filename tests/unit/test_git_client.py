"""GitClient: structured argv, read-only allowlist, controlled env, timeout, normalized errors."""

import os
import subprocess

import pytest

from siamang.git import client as client_mod
from siamang.git import (
    GitClient,
    GitCommandError,
    GitErrorCode,
    GitNotInstalledError,
    GitPermissionError,
    GitTimeoutError,
    InvalidGitArgumentError,
    InvalidRepositoryError,
    NotRepositoryError,
    RepositoryLockedError,
    UnexpectedGitError,
    UnsupportedGitVersionError,
)
from siamang.git.client import CHILD_ENV, GLOBAL_OPTIONS, MIN_GIT_VERSION, parse_version, validate_args
from siamang.git.errors import classify_failure, scrub
from tests.support.git import make_repo


class FakeRun:
    """Stands in for subprocess.run; records every call."""

    def __init__(self, stdout=b"", returncode=0, stderr=b"", raises=None, version=b"git version 2.51.2\n"):
        self.calls = []
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.raises = raises
        self.version = version

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[-1] == "version":
            return subprocess.CompletedProcess(argv, 0, self.version, b"")
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeRun(**kw)
        monkeypatch.setattr(client_mod.subprocess, "run", f)
        return f

    return install


def client():
    return GitClient(binary="/usr/bin/git-fake")


# --- allowlist -------------------------------------------------------------------


@pytest.mark.parametrize("args", [
    ["push"], ["push", "origin", "main"], ["pull"], ["fetch"], ["fetch", "--all"], ["clone", "x"],
    ["ls-remote", "origin"], ["remote", "update"], ["remote", "show", "origin"], ["remote", "add", "o", "u"],
    ["submodule", "update"], ["commit", "-m", "x"], ["reset", "--hard"], ["clean", "-fdx"], ["stash"],
    ["merge", "x"], ["rebase", "x"], ["checkout", "x"], ["switch", "x"], ["branch", "x"], ["tag", "x"],
    ["worktree", "add", "../x"], ["worktree", "remove", "x"], ["worktree", "prune"], ["worktree"],
    ["config", "core.x", "y"], ["apply", "p"], ["am"], ["gc"], ["add", "."], ["rm", "x"], ["mv", "a", "b"],
    ["symbolic-ref", "HEAD", "refs/heads/x"], ["diff", "--output=/tmp/x"], ["diff", "--ext-diff"],
    ["diff", "--textconv"], ["diff", "--no-index", "/a", "/b"], ["version", "--build-options"],
    ["-c", "core.sshCommand=touch /tmp/pwned", "status"], ["--exec-path=/tmp", "status"], ["!sh"],
])
def test_mutating_network_and_unknown_commands_are_rejected(args):
    with pytest.raises(InvalidGitArgumentError) as info:
        validate_args(args)
    assert info.value.code is GitErrorCode.INVALID_GIT_ARGUMENT


@pytest.mark.parametrize("args", [
    ["version"], ["rev-parse", "--show-toplevel"], ["status", "--porcelain=v2", "-z"], ["diff", "--cached"],
    ["worktree", "list", "--porcelain", "-z"], ["remote", "-v"], ["remote"],
    ["symbolic-ref", "--quiet", "--short", "HEAD"],
])
def test_read_only_commands_are_allowed(args):
    assert validate_args(args) == args


@pytest.mark.parametrize("bad", ["status", b"status", [], ("status", 1), ["sta\x00tus"], ["status", None], None])
def test_arguments_must_be_a_list_of_clean_strings(bad):
    with pytest.raises(InvalidGitArgumentError):
        validate_args(bad)


@pytest.mark.parametrize("path", ["", "relative/path", "~/x", "/a\nb", "/a\x00b", 7])
def test_cwd_must_be_absolute_and_clean(fake, path):
    f = fake()
    with pytest.raises(InvalidGitArgumentError):
        client().run(["status"], cwd=path)
    assert f.calls == []  # rejected before any process


def test_rejected_command_starts_no_process(fake):
    f = fake()
    with pytest.raises(InvalidGitArgumentError):
        client().run(["push", "origin"], cwd="/tmp")
    assert f.calls == []


# --- argv / environment ------------------------------------------------------------


def test_argv_is_a_structured_list_without_shell(fake, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_SECRET")
    monkeypatch.setenv("HOME", "/home/someone")
    f = fake(stdout=b"ok")
    result = client().run(["status", "--porcelain=v2"], cwd="/work/p; rm -rf ~")
    argv, kw = f.calls[-1]
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
    assert argv[0] == "/usr/bin/git-fake"
    assert argv[1 : 1 + len(GLOBAL_OPTIONS)] == list(GLOBAL_OPTIONS)
    assert argv[-4:] == ["-C", "/work/p; rm -rf ~", "status", "--porcelain=v2"]  # path is one argument
    assert kw["shell"] is False
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["cwd"] == "/"
    assert kw["timeout"] == client_mod.DEFAULT_TIMEOUT_SECS
    env = kw["env"]
    assert set(env) == {"PATH", *CHILD_ENV}
    assert "HOME" not in env and "GIT_DIR" not in env and "GITHUB_TOKEN" not in env
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_NO_LAZY_FETCH"] == "1"
    assert "ghp_SECRET" not in repr(f.calls)
    assert result.ok and result.text() == "ok" and result.command == "status"


def test_hardening_options_present():
    opts = " ".join(GLOBAL_OPTIONS)
    for needle in ("protocol.allow=never", "core.fsmonitor=false", f"core.hooksPath={os.devnull}",
                   "credential.helper=", "--no-pager"):
        assert needle in opts


def test_diff_always_disables_external_programs(fake):
    f = fake()
    client().run(["diff", "--cached"], cwd="/w")
    argv, _ = f.calls[-1]
    assert argv[-5:] == ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--cached"]


def test_output_is_truncated_and_flagged(fake):
    fake(stdout=b"a" * 100)
    result = GitClient(binary="/usr/bin/git-fake", max_output=10).run(["diff"], cwd="/w")
    assert result.stdout == b"a" * 10 and result.truncated


def test_undecodable_output_survives(fake):
    fake(stdout=b"caf\xe9")
    assert os.fsencode(client().run(["status"], cwd="/w").text()) == b"caf\xe9"


# --- timeout ---------------------------------------------------------------------


def test_timeout_is_normalized(fake):
    fake(raises=subprocess.TimeoutExpired(["git"], 0.5))
    with pytest.raises(GitTimeoutError) as info:
        GitClient(binary="/usr/bin/git-fake", timeout=0.5).run(["status"], cwd="/w")
    err = info.value
    assert err.code is GitErrorCode.COMMAND_TIMEOUT and err.retryable
    assert err.__cause__ is None and err.__context__ is None
    assert "0.5s" in err.message


def test_per_call_timeout_passed(fake):
    f = fake()
    client().run(["status"], cwd="/w", timeout=2)
    assert f.calls[-1][1]["timeout"] == 2


@pytest.mark.parametrize("timeout", [0, -1, 10_000, True, "5"])
def test_timeout_must_be_bounded(timeout):
    with pytest.raises(InvalidGitArgumentError):
        GitClient(timeout=timeout)


def test_per_call_timeout_must_be_bounded(fake):
    fake()
    with pytest.raises(InvalidGitArgumentError):
        client().run(["status"], cwd="/w", timeout=10_000)


def test_real_git_times_out(tmp_path, monkeypatch):
    # A real child process that outlives the timeout is killed and normalized.
    repo = make_repo(tmp_path / "r")
    real_run = subprocess.run

    def slow(argv, **kw):
        if argv[-1] == "version":
            return real_run(argv, **kw)
        return real_run(["sleep", "5"], **{**kw, "env": None})

    monkeypatch.setattr(client_mod.subprocess, "run", slow)
    with pytest.raises(GitTimeoutError):
        GitClient(timeout=0.2).run(["status"], cwd=repo.root)


# --- process errors --------------------------------------------------------------


def test_git_not_installed():
    with pytest.raises(GitNotInstalledError) as info:
        GitClient(binary="/nonexistent/bin/git").run(["status"], cwd="/w")
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_git_not_on_path(monkeypatch):
    monkeypatch.setattr(client_mod.shutil, "which", lambda name: None)
    with pytest.raises(GitNotInstalledError):
        GitClient().run(["status"], cwd="/w")


@pytest.mark.parametrize("exc, cls", [
    (PermissionError("denied"), GitPermissionError),
    (OSError("weird"), UnexpectedGitError),
    (subprocess.SubprocessError("boom"), UnexpectedGitError),
])
def test_process_exceptions_are_normalized(fake, exc, cls):
    fake(raises=exc)
    with pytest.raises(cls) as info:
        client().run(["status"], cwd="/w")
    assert info.value.__context__ is None and not isinstance(info.value, (OSError, subprocess.SubprocessError))


def test_nonzero_exit_without_check_returns_result(fake):
    fake(returncode=1, stderr=b"")
    result = client().run(["rev-parse", "--verify", "--quiet", "x"], cwd="/w", check=False)
    assert not result.ok and result.exit_code == 1


@pytest.mark.parametrize("stderr, cls", [
    ("fatal: not a git repository (or any of the parent directories): .git", NotRepositoryError),
    ("fatal: cannot change to '/nope': No such file or directory", NotRepositoryError),
    ("fatal: this operation must be run in a work tree", InvalidRepositoryError),
    ("fatal: detected dubious ownership in repository at '/x'", GitPermissionError),
    ("error: open(\"x\"): Permission denied", GitPermissionError),
    ("fatal: Unable to create '/x/.git/index.lock': File exists.", RepositoryLockedError),
    ("fatal: something else entirely", GitCommandError),
])
def test_failures_are_classified(fake, stderr, cls):
    fake(returncode=128, stderr=stderr.encode())
    with pytest.raises(cls) as info:
        client().run(["status"], cwd="/w/secret-project")
    err = info.value
    assert err.operation == "status"
    assert err.repository_path == "/w/secret-project"
    assert "/w/secret-project" not in str(err)  # path kept as attribute, not in the message
    assert stderr not in str(err)


def test_stderr_detail_is_redacted_and_bounded():
    err = classify_failure(
        "fatal: unable to access 'https://bob:hunter2@example.com/r.git/': token=abc123 " + "x" * 500,
        operation="status", repository_path=None, exit_code=128,
    )
    assert "hunter2" not in err.detail and "abc123" not in err.detail and "bob" not in err.detail
    assert len(err.detail) <= 200
    assert set(err.to_dict()) == {"code", "message", "operation", "repository_path", "retryable", "detail"}


def test_scrub_removes_url_credentials():
    assert scrub("https://user:pat_123@github.com/x.git") == "https://github.com/x.git"
    assert scrub("ssh://git@host/x.git") == "ssh://host/x.git"
    assert scrub("git@github.com:org/x.git") == "git@github.com:org/x.git"  # scp-like user is not a secret


# --- version ---------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("git version 2.51.2", (2, 51)), ("git version 2.39.3 (Apple Git-145)\n", (2, 39)),
    ("git version 2.36.0.windows.1", (2, 36)),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_unparseable_version():
    with pytest.raises(UnsupportedGitVersionError):
        parse_version("not git")


def test_old_git_is_unsupported_and_runs_nothing_else(fake):
    f = fake(version=b"git version 2.20.1\n")
    with pytest.raises(UnsupportedGitVersionError) as info:
        client().run(["status"], cwd="/w")
    assert "2.20" in info.value.message
    assert [c[0][-1] for c in f.calls] == ["version"]


def test_minimum_version_is_accepted(fake):
    major, minor = MIN_GIT_VERSION
    fake(version=f"git version {major}.{minor}.0\n".encode())
    assert client().ensure_supported() == MIN_GIT_VERSION


def test_version_is_cached(fake):
    f = fake()
    c = client()
    c.run(["status"], cwd="/w")
    c.run(["status"], cwd="/w")
    assert [c_[0][-1] for c_ in f.calls].count("version") == 1


def test_real_git_is_supported():
    assert GitClient().ensure_supported() >= MIN_GIT_VERSION
