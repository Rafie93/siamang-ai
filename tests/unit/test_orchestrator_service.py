"""Orchestrator.execute: one running task, one registered agent, one invocation; the task is never changed."""

import pytest

from siamang.agents import AgentRegistry, AgentResult, ModelAccess, ToolAccess, build_agent_registry
from siamang.agents.errors import AgentInputError
from siamang.git.client import GitClient
from siamang.models import Capability, ModelRegistry, ModelRequest, ModelRouter
from siamang.orchestrator import (
    OrchestrationErrorCode,
    OrchestrationPolicy,
    OrchestrationRequest,
    OrchestrationStatus,
    Orchestrator,
)
from siamang.tasks import TaskFailure, TaskRequest, TaskResult
from siamang.tools import build_tool_registry
from tests.support.agents import allow_probe, definition, make_world
from tests.support.fake_models import FakeProvider, fake_model

C = OrchestrationErrorCode
TG = Capability.TEXT_GENERATION


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def orch(world):
    return Orchestrator(world.engine, build_agent_registry())


def oreq(world, **over):
    data = dict(task_id=world.task.task_id, project_id=world.project.project_id, agent_id="agent.echo",
                instruction="Ulangi pesan", input={"message": "hello"})
    data.update(over)
    return OrchestrationRequest(**data)


def snapshot(world):
    return world.engine.get(world.task.task_id), len(world.engine.history(world.task.task_id))


def probe_orchestrator(world, monkeypatch, behavior, *, router=None, tools=None, **over):
    allow_probe(monkeypatch, behavior)
    agents = build_agent_registry()
    agents.register(definition(**over))
    return Orchestrator(world.engine, agents, router=router, tools=tools)


# --- the reference path ------------------------------------------------------------


def test_reference_echo(world, orch):
    before = snapshot(world)
    r = orch.execute(oreq(world))
    assert r.status is OrchestrationStatus.COMPLETED and r.success and r.error is None
    assert (r.task_id, r.project_id, r.agent_id) == (world.task.task_id, world.project.project_id, "agent.echo")
    assert r.orchestration_id.startswith("orc_")
    assert r.agent_result.status.value == "completed" and dict(r.agent_result.output) == {"message": "hello"}
    assert r.metadata["agents_invoked"] == 1 and isinstance(r.metadata["duration_ms"], int)
    after = snapshot(world)
    assert after == before and after[0].status.value == "running"


def test_each_execution_has_its_own_id_and_is_deterministic(world, orch):
    results = [orch.execute(oreq(world, input={"message": "sama"})) for _ in range(3)]
    assert len({r.orchestration_id for r in results}) == 3
    assert {r.agent_result.output["message"] for r in results} == {"sama"}


def test_result_is_structured_and_json_ready(world, orch):
    import json

    d = orch.execute(oreq(world)).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert set(d) == {"orchestration_id", "task_id", "project_id", "agent_id", "success", "status", "agent_result",
                      "error", "metadata"}


# --- request -----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, {"task_id": "tsk_01ABC"}, "agent.echo", 42])
def test_request_must_be_typed(orch, bad):
    r = orch.execute(bad)
    assert r.error.code == C.REQUEST_INVALID and r.agent_result is None
    assert (r.task_id, r.project_id, r.agent_id) == (None, None, None)


# --- task --------------------------------------------------------------------------


def test_unknown_task(world, orch):
    r = orch.execute(oreq(world, task_id="tsk_01UNKNOWN"))
    assert r.error.code == C.TASK_NOT_FOUND and r.metadata["agents_invoked"] == 0


@pytest.mark.parametrize("state", ["queued", "waiting", "completed", "failed", "cancelled"])
def test_task_must_be_running_and_is_never_transitioned(db, state):
    w = make_world(db, start=state != "queued")
    e, tid = w.engine, w.task.task_id
    {"queued": lambda: None, "waiting": lambda: e.wait(tid), "completed": lambda: e.complete(tid, TaskResult("x")),
     "failed": lambda: e.fail(tid, TaskFailure("QA_FAILED", retryable=True)), "cancelled": lambda: e.cancel(tid)}[state]()
    before = snapshot(w)
    r = Orchestrator(e, build_agent_registry()).execute(oreq(w))
    assert r.error.code == C.TASK_NOT_RUNNING and state in r.error.message
    assert snapshot(w) == before and before[0].status.value == state  # queued stays queued


def test_task_of_another_project(world, orch):
    other = world.projects.register(name="Kasir", root_path="/work/kasir")
    r = orch.execute(oreq(world, project_id=other.project_id))
    assert r.error.code == C.REQUEST_INVALID and r.error.details == ("project_id",)


# --- project -----------------------------------------------------------------------


def test_archived_project(world, orch):
    world.projects.archive(world.project.project_id)
    before = snapshot(world)
    r = orch.execute(oreq(world))
    assert r.error.code == C.PROJECT_ARCHIVED and snapshot(world) == before
    assert world.projects.get(world.project.project_id).is_active is False  # never restored


def test_unknown_project(world, db):
    """A task row whose project is not registered (tasks have no FK, decision K5) is refused."""
    t = world.engine.create(TaskRequest(project_id=world.project.project_id, description="x", source="test")).task
    db.conn.execute("DROP TRIGGER tasks_immutable_identity")
    db.conn.execute("UPDATE tasks SET project_id = 'prj_01GHOST', status = 'running', "
                    "started_at = created_at WHERE task_id = ?", (t.task_id,))
    r = Orchestrator(world.engine, build_agent_registry()).execute(
        oreq(world, task_id=t.task_id, project_id="prj_01GHOST"))
    assert r.error.code == C.PROJECT_NOT_FOUND


# --- agent -------------------------------------------------------------------------


def test_unknown_agent(world, orch):
    r = orch.execute(oreq(world, agent_id="agent.programmer"))
    assert r.error.code == C.AGENT_NOT_FOUND and r.agent_result is None


def test_disabled_agent(world, monkeypatch):
    allow_probe(monkeypatch)
    agents = AgentRegistry()
    agents.register(definition(enabled=False))
    r = Orchestrator(world.engine, agents).execute(oreq(world, agent_id="agent.probe"))
    assert r.error.code == C.AGENT_DISABLED and r.agent_result is None


def test_policy_allowlist(world):
    orch = Orchestrator(world.engine, build_agent_registry(), policy=OrchestrationPolicy(allowed_agents={"agent.x"}))
    assert orch.execute(oreq(world)).error.code == C.POLICY_DENIED


def test_agent_input_invalid_is_an_invocation_failure(world, orch):
    r = orch.execute(oreq(world, input={"msg": "typo"}))
    assert r.status is OrchestrationStatus.FAILED and r.error.code == C.AGENT_INVOCATION_FAILED
    assert r.error.cause == "agent_input_invalid" and r.agent_result.error.code.value == "agent_input_invalid"


def test_agent_failure_is_translated_with_cause(world, monkeypatch):
    def behavior(agent, req, ctx):
        raise RuntimeError("password=hunter2 /Users/x/.env")

    before = snapshot(world)
    r = probe_orchestrator(world, monkeypatch, behavior).execute(oreq(world, agent_id="agent.probe"))
    d = r.to_dict()
    assert d["status"] == "failed" and d["error"]["code"] == "agent_invocation_failed"
    assert d["error"]["cause"] == "agent_execution_failed"
    assert "hunter2" not in str(d) and ".env" not in str(d) and "Traceback" not in str(d)
    assert snapshot(world) == before  # a failed agent does not fail the task


def test_agent_error_keeps_retryable_without_retrying(world, monkeypatch):
    calls = []

    def behavior(agent, req, ctx):
        calls.append(1)
        raise AgentInputError("ambiguous", retryable=True)

    r = probe_orchestrator(world, monkeypatch, behavior).execute(oreq(world, agent_id="agent.probe"))
    assert r.error.retryable is True and r.error.cause == "agent_input_invalid" and calls == [1]


def test_invalid_agent_result(world, monkeypatch):
    r = probe_orchestrator(world, monkeypatch, lambda a, req, ctx: {"message": "not a result"}).execute(
        oreq(world, agent_id="agent.probe"))
    assert r.error.code == C.RESULT_INVALID and r.error.cause == "agent_result_invalid"


def test_result_identity_mismatch(world, orch, monkeypatch):
    forged = AgentResult.completed("agent.probe", world.task.task_id, {"message": "x"})
    monkeypatch.setattr(orch._agents, "invoke", lambda *a: forged)
    assert orch.execute(oreq(world)).error.code == C.RESULT_INVALID
    other = AgentResult.completed("agent.echo", "tsk_01OTHER", {"message": "x"})
    monkeypatch.setattr(orch._agents, "invoke", lambda *a: other)
    assert orch.execute(oreq(world)).error.code == C.RESULT_INVALID


def test_waiting_passes_through_without_task_change(world, monkeypatch):
    before = snapshot(world)
    r = probe_orchestrator(world, monkeypatch, lambda a, req, ctx: AgentResult.waiting(
        a.agent_id, ctx.task_id, {"question": "tabel apa?"})).execute(oreq(world, agent_id="agent.probe"))
    assert r.status is OrchestrationStatus.WAITING and r.error is None
    assert snapshot(world) == before and before[0].status.value == "running"  # not moved to waiting


def test_unexpected_exception_is_internal(world, orch, monkeypatch):
    def boom(*a):
        raise KeyError("secret-path /etc/shadow")

    monkeypatch.setattr(orch._agents, "invoke", boom)
    r = orch.execute(oreq(world))
    assert r.error.code == C.INTERNAL and r.error.cause == "KeyError" and "shadow" not in str(r.to_dict())


# --- context -----------------------------------------------------------------------


def test_context_is_the_phase11_context(world, monkeypatch):
    seen = {}

    def behavior(agent, req, ctx):
        seen.update(project=ctx.project.to_dict(), models=type(ctx.models), tools=type(ctx.tools),
                    orchestration=ctx.metadata["orchestration_id"], task=ctx.task)
        return AgentResult.completed(agent.agent_id, ctx.task_id, {"message": "ok"})

    reg = ModelRegistry()
    reg.register_provider(FakeProvider("fake", [fake_model("m")]))
    tools = build_tool_registry(projects=world.projects)
    orch = probe_orchestrator(world, monkeypatch, behavior, router=ModelRouter(reg), tools=tools,
                              model_requirements={TG}, tool_permissions={"tool.invoke", "project.read"})
    r = orch.execute(oreq(world, agent_id="agent.probe"))
    assert r.success and seen["orchestration"] == r.orchestration_id
    assert seen["project"]["project_id"] == world.project.project_id and "root_path" not in seen["project"]
    assert seen["models"] is ModelAccess and seen["tools"] is ToolAccess
    assert seen["task"] == world.engine.get(world.task.task_id)


def test_model_access_stays_behind_the_wrapper(world, monkeypatch):
    def behavior(agent, req, ctx):
        text = ctx.require_models().generate(ModelRequest.from_prompt(req.input["message"])).content
        return AgentResult.completed(agent.agent_id, ctx.task_id, {"message": text})

    reg = ModelRegistry()
    reg.register_provider(FakeProvider("fake", [fake_model("m")]))
    orch = probe_orchestrator(world, monkeypatch, behavior, router=ModelRouter(reg), model_requirements={TG})
    assert orch.execute(oreq(world, agent_id="agent.probe")).agent_result.output["message"] == "[m] hello"
    orch = probe_orchestrator(world, monkeypatch, behavior, model_requirements={TG})  # no router supplied
    r = orch.execute(oreq(world, agent_id="agent.probe"))
    assert r.error.code == C.AGENT_INVOCATION_FAILED and r.error.cause == "agent_model_unavailable"


def test_raw_services_never_reach_the_agent(world, monkeypatch):
    """A raw GitClient passed as the tool service is refused by the Phase 11 context builder."""
    orch = probe_orchestrator(world, monkeypatch, None, tools=GitClient(),
                              tool_permissions={"tool.invoke", "project.read"})
    r = orch.execute(oreq(world, agent_id="agent.probe"))
    assert r.error.code == C.CONTEXT_INVALID and r.error.cause == "agent_context_invalid"
    assert r.agent_result is None


def test_constructor_requires_the_real_services(world, db):
    with pytest.raises(TypeError):
        Orchestrator(db, build_agent_registry())
    with pytest.raises(TypeError):
        Orchestrator(world.engine, {"agent.echo": object()})
    with pytest.raises(TypeError):
        Orchestrator(world.engine, build_agent_registry(), policy={"allowed_task_statuses": ["queued"]})
