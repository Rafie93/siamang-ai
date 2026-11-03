"""Orchestrator boundary security: coordination only. No process, filesystem, network, database,
EventStore, raw Git, provider, direct ToolRegistry/model call, task mutation, dynamic loading,
nesting, or more than one agent per execution."""

import ast
import os
import socket
import subprocess
from pathlib import Path

import pytest

from siamang.agents import AgentRegistry, AgentResult, build_agent_registry, build_context
from siamang.git import client as client_mod
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter, Selection
from siamang.orchestrator import OrchestrationRequest, Orchestrator
from siamang.storage import Database
from siamang.storage.events import EventStore
from siamang.tasks import TaskEngine
from siamang.tools.registry import ToolRegistry
from tests.support.agents import allow_probe, definition, make_world, request

SRC = Path(__file__).parents[2] / "src" / "siamang"
ORCH = SRC / "orchestrator"
SERVICE = ORCH / "service.py"

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "asyncio", "threading", "multiprocessing",
                     "concurrent", "queue", "sched", "pickle", "marshal", "ctypes", "runpy", "dotenv", "git",
                     "pygit2", "dulwich", "openai", "anthropic", "ollama", "requests", "httpx"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.git", "siamang.config", "siamang.bus", "siamang.models",
                     "siamang.tools", "siamang.cli", "siamang.agents.implementations", "siamang.agents.reference",
                     "siamang.agents.interface")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"Database", "EventStore", "TaskRepository", "GitClient", "GitWorkspaceManager", "ToolRegistry",
                   "ModelRouter", "ModelProvider", "Selection", "EchoAgent", "KNOWN_IMPLEMENTATIONS",
                   "import_module", "system", "popen", "Popen", "environ", "getenv", "listdir", "scandir",
                   "iterdir", "rglob", "connect"}
# Task Engine mutators, model/tool calls, direct agent runs, event appends.
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "generate",
                   "select", "run", "append", "transaction", "execute_tool"}


def violations(source: str) -> set[str]:
    found = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
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
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "invoke":
            target = node.func.value  # only the AgentRegistry may be invoked
            if not (isinstance(target, ast.Attribute) and target.attr == "_agents"
                    and isinstance(target.value, ast.Name) and target.value.id == "self"):
                found.add("call:invoke-not-agent-registry")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            found.add("async")
    return found


def invoke_calls(source: str) -> int:
    return sum(1 for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "invoke")


def loops(source: str) -> list[int]:
    return [n.lineno for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.For, ast.While, ast.AsyncFor))]


# --- static ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(ORCH.glob("*.py")), ids=lambda p: p.name)
def test_orchestrator_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8")) == set()


def test_exactly_one_invocation_and_no_loop_in_the_service():
    source = SERVICE.read_text(encoding="utf-8")
    assert invoke_calls(source) == 1
    assert loops(source) == []


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import socket", "import sqlite3", "import importlib",
    "import threading", "import asyncio",
    "fn = getattr(module, name)", "eval('x')", "exec('x')", "__import__('os')", "open('.env')",
    "from siamang.storage import Database", "Database('x.db')", "from siamang.storage.events import EventStore",
    "store.append(event)", "from siamang.git.client import GitClient", "GitClient().run(['push'])",
    "from siamang.models.providers.echo import EchoProvider", "provider.generate(model, request)",
    "self._router.select(request)", "from siamang.tools.registry import ToolRegistry",
    "self._tools.invoke('git.status', ctx, {})",
    "self._tasks.start(task_id)", "self._tasks.complete(task_id, result)", "self._tasks.fail(task_id, err)",
    "self._tasks.wait(task_id)", "self._tasks.cancel(task_id)", "self._tasks.retry(task_id)",
    "from siamang.agents.reference import EchoAgent", "EchoAgent(definition)", "agent.run(request, context)",
    "from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS",
    "async def execute(self): pass",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


def test_loop_and_chaining_checks_catch_deliberate_breaks():
    chained = "def f(self):\n    a = self._agents.invoke(x, r, c)\n    b = self._agents.invoke(y, r, c)\n"
    assert invoke_calls(chained) == 2
    assert loops("while not done:\n    result = self._agents.invoke(a, r, c)\n") == [1]
    assert loops("for agent_id in plan:\n    self._agents.invoke(agent_id, r, c)\n") == [1]


# --- runtime --------------------------------------------------------------------------


@pytest.fixture
def tripwires(monkeypatch):
    """Call the returned function after setup; from then on every escape route raises and is recorded."""
    calls = []
    routes = [(subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen"), (os, "scandir"),
              (os, "listdir"), (os, "getenv"), (socket.socket, "connect"), (client_mod.GitClient, "run"),
              (ToolRegistry, "invoke"), (ModelRouter, "select"), (Selection, "generate"),
              (ModelProvider, "generate"), (Database, "transaction"), (EventStore, "append"),
              (TaskEngine, "create"), (TaskEngine, "start"), (TaskEngine, "wait"), (TaskEngine, "requeue"),
              (TaskEngine, "complete"), (TaskEngine, "fail"), (TaskEngine, "cancel"), (TaskEngine, "retry")]

    def trip(name):
        def f(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during orchestration")
        return f

    def arm():
        for target, attr in routes:
            monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))
        return calls

    return arm


def oreq(world, **over):
    data = dict(task_id=world.task.task_id, project_id=world.project.project_id, agent_id="agent.echo",
                instruction="Ulangi", input={"message": "hello"})
    data.update(over)
    return OrchestrationRequest(**data)


def test_orchestration_touches_nothing_outside_its_boundary(db, tripwires):
    world = make_world(db)
    orch = Orchestrator(world.engine, build_agent_registry())
    calls = tripwires()
    assert orch.execute(oreq(world)).success
    for bad in (oreq(world, agent_id="agent.nope"), oreq(world, input={}), None):
        assert not orch.execute(bad).success
    assert calls == []


def test_exactly_one_agent_invocation_per_execution(db, monkeypatch):
    world = make_world(db)
    agents = build_agent_registry()
    seen = []
    real = agents.invoke
    monkeypatch.setattr(agents, "invoke", lambda *a: seen.append(a[0]) or real(*a))
    orch = Orchestrator(world.engine, agents)
    orch.execute(oreq(world))
    orch.execute(oreq(world, input={}))  # failing agent: still exactly one call, no retry
    orch.execute(oreq(world, agent_id="agent.nope"))  # refused before invocation
    assert seen == ["agent.echo", "agent.echo"]


def test_request_cannot_name_several_agents():
    with pytest.raises(Exception):
        OrchestrationRequest.from_dict({"task_id": "tsk_01A", "project_id": "prj_01A", "agent_id": "agent.echo",
                                        "agents": ["agent.echo", "agent.echo"], "instruction": "x"})


# --- nesting: agents cannot call the orchestrator or each other ------------------------


def test_agent_cannot_call_the_orchestrator(db, monkeypatch):
    world = make_world(db)
    holder, inner = {}, []

    def behavior(agent, req, ctx):
        inner.append(holder["orch"].execute(oreq(world)))
        return AgentResult.completed(agent.agent_id, ctx.task_id, {"message": "done"})

    allow_probe(monkeypatch, behavior)
    agents = build_agent_registry()
    agents.register(definition())
    holder["orch"] = Orchestrator(world.engine, agents)
    outer = holder["orch"].execute(oreq(world, agent_id="agent.probe"))
    assert outer.success
    assert inner[0].error.code == "policy_denied" and "nested" in inner[0].error.message
    assert inner[0].agent_result is None


def test_agent_invoked_directly_still_cannot_reach_another_agent_via_the_orchestrator(db, monkeypatch):
    world = make_world(db)
    inner = []

    def behavior(agent, req, ctx):
        inner.append(Orchestrator(world.engine, build_agent_registry()).execute(oreq(world)))
        return AgentResult.completed(agent.agent_id, ctx.task_id, {"message": "done"})

    allow_probe(monkeypatch, behavior)
    agents = AgentRegistry()
    d = agents.register(definition())
    agents.invoke("agent.probe", request(world), build_context(d, task=world.task, projects=world.projects))
    assert inner[0].error.code == "agent_invocation_failed" and inner[0].error.cause == "agent_permission_denied"


def test_guard_is_released_after_a_crash(db, monkeypatch):
    world = make_world(db)
    agents = build_agent_registry()
    orch = Orchestrator(world.engine, agents)

    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(agents, "invoke", boom)
    assert orch.execute(oreq(world)).error.code == "internal"
    monkeypatch.undo()
    assert Orchestrator(world.engine, build_agent_registry()).execute(oreq(world)).success


def test_no_secret_or_input_in_logs(db, caplog):
    import logging

    world = make_world(db)
    orch = Orchestrator(world.engine, build_agent_registry())
    with caplog.at_level(logging.DEBUG, logger="siamang"):
        orch.execute(oreq(world, instruction="rahasia-INSTRUKSI", input={"message": "rahasia-INPUT"},
                          metadata={"note": "rahasia-META"}))
    text = caplog.text
    assert "rahasia" not in text
    assert world.task.task_id in text and "agent.echo" in text
