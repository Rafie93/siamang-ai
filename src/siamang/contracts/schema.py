"""Minimal declarative payload schema (stdlib only).

Deliberately small: just enough to express contract v0.1 payloads. Unknown
fields are always ignored (forward compatibility); only declared fields are
checked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# \Z, not $: `$` also matches before a trailing newline ("tsk_1\n" would pass).
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

KINDS = frozenset({"str", "text", "int", "number", "bool", "enum", "id", "timestamp", "object", "list", "any"})


@dataclass(frozen=True)
class F:
    """One field.

    kind:
      ``str``   non-empty string;  ``text`` any string (may be empty)
      ``int`` / ``number`` / ``bool``
      ``enum``  one of ``choices``
      ``id``    identifier (``ID_RE``)
      ``timestamp`` ISO-8601 UTC
      ``object`` nested ``fields`` (None = any JSON object)
      ``list``  items described by ``item``
      ``any``   any JSON value
    """

    kind: str
    required: bool = False
    nullable: bool = False
    choices: tuple[str, ...] = ()
    fields: Mapping[str, F] | None = None
    item: F | None = None
    min: float | None = None
    max: float | None = None
    max_items: int | None = None


@dataclass
class SchemaViolation:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class _Ctx:
    errors: list[SchemaViolation] = field(default_factory=list)

    def add(self, path: str, message: str) -> None:
        self.errors.append(SchemaViolation(path, message))


def check_object(value: Any, spec: Mapping[str, F], path: str = "payload") -> list[SchemaViolation]:
    ctx = _Ctx()
    _check(value, F("object", fields=spec), path, ctx)
    return ctx.errors


def is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check(value: Any, spec: F, path: str, ctx: _Ctx) -> None:
    if value is None:
        if not spec.nullable:
            ctx.add(path, "must not be null")
        return

    k = spec.kind
    if k == "any":
        return
    if k in ("str", "text", "enum", "id", "timestamp") and not isinstance(value, str):
        ctx.add(path, f"must be a string, got {type(value).__name__}")
        return
    if k == "str" and not value.strip():
        ctx.add(path, "must be a non-empty string")
    elif k == "enum" and value not in spec.choices:
        ctx.add(path, f"must be one of {list(spec.choices)}, got {value!r}")
    elif k == "id" and not ID_RE.match(value):
        ctx.add(path, f"invalid identifier {value!r}")
    elif k == "timestamp" and not is_utc_timestamp(value):
        ctx.add(path, f"must be an ISO-8601 UTC timestamp, got {value!r}")
    elif k == "bool" and not isinstance(value, bool):
        ctx.add(path, f"must be a boolean, got {type(value).__name__}")
    elif k == "int" and not _is_int(value):
        ctx.add(path, f"must be an integer, got {type(value).__name__}")
    elif k == "number" and not (_is_int(value) or isinstance(value, float)):
        ctx.add(path, f"must be a number, got {type(value).__name__}")
    elif k in ("int", "number"):
        if spec.min is not None and value < spec.min:
            ctx.add(path, f"must be >= {spec.min:g}")
        if spec.max is not None and value > spec.max:
            ctx.add(path, f"must be <= {spec.max:g}")
    elif k == "list":
        if not isinstance(value, list):
            ctx.add(path, f"must be a list, got {type(value).__name__}")
            return
        if spec.max_items is not None and len(value) > spec.max_items:
            ctx.add(path, f"must have at most {spec.max_items} items")
        if spec.item is not None:
            for i, item in enumerate(value):
                _check(item, spec.item, f"{path}[{i}]", ctx)
    elif k == "object":
        if not isinstance(value, dict):
            ctx.add(path, f"must be an object, got {type(value).__name__}")
            return
        for name, sub in (spec.fields or {}).items():
            if name not in value:
                if sub.required:
                    ctx.add(f"{path}.{name}", "required field missing")
                continue
            _check(value[name], sub, f"{path}.{name}", ctx)


def check_spec(spec: Mapping[str, F], path: str = "payload") -> list[str]:
    """Self-check a spec for well-formedness (used by ``siamang validate``)."""
    problems: list[str] = []
    for name, f in spec.items():
        p = f"{path}.{name}"
        if f.kind not in KINDS:
            problems.append(f"{p}: unknown kind {f.kind!r}")
        if f.kind == "enum" and not f.choices:
            problems.append(f"{p}: enum without choices")
        if f.kind == "list" and f.item is None:
            problems.append(f"{p}: list without item spec")
        if f.kind != "object" and f.fields is not None:
            problems.append(f"{p}: fields given for non-object")
        if f.fields:
            problems.extend(check_spec(f.fields, p))
        if f.item is not None and f.item.fields:
            problems.extend(check_spec(f.item.fields, f"{p}[]"))
    return problems
