"""Typed views of Git state, and parsers for Git's machine-readable output.

Pure functions only: no processes, filesystem, or network. Every parser
reads NUL-separated (``-z``) output and splits fixed-width fields with a
bounded ``maxsplit``, so paths containing spaces, quotes, tabs, newlines,
``$()``, backticks, or non-UTF-8 bytes stay intact (undecodable bytes become
surrogate escapes, like ``os.fsdecode``).

None of these values is persisted: Git is the source of truth and is asked
again on every call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from siamang.git.errors import UnexpectedGitError, scrub

SHORT_COMMIT = 12


class Relation(StrEnum):
    """How the project's root relates to its repository's root."""

    ROOT = "root"  # the project root is the repository's top level
    NESTED = "nested"  # the project root is a subdirectory of the repository


class FileState(StrEnum):
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNTRACKED = "untracked"
    CONFLICTED = "conflicted"


class WorkspaceKind(StrEnum):
    MAIN = "main"
    LINKED = "linked"
    BARE = "bare"


def _short(commit: str | None) -> str | None:
    return commit[:SHORT_COMMIT] if commit else None


@dataclass(frozen=True)
class RepositoryLocation:
    """Where a project sits in Git.

    ``project_root`` is the registry's value, unchanged (never resolved).
    The other paths are Git's own absolute answers, which resolve symlinks.
    ``prefix`` is Git's ``--show-prefix`` (project root relative to the
    repository root, no trailing slash), so the relationship is computed by
    Git rather than by a Siamang-side ``realpath``.
    """

    project_root: str
    repository_root: str
    prefix: str
    git_dir: str
    common_git_dir: str

    @property
    def relation(self) -> Relation:
        return Relation.NESTED if self.prefix else Relation.ROOT

    @property
    def is_linked_worktree(self) -> bool:
        return self.git_dir != self.common_git_dir

    @property
    def path_aliased(self) -> bool:
        """True when the project path reaches the repository through a symlink (or other alias)."""
        expected = f"{self.repository_root.rstrip('/')}/{self.prefix}" if self.prefix else self.repository_root
        return expected != self.project_root

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "repository_root": self.repository_root,
            "prefix": self.prefix,
            "relation": str(self.relation),
            "path_aliased": self.path_aliased,
            "git_dir": self.git_dir,
            "common_git_dir": self.common_git_dir,
            "linked_worktree": self.is_linked_worktree,
        }


@dataclass(frozen=True)
class HeadInfo:
    commit: str | None  # None in an empty repository (unborn branch)
    branch: str | None  # None when detached
    detached: bool

    @property
    def short_commit(self) -> str | None:
        return _short(self.commit)

    @property
    def unborn(self) -> bool:
        return self.commit is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "short_commit": self.short_commit,
            "branch": self.branch,
            "detached": self.detached,
            "unborn": self.unborn,
        }


@dataclass(frozen=True)
class StatusEntry:
    """One changed path. ``path`` is relative to the repository root.

    ``index_status``/``worktree_status`` are Git's porcelain-v2 letters
    (``.`` unchanged, ``M``, ``T``, ``A``, ``D``, ``R``, ``C``, ``U``; ``?``
    for untracked).
    """

    path: str
    index_status: str
    worktree_status: str
    state: FileState
    original_path: str | None = None
    submodule: bool = False

    @property
    def staged(self) -> bool:
        return self.index_status not in (".", "?")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "original_path": self.original_path,
            "index_status": self.index_status,
            "worktree_status": self.worktree_status,
            "state": str(self.state),
            "staged": self.staged,
            "submodule": self.submodule,
        }


@dataclass(frozen=True)
class WorkingTreeStatus:
    """Whole-repository status. Untracked files make the tree not clean.

    ``ahead``/``behind`` compare with the *local* upstream tracking ref (no
    fetch); both are ``None`` without an upstream.
    """

    head: HeadInfo
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    entries: tuple[StatusEntry, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.entries

    def counts(self) -> dict[str, int]:
        out = {str(s): 0 for s in FileState}
        for e in self.entries:
            out[str(e.state)] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "head": self.head.to_dict(),
            "upstream": self.upstream,
            "ahead": self.ahead,
            "behind": self.behind,
            "counts": self.counts(),
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass(frozen=True)
class DiffFile:
    path: str
    original_path: str | None
    added: int | None  # None for binary files
    deleted: int | None

    @property
    def binary(self) -> bool:
        return self.added is None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "original_path": self.original_path, "added": self.added,
                "deleted": self.deleted, "binary": self.binary}


@dataclass(frozen=True)
class Diff:
    """Read-only diff: ``cached=False`` is work tree vs index, ``True`` is index vs HEAD."""

    cached: bool
    files: tuple[DiffFile, ...] = ()
    patch: str = ""
    truncated: bool = False  # the patch text hit the client's output limit

    def to_dict(self) -> dict[str, Any]:
        return {"cached": self.cached, "files": [f.to_dict() for f in self.files], "patch": self.patch,
                "truncated": self.truncated}


@dataclass(frozen=True)
class Workspace:
    """One Git worktree, as reported by ``git worktree list``.

    ``workspace_id`` is derived (``wsp_`` + 24 hex of SHA-256 over the common
    Git dir and the worktree path), not stored: stable while the worktree
    stays where it is.
    """

    workspace_id: str
    path: str
    repository_root: str  # path of the main worktree
    kind: WorkspaceKind
    commit: str | None
    branch: str | None
    detached: bool
    locked: bool = False
    prunable: bool = False
    current: bool = False  # this is the worktree the project root lives in

    @property
    def short_commit(self) -> str | None:
        return _short(self.commit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "path": self.path,
            "repository_root": self.repository_root,
            "kind": str(self.kind),
            "commit": self.commit,
            "short_commit": self.short_commit,
            "branch": self.branch,
            "detached": self.detached,
            "locked": self.locked,
            "prunable": self.prunable,
            "current": self.current,
        }


@dataclass(frozen=True)
class Remote:
    """A configured remote, read from local config only. URLs have credentials removed."""

    name: str
    fetch_url: str | None = None
    push_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "fetch_url": self.fetch_url, "push_url": self.push_url}


@dataclass(frozen=True)
class RepositoryInspection:
    project_id: str
    location: RepositoryLocation
    status: WorkingTreeStatus
    remotes: tuple[Remote, ...] = field(default=())

    @property
    def head(self) -> HeadInfo:
        return self.status.head

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "location": self.location.to_dict(),
            "head": self.head.to_dict(),
            "status": self.status.to_dict(),
            "remotes": [r.to_dict() for r in self.remotes],
        }


# --- parsers ----------------------------------------------------------------------


def _bad(what: str) -> UnexpectedGitError:
    return UnexpectedGitError(f"unrecognized git {what} output", operation=what)


def workspace_id(common_git_dir: str, path: str) -> str:
    digest = hashlib.sha256(f"{common_git_dir}\0{path}".encode("utf-8", "surrogateescape")).hexdigest()
    return f"wsp_{digest[:24]}"


def parse_location(project_root: str, text: str) -> RepositoryLocation:
    """Parse ``rev-parse --path-format=absolute --show-toplevel --show-prefix --git-dir --git-common-dir``."""
    lines = text.split("\n")
    if len(lines) < 4:
        raise _bad("rev-parse")
    toplevel, prefix, git_dir, common = lines[:4]
    if not toplevel.startswith("/") or not git_dir.startswith("/") or not common.startswith("/"):
        raise _bad("rev-parse")
    return RepositoryLocation(project_root, toplevel, prefix.rstrip("/"), git_dir, common)


def _classify(x: str, y: str) -> FileState:
    if x == "A":
        return FileState.ADDED
    if "D" in (x, y):
        return FileState.DELETED
    return FileState.MODIFIED


def parse_status(text: str) -> WorkingTreeStatus:
    """Parse ``status --porcelain=v2 -z --branch``."""
    tokens = text.split("\0")
    commit: str | None = None
    branch: str | None = None
    detached = False
    upstream: str | None = None
    ahead = behind = None
    entries: list[StatusEntry] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok.startswith("# "):
            key, _, value = tok[2:].partition(" ")
            if key == "branch.oid":
                commit = None if value == "(initial)" else value
            elif key == "branch.head":
                detached = value == "(detached)"
                branch = None if detached else value
            elif key == "branch.upstream":
                upstream = value
            elif key == "branch.ab":
                a, _, b = value.partition(" ")
                try:
                    ahead, behind = int(a.lstrip("+")), int(b.lstrip("-"))
                except ValueError:
                    raise _bad("status") from None
            continue
        kind = tok[0]
        if kind == "1":
            parts = tok.split(" ", 8)
            if len(parts) != 9:
                raise _bad("status")
            xy, sub, path = parts[1], parts[2], parts[8]
            entries.append(StatusEntry(path, xy[0], xy[1], _classify(xy[0], xy[1]), submodule=sub[0] == "S"))
        elif kind == "2":
            parts = tok.split(" ", 9)
            if len(parts) != 10 or i >= len(tokens):
                raise _bad("status")
            xy, sub, path = parts[1], parts[2], parts[9]
            original = tokens[i]
            i += 1
            if not original:
                raise _bad("status")
            state = FileState.RENAMED if "R" in xy else FileState.ADDED
            entries.append(StatusEntry(path, xy[0], xy[1], state, original_path=original, submodule=sub[0] == "S"))
        elif kind == "u":
            parts = tok.split(" ", 10)
            if len(parts) != 11:
                raise _bad("status")
            xy, sub, path = parts[1], parts[2], parts[10]
            entries.append(StatusEntry(path, xy[0], xy[1], FileState.CONFLICTED, submodule=sub[0] == "S"))
        elif kind == "?":
            entries.append(StatusEntry(tok[2:], "?", "?", FileState.UNTRACKED))
        elif kind == "!":
            continue  # ignored files are not requested; skip defensively
        else:
            raise _bad("status")
    head = HeadInfo(commit=commit, branch=branch, detached=detached)
    return WorkingTreeStatus(head, upstream, ahead, behind, tuple(entries))


def parse_numstat(text: str) -> tuple[DiffFile, ...]:
    """Parse ``diff --numstat -z`` (renames: ``A\\tD\\t\\0old\\0new\\0``)."""
    tokens = text.split("\0")
    files: list[DiffFile] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        parts = tok.split("\t", 2)
        if len(parts) != 3:
            raise _bad("diff")
        added, deleted, path = parts
        original = None
        if path == "":
            if i + 1 >= len(tokens):
                raise _bad("diff")
            original, path = tokens[i], tokens[i + 1]
            i += 2
            if not original or not path:
                raise _bad("diff")
        try:
            a = None if added == "-" else int(added)
            d = None if deleted == "-" else int(deleted)
        except ValueError:
            raise _bad("diff") from None
        files.append(DiffFile(path, original, a, d))
    return tuple(files)


def _commit_or_none(value: str | None) -> str | None:
    # An unborn branch is reported as the all-zero object name.
    return value if value and value.strip("0") else None


def parse_worktrees(text: str, common_git_dir: str, current_root: str | None = None) -> tuple[Workspace, ...]:
    """Parse ``worktree list --porcelain -z``. The first record is the main worktree."""
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for tok in text.split("\0"):
        if tok == "":
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = tok.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    if not records or any("worktree" not in r for r in records):
        raise _bad("worktree")
    main_path = records[0]["worktree"]
    out = []
    for index, r in enumerate(records):
        path = r["worktree"]
        if "bare" in r:
            kind = WorkspaceKind.BARE
        else:
            kind = WorkspaceKind.MAIN if index == 0 else WorkspaceKind.LINKED
        branch = r.get("branch")
        if branch is not None:
            branch = branch.removeprefix("refs/heads/")
        out.append(
            Workspace(
                workspace_id=workspace_id(common_git_dir, path),
                path=path,
                repository_root=main_path,
                kind=kind,
                commit=_commit_or_none(r.get("HEAD")),
                branch=branch,
                detached="detached" in r,
                locked="locked" in r,
                prunable="prunable" in r,
                current=current_root is not None and path == current_root,
            )
        )
    return tuple(out)


def parse_remotes(text: str) -> tuple[Remote, ...]:
    """Parse ``remote -v`` (``name\\turl (fetch|push)`` per line). Credentials are scrubbed."""
    found: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, sep, rest = line.partition("\t")
        if not sep:
            raise _bad("remote")
        url, _, kind = rest.rpartition(" ")
        kind = kind.strip("()")
        if kind not in ("fetch", "push") or not url:
            raise _bad("remote")
        found.setdefault(name, {})[kind] = scrub(url)
    return tuple(Remote(n, v.get("fetch"), v.get("push")) for n, v in sorted(found.items()))
