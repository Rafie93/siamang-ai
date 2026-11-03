"""ToolWorkspace: the agent's view of the project. Reads come from the project plus its own edits; writes stay in memory.

The project is never written. Edits are an overlay (``FileChange``) that the sandbox applies to its throwaway copy
for every command, so a command sees exactly what the agent has done so far. The same path rules as for a proposed
patch apply: no secret-looking files, no ``.git``, no symlinks, no credential content in or out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from siamang.programmer.safety import check_text, find_secret, forbidden_reason, validate_path

READ_LIMIT = 24_000  # characters of one file shown to the agent
LIST_LIMIT = 200
FILE_LIMIT = 200_000  # bytes of one written file
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"})


class ToolError(Exception):
    """A tool call that cannot be honoured. The message is shown to the agent, so it carries no host path."""


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str  # create | modify | delete
    after_content: str | None = None


class ToolWorkspace:
    def __init__(self, project_root: str | Path, *, max_changes: int = 40):
        self._root = Path(project_root).resolve()
        if not self._root.is_dir():
            raise ValueError("project root is not a directory")
        self._edits: dict[str, FileChange] = {}
        self._max_changes = max_changes

    @property
    def root(self) -> Path:
        return self._root

    @property
    def changes(self) -> tuple[FileChange, ...]:
        return tuple(self._edits.values())

    def _path(self, raw) -> str:
        try:
            path = validate_path(raw)
        except Exception:
            raise ToolError("path must be a project-relative POSIX path") from None
        if forbidden_reason(path) is not None:
            raise ToolError("that file is off limits")
        return path

    def _on_disk(self, path: str) -> Path | None:
        target = self._root.joinpath(*path.split("/"))
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(self._root):
            return None
        return target

    def exists(self, path: str) -> bool:
        edit = self._edits.get(path)
        return edit.operation != "delete" if edit else self._on_disk(path) is not None

    def read(self, raw) -> str:
        path = self._path(raw)
        edit = self._edits.get(path)
        if edit is not None:
            if edit.operation == "delete":
                raise ToolError("file does not exist")
            text = edit.after_content or ""
        else:
            target = self._on_disk(path)
            if target is None:
                raise ToolError("file does not exist")
            try:
                text = target.read_bytes()[: READ_LIMIT * 4].decode("utf-8")
            except UnicodeDecodeError:
                raise ToolError("file is not UTF-8 text") from None
        if find_secret(text):
            raise ToolError("file withheld: it looks like it contains a credential")
        return text if len(text) <= READ_LIMIT else text[:READ_LIMIT] + "\n[truncated]"

    def list(self, raw="") -> list[str]:
        prefix = self._path(raw) if raw else ""
        found: set[str] = set()
        for current, dirs, names in os.walk(self._root, followlinks=False):
            here = Path(current)
            rel = here.relative_to(self._root)
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not (here / d).is_symlink()
                             and forbidden_reason((rel / d).as_posix()) is None)
            for name in names:
                relative = (rel / name).as_posix()
                if forbidden_reason(relative) is None and self._on_disk(relative) is not None:
                    found.add(relative)
        for path, edit in self._edits.items():
            (found.discard if edit.operation == "delete" else found.add)(path)
        listed = sorted(p for p in found if not prefix or p == prefix or p.startswith(prefix.rstrip("/") + "/"))
        return listed[:LIST_LIMIT]

    def write(self, raw, content) -> str:
        path = self._path(raw)
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        try:
            check_text(content, FILE_LIMIT)
        except Exception:
            raise ToolError("content must be UTF-8 text within the size limit") from None
        if find_secret(content):
            raise ToolError("content looks like it contains a credential; use configuration instead")
        existed = self.exists(path)
        self._claim(path)
        original = self._on_disk(path) is not None
        self._edits[path] = FileChange(path, "modify" if original else "create", content)
        return "modified" if existed else "created"

    def delete(self, raw) -> str:
        path = self._path(raw)
        if not self.exists(path):
            raise ToolError("file does not exist")
        if self._on_disk(path) is None:  # created by the agent itself: just forget it
            del self._edits[path]
            return "deleted"
        self._claim(path)
        self._edits[path] = FileChange(path, "delete")
        return "deleted"

    def _claim(self, path: str) -> None:
        if path not in self._edits and len(self._edits) >= self._max_changes:
            raise ToolError(f"at most {self._max_changes} files may change")
