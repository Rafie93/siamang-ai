"""Agent definitions: immutable data, never callables.

An :class:`AgentDefinition` says what an agent *is*: identity, role,
capabilities, input/output contract, which model capabilities it needs, and
which tool permissions it would ask for. It names its implementation by
**ID** only; the class that runs is looked up in a code-owned allowlist
(:mod:`siamang.agents.implementations`).

Three things are kept apart on purpose:

* ``capabilities`` (:class:`AgentCapability`) describe what the agent does.
  They grant nothing.
* ``model_requirements`` (Phase 06 :class:`~siamang.models.Capability`) are
  what the agent may ask the Model Router for. The router still selects.
* ``tool_permissions`` (Phase 09 :class:`~siamang.tools.Permission`) are what
  the agent *declares*. Declaring is not granting: the effective set is
  always declared ∩ the PermissionPolicy ceiling for actor type ``agent``.
"""

from __future__ import annotations

from siamang.jsondata import thaw

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.agents.errors import AgentDefinitionError, AgentError, AgentInputError
from siamang.contracts.schema import F, check_object, check_spec
from siamang.models.capabilities import Capability
from siamang.projects.definition import canonical_metadata, freeze_metadata
from siamang.projects.errors import InvalidProjectError
from siamang.tasks.definition import FORBIDDEN_KEYS
from siamang.tools.permissions import PHASE09_FORBIDDEN, Permission

# agent.<role>[.<variant>...]: lowercase, dot-separated, e.g. "agent.echo"
AGENT_ID_RE = re.compile(r"^agent\.[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31}){0,2}\Z")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")
IMPLEMENTATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31}){1,3}\Z")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}\Z")
NAME_MAX = 80
DESCRIPTION_MAX = 500
OUTPUT_MAX_BYTES = 256 * 1024
OUTPUT_MAX_DEPTH = 8

# ID segments that read as raw execution or autonomy. No agent may be called this.
FORBIDDEN_ID_SEGMENTS = frozenset({"run", "exec", "execute", "command", "cmd", "shell", "raw", "eval", "spawn",
                                   "system", "popen", "subprocess", "script", "autonomous", "auto", "root", "sudo"})


class AgentCapability(StrEnum):
    """What an agent does. Identity, not authorization."""

    REASONING = "reasoning"
    TEXT_GENERATION = "text_generation"
    STRUCTURED_ANALYSIS = "structured_analysis"
    CODE_ANALYSIS = "code_analysis"
    CODE_GENERATION = "code_generation"
    TESTING = "testing"
    REVIEW = "review"
    PLANNING = "planning"


def _frozen(spec: Mapping[str, F]) -> Mapping[str, F]:
    return MappingProxyType(dict(spec))


def _enum_set(cls: type[StrEnum], values: Any, agent_id: Any, what: str) -> frozenset[Any]:
    if isinstance(values, (str, bytes)):
        raise _bad(agent_id, f"{what} must be a collection, not a string")
    try:
        items = list(values)
    except TypeError:
        raise _bad(agent_id, f"{what} must be a collection") from None
    out = set()
    for v in items:
        try:
            out.add(cls(v))
        except ValueError:
            raise _bad(agent_id, f"unknown {what[:-1] if what.endswith('s') else what} {v!r}") from None
    return frozenset(out)


def _bad(agent_id: Any, message: str) -> AgentDefinitionError:
    return AgentDefinitionError(message, agent_id=agent_id if isinstance(agent_id, str) else None)


def canonical(value: Any, where: str, agent_id: str | None = None,
              error: type[AgentError] = AgentDefinitionError) -> str:
    """Project metadata policy (≤ 16 KiB, ≤ 64 keys, ≤ 5 levels, no credential/executable keys)."""
    try:
        return canonical_metadata(value)
    except InvalidProjectError as exc:
        raise error(f"{where}: {str(exc).split(': ', 1)[-1]}", agent_id=agent_id) from None


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str
    version: str
    role: str
    implementation: str  # ID in the implementation allowlist; never a callable
    capabilities: frozenset[AgentCapability]
    input_schema: Mapping[str, F]
    output_schema: Mapping[str, F]
    model_requirements: frozenset[Capability] = frozenset()
    tool_permissions: frozenset[Permission] = frozenset()
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        aid = self.agent_id
        object.__setattr__(self, "capabilities", _enum_set(AgentCapability, self.capabilities, aid, "capabilities"))
        object.__setattr__(self, "model_requirements",
                           _enum_set(Capability, self.model_requirements, aid, "model_requirements"))
        object.__setattr__(self, "tool_permissions",
                           _enum_set(Permission, self.tool_permissions, aid, "tool_permissions"))
        if not isinstance(self.input_schema, Mapping) or not isinstance(self.output_schema, Mapping):
            raise _bad(aid, "input_schema and output_schema must be mappings of schema fields")
        object.__setattr__(self, "input_schema", _frozen(self.input_schema))
        object.__setattr__(self, "output_schema", _frozen(self.output_schema))
        object.__setattr__(self, "metadata", freeze_metadata(canonical(self.metadata, "metadata", aid)))
        validate_definition(self)

    @property
    def uses_models(self) -> bool:
        return bool(self.model_requirements)

    @property
    def uses_tools(self) -> bool:
        return bool(self.tool_permissions)

    def to_dict(self) -> dict[str, Any]:
        """Public description. The implementation binding is internal and left out."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "role": self.role,
            "capabilities": sorted(str(c) for c in self.capabilities),
            "model_requirements": sorted(str(c) for c in self.model_requirements),
            "tool_permissions": sorted(str(p) for p in self.tool_permissions),
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


def validate_definition(d: AgentDefinition) -> None:
    aid = d.agent_id
    if not isinstance(aid, str) or not AGENT_ID_RE.fullmatch(aid):
        raise _bad(aid, "agent_id must look like agent.<role> (lowercase, dot-separated)")
    if set(aid.split(".")[1:]) & FORBIDDEN_ID_SEGMENTS:
        raise _bad(aid, "agent_id names raw execution or autonomy")
    if not isinstance(d.role, str) or not ROLE_RE.fullmatch(d.role) or aid.split(".")[1] != d.role:
        raise _bad(aid, "role must be a lowercase identifier equal to the agent_id's second segment")
    for attr, value, limit in (("name", d.name, NAME_MAX), ("description", d.description, DESCRIPTION_MAX)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise _bad(aid, f"{attr} must be 1-{limit} characters")
    if not isinstance(d.version, str) or not VERSION_RE.fullmatch(d.version):
        raise _bad(aid, "version must look like 1 or 1.2 or 1.2.3")
    if not isinstance(d.implementation, str) or not IMPLEMENTATION_RE.fullmatch(d.implementation):
        raise _bad(aid, "implementation must be an implementation ID string")
    if not isinstance(d.enabled, bool):
        raise _bad(aid, "enabled must be a boolean")
    if not d.capabilities:
        raise _bad(aid, "an agent must declare at least one capability")
    if d.model_requirements and Capability.TEXT_GENERATION not in d.model_requirements:
        raise _bad(aid, "model_requirements must include text_generation (every model request needs it)")
    forbidden = d.tool_permissions & PHASE09_FORBIDDEN
    if forbidden:
        raise _bad(aid, f"tool permissions not allowed in this phase: {sorted(map(str, forbidden))}")
    if d.tool_permissions and Permission.TOOL_INVOKE not in d.tool_permissions:
        raise _bad(aid, "tool_permissions must include tool.invoke when any tool use is declared")
    for which, spec in (("input", d.input_schema), ("output", d.output_schema)):
        for name, f in spec.items():
            if not isinstance(f, F):
                raise _bad(aid, f"{which} field {name!r} must be a schema field")
            if which == "input" and name.lower() in FORBIDDEN_KEYS:
                raise _bad(aid, f"input field {name!r} is not allowed (no commands, argv, paths, or credentials)")
        problems = check_spec(spec, which)
        if problems:
            raise _bad(aid, "; ".join(problems))
    if not d.output_schema:
        raise _bad(aid, "output_schema must not be empty")


# --- payload checks (used by the registry) -------------------------------------------


def validate_input(d: AgentDefinition, data: Mapping[str, Any]) -> None:
    """Strict: unknown fields are refused, then the input contract is checked."""
    unknown = sorted(set(data) - set(d.input_schema))
    if unknown:
        raise AgentInputError("unknown input field(s)", agent_id=d.agent_id, details=tuple(unknown))
    violations = check_object(thaw(data), d.input_schema, "input")
    if violations:
        raise AgentInputError("input does not match the agent contract", agent_id=d.agent_id,
                              details=tuple(str(v) for v in violations[:10]))


def plain_output(value: Any, where: str = "output") -> str:
    """Agent output: a JSON object of plain data, ≤ 256 KiB and ≤ 8 levels. Returns canonical JSON.

    Looser than the metadata policy (no key-name rules) because output is a
    product, not configuration; still never code, objects, or NaN.
    """

    def plain(v: Any, level: int) -> Any:  # validated plain copy (read-only mappings become dicts)
        if level > OUTPUT_MAX_DEPTH:
            raise ValueError(f"{where} is nested deeper than {OUTPUT_MAX_DEPTH} levels")
        if isinstance(v, Mapping):
            if not all(isinstance(k, str) for k in v):
                raise ValueError(f"{where} keys must be strings")
            return {k: plain(item, level + 1) for k, item in v.items()}
        if isinstance(v, (list, tuple)):
            return [plain(item, level + 1) for item in v]
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        raise ValueError(f"{where} must contain plain JSON data only")

    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be an object")
    copy = plain(value, 1)
    try:
        text = json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except ValueError:
        raise ValueError(f"{where} must not contain NaN or Infinity") from None
    if len(text.encode("utf-8")) > OUTPUT_MAX_BYTES:
        raise ValueError(f"{where} exceeds {OUTPUT_MAX_BYTES} bytes")
    return text
