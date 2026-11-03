"""Git Workspace Manager (Phase 08): read-only, local Git awareness for projects.

Does not commit, branch, push, pull, fetch, create worktrees, register agent
tools, create task workspaces, or call models.
"""

from siamang.git.client import GitClient, GitResult
from siamang.git.errors import (
    GitCommandError,
    GitError,
    GitErrorCode,
    GitNotInstalledError,
    GitPermissionError,
    GitTimeoutError,
    InvalidGitArgumentError,
    InvalidRepositoryError,
    NotRepositoryError,
    ProtectedRepositoryError,
    RepositoryLockedError,
    UnexpectedGitError,
    UnsupportedGitVersionError,
    WorktreeError,
)
from siamang.git.repository import (
    Diff,
    DiffFile,
    FileState,
    HeadInfo,
    Relation,
    Remote,
    RepositoryInspection,
    RepositoryLocation,
    StatusEntry,
    WorkingTreeStatus,
    Workspace,
    WorkspaceKind,
)
from siamang.git.workspace import PROTECTED_REPOSITORIES, GitWorkspaceManager

__all__ = [
    "PROTECTED_REPOSITORIES",
    "Diff",
    "DiffFile",
    "FileState",
    "GitClient",
    "GitCommandError",
    "GitError",
    "GitErrorCode",
    "GitNotInstalledError",
    "GitPermissionError",
    "GitResult",
    "GitTimeoutError",
    "GitWorkspaceManager",
    "HeadInfo",
    "InvalidGitArgumentError",
    "InvalidRepositoryError",
    "NotRepositoryError",
    "ProtectedRepositoryError",
    "Relation",
    "Remote",
    "RepositoryInspection",
    "RepositoryLocation",
    "RepositoryLockedError",
    "StatusEntry",
    "UnexpectedGitError",
    "UnsupportedGitVersionError",
    "WorkingTreeStatus",
    "Workspace",
    "WorkspaceKind",
    "WorktreeError",
]
