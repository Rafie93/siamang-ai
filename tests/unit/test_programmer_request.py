"""ProgrammingRequest: data only; Plan and SystemAnalysis as exact snapshots; no execution or mutation keys."""

import dataclasses

import pytest

from siamang.analyst import SystemAnalysis
from siamang.planner import Plan
from siamang.programmer import ProgrammingRequest
from siamang.programmer.errors import ProgrammingErrorCode, ProgrammingRequestError
from siamang.programmer.request import FORBIDDEN_FIELDS, PROGRAMMER_KEYS
from tests.support.programmer import INSTRUCTION, make_setup


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


def test_valid_request(setup):
    r = setup.request(input={"ticket": "AUTH-1"}, constraints=["Keep the public API"], metadata={"source": "test"})
    assert r.instruction == INSTRUCTION and r.plan is setup.plan and r.analysis is setup.analysis
    assert r.to_dict()["plan_id"] == setup.plan.plan_id and r.to_dict()["analysis_id"] == setup.analysis.analysis_id


def test_request_is_immutable(setup):
    r = setup.request(input={"nested": {"a": [1]}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.instruction = "x"
    with pytest.raises(TypeError):
        r.input["new"] = 1
    with pytest.raises(TypeError):
        r.input["nested"]["a"] = 2
    assert isinstance(r.constraints, tuple)


@pytest.mark.parametrize("key", sorted({"command", "argv", "shell", "cwd", "executor", "callback", "process",
                                        "subprocess", "tool", "tool_id", "agent", "agent_id", "orchestrator",
                                        "approval", "apply", "commit", "branch", "worktree", "git_write",
                                        "filesystem", "write_file", "delete_file", "rename_file"}))
@pytest.mark.parametrize("where", ["input", "metadata"])
def test_forbidden_keys_refused_at_any_depth(setup, key, where):
    for value in ({key: "x"}, {"outer": {key: "x"}}, {"list": [{"deep": [{key: 1}]}]}, {"a": {"b": {key.upper(): 1}}}):
        with pytest.raises(ProgrammingRequestError) as info:
            setup.request(**{where: value})
        assert info.value.code is ProgrammingErrorCode.REQUEST_INVALID


def test_required_forbidden_vocabulary_is_covered():
    required = {"command", "argv", "shell", "cwd", "executor", "callback", "process", "subprocess", "tool",
                "tool_id", "agent", "agent_id", "orchestrator", "approval", "apply", "commit", "branch", "worktree",
                "git_write", "filesystem", "write_file", "delete_file", "rename_file"}
    assert required <= PROGRAMMER_KEYS and required <= FORBIDDEN_FIELDS


@pytest.mark.parametrize("field", ["command", "shell", "apply", "approval", "write_file", "tool_id", "agent_id",
                                   "executor", "model_id"])
def test_from_dict_refuses_execution_fields(setup, field):
    data = {"task_id": setup.world.task.task_id, "project_id": setup.world.project.project_id,
            "instruction": INSTRUCTION, field: "x"}
    with pytest.raises(ProgrammingRequestError) as info:
        ProgrammingRequest.from_dict(data, plan=setup.plan, analysis=setup.analysis)
    assert info.value.message == "execution-style field is not accepted" and info.value.details == (field,)


def test_from_dict_strict(setup):
    ids = {"task_id": setup.world.task.task_id, "project_id": setup.world.project.project_id}
    r = ProgrammingRequest.from_dict({**ids, "instruction": INSTRUCTION, "constraints": ["a"]},
                                     plan=setup.plan, analysis=setup.analysis)
    assert r.constraints == ("a",)
    with pytest.raises(ProgrammingRequestError, match="unknown request field"):
        ProgrammingRequest.from_dict({**ids, "instruction": "x", "colour": 1}, plan=setup.plan, analysis=setup.analysis)
    with pytest.raises(ProgrammingRequestError):  # a plan is never parsed from data
        ProgrammingRequest.from_dict({**ids, "instruction": "x", "plan": {}}, plan=setup.plan, analysis=setup.analysis)
    with pytest.raises(ProgrammingRequestError, match="missing"):
        ProgrammingRequest.from_dict(ids, plan=setup.plan, analysis=setup.analysis)
    with pytest.raises(ProgrammingRequestError):
        ProgrammingRequest.from_dict(["x"], plan=setup.plan, analysis=setup.analysis)


def test_plan_and_analysis_must_be_exact_snapshots(setup):
    class LivePlan(Plan):
        def run(self):  # a "plan" that could do things
            return 1

    class LiveAnalysis(SystemAnalysis):
        pass

    live_plan = LivePlan(**{f.name: getattr(setup.plan, f.name) for f in dataclasses.fields(Plan)})
    live_analysis = LiveAnalysis(**{f.name: getattr(setup.analysis, f.name) for f in dataclasses.fields(SystemAnalysis)})
    for over in ({"plan": live_plan}, {"plan": setup.plan.to_dict()}, {"plan": None},
                 {"analysis": live_analysis}, {"analysis": setup.analysis.to_dict()}, {"analysis": object()}):
        with pytest.raises(ProgrammingRequestError) as info:
            setup.request(**over)
        assert info.value.details in (("plan",), ("analysis",))


@pytest.mark.parametrize("over", [{"task_id": "tsk_../x"}, {"task_id": ""}, {"project_id": "prj_ x"},
                                  {"project_id": None}, {"instruction": ""}, {"instruction": "a\x00b"},
                                  {"instruction": 5}, {"constraints": "one"}, {"input": []}, {"metadata": "x"},
                                  {"input": {"path": "/etc/passwd"}}, {"input": {"private_key": "x"}}])
def test_invalid_fields(setup, over):
    with pytest.raises(ProgrammingRequestError):
        setup.request(**over)


def test_instruction_is_opaque_data(setup):
    r = setup.request(instruction="rm -rf / && cat ../../etc/passwd && git push --force")
    assert "rm -rf" in r.instruction  # kept as text to program around; nothing parses or runs it
