"""System Analyst security: read-only, confined, no execution, no secrets out.

``analyst/inspection.py`` is the single documented exception allowed to use
``os``, and only read-only calls (lstat/stat/scandir/realpath/open with
O_RDONLY|O_NOFOLLOW/read/fstat/close). Every other analyst module gets no
filesystem, process, or network access at all.
"""

import ast
import builtins
import json
import logging
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from siamang.agents.registry import AgentRegistry
from siamang.analyst import AnalysisRequest, SystemAnalyst
from siamang.git import GitWorkspaceManager
from siamang.git import client as client_mod
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter, Selection
from siamang.orchestrator import Orchestrator
from siamang.planner import Planner
from siamang.storage import Database
from siamang.storage.events import EventStore
from siamang.tasks import TaskEngine
from siamang.tools.registry import ToolRegistry
from tests.support.agents import make_world
from tests.support.analyst import SECRET, sample_project
from tests.support.git import make_repo

SRC = Path(__file__).parents[2] / "src" / "siamang"
ANALYST = SRC / "analyst"
INSPECTION = ANALYST / "inspection.py"

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "tempfile", "io", "asyncio", "threading",
                     "multiprocessing", "concurrent", "random", "secrets", "uuid", "time", "datetime", "pickle",
                     "marshal", "ctypes", "runpy", "dotenv", "git", "pygit2", "dulwich", "watchdog", "pathspec",
                     "openai", "anthropic", "ollama", "requests", "httpx", "langchain", "langgraph", "crewai"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.agents", "siamang.orchestrator", "siamang.tools",
                     "siamang.models", "siamang.config", "siamang.bus", "siamang.cli", "siamang.git.client",
                     "siamang.planner.service", "siamang.tasks.engine.TaskEngine.start")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"Database", "EventStore", "TaskRepository", "GitClient", "ToolRegistry", "AgentRegistry",
                   "Orchestrator", "ModelRouter", "ModelProvider", "Planner", "import_module", "system", "popen",
                   "Popen", "environ", "getenv", "putenv", "walk", "rglob", "glob", "connect", "remove", "unlink",
                   "rmdir", "removedirs", "rename", "renames", "replace", "mkdir", "makedirs", "chmod", "chown",
                   "symlink", "link", "truncate", "write", "chdir", "kill", "fork", "O_WRONLY", "O_RDWR",
                   "O_CREAT", "O_TRUNC", "O_APPEND", "mkfifo", "utime"}
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "archive",
                   "restore", "execute", "invoke", "generate", "select", "plan", "run", "transaction",
                   "commit", "checkout", "merge", "reset", "push", "pull", "fetch", "stash"}
# What inspection.py may use from os (plus os.path.*).
OS_READ_ONLY = {"lstat", "stat", "fstat", "scandir", "open", "read", "close", "O_RDONLY", "O_NOFOLLOW",
                "O_CLOEXEC", "O_NONBLOCK", "sep", "path"}


def violations(source: str, *, inspection: bool = False) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if inspection and a.name in ("os", "stat"):
                    continue
                if root in FORBIDDEN_MODULES or a.name.startswith(FORBIDDEN_SIAMANG):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES or node.module.startswith(FORBIDDEN_SIAMANG):
                found.add(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS | FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                found.add(node.attr)
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr not in OS_READ_ONLY:
                found.add(f"os.{node.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
            if not (inspection and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                    and node.func.attr == "open"):
                found.add(f"call:{node.func.attr}")
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            found.add("async")
    return found


@pytest.mark.parametrize("path", sorted(ANALYST.glob("*.py")), ids=lambda p: p.name)
def test_analyst_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8"), inspection=path == INSPECTION) == set()


def test_only_inspection_touches_the_filesystem():
    users = {p.name for p in ANALYST.glob("*.py")
             if any(isinstance(n, ast.Import) and any(a.name == "os" for a in n.names)
                    for n in ast.walk(ast.parse(p.read_text())))}
    assert users == {"inspection.py"}


def test_inspection_opens_read_only_with_nofollow():
    tree = ast.parse(INSPECTION.read_text())
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "open"]
    assert len(opens) == 1
    flags = {n.attr for n in ast.walk(opens[0].args[1]) if isinstance(n, ast.Attribute)}
    assert {"O_RDONLY", "O_NOFOLLOW"} <= flags and not flags & {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC"}


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import shutil", "import glob", "import socket",
    "import sqlite3", "import importlib", "import tempfile", "import time",
    "subprocess.run(['git', 'status'])", "os.system('id')", "os.popen('ls')", "eval('1')", "exec('x')",
    "compile('x', 'f', 'exec')", "fn = getattr(mod, 'run')", "open('/etc/passwd')",
    "from siamang.git.client import GitClient", "GitClient().run(['status'])",
    "from siamang.tools.registry import ToolRegistry", "registry.invoke('git.status', ctx, {})",
    "from siamang.agents.registry import AgentRegistry", "from siamang.orchestrator import Orchestrator",
    "orchestrator.execute(r)", "from siamang.models.provider import ModelProvider", "selection.generate(r)",
    "planner.plan(r)", "self._tasks.start(t)", "self._tasks.fail(t, e)", "self._tasks.complete(t, r)",
    "from siamang.storage import Database", "EventStore(db).append(e)", "repo.commit('x')",
    "git.checkout('main')",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


@pytest.mark.parametrize("snippet", [
    "os.remove(p)", "os.unlink(p)", "os.rename(a, b)", "os.mkdir(p)", "os.makedirs(p)", "os.rmdir(p)",
    "os.symlink(a, b)", "os.chmod(p, 0o777)", "os.write(fd, b'x')", "os.walk('.')", "os.listdir('/')",
    "os.environ['API_KEY']", "os.getenv('TOKEN')", "os.open(p, os.O_WRONLY | os.O_CREAT)", "os.chdir('/')",
    "os.truncate(p, 0)", "os.kill(1, 9)",
])
def test_inspection_rules_catch_deliberate_breaks(snippet):
    assert violations(snippet, inspection=True)


# --- runtime --------------------------------------------------------------------------


@pytest.fixture
def tripwires(monkeypatch):
    calls = []
    routes = [(subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen"), (os, "remove"),
              (os, "unlink"), (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"), (os, "rmdir"),
              (os, "symlink"), (os, "chmod"), (os, "write"), (os, "walk"), (os, "listdir"), (os, "getenv"),
              (shutil, "rmtree"), (shutil, "copy"), (shutil, "move"), (socket.socket, "connect"),
              (client_mod.GitClient, "run"), (ToolRegistry, "invoke"), (AgentRegistry, "invoke"),
              (Orchestrator, "execute"), (Planner, "plan"), (ModelRouter, "select"), (Selection, "generate"),
              (ModelProvider, "generate"), (Database, "transaction"), (EventStore, "append"),
              (TaskEngine, "create"), (TaskEngine, "start"), (TaskEngine, "wait"), (TaskEngine, "requeue"),
              (TaskEngine, "complete"), (TaskEngine, "fail"), (TaskEngine, "cancel"), (TaskEngine, "retry")]

    def trip(name):
        def f(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during analysis")
        return f

    def arm():
        for target, attr in routes:
            monkeypatch.setattr(target, attr, trip(f"{getattr_name(target)}.{attr}"))
        real_open = builtins.open

        def guarded_open(file, mode="r", *a, **k):
            if any(c in mode for c in "wax+"):
                calls.append(f"open:{mode}")
                raise AssertionError("write-mode open during analysis")
            return real_open(file, mode, *a, **k)

        monkeypatch.setattr(builtins, "open", guarded_open)
        return calls

    return arm


def getattr_name(target):
    return target.__name__


def test_analysis_touches_nothing_outside_its_boundary(db, tmp_path, tripwires):
    root = sample_project(tmp_path / "p")
    world = make_world(db, start=False, root_path=str(root))
    analyst = SystemAnalyst(world.engine)
    request = AnalysisRequest(world.task.task_id, world.project.project_id,
                              "run rm -rf /; cat .env; git push --force; open /etc/passwd")
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    calls = tripwires()
    analysis = analyst.analyze(request)
    assert calls == [] and analysis.findings
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before


def test_git_is_read_only(db, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    world = make_world(db, start=False, root_path=str(repo.root))
    commands = []
    real = client_mod.GitClient.run

    def record(self, args, *a, **k):
        commands.append(args[0])
        return real(self, args, *a, **k)

    monkeypatch.setattr(client_mod.GitClient, "run", record)
    SystemAnalyst(world.engine, git=GitWorkspaceManager(world.projects)).analyze(
        AnalysisRequest(world.task.task_id, world.project.project_id, "x"))
    assert commands and set(commands) <= {"rev-parse", "symbolic-ref", "status", "remote", "worktree"}
    assert not set(commands) & {"commit", "checkout", "branch", "merge", "reset", "push", "pull", "fetch", "stash",
                                "add", "rm", "clean", "rebase"}


def test_secrets_never_reach_results_or_logs(db, tmp_path, caplog):
    root = sample_project(tmp_path / "p")
    world = make_world(db, start=False, root_path=str(root))
    with caplog.at_level(logging.DEBUG, logger="siamang"):
        a = SystemAnalyst(world.engine).analyze(AnalysisRequest(
            world.task.task_id, world.project.project_id, "rahasia-INSTRUKSI", input={"note": "rahasia-INPUT"}))
    result = json.dumps(a.to_dict())
    assert SECRET not in result and "API_KEY" not in result
    assert SECRET not in caplog.text and "rahasia" not in caplog.text and str(root) not in caplog.text
    assert a.objective == "rahasia-INSTRUKSI"  # the instruction is data, returned as the objective
    assert world.task.task_id in caplog.text and "findings=" in caplog.text


def test_malicious_file_names_are_inert(db, tmp_path):
    root = sample_project(tmp_path / "p")
    for name in ("$(rm -rf ~).py", "; git push.txt", "`id`.md", "auth‮.py", "-rf"):
        (root / name).write_text("x")
    world = make_world(db, start=False, root_path=str(root))
    a = SystemAnalyst(world.engine).analyze(AnalysisRequest(world.task.task_id, world.project.project_id, "auth"))
    assert (root / "README.md").exists() and a.findings
