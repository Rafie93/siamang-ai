"""VerificationReport: what real execution established about one patch. ``passed`` comes only from exit codes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

OUTPUT_TAIL = 2000  # characters of a failing check's output that go back to the Programmer
CRITERIA_LIMIT = 30
CRITERION_CHARS = 300
NOT_MAPPED = "no executable check is mapped to this criterion"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"  # no container engine, or the sandbox could not run the check
    NOT_CONFIGURED = "not_configured"  # the operator named no check


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    status: str  # a SandboxStatus value, or "skipped" after an earlier failure
    exit_code: int | None
    output: str
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "exit_code": self.exit_code, "output": self.output,
                "seconds": self.seconds}


@dataclass(frozen=True)
class VerificationReport:
    status: VerificationStatus
    checks: tuple[CheckOutcome, ...]
    criteria: tuple[dict[str, str], ...] = ()
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.PASSED

    def blockers(self) -> list[str]:
        """Short lines for the owner: why this is not a pass."""
        if self.status is VerificationStatus.NOT_CONFIGURED:
            return ["Tidak ada perintah test atau build yang dikonfigurasi, jadi tidak ada bukti eksekusi."]
        if self.status is VerificationStatus.UNAVAILABLE:
            return [self.note or "Sandbox tidak tersedia, jadi kode tidak dijalankan."]
        return [f"{c.name}: {c.status}" + (f" (exit {c.exit_code})" if c.exit_code is not None else "")
                for c in self.checks if c.status not in ("passed", "skipped")]

    def feedback(self) -> dict[str, Any]:
        """Bounded input for the next Programmer round: which checks failed and the end of their output.

        No ``status`` key: the loop refuses verdict-style keys in its input.
        """
        failed = [{"check": c.name, "how": c.status, "exit_code": c.exit_code, "output_tail": c.output[-OUTPUT_TAIL:]}
                  for c in self.checks if c.status not in ("passed", "skipped")]
        return {"failed_checks": failed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationReport:
        """Rebuild a stored report (a checkpoint). Anything malformed raises: a run never resumes on a guess."""
        checks = tuple(CheckOutcome(str(c["name"]), str(c["status"]), c["exit_code"], str(c["output"]),
                                    float(c["seconds"])) for c in data["checks"])
        return cls(VerificationStatus(data["status"]), checks, tuple(dict(c) for c in data["criteria"]),
                   str(data.get("note", "")))

    def to_dict(self) -> dict[str, Any]:
        verified = sum(1 for c in self.criteria if c["status"] == "verified")
        return {"status": str(self.status), "passed": self.passed, "note": self.note,
                "checks": [c.to_dict() for c in self.checks], "criteria": list(self.criteria),
                "criteria_verified": verified, "criteria_total": len(self.criteria)}
