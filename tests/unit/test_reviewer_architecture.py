"""Dependency direction: CLI -> reviewer -> {tasks, projects, planner/analyst/programmer/qa *domain* as data}.

    Task + Plan + SystemAnalysis + ProposedPatch + QAResult ──► Reviewer ──► ReviewResult ──X── (future) Loop / Approval

The Reviewer consumes earlier artifacts as immutable data and reuses their
validation rules. It never imports or calls a service (Programmer, QA,
Planner, Analyst), a model, an agent, the Orchestrator, tools, Git, storage,
or events, and it never reads the project (no ProjectSnapshot or
ProjectInspector). Nothing upstream imports it.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
REVIEWER = SRC / "reviewer"
FUTURE = ("repair", "api", "websocket", "telegram", "channels",
          "workflow", "scheduler", "sandbox", "runner", "unity", "events", "delivery")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_reviewer_dependencies_are_limited():
    allowed = {
        "siamang.analyst.analysis",  # SystemAnalysis accepted as immutable input
        "siamang.ids",
        "siamang.log",
        "siamang.planner.plan",  # Plan accepted as immutable input
        "siamang.planner.request",  # shared text/JSON validation helpers
        "siamang.planner.step",  # StepRole (what kind of work a step describes)
        "siamang.programmer.change",  # ProposedChange re-validation
        "siamang.programmer.patch",  # Patch re-validation (conflicts, bounds)
        "siamang.programmer.safety",  # secret rule; deep freeze
        "siamang.projects",  # the project exists and is active; root_path is never used
        "siamang.qa.checks",  # pure helpers: deep_frozen (tamper check), scope_roots (analysis locations)
        "siamang.qa.finding",  # safe_path
        "siamang.qa.request",  # hash format and the QA key rules
        "siamang.qa.result",  # QAResult accepted as immutable evidence
        "siamang.tasks.definition",
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.reviewer.{p.stem}" for p in REVIEWER.glob("*.py")}
    for path in REVIEWER.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_reviewer_never_reaches_services_models_execution_or_the_project():
    for path in REVIEWER.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith((
                "siamang.programmer.service", "siamang.programmer.access", "siamang.programmer.strategy",
                "siamang.programmer.context", "siamang.programmer.snapshot", "siamang.qa.service",
                "siamang.planner.service", "siamang.planner.strategy", "siamang.analyst.service",
                "siamang.analyst.strategy", "siamang.analyst.git_state", "siamang.analyst.inspection",
                "siamang.models", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.storage",
                "siamang.bus", "siamang.contracts", "siamang.config", "siamang.cli", "siamang.git",
            )), (path.name, m)


def test_upstream_layers_never_import_the_reviewer():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(REVIEWER) and any(m.startswith("siamang.reviewer") for m in _imports(p))
    }
    # Phase 18: the Loop calls ReviewerService.review once per round and checks the ReviewResult it gets.
    assert users == {'api/codec.py','api/runtime.py','api/parallel.py', "cli/main.py", "cli/checks.py", "loop/feedback.py", "loop/iteration.py", "loop/policy.py",
                     "loop/request.py", "loop/result.py", "loop/service.py",
                     # Phase 19: Approval re-validates the final ReviewResult and re-derives checks.judge.
                     "approval/provenance.py"}
    for layer in ("qa", "programmer", "analyst", "planner", "agents", "orchestrator", "tasks", "projects", "models",
                  "tools", "git", "storage", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.reviewer") for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name == "api":  # Phase 20 transport; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    assert {p.stem for p in REVIEWER.glob("*.py")} == {"__init__", "errors", "request", "finding", "result",
                                                       "policy", "checks", "service"}


def test_reviewer_is_not_an_agent_and_not_orchestrated():
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS
    from siamang.reviewer import ReviewerService

    assert not any("review" in impl.split(".")[-1] for impl in KNOWN_IMPLEMENTATIONS)
    assert not any(base.__module__.startswith(("siamang.agents", "siamang.qa", "siamang.programmer"))
                   for base in ReviewerService.__mro__)


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "review" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if "review" in t.value]


def test_no_approval_apply_repair_or_loop_vocabulary_in_the_api():
    from siamang.reviewer import ReviewerService, ReviewResult, ReviewStatus

    assert {n for n in dir(ReviewerService) if not n.startswith("_")} == {"review"}
    assert {s.value for s in ReviewStatus} == {"pass", "concern", "fail", "inconclusive"}
    for cls in (ReviewerService, ReviewResult):
        for name in dir(cls):
            assert not any(w in name.lower() for w in ("approve", "reject", "apply", "merge", "commit", "deploy",
                                                       "accept", "retry", "repair", "regenerate", "iteration")), name


def test_qa_strategy_constant_matches_qa():
    from siamang.qa.service import STRATEGY
    from siamang.reviewer.service import QA_STRATEGY

    assert QA_STRATEGY == STRATEGY


def test_synchronous_only():
    for path in REVIEWER.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
