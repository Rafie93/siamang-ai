"""ProjectInspector: the only filesystem access in the analyst, read-only and confined.

This is the one module of ``siamang.analyst`` allowed to import ``os``
(enforced by ``tests/unit/test_analyst_security.py``). It exposes two
operations, both on *project-relative* paths:

* :meth:`ProjectInspector.list_dir`: the entries of one directory, sorted;
* :meth:`ProjectInspector.read_text`: the text of one small regular file.

Rules, all deny-by-default:

* the root is the Project Registry's ``root_path`` (resolved once; it must
  be an existing directory), never a caller-supplied path;
* relative paths only: no absolute paths, ``..`` or ``.`` segments, empty
  segments, backslashes, ``~``, percent-encoding, or control characters;
* **symlinks are never followed**: every path component is checked with
  ``lstat``; a symlink is listed as a symlink but never entered or read, and
  the file itself is opened with ``O_NOFOLLOW``; the resolved path must
  still lie inside the root;
* only regular files are read; secret-looking files (``.env*``, ``*.pem``,
  ``*.key``, ``id_rsa*``, ``credentials.*``, ``secrets.*``, ...) are
  **never opened**, only reported by name;
* every operation charges a budget (files read, bytes read, entries
  listed) bounded by the :class:`AnalysisPolicy`; a spent budget raises
  :class:`LimitReachedError` deterministically. Files larger than
  ``max_file_size`` are reported, not read;
* nothing is written, created, renamed, deleted, executed, or watched.

The project can change while it is inspected; the result is a best-effort
snapshot (no locking, no watchers).
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum

from siamang.analyst.errors import AnalysisProjectUnavailableError, LimitReachedError, PathRejectedError
from siamang.analyst.policy import AnalysisPolicy

PATH_MAX = 1024
_SEGMENT_RE = re.compile(r"^[^/\\\x00-\x1f\x7f%~]+\Z")
_SENSITIVE_RE = re.compile(
    r"(?ix)^(?:"
    r"\.env(?:\..*)?|\.envrc|.*\.env|"
    r".*\.(?:pem|key|p12|pfx|jks|keystore|crt|cer|der|asc|gpg|kdbx|ovpn)|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|"
    r"(?:credentials?|secrets?)(?:\..*)?|"
    r"\.(?:netrc|npmrc|pypirc|pgpass|htpasswd|git-credentials)|"
    r".*\.secret(?:s)?(?:\..*)?|service[-_]account.*\.json"
    r")\Z"
)


class EntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True)
class Entry:
    path: str  # project-relative POSIX path
    name: str
    kind: EntryKind
    size: int | None  # regular files only
    sensitive: bool


@dataclass(frozen=True)
class FileText:
    path: str
    size: int
    text: str | None  # None when too large or binary
    too_large: bool = False
    binary: bool = False


def is_sensitive(name: str) -> bool:
    """Names that probably hold secrets. Such files are never opened."""
    return bool(_SENSITIVE_RE.match(name))


def validate_relative(path: object) -> tuple[str, ...]:
    """Segments of a safe project-relative path (``"."`` is the root: no segments)."""
    if not isinstance(path, str) or not path or len(path) > PATH_MAX:
        raise PathRejectedError("path must be a non-empty project-relative string")
    if path == ".":
        return ()
    if path.startswith("/") or "\\" in path:
        raise PathRejectedError("absolute or backslash paths are not accepted")
    parts = tuple(path.split("/"))
    for part in parts:
        if part in ("", ".", "..") or not _SEGMENT_RE.match(part):
            raise PathRejectedError("path segments must be plain names (no .., ~, %, or control characters)")
    return parts


class ProjectInspector:
    __slots__ = ("_root", "_policy", "_files", "_bytes", "_entries")

    def __init__(self, root_path: str, policy: AnalysisPolicy):
        if not isinstance(policy, AnalysisPolicy):
            raise TypeError("policy must be an AnalysisPolicy")
        if not isinstance(root_path, str) or not os.path.isabs(root_path):
            raise AnalysisProjectUnavailableError("project root must be an absolute registered path")
        root = os.path.realpath(root_path)  # the registered root itself may be a symlink; resolve it once
        try:
            info = os.stat(root)
        except OSError as exc:
            raise AnalysisProjectUnavailableError("project root is not accessible", cause=type(exc).__name__) from None
        if not stat.S_ISDIR(info.st_mode):
            raise AnalysisProjectUnavailableError("project root is not a directory")
        self._root = root
        self._policy = policy
        self._files = 0
        self._bytes = 0
        self._entries = 0

    # -- budget --------------------------------------------------------------------

    def usage(self) -> dict[str, int]:
        return {"files_read": self._files, "bytes_read": self._bytes, "entries_listed": self._entries}

    # -- operations ----------------------------------------------------------------

    def list_dir(self, path: str = ".") -> tuple[Entry, ...]:
        parts = validate_relative(path)
        full = self._walk(parts, final_is_dir=True)
        budget = self._policy.max_directory_entries - self._entries
        names: list[tuple[str, int, int]] = []  # (name, mode, size)
        try:
            with os.scandir(full) as scan:
                for item in scan:
                    if len(names) >= budget:
                        raise LimitReachedError("directory entry budget spent", details=(path,))
                    info = item.stat(follow_symlinks=False)
                    names.append((item.name, info.st_mode, info.st_size))
        except OSError as exc:
            raise PathRejectedError("directory cannot be listed", cause=type(exc).__name__, details=(path,)) from None
        self._entries += len(names)
        prefix = "" if not parts else "/".join(parts) + "/"
        out = []
        for name, mode, size in sorted(names):
            if stat.S_ISLNK(mode):
                kind = EntryKind.SYMLINK
            elif stat.S_ISDIR(mode):
                kind = EntryKind.DIRECTORY
            elif stat.S_ISREG(mode):
                kind = EntryKind.FILE
            else:
                kind = EntryKind.OTHER
            out.append(Entry(prefix + name, name, kind, size if kind is EntryKind.FILE else None,
                             kind is EntryKind.FILE and is_sensitive(name)))
        return tuple(out)

    def read_text(self, path: str) -> FileText:
        parts = validate_relative(path)
        if not parts:
            raise PathRejectedError("the project root is not a file")
        if is_sensitive(parts[-1]):
            raise PathRejectedError("secret-looking files are never read", details=(path,))
        full = self._walk(parts, final_is_dir=False)
        info = os.lstat(full)
        if self._files >= self._policy.max_files:
            raise LimitReachedError("file read budget spent", details=(path,))
        if info.st_size > self._policy.max_file_size:
            return FileText(path, info.st_size, None, too_large=True)
        if self._bytes + info.st_size > self._policy.max_total_bytes:
            raise LimitReachedError("total byte budget spent", details=(path,))
        data = self._read(full, info.st_size, path)
        self._files += 1
        self._bytes += len(data)
        if b"\x00" in data:
            return FileText(path, len(data), None, binary=True)
        return FileText(path, len(data), data.decode("utf-8", errors="replace"))

    # -- internals -----------------------------------------------------------------

    def _walk(self, parts: tuple[str, ...], *, final_is_dir: bool) -> str:
        """Check every component with lstat: directories all the way, no symlinks, inside the root."""
        current = self._root
        for i, part in enumerate(parts):
            current = os.path.join(current, part)
            try:
                info = os.lstat(current)
            except OSError:
                raise PathRejectedError("path does not exist in the project", details=("/".join(parts),)) from None
            if stat.S_ISLNK(info.st_mode):
                raise PathRejectedError("symlinks are not followed", details=("/".join(parts[: i + 1]),))
            last = i == len(parts) - 1
            want_dir = final_is_dir or not last
            if want_dir and not stat.S_ISDIR(info.st_mode):
                raise PathRejectedError("not a directory", details=("/".join(parts[: i + 1]),))
            if not want_dir and not stat.S_ISREG(info.st_mode):
                raise PathRejectedError("not a regular file", details=("/".join(parts),))
        resolved = os.path.realpath(current)
        if resolved != self._root and not resolved.startswith(self._root + os.sep):
            raise PathRejectedError("path resolves outside the project root", details=("/".join(parts),))
        return current

    def _read(self, full: str, size: int, path: str) -> bytes:
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        except OSError as exc:
            raise PathRejectedError("file cannot be opened", cause=type(exc).__name__, details=(path,)) from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PathRejectedError("not a regular file", details=(path,))
            limit = min(size, self._policy.max_file_size)
            chunks, remaining = [], limit
            while remaining > 0:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)
