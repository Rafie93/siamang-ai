"""Read-only Git tools: definitions and their SIAMANG-owned adapter.

::

    ToolRegistry ──► GitToolAdapter ──► GitWorkspaceManager ──► GitClient ──► git

The adapter calls :class:`GitWorkspaceManager` only. ``GitClient`` (and its
``run(args)``) is never reachable from a tool: each tool is one fixed,
read-only capability with no free-form arguments.

The adapter receives a :class:`Project` that the registry has already
resolved and checked (exists, active, in scope). It never takes a path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from siamang.contracts.schema import F
from siamang.git import GitError, GitWorkspaceManager, ProtectedRepositoryError
from siamang.projects import Project
from siamang.tools.definition import ToolDefinition, ToolOperation
from siamang.tools.errors import ToolError, ToolExecutionError, ToolScopeError
from siamang.tools.permissions import Permission

_PROJECT = {"project_id": F("id", required=True)}
_READ = frozenset({Permission.PROJECT_READ, Permission.WORKSPACE_READ, Permission.GIT_READ})

_HEAD = F("object", required=True, fields={
    "commit": F("text", required=True, nullable=True),
    "short_commit": F("text", required=True, nullable=True),
    "branch": F("text", required=True, nullable=True),
    "detached": F("bool", required=True),
    "unborn": F("bool", required=True),
})
_STATUS_FIELDS = {
    "clean": F("bool", required=True),
    "head": _HEAD,
    "upstream": F("text", required=True, nullable=True),
    "ahead": F("int", required=True, nullable=True, min=0),
    "behind": F("int", required=True, nullable=True, min=0),
    "counts": F("object", required=True),
    "entries": F("list", required=True, item=F("object", fields={
        "path": F("text", required=True),
        "state": F("enum", required=True,
                   choices=("modified", "added", "deleted", "renamed", "untracked", "conflicted")),
    })),
}
_DIFF_FIELDS = {
    "cached": F("bool", required=True),
    "files": F("list", required=True, item=F("object", fields={"path": F("text", required=True)})),
    "patch": F("text", required=True),
    "truncated": F("bool", required=True),
}


def _tool(tool_id: str, name: str, description: str, input_schema: Mapping[str, F],
          output_schema: Mapping[str, F]) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        name=name,
        description=description,
        version="1",
        category="git",
        operation=ToolOperation.READ,
        implementation=tool_id,
        capabilities=_READ,
        input_schema=input_schema,
        output_schema=output_schema,
        requires_project=True,
        requires_workspace=True,
        mutating=False,
        enabled=True,
        metadata={"network": False, "phase": "09"},
    )


GIT_TOOLS: tuple[ToolDefinition, ...] = (
    _tool("git.inspect", "Git Inspect",
          "Repository location, HEAD, branch, working-tree summary, and remotes (credentials removed)",
          _PROJECT, {
              "project_id": F("id", required=True),
              "location": F("object", required=True, fields={
                  "project_root": F("text", required=True),
                  "repository_root": F("text", required=True),
                  "relation": F("enum", required=True, choices=("root", "nested")),
              }),
              "head": _HEAD,
              "status": F("object", required=True, fields=_STATUS_FIELDS),
              "remotes": F("list", required=True, item=F("object", fields={"name": F("text", required=True)})),
          }),
    _tool("git.status", "Git Status", "Working-tree status of the whole repository", _PROJECT, _STATUS_FIELDS),
    _tool("git.diff", "Git Diff", "Unstaged diff (work tree vs index); cached=true gives the staged diff",
          {**_PROJECT, "cached": F("bool")}, _DIFF_FIELDS),
    _tool("git.diff_cached", "Git Diff (staged)", "Staged diff (index vs HEAD)", _PROJECT, _DIFF_FIELDS),
    _tool("git.worktrees", "Git Worktrees", "Existing Git worktrees (discovery only)", _PROJECT, {
        "worktrees": F("list", required=True, item=F("object", fields={
            "workspace_id": F("text", required=True),
            "path": F("text", required=True),
            "kind": F("enum", required=True, choices=("main", "linked", "bare")),
        })),
    }),
)


class GitToolAdapter:
    """The only Git tool implementations. Methods take ``(project, args)`` and return JSON data."""

    def __init__(self, manager: GitWorkspaceManager):
        if not isinstance(manager, GitWorkspaceManager):
            raise TypeError("GitToolAdapter needs a GitWorkspaceManager")
        self._manager = manager

    def bindings(self) -> dict[str, Callable[[Project, Mapping[str, Any]], dict[str, Any]]]:
        return {
            "git.inspect": self.inspect,
            "git.status": self.status,
            "git.diff": self.diff,
            "git.diff_cached": self.diff_cached,
            "git.worktrees": self.worktrees,
        }

    def inspect(self, project: Project, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._guard(lambda: self._manager.inspect(project).to_dict())

    def status(self, project: Project, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._guard(lambda: self._manager.status(project).to_dict())

    def diff(self, project: Project, args: Mapping[str, Any]) -> dict[str, Any]:
        cached = args.get("cached", False) is True
        return self._guard(lambda: self._manager.diff(project, cached=cached).to_dict())

    def diff_cached(self, project: Project, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._guard(lambda: self._manager.diff(project, cached=True).to_dict())

    def worktrees(self, project: Project, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._guard(lambda: {"worktrees": [w.to_dict() for w in self._manager.worktrees(project)]})

    @staticmethod
    def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        failure: ToolError | None = None
        try:
            return call()
        except ProtectedRepositoryError as exc:
            failure = ToolScopeError("project resolves to a protected repository",
                                     cause=str(exc.code))
        except GitError as exc:
            failure = ToolExecutionError(f"git operation failed: {exc.code}", cause=str(exc.code),
                                         retryable=exc.retryable)
        raise failure  # outside `except`: the GitError is not chained
