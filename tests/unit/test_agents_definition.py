"""AgentDefinition, AgentRequest, AgentResult: immutable, validated data."""

import dataclasses

import pytest

from siamang.agents import AgentCapability, AgentDefinition, AgentRequest, AgentResult, AgentStatus
from siamang.agents.definition import AGENT_ID_RE
from siamang.agents.errors import (
    ERROR_TYPES,
    AgentDefinitionError,
    AgentErrorCode,
    AgentExecutionError,
    AgentInputError,
    AgentResultError,
)
from siamang.agents.reference import ECHO
from siamang.contracts.schema import F
from siamang.models import Capability
from siamang.tools import Permission
from tests.support.agents import definition

TSK = "tsk_01ABC"
PRJ = "prj_01ABC"


# --- definition -------------------------------------------------------------------


def test_valid_definition_and_public_dict():
    d = definition(model_requirements={"text_generation", "reasoning"},
                   tool_permissions={"tool.invoke", "project.read", "git.read"}, metadata={"team": "core"})
    assert d.model_requirements == {Capability.TEXT_GENERATION, Capability.REASONING}
    assert d.tool_permissions == {Permission.TOOL_INVOKE, Permission.PROJECT_READ, Permission.GIT_READ}
    assert d.uses_models and d.uses_tools
    out = d.to_dict()
    assert "implementation" not in out
    assert out["capabilities"] == ["structured_analysis"]
    assert out["input_schema"] == {"message": {"kind": "text", "required": True}}


def test_definition_is_immutable():
    d = definition(metadata={"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.agent_id = "agent.other"
    with pytest.raises(TypeError):
        d.metadata["a"] = 2
    with pytest.raises(TypeError):
        d.input_schema["x"] = F("str")
    assert isinstance(d.capabilities, frozenset)


@pytest.mark.parametrize("agent_id", ["echo", "agent", "agent.", "Agent.echo", "agent.Echo", "agent.echo\n",
                                      "agent..echo", "agent.echo.", "tool.echo", "agent.e-cho", "agent.1echo",
                                      "agent.a.b.c.d", 42, None])
def test_invalid_ids(agent_id):
    with pytest.raises(AgentDefinitionError):
        definition(agent_id=agent_id, role="echo")


@pytest.mark.parametrize("segment", ["run", "exec", "shell", "eval", "subprocess", "autonomous", "sudo"])
def test_ids_naming_raw_execution_or_autonomy(segment):
    with pytest.raises(AgentDefinitionError):
        definition(agent_id=f"agent.{segment}", role=segment)
    with pytest.raises(AgentDefinitionError):
        definition(agent_id=f"agent.probe.{segment}")


def test_id_regex_is_strict_at_end():
    assert AGENT_ID_RE.fullmatch("agent.probe.python")
    assert not AGENT_ID_RE.match("agent.probe\n")


def test_role_must_match_namespace():
    with pytest.raises(AgentDefinitionError):
        definition(role="programmer")
    with pytest.raises(AgentDefinitionError):
        definition(role="Probe")


@pytest.mark.parametrize("over", [
    {"capabilities": frozenset()},
    {"capabilities": {"mind_reading"}},
    {"capabilities": "reasoning"},
    {"model_requirements": {"telepathy"}},
    {"model_requirements": {"reasoning"}},  # text_generation is always needed
    {"tool_permissions": {"tool.root"}},
    {"tool_permissions": {"project.read"}},  # tool.invoke missing
    {"tool_permissions": {"tool.invoke", "git.write"}},
    {"tool_permissions": {"tool.invoke", "process.execute"}},
    {"tool_permissions": {"tool.invoke", "network"}},
    {"tool_permissions": {"tool.invoke", "filesystem.write"}},
    {"tool_permissions": {"tool.invoke", "workspace.write"}},
    {"name": ""},
    {"name": "x" * 81},
    {"description": " "},
    {"version": "v1"},
    {"version": "1.2.3.4"},
    {"implementation": lambda request, context: None},
    {"implementation": "Not An ID"},
    {"enabled": "yes"},
    {"input_schema": {"command": F("str")}},
    {"input_schema": {"argv": F("list", item=F("str"))}},
    {"input_schema": {"cwd": F("str")}},
    {"input_schema": {"root_path": F("str")}},
    {"input_schema": {"message": "str"}},
    {"input_schema": {"message": F("nonsense")}},
    {"output_schema": {}},
    {"metadata": {"api_key": "sk-1"}},
    {"metadata": {"command": "x"}},
    {"metadata": {"x": "y" * 20_000}},
    {"metadata": {"n": float("nan")}},
    {"metadata": ["not", "an", "object"]},
])
def test_invalid_definitions(over):
    with pytest.raises(AgentDefinitionError):
        definition(**over)


def test_capability_is_not_permission():
    """Declaring every capability grants no tool permission and no model capability."""
    d = definition(capabilities=set(AgentCapability))
    assert d.tool_permissions == frozenset() and d.model_requirements == frozenset()
    assert not d.uses_tools and not d.uses_models
    assert not {c.value for c in AgentCapability} & {p.value for p in Permission}


def test_reference_definition():
    assert ECHO.agent_id == "agent.echo" and ECHO.version == "1"
    assert not ECHO.uses_models and not ECHO.uses_tools
    assert ECHO.metadata["reference"] is True


# --- request ----------------------------------------------------------------------


def test_valid_request_is_immutable_and_normalized():
    r = AgentRequest(TSK, PRJ, "  Ulangi pesan  ", {"message": "halo", "n": [1, 2]}, ["  be brief "], {"k": "v"})
    assert r.instruction == "Ulangi pesan" and r.constraints == ("be brief",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.task_id = "tsk_other"
    with pytest.raises(TypeError):
        r.input["message"] = "x"
    assert r.to_dict()["input"] == {"message": "halo", "n": [1, 2]}


@pytest.mark.parametrize("over", [
    {"task_id": None}, {"task_id": ""}, {"task_id": "tsk_x\n"}, {"task_id": "prj_01ABC"}, {"task_id": 1},
    {"project_id": None}, {"project_id": "prj_x\n"}, {"project_id": "/work/toko"}, {"project_id": "tsk_01ABC"},
    {"instruction": ""}, {"instruction": "   "}, {"instruction": "x\x00y"}, {"instruction": "x" * 10_001},
    {"instruction": None},
    {"input": ["not", "object"]}, {"input": {"command": "rm -rf /"}}, {"input": {"argv": ["git", "push"]}},
    {"input": {"nested": {"shell": "bash"}}}, {"input": {"cwd": "/etc"}}, {"input": {"root_path": "/"}},
    {"input": {"git_args": ["push"]}}, {"input": {"python": "import os"}}, {"input": {"callable": "x"}},
    {"input": {"api_key": "sk-1"}}, {"input": {"x": float("inf")}},
    {"constraints": "be brief"}, {"constraints": [""]}, {"constraints": ["x" * 501]}, {"constraints": ["a"] * 21},
    {"constraints": ["a\nb"]},
    {"metadata": {"password": "x"}},
])
def test_invalid_requests(over):
    data = dict(task_id=TSK, project_id=PRJ, instruction="Ulangi", input={"message": "halo"})
    data.update(over)
    with pytest.raises(AgentInputError):
        AgentRequest(**data)


def test_request_from_dict_is_strict():
    base = {"task_id": TSK, "project_id": PRJ, "instruction": "x"}
    assert AgentRequest.from_dict({**base, "constraints": ["a"]}).constraints == ("a",)
    for missing in ("task_id", "project_id", "instruction"):
        with pytest.raises(AgentInputError) as info:
            AgentRequest.from_dict({k: v for k, v in base.items() if k != missing})
        assert info.value.details == (missing,)
    with pytest.raises(AgentInputError):
        AgentRequest.from_dict([base])
    with pytest.raises(AgentInputError):
        AgentRequest.from_dict({**base, "surprise": 1})


@pytest.mark.parametrize("field", ["shell_command", "shell", "argv", "cwd", "root_path", "git_args", "python",
                                   "callable", "command", "repository_path", "executable"])
def test_request_refuses_execution_fields(field):
    with pytest.raises(AgentInputError) as info:
        AgentRequest.from_dict({"task_id": TSK, "project_id": PRJ, "instruction": "x", field: "anything"})
    assert "execution-style" in info.value.message and info.value.details == (field,)


# --- result -----------------------------------------------------------------------


def test_result_statuses():
    ok = AgentResult.completed("agent.echo", TSK, {"message": "halo"})
    assert ok.success and ok.status is AgentStatus.COMPLETED and ok.error is None
    wait = AgentResult.waiting("agent.echo", TSK, {"question": "which table?"})
    assert not wait.success and wait.status is AgentStatus.WAITING
    err = AgentExecutionError("boom", agent_id="agent.echo", cause="RuntimeError")
    bad = AgentResult.failed("agent.echo", TSK, err)
    assert not bad.success and bad.to_dict()["error"]["code"] == "agent_execution_failed"
    unidentified = AgentResult.failed(None, None, AgentInputError("bad"))
    assert unidentified.agent_id is None and unidentified.task_id is None


def test_agent_status_does_not_extend_task_lifecycle():
    from siamang.tasks import TaskStatus

    assert {s.value for s in AgentStatus} <= {s.value for s in TaskStatus}


@pytest.mark.parametrize("args", [
    ("agent.echo", TSK, "done"),  # unknown status
    ("agent.echo", TSK, "completed", {"x": object()}),
    ("agent.echo", TSK, "completed", {"x": float("nan")}),
    ("agent.echo", TSK, "completed", ["list"]),
    ("agent.echo", TSK, "completed", {"x": "y" * 300_000}),
    ("agent.echo", TSK, "completed", {}, AgentInputError("x")),  # completed with error
    ("agent.echo", TSK, "failed"),  # failed without error
    ("agent.echo", TSK, "failed", {}, ValueError("raw exception")),
    ("agent.echo", None, "completed"),
    ("agent.echo", "tsk_x\n", "completed"),
    (None, TSK, "completed"),
    ("not-an-agent", TSK, "completed"),
])
def test_invalid_results(args):
    with pytest.raises(AgentResultError):
        AgentResult(*args)


def test_result_metadata_errors_are_result_errors():
    with pytest.raises(AgentResultError):
        AgentResult.completed("agent.echo", TSK, {}, metadata={"token": "t"})


def test_deeply_nested_output_is_refused():
    deep = {"a": "leaf"}
    for _ in range(9):
        deep = {"a": deep}
    with pytest.raises(AgentResultError):
        AgentResult.completed("agent.echo", TSK, deep)


def test_result_output_is_read_only():
    r = AgentResult.completed("agent.echo", TSK, {"message": "halo"})
    with pytest.raises(TypeError):
        r.output["message"] = "x"


# --- errors -----------------------------------------------------------------------


def test_error_codes_cover_the_brief():
    brief = {"agent_not_found", "agent_disabled", "agent_input_invalid", "agent_context_invalid",
             "agent_permission_denied", "agent_model_unavailable", "agent_tool_unavailable",
             "agent_execution_failed", "agent_result_invalid"}
    assert brief <= {c.value for c in AgentErrorCode}
    assert set(ERROR_TYPES) == set(AgentErrorCode)


def test_error_messages_are_redacted():
    err = AgentExecutionError("failed with token=ghp_SECRET-1234567890abcdef", details=("password=hunter2",))
    d = err.to_dict()
    assert "ghp_SECRET" not in d["message"] and "hunter2" not in d["details"][0]
    assert str(err).startswith("agent_execution_failed: ")
