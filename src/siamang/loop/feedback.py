"""LoopFeedback: one bounded, safe line of evidence handed to the next Programmer call. Data, never instruction.

Feedback is built only from the *findings* of the current iteration's
QAResult and ReviewResult (code, severity, message, path), never from task,
plan, or analysis text, file contents, or model output. Every line is
re-checked here as if it were hostile:

* ``code`` must be a dotted slug, ``severity`` ``error`` or ``warning``
  (info is not forwarded);
* ``message`` is single-line text of at most :data:`MESSAGE_MAX` characters
  with no control characters, credential material, or machine paths;
  anything else is replaced by :data:`WITHHELD`;
* ``target`` is a safe project-relative path or ``None``; a hostile path is
  dropped, never echoed.

Lines are ordered deterministically (errors first, QA before Reviewer, then
finding order), de-duplicated, and cut to the policy's item and byte bounds;
a cut is recorded as ``truncated``. The block given to the Programmer goes
through the Programmer's own request validation (key rules, deep freeze,
16 KiB cap) and context scrubbing before any model sees it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from siamang.loop.errors import LoopResultError
from siamang.loop.policy import LoopPolicy
from siamang.planner.request import has_control
from siamang.programmer.safety import find_secret
from siamang.qa.finding import safe_path
from siamang.qa.result import QAResult
from siamang.reviewer.result import ReviewResult

MESSAGE_MAX = 300
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_]{1,40}\Z")
WITHHELD = "Finding details withheld (unsafe to forward)."
FEEDBACK_KEY = "loop_feedback"  # reserved key in the Programmer request input
KIND = "automated_review_findings"  # tells the Programmer (and its model) this is data, not instructions
_MACHINE_RE = re.compile(r"(?:^|[\s(\"'])(?:/|~/|[A-Za-z]:[\\/]|\\\\)")


class FeedbackSource(StrEnum):
    QA = "qa"
    REVIEWER = "reviewer"


def _safe_message(message: Any) -> str:
    if (not isinstance(message, str) or not 1 <= len(message) <= MESSAGE_MAX or has_control(message)
            or find_secret(message) or _MACHINE_RE.search(message)):
        return WITHHELD
    return message


@dataclass(frozen=True)
class LoopFeedback:
    source: FeedbackSource
    iteration: int
    code: str
    severity: str
    message: str
    target: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "source", FeedbackSource(self.source))
        except ValueError:
            raise LoopResultError("feedback source must be qa or reviewer", details=("source",)) from None
        if isinstance(self.iteration, bool) or not isinstance(self.iteration, int) or not 1 <= self.iteration <= 99:
            raise LoopResultError("feedback iteration must be a small positive integer", details=("iteration",))
        if not isinstance(self.code, str) or not CODE_RE.fullmatch(self.code):
            raise LoopResultError("feedback code must look like area.name", details=("code",))
        if self.severity not in ("error", "warning"):
            raise LoopResultError("feedback severity must be error or warning", details=("severity",))
        if _safe_message(self.message) != self.message:
            raise LoopResultError("feedback message is not safe to forward", details=(self.code,))
        if self.target is not None and safe_path(self.target) != self.target:
            raise LoopResultError("feedback target must be a safe project-relative path", details=(self.code,))

    def to_dict(self) -> dict[str, Any]:
        return {"source": str(self.source), "iteration": self.iteration, "code": self.code,
                "severity": self.severity, "message": self.message, "target": self.target}


def _line(source: FeedbackSource, iteration: int, finding: Any) -> LoopFeedback | None:
    code, severity = finding.code, str(finding.severity)
    if severity not in ("error", "warning") or not isinstance(code, str) or not CODE_RE.fullmatch(code):
        return None
    return LoopFeedback(source, iteration, code, severity, _safe_message(finding.message), safe_path(finding.path))


def collect(qa: QAResult, review: ReviewResult, iteration: int,
            policy: LoopPolicy) -> tuple[tuple[LoopFeedback, ...], bool]:
    """The bounded feedback lines for this iteration, and whether any were cut."""
    lines: list[LoopFeedback] = []
    for f in qa.findings:
        line = _line(FeedbackSource.QA, iteration, f)
        if line is not None:
            lines.append(line)
    for f in review.findings:
        if str(f.category) == "verification":  # mirrors QA's own status; the QA lines already say it
            continue
        line = _line(FeedbackSource.REVIEWER, iteration, f)
        if line is not None:
            lines.append(line)
    seen: set[tuple[str, str, str | None]] = set()
    unique = []
    for line in sorted(lines, key=lambda x: 0 if x.severity == "error" else 1):  # stable: QA before Reviewer
        key = (str(line.source), line.code, line.target)
        if key not in seen:
            seen.add(key)
            unique.append(line)
    kept: list[LoopFeedback] = []
    used = 0
    for line in unique[: policy.max_feedback_items]:
        size = len(json.dumps(line.to_dict(), ensure_ascii=False).encode("utf-8"))
        if used + size > policy.max_feedback_bytes:
            break
        kept.append(line)
        used += size
    return tuple(kept), len(kept) < len(unique)


def as_input(feedback: tuple[LoopFeedback, ...], truncated: bool, iteration: int, previous_patch_hash: str,
             previous_status: str) -> dict[str, Any]:
    """The typed block placed under :data:`FEEDBACK_KEY` in the next Programmer request's ``input``."""
    return {
        "kind": KIND,
        "from_iteration": iteration,
        "previous_patch_hash": previous_patch_hash,
        "previous_review_status": previous_status,
        "truncated": truncated,
        "notes": [{k: v for k, v in line.to_dict().items() if k != "iteration"} for line in feedback],
    }
