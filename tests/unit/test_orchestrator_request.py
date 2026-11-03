"""OrchestrationRequest, OrchestrationResult, OrchestrationPolicy, OrchestrationError: validated data."""

import dataclasses

import pytest

from siamang.agents import AgentRequest, AgentResult
from siamang.agents.errors import AgentExecutionError
from siamang.orchestrator import (
    DEFAULT_POLICY,
    OrchestrationErrorCode,
    OrchestrationPolicy,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationStatus,
)
from siamang.orchestrator.errors import (
    ERROR_TYPES,
    AgentInvocationError,
    OrchestrationRequestError,
    OrchestrationResultError,
    PolicyDeniedError,
    TaskNotRunningError,
)
from siamang.tasks import TaskStatus

TSK, PRJ, ORC = "tsk_01ABC", "prj_01ABC", "orc_01ABC"


def req(**over):
    data = dict(task_id=TSK, project_id=PRJ, agent_id="agent.echo", instruction="Ulangi", input={"message": "hello"})
    data.update(over)
    return OrchestrationRequest(**data)


# --- request ------------------------------------------------------------------------


def test_valid_request_is_immutable_and_normalized():
    r = req(instruction="  Ulangi  ", constraints=["  singkat "], metadata={"k": "v"})
    assert (r.instruction, r.constraints) == ("Ulangi", ("singkat",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.agent_id = "agent.other"
    with pytest.raises(TypeError):
        r.input["message"] = "x"
    assert r.to_dict()["input"] == {"message": "hello"}


def test_to_agent_request():
    a = req(constraints=("a",), metadata={"k": 1}).to_agent_request()
    assert type(a) is AgentRequest
    assert (a.task_id, a.project_id, a.instruction, dict(a.input), a.constraints) == (
        TSK, PRJ, "Ulangi", {"message": "hello"}, ("a",))


@pytest.mark.parametrize("over", [
    {"task_id": "tsk_x\n"}, {"task_id": "1"}, {"task_id": None}, {"task_id": PRJ},
    {"project_id": "prj_x\n"}, {"project_id": "/work/toko"}, {"project_id": None}, {"project_id": TSK},
    {"agent_id": "reference.echo"},  # an implementation ID, not an agent ID
    {"agent_id": "siamang.agents.reference.EchoAgent"},
    {"agent_id": "EchoAgent"},
    {"agent_id": "agent.echo\n"},
    {"agent_id": "agent.echo:run"},
    {"agent_id": lambda: None},
    {"agent_id": ["agent.echo", "agent.other"]},
    {"agent_id": None},
    {"instruction": ""}, {"instruction": None}, {"instruction": "x\x00"},
    {"input": ["x"]}, {"input": {"command": "rm"}}, {"input": {"argv": ["git", "push"]}},
    {"input": {"cwd": "/"}}, {"input": {"root_path": "/"}}, {"input": {"path": "/etc"}},
    {"input": {"shell": "bash"}}, {"input": {"python": "import os"}}, {"input": {"callable": "x"}},
    {"input": {"git": {"args": ["push"]}}}, {"input": {"git_command": "push"}}, {"input": {"module": "os"}},
    {"input": {"import": "os"}}, {"input": {"function": "system"}}, {"input": {"nested": [{"module": "os"}]}},
    {"input": {"api_key": "sk-1"}}, {"input": {"x": float("nan")}},
    {"metadata": {"password": "x"}}, {"metadata": {"git_command": "push"}}, {"metadata": ["x"]},
    {"constraints": "be brief"}, {"constraints": ["a\nb"]},
])
def test_invalid_requests(over):
    with pytest.raises(OrchestrationRequestError):
        req(**over)


def test_request_errors_keep_the_field_not_the_value():
    with pytest.raises(OrchestrationRequestError) as info:
        req(input={"api_key": "sk-SECRET-123"})
    assert "sk-SECRET-123" not in str(info.value.to_dict())


def test_from_dict_is_strict():
    base = {"task_id": TSK, "project_id": PRJ, "agent_id": "agent.echo", "instruction": "x",
            "input": {"message": "m"}, "constraints": ["a"]}
    assert OrchestrationRequest.from_dict(base).constraints == ("a",)
    for missing in ("task_id", "project_id", "agent_id", "instruction"):
        with pytest.raises(OrchestrationRequestError) as info:
            OrchestrationRequest.from_dict({k: v for k, v in base.items() if k != missing})
        assert info.value.details == (missing,)
    with pytest.raises(OrchestrationRequestError):
        OrchestrationRequest.from_dict([base])
    with pytest.raises(OrchestrationRequestError) as info:
        OrchestrationRequest.from_dict({**base, "surprise": 1})
    assert info.value.message == "unknown request field"


@pytest.mark.parametrize("field", ["shell", "command", "commands", "argv", "cwd", "root_path", "path", "git",
                                   "git_command", "python", "callable", "function", "module", "import", "agents",
                                   "agent_ids", "handler", "class_path", "tool_id", "model_id"])
def test_from_dict_refuses_execution_style_fields(field):
    base = {"task_id": TSK, "project_id": PRJ, "agent_id": "agent.echo", "instruction": "x"}
    with pytest.raises(OrchestrationRequestError) as info:
        OrchestrationRequest.from_dict({**base, field: "anything"})
    assert info.value.message == "execution-style field is not accepted" and info.value.details == (field,)


# --- result -------------------------------------------------------------------------


def test_result_mirrors_the_agent_result():
    ok = AgentResult.completed("agent.echo", TSK, {"message": "hello"})
    r = OrchestrationResult(ORC, TSK, PRJ, "agent.echo", "completed", ok)
    assert r.success and r.to_dict()["agent_result"]["output"] == {"message": "hello"}
    with pytest.raises(OrchestrationResultError):
        OrchestrationResult(ORC, TSK, PRJ, "agent.echo", "waiting", ok)
    with pytest.raises(OrchestrationResultError):
        OrchestrationResult(ORC, TSK, PRJ, "agent.echo", "completed", None)


def test_failed_result_needs_an_error():
    err = AgentInvocationError("agent invocation failed", cause="agent_execution_failed")
    r = OrchestrationResult(ORC, None, None, None, "failed", error=err)
    assert not r.success and r.to_dict()["error"]["cause"] == "agent_execution_failed"
    with pytest.raises(OrchestrationResultError):
        OrchestrationResult(ORC, TSK, PRJ, "agent.echo", "failed")
    with pytest.raises(OrchestrationResultError):
        OrchestrationResult(ORC, TSK, PRJ, "agent.echo", "failed", error=AgentExecutionError("raw agent error"))


@pytest.mark.parametrize("args", [
    ("tsk_01", TSK, PRJ, "agent.echo", "failed"),  # not an orc_ id
    (ORC, TSK, PRJ, "agent.echo", "done"),
    (ORC, TSK, PRJ, "agent.echo", "completed", {"output": "dict, not AgentResult"}),
])
def test_invalid_results(args):
    with pytest.raises(OrchestrationResultError):
        OrchestrationResult(*args, error=AgentInvocationError("x") if args[4] == "failed" else None)


def test_statuses_do_not_extend_the_task_lifecycle():
    assert {s.value for s in OrchestrationStatus} <= {s.value for s in TaskStatus}


# --- policy -------------------------------------------------------------------------


def test_default_policy():
    assert DEFAULT_POLICY.to_dict() == {"allowed_task_statuses": ["running"], "allowed_agents": None,
                                        "agents_per_orchestration": 1, "transitions_task": False,
                                        "retries": False, "chains_agents": False}
    DEFAULT_POLICY.check_task(TaskStatus.RUNNING)
    for status in set(TaskStatus) - {TaskStatus.RUNNING}:
        with pytest.raises(TaskNotRunningError):
            DEFAULT_POLICY.check_task(status)


@pytest.mark.parametrize("statuses", [{"queued", "running"}, {"waiting"}, set(), {"executing"}, "running"])
def test_policy_only_admits_running(statuses):
    with pytest.raises(ValueError):
        OrchestrationPolicy(allowed_task_statuses=statuses)


def test_agent_allowlist():
    policy = OrchestrationPolicy(allowed_agents={"agent.echo"})
    policy.check_agent("agent.echo")
    with pytest.raises(PolicyDeniedError):
        policy.check_agent("agent.probe")
    for bad in ("agent.echo", {"reference.echo"}, {"agent.echo\n"}, {None}):
        with pytest.raises(ValueError):
            OrchestrationPolicy(allowed_agents=bad)


def test_policy_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_POLICY.allowed_agents = frozenset()


# --- errors -------------------------------------------------------------------------


def test_error_codes_cover_the_brief():
    brief = {"request_invalid", "task_not_found", "project_not_found", "task_not_running", "agent_not_found",
             "agent_disabled", "agent_invocation_failed", "policy_denied", "context_invalid", "result_invalid",
             "internal"}
    assert brief <= {c.value for c in OrchestrationErrorCode}
    assert set(ERROR_TYPES) == set(OrchestrationErrorCode)


def test_errors_are_redacted():
    err = AgentInvocationError("failed token=ghp_SECRET-1234567890abcdef", details=("password=hunter2",))
    assert "ghp_SECRET" not in str(err.to_dict()) and "hunter2" not in str(err.to_dict())
