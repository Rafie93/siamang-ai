"""Permission model: explicit context, code-owned ceilings, deny by default."""

import pytest

from siamang.tools import DEFAULT_POLICY, ActorType, Permission, PermissionPolicy, ToolContext
from siamang.tools.errors import ToolContextError
from siamang.tools.permissions import PHASE09_FORBIDDEN, READ_ONLY

PID = "prj_01JABCDEFGHJKMNPQRSTVWXYZ0"
GIT_READ_SET = {Permission.TOOL_INVOKE, Permission.PROJECT_READ, Permission.WORKSPACE_READ, Permission.GIT_READ}


def test_permission_vocabulary():
    assert {str(p) for p in Permission} == {
        "tool.list", "tool.inspect", "tool.invoke", "project.read", "workspace.read", "workspace.write", "git.read",
        "git.write", "network", "process.execute", "filesystem.read", "filesystem.write"}


def test_context_parses_and_freezes():
    ctx = ToolContext("user", "rafie", ["git.read", Permission.TOOL_INVOKE], project_id=PID)
    assert ctx.actor_type is ActorType.USER
    assert ctx.permissions == {Permission.GIT_READ, Permission.TOOL_INVOKE}
    assert isinstance(ctx.permissions, frozenset)


@pytest.mark.parametrize("kwargs", [
    {"actor_type": "root", "actor_id": "x"},
    {"actor_type": "admin", "actor_id": "x"},
    {"actor_type": "user", "actor_id": ""},
    {"actor_type": "user", "actor_id": "a b"},
    {"actor_type": "user", "actor_id": "x\n"},
    {"actor_type": "user", "actor_id": "$(id)"},
    {"actor_type": "user", "actor_id": 7},
    {"actor_type": "user", "actor_id": "x", "permissions": ["sudo"]},
    {"actor_type": "user", "actor_id": "x", "permissions": "git.read"},
    {"actor_type": "user", "actor_id": "x", "project_id": "../x"},
    {"actor_type": "user", "actor_id": "x", "project_id": "prj_x\n"},
    {"actor_type": "user", "actor_id": "x", "project_id": 1},
])
def test_invalid_contexts(kwargs):
    with pytest.raises(ToolContextError):
        ToolContext(**kwargs)


def test_effective_is_intersection_with_ceiling():
    policy = PermissionPolicy({"user": {"git.read", "tool.invoke"}})
    ctx = ToolContext("user", "u", set(Permission))  # asks for everything
    assert policy.effective(ctx) == {Permission.GIT_READ, Permission.TOOL_INVOKE}


def test_deny_by_default():
    policy = PermissionPolicy({})
    ctx = ToolContext("system", "s", set(Permission))
    assert not policy.evaluate(ctx, {Permission.TOOL_INVOKE}).allowed
    assert not DEFAULT_POLICY.evaluate(ToolContext("user", "u"), {Permission.GIT_READ}).allowed  # nothing requested


def test_empty_requirement_is_not_a_silent_allow():
    assert not DEFAULT_POLICY.evaluate(ToolContext("system", "s", READ_ONLY), set()).allowed


def test_missing_permissions_are_reported():
    d = DEFAULT_POLICY.evaluate(ToolContext("user", "u", {"tool.invoke"}), GIT_READ_SET)
    assert not d.allowed
    assert d.missing == GIT_READ_SET - {Permission.TOOL_INVOKE}


def test_allowed():
    assert DEFAULT_POLICY.evaluate(ToolContext("system", "s", GIT_READ_SET), GIT_READ_SET).allowed


@pytest.mark.parametrize("actor", ["agent", "task"])
def test_agents_and_tasks_get_nothing_in_phase09(actor):
    ctx = ToolContext(actor, "a1", set(Permission), project_id=PID)
    assert DEFAULT_POLICY.effective(ctx) == frozenset()
    assert not DEFAULT_POLICY.evaluate(ctx, GIT_READ_SET).allowed


def test_default_policy_grants_no_write_network_or_process():
    for actor in ActorType:
        assert not DEFAULT_POLICY.ceiling(actor) & PHASE09_FORBIDDEN, actor
        assert Permission.FILESYSTEM_READ not in DEFAULT_POLICY.ceiling(actor)
    assert DEFAULT_POLICY.to_dict()["system"] == sorted(str(p) for p in READ_ONLY)


def test_policy_rejects_unknown_actor_or_permission():
    with pytest.raises(ValueError):
        PermissionPolicy({"root": {"git.read"}})
    with pytest.raises(ToolContextError):
        PermissionPolicy({"user": {"everything"}})


def test_policy_is_not_mutable():
    with pytest.raises(TypeError):
        DEFAULT_POLICY._ceilings[ActorType.AGENT] = frozenset(Permission)
