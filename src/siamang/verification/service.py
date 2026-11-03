"""VerificationService: run the operator's test and build commands in the sandbox against the patch.

The commands come from operator configuration, never from the repository or the patch: a patch that edited the
command to a weaker one would otherwise turn "pass" into a claim. A check that cannot run (no engine, no command)
is ``unavailable`` or ``not_configured``, which are not passes.

Acceptance criteria are listed with their coverage. A criterion counts as verified only when a check is mapped to
it; with no mapping it is reported ``not_verified``, so a green build never reads as "every criterion met".
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siamang.sandbox import SandboxService, SandboxStatus
from siamang.verification.report import (
    CRITERIA_LIMIT,
    CRITERION_CHARS,
    NOT_MAPPED,
    CheckOutcome,
    VerificationReport,
    VerificationStatus,
)

CHECKS_LIMIT = 4


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    covers: tuple[str, ...] = ()  # criteria (exact text) this check establishes

    def __post_init__(self) -> None:
        if not self.name or not self.argv:
            raise ValueError("a check needs a name and a command")


def parse_command(name: str, line: str) -> Check | None:
    """An operator's command line as an argv list (no shell). Empty means "not configured"."""
    try:
        argv = tuple(shlex.split(line or ""))
    except ValueError:
        raise ValueError(f"{name} command is not a valid command line") from None
    return Check(name, argv) if argv else None


class VerificationService:
    def __init__(self, sandbox: SandboxService, checks: Sequence[Check]):
        if not isinstance(sandbox, SandboxService):
            raise TypeError("sandbox must be the SandboxService")
        if len(checks) > CHECKS_LIMIT or not all(isinstance(c, Check) for c in checks):
            raise ValueError(f"at most {CHECKS_LIMIT} checks")
        self._sandbox = sandbox
        self._checks = tuple(checks)

    @property
    def configured(self) -> bool:
        return bool(self._checks)

    def verify(self, project_root: str | Path, changes: Iterable[Any], criteria: Iterable[str] = ()) -> VerificationReport:
        changes = tuple(changes)
        listed = tuple(str(c).strip()[:CRITERION_CHARS] for c in criteria if str(c).strip())[:CRITERIA_LIMIT]
        if not self._checks:
            return VerificationReport(VerificationStatus.NOT_CONFIGURED, (), self._coverage(listed, {}))
        outcomes: list[CheckOutcome] = []
        passed_names: set[str] = set()
        stopped = False
        for check in self._checks:
            if stopped:
                outcomes.append(CheckOutcome(check.name, "skipped", None, "", 0.0))
                continue
            result = self._sandbox.run(project_root, changes, check.argv)
            outcomes.append(CheckOutcome(check.name, str(result.status), result.exit_code, result.output, result.seconds))
            if result.status is SandboxStatus.PASSED:
                passed_names.add(check.name)
                continue
            stopped = True
            if result.status is SandboxStatus.UNAVAILABLE:
                return VerificationReport(VerificationStatus.UNAVAILABLE, tuple(outcomes),
                                          self._coverage(listed, {}), note=result.output)
        status = VerificationStatus.PASSED if not stopped else VerificationStatus.FAILED
        covered = {c.name: c.covers for c in self._checks if c.name in passed_names}
        return VerificationReport(status, tuple(outcomes), self._coverage(listed, covered))

    @staticmethod
    def _coverage(criteria: tuple[str, ...], covered: dict[str, tuple[str, ...]]) -> tuple[dict[str, str], ...]:
        established = {text: name for name, texts in covered.items() for text in texts}
        return tuple({"criterion": c, "status": "verified", "check": established[c]} if c in established
                     else {"criterion": c, "status": "not_verified", "reason": NOT_MAPPED} for c in criteria)
