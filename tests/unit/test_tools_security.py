"""Tool boundary security: no callable injection, no raw execution, no direct GitClient,
no shell/process/network/filesystem access from the tool layer, nothing leaked."""

import ast
import builtins
import glob
import io
import json
import os
import pathlib
import subprocess
from pathlib import Path

import pytest

from siamang.cli.main import main
from siamang.config import LoggingConfig
from siamang.git import GitWorkspaceManager
from siamang.git import client as client_mod
from siamang.log import configure_logging
from siamang.projects import ProjectRegistry
from siamang.tools import ToolContext, ToolErrorCode, build_tool_registry
from siamang.tools.errors import ToolDefinitionError, ToolImplementationNotFoundError
from siamang.tools.git_tools import GitToolAdapter
from siamang.tools.implementations import KNOWN_IMPLEMENTATIONS, ImplementationTable
from siamang.tools.permissions import READ_ONLY
from tests.support.git import make_repo

SRC = Path(__file__).parents[2] / "src" / "siamang"
TOOLS = SRC / "tools"
SECRET = "ghp_TOOL-SECRET-31337"

FORBIDDEN_MODULES = {"subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "shutil", "glob", "pathlib",
                     "os", "asyncio", "multiprocessing", "concurrent", "threading", "pty", "importlib", "pickle",
                     "marshal", "ctypes", "runpy", "code", "dotenv", "git", "pygit2", "dulwich"}
# Builtins that load/run code or dispatch by string: forbidden as bare names (re.compile stays legal).
FORBIDDEN_BUILTINS = {"getattr", "eval", "exec", "compile", "__import__", "open", "breakpoint"}
FORBIDDEN_NAMES = {"import_module", "system", "popen", "environ", "getenv", "putenv", "listdir", "scandir", "walk",
                   "iterdir", "rglob", "glob", "GitClient", "run_path", "load_dotenv"}
FORBIDDEN_PREFIXES = ("spawn", "exec", "fork")
GIT_READ_COMMANDS = {"version", "rev-parse", "symbolic-ref", "status", "diff", "worktree", "remote"}


# --- static checks (functions, so deliberate-break tests can feed mutated source) ------------


def import_violations(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names} & FORBIDDEN_MODULES
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES:
                found.add(node.module)
            if node.module.startswith("siamang.git.client") or node.module.startswith("siamang.storage"):
                found.add(node.module)
            if node.module == "siamang.git" and any(a.name in ("GitClient", "client") for a in node.names):
                found.add("siamang.git.GitClient")
    return found


def name_violations(source: str) -> set[str]:
    tree = ast.parse(source)
    bare = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    found = (bare & (FORBIDDEN_BUILTINS | FORBIDDEN_NAMES)) | (attrs & FORBIDDEN_NAMES)
    found |= {a for a in attrs if a.startswith(FORBIDDEN_PREFIXES)}
    return found


def raw_run_calls(source: str) -> list[int]:
    """Any `.run(...)` call in the tool layer would be a path to GitClient.run / subprocess.run."""
    return [n.lineno for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "Popen")]


def _tool_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in TOOLS.glob("*.py")}


def test_tool_layer_imports_nothing_dangerous():
    assert {name: v for name, src in _tool_sources().items() if (v := import_violations(src))} == {}


def test_tool_layer_uses_no_dynamic_dispatch_process_env_or_filesystem_names():
    assert {name: v for name, src in _tool_sources().items() if (v := name_violations(src))} == {}


def test_tool_layer_never_calls_run():
    assert {name: v for name, src in _tool_sources().items() if (v := raw_run_calls(src))} == {}


# --- deliberate breaks ---------------------------------------------------------------------


@pytest.mark.parametrize("injection", [
    "import subprocess\n", "import os\n", "from siamang.git.client import GitClient\n",
    "from siamang.git import GitClient\n", "import socket\n", "import shutil\n", "from pathlib import Path\n",
    "import importlib\n", "from siamang.storage.database import Database\n",
])
def test_break_dangerous_import_is_detected(injection):
    source = (TOOLS / "git_tools.py").read_text()
    assert import_violations(injection + source)


@pytest.mark.parametrize("snippet", [
    "getattr(self._manager, name)(project)", "os.system('git push')", "os.popen('ls')", "eval(args['x'])",
    "exec(code)", "open('/proj/.env')", "os.environ['GITHUB_TOKEN']", "os.walk('/')", "Path('/').rglob('*')",
    "GitClient().run(['push'], cwd='/')", "os.spawnv(0, 'x', [])", "os.execvp('git', [])", "os.fork()",
    "__import__('subprocess')", "compile(src, 'x', 'exec')",
])
def test_break_dangerous_name_is_detected(snippet):
    assert name_violations(f"def f(self, project, args, name, code):\n    {snippet}\n")


def test_break_run_call_is_detected():
    assert raw_run_calls("def f(c):\n    return c.run(['status'], cwd='/')\n")
    assert raw_run_calls("import subprocess\nsubprocess.run('git ' + x, shell=True)\n")


# --- implementation allowlist ---------------------------------------------------------------


@pytest.fixture
def manager(db):
    return GitWorkspaceManager(ProjectRegistry(db))


def test_known_implementations_are_fixed():
    assert set(KNOWN_IMPLEMENTATIONS) == {"git.inspect", "git.status", "git.diff", "git.diff_cached", "git.worktrees"}
    with pytest.raises(TypeError):
        KNOWN_IMPLEMENTATIONS["git.push"] = (GitToolAdapter, "push")
    assert not any(seg in impl for impl in KNOWN_IMPLEMENTATIONS for seg in ("run", "exec", "shell", "command"))


def test_table_accepts_only_owned_adapter_methods(manager):
    adapter = GitToolAdapter(manager)
    ImplementationTable({"git.status": adapter.status})  # the real thing is fine

    class Sneaky(GitToolAdapter):
        def status(self, project, args):
            return {"pwned": True}

    sneaky = Sneaky(manager)
    for impl_id, fn in [
        ("git.status", lambda project, args: {}),
        ("git.status", print),
        ("git.status", subprocess.run),
        ("git.status", os.system),
        ("git.status", adapter.inspect),  # owned, but not the method for this ID
        ("git.status", sneaky.status),  # subclass override
        ("git.status", GitToolAdapter.status),  # unbound function
        ("git.status", "siamang.tools.git_tools:GitToolAdapter.status"),
    ]:
        with pytest.raises(ToolDefinitionError):
            ImplementationTable({impl_id: fn})
    with pytest.raises(ToolImplementationNotFoundError):
        ImplementationTable({"git.push": adapter.status})
    with pytest.raises(ToolImplementationNotFoundError):
        ImplementationTable({"git.run": adapter.status})


def test_table_rechecks_at_use(manager):
    table = ImplementationTable.build(git=manager)
    object.__setattr__(table, "_bindings", {"git.status": lambda project, args: {"clean": True}})
    assert table.get("git.status") is None  # a swapped-in callable is never returned


def test_adapter_requires_the_workspace_manager():
    with pytest.raises(TypeError):
        GitToolAdapter(client_mod.GitClient())


def test_adapter_exposes_no_raw_git():
    public = {n for n in vars(GitToolAdapter) if not n.startswith("_")}
    assert public == {"bindings", "inspect", "status", "diff", "diff_cached", "worktrees"}


# --- runtime --------------------------------------------------------------------------------


@pytest.fixture
def setup(db, tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo.path / ".env").write_text(f"GITHUB_TOKEN={SECRET}\n")
    (repo.path / "credentials.json").write_text(f'{{"token": "{SECRET}"}}')
    repo.git("remote", "add", "origin", f"https://bob:{SECRET}@example.invalid/o/r.git")
    repo.commit("base")
    repo.write("README.md", "changed\n")
    projects = ProjectRegistry(db)
    project = projects.register(name="P", root_path=repo.root)
    tools = build_tool_registry(projects=projects, git=GitWorkspaceManager(projects))
    context = ToolContext("system", "sec", READ_ONLY, project_id=project.project_id)
    return tools, context, project, repo


def _invoke_all(tools, context, project):
    out = []
    for t in tools.list_tools(context):
        r = tools.invoke(t.tool_id, context, {"project_id": project.project_id})
        assert r.success, (t.tool_id, r.error and r.error.to_dict())
        out.append(r)
    return out


def test_only_allowlisted_read_commands_reach_git(setup, monkeypatch):
    tools, context, project, _ = setup
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    calls = []
    real = subprocess.run

    def record(argv, **kw):
        calls.append((list(argv), kw))
        return real(argv, **kw)

    monkeypatch.setattr(client_mod.subprocess, "run", record)
    _invoke_all(tools, context, project)
    assert calls
    for argv, kw in calls:
        assert kw["shell"] is False and kw["timeout"] > 0
        assert "protocol.allow=never" in argv
        command = argv[argv.index("-C") + 2:] if "-C" in argv else argv[-1:]
        assert command[0] in GIT_READ_COMMANDS, argv
        assert not set(command) & {"push", "pull", "fetch", "clone", "ls-remote", "submodule", "commit", "reset"}
        assert SECRET not in repr(kw["env"]) and "HOME" not in kw["env"]


@pytest.fixture
def guarded(monkeypatch, setup):
    _, _, _, repo = setup
    touched = []

    def forbidden(*a, **k):
        raise AssertionError("forbidden call during tool operation")

    for target, name in [(os, "scandir"), (os, "listdir"), (os, "walk"), (pathlib.Path, "iterdir"),
                         (pathlib.Path, "glob"), (pathlib.Path, "rglob"), (glob, "glob"), (glob, "iglob"),
                         (os, "system"), (os, "popen"), (os, "execv"), (os, "execvp"), (os, "spawnv"), (os, "fork")]:
        monkeypatch.setattr(target, name, forbidden)
    root = repo.root

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
    return touched


def test_no_scan_no_env_no_secret_leak(setup, guarded, monkeypatch):
    tools, context, project, repo = setup
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG"), stream=stream)
    results = _invoke_all(tools, context, project)
    assert guarded == []
    dumped = json.dumps([r.to_dict() for r in results])
    assert SECRET not in dumped
    logged = stream.getvalue()
    assert "tool git.status ok for system:sec" in logged
    assert SECRET not in logged and repo.root not in logged


def test_shell_syntax_in_inputs_never_executes(setup, tmp_path):
    tools, context, project, _ = setup
    marker = tmp_path / "PWNED"
    for payload in [
        {"project_id": f"prj_x; touch {marker}"},
        {"project_id": f"$(touch {marker})"},
        {"project_id": project.project_id, "command": f"touch {marker}"},
        {"project_id": project.project_id, "argv": ["-c", f"core.pager=touch {marker}", "log"]},
        {"project_id": project.project_id, "path": "../../../../etc"},
        {"project_id": project.project_id, "cached": f"`touch {marker}`"},
    ]:
        r = tools.invoke("git.diff", context, payload)
        assert r.error.code in (ToolErrorCode.TOOL_INPUT_INVALID, ToolErrorCode.TOOL_SCOPE_DENIED)
    assert not marker.exists()


def test_no_raw_git_tool_can_be_invoked(setup):
    tools, context, project, _ = setup
    for tid in ("git.run", "git.exec", "git.command", "git.shell", "git.push", "git.pull", "git.fetch",
                "git.commit", "git.raw", "process.execute", "shell.run", "fs.read"):
        assert tools.invoke(tid, context, {"project_id": project.project_id}).error.code is ToolErrorCode.TOOL_NOT_FOUND


def test_network_blocked_and_unused(setup):
    # autouse fixture: any socket connect raises. All tools still work.
    tools, context, project, _ = setup
    _invoke_all(tools, context, project)


def test_cli_listing_executes_nothing(capsys, config_file, tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("tools listing must not start a process")

    monkeypatch.setattr(client_mod.subprocess, "run", forbidden)
    db_path = tmp_path / "never.db"
    cfg = config_file(f'[storage]\npath = "{db_path}"\n')
    assert main(["--config", str(cfg), "tools", "list", "--json"]) == 0
    assert main(["--config", str(cfg), "tools", "get", "git.status"]) == 0
    capsys.readouterr()
    assert not db_path.exists()
