"""Dependency direction: CLI -> tasks -> {projects, storage, contracts, bus}; never the reverse.

    (future Agent Interface / Orchestrator)
                     │
                     ▼
                   tasks ──► projects (ProjectRegistry)
                     │
                     ├──► storage (TaskRepository, EventStore, Database)
                     └──► contracts, bus, ids, log

The Task Engine manages state only: it never reaches models, tools, Git, or
any future execution layer.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "siamang"
TASKS = SRC / "tasks"
TASK_MODULES = sorted(TASKS.glob("*.py")) + [SRC / "storage" / "tasks.py"]
FUTURE_LAYERS = ("siamang.agents", "siamang.agent", "siamang.planner", "siamang.orchestrator",
                 "siamang.analyst", "siamang.programmer", "siamang.qa", "siamang.reviewer", "siamang.approval",
                 "siamang.api", "siamang.channels", "siamang.telegram")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_tasks_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.bus",
        "siamang.contracts",
        "siamang.contracts.envelope",
        "siamang.contracts.events",
        "siamang.contracts.schema",
        "siamang.ids",
        "siamang.log",
        "siamang.projects",
        "siamang.projects.definition",  # PROJECT_ID_RE + the shared metadata policy
        "siamang.projects.errors",
        "siamang.storage.database",
        "siamang.storage.events",
        "siamang.storage.tasks",
    } | {f"siamang.tasks.{p.stem}" for p in TASKS.glob("*.py")}
    for path in TASKS.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_tasks_never_reach_models_tools_git_config_or_cli():
    for path in TASK_MODULES:
        for m in _imports(path):
            assert not m.startswith(("siamang.models", "siamang.tools", "siamang.git", "siamang.cli",
                                     "siamang.config")), (path.name, m)


def test_tasks_do_not_depend_on_future_layers():
    for path in TASK_MODULES:
        for m in _imports(path):
            assert not m.startswith(FUTURE_LAYERS), (path.name, m)
    for word in ("agent", "planner", "orchestr", "executor", "runner", "approval", "telegram"):
        assert not any(word in p.stem for p in TASKS.glob("*.py")), word


def test_project_lookup_goes_through_the_registry():
    """The engine never reads the projects table itself (Phase 10 §15)."""
    for path in TASK_MODULES:
        text = path.read_text(encoding="utf-8").lower()
        assert "from projects" not in text and "join projects" not in text, path.name
        assert "siamang.storage.projects" not in _imports(path), path.name


def test_repository_is_sql_only():
    """storage/tasks.py knows rows, not rules: it imports nothing from the task domain."""
    used = {m for m in _imports(SRC / "storage" / "tasks.py") if m.startswith("siamang")}
    assert used == {"siamang.storage.database"}


def test_only_the_cli_agents_orchestrator_and_planner_import_tasks():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(TASKS) and any(m.startswith("siamang.tasks") for m in _imports(p))
    }
    # Phase 11: agents use task domain types (Task snapshot, ID validation, TaskStatus), never the engine.
    # Phase 12: the orchestrator reads tasks through TaskEngine.get (never a mutator; see orchestrator tests).
    assert users == {'api/app.py','api/runtime.py','api/parallel.py', "cli/main.py", "agents/context.py", "agents/request.py", "agents/result.py",
                     "agents/definition.py", "agents/registry.py", "orchestrator/policy.py",
                     "orchestrator/request.py", "orchestrator/service.py",
                     # Phase 13: the planner reads tasks through TaskEngine.get and reuses ID/key validation.
                     "planner/plan.py", "planner/policy.py", "planner/request.py", "planner/service.py",
                     "planner/strategy.py",
                     # Phase 14: the analyst reads tasks through TaskEngine.get and reuses ID/key validation.
                     "analyst/analysis.py", "analyst/policy.py", "analyst/request.py", "analyst/service.py",
                     "analyst/strategy.py",
                     # Phase 15: the programmer reads tasks through TaskEngine.get and reuses ID/key validation.
                     "programmer/context.py", "programmer/patch.py", "programmer/policy.py", "programmer/request.py",
                     "programmer/result.py", "programmer/service.py",
                     # Phase 16: QA reads tasks through TaskEngine.get and reuses ID/status validation.
                     "qa/policy.py", "qa/request.py", "qa/result.py", "qa/service.py",
                     # Phase 17: the Reviewer reads tasks through TaskEngine.get and reuses ID/status validation.
                     "reviewer/policy.py", "reviewer/request.py", "reviewer/result.py", "reviewer/service.py",
                     # Phase 18: the Loop reads tasks through TaskEngine.get and reuses ID validation.
                     "loop/iteration.py", "loop/request.py", "loop/result.py", "loop/service.py",
                     # Phase 19: Approval reads tasks through TaskEngine.get and reuses ID/status validation.
                     "approval/provenance.py", "approval/request.py", "approval/service.py"}


def test_no_task_execution_api():
    """State only: no execute/run/dispatch entry points on the engine or the domain."""
    from siamang.tasks import Task, TaskEngine

    for cls in (TaskEngine, Task):
        names = {n for n in dir(cls) if not n.startswith("_")}
        for word in ("execute", "run", "dispatch", "invoke", "generate", "spawn", "process", "work"):
            assert not any(word in n for n in names), (cls.__name__, word)
