"""Central logging (stdlib ``logging``).

* Component = logger name (``siamang.<component>``).
* ``request_id`` / ``task_id`` / ``event_id`` come from :func:`log_context`
  (contextvars) or ``extra=``; they are emitted only when set.
* Console: human-readable text. File: JSON lines.
* Secret-looking values (``api_key=...``, ``token: ...``, ``password``,
  ``secret``, bearer tokens) are redacted from every record.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from siamang.config import LoggingConfig

ROOT = "siamang"
CONTEXT_FIELDS = ("request_id", "task_id", "event_id")
REDACTED = "[REDACTED]"

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("siamang_log_context", default={})

_SECRET_WORD = r"(?:api[_-]?key|token|secret|password|passwd|pwd)"
_SECRET_KEY = rf"[\w-]*{_SECRET_WORD}[\w-]*"
# A key starts only at a token boundary (or right after a quote), and a lookahead finds the secret word
# inside that one token: linear time. The older form retried ``[\w-]*`` from every position of a long
# word and was quadratic (4000 chars ~ 250 ms, 100k chars ~ minutes). Same matches, same output.
_SECRET_RE = re.compile(
    rf"(?i)(?P<key>(?:[\"'](?=[\w-])|(?<![\w-]))(?=[\w-]*?{_SECRET_WORD})[\w-]+[\"']?\s*[:=]\s*)"
    rf"(?P<q>[\"']?)(?P<val>[^\s\"',;}}]+)"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_NAME_RE = re.compile(rf"(?i)^{_SECRET_KEY}$")
# Credentials recognisable by their shape, with no key name beside them: build logs print them bare.
_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b")


def redact(text: str) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group('key')}{m.group('q')}{REDACTED}", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return _TOKEN_RE.sub(REDACTED, text)


def is_secret_name(name: str) -> bool:
    """True for key names that look like credentials (``api_key``, ``auth_token``, ...)."""
    return bool(_SECRET_NAME_RE.match(name))


def get_logger(component: str) -> logging.Logger:
    name = component if component.startswith(ROOT) else f"{ROOT}.{component}"
    return logging.getLogger(name)


@contextlib.contextmanager
def log_context(**ids: str | None) -> Iterator[None]:
    """Attach correlation IDs to every record logged inside the block."""
    unknown = set(ids) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"unknown log context fields: {sorted(unknown)}")
    merged = {**_context.get(), **{k: v for k, v in ids.items() if v}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


class _ContextFilter(logging.Filter):
    """Injects context IDs and redacts secrets. Never drops records."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context.get()
        for name in CONTEXT_FIELDS:
            if not getattr(record, name, None) and name in ctx:
                setattr(record, name, ctx[name])
        record.msg = redact(record.getMessage())
        record.args = None
        for key, value in list(record.__dict__.items()):
            if _SECRET_NAME_RE.match(key) and value is not None:
                setattr(record, key, REDACTED)
        record.component = record.name.removeprefix(f"{ROOT}.") if record.name != ROOT else ROOT
        return True


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ids = " ".join(f"{n}={getattr(record, n)}" for n in CONTEXT_FIELDS if getattr(record, n, None))
        line = f"{_timestamp(record)} {record.levelname:<8} {getattr(record, 'component', record.name)}: {record.getMessage()}"
        if ids:
            line += f" [{ids}]"
        if record.exc_info:
            line += "\n" + redact(self.formatException(record.exc_info))
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "message": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            if getattr(record, name, None):
                out[name] = getattr(record, name)
        if record.exc_info:
            out["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


def configure_logging(config: LoggingConfig, *, stream=None) -> logging.Logger:
    """(Re)configure the ``siamang`` logger tree. Idempotent."""
    logger = logging.getLogger(ROOT)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for f in list(logger.filters):
        logger.removeFilter(f)

    logger.setLevel(config.level.upper())
    logger.propagate = False

    if config.console:
        console = logging.StreamHandler(stream or sys.stderr)
        console.setFormatter(TextFormatter())
        console.addFilter(_ContextFilter())
        logger.addHandler(console)
    if config.file is not None:
        path = Path(config.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        fh.addFilter(_ContextFilter())
        logger.addHandler(fh)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger
