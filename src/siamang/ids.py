"""Identifier generation: ``<prefix>_<ULID>`` (time-ordered, URL-safe)."""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

PREFIXES = {
    "event": "evt",
    "request": "req",
    "task": "tsk",
    "approval": "apr",
    "model_request": "mrq",
    "project": "prj",
    "orchestration": "orc",
    "plan": "pln",
    "analysis": "ana",
    "programming": "prg",
    "patch": "pch",
    "qa": "qa",
    "review": "rev",
    "loop": "lop",
    "feature": "fea",
    "document": "doc",
    "draft": "dft",
}


def ulid(now_ms: int | None = None, rand: bytes | None = None) -> str:
    """26-char Crockford base32 ULID: 48-bit ms timestamp + 80 random bits."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    rnd = os.urandom(10) if rand is None else rand
    value = (ms << 80) | int.from_bytes(rnd, "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def new_id(kind: str) -> str:
    return f"{PREFIXES[kind]}_{ulid()}"
