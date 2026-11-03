"""Workspace: a throwaway copy of the project with a patch applied. The project itself is never written.

The copy leaves out version control, credential-looking files, and symlinks, so what runs in the sandbox cannot
see the operator's secrets or follow a link out of the tree. Each limit is checked while copying.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from siamang.programmer.safety import forbidden_reason, validate_path
from siamang.sandbox.policy import SandboxPolicy

SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"})


class WorkspaceError(Exception):
    """The workspace could not be built within its bounds. The message never carries a path or content."""


def _open_up(path: Path) -> None:
    """Group/other may read and write: the sandbox user is not the owner, and this copy is thrown away."""
    mode = stat.S_IMODE(path.lstat().st_mode)
    path.chmod(mode | 0o666 | (0o111 if path.is_dir() or mode & 0o100 else 0))


def build(project_root: str | Path, changes: Iterable[Any], dest: Path, policy: SandboxPolicy) -> dict[str, int]:
    root = Path(project_root)
    if not root.is_dir():
        raise WorkspaceError("project root is not a directory")
    files = size = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        rel = here.relative_to(root)
        dirs[:] = [d for d in sorted(dirs) if d not in SKIPPED_DIRS and not (here / d).is_symlink()
                   and forbidden_reason((rel / d).as_posix()) is None]
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(names):
            source = here / name
            relative = (rel / name).as_posix()
            if source.is_symlink() or not source.is_file() or forbidden_reason(relative) is not None:
                continue
            files += 1
            size += source.stat().st_size
            if files > policy.max_files or size > policy.max_bytes:
                raise WorkspaceError("project is larger than the sandbox workspace limit")
            shutil.copyfile(source, dest / relative)
            shutil.copymode(source, dest / relative)
    applied = 0
    for change in changes:
        path = validate_path(change.path)
        if forbidden_reason(path) is not None:
            raise WorkspaceError("a change targets a file the sandbox never receives")
        target = dest.joinpath(*path.split("/"))
        if not target.resolve().is_relative_to(dest.resolve()):
            raise WorkspaceError("a change leaves the workspace")
        if str(change.operation) == "delete":
            if target.is_file() and not target.is_symlink():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.after_content or "", encoding="utf-8")
        applied += 1
    _open_up(dest)
    for current, dirs, names in os.walk(dest):
        for name in (*dirs, *names):
            _open_up(Path(current) / name)
    return {"files": files, "bytes": size, "changes_applied": applied}
