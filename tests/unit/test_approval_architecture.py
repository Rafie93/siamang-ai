"""Dependency direction: CLI -> approval -> {immutable artifacts of Phases 13-18, their pure rules, tasks}.

    Task + Plan + SystemAnalysis + LoopResult(final Patch, QAResult, ReviewResult) ──► Approval ──► ApprovalResult ──X

Approval is the downstream authorization boundary: it consumes artifact
types and pure validation/judgment functions, never a service (Programmer,
QA, Reviewer, Loop, Planner, Analyst), a model, an agent, the Orchestrator,
tools, Git, storage, events, or the project tree. Nothing upstream imports it.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
APPROVAL = SRC / "approval"
FUTURE = ("api", "websocket", "telegram", "channels", "workflow", "scheduler", "sandbox", "runner",
          "unity", "events", "delivery", "executor", "execution", "deploy", "applier", "auth", "authentication")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_approval_dependencies_are_limited():
    allowed = {
        "siamang.analyst.analysis", "siamang.planner.plan", "siamang.planner.request",
        "siamang.programmer.change", "siamang.programmer.patch", "siamang.programmer.safety",
        "siamang.qa.checks", "siamang.qa.policy", "siamang.qa.request", "siamang.qa.result",
        "siamang.reviewer.checks", "siamang.reviewer.policy", "siamang.reviewer.result",
        "siamang.loop.iteration", "siamang.loop.result",
        "siamang.tasks.definition", "siamang.tasks.engine", "siamang.tasks.errors", "siamang.tasks.lifecycle",
        "siamang.projects", "siamang.ids", "siamang.log",
    } | {f"siamang.approval.{p.stem}" for p in APPROVAL.glob("*.py")}
    for path in APPROVAL.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_approval_never_reaches_services_models_or_execution():
    for path in APPROVAL.glob("*.py"):
        for m in _imports(path):
            assert not m.endswith(".service") or m.startswith("siamang.approval"), (path.name, m)
            assert not m.startswith((
                "siamang.models", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.storage",
                "siamang.bus", "siamang.contracts", "siamang.config", "siamang.cli", "siamang.git",
                "siamang.programmer.access", "siamang.programmer.strategy", "siamang.programmer.context",
                "siamang.programmer.snapshot", "siamang.analyst.inspection", "siamang.analyst.git_state",
                "siamang.planner.strategy", "siamang.loop.feedback",
            )), (path.name, m)


def test_nothing_upstream_imports_approval():
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if not p.is_relative_to(APPROVAL) and any(m.startswith("siamang.approval") for m in _imports(p))}
    assert users == {'api/app.py','api/runtime.py', "cli/main.py", "cli/checks.py"}
    for layer in ("programmer", "qa", "reviewer", "loop", "planner", "analyst", "agents", "orchestrator", "tasks",
                  "projects", "models", "tools", "git", "storage", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.approval") for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name in ("api", "sandbox", "executor"):  # Phase 20 transport, 25 sandbox, 27 executor; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    assert {p.stem for p in APPROVAL.glob("*.py")} == {"__init__", "errors", "policy", "decision", "provenance",
                                                       "request", "result", "service"}


def test_approval_is_not_an_agent_and_not_orchestrated():
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS
    from siamang.approval import ApprovalService

    assert not any("approv" in impl.split(".")[-1] for impl in KNOWN_IMPLEMENTATIONS)
    assert ApprovalService.__mro__ == (ApprovalService, object)


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "approval_result" not in sql.read_text().lower(), sql.name
    # the contract's approval.* events predate Phase 19 and are not emitted here (no bus/contracts import)
    assert {t.value for t in EventType if t.value.startswith("approval.")} == {
        "approval.required", "approval.granted", "approval.rejected"}


def test_api_has_only_explicit_decide_and_verify():
    from siamang.approval import ApprovalResult, ApprovalService

    assert {n for n in dir(ApprovalService) if not n.startswith("_")} == {"decide", "verify", "policy"}
    for cls in (ApprovalService, ApprovalResult):
        for name in dir(cls):
            low = name.lower()
            assert not any(w in low for w in ("apply", "commit", "merge", "deploy", "execute", "force", "override",
                                              "skip", "auto")), name


def test_synchronous_and_stateless():
    for path in APPROVAL.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await, ast.Global, ast.Nonlocal))
                       for n in ast.walk(tree)), path.name
    service = ast.parse((APPROVAL / "service.py").read_text())
    assigned = {t.attr for n in ast.walk(service) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                for t in ([n.target] if not isinstance(n, ast.Assign) else n.targets)
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"}
    assert assigned == {"_tasks", "_seal_key", "_policy"}  # set once, in __init__ only


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
