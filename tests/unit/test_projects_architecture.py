"""Project Registry -> storage abstraction -> SQLite; never Git, models, agents, or network."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "siamang"
PROJECT_MODULES = sorted((SRC / "projects").glob("*.py")) + [SRC / "storage" / "projects.py"]

FORBIDDEN_MODULES = {
    "subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "asyncio", "shutil", "glob",
    "multiprocessing", "concurrent", "git", "pygit2", "dulwich", "dotenv",
    "openai", "anthropic", "ollama",
}
FORBIDDEN_NAMES = {"system", "popen", "Popen", "environ", "getenv", "putenv", "listdir", "scandir", "walk",
                   "iterdir", "rglob", "exec", "eval", "load_dotenv"}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path):
    out = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_no_forbidden_imports():
    for path in PROJECT_MODULES:
        roots = {m.split(".")[0] for m in _imports(path)}
        assert not roots & FORBIDDEN_MODULES, (path.name, roots & FORBIDDEN_MODULES)


def test_siamang_dependencies_are_limited():
    allowed = {
        "siamang.jsondata",  # pure immutable JSON helper
        "siamang.contracts.envelope",  # UTC timestamp helpers
        "siamang.ids",
        "siamang.log",
        "siamang.projects.definition",
        "siamang.projects.errors",
        "siamang.projects.registry",
        "siamang.storage.database",
        "siamang.storage.projects",
    }
    for path in PROJECT_MODULES:
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_no_model_bus_or_task_dependencies():
    for path in PROJECT_MODULES:
        for m in _imports(path):
            assert not m.startswith(("siamang.models", "siamang.bus", "siamang.storage.tasks",
                                     "siamang.storage.events", "siamang.cli")), (path.name, m)


def test_no_process_env_or_scan_calls():
    for path in PROJECT_MODULES:
        names = {n.attr for n in ast.walk(_tree(path)) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(_tree(path)) if isinstance(n, ast.Name)}
        assert not names & FORBIDDEN_NAMES, (path.name, names & FORBIDDEN_NAMES)


def test_storage_does_not_depend_on_domain():
    # direction: projects.registry -> storage.projects, never the reverse
    assert not any(m.startswith("siamang.projects") for m in _imports(SRC / "storage" / "projects.py"))


def test_only_cli_uses_the_registry():
    users = set()
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(SRC / "projects"):
            continue
        if any(m.startswith("siamang.projects") for m in _imports(path)):
            users.add(path.relative_to(SRC).as_posix())
    # Phase 08: the Git Workspace Manager resolves project IDs through the registry.
    # Phase 09: the Tool Registry resolves and scope-checks projects through it too.
    # Phase 10: the Task Engine validates projects (and reuses the metadata policy) through it.
    # Phase 11: agent contexts resolve the task's project through it (and reuse the metadata policy).
    assert users == {'api/app.py','api/runtime.py','api/parallel.py', "cli/main.py", "git/workspace.py", "tools/definition.py", "tools/permissions.py",
                     "tools/registry.py", "tools/git_tools.py", "tools/implementations.py",
                     "tasks/definition.py", "tasks/engine.py",
                     "agents/context.py", "agents/definition.py", "agents/request.py", "agents/result.py",
                     # Phase 12: the orchestrator checks the project exists and is active.
                     "orchestrator/service.py",
                     # Phase 13: the planner checks the project and reuses the metadata policy.
                     "planner/request.py", "planner/service.py",
                     # Phase 14: the analyst resolves the project (root_path authority) through it.
                     "analyst/git_state.py", "analyst/service.py",
                     # Phase 15: the programmer resolves the project (exists, active, root_path) through it.
                     "programmer/context.py", "programmer/service.py",
                     # Phase 16: QA resolves the project (exists, active, root_path) through it.
                     "qa/service.py",
                     # Phase 17: the Reviewer checks the project exists and is active (never reads its root).
                     "reviewer/service.py",
                     # Phase 19: Approval checks the project exists and is active (never reads its root).
                     "approval/service.py"}


def test_registry_does_not_resolve_symlinks_or_read_files():
    src = (SRC / "projects" / "definition.py").read_text() + (SRC / "projects" / "registry.py").read_text()
    for call in ("resolve(", "realpath(", "read_text(", "read_bytes(", "open("):
        assert call not in src, call
