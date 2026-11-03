"""Planner boundary security: decomposition only. No process, filesystem, network, database, Git,
tool, model, agent, orchestrator, task mutation, dynamic loading, or async."""

import ast
import os
import socket
import subprocess
from pathlib import Path

import pytest

from siamang.agents.registry import AgentRegistry
from siamang.git import client as client_mod
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter, Selection
from siamang.orchestrator import Orchestrator
from siamang.planner import Planner, PlanningRequest
from siamang.storage import Database
from siamang.storage.events import EventStore
from siamang.tasks import TaskEngine
from siamang.tools.registry import ToolRegistry
from tests.support.agents import make_world

SRC = Path(__file__).parents[2] / "src" / "siamang"
PLANNER = SRC / "planner"

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "asyncio", "threading", "multiprocessing",
                     "concurrent", "random", "secrets", "uuid", "time", "datetime", "pickle", "marshal", "ctypes",
                     "runpy", "dotenv", "git", "pygit2", "dulwich", "openai", "anthropic", "ollama", "requests",
                     "httpx", "langchain", "langgraph", "crewai", "autogen"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.git", "siamang.models", "siamang.tools", "siamang.agents",
                     "siamang.orchestrator", "siamang.config", "siamang.bus", "siamang.cli")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"Database", "EventStore", "TaskRepository", "GitClient", "GitWorkspaceManager", "ToolRegistry",
                   "ModelRouter", "ModelProvider", "AgentRegistry", "Orchestrator", "import_module", "system",
                   "popen", "Popen", "environ", "getenv", "listdir", "scandir", "walk", "iterdir", "rglob", "glob",
                   "connect"}
# Task Engine mutators and execution entry points.
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "execute", "invoke",
                   "generate", "select", "run", "transaction", "register", "archive"}


def violations(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES or a.name.startswith(FORBIDDEN_SIAMANG):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES or node.module.startswith(FORBIDDEN_SIAMANG):
                found.add(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS | FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
            found.add(f"call:{node.func.attr}")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            found.add("async")
    return found


@pytest.mark.parametrize("path", sorted(PLANNER.glob("*.py")), ids=lambda p: p.name)
def test_planner_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import socket", "import sqlite3", "import importlib",
    "import glob", "import shutil", "import random", "import uuid", "import time", "import asyncio",
    "eval('x')", "exec('x')", "compile('x', 'f', 'exec')", "open('/etc/passwd')", "fn = getattr(mod, name)",
    "os.listdir('.')", "os.walk('.')", "Path('.').rglob('*.py')", "subprocess.run(['git', 'status'])",
    "from siamang.git.client import GitClient", "GitClient().run(['status'])",
    "from siamang.git.workspace import GitWorkspaceManager", "from siamang.tools.registry import ToolRegistry",
    "registry.invoke('git.status', ctx, {})", "from siamang.models.provider import ModelProvider",
    "selection.generate(request)", "from siamang.agents.registry import AgentRegistry",
    "from siamang.orchestrator import Orchestrator", "orchestrator.execute(request)",
    "from siamang.storage import Database", "Database('x.db')", "EventStore(db).append(event)",
    "self._tasks.start(task_id)", "self._tasks.complete(task_id, r)", "self._tasks.fail(task_id, e)",
    "self._tasks.cancel(task_id)", "self._tasks.retry(task_id)", "self._tasks.create(request)",
    "self._projects.archive(project_id)", "import langchain", "async def plan(self): pass",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


# --- runtime --------------------------------------------------------------------------


@pytest.fixture
def tripwires(monkeypatch):
    """Call the returned function after setup; from then on every escape route raises and is recorded."""
    calls = []
    routes = [(subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen"), (os, "scandir"),
              (os, "listdir"), (os, "walk"), (os, "getenv"), (socket.socket, "connect"),
              (client_mod.GitClient, "run"), (ToolRegistry, "invoke"), (ModelRouter, "select"),
              (Selection, "generate"), (ModelProvider, "generate"), (AgentRegistry, "invoke"),
              (Orchestrator, "execute"), (Database, "transaction"), (EventStore, "append"),
              (TaskEngine, "create"), (TaskEngine, "start"), (TaskEngine, "wait"), (TaskEngine, "requeue"),
              (TaskEngine, "complete"), (TaskEngine, "fail"), (TaskEngine, "cancel"), (TaskEngine, "retry")]

    def trip(name):
        def f(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during planning")
        return f

    def arm():
        for target, attr in routes:
            monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))
        return calls

    return arm


def test_planning_touches_nothing_outside_its_boundary(db, tripwires):
    world = make_world(db, start=False)
    planner = Planner(world.engine)
    good = PlanningRequest(world.task.task_id, world.project.project_id,
                           "run rm -rf /; open /etc/passwd; execute shell; call git push")
    bad = PlanningRequest("tsk_01UNKNOWN", world.project.project_id, "x")
    calls = tripwires()
    assert len(planner.plan(good).steps) == 4
    with pytest.raises(Exception):
        planner.plan(bad)
    assert calls == []


def test_no_file_is_opened_while_planning(db, monkeypatch):
    import builtins
    import io

    world = make_world(db, start=False)
    planner = Planner(world.engine)
    request = PlanningRequest(world.task.task_id, world.project.project_id, "Read /etc/passwd please")
    opened = []

    def no_open(*args, **kwargs):
        opened.append(args[:1])
        raise AssertionError("file opened during planning")

    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr(io, "open", no_open)
    try:
        plan = planner.plan(request)
    finally:
        monkeypatch.undo()  # restore before pytest itself needs files
    assert opened == [] and len(plan.steps) == 4


def test_no_secret_or_instruction_in_logs(db, caplog):
    import logging

    world = make_world(db, start=False)
    with caplog.at_level(logging.DEBUG, logger="siamang"):
        Planner(world.engine).plan(PlanningRequest(world.task.task_id, world.project.project_id,
                                                   "rahasia-INSTRUKSI", input={"note": "rahasia-INPUT"},
                                                   metadata={"m": "rahasia-META"}))
    assert "rahasia" not in caplog.text
    assert world.task.task_id in caplog.text and "steps=4" in caplog.text
