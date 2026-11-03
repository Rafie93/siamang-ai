"""Phase 19.5: the whole chain has no side effects, and the architecture still holds.

* No-write proof: Programmer -> QA -> Reviewer -> Loop -> Approval run under
  tripwires on every write, delete, rename, mkdir, process, shell, Git,
  network, Planner/Analyst/agent/tool/orchestrator/event/task-mutation entry
  point; a real Git project stays byte-identical (working tree and .git).
* Network: zero socket activity. Model: the only model calls are the
  Programmer's, exactly one per round; QA, Reviewer, Loop, and Approval make none.
* Architecture: static import audit (Approval reaches no service; QA, Reviewer,
  Loop, Approval are acyclic).
* CLI smoke: read-only commands work, JSON is valid, invalid input fails safely.
* Self-test: the real ~/web/siamang repository is the project; a
  documentation-only proposal is produced, verified, judged, and decided on
  as its evidence permits; nothing is applied and Git is unchanged.
"""

import ast
import builtins
import contextlib
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from siamang.agents import AgentRegistry
from siamang.analyst import SystemAnalyst
from siamang.approval import ApprovalService, ApprovalStatus
from siamang.approval.errors import ApprovalError
from siamang.bus import EventBus
from siamang.cli.main import main
from siamang.git import client as git_client
from siamang.loop import LoopPolicy, LoopRequest, LoopService
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter
from siamang.orchestrator import Orchestrator
from siamang.planner import Planner
from siamang.programmer import Programmer
from siamang.qa import QAService
from siamang.reviewer import ReviewerService
from siamang.storage import Database, EventStore
from siamang.tasks import TaskEngine
from siamang.tools import ToolRegistry
from tests.support.analyst import sample_project
from tests.support.approval import CLEAN, CONCERN, approve, reject
from tests.support.chain import build, files, git_state
from tests.support.git import make_repo
from tests.support.loop import scripted
from tests.support.programmer import INSTRUCTION, change, reply

REPO = Path(__file__).parents[2]
SRC = REPO / "src" / "siamang"
WRITE_MODES = set("wax+")


@contextlib.contextmanager
def tripwires():
    """Arm every forbidden entry point; collect what was touched. Disarmed on exit, before any assert."""
    hits: list[str] = []
    mp = pytest.MonkeyPatch()

    def trip(name):
        def f(*a, **k):
            hits.append(name)
            raise AssertionError(f"{name} during the chain")
        return f

    for target, attr in [
        (subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (subprocess, "check_call"),
        (subprocess, "check_output"), (os, "system"), (os, "popen"), (os, "execv"), (os, "execve"), (os, "fork"),
        (os, "remove"), (os, "unlink"), (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"),
        (os, "rmdir"), (os, "removedirs"), (os, "symlink"), (os, "link"), (os, "chmod"), (os, "chown"),
        (os, "truncate"), (os, "write"), (os, "utime"), (os, "mkfifo"),
        (pathlib.Path, "write_text"), (pathlib.Path, "write_bytes"), (pathlib.Path, "mkdir"),
        (pathlib.Path, "rmdir"), (pathlib.Path, "unlink"), (pathlib.Path, "touch"), (pathlib.Path, "rename"),
        (pathlib.Path, "replace"), (pathlib.Path, "symlink_to"), (pathlib.Path, "chmod"),
        (shutil, "rmtree"), (shutil, "copy"), (shutil, "copy2"), (shutil, "copyfile"), (shutil, "copytree"),
        (shutil, "move"),
        (socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "bind"),
        (socket.socket, "sendto"), (socket, "create_connection"), (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (git_client.GitClient, "run"), (ToolRegistry, "invoke"), (AgentRegistry, "invoke"),
        (Orchestrator, "execute"), (Planner, "plan"), (SystemAnalyst, "analyze"), (EventBus, "publish"),
        (EventStore, "append"), (Database, "transaction"),
        (TaskEngine, "create"), (TaskEngine, "start"), (TaskEngine, "wait"), (TaskEngine, "requeue"),
        (TaskEngine, "complete"), (TaskEngine, "fail"), (TaskEngine, "cancel"), (TaskEngine, "retry"),
    ]:
        mp.setattr(target, attr, trip(f"{getattr(target, '__name__', target)}.{attr}"))
    real_open, real_io_open = builtins.open, io.open

    def guarded(real):
        def opener(file, mode="r", *a, **k):
            if WRITE_MODES & set(mode):
                hits.append(f"open:{mode}")
                raise AssertionError("write-mode open during the chain")
            return real(file, mode, *a, **k)
        return opener

    def guarded_os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            hits.append("os.open:write")
            raise AssertionError("write-mode os.open during the chain")
        return real_os_open(path, flags, *a, **k)

    real_os_open = os.open
    mp.setattr(builtins, "open", guarded(real_open))
    mp.setattr(io, "open", guarded(real_io_open))
    mp.setattr(os, "open", guarded_os_open)
    for name in ("eval", "exec"):
        mp.setattr(builtins, name, trip(name))
    try:
        yield hits
    finally:
        mp.undo()


class Audit:
    """Which component is running when a model is called (model calls are legitimate only inside Programmer)."""

    def __init__(self, mp):
        self.stack: list[str] = []
        self.model_calls: list[tuple[str, ...]] = []
        for cls, attr, name in ((Programmer, "program", "programmer"), (QAService, "verify", "qa"),
                                (ReviewerService, "review", "reviewer"), (LoopService, "run", "loop"),
                                (ApprovalService, "decide", "approval"), (ApprovalService, "verify", "approval")):
            mp.setattr(cls, attr, self._enter(getattr(cls, attr), name))
        for cls, attr in ((ModelRouter, "select"), (ModelProvider, "generate")):
            mp.setattr(cls, attr, self._model(getattr(cls, attr), attr))

    def _enter(self, real, name):
        audit = self

        def wrapper(obj, *a, **k):
            audit.stack.append(name)
            try:
                return real(obj, *a, **k)
            finally:
                audit.stack.pop()
        return wrapper

    def _model(self, real, attr):
        audit = self

        def wrapper(obj, *a, **k):
            audit.model_calls.append((attr, *audit.stack))
            return real(obj, *a, **k)
        return wrapper


def _project_repo(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    (Path(repo.root) / ".gitignore").write_text(".env\ncerts/\nconfig/secrets.yaml\n")
    repo.commit("sample")
    return repo


def _repo_state(repo):
    return (repo.git("status", "--porcelain=v2", "--branch", "--untracked-files=all"), repo.head(),
            repo.git("branch", "--list"), repo.git("stash", "list"), repo.git("worktree", "list"),
            repo.git("diff", "--stat"), files(Path(repo.root)))


# --- 21. no-write proof: the whole Programmer -> ... -> Approval chain under tripwires --------------------------


@pytest.mark.parametrize("replies", [(CLEAN,), (CONCERN, CLEAN), (CONCERN, CONCERN)], ids=["one", "two", "stuck"])
def test_whole_chain_writes_nothing_and_leaves_git_byte_identical(db, tmp_path, replies):
    repo = _project_repo(tmp_path)
    root = Path(repo.root)
    warm = Database(tmp_path / "warm.db")
    build(warm, root, *replies)  # warm-up run: every lazy import is loaded before the tripwires are armed
    warm.close()
    c = build(db, root, *replies)  # Task, Plan, and SystemAnalysis are made before arming (they may persist)
    before = _repo_state(repo)
    task = c.engine.get(c.task.task_id)
    loop_request = LoopRequest(c.task.task_id, c.project.project_id, INSTRUCTION, c.plan, c.analysis)
    model = scripted(*replies)
    service = LoopService(c.engine, Programmer(c.engine, models=model.router), QAService(c.engine),
                          ReviewerService(c.engine))
    with tripwires() as hits:
        loop = service.run(loop_request)
        request = type(c.request)(c.task.task_id, c.project.project_id, c.plan, c.analysis, loop)
        decision = approve() if loop.passed else reject()
        result = c.service.decide(request, decision)
        c.service.verify(result, request)
    assert hits == []
    assert result.status is (ApprovalStatus.APPROVED if loop.passed else ApprovalStatus.REJECTED)
    assert _repo_state(repo) == before  # working tree, .git, HEAD, branches, stash, worktrees, diff
    assert c.engine.get(c.task.task_id) == task  # the task was not touched
    assert loop.final_patch is not None and not any(
        (root / ch.path).exists() and ch.operation.value == "create" for ch in loop.final_patch.changes)


def test_the_tripwires_actually_fire(tmp_path):
    for attempt in (lambda: (tmp_path / "x").write_text("x"), lambda: open(tmp_path / "y", "w"),
                    lambda: os.open(tmp_path / "z", os.O_CREAT | os.O_WRONLY), lambda: os.mkdir(tmp_path / "d"),
                    lambda: subprocess.run(["true"]), lambda: socket.create_connection(("example.com", 80)),
                    lambda: git_client.GitClient.run(None)):
        with tripwires() as hits:
            with pytest.raises(AssertionError):
                attempt()
        assert hits, attempt
    assert not any(tmp_path.iterdir())


# --- 22-23. network and model audit ---------------------------------------------------------------------


def test_no_network_and_models_only_inside_the_programmer(db, tmp_path, monkeypatch):
    sockets = []
    real_socket = socket.socket

    class CountingSocket(real_socket):
        def __init__(self, *a, **k):
            sockets.append(a)
            super().__init__(*a, **k)

    monkeypatch.setattr(socket, "socket", CountingSocket)
    audit = Audit(monkeypatch)
    c = build(db, sample_project(tmp_path / "banuva"), CONCERN, CLEAN)
    result = c.service.decide(c.request, approve())
    c.service.verify(result, c.request)
    c.service.decide(c.request, reject())
    assert sockets == []  # network calls: 0
    assert c.model.calls == 2 == len(c.loop.iterations)  # exactly one model call per Programmer round
    assert audit.model_calls and all(call[-1] == "programmer" for call in audit.model_calls)
    for stage in ("qa", "reviewer", "approval"):
        assert not any(stage in call for call in audit.model_calls), stage


def test_approval_invokes_no_upstream_service_or_model(db, tmp_path, monkeypatch):
    c = build(db, sample_project(tmp_path / "banuva"), CLEAN)
    calls = []
    for cls, attr in ((Programmer, "program"), (QAService, "verify"), (ReviewerService, "review"),
                      (LoopService, "run"), (Planner, "plan"), (SystemAnalyst, "analyze"), (ModelRouter, "select"),
                      (ModelProvider, "generate"), (Orchestrator, "execute"), (AgentRegistry, "invoke"),
                      (ToolRegistry, "invoke")):
        monkeypatch.setattr(cls, attr, lambda *a, _n=f"{cls.__name__}.{attr}", **k: calls.append(_n))
    result = c.service.decide(c.request, approve())
    c.service.verify(result, c.request)
    assert calls == [] and result.approved


# --- 24. architecture import audit ---------------------------------------------------------------------


def _module(path: Path) -> str:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _graph() -> dict[str, set[str]]:
    modules = {_module(p): p for p in SRC.rglob("*.py")}
    graph = {}
    for name, path in modules.items():
        deps = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                deps |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                deps.add(node.module)
                deps |= {f"{node.module}.{a.name}" for a in node.names}
        graph[name] = {d for d in deps if d in modules}
    return graph


def _reachable(graph, start):
    seen, stack = set(), list(start)
    while stack:
        for dep in graph[stack.pop()]:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


FORBIDDEN_FOR_APPROVAL = ("siamang.programmer.service", "siamang.qa.service", "siamang.reviewer.service",
                          "siamang.loop.service", "siamang.planner.service", "siamang.analyst.service",
                          "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.git",
                          "siamang.models.router", "siamang.models.registry", "siamang.programmer.access",
                          "siamang.cli")


def test_approval_source_reaches_no_service_transitively():
    """Through the modules Approval's code imports (not package re-exports), no service is reachable.

    Known and accepted: tasks.engine (read-only TaskEngine.get) brings storage and the bus along as module
    dependencies; Approval never calls them (tripwires above)."""
    graph = _graph()
    reached = _reachable(graph, [m for m in graph if m.startswith("siamang.approval")])
    bad = sorted(m for m in reached if m.startswith(FORBIDDEN_FOR_APPROVAL))
    assert bad == []


def test_qa_reviewer_loop_approval_are_acyclic():
    graph = _graph()
    layers = ("qa", "reviewer", "loop", "approval")
    edges = {layer: set() for layer in layers}
    for name, deps in graph.items():
        src = name.split(".")[1] if name.count(".") else None
        if src in layers:
            edges[src] |= {d.split(".")[1] for d in deps if d.count(".") and d.split(".")[1] in layers} - {src}
    assert edges == {"qa": set(), "reviewer": {"qa"}, "loop": {"qa", "reviewer"},
                     "approval": {"qa", "reviewer", "loop"}}
    for layer in ("programmer", "planner", "analyst", "tasks", "projects", "storage", "models"):
        for name, deps in graph.items():
            if name.startswith(f"siamang.{layer}"):
                assert not any(d.startswith(("siamang.qa", "siamang.reviewer", "siamang.loop", "siamang.approval"))
                               for d in deps), name


def test_no_future_layer_was_added():
    for name in ("websocket", "executor", "execution", "runtime", "events", "auth", "applier",
                 "deploy", "shell", "writer", "generator"):
        assert not (SRC / name).exists() and not (SRC / f"{name}.py").exists(), name


# --- 25. CLI smoke --------------------------------------------------------------------------------------


READ_ONLY = ["version", "health", "validate", "approval", "loop", "reviewer", "qa", "programmer", "analyst",
             "planner", "orchestrator", "agents", "tools"]
WITH_JSON = ["approval", "loop", "reviewer", "qa", "programmer", "analyst", "planner", "orchestrator", "agents",
             "tools"]


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIAMANG_STORAGE_PATH", str(tmp_path / "cli" / "siamang.db"))
    before = files(REPO / "src"), files(REPO / "docs")
    yield
    assert (files(REPO / "src"), files(REPO / "docs")) == before


@pytest.mark.parametrize("command", READ_ONLY)
def test_cli_read_only_commands_work(cli_env, capsys, command):
    assert main([command]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("command", WITH_JSON)
def test_cli_json_is_valid(cli_env, capsys, command):
    assert main([command, "--json"]) == 0
    json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("argv", [["approval", "approve"], ["approval", "--force"], ["loop", "run"],
                                  ["programmer", "apply"], ["qa", "verify"], ["reviewer", "approve"],
                                  ["execute"], ["apply"], ["commit"]])
def test_cli_has_no_execution_command(cli_env, argv):
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2


def test_cli_invalid_input_fails_safely(cli_env, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"broken":')
    assert main(["validate", str(bad)]) != 0
    assert main(["validate", str(tmp_path / "missing.json")]) != 0
    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err


# --- 26. real Siamang self-test ----------------------------------------------------------------------------


SELF_TASK = "Analyze the current repository and propose a documentation-only improvement. Do not modify any file."
SELF_NOTE = "docs/REPOSITORY_ANALYSIS_IMPROVEMENT.md"


def _self_replies():
    note = "# Repository analysis\n\nA documentation-only improvement proposed by the self-test.\n"
    test = "def test_repository_analysis_note():\n    assert True\n"
    return reply([change(SELF_NOTE, "create", note, reason="Documentation-only improvement."),
                  change("tests/test_repository_analysis.py", "create", test, step_id="step_04",
                         reason="Validation for the documentation improvement.")],
                 summary="Document the repository analysis.")


def test_self_test_on_the_real_siamang_repository(db):
    git = (REPO / ".git").exists()
    before_git = git_state(REPO) if git else None
    before_files = files(REPO / "src"), files(REPO / "docs"), (REPO / "pyproject.toml").read_bytes()
    c = build(db, REPO, _self_replies(), _self_replies(), _self_replies(), instruction=SELF_TASK, name="Siamang")
    loop = c.loop
    assert loop.final_patch is not None and all(ch.path.startswith(("docs/", "tests/"))
                                                for ch in loop.final_patch.changes)
    assert c.analysis.project_id == c.project.project_id and c.project.root_path == str(REPO)
    if loop.passed:
        result = c.service.decide(c.request, approve(reason="Documentation only."))
    else:
        result = c.service.decide(c.request, reject(reason="Evidence did not pass."))
    c.service.verify(result, c.request)
    assert result.status is (ApprovalStatus.APPROVED if loop.passed else ApprovalStatus.REJECTED)
    assert not (REPO / SELF_NOTE).exists()  # proposed, never applied
    assert (files(REPO / "src"), files(REPO / "docs"), (REPO / "pyproject.toml").read_bytes()) == before_files
    if git:
        assert git_state(REPO) == before_git


def test_self_test_evidence_decides_the_outcome(db):
    """On the real repository the Analyst's scope is ``tests/`` only, so QA warns that a docs/ change lies outside
    the analysis and the Reviewer raises a concern: the honest evidence makes the proposal ineligible. Approval
    refuses it (never overridden) and an authorized approver may still reject it."""
    c = build(db, REPO, _self_replies(), instruction=SELF_TASK, name="Siamang",
              loop_policy=LoopPolicy(retry_statuses=set()))
    assert {(f.code, f.path) for f in c.loop.final_qa_result.findings} == {("scope.outside_analysis", SELF_NOTE)}
    assert (c.loop.status.value, c.loop.stop_reason.value) == ("concern", "not_retryable")
    with pytest.raises(ApprovalError) as info:
        c.service.decide(c.request, approve())
    assert info.value.code.value == "approval_not_eligible"
    assert c.service.decide(c.request, reject(reason="Docs lie outside the analysis.")).status \
        is ApprovalStatus.REJECTED
