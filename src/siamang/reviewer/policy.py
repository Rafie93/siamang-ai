"""ReviewerPolicy: resource bounds and judgment thresholds for one review. Not a permission system.

Conservative fixed defaults, each bounded by a hard cap. There is no
environment or config override and no "unlimited" value.

* Resource bounds (exceeding one is an input refusal, never a partial
  review): ``max_changes``, ``max_total_bytes``, ``max_findings`` (findings
  beyond it are cut, errors first, and the cut makes the review
  inconclusive).
* Judgment thresholds (exceeding one is a *warning*, never a failure):
  ``broad_change_count``, ``max_directories``, ``max_new_files``,
  ``max_file_lines``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.programmer.patch import MAX_CHANGES_LIMIT, MAX_TOTAL_LIMIT
from siamang.reviewer.errors import ReviewPolicyError
from siamang.reviewer.result import FINDINGS_LIMIT
from siamang.tasks.lifecycle import TaskStatus

NOT_REVIEWABLE = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})
MAX_FILE_LINES_LIMIT = 100_000

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "max_changes": (20, MAX_CHANGES_LIMIT),  # changes reviewed per patch; more is refused
    "max_total_bytes": (1024 * 1024, MAX_TOTAL_LIMIT),  # proposed bytes per patch; more is refused
    "max_findings": (100, FINDINGS_LIMIT),
    "broad_change_count": (10, MAX_CHANGES_LIMIT),  # more changes than this: scope.broad_change
    "max_directories": (3, MAX_CHANGES_LIMIT),  # more top-level directories than this: scope.directory_spread
    "max_new_files": (5, MAX_CHANGES_LIMIT),  # more created files than this: maintainability.many_new_files
    "max_file_lines": (400, MAX_FILE_LINES_LIMIT),  # a proposed file longer than this: maintainability.large_file
}


# The only findings a policy may turn into notes: what a new application naturally does. Never a finding
# about security, QA, or whether the patch is right.
ADVISORY_ALLOWED = frozenset({
    "scope.broad_change", "scope.directory_spread", "maintainability.many_new_files", "maintainability.duplicate_content",
    "risk.dependency_change", "risk.configuration_change", "risk.public_interface", "consistency.unfamiliar_file_type",
    "consistency.naming_style", "feasibility.tests_absent",
})


@dataclass(frozen=True)
class ReviewerPolicy:
    max_changes: int = LIMITS["max_changes"][0]
    max_total_bytes: int = LIMITS["max_total_bytes"][0]
    max_findings: int = LIMITS["max_findings"][0]
    broad_change_count: int = LIMITS["broad_change_count"][0]
    max_directories: int = LIMITS["max_directories"][0]
    max_new_files: int = LIMITS["max_new_files"][0]
    max_file_lines: int = LIMITS["max_file_lines"][0]
    reviewable_statuses: frozenset[TaskStatus] = frozenset(TaskStatus) - NOT_REVIEWABLE
    advisory_codes: frozenset[str] = frozenset()  # warnings that stay in the report and hold nothing back

    def __post_init__(self) -> None:
        for name, value in self._limits().items():
            cap = LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        if not isinstance(self.advisory_codes, frozenset) or not self.advisory_codes <= ADVISORY_ALLOWED:
            raise ValueError("advisory_codes must be codes a policy may turn into notes")
        statuses = self.reviewable_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("reviewable_statuses must be a collection of task statuses")
        try:
            statuses = frozenset(TaskStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("reviewable_statuses must be task statuses") from None
        if statuses & NOT_REVIEWABLE or not statuses:
            raise ValueError("completed and cancelled tasks cannot be reviewed")
        object.__setattr__(self, "reviewable_statuses", statuses)

    def _limits(self) -> dict[str, int]:
        fields = self.__dict__  # plain field read; no dynamic attribute lookup
        return {name: fields[name] for name in LIMITS}

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.reviewable_statuses:
            raise ReviewPolicyError(f"a {status} task cannot be reviewed")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._limits(),
            "reviewable_statuses": sorted(str(s) for s in self.reviewable_statuses),
            **({"advisory_codes": sorted(self.advisory_codes)} if self.advisory_codes else {}),  # evidence sealed earlier has none
            "reads_project": False,
            "executes_code": False,
            "writes": False,
            "uses_models": False,
            "approves": False,
            "applies": False,
            "repairs": False,
            "retries": 0,
        }


DEFAULT_POLICY = ReviewerPolicy()
