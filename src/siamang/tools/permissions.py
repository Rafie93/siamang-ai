"""Permission model: deny by default.

A caller describes itself with a :class:`ToolContext` (actor, requested
permissions, project scope). The effective permissions are the context's
permissions **intersected** with the code-owned :class:`PermissionPolicy`
ceiling for that actor type, so a caller cannot grant itself more than the
policy allows. Anything not explicitly granted is denied.

There is no authentication, user database, role system, or token here.
Phase 09 only establishes the boundary that later phases (Task Engine,
agents, API) must pass through.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from siamang.contracts.schema import ID_RE
from siamang.projects.definition import PROJECT_ID_RE
from siamang.tools.errors import ToolContextError


class Permission(StrEnum):
    TOOL_LIST = "tool.list"  # discover which tools exist
    TOOL_INSPECT = "tool.inspect"  # read one tool's definition
    TOOL_INVOKE = "tool.invoke"  # required for every invocation
    PROJECT_READ = "project.read"
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    GIT_READ = "git.read"
    GIT_WRITE = "git.write"
    NETWORK = "network"
    PROCESS_EXECUTE = "process.execute"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"


# Capabilities no tool may declare in Phase 09 (the registry refuses such definitions).
PHASE09_FORBIDDEN = frozenset(
    {
        Permission.WORKSPACE_WRITE,
        Permission.GIT_WRITE,
        Permission.NETWORK,
        Permission.PROCESS_EXECUTE,
        Permission.FILESYSTEM_WRITE,
    }
)

DISCOVERY = frozenset({Permission.TOOL_LIST, Permission.TOOL_INSPECT})
READ_ONLY = DISCOVERY | {Permission.TOOL_INVOKE, Permission.PROJECT_READ, Permission.WORKSPACE_READ,
                         Permission.GIT_READ}


class ActorType(StrEnum):
    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"
    TASK = "task"


def parse_permissions(values: Iterable[Permission | str]) -> frozenset[Permission]:
    if isinstance(values, (str, bytes)):
        raise ToolContextError("permissions must be a collection, not a string")
    out = set()
    for v in values:
        try:
            out.add(Permission(v))
        except ValueError:
            raise ToolContextError(f"unknown permission {v!r}") from None
    return frozenset(out)


@dataclass(frozen=True)
class ToolContext:
    """Who is calling, what they ask to be allowed, and which project they are scoped to.

    ``project_id`` is the single project this caller may touch. ``None`` means
    no project scope, so project-scoped tools are denied.
    """

    actor_type: ActorType
    actor_id: str
    permissions: frozenset[Permission] = frozenset()
    project_id: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "actor_type", ActorType(self.actor_type))
        except ValueError:
            raise ToolContextError(f"unknown actor type {self.actor_type!r}") from None
        if not isinstance(self.actor_id, str) or not ID_RE.fullmatch(self.actor_id):
            raise ToolContextError("actor_id must be a safe identifier")
        object.__setattr__(self, "permissions", parse_permissions(self.permissions))
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id)
        ):
            raise ToolContextError("project_id must look like prj_<id>")


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    missing: frozenset[Permission] = frozenset()


class PermissionPolicy:
    """Code-owned ceilings per actor type. An actor type with no entry gets nothing."""

    def __init__(self, ceilings: Mapping[ActorType | str, Iterable[Permission | str]]):
        self._ceilings: Mapping[ActorType, frozenset[Permission]] = MappingProxyType(
            {ActorType(k): parse_permissions(v) for k, v in ceilings.items()}
        )

    def ceiling(self, actor_type: ActorType) -> frozenset[Permission]:
        return self._ceilings.get(actor_type, frozenset())

    def effective(self, context: ToolContext) -> frozenset[Permission]:
        return context.permissions & self.ceiling(context.actor_type)

    def evaluate(self, context: ToolContext, required: Iterable[Permission]) -> PermissionDecision:
        required = frozenset(required)
        if not required:  # nothing to check would be a silent allow; refuse instead
            return PermissionDecision(False)
        missing = required - self.effective(context)
        return PermissionDecision(not missing, frozenset(missing))

    def to_dict(self) -> dict[str, list[str]]:
        return {str(a): sorted(str(p) for p in self.ceiling(a)) for a in ActorType}


# Phase 09 policy. Agents and tasks do not exist yet, so they get nothing;
# Phase 10/11 grants them explicitly.
DEFAULT_POLICY = PermissionPolicy(
    {
        ActorType.SYSTEM: READ_ONLY,
        ActorType.USER: READ_ONLY,
        ActorType.AGENT: frozenset(),
        ActorType.TASK: frozenset(),
    }
)
