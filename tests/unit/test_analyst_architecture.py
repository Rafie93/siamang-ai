"""Dependency direction: CLI -> analyst -> {tasks, projects, planner (data/helpers), git.workspace}.

    Task / Plan ──► System Analyst ──► ProjectInspector (only os user) / GitWorkspaceManager (read-only)
                          │
                          ▼
                   SystemAnalysis ──(later)──► Programmer

Nothing downstream or beside it imports the analyst: planner, agents,
orchestrator, tools, models, git, storage, contracts, tasks, projects.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
ANALYST = SRC / "analyst"
FUTURE = ("api", "websocket", "telegram", "channels",
          "workflow", "scheduler")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_analyst_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.git.errors",  # GitError / ProtectedRepositoryError
        "siamang.git.workspace",  # GitWorkspaceManager, the read-only Git boundary (Phase 08)
        "siamang.ids",
        "siamang.log",
        "siamang.planner.plan",  # Plan accepted as immutable context
        "siamang.planner.request",  # shared text/JSON validation helpers
        "siamang.projects",
        "siamang.projects.definition",
        "siamang.tasks.definition",
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.analyst.{p.stem}" for p in ANALYST.glob("*.py")}
    for path in ANALYST.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_analyst_never_reaches_execution_layers():
    for path in ANALYST.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.models",
                                     "siamang.storage", "siamang.bus", "siamang.config", "siamang.cli",
                                     "siamang.git.client", "siamang.planner.service",
                                     "siamang.programmer", "siamang.qa", "siamang.reviewer")), (path.name, m)  # never
            # downstream


def test_git_only_through_the_workspace_manager():
    users = {p.name for p in ANALYST.glob("*.py") if any(m.startswith("siamang.git") for m in _imports(p))}
    assert users == {"git_state.py", "service.py"}


def test_nothing_else_imports_the_analyst():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(ANALYST) and any(m.startswith("siamang.analyst") for m in _imports(p))
    }
    # Phase 15: the programmer consumes a SystemAnalysis as data, reuses the request key rules and the
    # secret-name rule, and reads current files only through ProjectInspector. It never calls SystemAnalyst.
    assert users == {'api/codec.py','api/runtime.py','api/parallel.py', "cli/main.py", "cli/checks.py", "programmer/context.py", "programmer/request.py",
                     "programmer/safety.py", "programmer/service.py", "programmer/snapshot.py",
                     # Phase 16: QA consumes a SystemAnalysis as data (never calls SystemAnalyst).
                     "qa/checks.py", "qa/request.py", "qa/service.py",
                     # Phase 17: the Reviewer consumes a SystemAnalysis as data (never calls SystemAnalyst).
                     "reviewer/checks.py", "reviewer/request.py", "reviewer/result.py", "reviewer/service.py",
                     # Phase 18: the Loop passes a SystemAnalysis through as data (never calls SystemAnalyst).
                     "loop/request.py", "loop/result.py",
                     # Phase 19: Approval re-validates a SystemAnalysis as data (never calls SystemAnalyst).
                     "approval/provenance.py", "approval/request.py"}
    for layer in ("planner", "agents", "orchestrator", "tasks", "projects", "models", "tools", "git", "storage",
                  "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.analyst") for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name == "api":  # Phase 20 transport; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
    assert {p.stem for p in ANALYST.glob("*.py")} == {"__init__", "errors", "request", "finding", "component",
                                                      "analysis", "policy", "inspection", "git_state",
                                                      "strategy", "service"}


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "analys" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if "analys" in t.value]


def test_synchronous_only():
    for path in ANALYST.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_public_api_is_analyze_only():
    from siamang.analyst import SystemAnalyst

    assert {n for n in dir(SystemAnalyst) if not n.startswith("_")} == {"analyze", "strategy_id"}


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
