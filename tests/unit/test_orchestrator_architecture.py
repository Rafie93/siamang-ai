"""Dependency direction: CLI -> orchestrator -> {agents registry, tasks, projects}; never the reverse.

    Task (running, via TaskEngine)
              │
              ▼
        Orchestrator ──► TaskEngine.get / ProjectRegistry.get (read only)
              │
              ▼
        AgentRegistry ──► Agent ──► ModelAccess / ToolAccess
"""

import ast
from pathlib import Path

from siamang.contracts import EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
ORCH = SRC / "orchestrator"
FUTURE = ("planner", "analyst", "system_analyst", "programmer", "qa", "reviewer", "approval", "api", "websocket",
          "telegram", "channels", "workflow", "scheduler")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_orchestrator_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.agents.context",  # build_context (Phase 11 public boundary)
        "siamang.agents.definition",  # AGENT_ID_RE
        "siamang.agents.errors",
        "siamang.agents.registry",
        "siamang.agents.request",
        "siamang.agents.result",
        "siamang.ids",
        "siamang.log",
        "siamang.projects",
        "siamang.tasks.definition",
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.orchestrator.{p.stem}" for p in ORCH.glob("*.py")}
    for path in ORCH.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_orchestrator_never_reaches_below_the_agent_layer():
    for path in ORCH.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.storage", "siamang.git", "siamang.models", "siamang.tools",
                                     "siamang.config", "siamang.bus", "siamang.cli",
                                     "siamang.agents.implementations", "siamang.agents.reference")), (path.name, m)


def test_orchestrator_does_not_depend_on_future_layers():
    for path in ORCH.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(tuple(f"siamang.{f}" for f in FUTURE)), (path.name, m)


def test_no_future_layers_exist():
    for name in FUTURE:
        if name == "api":  # Phase 20 transport; upstream imports remain forbidden
            continue
        if name in ("planner", "analyst", "programmer", "qa", "reviewer", "approval"):  # Phases 13-19; the orchestrator may not
            # import them
            continue
        assert not (SRC / name).exists(), name
    assert {p.stem for p in ORCH.glob("*.py")} == {"__init__", "errors", "policy", "request", "result", "service"}


def test_only_the_cli_imports_the_orchestrator():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(ORCH) and any(m.startswith("siamang.orchestrator") for m in _imports(p))
    }
    assert users == {"cli/main.py", "cli/checks.py"}


def test_lower_layers_never_import_the_orchestrator():
    for layer in ("agents", "tasks", "projects", "models", "tools", "storage", "git", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.orchestrator") for m in _imports(path)), path


def test_no_orchestration_persistence_or_events():
    assert SCHEMA_VERSION == 3  # Phase 12 adds no schema step
    for sql in (SRC / "storage").glob("*.sql"):
        assert "orchestr" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if "orchestr" in t.value]


def test_synchronous_only():
    for path in ORCH.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_public_api_has_no_run_loop_or_plan():
    from siamang.orchestrator import Orchestrator

    names = {n for n in dir(Orchestrator) if not n.startswith("_")}
    assert names == {"execute"}
