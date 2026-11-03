"""Agent boundary security: no callable injection, dynamic loading, process, filesystem, network,
raw Git, raw database, or agent-to-agent path; an invocation touches nothing it was not given."""

import ast
import os
import socket
import subprocess
from pathlib import Path

import pytest

from siamang.agents import AgentRegistry, build_agent_registry, build_context
from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS
from siamang.agents.reference import EchoAgent
from siamang.git import client as client_mod
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter, Selection
from siamang.storage import Database
from siamang.storage.events import EventStore
from siamang.tasks import TaskEngine
from siamang.tools.registry import ToolRegistry
from tests.support.agents import make_world, request

SRC = Path(__file__).parents[2] / "src" / "siamang"
AGENTS = SRC / "agents"
AGENT_MODULES = sorted(AGENTS.glob("*.py"))
# Modules that hold agent implementations (bound through the allowlist).
IMPLEMENTATION_MODULES = [AGENTS / "reference.py"]

FORBIDDEN_MODULES = {"subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "shutil", "glob",
                     "pathlib", "os", "asyncio", "multiprocessing", "concurrent", "threading", "pty", "importlib",
                     "pkgutil", "inspect", "pickle", "marshal", "ctypes", "runpy", "sqlite3", "dotenv", "git",
                     "pygit2", "dulwich", "openai", "anthropic", "ollama", "requests", "httpx"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.git", "siamang.config", "siamang.cli", "siamang.bus",
                     "siamang.tasks.engine", "siamang.models.providers", "siamang.models.factory",
                     "siamang.models.credentials", "siamang.models.provider", "siamang.tools.git_tools",
                     "siamang.tools.implementations")
# Builtins that load or run code, or dispatch by string: forbidden as bare names.
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"import_module", "system", "popen", "Popen", "environ", "getenv", "putenv", "listdir",
                   "scandir", "walk", "iterdir", "rglob", "glob", "load_dotenv", "GitClient", "Database",
                   "connect", "EventStore", "TaskEngine", "TaskRepository"}


def violations(source: str, *, implementation: bool = False) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES or a.name.startswith(FORBIDDEN_SIAMANG):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES or node.module.startswith(FORBIDDEN_SIAMANG):
                found.add(node.module)
            if implementation and node.module.startswith(("siamang.agents.registry", "siamang.agents.implementations",
                                                          "siamang.models", "siamang.tools", "siamang.projects",
                                                          "siamang.tasks")):
                found.add(node.module)  # implementations get everything through the context
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS | FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                found.add(node.attr)
            if implementation and node.attr.startswith("_") and not node.attr.startswith("__"):
                found.add(f"private:{node.attr}")  # no reaching behind a facade
            if implementation and node.attr in ("invoke", "AgentRegistry", "build_agent_registry"):
                found.add(node.attr)
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            found.add("async")
    return found


@pytest.mark.parametrize("path", AGENT_MODULES, ids=lambda p: p.name)
def test_agent_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8"), implementation=path in IMPLEMENTATION_MODULES) == set()


@pytest.mark.parametrize("snippet", [
    "import importlib",
    "from importlib import import_module",
    "mod = __import__('siamang.agents.x')",
    "fn = getattr(module, name)",
    "eval('Agent()')",
    "exec('import os')",
    "compile('x', 'f', 'exec')",
    "import subprocess",
    "subprocess.Popen(['git'])",
    "os.system('id')",
    "import socket",
    "import urllib.request",
    "os.scandir('.')",
    "pathlib.Path('.').rglob('*.py')",
    "open('.env')",
    "token = os.environ['API_KEY']",
    "os.getenv('OPENAI_API_KEY')",
    "from siamang.git.client import GitClient",
    "GitClient().run(['status'])",
    "from siamang.storage import Database",
    "Database('x.db')",
    "import sqlite3",
    "from siamang.tasks.engine import TaskEngine",
    "async def run(self): pass",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


@pytest.mark.parametrize("snippet", [
    "from siamang.agents.registry import AgentRegistry",  # agent-to-agent
    "context.models._router.select(r)",  # reaching behind the facade
    "context.tools._registry.invoke('git.status', ctx, {})",
    "registry.invoke('agent.echo', request, context)",
    "from siamang.models.router import ModelRouter",
    "from siamang.tools.registry import ToolRegistry",
])
def test_implementation_rules_catch_deliberate_breaks(snippet):
    assert violations(snippet, implementation=True)


def test_allowlist_is_fixed():
    assert dict(KNOWN_IMPLEMENTATIONS) == {"reference.echo": EchoAgent}
    with pytest.raises(TypeError):
        KNOWN_IMPLEMENTATIONS["evil.handler"] = object  # read-only mapping


# --- runtime: one invocation, every escape route wired to a tripwire ---------------------


@pytest.fixture
def tripwires(monkeypatch):
    """Call ``arm()`` after setup; from then on every escape route raises and is recorded."""
    calls = []

    def trip(name):
        def f(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during agent invocation")
        return f

    routes = [(subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen"), (os, "scandir"),
              (os, "listdir"), (os, "getenv"), (socket.socket, "connect"), (client_mod.GitClient, "run"),
              (ToolRegistry, "invoke"), (ModelRouter, "select"), (Selection, "generate"),
              (ModelProvider, "generate"), (Database, "transaction"), (EventStore, "append"),
              (TaskEngine, "start"), (TaskEngine, "complete"), (TaskEngine, "fail"), (TaskEngine, "wait"),
              (TaskEngine, "cancel"), (TaskEngine, "retry")]

    def arm():
        for target, attr in routes:
            monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))
        return calls

    return arm


def test_echo_invocation_touches_nothing(db, tripwires):
    world = make_world(db)
    registry = build_agent_registry()
    ctx = build_context(registry.get("agent.echo"), task=world.task, projects=world.projects)
    calls = tripwires()
    result = registry.invoke("agent.echo", request(world), ctx)
    assert result.success and calls == []


def test_failed_invocations_touch_nothing(db, tripwires):
    world = make_world(db)
    registry = build_agent_registry()
    ctx = build_context(registry.get("agent.echo"), task=world.task, projects=world.projects)
    reqs = (("agent.nope", request(world)), ("agent.echo", request(world, input={})), ("agent.echo", None))
    calls = tripwires()
    for agent_id, req in reqs:
        assert not registry.invoke(agent_id, req, ctx).success
    assert calls == []


def test_registry_has_no_dynamic_loading_api():
    names = {n for n in dir(AgentRegistry) if not n.startswith("_")}
    assert names == {"register", "get", "exists", "list", "invoke"}
    for word in ("load", "discover", "scan", "import", "plugin", "handler"):
        assert not any(word in n for n in dir(AgentRegistry)), word


def test_tripwires_catch_a_misbehaving_agent(db, tripwires, monkeypatch):
    """Deliberate break: an agent that shells out is caught, and its failure is sanitized."""
    from tests.support.agents import allow_probe, definition

    def behavior(agent, req, ctx):
        os.system("id")

    allow_probe(monkeypatch, behavior)
    world = make_world(db)
    registry = AgentRegistry()
    d = registry.register(definition())
    ctx = build_context(d, task=world.task, projects=world.projects)
    calls = tripwires()
    result = registry.invoke("agent.probe", request(world), ctx)
    assert calls == ["os.system"]
    assert result.error.code == "agent_execution_failed" and result.error.cause == "AssertionError"
