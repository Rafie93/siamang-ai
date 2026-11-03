"""Dependency direction: CLI -> loop -> {Programmer, QA, Reviewer (services + domain), tasks}; never the reverse.

    LoopService ──► Programmer.program ──► QAService.verify ──► ReviewerService.review ──► LoopResult ──X── (future) Approval

Programmer, QA, and Reviewer never import the loop, and none of them
imports another's service: the only path between them runs through the
loop. The loop never reaches models, agents, the Orchestrator, tools, Git,
storage, events, the project registry, or the project tree.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
LOOP = SRC / "loop"
FUTURE = ("api", "websocket", "telegram", "channels", "workflow", "scheduler",
          "sandbox", "runner", "unity", "events", "delivery", "executor", "deploy")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_loop_dependencies_are_limited():
    allowed = {
        "siamang.analyst.analysis", "siamang.planner.plan",  # passed through as immutable data
        "siamang.planner.request",  # shared text/JSON validation helpers
        "siamang.ids", "siamang.log",
        "siamang.programmer.service", "siamang.programmer.request", "siamang.programmer.result",
        "siamang.programmer.patch", "siamang.programmer.errors", "siamang.programmer.safety",
        "siamang.qa.service", "siamang.qa.request", "siamang.qa.result", "siamang.qa.errors", "siamang.qa.checks",
        "siamang.qa.finding",
        "siamang.reviewer.service", "siamang.reviewer.request", "siamang.reviewer.result", "siamang.reviewer.errors",
        "siamang.tasks.engine", "siamang.tasks.errors", "siamang.tasks.definition",
    } | {f"siamang.loop.{p.stem}" for p in LOOP.glob("*.py")}
    for path in LOOP.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_only_the_service_module_touches_services():
    for path in LOOP.glob("*.py"):
        services = {m for m in _imports(path) if m.endswith(".service") and not m.startswith("siamang.loop")}
        if path.name == "service.py":
            assert services == {"siamang.programmer.service", "siamang.qa.service", "siamang.reviewer.service"}
        else:
            assert services == set(), path.name


def test_loop_never_reaches_models_agents_execution_or_the_project():
    for path in LOOP.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith((
                "siamang.models", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.storage",
                "siamang.bus", "siamang.contracts", "siamang.config", "siamang.cli", "siamang.git",
                "siamang.projects", "siamang.planner.service", "siamang.planner.strategy", "siamang.analyst.service",
                "siamang.analyst.inspection", "siamang.analyst.git_state", "siamang.programmer.access",
                "siamang.programmer.strategy", "siamang.programmer.context", "siamang.programmer.snapshot",
            )), (path.name, m)


def test_no_reverse_or_circular_dependency():
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if not p.is_relative_to(LOOP) and any(m.startswith("siamang.loop") for m in _imports(p))}
    # Phase 19: Approval consumes a LoopResult as data (never runs the loop; see approval architecture tests).
    assert users == {'api/codec.py','api/runtime.py','api/parallel.py', "cli/main.py", "cli/checks.py", "approval/provenance.py", "approval/request.py"}
    for layer in ("programmer", "qa", "reviewer", "analyst", "planner", "agents", "orchestrator", "tasks",
                  "projects", "models", "tools", "git", "storage", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.loop") for m in _imports(path)), path
    # the components stay unaware of each other's services: only the loop connects them
    for layer, others in (("programmer", ("siamang.qa", "siamang.reviewer")),
                          ("qa", ("siamang.reviewer", "siamang.programmer.service")),
                          ("reviewer", ("siamang.programmer.service", "siamang.qa.service"))):
        for path in (SRC / layer).glob("*.py"):
            assert not any(m.startswith(others) for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name in ("api", "sandbox"):  # Phase 20 transport, Phase 25 sandbox; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    assert {p.stem for p in LOOP.glob("*.py")} == {"__init__", "errors", "policy", "feedback", "iteration",
                                                   "request", "result", "service"}


def test_loop_is_not_an_agent_and_not_orchestrated():
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS
    from siamang.loop import LoopService

    assert not any("loop" in impl.split(".")[-1] for impl in KNOWN_IMPLEMENTATIONS)
    assert LoopService.__mro__ == (LoopService, object)


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "loop" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if "loop" in t.value or "iteration" in t.value]


def test_no_approval_apply_or_agent_vocabulary_in_the_api():
    from siamang.loop import LoopResult, LoopService, LoopStatus, StopReason

    assert {n for n in dir(LoopService) if not n.startswith("_")} == {"run"}
    assert {s.value for s in LoopStatus} == {"pass", "concern", "fail", "inconclusive"}
    words = ("approve", "approved", "reject", "apply", "applied", "merge", "commit", "deploy", "execute", "act",
             "run_tool", "token")
    assert not any(w in r.value for r in StopReason for w in words)
    for cls in (LoopService, LoopResult):
        for name in dir(cls):
            if not name.startswith("__"):
                assert not any(w == name.lower().strip("_") or name.lower().startswith(w + "_")
                               for w in words), name


def test_synchronous_and_stateless_service():
    for path in LOOP.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await, ast.Global, ast.Nonlocal))
                       for n in ast.walk(tree)), path.name
    service = ast.parse((LOOP / "service.py").read_text())
    assigned = {t.attr for n in ast.walk(service) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                for t in ([n.target] if not isinstance(n, ast.Assign) else n.targets)
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"}
    assert assigned == {"_tasks", "_programmer", "_qa", "_reviewer", "policy"}  # set once, in __init__ only


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
