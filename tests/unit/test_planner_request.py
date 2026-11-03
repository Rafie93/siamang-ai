"""PlanningRequest: immutable, validated planning input; never an execution envelope."""

import dataclasses

import pytest

from siamang.planner import PlanningRequest
from siamang.planner.errors import ERROR_TYPES, PlanningErrorCode, PlanningRequestError

TSK, PRJ = "tsk_01ABC", "prj_01ABC"


def req(**over):
    data = dict(task_id=TSK, project_id=PRJ, instruction="Add login validation")
    data.update(over)
    return PlanningRequest(**data)


def test_valid_request_is_normalized_and_immutable():
    r = req(instruction="  Add login validation\n  with tests ", input={"ticket": "LAP-12"},
            constraints=["  no new dependencies "], metadata={"source": "cli"})
    assert r.instruction == "Add login validation\n  with tests"
    assert r.constraints == ("no new dependencies",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.task_id = "tsk_other"
    with pytest.raises(TypeError):
        r.input["ticket"] = "x"
    assert r.to_dict()["input"] == {"ticket": "LAP-12"}


def test_instruction_is_opaque_text():
    """Dangerous-looking text is planning input, accepted and never interpreted."""
    for text in ("run rm -rf / now", "open /etc/passwd", "execute shell", "call git push --force",
                 "import os; os.system('id')"):
        assert req(instruction=text).instruction == text


@pytest.mark.parametrize("over", [
    {"task_id": None}, {"task_id": "tsk_x\n"}, {"task_id": PRJ}, {"task_id": 7},
    {"project_id": None}, {"project_id": "prj_x\n"}, {"project_id": "/work/toko"}, {"project_id": TSK},
    {"instruction": None}, {"instruction": ""}, {"instruction": "  "}, {"instruction": "x\x00"},
    {"instruction": "x" * 10_001}, {"instruction": b"bytes"},
    {"input": ["x"]}, {"input": {"command": "make"}}, {"input": {"argv": ["git"]}}, {"input": {"cwd": "/"}},
    {"input": {"path": "/etc"}}, {"input": {"shell": "bash"}}, {"input": {"agent_id": "agent.echo"}},
    {"input": {"tool": "git.status"}}, {"input": {"model": "gpt"}}, {"input": {"provider": "openai"}},
    {"input": {"retry": 3}}, {"input": {"workflow": ["a", "b"]}}, {"input": {"callback": "x"}},
    {"input": {"executor": "x"}}, {"input": {"nested": [{"git": "push"}]}}, {"input": {"module": "os"}},
    {"input": {"api_key": "sk-1"}}, {"input": {"x": float("nan")}}, {"input": {"x": "y" * 20_000}},
    {"metadata": {"token": "t"}}, {"metadata": {"orchestrator": "x"}}, {"metadata": "x"},
    {"constraints": "be brief"}, {"constraints": [""]}, {"constraints": ["a\nb"]}, {"constraints": ["x"] * 21},
])
def test_invalid_requests(over):
    with pytest.raises(PlanningRequestError):
        req(**over)


def test_errors_name_the_field_not_the_value():
    with pytest.raises(PlanningRequestError) as info:
        req(input={"api_key": "sk-SECRET-42"})
    assert "sk-SECRET-42" not in str(info.value.to_dict()) and info.value.details == ("input",)


def test_from_dict_is_strict():
    base = {"task_id": TSK, "project_id": PRJ, "instruction": "x", "constraints": ["a"]}
    assert PlanningRequest.from_dict(base).constraints == ("a",)
    for missing in ("task_id", "project_id", "instruction"):
        with pytest.raises(PlanningRequestError) as info:
            PlanningRequest.from_dict({k: v for k, v in base.items() if k != missing})
        assert info.value.details == (missing,)
    with pytest.raises(PlanningRequestError):
        PlanningRequest.from_dict([base])
    with pytest.raises(PlanningRequestError) as info:
        PlanningRequest.from_dict({**base, "surprise": 1})
    assert info.value.message == "unknown request field"


@pytest.mark.parametrize("field", ["agent_id", "command", "argv", "cwd", "path", "shell", "tool", "model",
                                   "provider", "retry", "workflow", "callback", "executor", "git", "module",
                                   "steps", "plan", "root_path", "python", "callable"])
def test_from_dict_refuses_execution_fields(field):
    with pytest.raises(PlanningRequestError) as info:
        PlanningRequest.from_dict({"task_id": TSK, "project_id": PRJ, "instruction": "x", field: "v"})
    assert info.value.message == "execution-style field is not accepted" and info.value.details == (field,)


def test_error_codes_cover_the_brief():
    brief = {"planning_request_invalid", "planning_task_not_found", "planning_project_not_found",
             "planning_project_archived", "planning_policy_denied", "planning_invalid_plan",
             "planning_dependency_cycle", "planning_too_many_steps", "planning_internal"}
    assert {c.value for c in PlanningErrorCode} == brief
    assert set(ERROR_TYPES) == set(PlanningErrorCode)
