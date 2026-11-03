"""Git boundary security: one subprocess call site, no shell, no network, no env/credential
leaks, no Siamang-side scanning or file reads, and project strings are inert data."""

import ast
import builtins
import glob
import io
import os
import pathlib
import subprocess
from pathlib import Path

import pytest

from siamang.cli.main import main
from siamang.config import LoggingConfig
from siamang.git import GitClient, GitWorkspaceManager, InvalidGitArgumentError
from siamang.git import client as client_mod
from siamang.git.client import READ_ONLY_COMMANDS, validate_args
from siamang.log import configure_logging
from siamang.projects import ProjectRegistry
from tests.support.git import make_repo

SRC = Path(__file__).parents[2] / "src" / "siamang"
GIT_PKG = SRC / "git"
CLIENT = GIT_PKG / "client.py"
CLAUDE_CLI = SRC / "models" / "providers" / "claude_cli.py"
REPO_CLIENT = SRC / "repos" / "client.py"
PROCESS_BOUNDARIES = {"git/client.py", "models/providers/claude_cli.py", "repos/client.py"}
SECRET = "ghp_GIT-ENV-SECRET-777"

NETWORK_COMMANDS = ("push", "pull", "fetch", "clone", "ls-remote", "remote update", "remote show",
                    "remote prune", "submodule", "archive", "send-email", "request-pull", "daemon")
MUTATING_COMMANDS = ("commit", "reset", "clean", "stash", "merge", "rebase", "checkout", "switch", "restore",
                     "branch", "tag", "add", "rm", "mv", "apply", "am", "cherry-pick", "revert", "gc", "prune",
                     "config", "update-ref", "update-index", "worktree add", "worktree remove", "worktree prune",
                     "worktree move", "worktree lock", "notes", "replace", "init")
READ_COMMANDS = {"version", "rev-parse", "symbolic-ref", "status", "diff", "worktree", "remote"}


# --- static checks (functions so deliberate-break tests can feed them mutated source) ------


def shell_violations(source: str) -> list[str]:
    """Calls with shell=<anything but a literal False>, and os.system/os.popen/os.exec*/os.spawn*."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                out.append(f"shell= at line {node.lineno}")
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
            if func.attr in ("system", "popen") or func.attr.startswith(("exec", "spawn")):
                out.append(f"os.{func.attr} at line {node.lineno}")
    return out


def subprocess_importers(sources: dict[str, str]) -> set[str]:
    users = set()
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            if any(m.split(".")[0] in ("subprocess", "pty", "multiprocessing") for m in mods):
                users.add(name)
    return users


def subprocess_run_calls_without_timeout(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen", "call", "check_output", "check_call")
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            if not any(kw.arg == "timeout" for kw in node.keywords):
                lines.append(node.lineno)
    return lines


def network_commands_blocked() -> bool:
    for cmd in NETWORK_COMMANDS + MUTATING_COMMANDS:
        try:
            validate_args(cmd.split())
        except InvalidGitArgumentError:
            continue
        return False
    return True


def _sources() -> dict[str, str]:
    return {p.relative_to(SRC).as_posix(): p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}


def test_no_shell_execution_anywhere():
    bad = {name: v for name, src in _sources().items() if (v := shell_violations(src))}
    assert bad == {}


def test_only_the_named_boundaries_spawn_processes():
    assert subprocess_importers(_sources()) == PROCESS_BOUNDARIES


@pytest.mark.parametrize("path", [CLIENT, CLAUDE_CLI, REPO_CLIENT])
def test_every_subprocess_call_has_a_timeout_and_no_shell(path):
    assert subprocess_run_calls_without_timeout(path.read_text()) == []
    assert shell_violations(path.read_text()) == []
    assert "subprocess.run(" in path.read_text()


def test_network_and_mutating_commands_are_blocked():
    assert network_commands_blocked()
    assert set(READ_ONLY_COMMANDS) == READ_COMMANDS


# --- deliberate breaks: the checks above must catch these mutations ---------------------


def test_break_shell_true_is_detected():
    source = CLIENT.read_text()
    assert "shell=False" in source
    assert shell_violations(source.replace("shell=False", "shell=True"))
    assert shell_violations("import os\nos.system('git status')\n")
    assert shell_violations("import subprocess\nsubprocess.run('git ' + x, shell=flag)\n")


def test_break_subprocess_outside_client_is_detected():
    sources = _sources()
    sources["git/workspace.py"] = "import subprocess\n" + sources["git/workspace.py"]
    assert subprocess_importers(sources) == PROCESS_BOUNDARIES | {"git/workspace.py"}


def test_break_missing_timeout_is_detected():
    mutated = CLIENT.read_text().replace("timeout=timeout,\n                check=False", "check=False")
    assert subprocess_run_calls_without_timeout(mutated)


def test_break_allowlisted_push_is_detected(monkeypatch):
    monkeypatch.setitem(READ_ONLY_COMMANDS, "push", lambda a: True)
    assert not network_commands_blocked()


# --- runtime: what actually reaches the process boundary --------------------------------


@pytest.fixture
def recorder(monkeypatch):
    calls = []
    real = subprocess.run

    def record(argv, **kw):
        calls.append((list(argv), kw))
        return real(argv, **kw)

    monkeypatch.setattr(client_mod.subprocess, "run", record)
    return calls


@pytest.fixture
def project_repo(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo.path / ".env").write_text(f"GITHUB_TOKEN={SECRET}\n")
    (repo.path / "credentials.json").write_text(f'{{"token": "{SECRET}"}}')
    repo.git("remote", "add", "origin", f"https://bob:{SECRET}@example.invalid/o/r.git")
    repo.write("src/app.py", "raise SystemExit('must never run')\n")
    repo.commit("base")
    repo.write("src/app.py", "changed\n")
    return repo


def _workflow(manager, ref):
    manager.inspect(ref)
    manager.head(ref)
    manager.status(ref)
    manager.diff(ref)
    manager.diff(ref, cached=True)
    manager.worktrees(ref)
    manager.remotes(ref)


def test_only_local_read_commands_reach_git(db, project_repo, recorder, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    reg = ProjectRegistry(db)
    p = reg.register(name="P", root_path=project_repo.root)
    _workflow(GitWorkspaceManager(reg), p.project_id)
    assert recorder
    binary = GitClient().binary
    for argv, kw in recorder:
        assert argv[0] == binary
        assert kw["shell"] is False and kw["timeout"] > 0 and kw["cwd"] == "/"
        assert "SSH_AUTH_SOCK" not in kw["env"] and "GITHUB_TOKEN" not in kw["env"] and "HOME" not in kw["env"]
        assert SECRET not in repr(kw["env"])
        assert "protocol.allow=never" in argv
        sub = argv[argv.index("-C") + 2] if "-C" in argv else argv[-1]
        assert sub in READ_COMMANDS, argv
        if sub == "remote":
            assert argv[-2:] == ["remote", "-v"]
        if sub == "worktree":
            assert argv[argv.index("worktree") + 1] == "list"


@pytest.fixture
def no_scan_no_read(monkeypatch, project_repo):
    """Siamang code may not list directories, or open anything inside the project.
    (Git itself runs in a child process and is unaffected.)"""
    touched = []

    def forbidden(*a, **k):
        raise AssertionError("directory listing during Git workspace operation")

    for target, name in [(os, "scandir"), (os, "listdir"), (os, "walk"), (pathlib.Path, "iterdir"),
                         (pathlib.Path, "glob"), (pathlib.Path, "rglob"), (glob, "glob"), (glob, "iglob"),
                         (os, "system"), (os, "popen")]:
        monkeypatch.setattr(target, name, forbidden)

    root = str(project_repo.path)

    def guard(real):
        def wrapper(path, *a, **k):
            try:
                inside = os.fspath(path).startswith(root)
            except TypeError:
                inside = False
            if inside:
                touched.append(os.fspath(path))
                raise AssertionError(f"read inside project: {path}")
            return real(path, *a, **k)

        return wrapper

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(os, "open", guard(os.open))
    real_read_text, real_read_bytes = pathlib.Path.read_text, pathlib.Path.read_bytes
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: guard(real_read_text)(self, *a, **k))
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self, *a, **k: guard(real_read_bytes)(self, *a, **k))
    return touched


def test_no_scan_no_env_file_and_no_secret_leak(db, project_repo, no_scan_no_read, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG"), stream=stream)
    reg = ProjectRegistry(db)
    p = reg.register(name="P", root_path=project_repo.root)
    manager = GitWorkspaceManager(reg)
    _workflow(manager, p.project_id)
    info = manager.inspect(p.project_id)
    assert no_scan_no_read == []
    assert SECRET not in repr(info.to_dict())
    logged = stream.getvalue()
    assert "git status exit=0" in logged  # operations are logged...
    assert SECRET not in logged and project_repo.root not in logged  # ...without secrets or paths


def test_cli_no_scan_no_env_file(capsys, config_file, tmp_path, project_repo, no_scan_no_read):
    cfg = config_file(f'[storage]\npath = "{tmp_path / "s.db"}"\n')
    assert main(["--config", str(cfg), "projects", "register", "--name", "P", "--path", project_repo.root,
                 "--json"]) == 0
    import json

    pid = json.loads(capsys.readouterr().out)["project_id"]
    for cmd in (["inspect"], ["status"], ["diff"], ["diff", "--cached"], ["worktrees"]):
        assert main(["--config", str(cfg), "git", *cmd, pid, "--json"]) == 0
    out = capsys.readouterr()
    assert no_scan_no_read == []
    assert SECRET not in out.out + out.err


def test_project_strings_cannot_execute(db, tmp_path):
    marker = tmp_path / "PWNED"
    evil_dir = tmp_path / f"p; touch {marker} && $(touch {marker}) `touch {marker}` | sh"
    repo = make_repo(evil_dir)
    reg = ProjectRegistry(db)
    p = reg.register(name="$(touch PWNED) `touch PWNED`; rm -rf ~", root_path=repo.root, description=f"`touch {marker}`",
                     metadata={"language": f"; touch {marker}"})
    manager = GitWorkspaceManager(reg)
    _workflow(manager, p.project_id)
    assert manager.locate(p).repository_root == repo.root
    assert not marker.exists()


def test_option_like_paths_are_not_options():
    # A cwd is always passed after -C as one argument and must be absolute, so it can never
    # be parsed as a Git option.
    with pytest.raises(InvalidGitArgumentError):
        GitClient().run(["status"], cwd="--exec-path=/tmp")


def test_network_blocked_and_unused(db, project_repo):
    # The autouse fixture makes any connect() raise; Git runs out of process and is
    # additionally barred from every transport (protocol.allow=never).
    reg = ProjectRegistry(db)
    p = reg.register(name="P", root_path=project_repo.root)
    _workflow(GitWorkspaceManager(reg), p)


def test_git_package_reads_no_environment_or_files():
    names_forbidden = {"environ", "getenv", "putenv", "load_dotenv", "listdir", "scandir", "walk", "iterdir",
                       "rglob", "realpath", "read_text", "read_bytes", "exec", "eval"}
    for path in GIT_PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not names & names_forbidden, (path.name, names & names_forbidden)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "open" not in calls, path.name
