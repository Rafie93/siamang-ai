"""Safety checks shared by every proposed change: paths, forbidden files, secrets, text, hashes.

Pure functions over strings. Nothing here touches the filesystem: a path is
judged by its text alone, and the same rules apply whether the path came
from a model, a caller, or a test.

* **Paths** are project-relative POSIX paths of plain segments: no absolute
  paths, ``~``, ``%`` (no encoded traversal), backslashes (no Windows or UNC
  paths), drive prefixes, ``.``/``..``/empty segments, control or format
  characters, or non-NFC text. These are the Phase 14 inspector rules, plus
  drive and normalization checks.
* **Forbidden files** are never proposed for create, modify, or delete:
  anything under a VCS directory (``.git/``, ``.hg/``, ``.svn/``) or a
  credential directory (``.ssh/``, ``.gnupg/``, ``.aws/``), and every name
  the analyst treats as secret-looking (``.env*``, ``*.pem``, ``*.key``,
  ``id_rsa*``, ``credentials.*``, ``secrets.*``, ...).
* **Secrets**: :func:`find_secret` reports the *kind* of credential material
  it found (never the value): private-key blocks, well-known token shapes,
  and credential-named keys assigned a literal value. Placeholders and
  environment lookups are not secrets. This is defensive detection, not a
  guarantee.
* **Text**: proposed content is UTF-8 text without NUL or other binary
  control characters, within a byte limit.
* **Immutability**: :func:`deep_freeze` turns validated JSON objects into
  read-only mappings and tuples all the way down (the shared
  Phase 20 JSON helpers also freeze metadata recursively); :func:`thaw` turns them back into plain
  JSON for hashing and ``to_dict``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from siamang.analyst.inspection import is_sensitive
from siamang.planner.request import has_control

PATH_MAX = 1024
SEGMENT_MAX = 255
FORBIDDEN_DIRS = frozenset({".git", ".hg", ".svn", ".ssh", ".gnupg", ".aws"})
_SEGMENT_RE = re.compile(r"^[^/\\%~]+\Z")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_BINARY_RE = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")  # Cc except \t \n \f \r

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_TOKEN_SHAPES = (
    ("api_key_token", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}")),
    ("gitlab_token", re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}")),
)
# KEY = "literal" / KEY: 'literal' / "KEY": "literal" (any language), and KEY=literal at line start (env-file style,
# no spaces around "=", so ``password = request.form[...]`` in code is not mistaken for one). A key starts only at a
# token boundary, so a long run of word characters is scanned once (linear), not retried from every position.
_QUOTED_RE = re.compile(r"""(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z0-9_.-]{1,80})["']?\s*(?::|=|:=|=>)\s*(?P<q>["'])(?P<val>[^"'\s]{1,512})(?P=q)""")
_ENV_RE = re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z0-9_.-]{1,80})=(?P<val>[^\s\"'#]{1,512})")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|[_.-])(?:api[_-]?key|apikey|secret|secret[_-]?key|client[_-]?secret|password|passwd|pwd|"
    r"token|access[_-]?token|auth[_-]?token|refresh[_-]?token|private[_-]?key|access[_-]?key)\Z"
)
_PLACEHOLDER_MARKS = ("${", "{{", "<", ">", "%(", "$(", "process.env", "environ", "getenv", "example", "xxxx",
                      "changeme", "change_me", "placeholder", "redacted", "dummy", "fake", "sample", "your",
                      "test", "none", "null", "todo", "...")
SECRET_VALUE_MIN = 8


def validate_path(path: Any) -> str:
    """A safe project-relative file path, or :class:`ValueError` naming the rule (never echoing the path)."""
    if not isinstance(path, str) or not path or len(path) > PATH_MAX:
        raise ValueError("path must be a non-empty project-relative string")
    if has_control(path):
        raise ValueError("path contains control or format characters")
    if unicodedata.normalize("NFC", path) != path:
        raise ValueError("path must be NFC-normalized")
    if path.startswith("/") or "\\" in path or _DRIVE_RE.match(path):
        raise ValueError("absolute, drive, UNC, or backslash paths are not accepted")
    for part in path.split("/"):
        if part in ("", ".", "..") or len(part) > SEGMENT_MAX or not _SEGMENT_RE.match(part):
            raise ValueError("path segments must be plain names (no .., ~, %, or empty segments)")
    return path


def forbidden_reason(path: str) -> str | None:
    """Why a (valid) path may never be changed by a proposal, or ``None``."""
    parts = path.split("/")
    if any(p.casefold() in FORBIDDEN_DIRS for p in parts[:-1]) or parts[-1].casefold() in FORBIDDEN_DIRS:
        return "version-control or credential directory"
    if any(is_sensitive(p) for p in parts):
        return "secret-looking file"
    return None


def _placeholder(value: str) -> bool:
    low = value.lower()
    return len(set(low)) <= 2 or any(mark in low for mark in _PLACEHOLDER_MARKS)


def find_secret(text: str) -> str | None:
    """The kind of credential material in ``text`` (e.g. ``"private_key"``), or ``None``. Never the value."""
    if not text:
        return None
    if _PRIVATE_KEY_RE.search(text):
        return "private_key"
    for kind, pattern in _TOKEN_SHAPES:
        if pattern.search(text):
            return kind
    for pattern in (_QUOTED_RE, _ENV_RE):
        for match in pattern.finditer(text):
            value = match.group("val")
            if (_SECRET_KEY_RE.search(match.group("key")) and len(value) >= SECRET_VALUE_MIN
                    and not _placeholder(value)):
                return "credential_assignment"
    return None


def check_text(text: Any, limit: int) -> bytes:
    """UTF-8 bytes of proposed file content, or :class:`ValueError` (binary-looking, too large, not text)."""
    if not isinstance(text, str):
        raise ValueError("content must be text")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("content is not valid UTF-8 text") from None
    if len(data) > limit:
        raise ValueError(f"content exceeds {limit} bytes")
    if _BINARY_RE.search(text):
        raise ValueError("content looks binary (NUL or control characters)")
    return data


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")


def deep_freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """A read-only copy of a JSON object at every depth (mappings -> MappingProxyType, lists -> tuples)."""
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({k: freeze(v) for k, v in item.items()})
        if isinstance(item, (list, tuple)):
            return tuple(freeze(v) for v in item)
        return item
    return freeze(json.loads(json.dumps(thaw(value))))


def thaw(value: Any) -> Any:
    """Plain JSON data (dicts and lists) from a frozen value."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value
