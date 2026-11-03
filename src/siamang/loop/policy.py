"""LoopPolicy: hard bounds for one bounded correction run. Not a permission system.

Conservative fixed defaults, each bounded by a small hard cap. There is no
environment or config override, and no "unlimited" value: ``None``, ``0``,
negatives, floats, infinity, and booleans are all refused.

One iteration costs exactly one Programmer call, one QA call, and one
Reviewer call. The loop starts an iteration only when every budget can pay
for a whole one.

``retry_statuses`` says which review outcomes may be followed by another
iteration (``concern`` and ``fail`` by default). ``inconclusive`` is retried
only when listed explicitly; ``pass`` never is: a pass always stops.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.reviewer.result import ReviewStatus

POLICY_VERSION = "1"
ITERATIONS_LIMIT = 5
FEEDBACK_ITEMS_LIMIT = 50
FEEDBACK_BYTES_LIMIT = 6 * 1024  # with the caller's input (<= 8 KiB) it stays under the Programmer's 16 KiB input cap
RETRYABLE = frozenset({ReviewStatus.CONCERN, ReviewStatus.FAIL, ReviewStatus.INCONCLUSIVE})

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "max_iterations": (3, ITERATIONS_LIMIT),
    "max_programmer_calls": (3, ITERATIONS_LIMIT),
    "max_qa_calls": (3, ITERATIONS_LIMIT),
    "max_reviewer_calls": (3, ITERATIONS_LIMIT),
    "max_feedback_items": (20, FEEDBACK_ITEMS_LIMIT),  # feedback lines handed to the next Programmer call
    "max_feedback_bytes": (4 * 1024, FEEDBACK_BYTES_LIMIT),  # serialized feedback lines
}


@dataclass(frozen=True)
class LoopPolicy:
    max_iterations: int = LIMITS["max_iterations"][0]
    max_programmer_calls: int = LIMITS["max_programmer_calls"][0]
    max_qa_calls: int = LIMITS["max_qa_calls"][0]
    max_reviewer_calls: int = LIMITS["max_reviewer_calls"][0]
    max_feedback_items: int = LIMITS["max_feedback_items"][0]
    max_feedback_bytes: int = LIMITS["max_feedback_bytes"][0]
    retry_statuses: frozenset[ReviewStatus] = frozenset({ReviewStatus.CONCERN, ReviewStatus.FAIL})

    def __post_init__(self) -> None:
        for name, value in self._limits().items():
            cap = LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        statuses = self.retry_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("retry_statuses must be a collection of review statuses")
        try:
            statuses = frozenset(ReviewStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("retry_statuses must be review statuses") from None
        if not statuses <= RETRYABLE:
            raise ValueError("a pass is never retried; retry_statuses may hold concern, fail, inconclusive")
        object.__setattr__(self, "retry_statuses", statuses)

    def _limits(self) -> dict[str, int]:
        fields = self.__dict__  # plain field read; no dynamic attribute lookup
        return {name: fields[name] for name in LIMITS}

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._limits(),
            "retry_statuses": sorted(str(s) for s in self.retry_statuses),
            "policy_version": POLICY_VERSION,
            "calls_per_iteration": {"programmer": 1, "qa": 1, "reviewer": 1},
            "applies_patches": False,
            "approves": False,
            "executes_code": False,
            "writes": False,
            "persists": False,
        }


DEFAULT_POLICY = LoopPolicy()
