"""Tool Registry + Permission boundary (Phase 09).

Declares tools, checks permissions (deny by default), scopes them to a
registered active project, and invokes allowlisted SIAMANG-owned
implementations. The only tools are five read-only Git capabilities.

Does not choose tools, run tasks or agents, call models, execute shells or
arbitrary processes, or mutate Git.
"""

from siamang.tools.definition import ToolDefinition, ToolOperation
from siamang.tools.errors import ToolError, ToolErrorCode
from siamang.tools.permissions import (
    DEFAULT_POLICY,
    ActorType,
    Permission,
    PermissionDecision,
    PermissionPolicy,
    ToolContext,
)
from siamang.tools.registry import ToolRegistry, ToolResult, build_tool_registry

__all__ = [
    "DEFAULT_POLICY",
    "ActorType",
    "Permission",
    "PermissionDecision",
    "PermissionPolicy",
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolErrorCode",
    "ToolOperation",
    "ToolRegistry",
    "ToolResult",
    "build_tool_registry",
]
