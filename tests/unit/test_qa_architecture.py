"""Dependency direction: CLI -> qa -> {tasks, projects, planner/analyst (data), programmer domain (data)}.

    Task + Plan + SystemAnalysis + ProposedPatch ──► QA ──► ProjectSnapshot (Phase 15, read-only)
                                                      │
                                                      ▼
                                                  QAResult ──X── (future) Reviewer / Loop / Approval

QA uses the Phase 15 *domain* (ProposedChange, Patch, safety, ProjectSnapshot)
as data and rules, never the Programmer service, its model access, or any
model. Nothing upstream imports QA.
"""

import ast
from pathlib import Path

from siamang.contracts import CONTRACT_VERSION, EventType
from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
QA = SRC / "qa"
FUTURE = ("repair", "api", "websocket", "telegram", "channels",
          "workflow", "scheduler", "sandbox", "runner", "unity")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_qa_dependencies_are_limited():
    allowed = {
        "siamang.analyst.analysis",  # SystemAnalysis accepted as immutable input
        "siamang.ids",
        "siamang.log",
        "siamang.planner.plan",  # Plan accepted as immutable input
        "siamang.planner.request",  # shared text/JSON validation helpers
        "siamang.programmer.change",  # ProposedChange re-validation
        "siamang.programmer.errors",  # the domain's errors, turned into findings
        "siamang.programmer.patch",  # Patch re-validation (conflicts, bounds)
        "siamang.programmer.policy",  # the snapshot's read-budget type
        "siamang.programmer.request",  # shared request key rules
        "siamang.programmer.safety",  # path, forbidden-file, secret rules; deep freeze
        "siamang.programmer.snapshot",  # read-only project snapshot over ProjectInspector
        "siamang.projects",
        "siamang.tasks.definition",
        "siamang.tasks.engine",  # the TaskEngine type; only .get is called (security tests)
        "siamang.tasks.errors",
        "siamang.tasks.lifecycle",
    } | {f"siamang.qa.{p.stem}" for p in QA.glob("*.py")}
    for path in QA.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_qa_never_reaches_services_models_or_execution():
    for path in QA.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith((
                "siamang.programmer.service", "siamang.programmer.access", "siamang.programmer.strategy",
                "siamang.programmer.context", "siamang.planner.service", "siamang.planner.strategy",
                "siamang.analyst.service", "siamang.analyst.strategy", "siamang.analyst.git_state",
                "siamang.models", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.storage",
                "siamang.bus", "siamang.contracts", "siamang.config", "siamang.cli", "siamang.git",
            )), (path.name, m)


def test_upstream_layers_never_import_qa():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(QA) and any(m.startswith("siamang.qa") for m in _imports(p))
    }
    # Phase 17: the Reviewer consumes a QAResult as data and reuses QA's safe-path, scope, and tamper helpers.
    # It never imports or calls QAService (see the reviewer architecture tests).
    assert users == {'api/codec.py','api/runtime.py','api/parallel.py', "cli/main.py", "cli/checks.py", "reviewer/checks.py", "reviewer/finding.py",
                     "reviewer/request.py", "reviewer/result.py", "reviewer/service.py",
                     # Phase 18: the Loop calls QAService.verify once per round and checks the QAResult it gets.
                     "loop/feedback.py", "loop/iteration.py", "loop/result.py", "loop/service.py",
                     # Phase 19: Approval re-validates the final QAResult and re-runs QA's pure static checks.
                     "approval/provenance.py"}
    for layer in ("programmer", "analyst", "planner", "agents", "orchestrator", "tasks", "projects", "models",
                  "tools", "git", "storage", "contracts"):
        for path in (SRC / layer).rglob("*.py"):
            assert not any(m.startswith("siamang.qa") for m in _imports(path)), path


def test_no_future_layers_exist():
    for name in FUTURE:
        if name in ("api", "sandbox"):  # Phase 20 transport, Phase 25 sandbox; upstream imports remain forbidden
            continue
        assert not (SRC / name).exists(), name
        assert not (SRC / f"{name}.py").exists(), name
    assert {p.stem for p in QA.glob("*.py")} == {"__init__", "errors", "request", "finding", "result", "policy",
                                                 "checks", "service"}


def test_qa_is_not_an_agent_and_not_orchestrated():
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS
    from siamang.qa import QAService

    assert not any("qa" in impl.split(".")[-1] for impl in KNOWN_IMPLEMENTATIONS)
    assert not any(base.__module__.startswith("siamang.agents") for base in QAService.__mro__)


def test_no_persistence_or_events():
    assert SCHEMA_VERSION == 3 and CONTRACT_VERSION == "0.2"
    for sql in (SRC / "storage").glob("*.sql"):
        assert "qa_" not in sql.read_text().lower(), sql.name
    assert not [t for t in EventType if t.value.startswith("qa") or "verif" in t.value]


def test_no_review_loop_or_approval_vocabulary_in_the_api():
    from siamang.qa import QAResult, QAService, QAStatus

    assert {n for n in dir(QAService) if not n.startswith("_")} == {"verify"}
    assert {s.value for s in QAStatus} == {"pass", "fail", "inconclusive"}  # never "approved" / "rejected"
    for name in dir(QAResult):
        assert not any(w in name.lower() for w in ("approve", "reject", "review", "retry", "repair", "iteration"))


def test_synchronous_only():
    for path in QA.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(n, (ast.AsyncFunctionDef, ast.Await)) for n in ast.walk(tree)), path.name


def test_no_runtime_dependencies():
    import tomllib

    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
