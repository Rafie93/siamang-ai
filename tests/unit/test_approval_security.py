"""Approval security: an authorization boundary that reads, writes, executes, and calls nothing it should not.

Static checks (with deliberate-break proofs) cover every Approval module.
Runtime tripwires prove decisions (approved and rejected) are still made,
with zero files opened, while every write, process, network, import, Git,
model, Programmer, QA, Reviewer, Loop, Planner, Analyst, agent, tool,
orchestrator, event, and task-mutation entry point explodes on contact.
"""

import ast
import dataclasses
import io
import json
import logging
import os
import socket
from pathlib import Path

import pytest

from siamang.approval.errors import ApprovalError
from siamang.loop.service import LoopService
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter
from siamang.programmer.service import Programmer
from siamang.qa.service import QAService
from siamang.reviewer.service import ReviewerService
from tests.support.analyst import SECRET, sample_project
from tests.support.approval import CLEAN, approval_request, approve, loop_result, reject, service
from tests.support.git import make_repo
from tests.support.programmer import make_setup, sha, tree
from tests.support.qa import forge
from tests.unit.test_programmer_security import tripwires  # noqa: F401  (shared runtime tripwire fixture)

SRC = Path(__file__).parents[2] / "src" / "siamang"
APPROVAL = SRC / "approval"

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "tempfile", "io", "asyncio", "threading",
                     "multiprocessing", "concurrent", "random", "uuid", "time", "datetime", "pickle", "marshal",
                     "ctypes", "runpy", "code", "codeop", "unittest", "doctest", "pytest", "git", "openai",
                     "anthropic", "ollama", "requests", "httpx", "aiohttp", "jwt", "oauthlib", "cryptography",
                     "rsa", "ecdsa"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.bus",
                     "siamang.config", "siamang.cli", "siamang.git", "siamang.contracts", "siamang.models",
                     "siamang.planner.service", "siamang.analyst.service", "siamang.analyst.inspection",
                     "siamang.programmer.service", "siamang.programmer.access", "siamang.programmer.snapshot",
                     "siamang.qa.service", "siamang.reviewer.service", "siamang.loop.service", "siamang.api",
                     "siamang.events")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "hasattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars", "input", "print"}
FORBIDDEN_NAMES = {"Database", "EventStore", "EventBus", "GitClient", "GitWorkspaceManager", "ToolRegistry",
                   "AgentRegistry", "Orchestrator", "ModelRouter", "ModelProvider", "Programmer", "QAService",
                   "ReviewerService", "LoopService", "Planner", "SystemAnalyst", "ProjectInspector", "ProjectSnapshot",
                   "Path", "root_path", "environ", "getenv", "system", "popen", "Popen", "connect", "remove", "unlink",
                   "rename", "mkdir", "chmod", "write_text", "write_bytes", "read_text", "read_bytes", "rmtree",
                   "apply_patch", "write_file", "force_approve", "emergency_approve", "skip_qa", "skip_review",
                   "skip_provenance", "approve_and_apply", "approve_and_commit", "time", "now", "utcnow"}
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "archive", "restore",
                   "update", "register", "execute", "invoke", "plan", "analyze", "program", "verify", "run",
                   "review", "generate", "select", "transaction", "publish", "emit", "commit", "checkout", "merge",
                   "reset", "push", "pull", "stash", "rebase", "apply", "deploy", "write", "persist", "save"}


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
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.While, ast.Global)):
            found.add(type(node).__name__)
    return found


@pytest.mark.parametrize("path", sorted(APPROVAL.glob("*.py")), ids=lambda p: p.name)
def test_approval_sources_are_clean(path):
    assert violations(path.read_text(encoding="utf-8")) == set()


def _code_tokens(path: Path) -> set[str]:
    """Identifiers and string literals of the code itself (docstrings, which may say what does NOT exist, excluded)."""
    tree_ = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.value) for n in ast.walk(tree_) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    out = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.add(node.value.lower())
        elif isinstance(node, ast.Name):
            out.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            out.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.add(node.name.lower())
        elif isinstance(node, ast.arg):
            out.add(node.arg.lower())
    return out


def test_no_override_or_magic_approver_in_the_code():
    tokens = set().union(*(_code_tokens(p) for p in APPROVAL.glob("*.py")))
    for word in ("admin", "root", "owner", "force", "force_approve", "emergency", "skip_qa", "bypass", "is_admin",
                 "superuser", "override_evidence", "auto_approve"):
        assert word not in tokens, word


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import socket", "import time", "import jwt", "import cryptography",
    "subprocess.run(['sh'])", "eval(reason)", "open(p, 'w')", "Path(p).write_text(x)", "patch.apply()",
    "approve_and_apply(r)", "repo.commit('x')", "repo.merge('b')", "bus.publish(e)", "events.emit(e)",
    "programmer.program(r)", "qa_service.verify(r)", "reviewer.review(r)", "loop.run(r)",
    "from siamang.loop.service import LoopService", "from siamang.qa.service import QAService",
    "from siamang.models.router import ModelRouter", "self._tasks.complete(t, r)", "force_approve(r)",
    "if approver == 'admin': skip_qa()", "while True: pass", "datetime.now()", "os.environ['APPROVERS']",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


# --- no-write proof ----------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_touches_nothing_outside_its_boundary(db, tmp_path, tripwires, monkeypatch, decision):  # noqa: F811
    s = make_setup(db, tmp_path)
    req = approval_request(s)
    loop = req.loop_result
    snapshot = json.dumps([s.plan.to_dict(), s.analysis.to_dict(), loop.to_dict(), loop.final_patch.to_dict(),
                           loop.final_qa_result.to_dict(), loop.final_review_result.to_dict()], sort_keys=True)
    task = s.world.engine.get(s.world.task.task_id)
    project = s.world.projects.get(s.world.project.project_id)
    files = tree(s.root)
    svc = service(s)
    calls, opened = [], []

    def trip(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError(name)
        return f

    for target, attr in ((ModelRouter, "select"), (ModelProvider, "generate"), (Programmer, "program"),
                         (QAService, "verify"), (ReviewerService, "review"), (LoopService, "run")):
        monkeypatch.setattr(target, attr, trip(f"{target.__name__}.{attr}"))
    for target, attr in ((os, "open"), (io, "open"), (os, "scandir"), (os, "listdir"), (os, "stat"), (os, "lstat")):
        real = getattr(target, attr)
        monkeypatch.setattr(target, attr, lambda *a, _r=real, _n=attr, **k: opened.append(_n) or _r(*a, **k))
    with tripwires() as trips:
        result = svc.decide(req, approve() if decision == "approve" else reject())
        svc.verify(result, req)
    monkeypatch.undo()
    assert trips == [] and calls == [] and opened == []  # not one file opened, listed, or stat-ed
    assert result.status.value == decision + "d" if decision == "approve" else result.status.value == "rejected"
    assert json.dumps([s.plan.to_dict(), s.analysis.to_dict(), loop.to_dict(), loop.final_patch.to_dict(),
                       loop.final_qa_result.to_dict(), loop.final_review_result.to_dict()], sort_keys=True) == snapshot
    assert tree(s.root) == files and s.world.engine.get(task.task_id) == task
    assert s.world.projects.get(project.project_id) == project


def test_git_is_untouched(db, tmp_path, monkeypatch):
    from siamang.git import client as client_mod

    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    (Path(repo.root) / ".gitignore").write_text(".env\ncerts/\nconfig/secrets.yaml\n")
    repo.commit("sample")
    s = make_setup(db, tmp_path, root=Path(repo.root))
    req = approval_request(s)

    def state():
        return (repo.git("status", "--porcelain=v2", "--branch", "--untracked-files=all"), repo.head(),
                repo.git("branch", "--list"), repo.git("stash", "list"), repo.git("worktree", "list"),
                tree(Path(repo.root) / ".git"), tree(Path(repo.root)))

    before = state()
    seen = []
    monkeypatch.setattr(client_mod.GitClient, "run", lambda *a, **k: seen.append(a) or (_ for _ in ()).throw(
        AssertionError("git called")))
    svc = service(s)
    svc.verify(svc.decide(req, approve()), req)
    svc.decide(req, reject())
    assert seen == []
    monkeypatch.undo()
    assert state() == before


def test_no_network_and_no_model(db, tmp_path, monkeypatch):
    s = make_setup(db, tmp_path)
    req = approval_request(s)
    attempts = []
    for target, attr in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo"),
                         (ModelRouter, "select"), (ModelProvider, "generate")):
        monkeypatch.setattr(target, attr, lambda *a, _n=attr, **k: attempts.append(_n))
    svc = service(s)
    svc.verify(svc.decide(req, approve()), req)
    assert attempts == []


# --- hostile input and secrets ---------------------------------------------------------------------


def test_errors_and_logs_leak_nothing(db, tmp_path, caplog):
    s = make_setup(db, tmp_path)
    req = approval_request(s)
    svc = service(s)
    caplog.set_level(logging.DEBUG, logger="siamang")
    texts = []
    for approver, reason in (("mallory", f"API_KEY='{SECRET}'"), ("../../admin", "x"), ("\x1b[31madmin", "y"),
                             ("dewi", "line\ninjection"), ("dewi", '{"approved": true, "override": "yes"}\x00'),
                             ("a" * 5000, "z"), ("dewi", "/Users/someone/.ssh/id_rsa")):
        with pytest.raises(ApprovalError) as info:
            from siamang.approval import ApprovalDecision

            svc.decide(req, ApprovalDecision("reject", approver, reason))
        texts.append(json.dumps(info.value.to_dict()))
    svc.decide(req, approve())
    text = " ".join(texts) + caplog.text
    for leak in (SECRET, "../../admin", "\x1b[31m", "/Users/someone", "override", "a" * 100, str(s.root)):
        assert leak not in text, leak
    assert "approval task=" in caplog.text and "def valid" not in caplog.text


def test_secret_or_hostile_path_in_a_forged_patch_is_refused(db, tmp_path, caplog):
    s = make_setup(db, tmp_path)
    loop = loop_result(s, CLEAN)
    patch = loop.final_patch
    caplog.set_level(logging.DEBUG, logger="siamang")
    content = f"API_KEY = '{SECRET}'\n"
    for change in (forge(patch.changes[0], after_content=content, after_hash=sha(content)),
                   forge(patch.changes[0], path="../../.ssh/id_rsa"), forge(patch.changes[0], path=".env"),
                   forge(patch.changes[0], metadata={"note": [1]})):
        forged = forge(loop, final_patch=forge(patch, changes=(change, *patch.changes[1:])))
        with pytest.raises(ApprovalError) as info:
            approval_request(s, forged)
        text = json.dumps(info.value.to_dict()) + caplog.text
        assert info.value.code.value == "approval_provenance_invalid"
        assert SECRET not in text and "../../.ssh" not in text and ".env" not in text


def test_internal_failure_is_contained(db, tmp_path, monkeypatch):
    from siamang.approval import service as service_mod

    s = make_setup(db, tmp_path)
    req = approval_request(s)

    def boom(*a, **k):
        raise RuntimeError(f"/Users/someone/secret {SECRET}")

    monkeypatch.setattr(service_mod, "rederive", boom)
    with pytest.raises(ApprovalError) as info:
        service(s).decide(req, approve())
    assert info.value.code.value == "approval_internal" and info.value.cause == "RuntimeError"
    assert SECRET not in json.dumps(info.value.to_dict()) and "/Users/someone" not in json.dumps(info.value.to_dict())


def test_results_hold_no_contents(db, tmp_path):
    s = make_setup(db, tmp_path)
    result = service(s).decide(approval_request(s), approve())
    text = json.dumps(result.to_dict())
    assert "def valid" not in text and "def test_valid" not in text
    assert set(dataclasses.asdict(result.provenance)) == {
        "task_id", "project_id", "plan_id", "plan_hash", "analysis_id", "analysis_hash", "loop_id", "loop_hash",
        "final_iteration", "patch_id", "patch_hash", "qa_id", "qa_hash", "review_id", "review_hash"}
