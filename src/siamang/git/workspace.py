"""Git Workspace Manager: Project -> repository -> read-only Git state.

::

    CLI ──► GitWorkspaceManager ──┬──► ProjectRegistry (project_id -> Project)
                                  └──► GitClient ──► git

The manager never writes anything: no commit, branch, checkout, stage,
reset, worktree creation, fetch, pull, or push. Git is the source of truth
for HEAD, branches, the work tree, and worktrees, so nothing here is cached
or persisted; every call asks Git again.

Paths: the project's ``root_path`` is used exactly as registered (never
resolved by Siamang) and handed to Git as ``-C <root_path>``. Git's answers
(repository root, git dirs, worktree paths) are Git's own absolute paths,
with symlinks resolved by Git. See :class:`RepositoryLocation`.

Protected repositories (ARCHITECTURE §6): a project whose repository *is*
the Siamang repository (identified by a commit that only its history
contains, so clones and worktrees count too) is refused with
:class:`ProtectedRepositoryError`. A separate repository that merely sits in
a directory under one of them is allowed.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from siamang.git.client import GitClient, validate_path
from siamang.git.errors import (
    InvalidGitArgumentError,
    InvalidRepositoryError,
    NotRepositoryError,
    ProtectedRepositoryError,
)
from siamang.git.repository import (
    Diff,
    HeadInfo,
    Remote,
    RepositoryInspection,
    RepositoryLocation,
    WorkingTreeStatus,
    Workspace,
    parse_location,
    parse_numstat,
    parse_remotes,
    parse_status,
    parse_worktrees,
)
from siamang.log import get_logger
from siamang.projects import Project, ProjectRegistry

log = get_logger("git")

# Identity markers: a commit only these histories contain.
PROTECTED_REPOSITORIES: Mapping[str, str] = MappingProxyType(
    {
        "siamang": "5551d4bfdd472e4a1c294ee11eada938b020ef0d",  # Siamang root commit
    }
)

_LOCATE = ["rev-parse", "--path-format=absolute", "--show-toplevel", "--show-prefix", "--git-dir", "--git-common-dir"]
_STATUS = ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=normal"]


class GitWorkspaceManager:
    def __init__(
        self,
        registry: ProjectRegistry | None = None,
        client: GitClient | None = None,
        *,
        protected: Mapping[str, str] = PROTECTED_REPOSITORIES,
    ):
        self.registry = registry
        self.client = client or GitClient()
        self.protected = MappingProxyType(dict(protected))

    # -- project resolution --------------------------------------------------------

    def project(self, ref: Project | str) -> Project:
        """Resolve a project ID through the registry (a ``Project`` passes through)."""
        if isinstance(ref, Project):
            return ref
        if self.registry is None:
            raise InvalidGitArgumentError("a project_id needs a ProjectRegistry; pass a Project instead")
        return self.registry.get(ref)

    # -- path level (no project, no protection check) ------------------------------

    def locate_path(self, path: str) -> RepositoryLocation:
        """Repository location for any absolute path. Raises ``NotRepositoryError``."""
        path = validate_path(path)
        result = self.client.run(_LOCATE, cwd=path, operation="locate")
        return parse_location(path, result.text())

    def is_repository(self, path: str) -> bool:
        """Is ``path`` inside a Git work tree? (Bare repositories and missing paths: ``False``.)"""
        try:
            self.locate_path(path)
        except (NotRepositoryError, InvalidRepositoryError):
            return False
        return True

    def repository_root(self, path: str) -> str:
        return self.locate_path(path).repository_root

    # -- project level -------------------------------------------------------------

    def locate(self, ref: Project | str) -> RepositoryLocation:
        """Project -> repository. Refuses protected repositories."""
        project = self.project(ref)
        location = self.locate_path(project.root_path)
        self._check_not_protected(location)
        log.debug("located project %s (%s)", project.project_id, location.relation)
        return location

    def head(self, ref: Project | str) -> HeadInfo:
        location = self.locate(ref)
        cwd = location.project_root
        commit_res = self.client.run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd=cwd,
                                     operation="head", check=False)
        commit = commit_res.text().strip() if commit_res.ok else None
        branch_res = self.client.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd,
                                     operation="head", check=False)
        branch = branch_res.text().strip() if branch_res.ok else None
        if branch is None and commit is None:
            raise InvalidRepositoryError("HEAD is neither a branch nor a commit", operation="head")
        return HeadInfo(commit=commit, branch=branch, detached=branch is None)

    def status(self, ref: Project | str) -> WorkingTreeStatus:
        location = self.locate(ref)
        return self._status(location)

    def diff(self, ref: Project | str, *, cached: bool = False) -> Diff:
        """Read-only diff of the whole repository: work tree vs index, or (``cached``) index vs HEAD."""
        location = self.locate(ref)
        base = ["diff", *(["--cached"] if cached else []), "--find-renames"]
        numstat = self.client.run([*base, "--numstat", "-z"], cwd=location.project_root, operation="diff")
        patch = self.client.run(base, cwd=location.project_root, operation="diff")
        return Diff(cached=cached, files=parse_numstat(numstat.text()), patch=patch.text(), truncated=patch.truncated)

    def worktrees(self, ref: Project | str) -> tuple[Workspace, ...]:
        """Discover existing worktrees from Git's own metadata (no filesystem scan)."""
        location = self.locate(ref)
        result = self.client.run(["worktree", "list", "--porcelain", "-z"], cwd=location.project_root,
                                 operation="worktrees")
        return parse_worktrees(result.text(), location.common_git_dir, location.repository_root)

    def remotes(self, ref: Project | str) -> tuple[Remote, ...]:
        """Configured remotes from local config. Never contacts them; credentials are scrubbed from URLs."""
        location = self.locate(ref)
        return self._remotes(location)

    def inspect(self, ref: Project | str) -> RepositoryInspection:
        project = self.project(ref)
        location = self.locate(project)
        return RepositoryInspection(project.project_id, location, self._status(location), self._remotes(location))

    # -- internals -----------------------------------------------------------------

    def _status(self, location: RepositoryLocation) -> WorkingTreeStatus:
        result = self.client.run(_STATUS, cwd=location.project_root, operation="status")
        return parse_status(result.text())

    def _remotes(self, location: RepositoryLocation) -> tuple[Remote, ...]:
        result = self.client.run(["remote", "-v"], cwd=location.project_root, operation="remotes")
        return parse_remotes(result.text())

    def _check_not_protected(self, location: RepositoryLocation) -> None:
        for name, commit in self.protected.items():
            result = self.client.run(["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                                     cwd=location.project_root, operation="protect", check=False)
            if result.ok:
                raise ProtectedRepositoryError(
                    f"this is the {name} repository; projects must not live inside a protected "
                    "repository (ARCHITECTURE §6)",
                    operation="locate",
                    repository_path=location.repository_root,
                )
