"""Dependency direction: CLI -> programmer -> {tasks, projects, planner/analyst (data), models (router only)}.

    Task + Plan + SystemAnalysis ──► Programmer ──► ProgrammerModelAccess ──► ModelRouter ──► ModelProvider
                                         │
                                         ├──► ProjectInspector (Phase 14, read-only; the only file access)
                                         ▼
                                   Proposed Patch ──X── (future) Approval System ──► Workspace

Plan and SystemAnalysis are data dependencies, never service calls. Nothing
downstream or beside the programmer imports it.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
PROGRAMMER = SRC / "programmer"
FUTURE = ("api", "websocket", "telegram", "channels", "workflow",
          "scheduler", "workspace_writer", "mutation")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_programmer_dependencies_are_limited():
    allowed = {
        "siamang.analyst.analysis",  # SystemAnalysis accepted as immutable input
        "siamang.analyst.errors",  # ProjectInspector's errors, translated at the boundary
        "siamang.analyst.inspection",  # ProjectInspector + is_sensitive: the read-only file boundary
        "siamang.analyst.policy",  # the inspector's budget type
        "siamang.analyst.request",  # shared request key rules
        "siamang.ids",
        "siamang.log",
        "siamang.models.capabilities",
        "siamang.models.errors",
        "siamang.models.request",
        "siamang.models.response",
        "siamang.models.router",  # the ModelRouter: the only way to a model
        "siamang.planner.plan",  # Plan accepted as immutable input
        "siamang.planner.request",  # shared text/JSON validation helpers
        "siamang.planner.step",  # step ID format
        "siamang.projects",
        "siamang.tasks.definition",
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.programmer.{p.stem}" for p in PROGRAMMER.glob("*.py")}
    for path in PROGRAMMER.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_programmer_never_reaches_execution_or_mutation_layers():
    for path in PROGRAMMER.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith((
                "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.storage", "siamang.bus",
                "siamang.config", "siamang.cli", "siamang.git", "siamang.contracts", "siamang.planner.service",
                "siamang.planner.strategy", "siamang.analyst.service", "siamang.analyst.strategy",
                "siamang.analyst.git_state", "siamang.models.provider", "siamang.models.providers",
                "siamang.models.factory", "siamang.models.registry", "siamang.models.policy",
                "siamang.qa",  # Phase 16: QA is downstream; the Programmer never depends on it
                "siamang.reviewer",  # Phase 17: the Reviewer is downstream too
            )), (path.name, m)


def test_model_layer_only_through_the_router():
    users = {p.name: {m for m in _imports(p) if m.startswith("siamang.models")} for p in PROGRAMMER.glob("*.py")}
    users = {k: v for k, v in users.items() if v}
    assert set(users) == {"access.py", "strategy.py", "service.py"}
    assert users["service.py"] == {"siamang.models.router"}  # the type, handed to ProgrammerModelAccess


def test_file_access_only_through_the_inspector():
    users = {p.name for p in PROGRAMMER.glob("*.py") if "siamang.analyst.inspection" in _imports(p)}
    assert users == {"snapshot.py", "safety.py"}  # snapshot reads; safety reuses the secret-name rule only
    safety_names = {n.id for n in ast.walk(ast.parse((PROGRAMMER / "safety.py").read_text())) if isinstance(n, ast.Name)}
    assert "ProjectInspector" not in safety_names


def test_nothing_else_imports_the_programmer():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(PROGRAMMER) and any(m.startswith("siamang.programmer") for m in _imports(p))
    }
    # Phase 16: QA re-validates the Phase 15 domain (change, patch, safety, snapshot) as data and reads the
    # project only through ProjectSnapshot. It never imports or calls the Programmer service.
    # repos/workspace.py applies an approved patch and reads the Phase 15 domain as data.
    assert users == {'api/codec.py','api/runtime.py','api/parallel.py', 'documents/packages.py', "repos/workspace.py", "sandbox/workspace.py", "executor/workspace.py", "api/tooling.py", "cli/main.py", "cli/checks.py", "qa/checks.py", "qa/finding.py", "qa/policy.py",
                     "qa/request.py", "qa/result.py", "qa/service.py",
                     # Phase 17: the Reviewer re-validates the same domain as data and never reads the project.
                     "reviewer/checks.py", "reviewer/finding.py", "reviewer/policy.py", "reviewer/request.py",
                     "reviewer/result.py", "reviewer/service.py",
                     # Phase 18: the Loop coordinates rounds: it calls Programmer.program (the one public entry
                     # point) and reads the returned domain objects. Nothing in the programmer imports the loop.
                     "loop/feedback.py", "loop/iteration.py", "loop/request.py", "loop/result.py",
                     "loop/service.py",
                     # Phase 19: Approval re-validates the final Patch under the Phase 15 rules (domain only).
                     "approval/decision.py", "approval/provenance.py"}
    for path in [*(SRC / "qa").glob("*.py"), *(SRC / "reviewer").glob("*.py")]:
        assert "siamang.programmer.service" not in _imports(path) and "siamang.programmer.access" not in _imports(
            path), path
    for layer in ("planner", "analyst", "agents", "orchestrator", "tasks", "projects", "models", "tools", "git",
                  "storage", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.programmer") for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name in ("api", "sandbox"):  # Phase 20 transport, Phase 25 sandbox; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    assert {p.stem for p in PROGRAMMER.glob("*.py")} == {"__init__", "errors", "safety", "request", "change",
                                                         "patch", "result", "policy", "access", "snapshot",
                                                         "context", "strategy", "service", "tools"}


def test_programmer_is_not_an_agent_and_not_orchestrated():
    for layer in ("agents", "orchestrator"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.programmer") for m in _imports(path)), path
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS

    assert not any("program" in impl for impl in KNOWN_IMPLEMENTATIONS)
    from siamang.programmer import Programmer

    assert not any(base.__module__.startswith("siamang.agents") for base in Programmer.__mro__)


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        text = sql.read_text().lower()
        assert "programm" not in text and "patch" not in text and "change_history" not in text, sql.name
    assert not [t for t in EventType if "program" in t.value or "patch" in t.value]


def test_synchronous_only():
    for path in PROGRAMMER.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_public_api_is_program_only():
    from siamang.programmer import Programmer, ProgrammerModelAccess

    assert {n for n in dir(Programmer) if not n.startswith("_")} == {"program", "strategy_id"}
    assert {n for n in dir(ProgrammerModelAccess) if not n.startswith("_")} == {
        "calls", "generate", "structured_output_available", "used"}


def test_strategies_are_allowlisted_by_id():
    from siamang.programmer import KNOWN_STRATEGIES

    assert dict(KNOWN_STRATEGIES).keys() == {"programming.model", "programming.tools"}
    try:
        KNOWN_STRATEGIES["x"] = object  # type: ignore[index]
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("strategy table must be read-only")


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
