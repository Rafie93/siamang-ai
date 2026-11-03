"""Dependency direction: CLI -> GitWorkspaceManager -> {ProjectRegistry, GitClient} -> git.

Never Project Registry -> Git, Git -> models/agents/tasks/bus, or a Git library dependency.
"""

import ast
from pathlib import Path

from siamang.storage import SCHEMA_VERSION

SRC = Path(__file__).parents[2] / "src" / "siamang"
GIT_PKG = SRC / "git"
GIT_LIBRARIES = {"git", "pygit2", "dulwich", "sh", "plumbum", "pexpect"}


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def _users(prefix: str, *, exclude: Path) -> set[str]:
    return {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if not p.is_relative_to(exclude) and any(m == prefix or m.startswith(prefix + ".") for m in _imports(p))
    }


def test_no_git_library_dependency():
    for path in SRC.rglob("*.py"):
        roots = {m.split(".")[0] for m in _imports(path)}
        assert not roots & GIT_LIBRARIES, path


def test_git_package_dependencies_are_limited():
    allowed = {"siamang.log", "siamang.projects", "siamang.git", "siamang.git.client", "siamang.git.errors",
               "siamang.git.repository", "siamang.git.workspace"}
    for path in GIT_PKG.glob("*.py"):
        used = {m for m in _imports(path) if m.startswith("siamang")}
        assert used <= allowed, (path.name, used - allowed)


def test_git_never_reaches_models_agents_tasks_bus_or_storage():
    for path in GIT_PKG.glob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("siamang.models", "siamang.bus", "siamang.storage", "siamang.contracts",
                                     "siamang.cli", "siamang.config")), (path.name, m)


def test_only_the_workspace_manager_uses_the_registry():
    users = {p.name for p in GIT_PKG.glob("*.py") if any(m.startswith("siamang.projects") for m in _imports(p))}
    assert users == {"workspace.py"}


def test_client_knows_nothing_about_projects():
    for name in ("client.py", "errors.py", "repository.py"):
        assert not any(m.startswith("siamang.projects") for m in _imports(GIT_PKG / name)), name


def test_registry_and_core_stay_git_agnostic():
    # Project Registry, storage, models, bus, contracts never import the Git layer.
    # Phase 09: the read-only Git tool adapter (and its allowlist) sit above the Git layer.
    # Phase 14: the System Analyst reads Git state through GitWorkspaceManager (read-only; never GitClient).
    assert _users("siamang.git", exclude=GIT_PKG) == {"cli/main.py", "cli/checks.py", "tools/git_tools.py",
                                                       "tools/implementations.py", "analyst/git_state.py",
                                                       "analyst/service.py"}


def test_no_tool_task_or_agent_modules_in_git():
    for path in GIT_PKG.glob("*.py"):
        for word in ("tool", "task", "agent", "orchestr", "approval", "permission"):
            assert word not in path.stem, path.name


def test_no_persistent_git_state():
    assert SCHEMA_VERSION == 3  # Phase 08 added no schema step (v3 = Phase 10 tasks)
    for sql in (SRC / "storage").glob("*.sql"):
        text = sql.read_text().lower()
        for word in ("branch", "worktree", "workspace", "commit_sha", "repository"):
            assert word not in text, (sql.name, word)
