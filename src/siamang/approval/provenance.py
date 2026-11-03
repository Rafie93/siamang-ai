"""ApprovalProvenance: the exact evidence chain an approval is about, bound by stable IDs and content hashes.

::

    Task ─ Plan ─ SystemAnalysis ─ LoopResult ─► final Patch ─► final QAResult ─► final ReviewResult
     (task_id, project_id, plan_id+hash, analysis_id+hash, loop_id+hash, final_iteration,
      patch_id+hash, qa_id+hash, review_id+hash)

:func:`bind` builds it, and refuses (``approval_provenance_invalid``) any
chain where an artifact does not re-validate under its own phase's rules,
was altered after validation (forced fields, mutable state), or does not
belong to the others: a different task, project, plan, analysis, patch,
patch hash, QA result, QA hash, or a final artifact that is not the loop's
last evaluated round.

:func:`rederive` goes one step beyond consistency. It recomputes, from the
artifacts alone, what the producers would have said:

* the **Reviewer's** complete judgment (``reviewer.checks.judge``, pure and
  deterministic) under the review's own recorded policy: the recorded
  findings must be exactly those;
* **QA's** static checks that need no project access (patch integrity,
  Phase 15 path/secret/text rules, policy limits, plan steps, analysis
  agreement and scope): every finding they produce must be in the recorded
  QA result.

So a chain fabricated to look consistent (a review stripped of its
findings, a QA "pass" over a patch that breaks QA's static rules) is
refused. What cannot be re-derived without reading the project (QA's
before-state comparison) and who produced an artifact are **not** proven;
see docs/APPROVAL.md.

Nothing here reads the filesystem, calls a service, or keeps file contents:
provenance holds IDs and hashes only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from siamang.analyst.analysis import ANALYSIS_ID_RE, SystemAnalysis
from siamang.approval.errors import ApprovalProvenanceError
from siamang.loop.iteration import Outcome
from siamang.loop.result import LOOP_ID_RE, LoopResult, LoopStatus, StopReason
from siamang.loop.result import NOT_DONE as LOOP_NOT_DONE
from siamang.planner.plan import PLAN_ID_RE, Plan
from siamang.programmer.change import ProposedChange
from siamang.programmer.patch import PATCH_ID_RE, Patch
from siamang.programmer.safety import thaw
from siamang.qa.checks import Findings as QAFindings
from siamang.qa.checks import check_analysis, check_patch, check_plan, deep_frozen
from siamang.qa.policy import LIMITS as QA_LIMITS
from siamang.qa.policy import QAPolicy
from siamang.qa.request import HASH_RE
from siamang.qa.result import CHECKS as QA_CHECKS
from siamang.qa.result import NOT_VERIFIED as QA_NOT_VERIFIED
from siamang.qa.result import QA_ID_RE, QAResult, QAStatus
from siamang.reviewer.checks import judge
from siamang.reviewer.policy import LIMITS as REVIEWER_LIMITS
from siamang.reviewer.policy import ReviewerPolicy
from siamang.reviewer.result import DIMENSIONS, REVIEW_ID_RE, ReviewResult, ReviewStatus
from siamang.reviewer.result import NOT_VERIFIED as REVIEW_NOT_VERIFIED
from siamang.tasks.definition import validate_project_id, validate_task_id
from siamang.tasks.errors import InvalidTaskError

_IDS: dict[str, re.Pattern[str]] = {"plan_id": PLAN_ID_RE, "analysis_id": ANALYSIS_ID_RE, "patch_id": PATCH_ID_RE,
                                    "qa_id": QA_ID_RE, "review_id": REVIEW_ID_RE, "loop_id": LOOP_ID_RE}
_HASHES = ("plan_hash", "analysis_hash", "patch_hash", "qa_hash", "review_hash", "loop_hash")


def _broken(what: str, cause: str | None = None) -> ApprovalProvenanceError:
    return ApprovalProvenanceError(f"{what} does not belong to a verified evidence chain", cause=cause,
                                   details=(what,))


@dataclass(frozen=True)
class ApprovalProvenance:
    task_id: str
    project_id: str
    plan_id: str
    plan_hash: str
    analysis_id: str
    analysis_hash: str
    loop_id: str
    loop_hash: str
    final_iteration: int
    patch_id: str
    patch_hash: str
    qa_id: str
    qa_hash: str
    review_id: str
    review_hash: str

    def __post_init__(self) -> None:
        for name, value, check in (("task_id", self.task_id, validate_task_id),
                                   ("project_id", self.project_id, validate_project_id)):
            try:
                check(value)
            except InvalidTaskError:
                raise ApprovalProvenanceError(f"{name} is not a valid identifier", details=(name,)) from None
        fields = self.__dict__  # plain field read; no dynamic attribute lookup
        for name, pattern in _IDS.items():
            if not isinstance(fields[name], str) or not pattern.fullmatch(fields[name]):
                raise ApprovalProvenanceError(f"{name} is not a valid identifier", details=(name,))
        for name in _HASHES:
            if not isinstance(fields[name], str) or not HASH_RE.fullmatch(fields[name]):
                raise ApprovalProvenanceError(f"{name} must look like sha256:<64 hex>", details=(name,))
        if isinstance(self.final_iteration, bool) or not isinstance(self.final_iteration, int) or not (
                1 <= self.final_iteration <= 5):
            raise ApprovalProvenanceError("final_iteration must be a loop round number", details=("final_iteration",))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def provenance_hash(self) -> str:
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- re-validation (every invariant of every artifact runs again) ----------------------------


def _plan(plan: Any) -> Plan:
    if type(plan) is not Plan:
        raise _broken("plan")
    try:
        again = dataclasses.replace(plan, steps=tuple(dataclasses.replace(s) for s in plan.steps))
        if again.content() != plan.content():
            raise ValueError("altered")
        return again
    except Exception as exc:
        raise _broken("plan", type(exc).__name__) from None


def _analysis(analysis: Any) -> SystemAnalysis:
    if type(analysis) is not SystemAnalysis:
        raise _broken("analysis")
    try:
        again = dataclasses.replace(analysis, findings=tuple(dataclasses.replace(f) for f in analysis.findings),
                                    components=tuple(dataclasses.replace(c) for c in analysis.components))
        if again.content() != analysis.content():
            raise ValueError("altered")
        return again
    except Exception as exc:
        raise _broken("analysis", type(exc).__name__) from None


def _patch(patch: Any) -> Patch:
    """Every Phase 15 rule again (paths, forbidden files, secrets, text, conflicts); nothing forced in."""
    if type(patch) is not Patch or not isinstance(patch.changes, tuple) or not patch.changes:
        raise _broken("patch")
    try:
        fresh = []
        for change in patch.changes:
            if type(change) is not ProposedChange:
                raise TypeError("change")
            again = dataclasses.replace(change)
            if (again.to_dict() != change.to_dict() or again.after_hash != change.after_hash
                    or not deep_frozen(change.metadata) or not deep_frozen(change.evidence)):
                raise ValueError("altered")
            fresh.append(again)
        rebuilt = Patch(patch.patch_id, patch.task_id, patch.project_id, patch.summary, tuple(fresh))
        if rebuilt.content_hash != patch.content_hash:
            raise ValueError("altered")
        return rebuilt
    except Exception as exc:
        raise _broken("patch", type(exc).__name__) from None


def _validated_form(result: QAResult | ReviewResult) -> bool:
    status = QAStatus if type(result) is QAResult else ReviewStatus
    return (type(result.status) is status and type(result.findings) is tuple
            and all(type(f.severity) is not str and type(f.category) is not str and deep_frozen(f.metadata)
                    for f in result.findings)
            and deep_frozen(result.policy) and deep_frozen(result.metadata))


def _qa(qa: Any) -> QAResult:
    if type(qa) is not QAResult:
        raise _broken("qa_result")
    try:
        again = dataclasses.replace(qa, findings=tuple(dataclasses.replace(f) for f in qa.findings))
        if (again.content() != qa.content() or not _validated_form(qa) or not deep_frozen(qa.snapshot)
                or qa.checks != QA_CHECKS or qa.not_verified != QA_NOT_VERIFIED
                or qa.metadata.get("strategy") != "qa.static"):  # exactly what QA emits; nothing trimmed
            raise ValueError("altered")
        return again
    except Exception as exc:
        raise _broken("qa_result", type(exc).__name__) from None


def _review(review: Any) -> ReviewResult:
    if type(review) is not ReviewResult:
        raise _broken("review_result")
    try:
        again = dataclasses.replace(review, findings=tuple(dataclasses.replace(f) for f in review.findings))
        if (again.content() != review.content() or not _validated_form(review)
                or type(review.qa_status) is not QAStatus or review.dimensions != DIMENSIONS
                or review.not_verified != REVIEW_NOT_VERIFIED
                or review.metadata.get("strategy") != "review.static"):  # exactly what the Reviewer emits
            raise ValueError("altered")
        return again
    except Exception as exc:
        raise _broken("review_result", type(exc).__name__) from None


def _loop(loop: Any) -> LoopResult:
    if type(loop) is not LoopResult:
        raise _broken("loop_result")
    try:  # LoopResult's own invariants: final artifacts = last evaluated round, status = last evidence, ...
        rounds = tuple(dataclasses.replace(i, feedback=tuple(dataclasses.replace(f) for f in i.feedback))
                       for i in loop.iterations)
        again = dataclasses.replace(loop, iterations=rounds)
        if (again.content() != loop.content() or type(loop.status) is not LoopStatus
                or type(loop.stop_reason) is not StopReason or type(loop.iterations) is not tuple
                or not deep_frozen(loop.policy) or not deep_frozen(loop.metadata) or loop.not_done != LOOP_NOT_DONE
                or loop.metadata.get("strategy") != "loop.bounded"):  # exactly what the loop emits
            raise ValueError("altered")
        return again
    except Exception as exc:
        raise _broken("loop_result", type(exc).__name__) from None


# --- the chain ---------------------------------------------------------------------------------


def bind(task_id: str, project_id: str, plan: Any, analysis: Any, loop: Any) -> ApprovalProvenance:
    """The provenance of ``loop``'s final artifacts, or ``approval_provenance_invalid``. Pure; reads nothing."""
    plan, analysis, loop = _plan(plan), _analysis(analysis), _loop(loop)
    if loop.final_patch is None or loop.final_qa_result is None or loop.final_review_result is None:
        raise ApprovalProvenanceError("the loop produced no final patch, QA result, and review to decide on",
                                      details=("loop_result",))
    patch, qa, review = _patch(loop.final_patch), _qa(loop.final_qa_result), _review(loop.final_review_result)
    ids = (task_id, project_id)
    plan_hash, analysis_hash = plan.content_hash, analysis.content_hash
    patch_hash, qa_hash = patch.content_hash, qa.content_hash
    evaluated = [i for i in loop.iterations if i.outcome is Outcome.EVALUATED]
    last = evaluated[-1] if evaluated else None
    checks = (
        ("plan", (plan.task_id, plan.project_id) == ids),
        ("analysis", (analysis.task_id, analysis.project_id) == ids),
        ("loop_result", (loop.task_id, loop.project_id) == ids and loop.plan_id == plan.plan_id
         and loop.analysis_id == analysis.analysis_id and loop.metadata.get("plan_content_hash") == plan_hash
         and loop.metadata.get("analysis_content_hash") == analysis_hash),
        ("final_iteration", last is not None and last.patch_id == patch.patch_id and last.patch_hash == patch_hash
         and last.qa_id == qa.qa_id and last.review_id == review.review_id),
        ("patch", (patch.task_id, patch.project_id) == ids),
        ("qa_result", (qa.task_id, qa.project_id) == ids and qa.patch_id == patch.patch_id
         and qa.patch_hash == patch_hash and qa.metadata.get("plan_content_hash") == plan_hash
         and qa.metadata.get("analysis_content_hash") == analysis_hash),
        ("review_result", (review.task_id, review.project_id) == ids and review.plan_id == plan.plan_id
         and review.analysis_id == analysis.analysis_id and review.patch_id == patch.patch_id
         and review.patch_hash == patch_hash and review.qa_id == qa.qa_id and review.qa_hash == qa_hash
         and review.qa_status is qa.status and review.metadata.get("plan_content_hash") == plan_hash
         and review.metadata.get("analysis_content_hash") == analysis_hash),
    )
    for what, ok in checks:
        if not ok:
            raise _broken(what)
    return ApprovalProvenance(task_id, project_id, plan.plan_id, plan_hash, analysis.analysis_id, analysis_hash,
                              loop.loop_id, loop.content_hash, last.number, patch.patch_id, patch_hash, qa.qa_id,
                              qa_hash, review.review_id, review.content_hash)


def _policy(cls: type, limits: dict[str, Any], recorded: Any, statuses: str, what: str, *, advisory: bool = False) -> Any:
    try:
        data = thaw(recorded)
        # Recorded with the review; the policy itself refuses any code outside its allowlist.
        notes = {"advisory_codes": frozenset(data.get("advisory_codes", ()))} if advisory else {}
        policy = cls(**{name: data[name] for name in limits}, **{statuses: data[statuses]}, **notes)
        if policy.to_dict() != data:
            raise ValueError("altered")
        return policy
    except Exception as exc:
        raise _broken(what, type(exc).__name__) from None


def rederive(plan: Plan, analysis: SystemAnalysis, loop: LoopResult) -> None:
    """Recompute the Reviewer's judgment and QA's static checks from the artifacts; refuse contradictions."""
    patch, qa, review = loop.final_patch, loop.final_qa_result, loop.final_review_result
    qa_policy = _policy(QAPolicy, QA_LIMITS, qa.policy, "verifiable_statuses", "qa_result")
    out = QAFindings()
    _, changes = check_patch(patch, patch.content_hash, qa_policy, out)
    check_plan(changes, plan, out)
    check_analysis(changes, plan, analysis, out)
    recorded = {(f.code, str(f.severity), f.path) for f in qa.findings}
    if any((f.code, str(f.severity), f.path) not in recorded for f in out.items):
        raise _broken("qa_result", "omits_static_findings")
    review_policy = _policy(ReviewerPolicy, REVIEWER_LIMITS, review.policy, "reviewable_statuses", "review_result",
                            advisory=True)
    expected = judge(patch.changes, plan, analysis, qa, review_policy)
    if [f.to_dict() for f in expected] != [f.to_dict() for f in review.findings]:
        raise _broken("review_result", "findings_not_rederivable")
