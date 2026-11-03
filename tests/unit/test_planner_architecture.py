"""Dependency direction: CLI -> planner -> {tasks, projects}; the planner is upstream of execution.

    Task ──► Planner ──► Plan ──(later)──► Orchestrator ──► AgentRegistry ──► Agent
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
PLANNER = SRC / "planner"
FUTURE = ("analyst", "system_analyst", "programmer", "qa", "reviewer", "approval", "api", "websocket", "telegram",
          "channels", "workflow", "scheduler")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_planner_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.ids",
        "siamang.log",
        "siamang.projects",
        "siamang.projects.definition",  # freeze_metadata (shared metadata policy)
        "siamang.tasks.definition",  # ID validators, metadata/key policy, Task snapshot type
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.planner.{p.stem}" for p in PLANNER.glob("*.py")}
    for path in PLANNER.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_planner_is_upstream_of_execution():
    for path in PLANNER.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.models",
                                     "siamang.git", "siamang.storage", "siamang.bus", "siamang.config",
                                     "siamang.cli", "siamang.programmer", "siamang.qa",
                                     "siamang.reviewer")), (path.name, m)


def test_execution_layers_do_not_import_the_planner():
    for layer in ("agents", "orchestrator", "tasks", "projects", "models", "tools", "storage", "git", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.planner") for m in _imports(path)), path


def test_only_the_cli_and_analyst_import_the_planner():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(PLANNER) and any(m.startswith("siamang.planner") for m in _imports(p))
    }
    # Phase 14: the analyst reuses the planner's validation helpers and accepts a Plan as data (never calls it).
    assert users == {'api/codec.py','api/runtime.py', "cli/main.py", "cli/checks.py", "analyst/analysis.py", "analyst/component.py",
                     "analyst/finding.py", "analyst/policy.py", "analyst/request.py",
                     # Phase 15: the programmer reuses the same helpers and consumes a Plan as data (never calls it).
                     "programmer/change.py", "programmer/context.py", "programmer/patch.py", "programmer/policy.py",
                     "programmer/request.py", "programmer/result.py", "programmer/safety.py",
                     "programmer/service.py",
                     # Phase 16: QA reuses the same helpers and consumes a Plan as data (never calls it).
                     "qa/checks.py", "qa/finding.py", "qa/request.py", "qa/result.py", "qa/service.py",
                     # Phase 17: the Reviewer reuses the same helpers and consumes a Plan as data (never calls it).
                     "reviewer/checks.py", "reviewer/finding.py", "reviewer/request.py", "reviewer/result.py",
                     "reviewer/service.py",
                     # Phase 18: the Loop reuses the same helpers and passes a Plan through as data.
                     "loop/feedback.py", "loop/request.py", "loop/result.py",
                     # Phase 19: Approval reuses the text helpers and re-validates a Plan as data.
                     "approval/decision.py", "approval/provenance.py", "approval/request.py"}


def test_no_future_layers_exist():
    for name in FUTURE:
        if name in ("api", "sandbox"):  # Phase 20 transport, Phase 25 sandbox; upstream imports remain forbidden
            continue
        if name in ("analyst", "programmer", "qa", "reviewer", "approval"):  # Phases 14-19; the planner may not import them
            continue
        assert not (SRC / name).exists(), name
    assert {p.stem for p in PLANNER.glob("*.py")} == {"__init__", "errors", "request", "step", "plan", "policy",
                                                      "strategy", "service"}


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3
    assert CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "plan" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if "plan" in t.value]


def test_synchronous_only():
    for path in PLANNER.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_public_api_is_plan_only():
    from siamang.planner import Planner

    assert {n for n in dir(Planner) if not n.startswith("_")} == {"plan", "strategy_id"}
