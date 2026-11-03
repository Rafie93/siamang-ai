"""SandboxPolicy: the bounds of one sandboxed run. There is no "unlimited" value and no way to enable the network."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}\Z")

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "timeout_secs": (300, 1800),  # wall clock for one command
    "memory_mb": (1024, 8192),
    "cpus": (1, 8),
    "pids": (256, 4096),
    "output_bytes": (64 * 1024, 1024 * 1024),  # kept from stdout and stderr together; the rest is drained and dropped
    "max_files": (20_000, 100_000),  # files copied into the workspace
    "max_bytes": (200 * 1024 * 1024, 1024 * 1024 * 1024),  # bytes copied into the workspace
}


@dataclass(frozen=True)
class SandboxPolicy:
    image: str
    timeout_secs: int = LIMITS["timeout_secs"][0]
    memory_mb: int = LIMITS["memory_mb"][0]
    cpus: int = LIMITS["cpus"][0]
    pids: int = LIMITS["pids"][0]
    output_bytes: int = LIMITS["output_bytes"][0]
    max_files: int = LIMITS["max_files"][0]
    max_bytes: int = LIMITS["max_bytes"][0]

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not IMAGE_RE.fullmatch(self.image):
            raise ValueError("image must be a plain image reference")
        for name in LIMITS:
            value, cap = self.__dict__[name], LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")

    def to_dict(self) -> dict[str, Any]:
        return {"image": self.image, **{name: self.__dict__[name] for name in LIMITS},
                "network": "none", "secrets": "none", "root_filesystem": "read-only", "user": "unprivileged"}
