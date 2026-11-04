"""AutonomyPolicy: the levels in docs/VISION.md as a table. Set by the operator's file, never by a request or a model.

=====  ======================  ========================  ==============
Level  Design                  Pull request              Merge
=====  ======================  ========================  ==============
0      owner                   owner                     owner
1      low                     owner                     owner
2      low, medium             low, medium + tests       owner
3      low, medium             low, medium + tests       low + CI green
=====  ======================  ========================  ==============

High risk stays with the owner at every level. A verdict only says a gate may pass: the proposal still goes through the
unchanged :class:`~siamang.approval.ApprovalService`, whose evidence requirements have no override.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from siamang.autonomy.risk import Risk, RiskLevel

AUTONOMY_ACTOR = "policy.autonomy"  # API identities cannot hold a dot, so no token can act under these names
LEAD_ACTOR = "lead.autonomy"
MERGE_METHODS = ("squash", "merge", "rebase")
LEVELS = (0, 1, 2, 3)


class Gate(StrEnum):
    DESIGN = "design"
    PUBLISH = "publish"
    MERGE = "merge"


_ALLOWED = {
    Gate.DESIGN: {1: {RiskLevel.LOW}, 2: {RiskLevel.LOW, RiskLevel.MEDIUM}, 3: {RiskLevel.LOW, RiskLevel.MEDIUM}},
    Gate.PUBLISH: {2: {RiskLevel.LOW, RiskLevel.MEDIUM}, 3: {RiskLevel.LOW, RiskLevel.MEDIUM}},
    Gate.MERGE: {3: {RiskLevel.LOW}},
}
_NOUN = {Gate.DESIGN: "desain", Gate.PUBLISH: "pull request", Gate.MERGE: "merge"}


@dataclass(frozen=True)
class Verdict:
    gate: Gate
    level: int
    risk: RiskLevel
    allowed: bool
    reasons: tuple[str, ...]  # why it stays with the owner, or the risk's own reasons when it does not

    @property
    def noun(self) -> str:
        return _NOUN[self.gate]

    @property
    def approval_reason(self) -> str:
        """Fixed wording for the approval record: one line, no paths or project text, which that record refuses."""
        return f"Level {self.level}: {_NOUN[self.gate]} berisiko {self.risk} memenuhi kebijakan otomatis."

    def to_dict(self) -> dict[str, Any]:
        return {"gate": str(self.gate), "level": self.level, "risk": str(self.risk), "allowed": self.allowed,
                "reasons": list(self.reasons)}


@dataclass(frozen=True)
class AutonomyPolicy:
    level: int = 0
    lead: bool = False  # Phase 30: the Lead settles what it can before the owner is asked
    lead_answers: int = 6  # questions answered per task
    lead_revisions: int = 1  # times per task the Lead sends held work back
    merge_method: str = "squash"

    def __post_init__(self) -> None:
        if isinstance(self.level, bool) or self.level not in LEVELS:
            raise ValueError("autonomy level must be 0, 1, 2 or 3")
        for name, low, high in (("lead_answers", 0, 20), ("lead_revisions", 0, 3)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if self.merge_method not in MERGE_METHODS:
            raise ValueError(f"merge_method must be one of {MERGE_METHODS}")

    def gate_open(self, gate: Gate) -> bool:
        """Whether this level can pass the gate at all. When it cannot, a stop there is the normal course of things."""
        return self.level in _ALLOWED[gate]

    def _verdict(self, gate: Gate, risk: Risk, blocks: list[str]) -> Verdict:
        blocks = list(blocks)
        if not self.gate_open(gate):
            blocks.insert(0, f"Level {self.level}: {_NOUN[gate]} selalu menunggu pemilik.")
        elif risk.level not in _ALLOWED[gate][self.level]:
            blocks.insert(0, f"Level {self.level}: {_NOUN[gate]} berisiko {risk.level} menunggu pemilik.")
        return Verdict(gate, self.level, risk.level, not blocks, tuple(blocks) + risk.reasons)

    def design(self, risk: Risk, *, division_failed: bool = False) -> Verdict:
        return self._verdict(Gate.DESIGN, risk, ["Pembagian kerja gagal, jadi desain ini perlu dilihat pemilik."] if division_failed else [])

    def publish(self, risk: Risk, *, test_passed: bool, has_branch: bool, compromises: tuple[str, ...] = ()) -> Verdict:
        blocks = []
        if not test_passed:
            blocks.append("Tidak ada test yang lulus di sandbox, jadi tidak ada bukti eksekusi.")
        if not has_branch:
            blocks.append("Task ini tidak punya repositori untuk dibuka pull request-nya.")
        blocks += [f"Kompromi pemulihan: {c}" for c in compromises]
        return self._verdict(Gate.PUBLISH, risk, blocks)

    def merge(self, risk: Risk, *, ci_state: str, ci_passed: bool, open_at_tested_commit: bool, base_is_default: bool,
              reviewers_object: bool) -> Verdict:
        blocks = []
        if not ci_passed:
            blocks.append(f"CI belum memberi bukti lulus (status: {ci_state}).")
        if not open_at_tested_commit:
            blocks.append("Pull request tidak terbuka pada commit yang diuji CI, atau masih draft.")
        if not base_is_default:
            blocks.append("Pull request tidak menuju branch utama.")
        if reviewers_object:
            blocks.append("Ada peninjau yang meminta perubahan.")
        return self._verdict(Gate.MERGE, risk, blocks)
