"""Implementation allowlist: implementation ID -> SIAMANG-owned adapter method.

The registry never maps a string to an arbitrary callable.
:data:`KNOWN_IMPLEMENTATIONS` is code-owned and fixed. An
:class:`ImplementationTable` accepts a binding only when it is a bound method
of exactly the declared adapter class, and only that class's own function
for that ID. Lambdas, plain functions, subclasses, other objects' methods,
and unknown IDs are refused, so a plugin cannot inject code through it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType, MethodType
from typing import Any

from siamang.git import GitWorkspaceManager
from siamang.projects import Project
from siamang.tools.errors import ToolDefinitionError, ToolImplementationNotFoundError
from siamang.tools.git_tools import GitToolAdapter

Implementation = Callable[[Project, Mapping[str, Any]], dict[str, Any]]

# implementation ID -> (adapter class, method name)
KNOWN_IMPLEMENTATIONS: Mapping[str, tuple[type, str]] = MappingProxyType({
    "git.inspect": (GitToolAdapter, "inspect"),
    "git.status": (GitToolAdapter, "status"),
    "git.diff": (GitToolAdapter, "diff"),
    "git.diff_cached": (GitToolAdapter, "diff_cached"),
    "git.worktrees": (GitToolAdapter, "worktrees"),
})


def _is_owned(impl_id: str, fn: Any) -> bool:
    cls, method = KNOWN_IMPLEMENTATIONS[impl_id]
    return (
        isinstance(fn, MethodType)
        and type(fn.__self__) is cls
        and fn.__func__ is cls.__dict__.get(method)
    )


class ImplementationTable:
    def __init__(self, bindings: Mapping[str, Implementation] | None = None):
        checked: dict[str, Implementation] = {}
        for impl_id, fn in (bindings or {}).items():
            if impl_id not in KNOWN_IMPLEMENTATIONS:
                raise ToolImplementationNotFoundError(f"unknown implementation {impl_id!r}")
            if not _is_owned(impl_id, fn):
                raise ToolDefinitionError(f"implementation {impl_id!r} must be the SIAMANG-owned adapter method")
            checked[impl_id] = fn
        self._bindings: Mapping[str, Implementation] = MappingProxyType(checked)

    @classmethod
    def build(cls, *, git: GitWorkspaceManager | None = None) -> ImplementationTable:
        """Bind every implementation whose service is available."""
        bindings: dict[str, Implementation] = {}
        if git is not None:
            bindings.update(GitToolAdapter(git).bindings())
        return cls(bindings)

    def get(self, impl_id: str) -> Implementation | None:
        fn = self._bindings.get(impl_id)
        # Re-checked at use: the allowlist is the authority, not the stored mapping.
        if fn is None or impl_id not in KNOWN_IMPLEMENTATIONS or not _is_owned(impl_id, fn):
            return None
        return fn

    def ids(self) -> frozenset[str]:
        return frozenset(self._bindings)
