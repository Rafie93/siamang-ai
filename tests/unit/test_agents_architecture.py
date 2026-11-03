"""Dependency direction: CLI -> agents -> {tasks domain, projects, models router, tools registry}; never the reverse.

    (future Orchestrator, Phase 12)
                │ invoke(agent_id, AgentRequest, AgentContext)
                ▼
             agents ──► tasks (Task snapshot, IDs, TaskStatus; never the engine)
                │ ├──► projects (ProjectRegistry, metadata policy)
                │ ├──► models (ModelRouter, ModelRequest/Response)
                │ └──► tools (ToolRegistry, PermissionPolicy, ToolContext)
                ▼
     only cli/main.py and cli/checks.py import agents
"""

import ast
from pathlib import Path

from siamang.agents import Agent
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
AGENTS = SRC / "agents"
FUTURE = ("orchestrator", "orchestration", "planner", "analyst", "system_analyst", "programmer", "qa", "reviewer",
          "approval", "api", "websocket", "telegram", "channels")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_agents_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.contracts.schema",
        "siamang.log",
        "siamang.models.capabilities",
        "siamang.models.errors",
        "siamang.models.request",
        "siamang.models.response",
        "siamang.models.router",
        "siamang.projects",
        "siamang.projects.definition",
        "siamang.projects.errors",
        "siamang.tasks.definition",
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
        "siamang.tools.definition",
        "siamang.tools.errors",
        "siamang.tools.permissions",
        "siamang.tools.registry",
    } | {f"siamang.agents.{p.stem}" for p in AGENTS.glob("*.py")}
    for path in AGENTS.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_agents_never_reach_storage_git_engine_or_providers():
    for path in AGENTS.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.storage", "siamang.git", "siamang.tasks.engine", "siamang.bus",
                                     "siamang.config", "siamang.cli", "siamang.models.providers",
                                     "siamang.models.provider", "siamang.models.factory")), (path.name, m)


def test_agents_do_not_depend_on_future_layers():
    for path in AGENTS.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(tuple(f"siamang.{f}" for f in FUTURE)), (path.name, m)


def test_no_future_layers_or_specialized_agents_exist():
    for name in FUTURE:
        if name in ("api", "sandbox"):  # Phase 20 transport, Phase 25 sandbox; upstream imports remain forbidden
            continue
        if name in ("orchestrator", "planner", "analyst", "programmer", "qa", "reviewer", "approval"):  # Phases 12-19;
            # agents may not import them
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    stems = {p.stem for p in AGENTS.glob("*.py")}
    assert stems == {"__init__", "context", "definition", "errors", "implementations", "interface", "reference",
                     "registry", "request", "result"}
    for path in AGENTS.glob("*.py"):
        classes = {n.name.lower() for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.ClassDef)}
        for word in ("analyst", "programmer", "qaagent", "reviewer", "planner", "orchestrator", "telegram",
                     "coding", "autonomous"):
            assert not any(word in c for c in classes), (path.name, word)


def test_only_the_cli_and_orchestrator_import_agents():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(AGENTS) and any(m.startswith("siamang.agents") for m in _imports(p))
    }
    # Phase 12: the orchestrator resolves and invokes agents through the AgentRegistry.
    assert users == {"cli/main.py", "cli/checks.py", "orchestrator/policy.py", "orchestrator/request.py",
                     "orchestrator/result.py", "orchestrator/service.py"}


def test_lower_layers_never_import_agents():
    for layer in ("projects", "tasks", "models", "tools", "storage", "git", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.agents") for m in _imports(path)), path


def test_no_agent_persistence():
    assert SCHEMA_VERSION == 3  # Phase 11 adds no schema step
    for sql in (SRC / "storage").glob("*.sql"):
        assert "agent" not in sql.read_text().lower(), sql.name


def test_interface_is_synchronous_and_minimal():
    assert Agent.__abstractmethods__ == frozenset({"run"})
    for path in AGENTS.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_no_loop_calls_run():
    """Agent.run is called in exactly one place (AgentRegistry._run), never in a loop."""
    calls = []
    for path in AGENTS.glob("*.py"):
        tree = ast.parse(path.read_text())
        for loop in (n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While))):
            for node in ast.walk(loop):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                    calls.append((path.name, "in loop"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                calls.append((path.name, node.lineno))
    assert len(calls) == 1 and calls[0][0] == "registry.py"
