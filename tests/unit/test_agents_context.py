"""AgentContext: explicit typed fields only; model and tool access only through their registries."""

import dataclasses
import os
import sqlite3
import subprocess

import pytest

from siamang.agents import (
    AgentContext,
    AgentErrorCode,
    AgentProject,
    AgentRegistry,
    AgentResult,
    ModelAccess,
    ToolAccess,
    build_context,
)
from siamang.agents.errors import (
    AgentContextError,
    AgentInputError,
    AgentModelUnavailableError,
    AgentPermissionError,
    AgentToolUnavailableError,
)
from siamang.agents.reference import ECHO
from siamang.git import GitWorkspaceManager
from siamang.git.client import GitClient
from siamang.models import Capability, ModelRegistry, ModelRequest, ModelRouter, ResponseFormat
from siamang.storage import Database
from siamang.tools import DEFAULT_POLICY, ActorType, Permission, PermissionPolicy, build_tool_registry
from siamang.tools.permissions import READ_ONLY
from tests.support.agents import allow_probe, definition, make_world, request
from tests.support.fake_models import FAKE_SECRET, FakeProvider, VendorAuthError, fake_model
from tests.support.git import make_repo

TG, SO, REASON = Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT, Capability.REASONING
TOOL_PERMS = {"tool.invoke", "project.read", "workspace.read", "git.read"}


@pytest.fixture
def world(db):
    return make_world(db)


def router(*models, **kw):
    reg = ModelRegistry()
    provider = FakeProvider("fake", list(models) or [fake_model("m", caps=(TG, SO, REASON))], **kw)
    reg.register_provider(provider)
    return ModelRouter(reg), provider


def ctx_for(world, d=ECHO, **kw):
    return build_context(d, task=world.task, projects=world.projects, **kw)


# --- shape --------------------------------------------------------------------------


def test_context_fields_are_explicit_and_typed():
    assert [f.name for f in dataclasses.fields(AgentContext)] == [
        "agent_id", "task", "project", "models", "tools", "permissions", "metadata"]
    assert not hasattr(AgentContext, "get") and not hasattr(AgentContext, "services")
    assert not hasattr(AgentContext, "__getitem__") and not hasattr(AgentContext, "__getattr__")


def test_context_is_immutable_and_closed(world):
    ctx = ctx_for(world, metadata={"phase": "a"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.task = None
    with pytest.raises((AttributeError, TypeError)):
        ctx.database = world.projects.db  # slots: no new attributes
    with pytest.raises(TypeError):
        ctx.metadata["phase"] = "b"


def test_project_view_has_no_filesystem_location(world):
    ctx = ctx_for(world)
    assert ctx.project.project_id == world.project.project_id and ctx.project.name == "Toko"
    assert "root_path" not in ctx.project.to_dict() and not hasattr(ctx.project, "root_path")
    assert ctx.task_id == world.task.task_id and ctx.task == world.task


def test_echo_context_gets_no_models_tools_or_permissions(world):
    r, _ = router()
    ctx = ctx_for(world, router=r, tools=build_tool_registry(projects=world.projects))
    assert ctx.models is None and ctx.tools is None and ctx.permissions == frozenset()
    with pytest.raises(AgentModelUnavailableError):
        ctx.require_models()
    with pytest.raises(AgentToolUnavailableError):
        ctx.require_tools()


# --- injection is refused -------------------------------------------------------------


def _base(world):
    return dict(agent_id="agent.echo", task=world.task, project=AgentProject.of(world.project))


@pytest.mark.parametrize("field, value", [
    ("models", "GitClient"),
    ("models", "router"),
    ("models", subprocess),
    ("models", os),
    ("models", "db"),
    ("tools", "GitClient"),
    ("tools", "tool_registry"),
    ("tools", "workspace"),
    ("tools", open),
    ("tools", "sqlite"),
    ("task", "task_dict"),
    ("project", "project"),  # the full Project (with root_path) is not accepted
    ("project", "registry"),
    ("permissions", {"tool.invoke"}),
    ("permissions", frozenset({"tool.invoke"})),
    ("permissions", frozenset({Permission.GIT_READ})),  # a claim the policy did not grant
    ("agent_id", "agent.echo\n"),
    ("agent_id", None),
    ("metadata", {"api_key": "sk"}),
])
def test_context_rejects_raw_infrastructure(world, tmp_path, field, value):
    db = world.projects.db
    stand_ins = {
        "GitClient": GitClient(),
        "router": router()[0],
        "db": db,
        "sqlite": db.conn,
        "tool_registry": build_tool_registry(projects=world.projects),
        "workspace": GitWorkspaceManager(world.projects),
        "task_dict": world.task.to_dict(),
        "project": world.project,
        "registry": world.projects,
    }
    data = _base(world)
    data[field] = stand_ins.get(value, value) if isinstance(value, str) and field != "agent_id" else value
    with pytest.raises(AgentContextError):
        AgentContext(**data)


def test_facade_subclasses_are_refused(world):
    class LooseModels(ModelAccess):
        __slots__ = ()

    d = definition(model_requirements={TG})
    allowed = LooseModels(router()[0], d)
    with pytest.raises(AgentContextError):
        AgentContext("agent.probe", world.task, AgentProject.of(world.project), models=allowed)


def test_facades_must_belong_to_this_agent_and_project(world):
    d = definition(model_requirements={TG}, tool_permissions=TOOL_PERMS)
    other = definition(agent_id="agent.probe.other", model_requirements={TG}, tool_permissions=TOOL_PERMS)
    tools = build_tool_registry(projects=world.projects)
    project = AgentProject.of(world.project)
    with pytest.raises(AgentContextError):
        AgentContext("agent.probe", world.task, project, models=ModelAccess(router()[0], other))
    with pytest.raises(AgentContextError):
        AgentContext("agent.probe", world.task, project, tools=ToolAccess(tools, other, world.project.project_id))
    second = world.projects.register(name="Kasir", root_path="/work/kasir")
    with pytest.raises(AgentContextError):
        AgentContext("agent.probe", world.task, project, tools=ToolAccess(tools, d, second.project_id))


def test_facades_refuse_raw_services(world):
    d = definition(model_requirements={TG}, tool_permissions=TOOL_PERMS)
    for raw in (GitClient(), world.projects, world.projects.db, subprocess):
        with pytest.raises(AgentContextError):
            ModelAccess(raw, d)
        with pytest.raises(AgentContextError):
            ToolAccess(raw, d, world.project.project_id)


def test_task_and_project_must_agree(world):
    second = world.projects.register(name="Kasir", root_path="/work/kasir")
    with pytest.raises(AgentContextError):
        AgentContext("agent.echo", world.task, AgentProject.of(second))


# --- build_context: project identity through the Project Registry ---------------------


def test_build_context_resolves_project_through_registry(world, db):
    with pytest.raises(AgentContextError):
        build_context(ECHO, task=world.task, projects=db)  # a raw database is not a registry
    world.projects.archive(world.project.project_id)
    with pytest.raises(AgentContextError) as info:
        ctx_for(world)
    assert "archived" in info.value.message


def test_build_context_unknown_project(tmp_path, world):
    with Database(tmp_path / "other.db") as other_db:
        from siamang.projects import ProjectRegistry

        with pytest.raises(AgentContextError) as info:
            build_context(ECHO, task=world.task, projects=ProjectRegistry(other_db))
    assert "not registered" in info.value.message


def test_build_context_needs_a_task_snapshot(world):
    for bad in (world.task.to_dict(), world.task.task_id, world.engine):
        with pytest.raises(AgentContextError):
            build_context(ECHO, task=bad, projects=world.projects)


# --- model access: through the ModelRouter only ---------------------------------------


def test_model_access_selects_through_router(world):
    r, provider = router()
    d = definition(model_requirements={TG, REASON})
    ctx = ctx_for(world, d, router=r)
    resp = ctx.require_models().generate(ModelRequest.from_prompt("halo", required_capabilities={REASON}))
    assert resp.content == "[m] halo" and provider.calls == [("m", provider.calls[0][1])]


def test_model_access_refuses_pinned_models_and_undeclared_capabilities(world):
    r, provider = router()
    models = ctx_for(world, definition(model_requirements={TG}), router=r).require_models()
    with pytest.raises(AgentPermissionError):
        models.generate(ModelRequest.from_prompt("halo", model_id="m"))
    with pytest.raises(AgentPermissionError) as info:
        models.generate(ModelRequest.from_prompt("halo", response_format=ResponseFormat.JSON))
    assert info.value.details == ("structured_output",)
    with pytest.raises(AgentInputError):
        models.generate({"messages": [{"role": "user", "content": "halo"}]})
    assert provider.calls == []


def test_model_errors_are_normalized_without_secrets(world):
    r, _ = router(fail_with=VendorAuthError(f"bad key {FAKE_SECRET}"))
    models = ctx_for(world, definition(model_requirements={TG}), router=r).require_models()
    with pytest.raises(AgentModelUnavailableError) as info:
        models.generate(ModelRequest.from_prompt("halo"))
    d = info.value.to_dict()
    assert d["code"] == "agent_model_unavailable" and d["cause"] == "authentication_error"
    assert FAKE_SECRET not in str(d)


def test_no_capable_model(world):
    r, _ = router(fake_model("m", caps=(TG,)))
    models = ctx_for(world, definition(model_requirements={TG, REASON}), router=r).require_models()
    with pytest.raises(AgentModelUnavailableError) as info:
        models.generate(ModelRequest.from_prompt("halo", required_capabilities={REASON}))
    assert info.value.cause == "unsupported_capability"


def test_model_access_needs_declared_requirements(world):
    with pytest.raises(AgentPermissionError):
        ModelAccess(router()[0], ECHO)


# --- tool access: through ToolRegistry.invoke + PermissionPolicy -----------------------


@pytest.fixture
def repo_world(db, tmp_path):
    repo = make_repo(tmp_path / "repo")
    return make_world(db, root_path=str(repo.root))


def test_default_policy_grants_agents_nothing(repo_world):
    w = repo_world
    tools = build_tool_registry(projects=w.projects, git=GitWorkspaceManager(w.projects))
    ctx = ctx_for(w, definition(tool_permissions=TOOL_PERMS), tools=tools)
    assert ctx.permissions == frozenset()  # declared is not granted
    r = ctx.require_tools().invoke("git.status", {"project_id": w.project.project_id})
    assert not r.success and r.error.code == "tool_permission_denied"
    assert r.metadata["actor"] == "agent:agent.probe"
    assert tools.policy is DEFAULT_POLICY


def test_policy_ceiling_is_the_authority(repo_world):
    w = repo_world
    policy = PermissionPolicy({ActorType.AGENT: READ_ONLY})
    tools = build_tool_registry(projects=w.projects, git=GitWorkspaceManager(w.projects), policy=policy)
    ctx = ctx_for(w, definition(tool_permissions=TOOL_PERMS), tools=tools)
    assert ctx.permissions == {Permission(p) for p in TOOL_PERMS}
    r = ctx.require_tools().invoke("git.status", {"project_id": w.project.project_id})
    assert r.success and r.data["clean"] is True
    # declaring less than the ceiling: the declared set limits the agent
    narrow = ctx_for(w, definition(tool_permissions={"tool.invoke", "project.read"}), tools=tools)
    r = narrow.require_tools().invoke("git.status", {"project_id": w.project.project_id})
    assert r.error.code == "tool_permission_denied" and set(r.error.details) == {"git.read", "workspace.read"}


def test_tool_access_is_scoped_to_the_context_project(repo_world, tmp_path):
    w = repo_world
    other = w.projects.register(name="Other", root_path=str(make_repo(tmp_path / "other").root))
    policy = PermissionPolicy({ActorType.AGENT: READ_ONLY})
    tools = build_tool_registry(projects=w.projects, git=GitWorkspaceManager(w.projects), policy=policy)
    access = ctx_for(w, definition(tool_permissions=TOOL_PERMS), tools=tools).require_tools()
    r = access.invoke("git.status", {"project_id": other.project_id})
    assert r.error.code == "tool_scope_denied"
    for bad in ({"project_id": w.project.project_id, "argv": ["push"]},
                {"project_id": w.project.project_id, "cwd": "/"}):
        assert access.invoke("git.status", bad).error.code == "tool_input_invalid"
    assert access.invoke("git.push", {"project_id": w.project.project_id}).error.code == "tool_not_found"


def test_tool_access_needs_declared_permissions(world):
    with pytest.raises(AgentPermissionError):
        ToolAccess(build_tool_registry(projects=world.projects), ECHO, world.project.project_id)


def test_agent_uses_tools_through_invoke(repo_world, monkeypatch):
    """End to end: AgentRegistry.invoke -> Agent.run -> ToolAccess -> ToolRegistry.invoke."""
    w = repo_world

    def behavior(agent, req, ctx):
        r = ctx.require_tools().invoke("git.status", {"project_id": ctx.project_id})
        if not r.success:
            return AgentResult.waiting(agent.agent_id, ctx.task_id, {"blocked": r.error.code})
        return AgentResult.completed(agent.agent_id, ctx.task_id, {"message": f"clean={r.data['clean']}"})

    allow_probe(monkeypatch, behavior)
    reg = AgentRegistry()
    d = reg.register(definition(tool_permissions=TOOL_PERMS))
    denied = build_tool_registry(projects=w.projects, git=GitWorkspaceManager(w.projects))
    r = reg.invoke("agent.probe", request(w), ctx_for(w, d, tools=denied))
    assert r.status.value == "waiting" and r.output["blocked"] == "tool_permission_denied"
    granted = build_tool_registry(projects=w.projects, git=GitWorkspaceManager(w.projects),
                                  policy=PermissionPolicy({ActorType.AGENT: READ_ONLY}))
    r = reg.invoke("agent.probe", request(w), ctx_for(w, d, tools=granted))
    assert r.success and r.output["message"] == "clean=True"


def test_missing_service_maps_to_unavailable(world, monkeypatch):
    def behavior(agent, req, ctx):
        ctx.require_models()

    allow_probe(monkeypatch, behavior)
    reg = AgentRegistry()
    d = reg.register(definition(model_requirements={TG}))
    r = reg.invoke("agent.probe", request(world), ctx_for(world, d))  # no router supplied
    assert r.error.code == AgentErrorCode.AGENT_MODEL_UNAVAILABLE


def test_sqlite_connection_is_unreachable_from_public_fields(world):
    ctx = ctx_for(world)
    seen = [getattr(ctx, name) for name in AgentContext.__slots__]
    assert not any(isinstance(v, (Database, sqlite3.Connection)) for v in seen)
