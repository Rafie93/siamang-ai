"""SandboxService: run one command against a copy of the project plus a patch, and report only what happened.

::

    project root ──copy (no .git, no secret-looking files, no symlinks) + patch──► temp workspace
                                                                                      │ mounted at /work
    argv (never a shell string) ─────────────────────────────► container: no network, no secrets, bounded
                                                                                      │
    SandboxResult  (passed | failed | timed_out | unavailable | error), output redacted and bounded

The project is read, never written. The workspace is deleted afterwards. A missing or broken container engine is
``unavailable``: it is never reported as a pass.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from siamang.log import get_logger, redact
from siamang.sandbox import workspace
from siamang.sandbox.policy import SandboxPolicy
from siamang.sandbox.runtime import ContainerRuntime

log = get_logger("sandbox")
ARGV_LIMIT = 64
ARG_LIMIT = 1000


class SandboxStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class SandboxResult:
    status: SandboxStatus
    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    truncated: bool
    seconds: float
    workspace: dict[str, int]
    policy: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.status is SandboxStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {"status": str(self.status), "argv": list(self.argv), "exit_code": self.exit_code, "output": self.output,
                "truncated": self.truncated, "seconds": self.seconds, "workspace": self.workspace,
                "policy": self.policy}


def check_argv(argv: Any) -> tuple[str, ...]:
    if (isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= ARGV_LIMIT
            or not all(isinstance(a, str) and a and len(a) <= ARG_LIMIT and "\x00" not in a for a in argv)):
        raise ValueError("a command is a non-empty list of strings, never a shell line")
    return tuple(argv)


class SandboxService:
    def __init__(self, runtime: ContainerRuntime, policy: SandboxPolicy):
        if not isinstance(policy, SandboxPolicy):
            raise TypeError("policy must be a SandboxPolicy")
        self._runtime = runtime
        self.policy = policy

    def available(self) -> bool:
        try:
            return bool(self._runtime.available())
        except Exception:
            return False

    def run(self, project_root: str | Path, changes: Iterable[Any], argv: Any) -> SandboxResult:
        command = check_argv(argv)

        def result(status, *, code=None, output="", truncated=False, seconds=0.0, stats=None):
            return SandboxResult(status, command, code, output, truncated, seconds, stats or {}, self.policy.to_dict())

        if not self.available():
            return result(SandboxStatus.UNAVAILABLE, output="no container engine is available")
        with tempfile.TemporaryDirectory(prefix="siamang-sandbox-") as tmp:
            dest = Path(tmp) / "work"
            dest.mkdir()
            try:
                stats = workspace.build(project_root, tuple(changes), dest, self.policy)
            except (workspace.WorkspaceError, ValueError, OSError) as exc:
                reason = str(exc) if isinstance(exc, workspace.WorkspaceError) else type(exc).__name__
                return result(SandboxStatus.ERROR, output=f"workspace could not be built: {reason}")
            raw = self._runtime.run(dest, command, self.policy)
        log.info("sandbox exit=%s timed_out=%s seconds=%s", raw.exit_code, raw.timed_out, raw.seconds)
        output = redact(raw.output)
        if raw.timed_out:
            status = SandboxStatus.TIMED_OUT
        elif raw.exit_code is None:
            status = SandboxStatus.ERROR
        else:
            status = SandboxStatus.PASSED if raw.exit_code == 0 else SandboxStatus.FAILED
        return result(status, code=raw.exit_code, output=output, truncated=raw.truncated, seconds=raw.seconds,
                      stats=stats)
