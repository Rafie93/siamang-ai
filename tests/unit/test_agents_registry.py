"""AgentRegistry: explicit registration, allowlisted implementations, validated invocation."""

import dataclasses

import pytest

from siamang.agents import (
    AgentContext,
    AgentErrorCode,
    AgentRegistry,
    AgentResult,
    AgentStatus,
    build_agent_registry,
    build_context,
)
from siamang.agents.errors import (
    AgentDefinitionError,
    AgentDuplicateError,
    AgentImplementationNotFoundError,
    AgentInputError,
    AgentNotFoundError,
)
from siamang.agents.reference import ECHO, EchoAgent
from siamang.contracts.schema import F
from siamang.tasks import TaskFailure, TaskRequest
from tests.support.agents import ProbeAgent, allow_probe, definition, make_world, request

C = AgentErrorCode


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def registry():
    return build_agent_registry()


def echo_context(world, registry, **kw):
    return build_context(registry.get("agent.echo"), task=world.task, projects=world.projects, **kw)


_DEFAULT = object()


def invoke(registry, world, agent_id="agent.echo", req=None, ctx=_DEFAULT):
    return registry.invoke(agent_id, req or request(world), echo_context(world, registry) if ctx is _DEFAULT else ctx)


# --- catalog ----------------------------------------------------------------------


def test_build_registers_only_the_reference_agent(registry):
    assert [d.agent_id for d in registry.list()] == ["agent.echo"]
    assert registry.get("agent.echo") is ECHO
    assert registry.exists("agent.echo") and not registry.exists("agent.programmer")
    assert not registry.exists(None) and not registry.exists(["agent.echo"])


def test_register_get_list_sorted(monkeypatch):
    allow_probe(monkeypatch)
    reg = AgentRegistry()
    reg.register(definition(agent_id="agent.probe.b"))
    reg.register(definition(agent_id="agent.probe.a"))
    reg.register(ECHO)
    assert [d.agent_id for d in reg.list()] == ["agent.echo", "agent.probe.a", "agent.probe.b"]


def test_duplicate_rejected(registry):
    with pytest.raises(AgentDuplicateError):
        registry.register(ECHO)
    with pytest.raises(AgentDuplicateError):
        registry.register(dataclasses.replace(ECHO, name="Echo again"))


def test_unknown_agent(registry):
    for bad in ("agent.programmer", "agent.echo\n", None, 42):
        with pytest.raises(AgentNotFoundError):
            registry.get(bad)


def test_unknown_implementation_rejected():
    with pytest.raises(AgentImplementationNotFoundError):
        AgentRegistry().register(definition(implementation="test.probe"))  # not in the real allowlist


@pytest.mark.parametrize("thing", [lambda request, context: None, EchoAgent, EchoAgent(ECHO), "agent.echo",
                                   {"agent_id": "agent.echo"}, None])
def test_only_definitions_can_be_registered(thing):
    with pytest.raises(AgentDefinitionError):
        AgentRegistry().register(thing)


def test_definition_subclass_rejected():
    class Sneaky(type(ECHO)):
        pass

    sneaky = Sneaky(**{f.name: getattr(ECHO, f.name) for f in dataclasses.fields(ECHO)})
    with pytest.raises(AgentDefinitionError):
        AgentRegistry().register(sneaky)


def test_no_handler_or_callable_parameter():
    fields = {f.name for f in dataclasses.fields(ECHO)}
    assert not fields & {"handler", "callable", "function", "run", "factory", "module", "class_path"}


# --- invocation: the reference path -----------------------------------------------


def test_echo_end_to_end(world, registry):
    r = invoke(registry, world)
    assert r.status is AgentStatus.COMPLETED and r.success
    assert dict(r.output) == {"message": "halo"}
    assert r.agent_id == "agent.echo" and r.task_id == world.task.task_id
    assert r.metadata["agent_version"] == "1" and r.metadata["project_id"] == world.project.project_id
    assert isinstance(r.metadata["duration_ms"], int)


def test_echo_is_deterministic(world, registry):
    outs = {invoke(registry, world, req=request(world, input={"message": "sama"})).to_dict()["output"]["message"]
            for _ in range(5)}
    assert outs == {"sama"}


def test_invocation_never_mutates_the_task(world, registry, db):
    before = world.engine.get(world.task.task_id)
    events = len(world.engine.history(world.task.task_id))
    rows = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    for msg in ("a", "b"):
        invoke(registry, world, req=request(world, input={"message": msg}))
    invoke(registry, world, req=request(world, input={}))  # a failed invocation too
    assert world.engine.get(world.task.task_id) == before
    assert len(world.engine.history(world.task.task_id)) == events
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == rows


# --- invocation: every step denies -------------------------------------------------


def test_unknown_and_disabled(world, registry, monkeypatch):
    assert invoke(registry, world, agent_id="agent.nope").error.code == C.AGENT_NOT_FOUND
    assert invoke(registry, world, agent_id=None).error.code == C.AGENT_NOT_FOUND
    allow_probe(monkeypatch)
    reg = AgentRegistry()
    off = reg.register(definition(enabled=False))
    ctx = build_context(off, task=world.task, projects=world.projects)
    r = reg.invoke("agent.probe", request(world), ctx)
    assert (r.status, r.error.code, r.agent_id) == (AgentStatus.FAILED, C.AGENT_DISABLED, "agent.probe")


def test_request_must_be_an_agent_request(world, registry):
    for bad in ({"task_id": world.task.task_id}, None, "halo"):
        r = registry.invoke("agent.echo", bad, echo_context(world, registry))
        assert r.error.code == C.AGENT_INPUT_INVALID and r.task_id is None


def test_context_must_be_for_this_agent(world, registry, monkeypatch):
    for bad in (None, {"task_id": world.task.task_id}, world.task):
        assert invoke(registry, world, ctx=bad).error.code == C.AGENT_CONTEXT_INVALID
    allow_probe(monkeypatch)
    other = build_context(definition(), task=world.task, projects=world.projects)
    assert invoke(registry, world, ctx=other).error.code == C.AGENT_CONTEXT_INVALID


def test_task_mismatch(world, registry):
    other = world.engine.start(world.engine.create(
        TaskRequest(project_id=world.project.project_id, description="lain", source="test")).task.task_id)
    r = invoke(registry, world, req=request(world, task_id=other.task_id))
    assert r.error.code == C.AGENT_CONTEXT_INVALID and "different task" in r.error.message


def test_project_mismatch(world, registry):
    second = world.projects.register(name="Kasir", root_path="/work/kasir")
    r = invoke(registry, world, req=request(world, project_id=second.project_id))
    assert r.error.code == C.AGENT_CONTEXT_INVALID and "different project" in r.error.message


@pytest.mark.parametrize("prepare", ["queued", "waiting", "completed", "failed", "cancelled"])
def test_task_must_be_running(db, registry, prepare):
    from siamang.tasks import TaskResult

    w = make_world(db, start=prepare != "queued")
    e, tid = w.engine, w.task.task_id
    task = {
        "queued": lambda: e.get(tid),
        "waiting": lambda: e.wait(tid),
        "completed": lambda: e.complete(tid, TaskResult("done")),
        "failed": lambda: e.fail(tid, TaskFailure("QA_FAILED", retryable=True)),
        "cancelled": lambda: e.cancel(tid),
    }[prepare]()
    ctx = build_context(ECHO, task=task, projects=w.projects)
    r = registry.invoke("agent.echo", request(w), ctx)
    assert r.error.code == C.AGENT_CONTEXT_INVALID and prepare in r.error.message


@pytest.mark.parametrize("data", [{}, {"message": 1}, {"message": "x", "extra": "y"}])
def test_input_must_match_contract(world, registry, data):
    r = invoke(registry, world, req=request(world, input=data))
    assert r.error.code == C.AGENT_INPUT_INVALID


def test_allowlist_rechecked_at_use(world, registry, monkeypatch):
    """A swapped-in instance (here: a subclass) is refused even after registration."""

    class Impostor(EchoAgent):
        __slots__ = ()

    registry._agents["agent.echo"] = Impostor(ECHO)
    assert invoke(registry, world).error.code == C.AGENT_IMPLEMENTATION_NOT_FOUND


# --- invocation: what the agent returns or raises ----------------------------------


def _probe(monkeypatch, world, behavior, **over):
    allow_probe(monkeypatch, behavior)
    reg = AgentRegistry()
    d = reg.register(definition(**over))
    return reg.invoke("agent.probe", request(world), build_context(d, task=world.task, projects=world.projects))


def test_waiting_result_passes_through(world, monkeypatch):
    r = _probe(monkeypatch, world, lambda a, req, ctx: AgentResult.waiting(a.agent_id, ctx.task_id,
                                                                           {"question": "tabel apa?"}))
    assert r.status is AgentStatus.WAITING and r.output["question"] == "tabel apa?" and r.error is None


def test_agent_error_is_kept(world, monkeypatch):
    def behavior(a, req, ctx):
        raise AgentInputError("message too vague")

    r = _probe(monkeypatch, world, behavior)
    assert (r.error.code, r.error.agent_id, r.error.message) == (C.AGENT_INPUT_INVALID, "agent.probe",
                                                                 "message too vague")


def test_arbitrary_exception_is_sanitized(world, monkeypatch):
    def behavior(a, req, ctx):
        raise RuntimeError("password=hunter2 at /Users/x/.env\nTraceback ...")

    r = _probe(monkeypatch, world, behavior)
    d = r.to_dict()
    assert d["error"]["code"] == "agent_execution_failed" and d["error"]["cause"] == "RuntimeError"
    assert "hunter2" not in str(d) and ".env" not in str(d) and "Traceback" not in str(d)


@pytest.mark.parametrize("bad", [
    lambda a, req, ctx: None,
    lambda a, req, ctx: {"message": "halo"},
    lambda a, req, ctx: AgentResult.completed("agent.echo", ctx.task_id, {"message": "x"}),  # wrong agent
    lambda a, req, ctx: AgentResult.completed(a.agent_id, "tsk_01OTHER", {"message": "x"}),  # wrong task
    lambda a, req, ctx: AgentResult.completed(a.agent_id, ctx.task_id, {}),  # missing output field
    lambda a, req, ctx: AgentResult.completed(a.agent_id, ctx.task_id, {"message": "x", "extra": 1}),
    lambda a, req, ctx: AgentResult.completed(a.agent_id, ctx.task_id, {"message": 5}),
])
def test_invalid_results_are_refused(world, monkeypatch, bad):
    assert _probe(monkeypatch, world, bad).error.code == C.AGENT_RESULT_INVALID


def test_output_schema_kinds(world, monkeypatch):
    r = _probe(monkeypatch, world, lambda a, req, ctx: AgentResult.completed(
        a.agent_id, ctx.task_id, {"findings": [{"line": 3}], "count": 1}),
        output_schema={"findings": F("list", required=True, item=F("object")), "count": F("int", required=True)})
    assert r.success and r.output["count"] == 1


def test_no_retry_on_failure(world, monkeypatch):
    calls = []

    def behavior(a, req, ctx):
        calls.append(1)
        raise AgentInputError("x", retryable=True)

    r = _probe(monkeypatch, world, behavior)
    assert r.error.retryable is True and calls == [1]


# --- no agent-to-agent calls --------------------------------------------------------


def test_agent_cannot_invoke_another_agent(world, monkeypatch):
    reg_holder = {}
    inner = []

    def behavior(a, req, ctx):
        echo_ctx = build_context(ECHO, task=world.task, projects=world.projects)
        inner.append(reg_holder["reg"].invoke("agent.echo", request(world), echo_ctx))
        return AgentResult.completed(a.agent_id, ctx.task_id, {"message": "done"})

    allow_probe(monkeypatch, behavior)
    reg = build_agent_registry()
    reg_holder["reg"] = reg
    d = reg.register(definition())
    outer = reg.invoke("agent.probe", request(world), build_context(d, task=world.task, projects=world.projects))
    assert outer.success
    assert inner[0].error.code == C.AGENT_PERMISSION_DENIED and "another agent" in inner[0].error.message


def test_agent_cannot_invoke_itself_through_a_second_registry(world, monkeypatch):
    seen = []

    def behavior(a, req, ctx):
        seen.append(build_agent_registry().invoke("agent.echo", request(world),
                                                  build_context(ECHO, task=world.task, projects=world.projects)))
        return AgentResult.completed(a.agent_id, ctx.task_id, {"message": "x"})

    _probe(monkeypatch, world, behavior)
    assert seen[0].error.code == C.AGENT_PERMISSION_DENIED


def test_guard_is_released_after_each_invocation(world, registry, monkeypatch):
    def behavior(a, req, ctx):
        raise RuntimeError("boom")

    _probe(monkeypatch, world, behavior)
    assert invoke(registry, world).success  # a crashed invocation does not leave the guard set


def test_context_carries_no_registry(world, registry):
    ctx = echo_context(world, registry)
    for name in AgentContext.__slots__:
        assert not isinstance(getattr(ctx, name), AgentRegistry)
    assert not hasattr(ProbeAgent(definition()), "registry")
