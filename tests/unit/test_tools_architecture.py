"""Dependency direction: CLI -> tools -> {projects, git.workspace}; never the reverse.

    (future Task Engine / agents / orchestrator)
                 │
                 ▼
               tools ──► projects (ProjectRegistry)
                 │
                 └──► git (GitWorkspaceManager only; GitClient stays below)
"""

import ast
from pathlib import Path

from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
TOOLS = SRC / "tools"
FUTURE_LAYERS = ("siamang.agents", "siamang.agent", "siamang.planner", "siamang.orchestrator", "siamang.tasks",
                 "siamang.task", "siamang.engine", "siamang.approval", "siamang.api")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_tools_dependencies_are_limited():
    allowed = {
        "siamang.contracts.schema",  # F / check_object / check_spec / ID_RE
        "siamang.log",
        "siamang.projects",
        "siamang.projects.definition",  # PROJECT_ID_RE
        "siamang.git",  # GitWorkspaceManager, GitError types (adapter + allowlist only)
    } | {f"siamang.tools.{p.stem}" for p in TOOLS.glob("*.py")}
    for path in TOOLS.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_only_the_adapter_layer_touches_git():
    users = {p.name for p in TOOLS.glob("*.py") if any(m.startswith("siamang.git") for m in _imports(p))}
    assert users == {"git_tools.py", "implementations.py"}


def test_tools_never_reach_storage_models_bus_or_cli():
    for path in TOOLS.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.storage", "siamang.models", "siamang.bus", "siamang.cli",
                                     "siamang.config", "siamang.git.client")), (path.name, m)


def test_tools_do_not_depend_on_future_layers():
    for path in TOOLS.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(FUTURE_LAYERS), (path.name, m)
    for word in ("agent", "planner", "orchestr", "task", "approval"):
        assert not any(word in p.stem for p in TOOLS.glob("*.py")), word


def test_lower_layers_never_import_tools():
    users = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(TOOLS) and any(m.startswith("siamang.tools") for m in _imports(p))
    }
    # Phase 11: agents reach tools only through ToolAccess -> ToolRegistry.invoke.
    assert users == {"cli/main.py", "cli/checks.py", "agents/context.py", "agents/definition.py",
                     "agents/request.py"}


def test_no_tool_persistence():
    assert SCHEMA_VERSION == 3  # Phase 09 added no schema step (v3 = Phase 10 tasks)
    for sql in (SRC / "storage").glob("*.sql"):
        text = sql.read_text().lower()
        for word in ("tool", "permission", "invocation"):
            assert word not in text, (sql.name, word)
