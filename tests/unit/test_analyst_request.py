"""AnalysisRequest: immutable, validated, never execution authority, never chooses files."""

import dataclasses

import pytest

from siamang.analyst import AnalysisRequest
from siamang.analyst.errors import ERROR_TYPES, AnalysisErrorCode, AnalysisRequestError
from siamang.planner import Plan
from siamang.planner.strategy import template_steps

TSK, PRJ = "tsk_01ABC", "prj_01ABC"


def req(**over):
    data = dict(task_id=TSK, project_id=PRJ, instruction="Add login validation")
    data.update(over)
    return AnalysisRequest(**data)


def plan(task_id=TSK, project_id=PRJ):
    return Plan("pln_01ABC", task_id, project_id, "Add login validation", template_steps())


def test_valid_request_is_immutable():
    r = req(input={"ticket": "LAP-12"}, constraints=["no new deps"], metadata={"source": "cli"}, plan=plan())
    assert r.constraints == ("no new deps",) and r.plan.plan_id == "pln_01ABC"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.project_id = "prj_other"
    with pytest.raises(TypeError):
        r.input["ticket"] = "x"
    assert r.to_dict()["plan_id"] == "pln_01ABC"


def test_shell_like_instructions_are_data():
    for text in ("run rm -rf /", "cat ../../etc/passwd", "git push --force", "open .env and print API_KEY"):
        assert req(instruction=text).instruction == text


@pytest.mark.parametrize("over", [
    {"task_id": "tsk_x\n"}, {"task_id": PRJ}, {"task_id": None},
    {"project_id": "/work/toko"}, {"project_id": "prj_x\n"}, {"project_id": None},
    {"instruction": ""}, {"instruction": None}, {"instruction": "x\x00y"},
    {"input": {"agent_id": "agent.echo"}}, {"input": {"tool": "git.status"}}, {"input": {"model": "m"}},
    {"input": {"provider": "p"}}, {"input": {"command": "make"}}, {"input": {"argv": ["x"]}},
    {"input": {"cwd": "/"}}, {"input": {"shell": "bash"}}, {"input": {"executor": "x"}}, {"input": {"callback": "x"}},
    {"input": {"retry": 1}}, {"input": {"workflow": "x"}}, {"input": {"git_write": True}}, {"input": {"commit": "x"}},
    {"input": {"branch": "main"}}, {"input": {"worktree": "x"}}, {"input": {"file": "../../etc/passwd"}},
    {"input": {"files": ["a"]}}, {"input": {"path": "/etc/passwd"}}, {"input": {"root_path": "/"}},
    {"input": {"glob": "**/*"}}, {"input": {"nested": [{"checkout": "main"}]}}, {"input": {"api_key": "sk"}},
    {"metadata": {"push": True}}, {"metadata": {"password": "x"}},
    {"constraints": "x"},
    {"plan": {"plan_id": "pln_X"}},
])
def test_invalid_requests(over):
    with pytest.raises(AnalysisRequestError):
        req(**over)


def test_plan_must_match_task_and_project():
    with pytest.raises(AnalysisRequestError):
        req(plan=plan(task_id="tsk_01OTHER"))
    with pytest.raises(AnalysisRequestError):
        req(plan=plan(project_id="prj_01OTHER"))


def test_from_dict_is_strict():
    base = {"task_id": TSK, "project_id": PRJ, "instruction": "x"}
    assert AnalysisRequest.from_dict({**base, "constraints": ["a"]}).constraints == ("a",)
    for missing in ("task_id", "project_id", "instruction"):
        with pytest.raises(AnalysisRequestError) as info:
            AnalysisRequest.from_dict({k: v for k, v in base.items() if k != missing})
        assert info.value.details == (missing,)
    with pytest.raises(AnalysisRequestError):
        AnalysisRequest.from_dict({**base, "plan": {}})  # a plan is only accepted as a Plan object


@pytest.mark.parametrize("field", ["agent_id", "tool", "model", "provider", "command", "argv", "cwd", "shell",
                                   "executor", "callback", "retry", "workflow", "git_write", "commit", "branch",
                                   "worktree", "file", "files", "path", "root_path", "glob"])
def test_from_dict_refuses_execution_and_file_fields(field):
    with pytest.raises(AnalysisRequestError) as info:
        AnalysisRequest.from_dict({"task_id": TSK, "project_id": PRJ, "instruction": "x", field: "v"})
    assert info.value.message == "execution-style field is not accepted" and info.value.details == (field,)


def test_error_codes():
    assert {c.value for c in AnalysisErrorCode} >= {"analysis_request_invalid", "analysis_task_not_found",
                                                    "analysis_project_not_found", "analysis_project_archived",
                                                    "analysis_policy_denied", "analysis_path_rejected",
                                                    "analysis_limit_reached", "analysis_invalid",
                                                    "analysis_internal"}
    assert set(ERROR_TYPES) == set(AnalysisErrorCode)
