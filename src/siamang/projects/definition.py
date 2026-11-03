"""Canonical project definition and field validation.

Everything here is pure: no filesystem, network, or process access.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.contracts.envelope import format_timestamp
from siamang.log import is_secret_name
from siamang.projects.errors import InvalidProjectError

# \Z, not $: `$` also matches before a trailing newline ("prj_x\n" would pass).
PROJECT_ID_RE = re.compile(r"^prj_[A-Za-z0-9][A-Za-z0-9_.:-]{0,123}\Z")
NAME_MAX = 100
DESCRIPTION_MAX = 2000
ROOT_PATH_MAX = 4096
METADATA_MAX_BYTES = 16 * 1024
METADATA_MAX_KEYS = 64
METADATA_MAX_DEPTH = 5
METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
# Metadata is data, never configuration that Siamang runs. Keys that read as
# executable configuration are refused outright, so no later component is
# tempted to interpret them.
METADATA_FORBIDDEN_KEYS = frozenset(
    {"command", "commands", "script", "scripts", "exec", "shell", "run", "hook", "hooks", "entrypoint"}
)


class ProjectType(StrEnum):
    """Descriptive only. It does not select agents, models, tools, or Git workflow."""

    SOFTWARE = "software"
    WEB = "web"
    MOBILE = "mobile"
    DESKTOP = "desktop"
    GAME = "game"
    RESEARCH = "research"
    DATA = "data"
    EDUCATION = "education"
    OTHER = "other"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    description: str
    project_type: ProjectType
    status: ProjectStatus
    root_path: str  # absolute, normalized POSIX path; a location reference only
    metadata: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status is ProjectStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "project_type": str(self.project_type),
            "status": str(self.status),
            "root_path": self.root_path,
            "metadata": json.loads(canonical_metadata(self.metadata)),
            "created_at": format_timestamp(self.created_at),
            "updated_at": format_timestamp(self.updated_at),
        }


# --- validation ---------------------------------------------------------------


def _has_control(text: str, allow: str = "") -> bool:
    return any(unicodedata.category(c).startswith("C") and c not in allow for c in text)


def validate_project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_RE.match(value):
        raise InvalidProjectError("project_id", "must look like prj_<id>")
    return value


def normalize_name(value: Any) -> tuple[str, str]:
    """Return ``(name, name_key)``. Whitespace is collapsed; ``name_key`` is case-folded."""
    if not isinstance(value, str):
        raise InvalidProjectError("name", "must be a string")
    if _has_control(value):
        raise InvalidProjectError("name", "must not contain control characters")
    name = " ".join(value.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise InvalidProjectError("name", f"must be 1-{NAME_MAX} characters")
    return name, name.casefold()


def validate_description(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidProjectError("description", "must be a string")
    if len(value) > DESCRIPTION_MAX:
        raise InvalidProjectError("description", f"must be at most {DESCRIPTION_MAX} characters")
    if _has_control(value, allow="\n\t"):
        raise InvalidProjectError("description", "must not contain control characters")
    return value.strip()


def parse_type(value: Any) -> ProjectType:
    try:
        return ProjectType(value)
    except ValueError:
        raise InvalidProjectError("project_type", f"must be one of {[t.value for t in ProjectType]}") from None


def normalize_root_path(value: Any) -> str:
    """Absolute POSIX path, normalized as a string. Touches no filesystem.

    ``~`` and relative paths are rejected here (callers such as the CLI
    expand them first) so the stored value never depends on the process's
    working directory. Symlinks are not resolved.
    """
    try:
        raw = os.fspath(value)
    except TypeError:
        raise InvalidProjectError("root_path", "must be a path string") from None
    if not isinstance(raw, str) or not raw:
        raise InvalidProjectError("root_path", "must be a non-empty path string")
    if _has_control(raw):
        raise InvalidProjectError("root_path", "must not contain control characters")
    if not raw.startswith("/"):
        raise InvalidProjectError("root_path", "must be an absolute path")
    path = os.path.normpath(raw)
    if path.startswith("//"):  # POSIX keeps a leading '//' as implementation-defined
        path = "/" + path.lstrip("/")
    if len(path) > ROOT_PATH_MAX:
        raise InvalidProjectError("root_path", f"must be at most {ROOT_PATH_MAX} characters")
    if PurePosixPath(path) == PurePosixPath("/"):
        raise InvalidProjectError("root_path", "must not be the filesystem root")
    home = os.path.expanduser("~")
    if home.startswith("/") and os.path.normpath(home) == path:
        raise InvalidProjectError("root_path", "must not be the home directory itself")
    return path


def canonical_metadata(value: Any) -> str:
    """Validate metadata and return its canonical JSON (sorted keys, compact)."""
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise InvalidProjectError("metadata", "must be an object")
    if len(value) > METADATA_MAX_KEYS:
        raise InvalidProjectError("metadata", f"must have at most {METADATA_MAX_KEYS} top-level keys")
    for key in value:
        if isinstance(key, str) and key.lower() in METADATA_FORBIDDEN_KEYS:
            raise InvalidProjectError("metadata", f"key {key!r} looks like executable configuration")
    _check_json(value, "metadata", 1)
    try:
        text = json.dumps(thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidProjectError("metadata", f"not JSON-serializable: {exc}") from None
    if len(text.encode("utf-8")) > METADATA_MAX_BYTES:
        raise InvalidProjectError("metadata", f"must serialize to at most {METADATA_MAX_BYTES} bytes")
    return text


def _check_json(value: Any, path: str, depth: int) -> None:
    if depth > METADATA_MAX_DEPTH:
        raise InvalidProjectError("metadata", f"nesting deeper than {METADATA_MAX_DEPTH} at {path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not METADATA_KEY_RE.match(key):
                raise InvalidProjectError("metadata", f"invalid key {key!r} at {path}")
            if is_secret_name(key):
                raise InvalidProjectError("metadata", f"key {key!r} looks like a credential")
            _check_json(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_json(item, f"{path}[{i}]", depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidProjectError("metadata", f"non-finite number at {path}")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise InvalidProjectError("metadata", f"unsupported value type {type(value).__name__} at {path}")


def freeze_metadata(text: str) -> Mapping[str, Any]:
    return freeze(json.loads(text))
