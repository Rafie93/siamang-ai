"""AgentContext: the only things an agent can reach, as explicit typed fields.

::

    AgentContext
      ├── agent_id
      ├── task         Task snapshot (frozen domain object, from the Task Engine)
      ├── project      AgentProject: id, name, type, description, metadata (no root_path)
      ├── models       ModelAccess | None  ──► ModelRouter.select ──► Selection.generate
      ├── tools        ToolAccess  | None  ──► ToolRegistry.invoke ──► PermissionPolicy
      ├── permissions  effective tool permissions (declared ∩ policy ceiling for "agent")
      └── metadata

There is no service locator, no ``get(name)``, and no database, registry,
GitClient, filesystem, environment, or network handle. ``AgentContext``
refuses any other object in these fields, including subclasses of the
facades, and uses ``__slots__`` so no attribute can be added later.

Project identity comes from the Project Registry: :func:`build_context`
resolves the task's project through :class:`ProjectRegistry` (it must exist
and be active) and hands the agent a narrow view. Workspace access is only
possible through tools, so the agent never sees ``root_path``.

This is an in-process boundary, not a sandbox: it holds against mistakes and
against agent code that keeps to the rules enforced by the architecture and
security tests (no private-attribute access, no forbidden imports).
"""

from __future__ import annotations

from siamang.jsondata import thaw

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.agents.definition import AGENT_ID_RE, AgentDefinition, canonical
from siamang.agents.errors import (
    AgentContextError,
    AgentInputError,
    AgentModelUnavailableError,
    AgentPermissionError,
    AgentToolUnavailableError,
)
from siamang.models.errors import ModelError
from siamang.models.request import ModelRequest
from siamang.models.response import ModelResponse
from siamang.models.router import ModelRouter
from siamang.projects import Project, ProjectNotFoundError, ProjectRegistry
from siamang.projects.definition import freeze_metadata
from siamang.tasks.definition import Task
from siamang.tools.errors import ToolError
from siamang.tools.permissions import ActorType, Permission, ToolContext
from siamang.tools.registry import ToolRegistry, ToolResult


@dataclass(frozen=True, slots=True)
class AgentProject:
    """What an agent may know about its project. No filesystem location."""

    project_id: str
    name: str
    project_type: str
    description: str
    metadata: Mapping[str, Any]

    @classmethod
    def of(cls, project: Project) -> AgentProject:
        return cls(project.project_id, project.name, str(project.project_type), project.description,
                   project.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "name": self.name, "project_type": self.project_type,
                "description": self.description, "metadata": thaw(self.metadata)}


class ModelAccess:
    """Model access for one agent: through the ModelRouter only.

    * the router selects; an agent may not pin ``model_id``;
    * a request may only need capabilities the agent declared in
      ``model_requirements``;
    * every :class:`ModelError` becomes ``agent_model_unavailable`` with the
      model error code in ``cause`` (no vendor detail, no credentials).
    """

    __slots__ = ("_router", "_agent_id", "_allowed")

    def __init__(self, router: ModelRouter, definition: AgentDefinition):
        if not isinstance(router, ModelRouter):
            raise AgentContextError("model access needs a ModelRouter", agent_id=definition.agent_id)
        if not definition.uses_models:
            raise AgentPermissionError("agent declares no model requirements", agent_id=definition.agent_id)
        self._router = router
        self._agent_id = definition.agent_id
        self._allowed = definition.model_requirements

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def capabilities(self) -> frozenset:
        return self._allowed

    def generate(self, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise AgentInputError("model request must be a ModelRequest", agent_id=self._agent_id)
        if request.model_id is not None:
            raise AgentPermissionError("agents may not pin a model; the ModelRouter selects",
                                       agent_id=self._agent_id)
        undeclared = request.effective_capabilities - self._allowed
        if undeclared:
            raise AgentPermissionError("model request needs capabilities the agent did not declare",
                                       agent_id=self._agent_id, details=tuple(sorted(map(str, undeclared))))
        failure: ModelError | None = None
        try:
            return self._router.select(request).generate(request)
        except ModelError as exc:
            failure = exc
        raise AgentModelUnavailableError("model request failed", agent_id=self._agent_id, cause=str(failure.code),
                                         retryable=failure.retryable)


class ToolAccess:
    """Tool access for one agent, scoped to one project: through ToolRegistry.invoke only.

    The ToolContext is built here, not by the agent: actor type ``agent``,
    actor ID = agent ID, the agent's *declared* permissions, the context's
    project. The registry then intersects with the PermissionPolicy ceiling
    (Phase 09). ``DEFAULT_POLICY`` grants ``agent`` nothing, so every call
    is denied until a policy grants it.
    """

    __slots__ = ("_registry", "_context")

    def __init__(self, registry: ToolRegistry, definition: AgentDefinition, project_id: str):
        if not isinstance(registry, ToolRegistry):
            raise AgentContextError("tool access needs a ToolRegistry", agent_id=definition.agent_id)
        if not definition.uses_tools:
            raise AgentPermissionError("agent declares no tool permissions", agent_id=definition.agent_id)
        try:
            self._context = ToolContext(ActorType.AGENT, definition.agent_id, definition.tool_permissions, project_id)
        except ToolError:
            raise AgentContextError("invalid tool scope", agent_id=definition.agent_id) from None
        self._registry = registry

    @property
    def agent_id(self) -> str:
        return self._context.actor_id

    @property
    def project_id(self) -> str | None:
        return self._context.project_id

    @property
    def permissions(self) -> frozenset[Permission]:
        """Effective permissions: declared ∩ the policy ceiling for actor type ``agent``."""
        return self._registry.policy.effective(self._context)

    def invoke(self, tool_id: str, input_data: Any) -> ToolResult:
        """Exactly ``ToolRegistry.invoke``: validation, scope, permission, project checks all apply."""
        return self._registry.invoke(tool_id, self._context, input_data)


@dataclass(frozen=True, slots=True)
class AgentContext:
    agent_id: str
    task: Task
    project: AgentProject
    models: ModelAccess | None = None
    tools: ToolAccess | None = None
    permissions: frozenset[Permission] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        aid = self.agent_id if isinstance(self.agent_id, str) else None
        if aid is None or not AGENT_ID_RE.fullmatch(aid):
            raise AgentContextError("agent_id must look like agent.<role>")
        if type(self.task) is not Task:
            raise AgentContextError("task must be a Task snapshot", agent_id=aid)
        if type(self.project) is not AgentProject:
            raise AgentContextError("project must be an AgentProject", agent_id=aid)
        if self.task.project_id != self.project.project_id:
            raise AgentContextError("task belongs to a different project", agent_id=aid)
        if self.models is not None and (type(self.models) is not ModelAccess or self.models.agent_id != aid):
            raise AgentContextError("models must be this agent's ModelAccess", agent_id=aid)
        if self.tools is not None and (
            type(self.tools) is not ToolAccess or self.tools.agent_id != aid
            or self.tools.project_id != self.project.project_id
        ):
            raise AgentContextError("tools must be this agent's ToolAccess for this project", agent_id=aid)
        if not isinstance(self.permissions, frozenset) or not all(type(p) is Permission for p in self.permissions):
            raise AgentContextError("permissions must be a frozenset of Permission", agent_id=aid)
        granted = self.tools.permissions if self.tools is not None else frozenset()
        if self.permissions != granted:  # informational, but never a claim the policy did not grant
            raise AgentContextError("permissions must equal what the policy grants", agent_id=aid)
        metadata = canonical(self.metadata, "context.metadata", aid, AgentContextError)
        object.__setattr__(self, "metadata", freeze_metadata(metadata))

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def project_id(self) -> str:
        return self.project.project_id

    def require_models(self) -> ModelAccess:
        if self.models is None:
            raise AgentModelUnavailableError("no model access in this context", agent_id=self.agent_id)
        return self.models

    def require_tools(self) -> ToolAccess:
        if self.tools is None:
            raise AgentToolUnavailableError("no tool access in this context", agent_id=self.agent_id)
        return self.tools


def build_context(
    definition: AgentDefinition,
    *,
    task: Task,
    projects: ProjectRegistry,
    router: ModelRouter | None = None,
    tools: ToolRegistry | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AgentContext:
    """Assemble a context for ``definition`` working on ``task``.

    The project is resolved through the Project Registry (must exist and be
    active). Model/tool access is attached only if the agent declares a need
    *and* the caller supplies the service; an undeclared need gets nothing.
    """
    if not isinstance(definition, AgentDefinition):
        raise AgentContextError("definition must be an AgentDefinition")
    aid = definition.agent_id
    if type(task) is not Task:
        raise AgentContextError("task must be a Task snapshot", agent_id=aid)
    if not isinstance(projects, ProjectRegistry):
        raise AgentContextError("projects must be the ProjectRegistry", agent_id=aid)
    failure: AgentContextError | None = None
    try:
        project = projects.get(task.project_id)
    except ProjectNotFoundError:
        failure = AgentContextError("task's project is not registered", agent_id=aid)
    if failure is not None:
        raise failure
    if not project.is_active:
        raise AgentContextError("task's project is archived", agent_id=aid)
    model_access = ModelAccess(router, definition) if router is not None and definition.uses_models else None
    tool_access = (ToolAccess(tools, definition, project.project_id)
                   if tools is not None and definition.uses_tools else None)
    return AgentContext(
        agent_id=aid,
        task=task,
        project=AgentProject.of(project),
        models=model_access,
        tools=tool_access,
        permissions=tool_access.permissions if tool_access else frozenset(),
        metadata=metadata or {},
    )
