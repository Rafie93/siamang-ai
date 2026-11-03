"""ProjectSnapshot: what the Programmer may know about current files. Read-only, through ProjectInspector.

The Programmer reads files only through the Phase 14
:class:`~siamang.analyst.inspection.ProjectInspector` (registered root only,
relative paths, symlinks never followed, secret-looking files never opened,
every read budgeted). It never imports ``os`` and has no write API.

Two uses:

* :meth:`before`: the before-state of a path a change proposes to touch.
  Parent directories are *listed*, never assumed: a missing entry is
  ``absent``; a regular file is read and, if it is exact UTF-8 text,
  ``observed`` with the SHA-256 of its bytes; anything that cannot be
  established (too large, binary, budget spent, vanished) is ``unverified``
  with no hash. A path through a symlink, a file used as a directory, a
  directory or special file as the target, or a name that differs from an
  existing entry only by case is refused outright.
* :meth:`context_text`: the text of a file to show the model, if it is
  small, plain text, and free of credential material and machine paths.

The project can change while it is read; the snapshot is best effort. The
before hash is exactly what lets a later Approval System notice that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from siamang.analyst.errors import AnalysisError, AnalysisProjectUnavailableError, LimitReachedError
from siamang.analyst.inspection import Entry, EntryKind, FileText, ProjectInspector
from siamang.analyst.policy import LIMITS as ANALYSIS_LIMITS
from siamang.analyst.policy import AnalysisPolicy
from siamang.programmer.change import BeforeState
from siamang.programmer.errors import ChangeRejectedError, ProgrammingProjectUnavailableError
from siamang.programmer.policy import ProgrammingPolicy
from siamang.programmer.safety import content_hash, find_secret


class _Lookup(StrEnum):
    ABSENT = "absent"
    FILE = "file"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Before:
    state: BeforeState
    hash: str | None = None
    note: str | None = None  # why it is unverified


UNLISTED = "its directory could not be listed within the budget"  # existence itself not established


def _cap(name: str, value: int) -> int:
    return max(1, min(value, ANALYSIS_LIMITS[name][1]))


class ProjectSnapshot:
    __slots__ = ("_inspector", "_policy", "_dirs", "_texts")

    def __init__(self, root_path: str, policy: ProgrammingPolicy):
        if not isinstance(policy, ProgrammingPolicy):
            raise TypeError("policy must be a ProgrammingPolicy")
        files = policy.max_context_files + policy.max_changes
        size = max(policy.max_file_size, policy.max_context_file_size)
        budget = AnalysisPolicy(
            max_files=_cap("max_files", files),
            max_file_size=_cap("max_file_size", size),
            max_total_bytes=_cap("max_total_bytes", files * size),
            max_directory_entries=ANALYSIS_LIMITS["max_directory_entries"][1],
        )
        failure = None
        try:
            self._inspector = ProjectInspector(root_path, budget)
        except AnalysisProjectUnavailableError as exc:
            failure = ProgrammingProjectUnavailableError("project root is not usable", cause=exc.cause)
        if failure is not None:
            raise failure
        self._policy = policy
        self._dirs: dict[str, dict[str, Entry] | None] = {}
        self._texts: dict[str, FileText | None] = {}

    def usage(self) -> dict[str, int]:
        return self._inspector.usage()

    # -- before-state of a proposed change --------------------------------------------

    def before(self, path: str, change_id: str) -> Before:
        lookup = self._lookup(path, change_id)
        if lookup is _Lookup.ABSENT:
            return Before(BeforeState.ABSENT)
        if lookup is _Lookup.UNKNOWN:
            return Before(BeforeState.UNVERIFIED, note=UNLISTED)
        text = self._read(path)
        if text is None:
            return Before(BeforeState.UNVERIFIED, note="it could not be read within the budget")
        if text.too_large:
            return Before(BeforeState.UNVERIFIED, note="it is larger than the read limit")
        if text.binary or text.text is None:
            return Before(BeforeState.UNVERIFIED, note="it is not text")
        if "�" in text.text:  # not exact UTF-8: a hash of the decoded text would not match the bytes
            return Before(BeforeState.UNVERIFIED, note="it is not valid UTF-8")
        return Before(BeforeState.OBSERVED, content_hash(text.text.encode("utf-8")))

    def _lookup(self, path: str, change_id: str) -> _Lookup:
        parts = path.split("/")
        directory = "."
        for i, part in enumerate(parts):
            entries = self._list(directory)
            if entries is None:
                return _Lookup.UNKNOWN
            entry = entries.get(part)
            if entry is None:
                if any(name.casefold() == part.casefold() for name in entries):
                    raise ChangeRejectedError("path differs only by case from an existing entry",
                                              details=(change_id, path))
                return _Lookup.ABSENT
            if i < len(parts) - 1:
                if entry.kind is not EntryKind.DIRECTORY:
                    raise ChangeRejectedError("a parent of the path is not a plain directory (symlink, file, or "
                                              "special)", details=(change_id, path))
                directory = entry.path
            elif entry.kind is not EntryKind.FILE:
                raise ChangeRejectedError("the path is not a regular file (directory, symlink, or special)",
                                          details=(change_id, path))
        return _Lookup.FILE

    def _list(self, directory: str) -> dict[str, Entry] | None:
        if directory not in self._dirs:
            try:
                self._dirs[directory] = {e.name: e for e in self._inspector.list_dir(directory)}
            except AnalysisError:  # budget spent, vanished, or refused: not established
                self._dirs[directory] = None
        return self._dirs[directory]

    def _read(self, path: str) -> FileText | None:
        if path not in self._texts:
            try:
                self._texts[path] = self._inspector.read_text(path)
            except (LimitReachedError, AnalysisError):
                self._texts[path] = None
        return self._texts[path]

    # -- files shown to the model -----------------------------------------------------

    def context_text(self, path: str, machine_paths: tuple[str, ...]) -> tuple[str | None, str | None]:
        """``(text, None)`` if the file may be shown to the model, else ``(None, reason)``."""
        try:
            lookup = self._lookup(path, "context")
        except ChangeRejectedError:
            return None, "not a plain file"
        if lookup is not _Lookup.FILE:
            return None, "not found"
        text = self._read(path)
        if text is None or text.text is None or text.too_large or "�" in text.text:
            return None, "not readable text"
        if text.size > self._policy.max_context_file_size:
            return None, "too large"
        if find_secret(text.text):
            return None, "credential material"
        if any(p and p in text.text for p in machine_paths):
            return None, "machine paths"
        return text.text, None
