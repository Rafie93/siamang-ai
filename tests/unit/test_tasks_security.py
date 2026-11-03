"""Task boundary security: state only. No shell, process, network, filesystem scan, Git,
tool, or model call from any lifecycle method; hostile input is refused before it is stored."""

import ast
import os
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

from siamang.git import client as client_mod
from siamang.models.provider import ModelProvider
from siamang.models.router import ModelRouter, Selection
from siamang.tasks import (
    InvalidTaskError,
    TaskFailure,
    TaskProjectArchivedError,
    TaskProjectNotFoundError,
    TaskRequest,
    TaskResult,
)
from siamang.tools.registry import ToolRegistry
from tests.support.tasks import make_engine, request

SRC = Path(__file__).parents[2] / "src" / "siamang"
TASK_MODULES = sorted((SRC / "tasks").glob("*.py")) + [SRC / "storage" / "tasks.py"]

FORBIDDEN_MODULES = {"subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "shutil", "glob",
                     "pathlib", "os", "asyncio", "multiprocessing", "concurrent", "threading", "pty", "importlib",
                     "pickle", "marshal", "ctypes", "runpy", "dotenv", "git", "pygit2", "dulwich",
                     "openai", "anthropic", "ollama"}
# Builtins that load/run code: forbidden as bare names (re.compile stays legal).
FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "__import__", "open", "breakpoint"}
FORBIDDEN_NAMES = {"system", "popen", "Popen", "environ", "getenv", "putenv", "listdir", "scandir", "walk",
                   "iterdir", "rglob", "load_dotenv", "GitClient", "ToolRegistry", "ModelRouter", "invoke",
                   "generate"}


@pytest.fixture
def engine(db):
    return make_engine(db)[0]


@pytest.fixture
def project(engine):
    return engine.projects.list()[0]


# --- static -------------------------------------------------------------------------------


def _violations(source: str) -> set[str]:
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names} & FORBIDDEN_MODULES
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
            found.add(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES | FORBIDDEN_BUILTINS:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.add(node.attr)
    return found


@pytest.mark.parametrize("path", TASK_MODULES, ids=lambda p: p.name)
def test_task_sources_are_clean(path):
    assert _violations(path.read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "from socket import socket", "x = os.environ",
    "subprocess.Popen(['git'])", "registry.invoke('git.status', ctx, {})", "selection.generate(req)",
    "open('/etc/passwd')", "eval('1')", "compile('x', 'f', 'exec')",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert _violations(snippet)


# --- runtime: the lifecycle touches nothing outside the database ---------------------------


@pytest.fixture
def tripwires(monkeypatch):
    calls = []

    def trip(name):
        def f(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called during task lifecycle")
        return f

    monkeypatch.setattr(subprocess, "Popen", trip("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", trip("subprocess.run"))
    monkeypatch.setattr(os, "system", trip("os.system"))
    monkeypatch.setattr(os, "popen", trip("os.popen"))
    monkeypatch.setattr(os, "scandir", trip("os.scandir"))
    monkeypatch.setattr(os, "listdir", trip("os.listdir"))
    monkeypatch.setattr(socket.socket, "connect", trip("socket.connect"))
    monkeypatch.setattr(client_mod.GitClient, "run", trip("GitClient.run"))
    monkeypatch.setattr(ToolRegistry, "invoke", trip("ToolRegistry.invoke"))
    monkeypatch.setattr(ModelRouter, "select", trip("ModelRouter.select"))
    monkeypatch.setattr(Selection, "generate", trip("Selection.generate"))
    monkeypatch.setattr(ModelProvider, "generate", trip("ModelProvider.generate"))
    return calls


def test_full_lifecycle_calls_nothing_outside_the_database(engine, project, tripwires):
    t = engine.create(request(project)).task
    engine.start(t.task_id)
    engine.wait(t.task_id, reason="needs input")
    engine.requeue(t.task_id)
    engine.start(t.task_id)
    engine.fail(t.task_id, TaskFailure("MODEL_UNAVAILABLE", "provider down", retryable=True))
    engine.retry(t.task_id)
    engine.start(t.task_id)
    engine.complete(t.task_id, TaskResult("Done", details={"model": "echo"}))
    u = engine.create(request(project, description="other")).task
    engine.cancel(u.task_id, reason="not needed")
    engine.list()
    engine.history(t.task_id)
    engine.recovery()
    assert tripwires == []


# --- hostile input ------------------------------------------------------------------------


@pytest.mark.parametrize("over", [
    {"project_id": "prj_x\n"},  # trailing newline (Phase 09 finding)
    {"project_id": "prj_x\r"},
    {"project_id": "../prj_x"},
    {"project_id": "/work/toko"},  # a path is not a project
    {"project_id": "prj_x; rm -rf /"},
    {"task_id": "tsk_x\n"},
    {"task_id": "1"},
    {"task_id": "prj_01ABC"},
    {"parent_task_id": "tsk_../../etc"},
    {"priority": "critical"},
    {"source": "shell"},
    {"max_retries": -1},
    {"max_retries": 11},
    {"max_retries": True},
    {"description": "   "},
    {"description": "x\x00y"},
    {"metadata": {"x": "y" * 20_000}},  # oversized
    {"metadata": {"api_key": "sk-123"}},
    {"metadata": {"password": "hunter2"}},
    {"metadata": {"token": "t"}},
    {"metadata": {"nested": {"private_key": "k"}}},
    {"metadata": {"command": "make deploy"}},
    {"metadata": {"steps": [{"shell": "bash -c id"}]}},
    {"metadata": {"argv": ["git", "push"]}},
    {"metadata": {"git_args": ["push", "--force"]}},
    {"metadata": {"cwd": "/etc"}},
    {"metadata": {"root_path": "/"}},
    {"metadata": {"n": float("nan")}},
    {"metadata": ["not", "an", "object"]},
])
def test_create_refuses_hostile_input(engine, project, db, over):
    with pytest.raises(InvalidTaskError):
        engine.create(request(project, **over))
    assert db.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["shell", "argv", "command", "cwd", "root_path", "git_args", "python", "callable",
                                   "repository_path"])
def test_request_refuses_execution_fields(project, field):
    data = {"project_id": project.project_id, "description": "x", "source": "cli", field: "anything"}
    with pytest.raises(InvalidTaskError) as info:
        TaskRequest.from_dict(data)
    assert info.value.field == field


def test_unknown_and_archived_project_store_nothing(engine, project, db):
    with pytest.raises(TaskProjectNotFoundError):
        engine.create(request(project, project_id="prj_01UNKNOWN"))
    engine.projects.archive(project.project_id)
    with pytest.raises(TaskProjectArchivedError):
        engine.create(request(project))
    assert engine.projects.get(project.project_id).is_active is False  # never auto-restored
    assert db.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_failure_never_stores_secrets_or_tracebacks(engine, project, db):
    t = engine.create(request(project)).task
    try:
        raise RuntimeError(f"token=ghp_SECRET-31337 HOME={os.environ.get('HOME', '/home/x')}")
    except RuntimeError as exc:
        engine.fail(t.task_id, exc)
    raw = db.path.read_bytes()
    assert b"ghp_SECRET-31337" not in raw and b"Traceback" not in raw and b"HOME=" not in raw


# --- the database refuses bad rows too (defence in depth) ----------------------------------


def _raw_insert(db, **over):
    row = dict(task_id="tsk_01RAW", request_id="req_01RAW", project_id="prj_01RAW", parent_task_id=None,
               title="t", description="d", status="queued", priority="normal", source="test", retry_count=0,
               max_retries=3, metadata="{}", last_error=None, result=None, created_at="2026-10-05T00:00:00.000Z",
               updated_at="2026-10-05T00:00:00.000Z", started_at=None, completed_at=None, failed_at=None,
               cancelled_at=None)
    row.update(over)
    db.conn.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


@pytest.mark.parametrize("over", [
    {"task_id": "tsk_x\n"},
    {"task_id": "abc"},
    {"project_id": "prj_x\n"},
    {"project_id": "/work/toko"},
    {"status": "accepted"},
    {"status": "executing"},
    {"priority": "critical"},
    {"source": "shell"},
    {"retry_count": -1},
    {"retry_count": 4, "max_retries": 3},
    {"max_retries": 11},
    {"metadata": "[1]"},
    {"metadata": "{nope"},
    {"created_at": "yesterday"},
    {"updated_at": "2026-10-04T00:00:00.000Z"},  # before created_at
    {"status": "completed"},  # no started_at/completed_at
])
def test_schema_rejects_bad_rows(db, over):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(db, **over)


def test_schema_accepts_the_reference_row(db):
    _raw_insert(db)
    assert db.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
