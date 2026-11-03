"""Normalized Git errors.

Every failure that crosses the Git boundary is a :class:`GitError` with a
typed ``code``. Raw ``subprocess`` exceptions (``FileNotFoundError``,
``TimeoutExpired``, ``CalledProcessError``, ...) never escape: the client
converts them and raises the new error *outside* the ``except`` block, so
``__cause__``/``__context__`` stay empty.

Messages are fixed, classified text. They never contain Git's stderr, local
paths, or command lines. A short, redacted excerpt of stderr is kept in
``detail`` for debugging; ``repository_path`` is kept as an attribute for
the caller (who supplied it) and is left out of ``str()``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from siamang.log import redact

DETAIL_MAX = 200
# scheme://user:password@host -> scheme://host (credentials in remote URLs or error text)
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")


class GitErrorCode(StrEnum):
    GIT_NOT_INSTALLED = "git_not_installed"
    NOT_REPOSITORY = "not_repository"
    INVALID_REPOSITORY = "invalid_repository"
    COMMAND_FAILED = "command_failed"
    COMMAND_TIMEOUT = "command_timeout"
    PERMISSION_DENIED = "permission_denied"
    INVALID_GIT_ARGUMENT = "invalid_git_argument"
    REPOSITORY_LOCKED = "repository_locked"
    WORKTREE_ERROR = "worktree_error"
    UNSUPPORTED_GIT_VERSION = "unsupported_git_version"
    PROTECTED_REPOSITORY = "protected_repository"
    UNKNOWN_GIT_ERROR = "unknown_git_error"


def scrub(text: str) -> str:
    """Remove URL credentials and secret-looking values from free text."""
    return redact(_URL_USERINFO_RE.sub(r"\1", text))


class GitError(Exception):
    code: GitErrorCode = GitErrorCode.UNKNOWN_GIT_ERROR
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        repository_path: str | None = None,
        detail: str | None = None,
        retryable: bool | None = None,
    ):
        self.message = scrub(message)
        self.operation = operation
        self.repository_path = repository_path
        self.detail = scrub(detail)[:DETAIL_MAX] if detail else None
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (operation={self.operation})" if self.operation else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "operation": self.operation,
            "repository_path": self.repository_path,
            "retryable": self.retryable,
            "detail": self.detail,
        }


class GitNotInstalledError(GitError):
    code = GitErrorCode.GIT_NOT_INSTALLED


class NotRepositoryError(GitError):
    code = GitErrorCode.NOT_REPOSITORY


class InvalidRepositoryError(GitError):
    code = GitErrorCode.INVALID_REPOSITORY


class GitCommandError(GitError):
    code = GitErrorCode.COMMAND_FAILED


class GitTimeoutError(GitError):
    code = GitErrorCode.COMMAND_TIMEOUT
    retryable = True


class GitPermissionError(GitError):
    code = GitErrorCode.PERMISSION_DENIED


class InvalidGitArgumentError(GitError, ValueError):
    code = GitErrorCode.INVALID_GIT_ARGUMENT


class RepositoryLockedError(GitError):
    code = GitErrorCode.REPOSITORY_LOCKED
    retryable = True


class WorktreeError(GitError):
    code = GitErrorCode.WORKTREE_ERROR


class UnsupportedGitVersionError(GitError):
    code = GitErrorCode.UNSUPPORTED_GIT_VERSION


class ProtectedRepositoryError(GitError):
    """The repository is Siamang's own (ARCHITECTURE §6)."""

    code = GitErrorCode.PROTECTED_REPOSITORY


class UnexpectedGitError(GitError):
    code = GitErrorCode.UNKNOWN_GIT_ERROR


# Ordered (pattern, error type, message). Matched against stderr with LC_ALL=C.
_STDERR_RULES: tuple[tuple[re.Pattern[str], type[GitError], str], ...] = (
    (re.compile(r"not a git repository"), NotRepositoryError, "not a Git repository"),
    (re.compile(r"cannot change to"), NotRepositoryError, "path does not exist or is not accessible"),
    (re.compile(r"must be run in a work tree"), InvalidRepositoryError, "not inside a Git work tree (bare repository?)"),
    (re.compile(r"dubious ownership"), GitPermissionError, "Git refuses a repository owned by another user"),
    (re.compile(r"(?i)permission denied"), GitPermissionError, "permission denied"),
    (re.compile(r"\.lock'?: File exists|another git process", re.I), RepositoryLockedError, "repository is locked by another Git process"),
    (re.compile(r"(?i)\bworktree\b"), WorktreeError, "worktree operation failed"),
)


def classify_failure(stderr: str, *, operation: str, repository_path: str | None, exit_code: int) -> GitError:
    """Map a non-zero Git exit to a typed error, from Git's (C-locale) stderr."""
    first = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    for pattern, cls, message in _STDERR_RULES:
        if pattern.search(stderr):
            return cls(message, operation=operation, repository_path=repository_path, detail=first)
    return GitCommandError(
        f"git exited with status {exit_code}", operation=operation, repository_path=repository_path, detail=first
    )
