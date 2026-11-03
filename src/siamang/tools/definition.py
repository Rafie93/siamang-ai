"""Tool definitions: immutable data, never callables.

A :class:`ToolDefinition` says what a tool is (ID, contract, required
permissions). It names its implementation by **ID** only. The code that
runs is bound separately, from a code-owned allowlist
(:mod:`siamang.tools.implementations`).

Input and output contracts reuse the contract mini-schema
(:mod:`siamang.contracts.schema`). Inputs are additionally **strict**:
unknown fields are rejected, and no tool may declare an execution-style
or path input (``command``, ``argv``, ``path``, ...).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.contracts.schema import F, check_object, check_spec
from siamang.projects.definition import PROJECT_ID_RE
from siamang.tools.errors import ToolDefinitionError, ToolInputError, ToolProjectRequiredError
from siamang.tools.permissions import PHASE09_FORBIDDEN, Permission

# namespace.name[.name...]: lowercase, dot-separated, e.g. "git.diff_cached"
TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31}){1,3}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}$")
NAME_MAX = 80
DESCRIPTION_MAX = 500

# Tool ID segments that read as raw execution. No tool may be called this.
FORBIDDEN_ID_SEGMENTS = frozenset({"run", "exec", "execute", "command", "cmd", "shell", "raw", "eval", "spawn",
                                   "system", "popen", "subprocess", "script"})
# Input fields that would turn a specific tool into a generic executor or bypass the Project Registry.
FORBIDDEN_INPUT_FIELDS = frozenset({
    "command", "commands", "cmd", "argv", "args", "arguments", "shell", "shell_command", "git_args", "script",
    "python", "callable", "function", "exec", "executable", "binary", "program", "env", "environment",
    "path", "paths", "root_path", "cwd", "directory", "repository_path", "pathspec", "url",
})
# Input kinds that carry arbitrary structure are not allowed in strict input schemas.
INPUT_KINDS = frozenset({"str", "text", "int", "number", "bool", "enum", "id"})


class ToolOperation(StrEnum):
    READ = "read"
    WRITE = "write"


def _frozen(spec: Mapping[str, F]) -> Mapping[str, F]:
    return MappingProxyType(dict(spec))


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    name: str
    description: str
    version: str
    category: str
    operation: ToolOperation
    implementation: str  # ID in the implementation allowlist; never a callable
    capabilities: frozenset[Permission]  # permissions required to invoke (besides tool.invoke)
    input_schema: Mapping[str, F]
    output_schema: Mapping[str, F]
    requires_project: bool = True
    requires_workspace: bool = False
    mutating: bool = False
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", ToolOperation(self.operation))
        object.__setattr__(self, "capabilities", frozenset(Permission(c) for c in self.capabilities))
        object.__setattr__(self, "input_schema", _frozen(self.input_schema))
        object.__setattr__(self, "output_schema", _frozen(self.output_schema))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        validate_definition(self)

    @property
    def required_permissions(self) -> frozenset[Permission]:
        return self.capabilities | {Permission.TOOL_INVOKE}

    def to_dict(self) -> dict[str, Any]:
        """Public description. The implementation binding is internal and left out."""
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "operation": str(self.operation),
            "mutating": self.mutating,
            "requires_project": self.requires_project,
            "requires_workspace": self.requires_workspace,
            "capabilities": sorted(str(c) for c in self.capabilities),
            "required_permissions": sorted(str(c) for c in self.required_permissions),
            "input_schema": _describe(self.input_schema),
            "output_schema": _describe(self.output_schema),
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }


def _describe(spec: Mapping[str, F]) -> dict[str, Any]:
    out = {}
    for name, f in spec.items():
        d: dict[str, Any] = {"kind": f.kind, "required": f.required}
        if f.nullable:
            d["nullable"] = True
        if f.choices:
            d["choices"] = list(f.choices)
        out[name] = d
    return out


def _bad(tool_id: Any, message: str) -> ToolDefinitionError:
    return ToolDefinitionError(message, tool_id=tool_id if isinstance(tool_id, str) else None)


def validate_definition(d: ToolDefinition) -> None:
    tid = d.tool_id
    if not isinstance(tid, str) or not TOOL_ID_RE.fullmatch(tid):
        raise _bad(tid, "tool_id must look like namespace.name (lowercase, dot-separated)")
    if set(tid.split(".")) & FORBIDDEN_ID_SEGMENTS:
        raise _bad(tid, "tool_id names raw execution; only specific capabilities may be tools")
    for attr, value, limit in (("name", d.name, NAME_MAX), ("description", d.description, DESCRIPTION_MAX)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise _bad(tid, f"{attr} must be 1-{limit} characters")
    if not isinstance(d.version, str) or not VERSION_RE.fullmatch(d.version):
        raise _bad(tid, "version must look like 1 or 1.2 or 1.2.3")
    if not isinstance(d.category, str) or d.category != tid.split(".")[0]:
        raise _bad(tid, "category must equal the tool_id namespace")
    if not isinstance(d.implementation, str) or not TOOL_ID_RE.fullmatch(d.implementation):
        raise _bad(tid, "implementation must be an implementation ID string")
    flags = {"requires_project": d.requires_project, "requires_workspace": d.requires_workspace,
             "mutating": d.mutating, "enabled": d.enabled}
    for flag, value in flags.items():
        if not isinstance(value, bool):
            raise _bad(tid, f"{flag} must be a boolean")
    if d.mutating != (d.operation is ToolOperation.WRITE):
        raise _bad(tid, "mutating must match operation (write <=> mutating)")
    if d.requires_workspace and not d.requires_project:
        raise _bad(tid, "a workspace belongs to a project; requires_workspace needs requires_project")
    # input schema: flat, strict, no execution-style or path fields
    for name, f in d.input_schema.items():
        if not isinstance(f, F):
            raise _bad(tid, f"input field {name!r} must be a schema field")
        if name.lower() in FORBIDDEN_INPUT_FIELDS:
            raise _bad(tid, f"input field {name!r} is not allowed (no commands, argv, or paths)")
        if f.kind not in INPUT_KINDS:
            raise _bad(tid, f"input field {name!r}: kind {f.kind!r} not allowed in tool inputs")
    if d.requires_project:
        pf = d.input_schema.get("project_id")
        if pf is None or not pf.required or pf.kind != "id":
            raise _bad(tid, "project tools must declare a required project_id (kind id)")
    elif "project_id" in d.input_schema:
        raise _bad(tid, "project_id declared but requires_project is false")
    for which, spec in (("input", d.input_schema), ("output", d.output_schema)):
        problems = check_spec(spec, which)
        if problems:
            raise _bad(tid, "; ".join(problems))
    if not d.output_schema:
        raise _bad(tid, "output_schema must not be empty")
    try:
        json.dumps(dict(d.metadata), allow_nan=False)
    except (TypeError, ValueError):
        raise _bad(tid, "metadata must be plain JSON data") from None


def check_read_only(d: ToolDefinition) -> None:
    """Phase 09 rule, applied by the registry: only read-only tools exist."""
    if d.mutating or d.operation is not ToolOperation.READ:
        raise _bad(d.tool_id, "mutating tools cannot be registered in this phase")
    forbidden = d.capabilities & PHASE09_FORBIDDEN
    if forbidden:
        raise _bad(d.tool_id, f"capabilities not allowed in this phase: {sorted(map(str, forbidden))}")


def validate_input(d: ToolDefinition, data: Any) -> dict[str, Any]:
    """Strict validation. Returns a plain-dict copy; the caller's object is never used afterwards."""
    if not isinstance(data, Mapping):
        raise ToolInputError("input must be an object", tool_id=d.tool_id)
    if not all(isinstance(k, str) for k in data):
        raise ToolInputError("input keys must be strings", tool_id=d.tool_id)
    if d.requires_project and "project_id" not in data:
        raise ToolProjectRequiredError("this tool needs project_id", tool_id=d.tool_id)
    unknown = sorted(set(data) - set(d.input_schema))
    if unknown:
        execution = [k for k in unknown if k.lower() in FORBIDDEN_INPUT_FIELDS]
        msg = "execution-style or path input is not accepted" if execution else "unknown input field(s)"
        raise ToolInputError(msg, tool_id=d.tool_id, details=tuple(unknown))
    copy = dict(data)
    violations = check_object(copy, d.input_schema, "input")
    if violations:
        raise ToolInputError("input does not match the tool contract", tool_id=d.tool_id,
                             details=tuple(str(v) for v in violations))
    if d.requires_project and not PROJECT_ID_RE.fullmatch(copy["project_id"]):
        raise ToolInputError("project_id must look like prj_<id>", tool_id=d.tool_id, details=("input.project_id",))
    return copy
