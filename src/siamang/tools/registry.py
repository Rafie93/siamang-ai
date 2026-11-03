"""Tool Registry: the single declaration, authorization, and invocation boundary.

::

    caller (future Task Engine / agent)
      │  invoke(tool_id, context, input)
      ▼
    ToolRegistry ── permission policy (deny by default)
      │          ── ProjectRegistry (project exists, active, in scope)
      ▼
    ImplementationTable (allowlisted adapter methods only)
      ▼
    GitToolAdapter ──► GitWorkspaceManager ──► GitClient ──► git

Invocation order (every step can only deny):

 1. resolve the tool                       -> tool_not_found
 2. tool enabled                           -> tool_disabled
 3. context is a valid ToolContext         -> tool_context_invalid
 4. strict input validation                -> tool_project_required / tool_input_invalid
 5. project scope (pure comparison)        -> tool_scope_denied
 6. permissions (policy ceiling ∩ context) -> tool_permission_denied
 7. resolve project via ProjectRegistry    -> tool_project_not_found / tool_project_archived
 8. resolve allowlisted implementation     -> tool_implementation_not_found
 9. execute                                -> tool_execution_failed (or the adapter's own code)
10. validate the result against output_schema -> tool_result_invalid

Permissions are checked *before* the project lookup, so a caller without
rights cannot probe which projects exist. ``invoke`` never raises for these
outcomes; it returns a :class:`ToolResult` with ``success=False`` and a
structured error.

The registry is in-memory and code-owned: definitions, policy, and
invocations are not persisted (Phase 09). It decides nothing about *which*
tool to use; that belongs to later phases.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.contracts.schema import check_object
from siamang.log import get_logger
from siamang.projects import Project, ProjectNotFoundError, ProjectRegistry
from siamang.tools.definition import ToolDefinition, check_read_only, validate_input
from siamang.tools.errors import (
    ToolContextError,
    ToolDefinitionError,
    ToolDisabledError,
    ToolDuplicateError,
    ToolError,
    ToolExecutionError,
    ToolImplementationNotFoundError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolProjectArchivedError,
    ToolProjectNotFoundError,
    ToolResultError,
    ToolScopeError,
)
from siamang.tools.git_tools import GIT_TOOLS
from siamang.tools.implementations import KNOWN_IMPLEMENTATIONS, GitWorkspaceManager, ImplementationTable
from siamang.tools.permissions import DEFAULT_POLICY, Permission, PermissionPolicy, ToolContext

log = get_logger("tools")


@dataclass(frozen=True)
class ToolResult:
    tool_id: str
    success: bool
    data: Mapping[str, Any] | None = None
    error: ToolError | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "success": self.success,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "metadata": dict(self.metadata),
        }


class ToolRegistry:
    def __init__(
        self,
        *,
        projects: ProjectRegistry | None = None,
        implementations: ImplementationTable | None = None,
        policy: PermissionPolicy = DEFAULT_POLICY,
    ):
        if not isinstance(policy, PermissionPolicy):
            raise TypeError("policy must be a PermissionPolicy")
        if implementations is not None and not isinstance(implementations, ImplementationTable):
            raise TypeError("implementations must be an ImplementationTable")
        self._projects = projects
        self._implementations = implementations or ImplementationTable()
        self.policy = policy
        self._tools: dict[str, ToolDefinition] = {}

    # -- catalog -------------------------------------------------------------------

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        if not isinstance(definition, ToolDefinition):
            raise ToolDefinitionError("only ToolDefinition objects can be registered")
        check_read_only(definition)
        if definition.implementation not in KNOWN_IMPLEMENTATIONS:
            raise ToolImplementationNotFoundError(f"unknown implementation {definition.implementation!r}",
                                                  tool_id=definition.tool_id)
        if definition.tool_id in self._tools:
            raise ToolDuplicateError("tool_id is already registered", tool_id=definition.tool_id)
        self._tools[definition.tool_id] = definition
        log.debug("registered tool %s", definition.tool_id)
        return definition

    def list_tools(self, context: ToolContext) -> list[ToolDefinition]:
        """All registered tools (including disabled ones), sorted by ID. Needs ``tool.list``."""
        self._require(context, {Permission.TOOL_LIST}, None)
        return [self._tools[k] for k in sorted(self._tools)]

    def get_tool(self, tool_id: str, context: ToolContext) -> ToolDefinition:
        """One tool's definition. Needs ``tool.inspect``."""
        self._require(context, {Permission.TOOL_INSPECT}, tool_id)
        definition = self._tools.get(tool_id) if isinstance(tool_id, str) else None
        if definition is None:
            raise ToolNotFoundError("unknown tool", tool_id=tool_id if isinstance(tool_id, str) else None)
        return definition

    # -- invocation ----------------------------------------------------------------

    def invoke(self, tool_id: str, context: ToolContext, input_data: Any) -> ToolResult:
        started = time.monotonic()
        safe_id = tool_id if isinstance(tool_id, str) else "<invalid>"
        try:
            data, project = self._invoke(tool_id, context, input_data)
        except ToolError as exc:
            error: ToolError | None = exc
            data, project = None, None
        else:
            error = None
        ms = int((time.monotonic() - started) * 1000)
        actor = f"{context.actor_type}:{context.actor_id}" if isinstance(context, ToolContext) else "<invalid>"
        if error is not None:
            if error.tool_id is None:
                error.tool_id = safe_id if safe_id in self._tools else None
            log.info("tool %s denied/failed for %s: %s", safe_id, actor, error.code)
        else:
            log.info("tool %s ok for %s (%d ms)", safe_id, actor, ms)
        metadata = {
            "actor": actor,
            "project_id": project.project_id if project else None,
            "tool_version": self._tools[safe_id].version if safe_id in self._tools else None,
            "duration_ms": ms,
        }
        return ToolResult(safe_id, error is None, data, error, MappingProxyType(metadata))

    def _invoke(self, tool_id: Any, context: Any, input_data: Any) -> tuple[dict[str, Any], Project]:
        definition = self._tools.get(tool_id) if isinstance(tool_id, str) else None  # 1
        if definition is None:
            raise ToolNotFoundError("unknown tool")
        if not definition.enabled:  # 2
            raise ToolDisabledError("tool is disabled", tool_id=definition.tool_id)
        if not isinstance(context, ToolContext):  # 3
            raise ToolContextError("context must be a ToolContext", tool_id=definition.tool_id)
        args = validate_input(definition, input_data)  # 4
        if definition.requires_project and context.project_id != args["project_id"]:  # 5
            raise ToolScopeError("project_id is outside the caller's project scope", tool_id=definition.tool_id)
        self._require(context, definition.required_permissions, definition.tool_id)  # 6
        project = self._resolve_project(definition, args) if definition.requires_project else None  # 7
        implementation = self._implementations.get(definition.implementation)  # 8
        if implementation is None:
            raise ToolImplementationNotFoundError("no implementation bound for this tool", tool_id=definition.tool_id)
        data = self._execute(definition, implementation, project, args)  # 9
        violations = check_object(data, definition.output_schema, "result")  # 10
        if violations:
            raise ToolResultError("result does not match the tool contract", tool_id=definition.tool_id,
                                  details=tuple(str(v) for v in violations[:10]))
        return data, project

    # -- internals -----------------------------------------------------------------

    def _require(self, context: Any, required: set[Permission] | frozenset[Permission], tool_id: str | None) -> None:
        if not isinstance(context, ToolContext):
            raise ToolContextError("context must be a ToolContext", tool_id=tool_id)
        decision = self.policy.evaluate(context, required)
        if not decision.allowed:
            raise ToolPermissionError("permission denied", tool_id=tool_id,
                                      details=tuple(sorted(str(p) for p in decision.missing)))

    def _resolve_project(self, definition: ToolDefinition, args: Mapping[str, Any]) -> Project:
        if self._projects is None:
            raise ToolProjectNotFoundError("no project registry available", tool_id=definition.tool_id)
        failure: ToolError | None = None
        try:
            project = self._projects.get(args["project_id"])
        except ProjectNotFoundError:
            failure = ToolProjectNotFoundError("unknown project", tool_id=definition.tool_id)
        if failure is not None:
            raise failure
        if not project.is_active:
            raise ToolProjectArchivedError("project is archived; restore it first", tool_id=definition.tool_id)
        return project

    @staticmethod
    def _execute(definition: ToolDefinition, implementation, project: Project | None,
                 args: Mapping[str, Any]) -> dict[str, Any]:
        failure: ToolError | None = None
        try:
            data = implementation(project, MappingProxyType(args))
        except ToolError as exc:
            exc.tool_id = definition.tool_id
            failure = exc
        except Exception as exc:  # never leak a traceback or message from below
            failure = ToolExecutionError("tool implementation failed", tool_id=definition.tool_id,
                                         cause=type(exc).__name__)
        if failure is not None:
            raise failure
        if not isinstance(data, dict):
            raise ToolResultError("result must be an object", tool_id=definition.tool_id)
        return data


def build_tool_registry(
    *,
    projects: ProjectRegistry | None = None,
    git: GitWorkspaceManager | None = None,
    policy: PermissionPolicy = DEFAULT_POLICY,
) -> ToolRegistry:
    """The Phase 09 registry: the five read-only Git tools.

    Without ``projects``/``git`` the catalog is still complete (for listing),
    but invocations are denied (no project registry / no implementation).
    """
    registry = ToolRegistry(projects=projects, implementations=ImplementationTable.build(git=git), policy=policy)
    for definition in GIT_TOOLS:
        registry.register(definition)
    return registry
