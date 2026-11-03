"""ToolRegistry: catalog, discovery permissions, and the invocation boundary (real temp repos)."""

import dataclasses
import json

import pytest

from siamang.git import GitWorkspaceManager
from siamang.projects import ProjectRegistry
from siamang.tools import (
    DEFAULT_POLICY,
    ActorType,
    Permission,
    PermissionPolicy,
    ToolContext,
    ToolErrorCode,
    ToolRegistry,
    build_tool_registry,
)
from siamang.tools.errors import (
    ToolDefinitionError,
    ToolDuplicateError,
    ToolImplementationNotFoundError,
    ToolNotFoundError,
    ToolPermissionError,
)
from siamang.tools.git_tools import GIT_TOOLS
from siamang.tools.implementations import ImplementationTable
from siamang.tools.permissions import READ_ONLY
from tests.support.git import make_repo

GIT_TOOL_IDS = ["git.diff", "git.diff_cached", "git.inspect", "git.status", "git.worktrees"]


@pytest.fixture
def projects(db):
    return ProjectRegistry(db)


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def project(projects, repo):
    return projects.register(name="Repo", root_path=repo.root)


@pytest.fixture
def tools(projects):
    return build_tool_registry(projects=projects, git=GitWorkspaceManager(projects))


def ctx(project=None, actor="system", perms=READ_ONLY):
    pid = project.project_id if hasattr(project, "project_id") else project
    return ToolContext(actor, "test", perms, project_id=pid)


def call(tools, tool_id, project, extra=None, context=None):
    return tools.invoke(tool_id, context or ctx(project), {"project_id": project.project_id, **(extra or {})})


def code(result):
    assert not result.success and result.data is None
    return result.error.code


# --- catalog ---------------------------------------------------------------------------


def test_exactly_the_five_read_only_git_tools():
    tools = build_tool_registry()
    listed = tools.list_tools(ctx())
    assert [t.tool_id for t in listed] == GIT_TOOL_IDS
    for t in listed:
        assert t.enabled and not t.mutating and str(t.operation) == "read"
        assert t.requires_project and t.requires_workspace and t.category == "git"
        assert t.metadata["network"] is False
        assert t.capabilities == {Permission.PROJECT_READ, Permission.WORKSPACE_READ, Permission.GIT_READ}


def test_get_tool(tools):
    assert tools.get_tool("git.status", ctx()).name == "Git Status"
    with pytest.raises(ToolNotFoundError):
        tools.get_tool("git.push", ctx())
    with pytest.raises(ToolNotFoundError):
        tools.get_tool(None, ctx())


def test_discovery_needs_permissions(tools):
    for perms, ok_list, ok_get in [
        ({Permission.TOOL_LIST}, True, False),
        ({Permission.TOOL_INSPECT}, False, True),
        (set(), False, False),
    ]:
        c = ToolContext("system", "s", perms)
        if ok_list:
            tools.list_tools(c)
        else:
            with pytest.raises(ToolPermissionError):
                tools.list_tools(c)
        if ok_get:
            tools.get_tool("git.status", c)
        else:
            with pytest.raises(ToolPermissionError):
                tools.get_tool("git.status", c)
    with pytest.raises(ToolPermissionError):
        tools.list_tools(ToolContext("agent", "a", {Permission.TOOL_LIST}))


def test_duplicate_registration(tools):
    with pytest.raises(ToolDuplicateError) as info:
        tools.register(GIT_TOOLS[0])
    assert info.value.code is ToolErrorCode.TOOL_DUPLICATE


def test_register_requires_a_definition():
    with pytest.raises(ToolDefinitionError):
        ToolRegistry().register({"tool_id": "git.status"})


def test_register_unknown_implementation():
    d = dataclasses.replace(GIT_TOOLS[1], tool_id="git.status_v2", implementation="git.nope")
    with pytest.raises(ToolImplementationNotFoundError):
        ToolRegistry().register(d)


def test_register_refuses_mutating_and_write_capabilities():
    status = next(t for t in GIT_TOOLS if t.tool_id == "git.status")
    for changes in ({"operation": "write", "mutating": True}, {"capabilities": {Permission.GIT_WRITE}},
                    {"capabilities": {Permission.NETWORK}}, {"capabilities": {Permission.PROCESS_EXECUTE}}):
        with pytest.raises(ToolDefinitionError):
            ToolRegistry().register(dataclasses.replace(status, tool_id="git.other", **changes))


def test_registry_constructor_types():
    with pytest.raises(TypeError):
        ToolRegistry(policy={"system": READ_ONLY})
    with pytest.raises(TypeError):
        ToolRegistry(implementations={"git.status": print})


# --- invocation: success -------------------------------------------------------------------


def test_inspect(tools, project, repo):
    r = call(tools, "git.inspect", project)
    assert r.success and r.error is None
    assert r.data["project_id"] == project.project_id
    assert r.data["head"]["commit"] == repo.head() and r.data["head"]["branch"] == "main"
    assert r.data["location"]["repository_root"] == repo.root
    assert r.metadata["project_id"] == project.project_id and r.metadata["actor"] == "system:test"
    assert r.metadata["tool_version"] == "1"
    assert json.loads(json.dumps(r.to_dict())) == r.to_dict()


def test_status(tools, project, repo):
    assert call(tools, "git.status", project).data["clean"] is True
    repo.write("new file.txt")
    data = call(tools, "git.status", project).data
    assert not data["clean"] and data["entries"][0]["path"] == "new file.txt"


def test_diff_and_diff_cached(tools, project, repo):
    repo.write("README.md", "unstaged\n")
    repo.write("staged.txt", "s\n")
    repo.git("add", "staged.txt")
    unstaged = call(tools, "git.diff", project).data
    staged = call(tools, "git.diff_cached", project).data
    via_flag = call(tools, "git.diff", project, {"cached": True}).data
    assert [f["path"] for f in unstaged["files"]] == ["README.md"] and not unstaged["cached"]
    assert [f["path"] for f in staged["files"]] == ["staged.txt"] and staged["cached"]
    assert via_flag == staged
    assert call(tools, "git.diff", project, {"cached": False}).data == unstaged


def test_worktrees(tools, project, repo, tmp_path):
    repo.git("worktree", "add", "-q", "-b", "feature", str(tmp_path / "wt"))
    data = call(tools, "git.worktrees", project).data
    assert [w["kind"] for w in data["worktrees"]] == ["main", "linked"]
    assert data["worktrees"][0]["current"] is True


# --- invocation: denials (every one is structured, none raises) ----------------------------


def test_unknown_tool(tools, project):
    for tid in ("git.push", "git.run", "", None, 42):
        r = tools.invoke(tid, ctx(project), {"project_id": project.project_id})
        assert code(r) is ToolErrorCode.TOOL_NOT_FOUND


def test_disabled_tool(projects, project):
    status = next(t for t in GIT_TOOLS if t.tool_id == "git.status")
    reg = ToolRegistry(projects=projects, implementations=ImplementationTable.build(git=GitWorkspaceManager(projects)))
    reg.register(dataclasses.replace(status, tool_id="git.status_off", enabled=False))
    assert code(call(reg, "git.status_off", project)) is ToolErrorCode.TOOL_DISABLED


@pytest.mark.parametrize("bad", [None, {}, {"actor_type": "system"}, "system"])
def test_invalid_context(tools, project, bad):
    r = tools.invoke("git.status", bad, {"project_id": project.project_id})
    assert code(r) is ToolErrorCode.TOOL_CONTEXT_INVALID
    assert r.metadata["actor"] == "<invalid>"


def test_input_invalid_and_project_required(tools, project):
    assert code(tools.invoke("git.status", ctx(project), {})) is ToolErrorCode.TOOL_PROJECT_REQUIRED
    for bad in ({"project_id": project.project_id, "argv": ["push"]}, {"project_id": "prj_x; push"}, None,
                {"project_id": project.project_id, "cached": True}):
        assert code(tools.invoke("git.status", ctx(project), bad)) is ToolErrorCode.TOOL_INPUT_INVALID


def test_scope_denied(tools, project, projects, tmp_path):
    other = projects.register(name="Other", root_path=make_repo(tmp_path / "other").root)
    assert code(call(tools, "git.status", project, context=ctx(None))) is ToolErrorCode.TOOL_SCOPE_DENIED
    assert code(call(tools, "git.status", project, context=ctx(other))) is ToolErrorCode.TOOL_SCOPE_DENIED
    assert call(tools, "git.status", other, context=ctx(other)).success


@pytest.mark.parametrize("missing", ["tool.invoke", "project.read", "workspace.read", "git.read"])
def test_each_required_permission_is_needed(tools, project, missing):
    perms = {p for p in READ_ONLY if str(p) != missing}
    r = call(tools, "git.status", project, context=ctx(project, perms=perms))
    assert code(r) is ToolErrorCode.TOOL_PERMISSION_DENIED
    assert r.error.details == (missing,)


@pytest.mark.parametrize("actor", ["agent", "task"])
def test_agent_and_task_actors_are_denied(tools, project, actor):
    r = call(tools, "git.status", project, context=ctx(project, actor=actor, perms=set(Permission)))
    assert code(r) is ToolErrorCode.TOOL_PERMISSION_DENIED


def test_custom_policy_applies_to_agents_the_same_way(projects, project):
    policy = PermissionPolicy({ActorType.AGENT: READ_ONLY})
    reg = build_tool_registry(projects=projects, git=GitWorkspaceManager(projects), policy=policy)
    assert call(reg, "git.status", project, context=ctx(project, actor="agent")).success
    r = call(reg, "git.status", project, context=ctx(project, actor="agent", perms={Permission.TOOL_INVOKE}))
    assert code(r) is ToolErrorCode.TOOL_PERMISSION_DENIED


def test_permission_checked_before_project_lookup(tools):
    pid = "prj_doesnotexist"
    r = tools.invoke("git.status", ctx(pid, perms=set()), {"project_id": pid})
    assert code(r) is ToolErrorCode.TOOL_PERMISSION_DENIED  # not tool_project_not_found: no probing


def test_project_not_found(tools):
    pid = "prj_doesnotexist"
    assert code(tools.invoke("git.status", ctx(pid), {"project_id": pid})) is ToolErrorCode.TOOL_PROJECT_NOT_FOUND


def test_archived_project_denied_and_not_revived(tools, projects, project):
    projects.archive(project.project_id)
    for tid in GIT_TOOL_IDS:
        assert code(call(tools, tid, project)) is ToolErrorCode.TOOL_PROJECT_ARCHIVED
    assert not projects.get(project.project_id).is_active


def test_no_project_registry(project):
    reg = build_tool_registry(git=None)
    assert code(call(reg, "git.status", project)) is ToolErrorCode.TOOL_PROJECT_NOT_FOUND


def test_implementation_not_bound(projects, project):
    reg = build_tool_registry(projects=projects)  # catalog only, no Git service
    assert code(call(reg, "git.status", project)) is ToolErrorCode.TOOL_IMPLEMENTATION_NOT_FOUND


def test_execution_failure_is_normalized(tools, projects, tmp_path):
    (tmp_path / "plain").mkdir()
    p = projects.register(name="Plain", root_path=str(tmp_path / "plain"))
    r = call(tools, "git.status", p)
    assert code(r) is ToolErrorCode.TOOL_EXECUTION_FAILED
    assert r.error.cause == "not_repository" and r.error.tool_id == "git.status"
    assert str(tmp_path) not in json.dumps(r.to_dict())
    assert r.error.__context__ is None and r.error.__cause__ is None


def test_protected_repository_is_scope_denied(projects, project, repo):
    manager = GitWorkspaceManager(projects, protected={"other": repo.head()})
    reg = build_tool_registry(projects=projects, git=manager)
    r = call(reg, "git.inspect", project)
    assert code(r) is ToolErrorCode.TOOL_SCOPE_DENIED and r.error.cause == "protected_repository"


def test_unexpected_exception_does_not_leak(tools, project, monkeypatch):
    def boom(self, ref):
        raise RuntimeError("secret ghp_LEAK at /Users/x/.ssh/id_rsa")

    monkeypatch.setattr(GitWorkspaceManager, "status", boom)
    r = call(tools, "git.status", project)
    assert code(r) is ToolErrorCode.TOOL_EXECUTION_FAILED and r.error.cause == "RuntimeError"
    assert "ghp_LEAK" not in json.dumps(r.to_dict()) and ".ssh" not in json.dumps(r.to_dict())
    assert "Traceback" not in json.dumps(r.to_dict())


def test_result_contract_is_enforced(tools, project, monkeypatch):
    class Bad:
        def to_dict(self):
            return {"clean": "yes"}

    monkeypatch.setattr(GitWorkspaceManager, "status", lambda self, ref: Bad())
    r = call(tools, "git.status", project)
    assert code(r) is ToolErrorCode.TOOL_RESULT_INVALID
    assert any("result.clean" in d for d in r.error.details)


def test_invoke_never_raises_for_tool_errors(tools, project):
    for args in [("nope", None, None), ("git.status", None, None), ("git.status", ctx(project), "x")]:
        assert tools.invoke(*args).success is False


def test_default_policy_is_used_by_default():
    assert build_tool_registry().policy is DEFAULT_POLICY
