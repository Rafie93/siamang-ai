"""Phase 27: the agent acts and observes inside the sandbox, within limits, and 'verified' needs a run after the last edit."""
import json

import pytest

from siamang.executor import ExecutionStatus, ToolExecutor, ToolPolicy, ToolWorkspace
from siamang.sandbox import RawRun, SandboxPolicy, SandboxService


class Runtime:
    def __init__(self, *runs, up=True):
        self.runs, self.up, self.seen = list(runs), up, []

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.seen.append((argv, sorted(p.name for p in work.rglob("*") if p.is_file()), (work / "app.py").read_text()))
        return self.runs.pop(0)


def agent(*calls):
    replies = [c if isinstance(c, str) else json.dumps(c) for c in calls]
    prompts = []

    def ask(system, transcript):
        prompts.append(transcript)
        return replies.pop(0)
    ask.prompts = prompts
    return ask


@pytest.fixture
def root(tmp_path):
    (tmp_path / "app.py").write_text("def f(): return 1\n")
    (tmp_path / ".env").write_text("TOKEN=abc")
    (tmp_path / "link.py").symlink_to(tmp_path / "app.py")
    return tmp_path


class Bound:
    def __init__(self, tool, ask):
        self._tool, self._ask = tool, ask

    def run(self, root, task):
        return self._tool.run(root, task, self._ask)


def executor(runtime, ask, **policy):
    return Bound(ToolExecutor(SandboxService(runtime, SandboxPolicy("img")), ToolPolicy(**policy)), ask)


def test_the_agent_sees_its_own_failure_fixes_it_and_is_verified(root):
    runtime = Runtime(RawRun(1, "AssertionError"), RawRun(0, "1 passed"))
    ask = agent({"tool": "read_file", "path": "app.py"}, {"tool": "run", "argv": ["pytest"]},
                {"tool": "write_file", "path": "app.py", "content": "def f(): return 2\n"},
                {"tool": "run", "argv": ["pytest"]}, {"tool": "finish", "summary": "fixed"})
    result = executor(runtime, ask).run(root, "make f return 2")
    assert result.finished and result.verified and result.runs == 2
    assert "AssertionError" in ask.prompts[2]  # the failure was observed before the fix
    assert runtime.seen[1][2] == "def f(): return 2\n"  # the second run saw the edit
    assert (root / "app.py").read_text() == "def f(): return 1\n"  # the project is not written
    assert [(c.path, c.operation) for c in result.changes] == [("app.py", "modify")]


def test_finishing_without_a_run_after_the_last_edit_is_not_verified(root):
    runtime = Runtime(RawRun(0, "ok"))
    ask = agent({"tool": "run", "argv": ["pytest"]}, {"tool": "write_file", "path": "new.py", "content": "x = 1\n"},
                {"tool": "finish", "summary": "done"})
    result = executor(runtime, ask).run(root, "t")
    assert result.finished and not result.verified


def test_secret_files_symlinks_and_credentials_are_unreachable(root):
    ask = agent({"tool": "read_file", "path": ".env"}, {"tool": "read_file", "path": "link.py"},
                {"tool": "write_file", "path": "k.py", "content": "KEY = 'sk-abcdefghijklmnopqrstuv'"},
                {"tool": "list_files"}, {"tool": "finish", "summary": ""})
    executor(Runtime(), ask, max_invalid=5).run(root, "t")
    listing = ask.prompts[-1]
    assert "abc" not in listing and "link.py" not in listing.split("OBSERVATION 4:")[-1]
    assert listing.count("error:") == 3


def test_bad_calls_are_answered_and_too_many_end_the_run(root):
    result = executor(Runtime(), agent("not json", {"tool": "rm"}, {"tool": "run", "argv": "rm -rf /"}),
                      max_invalid=3).run(root, "t")
    assert result.status is ExecutionStatus.INVALID and not result.verified


def test_the_turn_limit_ends_a_run_that_never_finishes(root):
    ask = agent(*[{"tool": "list_files"}] * 3)
    result = executor(Runtime(), ask, max_turns=3).run(root, "t")
    assert result.status is ExecutionStatus.TURN_LIMIT and result.turns == 3


def test_the_run_limit_is_enforced(root):
    runtime = Runtime(RawRun(0, "ok"))
    ask = agent({"tool": "run", "argv": ["a"]}, {"tool": "run", "argv": ["b"]}, {"tool": "finish", "summary": ""})
    result = executor(runtime, ask, max_runs=1).run(root, "t")
    assert result.runs == 1 and len(runtime.seen) == 1


def test_no_engine_means_nothing_ran_and_it_says_so(root):
    result = executor(Runtime(up=False), agent({"tool": "run", "argv": ["pytest"]})).run(root, "t")
    assert result.status is ExecutionStatus.UNAVAILABLE and not result.verified


def test_a_model_failure_is_reported_not_raised(root):
    def broken(system, transcript):
        raise RuntimeError("key sk-secret")
    result = executor(Runtime(), broken).run(root, "t")
    assert result.status is ExecutionStatus.MODEL_ERROR and "sk-secret" not in json.dumps(result.to_dict())


def test_workspace_overlay_create_delete_and_change_cap(root):
    work = ToolWorkspace(root, max_changes=2)
    assert work.write("a.py", "1") == "created" and work.write("app.py", "2") == "modified"
    with pytest.raises(Exception, match="at most 2"):
        work.write("b.py", "3")
    assert work.delete("a.py") == "deleted" and "a.py" not in work.list()
    assert work.delete("app.py") == "deleted" and [c.operation for c in work.changes] == ["delete"]
    with pytest.raises(Exception):
        work.read("../etc/passwd")


def test_a_step_a_reason_and_owner_questions_travel_with_the_result(root):
    ask = agent({"tool": "write_file", "path": "n.py", "content": "x = 1\n", "step_id": "step-1", "reason": "adds n"},
                {"tool": "finish", "summary": "stuck", "questions": ["Which database?", "", 7]})
    result = executor(Runtime(), ask).run(root, "t")
    assert result.questions == ("Which database?",)
    assert (result.changes[0].step_id, result.changes[0].reason) == ("step-1", "adds n")
