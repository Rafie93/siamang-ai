"""GitState: a read-only Git snapshot through the Phase 08 GitWorkspaceManager.

The analyst never runs Git itself. It asks the existing manager for
``inspect`` (location, status, remotes) and ``worktrees``, which are all
read-only, and keeps only machine-independent facts: branch, commit,
clean/dirty counts, ahead/behind, repository-relative changed paths, the
number of worktrees, and remote *names* (no URLs). Absolute paths (repository
root, git dir, worktree paths) are dropped so the analysis stays
deterministic across machines.

A protected repository (ARCHITECTURE §6) refuses the whole
analysis. Any other Git error leaves Git state ``unavailable`` with the
error code as the reason. It is not raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.errors import AnalysisProjectUnavailableError
from siamang.git.errors import GitError, ProtectedRepositoryError
from siamang.git.workspace import GitWorkspaceManager
from siamang.projects import Project

CHANGED_PATHS_MAX = 50


@dataclass(frozen=True)
class GitState:
    available: bool
    reason: str | None = None  # why unavailable: "not_inspected" or a GitError code
    branch: str | None = None
    commit: str | None = None
    detached: bool = False
    unborn: bool = False
    clean: bool | None = None
    counts: Any = field(default_factory=lambda: MappingProxyType({}))
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    changed_paths: tuple[str, ...] = ()
    changed_total: int = 0
    worktrees: int = 0
    remotes: tuple[str, ...] = ()
    in_subdirectory: bool = False  # the project root is below the repository root

    @property
    def short_commit(self) -> str | None:
        return self.commit[:12] if self.commit else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available, "reason": self.reason, "branch": self.branch,
            "commit": self.short_commit, "detached": self.detached, "unborn": self.unborn, "clean": self.clean,
            "counts": dict(self.counts), "upstream": self.upstream, "ahead": self.ahead, "behind": self.behind,
            "changed_total": self.changed_total, "worktrees": self.worktrees, "remotes": list(self.remotes),
            "in_subdirectory": self.in_subdirectory,
        }


NOT_INSPECTED = GitState(False, "not_inspected")


def read_git_state(git: GitWorkspaceManager | None, project: Project) -> GitState:
    if git is None:
        return NOT_INSPECTED
    if not isinstance(git, GitWorkspaceManager):
        raise TypeError("git must be the GitWorkspaceManager")
    failure: AnalysisProjectUnavailableError | None = None
    try:
        inspection = git.inspect(project)
        worktrees = git.worktrees(project)
    except ProtectedRepositoryError:
        failure = AnalysisProjectUnavailableError("project is inside a protected repository (ARCHITECTURE §6)",
                                                  cause="protected_repository")
    except GitError as exc:
        return GitState(False, str(exc.code))
    if failure is not None:
        raise failure
    status = inspection.status
    head = status.head
    paths = tuple(sorted(e.path for e in status.entries))
    return GitState(
        available=True,
        branch=head.branch,
        commit=head.commit,
        detached=head.detached,
        unborn=head.unborn,
        clean=status.clean,
        counts=MappingProxyType({k: v for k, v in status.counts().items() if v}),
        upstream=status.upstream,
        ahead=status.ahead,
        behind=status.behind,
        changed_paths=paths[:CHANGED_PATHS_MAX],
        changed_total=len(paths),
        worktrees=len(worktrees),
        remotes=tuple(sorted({r.name for r in inspection.remotes})),
        in_subdirectory=bool(inspection.location.prefix),
    )
