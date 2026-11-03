"""RecoveryEngine: run stages in order; when one fails, escalate through three tiers before giving up.

::

    tier 1  the stage again, with the failure as feedback           (retries)
    tier 2  an advisor changes the approach or splits the stage     (advisor_rounds)
    tier 3  a replanner rewrites the stages that remain             (replans)
    else    stop, with the stage and the reason that is true

Every tier is bounded and the whole run has an attempt budget, so recovery cannot loop. A stage that succeeded
(and was checkpointed) is never run again on resume. A compromise, from a stage or from tier 2/3, is recorded as
debt and reaches the pull request. A stage is judged only by its own ``StageOutcome``; an exception is a failure
whose reason is the exception type, never its message.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from siamang.log import get_logger
from siamang.recovery.checkpoint import CheckpointStore
from siamang.recovery.debt import DebtItem

log = get_logger("recovery")


@dataclass(frozen=True)
class StageContext:
    stage: str
    attempt: int  # 1-based, across all tiers for this stage
    tier: int  # 1 = plain/feedback retry, 2 = advised, 3 = after a replan
    feedback: str = ""  # why the previous attempt failed
    approach: str = ""  # tier 2: the advisor's instruction
    subtask: str = ""  # tier 2 split: the part being run
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)  # finished stages, by name


@dataclass(frozen=True)
class StageOutcome:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    failure: str = ""
    compromise: str = ""  # something was left undone or weakened; it becomes debt


@dataclass(frozen=True)
class Stage:
    name: str
    run: Callable[[StageContext], StageOutcome]


@dataclass(frozen=True)
class Advice:
    action: str  # change_approach | split | give_up
    approach: str = ""
    subtasks: tuple[str, ...] = ()
    compromise: str = ""


class RecoveryStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class RecoveryPolicy:
    retries: int = 1  # tier 1: extra attempts after the first
    advisor_rounds: int = 1  # tier 2: pieces of advice per stage
    replans: int = 1  # tier 3: for the whole run
    max_attempts: int = 12  # all stage attempts in the run
    max_subtasks: int = 4

    def __post_init__(self) -> None:
        for name, low, cap in (("retries", 0, 3), ("advisor_rounds", 0, 2), ("replans", 0, 2),
                               ("max_attempts", 1, 40), ("max_subtasks", 1, 8)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= cap:
                raise ValueError(f"{name} must be an integer in [{low}, {cap}]")


@dataclass(frozen=True)
class RecoveryResult:
    status: RecoveryStatus
    completed: tuple[str, ...]
    resumed: tuple[str, ...]
    stopped_at: str
    reason: str
    attempts: tuple[dict[str, Any], ...]
    debt: tuple[DebtItem, ...]
    tiers_used: tuple[int, ...]
    outputs: dict[str, dict[str, Any]]

    @property
    def ok(self) -> bool:
        return self.status is RecoveryStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {"status": str(self.status), "completed": list(self.completed), "resumed": list(self.resumed),
                "stopped_at": self.stopped_at, "reason": self.reason, "attempts": list(self.attempts),
                "debt": [d.to_dict() for d in self.debt], "tiers_used": list(self.tiers_used)}


class _Budget(Exception):
    pass


class RecoveryEngine:
    def __init__(self, advisor: Callable[[str, Sequence[str]], Advice] | None = None,
                 replanner: Callable[[Sequence[str], str], Sequence[Stage]] | None = None,
                 checkpoints: CheckpointStore | None = None, policy: RecoveryPolicy | None = None):
        self._advisor, self._replanner, self._checkpoints = advisor, replanner, checkpoints
        self.policy = policy or RecoveryPolicy()

    def run(self, run_id: str, stages: Sequence[Stage], *, keep_checkpoint: bool = False) -> RecoveryResult:
        """``keep_checkpoint``: leave the checkpoint after success, for a caller that clears it once its own work is saved."""
        pending = list(stages)
        names = [s.name for s in pending]
        if not pending or len(set(names)) != len(names) or not all(isinstance(s, Stage) and s.name for s in pending):
            raise ValueError("stages need distinct, non-empty names")
        done = self._checkpoints.load(run_id) if self._checkpoints else {}
        outputs: dict[str, dict[str, Any]] = {}
        completed: list[str] = []
        resumed: list[str] = []
        attempts: list[dict[str, Any]] = []
        debt: list[DebtItem] = []
        tiers: set[int] = set()
        replans = 0

        def result(status, stopped_at="", reason=""):
            return RecoveryResult(status, tuple(completed), tuple(resumed), stopped_at, reason, tuple(attempts),
                                  tuple(debt), tuple(sorted(tiers)), outputs)

        def attempt(stage: Stage, tier: int, **extra) -> StageOutcome:
            if len(attempts) >= self.policy.max_attempts:
                raise _Budget
            number = 1 + sum(1 for a in attempts if a["stage"] == stage.name)
            context = StageContext(stage.name, number, tier, outputs={k: dict(v) for k, v in outputs.items()}, **extra)
            try:
                outcome = stage.run(context)
                if not isinstance(outcome, StageOutcome):
                    outcome = StageOutcome(False, failure="stage returned no outcome")
            except Exception as exc:
                outcome = StageOutcome(False, failure=f"stage raised {type(exc).__name__}")
            attempts.append({"stage": stage.name, "tier": tier, "attempt": number, "ok": outcome.ok,
                             **({"failure": outcome.failure[:300]} if not outcome.ok else {})})
            return outcome

        def accept(stage: Stage, outcome: StageOutcome) -> None:
            outputs[stage.name] = outcome.output
            completed.append(stage.name)
            if outcome.compromise:
                debt.append(DebtItem(stage.name, 0, outcome.compromise))
            if self._checkpoints:
                self._checkpoints.save(run_id, stage.name, outcome.output)

        index = 0
        while index < len(pending):
            stage = pending[index]
            if stage.name in done:
                outputs[stage.name] = done[stage.name]
                completed.append(stage.name)
                resumed.append(stage.name)
                index += 1
                continue
            try:
                failure = self._recover(stage, attempt, accept, debt, tiers, replans)
            except _Budget:
                return result(RecoveryStatus.STOPPED, stage.name, "the attempt budget for this run is used up")
            if failure is None:
                index += 1
                continue
            if self._replanner and replans < self.policy.replans:
                replans += 1
                tiers.add(3)
                try:
                    fresh = list(self._replanner([s.name for s in pending[index:]], failure))
                except Exception:
                    fresh = []
                taken = set(completed)
                if fresh and all(isinstance(s, Stage) and s.name and s.name not in taken for s in fresh) \
                        and len({s.name for s in fresh}) == len(fresh):
                    debt.append(DebtItem(stage.name, 3, f"remaining work was replanned after: {failure}"))
                    pending[index:] = fresh
                    continue
            return result(RecoveryStatus.STOPPED, stage.name, failure)
        if self._checkpoints and not keep_checkpoint:
            self._checkpoints.clear(run_id)
        return result(RecoveryStatus.COMPLETED)

    def _recover(self, stage, attempt, accept, debt, tiers, replans) -> str | None:
        """Tiers 1 and 2 for one stage. ``None`` on success, otherwise the last failure."""
        feedback = ""
        for _ in range(1 + self.policy.retries):
            outcome = attempt(stage, 3 if replans else 1, feedback=feedback)
            if outcome.ok:
                accept(stage, outcome)
                return None
            feedback = outcome.failure or "the stage failed"
            if self.policy.retries:
                tiers.add(1)
        history = [feedback]
        for _ in range(self.policy.advisor_rounds if self._advisor else 0):
            tiers.add(2)
            try:
                advice = self._advisor(stage.name, tuple(history))
            except Exception:
                return feedback
            if not isinstance(advice, Advice) or advice.action == "give_up":
                return feedback
            if advice.action == "change_approach" and advice.approach.strip():
                outcome = attempt(stage, 2, feedback=feedback, approach=advice.approach)
            elif advice.action == "split" and 1 <= len(advice.subtasks) <= self.policy.max_subtasks:
                outcome = self._split(stage, attempt, advice, feedback)
            else:
                return feedback
            if outcome.ok:
                if advice.compromise:
                    debt.append(DebtItem(stage.name, 2, advice.compromise))
                accept(stage, outcome)
                return None
            feedback = outcome.failure or "the stage failed"
            history.append(feedback)
        return feedback

    @staticmethod
    def _split(stage, attempt, advice, feedback) -> StageOutcome:
        merged: dict[str, Any] = {}
        notes: list[str] = []
        for part in advice.subtasks:
            outcome = attempt(stage, 2, feedback=feedback, approach=advice.approach, subtask=part)
            if not outcome.ok:
                return StageOutcome(False, failure=f"part '{part}' failed: {outcome.failure}")
            merged[part] = outcome.output
            if outcome.compromise:
                notes.append(outcome.compromise)
        return StageOutcome(True, merged, compromise="; ".join(notes))
