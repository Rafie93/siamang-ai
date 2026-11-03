"""QA security: static and read-only. No writes, Git, execution, network, models, secrets, or other services.

Static checks (with deliberate-break proofs) cover every QA module; runtime
tripwires prove QA still produces a result while every filesystem-mutation,
process, network, import, Git, model, Programmer, Planner, Analyst, agent,
tool, orchestrator, event, and task-mutation entry point explodes on contact.
"""

import ast
import json
import logging
from pathlib import Path

import pytest

from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter
from siamang.programmer import Patch, ProposedChange
from siamang.programmer.service import Programmer
from siamang.programmer.snapshot import Before, ProjectSnapshot
from siamang.programmer.change import BeforeState
from siamang.qa import QAStatus
from siamang.qa.errors import QAError
from tests.support.analyst import SECRET, sample_project, write
from tests.support.git import make_repo
from tests.support.programmer import AUTH, make_setup, sha, tree
from tests.support.qa import codes, forge, propose, verify, with_changes
from tests.unit.test_programmer_security import tripwires  # noqa: F401  (shared runtime tripwire fixture)

SRC = Path(__file__).parents[2] / "src" / "siamang"
QA = SRC / "qa"

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "tempfile", "io", "asyncio", "threading",
                     "multiprocessing", "concurrent", "random", "secrets", "uuid", "time", "datetime", "pickle",
                     "marshal", "ctypes", "runpy", "code", "codeop", "unittest", "doctest", "pytest", "git",
                     "openai", "anthropic", "ollama", "requests", "httpx", "aiohttp"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.bus",
                     "siamang.config", "siamang.cli", "siamang.git", "siamang.contracts", "siamang.models",
                     "siamang.planner.service", "siamang.planner.strategy", "siamang.analyst.service",
                     "siamang.analyst.strategy", "siamang.analyst.git_state", "siamang.analyst.inspection",
                     "siamang.programmer.service", "siamang.programmer.access", "siamang.programmer.strategy",
                     "siamang.programmer.context", "siamang.reviewer", "siamang.approval")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"Database", "EventStore", "EventBus", "GitClient", "GitWorkspaceManager", "ToolRegistry",
                   "AgentRegistry", "Orchestrator", "ModelRouter", "ModelProvider", "ProgrammerModelAccess",
                   "Programmer", "Planner", "SystemAnalyst", "ProjectInspector", "Reviewer", "ApprovalSystem",
                   "import_module", "system", "popen", "Popen", "environ", "getenv", "walk", "rglob", "connect",
                   "remove", "unlink", "rmdir", "rename", "mkdir", "makedirs", "chmod", "symlink", "write_text",
                   "write_bytes", "rmtree", "touch", "O_WRONLY", "O_CREAT"}
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "archive",
                   "restore", "execute", "invoke", "plan", "analyze", "program", "generate", "select", "run",
                   "transaction", "publish", "commit", "checkout", "merge", "reset", "push", "stash", "apply",
                   "approve", "reject", "review", "repair", "fix", "write"}


def violations(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES or a.name.startswith(FORBIDDEN_SIAMANG):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES or node.module.startswith(FORBIDDEN_SIAMANG):
                found.add(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS | FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.add(node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
            found.add(f"call:{node.func.attr}")
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.While)):
            found.add(type(node).__name__)  # no async, and no loops that could iterate a repair cycle
    return found


@pytest.mark.parametrize("path", sorted(QA.glob("*.py")), ids=lambda p: p.name)
def test_qa_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import socket", "import importlib", "import pytest",
    "subprocess.run(['pytest'])", "os.system('npm test')", "eval(src)", "exec(src)", "compile(src, 'f', 'exec')",
    "open(path, 'w')", "Path(p).write_text(x)", "os.remove(p)", "shutil.rmtree(p)",
    "from siamang.programmer.service import Programmer", "Programmer(engine).program(r)", "programmer.program(r)",
    "from siamang.models.router import ModelRouter", "router.select(r).generate(r)",
    "from siamang.planner.service import Planner", "planner.plan(r)", "analyst.analyze(r)",
    "from siamang.agents.registry import AgentRegistry", "orchestrator.execute(r)", "tools.invoke('x', c, {})",
    "from siamang.git.client import GitClient", "repo.commit('x')", "self._tasks.fail(t, e)",
    "self._tasks.complete(t, r)", "bus.publish(e)", "approval.approve(p)", "reviewer.review(p)",
    "patch.apply()", "qa.repair(p)", "while not passed: retry()", "from siamang.reviewer import Reviewer",
    "from siamang.analyst.inspection import ProjectInspector", "os.environ['TOKEN']",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


# --- no-write proof ----------------------------------------------------------------------------


def _arm_models_and_services(monkeypatch, calls):
    def trip(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError(name)
        return f
    for target, attr in ((ModelRouter, "select"), (ModelProvider, "generate"), (Programmer, "program")):
        monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))


@pytest.mark.parametrize("variant", ["pass", "fail", "inconclusive"])
def test_qa_touches_nothing_outside_its_boundary(db, tmp_path, tripwires, monkeypatch, variant):  # noqa: F811
    s = make_setup(db, tmp_path)
    patch = propose(s)
    if variant == "fail":
        patch_hash = "sha256:" + "0" * 64
    else:
        patch_hash = None
    if variant == "inconclusive":
        patch = Patch(patch.patch_id, patch.task_id, patch.project_id, "Nothing.", ())
    task = s.world.engine.get(s.world.task.task_id)
    before = tree(s.root)
    calls = []
    _arm_models_and_services(monkeypatch, calls)
    with tripwires() as trips:
        result = verify(s, patch, patch_hash)
    assert trips == [] and calls == []
    assert result.status.value == variant
    assert tree(s.root) == before and s.world.engine.get(s.world.task.task_id) == task


def test_git_is_untouched(db, tmp_path, monkeypatch):
    from siamang.git import client as client_mod

    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    (Path(repo.root) / ".gitignore").write_text(".env\ncerts/\nconfig/secrets.yaml\n")
    repo.commit("sample")
    s = make_setup(db, tmp_path, root=Path(repo.root))
    patch = propose(s)

    def state():
        return (repo.git("status", "--porcelain=v2", "--branch", "--untracked-files=all"), repo.head(),
                repo.git("branch", "--list"), repo.git("stash", "list"), repo.git("worktree", "list"),
                tree(Path(repo.root) / ".git"), tree(Path(repo.root)))

    before = state()
    seen = []
    monkeypatch.setattr(client_mod.GitClient, "run", lambda *a, **k: seen.append(a) or (_ for _ in ()).throw(
        AssertionError("git called")))
    assert verify(s, patch).status is QAStatus.PASS
    git_config = forge(patch.changes[0], path=".git/config")
    assert verify(s, with_changes(patch, (git_config,)), "sha256:" + "5" * 64).status is QAStatus.FAIL
    assert seen == []
    monkeypatch.undo()
    assert state() == before


# --- path security (forged past Phase 15 validation) ----------------------------------------------

HOSTILE_PATHS = ["../outside.py", "../../etc/passwd", "src/../../x.py", "/etc/passwd", "/Users/someone/web/x.py",
                 "~/x.py", "a/~/b.py", "%2e%2e/x.py", "src/%2e%2e/x", "src\\app.py", "C:/x.py", "C:\\x.py",
                 "\\\\server\\share\\x", "//server/share/x", "src/a\x00.py", "src/a\nb.py", "src/\u202egnp.py",
                 "a//b.py", "./a.py", "", "x/" * 600 + "y"]
FORBIDDEN_PATHS = [".git/config", ".git/hooks/pre-commit", "sub/.git/HEAD", ".ssh/id_ed25519", "home/.ssh/config",
                   ".env", ".env.local", "config/.env.production", "credentials.json", "config/secrets.yaml",
                   "certs/server.pem", "deploy.key", "id_rsa", ".aws/credentials", ".netrc"]


@pytest.mark.parametrize("path", HOSTILE_PATHS + FORBIDDEN_PATHS)
def test_unsafe_and_forbidden_paths_fail_without_echo(db, tmp_path, path):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    bad = forge(patch.changes[1], path=path)  # the create: no snapshot read is even attempted
    forged = with_changes(patch, (patch.changes[0], bad))
    r = verify(s, forged, forged.content_hash)  # even with a self-consistent recorded hash
    assert r.status is QAStatus.FAIL
    hits = [f for f in r.findings if f.code == "patch.change_invalid"]
    assert hits and all(f.category.value == "security" for f in hits)
    text = json.dumps(r.to_dict())
    if path in HOSTILE_PATHS and path:
        assert path not in text and json.dumps(path)[1:-1] not in text
    assert "/Users/someone" not in text and str(s.root) not in text


@pytest.mark.parametrize("content", [f"API_KEY = '{SECRET}'\n", "-----BEGIN PRIVATE KEY-----\nMII\n",
                                     "export PASSWORD=hunter2hunter2\n", "t = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n"])
def test_secret_content_fails_without_leak(db, tmp_path, caplog, content):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    bad = forge(patch.changes[1], after_content=content, after_hash=sha(content))
    caplog.set_level(logging.DEBUG, logger="siamang")
    r = verify(s, with_changes(patch, (patch.changes[0], bad)))
    assert r.status is QAStatus.FAIL and any(f.category.value == "security" for f in r.findings)
    secret_part = content.strip().split("=")[-1].strip(" '")
    for text in (json.dumps(r.to_dict()), caplog.text):
        assert secret_part not in text and SECRET not in text and "BEGIN PRIVATE KEY" not in text


def test_secret_in_existing_file_is_never_exposed(db, tmp_path, caplog):
    root = sample_project(tmp_path / "banuva")
    write(root, {"backend/routes/auth_conf.py": f"API_KEY = '{SECRET}'\n"})
    s = make_setup(db, tmp_path, root=root)
    current = (root / "backend/routes/auth_conf.py").read_text()
    c = ProposedChange("change_01", "backend/routes/auth_conf.py", "modify", "step_03", "Load the key from env.",
                       after_content="import os\nAPI_KEY = os.environ['API_KEY']\n", before="observed",
                       before_hash=sha(current))
    p = Patch("pch_01ABC", s.world.task.task_id, s.world.project.project_id, "Move key to env.", (c,))
    caplog.set_level(logging.DEBUG, logger="siamang")
    r = verify(s, p)
    assert r.status is QAStatus.PASS
    assert SECRET not in json.dumps(r.to_dict()) and SECRET not in caplog.text


def test_secret_files_are_never_opened(db, tmp_path, monkeypatch):
    import os

    s = make_setup(db, tmp_path)
    patch = propose(s)
    opened = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: opened.append(os.path.basename(p)) or real(p, *a, **k))
    verify(s, patch)
    assert opened and not {".env", "secrets.yaml", "server.pem"} & set(opened)


def test_symlinks_are_never_followed(db, tmp_path):
    s = make_setup(db, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x\n")
    (s.root / "link").symlink_to(outside)
    (s.root / "flink.py").symlink_to(outside / "x.py")
    for path, op in (("link/x.py", "modify"), ("link/new.py", "create"), ("flink.py", "modify")):
        kw = dict(before="absent") if op == "create" else dict(before="observed", before_hash=sha("x\n"))
        c = ProposedChange("change_01", path, op, "step_03", "r", after_content="y\n", **kw)
        r = verify(s, Patch("pch_01ABC", s.world.task.task_id, s.world.project.project_id, "s", (c,)))
        assert r.status is QAStatus.FAIL and "project.path_blocked" in codes(r)
        assert [f.category.value for f in r.findings if f.code == "project.path_blocked"] == ["security"]
    assert (outside / "x.py").read_text() == "x\n" and sorted(p.name for p in outside.iterdir()) == ["x.py"]


def test_case_only_collision_with_existing_entry(db, tmp_path):
    s = make_setup(db, tmp_path)
    c = ProposedChange("change_01", "Backend/new.py", "create", "step_03", "r", after_content="x\n", before="absent")
    r = verify(s, Patch("pch_01ABC", s.world.task.task_id, s.world.project.project_id, "s", (c,)))
    assert r.status is QAStatus.FAIL and "project.path_blocked" in codes(r)


# --- adversarial input ---------------------------------------------------------------------------


def test_inconsistent_snapshot_is_reported(db, tmp_path, monkeypatch):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    monkeypatch.setattr(ProjectSnapshot, "before",
                        lambda self, path, cid: Before(BeforeState.OBSERVED, sha("racing write\n")))
    r = verify(s, patch)
    assert r.status is QAStatus.FAIL and codes(r) >= {"project.before_hash_mismatch", "project.create_target_exists"}


def test_extra_attributes_and_mutable_metadata_are_inert(db, tmp_path):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    smuggled = forge(patch, summary=patch.summary)
    object.__setattr__(smuggled, "apply", lambda: (_ for _ in ()).throw(AssertionError("called")))
    object.__setattr__(smuggled, "command", "rm -rf /")
    assert verify(s, smuggled, patch.content_hash).status is QAStatus.PASS  # unknown attributes are never used
    meta = {"a": {"b": [1]}}
    mutable = forge(patch.changes[0], metadata=meta)  # a plain, mutable dict forced in
    forged = with_changes(patch, (mutable, patch.changes[1]))
    recorded = forged.content_hash
    r = verify(s, forged, recorded)
    meta["a"]["b"].append(2)  # mutate after verification
    assert r.status is QAStatus.FAIL and "patch.change_altered" in codes(r)  # forced-in mutable state is flagged


def test_huge_and_hostile_values_are_bounded(db, tmp_path):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    huge = forge(patch.changes[1], path="a/" * 5000 + "x.py", reason="r" * 100_000, after_content="x" * 3_000_000)
    r = verify(s, with_changes(patch, (huge,)), "sha256:" + "6" * 64)
    assert r.status is QAStatus.FAIL and len(json.dumps(r.to_dict())) < 100_000
    with pytest.raises(QAError):
        verify(s, patch, metadata={"blob": "x" * 50_000})


def test_errors_and_logs_leak_nothing(db, tmp_path, caplog):
    s = make_setup(db, tmp_path)
    patch = propose(s)
    caplog.set_level(logging.DEBUG, logger="siamang")
    verify(s, with_changes(patch, (forge(patch.changes[0], path="/Users/someone/.ssh/id_rsa"),)), "sha256:" + "7" * 64)
    with pytest.raises(QAError) as info:
        verify(s, patch, task_id="tsk_01UNKNOWN")
    for text in (caplog.text, str(info.value.to_dict())):
        assert "/Users/someone" not in text and str(s.root) not in text and "def login" not in text
    assert "qa task=" in caplog.text


def test_internal_failure_is_contained(db, tmp_path, monkeypatch):
    from siamang.qa import service as service_mod

    s = make_setup(db, tmp_path)
    patch = propose(s)

    def boom(*a, **k):
        raise RuntimeError(f"/Users/someone/secret {SECRET}")

    monkeypatch.setattr(service_mod, "check_plan", boom)
    with pytest.raises(QAError) as info:
        verify(s, patch)
    assert info.value.code.value == "qa_internal" and info.value.cause == "RuntimeError"
    assert SECRET not in str(info.value.to_dict()) and "/Users/someone" not in str(info.value.to_dict())
