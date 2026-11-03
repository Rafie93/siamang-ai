"""QAPolicy: resource and scope bounds for one verification. Not a permission system.

Conservative fixed defaults, each bounded by a hard cap. There is no
environment or config override and no "unlimited" value.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from siamang.programmer.change import MAX_FILE_LIMIT
from siamang.programmer.patch import MAX_CHANGES_LIMIT, MAX_TOTAL_LIMIT
from siamang.programmer.safety import PATH_MAX
from siamang.qa.errors import QAPolicyError
from siamang.qa.result import FINDINGS_LIMIT
from siamang.tasks.lifecycle import TaskStatus

NOT_VERIFIABLE = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

# name -> (default, hard cap)
LIMITS: dict[str, tuple[int, int]] = {
    "max_changes": (20, MAX_CHANGES_LIMIT),  # changes verified per patch
    "max_file_size": (256 * 1024, MAX_FILE_LIMIT),  # proposed bytes per file; current files larger are unverified
    "max_total_bytes": (1024 * 1024, MAX_TOTAL_LIMIT),  # proposed bytes per patch
    "max_path_length": (512, PATH_MAX),
    "max_findings": (200, FINDINGS_LIMIT),
}


@dataclass(frozen=True)
class QAPolicy:
    max_changes: int = LIMITS["max_changes"][0]
    max_file_size: int = LIMITS["max_file_size"][0]
    max_total_bytes: int = LIMITS["max_total_bytes"][0]
    max_path_length: int = LIMITS["max_path_length"][0]
    max_findings: int = LIMITS["max_findings"][0]
    verifiable_statuses: frozenset[TaskStatus] = frozenset(TaskStatus) - NOT_VERIFIABLE

    def __post_init__(self) -> None:
        for name, value in self._limits().items():
            cap = LIMITS[name][1]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise ValueError(f"{name} must be an integer in [1, {cap}]")
        if self.max_file_size > self.max_total_bytes:
            raise ValueError("max_file_size cannot exceed max_total_bytes")
        statuses = self.verifiable_statuses
        if isinstance(statuses, (str, bytes)) or not isinstance(statuses, Iterable):
            raise ValueError("verifiable_statuses must be a collection of task statuses")
        try:
            statuses = frozenset(TaskStatus(s) for s in statuses)
        except ValueError:
            raise ValueError("verifiable_statuses must be task statuses") from None
        if statuses & NOT_VERIFIABLE or not statuses:
            raise ValueError("completed and cancelled tasks cannot be verified")
        object.__setattr__(self, "verifiable_statuses", statuses)

    def _limits(self) -> dict[str, int]:
        fields = self.__dict__  # plain field read; no dynamic attribute lookup
        return {name: fields[name] for name in LIMITS}

    def check_task(self, status: TaskStatus) -> None:
        if status not in self.verifiable_statuses:
            raise QAPolicyError(f"a {status} task cannot be verified")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._limits(),
            "verifiable_statuses": sorted(str(s) for s in self.verifiable_statuses),
            "executes_code": False,
            "writes": False,
            "uses_models": False,
            "retries": 0,
            # current files are read through the Phase 15 snapshot: (changes + 1) x file size, hard cap 8 MiB
            "inspected_bytes_cap": 8 * 1024 * 1024,
        }


DEFAULT_POLICY = QAPolicy()
