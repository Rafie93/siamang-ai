"""Task domain: Task, TaskRequest, TaskFailure, TaskResult, and field validation.

Pure: no filesystem, process, network, model, tool, or Git access.

Metadata (and failure/result ``details``) reuse the Project Registry policy
(:func:`siamang.projects.definition.canonical_metadata`: JSON object only,
≤ 64 keys, ≤ 5 levels, ≤ 16 KiB, no NaN/Infinity, no credential-looking keys,
no executable-looking top-level keys). Tasks add one stricter rule: keys that
name commands, argv, paths, or credentials are refused at **any** depth.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from siamang.jsondata import freeze, thaw

from siamang.contracts.events import OUTCOMES, PAYLOADS, EventType
from siamang.contracts.schema import check_object
from siamang.log import redact
from siamang.projects.definition import PROJECT_ID_RE, canonical_metadata, freeze_metadata
from siamang.projects.errors import InvalidProjectError
from siamang.tasks.errors import InvalidTaskError
from siamang.tasks.lifecycle import TaskStatus

TASK_ID_RE = re.compile(r"^tsk_[A-Za-z0-9][A-Za-z0-9_.:-]{0,123}\Z")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
ERROR_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}\Z")
TITLE_MAX = 200
DESCRIPTION_MAX = 10_000
SUMMARY_MAX = 500
MESSAGE_MAX = 1000
STEP_MAX = 200
DEFAULT_MAX_RETRIES = 3
MAX_RETRIES_LIMIT = 10

# Refused as metadata/details keys at any depth (case-insensitive).
FORBIDDEN_KEYS = frozenset({
    "command", "commands", "cmd", "argv", "args", "shell", "shell_command", "script", "scripts", "exec",
    "python", "callable", "git_args", "path", "paths", "root_path", "repository_path", "cwd", "directory",
    "private_key", "credential", "credentials", "env", "environment",
})
# Fields a TaskRequest never accepts (raised with a dedicated message by from_dict).
FORBIDDEN_REQUEST_FIELDS = FORBIDDEN_KEYS | {"repository", "executable", "binary", "tool", "tool_id",
                                             "agent", "agent_id", "model_id", "branch", "worktree"}
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskSource(StrEnum):
    """Where the request came from. Identifiers only: no channel integration exists."""

    CLI = "cli"
    API = "api"
    TELEGRAM = "telegram"
    SYSTEM = "system"
    TEST = "test"


# --- timestamps -----------------------------------------------------------------------


def format_ts(dt: datetime) -> str:
    """Fixed-width UTC with milliseconds, so string order is time order."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_ts(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


# --- field validation -------------------------------------------------------------------


def _has_control(text: str, allow: str = "") -> bool:
    return any(unicodedata.category(c).startswith("C") and c not in allow for c in text)


def validate_task_id(value: Any, field_name: str = "task_id") -> str:
    if not isinstance(value, str) or not TASK_ID_RE.match(value):
        raise InvalidTaskError(field_name, "must look like tsk_<id>")
    return value


def validate_project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_RE.match(value):
        raise InvalidTaskError("project_id", "must look like prj_<id>")
    return value


def validate_request_id(value: Any) -> str:
    if not isinstance(value, str) or not REQUEST_ID_RE.match(value):
        raise InvalidTaskError("request_id", "must be a safe identifier")
    return value


def validate_description(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidTaskError("description", "must be a string")
    if _has_control(value, allow="\n\t"):
        raise InvalidTaskError("description", "must not contain control characters")
    text = value.strip()
    if not 1 <= len(text) <= DESCRIPTION_MAX:
        raise InvalidTaskError("description", f"must be 1-{DESCRIPTION_MAX} characters")
    return text


def normalize_title(value: Any, description: str) -> str:
    if value is None:  # derived: first non-empty line of the description
        value = next(line for line in description.splitlines() if line.strip())
        value = " ".join(value.split())
        return value if len(value) <= TITLE_MAX else value[: TITLE_MAX - 3] + "..."
    if not isinstance(value, str):
        raise InvalidTaskError("title", "must be a string")
    if _has_control(value):
        raise InvalidTaskError("title", "must not contain control characters")
    value = " ".join(value.split())
    if not 1 <= len(value) <= TITLE_MAX:
        raise InvalidTaskError("title", f"must be 1-{TITLE_MAX} characters")
    return value


def _enum(cls: type[StrEnum], value: Any, name: str) -> Any:
    try:
        return cls(value)
    except ValueError:
        raise InvalidTaskError(name, f"must be one of {[m.value for m in cls]}") from None


def parse_status(value: Any) -> TaskStatus:
    return _enum(TaskStatus, value, "status")


def parse_priority(value: Any) -> TaskPriority:
    return _enum(TaskPriority, value, "priority")


def parse_source(value: Any) -> TaskSource:
    return _enum(TaskSource, value, "source")


def validate_max_retries(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RETRIES_LIMIT:
        raise InvalidTaskError("max_retries", f"must be an integer in [0, {MAX_RETRIES_LIMIT}]")
    return value


def _forbidden_keys(value: Any, where: str, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_KEYS:
                raise InvalidTaskError(where, f"key {key!r} at {path} is not allowed (commands, paths, credentials)")
            _forbidden_keys(item, where, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _forbidden_keys(item, where, f"{path}[{i}]")


def canonical_json_object(value: Any, where: str = "metadata") -> str:
    """Project metadata policy + task key rules. Returns canonical JSON."""
    try:
        text = canonical_metadata(value)
    except InvalidProjectError as exc:
        raise InvalidTaskError(where, str(exc).split(": ", 1)[-1]) from None
    _forbidden_keys(value or {}, where, where)
    return text


def _clean_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    return redact(_URL_USERINFO_RE.sub(r"\1", text))[:limit]


# --- value objects ------------------------------------------------------------------------


def _frozen(text: str | None) -> Mapping[str, Any]:
    return freeze_metadata(text or "{}")


@dataclass(frozen=True)
class TaskFailure:
    """Normalized failure: no traceback, raw exception, environment, or credentials."""

    code: str
    message: str = ""
    retryable: bool = False
    step: str | int | None = None
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not ERROR_CODE_RE.match(self.code):
            raise InvalidTaskError("error.code", "must be an error code like TOOL_FAILED or not_repository")
        if not isinstance(self.retryable, bool):
            raise InvalidTaskError("error.retryable", "must be a boolean")
        if isinstance(self.step, bool) or not (self.step is None or isinstance(self.step, (str, int))):
            raise InvalidTaskError("error.step", "must be a string, an integer, or None")
        if isinstance(self.step, str):
            object.__setattr__(self, "step", _clean_text(self.step, STEP_MAX))
        object.__setattr__(self, "message", _clean_text(self.message, MESSAGE_MAX))
        object.__setattr__(self, "details", _frozen(canonical_json_object(self.details, "error.details")))

    @classmethod
    def from_exception(cls, exc: BaseException, *, step: str | int | None = None) -> TaskFailure:
        """Map any exception. SIAMANG errors keep their ``code``/``retryable``/``message``
        (redacted); anything else becomes ``INTERNAL`` with only the exception type name."""
        code = getattr(exc, "code", None)
        code = str(code) if code is not None and ERROR_CODE_RE.match(str(code)) else None
        retryable = getattr(exc, "retryable", False)
        message = getattr(exc, "message", None) if code else None
        return cls(
            code=code or "INTERNAL",
            message=message if isinstance(message, str) else "",
            retryable=retryable if isinstance(retryable, bool) and code else False,
            step=step,
            details={"cause": type(exc).__name__},
        )

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "step": self.step,
                "details": thaw(self.details)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str | None) -> TaskFailure | None:
        return None if text is None else cls(**json.loads(text))


_RESULT_SPEC = PAYLOADS[EventType.TASK_COMPLETED]["result"].fields


@dataclass(frozen=True)
class TaskResult:
    """What a completion records. ``result`` follows the contract's task.completed
    ``result`` object; ``details`` is free execution metadata (e.g. which model or
    tool a future agent used) under the metadata policy. The engine never produces
    these itself."""

    summary: str
    outcome: str = "success"
    result: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > SUMMARY_MAX:
            raise InvalidTaskError("result.summary", f"must be 1-{SUMMARY_MAX} characters")
        if _has_control(self.summary, allow="\n"):
            raise InvalidTaskError("result.summary", "must not contain control characters")
        if self.outcome not in OUTCOMES:
            raise InvalidTaskError("result.outcome", f"must be one of {list(OUTCOMES)}")
        text = canonical_json_object(self.result, "result.result")
        violations = check_object(json.loads(text), _RESULT_SPEC, "result.result")
        if violations:
            raise InvalidTaskError("result.result", str(violations[0]))
        object.__setattr__(self, "summary", self.summary.strip())
        object.__setattr__(self, "result", _frozen(text))
        object.__setattr__(self, "details", _frozen(canonical_json_object(self.details, "result.details")))

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "outcome": self.outcome, "result": thaw(self.result),
                "details": thaw(self.details)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str | None) -> TaskResult | None:
        return None if text is None else cls(**json.loads(text))


@dataclass(frozen=True)
class TaskRequest:
    """What a caller asks for. Validated by the engine on ``create``.

    ``source`` is required (no silent default). ``request_id`` makes creation
    idempotent; it is generated when omitted. ``task_id`` may be given
    explicitly (like ``project_id`` at project registration).
    """

    project_id: str
    description: str
    source: TaskSource | str
    title: str | None = None
    priority: TaskPriority | str = TaskPriority.NORMAL
    max_retries: int = DEFAULT_MAX_RETRIES
    parent_task_id: str | None = None
    metadata: Mapping[str, Any] | None = None
    request_id: str | None = None
    task_id: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> TaskRequest:
        """Strict constructor for untrusted input: unknown or execution-style fields are refused."""
        if not isinstance(data, Mapping):
            raise InvalidTaskError("request", "must be an object")
        known = {f for f in cls.__dataclass_fields__}
        for key in data:
            if not isinstance(key, str) or key not in known:
                if isinstance(key, str) and key.lower() in FORBIDDEN_REQUEST_FIELDS:
                    raise InvalidTaskError(key, "is not accepted (no commands, paths, tools, or agents)")
                raise InvalidTaskError(str(key), "unknown field")
        missing = [f for f in ("project_id", "description", "source") if f not in data]
        if missing:
            raise InvalidTaskError(missing[0], "is required")
        return cls(**dict(data))


@dataclass(frozen=True)
class Task:
    task_id: str
    request_id: str
    project_id: str
    parent_task_id: str | None
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    source: TaskSource
    retry_count: int
    max_retries: int
    metadata: Mapping[str, Any]
    last_error: TaskFailure | None
    result: TaskResult | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None

    @property
    def retries_left(self) -> int:
        return self.max_retries - self.retry_count

    def to_dict(self) -> dict[str, Any]:
        def ts(dt: datetime | None) -> str | None:
            return format_ts(dt) if dt else None

        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "project_id": self.project_id,
            "parent_task_id": self.parent_task_id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "priority": str(self.priority),
            "source": str(self.source),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "metadata": thaw(self.metadata),
            "last_error": self.last_error.to_dict() if self.last_error else None,
            "result": self.result.to_dict() if self.result else None,
            "created_at": ts(self.created_at),
            "updated_at": ts(self.updated_at),
            "started_at": ts(self.started_at),
            "completed_at": ts(self.completed_at),
            "failed_at": ts(self.failed_at),
            "cancelled_at": ts(self.cancelled_at),
        }
