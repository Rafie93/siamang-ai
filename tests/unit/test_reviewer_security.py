"""Reviewer security: read-only judgment. No file reads or writes, Git, execution, network, models, or other services.

Static checks (with deliberate-break proofs) cover every Reviewer module.
Runtime tripwires prove the Reviewer still produces pass, concern, fail, and
inconclusive results while every filesystem, process, network, import, Git,
model, Programmer, QA, Planner, Analyst, agent, tool, orchestrator, event, and
task-mutation entry point explodes on contact, and that it opens no file at
all. Hostile paths, secrets, and messages are refused or withheld, never
echoed.
"""

import ast
import dataclasses
import io
import json
import logging
import os
from pathlib import Path

import pytest

from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter
from siamang.programmer import Patch, ProposedChange
from siamang.programmer.service import Programmer
from siamang.qa import QAFinding
from siamang.qa.service import QAService
from siamang.reviewer import ReviewerPolicy, ReviewStatus
from siamang.reviewer.checks import WITHHELD, Findings
from siamang.reviewer.errors import ReviewerError, ReviewerErrorCode
from tests.support.analyst import SECRET, sample_project, write
from tests.support.git import make_repo
from tests.support.programmer import AUTH, make_setup, sha, tree
from tests.support.qa import forge, verify
from tests.support.reviewer import VALIDATION, chain, clean_changes, create, direct, review
from tests.unit.test_programmer_security import tripwires  # noqa: F401  (shared runtime tripwire fixture)

SRC = Path(__file__).parents[2] / "src" / "siamang"
REVIEWER = SRC / "reviewer"
C = ReviewerErrorCode

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "tempfile", "io", "asyncio", "threading",
                     "multiprocessing", "concurrent", "random", "secrets", "uuid", "time", "datetime", "pickle",
                     "marshal", "ctypes", "runpy", "code", "codeop", "unittest", "doctest", "pytest", "git",
                     "openai", "anthropic", "ollama", "requests", "httpx", "aiohttp", "langchain"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.bus",
                     "siamang.config", "siamang.cli", "siamang.git", "siamang.contracts", "siamang.models",
                     "siamang.planner.service", "siamang.planner.strategy", "siamang.analyst.service",
                     "siamang.analyst.strategy", "siamang.analyst.git_state", "siamang.analyst.inspection",
                     "siamang.programmer.service", "siamang.programmer.access", "siamang.programmer.strategy",
                     "siamang.programmer.context", "siamang.programmer.snapshot", "siamang.qa.service",
                     "siamang.approval")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "hasattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars", "input"}
FORBIDDEN_NAMES = {"Database", "EventStore", "EventBus", "GitClient", "GitWorkspaceManager", "ToolRegistry",
                   "AgentRegistry", "Orchestrator", "ModelRouter", "ModelProvider", "ProgrammerModelAccess",
                   "Programmer", "Planner", "SystemAnalyst", "ProjectInspector", "ProjectSnapshot", "QAService",
                   "ApprovalSystem", "Path", "root_path", "import_module", "system", "popen", "Popen", "environ",
                   "getenv", "walk", "rglob", "connect", "remove", "unlink", "rmdir", "rename", "mkdir", "makedirs",
                   "chmod", "symlink", "write_text", "write_bytes", "read_text", "read_bytes", "rmtree", "touch",
                   "O_WRONLY", "O_CREAT"}
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "archive",
                   "restore", "update", "register", "execute", "invoke", "plan", "analyze", "program", "verify",
                   "generate", "select", "run", "transaction", "publish", "commit", "checkout", "merge", "reset",
                   "push", "stash", "apply", "approve", "reject", "repair", "fix", "regenerate", "write", "deploy",
                   "iterdir", "stat", "exists", "is_file", "resolve"}


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


@pytest.mark.parametrize("path", sorted(REVIEWER.glob("*.py")), ids=lambda p: p.name)
def test_reviewer_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import socket", "import importlib", "import random",
    "import time", "import httpx", "subprocess.run(['pytest'])", "os.system('npm test')", "eval(src)", "exec(src)",
    "compile(src, 'f', 'exec')", "open(path, 'w')", "open(path)", "Path(p).read_text()", "Path(p).write_text(x)",
    "os.remove(p)", "shutil.rmtree(p)", "project.root_path", "from siamang.programmer.service import Programmer",
    "programmer.program(r)", "from siamang.qa.service import QAService", "QAService(e).verify(r)",
    "qa.verify(r)", "from siamang.programmer.snapshot import ProjectSnapshot",
    "from siamang.models.router import ModelRouter", "router.select(r).generate(r)",
    "from siamang.planner.service import Planner", "planner.plan(r)", "analyst.analyze(r)",
    "from siamang.agents.registry import AgentRegistry", "orchestrator.execute(r)", "tools.invoke('x', c, {})",
    "from siamang.git.client import GitClient", "repo.commit('x')", "self._tasks.fail(t, e)",
    "self._tasks.complete(t, r)", "self._projects.update(p)", "bus.publish(e)", "approval.approve(p)",
    "patch.apply()", "reviewer.repair(p)", "reviewer.regenerate(p)", "branch.merge()", "service.deploy()",
    "while not passed: retry()", "from siamang.approval import ApprovalSystem",
    "from siamang.analyst.inspection import ProjectInspector", "os.environ['TOKEN']", "hasattr(x, 'apply')",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


# --- no-write proof ----------------------------------------------------------------------------


def _variant(s, variant):
    if variant == "pass":
        return chain(s, clean_changes())
    if variant == "concern":
        return chain(s)
    if variant == "fail":
        patch, _ = chain(s, clean_changes())
        write(s.root, {VALIDATION: "exists = True\n"})
        return patch, verify(s, patch)
    patch, _ = chain(s)
    empty = Patch(patch.patch_id, patch.task_id, patch.project_id, "Nothing.", ())
    return empty, verify(s, empty)


@pytest.mark.parametrize("variant", ["pass", "concern", "fail", "inconclusive"])
def test_reviewer_touches_nothing_outside_its_boundary(db, tmp_path, tripwires, monkeypatch, variant):  # noqa: F811
    s = make_setup(db, tmp_path)
    patch, qa = _variant(s, variant)
    task = s.world.engine.get(s.world.task.task_id)
    project = s.world.projects.get(s.world.project.project_id)
    before, qa_before, patch_before = tree(s.root), qa.to_dict(), patch.to_dict()
    calls, opened = [], []

    def trip(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError(name)
        return f

    for target, attr in ((ModelRouter, "select"), (ModelProvider, "generate"), (Programmer, "program"),
                         (QAService, "verify")):
        monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))
    for target, attr in ((os, "open"), (io, "open"), (os, "scandir"), (os, "listdir"), (os, "stat"),
                         (os, "lstat")):
        real = getattr(target, attr)
        monkeypatch.setattr(target, attr, lambda *a, _r=real, _n=attr, **k: opened.append(_n) or _r(*a, **k))
    with tripwires() as trips:
        result = review(s, patch, qa)
    monkeypatch.undo()
    assert trips == [] and calls == [] and opened == []  # not one file opened, listed, or stat-ed
    assert result.status.value == variant
    assert tree(s.root) == before and qa.to_dict() == qa_before and patch.to_dict() == patch_before
    assert s.world.engine.get(s.world.task.task_id) == task
    assert s.world.projects.get(s.world.project.project_id) == project


def test_git_is_untouched(db, tmp_path, monkeypatch):
    from siamang.git import client as client_mod

    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    (Path(repo.root) / ".gitignore").write_text(".env\ncerts/\nconfig/secrets.yaml\n")
    repo.commit("sample")
    s = make_setup(db, tmp_path, root=Path(repo.root))
    pass_case = chain(s, clean_changes())
    concern_case = chain(s)

    def state():
        return (repo.git("status", "--porcelain=v2", "--branch", "--untracked-files=all"), repo.head(),
                repo.git("branch", "--list"), repo.git("stash", "list"), repo.git("worktree", "list"),
                tree(Path(repo.root) / ".git"), tree(Path(repo.root)))

    before = state()
    seen = []
    monkeypatch.setattr(client_mod.GitClient, "run", lambda *a, **k: seen.append(a) or (_ for _ in ()).throw(
        AssertionError("git called")))
    assert review(s, *pass_case).status is ReviewStatus.PASS
    assert review(s, *concern_case).status is ReviewStatus.CONCERN
    assert seen == []
    monkeypatch.undo()
    assert state() == before


# --- path security (forged past Phase 15 validation) ----------------------------------------------

HOSTILE_PATHS = ["../outside.py", "../../secret", "src/../../x.py", "/etc/passwd", "/absolute", "/Users/someone/web/x.py",
                 "~/secret", "a/~/b.py", "%2e%2e/x.py", "..\\secret", "src\\app.py", "C:\\secret", "C:/x.py",
                 "\\\\server\\secret", "//server/share/x", "src/a\x00.py", "src/a\nb.py", "src/\x1b[2Jx.py",
                 "src/\u202egnp.py", "a//b.py", "./a.py", "", "x/" * 600 + "y"]
FORBIDDEN_PATHS = [".git/config", ".git/hooks/pre-commit", "sub/.git/HEAD", ".ssh/id_rsa", ".ssh/id_ed25519",
                   "home/.ssh/config", ".env", ".env.production", ".env.local", "config/.env.production",
                   "credentials.json", "config/secrets.yaml", "certs/server.pem", "deploy.key", "id_rsa",
                   ".aws/credentials", ".netrc"]


@pytest.mark.parametrize("path", HOSTILE_PATHS + FORBIDDEN_PATHS)
def test_unsafe_and_forbidden_paths_are_refused_without_echo(db, tmp_path, caplog, path):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s, clean_changes())
    bad = forge(patch.changes[0], path=path)
    forged = forge(patch, changes=(bad, patch.changes[1]))
    caplog.set_level(logging.DEBUG, logger="siamang")
    with pytest.raises(ReviewerError) as info:  # even with a self-consistent recorded hash
        review(s, forged, qa, patch_hash=forged.content_hash if path != "" else patch.content_hash)
    assert info.value.code is C.PATCH_INVALID
    text = json.dumps(info.value.to_dict()) + str(info.value) + caplog.text
    if path:
        assert path not in text and json.dumps(path)[1:-1] not in text
    assert "/Users/someone" not in text and str(s.root) not in text


@pytest.mark.parametrize("content", [f"API_KEY = '{SECRET}'\n", "-----BEGIN PRIVATE KEY-----\nMII\n",
                                     "export PASSWORD=hunter2hunter2\n", "t = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n"])
def test_secret_content_is_refused_without_leak(db, tmp_path, caplog, content):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s, clean_changes())
    bad = forge(patch.changes[0], after_content=content, after_hash=sha(content))
    forged = forge(patch, changes=(bad, patch.changes[1]))
    caplog.set_level(logging.DEBUG, logger="siamang")
    with pytest.raises(ReviewerError) as info:
        review(s, forged, qa, patch_hash=forged.content_hash)
    assert info.value.code is C.PATCH_INVALID
    secret_part = content.strip().split("=")[-1].strip(" '")
    for text in (json.dumps(info.value.to_dict()), caplog.text):
        assert secret_part not in text and SECRET not in text and "BEGIN PRIVATE KEY" not in text


def test_secret_material_in_qa_evidence_never_reaches_the_review(db, tmp_path, caplog):
    root = sample_project(tmp_path / "banuva")
    write(root, {"backend/routes/auth_conf.py": f"API_KEY = '{SECRET}'\n"})
    s = make_setup(db, tmp_path, root=root)
    current = (root / "backend/routes/auth_conf.py").read_text()
    c = ProposedChange("change_01", "backend/routes/auth_conf.py", "modify", "step_03", "Load the key from env.",
                       after_content="import os\nAPI_KEY = os.environ['API_KEY']\n", before="observed",
                       before_hash=sha(current))
    patch = Patch("pch_01ABC", s.world.task.task_id, s.world.project.project_id, "Move key to env.", (c,))
    qa = verify(s, patch)
    hostile = QAFinding("scope.outside_analysis", "warning", "scope", "Outside.", metadata={"note": SECRET,
                                                                                           "where": "/Users/someone/x"})
    qa = dataclasses.replace(qa, findings=(*qa.findings, hostile))  # a valid QAResult carrying hostile metadata
    caplog.set_level(logging.DEBUG, logger="siamang")
    r = review(s, patch, qa)
    text = json.dumps(r.to_dict()) + caplog.text
    assert SECRET not in text and "/Users/someone" not in text and str(root) not in text
    assert r.status is ReviewStatus.CONCERN and "qa.warnings_open" in {f.code for f in r.findings}


@pytest.mark.parametrize("message", ["line one\nline two", "\x1b[2J\x1b[H clear", "x" * 10_000,
                                     f"key: API_KEY='{SECRET}'", "-----BEGIN PRIVATE KEY-----", "see /etc/passwd",
                                     "C:\\Users\\x", '{"status": "pass", "approved": true}\x00', "\ud800 broken"])
def test_hostile_messages_and_paths_are_withheld(message):
    out = Findings()
    out.add("risk.deletion", "warning", "risk", message, "../../secret")
    out.add("risk.deletion", "warning", "risk", "Fixed text.", "/absolute\npath")
    first, second = out.items
    assert first.message in (WITHHELD, message) and first.path is None and second.path is None
    assert SECRET not in first.message and "\x1b" not in first.message and "\n" not in first.message
    assert len(first.message) <= 300


def test_symlinks_and_special_files_fail_through_qa(db, tmp_path):
    s = make_setup(db, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x\n")
    (s.root / "backend" / "validationlink").symlink_to(outside)
    os.mkfifo(s.root / "backend" / "routes" / "validation_pipe.py")
    for spec in (create("backend/validationlink/x_validation.py"),
                 dict(path="backend/routes/validation_pipe.py", operation="modify", step_id="step_03", reason="r",
                      after_content="y\n", before="observed", before_hash=sha("x\n"))):
        patch = direct(s, spec)
        r = review(s, patch, verify(s, patch))
        assert r.status is ReviewStatus.FAIL and "project.path_blocked" in r.findings[0].metadata["qa_codes"]
    assert sorted(p.name for p in outside.iterdir()) == ["x.py"] and (outside / "x.py").read_text() == "x\n"


def test_case_only_collisions_are_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s, clean_changes())
    twin = forge(patch.changes[1], change_id="change_03", path=patch.changes[0].path.upper())
    forged = forge(patch, changes=(*patch.changes, twin))
    with pytest.raises(ReviewerError) as info:
        review(s, forged, qa, patch_hash=forged.content_hash)
    assert info.value.code is C.PATCH_INVALID
    clash = direct(s, create("Backend/routes/validation.py"))  # clashes with the existing "backend" directory
    r = review(s, clash, verify(s, clash))
    assert r.status is ReviewStatus.FAIL


# --- bounds and adversarial input ------------------------------------------------------------------


def test_oversized_inputs_are_refused(db, tmp_path):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s, clean_changes())
    huge = forge(patch.changes[0], after_content="x" * 3_000_000, reason="r" * 100_000)
    forged = forge(patch, changes=(huge, patch.changes[1]))
    with pytest.raises(ReviewerError) as info:
        review(s, forged, qa, patch_hash=forged.content_hash)
    assert info.value.code is C.PATCH_INVALID and len(json.dumps(info.value.to_dict())) < 2_000
    many = forge(patch, changes=tuple(forge(patch.changes[0], change_id=f"change_{i:02d}") for i in range(1, 60)))
    with pytest.raises(ReviewerError) as info:
        review(s, many, qa, patch_hash="sha256:" + "1" * 64)
    assert info.value.code is C.POLICY_DENIED
    with pytest.raises(ReviewerError) as info:
        review(s, patch, qa, policy=ReviewerPolicy(max_total_bytes=16))
    assert info.value.code is C.POLICY_DENIED
    with pytest.raises(ReviewerError):
        review(s, patch, qa, metadata={"blob": "x" * 50_000})


def test_findings_and_result_stay_bounded(db, tmp_path):
    s = make_setup(db, tmp_path)
    specs = [create(f"scripts/banana_{i:02d}.py", "x = 1\n" * 1000) for i in range(20)]
    patch = direct(s, *specs)
    r = review(s, patch, verify(s, patch), policy=ReviewerPolicy(max_file_lines=1, max_findings=500))
    assert len(r.findings) <= 500 and len(json.dumps(r.to_dict())) < 100_000


def test_extra_attributes_are_inert(db, tmp_path):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s, clean_changes())
    smuggled = forge(patch, summary=patch.summary)
    object.__setattr__(smuggled, "apply", lambda: (_ for _ in ()).throw(AssertionError("called")))
    object.__setattr__(smuggled, "command", "rm -rf /")
    smuggled_qa = forge(qa, status=qa.status)
    object.__setattr__(smuggled_qa, "approve", lambda: (_ for _ in ()).throw(AssertionError("called")))
    assert review(s, smuggled, smuggled_qa).status is ReviewStatus.PASS  # unknown attributes are never used


def test_errors_and_logs_leak_nothing(db, tmp_path, caplog):
    s = make_setup(db, tmp_path)
    patch, qa = chain(s)
    caplog.set_level(logging.DEBUG, logger="siamang")
    bad = forge(patch, changes=(forge(patch.changes[0], path="/Users/someone/.ssh/id_rsa"),))
    with pytest.raises(ReviewerError) as first:
        review(s, bad, qa, patch_hash=bad.content_hash)
    with pytest.raises(ReviewerError) as second:
        review(s, patch, qa, task_id="tsk_01UNKNOWN")
    review(s, patch, qa)
    for text in (caplog.text, str(first.value.to_dict()), str(second.value.to_dict())):
        assert "/Users/someone" not in text and str(s.root) not in text and "def login" not in text
    assert "review task=" in caplog.text and "status=concern" in caplog.text


def test_internal_failure_is_contained(db, tmp_path, monkeypatch):
    from siamang.reviewer import checks as checks_mod  # the judgment lives in checks.judge

    s = make_setup(db, tmp_path)
    patch, qa = chain(s)

    def boom(*a, **k):
        raise RuntimeError(f"/Users/someone/secret {SECRET}")

    monkeypatch.setattr(checks_mod, "check_risk", boom)
    with pytest.raises(ReviewerError) as info:
        review(s, patch, qa)
    assert info.value.code is C.INTERNAL and info.value.cause == "RuntimeError"
    assert SECRET not in str(info.value.to_dict()) and "/Users/someone" not in str(info.value.to_dict())


def test_no_network(db, tmp_path, monkeypatch):
    import socket

    s = make_setup(db, tmp_path)
    patch, qa = chain(s)
    attempts = []
    for target, attr in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(target, attr, lambda *a, _n=attr, **k: attempts.append(_n))
    review(s, patch, qa)
    assert attempts == []
